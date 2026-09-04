#!/usr/bin/env python3
"""PHASE 2 — Independent raw stream dump (task doc §4).

Saves the REAL camera stream for a defined duration/size into
diagnostics/raw/ and reports exact receive statistics. This file is the
input for every OFFLINE phase (parser analysis, 00VD extraction, H264
validation, decode test) — the camera is not needed again afterwards.

Usage:
  python tools/raw_camera_stream_dump.py --seconds 10
  python tools/raw_camera_stream_dump.py --mb 5
"""
from __future__ import annotations

import json
import socket
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from g360_common import (RAW_DIR, base_parser, ensure_dirs, fail, fmt_bytes,
                         guard_stream_access, hr, info, ok, read_headers_from_socket,
                         save_evidence, tcp_connect, verdict, warn)


def main() -> int:
    ap = base_parser("Dump the real Gear 360 stream to diagnostics/raw/")
    ap.add_argument("--seconds", type=float, default=10.0, help="max capture seconds (default 10)")
    ap.add_argument("--mb", type=float, default=2.0, help="stop after N MB (default 2)")
    args = ap.parse_args()

    ensure_dirs()
    hr(f"PHASE 2 — RAW STREAM DUMP  target={args.mb} MB / {args.seconds}s max")
    lock = guard_stream_access(args.override, "raw_camera_stream_dump")

    request = (f"GET {args.path} HTTP/1.1\r\n"
               f"Host: {args.host}:{args.port}\r\n"
               f"User-Agent: g360-dump/{time.time():.0f}\r\n"
               f"Accept: */*\r\n"
               f"Range: bytes=0-\r\n"
               f"Connection: keep-alive\r\n\r\n")

    ts = time.strftime("%Y%m%d_%H%M%S")
    out_path = RAW_DIR / f"dump_{ts}.bin"
    meta_path = RAW_DIR / f"dump_{ts}.meta.json"
    timeline: list = []
    ev: dict = {"url": f"http://{args.host}:{args.port}{args.path}",
                "requested_mb": args.mb, "max_seconds": args.seconds,
                "lock_status": lock}
    target = int(args.mb * 1024 * 1024)
    total = 0
    status = None
    headers: dict = {}

    try:
        sock, connect_ms = tcp_connect(args.host, args.port)
        ev["tcp_connect_ms"] = connect_ms
        sock.sendall(request.encode("ascii"))
        status, headers, rest = read_headers_from_socket(sock)
        ev["http_status"] = status
        ev["headers"] = headers
        ok(f"HTTP {status} — dumping until {fmt_bytes(target)} or {args.seconds}s")
        with open(out_path, "wb") as fh:
            fh.write(rest[:target])
            total = len(rest[:target])
            timeline.append([0, total, total])
            deadline = time.time() + args.seconds
            stats = None
            while total < target and time.time() < deadline:
                remaining_s = deadline - time.time()
                sock.settimeout(min(5.0, max(0.5, remaining_s)))
                try:
                    chunk = sock.recv(65536)
                except (socket.timeout, OSError) as exc:
                    stats = {"total": total, "gaps": [{"kind": f"{exc.__class__.__name__}: {exc}"}],
                             "closed": not isinstance(exc, socket.timeout), "milestones_ms": {},
                             "duration_s": args.seconds - remaining_s}
                    break
                if not chunk:
                    stats = {"total": total, "gaps": [], "closed": True,
                             "milestones_ms": {}, "duration_s": args.seconds - remaining_s}
                    break
                take = chunk[: target - total]
                fh.write(take)
                total += len(take)
                timeline.append([round((time.time() - (deadline - args.seconds)) * 1000),
                                 len(take), total])
            sock.close()
        if stats is None:
            stats = {"total": total, "gaps": [], "closed": False,
                     "milestones_ms": {}, "duration_s": args.seconds}
    except Exception as exc:
        fail(f"{exc.__class__.__name__}: {exc}")
        ev["error"] = f"{exc.__class__.__name__}: {exc}"
        ev["bytes_written"] = total
        save_evidence("raw_camera_stream_dump", ev)
        return 1

    dur = max(0.001, stats.get("duration_s") or args.seconds)
    ev.update({
        "bytes_written": total,
        "duration_s": round(dur, 2),
        "avg_bytes_per_second": round(total / dur, 1),
        "gaps": stats.get("gaps", []),
        "connection_closed": stats.get("closed", False),
        "first_byte_time": timeline[0][0] if timeline else None,
        "last_byte_time": timeline[-1][0] if timeline else None,
        "timeline_buckets_100ms": _bucket_100ms(timeline),
        "file": out_path.name,
    })
    meta = {"camera": "SM-R210", "ip": args.host, "port": args.port,
            "endpoint": args.path, "ts": ts, "bytes": total,
            "http_status": status, "content_type": headers.get("content-type", ""),
            "avg_bps": ev["avg_bytes_per_second"]}
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    hr("RECEIVE PROFILE (100 ms buckets)")
    for bucket in ev["timeline_buckets_100ms"][:40]:
        info(f"  t={bucket['t_ms']:>7} ms   {bucket['bytes']:>7} B")
    hr("STATS")
    info(f"file            : {out_path}  ({fmt_bytes(total)})")
    info(f"duration        : {dur:.2f} s")
    info(f"average rate    : {fmt_bytes(ev['avg_bytes_per_second'])}/s")
    info(f"gaps            : {len(ev['gaps'])}")
    info(f"closed early    : {ev['connection_closed']}")

    if total >= target:
        ok(f"requested {fmt_bytes(target)} fully received — CONTINUOUS stream")
        ev["verdict"] = "CONTINUOUS"
    elif total > 65536:
        warn(f"{fmt_bytes(total)} received, then no more within the window — "
             f"stall AFTER the probe cap; not a 64 KiB stop")
        ev["verdict"] = f"PARTIAL_{total}"
    elif total > 0:
        fail(f"stalled at {total} B")
        ev["verdict"] = f"STALLED_AT_{total}"
    else:
        fail("zero bytes — headers without body")
        ev["verdict"] = "HEADERS_WITHOUT_BODY"

    verdict(str(ev["verdict"]))
    save_evidence("raw_camera_stream_dump", ev)
    return 0 if ev["verdict"] == "CONTINUOUS" else 1


def _bucket_100ms(timeline: list) -> list:
    """Collapse (ms, n, cum) rows into 100 ms buckets."""
    if not timeline:
        return []
    buckets: dict[int, int] = {}
    for ms, n, _cum in timeline:
        if isinstance(ms, str):
            continue
        buckets.setdefault(int(ms // 100) * 100, 0)
        buckets[int(ms // 100) * 100] += n
    return [{"t_ms": k, "bytes": v} for k, v in sorted(buckets.items())[:400]]


if __name__ == "__main__":
    sys.exit(main())
