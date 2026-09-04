#!/usr/bin/env python3
"""§10 — Endpoint search across the vendored reference sources.

Searches ONLY evidence-backed locations (gear360_modding + Gear360App) for
stream/preview endpoint strings. No guessing of paths — anything found here
is a candidate; anything not found must NOT be probed.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VENDOR = REPO_ROOT / "vendor"
PATTERNS = re.compile(
    r"(livestream\w*|preview\w*|/stream\w*|video\w*\.avi|\.avi|:7679|port\s*7679|"
    r"high\.avi|low\.avi|osc\w*)", re.IGNORECASE)
URI = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)


def main() -> int:
    if not VENDOR.exists():
        print("vendor/ missing — run:\n"
              "  git clone --depth 1 https://github.com/ottokiksmaler/gear360_modding vendor/gear360_modding\n"
              "  git clone --depth 1 https://github.com/TecCheck/Gear360App vendor/Gear360App")
        return 2
    hits: dict[str, list] = {}
    uris: set = set()
    for base in VENDOR.iterdir():
        if not base.is_dir():
            continue
        for f in base.rglob("*"):
            if not f.is_file() or f.stat().st_size > 3_000_000:
                continue
            if f.suffix.lower() in (".png", ".jpg", ".zip", ".jar", ".so", ".static", ".tg", ".adj"):
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if PATTERNS.search(line):
                    rel = f.relative_to(VENDOR)
                    hits.setdefault(str(rel), []).append(f"{i}: {line.strip()[:160]}")
                for u in URI.findall(line):
                    if any(k in u.lower() for k in ("7679", "avi", "stream", "preview")):
                        uris.add(u[:160])

    print("=" * 72)
    print("  EVIDENCE-BACKED ENDPOINT SEARCH (vendor sources)")
    print("=" * 72)
    for f, lines in sorted(hits.items()):
        print(f"\n  {f}")
        for l in lines[:6]:
            print(f"    {l}")
        if len(lines) > 6:
            print(f"    … +{len(lines) - 6} more")
    print("\n  Distinct URIs found:")
    if uris:
        for u in sorted(uris):
            print(f"    {u}")
    else:
        print("    (none)")
    print("\n  RULE: probe ONLY URIs listed above. Everything else = guessing (forbidden).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
