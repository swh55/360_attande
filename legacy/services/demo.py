"""Demo Mode: procedural dual-fisheye source injected straight into the FrameBus.

Clearly separated from Live mode: frames carry source="demo" and the hub
reports mode="demo". Never touches the camera layer.
"""
from __future__ import annotations

import math
import threading
import time

import cv2
import numpy as np

from ..core import database as db
from ..core import settings as settings_svc
from ..core.logging_setup import get
from ..media.framebus import Frame, FrameBus
from ..stream.parser import StreamInfo

LOG = get("app")

W, H = 1280, 720
FPS = 15


class DemoSource:
    """Generates synthetic dual-fisheye frames: sky/ground grid, moving blobs,
    timestamp overlay. Good enough to develop viewer/Gallery/Events offline."""

    def __init__(self, bus: FrameBus):
        self.bus = bus
        self.info = StreamInfo(codec=1, width=W, height=H, frame_rate=float(FPS))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.published = 0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="demo-source", daemon=True)
        self._thread.start()
        LOG.info("Demo source started (%dx%d @%dfps)", W, H, FPS)
        db.execute(
            "INSERT INTO system_logs (ts, level, domain, message) "
            "VALUES (datetime('now','localtime'), 'info', 'app', 'Demo mode started')")

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        LOG.info("Demo source stopped")

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    # ---------------------------------------------------------------------
    def _run(self) -> None:
        start = time.time()
        next_tick = start
        interval = 1.0 / FPS
        ts = 0
        while not self._stop.is_set():
            next_tick += interval
            speed = float(settings_svc.get("demo.speed", 1.0))
            img = self._render((time.time() - start) * speed)
            self.bus.publish(Frame(image=img, timestamp=ts, keyframe=(ts % 60 == 0), source="demo"))
            self.bus.source = "demo"
            ts += 1
            self.published += 1
            delay = next_tick - time.time()
            if delay > 0:
                time.sleep(delay)
            else:
                next_tick = time.time()

    def _render(self, t: float) -> np.ndarray:
        img = np.zeros((H, W, 3), dtype=np.uint8)
        self._draw_fisheye_circle(img, cx=W // 4, cy=H // 2, t=t, front=True)
        self._draw_fisheye_circle(img, cx=3 * W // 4, cy=H // 2, t=t, front=False)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        cv2.putText(img, f"GEAR360 HUB - DEMO {stamp}", (24, H - 24),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2, cv2.LINE_AA)
        return img

    def _draw_fisheye_circle(self, img: np.ndarray, cx: int, cy: int, t: float, front: bool) -> None:
        r = int(H * 0.48)
        yy, xx = np.mgrid[0:H, 0:W]
        dx = xx - cx
        dy = yy - cy
        rho = np.sqrt(dx * dx + dy * dy) / r
        mask = rho <= 1.0

        # fake sky/ground from polar angle
        ang = np.arctan2(dy, dx)
        sky = (ang < 0).astype(np.float32)
        b = (60 + 140 * sky).astype(np.uint8)
        g = (140 + 60 * sky).astype(np.uint8)
        rr = (200 - 140 * sky).astype(np.uint8)

        # lat/lon grid lines in fisheye space
        grid = ((np.abs((dx / (rho + 1e-6) * 4) % 1) < 0.03) |
                (np.abs((dy / (rho + 1e-6) * 4) % 1) < 0.03))
        edge = 1.0 - np.clip(rho, 0, 1)
        grid = (grid & (edge > 0.12)).astype(np.uint8) * 90

        out_b = np.where(mask, b, 30).astype(np.uint8)
        out_g = np.where(mask, g, 30).astype(np.uint8)
        out_r = np.where(mask, rr, 30).astype(np.uint8)
        out_g = np.clip(out_g.astype(np.int16) + grid, 0, 255).astype(np.uint8)
        out_r = np.clip(out_r.astype(np.int16) + grid, 0, 255).astype(np.uint8)

        img[..., 0] = np.where(mask, out_b, img[..., 0])
        img[..., 1] = np.where(mask, out_g, img[..., 1])
        img[..., 2] = np.where(mask, out_r, img[..., 2])

        # moving "people" blobs at various fake lon/lat
        for i in range(3):
            lon = (0.55 * math.sin(t * 0.9 + i * 2.1) + (i - 1) * 1.6) % (2 * math.pi)
            if not front:
                lon = lon + math.pi
            lat = 0.16 * math.sin(t * 1.5 + i)
            px, py = self._lonlat_to_px(lon, lat, cx, cy, r)
            cv2.ellipse(img, (int(px), int(py)), (14, 34), 0, 0, 360,
                        (40, 40, 220), -1)
            cv2.circle(img, (int(px), int(py - 34)), 11, (90, 110, 245), -1)
            cv2.putText(img, f"P{i + 1}", (int(px) - 12, int(py + 52)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1, cv2.LINE_AA)

        label = "FRONT" if front else "BACK"
        cv2.putText(img, label, (cx - 44, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                    (255, 255, 255), 2, cv2.LINE_AA)

    @staticmethod
    def _lonlat_to_px(lon: float, lat: float, cx: int, cy: int, r: int) -> tuple[float, float]:
        x_f = math.cos(lat) * math.cos(lon)
        y_f = math.sin(lat)
        z_f = math.cos(lat) * math.sin(lon)
        theta = math.acos(max(-1.0, min(1.0, x_f)))
        fov_half = math.radians(195.0) / 2.0
        rho = theta / fov_half
        psi = math.atan2(-y_f, z_f)
        return cx + rho * r * math.cos(psi), cy + rho * r * math.sin(psi)
