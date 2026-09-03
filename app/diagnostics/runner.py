"""Full diagnostic runner: 19 ordered checks against the REAL camera when
available (no mocks). Each step is exception-isolated; one failure never
aborts the session and never disturbs the live pipeline."""
from __future__ import annotations

import threading
import time
import traceback

from ..core import settings as settings_svc
from ..core.logging_setup import get
from .analyzer import analyze_findings
from .capture import capture_path, latest_capture, load_slice
from .decoderprobe import decode_probe, ffmpeg_self_test
from .errors import classify, snapshot_error_payload
from .inspectors import (analyze_vid0, detect_container, first_bytes_report,
                         hexdump_rows, scan_signatures, validate_structure)
from .models import (STATUS_FAIL, STATUS_PASS, STATUS_UNTESTED, STATUS_WARN,
                     PIPELINE_STAGES, StepResult)
from .probes import http_probe, icmp_ping, tcp_ping
from .report import build_zip

LOG = get("app")
STEP_TOTAL = 19


class DiagnosticsRunner:
    """One runner per process (created via diagnostics.get_runner(hub))."""

    def __init__(self, hub) -> None:
        self.hub = hub
        self._lock = threading.Lock()
        self._progress: dict = {}

    # ---- public ------------------------------------------------------------
    def start(self) -> str:
        from . import get_registry
        registry = get_registry()
        if registry.running_id():
            return registry.running_id()
        sid = registry.new_id()
        registry.start(sid)
        threading.Thread(target=self._run, args=(sid,), name=f"diag-{sid}", daemon=True).start()
        return sid

    def progress(self) -> dict:
        with self._lock:
            return dict(self._progress)

    # ---- internals -----------------------------------------------------------
    def _run(self, sid: str) -> None:
        from . import get_registry
        registry = get_registry()
        t0 = time.time()
        steps: list[dict] = []
        ctx: dict = {"steps": steps}

        def add(step: StepResult) -> None:
            steps.append(step.to_dict())
            with self._lock:
                self._progress = {"session": sid, "done": len(steps), "total": STEP_TOTAL,
                                  "last": step.key, "status": step.status}

        def run(key: str, label: str, fn, requires: str = "") -> None:
            try:
                result = fn(StepResult(key=key, label=label))
                if result is not None:
                    add(result)
            except Exception as exc:  # never abort the session
                LOG.warning("diagnostic step %s failed: %s\n%s", key, exc, traceback.format_exc(limit=3))
                add(StepResult(key=key, label=label, status=STATUS_FAIL,
                               detail=str(exc), evidence={"trace": traceback.format_exc(limit=2)}))

        def skip(key: str, label: str, reason: str) -> None:
            add(StepResult(key=key, label=label, status=STATUS_UNTESTED,
                           detail=reason, skipped_reason=reason))

        hub = self.hub
        host = settings_svc.get("camera.host")
        port = int(settings_svc.get("camera.port", 7679))
        path = settings_svc.get("camera.stream_path", "/livestream_high.avi")
        live_parser = None
        p = hub.camera._pipeline
        if p is not None and p.parser is not None:
            live_parser = p.parser

        # source resolution: live parser head -> capture file
        source_name = "live"
        data = bytes(live_parser.head_bytes) if live_parser is not None else b""
        if len(data) < 64:
            cap = latest_capture()
            if cap:
                try:
                    data = load_slice(cap, 0, 8 * 1024 * 1024)
                    source_name = f"capture:{cap}"
                except Exception:
                    data = b""
        has_data = len(data) >= 16

        # 1+2 — reachability
        def s_camera_ping(sr: StepResult) -> StepResult:
            icmp = icmp_ping(host)
            tcp = tcp_ping(host, port)
            ctx["icmp"], ctx["tcp"] = icmp, tcp
            ok = tcp["ok"] or icmp["ok"]
            sr.status = STATUS_PASS if ok else STATUS_FAIL
            sr.detail = (f"TCP {'OK ' + str(tcp['latency_ms']) + 'ms' if tcp['ok'] else 'failed'}"
                         f" · ICMP {'OK' if icmp['ok'] else 'n/a'}")
            sr.evidence = {"tcp": tcp, "icmp": icmp}
            return sr
        run("camera_ping", "Camera ping", s_camera_ping)

        def s_tcp(sr: StepResult) -> StepResult:
            tcp = ctx.get("tcp") or tcp_ping(host, port)
            ctx["tcp"] = tcp
            sr.status = STATUS_PASS if tcp.get("ok") else STATUS_FAIL
            sr.detail = f"TCP {host}:{port} " + (f"connected in {tcp['latency_ms']}ms" if tcp.get("ok") else tcp.get("error", ""))
            sr.evidence = {"tcp": tcp}
            return sr
        run("tcp_connect", "TCP connectivity", s_tcp)

        # 3+4+5 — HTTP
        probe_res = {}

        def s_http(sr: StepResult) -> StepResult:
            res = http_probe(host, port, path, max_bytes=262_144)
            probe_res.update(res)
            ctx["http"] = res
            sr.status = STATUS_PASS if res.get("ok") else STATUS_FAIL
            sr.detail = (f"HTTP {res.get('status')} · {res.get('bytes_read')}B · "
                         f"{res.get('content_type') or '?'}") if not res.get("error") else res["error"]
            sr.evidence = {k: res.get(k) for k in ("url", "status", "content_type", "bytes_read", "error")}
            return sr
        run("http_connect", "HTTP connection", s_http)

        def s_headers(sr: StepResult) -> StepResult:
            res = ctx.get("http") or {}
            if not res:
                skip("http_headers", "HTTP headers", "HTTP probe did not run")
                return
            sr.status = STATUS_PASS if res.get("status") in (200, 206) else STATUS_FAIL
            sr.detail = f"status={res.get('status')} content-type={res.get('content_type')}"
            sr.evidence = {"headers": res.get("headers", {})}
            ctx["headers"] = res.get("headers", {})
            return sr
        run("http_headers", "HTTP headers", s_headers)

        def s_read(sr: StepResult) -> StepResult:
            if not ctx.get("http"):
                skip("stream_read", "Stream read", "HTTP probe did not run")
                return
            res = http_probe(host, port, path, max_bytes=2 * 1024 * 1024)
            ctx["http"] = res
            sr.status = STATUS_PASS if res.get("bytes_read", 0) > 0 else STATUS_FAIL
            rate = res.get("bytes_per_sec")
            sr.detail = (f"{res.get('bytes_read')} bytes in {res.get('elapsed_ms')}ms"
                         + (f" · {rate} B/s" if rate else ""))
            sr.evidence = {k: res.get(k) for k in ("bytes_read", "elapsed_ms", "bytes_per_sec", "error")}
            return sr
        run("stream_read", "Stream read (2MB)", s_read)

        # source-based analysis
        def s_first_bytes(sr: StepResult) -> StepResult:
            if not has_data:
                skip("first_bytes", "First bytes", f"no stream data (source={source_name})")
                return
            rep = first_bytes_report(data[:1024])
            sr.status = STATUS_PASS
            sr.detail = f"{len(data)} bytes available from {source_name}"
            sr.evidence = {"first_256": rep["first_256"][:16], "signatures": rep["signatures"]}
            return sr
        run("first_bytes", "First bytes", s_first_bytes)

        def s_container(sr: StepResult) -> StepResult:
            if not has_data:
                skip("container", "Container detection", f"no stream data (source={source_name})")
                return
            det = detect_container(data[:64 * 1024])
            ctx["container"] = det
            sr.status = STATUS_PASS if det["kind"] == "gear360_chunks" else STATUS_WARN
            sr.detail = f"container={det['kind']}"
            sr.evidence = det
            return sr
        run("container_detect", "Container detection", s_container)

        def s_scan(sr: StepResult) -> StepResult:
            if not has_data:
                skip("chunk_scan", "Chunk scanner", f"no stream data (source={source_name})")
                return
            scan = scan_signatures(data)
            ctx["scan"] = scan
            sr.status = STATUS_PASS if scan["total"] else STATUS_WARN
            sr.detail = f"{scan['total']} signature hits · counts={scan['counts']}"
            sr.evidence = {"counts": scan["counts"], "truncated": scan["truncated"]}
            return sr
        run("chunk_scan", "Chunk scanner", s_scan)

        def s_structure(sr: StepResult) -> StepResult:
            if not has_data:
                skip("structure", "Structural validation", f"no stream data (source={source_name})")
                return
            st = validate_structure(data)
            ctx["structure"] = st
            sr.status = STATUS_PASS if st["invalid_rows"] == 0 else STATUS_WARN
            sr.detail = f"{st['valid_rows']} valid boundaries · {st['invalid_rows']} invalid"
            sr.evidence = {"invalid_rows": st["invalid_rows"], "valid_rows": st["valid_rows"]}
            return sr
        run("structure", "Structural validation", s_structure)

        # VID0: prefer live parser's captured payload; else parse from data
        vid0_payload = b""
        if live_parser is not None and live_parser.vid0_raw:
            vid0_payload = bytes(live_parser.vid0_raw)
        elif has_data:
            i = data.find(b"VID0")
            if i >= 0 and len(data) >= i + 8 + 96:
                vid0_payload = data[i + 8: i + 8 + 96]

        def s_vid0(sr: StepResult) -> StepResult:
            if len(vid0_payload) < 84:
                skip("vid0", "VID0 metadata", "no VID0 payload available")
                return
            analysis = analyze_vid0(vid0_payload)
            ctx["vid0"] = analysis
            sr.status = STATUS_PASS if analysis["verdict"] == "VALID" else STATUS_FAIL
            sr.detail = analysis["summary"]
            sr.evidence = {"fields": analysis["fields"], "subtag_ok": analysis["subtag_ok"]}
            return sr
        run("vid0", "VID0 metadata", s_vid0)

        live_snap = live_parser.diagnostics_snapshot() if live_parser is not None else {}
        ctx["live"] = live_snap
        info = live_snap.get("info", {})

        def s_codec(sr: StepResult) -> StepResult:
            if not vid0_payload:
                skip("codec", "Codec analysis", "no VID0 payload")
                return
            codec_raw = info.get("codec")
            name = "hevc" if codec_raw == 1 else "h264"
            sr.status = STATUS_PASS if codec_raw in (0, 1) else STATUS_WARN
            sr.detail = f"codec_raw={codec_raw} → {name} (profile map: 1=HEVC, 0/other=h264)"
            sr.evidence = {"codec_raw": codec_raw, "normalized": name}
            ctx["codec"] = {"raw": codec_raw, "normalized": name}
            return sr
        run("codec", "Codec analysis", s_codec)

        def s_dims(sr: StepResult) -> StepResult:
            if not info:
                skip("dims", "Dimensions analysis", "no parser metadata")
                return
            w, h = info.get("width"), info.get("height")
            ok = 64 <= (w or 0) <= 8192 and 64 <= (h or 0) <= 8192
            sr.status = STATUS_PASS if ok else STATUS_FAIL
            sr.detail = f"{w}x{h}"
            sr.evidence = {"width": w, "height": h}
            return sr
        run("dims", "Dimensions analysis", s_dims)

        def s_fps(sr: StepResult) -> StepResult:
            if not info:
                skip("fps", "FPS analysis", "no parser metadata")
                return
            fps = info.get("frame_rate") or 0.0
            ok = 0.1 <= fps <= 240
            sr.status = STATUS_PASS if ok else STATUS_FAIL
            sr.detail = f"fps={fps:.3f} (scale={info.get('timestamp_scale')})"
            sr.evidence = {"fps": fps, "timestamp_scale": info.get("timestamp_scale")}
            return sr
        run("fps", "FPS analysis", s_fps)

        # 14+15+16 — payload framing + isolated decode + frame production
        aus = list(live_parser.au_ring) if live_parser is not None else []
        if not aus and has_data:
            from ..stream.parser import Gear360ChunkParser
            tmp = Gear360ChunkParser()
            try:
                tmp.feed(data)
                aus = list(tmp.au_ring)
                if not vid0_payload and tmp.vid0_raw:
                    ctx["vid0_from_capture"] = analyze_vid0(bytes(tmp.vid0_raw))
                if not info and tmp.info.width:
                    info = {"codec": tmp.info.codec, "width": tmp.info.width,
                            "height": tmp.info.height, "frame_rate": tmp.info.frame_rate,
                            "timestamp_scale": tmp.info.timestamp_scale,
                            "valid": tmp.info.valid}
                    ctx["live"] = live_snap = tmp.diagnostics_snapshot()
            except Exception:
                pass

        def s_payload(sr: StepResult) -> StepResult:
            head_hex = live_snap.get("first_00vd_head_hex") if live_snap else ""
            if not head_hex and aus:
                head_hex = aus[0][:24].hex(" ")
            if not head_hex:
                skip("payload", "First video payload", "no 00VD payload available")
                return
            first = bytes.fromhex(head_hex.replace(" ", "")) if head_hex else b""
            framing = "native_annexb" if first[:4] in (b"\x00\x00\x00\x01",) or first[:3] == b"\x00\x00\x01" else "prefix_stripped"
            sr.status = STATUS_PASS if framing == "native_annexb" else STATUS_WARN
            sr.detail = f"payload[0:8]={head_hex[:23]} → framing={framing}"
            sr.evidence = {"head_hex": head_hex, "framing": framing,
                           "au_ring_size": len(aus)}
            return sr
        run("payload", "First video payload", s_payload)

        def s_decoder_probe(sr: StepResult) -> StepResult:
            ff = ffmpeg_self_test()
            ctx["ffmpeg"] = ff
            if not ff.get("found"):
                sr.status = STATUS_FAIL
                sr.detail = f"FFmpeg not available: {ff.get('error')}"
                sr.evidence = ff
                return sr
            if not aus:
                skip("decoder_probe", "Decoder (isolated)", "no access units to feed (connect camera or capture a sample)")
                return
            codec = "hevc" if (info.get("codec") == 1) else "h264"
            w = int(info.get("width") or 0)
            h = int(info.get("height") or 0)
            if not (64 <= w <= 8192 and 64 <= h <= 8192):
                skip("decoder_probe", "Decoder (isolated)", f"metadata unusable ({w}x{h}) — VID0/layout issue upstream")
                return
            res = decode_probe(aus[-12:], codec, w, h, ff.get("path", "ffmpeg"))
            ctx["decoder_probe"] = res
            sr.status = STATUS_PASS if res.get("ok") else STATUS_FAIL
            sr.detail = (f"{res.get('frames')} frames from {res.get('fed')} AUs"
                         if res.get("ok") else f"0 frames — {res.get('error') or 'no output'}")
            sr.evidence = {k: res.get(k) for k in ("fed", "frames", "codec", "width", "height", "error")}
            return sr
        run("decoder_probe", "Decoder (isolated)", s_decoder_probe)

        def s_frames(sr: StepResult) -> StepResult:
            decoded = p.decoded_frames if p else 0
            probe = ctx.get("decoder_probe") or {}
            produced = decoded or probe.get("frames") or 0
            if produced:
                sr.status = STATUS_PASS
                sr.detail = f"{produced} decoded frames available"
                sr.evidence = {"decoded_frames": decoded, "probe_frames": probe.get("frames")}
            else:
                sr.status = STATUS_FAIL
                sr.detail = "Frame pipeline produced zero frames"
                sr.evidence = {"decoded_frames": decoded, "decoder_block_reason": getattr(p, "decoder_block_reason", None)}
            ctx["frame"] = {"produced": produced, "decoded_frames": decoded}
            return sr
        run("frames", "Frame production", s_frames)

        def s_jpeg(sr: StepResult) -> StepResult:
            img = None
            latest = hub.bus.latest()
            if latest is not None:
                img = latest.image
            if img is None:
                skip("jpeg", "JPEG encode", "no decoded frame to encode")
                return
            import cv2
            ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
            sr.status = STATUS_PASS if ok else STATUS_FAIL
            sr.detail = f"JPEG {len(buf)}B, {img.shape[1]}x{img.shape[0]}" if ok else "encode failed"
            sr.evidence = {"bytes": int(len(buf)) if ok else 0,
                           "width": int(img.shape[1]), "height": int(img.shape[0]),
                           "pixel_format": "BGR24"}
            return sr
        run("jpeg", "JPEG encode", s_jpeg)

        def s_snapshot(sr: StepResult) -> StepResult:
            latest = hub.bus.latest()
            if latest is None:
                payload = snapshot_error_payload(hub)
                ctx["snapshot"] = payload
                sr.status = STATUS_FAIL
                sr.detail = f"{payload.get('code')} — {payload.get('message')}"
                sr.evidence = payload
                return sr
            from ..media.projections import Calibration
            cal = Calibration.from_settings({k: settings_svc.get(k) for k in (
                "cal.fisheye_fov_deg", "cal.radius_factor", "cal.center_offset_x",
                "cal.lens0_azimuth_deg", "cal.swap_lenses", "cal.roll_deg")})
            try:
                out = hub.snapshots.capture(cal=cal, projection="equirect", note="diagnostic")
                ctx["snapshot"] = {"code": "OK", "filename": out.get("filename")}
                sr.status = STATUS_PASS
                sr.detail = f"snapshot OK: {out.get('filename')}"
                sr.evidence = {"filename": out.get("filename"), "projection": "equirect"}
            except Exception as exc:
                payload = snapshot_error_payload(hub, exc)
                ctx["snapshot"] = payload
                sr.status = STATUS_FAIL
                sr.detail = f"{payload.get('code')} — {payload.get('message')}"
                sr.evidence = payload
            return sr
        run("snapshot", "Snapshot pipeline", s_snapshot)

        def s_viewer(sr: StepResult) -> StepResult:
            latest = hub.bus.latest()
            age = (time.time() - latest.wall_time) if latest is not None else None
            fps = p.fps if p else 0.0
            ok = latest is not None and age is not None and age < 5
            sr.status = STATUS_PASS if ok else STATUS_FAIL
            sr.detail = (f"frame age {age:.1f}s · {fps} fps · ws_clients={hub.ws.count}"
                         if ok else "viewer input (fresh frames) unavailable")
            sr.evidence = {"frame_age": age, "fps": fps, "ws_clients": hub.ws.count}
            return sr
        run("viewer", "Viewer pipeline", s_viewer)

        # aggregate + findings + report
        findings = analyze_findings(ctx)
        finished = time.time()
        stage_status = {k: STATUS_UNTESTED for k, _ in PIPELINE_STAGES}
        mapping = {
            "camera": ("camera_ping", "tcp_connect"), "network": ("tcp_connect", "http_connect"),
            "http": ("http_connect", "http_headers", "stream_read"),
            "container": ("container_detect",), "chunks": ("chunk_scan", "structure"),
            "frames": ("payload", "frames"), "decoder": ("decoder_probe",),
            "projection": ("jpeg",), "viewer": ("viewer",), "snapshot": ("snapshot",),
        }
        order = {"PASS": 0, "WARNING": 1, "NOT_TESTED": 2, "FAIL": 3}
        for stage, keys in mapping.items():
            statuses = [next((s["status"] for s in steps if s["key"] == k), STATUS_UNTESTED) for k in keys]
            worst = max(statuses, key=lambda st: order.get(st, 2))
            stage_status[stage] = worst
        summary = {
            "passed": sum(1 for s in steps if s["status"] == "PASS"),
            "failed": sum(1 for s in steps if s["status"] == "FAIL"),
            "warned": sum(1 for s in steps if s["status"] == "WARNING"),
            "untested": sum(1 for s in steps if s["status"] == "NOT_TESTED"),
            "source": source_name,
            "stage_status": stage_status,
        }
        session = {
            "id": sid,
            "started_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t0)),
            "finished_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(finished)),
            "duration_ms": round((finished - t0) * 1000),
            "steps": steps,
            "findings": findings,
            "summary": summary,
            "live": live_snap,
            "http": ctx.get("http"),
            "tcp": ctx.get("tcp"),
            "icmp": ctx.get("icmp"),
            "scan": ctx.get("scan"),
            "structure": ctx.get("structure"),
            "vid0": ctx.get("vid0") or ctx.get("vid0_from_capture"),
            "codec": ctx.get("codec"),
            "decoder_probe": ctx.get("decoder_probe"),
            "frame": ctx.get("frame"),
            "snapshot": ctx.get("snapshot"),
            "headers": ctx.get("headers"),
        }
        registry.finish(sid, session)
        with self._lock:
            self._progress = {"session": sid, "done": STEP_TOTAL, "total": STEP_TOTAL,
                              "last": "done", "status": "DONE"}
        LOG.info("Diagnostics session %s finished in %.1fs (F%d W%d U%d P%s)",
                 sid, finished - t0, summary["failed"], summary["warned"],
                 summary["untested"], summary["passed"])

    def export_zip(self, session: dict):
        return build_zip(self.hub, session)
