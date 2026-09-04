"""Recording manager: taps the FrameBus, encodes MP4 via FFmpeg, indexes in DB.

Layout: recordings/YYYY/MM/DD/HH/YYYY-MM-DD_HH-MM-SS.mp4  (+ .thumbs/*.jpg)
Supports start/stop/pause/resume, fps capping, auto-stop on stream idle, and
event-triggered clips. Never buffers unbounded frames.
"""
from __future__ import annotations

import logging
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from ..core import database as db
from ..core.bus import bus as event_bus
from ..core.paths import DIRS
from .ffmpeg import FFmpegInfo
from .framebus import Frame, FrameBus, Tap

LOG = logging.getLogger("hub.media")


class RecordingHandle:
    def __init__(self, rec_id: int, path: Path, started_at: float):
        self.id = rec_id
        self.path = path
        self.started_at = started_at
        self.paused = False
        self.frames = 0
        self.stopped_at: Optional[float] = None

    @property
    def elapsed(self) -> float:
        end = self.stopped_at or time.time()
        return max(0.0, end - self.started_at)


class RecordingManager:
    def __init__(self, bus: FrameBus, ffmpeg: FFmpegInfo):
        self.bus = bus
        self.ffmpeg = ffmpeg
        self.active: Optional[RecordingHandle] = None
        self._proc: Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._pause = threading.Event()
        self._lock = threading.Lock()
        self.error: Optional[str] = None
        self._last_frame_wall = 0.0

    # ---- public API --------------------------------------------------------
    def start(self, width: int, height: int, fps: int, crf: int = 23,
              trigger_event_id: int | None = None) -> RecordingHandle:
        with self._lock:
            if self.active is not None:
                raise RuntimeError("Recording already active")
            if not self.ffmpeg.found:
                raise RuntimeError("FFmpeg not available: " + (self.ffmpeg.error or ""))
            now = datetime.now()
            rel_dir = Path("recordings") / f"{now.year:04d}" / f"{now.month:02d}" / f"{now.day:02d}" / f"{now.hour:02d}"
            abs_dir = DIRS["base"] / rel_dir
            abs_dir.mkdir(parents=True, exist_ok=True)
            filename = now.strftime("%Y-%m-%d_%H-%M-%S") + ".mp4"
            path = abs_dir / filename

            cmd = [
                self.ffmpeg.path, "-hide_banner", "-loglevel", "error", "-y",
                "-f", "rawvideo", "-pix_fmt", "bgr24",
                "-s", f"{width}x{height}", "-r", str(fps), "-i", "pipe:0",
                "-an", "-c:v", "libx264", "-preset", "veryfast",
                "-crf", str(crf), "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                str(path),
            ]
            LOG.info("Recording start -> %s", path)
            self._proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
            self._stop.clear()
            self._pause.clear()
            handle = RecordingHandle(0, path, time.time())
            rec_id = db.execute(
                """INSERT INTO recordings (path, thumb_path, filename, created_at, width, height, fps, is_360, event_id)
                   VALUES (?, NULL, ?, datetime('now','localtime'), ?, ?, ?, 1, ?)""",
                (str(path), filename, width, height, fps, trigger_event_id),
            )
            handle.id = rec_id
            self.active = handle
            self._thread = threading.Thread(
                target=self._run, args=(handle, width, height, fps), name="recorder", daemon=True
            )
            self._thread.start()
            event_bus.publish("recording_started", {"id": rec_id, "path": str(path)})
            return handle

    def stop(self) -> Optional[dict]:
        with self._lock:
            handle = self.active
            if handle is None:
                return None
            handle.stopped_at = time.time()
            self.active = None
            self._stop.set()
        if self._thread:
            self._thread.join(timeout=6)
        if self._proc:
            try:
                self._proc.stdin.close()
                self._proc.wait(timeout=8)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None
        size = handle.path.stat().st_size if handle.path.exists() else 0
        thumb = self._make_thumb(handle.path, handle.id)
        db.execute(
            """UPDATE recordings SET duration_sec=?, size_bytes=?, frames=?, thumb_path=?
               WHERE id=?""",
            (round(handle.elapsed, 2), size, handle.frames, thumb, handle.id),
        )
        LOG.info("Recording stop id=%s frames=%s size=%s", handle.id, handle.frames, size)
        event_bus.publish("recording_stopped", {"id": handle.id, "frames": handle.frames})
        return {"id": handle.id, "path": str(handle.path), "duration": handle.elapsed,
                "frames": handle.frames, "size": size}

    def toggle_pause(self) -> bool:
        if self.active is None:
            return False
        if self._pause.is_set():
            self._pause.clear()
        else:
            self._pause.set()
        self.active.paused = self._pause.is_set()
        return self.active.paused

    def snapshot_of_active(self) -> Optional[RecordingHandle]:
        return self.active

    # ---- internals ----------------------------------------------------------
    def _run(self, handle: RecordingHandle, width: int, height: int, fps: int) -> None:
        tap = self.bus.add_tap(f"recorder:{handle.id}", maxsize=2)
        frame_interval = 1.0 / max(1, fps)
        idle_limit = 8.0
        try:
            while not self._stop.is_set():
                if self._pause.is_set():
                    time.sleep(0.1)
                    continue
                frame = tap.get(timeout=0.4)
                if frame is None:
                    if (time.time() - self._last_frame_wall > idle_limit
                            and self._last_frame_wall > 0
                            and handle.frames > 0):
                        LOG.warning("Recorder idle: stopping segment %s", handle.id)
                        threading.Thread(target=self.stop, daemon=True).start()
                        break
                    continue
                if self._pause.is_set():
                    continue
                if self._write_frame(frame, width, height):
                    handle.frames += 1
                    self._last_frame_wall = time.time()
                    time.sleep(0)  # yield; pacing comes from bus cadence
        except Exception as exc:
            self.error = str(exc)
            LOG.exception("Recorder thread failed")
        finally:
            self.bus.remove_tap(f"recorder:{handle.id}")

    def _write_frame(self, frame: Frame, width: int, height: int) -> bool:
        if self._proc is None or self._proc.stdin is None:
            return False
        img = frame.image
        if img.shape[1] != width or img.shape[0] != height:
            img = cv2.resize(img, (width, height))
        try:
            self._proc.stdin.write(img.tobytes())
            self._proc.stdin.flush()
            return True
        except (BrokenPipeError, OSError) as exc:
            self.error = str(exc)
            LOG.error("Recorder pipe broken: %s", exc)
            threading.Thread(target=self.stop, daemon=True).start()
            return False

    def _make_thumb(self, video_path: Path, rec_id: int) -> Optional[str]:
        thumb_dir = DIRS["thumbs"]
        thumb_dir.mkdir(parents=True, exist_ok=True)
        out = thumb_dir / f"rec_{rec_id}.jpg"
        try:
            cap = cv2.VideoCapture(str(video_path))
            ok, frame = cap.read()
            cap.release()
            if ok:
                frame = _resize_max(frame, 480)
                cv2.imwrite(str(out), frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                return str(out)
        except Exception:
            LOG.exception("thumb failed for %s", video_path)
        return None


def _resize_max(img: np.ndarray, max_side: int) -> np.ndarray:
    h, w = img.shape[:2]
    scale = max_side / max(h, w)
    if scale >= 1.0:
        return img
    return cv2.resize(img, (int(w * scale), int(h * scale)))
