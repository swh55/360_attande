"""Settings service: DB-backed key/value store with typed defaults registry.

Every setting is declared in DEFAULTS with a category, type and metadata so the
API and the Settings UI can render forms generically. Values are stored as JSON
in the `settings` table. Nothing is hard-coded in business logic; consumers call
`settings.get("camera.host")` etc.
"""
from __future__ import annotations

import json
import threading
from typing import Any

from . import database as db
from .logging_setup import get

LOG = get("app")
_lock = threading.RLock()
_cache: dict[str, Any] = {}

# ---- defaults registry ----------------------------------------------------
# meta: t=type(int/float/bool/str/enum), min/max/step for numbers,
#       options for enums, label keys are derived (settings.<key>)
DEFAULTS: dict[str, dict[str, Any]] = {
    # Camera
    "camera.host": {"v": "192.168.43.1", "t": "str", "cat": "camera"},
    "camera.port": {"v": 7679, "t": "int", "min": 1, "max": 65535, "cat": "camera"},
    "camera.stream_path": {"v": "/livestream_high.avi", "t": "str", "cat": "camera"},
    "camera.profile": {"v": "gear360_2017", "t": "str", "cat": "camera"},
    "camera.auto_reconnect": {"v": True, "t": "bool", "cat": "camera"},
    "camera.reconnect_delay": {"v": 1.0, "t": "float", "min": 0.2, "max": 30, "step": 0.1, "cat": "camera"},
    "camera.health_interval": {"v": 5.0, "t": "float", "min": 1, "max": 120, "step": 1, "cat": "camera"},
    # Camera control (gear360_modding services — Take Photo via the camera)
    "camera.control.enabled": {"v": True, "t": "bool", "cat": "camera"},
    "camera.control.telnet_port": {"v": 23, "t": "int", "min": 1, "max": 65535, "cat": "camera"},
    "camera.control.ftp_port": {"v": 21, "t": "int", "min": 1, "max": 65535, "cat": "camera"},
    "camera.control.command": {"v": "st key click s2", "t": "str", "cat": "camera"},
    "camera.control.settle_ms": {"v": 2500, "t": "int", "min": 500, "max": 15000, "step": 250, "cat": "camera"},
    # Streaming
    "stream.request_timeout": {"v": 8.0, "t": "float", "min": 1, "max": 60, "step": 1, "cat": "streaming"},
    "stream.buffer_mb": {"v": 64, "t": "int", "min": 8, "max": 512, "step": 8, "cat": "streaming"},
    "stream.mjpeg_fps": {"v": 20, "t": "int", "min": 5, "max": 30, "step": 1, "cat": "streaming"},
    "stream.mjpeg_quality": {"v": 80, "t": "int", "min": 40, "max": 100, "step": 5, "cat": "streaming"},
    "stream.ws_rate_hz": {"v": 2.0, "t": "float", "min": 0.5, "max": 10, "step": 0.5, "cat": "streaming"},
    # Recording
    "recording.fps_cap": {"v": 30, "t": "int", "min": 5, "max": 60, "step": 1, "cat": "recording"},
    "recording.crf": {"v": 23, "t": "int", "min": 16, "max": 34, "step": 1, "cat": "recording"},
    "recording.auto_record": {"v": False, "t": "bool", "cat": "recording"},
    "recording.event_clip_sec": {"v": 10, "t": "int", "min": 5, "max": 120, "step": 5, "cat": "recording"},
    "recording.idle_stop_sec": {"v": 6.0, "t": "float", "min": 2, "max": 60, "step": 1, "cat": "recording"},
    # AI
    "ai.enabled": {"v": False, "t": "bool", "cat": "ai"},
    "ai.mode": {"v": "person-and-face", "t": "enum", "options": ["motion", "person", "person-and-face"], "cat": "ai"},
    "ai.sample_fps": {"v": 3.0, "t": "float", "min": 0.5, "max": 15, "step": 0.5, "cat": "ai"},
    "ai.confidence": {"v": 0.5, "t": "float", "min": 0.1, "max": 0.95, "step": 0.05, "cat": "ai"},
    "ai.motion.enabled": {"v": True, "t": "bool", "cat": "ai"},
    "ai.motion.sensitivity": {"v": 25, "t": "int", "min": 5, "max": 90, "step": 5, "cat": "ai"},
    "ai.motion.min_area": {"v": 900, "t": "int", "min": 100, "max": 50000, "step": 100, "cat": "ai"},
    "ai.motion.cooldown": {"v": 8.0, "t": "float", "min": 1, "max": 300, "step": 1, "cat": "ai"},
    "ai.person.enabled": {"v": True, "t": "bool", "cat": "ai"},
    "ai.face.enabled": {"v": True, "t": "bool", "cat": "ai"},
    "ai.recognition.enabled": {"v": False, "t": "bool", "cat": "ai"},
    "ai.recognition.threshold": {"v": 0.5, "t": "float", "min": 0.3, "max": 0.7, "step": 0.01, "cat": "ai"},
    "ai.recognition.cooldown": {"v": 60.0, "t": "float", "min": 10, "max": 600, "step": 10, "cat": "ai"},
    "ai.director.enabled": {"v": True, "t": "bool", "cat": "ai"},
    "ai.attendance.enabled": {"v": False, "t": "bool", "cat": "ai"},
    "ai.attendance.cooldown_min": {"v": 5, "t": "int", "min": 1, "max": 120, "step": 1, "cat": "ai"},
    # Storage
    "storage.max_age_days": {"v": 30, "t": "int", "min": 1, "max": 3650, "step": 1, "cat": "storage"},
    "storage.prune_enabled": {"v": False, "t": "bool", "cat": "storage"},
    # FFmpeg
    "ffmpeg.mode": {"v": "auto", "t": "enum", "options": ["auto", "bundled", "custom", "path"], "cat": "ffmpeg"},
    "ffmpeg.custom_path": {"v": "", "t": "str", "cat": "ffmpeg"},
    # Network / security
    "network.lan_access": {"v": False, "t": "bool", "cat": "network"},
    # Demo
    "demo.enabled": {"v": False, "t": "bool", "cat": "system"},
    "demo.speed": {"v": 1.0, "t": "float", "min": 0.2, "max": 3, "step": 0.1, "cat": "system"},
    # Lens calibration (dual-fisheye -> equirect)
    "cal.fisheye_fov_deg": {"v": 195.0, "t": "float", "min": 150, "max": 230, "step": 1, "cat": "camera"},
    "cal.radius_factor": {"v": 1.0, "t": "float", "min": 0.7, "max": 1.4, "step": 0.01, "cat": "camera"},
    "cal.center_offset_x": {"v": 0.0, "t": "float", "min": -0.1, "max": 0.1, "step": 0.005, "cat": "camera"},
    "cal.lens0_azimuth_deg": {"v": 0.0, "t": "float", "min": -180, "max": 180, "step": 5, "cat": "camera"},
    "cal.swap_lenses": {"v": False, "t": "bool", "cat": "camera"},
    "cal.roll_deg": {"v": 0.0, "t": "float", "min": -30, "max": 30, "step": 1, "cat": "camera"},
    # UI
    "ui.language": {"v": "ar", "t": "enum", "options": ["ar", "en"], "cat": "system"},
}

# Live-applied callbacks registered by the hub: key prefix -> callback(values)
_change_listeners: list = []


def on_change(cb) -> None:
    _change_listeners.append(cb)


def load() -> None:
    with _lock:
        _cache.clear()
        for row in db.query("SELECT key, value FROM settings"):
            try:
                _cache[row["key"]] = json.loads(row["value"])
            except (json.JSONDecodeError, TypeError):
                LOG.warning("Corrupt setting %r ignored", row["key"])
    LOG.info("Settings loaded: %d overrides (defaults: %d keys)", len(_cache), len(DEFAULTS))


def get(key: str, default: Any = None) -> Any:
    with _lock:
        if key in _cache:
            return _cache[key]
    meta = DEFAULTS.get(key)
    if meta is not None:
        return meta["v"]
    return default


def get_section(prefix: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in DEFAULTS:
        if key.startswith(prefix):
            out[key] = get(key)
    return out


def set_value(key: str, value: Any) -> None:
    meta = DEFAULTS.get(key)
    if meta is None:
        raise KeyError(f"unknown setting {key!r}")
    value = _validate(key, meta, value)
    with _lock:
        _cache[key] = value
    db.execute(
        "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, datetime('now','localtime')) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (key, json.dumps(value)),
    )
    for cb in _change_listeners:
        try:
            cb(key, value)
        except Exception:  # pragma: no cover
            LOG.exception("settings change listener failed")


def set_many(values: dict[str, Any]) -> dict[str, str]:
    """Set multiple; returns map of key->error for failed keys."""
    errors: dict[str, str] = {}
    for key, value in values.items():
        try:
            set_value(key, value)
        except (KeyError, ValueError, TypeError) as exc:
            errors[key] = str(exc)
    return errors


def reset_key(key: str) -> None:
    with _lock:
        _cache.pop(key, None)
    db.execute("DELETE FROM settings WHERE key=?", (key,))


def export_all() -> dict[str, dict[str, Any]]:
    """Full metadata view used by the Settings UI."""
    out: dict[str, dict[str, Any]] = {}
    for key, meta in DEFAULTS.items():
        out[key] = {
            "value": get(key),
            "default": meta["v"],
            "type": meta["t"],
            "category": meta["cat"],
            "min": meta.get("min"),
            "max": meta.get("max"),
            "step": meta.get("step"),
            "options": meta.get("options"),
        }
    return out


def _validate(key: str, meta: dict[str, Any], value: Any) -> Any:
    t = meta["t"]
    if t == "bool":
        if not isinstance(value, bool):
            raise ValueError("expected boolean")
        return value
    if t == "int":
        v = int(value)
        if "min" in meta:
            v = max(meta["min"], v)
        if "max" in meta:
            v = min(meta["max"], v)
        return v
    if t == "float":
        v = float(value)
        if "min" in meta:
            v = max(meta["min"], v)
        if "max" in meta:
            v = min(meta["max"], v)
        return v
    if t == "enum":
        v = str(value)
        if v not in meta.get("options", []):
            raise ValueError(f"must be one of {meta.get('options')}")
        return v
    return str(value)
