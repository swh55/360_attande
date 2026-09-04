"""Detector abstraction: pluggable backends (Null / OpenCV HOG / future YOLO).

Detectors operate on *perspective* views (rectilinear crops of the 360 scene)
because generic detectors expect perspective imagery, not equirect/fisheye.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol

import cv2
import numpy as np


@dataclass
class Detection:
    x: float            # normalized [0..1] in the view
    y: float
    w: float
    h: float
    label: str = "object"
    confidence: float = 0.0
    track_id: Optional[int] = None
    person_id: Optional[int] = None
    person_name: Optional[str] = None
    view_index: int = 0


class Detector(Protocol):
    name: str

    def detect(self, image: np.ndarray) -> list[Detection]: ...

    def close(self) -> None: ...


class NullDetector:
    """Always returns nothing. Default when AI deps are unavailable/disabled."""

    name = "null"

    def detect(self, image: np.ndarray) -> list[Detection]:
        return []

    def close(self) -> None:
        pass


class OpenCVPersonDetector:
    """HOG+SVM person detector shipped inside OpenCV (offline, CPU, no deps)."""

    name = "opencv_hog"

    def __init__(self, confidence: float = 0.5):
        self.hog = cv2.HOGDescriptor()  # type: ignore[attr-defined]
        self.hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())  # type: ignore[attr-defined]
        self.confidence = confidence

    def detect(self, image: np.ndarray) -> list[Detection]:
        h, w = image.shape[:2]
        if w < 64 or h < 64:
            return []
        rects, weights = self.hog.detectMultiScale(
            image, winStride=(8, 8), padding=(4, 4), scale=1.05,
            finalThreshold=2.0,
        )
        out: list[Detection] = []
        for (x, y, rw, rh), weight in zip(rects, weights):
            conf = float(min(1.0, weight))
            if conf < self.confidence:
                continue
            out.append(Detection(
                x=x / w, y=y / h, w=rw / w, h=rh / h,
                label="person", confidence=round(conf, 2),
            ))
        return out

    def close(self) -> None:
        pass


class YOLODetector:
    """Optional Ultralytics YOLO backend; only used if the package is installed
    and a model is provided. Kept behind a lazy import so base installs never
    need it."""

    name = "yolo"

    def __init__(self, model_path: str = "yolov8n.pt", confidence: float = 0.5):
        from ultralytics import YOLO  # type: ignore
        self.model = YOLO(model_path)
        self.confidence = confidence

    def detect(self, image: np.ndarray) -> list[Detection]:
        h, w = image.shape[:2]
        results = self.model.predict(image, verbose=False, conf=self.confidence)
        out: list[Detection] = []
        for r in results:
            for box in r.boxes:
                cls_name = self.model.names.get(int(box.cls[0]), "object")
                if cls_name != "person":
                    continue
                x1, y1, x2, y2 = [float(v) for v in box.xyxy[0]]
                out.append(Detection(
                    x=x1 / w, y=y1 / h, w=(x2 - x1) / w, h=(y2 - y1) / h,
                    label="person", confidence=round(float(box.conf[0]), 2),
                ))
        return out

    def close(self) -> None:
        pass


def build_person_detector(kind: str, confidence: float) -> Detector:
    if kind == "yolo":
        try:
            return YOLODetector(confidence=confidence)
        except ImportError:
            pass
    try:
        return OpenCVPersonDetector(confidence=confidence)
    except Exception:
        return NullDetector()
