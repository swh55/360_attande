"""CameraService — the Stable Edition facade (§22).

Provides exactly: status(), test(), open_web(), take_photo(), list_media(),
download_media(). Provides NEVER: stream(), decode(), snapshot().

Take Photo is verified end-to-end (§8/§12/§24/§44):
    1. baseline identity of the SD (path → size+mtime)
    2. telnet capture command            → SENDING_COMMAND
    3. settle wait, then FTP re-scan     → WAITING_CAMERA
    4. new (path, size, mtime) found     → PHOTO_DETECTED
    5. FTP download into the gallery     → PHOTO_READY
    any failure                          → FAILED (+ a clear Arabic reason)

Success is NEVER claimed from the command echo alone.
"""
from __future__ import annotations

import logging
import time

from . import control, media as media_mod, settings as settings_svc
from .ftp import FTPClient, FTPError, dcim_entries, file_identity

LOG = logging.getLogger("stable.camera")

# photo workflow states (surfaced verbatim to the UI, §8)
ST_SENDING = "SENDING_COMMAND"
ST_WAITING = "WAITING_CAMERA"
ST_DETECTED = "PHOTO_DETECTED"
ST_READY = "PHOTO_READY"
ST_FAILED = "FAILED"


def status() -> dict:
    return control.get_status()


def test() -> dict:
    """Test Camera — TCP/HTTP/FTP/Telnet only. NEVER touches 7679 (§4)."""
    c = {
        "host": settings_svc.get("camera.host"),
        "timeout": float(settings_svc.get("camera.timeout_s")),
    }
    tcp = {}
    for name, port in (("http", int(settings_svc.get("camera.http_port"))),
                       ("ftp", int(settings_svc.get("camera.ftp_port"))),
                       ("telnet", int(settings_svc.get("camera.telnet_port")))):
        tcp[name] = control.test_tcp(c["host"], port, c["timeout"])
    http_r = control.retry(control.test_http, attempts=2, backoff=0.8)
    ftp_r = control.retry(control.test_ftp, attempts=2, backoff=0.8)
    telnet_r = control.retry(control.test_telnet, attempts=2, backoff=0.8)
    return {
        "ok": bool(http_r.get("ok")),
        "host": c["host"],
        "tcp": tcp,
        "http": http_r,
        "ftp": ftp_r,
        "telnet": telnet_r,
        "note": "هذا الاختبار لا يلمس منفذ البث 7679 إطلاقاً",
    }


def open_web() -> dict:
    st = control.get_status()
    return {"url": st["camera_web_url"], "state": st["state"]}


# ------------------------------------------------------------------ photo
def _baseline_entries() -> tuple[dict[str, tuple[int, str]] | None, str]:
    """Best-effort SD identity snapshot before the capture."""
    c = {
        "host": settings_svc.get("camera.host"),
        "ftp_port": int(settings_svc.get("camera.ftp_port")),
        "user": str(settings_svc.get("ftp.username") or ""),
        "pass": str(settings_svc.get("ftp.password") or ""),
        "timeout": float(settings_svc.get("camera.timeout_s")),
    }
    try:
        with FTPClient(c["host"], c["ftp_port"], timeout=c["timeout"],
                       username=c["user"], password=c["pass"]) as ftp:
            _root, entries = dcim_entries(ftp)
        return file_identity(entries), ""
    except (FTPError, OSError) as exc:
        LOG.warning("baseline SD scan failed: %s", exc)
        return None, str(exc)


def _rescan_until_new(baseline: dict | None, detect_timeout: float) -> dict:
    """Poll the SD until a file identity appears that is not in baseline."""
    deadline = time.time() + detect_timeout
    last_err = ""
    while time.time() < deadline:
        ident, err = _baseline_entries()
        last_err = err
        if ident:
            for path, (size, mtime) in ident.items():
                if baseline is None:
                    return {"path": path, "size": size, "mtime": mtime}
                old = baseline.get(path)
                if old is None or old != (size, mtime):
                    return {"path": path, "size": size, "mtime": mtime}
        time.sleep(2.0)          # calm polling: one FTP pass / 2 s, no hammering
    return {"error": last_err or "انتهت مهلة الانتظار"}


def take_photo() -> dict:
    """Full verified capture → PHOTO_READY or FAILED (+ workflow log)."""
    workflow: list[dict] = []
    detect_timeout = float(settings_svc.get("photo.detect_timeout_s"))

    def mark(state: str, **kw):
        workflow.append({"state": state, "at_s": round(
            time.time() - t0, 1), **kw})

    t0 = time.time()

    # 0) the camera must be control-ready
    st = control.get_status()
    if st["state"] == "OFFLINE":
        mark(ST_FAILED, reason="الكاميرا غير متصلة بالشبكة.")
        return {"state": ST_FAILED, "workflow": workflow}
    if not (st["services"]["telnet"].get("ok") and st["services"]["ftp"].get("ok")):
        mark(ST_FAILED, reason="خادم التحكم في الكاميرا غير متاح "
             "(Telnet/FTP غير جاهزين).")
        return {"state": ST_FAILED, "workflow": workflow}

    # 1) baseline identity (before the shutter)
    baseline, baseline_err = _baseline_entries()
    mark(ST_SENDING, baseline_files=len(baseline or {}),
         baseline_error=baseline_err)

    # 2) send the capture command (NOT success by itself, §24)
    sent = control.send_capture_command()
    if not sent.get("ok"):
        mark(ST_FAILED, reason="تعذر تنفيذ أمر التصوير: "
             + str(sent.get("error", "")))
        return {"state": ST_FAILED, "workflow": workflow, "command": sent}

    # 3) settle, then poll for the new file
    settle = max(0.5, int(settings_svc.get("photo.settle_ms")) / 1000.0)
    time.sleep(settle)
    mark(ST_WAITING, settle_s=settle, detect_timeout_s=detect_timeout)
    found = _rescan_until_new(baseline, detect_timeout)
    if "error" in found and "path" not in found:
        mark(ST_FAILED,
             reason="تم تنفيذ الأمر ولكن لم يتم العثور على صورة جديدة. "
                    "قد تكون الكاميرا في وضع لا يستجيب للزر (مثل المعاينة) "
                    "أو فشلت كتابة الملف على البطاقة.")
        return {"state": ST_FAILED, "workflow": workflow, "scan": found}
    mark(ST_DETECTED, path=found["path"], size=found["size"])

    # 4) download into the local gallery
    dl = media_mod.download(found["path"], size_hint=int(found["size"]))
    if not dl.get("ok"):
        mark(ST_FAILED, reason="تعذر تنزيل الصورة: " + str(dl.get("error", "")))
        return {"state": ST_FAILED, "workflow": workflow}
    mark(ST_READY, name=dl["name"], local=dl["path"], size=dl["size"])

    LOG.info("Take Photo OK: %s (%s bytes) in %.1fs",
             dl["name"], dl["size"], time.time() - t0)
    return {"state": ST_READY, "filename": dl["name"], "id": dl["id"],
            "path": dl["path"], "size": dl["size"],
            "sd_path": found["path"], "workflow": workflow}


# ------------------------------------------------------------------ media
def list_media() -> dict:
    return media_mod.list_media()


def download_media(remote_path: str, size_hint: int = 0) -> dict:
    return media_mod.download(remote_path, size_hint)


def list_gallery() -> dict:
    return {"ok": True, "items": media_mod.list_gallery()}
