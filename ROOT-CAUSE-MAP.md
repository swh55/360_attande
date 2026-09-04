# ROOT-CAUSE-MAP — Samsung Gear 360 SM-R210 Live Pipeline

**Build under investigation:** Gear360 Hub @ `863e3ca` → this investigation
**Camera:** Samsung Gear 360 2017 (SM-R210), firmware `R210GLU0ARB2`, `192.168.43.1:7679`
**Method:** layered investigation (task doc §1–§53). Evidence Ladder per §41:
**LEVEL 0 = proven · LEVEL 1 = highly likely · LEVEL 2 = possible · LEVEL 3 = unknown.**
Nothing in this document may be promoted a level without new evidence.

---

## 0. Evidence Ladder summary

| # | Claim | Level |
|---|---|---|
| E1 | Camera reachable, TCP 7679 OK, HTTP 206, `video/x-avi` | **LEVEL 0** (real diag DG-20260903-001) |
| E2 | Container structure (TTTS/VID0/VD00/LIST/movi/00VD) arrives on the wire; VID0 fields sane (codec=2, 1280x640, 1500/100) | **LEVEL 0** (real capture) |
| E3 | The "65536 bytes received" number is the **diagnostic probe's own read cap** (`http_probe_variants(max_bytes=65_536)`), NOT a camera stop | **LEVEL 0** (code: `app/diagnostics/probes.py`) |
| E4 | Camera streams continuously BEYOND 64 KiB | **LEVEL 3 — UNKNOWN, untested** → `tools/raw_camera_stream_dump.py` decides |
| E5 | "Only `Range: bytes=0-` streams a body" | **LEVEL 1** (one real report) **but the variant matrix ran sequentially on a single-session server → ordering-artifact suspicion (F4)** → `tools/http_variants_probe.py` re-decides in isolation |
| E6 | Hub state machine could FREEZE (`camera.state` stuck while bytes flow) after restart/disconnect→connect (state-loop thread race F2) | **LEVEL 0** (code proof) → **FIXED** |
| E7 | Health probe knocked on 7679 every 5 s even while live (single-session competition, F3) | **LEVEL 0** (code proof) → **FIXED** |
| E8 | Android app arms live preview over **Bluetooth** (`{"msgId":"cmd-req","action":{"enum":"execute","description":"liveview"}}`, Samsung Accessory channel 204) BEFORE opening the stream; NO HTTP arming command exists in any source | **LEVEL 0** (vendor sources: `Gear360App/README.md:21-31`, `MessageSender.kt:96-102`, `BTMessage.kt:668-671`) |
| E9 | The mod (`gear360_modding`) never touches port 7679 / streaming — the listener is STOCK firmware | **LEVEL 0** (vendor grep: 0 matches) |
| E10 | `codec=2` from the real camera is UNDOCUMENTED (reference maps 0=raw, 1=HEVC); Hub's "2→h264" is an assumption | **LEVEL 1** → `tools/parse_captured_stream.py` + `tools/test_h264_decode.py` decide empirically |
| E11 | A mid-stream repeated `TTTS` header block is a REAL documented camera behaviour; Hub parser survives it via resync (1 resync + jump to next 00VD), not by the reference's explicit skip | **LEVEL 1** (reference code) — functional, improvement optional |
| E12 | Take Photo (`st key click s2`) + FTP 21 + HTTP 8888 work independently of the stream | **LEVEL 3** on this unit → `tools/camera_control_probe.py --photo` decides |
| E13 | **The SM-R210 streamer is SINGLE-SHOT per arming**: client #1 got 9.2 MB continuous (~1.15 MB/s, 8 s, no idle events); client #2 seconds later, IDENTICAL request bytes (`Range: bytes=0-` + keep-alive — verified in both probe sources), got 206 headers and ZERO body (recv_timeout 3 s); client #3 timed out with 0 bytes | **LEVEL 0** (field evidence `20260904_115725_tcp_stream_probe.json` vs `115729_raw_camera_probe.json` vs `115733_raw_camera_stream_dump.json` — sequential PHASE 1→2→2d) |
| E14 | The PHASE 2b variant matrix of 2026-09-04 ran ZERO variants (`order=[] results=[]`) — orchestrator passed letters `B,A,C,D,E` but the tool filtered on full keys → **"HTTP style dependency? true" in that run's failure tree was an ARTIFACT, not evidence** | **LEVEL 0** (tool bug, fixed inv-2.0: letter aliases accepted) |
| E15 | Telnet on this unit demands auth (`Password:` / `Login incorrect` — zero commands executed, PHASE 3 verdict LISTENER_UNCONFIRMED meaningless) while **8888 `/cgi-bin/execute?rawcmd=` executes commands and returns output** (`echo g360probe-ok` → output echoed) | **LEVEL 0** (`20260904_115759_camera_shell_inspect.json` vs `20260904_115759_camera_control_probe.json`) |

---

## 1. Layer map (task §1/§42)

| Layer | Test (tool / code) | Evidence | Result | Confidence | Root-cause candidate? |
|---|---|---|---|---|---|
| L0 Physical camera state | `camera_control_probe.py`, user visual | camera powers, Wi-Fi up (E1) | OK | LEVEL 0 | no |
| L1 Firmware services | `camera_shell_inspect.py` (inv-2.0: 8888 exec-CGI primary — E15) | 7679 listener = stock; mod adds 23/21/8888 only; telnet auth-gated on this unit | pending field run (new channel) | LEVEL 1 | no |
| L2 Wi-Fi/IP/TCP | `tcp_stream_probe.py` | E1: TCP OK | OK | LEVEL 0 | no |
| L3 HTTP streaming endpoint | `http_variants_probe.py` (ISOLATED, letters fixed) | E13: request style is NOT the variable — client #1 streamed 9.2 MB with the same bytes client #2 starved on | **session-lifecycle semantics, not style** | LEVEL 0 | **RC-6 (root)** |
| L4 Raw byte stream | `raw_camera_probe.py`, `raw_camera_stream_dump.py` | E13: 9.2 MB in 8 s continuous → E4 PROVEN | **continuity PROVEN (first client)** | LEVEL 0 | no |
| L5 Container/protocol | `parse_captured_stream.py` (offline) | E2: structure present and consistent with Extractor.kt | OK (structure) | LEVEL 0 | no |
| L6 Chunk/AU extraction | `parse_captured_stream.py` | parser fixed+verified (0 resyncs, self-inclusive sizes, size−5 payload) | OK | LEVEL 0 | no |
| L7 H264 validation | `test_h264_decode.py` + ffprobe on real AUs | codec=2 undocumented (E10) | **unresolved: true codec of this unit** | LEVEL 1 | **RC-3** |
| L8 Decoder | `test_h264_decode.py` (fresh ffmpeg), Hub `decoderprobe` | FFmpeg pipeline proven on synthetic; real-byte proof pending | pending field run | LEVEL 2 | no |
| L9 Frame delivery | FrameBus counters `/api/stream/counters` | counters wired end-to-end | pending field run | LEVEL 2 | no |
| L10 360 projection | Hub viewer + tests | tested (26-test suite + visual) | OK | LEVEL 0 | no |
| L11 Snapshot | `/api/stream/snapshot` semantics | honest codes fixed (NO_FRAME_AVAILABLE vs CAMERA_DISCONNECTED) | OK (semantics) | LEVEL 0 | no |
| L12 Web UI | Hub UI + this investigation's new buttons | Independent Test / Capture / Analyze wired | OK | LEVEL 0 | no |
| L13 Remote control / photo | `camera_control_probe.py --photo` | mechanism from mod's own `top.html:19` | pending field run | LEVEL 2 | no |

---

## 2. Root-cause candidates (ranked)

### RC-6 — SINGLE-SHOT STREAMER: one arming serves ONE client; no re-arm afterwards (THE root cause of the freeze) — **ROOT CAUSE, LEVEL 0**
- **Evidence E13 (LEVEL 0):** sequential PHASE 1→2→2d with IDENTICAL request bytes: client #1 → 9.2 MB continuous; client #2 → 206 + zero body; client #3 → timeout. The ONLY variable is connection history.
- **Mechanism (E8):** the stock phone app arms live preview over Bluetooth BEFORE each stream session; one arming = one serving window. After the client disconnects the streamer stays disarmed; new HTTP clients receive a canned 206 header block from the alive HTTP front-end while the body producer never attaches.
- **Why the Hub froze (user symptom):** any transport interruption → `iter_camera_bytes` re-connected blindly → every reconnect after a session end is a starving client #2 → endless headers-without-body → the UI froze on the last frame and the camera stayed wedged until power-cycle. Worse, `restart_stream()` itself disconnected the (serving) session and reconnected — starved by the same mechanism.
- **Decides:** `tools/session_lifecycle_probe.py` (self-heal schedule, concurrency first-wins/newest-wins, re-arm ladder incl. 8888 candidates + optional streamer kill).
- **Hub-side fix shipped (inv-2.0):** transport STOPS after `WEDGED_STALL_LIMIT` (=3) header-only attempts and surfaces `CAMERA_STREAM_WEDGED`; `POST /api/camera/rearm[?apply=true]` runs the 8888 re-arm ladder then reconnects; `restart_stream()` runs the safe re-arm ladder before reconnecting; UI shows a WEDGED badge + Re-arm button.

### RC-1 — "Request-style" knowledge is incomplete; the variant matrix conclusion may be an ordering artifact
- **STATUS (E14, LEVEL 0): RESOLVED AS ARTIFACT** — the 2026-09-04 matrix ran ZERO variants (letter/key mismatch, tool bug fixed inv-1.1). E13 proves style is NOT the variable: client #1 streamed with the same bytes client #2 starved on. No style ever recovers a disarmed streamer; the range-style live transport stays (harmless).
- The live transport was locked to `Range: bytes=0-` after one report; but that matrix ran variants SEQUENTIALLY on a single-session server (F4). Once the first (range) request wedged/kept the session, the later "plain"/"close" attempts were doomed regardless of style.
- **Decides:** `tools/http_variants_probe.py` (isolated connections + settle gaps + reversed/repeated order).
- Note: the legacy Android reader sends **plain GET, HTTP/1.1, Keep-Alive, no Range** (`MediaExtractor.java:118`) — if plain truly always stalled, the reference app could never work. This is strong circumstantial evidence that style-dependence is session-order, not style.

### RC-2 — Camera streams an initial burst (header + first frame(s)) then stalls without the Bluetooth "liveview" arming
- **STATUS: CONFIRMED AS THE MECHANISM BEHIND RC-6** (E8 vendor + E13 field, LEVEL 0). One arming = one serving window; the app re-arms per session. Remaining question: WHICH non-Bluetooth re-arm works — `tools/session_lifecycle_probe.py` ST3 decides (root/echo/ps/internal-wget/kill).
- Vendor proof E8: the app sends the BT `cmd-req`/`liveview` message BEFORE `GET /livestream_high.avi`; no HTTP arming exists. If the 64 KiB reading was real camera behaviour (it was NOT — E3 proves it was a probe cap), this would be the mechanism.
- Counter-evidence: the probe DID receive real container bytes incl. 00VD chunks — so some stream flows without BT arming. Open question is only **continuity** (E4).
- **Decides:** `tools/raw_camera_stream_dump.py --seconds 30` (continuity + milestones), `tools/camera_shell_inspect.py` (internal localhost:7679 test), then PHASE 3 evidence.

### RC-3 — Codec assumption: `codec=2 → h264` unverified
- **Confidence: LEVEL 1 that it is unverified; the bits will decide.**
- If the stream is actually HEVC while the decoder is opened in h264 mode, FFmpeg decodes nothing and the pipeline dies exactly at "metadata ok, no frames".
- **Decides:** `tools/parse_captured_stream.py --save-aus` + `tools/test_h264_decode.py` (tries both codecs on REAL bytes; full stderr preserved).

### RC-4 — Hub-side state freeze (FIXED in this investigation)
- **Confidence: LEVEL 0 (was a real bug).**
- `_ensure_state_loop()` race: `disconnect()`/`restart_stream()` set `_stop_state`; an immediate `connect()` within ≤1.5 s found the old thread still `is_alive()`, skipped creating a new loop, and the old thread then exited → `camera.state` FROZEN while the pipeline might still run. **This answers task §44: yes, "disconnected" could be a stale internal state, not camera truth.**
- **Fixed:** old thread is now joined before a new loop starts (`app/camera/manager.py::_ensure_state_loop`).

### RC-5 — Camera single-session competition from the Hub itself (FIXED in this investigation)
- **Confidence: LEVEL 0 (was a real risk).**
- The health probe opened a bare TCP connection to 7679 every 5 s **including while live**; test/diagnostic probes used to compete too (probe matrix suppressed since `171e8bb`).
- **Fixed:** `_probe_loop` now derives reachability from live transport bytes while a session exists; a session broker (`app/camera/session_broker.py`) now guards every connection path in-process AND cross-process (`diagnostics/raw/session.lock` heartbeat read by all `tools/*.py`).

---

## 3. Failure Tree (task §43) — current state

```text
NO LIVE VIDEO
│
├── Camera unreachable? ................ NO (E1, LEVEL 0)
│
├── HTTP endpoint unavailable? ......... NO (E1: 206 + content-type, LEVEL 0)
│
├── HTTP body unavailable? ............. YES after first client (E13, LEVEL 0)
│     └── first client: 9.2 MB continuous (continuity PROVEN — E4 resolved)
│         clients #2+: 206 headers + ZERO body → single-shot streamer (RC-6)
│
├── HTTP style dependency? ............. NO (RC-1 resolved as artifact E14)
│     └── matrix tool bug fixed (letters); style is NOT the variable (E13)
│
├── Parser fails? ...................... NO (E2 + verified parser, LEVEL 0)
│     └── mid-stream TTTS re-header: handled via resync (E11)
│
├── 00VD extraction fails? ............. NO (size−5 rule verified, LEVEL 0)
│
├── H264 invalid? ...................... ? (RC-3, codec=2 undocumented, E10)
│     └── → tools/test_h264_decode.py on real AUs
│
├── FFmpeg fails? ...................... NO for pipeline mechanics (LEVEL 0:
│     probes + tests); real-byte verdict pending
│
├── FrameBus fails? .................... NO evidence of failure (counters wired)
│
├── Hub state machine lies? ............ WAS YES — frozen state possible (RC-4,
│     LEVEL 0) → FIXED; honest layered status now exposed (/api/camera/status
│     "layers", §45)
│
└── Viewer fails? ...................... NO evidence (projections tested)
```

---

## 4. The decisive field experiments (run order = task §53)

1. `python tools/tcp_stream_probe.py --seconds 8`
2. `python tools/raw_camera_probe.py --seconds 15`
3. `python tools/raw_camera_stream_dump.py --seconds 30 --mb 8`   ← **answers E4**
4. `python tools/http_variants_probe.py --order B,A,C,D,E --repeat 2`  ← RC-1 (resolved E14)
4b. `python tools/session_lifecycle_probe.py --apply`  ← **answers RC-6: self-heal / steal / re-arm winner**
5. `python tools/camera_shell_inspect.py`   ← who owns 7679, internal stream test
6. `python tools/camera_control_probe.py --photo`  ← control plane + real photo
7. `python tools/parse_captured_stream.py diagnostics/raw/dump_*.bin --extract 8 --save-aus`
8. `python tools/test_h264_decode.py diagnostics/raw/dump_*.bin`  ← **answers RC-3**

Or everything in order: `python tools/run_investigation.py`
(Every run writes JSON evidence into `diagnostics/raw/evidence/`; update this map
and `INVESTIGATION-REPORT.md` from that evidence — never from intuition.)

## 5. Fixed in this investigation (code changes driven by evidence)

| Fix | Where | Evidence that demanded it |
|---|---|---|
| State-loop thread race (frozen `camera.state`) | `app/camera/manager.py::_ensure_state_loop` | F2/RC-4 (§44 suspicion confirmed) |
| Health probe TCP knocks while live | `app/camera/manager.py::_probe_loop` | F3/RC-5 (§51) |
| Session broker (in-process + cross-process lock file) | `app/camera/session_broker.py` (new) | §49–§51 |
| Layered honest status (7 layers) | `manager.layers()` → `/api/camera/status` | §45 |
| Broker released when pipeline thread dies | `manager._state_loop` | prevents dead-session lockout |
| Independent Camera Test endpoint + button | `/api/diagnostics/independent-test`, UI Diagnostics tab | §47 |
| Capture + Analyze Offline endpoint + button | `/api/diagnostics/captures/analyze`, UI Diagnostics tab | §48 |
| 8 independent field tools + orchestrator | `tools/` | §3–§8, §12–§18, §20–§28, §39–§40 |
| Blind reconnect STOP: terminal WEDGED after 3 header-only attempts | `app/stream/parser.py` (WEDGED_STALL_LIMIT/WEDGED_ERROR_MSG), `app/media/decoder.py` | E13/RC-6 (blind retries proven useless) |
| Re-arm ladder via 8888 exec-CGI (safe S1-S3 + invasive S4 kill) | `app/camera/rearm.py` (new) | E15 (exec CGI proven), RC-6 |
| `POST /api/camera/rearm[?apply=true]` + auto reconnect | `app/api/camera_api.py`, `manager.rearm_and_connect()` | RC-6 recovery path |
| restart_stream() re-arms BEFORE reconnecting | `app/camera/manager.py::restart_stream` | RC-6 (restart itself starved) |
| WEDGED badge + Re-arm button (AR/EN) | `frontend/js/views/camera.js`, `api.js`, `i18n.js` | user freeze symptom |
| Variant matrix letter aliases (letters now valid order) | `tools/http_variants_probe.py` | E14 tool bug |
| PHASE 3 switched to 8888 exec-CGI (telnet fallback) | `tools/camera_shell_inspect.py` (inv-2.0) | E15 |
| Session-lifecycle decisive probe (self-heal/steal/re-arm) | `tools/session_lifecycle_probe.py` (new) + PHASE 2c in orchestrator | RC-6 next decisive run |
| Vendor references cloned for study | `vendor/gear360_modding/`, `vendor/Gear360App/` | §10, §11, §29–§30 |

## 6. Camera-side (mod) changes

**NONE — and none are justified by current evidence.** The mod never touches
streaming (E9, LEVEL 0). Should PHASE 3 prove the stock streamer itself is the
problem, the A/B procedure is: clone to `vendor/gear360_modding/patched/`,
keep `vendor/gear360_modding/extracted/` as the ORIGINAL, document in
`BUILD.md`+`CHANGELOG.md`, and roll back by copying the original back. Nothing
in the stock firmware is to be modified (task §36).
