"""API tests: full app via TestClient with isolated DB."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("GEAR360HUB_DB", str(Path(__file__).parent / ".tmp-api.db"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_about_and_info(client):
    r = client.get("/api/about")
    assert r.status_code == 200
    assert r.json()["name"] == "Gear360 Hub"
    r = client.get("/api/system/info")
    j = r.json()
    assert j["app"] == "Gear360 Hub"
    assert j["database"]["schema_version"] >= 1


def test_health_endpoint(client):
    j = client.get("/api/system/health").json()
    for key in ("camera", "stream", "decoder", "ffmpeg", "ai", "storage", "database"):
        assert key in j
        assert j[key]["status"] in ("OK", "WARNING", "ERROR", "DISABLED")


def test_settings_roundtrip(client):
    j = client.get("/api/settings").json()
    assert "camera.host" in j["settings"]
    r = client.put("/api/settings", json={"camera.host": "192.168.1.50"})
    assert r.status_code == 200
    j = client.get("/api/settings").json()
    assert j["settings"]["camera.host"]["value"] == "192.168.1.50"
    # invalid enum rejected
    r = client.put("/api/settings", json={"ai.mode": "bogus"})
    assert r.status_code == 400
    # out-of-range int clamped
    r = client.put("/api/settings", json={"camera.port": 999999})
    assert r.status_code == 200
    j = client.get("/api/settings").json()
    assert j["settings"]["camera.port"]["value"] == 65535
    # restore
    client.put("/api/settings", json={"camera.host": "192.168.43.1", "camera.port": 7679})


def test_demo_mode_frames_and_snapshot(client):
    r = client.post("/api/system/demo?enabled=true")
    assert r.json()["mode"] == "demo"
    deadline = time.time() + 10
    published = 0
    while time.time() < deadline:
        j = client.get("/api/stream/status").json()
        published = j["frames_published"]
        if published > 5:
            break
        time.sleep(0.3)
    assert published > 5, "demo frames not published"

    # single frame endpoint
    r = client.get("/api/stream/frame.jpg")
    assert r.status_code == 200
    assert len(r.content) > 5000

    # snapshot (equirect projection of the synthetic dual-fisheye)
    r = client.get("/api/stream/snapshot?projection=equirect")
    assert r.status_code == 200
    assert Path(r.json()["path"]).exists()

    # snapshot registered in photos
    r = client.get("/api/photos?limit=5")
    assert len(r.json()["photos"]) >= 1
    client.post("/api/system/demo?enabled=false")


def test_recording_lifecycle(client):
    client.post("/api/system/demo?enabled=true")
    time.sleep(1.2)
    r = client.post("/api/recording/start")
    assert r.status_code == 200
    time.sleep(2.5)
    r = client.post("/api/recording/stop")
    assert r.status_code == 200
    out = r.json()["stopped"]
    assert out["frames"] > 5
    assert Path(out["path"]).exists()
    assert Path(out["path"]).stat().st_size > 10000
    # appears in recordings list with correct naming YYYY-MM-DD_HH-MM-SS.mp4
    recs = client.get("/api/recordings").json()["recordings"]
    assert any(x["id"] == out["id"] for x in recs)
    import re
    assert re.match(r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.mp4", recs[0]["filename"])
    client.post("/api/system/demo?enabled=false")


def test_events_and_people_crud(client):
    # create a person with photo
    import io
    import numpy as np
    import cv2
    img = np.zeros((80, 80, 3), dtype=np.uint8)
    img[:] = (90, 140, 120)
    ok, buf = cv2.imencode(".jpg", img)
    r = client.post("/api/people",
                    data={"name": "Test User", "person_code": "E-100", "department": "QA"},
                    files={"photo": ("face.jpg", io.BytesIO(buf.tobytes()), "image/jpeg")})
    assert r.status_code == 200
    pid = r.json()["id"]

    people = client.get("/api/people").json()["people"]
    assert any(p["id"] == pid and p["name"] == "Test User" for p in people)

    # update
    r = client.put(f"/api/people/{pid}", params={"department": "Ops"})
    assert r.json()["department"] == "Ops"

    # delete person
    assert client.delete(f"/api/people/{pid}").json()["deleted"]

    # vcameras CRUD
    r = client.post("/api/vcameras", json={"name": "VC1", "mode": "perspective",
                                           "yaw": 90, "pitch": 0, "fov": 90})
    assert r.status_code == 200
    vid = r.json()["id"]
    vcams = client.get("/api/vcameras").json()["virtual_cameras"]
    assert any(v["id"] == vid and v["yaw"] == 90 for v in vcams)
    assert client.delete(f"/api/vcameras/{vid}").json()["deleted"]


def test_frontend_served(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Gear360 Hub" in r.text
    r = client.get("/static/js/app.js")
    assert r.status_code == 200
