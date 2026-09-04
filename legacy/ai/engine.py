"""AI Engine: orchestrates motion/person/face/recognition/tracking/director.

Runs on its own thread, sampling the *latest* frame only (streaming pipeline,
never accumulating). Detections are produced on perspective views computed from
the dual-fisheye frame via equirect remap. Events flow into the EventStore with
per-type cooldowns. Live state is pushed to WebSocket subscribers.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional

import cv2
import numpy as np

from ..camera.manager import CameraManager
from ..core import database as db
from ..core import settings as settings_svc
from ..events.store import EventStore
from ..media.framebus import Frame, FrameBus
from ..media.projections import Calibration, EquirectProjector, PerspectiveViewport
from .attendance import AttendanceService
from .base import Detection, build_person_detector
from .director import RuleBasedDirector, ViewScore
from .face import OpenCVFaceDetector
from .motion import MotionDetector
from .recognition import RecognitionEngine
from .tracking import CentroidTracker

LOG = logging.getLogger("hub.ai")

VIEW_YAWS = (0.0, 90.0, 180.0, 270.0)


class AIEngine:
    def __init__(self, bus: FrameBus, store: EventStore, camera: CameraManager,
                 ws_broadcaster=None):
        self.bus = bus
        self.store = store
        self.camera = camera
        self.ws = ws_broadcaster
        self.enabled = False
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        self.motion = MotionDetector()
        self.person_detector = None
        self.face_detector: Optional[OpenCVFaceDetector] = None
        self.recognition = RecognitionEngine()
        self.attendance = AttendanceService()
        self.trackers = {i: CentroidTracker(max_age=2.5) for i in range(len(VIEW_YAWS))}
        self.director = RuleBasedDirector(
            [{"index": i, "yaw": y, "pitch": 0.0, "fov": 90.0} for i, y in enumerate(VIEW_YAWS)]
        )

        # live state for /api/ai/status + WS
        self.last_tick = 0.0
        self.frames_processed = 0
        self.detections_total = 0
        self.persons = 0
        self.faces = 0
        self.known = 0
        self.unknown = 0
        self.tracks_snapshot: list[dict] = []
        self.best_view = 0
        self.scores_snapshot: list[dict] = []
        self._last_face_ts: dict[int, float] = {}
        self._last_event_ts: dict[str, float] = {}
        self._last_frame_ts_seen = 0.0

        settings_svc.on_change(self._on_setting_change)

    # ---- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        self._apply_config()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="ai-engine", daemon=True)
        self._thread.start()
        LOG.info("AI engine thread started (enabled=%s)", self.enabled)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    def _on_setting_change(self, key: str, value) -> None:
        if key.startswith("ai.") or key.startswith("cal."):
            self._apply_config()

    def _apply_config(self) -> None:
        with self._lock:
            self.enabled = bool(settings_svc.get("ai.enabled"))
            conf = float(settings_svc.get("ai.confidence", 0.5))
            if bool(settings_svc.get("ai.person.enabled")):
                if self.person_detector is None:
                    self.person_detector = build_person_detector("auto", conf)
            else:
                self.person_detector = None
            if bool(settings_svc.get("ai.face.enabled")):
                if self.face_detector is None:
                    try:
                        self.face_detector = OpenCVFaceDetector()
                    except Exception:
                        self.face_detector = None
            else:
                self.face_detector = None
            self.motion = MotionDetector(
                sensitivity=int(settings_svc.get("ai.motion.sensitivity", 25)),
                min_area=int(settings_svc.get("ai.motion.min_area", 900)),
            )
            self.attendance.enabled = bool(settings_svc.get("ai.attendance.enabled"))
            self.attendance.cooldown_min = int(settings_svc.get("ai.attendance.cooldown_min", 5))
            self.recognition.threshold = float(settings_svc.get("ai.recognition.threshold", 0.5))

    # ---- main loop -----------------------------------------------------------
    def _run(self) -> None:
        while not self._stop.is_set():
            interval = 1.0 / max(0.5, float(settings_svc.get("ai.sample_fps", 3)))
            if not self.enabled:
                if self._stop.wait(1.0):
                    return
                continue
            frame = self.bus.latest()
            if frame is None or frame.wall_time == self._last_frame_ts_seen:
                if self._stop.wait(0.2):
                    return
                continue
            self._last_frame_ts_seen = frame.wall_time
            try:
                self._tick(frame)
            except Exception:
                LOG.exception("AI tick failed")
                if self._stop.wait(1.0):
                    return
            time.sleep(interval)

    def _tick(self, frame: Frame) -> None:
        self.frames_processed += 1
        self.last_tick = time.time()
        cal = Calibration.from_settings({k: settings_svc.get(k) for k in (
            "cal.fisheye_fov_deg", "cal.radius_factor", "cal.center_offset_x",
            "cal.lens0_azimuth_deg", "cal.swap_lenses", "cal.roll_deg")})

        h, w = frame.image.shape[:2]
        projector = EquirectProjector(w, h, min(1024, w), cal)
        equirect = projector.project(frame.image)

        viewport = PerspectiveViewport(width=480, height=270, fov=90.0)
        views = [viewport.view(equirect, yaw) for yaw in VIEW_YAWS]

        view_payloads: list[dict] = []
        scores: list[ViewScore] = []
        now = time.time()
        cooldown_motion = float(settings_svc.get("ai.motion.cooldown", 8))
        cooldown_rec = float(settings_svc.get("ai.recognition.cooldown", 60))

        persons_total = faces_total = known_total = unknown_total = 0

        for i, view in enumerate(views):
            boxes: list[dict] = []
            detections: list[Detection] = []
            motion_hit = None

            if bool(settings_svc.get("ai.motion.enabled")) and bool(settings_svc.get("ai.enabled")):
                m = self.motion.process(view)
                if m.motion:
                    motion_hit = m
                    key = f"motion:{i}"
                    if now - self._last_event_ts.get(key, 0) > cooldown_motion:
                        self._last_event_ts[key] = now
                        lon = (VIEW_YAWS[i] + (m.cx - 0.5) * 90.0 + 180.0) % 360.0 - 180.0
                        self.store.create(
                            type="motion", severity="warning",
                            message=f"Motion detected (view {i + 1})",
                            confidence=0.8, lon=lon, lat=(0.5 - m.cy) * 180.0,
                            meta={"view": i, "area": round(m.area, 1)},
                        )

            if self.person_detector is not None:
                dets = self.person_detector.detect(view)
                detections.extend(dets)

            faces = []
            if self.face_detector is not None:
                faces = self.face_detector.detect(view)
                detections.extend(faces)

            tracks = self.trackers[i].update(detections, now=now)
            for t in self.trackers[i].recently_left():
                t.left_announced = True
                if t.hits >= 5 and not settings_svc.get("demo.enabled"):
                    self.store.create(
                        type="person_left", message=f"Person #{t.track_id} left (view {i + 1})",
                        track_id=t.track_id, meta={"view": i, "duration": t.duration},
                    )

            # face recognition on largest face (throttled per view)
            if (faces and bool(settings_svc.get("ai.recognition.enabled"))
                    and RecognitionEngine.available()):
                faces_sorted = sorted(faces, key=lambda d: d.w * d.h, reverse=True)
                f = faces_sorted[0]
                if now - self._last_face_ts.get(i, 0) > 2.0:
                    self._last_face_ts[i] = now
                    fh, fw = view.shape[:2]
                    x1 = max(0, int(f.x * fw)); y1 = max(0, int(f.y * fh))
                    x2 = min(fw, int((f.x + f.w) * fw)); y2 = min(fh, int((f.y + f.h) * fh))
                    crop = view[y1:y2, x1:x2]
                    if crop.size:
                        result = self.recognition.identify(crop)
                        if result is not None:
                            key = (f"known:{result.person_id}" if result.known
                                   else "unknown")
                            if now - self._last_event_ts.get(key, 0) > cooldown_rec:
                                self._last_event_ts[key] = now
                                snapshot = self._save_snapshot(frame, cal)
                                if result.known:
                                    known_total += 1
                                    person = self.store.create(
                                        type="recognition_known", severity="success",
                                        message=f"{result.name} recognized",
                                        confidence=round(1.0 - result.distance, 2),
                                        snapshot_path=snapshot, person_id=result.person_id,
                                        lon=self._view_center_lon(i), lat=0.0,
                                        meta={"view": i, "distance": result.distance},
                                    )
                                    self._maybe_record_clip(person.get("id"))
                                    if self.attendance.enabled:
                                        att = self.attendance.process(result.person_id, result.name or "")
                                        if att:
                                            self.store.create(
                                                type=f"attendance_{att['kind']}",
                                                severity="success",
                                                message=f"{att['person_name']} "
                                                        f"{'checked in' if att['kind'] == 'in' else 'checked out'}",
                                                person_id=att["person_id"],
                                                snapshot_path=snapshot,
                                            )
                                else:
                                    unknown_total += 1
                                    self.store.create(
                                        type="recognition_unknown", severity="warning",
                                        message="Unknown person",
                                        confidence=round(1.0 - result.distance, 2),
                                        snapshot_path=snapshot,
                                        lon=self._view_center_lon(i), lat=0.0,
                                        meta={"view": i, "distance": result.distance},
                                    )

            if motion_hit is not None:
                boxes.append({"x": motion_hit.bbox[0], "y": motion_hit.bbox[1],
                              "w": motion_hit.bbox[2], "h": motion_hit.bbox[3],
                              "label": "motion", "conf": 0.8, "track_id": None})
            for d in detections:
                boxes.append({"x": round(d.x, 3), "y": round(d.y, 3),
                              "w": round(d.w, 3), "h": round(d.h, 3),
                              "label": d.label, "conf": d.confidence,
                              "track_id": d.track_id,
                              "person": d.person_name})
            persons_total += sum(1 for d in detections if d.label == "person")
            faces_total += len(faces)
            self.detections_total += len(detections)

            view_payloads.append({
                "view": i, "yaw": VIEW_YAWS[i], "pitch": 0.0, "fov": 90.0,
                "boxes": boxes,
                "motion": bool(motion_hit),
                "persons": sum(1 for d in detections if d.label == "person"),
                "faces": len(faces),
            })
            scores.append(ViewScore(
                view_index=i, yaw=VIEW_YAWS[i], pitch=0.0,
                score=len(faces) * 2.0 + sum(1 for d in detections if d.label == "person") * 1.5
                      + (0.5 if motion_hit else 0.0),
                persons=sum(1 for d in detections if d.label == "person"),
                faces=len(faces), motion=bool(motion_hit),
            ))

        best = self.director.update(scores) if scores and bool(settings_svc.get("ai.director.enabled")) else None

        self.persons = persons_total
        self.faces = faces_total
        self.known = known_total
        self.unknown = unknown_total
        self.best_view = best.view_index if best else 0
        self.scores_snapshot = [s.__dict__ | {"yaw": s.yaw} for s in scores]
        self.tracks_snapshot = [
            {"track_id": t.track_id, "view": i, "label": t.label,
             "first_seen": t.first_seen, "duration": t.duration,
             "person": t.person_name, "position": [round(t.cx, 2), round(t.cy, 2)]}
            for i, tr in self.trackers.items() for t in tr.active_tracks()
        ]

        if self.ws is not None:
            self.ws.publish({
                "type": "detections",
                "views": view_payloads,
                "best_view": self.best_view,
                "counts": {"persons": persons_total, "faces": faces_total,
                           "known": known_total, "unknown": unknown_total},
            })

    def _view_center_lon(self, view_index: int) -> float:
        return (VIEW_YAWS[view_index] + 180.0) % 360.0 - 180.0

    def _save_snapshot(self, frame: Frame, cal: Calibration) -> str:
        from ..media.snapshots import SnapshotManager
        try:
            if not hasattr(self, "_snap"):
                self._snap = SnapshotManager(self.bus)
            out = self._snap.capture(cal=cal, projection="perspective",
                                     yaw=VIEW_YAWS[self.best_view], fov=90.0,
                                     note="ai")
            return out["path"]
        except Exception:
            LOG.exception("AI snapshot failed")
            return ""

    def _maybe_record_clip(self, event_id: int) -> None:
        """Event-triggered clip: registered via hub when a recorder is active."""
        event_bus_publish = getattr(self, "_event_clip_hook", None)
        if event_bus_publish:
            try:
                event_bus_publish(event_id)
            except Exception:
                pass

    # ---- status ---------------------------------------------------------------
    def status(self) -> dict:
        return {
            "enabled": self.enabled,
            "available": {
                "person": self.person_detector.name if self.person_detector else None,
                "face": self.face_detector.name if self.face_detector else None,
                "recognition": RecognitionEngine.available(),
            },
            "mode": settings_svc.get("ai.mode"),
            "sample_fps": settings_svc.get("ai.sample_fps"),
            "frames_processed": self.frames_processed,
            "detections_total": self.detections_total,
            "counts": {"persons": self.persons, "faces": self.faces,
                       "known": self.known, "unknown": self.unknown},
            "best_view": self.best_view,
            "views": [
                {"index": i, "yaw": y, "pitch": 0.0, "fov": 90.0}
                for i, y in enumerate(VIEW_YAWS)
            ],
            "scores": self.scores_snapshot,
            "tracks": self.tracks_snapshot,
            "last_tick": self.last_tick,
        }
