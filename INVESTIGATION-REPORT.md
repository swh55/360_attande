# INVESTIGATION-REPORT — Gear360 Hub ↔ Samsung Gear 360 SM-R210

**Date:** 2026-09-04 · **Investigator:** Gear360 Hub root-cause investigation (task doc §1–§60)
**Repository:** `swh55/360_attande` · **Camera:** SM-R210, firmware `R210GLU0ARB2`
**Companion document:** `ROOT-CAUSE-MAP.md` (layer map, failure tree, evidence ladder)

> **STATUS (honest, task §58):** `ROOT CAUSE NOT YET FULLY PROVEN` — the Hub-side
> bugs that could fake or freeze the live state are **proven and fixed**; the
> camera-side continuity/codec questions are now **instrumented with decisive
> field tools** but require a run on the machine that is attached to the camera's
> Wi-Fi. Nothing below claims real-camera streaming success that has not happened.

---

## 1. Proven Working

| Item | Proof | Level |
|---|---|---|
| Camera reachable / TCP 7679 / HTTP 206 `video/x-avi` | real diagnostic DG-20260903-001 | LEVEL 0 |
| Container bytes on the wire: TTTS, VID0 (96B), VD00, AUD0, VR00, LIST+movi, 00VD chunks | real capture | LEVEL 0 |
| VID0 metadata from real bytes: codec=2, scale=120000, fps=1500/100=15, 1280×640, subTag=VD00 | real capture + parser verification | LEVEL 0 |
| Parser record conventions (TTTS fixed 12B; VID0/AUD0/VR00 self-inclusive size; movi chunk = tag4+size4+ts8+payload; payload = bitstream + 5B trailer) | verified against Extractor.kt arithmetic AND real bytes; 0 resyncs | LEVEL 0 |
| FFmpeg pipeline mechanics (spawn, stdin feed, bounded queue, stderr capture, exit-code telemetry) | unit + golden-pipeline tests (73 passed) | LEVEL 0 |
| Snapshot error semantics: `CAMERA_DISCONNECTED` only when unreachable; `NO_FRAME_AVAILABLE` when reachable w/o frames | code + tests | LEVEL 0 |
| gear360_modding services (Telnet 23, FTP 21, HTTP 8888 + `/cgi-bin/execute?rawcmd=st+key+click+s2`) | vendor sources (`G360POWE_G360POW.sh:23-25`, `top.html:19`) — field run pending on this unit | LEVEL 0 (existence) / LEVEL 3 (this unit) |
| Take Photo pipeline design (telnet/`st key click s2` → SD write → FTP detect → gallery) | `app/camera/control.py`, documented mod mechanism | LEVEL 0 (design) |

## 2. Proven Failing

| Item | Proof | Level |
|---|---|---|
| **Hub state machine could freeze** — `camera.state` stuck (e.g. "disconnected"/"connecting") while the transport reality differed | code proof: `_ensure_state_loop()` race with `disconnect()`/`restart_stream()` + immediate `connect()` (F2/RC-4) | LEVEL 0 |
| **Hub competed for the single stream session** — health probe opened TCP to 7679 every 5 s even while live | code proof (`_probe_loop`) (F3/RC-5) | LEVEL 0 |
| **Diagnostic evidence was incomplete** — "65536 bytes" is the probe's own read cap, not a camera stop | code proof: `probes.py::http_probe_variants(max_bytes=65_536)` (F1/E3) | LEVEL 0 |
| **The "Range-only" conclusion is unsafe** — variant matrix ran sequentially on a single-session server | code proof of test design (F4); contradicts the legacy Android reader which uses plain GET (`MediaExtractor.java:118`) | LEVEL 1 |
| Live preview end-to-end on real hardware | never achieved; `decoded_frames=0` in every real report | LEVEL 0 (fact of failure) |

## 3. First Failing Layer

With the Hub-side fixes applied, the **first still-open failing layer is L4 →
L3 boundary**: whether the camera's body stream is **continuous** beyond the
first buffer (L4) and under **which request styles** (L3). Both are now
measurable in one run (`tools/run_investigation.py`). Everything below that
boundary (parser L5–L6) is proven by bytes already captured.

## 4. Root Cause

**Not yet conclusively proven (task §58).** Ranked candidates with their
decisive experiments — see `ROOT-CAUSE-MAP.md` §2:

1. **RC-1 (LEVEL 1):** request-style dependency unproven; old conclusion is an
   ordering-artifact suspect → `http_variants_probe.py` (isolated, repeated).
2. **RC-2 (LEVEL 2):** stream is armed over **Bluetooth** (`cmd-req`/`liveview`,
   Samsung Accessory channel 204) in the reference app; HTTP-only clients may
   get a burst, not a continuous stream → `raw_camera_stream_dump.py --seconds 30`.
3. **RC-3 (LEVEL 1):** `codec=2` undocumented; h264-vs-hevc assumed →
   `test_h264_decode.py` on real extracted AUs.
4. **RC-4 (LEVEL 0, FIXED):** Hub state-loop freeze — explains the observed
   `camera.state=disconnected` contradiction (task §44 answered: yes, the state
   could be stale).
5. **RC-5 (LEVEL 0, FIXED):** Hub-side probe competition for the single session.

## 5. Evidence

* Real diagnostic DG-20260903-001 (reachable/TCP/206/65536B/chunks/VID0) — quoted in task doc §2.
* Code proofs: `app/diagnostics/probes.py` (cap), `app/camera/manager.py` (race, probe loop), `app/diagnostics/errors.py` (snapshot semantics).
* Vendor proofs: `Gear360App/README.md:21-31`, `MessageSender.kt:96-102`, `BTMessage.kt:668-671` (BT liveview arming); `MediaExtractor.java:114-132` (plain GET, 768 KiB buffers, no Range); `Extractor.kt:16-27,105-270,311-383` (container math); `gear360_modding/extracted/mod.sh`, `G360POWE_G360POW.sh`, `top.html` (services + capture command); `keyscan360.c` (key→script dispatcher, unrelated to shutter command).
* New instrumented evidence: every field run writes `diagnostics/raw/evidence/*.json`.

## 6. Why Previous Fixes Failed

1. **Parser patches were correct but addressed a layer that was already working.** The container/chunk layer was proven sane by the same diagnostic that showed the contradiction; no parser fix can change `state=disconnected`.
2. **The transport "fix" (range-only) was built on a contaminated experiment** (sequential variants on a single-session server — F4), so it locked the transport to one style without proving the alternative styles fail in isolation.
3. **The diagnostics could not see its own artifacts**: the probe capped at exactly 65 536 bytes and never tested continuity; the report then read "65536" as a camera property (E3).
4. **State semantics conflated layers** until `phase()` was introduced; but the state loop itself had a lifecycle race (F2) that could freeze whatever honest state existed.
5. **No session arbitration existed**: probes, health checks and live all knocked on the same single-session server independently (F3).

## 7. Code Changes (this investigation)

* `app/camera/session_broker.py` **NEW** — single-session broker: in-process ownership + cross-process heartbeat lock file (`diagnostics/raw/session.lock`) consumed by `tools/g360_common.py`.
* `app/camera/manager.py` — state-loop race fix; broker acquire/release/heartbeat (+ release on dead pipeline); health probe derives reachability from live bytes while a session exists (no more 7679 knocks); `layers()` (7-layer honest status §45) exposed in `/api/camera/status`.
* `app/api/diagnostics_api.py` — `POST /api/diagnostics/independent-test` (raw-socket minimal client, session-safe: reports live evidence instead of a second connection); `POST /api/diagnostics/captures/analyze` (offline structural analysis of a saved capture).
* `frontend/js/views/diagnostics.js` + `i18n.js` — buttons: **Independent Camera Test**, **Analyze Offline** (next to existing Capture).
* `tools/` **NEW** — 8 standalone investigation tools + shared `g360_common.py` + orchestrator `run_investigation.py` (mandatory phase order §53, session-lock aware, JSON evidence).
* Regression safety: full suite **73 passed / 1 skipped** after changes.

## 8. Camera Mod Changes

**None.** Vendor copies are study-only references (`vendor/gear360_modding/`,
`vendor/Gear360App/`); the mod is not modified (E9). If a future evidence chain
demands a mod change: `vendor/gear360_modding/patched/` + BUILD/INSTALL/CHANGELOG
docs + rollback = copy the original `extracted/` back to the SD card (task §37–§38).

## 9. Tests

* `pytest tests/` → **73 passed, 1 skipped** (post-change).
* New offline validation: `tools/parse_captured_stream.py` round-trips a
  structurally-real sample (identical VID0 layout to the real capture) and the
  decode tool correctly refuses synthetic bytes with full stderr (honest failure §28/§58).
* Field test plan = `tools/run_investigation.py` (PHASE 1→8, one command).

## 10. Real Camera Proof

**Not yet produced (task §55/§58).** The required log sequence (§56):

```text
[CAMERA] Reachable            ← health probe / PHASE 1
[CAMERA] TCP 7679 connected   ← PHASE 1
[STREAM] HTTP 206             ← transport on_response
[STREAM] Continuous body received   ← raw_camera_stream_dump VERDICT: CONTINUOUS
[STREAM] Gear360 container detected ← parser first bytes
[PARSER] VID0 valid           ← VID0 log line (fields above)
[PARSER] 00VD #1 complete     ← first 00VD log line
[H264] Valid NAL units        ← parse_captured_stream NAL scan on real AUs
[DECODER] FFmpeg started      ← FFMPEG: started pid=
[DECODER] Input bytes > 0     ← first encoded AU written
[DECODER] First REAL frame decoded  ← FRAME: first frame decoded
[FRAMEBUS] Frame published    ← bus.published > 0
[LIVE] Viewer received real frame   ← viewer_frames > 0
[SNAPSHOT] JPEG generated     ← /api/stream/snapshot 200
[CAMERA] Take Photo command sent    ← camera_control_probe --photo
[CAMERA] Photo created / [FTP] Photo downloaded / [GALLERY] Photo indexed
```

Until a field run prints this chain on the machine connected to the camera,
the correct status is: **Hub-side blockers fixed; camera-side continuity and
codec identity instrumented and awaiting decisive runs.**

## 11. Rollback Instructions

* Code: `git revert <this commit>` — all changes are in one commit; no data migrations.
* Session lock: delete `diagnostics/raw/session.lock` (auto-expires in 15 s anyway).
* Field artifacts: `diagnostics/raw/` is disposable except `evidence/*.json` + `baseline.json` (keep as Known-Good Baseline, task §40).
* Mod/firmware: nothing was modified (see §8).
* Transport behaviour rollback: revert `LIVE_REQUEST_STYLE` usage in `app/stream/parser.py` only after `http_variants_probe.py` proves another style superior in isolation.

---

### §29 comparison table — Android reference vs current Python vs observed bytes

| Field | Android (Extractor.kt / MediaExtractor.java) | Hub Python (app/stream/parser.py) | Observed real bytes (SM-R210) | Verdict |
|---|---|---|---|---|
| TTTS record | read size+type, `headerSize += 12` (Extractor.kt:133-141) | fixed 12B structure, size informational | size=0, type=3 present | ✓ match |
| VID0 size semantics | `headerSize += size + 4` (self-inclusive) | payload = size − 4 | size=96 → 92B payload, LIST next at +100 | ✓ match |
| VID0 codec @ | +16 (`videoCodecType`) | +16 | 2 | match; **interpretation: 1=HEVC (ref) — 2 undocumented** (RC-3) |
| VID0 scale @ | +32 | +32 | 120000 | ✓ |
| VID0 subTag @ | +36, must be `VD00` | +36, must be `VD00` | "VD00" | ✓ |
| VID0 fps @ | +44/+48 | +44/+48 | 1500/100 = 15 | ✓ |
| VID0 dims @ | +76/+80 | +76/+80 | 1280/640 | ✓ |
| LIST record | cost 8, then `movi` costs 4 | same | LIST@0xB4, movi@0xBC | ✓ |
| movi chunk header | tag4 + i32 size + i64 BE ts | same | ts deltas = 8000 ticks @ scale 120000 | ✓ |
| keyframe marker | 5th payload byte ∈ {64, 38} (peeked) | payload[4] ∈ {64, 38} | pending field AUs | ✓ rule (bytes pending) |
| payload size | `chunkSize − 5` | `payload[:-5]` native-annexb / strip 5+5 otherwise | pending field AUs | ✓ rule |
| PTS | `ts × 1e6 / videoTimeStampScale` | same scale math | — | ✓ |
| mid-stream TTTS | skip `headerSize − 4` (re-sync) | resync: jump to next `00VD` + anomaly log | not yet observed live | functional equivalent |
| HTTP request | plain GET HTTP/1.1, Keep-Alive, UA "Android Linux", NO Range, 768 KiB buffers, soTimeout ∞ | GET + `Range: bytes=0-` (locked), 64 KiB reads, 8 s read timeout | camera answered 206 + body to Range | **difference — RC-1 decides which style is truly required** |
| stream arming | Bluetooth `cmd-req` "liveview" (channel 204) BEFORE GET | none (HTTP only) | unknown | **difference — RC-2 decides continuity impact** |
| reconnect/retry | none (EOF = dead) | backoff + settle + parser reset | — | Hub stronger |

### §39 Compatibility Matrix / §40 Known-Good Baseline

Generated per machine by `tools/compatibility_matrix.py --photo` →
`diagnostics/raw/baseline.json` (ping, TCP 7679/8888/21/23, stream body proof,
mod web, real Take Photo). Previous known-good from DG-20260903-001: Ping=OK,
TCP=OK, HTTP 206=OK, bytes=OK(capped), chunks=OK; 8888/21/23/TakePhoto = untested.
