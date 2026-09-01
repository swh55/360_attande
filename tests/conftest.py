
"""Pytest fixtures: isolated DB + ffmpeg-generated H264 access units."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# isolate the DB per test session BEFORE app modules get imported
_TMPDIR = Path(__file__).resolve().parent / ".tmp"
_TMPDIR.mkdir(exist_ok=True)
os.environ["GEAR360HUB_DB"] = str(_TMPDIR / "session.db")


@pytest.fixture()
def tmp_db():
    """Fresh DB file per test (reconnects the module-level connection)."""
    from app.core import database as db

    db_path = _TMPDIR / f"t{os.getpid()}-{abs(hash(str(id(object())))) % 99999}.db"
    os.environ["GEAR360HUB_DB"] = str(db_path)
    db.close()
    db.migrate()
    yield db_path
    db.close()


@pytest.fixture(scope="session")
def real_h264_access_units():
    """Generate real H.264 access units with ffmpeg (golden decoder test)."""
    from tests.fixtures import _find_ffmpeg, annexb_to_access_units

    ffmpeg = _find_ffmpeg()
    if ffmpeg is None:
        pytest.skip("ffmpeg not available")
    import subprocess
    w, h, n = 160, 120, 12
    proc = subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", f"testsrc=size={w}x{h}:rate=10",
         "-frames:v", str(n), "-c:v", "libx264", "-preset", "ultrafast",
         "-x264-params", "keyint=5", "-f", "h264", "pipe:1"],
        capture_output=True,
    )
    assert proc.returncode == 0, proc.stderr[:300]
    aus = annexb_to_access_units(proc.stdout)
    assert len(aus) >= n * 0.5, f"only {len(aus)} AUs from {len(proc.stdout)}B"
    return {"aus": aus, "width": w, "height": h}
