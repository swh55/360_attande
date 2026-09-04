"""Motion detector + event store + attendance unit tests."""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("GEAR360HUB_DB", str(Path(__file__).parent / ".tmp-ai.db"))

import numpy as np  # noqa: E402
import pytest  # noqa: E402


@pytest.fixture()
def fresh_db(tmp_db):
    return tmp_db


def _frame(t: float, moving: bool, size=(320, 240)):
    import cv2
    img = np.zeros((size[1], size[0], 3), dtype=np.uint8)
    img[:] = (30, 30, 30)
    x = int(40 + t * 60) if moving else 80
    cv2.rectangle(img, (x, 80), (x + 50, 160), (0, 0, 255), -1)
    return img


def test_motion_detects_moving_object(fresh_db):
    from app.ai.motion import MotionDetector

    det = MotionDetector(sensitivity=25, min_area=400)
    assert det.process(_frame(0, moving=False)).motion is False   # baseline
    r = det.process(_frame(1, moving=True))
    assert r.motion is True
    assert r.area > 400
    assert 0 < r.cx < 1


def test_motion_ignores_static(fresh_db):
    from app.ai.motion import MotionDetector

    det = MotionDetector(sensitivity=25, min_area=400)
    det.process(_frame(0, moving=False))
    det.process(_frame(1, moving=False))
    r = det.process(_frame(2, moving=False))
    assert r.motion is False


def test_event_store_roundtrip(fresh_db):
    from app.events.store import EventStore

    store = EventStore()
    row = store.create(type="motion", message="test motion", severity="warning",
                       confidence=0.8, lon=10.5, lat=-3.2)
    assert row["type"] == "motion"
    events = store.list_events(limit=10)
    assert any(e["id"] == row["id"] for e in events)
    stats = store.stats(24)
    assert stats["by_type"].get("motion", 0) >= 1
    tl = store.timeline(days=1)
    assert any(e["id"] == row["id"] for g in tl for e in g["events"])


def test_attendance_toggle_and_cooldown(fresh_db):
    from app.ai.attendance import AttendanceService
    from app.core import database as db

    pid = db.execute(
        "INSERT INTO people (name, created_at, updated_at) "
        "VALUES ('Att User', datetime('now','localtime'), datetime('now','localtime'))")
    svc = AttendanceService(cooldown_min=5)
    first = svc.process(pid, "Att User")
    assert first is not None and first["kind"] == "in"
    # inside cooldown -> None
    assert svc.process(pid, "Att User") is None
    # simulate cooldown expiry -> out
    svc._last[pid] = ("in", 0)
    second = svc.process(pid, "Att User")
    assert second is not None and second["kind"] == "out"
    rows = AttendanceService.summary()
    assert any(r["person_id"] == pid and r["check_in"] for r in rows)


def test_tracker_matching(fresh_db):
    from app.ai.base import Detection
    from app.ai.tracking import CentroidTracker

    tr = CentroidTracker()
    d1 = [Detection(x=0.1, y=0.1, w=0.2, h=0.2, label="person")]
    tracks = tr.update(d1)
    assert len(tracks) == 1
    tid = tracks[0].track_id
    # same position -> same id
    tracks = tr.update([Detection(x=0.12, y=0.11, w=0.2, h=0.2, label="person")])
    assert tracks[0].track_id == tid
    # far away -> new id
    tracks = tr.update([Detection(x=0.9, y=0.9, w=0.2, h=0.2, label="person")])
    assert len(tracks) == 2


def test_director_hysteresis():
    from app.ai.director import RuleBasedDirector, ViewScore

    d = RuleBasedDirector([{"index": i, "yaw": i * 90, "pitch": 0, "fov": 90}
                           for i in range(4)], margin=1.5, min_dwell=0.0)
    scores = [ViewScore(i, i * 90, 0, s, 0, 0, False)
              for i, s in enumerate([5.0, 6.0, 0.0, 0.0])]
    best = d.update(scores)
    assert best.view_index == 0  # 6.0 < 5.0 + margin
    scores2 = [ViewScore(i, i * 90, 0, s, 0, 0, False)
               for i, s in enumerate([5.0, 9.0, 0.0, 0.0])]
    best = d.update(scores2)
    assert best.view_index == 1  # 9.0 >= 5.0 + 1.5
