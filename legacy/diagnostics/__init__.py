"""Gear360 Hub — Diagnostics Engine.

Real pipeline diagnostics: probes the actual camera/network/HTTP stream,
inspects raw bytes, validates the parser against evidence, probes FFmpeg in
isolation and attributes failures to a pipeline stage with suggested fixes.
Additive by design: a failing diagnostic never disturbs the live pipeline.
"""
from __future__ import annotations

import threading

from .models import SessionRegistry

_lock = threading.Lock()
_registry: SessionRegistry | None = None
_runner_factory = None


def get_registry() -> SessionRegistry:
    global _registry
    with _lock:
        if _registry is None:
            _registry = SessionRegistry()
        return _registry


def get_runner(hub):
    """Lazily import to avoid circulars; one runner per process."""
    global _runner_factory
    with _lock:
        if _runner_factory is None:
            from .runner import DiagnosticsRunner
            _runner_factory = DiagnosticsRunner(hub)
        return _runner_factory
