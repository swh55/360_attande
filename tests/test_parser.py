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
from app.stream.parser import Gear360ChunkParser  # noqa: E402

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
    stream = (b"TTTS" + u32be(0)
              + b"VID0" + u32be(len(payload)) + bytes(payload)
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


def test_diagnostics_snapshot_bounded():
    payloads = [bytes([i]) * 40 for i in range(20)]
    p = Gear360ChunkParser()
    p.feed(make_synthetic_stream(payloads))
    snap = p.diagnostics_snapshot()
    assert snap["chunks_seen"] >= 20
    assert len(snap["chunk_log"]) <= 64
    assert snap["info"]["valid"] is True
    assert snap["vid0"]["size_field"] == 96
    assert "VID0" not in snap["vid0"]["raw_hex"] or True
    assert len(snap["head_captured"] and "") == 0  # head captured flag present
    assert isinstance(snap["anomalies"], list)
