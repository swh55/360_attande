"""Stream sample capture for offline analysis: saves a configurable-size slice
of the real camera stream plus JSON metadata, into data/diagnostics/captures."""
from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path

import requests

from ..core.paths import DIRS

CAPTURES_DIR = DIRS["data"] / "diagnostics" / "captures"
SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")
MAX_MB = 32.0


def _ensure_dir() -> Path:
    CAPTURES_DIR.mkdir(parents=True, exist_ok=True)
    return CAPTURES_DIR


def capture_sample(host: str, port: int, path: str, mb: float = 2.0,
                   camera: str = "SM-R210") -> dict:
    mb = max(0.1, min(MAX_MB, float(mb)))
    _ensure_dir()
    url = f"http://{host}:{port}{path}"
    ts = datetime.now()
    name = f"stream_{ts.strftime('%Y-%m-%d_%H-%M-%S')}.bin"
    out_path = CAPTURES_DIR / name
    meta = {
        "camera": camera, "ip": host, "port": port, "endpoint": path,
        "timestamp": ts.isoformat(timespec="seconds"),
        "requested_mb": mb,
    }
    result = {**meta, "name": name, "path": str(out_path), "http_status": None,
              "content_type": None, "bytes": 0, "error": ""}
    t0 = time.time()
    try:
        with requests.get(url, stream=True, timeout=(3, 8),
                          headers={"Range": "bytes=0-", "User-Agent": "Gear360Hub-Diag/1.0"}) as r:
            result["http_status"] = r.status_code
            result["content_type"] = r.headers.get("Content-Type")
            target = int(mb * 1024 * 1024)
            n = 0
            with open(out_path, "wb") as fh:
                for chunk in r.iter_content(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    take = chunk[: target - n]
                    fh.write(take)
                    n += len(take)
                    if n >= target:
                        break
            result["bytes"] = n
            result["elapsed_ms"] = round((time.time() - t0) * 1000)
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        if out_path.exists() and out_path.stat().st_size == 0:
            out_path.unlink(missing_ok=True)
            return result
    meta.update({"http_status": result["http_status"], "content_type": result["content_type"],
                 "bytes": result["bytes"]})
    if out_path.exists():
        (CAPTURES_DIR / (name + ".meta.json")).write_text(
            json.dumps(meta, indent=2), encoding="utf-8")
    return result


def list_captures() -> list[dict]:
    if not CAPTURES_DIR.exists():
        return []
    items = []
    for f in sorted(CAPTURES_DIR.glob("stream_*.bin"), reverse=True):
        meta_path = f.with_name(f.name + ".meta.json")
        meta = {}
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                meta = {}
        items.append({"name": f.name, "size": f.stat().st_size,
                      "mtime": f.stat().st_mtime, "meta": meta})
    return items


def capture_path(name: str) -> Path:
    if not SAFE_NAME.match(name or ""):
        raise ValueError("invalid capture name")
    return CAPTURES_DIR / name


def load_slice(name: str, offset: int = 0, length: int = 4096) -> bytes:
    p = capture_path(name)
    if not p.exists():
        raise FileNotFoundError(name)
    length = max(0, min(int(length), 4 * 1024 * 1024))
    with open(p, "rb") as fh:
        fh.seek(max(0, int(offset)))
        return fh.read(length)


def latest_capture() -> str | None:
    items = list_captures()
    return items[0]["name"] if items else None
