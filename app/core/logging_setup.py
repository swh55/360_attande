"""Structured logging with per-domain rotating files: app/camera/stream/decoder/ai/errors.

Each `hub.<domain>` logger gets its own rotating file (logs/<domain>.log) while
console output is handled once on the `hub` root logger. errors.log receives
every record of level >= ERROR across all domains.
"""
from __future__ import annotations

import logging
import logging.handlers
import sys

from .paths import DIRS

_DOMAINS = ("app", "camera", "stream", "decoder", "ai", "events", "media", "api")
_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
configured = False


def setup_logging(level: str = "INFO") -> None:
    global configured
    if configured:
        return
    configured = True
    log_dir = DIRS["logs"]
    fmt = logging.Formatter(_FORMAT)

    root = logging.getLogger("hub")
    root.setLevel(logging.DEBUG)
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(getattr(logging, level.upper(), logging.INFO))
    console.setFormatter(fmt)
    root.addHandler(console)

    for domain in _DOMAINS:
        lg = logging.getLogger(f"hub.{domain}")
        fh = logging.handlers.RotatingFileHandler(
            log_dir / f"{domain}.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
        )
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        lg.addHandler(fh)

    errors = logging.handlers.RotatingFileHandler(
        log_dir / "errors.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    errors.setLevel(logging.ERROR)
    errors.setFormatter(fmt)
    root.addHandler(errors)

    for noisy in ("urllib3", "uvicorn.access", "websockets"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get(domain: str) -> logging.Logger:
    """Return hub.<domain> logger; records also land in logs/<domain>.log."""
    return logging.getLogger(f"hub.{domain}")
