"""Capability Center API — features ranked by guarantee (RC-6/RC-7 evidence).

GET  /api/camera/capabilities      non-invasive ladder + tier manifest
POST /api/camera/probe/arming      DECISIVE stream test — user-gated:
                                   consumes a fresh arming if present.
"""
from __future__ import annotations

from fastapi import APIRouter

from ..camera.capabilities import decisive_arming_probe, probe

router = APIRouter(prefix="/api")


@router.get("/camera/capabilities")
def camera_capabilities():
    """Non-invasive capability report — safe to auto-refresh.

    Never touches port 7679: control-plane ping (8888), camera-side socket
    inspector via exec-CGI, session-broker state, wedge-stage classification
    and the guarantee-ordered feature manifest."""
    return probe()


@router.post("/camera/probe/arming")
def probe_arming():
    """Decisive single-connection stream test.

    Contracts honoured by the UI:
      * refused outright while a live session is held (broker guard, RC-5);
      * on a FRESH arming the body flows and the test CONSUMES the arming
        (single-shot streamer — unavoidable, that is what makes it decisive);
      * result is one of ARMED_BODY_OK / HEADERS_ONLY_WEDGED /
        UNREACHABLE_STAGE2 / REFUSED_SESSION_HELD."""
    return decisive_arming_probe()
