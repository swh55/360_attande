"""Diagnostic report assembly: full JSON report + downloadable ZIP bundle.
The ZIP never contains secrets (no .env, no tokens, no credentials)."""
from __future__ import annotations

import json
import platform
import sys
import zipfile
from pathlib import Path

from ..core.paths import DIRS

LOG_DOMAINS = ("app", "camera", "stream", "decoder", "errors")
LOG_TAIL_LINES = 200


def build_report(hub, session: dict) -> dict:
    from ..core import database as db
    from ..core import settings as settings_svc

    steps = session.get("steps", [])
    timing = {s.get("key"): s.get("duration_ms") for s in steps}
    errors = [
        {"step": s.get("key"), "detail": s.get("detail")}
        for s in steps if s.get("status") == "FAIL"
    ]
    warnings = [
        {"step": s.get("key"), "detail": s.get("detail")}
        for s in steps if s.get("status") == "WARNING"
    ]
    live = session.get("live") or {}
    http = session.get("http") or {}
    variants = session.get("http_variants") or {}
    tcp = session.get("tcp") or {}
    p = hub.camera._pipeline
    transport = p.diagnostics().get("transport") if p is not None else None
    return {
        "generated_at": session.get("finished_at"),
        "session_id": session.get("id"),
        "system": {
            "app": "Gear360 Hub", "version": "1.0.0",
            "python": sys.version.split(" ")[0],
            "platform": f"{platform.system()} {platform.release()}",
            "mode": hub.mode,
            "ffmpeg": {"found": hub.ffmpeg.found, "path": hub.ffmpeg.path,
                       "source": hub.ffmpeg.source},
            "database": {"schema_version": db.schema_version()},
        },
        "camera": {
            "config": {
                "host": settings_svc.get("camera.host"),
                "port": settings_svc.get("camera.port"),
                "stream_path": settings_svc.get("camera.stream_path"),
                "profile": settings_svc.get("camera.profile"),
            },
            "state": hub.camera.state,
            "reachable": hub.camera.camera_reachable,
            "latency_ms": hub.camera.last_latency_ms,
        },
        "network": {"tcp": {k: v for k, v in tcp.items() if k != "host"},
                    "icmp": session.get("icmp")},
        "http": {k: http.get(k) for k in
                 ("url", "status", "content_type", "bytes_read", "elapsed_ms",
                  "bytes_per_sec", "variant", "stalled", "error")},
        "http_variants": {
            "any_data": variants.get("any_data"),
            "all_stalled": variants.get("all_stalled"),
            "variants": [
                {k: v.get(k) for k in ("variant", "path", "status", "content_type",
                                       "bytes_read", "elapsed_ms", "error")}
                for v in variants.get("variants", [])
            ],
        },
        "transport": transport,
        "stream": {"bytes_seen": live.get("bytes_seen"),
                   "chunks_seen": live.get("chunks_seen"),
                   "resyncs": live.get("resyncs"),
                   "boundary_errors": live.get("boundary_errors"),
                   "framing": live.get("framing")},
        "chunks": session.get("scan"),
        "parser": session.get("vid0"),
        "codec": session.get("codec"),
        "decoder": session.get("decoder_probe"),
        "frame": session.get("frame"),
        "snapshot": session.get("snapshot"),
        "errors": errors,
        "warnings": warnings,
        "timing": timing,
        "findings": session.get("findings", []),
    }


def _tail(path: Path, lines: int) -> list[str]:
    try:
        if not path.exists():
            return []
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return [l.rstrip("\n") for l in fh.readlines()[-lines:]]
    except OSError:
        return []


def build_zip(hub, session: dict, out_dir: Path | None = None) -> Path:
    from .capture import CAPTURES_DIR, latest_capture

    out_dir = out_dir or (DIRS["data"] / "diagnostics")
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = (session.get("finished_at") or "").replace(":", "").replace("-", "").replace(" ", "_")
    zip_path = out_dir / f"gear360_diagnostic_{stamp}.zip"
    report = build_report(hub, session)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("report.json", json.dumps(report, indent=2, default=str))
        zf.writestr("parser_output.json",
                    json.dumps(session.get("live") or {}, indent=2, default=str))
        probe = session.get("decoder_probe") or {}
        zf.writestr("ffmpeg_stderr.txt", "\n".join(probe.get("stderr") or []) or "(empty)")
        for domain in LOG_DOMAINS:
            lines = _tail(DIRS["logs"] / f"{domain}.log", LOG_TAIL_LINES)
            if lines:
                zf.writestr(f"logs/{domain}.log.tail", "\n".join(lines))
        cap = latest_capture()
        if cap:
            p = CAPTURES_DIR / cap
            try:
                if p.exists() and p.stat().st_size <= 1024 * 1024:
                    zf.write(p, f"sample/{cap}")
            except OSError:
                pass
    return zip_path
