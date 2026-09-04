"""Camera streamer re-arm — recovery for the PROVEN single-shot streamer.

Field evidence (diagnostics/raw/evidence 2026-09-04, run_investigation
PHASE 1→2→2d with identical request bytes): the SM-R210 streamer serves
exactly ONE client per arming; after that client disconnects, every new
client receives 206 headers and ZERO body. The stock phone app re-arms via
Bluetooth ('liveview' cmd-req) — a PC Hub has no BT channel, but THIS unit
runs gear360_modding whose web shell on port 8888 executes commands
(camera_control_probe PHASE 4: /cgi-bin/execute?rawcmd=echo → 200 + output).

Re-arm candidates (ordered, safe first — verified by tools/
session_lifecycle_probe.py on the real camera):
  S1 root_ping      GET http://cam:8888/                     (harmless)
  S2 cgi_ping       execute CGI 'echo …'                      (harmless)
  S3 cgi_ps         execute CGI 'ps'                          (harmless)
  S4 kill_streamer  execute CGI: TERM the pid holding the 7679 LISTEN
                    socket; the camera's init must respawn it (invasive;
                    only with apply=True)

The re-arm outcome is ALWAYS verified implicitly: the caller (manager/UI)
immediately reconnects the real client afterwards — a separate "verify"
client would consume the arming itself.
"""
from __future__ import annotations

import re
import time
import urllib.parse
import urllib.request

from ..core import settings as settings_svc
from ..core.logging_setup import get

LOG = get("camera.rearm")

_STREAM_PORT_HEX = "1DFF"   # 7679

_KILL_STREAMER_CMD = (
    "inode=$(awk 'NR>1 && $2 ~ /:" + _STREAM_PORT_HEX + "$/ && $4 == \"0A\" "
    "{print $10}' /proc/net/tcp | head -1); "
    "if [ -z \"$inode\" ]; then echo 'no 7679 listener — nothing to kill'; else "
    "for p in /proc/[0-9]*; do for fd in $p/fd/*; do "
    "lnk=$(readlink $fd 2>/dev/null); case \"$lnk\" in "
    "socket:[$inode]*) pid=${p#/proc/}; "
    "echo \"killing pid $pid $(cat $p/cmdline 2>/dev/null | tr '\\0' ' ')\"; "
    "kill -TERM $pid; break 2;; esac; done; done; fi"
)
_LISTEN_CHECK_CMD = (
    "awk 'NR>1 && $2 ~ /:" + _STREAM_PORT_HEX + "$/ && $4 == \"0A\" "
    "{print \"LISTEN\", $10}' /proc/net/tcp | head -1; "
    "echo done"
)


def _cgi_exec(host: str, command: str, timeout: float = 15.0) -> dict:
    out: dict = {"command": command}
    url = (f"http://{host}:8888/cgi-bin/execute?rawcmd="
           + urllib.parse.quote(command, safe=""))
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
        m = re.search(r"<pre>(.*?)</pre>", body, re.S)
        text = m.group(1) if m else body
        text = re.sub(r"^Executing:[^\n]*\n?", "", text, count=1)
        out["output"] = text.strip()
        out["ok"] = True
    except Exception as exc:
        out["error"] = f"{exc.__class__.__name__}: {exc}"
        out["ok"] = False
    return out


def _root_ping(host: str, timeout: float = 6.0) -> dict:
    try:
        with urllib.request.urlopen(f"http://{host}:8888/", timeout=timeout) as r:
            return {"ok": True, "http_status": getattr(r, "status", 200),
                    "bytes": len(r.read())}
    except Exception as exc:
        return {"ok": False, "error": f"{exc.__class__.__name__}: {exc}"}


def _listener_alive(host: str) -> bool:
    r = _cgi_exec(host, _LISTEN_CHECK_CMD, timeout=10)
    return "LISTEN" in r.get("output", "")


def rearm(apply: bool = False, host: str | None = None,
          respawn_wait: float = 5.0) -> dict:
    """Run the re-arm ladder. apply=False → safe steps only.

    Returns a structured report; `applied` is True when at least one action
    executed, `listener_respawned` only meaningful for the invasive step.
    NEVER claims success of the arming itself — only of the actions taken;
    the real proof is the next live connection receiving a body.
    """
    host = host or settings_svc.get("camera.host")
    report: dict = {"host": host, "apply": apply, "actions": [],
                    "applied": False, "listener_before": None,
                    "listener_after": None}

    report["listener_before"] = _listener_alive(host)

    steps: list[tuple[str, callable]] = [
        ("S1_root_ping", lambda: _root_ping(host)),
        ("S2_cgi_echo", lambda: _cgi_exec(host, "echo g360-rearm")),
        ("S3_cgi_ps", lambda: _cgi_exec(host, "ps")),
    ]
    if apply:
        steps.append(("S4_kill_streamer", lambda: _cgi_exec(
            host, _KILL_STREAMER_CMD, timeout=20)))

    for name, fn in steps:
        res = fn()
        res["step"] = name
        report["actions"].append(res)
        if res.get("ok"):
            report["applied"] = True
            LOG.info("REARM %s ok (%s)", name,
                     str(res.get("output", res.get("http_status", "")))[:80])
            if name == "S4_kill_streamer":
                info = str(res.get("output", ""))
                if "no 7679 listener" in info:
                    LOG.info("REARM: streamer already gone (no listener to kill)")
                else:
                    LOG.info("REARM: kill issued — waiting %.1fs for init respawn",
                             respawn_wait)
                    time.sleep(respawn_wait)
        else:
            LOG.warning("REARM %s failed: %s", name, res.get("error"))
        # brief pacing between actions — the 8888 server is single-threaded
        time.sleep(0.3)

    report["listener_after"] = _listener_alive(host)

    if any(a["step"] == "S4_kill_streamer" and a.get("ok") for a in report["actions"]):
        report["listener_respawned"] = report["listener_after"]
        if report["listener_respawned"]:
            LOG.info("REARM: 7679 listener respawned after kill")
        else:
            LOG.error("REARM: 7679 listener NOT back after kill — power-cycle needed")
    report["note"] = ("actions executed; the arming itself is only proven by the "
                      "next live connection receiving a body (Connect now)")
    return report
