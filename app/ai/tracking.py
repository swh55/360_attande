"""Centroid-based person tracker across perspective views (per view)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .base import Detection


@dataclass
class Track:
    track_id: int
    label: str
    cx: float
    cy: float
    bbox: tuple[float, float, float, float]
    first_seen: float = field(default_factory=lambda: _now())
    last_seen: float = field(default_factory=lambda: _now())
    hits: int = 1
    person_id: Optional[int] = None
    person_name: Optional[str] = None
    left_announced: bool = False

    @property
    def duration(self) -> float:
        return round(self.last_seen - self.first_seen, 1)


def _now() -> float:
    import time
    return time.time()


class CentroidTracker:
    """Simple IoU-free centroid tracker: nearest-match with max distance,
    max_age for disappearance, and 'person_left' detection."""

    def __init__(self, max_distance: float = 0.25, max_age: float = 3.0):
        self.max_distance = max_distance
        self.max_age = max_age
        self._tracks: dict[int, Track] = {}
        self._next_id = 1

    def update(self, detections: list[Detection], now: float | None = None) -> list[Track]:
        now = now or _now()
        unmatched = list(range(len(detections)))
        # match existing tracks to nearest detection
        for tid, track in list(self._tracks.items()):
            best_i: Optional[int] = None
            best_d = self.max_distance
            for i in unmatched:
                d = detections[i]
                dcx, dcy = d.x + d.w / 2, d.y + d.h / 2
                dist = ((track.cx - dcx) ** 2 + (track.cy - dcy) ** 2) ** 0.5
                if dist < best_d:
                    best_d = dist
                    best_i = i
            if best_i is not None:
                d = detections[best_i]
                track.cx, track.cy = d.x + d.w / 2, d.y + d.h / 2
                track.bbox = (d.x, d.y, d.w, d.h)
                track.last_seen = now
                track.hits += 1
                track.left_announced = False
                if d.person_id is not None:
                    track.person_id = d.person_id
                    track.person_name = d.person_name
                detections[best_i].track_id = tid
                unmatched.remove(best_i)
            elif now - track.last_seen > self.max_age:
                del self._tracks[tid]
            elif not track.left_announced and now - track.last_seen > 1.5:
                track.left_announced = True  # consumed by engine for person_left
        # new tracks
        for i in unmatched:
            d = detections[i]
            tid = self._next_id
            self._next_id += 1
            d.track_id = tid
            self._tracks[tid] = Track(
                track_id=tid, label=d.label,
                cx=d.x + d.w / 2, cy=d.y + d.h / 2, bbox=(d.x, d.y, d.w, d.h),
                person_id=d.person_id, person_name=d.person_name,
            )
        return list(self._tracks.values())

    def active_tracks(self) -> list[Track]:
        now = _now()
        return [t for t in self._tracks.values() if now - t.last_seen <= self.max_age]

    def recently_left(self) -> list[Track]:
        return [t for t in self._tracks.values() if t.left_announced]

    def clear(self) -> None:
        self._tracks.clear()
