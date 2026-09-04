#!/usr/bin/env python3
"""Investigation orchestrator — runs the mandatory phase order (task §53).

PHASE 1  Independent network test          → tcp_stream_probe.py
PHASE 2  Independent raw stream capture    → raw_camera_probe.py + raw_camera_stream_dump.py
PHASE 2b Isolated HTTP variant matrix      → http_variants_probe.py
PHASE 3  Camera-side service inspection    → camera_shell_inspect.py   (telnet; skipped if closed)
PHASE 4  Camera control (8888/FTP/Telnet)  → camera_control_probe.py   (never touches 7679)
PHASE 5-7 Offline container + H264         → parse_captured_stream.py (on the PHASE 2 dump)
PHASE 8  Independent FFmpeg decode         → test_h264_decode.py      (on extracted AUs)

Every phase result is collected into diagnostics/raw/evidence/investigation_summary.json
and printed as a live Failure Tree (task §43). Stream-touching phases honor the
session lock; control/offline phases never compete with the live session.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from g360_common import (EVIDENCE_DIR, RAW_DIR, ensure_dirs, fail, hr, info,
                         ok, save_evidence, session_lock_status, verdict, warn)

PHASES = [
    ("PHASE 1", "Independent TCP/stream timing", [sys.executable, "tools/tcp_stream_probe.py", "--seconds", "8"]),
    ("PHASE 2", "Raw probe + continuity proof", [sys.executable, "tools/raw_camera_probe.py", "--seconds", "15", "--save-mb", "0.25"]),
    ("PHASE 2d", "Raw stream dump (input for offline phases)", [sys.executable, "tools/raw_camera_stream_dump.py", "--seconds", "12", "--mb", "4"]),
    ("PHASE 2b", "Isolated HTTP variant matrix", [sys.executable, "tools/http_variants_probe.py", "--order", "B,A,C,D,E", "--settle", "3"]),
    ("PHASE 2c", "Session lifecycle (single-shot streamer + re-arm)", [sys.executable, "tools/session_lifecycle_probe.py", "--seconds", "5"]),
    ("PHASE 3", "Camera-side inspection (8888 exec-CGI)", [sys.executable, "tools/camera_shell_inspect.py"]),
    ("PHASE 4", "Camera control probe (23/21/8888)", [sys.executable, "tools/camera_control_probe.py"]),
    ("PHASE 5-7", "Offline container parse + H264 validation", [sys.executable, "tools/parse_captured_stream.py"]),
    ("PHASE 8", "Independent FFmpeg decode (real bytes)", [sys.executable, "tools/test_h264_decode.py"]),
]
LATEST = EVIDENCE_DIR / "latest_dump.txt"


def latest_dump() -> Path | None:
    dumps = sorted(RAW_DIR.glob("dump_*.bin"))
    return dumps[-1] if dumps else None


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Run the full investigation in mandatory order")
    ap.add_argument("--skip-stream", action="store_true",
                    help="skip stream-touching phases (use when live session is precious)")
    ap.add_argument("--only", default="", help="comma list of phase keys to run (e.g. 2,5-7)")
    args = ap.parse_args()
    ensure_dirs()

    lock = session_lock_status()
    only = [s.strip() for s in args.only.split(",") if s.strip()]

    hr("ROOT-CAUSE INVESTIGATION — MANDATORY PHASE ORDER (§53)")
    info(f"session lock: {lock}")

    results: dict = {}
    latest = latest_dump()
    for key, title, cmd in PHASES:
        if only and key not in only:
            continue
        stream_phase = key in ("PHASE 1", "PHASE 2", "PHASE 2d", "PHASE 2b", "PHASE 2c")
        if stream_phase and (args.skip_stream or lock["live_session"]):
            reason = "skipped-stream" if args.skip_stream else "skipped-live-session"
            results[key] = {"status": reason, "title": title}
            warn(f"{key} {title} → {reason} (camera busy / protected)")
            continue
        if key in ("PHASE 5-7", "PHASE 8"):
            latest = latest_dump()
            if latest is None:
                results[key] = {"status": "no-dump", "title": title}
                warn(f"{key} needs a dump file (run PHASE 2d first)")
                continue
            if key == "PHASE 5-7":
                cmd = [sys.executable, "tools/parse_captured_stream.py",
                       str(latest), "--extract", "8", "--save-aus"]
            else:
                cmd = [sys.executable, "tools/test_h264_decode.py", str(latest)]

        hr(f"{key} — {title}")
        info("$ " + " ".join(str(c) for c in cmd))
        try:
            proc = subprocess.run(cmd, cwd=str(REPO_ROOT), timeout=420)
            status = "PASS" if proc.returncode == 0 else f"FAIL(rc={proc.returncode})"
        except subprocess.TimeoutExpired:
            status = "TIMEOUT"
        except Exception as exc:
            status = f"ERROR {exc}"
        results[key] = {"status": status, "title": title}
        (ok if status == "PASS" else fail)(f"{key} → {status}")

    hr("FAILURE TREE (§43) — live from evidence")
    tree = [
        ("Camera unreachable?", "PHASE 1" not in results or "FAIL" in results.get("PHASE 1", {}).get("status", "")),
        ("HTTP body unavailable?", "PHASE 2" in results and "FAIL" in results["PHASE 2"].get("status", "")),
        ("HTTP style dependency?", "PHASE 2b" in results and "FAIL" in results["PHASE 2b"].get("status", "")),
        ("Camera-side streamer suspect?", "PHASE 3" in results and "FAIL" in results["PHASE 3"].get("status", "")),
        ("Control plane broken?", "PHASE 4" in results and "FAIL" in results["PHASE 4"].get("status", "")),
        ("Container/parser fails?", "PHASE 5-7" in results and "FAIL" in results["PHASE 5-7"].get("status", "")),
        ("Decoder fails?", "PHASE 8" in results and "FAIL" in results["PHASE 8"].get("status", "")),
    ]
    for label, failed in tree:
        info(f"  {'[x]' if failed else '[ ]'} {label}")
    if not any(f for _, f in tree):
        ok("NO failure isolated yet — with all-green phases the problem is in the "
           "Hub integration layer (compare hub logs against these independent results)")

    summary = {"ts": time.strftime("%F %T"), "lock": lock, "phases": results,
               "failure_tree": {k: v for k, v in tree}}
    save_evidence("investigation_summary", summary)
    verdict("investigation run complete — attach diagnostics/raw/evidence/ to the report")
    return 0


if __name__ == "__main__":
    sys.exit(main())
