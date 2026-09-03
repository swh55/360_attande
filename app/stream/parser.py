"""Gear 360 live-preview container parser (SM-R210 /livestream_high.avi).

Layout reference: TecCheck/Gear360App player/Extractor.kt (proven against real
hardware). The header zone is a run of big-endian FourCC-tagged structures:

  TTTS  tag(4) + size(4) + payload (size bytes; payload[0:4] = type flags,
        i.e. size COUNTS the type field -- Extractor.kt reads tag+size+type
        as a fixed 12-byte structure and never skips extra bytes)
  VID0  tag(4) + size(4) + payload:
          +16  u32be codec          (1 = HEVC; 0/other = h264/raw)
          +32  u32be timestamp_scale
          +36  u32be subTag         MUST be "VD00" (0x56443030) -- layout marker
          +44  u32be fps numerator
          +48  u32be fps denominator
          +76  u32be width
          +80  u32be height
  AUD0  tag(4) + size(4) + payload (+12: subTag "AU00")
  VR00  tag(4) + size(4) + payload (+4: subTag "VR00")
  LIST  tag(4) + size(4) (list type follows, e.g. "movi")
  movi  payload zone: 00VD / 00AU / 00VR chunks:
          tag(4) + size(4) + timestamp(8) + payload(size)
        00VD payload = 00 00 00 01 (Annex-B start code) + access unit, where
        payload[4] is the first NAL header byte (HEVC IDR = 0x26/38, VPS-led
        AU = 0x40/64 -> keyframes), plus a 5-byte per-chunk trailer that is
        NOT part of the bitstream (reference keeps size-5 bytes per chunk).

History: the first Hub implementation parsed VID0 at shifted offsets
(codec +20, scale +36, fps +52, dims +88) and read the VD00 subTag as
"scale" (1447309360) and the following LIST tag as "height" (1279873876).
The offsets above were verified against Extractor.kt and real-camera
evidence; app/diagnostics + tests guard this regression.

Robustness rules:
  * parsing is atomic -- a structure is consumed only when fully buffered;
  * the header zone re-synchronises by scanning for the next known tag;
  * implausible metadata is reported, never silently accepted;
  * bounded side-data is kept for the diagnostics engine (head bytes, chunk
    log, recent access-unit ring) without ever blocking the live path.
"""
from __future__ import annotations

import logging
import struct
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Generator, Optional

LOG = logging.getLogger("hub.stream")

TAG_TTTS = b"TTTS"
TAG_VID0 = b"VID0"
TAG_VD00 = b"VD00"
TAG_AUD0 = b"AUD0"
TAG_AU00 = b"AU00"
TAG_VR00 = b"VR00"
TAG_LIST = b"LIST"
TAG_MOVI = b"movi"
TAG_00VD = b"00VD"
TAG_00AU = b"00AU"
TAG_00VR = b"00VR"

KNOWN_MEDIA_TAGS = (TAG_00VD, TAG_00AU, TAG_00VR, TAG_TTTS)
KNOWN_TOP_TAGS = (TAG_TTTS, TAG_VID0, TAG_AUD0, TAG_VR00, TAG_LIST, TAG_MOVI)

# frame-type bytes that mark a keyframe inside a 00VD payload (Gear360App:
# payload[4] is the first NAL header byte; HEVC IDR_W_RADL = 0x26 = 38,
# VPS-led access unit = 0x40 = 64)
KEYFRAME_TYPES = (64, 38)

_MAX_HEADER = 32 * 1024 * 1024
_MAX_TTTS = 4096              # TTTS is a tiny fixed structure; larger = bogus
_MAX_CHUNK = 16 * 1024 * 1024
_COMPACT_THRESHOLD = 1024 * 1024
_HEAD_CAPTURE = 4096          # first bytes of the stream kept for inspection
_AU_RING_MAX = 12             # recent encoded access units for decoder probes
_AU_MAX_BYTES = 512 * 1024


@dataclass
class StreamInfo:
    codec: int = -1            # 1 => HEVC, otherwise H264
    width: int = 0
    height: int = 0
    frame_rate: float = 0.0
    timestamp_scale: int = 1
    valid: bool = False        # subTag verified AND values plausible
    subtag_ok: bool = False
    anomalies: list = field(default_factory=list)

    @property
    def codec_name(self) -> str:
        return "hevc" if self.codec == 1 else "h264"


@dataclass
class VideoFrame:
    timestamp: int
    payload: bytes             # encoded Annex-B access unit (starts with 00 00 00 01)
    keyframe: bool
    framing: str = "native_annexb"   # native_annexb | prefix_stripped


class ByteBuffer:
    """Thread-safe growable read buffer with compaction."""

    __slots__ = ("_buf", "_pos", "_lock")

    def __init__(self) -> None:
        self._buf = bytearray()
        self._pos = 0
        self._lock = threading.Lock()

    def append(self, data: bytes) -> None:
        with self._lock:
            self._buf.extend(data)

    def available(self) -> int:
        with self._lock:
            return len(self._buf) - self._pos

    def peek(self, n: int) -> bytes:
        with self._lock:
            end = self._pos + n
            if end > len(self._buf):
                raise EOFError
            return bytes(self._buf[self._pos:end])

    def read(self, n: int) -> bytes:
        with self._lock:
            end = self._pos + n
            if end > len(self._buf):
                raise EOFError
            out = bytes(self._buf[self._pos:end])
            self._pos = end
            self._maybe_compact()
            return out

    def find(self, needle: bytes) -> int:
        """Return index RELATIVE to the read position (matches discard_to)."""
        with self._lock:
            idx = bytes(self._buf).find(needle, self._pos)
            return -1 if idx < 0 else idx - self._pos

    def find_any(self, needles) -> tuple[int, bytes]:
        """Earliest occurrence of any needle, relative to read position."""
        with self._lock:
            data = bytes(self._buf[self._pos:])
            best, hit = -1, b""
            for n in needles:
                i = data.find(n)
                if i != -1 and (best == -1 or i < best):
                    best, hit = i, n
            return best, hit

    def discard_to(self, relative_index: int) -> None:
        with self._lock:
            self._pos += relative_index
            self._maybe_compact()

    def _maybe_compact(self) -> None:
        if self._pos > _COMPACT_THRESHOLD:
            del self._buf[:self._pos]
            self._pos = 0


class Gear360ChunkParser:
    """Stateful parser: feed network bytes, extract video access units."""

    # VID0 field offsets -- VERIFIED against TecCheck/Gear360App Extractor.kt
    # and real SM-R210 captures. Do NOT shift without a hardware capture that
    # proves it; the diagnostics engine + regression tests guard these.
    OFF_CODEC, OFF_SCALE, OFF_SUBTAG = 16, 32, 36
    OFF_FPS_NUM, OFF_FPS_DEN = 44, 48
    OFF_WIDTH, OFF_HEIGHT = 76, 80

    def __init__(self) -> None:
        self.buffer = ByteBuffer()
        self.info = StreamInfo()
        self.started = False           # reached movi payload zone
        self.chunks_seen = 0
        self.resyncs = 0
        self.bytes_seen = 0
        self.boundary_errors = 0
        self.vid0_seen_at: Optional[int] = None
        self.vid0_size_field: Optional[int] = None
        self.vid0_raw: bytes = b""             # first 160 bytes of VID0 payload
        self.ttts_size_fields: list = []
        self.head_bytes: bytes = b""           # first _HEAD_CAPTURE bytes
        self.first_00vd_head: bytes = b""      # first 32 bytes of first 00VD payload
        self.chunk_log: deque = deque(maxlen=256)
        self.au_ring: deque = deque(maxlen=_AU_RING_MAX)
        self.framing_stats: dict = {}
        self._anomalies: deque = deque(maxlen=64)

    # -- public API ---------------------------------------------------------
    def feed(self, data: bytes) -> list[VideoFrame]:
        self.bytes_seen += len(data)
        self.buffer.append(data)
        if len(self.head_bytes) < _HEAD_CAPTURE:
            self.head_bytes += data[: _HEAD_CAPTURE - len(self.head_bytes)]
        frames: list[VideoFrame] = []
        while True:
            try:
                frame = self._parse_one()
            except EOFError:
                break
            except ValueError as exc:
                self.resyncs += 1
                self._anomaly("parser_resync", str(exc))
                LOG.debug("Parser resync (%s)", exc)
                idx = self.buffer.find(TAG_00VD)
                if idx < 0 and not self.started:
                    idx = self.buffer.find(TAG_MOVI)
                    if idx >= 0:
                        self.buffer.discard_to(idx + len(TAG_MOVI))
                        self.started = True
                        continue
                if idx < 0:
                    avail = self.buffer.available()
                    if avail > 64:
                        self.buffer.discard_to(avail - 16)
                    break
                self.buffer.discard_to(idx)
                continue
            if frame is not None:
                frames.append(frame)
        return frames

    def reset(self) -> None:
        self.buffer = ByteBuffer()
        self.info = StreamInfo()
        self.started = False
        self.chunks_seen = 0
        self.resyncs = 0
        self.bytes_seen = 0
        self.boundary_errors = 0
        self.vid0_seen_at = None
        self.vid0_size_field = None
        self.vid0_raw = b""
        self.ttts_size_fields = []
        self.head_bytes = b""
        self.first_00vd_head = b""
        self.chunk_log = deque(maxlen=256)
        self.au_ring = deque(maxlen=_AU_RING_MAX)
        self.framing_stats = {}
        self._anomalies = deque(maxlen=64)

    def diagnostics_snapshot(self) -> dict:
        """Bounded structured state for the diagnostics engine / API."""
        info = self.info
        return {
            "info": {
                "codec": info.codec, "codec_name": info.codec_name,
                "width": info.width, "height": info.height,
                "frame_rate": info.frame_rate, "timestamp_scale": info.timestamp_scale,
                "valid": info.valid, "subtag_ok": info.subtag_ok,
            },
            "started": self.started,
            "chunks_seen": self.chunks_seen,
            "resyncs": self.resyncs,
            "bytes_seen": self.bytes_seen,
            "boundary_errors": self.boundary_errors,
            "vid0": {
                "seen_at": self.vid0_seen_at,
                "size_field": self.vid0_size_field,
                "raw_len": len(self.vid0_raw),
                "raw_hex": self.vid0_raw.hex(" "),
            },
            "ttts_size_fields": list(self.ttts_size_fields[-8:]),
            "framing": dict(self.framing_stats),
            "first_00vd_head_hex": self.first_00vd_head.hex(" "),
            "head_captured": len(self.head_bytes),
            "chunk_log": list(self.chunk_log)[-64:],
            "anomalies": list(self._anomalies),
            "au_ring_size": len(self.au_ring),
        }

    # -- internals ----------------------------------------------------------
    def _u32be(self, b: bytes) -> int:
        return struct.unpack(">I", b)[0]

    def _abs_offset(self) -> int:
        return max(0, self.bytes_seen - self.buffer.available())

    def _anomaly(self, key: str, detail: str) -> None:
        entry = {"key": key, "detail": detail, "ts": round(time.time(), 3)}
        self._anomalies.append(entry)
        if len(self.info.anomalies) < 10:
            self.info.anomalies.append(entry)
        LOG.warning("PARSER: %s — %s", key, detail)

    def _log_chunk(self, tag: bytes, offset: int, size: int, ts: int, boundary_ok: bool) -> None:
        self.chunk_log.append({
            "tag": tag.decode("ascii", "replace"), "offset": offset,
            "size": size, "timestamp": ts, "boundary_ok": bool(boundary_ok),
        })

    # ---- header zone -------------------------------------------------------
    def _parse_one(self) -> Optional[VideoFrame]:
        if not self.started:
            return self._parse_header_zone()
        return self._parse_movi_chunk()

    def _parse_header_zone(self) -> Optional[VideoFrame]:
        while True:
            avail = self.buffer.available()
            if avail < 4:
                raise EOFError
            tag = self.buffer.peek(4)
            if tag not in KNOWN_TOP_TAGS:
                idx, hit = self.buffer.find_any(KNOWN_TOP_TAGS)
                if idx < 0:
                    if avail < 256:
                        raise EOFError
                    self.buffer.discard_to(avail - 16)
                    raise EOFError          # wait for more bytes (atomic)
                if idx > 0:
                    self.resyncs += 1
                    self._anomaly("header_resync", f"skipped {idx} unknown bytes before {hit!r}")
                self.buffer.discard_to(idx)
                tag = hit

            if tag == TAG_MOVI:
                self.buffer.read(4)
                self.started = True
                self._log_chunk(TAG_MOVI, self._abs_offset(), 0, 0, True)
                LOG.info("STREAM: movi payload zone entered at offset 0x%x", self._abs_offset())
                return None

            if tag == TAG_TTTS:
                # Extractor.kt: TTTS = tag + size + payload(size bytes), where
                # the size field counts the 4-byte type flags -> total 8+size.
                # A size beyond _MAX_TTTS can never be real (real TTTS = 12B);
                # reject it instead of stalling the stream forever.
                if avail < 8:
                    raise EOFError
                size = self._u32be(self.buffer.peek(8)[4:8])
                if size > _MAX_TTTS:
                    self.buffer.discard_to(4)
                    self._anomaly("ttts_size_invalid", f"size={size}")
                    continue
                if avail < 8 + size:
                    raise EOFError
                self.buffer.read(8 + size)
                self.ttts_size_fields.append(size)
                self._log_chunk(TAG_TTTS, self._abs_offset(), size, 0, True)
                continue

            if tag in (TAG_VID0, TAG_AUD0, TAG_VR00):
                if avail < 8:
                    raise EOFError
                size = self._u32be(self.buffer.peek(8)[4:8])
                if size > _MAX_HEADER:
                    self.buffer.discard_to(4)
                    self._anomaly(f"{tag.decode('ascii', 'replace').lower()}_size_invalid", f"size={size}")
                    continue
                if avail < 8 + size:
                    raise EOFError
                payload = self.buffer.read(8 + size)[8:]
                if tag == TAG_VID0:
                    self.vid0_seen_at = self._abs_offset()
                    self.vid0_size_field = size
                    self.vid0_raw = payload[:160]
                    self._parse_vid0(payload)
                elif tag == TAG_AUD0:
                    sub = payload[12:16] if len(payload) >= 16 else b""
                    if sub and sub != TAG_AU00:
                        self._anomaly("aud0_subtag", f"subTag={sub!r} (expected AU00)")
                elif tag == TAG_VR00:
                    sub = payload[4:8] if len(payload) >= 8 else b""
                    if sub and sub != TAG_VR00:
                        self._anomaly("vr00_subtag", f"subTag={sub!r} (expected VR00)")
                self._log_chunk(tag, self._abs_offset(), size, 0, True)
                continue

            if tag == TAG_LIST:
                if avail < 8:
                    raise EOFError
                size = self._u32be(self.buffer.peek(8)[4:8])
                if size > _MAX_HEADER:
                    self.buffer.discard_to(4)
                    self._anomaly("list_size_invalid", f"size={size}")
                    continue
                self.buffer.read(8)   # tag + size; list type handled on next pass
                self._log_chunk(TAG_LIST, self._abs_offset(), size, 0, True)
                continue

            # unreachable (tag membership checked above)
            raise ValueError(f"unhandled header tag {tag!r}")

    def _parse_vid0(self, payload: bytes) -> None:
        info = self.info
        if len(payload) < self.OFF_HEIGHT + 4:
            self._anomaly("vid0_too_short", f"payload={len(payload)}B (<{self.OFF_HEIGHT + 4})")
            return
        codec = self._u32be(payload[self.OFF_CODEC:self.OFF_CODEC + 4])
        scale = self._u32be(payload[self.OFF_SCALE:self.OFF_SCALE + 4])
        subtag = payload[self.OFF_SUBTAG:self.OFF_SUBTAG + 4]
        num = self._u32be(payload[self.OFF_FPS_NUM:self.OFF_FPS_NUM + 4])
        den = self._u32be(payload[self.OFF_FPS_DEN:self.OFF_FPS_DEN + 4])
        width = self._u32be(payload[self.OFF_WIDTH:self.OFF_WIDTH + 4])
        height = self._u32be(payload[self.OFF_HEIGHT:self.OFF_HEIGHT + 4])
        fps = (num / den) if den else 0.0

        subtag_ok = subtag == TAG_VD00
        dims_ok = 64 <= width <= 8192 and 64 <= height <= 8192 and width % 2 == 0 and height % 2 == 0
        fps_ok = 0.1 <= fps <= 240.0
        scale_ok = 1 <= scale <= 10_000_000

        info.codec = codec
        info.timestamp_scale = scale or 1
        info.frame_rate = fps
        info.width, info.height = width, height
        info.subtag_ok = subtag_ok
        info.valid = bool(subtag_ok and dims_ok and fps_ok and scale_ok)

        if not subtag_ok:
            self._anomaly("vid0_subtag", f"subTag={subtag!r} (expected VD00) — VID0 field layout suspect")
        if not dims_ok:
            self._anomaly("vid0_dims", f"width={width} height={height}")
        if not fps_ok:
            self._anomaly("vid0_fps", f"fps={fps:.3f} (raw {num}/{den})")
        if not scale_ok:
            self._anomaly("vid0_scale", f"scale={scale}")
        # structured log: raw + normalized values, never claims success when implausible
        LOG.info(
            "STREAM: VID0 at offset 0x%x size_field=%s codec_raw=%s codec=%s "
            "dims=%sx%s fps_raw=%s/%s fps=%.3f scale=%s subtag=%s valid=%s",
            self.vid0_seen_at or 0, self.vid0_size_field, codec, info.codec_name,
            width, height, num, den, fps, scale, subtag, info.valid,
        )

    # ---- movi zone ----------------------------------------------------------
    def _parse_movi_chunk(self) -> Optional[VideoFrame]:
        avail = self.buffer.available()
        if avail < 4:
            raise EOFError
        tag = self.buffer.peek(4)
        if tag not in KNOWN_MEDIA_TAGS:
            idx, hit = self.buffer.find_any(KNOWN_MEDIA_TAGS)
            if idx < 0:
                raise ValueError(f"unexpected chunk tag {tag!r}")
            if idx > 0:
                self.resyncs += 1
                self._anomaly("movi_resync", f"skipped {idx} bytes to {hit!r}")
            self.buffer.discard_to(idx)
            tag = hit

        if self.buffer.available() < 16:
            raise EOFError
        start_abs = self._abs_offset()
        hdr = self.buffer.peek(16)
        size = struct.unpack(">I", hdr[4:8])[0]
        ts = struct.unpack(">Q", hdr[8:16])[0]
        if size <= 0 or size > _MAX_CHUNK:
            raise ValueError(f"invalid chunk size {size}")
        if self.buffer.available() < 16 + size:
            raise EOFError
        self.buffer.read(16)
        payload = self.buffer.read(size)
        self.chunks_seen += 1

        nxt = b""
        if self.buffer.available() >= 4:
            nxt = self.buffer.peek(4)
        boundary_ok = nxt in KNOWN_MEDIA_TAGS or self.buffer.available() < 4
        if not boundary_ok:
            self.boundary_errors += 1
        self._log_chunk(tag, start_abs, size, ts, boundary_ok)

        if tag != TAG_00VD:
            return None
        if len(payload) < 8:
            self._anomaly("vd_short", f"payload={len(payload)}B")
            return None
        if not self.first_00vd_head:
            self.first_00vd_head = payload[:32]
        frame_type = payload[4]

        if payload[:4] == b"\x00\x00\x00\x01" or payload[:3] == b"\x00\x00\x01":
            encoded = payload[:-5] if len(payload) > 5 else b""
            framing = "native_annexb"
            trailer = payload[-5:]
            if trailer != b"\x00" * 5:
                self._anomaly("vd_trailer_nonzero", f"last5={trailer.hex(' ')} (stripped per reference)")
        else:
            encoded = payload[5:-5] if len(payload) > 10 else payload[5:]
            framing = "prefix_stripped"
        self.framing_stats[framing] = self.framing_stats.get(framing, 0) + 1
        if not encoded:
            self._anomaly("vd_empty_au", f"payload={len(payload)}B framing={framing}")
            return None
        if len(encoded) <= _AU_MAX_BYTES:
            self.au_ring.append(encoded)
        return VideoFrame(
            timestamp=ts, payload=encoded,
            keyframe=frame_type in KEYFRAME_TYPES, framing=framing,
        )


def iter_camera_bytes(cfg: dict, stop_event: threading.Event,
                      on_response=None) -> Generator[bytes, None, None]:
    """Yield raw bytes from the camera live-preview HTTP endpoint with retries.

    `on_response(status, headers)` is called once per HTTP attempt so callers
    can track transport state without parsing the byte flow.
    """
    import requests

    host = cfg["host"]
    port = int(cfg["port"])
    path = cfg.get("path", "/livestream_high.avi")
    timeout = float(cfg.get("request_timeout_seconds", 8))
    reconnect = float(cfg.get("reconnect_seconds", 1))
    url = f"http://{host}:{port}{path}"
    session = requests.Session()
    session.headers.update({"User-Agent": "Gear360Hub/1.0", "Accept": "*/*"})
    while not stop_event.is_set():
        try:
            LOG.info("Connecting to %s", url)
            with session.get(url, stream=True, timeout=(3, timeout), headers={"Range": "bytes=0-"}) as r:
                r.raise_for_status()
                LOG.info("STREAM: HTTP %s, Content-Type=%s", r.status_code, r.headers.get("Content-Type"))
                if on_response is not None:
                    try:
                        on_response(r.status_code, dict(r.headers))
                    except Exception:
                        LOG.debug("on_response callback failed", exc_info=True)
                for chunk in r.iter_content(chunk_size=64 * 1024):
                    if stop_event.is_set():
                        return
                    if chunk:
                        yield chunk
        except Exception as exc:
            LOG.warning("Camera stream ended: %s", exc)
            if stop_event.wait(reconnect):
                return
