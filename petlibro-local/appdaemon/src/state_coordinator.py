"""Central authority for persistent feeder state and verified writes."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
import enum
import functools
import time
import uuid
from typing import Callable, Protocol

from state_agent import (
    FeederPlan,
    FeederRevisions,
    FeederTruth,
    RevisionSnapshot,
    StateAgentClient,
    StateAgentError,
)


class FeederState(enum.Enum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTED = "CONNECTED"
    RECONCILING = "RECONCILING"
    READY = "READY"
    PENDING_WRITE = "PENDING_WRITE"
    VERIFYING_WRITE = "VERIFYING_WRITE"
    DIVERGED = "DIVERGED"


class PendingStage(enum.Enum):
    PREPARING = "PREPARING"
    AWAITING_ACK = "AWAITING_ACK"
    VERIFYING = "VERIFYING"


class VerificationMode(enum.Enum):
    FULL = "FULL"
    ACK_ONLY_UNVERIFIED = "ACK_ONLY_UNVERIFIED"


@dataclass(frozen=True)
class CommandReceipt:
    ack_kind: str
    message_id: str


@dataclass(frozen=True)
class MqttAck:
    ack_kind: str
    message_id: str
    success: bool
    detail: str = ""


class TruthPredicate(Protocol):
    def matches(self, truth: FeederTruth) -> bool: ...

    def expected_description(self) -> str: ...

    def actual_description(self, truth: FeederTruth) -> str: ...


@dataclass(frozen=True)
class SettingEqualsPredicate:
    field: str
    expected: object

    def matches(self, truth: FeederTruth) -> bool:
        return truth.settings.get(self.field) == self.expected

    def expected_description(self) -> str:
        return f"{self.field}={self.expected!r}"

    def actual_description(self, truth: FeederTruth) -> str:
        return f"{self.field}={truth.settings.get(self.field)!r}"


@dataclass(frozen=True)
class PlanPatch:
    plan_id: int
    hour_utc: int
    minute: int
    days_raw: tuple[int, ...]
    portions: int

    def __post_init__(self):
        if not 1 <= self.plan_id <= 255:
            raise ValueError("plan ID must be between 1 and 255")
        if not 0 <= self.hour_utc <= 23 or not 0 <= self.minute <= 59:
            raise ValueError("plan time is invalid")
        normalized = tuple(sorted(self.days_raw))
        if normalized != tuple(self.days_raw):
            object.__setattr__(self, "days_raw", normalized)
        if len(normalized) != len(set(normalized)) or any(
            day < 1 or day > 7 for day in normalized
        ):
            raise ValueError("plan weekdays are invalid")
        if not 0 <= self.portions <= 255:
            raise ValueError("plan portions are invalid")


@dataclass(frozen=True)
class PlanCollectionPredicate:
    baseline: tuple[FeederPlan, ...]
    expected: tuple[FeederPlan, ...]
    target_plan_id: int

    def __post_init__(self):
        baseline_by_id = {plan.id: plan for plan in self.baseline}
        expected_by_id = {plan.id: plan for plan in self.expected}
        if (
            len(baseline_by_id) != len(self.baseline)
            or len(expected_by_id) != len(self.expected)
            or set(baseline_by_id) != set(expected_by_id)
            or self.target_plan_id not in baseline_by_id
        ):
            raise ValueError("feeding-plan verification collections do not align")
        for plan_id, baseline_plan in baseline_by_id.items():
            expected_plan = expected_by_id[plan_id]
            if plan_id != self.target_plan_id:
                if (
                    baseline_plan.semantic_fingerprint()
                    != expected_plan.semantic_fingerprint()
                ):
                    raise ValueError("non-target feeding plan was mutated")
                continue
            if (
                baseline_plan.enabled_raw != expected_plan.enabled_raw
                or baseline_plan.bowl_or_target_raw
                != expected_plan.bowl_or_target_raw
            ):
                raise ValueError("target feeding-plan opaque fields were mutated")

    def matches(self, truth: FeederTruth) -> bool:
        actual = truth.plans.semantic_records
        if truth.plans.count != len(self.expected) or len(actual) != len(self.expected):
            return False
        expected_by_id = {plan.id: plan for plan in self.expected}
        actual_by_id = {plan.id: plan for plan in actual}
        if set(expected_by_id) != set(actual_by_id):
            return False
        return all(
            actual_by_id[plan_id].semantic_fingerprint()
            == expected_plan.semantic_fingerprint()
            for plan_id, expected_plan in expected_by_id.items()
        )

    def expected_description(self) -> str:
        return _plan_collection_description(self.expected)

    def actual_description(self, truth: FeederTruth) -> str:
        return _plan_collection_description(truth.plans.semantic_records)


@dataclass(frozen=True)
class PersistentWriteRequest:
    control: str
    target: object
    publisher: Callable[[FeederTruth], CommandReceipt]
    predicate: TruthPredicate | None
    verification_mode: VerificationMode = VerificationMode.FULL
    command_summary: str = ""
    requires_fresh_preflight: bool = False
    plan_patch: PlanPatch | None = None


@dataclass
class PendingWrite:
    id: str
    created_at: float
    request: PersistentWriteRequest
    stage: PendingStage
    deadline: float
    receipt: CommandReceipt | None = None
    predicate: TruthPredicate | None = None
    baseline_truth: FeederTruth | None = None
    retry_count: int = 0
    last_observed_truth: FeederTruth | None = None
    failure_reason: str | None = None


@dataclass(frozen=True)
class _AgentCallResult:
    value: object | None = None
    error_type: str | None = None
    error_message: str | None = None

    @property
    def ok(self) -> bool:
        return self.error_type is None


class FeederStateCoordinator:
    ACK_TIMEOUT_SECONDS = 12
    VERIFY_DELAYS_SECONDS = (0.35, 1.0, 2.0, 4.0)
    MAX_QUEUED_WRITES = 8

    def __init__(
        self,
        ad,
        state_agent: StateAgentClient,
        logger,
        truth_sink: Callable[[FeederTruth], None],
        availability_sink: Callable[[bool], None],
        verified_truth_store: Callable[[FeederTruth], None] | None = None,
    ):
        self.ad = ad
        self.state_agent = state_agent
        self.logger = logger
        self.truth_sink = truth_sink
        self.availability_sink = availability_sink
        self.verified_truth_store = verified_truth_store

        self.state = FeederState.DISCONNECTED
        self.mqtt_connected = False
        self.state_api_healthy = False
        self._latest_truth: FeederTruth | None = None
        self._latest_revisions: FeederRevisions | None = None
        self.pending_write: PendingWrite | None = None
        self._queued_writes: deque[PersistentWriteRequest] = deque()
        self._applying_feeder_truth = False
        self._generation = 0
        self._operation_token: str | None = None
        self._refresh_requested = False
        self._plan_snapshot_callbacks: list[
            Callable[[tuple[FeederPlan, ...] | None], None]
        ] = []
        self._ack_timeout_handle = None
        self._verify_timer_handle = None

    def latest_truth(self) -> FeederTruth | None:
        return self._latest_truth

    def latest_revisions(self) -> FeederRevisions | None:
        return self._latest_revisions

    def suppressing_writeback(self) -> bool:
        return self._applying_feeder_truth

    def is_ready_for_user_write(self) -> bool:
        return (
            self.state == FeederState.READY
            and self.mqtt_connected
            and self.state_api_healthy
            and self.pending_write is None
            and self._operation_token is None
        )

    def on_feeder_connected(self) -> None:
        if self.mqtt_connected and self.state != FeederState.DISCONNECTED:
            return
        if not self.mqtt_connected:
            self._generation += 1
        self.mqtt_connected = True
        self.availability_sink(False)
        self._transition(FeederState.CONNECTED, "feeder MQTT connected")
        self.reconcile_from_feeder("connect")

    def on_feeder_disconnected(self, reason: str) -> None:
        self.mqtt_connected = False
        self.state_api_healthy = False
        self._generation += 1
        self._operation_token = None
        self._cancel_pending_timers()
        if self.pending_write is not None:
            self.pending_write.failure_reason = reason
        self.pending_write = None
        self._queued_writes.clear()
        self.availability_sink(False)
        self._transition(FeederState.DISCONNECTED, reason)

    def on_heartbeat(self) -> None:
        if not self.mqtt_connected:
            self.on_feeder_connected()
            return
        if self.state == FeederState.DISCONNECTED or not self.state_api_healthy:
            self.reconcile_from_feeder("heartbeat recovery")
            return
        if self.pending_write is not None:
            self._refresh_requested = True
            return
        self.refresh_revisions()

    def on_persistent_state_hint(self) -> None:
        if self.state == FeederState.READY and self.pending_write is None:
            self._refresh_requested = True
            self.refresh_revisions()

    def reconcile_from_feeder(self, reason: str) -> None:
        if not self.mqtt_connected or self._operation_token is not None:
            return
        self._transition(FeederState.RECONCILING, reason)
        self._submit_agent_call("core", "reconcile")

    def refresh_revisions(self) -> None:
        if not self.mqtt_connected or self._operation_token is not None:
            self._refresh_requested = True
            return
        self._refresh_requested = False
        self._submit_agent_call("revisions", "heartbeat")

    def request_persistent_write(self, request: PersistentWriteRequest) -> bool:
        if self._applying_feeder_truth:
            self.logger.debug(
                "suppressed HA writeback event", control=request.control
            )
            return False
        if not self.mqtt_connected or not self.state_api_healthy:
            self.logger.warning(
                "persistent feeder write blocked",
                control=request.control,
                reason="state agent is not ready",
            )
            return False
        if self.pending_write is not None:
            if (
                self.pending_write.stage == PendingStage.PREPARING
                and self.pending_write.request.control == request.control
            ):
                self.pending_write.request = request
                self.logger.debug(
                    "coalesced pending feeder write", control=request.control
                )
                return True
            return self._queue_write(request)
        if self.state != FeederState.READY or self._latest_truth is None:
            self.logger.warning(
                "persistent feeder write blocked",
                control=request.control,
                reason=f"coordinator state is {self.state.value}",
            )
            return False
        if self._operation_token is not None:
            return self._queue_write(request)
        self._start_write(request)
        return True

    def request_plan_snapshot(
        self, callback: Callable[[tuple[FeederPlan, ...] | None], None]
    ) -> None:
        self._plan_snapshot_callbacks.append(callback)
        if not self.mqtt_connected:
            self._finish_plan_snapshots(None)
            return
        if self._operation_token is None and self.pending_write is None:
            self._submit_agent_call("core", "plan_snapshot")

    def on_mqtt_ack(self, ack: MqttAck) -> bool:
        pending = self.pending_write
        if (
            pending is None
            or pending.stage != PendingStage.AWAITING_ACK
            or pending.receipt is None
            or pending.receipt.ack_kind != ack.ack_kind
            or pending.receipt.message_id != ack.message_id
        ):
            self.logger.debug(
                "ignored unmatched MQTT acknowledgement", ack_kind=ack.ack_kind
            )
            return False
        self._cancel_ack_timeout()
        self.logger.info(
            "persistent write MQTT acknowledgement received",
            control=pending.request.control,
            success=ack.success,
        )
        if not ack.success:
            self._complete_failed_write(ack.detail or "MQTT command rejected")
            return True
        if pending.request.verification_mode == VerificationMode.ACK_ONLY_UNVERIFIED:
            self.logger.warning(
                "persistent write acknowledged but not locally verifiable",
                control=pending.request.control,
            )
            self._complete_write_without_truth_update()
            return True
        pending.stage = PendingStage.VERIFYING
        self._transition(FeederState.VERIFYING_WRITE, "MQTT acknowledgement")
        self._schedule_verification(0)
        return True

    def _start_write(self, request: PersistentWriteRequest) -> None:
        now = time.monotonic()
        self.pending_write = PendingWrite(
            id=uuid.uuid4().hex[:16],
            created_at=now,
            request=request,
            stage=(
                PendingStage.PREPARING
                if request.requires_fresh_preflight
                else PendingStage.AWAITING_ACK
            ),
            deadline=now + self.ACK_TIMEOUT_SECONDS,
            predicate=request.predicate,
        )
        self._transition(FeederState.PENDING_WRITE, "user-originated write")
        self.logger.info(
            "persistent feeder write pending",
            control=request.control,
            preflight=request.requires_fresh_preflight,
        )
        if request.requires_fresh_preflight:
            self._submit_agent_call("core", "write_preflight")
        else:
            self._publish_pending(self._latest_truth)

    def _queue_write(self, request: PersistentWriteRequest) -> bool:
        for index in range(len(self._queued_writes) - 1, -1, -1):
            if self._queued_writes[index].control == request.control:
                self._queued_writes[index] = request
                self.logger.debug("coalesced queued feeder write", control=request.control)
                return True
        if len(self._queued_writes) >= self.MAX_QUEUED_WRITES:
            self.logger.warning(
                "persistent feeder write queue full", control=request.control
            )
            return False
        self._queued_writes.append(request)
        self.logger.info("persistent feeder write queued", control=request.control)
        return True

    def _publish_pending(self, truth: FeederTruth | None) -> None:
        pending = self.pending_write
        if pending is None or truth is None:
            self._complete_failed_write("verified feeder truth is unavailable")
            return
        request = pending.request
        if request.plan_patch is not None:
            try:
                expected_plans = build_patched_plan_collection(
                    truth.plans.semantic_records, request.plan_patch
                )
            except ValueError as exc:
                self._complete_failed_write(str(exc))
                return
            pending.baseline_truth = truth
            pending.predicate = PlanCollectionPredicate(
                baseline=truth.plans.semantic_records,
                expected=expected_plans,
                target_plan_id=request.plan_patch.plan_id,
            )
            publisher_truth = replace(
                truth,
                plans=replace(
                    truth.plans,
                    count=len(expected_plans),
                    semantic_records=expected_plans,
                ),
            )
        else:
            publisher_truth = truth
            pending.baseline_truth = truth
        try:
            receipt = request.publisher(publisher_truth)
        except Exception as exc:
            self._complete_failed_write(
                f"MQTT publisher failed ({type(exc).__name__})"
            )
            return
        pending.receipt = receipt
        pending.stage = PendingStage.AWAITING_ACK
        pending.deadline = time.monotonic() + self.ACK_TIMEOUT_SECONDS
        self._ack_timeout_handle = self.ad.run_in(
            self._ack_timeout,
            self.ACK_TIMEOUT_SECONDS,
            pending_id=pending.id,
        )
        self.logger.info(
            "persistent feeder command sent",
            control=request.control,
            ack_kind=receipt.ack_kind,
        )

    def _ack_timeout(self, kwargs: dict) -> None:
        self._ack_timeout_handle = None
        pending = self.pending_write
        if pending is None or pending.id != kwargs.get("pending_id"):
            return
        self._complete_failed_write("MQTT acknowledgement timed out")

    def _schedule_verification(self, retry_index: int) -> None:
        pending = self.pending_write
        if pending is None:
            return
        delay = self.VERIFY_DELAYS_SECONDS[min(retry_index, len(self.VERIFY_DELAYS_SECONDS) - 1)]
        self._verify_timer_handle = self.ad.run_in(
            self._verification_timer,
            delay,
            pending_id=pending.id,
        )

    def _verification_timer(self, kwargs: dict) -> None:
        self._verify_timer_handle = None
        pending = self.pending_write
        if pending is None or pending.id != kwargs.get("pending_id"):
            return
        if self._operation_token is not None:
            self._schedule_verification(pending.retry_count)
            return
        self._submit_agent_call("core", "verify")

    def _submit_agent_call(self, method_name: str, purpose: str) -> None:
        if self._operation_token is not None:
            self._refresh_requested = True
            return
        operation_token = uuid.uuid4().hex[:16]
        generation = self._generation
        self._operation_token = operation_token
        try:
            self.ad.submit_to_executor(
                self._agent_worker,
                method_name,
                callback=functools.partial(
                    self._agent_call_complete,
                    purpose=purpose,
                    operation_token=operation_token,
                    generation=generation,
                ),
            )
        except Exception as exc:
            self._operation_token = None
            self._handle_agent_failure(
                purpose,
                _AgentCallResult(
                    error_type=type(exc).__name__,
                    error_message="state-agent executor submission failed",
                ),
            )

    def _agent_worker(self, method_name: str) -> _AgentCallResult:
        try:
            method = getattr(self.state_agent, method_name)
            return _AgentCallResult(value=method())
        except StateAgentError as exc:
            return _AgentCallResult(
                error_type=type(exc).__name__, error_message=str(exc)
            )
        except Exception as exc:
            return _AgentCallResult(
                error_type=type(exc).__name__,
                error_message="unexpected state-agent client failure",
            )

    def _agent_call_complete(
        self,
        result: _AgentCallResult | None = None,
        *,
        purpose: str,
        operation_token: str,
        generation: int,
        **_kwargs,
    ) -> None:
        if generation != self._generation or operation_token != self._operation_token:
            return
        self._operation_token = None
        if not isinstance(result, _AgentCallResult):
            result = _AgentCallResult(
                error_type="InvalidExecutorResult",
                error_message="state-agent executor returned an invalid result",
            )
        if not result.ok:
            self._handle_agent_failure(purpose, result)
            self._maybe_process_deferred()
            return
        self._mark_agent_recovered()
        if purpose == "heartbeat":
            self._handle_revision_snapshot(result.value)
        elif purpose == "reconcile":
            self._handle_reconciled_truth(result.value)
        elif purpose == "write_preflight":
            self._handle_write_preflight(result.value)
        elif purpose == "verify":
            self._handle_verification_truth(result.value)
        elif purpose == "plan_snapshot":
            self._handle_plan_snapshot(result.value)
        self._maybe_process_deferred()

    def _handle_revision_snapshot(self, value: object) -> None:
        if not isinstance(value, RevisionSnapshot):
            self._handle_agent_failure(
                "heartbeat",
                _AgentCallResult(
                    error_type="InvalidResponse",
                    error_message="state-agent revision response was invalid",
                ),
            )
            return
        if self._latest_revisions is None or (
            value.revisions.core_rev != self._latest_revisions.core_rev
        ):
            self.logger.info(
                "feeder revisions changed",
                previous=(
                    self._latest_revisions.core_rev
                    if self._latest_revisions is not None
                    else None
                ),
                current=value.revisions.core_rev,
            )
            self._submit_agent_call("core", "reconcile")

    def _handle_reconciled_truth(self, value: object) -> None:
        if not isinstance(value, FeederTruth):
            self._handle_agent_failure(
                "reconcile",
                _AgentCallResult(
                    error_type="InvalidResponse",
                    error_message="state-agent core response was invalid",
                ),
            )
            return
        if not self._commit_verified_truth(value):
            self._transition(FeederState.DISCONNECTED, "HA truth projection failed")
            self.availability_sink(False)
            return
        self._transition(FeederState.READY, "reconciliation complete")
        self.availability_sink(True)
        self.logger.info(
            "feeder reconciliation complete", core_rev=value.revisions.core_rev
        )
        self._start_next_write()

    def _handle_write_preflight(self, value: object) -> None:
        pending = self.pending_write
        if pending is None or pending.stage != PendingStage.PREPARING:
            return
        if not isinstance(value, FeederTruth):
            self._complete_failed_write("feed-plan preflight returned invalid truth")
            return
        if not self._commit_verified_truth(value):
            self._complete_failed_write(
                "HA truth projection failed during preflight", degraded=True
            )
            return
        self._publish_pending(value)

    def _handle_verification_truth(self, value: object) -> None:
        pending = self.pending_write
        if pending is None or pending.stage != PendingStage.VERIFYING:
            return
        if not isinstance(value, FeederTruth):
            self._retry_verification("verification returned invalid truth")
            return
        pending.last_observed_truth = value
        predicate = pending.predicate
        if predicate is not None and predicate.matches(value):
            if not self._commit_verified_truth(value):
                self._complete_failed_write(
                    "write persisted but HA truth projection failed",
                    degraded=True,
                )
                return
            self.logger.info(
                "persistent feeder write verified",
                control=pending.request.control,
                attempts=pending.retry_count + 1,
            )
            self.pending_write = None
            self._transition(FeederState.READY, "write verified")
            self.availability_sink(True)
            self._start_next_write()
            return
        self._retry_verification("persisted feeder state did not match target")

    def _retry_verification(self, reason: str, *, api_failure: bool = False) -> None:
        pending = self.pending_write
        if pending is None:
            return
        pending.retry_count += 1
        if pending.retry_count < len(self.VERIFY_DELAYS_SECONDS):
            self.logger.debug(
                "persistent write verification retry",
                control=pending.request.control,
                attempt=pending.retry_count + 1,
                reason=reason,
            )
            self._schedule_verification(pending.retry_count)
            return
        actual_truth = pending.last_observed_truth
        self._transition(FeederState.DIVERGED, reason)
        if actual_truth is not None:
            predicate = pending.predicate
            self.logger.warning(
                "persistent feeder state diverged",
                control=pending.request.control,
                expected=(predicate.expected_description() if predicate else None),
                actual=(predicate.actual_description(actual_truth) if predicate else None),
            )
            projection_ok = self._commit_verified_truth(actual_truth)
            self.pending_write = None
            if api_failure or not projection_ok:
                self._transition(
                    FeederState.DISCONNECTED,
                    (
                        "state API unavailable after write divergence"
                        if api_failure
                        else "HA truth projection failed after write divergence"
                    ),
                )
                self.availability_sink(False)
                return
            self._transition(FeederState.READY, "feeder truth applied after divergence")
            self.availability_sink(True)
            self._start_next_write()
        else:
            self._complete_failed_write(reason, degraded=api_failure)

    def _handle_plan_snapshot(self, value: object) -> None:
        if not isinstance(value, FeederTruth):
            self._finish_plan_snapshots(None)
            return
        projection_ok = self._commit_verified_truth(value)
        if not projection_ok:
            self._transition(FeederState.DISCONNECTED, "HA truth projection failed")
            self.availability_sink(False)
        elif self.state == FeederState.DISCONNECTED:
            self._transition(FeederState.READY, "plan snapshot restored state API")
            self.availability_sink(True)
        self._finish_plan_snapshots(value.plans.semantic_records)

    def _handle_agent_failure(self, purpose: str, result: _AgentCallResult) -> None:
        self.state_api_healthy = False
        self.availability_sink(False)
        self.logger.warning(
            "feeder state API unavailable",
            phase=purpose,
            error_type=result.error_type,
        )
        if purpose == "verify" and self.pending_write is not None:
            self._retry_verification(
                result.error_message or "verification read failed",
                api_failure=True,
            )
            return
        if purpose == "plan_snapshot":
            self._finish_plan_snapshots(None)
            self._transition(FeederState.DISCONNECTED, "state API unavailable")
            return
        if purpose == "write_preflight":
            self._complete_failed_write(
                result.error_message or "feed-plan preflight failed",
                degraded=True,
            )
            return
        self._transition(FeederState.DISCONNECTED, "state API unavailable")

    def _mark_agent_recovered(self) -> None:
        if not self.state_api_healthy:
            self.logger.info("feeder state API recovered")
        self.state_api_healthy = True

    def _commit_verified_truth(self, truth: FeederTruth) -> bool:
        projection_ok = True
        self._applying_feeder_truth = True
        try:
            self.truth_sink(truth)
        except Exception as exc:
            projection_ok = False
            self.logger.error(
                "failed to mirror feeder truth into Home Assistant",
                error_type=type(exc).__name__,
            )
        finally:
            self._applying_feeder_truth = False
        self._latest_truth = truth
        self._latest_revisions = truth.revisions
        if self.verified_truth_store is not None:
            try:
                self.verified_truth_store(truth)
            except Exception as exc:
                self.logger.warning(
                    "failed to store diagnostic feeder truth snapshot",
                    error_type=type(exc).__name__,
                )
        return projection_ok

    def _complete_failed_write(self, reason: str, degraded: bool = False) -> None:
        pending = self.pending_write
        if pending is not None:
            pending.failure_reason = reason
            self.logger.warning(
                "persistent feeder write failed",
                control=pending.request.control,
                reason=reason,
            )
        self._cancel_pending_timers()
        self.pending_write = None
        if degraded:
            self._transition(FeederState.DISCONNECTED, reason)
            self.availability_sink(False)
            return
        if self._latest_truth is not None:
            if not self._commit_verified_truth(self._latest_truth):
                self._transition(FeederState.DISCONNECTED, "HA truth projection failed")
                self.availability_sink(False)
                return
        self._transition(FeederState.READY, "write failed; feeder truth retained")
        self._start_next_write()

    def _complete_write_without_truth_update(self) -> None:
        self._cancel_pending_timers()
        self.pending_write = None
        self._transition(FeederState.READY, "unverified write acknowledged")
        self._start_next_write()

    def _start_next_write(self) -> None:
        if (
            self.pending_write is None
            and self.state == FeederState.READY
            and self._queued_writes
        ):
            self._start_write(self._queued_writes.popleft())

    def _finish_plan_snapshots(
        self, plans: tuple[FeederPlan, ...] | None
    ) -> None:
        callbacks = self._plan_snapshot_callbacks
        self._plan_snapshot_callbacks = []
        for callback in callbacks:
            try:
                callback(plans)
            except Exception as exc:
                self.logger.error(
                    "feeding-plan response callback failed",
                    error_type=type(exc).__name__,
                )

    def _maybe_process_deferred(self) -> None:
        if self._operation_token is not None:
            return
        if (
            self.pending_write is not None
            and self.pending_write.stage == PendingStage.PREPARING
        ):
            self._submit_agent_call("core", "write_preflight")
            return
        if self._plan_snapshot_callbacks and self.pending_write is None:
            self._submit_agent_call("core", "plan_snapshot")
            return
        if (
            self.pending_write is None
            and self.state == FeederState.READY
            and self._queued_writes
        ):
            self._start_write(self._queued_writes.popleft())
            return
        if self._refresh_requested and self.pending_write is None:
            self.refresh_revisions()

    def _cancel_ack_timeout(self) -> None:
        if self._ack_timeout_handle is not None:
            self.ad.cancel_timer(self._ack_timeout_handle, True)
            self._ack_timeout_handle = None

    def _cancel_pending_timers(self) -> None:
        self._cancel_ack_timeout()
        if self._verify_timer_handle is not None:
            self.ad.cancel_timer(self._verify_timer_handle, True)
            self._verify_timer_handle = None

    def shutdown(self) -> None:
        """Cancel coordinator work without scheduling another API read."""
        self.mqtt_connected = False
        self.state_api_healthy = False
        self._generation += 1
        self._operation_token = None
        self._refresh_requested = False
        self._cancel_pending_timers()
        self.pending_write = None
        self._queued_writes.clear()
        self._plan_snapshot_callbacks.clear()
        self._transition(FeederState.DISCONNECTED, "controller shutting down")

    def _transition(self, new_state: FeederState, reason: str) -> None:
        previous = self.state
        self.state = new_state
        if previous != new_state:
            self.logger.info(
                "feeder state transition",
                previous=previous.value,
                current=new_state.value,
                reason=reason,
            )


def build_patched_plan_collection(
    baseline: tuple[FeederPlan, ...], patch: PlanPatch
) -> tuple[FeederPlan, ...]:
    if not baseline:
        raise ValueError("feeder has no stored feeding plans")
    validate_plan_transport_fields(baseline)
    if not any(plan.id == patch.plan_id for plan in baseline):
        raise ValueError("feeding-plan addition is not supported")
    expected = []
    for plan in baseline:
        if plan.id != patch.plan_id:
            expected.append(plan)
            continue
        expected.append(
            replace(
                plan,
                hour_utc=patch.hour_utc,
                minute=patch.minute,
                time_utc=f"{patch.hour_utc:02d}:{patch.minute:02d}",
                days_raw=tuple(sorted(patch.days_raw)),
                portions=patch.portions,
            )
        )
    return tuple(expected)


def validate_plan_transport_fields(plans: tuple[FeederPlan, ...]) -> None:
    for plan in plans:
        if plan.enabled_raw not in {0, 1}:
            raise ValueError(
                f"feeding plan {plan.id} has unsupported enabled_raw value"
            )


def _plan_collection_description(plans: tuple[FeederPlan, ...]) -> str:
    return repr([plan.semantic_fingerprint() for plan in plans])
