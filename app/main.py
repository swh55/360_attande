"""Gear360 Hub — FastAPI application assembly.

Serves the REST API, WebSocket, MJPEG stream and the built-in frontend
(offline-first; no CDN resources).
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .api.camera_api import router as camera_router
from .api.hub_api import router as hub_router
from .core import database as db
from .core import settings as settings_svc
from .core.paths import DIRS
from .hub import get_hub

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    hub = get_hub()
    hub.start()
    yield
    hub.shutdown()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Gear360 Hub",
        version="1.0.0",
        description="Smart Open Platform for Samsung Gear 360 (SM-R210)",
        lifespan=lifespan,
    )
    # Security default: same-origin only. CORS kept permissive only for localhost dev.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1", "http://localhost"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(camera_router)
    app.include_router(hub_router)

    @app.get("/api/about")
    def about():
        return {"name": "Gear360 Hub", "tagline": "Smart Open Platform for Samsung Gear 360",
                "version": "1.0.0"}

    # ---- frontend (offline-first, served locally) -------------------------
    if FRONTEND_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

        @app.get("/", include_in_schema=False)
        def index():
            return FileResponse(FRONTEND_DIR / "index.html")

        @app.get("/favicon.ico", include_in_schema=False)
        def favicon():
            f = FRONTEND_DIR / "assets" / "favicon.svg"
            if f.exists():
                return FileResponse(f, media_type="image/svg+xml")
            return Response_404()
    else:  # pragma: no cover
        @app.get("/", include_in_schema=False)
        def index_missing():
            return RedirectResponse("/docs")

    return app


def Response_404():
    from fastapi import HTTPException
    raise HTTPException(404)


app = create_app()
