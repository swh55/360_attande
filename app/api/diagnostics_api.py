"""Diagnostics API: run full diagnostics, inspect raw stream bytes, probe the
decoder, capture samples and export reports. Every endpoint is exception-
isolated and never disturbs the live pipeline."""
from __future__ import annotations

import json
import time

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, Response

from .. import diagnostics as diag
from ..core import settings as settings_svc
from ..diagnostics.capture import (capture_path, capture_sample, latest_capture,
                                   list_captures, load_slice)
from ..diagnostics.decoderprobe import decode_probe, ffmpeg_self_test
from ..diagnostics.errors import classify
from ..diagnostics.inspectors import (analyze_vid0, first_bytes_report,
                                      hexdump_rows, scan_signatures,
                                      validate_structure)
from ..hub import get_hub
from ..media.ffmpeg import locate

router = APIRouter(prefix="/api/diagnostics")


def _hub():
    return get_hub()


def _registry():
    return diag.get_registry()


def _jsonsafe(payload) -> Response:
    """Serialize any diagnostics payload; never 500 on evidence objects.
    (json.dumps default=str keeps bytes/deques representable.)"""
    return Response(content=json.dumps(payload, default=str),
                    media_type="application/json")


def _runner():
    return diag.get_runner(_hub())


def _err(stage: str, exc: Exception):
    payload = classify(str(exc), stage)
    payload["error"] = "DIAGNOSTICS_ERROR"
    return JSONResponse(status_code=500, content=payload)


def _vid0_from(data: bytes) -> bytes:
    i = data.find(b"VID0")
    if i < 0 or len(data) < i + 8:
        return b""
    size = int.from_bytes(data[i + 4:i + 8], "big")
    return data[i + 8: i + 8 + min(max(size, 0), 4096)]


def _source_bytes(source: str = "auto", name: str = "",
                  max_len: int = 8 * 1024 * 1024):
    hub = _hub()
    if source in ("auto", "live"):
        p = hub.camera._pipeline
        if p is not None and p.parser is not None and p.parser.head_bytes:
            data = bytes(p.parser.head_bytes)
            if len(data) >= 64:
                return data[:max_len], "live"
    if source in ("auto", "capture"):
        cap = name or latest_capture()
        if cap:
            try:
                return load_slice(cap, 0, max_len), f"capture:{cap}"
            except Exception:
                pass
    return b"", ""


# ---- sessions ---------------------------------------------------------------
@router.post("/run")
def run_diagnostics():
    running = _registry().running_id()
    if running:
        return {"started": False, "session": running, "progress": _runner().progress()}
    sid = _runner().start()
    return {"started": True, "session": sid}


@router.get("/status")
def diag_status():
    reg = _registry()
    latest = reg.latest()
    return {
        "progress": _runner().progress(),
        **reg.status(),
        "summary": None if latest is None else {
            "id": latest.get("id"), "finished_at": latest.get("finished_at"),
            "duration_ms": latest.get("duration_ms"),
            "summary": latest.get("summary"), "findings": latest.get("findings"),
        },
    }


@router.get("/latest")
def diag_latest():
    s = _registry().latest()
    # _jsonsafe: a diagnostic session must always be downloadable even if a
    # probe leaked a non-JSON value (never hide evidence behind a 500)
    return _jsonsafe({"session": s})


@router.get("/sessions/{sid}")
def diag_session(sid: str):
    s = _registry().get(sid)
    if not s:
        raise HTTPException(404, "session not found")
    return _jsonsafe({"session": s})


# ---- byte-level inspectors ----------------------------------------------------
@router.get("/inspector")
def diag_inspector(source: str = "auto", name: str = ""):
    try:
        data, src = _source_bytes(source, name)
        if not data:
            return {"source": "", "inspector": None,
                    "note": "no stream data — connect the camera or capture a sample"}
        rep = first_bytes_report(data)
        p = _hub().camera._pipeline
        if p is not None:
            diag = p.diagnostics()
            rep["http"] = diag.get("http", {})
            rep["transport"] = diag.get("transport", {})
        else:
            rep["http"] = {}
            rep["transport"] = None
        rep["source"] = src
        return {"source": src, "inspector": rep}
    except Exception as exc:
        return _err("http", exc)


@router.get("/hexdump")
def diag_hexdump(source: str = "auto", name: str = "",
                 offset: int = Query(0, ge=0),
                 length: int = Query(256, ge=1, le=4096)):
    try:
        data, src = _source_bytes(source, name)
        if not data:
            return {"source": "", "rows": [], "note": "no stream data"}
        return {"source": src, "rows": hexdump_rows(data, offset, length),
                "total": len(data), "offset": offset, "length": length}
    except Exception as exc:
        return _err("http", exc)


@router.get("/vid0")
def diag_vid0(source: str = "auto", name: str = ""):
    try:
        hub = _hub()
        payload, src = b"", ""
        p = hub.camera._pipeline
        if p is not None and p.parser is not None and p.parser.vid0_raw:
            payload = bytes(p.parser.vid0_raw)
            src = "live"
        else:
            data, src2 = _source_bytes(source, name)
            if data:
                payload = _vid0_from(data)
                src = src2
        if len(payload) < 40:
            return {"source": "", "vid0": None,
                    "note": "no VID0 payload — connect the camera or capture a sample"}
        return {"source": src, "vid0": analyze_vid0(payload)}
    except Exception as exc:
        return _err("parser", exc)


@router.get("/structure")
def diag_structure(source: str = "auto", name: str = ""):
    try:
        data, src = _source_bytes(source, name)
        if not data:
            return {"source": "", "structure": None, "note": "no stream data"}
        return {"source": src, "structure": validate_structure(data[:2_000_000])}
    except Exception as exc:
        return _err("chunks", exc)


@router.get("/scan")
def diag_scan(source: str = "auto", name: str = ""):
    try:
        data, src = _source_bytes(source, name)
        if not data:
            return {"source": "", "scan": None, "note": "no stream data"}
        return {"source": src, "scan": scan_signatures(data)}
    except Exception as exc:
        return _err("chunks", exc)


# ---- decoder -------------------------------------------------------------------
@router.get("/decoder")
def diag_decoder():
    hub = _hub()
    p = hub.camera._pipeline
    ff = locate(settings_svc.get("ffmpeg.mode"), settings_svc.get("ffmpeg.custom_path", ""))
    return {
        "ffmpeg": {"found": ff.found, "path": ff.path, "source": ff.source,
                   "version": (ff.version or "").split("\n")[0], "error": ff.error},
        "pipeline": p.diagnostics() if p else None,
    }


@router.post("/test/ffmpeg")
def diag_test_ffmpeg():
    try:
        return ffmpeg_self_test()
    except Exception as exc:
        return _err("decoder", exc)


@router.post("/test/decoder")
def diag_test_decoder():
    try:
        hub = _hub()
        p = hub.camera._pipeline
        aus = list(p.parser.au_ring) if (p is not None and p.parser is not None) else []
        info = p.parser.info if (p is not None and p.parser is not None) else None
        if not aus:
            data, _src = _source_bytes("auto", "", 4 * 1024 * 1024)
            if data:
                from ..stream.parser import Gear360ChunkParser
                tmp = Gear360ChunkParser()
                tmp.feed(data)
                aus = list(tmp.au_ring)
                info = tmp.info
        if not aus:
            return {"ok": False,
                    "error": "no access units available — connect the camera or capture a sample"}
        if info is None or not (64 <= info.width <= 8192 and 64 <= info.height <= 8192):
            return {"ok": False,
                    "error": f"stream metadata unusable ({getattr(info, 'width', '?')}x"
                             f"{getattr(info, 'height', '?')}) — VID0 layout issue upstream"}
        ff = locate(settings_svc.get("ffmpeg.mode"), settings_svc.get("ffmpeg.custom_path", ""))
        if not ff.found:
            return {"ok": False, "error": ff.error}
        res = decode_probe(aus[-12:], "hevc" if info.codec == 1 else "h264",
                           info.width, info.height, ff.path)
        res["source_au_count"] = len(aus)
        return res
    except Exception as exc:
        return _err("decoder", exc)


@router.get("/frame-thumb")
def diag_frame_thumb():
    hub = _hub()
    frame = hub.bus.latest()
    if frame is None:
        return JSONResponse(status_code=404, content={
            "error": "FRAME_UNAVAILABLE", "code": "NO_DECODED_FRAME",
            "message": "No decoded frame is currently available.",
            "stage": "decoder", "retryable": True})
    import cv2
    img = frame.image
    if img.shape[1] > 480:
        scale = 480 / img.shape[1]
        img = cv2.resize(img, (480, int(img.shape[0] * scale)))
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
    if not ok:
        return _err("decoder", RuntimeError("encode failed"))
    return Response(content=buf.tobytes(), media_type="image/jpeg")


# ---- metrics --------------------------------------------------------------------
@router.get("/metrics")
def diag_metrics():
    hub = _hub()
    p = hub.camera._pipeline
    metrics = {
        "mode": hub.mode,
        "fps": p.fps if p else 0.0,
        "bytes_per_sec": p.bytes_per_sec if p else 0,
        "frames_published": hub.bus.published,
        "encoded_frames": p.encoded_frames if p else 0,
        "decoded_frames": p.decoded_frames if p else 0,
        "dropped": p.decoder.dropped if (p is not None and p.decoder) else 0,
        "queue_depth": None,
        "parser_resyncs": p.parser.resyncs if (p is not None and p.parser) else 0,
        "latency_ms": hub.camera.last_latency_ms,
        "uptime_sec": round(time.time() - hub.started_at, 1),
        "cpu_percent": None, "ram_mb": None, "note": "",
    }
    if p is not None and p.decoder is not None:
        try:
            metrics["queue_depth"] = p.decoder._out_q.qsize()
        except Exception:
            pass
    try:
        import psutil  # optional dependency
        proc = psutil.Process()
        metrics["cpu_percent"] = proc.cpu_percent(interval=None)
        metrics["ram_mb"] = round(proc.memory_info().rss / (1024 * 1024), 1)
    except Exception:
        try:
            with open("/proc/meminfo") as fh:
                for line in fh:
                    if line.startswith("VmRSS"):
                        metrics["ram_mb"] = round(int(line.split()[1]) / 1024, 1)
                        break
            metrics["note"] = "cpu_percent unavailable (psutil not installed)"
        except Exception:
            metrics["note"] = "process CPU/RAM not available on this platform"
    return metrics


# ---- captures ---------------------------------------------------------------------
@router.get("/captures")
def diag_captures():
    return {"captures": list_captures()}


@router.post("/captures")
def diag_capture_new(mb: float = Query(2.0, ge=0.1, le=32.0)):
    hub = _hub()
    host = settings_svc.get("camera.host")
    port = int(settings_svc.get("camera.port", 7679))
    path = settings_svc.get("camera.stream_path", "/livestream_high.avi")
    try:
        return capture_sample(host, port, path, mb=mb)
    except Exception as exc:
        return _err("http", exc)


@router.get("/captures/{name}/download")
def diag_capture_download(name: str):
    try:
        p = capture_path(name)
        if not p.exists():
            raise HTTPException(404, "capture not found")
        return FileResponse(str(p), media_type="application/octet-stream",
                            headers={"Content-Disposition": f'attachment; filename="{name}"'})
    except HTTPException:
        raise
    except Exception as exc:
        return _err("http", exc)


@router.delete("/captures/{name}")
def diag_capture_delete(name: str):
    try:
        p = capture_path(name)
        removed = False
        for f in (p, p.with_name(p.name + ".meta.json")):
            if f.exists():
                f.unlink()
                removed = True
        return {"deleted": removed}
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, str(exc))
    except Exception as exc:
        return _err("http", exc)


# ---- reports -------------------------------------------------------------------------
@router.get("/report")
def diag_report(session: str = ""):
    s = _registry().get(session) if session else _registry().latest()
    if not s:
        raise HTTPException(404, "no diagnostic session available")
    from ..diagnostics.report import build_report
    return build_report(_hub(), s)


@router.get("/report/zip")
def diag_report_zip(session: str = ""):
    s = _registry().get(session) if session else _registry().latest()
    if not s:
        raise HTTPException(404, "no diagnostic session available")
    try:
        zpath = _runner().export_zip(s)
        return FileResponse(str(zpath), media_type="application/zip",
                            headers={"Content-Disposition": f'attachment; filename="{zpath.name}"'})
    except Exception as exc:
        return _err("http", exc)


@router.get("/health-machine")
def diag_health_machine():
    hub = _hub()
    machine = getattr(hub, "health_machine", None)
    if machine is None:
        return {"state": "UNKNOWN"}
    return machine.view()
