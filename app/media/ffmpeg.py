"""FFmpeg locator: bundled runtime/ffmpeg -> custom path -> PATH. With test."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..core.paths import DIRS
from ..core.logging_setup import get

LOG = get("media")


@dataclass
class FFmpegInfo:
    found: bool = False
    path: str = ""
    version: str = ""
    source: str = ""            # bundled | custom | path
    error: str = ""
    ffprobe_path: str = field(default="")


_lock = threading.Lock()
_cached: Optional[FFmpegInfo] = None


def _run_version(binary: Path) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            [str(binary), "-version"], capture_output=True, text=True, timeout=10
        )
        if proc.returncode == 0:
            first = proc.stdout.splitlines()[0] if proc.stdout else ""
            return True, first
        return False, proc.stderr.strip()[:200]
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)


def locate(mode: str = "auto", custom_path: str = "") -> FFmpegInfo:
    """Find ffmpeg according to policy. Never raises; result cached per inputs."""
    global _cached
    with _lock:
        key = (mode, custom_path)
        if _cached is not None and getattr(_cached, "_key", None) == key:
            return _cached

        info = FFmpegInfo()
        exe_name = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
        candidates: list[tuple[str, Path]] = []
        if mode == "custom" and custom_path:
            candidates.append(("custom", Path(custom_path)))
        if mode in ("auto", "bundled"):
            candidates.append(("bundled", DIRS["ffmpeg"] / exe_name))
        if mode in ("auto", "custom", "path"):
            which = shutil.which("ffmpeg")
            if which:
                candidates.append(("path", Path(which)))

        for source, path in candidates:
            ok, out = _run_version(path)
            if ok:
                info.found = True
                info.path = str(path)
                info.source = source
                info.version = out
                probe = path.parent / (("ffprobe.exe" if os.name == "nt" else "ffprobe"))
                info.ffprobe_path = str(probe) if probe.exists() else ""
                LOG.info("FFmpeg located via %s: %s (%s)", source, path, out[:80])
                break
        else:
            info.error = (
                "FFmpeg not found. Put ffmpeg.exe in runtime/ffmpeg/, "
                "install it in PATH, or set a custom path in Settings."
            )
            LOG.warning("FFmpeg not found (mode=%s custom=%r)", mode, custom_path)

        info._key = key  # type: ignore[attr-defined]
        _cached = info
        return info


def reset_cache() -> None:
    global _cached
    with _lock:
        _cached = None


def parse_version(info: FFmpegInfo) -> str:
    m = re.search(r"ffmpeg version (\S+)", info.version)
    return m.group(1) if m else (info.version.split(" ")[2] if info.version else "")
