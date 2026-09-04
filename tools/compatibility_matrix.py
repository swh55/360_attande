#!/usr/bin/env python3
"""§39-40 — Camera Compatibility Matrix + Known-Good Baseline.

Fills diagnostics/raw/baseline.json with what is PROVEN to work right now:
  ping / TCP 7679 / HTTP 206+body / HTTP 8888 / FTP 21 / Telnet 23
Optional: --photo adds a real Take Photo proof.
This file is the reference every later investigation step compares against.
"""
from __future__ import annotations

import json
import socket
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from g360_common import RAW_DIR, ensure_dirs, fail, hr, info, ok, save_evidence, verdict

sys.path.insert(0, str(REPO_ROOT / "tools"))


def tcp(host: str, port: int, timeout: float = 2.5) -> dict:
    try:
        t0 = time.time()
        with socket.create_connection((host, port), timeout=timeout):
            return {"ok": True, "ms": round((time.time() - t0) * 1000, 1)}
    except OSError as exc:
        return {"ok": False, "error": str(exc)}


def http_body(host: str, port: int, path: str, cap: int = 65536,
              extra: dict | None = None) -> dict:
    try:
        import requests
        h = {"User-Agent": "g360-matrix/1.0", "Range": "bytes=0-", **(extra or {})}
        t0 = time.time()
        with requests.get(f"http://{host}:{port}{path}", stream=True,
                          timeout=(3, 5), headers=h) as r:
            n = 0
            for chunk in r.iter_content(32768):
                n += len(chunk)
                if n >= cap:
                    break
        return {"ok": n > 0, "status": r.status_code,
                "content_type": r.headers.get("Content-Type", ""),
                "bytes": n, "ms": round((time.time() - t0) * 1000)}
    except Exception as exc:
        return {"ok": False, "error": f"{exc.__class__.__name__}: {exc}"}


def icmp(host: str, timeout: float = 1.5) -> dict:
    """Minimal ICMP echo (needs admin on Windows — degrades gracefully)."""
    try:
        import struct as _s
        import socket as _s2
        pid = _s2.getpid() & 0xFFFF
        hdr = _s.pack("!BBHHH", 8, 0, 0, pid, 1)
        data = b"g360probe"
        chk = _s.pack("!H", (~sum(_s.unpack("!%dH" % (len(hdr + data) // 2), hdr + data)) & 0xFFFF))
        pkt = hdr[:2] + chk + hdr[4:] + data
        s = _s2.socket(_s2.AF_INET, _s2.SOCK_RAW, _s2.IPPROTO_ICMP)
        s.settimeout(timeout)
        t0 = time.time()
        s.sendto(pkt, (host, 0))
        s.recvfrom(2048)
        return {"ok": True, "ms": round((time.time() - t0) * 1000, 1)}
    except PermissionError:
        return {"ok": False, "note": "ICMP needs admin — skipped (TCP is decisive)"}
    except OSError as exc:
        return {"ok": False, "error": str(exc)}


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Camera compatibility matrix + baseline")
    ap.add_argument("--host", default="192.168.43.1")
    ap.add_argument("--photo", action="store_true")
    args = ap.parse_args()
    ensure_dirs()
    host = args.host
    hr(f"§39-40 — COMPATIBILITY MATRIX  ({host})")

    m: dict = {"firmware": "R210GLU0ARB2", "model": "SM-R210", "ts": time.strftime("%F %T")}
    m["icmp_ping"] = icmp(host)
    m["tcp_7679"] = tcp(host, 7679)
    m["tcp_8888"] = tcp(host, 8888)
    m["tcp_21"] = tcp(host, 21)
    m["tcp_23"] = tcp(host, 23)
    m["stream_http"] = http_body(host, 7679, "/livestream_high.avi", cap=262144)
    m["mod_http_root"] = http_body(host, 8888, "/", cap=8192) if m["tcp_8888"]["ok"] else {"ok": False, "note": "port closed"}

    rows = [
        ("Wi-Fi reachable (TCP 7679 SYN)", m["tcp_7679"]["ok"]),
        ("Stream HTTP + body (>=64KB)", m["stream_http"].get("bytes", 0) >= 65536),
        ("HTTP 8888 (mod web)", m["tcp_8888"]["ok"]),
        ("FTP 21", m["tcp_21"]["ok"]),
        ("Telnet 23", m["tcp_23"]["ok"]),
    ]
    if args.photo:
        import subprocess
        r = subprocess.run([sys.executable, str(REPO_ROOT / "tools" / "camera_control_probe.py"),
                            "--host", host, "--photo"], capture_output=True, text=True, timeout=120)
        m["take_photo"] = {"returncode": r.returncode,
                           "tail": (r.stdout + r.stderr)[-800:]}
        rows.append(("Take Photo (real shutter)", r.returncode == 0))

    hr("MATRIX")
    for label, good in rows:
        (ok if good else fail)(label)
    info(f"stream probe detail: {m['stream_http']}")

    baseline_path = RAW_DIR / "baseline.json"
    existing = {}
    if baseline_path.exists():
        try:
            existing = json.loads(baseline_path.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
    existing[time.strftime("%Y-%m-%d %H:%M")] = m
    baseline_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    info(f"baseline → {baseline_path}")

    all_core = all(g for _, g in rows[:2])
    verdict("KNOWN-GOOD BASELINE SAVED — stream endpoint proven with body"
            if all_core else "BASELINE SAVED — stream endpoint NOT proven with body "
            "(this is the decisive gap; run raw_camera_probe.py next)")
    save_evidence("compatibility_matrix", {"matrix": m})
    return 0 if all_core else 1


if __name__ == "__main__":
    sys.exit(main())
