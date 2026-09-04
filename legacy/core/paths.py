"""Central filesystem layout for Gear360 Hub. All dirs are created on demand."""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent


def ensure_dirs() -> dict[str, Path]:
    dirs = {
        "base": BASE_DIR,
        "config": BASE_DIR / "config",
        "data": BASE_DIR / "data",
        "recordings": BASE_DIR / "recordings",
        "photos": BASE_DIR / "photos",
        "thumbs": BASE_DIR / "photos" / ".thumbs",
        "logs": BASE_DIR / "logs",
        "runtime": BASE_DIR / "runtime",
        "ffmpeg": BASE_DIR / "runtime" / "ffmpeg",
        "debug": BASE_DIR / "data" / "debug",
    }
    for p in dirs.values():
        p.mkdir(parents=True, exist_ok=True)
    return dirs


def db_path() -> Path:
    env = os.environ.get("GEAR360HUB_DB")
    if env:
        return Path(env)
    return BASE_DIR / "data" / "gear360hub.db"


DIRS = ensure_dirs()
