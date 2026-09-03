"""Bounded frame bus: latest-frame delivery + named taps with drop-oldest policy.

Memory is strictly bounded: publishing never accumulates unbounded frames.
Slow consumers (AI, recorder) lose old frames instead of growing RAM.
"""
from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np


@dataclass
class Frame:
    image: np.ndarray            # BGR
    timestamp: int = 0           # camera timestamp (from 00VD chunk)
    keyframe: bool = False
    wall_time: float = field(default_factory=time.time)
    source: str = "live"         # live | demo


class Tap:
    """A bounded subscription queue into the bus (drop-oldest)."""

    def __init__(self, name: str, maxsize: int = 4):
        self.name = name
        self._q: "queue.Queue[Frame]" = queue.Queue(maxsize=maxsize)
        self._lock = threading.Lock()
        self.closed = False
        self.dropped = 0

    def put(self, frame: Frame) -> None:
        if self.closed:
            return
        try:
            self._q.put_nowait(frame)
        except queue.Full:
            try:
                self._q.get_nowait()
                self.dropped += 1
            except queue.Empty:
                pass
            try:
                self._q.put_nowait(frame)
            except queue.Full:
                pass

    def get(self, timeout: float = 0.5) -> Optional[Frame]:
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self) -> None:
        self.closed = True


class FrameBus:
    def __init__(self) -> None:
        self._latest: Optional[Frame] = None
        self._taps: dict[str, Tap] = {}
        self._lock = threading.Lock()
        self.published = 0
        self.viewer_frames = 0       # frames actually consumed by viewers/UI
        self.last_viewer_wall = 0.0
        self.source = "idle"

    def note_viewer_frame(self) -> None:
        """Called by viewer endpoints (mjpeg / frame.jpg) when a frame is sent."""
        self.viewer_frames += 1
        self.last_viewer_wall = time.time()

    def publish(self, frame: Frame) -> None:
        with self._lock:
            self._latest = frame
            taps = list(self._taps.values())
        self.published += 1
        for tap in taps:
            tap.put(frame)

    def latest(self) -> Optional[Frame]:
        with self._lock:
            return self._latest

    def add_tap(self, name: str, maxsize: int = 4) -> Tap:
        with self._lock:
            old = self._taps.get(name)
            if old:
                old.close()
            tap = Tap(name, maxsize=maxsize)
            self._taps[name] = tap
            return tap

    def remove_tap(self, name: str) -> None:
        with self._lock:
            tap = self._taps.pop(name, None)
        if tap:
            tap.close()

    def tap_names(self) -> list[str]:
        with self._lock:
            return list(self._taps.keys())

    def clear(self) -> None:
        """Drop the latest frame (used by Restart Stream — the viewer must
        show 'waiting' instead of a stale frame; counters stay monotonic)."""
        with self._lock:
            self._latest = None
