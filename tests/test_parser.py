"""Golden parser tests: header parsing, chunk walking, resync, framing."""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("GEAR360HUB_DB", str(Path(__file__).parent / ".tmp.db"))

from tests.fixtures import (  # noqa: E402
    make_00au, make_00vd, make_movi_header, make_synthetic_stream, make_ttts,
    make_vid0, u32be, u64be,
)
from app.stream.parser import Gear360ChunkParser  # noqa: E402


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
    assert p.started


def test_full_chunk_walk_and_frames():
    payloads = [bytes([i]) * 40 for i in range(6)]
    stream = make_synthetic_stream(payloads, codec=1)
    p = Gear360ChunkParser()
    frames = p.feed(stream)
    assert len(frames) == 6
    assert frames[0].keyframe is True
    assert frames[1].keyframe is False
    assert frames[0].payload == payloads[0]
    assert frames[2].timestamp == 66
    assert p.chunks_seen == 8  # 6 video + 2 audio media chunks


def test_incremental_feed_split_chunks():
    payloads = [bytes([7]) * 30 for _ in range(4)]
    stream = make_synthetic_stream(payloads)
    p = Gear360ChunkParser()
    all_frames = []
    # feed 7 bytes at a time to stress split tags
    for i in range(0, len(stream), 7):
        all_frames.extend(p.feed(stream[i:i + 7]))
    assert len(all_frames) == 4
    assert all(f.payload == payloads[i] for i, f in enumerate(all_frames))


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
    assert frames[0].payload == b"\x11" * 24


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
    assert frames[1].payload == b"\x44" * 20


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
    for split in (1, 2, 3, 5, 8, 13, 21, 34, 55, 89, 144):
        p = Gear360ChunkParser()
        frames = p.feed(stream[:split]) + p.feed(stream[split:])
        assert len(frames) == 5, f"split={split} produced {len(frames)}"
        assert [f.payload for f in frames] == payloads
