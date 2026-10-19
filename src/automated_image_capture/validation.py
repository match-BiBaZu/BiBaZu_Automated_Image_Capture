"""Class-independent tracking and per-part voting for a right-to-left conveyor."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

import cv2
import numpy as np
from scipy.optimize import linear_sum_assignment

from automated_image_capture.inference import InferenceDetection


@dataclass(frozen=True, slots=True)
class ValidationSettings:
    speed_mm_s: float = 10.0
    exit_fraction: float = 0.05
    entry_fraction: float = 0.75
    min_observations: int = 5
    min_duration_s: float = 0.25
    min_consensus: float = 0.70
    max_gap_s: float = 0.5

    def validated(self) -> ValidationSettings:
        if not 0.1 <= self.speed_mm_s <= 200:
            raise ValueError("Die Bandgeschwindigkeit muss zwischen 0,1 und 200 mm/s liegen.")
        if not 0 <= self.exit_fraction <= 0.5:
            raise ValueError("Die Abschlusslinie muss in der linken Bildhälfte liegen.")
        if not self.exit_fraction < self.entry_fraction < 1:
            raise ValueError("Eintrittsbereich und Abschlusslinie überlappen sich.")
        if not 2 <= self.min_observations <= 1000:
            raise ValueError("Mindestens zwei Beobachtungen sind erforderlich.")
        if not 0 <= self.min_duration_s <= 60 or not 0.5 < self.min_consensus <= 1:
            raise ValueError("Ungültige Konsensparameter.")
        if not 0.05 <= self.max_gap_s <= 5:
            raise ValueError("Ungültige Überbrückungszeit.")
        return self


@dataclass(frozen=True, slots=True)
class ValidationResult:
    track_id: int
    first_seen: float
    finished_at: float
    class_id: int | None
    pose: str
    status: str
    reason: str
    observations: int
    consensus: float
    mean_confidence: float
    speed_mm_s: float
    image: str | None
    evidence_class_id: int | None = None


@dataclass(slots=True)
class PartTrack:
    track_id: int
    detection: InferenceDetection
    first_seen: float
    last_seen: float
    velocity: np.ndarray = field(default_factory=lambda: np.zeros(2))
    hits: int = 1
    ambiguous: bool = False
    finalized: bool = False
    weights: dict[int, float] = field(default_factory=dict)
    counts: dict[int, int] = field(default_factory=dict)
    best: dict[int, float] = field(default_factory=dict)
    first_vote: float | None = None
    last_vote: float | None = None

    @property
    def observations(self) -> int:
        return sum(self.counts.values())

    @property
    def winner(self) -> int | None:
        return max(self.weights, key=self.weights.get) if self.weights else None

    @property
    def consensus(self) -> float:
        total = sum(self.weights.values())
        return max(self.weights.values()) / total if total > 0 else 0.0


def points(detection: InferenceDetection) -> np.ndarray:
    return np.asarray(detection.corners, dtype=np.float32)


def obb_iou(a: np.ndarray, b: np.ndarray) -> float:
    area_a, area_b = abs(cv2.contourArea(a)), abs(cv2.contourArea(b))
    if min(area_a, area_b) <= 0:
        return 0.0
    intersection, _ = cv2.intersectConvexConvex(cv2.convexHull(a), cv2.convexHull(b))
    return float(intersection) / max(1e-9, area_a + area_b - intersection)


def suppress_duplicates(detections: tuple[InferenceDetection, ...]) -> list[InferenceDetection]:
    kept: list[InferenceDetection] = []
    for detection in sorted(detections, key=lambda d: d.confidence, reverse=True):
        polygon = points(detection)
        if (
            polygon.shape != (4, 2)
            or not np.isfinite(polygon).all()
            or not math.isfinite(detection.confidence)
            or detection.confidence <= 0
            or abs(cv2.contourArea(polygon)) <= 1
        ):
            continue
        if all(obb_iou(polygon, points(other)) < 0.6 for other in kept):
            kept.append(detection)
    return kept


class ValidationTracker:
    def __init__(
        self,
        settings: ValidationSettings,
        classes: dict[int, str],
        evidence: Callable[[int, InferenceDetection, np.ndarray], None] | None = None,
    ) -> None:
        self.settings = settings.validated()
        self.classes = dict(classes)
        self.tracks: dict[int, PartTrack] = {}
        self.results: list[ValidationResult] = []
        self._next_id = 1
        self._timestamp = -math.inf
        self._shape: tuple[int, int] | None = None
        self._evidence = evidence

    def counts(self) -> dict[str, int]:
        counts = Counter(result.status for result in self.results)
        return {"Unklar": counts["Unklar"], "Unvollständig": counts["Unvollständig"]}

    def pose_counts(self) -> dict[int, int]:
        return {
            cid: sum(r.status == "Erkannt" and r.class_id == cid for r in self.results)
            for cid in self.classes
        }

    def _cost(
        self,
        track: PartTrack,
        detection: InferenceDetection,
        timestamp: float,
        width: int,
        height: int,
    ) -> float:
        old, new = points(track.detection), points(detection)
        predicted = old + track.velocity * (timestamp - track.last_seen)
        delta = new.mean(axis=0) - predicted.mean(axis=0)
        diagonal = float(np.linalg.norm(np.ptp(old, axis=0)))
        gate = max(width * 0.08, diagonal * 1.5)
        distance = float(np.linalg.norm(delta))
        ratio = abs(cv2.contourArea(new)) / max(1, abs(cv2.contourArea(old)))
        if (
            distance > gate
            or delta[0] > gate * 0.5
            or not 0.35 <= ratio <= 3
            or abs(delta[1]) > max(height * 0.04, np.ptp(old[:, 1]) * 0.8)
        ):
            return 1e6
        return 0.6 * (1 - obb_iou(predicted.astype(np.float32), new)) + 0.4 * distance / gate

    def _vote(self, track: PartTrack, image: np.ndarray, timestamp: float) -> None:
        detection = track.detection
        polygon = points(detection)
        height, width = image.shape[:2]
        if (
            track.finalized
            or track.ambiguous
            or detection.class_id not in self.classes
            or polygon[:, 0].min() <= self.settings.exit_fraction * width
            or polygon[:, 0].max() >= width - 1
            or polygon[:, 1].min() <= 0
            or polygon[:, 1].max() >= height - 1
        ):
            return
        cid = detection.class_id
        track.weights[cid] = track.weights.get(cid, 0) + detection.confidence
        track.counts[cid] = track.counts.get(cid, 0) + 1
        if track.first_vote is None:
            track.first_vote = timestamp
        track.last_vote = timestamp
        if detection.confidence > track.best.get(cid, -1):
            if self._evidence is not None:
                self._evidence(track.track_id, detection, image)
            track.best[cid] = detection.confidence

    def _finish(
        self, track: PartTrack, timestamp: float, reason: str, incomplete: bool = False
    ) -> None:
        if track.finalized:
            return
        track.finalized = True
        winner = track.winner
        span = (
            track.last_vote - track.first_vote
            if track.first_vote is not None and track.last_vote is not None
            else 0
        )
        recognized = (
            not incomplete
            and not track.ambiguous
            and winner is not None
            and track.observations >= self.settings.min_observations
            and span >= self.settings.min_duration_s
            and track.consensus >= self.settings.min_consensus
        )
        status = "Unvollständig" if incomplete else "Erkannt" if recognized else "Unklar"
        # An entry detection is an attempted identification, even if tracking or
        # pose voting fails immediately. Never silently discard that attempt.
        if track.hits == 1:
            reason += "; nur eine Erkennung"
        if track.observations == 0:
            reason += "; keine gültige vollständig sichtbare Beobachtung"
        if track.ambiguous:
            reason += "; mehrdeutige Zuordnung"
        if not incomplete and not recognized:
            if track.observations < self.settings.min_observations:
                reason += "; zu wenige gültige Beobachtungen"
            if span < self.settings.min_duration_s:
                reason += "; Beobachtungsdauer zu kurz"
            if winner is not None and track.consensus < self.settings.min_consensus:
                reason += "; Konsensanteil zu niedrig"
        self.results.append(
            ValidationResult(
                track.track_id,
                track.first_seen,
                timestamp,
                winner if recognized else None,
                self.classes[winner] if recognized else status,
                status,
                reason,
                track.observations,
                track.consensus,
                track.weights[winner] / track.counts[winner] if winner is not None else 0,
                self.settings.speed_mm_s,
                f"bilder/{track.track_id:06d}.png" if winner is not None else None,
                winner,
            )
        )

    def update(
        self, image: np.ndarray, detections: tuple[InferenceDetection, ...], timestamp: float
    ) -> list[ValidationResult]:
        if timestamp <= self._timestamp:
            return []
        if not math.isfinite(timestamp):
            raise ValueError("Ungültiger Bildzeitpunkt.")
        height, width = image.shape[:2]
        if self._shape is not None and self._shape != (height, width):
            raise ValueError("Die Bildgröße hat sich während der Validation geändert.")
        self._shape = (height, width)
        self._timestamp = timestamp
        first_result = len(self.results)
        for tid, track in list(self.tracks.items()):
            # Retire old sealed identities, but associate this frame before
            # declaring an open track lost. The current OBB may show its exit.
            if (
                track.finalized
                and timestamp - track.last_seen >= self.settings.max_gap_s + 1.5
            ):
                del self.tracks[tid]
        valid = suppress_duplicates(detections)
        tracks = list(self.tracks.values())
        assigned: set[int] = set()
        blocked: set[int] = set()
        if tracks and valid:
            costs = np.array(
                [[self._cost(t, d, timestamp, width, height) for d in valid] for t in tracks]
            )
            rows, cols = linear_sum_assignment(costs)
            globally_assigned = {
                int(col) for row, col in zip(rows, cols, strict=True) if costs[row, col] < 1e5
            }
            # Competing geometrically similar matches are never allowed to exchange votes.
            ambiguous_rows: set[int] = set()
            for row in range(len(tracks)):
                candidates = np.flatnonzero(costs[row] < 1e5)
                if len(candidates) > 1:
                    order = candidates[np.argsort(costs[row, candidates])]
                    birth_competition = tracks[row].hits == 1 and any(
                        int(col) not in globally_assigned for col in candidates
                    )
                    if costs[row, order[1]] - costs[row, order[0]] < 0.12 or birth_competition:
                        ambiguous_rows.add(row)
                        blocked.update(candidates)
            for col in range(len(valid)):
                candidates = np.flatnonzero(costs[:, col] < 1e5)
                if len(candidates) > 1:
                    order = candidates[np.argsort(costs[candidates, col])]
                    if costs[order[1], col] - costs[order[0], col] < 0.12:
                        ambiguous_rows.update(candidates)
                        blocked.add(col)
            for row in ambiguous_rows:
                tracks[row].ambiguous = True
                tracks[row].hits += 1
                blocked.update(np.flatnonzero(costs[row] < 1e5))
            for row, col in zip(rows, cols, strict=True):
                if costs[row, col] >= 1e5:
                    continue
                assigned.add(int(col))
                track, detection = tracks[row], valid[col]
                if row in ambiguous_rows or col in blocked:
                    track.ambiguous = True
                    continue
                dt = timestamp - track.last_seen
                velocity = (
                    points(detection).mean(axis=0) - points(track.detection).mean(axis=0)
                ) / max(1e-6, dt)
                velocity[0] = min(0, velocity[0])
                track.velocity = 0.6 * velocity + 0.4 * track.velocity
                track.detection, track.last_seen = detection, timestamp
                track.hits += 1
                if points(detection)[:, 0].min() <= self.settings.exit_fraction * width:
                    self._finish(track, timestamp, "Abschlusslinie")
                else:
                    self._vote(track, image, timestamp)
                if (
                    track.finalized
                    and points(detection)[:, 0].max() < self.settings.exit_fraction * width
                ):
                    self.tracks.pop(track.track_id, None)
        for index, detection in enumerate(valid):
            if index in assigned or index in blocked:
                continue
            polygon = points(detection)
            if (
                polygon[:, 0].max() < self.settings.entry_fraction * width
                or polygon[:, 0].min() <= self.settings.exit_fraction * width
            ):
                continue
            track = PartTrack(self._next_id, detection, timestamp, timestamp)
            self._next_id += 1
            self.tracks[track.track_id] = track
            self._vote(track, image, timestamp)
        for tid, track in list(self.tracks.items()):
            if timestamp - track.last_seen > self.settings.max_gap_s:
                self._finish(track, timestamp, "Erkennung verloren", incomplete=True)
                # Keep a sealed identity briefly to prevent a second count.
                if timestamp - track.last_seen >= self.settings.max_gap_s + 1.5:
                    del self.tracks[tid]
        return self.results[first_result:]

    def stop(self, timestamp: float, reason: str = "Gestoppt") -> list[ValidationResult]:
        first = len(self.results)
        for track in self.tracks.values():
            self._finish(track, timestamp, reason, incomplete=True)
        self.tracks.clear()
        return self.results[first:]
