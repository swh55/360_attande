"""Face detection layer (independent from recognition). OpenCV Haar / optional DNN."""
from __future__ import annotations

from typing import Optional

import cv2
import numpy as np

from .base import Detection


class OpenCVFaceDetector:
    """Haar cascade frontal face detector bundled with opencv-python."""

    name = "opencv_haar"

    def __init__(self, confidence: float = 0.5):
        cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"  # type: ignore[attr-defined]
        self.cascade: Optional[cv2.CascadeClassifier] = cv2.CascadeClassifier(cascade_path)
        self.confidence = confidence

    def detect(self, image: np.ndarray) -> list[Detection]:
        if self.cascade is None or self.cascade.empty():
            return []
        h, w = image.shape[:2]
        if w < 64 or h < 64:
            return []
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        gray = cv2.equalizeHist(gray)
        faces = self.cascade.detectMultiScale(
            gray, scaleFactor=1.12, minNeighbors=5,
            minSize=(max(24, h // 12), max(24, h // 12)),
        )
        out: list[Detection] = []
        for (x, y, fw, fh) in faces:
            out.append(Detection(
                x=x / w, y=y / h, w=fw / w, h=fh / h,
                label="face", confidence=self.confidence + 0.2,
            ))
        return out

    def close(self) -> None:
        pass


class OpenCVFaceDetectorDNN(OpenCVFaceDetector):
    """Placeholder for a YuNet/ONNX DNN backend; falls back to Haar now."""

    name = "opencv_dnn_fallback"
