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


def stale_status_plc():
    from automated_image_capture.hardware.conveyor import STATUS_SYMBOLS

    plc = FakePlc()
    plc.values.update(dict.fromkeys(STATUS_SYMBOLS, 0))
    plc.values.update({
        "MAIN.ConveyorServoVelocityOnly": True,
        "MAIN.ConveyorCaptureInterfaceVersion": 1,
        "MAIN.CalibrationStatusCode": 5,
        "MAIN.StepperInternalPosition": 1459326308,
    })
    original_write = plc.write_list_by_name

    def write(values, *, cache_symbol_info):
        result = original_write(values, cache_symbol_info=cache_symbol_info)
        plc.values.update(values)
        return result

    plc.write_list_by_name = write
    return plc


def test_startup_clears_only_retained_diagnostic_without_drive_commands():
    plc = stale_status_plc()
    worker = ConveyorWorker(AppSettings())
    events = QSignalSpy(worker.event_message)
    result = worker._clear_stale_startup_status(plc, dict(plc.values), None)
    assert result["MAIN.CalibrationStatusCode"] == 0
    assert plc.writes == [{"MAIN.CalibrationStatusCode": 0}]
    assert result["MAIN.StepperInternalPosition"] == 1459326308
    assert not result["MAIN.StepperControlEnable"]
    assert len(events) == 1 and "quittiert" in events[0][0]


@pytest.mark.parametrize(("symbol", "value"), [
    ("MAIN.CalibrationStatusCode", 4),
    ("MAIN.ConveyorServoVelocityOnly", False),
    ("MAIN.ConveyorCaptureInterfaceVersion", 0),
    ("MAIN.CalibrationError", True),
    ("MAIN.StepperPosError", True),
    ("MAIN.ConveyorServoFaultCode", 12),
    ("MAIN.ConveyorServoState", 10),
    ("MAIN.CalibrationBusy", True),
    ("MAIN.StepperPosBusy", True),
    ("MAIN.StepperControlEnable", True),
    ("MAIN.GuiConveyorEnabled", True),
    ("MAIN.GuiConveyorCalibrationMode", True),
    ("MAIN.ConveyorServo1TargetVelocity", 100),
    ("MAIN.ConveyorServo2TargetVelocity", -100),
])
def test_startup_does_not_acknowledge_active_drive_or_fault(symbol, value):
    plc = stale_status_plc()
    plc.values[symbol] = value
    worker = ConveyorWorker(AppSettings())
    result = worker._clear_stale_startup_status(plc, dict(plc.values), None)
    assert not plc.writes
    assert result["MAIN.CalibrationStatusCode"] == (value if symbol.endswith("StatusCode") else 5)


@pytest.mark.parametrize(("symbol", "value"), [
    ("MAIN.StepperPosError", True),
    ("MAIN.StepperInternalPosition", 1459326309),
    ("MAIN.ConveyorServo2TargetVelocity", 100),
])
def test_startup_rechecks_faults_and_position_before_acknowledging(symbol, value):
    plc = stale_status_plc()
    original_sample = dict(plc.values)
    plc.values[symbol] = value
    worker = ConveyorWorker(AppSettings())
    result = worker._clear_stale_startup_status(plc, original_sample, None)
    assert not plc.writes
    assert result[symbol] == value
    assert result["MAIN.CalibrationStatusCode"] == 5


def test_startup_preserves_retained_status_when_write_is_rejected():
    plc = stale_status_plc()
    plc.write_list_by_name = lambda values, **_: dict.fromkeys(values, "write denied")
    worker = ConveyorWorker(AppSettings())
    events = QSignalSpy(worker.event_message)
    result = worker._clear_stale_startup_status(plc, dict(plc.values), None)
    assert result["MAIN.CalibrationStatusCode"] == 5
    assert "nicht quittiert" in events[0][0]


def test_startup_reports_reasserted_fault_instead_of_hiding_it():
    plc = stale_status_plc()
    original_write = plc.write_list_by_name

    def write(values, **kwargs):
        result = original_write(values, **kwargs)
        plc.values["MAIN.CalibrationStatusCode"] = 5
        plc.values["MAIN.StepperPosError"] = True
        return result

    plc.write_list_by_name = write
    worker = ConveyorWorker(AppSettings())
    result = worker._clear_stale_startup_status(plc, dict(plc.values), None)
    assert result["MAIN.CalibrationStatusCode"] == 5
    assert result["MAIN.StepperPosError"]


@pytest.mark.parametrize("pending", ["move", "continuous", "queued", "stop", "cancel"])
def test_startup_skips_acknowledgement_with_pending_app_command(pending):
    plc = stale_status_plc()
    worker = ConveyorWorker(AppSettings())
    if pending == "move":
        worker._requested_move = ConveyorMove(7, 10, 10, 30, 30, 10, 30, "right")
    elif pending == "continuous":
        worker._continuous_requested = True
    elif pending == "queued":
        worker.enqueue_continuous(10)
    elif pending == "stop":
        worker._stop.set()
    else:
        worker._cancel_motion.set()
    worker._clear_stale_startup_status(plc, dict(plc.values), None)
    assert not plc.writes


def test_worker_clears_retained_status_on_connection_and_publishes_verified_value(monkeypatch):
    import sys
    from types import SimpleNamespace

    from automated_image_capture.hardware import conveyor

    plc = stale_status_plc()
    plc.open = lambda: None
    plc.close = lambda: None
    plc.read_state = lambda: (5, 0)
    monkeypatch.setitem(sys.modules, "pyads", SimpleNamespace(
        Connection=lambda *_: plc, set_timeout=lambda _: None,
    ))
    monkeypatch.setattr(conveyor, "IDLE_POLL_SECONDS", 0)
    worker = ConveyorWorker(AppSettings())
    statuses = []

    def observed(status):
        statuses.append(status)
        if len(statuses) == 1:
            # A later status must not trigger another automatic acknowledgement.
            plc.values["MAIN.CalibrationStatusCode"] = 5
        else:
            worker.stop()

    worker.status_changed.connect(observed)
    worker.run()
    assert [status.status_code for status in statuses] == [0, 5]
    assert sum("MAIN.CalibrationStatusCode" in write for write in plc.writes) == 1
    assert all(not write.get("MAIN.GuiConveyorReset") for write in plc.writes)
    assert all(not write.get("MAIN.GuiConveyorEnabled") for write in plc.writes)
    assert all(not write.get("MAIN.GuiConveyorCalibrationMode") for write in plc.writes)


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


@pytest.mark.parametrize("direction", ["left", "right"])
def test_updated_capture_direction_controls_outward_and_return_moves(direction):
    from types import SimpleNamespace

    moves = []
    adapter = ConveyorAdapter(AppSettings(conveyor_forward_direction="right"))
    adapter._state = ConnectionState.CONNECTED
    adapter._worker = SimpleNamespace(enqueue_move=moves.append)
    adapter._thread = SimpleNamespace(isRunning=lambda: True)
    adapter._origin_position = 1000
    adapter._status = ConveyorStatus(
        internal_position=1000, calibration_valid=True, mm_per_full_step=1,
    )
    assert adapter.set_forward_direction(direction)
    adapter.request_offset(10, 10)
    assert moves[-1].plc_direction == direction
    assert moves[-1].delta_full_steps == 10
    end_position = 1000 + effective_direction_sign(direction, False) * 10 * 64
    adapter._on_status(ConveyorStatus(
        internal_position=end_position, calibration_valid=True, mm_per_full_step=1,
        completed_sequence=moves[-1].sequence,
    ))
    assert adapter.status.forward_direction == direction
    adapter.request_offset(0, 10)
    assert moves[-1].plc_direction == ("right" if direction == "left" else "left")
    assert moves[-1].delta_full_steps == -10


@pytest.mark.parametrize(("field", "value"), [
    ("busy", True), ("preparing_drive", True), ("requested_sequence", 7),
    ("continuous_requested", True), ("continuous_running", True),
])
def test_direction_change_cannot_modify_pending_or_active_move(field, value):
    adapter = ConveyorAdapter(AppSettings(conveyor_forward_direction="right"))
    setattr(adapter._status, field, value)
    assert not adapter.set_forward_direction("left")
    assert adapter.config.conveyor_forward_direction == "right"


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


@pytest.mark.parametrize("position", [0, 1459326308])
def test_origin_can_be_set_in_standby_after_historical_fault_without_motion(position):
    adapter = ConveyorAdapter(AppSettings(conveyor_forward_direction="right"))
    adapter._state = ConnectionState.CONNECTED
    adapter._status = ConveyorStatus(internal_position=position, status_code=5)
    changed = QSignalSpy(adapter.status_changed)
    assert adapter.set_current_as_origin()
    assert adapter.origin_position == position
    assert adapter.status.logical_offset_mm == 0
    assert len(changed) == 1
    # No worker or PLC connection is needed: this is a local coordinate reference.
    assert adapter._worker is None


@pytest.mark.parametrize(("field", "value"), [
    ("internal_position", None), ("busy", True), ("preparing_drive", True),
    ("requested_sequence", 7), ("continuous_requested", True),
    ("continuous_running", True), ("error", True), ("servo_fault_code", 12),
])
def test_origin_rejects_active_fault_or_pending_motion(field, value):
    adapter = ConveyorAdapter(AppSettings())
    adapter._state = ConnectionState.CONNECTED
    adapter._status = ConveyorStatus(internal_position=1234)
    setattr(adapter._status, field, value)
    assert not adapter.set_current_as_origin()
    assert adapter.origin_position is None


def test_origin_rejects_disconnected_encoder_position():
    adapter = ConveyorAdapter(AppSettings())
    adapter._status = ConveyorStatus(internal_position=1234)
    assert not adapter.set_current_as_origin()
    assert adapter.origin_position is None


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
