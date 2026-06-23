"""Unit tests for climate.py error handling.

Covers:
- ClimateControl.async_set_hvac_mode: JSONRPCError caught + LOGGER.warning
- ClimateControl.async_set_preset_mode: JSONRPCError caught + LOGGER.warning
- HeatingCircuit.async_set_temperature: JSONRPCError caught + LOGGER.warning
- HeatingCircuit.async_set_hvac_mode: JSONRPCError caught + LOGGER.warning (#273)

Pattern: __new__ bypass + AsyncMock device setters that raise + asyncio.run().
No HA harness.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from boschshcpy.exceptions import JSONRPCError, SHCException
from custom_components.bosch_shc.climate import (
    ClimateControl,
    HeatingCircuit,
    PRESET_BOOST,
    PRESET_ECO,
)
from homeassistant.components.climate.const import HVACMode


# JSONRPCError requires (code, message) — use a subclass for brevity
class _JRPC(JSONRPCError):
    def __init__(self, msg="err"):
        super().__init__(-32001, msg)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_climate_control(
    *, summer_mode=False, low=False, boost_mode=False, supports_eco=True
):
    """Build a ClimateControl bypassing SHCEntity.__init__."""
    entity = ClimateControl.__new__(ClimateControl)
    entity._device = SimpleNamespace(
        name="Room",
        id="cc-1",
        root_device_id="root-1",
        summer_mode=summer_mode,
        supports_cooling=False,
        supports_boost_mode=True,
        supports_eco=supports_eco,
        cooling_mode=False,
        operation_mode=None,
        boost_mode=boost_mode,
        low=low,
        async_set_low=AsyncMock(),
        async_set_summer_mode=AsyncMock(),
        async_set_cooling_mode=AsyncMock(),
        async_set_boost_mode=AsyncMock(),
        async_set_operation_mode=AsyncMock(),
        async_set_setpoint_temperature=AsyncMock(),
    )
    entity._attr_name = "Room Climate"
    entity._room_label = "Room Climate"
    entity._attr_unique_id = "root-1_cc-1"
    return entity


# ---------------------------------------------------------------------------
# ClimateControl.async_set_hvac_mode
# ---------------------------------------------------------------------------

class TestClimateControlHvacModeErrors:
    def test_jsonrpc_error_is_caught_and_logged(self):
        """JSONRPCError from async setter must not propagate.

        PR #329: AUTO is no longer an hvac_mode, use HEAT instead.
        """
        entity = _make_climate_control(summer_mode=False, low=False)
        entity._device.async_set_summer_mode = AsyncMock(side_effect=_JRPC("timeout"))

        with patch("custom_components.bosch_shc.climate.LOGGER") as mock_log:
            asyncio.run(entity.async_set_hvac_mode(HVACMode.HEAT))
            mock_log.warning.assert_called_once()
            assert "HVAC mode" in mock_log.warning.call_args[0][0]

    def test_shc_exception_is_caught_and_logged(self):
        entity = _make_climate_control(summer_mode=False, low=False)
        entity._device.async_set_summer_mode = AsyncMock(
            side_effect=SHCException("conn")
        )

        with patch("custom_components.bosch_shc.climate.LOGGER") as mock_log:
            asyncio.run(entity.async_set_hvac_mode(HVACMode.HEAT))
            mock_log.warning.assert_called_once()

    def test_unknown_hvac_mode_returns_early_without_error(self):
        """Unsupported mode must short-circuit — no async call, no error."""
        entity = _make_climate_control(low=False)
        entity._device.async_set_summer_mode = AsyncMock(side_effect=_JRPC("x"))
        # Should not raise even though async setter raises
        asyncio.run(entity.async_set_hvac_mode("INVALID_MODE"))
        entity._device.async_set_summer_mode.assert_not_awaited()


# ---------------------------------------------------------------------------
# ClimateControl.async_set_preset_mode
# ---------------------------------------------------------------------------

class TestClimateControlPresetModeErrors:
    def test_jsonrpc_error_preset_boost_is_caught(self):
        """#334: PRESET_BOOST sets boost_mode=True; JSONRPCError must be swallowed."""
        entity = _make_climate_control(boost_mode=False, low=False)
        entity._device.async_set_boost_mode = AsyncMock(side_effect=_JRPC("err"))

        with patch("custom_components.bosch_shc.climate.LOGGER") as mock_log:
            asyncio.run(entity.async_set_preset_mode(PRESET_BOOST))
            mock_log.warning.assert_called_once()

    def test_shc_exception_preset_eco_is_caught(self):
        entity = _make_climate_control(boost_mode=False, low=False)
        entity._device.async_set_low = AsyncMock(side_effect=SHCException("x"))

        with patch("custom_components.bosch_shc.climate.LOGGER") as mock_log:
            asyncio.run(entity.async_set_preset_mode(PRESET_ECO))
            mock_log.warning.assert_called_once()


# ---------------------------------------------------------------------------
# HeatingCircuit.async_set_temperature
# ---------------------------------------------------------------------------

def _make_heating_circuit():
    entity = HeatingCircuit.__new__(HeatingCircuit)
    entity._device = SimpleNamespace(
        name="HC1",
        id="hc-1",
        root_device_id="root-1",
        setpoint_temperature=20.0,
        operation_mode=None,
        async_set_setpoint_temperature=AsyncMock(),
        async_set_operation_mode=AsyncMock(),
    )
    entity._attr_unique_id = "root-1_hc-1"
    entity._attr_min_temp = 5.0
    entity._attr_max_temp = 30.0
    return entity


class TestHeatingCircuitTemperatureErrors:
    def test_jsonrpc_error_is_caught_and_logged(self):
        entity = _make_heating_circuit()
        entity._device.async_set_setpoint_temperature = AsyncMock(
            side_effect=_JRPC("timeout")
        )

        with patch("custom_components.bosch_shc.climate.LOGGER") as mock_log:
            asyncio.run(entity.async_set_temperature(temperature=21.0))
            mock_log.warning.assert_called_once()
            assert "temperature" in mock_log.warning.call_args[0][0].lower()

    def test_shc_exception_is_caught_and_logged(self):
        entity = _make_heating_circuit()
        entity._device.async_set_setpoint_temperature = AsyncMock(
            side_effect=SHCException("conn")
        )

        with patch("custom_components.bosch_shc.climate.LOGGER") as mock_log:
            asyncio.run(entity.async_set_temperature(temperature=22.0))
            mock_log.warning.assert_called_once()

    def test_none_temperature_returns_early(self):
        """None temperature must return without any async call or error."""
        entity = _make_heating_circuit()
        entity._device.async_set_setpoint_temperature = AsyncMock(
            side_effect=_JRPC("x")
        )
        asyncio.run(entity.async_set_temperature(temperature=None))
        entity._device.async_set_setpoint_temperature.assert_not_awaited()

    def test_out_of_range_temperature_skipped(self):
        """Temperature outside min/max must not trigger an async call."""
        entity = _make_heating_circuit()
        entity._device.async_set_setpoint_temperature = AsyncMock(
            side_effect=_JRPC("x")
        )
        asyncio.run(entity.async_set_temperature(temperature=99.0))
        entity._device.async_set_setpoint_temperature.assert_not_awaited()


# ---------------------------------------------------------------------------
# HeatingCircuit.async_set_hvac_mode — error handling (#273 / P1-D)
# ---------------------------------------------------------------------------

def _make_hc_entity():
    """HeatingCircuit bypassing SHCEntity.__init__."""
    from boschshcpy import SHCHeatingCircuit
    entity = HeatingCircuit.__new__(HeatingCircuit)
    entity._device = SimpleNamespace(
        name="HC1",
        id="hc-1",
        root_device_id="root-1",
        setpoint_temperature=20.0,
        operation_mode=SHCHeatingCircuit.HeatingCircuitService.OperationMode.AUTOMATIC,
        on=False,
        async_set_setpoint_temperature=AsyncMock(),
        async_set_operation_mode=AsyncMock(),
    )
    entity._attr_unique_id = "root-1_hc-1"
    entity._attr_min_temp = 5.0
    entity._attr_max_temp = 30.0
    return entity


class TestHeatingCircuitHvacModeErrors:
    """HeatingCircuit.async_set_hvac_mode must catch JSONRPCError / SHCException.

    Addresses #273 / P1-D: the old code had no try/except; a WRONG_THERMOSTAT_GROUP_MODE
    400 from the SHC would propagate unhandled into HA.
    """

    def test_jsonrpc_error_is_caught_and_logged(self):
        entity = _make_hc_entity()
        entity._device.async_set_operation_mode = AsyncMock(
            side_effect=_JRPC("timeout")
        )

        with patch("custom_components.bosch_shc.climate.LOGGER") as mock_log:
            asyncio.run(entity.async_set_hvac_mode("heat"))
            mock_log.warning.assert_called_once()
            assert "HeatingCircuit" in mock_log.warning.call_args[0][0]

    def test_shc_exception_is_caught_and_logged(self):
        entity = _make_hc_entity()
        entity._device.async_set_operation_mode = AsyncMock(
            side_effect=SHCException("conn")
        )

        with patch("custom_components.bosch_shc.climate.LOGGER") as mock_log:
            asyncio.run(entity.async_set_hvac_mode("heat"))
            mock_log.warning.assert_called_once()

    def test_invalid_mode_returns_early_no_executor(self):
        """Invalid mode must short-circuit before async call — no error."""
        entity = _make_hc_entity()
        entity._device.async_set_operation_mode = AsyncMock(side_effect=_JRPC("x"))
        # "off" is not in HeatingCircuit.hvac_modes → early return, no exception
        asyncio.run(entity.async_set_hvac_mode("off"))
        entity._device.async_set_operation_mode.assert_not_awaited()
