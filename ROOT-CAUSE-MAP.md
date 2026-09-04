# ROOT-CAUSE-MAP — Samsung Gear 360 SM-R210 Live Pipeline

**Build under investigation:** Gear360 Hub @ `171e8bb` → this investigation
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

---

## 1. Layer map (task §1/§42)

| Layer | Test (tool / code) | Evidence | Result | Confidence | Root-cause candidate? |
|---|---|---|---|---|---|
| L0 Physical camera state | `camera_control_probe.py`, user visual | camera powers, Wi-Fi up (E1) | OK | LEVEL 0 | no |
| L1 Firmware services | `camera_shell_inspect.py` (ps/netstat), vendor analysis E9 | 7679 listener = stock; mod adds 23/21/8888 only | pending field run | LEVEL 1 | no |
| L2 Wi-Fi/IP/TCP | `tcp_stream_probe.py` | E1: TCP OK | OK | LEVEL 0 | no |
| L3 HTTP streaming endpoint | `http_variants_probe.py` (ISOLATED) | E1: 206 OK; E5 needs re-proof in isolation | **unresolved: which request styles really work** | LEVEL 1 | **RC-1** |
| L4 Raw byte stream | `raw_camera_probe.py`, `raw_camera_stream_dump.py` | E3: previous evidence capped at 64 KiB; E4 unknown | **unresolved: continuity beyond 64 KiB** | LEVEL 3 | **RC-2** |
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

### RC-1 — "Request-style" knowledge is incomplete; the variant matrix conclusion may be an ordering artifact
- **Confidence: LEVEL 1.**
- The live transport was locked to `Range: bytes=0-` after one report; but that matrix ran variants SEQUENTIALLY on a single-session server (F4). Once the first (range) request wedged/kept the session, the later "plain"/"close" attempts were doomed regardless of style.
- **Decides:** `tools/http_variants_probe.py` (isolated connections + settle gaps + reversed/repeated order).
- Note: the legacy Android reader sends **plain GET, HTTP/1.1, Keep-Alive, no Range** (`MediaExtractor.java:118`) — if plain truly always stalled, the reference app could never work. This is strong circumstantial evidence that style-dependence is session-order, not style.

### RC-2 — Camera streams an initial burst (header + first frame(s)) then stalls without the Bluetooth "liveview" arming
- **Confidence: LEVEL 2.**
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
├── HTTP body unavailable? ............. ?
│     └── previously "65536 then stop" = PROBE CAP artifact (E3, LEVEL 0)
│         real continuity untested (E4) → tools/raw_camera_stream_dump.py
│
├── HTTP style dependency? ............. ? (RC-1)
│     └── old conclusion = ordering-artifact suspect (F4)
│         → tools/http_variants_probe.py (isolated)
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
4. `python tools/http_variants_probe.py --order B,A,C,D,E --repeat 2`  ← **answers RC-1**
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
| Vendor references cloned for study | `vendor/gear360_modding/`, `vendor/Gear360App/` | §10, §11, §29–§30 |

## 6. Camera-side (mod) changes

**NONE — and none are justified by current evidence.** The mod never touches
streaming (E9, LEVEL 0). Should PHASE 3 prove the stock streamer itself is the
problem, the A/B procedure is: clone to `vendor/gear360_modding/patched/`,
keep `vendor/gear360_modding/extracted/` as the ORIGINAL, document in
`BUILD.md`+`CHANGELOG.md`, and roll back by copying the original back. Nothing
in the stock firmware is to be modified (task §36).
