#!/usr/bin/env python3
"""Render runtime configuration without exposing secret option values."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from string import Template
from urllib.parse import urlencode


DEFAULTS = {
    "mqtt_host": "core-mosquitto",
    "mqtt_port": 1883,
    "mqtt_username": "",
    "mqtt_password": "",
    "device_ip": "",
    "product": "PLAF203",
    "serial": "",
    "uid": "",
    "product_secret": "",
    "go2rtc_stream_name": "petlibro_feeder",
    "camera_quality": "hd",
    "ack_mode": "hybrid",
    "send_delay_ctrl": True,
    "hd_probe_wait_ms": 15000,
    "go2rtc_api_port": 1984,
    "go2rtc_rtsp_port": 8554,
    "go2rtc_webrtc_port": 8555,
    "verbose_logs": False,
    "enable_debug_dumps": False,
}

ENV_KEYS = {key: key.upper() for key in DEFAULTS}
BOOL_KEYS = {"send_delay_ctrl", "verbose_logs", "enable_debug_dumps"}
INT_KEYS = {
    "mqtt_port",
    "hd_probe_wait_ms",
    "go2rtc_api_port",
    "go2rtc_rtsp_port",
    "go2rtc_webrtc_port",
}


def parse_bool(value: object, key: str) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{key} must be true or false")


def load_options(data_dir: Path) -> dict[str, object]:
    options_path = data_dir / "options.json"
    if options_path.is_file():
        raw = json.loads(options_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("/data/options.json must contain an object")
    else:
        raw = {
            key: os.environ[env_key]
            for key, env_key in ENV_KEYS.items()
            if env_key in os.environ
        }

    options = DEFAULTS | raw
    for key in BOOL_KEYS:
        options[key] = parse_bool(options[key], key)
    for key in INT_KEYS:
        try:
            options[key] = int(options[key])
        except (TypeError, ValueError) as err:
            raise ValueError(f"{key} must be an integer") from err
    return options


def validate(options: dict[str, object]) -> None:
    if options["product"] != "PLAF203":
        raise ValueError("product must be PLAF203")

    device_ip = str(options["device_ip"])
    try:
        ipaddress.ip_address(device_ip)
    except ValueError as err:
        raise ValueError("device_ip must be a valid IPv4 or IPv6 address") from err

    serial = str(options["serial"])
    if not serial or not re.fullmatch(r"[A-Za-z0-9_-]+", serial):
        raise ValueError("serial must contain only letters, numbers, underscores, or hyphens")

    uid = str(options["uid"])
    if not re.fullmatch(r"[A-Za-z0-9]{20}", uid):
        raise ValueError("uid must contain exactly 20 letters or numbers")

    stream_name = str(options["go2rtc_stream_name"])
    if not re.fullmatch(r"[A-Za-z0-9_-]+", stream_name):
        raise ValueError("go2rtc_stream_name contains unsupported characters")

    if options["camera_quality"] not in {"hd", "sd"}:
        raise ValueError("camera_quality must be hd or sd")
    if options["ack_mode"] not in {"high", "contig", "hybrid"}:
        raise ValueError("ack_mode must be high, contig, or hybrid")
    if not 0 <= int(options["hd_probe_wait_ms"]) <= 60000:
        raise ValueError("hd_probe_wait_ms must be between 0 and 60000")

    for key in ("mqtt_port", "go2rtc_api_port", "go2rtc_rtsp_port", "go2rtc_webrtc_port"):
        if not 1 <= int(options[key]) <= 65535:
            raise ValueError(f"{key} must be between 1 and 65535")


def yaml_string(value: object) -> str:
    return json.dumps(str(value), ensure_ascii=True)


def atomic_write(path: Path, content: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        handle.write(content)
        handle.flush()
        os.fchmod(handle.fileno(), mode)
        temporary = Path(handle.name)
    temporary.replace(path)


def render_template(template_dir: Path, name: str, values: dict[str, str]) -> str:
    source = (template_dir / name).read_text(encoding="utf-8")
    return Template(source).substitute(values)


def render_go2rtc(options: dict[str, object], data_dir: Path, template_dir: Path) -> None:
    query = {
        "uid": str(options["uid"]),
        "quality": str(options["camera_quality"]),
        "ack": str(options["ack_mode"]),
        "send_delay_ctrl": "1" if options["send_delay_ctrl"] else "0",
        "hd_probe_wait_ms": str(options["hd_probe_wait_ms"]),
    }
    if options["verbose_logs"]:
        query["verbose"] = "1"
    if options["enable_debug_dumps"]:
        query["dump_c2d_plain"] = "/data/petlibro_c2d.dat"
        query["dump_d2c_plain"] = "/data/petlibro_d2c.dat"

    stream_url = f"petlibro://{options['device_ip']}?{urlencode(query)}"
    level = "debug" if options["verbose_logs"] else "info"
    values = {
        "LOG_LEVEL": yaml_string("info"),
        "PETLIBRO_LOG_LEVEL": yaml_string(level),
        "API_LISTEN": yaml_string(f":{options['go2rtc_api_port']}"),
        "RTSP_LISTEN": yaml_string(f":{options['go2rtc_rtsp_port']}"),
        "WEBRTC_LISTEN": yaml_string(f":{options['go2rtc_webrtc_port']}"),
        "STREAM_NAME": yaml_string(options["go2rtc_stream_name"]),
        "STREAM_URL": yaml_string(stream_url),
    }
    atomic_write(
        data_dir / "go2rtc.yaml",
        render_template(template_dir, "go2rtc.yaml.tmpl", values),
    )


def render_appdaemon(options: dict[str, object], data_dir: Path, template_dir: Path) -> None:
    common = {
        "MQTT_HOST": yaml_string(options["mqtt_host"]),
        "MQTT_PORT": str(options["mqtt_port"]),
        "SERIAL": yaml_string(options["serial"]),
    }
    atomic_write(
        data_dir / "appdaemon.yaml",
        render_template(template_dir, "appdaemon.yaml.tmpl", common),
    )
    atomic_write(
        data_dir / "apps.yaml",
        render_template(template_dir, "apps.yaml.tmpl", common),
    )
    atomic_write(
        data_dir / "appdaemon-secrets.yaml",
        "mqtt_username: "
        + yaml_string(options["mqtt_username"])
        + "\nmqtt_password: "
        + yaml_string(options["mqtt_password"])
        + "\n",
    )

    app_dir = data_dir / "appdaemon" / "apps"
    app_dir.mkdir(parents=True, exist_ok=True)
    links = {
        app_dir / "apps.yaml": data_dir / "apps.yaml",
        app_dir / "plaf203.py": Path(
            os.environ.get(
                "PETLIBRO_APP_SOURCE", "/opt/petlibro-local/appdaemon/plaf203.py"
            )
        ),
    }
    for link, target in links.items():
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(target)

    marker = data_dir / ".verbose_logs"
    if options["verbose_logs"]:
        atomic_write(marker, "enabled\n")
    elif marker.exists():
        marker.unlink()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("component", choices=("all", "go2rtc", "appdaemon"), nargs="?", default="all")
    args = parser.parse_args()

    data_dir = Path(os.environ.get("PETLIBRO_DATA_DIR", "/data"))
    template_dir = Path(
        os.environ.get("PETLIBRO_TEMPLATE_DIR", "/opt/petlibro-local/templates")
    )
    data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

    try:
        options = load_options(data_dir)
        validate(options)
        # product_secret is accepted for future provisioning work but neither
        # imported backend consumes it. Deliberately do not render or export it.
        _ = options["product_secret"]
        if args.component in {"all", "go2rtc"}:
            render_go2rtc(options, data_dir, template_dir)
        if args.component in {"all", "appdaemon"}:
            render_appdaemon(options, data_dir, template_dir)
    except (OSError, ValueError, json.JSONDecodeError, KeyError) as err:
        print(f"Petlibro Local configuration error: {err}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
