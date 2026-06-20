"""Unit tests for climate.py — targets missing lines 261-295, 301-307, 334-335, 364-384.

Also covers line 213 (async_set_hvac_mode ECO early-return).
Does NOT duplicate test_climate.py / test_heating_circuit.py assertions.
Pattern: Cls.__new__(Cls) + SimpleNamespace fake device; asyncio.run() for async.
"""

import asyncio
from types import SimpleNamespace

import pytest

from boschshcpy import SHCHeatingCircuit
from boschshcpy.services_impl import RoomClimateControlService
from homeassistant.components.climate.const import (
    HVACMode,
    PRESET_BOOST,
    PRESET_ECO,
    PRESET_NONE,
)
from homeassistant.const import ATTR_TEMPERATURE

from custom_components.bosch_shc.climate import ClimateControl, HeatingCircuit

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
    """Line 213: async_set_hvac_mode must return early when preset=ECO."""

    def test_set_hvac_mode_ignored_in_eco(self):
        device = _make_cc_device(low=True, operation_mode_value="MANUAL")
        entity = _make_cc(device)
        hass, writes = _make_hass()
        entity.hass = hass

        _run(entity.async_set_hvac_mode(HVACMode.AUTO))

        # ECO guard fires first → nothing written
        assert writes == {}, f"Expected no writes in ECO, got {writes}"


# ===========================================================================
# ClimateControl — async_set_preset_mode (lines 261-295)
# ===========================================================================

class TestSetPresetModeNone:
    """PRESET_NONE: clears boost_mode (if active) and low (if active)."""

    def test_none_clears_boost_mode(self):
        device = _make_cc_device(boost_mode=True, low=False, supports_boost_mode=True)
        entity = _make_cc(device)
        hass, writes = _make_hass()
        entity.hass = hass

        _run(entity.async_set_preset_mode(PRESET_NONE))

        assert writes.get("boost_mode") is False

    def test_none_clears_low_when_low_is_true(self):
        device = _make_cc_device(boost_mode=False, low=True, supports_boost_mode=True)
        entity = _make_cc(device)
        hass, writes = _make_hass()
        entity.hass = hass

        _run(entity.async_set_preset_mode(PRESET_NONE))

        assert writes.get("low") is False

    def test_none_no_write_when_already_none(self):
        device = _make_cc_device(boost_mode=False, low=False, supports_boost_mode=True)
        entity = _make_cc(device)
        hass, writes = _make_hass()
        entity.hass = hass

        _run(entity.async_set_preset_mode(PRESET_NONE))

        # Neither boost_mode nor low was active → no writes needed
        assert "boost_mode" not in writes
        assert "low" not in writes

    def test_none_clears_both_boost_and_low(self):
        device = _make_cc_device(boost_mode=True, low=True, supports_boost_mode=True)
        entity = _make_cc(device)
        hass, writes = _make_hass()
        entity.hass = hass

        _run(entity.async_set_preset_mode(PRESET_NONE))

        assert writes.get("boost_mode") is False
        assert writes.get("low") is False

    def test_invalid_preset_mode_is_ignored(self):
        device = _make_cc_device()
        entity = _make_cc(device)
        hass, writes = _make_hass()
        entity.hass = hass

        _run(entity.async_set_preset_mode("INVALID_PRESET"))

        assert writes == {}


class TestSetPresetModeBoost:
    """PRESET_BOOST: sets boost_mode=True; clears low if active."""

    def test_boost_sets_boost_mode(self):
        device = _make_cc_device(boost_mode=False, low=False, supports_boost_mode=True)
        entity = _make_cc(device)
        hass, writes = _make_hass()
        entity.hass = hass

        _run(entity.async_set_preset_mode(PRESET_BOOST))

        assert writes.get("boost_mode") is True

    def test_boost_already_active_no_redundant_write(self):
        device = _make_cc_device(boost_mode=True, low=False, supports_boost_mode=True)
        entity = _make_cc(device)
        hass, writes = _make_hass()
        entity.hass = hass

        _run(entity.async_set_preset_mode(PRESET_BOOST))

        assert "boost_mode" not in writes

    def test_boost_clears_low_when_low_active(self):
        device = _make_cc_device(boost_mode=False, low=True, supports_boost_mode=True)
        entity = _make_cc(device)
        hass, writes = _make_hass()
        entity.hass = hass

        _run(entity.async_set_preset_mode(PRESET_BOOST))

        assert writes.get("low") is False

    def test_boost_no_low_write_when_already_false(self):
        device = _make_cc_device(boost_mode=False, low=False, supports_boost_mode=True)
        entity = _make_cc(device)
        hass, writes = _make_hass()
        entity.hass = hass

        _run(entity.async_set_preset_mode(PRESET_BOOST))

        assert "low" not in writes


class TestSetPresetModeEco:
    """PRESET_ECO: sets low=True; clears boost_mode if active."""

    def test_eco_sets_low(self):
        device = _make_cc_device(boost_mode=False, low=False, supports_boost_mode=True)
        entity = _make_cc(device)
        hass, writes = _make_hass()
        entity.hass = hass

        _run(entity.async_set_preset_mode(PRESET_ECO))

        assert writes.get("low") is True

    def test_eco_already_low_no_redundant_write(self):
        device = _make_cc_device(boost_mode=False, low=True, supports_boost_mode=True)
        entity = _make_cc(device)
        hass, writes = _make_hass()
        entity.hass = hass

        _run(entity.async_set_preset_mode(PRESET_ECO))

        assert "low" not in writes

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
