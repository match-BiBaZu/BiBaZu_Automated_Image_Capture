from __future__ import annotations

import math
import queue
import socket
import threading
import time
from dataclasses import replace
from typing import Any

from PyQt6.QtCore import QObject, QThread, pyqtSignal, pyqtSlot

from automated_image_capture.hardware.base import DeviceAdapter
from automated_image_capture.models import ConnectionState, ConveyorMove, ConveyorStatus
from automated_image_capture.settings import AppSettings

PLC_PORT_TC3 = 851
ADS_TIMEOUT_MS = 500
ADS_RECONNECT_SECONDS = 2.0
DRIVE_ENABLE_TIMEOUT_SECONDS = 5.0
IDLE_POLL_SECONDS = 0.5
ACTIVE_POLL_SECONDS = 0.1
INCREMENTS_PER_FULL_STEP = 64
UINT32_MODULUS = 1 << 32
ENDPOINT_FEEDBACK_TOLERANCE_FULL_STEPS = 3
ENDPOINT_FEEDBACK_TIMEOUT_SECONDS = 2.0
MAX_BELT_SPEED_MM_S = 200.0
MAX_FULL_STEPS_PER_S = 8148.733086
# The velocity-only PLC does not correct the residual distance of a finite jog.
POSITION_TOLERANCE_MM = 1.0
NOMINAL_MM_PER_FULL_STEP = math.pi * 50.0 / (200.0 * 32.0)
TANDEM_CONFIG_VALUES = {
    "MAIN.ConveyorServoCommissioned": True,
    "MAIN.ConveyorServoMotorCount": 2,
    "MAIN.ConveyorServoSingleMotor": 1,
    "MAIN.ConveyorServoDirection1": -1,
    "MAIN.ConveyorServoDirection2": -1,
    "MAIN.ConveyorServoMotor2Ratio": 1.0,
    "MAIN.ConveyorServoMaxMotorRpm": 0.0,
}

STATUS_SYMBOLS = {
    "MAIN.ConveyorCaptureInterfaceVersion": "capture_interface_version",
    "MAIN.ConveyorServoVelocityOnly": "velocity_only",
    "MAIN.ConveyorServoMotorCount": "motor_count",
    "MAIN.ConveyorServoFaultCode": "servo_fault_code",
    "MAIN.ConveyorServoAccelerationRpmPerSec": "acceleration_rpm_per_s",
    "MAIN.CalibrationBusy": "busy",
    "MAIN.CalibrationError": "error",
    "MAIN.CalibrationStatusCode": "status_code",
    "MAIN.StepperPosReadyToExecute": "ready_to_execute",
    "MAIN.StepperPosWarning": "warning",
    "MAIN.StepperInternalPosition": "internal_position",
    "MAIN.StepperControlEnable": "control_enabled",
    "MAIN.StepperWcState": "wc_state",
    "MAIN.StepperInfoDataState": "info_data_state",
    "MAIN.GuiConveyorMmPerFullStep": "mm_per_full_step",
    "MAIN.GuiConveyorCalibrationValid": "calibration_valid",
    "MAIN.GuiConveyorReverse": "conveyor_reverse",
}


def signed_u32_delta(value: int, origin: int) -> int:
    """Return a wrap-safe signed delta between two PLC UDINT positions."""
    delta = (int(value) - int(origin)) % UINT32_MODULUS
    return delta - UINT32_MODULUS if delta >= UINT32_MODULUS // 2 else delta


def full_steps_for_offset(offset_mm: float, mm_per_full_step: float) -> int:
    if mm_per_full_step <= 0.0:
        raise ValueError("Die Förderbandkalibrierung ist ungültig.")
    if offset_mm < 0.0:
        raise ValueError("Der Förderbandoffset darf nicht negativ sein.")
    return int(float(offset_mm) / float(mm_per_full_step) + 0.5)


def speed_in_full_steps(speed_mm_per_s: float, mm_per_full_step: float) -> float:
    if not 0.1 <= speed_mm_per_s <= MAX_BELT_SPEED_MM_S:
        raise ValueError("Die Förderbandgeschwindigkeit muss zwischen 0,1 und 200 mm/s liegen.")
    if mm_per_full_step <= 0.0:
        raise ValueError("Die Förderbandkalibrierung ist ungültig.")
    return max(1.0, min(MAX_FULL_STEPS_PER_S, float(speed_mm_per_s) / mm_per_full_step))


def move_duration_seconds(
    distance_steps: float, speed_steps_s: float, acceleration_rpm_s: float
) -> float:
    """Include the common motor acceleration and deceleration of a finite jog."""
    acceleration = max(0.001, acceleration_rpm_s * 200.0 / 60.0)
    distance = abs(distance_steps)
    speed = max(0.001, speed_steps_s)
    if distance <= speed * speed / acceleration:
        return 2.0 * math.sqrt(distance / acceleration)
    return distance / speed + speed / acceleration


def effective_direction_sign(plc_direction: str, conveyor_reverse: bool) -> int:
    if plc_direction not in {"left", "right"}:
        raise ValueError("Die SPS-Richtung muss 'left' oder 'right' sein.")
    negative = plc_direction == "left"
    if conveyor_reverse:
        negative = not negative
    return -1 if negative else 1


def expected_move_internal_position(
    move: ConveyorMove,
    conveyor_reverse: bool,
) -> int | None:
    if move.start_internal_position is None:
        return None
    sign = effective_direction_sign(move.plc_direction, conveyor_reverse)
    increments = sign * abs(move.delta_full_steps) * INCREMENTS_PER_FULL_STEP
    return (int(move.start_internal_position) + increments) % UINT32_MODULUS


def move_position_matches(
    move: ConveyorMove,
    internal_position: int | None,
    conveyor_reverse: bool,
    tolerance_full_steps: int = ENDPOINT_FEEDBACK_TOLERANCE_FULL_STEPS,
    *,
    mm_per_full_step: float | None = None,
    tolerance_mm: float | None = None,
) -> bool:
    expected = expected_move_internal_position(move, conveyor_reverse)
    if expected is None or internal_position is None:
        return False
    difference = abs(signed_u32_delta(internal_position, expected))
    if mm_per_full_step is not None and tolerance_mm is not None:
        tolerance = max(tolerance_mm, max(0, tolerance_full_steps) * mm_per_full_step)
        return difference / INCREMENTS_PER_FULL_STEP * mm_per_full_step <= tolerance
    return difference <= max(0, tolerance_full_steps) * INCREMENTS_PER_FULL_STEP


def diagnose_plc_network(host: str, port: int = 48898, timeout: float = 0.4) -> str:
    """Give a useful hint without modifying routes or network settings."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return ""
    except OSError as exc:
        return f"SPS-IP {host} ist auf ADS/TCP {port} nicht erreichbar: {exc}"


class ConveyorWorker(QObject):
    state_changed = pyqtSignal(object)
    status_changed = pyqtSignal(object)
    error = pyqtSignal(str)
    event_message = pyqtSignal(str)
    move_failed = pyqtSignal(int, str)
    finished = pyqtSignal()

    def __init__(self, config: AppSettings) -> None:
        super().__init__()
        self._config = config
        self._stop = threading.Event()
        self._cancel_motion = threading.Event()
        self._commands: queue.Queue[tuple[str, object | None]] = queue.Queue()
        self._requested_move: ConveyorMove | None = None
        self._completed_sequence: int | None = None
        self._completed_internal_position: int | None = None
        self._completed_at: float | None = None
        self._saw_busy = False
        self._move_not_before = 0.0
        self._move_command_sent = False
        self._enable_deadline = 0.0
        self._movement_started_at: float | None = None
        self._movement_started_sequence: int | None = None
        self._completion_feedback_wait_started: float | None = None

    def enqueue_move(self, move: ConveyorMove) -> None:
        self._commands.put(("move", move))

    def enqueue_stop(self) -> None:
        self._cancel_motion.set()
        self._commands.put(("stop", None))

    def enqueue_release(self) -> None:
        self._cancel_motion.set()
        self._commands.put(("release", None))

    def stop(self) -> None:
        self._cancel_motion.set()
        self._stop.set()

    def _wait_tandem(self, plc: Any, predicate: Any, message: str) -> None:
        names = [
            "MAIN.StepperPosBusy",
            "MAIN.StepperPosError",
            "MAIN.ConveyorServoFaultCode",
            "MAIN.ConveyorServoState",
            "MAIN.ConveyorServo1TargetVelocity",
            "MAIN.ConveyorServo2TargetVelocity",
        ]
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline:
            if self._cancel_motion.is_set() or self._stop.is_set():
                raise RuntimeError("Förderbandvorbereitung abgebrochen.")
            values = plc.read_list_by_name(names, cache_symbol_info=True)
            if predicate(values):
                return
            self._cancel_motion.wait(0.05)
        raise RuntimeError(message)

    def _prepare_tandem(self, plc: Any, pyads: Any) -> None:
        """Use the same stopped tandem configuration as PressureControlGUI."""
        marker = plc.read_list_by_name(
            ["MAIN.ConveyorServoVelocityOnly", "MAIN.ConveyorCaptureInterfaceVersion"],
            cache_symbol_info=True,
        )
        if (
            not bool(marker["MAIN.ConveyorServoVelocityOnly"])
            or int(marker["MAIN.ConveyorCaptureInterfaceVersion"]) != 1
        ):
            raise RuntimeError("Die SPS enthält nicht das Zweimotoren-Geschwindigkeitsprogramm.")
        self._write_values(plc, self._safe_values(), pyads)
        self._wait_tandem(
            plc,
            lambda v: (
                not v["MAIN.StepperPosBusy"]
                and v["MAIN.ConveyorServo1TargetVelocity"] == 0
                and v["MAIN.ConveyorServo2TargetVelocity"] == 0
            ),
            "Beide Förderbandmotoren müssen vor der Konfiguration stillstehen.",
        )
        config = dict(TANDEM_CONFIG_VALUES)
        if (
            self._requested_move is not None
            and self._requested_move.acceleration_rpm_per_s is not None
        ):
            config["MAIN.ConveyorServoAccelerationRpmPerSec"] = (
                self._requested_move.acceleration_rpm_per_s
            )
        self._write_values(plc, config, pyads)
        if self._cancel_motion.wait(0.25) or self._stop.is_set():
            raise RuntimeError("Förderbandvorbereitung abgebrochen.")
        self._write_values(plc, {"MAIN.GuiConveyorReset": True}, pyads)
        if self._cancel_motion.wait(0.05) or self._stop.is_set():
            raise RuntimeError("Förderbandvorbereitung abgebrochen.")
        self._wait_tandem(
            plc,
            lambda v: (
                not v["MAIN.StepperPosBusy"]
                and not v["MAIN.StepperPosError"]
                and v["MAIN.ConveyorServoFaultCode"] == 0
                and v["MAIN.ConveyorServoState"] == 0
            ),
            "Die Tandem-Konfiguration wurde von der SPS nicht übernommen.",
        )

    @staticmethod
    def _safe_values() -> dict[str, object]:
        return {
            "MAIN.GuiCalibrationStop": True,
            "MAIN.GuiConveyorCalibrationMode": False,
            "MAIN.GuiConveyorEnabled": False,
        }

    def _write_values(self, plc: Any, values: dict[str, object], pyads: Any) -> None:
        del pyads
        errors = plc.write_list_by_name(values, cache_symbol_info=True)
        failed = {
            name: error
            for name, error in errors.items()
            if error and str(error).lower() != "no error"
        }
        if failed:
            raise RuntimeError(f"ADS-Sammelschreibzugriff fehlgeschlagen: {failed}")

    def _handle_command(self, plc: Any, command: str, payload: object | None, pyads: Any) -> None:
        if command == "stop":
            self._write_values(plc, self._safe_values(), pyads)
            self._requested_move = None
            self._move_command_sent = False
            self._movement_started_at = None
            self._movement_started_sequence = None
            self._completion_feedback_wait_started = None
            self.event_message.emit("Förderband-Stop an die SPS gesendet.")
            self._cancel_motion.clear()
            return
        if command == "release":
            self._write_values(plc, self._safe_values(), pyads)
            self._requested_move = None
            self._saw_busy = False
            self._move_command_sent = False
            self._movement_started_at = None
            self._movement_started_sequence = None
            self._completion_feedback_wait_started = None
            self.event_message.emit("Förderbandsteuerung freigegeben und gestoppt.")
            self._cancel_motion.clear()
            return
        if command != "move" or not isinstance(payload, ConveyorMove):
            return
        if self._requested_move is not None:
            raise RuntimeError("Eine Förderbandfahrt ist bereits aktiv.")
        self._requested_move = payload
        try:
            self._prepare_tandem(plc, pyads)
        except Exception as exc:
            self._fail_move(plc, pyads, str(exc))
            return
        self._write_values(
            plc,
            {
                "MAIN.GuiConveyorEnabled": False,
                "MAIN.GuiConveyorCalibrationMode": True,
            },
            pyads,
        )
        self._requested_move = payload
        self._saw_busy = False
        self._move_command_sent = False
        self._movement_started_at = None
        self._movement_started_sequence = None
        self._completion_feedback_wait_started = None
        self._enable_deadline = time.monotonic() + DRIVE_ENABLE_TIMEOUT_SECONDS
        self.event_message.emit(
            f"Aktiviere Positioniermodus für Förderbandfahrt #{payload.sequence} …"
        )

    def _issue_move_command(self, plc: Any, pyads: Any) -> None:
        move = self._requested_move
        if move is None:
            return
        if self._cancel_motion.is_set() or self._stop.is_set():
            self._fail_move(plc, pyads, "Förderbandfahrt abgebrochen.")
            return
        direction_symbol = (
            "MAIN.GuiCalibrationMoveLeft"
            if move.plc_direction == "left"
            else "MAIN.GuiCalibrationMoveRight"
        )
        self._write_values(
            plc,
            {
                "MAIN.GuiCalibrationJogSteps": abs(move.delta_full_steps),
                "MAIN.GuiCalibrationJogSpeedFullStepsPerSec": move.speed_full_steps_per_s,
                direction_symbol: True,
            },
            pyads,
        )
        self._move_command_sent = True
        self._movement_started_at = time.time()
        self._movement_started_sequence = move.sequence
        self._move_not_before = time.monotonic() + 0.05
        self.event_message.emit(
            f"Fahrt #{move.sequence}: {move.requested_offset_mm:g} mm "
            f"({abs(move.delta_full_steps)} Vollschritte, {move.plc_direction})."
        )

    def _fail_move(self, plc: Any, pyads: Any, message: str) -> None:
        move = self._requested_move
        try:
            self._write_values(plc, self._safe_values(), pyads)
        except Exception:
            pass
        self._requested_move = None
        self._move_command_sent = False
        self._saw_busy = False
        self._movement_started_at = None
        self._movement_started_sequence = None
        self._completion_feedback_wait_started = None
        if move is not None:
            self.move_failed.emit(move.sequence, message)

    @pyqtSlot()
    def run(self) -> None:
        plc: Any = None
        pyads: Any = None
        last_attempt = 0.0
        last_error = ""
        self.state_changed.emit(ConnectionState.CONNECTING)
        try:
            import pyads as imported_pyads

            pyads = imported_pyads
            pyads.set_timeout(ADS_TIMEOUT_MS)
            while not self._stop.is_set():
                now = time.monotonic()
                if plc is None and now - last_attempt >= ADS_RECONNECT_SECONDS:
                    last_attempt = now
                    try:
                        plc = pyads.Connection(
                            self._config.plc_ams_net_id,
                            self._config.plc_port,
                            self._config.plc_ip,
                        )
                        plc.open()
                        plc.read_state()
                        self.state_changed.emit(ConnectionState.CONNECTED)
                        self.event_message.emit(
                            f"TwinCAT ADS verbunden ({self._config.plc_ip}, "
                            f"{self._config.plc_ams_net_id}:{self._config.plc_port})."
                        )
                        last_error = ""
                    except Exception as exc:
                        if plc is not None:
                            try:
                                plc.close()
                            except Exception:
                                pass
                        plc = None
                        hint = diagnose_plc_network(self._config.plc_ip)
                        message = hint or f"ADS-Verbindung/Route fehlgeschlagen: {exc}"
                        if message != last_error:
                            self.error.emit(message)
                            last_error = message
                        self.state_changed.emit(ConnectionState.ERROR)

                if plc is None:
                    self._stop.wait(0.1)
                    continue

                try:
                    while True:
                        command, payload = self._commands.get_nowait()
                        self._handle_command(plc, command, payload, pyads)
                except queue.Empty:
                    pass

                try:
                    values = plc.read_list_by_name(list(STATUS_SYMBOLS), cache_symbol_info=True)
                    status = ConveyorStatus(
                        connected=True,
                        capture_interface_version=int(
                            values["MAIN.ConveyorCaptureInterfaceVersion"]
                        ),
                        velocity_only=bool(values["MAIN.ConveyorServoVelocityOnly"]),
                        motor_count=int(values["MAIN.ConveyorServoMotorCount"]),
                        servo_fault_code=int(values["MAIN.ConveyorServoFaultCode"]),
                        acceleration_rpm_per_s=float(
                            values["MAIN.ConveyorServoAccelerationRpmPerSec"]
                        ),
                        calibration_valid=bool(values["MAIN.GuiConveyorCalibrationValid"]),
                        mm_per_full_step=float(values["MAIN.GuiConveyorMmPerFullStep"]),
                        ready_to_execute=bool(values["MAIN.StepperPosReadyToExecute"]),
                        warning=bool(values["MAIN.StepperPosWarning"]),
                        busy=bool(values["MAIN.CalibrationBusy"]),
                        error=bool(values["MAIN.CalibrationError"]),
                        status_code=int(values["MAIN.CalibrationStatusCode"]),
                        internal_position=int(values["MAIN.StepperInternalPosition"]),
                        conveyor_reverse=bool(values["MAIN.GuiConveyorReverse"]),
                        control_enabled=bool(values["MAIN.StepperControlEnable"]),
                        preparing_drive=(
                            self._requested_move is not None and not self._move_command_sent
                        ),
                        wc_state=bool(values["MAIN.StepperWcState"]),
                        info_data_state=int(values["MAIN.StepperInfoDataState"]),
                        requested_offset_mm=(
                            None
                            if self._requested_move is None
                            else self._requested_move.requested_offset_mm
                        ),
                        actual_target_offset_mm=(
                            None
                            if self._requested_move is None
                            else self._requested_move.actual_offset_mm
                        ),
                        requested_sequence=(
                            None if self._requested_move is None else self._requested_move.sequence
                        ),
                        completed_sequence=self._completed_sequence,
                        completed_internal_position=self._completed_internal_position,
                        completed_at=self._completed_at,
                        movement_started_at=self._movement_started_at,
                        movement_started_sequence=self._movement_started_sequence,
                        sampled_at=time.time(),
                        last_move=self._requested_move,
                    )
                    if status.busy or status.status_code in {1, 2}:
                        self._saw_busy = True
                        self._completion_feedback_wait_started = None
                    if self._requested_move is not None:
                        if status.error or status.status_code in {4, 5}:
                            failed = self._requested_move
                            self._fail_move(
                                plc,
                                pyads,
                                f"Förderbandfahrt #{failed.sequence} von der SPS abgelehnt "
                                f"(Status {status.status_code}).",
                            )
                            status.requested_sequence = None
                            status.requested_offset_mm = None
                            status.actual_target_offset_mm = None
                            status.preparing_drive = False
                        elif not self._move_command_sent:
                            if status.ready_to_execute:
                                self._issue_move_command(plc, pyads)
                            elif time.monotonic() >= self._enable_deadline:
                                self._fail_move(
                                    plc,
                                    pyads,
                                    "Der Förderbandantrieb wurde nach Aktivierung des "
                                    "Positioniermodus nicht innerhalb von 5 Sekunden bereit. "
                                    f"Warning={status.warning}, Error={status.error}, "
                                    f"InfoData-State={status.info_data_state}.",
                                )
                                status.requested_sequence = None
                                status.requested_offset_mm = None
                                status.actual_target_offset_mm = None
                                status.preparing_drive = False
                        elif (
                            status.status_code == 3
                            and not status.busy
                            and time.monotonic() >= self._move_not_before
                        ):
                            move = self._requested_move
                            feedback_matches = move_position_matches(
                                move,
                                status.internal_position,
                                status.conveyor_reverse,
                                mm_per_full_step=status.mm_per_full_step,
                                tolerance_mm=POSITION_TOLERANCE_MM,
                            )
                            if feedback_matches or move.start_internal_position is None:
                                self._completed_sequence = move.sequence
                                self._completed_internal_position = status.internal_position
                                self._completed_at = time.time()
                                status.completed_sequence = self._completed_sequence
                                status.completed_internal_position = (
                                    self._completed_internal_position
                                )
                                status.completed_at = self._completed_at
                                status.last_move = move
                                self.event_message.emit(
                                    f"Förderbandfahrt #{self._completed_sequence} abgeschlossen."
                                )
                                self._requested_move = None
                                self._move_command_sent = False
                                self._saw_busy = False
                                self._completion_feedback_wait_started = None
                            elif self._completion_feedback_wait_started is None:
                                self._completion_feedback_wait_started = time.monotonic()
                                self.event_message.emit(
                                    f"Förderbandfahrt #{move.sequence} von der SPS quittiert; "
                                    "warte auf die nachlaufende Positionsrückmeldung …"
                                )
                            elif (
                                time.monotonic() - self._completion_feedback_wait_started
                                >= ENDPOINT_FEEDBACK_TIMEOUT_SECONDS
                            ):
                                expected = expected_move_internal_position(
                                    move, status.conveyor_reverse
                                )
                                actual = status.internal_position
                                difference = (
                                    None
                                    if expected is None or actual is None
                                    else abs(signed_u32_delta(actual, expected))
                                    / INCREMENTS_PER_FULL_STEP
                                )
                                self._fail_move(
                                    plc,
                                    pyads,
                                    f"Förderbandfahrt #{move.sequence}: SPS meldet fertig, "
                                    "aber die Positionsrückmeldung hat den Endpunkt auch nach "
                                    f"{ENDPOINT_FEEDBACK_TIMEOUT_SECONDS:g} s nicht erreicht"
                                    + (
                                        "."
                                        if difference is None
                                        else f" (Rest {difference:.1f} Vollschritte)."
                                    ),
                                )
                                status.requested_sequence = None
                                status.requested_offset_mm = None
                                status.actual_target_offset_mm = None
                                status.preparing_drive = False
                    self.status_changed.emit(status)
                    self.state_changed.emit(ConnectionState.CONNECTED)
                    last_error = ""
                except Exception as exc:
                    message = f"ADS-Lese-/Schreibfehler oder fehlendes SPS-Symbol: {exc}"
                    if message != last_error:
                        self.error.emit(message)
                        last_error = message
                    try:
                        plc.close()
                    except Exception:
                        pass
                    plc = None
                    self.state_changed.emit(ConnectionState.ERROR)
                self._stop.wait(
                    ACTIVE_POLL_SECONDS if self._requested_move is not None else IDLE_POLL_SECONDS
                )
        except Exception as exc:
            self.state_changed.emit(ConnectionState.ERROR)
            self.error.emit(f"pyads konnte nicht geladen oder gestartet werden: {exc}")
        finally:
            if plc is not None and pyads is not None:
                try:
                    self._write_values(plc, self._safe_values(), pyads)
                except Exception:
                    pass
                try:
                    plc.close()
                except Exception:
                    pass
            self.state_changed.emit(ConnectionState.DISCONNECTED)
            self.event_message.emit("TwinCAT-ADS-Verbindung getrennt.")
            self.finished.emit()


class ConveyorAdapter(DeviceAdapter):
    move_failed = pyqtSignal(int, str)

    def __init__(self, config: AppSettings, parent: QObject | None = None) -> None:
        super().__init__("Förderband", parent)
        self.config = config
        self._thread: QThread | None = None
        self._worker: ConveyorWorker | None = None
        self._status = ConveyorStatus(forward_direction=config.conveyor_forward_direction)
        self._origin_position: int | None = None
        self._next_sequence = int(time.time() * 1000) & 0x7FFFFFFF

    @property
    def status(self) -> ConveyorStatus:
        return replace(self._status)

    @property
    def origin_position(self) -> int | None:
        return self._origin_position

    def connect(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            return
        self._thread = QThread(self)
        self._worker = ConveyorWorker(self.config)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.state_changed.connect(self._set_state)
        self._worker.status_changed.connect(self._on_status)
        self._worker.move_failed.connect(self._on_move_failed)
        self._worker.error.connect(self._emit_error)
        self._worker.event_message.connect(self._emit_event)
        self._worker.finished.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(self._clear_thread)
        self._thread.start()

    @pyqtSlot()
    def _clear_thread(self) -> None:
        self._worker = None
        self._thread = None

    def set_forward_direction(self, direction: str) -> None:
        if direction not in {"left", "right"}:
            raise ValueError("Bitte zuerst Links oder Rechts als Vorwärtsrichtung festlegen.")
        self._status.forward_direction = direction
        self.config.conveyor_forward_direction = direction
        self._decorate_status()
        self.status_changed.emit(replace(self._status))

    def set_current_as_origin(self) -> bool:
        if self._status.internal_position is None or self._status.busy:
            self._emit_error("Der Förderband-Nullpunkt kann aktuell nicht übernommen werden.")
            return False
        self._origin_position = self._status.internal_position
        self._status.origin_position = self._origin_position
        self._status.logical_offset_mm = 0.0
        self.status_changed.emit(replace(self._status))
        self._emit_event(f"Aktuelle SPS-Position {self._origin_position} als 0 mm übernommen.")
        return True

    def restore_origin(self, internal_position: int) -> None:
        self._origin_position = int(internal_position) % UINT32_MODULUS
        self._decorate_status()

    def _decorate_status(self) -> None:
        self._status.forward_direction = self.config.conveyor_forward_direction
        self._status.origin_position = self._origin_position
        if self._origin_position is None or self._status.internal_position is None:
            self._status.logical_offset_mm = None
            return
        direction = self._status.forward_direction
        if direction not in {"left", "right"}:
            self._status.logical_offset_mm = None
            return
        sign = effective_direction_sign(direction, self._status.conveyor_reverse)
        increments = signed_u32_delta(self._status.internal_position, self._origin_position)
        self._status.logical_offset_mm = (
            increments * sign / INCREMENTS_PER_FULL_STEP * self._status.mm_per_full_step
        )

    @pyqtSlot(object)
    def _on_status(self, status: object) -> None:
        if not isinstance(status, ConveyorStatus):
            return
        previous_position = self._status.internal_position
        feedback_verified = self._status.position_feedback_verified or (
            previous_position is not None
            and status.internal_position is not None
            and status.internal_position != previous_position
        )
        previous_move = self._status.last_move
        self._status = replace(status)
        self._status.position_feedback_verified = feedback_verified
        if self._status.last_move is None:
            self._status.last_move = previous_move
        if self._status.last_move is not None:
            if self._status.requested_offset_mm is None:
                self._status.requested_offset_mm = self._status.last_move.requested_offset_mm
            if self._status.actual_target_offset_mm is None:
                self._status.actual_target_offset_mm = self._status.last_move.actual_offset_mm
        if (
            self._status.requested_sequence is not None
            and self._status.completed_sequence == self._status.requested_sequence
        ):
            self._status.requested_sequence = None
        self._decorate_status()
        self.status_changed.emit(replace(self._status))

    @pyqtSlot(int, str)
    def _on_move_failed(self, sequence: int, message: str) -> None:
        if self._status.requested_sequence == sequence:
            self._status.requested_sequence = None
        self._status.preparing_drive = False
        self.move_failed.emit(sequence, message)
        self._emit_error(message)

    def _ready_worker(self) -> ConveyorWorker | None:
        if (
            self._worker is None
            or self._thread is None
            or not self._thread.isRunning()
            or self.state is not ConnectionState.CONNECTED
        ):
            self._emit_error("Das Förderband ist nicht über ADS verbunden.")
            return None
        return self._worker

    def request_offset(
        self, offset_mm: float, speed_mm_per_s: float, acceleration_rpm_per_s: float | None = None
    ) -> int | None:
        try:
            speed_in_full_steps(speed_mm_per_s, self._status.mm_per_full_step)
            if acceleration_rpm_per_s is not None and not 0.1 <= acceleration_rpm_per_s <= 1000.0:
                raise ValueError("Die Motorbeschleunigung muss zwischen 0,1 und 1000 rpm/s liegen.")
        except ValueError as exc:
            self._emit_error(str(exc))
            return None
        worker = self._ready_worker()
        if worker is None:
            return None
        if self._origin_position is None:
            self._emit_error("Bitte zuerst die aktuelle Förderbandposition als 0 mm übernehmen.")
            return None
        if not self._status.calibration_valid or self._status.mm_per_full_step <= 0.0:
            self._emit_error("Die Förderbandkalibrierung in der SPS ist ungültig.")
            return None
        if self._status.busy or self._status.requested_sequence is not None:
            self._emit_error("Eine Förderbandfahrt ist bereits aktiv.")
            return None
        forward = self.config.conveyor_forward_direction
        if forward not in {"left", "right"}:
            self._emit_error("Bitte zuerst die Vorwärtsrichtung des Förderbands bestätigen.")
            return None
        target_steps = full_steps_for_offset(offset_mm, self._status.mm_per_full_step)
        sign = effective_direction_sign(forward, self._status.conveyor_reverse)
        current_position = self._status.internal_position
        if current_position is None:
            current_position = self._origin_position
        current_increments = signed_u32_delta(current_position, self._origin_position)
        current_steps = round(current_increments * sign / INCREMENTS_PER_FULL_STEP)
        delta_steps = target_steps - current_steps
        if abs(delta_steps) > 250000:
            self._emit_error("Die Fahrstrecke überschreitet 250000 virtuelle Vollschritte.")
            return None
        if delta_steps == 0:
            move = ConveyorMove(
                sequence=0,
                requested_offset_mm=float(offset_mm),
                actual_offset_mm=target_steps * self._status.mm_per_full_step,
                target_full_steps=target_steps,
                delta_full_steps=0,
                speed_mm_per_s=float(speed_mm_per_s),
                speed_full_steps_per_s=speed_in_full_steps(
                    speed_mm_per_s, self._status.mm_per_full_step
                ),
                plc_direction=forward,
                start_internal_position=current_position,
                commanded_at=time.time(),
            )
            self._status.requested_offset_mm = float(offset_mm)
            self._status.actual_target_offset_mm = target_steps * self._status.mm_per_full_step
            self._status.last_move = move
            self.status_changed.emit(replace(self._status))
            return 0
        plc_direction = forward if delta_steps > 0 else ("left" if forward == "right" else "right")
        self._next_sequence = (self._next_sequence + 1) & 0x7FFFFFFF or 1
        move = ConveyorMove(
            sequence=self._next_sequence,
            requested_offset_mm=float(offset_mm),
            actual_offset_mm=target_steps * self._status.mm_per_full_step,
            target_full_steps=target_steps,
            delta_full_steps=delta_steps,
            speed_mm_per_s=float(speed_mm_per_s),
            speed_full_steps_per_s=speed_in_full_steps(
                speed_mm_per_s, self._status.mm_per_full_step
            ),
            plc_direction=plc_direction,
            start_internal_position=self._status.internal_position,
            commanded_at=time.time(),
            acceleration_rpm_per_s=acceleration_rpm_per_s,
        )
        self._status.requested_sequence = move.sequence
        self._status.requested_offset_mm = move.requested_offset_mm
        self._status.actual_target_offset_mm = move.actual_offset_mm
        self._status.last_move = move
        self._status.preparing_drive = True
        self.status_changed.emit(replace(self._status))
        worker.enqueue_move(move)
        return move.sequence

    def jog(
        self,
        direction: str,
        distance_mm: float = 1.0,
        speed_mm_per_s: float = 10.0,
    ) -> int | None:
        if direction not in {"left", "right"}:
            raise ValueError("Ungültige Förderbandrichtung.")
        worker = self._ready_worker()
        if worker is None:
            return None
        if not self._status.calibration_valid or self._status.mm_per_full_step <= 0.0:
            self._emit_error("Die Förderbandkalibrierung in der SPS ist ungültig.")
            return None
        if self._status.busy or self._status.requested_sequence is not None:
            self._emit_error("Eine Förderbandfahrt ist bereits aktiv.")
            return None
        steps = max(1, full_steps_for_offset(distance_mm, self._status.mm_per_full_step))
        self._next_sequence = (self._next_sequence + 1) & 0x7FFFFFFF or 1
        move = ConveyorMove(
            sequence=self._next_sequence,
            requested_offset_mm=float(distance_mm),
            actual_offset_mm=steps * self._status.mm_per_full_step,
            target_full_steps=steps,
            delta_full_steps=steps,
            speed_mm_per_s=float(speed_mm_per_s),
            speed_full_steps_per_s=speed_in_full_steps(
                speed_mm_per_s, self._status.mm_per_full_step
            ),
            plc_direction=direction,
            start_internal_position=self._status.internal_position,
            commanded_at=time.time(),
        )
        self._status.requested_sequence = move.sequence
        self._status.last_move = move
        self._status.preparing_drive = True
        self.status_changed.emit(replace(self._status))
        worker.enqueue_move(move)
        return move.sequence

    def stop_motion(self) -> None:
        if self._worker is not None:
            self._worker.enqueue_stop()

    def release_control(self) -> None:
        if self._worker is not None:
            self._worker.enqueue_release()

    def position_matches(self, offset_mm: float, tolerance_full_steps: int = 1) -> bool:
        if self._status.logical_offset_mm is None or self._status.mm_per_full_step <= 0.0:
            return False
        expected = full_steps_for_offset(offset_mm, self._status.mm_per_full_step)
        tolerance = max(POSITION_TOLERANCE_MM, tolerance_full_steps * self._status.mm_per_full_step)
        expected_mm = expected * self._status.mm_per_full_step
        return abs(self._status.logical_offset_mm - expected_mm) <= tolerance

    def disconnect(self) -> None:
        worker, thread = self._worker, self._thread
        if worker is not None:
            worker.enqueue_release()
            worker.stop()
        if thread is not None and thread.isRunning():
            thread.quit()
            if not thread.wait(2000):
                self._emit_error("Förderband-Worker wurde nicht innerhalb von 2 Sekunden beendet.")
        self._set_state(ConnectionState.DISCONNECTED)
