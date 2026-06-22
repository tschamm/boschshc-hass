"""Unit tests for climate.py — targets missing lines 261-295, 301-307, 334-335, 364-384.

Also covers line 213 (async_set_hvac_mode ECO early-return).
Does NOT duplicate test_climate.py / test_heating_circuit.py assertions.
Pattern: Cls.__new__(Cls) + SimpleNamespace fake device; asyncio.run() for async.
"""

import asyncio
from types import SimpleNamespace

from boschshcpy import SHCHeatingCircuit
from boschshcpy.services_impl import RoomClimateControlService
from homeassistant.components.climate.const import (
    HVACMode,
    HVACAction,
)
from homeassistant.const import ATTR_TEMPERATURE

from custom_components.bosch_shc.climate import (
    ClimateControl,
    HeatingCircuit,
    PRESET_MANUAL,
    PRESET_BOOST,
    PRESET_ECO,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

OM_CC = RoomClimateControlService.OperationMode
OM_HC = SHCHeatingCircuit.HeatingCircuitService.OperationMode


def _make_cc_device(
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
    has_demand=False,
):
    return SimpleNamespace(
        boost_mode=boost_mode,
        low=low,
        summer_mode=summer_mode,
        supports_boost_mode=supports_boost_mode,
        setpoint_temperature=setpoint_temperature,
        temperature=temperature,
        operation_mode=OM_CC(operation_mode_value),
        supports_cooling=supports_cooling,
        cooling_mode=cooling_mode,
        has_demand=has_demand,
        root_device_id="r",
        id="d",
    )


def _make_cc(device):
    entity = ClimateControl.__new__(ClimateControl)
    entity._device = device
    entity._name = "Test"
    entity._attr_unique_id = "r_d"
    return entity


def _make_hass(writes=None):
    """Minimal hass stub; records setattr calls in *writes* dict if provided."""
    captured = writes if writes is not None else {}

    async def _exec(func, *args):
        if func is setattr and len(args) == 3:
            captured[args[1]] = args[2]
        return func(*args)

    return SimpleNamespace(async_add_executor_job=_exec), captured


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
    )


def _make_hc(device):
    entity = HeatingCircuit.__new__(HeatingCircuit)
    entity._device = device
    return entity


# ===========================================================================
# ClimateControl — async_set_hvac_mode ECO guard (line 213)
# ===========================================================================

class TestSetHvacModeEcoGuard:
    """#196: async_set_hvac_mode in ECO must exit ECO first, then write mode.

    Old behaviour: returned early when preset==ECO → mode change silently no-oped.
    New behaviour: clears low=False first, then proceeds with the requested mode.

    PR #329: AUTO is now a preset, not an hvac_mode. Use HEAT to test the ECO-exit path.
    """

    def test_set_hvac_mode_in_eco_exits_eco_and_writes_mode(self):
        """#196: In ECO, HEAT mode must clear low and set summer_mode=False."""
        device = _make_cc_device(low=True, operation_mode_value="MANUAL")
        entity = _make_cc(device)
        hass, writes = _make_hass()
        entity.hass = hass

        _run(entity.async_set_hvac_mode(HVACMode.HEAT))

        # ECO is exited first, then HEAT is applied
        assert writes.get("low") is False, "ECO (low) must be cleared"
        assert writes.get("summer_mode") is False, (
            "summer_mode must be set to False for HEAT"
        )


# ===========================================================================
# ClimateControl — async_set_preset_mode (lines 261-295)
# ===========================================================================

class TestSetPresetModeManual:
    """PRESET_MANUAL: clears boost_mode (if active) and low (if active), sets operation_mode=MANUAL.

    PR #329: PRESET_NONE is replaced by PRESET_MANUAL (regulation axis).
    """

    def test_manual_clears_boost_mode(self):
        device = _make_cc_device(boost_mode=True, low=False, supports_boost_mode=True,
                                 operation_mode_value="AUTOMATIC")
        entity = _make_cc(device)
        hass, writes = _make_hass()
        entity.hass = hass

        _run(entity.async_set_preset_mode(PRESET_MANUAL))

        assert writes.get("boost_mode") is False

    def test_manual_clears_low_when_low_is_true(self):
        device = _make_cc_device(boost_mode=False, low=True, supports_boost_mode=True,
                                 operation_mode_value="AUTOMATIC")
        entity = _make_cc(device)
        hass, writes = _make_hass()
        entity.hass = hass

        _run(entity.async_set_preset_mode(PRESET_MANUAL))

        assert writes.get("low") is False

    def test_manual_no_boost_write_when_not_in_boost(self):
        device = _make_cc_device(boost_mode=False, low=False, supports_boost_mode=True,
                                 operation_mode_value="AUTOMATIC")
        entity = _make_cc(device)
        hass, writes = _make_hass()
        entity.hass = hass

        _run(entity.async_set_preset_mode(PRESET_MANUAL))

        # boost was already False → no write
        assert "boost_mode" not in writes

    def test_manual_clears_both_boost_and_low(self):
        device = _make_cc_device(boost_mode=True, low=True, supports_boost_mode=True,
                                 operation_mode_value="AUTOMATIC")
        entity = _make_cc(device)
        hass, writes = _make_hass()
        entity.hass = hass

        _run(entity.async_set_preset_mode(PRESET_MANUAL))

        assert writes.get("boost_mode") is False
        assert writes.get("low") is False
        assert writes.get("operation_mode") == OM_CC.MANUAL

    def test_invalid_preset_mode_is_ignored(self):
        device = _make_cc_device()
        entity = _make_cc(device)
        hass, writes = _make_hass()
        entity.hass = hass

        _run(entity.async_set_preset_mode("INVALID_PRESET"))

        assert writes == {}


class TestSetPresetModeBoost:
    """PRESET_BOOST: sets boost_mode=True.

    PR #329: new impl always writes boost_mode=True (no idempotency guard).
    Low is not cleared by the boost write itself (eco and boost are separate presets).
    """

    def test_boost_sets_boost_mode(self):
        device = _make_cc_device(boost_mode=False, low=False, supports_boost_mode=True)
        entity = _make_cc(device)
        hass, writes = _make_hass()
        entity.hass = hass

        _run(entity.async_set_preset_mode(PRESET_BOOST))

        assert writes.get("boost_mode") is True

    def test_boost_writes_even_if_already_active(self):
        # New impl: no idempotency guard, always writes
        device = _make_cc_device(boost_mode=True, low=False, supports_boost_mode=True)
        entity = _make_cc(device)
        hass, writes = _make_hass()
        entity.hass = hass

        _run(entity.async_set_preset_mode(PRESET_BOOST))

        assert writes.get("boost_mode") is True

    def test_boost_no_low_write(self):
        # New impl: boost does not touch low
        device = _make_cc_device(boost_mode=False, low=True, supports_boost_mode=True)
        entity = _make_cc(device)
        hass, writes = _make_hass()
        entity.hass = hass

        _run(entity.async_set_preset_mode(PRESET_BOOST))

        assert "low" not in writes


class TestSetPresetModeEco:
    """PRESET_ECO: sets low=True; clears boost_mode if active.

    PR #329: new impl always writes low=True (no idempotency guard).
    """

    def test_eco_sets_low(self):
        device = _make_cc_device(boost_mode=False, low=False, supports_boost_mode=True)
        entity = _make_cc(device)
        hass, writes = _make_hass()
        entity.hass = hass

        _run(entity.async_set_preset_mode(PRESET_ECO))

        assert writes.get("low") is True

    def test_eco_writes_low_even_if_already_low(self):
        # New impl: no idempotency guard, always writes low=True
        device = _make_cc_device(boost_mode=False, low=True, supports_boost_mode=True)
        entity = _make_cc(device)
        hass, writes = _make_hass()
        entity.hass = hass

        _run(entity.async_set_preset_mode(PRESET_ECO))

        assert writes.get("low") is True

    def test_eco_clears_boost_when_active(self):
        device = _make_cc_device(boost_mode=True, low=False, supports_boost_mode=True)
        entity = _make_cc(device)
        hass, writes = _make_hass()
        entity.hass = hass

        _run(entity.async_set_preset_mode(PRESET_ECO))

        assert writes.get("boost_mode") is False
        assert writes.get("low") is True

    def test_eco_no_boost_write_when_not_in_boost(self):
        device = _make_cc_device(boost_mode=False, low=False, supports_boost_mode=True)
        entity = _make_cc(device)
        hass, writes = _make_hass()
        entity.hass = hass

        _run(entity.async_set_preset_mode(PRESET_ECO))

        # boost_mode was False → no write
        assert "boost_mode" not in writes

    def test_eco_without_boost_support_does_not_touch_boost(self):
        device = _make_cc_device(boost_mode=False, low=False, supports_boost_mode=False)
        entity = _make_cc(device)
        hass, writes = _make_hass()
        entity.hass = hass

        _run(entity.async_set_preset_mode(PRESET_ECO))

        assert "boost_mode" not in writes
        assert writes.get("low") is True


# ===========================================================================
# ClimateControl — async_turn_on / async_turn_off (lines 301-302, 306-307)
# ===========================================================================

class TestTurnOnOff:
    """turn_on switches to HEAT when currently OFF; turn_off sets summer_mode."""

    def test_turn_on_when_off_calls_set_hvac_heat(self):
        device = _make_cc_device(summer_mode=True)
        entity = _make_cc(device)
        hass, writes = _make_hass()
        entity.hass = hass

        _run(entity.async_turn_on())

        # summer_mode=True makes hvac_mode=OFF → turn_on sets summer_mode=False + operation MANUAL
        assert writes.get("summer_mode") is False

    def test_turn_on_noop_when_already_on(self):
        device = _make_cc_device(summer_mode=False, operation_mode_value="MANUAL")
        entity = _make_cc(device)
        hass, writes = _make_hass()
        entity.hass = hass

        _run(entity.async_turn_on())

        # Already HEAT mode → no writes
        assert "summer_mode" not in writes

    def test_turn_off_when_on_sets_summer_mode(self):
        device = _make_cc_device(summer_mode=False, operation_mode_value="AUTOMATIC")
        entity = _make_cc(device)
        hass, writes = _make_hass()
        entity.hass = hass

        _run(entity.async_turn_off())

        assert writes.get("summer_mode") is True

    def test_turn_off_noop_when_already_off(self):
        device = _make_cc_device(summer_mode=True)
        entity = _make_cc(device)
        hass, writes = _make_hass()
        entity.hass = hass

        _run(entity.async_turn_off())

        # Already OFF → no writes
        assert "summer_mode" not in writes


# ===========================================================================
# HeatingCircuit — async_set_temperature (lines 364-368, 334-335)
# ===========================================================================

class TestHeatingCircuitSetTemperature:
    """Lines 362-373: HeatingCircuit.async_set_temperature."""

    def test_set_temp_writes_rounded_value(self):
        device = _make_hc_device()
        entity = _make_hc(device)
        hass, writes = _make_hass()
        entity.hass = hass

        _run(entity.async_set_temperature(**{ATTR_TEMPERATURE: 21.3}))

        assert writes.get("setpoint_temperature") == 21.5

    def test_set_temp_exact_half_degree(self):
        device = _make_hc_device()
        entity = _make_hc(device)
        hass, writes = _make_hass()
        entity.hass = hass

        _run(entity.async_set_temperature(**{ATTR_TEMPERATURE: 19.5}))

        assert writes.get("setpoint_temperature") == 19.5

    def test_set_temp_none_arg_is_noop(self):
        device = _make_hc_device()
        entity = _make_hc(device)
        hass, writes = _make_hass()
        entity.hass = hass

        _run(entity.async_set_temperature())

        assert writes == {}

    def test_set_temp_below_min_is_noop(self):
        device = _make_hc_device()
        entity = _make_hc(device)
        hass, writes = _make_hass()
        entity.hass = hass

        _run(entity.async_set_temperature(**{ATTR_TEMPERATURE: 4.9}))

        assert writes == {}

    def test_set_temp_above_max_is_noop(self):
        device = _make_hc_device()
        entity = _make_hc(device)
        hass, writes = _make_hass()
        entity.hass = hass

        _run(entity.async_set_temperature(**{ATTR_TEMPERATURE: 30.1}))

        assert writes == {}

    def test_set_temp_at_min_boundary_writes(self):
        device = _make_hc_device()
        entity = _make_hc(device)
        hass, writes = _make_hass()
        entity.hass = hass

        _run(entity.async_set_temperature(**{ATTR_TEMPERATURE: 5.0}))

        assert writes.get("setpoint_temperature") == 5.0

    def test_set_temp_at_max_boundary_writes(self):
        device = _make_hc_device()
        entity = _make_hc(device)
        hass, writes = _make_hass()
        entity.hass = hass

        _run(entity.async_set_temperature(**{ATTR_TEMPERATURE: 30.0}))

        assert writes.get("setpoint_temperature") == 30.0


# ===========================================================================
# HeatingCircuit — async_set_hvac_mode (lines 377-384)
# ===========================================================================

class TestHeatingCircuitSetHvacMode:
    """Lines 375-386: HeatingCircuit.async_set_hvac_mode."""

    def test_set_auto_writes_automatic_operation_mode(self):
        device = _make_hc_device(operation_mode=OM_HC.MANUAL)
        entity = _make_hc(device)
        hass, writes = _make_hass()
        entity.hass = hass

        _run(entity.async_set_hvac_mode(HVACMode.AUTO))

        assert writes.get("operation_mode") == OM_HC.AUTOMATIC

    def test_set_heat_writes_manual_operation_mode(self):
        device = _make_hc_device(operation_mode=OM_HC.AUTOMATIC)
        entity = _make_hc(device)
        hass, writes = _make_hass()
        entity.hass = hass

        _run(entity.async_set_hvac_mode(HVACMode.HEAT))

        assert writes.get("operation_mode") == OM_HC.MANUAL

    def test_invalid_hvac_mode_is_noop(self):
        device = _make_hc_device()
        entity = _make_hc(device)
        hass, writes = _make_hass()
        entity.hass = hass

        _run(entity.async_set_hvac_mode(HVACMode.OFF))

        # OFF not in hvac_modes for HeatingCircuit → noop
        assert writes == {}

    def test_invalid_cool_mode_is_noop(self):
        device = _make_hc_device()
        entity = _make_hc(device)
        hass, writes = _make_hass()
        entity.hass = hass

        _run(entity.async_set_hvac_mode(HVACMode.COOL))

        assert writes == {}


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
        entity._name = "Test"
        entity._attr_unique_id = "r_d"
        return entity

    def test_hvac_action_heating_when_has_demand(self):
        from homeassistant.components.climate.const import HVACAction
        entity = self._entity(has_demand=True, summer_mode=False)
        assert entity.hvac_action == HVACAction.HEATING

    def test_hvac_action_idle_when_no_demand(self):
        from homeassistant.components.climate.const import HVACAction
        entity = self._entity(has_demand=False, summer_mode=False)
        assert entity.hvac_action == HVACAction.IDLE

    def test_hvac_action_off_when_summer_mode(self):
        from homeassistant.components.climate.const import HVACAction
        entity = self._entity(has_demand=True, summer_mode=True)
        assert entity.hvac_action == HVACAction.OFF

    def test_hvac_action_off_overrides_has_demand(self):
        """Even when has_demand=True, summer_mode (OFF) wins."""
        from homeassistant.components.climate.const import HVACAction
        entity = self._entity(has_demand=True, summer_mode=True)
        assert entity.hvac_action == HVACAction.OFF


# ===========================================================================
# #196 / P2-A — async_turn_off from ECO now actually turns off (#196)
# ===========================================================================

class TestTurnOffFromEco:
    """#196: turn_off must work even when preset_mode==ECO.

    Old code: async_set_hvac_mode returned early if preset==ECO → summer_mode
    never written → device stayed on.
    New code: exit ECO (low=False) first, then write summer_mode=True.
    """

    def test_turn_off_from_eco_exits_eco_and_sets_summer_mode(self):
        """In ECO mode, turn_off must clear low AND set summer_mode=True."""
        device = _make_cc_device(low=True, summer_mode=False,
                                 operation_mode_value="MANUAL")
        entity = _make_cc(device)
        hass, writes = _make_hass()
        entity.hass = hass

        _run(entity.async_turn_off())

        assert writes.get("low") is False, "ECO (low) was not cleared before turning off"
        assert writes.get("summer_mode") is True, "summer_mode was not set to True"

    def test_set_hvac_mode_off_from_eco_clears_eco(self):
        """async_set_hvac_mode(OFF) in ECO must clear low before setting summer_mode."""
        device = _make_cc_device(low=True, summer_mode=False,
                                 operation_mode_value="AUTOMATIC")
        entity = _make_cc(device)
        hass, writes = _make_hass()
        entity.hass = hass

        _run(entity.async_set_hvac_mode(HVACMode.OFF))

        assert writes.get("low") is False
        assert writes.get("summer_mode") is True

    def test_set_hvac_mode_heat_from_eco_clears_eco(self):
        """async_set_hvac_mode(HEAT) in ECO must clear low before setting summer_mode=False.

        PR #329: HEAT no longer writes operation_mode; it only sets summer_mode=False.
        """
        device = _make_cc_device(low=True, summer_mode=False,
                                 operation_mode_value="AUTOMATIC")
        entity = _make_cc(device)
        hass, writes = _make_hass()
        entity.hass = hass

        _run(entity.async_set_hvac_mode(HVACMode.HEAT))

        assert writes.get("low") is False
        assert writes.get("summer_mode") is False

    def test_set_hvac_mode_off_from_eco_clears_eco_via_preset(self):
        """async_set_hvac_mode(OFF) in ECO must clear low and set summer_mode.

        PR #329: AUTO is now a preset. Use OFF to verify eco-exit + direction change.
        """
        device = _make_cc_device(low=True, summer_mode=False,
                                 operation_mode_value="MANUAL")
        entity = _make_cc(device)
        hass, writes = _make_hass()
        entity.hass = hass

        _run(entity.async_set_hvac_mode(HVACMode.OFF))

        assert writes.get("low") is False
        assert writes.get("summer_mode") is True

    def test_set_hvac_mode_not_in_eco_does_not_touch_low(self):
        """When not in ECO, low must not be written."""
        device = _make_cc_device(low=False, summer_mode=False,
                                 operation_mode_value="MANUAL")
        entity = _make_cc(device)
        hass, writes = _make_hass()
        entity.hass = hass

        _run(entity.async_set_hvac_mode(HVACMode.HEAT))

        assert "low" not in writes, "low must not be touched when not in ECO"


# ===========================================================================
# P2-C — COOL branch now also writes operation_mode=MANUAL
# ===========================================================================

class TestCoolSetsDirectionAxis:
    """PR #329: COOL sets cooling_mode=True + summer_mode=False (direction axis only).

    operation_mode (regulation axis) is NOT touched by set_hvac_mode; it is managed
    via set_preset_mode. The old P2-C MANUAL write is removed.
    """

    def test_cool_writes_cooling_mode_and_summer_false(self):
        device = _make_cc_device(
            summer_mode=False,
            supports_cooling=True,
            cooling_mode=False,
            operation_mode_value="AUTOMATIC",
        )
        entity = _make_cc(device)
        hass, writes = _make_hass()
        entity.hass = hass

        _run(entity.async_set_hvac_mode(HVACMode.COOL))

        assert writes.get("cooling_mode") is True, "cooling_mode must be set to True"
        assert writes.get("summer_mode") is False, "summer_mode must be cleared"
        assert "operation_mode" not in writes, (
            "operation_mode must NOT be written by set_hvac_mode in PR #329 design"
        )

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
        hass, writes = _make_hass()
        entity.hass = hass

        _run(entity.async_set_hvac_mode(HVACMode.COOL))

        assert writes.get("low") is False
        assert writes.get("cooling_mode") is True
        assert writes.get("summer_mode") is False
