"""Attendance module: optional, on top of recognition. In/out per person per day.

Prevents duplicates with a cooldown; each transition is linked to the person,
the triggering event and a snapshot when available.
"""
from __future__ import annotations

import time
from datetime import datetime

from ..core import database as db
from ..core.logging_setup import get

LOG = get("events")


class AttendanceService:
    def __init__(self, cooldown_min: int = 5):
        self.cooldown_min = cooldown_min
        self._last: dict[int, tuple[str, float]] = {}  # person_id -> (kind, ts)

    def process(self, person_id: int, person_name: str) -> dict | None:
        """Register recognition; returns dict describing an in/out transition
        (or None if inside cooldown)."""
        now = time.time()
        last_kind, last_ts = self._last.get(person_id, ("", 0.0))
        if last_kind and now - last_ts < self.cooldown_min * 60:
            return None

        day = datetime.now().strftime("%Y-%m-%d")
        kind: str
        if last_kind == "in":
            kind = "out"
        else:
            kind = "in"

        row = db.query_one(
            "SELECT id, check_in, check_out FROM attendance WHERE person_id=? AND day=?",
            (person_id, day),
        )
        now_iso = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        if row is None:
            db.execute(
                "INSERT INTO attendance (person_id, day, check_in, check_out, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (person_id, day, now_iso if kind == "in" else None,
                 now_iso if kind == "out" else None, now_iso),
            )
        elif kind == "in":
            db.execute(
                "UPDATE attendance SET check_in=COALESCE(check_in, ?), updated_at=? WHERE id=?",
                (now_iso, now_iso, row["id"]),
            )
        else:
            db.execute(
                "UPDATE attendance SET check_out=?, updated_at=? WHERE id=?",
                (now_iso, now_iso, row["id"]),
            )
        self._last[person_id] = (kind, now)
        LOG.info("Attendance %s for %s (person %s)", kind, person_name, person_id)
        return {"kind": kind, "person_id": person_id, "person_name": person_name, "at": now_iso}

    @staticmethod
    def summary(day: str | None = None) -> list[dict]:
        day = day or datetime.now().strftime("%Y-%m-%d")
        return db.query(
            """SELECT a.id, a.person_id, p.name, p.person_code, p.department,
                      a.day, a.check_in, a.check_out
               FROM attendance a JOIN people p ON p.id = a.person_id
               WHERE a.day = ? ORDER BY a.check_in""",
            (day,),
        )
