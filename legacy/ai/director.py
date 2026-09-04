"""AI Director (rule-based): picks the most interesting perspective view.

Score = faces*2 + persons*1.5 + motion*0.5 per view, with hysteresis so the
camera does not jump around: a challenger must beat the incumbent by a margin
and a minimum dwell time must elapse.
"""
from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class ViewScore:
    view_index: int
    yaw: float
    pitch: float
    score: float
    persons: int
    faces: int
    motion: bool


class RuleBasedDirector:
    def __init__(self, views: list[dict], margin: float = 1.5, min_dwell: float = 5.0):
        self.views = views                       # [{index, yaw, pitch, fov}]
        self.margin = margin
        self.min_dwell = min_dwell
        self.best_index = 0
        self._since_switch = time.time()

    def update(self, scores: list[ViewScore]) -> ViewScore:
        now = time.time()
        current = next((s for s in scores if s.view_index == self.best_index), scores[0])
        challenger = max(scores, key=lambda s: s.score)
        dwell_ok = now - self._since_switch >= self.min_dwell
        beats = challenger.score >= current.score + self.margin
        if challenger.view_index != self.best_index and dwell_ok and beats:
            self.best_index = challenger.view_index
            self._since_switch = now
        elif challenger.view_index == self.best_index:
            self._since_switch = now
        return next(s for s in scores if s.view_index == self.best_index)
