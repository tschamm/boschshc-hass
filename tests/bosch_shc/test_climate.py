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

PR #329 (jumlu) — direction/regulation axis split:
       hvac_mode maps the direction axis (HEAT/COOL/OFF).
       preset_mode maps the regulation axis (auto/manual/boost/eco).
       AUTO is no longer an hvac_mode; it is expressed as preset "auto".
"""

import asyncio
from types import SimpleNamespace

import pytest

from boschshcpy.exceptions import JSONRPCError, SHCException
from custom_components.bosch_shc.climate import (
    ClimateControl,
    PRESET_AUTO,
    PRESET_MANUAL,
    PRESET_BOOST,
    PRESET_ECO,
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
    # device_name reads _room_label; primary entity has no own name.
    entity._room_label = "Test Climate"
    entity._attr_name = None
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
    """preset_mode == PRESET_ECO when device.low is True.

    P2-B (#196): the old guard that silently skipped setpoint writes in ECO
    mode has been removed.  async_set_hvac_mode (called first in
    async_set_temperature) already clears low=False, but put_state_element
    does NOT update the in-memory _raw_state cache, so the stale ECO check
    would always block the write.  The fix: only skip the write when truly OFF.
    """

    def test_preset_mode_eco_when_low(self):
        device = _make_device(low=True)
        entity = _make_entity(device)
        assert entity.preset_mode == PRESET_ECO

    def test_preset_mode_auto_when_not_low_not_boost(self):
        # Default _make_device uses operation_mode_value="AUTOMATIC" → preset "auto"
        device = _make_device(low=False, boost_mode=False, operation_mode_value="AUTOMATIC")
        entity = _make_entity(device)
        assert entity.preset_mode == PRESET_AUTO

    def test_eco_set_temperature_writes_setpoint(self):
        """P2-B: ECO no longer blocks setpoint writes — setpoint IS written. #196"""
        device = _make_device(low=True, operation_mode_value="MANUAL")
        entity = _make_entity(device)
        written = {}

        async def _executor(func, *args):
            if func is setattr and len(args) == 3:
                written[args[1]] = args[2]
            return func(*args)

        entity.hass = _make_hass(_executor)
        _run_async(entity.async_set_temperature(**{ATTR_TEMPERATURE: 19.0}))
        # Setpoint must be written even from ECO state (guard removed)
        assert written.get("setpoint_temperature") == 19.0, (
            "setpoint_temperature must be written in ECO mode after P2-B fix"
        )

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

    def test_hvac_mode_heat_when_not_cooling_not_summer(self):
        # PR #329: AUTO is no longer an hvac_mode; AUTOMATIC operation_mode → preset "auto"
        # but hvac_mode is still HEAT (direction axis not affected by regulation mode)
        from homeassistant.components.climate.const import HVACMode
        device = _make_device_cooling(supports_cooling=True, cooling_mode=False,
                                      operation_mode_value="AUTOMATIC")
        entity = _make_entity(device)
        assert entity.hvac_mode == HVACMode.HEAT

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

    def test_hvac_modes_does_not_include_auto(self):
        # PR #329: AUTO is no longer an hvac_mode; it is the "auto" preset
        from homeassistant.components.climate.const import HVACMode
        device = _make_device_cooling(supports_cooling=True)
        entity = _make_entity(device)
        assert HVACMode.AUTO not in entity.hvac_modes

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


# ---------------------------------------------------------------------------
# PR #329 (jumlu) — direction/regulation axis split
# hvac_mode = direction axis; preset_mode = regulation axis
# ---------------------------------------------------------------------------

class TestHvacModeDirectionAxis:
    """hvac_mode maps the Bosch direction axis: summer→OFF, cooling→COOL, else→HEAT."""

    def test_hvac_mode_heat(self):
        from homeassistant.components.climate.const import HVACMode
        device = _make_device(summer_mode=False, cooling_mode=False, supports_cooling=False)
        entity = _make_entity(device)
        assert entity.hvac_mode == HVACMode.HEAT

    def test_hvac_mode_cool(self):
        from homeassistant.components.climate.const import HVACMode
        device = _make_device(summer_mode=False, cooling_mode=True, supports_cooling=True)
        entity = _make_entity(device)
        assert entity.hvac_mode == HVACMode.COOL

    def test_hvac_mode_off(self):
        from homeassistant.components.climate.const import HVACMode
        device = _make_device(summer_mode=True)
        entity = _make_entity(device)
        assert entity.hvac_mode == HVACMode.OFF

    def test_hvac_modes_heat_and_off_always_present(self):
        from homeassistant.components.climate.const import HVACMode
        device = _make_device(supports_cooling=False)
        entity = _make_entity(device)
        assert HVACMode.HEAT in entity.hvac_modes
        assert HVACMode.OFF in entity.hvac_modes

    def test_hvac_modes_cool_only_when_supported(self):
        from homeassistant.components.climate.const import HVACMode
        device_with = _make_device(supports_cooling=True)
        device_without = _make_device(supports_cooling=False)
        assert HVACMode.COOL in _make_entity(device_with).hvac_modes
        assert HVACMode.COOL not in _make_entity(device_without).hvac_modes

    def test_hvac_modes_no_auto(self):
        from homeassistant.components.climate.const import HVACMode
        device = _make_device(supports_cooling=True)
        entity = _make_entity(device)
        assert HVACMode.AUTO not in entity.hvac_modes


class TestPresetModeRegulationAxis:
    """preset_mode maps the Bosch regulation axis: boost/eco override AUTOMATIC/MANUAL."""

    def test_preset_mode_auto(self):
        device = _make_device(operation_mode_value="AUTOMATIC", boost_mode=False, low=False)
        entity = _make_entity(device)
        assert entity.preset_mode == PRESET_AUTO

    def test_preset_mode_manual(self):
        device = _make_device(operation_mode_value="MANUAL", boost_mode=False, low=False)
        entity = _make_entity(device)
        assert entity.preset_mode == PRESET_MANUAL

    def test_preset_mode_boost_takes_priority(self):
        # boost_mode=True wins over operation_mode
        device = _make_device(boost_mode=True, low=False, operation_mode_value="MANUAL")
        entity = _make_entity(device)
        assert entity.preset_mode == PRESET_BOOST

    def test_preset_mode_eco(self):
        device = _make_device(low=True, boost_mode=False, operation_mode_value="MANUAL")
        entity = _make_entity(device)
        assert entity.preset_mode == PRESET_ECO

    def test_preset_modes_always_includes_auto_and_manual(self):
        device = _make_device()
        entity = _make_entity(device)
        assert PRESET_AUTO in entity.preset_modes
        assert PRESET_MANUAL in entity.preset_modes

    def test_preset_modes_includes_boost_when_supported(self):
        device = _make_device(supports_boost_mode=True)
        entity = _make_entity(device)
        assert PRESET_BOOST in entity.preset_modes

    def test_preset_modes_eco_only_when_device_has_low(self):
        # Device with `low` attribute → eco offered
        device = _make_device(low=False)
        entity = _make_entity(device)
        assert PRESET_ECO in entity.preset_modes

    def test_eco_not_offered_without_low_attr(self):
        # Device without `low` attribute → eco not offered
        from types import SimpleNamespace
        from boschshcpy.services_impl import RoomClimateControlService
        op = RoomClimateControlService.OperationMode("AUTOMATIC")
        device = SimpleNamespace(
            boost_mode=False,
            summer_mode=False,
            supports_boost_mode=False,
            supports_cooling=False,
            cooling_mode=False,
            setpoint_temperature=20.0,
            temperature=19.0,
            operation_mode=op,
            root_device_id="test-root",
            id="test-id",
            # NOTE: no `low` attribute
        )
        entity = _make_entity(device)
        assert PRESET_ECO not in entity.preset_modes


class TestSetHvacModeNew:
    """Tests for async_set_hvac_mode with the direction-axis design."""

    def test_set_hvac_mode_heat_sets_summer_false(self):
        from homeassistant.components.climate.const import HVACMode
        device = _make_device(summer_mode=True, supports_cooling=False)
        entity = _make_entity(device)
        written = {}

        async def _executor(func, *args):
            if func is setattr and len(args) == 3:
                written[args[1]] = args[2]
            else:
                func(*args)

        entity.hass = _make_hass(_executor)
        _run_async(entity.async_set_hvac_mode(HVACMode.HEAT))
        assert written.get("summer_mode") is False

    def test_set_hvac_mode_cool_sets_cooling_true(self):
        from homeassistant.components.climate.const import HVACMode
        device = _make_device(summer_mode=False, cooling_mode=False, supports_cooling=True)
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

    def test_set_hvac_mode_off_sets_summer_true(self):
        from homeassistant.components.climate.const import HVACMode
        device = _make_device(summer_mode=False, supports_cooling=False)
        entity = _make_entity(device)
        written = {}

        async def _executor(func, *args):
            if func is setattr and len(args) == 3:
                written[args[1]] = args[2]
            else:
                func(*args)

        entity.hass = _make_hass(_executor)
        _run_async(entity.async_set_hvac_mode(HVACMode.OFF))
        assert written.get("summer_mode") is True

    def test_set_hvac_mode_exits_eco_first(self):
        """When device.low=True, set_hvac_mode must clear low before direction change."""
        from homeassistant.components.climate.const import HVACMode
        device = _make_device(low=True, summer_mode=False, supports_cooling=False,
                              operation_mode_value="MANUAL")
        entity = _make_entity(device)
        call_order = []

        async def _executor(func, *args):
            if func is setattr and len(args) == 3:
                call_order.append(args[1])
            func(*args)  # actually apply so preset_mode re-reads correctly

        entity.hass = _make_hass(_executor)
        _run_async(entity.async_set_hvac_mode(HVACMode.OFF))
        # "low" must appear before "summer_mode" in the call order
        assert "low" in call_order, "low must be cleared when exiting ECO"
        low_idx = call_order.index("low")
        summer_idx = call_order.index("summer_mode")
        assert low_idx < summer_idx, "low=False must be written before summer_mode=True"


class TestSetPresetModeNew:
    """Tests for async_set_preset_mode with the regulation-axis design."""

    def test_set_preset_boost(self):
        device = _make_device(boost_mode=False, supports_boost_mode=True)
        entity = _make_entity(device)
        written = {}

        async def _executor(func, *args):
            if func is setattr and len(args) == 3:
                written[args[1]] = args[2]
            else:
                func(*args)

        entity.hass = _make_hass(_executor)
        _run_async(entity.async_set_preset_mode(PRESET_BOOST))
        assert written.get("boost_mode") is True

    def test_set_preset_eco(self):
        device = _make_device(low=False, supports_boost_mode=True)
        entity = _make_entity(device)
        written = {}

        async def _executor(func, *args):
            if func is setattr and len(args) == 3:
                written[args[1]] = args[2]
            else:
                func(*args)

        entity.hass = _make_hass(_executor)
        _run_async(entity.async_set_preset_mode(PRESET_ECO))
        assert written.get("low") is True

    def test_set_preset_auto_sets_operation_mode(self):
        from boschshcpy.services_impl import RoomClimateControlService
        device = _make_device(operation_mode_value="MANUAL", boost_mode=False, low=False)
        entity = _make_entity(device)
        written = {}

        async def _executor(func, *args):
            if func is setattr and len(args) == 3:
                written[args[1]] = args[2]
            else:
                func(*args)

        entity.hass = _make_hass(_executor)
        _run_async(entity.async_set_preset_mode(PRESET_AUTO))
        assert written.get("operation_mode") == (
            RoomClimateControlService.OperationMode.AUTOMATIC
        )

    def test_set_preset_manual_sets_operation_mode(self):
        from boschshcpy.services_impl import RoomClimateControlService
        device = _make_device(operation_mode_value="AUTOMATIC", boost_mode=False, low=False)
        entity = _make_entity(device)
        written = {}

        async def _executor(func, *args):
            if func is setattr and len(args) == 3:
                written[args[1]] = args[2]
            else:
                func(*args)

        entity.hass = _make_hass(_executor)
        _run_async(entity.async_set_preset_mode(PRESET_MANUAL))
        assert written.get("operation_mode") == (
            RoomClimateControlService.OperationMode.MANUAL
        )

    def test_set_preset_invalid_ignored(self):
        device = _make_device()
        entity = _make_entity(device)
        written = {}

        async def _executor(func, *args):
            if func is setattr and len(args) == 3:
                written[args[1]] = args[2]

        entity.hass = _make_hass(_executor)
        _run_async(entity.async_set_preset_mode("nonexistent_preset"))
        assert written == {}, "Unknown preset must not write anything"

    def test_set_preset_auto_clears_boost_first(self):
        """PRESET_AUTO with boost active: clears boost_mode before writing AUTOMATIC."""
        from boschshcpy.services_impl import RoomClimateControlService
        device = _make_device(boost_mode=True, low=False,
                              supports_boost_mode=True, operation_mode_value="MANUAL")
        entity = _make_entity(device)
        written = {}

        async def _executor(func, *args):
            if func is setattr and len(args) == 3:
                written[args[1]] = args[2]
            else:
                func(*args)

        entity.hass = _make_hass(_executor)
        _run_async(entity.async_set_preset_mode(PRESET_AUTO))
        assert written.get("boost_mode") is False
        assert written.get("operation_mode") == (
            RoomClimateControlService.OperationMode.AUTOMATIC
        )

    def test_set_preset_auto_clears_low_first(self):
        """PRESET_AUTO with eco active: clears low before writing AUTOMATIC."""
        from boschshcpy.services_impl import RoomClimateControlService
        device = _make_device(boost_mode=False, low=True,
                              supports_boost_mode=True, operation_mode_value="MANUAL")
        entity = _make_entity(device)
        written = {}

        async def _executor(func, *args):
            if func is setattr and len(args) == 3:
                written[args[1]] = args[2]
            else:
                func(*args)

        entity.hass = _make_hass(_executor)
        _run_async(entity.async_set_preset_mode(PRESET_AUTO))
        assert written.get("low") is False
        assert written.get("operation_mode") == (
            RoomClimateControlService.OperationMode.AUTOMATIC
        )


class TestHvacActionNew:
    """hvac_action: COOLING when cooling active, OFF when summer_mode, else HEATING/IDLE."""

    def test_hvac_action_cooling(self):
        from homeassistant.components.climate.const import HVACAction
        device = _make_device(summer_mode=False, cooling_mode=True, supports_cooling=True)
        entity = _make_entity(device)
        assert entity.hvac_action == HVACAction.COOLING

    def test_hvac_action_off_when_summer_mode(self):
        from homeassistant.components.climate.const import HVACAction
        device = _make_device(summer_mode=True)
        entity = _make_entity(device)
        assert entity.hvac_action == HVACAction.OFF
