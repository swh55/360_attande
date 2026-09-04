"""Hub orchestrator: singleton wiring DB, settings, camera, demo, media, AI, WS."""
from __future__ import annotations

import threading
import time
from typing import Optional

from .ai.engine import AIEngine
from .build_info import banner_lines
from .camera.manager import CameraManager
from .core import database as db
from .core import settings as settings_svc
from .core.bus import bus as event_bus
from .core.health import HealthStateMachine
from .core.logging_setup import get, setup_logging
from .core.paths import ensure_dirs
from .events.store import EventStore
from .media.ffmpeg import FFmpegInfo, locate
from .media.framebus import FrameBus
from .media.recorder import RecordingManager
from .media.snapshots import SnapshotManager
from .services.demo import DemoSource

LOG = get("app")


class WebSocketHub:
    """Fans out live state to all connected WebSocket clients."""

    def __init__(self):
        self._clients: set = set()
        self._lock = threading.Lock()

    def register(self, ws) -> None:
        with self._lock:
            self._clients.add(ws)

    def unregister(self, ws) -> None:
        with self._lock:
            self._clients.discard(ws)

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._clients)

    async def broadcast(self, payload: dict) -> None:
        import json
        msg = json.dumps(payload, default=str)
        dead = []
        with self._lock:
            clients = list(self._clients)
        for ws in clients:
            try:
                await ws.send_text(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.unregister(ws)

    def publish_threadsafe(self, payload: dict) -> None:
        """Called from worker threads; queues via the hub loop."""
        with self._lock:
            clients = list(self._clients)
        if not clients:
            return
        try:
            import asyncio
            loop = getattr(self, "_loop", None)
            if loop is not None and loop.is_running():
                asyncio.run_coroutine_threadsafe(self.broadcast(payload), loop)
        except Exception:
            pass


class Hub:
    def __init__(self):
        self.started_at = time.time()
        self.mode = "idle"           # idle | live | demo
        self.bus = FrameBus()
        self.ffmpeg: FFmpegInfo = FFmpegInfo()
        self.camera = CameraManager(self.bus)
        self.store = EventStore()
        self.recorder = RecordingManager(self.bus, self.ffmpeg)
        self.snapshots = SnapshotManager(self.bus)
        self.health_machine = HealthStateMachine()
        self.demo: Optional[DemoSource] = None
        self.ai: Optional[AIEngine] = None
        self.ws = WebSocketHub()
        self._state_thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    # ---- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        ensure_dirs()
        setup_logging()
        version = db.migrate()
        settings_svc.load()
        # build identity banner — proves WHICH build produced the log lines
        for line in banner_lines():
            LOG.info("%s", line)
        LOG.info("Gear360 Hub starting (schema v%s)", version)
        self.ffmpeg = locate(settings_svc.get("ffmpeg.mode"), settings_svc.get("ffmpeg.custom_path", ""))
        self.recorder.ffmpeg = self.ffmpeg
        self.camera.start_probe()
        self.ai = AIEngine(self.bus, self.store, self.camera, ws_broadcaster=self.ws)
        self.ai._event_clip_hook = self._on_ai_event_clip
        self.ai.start()
        event_bus.subscribe("event", self._on_event)
        settings_svc.on_change(self._on_setting_change)
        # clear the stop event so a restarted lifecycle (e.g. a second TestClient
        # lifespan in the same process) gets a live state loop again
        self._stop.clear()
        self._state_thread = threading.Thread(target=self._state_loop, name="hub-state", daemon=True)
        self._state_thread.start()
        self.apply_mode()
        db.execute(
            "INSERT INTO system_logs (ts, level, domain, message) "
            "VALUES (datetime('now','localtime'), 'info', 'app', 'Gear360 Hub started')")
        LOG.info("Hub started. FFmpeg=%s", self.ffmpeg.path or "NOT FOUND")

    def shutdown(self) -> None:
        self._stop.set()
        if self.ai:
            self.ai.stop()
        if self.demo:
            self.demo.stop()
        self.camera.disconnect()
        self.camera.stop_probe()
        db.close()
        LOG.info("Hub stopped")

    # ---- mode management -----------------------------------------------------
    def apply_mode(self) -> None:
        """Demo vs Live priority: live camera wins; demo only when enabled."""
        demo_wanted = bool(settings_svc.get("demo.enabled"))
        live_active = self.camera.state in ("connecting", "streaming", "reconnecting")
        if live_active:
            self.mode = "live"
            if self.demo and self.demo.running:
                self.demo.stop()
        elif demo_wanted:
            if self.demo is None:
                self.demo = DemoSource(self.bus)
            if not self.demo.running:
                self.demo.start()
            self.mode = "demo"
        else:
            if self.demo and self.demo.running:
                self.demo.stop()
            self.mode = "idle"
        self.bus.source = self.mode if self.mode != "idle" else "idle"

    def set_demo(self, enabled: bool) -> dict:
        settings_svc.set_value("demo.enabled", enabled)
        self.apply_mode()
        return {"demo": enabled, "mode": self.mode}

    def _on_setting_change(self, key: str, value) -> None:
        if key == "demo.enabled":
            self.apply_mode()
        if key.startswith("ffmpeg."):
            self.ffmpeg = locate(settings_svc.get("ffmpeg.mode"), settings_svc.get("ffmpeg.custom_path", ""))
            self.recorder.ffmpeg = self.ffmpeg

    # ---- hooks ---------------------------------------------------------------
    def _on_event(self, event: dict) -> None:
        self.ws.publish_threadsafe({"type": "event", "event": event})

    def _on_ai_event_clip(self, event_id: int) -> None:
        if not settings_svc.get("recording.auto_record"):
            return
        # event clips only meaningful with frames flowing
        if self.bus.latest() is None:
            return

    # ---- health ---------------------------------------------------------------
    def _health_signals(self) -> dict:
        p = self.camera._pipeline
        sig = p.signals() if p is not None else {}
        latest = self.bus.latest()
        return {
            "mode": self.mode,
            "camera_state": self.camera.state,
            "camera_reachable": self.camera.camera_reachable,
            "latency_ms": self.camera.last_latency_ms,
            "http_ok": sig.get("http_ok", False),
            "http_status": sig.get("http_status"),
            "bytes_seen": sig.get("bytes_seen", 0),
            "chunks_seen": sig.get("chunks_seen", 0),
            "resyncs": sig.get("resyncs", 0),
            "parser_valid": sig.get("parser_valid", False),
            "decoder_active": sig.get("decoder_active", False),
            "decoder_running": sig.get("decoder_running", False),
            "frame_age": (time.time() - latest.wall_time) if latest else None,
            "bytes_per_sec": sig.get("bytes_per_sec", 0),
            "error": sig.get("error") or (self.camera.error if self.camera.state == "error" else None),
        }

    def pipeline_view(self) -> dict:
        view = self.health_machine.view()
        p = self.camera._pipeline
        view["fps"] = self._pipeline_fps()
        view["bytes_per_sec"] = p.bytes_per_sec if p is not None else 0
        view["mode"] = self.mode
        return view

    # ---- state broadcast -------------------------------------------------------
    def state_snapshot(self) -> dict:
        h = w = fps = 0
        latest = self.bus.latest()
        if latest is not None:
            h, w = latest.image.shape[:2]
        fps = self._pipeline_fps()
        p = self.camera._pipeline
        return {
            "type": "state",
            "mode": self.mode,
            "app": {"version": "1.2.0", "uptime": round(time.time() - self.started_at, 1)},
            "camera": self.camera.info(),
            "stream": {"fps": fps, "width": w, "height": h,
                       "frames_published": self.bus.published,
                       "source": self.bus.source},
            "counters": (p.counters() if p is not None else {
                "http_bytes": 0, "chunks": 0, "chunks_00vd": 0, "chunk_counts": {},
                "h264_bytes": 0, "aus": 0, "ffmpeg_input_bytes": 0,
                "decoded_frames": 0, "published_frames": self.bus.published,
                "viewer_frames": self.bus.viewer_frames,
            }),
            "pipeline": self.pipeline_view(),
            "recording": self.recording_status(),
            "ai": self.ai.status() if self.ai else {"enabled": False},
            "health": self.health(),
            "ws_clients": self.ws.count,
        }

    def _pipeline_fps(self) -> float:
        if self.mode == "live" and self.camera._pipeline is not None:
            return self.camera._pipeline.fps
        # demo fps estimate from publish cadence
        return 15.0 if self.mode == "demo" else 0.0

    def recording_status(self) -> dict:
        r = self.recorder
        if r.active is None:
            return {"active": False}
        return {
            "active": True, "id": r.active.id, "path": str(r.active.path),
            "elapsed": round(r.active.elapsed, 1), "frames": r.active.frames,
            "paused": r.active.paused,
        }

    def health(self) -> dict:
        def status_of(ok: bool, warn: bool = False, disabled: bool = False, error: bool = False) -> str:
            if disabled:
                return "DISABLED"
            if error:
                return "ERROR"
            if warn:
                return "WARNING"
            return "OK" if ok else "ERROR"

        latest = self.bus.latest()
        frame_age = (time.time() - latest.wall_time) if latest else None
        fps = self._pipeline_fps()
        ffmpeg_ok = self.ffmpeg.found
        import shutil
        disk = shutil.disk_usage(str(__import__("pathlib").Path.cwd()))
        camera_state = self.camera.state
        ai_enabled = bool(self.ai and self.ai.enabled)
        return {
            "camera": {"status": status_of(camera_state == "streaming",
                                           error=camera_state == "error",
                                           disabled=camera_state == "disconnected"),
                       "detail": camera_state},
            "stream": {"status": status_of(fps > 0, warn=0 < fps < 5, disabled=self.mode == "idle"),
                       "detail": f"{fps} fps"},
            "decoder": {"status": status_of(self.mode != "live" or fps > 0,
                                            disabled=self.mode != "live"),
                        "detail": "ffmpeg" if self.mode == "live" else "-"},
            "ffmpeg": {"status": status_of(ffmpeg_ok, disabled=False, error=not ffmpeg_ok),
                       "detail": self.ffmpeg.version.split(" ")[2] if ffmpeg_ok else (self.ffmpeg.error or "")[:80]},
            "ai": {"status": "OK" if ai_enabled else "DISABLED",
                   "detail": str(settings_svc.get("ai.mode"))},
            "storage": {"status": "OK" if disk.free > 1024 ** 3 else "WARNING",
                        "detail": f"{disk.free / 1024 ** 3:.1f} GB free"},
            "database": {"status": "OK", "detail": f"v{db.schema_version()}"},
            "frame_age": frame_age,
        }

    def _state_loop(self) -> None:
        rate = float(settings_svc.get("stream.ws_rate_hz", 2))
        while not self._stop.wait(1.0 / max(0.5, rate)):
            try:
                self.apply_mode()  # react to setting changes each tick
                self.health_machine.evaluate(self._health_signals())
                snap = self.state_snapshot()
                self.ws.publish_threadsafe(snap)
            except Exception:
                LOG.exception("state loop error")


# ---- module-level singleton -----------------------------------------------------
_hub: Optional[Hub] = None
_hub_lock = threading.Lock()


def get_hub() -> Hub:
    global _hub
    with _hub_lock:
        if _hub is None:
            _hub = Hub()
        return _hub
