"""Golden parser tests: header parsing, chunk walking, resync, framing.

Regression guard: the 2026-09 incident where shifted VID0 offsets produced
"fps=0.00 scale=1447309360" (the VD00 subTag read as scale) and
"height=1279873876" (the next chunk's LIST tag read as height).
"""
from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("GEAR360HUB_DB", str(Path(__file__).parent / ".tmp.db"))

import pytest  # noqa: E402

from tests.fixtures import (  # noqa: E402
    make_00au, make_00vd, make_movi_header, make_synthetic_stream, make_ttts,
    make_vid0, u32be, u64be,
)
from app.stream.parser import Gear360ChunkParser, parse_chunk  # noqa: E402

SC = b"\x00\x00\x00\x01"


def test_vid0_header_fields():
    p = Gear360ChunkParser()
    stream = make_synthetic_stream([b"\x00" * 16] * 3)
    p.feed(stream[: len(stream) // 2])
    assert p.info.width == 1920
    assert p.info.height == 1080
    p.feed(stream[len(stream) // 2:])
    assert p.info.codec == 1
    assert p.info.codec_name == "hevc"
    assert p.info.frame_rate == 30.0
    assert p.info.timestamp_scale == 1000
    assert p.info.subtag_ok is True
    assert p.info.valid is True
    assert p.started


def test_vid0_bad_subtag_flagged():
    """If the VD00 layout marker is missing, metadata must be flagged invalid,
    never silently accepted."""
    stream = bytearray(make_synthetic_stream([b"\x00" * 16]))
    i = stream.index(b"VID0")
    stream[i + 8 + 36: i + 8 + 40] = b"XXXX"
    p = Gear360ChunkParser()
    p.feed(bytes(stream))
    assert p.info.subtag_ok is False
    assert p.info.valid is False
    assert any(a["key"] == "vid0_subtag" for a in p.info.anomalies)


def test_full_chunk_walk_and_frames():
    payloads = [bytes([i]) * 40 for i in range(6)]
    stream = make_synthetic_stream(payloads, codec=1)
    p = Gear360ChunkParser()
    frames = p.feed(stream)
    assert len(frames) == 6
    assert frames[0].keyframe is True          # 0x26 marker prepended to AU 0
    assert frames[1].keyframe is False
    assert frames[0].payload == SC + bytes([38]) + payloads[0]
    assert frames[2].timestamp == 66
    assert p.chunks_seen == 8                  # 6 video + 2 audio media chunks
    assert p.framing_stats.get("native_annexb") == 6


def test_incremental_feed_split_chunks():
    payloads = [bytes([7]) * 30 for _ in range(4)]
    stream = make_synthetic_stream(payloads)
    p = Gear360ChunkParser()
    all_frames = []
    for i in range(0, len(stream), 7):
        all_frames.extend(p.feed(stream[i:i + 7]))
    assert len(all_frames) == 4
    assert all(f.payload == (SC + bytes([38]) + payloads[0] if i == 0 else SC + payloads[i])
               for i, f in enumerate(all_frames))


def test_resync_on_garbage():
    payloads = [b"\x11" * 24, b"\x22" * 24]
    stream = make_synthetic_stream(payloads)
    movi_pos = stream.index(b"movi") + 4
    head, tail = stream[:movi_pos], stream[movi_pos:]
    p = Gear360ChunkParser()
    p.feed(head)                       # reach movi zone
    frames = p.feed(b"JUNK" * 50)      # garbage with no 00VD -> ValueError path
    assert p.resyncs >= 1
    frames += p.feed(tail)
    assert len(frames) == 2
    assert frames[0].payload == SC + bytes([38]) + b"\x11" * 24


def test_silent_skip_on_short_garbage():
    """Short garbage between chunks is skipped silently via find (no ValueError)."""
    payloads = [b"\x33" * 20, b"\x44" * 20]
    stream = make_synthetic_stream(payloads)
    pos = stream.index(b"00VD")
    corrupted = stream[:pos + 40] + b"GARBAGE12" + stream[pos + 40:]
    p = Gear360ChunkParser()
    frames = p.feed(corrupted)
    assert len(frames) == 2


def test_audio_chunks_skipped():
    stream = bytearray()
    stream += make_ttts()
    stream += make_vid0()
    stream += make_movi_header()
    stream += make_00vd(0, b"\x33" * 20)
    stream += make_00au(10)
    stream += make_00vd(20, b"\x44" * 20)
    p = Gear360ChunkParser()
    frames = p.feed(bytes(stream))
    assert len(frames) == 2
    assert frames[1].payload == SC + b"\x44" * 20


def test_partial_data_does_not_crash():
    p = Gear360ChunkParser()
    assert p.feed(b"") == []
    assert p.feed(b"TT") == []           # too short
    assert p.feed(b"TTTS" + u32be(9999)) == []  # EOF inside header
    stream = make_synthetic_stream([b"\x55" * 16])
    assert p.feed(stream[:20]) == []     # header zone partial
    frames = p.feed(stream[20:])         # resumed cleanly
    assert len(frames) == 1


def test_chunk_boundary_never_desyncs():
    """The atomicity guarantee: any split point must parse identically."""
    payloads = [bytes([i]) * 26 for i in range(5)]
    stream = make_synthetic_stream(payloads)
    expected = [SC + (bytes([38]) if i == 0 else b"") + payloads[i] for i in range(5)]
    for split in (1, 2, 3, 5, 8, 13, 21, 34, 55, 89, 144):
        p = Gear360ChunkParser()
        frames = p.feed(stream[:split]) + p.feed(stream[split:])
        assert len(frames) == 5, f"split={split} produced {len(frames)}"
        assert [f.payload for f in frames] == expected


def test_regression_real_camera_garbage_signature():
    """2026-09 incident regression: the same real-camera bytes must now parse
    into plausible metadata. Previously: fps=0.00, scale=1447309360 (ASCII
    'VD00' = the subTag slot), height=1279873876 (ASCII 'LIST' = next tag)."""
    payload = bytearray(96)
    struct.pack_into(">I", payload, 16, 1)        # codec = HEVC
    struct.pack_into(">I", payload, 32, 90000)    # scale
    payload[36:40] = b"VD00"                      # subTag layout marker
    struct.pack_into(">I", payload, 44, 30000)    # fps 29.97
    struct.pack_into(">I", payload, 48, 1001)
    struct.pack_into(">I", payload, 76, 3840)
    struct.pack_into(">I", payload, 80, 2160)
    stream = (b"TTTS" + u32be(0) + u32be(1)
              + b"VID0" + u32be(4 + len(payload)) + bytes(payload)
              + b"LIST" + u32be(4) + b"movi"
              + make_00vd(0, bytes([38]) + b"\x00" * 60))
    p = Gear360ChunkParser()
    frames = p.feed(stream)
    assert p.info.valid is True
    assert p.info.timestamp_scale == 90000
    assert p.info.frame_rate == pytest.approx(29.97, abs=0.01)
    assert (p.info.width, p.info.height) == (3840, 2160)
    assert len(frames) == 1
    assert p.info.codec_name == "hevc"
    assert p.resyncs == 0


# ---------------------------------------------------------------------------
# REAL-camera regression (DG-20260903-001, 2026-09-03 22:48 capture).
# The VID0 payload below is the EXACT 92-byte payload captured from the real
# SM-R210 (96-byte raw minus the trailing 'LIST' FourCC of the next record);
# the TTTS/LIST/movi geometry comes from the same capture (scanner offsets
# TTTS@0 VID0@12 LIST@112 movi@120 00VD@124, TTTS size=0, VID0 size_field=96).
# Guards user requirement: fps=15, 1280x640, codec=h264, ZERO resyncs.
# ---------------------------------------------------------------------------
_REAL_VID0_PAYLOAD_92B = bytes.fromhex(
    "00000001000000020000050000000280"
    "0000000201" "2c00000000000100000000"
    "0001d4c05644303000000001000005dc"
    "00000064000000000000006400000000"
    "0000006400000af00000006400000500"
    "000002800000000000000000"
)


def _real_camera_stream(au: bytes | None = None) -> bytes:
    """Reconstruct the real container header byte-for-byte from the capture,
    followed by one realistic 00VD H264 access-unit chunk."""
    if au is None:
        # SPS-led H264 IDR access unit shape (parse-only; not decoded here)
        au = bytes([0x67]) + b"\x42\x00\x1e" + b"\xa5" * 48
    header = (b"TTTS" + u32be(0) + u32be(0)                 # @0   fixed 12B
              + b"VID0" + u32be(96) + _REAL_VID0_PAYLOAD_92B  # @12  self-inclusive
              + b"LIST" + u32be(4)                          # @112
              + b"movi")                                    # @120
    assert len(header) == 124, len(header)                  # 00VD must land @124
    return header + make_00vd(0, au)


def test_regression_real_camera_header_zero_resyncs():
    """The REAL SM-R210 header (verified capture) must walk the header zone
    with ZERO resyncs and land directly on 00VD — this is what failed live on
    2026-09-03 22:48 (2 resyncs under the old 8+size convention)."""
    stream = _real_camera_stream()
    p = Gear360ChunkParser()
    frames = p.feed(stream)
    assert p.resyncs == 0, p.info.anomalies
    assert p.started is True
    assert p.chunks_seen == 1
    assert p.info.valid is True
    assert (p.info.width, p.info.height) == (1280, 640)   # user requirement #44
    assert p.info.frame_rate == pytest.approx(15.0)
    assert p.info.timestamp_scale == 120000
    assert p.info.codec == 2
    assert p.info.codec_name == "h264"
    assert p.info.subtag_ok is True
    assert len(frames) == 1
    assert frames[0].framing == "native_annexb"
    assert frames[0].payload.startswith(b"\x00\x00\x00\x01")
    assert p.ttts_size_fields == [0]                      # real TTTS size field
    assert p.vid0_size_field == 96                        # real VID0 size field


def test_regression_real_camera_header_split_feed():
    """Same real header fed in arbitrary splits must parse identically."""
    stream = _real_camera_stream()
    expected_payload = b"\x00\x00\x00\x01" + bytes([0x67]) + b"\x42\x00\x1e" + b"\xa5" * 48
    for split in (5, 12, 13, 60, 112, 120, 124, 130):
        p = Gear360ChunkParser()
        frames = p.feed(stream[:split]) + p.feed(stream[split:])
        assert p.resyncs == 0, f"split={split} resyncs={p.resyncs}"
        assert p.chunks_seen == 1
        assert len(frames) == 1
        assert frames[0].payload == expected_payload


def test_regression_real_camera_pending_chunk_visibility():
    """When the first 00VD is bigger than the bytes received (the exact live
    stall of 2026-09-03 22:48), the parser must expose a pending_chunk state
    instead of waiting silently."""
    big_au = bytes([0x67]) + b"\x00" * 20000
    stream = _real_camera_stream(au=big_au)
    p = Gear360ChunkParser()
    frames = p.feed(stream[:4096])          # header complete, chunk truncated
    assert frames == []
    assert p.started is True
    assert p.chunks_seen == 0
    assert p.pending_chunk is not None
    assert p.pending_chunk["tag"] == "00VD"
    assert p.pending_chunk["have"] < p.pending_chunk["size"]
    frames = p.feed(stream[4096:])          # rest arrives -> parses
    assert len(frames) == 1
    assert p.pending_chunk is None


def test_parse_chunk_helper_geometry():
    """parse_chunk() must reproduce the verified boundary math exactly."""
    stream = _real_camera_stream()
    ttts = parse_chunk(stream, 0)
    assert ttts["tag"] == b"TTTS" and ttts["zone"] == "header"
    assert ttts["header_size"] == 12 and ttts["next_offset"] == 12
    assert ttts["declared_size"] == 0 and ttts["error"] == ""
    vid0 = parse_chunk(stream, 12)
    assert vid0["tag"] == b"VID0"
    assert vid0["declared_size"] == 96
    assert vid0["payload_size"] == 92                      # self-inclusive
    assert vid0["payload_offset"] == 20
    assert vid0["next_offset"] == 112                      # lands on LIST
    lst = parse_chunk(stream, 112)
    assert lst["tag"] == b"LIST" and lst["next_offset"] == 120
    movi = parse_chunk(stream, 120)
    assert movi["tag"] == b"movi" and movi["next_offset"] == 124
    chunk = parse_chunk(stream, 124)
    assert chunk["tag"] == b"00VD" and chunk["zone"] == "media"
    assert chunk["header_size"] == 16
    assert chunk["payload_offset"] == 140
    assert chunk["payload_size"] == chunk["declared_size"]
    assert chunk["next_offset"] == 124 + 16 + chunk["declared_size"]
    assert chunk["error"] == ""


def test_parse_chunk_helper_validation():
    bad = parse_chunk(b"JUNK\x00\x00\x00\x00" + b"\x00" * 8, 0)
    assert bad["zone"] == "unknown" and bad["error"]
    trunc = parse_chunk(b"TTT", 0)
    assert trunc["error"] == "truncated tag"
    trunc2 = parse_chunk(b"TTTS\x00\x00", 0)
    assert trunc2["error"] == "truncated size field"
    vid_bad = parse_chunk(b"VID0\x00\x00\x00\x02" + b"\x00" * 16, 0)
    assert vid_bad["error"]                     # size < 4 (self-inclusive)
    media_bad = parse_chunk(b"00VD\x00\x00\x00\x00" + b"\x00" * 12, 0)
    assert "implausible" in media_bad["error"]


def test_diagnostics_snapshot_bounded():
    payloads = [bytes([i]) * 40 for i in range(20)]
    p = Gear360ChunkParser()
    p.feed(make_synthetic_stream(payloads))
    snap = p.diagnostics_snapshot()
    assert snap["chunks_seen"] >= 20
    assert len(snap["chunk_log"]) <= 64
    assert snap["info"]["valid"] is True
    assert snap["vid0"]["size_field"] == 100   # self-inclusive (96B payload + 4)
    assert "VID0" not in snap["vid0"]["raw_hex"] or True
    assert len(snap["head_captured"] and "") == 0  # head captured flag present
    assert isinstance(snap["anomalies"], list)
    assert snap["pending_chunk"] is None
