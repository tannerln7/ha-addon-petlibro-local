import datetime
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
MODULE_PATH = ROOT / "src" / "device_discovery.py"
SPEC = importlib.util.spec_from_file_location("device_discovery", MODULE_PATH)
device_discovery = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(device_discovery)


def fixed_now():
    return datetime.datetime(2026, 8, 11, 12, 0, tzinfo=datetime.timezone.utc)


def test_extracts_serial_and_uid_only_from_valid_device_start_event():
    topic = "dl/PLAF203/EXAMPLE123/device/event/post"
    payload = json.dumps(
        {"cmd": "DEVICE_START_EVENT", "uuid": "PLAF20300000000ABCD0"}
    )
    assert device_discovery.parse_device_topic(topic, "PLAF203") == (
        "PLAF203",
        "EXAMPLE123",
        "dl/PLAF203/EXAMPLE123/device",
    )
    assert (
        device_discovery.extract_device_start_uid(topic, payload, "PLAF203")
        == "PLAF20300000000ABCD0"
    )
    assert device_discovery.extract_device_start_uid(
        topic, payload, "OTHER"
    ) is None
    assert device_discovery.extract_device_start_uid(
        topic, '{"cmd":"OTHER","uuid":"PLAF20300000000ABCD0"}', "PLAF203"
    ) is None


def test_stream_name_sanitization_is_stable_and_safe():
    assert (
        device_discovery.stream_name_for("PLAF203", "Kitchen Feeder/One")
        == "petlibro_plaf203_kitchen_feeder_one"
    )


def test_discovery_registers_mqtt_device_filter_as_wildcard():
    class FakeAD:
        def run_every(self, *_args, **_kwargs):
            return "timer"

    class FakeMQTT:
        def __init__(self):
            self.listeners = []

        def listen_event(self, callback, event, **kwargs):
            self.listeners.append((callback, event, kwargs))

        def mqtt_publish(self, *_args, **_kwargs):
            return None

    with tempfile.TemporaryDirectory() as temporary:
        coordinator = object.__new__(device_discovery.PetlibroDiscovery)
        coordinator.args = {
            "enabled": True,
            "product_filter": "PLAF203",
            "lan_cidr": "192.0.2.0/24",
            "registry_file": str(Path(temporary) / "devices.json"),
            "resolver_command": "petlibro-resolve",
            "renderer_command": "petlibro-render-config",
            "go2rtc_service": "/run/service/go2rtc",
        }
        fake_ad = FakeAD()
        fake_mqtt = FakeMQTT()
        coordinator.get_ad_api = lambda: fake_ad
        coordinator.get_plugin_api = lambda _name: fake_mqtt

        coordinator.initialize()

    _callback, event, filters = fake_mqtt.listeners[0]
    assert event == "MQTT_MESSAGE"
    assert filters["wildcard"] == "dl/+/+/device/#"
    assert "topic" not in filters


def test_registry_is_atomic_private_and_excludes_credentials():
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "devices.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "devices": {
                        "PLAF203/EXAMPLE123": {
                            "product": "PLAF203",
                            "serial": "EXAMPLE123",
                            "mqtt_password": "must-not-survive",
                            "product_secret": "must-not-survive",
                            "ready": "malformed",
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        registry = device_discovery.DeviceRegistry(path, now=fixed_now)
        _key, feeder, _created = registry.observe(
            "PLAF203", "EXAMPLE123", "dl/PLAF203/EXAMPLE123/device"
        )
        registry.set_uid(feeder, "PLAF20300000000ABCD0")
        registry.resolution_succeeded(feeder, "192.0.2.100")
        assert registry.persist()
        saved = path.read_text(encoding="utf-8")
        assert "must-not-survive" not in saved
        assert os.stat(path).st_mode & 0o777 == 0o600
        assert not registry.persist()


def test_resolver_helper_json_is_validated():
    stats = {
        "broadcasts_sent": 1,
        "unicasts_sent": 0,
        "packets_received": 1,
        "responses_rejected": 0,
        "send_errors": 0,
        "deadline_exceeded": False,
    }
    success = subprocess.CompletedProcess(
        [],
        0,
        stdout=json.dumps(
            {
                "resolved": True,
                "ip_address": "192.0.2.100",
                "method": "broadcast",
                "elapsed_ms": 50,
                "stats": stats,
            }
        ),
        stderr="",
    )
    with patch.object(device_discovery.subprocess, "run", return_value=success) as run:
        result = device_discovery.resolve_uid(
            "/usr/local/bin/petlibro-resolve",
            "PLAF20300000000ABCD0",
            "192.0.2.0/24",
            10,
        )
    assert result["ip_address"] == "192.0.2.100"
    assert run.call_args.args[0][-1] == "--json"

    invalid = subprocess.CompletedProcess([], 1, stdout="not-json", stderr="")
    with patch.object(device_discovery.subprocess, "run", return_value=invalid):
        result = device_discovery.resolve_uid(
            "/usr/local/bin/petlibro-resolve",
            "PLAF20300000000ABCD0",
            "192.0.2.0/24",
            10,
        )
    assert result["error_code"] == "invalid_helper_output"

    unresolved = subprocess.CompletedProcess(
        [],
        1,
        stdout=json.dumps(
            {
                "resolved": False,
                "method": "not_found",
                "elapsed_ms": 10000,
                "error_code": "deadline_exceeded",
                "stats": stats | {"deadline_exceeded": True},
            }
        ),
        stderr="",
    )
    with patch.object(device_discovery.subprocess, "run", return_value=unresolved):
        result = device_discovery.resolve_uid(
            "/usr/local/bin/petlibro-resolve",
            "PLAF20300000000ABCD0",
            "192.0.2.0/24",
            10,
        )
    assert result["resolved"] is False


def test_resolver_timeout_is_classified_without_raising():
    with patch.object(
        device_discovery.subprocess,
        "run",
        side_effect=subprocess.TimeoutExpired(["petlibro-resolve"], 12),
    ):
        result = device_discovery.resolve_uid(
            "/usr/local/bin/petlibro-resolve",
            "PLAF20300000000ABCD0",
            "192.0.2.0/24",
            10,
        )
    assert result["error_code"] == "helper_timeout"
    assert result["stats"]["deadline_exceeded"] is True


def test_resolution_is_submitted_without_blocking_appdaemon_callback():
    class Future:
        def done(self):
            return False

        def cancel(self):
            return False

    class FakeAD:
        def __init__(self):
            self.submission = None

        def submit_to_executor(self, func, *args, callback=None):
            self.submission = (func, args, callback)
            return Future()

        def log(self, *_args, **_kwargs):
            return None

    coordinator = object.__new__(device_discovery.PetlibroDiscovery)
    coordinator.ad = FakeAD()
    coordinator.logger = device_discovery.PetlibroLogger(
        coordinator.ad, "petlibro.discovery", "debug"
    )
    coordinator.registry = type(
        "Registry",
        (),
        {
            "devices": {
                "PLAF203/EXAMPLE123": {"uid": "PLAF20300000000ABCD0"}
            }
        },
    )()
    coordinator.resolving = {"PLAF203/EXAMPLE123"}
    coordinator.resolve_futures = set()
    coordinator._resolve({"device_key": "PLAF203/EXAMPLE123"})
    assert coordinator.ad.submission is not None
    assert coordinator.resolving == {"PLAF203/EXAMPLE123"}
    assert len(coordinator.resolve_futures) == 1


def test_duplicate_resolution_attempt_is_not_scheduled():
    calls = []
    coordinator = object.__new__(device_discovery.PetlibroDiscovery)
    coordinator.resolving = {"PLAF203/EXAMPLE123"}
    coordinator.registry = type(
        "Registry",
        (),
        {"devices": {"PLAF203/EXAMPLE123": {"uid": "PLAF20300000000ABCD0"}}},
    )()
    coordinator.ad = type("AD", (), {"run_in": lambda *_args, **_kwargs: calls.append(1)})()
    coordinator._schedule_resolution("PLAF203/EXAMPLE123", force=True)
    assert calls == []


def test_resolution_completion_serializes_registry_update():
    class FakeAD:
        def log(self, *_args, **_kwargs):
            return None

    with tempfile.TemporaryDirectory() as temporary:
        registry = device_discovery.DeviceRegistry(Path(temporary) / "devices.json")
        key, device, _created = registry.observe(
            "PLAF203", "EXAMPLE123", "dl/PLAF203/EXAMPLE123/device"
        )
        registry.set_uid(device, "PLAF20300000000ABCD0")
        coordinator = object.__new__(device_discovery.PetlibroDiscovery)
        coordinator.ad = FakeAD()
        coordinator.logger = device_discovery.PetlibroLogger(
            coordinator.ad, "petlibro.discovery", "debug"
        )
        coordinator.registry = registry
        coordinator.resolving = {key}
        coordinator.resolve_futures = set()
        flushes = []
        coordinator._schedule_flush = lambda *, config_dirty: flushes.append(
            config_dirty
        )
        coordinator._resolve_complete(
            {
                "device_key": key,
                "uid": "PLAF20300000000ABCD0",
                "result": {
                    "resolved": True,
                    "ip_address": "192.0.2.100",
                    "method": "broadcast",
                    "elapsed_ms": 40,
                    "stats": {
                        "broadcasts_sent": 1,
                        "unicasts_sent": 0,
                        "packets_received": 1,
                        "responses_rejected": 0,
                        "send_errors": 0,
                        "deadline_exceeded": False,
                    },
                },
            }
        )
        assert registry.devices[key]["ip_address"] == "192.0.2.100"
        assert registry.devices[key]["ready"]["ip_resolved"] is True
        assert key not in coordinator.resolving
        assert flushes == [True]


def test_runtime_reloader_restarts_only_when_go2rtc_config_changes():
    with tempfile.TemporaryDirectory() as temporary:
        config = Path(temporary) / "go2rtc.yaml"
        config.write_text("old\n", encoding="utf-8")
        calls = []

        def unchanged(command, **_kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0)

        reloader = device_discovery.RuntimeReloader(
            "render", "/run/service/go2rtc", config, unchanged
        )
        assert not reloader.apply()
        assert all(command[0] != "s6-svc" for command in calls)

        calls.clear()

        def changed(command, **_kwargs):
            calls.append(command)
            if command == ["render", "go2rtc"]:
                config.write_text("new\n", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0)

        reloader = device_discovery.RuntimeReloader(
            "render", "/run/service/go2rtc", config, changed
        )
        assert reloader.apply()
        assert calls[-1] == ["s6-svc", "-r", "/run/service/go2rtc"]


def test_stale_or_error_camera_status_requests_reresolution_after_backoff():
    coordinator = object.__new__(device_discovery.PetlibroDiscovery)
    coordinator.retry_seconds = 60
    coordinator.refresh_minutes = 360
    now = fixed_now()
    device = {
        "uid": "PLAF20300000000ABCD0",
        "ip_address": "192.0.2.100",
        "last_ip_attempt": device_discovery.format_timestamp(
            now - datetime.timedelta(seconds=61)
        ),
        "last_ip_resolved": device_discovery.format_timestamp(now),
    }
    assert coordinator._resolution_due(device, now, unhealthy=True)
    device["last_ip_attempt"] = device_discovery.format_timestamp(
        now - datetime.timedelta(seconds=30)
    )
    assert not coordinator._resolution_due(device, now, unhealthy=True)
    device["manual_override"] = True
    assert not coordinator._resolution_due(device, now, unhealthy=True)


def test_manual_ip_override_is_not_replaced_by_forced_refresh():
    coordinator = object.__new__(device_discovery.PetlibroDiscovery)
    coordinator.resolving = set()
    coordinator.registry = type(
        "Registry",
        (),
        {
            "devices": {
                "PLAF203/EXAMPLE123": {
                    "uid": "PLAF20300000000ABCD0",
                    "ip_address": "192.0.2.100",
                    "manual_override": True,
                }
            }
        },
    )()
    coordinator.ad = type(
        "AD", (), {"run_in": lambda *_args, **_kwargs: None}
    )()
    coordinator._schedule_resolution("PLAF203/EXAMPLE123", force=True)
    assert coordinator.resolving == set()


def test_public_readiness_payload_omits_uid():
    with tempfile.TemporaryDirectory() as temporary:
        registry = device_discovery.DeviceRegistry(
            Path(temporary) / "devices.json", now=fixed_now
        )
        _key, feeder, _created = registry.observe(
            "PLAF203", "EXAMPLE123", "dl/PLAF203/EXAMPLE123/device"
        )
        registry.set_uid(feeder, "PLAF20300000000ABCD0")
        payload = registry.public_payload(feeder)
        assert "uid" not in payload
        assert payload["uid_discovered"] is True
        assert payload["mqtt_topic_root"].endswith("/device")
