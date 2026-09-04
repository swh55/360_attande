#!/usr/bin/env python3
"""PHASE 5-7 — OFFLINE container analysis of a captured stream (task §20-26).

Works ONLY on a saved sample (from raw_camera_stream_dump.py or the Hub's
captures) — the camera is NOT opened (§21).

Produces:
  * header-zone table: TTTS/VID0/AUD0/VR00/LIST/movi with offsets + fields
  * movi chunk table: offset | tag | size | timestamp | payload offset |
    payload size | next offset  (§22 — real boundary proof)
  * extracted video frames (§25): first N 00VD payloads with sizes,
    timestamps, keyframe bytes, NAL scan (start codes, NAL types)
  * ffprobe verdict per extracted AU (§26) when ffprobe is on PATH

Usage:
  python tools/parse_captured_stream.py diagnostics/raw/dump_xxx.bin
  python tools/parse_captured_stream.py --extract 8 --list-only dump.bin
"""
from __future__ import annotations

import json
import struct
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from g360_common import (EXTRACTED_DIR, ensure_dirs, fail, fmt_bytes, hr,
                         info, ok, save_evidence, warn)

TAG_TTTS = b"TTTS"
TAG_VID0 = b"VID0"
TAG_VD00 = b"VD00"
TAG_AUD0 = b"AUD0"
TAG_VR00 = b"VR00"
TAG_LIST = b"LIST"
TAG_MOVI = b"movi"
MEDIA_TAGS = (b"00VD", b"00AU", b"00VR", b"TTTS")
MAX_CHUNK = 4 * 1024 * 1024


def u32(b: bytes, o: int) -> int:
    return struct.unpack(">I", b[o:o + 4])[0]


def u64(b: bytes, o: int) -> int:
    return struct.unpack(">Q", b[o:o + 8])[0]


def parse_vid0(data: bytes, payload_off: int, size: int) -> dict:
    """VID0 fields — offsets VERIFIED against Extractor.kt AND the real
    SM-R210 capture (identical to app/stream/parser.py):
      codec@16  scale@32  subTag@36  fps@44/48  width@76  height@80
    (payload = size_field - 4 bytes; the real camera sends size=96 → 92B)."""
    p = data[payload_off: payload_off + max(0, size - 4)]
    if len(p) < 84:
        return {"error": f"VID0 payload too short ({len(p)}B) — need ≥84"}
    fields = {
        "offset": payload_off, "size_field": size,
        "codec_raw": u32(p, 16),
        "timestamp_scale": u32(p, 32),
        "subtag": p[36:40].decode("latin-1", "replace"),
        "fps_num": u32(p, 44), "fps_den": u32(p, 48),
        "width": u32(p, 76), "height": u32(p, 80),
    }
    fields["fps"] = round(fields["fps_num"] / fields["fps_den"], 3) if fields["fps_den"] else 0
    fields["codec_guess"] = "hevc" if fields["codec_raw"] == 1 else "h264"
    return fields


def scan(data: bytes) -> dict:
    ev: dict = {"size": len(data)}
    head: list = []
    off = 0
    started = False
    chunks: list = []
    vid0 = None
    resyncs = 0
    while off + 8 <= len(data):
        tag = data[off:off + 4]
        if not started:
            if tag == TAG_TTTS:
                head.append({"off": off, "tag": "TTTS", "size": u32(data, off + 4),
                             "type": u32(data, off + 8) if off + 12 <= len(data) else None})
                off += 12
                continue
            if tag == TAG_VID0:
                size = u32(data, off + 4)
                vid0 = parse_vid0(data, off + 8, size)
                head.append({"off": off, "tag": "VID0", "size": size})
                off += 8 + max(0, size - 4)
                continue
            if tag == TAG_AUD0:
                size = u32(data, off + 4)
                head.append({"off": off, "tag": "AUD0", "size": size,
                             "subtag": data[off + 20:off + 24].decode("latin-1", "replace")})
                off += 8 + max(0, size - 4)
                continue
            if tag == TAG_VR00:
                size = u32(data, off + 4)
                head.append({"off": off, "tag": "VR00", "size": size})
                off += 8 + max(0, size - 4)
                continue
            if tag == TAG_LIST:
                # Extractor.kt: LIST costs 8 bytes (tag + size); its type word
                # ("movi") is a SEPARATE 4-byte record handled below.
                head.append({"off": off, "tag": "LIST", "size": u32(data, off + 4),
                             "type": data[off + 8:off + 12].decode("latin-1", "replace")})
                off += 8
                continue
            if tag == TAG_MOVI:
                head.append({"off": off, "tag": "movi"})
                off += 4
                started = True
                continue
            # not yet in header zone → scan for movi
            idx = data.find(TAG_MOVI, off)
            if idx < 0:
                ev["error"] = "movi not found — not a Gear360 container?"
                return ev
            resyncs += 1
            off = idx
            continue
        # ---- movi zone ----
        if tag not in MEDIA_TAGS:
            # find next media tag
            positions = [data.find(t, off) for t in MEDIA_TAGS]
            positions = [p for p in positions if p >= 0]
            if not positions:
                chunks.append({"off": off, "tag": tag.decode("latin-1", "replace"),
                               "note": "TAIL — no further media tag (truncated sample)"})
                break
            nxt = min(positions)
            resyncs += 1
            chunks.append({"off": off, "tag": tag.decode("latin-1", "replace"),
                           "note": f"unknown tag; resync +{nxt - off}B"})
            off = nxt
            continue
        if off + 16 > len(data):
            chunks.append({"off": off, "tag": tag.decode("latin-1", "replace"),
                           "note": "TRUNCATED header of chunk"})
            break
        size = u32(data, off + 4)
        ts = u64(data, off + 8)
        if size <= 0 or size > MAX_CHUNK:
            chunks.append({"off": off, "tag": tag.decode("latin-1", "replace"),
                           "size_field": size, "note": "INVALID size"})
            positions = [data.find(t, off + 4) for t in MEDIA_TAGS]
            positions = [p for p in positions if p >= 0]
            if not positions:
                break
            off = min(positions)
            resyncs += 1
            continue
        payload_off = off + 16
        have = min(size, len(data) - payload_off)
        complete = have == size
        chunks.append({"off": off, "tag": tag.decode("latin-1", "replace"),
                       "size": size, "timestamp": ts,
                       "payload_off": payload_off, "payload_have": have,
                       "complete": complete,
                       "next_off": (payload_off + size) if complete else None})
        off = payload_off + size
        if not complete:
            chunks[-1]["note"] = ("TRUNCATED chunk — sample ends mid-frame "
                                  f"(have {have}/{size})")
            break
        if len(chunks) >= 20000:
            break
    ev["header_zone"] = head
    ev["vid0"] = vid0
    ev["movi_start"] = next((h["off"] for h in head if h["tag"] == "movi"), None)
    ev["resyncs"] = resyncs
    ev["chunk_count"] = len([c for c in chunks if "size" in c])
    ev["chunks"] = chunks
    return ev


def nal_scan(au: bytes) -> dict:
    """Locate Annex-B start codes + NAL header bytes in an access unit."""
    nals = []
    i = 0
    starts = []
    while i < len(au) - 4:
        if au[i] == 0 and au[i + 1] == 0:
            if au[i + 2] == 1:
                starts.append(i + 3)
                i += 3
                continue
            if au[i + 2] == 0 and au[i + 3] == 1:
                starts.append(i + 4)
                i += 4
                continue
        i += 1
    for s in starts:
        nals.append({"offset": s, "byte": au[s], "byte_hex": f"{au[s]:02x}"})
    return {"start_codes": len(starts), "nal_first_bytes": [n["byte"] for n in nals[:8]],
            "nal_offsets": [n["offset"] for n in nals[:8]]}


def extract_video_aus(data: bytes, chunks: list, want: int) -> list:
    aus = []
    for c in chunks:
        if c.get("tag") != "00VD" or not c.get("complete"):
            if c.get("tag") == "00VD" and not c.get("complete"):
                aus.append({"index": len(aus), "truncated": True,
                            "size": c["size"], "have": c["payload_have"]})
            continue
        payload = data[c["payload_off"]: c["payload_off"] + c["size"]]
        frame_type = payload[4] if len(payload) > 4 else None
        # payload = [5-byte sub-header?] + bitstream + [5-byte trailer?]
        if payload[:4] == b"\x00\x00\x00\x01" or payload[:3] == b"\x00\x00\x01":
            bitstream = payload[:-5] if len(payload) > 5 else b""
            framing = "native_annexb"
        else:
            bitstream = payload[5:-5] if len(payload) > 10 else payload[5:]
            framing = "subheader_stripped"
        au = {"index": len(aus), "chunk_size": c["size"], "timestamp": c["timestamp"],
              "frame_type_byte": frame_type,
              "keyframe": frame_type in (64, 38),
              "framing": framing, "bitstream_bytes": len(bitstream),
              "nal": nal_scan(bitstream)}
        aus.append(au)
        if len(aus) >= want:
            break
    return aus


def ffprobe_au(bitstream: bytes, codec: str) -> dict:
    """Try to decode one real AU with ffprobe in a given container codec (§26)."""
    import tempfile
    try:
        with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as tf:
            tf.write(bitstream)
            path = tf.name
        cmd = ["ffprobe", "-v", "error", "-probesize", "32", "-analyzeduration", "0",
               "-show_streams", "-select_streams", "v:0", "-of", "json",
               "-f", codec, path]
        proc = subprocess.run(cmd, capture_output=True, timeout=15)
        out = {"codec": codec, "returncode": proc.returncode}
        out["stderr"] = proc.stderr.decode("utf-8", "replace")[-400:]
        if proc.returncode == 0:
            try:
                j = json.loads(proc.stdout.decode())
                st = (j.get("streams") or [{}])[0]
                out["resolved"] = {k: st.get(k) for k in
                                   ("codec_name", "profile", "width", "height",
                                    "r_frame_rate", "pix_fmt")}
            except Exception:
                out["resolved"] = None
        Path(path).unlink(missing_ok=True)
        return out
    except FileNotFoundError:
        return {"codec": codec, "error": "ffprobe not on PATH"}
    except Exception as exc:
        return {"codec": codec, "error": f"{exc.__class__.__name__}: {exc}"}


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Offline Gear360 container analysis (no camera)")
    ap.add_argument("sample", help="path to a captured stream .bin")
    ap.add_argument("--extract", type=int, default=6, help="video AUs to analyze (default 6)")
    ap.add_argument("--save-aus", action="store_true",
                    help="write extracted AU bitstreams into diagnostics/raw/extracted/")
    args = ap.parse_args()
    ensure_dirs()

    path = Path(args.sample)
    if not path.exists():
        fail(f"sample not found: {path}")
        return 2
    data = path.read_bytes()
    hr(f"PHASE 5-7 — OFFLINE PARSE  {path.name}  ({fmt_bytes(len(data))})")

    ev = scan(data)
    hr("HEADER ZONE")
    for h in ev["header_zone"]:
        info(f"  off=0x{h['off']:06x}  {h['tag']:5s} size={h.get('size')} "
             + (f"type={h['type']}" if h.get("type") is not None else "")
             + (f" type={h.get('type')}" if "type" in h and h["tag"] == "LIST" else ""))
    if ev["vid0"]:
        hr("VID0 METADATA (from REAL bytes)")
        for k, v in ev["vid0"].items():
            info(f"  {k:18s}= {v}")
        v0 = ev["vid0"]
        if v0.get("subtag") != "VD00":
            warn("subTag is NOT VD00 — layout suspect")
    else:
        warn("no VID0 parsed — header zone incomplete in this sample")

    hr(f"MOVI CHUNKS ({ev['chunk_count']} counted — boundary table §22)")
    for c in ev["chunks"][:25]:
        if "size" in c:
            info(f"  off=0x{c['off']:06x} {c['tag']} size={c['size']:>7} ts={c.get('timestamp')} "
                 f"payload@0x{c.get('payload_off', 0):06x} "
                 f"{'COMPLETE' if c.get('complete') else 'TRUNCATED ' + str(c.get('note', ''))}")
        else:
            info(f"  off=0x{c['off']:06x} {c['tag']} — {c.get('note', '')}")
    trunc = [c for c in ev["chunks"] if c.get("note") and "TRUNCATED" in str(c.get("note"))]
    if trunc:
        warn(f"{len(trunc)} truncated chunk(s) — sample ends mid-frame (expected for bounded dumps)")
    else:
        ok("no truncated chunk inside the sample")

    hr(f"VIDEO CHUNKS (00VD) — first {args.extract} real frames (§24/§25)")
    aus = extract_video_aus(data, ev["chunks"], args.extract)
    for au in aus:
        if au.get("truncated"):
            warn(f"  AU#{au['index']} TRUNCATED size={au['size']} have={au['have']}")
            continue
        info(f"  AU#{au['index']} chunk_size={au['chunk_size']:>7} ts={au['timestamp']:>10} "
             f"keyframe={au['keyframe']} frame_type_byte={au['frame_type_byte']} "
             f"bitstream={au['bitstream_bytes']}B framing={au['framing']} "
             f"start_codes={au['nal']['start_codes']} "
             f"first_nal_bytes={[f'{b:02x}' for b in au['nal']['nal_first_bytes'][:4]]}")

    # save real AUs (§25) for the independent decode test
    saved_paths = []
    if args.save_aus:
        idx = 0
        for c in ev["chunks"]:
            if c.get("tag") != "00VD" or not c.get("complete") or idx >= args.extract:
                continue
            payload = data[c["payload_off"]: c["payload_off"] + c["size"]]
            bs = payload[:-5] if payload[:4] == b"\x00\x00\x00\x01" else payload[5:-5]
            p = EXTRACTED_DIR / f"au_{idx:03d}.bin"
            p.write_bytes(bs)
            saved_paths.append(str(p))
            idx += 1
        if saved_paths:
            ok(f"saved {len(saved_paths)} real AU bitstreams → diagnostics/raw/extracted/")

    ev["video_aus"] = aus
    ev["saved_au_files"] = saved_paths

    hr("FFPROBE ON REAL AUs (codec identification §26)")
    first_complete = next((a for a in aus if not a.get("truncated") and a["bitstream_bytes"] > 16), None)
    if first_complete and ev["vid0"]:
        au_data = None
        for c in ev["chunks"]:
            if c.get("tag") == "00VD" and c.get("complete"):
                payload = data[c["payload_off"]: c["payload_off"] + c["size"]]
                au_data = payload[:-5] if payload[:4] == b"\x00\x00\x00\x01" else payload[5:-5]
                break
        if au_data:
            for codec in ("h264", "hevc"):
                r = ffprobe_au(au_data, codec)
                if r.get("returncode") == 0:
                    ok(f"ffprobe({codec}) resolved: {r.get('resolved')}")
                else:
                    warn(f"ffprobe({codec}) failed: {r.get('stderr', r.get('error', ''))[:120]}")
                ev.setdefault("ffprobe", []).append(r)
    else:
        warn("no complete video AU in sample — ffprobe skipped (capture more bytes)")

    hr("VERDICT")
    complete_vd = [c for c in ev["chunks"] if c.get("tag") == "00VD" and c.get("complete")]
    if ev["chunk_count"] == 0:
        fail("no movi chunks recognized — container structure mismatch")
        ev["verdict"] = "NO_CHUNKS"
        ret = 1
    elif not complete_vd:
        fail("no COMPLETE 00VD chunk — need a bigger sample (first AU can be ~43KB)")
        ev["verdict"] = "NO_COMPLETE_00VD"
        ret = 1
    else:
        ok(f"{len(complete_vd)} complete 00VD chunk(s); boundaries verified; resyncs={ev['resyncs']}")
        ev["verdict"] = "STRUCTURE_VALID"
        ret = 0
    save_evidence("parse_captured_stream", ev)
    return ret


if __name__ == "__main__":
    sys.exit(main())
