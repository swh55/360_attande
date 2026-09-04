"""Face recognition engine (optional): needs the `face_recognition` package.

People identities live in `people`; their 128-d embeddings in
`face_embeddings`. Matching is nearest-embedding with a distance threshold.
Everything works offline. If the package is missing, `available=False` and the
hub simply reports recognition disabled.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

import numpy as np

from ..core import database as db

LOG = logging.getLogger("hub.ai")

try:  # optional dependency
    import face_recognition  # type: ignore
    AVAILABLE = True
except ImportError:
    face_recognition = None  # type: ignore
    AVAILABLE = False


class RecognitionResult:
    def __init__(self, person_id: Optional[int], name: Optional[str], distance: float):
        self.person_id = person_id
        self.name = name
        self.distance = distance

    @property
    def known(self) -> bool:
        return self.person_id is not None


class RecognitionEngine:
    def __init__(self, threshold: float = 0.5):
        self.threshold = threshold

    @staticmethod
    def available() -> bool:
        return AVAILABLE

    # ---- enrollment ---------------------------------------------------------
    def compute_embedding(self, image_bgr: np.ndarray) -> Optional[np.ndarray]:
        if not AVAILABLE:
            return None
        rgb = image_bgr[:, :, ::-1].copy()
        boxes = face_recognition.face_locations(rgb)
        if not boxes:
            return None
        encodings = face_recognition.face_encodings(rgb, boxes[:1])
        return encodings[0] if encodings else None

    def enroll(self, person_id: int, image_bgr: np.ndarray, source: str = "upload") -> bool:
        emb = self.compute_embedding(image_bgr)
        if emb is None:
            return False
        db.execute(
            "INSERT INTO face_embeddings (person_id, embedding, dim, source, created_at) "
            "VALUES (?, ?, ?, ?, datetime('now','localtime'))",
            (person_id, np.asarray(emb, dtype=np.float32).tobytes(), int(emb.shape[0]), source),
        )
        return True

    # ---- matching -------------------------------------------------------------
    def _load_embeddings(self) -> list[dict]:
        return db.query(
            """SELECT e.id, e.person_id, e.embedding, p.name
               FROM face_embeddings e JOIN people p ON p.id = e.person_id
               WHERE p.active = 1"""
        )

    def identify(self, image_bgr: np.ndarray, face_box: tuple[float, float, float, float] | None = None) -> Optional[RecognitionResult]:
        if not AVAILABLE:
            return None
        emb = self.compute_embedding(image_bgr)
        if emb is None:
            return None
        rows = self._load_embeddings()
        if not rows:
            return RecognitionResult(None, "Unknown", 1.0)
        best_id: Optional[int] = None
        best_name: Optional[str] = None
        best_dist = 999.0
        emb_arr = np.asarray(emb, dtype=np.float32)
        for row in rows:
            try:
                stored = np.frombuffer(row["embedding"], dtype=np.float32)
            except (ValueError, TypeError):
                continue
            if stored.size != emb_arr.size:
                continue
            dist = float(np.linalg.norm(stored - emb_arr))
            if dist < best_dist:
                best_dist = dist
                best_id = row["person_id"]
                best_name = row["name"]
        if best_id is not None and best_dist <= self.threshold:
            return RecognitionResult(best_id, best_name, round(best_dist, 3))
        return RecognitionResult(None, "Unknown", round(best_dist, 3))

    # ---- maintenance ----------------------------------------------------------
    def recompute_for_person(self, person_id: int) -> bool:
        """Re-enroll from the person's stored photo (keeps DB lean)."""
        row = db.query_one("SELECT photo_path FROM people WHERE id=?", (person_id,))
        if not row or not row["photo_path"]:
            return False
        import cv2
        img = cv2.imread(row["photo_path"])
        if img is None:
            return False
        return self.enroll(person_id, img, source="recompute")
