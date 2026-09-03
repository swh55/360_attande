"""Network / HTTP probes for the diagnostics engine.

These probes NEVER touch the live pipeline: they open their own short-lived
connections with hard timeouts. ICMP is attempted opportunistically; TCP
handshake latency is the authoritative "ping" (ICMP often needs admin rights,
e.g. on Windows).
"""
from __future__ import annotations

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
        pid = socket.getpid() & 0xFFFF
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
               timeout: float = 6.0) -> dict:
    """Open the live-preview endpoint, read up to max_bytes, measure everything."""
    url = f"http://{host}:{port}{path}"
    out: dict = {"url": url, "ok": False, "status": None, "headers": {},
                 "content_type": "", "bytes_read": 0, "elapsed_ms": None,
                 "bytes_per_sec": None, "first_bytes_hex": "", "error": ""}
    t0 = time.time()
    try:
        with requests.get(url, stream=True, timeout=(3, timeout),
                          headers={"Range": "bytes=0-", "User-Agent": "Gear360Hub-Diag/1.0"}) as r:
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
            out["first_bytes"] = bytes(buf[:4096])
            out["ok"] = r.status_code in (200, 206) and len(buf) > 0
            if r.status_code not in (200, 206):
                out["error"] = f"unexpected HTTP status {r.status_code}"
            elif len(buf) == 0:
                out["error"] = "HTTP connected but zero bytes received (camera closed the stream)"
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
        out["elapsed_ms"] = round((time.time() - t0) * 1000, 1)
    return out
