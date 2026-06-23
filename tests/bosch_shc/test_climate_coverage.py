"""Additional pure-unit coverage for climate.py.

Focus: branches / guard paths not yet exercised by existing climate test files.
- ClimateControl: hvac_mode all branches, hvac_modes, preset_modes, min/max temp,
  async_set_temperature (OFF-mode skip, HVAC-mode kwarg path), async_set_hvac_mode
  (AUTO/HEAT/OFF without cooling, COOL branch, ECO preset guard, None guard),
  async_set_preset_mode error paths with SHCException.
- HeatingCircuit: class-level attributes, async_set_temperature error swallowed.
- SHCException catch in async_set_hvac_mode and async_set_preset_mode.

#334: AUTO is HVACMode.AUTO, MANUAL is HVACMode.HEAT.
preset_modes contains only boost/eco (override states); auto/manual removed.

Pattern: Cls.__new__(Cls) + SimpleNamespace device; asyncio.run() for async.
No HA harness, no MockConfigEntry.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from boschshcpy import SHCHeatingCircuit
from boschshcpy.exceptions import JSONRPCError, SHCException
from boschshcpy.services_impl import RoomClimateControlService
from homeassistant.components.climate.const import (
    ATTR_HVAC_MODE,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature

from custom_components.bosch_shc.climate import (
    ClimateControl,
    HeatingCircuit,
    PRESET_BOOST,
    PRESET_ECO,
)

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


def _make_cc(device, *, attr_name="Test Room"):
    entity = ClimateControl.__new__(ClimateControl)
    entity._device = device
    # Primary entity: friendly name = device name; the room label drives the
    # DEVICE name via the device_name property.
    entity._room_label = attr_name
    entity._attr_name = None
    entity._attr_unique_id = "r_d"
    return entity


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
        async_set_operation_mode=AsyncMock(),
        async_set_setpoint_temperature=AsyncMock(),
    )
    entity._attr_unique_id = "r_h"
    return entity


# ===========================================================================
# ClimateControl — hvac_mode property
# ===========================================================================

class TestHvacModeProperty:
    """All branches of ClimateControl.hvac_mode. #334: AUTOMATIC → AUTO, MANUAL → HEAT."""

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

    def test_supports_cooling_but_not_active_automatic_returns_auto(self):
        """#334: supports_cooling=True + cooling_mode=False + AUTOMATIC → HVACMode.AUTO."""
        device = _make_cc_device(
            summer_mode=False, supports_cooling=True, cooling_mode=False,
            operation_mode_value="AUTOMATIC",
        )
        entity = _make_cc(device)
        assert entity.hvac_mode == HVACMode.AUTO

    def test_operation_mode_automatic_returns_auto(self):
        """#334: AUTOMATIC operation mode → HVACMode.AUTO."""
        device = _make_cc_device(summer_mode=False, operation_mode_value="AUTOMATIC")
        entity = _make_cc(device)
        assert entity.hvac_mode == HVACMode.AUTO

    def test_operation_mode_manual_returns_heat(self):
        """MANUAL operation mode → HVACMode.HEAT."""
        device = _make_cc_device(summer_mode=False, operation_mode_value="MANUAL")
        entity = _make_cc(device)
        assert entity.hvac_mode == HVACMode.HEAT

    def test_no_cooling_support_skips_cool_branch_returns_auto(self):
        """supports_cooling=False + cooling_mode=True (impossible, but guard holds) → AUTO."""
        device = _make_cc_device(
            summer_mode=False, supports_cooling=False, cooling_mode=True,
            operation_mode_value="AUTOMATIC",
        )
        entity = _make_cc(device)
        # cooling_mode=True but supports_cooling=False → cooling branch skipped → AUTO
        assert entity.hvac_mode == HVACMode.AUTO


# ===========================================================================
# ClimateControl — hvac_modes property
# ===========================================================================

class TestHvacModesProperty:
    """ClimateControl.hvac_modes: #334 adds AUTO; includes COOL only when supports_cooling."""

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
        # #334: AUTO + HEAT + OFF = 3
        device = _make_cc_device(supports_cooling=False)
        entity = _make_cc(device)
        assert len(entity.hvac_modes) == 3

    def test_modes_count_with_cooling(self):
        # #334: AUTO + HEAT + COOL + OFF = 4
        device = _make_cc_device(supports_cooling=True)
        entity = _make_cc(device)
        assert len(entity.hvac_modes) == 4


# ===========================================================================
# ClimateControl — preset_modes property
# ===========================================================================

class TestPresetModesProperty:
    """#334: preset_modes is override-only (boost/eco); auto/manual removed.
    Returns None when no presets available."""

    def test_no_presets_when_no_boost_no_low(self):
        """Device without boost or low → preset_modes is None."""
        device = SimpleNamespace(
            boost_mode=False,
            summer_mode=False,
            supports_boost_mode=False,
            supports_cooling=False,
            cooling_mode=False,
            setpoint_temperature=20.0,
            temperature=19.0,
            operation_mode=OM_CC("AUTOMATIC"),
            root_device_id="r",
            id="d",
            # NOTE: no `low` attribute
        )
        entity = _make_cc(device)
        assert entity.preset_modes is None

    def test_eco_in_presets_when_device_has_low_attr(self):
        # _make_cc_device always adds `low` attribute → eco offered
        device = _make_cc_device(supports_boost_mode=False, low=False)
        entity = _make_cc(device)
        assert entity.preset_modes is not None
        assert PRESET_ECO in entity.preset_modes

    def test_boost_added_when_supported(self):
        device = _make_cc_device(supports_boost_mode=True)
        entity = _make_cc(device)
        assert PRESET_BOOST in entity.preset_modes

    def test_preset_modes_count_with_only_eco(self):
        # eco only (device has `low` attr, no boost)
        device = _make_cc_device(supports_boost_mode=False)
        entity = _make_cc(device)
        assert entity.preset_modes is not None
        assert len(entity.preset_modes) == 1
        assert PRESET_ECO in entity.preset_modes

    def test_preset_modes_count_with_boost_and_eco(self):
        # boost + eco = 2 (device has `low` attr)
        device = _make_cc_device(supports_boost_mode=True)
        entity = _make_cc(device)
        assert len(entity.preset_modes) == 2

    def test_no_auto_or_manual_in_preset_modes(self):
        """#334: 'auto' and 'manual' are never in preset_modes."""
        device = _make_cc_device(supports_boost_mode=True)
        entity = _make_cc(device)
        presets = entity.preset_modes or []
        assert "auto" not in presets
        assert "manual" not in presets


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
    def test_device_name_returns_room_label(self):
        entity = _make_cc(_make_cc_device(), attr_name="My Room")
        assert entity.device_name == "My Room"

    def test_primary_entity_has_no_own_name(self):
        entity = _make_cc(_make_cc_device(), attr_name="Kitchen")
        assert entity._attr_name is None
        assert entity.device_name == "Kitchen"


# ===========================================================================
# ClimateControl — async_set_temperature: OFF/ECO skip + HVAC mode kwarg
# ===========================================================================

class TestSetTemperatureGuards:
    """Lines 172-208: guard paths in async_set_temperature."""

    def test_off_mode_skips_setpoint_write(self):
        """hvac_mode=OFF → no setpoint written even with valid temperature."""
        device = _make_cc_device(summer_mode=True)
        entity = _make_cc(device)
        _run(entity.async_set_temperature(**{ATTR_TEMPERATURE: 22.0}))
        device.async_set_setpoint_temperature.assert_not_awaited()

    def test_hvac_mode_kwarg_heat_sets_mode_first(self):
        """ATTR_HVAC_MODE=HEAT kwarg is forwarded to async_set_hvac_mode before write.

        #334: HEAT sets operationMode=MANUAL + summer_mode=False.
        The setpoint is then written after the direction change.
        """
        device = _make_cc_device(summer_mode=False, operation_mode_value="MANUAL")
        entity = _make_cc(device)
        _run(entity.async_set_temperature(
            **{ATTR_TEMPERATURE: 22.0, ATTR_HVAC_MODE: HVACMode.HEAT}
        ))
        # HEAT writes summer_mode=False and operationMode=MANUAL
        device.async_set_summer_mode.assert_awaited_with(False)
        device.async_set_setpoint_temperature.assert_awaited_with(22.0)

    def test_hvac_mode_kwarg_none_does_not_crash(self):
        """None ATTR_HVAC_MODE is passed; async_set_hvac_mode must handle None."""
        device = _make_cc_device(summer_mode=False, operation_mode_value="MANUAL")
        entity = _make_cc(device)
        _run(entity.async_set_temperature(
            **{ATTR_TEMPERATURE: 21.0, ATTR_HVAC_MODE: None}
        ))
        # None not in hvac_modes → early return in async_set_hvac_mode → no mode write
        device.async_set_operation_mode.assert_not_awaited()
        # setpoint still written since mode remains HEAT
        device.async_set_setpoint_temperature.assert_awaited_with(21.0)

    def test_temperature_not_in_kwargs_returns_early(self):
        """Missing ATTR_TEMPERATURE → returns without any write."""
        device = _make_cc_device(summer_mode=False, operation_mode_value="MANUAL")
        entity = _make_cc(device)
        _run(entity.async_set_temperature())
        device.async_set_setpoint_temperature.assert_not_awaited()

    def test_temperature_below_min_skipped(self):
        """Temperature < 5.0 must not be written."""
        device = _make_cc_device(summer_mode=False, operation_mode_value="MANUAL")
        entity = _make_cc(device)
        _run(entity.async_set_temperature(**{ATTR_TEMPERATURE: 4.9}))
        device.async_set_setpoint_temperature.assert_not_awaited()

    def test_temperature_above_max_skipped(self):
        """Temperature > 30.0 must not be written."""
        device = _make_cc_device(summer_mode=False, operation_mode_value="MANUAL")
        entity = _make_cc(device)
        _run(entity.async_set_temperature(**{ATTR_TEMPERATURE: 30.1}))
        device.async_set_setpoint_temperature.assert_not_awaited()

    def test_temperature_boundary_min_written(self):
        """Temperature exactly 5.0 is written."""
        device = _make_cc_device(summer_mode=False, operation_mode_value="MANUAL")
        entity = _make_cc(device)
        _run(entity.async_set_temperature(**{ATTR_TEMPERATURE: 5.0}))
        device.async_set_setpoint_temperature.assert_awaited_with(5.0)

    def test_temperature_boundary_max_written(self):
        """Temperature exactly 30.0 is written."""
        device = _make_cc_device(summer_mode=False, operation_mode_value="MANUAL")
        entity = _make_cc(device)
        _run(entity.async_set_temperature(**{ATTR_TEMPERATURE: 30.0}))
        device.async_set_setpoint_temperature.assert_awaited_with(30.0)

    def test_shcexception_from_setpoint_is_swallowed(self):
        """SHCException from async setter must not propagate."""
        device = _make_cc_device(summer_mode=False, operation_mode_value="MANUAL")
        device.async_set_setpoint_temperature = AsyncMock(side_effect=SHCException("err"))
        entity = _make_cc(device)
        # Must not raise
        _run(entity.async_set_temperature(**{ATTR_TEMPERATURE: 21.0}))

    def test_jsonrpcerror_from_setpoint_is_swallowed(self):
        """JSONRPCError from async setter must not propagate."""
        device = _make_cc_device(summer_mode=False, operation_mode_value="MANUAL")
        device.async_set_setpoint_temperature = AsyncMock(
            side_effect=JSONRPCError(-32001, "err")
        )
        entity = _make_cc(device)
        _run(entity.async_set_temperature(**{ATTR_TEMPERATURE: 21.0}))


# ===========================================================================
# ClimateControl — async_set_hvac_mode: AUTO/HEAT/OFF
# ===========================================================================

class TestSetHvacModeNoCooling:
    """#334: async_set_hvac_mode with AUTO/HEAT/OFF design.

    AUTO → operationMode=AUTOMATIC + summer_mode=False.
    HEAT → operationMode=MANUAL + summer_mode=False.
    OFF  → summer_mode=True.
    """

    def test_auto_mode_sets_automatic_operation_mode(self):
        """#334: AUTO sets operationMode=AUTOMATIC + summer_mode=False."""
        device = _make_cc_device(summer_mode=False, supports_cooling=False,
                                 operation_mode_value="MANUAL")
        entity = _make_cc(device)
        _run(entity.async_set_hvac_mode(HVACMode.AUTO))
        device.async_set_summer_mode.assert_awaited_with(False)
        device.async_set_operation_mode.assert_awaited_with(OM_CC.AUTOMATIC)

    def test_heat_mode_no_cooling_sets_manual_operation_mode(self):
        """#334: HEAT sets operationMode=MANUAL + summer_mode=False."""
        device = _make_cc_device(summer_mode=False, supports_cooling=False,
                                 operation_mode_value="AUTOMATIC")
        entity = _make_cc(device)
        _run(entity.async_set_hvac_mode(HVACMode.HEAT))
        device.async_set_summer_mode.assert_awaited_with(False)
        device.async_set_operation_mode.assert_awaited_with(OM_CC.MANUAL)

    def test_off_mode_no_cooling_sets_summer_mode(self):
        device = _make_cc_device(summer_mode=False, supports_cooling=False,
                                 operation_mode_value="AUTOMATIC")
        entity = _make_cc(device)
        _run(entity.async_set_hvac_mode(HVACMode.OFF))
        device.async_set_summer_mode.assert_awaited_with(True)
        device.async_set_cooling_mode.assert_not_awaited()

    def test_off_mode_with_cooling_clears_cooling_first(self):
        """OFF mode + supports_cooling=True → cooling_mode=False first, then summer."""
        device = _make_cc_device(summer_mode=False, supports_cooling=True,
                                 cooling_mode=True, operation_mode_value="AUTOMATIC")
        entity = _make_cc(device)
        _run(entity.async_set_hvac_mode(HVACMode.OFF))
        device.async_set_cooling_mode.assert_awaited_with(False)
        device.async_set_summer_mode.assert_awaited_with(True)

    def test_cool_mode_not_available_without_support(self):
        """COOL not in hvac_modes when supports_cooling=False → noop."""
        device = _make_cc_device(summer_mode=False, supports_cooling=False)
        entity = _make_cc(device)
        _run(entity.async_set_hvac_mode(HVACMode.COOL))
        device.async_set_cooling_mode.assert_not_awaited()
        device.async_set_summer_mode.assert_not_awaited()

    def test_invalid_mode_is_noop(self):
        """Unsupported string mode → early return, no writes."""
        device = _make_cc_device()
        entity = _make_cc(device)
        _run(entity.async_set_hvac_mode("INVALID_MODE"))
        device.async_set_summer_mode.assert_not_awaited()

    def test_shcexception_in_hvac_mode_swallowed(self):
        """SHCException from async setter in async_set_hvac_mode must not propagate."""
        device = _make_cc_device(summer_mode=False, supports_cooling=False, low=False)
        device.async_set_summer_mode = AsyncMock(side_effect=SHCException("conn error"))
        entity = _make_cc(device)
        _run(entity.async_set_hvac_mode(HVACMode.HEAT))

    def test_jsonrpcerror_in_hvac_mode_swallowed(self):
        """JSONRPCError in async_set_hvac_mode must not propagate."""
        device = _make_cc_device(summer_mode=False, supports_cooling=False, low=False)
        device.async_set_summer_mode = AsyncMock(
            side_effect=JSONRPCError(-32001, "timeout")
        )
        entity = _make_cc(device)
        _run(entity.async_set_hvac_mode(HVACMode.HEAT))

    def test_eco_exits_then_hvac_mode_written(self):
        """P2-B/#196: preset_mode=ECO → async_set_hvac_mode exits ECO first (low=False),
        then writes the requested HVAC direction.

        #334: Use HEAT (sets MANUAL) since it writes operationMode.
        """
        device = _make_cc_device(low=True, operation_mode_value="AUTOMATIC")
        entity = _make_cc(device)
        _run(entity.async_set_hvac_mode(HVACMode.HEAT))
        # ECO exit: low must be cleared
        device.async_set_low.assert_awaited_with(False)
        # HEAT direction: summer_mode=False + operationMode=MANUAL
        device.async_set_summer_mode.assert_awaited_with(False)
        device.async_set_operation_mode.assert_awaited_with(OM_CC.MANUAL)


# ===========================================================================
# ClimateControl — async_set_preset_mode: SHCException paths
# ===========================================================================

class TestSetPresetModeExceptions:
    """SHCException must be swallowed in async_set_preset_mode."""

    def test_shcexception_preset_boost_swallowed(self):
        device = _make_cc_device(boost_mode=False, low=False, supports_boost_mode=True)
        device.async_set_boost_mode = AsyncMock(side_effect=SHCException("err"))
        entity = _make_cc(device)
        _run(entity.async_set_preset_mode(PRESET_BOOST))

    def test_shcexception_preset_eco_swallowed(self):
        device = _make_cc_device(boost_mode=False, low=False, supports_boost_mode=True)
        device.async_set_low = AsyncMock(side_effect=SHCException("err"))
        entity = _make_cc(device)
        _run(entity.async_set_preset_mode(PRESET_ECO))

    def test_jsonrpc_preset_boost_swallowed(self):
        device = _make_cc_device(boost_mode=False, low=False, supports_boost_mode=True)
        device.async_set_boost_mode = AsyncMock(
            side_effect=JSONRPCError(-32001, "err")
        )
        entity = _make_cc(device)
        _run(entity.async_set_preset_mode(PRESET_BOOST))

    def test_preset_mode_not_in_presets_returns_early(self):
        """Preset not in preset_modes → no async call, no error."""
        device = _make_cc_device(supports_boost_mode=False)
        device.async_set_boost_mode = AsyncMock(side_effect=SHCException("should not reach"))
        entity = _make_cc(device)
        # BOOST not in preset_modes when supports_boost_mode=False → early return
        _run(entity.async_set_preset_mode(PRESET_BOOST))
        device.async_set_boost_mode.assert_not_awaited()


# ===========================================================================
# ClimateControl — supported_features (#334 dynamic PRESET_MODE)
# ===========================================================================

class TestSupportedFeatures:
    def test_preset_mode_feature_present_when_presets_available(self):
        """Device with boost/eco → PRESET_MODE feature advertised."""
        entity = _make_cc(_make_cc_device(supports_boost_mode=True))
        feats = entity.supported_features
        assert feats & ClimateEntityFeature.TARGET_TEMPERATURE
        assert feats & ClimateEntityFeature.PRESET_MODE
        assert feats & ClimateEntityFeature.TURN_OFF
        assert feats & ClimateEntityFeature.TURN_ON

    def test_preset_mode_feature_absent_when_no_presets(self):
        """Device without boost or eco → PRESET_MODE feature NOT advertised."""
        device = SimpleNamespace(
            boost_mode=False,
            summer_mode=False,
            supports_boost_mode=False,
            supports_cooling=False,
            cooling_mode=False,
            setpoint_temperature=20.0,
            temperature=19.0,
            operation_mode=OM_CC("AUTOMATIC"),
            root_device_id="r",
            id="d",
            # NOTE: no `low` attribute
        )
        entity = _make_cc(device)
        feats = entity.supported_features
        assert feats & ClimateEntityFeature.TARGET_TEMPERATURE
        assert not (feats & ClimateEntityFeature.PRESET_MODE)
        assert feats & ClimateEntityFeature.TURN_OFF
        assert feats & ClimateEntityFeature.TURN_ON


# ===========================================================================
# ClimateControl — async_turn_on / async_turn_off
# ===========================================================================

class TestTurnOnOffClimate:
    """Lines 315-323: async_turn_on and async_turn_off."""

    def test_turn_on_when_off_calls_auto(self):
        """#334: summer_mode=True (OFF) → turn_on must set AUTO (operationMode=AUTOMATIC)."""
        device = _make_cc_device(summer_mode=True, low=False)
        entity = _make_cc(device)
        _run(entity.async_turn_on())
        device.async_set_summer_mode.assert_awaited_with(False)
        device.async_set_operation_mode.assert_awaited_with(OM_CC.AUTOMATIC)

    def test_turn_on_when_already_auto_is_noop(self):
        """Mode=AUTO (not OFF) → turn_on is a noop."""
        device = _make_cc_device(summer_mode=False, operation_mode_value="AUTOMATIC")
        entity = _make_cc(device)
        _run(entity.async_turn_on())
        device.async_set_summer_mode.assert_not_awaited()

    def test_turn_on_when_already_heat_is_noop(self):
        """Mode=HEAT (not OFF) → turn_on is a noop."""
        device = _make_cc_device(summer_mode=False, operation_mode_value="MANUAL")
        entity = _make_cc(device)
        _run(entity.async_turn_on())
        device.async_set_summer_mode.assert_not_awaited()

    def test_turn_off_when_on_sets_summer_mode(self):
        """Mode=AUTO (not OFF) → turn_off sets summer_mode=True."""
        device = _make_cc_device(summer_mode=False, operation_mode_value="AUTOMATIC")
        entity = _make_cc(device)
        _run(entity.async_turn_off())
        device.async_set_summer_mode.assert_awaited_with(True)

    def test_turn_off_when_already_off_is_noop(self):
        """summer_mode=True (already OFF) → turn_off is noop."""
        device = _make_cc_device(summer_mode=True)
        entity = _make_cc(device)
        _run(entity.async_turn_off())
        device.async_set_summer_mode.assert_not_awaited()


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
        entity._device.async_set_setpoint_temperature = AsyncMock(side_effect=exc)
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
        entity._device.async_set_setpoint_temperature.assert_not_awaited()

    def test_out_of_range_low_noop(self):
        entity = self._hc_raises(SHCException("should not reach"))
        _run(entity.async_set_temperature(**{ATTR_TEMPERATURE: 4.9}))
        entity._device.async_set_setpoint_temperature.assert_not_awaited()

    def test_out_of_range_high_noop(self):
        entity = self._hc_raises(SHCException("should not reach"))
        _run(entity.async_set_temperature(**{ATTR_TEMPERATURE: 30.1}))
        entity._device.async_set_setpoint_temperature.assert_not_awaited()

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
        _run(entity.async_set_hvac_mode(HVACMode.AUTO))
        entity._device.async_set_operation_mode.assert_awaited_with(OM_HC.AUTOMATIC)

    def test_set_heat_writes_manual(self):
        entity = _make_hc(operation_mode=OM_HC.AUTOMATIC)
        _run(entity.async_set_hvac_mode(HVACMode.HEAT))
        entity._device.async_set_operation_mode.assert_awaited_with(OM_HC.MANUAL)

    def test_invalid_mode_is_noop(self):
        entity = _make_hc()
        _run(entity.async_set_hvac_mode(HVACMode.OFF))
        entity._device.async_set_operation_mode.assert_not_awaited()

    def test_cool_mode_is_noop(self):
        entity = _make_hc()
        _run(entity.async_set_hvac_mode(HVACMode.COOL))
        entity._device.async_set_operation_mode.assert_not_awaited()
