"""Diagnostics API integration tests: full app via TestClient.

Offline suite (camera unreachable): the full diagnostic must complete without
crashing, every endpoint must answer, and the snapshot endpoint must return a
STRUCTURED 409. Plus one true end-to-end test: a local HTTP server streams a
synthetic Gear360 container (real H.264 access units) and the pipeline must
produce frames and a working snapshot."""
from __future__ import annotations

import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("GEAR360HUB_DB", str(Path(__file__).parent / ".tmp-diagapi.db"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def done_session(client):
    """Run one full diagnostic (camera unreachable → fast FAILs) and wait."""
    client.post("/api/diagnostics/run")
    st = {}
    for _ in range(120):
        st = client.get("/api/diagnostics/status").json()
        if not st.get("running"):
            break
        time.sleep(0.5)
    return st


def test_run_full_diagnostic_completes(client, done_session):
    sid = client.get("/api/diagnostics/status").json()["latest_id"]
    assert sid
    s = client.get(f"/api/diagnostics/sessions/{sid}").json()["session"]
    assert s["id"] == sid
    assert len(s["steps"]) >= 19
    allowed = ("PASS", "WARNING", "FAIL", "NOT_TESTED")
    for step in s["steps"]:
        assert step["status"] in allowed, f"step {step['key']} status {step['status']}"
    assert s["summary"]["stage_status"]["camera"] in allowed
    assert s["findings"], "root-cause findings must exist"


def test_inspector_endpoints_offline(client):
    for ep in ("vid0", "hexdump", "structure", "scan", "inspector"):
        r = client.get(f"/api/diagnostics/{ep}")
        assert r.status_code == 200, f"{ep} -> {r.status_code}"
    r = client.get("/api/diagnostics/hexdump?offset=0&length=256")
    assert "rows" in r.json()


def test_snapshot_structured_409(client):
    r = client.get("/api/stream/snapshot?projection=equirect")
    if r.status_code == 200:
        pytest.skip("frames available on the shared hub — snapshot works")
    assert r.status_code == 409
    body = r.json()
    assert body["error"] == "SNAPSHOT_UNAVAILABLE"
    assert body["code"], "a machine-readable code is required"
    assert body["stage"]
    assert isinstance(body["retryable"], bool)
    assert body["suggested_action"]
    assert "evidence" in body


def test_decoder_metrics_health_endpoints(client):
    r = client.get("/api/diagnostics/decoder")
    assert r.status_code == 200 and "ffmpeg" in r.json()
    r = client.get("/api/diagnostics/metrics")
    assert r.status_code == 200 and "bytes_per_sec" in r.json()
    r = client.post("/api/diagnostics/test/ffmpeg")
    assert r.status_code == 200 and r.json().get("found") is True
    r = client.get("/api/diagnostics/health-machine")
    assert r.status_code == 200 and r.json()["state"]


def test_report_export_json_and_zip(client, done_session):
    r = client.get("/api/diagnostics/report")
    assert r.status_code == 200
    j = r.json()
    for key in ("system", "camera", "network", "http", "stream", "chunks",
                "parser", "decoder", "frame", "snapshot", "errors", "warnings",
                "timing", "findings"):
        assert key in j, f"report missing {key}"
    r = client.get("/api/diagnostics/report/zip")
    assert r.status_code == 200
    assert "zip" in r.headers.get("content-type", "")


def test_full_pipeline_synthetic_camera(client, real_h264_access_units):
    """End-to-end: local HTTP server streams a synthetic Gear360 container
    (real H.264 AUs) → HTTP reader → parser (fixed VID0 layout) → FFmpeg
    decoder → FrameBus → snapshot 200 + health machine progresses."""
    from tests.fixtures import make_synthetic_stream

    aus = real_h264_access_units["aus"]
    w, h = real_h264_access_units["width"], real_h264_access_units["height"]
    stream = make_synthetic_stream(aus, codec=0, width=w, height=h, fps=10.0,
                                   force_first_keyframe=False)
    big = stream * 8          # keeps the connection busy

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            try:
                self.send_response(206)
                self.send_header("Content-Type", "video/x-avi")
                self.send_header("Content-Length", str(len(big)))
                self.end_headers()
                for i in range(0, len(big), 32 * 1024):
                    if getattr(self.server, "stop").is_set():
                        break
                    self.wfile.write(big[i:i + 32 * 1024])
                    self.wfile.flush()
            except Exception:
                pass

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    srv.stop = threading.Event()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]

    client.put("/api/settings", json={"camera.host": "127.0.0.1", "camera.port": port})
    try:
        client.post("/api/camera/connect")
        deadline = time.time() + 30
        frames_seen = 0
        st = {}
        while time.time() < deadline:
            st = client.get("/api/stream/status").json()
            frames_seen = st.get("frames_published") or 0
            if st.get("fps") and frames_seen > 0:
                break
            time.sleep(0.4)
        assert frames_seen > 0, f"no frames published; status={st}"
        r = client.get("/api/stream/snapshot?projection=raw")
        assert r.status_code == 200, f"snapshot failed: {r.status_code} {r.text[:200]}"
        assert r.json().get("filename")
        # the health machine is evaluated asynchronously by the hub state loop
        # (every 0.5 s) — wait briefly for it to observe the live pipeline
        hm = {}
        layers = {}
        hm_deadline = time.time() + 10
        while time.time() < hm_deadline:
            hm = client.get("/api/diagnostics/health-machine").json()
            layers = hm.get("layers", {})
            if (hm.get("state") in ("READY", "DECODING", "PARSING", "STREAMING")
                    and layers.get("metadata", {}).get("state") == "ok"
                    and layers.get("decoder", {}).get("state") == "ok"):
                break
            time.sleep(0.3)
        assert hm["state"] in ("READY", "DECODING", "PARSING", "STREAMING")
        assert layers.get("metadata", {}).get("state") == "ok"
        assert layers.get("decoder", {}).get("state") == "ok"
    finally:
        client.post("/api/camera/disconnect")
        client.put("/api/settings", json={"camera.host": "192.168.43.1", "camera.port": 7679})
        srv.stop.set()
        srv.shutdown()
