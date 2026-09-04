"""Stable Edition REST API (§17) — plain REST, no WebSocket.

    GET  /api/status                  state + per-service flags
    GET  /api/camera/services         per-service details
    POST /api/camera/test             TCP + HTTP + FTP + Telnet test (no 7679)
    GET  /api/camera/web              camera-web URL (§42)
    POST /api/camera/photo            verified Take Photo workflow
    GET  /api/media                   SD (DCIM) listing
    POST /api/media/download          download an SD file into the gallery
    GET  /api/gallery                 local downloaded gallery
    GET  /api/gallery/file?id=...     serve one local gallery file
    GET/PUT /api/settings             settings (password masked)
    GET  /api/about                   Stable Edition info
"""
from __future__ import annotations

import logging
import mimetypes
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from . import camera, control, settings as settings_svc

LOG = logging.getLogger("stable.api")
router = APIRouter(prefix="/api")


@router.get("/status")
def api_status():
    return camera.status()


@router.get("/camera/services")
def api_services():
    return control.get_status()["services"]


@router.post("/camera/test")
def api_test():
    return camera.test()


@router.get("/camera/web")
def api_camera_web():
    return camera.open_web()


@router.post("/camera/photo")
def api_take_photo():
    """Verified capture. 200 + PHOTO_READY, or 200 + FAILED with a clear
    Arabic reason — never a fake success, never a raw traceback."""
    out = camera.take_photo()
    return out


@router.get("/media")
def api_media():
    out = camera.list_media()
    if not out.get("ok"):
        raise HTTPException(502, out.get("error", "تعذر قراءة بطاقة الذاكرة"))
    return out


@router.post("/media/download")
def api_media_download(body: dict):
    out = camera.download_media(str(body.get("path", "")),
                                int(body.get("size", 0) or 0))
    if not out.get("ok"):
        raise HTTPException(502, out.get("error", "تعذر تنزيل الملف"))
    return out


@router.get("/media/preview")
def api_media_preview(path: str):
    """View-only proxy: fetches the SD file into a tmp cache and serves it
    (JPG lightbox / MP4 open). Never interprets video content."""
    from .media import preview
    out = preview(path)
    if not out.get("ok"):
        raise HTTPException(502, out.get("error", "تعذر جلب الملف"))
    p = Path(out["path"])
    mt = mimetypes.guess_type(str(p))[0] or "application/octet-stream"
    return FileResponse(str(p), media_type=mt)


@router.get("/gallery")
def api_gallery():
    return camera.list_gallery()


@router.get("/gallery/file")
def api_gallery_file(id: str):
    p = settings_svc.gallery_dir() if False else None  # placeholder guard
    from .media import gallery_find
    path = gallery_find(id)
    if path is None or not path.is_file():
        raise HTTPException(404, "الملف غير موجود في المعرض المحلي")
    mt = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    return FileResponse(str(path), media_type=mt)


@router.get("/settings")
def api_settings_get():
    return {"settings": list(settings_svc.all_settings().values())}


@router.put("/settings")
def api_settings_put(body: dict):
    values = body.get("settings") if isinstance(body.get("settings"), list) else None
    flat = {}
    if values:
        for item in values:
            if isinstance(item, dict) and "key" in item:
                flat[item["key"]] = item.get("value")
    flat.update({k: v for k, v in (body or {}).items() if k != "settings"})
    return {"settings": list(settings_svc.save(flat).values())}


@router.get("/about")
def api_about():
    return {
        "name": "Gear360 Hub",
        "edition": "Stable Edition",
        "camera": "Samsung Gear 360 2017 (SM-R210)",
        "mode": "CONTROL_ONLY",
        "live_preview": "غير مستخدم في هذا الإصدار",
        "core": "Camera Control + Media",
        "plane": {"http": 8888, "ftp": 21, "telnet": 23},
        "disabled": ["Live Preview (7679)", "360 Viewer", "AI", "FFmpeg",
                     "Recording from stream", "Snapshot from stream"],
    }
