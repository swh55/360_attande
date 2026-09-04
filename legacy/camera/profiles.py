"""Camera profiles: SM-R210 today, extensible for future devices."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..core.paths import DIRS

PROFILES_FILE = DIRS["config"] / "camera_profiles.json"


@dataclass
class CameraProfile:
    id: str
    name: str
    host: str
    port: int
    stream_path: str
    fisheye_fov_deg: float = 195.0
    lens0_azimuth_deg: float = 0.0
    radius_factor: float = 1.0
    notes: str = ""


def load_profiles() -> list[CameraProfile]:
    try:
        data = json.loads(PROFILES_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {"profiles": []}
    out: list[CameraProfile] = []
    for p in data.get("profiles", []):
        d = p.get("defaults", {})
        o = p.get("optics", {})
        out.append(CameraProfile(
            id=p.get("id", "unknown"),
            name=p.get("name", "Unknown camera"),
            host=d.get("host", "192.168.43.1"),
            port=int(d.get("port", 7679)),
            stream_path=d.get("stream_path", "/livestream_high.avi"),
            fisheye_fov_deg=float(o.get("fisheye_fov_deg", 195.0)),
            lens0_azimuth_deg=float(o.get("lens0_azimuth_deg", 0.0)),
            radius_factor=float(o.get("radius_factor", 1.0)),
            notes=p.get("notes", ""),
        ))
    return out


def get_profile(profile_id: str) -> Optional[CameraProfile]:
    for p in load_profiles():
        if p.id == profile_id:
            return p
    return None
