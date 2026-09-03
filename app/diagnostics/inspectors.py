"""Pure byte-level inspectors: multi-interpretation field analysis, endianness
plausibility, container detection, independent chunk scanner, structural
validation and hexdump. No I/O — everything operates on bytes."""
from __future__ import annotations

import bisect
import struct
from typing import Optional

KNOWN_TAGS = (b"TTTS", b"VID0", b"VD00", b"AUD0", b"AU00", b"VR00",
              b"LIST", b"movi", b"00VD", b"00AU", b"00VR")
TOP_TAGS = (b"TTTS", b"VID0", b"AUD0", b"VR00", b"LIST", b"movi")
MEDIA_TAGS = (b"00VD", b"00AU", b"00VR", b"TTTS")

# fields whose plausibility the endianness analyzer scores
PLAUSIBLE_RULES = {
    "width": lambda v: isinstance(v, int) and 64 <= v <= 8192 and v % 2 == 0,
    "height": lambda v: isinstance(v, int) and 64 <= v <= 8192 and v % 2 == 0,
    "fps": lambda v: isinstance(v, (int, float)) and 0.1 <= float(v) <= 240.0,
    "fps_num": lambda v: isinstance(v, int) and 1 <= v <= 10_000_000,
    "fps_den": lambda v: isinstance(v, int) and 1 <= v <= 10_000_000,
    "scale": lambda v: isinstance(v, int) and 1 <= v <= 10_000_000,
    "codec": lambda v: isinstance(v, int) and v in (0, 1, 2),
    "subtag": None,   # handled specially
    "generic": lambda v: isinstance(v, int) and 0 < v < 2 ** 31,
}


def ascii_of(b: bytes) -> str:
    return "".join(chr(c) if 32 <= c < 127 else "·" for c in b)


def _looks_fourcc(b: bytes) -> bool:
    return len(b) >= 4 and all(32 <= c < 127 for c in b[:4])


def _is_known_tag(b: bytes) -> bool:
    return b[:4] in KNOWN_TAGS


def interpret4(b: bytes) -> dict:
    """All plausible interpretations of a 4-byte field (endianness analyzer)."""
    out: dict = {"hex": b.hex(" "), "ascii": ascii_of(b), "fourcc": _is_known_tag(b)}
    if len(b) >= 2:
        out["u16be"] = int.from_bytes(b[0:2], "big")
        out["u16le"] = int.from_bytes(b[0:2], "little")
    if len(b) >= 4:
        out["u32be"] = int.from_bytes(b[0:4], "big", signed=False)
        out["u32le"] = int.from_bytes(b[0:4], "little", signed=False)
        out["i32be"] = int.from_bytes(b[0:4], "big", signed=True)
        out["i32le"] = int.from_bytes(b[0:4], "little", signed=True)
        try:
            out["f32be"] = struct.unpack(">f", b[:4])[0]
            out["f32le"] = struct.unpack("<f", b[:4])[0]
        except (struct.error, ValueError):
            pass
    return out


def _score(field_name: str, interp: dict) -> list[dict]:
    """Score each interpretation of a raw 4-byte field for plausibility."""
    rows = []
    candidates = [
        ("u32be", interp.get("u32be")), ("u32le", interp.get("u32le")),
        ("i32be", interp.get("i32be")), ("i32le", interp.get("i32le")),
        ("f32be", interp.get("f32be")), ("f32le", interp.get("f32le")),
    ]
    rule = PLAUSIBLE_RULES.get(field_name, PLAUSIBLE_RULES["generic"])
    for name, value in candidates:
        if value is None:
            continue
        if name.startswith("f"):
            ok = isinstance(rule, bool) or False
            try:
                ok = (not isinstance(rule, bool)) and rule(float(value)) and abs(value) < 1e6 \
                    and value == value and abs(value) > 1e-12
            except Exception:
                ok = False
        else:
            try:
                ok = (not isinstance(rule, bool)) and rule(int(value))
            except Exception:
                ok = False
        note = ""
        if name == "u32be" and interp.get("fourcc"):
            note = f"reads as ASCII FourCC '{interp['ascii'][:4]}' — misaligned field!"
        rows.append({"interp": name, "value": value, "plausible": bool(ok), "note": note})
    return rows


# VID0 field map (verified against TecCheck/Gear360App Extractor.kt)
VID0_FIELDS = [
    ("codec", 16, "codec"),
    ("scale", 32, "scale"),
    ("subtag", 36, "subtag"),
    ("fps_num", 44, "fps_num"),
    ("fps_den", 48, "fps_den"),
    ("width", 76, "width"),
    ("height", 80, "height"),
]
# slots the ORIGINAL (buggy) Hub parser used — kept for forensic comparison
VID0_LEGACY_SLOTS = [
    ("codec_legacy", 20), ("scale_legacy", 36), ("fps_num_legacy", 52),
    ("fps_den_legacy", 56), ("width_legacy", 88), ("height_legacy", 92),
]


def analyze_vid0(payload: bytes) -> dict:
    """Parser Debugger: per-field raw bytes + all interpretations + verdicts."""
    fields = []
    for name, off, rule_key in VID0_FIELDS:
        if len(payload) < off + 4:
            continue
        raw = payload[off:off + 4]
        interp = interpret4(raw)
        if rule_key == "subtag":
            ok = raw == b"VD00"
            verdict = "OK" if ok else "IMPLAUSIBLE"
            note = "" if ok else "expected ASCII 'VD00' (0x56443030) — layout marker"
            rows = _score("generic", interp)
        else:
            rows = _score(rule_key, interp)
            ok = any(r["plausible"] for r in rows if r["interp"] == "u32be")
            verdict = "OK" if ok else "IMPLAUSIBLE"
            note = ""
            if interp.get("fourcc") and rule_key != "codec":
                note = f"u32be reads as ASCII '{interp['ascii'][:4]}' — this slot likely misaligned"
        fields.append({
            "name": name, "offset": off, "raw_hex": interp["hex"],
            "ascii": interp["ascii"], "value_be": interp.get("u32be"),
            "interpretations": rows, "verdict": verdict, "note": note,
            "legacy": False,
        })
    for name, off in VID0_LEGACY_SLOTS:
        if len(payload) < off + 4:
            continue
        raw = payload[off:off + 4]
        interp = interpret4(raw)
        note = "slot used by the original buggy parser (pre-fix offsets)"
        if interp.get("fourcc"):
            note += f" — contains FourCC '{interp['ascii'][:4]}' (explains garbage values)"
        fields.append({
            "name": name, "offset": off, "raw_hex": interp["hex"],
            "ascii": interp["ascii"], "value_be": interp.get("u32be"),
            "interpretations": _score("generic", interp),
            "verdict": "FOURCC" if interp.get("fourcc") else "INFO",
            "note": note, "legacy": True,
        })
    subtag_ok = len(payload) >= 40 and payload[36:40] == b"VD00"
    return {
        "length": len(payload),
        "subtag_ok": subtag_ok,
        "fields": fields,
        "verdict": "VALID" if subtag_ok else "SUSPECT",
        "summary": (
            "VD00 layout marker verified — field map matches Extractor.kt"
            if subtag_ok else
            "VD00 marker missing at +36 — the VID0 field layout does NOT match "
            "the reference extractor; do not trust the parsed metadata"
        ),
    }


def detect_container(data: bytes) -> dict:
    sigs = []
    if data[:4] == b"RIFF":
        sigs.append({"sig": "RIFF", "offset": 0})
        if data[8:12] == b"AVI ":
            sigs.append({"sig": "AVI ", "offset": 8})
    if data[:4] == b"TTTS":
        sigs.append({"sig": "TTTS (Gear360 chunk stream)", "offset": 0})
    for tag in (b"VID0", b"LIST", b"movi", b"00VD"):
        i = data.find(tag)
        if i >= 0:
            sigs.append({"sig": tag.decode(), "offset": i})
    kind = "gear360_chunks" if any(s["sig"].startswith(("TTTS", "VID0")) for s in sigs) else \
        ("avi_riff" if data[:4] == b"RIFF" else "unknown")
    return {"kind": kind, "signatures": sigs[:12], "size": len(data)}


def scan_signatures(data: bytes, cap: int = 2000) -> dict:
    """Independent chunk scanner — finds ALL signature occurrences without
    relying on the live parser, to answer: does the parser miss chunks, or
    does it find them but misinterpret fields?

    `aligned` marks occurrences that sit on the container's STRUCTURAL grid:
    a boundary the structure walk lands on, or a 4-byte-aligned field position
    inside a walked structure. (Absolute `offset % 4 == 0` is meaningless here:
    the header zone is word-aligned, but real media-chunk runs drift off the
    absolute word grid because chunk payloads are unpadded — Extractor.kt reads
    chunks back-to-back with no pad bytes.)"""
    found = []
    for tag in KNOWN_TAGS:
        start = 0
        count = 0
        while count < cap:
            i = data.find(tag, start)
            if i < 0:
                break
            found.append({
                "tag": tag.decode(), "offset": i,
                "aligned": i % 4 == 0,
            })
            start = i + 1
            count += 1
    found.sort(key=lambda r: r["offset"])
    # structural grid: walk the container once, keep structure-start offsets
    starts = _structure_anchors(data)
    for r in found:
        i = r["offset"]
        if starts:
            j = bisect.bisect_right(starts, i) - 1
            if j >= 0:
                r["aligned"] = (i - starts[j]) % 4 == 0
                continue
    summary: dict = {}
    for r in found:
        summary[r["tag"]] = summary.get(r["tag"], 0) + 1
    return {"total": len(found), "counts": summary, "occurrences": found[:400],
            "truncated": len(found) > 400}


def _structure_anchors(data: bytes, cap: int = 5000) -> list[int]:
    """Walk the container (header zone + movi media chunks) and return the
    sorted offsets where known structures start. Mirrors the layout rules of
    validate_structure()/the live parser: TOP tags consume 8+size bytes (TTTS
    size counts its type field), media chunks consume 16+size bytes."""
    starts: list[int] = []
    pos = 0
    n = len(data)
    while pos < n and len(starts) < cap:
        tag = data[pos:pos + 4]
        if tag == b"movi":
            starts.append(pos)
            pos += 4
            continue
        if tag in TOP_TAGS:
            if n < pos + 8:
                break
            size = int.from_bytes(data[pos + 4:pos + 8], "big")
            end = pos + 8 + size
        elif tag in MEDIA_TAGS:
            if n < pos + 16:
                break
            size = int.from_bytes(data[pos + 4:pos + 8], "big")
            end = pos + 16 + size
        else:
            break
        starts.append(pos)
        if end >= n:
            break
        pos = end if data[end:end + 4] in TOP_TAGS or data[end:end + 4] in MEDIA_TAGS else n
    return starts


def validate_structure(data: bytes, cap: int = 500) -> dict:
    """Walk the buffer structure-by-structure and verify chunk boundaries:
    next_offset == current_offset + header + payload must land on a known tag.
    Header-zone tags consume 8+size bytes (TTTS size counts its type field);
    movi-zone media chunks (00VD/00AU/00VR) consume 16+size bytes
    (tag + size + timestamp + payload). A structure that runs to the end of
    the buffer counts as valid — bounded windows/live tails truncate it."""
    rows = []
    pos = 0
    n = len(data)
    while pos < n and len(rows) < cap:
        tag = data[pos:pos + 4]
        row = {"tag": tag.decode("ascii", "replace"), "offset": pos,
               "declared_size": None, "available": None, "next_offset": None,
               "boundary_ok": None}
        if tag in TOP_TAGS:
            if tag == b"movi":
                row["next_offset"] = pos + 4
                row["boundary_ok"] = True
                rows.append(row)
                pos += 4
                continue
            size = int.from_bytes(data[pos + 4:pos + 8], "big") if n >= pos + 8 else None
            header = 8
            if tag == b"TTTS":
                header = 12
                declared_next = (pos + 8 + size) if size is not None else None
            else:
                declared_next = (pos + 8 + size) if size is not None else None
            row["declared_size"] = size
            row["available"] = max(0, n - pos - 8)
            row["next_offset"] = declared_next
            if declared_next is None:
                row["boundary_ok"] = None
                rows.append(row)
                break
            if declared_next >= n:
                if size is not None and 0 < size <= 16 * 1024 * 1024:
                    row["boundary_ok"] = True
                    row["note"] = "end of buffer"
                else:
                    row["boundary_ok"] = False
                    row["note"] = f"implausible declared size {size}"
                rows.append(row)
                break
            nxt = data[declared_next:declared_next + 4]
            if nxt in TOP_TAGS or nxt in MEDIA_TAGS or nxt == b"VD00":
                row["boundary_ok"] = True
                rows.append(row)
                pos = declared_next
                continue
            # declared size may be self-inclusive (+4) on some firmwares
            nxt2 = data[declared_next - 4:declared_next]
            if nxt2 in TOP_TAGS or nxt2 in MEDIA_TAGS:
                row["boundary_ok"] = False
                row["note"] = "boundary lands 4 bytes late — size field looks self-inclusive"
                rows.append(row)
                pos = declared_next - 4
                continue
            row["boundary_ok"] = False
            rows.append(row)
            # resync: next known tag
            best = -1
            for t in TOP_TAGS + MEDIA_TAGS:
                i = data.find(t, pos + 4)
                if i >= 0 and (best < 0 or i < best):
                    best = i
            pos = best if best >= 0 else n
        elif tag in MEDIA_TAGS:
            # media chunk: tag(4) + size(4) + timestamp(8) + payload(size)
            if n < pos + 16:
                row["note"] = "truncated media chunk header"
                rows.append(row)
                break
            size = int.from_bytes(data[pos + 4:pos + 8], "big")
            declared_next = pos + 16 + size
            row["declared_size"] = size
            row["available"] = max(0, n - pos - 16)
            row["next_offset"] = declared_next
            if declared_next >= n:
                if 0 < size <= 16 * 1024 * 1024:
                    row["boundary_ok"] = True
                    row["note"] = "end of buffer"
                else:
                    row["boundary_ok"] = False
                    row["note"] = f"implausible declared size {size}"
                rows.append(row)
                break
            nxt = data[declared_next:declared_next + 4]
            if nxt in TOP_TAGS or nxt in MEDIA_TAGS:
                row["boundary_ok"] = True
                rows.append(row)
                pos = declared_next
                continue
            row["boundary_ok"] = False
            rows.append(row)
            best = -1
            for t in TOP_TAGS + MEDIA_TAGS:
                i = data.find(t, pos + 4)
                if i >= 0 and (best < 0 or i < best):
                    best = i
            pos = best if best >= 0 else n
        else:
            best = -1
            for t in TOP_TAGS + MEDIA_TAGS:
                i = data.find(t, pos + 1)
                if i >= 0 and (best < 0 or i < best):
                    best = i
            row["boundary_ok"] = False
            row["note"] = "unknown bytes (no valid tag here)"
            rows.append(row)
            pos = best if best >= 0 else n
    invalid = [r for r in rows if r["boundary_ok"] is False]
    return {
        "rows": rows,
        "valid_rows": sum(1 for r in rows if r["boundary_ok"] is True),
        "invalid_rows": len(invalid),
        "truncated": len(rows) >= cap,
    }


def hexdump_rows(data: bytes, offset: int = 0, length: int = 256, width: int = 16) -> list[dict]:
    out = []
    end = min(len(data), max(0, offset) + max(0, length))
    pos = max(0, offset)
    while pos < end:
        chunk = data[pos:pos + width]
        out.append({
            "offset": pos,
            "hex": " ".join(f"{c:02x}" for c in chunk),
            "ascii": ascii_of(chunk),
        })
        pos += width
    return out


def first_bytes_report(data: bytes) -> dict:
    return {
        "size": len(data),
        "first_256": hexdump_rows(data, 0, 256),
        "first_1024": hexdump_rows(data, 0, 1024),
        "signatures": detect_container(data),
    }
