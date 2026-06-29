"""Unit tests for climate.py — targets missing lines 261-295, 301-307, 334-335, 364-384.

Also covers line 213 (async_set_hvac_mode ECO early-return).
Does NOT duplicate test_climate.py / test_heating_circuit.py assertions.
Pattern: Cls.__new__(Cls) + SimpleNamespace fake device; asyncio.run() for async.

#334: AUTOMATIC is HVACMode.AUTO, MANUAL is HVACMode.HEAT.
AUTO and MANUAL are no longer presets — only boost and eco remain.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from boschshcpy import HeatingCircuitService
from boschshcpy.services_impl import RoomClimateControlService
from homeassistant.components.climate.const import (
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE

from custom_components.bosch_shc.climate import (
    PRESET_BOOST,
    PRESET_ECO,
    ClimateControl,
    HeatingCircuit,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

OM_CC = RoomClimateControlService.OperationMode
OM_HC = HeatingCircuitService.OperationMode


def _make_cc_device(
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
    has_demand=False,
):
    return SimpleNamespace(
        boost_mode=boost_mode,
        low=low,
        summer_mode=summer_mode,
        supports_boost_mode=supports_boost_mode,
        supports_eco=supports_eco,
        setpoint_temperature=setpoint_temperature,
        temperature=temperature,
        operation_mode=OM_CC(operation_mode_value),
        supports_cooling=supports_cooling,
        cooling_mode=cooling_mode,
        has_demand=has_demand,
        root_device_id="r",
        id="d",
        async_set_low=AsyncMock(),
        async_set_summer_mode=AsyncMock(),
        async_set_cooling_mode=AsyncMock(),
        async_set_boost_mode=AsyncMock(),
        async_set_operation_mode=AsyncMock(),
        async_set_setpoint_temperature=AsyncMock(),
    )


def _make_cc(device):
    entity = ClimateControl.__new__(ClimateControl)
    entity._device = device
    entity._room_label = "Test"
    entity._attr_name = None
    entity._attr_unique_id = "r_d"
    return entity


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_hc_device(*, operation_mode=None, on=False, setpoint=20.0,
                    root_device_id="r", id_="d"):
    return SimpleNamespace(
        operation_mode=operation_mode or OM_HC.AUTOMATIC,
        on=on,
        setpoint_temperature=setpoint,
        root_device_id=root_device_id,
        id=id_,
        async_set_operation_mode=AsyncMock(),
        async_set_setpoint_temperature=AsyncMock(),
    )


def _make_hc(device):
    entity = HeatingCircuit.__new__(HeatingCircuit)
    entity._device = device
    entity._attr_unique_id = "r_d"
    return entity


# ===========================================================================
# ClimateControl — async_set_hvac_mode ECO guard (line 213)
# ===========================================================================

class TestSetHvacModeEcoGuard:
    """#196: async_set_hvac_mode in ECO must exit ECO first, then write mode.

    Old behaviour: returned early when preset==ECO → mode change silently no-oped.
    New behaviour: calls async_set_low(False) first, then proceeds with the requested mode.

    #334: AUTO sets operationMode=AUTOMATIC; HEAT sets MANUAL. Both clear ECO first.
    """

    def test_set_hvac_mode_auto_in_eco_exits_eco_and_writes_mode(self):
        """#196/#334: In ECO, AUTO mode must clear low and set operationMode=AUTOMATIC."""
        device = _make_cc_device(low=True, operation_mode_value="MANUAL")
        entity = _make_cc(device)

        _run(entity.async_set_hvac_mode(HVACMode.AUTO))

        # ECO is exited first, then AUTO is applied
        device.async_set_low.assert_awaited_with(False)
        device.async_set_operation_mode.assert_awaited_with(OM_CC.AUTOMATIC)

    def test_set_hvac_mode_heat_in_eco_exits_eco_and_writes_mode(self):
        """#196/#334: In ECO, HEAT mode must clear low and set operationMode=MANUAL."""
        device = _make_cc_device(low=True, operation_mode_value="AUTOMATIC")
        entity = _make_cc(device)

        _run(entity.async_set_hvac_mode(HVACMode.HEAT))

        # ECO is exited first, then HEAT is applied
        device.async_set_low.assert_awaited_with(False)
        device.async_set_summer_mode.assert_awaited_with(False)
        device.async_set_operation_mode.assert_awaited_with(OM_CC.MANUAL)


# ===========================================================================
# ClimateControl — async_set_preset_mode (lines 261-295)
# ===========================================================================

class TestSetPresetModeBoost:
    """PRESET_BOOST: sets boost_mode=True.

    #334: Only boost and eco remain as presets (no auto/manual).
    """

    def test_boost_sets_boost_mode(self):
        device = _make_cc_device(boost_mode=False, low=False, supports_boost_mode=True)
        entity = _make_cc(device)

        _run(entity.async_set_preset_mode(PRESET_BOOST))

        device.async_set_boost_mode.assert_awaited_with(True)

    def test_boost_writes_even_if_already_active(self):
        # New impl: no idempotency guard, always writes
        device = _make_cc_device(boost_mode=True, low=False, supports_boost_mode=True)
        entity = _make_cc(device)

        _run(entity.async_set_preset_mode(PRESET_BOOST))

        device.async_set_boost_mode.assert_awaited_with(True)

    def test_boost_no_low_write(self):
        # New impl: boost does not touch low
        device = _make_cc_device(boost_mode=False, low=True, supports_boost_mode=True)
        entity = _make_cc(device)

        _run(entity.async_set_preset_mode(PRESET_BOOST))

        device.async_set_low.assert_not_awaited()

    def test_invalid_preset_mode_is_ignored(self):
        device = _make_cc_device()
        entity = _make_cc(device)

        _run(entity.async_set_preset_mode("INVALID_PRESET"))

        device.async_set_boost_mode.assert_not_awaited()
        device.async_set_low.assert_not_awaited()
        device.async_set_operation_mode.assert_not_awaited()


class TestSetPresetModeEco:
    """PRESET_ECO: sets low=True; clears boost_mode if active.

    #334: new impl always writes low=True (no idempotency guard).
    """

    def test_eco_sets_low(self):
        device = _make_cc_device(boost_mode=False, low=False, supports_boost_mode=True)
        entity = _make_cc(device)

        _run(entity.async_set_preset_mode(PRESET_ECO))

        device.async_set_low.assert_awaited_with(True)

    def test_eco_writes_low_even_if_already_low(self):
        # New impl: no idempotency guard, always writes low=True
        device = _make_cc_device(boost_mode=False, low=True, supports_boost_mode=True)
        entity = _make_cc(device)

        _run(entity.async_set_preset_mode(PRESET_ECO))

        device.async_set_low.assert_awaited_with(True)

    def test_eco_clears_boost_when_active(self):
        device = _make_cc_device(boost_mode=True, low=False, supports_boost_mode=True)
        entity = _make_cc(device)

        _run(entity.async_set_preset_mode(PRESET_ECO))

        device.async_set_boost_mode.assert_awaited_with(False)
        device.async_set_low.assert_awaited_with(True)

    def test_eco_no_boost_write_when_not_in_boost(self):
        device = _make_cc_device(boost_mode=False, low=False, supports_boost_mode=True)
        entity = _make_cc(device)

        _run(entity.async_set_preset_mode(PRESET_ECO))

        # boost_mode was False → no write
        device.async_set_boost_mode.assert_not_awaited()

    def test_eco_without_boost_support_does_not_touch_boost(self):
        device = _make_cc_device(boost_mode=False, low=False, supports_boost_mode=False)
        entity = _make_cc(device)

        _run(entity.async_set_preset_mode(PRESET_ECO))

        device.async_set_boost_mode.assert_not_awaited()
        device.async_set_low.assert_awaited_with(True)


# ===========================================================================
# ClimateControl — async_turn_on / async_turn_off (lines 301-302, 306-307)
# ===========================================================================

class TestTurnOnOff:
    """#334: turn_on switches to AUTO (AUTOMATIC); turn_off sets summer_mode."""

    def test_turn_on_when_off_calls_set_hvac_auto(self):
        """#334: summer_mode=True makes hvac_mode=OFF → turn_on sets AUTO (operationMode=AUTOMATIC)."""
        device = _make_cc_device(summer_mode=True)
        entity = _make_cc(device)

        _run(entity.async_turn_on())

        # AUTO sets summer_mode=False and operationMode=AUTOMATIC
        device.async_set_summer_mode.assert_awaited_with(False)
        device.async_set_operation_mode.assert_awaited_with(OM_CC.AUTOMATIC)

    def test_turn_on_noop_when_already_on_auto(self):
        """Already in AUTO mode → turn_on is noop."""
        device = _make_cc_device(summer_mode=False, operation_mode_value="AUTOMATIC")
        entity = _make_cc(device)

        _run(entity.async_turn_on())

        # Already AUTO mode (not OFF) → no writes from async_set_hvac_mode(AUTO)
        # Actually, turn_on checks hvac_mode == OFF, so if already AUTO → noop
        # The check is: if OFF → call async_set_hvac_mode(AUTO). AUTO is not OFF.
        # But async_set_hvac_mode(AUTO) would still be called if mode is AUTO+not OFF.
        # Correct: turn_on only calls when hvac_mode==OFF.
        # AUTO is hvac_mode AUTO (not OFF) → no call needed.
        # However since turn_on checks `if self.hvac_mode == HVACMode.OFF` → NO call.
        device.async_set_summer_mode.assert_not_awaited()

    def test_turn_on_noop_when_already_on_heat(self):
        device = _make_cc_device(summer_mode=False, operation_mode_value="MANUAL")
        entity = _make_cc(device)

        _run(entity.async_turn_on())

        # Already HEAT mode → no writes
        device.async_set_summer_mode.assert_not_awaited()

    def test_turn_off_when_on_sets_summer_mode(self):
        device = _make_cc_device(summer_mode=False, operation_mode_value="AUTOMATIC")
        entity = _make_cc(device)

        _run(entity.async_turn_off())

        device.async_set_summer_mode.assert_awaited_with(True)

    def test_turn_off_noop_when_already_off(self):
        device = _make_cc_device(summer_mode=True)
        entity = _make_cc(device)

        _run(entity.async_turn_off())

        # Already OFF → no writes
        device.async_set_summer_mode.assert_not_awaited()


# ===========================================================================
# HeatingCircuit — async_set_temperature (lines 364-368, 334-335)
# ===========================================================================

class TestHeatingCircuitSetTemperature:
    """Lines 362-373: HeatingCircuit.async_set_temperature."""

    def test_set_temp_writes_rounded_value(self):
        device = _make_hc_device()
        entity = _make_hc(device)

        _run(entity.async_set_temperature(**{ATTR_TEMPERATURE: 21.3}))

        device.async_set_setpoint_temperature.assert_awaited_with(21.5)

    def test_set_temp_exact_half_degree(self):
        device = _make_hc_device()
        entity = _make_hc(device)

        _run(entity.async_set_temperature(**{ATTR_TEMPERATURE: 19.5}))

        device.async_set_setpoint_temperature.assert_awaited_with(19.5)

    def test_set_temp_none_arg_is_noop(self):
        device = _make_hc_device()
        entity = _make_hc(device)

        _run(entity.async_set_temperature())

        device.async_set_setpoint_temperature.assert_not_awaited()

    def test_set_temp_below_min_is_noop(self):
        device = _make_hc_device()
        entity = _make_hc(device)

        _run(entity.async_set_temperature(**{ATTR_TEMPERATURE: 4.9}))

        device.async_set_setpoint_temperature.assert_not_awaited()

    def test_set_temp_above_max_is_noop(self):
        device = _make_hc_device()
        entity = _make_hc(device)

        _run(entity.async_set_temperature(**{ATTR_TEMPERATURE: 30.1}))

        device.async_set_setpoint_temperature.assert_not_awaited()

    def test_set_temp_at_min_boundary_writes(self):
        device = _make_hc_device()
        entity = _make_hc(device)

        _run(entity.async_set_temperature(**{ATTR_TEMPERATURE: 5.0}))

        device.async_set_setpoint_temperature.assert_awaited_with(5.0)

    def test_set_temp_at_max_boundary_writes(self):
        device = _make_hc_device()
        entity = _make_hc(device)

        _run(entity.async_set_temperature(**{ATTR_TEMPERATURE: 30.0}))

        device.async_set_setpoint_temperature.assert_awaited_with(30.0)


# ===========================================================================
# HeatingCircuit — async_set_hvac_mode (lines 377-384)
# ===========================================================================

class TestHeatingCircuitSetHvacMode:
    """Lines 375-386: HeatingCircuit.async_set_hvac_mode."""

    def test_set_auto_writes_automatic_operation_mode(self):
        device = _make_hc_device(operation_mode=OM_HC.MANUAL)
        entity = _make_hc(device)

        _run(entity.async_set_hvac_mode(HVACMode.AUTO))

        device.async_set_operation_mode.assert_awaited_with(OM_HC.AUTOMATIC)

    def test_set_heat_writes_manual_operation_mode(self):
        device = _make_hc_device(operation_mode=OM_HC.AUTOMATIC)
        entity = _make_hc(device)

        _run(entity.async_set_hvac_mode(HVACMode.HEAT))

        device.async_set_operation_mode.assert_awaited_with(OM_HC.MANUAL)

    def test_invalid_hvac_mode_is_noop(self):
        device = _make_hc_device()
        entity = _make_hc(device)

        _run(entity.async_set_hvac_mode(HVACMode.OFF))

        # OFF not in hvac_modes for HeatingCircuit → noop
        device.async_set_operation_mode.assert_not_awaited()

    def test_invalid_cool_mode_is_noop(self):
        device = _make_hc_device()
        entity = _make_hc(device)

        _run(entity.async_set_hvac_mode(HVACMode.COOL))

        device.async_set_operation_mode.assert_not_awaited()


# ===========================================================================
# HeatingCircuit — __init__ (lines 334-335)
# ===========================================================================

class TestHeatingCircuitInit:
    """Lines 334-335: HeatingCircuit.__init__ sets unique_id correctly."""

    def _make_full_hc_device(self, root_device_id="root-123", id_="dev-456"):
        """Device with all attributes SHCEntity.__init__ needs."""
        return SimpleNamespace(
            operation_mode=OM_HC.AUTOMATIC,
            on=False,
            setpoint_temperature=20.0,
            root_device_id=root_device_id,
            id=id_,
            name="Test Heating Circuit",
            manufacturer="Bosch",
            device_model="HC",
            device_services=[],
            status="AVAILABLE",
            deleted=False,
        )

    def test_init_sets_unique_id(self):
        device = self._make_full_hc_device(root_device_id="root-123", id_="dev-456")
        entity = HeatingCircuit(device=device, name="HC Test", entry_id="test_entry")
        assert entity._attr_unique_id == "root-123_dev-456"

    def test_init_unique_id_different_ids(self):
        device = self._make_full_hc_device(root_device_id="abc", id_="xyz")
        entity = HeatingCircuit(device=device, name="HC Other", entry_id="e2")
        assert entity._attr_unique_id == "abc_xyz"


# ===========================================================================
# ClimateControl — hvac_action (lines 132-136)
# ===========================================================================

class TestHvacActionByMode:
    """ClimateControl.hvac_action delegates to has_demand + hvac_mode."""

    def test_hvac_action_heating_when_has_demand_and_mode_auto(self):
        """has_demand=True, mode=AUTO → HVACAction.HEATING."""
        device = _make_cc_device(summer_mode=False, operation_mode_value="AUTOMATIC", has_demand=True)
        entity = _make_cc(device)
        assert entity.hvac_action == HVACAction.HEATING

    def test_hvac_action_heating_when_has_demand_and_mode_heat(self):
        """has_demand=True, mode=HEAT → HVACAction.HEATING."""
        device = _make_cc_device(summer_mode=False, operation_mode_value="MANUAL", has_demand=True)
        entity = _make_cc(device)
        assert entity.hvac_action == HVACAction.HEATING

    def test_hvac_action_idle_when_no_demand(self):
        """has_demand=False, mode=AUTO → HVACAction.IDLE."""
        device = _make_cc_device(summer_mode=False, operation_mode_value="AUTOMATIC", has_demand=False)
        entity = _make_cc(device)
        assert entity.hvac_action == HVACAction.IDLE

    def test_hvac_action_off_when_summer_mode(self):
        """summer_mode=True → hvac_mode=OFF → HVACAction.OFF regardless of has_demand."""
        device = _make_cc_device(summer_mode=True, has_demand=True)
        entity = _make_cc(device)
        assert entity.hvac_action == HVACAction.OFF

    def test_hvac_action_off_when_off_and_no_demand(self):
        """summer_mode=True, has_demand=False → HVACAction.OFF."""
        device = _make_cc_device(summer_mode=True, has_demand=False)
        entity = _make_cc(device)
        assert entity.hvac_action == HVACAction.OFF


# ===========================================================================
# ClimateControl — hvac_action (has_demand / hasDemand)
# ===========================================================================

class TestHvacAction:
    """hvac_action returns HEATING when has_demand, IDLE otherwise, OFF when mode=OFF."""

    def _entity(self, *, summer_mode=False, has_demand=False,
                operation_mode_value="AUTOMATIC", supports_cooling=False):
        device = SimpleNamespace(
            summer_mode=summer_mode,
            has_demand=has_demand,
            operation_mode=OM_CC(operation_mode_value),
            supports_cooling=supports_cooling,
            cooling_mode=False,
            boost_mode=False,
            low=False,
            supports_boost_mode=True,
            setpoint_temperature=20.0,
            temperature=19.0,
            root_device_id="r",
            id="d",
        )
        entity = ClimateControl.__new__(ClimateControl)
        entity._device = device
        entity._room_label = "Test"
        entity._attr_name = None
        entity._attr_unique_id = "r_d"
        return entity

    def test_hvac_action_heating_when_has_demand(self):
        entity = self._entity(has_demand=True, summer_mode=False)
        assert entity.hvac_action == HVACAction.HEATING

    def test_hvac_action_idle_when_no_demand(self):
        entity = self._entity(has_demand=False, summer_mode=False)
        assert entity.hvac_action == HVACAction.IDLE

    def test_hvac_action_off_when_summer_mode(self):
        entity = self._entity(has_demand=True, summer_mode=True)
        assert entity.hvac_action == HVACAction.OFF

    def test_hvac_action_off_overrides_has_demand(self):
        """Even when has_demand=True, summer_mode (OFF) wins."""
        entity = self._entity(has_demand=True, summer_mode=True)
        assert entity.hvac_action == HVACAction.OFF


# ===========================================================================
# #196 / P2-A — async_turn_off from ECO now actually turns off (#196)
# ===========================================================================

class TestTurnOffFromEco:
    """#196: turn_off must work even when preset_mode==ECO.

    Old code: async_set_hvac_mode returned early if preset==ECO → summer_mode
    never written → device stayed on.
    New code: exit ECO (async_set_low(False)) first, then write summer_mode=True.
    """

    def test_turn_off_from_eco_exits_eco_and_sets_summer_mode(self):
        """In ECO mode, turn_off must clear low AND set summer_mode=True."""
        device = _make_cc_device(low=True, summer_mode=False,
                                 operation_mode_value="MANUAL")
        entity = _make_cc(device)

        _run(entity.async_turn_off())

        device.async_set_low.assert_awaited_with(False)
        device.async_set_summer_mode.assert_awaited_with(True)

    def test_set_hvac_mode_off_from_eco_clears_eco(self):
        """async_set_hvac_mode(OFF) in ECO must clear low before setting summer_mode."""
        device = _make_cc_device(low=True, summer_mode=False,
                                 operation_mode_value="AUTOMATIC")
        entity = _make_cc(device)

        _run(entity.async_set_hvac_mode(HVACMode.OFF))

        device.async_set_low.assert_awaited_with(False)
        device.async_set_summer_mode.assert_awaited_with(True)

    def test_set_hvac_mode_heat_from_eco_clears_eco(self):
        """async_set_hvac_mode(HEAT) in ECO must clear low before setting mode.

        #334: HEAT sets operationMode=MANUAL + summer_mode=False.
        """
        device = _make_cc_device(low=True, summer_mode=False,
                                 operation_mode_value="AUTOMATIC")
        entity = _make_cc(device)

        _run(entity.async_set_hvac_mode(HVACMode.HEAT))

        device.async_set_low.assert_awaited_with(False)
        device.async_set_summer_mode.assert_awaited_with(False)
        device.async_set_operation_mode.assert_awaited_with(OM_CC.MANUAL)

    def test_set_hvac_mode_auto_from_eco_clears_eco(self):
        """async_set_hvac_mode(AUTO) in ECO must clear low and set AUTOMATIC.

        #334: AUTO sets operationMode=AUTOMATIC.
        """
        device = _make_cc_device(low=True, summer_mode=False,
                                 operation_mode_value="MANUAL")
        entity = _make_cc(device)

        _run(entity.async_set_hvac_mode(HVACMode.AUTO))

        device.async_set_low.assert_awaited_with(False)
        device.async_set_operation_mode.assert_awaited_with(OM_CC.AUTOMATIC)

    def test_set_hvac_mode_not_in_eco_does_not_touch_low(self):
        """When not in ECO, low must not be written."""
        device = _make_cc_device(low=False, summer_mode=False,
                                 operation_mode_value="MANUAL")
        entity = _make_cc(device)

        _run(entity.async_set_hvac_mode(HVACMode.HEAT))

        device.async_set_low.assert_not_awaited()


# ===========================================================================
# #334 — COOL branch writes direction axis only
# ===========================================================================

class TestCoolSetsDirectionAxis:
    """#334: COOL sets cooling_mode=True + summer_mode=False (direction axis).
    operationMode is NOT touched by set_hvac_mode(COOL).
    """

    def test_cool_writes_cooling_mode_and_summer_false(self):
        device = _make_cc_device(
            summer_mode=False,
            supports_cooling=True,
            cooling_mode=False,
            operation_mode_value="AUTOMATIC",
        )
        entity = _make_cc(device)

        _run(entity.async_set_hvac_mode(HVACMode.COOL))

        device.async_set_cooling_mode.assert_awaited_with(True)
        device.async_set_summer_mode.assert_awaited_with(False)
        # COOL does NOT touch operationMode
        device.async_set_operation_mode.assert_not_awaited()

    def test_cool_from_eco_exits_eco_and_sets_cooling(self):
        """From ECO, switching to COOL should also clear low first."""
        device = _make_cc_device(
            low=True,
            summer_mode=False,
            supports_cooling=True,
            cooling_mode=False,
            operation_mode_value="AUTOMATIC",
        )
        entity = _make_cc(device)

        _run(entity.async_set_hvac_mode(HVACMode.COOL))

        device.async_set_low.assert_awaited_with(False)
        device.async_set_cooling_mode.assert_awaited_with(True)
        device.async_set_summer_mode.assert_awaited_with(False)


class TestEcoGatedOnSupportsEco:
    """#334 / jumlu #68 regression: eco gates on supports_eco, NOT supports_low.

    SHC-II floor-heating rooms carry low=False/True without an eco model, so a
    supports_low-based gate wrongly offered Eco there. Eco must only appear when
    supports_eco (the eco-setpoint field) is present.
    """

    def test_eco_not_offered_when_supports_eco_false_even_with_low(self):
        device = _make_cc_device(low=True, supports_eco=False, supports_boost_mode=False)
        entity = _make_cc(device)
        assert entity.preset_modes is None
        assert entity.preset_mode is None

    def test_eco_offered_when_supports_eco_true(self):
        device = _make_cc_device(low=True, supports_eco=True, supports_boost_mode=False)
        entity = _make_cc(device)
        assert entity.preset_modes == [PRESET_ECO]
        assert entity.preset_mode == PRESET_ECO
