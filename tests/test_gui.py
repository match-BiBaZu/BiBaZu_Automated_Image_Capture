from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PyQt6.QtCore import QPoint, QSettings, Qt
from PyQt6.QtTest import QSignalSpy
from PyQt6.QtWidgets import QPushButton

from automated_image_capture.acquisition import AcquisitionSettings, PreflightCheck
from automated_image_capture.inference import InferenceDetection, InferenceFrame
from automated_image_capture.models import (
    CameraStatus,
    ConnectionState,
    ConveyorStatus,
    LightStatus,
)
from automated_image_capture.settings import SettingsStore
from automated_image_capture.ui.main_window import MainWindow
from automated_image_capture.ui.widgets import AcquisitionDialog


def make_window(qtbot, tmp_path) -> MainWindow:
    backend = QSettings(str(tmp_path / "gui.ini"), QSettings.Format.IniFormat)
    window = MainWindow(SettingsStore(backend))
    qtbot.addWidget(window)
    window.show()
    return window


def test_dashboard_renders_status_without_hardware(qtbot, tmp_path) -> None:
    window = make_window(qtbot, tmp_path)

    window._camera_status(
        CameraStatus(
            model="TestCam",
            serial_number="42",
            ip_address="169.254.117.70",
            width=1920,
            height=1200,
            pixel_format="Mono8",
            camera_fps=30.0,
            stream_fps=25.0,
            preview_fps=15.0,
        )
    )
    window._light_status(
        LightStatus(
            name="RGB660",
            address="AA:BB",
            connected=True,
            brightness=35,
            values_are_confirmed_commands=True,
        )
    )
    window._light_2_status(
        LightStatus(
            name="RGB660-2",
            address="CC:DD",
            connected=True,
            brightness=70,
            values_are_confirmed_commands=True,
        )
    )

    assert "TestCam" in window.camera_card.details.text()
    assert "30.0 / 25.0 / 15.0 FPS" in window.camera_card.details.text()
    assert "35 %" in window.light_card.details.text()
    assert "70 %" in window.light_2_card.details.text()
    assert window.config.camera_serial == "42"
    assert window.config.light_address == "AA:BB"
    assert window.config.light_2_address == "CC:DD"
    assert not hasattr(window, "robot")
    assert not hasattr(window, "robot_card")


def test_dashboard_exposes_storage_cleanup_dialog(qtbot, tmp_path) -> None:
    window = make_window(qtbot, tmp_path)

    labels = {button.text() for button in window.findChildren(QPushButton)}

    assert "Speicher bereinigen …" in labels


def test_validation_tab_shares_preview_and_shows_all_model_classes(qtbot, tmp_path):
    from automated_image_capture.inference import InferenceModelInfo

    window = make_window(qtbot, tmp_path)
    assert [window.tabs.tabText(i) for i in range(window.tabs.count())] == [
        "Aufnahme",
        "YOLO Validation",
    ]
    window.validation.set_model(InferenceModelInfo("model.pt", "hash", "obb", {0: "A", 1: "B"}))
    window.tabs.setCurrentIndex(1)
    assert window.preview.validation_visible
    assert window.validation_panel.counts.item(1, 0).text() == "1: B"
    assert window.validation_panel.counts.item(1, 1).text() == "0"
    window.validation_panel.line.setValue(12)
    assert window.preview.line_fraction == 0.12
    assert window.inference_max_fps.maximum() == 60


def test_live_duplicate_controls_apply_immediately_and_restore(qtbot, tmp_path):
    from types import SimpleNamespace

    window = make_window(qtbot, tmp_path)
    assert window.inference_duplicate_filter.isChecked()
    assert window.inference_duplicate_iou.value() == 0.6
    updates = []
    window._inference_worker = SimpleNamespace(
        update_runtime_settings=lambda *args, **kwargs: updates.append((args, kwargs)),
        stop=lambda: True,
        deleteLater=lambda: None,
    )
    window.inference_duplicate_filter.setChecked(False)
    assert not window.inference_duplicate_iou.isEnabled()
    assert updates[-1][1] == {"duplicate_filter": False, "duplicate_iou": 0.6}
    window.inference_duplicate_filter.setChecked(True)
    window.inference_duplicate_iou.setValue(0.75)
    window.inference_duplicate_iou.editingFinished.emit()
    assert updates[-1][1] == {"duplicate_filter": True, "duplicate_iou": 0.75}
    restored = MainWindow(SettingsStore(
        QSettings(str(tmp_path / "gui.ini"), QSettings.Format.IniFormat)
    ))
    qtbot.addWidget(restored)
    assert restored.inference_duplicate_filter.isChecked()
    assert restored.inference_duplicate_iou.value() == 0.75
    window._inference_worker = None


def test_validation_shows_single_failed_entry_and_failure_totals(qtbot, tmp_path, monkeypatch):
    from types import SimpleNamespace

    from automated_image_capture.inference import InferenceModelInfo
    from automated_image_capture.validation import ValidationSettings, ValidationTracker

    window = make_window(qtbot, tmp_path)
    window.validation.set_model(InferenceModelInfo("model.pt", "hash", "obb", {0: "A"}))
    tracker = ValidationTracker(ValidationSettings(), {0: "A"})
    window.validation.tracker = tracker
    image = np.zeros((400, 1000, 3), np.uint8)
    entry = InferenceDetection(0, "A", 0.9, ((960, 100), (1010, 100), (1010, 150), (960, 150)))
    tracker.update(image, (entry,), 1)
    tracker.update(image, (), 1.6)
    window._refresh_validation()
    panel = window.validation_panel
    assert panel.results.rowCount() == 1
    assert panel.results.item(0, 5).text() == "Unvollständig"
    assert "nur eine Erkennung" in panel.results.item(0, 6).text()
    assert "Abgeschlossen: 1" in panel.summary.text()
    assert "Erkannt: 0" in panel.summary.text()
    assert "Unvollständig: 1" in panel.summary.text()
    assert panel.results.item(0, 7).text() == "Öffnen"
    record = tracker.results[0]
    first_path = tmp_path / record.first_image
    first_path.parent.mkdir()
    first_path.touch()
    panel._controller = SimpleNamespace(
        tracker=tracker, store=SimpleNamespace(directory=tmp_path)
    )
    opened = []
    monkeypatch.setattr(
        "automated_image_capture.ui.validation_panel.QDesktopServices.openUrl",
        lambda url: opened.append(Path(url.toLocalFile())),
    )
    panel._open_image(0, 0)
    panel._open_image(0, 7)
    assert opened == [first_path, first_path]
    window.validation.reset()
    assert panel.results.rowCount() == 0
    assert "Abgeschlossen: 0" in panel.summary.text()


def test_validation_first_image_column_opens_entry_instead_of_best_evidence(
    qtbot, tmp_path, monkeypatch
):
    from types import SimpleNamespace

    from automated_image_capture.ui.validation_panel import ValidationPanel

    panel = ValidationPanel()
    qtbot.addWidget(panel)
    first_path, best_path = tmp_path / "first.png", tmp_path / "best.png"
    first_path.touch()
    best_path.touch()
    panel._controller = SimpleNamespace(
        tracker=SimpleNamespace(
            results=[SimpleNamespace(image="best.png", first_image="first.png")]
        ),
        store=SimpleNamespace(directory=tmp_path),
    )
    opened = []
    monkeypatch.setattr(
        "automated_image_capture.ui.validation_panel.QDesktopServices.openUrl",
        lambda url: opened.append(Path(url.toLocalFile())),
    )
    panel._open_image(0, 0)
    panel._open_image(0, 7)
    assert opened == [best_path, first_path]


def test_validation_counts_majority_pose_after_loss_and_shows_vote_numbers(qtbot, tmp_path):
    from automated_image_capture.inference import InferenceModelInfo
    from automated_image_capture.validation import ValidationSettings, ValidationTracker

    window = make_window(qtbot, tmp_path)
    window.validation.set_model(InferenceModelInfo("model.pt", "hash", "obb", {4: "Pose 4"}))
    tracker = ValidationTracker(ValidationSettings(), {4: "Pose 4"})
    window.validation.tracker = tracker
    image = np.zeros((400, 1000, 3), np.uint8)
    for step in range(12):
        x = 900 - 20 * step
        obb = InferenceDetection(
            4, "Pose 4", 0.95, ((x - 25, 100), (x + 25, 100), (x + 25, 150), (x - 25, 150))
        )
        tracker.update(image, (obb,), step * 0.1)
    tracker.update(image, (), 1.7)
    window._refresh_validation()
    panel = window.validation_panel
    assert panel.results.item(0, 2).text() == "Pose 4"
    assert panel.results.item(0, 3).text() == "100.00%"
    assert panel.results.item(0, 4).text() == "12 / 12"
    assert panel.results.item(0, 5).text() == "Erkannt"
    assert tracker.pose_counts() == {4: 1}
    assert "Erkannt: 1" in panel.summary.text()
    assert not tracker.results[0].pass_completed
    assert window.validation_panel.min_consensus.value() == 90


def test_validation_camera_uses_mono_capture_stream_independent_of_preview(qtbot, tmp_path):
    from types import SimpleNamespace

    from automated_image_capture.models import CameraFrame

    window = make_window(qtbot, tmp_path)
    submissions = []
    window._inference_worker = SimpleNamespace(
        isRunning=lambda: True,
        submit=lambda image, timestamp: submissions.append((image, timestamp)),
        stop=lambda: True,
        deleteLater=lambda: None,
    )
    window.validation.state = "warming"
    window._validation_camera_frame(CameraFrame(np.ones((12, 16), np.uint8), "Mono8", 123))
    assert submissions[0][0].shape == (12, 16)
    assert submissions[0][1] == 123
    window._camera_frame(CameraFrame(np.ones((12, 16, 3), np.uint8), "RGB8", 124))
    assert len(submissions) == 1
    window.validation.state = "idle"


def test_validation_line_mouse_editing_respects_image_letterboxing_and_run_lock(qtbot):
    from automated_image_capture.ui.validation_panel import ValidationPreview

    preview = ValidationPreview("")
    qtbot.addWidget(preview)
    preview.resize(400, 400)
    preview.source_size = (1000, 500)
    preview.validation_visible = True
    preview.show()
    changes = QSignalSpy(preview.line_changed)
    qtbot.mouseClick(preview, Qt.MouseButton.LeftButton, pos=QPoint(80, 200))
    assert len(changes) == 1 and changes[0][0] == 0.2
    qtbot.mouseClick(preview, Qt.MouseButton.LeftButton, pos=QPoint(80, 10))
    assert len(changes) == 1
    preview.line_editable = False
    qtbot.mouseClick(preview, Qt.MouseButton.LeftButton, pos=QPoint(120, 200))
    assert len(changes) == 1


def test_validation_lines_keep_order_and_sync_preview(qtbot, tmp_path):
    window = make_window(qtbot, tmp_path)
    panel = window.validation_panel
    panel.entry.setValue(40)
    panel.line.setValue(25)
    assert window.preview.entry_fraction == 0.4
    assert window.preview.line_fraction == 0.25
    assert panel.settings().entry_fraction == 0.4
    assert panel.settings().exit_fraction == 0.25
    panel.line.setValue(60)
    assert panel.line.value() == 39.99
    panel.entry.setValue(20)
    assert panel.entry.value() == 40
    panel.entry.setValue(90)
    panel.line.setValue(80)
    assert panel.settings().exit_fraction == 0.8
    panel.entry.setValue(100)
    assert panel.entry.value() == 99.99
    panel.line.setValue(100)
    assert panel.line.value() == 99.98
    assert panel.settings().exit_fraction < panel.settings().entry_fraction < 1


@pytest.mark.parametrize(("exit_fraction", "entry_fraction"), [(0.8, 0.9), (0, 0.02)])
def test_validation_restores_both_lines(qtbot, tmp_path, exit_fraction, entry_fraction):
    from automated_image_capture.validation import ValidationSettings

    store = SettingsStore(QSettings(str(tmp_path / "lines.ini"), QSettings.Format.IniFormat))
    config = ValidationSettings(exit_fraction=exit_fraction, entry_fraction=entry_fraction)
    store.save_validation(config, tmp_path)
    window = MainWindow(store)
    qtbot.addWidget(window)
    assert window.validation_panel.settings() == config
    assert window.preview.line_fraction == exit_fraction
    assert window.preview.entry_fraction == entry_fraction


def test_validation_drag_keeps_selected_line_and_prevents_crossing(qtbot, tmp_path):
    from PyQt6.QtCore import QEvent, QPointF
    from PyQt6.QtGui import QMouseEvent

    window = make_window(qtbot, tmp_path)
    window.tabs.setCurrentIndex(1)
    preview = window.preview
    preview.source_size = (1000, 500)

    def position(fraction):
        x, y, width, height = preview._image_rect()
        return QPoint(round(x + width * fraction), round(y + height / 2))

    def move(fraction):
        preview.mouseMoveEvent(
            QMouseEvent(
                QEvent.Type.MouseMove,
                QPointF(position(fraction)),
                Qt.MouseButton.NoButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            )
        )

    # Select blue, then drag past red. The selected line stays blue and is clamped.
    qtbot.mousePress(preview, Qt.MouseButton.LeftButton, pos=position(0.75))
    move(0)
    assert window.validation_panel.line.value() == 5
    assert window.validation_panel.entry.value() == 5.01
    qtbot.mouseRelease(preview, Qt.MouseButton.LeftButton, pos=position(0))
    move(0.9)
    assert window.validation_panel.entry.value() == 5.01
    # Select red and try to cross blue in the other direction.
    window.validation_panel.entry.setValue(90)
    qtbot.mousePress(preview, Qt.MouseButton.LeftButton, pos=position(0.05))
    move(1)
    assert window.validation_panel.line.value() == 89.99
    assert preview.line_fraction == pytest.approx(0.8999)
    assert preview.entry_fraction == 0.9
    qtbot.mouseRelease(preview, Qt.MouseButton.LeftButton, pos=position(1))


def test_validation_blocks_acquisition_and_keeps_stop_visible_on_tab_switch(qtbot, tmp_path):
    window = make_window(qtbot, tmp_path)
    window.validation.state = "warming"
    window._validation_running(True)
    assert window.camera._capture_consumer == window._validation_camera_frame
    window.tabs.setCurrentIndex(0)
    assert not window.validation_stop.isHidden()
    assert not window.choose_model.isEnabled()
    assert not window.validation_panel.line.isEnabled()
    assert not window.validation_panel.entry.isEnabled()
    assert not window.inference_duplicate_filter.isEnabled()
    assert not window.inference_duplicate_iou.isEnabled()
    window.start_acquisition()
    assert not window.acquisition.running
    window.validation.state = "idle"
    window._validation_running(False)
    assert window.camera._capture_consumer is None
    assert window.validation_panel.entry.isEnabled()
    assert window.inference_duplicate_filter.isEnabled()
    assert window.inference_duplicate_iou.isEnabled()
    window.inference_duplicate_filter.setChecked(False)
    window._validation_running(True)
    window._validation_running(False)
    assert not window.inference_duplicate_iou.isEnabled()


def test_light_controls_follow_connection_state(qtbot, tmp_path) -> None:
    window = make_window(qtbot, tmp_path)

    window._light_state(ConnectionState.DISCONNECTED)
    assert not window.light_brightness.isEnabled()

    window._light_state(ConnectionState.CONNECTED)
    assert window.light_brightness.isEnabled()

    window._light_2_state(ConnectionState.DISCONNECTED)
    assert not window.light_2_card.brightness.isEnabled()
    window._light_2_state(ConnectionState.CONNECTED)
    assert window.light_2_card.brightness.isEnabled()


def test_camera_exposure_control_follows_status_and_emits_request(qtbot, tmp_path) -> None:
    window = make_window(qtbot, tmp_path)
    spy = QSignalSpy(window.camera_card.exposure_requested)

    window.camera_card.set_state(ConnectionState.CONNECTED)
    window._camera_status(
        CameraStatus(
            exposure_time_us=4_000,
            exposure_min_us=100,
            exposure_max_us=100_000,
            exposure_writable=True,
            exposure_auto="Off",
        )
    )

    assert window.camera_card.exposure_time.isEnabled()
    assert window.camera_card.exposure_time.value() == 4_000
    window.camera_card.exposure_time.setValue(7_500)
    window.camera_card.apply_exposure_button.click()
    assert len(spy) == 1
    assert spy[0][0] == 7_500.0

    window._camera_status(CameraStatus(exposure_writable=True, exposure_auto="Continuous"))
    assert window.camera_card.exposure_time.isEnabled()
    assert "deaktiviert ExposureAuto" in window.camera_card.exposure_hint.text()

    window._camera_status(CameraStatus(exposure_writable=False, exposure_auto="Continuous"))
    assert not window.camera_card.exposure_time.isEnabled()
    assert "ExposureAuto" in window.camera_card.exposure_hint.text()


def test_device_failures_do_not_change_other_cards(qtbot, tmp_path) -> None:
    window = make_window(qtbot, tmp_path)

    window.camera_card.set_state(ConnectionState.ERROR)

    assert window.camera_card.state is ConnectionState.ERROR
    assert window.light_card.state is ConnectionState.DISCONNECTED
    assert window.light_2_card.state is ConnectionState.DISCONNECTED


def test_live_inference_result_updates_preview_and_status(qtbot, tmp_path) -> None:
    window = make_window(qtbot, tmp_path)
    frame = InferenceFrame(
        image=np.zeros((80, 120, 3), dtype=np.uint8),
        detections=(
            InferenceDetection(
                0,
                "Pose 1",
                0.95,
                ((10, 10), (30, 10), (30, 30), (10, 30)),
            ),
        ),
        inference_ms=24.6,
        timestamp=0.0,
    )

    window._inference_frame(frame)

    assert window._last_image is not None
    assert "1 Objekt" in window.inference_status.text()
    assert "25 ms" in window.inference_status.text()


def test_resume_button_is_only_enabled_for_interrupted_sequence(qtbot, tmp_path) -> None:
    window = make_window(qtbot, tmp_path)
    window.acquisition_card.set_preflight((PreflightCheck("ready", "Test", True, "bereit"),))

    assert not window.acquisition_card.resume_button.isEnabled()
    window.acquisition_card.set_resume_available(True)
    assert window.acquisition_card.resume_button.isEnabled()
    window.acquisition_card.set_running(True)
    assert not window.acquisition_card.resume_button.isEnabled()
    window.acquisition_card.set_running(False)
    assert window.acquisition_card.resume_button.isEnabled()
    window.acquisition_card.set_resume_available(False)
    assert not window.acquisition_card.resume_button.isEnabled()


def test_acquisition_card_lists_failed_preflight_checks(qtbot, tmp_path) -> None:
    window = make_window(qtbot, tmp_path)

    window.acquisition_card.set_preflight(
        (
            PreflightCheck("camera", "Kamera", True, "verbunden"),
            PreflightCheck(
                "conveyor_origin",
                "Förderband-Nullpunkt",
                False,
                "Aktuelle Position als 0 mm übernehmen",
            ),
        )
    )

    assert not window.acquisition_card.start_button.isEnabled()
    assert "Start blockiert" in window.acquisition_card.preflight.text()
    assert "Förderband-Nullpunkt" in window.acquisition_card.preflight.text()
    assert "0 mm übernehmen" in window.acquisition_card.preflight.text()


def test_acquisition_dialog_switches_to_persistable_ramp_settings(qtbot, tmp_path) -> None:
    dialog = AcquisitionDialog(
        AcquisitionSettings(output_directory=Path(tmp_path)),
        CameraStatus(exposure_writable=True),
    )
    qtbot.addWidget(dialog)

    dialog.capture_mode.setCurrentIndex(dialog.capture_mode.findData("ramp"))
    dialog.ramp_duration.setValue(12.5)
    dialog.ramp_rate.setValue(8)
    dialog.ramp_light_1_period.setValue(1.6)
    dialog.ramp_light_2_period.setValue(12.5)
    config = dialog._current_config().validated()

    assert dialog.ramp_group.isVisibleTo(dialog)
    assert not dialog.light_1_row.isVisibleTo(dialog)
    assert config.capture_mode == "ramp"
    assert config.ramp_duration_s == 12.5
    assert config.ramp_image_rate_fps == 8
    assert "100" in dialog.estimate.text()


def test_acquisition_dialog_configures_continuous_angles_and_conveyor(qtbot, tmp_path) -> None:
    dialog = AcquisitionDialog(AcquisitionSettings(output_directory=Path(tmp_path)))
    qtbot.addWidget(dialog)

    dialog.conveyor_enabled.setChecked(True)
    dialog.conveyor_motion_mode.setCurrentIndex(
        dialog.conveyor_motion_mode.findData("synchronized")
    )
    dialog.conveyor_max_offset.setValue(50.0)
    dialog.conveyor_step.setValue(10.0)
    config = dialog._current_config().validated()

    assert config.conveyor_enabled
    assert config.conveyor_motion_mode == "synchronized"
    assert config.conveyor_max_offset_mm == 50.0
    assert config.conveyor_step_mm == 10.0
    assert dialog.ramp_group.isVisibleTo(dialog)
    assert not dialog.conveyor_step.isEnabled()
    assert "pro Bandlauf" in dialog.estimate.text()


def test_conveyor_card_shows_calibration_and_enables_manual_controls(qtbot, tmp_path) -> None:
    window = make_window(qtbot, tmp_path)
    jog_spy = QSignalSpy(window.conveyor_card.jog_requested)
    window.conveyor_card.set_state(ConnectionState.CONNECTED)
    window._conveyor_status(
        ConveyorStatus(
            connected=True,
            calibration_valid=True,
            mm_per_full_step=0.32960026,
            ready_to_execute=True,
            internal_position=1234,
            logical_offset_mm=0.0,
        )
    )

    assert "gültig" in window.conveyor_card.details.text()
    assert window.conveyor_card.left_button.isEnabled()
    assert window.conveyor_card.right_button.isEnabled()
    assert window.conveyor_card.jog_distance.isEnabled()
    assert window.conveyor_card.origin_button.isEnabled()

    window.conveyor_card.jog_distance.setValue(12.5)
    window.conveyor_card.right_button.click()

    assert len(jog_spy) == 1
    assert jog_spy[0][0] == "right"
    assert jog_spy[0][1] == 12.5


def test_manual_conveyor_jog_starts_without_confirmation(qtbot, tmp_path, monkeypatch) -> None:
    window = make_window(qtbot, tmp_path)
    requests: list[tuple[str, float, float]] = []
    monkeypatch.setattr(
        window.conveyor,
        "jog",
        lambda direction, distance, speed: requests.append((direction, distance, speed)),
    )

    window._jog_conveyor("left", 37.5)

    assert requests == [("left", 37.5, 10.0)]


@pytest.mark.parametrize(("direction", "label", "back"), [
    ("left", "Links", "Rechts"), ("right", "Rechts", "Links"),
])
def test_capture_direction_updates_all_controls_and_persists(
    qtbot, tmp_path, direction, label, back
):
    from dataclasses import replace

    window = make_window(qtbot, tmp_path)
    window.acquisition_config = replace(window.acquisition_config, conveyor_enabled=True)
    dropdown = window.conveyor_card.forward_direction
    dropdown.setCurrentIndex(dropdown.findData(direction))
    assert window.config.conveyor_forward_direction == direction
    assert window.conveyor.status.forward_direction == direction
    assert dropdown.currentData() == direction
    assert f"Aufnahme-Hinfahrt: {label}" in window.conveyor_card.details.text()
    assert window.acquisition_card.conveyor_direction.text() == (
        f"Band-Hinfahrt: {label} · Rückfahrt: {back}"
    )
    assert not window.acquisition_card.conveyor_direction.isHidden()
    assert window.settings_store.load().conveyor_forward_direction == direction
    # A status refresh must update the dropdown without emitting another edit.
    changes = QSignalSpy(window.conveyor_card.forward_direction_changed)
    window.conveyor._on_status(ConveyorStatus(internal_position=1234))
    assert dropdown.currentData() == direction and not changes
    restored = MainWindow(SettingsStore(
        QSettings(str(tmp_path / "gui.ini"), QSettings.Format.IniFormat)
    ))
    qtbot.addWidget(restored)
    assert restored.conveyor_card.forward_direction.currentData() == direction
    # Clearing the selection must clear the backend and the displayed mapping too.
    dropdown.setCurrentIndex(0)
    assert window.conveyor.status.forward_direction == ""
    assert window.settings_store.load().conveyor_forward_direction == ""
    assert "bitte" in window.acquisition_card.conveyor_direction.text()


@pytest.mark.parametrize(("field", "value"), [
    ("busy", True), ("preparing_drive", True), ("requested_sequence", 7),
    ("continuous_requested", True), ("continuous_running", True),
])
def test_capture_direction_dropdown_is_locked_during_pending_motion(qtbot, field, value):
    from automated_image_capture.ui.widgets import ConveyorControlCard

    card = ConveyorControlCard("Band", "right")
    qtbot.addWidget(card)
    status = ConveyorStatus(forward_direction="right")
    setattr(status, field, value)
    card.set_status(status)
    assert not card.forward_direction.isEnabled()
    assert card.forward_direction.currentData() == "right"


def test_conveyor_can_recover_from_historical_error_status_without_active_fault(qtbot):
    from automated_image_capture.ui.widgets import ConveyorControlCard

    card = ConveyorControlCard("Band", "right")
    qtbot.addWidget(card)
    card.set_state(ConnectionState.CONNECTED)
    card.set_status(ConveyorStatus(
        connected=True, velocity_only=True, capture_interface_version=1,
        calibration_valid=True, mm_per_full_step=0.02454369,
        ready_to_execute=False, status_code=5, internal_position=1459326308,
    ))
    origin = QSignalSpy(card.origin_requested)
    jog = QSignalSpy(card.jog_requested)
    assert card.origin_button.isEnabled()
    assert card.left_button.isEnabled() and card.right_button.isEnabled()
    card.origin_button.click()
    assert len(origin) == 1 and len(jog) == 0
    card.right_button.click()
    assert len(jog) == 1 and jog[0] == ["right", 1.0]


@pytest.mark.parametrize(("field", "value"), [
    ("error", True), ("servo_fault_code", 12), ("busy", True),
    ("preparing_drive", True), ("requested_sequence", 7),
    ("continuous_requested", True), ("continuous_running", True),
])
def test_conveyor_blocks_controls_for_active_fault_or_motion(qtbot, field, value):
    from automated_image_capture.ui.widgets import ConveyorControlCard

    card = ConveyorControlCard("Band", "right")
    qtbot.addWidget(card)
    card.set_state(ConnectionState.CONNECTED)
    status = ConveyorStatus(
        connected=True, velocity_only=True, capture_interface_version=1,
        calibration_valid=True, mm_per_full_step=0.02454369,
        status_code=5, internal_position=1234,
    )
    setattr(status, field, value)
    card.set_status(status)
    assert not card.origin_button.isEnabled()
    assert not card.left_button.isEnabled() and not card.right_button.isEnabled()
    assert card.stop_button.isEnabled()


def test_conveyor_zero_does_not_require_drive_calibration_or_ready(qtbot):
    from automated_image_capture.ui.widgets import ConveyorControlCard

    card = ConveyorControlCard("Band", "right")
    qtbot.addWidget(card)
    card.set_state(ConnectionState.CONNECTED)
    card.set_status(ConveyorStatus(internal_position=0, status_code=5))
    assert card.origin_button.isEnabled()
    assert not card.left_button.isEnabled() and not card.right_button.isEnabled()
    card.set_status(ConveyorStatus(internal_position=None))
    assert not card.origin_button.isEnabled()
    card.set_status(ConveyorStatus(internal_position=1234))
    card.set_state(ConnectionState.DISCONNECTED)
    assert not card.origin_button.isEnabled()
