"""Snapshot manager: capture latest frame in raw / equirect / perspective modes."""
from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from ..core import database as db
from ..core.bus import bus as event_bus
from ..core.paths import DIRS
from .framebus import Frame, FrameBus
from .projections import Calibration, EquirectProjector
from .recorder import _resize_max

LOG = logging.getLogger("hub.media")


class SnapshotManager:
    def __init__(self, bus: FrameBus):
        self.bus = bus
        self._projector: EquirectProjector | None = None
        self._proj_key: tuple | None = None

    def _projector_for(self, w: int, h: int, cal: Calibration, out_w: int) -> EquirectProjector:
        key = (w, h, out_w, round(cal.fov_deg, 1), round(cal.radius_factor, 3),
               round(cal.center_offset_x, 3), round(cal.lens0_azimuth_deg, 1),
               cal.swap_lenses, round(cal.roll_deg, 1))
        if self._projector is None or self._proj_key != key:
            self._projector = EquirectProjector(w, h, out_w, cal)
            self._proj_key = key
        return self._projector

    def capture(
        self,
        cal: Calibration,
        projection: str = "raw",           # raw | equirect | perspective
        yaw: float = 0.0, pitch: float = 0.0, fov: float = 90.0,
        event_id: int | None = None,
        note: str = "",
        frame: Frame | None = None,
        max_side: int = 2048,
    ) -> dict:
        if frame is None:
            frame = self.bus.latest()
        if frame is None:
            raise RuntimeError("No frame available")
        img = frame.image
        h, w = img.shape[:2]

        if projection == "equirect":
            proj = self._projector_for(w, h, cal, min(2048, w))
            out = proj.project(img)
        elif projection == "perspective":
            proj = self._projector_for(w, h, cal, min(2048, w))
            eq = proj.project(img)
            from .projections import project_to_perspective
            vw = min(1280, eq.shape[1] // 2)
            out = project_to_perspective(eq, yaw, pitch, fov, vw, vw * 9 // 16)
        else:
            out = img

        out = _resize_max(out, max_side)
        now = datetime.now()
        rel_dir = Path("photos") / f"{now.year:04d}" / f"{now.month:02d}" / f"{now.day:02d}"
        abs_dir = DIRS["base"] / rel_dir
        abs_dir.mkdir(parents=True, exist_ok=True)
        suffix = {"raw": "snap", "equirect": "pano", "perspective": "view"}[projection]
        filename = now.strftime("%Y-%m-%d_%H-%M-%S") + f"_{suffix}.jpg"
        path = abs_dir / filename
        ok, buf = cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, 92])
        if not ok:
            raise RuntimeError("JPEG encode failed")
        buf.tofile(path)  # non-ASCII-safe save

        # thumbnail
        thumb_dir = DIRS["thumbs"]
        th = _resize_max(out, 480)
        thumb_path = thumb_dir / f"photo_{now.strftime('%Y%m%d%H%M%S%f')}.jpg"
        cv2.imwrite(str(thumb_path), th, [cv2.IMWRITE_JPEG_QUALITY, 75])

        photo_id = db.execute(
            """INSERT INTO photos (path, thumb_path, filename, created_at, size_bytes,
               width, height, is_360, projection, event_id, note)
               VALUES (?, ?, ?, datetime('now','localtime'), ?, ?, ?, 1, ?, ?, ?)""",
            (str(path), str(thumb_path), filename, path.stat().st_size,
             out.shape[1], out.shape[0], projection, event_id, note),
        )
        LOG.info("Snapshot %s -> %s", projection, path)
        event_bus.publish("photo_taken", {"id": photo_id, "path": str(path), "projection": projection})
        return {"id": photo_id, "path": str(path), "filename": filename,
                "projection": projection, "size": path.stat().st_size}

    def save_ai_snapshot(self, img: np.ndarray, event_type: str, event_id: int) -> str:
        """Persist an AI detection snapshot (already-cropped/annotated image)."""
        now = datetime.now()
        rel_dir = Path("photos") / "events" / f"{now.year:04d}" / f"{now.month:02d}" / f"{now.day:02d}"
        abs_dir = DIRS["base"] / rel_dir
        abs_dir.mkdir(parents=True, exist_ok=True)
        filename = now.strftime("%Y-%m-%d_%H-%M-%S") + f"_{event_type}.jpg"
        path = abs_dir / filename
        ok, buf = cv2.imencode(".jpg", _resize_max(img, 960), [cv2.IMWRITE_JPEG_QUALITY, 85])
        if ok:
            buf.tofile(path)
            return str(path)
        return ""
