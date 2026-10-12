from __future__ import annotations

import os

import pytest

from automated_image_capture.hardware.camera import CameraAdapter
from automated_image_capture.models import ConnectionState
from automated_image_capture.settings import AppSettings

pytestmark = [
    pytest.mark.hardware,
    pytest.mark.skipif(
        os.environ.get("RUN_HARDWARE_TESTS") != "1",
        reason="Set RUN_HARDWARE_TESTS=1 for physical hardware tests",
    ),
]


def _config() -> AppSettings:
    return AppSettings(
        camera_ip=os.environ.get("BAUMER_IP", "169.254.117.70"),
        camera_cti_path=os.environ.get(
            "BAUMER_CTI", r"C:\Program Files\Baumer Camera Explorer\bgapi2_gige.cti"
        ),
        preview_max_fps=15,
    )


def test_camera_qt_adapter(qtbot) -> None:
    camera = CameraAdapter(_config())
    frames: list[object] = []
    errors: list[str] = []
    camera.frame_ready.connect(frames.append)
    camera.error.connect(errors.append)

    try:
        camera.connect()
        qtbot.waitUntil(lambda: len(frames) >= 3, timeout=20_000)
        assert camera.state is ConnectionState.CONNECTED
        assert not errors
    finally:
        camera.disconnect()

    assert camera.state is ConnectionState.DISCONNECTED
