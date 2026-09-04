#!/usr/bin/env python3
"""SESSION LIFECYCLE PROBE (inv-2.0) — the decisive experiment for RC-2/F-A.

Proven by evidence 2026-09-04 115725/115729/115733 (run_investigation PHASE
1 vs 2 vs 2d, all with the IDENTICAL request bytes 'Range: bytes=0-' +
keep-alive):
  * client #1 after arming      → 9.2 MB continuous body  (streamer healthy)
  * client #2 after #1 closed   → 206 headers, ZERO body   (HEADERS_WITHOUT_BODY)
  * client #3 after that        → connect timeout / zero body
=> the SM-R210 streamer is SINGLE-SHOT per arming. What we still do NOT know
   — and what this probe proves step by step — is:

  ST0  baseline armed state (client A must receive a body, else ABORT)
  ST1  does the streamer self-heal when client #2 waits?  (+1s, +5s, +15s, +30s)
  ST2  concurrent clients: does client B STEAL the session from A
       ('newest wins' — the official app comment) or STARVE while A flows?
  ST3  which action RE-ARMS a wedged streamer? candidates:
         R0 control (wait only — is the wedge even persistent?)
         R1 GET http://cam:8888/          (touch the mod's web root)
         R2 execute CGI 'ps'              (any 8888 CGI activity)
         R3 execute CGI internal wget     (does the streamer serve INSIDE?
                                           also consumes an arming if it does)
         R4 kill streamer pid             (--apply only; init must respawn it)
       after each candidate: verification client C (body within its window?)

Every step appends machine-readable evidence to
diagnostics/raw/evidence/ and prints a live verdict line.

Rules (task §49-§51): refuses to run while a Hub live session holds the
camera (session.lock / Hub API) unless --override.
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
from g360_common import (base_parser, ensure_dirs, fail, fmt_bytes, guard_stream_access,
                         hr, info, ok, read_headers_from_socket, save_evidence,
                         tcp_connect, verdict, warn)

WATCH_STATES = {0x01: "ESTABLISHED", 0x06: "TIME_WAIT", 0x08: "CLOSE_WAIT", 0x0A: "LISTEN"}


# ------------------------------------------------------------------ client
def stream_client(host: str, port: int, path: str, read_seconds: float = 4.0,
                  label: str = "client") -> dict:
    """One full HTTP-range stream client. Returns classification + metrics."""
    out: dict = {"label": label, "read_seconds": read_seconds}
    req = (f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\n"
           f"User-Agent: g360-lifecycle/{int(time.time())}\r\nAccept: */*\r\n"
           f"Range: bytes=0-\r\nConnection: keep-alive\r\n\r\n")
    try:
        t0 = time.perf_counter()
        sock, connect_ms = tcp_connect(host, port, timeout=4.0)
        out["connect_ms"] = connect_ms
        sock.sendall(req.encode("ascii"))
        status, headers, rest = read_headers_from_socket(sock)
        out["status"] = status
        out["ttfb_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        cum = len(rest)
        out["bytes"] = cum
        t1 = time.perf_counter()
        first_body_ms = None
        stalled_at = None
        while (time.perf_counter() - t1) < read_seconds:
            sock.settimeout(2.0)
            try:
                chunk = sock.recv(65536)
            except socket.timeout:
                if cum > 0 and first_body_ms is not None:
                    stalled_at = round((time.perf_counter() - t1) * 1000)
                break
            except OSError as exc:
                out["read_error"] = str(exc)
                break
            if not chunk:
                out["closed_by_camera"] = True
                break
            if first_body_ms is None:
                first_body_ms = round((time.perf_counter() - t1) * 1000, 1)
            cum += len(chunk)
            if cum >= 8 * 1024 * 1024:
                break
        sock.close()
        out["bytes"] = cum
        out["first_body_ms"] = first_body_ms
        out["stalled_after_ms"] = stalled_at
        if cum > 64 * 1024:
            out["result"] = "BODY_OK"
        elif cum > 0:
            out["result"] = "BODY_THEN_STALL"
        else:
            out["result"] = "HEADERS_ONLY" if status else "NO_RESPONSE"
    except Exception as exc:
        out["result"] = "ERROR"
        out["error"] = f"{exc.__class__.__name__}: {exc}"
    info(f"  [{label}] {out.get('result')}  bytes={fmt_bytes(out.get('bytes', 0))} "
         f"ttfb={out.get('ttfb_ms', '—')}ms "
         f"first_body={out.get('first_body_ms', '—')}ms")
    return out


# ------------------------------------------------------------- camera-side
def camera_sockets(host: str, label: str) -> dict:
    """Snapshot /proc/net/tcp rows on the stream port via execute CGI."""
    out: dict = {"label": label, "rows": []}
    try:
        cmd = urllib.parse.quote("cat /proc/net/tcp", safe="")
        url = f"http://{host}:8888/cgi-bin/execute?rawcmd={cmd}"
        with urllib.request.urlopen(url, timeout=10) as r:
            body = r.read().decode("utf-8", "replace")
        m = re.search(r"<pre>(.*?)</pre>", body, re.S)
        text = m.group(1) if m else body
        for line in text.splitlines():
            parts = line.split()
            if len(parts) < 4 or ":" not in parts[1]:
                continue
            try:
                if int(parts[1].split(":")[1], 16) == 7679:
                    out["rows"].append({"local": parts[1], "remote": parts[2],
                                        "state": WATCH_STATES.get(int(parts[3], 16),
                                                                  hex(int(parts[3], 16)))})
            except (ValueError, IndexError):
                continue
    except Exception as exc:
        out["error"] = f"{exc.__class__.__name__}: {exc}"
    states = [r["state"] for r in out["rows"]]
    out["counts"] = {s: states.count(s) for s in set(states)}
    info(f"  [camera] :7679 sockets {out.get('counts', {})} {out.get('error', '')}")
    return out


def camera_exec(host: str, command: str, timeout: float = 20.0) -> dict:
    """One execute-CGI command → {'output': …} (raw, <pre> stripped)."""
    out: dict = {"command": command}
    try:
        url = (f"http://{host}:8888/cgi-bin/execute?rawcmd="
               + urllib.parse.quote(command, safe=""))
        with urllib.request.urlopen(url, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
        m = re.search(r"<pre>(.*?)</pre>", body, re.S)
        text = m.group(1) if m else body
        text = re.sub(r"^Executing:[^\n]*\n?", "", text, count=1)
        out["output"] = text.strip()
    except Exception as exc:
        out["error"] = f"{exc.__class__.__name__}: {exc}"
    return out


# ------------------------------------------------------------------ steps
def st0_baseline(host: str, port: int, path: str, seconds: float) -> dict:
    hr("ST0 — BASELINE: is the streamer currently ARMED (first client gets body)?")
    a = stream_client(host, port, path, read_seconds=seconds, label="A-baseline")
    if a["result"] != "BODY_OK":
        fail("ST0 FAILED — the camera is NOT serving a body even to the first "
             "client. Arm live preview first (menu / BT app), wait 10s, re-run.")
    else:
        ok(f"ST0 PASS — armed; {fmt_bytes(a['bytes'])} in {seconds:.0f}s")
    return a


def st1_self_heal(host: str, port: int, path: str, seconds: float,
                  gaps: list[float]) -> dict:
    hr("ST1 — SELF-HEAL: does a delayed reconnect ever get a body?")
    results = []
    for i, gap in enumerate(gaps):
        info(f"  … waiting {gap:.0f}s after the previous disconnect")
        time.sleep(gap)
        b = stream_client(host, port, path, read_seconds=seconds,
                          label=f"B-heal+{int(sum(gaps[:i + 1]))}s")
        results.append(b)
        if b["result"] == "BODY_OK":
            ok(f"SELF-HEALS after ~{sum(gaps[:i + 1]):.0f}s — plain reconnect IS "
               "enough (then the Hub's retry loop should have recovered…)")
            break
    if all(r["result"] != "BODY_OK" for r in results):
        fail(f"NO self-heal within {sum(gaps):.0f}s — wedge persists without an "
             "external re-arm action (F-A confirmed)")
    return results


def st2_concurrent(host: str, port: int, path: str, seconds: float) -> dict:
    hr("ST2 — CONCURRENCY: does client B STEAL the session from a live client A?")
    import threading
    a_result: dict = {}
    b_result: dict = {}

    def run_a():
        a_result.update(stream_client(host, port, path, read_seconds=seconds,
                                      label="A-holder"))

    ta = threading.Thread(target=run_a)
    ta.start()
    time.sleep(2.5)                       # A is now streaming
    b_result.update(stream_client(host, port, path, read_seconds=4.0,
                                  label="B-joiner"))
    ta.join()
    ar, br = a_result.get("result"), b_result.get("result")
    if ar == "BODY_OK" and br == "BODY_OK":
        warn("BOTH flowed — camera served two clients concurrently (unexpected; "
             "single-session model needs revision)")
    elif ar == "BODY_OK" and br != "BODY_OK":
        ok("FIRST-WINS: A kept the session, B starved → concurrent probes are "
           "harmless to a healthy live session")
    elif ar != "BODY_OK" and br == "BODY_OK":
        warn("NEWEST-WINS: B STOLE the session and A froze → ANY second "
             "connection (phone app, probe, 2nd tab) freezes live view! "
             "This matches the reported freeze symptom.")
    else:
        warn(f"A={ar} B={br} — neither flowed (session was already consumed)")
    return {"a": a_result, "b": b_result}


def st3_rearm(host: str, port: int, path: str, seconds: float,
              apply_invasive: bool, verify_window: float) -> dict:
    hr("ST3 — RE-ARM MATRIX: which action makes the streamer serve again?")
    candidates: list[tuple[str, str, callable]] = [
        ("R0_control_wait", "no action — wedge persistence control", None),
        ("R1_root_8888", "GET http://cam:8888/ (touch mod web root)", None),
        ("R2_exec_ps", "execute CGI 'ps'", "ps"),
        ("R3_exec_internal_wget",
         "execute CGI: internal wget 127.0.0.1:7679 (does streamer serve INSIDE?)",
         "internal"),
        ("R4_kill_streamer", "execute CGI: kill the 7679 listener pid (init must "
         "respawn it)", "kill"),
    ]
    results = {}
    for name, desc, action in candidates:
        if name == "R4_kill_streamer" and not apply_invasive:
            info(f"  [{name}] SKIPPED (needs --apply)")
            results[name] = {"skipped": True, "desc": desc}
            continue
        hr(f"  {name} — {desc}")
        cam_before = camera_sockets(host, f"{name}/before")
        action_out: dict = {"desc": desc}
        if action is None and name == "R1_root_8888":
            try:
                with urllib.request.urlopen(f"http://{host}:8888/", timeout=6) as r:
                    action_out["http_status"] = getattr(r, "status", 200)
                    action_out["root_bytes"] = len(r.read())
                info(f"  root page: HTTP {action_out['http_status']} "
                     f"({action_out['root_bytes']} B)")
            except Exception as exc:
                action_out["error"] = str(exc)
                warn(f"  root page failed: {exc}")
        elif action == "ps":
            action_out.update(camera_exec(host, "ps"))
            info(f"  ps output: {len(action_out.get('output', ''))} bytes")
        elif action == "internal":
            wget = ("wget -q -O /tmp/g360_rearm.bin --header='Range: bytes=0-' "
                    "--timeout=5 http://127.0.0.1:7679/livestream_high.avi; "
                    "ls -l /tmp/g360_rearm.bin; rm -f /tmp/g360_rearm.bin")
            action_out.update(camera_exec(host, wget, timeout=25))
            m = re.search(r"g360_rearm\.bin\s+(\d+)", action_out.get("output", ""))
            if m:
                action_out["internal_bytes"] = int(m.group(1))
                info(f"  INTERNAL wget got {action_out['internal_bytes']} bytes "
                     "→ streamer serves INSIDE")
            else:
                info(f"  internal wget: {action_out.get('output', action_out.get('error', ''))[:120]}")
        elif action == "kill":
            killer = (
                "port=1DFF; "
                "for line in $(cat /proc/net/tcp); do case $line in *:$port) ;; esac; done; "
                "inode=$(awk 'NR>1 && $2 ~ /:1DFF$/ && $4 == \"0A\" {print $10}' /proc/net/tcp | head -1); "
                "if [ -z \"$inode\" ]; then echo 'no 7679 listener found'; else "
                "for p in /proc/[0-9]*; do for fd in $p/fd/*; do "
                "lnk=$(readlink $fd 2>/dev/null); case \"$lnk\" in "
                "socket:[$inode]*) pid=${p#/proc/}; echo \"killing pid $pid $(cat $p/cmdline 2>/dev/null | tr '\\0' ' ')\"; "
                "kill -TERM $pid; break 2;; esac; done; done; fi"
            )
            action_out.update(camera_exec(host, killer, timeout=20))
            info(f"  kill: {action_out.get('output', action_out.get('error', ''))[:200]}")
            info("  … waiting 4s for init respawn")
            time.sleep(4)
        time.sleep(1.5)
        c = stream_client(host, port, path, read_seconds=verify_window,
                          label=f"C-after-{name}")
        cam_after = camera_sockets(host, f"{name}/after")
        results[name] = {"action": action_out, "verify": c,
                         "sockets_before": cam_before, "sockets_after": cam_after}
        if c["result"] == "BODY_OK":
            ok(f"{name} RE-ARMED the streamer — body flows again!")
            save_evidence("session_lifecycle_rearm", {"winner": name,
                                                      "results": results})
            return results
        fail(f"{name} did NOT re-arm (C={c['result']})")
    return results


# ------------------------------------------------------------------- main
def main() -> int:
    ap = base_parser("Session-lifecycle decisive probe (single-shot streamer)")
    ap.add_argument("--seconds", type=float, default=5.0,
                    help="read window per client (default 5)")
    ap.add_argument("--heal-gaps", default="1,5,15,30",
                    help="cumulative self-heal wait schedule (s)")
    ap.add_argument("--verify-window", type=float, default=4.0,
                    help="verification client window for re-arm candidates")
    ap.add_argument("--apply", action="store_true",
                    help="allow the INVASIVE re-arm candidate (kill streamer pid; "
                         "init must respawn it) — run only when you accept that")
    ap.add_argument("--skip-st2", action="store_true", help="skip concurrency test")
    ap.add_argument("--skip-st3", action="store_true", help="skip re-arm matrix")
    args = ap.parse_args()

    ensure_dirs()
    hr(f"SESSION LIFECYCLE PROBE — {args.host}:{args.port}{args.path}")
    lock = guard_stream_access(args.override, "session_lifecycle_probe")

    gaps = [float(x) for x in args.heal_gaps.split(",") if x.strip()]
    summary: dict = {"host": args.host, "port": args.port, "path": args.path,
                     "apply": args.apply}

    st0 = st0_baseline(args.host, args.port, args.path, args.seconds)
    summary["st0_baseline"] = st0
    if st0.get("result") != "BODY_OK":
        summary["verdict"] = "ABORT_NOT_ARMED"
        save_evidence("session_lifecycle_probe", summary)
        verdict("camera not armed — fix that first, then re-run")
        return 1

    sockets = camera_sockets(args.host, "after-baseline")
    summary["sockets_after_baseline"] = sockets

    st1 = st1_self_heal(args.host, args.port, args.path, args.seconds, gaps)
    summary["st1_self_heal"] = st1
    healed = any(r.get("result") == "BODY_OK" for r in st1)

    if not healed and not args.skip_st2:
        st2 = st2_concurrent(args.host, args.port, args.path, args.seconds)
        summary["st2_concurrent"] = st2
    elif healed:
        info("self-heal proven — concurrency test unnecessary")

    if not healed and not args.skip_st3:
        st3 = st3_rearm(args.host, args.port, args.path, args.seconds,
                        args.apply, args.verify_window)
        summary["st3_rearm"] = st3
        winners = [k for k, v in st3.items()
                   if isinstance(v, dict) and v.get("verify", {}).get("result") == "BODY_OK"]
        summary["rearm_winners"] = winners

    hr("FINAL VERDICT")
    if healed:
        summary["verdict"] = "SELF_HEALS"
        verdict(f"streamer self-heals within {sum(gaps):.0f}s — Hub needs a longer "
                "settle window on headers-without-body, not a re-arm")
        ret = 0
    elif summary.get("rearm_winners"):
        summary["verdict"] = f"REARM_VIA_{summary['rearm_winners'][0]}"
        verdict(f"re-arm works via {summary['rearm_winners']} — wire this into the "
                "Hub's wedged-recovery path (POST /api/camera/rearm)")
        ret = 0
    else:
        summary["verdict"] = "NO_REARM_FOUND"
        verdict("no re-arm found among candidates — power-cycle remains the only "
                "recovery; attach evidence + ST3 sockets to the report")
        ret = 1
    save_evidence("session_lifecycle_probe", summary)
    return ret


if __name__ == "__main__":
    sys.exit(main())
