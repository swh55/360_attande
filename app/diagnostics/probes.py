"""Network / HTTP probes for the diagnostics engine.

These probes NEVER touch the live pipeline: they open their own short-lived
connections with hard timeouts. ICMP is attempted opportunistically; TCP
handshake latency is the authoritative "ping" (ICMP often needs admin rights,
e.g. on Windows).
"""
from __future__ import annotations

import os
import socket
import struct
import time
from typing import Optional

import requests


def tcp_ping(host: str, port: int, timeout: float = 3.0) -> dict:
    t0 = time.time()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return {"ok": True, "latency_ms": round((time.time() - t0) * 1000, 1),
                    "host": host, "port": port, "error": ""}
    except OSError as exc:
        return {"ok": False, "latency_ms": None, "host": host, "port": port,
                "error": f"{type(exc).__name__}: {exc}"}


def _icmp_checksum(data: bytes) -> int:
    if len(data) % 2:
        data += b"\x00"
    total = 0
    for i in range(0, len(data), 2):
        total += (data[i] << 8) + data[i + 1]
    total = (total >> 16) + (total & 0xFFFF)
    total += total >> 16
    return (~total) & 0xFFFF


def icmp_ping(host: str, timeout: float = 2.0) -> dict:
    """Try a real ICMP echo; degrade gracefully to a documented TCP result."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP)
    except (PermissionError, OSError) as exc:
        return {"ok": False, "method": "icmp", "latency_ms": None,
                "note": f"ICMP unavailable ({exc.__class__.__name__}) — TCP ping is authoritative",
                "error": str(exc)}
    try:
        sock.settimeout(timeout)
        pid = os.getpid() & 0xFFFF
        seq = 1
        header = struct.pack("!BBHHH", 8, 0, 0, pid, seq)
        payload = b"gear360diag"
        packet = header + payload
        packet = packet[:2] + struct.pack("!H", _icmp_checksum(packet)) + packet[4:]
        t0 = time.time()
        sock.sendto(packet, (host, 0))
        data, _ = sock.recvfrom(2048)
        latency = round((time.time() - t0) * 1000, 1)
        return {"ok": True, "method": "icmp", "latency_ms": latency, "note": "", "error": ""}
    except (OSError, TimeoutError) as exc:
        return {"ok": False, "method": "icmp", "latency_ms": None,
                "note": "ICMP echo failed — host may block ICMP; TCP ping decides",
                "error": f"{type(exc).__name__}: {exc}"}
    finally:
        sock.close()


def http_probe(host: str, port: int, path: str, max_bytes: int = 262_144,
               timeout: float = 6.0, extra_headers: Optional[dict] = None) -> dict:
    """Open the live-preview endpoint, read up to max_bytes, measure everything."""
    url = f"http://{host}:{port}{path}"
    headers = {"User-Agent": "Gear360Hub-Diag/1.0"}
    headers.update(extra_headers or {})
    out: dict = {"url": url, "ok": False, "status": None, "headers": {},
                 "content_type": "", "bytes_read": 0, "elapsed_ms": None,
                 "bytes_per_sec": None, "first_bytes_hex": "", "error": ""}
    t0 = time.time()
    try:
        with requests.get(url, stream=True, timeout=(3, timeout), headers=headers) as r:
            out["status"] = r.status_code
            out["headers"] = dict(r.headers)
            out["content_type"] = r.headers.get("Content-Type", "")
            buf = bytearray()
            for chunk in r.iter_content(chunk_size=32 * 1024):
                if chunk:
                    buf.extend(chunk)
                if len(buf) >= max_bytes:
                    break
            out["bytes_read"] = len(buf)
            out["elapsed_ms"] = round((time.time() - t0) * 1000, 1)
            if out["elapsed_ms"]:
                out["bytes_per_sec"] = int(len(buf) / (out["elapsed_ms"] / 1000))
            out["first_bytes_hex"] = bytes(buf[:512]).hex(" ")
            # analysis window (hex-only; binary in the session would break JSON)
            out["head_hex"] = bytes(buf[:8192]).hex()
            # NOTE: never put raw `bytes` in the probe result — the runner
            # stores it in the JSON session (API + ZIP) and arbitrary binary
            # breaks jsonable_encoder. Hex strings only.
            out["ok"] = r.status_code in (200, 206) and len(buf) > 0
            if r.status_code not in (200, 206):
                out["error"] = f"unexpected HTTP status {r.status_code}"
            elif len(buf) == 0:
                # distinguish the failure mode precisely: headers arrived but the
                # body never started (read timeout on the first byte)
                out["error"] = ("HEADERS_WITHOUT_BODY: camera accepted the request and sent "
                                "headers, but streamed zero body bytes before the read window "
                                f"closed ({timeout:.0f}s)")
                out["stalled"] = True
    except Exception as exc:
        # requests wraps body read-timeouts into ConnectionError (exactly the
        # "ConnectionError: ... Read timed out." seen in real diagnostics), so
        # classification is by RESULT, not by exception type:
        out["error"] = f"{type(exc).__name__}: {exc}"
        out["elapsed_ms"] = round((time.time() - t0) * 1000, 1)
        if out.get("status") in (200, 206) and out.get("bytes_read", 0) == 0:
            out["stalled"] = True
            out["error"] = (f"HEADERS_WITHOUT_BODY (underlying: {type(exc).__name__}: {exc})")
    return out


# Request styles tried in order. Real Gear 360 units are known to answer
# Range requests with 206 + headers; if the body stalls we empirically test
# whether the request STYLE is the trigger before blaming the camera.
PROBE_VARIANTS = (
    ("range", {"Range": "bytes=0-"}),
    ("plain", {}),
    ("close", {"Range": "bytes=0-", "Connection": "close"}),
)


def http_probe_variants(host: str, port: int, path: str,
                        alt_path: str = "/livestream_low.avi",
                        max_bytes: int = 65_536, read_window: float = 4.0) -> dict:
    """Try several request styles/endpoints with short read windows.

    Returns a matrix so the diagnostics engine can distinguish:
      * one style streams while others stall  -> request-style sensitivity
      * all styles stall                      -> camera session wedged / single-
      * alt endpoint streams                  -> endpoint-specific issue
    """
    variants = [(name, dict(hdrs), path) for name, hdrs in PROBE_VARIANTS]
    if alt_path and alt_path != path:
        variants.append((f"range{alt_path}", {"Range": "bytes=0-"}, alt_path))
    results = []
    for name, hdrs, vpath in variants:
        res = http_probe(host, port, vpath, max_bytes=max_bytes,
                         timeout=read_window, extra_headers=hdrs)
        res["variant"] = name
        res["path"] = vpath
        res["request_headers"] = hdrs
        results.append(res)
    any_data = any(r.get("bytes_read", 0) > 0 for r in results)
    return {
        "variants": results,
        "any_data": any_data,
        "all_stalled": (not any_data) and
                       all(r.get("status") in (200, 206) for r in results),
        "best": max(results, key=lambda r: r.get("bytes_read", 0)),
    }
