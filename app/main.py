"""Gear360 Hub — Stable Edition (CONTROL_ONLY).

Startup (§46): init settings → start FastAPI → serve UI. It NEVER starts
FFmpeg, AI, streams, decoders, diagnostics runners, or anything on 7679.

Banner (§47):
    Gear360 Hub Stable
    Camera: SM-R210
    Mode: CONTROL_ONLY
    Live Stream: DISABLED
    AI: DISABLED

Bind: 127.0.0.1 by default (§28). The old live-stream app lives in
legacy/ and is not imported from here.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .stable.api import router as stable_router

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
LOG_PATH = Path(__file__).resolve().parent.parent / "logs" / "hub.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout),
              logging.FileHandler(LOG_PATH, encoding="utf-8")],
)
LOG = logging.getLogger("stable.main")


def print_banner() -> None:
    print(
        "\n"
        "  Gear360 Hub Stable\n"
        "  Camera: SM-R210\n"
        "  Mode: CONTROL_ONLY\n"
        "  Live Stream: DISABLED\n"
        "  AI: DISABLED\n",
        flush=True,
    )


def create_app() -> FastAPI:
    app = FastAPI(
        title="Gear360 Hub — Stable Edition",
        version="2.0.0",
        description="Control-plane only hub for Samsung Gear 360 SM-R210 "
                    "(HTTP 8888 / FTP 21 / Telnet 23). No live stream.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1", "http://localhost"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(stable_router)

    @app.get("/api/health")
    def hub_health():
        return {"ok": True, "edition": "stable", "mode": "CONTROL_ONLY"}

    if FRONTEND_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)),
                  name="static")

        @app.get("/", include_in_schema=False)
        def index():
            return FileResponse(FRONTEND_DIR / "index.html")

    return app


app = create_app()
print_banner()
