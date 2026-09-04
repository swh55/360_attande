#!/usr/bin/env python3
"""PHASE 2b — HTTP request-variant matrix with ISOLATION (task doc §8 + §9).

Design flaw this tool corrects: the Hub's http_probe_variants() ran request
styles SEQUENTIALLY on the camera's SINGLE-SESSION server. Once the first
(range) request wedged the stream session, later styles stalled REGARDLESS
of their own merits — the "only Range works" conclusion may be an ordering
artifact. This tool tests each style in its OWN connection with a settle
gap between runs, and supports reversed/repeated orders to prove whether a
style itself is the trigger.

Variants:
  A  plain GET                      (the legacy Android app's request shape)
  B  Range: bytes=0-
  C  Range: bytes=0-1048575
  D  Range + Connection: keep-alive
  E  Range + Connection: close

All response headers are recorded literally (Content-Range, Content-Length,
Transfer-Encoding, Connection, Accept-Ranges, DLNA.*, Server, ...).
"""
from __future__ import annotations

import sys
import socket
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from g360_common import (base_parser, ensure_dirs, fail, fmt_bytes,
                         guard_stream_access, hr, info, ok, read_headers_from_socket,
                         save_evidence, tcp_connect, verdict, warn)

VARIANTS = {
    "A_plain_get":     {},
    "B_range_open":    {"Range": "bytes=0-"},
    "C_range_1m":      {"Range": "bytes=0-1048575"},
    "D_range_ka":      {"Range": "bytes=0-", "Connection": "keep-alive"},
    "E_range_close":   {"Range": "bytes=0-", "Connection": "close"},
}
# interesting headers recorded verbatim (task §9)
WATCH_HEADERS = ("content-range", "content-length", "transfer-encoding",
                 "connection", "accept-ranges", "cache-control", "server",
                 "content-type", "date", "pragma", "dlna", "getcontentfeatures",
                 "friendlyname", "man", "mime-version", "ext")


def run_variant(name: str, host: str, port: int, path: str, extra: dict,
                read_seconds: float) -> dict:
    out: dict = {"variant": name, "extra_headers": extra}
    req_lines = [f"GET {path} HTTP/1.1", f"Host: {host}:{port}",
                 "User-Agent: g360-variant-probe", "Accept: */*"]
    for k, v in extra.items():
        req_lines.append(f"{k}: {v}")
    req = "\r\n".join(req_lines) + "\r\n\r\n"
    try:
        t0 = time.perf_counter()
        sock, connect_ms = tcp_connect(host, port, timeout=4.0)
        out["connect_ms"] = connect_ms
        sock.sendall(req.encode("ascii"))
        status, headers, rest = read_headers_from_socket(sock)
        out["status"] = status
        out["headers"] = {k: v for k, v in headers.items() if k in WATCH_HEADERS or "dlna" in k}
        out["all_headers"] = headers
        # read body for a bounded window
        cum = len(rest)
        out["first_read_bytes"] = len(rest)
        t1 = time.perf_counter()
        stalled = False
        while (time.perf_counter() - t1) < read_seconds and cum < 1_048_576:
            sock.settimeout(2.0)
            try:
                chunk = sock.recv(65536)
            except socket.timeout:
                stalled = True
                break
            except OSError as exc:
                out["read_error"] = str(exc)
                break
            if not chunk:
                out["closed_after_bytes"] = cum
                break
            cum += len(chunk)
        sock.close()
        out["bytes_read"] = cum
        out["elapsed_ms"] = round((time.perf_counter() - t0) * 1000)
        if cum > 0:
            out["result"] = "BODY_OK" if not stalled or cum > 65536 else "BODY_THEN_STALL"
        else:
            out["result"] = "HEADERS_ONLY" if status else "NO_RESPONSE"
    except Exception as exc:
        out["result"] = "ERROR"
        out["error"] = f"{exc.__class__.__name__}: {exc}"
    return out


def main() -> int:
    ap = base_parser("Isolated HTTP request-variant matrix")
    ap.add_argument("--read-seconds", type=float, default=4.0,
                    help="body read window per variant (default 4)")
    ap.add_argument("--settle", type=float, default=3.0,
                    help="camera settle gap BETWEEN variants (default 3s)")
    ap.add_argument("--order", default="A,B,C,D,E", help="comma list of variants")
    ap.add_argument("--repeat", type=int, default=1, help="full-matrix repetitions (default 1)")
    ap.add_argument("--skip-guard", action="store_true",
                    help="alias of --override for this probe (kept for clarity)")
    args = ap.parse_args()
    override = args.override or args.skip_guard

    ensure_dirs()
    hr(f"PHASE 2b — ISOLATED HTTP VARIANTS  order={args.order} repeat={args.repeat}")
    lock = guard_stream_access(override, "http_variants_probe")

    results: list = []
    order_list = [s.strip() for s in args.order.split(",") if s.strip() in VARIANTS]
    for rep in range(max(1, args.repeat)):
        for name in order_list:
            hr(f"variant {name}  (rep {rep + 1}/{args.repeat})")
            r = run_variant(name, args.host, args.port, args.path,
                            VARIANTS[name], args.read_seconds)
            info(f"  status={r.get('status')} bytes={fmt_bytes(r.get('bytes_read', 0))} "
                 f"→ {r['result']}")
            if r.get("headers"):
                for k, v in r["headers"].items():
                    info(f"    {k}: {v}")
            results.append({**r, "rep": rep + 1})
            if args.settle > 0:
                info(f"  … camera settle {args.settle}s (single-session release)")
                time.sleep(args.settle)

    by_variant: dict = {}
    for r in results:
        by_variant.setdefault(r["variant"], []).append(r["result"])

    ev = {"order": order_list, "repeat": args.repeat, "results": results,
          "summary": {k: v for k, v in by_variant.items()},
          "lock_status": lock}

    hr("MATRIX SUMMARY (consistency across repeats — the ordering-artifact check)")
    stable_body, stable_stall, inconsistent = [], [], []
    for name, outcomes in by_variant.items():
        all_ok = all(o == "BODY_OK" for o in outcomes)
        all_bad = all(o in ("HEADERS_ONLY", "BODY_THEN_STALL", "ERROR", "NO_RESPONSE")
                      for o in outcomes)
        if all_ok:
            stable_body.append(name)
            ok(f"{name:15s} → BODY in every run {outcomes}")
        elif all_bad:
            stable_stall.append(name)
            fail(f"{name:15s} → stalled in every run {outcomes}")
        else:
            inconsistent.append(name)
            warn(f"{name:15s} → INCONSISTENT {outcomes} (session-ordering effect!)")

    ev["conclusion"] = {
        "always_body": stable_body,
        "always_stall": stable_stall,
        "inconsistent": inconsistent,
        "ordering_artifact_suspected": bool(inconsistent),
    }
    if stable_body:
        verdict(f"body flows via {stable_body}" +
                (f"; stalled via {stable_stall}" if stable_stall else ""))
        ret = 0
    elif inconsistent:
        verdict("INCONSISTENT — variant success depends on session ORDER, "
                "not the request style itself (single-session server behaviour)")
        ret = 1
    else:
        verdict("NO style produced a body — camera-side streamer is not serving")
        ret = 1
    save_evidence("http_variants_probe", ev)
    return ret


if __name__ == "__main__":
    sys.exit(main())
