"""Motion detector: frame-difference based, with sensitivity + min area + cooldown."""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class MotionResult:
    motion: bool
    area: float = 0.0
    cx: float = 0.0     # normalized center of largest changed region
    cy: float = 0.0
    bbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)


class MotionDetector:
    def __init__(self, sensitivity: int = 25, min_area: int = 900):
        self.sensitivity = max(5, min(90, sensitivity))
        self.min_area = min_area
        self._prev_gray: np.ndarray | None = None

    def reset(self) -> None:
        self._prev_gray = None

    def process(self, image: np.ndarray) -> MotionResult:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (9, 9), 0)
        if self._prev_gray is None or self._prev_gray.shape != gray.shape:
            self._prev_gray = gray
            return MotionResult(motion=False)
        diff = cv2.absdiff(self._prev_gray, gray)
        _, thresh = cv2.threshold(diff, self.sensitivity, 255, cv2.THRESH_BINARY)
        thresh = cv2.dilate(thresh, None, iterations=2)
        self._prev_gray = gray

        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return MotionResult(motion=False)
        biggest = max(contours, key=cv2.contourArea)
        area = float(cv2.contourArea(biggest))
        if area < self.min_area:
            return MotionResult(motion=False, area=area)
        x, y, w, h = cv2.boundingRect(biggest)
        ih, iw = image.shape[:2]
        return MotionResult(
            motion=True, area=area,
            cx=(x + w / 2) / iw, cy=(y + h / 2) / ih,
            bbox=(x / iw, y / ih, w / iw, h / ih),
        )
