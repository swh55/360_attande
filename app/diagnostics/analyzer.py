"""Automatic root-cause analysis: evidence-based rules with explicit confidence.
The analyzer never claims certainty without evidence (HIGH/MEDIUM/LOW)."""
from __future__ import annotations

from .models import Finding


def analyze_findings(ctx: dict) -> list[Finding]:
    """ctx keys (all optional): tcp, icmp, http, container, scan, structure,
    vid0, payload_head, decoder_probe, snapshot, live (parser diagnostics
    snapshot), steps (list of step dicts)."""
    findings: list[Finding] = []

    def step(key: str) -> dict:
        for s in ctx.get("steps", []) or []:
            if s.get("key") == key:
                return s
        return {}

    tcp = ctx.get("tcp") or {}
    http = ctx.get("http") or {}
    vid0 = ctx.get("vid0") or {}
    scan = ctx.get("scan") or {}
    structure = ctx.get("structure") or {}
    live = ctx.get("live") or {}
    probe = ctx.get("decoder_probe") or {}
    info = (live.get("info") or {}) if isinstance(live, dict) else {}

    # R1 — camera unreachable
    if tcp and not tcp.get("ok"):
        findings.append(Finding(
            id="R1", confidence="HIGH",
            title="Camera is unreachable over the network",
            evidence=[f"TCP {tcp.get('host')}:{tcp.get('port')} failed: {tcp.get('error')}"],
            suggested_action="Check the camera Wi-Fi/hotspot, IP address and port in Settings; then re-run diagnostics.",
            error_code="CAMERA_UNREACHABLE",
        ).to_dict())

    # R2 — HTTP OK but zero bytes
    if http.get("status") in (200, 206) and http.get("bytes_read", 0) == 0:
        findings.append(Finding(
            id="R2", confidence="HIGH",
            title="HTTP connected but the camera sent zero stream bytes",
            evidence=[f"HTTP {http.get('status')}", f"Content-Type={http.get('content_type')}",
                      "bytes_read=0"],
            suggested_action="Another client may hold the live stream, or the camera dropped it right after headers. Retry, then capture a sample.",
            error_code="HTTP_STREAM_ERROR",
        ).to_dict())

    # R3 — VID0 layout suspect (live parser metadata invalid)
    if vid0 and vid0.get("verdict") == "SUSPECT":
        fourcc = [f for f in vid0.get("fields", []) if f.get("verdict") == "FOURCC"]
        ev = [f"subTag@+36 = {next((f['ascii'] for f in vid0['fields'] if f['name'] == 'subtag'), '?')!r}"]
        ev += [f"field {f['name']}@+{f['offset']} contains FourCC '{f['ascii'][:4]}'" for f in fourcc[:4]]
        findings.append(Finding(
            id="R3", confidence="HIGH" if fourcc else "MEDIUM",
            title="VID0 metadata layout does not match the reference extractor",
            evidence=ev,
            suggested_action="Compare VID0 field offsets against TecCheck Extractor.kt (Parser Details table). The Hub now uses the verified offsets; if this persists, capture a stream sample.",
            error_code="VID0_PARSE_ERROR",
        ).to_dict())

    # R4 — implausible metadata values that still parsed
    if info and (info.get("valid") is False) and vid0 and vid0.get("verdict") == "VALID":
        findings.append(Finding(
            id="R4", confidence="MEDIUM",
            title="VID0 layout marker valid but some metadata values are implausible",
            evidence=[f"codec={info.get('codec')} dims={info.get('width')}x{info.get('height')} "
                      f"fps={info.get('frame_rate')} scale={info.get('timestamp_scale')}"],
            suggested_action="Check the endianness analyzer table for this capture; values may use a non-standard unit.",
            error_code="VID0_PARSE_ERROR",
        ).to_dict())

    # R5 — no video chunks at all
    counts = (scan or {}).get("counts", {})
    if scan and counts and counts.get("VID0", 0) > 0 and counts.get("00VD", 0) == 0:
        findings.append(Finding(
            id="R5", confidence="MEDIUM",
            title="Stream header present but no 00VD video chunks in the inspected window",
            evidence=[f"signature counts: {counts}"],
            suggested_action="Capture a larger sample (e.g. 8 MB) — the video payload zone may start later.",
            error_code="CHUNK_PARSE_ERROR",
        ).to_dict())

    # R6 — structural boundaries broken
    if structure and structure.get("invalid_rows"):
        findings.append(Finding(
            id="R6", confidence="MEDIUM",
            title=f"{structure['invalid_rows']} chunk boundaries failed validation",
            evidence=["next_offset != offset + header + payload on those chunks",
                      "a self-inclusive size field convention shifts boundaries by 4 bytes"],
            suggested_action="Open Structural Validation — the parser resyncs, but check the boundary notes.",
            error_code="CHUNK_PARSE_ERROR",
        ).to_dict())

    # R7 — decoder probe produced no frames although AUs were fed
    if probe and probe.get("fed") and not probe.get("frames"):
        findings.append(Finding(
            id="R7", confidence="HIGH",
            title="Encoded access units were fed to FFmpeg but zero frames decoded",
            evidence=[f"fed={probe.get('fed')} AUs", f"codec={probe.get('codec')}",
                      f"stderr tail: {(probe.get('stderr') or ['(empty)'])[-2:]}"],
            suggested_action="Check the 00VD payload framing (first bytes hex) and the codec mapping; if payload[0:4] is not a start code the prefix convention differs.",
            error_code="FFMPEG_ERROR",
        ).to_dict())

    # R8 — snapshot unavailable with no frames (link upstream)
    snap = ctx.get("snapshot") or {}
    if snap and snap.get("code") and snap.get("code") != "OK":
        findings.append(Finding(
            id="R8", confidence="MEDIUM",
            title=f"Snapshot unavailable: {snap.get('code')}",
            evidence=[snap.get("message") or "", f"stage={snap.get('stage')}"],
            suggested_action=snap.get("suggested_action") or "Fix the failing upstream stage shown in the pipeline view.",
            error_code=snap.get("code") or "SNAPSHOT_ERROR",
        ).to_dict())

    # R9 — resync storm (stream format drift)
    if live and live.get("resyncs", 0) > 3 and live.get("chunks_seen", 0) and \
            live["resyncs"] > max(3, live["chunks_seen"] // 10):
        findings.append(Finding(
            id="R9", confidence="MEDIUM",
            title="Parser is resyncing frequently — stream format drift",
            evidence=[f"resyncs={live.get('resyncs')} chunks={live.get('chunks_seen')} "
                      f"boundary_errors={live.get('boundary_errors')}"],
            suggested_action="Capture a sample and run structural validation; the size-field convention may differ on this firmware.",
            error_code="CHUNK_PARSE_ERROR",
        ).to_dict())

    # R10 — FFmpeg missing entirely
    dec_step = step("decoder_probe")
    if dec_step and "FFmpeg" in (dec_step.get("detail") or "") and dec_step.get("status") == "FAIL":
        findings.append(Finding(
            id="R10", confidence="HIGH",
            title="FFmpeg is unavailable",
            evidence=[dec_step.get("detail") or ""],
            suggested_action="Put ffmpeg.exe in runtime/ffmpeg/, install it in PATH, or set the path in Settings.",
            error_code="FFMPEG_ERROR",
        ).to_dict())

    if not findings:
        findings.append(Finding(
            id="R0", confidence="LOW",
            title="No single root cause identified — pipeline looks healthy at the inspected layers",
            evidence=["All executed checks passed or had insufficient evidence."],
            suggested_action="Capture a stream sample while the problem is live and re-run diagnostics.",
            error_code="",
        ).to_dict())
    return findings
