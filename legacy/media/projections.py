"""360 projection math (dual-fisheye -> equirectangular -> perspective).

Used server-side for: AI perspective views, snapshot projections, thumbnails.
The web viewer performs the same math in a GLSL shader for real-time display.

Conventions (must stay identical to frontend/js/gl360.js):
  World: lon around +Y (radians, 0 = lens0 heading), lat elevation (+up).
  Lens k has azimuth A_k; fisheye is assumed equidistant with FOV `fdeg`.
  Image pixel (px,py) from top-left; texture coords (u,v) = (px/w, py/h).

  For a world direction d, in lens-local frame (axis = A_k):
    x_f = cos(lat) * cos(lon - A)     (along optical axis)
    y_f = sin(lat)                    (world up)
    z_f = cos(lat) * sin(lon - A)     (lens-image right)
  theta = acos(x_f) in [0, pi]; rho = theta / (fov/2)  (equidistant)
  psi = atan2(-y_f, z_f)   (image space, y down)
  px = cx + rho * r * cos(psi); py = cy + rho * r * sin(psi)
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class Calibration:
    """Lens calibration; derivable from camera profile + user adjustments."""

    fov_deg: float = 195.0
    radius_factor: float = 1.0
    center_offset_x: float = 0.0
    lens0_azimuth_deg: float = 0.0
    swap_lenses: bool = False
    roll_deg: float = 0.0

    @classmethod
    def from_settings(cls, s: dict) -> "Calibration":
        return cls(
            fov_deg=float(s.get("cal.fisheye_fov_deg", 195.0)),
            radius_factor=float(s.get("cal.radius_factor", 1.0)),
            center_offset_x=float(s.get("cal.center_offset_x", 0.0)),
            lens0_azimuth_deg=float(s.get("cal.lens0_azimuth_deg", 0.0)),
            swap_lenses=bool(s.get("cal.swap_lenses", False)),
            roll_deg=float(s.get("cal.roll_deg", 0.0)),
        )


def lens_geometry(w: int, h: int, cal: Calibration) -> list[dict]:
    """Return per-lens geometry: center, radius (px), azimuth (radians)."""
    cx0 = w * 0.25 + cal.center_offset_x * w
    cx1 = w * 0.75 + cal.center_offset_x * w
    cy = h * 0.5
    r = h * 0.5 * cal.radius_factor
    az0 = math.radians(cal.lens0_azimuth_deg)
    az1 = az0 + math.pi
    lenses = [
        {"cx": cx0, "cy": cy, "r": r, "az": az0},
        {"cx": cx1, "cy": cy, "r": r, "az": az1},
    ]
    if cal.swap_lenses:
        lenses.reverse()
    return lenses


def fisheye_to_equirect_map(
    src_w: int, src_h: int, out_w: int, out_h: int, cal: Calibration
) -> tuple[np.ndarray, np.ndarray]:
    """Build cv2.remap maps producing an equirectangular frame from dual fisheye."""
    lenses = lens_geometry(src_w, src_h, cal)
    fov_half = math.radians(cal.fov_deg) / 2.0

    lon = (np.arange(out_w, dtype=np.float32) + 0.5) / out_w * 2.0 * math.pi - math.pi
    lat = (0.5 - (np.arange(out_h, dtype=np.float32) + 0.5) / out_h) * math.pi
    lon_g, lat_g = np.meshgrid(lon, lat)

    map_x = np.full((out_h, out_w), -1.0, dtype=np.float32)
    map_y = np.full((out_h, out_w), -1.0, dtype=np.float32)

    cos_lat = np.cos(lat_g)
    for lens in lenses:
        dlon = lon_g - lens["az"]
        x_f = cos_lat * np.cos(dlon)          # along optical axis
        y_f = np.sin(lat_g)                   # up
        z_f = cos_lat * np.sin(dlon)          # image right
        # this lens owns directions whose azimuth is within +/-90 deg of its own
        owner = np.cos(dlon) >= 0.0
        theta = np.arccos(np.clip(x_f, -1.0, 1.0))
        rho = theta / fov_half
        psi = np.arctan2(-y_f, z_f) + math.radians(cal.roll_deg)
        px = lens["cx"] + rho * lens["r"] * np.cos(psi)
        py = lens["cy"] + rho * lens["r"] * np.sin(psi)
        valid = owner & (rho <= 1.0)
        map_x[valid] = px[valid]
        map_y[valid] = py[valid]

    return map_x.astype(np.float32), map_y.astype(np.float32)


def equirect_to_perspective_map(
    out_w: int, out_h: int, yaw_deg: float, pitch_deg: float, fov_deg: float
) -> tuple[np.ndarray, np.ndarray]:
    """Maps producing a rectilinear perspective view from an equirect frame."""
    yaw = math.radians(yaw_deg)
    pitch = math.radians(pitch_deg)
    fov = math.radians(fov_deg)
    tan_f = math.tan(fov / 2.0)

    a = ((np.arange(out_w, dtype=np.float32) + 0.5) / out_w * 2.0 - 1.0) * tan_f * (out_w / out_h)
    b = (0.5 - (np.arange(out_h, dtype=np.float32) + 0.5) / out_h) * 2.0 * tan_f
    a_g, b_g = np.meshgrid(a, b)

    # camera basis: forward from yaw/pitch
    fx = math.cos(pitch) * math.sin(yaw)
    fy = math.sin(pitch)
    fz = -math.cos(pitch) * math.cos(yaw)
    # right = normalize(cross(forward, up)), up2 = cross(right, forward)
    rx, ry, rz = math.cos(yaw), 0.0, math.sin(yaw)
    ux = -math.sin(pitch) * math.sin(yaw)
    uy = math.cos(pitch)
    uz = math.sin(pitch) * math.cos(yaw)

    dx = fx + a_g * rx + b_g * ux
    dy = fy + a_g * ry + b_g * uy
    dz = fz + a_g * rz + b_g * uz

    lon = np.arctan2(dx, -dz)
    lat = np.arcsin(np.clip(dy / np.sqrt(dx * dx + dy * dy + dz * dz), -1.0, 1.0))

    # normalized [0,1] coords into the equirect frame; caller scales to pixels
    nx = (lon + math.pi) / (2.0 * math.pi)
    ny = (0.5 - lat / math.pi)
    return nx, ny


def project_to_perspective(
    equirect: np.ndarray, yaw_deg: float, pitch_deg: float, fov_deg: float,
    out_w: int, out_h: int, maps_cache: dict | None = None,
) -> np.ndarray:
    h, w = equirect.shape[:2]
    key = ("p", out_w, out_h, round(yaw_deg, 1), round(pitch_deg, 1), round(fov_deg, 1), w, h)
    if maps_cache is not None and key in maps_cache:
        nx, ny = maps_cache[key]
    else:
        nx, ny = equirect_to_perspective_map(out_w, out_h, yaw_deg, pitch_deg, fov_deg)
        nx = nx.astype(np.float32) * w
        ny = ny.astype(np.float32) * h
        if maps_cache is not None:
            if len(maps_cache) > 48:
                maps_cache.clear()
            maps_cache[key] = (nx, ny)
    return cv2.remap(equirect, nx, ny, cv2.INTER_LINEAR, borderMode=cv2.BORDER_WRAP)


class EquirectProjector:
    """Cached dual-fisheye -> equirect projector for a fixed source size."""

    def __init__(self, src_w: int, src_h: int, out_w: int, cal: Calibration):
        self.src_w = src_w
        self.src_h = src_h
        self.out_w = out_w
        self.out_h = out_w // 2
        self.cal = cal
        self._key = None
        self._mapx: np.ndarray | None = None
        self._mapy: np.ndarray | None = None

    def _ensure_maps(self) -> None:
        key = (self.src_w, self.src_h, self.out_w,
               round(self.cal.fov_deg, 1), round(self.cal.radius_factor, 3),
               round(self.cal.center_offset_x, 3), round(self.cal.lens0_azimuth_deg, 1),
               self.cal.swap_lenses, round(self.cal.roll_deg, 1))
        if key != self._key:
            self._mapx, self._mapy = fisheye_to_equirect_map(
                self.src_w, self.src_h, self.out_w, self.out_h, self.cal
            )
            self._key = key

    def project(self, frame: np.ndarray) -> np.ndarray:
        self._ensure_maps()
        assert self._mapx is not None and self._mapy is not None
        return cv2.remap(frame, self._mapx, self._mapy, cv2.INTER_LINEAR,
                         borderMode=cv2.BORDER_CONSTANT, borderValue=(16, 16, 16))


class PerspectiveViewport:
    """Cached equirect -> perspective view for AI / director / virtual cameras."""

    def __init__(self, width: int = 480, height: int = 270, fov: float = 90.0):
        self.width = width
        self.height = height
        self.fov = fov
        self._cache: dict = {}

    def view(self, equirect: np.ndarray, yaw_deg: float, pitch_deg: float = 0.0) -> np.ndarray:
        return project_to_perspective(
            equirect, yaw_deg, pitch_deg, self.fov, self.width, self.height, self._cache
        )
