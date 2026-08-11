"""Discover Petlibro feeders and maintain their local stream registry."""

from __future__ import annotations

import datetime
import ipaddress
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Callable

from appdaemon.adbase import ADBase
from appdaemon.exceptions import DomainException


SCHEMA_VERSION = 1
DISCOVERY_METHOD = "lan_search3"
UID_SOURCE = "DEVICE_START_EVENT.uuid"
DEVICE_TOPIC_RE = re.compile(
    r"^dl/(?P<product>[A-Za-z0-9_-]+)/(?P<serial>[A-Za-z0-9_-]+)/device(?:/.*)?$"
)


def utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def format_timestamp(value: datetime.datetime) -> str:
    return value.astimezone(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def parse_timestamp(value: object) -> datetime.datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.datetime.fromisoformat(
            value[:-1] + "+00:00" if value.endswith("Z") else value
        )
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(datetime.timezone.utc)


def parse_device_topic(topic: object, product_filter: str) -> tuple[str, str, str] | None:
    if not isinstance(topic, str):
        return None
    match = DEVICE_TOPIC_RE.fullmatch(topic)
    if match is None or match.group("product") != product_filter:
        return None
    product = match.group("product")
    serial = match.group("serial")
    return product, serial, f"dl/{product}/{serial}/device"


def extract_device_start_uid(topic: object, payload: object, product_filter: str) -> str | None:
    parsed = parse_device_topic(topic, product_filter)
    if parsed is None or not str(topic).endswith("/event/post"):
        return None
    if isinstance(payload, bytes):
        try:
            payload = payload.decode("utf-8")
        except UnicodeDecodeError:
            return None
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return None
    if not isinstance(payload, dict) or payload.get("cmd") != "DEVICE_START_EVENT":
        return None
    uid = payload.get("uuid")
    if not isinstance(uid, str) or re.fullmatch(r"[A-Za-z0-9]{20}", uid) is None:
        return None
    return uid


def stream_name_for(product: str, serial: str) -> str:
    product_part = re.sub(r"[^A-Za-z0-9]+", "_", product).strip("_").lower()
    serial_part = re.sub(r"[^A-Za-z0-9]+", "_", serial).strip("_").lower()
    return f"petlibro_{product_part}_{serial_part}"[:96]


def status_file_for(stream_name: str) -> Path:
    return Path(f"/data/petlibro_camera_status_{stream_name}.json")


def default_ready() -> dict[str, bool]:
    return {
        "mqtt_discovered": False,
        "uid_discovered": False,
        "ip_resolved": False,
        "stream_configured": False,
        "camera_online": False,
    }


class DeviceRegistry:
    """Schema-validated, atomic persistence for discovery state."""

    def __init__(self, path: str | Path, now: Callable[[], datetime.datetime] = utc_now):
        self.path = Path(path)
        self.now = now
        self.data: dict[str, object] = {"schema_version": SCHEMA_VERSION, "devices": {}}
        self.load()

    @property
    def devices(self) -> dict[str, dict]:
        return self.data["devices"]  # type: ignore[return-value]

    def load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        if not isinstance(raw, dict) or raw.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("unsupported devices registry schema")
        devices = raw.get("devices")
        if not isinstance(devices, dict):
            raise ValueError("devices registry must contain an object")
        clean = {}
        for key, device in devices.items():
            if isinstance(key, str) and isinstance(device, dict):
                clean[key] = self._sanitize(device)
        self.data = {"schema_version": SCHEMA_VERSION, "devices": clean}

    def observe(self, product: str, serial: str, topic_root: str) -> tuple[str, dict, bool]:
        key = f"{product}/{serial}"
        created = key not in self.devices
        device = self.devices.setdefault(key, {})
        device.update(
            {
                "product": product,
                "serial": serial,
                "mqtt_topic_root": topic_root,
                "last_mqtt_seen": format_timestamp(self.now()),
                "stream_name": str(
                    device.get("stream_name") or stream_name_for(product, serial)
                ),
            }
        )
        ready = default_ready() | self._ready(device)
        ready["mqtt_discovered"] = True
        device["ready"] = ready
        return key, device, created

    def set_uid(self, device: dict, uid: str, source: str = UID_SOURCE) -> bool:
        changed = device.get("uid") != uid or device.get("uid_source") != source
        device["uid"] = uid
        device["uid_source"] = source
        ready = default_ready() | self._ready(device)
        ready["uid_discovered"] = True
        device["ready"] = ready
        return changed

    def resolution_succeeded(self, device: dict, ip_address: str) -> bool:
        ipaddress.ip_address(ip_address)
        existing_ready = self._ready(device)
        changed = (
            device.get("ip_address") != ip_address
            or not existing_ready.get("stream_configured", False)
        )
        device.update(
            {
                "ip_address": ip_address,
                "ip_resolution_method": DISCOVERY_METHOD,
                "last_ip_resolved": format_timestamp(self.now()),
                "last_ip_attempt": format_timestamp(self.now()),
            }
        )
        ready = default_ready() | self._ready(device)
        ready["ip_resolved"] = True
        ready["stream_configured"] = True
        device["ready"] = ready
        return changed

    def resolution_failed(self, device: dict) -> None:
        device["last_ip_attempt"] = format_timestamp(self.now())
        ready = default_ready() | self._ready(device)
        ready["ip_resolved"] = False
        device["ready"] = ready

    def set_camera_online(self, device: dict, online: bool) -> bool:
        ready = default_ready() | self._ready(device)
        changed = ready["camera_online"] != online
        ready["camera_online"] = online
        device["ready"] = ready
        return changed

    def persist(self) -> bool:
        content = (json.dumps(self.data, indent=2, sort_keys=True) + "\n").encode()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            if self.path.read_bytes() == content:
                os.chmod(self.path, 0o600)
                return False
        except FileNotFoundError:
            pass
        with tempfile.NamedTemporaryFile(dir=self.path.parent, delete=False) as handle:
            handle.write(content)
            handle.flush()
            os.fchmod(handle.fileno(), 0o600)
            temporary = Path(handle.name)
        temporary.replace(self.path)
        return True

    def public_payload(self, device: dict, publish_ip: bool = True) -> dict:
        ready = default_ready() | self._ready(device)
        payload = {
            "schema_version": SCHEMA_VERSION,
            "product": device.get("product"),
            "serial": device.get("serial"),
            "mqtt_topic_root": device.get("mqtt_topic_root"),
            "uid_discovered": ready["uid_discovered"],
            "uid_source": device.get("uid_source"),
            "ip_resolved": ready["ip_resolved"],
            "ip_resolution_method": device.get("ip_resolution_method"),
            "stream_name": device.get("stream_name"),
            "stream_configured": ready["stream_configured"],
            "camera_online": ready["camera_online"],
            "last_mqtt_seen": device.get("last_mqtt_seen"),
            "last_ip_resolved": device.get("last_ip_resolved"),
            "last_update": format_timestamp(self.now()),
        }
        if publish_ip:
            payload["ip_address"] = device.get("ip_address")
        return payload

    @staticmethod
    def _sanitize(device: dict) -> dict:
        allowed = {
            "product",
            "serial",
            "uid",
            "uid_source",
            "ip_address",
            "ip_resolution_method",
            "stream_name",
            "mqtt_topic_root",
            "last_mqtt_seen",
            "last_ip_resolved",
            "last_ip_attempt",
            "manual_override",
            "ready",
        }
        clean = {key: value for key, value in device.items() if key in allowed}
        ready = clean.get("ready")
        clean["ready"] = default_ready() | (ready if isinstance(ready, dict) else {})
        return clean

    @staticmethod
    def _ready(device: dict) -> dict:
        ready = device.get("ready")
        return ready if isinstance(ready, dict) else {}


def resolve_uid(command: str, uid: str, subnet: str, timeout_seconds: int) -> dict:
    completed = subprocess.run(
        [
            command,
            "--uid",
            uid,
            "--subnet",
            subnet,
            "--timeout",
            f"{timeout_seconds}s",
            "--json",
        ],
        capture_output=True,
        text=True,
        timeout=timeout_seconds + 5,
        check=False,
    )
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as err:
        raise RuntimeError("resolver returned invalid JSON") from err
    if not isinstance(result, dict) or not isinstance(result.get("resolved"), bool):
        raise RuntimeError("resolver result has an invalid schema")
    if result["resolved"]:
        ipaddress.ip_address(str(result.get("ip_address", "")))
    return result


class RuntimeReloader:
    def __init__(
        self,
        renderer: str,
        service: str,
        config_path: str | Path = "/data/go2rtc.yaml",
        runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    ):
        self.renderer = renderer
        self.service = service
        self.config_path = Path(config_path)
        self.runner = runner

    def apply(self) -> bool:
        config = self.config_path
        before = config.read_bytes() if config.exists() else None
        self.runner([self.renderer, "appdaemon"], check=True, timeout=15)
        self.runner([self.renderer, "go2rtc"], check=True, timeout=15)
        after = config.read_bytes() if config.exists() else None
        changed = before != after
        if changed:
            self.runner(["s6-svc", "-r", self.service], check=True, timeout=15)
        return changed


class PetlibroDiscovery(ADBase):
    """AppDaemon coordinator for feeder identity and camera address discovery."""

    def initialize(self):
        self.ad = self.get_ad_api()
        self.mqtt = self.get_plugin_api("MQTT")
        self.enabled = bool(self.args.get("enabled", True))
        self.product_filter = str(self.args.get("product_filter", "PLAF203"))
        self.lan_cidr = str(self.args["lan_cidr"])
        self.timeout_seconds = int(self.args.get("ip_resolve_timeout_seconds", 10))
        self.refresh_minutes = int(self.args.get("ip_refresh_interval_minutes", 360))
        self.retry_seconds = int(self.args.get("ip_retry_backoff_seconds", 60))
        self.publish_ip = bool(self.args.get("publish_discovery_ip", True))
        self.resolver_command = str(self.args["resolver_command"])
        self.registry = DeviceRegistry(str(self.args["registry_file"]))
        self.reloader = RuntimeReloader(
            str(self.args["renderer_command"]), str(self.args["go2rtc_service"])
        )
        self.flush_handle = None
        self.config_dirty = False
        self.resolving: set[str] = set()
        self.publish_unavailable = False
        if self.enabled:
            self.mqtt.listen_event(
                self._mqtt_device_event,
                "MQTT_MESSAGE",
                topic="dl/+/+/device/#",
                namespace="mqtt",
            )
        self.mqtt.listen_event(
            self._manual_refresh_event,
            "MQTT_MESSAGE",
            topic="petlibro_local/discovery/refresh",
            namespace="mqtt",
        )
        self.timer = self.ad.run_every(self._periodic, "immediate", 30)
        self._publish_all()

    def terminate(self):
        if getattr(self, "timer", None) is not None:
            self.ad.cancel_timer(self.timer, True)
        if self.flush_handle is not None:
            self.ad.cancel_timer(self.flush_handle, True)

    def _mqtt_device_event(self, _event_name: str, data: dict, _kwargs) -> None:
        parsed = parse_device_topic(data.get("topic"), self.product_filter)
        if parsed is None:
            return
        product, serial, topic_root = parsed
        key, device, created = self.registry.observe(product, serial, topic_root)
        uid = extract_device_start_uid(
            data.get("topic"), data.get("payload"), self.product_filter
        )
        uid_changed = self.registry.set_uid(device, uid) if uid is not None else False
        self._schedule_flush(config_dirty=created or uid_changed)
        if uid is not None:
            self._schedule_resolution(key, force=False)

    def _manual_refresh_event(self, _event_name: str, data: dict, _kwargs) -> None:
        requested = None
        payload = data.get("payload")
        if isinstance(payload, str) and payload not in {"", "all"}:
            try:
                decoded = json.loads(payload)
            except json.JSONDecodeError:
                requested = payload
            else:
                requested = decoded.get("serial") if isinstance(decoded, dict) else None
        for key, device in self.registry.devices.items():
            if requested is None or requested in {key, device.get("serial")}:
                self._schedule_resolution(key, force=True)

    def _schedule_flush(self, *, config_dirty: bool) -> None:
        self.config_dirty = self.config_dirty or config_dirty
        if self.flush_handle is None:
            self.flush_handle = self.ad.run_in(self._flush, 1)

    def _flush(self, _kwargs=None) -> None:
        self.flush_handle = None
        self.registry.persist()
        if self.config_dirty:
            try:
                restarted = self.reloader.apply()
            except (OSError, subprocess.SubprocessError) as err:
                self.ad.log(
                    f"Petlibro discovery: configuration update failed ({type(err).__name__})"
                )
                self.flush_handle = self.ad.run_in(self._flush, self.retry_seconds)
            else:
                self.ad.log(
                    "Petlibro discovery: configuration updated"
                    + ("; go2rtc restarted" if restarted else "")
                )
                self.config_dirty = False
        self._publish_all()

    def _periodic(self, _kwargs=None) -> None:
        now = utc_now()
        changed = False
        for key, device in self.registry.devices.items():
            camera_online, unhealthy = self._camera_health(device, now)
            changed = self.registry.set_camera_online(device, camera_online) or changed
            if self._resolution_due(device, now, unhealthy):
                self._schedule_resolution(key, force=False)
        if changed:
            self._schedule_flush(config_dirty=False)
        else:
            self._publish_all()

    def _camera_health(self, device: dict, now: datetime.datetime) -> tuple[bool, bool]:
        stream_name = str(device.get("stream_name", ""))
        if not stream_name:
            return False, False
        try:
            status = json.loads(status_file_for(stream_name).read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
            return False, False
        update = parse_timestamp(status.get("last_update")) if isinstance(status, dict) else None
        stale = update is None or now - update > datetime.timedelta(seconds=90)
        state = status.get("status") if isinstance(status, dict) else None
        return state == "online" and not stale, state in {"offline", "error"} or stale

    def _resolution_due(self, device: dict, now: datetime.datetime, unhealthy: bool) -> bool:
        uid = device.get("uid")
        if not isinstance(uid, str) or len(uid) != 20:
            return False
        if device.get("manual_override") and device.get("ip_address"):
            return False
        last_attempt = parse_timestamp(device.get("last_ip_attempt"))
        if last_attempt and now - last_attempt < datetime.timedelta(seconds=self.retry_seconds):
            return False
        last_resolved = parse_timestamp(device.get("last_ip_resolved"))
        expired = last_resolved is None or now - last_resolved >= datetime.timedelta(
            minutes=self.refresh_minutes
        )
        return not device.get("ip_address") or expired or unhealthy

    def _schedule_resolution(self, key: str, *, force: bool) -> None:
        if key in self.resolving:
            return
        device = self.registry.devices.get(key)
        if device is None or not isinstance(device.get("uid"), str):
            return
        if device.get("manual_override") and device.get("ip_address"):
            return
        if not force and not self._resolution_due(device, utc_now(), False):
            return
        self.resolving.add(key)
        # Give the debounced app-config write time to create the device's
        # controller before a potentially blocking LAN lookup begins.
        self.ad.run_in(self._resolve, 2, device_key=key)

    def _resolve(self, kwargs: dict) -> None:
        key = str(kwargs["device_key"])
        device = self.registry.devices.get(key)
        if device is None:
            self.resolving.discard(key)
            return
        old_ip = device.get("ip_address")
        try:
            result = resolve_uid(
                self.resolver_command,
                str(device["uid"]),
                self.lan_cidr,
                self.timeout_seconds,
            )
            if not result["resolved"]:
                self.registry.resolution_failed(device)
                self.ad.log(f"Petlibro discovery: IP resolution failed for {key}")
                changed = False
            else:
                changed = self.registry.resolution_succeeded(
                    device, str(result["ip_address"])
                )
                self.ad.log(f"Petlibro discovery: resolved IP for {key}")
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as err:
            self.registry.resolution_failed(device)
            self.ad.log(
                f"Petlibro discovery: IP resolution failed for {key} ({type(err).__name__})"
            )
            changed = False
        finally:
            self.resolving.discard(key)
        self._schedule_flush(
            config_dirty=changed
            or (old_ip is None and device.get("ip_address") is not None)
        )

    def _publish_all(self) -> None:
        payloads = []
        for key, device in sorted(self.registry.devices.items()):
            payload = self.registry.public_payload(device, self.publish_ip)
            payloads.append(payload)
            self._publish_json(
                f"petlibro_local/{key}/discovery/state",
                payload,
            )
        self._publish_json(
            "petlibro_local/discovery/devices",
            {
                "schema_version": SCHEMA_VERSION,
                "devices": payloads,
                "last_update": format_timestamp(self.registry.now()),
            },
        )

    def _publish_json(self, topic: str, payload: dict) -> bool:
        try:
            self.mqtt.mqtt_publish(
                topic,
                json.dumps(payload, sort_keys=True, separators=(",", ":")),
                namespace="mqtt",
                qos=1,
                retain=True,
            )
        except DomainException:
            if not self.publish_unavailable:
                self.ad.log(
                    "Petlibro discovery: MQTT is unavailable; readiness publish deferred"
                )
            self.publish_unavailable = True
            return False
        self.publish_unavailable = False
        return True
