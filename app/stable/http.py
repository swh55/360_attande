"""CameraHttp — health + open for the mod's web interface (HTTP :8888).

Only documented behaviour is used: GET / must answer. No endpoints are
invented (§26). This module never touches port 7679.
"""
from __future__ import annotations

import logging
import time
import urllib.request

LOG = logging.getLogger("stable.http")


class HttpError(RuntimeError):
    pass


def health(host: str, port: int, timeout: float = 5.0) -> dict:
    """GET http://host:port/ → {ok, latency_ms, http_status}."""
    url = f"http://{host}:{port}/"
    t0 = time.time()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            body = r.read(4096)
            ms = round((time.time() - t0) * 1000, 1)
            return {"ok": True, "latency_ms": ms,
                    "http_status": getattr(r, "status", 200),
                    "bytes": len(body), "url": url}
    except Exception as exc:  # noqa: BLE001 — every failure mode is "down"
        return {"ok": False, "latency_ms": None, "http_status": None,
                "bytes": 0, "url": url,
                "error": f"{exc.__class__.__name__}: {exc}"}


def open_url(host: str, port: int) -> str:
    """The camera-web URL the Hub UI opens in a new tab (§42)."""
    return f"http://{host}:{port}/"
