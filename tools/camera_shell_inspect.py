#!/usr/bin/env python3
"""PHASE 3 — Camera-side inspection via the mod's Telnet shell (§12-15, §33-35).

Runs READ-ONLY commands inside the camera (never kills/restarts anything):
  ps, netstat -ln, ifconfig, mount, df, free, top -n1
  → who LISTENS on 7679 (netstat/proc/net/tcp → /proc/<pid>/cmdline+exe)
  → does the stream serve correctly from INSIDE (localhost:7679 via wget)

Answers task §13 (who owns 7679), §14 (stock service vs mod — the mod never
touches 7679, so the listener is STOCK firmware), §15 (internal stream test).
"""
from __future__ import annotations

import re
import socket
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from g360_common import ensure_dirs, fail, hr, info, ok, save_evidence, warn

_IAC = 0xFF


def telnet_session(host: str, port: int, commands: list[str], timeout: float = 6.0) -> dict:
    """Open ONE telnet session and run several commands (avoids reconnect churn)."""
    results: dict = {}
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(timeout)

        def recv_until_idle(idle: float = 0.8, budget: float = 6.0) -> bytes:
            buf = bytearray()
            t0 = time.time()
            sock.settimeout(idle)
            while time.time() - t0 < budget:
                try:
                    data = sock.recv(8192)
                except socket.timeout:
                    if buf:
                        return bytes(buf)
                    return bytes(buf)
                except OSError:
                    break
                if not data:
                    break
                buf.extend(data)
            return bytes(buf)

        banner = recv_until_idle()
        # answer telnet negotiation: WONT/DONT to everything
        out = bytearray()
        i = 0
        while i < len(banner):
            b = banner[i]
            if b == _IAC and i + 2 < len(banner):
                cmd, opt = banner[i + 1], banner[i + 2]
                if cmd in (0xFD, 0xFB):
                    out += bytes([_IAC, 0xFE if cmd == 0xFD else 0xFC, opt])
                i += 3
                continue
            i += 1
        if out:
            sock.sendall(bytes(out))
        time.sleep(0.2)
        for cmd in commands:
            sock.sendall(cmd.encode("ascii") + b"\r\n")
            time.sleep(0.3)
            resp = recv_until_idle(idle=1.0 if len(cmd) < 30 else 2.5)
            results[cmd] = resp.decode("ascii", "replace")
            info(f"ran: {cmd}")
    return results


def parse_7679_inodes(proc_tcp: str) -> list[str]:
    """Extract socket inodes of LISTEN sockets on port 7679 (0x1E07) from
    /proc/net/tcp text (local_address like 0100A8C0:1E07, st=0A listen)."""
    inodes = []
    for line in proc_tcp.splitlines():
        parts = line.split()
        if len(parts) < 10:
            continue
        local, st, inode = parts[1], parts[3], parts[9]
        if ":" not in local:
            continue
        try:
            port = int(local.split(":")[1], 16)
            state = int(st, 16)
        except ValueError:
            continue
        if port == 7679 and state == 0x0A:  # LISTEN
            inodes.append(inode)
    return inodes


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Camera-side inspection over Telnet (read-only)")
    ap.add_argument("--host", default="192.168.43.1")
    ap.add_argument("--port", type=int, default=23)
    args = ap.parse_args()
    ensure_dirs()

    hr(f"PHASE 3 — CAMERA-SIDE INSPECTION via telnet {args.host}:{args.port}")
    ev: dict = {"host": args.host}

    cmds = [
        "ps",
        "netstat -ltnp 2>/dev/null || netstat -ltn",
        "cat /proc/net/tcp",
        "ifconfig",
        "mount",
        "df",
        "free",
        "top -n 1 | head -25",
        # targeted process search (§12)
        "ps | grep -iE 'stream|video|live|camera|avi|wifi|http|preview' | grep -v grep",
        # §33-34: identify the 7679 listener binary + arguments
        ("for p in /proc/[0-9]*; do ls -l $p/fd 2>/dev/null | grep -q socket && "
         "cat $p/cmdline 2>/dev/null | tr '\\0' ' ' | grep -qE '.' && echo \"$p: $(cat $p/cmdline | tr '\\0' ' ')\"; done | head -60"),
    ]
    try:
        results = telnet_session(args.host, args.port, cmds)
    except Exception as exc:
        fail(f"telnet session failed: {exc.__class__.__name__}: {exc}")
        ev["error"] = str(exc)
        save_evidence("camera_shell_inspect", ev)
        return 1

    ev["commands"] = results

    # --- analysis: who listens on 7679? -------------------------------------
    proc_tcp = results.get("cat /proc/net/tcp", "")
    listen_inodes = parse_7679_inodes(proc_tcp)
    ev["listen_inodes_7679"] = listen_inodes
    netstat = results.get("netstat -ltnp 2>/dev/null || netstat -ltn", "")
    ns_7679 = [l for l in netstat.splitlines() if ":7679" in l or ":1E07" in l.upper()]
    ev["netstat_7679_lines"] = ns_7679

    hr("WHO LISTENS ON 7679 (inside the camera)")
    if ns_7679:
        for l in ns_7679:
            info("  " + l.strip())
    else:
        warn("7679 LISTEN not visible in netstat output")
    # try to map inodes → pid
    ps_text = results.get("ps", "")
    ev["ps_lines"] = ps_text.splitlines()[:120]
    if ps_text:
        stream_rows = [l for l in ps_text.splitlines()
                       if re.search(r"stream|video|live|camera|avi|preview|server", l, re.I)]
        if stream_rows:
            info("streaming-related processes (from ps):")
            for l in stream_rows[:15]:
                info("  " + l.strip())

    # --- §15: internal stream test (localhost:7679 from inside the camera) --
    hr("INTERNAL STREAM TEST (camera serves itself on localhost:7679)")
    wget_cmd = ("wget -q -O /tmp/g360_selftest.bin --header='Range: bytes=0-' "
                "--timeout=6 http://127.0.0.1:7679/livestream_high.avi; "
                "ls -l /tmp/g360_selftest.bin; head -c 64 /tmp/g360_selftest.bin | od -A x -t x1z | head -4; "
                "rm -f /tmp/g360_selftest.bin")
    try:
        selftest = telnet_session(args.host, args.port, [wget_cmd], timeout=12.0)
        ev["internal_stream_test"] = selftest.get(wget_cmd, "")
        out_txt = ev["internal_stream_test"]
        m = re.search(r"/tmp/g360_selftest\.bin\s+(\d+)", out_txt)
        if m:
            n = int(m.group(1))
            if n > 1024:
                ok(f"INTERNAL stream delivered {n} bytes to localhost → "
                   "camera-side stream service is ALIVE (problem is not the service)")
                ev["internal_stream_bytes"] = n
            else:
                fail(f"internal wget got only {n} bytes → camera-side streamer is the suspect")
                ev["internal_stream_bytes"] = n
        else:
            warn("could not parse internal wget result (busybox wget may lack --header; "
                 "raw output saved in evidence)")
    except Exception as exc:
        warn(f"internal stream test failed: {exc}")

    hr("VERDICT")
    if listen_inodes or ns_7679:
        ok("7679 listener confirmed inside the camera (stock service — mod never touches 7679)")
        ev["verdict"] = "LISTENER_CONFIRMED"
        ret = 0
    else:
        warn("could not confirm 7679 listener from inside — save evidence for manual review")
        ev["verdict"] = "LISTENER_UNCONFIRMED"
        ret = 1
    save_evidence("camera_shell_inspect", ev)
    return ret


if __name__ == "__main__":
    sys.exit(main())
