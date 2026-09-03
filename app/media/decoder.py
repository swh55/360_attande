"""FFmpeg-based decoder for raw HEVC/H264 access units from the Gear 360 stream.

A dedicated stdout reader thread feeds a bounded queue, so `write()` never
blocks forever when ffmpeg emits no frame yet. Decoded frames are published as
BGR ndarrays, memory-bounded via drop-oldest.

StreamPipeline glues camera bytes -> parser -> decoder -> FrameBus and exposes
live signals + a diagnostics snapshot for the diagnostics engine.
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
        self.frames_out = 0
        self.bytes_in = 0
        self.started_at = 0.0
        self.last_write_wall = 0.0
        self.last_error = ""
        self.exit_code: Optional[int] = None
        self.command: list[str] = []
        self.stderr_tail: deque = deque(maxlen=40)

    def start(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise RuntimeError("Stream dimensions unknown (VID0 not parsed yet)")
        fmt = "hevc" if self.codec == "hevc" else "h264"
        cmd = [
            self.ffmpeg_path, "-hide_banner", "-loglevel", "error",
            # low-latency streaming: minimal probe so frames flow immediately
            "-probesize", "32", "-analyzeduration", "0",
            "-f", fmt, "-i", "pipe:0",
            "-an", "-sn", "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1",
        ]
        self.command = list(cmd)
        LOG.info("FFMPEG: path=%s", self.ffmpeg_path)
        LOG.info("FFMPEG: command=%s", " ".join(cmd))
        self.proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        LOG.info("FFMPEG: started pid=%s (stdin/stdout/stderr pipes open)", self.proc.pid)
        self.started_at = time.time()
        self._stopped.clear()
        self._reader = threading.Thread(target=self._read_loop, name="ffmpeg-reader", daemon=True)
        self._reader.start()
        self._err_thread = threading.Thread(target=self._drain_stderr, name="ffmpeg-stderr", daemon=True)
        self._err_thread.start()

    @property
    def is_running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    @property
    def decode_fps(self) -> float:
        if not self.started_at:
            return 0.0
        dt = max(0.001, time.time() - self.started_at)
        return round(self.frames_out / dt, 1)

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
                        rc = self.proc.poll() if self.proc else None
                        self.exit_code = rc
                        LOG.error(
                            "FFMPEG: stdout closed — return_code=%s frames_out=%d "
                            "bytes_in=%d stderr_tail=%s",
                            rc, self.frames_out, self.bytes_in, list(self.stderr_tail)[-5:])
                        self.last_error = self.last_error or \
                            f"ffmpeg stdout closed unexpectedly (exit={rc})"
                        self._stopped.set()
                    return
                buf.extend(chunk)
            arr = np.frombuffer(bytes(buf), dtype=np.uint8).reshape((self.height, self.width, 3))
            self.frames_out += 1
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
            for raw in self.proc.stderr:
                line = raw.decode("utf-8", "replace").rstrip()
                if line:
                    self.stderr_tail.append(line)
                    if "error" in line.lower() or "invalid" in line.lower():
                        self.last_error = line
        except Exception:
            pass

    _START_CODES = (b"\x00\x00\x00\x01", b"\x00\x00\x01")

    def write(self, payload: bytes) -> list[np.ndarray]:
        """Push one encoded access unit; return any decoded frames available.

        Access units are fed to ffmpeg as Annex-B. If the payload does not
        already begin with a start code, one is prepended.
        """
        if self.proc is None or self.proc.stdin is None:
            raise RuntimeError("Decoder not started")
        if not payload.startswith(self._START_CODES):
            payload = b"\x00\x00\x00\x01" + payload
        out: list[np.ndarray] = []
        try:
            self.proc.stdin.write(payload)
            self.proc.stdin.flush()
            self.bytes_in += len(payload)
            self.last_write_wall = time.time()
            if self.bytes_in - len(payload) == 0:
                LOG.info("FFMPEG: first encoded AU written to stdin (%d bytes) — input bytes flowing", len(payload))
        except (BrokenPipeError, OSError) as exc:
            self.last_error = f"stdin write failed: {exc}"
            LOG.error("FFMPEG: stdin write failed (%d bytes in so far, exit=%s): %s",
                      self.bytes_in, (self.proc.poll() if self.proc else None), exc)
            raise
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
            try:
                self.exit_code = self.proc.wait(timeout=2)
            except Exception:
                self.exit_code = self.exit_code if self.exit_code is not None else -1
            LOG.info("FFMPEG: terminated exit_code=%s frames_out=%d bytes_in=%d",
                     self.exit_code, self.frames_out, self.bytes_in)
            self.proc = None


class StreamPipeline:
    """Camera bytes -> parser -> decoder -> FrameBus. One thread, bounded RAM."""

    def __init__(self, bus: FrameBus, ffmpeg_path: str = "ffmpeg"):
        self.bus = bus
        self.ffmpeg_path = ffmpeg_path
        self.parser = None  # set when pipeline runs
        self.decoder: Optional[FFmpegDecoder] = None
        self.encoded_frames = 0          # AUs delivered by the parser
        self.decoded_frames = 0
        self.video_bytes_received = 0    # sum of 00VD payload bytes (encoded)
        self.first_au_ts: Optional[float] = None
        self.last_au_ts: Optional[float] = None
        self.error: Optional[str] = None
        self.last_frame_wall = 0.0
        self.first_frame_wall = 0.0
        self.http_ok = False
        self.http_status: Optional[int] = None
        self.http_content_type = ""
        self.first_byte_wall = 0.0
        self.last_chunk_wall = 0.0
        self.connect_wall = 0.0
        self.decoder_block_reason: Optional[str] = None
        # transport observability (fed by iter_camera_bytes on_event)
        self.transport_status = "idle"   # idle|connecting|headers|data|stalled|error
        self.last_transport_event: dict = {}
        self.stall_events = 0
        self.stalled_attempts_total = 0
        self.transport_retries = 0
        self.reconnects = 0
        self.last_transport_error = ""
        self._fps_window: deque[float] = deque(maxlen=120)
        self._bytes_window: deque[tuple] = deque(maxlen=240)
        self._last_sample = 0.0
        self._last_frame_log = 0.0
        self._parser_reset_pending = False
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

    @property
    def bytes_per_sec(self) -> float:
        now = time.time()
        pts = [(t, b) for t, b in self._bytes_window if now - t <= 5.0]
        if len(pts) < 2:
            return 0.0
        dt = pts[-1][0] - pts[0][0]
        db = pts[-1][1] - pts[0][1]
        return round(db / dt, 0) if dt > 0 else 0.0

    # ---- diagnostics / health ----------------------------------------------
    def counters(self) -> dict:
        """Full pipeline counter table (HTTP bytes -> ... -> viewer frames).

        Reading it answers WHERE frames stop:
          00VD == 0                      -> parser/stream problem
          00VD > 0, ffmpeg input == 0    -> decoder problem
          decoded > 0, viewer == 0       -> frontend/projection problem
        """
        parser = self.parser
        tc = getattr(parser, "tag_counts", {}) or {}
        dec = self.decoder
        return {
            "http_bytes": parser.bytes_seen if parser else 0,
            "chunks": parser.chunks_seen if parser else 0,
            "chunks_00vd": tc.get("00VD", 0),
            "chunk_counts": dict(tc),
            "h264_bytes": self.video_bytes_received,
            "aus": self.encoded_frames,
            "first_au_ts": self.first_au_ts,
            "last_au_ts": self.last_au_ts,
            "ffmpeg_input_bytes": dec.bytes_in if dec else 0,
            "decoded_frames": self.decoded_frames,
            "published_frames": self.bus.published,
            "viewer_frames": self.bus.viewer_frames,
        }

    def signals(self) -> dict:
        """Layered health signals for the health state machine."""
        parser = self.parser
        return {
            "http_ok": self.http_ok,
            "http_status": self.http_status,
            "transport_status": self.transport_status,
            "bytes_seen": parser.bytes_seen if parser else 0,
            "chunks_seen": parser.chunks_seen if parser else 0,
            "parser_valid": bool(parser and parser.info.valid),
            "parser_started": bool(parser and parser.started),
            "decoder_active": self.decoder is not None,
            "decoder_running": bool(self.decoder and self.decoder.is_running),
            "frame_age": (time.time() - self.last_frame_wall) if self.last_frame_wall else None,
            "bytes_per_sec": self.bytes_per_sec,
            "fps": self.fps,
            "error": self.error,
        }

    def diagnostics(self) -> dict:
        parser = self.parser
        return {
            "http": {"ok": self.http_ok, "status": self.http_status,
                     "content_type": self.http_content_type},
            "transport": {
                "status": self.transport_status,
                "last_event": dict(self.last_transport_event),
                "stall_events": self.stall_events,
                "stalled_attempts_total": self.stalled_attempts_total,
                "retries": self.transport_retries,
                "reconnects": self.reconnects,
                "last_error": self.last_transport_error,
            },
            "bytes_seen": parser.bytes_seen if parser else 0,
            "bytes_per_sec": self.bytes_per_sec,
            "first_byte_wall": self.first_byte_wall,
            "last_chunk_wall": self.last_chunk_wall,
            "decoder_block_reason": self.decoder_block_reason,
            "decoder": None if self.decoder is None else {
                "codec": self.decoder.codec, "width": self.decoder.width,
                "height": self.decoder.height, "running": self.decoder.is_running,
                "frames_out": self.decoder.frames_out, "dropped": self.decoder.dropped,
                "bytes_in": self.decoder.bytes_in, "decode_fps": self.decoder.decode_fps,
                "last_error": self.decoder.last_error,
                "stderr_tail": list(self.decoder.stderr_tail)[-8:],
            },
            "parser": parser.diagnostics_snapshot() if parser else None,
            "error": self.error,
        }

    # ---- internals -----------------------------------------------------------
    @staticmethod
    def _metadata_usable(info) -> tuple[bool, str]:
        if not info.width or not info.height:
            return False, "dimensions unknown (VID0 not parsed yet)"
        if not (64 <= info.width <= 8192 and 64 <= info.height <= 8192):
            return False, f"implausible dimensions {info.width}x{info.height}"
        if info.width % 2 or info.height % 2:
            return False, f"odd dimensions {info.width}x{info.height}"
        return True, ""

    def _on_http_response(self, status: int, headers: dict) -> None:
        self.http_status = status
        self.http_content_type = headers.get("Content-Type", "")
        self.http_ok = status in (200, 206)
        self.connect_wall = time.time()

    def _on_transport_event(self, etype: str, **detail) -> None:
        self.last_transport_event = {"type": etype, "ts": round(time.time(), 3), **detail}
        if etype == "connect":
            self.transport_status = "connecting"
            # every new HTTP response restarts the container from byte 0 (the
            # camera always answers Range: bytes=0- with a fresh header zone);
            # a parser that already consumed bytes must be reset or it stays
            # desynced inside the old movi zone forever
            if self.parser is not None and (self.parser.bytes_seen or self.parser.started):
                self._parser_reset_pending = True
        elif etype == "headers":
            self.transport_status = "headers"
        elif etype == "data":
            self.transport_status = "data"
        elif etype == "stalled":
            self.transport_status = "stalled"
            self.stall_events += 1
            self.stalled_attempts_total = detail.get("stalled_total", self.stall_events)
        elif etype == "interrupted":
            self.transport_status = "error"
            self.last_transport_error = detail.get("error", "")
            self.transport_retries += 1
            self.reconnects += 1
        elif etype == "eof":
            self.transport_retries += 1
            self.reconnects += 1
        elif etype == "stall_persist":
            LOG.error("TRANSPORT: persistent headers-without-body (attempts=%s) — see earlier guidance",
                      detail.get("attempts"))

    def _run(self) -> None:
        from ..stream.parser import Gear360ChunkParser, iter_camera_bytes

        parser = Gear360ChunkParser()
        self.parser = parser
        try:
            for chunk in iter_camera_bytes(self._cfg, self._stop,
                                           on_response=self._on_http_response,
                                           on_event=self._on_transport_event):
                if not self.first_byte_wall:
                    self.first_byte_wall = time.time()
                    wait = (self.first_byte_wall - self.connect_wall) if self.connect_wall else 0.0
                    LOG.info("STREAM: first stream bytes after %.2fs", wait)
                self.last_chunk_wall = time.time()
                if self._parser_reset_pending:
                    self._parser_reset_pending = False
                    parser.reset()
                    LOG.info("STREAM: parser reset — fresh container after transport reconnect")
                frames = parser.feed(chunk)

                now = time.time()
                if now - self._last_sample > 0.5:
                    self._last_sample = now
                    self._bytes_window.append((now, parser.bytes_seen))

                if self.decoder is None and not self.decoder_block_reason and parser.info.width:
                    ok, reason = self._metadata_usable(parser.info)
                    if ok:
                        if not parser.info.valid:
                            LOG.warning("DECODER: metadata suspect (subtag_ok=%s) — "
                                        "proceeding with plausible dims",
                                        parser.info.subtag_ok)
                        self.decoder = FFmpegDecoder(
                            parser.info.codec_name, parser.info.width, parser.info.height,
                            ffmpeg_path=self.ffmpeg_path,
                        )
                        self.decoder.start()
                        LOG.info("DECODER: online %s %sx%s @%.3ffps scale=%s",
                                 parser.info.codec_name, parser.info.width,
                                 parser.info.height, parser.info.frame_rate,
                                 parser.info.timestamp_scale)
                    else:
                        self.decoder_block_reason = reason
                        LOG.error("DECODER: blocked — %s (raw dims %sx%s, "
                                  "subtag_ok=%s, scale=%s, fps=%.3f)",
                                  reason, parser.info.width, parser.info.height,
                                  parser.info.subtag_ok, parser.info.timestamp_scale,
                                  parser.info.frame_rate)
                if self.decoder is None:
                    continue
                for frame in frames:
                    self.encoded_frames += 1
                    self.video_bytes_received += len(frame.payload)
                    now = time.time()
                    if self.first_au_ts is None:
                        self.first_au_ts = now
                        LOG.info("AU: first H264 access unit #%d (%d bytes encoded)",
                                 self.encoded_frames, len(frame.payload))
                    self.last_au_ts = now
                    if self.encoded_frames <= 10:
                        LOG.info("AU: H264 AU #%d, %d bytes, keyframe=%s",
                                 self.encoded_frames, len(frame.payload), frame.keyframe)
                    try:
                        images = self.decoder.write(frame.payload)
                    except (BrokenPipeError, OSError) as exc:
                        raise RuntimeError(f"FFmpeg pipe failed: {exc}") from exc
                    for image in images:
                        self.decoded_frames += 1
                        self._fps_window.append(time.time())
                        now = time.time()
                        if not self.first_frame_wall:
                            self.first_frame_wall = now
                            LOG.info(
                                "FRAME: first frame decoded %dx%d — %.1fs after stream start "
                                "(parser: %d bytes, %d chunks, %d resyncs)",
                                image.shape[1], image.shape[0],
                                now - (self.first_byte_wall or now),
                                parser.bytes_seen, parser.chunks_seen, parser.resyncs)
                        elif now - self._last_frame_log >= 10.0:
                            self._last_frame_log = now
                            LOG.info("FRAME: %d decoded total, %.1f fps",
                                     self.decoded_frames, self.fps)
                        self.last_frame_wall = now
                        self.bus.publish(Frame(
                            image=image, timestamp=frame.timestamp,
                            keyframe=frame.keyframe, source="live",
                        ))
        except Exception as exc:
            self.error = str(exc)
            LOG.exception("Stream pipeline failed")
