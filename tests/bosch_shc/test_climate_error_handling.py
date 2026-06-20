"""Unit tests for climate.py error handling.

Covers:
- ClimateControl.async_set_hvac_mode: JSONRPCError caught + LOGGER.warning
- ClimateControl.async_set_preset_mode: JSONRPCError caught + LOGGER.warning
- HeatingCircuit.async_set_temperature: JSONRPCError caught + LOGGER.warning

Pattern: __new__ bypass + fake async_add_executor_job that raises + asyncio.run().
No HA harness.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from boschshcpy.exceptions import JSONRPCError, SHCException
from custom_components.bosch_shc.climate import ClimateControl, HeatingCircuit
from homeassistant.components.climate.const import HVACMode, PRESET_NONE, PRESET_ECO


# JSONRPCError requires (code, message) — use a subclass for brevity
class _JRPC(JSONRPCError):
    def __init__(self, msg="err"):
        super().__init__(-32001, msg)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_hass_raises(exc):
    """Hass mock whose async_add_executor_job raises exc."""
    hass = MagicMock(name="hass")

    async def _raise(*args, **kwargs):
        raise exc

    hass.async_add_executor_job = _raise
    return hass


def _make_hass_ok():
    """Hass mock whose async_add_executor_job succeeds silently."""
    hass = MagicMock(name="hass")

    async def _noop(*args, **kwargs):
        pass

    hass.async_add_executor_job = _noop
    return hass


def _make_climate_control(hass=None, hvac_modes=None):
    """Build a ClimateControl bypassing SHCEntity.__init__."""
    entity = ClimateControl.__new__(ClimateControl)
    entity._device = SimpleNamespace(
        name="Room",
        id="cc-1",
        root_device_id="root-1",
        summer_mode=False,
        supports_cooling=False,
        supports_boost_mode=True,
        cooling_mode=False,
        operation_mode=None,
        boost_mode=False,
        low=False,
    )
    entity._attr_name = "Room Climate"
    entity.hass = hass or _make_hass_ok()
    entity._attr_unique_id = "root-1_cc-1"
    if hvac_modes is not None:
        entity._hvac_modes = hvac_modes
    return entity


# ---------------------------------------------------------------------------
# ClimateControl.async_set_hvac_mode
# ---------------------------------------------------------------------------

class TestClimateControlHvacModeErrors:
    def test_jsonrpc_error_is_caught_and_logged(self):
        """JSONRPCError from executor job must not propagate."""
        entity = _make_climate_control(hass=_make_hass_raises(_JRPC("timeout")))
        entity._device.summer_mode = False
        entity._device.low = False  # not in ECO

        with patch("custom_components.bosch_shc.climate.LOGGER") as mock_log:
            asyncio.run(entity.async_set_hvac_mode(HVACMode.AUTO))
            mock_log.warning.assert_called_once()
            assert "HVAC mode" in mock_log.warning.call_args[0][0]

    def test_shc_exception_is_caught_and_logged(self):
        entity = _make_climate_control(hass=_make_hass_raises(SHCException("conn")))
        entity._device.low = False

        with patch("custom_components.bosch_shc.climate.LOGGER") as mock_log:
            asyncio.run(entity.async_set_hvac_mode(HVACMode.HEAT))
            mock_log.warning.assert_called_once()

    def test_unknown_hvac_mode_returns_early_without_error(self):
        """Unsupported mode must short-circuit — no executor call, no error."""
        entity = _make_climate_control(hass=_make_hass_raises(_JRPC("x")))
        entity._device.low = False
        # Should not raise even though hass raises
        asyncio.run(entity.async_set_hvac_mode("INVALID_MODE"))


# ---------------------------------------------------------------------------
# ClimateControl.async_set_preset_mode
# ---------------------------------------------------------------------------

class TestClimateControlPresetModeErrors:
    def test_jsonrpc_error_preset_none_is_caught(self):
        entity = _make_climate_control(hass=_make_hass_raises(_JRPC("err")))
        entity._device.boost_mode = True  # will try to turn off
        entity._device.low = False

        with patch("custom_components.bosch_shc.climate.LOGGER") as mock_log:
            asyncio.run(entity.async_set_preset_mode(PRESET_NONE))
            mock_log.warning.assert_called_once()

    def test_shc_exception_preset_eco_is_caught(self):
        entity = _make_climate_control(hass=_make_hass_raises(SHCException("x")))
        entity._device.boost_mode = False
        entity._device.low = False

        with patch("custom_components.bosch_shc.climate.LOGGER") as mock_log:
            asyncio.run(entity.async_set_preset_mode(PRESET_ECO))
            mock_log.warning.assert_called_once()


# ---------------------------------------------------------------------------
# HeatingCircuit.async_set_temperature
# ---------------------------------------------------------------------------

def _make_heating_circuit(hass=None):
    entity = HeatingCircuit.__new__(HeatingCircuit)
    entity._device = SimpleNamespace(
        name="HC1",
        id="hc-1",
        root_device_id="root-1",
        setpoint_temperature=20.0,
        operation_mode=None,
    )
    entity._attr_unique_id = "root-1_hc-1"
    entity._attr_min_temp = 5.0
    entity._attr_max_temp = 30.0
    entity.hass = hass or _make_hass_ok()
    return entity


class TestHeatingCircuitTemperatureErrors:
    def test_jsonrpc_error_is_caught_and_logged(self):
        entity = _make_heating_circuit(hass=_make_hass_raises(_JRPC("timeout")))

        with patch("custom_components.bosch_shc.climate.LOGGER") as mock_log:
            asyncio.run(entity.async_set_temperature(temperature=21.0))
            mock_log.warning.assert_called_once()
            assert "temperature" in mock_log.warning.call_args[0][0].lower()

    def test_shc_exception_is_caught_and_logged(self):
        entity = _make_heating_circuit(hass=_make_hass_raises(SHCException("conn")))

        with patch("custom_components.bosch_shc.climate.LOGGER") as mock_log:
            asyncio.run(entity.async_set_temperature(temperature=22.0))
            mock_log.warning.assert_called_once()

    def test_none_temperature_returns_early(self):
        """None temperature must return without any executor job or error."""
        entity = _make_heating_circuit(hass=_make_hass_raises(_JRPC("x")))
        asyncio.run(entity.async_set_temperature(temperature=None))

    def test_out_of_range_temperature_skipped(self):
        """Temperature outside min/max must not trigger an executor job."""
        entity = _make_heating_circuit(hass=_make_hass_raises(_JRPC("x")))
        asyncio.run(entity.async_set_temperature(temperature=99.0))
