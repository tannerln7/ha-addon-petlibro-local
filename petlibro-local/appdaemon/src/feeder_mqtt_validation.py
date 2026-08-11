"""Validate an MQTT destination before it can be persisted to a feeder."""

from __future__ import annotations

import ipaddress
import re
import socket


CONNECT_TIMEOUT_SECONDS = 2.0
HOME_ASSISTANT_INTERNAL_NETWORKS = (
    ipaddress.ip_network("172.30.32.0/23"),
    ipaddress.ip_network("172.30.232.0/23"),
)
_HOST_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")


def _address_rejection(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> str | None:
    if address.is_loopback:
        return "loopback addresses are not reachable by the feeder"
    if address.is_link_local:
        return "link-local addresses are not safe feeder destinations"
    if address.is_multicast:
        return "multicast addresses are not valid MQTT destinations"
    if address.is_unspecified:
        return "unspecified addresses are not valid MQTT destinations"
    if any(address in network for network in HOME_ASSISTANT_INTERNAL_NETWORKS):
        return (
            "Home Assistant internal container-network addresses are not "
            "reachable by the feeder"
        )
    return None


def _normalize_host(host: object) -> str:
    candidate = str(host).strip()
    if not candidate:
        raise ValueError("feeder_mqtt_host is required")

    try:
        address = ipaddress.ip_address(candidate)
    except ValueError:
        candidate = candidate.rstrip(".")
        if "." not in candidate:
            raise ValueError(
                "feeder_mqtt_host must be an IP address or multi-label DNS name; "
                "Home Assistant internal names such as core-mosquitto are unsafe"
            )
        if len(candidate) > 253 or any(
            _HOST_LABEL.fullmatch(label) is None for label in candidate.split(".")
        ):
            raise ValueError("feeder_mqtt_host is not a valid DNS name")
        return candidate

    rejection = _address_rejection(address)
    if rejection is not None:
        raise ValueError(rejection)
    return candidate


def validate_feeder_mqtt_destination(
    host: object,
    port: object,
    *,
    connect_timeout: float = CONNECT_TIMEOUT_SECONDS,
) -> tuple[str, tuple[str, ...]]:
    """Return a normalized, currently reachable feeder broker destination."""

    normalized_host = _normalize_host(host)
    try:
        normalized_port = int(port)
    except (TypeError, ValueError) as err:
        raise ValueError("feeder_mqtt_port must be an integer") from err
    if not 1 <= normalized_port <= 65535:
        raise ValueError("feeder_mqtt_port must be between 1 and 65535")

    try:
        answers = socket.getaddrinfo(
            normalized_host,
            normalized_port,
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as err:
        raise ValueError("feeder_mqtt_host does not resolve from the add-on") from err

    resolved: list[str] = []
    for _family, _type, _protocol, _canonical, sockaddr in answers:
        address_text = str(sockaddr[0])
        address = ipaddress.ip_address(address_text)
        rejection = _address_rejection(address)
        if rejection is not None:
            raise ValueError(rejection)
        if address_text not in resolved:
            resolved.append(address_text)
    if not resolved:
        raise ValueError("feeder_mqtt_host did not resolve to an IP address")

    failures = []
    for address in resolved:
        try:
            connection = socket.create_connection(
                (address, normalized_port), timeout=connect_timeout
            )
        except OSError as err:
            failures.append(err)
            continue
        connection.close()
        return normalized_host, tuple(resolved)

    raise ValueError(
        "feeder MQTT destination is not reachable from the add-on on the "
        "configured port"
    ) from failures[-1]
