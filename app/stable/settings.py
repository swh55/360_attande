"""Stable Edition settings — tiny JSON-file store (no database).

Stored at config/hub_settings.json (git-ignored so credentials never reach
Git — §28). Thread-safe, atomic writes, masked password read-back.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = BASE_DIR / "config" / "hub_settings.json"
GALLERY_DIR = BASE_DIR / "gallery"

_lock = threading.Lock()

# key: (default, type, label_ar, label_en)
DEFAULTS: dict[str, tuple[Any, type, str, str]] = {
    "camera.host": ("192.168.43.1", str, "عنوان IP للكاميرا", "Camera IP"),
    "camera.http_port": (8888, int, "منفذ HTTP (واجهة الكاميرا)", "HTTP port (camera web)"),
    "camera.ftp_port": (21, int, "منفذ FTP", "FTP port"),
    "camera.telnet_port": (23, int, "منفذ Telnet", "Telnet port"),
    "ftp.username": ("", str, "مستخدم FTP (فارغ = anonymous)", "FTP username (empty = anonymous)"),
    "ftp.password": ("", str, "كلمة مرور FTP", "FTP password"),
    "camera.timeout_s": (5.0, float, "مهلة الاتصال (ثانية)", "Connection timeout (s)"),
    "photo.detect_timeout_s": (30.0, float, "مهلة انتظار الصورة الجديدة (ثانية)", "New-photo wait timeout (s)"),
    "photo.command": ("st key click s2", str, "أمر التصوير عبر Telnet", "Telnet capture command"),
    "photo.settle_ms": (2500, int, "سكون ما بعد الأمر (ملي ثانية)", "Post-command settle (ms)"),
}


def _read_file() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def get(key: str) -> Any:
    default, t, _, _ = DEFAULTS.get(key, ("", str, "", ""))
    with _lock:
        raw = _read_file().get(key, default)
    try:
        return t(raw)
    except (TypeError, ValueError):
        return default


def all_settings() -> dict:
    """Public settings with metadata; password values are never echoed."""
    with _lock:
        stored = _read_file()
    out = {}
    for key, (default, t, ar, en) in DEFAULTS.items():
        val = stored.get(key, default)
        try:
            val = t(val)
        except (TypeError, ValueError):
            val = default
        item = {"key": key, "value": val, "type": t.__name__,
                "label_ar": ar, "label_en": en}
        if "password" in key:
            item["value"] = ""
            item["masked"] = True
        out[key] = item
    return out


def save(values: dict) -> dict:
    """Merge-save settings; empty password means "unchanged" (masked UI)."""
    clean: dict[str, Any] = {}
    for k, v in (values or {}).items():
        if k not in DEFAULTS:
            continue
        default, t, _, _ = DEFAULTS[k]
        if "password" in k and (v is None or v == ""):
            continue
        try:
            clean[k] = t(v)
        except (TypeError, ValueError):
            continue
    with _lock:
        current = _read_file()
        current.update(clean)
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(current, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.replace(tmp, CONFIG_PATH)  # atomic
    return all_settings()


def gallery_dir() -> Path:
    GALLERY_DIR.mkdir(parents=True, exist_ok=True)
    return GALLERY_DIR
