"""CameraManager: connection state machine + health probe + live metrics.

States: disconnected | connecting | reconnecting | streaming | error
The manager owns a StreamPipeline (parser -> decoder -> FrameBus) and a health
probe thread that measures TCP latency to the camera at a configurable rate.
"""
from __future__ import annotations

import socket
import threading
import time
from typing import Optional

from ..core import database as db
from ..core.bus import bus as event_bus
from ..core.logging_setup import get
from ..core import settings as settings_svc
from ..media.decoder import StreamPipeline
from ..media.ffmpeg import locate
from ..media.framebus import FrameBus

LOG = get("camera")

STATES = ("disconnected", "connecting", "reconnecting", "streaming", "error")


class CameraManager:
    def __init__(self, bus: FrameBus):
        self.bus = bus
        self.state = "disconnected"
        self.error: Optional[str] = None
        self._pipeline: Optional[StreamPipeline] = None
        self._lock = threading.Lock()
        self._stop_probe = threading.Event()
        self._probe_thread: Optional[threading.Thread] = None
        self._stop_state = threading.Event()
        self._state_thread: Optional[threading.Thread] = None
        self.started_at = 0.0
        self.reconnects = 0
        self.error_count = 0
        self.last_latency_ms: Optional[float] = None
        self.last_probe_at = 0.0
        self.camera_reachable = False

    # ---- info ---------------------------------------------------------------
    def info(self) -> dict:
        cfg = {
            "host": settings_svc.get("camera.host"),
            "port": settings_svc.get("camera.port"),
            "stream_path": settings_svc.get("camera.stream_path"),
            "profile": settings_svc.get("camera.profile"),
            "auto_reconnect": settings_svc.get("camera.auto_reconnect"),
        }
        p = self._pipeline
        parser_info = None
        if p is not None and p.parser is not None:
            pi = p.parser.info
            parser_info = {
                "codec": pi.codec_name, "width": pi.width, "height": pi.height,
                "frame_rate": pi.frame_rate,
            }
        return {
            "state": self.state,
            "config": cfg,
            "stream_url": f"http://{cfg['host']}:{cfg['port']}{cfg['stream_path']}",
            "latency_ms": self.last_latency_ms,
            "camera_reachable": self.camera_reachable,
            "last_probe_at": self.last_probe_at,
            "last_frame_age": (round(time.time() - p.last_frame_wall, 2)
                                if p and p.last_frame_wall else None),
            "fps": p.fps if p else 0.0,
            "encoded_frames": p.encoded_frames if p else 0,
            "decoded_frames": p.decoded_frames if p else 0,
            "transport": (p.diagnostics().get("transport") if p else None),
            "uptime_sec": round(time.time() - self.started_at, 1) if self.started_at else 0,
            "error_count": self.error_count,
            "reconnects": self.reconnects,
            "error": self.error,
            "stream": parser_info,
        }

    # ---- commands -------------------------------------------------------------
    def connect(self) -> dict:
        with self._lock:
            if self.state in ("connecting", "streaming", "reconnecting"):
                return self.info()
            self._ensure_probe()
            ff = locate(
                settings_svc.get("ffmpeg.mode"),
                settings_svc.get("ffmpeg.custom_path", ""),
            )
            if not ff.found:
                self.state = "error"
                self.error = ff.error
                self.error_count += 1
                return self.info()
            cfg = {
                "host": settings_svc.get("camera.host"),
                "port": settings_svc.get("camera.port"),
                "stream_path": settings_svc.get("camera.stream_path"),
                "request_timeout": settings_svc.get("stream.request_timeout"),
                "reconnect_delay": settings_svc.get("camera.reconnect_delay"),
                "auto_reconnect": settings_svc.get("camera.auto_reconnect"),
            }
            self.state = "connecting"
            self.error = None
            self.started_at = time.time()
            self._pipeline = StreamPipeline(self.bus, ffmpeg_path=ff.path)
            self._pipeline.start(cfg)
            self._ensure_state_loop()
            LOG.info("Connect requested -> %s:%s%s", cfg["host"], cfg["port"], cfg["stream_path"])
            db.execute(
                "INSERT INTO system_logs (ts, level, domain, message) "
                "VALUES (datetime('now','localtime'), 'info', 'camera', ?)",
                (f"Connect to {cfg['host']}:{cfg['port']}{cfg['stream_path']}",),
            )
            event_bus.publish("camera_state", {"state": "connecting"})
        # give the pipeline a moment so the UI can reflect early state
        threading.Timer(1.0, self._refresh_state).start()
        return self.info()

    def disconnect(self) -> dict:
        with self._lock:
            if self._pipeline is not None:
                self._pipeline.stop()
                self._pipeline = None
            self.state = "disconnected"
            self.started_at = 0.0
            self._stop_state.set()
            LOG.info("Disconnected by user")
            event_bus.publish("camera_state", {"state": "disconnected"})
        return self.info()

    def test_connection(self) -> dict:
        """One-shot reachability + HTTP probe of the live-preview endpoint."""
        import requests

        host = settings_svc.get("camera.host")
        port = settings_svc.get("camera.port")
        path = settings_svc.get("camera.stream_path")
        url = f"http://{host}:{port}{path}"
        out: dict = {"host": host, "port": port, "url": url, "tcp": False, "http": False}
        t0 = time.time()
        try:
            with socket.create_connection((host, port), timeout=3):
                out["tcp"] = True
                out["latency_ms"] = round((time.time() - t0) * 1000, 1)
        except OSError as exc:
            out["error"] = f"TCP connect failed: {exc}"
            return out
        try:
            t1 = time.time()
            with requests.get(url, stream=True, timeout=(3, 5), headers={"Range": "bytes=0-"}) as r:
                out["http"] = True
                out["http_status"] = r.status_code
                out["content_type"] = r.headers.get("Content-Type", "")
                n = 0
                # read just enough to prove real body bytes flow — the camera's
                # stream server is single-session, so keep this window tiny
                for chunk in r.iter_content(8 * 1024):
                    n += len(chunk)
                    if n >= 8192:
                        break
                out["bytes_received"] = n
                out["http_latency_ms"] = round((time.time() - t1) * 1000, 1)
        except Exception as exc:
            out["error"] = f"HTTP stream failed: {exc}"
        return out

    # ---- internals ---------------------------------------------------------
    def _ensure_state_loop(self) -> None:
        """Periodic state sync: transport reality -> camera state for the UI.

        CONNECTED (headers) is NOT STREAM READY is NOT FRAME READY — the UI
        must see the difference, e.g. 'headers but no body' stalls.
        """
        if self._state_thread is None or not self._state_thread.is_alive():
            self._stop_state.clear()
            self._state_thread = threading.Thread(target=self._state_loop, name="camera-state", daemon=True)
            self._state_thread.start()

    def _state_loop(self) -> None:
        while not self._stop_state.wait(1.5):
            p = self._pipeline
            if p is None:
                return
            fresh_chunks = p.last_chunk_wall and (time.time() - p.last_chunk_wall) < 3.0
            if p.error:
                if self.state != "error":
                    self.state = "error"
                    self.error = p.error
                    self.error_count += 1
                    event_bus.publish("camera_state", {"state": "error", "error": p.error})
            elif fresh_chunks:
                if self.state != "streaming":
                    self.state = "streaming"
                    event_bus.publish("camera_state", {"state": "streaming"})
                    LOG.info("Streaming")
            elif p.transport_status in ("stalled", "error"):
                if self.state != "reconnecting":
                    self.state = "reconnecting"
                    self.error = None
                    event_bus.publish("camera_state", {
                        "state": "reconnecting",
                        "transport": p.transport_status,
                        "stalled": p.stalled_attempts_total,
                    })
            elif self.state not in ("connecting", "streaming", "reconnecting", "error"):
                self.state = "connecting"

    def _refresh_state(self) -> None:
        p = self._pipeline
        if p is None:
            return
        if p.last_frame_wall and time.time() - p.last_frame_wall < 2.0:
            if self.state != "streaming":
                self.state = "streaming"
                event_bus.publish("camera_state", {"state": "streaming"})
                LOG.info("Streaming")
        elif p.error:
            self.error_count += 1
            if self.state != "error":
                self.state = "error"
                self.error = p.error
                event_bus.publish("camera_state", {"state": "error", "error": p.error})

    def _ensure_probe(self) -> None:
        if self._probe_thread is None or not self._probe_thread.is_alive():
            self._stop_probe.clear()
            self._probe_thread = threading.Thread(target=self._probe_loop, name="camera-probe", daemon=True)
            self._probe_thread.start()

    def start_probe(self) -> None:
        self._ensure_probe()

    def stop_probe(self) -> None:
        self._stop_probe.set()

    def _probe_loop(self) -> None:
        interval = float(settings_svc.get("camera.health_interval", 5))
        while not self._stop_probe.wait(interval):
            host = settings_svc.get("camera.host")
            port = int(settings_svc.get("camera.port", 7679))
            t0 = time.time()
            try:
                with socket.create_connection((host, port), timeout=2.5):
                    self.camera_reachable = True
                    self.last_latency_ms = round((time.time() - t0) * 1000, 1)
            except OSError:
                self.camera_reachable = False
                self.last_latency_ms = None
            self.last_probe_at = round(time.time(), 3)
