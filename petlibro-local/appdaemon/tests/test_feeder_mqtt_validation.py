import socket
import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from feeder_mqtt_validation import validate_feeder_mqtt_destination


def addrinfo(address: str, port: int = 1883):
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    return [(family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, port))]


def test_accepts_reachable_multi_label_hostname():
    connection = Mock()
    with patch(
        "feeder_mqtt_validation.socket.getaddrinfo",
        return_value=addrinfo("192.168.1.10"),
    ), patch(
        "feeder_mqtt_validation.socket.create_connection",
        return_value=connection,
    ) as connect:
        host, addresses = validate_feeder_mqtt_destination(
            "mqtt.dhcp.example.", 1883
        )

    assert host == "mqtt.dhcp.example"
    assert addresses == ("192.168.1.10",)
    connect.assert_called_once_with(("192.168.1.10", 1883), timeout=2.0)
    connection.close.assert_called_once_with()


@pytest.mark.parametrize(
    ("host", "message"),
    [
        ("core-mosquitto", "multi-label DNS name"),
        ("127.0.0.1", "loopback"),
        ("169.254.10.20", "link-local"),
        ("224.0.0.1", "multicast"),
        ("0.0.0.0", "unspecified"),
        ("172.30.33.5", "Home Assistant internal"),
    ],
)
def test_rejects_destinations_the_feeder_cannot_safely_route_to(host, message):
    with pytest.raises(ValueError, match=message):
        validate_feeder_mqtt_destination(host, 1883)


def test_rejects_hostname_resolving_to_home_assistant_internal_network():
    with patch(
        "feeder_mqtt_validation.socket.getaddrinfo",
        return_value=addrinfo("172.30.32.10"),
    ), pytest.raises(ValueError, match="Home Assistant internal"):
        validate_feeder_mqtt_destination("mqtt.example.test", 1883)


def test_rejects_unresolvable_destination():
    with patch(
        "feeder_mqtt_validation.socket.getaddrinfo",
        side_effect=socket.gaierror("not found"),
    ), pytest.raises(ValueError, match="does not resolve"):
        validate_feeder_mqtt_destination("mqtt.example.test", 1883)


def test_rejects_destination_with_no_reachable_mqtt_listener():
    with patch(
        "feeder_mqtt_validation.socket.getaddrinfo",
        return_value=addrinfo("192.168.1.10"),
    ), patch(
        "feeder_mqtt_validation.socket.create_connection",
        side_effect=TimeoutError,
    ), pytest.raises(ValueError, match="not reachable"):
        validate_feeder_mqtt_destination("mqtt.example.test", 1883)
