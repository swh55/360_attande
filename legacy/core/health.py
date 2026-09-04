"""Formal health state machine for the streaming pipeline.

States (mutually exclusive, reported to the UI):
  DISCONNECTED → CONNECTING → CONNECTED → STREAMING → PARSING → DECODING → READY
  plus ERROR and RECONNECTING.

Principle (do not fake success): a TCP handshake is CONNECTED, bytes flowing is
STREAMING, valid parser metadata is PARSING, a running decoder is DECODING —
only fresh decoded frames are READY.
"""
from __future__ import annotations

import time
from collections import deque

STATES = ("DISCONNECTED", "CONNECTING", "CONNECTED", "STREAMING",
          "PARSING", "DECODING", "READY", "ERROR", "RECONNECTING")

_FRESH_FRAME_S = 3.0


class HealthStateMachine:
    def __init__(self) -> None:
        self.state = "DISCONNECTED"
        self.previous = "DISCONNECTED"
        self.since = time.time()
        self.reason = "startup"
        self.layers: dict = {}
        self.history: deque = deque(maxlen=30)

    def evaluate(self, s: dict) -> dict:
        """s: signals dict from StreamPipeline.signals() + camera/hub context."""
        now = time.time()
        camera_state = s.get("camera_state") or "disconnected"
        mode = s.get("mode") or "idle"
        frame_age = s.get("frame_age")
        frame_ok = frame_age is not None and frame_age <= _FRESH_FRAME_S
        pipeline_error = s.get("error")

        layers = {
            "camera": {
                "state": "ok" if s.get("camera_reachable") else "fail",
                "detail": f"{s.get('latency_ms') or '—'} ms" if s.get("camera_reachable") else "not reachable",
            },
            "http": {
                "state": "ok" if s.get("http_ok") else ("fail" if camera_state == "streaming" else "unknown"),
                "detail": f"HTTP {s.get('http_status')}" if s.get("http_status") else "no response yet",
            },
            "stream": {
                "state": "ok" if (s.get("bytes_seen") or 0) > 0 else (
                    "warn" if s.get("transport_status") == "stalled" else "fail"),
                "detail": (
                    f"{s.get('bytes_seen') or 0} bytes · {s.get('bytes_per_sec') or 0} B/s"
                    if (s.get("bytes_seen") or 0) > 0
                    else ("headers received, body stalled" if s.get("transport_status") == "stalled"
                          else "no stream bytes yet")),
            },
            "parser": {
                "state": "ok" if (s.get("chunks_seen") or 0) > 0 else ("fail" if (s.get("bytes_seen") or 0) > 0 else "unknown"),
                "detail": f"{s.get('chunks_seen') or 0} chunks · {s.get('resyncs') or 0} resyncs",
            },
            "metadata": {
                "state": "ok" if s.get("parser_valid") else ("fail" if (s.get("chunks_seen") or 0) > 0 else "unknown"),
                "detail": "VID0 valid" if s.get("parser_valid") else "VID0 invalid/unparsed",
            },
            "decoder": {
                "state": "ok" if s.get("decoder_running") else ("unknown" if not s.get("decoder_active") else "fail"),
                "detail": "ffmpeg running" if s.get("decoder_running") else "not running",
            },
            "frame": {
                "state": "ok" if frame_ok else ("fail" if s.get("decoder_active") else "unknown"),
                "detail": f"{frame_age:.1f}s ago" if frame_age is not None else "no frame yet",
            },
        }
        self.layers = layers

        if pipeline_error:
            new, reason = "ERROR", f"pipeline error: {pipeline_error}"
        elif camera_state == "reconnecting":
            new, reason = "RECONNECTING", "camera manager is reconnecting"
        elif camera_state == "connecting":
            new, reason = "CONNECTING", "connect requested"
        elif camera_state == "disconnected" and not s.get("http_ok"):
            new, reason = "DISCONNECTED", "no active pipeline"
        elif mode == "demo" and frame_ok:
            new, reason = "READY", "demo source producing frames"
        elif frame_ok:
            new, reason = "READY", "fresh decoded frames"
        elif s.get("decoder_active") and s.get("parser_valid"):
            new, reason = "DECODING", "decoder running, waiting for frames"
        elif (s.get("chunks_seen") or 0) > 0:
            new, reason = "PARSING", "chunks parsed" + ("" if s.get("parser_valid") else " (metadata invalid)")
        elif s.get("http_ok") and (s.get("bytes_seen") or 0) > 0:
            new, reason = "STREAMING", "bytes flowing, no chunks yet"
        elif s.get("transport_status") == "stalled":
            new, reason = "RECONNECTING", "camera sends headers but never a body — retrying with backoff"
        elif s.get("http_ok") or s.get("camera_reachable"):
            new, reason = "CONNECTED", "transport connected, no stream bytes yet"
        elif self.state in ("STREAMING", "PARSING", "DECODING", "READY"):
            new, reason = "RECONNECTING", "lost stream bytes — reconnecting"
        else:
            new, reason = "CONNECTING", "establishing connection"

        if new != self.state:
            self.previous = self.state
            self.state = new
            self.since = now
            self.reason = reason
            self.history.append({"ts": time.strftime("%H:%M:%S"), "from": self.previous,
                                 "to": new, "reason": reason})
        return self.view()

    def view(self) -> dict:
        return {
            "state": self.state,
            "previous": self.previous,
            "since": round(time.time() - self.since, 1),
            "reason": self.reason,
            "layers": self.layers,
            "history": list(self.history)[-10:],
        }
