from __future__ import annotations

import logging
import struct
import threading
from dataclasses import dataclass
from typing import Generator, Optional

import requests

LOG = logging.getLogger("gear360.stream")

TAG_TTTS = b"TTTS"
TAG_VID0 = b"VID0"
TAG_AUD0 = b"AUD0"
TAG_VR00 = b"VR00"
TAG_LIST = b"LIST"
TAG_MOVI = b"movi"
TAG_00VD = b"00VD"
TAG_00AU = b"00AU"
TAG_00VR = b"00VR"


@dataclass
class StreamInfo:
    codec: int = -1
    width: int = 0
    height: int = 0
    frame_rate: float = 0.0
    timestamp_scale: int = 1


@dataclass
class VideoFrame:
    timestamp: int
    payload: bytes
    keyframe: bool


class ByteBuffer:
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
            if self._pos > 1024 * 1024:
                del self._buf[:self._pos]
                self._pos = 0
            return out

    def find(self, needle: bytes) -> int:
        with self._lock:
            return bytes(self._buf).find(needle, self._pos)

    def discard_to(self, relative_index: int) -> None:
        with self._lock:
            self._pos += relative_index
            if self._pos > 1024 * 1024:
                del self._buf[:self._pos]
                self._pos = 0


class Gear360ChunkDecoder:
    """Decode the Gear 360 live-preview chunk structure used by Gear360App."""

    def __init__(self) -> None:
        self.buffer = ByteBuffer()
        self.started = False
        self.info = StreamInfo()

    @staticmethod
    def _u32be(b: bytes) -> int:
        return struct.unpack(">I", b)[0]

    def feed(self, data: bytes) -> list[VideoFrame]:
        self.buffer.append(data)
        frames: list[VideoFrame] = []
        while True:
            try:
                frame = self._parse_one()
            except EOFError:
                break
            except ValueError as exc:
                LOG.warning("Decoder resync: %s", exc)
                idx = self.buffer.find(TAG_00VD)
                if idx < 0:
                    idx = self.buffer.find(TAG_MOVI)
                    if idx >= 0:
                        self.buffer.discard_to(idx + len(TAG_MOVI))
                        self.started = True
                        continue
                if idx < 0:
                    if self.buffer.available() > 64:
                        self.buffer.discard_to(self.buffer.available() - 16)
                    break
                self.buffer.discard_to(idx)
                continue
            if frame is not None:
                frames.append(frame)
        return frames

    def _parse_vid0_header(self, size: int) -> None:
        if self.buffer.available() < size:
            raise EOFError
        payload = self.buffer.read(size)
        # Mirrors the field offsets read by Gear360App's Extractor.kt.
        if len(payload) < 72:
            return
        try:
            self.info.codec = self._u32be(payload[20:24])
            self.info.timestamp_scale = self._u32be(payload[36:40]) or 1
            num = self._u32be(payload[52:56])
            den = self._u32be(payload[56:60])
            self.info.frame_rate = (num / den) if den else 0.0
            self.info.width = self._u32be(payload[88:92])
            self.info.height = self._u32be(payload[92:96])
        except Exception:
            LOG.debug("Unable to parse VID0 header", exc_info=True)

    def _parse_one(self) -> Optional[VideoFrame]:
        if not self.started:
            # Parse the top-level stream until MOVI. We only need VID0 metadata.
            while True:
                if self.buffer.available() < 4:
                    raise EOFError
                tag = self.buffer.peek(4)
                if tag == TAG_MOVI:
                    self.buffer.read(4)
                    self.started = True
                    return None
                if tag == TAG_TTTS:
                    self.buffer.read(4)
                    if self.buffer.available() < 8:
                        raise EOFError
                    size = self._u32be(self.buffer.read(4))
                    self.buffer.read(4)
                    if size > 32 * 1024 * 1024:
                        raise ValueError("invalid TTTS size")
                    if self.buffer.available() < size:
                        raise EOFError
                    self.buffer.read(size)
                    continue
                if tag in (TAG_VID0, TAG_AUD0, TAG_VR00):
                    self.buffer.read(4)
                    if self.buffer.available() < 4:
                        raise EOFError
                    size = self._u32be(self.buffer.read(4))
                    if size > 32 * 1024 * 1024:
                        raise ValueError("invalid header chunk size")
                    if tag == TAG_VID0:
                        self._parse_vid0_header(size)
                    else:
                        if self.buffer.available() < size:
                            raise EOFError
                        self.buffer.read(size)
                    continue
                if tag == TAG_LIST:
                    self.buffer.read(4)
                    if self.buffer.available() < 4:
                        raise EOFError
                    size = self._u32be(self.buffer.read(4))
                    if size > 32 * 1024 * 1024:
                        raise ValueError("invalid LIST size")
                    # Gear360App treats LIST as a header marker; consume it and
                    # continue scanning until the MOVI tag.
                    continue
                idx = self.buffer.find(TAG_MOVI)
                if idx >= 0:
                    self.buffer.discard_to(idx + 4)
                    self.started = True
                    return None
                if self.buffer.available() < 256:
                    raise EOFError
                self.buffer.discard_to(self.buffer.available() - 16)

        if self.buffer.available() < 4:
            raise EOFError
        tag = self.buffer.peek(4)
        if tag not in (TAG_00VD, TAG_00AU, TAG_00VR, TAG_TTTS):
            idx = self.buffer.find(TAG_00VD)
            if idx < 0:
                raise ValueError(f"unexpected chunk tag {tag!r}")
            self.buffer.discard_to(idx)
            tag = self.buffer.peek(4)

        self.buffer.read(4)
        if self.buffer.available() < 12:
            raise EOFError
        size = self._u32be(self.buffer.read(4))
        timestamp = struct.unpack(">Q", self.buffer.read(8))[0]
        if size <= 0 or size > 16 * 1024 * 1024:
            raise ValueError(f"invalid chunk size {size}")
        if self.buffer.available() < size:
            raise EOFError
        payload = self.buffer.read(size)
        if tag != TAG_00VD:
            return None
        if len(payload) < 6:
            return None
        frame_type = payload[4]
        encoded = payload[5:]
        return VideoFrame(timestamp=timestamp, payload=encoded, keyframe=frame_type in (64, 38))


def iter_camera_bytes(cfg: dict, stop_event: threading.Event) -> Generator[bytes, None, None]:
    host = cfg["host"]
    port = int(cfg["port"])
    path = cfg["path"]
    timeout = float(cfg.get("request_timeout_seconds", 8))
    reconnect = float(cfg.get("reconnect_seconds", 1))
    url = f"http://{host}:{port}{path}"
    session = requests.Session()
    session.headers.update({"User-Agent": "Gear360AttendanceGateway/0.1", "Accept": "*/*"})
    while not stop_event.is_set():
        try:
            LOG.info("Connecting to %s", url)
            with session.get(url, stream=True, timeout=(3, timeout), headers={"Range": "bytes=0-"}) as r:
                r.raise_for_status()
                LOG.info("Camera stream HTTP %s, Content-Type=%s", r.status_code, r.headers.get("Content-Type"))
                for chunk in r.iter_content(chunk_size=64 * 1024):
                    if stop_event.is_set():
                        return
                    if chunk:
                        yield chunk
        except Exception as exc:
            LOG.warning("Camera stream ended: %s", exc)
            if stop_event.wait(reconnect):
                return
