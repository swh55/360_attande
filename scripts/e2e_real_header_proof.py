"""E2E proof: REAL camera header (DG-20260903-001 capture) + real H264 AUs
-> Gear360ChunkParser -> FFmpegDecoder -> decoded frames.

Mirrors exactly what the SM-R210 sends on /livestream_high.avi:
TTTS(12B, size=0) + VID0(size=96 self-inclusive, 1280x640@15fps h264)
+ LIST/movi + 00VD chunks (start code + AU + 5-byte trailer).

Run: python3 scripts/e2e_real_header_proof.py
"""
import os
import struct
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["GEAR360HUB_DB"] = os.path.join(os.path.dirname(__file__), ".e2e.db")

from app.stream.parser import Gear360ChunkParser  # noqa: E402
from app.media.decoder import FFmpegDecoder  # noqa: E402

VID0_PAYLOAD = bytes.fromhex(
    "00000001000000020000050000000280"
    "00000002012c00000000000100000000"
    "0001d4c05644303000000001000005dc"
    "00000064000000000000006400000000"
    "0000006400000af00000006400000500"
    "000002800000000000000000")
assert len(VID0_PAYLOAD) == 92

W, H, FPS = 1280, 640, 15

# ---- generate a REAL h264 elementary stream at 1280x640 @15fps -------------
pattern = subprocess.run(
    ["ffmpeg", "-hide_banner", "-loglevel", "error",
     "-f", "lavfi", "-i", f"testsrc2=size={W}x{H}:rate={FPS}",
     "-t", "2", "-c:v", "libx264", "-preset", "ultrafast",
     "-x264-params", "keyint=15:min-keyint=15", "-pix_fmt", "yuv420p",
     "-f", "h264", "-"],
    capture_output=True, check=True).stdout
assert pattern[:4] == b"\x00\x00\x00\x01", "expected Annex-B"
print(f"generated {len(pattern)} bytes of real H264 ({pattern[:5].hex(' ')})")


# ---- split into access units ------------------------------------------------
def split_aus(annexb: bytes) -> list[bytes]:
    starts, i, n = [], 0, len(annexb)
    while i < n - 3:
        if annexb[i:i + 4] == b"\x00\x00\x00\x01":
            starts.append(i + 4)
            i += 4
        elif annexb[i:i + 3] == b"\x00\x00\x01":
            starts.append(i + 3)
            i += 3
        else:
            i += 1
    aus = []
    for idx, s in enumerate(starts):
        e = starts[idx + 1] if idx + 1 < len(starts) else n
        while e > s and annexb[e - 1:e] == b"\x00":
            e -= 1
        if e > s:
            aus.append(annexb[s:e])
    # group into access units at VCL boundaries (h264 types 1-5)
    out, cur, prev_vcl = [], [], False
    for nal in aus:
        t = nal[0] & 0x1F
        vcl = t in (1, 2, 3, 4, 5)
        if vcl and prev_vcl:
            out.append(b"".join(x + b"\x00\x00\x00\x01" for x in cur))
            cur = []
        cur.append(nal)
        prev_vcl = prev_vcl or vcl
    if cur:
        out.append(b"".join(x + b"\x00\x00\x00\x01" for x in cur))
    return out


aus = split_aus(pattern)
print(f"split into {len(aus)} access units; AU sizes {[len(a) for a in aus][:8]}")


# ---- wrap in the REAL container ---------------------------------------------
def chunk_00vd(ts: int, au: bytes) -> bytes:
    body = b"\x00\x00\x00\x01" + au + b"\x00" * 5
    return b"00VD" + struct.pack(">I", len(body)) + struct.pack(">Q", ts) + body


stream = (b"TTTS" + struct.pack(">II", 0, 0)
          + b"VID0" + struct.pack(">I", 96) + VID0_PAYLOAD
          + b"LIST" + struct.pack(">I", 4) + b"movi")
assert len(stream) == 124
ts = 0
for au in aus:
    stream += chunk_00vd(ts, au)
    ts += 6666  # 15fps at scale 100000

print(f"container: {len(stream)} bytes, header zone 124 bytes")

# ---- parse (in 8KB increments, like the camera delivers) --------------------
p = Gear360ChunkParser()
frames = []
step = 8192
for i in range(0, len(stream), step):
    frames.extend(p.feed(stream[i:i + step]))

print(f"parser: resyncs={p.resyncs} chunks_seen={p.chunks_seen} "
      f"frames={len(frames)} bytes_seen={p.bytes_seen}")
print(f"metadata: {p.info.codec_name} {p.info.width}x{p.info.height} "
      f"@{p.info.frame_rate}fps scale={p.info.timestamp_scale} valid={p.info.valid}")
assert p.resyncs == 0, "real header must parse with ZERO resyncs"
assert (p.info.width, p.info.height) == (1280, 640)
assert p.info.frame_rate == 15.0
assert p.info.codec_name == "h264"
assert p.chunks_seen == len(aus)

# ---- decode with the bundled FFmpegDecoder ----------------------------------
dec = FFmpegDecoder(p.info.codec_name, p.info.width, p.info.height,
                    ffmpeg_path="/usr/bin/ffmpeg")
dec.start()
decoded = 0
first = None
for f in frames:
    for img in dec.write(f.payload):
        decoded += 1
        if first is None:
            first = img
if first is None:
    time.sleep(0.5)
    while True:
        try:
            img = dec._out_q.get_nowait()
        except Exception:
            break
        decoded += 1
        if first is None:
            first = img

if first is not None:
    print(f"FIRST DECODED FRAME: {first.shape} dtype={first.dtype} "
          f"mean={first.mean():.1f} std={first.std():.1f}")
print(f"DECODED FRAMES TOTAL: {decoded}")
assert decoded > 0, "decoder must produce frames from parser AUs"
dec.close()

print("")
print("=== E2E PROOF PASSED: real header -> parser -> FFmpeg -> frames ===")
print(f"  resyncs=0, chunks={p.chunks_seen}, AUs={len(frames)}, decoded={decoded}")
