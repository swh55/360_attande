"""SQLite access layer with schema versioning (migrations). Thread-safe, WAL mode."""
from __future__ import annotations

import sqlite3
import threading
import time
from typing import Any, Iterable

from .paths import db_path
from .logging_setup import get

LOG = get("app")
_lock = threading.RLock()
_conn: sqlite3.Connection | None = None

SCHEMA_VERSION = 1

_MIGRATIONS: dict[int, list[str]] = {
    1: [
        """CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS cameras (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
            profile TEXT NOT NULL DEFAULT 'gear360_2017',
            host TEXT NOT NULL, port INTEGER NOT NULL, stream_path TEXT NOT NULL,
            is_default INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS recordings (
            id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT UNIQUE NOT NULL,
            thumb_path TEXT, filename TEXT NOT NULL, created_at TEXT NOT NULL,
            duration_sec REAL NOT NULL DEFAULT 0, size_bytes INTEGER NOT NULL DEFAULT 0,
            frames INTEGER NOT NULL DEFAULT 0, width INTEGER, height INTEGER, fps REAL,
            is_360 INTEGER NOT NULL DEFAULT 1, favorite INTEGER NOT NULL DEFAULT 0,
            event_id INTEGER, note TEXT)""",
        """CREATE TABLE IF NOT EXISTS photos (
            id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT UNIQUE NOT NULL,
            thumb_path TEXT, filename TEXT NOT NULL, created_at TEXT NOT NULL,
            size_bytes INTEGER NOT NULL DEFAULT 0, width INTEGER, height INTEGER,
            is_360 INTEGER NOT NULL DEFAULT 1, projection TEXT DEFAULT 'raw',
            favorite INTEGER NOT NULL DEFAULT 0, event_id INTEGER, note TEXT)""",
        """CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,
            type TEXT NOT NULL, severity TEXT NOT NULL DEFAULT 'info',
            confidence REAL, message TEXT NOT NULL, snapshot_path TEXT,
            recording_id INTEGER, lon REAL, lat REAL, track_id INTEGER,
            person_id INTEGER, meta TEXT)""",
        """CREATE TABLE IF NOT EXISTS people (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
            person_code TEXT, department TEXT, notes TEXT, photo_path TEXT,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS face_embeddings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
            embedding BLOB NOT NULL, dim INTEGER NOT NULL, source TEXT,
            created_at TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS virtual_cameras (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
            mode TEXT NOT NULL DEFAULT 'perspective',
            yaw REAL NOT NULL DEFAULT 0, pitch REAL NOT NULL DEFAULT 0,
            fov REAL NOT NULL DEFAULT 90, created_at TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS ai_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT,
            stopped_at TEXT, mode TEXT, frames_processed INTEGER NOT NULL DEFAULT 0,
            detections INTEGER NOT NULL DEFAULT 0, meta TEXT)""",
        """CREATE TABLE IF NOT EXISTS system_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,
            level TEXT NOT NULL, domain TEXT, message TEXT NOT NULL, meta TEXT)""",
        """CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
            day TEXT NOT NULL, check_in TEXT, check_out TEXT, updated_at TEXT,
            UNIQUE(person_id, day))""",
        """CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts DESC)""",
        """CREATE INDEX IF NOT EXISTS idx_events_type ON events(type)""",
        """CREATE INDEX IF NOT EXISTS idx_recordings_created ON recordings(created_at DESC)""",
        """CREATE INDEX IF NOT EXISTS idx_photos_created ON photos(created_at DESC)""",
    ],
}


def connect() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            _conn = sqlite3.connect(db_path(), check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.execute("PRAGMA foreign_keys=ON")
            _conn.execute("PRAGMA busy_timeout=5000")
        return _conn


def close() -> None:
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
            _conn = None


def query(sql: str, params: Iterable[Any] = ()) -> list[dict]:
    with _lock:
        cur = connect().execute(sql, tuple(params))
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
        return rows


def query_one(sql: str, params: Iterable[Any] = ()) -> dict | None:
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params: Iterable[Any] = ()) -> int:
    """Execute single statement, return lastrowid."""
    with _lock:
        conn = connect()
        cur = conn.execute(sql, tuple(params))
        conn.commit()
        rid = cur.lastrowid
        cur.close()
        return int(rid or 0)


def executemany(sql: str, seq: Iterable[tuple]) -> None:
    with _lock:
        conn = connect()
        conn.executemany(sql, seq)
        conn.commit()


def migrate() -> int:
    """Apply pending migrations in order. Returns current schema version."""
    conn = connect()
    with _lock:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        applied = {r["version"] for r in query("SELECT version FROM schema_version")}
        for version in sorted(_MIGRATIONS):
            if version in applied:
                continue
            LOG.info("Applying DB migration v%s", version)
            for stmt in _MIGRATIONS[version]:
                conn.execute(stmt)
            conn.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                (version, _now()),
            )
            conn.commit()
        row = query_one("SELECT MAX(version) AS v FROM schema_version")
        return int(row["v"] or 0) if row else SCHEMA_VERSION


def schema_version() -> int:
    try:
        row = query_one("SELECT MAX(version) AS v FROM schema_version")
        return int(row["v"] or 0) if row else 0
    except sqlite3.OperationalError:
        return 0


def db_size_bytes() -> int:
    try:
        return db_path().stat().st_size
    except OSError:
        return 0


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
