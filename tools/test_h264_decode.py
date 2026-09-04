#!/usr/bin/env python3
"""PHASE 8 — Independent FFmpeg decode of REAL camera bytes (task §27-28).

Takes AUs extracted from the REAL SM-R210 stream (by parse_captured_stream.py
--save-aus, or directly from a .bin dump) and decodes them with a FRESH
ffmpeg process — trying BOTH h264 and hevc (the VID0 codec field of the real
camera reports 2, which no reference table documents; the bits decide).

Success = real PNG frames written to diagnostics/raw/extracted/frame_*.png
Failure  = full ffmpeg stderr is captured and shown (never hidden).
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from g360_common import (EXTRACTED_DIR, ensure_dirs, fail, hr, info,
                         ok, save_evidence, verdict)


def parse_aus_from_dump(data: bytes, limit: int = 300) -> list[bytes]:
    """Minimal 00VD extractor (same conventions as parse_captured_stream)."""
    aus: list[bytes] = []
    off = 0
    movi = data.find(b"movi")
    if movi < 0:
        return aus
    off = movi + 4
    while off + 16 <= len(data) and len(aus) < limit:
        tag = data[off:off + 4]
        if tag not in (b"00VD", b"00AU", b"00VR", b"TTTS"):
            positions = [data.find(t, off) for t in (b"00VD", b"00AU", b"00VR", b"TTTS")]
            positions = [p for p in positions if p >= 0]
            if not positions:
                break
            off = min(positions)
            continue
        import struct
        size = struct.unpack(">I", data[off + 4:off + 8])[0]
        if size <= 0 or size > 4 * 1024 * 1024:
            break
        payload_off = off + 16
        if payload_off + size > len(data):
            break                      # truncated tail — stop cleanly
        payload = data[payload_off: payload_off + size]
        off = payload_off + size
        if tag != b"00VD":
            continue
        if payload[:4] == b"\x00\x00\x00\x01" or payload[:3] == b"\x00\x00\x01":
            bs = payload[:-5] if len(payload) > 5 else b""
        else:
            bs = payload[5:-5] if len(payload) > 10 else payload[5:]
        if bs:
            aus.append(bs)
    return aus


def try_decode(aus: list[bytes], codec: str, ffmpeg: str, out_dir: Path,
               width: int | None, height: int | None) -> dict:
    """Feed concatenated AUs to ffmpeg as raw annexb; save frames as PNG."""
    res: dict = {"codec": codec, "aus_fed": len(aus), "frames": 0, "ok": False}
    with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as tf:
        for au in aus:
            if not au.startswith((b"\x00\x00\x00\x01", b"\x00\x00\x01")):
                tf.write(b"\x00\x00\x00\x01")
            tf.write(au)
        path = tf.name
    try:
        cmd = ([ffmpeg, "-hide_banner", "-loglevel", "error",
                "-probesize", "32", "-analyzeduration", "0",
                "-f", codec, "-i", path] +
               (["-vf", f"scale={width}:{height}"] if width and height else []) +
               ["-vsync", "0", str(out_dir / "frame_%03d.png")])
        res["command"] = " ".join(cmd[:8]) + " …"
        proc = subprocess.run(cmd, capture_output=True, timeout=30)
        res["returncode"] = proc.returncode
        res["stderr"] = proc.stderr.decode("utf-8", "replace")[-1200:]
        frames = sorted(out_dir.glob("frame_*.png"))
        res["frames"] = len(frames)
        res["ok"] = proc.returncode == 0 and len(frames) > 0
        if res["ok"]:
            res["first_frame"] = str(frames[0])
    except FileNotFoundError:
        res["error"] = "ffmpeg not on PATH"
    except subprocess.TimeoutExpired:
        res["error"] = "ffmpeg timed out after 30s"
    finally:
        Path(path).unlink(missing_ok=True)
    return res


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Independent decode of REAL camera AUs")
    ap.add_argument("source", nargs="?", help=".bin dump OR directory with au_*.bin")
    ap.add_argument("--ffmpeg", default="ffmpeg")
    ap.add_argument("--width", type=int, default=None, help="stream width if known (e.g. 1280)")
    ap.add_argument("--height", type=int, default=None, help="stream height if known (e.g. 640)")
    args = ap.parse_args()
    ensure_dirs()

    hr("PHASE 8 — INDEPENDENT DECODE TEST (real camera bytes only)")
    src = Path(args.source) if args.source else EXTRACTED_DIR
    aus: list[bytes] = []
    if src.is_dir():
        files = sorted(src.glob("au_*.bin"))
        if files:
            aus = [f.read_bytes() for f in files]
            info(f"loaded {len(aus)} pre-extracted AUs from {src}")
        else:
            fail(f"no au_*.bin in {src} — run parse_captured_stream.py --save-aus first")
            return 2
    elif src.is_file():
        aus = parse_aus_from_dump(src.read_bytes())
        info(f"parsed {len(aus)} real 00VD AUs from {src.name}")
    else:
        fail(f"source not found: {src}")
        return 2

    if not aus:
        fail("no real AUs available — nothing to decode (REAL bytes required, §28)")
        save_evidence("test_h264_decode", {"verdict": "NO_REAL_AUS"})
        return 1

    total_bs = sum(len(a) for a in aus)
    info(f"real bitstream bytes: {total_bs}")
    out_dir = EXTRACTED_DIR
    results = []
    for codec in ("h264", "hevc"):
        for f in out_dir.glob("frame_*.png"):
            f.unlink()
        r = try_decode(aus, codec, args.ffmpeg, out_dir, args.width, args.height)
        results.append(r)
        if r["ok"]:
            ok(f"{codec}: DECODED {r['frames']} REAL frame(s) → {r['first_frame']}")
        else:
            fail(f"{codec}: no frames — stderr tail:")
            for line in r.get("stderr", r.get("error", "")).splitlines()[-6:]:
                info("      " + line)

    ev = {"source": str(src), "aus": len(aus), "bitstream_bytes": total_bs,
          "results": results}
    good = [r for r in results if r["ok"]]
    if good:
        ev["verdict"] = f"REAL_DECODE_OK_{good[0]['codec'].upper()}"
        ev["working_codec"] = good[0]["codec"]
        verdict(f"REAL H264/HEVC DECODE PROVEN via {good[0]['codec']} "
                f"({good[0]['frames']} real frames) — decoder layer is NOT the problem")
        ret = 0
    else:
        ev["verdict"] = "DECODE_FAILED_BOTH_CODECS"
        verdict("ffmpeg decoded NEITHER h264 nor hevc from the real bytes — "
                "capture full stderr evidence above; suspect AU extraction or codec")
        ret = 1
    save_evidence("test_h264_decode", ev)
    return ret


if __name__ == "__main__":
    sys.exit(main())
