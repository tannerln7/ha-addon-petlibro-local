import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from petlibro_logging import PetlibroLogger, normalize_log_level


class FakeAd:
    def __init__(self):
        self.logs = []

    def log(self, message, **kwargs):
        self.logs.append((message, kwargs))


def test_debug_does_not_emit_trace_payloads():
    ad = FakeAd()
    logger = PetlibroLogger(ad, "petlibro.mqtt", "debug")
    logger.trace("MQTT message", payload={"cmd": "HEARTBEAT"})
    logger.debug("message summary", command="HEARTBEAT")
    assert len(ad.logs) == 1
    assert "message summary" in ad.logs[0][0]
    assert "payload" not in ad.logs[0][0]


def test_trace_is_explicit_and_redacts_secrets():
    ad = FakeAd()
    logger = PetlibroLogger(ad, "petlibro.mqtt", "trace")
    logger.trace(
        "MQTT message",
        payload={
            "cmd": "DEVICE_START_EVENT",
            "cameraAuthInfo": "must-not-leak",
            "mqtt_password": "must-not-leak",
            "nested": {"product_secret": "must-not-leak"},
        },
    )
    rendered = ad.logs[0][0]
    assert "must-not-leak" not in rendered
    assert rendered.count("<redacted>") == 3
    assert "[TRACE] [petlibro.mqtt]" in rendered


def test_invalid_level_is_rejected():
    try:
        normalize_log_level("verbose")
    except ValueError as err:
        assert "log_level" in str(err)
    else:
        raise AssertionError("invalid log level was accepted")
