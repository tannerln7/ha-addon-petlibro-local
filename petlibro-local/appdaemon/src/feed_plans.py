"""Feeding-plan parsing, wire serialization, and Home Assistant projection."""

from __future__ import annotations

import json

from protocol import (
    Code,
    FeedingPlanOut,
    FeedingPlanServiceOut,
    GetFeedingPlanEventIn,
    GetFeedingPlanEventOut,
    GetFeedingPlanOut,
    HourMinTimestamp,
    Timestamp,
    Weekday,
    WeekdaySchedule,
)
from state_agent import FeederPlan
from state_coordinator import PlanPatch


class PlanSlotMismatch(ValueError):
    """Raised when an HA plan command targets a different slot."""

    def __init__(self, slot: int, plan_id: int):
        super().__init__(f"feeding-plan slot {slot} does not match plan ID {plan_id}")
        self.slot = slot
        self.plan_id = plan_id


def parse_plan_patch(raw_payload: object, plan_slot: int) -> PlanPatch:
    """Parse an HA plan command without accepting feeder-owned opaque fields."""

    payload = json.loads(raw_payload)
    if not isinstance(payload, dict):
        raise ValueError("feeding-plan payload must be an object")
    plan_id = int(payload["id"])
    if plan_id != plan_slot:
        raise PlanSlotMismatch(plan_slot, plan_id)

    execution_time = HourMinTimestamp.from_dict(payload["execution_time"])
    hour_utc, minute = map(
        int, execution_time.to_mqtt_payload_value().split(":")
    )
    schedule = WeekdaySchedule.from_list(payload["scheduled_days"])
    return PlanPatch(
        plan_id=plan_id,
        hour_utc=hour_utc,
        minute=minute,
        days_raw=tuple(sorted(day.value for day in schedule.value)),
        portions=int(payload["grain_num"]),
    )


def build_plan_service(plans: tuple[FeederPlan, ...]) -> FeedingPlanServiceOut:
    """Serialize a complete feeder-truth plan collection for a write."""

    return FeedingPlanServiceOut.create([
        _wire_plan(plan, FeedingPlanOut) for plan in plans
    ])


def build_plan_response(
    request: GetFeedingPlanEventIn,
    plans: tuple[FeederPlan, ...] | None,
) -> GetFeedingPlanEventOut:
    """Build a fresh-truth response, or an explicit protocol error."""

    wire_plans = [] if plans is None else [
        _wire_plan(plan, GetFeedingPlanOut) for plan in plans
    ]
    return GetFeedingPlanEventOut(
        message_id=request.message_id,
        timestamp=Timestamp.now(),
        code=Code.OK if plans is not None else Code.ERROR_1,
        plans=wire_plans,
    )


def plan_state_payload(plan: FeederPlan) -> dict[str, object]:
    """Project feeder truth into the existing retained HA JSON schema."""

    try:
        hour, minute = map(int, plan.time_local_candidate.split(":"))
    except (AttributeError, ValueError):
        local_time = HourMinTimestamp.create_from_utc(
            plan.hour_utc, plan.minute
        ).to_dict()
    else:
        local_time = {"hour": hour, "minute": minute}
    return {
        "id": plan.id,
        "execution_time": local_time,
        "scheduled_days": [day.upper() for day in plan.days],
        "grain_num": plan.portions,
    }


def _wire_plan(plan: FeederPlan, wire_type):
    if plan.enable_audio_raw not in {0, 1}:
        raise ValueError(
            f"feeding plan {plan.id} has unsupported enable_audio_raw"
        )
    return wire_type(
        plan_id=plan.id,
        execution_time=HourMinTimestamp.create_from_utc(
            plan.hour_utc, plan.minute
        ),
        repeat_day=WeekdaySchedule({Weekday(day) for day in plan.days_raw}),
        enable_audio=bool(plan.enable_audio_raw),
        audio_times=plan.audio_times,
        grain_num=plan.portions,
        sync_time=Timestamp.from_timestamp_epoch_ms(plan.sync_time),
        skip_end_time=(plan.skip_end_time or None),
    )
