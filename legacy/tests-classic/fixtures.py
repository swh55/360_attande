"""Shared test fixtures: synthetic Gear360 stream builder + temp DB.

The synthetic builder mirrors the REAL SM-R210 container structure — every
record convention verified against TecCheck/Gear360App Extractor.kt AND the
real capture (DG-20260903-001):
  TTTS:  FIXED 12-byte structure (tag + size + type); real camera sends size=0
  VID0:  size field is SELF-INCLUSIVE (record = 4+size; payload = size-4)
         with field offsets codec@16 scale@32 subTag("VD00")@36 fpsNum@44
         fpsDen@48 width@76 height@80 (relative to the byte after tag+size)
  AUD0:  same self-inclusive convention; subTag "AU00" at payload +12
  LIST/movi + 00VD/00AU media chunks: tag(4) + size(4) + ts(8) + payload(size)
  00VD payload: 00 00 00 01 + access unit + 5-byte trailer (payload[4] = first
  NAL header byte; 0x26/38 = HEVC IDR -> keyframe).
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
              fps: float = 30.0, scale: int = 1000, with_subtag: bool = True) -> bytes:
    """VID0 header — field offsets verified against Gear360App Extractor.kt.
    The size field is SELF-INCLUSIVE: record = 4 + size, payload = size - 4.
    (Real capture: VID0@12 size=96 with a 92-byte payload, LIST next.)"""
    payload = bytearray(96)
    struct.pack_into(">I", payload, 16, codec)
    struct.pack_into(">I", payload, 32, scale)
    if with_subtag:
        payload[36:40] = b"VD00"
    struct.pack_into(">I", payload, 44, int(round(fps * 1000)))
    struct.pack_into(">I", payload, 48, 1000)
    struct.pack_into(">I", payload, 76, width)
    struct.pack_into(">I", payload, 80, height)
    return b"VID0" + u32be(4 + len(payload)) + bytes(payload)


def make_ttts(type_flags: int = 1) -> bytes:
    """TTTS structure per Extractor.kt: a FIXED 12-byte structure
    (tag + size + type). The real camera sends size=0 and still includes the
    4-byte type word — the size field is informational and NEVER used to
    skip payload."""
    return b"TTTS" + u32be(0) + u32be(type_flags)


def make_aud0(channel: int = 2, scale: int = 0, codec: int = 0,
              sample_rate: int = 0) -> bytes:
    """AUD0 header in the real self-inclusive convention (record = 4 + size).
    Extractor.kt reads: size, channel, skip4, audioScale, subTag(=AU00),
    skip4, audioCodecType, skip32, sampleRate, skip20 -> 88 bytes total."""
    payload = bytearray(80)
    struct.pack_into(">I", payload, 0, channel)          # +8 abs
    struct.pack_into(">I", payload, 8, scale)            # +16 abs
    payload[12:16] = b"AU00"                             # +20 abs
    struct.pack_into(">I", payload, 16, codec)           # +24 abs
    struct.pack_into(">I", payload, 52, sample_rate)     # +60 abs
    return b"AUD0" + u32be(4 + len(payload)) + bytes(payload)


def make_movi_header() -> bytes:
    """Realistic LIST header: listType='movi', payload zone follows directly."""
    return b"LIST" + u32be(4) + b"movi"


def make_00vd(timestamp: int, au: bytes) -> bytes:
    """Video chunk with the real-camera framing:
    payload = 00 00 00 01 + AU + 5-byte trailer.
    `au` is a complete access unit WITHOUT a leading start code; its first byte
    is the first NAL header byte, which the parser reads as the keyframe
    marker (0x26/38 = HEVC IDR, 0x40/64 = VPS-led AU)."""
    body = b"\x00\x00\x00\x01" + au + b"\x00" * 5
    return b"00VD" + u32be(len(body)) + u64be(timestamp) + body


# first-NAL header bytes used by tests (HEVC semantics, parse-only — never decoded)
KEYFRAME_TYPE = 38      # HEVC IDR_W_RADL NAL header byte
NON_KEY_TYPE = 2        # HEVC TRAIL_R


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
                          fps: float = 30.0,
                          force_first_keyframe: bool = True) -> bytes:
    """Build a complete Gear360 container around access units.

    `frames` are access units WITHOUT leading start codes (their first byte is
    the first NAL header byte). With force_first_keyframe=True, frame 0 gets a
    0x26 marker prepended so the parser reports a keyframe (parse-only tests —
    pass False when the AUs will actually be decoded, e.g. golden tests)."""
    aus = list(frames)
    if force_first_keyframe and aus and aus[0][:1] not in (b"\x26", b"\x40"):
        aus[0] = bytes([KEYFRAME_TYPE]) + aus[0]
    out = bytearray()
    out += make_ttts()
    out += make_vid0(codec=codec, width=width, height=height, fps=fps)
    out += make_aud0()
    out += make_movi_header()
    ts = 0
    for au in aus:
        out += make_00vd(ts, au)
        ts += 33
        if ts % 165 == 33:      # audio every 5th frame (after frames 0, 5, 10, ...)
            out += make_00au(ts)
    return bytes(out)


def _find_ffmpeg() -> str | None:
    from shutil import which
    found = which("ffmpeg")
    if found:
        return found
    bundled = Path(__file__).resolve().parent.parent / "runtime" / "ffmpeg" / "ffmpeg"
    return str(bundled) if bundled.exists() else None
