from __future__ import annotations

from pathlib import Path

import numpy as np
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


def test_validation_shows_single_failed_entry_and_failure_totals(qtbot, tmp_path):
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
    window.validation.reset()
    assert panel.results.rowCount() == 0
    assert "Abgeschlossen: 0" in panel.summary.text()


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


def test_validation_blocks_acquisition_and_keeps_stop_visible_on_tab_switch(qtbot, tmp_path):
    window = make_window(qtbot, tmp_path)
    window.validation.state = "warming"
    window._validation_running(True)
    assert window.camera._capture_consumer == window._validation_camera_frame
    window.tabs.setCurrentIndex(0)
    assert not window.validation_stop.isHidden()
    assert not window.choose_model.isEnabled()
    assert not window.validation_panel.line.isEnabled()
    window.start_acquisition()
    assert not window.acquisition.running
    window.validation.state = "idle"
    window._validation_running(False)
    assert window.camera._capture_consumer is None


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
