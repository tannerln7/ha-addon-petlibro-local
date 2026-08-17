import json

from commands import CommandRouter
from ha_entities import HomeAssistantDiscoveryMqtt, HomeAssistantStatePublisher


class _FakeMQTT:
    def __init__(self):
        self.listeners = []
        self.published = []

    def listen_event(self, callback, event, **kwargs):
        self.listeners.append((callback, event, kwargs))

    def mqtt_publish(self, topic, payload, namespace="mqtt", retain=False):
        self.published.append((topic, payload, namespace, retain))


class _FakeBackend:
    def device_reboot(self):
        return None

    def device_factory_reset(self):
        return None

    def device_wifi_reconnect(self):
        return None

    def device_sd_card_format(self):
        return None

    def food_manual_feed_now(self, _amount):
        return None


class _FakeCoordinator:
    def suppressing_writeback(self):
        return False


class _FakeStorage:
    def food_manual_feed_grain_num_get(self):
        return 3

    def food_manual_feed_grain_num_set(self, _amount):
        return None


class _FakeLogger:
    def debug(self, *_args, **_kwargs):
        return None

    def warning(self, *_args, **_kwargs):
        return None


class _FakeUpdater:
    def __init__(self):
        self.install_calls = 0
        self.check_calls = 0

    def request_install(self):
        self.install_calls += 1

    def request_check(self, *, force, reason):
        assert reason == "manual"
        self.check_calls += 1
        assert force is True


def test_discovery_publishes_update_entity_and_check_button():
    mqtt = _FakeMQTT()
    discovery = HomeAssistantDiscoveryMqtt(mqtt, "EXAMPLE123")

    discovery.discovery_issue()

    update_topics = [
        item for item in mqtt.published if item[0].endswith("state_agent_update/config")
    ]
    assert len(update_topics) == 1
    payload = json.loads(update_topics[0][1])
    assert payload["device_class"] == "firmware"
    assert payload["state_topic"].endswith("/state_agent/update")
    assert payload["command_topic"].endswith("/state_agent/cmd/install")
    assert payload["payload_install"] == "install"
    assert payload["installed_version_template"] == "{{ value_json.installed_version }}"

    check_topics = [
        item
        for item in mqtt.published
        if item[0].endswith("state_agent_check_updates/config")
    ]
    assert len(check_topics) == 1
    check_payload = json.loads(check_topics[0][1])
    assert check_payload["payload_press"] == "check"


def test_command_router_routes_state_agent_update_commands_and_ignores_retained():
    mqtt = _FakeMQTT()
    serial = "EXAMPLE123"
    state = HomeAssistantStatePublisher(mqtt, serial, _FakeLogger())
    updater = _FakeUpdater()
    router = CommandRouter(
        mqtt,
        serial,
        _FakeBackend(),
        _FakeCoordinator(),
        _FakeStorage(),
        state,
        _FakeLogger(),
        updater=updater,
    )

    router.start()

    callbacks = {}
    for callback, event, kwargs in mqtt.listeners:
        assert event == "MQTT_MESSAGE"
        callbacks[kwargs["topic"]] = callback

    install_topic = f"plaf203/{serial}/state_agent/cmd/install"
    check_topic = f"plaf203/{serial}/state_agent/cmd/check_updates"

    callbacks[install_topic](
        "MQTT_MESSAGE", {"payload": "install", "retain": False}, {}
    )
    callbacks[check_topic]("MQTT_MESSAGE", {"payload": "check", "retain": False}, {})
    assert updater.install_calls == 1
    assert updater.check_calls == 1

    callbacks[install_topic]("MQTT_MESSAGE", {"payload": "install", "retain": True}, {})
    callbacks[check_topic]("MQTT_MESSAGE", {"payload": "check", "retain": "true"}, {})
    assert updater.install_calls == 1
    assert updater.check_calls == 1
