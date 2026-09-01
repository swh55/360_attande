"""FFmpeg-based decoder for raw HEVC/H264 access units from the Gear 360 stream.

Improvement over the prototype: a dedicated stdout reader thread feeds a bounded
queue, so `write()` never blocks forever when ffmpeg emits no frame yet (the
prototype could deadlock on a blocking exact-size read). Decoded frames are
published as BGR ndarrays, memory-bounded via drop-oldest.
"""
from __future__ import annotations

import logging
import queue
import subprocess
import threading
import time
from collections import deque
from typing import Optional

import numpy as np

from .framebus import Frame, FrameBus

LOG = logging.getLogger("hub.decoder")


class FFmpegDecoder:
    def __init__(self, codec: str, width: int, height: int, ffmpeg_path: str = "ffmpeg"):
        self.codec = codec
        self.width = width
        self.height = height
        self.ffmpeg_path = ffmpeg_path
        self.proc: Optional[subprocess.Popen] = None
        self._out_q: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=8)
        self._reader: Optional[threading.Thread] = None
        self._err_thread: Optional[threading.Thread] = None
        self._stopped = threading.Event()
        self.dropped = 0

    def start(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise RuntimeError("Stream dimensions unknown (VID0 not parsed yet)")
        fmt = "hevc" if self.codec == "hevc" else "h264"
        cmd = [
            self.ffmpeg_path, "-hide_banner", "-loglevel", "error",
            # low-latency streaming: minimal probe so frames flow immediately
            # (defaults buffer ~5MB before emitting the first packet)
            "-probesize", "32", "-analyzeduration", "0",
            "-f", fmt, "-i", "pipe:0",
            "-an", "-sn", "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1",
        ]
        LOG.info("Starting decoder: %s", " ".join(cmd))
        self.proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        self._stopped.clear()
        self._reader = threading.Thread(target=self._read_loop, name="ffmpeg-reader", daemon=True)
        self._reader.start()
        self._err_thread = threading.Thread(target=self._drain_stderr, name="ffmpeg-stderr", daemon=True)
        self._err_thread.start()

    def _read_loop(self) -> None:
        assert self.proc and self.proc.stdout
        frame_bytes = self.width * self.height * 3
        stream = self.proc.stdout
        while not self._stopped.is_set():
            buf = bytearray()
            while len(buf) < frame_bytes:
                need = frame_bytes - len(buf)
                chunk = stream.read(need)
                if not chunk:
                    if not self._stopped.is_set():
                        LOG.warning("ffmpeg stdout closed")
                        self._stopped.set()
                    return
                buf.extend(chunk)
            arr = np.frombuffer(bytes(buf), dtype=np.uint8).reshape((self.height, self.width, 3))
            try:
                self._out_q.put_nowait(arr.copy())
            except queue.Full:
                try:
                    self._out_q.get_nowait()
                    self.dropped += 1
                except queue.Empty:
                    pass
                try:
                    self._out_q.put_nowait(arr.copy())
                except queue.Full:
                    pass

    def _drain_stderr(self) -> None:
        assert self.proc and self.proc.stderr
        try:
            for _line in self.proc.stderr:
                pass  # ffmpeg errors surface via exit status / logs
        except Exception:
            pass

    _START_CODES = (b"\x00\x00\x00\x01", b"\x00\x00\x01")

    def write(self, payload: bytes) -> list[np.ndarray]:
        """Push one encoded access unit; return any decoded frames available.

        Access units are fed to ffmpeg as Annex-B: if the payload does not
        already begin with a start code, one is prepended so the first NAL is
        framed correctly (the real camera payload framing may vary).
        """
        if self.proc is None or self.proc.stdin is None:
            raise RuntimeError("Decoder not started")
        if not payload.startswith(self._START_CODES):
            payload = b"\x00\x00\x00\x01" + payload
        out: list[np.ndarray] = []
        self.proc.stdin.write(payload)
        self.proc.stdin.flush()
        while True:
            try:
                out.append(self._out_q.get_nowait())
            except queue.Empty:
                break
        return out

    def close(self) -> None:
        self._stopped.set()
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


class StreamPipeline:
    """Camera bytes -> parser -> decoder -> FrameBus. One thread, bounded RAM."""

    def __init__(self, bus: FrameBus, ffmpeg_path: str = "ffmpeg"):
        self.bus = bus
        self.ffmpeg_path = ffmpeg_path
        self.parser = None  # set when pipeline runs
        self.decoder: Optional[FFmpegDecoder] = None
        self.encoded_frames = 0
        self.decoded_frames = 0
        self.error: Optional[str] = None
        self.last_frame_wall = 0.0
        self._fps_window: deque[float] = deque(maxlen=120)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._cfg: dict = {}

    def start(self, camera_cfg: dict) -> None:
        self._cfg = {
            "host": camera_cfg["host"],
            "port": camera_cfg["port"],
            "path": camera_cfg.get("stream_path", "/livestream_high.avi"),
            "request_timeout_seconds": camera_cfg.get("request_timeout", 8),
            "reconnect_seconds": camera_cfg.get("reconnect_delay", 1),
        }
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="stream-pipeline", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=4)
        if self.decoder:
            self.decoder.close()
            self.decoder = None

    @property
    def fps(self) -> float:
        now = time.time()
        recent = [t for t in self._fps_window if now - t <= 2.0]
        return round(len(recent) / 2.0, 1) if len(recent) >= 2 else 0.0

    def _run(self) -> None:
        from ..stream.parser import Gear360ChunkParser, iter_camera_bytes

        parser = Gear360ChunkParser()
        self.parser = parser
        try:
            for chunk in iter_camera_bytes(self._cfg, self._stop):
                frames = parser.feed(chunk)
                if parser.info.width and self.decoder is None:
                    self.decoder = FFmpegDecoder(
                        parser.info.codec_name, parser.info.width, parser.info.height,
                        ffmpeg_path=self.ffmpeg_path,
                    )
                    self.decoder.start()
                    LOG.info(
                        "Decoder online: %s %sx%s", parser.info.codec_name,
                        parser.info.width, parser.info.height,
                    )
                if self.decoder is None:
                    continue
                for frame in frames:
                    self.encoded_frames += 1
                    try:
                        images = self.decoder.write(frame.payload)
                    except (BrokenPipeError, OSError) as exc:
                        raise RuntimeError(f"FFmpeg pipe failed: {exc}") from exc
                    for image in images:
                        self.decoded_frames += 1
                        self._fps_window.append(time.time())
                        self.last_frame_wall = time.time()
                        self.bus.publish(Frame(
                            image=image, timestamp=frame.timestamp,
                            keyframe=frame.keyframe, source="live",
                        ))
        except Exception as exc:
            self.error = str(exc)
            LOG.exception("Stream pipeline failed")
