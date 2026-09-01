"""Gear 360 live-preview container parser.

Parses the custom chunk structure used by the SM-R210 live preview endpoint
(`livestream_high.avi`): TTTS / VID0 / AUD0 / VR00 headers, LIST, MOVI, then a
continuous run of 00VD (video access units), 00AU (audio) and 00VR chunks.

Design mirrors the public Gear360App extractor (ExoplayerActivity.kt /
Extractor.kt) but is implemented cleanly for a threaded server pipeline:
  feed(bytes) -> list[VideoFrame]

Robustness: on any unexpected tag the parser resynchronises by scanning forward
to the next 00VD / movi tag instead of failing the stream.
"""
from __future__ import annotations

import logging
import struct
import threading
from dataclasses import dataclass, field
from typing import Generator, Optional

LOG = logging.getLogger("hub.stream")

TAG_TTTS = b"TTTS"
TAG_VID0 = b"VID0"
TAG_AUD0 = b"AUD0"
TAG_VR00 = b"VR00"
TAG_LIST = b"LIST"
TAG_MOVI = b"movi"
TAG_00VD = b"00VD"
TAG_00AU = b"00AU"
TAG_00VR = b"00VR"

# frame type bytes that mark a keyframe inside a 00VD payload (Gear360App)
KEYFRAME_TYPES = (64, 38)
_MAX_HEADER = 32 * 1024 * 1024
_MAX_CHUNK = 16 * 1024 * 1024
_COMPACT_THRESHOLD = 1024 * 1024


@dataclass
class StreamInfo:
    codec: int = -1            # 1 => HEVC, otherwise H264
    width: int = 0
    height: int = 0
    frame_rate: float = 0.0
    timestamp_scale: int = 1

    @property
    def codec_name(self) -> str:
        return "hevc" if self.codec == 1 else "h264"


@dataclass
class VideoFrame:
    timestamp: int
    payload: bytes     # encoded access unit (no frame-type prefix)
    keyframe: bool


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

    def __init__(self) -> None:
        self.buffer = ByteBuffer()
        self.info = StreamInfo()
        self.started = False           # reached movi payload zone
        self.chunks_seen = 0
        self.resyncs = 0
        self.bytes_seen = 0

    # -- public API ---------------------------------------------------------
    def feed(self, data: bytes) -> list[VideoFrame]:
        self.bytes_seen += len(data)
        self.buffer.append(data)
        frames: list[VideoFrame] = []
        while True:
            try:
                frame = self._parse_one()
            except EOFError:
                break
            except ValueError as exc:
                self.resyncs += 1
                LOG.debug("Parser resync (%s)", exc)
                idx = self.buffer.find(TAG_00VD)
                if idx < 0:
                    idx = self.buffer.find(TAG_MOVI)
                    if idx >= 0:
                        self.buffer.discard_to(idx + len(TAG_MOVI))
                        self.started = True
                        continue
                if idx < 0:
                    # keep a small tail so a split tag can still be matched
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

    # -- internals ----------------------------------------------------------
    def _u32be(self, b: bytes) -> int:
        return struct.unpack(">I", b)[0]

    def _parse_vid0_header(self, size: int) -> None:
        if self.buffer.available() < size:
            raise EOFError
        payload = self.buffer.read(size)
        if len(payload) < 96:
            return
        try:
            # Field offsets follow Gear360App Extractor.kt
            self.info.codec = self._u32be(payload[20:24])
            self.info.timestamp_scale = self._u32be(payload[36:40]) or 1
            num = self._u32be(payload[52:56])
            den = self._u32be(payload[56:60])
            self.info.frame_rate = (num / den) if den else 0.0
            self.info.width = self._u32be(payload[88:92])
            self.info.height = self._u32be(payload[92:96])
            LOG.info(
                "VID0: codec=%s %sx%s @%.2ffps scale=%s",
                self.info.codec_name, self.info.width, self.info.height,
                self.info.frame_rate, self.info.timestamp_scale,
            )
        except Exception:
            LOG.debug("VID0 header parse failed", exc_info=True)

    def _read_sized(self, limit: int, what: str) -> int:
        if self.buffer.available() < 4:
            raise EOFError
        size = self._u32be(self.buffer.read(4))
        if size > limit:
            raise ValueError(f"invalid {what} size {size}")
        return size

    def _parse_one(self) -> Optional[VideoFrame]:
        """Parse exactly one structure. ATOMIC: never consumes bytes unless the
        whole structure is buffered, so chunk boundaries can never desync us."""
        if not self.started:
            # Scan the top-level structure until we reach the movi payload zone.
            while True:
                avail = self.buffer.available()
                if avail < 4:
                    raise EOFError
                tag = self.buffer.peek(4)
                if tag == TAG_MOVI:
                    self.buffer.read(4)
                    self.started = True
                    return None
                if tag == TAG_TTTS:
                    if avail < 12:
                        raise EOFError
                    size = self._u32be(self.buffer.peek(8)[4:8])
                    if size > _MAX_HEADER:
                        raise ValueError(f"invalid TTTS size {size}")
                    if avail < 12 + size:
                        raise EOFError
                    self.buffer.read(12 + size)
                    continue
                if tag in (TAG_VID0, TAG_AUD0, TAG_VR00):
                    if avail < 8:
                        raise EOFError
                    size = self._u32be(self.buffer.peek(8)[4:8])
                    if size > _MAX_HEADER:
                        raise ValueError(f"invalid header chunk size {size}")
                    if avail < 8 + size:
                        raise EOFError
                    payload = self.buffer.read(8 + size)[8:]
                    if tag == TAG_VID0:
                        self._parse_vid0_payload(payload)
                    continue
                if tag == TAG_LIST:
                    if avail < 8:
                        raise EOFError
                    size = self._u32be(self.buffer.peek(8)[4:8])
                    if size > _MAX_HEADER:
                        raise ValueError(f"invalid LIST size {size}")
                    # Gear360App treats LIST as a header marker; consume tag+size
                    # and keep scanning (real LIST payload begins with 'movi').
                    self.buffer.read(8)
                    continue
                idx = self.buffer.find(TAG_MOVI)
                if idx >= 0:
                    self.buffer.discard_to(idx + 4)
                    self.started = True
                    return None
                if avail < 256:
                    raise EOFError
                self.buffer.discard_to(avail - 16)

        # ---- movi zone: media chunks ----
        avail = self.buffer.available()
        if avail < 4:
            raise EOFError
        tag = self.buffer.peek(4)
        if tag not in (TAG_00VD, TAG_00AU, TAG_00VR, TAG_TTTS):
            idx = self.buffer.find(TAG_00VD)
            if idx < 0:
                raise ValueError(f"unexpected chunk tag {tag!r}")
            self.buffer.discard_to(idx)
            tag = self.buffer.peek(4)

        # full chunk header (tag+size+timestamp) must be present before consuming
        if self.buffer.available() < 16:
            raise EOFError
        hdr = self.buffer.peek(16)
        size = struct.unpack(">I", hdr[4:8])[0]
        if size <= 0 or size > _MAX_CHUNK:
            raise ValueError(f"invalid chunk size {size}")
        if self.buffer.available() < 16 + size:
            raise EOFError
        self.buffer.read(16)
        payload = self.buffer.read(size)
        self.chunks_seen += 1
        if tag != TAG_00VD:
            return None
        if len(payload) < 6:
            return None
        frame_type = payload[4]
        encoded = payload[5:]
        return VideoFrame(
            timestamp=struct.unpack(">Q", hdr[8:16])[0],
            payload=encoded,
            keyframe=frame_type in KEYFRAME_TYPES,
        )

    def _parse_vid0_payload(self, payload: bytes) -> None:
        if len(payload) < 96:
            return
        try:
            # Field offsets follow Gear360App Extractor.kt
            self.info.codec = self._u32be(payload[20:24])
            self.info.timestamp_scale = self._u32be(payload[36:40]) or 1
            num = self._u32be(payload[52:56])
            den = self._u32be(payload[56:60])
            self.info.frame_rate = (num / den) if den else 0.0
            self.info.width = self._u32be(payload[88:92])
            self.info.height = self._u32be(payload[92:96])
            LOG.info(
                "VID0: codec=%s %sx%s @%.2ffps scale=%s",
                self.info.codec_name, self.info.width, self.info.height,
                self.info.frame_rate, self.info.timestamp_scale,
            )
        except Exception:
            LOG.debug("VID0 header parse failed", exc_info=True)


def iter_camera_bytes(cfg: dict, stop_event: threading.Event) -> Generator[bytes, None, None]:
    """Yield raw bytes from the camera live-preview HTTP endpoint with retries."""
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
                LOG.info(
                    "Camera stream HTTP %s, Content-Type=%s",
                    r.status_code, r.headers.get("Content-Type"),
                )
                for chunk in r.iter_content(chunk_size=64 * 1024):
                    if stop_event.is_set():
                        return
                    if chunk:
                        yield chunk
        except Exception as exc:
            LOG.warning("Camera stream ended: %s", exc)
            if stop_event.wait(reconnect):
                return
