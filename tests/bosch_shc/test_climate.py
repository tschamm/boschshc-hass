"""Regression tests for climate.py fixes.

#273 — Setting temperature in BOOST mode must not surface an unhandled
       exception. The SHC returns HTTP 400 / WRONG_THERMOSTAT_GROUP_MODE,
       which boschshcpy raises as JSONRPCError (or SHCException). The HA
       entity must:
         (a) return early with a warning before ever writing the setpoint, and
         (b) catch JSONRPCError/SHCException even if the early-return is missed.

#242 — ECO / low preset: TRV_GEN2 is controlled via ROOM_CLIMATE_CONTROL.
       Setting low=True on that entity is the correct path. Covered here by
       verifying the preset_mode property and the temperature-skip in ECO.

#253 — TRV_I is not in boschshcpy MODEL_MAPPING; no climate entity is created
       by design (the device is not supported in boschshcpy). Documented, no
       code change needed.
"""

import asyncio
from types import SimpleNamespace

import pytest

from boschshcpy.exceptions import JSONRPCError, SHCException
from custom_components.bosch_shc.climate import ClimateControl
from homeassistant.components.climate.const import (
    PRESET_BOOST,
    PRESET_ECO,
    PRESET_NONE,
)
from homeassistant.const import ATTR_TEMPERATURE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_device(
    *,
    boost_mode=False,
    low=False,
    summer_mode=False,
    supports_boost_mode=True,
    setpoint_temperature=20.0,
    temperature=19.0,
    operation_mode_value="AUTOMATIC",
    supports_cooling=False,
    cooling_mode=False,
):
    from boschshcpy.services_impl import RoomClimateControlService

    op = RoomClimateControlService.OperationMode(operation_mode_value)
    return SimpleNamespace(
        boost_mode=boost_mode,
        low=low,
        summer_mode=summer_mode,
        supports_boost_mode=supports_boost_mode,
        setpoint_temperature=setpoint_temperature,
        temperature=temperature,
        operation_mode=op,
        supports_cooling=supports_cooling,
        cooling_mode=cooling_mode,
        root_device_id="test-root",
        id="test-id",
    )


def _make_entity(device):
    """Build a ClimateControl without going through __init__ (no HA session)."""
    entity = ClimateControl.__new__(ClimateControl)
    entity._device = device
    entity._name = "Test Climate"
    entity._attr_unique_id = "test-root_test-id"
    return entity


def _run_async(coro):
    """Run an async coroutine synchronously (no pytest-asyncio needed)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_hass(executor_func=None):
    """Build a minimal hass stub with a configurable executor."""
    if executor_func is None:
        async def executor_func(func, *args):
            return func(*args)
    return SimpleNamespace(async_add_executor_job=executor_func)


# ---------------------------------------------------------------------------
# #273 — BOOST mode: early-return guard (no write at all)
# ---------------------------------------------------------------------------

class TestBoostPresetGuard:
    """When preset_mode == PRESET_BOOST, async_set_temperature must return
    early — the setpoint setter must never be called."""

    def _entity_with_write_tracker(self):
        """Return (entity, written_list). Any setattr call appends to written."""
        device = _make_device(boost_mode=True, operation_mode_value="AUTOMATIC")
        entity = _make_entity(device)
        written = []

        async def _executor(func, *args):
            # Record the setattr target attribute
            if func is setattr and len(args) == 3:
                written.append(args[1])  # attribute name
            return func(*args)

        entity.hass = _make_hass(_executor)
        return entity, written

    def test_preset_is_boost(self):
        device = _make_device(boost_mode=True)
        entity = _make_entity(device)
        assert entity.preset_mode == PRESET_BOOST

    def test_boost_set_temp_does_not_call_executor(self):
        entity, written = self._entity_with_write_tracker()
        _run_async(entity.async_set_temperature(**{ATTR_TEMPERATURE: 22.0}))
        assert "setpoint_temperature" not in written, (
            "setpoint_temperature must NOT be written while in BOOST"
        )

    def test_boost_set_temp_does_not_raise(self):
        entity, _ = self._entity_with_write_tracker()
        try:
            _run_async(entity.async_set_temperature(**{ATTR_TEMPERATURE: 22.0}))
        except Exception as exc:
            pytest.fail(f"async_set_temperature raised in BOOST: {exc!r}")

    def test_boost_set_temp_no_temperature_arg_does_not_raise(self):
        entity, _ = self._entity_with_write_tracker()
        _run_async(entity.async_set_temperature())  # no ATTR_TEMPERATURE


# ---------------------------------------------------------------------------
# #273 — JSONRPCError swallowed (safety net catch)
# ---------------------------------------------------------------------------

class TestBoostJsonRpcErrorSwallowed:
    """If the SHC returns HTTP 400, the JSONRPCError must be caught;
    no unhandled exception propagates to HA."""

    def test_jsonrpc_error_is_swallowed(self):
        device = _make_device(boost_mode=False, low=False, operation_mode_value="MANUAL")
        entity = _make_entity(device)

        call_count = [0]

        async def _executor(func, *args):
            call_count[0] += 1
            if call_count[0] == 1:
                raise JSONRPCError(400, "WRONG_THERMOSTAT_GROUP_MODE")
            return func(*args)

        entity.hass = _make_hass(_executor)

        try:
            _run_async(entity.async_set_temperature(**{ATTR_TEMPERATURE: 22.0}))
        except JSONRPCError as exc:
            pytest.fail(f"JSONRPCError was not caught: {exc!r}")

    def test_shcexception_is_swallowed(self):
        device = _make_device(boost_mode=False, low=False, operation_mode_value="MANUAL")
        entity = _make_entity(device)

        async def _executor(func, *args):
            raise SHCException("generic SHC error")

        entity.hass = _make_hass(_executor)

        try:
            _run_async(entity.async_set_temperature(**{ATTR_TEMPERATURE: 21.0}))
        except SHCException as exc:
            pytest.fail(f"SHCException was not caught: {exc!r}")


# ---------------------------------------------------------------------------
# #242 — ECO / low preset: verified by design
# ---------------------------------------------------------------------------

class TestEcoPreset:
    """preset_mode == PRESET_ECO when device.low is True; temperature write
    is skipped in ECO mode."""

    def test_preset_mode_eco_when_low(self):
        device = _make_device(low=True)
        entity = _make_entity(device)
        assert entity.preset_mode == PRESET_ECO

    def test_preset_mode_none_when_not_low_not_boost(self):
        device = _make_device(low=False, boost_mode=False)
        entity = _make_entity(device)
        assert entity.preset_mode == PRESET_NONE

    def test_eco_skips_set_temperature(self):
        device = _make_device(low=True, operation_mode_value="MANUAL")
        entity = _make_entity(device)
        executor_calls = []

        async def _executor(func, *args):
            executor_calls.append(func)
            return func(*args)

        entity.hass = _make_hass(_executor)
        _run_async(entity.async_set_temperature(**{ATTR_TEMPERATURE: 19.0}))
        assert executor_calls == [], "No executor call expected in ECO mode"

    def test_eco_does_not_raise(self):
        device = _make_device(low=True, operation_mode_value="MANUAL")
        entity = _make_entity(device)
        entity.hass = _make_hass()
        try:
            _run_async(entity.async_set_temperature(**{ATTR_TEMPERATURE: 19.0}))
        except Exception as exc:
            pytest.fail(f"async_set_temperature raised in ECO: {exc!r}")


# ---------------------------------------------------------------------------
# Normal HEAT mode: temperature written and rounded
# ---------------------------------------------------------------------------

class TestNormalHeatSetTemp:
    def _entity_capturing_writes(self):
        device = _make_device(
            boost_mode=False, low=False, summer_mode=False,
            operation_mode_value="MANUAL",
        )
        entity = _make_entity(device)
        written = []

        async def _executor(func, *args):
            if func is setattr and len(args) == 3:
                written.append(args[2])  # value written
            else:
                func(*args)

        entity.hass = _make_hass(_executor)
        return entity, written

    def test_heat_mode_sets_setpoint(self):
        entity, written = self._entity_capturing_writes()
        _run_async(entity.async_set_temperature(**{ATTR_TEMPERATURE: 21.5}))
        assert written == [21.5], f"Expected [21.5], got {written}"

    def test_temperature_rounded_to_half_degree(self):
        entity, written = self._entity_capturing_writes()
        _run_async(entity.async_set_temperature(**{ATTR_TEMPERATURE: 21.3}))
        assert written == [21.5], f"Expected [21.5] (rounded), got {written}"

    def test_temperature_below_min_not_written(self):
        entity, written = self._entity_capturing_writes()
        _run_async(entity.async_set_temperature(**{ATTR_TEMPERATURE: 4.0}))
        assert written == [], "Value below min_temp must not be written"

    def test_temperature_above_max_not_written(self):
        entity, written = self._entity_capturing_writes()
        _run_async(entity.async_set_temperature(**{ATTR_TEMPERATURE: 31.0}))
        assert written == [], "Value above max_temp must not be written"


# ---------------------------------------------------------------------------
# PR #304 — COOL HVACMode on ClimateControl (supports_cooling)
# ---------------------------------------------------------------------------

def _make_device_cooling(
    *,
    supports_cooling=True,
    cooling_mode=False,
    summer_mode=False,
    operation_mode_value="AUTOMATIC",
):
    from boschshcpy.services_impl import RoomClimateControlService

    op = RoomClimateControlService.OperationMode(operation_mode_value)
    return SimpleNamespace(
        boost_mode=False,
        low=False,
        summer_mode=summer_mode,
        supports_boost_mode=False,
        supports_cooling=supports_cooling,
        cooling_mode=cooling_mode,
        setpoint_temperature=20.0,
        temperature=19.0,
        operation_mode=op,
        root_device_id="test-root",
        id="test-id",
    )


class TestCoolingHvacMode:
    """PR #304: hvac_mode returns COOL when supports_cooling and cooling_mode."""

    def test_hvac_mode_cool_when_cooling_active(self):
        from homeassistant.components.climate.const import HVACMode
        device = _make_device_cooling(supports_cooling=True, cooling_mode=True)
        entity = _make_entity(device)
        assert entity.hvac_mode == HVACMode.COOL

    def test_hvac_mode_auto_when_not_cooling(self):
        from homeassistant.components.climate.const import HVACMode
        device = _make_device_cooling(supports_cooling=True, cooling_mode=False,
                                      operation_mode_value="AUTOMATIC")
        entity = _make_entity(device)
        assert entity.hvac_mode == HVACMode.AUTO

    def test_hvac_modes_includes_cool_when_supported(self):
        from homeassistant.components.climate.const import HVACMode
        device = _make_device_cooling(supports_cooling=True)
        entity = _make_entity(device)
        assert HVACMode.COOL in entity.hvac_modes

    def test_hvac_modes_excludes_cool_when_not_supported(self):
        from homeassistant.components.climate.const import HVACMode
        device = _make_device_cooling(supports_cooling=False)
        entity = _make_entity(device)
        assert HVACMode.COOL not in entity.hvac_modes

    def test_set_hvac_mode_cool_sets_cooling_mode(self):
        from homeassistant.components.climate.const import HVACMode
        device = _make_device_cooling(supports_cooling=True, cooling_mode=False)
        entity = _make_entity(device)
        written = {}

        async def _executor(func, *args):
            if func is setattr and len(args) == 3:
                written[args[1]] = args[2]
            else:
                func(*args)

        entity.hass = _make_hass(_executor)
        _run_async(entity.async_set_hvac_mode(HVACMode.COOL))
        assert written.get("cooling_mode") is True
        assert written.get("summer_mode") is False

    def test_set_hvac_mode_auto_clears_cooling_mode(self):
        from homeassistant.components.climate.const import HVACMode
        device = _make_device_cooling(supports_cooling=True, cooling_mode=True,
                                      operation_mode_value="AUTOMATIC")
        entity = _make_entity(device)
        written = {}

        async def _executor(func, *args):
            if func is setattr and len(args) == 3:
                written[args[1]] = args[2]
            else:
                func(*args)

        entity.hass = _make_hass(_executor)
        _run_async(entity.async_set_hvac_mode(HVACMode.AUTO))
        assert written.get("cooling_mode") is False

    def test_set_hvac_mode_heat_clears_cooling_mode(self):
        from homeassistant.components.climate.const import HVACMode
        device = _make_device_cooling(supports_cooling=True, cooling_mode=True,
                                      operation_mode_value="MANUAL")
        entity = _make_entity(device)
        written = {}

        async def _executor(func, *args):
            if func is setattr and len(args) == 3:
                written[args[1]] = args[2]
            else:
                func(*args)

        entity.hass = _make_hass(_executor)
        _run_async(entity.async_set_hvac_mode(HVACMode.HEAT))
        assert written.get("cooling_mode") is False

    def test_set_hvac_mode_off_clears_cooling_mode(self):
        from homeassistant.components.climate.const import HVACMode
        device = _make_device_cooling(supports_cooling=True, cooling_mode=True)
        entity = _make_entity(device)
        written = {}

        async def _executor(func, *args):
            if func is setattr and len(args) == 3:
                written[args[1]] = args[2]
            else:
                func(*args)

        entity.hass = _make_hass(_executor)
        _run_async(entity.async_set_hvac_mode(HVACMode.OFF))
        assert written.get("cooling_mode") is False
        assert written.get("summer_mode") is True
