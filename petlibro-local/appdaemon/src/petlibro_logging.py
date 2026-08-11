"""Small level-aware, privacy-safe logger for Petlibro AppDaemon apps."""

from __future__ import annotations

import json
from collections.abc import Mapping


LOG_LEVELS = ("critical", "error", "warning", "info", "debug", "trace")
_PRIORITY = {name: index for index, name in enumerate(LOG_LEVELS)}
_SECRET_MARKERS = (
    "password",
    "secret",
    "credential",
    "token",
    "cameraauthinfo",
    "wifi_ssid",
    "ssid",
)


def normalize_log_level(value: object, *, legacy_verbose: bool = False) -> str:
    if value is None or str(value).strip() == "":
        return "debug" if legacy_verbose else "info"
    level = str(value).strip().lower()
    if level not in _PRIORITY:
        raise ValueError("log_level must be one of " + ", ".join(LOG_LEVELS))
    return level


def redact(value: object, key: str = "") -> object:
    normalized_key = key.replace("_", "").lower()
    if any(marker.replace("_", "") in normalized_key for marker in _SECRET_MARKERS):
        return "<redacted>"
    if isinstance(value, Mapping):
        return {str(item_key): redact(item, str(item_key)) for item_key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    return value


class PetlibroLogger:
    """Filter Petlibro messages without enabling AppDaemon's global DEBUG flood."""

    def __init__(self, ad, component: str, level: object = "info"):
        self.ad = ad
        self.component = component
        self.level = normalize_log_level(level)

    def enabled(self, level: str) -> bool:
        return _PRIORITY[level] <= _PRIORITY[self.level]

    def log(self, level: str, message: str, **fields: object) -> None:
        level = normalize_log_level(level)
        if not self.enabled(level):
            return
        safe_fields = redact(fields)
        suffix = "".join(
            f" {key}={self._render(value)}"
            for key, value in safe_fields.items()
            if value is not None
        )
        rendered = f"[{level.upper()}] [{self.component}]: {message}{suffix}"
        # Keep AppDaemon itself at INFO so its scheduler/state internals do not
        # flood the add-on log. PetlibroLogger performs application filtering.
        appdaemon_level = {
            "critical": "CRITICAL",
            "error": "ERROR",
            "warning": "WARNING",
        }.get(level, "INFO")
        try:
            self.ad.log(rendered, level=appdaemon_level)
        except TypeError:
            # Test doubles and older AppDaemon releases may only accept text.
            self.ad.log(rendered)

    def critical(self, message: str, **fields: object) -> None:
        self.log("critical", message, **fields)

    def error(self, message: str, **fields: object) -> None:
        self.log("error", message, **fields)

    def warning(self, message: str, **fields: object) -> None:
        self.log("warning", message, **fields)

    def info(self, message: str, **fields: object) -> None:
        self.log("info", message, **fields)

    def debug(self, message: str, **fields: object) -> None:
        self.log("debug", message, **fields)

    def trace(self, message: str, **fields: object) -> None:
        self.log("trace", message, **fields)

    @staticmethod
    def _render(value: object) -> str:
        if isinstance(value, (dict, list)):
            return json.dumps(value, sort_keys=True, separators=(",", ":"))
        return str(value).replace("\n", "\\n")
