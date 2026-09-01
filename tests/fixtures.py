"""Shared test fixtures: synthetic Gear360 stream builder + temp DB.

The synthetic builder mirrors the real SM-R210 container structure:
  TTTS chunks -> VID0 header -> LIST -> movi -> 00VD/00AU chunk run
so parser tests exercise the exact byte layout the camera produces.
"""
from __future__ import annotations

import os
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# isolate the DB per test session BEFORE app modules are imported
_TMPDIR = tempfile.mkdtemp(prefix="g360hub-test-")
os.environ["GEAR360HUB_DB"] = str(Path(_TMPDIR) / "test.db")

# ---------------------------------------------------------------- fixtures
def u32be(n: int) -> bytes:
    return struct.pack(">I", n)


def u64be(n: int) -> bytes:
    return struct.pack(">Q", n)


def make_vid0(codec: int = 1, width: int = 1920, height: int = 1080,
              fps: float = 30.0, scale: int = 1000) -> bytes:
    """VID0 header with field offsets matching Gear360App Extractor.kt."""
    payload = bytearray(96)
    struct.pack_into(">I", payload, 20, codec)
    struct.pack_into(">I", payload, 36, scale)
    struct.pack_into(">I", payload, 52, int(fps * 1000))
    struct.pack_into(">I", payload, 56, 1000)
    struct.pack_into(">I", payload, 88, width)
    struct.pack_into(">I", payload, 92, height)
    return b"VID0" + u32be(len(payload)) + bytes(payload)


def make_ttts(data: bytes = b"GEAR360HUBTEST") -> bytes:
    return b"TTTS" + u32be(len(data)) + b"\x00\x00\x00\x00" + data


def make_movi_header() -> bytes:
    """Realistic LIST header: listType='movi', payload zone follows directly."""
    return b"LIST" + u32be(4) + b"movi"


def make_00vd(timestamp: int, payload: bytes, frame_type: int = 64) -> bytes:
    body = bytes([0, 0, 0, 0, frame_type]) + payload
    return b"00VD" + u32be(len(body)) + u64be(timestamp) + body


# frame-type bytes that the parser counts as keyframes (Gear360App semantics)
KEYFRAME_TYPE = 64
NON_KEY_TYPE = 1


def make_00au(timestamp: int, payload: bytes = b"\x01\x02\x03\x04") -> bytes:
    return b"00AU" + u32be(len(payload)) + u64be(timestamp) + payload


def annexb_to_access_units(annexb: bytes) -> list[bytes]:
    """Split an Annex-B H.264/HEVC elementary stream into access units."""
    starts = []
    i = 0
    n = len(annexb)
    while i < n - 3:
        if annexb[i:i + 4] == b"\x00\x00\x00\x01":
            starts.append(i + 4)
            i += 4
        elif annexb[i:i + 3] == b"\x00\x00\x01":
            starts.append(i + 3)
            i += 3
        else:
            i += 1
    if not starts:
        return []
    nals = []
    for idx, s in enumerate(starts):
        e = starts[idx + 1] if idx + 1 < len(starts) else n
        # strip trailing zero bytes belonging to the next start code
        while e > s and annexb[e - 1:e] == b"\x00":
            e -= 1
        if e > s:
            nals.append(annexb[s:e])

    # group NAL units into access units: new AU at each VCL NAL boundary
    VCL_H264 = {1, 2, 3, 4, 5}
    aus: list[bytes] = []
    current: list[bytes] = []
    prev_vcl_seen = False
    for nal in nals:
        h264_type = nal[0] & 0x1F
        hevc_type = (nal[0] >> 1) & 0x3F
        is_vcl = h264_type in VCL_H264 or hevc_type < 16
        if is_vcl and prev_vcl_seen:
            aus.append(b"".join(n + b"\x00\x00\x00\x01" for n in current))
            current = []
        current.append(nal)
        if is_vcl:
            prev_vcl_seen = True
    if current:
        aus.append(b"".join(n + b"\x00\x00\x00\x01" for n in current))
    return aus


def make_synthetic_stream(frames: list[bytes], codec: int = 1,
                          width: int = 1920, height: int = 1080,
                          fps: float = 30.0) -> bytes:
    """Build a complete Gear360 container around pre-encoded access units."""
    out = bytearray()
    out += make_ttts()
    out += make_vid0(codec=codec, width=width, height=height, fps=fps)
    out += b"AUD0" + u32be(8) + b"\x00" * 8
    out += make_movi_header()
    ts = 0
    for i, au in enumerate(frames):
        ftype = KEYFRAME_TYPE if i == 0 else NON_KEY_TYPE
        out += make_00vd(ts, au, frame_type=ftype)
        ts += 33
        if i % 5 == 0:
            out += make_00au(ts)
    return bytes(out)

def _find_ffmpeg() -> str | None:
    from shutil import which
    found = which("ffmpeg")
    if found:
        return found
    bundled = Path(__file__).resolve().parent.parent / "runtime" / "ffmpeg" / "ffmpeg"
    return str(bundled) if bundled.exists() else None
