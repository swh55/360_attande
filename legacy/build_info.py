"""Build identity: which exact version is running? (never trust READMEs).

Resolution order for the commit hash:
  1. GEAR360_BUILD_COMMIT env var (set by packaging/release scripts)
  2. runtime/build_info.json (written by release tooling)
  3. `git rev-parse HEAD` in the repository root (source checkouts)
  4. "unknown" — displayed honestly everywhere

The startup banner prints it so a support session can verify instantly which
build produced the log lines below it.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path

VERSION = "1.2.0"

_cache: dict | None = None
_lock = threading.Lock()


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _git_commit(root: Path) -> tuple[str, bool]:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(root), capture_output=True,
            text=True, timeout=3,
        )
        if out.returncode == 0:
            sha = out.stdout.strip()
            dirty = False
            try:
                st = subprocess.run(
                    ["git", "status", "--porcelain"], cwd=str(root),
                    capture_output=True, text=True, timeout=3)
                dirty = bool(st.stdout.strip())
            except Exception:
                pass
            return sha, dirty
    except Exception:
        pass
    return "", False


def build_info() -> dict:
    """{version, commit, commit_short, build_time, source, dirty}."""
    global _cache
    with _lock:
        if _cache is not None:
            return dict(_cache)
        commit, source, dirty = "", "unknown", False
        env = os.environ.get("GEAR360_BUILD_COMMIT", "").strip()
        if env:
            commit, source = env, "env"
        else:
            meta_file = _repo_root() / "runtime" / "build_info.json"
            if meta_file.exists():
                try:
                    meta = json.loads(meta_file.read_text())
                    commit = str(meta.get("commit", "")).strip()
                    source = "build_info.json"
                except Exception:
                    pass
        if not commit:
            commit, dirty = _git_commit(_repo_root())
            if commit:
                source = "git"
        _cache = {
            "version": VERSION,
            "commit": commit or "unknown",
            "commit_short": (commit[:7] if commit else "unknown"),
            "build_time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "source": source,
            "dirty": dirty,
        }
        return dict(_cache)


def banner_lines() -> list[str]:
    b = build_info()
    return [
        f"Gear360 Hub v{b['version']}",
        f"BUILD COMMIT: {b['commit_short']}" + (" (local changes present)" if b["dirty"] else ""),
        f"BUILD TIME:   {b['build_time']}",
    ]
