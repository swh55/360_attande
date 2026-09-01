"""Event store: persists timeline events and notifies the bus + WebSocket."""
from __future__ import annotations

import json
from typing import Any, Optional

from ..core import database as db
from ..core.bus import bus as event_bus


class EventStore:
    def create(
        self,
        type: str,
        message: str,
        severity: str = "info",
        confidence: Optional[float] = None,
        snapshot_path: Optional[str] = None,
        recording_id: Optional[int] = None,
        lon: Optional[float] = None,
        lat: Optional[float] = None,
        track_id: Optional[int] = None,
        person_id: Optional[int] = None,
        meta: Optional[dict[str, Any]] = None,
    ) -> dict:
        event_id = db.execute(
            """INSERT INTO events (ts, type, severity, confidence, message, snapshot_path,
               recording_id, lon, lat, track_id, person_id, meta)
               VALUES (datetime('now','localtime'), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (type, severity, confidence, message, snapshot_path, recording_id,
             lon, lat, track_id, person_id,
             json.dumps(meta) if meta else None),
        )
        row = db.query_one("SELECT * FROM events WHERE id=?", (event_id,)) or {}
        event_bus.publish("event", row)
        return row

    @staticmethod
    def list_events(limit: int = 100, offset: int = 0, type: str | None = None,
                    from_ts: str | None = None, to_ts: str | None = None) -> list[dict]:
        sql = "SELECT * FROM events WHERE 1=1"
        params: list[Any] = []
        if type:
            sql += " AND type=?"
            params.append(type)
        if from_ts:
            sql += " AND ts >= ?"
            params.append(from_ts)
        if to_ts:
            sql += " AND ts <= ?"
            params.append(to_ts)
        sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
        params += [limit, offset]
        return db.query(sql, params)

    @staticmethod
    def stats(range_hours: int = 24) -> dict:
        rows = db.query(
            """SELECT type, COUNT(*) AS n FROM events
               WHERE ts >= datetime('now','localtime', ?)
               GROUP BY type""",
            (f"-{int(range_hours)} hours",),
        )
        by_type = {r["type"]: r["n"] for r in rows}
        return {
            "range_hours": range_hours,
            "total": sum(by_type.values()),
            "by_type": by_type,
        }

    @staticmethod
    def today_counts() -> dict:
        row = db.query_one(
            """SELECT
                 (SELECT COUNT(*) FROM events WHERE date(ts)=date('now','localtime')) AS events,
                 (SELECT COUNT(*) FROM events WHERE date(ts)=date('now','localtime') AND type='person') AS persons,
                 (SELECT COUNT(*) FROM events WHERE date(ts)=date('now','localtime') AND type='face') AS faces,
                 (SELECT COUNT(*) FROM events WHERE date(ts)=date('now','localtime') AND type='recognition_known') AS known,
                 (SELECT COUNT(*) FROM events WHERE date(ts)=date('now','localtime') AND type='recognition_unknown') AS unknown"""
        ) or {}
        return row

    @staticmethod
    def timeline(days: int = 1) -> list[dict]:
        rows = db.query(
            """SELECT * FROM events
               WHERE ts >= datetime('now','localtime', ?)
               ORDER BY ts ASC""",
            (f"-{int(days)} days",),
        )
        groups: dict[str, list[dict]] = {}
        for r in rows:
            day = str(r["ts"])[:10]
            hour = str(r["ts"])[11:13]
            groups.setdefault(f"{day} {hour}:00", []).append(r)
        return [
            {"group": g, "events": evs}
            for g, evs in sorted(groups.items(), reverse=False)
        ]
