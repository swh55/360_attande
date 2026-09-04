"""Diagnostics engine unit tests (offline — no camera needed).

Includes the regression guard for the 2026-09 incident: scale=1447309360
(ASCII "VD00" — the subTag slot) and height=1279873876 (ASCII "LIST" — the
next chunk tag) must never be accepted as plausible metadata again."""
from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("GEAR360HUB_DB", str(Path(__file__).parent / ".tmp.db"))

from app.diagnostics.analyzer import analyze_findings  # noqa: E402
from app.diagnostics.errors import classify  # noqa: E402
from app.diagnostics.inspectors import (  # noqa: E402
    analyze_vid0, detect_container, hexdump_rows, interpret4, scan_signatures,
    validate_structure,
)
from tests.fixtures import make_synthetic_stream  # noqa: E402


def _real_vid0_payload() -> bytes:
    payload = bytearray(96)
    struct.pack_into(">I", payload, 16, 1)        # codec = HEVC
    struct.pack_into(">I", payload, 32, 90000)    # scale
    payload[36:40] = b"VD00"                      # subTag
    struct.pack_into(">I", payload, 44, 30000)    # fps 29.97
    struct.pack_into(">I", payload, 48, 1001)
    struct.pack_into(">I", payload, 76, 3840)
    struct.pack_into(">I", payload, 80, 2160)
    return bytes(payload)


def test_interpret4_endianness():
    r = interpret4(struct.pack(">I", 90000))
    assert r["u32be"] == 90000
    assert r["u32le"] == int.from_bytes(struct.pack(">I", 90000), "little")
    assert isinstance(r["f32be"], float)
    r2 = interpret4(b"VD00")
    assert r2["fourcc"] is True
    assert r2["ascii"] == "VD00"


def test_analyze_vid0_valid():
    r = analyze_vid0(_real_vid0_payload())
    assert r["verdict"] == "VALID"
    assert r["subtag_ok"] is True
    fields = {f["name"]: f for f in r["fields"]}
    assert fields["width"]["value_be"] == 3840
    assert fields["height"]["value_be"] == 2160
    assert fields["scale"]["value_be"] == 90000
    assert fields["codec"]["value_be"] == 1
    # forensic legacy slots: the OLD buggy scale slot (offset 36) holds "VD00"
    legacy = fields["scale_legacy"]
    assert legacy["verdict"] == "FOURCC"
    assert "VD00" in legacy["note"]
    assert fields["height_legacy"]["verdict"] in ("INFO", "FOURCC")


def test_analyze_vid0_suspect_without_subtag():
    payload = bytearray(_real_vid0_payload())
    payload[36:40] = b"XXXX"
    r = analyze_vid0(bytes(payload))
    assert r["verdict"] == "SUSPECT"
    assert r["subtag_ok"] is False


def test_scanner_finds_all_signatures():
    stream = make_synthetic_stream([b"\x00" * 40] * 3)
    scan = scan_signatures(stream)
    c = scan["counts"]
    assert c.get("TTTS", 0) >= 1
    assert c.get("VID0", 0) == 1
    assert c.get("LIST", 0) >= 1
    assert c.get("movi", 0) == 1
    assert c.get("00VD", 0) == 3
    assert all(o["aligned"] for o in scan["occurrences"])


def test_structure_valid_on_wellformed_fixture():
    stream = make_synthetic_stream([b"\x00" * 40] * 2)
    st = validate_structure(stream)
    assert st["invalid_rows"] == 0
    assert st["valid_rows"] >= 5


def test_hexdump_rows():
    rows = hexdump_rows(b"RIFFxxxx" + b"\x00" * 8, 0, 16, 16)
    assert rows[0]["offset"] == 0
    assert rows[0]["ascii"].startswith("RIFF")
    assert rows[0]["hex"].startswith("52 49 46 46")


def test_detect_container():
    stream = make_synthetic_stream([b"\x00" * 40])
    assert detect_container(stream)["kind"] == "gear360_chunks"


def test_classifier_codes_and_retryable():
    e = classify("connection timed out", "network")
    assert e["code"] == "NETWORK_ERROR" and e["retryable"] is True
    e2 = classify("VID0 subTag mismatch", "parser")
    assert e2["code"] == "VID0_PARSE_ERROR"
    assert e2["suggested_action"]
    e3 = classify("camera refused", "camera")
    assert e3["code"] == "CAMERA_UNREACHABLE"


def test_rca_flags_suspect_layout():
    payload = bytearray(_real_vid0_payload())
    payload[36:40] = b"XXXX"
    vid0 = analyze_vid0(bytes(payload))
    findings = analyze_findings({"vid0": vid0, "steps": []})
    assert any(f["id"] == "R3" for f in findings)
    r3 = next(f for f in findings if f["id"] == "R3")
    assert r3["confidence"] in ("HIGH", "MEDIUM")
    assert r3["error_code"] == "VID0_PARSE_ERROR"


def test_regression_incident_garbage_values_rejected():
    """The exact garbage numbers from the user's real-camera log must be
    recognized as ASCII FourCCs (misaligned fields), never as valid values."""
    bad = interpret4(struct.pack(">I", 1447309360))
    assert bad["fourcc"] is True and bad["ascii"] == "VD00"
    bad2 = interpret4(struct.pack(">I", 1279873876))
    assert bad2["fourcc"] is True and bad2["ascii"] == "LIST"
    # the fixed parser slot reads the true scale, not the subTag
    r = analyze_vid0(_real_vid0_payload())
    scale_field = next(f for f in r["fields"] if f["name"] == "scale")
    assert scale_field["value_be"] == 90000
