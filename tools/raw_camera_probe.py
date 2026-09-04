#!/usr/bin/env python3
"""PHASE 1 — Raw independent camera probe (task doc §3 + §7).

Isolates the CAMERA from the Hub entirely: no CameraManager, no Hub, no
FrameBus, no FFmpeg, no parser. Just TCP + HTTP GET + raw byte counting.

Proves (or refutes):
  * the endpoint answers and a body actually flows
  * the body KEEPS flowing (100KB / 1MB / 5MB / 10MB …) or stops at N bytes
  * per-chunk arrival timestamps (gaps made visible)

Usage:
  python tools/raw_camera_probe.py                 # 10 s observation window
  python tools/raw_camera_probe.py --seconds 30
  python tools/raw_camera_probe.py --save-mb 0.25  # save first 256 KB
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from g360_common import (RAW_DIR, base_parser, ensure_dirs, fail,
                         fmt_bytes, guard_stream_access, hr, info, ok, read_headers_from_socket,
                         read_with_timeline, save_evidence, tcp_connect, verdict, warn)


def main() -> int:
    ap = base_parser("Independent raw probe of the Gear 360 live stream")
    ap.add_argument("--seconds", type=float, default=10.0,
                    help="observation window after first byte (default 10)")
    ap.add_argument("--save-mb", type=float, default=0.25,
                    help="save the first N MB of the stream (0 disables, default 0.25)")
    args = ap.parse_args()

    ensure_dirs()
    hr(f"PHASE 1 — RAW CAMERA PROBE  ({args.host}:{args.port}{args.path})")
    lock = guard_stream_access(args.override, "raw_camera_probe")

    url = f"http://{args.host}:{args.port}{args.path}"
    request = (f"GET {args.path} HTTP/1.1\r\n"
               f"Host: {args.host}:{args.port}\r\n"
               f"User-Agent: g360-raw-probe/{time.time():.0f}\r\n"
               f"Accept: */*\r\n"
               f"Range: bytes=0-\r\n"
               f"Connection: keep-alive\r\n\r\n")

    ev: dict = {"url": url, "request": request.strip(), "lock_status": lock,
                "seconds": args.seconds}
    timeline: list = []
    out_path = RAW_DIR / f"probe_{time.strftime('%Y%m%d_%H%M%S')}.bin"
    save_bytes = int(args.save_mb * 1024 * 1024)
    saved = 0
    fh = open(out_path, "wb") if save_bytes > 0 else None

    try:
        sock, connect_ms = tcp_connect(args.host, args.port)
        ok(f"TCP connected in {connect_ms} ms")
        ev["tcp_connect_ms"] = connect_ms

        sock.sendall(request.encode("ascii"))
        info("HTTP request sent (GET + Range: bytes=0-)")
        status, headers, rest = read_headers_from_socket(sock)
        ok(f"HTTP {status} — {len(headers)} headers")
        for k, v in headers.items():
            info(f"    {k}: {v}")
        ev["http_status"] = status
        ev["headers"] = headers

        # feed the leftover bytes (already-read body start) into the counters
        timeline.append([0, len(rest), len(rest)])
        stats = read_with_timeline(sock, timeline, stop_check=None,
                                   max_seconds=args.seconds)
        if fh is not None:
            fh.write(rest[:save_bytes])
            saved = len(rest[:save_bytes])
        sock.close()
    except Exception as exc:
        fail(f"{exc.__class__.__name__}: {exc}")
        ev["error"] = f"{exc.__class__.__name__}: {exc}"
        save_evidence("raw_camera_probe", ev)
        return 1
    finally:
        if fh is not None:
            fh.close()

    total = stats["total"] + len(rest)
    ev.update({
        "bytes_total": total,
        "first_byte_ms": stats["first_byte_ms"],
        "last_byte_ms": stats["last_byte_ms"],
        "gaps": stats["gaps"],
        "connection_closed": stats["closed"],
        "milestones_ms": {str(k): v for k, v in stats["milestones_ms"].items()},
        "timeline_sample": timeline[:400] + (["..."] if len(timeline) > 400 else []),
        "saved_bytes": saved,
        "saved_file": out_path.name if fh is not None else None,
        "avg_bytes_per_sec": round(total / max(0.001, stats["duration_s"]), 1),
    })

    hr("CONTINUITY TIMELINE (relative ms → cumulative bytes)")
    step = max(1, len(timeline) // 24)
    for row in timeline[::step]:
        if isinstance(row, list):
            info(f"  {row[0]:>8} ms   +{row[1]:>6} B   Σ {fmt_bytes(row[2])}")
    for target, ms in stats["milestones_ms"].items():
        label = fmt_bytes(int(target))
        if ms is not None:
            ok(f"{label} reached at {ms} ms")
        else:
            warn(f"{label} NOT reached")
    if stats["gaps"]:
        for g in stats["gaps"][:10]:
            warn(f"gap/interruption: {g}")
    else:
        ok("no >1s gaps observed")

    hr("ANALYSIS")
    if total == 0:
        fail("ZERO body bytes — HEADERS_WITHOUT_BODY (the camera never streamed)")
        ev["verdict"] = "HEADERS_WITHOUT_BODY"
    elif stats["closed"]:
        fail(f"connection CLOSED by the camera after {fmt_bytes(total)}")
        ev["verdict"] = f"CLOSED_AT_{total}"
    elif total < 65536:
        fail(f"only {fmt_bytes(total)} flowed in {args.seconds}s — stream effectively dead")
        ev["verdict"] = f"STALLED_AT_{total}"
    else:
        rate = ev["avg_bytes_per_sec"]
        ok(f"{fmt_bytes(total)} in {stats['duration_s']}s — avg {fmt_bytes(rate)}/s")
        ev["verdict"] = f"CONTINUOUS_{total}"

    # the decisive question the old diagnostic could NOT answer:
    if total > 65536:
        ok(f"stream flowed BEYOND 65536 B ({fmt_bytes(total)}) — the old "
           "\"65536\" reading was the DIAGNOSTIC PROBE CAP, not a camera stop")
        ev["beyond_probe_cap"] = True
    else:
        warn("stream did NOT exceed 65536 B — cannot yet exclude a real "
             "camera-side stall at 64 KiB (increase --seconds and re-run)")
        ev["beyond_probe_cap"] = False

    verdict(str(ev.get("verdict")))
    save_evidence("raw_camera_probe", ev)
    return 0 if str(ev.get("verdict", "")).startswith("CONTINUOUS") else 1


if __name__ == "__main__":
    sys.exit(main())
