"""MediaManager — SD-card media over FTP + the local downloads gallery.

Flow (§43): camera SD → FTP → local media (gallery/YYYY/MM/) → Gallery.
Video files are listed/downloaded/opened, never interpreted or converted.
No Delete in this edition (§10).
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
from datetime import datetime
from pathlib import Path

from . import settings as settings_svc
from .ftp import FTPClient, FTPError, dcim_entries, file_identity

LOG = logging.getLogger("stable.media")

_SAFE = re.compile(r"[^A-Za-z0-9._\- ]+")


def _cfg() -> dict:
    return {
        "host": settings_svc.get("camera.host"),
        "ftp_port": int(settings_svc.get("camera.ftp_port")),
        "user": str(settings_svc.get("ftp.username") or ""),
        "pass": str(settings_svc.get("ftp.password") or ""),
        "timeout": float(settings_svc.get("camera.timeout_s")),
    }


def _client() -> FTPClient:
    c = _cfg()
    return FTPClient(c["host"], c["ftp_port"], timeout=c["timeout"],
                     username=c["user"], password=c["pass"])


def list_media() -> dict:
    """List DCIM (name/date/size/type) — view only, no interpretation."""
    try:
        with _client() as ftp:
            _root, entries = dcim_entries(ftp)
        files = [e for e in entries if not e.get("is_dir")]
        files.sort(key=lambda e: e["path"], reverse=True)
        return {"ok": True, "count": len(files), "files": files,
                "roots": ["/DCIM"]}
    except (FTPError, OSError) as exc:
        return {"ok": False, "error": str(exc), "count": 0, "files": []}


def _local_path_for(name: str, mtime: str | None) -> Path:
    """gallery/YYYY/MM/<name> (§13) — name collision-safe."""
    safe = _SAFE.sub("_", name).strip() or f"file_{int(time.time())}"
    now = datetime.now()
    if mtime:
        try:
            now = datetime.strptime(mtime, "%Y-%m-%d %H:%M")
        except ValueError:
            pass
    d = settings_svc.gallery_dir() / f"{now.year:04d}" / f"{now.month:02d}"
    d.mkdir(parents=True, exist_ok=True)
    p = d / safe
    i = 1
    while p.exists() and p.stat().st_size > 0:
        p = d / f"{p.stem}_{i}{p.suffix}"
        i += 1
    return p


def download(remote_path: str, size_hint: int = 0) -> dict:
    """Download one file from the SD into the local gallery (§10 Download).

    Returns {id, name, path, size, dir} — `id` is a stable path hash used
    by /api/gallery endpoints.
    """
    remote_path = (remote_path or "").lstrip("/")
    if not remote_path or ".." in remote_path.split("/"):
        return {"ok": False, "error": "مسار غير صالح"}
    name = remote_path.split("/")[-1]
    try:
        with _client() as ftp:
            root = ""
            for r in ("/dcim", "/DCIM"):
                if ftp.cwd(r):
                    root = r
                    break
            if not root:
                raise FTPError("لم يتم العثور على DCIM")
            # find the file's mtime for the gallery date folder
            subdir_parts = remote_path.split("/")
            mtime = ""
            if len(subdir_parts) > 1:
                if not ftp.cwd(subdir_parts[0]):
                    raise FTPError(f"المجلد الفرعي {subdir_parts[0]} غير موجود")
                local_name = subdir_parts[-1]
                match = next((e for e in ftp.list()
                              if e["name"] == local_name), None)
                mtime = match.get("mtime", "") if match else ""
                remote = local_name
            else:
                remote = remote_path
                match = next((e for e in ftp.list()
                              if e["name"] == remote), None)
                mtime = match.get("mtime", "") if match else ""
            local = _local_path_for(name, mtime)
            n = ftp.retr(remote, local)
        if n <= 0 or (size_hint and n != size_hint and n < 1024):
            return {"ok": False, "error": f"حجم التنزيل غير متوقع ({n} بايت)"}
        return {"ok": True, "name": name, "path": str(local),
                "size": n, "id": _hash_path(local)}
    except (FTPError, OSError) as exc:
        return {"ok": False, "error": str(exc)}


def _hash_path(p: Path) -> str:
    return hashlib.sha1(str(p).encode("utf-8")).hexdigest()[:16]


def preview(remote_path: str, size_hint: int = 0) -> dict:
    """Fetch a file into a small tmp cache for in-browser viewing (JPG) or
    local opening. Read-only view convenience (§11: عرض / تنزيل / فتح)."""
    remote_path = (remote_path or "").lstrip("/")
    if not remote_path or ".." in remote_path.split("/"):
        return {"ok": False, "error": "مسار غير صالح"}
    name = remote_path.split("/")[-1]
    safe = _SAFE.sub("_", name)
    tmp = Path(BASE_DIR := settings_svc.BASE_DIR) / "data" / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    local = tmp / safe
    try:
        with _client() as ftp:
            root = ""
            for r in ("/dcim", "/DCIM"):
                if ftp.cwd(r):
                    root = r
                    break
            if not root:
                raise FTPError("لم يتم العثور على DCIM")
            parts = remote_path.split("/")
            if len(parts) > 1 and not ftp.cwd(parts[0]):
                raise FTPError("المجلد الفرعي غير موجود")
            n = ftp.retr(parts[-1], local)
        if n <= 0:
            return {"ok": False, "error": "ملف فارغ"}
        return {"ok": True, "path": str(local), "size": n, "name": name}
    except (FTPError, OSError) as exc:
        return {"ok": False, "error": str(exc)}


def list_gallery() -> list[dict]:
    """Local downloaded gallery (gallery/YYYY/MM/*.{jpg,jpeg,mp4,...})."""
    base = settings_svc.gallery_dir()
    out: list[dict] = []
    for p in sorted(base.rglob("*"), key=lambda x: x.stat().st_mtime if x.is_file() else 0,
                    reverse=True):
        if not p.is_file():
            continue
        low = p.suffix.lower()
        if low not in (".jpg", ".jpeg", ".mp4", ".png"):
            continue
        st = p.stat()
        rel = p.relative_to(base)
        out.append({"id": _hash_path(p), "name": p.name,
                    "rel": str(rel), "size": st.st_size,
                    "mtime": datetime.fromtimestamp(st.st_mtime).strftime(
                        "%Y-%m-%d %H:%M"),
                    "type": "JPG" if low in (".jpg", ".jpeg", ".png") else "MP4"})
    return out


def gallery_find(file_id: str) -> Path | None:
    base = settings_svc.gallery_dir()
    for p in base.rglob("*"):
        if p.is_file() and _hash_path(p) == file_id:
            return p
    return None
