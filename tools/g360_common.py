"""Shared foundation for the independent Gear 360 investigation tools.

Rules (task doc §3/§46/§49):
  * NO imports from the Hub application (app.*) — every tool is standalone.
  * Tools that touch port 7679 MUST pass the session guard: when the Hub
    holds a live session (session.lock file or Hub API says connected),
    the tool refuses (or requires --override) — the camera's stream server
    is single-session and a second client can steal it.
  * Every tool writes machine-readable evidence JSON into
    diagnostics/raw/evidence/ so the Failure Tree can be updated from data.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import time
from pathlib import Path
from typing import Optional

TOOL_VERSION = "inv-1.0"

DEFAULT_HOST = os.environ.get("G360_HOST", "192.168.43.1")
DEFAULT_PORT = int(os.environ.get("G360_PORT", "7679"))
DEFAULT_PATH = os.environ.get("G360_PATH", "/livestream_high.avi")
HUB_API = os.environ.get("G360_HUB_API", "http://127.0.0.1:8765")

REPO_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = REPO_ROOT / "diagnostics" / "raw"
EVIDENCE_DIR = RAW_DIR / "evidence"
EXTRACTED_DIR = RAW_DIR / "extracted"
LOCK_FILE = RAW_DIR / "session.lock"


# ---------------------------------------------------------------- output
def hr(title: str = "") -> None:
    print("\n" + "=" * 72)
    if title:
        print(f"  {title}")
        print("=" * 72)


def info(msg: str) -> None:
    print(f"  {msg}")


def ok(msg: str) -> None:
    print(f"  [PASS] {msg}")


def fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")


def warn(msg: str) -> None:
    print(f"  [WARN] {msg}")


def verdict(msg: str) -> None:
    print("\n  >>> VERDICT: " + msg)


# ---------------------------------------------------------------- dirs
def ensure_dirs() -> None:
    for d in (RAW_DIR, EVIDENCE_DIR, EXTRACTED_DIR):
        d.mkdir(parents=True, exist_ok=True)
    # keep big binaries out of git, keep evidence JSONs in
    gi = RAW_DIR / ".gitignore"
    if not gi.exists():
        gi.write_text("*.bin\n*.avi\n*.h264\n*.hevc\n*.jpg\n*.jpeg\n*.png\nphotos/\nextracted/\n", encoding="utf-8")


def save_evidence(tool: str, data: dict) -> Path:
    """Persist one tool's evidence JSON (committed to git — small, textual)."""
    ensure_dirs()
    data.setdefault("tool", tool)
    data.setdefault("tool_version", TOOL_VERSION)
    data.setdefault("ts", time.strftime("%Y-%m-%dT%H:%M:%S"))
    path = EVIDENCE_DIR / f"{time.strftime('%Y%m%d_%H%M%S')}_{tool}.json"
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    info(f"evidence → {path.relative_to(REPO_ROOT)}")
    return path


# ---------------------------------------------------------------- session lock
def session_lock_status(timeout: float = 0.8) -> dict:
    """Who currently owns the camera's single stream session?

    Checks (in order):
      1. session.lock file (written by the Hub whenever a live session exists)
      2. Hub API /api/camera/status (works when tools run on the same PC)
    Never opens a connection to the camera itself.
    """
    status = {"lock_file": False, "lock_detail": None, "hub_state": None,
              "hub_reachable": None, "live_session": False}
    try:
        if LOCK_FILE.exists():
            raw = LOCK_FILE.read_text(encoding="utf-8")
            age = time.time() - LOCK_FILE.stat().st_mtime
            status["lock_file"] = age < 15  # hub refreshes it every ~5s
            status["lock_detail"] = {"age_s": round(age, 1),
                                     "payload": raw[:200] if raw else ""}
    except OSError:
        pass
    try:
        import urllib.request
        with urllib.request.urlopen(HUB_API + "/api/camera/status", timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
        status["hub_state"] = data.get("state")
        status["hub_reachable"] = True
    except Exception:
        status["hub_reachable"] = False
    status["live_session"] = bool(status["lock_file"]) or status["hub_state"] in (
        "connecting", "streaming", "reconnecting", "error")
    return status


def guard_stream_access(override: bool, tool: str) -> dict:
    """Refuse to touch 7679 while another session holds it (task §49/§51).

    Returns the lock status; raises SystemExit when blocked.
    """
    st = session_lock_status()
    if st["live_session"] and not override:
        hr("SESSION LOCK ACTIVE")
        fail(f"{tool} would open a SECOND stream session — the camera serves ONE.")
        info(f"lock_file={st['lock_file']} {st['lock_detail'] or ''}")
        info(f"hub_state={st['hub_state']}")
        info("Stop the Hub live session (or close the phone app), then re-run.")
        info("Explicit override: pass --override  (you take responsibility for")
        info("the two clients competing for the single stream session).")
        raise SystemExit(2)
    if st["live_session"]:
        warn("OVERRIDE: proceeding with a second stream client at user request")
    return st


# ---------------------------------------------------------------- args
def base_parser(desc: str, stream_touch: bool = True) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=desc)
    p.add_argument("--host", default=DEFAULT_HOST, help=f"camera IP (default {DEFAULT_HOST})")
    p.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"stream port (default {DEFAULT_PORT})")
    p.add_argument("--path", default=DEFAULT_PATH, help=f"stream path (default {DEFAULT_PATH})")
    if stream_touch:
        p.add_argument("--override", action="store_true",
                       help="open the stream even when a live session is active (dangerous)")
    return p


# ---------------------------------------------------------------- helpers
def tcp_connect(host: str, port: int, timeout: float = 3.0) -> tuple[socket.socket, float]:
    """Connect and return (sock, connect_ms)."""
    t0 = time.perf_counter()
    sock = socket.create_connection((host, port), timeout=timeout)
    ms = (time.perf_counter() - t0) * 1000
    sock.settimeout(timeout)
    return sock, round(ms, 2)


def read_with_timeline(sock: socket.socket, timeline: list, stop_check=None,
                       max_bytes: Optional[int] = None,
                       max_seconds: Optional[float] = None) -> dict:
    """Read a socket, appending (t_rel_ms, bytes_this_read, cumulative) rows.

    Returns stats: total, first_byte_ms, last_byte_ms, gaps(list of >1s holes),
    closed(bool), milestones dict.
    """
    t0 = time.perf_counter()
    total = 0
    first_ms = None
    last_ms = None
    last_arrival = t0
    gaps: list[dict] = []
    closed = False
    milestones = {}
    for target in (8 * 1024, 64 * 1024, 100 * 1024, 512 * 1024, 1024 * 1024,
                   2 * 1024 * 1024, 5 * 1024 * 1024, 10 * 1024 * 1024):
        milestones[target] = None
    while True:
        if stop_check is not None and stop_check():
            break
        if max_bytes is not None and total >= max_bytes:
            break
        if max_seconds is not None and (time.perf_counter() - t0) >= max_seconds:
            break
        try:
            chunk = sock.recv(65536)
        except socket.timeout:
            gaps.append({"at_ms": round((time.perf_counter() - t0) * 1000),
                         "kind": "recv_timeout"})
            break
        except (ConnectionResetError, OSError) as exc:
            gaps.append({"at_ms": round((time.perf_counter() - t0) * 1000),
                         "kind": f"{exc.__class__.__name__}: {exc}"})
            closed = True
            break
        if not chunk:
            closed = True
            break
        now = time.perf_counter()
        rel_ms = round((now - t0) * 1000)
        if first_ms is None:
            first_ms = rel_ms
        idle = now - last_arrival
        if idle > 1.0 and total > 0:
            gaps.append({"at_ms": rel_ms, "kind": "gap", "seconds": round(idle, 2)})
        last_arrival = now
        last_ms = rel_ms
        total += len(chunk)
        timeline.append([rel_ms, len(chunk), total])
        for target, hit in milestones.items():
            if hit is None and total >= target:
                milestones[target] = rel_ms
    return {"total": total, "first_byte_ms": first_ms, "last_byte_ms": last_ms,
            "gaps": gaps[:80], "closed": closed, "milestones_ms": milestones,
            "duration_s": round((time.perf_counter() - t0), 2)}


def read_headers_from_socket(sock: socket.socket, max_header: int = 32768) -> tuple[int, dict, bytes]:
    """Read one HTTP response head from a raw socket.

    Returns (status, headers dict (lower-case keys), leftover bytes read past
    the header end). Raises ValueError when no complete header arrives.
    """
    buf = b""
    while b"\r\n\r\n" not in buf:
        if len(buf) > max_header:
            raise ValueError(f"HTTP header exceeded {max_header}B without CRLFCRLF")
        chunk = sock.recv(4096)
        if not chunk:
            raise ValueError("connection closed before response header completed")
        buf += chunk
    head, _, rest = buf.partition(b"\r\n\r\n")
    lines = head.decode("latin-1", "replace").split("\r\n")
    status = 0
    try:
        status = int(lines[0].split()[1])
    except (IndexError, ValueError):
        pass
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            k, _, v = line.partition(":")
            headers[k.strip().lower()] = v.strip()
    return status, headers, rest


def fmt_bytes(n: Optional[float]) -> str:
    if n is None:
        return "-"
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024.0
    return f"{n:.1f} GB"
