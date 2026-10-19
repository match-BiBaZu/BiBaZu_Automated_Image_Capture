from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PyQt6.QtCore import QSettings, QTimer
from PyQt6.QtTest import QSignalSpy

from automated_image_capture.inference import (
    InferenceDetection,
    LiveInferenceConfig,
    LiveInferenceWorker,
    draw_obb_overlay,
    extract_obb_detections,
)
from automated_image_capture.settings import SettingsStore


class FakeObb:
    xyxyxyxy = np.array([[[10, 10], [30, 10], [30, 25], [10, 25]]], dtype=np.float32)
    cls = np.array([1], dtype=np.float32)
    conf = np.array([0.91], dtype=np.float32)


class FakeResult:
    obb = FakeObb()
    names = {0: "Pose 1", 1: "Pose 2"}


class FakeModel:
    names = {0: "Pose 1", 1: "Pose 2", 2: "Pose 3"}
    task = "obb"

    def __init__(self) -> None:
        self.sources: list[np.ndarray] = []

    def predict(self, *, source, **_kwargs):
        self.sources.append(source.copy())
        return [FakeResult()]


class BlockingModel(FakeModel):
    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()

    def predict(self, *, source, **kwargs):
        result = super().predict(source=source, **kwargs)
        if len(self.sources) == 1:
            self.started.set()
            self.release.wait(2)
        return result


def test_extract_and_draw_obb_does_not_modify_camera_frame() -> None:
    detections = extract_obb_detections(FakeResult())
    assert len(detections) == 1
    assert detections[0].class_id == 1
    assert detections[0].class_name == "Pose 2"
    assert abs(detections[0].confidence - 0.91) < 1e-5

    image = np.zeros((50, 60, 3), dtype=np.uint8)
    before = image.copy()
    annotated = draw_obb_overlay(image, detections)

    assert np.array_equal(image, before)
    assert not np.array_equal(annotated, before)
    assert annotated.flags.c_contiguous


def test_live_worker_runs_model_in_background(qtbot, tmp_path: Path) -> None:
    model_path = tmp_path / "best.pt"
    model_path.touch()
    fake_model = FakeModel()
    worker = LiveInferenceWorker(
        LiveInferenceConfig(model_path, confidence=0.2, max_fps=15),
        model_factory=lambda _path: fake_model,
    )
    spy = QSignalSpy(worker.frame_ready)
    worker.start()
    rgb = np.zeros((50, 60, 3), dtype=np.uint8)
    rgb[:, :, 0] = 11
    rgb[:, :, 2] = 33
    worker.submit(rgb, time.time())

    qtbot.waitUntil(lambda: len(spy) == 1, timeout=3000)
    frame = spy[0][0]
    assert len(frame.detections) == 1
    assert frame.detections[0].class_name == "Pose 2"
    assert fake_model.sources[0][0, 0].tolist() == [33, 0, 11]
    assert worker.stop()


def test_loaded_model_classes_are_available_without_any_detection(qtbot, tmp_path):
    path = tmp_path / "model.pt"
    path.touch()
    worker = LiveInferenceWorker(LiveInferenceConfig(path), model_factory=lambda _: FakeModel())
    loaded = QSignalSpy(worker.model_loaded)
    frames = QSignalSpy(worker.frame_ready)
    worker.start()
    try:
        qtbot.waitUntil(lambda: len(loaded) == 1, timeout=5000)
        info = loaded[0][0]
        assert info.task == "obb"
        assert info.classes == FakeModel.names
        assert len(info.sha256) == 64
        assert not frames
    finally:
        assert worker.stop()


def test_live_worker_drops_stale_frames(qtbot, tmp_path: Path) -> None:
    model_path = tmp_path / "best.pt"
    model_path.touch()
    fake_model = BlockingModel()
    worker = LiveInferenceWorker(
        LiveInferenceConfig(model_path, max_fps=60),
        model_factory=lambda _path: fake_model,
    )
    spy = QSignalSpy(worker.frame_ready)
    worker.start()
    worker.submit(np.full((20, 20, 3), 1, dtype=np.uint8))
    qtbot.waitUntil(fake_model.started.is_set, timeout=2000)
    worker.submit(np.full((20, 20, 3), 2, dtype=np.uint8))
    worker.submit(np.full((20, 20, 3), 3, dtype=np.uint8))
    fake_model.release.set()

    qtbot.waitUntil(lambda: len(spy) >= 2, timeout=3000)
    assert len(fake_model.sources) == 2
    assert fake_model.sources[0][0, 0, 0] == 1
    assert fake_model.sources[1][0, 0, 0] == 3
    assert worker.stop()


def test_mono_frames_are_converted_only_after_latest_frame_selection(qtbot, tmp_path):
    path = tmp_path / "best.pt"
    path.touch()
    model = BlockingModel()
    worker = LiveInferenceWorker(
        LiveInferenceConfig(path, max_fps=60), model_factory=lambda _: model
    )
    frames = QSignalSpy(worker.frame_ready)
    worker.start()
    try:
        worker.submit(np.full((50, 60), 1, np.uint8))
        assert model.started.wait(timeout=2)
        worker.submit(np.full((50, 60), 2, np.uint8))
        worker.submit(np.full((50, 60), 3, np.uint8))
        model.release.set()
        qtbot.waitUntil(lambda: len(frames) >= 2, timeout=3000)
        assert [image[0, 0].tolist() for image in model.sources] == [[1, 1, 1], [3, 3, 3]]
        assert frames[1][0].raw_image.shape == (50, 60, 3)
        assert frames[1][0].timings["convert_ms"] >= 0
        assert frames[1][0].timings["predict_ms"] >= 0
    finally:
        model.release.set()
        assert worker.stop()


@pytest.mark.parametrize("clock_offset", [3600, -3600])
def test_fps_limit_is_independent_of_performance_clock_epoch(
    qtbot, tmp_path, monkeypatch, clock_offset
):
    from automated_image_capture import inference

    # Windows/Python 3.12 can expose different epochs for these two clocks.
    # Both extra waits and accidentally disabling the FPS cap are regressions.
    monkeypatch.setattr(
        inference,
        "time",
        SimpleNamespace(
            time=time.time,
            monotonic=time.monotonic,
            perf_counter=lambda: time.perf_counter() + clock_offset,
        ),
    )
    path = tmp_path / "model.pt"
    path.touch()
    model = FakeModel()
    prediction_times = []
    original_predict = model.predict

    def predict(**kwargs):
        prediction_times.append(time.perf_counter())
        return original_predict(**kwargs)

    model.predict = predict
    worker = LiveInferenceWorker(
        LiveInferenceConfig(path, max_fps=10), model_factory=lambda _: model
    )
    feed = QTimer()
    feed.timeout.connect(lambda: worker.submit(np.zeros((50, 60, 3), np.uint8)))
    worker.start()
    feed.start(2)
    try:
        qtbot.waitUntil(lambda: len(prediction_times) >= 5, timeout=2000)
        # Four periods at 10 FPS; allow scheduler/clock resolution jitter.
        assert prediction_times[4] - prediction_times[0] >= 0.3
    finally:
        feed.stop()
        assert worker.stop()


def test_live_settings_are_persisted(tmp_path: Path) -> None:
    backend = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    store = SettingsStore(backend)
    config = LiveInferenceConfig(
        tmp_path / "weights" / "best.pt",
        confidence=0.4,
        image_size=640,
        max_fps=7,
        device="0",
    )

    store.save_live_inference(config)
    loaded = store.load_live_inference()

    assert loaded == config


def test_overlay_supports_empty_detection_list() -> None:
    image = np.full((12, 16, 3), 127, dtype=np.uint8)
    assert np.array_equal(draw_obb_overlay(image, ()), image)


def test_overlay_detection_type_is_immutable() -> None:
    detection = InferenceDetection(0, "Pose 1", 0.75, ((1, 1), (8, 1), (8, 8), (1, 8)))
    assert detection.class_name == "Pose 1"
