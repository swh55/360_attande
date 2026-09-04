"""Unified pipeline error classification (code / stage / message / evidence /
retryable / suggested fix) — shared by the API and the diagnostics engine."""
from __future__ import annotations

from typing import Optional

STAGE_CODES = {
    "camera": "CAMERA_UNREACHABLE",
    "network": "NETWORK_ERROR",
    "http": "HTTP_STREAM_ERROR",
    "container": "STREAM_FORMAT_ERROR",
    "chunks": "CHUNK_PARSE_ERROR",
    "parser": "VID0_PARSE_ERROR",
    "frames": "FRAME_EXTRACTION_ERROR",
    "codec": "CODEC_ERROR",
    "decoder": "FFMPEG_ERROR",
    "projection": "PROJECTION_ERROR",
    "snapshot": "SNAPSHOT_ERROR",
    "viewer": "UI_ERROR",
}

_KEYWORD_CODES = [
    ("timeout", "NETWORK_ERROR"), ("timed out", "NETWORK_ERROR"),
    ("unreachable", "CAMERA_UNREACHABLE"), ("refused", "CAMERA_UNREACHABLE"),
    ("206", "HTTP_STREAM_ERROR"), ("http", "HTTP_STREAM_ERROR"),
    ("vid0", "VID0_PARSE_ERROR"), ("chunk", "CHUNK_PARSE_ERROR"),
    ("ffmpeg", "FFMPEG_ERROR"), ("codec", "CODEC_ERROR"),
    ("frame", "FRAME_EXTRACTION_ERROR"), ("projection", "PROJECTION_ERROR"),
    ("snapshot", "SNAPSHOT_ERROR"),
]


def classify(message: str, stage: str) -> dict:
    """Classify a failure message into the unified error schema."""
    code = STAGE_CODES.get(stage, "NETWORK_ERROR")
    low = (message or "").lower()
    for kw, c in _KEYWORD_CODES:
        if kw in low:
            code = c
            break
    retryable = code in {
        "NETWORK_ERROR", "CAMERA_UNREACHABLE", "HTTP_STREAM_ERROR",
        "CAMERA_STREAM_STALLED", "FRAME_BUFFER_EMPTY", "SNAPSHOT_ERROR",
        "NO_FRAME_AVAILABLE", "STREAM_NOT_READY",
    }
    fixes = {
        "NETWORK_ERROR": "Check the camera Wi-Fi/hotspot and this machine's network.",
        "CAMERA_UNREACHABLE": "Verify the camera is powered, on its Wi-Fi, and the IP/port are correct in Settings.",
        "CAMERA_DISCONNECTED": "The camera did not answer (ping/TCP failed): power, Wi-Fi or IP settings.",
        "NO_FRAME_AVAILABLE": "The camera is reachable but the live stream is not connected — press Connect (Camera tab), wait for the first frame, then snapshot.",
        "HTTP_STREAM_ERROR": "Confirm the stream path (/livestream_high.avi) and that no other client holds the live stream.",
        "CAMERA_STREAM_STALLED": "Camera sends headers but never a body: power-cycle the camera, close the phone Gear 360 app (it holds the single live-stream session), and re-arm live preview; then Connect again.",
        "STREAM_FORMAT_ERROR": "Capture a stream sample and inspect the container signatures.",
        "CHUNK_PARSE_ERROR": "Run the chunk scanner — compare parser offsets with TecCheck Extractor.kt.",
        "VID0_PARSE_ERROR": "Open Parser Details — verify VID0 field offsets against Extractor.kt.",
        "FRAME_EXTRACTION_ERROR": "Inspect the first 00VD payload framing (start code / trailer).",
        "CODEC_ERROR": "Check the codec field interpretation and FFmpeg support for HEVC.",
        "FFMPEG_ERROR": "Test FFmpeg in isolation; bundle ffmpeg.exe into runtime/ffmpeg/.",
        "FRAME_BUFFER_EMPTY": "No decoded frame yet — check decoder diagnostics and wait a few seconds.",
        "PROJECTION_ERROR": "Check lens calibration values in Live settings.",
        "SNAPSHOT_ERROR": "Retry after frames are flowing; check snapshot diagnostics.",
        "UI_ERROR": "Check the browser console and the /api/mjpeg endpoint.",
    }
    return {"code": code, "stage": stage, "message": message,
            "retryable": retryable, "suggested_action": fixes.get(code, "Run Full Diagnostic.")}


def snapshot_error_payload(hub, exc: Optional[Exception] = None) -> dict:
    """Structured 409 body for /api/stream/snapshot — explains WHY, not just 409."""
    cam = hub.camera
    p = cam._pipeline
    parser = p.parser if p else None
    latest = hub.bus.latest()
    payload = {"error": "SNAPSHOT_UNAVAILABLE"}
    if latest is not None:
        payload.update({
            "code": "SNAPSHOT_FAILED" if exc else "SNAPSHOT_UNAVAILABLE",
            "stage": "snapshot" if exc else "projection",
            "message": str(exc) if exc else "Snapshot could not be produced from the latest frame.",
            "retryable": True,
            "suggested_action": "Check projection settings; inspect hub logs (logs/errors.log).",
        })
    elif p is None or cam.state == "disconnected":
        # CAMERA_DISCONNECTED is reserved for "the camera itself is not
        # answering". When the camera IS reachable but no live session
        # exists (or no frame was decoded yet) the honest code is
        # NO_FRAME_AVAILABLE — reachable != connected != frames.
        if cam.camera_reachable:
            payload.update({
                "code": "NO_FRAME_AVAILABLE", "stage": "stream",
                "message": ("Camera is reachable, but the live stream is not connected "
                            "— no frames are available for a snapshot."),
                "retryable": True,
                "suggested_action": "Press Connect (Camera tab), wait for the first decoded frame, then retry the snapshot.",
            })
        else:
            payload.update({
                "code": "CAMERA_DISCONNECTED", "stage": "camera",
                "message": "Camera is not answering (unreachable) — verify power and Wi-Fi.",
                "retryable": True,
                "suggested_action": "Check the camera is powered and this machine is on its Wi-Fi; verify the IP in Settings.",
            })
    elif cam.state in ("connecting", "reconnecting"):
        payload.update({
            "code": "STREAM_NOT_READY", "stage": "network",
            "message": "Camera connection is still being established.",
            "retryable": True,
            "suggested_action": "Wait a moment and retry; run diagnostics if it stays stuck.",
        })
    elif parser is not None and not parser.info.valid:
        payload.update({
            "code": "STREAM_METADATA_INVALID", "stage": "parser",
            "message": "Stream bytes arrive, but VID0 metadata is invalid — the decoder never started.",
            "retryable": True,
            "suggested_action": "Run Full Diagnostic and open Parser Details (VID0 field layout).",
        })
    elif p.decoder_block_reason:
        payload.update({
            "code": "DECODER_NOT_READY", "stage": "decoder",
            "message": f"Decoder blocked: {p.decoder_block_reason}",
            "retryable": True,
            "suggested_action": "Open Parser Details; verify stream metadata values.",
        })
    elif p.decoder is None:
        payload.update({
            "code": "DECODER_NOT_READY", "stage": "decoder",
            "message": "Stream parser has not produced decodable video yet.",
            "retryable": True,
            "suggested_action": "Check stream parser diagnostics; verify FFmpeg is available.",
        })
    else:
        payload.update({
            "code": "NO_FRAME_AVAILABLE", "stage": "decoder",
            "message": "Decoder is running but no decoded frame is available yet.",
            "retryable": True,
            "suggested_action": "Check stream parser and decoder diagnostics.",
        })
    payload["evidence"] = {
        "mode": hub.mode,
        "camera_state": cam.state,
        "camera_connected": bool(cam.camera_reachable),
        "stream_connected": bool(p and p.http_ok),
        "http_status": p.http_status if p else None,
        "bytes_seen": parser.bytes_seen if parser else 0,
        "chunks_seen": parser.chunks_seen if parser else 0,
        "parser_valid": bool(parser and parser.info.valid),
        "decoder_active": bool(p and p.decoder is not None),
        "decoded_frames": p.decoded_frames if p else 0,
        "frames_published": hub.bus.published,
    }
    return payload
