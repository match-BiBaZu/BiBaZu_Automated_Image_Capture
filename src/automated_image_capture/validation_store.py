"""Bounded background image persistence and consistent validation exports."""

from __future__ import annotations

import csv
import json
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import cv2
import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal

from automated_image_capture.inference import InferenceDetection, draw_obb_overlay
from automated_image_capture.validation import ValidationResult


def atomic_json(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


class ValidationSessionStore(QObject):
    failed = pyqtSignal(str)
    exported = pyqtSignal(str)

    def __init__(self, root: Path, metadata: dict, parent: QObject | None = None) -> None:
        super().__init__(parent)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.directory = root / f"validation_{stamp}_{uuid4().hex[:8]}"
        (self.directory / "belege").mkdir(parents=True)
        (self.directory / "bilder").mkdir()
        atomic_json(self.directory / "session.json", metadata)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="validation-save")
        self._slots = threading.BoundedSemaphore(16)
        self._closed = False
        self._failed = False

    def _submit(self, function, *args) -> None:
        if self._closed or self._failed:
            raise RuntimeError("Die Ergebnissicherung ist nicht verfügbar.")
        if not self._slots.acquire(blocking=False):
            raise RuntimeError("Die Ergebnissicherung kommt nicht nach (16 Schreibaufträge).")
        try:
            future = self._executor.submit(function, *args)
        except Exception:
            self._slots.release()
            raise

        def done(result):
            self._slots.release()
            error = result.exception()
            if error is not None:
                self._failed = True
                self.failed.emit(f"Ergebnissicherung fehlgeschlagen: {error}")

        future.add_done_callback(done)

    def evidence(self, track_id: int, detection: InferenceDetection, image: np.ndarray) -> None:
        # At most sixteen frame copies are retained by the writer queue.
        self._submit(self._write_evidence, track_id, detection, image.copy())

    def _write_evidence(self, track_id: int, detection: InferenceDetection, image: np.ndarray):
        annotated = draw_obb_overlay(image, (detection,))
        cv2.putText(
            annotated,
            f"Track {track_id}",
            (12, 28),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (255, 255, 0),
            2,
        )
        path = self.directory / "belege" / f"{track_id:06d}_{detection.class_id}.png"
        self._png(path, annotated)

    @staticmethod
    def _png(path: Path, rgb: np.ndarray):
        temporary = path.with_name(path.stem + ".tmp.png")
        if not cv2.imwrite(str(temporary), cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)):
            raise OSError(f"Bild konnte nicht gespeichert werden: {path}")
        temporary.replace(path)

    def save(self, results: list[ValidationResult], snapshot: dict) -> None:
        self._submit(self._save, tuple(results), json.loads(json.dumps(snapshot)))

    def _save(self, results: tuple[ValidationResult, ...], snapshot: dict) -> None:
        for result in results:
            if result.image is not None:
                source = (
                    self.directory
                    / "belege"
                    / (f"{result.track_id:06d}_{result.evidence_class_id}.png")
                )
                image = cv2.imread(str(source))
                if image is None:
                    raise OSError(f"Belegbild fehlt: {source}")
                label = f"{result.status}: {result.pose}".encode("ascii", "replace").decode()
                cv2.putText(image, label, (12, 56), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
                self._png(self.directory / result.image, cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
            # Immutable per-part records survive an interrupted session snapshot.
            atomic_json(self.directory / f"part_{result.track_id:06d}.json", asdict(result))
        atomic_json(self.directory / "session.json", snapshot)

    def export(self, destination: Path, snapshot: dict) -> None:
        if destination.resolve() == self.directory.resolve():
            raise ValueError("Bitte einen anderen Exportordner wählen.")
        self._submit(self._export, destination, json.loads(json.dumps(snapshot)))

    def _export(self, destination: Path, snapshot: dict) -> None:
        # Create a new child every time; exports never overwrite earlier results.
        target = destination / f"{self.directory.name}_export_{uuid4().hex[:8]}"
        target.mkdir(parents=True)
        (target / "bilder").mkdir()
        columns = [
            "track_id",
            "first_seen",
            "finished_at",
            "class_id",
            "pose",
            "status",
            "reason",
            "observations",
            "consensus",
            "mean_confidence",
            "speed_mm_s",
            "image",
        ]
        with (target / "bauteile.csv").open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(
                stream, fieldnames=columns, delimiter=";", extrasaction="ignore"
            )
            writer.writeheader()
            for record in snapshot["results"]:
                row = dict(record)
                for key in ("first_seen", "finished_at"):
                    row[key] = datetime.fromtimestamp(row[key]).astimezone().isoformat()
                writer.writerow(row)
                if record["image"]:
                    shutil.copy2(self.directory / record["image"], target / record["image"])
        with (target / "summen.csv").open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream, delimiter=";")
            writer.writerow(["Klassen-ID", "Pose/Status", "Anzahl"])
            for cid, name in snapshot["model"]["classes"].items():
                writer.writerow([cid, name, snapshot["pose_counts"].get(str(cid), 0)])
            for status, count in snapshot["status_counts"].items():
                writer.writerow(["", status, count])
        atomic_json(target / "session.json", snapshot)
        self.exported.emit(str(target))

    def flush(self) -> None:
        if not self._closed:
            self._executor.submit(lambda: None).result(timeout=30)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._executor.shutdown(wait=True)
