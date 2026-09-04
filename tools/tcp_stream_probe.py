#!/usr/bin/env python3
"""PHASE 1b — TCP-level stream probe (task doc §6).

Measures with raw sockets, no HTTP library involved:
  * TCP connect time
  * request-sent → first response byte (TTFB)
  * first body byte after headers
  * body throughput sampled every 100 ms (timestamp, bytes)
  * inter-packet timing (idle detection)
  * connection close / reset detection
"""
from __future__ import annotations

import sys
import socket
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from g360_common import (base_parser, ensure_dirs, fail, fmt_bytes,
                         guard_stream_access, hr, info, ok, save_evidence,
                         tcp_connect, verdict, warn)


def main() -> int:
    ap = base_parser("TCP-level timing probe of the live stream")
    ap.add_argument("--seconds", type=float, default=8.0)
    args = ap.parse_args()

    ensure_dirs()
    hr(f"PHASE 1b — TCP STREAM PROBE  ({args.host}:{args.port})")
    lock = guard_stream_access(args.override, "tcp_stream_probe")

    ev: dict = {"host": args.host, "port": args.port, "lock_status": lock}
    samples: list = []          # (rel_ms, bytes_this_sample, cumulative)

    try:
        sock, connect_ms = tcp_connect(args.host, args.port, timeout=4.0)
        ok(f"1. TCP connect            : {connect_ms} ms")
        ev["tcp_connect_ms"] = connect_ms

        req = (f"GET {args.path} HTTP/1.1\r\nHost: {args.host}:{args.port}\r\n"
               f"Range: bytes=0-\r\nConnection: keep-alive\r\n\r\n")
        t0 = time.perf_counter()
        sock.sendall(req.encode("ascii"))
        info("2. HTTP request sent")

        # first response byte
        sock.settimeout(8.0)
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = sock.recv(4096)
            if not chunk:
                raise ConnectionError("closed before headers completed")
            buf += chunk
        ttfb_ms = (time.perf_counter() - t0) * 1000
        ok(f"3. first response byte    : {ttfb_ms:.1f} ms (headers complete)")
        ev["ttfb_ms"] = round(ttfb_ms, 1)
        status_line = buf.split(b"\r\n", 1)[0].decode("latin-1", "replace")
        info(f"   {status_line}")
        ev["status_line"] = status_line

        head, _, rest = buf.partition(b"\r\n\r\n")
        headers = {}
        for line in head.decode("latin-1", "replace").split("\r\n")[1:]:
            if ":" in line:
                k, _, v = line.partition(":")
                headers[k.strip().lower()] = v.strip()
        ev["headers"] = headers
        cl = headers.get("content-length")
        te = headers.get("transfer-encoding")
        cr = headers.get("content-range")
        info(f"   content-length={cl} transfer-encoding={te} content-range={cr}")

        # body phase with 100 ms sampling
        t1 = time.perf_counter()
        cum = len(rest)
        last_sample_t = t1
        last_sample_b = cum
        first_body_ms = None
        idle_events = []
        closed = False
        if rest:
            first_body_ms = 0.0
            samples.append([0, len(rest), cum])
        while (time.perf_counter() - t1) < args.seconds:
            sock.settimeout(1.0)
            try:
                chunk = sock.recv(65536)
            except socket.timeout:
                now = time.perf_counter()
                idle_events.append({"at_ms": round((now - t1) * 1000),
                                    "idle_s": 1.0})
                continue
            except OSError as exc:
                closed = True
                warn(f"socket error: {exc}")
                break
            if not chunk:
                closed = True
                warn("connection closed (FIN) by camera")
                break
            now = time.perf_counter()
            if first_body_ms is None:
                first_body_ms = (now - t1) * 1000
                ok(f"4. first body byte        : +{first_body_ms:.1f} ms after headers")
            cum += len(chunk)
            if now - last_sample_t >= 0.1:
                samples.append([round((now - t1) * 1000),
                                cum - last_sample_b, cum])
                last_sample_t = now
                last_sample_b = cum
        sock.close()
    except Exception as exc:
        fail(f"{exc.__class__.__name__}: {exc}")
        ev["error"] = f"{exc.__class__.__name__}: {exc}"
        save_evidence("tcp_stream_probe", ev)
        return 1

    dur = args.seconds
    ev.update({
        "first_body_ms": round(first_body_ms, 1) if first_body_ms is not None else None,
        "body_samples_100ms": samples[:400],
        "idle_events": idle_events[:40],
        "closed": closed,
        "bytes_total": cum,
        "avg_bps": round(cum / max(0.001, dur), 1),
    })

    hr("BODY THROUGHPUT (100 ms samples)")
    for s in samples[:30]:
        info(f"  t={s[0]:>7} ms   +{s[1]:>7} B   Σ {fmt_bytes(s[2])}")
    hr("RESULT")
    info(f"total body      : {fmt_bytes(cum)} in {dur:.0f}s")
    info(f"avg throughput  : {fmt_bytes(ev['avg_bps'])}/s")
    info(f"idle >1s events : {len(idle_events)}")
    info(f"closed early    : {closed}")
    if cum > 2 * 1024 * 1024:
        ok("healthy continuous body flow")
        ev["verdict"] = "CONTINUOUS"
    elif cum > 65536:
        warn("flowed past 64 KiB but under 2 MB in the window — verify with longer run")
        ev["verdict"] = "PARTIAL"
    elif cum > 0:
        fail("body stalled early — TCP-level evidence of camera-side stall")
        ev["verdict"] = f"STALLED_AT_{cum}"
    else:
        fail("no body bytes at all")
        ev["verdict"] = "HEADERS_WITHOUT_BODY"
    verdict(str(ev["verdict"]))
    save_evidence("tcp_stream_probe", ev)
    return 0 if ev["verdict"] == "CONTINUOUS" else 1


if __name__ == "__main__":
    sys.exit(main())
