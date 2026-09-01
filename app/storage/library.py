"""Media library: recordings/photos queries, favorites, stats, prune."""
from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..core import database as db
from ..core.paths import DIRS


def list_recordings(limit: int = 100, offset: int = 0,
                    favorite: Optional[bool] = None) -> list[dict]:
    sql = "SELECT * FROM recordings WHERE 1=1"
    params: list = []
    if favorite is not None:
        sql += " AND favorite=?"
        params.append(1 if favorite else 0)
    sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params += [limit, offset]
    rows = db.query(sql, params)
    for r in rows:
        r["exists"] = Path(r["path"]).exists() if r.get("path") else False
    return rows


def list_photos(limit: int = 100, offset: int = 0, projection: Optional[str] = None,
                favorite: Optional[bool] = None) -> list[dict]:
    sql = "SELECT * FROM photos WHERE 1=1"
    params: list = []
    if projection:
        sql += " AND projection=?"
        params.append(projection)
    if favorite is not None:
        sql += " AND favorite=?"
        params.append(1 if favorite else 0)
    sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params += [limit, offset]
    rows = db.query(sql, params)
    for r in rows:
        r["exists"] = Path(r["path"]).exists() if r.get("path") else False
    return rows


def stats() -> dict:
    today = datetime.now().strftime("%Y-%m-%d")
    rec = db.query_one(
        """SELECT COUNT(*) AS n, COALESCE(SUM(size_bytes),0) AS bytes FROM recordings
           WHERE date(created_at)=?""", (today,)) or {}
    ph = db.query_one(
        """SELECT COUNT(*) AS n, COALESCE(SUM(size_bytes),0) AS bytes FROM photos
           WHERE date(created_at)=?""", (today,)) or {}
    ev = db.query_one(
        "SELECT COUNT(*) AS n FROM events WHERE date(ts)=?", (today,)) or {}
    total_rec = db.query_one(
        "SELECT COUNT(*) AS n, COALESCE(SUM(size_bytes),0) AS bytes FROM recordings") or {}
    total_ph = db.query_one(
        "SELECT COUNT(*) AS n, COALESCE(SUM(size_bytes),0) AS bytes FROM photos") or {}
    disk = shutil.disk_usage(str(DIRS["base"]))
    return {
        "today": {
            "videos": rec.get("n", 0), "videos_bytes": rec.get("bytes", 0),
            "photos": ph.get("n", 0), "photos_bytes": ph.get("bytes", 0),
            "events": ev.get("n", 0),
            "storage_bytes": (rec.get("bytes", 0) or 0) + (ph.get("bytes", 0) or 0),
        },
        "total": {
            "videos": total_rec.get("n", 0), "videos_bytes": total_rec.get("bytes", 0),
            "photos": total_ph.get("n", 0), "photos_bytes": total_ph.get("bytes", 0),
            "storage_bytes": (total_rec.get("bytes", 0) or 0) + (total_ph.get("bytes", 0) or 0),
        },
        "disk": {"free_bytes": disk.free, "total_bytes": disk.total},
    }


def toggle_favorite(kind: str, item_id: int) -> bool:
    table = {"recording": "recordings", "photo": "photos"}[kind]
    row = db.query_one(f"SELECT favorite FROM {table} WHERE id=?", (item_id,))
    if not row:
        raise KeyError(f"{kind} {item_id} not found")
    new_val = 0 if row["favorite"] else 1
    db.execute(f"UPDATE {table} SET favorite=? WHERE id=?", (new_val, item_id))
    return bool(new_val)


def delete_media(kind: str, item_id: int) -> bool:
    table = {"recording": "recordings", "photo": "photos"}[kind]
    row = db.query_one(f"SELECT * FROM {table} WHERE id=?", (item_id,))
    if not row:
        return False
    for key in ("path", "thumb_path"):
        p = row.get(key)
        if p:
            try:
                Path(p).unlink(missing_ok=True)
            except OSError:
                pass
    db.execute(f"DELETE FROM {table} WHERE id=?", (item_id,))
    return True


def prune(max_age_days: int) -> int:
    cutoff = datetime.now().strftime("%Y-%m-%d")
    n = 0
    for kind in ("recording", "photo"):
        table = {"recording": "recordings", "photo": "photos"}[kind]
        col = "created_at"
        rows = db.query(
            f"SELECT id, path, thumb_path FROM {table} WHERE date({col}) < date('now','localtime', ?)",
            (f"-{int(max_age_days)} days",),
        )
        for row in rows:
            if delete_media(kind, row["id"]):
                n += 1
    return n
