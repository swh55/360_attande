#!/usr/bin/env python3
"""PHASE 3 — Camera-side inspection (inv-2.0, channel: 8888 execute-CGI).

History: inv-1.0 used telnet only; on the user's unit (evidence 20260904
115759) every command answered "Password:"/"Login incorrect" and ZERO
commands executed — verdict LISTENER_UNCONFIRMED was meaningless. The same
run's PHASE 4 (camera_control_probe) PROVED that the mod's web shell on
port 8888 executes arbitrary commands:

    GET http://<cam>:8888/cgi-bin/execute?rawcmd=<url-encoded cmd>
    → 200 + <pre>Executing:  ...\n<output></pre>

This version therefore runs its READ-ONLY inspection through the execute
CGI (primary) and keeps telnet as a fallback. Never kills/restarts anything
here — the invasive re-arm experiment lives in session_lifecycle_probe.py.

Answers (task §12-15, §33-35):
  * who LISTENS on 7679 (port 0x1DFF in /proc/net/tcp → pid via fd map)
  * is the streamer a STOCK firmware service (mod never touches 7679, E9)
  * does the stream serve INSIDE the camera (localhost:7679 wget self-test)
    — the decisive 'wedge is external or internal' discriminator
"""
from __future__ import annotations

import argparse
import re
import socket
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from g360_common import ensure_dirs, fail, hr, info, ok, save_evidence, warn

STREAM_PORT = 7679
STREAM_PORT_HEX = "1DFF"

# read-only inspection commands (busybox-safe)
CMDS = {
    "ps": "ps",
    "proc_net_tcp": "cat /proc/net/tcp",
    "who_owns_7679": (
        "for p in /proc/[0-9]*; do for fd in $p/fd/*; do "
        "read lnk < $fd 2>/dev/null; "
        'case "$lnk" in *socket:*) ino=${lnk#socket:[}; ino=${ino%]}; '
        "grep -q \" $ino \" /proc/net/tcp 2>/dev/null && "
        "echo \"$p $(cat $p/cmdline 2>/dev/null | tr '\\0' ' ')\";; esac; "
        "done; done 2>/dev/null | head -40"
    ),
    "streamer_search": (
        "ps | grep -iE 'stream|video|live|camera|avi|preview|dnd|httpd' | grep -v grep"
    ),
    "init_check": "ls /etc/init.d/rcS 2>/dev/null; ls /etc/inittab 2>/dev/null",
}
INTERNAL_WGET = (
    "wget -q -O /tmp/g360_selftest.bin --header='Range: bytes=0-' "
    "--timeout=6 http://127.0.0.1:7679/livestream_high.avi; "
    "ls -l /tmp/g360_selftest.bin; "
    "head -c 48 /tmp/g360_selftest.bin | od -A x -t x1z | head -3; "
    "rm -f /tmp/g360_selftest.bin"
)


def execute_cgi(host: str, command: str, timeout: float = 15.0) -> dict:
    """Run one command through the mod's 8888 execute CGI. Returns raw text."""
    url = (f"http://{host}:8888/cgi-bin/execute?rawcmd="
           + urllib.parse.quote(command, safe=""))
    out: dict = {"command": command, "url": url}
    t0 = time.time()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
        out["http_status"] = getattr(r, "status", 200)
    except Exception as exc:
        out["error"] = f"{exc.__class__.__name__}: {exc}"
        out["elapsed_ms"] = round((time.time() - t0) * 1000)
        return out
    out["elapsed_ms"] = round((time.time() - t0) * 1000)
    # extract the <pre>…</pre> payload the CGI returns
    m = re.search(r"<pre>(.*?)</pre>", body, re.S)
    text = m.group(1) if m else body
    # drop the CGI's own 'Executing: …' echo line
    text = re.sub(r"^Executing:[^\n]*\n?", "", text, count=1)
    out["output"] = text.strip()
    out["output_len"] = len(out["output"])
    return out


def telnet_fallback(host: str, command: str, timeout: float = 8.0) -> dict:
    """Legacy channel — kept because some units run telnet without auth."""
    out: dict = {"command": command, "channel": "telnet"}
    try:
        with socket.create_connection((host, 23), timeout=timeout) as sock:
            sock.settimeout(timeout)
            buf = bytearray()
            try:
                while True:
                    data = sock.recv(8192)
                    if not data:
                        break
                    buf.extend(data)
                    if b"Password" in buf or b"login" in buf.lower() or b"# " in buf:
                        break
            except socket.timeout:
                pass
            sock.sendall(command.encode("ascii") + b"\r\n")
            time.sleep(1.0)
            buf.clear()
            try:
                while True:
                    data = sock.recv(8192)
                    if not data:
                        break
                    buf.extend(data)
            except socket.timeout:
                pass
            text = bytes(buf).decode("ascii", "replace")
            if "Password" in text or "Login incorrect" in text:
                out["error"] = "auth_required (telnet)"
            else:
                out["output"] = text.strip()
    except Exception as exc:
        out["error"] = f"{exc.__class__.__name__}: {exc}"
    return out


def parse_7679_rows(proc_tcp: str) -> dict:
    """Socket rows on the stream port, grouped by TCP state."""
    states = {0x01: "ESTABLISHED", 0x02: "SYN_SENT", 0x03: "SYN_RECV",
              0x04: "FIN_WAIT1", 0x05: "FIN_WAIT2", 0x06: "TIME_WAIT",
              0x08: "CLOSE_WAIT", 0x0A: "LISTEN"}
    rows: list[dict] = []
    for line in proc_tcp.splitlines():
        parts = line.split()
        if len(parts) < 10 or ":" not in parts[1]:
            continue
        try:
            port = int(parts[1].split(":")[1], 16)
            state = int(parts[3], 16)
        except ValueError:
            continue
        if port == STREAM_PORT:
            rows.append({"local": parts[1], "remote": parts[2],
                         "state": states.get(state, hex(state)),
                         "inode": parts[9] if len(parts) > 9 else "?"})
    return {
        "rows": rows,
        "listen": [r for r in rows if r["state"] == "LISTEN"],
        "established": [r for r in rows if r["state"] == "ESTABLISHED"],
        "lingering": [r for r in rows if r["state"] in ("CLOSE_WAIT", "TIME_WAIT")],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Camera-side inspection (execute-CGI primary)")
    ap.add_argument("--host", default="192.168.43.1")
    ap.add_argument("--skip-internal-wget", action="store_true",
                    help="skip the localhost:7679 self-test")
    args = ap.parse_args()
    ensure_dirs()

    hr(f"PHASE 3 — CAMERA-SIDE INSPECTION via 8888 execute-CGI ({args.host})")
    ev: dict = {"host": args.host, "channel": "execute-cgi", "commands": {}}

    # 0) channel availability check
    ping = execute_cgi(args.host, "echo g360-inspect-ok", timeout=8)
    ev["channel_probe"] = {k: ping.get(k) for k in ("http_status", "output", "error")}
    channel_ok = ping.get("output") == "g360-inspect-ok"
    if channel_ok:
        ok("execute CGI channel alive (echo returned)")
    else:
        warn(f"execute CGI channel degraded: {ping.get('error') or ping.get('output')!r} "
             "— will still try the inspection commands")

    # 1) read-only inspection
    for name, cmd in CMDS.items():
        r = execute_cgi(args.host, cmd)
        ev["commands"][name] = r
        if r.get("error"):
            fail(f"{name}: {r['error']}")
            r_fb = telnet_fallback(args.host, cmd)
            ev["commands"][name + "_telnet_fallback"] = r_fb
            if r_fb.get("output"):
                r["output"] = r_fb["output"]
                warn(f"{name}: recovered via telnet fallback")
            else:
                continue
        head = "\n".join(r.get("output", "").splitlines()[:6])
        info(f"{name}: {len(r.get('output', ''))} bytes")
        for line in head.splitlines():
            info("    " + line[:150])

    # 2) socket-state analysis for the stream port
    proc_tcp = ev["commands"].get("proc_net_tcp", {}).get("output", "")
    sockets = parse_7679_rows(proc_tcp)
    ev["stream_sockets"] = sockets
    hr(f"SOCKETS ON :{STREAM_PORT} (from /proc/net/tcp)")
    if not sockets["rows"] and proc_tcp:
        warn(f"no socket rows on :{STREAM_PORT} (hex {STREAM_PORT_HEX}) — "
             "streamer not listening at all")
    for row in sockets["rows"][:12]:
        info(f"    {row['state']:<12} local={row['local']} remote={row['remote']} "
             f"inode={row['inode']}")
    if sockets["listen"]:
        ok(f"streamer LISTEN confirmed ({len(sockets['listen'])} socket)")
    if sockets["established"]:
        warn(f"{len(sockets['established'])} ESTABLISHED stream socket(s) — "
             "a client currently holds the single session")
    if sockets["lingering"]:
        warn(f"{len(sockets['lingering'])} lingering socket(s) "
             f"(CLOSE_WAIT/TIME_WAIT: {[r['state'] for r in sockets['lingering']]}) "
             "— dead session may still be pinned")

    # 3) who owns the listener
    who = ev["commands"].get("who_owns_7679", {}).get("output", "")
    ps_out = ev["commands"].get("ps", {}).get("output", "")
    ev["streamer_candidates"] = [
        line.strip() for line in (who + "\n" + ps_out).splitlines()
        if re.search(r"stream|video|preview|dnd|gear|avi", line, re.I)][:10]
    if ev["streamer_candidates"]:
        hr("STREAMER PROCESS CANDIDATES")
        for line in ev["streamer_candidates"]:
            info("    " + line[:160])

    # 4) the decisive internal self-test: does the streamer serve INSIDE?
    if not args.skip_internal_wget:
        hr("INTERNAL STREAM SELF-TEST (localhost:7679 from inside the camera)")
        r = execute_cgi(args.host, INTERNAL_WGET, timeout=20)
        ev["internal_stream_test"] = r
        m = re.search(r"g360_selftest\.bin\s+(\d+)", r.get("output", ""))
        if m:
            n = int(m.group(1))
            ev["internal_stream_bytes"] = n
            if n > 1024:
                ok(f"INTERNAL stream delivered {n} bytes to localhost → the streamer "
                   "CAN serve; an external wedge is a session/arming state, not a dead service")
            else:
                fail(f"internal wget got only {n} bytes → the streamer itself is wedged "
                     "internally (process alive, producer dead)")
        else:
            warn(f"could not parse internal wget result — raw saved. "
                 f"({(r.get('output') or r.get('error') or '')[:200]})")

    # 5) verdict
    hr("VERDICT")
    if sockets["listen"] and ev.get("internal_stream_bytes", 0) > 1024:
        ev["verdict"] = "LISTENER_CONFIRMED_INTERNAL_ALIVE"
        ok("listener + internal delivery confirmed — the wedge is SESSION state "
           "(arming), not a dead service → session_lifecycle_probe.py decides re-arm")
        ret = 0
    elif sockets["listen"]:
        ev["verdict"] = "LISTENER_CONFIRMED_INTERNAL_STALLED"
        fail("listener exists but internal delivery stalled — streamer producer wedged")
        ret = 1
    else:
        ev["verdict"] = "LISTENER_UNCONFIRMED"
        warn("listener not confirmable from inside (command output insufficient)")
        ret = 1
    save_evidence("camera_shell_inspect", ev)
    return ret


if __name__ == "__main__":
    sys.exit(main())
