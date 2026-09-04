"""Diagnostics data models: pipeline stages, step results, findings, sessions."""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Optional

STATUS_PASS = "PASS"
STATUS_WARN = "WARNING"
STATUS_FAIL = "FAIL"
STATUS_UNTESTED = "NOT_TESTED"

# The visual pipeline shown in the UI (order matters)
PIPELINE_STAGES = [
    ("camera", "Camera"),
    ("network", "Network"),
    ("http", "HTTP Stream"),
    ("container", "Stream Container"),
    ("chunks", "Chunk Parser"),
    ("frames", "Video Frames"),
    ("decoder", "Decoder"),
    ("projection", "Projection"),
    ("viewer", "Viewer"),
    ("snapshot", "Snapshot"),
]


@dataclass
class StepResult:
    key: str
    label: str
    status: str = STATUS_UNTESTED
    detail: str = ""
    evidence: dict = field(default_factory=dict)
    duration_ms: float = 0.0
    ts: float = field(default_factory=time.time)
    skipped_reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Finding:
    id: str
    title: str
    confidence: str            # HIGH | MEDIUM | LOW
    evidence: list = field(default_factory=list)
    suggested_action: str = ""
    error_code: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class SessionRegistry:
    """Thread-safe in-memory registry of diagnostic sessions (last 20)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, dict] = {}
        self._order: deque = deque(maxlen=20)
        self._counter = 0
        self._running: Optional[str] = None

    def new_id(self) -> str:
        with self._lock:
            self._counter += 1
            return f"DG-{time.strftime('%Y%m%d')}-{self._counter:03d}"

    def start(self, sid: str) -> None:
        with self._lock:
            self._running = sid

    def finish(self, sid: str, payload: dict) -> None:
        with self._lock:
            self._sessions[sid] = payload
            self._order.append(sid)
            if self._running == sid:
                self._running = None

    def running_id(self) -> Optional[str]:
        with self._lock:
            return self._running

    def get(self, sid: str) -> Optional[dict]:
        with self._lock:
            return self._sessions.get(sid)

    def latest(self) -> Optional[dict]:
        with self._lock:
            if not self._order:
                return None
            return self._sessions.get(self._order[-1])

    def status(self) -> dict:
        with self._lock:
            return {
                "running": self._running,
                "count": len(self._order),
                "latest_id": self._order[-1] if self._order else None,
            }
