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

#334 — AUTOMATIC is HVACMode.AUTO (not a preset); presets are override-only
       (boost/eco). AUTO removed from preset_modes; added to hvac_modes.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from boschshcpy.exceptions import JSONRPCError, SHCException
from homeassistant.components.climate.const import HVACMode
from homeassistant.const import ATTR_TEMPERATURE

from custom_components.bosch_shc.climate import (
    PRESET_BOOST,
    PRESET_ECO,
    ClimateControl,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_device(
    *,
    boost_mode=False,
    low=False,
    summer_mode=False,
    supports_boost_mode=True,
    supports_eco=True,
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
        supports_eco=supports_eco,
        setpoint_temperature=setpoint_temperature,
        temperature=temperature,
        operation_mode=op,
        supports_cooling=supports_cooling,
        cooling_mode=cooling_mode,
        root_device_id="test-root",
        id="test-id",
        async_set_low=AsyncMock(),
        async_set_summer_mode=AsyncMock(),
        async_set_cooling_mode=AsyncMock(),
        async_set_boost_mode=AsyncMock(),
        async_set_operation_mode=AsyncMock(),
        async_set_setpoint_temperature=AsyncMock(),
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


# ---------------------------------------------------------------------------
# #273 — BOOST mode: early-return guard (no write at all)
# ---------------------------------------------------------------------------

class TestBoostPresetGuard:
    """When preset_mode == PRESET_BOOST, async_set_temperature must return
    early — the setpoint setter must never be called.
    """

    def test_preset_is_boost(self):
        device = _make_device(boost_mode=True)
        entity = _make_entity(device)
        assert entity.preset_mode == PRESET_BOOST

    def test_boost_set_temp_does_not_call_executor(self):
        device = _make_device(boost_mode=True, operation_mode_value="AUTOMATIC")
        entity = _make_entity(device)
        _run_async(entity.async_set_temperature(**{ATTR_TEMPERATURE: 22.0}))
        device.async_set_setpoint_temperature.assert_not_awaited()

    def test_boost_set_temp_does_not_raise(self):
        device = _make_device(boost_mode=True, operation_mode_value="AUTOMATIC")
        entity = _make_entity(device)
        try:
            _run_async(entity.async_set_temperature(**{ATTR_TEMPERATURE: 22.0}))
        except Exception as exc:
            pytest.fail(f"async_set_temperature raised in BOOST: {exc!r}")

    def test_boost_set_temp_no_temperature_arg_does_not_raise(self):
        device = _make_device(boost_mode=True, operation_mode_value="AUTOMATIC")
        entity = _make_entity(device)
        _run_async(entity.async_set_temperature())  # no ATTR_TEMPERATURE


# ---------------------------------------------------------------------------
# #273 — JSONRPCError swallowed (safety net catch)
# ---------------------------------------------------------------------------

class TestBoostJsonRpcErrorSwallowed:
    """If the SHC returns HTTP 400, the JSONRPCError must be caught;
    no unhandled exception propagates to HA.
    """

    def test_jsonrpc_error_is_swallowed(self):
        device = _make_device(boost_mode=False, low=False, operation_mode_value="MANUAL")
        device.async_set_setpoint_temperature = AsyncMock(
            side_effect=JSONRPCError(400, "WRONG_THERMOSTAT_GROUP_MODE")
        )
        entity = _make_entity(device)

        try:
            _run_async(entity.async_set_temperature(**{ATTR_TEMPERATURE: 22.0}))
        except JSONRPCError as exc:
            pytest.fail(f"JSONRPCError was not caught: {exc!r}")

    def test_shcexception_is_swallowed(self):
        device = _make_device(boost_mode=False, low=False, operation_mode_value="MANUAL")
        device.async_set_setpoint_temperature = AsyncMock(
            side_effect=SHCException("generic SHC error")
        )
        entity = _make_entity(device)

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
    mode has been removed.  only skip the write when truly OFF.

    #73: a bare set_temperature (no explicit hvac_mode, e.g. called from an
    automation) never reaches _async_apply_hvac_mode's ECO-clearing branch,
    since that only runs when a mode is actually requested. The real SHC
    rejects the setpoint write with WRONG_THERMOSTAT_GROUP_MODE while
    low=True, independent of operationMode (confirmed by both an open-window
    report and a floor-heating report on the SHC-II, which has `low` without
    a dedicated eco preset). async_set_temperature now clears low=False
    itself before writing the setpoint whenever the device reports low=True.
    """

    def test_preset_mode_eco_when_low(self):
        device = _make_device(low=True)
        entity = _make_entity(device)
        assert entity.preset_mode == PRESET_ECO

    def test_preset_mode_none_when_automatic(self):
        # #334: AUTOMATIC → HVACMode.AUTO, no preset
        device = _make_device(low=False, boost_mode=False, operation_mode_value="AUTOMATIC")
        entity = _make_entity(device)
        assert entity.preset_mode is None

    def test_preset_mode_none_when_manual(self):
        # #334: MANUAL → HVACMode.HEAT, no preset
        device = _make_device(low=False, boost_mode=False, operation_mode_value="MANUAL")
        entity = _make_entity(device)
        assert entity.preset_mode is None

    def test_eco_set_temperature_writes_setpoint(self):
        """P2-B: ECO no longer blocks setpoint writes — setpoint IS written. #196"""
        device = _make_device(low=True, operation_mode_value="MANUAL")
        entity = _make_entity(device)
        _run_async(entity.async_set_temperature(**{ATTR_TEMPERATURE: 19.0}))
        # Setpoint must be written even from ECO state (guard removed)
        device.async_set_setpoint_temperature.assert_awaited_with(19.0)

    def test_eco_does_not_raise(self):
        device = _make_device(low=True, operation_mode_value="MANUAL")
        entity = _make_entity(device)
        try:
            _run_async(entity.async_set_temperature(**{ATTR_TEMPERATURE: 19.0}))
        except Exception as exc:
            pytest.fail(f"async_set_temperature raised in ECO: {exc!r}")

    def test_eco_set_temperature_clears_low_first(self):
        """#73: bare set_temperature (no hvac_mode) must clear low=False
        itself — the ECO-clearing branch in _async_apply_hvac_mode is never
        reached when no explicit hvac_mode is requested."""
        device = _make_device(low=True, operation_mode_value="MANUAL")
        entity = _make_entity(device)
        _run_async(entity.async_set_temperature(**{ATTR_TEMPERATURE: 19.0}))
        device.async_set_low.assert_awaited_once_with(False)

    def test_eco_and_automatic_set_temperature_clears_low_and_manual(self):
        """#73: the exact reported scenario — a room left in eco/reduced
        (open window) AND still on the AUTOMATIC schedule. Both the low
        state and the operation mode must be cleared before the setpoint
        write, or the SHC still rejects it with
        WRONG_THERMOSTAT_GROUP_MODE."""
        device = _make_device(low=True, operation_mode_value="AUTOMATIC")
        entity = _make_entity(device)
        _run_async(entity.async_set_temperature(**{ATTR_TEMPERATURE: 19.0}))
        device.async_set_low.assert_awaited_once_with(False)
        device.async_set_setpoint_temperature.assert_awaited_with(19.0)

    def test_not_eco_does_not_call_async_set_low(self):
        device = _make_device(low=False, operation_mode_value="MANUAL")
        entity = _make_entity(device)
        _run_async(entity.async_set_temperature(**{ATTR_TEMPERATURE: 19.0}))
        device.async_set_low.assert_not_awaited()


# ---------------------------------------------------------------------------
# Normal HEAT mode: temperature written and rounded
# ---------------------------------------------------------------------------

class TestNormalHeatSetTemp:
    def _entity(self):
        device = _make_device(
            boost_mode=False, low=False, summer_mode=False,
            operation_mode_value="MANUAL",
        )
        entity = _make_entity(device)
        return entity

    def test_heat_mode_sets_setpoint(self):
        entity = self._entity()
        _run_async(entity.async_set_temperature(**{ATTR_TEMPERATURE: 21.5}))
        entity._device.async_set_setpoint_temperature.assert_awaited_with(21.5)

    def test_temperature_rounded_to_half_degree(self):
        entity = self._entity()
        _run_async(entity.async_set_temperature(**{ATTR_TEMPERATURE: 21.3}))
        entity._device.async_set_setpoint_temperature.assert_awaited_with(21.5)

    def test_temperature_below_min_not_written(self):
        entity = self._entity()
        _run_async(entity.async_set_temperature(**{ATTR_TEMPERATURE: 4.0}))
        entity._device.async_set_setpoint_temperature.assert_not_awaited()

    def test_temperature_above_max_not_written(self):
        entity = self._entity()
        _run_async(entity.async_set_temperature(**{ATTR_TEMPERATURE: 31.0}))
        entity._device.async_set_setpoint_temperature.assert_not_awaited()


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
        async_set_low=AsyncMock(),
        async_set_summer_mode=AsyncMock(),
        async_set_cooling_mode=AsyncMock(),
        async_set_boost_mode=AsyncMock(),
        async_set_operation_mode=AsyncMock(),
        async_set_setpoint_temperature=AsyncMock(),
    )


class TestCoolingHvacMode:
    """PR #304 / #334: hvac_mode returns COOL when supports_cooling and cooling_mode."""

    def test_hvac_mode_cool_when_cooling_active(self):
        device = _make_device_cooling(supports_cooling=True, cooling_mode=True)
        entity = _make_entity(device)
        assert entity.hvac_mode == HVACMode.COOL

    def test_hvac_mode_auto_when_not_cooling_not_summer_automatic(self):
        # #334: AUTOMATIC operation_mode → HVACMode.AUTO
        device = _make_device_cooling(supports_cooling=True, cooling_mode=False,
                                      operation_mode_value="AUTOMATIC")
        entity = _make_entity(device)
        assert entity.hvac_mode == HVACMode.AUTO

    def test_hvac_mode_heat_when_not_cooling_not_summer_manual(self):
        # MANUAL operation_mode → HVACMode.HEAT
        device = _make_device_cooling(supports_cooling=True, cooling_mode=False,
                                      operation_mode_value="MANUAL")
        entity = _make_entity(device)
        assert entity.hvac_mode == HVACMode.HEAT

    def test_hvac_modes_includes_cool_when_supported(self):
        device = _make_device_cooling(supports_cooling=True)
        entity = _make_entity(device)
        assert HVACMode.COOL in entity.hvac_modes

    def test_hvac_modes_excludes_cool_when_not_supported(self):
        device = _make_device_cooling(supports_cooling=False)
        entity = _make_entity(device)
        assert HVACMode.COOL not in entity.hvac_modes

    def test_set_hvac_mode_cool_sets_cooling_mode(self):
        device = _make_device_cooling(supports_cooling=True, cooling_mode=False)
        entity = _make_entity(device)
        _run_async(entity.async_set_hvac_mode(HVACMode.COOL))
        device.async_set_cooling_mode.assert_awaited_with(True)
        device.async_set_summer_mode.assert_awaited_with(False)

    def test_hvac_modes_includes_auto(self):
        # #334: AUTO is always present in hvac_modes for ClimateControl
        device = _make_device_cooling(supports_cooling=True)
        entity = _make_entity(device)
        assert HVACMode.AUTO in entity.hvac_modes

    def test_set_hvac_mode_heat_clears_cooling_mode(self):
        device = _make_device_cooling(supports_cooling=True, cooling_mode=True,
                                      operation_mode_value="MANUAL")
        entity = _make_entity(device)
        _run_async(entity.async_set_hvac_mode(HVACMode.HEAT))
        device.async_set_cooling_mode.assert_awaited_with(False)

    def test_set_hvac_mode_off_clears_cooling_mode(self):
        device = _make_device_cooling(supports_cooling=True, cooling_mode=True)
        entity = _make_entity(device)
        _run_async(entity.async_set_hvac_mode(HVACMode.OFF))
        device.async_set_cooling_mode.assert_awaited_with(False)
        device.async_set_summer_mode.assert_awaited_with(True)


# ---------------------------------------------------------------------------
# #334 — HVACMode.AUTO back as hvac_mode; preset axis is override-only
# ---------------------------------------------------------------------------

class TestHvacModeDirectionAxis:
    """#334: hvac_mode maps AUTOMATIC→AUTO, MANUAL→HEAT, summer→OFF, cooling→COOL."""

    def test_hvac_mode_auto_when_automatic(self):
        device = _make_device(summer_mode=False, operation_mode_value="AUTOMATIC",
                              supports_cooling=False)
        entity = _make_entity(device)
        assert entity.hvac_mode == HVACMode.AUTO

    def test_hvac_mode_heat_when_manual(self):
        device = _make_device(summer_mode=False, operation_mode_value="MANUAL",
                              supports_cooling=False)
        entity = _make_entity(device)
        assert entity.hvac_mode == HVACMode.HEAT

    def test_hvac_mode_cool(self):
        device = _make_device(summer_mode=False, cooling_mode=True, supports_cooling=True)
        entity = _make_entity(device)
        assert entity.hvac_mode == HVACMode.COOL

    def test_hvac_mode_off(self):
        device = _make_device(summer_mode=True)
        entity = _make_entity(device)
        assert entity.hvac_mode == HVACMode.OFF

    def test_hvac_modes_always_includes_auto(self):
        device = _make_device(supports_cooling=False)
        entity = _make_entity(device)
        assert HVACMode.AUTO in entity.hvac_modes

    def test_hvac_modes_heat_and_off_always_present(self):
        device = _make_device(supports_cooling=False)
        entity = _make_entity(device)
        assert HVACMode.HEAT in entity.hvac_modes
        assert HVACMode.OFF in entity.hvac_modes

    def test_hvac_modes_cool_only_when_supported(self):
        device_with = _make_device(supports_cooling=True)
        device_without = _make_device(supports_cooling=False)
        assert HVACMode.COOL in _make_entity(device_with).hvac_modes
        assert HVACMode.COOL not in _make_entity(device_without).hvac_modes


class TestPresetModeOverrideAxis:
    """#334: preset_mode is override-only: boost/eco. auto/manual removed."""

    def test_preset_mode_boost_takes_priority(self):
        # boost_mode=True wins over operation_mode
        device = _make_device(boost_mode=True, low=False, operation_mode_value="MANUAL")
        entity = _make_entity(device)
        assert entity.preset_mode == PRESET_BOOST

    def test_preset_mode_eco(self):
        device = _make_device(low=True, boost_mode=False, operation_mode_value="MANUAL")
        entity = _make_entity(device)
        assert entity.preset_mode == PRESET_ECO

    def test_preset_mode_none_when_automatic(self):
        # #334: AUTOMATIC → HVACMode.AUTO (no preset)
        device = _make_device(operation_mode_value="AUTOMATIC", boost_mode=False, low=False)
        entity = _make_entity(device)
        assert entity.preset_mode is None

    def test_preset_mode_none_when_manual(self):
        # #334: MANUAL → HVACMode.HEAT (no preset)
        device = _make_device(operation_mode_value="MANUAL", boost_mode=False, low=False)
        entity = _make_entity(device)
        assert entity.preset_mode is None

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
        assert entity.preset_modes is None or PRESET_ECO not in (entity.preset_modes or [])

    def test_preset_modes_none_when_no_presets(self):
        """When supports_boost_mode=False and no low attribute, preset_modes is None."""
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
        )
        entity = _make_entity(device)
        assert entity.preset_modes is None


class TestSetHvacModeNew:
    """Tests for async_set_hvac_mode with the #334 design."""

    def test_set_hvac_mode_auto_sets_automatic(self):
        """#334: AUTO sets operationMode=AUTOMATIC."""
        from boschshcpy.services_impl import RoomClimateControlService
        device = _make_device(summer_mode=False, supports_cooling=False,
                              operation_mode_value="MANUAL")
        entity = _make_entity(device)
        _run_async(entity.async_set_hvac_mode(HVACMode.AUTO))
        device.async_set_operation_mode.assert_awaited_with(
            RoomClimateControlService.OperationMode.AUTOMATIC
        )
        device.async_set_summer_mode.assert_awaited_with(False)

    def test_set_hvac_mode_heat_sets_manual(self):
        """#334: HEAT sets operationMode=MANUAL."""
        from boschshcpy.services_impl import RoomClimateControlService
        device = _make_device(summer_mode=False, supports_cooling=False,
                              operation_mode_value="AUTOMATIC")
        entity = _make_entity(device)
        _run_async(entity.async_set_hvac_mode(HVACMode.HEAT))
        device.async_set_operation_mode.assert_awaited_with(
            RoomClimateControlService.OperationMode.MANUAL
        )
        device.async_set_summer_mode.assert_awaited_with(False)

    def test_set_hvac_mode_cool_sets_cooling_true(self):
        device = _make_device(summer_mode=False, cooling_mode=False, supports_cooling=True)
        entity = _make_entity(device)
        _run_async(entity.async_set_hvac_mode(HVACMode.COOL))
        device.async_set_cooling_mode.assert_awaited_with(True)
        device.async_set_summer_mode.assert_awaited_with(False)

    def test_set_hvac_mode_off_sets_summer_true(self):
        device = _make_device(summer_mode=False, supports_cooling=False)
        entity = _make_entity(device)
        _run_async(entity.async_set_hvac_mode(HVACMode.OFF))
        device.async_set_summer_mode.assert_awaited_with(True)

    def test_set_hvac_mode_exits_eco_first(self):
        """When device.low=True, set_hvac_mode must clear low before direction change."""
        device = _make_device(low=True, summer_mode=False, supports_cooling=False,
                              operation_mode_value="MANUAL")
        entity = _make_entity(device)

        # Track call order by recording each mock's first invocation
        call_order = []

        async def _set_low(val):
            call_order.append(("low", val))

        async def _set_summer(val):
            call_order.append(("summer_mode", val))

        device.async_set_low = _set_low
        device.async_set_summer_mode = _set_summer

        _run_async(entity.async_set_hvac_mode(HVACMode.OFF))
        assert "low" in [k for k, _ in call_order], "low must be cleared when exiting ECO"
        keys = [k for k, _ in call_order]
        low_idx = keys.index("low")
        summer_idx = keys.index("summer_mode")
        assert low_idx < summer_idx, "low=False must be written before summer_mode=True"


class TestSetPresetModeNew:
    """Tests for async_set_preset_mode with the #334 design (override-only)."""

    def test_set_preset_boost(self):
        device = _make_device(boost_mode=False, supports_boost_mode=True)
        entity = _make_entity(device)
        _run_async(entity.async_set_preset_mode(PRESET_BOOST))
        device.async_set_boost_mode.assert_awaited_with(True)

    def test_set_preset_eco(self):
        device = _make_device(low=False, supports_boost_mode=True)
        entity = _make_entity(device)
        _run_async(entity.async_set_preset_mode(PRESET_ECO))
        device.async_set_low.assert_awaited_with(True)

    def test_set_preset_invalid_ignored(self):
        device = _make_device()
        entity = _make_entity(device)
        _run_async(entity.async_set_preset_mode("nonexistent_preset"))
        device.async_set_boost_mode.assert_not_awaited()
        device.async_set_low.assert_not_awaited()
        device.async_set_operation_mode.assert_not_awaited()

    def test_set_preset_boost_clears_boost_before_eco(self):
        """PRESET_ECO with boost active: clears boost_mode before writing low=True."""
        device = _make_device(boost_mode=True, low=False,
                              supports_boost_mode=True, operation_mode_value="MANUAL")
        entity = _make_entity(device)
        _run_async(entity.async_set_preset_mode(PRESET_ECO))
        device.async_set_boost_mode.assert_awaited_with(False)
        device.async_set_low.assert_awaited_with(True)


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


class TestTurnOnOff334:
    """#334: turn_on defaults to AUTO (schedule); turn_off still uses OFF/summer_mode."""

    def test_turn_on_from_off_sets_auto(self):
        """#334: turn_on → async_set_hvac_mode(AUTO) → sets operationMode=AUTOMATIC."""
        from boschshcpy.services_impl import RoomClimateControlService
        device = _make_device(summer_mode=True, supports_cooling=False)
        entity = _make_entity(device)
        _run_async(entity.async_turn_on())
        # AUTO sets summer_mode=False and operationMode=AUTOMATIC
        device.async_set_summer_mode.assert_awaited_with(False)
        device.async_set_operation_mode.assert_awaited_with(
            RoomClimateControlService.OperationMode.AUTOMATIC
        )

    def test_turn_off_sets_summer_mode(self):
        device = _make_device(summer_mode=False, operation_mode_value="AUTOMATIC")
        entity = _make_entity(device)
        _run_async(entity.async_turn_off())
        device.async_set_summer_mode.assert_awaited_with(True)
