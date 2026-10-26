from __future__ import annotations

import csv
import json
import time
from dataclasses import replace
from types import SimpleNamespace

import cv2
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


@pytest.mark.parametrize(
    ("exit_fraction", "entry_fraction", "positions"),
    [(0.25, 0.4, (450, 410, 350, 270)), (0.6, 0.85, (900, 820, 740, 620))],
)
def test_tracker_uses_adjusted_entry_and_exit_lines(exit_fraction, entry_fraction, positions):
    settings = ValidationSettings(
        exit_fraction=exit_fraction,
        entry_fraction=entry_fraction,
    )
    tracker = ValidationTracker(settings, CLASSES)
    tracker.update(IMAGE, (detection(entry_fraction * 1000 - 30),), 0)
    assert not tracker.tracks
    for step, x in enumerate(positions, 1):
        tracker.update(IMAGE, (detection(x),), step * 0.1)
    assert len(tracker.results) == 1
    assert tracker.results[0].status == "Erkannt"
    assert tracker.results[0].reason == "Abschlusslinie"
    assert tracker.results[0].observations == 3


@pytest.mark.parametrize(
    ("exit_fraction", "entry_fraction"),
    [(-0.01, 0.75), (0.5, 0.5), (0.6, 0.4), (0.05, 1), (float("nan"), 0.75)],
)
def test_validation_rejects_out_of_bounds_or_unordered_lines(exit_fraction, entry_fraction):
    with pytest.raises(ValueError, match="Abschlusslinie muss links"):
        ValidationSettings(exit_fraction=exit_fraction, entry_fraction=entry_fraction).validated()


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


def test_stable_pose_survives_loss_without_adopting_later_or_unrelated_votes():
    for actual_missing_frame in (False, True):
        tracker = ValidationTracker(ValidationSettings(exit_fraction=0.35), CLASSES)
        for step in range(12):
            tracker.update(IMAGE, (detection(900 - 40 * step, 2),), step * 0.1)
        if actual_missing_frame:
            tracker.update(IMAGE, (), 1.7)
            tracker.update(IMAGE, (detection(180, 1),), 1.8)
        else:
            tracker.update(IMAGE, (detection(220, 2, y=280),), 1.7)
        assert len(tracker.results) == 1
        assert tracker.results[0].status == "Erkannt"
        assert tracker.results[0].class_id == 2
        assert not tracker.results[0].pass_completed
        assert tracker.results[0].observations == 12
        assert tracker.pose_counts() == {0: 0, 1: 0, 2: 1}


def test_short_gap_bridges_but_lost_part_cannot_restart_in_middle():
    tracker = ValidationTracker(ValidationSettings(), CLASSES)
    tracker.update(IMAGE, (detection(900),), 1)
    tracker.update(IMAGE, (detection(850),), 1.1)
    tracker.update(IMAGE, (), 1.2)
    tracker.update(IMAGE, (detection(750),), 1.3)
    assert len(tracker.tracks) == 1
    tracker.update(IMAGE, (), 2)
    assert tracker.results[0].status == "Erkannt"
    assert tracker.results[0].candidate_class_id == 0
    assert tracker.results[0].candidate_pose == "Pose A"
    tracker.update(IMAGE, (detection(600),), 2.1)
    assert all(t.finalized for t in tracker.tracks.values())
    assert tracker.pose_counts() == {0: 1, 1: 0, 2: 0}


def test_single_clipped_entry_is_recorded_without_supplying_pose_votes():
    tracker = ValidationTracker(ValidationSettings(), CLASSES)
    tracker.update(IMAGE, (detection(990),), 1)
    assert tracker.tracks[1].observations == 0
    tracker.update(IMAGE, (), 2)
    assert len(tracker.results) == 1
    result = tracker.results[0]
    assert result.status == "Unvollständig" and result.class_id is None
    assert result.observations == 0 and result.image is None
    assert result.first_image == "erstbilder/000001.png"
    assert result.first_class_id == 0 and result.first_confidence == 0.9
    assert result.candidate_pose is None and result.candidate_class_id is None
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
    assert results[0].status == "Erkannt"
    assert "nur eine Erkennung" in results[0].reason
    tracker.update(IMAGE, (detection(890, 1),), 1.7)
    tracker.update(IMAGE, (), 2.3)
    assert tracker.stop(2.4) == []
    assert tracker.results == results
    assert tracker.counts() == {"Unklar": 0, "Unvollständig": 0}
    assert tracker.pose_counts() == {0: 1, 1: 0, 2: 0}


def test_stop_records_every_entry_even_with_only_one_detection_per_part():
    tracker = ValidationTracker(ValidationSettings(), CLASSES)
    tracker.update(IMAGE, (detection(900), detection(990, 1, y=280)), 1)
    results = tracker.stop(1.1)
    assert [(r.track_id, r.status) for r in results] == [
        (1, "Erkannt"),
        (2, "Unvollständig"),
    ]
    assert [r.observations for r in results] == [1, 0]
    assert tracker.counts() == {"Unklar": 0, "Unvollständig": 1}
    assert tracker.pose_counts() == {0: 1, 1: 0, 2: 0}
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
    assert tracker.results[0].status == "Erkannt"


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


def test_balanced_votes_are_unclear_and_duplicate_timestamps_ignored():
    tracker = ValidationTracker(ValidationSettings(), CLASSES)
    for step, x in enumerate((900, 850, 800)):
        tracker.update(IMAGE, (detection(x),), step * 0.1)
    tracker.update(IMAGE, (detection(780, 1),), 0.2)
    assert tracker.tracks[1].observations == 3
    tracker.settings = replace(tracker.settings, exit_fraction=0.5)
    for step, x in enumerate((730, 650, 570, 500), 3):
        tracker.update(IMAGE, (detection(x, 1),), step * 0.1)
    assert tracker.results[0].status == "Unklar"
    assert "Erkennungsanteil überschreitet die Schwelle nicht" in tracker.results[0].reason


@pytest.mark.parametrize("majority", [90, 91, 100])
def test_pose_decision_uses_strictly_over_90_percent_of_detection_counts(majority):
    tracker = ValidationTracker(ValidationSettings(), CLASSES)
    for step in range(100):
        cid = 0 if step < majority else 1
        tracker.update(
            IMAGE,
            (detection(900 - step * 4, cid, confidence=0.3 if cid == 0 else 0.99),),
            step * 0.01,
        )
    tracker.stop(1.1)
    assert len(tracker.results) == 1
    record = tracker.results[0]
    assert record.observations == 100 and record.winning_observations == majority
    assert record.consensus == majority / 100
    assert record.mean_confidence == pytest.approx(0.3)
    expected_counts = {0: majority, 1: 100 - majority} if majority < 100 else {0: 100}
    assert record.pose_observations == expected_counts
    assert record.status == ("Erkannt" if majority > 90 else "Unklar")
    assert record.class_id == (0 if majority > 90 else None)
    assert not record.pass_completed
    assert tracker.pose_counts() == {0: int(majority > 90), 1: 0, 2: 0}


def test_pose_decision_has_no_additional_observation_count_or_duration_gate():
    tracker = ValidationTracker(ValidationSettings(), CLASSES)
    tracker.update(IMAGE, (detection(900, 2),), 1)
    tracker.stop(1.001)
    assert tracker.results[0].status == "Erkannt"
    assert tracker.results[0].class_id == 2
    assert tracker.results[0].winning_observations == tracker.results[0].observations == 1


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
    assert control.tracker.results[0].status == "Erkannt"
    assert not control.tracker.results[0].pass_completed
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
    assert saved["vote_policy"]["method"] == "detection_count"
    assert saved["vote_policy"]["comparison"] == ">"
    assert saved["vote_policy"]["threshold"] == 0.9
    assert saved["results"][0]["winning_observations"] == saved["results"][0]["observations"]
    assert saved["results"][0]["pass_completed"]
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
        assert saved["status_counts"] == {"Unklar": 0, "Unvollständig": int(clipped)}
        assert sum(saved["pose_counts"].values()) == int(not clipped)
        result = saved["results"][0]
        assert result["status"] == ("Unvollständig" if clipped else "Erkannt")
        assert result["class_id"] == (None if clipped else 0)
        assert result["candidate_class_id"] == (None if clipped else 0)
        assert result["candidate_pose"] == (None if clipped else "Pose A")
        assert result["observations"] == (0 if clipped else 1)
        assert result["first_class_id"] == 0 and result["first_confidence"] == 0.9
        assert (directory / result["first_image"]).is_file()
        assert "nur eine Erkennung" in result["reason"]
        if clipped:
            assert result["image"] is None
        else:
            assert (directory / result["image"]).is_file()
    export = next((tmp_path / "exports").iterdir())
    with (export / "bauteile.csv").open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream, delimiter=";"))
    assert len(rows) == 1 and rows[0]["status"] == ("Unvollständig" if clipped else "Erkannt")
    assert json.loads(rows[0]["pose_observations"]) == ({} if clipped else {"0": 1})
    assert int(rows[0]["winning_observations"]) == int(not clipped)
    assert rows[0]["candidate_pose"] == ("" if clipped else "Pose A")
    assert rows[0]["first_image"] == "erstbilder/000001.png"
    assert rows[0]["first_class_id"] == "0"
    assert float(rows[0]["first_confidence"]) == 0.9
    assert "nur eine Erkennung" in rows[0]["reason"]
    with (export / "summen.csv").open(encoding="utf-8-sig", newline="") as stream:
        assert ["", "Unvollständig", str(int(clipped))] in list(csv.reader(stream, delimiter=";"))
    control.close()


def test_first_image_is_saved_immediately_and_preserved_after_pose_changes(tmp_path, qtbot):
    control, _, band, config = controller(tmp_path)
    assert control.start(ValidationSettings(), tmp_path / "sessions", config)
    now = time.time()
    control.on_frame(InferenceFrame(IMAGE, (), 5, now, IMAGE))
    band.status.continuous_running = True
    band.status_changed.emit(band.status)
    initial = np.empty_like(IMAGE)
    initial[:] = (10, 20, 30)
    try:
        control.on_frame(
            InferenceFrame(initial, (detection(990, confidence=0.3),), 5, now + 0.1, initial)
        )
        # The bounded writer must own a copy, including for a clipped, zero-vote entry.
        initial[:] = 255
        control.store.flush()
        first_path = control.store.directory / "erstbilder/000001.png"
        first_bytes = first_path.read_bytes()
        assert cv2.imread(str(first_path))[390, 10].tolist() == [30, 20, 10]
        assert control.tracker.tracks[1].observations == 0
        assert not control.tracker.results
        for step, x in enumerate((960, 920, 880), 2):
            control.on_frame(
                InferenceFrame(
                    IMAGE, (detection(x, 1, confidence=0.99),), 5, now + step * 0.1, IMAGE
                )
            )
        control.stop()
        control.export(tmp_path / "exports")
        control.store.flush()
        record = control.tracker.results[0]
        assert record.first_class_id == 0 and record.first_confidence == 0.3
        assert record.candidate_class_id == 1
        assert record.observations == 3
        assert first_path.read_bytes() == first_bytes
        assert (control.store.directory / record.image).is_file()
        exported = next((tmp_path / "exports").iterdir())
        assert (exported / record.first_image).read_bytes() == first_bytes
        saved_part = json.loads(
            (control.store.directory / "part_000001.json").read_text(encoding="utf-8")
        )
        assert saved_part["first_image"] == record.first_image
        assert saved_part["first_confidence"] == 0.3
    finally:
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


@pytest.mark.parametrize(("exit_fraction", "entry_fraction"), [(0.12, 0.4), (0.8, 0.9)])
def test_validation_settings_persist_without_changing_existing_inference_settings(
    tmp_path, exit_fraction, entry_fraction
):
    from PyQt6.QtCore import QSettings

    from automated_image_capture.settings import SettingsStore

    backend = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    store = SettingsStore(backend)
    backend.setValue("inference/max_fps", 7)
    config = ValidationSettings(
        speed_mm_s=20,
        exit_fraction=exit_fraction,
        entry_fraction=entry_fraction,
        min_consensus=0.95,
    )
    store.save_validation(config, tmp_path / "results")
    assert store.load_validation() == (config, tmp_path / "results")
    assert store.load_live_inference().max_fps == 7


def test_legacy_vote_settings_migrate_to_count_share_and_ignore_old_gates(tmp_path):
    from PyQt6.QtCore import QSettings

    from automated_image_capture.settings import SettingsStore

    backend = QSettings(str(tmp_path / "legacy.ini"), QSettings.Format.IniFormat)
    backend.setValue("validation/min_consensus", 0.7)
    backend.setValue("validation/min_observations", 1000)
    backend.setValue("validation/min_duration_s", 60)
    backend.setValue("validation/speed_mm_s", 35)
    store = SettingsStore(backend)
    config, directory = store.load_validation()
    assert config.min_consensus == 0.9
    assert config.speed_mm_s == 35
    assert not hasattr(config, "min_observations") and not hasattr(config, "min_duration_s")
    store.save_validation(replace(config, min_consensus=0.95), directory)
    assert store.load_validation()[0].min_consensus == 0.95


def test_lost_part_with_103_observations_is_counted_and_exported_as_its_majority_pose(
    tmp_path, qtbot
):
    control, _, band, config = controller(tmp_path)
    assert control.start(ValidationSettings(), tmp_path / "sessions", config)
    now = time.time()
    control.on_frame(InferenceFrame(IMAGE, (), 5, now, IMAGE))
    band.status.continuous_running = True
    band.status_changed.emit(band.status)
    try:
        for step in range(103):
            cid = 2 if step < 102 else 1
            control.on_frame(InferenceFrame(
                IMAGE, (detection(900 - 3 * step, cid),), 5, now + 0.1 + step * 0.03, IMAGE
            ))
        control.on_frame(InferenceFrame(IMAGE, (), 5, now + 3.9, IMAGE))
        assert len(control.tracker.results) == 1
        result = control.tracker.results[0]
        assert result.class_id == 2 and result.pose == "Pose C" and result.status == "Erkannt"
        assert result.observations == 103 and result.winning_observations == 102
        assert result.consensus == 102 / 103
        assert result.pose_observations == {2: 102, 1: 1}
        assert not result.pass_completed and "Erkennung verloren" in result.reason
        assert control.tracker.pose_counts() == {0: 0, 1: 0, 2: 1}
        # A sealed track cannot change its pose or count again after reappearing.
        control.on_frame(InferenceFrame(IMAGE, (detection(510, 0),), 5, now + 4, IMAGE))
        assert control.tracker.results == [result]
        control.export(tmp_path / "exports")
        control.store.flush()
        export = next((tmp_path / "exports").iterdir())
        saved = json.loads((export / "session.json").read_text(encoding="utf-8"))
        assert saved["pose_counts"] == {"0": 0, "1": 0, "2": 1}
        assert saved["status_counts"] == {"Unklar": 0, "Unvollständig": 0}
        assert saved["results"][0]["pose_observations"] == {"2": 102, "1": 1}
        assert (export / result.image).is_file() and (export / result.first_image).is_file()
        with (export / "bauteile.csv").open(encoding="utf-8-sig", newline="") as stream:
            row = next(csv.DictReader(stream, delimiter=";"))
        assert row["pose"] == "Pose C" and row["status"] == "Erkannt"
        assert row["observations"] == "103" and row["winning_observations"] == "102"
        assert row["pass_completed"] == "False"
        assert json.loads(row["pose_observations"]) == {"2": 102, "1": 1}
    finally:
        control.close()
