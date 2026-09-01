from __future__ import annotations

import logging
import queue
import subprocess
import threading
from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np

from .gear360_stream import Gear360ChunkDecoder, VideoFrame, iter_camera_bytes

LOG = logging.getLogger("gear360.decoder")


@dataclass
class DecodedImage:
    image: np.ndarray
    timestamp: int
    keyframe: bool


class FrameBus:
    def __init__(self, maxsize: int = 2) -> None:
        self._q: queue.Queue[DecodedImage] = queue.Queue(maxsize=maxsize)
        self._latest: Optional[DecodedImage] = None
        self._lock = threading.Lock()

    def publish(self, item: DecodedImage) -> None:
        with self._lock:
            self._latest = item
        try:
            self._q.put_nowait(item)
        except queue.Full:
            try:
                self._q.get_nowait()
            except queue.Empty:
                pass
            try:
                self._q.put_nowait(item)
            except queue.Full:
                pass

    def latest(self) -> Optional[DecodedImage]:
        with self._lock:
            return self._latest


class FFmpegDecoder:
    """Decode a stream of raw HEVC/H.264 access units using FFmpeg."""

    def __init__(self, codec: str, width: int, height: int) -> None:
        self.codec = codec
        self.width = width
        self.height = height
        self.proc: Optional[subprocess.Popen[bytes]] = None
        self._stdout_buffer = bytearray()

    def start(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise RuntimeError("Stream dimensions were not discovered from VID0 header")
        fmt = "hevc" if self.codec == "hevc" else "h264"
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-f", fmt, "-i", "pipe:0",
            "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1",
        ]
        LOG.info("Starting decoder: %s", " ".join(cmd))
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def write(self, payload: bytes) -> list[np.ndarray]:
        if self.proc is None or self.proc.stdin is None or self.proc.stdout is None:
            raise RuntimeError("Decoder not started")
        self.proc.stdin.write(payload)
        self.proc.stdin.flush()
        frame_bytes = self.width * self.height * 3
        out: list[np.ndarray] = []
        while True:
            chunk = self.proc.stdout.read(frame_bytes)
            if not chunk or len(chunk) < frame_bytes:
                break
            arr = np.frombuffer(chunk, dtype=np.uint8).reshape((self.height, self.width, 3)).copy()
            out.append(arr)
        return out

    def close(self) -> None:
        if self.proc:
            try:
                if self.proc.stdin:
                    self.proc.stdin.close()
            except Exception:
                pass
            try:
                self.proc.kill()
            except Exception:
                pass
            self.proc = None


class Gear360DecoderWorker:
    def __init__(self, camera_cfg: dict, bus: FrameBus) -> None:
        self.camera_cfg = camera_cfg
        self.bus = bus
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, name="gear360-decoder", daemon=True)
        self.encoded_frame_count = 0
        self.decoded_frame_count = 0
        self.error: Optional[str] = None
        self.info = None

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=3)

    def _run(self) -> None:
        parser = Gear360ChunkDecoder()
        decoder: Optional[FFmpegDecoder] = None
        try:
            for chunk in iter_camera_bytes(self.camera_cfg, self.stop_event):
                frames = parser.feed(chunk)
                if parser.info.width and decoder is None:
                    codec = "hevc" if parser.info.codec == 1 else "h264"
                    self.info = parser.info
                    decoder = FFmpegDecoder(codec, parser.info.width, parser.info.height)
                    decoder.start()
                    LOG.info("Stream info: codec=%s %sx%s %.2ffps", codec, parser.info.width, parser.info.height, parser.info.frame_rate)
                if decoder is None:
                    continue
                for frame in frames:
                    self.encoded_frame_count += 1
                    try:
                        images = decoder.write(frame.payload)
                    except (BrokenPipeError, OSError) as exc:
                        raise RuntimeError(f"FFmpeg decoder pipe failed: {exc}") from exc
                    for image in images:
                        self.decoded_frame_count += 1
                        self.bus.publish(DecodedImage(image=image, timestamp=frame.timestamp, keyframe=frame.keyframe))
        except Exception as exc:
            self.error = str(exc)
            LOG.exception("Decoder worker failed")
        finally:
            if decoder:
                decoder.close()
