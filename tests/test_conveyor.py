from __future__ import annotations

import pytest
from PyQt6.QtTest import QSignalSpy

from automated_image_capture.acquisition import conveyor_positions
from automated_image_capture.hardware.conveyor import (
    UINT32_MODULUS,
    ConveyorAdapter,
    ConveyorWorker,
    effective_direction_sign,
    expected_move_internal_position,
    full_steps_for_offset,
    move_duration_seconds,
    move_position_matches,
    signed_u32_delta,
    speed_in_full_steps,
)
from automated_image_capture.models import ConnectionState, ConveyorMove, ConveyorStatus
from automated_image_capture.settings import AppSettings


def test_conveyor_path_records_outward_and_return_passes() -> None:
    assert conveyor_positions(50.0, 10.0) == (
        (0, 0.0, "out"),
        (1, 10.0, "out"),
        (2, 20.0, "out"),
        (3, 30.0, "out"),
        (4, 40.0, "out"),
        (5, 50.0, "out"),
        (6, 40.0, "back"),
        (7, 30.0, "back"),
        (8, 20.0, "back"),
        (9, 10.0, "back"),
        (10, 0.0, "back"),
    )


def test_absolute_step_targets_do_not_accumulate_rounding() -> None:
    calibration = 0.32960026
    targets = [full_steps_for_offset(offset, calibration) for offset in (0, 10, 20, 30)]

    assert targets == [0, 30, 61, 91]
    assert targets[-1] * calibration == pytest.approx(29.99362366)


def test_signed_plc_position_delta_handles_udint_wrap() -> None:
    assert signed_u32_delta(5, UINT32_MODULUS - 5) == 10
    assert signed_u32_delta(UINT32_MODULUS - 5, 5) == -10


def test_direction_sign_respects_plc_reverse_setting() -> None:
    assert effective_direction_sign("right", False) == 1
    assert effective_direction_sign("left", False) == -1
    assert effective_direction_sign("right", True) == -1
    assert effective_direction_sign("left", True) == 1


def test_move_completion_waits_for_actual_internal_endpoint() -> None:
    move = ConveyorMove(
        7,
        150.0,
        149.968,
        455,
        455,
        15.0,
        45.5,
        "right",
        start_internal_position=8002,
    )
    expected = 8002 + 455 * 64

    assert expected_move_internal_position(move, False) == expected
    assert not move_position_matches(move, expected - 19 * 64, False)
    assert move_position_matches(move, expected - 2 * 64, False)


def test_expected_move_endpoint_handles_reverse_and_u32_wrap() -> None:
    move = ConveyorMove(
        8,
        10.0,
        9.888,
        30,
        30,
        10.0,
        30.34,
        "right",
        start_internal_position=10,
    )

    assert expected_move_internal_position(move, True) == (10 - 30 * 64) % UINT32_MODULUS


def test_conveyor_speed_is_clamped_to_plc_range() -> None:
    assert speed_in_full_steps(10.0, 0.5) == 20.0
    assert speed_in_full_steps(200.0, 0.0245436926) == pytest.approx(8148.733086)
    with pytest.raises(ValueError, match="200 mm/s"):
        speed_in_full_steps(201.0, 0.0245436926)


class FakePlc:
    def __init__(self) -> None:
        self.writes: list[dict[str, object]] = []
        self.values = {
            "MAIN.ConveyorServoVelocityOnly": True,
            "MAIN.ConveyorCaptureInterfaceVersion": 1,
            "MAIN.StepperPosBusy": False,
            "MAIN.StepperPosError": False,
            "MAIN.ConveyorServoFaultCode": 0,
            "MAIN.ConveyorServoState": 0,
            "MAIN.ConveyorServo1TargetVelocity": 0,
            "MAIN.ConveyorServo2TargetVelocity": 0,
        }

    def read_list_by_name(self, names, *, cache_symbol_info: bool):
        return {name: self.values[name] for name in names}

    def write_list_by_name(self, values, *, cache_symbol_info: bool):
        assert cache_symbol_info
        self.writes.append(dict(values))
        return {name: "no error" for name in values}


def test_worker_enables_drive_before_sending_relative_move() -> None:
    plc = FakePlc()
    worker = ConveyorWorker(AppSettings())
    move = ConveyorMove(7, 10.0, 9.888, 30, 30, 10.0, 30.34, "right")

    worker._handle_command(plc, "move", move, object())

    assert plc.writes[-1] == {
        "MAIN.GuiConveyorEnabled": False,
        "MAIN.GuiConveyorCalibrationMode": True,
    }
    assert any(v.get("MAIN.ConveyorServoMotorCount") == 2 for v in plc.writes)
    assert any(v.get("MAIN.GuiConveyorReset") for v in plc.writes)
    assert not worker._move_command_sent

    worker._issue_move_command(plc, object())

    assert plc.writes[-1] == {
        "MAIN.GuiCalibrationJogSteps": 30,
        "MAIN.GuiCalibrationJogSpeedFullStepsPerSec": 30.34,
        "MAIN.GuiCalibrationMoveRight": True,
    }
    assert worker._move_command_sent


def test_worker_stop_disables_positioning_mode() -> None:
    plc = FakePlc()
    worker = ConveyorWorker(AppSettings())
    move = ConveyorMove(7, 10.0, 9.888, 30, 30, 10.0, 30.34, "right")
    worker._handle_command(plc, "move", move, object())

    worker._handle_command(plc, "stop", None, object())

    assert plc.writes[-1] == {
        "MAIN.GuiCalibrationStop": True,
        "MAIN.GuiConveyorCalibrationMode": False,
        "MAIN.GuiConveyorEnabled": False,
    }
    assert worker._requested_move is None


def test_adapter_computes_each_target_from_origin_not_previous_rounding() -> None:
    class DummyThread:
        @staticmethod
        def isRunning() -> bool:
            return True

    class DummyWorker:
        def __init__(self) -> None:
            self.moves: list[ConveyorMove] = []

        def enqueue_move(self, move: ConveyorMove) -> None:
            self.moves.append(move)

    adapter = ConveyorAdapter(AppSettings(conveyor_forward_direction="right"))
    worker = DummyWorker()
    adapter._worker = worker
    adapter._thread = DummyThread()
    adapter._state = ConnectionState.CONNECTED
    adapter._origin_position = 1000
    adapter._status = ConveyorStatus(
        connected=True,
        calibration_valid=True,
        mm_per_full_step=0.32960026,
        ready_to_execute=True,
        internal_position=1000,
        forward_direction="right",
        origin_position=1000,
    )

    adapter.request_offset(10.0, 10.0)
    first = worker.moves[-1]
    adapter._on_status(
        ConveyorStatus(
            connected=True,
            calibration_valid=True,
            mm_per_full_step=0.32960026,
            ready_to_execute=True,
            internal_position=1000 + first.target_full_steps * 64,
        )
    )
    adapter.request_offset(20.0, 10.0)
    second = worker.moves[-1]

    assert first.target_full_steps == 30
    assert first.delta_full_steps == 30
    assert second.target_full_steps == 61
    assert second.delta_full_steps == 31


def test_adapter_treats_plc_position_zero_as_a_real_position() -> None:
    class DummyThread:
        @staticmethod
        def isRunning() -> bool:
            return True

    class DummyWorker:
        move: ConveyorMove | None = None

        def enqueue_move(self, move: ConveyorMove) -> None:
            self.move = move

    adapter = ConveyorAdapter(AppSettings(conveyor_forward_direction="right"))
    worker = DummyWorker()
    adapter._worker = worker
    adapter._thread = DummyThread()
    adapter._state = ConnectionState.CONNECTED
    adapter._origin_position = 64
    adapter._status = ConveyorStatus(
        connected=True,
        calibration_valid=True,
        mm_per_full_step=1.0,
        ready_to_execute=True,
        internal_position=0,
    )

    adapter.request_offset(0.0, 10.0)

    assert worker.move is not None
    assert worker.move.delta_full_steps == 1


def test_adapter_marks_position_feedback_verified_only_after_observed_change() -> None:
    adapter = ConveyorAdapter(AppSettings(conveyor_forward_direction="right"))

    adapter._on_status(
        ConveyorStatus(
            connected=True,
            calibration_valid=True,
            mm_per_full_step=1.0,
            internal_position=100,
        )
    )
    assert not adapter.status.position_feedback_verified

    adapter._on_status(
        ConveyorStatus(
            connected=True,
            calibration_valid=True,
            mm_per_full_step=1.0,
            internal_position=164,
        )
    )
    assert adapter.status.position_feedback_verified

    adapter._on_status(
        ConveyorStatus(
            connected=True,
            calibration_valid=True,
            mm_per_full_step=1.0,
            internal_position=164,
        )
    )
    assert adapter.status.position_feedback_verified


def test_position_match_tolerance_can_cover_small_drive_residual() -> None:
    adapter = ConveyorAdapter(AppSettings(conveyor_forward_direction="right"))
    adapter._origin_position = 1000
    adapter._status = ConveyorStatus(
        connected=True,
        calibration_valid=True,
        mm_per_full_step=0.32960026,
        internal_position=1000 + 455 * 64 + 2 * 64,
        forward_direction="right",
        origin_position=1000,
    )
    adapter._decorate_status()

    assert adapter.position_matches(150.0, tolerance_full_steps=1)
    assert adapter.position_matches(150.0, tolerance_full_steps=3)
    adapter._status.logical_offset_mm = 151.5
    assert not adapter.position_matches(150.0)


def test_old_plc_interface_cannot_enable_capture_motors() -> None:
    plc = FakePlc()
    plc.values["MAIN.ConveyorCaptureInterfaceVersion"] = 0
    worker = ConveyorWorker(AppSettings())
    failures = QSignalSpy(worker.move_failed)
    worker._handle_command(plc, "move", ConveyorMove(7, 10, 10, 30, 30, 10, 30, "right"), None)

    assert len(failures) == 1
    assert not any(v.get("MAIN.GuiConveyorCalibrationMode") for v in plc.writes)
    assert worker._requested_move is None


def test_stop_during_tandem_preparation_prevents_later_enable() -> None:
    plc = FakePlc()
    worker = ConveyorWorker(AppSettings())
    original_write = plc.write_list_by_name

    def stop_when_configured(values, *, cache_symbol_info):
        result = original_write(values, cache_symbol_info=cache_symbol_info)
        if "MAIN.ConveyorServoMotorCount" in values:
            worker.enqueue_stop()
        return result

    plc.write_list_by_name = stop_when_configured
    failures = QSignalSpy(worker.move_failed)
    worker._handle_command(plc, "move", ConveyorMove(7, 10, 10, 30, 30, 10, 30, "right"), None)

    assert len(failures) == 1
    assert not any(v.get("MAIN.GuiConveyorCalibrationMode") for v in plc.writes)
    assert not any(v.get("MAIN.GuiCalibrationMoveRight") for v in plc.writes)


def test_finite_jog_duration_includes_acceleration_and_braking() -> None:
    assert move_duration_seconds(100, 10, 3) == pytest.approx(11)
    assert move_duration_seconds(10, 20, 3) == pytest.approx(2)


def test_continuous_start_uses_existing_right_direction_and_normal_plc_mode():
    plc = FakePlc()
    worker = ConveyorWorker(AppSettings())
    worker._handle_command(plc, "continuous_start", 10.0, None)
    assert plc.writes[-1] == {
        "MAIN.GuiConveyorSpeedMmPerSec": 10.0,
        "MAIN.GuiConveyorCalibrationMode": False,
        "MAIN.GuiConveyorEnabled": True,
    }
    # Right is the normal forward trajectory; existing PLC reverse wiring is preserved.
    assert not any("MAIN.GuiConveyorReverse" in values for values in plc.writes)
    assert worker._continuous_requested
    worker._handle_command(plc, "continuous_speed", 25.0, None)
    assert plc.writes[-1] == {"MAIN.GuiConveyorSpeedMmPerSec": 25.0}
    worker._handle_command(plc, "stop", None, None)
    assert not worker._continuous_requested
    assert plc.writes[-1]["MAIN.GuiConveyorEnabled"] is False


def test_continuous_stop_during_preparation_never_enables_motors():
    plc = FakePlc()
    worker = ConveyorWorker(AppSettings())
    original_write = plc.write_list_by_name

    def cancel(values, *, cache_symbol_info):
        response = original_write(values, cache_symbol_info=cache_symbol_info)
        if "MAIN.ConveyorServoMotorCount" in values:
            worker.enqueue_stop()
        return response

    plc.write_list_by_name = cancel
    worker._handle_command(plc, "continuous_start", 10.0, None)
    assert not any(values.get("MAIN.GuiConveyorEnabled") for values in plc.writes)
    assert not worker._continuous_requested


@pytest.mark.parametrize("speed", [0.0, -1.0, 200.1, float("nan")])
def test_continuous_speed_range_does_not_enable_for_invalid_values(speed):
    plc = FakePlc()
    worker = ConveyorWorker(AppSettings())
    worker._handle_command(plc, "continuous_start", speed, None)
    assert not any(values.get("MAIN.GuiConveyorEnabled") for values in plc.writes)


@pytest.mark.parametrize("second_motor_target, expected_running", [(100, True), (0, False)])
def test_worker_requires_both_motor_targets_to_confirm_continuous_run(
    monkeypatch, second_motor_target, expected_running
):
    import sys
    from types import SimpleNamespace

    from automated_image_capture.hardware.conveyor import STATUS_SYMBOLS

    worker = ConveyorWorker(AppSettings())
    worker._continuous_requested = True
    worker._continuous_deadline = 0
    plc = FakePlc()
    plc.values.update(dict.fromkeys(STATUS_SYMBOLS, 0))
    plc.values.update(
        {
            "MAIN.GuiConveyorEnabled": True,
            "MAIN.StepperPosBusy": True,
            "MAIN.ConveyorServo1TargetVelocity": 100,
            "MAIN.ConveyorServo2TargetVelocity": second_motor_target,
            "MAIN.GuiConveyorSpeedMmPerSec": 10,
        }
    )
    plc.open = lambda: None
    plc.close = lambda: None
    plc.read_state = lambda: (5, 0)
    monkeypatch.setitem(
        sys.modules,
        "pyads",
        SimpleNamespace(
            Connection=lambda *_: plc,
            set_timeout=lambda _: None,
        ),
    )
    statuses = []

    def observed(status):
        statuses.append(status)
        worker.stop()

    worker.status_changed.connect(observed)
    worker.run()
    assert statuses[0].continuous_running is expected_running
    assert statuses[0].continuous_requested is expected_running
    assert plc.writes[-1]["MAIN.GuiConveyorEnabled"] is False
