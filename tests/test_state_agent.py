import json
from unittest.mock import patch

import pytest

from state_agent import (
    FeederTruth,
    StateAgentBadResponse,
    StateAgentClient,
)


class FakeResponse:
    def __init__(self, payload):
        self.body = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit):
        return self.body


def core_payload(*, enabled_raw=1, bowl_or_target_raw=2):
    return {
        "ok": True,
        "read_ms": 1,
        "revisions": {
            "core_rev": "fnv64:core",
            "settings_rev": "fnv64:settings",
            "plans_rev": "fnv64:plans",
            "queue_index_rev": "fnv64:queue",
        },
        "settings": {
            "volume": 76,
            "camera_resolution": "1080p",
        },
        "plans": {
            "ok": True,
            "count": 1,
            "plan_bin_size": 47,
            "record_size": 47,
            "even_split": True,
            "semantic_records": [
                {
                    "id": 1,
                    "minute": 0,
                    "hour_utc": 11,
                    "time_utc": "11:00",
                    "time_local_candidate": "07:00",
                    "days_raw": [1, 2, 3, 4, 5, 6, 7],
                    "days": [
                        "monday",
                        "tuesday",
                        "wednesday",
                        "thursday",
                        "friday",
                        "saturday",
                        "sunday",
                    ],
                    "portions": 10,
                    "enabled_raw": enabled_raw,
                    "bowl_or_target_raw": bowl_or_target_raw,
                }
            ],
        },
        "queue": {"head": 23, "tail": 23, "pending": False},
    }


def test_core_parser_builds_explicit_truth_models():
    truth = FeederTruth.from_dict(core_payload())

    assert truth.settings["volume"] == 76
    assert truth.revisions.core_rev == "fnv64:core"
    assert truth.plans.count == 1
    assert truth.plans.by_id(1).enabled_raw == 1
    assert truth.plans.by_id(1).bowl_or_target_raw == 2


def test_core_parser_rejects_count_mismatch_and_duplicate_days():
    payload = core_payload()
    payload["plans"]["count"] = 2
    with pytest.raises(StateAgentBadResponse, match="plans.count"):
        FeederTruth.from_dict(payload)

    payload = core_payload()
    payload["plans"]["semantic_records"][0]["days_raw"] = [1, 1]
    with pytest.raises(StateAgentBadResponse, match="duplicates"):
        FeederTruth.from_dict(payload)


def test_client_rejects_credentials_in_url_and_never_exposes_token():
    token = "top-secret-token"
    with pytest.raises(ValueError, match="credentials") as error:
        StateAgentClient(
            "http://user:password@192.0.2.1:8765", token, timeout_seconds=1
        )
    assert token not in str(error.value)


def test_client_sends_bearer_auth_and_parses_all_read_only_endpoints():
    responses = [
        FakeResponse({"ok": True, "agent": {"name": "state-agent"}}),
        FakeResponse(
            {
                "ok": True,
                "read_ms": 0,
                "revisions": core_payload()["revisions"],
                "queue": core_payload()["queue"],
            }
        ),
        FakeResponse(core_payload()),
        FakeResponse(
            {
                "ok": True,
                "queue": core_payload()["queue"],
                "err_queue": {"head": 255, "tail": 255, "pending": False},
                "events": [],
            }
        ),
    ]
    client = StateAgentClient(
        "http://192.0.2.1:8765", "synthetic-test-token", timeout_seconds=1
    )

    with patch("state_agent.request.urlopen", side_effect=responses) as urlopen:
        assert client.health()["ok"] is True
        assert client.revisions().revisions.core_rev == "fnv64:core"
        assert client.core().plans.by_id(1).portions == 10
        assert client.feed_events().events == ()

    assert [call.args[0].full_url for call in urlopen.call_args_list] == [
        "http://192.0.2.1:8765/health",
        "http://192.0.2.1:8765/v1/rev",
        "http://192.0.2.1:8765/v1/core",
        "http://192.0.2.1:8765/v1/feed-events",
    ]
    assert all(
        call.args[0].get_header("Authorization")
        == "Bearer synthetic-test-token"
        for call in urlopen.call_args_list
    )
