"""Camera + Stream + Media API routers."""
from __future__ import annotations

import time

import cv2
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse, StreamingResponse

from ..camera.control import ControlError, control_cfg, status as control_status, take_photo
from ..camera.profiles import load_profiles
from ..core import settings as settings_svc
from ..diagnostics.errors import snapshot_error_payload
from ..hub import get_hub
from ..media.projections import Calibration
from ..storage import library

router = APIRouter(prefix="/api")


# ----------------------------------------------------------------- camera
@router.get("/camera/status")
def camera_status():
    return get_hub().camera.info()


@router.post("/camera/connect")
def camera_connect():
    hub = get_hub()
    info = hub.camera.connect()
    hub.apply_mode()
    return info


@router.post("/camera/disconnect")
def camera_disconnect():
    hub = get_hub()
    info = hub.camera.disconnect()
    hub.apply_mode()
    return info


@router.post("/camera/test")
def camera_test():
    return get_hub().camera.test_connection()


@router.post("/camera/restart")
def camera_restart():
    """Restart Live Stream: fresh HTTP session → fresh parser → fresh decoder.
    Waits for the first frame; never reuses the old session's parser state."""
    hub = get_hub()
    info = hub.camera.restart_stream()
    hub.apply_mode()
    return info


@router.post("/camera/photo")
def camera_take_photo():
    """REAL Take Photo: camera command (st key click s2 via the mod's telnet)
    → file on the SD → FTP sync → Gallery. Independent from the live stream
    and from FrameBus snapshots. Structured 409 body on any stage failure."""
    try:
        return take_photo()
    except ControlError as exc:
        return JSONResponse(status_code=409, content={
            "error": "TAKE_PHOTO_FAILED", "code": exc.code,
            "message": str(exc), "retryable": True,
            "evidence": exc.evidence,
            "suggested_action": "Check the gear360_modding services (Telnet 23 / FTP 21) are reachable: Settings → Camera control.",
        })
    except Exception as exc:
        return JSONResponse(status_code=500, content={
            "error": "TAKE_PHOTO_FAILED", "code": "SNAPSHOT_FAILED",
            "message": str(exc), "retryable": True, "evidence": {},
        })


@router.get("/camera/control/status")
def camera_control_status():
    """Availability of the mod's control transports (telnet/ftp)."""
    return control_status()


@router.get("/camera/profiles")
def camera_profiles():
    return {"profiles": [p.__dict__ for p in load_profiles()]}


# ----------------------------------------------------------------- stream
@router.get("/stream/counters")
def stream_counters():
    """Full pipeline counter table (task: HTTP bytes → chunks → 00VD → H264
    AUs → FFmpeg input → decoded → published → viewer). Reading the table
    localises where frames stop."""
    hub = get_hub()
    p = hub.camera._pipeline
    table = p.counters() if p is not None else {
        "http_bytes": 0, "chunks": 0, "chunks_00vd": 0, "chunk_counts": {},
        "h264_bytes": 0, "aus": 0, "first_au_ts": None, "last_au_ts": None,
        "ffmpeg_input_bytes": 0, "decoded_frames": 0,
        "published_frames": hub.bus.published, "viewer_frames": hub.bus.viewer_frames,
    }
    return {"mode": hub.mode, "camera_state": hub.camera.state,
            "phase": hub.camera.phase(), "counters": table}


@router.get("/stream/status")
def stream_status():
    hub = get_hub()
    p = hub.camera._pipeline
    parser = p.parser if p else None
    machine = getattr(hub, "health_machine", None)
    return {
        "mode": hub.mode,
        "fps": hub._pipeline_fps(),
        "frames_published": hub.bus.published,
        "source": hub.bus.source,
        "taps": hub.bus.tap_names(),
        "pipeline": machine.view() if machine else None,
        "parser": None if parser is None else {
            "codec": parser.info.codec_name,
            "width": parser.info.width,
            "height": parser.info.height,
            "frame_rate": parser.info.frame_rate,
            "valid": parser.info.valid,
            "subtag_ok": parser.info.subtag_ok,
            "chunks_seen": parser.chunks_seen,
            "resyncs": parser.resyncs,
            "bytes_seen": parser.bytes_seen,
        },
        "decoder": None if p is None or p.decoder is None else {
            "codec": p.decoder.codec, "width": p.decoder.width,
            "height": p.decoder.height, "dropped": p.decoder.dropped,
            "frames_out": p.decoder.frames_out, "running": p.decoder.is_running,
        },
        "decoder_block_reason": p.decoder_block_reason if p else None,
        "error": p.error if p else None,
    }


@router.get("/stream/snapshot")
def stream_snapshot(
    projection: str = Query("raw", pattern="^(raw|equirect|perspective)$"),
    yaw: float = 0.0, pitch: float = 0.0, fov: float = 90.0,
):
    hub = get_hub()
    cal = Calibration.from_settings({k: settings_svc.get(k) for k in (
        "cal.fisheye_fov_deg", "cal.radius_factor", "cal.center_offset_x",
        "cal.lens0_azimuth_deg", "cal.swap_lenses", "cal.roll_deg")})
    try:
        out = hub.snapshots.capture(cal=cal, projection=projection,
                                    yaw=yaw, pitch=pitch, fov=fov)
    except Exception as exc:
        # structured, actionable 409 — never a bare "409 Conflict"
        return JSONResponse(status_code=409, content=snapshot_error_payload(hub, exc))
    return out


@router.get("/mjpeg")
def mjpeg(width: int | None = None):
    """MJPEG live stream (compatible with <img src>)."""
    hub = get_hub()
    quality = int(settings_svc.get("stream.mjpeg_quality", 80))
    min_interval = 1.0 / max(1, int(settings_svc.get("stream.mjpeg_fps", 20)))

    def gen():
        last = 0.0
        while True:
            frame = hub.bus.latest()
            if frame is None:
                time.sleep(0.1)
                continue
            now = time.time()
            if now - last < min_interval:
                time.sleep(min_interval - (now - last))
                continue
            last = time.time()
            img = frame.image
            if width and img.shape[1] > width:
                scale = width / img.shape[1]
                img = cv2.resize(img, (width, int(img.shape[0] * scale)))
            ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
            if ok:
                hub.bus.note_viewer_frame()
                yield (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                       + str(len(buf)).encode() + b"\r\n\r\n" + buf.tobytes() + b"\r\n")

    return StreamingResponse(gen(), media_type="multipart/x-mixed-replace; boundary=frame")


@router.get("/stream/frame.jpg")
def single_frame():
    hub = get_hub()
    frame = hub.bus.latest()
    if frame is None:
        raise HTTPException(404, "No frame available")
    ok, buf = cv2.imencode(".jpg", frame.image, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not ok:
        raise HTTPException(500, "encode failed")
    hub.bus.note_viewer_frame()
    from fastapi.responses import Response
    return Response(content=buf.tobytes(), media_type="image/jpeg")


# ----------------------------------------------------------------- recording
@router.get("/recording/status")
def recording_status():
    return {"recording": get_hub().recording_status()}


@router.post("/recording/start")
def recording_start():
    hub = get_hub()
    frame = hub.bus.latest()
    if frame is None:
        raise HTTPException(409, "No frames available - connect a camera or enable demo mode")
    h, w = frame.image.shape[:2]
    fps = min(int(settings_svc.get("recording.fps_cap", 30)), max(10, int(hub._pipeline_fps() or 30)))
    crf = int(settings_svc.get("recording.crf", 23))
    try:
        handle = hub.recorder.start(w, h, fps, crf=crf)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))
    return {"recording": hub.recording_status()}


@router.post("/recording/stop")
def recording_stop():
    hub = get_hub()
    out = hub.recorder.stop()
    if out is None:
        raise HTTPException(409, "Not recording")
    return {"stopped": out}


@router.post("/recording/pause")
def recording_pause():
    paused = get_hub().recorder.toggle_pause()
    return {"paused": paused}


# ----------------------------------------------------------------- gallery
@router.get("/recordings")
def recordings(limit: int = 100, offset: int = 0, favorite: bool | None = None):
    return {"recordings": library.list_recordings(limit, offset, favorite)}


@router.get("/photos")
def photos(limit: int = 100, offset: int = 0, projection: str | None = None,
           favorite: bool | None = None):
    return {"photos": library.list_photos(limit, offset, projection, favorite)}


@router.get("/gallery/stats")
def gallery_stats():
    return library.stats()


@router.post("/gallery/{kind}/{item_id}/favorite")
def gallery_favorite(kind: str, item_id: int):
    if kind not in ("recording", "photo"):
        raise HTTPException(404, "unknown kind")
    return {"favorite": library.toggle_favorite(kind, item_id)}


@router.delete("/gallery/{kind}/{item_id}")
def gallery_delete(kind: str, item_id: int):
    if kind not in ("recording", "photo"):
        raise HTTPException(404, "unknown kind")
    ok = library.delete_media(kind, item_id)
    if not ok:
        raise HTTPException(404, "not found")
    return {"deleted": True}


@router.get("/media/file")
def media_file(kind: str, id: int, download: bool = False):
    table = {"recording": "recordings", "photo": "photos"}.get(kind)
    if not table:
        raise HTTPException(404, "unknown kind")
    row = library.db.query_one(f"SELECT path, filename FROM {table} WHERE id=?", (id,))
    if not row:
        raise HTTPException(404, "not found")
    from pathlib import Path
    from fastapi.responses import FileResponse
    path = Path(row["path"])
    if not path.exists():
        raise HTTPException(404, "file missing on disk")
    media_type = "video/mp4" if kind == "recording" else "image/jpeg"
    headers = {"Content-Disposition": f'attachment; filename="{row["filename"]}"'} if download else {}
    return FileResponse(str(path), media_type=media_type, headers=headers)


@router.get("/media/thumb")
def media_thumb(kind: str, id: int):
    table = {"recording": "recordings", "photo": "photos"}.get(kind)
    if not table:
        raise HTTPException(404, "unknown kind")
    row = library.db.query_one(f"SELECT thumb_path, path FROM {table} WHERE id=?", (id,))
    if not row:
        raise HTTPException(404, "not found")
    from pathlib import Path
    from fastapi.responses import FileResponse
    path = Path(row["thumb_path"] or row["path"])
    if not path.exists():
        raise HTTPException(404, "thumb missing")
    return FileResponse(str(path), media_type="image/jpeg")
