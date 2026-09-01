# Gear360 Hub

**Smart Open Platform for Samsung Gear 360 (2017 — SM-R210)**

Gear360 Hub revives the Samsung Gear 360 2017 as a modern, offline-first smart
camera platform: live 360° viewing, recording, a media gallery, an event
timeline, and a modular AI engine (motion / person / face / recognition /
tracking / smart framing / optional attendance) — all running locally on
Windows with **no cloud and no internet required**.

```text
Gear 360 (SM-R210)  --Wi-Fi-->  Gear360 Hub  -->  Live 360 Viewer
                                              -->  Recording + Gallery
                                              -->  Events + AI Modules
```

---

## 1. Supported camera

| | |
|---|---|
| Device | Samsung Gear 360 2017 Edition — **SM-R210** |
| Verified firmware | `R210GLU0ARB2` |
| Connection | Camera Wi-Fi AP (`Gear 360(02F4)`), camera IP `192.168.43.1` |
| Live preview | `http://192.168.43.1:7679/livestream_high.avi` |
| Response | `HTTP 206 Partial Content`, `Content-Type: video/x-avi` |

The preview endpoint is **not** a normal AVI file. It is a custom chunked
container (`TTTS` / `VID0` / `AUD0` / `VR00` / `LIST` / `movi` / `00VD` /
`00AU` / `00VR`) carrying HEVC/H.264 access units. Gear360 Hub implements a
dedicated parser for it (based on the public `TecCheck/Gear360App` extractor
logic, reimplemented cleanly for a threaded server pipeline).

Camera support is profile-based (`config/camera_profiles.json`): SM-R210 is
bundled; future devices can be added without touching the core.

---

## 2. Architecture

```text
Gear360 Hub
├── Camera Layer    app/camera/    state machine, health probe, latency, profiles
├── Stream Layer    app/stream/    HTTP reader + Gear360 chunk parser (atomic)
├── Media Layer     app/media/     FFmpeg locator/decoder, bounded FrameBus,
│                                  recorder, snapshots, 360 projections
├── 360 Engine      app/media/projections.py + frontend/js/gl360.js
│                                  dual-fisheye -> equirect/perspective/fisheye/
│                                  tiny-planet, virtual cameras, AI director
├── AI Engine       app/ai/        motion, person (OpenCV HOG / YOLO-ready),
│                                  face detection, face recognition (optional),
│                                  centroid tracking, rule-based director,
│                                  smart attendance module
├── Event Engine    app/events/    timeline events with snapshots + coordinates
├── Storage Layer   app/storage/   media library, SQLite (schema versioned)
└── UI Layer        frontend/      Control-center web UI (AR/EN, RTL, offline)
```

Key design points:

- **Offline-first** — every core feature works without internet. No CDN, no
  external fonts; the UI is served locally by the API server.
- **Bounded memory** — the FrameBus keeps only the latest frame plus small
  drop-oldest taps; a slow consumer can never grow RAM unbounded.
- **Windows-first** — `setup.bat` / `run_gateway.bat`; FFmpeg can be bundled
  in `runtime/ffmpeg/` (no PATH requirement).
- **Secure defaults** — the server binds `127.0.0.1`; LAN access is an
  explicit setting. No credentials are stored in git or logs.

---

## 3. Installation (Windows 11)

```bat
1. Install Python 3.11+  (python.org, "Add to PATH" checked)
2. Double-click  setup.bat
```

`setup.bat` creates `.venv`, installs dependencies, and checks FFmpeg.

### FFmpeg (needed for decoding + recording)

Any **one** of these works — the Hub auto-detects it:

1. **Bundled (recommended, fully offline):** download a Windows build from
   <https://www.gyan.dev/ffmpeg/builds/> and put `ffmpeg.exe` (and
   `ffprobe.exe`) inside `runtime\ffmpeg\`.
2. **PATH:** install FFmpeg normally.
3. **Custom path:** set it in the UI → Settings → FFmpeg (with a Test button).

If FFmpeg is missing, the Hub still runs (demo mode, gallery, events) and
recording/decoding show a clear warning instead of crashing.

### Optional AI extras (face recognition)

```bat
.venv\Scripts\activate
pip install -r requirements-ai.txt
```

Without it, everything else works; recognition simply reports "disabled".

---

## 4. Run

```bat
run_gateway.bat
```

Then open: **http://127.0.0.1:8765/**

| Script | Purpose |
|---|---|
| `setup.bat` | first-time installation |
| `run_gateway.bat` | start the Hub (127.0.0.1 by default) |
| `run_dev.bat` | development mode with auto-reload |

LAN access: Settings → Network → `network.lan_access`, then start with
`set HUB_HOST=0.0.0.0` before `run_gateway.bat`.

---

## 5. Connect the camera

1. Put the PC on the camera's Wi-Fi (the camera acts as an AP without
   internet — this is fine, the Hub is offline-first).
2. UI → **Camera** → *Test Connection* (TCP + HTTP probe with latency).
3. UI → **Camera** → **Connect**. The state pill moves
   `Connecting → Streaming` and the live preview starts.

Connection settings (IP / port / path / auto-reconnect / health interval)
live in **Settings → Camera** and the DB — nothing is hard-coded.

> If the IP ever changes, edit it in Settings; the health probe re-checks
> reachability every few seconds.

---

## 6. Live 360 Viewer

UI → **Live 360° Viewer**. The dual-fisheye preview is reprojected in WebGL:

| Projection | Description |
|---|---|
| `Perspective` | look around (drag = pan/tilt, wheel = FOV zoom) |
| `360° Equirectangular` | flat stitched 360 view |
| `Fisheye` | single raw lens (front/back) |
| `Tiny Planet` | stereographic "little planet" |
| `Raw` | untouched camera frame |

Also included: FOV slider, fullscreen, reset view, **lens calibration panel**
(FOV / radius / offset / roll — stored in settings), snapshot button (saves
the current projection), record/pause/stop, and **Virtual Cameras** — save any
(yaw, pitch, FOV, projection) as a reusable preset, jump between them, and
open recorded 360 videos in the same viewer (`#/live?recording=ID`).

---

## 7. Demo Mode (no camera needed)

Settings → System → **Demo Mode** (or `POST /api/system/demo?enabled=true`).

The Hub generates a synthetic dual-fisheye scene (moving "people", grid
sphere, timestamp) injected into the normal pipeline. Every surface shows a
clear amber **DEMO MODE** badge; demo never mixes with live camera data. This
is how you can develop/test the UI, gallery, events and AI without the device.

---

## 8. AI Engine

UI → **AI Engine**. Everything runs locally on CPU:

| Module | Backend | Notes |
|---|---|---|
| Motion detection | frame-diff + contours | sensitivity / min-area / cooldown |
| Person detection | OpenCV HOG (built-in) | YOLO backend ready via abstraction |
| Face detection | OpenCV Haar (built-in) | independent from recognition |
| Face recognition | `face_recognition` (optional) | 128-d embeddings in SQLite |
| Tracking | centroid tracker | track ids, first/last seen, person-left |
| AI Director | rule-based + hysteresis | picks best of 4 analysis views |
| Attendance | optional module | check-in/out per person per day, cooldown |

AI analyses **perspective views** reprojected from the 360 scene (yaw
0°/90°/180°/270°) — detectors see rectilinear imagery, not fisheye. Detections
and the director's best view stream live to the UI over WebSocket; events land
in the timeline with snapshots and scene coordinates.

> The detector abstraction (`app/ai/base.py`: `NullDetector` /
> `OpenCVPersonDetector` / `YOLODetector`) makes swapping models a config
> change, not a rewrite.

---

## 9. Events, Gallery, People

- **Events** — live timeline (`motion`, `person`, `face`, `recognition`,
  `attendance`…), each with timestamp, confidence, snapshot, scene
  coordinates, track/person references. Type filters + 24h stats.
- **Gallery** — photos + recordings with thumbnails, type badges, favorites,
  size/duration, disk usage; 360 videos open in the 360 viewer.
- **Recordings** — management table (play in 360°, download, delete).
- **People** — face database: name, ID, department, notes, photo upload,
  embedding status, recognition counters.

Storage layout (auto-organized, timestamped names):

```text
recordings/2026/09/02/01/2026-09-02_01-34-20.mp4
photos/2026/09/02/2026-09-02_01-35-10_pano.jpg
photos/events/...            (AI snapshots)
```

---

## 10. Dashboard & health

The dashboard shows camera state, FPS/resolution, live preview, AI counters,
storage KPIs, recent events and a **health matrix** (Camera / Stream / Decoder
/ FFmpeg / AI / Storage / DB) with `OK / WARNING / ERROR / DISABLED` states.
Logs are split per domain into `logs/app.log`, `camera.log`, `stream.log`,
`decoder.log`, `ai.log`, `errors.log` (rotating) and mirrored for the UI.

---

## 11. REST API & WebSocket

Everything the UI does is available over HTTP (all local):

```text
GET  /api/camera/status        POST /api/camera/connect|disconnect|test
GET  /api/stream/status        GET  /api/mjpeg            GET  /api/stream/frame.jpg
GET  /api/stream/snapshot      POST /api/recording/start|stop|pause
GET  /api/recordings           GET  /api/photos           GET  /api/gallery/stats
GET  /api/events               GET  /api/events/timeline  GET  /api/attendance/summary
GET  /api/people               POST /api/people           POST /api/people/{id}/photo
GET  /api/ai/status            POST /api/ai/config
GET  /api/settings             PUT  /api/settings
GET  /api/system/health        GET  /api/system/info      GET  /api/system/logs
GET  /api/vcameras             POST /api/vcameras
WS   /api/ws                   (state, detections, events — live push)
```

Interactive docs: `http://127.0.0.1:8765/docs`

---

## 12. Database

SQLite at `data/gear360hub.db` (WAL mode) with schema versioning
(`app/core/database.py` migrations): `settings`, `cameras`, `recordings`,
`photos`, `events`, `people`, `face_embeddings`, `virtual_cameras`,
`ai_sessions`, `system_logs`, `attendance`.

---

## 13. Tests

```bash
pip install -r requirements-dev.txt
pytest tests/ -q
```

26 tests including a **golden pipeline test**: real H.264 access units
generated by FFmpeg are wrapped in the exact Gear360 container byte layout,
then run through parser → decoder → frames. Parser atomicity (any chunk
boundary), resync, header fields, projections round-trip, settings, API,
recording lifecycle and motion/tracker/director units are covered.

---

## 14. Development

```bash
python -m uvicorn app.main:app --host 127.0.0.1 --port 8765 --reload
```

```text
360_attande/
├── app/                  backend (FastAPI)
│   ├── api/  camera/  stream/  media/  ai/  events/  storage/  core/  services/
│   ├── hub.py            orchestrator (wires all layers + WebSocket)
│   └── main.py           app assembly
├── frontend/             offline web UI (vanilla ES modules + WebGL)
│   ├── js/views/         dashboard, live, gallery, recordings, events,
│   │                     ai, people, camera, settings
│   └── js/gl360.js       dual-fisheye 360 WebGL renderer
├── config/               camera profiles
├── data/ recordings/ photos/ logs/ runtime/   (gitignored, created at runtime)
├── tests/                pytest suite + synthetic stream fixtures
├── setup.bat  run_gateway.bat  run_dev.bat  run.sh
└── requirements*.txt
```

Frontend/backend share the projection math: `app/media/projections.py`
(server, for AI + snapshots) mirrors `frontend/js/gl360.js` (client, for
real-time display) — same conventions, verified by round-trip tests.

---

## 15. Troubleshooting

| Symptom | Fix |
|---|---|
| "FFmpeg not found" | bundle `runtime\ffmpeg\ffmpeg.exe` or install in PATH; test in Settings |
| Camera unreachable | PC must be on the camera Wi-Fi; check IP in Settings; use *Test Connection* |
| Connect stays "connecting" | camera preview stops when battery is low / menu open on camera |
| Recording disabled | FFmpeg missing or no frames flowing (connect camera or enable demo) |
| Recognition disabled | `pip install -r requirements-ai.txt` |
| Port already in use | `set HUB_PORT=8800` then run `run_gateway.bat` |

---

## 16. Roadmap

- [x] Phase 1–8: camera, stream, decoder, 360 viewer, recording, gallery,
      events, motion, person/face detection, tracking, virtual cameras
- [x] Phase 9–10: AI Director, attendance module (MVP)
- [ ] RTSP / WebRTC output from the decoded pipeline
- [ ] Two-click stitched 360 video export (server-side render)
- [ ] YOLO person backend packaging + GPU auto-detection
- [ ] Multi-camera hub (profiles already support it)
- [ ] Timeline scrubber with event markers over recordings

---

## 17. Security notes

- Binds `127.0.0.1` by default; LAN access is an explicit opt-in setting.
- No secrets, tokens, Wi-Fi passwords or personal media ever enter git
  (see `.gitignore`: `data/`, `recordings/`, `photos/`, `logs/`, `.env`).
- Face embeddings are stored locally only; photo storage per person is
  optional.
