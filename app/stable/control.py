"""CameraController — stream-independent control layer (§7).

Capabilities (exactly these, nothing more):
    get_status()   per-service reachability (HTTP / FTP / Telnet)
    take_photo()   the documented mod capture command over Telnet —
                   sends the command ONLY; verification happens in
                   CameraService via FTP file detection (§24)
    test_http() / test_ftp() / test_telnet()

Never provides: stream(), decode(), snapshot(). Never touches 7679.
"""
from __future__ import annotations

import logging
import socket
import time

from . import http as http_mod
from . import settings as settings_svc
from .ftp import FTPClient, FTPError
from .telnet import TelnetError, TelnetSession

LOG = logging.getLogger("stable.control")


def _cfg() -> dict:
    return {
        "host": settings_svc.get("camera.host"),
        "http_port": int(settings_svc.get("camera.http_port")),
        "ftp_port": int(settings_svc.get("camera.ftp_port")),
        "telnet_port": int(settings_svc.get("camera.telnet_port")),
        "ftp_user": str(settings_svc.get("ftp.username") or ""),
        "ftp_pass": str(settings_svc.get("ftp.password") or ""),
        "timeout": float(settings_svc.get("camera.timeout_s")),
        "command": str(settings_svc.get("photo.command")),
    }


# ------------------------------------------------------------------ probes
def test_tcp(host: str, port: int, timeout: float) -> dict:
    t0 = time.time()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return {"ok": True, "latency_ms": round((time.time() - t0) * 1000, 1)}
    except OSError as exc:
        return {"ok": False,
                "error": f"{exc.__class__.__name__}: {exc}"}


def test_http() -> dict:
    c = _cfg()
    return http_mod.health(c["host"], c["http_port"], timeout=c["timeout"])


def test_ftp() -> dict:
    c = _cfg()
    t0 = time.time()
    try:
        with FTPClient(c["host"], c["ftp_port"], timeout=c["timeout"],
                       username=c["ftp_user"], password=c["ftp_pass"]) as ftp:
            ftp.list() if ftp.cwd("/") else ftp.list()
        return {"ok": True, "latency_ms": round((time.time() - t0) * 1000, 1),
                "port": c["ftp_port"]}
    except (FTPError, OSError) as exc:
        return {"ok": False, "port": c["ftp_port"], "error": str(exc)}


def test_telnet() -> dict:
    c = _cfg()
    t0 = time.time()
    session = TelnetSession(c["host"], c["telnet_port"], timeout=c["timeout"])
    try:
        session.login()
        return {"ok": True, "latency_ms": round((time.time() - t0) * 1000, 1),
                "port": c["telnet_port"]}
    except TelnetError as exc:
        return {"ok": False, "port": c["telnet_port"], "error": str(exc)}
    finally:
        session.close()


def send_capture_command() -> dict:
    """Telnet `st key click s2` (or the configured command). NOT a success
    signal by itself — the caller must verify a new file over FTP (§24)."""
    c = _cfg()
    session = TelnetSession(c["host"], c["telnet_port"], timeout=c["timeout"])
    try:
        session.login()
        echo = session.command(c["command"], wait_after=1.2)
        LOG.info("capture command sent: %r", c["command"])
        return {"ok": True, "command": c["command"], "echo": echo[-300:]}
    except TelnetError as exc:
        return {"ok": False, "command": c["command"], "error": str(exc)}
    finally:
        session.close()


# ------------------------------------------------------------------ status
def get_status() -> dict:
    """Full per-service status + the OFFLINE/REACHABLE/CONTROL_READY state.

    State machine (§5):
        OFFLINE        no TCP service answers
        REACHABLE      TCP answers but HTTP 8888 does not
        CONTROL_READY  HTTP 8888 answers (the control plane is usable);
                       FTP/Telnet remain independent flags
    """
    c = _cfg()
    http_r = test_http()
    tcp_http = test_tcp(c["host"], c["http_port"], c["timeout"])
    ftp_r = test_ftp() if tcp_http["ok"] else {"ok": False, "skipped": True}
    telnet_r = (test_telnet() if tcp_http["ok"]
                else {"ok": False, "skipped": True})

    any_tcp = any(r.get("ok") for r in (tcp_http, ftp_r, telnet_r))
    if http_r.get("ok"):
        state = "CONTROL_READY"
    elif any_tcp:
        state = "REACHABLE"
    else:
        state = "OFFLINE"

    return {
        "state": state,
        "host": c["host"],
        "camera_web_url": http_mod.open_url(c["host"], c["http_port"]),
        "services": {
            "http": {**http_r, "port": c["http_port"],
                     "label": "HTTP (واجهة الكاميرا)"},
            "ftp": {**ftp_r, "port": c["ftp_port"], "label": "FTP"},
            "telnet": {**telnet_r, "port": c["telnet_port"], "label": "Telnet"},
        },
        "capture_ready": bool(ftp_r.get("ok") and telnet_r.get("ok")),
    }


def retry(fn, attempts: int = 2, backoff: float = 0.8):
    """Retry ≤2 with backoff (§41). Returns the last result dict."""
    result = {}
    for i in range(max(1, attempts)):
        result = fn()
        if result.get("ok"):
            return result
        if i < attempts - 1:
            time.sleep(backoff * (i + 1))
    return result
