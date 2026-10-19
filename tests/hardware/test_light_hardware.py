from __future__ import annotations

import asyncio
import os

import pytest
import qasync

from automated_image_capture.hardware.light import LightAdapter, _as_discovered, _looks_like_neewer
from automated_image_capture.hardware_lease import HardwareLease
from automated_image_capture.models import ConnectionState
from automated_image_capture.settings import AppSettings

pytestmark = [
    pytest.mark.hardware,
    pytest.mark.skipif(
        os.environ.get("RUN_HARDWARE_TESTS") != "1",
        reason="Set RUN_HARDWARE_TESTS=1 for physical hardware tests",
    ),
]


def test_rgb660_scan_and_connect_without_changing_output(qapp) -> None:
    lease = HardwareLease.acquire()
    assert lease is not None, "Close the dashboard before running the BLE hardware test"
    with lease, qasync.QEventLoop(qapp) as loop:
        loop.run_until_complete(_check_rgb660_connections())


async def _check_rgb660_connections() -> None:
    from neewerlite import NeewerScanner

    try:
        raw_devices = await NeewerScanner.scan(timeout=5.0)
    except TypeError:
        raw_devices = await NeewerScanner.scan()
    devices = [_as_discovered(device) for device in raw_devices]
    candidates = [device for device in devices if _looks_like_neewer(device)]
    assert candidates, "No RGB660/NEEWER light discovered"
    config = AppSettings(auto_reconnect=False)
    adapters = []
    for number, selected in enumerate(candidates[:2]):
        attribute = "light_address" if number == 0 else "light_2_address"
        setattr(config, attribute, selected.address)
        adapters.append(LightAdapter(config, address_attribute=attribute))
    try:
        for adapter in adapters:
            adapter.connect()
        async with asyncio.timeout(45.0):
            while any(adapter.state is not ConnectionState.CONNECTED for adapter in adapters):
                assert all(adapter.state is not ConnectionState.ERROR for adapter in adapters)
                await asyncio.sleep(0.05)
        await asyncio.sleep(1.0)
        for adapter in adapters:
            adapter._check_connection()
            assert adapter.state is ConnectionState.CONNECTED
            assert adapter._status.connected
            assert adapter._status.power is not None
            assert not adapter._status.values_are_confirmed_commands
    finally:
        for adapter in adapters:
            await adapter.shutdown()
            assert adapter.state is ConnectionState.DISCONNECTED
