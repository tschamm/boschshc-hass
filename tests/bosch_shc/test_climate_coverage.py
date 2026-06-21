"""Additional pure-unit coverage for climate.py.

Focus: branches / guard paths not yet exercised by existing climate test files.
- ClimateControl: hvac_mode all branches, hvac_modes, preset_modes, min/max temp,
  async_set_temperature (OFF-mode skip, HVAC-mode kwarg path), async_set_hvac_mode
  (HEAT/OFF without cooling, COOL branch, ECO preset guard, None guard),
  async_set_preset_mode error paths with SHCException.
- HeatingCircuit: class-level attributes, async_set_temperature error swallowed.
- SHCException catch in async_set_hvac_mode and async_set_preset_mode.

Pattern: Cls.__new__(Cls) + SimpleNamespace device; asyncio.run() for async.
No HA harness, no MockConfigEntry.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from boschshcpy import SHCHeatingCircuit
from boschshcpy.exceptions import JSONRPCError, SHCException
from boschshcpy.services_impl import RoomClimateControlService
from homeassistant.components.climate.const import (
    ATTR_HVAC_MODE,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
    PRESET_BOOST,
    PRESET_ECO,
    PRESET_NONE,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature

from custom_components.bosch_shc.climate import ClimateControl, HeatingCircuit

# ---------------------------------------------------------------------------
# Constants / shared enum refs
# ---------------------------------------------------------------------------

OM_CC = RoomClimateControlService.OperationMode
OM_HC = SHCHeatingCircuit.HeatingCircuitService.OperationMode


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

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


def _make_cc(device, *, attr_name="Test Room"):
    entity = ClimateControl.__new__(ClimateControl)
    entity._device = device
    entity._attr_name = attr_name
    entity._attr_unique_id = "r_d"
    return entity


def _make_hass(writes=None):
    """Minimal hass stub; records setattr calls."""
    captured = writes if writes is not None else {}

    async def _exec(func, *args):
        if func is setattr and len(args) == 3:
            captured[args[1]] = args[2]
        return func(*args)

    return SimpleNamespace(async_add_executor_job=_exec), captured


def _make_hass_raises(exc_instance):
    """hass stub whose async_add_executor_job raises exc_instance."""
    async def _raise(*args, **kwargs):
        raise exc_instance

    return SimpleNamespace(async_add_executor_job=_raise)


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_hc(*, on=False, operation_mode=None, setpoint=20.0):
    entity = HeatingCircuit.__new__(HeatingCircuit)
    entity._device = SimpleNamespace(
        operation_mode=operation_mode or OM_HC.AUTOMATIC,
        on=on,
        setpoint_temperature=setpoint,
        root_device_id="r",
        id="h",
    )
    entity._attr_unique_id = "r_h"
    return entity


# ===========================================================================
# ClimateControl — hvac_mode property (line 104-118)
# ===========================================================================

class TestHvacModeProperty:
    """All branches of ClimateControl.hvac_mode."""

    def test_summer_mode_returns_off(self):
        """summer_mode=True → HVACMode.OFF regardless of anything else."""
        device = _make_cc_device(summer_mode=True, operation_mode_value="AUTOMATIC")
        entity = _make_cc(device)
        assert entity.hvac_mode == HVACMode.OFF

    def test_supports_cooling_and_cooling_mode_returns_cool(self):
        """supports_cooling=True + cooling_mode=True → HVACMode.COOL."""
        device = _make_cc_device(
            summer_mode=False, supports_cooling=True, cooling_mode=True,
            operation_mode_value="AUTOMATIC",
        )
        entity = _make_cc(device)
        assert entity.hvac_mode == HVACMode.COOL

    def test_supports_cooling_but_not_active_returns_auto(self):
        """supports_cooling=True but cooling_mode=False + AUTOMATIC → HVACMode.AUTO."""
        device = _make_cc_device(
            summer_mode=False, supports_cooling=True, cooling_mode=False,
            operation_mode_value="AUTOMATIC",
        )
        entity = _make_cc(device)
        assert entity.hvac_mode == HVACMode.AUTO

    def test_operation_mode_automatic_returns_auto(self):
        """AUTOMATIC operation mode + no summer/cooling → HVACMode.AUTO."""
        device = _make_cc_device(summer_mode=False, operation_mode_value="AUTOMATIC")
        entity = _make_cc(device)
        assert entity.hvac_mode == HVACMode.AUTO

    def test_operation_mode_manual_returns_heat(self):
        """MANUAL operation mode → HVACMode.HEAT."""
        device = _make_cc_device(summer_mode=False, operation_mode_value="MANUAL")
        entity = _make_cc(device)
        assert entity.hvac_mode == HVACMode.HEAT

    def test_no_cooling_support_does_not_short_circuit_at_cool_branch(self):
        """supports_cooling=False skips the cooling branch → falls through to AUTO."""
        device = _make_cc_device(
            summer_mode=False, supports_cooling=False, cooling_mode=True,
            operation_mode_value="AUTOMATIC",
        )
        entity = _make_cc(device)
        # cooling_mode=True but supports_cooling=False → ignored → AUTO
        assert entity.hvac_mode == HVACMode.AUTO


# ===========================================================================
# ClimateControl — hvac_modes property (line 121-126)
# ===========================================================================

class TestHvacModesProperty:
    """ClimateControl.hvac_modes includes COOL only when supports_cooling."""

    def test_base_modes_without_cooling(self):
        device = _make_cc_device(supports_cooling=False)
        entity = _make_cc(device)
        modes = entity.hvac_modes
        assert HVACMode.AUTO in modes
        assert HVACMode.HEAT in modes
        assert HVACMode.OFF in modes
        assert HVACMode.COOL not in modes

    def test_cool_added_when_supports_cooling(self):
        device = _make_cc_device(supports_cooling=True)
        entity = _make_cc(device)
        assert HVACMode.COOL in entity.hvac_modes

    def test_modes_count_without_cooling(self):
        device = _make_cc_device(supports_cooling=False)
        entity = _make_cc(device)
        assert len(entity.hvac_modes) == 3

    def test_modes_count_with_cooling(self):
        device = _make_cc_device(supports_cooling=True)
        entity = _make_cc(device)
        assert len(entity.hvac_modes) == 4


# ===========================================================================
# ClimateControl — preset_modes property (line 153-158)
# ===========================================================================

class TestPresetModesProperty:
    """preset_modes always includes NONE + ECO; BOOST added when supported."""

    def test_base_presets_without_boost(self):
        device = _make_cc_device(supports_boost_mode=False)
        entity = _make_cc(device)
        modes = entity.preset_modes
        assert PRESET_NONE in modes
        assert PRESET_ECO in modes
        assert PRESET_BOOST not in modes

    def test_boost_added_when_supported(self):
        device = _make_cc_device(supports_boost_mode=True)
        entity = _make_cc(device)
        assert PRESET_BOOST in entity.preset_modes

    def test_preset_modes_count_without_boost(self):
        device = _make_cc_device(supports_boost_mode=False)
        entity = _make_cc(device)
        assert len(entity.preset_modes) == 2

    def test_preset_modes_count_with_boost(self):
        device = _make_cc_device(supports_boost_mode=True)
        entity = _make_cc(device)
        assert len(entity.preset_modes) == 3


# ===========================================================================
# ClimateControl — min_temp / max_temp (lines 88, 93)
# ===========================================================================

class TestTempBounds:
    def test_min_temp(self):
        entity = _make_cc(_make_cc_device())
        assert entity.min_temp == 5.0

    def test_max_temp(self):
        entity = _make_cc(_make_cc_device())
        assert entity.max_temp == 30.0


# ===========================================================================
# ClimateControl — device_name (line 69-71)
# ===========================================================================

class TestDeviceName:
    def test_device_name_returns_attr_name(self):
        entity = _make_cc(_make_cc_device(), attr_name="My Room")
        assert entity.device_name == "My Room"

    def test_device_name_matches_name(self):
        entity = _make_cc(_make_cc_device(), attr_name="Kitchen")
        assert entity.device_name == entity.name


# ===========================================================================
# ClimateControl — async_set_temperature: OFF/ECO skip + HVAC mode kwarg
# ===========================================================================

class TestSetTemperatureGuards:
    """Lines 172-208: guard paths in async_set_temperature."""

    def test_off_mode_skips_setpoint_write(self):
        """hvac_mode=OFF → no setpoint written even with valid temperature."""
        device = _make_cc_device(summer_mode=True)
        entity = _make_cc(device)
        hass, writes = _make_hass()
        entity.hass = hass
        _run(entity.async_set_temperature(**{ATTR_TEMPERATURE: 22.0}))
        assert "setpoint_temperature" not in writes

    def test_hvac_mode_kwarg_sets_mode_first(self):
        """ATTR_HVAC_MODE kwarg is forwarded to async_set_hvac_mode before write."""
        device = _make_cc_device(summer_mode=False, operation_mode_value="AUTOMATIC")
        entity = _make_cc(device)
        hass, writes = _make_hass()
        entity.hass = hass
        _run(entity.async_set_temperature(
            **{ATTR_TEMPERATURE: 22.0, ATTR_HVAC_MODE: HVACMode.HEAT}
        ))
        # HEAT mode sets operation_mode → MANUAL
        assert writes.get("operation_mode") == OM_CC.MANUAL
        assert writes.get("setpoint_temperature") == 22.0

    def test_hvac_mode_kwarg_none_does_not_crash(self):
        """None ATTR_HVAC_MODE is passed; async_set_hvac_mode must handle None."""
        device = _make_cc_device(summer_mode=False, operation_mode_value="MANUAL")
        entity = _make_cc(device)
        hass, writes = _make_hass()
        entity.hass = hass
        _run(entity.async_set_temperature(
            **{ATTR_TEMPERATURE: 21.0, ATTR_HVAC_MODE: None}
        ))
        # None not in hvac_modes → early return in async_set_hvac_mode → no mode write
        assert "operation_mode" not in writes
        # setpoint still written since mode remains HEAT
        assert writes.get("setpoint_temperature") == 21.0

    def test_temperature_not_in_kwargs_returns_early(self):
        """Missing ATTR_TEMPERATURE → returns without any write."""
        device = _make_cc_device(summer_mode=False, operation_mode_value="MANUAL")
        entity = _make_cc(device)
        hass, writes = _make_hass()
        entity.hass = hass
        _run(entity.async_set_temperature())
        assert writes == {}

    def test_temperature_below_min_skipped(self):
        """Temperature < 5.0 must not be written."""
        device = _make_cc_device(summer_mode=False, operation_mode_value="MANUAL")
        entity = _make_cc(device)
        hass, writes = _make_hass()
        entity.hass = hass
        _run(entity.async_set_temperature(**{ATTR_TEMPERATURE: 4.9}))
        assert "setpoint_temperature" not in writes

    def test_temperature_above_max_skipped(self):
        """Temperature > 30.0 must not be written."""
        device = _make_cc_device(summer_mode=False, operation_mode_value="MANUAL")
        entity = _make_cc(device)
        hass, writes = _make_hass()
        entity.hass = hass
        _run(entity.async_set_temperature(**{ATTR_TEMPERATURE: 30.1}))
        assert "setpoint_temperature" not in writes

    def test_temperature_boundary_min_written(self):
        """Temperature exactly 5.0 is written."""
        device = _make_cc_device(summer_mode=False, operation_mode_value="MANUAL")
        entity = _make_cc(device)
        hass, writes = _make_hass()
        entity.hass = hass
        _run(entity.async_set_temperature(**{ATTR_TEMPERATURE: 5.0}))
        assert writes.get("setpoint_temperature") == 5.0

    def test_temperature_boundary_max_written(self):
        """Temperature exactly 30.0 is written."""
        device = _make_cc_device(summer_mode=False, operation_mode_value="MANUAL")
        entity = _make_cc(device)
        hass, writes = _make_hass()
        entity.hass = hass
        _run(entity.async_set_temperature(**{ATTR_TEMPERATURE: 30.0}))
        assert writes.get("setpoint_temperature") == 30.0

    def test_shcexception_from_setpoint_is_swallowed(self):
        """SHCException from executor must not propagate."""
        device = _make_cc_device(summer_mode=False, operation_mode_value="MANUAL")
        entity = _make_cc(device)
        entity.hass = _make_hass_raises(SHCException("err"))
        # Must not raise
        _run(entity.async_set_temperature(**{ATTR_TEMPERATURE: 21.0}))

    def test_jsonrpcerror_from_setpoint_is_swallowed(self):
        """JSONRPCError from executor must not propagate."""
        device = _make_cc_device(summer_mode=False, operation_mode_value="MANUAL")
        entity = _make_cc(device)
        entity.hass = _make_hass_raises(JSONRPCError(-32001, "err"))
        _run(entity.async_set_temperature(**{ATTR_TEMPERATURE: 21.0}))


# ===========================================================================
# ClimateControl — async_set_hvac_mode: HEAT/OFF (no cooling)
# ===========================================================================

class TestSetHvacModeNoCooling:
    """async_set_hvac_mode with supports_cooling=False (lines 232-260)."""

    def test_heat_mode_no_cooling_sets_manual(self):
        device = _make_cc_device(summer_mode=False, supports_cooling=False,
                                 operation_mode_value="AUTOMATIC")
        entity = _make_cc(device)
        hass, writes = _make_hass()
        entity.hass = hass
        _run(entity.async_set_hvac_mode(HVACMode.HEAT))
        assert writes.get("summer_mode") is False
        assert writes.get("operation_mode") == OM_CC.MANUAL
        assert "cooling_mode" not in writes

    def test_auto_mode_no_cooling_sets_automatic(self):
        device = _make_cc_device(summer_mode=False, supports_cooling=False,
                                 operation_mode_value="MANUAL")
        entity = _make_cc(device)
        hass, writes = _make_hass()
        entity.hass = hass
        _run(entity.async_set_hvac_mode(HVACMode.AUTO))
        assert writes.get("summer_mode") is False
        assert writes.get("operation_mode") == OM_CC.AUTOMATIC
        assert "cooling_mode" not in writes

    def test_off_mode_no_cooling_sets_summer_mode(self):
        device = _make_cc_device(summer_mode=False, supports_cooling=False,
                                 operation_mode_value="AUTOMATIC")
        entity = _make_cc(device)
        hass, writes = _make_hass()
        entity.hass = hass
        _run(entity.async_set_hvac_mode(HVACMode.OFF))
        assert writes.get("summer_mode") is True
        assert "cooling_mode" not in writes

    def test_off_mode_with_cooling_clears_cooling_first(self):
        """OFF mode + supports_cooling=True → cooling_mode=False first, then summer."""
        device = _make_cc_device(summer_mode=False, supports_cooling=True,
                                 cooling_mode=True, operation_mode_value="AUTOMATIC")
        entity = _make_cc(device)
        hass, writes = _make_hass()
        entity.hass = hass
        _run(entity.async_set_hvac_mode(HVACMode.OFF))
        assert writes.get("cooling_mode") is False
        assert writes.get("summer_mode") is True

    def test_cool_mode_not_available_without_support(self):
        """COOL not in hvac_modes when supports_cooling=False → noop."""
        device = _make_cc_device(summer_mode=False, supports_cooling=False)
        entity = _make_cc(device)
        hass, writes = _make_hass()
        entity.hass = hass
        _run(entity.async_set_hvac_mode(HVACMode.COOL))
        assert writes == {}

    def test_invalid_mode_is_noop(self):
        """Unsupported string mode → early return, no writes."""
        device = _make_cc_device()
        entity = _make_cc(device)
        hass, writes = _make_hass()
        entity.hass = hass
        _run(entity.async_set_hvac_mode("INVALID_MODE"))
        assert writes == {}

    def test_shcexception_in_hvac_mode_swallowed(self):
        """SHCException from executor in async_set_hvac_mode must not propagate."""
        device = _make_cc_device(summer_mode=False, supports_cooling=False, low=False)
        entity = _make_cc(device)
        entity.hass = _make_hass_raises(SHCException("conn error"))
        _run(entity.async_set_hvac_mode(HVACMode.AUTO))

    def test_jsonrpcerror_in_hvac_mode_swallowed(self):
        """JSONRPCError in async_set_hvac_mode must not propagate."""
        device = _make_cc_device(summer_mode=False, supports_cooling=False, low=False)
        entity = _make_cc(device)
        entity.hass = _make_hass_raises(JSONRPCError(-32001, "timeout"))
        _run(entity.async_set_hvac_mode(HVACMode.HEAT))

    def test_eco_exits_then_hvac_mode_written(self):
        """P2-B: preset_mode=ECO → async_set_hvac_mode exits ECO first (low=False),
        then writes the requested HVAC mode.  The old early-return is gone. #196"""
        device = _make_cc_device(low=True, operation_mode_value="MANUAL")
        entity = _make_cc(device)
        hass, writes = _make_hass()
        entity.hass = hass
        _run(entity.async_set_hvac_mode(HVACMode.AUTO))
        # ECO exit: low must be cleared
        assert writes.get("low") is False
        # HVAC mode write must proceed despite starting in ECO
        assert writes.get("operation_mode") == OM_CC.AUTOMATIC
        assert writes.get("summer_mode") is False


# ===========================================================================
# ClimateControl — async_set_preset_mode: SHCException paths
# ===========================================================================

class TestSetPresetModeExceptions:
    """SHCException must be swallowed in async_set_preset_mode (line 308)."""

    def test_shcexception_preset_none_swallowed(self):
        device = _make_cc_device(boost_mode=True, low=False, supports_boost_mode=True)
        entity = _make_cc(device)
        entity.hass = _make_hass_raises(SHCException("err"))
        _run(entity.async_set_preset_mode(PRESET_NONE))

    def test_shcexception_preset_boost_swallowed(self):
        device = _make_cc_device(boost_mode=False, low=False, supports_boost_mode=True)
        entity = _make_cc(device)
        entity.hass = _make_hass_raises(SHCException("err"))
        _run(entity.async_set_preset_mode(PRESET_BOOST))

    def test_shcexception_preset_eco_swallowed(self):
        device = _make_cc_device(boost_mode=False, low=False, supports_boost_mode=True)
        entity = _make_cc(device)
        entity.hass = _make_hass_raises(SHCException("err"))
        _run(entity.async_set_preset_mode(PRESET_ECO))

    def test_jsonrpc_preset_boost_swallowed(self):
        device = _make_cc_device(boost_mode=False, low=False, supports_boost_mode=True)
        entity = _make_cc(device)
        entity.hass = _make_hass_raises(JSONRPCError(-32001, "err"))
        _run(entity.async_set_preset_mode(PRESET_BOOST))

    def test_preset_mode_not_in_presets_returns_early(self):
        """Preset not in preset_modes → no executor call, no error."""
        device = _make_cc_device(supports_boost_mode=False)
        entity = _make_cc(device)
        entity.hass = _make_hass_raises(SHCException("should not reach"))
        # BOOST not in preset_modes when supports_boost_mode=False → early return
        _run(entity.async_set_preset_mode(PRESET_BOOST))


# ===========================================================================
# ClimateControl — async_turn_on / async_turn_off
# ===========================================================================

class TestTurnOnOffClimate:
    """Lines 315-323: async_turn_on and async_turn_off."""

    def test_turn_on_when_off_calls_heat(self):
        """summer_mode=True (OFF) → turn_on must set summer_mode=False."""
        device = _make_cc_device(summer_mode=True, low=False)
        entity = _make_cc(device)
        hass, writes = _make_hass()
        entity.hass = hass
        _run(entity.async_turn_on())
        assert writes.get("summer_mode") is False

    def test_turn_on_when_already_on_is_noop(self):
        """Mode=HEAT (not OFF) → turn_on is a noop."""
        device = _make_cc_device(summer_mode=False, operation_mode_value="MANUAL")
        entity = _make_cc(device)
        hass, writes = _make_hass()
        entity.hass = hass
        _run(entity.async_turn_on())
        assert "summer_mode" not in writes

    def test_turn_off_when_on_sets_summer_mode(self):
        """Mode=AUTO (not OFF) → turn_off sets summer_mode=True."""
        device = _make_cc_device(summer_mode=False, operation_mode_value="AUTOMATIC")
        entity = _make_cc(device)
        hass, writes = _make_hass()
        entity.hass = hass
        _run(entity.async_turn_off())
        assert writes.get("summer_mode") is True

    def test_turn_off_when_already_off_is_noop(self):
        """summer_mode=True (already OFF) → turn_off is noop."""
        device = _make_cc_device(summer_mode=True)
        entity = _make_cc(device)
        hass, writes = _make_hass()
        entity.hass = hass
        _run(entity.async_turn_off())
        assert "summer_mode" not in writes


# ===========================================================================
# ClimateControl — hvac_action (lines 129-138)
# ===========================================================================

class TestClimateControlHvacAction:
    """All branches of ClimateControl.hvac_action."""

    def test_off_when_summer_mode(self):
        device = _make_cc_device(summer_mode=True, has_demand=True)
        entity = _make_cc(device)
        assert entity.hvac_action == HVACAction.OFF

    def test_heating_when_has_demand(self):
        device = _make_cc_device(summer_mode=False, has_demand=True,
                                 operation_mode_value="AUTOMATIC")
        entity = _make_cc(device)
        assert entity.hvac_action == HVACAction.HEATING

    def test_idle_when_no_demand(self):
        device = _make_cc_device(summer_mode=False, has_demand=False,
                                 operation_mode_value="AUTOMATIC")
        entity = _make_cc(device)
        assert entity.hvac_action == HVACAction.IDLE

    def test_missing_has_demand_attr_treated_as_false(self):
        """getattr guard: device without has_demand attribute → IDLE."""
        device = _make_cc_device(summer_mode=False, operation_mode_value="AUTOMATIC")
        del device.has_demand  # simulate older lib without attribute
        entity = _make_cc(device)
        assert entity.hvac_action == HVACAction.IDLE


# ===========================================================================
# ClimateControl — supported_features (line 161-168)
# ===========================================================================

class TestSupportedFeatures:
    def test_all_four_features_present(self):
        entity = _make_cc(_make_cc_device())
        feats = entity.supported_features
        assert feats & ClimateEntityFeature.TARGET_TEMPERATURE
        assert feats & ClimateEntityFeature.PRESET_MODE
        assert feats & ClimateEntityFeature.TURN_OFF
        assert feats & ClimateEntityFeature.TURN_ON

    def test_supported_features_exact(self):
        entity = _make_cc(_make_cc_device())
        expected = (
            ClimateEntityFeature.TARGET_TEMPERATURE
            | ClimateEntityFeature.PRESET_MODE
            | ClimateEntityFeature.TURN_OFF
            | ClimateEntityFeature.TURN_ON
        )
        assert entity.supported_features == expected


# ===========================================================================
# HeatingCircuit — class-level attributes (lines 335-341)
# ===========================================================================

class TestHeatingCircuitClassAttrs:
    """HeatingCircuit class-level _attr_* defaults."""

    def test_temperature_unit_celsius(self):
        entity = _make_hc()
        assert entity._attr_temperature_unit == UnitOfTemperature.CELSIUS

    def test_max_temp_30(self):
        entity = _make_hc()
        assert entity._attr_max_temp == 30.0

    def test_min_temp_5(self):
        entity = _make_hc()
        assert entity._attr_min_temp == 5.0

    def test_hvac_modes_auto_and_heat_only(self):
        entity = _make_hc()
        assert entity._attr_hvac_modes == [HVACMode.AUTO, HVACMode.HEAT]
        assert HVACMode.OFF not in entity._attr_hvac_modes
        assert HVACMode.COOL not in entity._attr_hvac_modes

    def test_supported_features_target_temperature_only(self):
        entity = _make_hc()
        assert entity._attr_supported_features == ClimateEntityFeature.TARGET_TEMPERATURE

    def test_target_temperature_step(self):
        entity = _make_hc()
        assert entity._attr_target_temperature_step == 0.5

    def test_current_temperature_is_none(self):
        entity = _make_hc()
        assert entity.current_temperature is None

    def test_target_temperature_reads_device(self):
        entity = _make_hc(setpoint=18.5)
        assert entity.target_temperature == 18.5

    def test_hvac_action_heating_when_on(self):
        entity = _make_hc(on=True)
        assert entity.hvac_action == HVACAction.HEATING

    def test_hvac_action_idle_when_off(self):
        entity = _make_hc(on=False)
        assert entity.hvac_action == HVACAction.IDLE

    def test_hvac_mode_auto_from_automatic(self):
        entity = _make_hc(operation_mode=OM_HC.AUTOMATIC)
        assert entity.hvac_mode == HVACMode.AUTO

    def test_hvac_mode_heat_from_manual(self):
        entity = _make_hc(operation_mode=OM_HC.MANUAL)
        assert entity.hvac_mode == HVACMode.HEAT


# ===========================================================================
# HeatingCircuit — async_set_temperature error paths (lines 384-396)
# ===========================================================================

class TestHeatingCircuitSetTemperatureErrors:
    """SHCException and JSONRPCError must be swallowed in HC.async_set_temperature."""

    def _hc_raises(self, exc):
        entity = _make_hc()
        entity.hass = _make_hass_raises(exc)
        return entity

    def test_shcexception_swallowed(self):
        entity = self._hc_raises(SHCException("net err"))
        _run(entity.async_set_temperature(**{ATTR_TEMPERATURE: 22.0}))

    def test_jsonrpcerror_swallowed(self):
        entity = self._hc_raises(JSONRPCError(-32001, "timeout"))
        _run(entity.async_set_temperature(**{ATTR_TEMPERATURE: 22.0}))

    def test_none_temperature_noop_no_raise(self):
        entity = self._hc_raises(SHCException("should not reach"))
        _run(entity.async_set_temperature(**{ATTR_TEMPERATURE: None}))

    def test_out_of_range_low_noop(self):
        entity = self._hc_raises(SHCException("should not reach"))
        _run(entity.async_set_temperature(**{ATTR_TEMPERATURE: 4.9}))

    def test_out_of_range_high_noop(self):
        entity = self._hc_raises(SHCException("should not reach"))
        _run(entity.async_set_temperature(**{ATTR_TEMPERATURE: 30.1}))

    def test_warning_logged_on_shcexception(self):
        entity = self._hc_raises(SHCException("net err"))
        with patch("custom_components.bosch_shc.climate.LOGGER") as mock_log:
            _run(entity.async_set_temperature(**{ATTR_TEMPERATURE: 21.0}))
            mock_log.warning.assert_called_once()

    def test_warning_logged_on_jsonrpcerror(self):
        entity = self._hc_raises(JSONRPCError(-32001, "rpc err"))
        with patch("custom_components.bosch_shc.climate.LOGGER") as mock_log:
            _run(entity.async_set_temperature(**{ATTR_TEMPERATURE: 21.0}))
            mock_log.warning.assert_called_once()


# ===========================================================================
# HeatingCircuit — async_set_hvac_mode (lines 398-409)
# ===========================================================================

class TestHeatingCircuitSetHvacMode:
    """All branches of HeatingCircuit.async_set_hvac_mode."""

    def test_set_auto_writes_automatic(self):
        entity = _make_hc(operation_mode=OM_HC.MANUAL)
        hass, writes = _make_hass()
        entity.hass = hass
        _run(entity.async_set_hvac_mode(HVACMode.AUTO))
        assert writes.get("operation_mode") == OM_HC.AUTOMATIC

    def test_set_heat_writes_manual(self):
        entity = _make_hc(operation_mode=OM_HC.AUTOMATIC)
        hass, writes = _make_hass()
        entity.hass = hass
        _run(entity.async_set_hvac_mode(HVACMode.HEAT))
        assert writes.get("operation_mode") == OM_HC.MANUAL

    def test_invalid_mode_is_noop(self):
        entity = _make_hc()
        hass, writes = _make_hass()
        entity.hass = hass
        _run(entity.async_set_hvac_mode(HVACMode.OFF))
        assert writes == {}

    def test_cool_mode_is_noop(self):
        entity = _make_hc()
        hass, writes = _make_hass()
        entity.hass = hass
        _run(entity.async_set_hvac_mode(HVACMode.COOL))
        assert writes == {}
