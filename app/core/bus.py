"""Tiny in-process pub/sub event bus used across layers (decoupled from WS)."""
from __future__ import annotations

import threading
from typing import Callable


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[str, list[Callable]] = {}
        self._lock = threading.Lock()

    def subscribe(self, topic: str, cb: Callable) -> None:
        with self._lock:
            self._subs.setdefault(topic, []).append(cb)

    def publish(self, topic: str, payload: object) -> None:
        with self._lock:
            cbs = list(self._subs.get(topic, ()))
        for cb in cbs:
            try:
                cb(payload)
            except Exception:
                pass  # never break the publisher


bus = EventBus()
