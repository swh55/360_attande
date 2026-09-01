"""Events, People, AI, Settings, System API + WebSocket endpoint."""
from __future__ import annotations

import asyncio
import shutil
from datetime import datetime
from pathlib import Path

import cv2
from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, WebSocket, WebSocketDisconnect

from ..ai.recognition import RecognitionEngine
from ..core import database as db
from ..core import settings as settings_svc
from ..hub import get_hub
from ..storage import library

router = APIRouter(prefix="/api")


# ----------------------------------------------------------------- events
@router.get("/events")
def events(limit: int = 100, offset: int = 0, type: str | None = None,
           from_ts: str | None = None, to_ts: str | None = None):
    return {"events": get_hub().store.list_events(limit, offset, type, from_ts, to_ts)}


@router.get("/events/{event_id}/snapshot")
def event_snapshot(event_id: int):
    from fastapi.responses import FileResponse
    row = db.query_one("SELECT snapshot_path FROM events WHERE id=?", (event_id,))
    if not row or not row["snapshot_path"]:
        raise HTTPException(404, "no snapshot")
    path = Path(row["snapshot_path"])
    if not path.exists():
        raise HTTPException(404, "file missing")
    return FileResponse(str(path), media_type="image/jpeg")


# ----------------------------------------------------------------- virtual cameras
@router.get("/vcameras")
def vcameras_list():
    return {"virtual_cameras": db.query("SELECT * FROM virtual_cameras ORDER BY id")}


@router.post("/vcameras")
def vcameras_create(values: dict):
    vid = db.execute(
        "INSERT INTO virtual_cameras (name, mode, yaw, pitch, fov, created_at) "
        "VALUES (?, ?, ?, ?, ?, datetime('now','localtime'))",
        (str(values.get("name", "VC"))[:60], str(values.get("mode", "perspective")),
         float(values.get("yaw", 0)), float(values.get("pitch", 0)),
         float(values.get("fov", 90))),
    )
    return db.query_one("SELECT * FROM virtual_cameras WHERE id=?", (vid,))


@router.delete("/vcameras/{vcam_id}")
def vcameras_delete(vcam_id: int):
    db.execute("DELETE FROM virtual_cameras WHERE id=?", (vcam_id,))
    return {"deleted": True}


@router.get("/events/stats")
def events_stats(hours: int = 24):
    return get_hub().store.stats(hours)


@router.get("/events/today")
def events_today():
    return get_hub().store.today_counts()


@router.get("/events/timeline")
def events_timeline(days: int = 1):
    return {"timeline": get_hub().store.timeline(days)}


@router.get("/attendance/summary")
def attendance_summary(day: str | None = None):
    return {"day": day or datetime.now().strftime("%Y-%m-%d"),
            "rows": get_hub().ai.attendance.summary(day) if get_hub().ai else []}


# ----------------------------------------------------------------- people
@router.get("/people")
def people_list():
    rows = db.query(
        """SELECT p.*, (SELECT COUNT(*) FROM face_embeddings e WHERE e.person_id=p.id) AS embeddings,
                  (SELECT COUNT(*) FROM events ev WHERE ev.person_id=p.id AND ev.type='recognition_known') AS recognitions
           FROM people p ORDER BY p.name""")
    return {"people": rows}


@router.post("/people")
async def people_create(
    name: str = Form(..., min_length=1),
    person_code: str = Form(""),
    department: str = Form(""),
    notes: str = Form(""),
    photo: UploadFile | None = File(None),
):
    hub = get_hub()
    pid = db.execute(
        "INSERT INTO people (name, person_code, department, notes, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, datetime('now','localtime'), datetime('now','localtime'))",
        (name, person_code or None, department or None, notes or None),
    )
    photo_path = None
    if photo is not None:
        data = await photo.read()
        if data:
            photo_path = _save_person_photo(pid, data)
            db.execute("UPDATE people SET photo_path=? WHERE id=?", (photo_path, pid))
            if RecognitionEngine.available() and photo_path:
                img = cv2.imread(photo_path)
                if img is not None:
                    engine = RecognitionEngine()
                    engine.enroll(pid, img, source="upload")
    db.execute(
        "INSERT INTO system_logs (ts, level, domain, message) "
        "VALUES (datetime('now','localtime'), 'info', 'app', ?)",
        (f"Person added: {name}",))
    return {"id": pid, "photo_path": photo_path,
            "recognition_available": RecognitionEngine.available()}


@router.get("/people/{person_id}/photo")
def people_photo_get(person_id: int):
    from fastapi.responses import FileResponse
    row = db.query_one("SELECT photo_path FROM people WHERE id=?", (person_id,))
    if not row or not row["photo_path"]:
        raise HTTPException(404, "no photo")
    path = Path(row["photo_path"])
    if not path.exists():
        raise HTTPException(404, "file missing")
    return FileResponse(str(path), media_type="image/jpeg")


@router.post("/people/{person_id}/photo")
async def people_photo(person_id: int, photo: UploadFile = File(...)):
    if not db.query_one("SELECT id FROM people WHERE id=?", (person_id,)):
        raise HTTPException(404, "person not found")
    data = await photo.read()
    if not data:
        raise HTTPException(400, "empty upload")
    path = _save_person_photo(person_id, data)
    db.execute("UPDATE people SET photo_path=?, updated_at=datetime('now','localtime') WHERE id=?",
               (path, person_id))
    ok = False
    if RecognitionEngine.available():
        img = cv2.imread(path)
        if img is not None:
            ok = RecognitionEngine().enroll(person_id, img, source="upload")
    return {"photo_path": path, "embedding_created": ok,
            "recognition_available": RecognitionEngine.available()}


@router.put("/people/{person_id}")
def people_update(person_id: int, name: str | None = None, person_code: str | None = None,
                  department: str | None = None, notes: str | None = None,
                  active: bool | None = None):
    row = db.query_one("SELECT * FROM people WHERE id=?", (person_id,))
    if not row:
        raise HTTPException(404, "person not found")
    fields, params = [], []
    for col, val in (("name", name), ("person_code", person_code),
                     ("department", department), ("notes", notes)):
        if val is not None:
            fields.append(f"{col}=?")
            params.append(val)
    if active is not None:
        fields.append("active=?")
        params.append(1 if active else 0)
    if fields:
        fields.append("updated_at=datetime('now','localtime')")
        params.append(person_id)
        db.execute(f"UPDATE people SET {', '.join(fields)} WHERE id=?", params)
    return db.query_one("SELECT * FROM people WHERE id=?", (person_id,))


@router.delete("/people/{person_id}")
def people_delete(person_id: int):
    row = db.query_one("SELECT photo_path FROM people WHERE id=?", (person_id,))
    if not row:
        raise HTTPException(404, "person not found")
    if row["photo_path"]:
        try:
            Path(row["photo_path"]).unlink(missing_ok=True)
        except OSError:
            pass
    db.execute("DELETE FROM people WHERE id=?", (person_id,))
    return {"deleted": True}


def _save_person_photo(person_id: int, data: bytes) -> str:
    import numpy as np
    from ..core.paths import DIRS
    d = DIRS["data"] / "people"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"person_{person_id}_{int(datetime.now().timestamp())}.jpg"
    img = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise HTTPException(400, "invalid image")
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 90])
    if not ok:
        raise HTTPException(500, "encode failed")
    buf.tofile(path)
    return str(path)


# ----------------------------------------------------------------- AI
@router.get("/ai/status")
def ai_status():
    hub = get_hub()
    return hub.ai.status() if hub.ai else {"enabled": False}


@router.post("/ai/config")
def ai_config(values: dict):
    allowed = [k for k in values if k.startswith("ai.")]
    errors = settings_svc.set_many({k: values[k] for k in allowed})
    if errors:
        raise HTTPException(400, errors)
    return {"ok": True, "ai": settings_svc.get_section("ai.")}


@router.get("/ai/sessions")
def ai_sessions():
    return {"sessions": db.query("SELECT * FROM ai_sessions ORDER BY id DESC LIMIT 50")}


# ----------------------------------------------------------------- settings
@router.get("/settings")
def settings_all():
    return {"settings": settings_svc.export_all()}


@router.put("/settings")
def settings_update(values: dict):
    errors = settings_svc.set_many(values)
    if errors:
        raise HTTPException(400, errors)
    return {"ok": True, "updated": len(values)}


@router.post("/settings/reset")
def settings_reset(key: str):
    try:
        settings_svc.reset_key(key)
    except KeyError:
        raise HTTPException(404, "unknown key")
    return {"ok": True}


# ----------------------------------------------------------------- system
@router.get("/system/health")
def system_health():
    return get_hub().health()


@router.get("/system/info")
def system_info():
    import platform
    import sys
    hub = get_hub()
    return {
        "app": "Gear360 Hub",
        "version": "1.0.0",
        "python": sys.version.split(" ")[0],
        "platform": f"{platform.system()} {platform.release()}",
        "mode": hub.mode,
        "ffmpeg": {"found": hub.ffmpeg.found, "path": hub.ffmpeg.path,
                   "source": hub.ffmpeg.source, "version": hub.ffmpeg.version.split("\n")[0]},
        "database": {"schema_version": db.schema_version(), "size_bytes": db.db_size_bytes()},
        "recognition_available": RecognitionEngine.available(),
    }


@router.get("/system/logs")
def system_logs(domain: str = "app", tail: int = 100):
    safe = {"app", "camera", "stream", "decoder", "ai", "events", "media", "api", "errors"}
    if domain not in safe:
        raise HTTPException(404, "unknown domain")
    from ..core.paths import DIRS
    log_file = DIRS["logs"] / f"{domain}.log"
    lines: list[str] = []
    if log_file.exists():
        with open(log_file, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()[-min(tail, 1000):]
    return {"domain": domain, "lines": [l.rstrip("\n") for l in lines]}


@router.get("/system/db-logs")
def db_logs(limit: int = 200):
    return {"logs": db.query("SELECT * FROM system_logs ORDER BY id DESC LIMIT ?", (min(limit, 1000),))}


@router.post("/system/demo")
def demo_mode(enabled: bool):
    return get_hub().set_demo(enabled)


@router.post("/system/prune")
def system_prune():
    hub = get_hub()
    if not settings_svc.get("storage.prune_enabled"):
        raise HTTPException(409, "Pruning disabled in settings")
    n = library.prune(int(settings_svc.get("storage.max_age_days", 30)))
    return {"deleted": n}


@router.get("/system/storage-info")
def storage_info():
    disk = shutil.disk_usage(str(Path.cwd()))
    return {"free_bytes": disk.free, "total_bytes": disk.total}


# ----------------------------------------------------------------- WebSocket
@router.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    from fastapi import WebSocket as _WS
    hub = get_hub()
    await ws.accept()
    hub.ws.register(ws)
    hub.ws._loop = asyncio.get_running_loop()
    try:
        while True:
            # client pings keep the socket alive; server pushes state at ws_rate
            msg = await ws.receive_text()
            if msg == "ping":
                await ws.send_text('{"type":"pong"}')
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        hub.ws.unregister(ws)
