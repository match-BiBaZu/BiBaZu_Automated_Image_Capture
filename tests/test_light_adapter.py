from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

import pytest

import automated_image_capture.hardware.light as light_module
from automated_image_capture.hardware.light import LightAdapter
from automated_image_capture.models import ConnectionState, DiscoveredLight, LightStatus
from automated_image_capture.settings import AppSettings


async def wait_for(predicate, timeout: float = 1.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.01)


class FakeScanner:
    @staticmethod
    async def scan(timeout: float = 5.0):
        return [SimpleNamespace(name="NEEWER-RGB660", address="AA:BB")]


class FakeLight:
    instances: list[FakeLight] = []

    def __init__(self, address: str, name: str = "") -> None:
        self.address = address
        self.name = name
        self.client = SimpleNamespace(is_connected=False)
        self.is_on = True
        self.commands: list[tuple[object, ...]] = []
        self.instances.append(self)

    async def connect(self) -> None:
        self.client.is_connected = True

    async def disconnect(self) -> None:
        self.client.is_connected = False

    async def turn_on(self) -> None:
        self.commands.append(("power", True))

    async def turn_off(self) -> None:
        self.commands.append(("power", False))

    async def set_cct(self, kelvin: int, brightness: int, gm: int = 50) -> None:
        self.commands.append(("cct", kelvin, brightness, gm))

    async def set_rgb(self, hue: int, saturation: int, brightness: int) -> None:
        self.commands.append(("hsi", hue, saturation, brightness))


@pytest.mark.asyncio
async def test_light_connect_does_not_change_output_and_commands_are_explicit(
    qtbot, monkeypatch
) -> None:
    FakeLight.instances.clear()
    fake_module = SimpleNamespace(NeewerScanner=FakeScanner, NeewerLight=FakeLight)
    monkeypatch.setitem(sys.modules, "neewerlite", fake_module)
    adapter = LightAdapter(AppSettings(auto_reconnect=False))
    statuses: list[LightStatus] = []
    adapter.status_changed.connect(statuses.append)

    adapter.connect()
    await wait_for(lambda: adapter.state is ConnectionState.CONNECTED)
    light = FakeLight.instances[-1]

    assert light.commands == []
    assert statuses[-1].power is True
    assert statuses[-1].values_are_confirmed_commands is False

    adapter.set_cct(4200, 35)
    await wait_for(lambda: bool(light.commands))
    assert light.commands == [("cct", 4200, 35, 50)]
    assert statuses[-1].values_are_confirmed_commands is True

    await adapter.shutdown()
    assert light.commands == [] or all(command[0] != "power" for command in light.commands)
    assert adapter.state is ConnectionState.DISCONNECTED


@pytest.mark.asyncio
async def test_light_hsi_values_are_clamped(qtbot, monkeypatch) -> None:
    FakeLight.instances.clear()
    fake_module = SimpleNamespace(NeewerScanner=FakeScanner, NeewerLight=FakeLight)
    monkeypatch.setitem(sys.modules, "neewerlite", fake_module)
    adapter = LightAdapter(AppSettings(auto_reconnect=False))

    adapter.connect()
    await wait_for(lambda: adapter.state is ConnectionState.CONNECTED)
    light = FakeLight.instances[-1]
    adapter.set_hsi(999, -10, 150)
    await wait_for(lambda: bool(light.commands))

    assert light.commands == [("hsi", 360, 0, 100)]
    await adapter.shutdown()


@pytest.mark.asyncio
async def test_ramp_commands_are_serialized_and_stale_targets_are_skipped(
    qtbot, monkeypatch
) -> None:
    class SlowFakeLight(FakeLight):
        active = 0
        maximum_active = 0

        async def set_cct(self, kelvin: int, brightness: int, gm: int = 50) -> None:
            type(self).active += 1
            type(self).maximum_active = max(type(self).maximum_active, type(self).active)
            await asyncio.sleep(0.05)
            self.commands.append(("cct", kelvin, brightness, gm))
            type(self).active -= 1

    FakeLight.instances.clear()
    fake_module = SimpleNamespace(NeewerScanner=FakeScanner, NeewerLight=SlowFakeLight)
    monkeypatch.setitem(sys.modules, "neewerlite", fake_module)
    adapter = LightAdapter(AppSettings(auto_reconnect=False))
    adapter.connect()
    await wait_for(lambda: adapter.state is ConnectionState.CONNECTED)
    light = FakeLight.instances[-1]

    assert adapter.try_set_ramp_brightness(10)
    assert not adapter.try_set_ramp_brightness(20)
    assert not adapter.try_set_ramp_brightness(30)
    await wait_for(lambda: not adapter.command_busy)
    await asyncio.sleep(0.06)
    assert adapter.try_set_ramp_brightness(30)
    await wait_for(lambda: not adapter.command_busy)

    assert [command[2] for command in light.commands] == [10, 30]
    assert SlowFakeLight.maximum_active == 1
    assert adapter._status.last_command_confirmed_at is not None
    assert adapter._status.last_command_duration_ms is not None
    assert adapter._status.last_command_duration_ms >= 40
    await adapter.shutdown()


@pytest.mark.asyncio
async def test_stalled_light_command_disconnects_cleanly(qtbot, monkeypatch) -> None:
    class HangingFakeLight(FakeLight):
        async def set_cct(self, kelvin: int, brightness: int, gm: int = 50) -> None:
            await asyncio.sleep(1.0)

    FakeLight.instances.clear()
    monkeypatch.setattr(light_module, "LIGHT_COMMAND_TIMEOUT_SECONDS", 0.02)
    monkeypatch.setitem(
        sys.modules,
        "neewerlite",
        SimpleNamespace(NeewerScanner=FakeScanner, NeewerLight=HangingFakeLight),
    )
    adapter = LightAdapter(AppSettings(auto_reconnect=False))
    adapter.connect()
    await wait_for(lambda: adapter.state is ConnectionState.CONNECTED)
    light = FakeLight.instances[-1]

    adapter.set_brightness(25)
    await wait_for(lambda: adapter.state is ConnectionState.ERROR)

    assert not light.client.is_connected
    assert not adapter._status.connected
    assert not adapter.command_busy
    await adapter.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["disconnect", "shutdown", "timeout", "failure"])
async def test_incomplete_connection_releases_client(qtbot, monkeypatch, action) -> None:
    started = asyncio.Event()

    class IncompleteLight(FakeLight):
        disconnected = False

        async def connect(self) -> None:
            self.client.is_connected = True
            started.set()
            if action == "failure":
                raise RuntimeError("Could not start notify on 000A: Unreachable")
            await asyncio.Event().wait()

        async def disconnect(self) -> None:
            # Exercise asynchronous cleanup before considering Disconnect complete.
            await asyncio.sleep(0.01)
            await super().disconnect()
            self.disconnected = True

    monkeypatch.setitem(
        sys.modules,
        "neewerlite",
        SimpleNamespace(NeewerScanner=FakeScanner, NeewerLight=IncompleteLight),
    )
    if action == "timeout":
        monkeypatch.setattr(light_module, "LIGHT_CONNECT_TIMEOUT_SECONDS", 0.02)
    adapter = LightAdapter(AppSettings(auto_reconnect=False))
    errors = []
    adapter.error.connect(errors.append)
    adapter.connect()
    await started.wait()
    light = FakeLight.instances[-1]

    if action == "disconnect":
        adapter.disconnect()
        await wait_for(lambda: adapter.state is ConnectionState.DISCONNECTED)
    elif action == "shutdown":
        await adapter.shutdown()
    else:
        await wait_for(lambda: adapter.state is ConnectionState.ERROR)
        assert errors
        assert "GATT" in errors[-1] if action == "failure" else "Zeitüberschreitung" in errors[-1]

    assert light.disconnected
    assert not light.client.is_connected
    assert adapter._light is None
    assert not adapter._status.connected
    assert not light.commands
    await adapter.shutdown()


@pytest.mark.asyncio
async def test_two_panels_serialize_scans_and_connections(qtbot, monkeypatch) -> None:
    operations = []

    class RecordingScanner:
        @staticmethod
        async def scan(timeout=5.0):
            operations.append("scan-start")
            await asyncio.sleep(0.01)
            operations.append("scan-end")
            return [
                SimpleNamespace(name="NEEWER-RGB660", address="AA:01"),
                SimpleNamespace(name="NEEWER-RGB660", address="AA:02"),
            ]

    class RecordingLight(FakeLight):
        async def connect(self):
            operations.append(("connect-start", self.address.address))
            await asyncio.sleep(0.01)
            await super().connect()
            operations.append(("connect-end", self.address.address))

    monkeypatch.setitem(
        sys.modules,
        "neewerlite",
        SimpleNamespace(NeewerScanner=RecordingScanner, NeewerLight=RecordingLight),
    )
    config = AppSettings(light_address="AA:01", light_2_address="AA:02", auto_reconnect=False)
    first = LightAdapter(config)
    second = LightAdapter(config, address_attribute="light_2_address")
    try:
        first.connect()
        first.connect()  # Repeated clicks must not open another session.
        second.connect()
        await wait_for(
            lambda: (
                first.state is ConnectionState.CONNECTED
                and second.state is ConnectionState.CONNECTED
            )
        )
        assert operations == [
            "scan-start",
            "scan-end",
            ("connect-start", "AA:01"),
            ("connect-end", "AA:01"),
            "scan-start",
            "scan-end",
            ("connect-start", "AA:02"),
            ("connect-end", "AA:02"),
        ]
    finally:
        await first.shutdown()
        await second.shutdown()


def test_second_light_selection_excludes_first_address() -> None:
    config = AppSettings(light_address="AA:01", light_2_address="AA:02")
    second = LightAdapter(
        config,
        display_name="Neewer-Licht 2",
        address_attribute="light_2_address",
        excluded_addresses=lambda: {config.light_address},
    )
    devices = [
        DiscoveredLight("NEEWER-RGB660 PRO", "AA:01", -30),
        DiscoveredLight("NEEWER-RGB660 PRO", "AA:02", -60),
    ]

    assert second._select(devices).address == "AA:02"


def test_unconfigured_second_light_does_not_select_first_address() -> None:
    config = AppSettings(light_address="AA:01")
    second = LightAdapter(
        config,
        address_attribute="light_2_address",
        excluded_addresses=lambda: {config.light_address},
    )
    devices = [
        DiscoveredLight("NEEWER-RGB660 PRO", "AA:01", -20),
        DiscoveredLight("NEEWER-RGB660 PRO", "AA:02", -50),
    ]

    assert second._select(devices).address == "AA:02"


def test_duplicate_saved_address_does_not_override_other_panel_exclusion() -> None:
    config = AppSettings(light_address="AA:01", light_2_address="AA:01")
    second = LightAdapter(
        config,
        address_attribute="light_2_address",
        excluded_addresses=lambda: {config.light_address},
    )
    devices = [
        DiscoveredLight("NEEWER-RGB660 PRO", "AA:01", -20),
        DiscoveredLight("NEEWER-RGB660 PRO", "AA:02", -50),
    ]
    assert second._select(devices).address == "AA:02"
