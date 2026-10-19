from __future__ import annotations

import csv
import json
import time
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest
from PyQt6.QtCore import QObject, pyqtSignal

from automated_image_capture.inference import (
    InferenceDetection,
    InferenceFrame,
    InferenceModelInfo,
    LiveInferenceConfig,
)
from automated_image_capture.models import ConnectionState, ConveyorStatus
from automated_image_capture.validation import ValidationSettings, ValidationTracker
from automated_image_capture.validation_controller import ValidationController
from automated_image_capture.validation_store import ValidationSessionStore

IMAGE = np.full((400, 1000, 3), 100, np.uint8)
CLASSES = {0: "Pose A", 1: "Pose B", 2: "Pose C"}


def detection(x, cid=0, y=120, confidence=0.9, half_width=25):
    return InferenceDetection(
        cid,
        CLASSES[cid],
        confidence,
        (
            (x - half_width, y - 20),
            (x + half_width, y - 20),
            (x + half_width, y + 20),
            (x - half_width, y + 20),
        ),
    )


def test_multiple_parts_keep_identity_despite_class_and_detection_order_changes():
    tracker = ValidationTracker(ValidationSettings(), CLASSES)
    for step in range(19):
        detections = []
        if step <= 14:
            detections.append(detection(900 - 60 * step, 1 if step == 3 else 0))
        if 4 <= step <= 18:
            detections.append(detection(900 - 60 * (step - 4), 1))
        tracker.update(
            IMAGE, tuple(reversed(detections)) if step % 2 else tuple(detections), step * 0.1
        )
    assert len(tracker.results) == 2
    assert [(r.track_id, r.class_id, r.status) for r in tracker.results] == [
        (1, 0, "Erkannt"),
        (2, 1, "Erkannt"),
    ]
    assert tracker.pose_counts() == {0: 1, 1: 1, 2: 0}


def test_leftmost_corner_crosses_before_center_and_crossing_vote_is_excluded():
    tracker = ValidationTracker(ValidationSettings(), CLASSES)
    for step, x in enumerate(range(900, 59, -60)):
        tracker.update(IMAGE, (detection(x),), step * 0.1)
    before = next(iter(tracker.tracks.values())).observations
    # Center 70 is still to the right of line x=50, but left corner 45 crosses.
    tracker.update(IMAGE, (detection(70, 1),), 1.5)
    assert len(tracker.results) == 1
    result = tracker.results[0]
    assert result.class_id == 0 and result.observations == before
    tracker.update(IMAGE, (detection(30, 1),), 1.6)
    tracker.update(IMAGE, (detection(10, 1),), 1.7)
    assert tracker.results == [result]


def test_delayed_crossing_frame_is_matched_before_track_loss_timeout():
    tracker = ValidationTracker(ValidationSettings(exit_fraction=0.35), CLASSES)
    for step in range(12):
        tracker.update(IMAGE, (detection(900 - 40 * step, 2),), step * 0.1)
    assert tracker.tracks[1].observations == 12
    # A processing stall lasts longer than max_gap_s. The next observed frame
    # still contains the uniquely matching part, now beyond the exit line.
    result = tracker.update(IMAGE, (detection(220, 1),), 1.7)
    assert len(result) == 1
    assert result[0].status == "Erkannt" and result[0].class_id == 2
    assert result[0].observations == 12 and result[0].consensus == 1
    assert result[0].reason == "Abschlusslinie"
    tracker.update(IMAGE, (detection(180, 1),), 1.8)
    tracker.stop(2)
    assert tracker.results == result


def test_delayed_exit_frame_keeps_two_parts_separate():
    tracker = ValidationTracker(ValidationSettings(exit_fraction=0.35), CLASSES)
    for step in range(12):
        tracker.update(
            IMAGE,
            (detection(900 - 40 * step, 2), detection(900 - 40 * step, 0, y=280)),
            step * 0.1,
        )
    results = tracker.update(IMAGE, (detection(220, 1, y=280), detection(220, 1)), 1.7)
    assert [(r.track_id, r.class_id, r.status) for r in results] == [
        (1, 2, "Erkannt"), (2, 0, "Erkannt")
    ]
    assert all(r.observations == 12 and r.consensus == 1 for r in results)


def test_stable_votes_do_not_override_actual_loss_or_wrong_geometry():
    for actual_missing_frame in (False, True):
        tracker = ValidationTracker(ValidationSettings(exit_fraction=0.35), CLASSES)
        for step in range(12):
            tracker.update(IMAGE, (detection(900 - 40 * step, 2),), step * 0.1)
        if actual_missing_frame:
            tracker.update(IMAGE, (), 1.7)
            tracker.update(IMAGE, (detection(180, 2),), 1.8)
        else:
            tracker.update(IMAGE, (detection(220, 2, y=280),), 1.7)
        assert len(tracker.results) == 1
        assert tracker.results[0].status == "Unvollständig"
        assert tracker.results[0].observations == 12
        assert tracker.pose_counts() == {0: 0, 1: 0, 2: 0}


def test_short_gap_bridges_but_lost_part_cannot_restart_in_middle():
    tracker = ValidationTracker(ValidationSettings(), CLASSES)
    tracker.update(IMAGE, (detection(900),), 1)
    tracker.update(IMAGE, (detection(850),), 1.1)
    tracker.update(IMAGE, (), 1.2)
    tracker.update(IMAGE, (detection(750),), 1.3)
    assert len(tracker.tracks) == 1
    tracker.update(IMAGE, (), 2)
    assert tracker.results[0].status == "Unvollständig"
    tracker.update(IMAGE, (detection(600),), 2.1)
    assert all(t.finalized for t in tracker.tracks.values())
    assert tracker.pose_counts() == {0: 0, 1: 0, 2: 0}


def test_single_clipped_entry_is_recorded_without_supplying_pose_votes():
    tracker = ValidationTracker(ValidationSettings(), CLASSES)
    tracker.update(IMAGE, (detection(990),), 1)
    assert tracker.tracks[1].observations == 0
    tracker.update(IMAGE, (), 2)
    assert len(tracker.results) == 1
    result = tracker.results[0]
    assert result.status == "Unvollständig" and result.class_id is None
    assert result.observations == 0 and result.image is None
    assert "nur eine Erkennung" in result.reason
    assert "keine gültige vollständig sichtbare Beobachtung" in result.reason
    tracker.update(IMAGE, (detection(500),), 3)
    assert not tracker.tracks
    assert tracker.results == [result]


def test_single_full_entry_is_recorded_once_after_loss_and_cannot_be_counted_again():
    tracker = ValidationTracker(ValidationSettings(), CLASSES)
    tracker.update(IMAGE, (detection(900),), 1)
    results = tracker.update(IMAGE, (), 1.6)
    assert len(results) == 1 and results[0].observations == 1
    assert results[0].status == "Unvollständig"
    assert "nur eine Erkennung" in results[0].reason
    tracker.update(IMAGE, (detection(890, 1),), 1.7)
    tracker.update(IMAGE, (), 2.3)
    assert tracker.stop(2.4) == []
    assert tracker.results == results
    assert tracker.counts() == {"Unklar": 0, "Unvollständig": 1}
    assert tracker.pose_counts() == {0: 0, 1: 0, 2: 0}


def test_stop_records_every_entry_even_with_only_one_detection_per_part():
    tracker = ValidationTracker(ValidationSettings(), CLASSES)
    tracker.update(IMAGE, (detection(900), detection(990, 1, y=280)), 1)
    results = tracker.stop(1.1)
    assert [(r.track_id, r.status) for r in results] == [
        (1, "Unvollständig"),
        (2, "Unvollständig"),
    ]
    assert [r.observations for r in results] == [1, 0]
    assert tracker.counts() == {"Unklar": 0, "Unvollständig": 2}
    assert tracker.stop(1.2) == []


def test_class_independent_duplicate_suppression():
    tracker = ValidationTracker(ValidationSettings(), CLASSES)
    tracker.update(IMAGE, (detection(900, 0), detection(900, 1, confidence=0.8)), 1)
    assert len(tracker.tracks) == 1
    assert tracker.tracks[1].observations == 1


def test_new_entry_cannot_steal_unconfirmed_parts_votes():
    tracker = ValidationTracker(ValidationSettings(), CLASSES)
    tracker.update(IMAGE, (detection(900, 0),), 1)
    tracker.update(IMAGE, (detection(840, 0), detection(900, 1)), 1.1)
    assert tracker.tracks[1].ambiguous
    assert tracker.tracks[1].counts == {0: 1}


def test_lost_part_reappearing_in_entry_area_is_not_born_again():
    tracker = ValidationTracker(ValidationSettings(), CLASSES)
    tracker.update(IMAGE, (detection(900),), 1)
    tracker.update(IMAGE, (detection(900),), 1.1)
    tracker.update(IMAGE, (), 1.7)
    tracker.update(IMAGE, (detection(890, 1),), 1.8)
    assert list(tracker.tracks) == [1]
    assert len(tracker.results) == 1
    assert tracker.results[0].status == "Unvollständig"


def test_ambiguous_assignment_never_transfers_class_votes():
    tracker = ValidationTracker(ValidationSettings(), CLASSES)
    pair = (detection(880, 0, half_width=40), detection(980, 1, half_width=40))
    tracker.update(IMAGE, pair, 1)
    tracker.update(IMAGE, pair, 1.1)
    counts = [dict(t.counts) for t in tracker.tracks.values()]
    tracker.update(IMAGE, (detection(930, 1, half_width=40),), 1.2)
    assert all(t.ambiguous for t in tracker.tracks.values())
    assert [t.counts for t in tracker.tracks.values()] == counts
    tracker.stop(2)
    assert all(r.status != "Erkannt" for r in tracker.results)


def test_insufficient_observations_are_unclear_and_duplicate_timestamps_ignored():
    tracker = ValidationTracker(ValidationSettings(), CLASSES)
    for step, x in enumerate((900, 850, 800)):
        tracker.update(IMAGE, (detection(x),), step * 0.1)
    tracker.update(IMAGE, (detection(780, 1),), 0.2)
    assert tracker.tracks[1].observations == 3
    tracker.settings = replace(tracker.settings, exit_fraction=0.5)
    for step, x in enumerate((730, 650, 570, 500), 3):
        tracker.update(IMAGE, (detection(x, 1),), step * 0.1)
    assert tracker.results[0].status == "Unklar"
    assert "Konsensanteil zu niedrig" in tracker.results[0].reason


@pytest.mark.parametrize(
    ("settings", "reason"),
    [
        (ValidationSettings(min_observations=100), "zu wenige gültige Beobachtungen"),
        (ValidationSettings(min_duration_s=5), "Beobachtungsdauer zu kurz"),
    ],
)
def test_failed_pose_requirements_are_recorded_with_reason(settings, reason):
    tracker = ValidationTracker(settings, CLASSES)
    for step, x in enumerate(range(900, -1, -60)):
        tracker.update(IMAGE, (detection(x),), step * 0.1)
    assert len(tracker.results) == 1
    assert tracker.results[0].status == "Unklar"
    assert reason in tracker.results[0].reason
    assert tracker.counts() == {"Unklar": 1, "Unvollständig": 0}
    assert tracker.pose_counts() == {0: 0, 1: 0, 2: 0}


class Camera(QObject):
    state_changed = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self.state = ConnectionState.CONNECTED
        self.capture = []

    def set_capture_stream_enabled(self, enabled, rate=None):
        self.capture.append((enabled, rate))


class Band(QObject):
    state_changed = pyqtSignal(object)
    status_changed = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.state = ConnectionState.CONNECTED
        self.config = SimpleNamespace(conveyor_forward_direction="right")
        self.status = ConveyorStatus(
            connected=True, velocity_only=True, capture_interface_version=1
        )
        self.started = []
        self.stops = 0

    def start_continuous(self, speed):
        self.started.append(speed)
        return True

    def stop_motion(self):
        self.stops += 1

    def set_continuous_speed(self, speed):
        return 0.1 <= speed <= 200


def controller(tmp_path):
    camera, band = Camera(), Band()
    control = ValidationController(camera, band)
    model = tmp_path / "model.pt"
    model.touch()
    control.set_model(InferenceModelInfo(str(model), "hash", "obb", CLASSES))
    return control, camera, band, LiveInferenceConfig(model)


def test_controller_warms_before_band_starts_and_stops_with_camera_failure(tmp_path, qtbot):
    control, camera, band, config = controller(tmp_path)
    assert control.start(ValidationSettings(), tmp_path / "sessions", config)
    assert not band.started and camera.capture[-1] == (True, 15)
    now = time.time()
    control.on_frame(InferenceFrame(IMAGE, (), 5, now, IMAGE))
    assert band.started == [10] and control.state == "preparing"
    band.status.continuous_running = True
    band.status_changed.emit(band.status)
    assert control.state == "running"
    assert control.set_speed(20)
    assert control.speed_events[-1]["speed_mm_s"] == 20
    control.on_frame(InferenceFrame(IMAGE, (detection(900),), 5, now + 0.1, IMAGE))
    control.on_frame(InferenceFrame(IMAGE, (detection(850),), 5, now + 0.2, IMAGE))
    camera.state_changed.emit(ConnectionState.ERROR)
    assert not control.running and band.stops == 1
    assert control.tracker.results[0].status == "Unvollständig"
    assert camera.capture[-1] == (False, None)
    control.close()


def test_wrong_model_and_wrong_band_direction_prevent_start(tmp_path):
    control, _, band, config = controller(tmp_path)
    control.model = replace(control.model, task="detect")
    assert not control.start(ValidationSettings(), tmp_path / "sessions", config)
    control.model = replace(control.model, task="obb")
    band.config.conveyor_forward_direction = "left"
    assert not control.start(ValidationSettings(), tmp_path / "sessions", config)
    assert not band.started


def test_unwritable_session_stops_before_band_start(tmp_path, qtbot):
    control, camera, band, config = controller(tmp_path)
    blocked_root = tmp_path / "file_instead_of_directory"
    blocked_root.write_text("occupied", encoding="utf-8")
    errors = []
    control.error.connect(errors.append)
    assert control.start(ValidationSettings(), blocked_root, config)
    control.on_frame(InferenceFrame(IMAGE, (), 5, time.time(), IMAGE))
    assert not control.running and not band.started
    assert band.stops == 1 and camera.capture[-1] == (False, None)
    assert errors and control.store is None
    control.close()


def test_store_export_is_consistent_and_contains_images_and_zero_classes(tmp_path, qtbot):
    control, _, band, config = controller(tmp_path)
    control.start(ValidationSettings(), tmp_path / "sessions", config)
    now = time.time()
    control.on_frame(InferenceFrame(IMAGE, (), 5, now, IMAGE))
    band.status.continuous_running = True
    band.status_changed.emit(band.status)
    for step, x in enumerate(range(900, -1, -60), 1):
        control.on_frame(InferenceFrame(IMAGE, (detection(x),), 5, now + step * 0.1, IMAGE))
    snapshot = control.snapshot()
    assert len(snapshot["results"]) == 1
    control.export(tmp_path / "exports")
    # Later data cannot change the already queued export snapshot.
    control.speed_events.append({"timestamp": now + 2, "speed_mm_s": 99})
    control.store.flush()
    export = next((tmp_path / "exports").iterdir())
    saved = json.loads((export / "session.json").read_text(encoding="utf-8"))
    assert len(saved["speed_events"]) == 1
    assert "frame_age_ms" in saved["performance"]
    assert saved["performance"]["inference_ms"] == 5
    assert (export / saved["results"][0]["image"]).is_file()
    assert (export / "bauteile.csv").read_bytes().startswith(b"\xef\xbb\xbf")
    with (export / "summen.csv").open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.reader(stream, delimiter=";"))
    assert ["2", "Pose C", "0"] in rows
    control.stop()
    original = control.store.directory
    control.reset()
    assert not control.has_results and (original / "session.json").is_file()


@pytest.mark.parametrize("clipped", [False, True])
@pytest.mark.parametrize("finish", ["lost", "stop"])
def test_single_failed_entry_is_saved_and_exported(tmp_path, qtbot, clipped, finish):
    control, _, band, config = controller(tmp_path)
    assert control.start(ValidationSettings(), tmp_path / "sessions", config)
    now = time.time()
    control.on_frame(InferenceFrame(IMAGE, (), 5, now, IMAGE))
    band.status.continuous_running = True
    band.status_changed.emit(band.status)
    control.on_frame(
        InferenceFrame(IMAGE, (detection(990 if clipped else 900),), 5, now + 0.1, IMAGE)
    )
    if finish == "lost":
        control.on_frame(InferenceFrame(IMAGE, (), 5, now + 0.7, IMAGE))
    else:
        control.stop()
    assert len(control.tracker.results) == 1
    control.export(tmp_path / "exports")
    control.store.flush()
    for directory in (control.store.directory, *list((tmp_path / "exports").iterdir())):
        saved = json.loads((directory / "session.json").read_text(encoding="utf-8"))
        assert saved["status_counts"] == {"Unklar": 0, "Unvollständig": 1}
        assert sum(saved["pose_counts"].values()) == 0
        result = saved["results"][0]
        assert result["status"] == "Unvollständig" and result["class_id"] is None
        assert result["observations"] == (0 if clipped else 1)
        assert "nur eine Erkennung" in result["reason"]
        if clipped:
            assert result["image"] is None
        else:
            assert (directory / result["image"]).is_file()
    export = next((tmp_path / "exports").iterdir())
    with (export / "bauteile.csv").open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream, delimiter=";"))
    assert len(rows) == 1 and rows[0]["status"] == "Unvollständig"
    assert "nur eine Erkennung" in rows[0]["reason"]
    with (export / "summen.csv").open(encoding="utf-8-sig", newline="") as stream:
        assert ["", "Unvollständig", "1"] in list(csv.reader(stream, delimiter=";"))
    control.close()


def test_store_queue_is_bounded(tmp_path):
    store = ValidationSessionStore(tmp_path, {})
    for _ in range(16):
        assert store._slots.acquire(blocking=False)
    with pytest.raises(RuntimeError, match="16 Schreibaufträge"):
        store.evidence(1, detection(900), IMAGE)
    store.close()


@pytest.mark.parametrize("fault", ["inference", "ads", "writer", "prepare_timeout"])
def test_all_failures_stop_band_without_restarting(tmp_path, fault):
    control, _, band, config = controller(tmp_path)
    control.start(ValidationSettings(), tmp_path / "sessions", config)
    control.on_frame(InferenceFrame(IMAGE, (), 5, time.time(), IMAGE))
    assert band.started == [10]
    if fault == "inference":
        control.stop("Inferenz ausgefallen")
    elif fault == "ads":
        band.state_changed.emit(ConnectionState.ERROR)
    elif fault == "writer":
        control._storage_error("Datenträger voll")
    else:
        control._deadline = time.monotonic() - 1
        control._watchdog()
    assert not control.running
    control.on_frame(InferenceFrame(IMAGE, (), 5, time.time() + 1, IMAGE))
    assert band.started == [10] and band.stops == 1
    control.close()


def test_model_change_with_results_requires_reset_and_preserves_saved_session(tmp_path):
    control, _, band, config = controller(tmp_path)
    control.start(ValidationSettings(), tmp_path / "sessions", config)
    now = time.time()
    control.on_frame(InferenceFrame(IMAGE, (), 5, now, IMAGE))
    band.status.continuous_running = True
    band.status_changed.emit(band.status)
    control.on_frame(InferenceFrame(IMAGE, (detection(900),), 5, now + 0.1, IMAGE))
    control.on_frame(InferenceFrame(IMAGE, (detection(850),), 5, now + 0.2, IMAGE))
    control.stop()
    model = control.model
    path = control.store.directory
    control.set_model(replace(model, sha256="other"))
    assert control.model == model
    control.reset()
    control.set_model(replace(model, sha256="other"))
    assert control.model.sha256 == "other"
    saved = json.loads((path / "session.json").read_text(encoding="utf-8"))
    assert saved["runs"][-1]["stop_reason"] == "Gestoppt"


def test_validation_settings_persist_without_changing_existing_inference_settings(tmp_path):
    from PyQt6.QtCore import QSettings

    from automated_image_capture.settings import SettingsStore

    backend = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    store = SettingsStore(backend)
    backend.setValue("inference/max_fps", 7)
    config = ValidationSettings(speed_mm_s=20, exit_fraction=0.12, min_observations=8)
    store.save_validation(config, tmp_path / "results")
    assert store.load_validation() == (config, tmp_path / "results")
    assert store.load_live_inference().max_fps == 7
