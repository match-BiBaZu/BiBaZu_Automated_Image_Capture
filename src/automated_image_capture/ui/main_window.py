from __future__ import annotations

import logging
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCloseEvent, QImage, QPixmap
from PyQt6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from automated_image_capture.acquisition import AcquisitionController, build_capture_points
from automated_image_capture.hardware import (
    CameraAdapter,
    ConveyorAdapter,
    LightAdapter,
)
from automated_image_capture.inference import (
    InferenceFrame,
    LiveInferenceConfig,
    LiveInferenceWorker,
)
from automated_image_capture.models import (
    CameraFrame,
    CameraStatus,
    ConnectionState,
    ConveyorStatus,
    LightStatus,
)
from automated_image_capture.settings import SettingsStore
from automated_image_capture.ui.cleanup_dialog import CleanupDialog
from automated_image_capture.ui.labeling_dialog import LabelingDialog
from automated_image_capture.ui.training_dialog import TrainingDialog
from automated_image_capture.ui.validation_panel import ValidationPanel, ValidationPreview
from automated_image_capture.ui.widgets import (
    AcquisitionCard,
    AcquisitionDialog,
    CameraControlCard,
    ConveyorControlCard,
    LightControlCard,
    SettingsDialog,
)
from automated_image_capture.validation_controller import ValidationController


class MainWindow(QMainWindow):
    def __init__(
        self,
        settings_store: SettingsStore | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("YOLO Trainingsbild-Aufnahme – Hardware-Dashboard")
        self.resize(1420, 900)
        self._logger = logging.getLogger("ui")
        self.settings_store = settings_store or SettingsStore()
        self.config = self.settings_store.load()
        self.acquisition_config = self.settings_store.load_acquisition()
        self.inference_config = self.settings_store.load_live_inference()
        self._last_image: QImage | None = None
        self._last_raw_image: QImage | None = None
        self._inference_worker: LiveInferenceWorker | None = None
        self._inference_has_result = False
        self._camera_status_data = CameraStatus()
        self._closing = False
        self._training_dialog: TrainingDialog | None = None
        self.camera = CameraAdapter(self.config, self)
        self.conveyor = ConveyorAdapter(self.config, self)
        self.light = LightAdapter(
            self.config,
            self,
            display_name="Neewer-Licht 1",
            address_attribute="light_address",
            excluded_addresses=lambda: {self.config.light_2_address},
        )
        self.light_2 = LightAdapter(
            self.config,
            self,
            display_name="Neewer-Licht 2",
            address_attribute="light_2_address",
            excluded_addresses=lambda: {self.config.light_address},
        )
        self.acquisition = AcquisitionController(
            self.camera,
            self.light,
            self.light_2,
            self,
            conveyor=self.conveyor,
        )
        self.validation = ValidationController(self.camera, self.conveyor, self)

        self._build_ui()
        self._wire_adapters()
        self.statusBar().showMessage(
            f"Kamera {self.config.camera_ip} · SPS {self.config.plc_ip} · feste Rutsche"
        )
        if self.acquisition.restore_interrupted(self.acquisition_config):
            if self.acquisition.session_settings is not None:
                self.acquisition_config = self.acquisition.session_settings
                self.acquisition_card.set_settings(self.acquisition_config)
        self._refresh_acquisition_preflight()
        self._append_event("Dashboard bereit. Es werden noch keine Geräte automatisch verbunden.")

    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)

        toolbar = QHBoxLayout()
        connect_all = QPushButton("Alle verbinden")
        disconnect_all = QPushButton("Alle trennen")
        labeling_button = QPushButton("OBB-Labels …")
        cleanup_button = QPushButton("Speicher bereinigen …")
        settings_button = QPushButton("Einstellungen …")
        training_button = QPushButton("YOLO-Training …")
        connect_all.clicked.connect(self.connect_all)
        disconnect_all.clicked.connect(self.disconnect_all)
        labeling_button.clicked.connect(self.open_labeling)
        cleanup_button.clicked.connect(self.open_cleanup)
        training_button.clicked.connect(self.open_training)
        settings_button.clicked.connect(self.open_settings)
        toolbar.addWidget(connect_all)
        toolbar.addWidget(disconnect_all)
        toolbar.addWidget(labeling_button)
        toolbar.addWidget(training_button)
        self.validation_banner = QLabel()
        self.validation_stop = QPushButton("Validation + Band stoppen")
        self.validation_stop.clicked.connect(lambda: self.validation.stop())
        self.validation_stop.hide()
        toolbar.addWidget(self.validation_banner)
        toolbar.addWidget(self.validation_stop)
        toolbar.addWidget(cleanup_button)
        toolbar.addStretch(1)
        safety = QLabel("Fest montierte Rutsche · feste Neigung · Zweimotoren-Förderband")
        safety.setStyleSheet("color:#b45309; font-weight:600;")
        toolbar.addWidget(safety)
        toolbar.addWidget(settings_button)
        root.addLayout(toolbar)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        cards_container = QWidget()
        cards_layout = QVBoxLayout(cards_container)
        self.camera_card = CameraControlCard("Baumer Industriekamera")
        self.conveyor_card = ConveyorControlCard(
            "TwinCAT-Förderband · 2 Motoren", self.config.conveyor_forward_direction
        )
        self.light_card = LightControlCard("Neewer RGB660 Pro II · Licht 1")
        self.light_2_card = LightControlCard("Neewer RGB660 Pro II · Licht 2")
        self.acquisition_card = AcquisitionCard()
        self.camera_card.action_requested.connect(lambda: self._toggle(self.camera))
        self.conveyor_card.action_requested.connect(lambda: self._toggle(self.conveyor))
        self.light_card.action_requested.connect(lambda: self._toggle(self.light))
        self.light_2_card.action_requested.connect(lambda: self._toggle(self.light_2))
        cards_layout.addWidget(self.acquisition_card)
        cards_layout.addWidget(self.camera_card)
        cards_layout.addWidget(self.conveyor_card)
        cards_layout.addWidget(self.light_card)
        cards_layout.addWidget(self.light_2_card)
        self.light_power = self.light_card.power_button
        self.light_mode = self.light_card.mode
        self.light_brightness = self.light_card.brightness
        self.light_cct = self.light_card.cct
        self.light_hue = self.light_card.hue
        self.light_saturation = self.light_card.saturation
        self.light_command_timer = self.light_card.command_timer
        self.acquisition_card.set_settings(self.acquisition_config)
        self.acquisition_card.set_conveyor_direction(
            self.config.conveyor_forward_direction, self.acquisition_config.conveyor_enabled
        )
        cards_layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(cards_container)
        scroll.setMinimumWidth(570)
        self.tabs = QTabWidget()
        self.tabs.addTab(scroll, "Aufnahme")
        self.validation_panel = ValidationPanel()
        saved_validation, saved_root = self.settings_store.load_validation()
        self.validation_panel.speed.setValue(saved_validation.speed_mm_s)
        # Restore the entry bound before applying an exit beyond the default entry.
        self.validation_panel.line.setValue(0)
        self.validation_panel.entry.setValue(saved_validation.entry_fraction * 100)
        self.validation_panel.line.setValue(saved_validation.exit_fraction * 100)
        self.validation_panel.min_consensus.setValue(saved_validation.min_consensus * 100)
        self.validation_panel.output.setText(str(saved_root))
        self.validation_panel.setMinimumWidth(570)
        self.tabs.addTab(self.validation_panel, "YOLO Validation")
        self.tabs.currentChanged.connect(self._validation_tab_changed)
        splitter.addWidget(self.tabs)

        preview_container = QWidget()
        preview_layout = QVBoxLayout(preview_container)
        preview_title = QLabel("Kamera-Livebild")
        preview_title.setStyleSheet("font-size:16px; font-weight:600;")
        preview_layout.addWidget(preview_title)

        inference_controls = QHBoxLayout()
        self.inference_toggle = QCheckBox("YOLO Live-Erkennung")
        self.inference_toggle.setToolTip(
            "Führt das trainierte OBB-Modell auf dem neuesten Kamerabild aus."
        )
        self.inference_model = QLineEdit()
        self.inference_model.setReadOnly(True)
        self.inference_model.setMinimumWidth(220)
        self.inference_model.setText(str(self.inference_config.model_path))
        self.inference_model.setToolTip(str(self.inference_config.model_path))
        choose_model = QPushButton("Modell …")
        self.choose_model = choose_model
        choose_model.clicked.connect(self._choose_inference_model)
        self.load_model = QPushButton("Laden")
        self.load_model.clicked.connect(lambda: self.inference_toggle.setChecked(True))
        self.inference_confidence = QDoubleSpinBox()
        self.inference_confidence.setRange(0.01, 1.0)
        self.inference_confidence.setSingleStep(0.05)
        self.inference_confidence.setDecimals(2)
        self.inference_confidence.setValue(self.inference_config.confidence)
        self.inference_confidence.setPrefix("Konf. ")
        self.inference_confidence.setToolTip("Minimale Konfidenz für eine angezeigte OBB")
        self.inference_max_fps = QSpinBox()
        self.inference_max_fps.setRange(1, 60)
        self.inference_max_fps.setValue(round(self.inference_config.max_fps))
        self.inference_max_fps.setSuffix(" FPS")
        self.inference_max_fps.setToolTip("Maximale Bildrate der YOLO-Inferenz")
        self.inference_status = QLabel("Aus")
        self.inference_status.setMinimumWidth(150)
        self.inference_status.setStyleSheet("color:#64748b;")
        self.inference_toggle.toggled.connect(self._toggle_live_inference)
        self.inference_confidence.editingFinished.connect(self._apply_inference_runtime_settings)
        self.inference_max_fps.editingFinished.connect(self._apply_inference_runtime_settings)
        inference_controls.addWidget(self.inference_toggle)
        inference_controls.addWidget(self.inference_model, 1)
        inference_controls.addWidget(choose_model)
        inference_controls.addWidget(self.load_model)
        inference_controls.addWidget(self.inference_confidence)
        inference_controls.addWidget(self.inference_max_fps)
        inference_controls.addWidget(self.inference_status)
        preview_layout.addLayout(inference_controls)

        duplicate_controls = QHBoxLayout()
        self.inference_duplicate_filter = QCheckBox("Doppelte Posen filtern")
        self.inference_duplicate_filter.setChecked(self.inference_config.duplicate_filter)
        self.inference_duplicate_filter.setToolTip(
            "Bei stark überlappenden OBBs bleibt nur die Pose mit der höchsten Konfidenz."
        )
        self.inference_duplicate_iou = QDoubleSpinBox()
        self.inference_duplicate_iou.setRange(0.01, 1)
        self.inference_duplicate_iou.setSingleStep(0.05)
        self.inference_duplicate_iou.setDecimals(2)
        self.inference_duplicate_iou.setValue(self.inference_config.duplicate_iou)
        self.inference_duplicate_iou.setPrefix("Überlappung (IoU) ")
        self.inference_duplicate_iou.setToolTip(
            "Ab dieser OBB-Überlappung wird die schwächere Erkennung ausgeblendet. "
            "Kleinere Werte filtern stärker. Standard: 0,60."
        )
        self.inference_duplicate_iou.setEnabled(self.inference_config.duplicate_filter)
        self.inference_duplicate_filter.toggled.connect(self._apply_inference_runtime_settings)
        self.inference_duplicate_iou.editingFinished.connect(self._apply_inference_runtime_settings)
        duplicate_controls.addWidget(self.inference_duplicate_filter)
        duplicate_controls.addWidget(self.inference_duplicate_iou)
        duplicate_controls.addStretch(1)
        preview_layout.addLayout(duplicate_controls)

        self.preview = ValidationPreview("Noch kein Kamerabild")
        self.preview.line_fraction = self.validation_panel.line.value() / 100
        self.preview.entry_fraction = self.validation_panel.entry.value() / 100
        self.preview.line_changed.connect(
            lambda value: self.validation_panel.line.setValue(value * 100)
        )
        self.preview.entry_changed.connect(
            lambda value: self.validation_panel.entry.setValue(value * 100)
        )
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumSize(640, 480)
        self.preview.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.preview.setStyleSheet("background:#111827; color:#d1d5db; border-radius:5px;")
        preview_layout.addWidget(self.preview, 1)
        splitter.addWidget(preview_container)
        splitter.setSizes([570, 850])
        root.addWidget(splitter, 1)

        log_header = QHBoxLayout()
        log_header.addWidget(QLabel("Ereignisprotokoll"))
        clear_log = QPushButton("Leeren")
        clear_log.clicked.connect(lambda: self.event_log.clear())
        log_header.addStretch(1)
        log_header.addWidget(clear_log)
        root.addLayout(log_header)
        self.event_log = QPlainTextEdit()
        self.event_log.setReadOnly(True)
        self.event_log.setMaximumBlockCount(1000)
        self.event_log.setMaximumHeight(180)
        root.addWidget(self.event_log)

        self.setCentralWidget(central)
        self.statusBar().showMessage(f"Kamera {self.config.camera_ip} · SPS {self.config.plc_ip}")

    def _wire_adapters(self) -> None:
        self.validation.changed.connect(self._refresh_validation)
        self.validation.running_changed.connect(self._validation_running)
        self.validation.error.connect(lambda message: self._show_error("Validation", message))
        self.validation.exported.connect(
            lambda directory: self._append_event(f"Validation exportiert: {directory}")
        )
        self.validation_panel.start_requested.connect(self.start_validation)
        self.validation_panel.stop_requested.connect(lambda: self.validation.stop())
        self.validation_panel.speed_requested.connect(self._validation_speed)
        self.validation_panel.export_requested.connect(self._export_validation)
        self.validation_panel.reset_requested.connect(self.validation.reset)
        self.validation_panel.line_changed.connect(self._validation_line)
        self.validation_panel.entry_changed.connect(self._validation_entry)
        self.camera.capture_frame_ready.connect(self._validation_camera_frame)
        self._refresh_validation()
        for adapter, card in (
            (self.camera, self.camera_card),
            (self.conveyor, self.conveyor_card),
            (self.light, self.light_card),
            (self.light_2, self.light_2_card),
        ):
            adapter.state_changed.connect(card.set_state)
            adapter.state_changed.connect(self._refresh_acquisition_preflight)
            adapter.event_message.connect(self._append_event)
            adapter.error.connect(
                lambda message, name=adapter.display_name: self._show_error(name, message)
            )
        self.camera.status_changed.connect(self._camera_status)
        self.camera.frame_ready.connect(self._camera_frame)
        self.camera_card.exposure_requested.connect(self.camera.set_exposure_time)
        self.conveyor.status_changed.connect(self._conveyor_status)
        self.conveyor_card.jog_requested.connect(self._jog_conveyor)
        self.conveyor_card.stop_requested.connect(self.conveyor.stop_motion)
        self.conveyor_card.origin_requested.connect(self.conveyor.set_current_as_origin)
        self.conveyor_card.forward_direction_changed.connect(self._set_conveyor_forward_direction)
        self.light.status_changed.connect(self._light_status)
        self.light.state_changed.connect(self._light_state)
        self.light_2.status_changed.connect(self._light_2_status)
        self.light_2.state_changed.connect(self._light_2_state)
        for adapter, card in (
            (self.light, self.light_card),
            (self.light_2, self.light_2_card),
        ):
            card.power_requested.connect(adapter.set_power)
            card.cct_requested.connect(adapter.set_cct)
            card.hsi_requested.connect(adapter.set_hsi)
        self.acquisition_card.configure_requested.connect(self.open_acquisition_settings)
        self.acquisition_card.start_requested.connect(self.start_acquisition)
        self.acquisition_card.resume_requested.connect(self.resume_acquisition)
        self.acquisition_card.stop_requested.connect(self.acquisition.stop)
        self.acquisition_card.align_conveyor_requested.connect(self._align_conveyor_for_resume)
        self.acquisition.running_changed.connect(self.acquisition_card.set_running)
        self.acquisition.resume_available_changed.connect(
            self.acquisition_card.set_resume_available
        )
        self.acquisition.running_changed.connect(self._acquisition_running)
        self.acquisition.progress_changed.connect(self.acquisition_card.set_progress)
        self.acquisition.status_changed.connect(self._acquisition_status)
        self.acquisition.error.connect(
            lambda message: self._show_error("Automatische Aufnahme", message)
        )
        self.acquisition.alignment_required_changed.connect(
            self.acquisition_card.set_alignment_required
        )

    def _toggle(self, adapter: CameraAdapter | ConveyorAdapter | LightAdapter) -> None:
        if adapter.state is ConnectionState.DISCONNECTED:
            adapter.connect()
        else:
            adapter.disconnect()

    def connect_all(self) -> None:
        self.camera.connect()
        self.conveyor.connect()
        self.light.connect()
        self.light_2.connect()

    def disconnect_all(self) -> None:
        self.acquisition.stop()
        self.camera.disconnect()
        self.conveyor.disconnect()
        self.light.disconnect()
        self.light_2.disconnect()

    def _camera_status(self, status: CameraStatus) -> None:
        self._camera_status_data = status
        self.camera_card.set_status(status)
        self._refresh_acquisition_preflight()
        fps = "–" if status.camera_fps is None else f"{status.camera_fps:.1f}"
        self.camera_card.details.setText(
            f"Modell: {status.model}\n"
            f"Seriennummer: {status.serial_number}\n"
            f"IP: {status.ip_address}\n"
            f"Bild: {status.width} × {status.height} · {status.pixel_format}\n"
            f"Kamera/Rohabruf/Vorschau: {fps} / {status.stream_fps:.1f} / "
            f"{status.preview_fps:.1f} FPS"
            f"\nBelichtung: "
            f"{'–' if status.exposure_time_us is None else f'{status.exposure_time_us:.0f} µs'}"
            f" · Auto: {status.exposure_auto}"
        )
        if (
            status.serial_number not in ("", "–")
            and status.serial_number != self.config.camera_serial
        ):
            self.config.camera_serial = status.serial_number
            self.settings_store.save(self.config)

    def _camera_frame(self, frame: CameraFrame) -> None:
        image = frame.image
        height, width, channels = image.shape
        if channels != 3:
            return
        raw_image = QImage(
            image.data,
            width,
            height,
            int(image.strides[0]),
            QImage.Format.Format_RGB888,
        ).copy()
        self._last_raw_image = raw_image
        worker = self._inference_worker
        if worker is not None and worker.isRunning():
            if not self.validation.running:
                worker.submit(image, frame.timestamp)
            if not self._inference_has_result:
                self._last_image = raw_image
                self._render_image()
        else:
            self._last_image = raw_image
            self._render_image()

    def _current_inference_config(self) -> LiveInferenceConfig:
        return LiveInferenceConfig(
            model_path=self.inference_config.model_path,
            confidence=self.inference_confidence.value(),
            image_size=self.inference_config.image_size,
            max_fps=float(self.inference_max_fps.value()),
            device=self.inference_config.device,
            duplicate_filter=self.inference_duplicate_filter.isChecked(),
            duplicate_iou=self.inference_duplicate_iou.value(),
        )

    def _choose_inference_model(self) -> None:
        if self.validation.running:
            return
        current = self.inference_config.model_path
        start_directory = str(current.parent if current.is_file() else current)
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "YOLO-OBB-Modell auswählen",
            start_directory,
            "PyTorch-Modell (*.pt)",
        )
        if not filename:
            return
        if self.validation.has_results:
            answer = QMessageBox.question(
                self,
                "Neue Validierungssitzung",
                "Für das neue Modell die Ergebnisliste "
                "zurücksetzen? Die lokale Sitzungssicherung und Exporte bleiben erhalten.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        if self.validation.store is not None:
            self.validation.reset()
        self.validation.model = None
        self._refresh_validation()
        was_running = self.inference_toggle.isChecked()
        if was_running:
            self.inference_toggle.setChecked(False)
        self.inference_config = replace(self._current_inference_config(), model_path=Path(filename))
        self.inference_model.setText(filename)
        self.inference_model.setToolTip(filename)
        self.settings_store.save_live_inference(self.inference_config)
        self._append_event(f"Live-Modell ausgewählt: {filename}")
        if was_running:
            self.inference_toggle.setChecked(True)

    def _toggle_live_inference(self, enabled: bool) -> None:
        if not enabled:
            self._stop_live_inference()
            self.inference_status.setText("Aus")
            self._inference_has_result = False
            if self._last_raw_image is not None:
                self._last_image = self._last_raw_image
                self._render_image()
            return
        try:
            config = self._current_inference_config().validated()
        except ValueError as exc:
            self.inference_toggle.blockSignals(True)
            self.inference_toggle.setChecked(False)
            self.inference_toggle.blockSignals(False)
            self.inference_status.setText("Kein Modell")
            QMessageBox.warning(self, "Live-Erkennung", str(exc))
            return

        self.inference_config = config
        self.settings_store.save_live_inference(config)
        self._inference_has_result = False
        worker = LiveInferenceWorker(config, self)
        worker.model_loaded.connect(
            lambda info, source=worker: (
                self.validation.set_model(info) if source is self._inference_worker else None
            )
        )
        worker.frame_ready.connect(
            lambda frame, source=worker: self._inference_frame_from(source, frame)
        )
        worker.status_changed.connect(
            lambda message, source=worker: self._inference_status_changed(source, message)
        )
        worker.error.connect(
            lambda message, source=worker: self._inference_error_from(source, message)
        )
        worker.finished.connect(lambda source=worker: self._inference_finished(source))
        self._inference_worker = worker
        worker.start()
        self._append_event(
            f"YOLO-Live-Erkennung gestartet ({config.model_path.name}, "
            f"Konfidenz {config.confidence:.2f}, max. {config.max_fps:g} FPS)."
        )

    def _apply_inference_runtime_settings(self) -> None:
        try:
            config = self._current_inference_config().validated(require_model=False)
        except ValueError as exc:
            self._show_error("Live-Erkennung", str(exc))
            return
        self.inference_config = config
        self.settings_store.save_live_inference(config)
        if self._inference_worker is not None:
            self._inference_worker.update_runtime_settings(
                config.confidence,
                config.max_fps,
                duplicate_filter=config.duplicate_filter,
                duplicate_iou=config.duplicate_iou,
            )
        self.inference_duplicate_iou.setEnabled(
            config.duplicate_filter and not self.validation.running
        )

    def _inference_frame(self, frame: InferenceFrame) -> None:
        started = time.perf_counter()
        self.validation.on_frame(frame)
        image = frame.image
        height, width, channels = image.shape
        if channels != 3:
            return
        self._last_image = QImage(
            image.data,
            width,
            height,
            int(image.strides[0]),
            QImage.Format.Format_RGB888,
        ).copy()
        self._inference_has_result = True
        count = len(frame.detections)
        detection_text = "1 Objekt" if count == 1 else f"{count} Objekte"
        self.inference_status.setText(f"{detection_text} · {frame.inference_ms:.0f} ms")
        if frame.timings:
            self.inference_status.setToolTip(
                f"Bildzulauf: {frame.timings.get('input_fps', 0):.1f} FPS\n"
                f"Farbumwandlung: {frame.timings.get('convert_ms', 0):.1f} ms\n"
                f"YOLO-Aufruf: {frame.timings.get('predict_ms', 0):.1f} ms\n"
                f"Modellberechnung: {frame.timings.get('backend_inference_ms', 0):.1f} ms"
            )
        self._render_image()
        if self.validation.running:
            self.validation.performance["last_gui_ms"] = (time.perf_counter() - started) * 1000

    def _inference_frame_from(
        self,
        worker: LiveInferenceWorker,
        frame: InferenceFrame,
    ) -> None:
        if worker is self._inference_worker:
            self._inference_frame(frame)

    def _inference_status_changed(
        self,
        worker: LiveInferenceWorker,
        message: str,
    ) -> None:
        if worker is self._inference_worker:
            self.inference_status.setText(message)

    def _inference_error(self, message: str) -> None:
        self.validation.stop(message)
        self._show_error("Live-Erkennung", message)
        self._append_event(message)

    def _inference_error_from(self, worker: LiveInferenceWorker, message: str) -> None:
        if worker is self._inference_worker:
            self._inference_error(message)

    def _inference_finished(self, worker: LiveInferenceWorker) -> None:
        if worker is not self._inference_worker:
            return
        self.validation.stop("YOLO-Inferenz wurde beendet.")
        self._inference_worker = None
        if self.inference_toggle.isChecked():
            self.inference_toggle.blockSignals(True)
            self.inference_toggle.setChecked(False)
            self.inference_toggle.blockSignals(False)
        self._inference_has_result = False
        if self._last_raw_image is not None:
            self._last_image = self._last_raw_image
            self._render_image()

    def _stop_live_inference(self) -> None:
        worker = self._inference_worker
        if worker is None:
            return
        self._inference_worker = None
        if not worker.stop():
            self._logger.warning("YOLO-Inferenz-Thread reagierte nicht innerhalb des Timeouts.")
        worker.deleteLater()

    def _render_image(self) -> None:
        if self._last_image is None:
            return
        self.preview.source_size = (self._last_image.width(), self._last_image.height())
        pixmap = QPixmap.fromImage(self._last_image).scaled(
            self.preview.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.preview.setPixmap(pixmap)

    def resizeEvent(self, event: object) -> None:  # noqa: N802
        super().resizeEvent(event)  # type: ignore[arg-type]
        self._render_image()

    def _conveyor_status(self, status: ConveyorStatus) -> None:
        self.conveyor_card.set_status(status)
        self.acquisition_card.set_conveyor_direction(
            status.forward_direction, self.acquisition_config.conveyor_enabled
        )
        self._refresh_acquisition_preflight()

    def _jog_conveyor(self, direction: str, distance_mm: float) -> None:
        self.conveyor.jog(direction, distance_mm, 10.0)

    def _set_conveyor_forward_direction(self, direction: str) -> None:
        if self.acquisition.running or self.validation.running:
            self.conveyor_card.set_status(self.conveyor.status)
            return
        if not self.conveyor.set_forward_direction(direction):
            self.conveyor_card.set_status(self.conveyor.status)
            return
        self.config.conveyor_forward_direction = direction
        self.settings_store.save(self.config)
        self._refresh_acquisition_preflight()
        if direction:
            self._append_event(
                f"Förderband-Vorwärtsrichtung gespeichert: "
                f"{'links' if direction == 'left' else 'rechts'}."
            )

    def _align_conveyor_for_resume(self) -> None:
        expected = self.acquisition.expected_resume_offset_mm
        if expected is None:
            return
        self.acquisition.align_for_resume()

    def _light_state(self, state: ConnectionState) -> None:
        self.light_card.set_connection_state(state)

    def _light_2_state(self, state: ConnectionState) -> None:
        self.light_2_card.set_connection_state(state)

    def _light_status(self, status: LightStatus) -> None:
        self._set_light_status(1, status)
        self._refresh_acquisition_preflight()

    def _light_2_status(self, status: LightStatus) -> None:
        self._set_light_status(2, status)
        self._refresh_acquisition_preflight()

    def _set_light_status(self, number: int, status: LightStatus) -> None:
        card = self.light_card if number == 1 else self.light_2_card
        card.set_status(status)
        address_attribute = "light_address" if number == 1 else "light_2_address"
        name_attribute = "light_name" if number == 1 else "light_2_name"
        if status.address not in ("", "–") and status.address != getattr(
            self.config, address_attribute
        ):
            setattr(self.config, address_attribute, status.address)
            setattr(self.config, name_attribute, status.name)
            self.settings_store.save(self.config)

    def open_settings(self) -> None:
        if self.validation.running:
            self._show_error("Einstellungen", "Bitte zuerst die Validation stoppen.")
            return
        dialog = SettingsDialog(self.config, self)
        if dialog.exec() != SettingsDialog.DialogCode.Accepted or dialog.result_config is None:
            return
        new_config = replace(
            dialog.result_config,
            camera_serial=self.config.camera_serial,
        )
        self.config = new_config
        self.settings_store.save(self.config)
        self.camera.config = self.config
        self.conveyor.config = self.config
        self.light.config = self.config
        self.light_2.config = self.config
        self.statusBar().showMessage(f"Kamera {self.config.camera_ip} · SPS {self.config.plc_ip}")
        self._append_event("Einstellungen gespeichert; sie gelten ab der nächsten Verbindung.")
        self._refresh_acquisition_preflight()

    def open_labeling(self) -> None:
        dialog = LabelingDialog(self.settings_store, self)
        dialog.exec()

    def open_training(self) -> None:
        if self._training_dialog is None:
            self._training_dialog = TrainingDialog(self.settings_store, self)
            self._training_dialog.finished.connect(self._training_dialog_closed)
        self._training_dialog.show()
        self._training_dialog.raise_()
        self._training_dialog.activateWindow()

    def open_cleanup(self) -> None:
        dialog = CleanupDialog(
            self.settings_store,
            acquisition_running=lambda: self.acquisition.running,
            parent=self,
        )
        dialog.exec()

    def _training_dialog_closed(self) -> None:
        if self._training_dialog is not None:
            self._training_dialog.deleteLater()
        self._training_dialog = None

    def open_acquisition_settings(self) -> None:
        if self.acquisition.running:
            return
        dialog = AcquisitionDialog(
            self.acquisition_config,
            self._camera_status_data,
            self,
        )
        if dialog.exec() != AcquisitionDialog.DialogCode.Accepted:
            return
        if dialog.result_config is None:
            return
        self.acquisition_config = dialog.result_config
        self.settings_store.save_acquisition(self.acquisition_config)
        self.acquisition_card.set_settings(self.acquisition_config)
        self.acquisition_card.set_conveyor_direction(
            self.config.conveyor_forward_direction, self.acquisition_config.conveyor_enabled
        )
        self._refresh_acquisition_preflight()
        self._append_event("Einstellungen für die automatische Aufnahme gespeichert.")

    def start_acquisition(self) -> None:
        if self.validation.running:
            self._show_error("Aufnahme", "Bitte zuerst die Validation stoppen.")
            return
        checks = self.acquisition.preflight_checks(self.acquisition_config)
        self.acquisition_card.set_preflight(checks)
        failed = [check for check in checks if not check.ready]
        if failed:
            self._acquisition_status(
                "Start blockiert: "
                + "; ".join(f"{check.label}: {check.detail}" for check in failed)
            )
            return
        count = len(build_capture_points(self.acquisition_config))
        movement = (
            " und verfährt das Förderband automatisch"
            if self.acquisition_config.conveyor_enabled
            else ""
        )
        interrupted = (
            "\n\nEine unterbrochene Sitzung ist vorhanden. Beim Start einer neuen Sitzung "
            "wird deren Fortsetzung verworfen."
            if self.acquisition.resume_available
            else ""
        )
        answer = QMessageBox.question(
            self,
            "Automatische Aufnahme starten",
            f"Die Sitzung umfasst {count} Aufnahmen{movement}.\n\n"
            f"Ist der Arbeitsraum frei und darf die Sequenz gestartet werden?{interrupted}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer is QMessageBox.StandardButton.Yes:
            self.acquisition.start(self.acquisition_config)

    def resume_acquisition(self) -> None:
        if self.validation.running:
            self._show_error("Aufnahme", "Bitte zuerst die Validation stoppen.")
            return
        if not self.acquisition.resume_available:
            return
        remaining = self.acquisition.remaining_count
        session = self.acquisition.session_directory or "–"
        answer = QMessageBox.question(
            self,
            "Automatische Aufnahme fortsetzen",
            f"Es fehlen noch {remaining} Aufnahmen in:\n{session}\n\n"
            "Die Hardware wird erneut geprüft; bei aktiver Bandserie fährt das Förderband auf "
            "das nächste benötigte Ziel. "
            "Ist der Arbeitsraum frei und wurde die Fehlerursache behoben?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer is QMessageBox.StandardButton.Yes:
            self.acquisition.resume()

    def _acquisition_status(self, message: str) -> None:
        self.acquisition_card.status.setText(message)
        self._append_event(f"Aufnahme: {message}")

    def _acquisition_running(self, running: bool) -> None:
        self.validation_panel.start.setEnabled(not running and not self.validation.running)
        for card in (
            self.camera_card,
            self.conveyor_card,
            self.light_card,
            self.light_2_card,
        ):
            card.setEnabled(not running)
        if not running:
            self._refresh_acquisition_preflight()

    def _refresh_acquisition_preflight(self, *_: object) -> None:
        if self.acquisition.running:
            return
        checks = self.acquisition.preflight_checks(
            self.acquisition_config,
            resuming=self.acquisition.resume_available,
        )
        self.acquisition_card.set_preflight(checks)

    def _validation_tab_changed(self, index: int) -> None:
        self.preview.validation_visible = index == 1
        self.preview.update()

    def _validation_line(self, value: float) -> None:
        if not self.validation.running:
            self.preview.line_fraction = value
            self.preview.update()

    def _validation_entry(self, value: float) -> None:
        if not self.validation.running:
            self.preview.entry_fraction = value
            self.preview.update()

    def _refresh_validation(self) -> None:
        self.validation_panel.refresh(self.validation)
        self.validation_panel.start.setEnabled(
            not self.validation.running and not self.acquisition.running
        )
        self.preview.tracks = (
            [t for t in self.validation.tracker.tracks.values() if t.entered]
            if self.validation.tracker else []
        )
        self.preview.update()
        self.validation_banner.setText(
            f"Validation: {self.validation.state} · {self.validation.fps:.1f} FPS"
            if self.validation.running
            else ""
        )

    def _validation_running(self, running: bool) -> None:
        # Bypass the GUI event queue: submit only retains the newest raw frame.
        self.camera.set_capture_frame_consumer(self._validation_camera_frame if running else None)
        self.validation_panel.set_busy(running)
        self.validation_stop.setVisible(running)
        self.preview.line_editable = not running
        for widget in (
            self.choose_model,
            self.load_model,
            self.inference_toggle,
            self.inference_confidence,
            self.inference_max_fps,
            self.inference_duplicate_filter,
            self.inference_duplicate_iou,
            self.acquisition_card,
            self.conveyor_card,
            self.camera_card,
        ):
            widget.setEnabled(not running)
        self.inference_duplicate_iou.setEnabled(
            not running and self.inference_duplicate_filter.isChecked()
        )
        if not running:
            self._refresh_acquisition_preflight()

    def start_validation(self) -> None:
        if self.acquisition.running:
            self._show_error("Validation", "Bitte zuerst die automatische Aufnahme stoppen.")
            return
        try:
            settings = self.validation_panel.settings()
            if not self.validation_panel.output.text().strip():
                raise ValueError("Bitte einen Ordner für die Sitzungssicherung wählen.")
            root = Path(self.validation_panel.output.text()).expanduser()
            config = self._current_inference_config().validated()
            if self.validation.start(settings, root, config):
                self.settings_store.save_validation(settings, root)
                if self._inference_worker is None:
                    self.inference_toggle.setChecked(True)
        except (ValueError, OSError) as exc:
            self._show_error("Validation", str(exc))

    def _validation_camera_frame(self, frame: CameraFrame) -> None:
        worker = self._inference_worker
        if self.validation.running and worker is not None and worker.isRunning():
            worker.submit(frame.image, frame.timestamp)

    def _validation_speed(self, speed: float) -> None:
        try:
            if not self.validation.set_speed(speed):
                self._show_error("Validation", "Geschwindigkeit konnte nicht übernommen werden.")
        except (ValueError, RuntimeError) as exc:
            self.validation.stop(str(exc))

    def _export_validation(self, directory: str) -> None:
        try:
            self.validation.export(Path(directory))
        except (ValueError, RuntimeError) as exc:
            self._show_error("Validation", str(exc))

    def _show_error(self, device: str, message: str) -> None:
        self.statusBar().showMessage(f"{device}: {message}", 10000)
        self._logger.error("%s: %s", device, message)

    def _append_event(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.event_log.appendPlainText(f"[{timestamp}] {message}")

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self._closing:
            event.accept()
            return
        self._closing = True
        self.validation.close()
        self.light_card.command_timer.stop()
        self.light_2_card.command_timer.stop()
        self.acquisition.stop()
        self._stop_live_inference()
        if self._training_dialog is not None:
            self._training_dialog.shutdown()
        self.camera.disconnect()
        self.conveyor.disconnect()
        event.accept()

    async def shutdown_async(self) -> None:
        self.validation.close()
        self._stop_live_inference()
        if self._training_dialog is not None:
            self._training_dialog.shutdown()
        self.acquisition.close()
        self.camera.disconnect()
        self.conveyor.disconnect()
        await self.light.shutdown()
        await self.light_2.shutdown()
