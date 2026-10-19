from __future__ import annotations

import json
import logging
import time
from collections import deque
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from automated_image_capture.inference import (
    InferenceFrame,
    InferenceModelInfo,
    LiveInferenceConfig,
)
from automated_image_capture.models import ConnectionState
from automated_image_capture.validation import ValidationSettings, ValidationTracker
from automated_image_capture.validation_store import ValidationSessionStore


class ValidationController(QObject):
    changed = pyqtSignal()
    running_changed = pyqtSignal(bool)
    error = pyqtSignal(str)
    exported = pyqtSignal(str)

    def __init__(self, camera, conveyor, parent=None) -> None:
        super().__init__(parent)
        self.camera, self.conveyor = camera, conveyor
        self.model: InferenceModelInfo | None = None
        self.tracker: ValidationTracker | None = None
        self.store: ValidationSessionStore | None = None
        self.settings = ValidationSettings()
        self.state = "idle"
        self.message = "Bereit · Bildbewegung rechts → links, Bandrichtung Rechts."
        self.speed_events: list[dict] = []
        self.runs: list[dict] = []
        self._frame_times: deque[float] = deque(maxlen=60)
        self.performance: dict[str, float] = {}
        self._last_performance_log = 0.0
        self._root = Path.home() / "Pictures" / "YOLOValidation"
        self._inference: LiveInferenceConfig | None = None
        self._started_wall = 0.0
        self._last_frame = 0.0
        self._last_band = 0.0
        self._deadline = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._watchdog)
        camera.state_changed.connect(self._camera_state)
        conveyor.state_changed.connect(self._band_state)
        conveyor.status_changed.connect(self._band_status)
        conveyor.error.connect(lambda message: self.stop(f"Förderbandfehler: {message}"))

    @property
    def running(self) -> bool:
        return self.state != "idle"

    @property
    def has_results(self) -> bool:
        return bool(self.tracker and self.tracker.results)

    @property
    def fps(self) -> float:
        if len(self._frame_times) < 2:
            return 0.0
        return (len(self._frame_times) - 1) / max(
            1e-6, self._frame_times[-1] - self._frame_times[0]
        )

    def set_model(self, model: InferenceModelInfo) -> None:
        if self.has_results and self.model is not None and model != self.model:
            self.stop("Das Modell hat sich geändert. Zuerst die Liste zurücksetzen.")
            self.error.emit("Modellwechsel bei vorhandener Ergebnissitzung ist gesperrt.")
            return
        if self.store is not None and self.model != model:
            if self.running:
                self.stop("Modellwechsel während der Validation.")
                return
            self.reset()
        self.model = model
        if self.running and (model.task != "obb" or not model.classes):
            self.stop("Validation benötigt ein OBB-Modell mit Klassenliste.")
        self.changed.emit()

    def start(
        self, settings: ValidationSettings, root: Path, inference: LiveInferenceConfig
    ) -> bool:
        if self.running:
            return False
        try:
            settings.validated()
            inference.validated()
            if self.camera.state is not ConnectionState.CONNECTED:
                raise ValueError("Bitte zuerst die Kamera verbinden.")
            if self.conveyor.state is not ConnectionState.CONNECTED:
                raise ValueError("Bitte zuerst das Förderband verbinden.")
            status = self.conveyor.status
            if not status.velocity_only or status.capture_interface_version != 1:
                raise ValueError("Die passende Zweimotoren-SPS ist erforderlich.")
            if self.conveyor.config.conveyor_forward_direction != "right":
                raise ValueError("Bitte in der Bandkarte Rechts als Vorwärtsrichtung bestätigen.")
            if status.busy or status.preparing_drive or status.error or status.servo_fault_code:
                raise ValueError("Das Förderband muss stillstehen und fehlerfrei sein.")
            if self.model is not None and (self.model.task != "obb" or not self.model.classes):
                raise ValueError("Bitte ein YOLO-OBB-Modell laden.")
            if (
                self.model is not None
                and Path(self.model.path).resolve() != inference.model_path.resolve()
            ):
                raise ValueError("Das ausgewählte Modell ist noch nicht geladen.")
        except ValueError as exc:
            self.error.emit(str(exc))
            return False
        self.settings, self._root, self._inference = settings, root, inference
        self._started_wall = time.time()
        self._last_frame = self._last_band = time.monotonic()
        self._deadline = time.monotonic() + 30
        self._frame_times.clear()
        self.performance.clear()
        self._last_performance_log = 0.0
        self.state = "warming"
        self.message = "Warte auf geladenes OBB-Modell und erfolgreiche Kamerainferenz …"
        self.running_changed.emit(True)
        self.changed.emit()
        self.camera.set_capture_stream_enabled(True, inference.max_fps)
        self._timer.start()
        return True

    def _open_session(self) -> None:
        assert self.model is not None
        if self.store is None:
            self.tracker = ValidationTracker(self.settings, self.model.classes, self._evidence)
            self.store = ValidationSessionStore(self._root, self.snapshot(), self)
            self.store.failed.connect(self._storage_error)
            self.store.exported.connect(self.exported.emit)
        assert self.tracker is not None
        self.tracker.settings = self.settings
        self.runs.append(
            {
                "started_at": self._started_wall,
                "settings": asdict(self.settings),
                "inference": {
                    **asdict(self._inference),
                    "model_path": str(self._inference.model_path),
                },
            }
        )
        self.speed_events.append({"timestamp": time.time(), "speed_mm_s": self.settings.speed_mm_s})
        self.store.save([], self.snapshot())

    def on_frame(self, frame: InferenceFrame) -> None:
        if not self.running or frame.timestamp < self._started_wall:
            return
        self._last_frame = time.monotonic()
        self._frame_times.append(self._last_frame)
        self.performance = {
            **frame.timings,
            "inference_ms": frame.inference_ms,
            "frame_age_ms": max(0.0, time.time() - frame.timestamp) * 1000,
            "fps": self.fps,
        }
        if self._last_frame - self._last_performance_log >= 5:
            self._last_performance_log = self._last_frame
            logging.getLogger(__name__).info(
                "Validation-Leistung: %s", json.dumps(self.performance, sort_keys=True)
            )
        try:
            if self.state == "warming":
                if self.model is None:
                    return
                self._open_session()
                self.state = "preparing"
                self.message = "Erkennung bereit · beide Bandmotoren werden vorbereitet …"
                self._deadline = time.monotonic() + 25
                if not self.conveyor.start_continuous(self.settings.speed_mm_s):
                    raise RuntimeError("Der Förderband-Dauerlauf konnte nicht angefordert werden.")
            elif self.state == "running" and self.tracker is not None:
                image = frame.raw_image if frame.raw_image is not None else frame.image
                results = self.tracker.update(image, frame.detections, frame.timestamp)
                if results:
                    self.store.save(results, self.snapshot())
                self.message = (
                    f"Läuft · {self.fps:.1f} Inferenz-FPS · "
                    f"{len(self.tracker.results)} abgeschlossene Bauteile"
                )
            self.changed.emit()
        except Exception as exc:
            self.stop(str(exc))
            self.error.emit(str(exc))

    def _evidence(self, tid, detection, image) -> None:
        if self.store is not None:
            self.store.evidence(tid, detection, image)

    def _band_status(self, status) -> None:
        self._last_band = time.monotonic()
        if not self.running:
            return
        if status.error or status.servo_fault_code:
            self.stop("Förderband meldet einen Fehler.")
        elif self.state == "preparing" and status.continuous_running:
            self.state = "running"
            self.message = "Dauerlauf bestätigt · Validation läuft."
            self.changed.emit()
        elif self.state == "running" and not status.continuous_running:
            self.stop("Förderband-Dauerlauf wurde unterbrochen.")

    def _camera_state(self, state) -> None:
        if self.running and state is not ConnectionState.CONNECTED:
            self.stop("Kameraverbindung unterbrochen.")

    def _band_state(self, state) -> None:
        if self.running and state is not ConnectionState.CONNECTED:
            self.stop("Förderbandverbindung unterbrochen.")

    def _watchdog(self) -> None:
        now = time.monotonic()
        if not self.running:
            return
        if self.state in {"warming", "preparing"} and now > self._deadline:
            self.stop("Zeitüberschreitung beim Laden oder Vorbereiten.")
        elif self.state != "warming" and now - self._last_frame > 3:
            self.stop("Seit drei Sekunden keine erfolgreiche Kamerainferenz.")
        elif now - self._last_band > 3:
            self.stop("Keine aktuelle Förderbandrückmeldung.")

    def set_speed(self, speed_mm_s: float) -> bool:
        if self.state != "running" or not self.conveyor.set_continuous_speed(speed_mm_s):
            return False
        self.settings = replace(self.settings, speed_mm_s=speed_mm_s).validated()
        self.tracker.settings = self.settings
        self.speed_events.append({"timestamp": time.time(), "speed_mm_s": speed_mm_s})
        self.store.save([], self.snapshot())
        self.changed.emit()
        return True

    def stop(self, reason: str = "Gestoppt") -> None:
        if not self.running:
            return
        self.state = "idle"
        self.message = reason
        self._timer.stop()
        self.conveyor.stop_motion()
        self.camera.set_capture_stream_enabled(False)
        if self.runs:
            self.runs[-1]["stopped_at"] = time.time()
            self.runs[-1]["stop_reason"] = reason
        if self.tracker is not None:
            results = self.tracker.stop(time.time(), reason)
            try:
                if self.store is not None:
                    self.store.save(results, self.snapshot())
            except Exception as exc:
                self.error.emit(str(exc))
        self.running_changed.emit(False)
        self.changed.emit()

    def _storage_error(self, message) -> None:
        self.stop(message)
        self.error.emit(message)

    def snapshot(self) -> dict:
        return {
            "format_version": 1,
            "updated_at": datetime.now().astimezone().isoformat(),
            "model": asdict(self.model) if self.model is not None else {"classes": {}},
            "settings": asdict(self.settings),
            "runs": self.runs,
            "running": self.running,
            "message": self.message,
            "speed_events": self.speed_events,
            "performance": self.performance,
            "results": [asdict(r) for r in self.tracker.results] if self.tracker else [],
            "pose_counts": self.tracker.pose_counts() if self.tracker else {},
            "status_counts": self.tracker.counts()
            if self.tracker
            else {"Unklar": 0, "Unvollständig": 0},
        }

    def export(self, destination: Path) -> None:
        if self.store is None:
            raise ValueError("Noch keine Validierungssitzung vorhanden.")
        self.store.export(destination, self.snapshot())

    def reset(self) -> None:
        if self.running:
            raise ValueError("Die Validation muss zuerst gestoppt werden.")
        if self.store is not None:
            self.store.close()
            self.store.deleteLater()
        self.tracker, self.store = None, None
        self.speed_events, self.runs = [], []
        self.message = "Neue Sitzung · bereit."
        self.changed.emit()

    def close(self) -> None:
        self.stop("GUI beendet")
        if self.store is not None:
            self.store.close()
