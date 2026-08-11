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
from petlibro_logging import PetlibroLogger


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


def _resolver_failure(error_code: str, message: str, elapsed_ms: int = 0) -> dict:
    return {
        "resolved": False,
        "method": "not_found",
        "elapsed_ms": elapsed_ms,
        "error_code": error_code,
        "error": message,
        "stats": {
            "lan_search_w3_1_sent": 0,
            "lan_search_w3_2_sent": 0,
            "knock2_sent": 0,
            "lan_search_r_received": 0,
            "knock_rr2_received": 0,
            "wrong_uid_rejected": 0,
            "nonce_mismatch_rejected": 0,
            "broadcasts_sent": 0,
            "unicasts_sent": 0,
            "packets_received": 0,
            "responses_rejected": 0,
            "send_errors": 0,
            "deadline_exceeded": error_code in {"deadline_exceeded", "helper_timeout"},
        },
    }


def _valid_resolver_result(result: object) -> bool:
    if not isinstance(result, dict) or not isinstance(result.get("resolved"), bool):
        return False
    if not isinstance(result.get("method"), str):
        return False
    elapsed = result.get("elapsed_ms")
    if isinstance(elapsed, bool) or not isinstance(elapsed, int) or elapsed < 0:
        return False
    stats = result.get("stats")
    if not isinstance(stats, dict):
        return False
    for key in (
        "lan_search_w3_1_sent",
        "lan_search_w3_2_sent",
        "knock2_sent",
        "lan_search_r_received",
        "knock_rr2_received",
        "wrong_uid_rejected",
        "nonce_mismatch_rejected",
        "broadcasts_sent",
        "unicasts_sent",
        "packets_received",
        "responses_rejected",
        "send_errors",
    ):
        value = stats.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return False
    return isinstance(stats.get("deadline_exceeded"), bool)


def resolve_uid(
    command: str,
    uid: str,
    subnet: str,
    timeout_seconds: int,
    broadcast_seconds: int = 2,
    max_unicast_per_second: int = 32,
    cached_ip: str | None = None,
    candidates: tuple[str, ...] = (),
) -> dict:
    args = [
        command,
        "--uid",
        uid,
        "--subnet",
        subnet,
        "--timeout",
        f"{timeout_seconds}s",
        "--broadcast-duration",
        f"{broadcast_seconds}s",
        "--max-unicast-per-second",
        str(max_unicast_per_second),
        "--json",
    ]
    if cached_ip:
        args.extend(("--cached-ip", cached_ip))
    for candidate in candidates:
        args.extend(("--candidate", candidate))
    started = utc_now()
    try:
        completed = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout_seconds + 2,
            check=False,
        )
    except subprocess.TimeoutExpired:
        elapsed_ms = int((utc_now() - started).total_seconds() * 1000)
        return _resolver_failure(
            "helper_timeout", "resolver exceeded its safety timeout", elapsed_ms
        )
    except OSError as err:
        elapsed_ms = int((utc_now() - started).total_seconds() * 1000)
        return _resolver_failure(
            "send_failed", f"resolver could not start ({type(err).__name__})", elapsed_ms
        )
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as err:
        return _resolver_failure(
            "invalid_helper_output",
            f"resolver returned invalid JSON ({type(err).__name__})",
            int((utc_now() - started).total_seconds() * 1000),
        )
    if not _valid_resolver_result(result):
        return _resolver_failure(
            "invalid_helper_output",
            "resolver result has an invalid schema",
            int((utc_now() - started).total_seconds() * 1000),
        )
    if result["resolved"]:
        try:
            ipaddress.ip_address(str(result.get("ip_address", "")))
        except ValueError:
            return _resolver_failure(
                "invalid_helper_output",
                "resolver returned an invalid IP address",
                int((utc_now() - started).total_seconds() * 1000),
            )
        if completed.returncode != 0:
            return _resolver_failure(
                "invalid_helper_output",
                "resolver reported success with a nonzero exit status",
                int((utc_now() - started).total_seconds() * 1000),
            )
    elif completed.returncode == 0:
        result["error_code"] = "invalid_helper_output"
        result["error"] = "resolver reported failure with a zero exit status"
    elif result.get("error_code") not in {
        "not_found",
        "deadline_exceeded",
        "send_failed",
        "invalid_response",
        "invalid_request",
    }:
        return _resolver_failure(
            "invalid_helper_output",
            "resolver failure omitted a recognized error code",
            int((utc_now() - started).total_seconds() * 1000),
        )
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
        self.timeout_seconds = int(self.args.get("ip_resolve_timeout_seconds", 15))
        self.broadcast_seconds = int(
            self.args.get("ip_discovery_broadcast_seconds", 2)
        )
        self.max_unicast_per_second = int(
            self.args.get("ip_discovery_max_unicast_per_second", 32)
        )
        self.refresh_minutes = int(self.args.get("ip_refresh_interval_minutes", 360))
        self.retry_seconds = int(self.args.get("ip_retry_backoff_seconds", 60))
        self.publish_ip = bool(self.args.get("publish_discovery_ip", True))
        self.resolver_command = str(self.args["resolver_command"])
        self.registry = DeviceRegistry(str(self.args["registry_file"]))
        self.reloader = RuntimeReloader(
            str(self.args["renderer_command"]), str(self.args["go2rtc_service"])
        )
        self.logger = PetlibroLogger(
            self.ad, "petlibro.discovery", self.args.get("log_level", "info")
        )
        self.flush_handle = None
        self.config_dirty = False
        self.resolving: set[str] = set()
        self.resolve_futures = set()
        self.publish_unavailable = False
        if self.enabled:
            self.mqtt.listen_event(
                self._mqtt_device_event,
                "MQTT_MESSAGE",
                wildcard="dl/+/+/device/#",
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
        for future in self.resolve_futures:
            future.cancel()
        self.resolve_futures.clear()

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
        if created:
            self.logger.info("device discovered", product=product, serial=serial)
        if uid_changed:
            self.logger.info("camera UID discovered", product=product, serial=serial)
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
                self.logger.error(
                    "configuration update failed", error_type=type(err).__name__
                )
                self.flush_handle = self.ad.run_in(self._flush, self.retry_seconds)
            else:
                self.logger.info(
                    "configuration updated", go2rtc_restarted=restarted
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
        # Give the debounced app-config write time to create the controller;
        # _resolve only submits work and returns immediately.
        self.ad.run_in(self._resolve, 2, device_key=key)

    def _resolve(self, kwargs: dict) -> None:
        key = str(kwargs["device_key"])
        device = self.registry.devices.get(key)
        if device is None:
            self.resolving.discard(key)
            return
        uid = str(device["uid"])
        cached_ip = str(device.get("ip_address") or "") or None
        candidates = tuple(self._known_candidate_ips(key))
        try:
            future = self.ad.submit_to_executor(
                self._resolve_worker,
                key,
                uid,
                cached_ip,
                candidates,
                callback=self._resolve_complete,
            )
        except Exception as err:
            self.resolving.discard(key)
            self.logger.error(
                "resolver submission failed",
                device=key,
                error_type=type(err).__name__,
            )
            self.registry.resolution_failed(device)
            self._schedule_flush(config_dirty=False)
            return
        self.resolve_futures.add(future)
        self.logger.debug(
            "resolver submitted",
            device=key,
            cached=bool(cached_ip),
            candidates=len(candidates),
        )

    def _resolve_worker(
        self,
        key: str,
        uid: str,
        cached_ip: str | None,
        candidates: tuple[str, ...],
    ) -> dict:
        try:
            result = resolve_uid(
                self.resolver_command,
                uid,
                self.lan_cidr,
                self.timeout_seconds,
                self.broadcast_seconds,
                self.max_unicast_per_second,
                cached_ip,
                candidates,
            )
        except Exception as err:
            result = _resolver_failure(
                "invalid_helper_output",
                f"resolver worker failed ({type(err).__name__})",
            )
        return {"device_key": key, "uid": uid, "result": result}

    def _resolve_complete(self, work: dict, **_kwargs) -> None:
        self.resolve_futures = {
            future for future in self.resolve_futures if not future.done()
        }
        key = str(work.get("device_key", ""))
        attempted_uid = str(work.get("uid", ""))
        result = work.get("result")
        device = self.registry.devices.get(key)
        self.resolving.discard(key)
        if device is None or device.get("uid") != attempted_uid:
            self.logger.debug("discarded stale resolver result", device=key)
            return

        old_ip = device.get("ip_address")
        stats = result.get("stats", {}) if isinstance(result, dict) else {}
        resolved = isinstance(result, dict) and result.get("resolved") is True
        if resolved:
            changed = self.registry.resolution_succeeded(
                device, str(result["ip_address"])
            )
            self.logger.info(
                "IP resolved",
                device=key,
                ip_address=result.get("ip_address"),
                method=result.get("method"),
                elapsed_ms=result.get("elapsed_ms"),
            )
        else:
            changed = False
            self.registry.resolution_failed(device)
            error_code = (
                result.get("error_code", "invalid_helper_output")
                if isinstance(result, dict)
                else "invalid_helper_output"
            )
            log_method = (
                self.logger.warning
                if error_code in {"not_found", "deadline_exceeded", "helper_timeout"}
                else self.logger.error
            )
            log_method(
                "IP resolution failed",
                device=key,
                error_code=error_code,
                elapsed_ms=result.get("elapsed_ms") if isinstance(result, dict) else None,
            )

        self.logger.debug(
            "resolver stats",
            device=key,
            method=result.get("method") if isinstance(result, dict) else None,
            elapsed_ms=result.get("elapsed_ms") if isinstance(result, dict) else None,
            lan_search_w3_1_sent=stats.get("lan_search_w3_1_sent"),
            lan_search_w3_2_sent=stats.get("lan_search_w3_2_sent"),
            knock2_sent=stats.get("knock2_sent"),
            lan_search_r_received=stats.get("lan_search_r_received"),
            knock_rr2_received=stats.get("knock_rr2_received"),
            wrong_uid_rejected=stats.get("wrong_uid_rejected"),
            nonce_mismatch_rejected=stats.get("nonce_mismatch_rejected"),
            broadcasts=stats.get("broadcasts_sent"),
            unicasts=stats.get("unicasts_sent"),
            received=stats.get("packets_received"),
            rejected=stats.get("responses_rejected"),
            send_errors=stats.get("send_errors"),
        )
        self._schedule_flush(
            config_dirty=changed
            or (old_ip is None and device.get("ip_address") is not None)
        )

    def _known_candidate_ips(self, current_key: str) -> list[str]:
        candidates = []
        for key, device in self.registry.devices.items():
            if key == current_key:
                continue
            value = device.get("ip_address")
            try:
                parsed = ipaddress.ip_address(str(value))
            except ValueError:
                continue
            if parsed.version == 4 and parsed.compressed not in candidates:
                candidates.append(parsed.compressed)
            if len(candidates) >= 32:
                break
        return candidates

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
                self.logger.warning(
                    "MQTT unavailable; readiness publish deferred"
                )
            self.publish_unavailable = True
            return False
        self.publish_unavailable = False
        return True
