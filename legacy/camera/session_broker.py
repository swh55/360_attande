"""CameraSessionBroker: the ONE authoritative guard for the camera's single
stream session (investigation task §49-§51).

The SM-R210 streaming server serves exactly ONE client. Every component that
opens a connection to port 7679 — Live, Diagnostics, Snapshot, AI, recording,
external tools — must be able to ASK whether a session is active and must
never open a second one silently.

Two coordination channels:
  * in-process: a module singleton with a threading lock (the Hub's own
    CameraManager acquires/releases here);
  * cross-process: diagnostics/raw/session.lock — refreshed every state-loop
    tick while a session exists, so the INDEPENDENT tools (tools/*.py) can
    detect an active live session without talking to the Hub API (see
    g360_common.session_lock_status).

The lock file is a HEARTBEAT (mtime age < 15 s = alive): a crashed Hub
cannot leave a stale lock that blocks field tools forever.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Optional

from ..core.logging_setup import get

LOG = get("camera.broker")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
LOCK_PATH = BASE_DIR / "diagnostics" / "raw" / "session.lock"
LOCK_TTL = 15.0  # seconds — heartbeat freshness window


class SessionBroker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._owner: Optional[str] = None      # which component holds the session
        self._acquired_at: float = 0.0
        self._pid = os.getpid()

    # ---- in-process --------------------------------------------------------
    def acquire(self, owner: str) -> bool:
        """Claim the camera's single stream session for `owner`."""
        with self._lock:
            if self._owner is not None:
                LOG.warning("session already held by %r — refusing %r",
                            self._owner, owner)
                return False
            self._owner = owner
            self._acquired_at = time.time()
            self._write_lock()
            LOG.info("SESSION acquired by %r", owner)
            return True

    def release(self, owner: str) -> None:
        with self._lock:
            if self._owner == owner:
                self._owner = None
                self._remove_lock()
                LOG.info("SESSION released by %r", owner)
            elif self._owner is not None:
                LOG.warning("release(%r) ignored — held by %r", owner, self._owner)

    def heartbeat(self, owner: str) -> None:
        """Refresh the lock-file mtime while the session is alive."""
        with self._lock:
            if self._owner == owner:
                self._write_lock()

    def status(self) -> dict:
        with self._lock:
            owner = self._owner
            acquired = self._acquired_at
        file_alive, age = self._file_alive()
        return {
            "in_process_owner": owner,
            "held_since": (round(time.time() - acquired, 1) if acquired and owner else None),
            "lock_file": str(LOCK_PATH),
            "lock_file_alive": file_alive,
            "lock_age_s": age,
            "pid": self._pid,
        }

    # ---- cross-process -----------------------------------------------------
    def _write_lock(self) -> None:
        try:
            LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "pid": self._pid,
                "owner": self._owner,
                "acquired_at": round(self._acquired_at, 3),
                "refreshed_at": round(time.time(), 3),
                "note": "camera single-stream session held by Gear360 Hub",
            }
            tmp = LOCK_PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload), encoding="utf-8")
            os.replace(tmp, LOCK_PATH)  # atomic on POSIX + Windows
        except OSError as exc:
            LOG.warning("cannot write session lock: %s", exc)

    def _remove_lock(self) -> None:
        try:
            LOCK_PATH.unlink(missing_ok=True)
        except OSError:
            pass

    @staticmethod
    def _file_alive() -> tuple[bool, Optional[float]]:
        try:
            age = time.time() - LOCK_PATH.stat().st_mtime
            return age < LOCK_TTL, round(age, 2)
        except OSError:
            return False, None


_broker: Optional[SessionBroker] = None
_broker_lock = threading.Lock()


def get_broker() -> SessionBroker:
    global _broker
    with _broker_lock:
        if _broker is None:
            _broker = SessionBroker()
        return _broker
