from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

import cv2
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse

from .decoder import FrameBus, Gear360DecoderWorker

BASE = Path(__file__).resolve().parent.parent
CONFIG = json.loads((BASE / "config.json").read_text(encoding="utf-8"))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(title="Gear 360 Attendance Gateway", version="0.2.0")
bus = FrameBus()
worker = Gear360DecoderWorker(CONFIG["camera"], bus)
worker_started = False
worker_lock = threading.Lock()


def ensure_worker() -> None:
    global worker_started
    with worker_lock:
        if not worker_started:
            worker.start()
            worker_started = True


@app.on_event("startup")
def startup() -> None:
    ensure_worker()


@app.get("/", response_class=HTMLResponse)
def home() -> str:
    return """<!doctype html><html><head><meta charset='utf-8'><title>Gear 360 Gateway</title>
    <style>body{font-family:Arial,sans-serif;max-width:1100px;margin:30px auto;padding:0 20px}img{max-width:100%;background:#111;border-radius:8px}.grid{display:grid;grid-template-columns:1fr 320px;gap:24px}.card{padding:16px;border:1px solid #ddd;border-radius:10px}pre{white-space:pre-wrap}</style></head>
    <body><h1>Gear 360 Attendance Gateway</h1><div class='grid'><div><img src='/mjpeg' alt='Live preview'></div><div class='card'><h2>Status</h2><pre id='s'>Loading...</pre></div></div>
    <script>async function refresh(){const r=await fetch('/api/status');document.getElementById('s').textContent=JSON.stringify(await r.json(),null,2)}setInterval(refresh,1000);refresh();</script></body></html>"""


@app.get("/api/status")
def status() -> dict[str, Any]:
    info = worker.info
    return {
        "camera": CONFIG["camera"],
        "worker": {
            "started": worker_started,
            "encoded_frames": worker.encoded_frame_count,
            "decoded_frames": worker.decoded_frame_count,
            "error": worker.error,
            "stream": None if info is None else {
                "codec": info.codec,
                "width": info.width,
                "height": info.height,
                "frame_rate": info.frame_rate,
            }
        },
        "ai": CONFIG["ai"],
    }


@app.get("/mjpeg")
def mjpeg() -> StreamingResponse:
    ensure_worker()
    def gen():
        while True:
            item = bus.latest()
            if item is None:
                time.sleep(0.05)
                continue
            ok, jpeg = cv2.imencode('.jpg', item.image, [cv2.IMWRITE_JPEG_QUALITY, 80])
            if ok:
                yield b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: ' + str(len(jpeg)).encode() + b'\r\n\r\n' + jpeg.tobytes() + b'\r\n'
            time.sleep(0.03)
    return StreamingResponse(gen(), media_type='multipart/x-mixed-replace; boundary=frame')
