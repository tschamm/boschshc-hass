"""Unit tests for SHCZigbeeRoutingCoordinator (coordinator.py).

Exercises _async_update_data directly — the DataUpdateCoordinator base class'
own scheduling/refresh machinery is HA's, not ours, so these tests focus on
this integration's own logic: which devices are polled, and that one
device's failure doesn't take down the whole refresh.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from boschshcpy.exceptions import SHCConnectionError, SHCException

from custom_components.bosch_shc.coordinator import SHCZigbeeRoutingCoordinator


def _run(coro):
    return asyncio.run(coro)


def _fake_device(device_id: str) -> SimpleNamespace:
    return SimpleNamespace(id=device_id)


def _make_coordinator(session) -> SHCZigbeeRoutingCoordinator:
    """Build a coordinator without going through full HA setup — hass/entry
    are only touched by DataUpdateCoordinator.__init__ (attribute storage +
    entry.async_on_unload registration), not by _async_update_data."""
    hass = MagicMock()
    entry = MagicMock()
    return SHCZigbeeRoutingCoordinator(hass, entry, session)


class TestAsyncUpdateData:
    """_async_update_data: which devices get polled, and failure isolation."""

    def test_only_zigbee_prefixed_devices_are_polled(self):
        zb = _fake_device("hdm:ZigBee:001")
        other = _fake_device("hdm:SC2:001")
        routing_info = SimpleNamespace(aggregated_quality=SimpleNamespace(name="GOOD"))
        session = SimpleNamespace(
            devices=[zb, other],
            get_zigbee_routing_info=AsyncMock(return_value=routing_info),
        )
        coordinator = _make_coordinator(session)

        result = _run(coordinator._async_update_data())

        assert result == {"hdm:ZigBee:001": routing_info}
        session.get_zigbee_routing_info.assert_awaited_once_with("hdm:ZigBee:001")

    def test_no_zigbee_devices_yields_empty_dict(self):
        session = SimpleNamespace(
            devices=[_fake_device("hdm:SC2:001")],
            get_zigbee_routing_info=AsyncMock(),
        )
        coordinator = _make_coordinator(session)

        result = _run(coordinator._async_update_data())

        assert result == {}
        session.get_zigbee_routing_info.assert_not_awaited()

    def test_one_device_failure_does_not_fail_the_others(self):
        """A single offline mesh node must not take every other Zigbee sensor
        unavailable with it — its id is simply omitted from the result."""
        ok_device = _fake_device("hdm:ZigBee:ok")
        bad_device = _fake_device("hdm:ZigBee:bad")
        routing_info = SimpleNamespace(aggregated_quality=SimpleNamespace(name="GOOD"))

        async def _get(device_id: str):
            if device_id == "hdm:ZigBee:bad":
                raise SHCException("offline")
            return routing_info

        session = SimpleNamespace(
            devices=[ok_device, bad_device], get_zigbee_routing_info=_get
        )
        coordinator = _make_coordinator(session)

        result = _run(coordinator._async_update_data())

        assert result == {"hdm:ZigBee:ok": routing_info}
        assert "hdm:ZigBee:bad" not in result

    def test_connection_error_is_also_isolated_per_device(self):
        async def _get(device_id: str):
            raise SHCConnectionError("unreachable")

        session = SimpleNamespace(
            devices=[_fake_device("hdm:ZigBee:001")], get_zigbee_routing_info=_get
        )
        coordinator = _make_coordinator(session)

        result = _run(coordinator._async_update_data())

        assert result == {}

    def test_no_devices_attribute_on_session_is_safe(self):
        session = SimpleNamespace()
        coordinator = _make_coordinator(session)

        result = _run(coordinator._async_update_data())

        assert result == {}

    def test_device_without_id_is_skipped(self):
        session = SimpleNamespace(
            devices=[SimpleNamespace()], get_zigbee_routing_info=AsyncMock()
        )
        coordinator = _make_coordinator(session)

        result = _run(coordinator._async_update_data())

        assert result == {}
        session.get_zigbee_routing_info.assert_not_awaited()
