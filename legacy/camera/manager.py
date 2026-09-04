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
from . import rearm as rearm_svc
from .session_broker import get_broker

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
        self._last_rearm: dict | None = None

    # ---- info ---------------------------------------------------------------
    def layers(self) -> dict:
        """The SEVEN distinct stream layers (investigation task §45).

        Reachable ≠ TCP-Connected ≠ HTTP-Connected ≠ Stream-Active ≠
        Parser-Active ≠ Decoder-Active ≠ Frame-Ready. The UI and every
        error path must be able to name WHICH layer is the last one alive.
        """
        p = self._pipeline
        parser = p.parser if p else None
        bytes_seen = parser.bytes_seen if parser else 0
        chunks = parser.chunks_seen if parser else 0
        http_ok = bool(p and p.http_ok)
        now = time.time()
        stream_active = bool(p and p.last_chunk_wall
                             and (now - p.last_chunk_wall) < 3.0)
        parser_active = bool(parser and parser.info.valid and chunks > 0)
        decoder_active = bool(p and p.decoder is not None
                              and p.decoder.is_running)
        frame_ready = bool(p and p.last_frame_wall
                           and (now - p.last_frame_wall) < 3.0)
        return {
            "reachable": self.camera_reachable,          # TCP SYN succeeds (health probe or live bytes)
            "http_connected": http_ok,                   # 200/206 received
            "stream_active": stream_active,              # container bytes still arriving
            "parser_active": parser_active,              # chunks parsed, VID0 valid
            "decoder_active": decoder_active,            # ffmpeg alive
            "frame_ready": frame_ready,                  # decoded frame < 3s old
            "bytes_seen": bytes_seen,
            "chunks_seen": chunks,
            "decoded_frames": p.decoded_frames if p else 0,
        }

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
            "phase": self.phase(),
            "layers": self.layers(),
            "session": get_broker().status(),
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
            "wedged": bool(self.error and "CAMERA_STREAM_WEDGED" in str(self.error)),
            "last_rearm": self._last_rearm,
            "stream": parser_info,
        }

    # ---- commands -------------------------------------------------------------
    def _session_active(self) -> bool:
        """True while a live session exists (any state between connect and stop)."""
        return self._pipeline is not None or self.state in (
            "connecting", "streaming", "reconnecting", "error")

    def phase(self) -> str:
        """Granular pipeline phase — NEVER conflates 'connected' with 'streaming'.

        DISCONNECTED → CONNECTING → STREAM_CONNECTED (headers, no body yet)
        → PARSING (bytes, no valid metadata) → DECODING (metadata ok, decoder
        starting) → FRAME_READY (first decoded frame) → STREAMING (frames
        flowing). ERROR/RECONNECTING are reported through the camera state.
        """
        if self.state == "disconnected" or self._pipeline is None:
            return "DISCONNECTED"
        if self.state == "error":
            return "ERROR"
        if self.state == "reconnecting":
            return "RECONNECTING"
        p = self._pipeline
        if not p.http_ok:
            return "CONNECTING"
        parser = p.parser
        if p.first_byte_wall == 0 or parser is None or parser.chunks_seen == 0:
            return "STREAM_CONNECTED"
        if not parser.info.valid or not parser.info.width:
            return "PARSING"
        if p.decoded_frames == 0:
            return "DECODING"
        fresh = p.last_frame_wall and (time.time() - p.last_frame_wall) < 3.0
        return "STREAMING" if fresh else "FRAME_READY"

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
            # single-session camera: claim the broker BEFORE any byte flows
            if not get_broker().acquire("live"):
                self.state = "error"
                self.error = "another component holds the camera session"
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
            get_broker().release("live")
            LOG.info("Disconnected by user")
            event_bus.publish("camera_state", {"state": "disconnected"})
        return self.info()

    def restart_stream(self) -> dict:
        """Restart Live Stream: stop old session, close response, terminate
        decoder, drop the stale latest frame, RE-ARM the camera's single-shot
        streamer, then start a FRESH session (HTTP #2 → Parser #2 → Decoder #2)
        and wait for the first frame.

        Field evidence (diagnostics/raw/evidence 2026-09-04): after a client
        disconnects, the camera answers new requests with headers-but-no-body
        until it is re-armed — a plain reconnect produces exactly the reported
        freeze. The safe re-arm ladder (8888 root/echo/ps) runs here; the
        invasive kill stays behind the explicit /api/camera/rearm?apply=1.
        """
        LOG.info("RESTART: stopping current live stream (if any) …")
        with self._lock:
            if self._pipeline is not None:
                self._pipeline.stop()
                self._pipeline = None
            self.state = "disconnected"
            self._stop_state.set()
            get_broker().release("live")
        # stale frame must never masquerade as a live one after restart
        self.bus.clear()
        time.sleep(0.5)   # camera settle: single-session server releases the socket
        try:
            self._last_rearm = rearm_svc.rearm(apply=False)
            if self._last_rearm.get("applied"):
                LOG.info("RESTART: safe re-arm actions executed before reconnect")
        except Exception as exc:                       # re-arm must never block restart
            LOG.warning("RESTART: safe re-arm failed (%s) — reconnecting anyway", exc)
            self._last_rearm = {"error": str(exc)}
        LOG.info("RESTART: starting fresh live stream session")
        out = self.connect()
        out["rearm"] = self._last_rearm
        event_bus.publish("camera_state", {"state": "restarting"})
        return out

    def rearm_and_connect(self, apply: bool = False) -> dict:
        """Explicit wedged-recovery: run the re-arm ladder (safe, or invasive
        kill with apply=True), then immediately reconnect the real client —
        the ONLY proof of a successful arming is a live connection that
        actually streams a body."""
        with self._lock:
            if self._pipeline is not None:
                self._pipeline.stop()
                self._pipeline = None
            self.state = "disconnected"
            self._stop_state.set()
            get_broker().release("live")
        self.bus.clear()
        self._last_rearm = rearm_svc.rearm(apply=apply)
        LOG.info("REARM: apply=%s applied=%s listener_after=%s",
                 apply, self._last_rearm.get("applied"),
                 self._last_rearm.get("listener_after"))
        time.sleep(0.5)
        out = self.connect()
        out["rearm"] = self._last_rearm
        return out

    def test_connection(self) -> dict:
        """One-shot reachability + HTTP probe of the live-preview endpoint.

        The camera's streaming server is SINGLE-SESSION: when the live
        pipeline exists, this probe MUST NOT open a second connection — it
        would steal the stream session from the live preview (newest request
        wins on the camera). Instead it reports live-pipeline evidence.
        """
        import requests

        host = settings_svc.get("camera.host")
        port = settings_svc.get("camera.port")
        path = settings_svc.get("camera.stream_path")
        url = f"http://{host}:{port}{path}"
        out: dict = {"host": host, "port": port, "url": url, "tcp": False, "http": False}
        p = self._pipeline
        if self._session_active() and p is not None:
            parser = p.parser
            out.update({
                "source": "live-pipeline (no probe connection opened)",
                "tcp": self.camera_reachable,
                "latency_ms": self.last_latency_ms,
                "http": bool(p.http_ok),
                "http_status": p.http_status,
                "content_type": p.http_content_type,
                "bytes_received": parser.bytes_seen if parser else 0,
                "bytes_per_sec": p.bytes_per_sec,
                "camera_state": self.state,
                "phase": self.phase(),
                "note": ("live session holds the camera's single stream session — "
                         "this report reads live-pipeline state instead of probing"),
            })
            return out
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
            # Range header is REQUIRED: this camera streams a body only for
            # Range requests (plain GET answers headers and stalls forever)
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

        Race fix (investigation finding F2): disconnect()/restart_stream()
        set `_stop_state`, but the old thread may still be alive for a short
        window. is_alive() alone would then SKIP creating a new loop and the
        old thread would exit right after — leaving camera.state FROZEN
        (the exact 'state says disconnected/stuck while bytes flow' class of
        symptom). We now join the doomed thread before starting a new one.
        """
        if self._state_thread is not None and self._stop_state.is_set():
            self._state_thread.join(timeout=2.5)   # it must exit within ~1.5s
        if self._state_thread is None or not self._state_thread.is_alive():
            self._stop_state.clear()
            self._state_thread = threading.Thread(target=self._state_loop, name="camera-state", daemon=True)
            self._state_thread.start()

    def _state_loop(self) -> None:
        while not self._stop_state.wait(1.5):
            p = self._pipeline
            if p is None:
                return
            get_broker().heartbeat("live")   # keep the cross-process lock alive
            fresh_chunks = p.last_chunk_wall and (time.time() - p.last_chunk_wall) < 3.0
            fresh_frames = p.last_frame_wall and (time.time() - p.last_frame_wall) < 3.0
            if p.error:
                if self.state != "error":
                    self.state = "error"
                    self.error = p.error
                    self.error_count += 1
                    event_bus.publish("camera_state", {"state": "error", "error": p.error})
                # a dead pipeline thread means the session is truly over —
                # free the single-session broker so Connect can run again
                if (p._thread is not None and not p._thread.is_alive()
                        and self._pipeline is p):
                    self._pipeline = None
                    get_broker().release("live")
                    return
            elif fresh_frames or fresh_chunks:
                if self.state != "streaming":
                    self.state = "streaming"
                    event_bus.publish("camera_state", {"state": "streaming"})
                    if fresh_frames:
                        LOG.info("Streaming (decoded frames flowing)")
                    else:
                        LOG.info("Streaming (container bytes flowing)")
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
        """TCP reachability at a fixed rate — WITH one exception:

        While the live pipeline holds the camera's single stream session,
        opening extra TCP connections to port 7679 competes with the live
        stream on a fragile embedded server (investigation §51 / finding F3).
        When a session exists we DERIVE reachability from live transport
        reality instead of knocking on the door.
        """
        interval = float(settings_svc.get("camera.health_interval", 5))
        while not self._stop_probe.wait(interval):
            p = self._pipeline
            if p is not None:
                parser = p.parser
                flowing = bool(parser and parser.bytes_seen > 0
                               and p.last_chunk_wall
                               and (time.time() - p.last_chunk_wall) < 10.0)
                # live evidence is stronger than a TCP handshake
                self.camera_reachable = flowing or self.camera_reachable
                if flowing:
                    self.last_probe_at = round(time.time(), 3)
                continue
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
