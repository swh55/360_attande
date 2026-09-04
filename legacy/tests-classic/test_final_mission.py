"""Final-mission regression tests (user mission list items 3, 7, 8, 15-18,
30-31, 36): honest state semantics, non-competing diagnostics, counters,
camera control and the range-only live transport.

These are REGRESSION tests — the real camera acceptance still happens on the
user's machine against the real SM-R210 (fixtures cannot prove camera truth).
"""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest


# ---------------------------------------------------------------- transport
def test_live_transport_uses_range_only():
    """Hard evidence (DG-20260903-001 http_variants): only Range requests get
    a body; plain/close stall. The LIVE transport must never rotate into the
    dead styles again."""
    from app.stream.parser import LIVE_REQUEST_STYLE, REQUEST_STYLES

    assert REQUEST_STYLES == (LIVE_REQUEST_STYLE,)
    name, headers = LIVE_REQUEST_STYLE
    assert name == "range"
    assert headers.get("Range") == "bytes=0-"


def test_iter_camera_bytes_sends_range_every_attempt(fake_server):
    """Every live attempt must carry the Range header (no style rotation)."""
    from app.stream.parser import iter_camera_bytes

    srv = fake_server("stream")
    stop = threading.Event()
    cfg = {"host": "127.0.0.1", "port": srv.port, "path": "/livestream_high.avi",
           "request_timeout_seconds": 2.0, "reconnect_seconds": 0.2}
    got = bytearray()
    gen = iter_camera_bytes(cfg, stop)
    try:
        for chunk in gen:
            got.extend(chunk)
            if len(got) >= 8192:
                break
    finally:
        gen.close()
        stop.set()
    assert len(got) >= 8192
    for r in srv.requests:
        assert r["range"] == "bytes=0-"


# ------------------------------------------------------- snapshot semantics
def test_snapshot_payload_reachable_camera_is_not_camera_disconnected():
    """Mission item 18: reachable camera + no live session must report
    NO_FRAME_AVAILABLE — CAMERA_DISCONNECTED is reserved for an unreachable
    camera."""
    from app.diagnostics.errors import snapshot_error_payload

    hub = SimpleNamespace(
        mode="idle",
        bus=SimpleNamespace(latest=lambda: None, published=0),
        camera=SimpleNamespace(
            state="disconnected",
            camera_reachable=True,          # ping answers — camera is THERE
            _pipeline=None,
        ),
    )
    payload = snapshot_error_payload(hub)
    assert payload["code"] == "NO_FRAME_AVAILABLE"
    assert payload["stage"] == "stream"
    assert payload["evidence"]["camera_connected"] is True
    assert payload["evidence"]["stream_connected"] is False


def test_snapshot_payload_unreachable_camera_keeps_camera_disconnected():
    from app.diagnostics.errors import snapshot_error_payload

    hub = SimpleNamespace(
        mode="idle",
        bus=SimpleNamespace(latest=lambda: None, published=0),
        camera=SimpleNamespace(
            state="disconnected",
            camera_reachable=False,         # nothing answers on the network
            _pipeline=None,
        ),
    )
    payload = snapshot_error_payload(hub)
    assert payload["code"] == "CAMERA_DISCONNECTED"
    assert payload["stage"] == "camera"


# ------------------------------------------------------------------ phase
def _fake_pipeline(**kw):
    p = SimpleNamespace(
        http_ok=False, first_byte_wall=0.0, last_frame_wall=0.0,
        decoded_frames=0, parser=None, transport_status="idle",
        stalled_attempts_total=0, bytes_per_sec=0, error=None, fps=0.0,
    )
    for k, v in kw.items():
        setattr(p, k, v)
    return p


def test_phase_chain_progression():
    """CONNECTED is NOT STREAMING is NOT FRAME READY — the phase must walk
    the full chain and never jump to STREAMING without decoded frames."""
    from app.camera.manager import CameraManager

    mgr = CameraManager.__new__(CameraManager)   # skip __init__ (no bus needed)
    mgr.state = "disconnected"
    mgr._pipeline = None
    assert mgr.phase() == "DISCONNECTED"

    mgr.state = "connecting"
    mgr._pipeline = _fake_pipeline()
    assert mgr.phase() == "CONNECTING"

    mgr._pipeline = _fake_pipeline(http_ok=True, first_byte_wall=time.time())
    assert mgr.phase() == "STREAM_CONNECTED"

    mgr._pipeline.parser = SimpleNamespace(chunks_seen=5, info=SimpleNamespace(valid=False, width=0))
    assert mgr.phase() == "PARSING"

    mgr._pipeline.parser.info = SimpleNamespace(valid=True, width=1280, height=640)
    assert mgr.phase() == "DECODING"

    mgr._pipeline.decoded_frames = 7
    mgr._pipeline.last_frame_wall = time.time() - 1.0
    assert mgr.phase() == "STREAMING"

    mgr._pipeline.last_frame_wall = time.time() - 30.0
    assert mgr.phase() == "FRAME_READY"


# ---------------------------------------------------------------- counters
def test_pipeline_counters_table_shape():
    from app.media.decoder import StreamPipeline
    from app.media.framebus import FrameBus

    bus = FrameBus()
    p = StreamPipeline(bus, ffmpeg_path="ffmpeg")
    p.encoded_frames = 3
    p.decoded_frames = 2
    p.video_bytes_received = 1234
    p.parser = SimpleNamespace(bytes_seen=9999, chunks_seen=10,
                               tag_counts={"00VD": 3, "TTTS": 1})
    p.decoder = SimpleNamespace(bytes_in=1234)
    c = p.counters()
    assert c["http_bytes"] == 9999
    assert c["chunks"] == 10
    assert c["chunks_00vd"] == 3
    assert c["h264_bytes"] == 1234
    assert c["aus"] == 3
    assert c["ffmpeg_input_bytes"] == 1234
    assert c["decoded_frames"] == 2
    assert c["published_frames"] == 0
    assert c["viewer_frames"] == 0


def test_parser_tag_counts_increment():
    from app.stream.parser import Gear360ChunkParser
    from tests.fixtures import make_synthetic_stream

    data = make_synthetic_stream([b"\x41" * 16, b"\x41" * 16], codec=0)
    parser = Gear360ChunkParser()
    parser.feed(data)
    assert parser.tag_counts.get("TTTS", 0) == 1
    assert parser.tag_counts.get("VID0", 0) == 1
    assert parser.tag_counts.get("00VD", 0) >= 2
    snap = parser.diagnostics_snapshot()
    assert snap["tag_counts"]["00VD"] >= 2


def test_framebus_clear_and_viewer_counter():
    import numpy as np

    from app.media.framebus import Frame, FrameBus

    bus = FrameBus()
    bus.publish(Frame(image=np.zeros((4, 4, 3), dtype=np.uint8)))
    assert bus.latest() is not None
    bus.note_viewer_frame()
    assert bus.viewer_frames == 1
    bus.clear()
    assert bus.latest() is None          # stale frame gone, counters stay
    assert bus.viewer_frames == 1


# ------------------------------------------------------------ camera control
def test_parse_list_line_variants():
    from app.camera.control import _parse_list_line

    e = _parse_list_line("-rwxr-xr-x 1 root root 3456789 Jan 01 00:11 SAM_0001.JPG")
    assert e == {"name": "SAM_0001.JPG", "size": 3456789, "is_dir": False}
    d = _parse_list_line("drwxr-xr-x 2 root root 4096 Jan 01 2024 100PHOTO")
    assert d["is_dir"] and d["name"] == "100PHOTO"
    assert _parse_list_line("total 4") is None
    assert _parse_list_line("") is None


def test_take_photo_structured_error_when_telnet_down(tmp_db):
    """With no telnet on the target, Take Photo must fail with a STRUCTURED
    transport code — never fake success, never hang."""
    from app.camera import control
    from app.core import settings as settings_svc

    settings_svc.load()
    settings_svc.set_value("camera.host", "127.0.0.1")
    settings_svc.set_value("camera.control.telnet_port", 1)   # nothing listens
    settings_svc.set_value("camera.control.settle_ms", 500)
    with pytest.raises(control.ControlError) as ei:
        control.take_photo()
    assert ei.value.code == control.E_TRANSPORT


def test_control_status_shape(tmp_db):
    from app.camera import control
    from app.core import settings as settings_svc

    settings_svc.load()
    settings_svc.set_value("camera.host", "127.0.0.1")
    out = control.status()
    assert set(out) >= {"enabled", "host", "command", "telnet", "ftp", "available"}
    assert "ok" in out["telnet"] and "ok" in out["ftp"]


# ----------------------------------------------------------------- build info
def test_build_info_banner():
    from app.build_info import banner_lines, build_info

    b = build_info()
    assert b["version"]
    lines = banner_lines()
    assert any("BUILD COMMIT:" in l for l in lines)
    assert any("BUILD TIME:" in l for l in lines)


# ------------------------------------------------- diagnostics non-competition
def test_test_connection_never_probes_while_live(monkeypatch):
    """Mission items 7+8: while the live pipeline exists, /camera/test must
    NOT open a second HTTP connection to the camera."""
    from app.camera.manager import CameraManager

    mgr = CameraManager.__new__(CameraManager)
    mgr.state = "streaming"
    mgr.camera_reachable = True
    mgr.last_latency_ms = 7.0
    mgr._pipeline = _fake_pipeline(http_ok=True, bytes_per_sec=95000,
                                   first_byte_wall=time.time())
    mgr._pipeline.parser = SimpleNamespace(
        bytes_seen=1_234_567, chunks_seen=5,
        info=SimpleNamespace(valid=True, width=1280, height=640))
    mgr._pipeline.http_status = 206
    mgr._pipeline.http_content_type = "video/x-avi "

    opened = []
    import socket as _socket
    real_create = _socket.create_connection

    def spy(*a, **kw):
        opened.append(a)
        return real_create(*a, **kw)

    monkeypatch.setattr(_socket, "create_connection", spy)
    # requests.get must not be reached either
    import requests as _requests
    monkeypatch.setattr(_requests, "get",
                        lambda *a, **kw: opened.append(("requests.get", a)) or (_ for _ in ()).throw(AssertionError("probe opened!")))

    out = mgr.test_connection()
    assert opened == []                      # zero new connections
    assert out["source"].startswith("live-pipeline")
    assert out["http_status"] == 206
    assert out["bytes_received"] == 1_234_567
    assert out["phase"] in ("STREAM_CONNECTED", "PARSING", "DECODING", "STREAMING", "FRAME_READY")
