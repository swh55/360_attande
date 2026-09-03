"""Isolated FFmpeg / decoder probes — never touch the live pipeline."""
from __future__ import annotations

import time

from ..media.ffmpeg import locate
from ..core import settings as settings_svc


def ffmpeg_self_test() -> dict:
    """Locate FFmpeg and run a tiny in-process transcode to prove it works."""
    info = locate(settings_svc.get("ffmpeg.mode"), settings_svc.get("ffmpeg.custom_path", ""))
    out: dict = {
        "found": info.found, "path": info.path, "source": info.source,
        "version": info.version.split("\n")[0] if info.version else "",
        "error": info.error,
    }
    if not info.found:
        return out
    import subprocess
    try:
        t0 = time.time()
        proc = subprocess.run(
            [info.path, "-hide_banner", "-loglevel", "error",
             "-f", "lavfi", "-i", "testsrc=size=64x64:rate=5:duration=0.2",
             "-f", "null", "-"],
            capture_output=True, timeout=15,
        )
        out["self_test"] = "PASS" if proc.returncode == 0 else "FAIL"
        out["self_test_ms"] = round((time.time() - t0) * 1000)
        if proc.returncode != 0:
            out["stderr"] = proc.stderr.decode("utf-8", "replace")[-500:]
    except (OSError, subprocess.TimeoutExpired) as exc:
        out["self_test"] = "FAIL"
        out["stderr"] = str(exc)
    return out


def decode_probe(aus: list[bytes], codec: str, width: int, height: int,
                 ffmpeg_path: str, timeout: float = 15.0) -> dict:
    """Feed captured access units to a fresh FFmpegDecoder and count frames."""
    from ..media.decoder import FFmpegDecoder

    out: dict = {"fed": 0, "frames": 0, "ok": False, "codec": codec,
                 "width": width, "height": height, "stderr": [], "error": ""}
    if not aus:
        out["error"] = "no access units available"
        return out
    if width <= 0 or height <= 0:
        out["error"] = f"invalid dimensions {width}x{height}"
        return out
    dec = FFmpegDecoder(codec, width, height, ffmpeg_path=ffmpeg_path or "ffmpeg")
    try:
        dec.start()
    except Exception as exc:
        out["error"] = f"decoder start failed: {exc}"
        return out
    deadline = time.time() + timeout
    try:
        for au in aus:
            out["fed"] += 1
            try:
                got = dec.write(au)
            except (BrokenPipeError, OSError) as exc:
                out["error"] = f"pipe failed after {out['fed']} AUs: {exc}"
                break
            out["frames"] += len(got)
            if out["frames"] >= 3:
                break
        # drain briefly — the rawvideo demuxer needs a little lookahead
        while out["frames"] < 3 and time.time() < deadline:
            try:
                got = dec.write(b"")
            except (BrokenPipeError, OSError):
                break
            out["frames"] += len(got)
            time.sleep(0.1)
        out["ok"] = out["frames"] > 0
        out["stderr"] = list(dec.stderr_tail)[-6:]
        if not out["ok"] and dec.last_error:
            out["error"] = out["error"] or dec.last_error
    finally:
        dec.close()
    return out
