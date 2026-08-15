"""Read-only client and validated models for the feeder state agent."""

from __future__ import annotations

from dataclasses import dataclass
import json
import socket
from typing import Any, Mapping
from urllib import error, parse, request


MAX_RESPONSE_BYTES = 1024 * 1024


class StateAgentError(RuntimeError):
    """Base class for sanitized state-agent failures."""


class StateAgentUnavailable(StateAgentError):
    pass


class StateAgentUnauthorized(StateAgentError):
    pass


class StateAgentBadResponse(StateAgentError):
    pass


class StateAgentTimeout(StateAgentError):
    pass


@dataclass(frozen=True)
class FeederRevisions:
    core_rev: str
    settings_rev: str
    plans_rev: str
    queue_index_rev: str

    @classmethod
    def from_dict(cls, data: object) -> "FeederRevisions":
        obj = _object(data, "revisions")
        values = {
            key: _nonempty_string(obj.get(key), f"revisions.{key}")
            for key in (
                "core_rev",
                "settings_rev",
                "plans_rev",
                "queue_index_rev",
            )
        }
        return cls(**values)

    def to_dict(self) -> dict[str, str]:
        return {
            "core_rev": self.core_rev,
            "settings_rev": self.settings_rev,
            "plans_rev": self.plans_rev,
            "queue_index_rev": self.queue_index_rev,
        }


@dataclass(frozen=True)
class FeederQueue:
    head: int
    tail: int
    pending: bool

    @classmethod
    def from_dict(cls, data: object, name: str = "queue") -> "FeederQueue":
        obj = _object(data, name)
        return cls(
            head=_integer(obj.get("head"), f"{name}.head", 0, 255),
            tail=_integer(obj.get("tail"), f"{name}.tail", 0, 255),
            pending=_boolean(obj.get("pending"), f"{name}.pending"),
        )

    def to_dict(self) -> dict[str, object]:
        return {"head": self.head, "tail": self.tail, "pending": self.pending}


@dataclass(frozen=True)
class FeederSettings:
    values: Mapping[str, object]

    @classmethod
    def from_dict(cls, data: object) -> "FeederSettings":
        obj = _object(data, "settings")
        return cls(values=dict(obj))

    def get(self, key: str, default: object = None) -> object:
        return self.values.get(key, default)

    def __getitem__(self, key: str) -> object:
        return self.values[key]

    def to_dict(self) -> dict[str, object]:
        return dict(self.values)


@dataclass(frozen=True)
class FeederPlan:
    id: int
    minute: int
    hour_utc: int
    time_utc: str
    time_local_candidate: str
    days_raw: tuple[int, ...]
    days: tuple[str, ...]
    portions: int
    enabled_raw: int
    bowl_or_target_raw: int

    @classmethod
    def from_dict(cls, data: object, index: int) -> "FeederPlan":
        name = f"plans.semantic_records[{index}]"
        obj = _object(data, name)
        days_raw_obj = obj.get("days_raw")
        days_obj = obj.get("days")
        if not isinstance(days_raw_obj, list):
            raise StateAgentBadResponse(f"{name}.days_raw must be an array")
        if not isinstance(days_obj, list):
            raise StateAgentBadResponse(f"{name}.days must be an array")
        days_raw = tuple(
            _integer(value, f"{name}.days_raw[{day_index}]", 1, 7)
            for day_index, value in enumerate(days_raw_obj)
        )
        if len(days_raw) != len(set(days_raw)):
            raise StateAgentBadResponse(f"{name}.days_raw contains duplicates")
        days = tuple(
            _nonempty_string(value, f"{name}.days[{day_index}]")
            for day_index, value in enumerate(days_obj)
        )
        return cls(
            id=_integer(obj.get("id"), f"{name}.id", 1, 255),
            minute=_integer(obj.get("minute"), f"{name}.minute", 0, 59),
            hour_utc=_integer(obj.get("hour_utc"), f"{name}.hour_utc", 0, 23),
            time_utc=_nonempty_string(obj.get("time_utc"), f"{name}.time_utc"),
            time_local_candidate=_nonempty_string(
                obj.get("time_local_candidate"), f"{name}.time_local_candidate"
            ),
            days_raw=days_raw,
            days=days,
            portions=_integer(obj.get("portions"), f"{name}.portions", 0, 255),
            enabled_raw=_integer(
                obj.get("enabled_raw"), f"{name}.enabled_raw", 0, 255
            ),
            bowl_or_target_raw=_integer(
                obj.get("bowl_or_target_raw"),
                f"{name}.bowl_or_target_raw",
                0,
                255,
            ),
        )

    def semantic_fingerprint(self) -> tuple[object, ...]:
        return (
            self.id,
            self.hour_utc,
            self.minute,
            tuple(sorted(self.days_raw)),
            self.portions,
            self.enabled_raw,
            self.bowl_or_target_raw,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "minute": self.minute,
            "hour_utc": self.hour_utc,
            "time_utc": self.time_utc,
            "time_local_candidate": self.time_local_candidate,
            "days_raw": list(self.days_raw),
            "days": list(self.days),
            "portions": self.portions,
            "enabled_raw": self.enabled_raw,
            "bowl_or_target_raw": self.bowl_or_target_raw,
        }


@dataclass(frozen=True)
class FeederPlans:
    count: int
    plan_bin_size: int
    record_size: int
    even_split: bool
    semantic_records: tuple[FeederPlan, ...]

    @classmethod
    def from_dict(cls, data: object) -> "FeederPlans":
        obj = _object(data, "plans")
        if obj.get("ok") is not True:
            raise StateAgentBadResponse("plans.ok must be true")
        records_obj = obj.get("semantic_records")
        if not isinstance(records_obj, list):
            raise StateAgentBadResponse("plans.semantic_records must be an array")
        records = tuple(
            FeederPlan.from_dict(value, index)
            for index, value in enumerate(records_obj)
        )
        count = _integer(obj.get("count"), "plans.count", 0, 255)
        if count != len(records):
            raise StateAgentBadResponse(
                "plans.count does not match plans.semantic_records"
            )
        ids = [plan.id for plan in records]
        if len(ids) != len(set(ids)):
            raise StateAgentBadResponse("plans.semantic_records contains duplicate IDs")
        return cls(
            count=count,
            plan_bin_size=_integer(
                obj.get("plan_bin_size"), "plans.plan_bin_size", 0
            ),
            record_size=_integer(obj.get("record_size"), "plans.record_size", 0),
            even_split=_boolean(obj.get("even_split"), "plans.even_split"),
            semantic_records=records,
        )

    def by_id(self, plan_id: int) -> FeederPlan | None:
        return next((plan for plan in self.semantic_records if plan.id == plan_id), None)

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": True,
            "count": self.count,
            "plan_bin_size": self.plan_bin_size,
            "record_size": self.record_size,
            "even_split": self.even_split,
            "semantic_records": [plan.to_dict() for plan in self.semantic_records],
        }


@dataclass(frozen=True)
class FeederTruth:
    read_ms: int
    revisions: FeederRevisions
    settings: FeederSettings
    plans: FeederPlans
    queue: FeederQueue

    @classmethod
    def from_dict(cls, data: object) -> "FeederTruth":
        obj = _successful_object(data)
        return cls(
            read_ms=_integer(obj.get("read_ms"), "read_ms", 0),
            revisions=FeederRevisions.from_dict(obj.get("revisions")),
            settings=FeederSettings.from_dict(obj.get("settings")),
            plans=FeederPlans.from_dict(obj.get("plans")),
            queue=FeederQueue.from_dict(obj.get("queue")),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": True,
            "read_ms": self.read_ms,
            "revisions": self.revisions.to_dict(),
            "settings": self.settings.to_dict(),
            "plans": self.plans.to_dict(),
            "queue": self.queue.to_dict(),
        }


@dataclass(frozen=True)
class RevisionSnapshot:
    read_ms: int
    revisions: FeederRevisions
    queue: FeederQueue

    @classmethod
    def from_dict(cls, data: object) -> "RevisionSnapshot":
        obj = _successful_object(data)
        return cls(
            read_ms=_integer(obj.get("read_ms"), "read_ms", 0),
            revisions=FeederRevisions.from_dict(obj.get("revisions")),
            queue=FeederQueue.from_dict(obj.get("queue")),
        )


@dataclass(frozen=True)
class FeedEventSnapshot:
    queue: FeederQueue
    err_queue: FeederQueue
    events: tuple[Mapping[str, object], ...]

    @classmethod
    def from_dict(cls, data: object) -> "FeedEventSnapshot":
        obj = _successful_object(data)
        events = obj.get("events")
        if not isinstance(events, list) or not all(
            isinstance(event_value, dict) for event_value in events
        ):
            raise StateAgentBadResponse("events must be an array of objects")
        return cls(
            queue=FeederQueue.from_dict(obj.get("queue")),
            err_queue=FeederQueue.from_dict(obj.get("err_queue"), "err_queue"),
            events=tuple(dict(event_value) for event_value in events),
        )


class StateAgentClient:
    """Small blocking HTTP client intended to run in an AppDaemon executor."""

    def __init__(self, base_url: str, token: str, timeout_seconds: float = 2.0):
        parsed = parse.urlsplit(base_url.rstrip("/"))
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("state-agent URL must be an HTTP(S) URL with a host")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("state-agent URL must not contain credentials, query, or fragment")
        if timeout_seconds <= 0:
            raise ValueError("state-agent timeout must be positive")
        self._base_url = parse.urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "")
        )
        self._token = token
        self._timeout_seconds = float(timeout_seconds)

    def health(self) -> Mapping[str, object]:
        return _successful_object(self._get("/health"))

    def revisions(self) -> RevisionSnapshot:
        return RevisionSnapshot.from_dict(self._get("/v1/rev"))

    def core(self) -> FeederTruth:
        return FeederTruth.from_dict(self._get("/v1/core"))

    def feed_events(self) -> FeedEventSnapshot:
        return FeedEventSnapshot.from_dict(self._get("/v1/feed-events"))

    def _get(self, path: str) -> Mapping[str, object]:
        req = request.Request(
            self._base_url + path,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self._token}",
            },
            method="GET",
        )
        try:
            with request.urlopen(req, timeout=self._timeout_seconds) as response:
                body = response.read(MAX_RESPONSE_BYTES + 1)
        except error.HTTPError as exc:
            if exc.code in {401, 403}:
                raise StateAgentUnauthorized("state agent rejected authentication") from None
            raise StateAgentUnavailable(
                f"state agent returned HTTP status {exc.code}"
            ) from None
        except (TimeoutError, socket.timeout):
            raise StateAgentTimeout("state agent request timed out") from None
        except (error.URLError, OSError):
            raise StateAgentUnavailable("state agent is unavailable") from None
        if len(body) > MAX_RESPONSE_BYTES:
            raise StateAgentBadResponse("state agent response exceeds size limit")
        try:
            decoded = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise StateAgentBadResponse("state agent returned invalid JSON") from None
        return _object(decoded, "response")


class UnavailableStateAgentClient:
    """Used while discovery has not resolved a feeder address yet."""

    def _unavailable(self):
        raise StateAgentUnavailable("state-agent URL is not configured")

    health = _unavailable
    revisions = _unavailable
    core = _unavailable
    feed_events = _unavailable


def _successful_object(data: object) -> Mapping[str, Any]:
    obj = _object(data, "response")
    if obj.get("ok") is not True:
        raise StateAgentBadResponse("state agent response did not report success")
    return obj


def _object(data: object, name: str) -> Mapping[str, Any]:
    if not isinstance(data, dict):
        raise StateAgentBadResponse(f"{name} must be an object")
    return data


def _nonempty_string(data: object, name: str) -> str:
    if not isinstance(data, str) or not data:
        raise StateAgentBadResponse(f"{name} must be a non-empty string")
    return data


def _integer(
    data: object, name: str, minimum: int | None = None, maximum: int | None = None
) -> int:
    if isinstance(data, bool) or not isinstance(data, int):
        raise StateAgentBadResponse(f"{name} must be an integer")
    if minimum is not None and data < minimum:
        raise StateAgentBadResponse(f"{name} is below its minimum")
    if maximum is not None and data > maximum:
        raise StateAgentBadResponse(f"{name} exceeds its maximum")
    return data


def _boolean(data: object, name: str) -> bool:
    if not isinstance(data, bool):
        raise StateAgentBadResponse(f"{name} must be a boolean")
    return data
