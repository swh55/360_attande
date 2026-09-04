"""Projection math tests: dual-fisheye roundtrip + perspective mapping."""
from __future__ import annotations

import math
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("GEAR360HUB_DB", str(Path(__file__).parent / ".tmp.db"))

import numpy as np  # noqa: E402

from app.media.projections import (  # noqa: E402
    Calibration, EquirectProjector, fisheye_to_equirect_map,
    lens_geometry, project_to_perspective,
)


def _render_marker_pano(w=1024, h=512, lon_deg=0.0, lat_deg=0.0):
    """Equirect pano with a bright marker at a known lon/lat."""
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:] = (40, 40, 40)
    x = int((lon_deg + 180) / 360 * w) % w
    y = int((90 - lat_deg) / 180 * h)
    cv2_circle(img, x, y, 12)
    return img


def cv2_circle(img, x, y, r):
    yy, xx = np.mgrid[:img.shape[0], :img.shape[1]]
    mask = (xx - x) ** 2 + (yy - y) ** 2 <= r * r
    img[mask] = (255, 255, 255)


def _pano_to_dual_fisheye(pano: np.ndarray, out_w=1280, out_h=720,
                          cal: Calibration | None = None):
    """Inverse map: equirect -> dual fisheye (for building test inputs)."""
    import cv2
    cal = cal or Calibration()
    lenses = lens_geometry(out_w, out_h, cal)
    fov_half = math.radians(cal.fov_deg) / 2.0
    h, w = pano.shape[:2]

    # build source->dest maps: for each fisheye pixel, find (lon, lat)
    map_x = np.full((out_h, out_w), -1, dtype=np.float32)
    map_y = np.full((out_h, out_w), -1, dtype=np.float32)
    yy, xx = np.mgrid[0:out_h, 0:out_w].astype(np.float32)
    for lens in lenses:
        in_circle = (xx - lens["cx"]) ** 2 + (yy - lens["cy"]) ** 2 <= lens["r"] ** 2
        rho = np.sqrt((xx - lens["cx"]) ** 2 + (yy - lens["cy"]) ** 2) / lens["r"]
        psi = np.arctan2(yy - lens["cy"], xx - lens["cx"]) - math.radians(cal.roll_deg)
        theta = rho * fov_half
        y_f = -np.sin(psi) * np.sin(theta)          # up
        z_f = np.cos(psi) * np.sin(theta)           # image right
        x_f = np.cos(theta)                         # optical axis
        lat = np.arcsin(np.clip(y_f, -1, 1))
        lon = np.arctan2(z_f, x_f) + lens["az"]
        px = ((lon + math.pi) / (2 * math.pi) * w)
        py = ((math.pi / 2 - lat) / math.pi * h)
        map_x[in_circle] = px[in_circle]
        map_y[in_circle] = py[in_circle]
    return cv2.remap(pano, map_x, map_y, cv2.INTER_LINEAR,
                     borderMode=cv2.BORDER_CONSTANT, borderValue=(20, 20, 20))


def _find_marker(img, thresh=200):
    ys, xs = np.where(img[:, :, 0] > thresh)
    if len(xs) == 0:
        return None
    return xs.mean(), ys.mean()


def test_fisheye_roundtrip_marker_angles():
    """Equirect -> fisheye -> equirect must preserve marker longitude."""
    import cv2
    cal = Calibration(fov_deg=195.0)
    w, h = 1024, 512
    for lon_deg in (-60, 0, 60):
        pano = _render_marker_pano(w, h, lon_deg=lon_deg, lat_deg=0)
        fisheye = _pano_to_dual_fisheye(pano, 1280, 720, cal)
        proj = EquirectProjector(1280, 720, 1024, cal)
        out = proj.project(fisheye)
        marker = _find_marker(out)
        assert marker is not None, f"marker lost at lon={lon_deg}"
        got_lon = (marker[0] / out.shape[1]) * 360 - 180
        assert abs(got_lon - lon_deg) < 12, f"lon drift {got_lon:.1f} vs {lon_deg}"


def test_geometry_two_circles():
    cal = Calibration()
    lenses = lens_geometry(1920, 1080, cal)
    assert len(lenses) == 2
    assert abs(lenses[0]["cx"] - 480) < 1
    assert abs(lenses[1]["cx"] - 1440) < 1
    assert abs(lenses[1]["az"] - lenses[0]["az"] - math.pi) < 1e-9
    # swap flips ownership
    cal2 = Calibration(swap_lenses=True)
    l2 = lens_geometry(1920, 1080, cal2)
    assert abs(l2[0]["az"] - lenses[1]["az"]) < 1e-9


def test_perspective_view_shapes_and_center():
    import cv2
    cal = Calibration()
    proj = EquirectProjector(1280, 720, 1024, cal)
    pano = _render_marker_pano(1024, 512, lon_deg=90, lat_deg=0)
    fisheye = _pano_to_dual_fisheye(pano, 1280, 720, cal)
    eq = proj.project(fisheye)
    view = project_to_perspective(eq, yaw_deg=90, pitch_deg=0, fov_deg=90,
                                  out_w=480, out_h=270)
    assert view.shape == (270, 480, 3)
    marker = _find_marker(view)
    # marker at view center-ish (yaw=90 looking straight at it)
    assert marker is not None, "marker not visible in perspective view"
    assert abs(marker[0] - 240) < 80
    assert abs(marker[1] - 135) < 60
