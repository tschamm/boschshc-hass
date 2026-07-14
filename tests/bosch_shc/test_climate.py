"""Tests for the climate platform (ClimateControl / HeatingCircuit).

Covers: async_setup_entry (device-exclusion, room-name handling), ClimateControl
and HeatingCircuit __init__ and simple property getters, hvac_mode/hvac_modes/
preset_mode/preset_modes across all branches, async_set_temperature (bounds,
rounding, OFF/ECO guards, ATTR_HVAC_MODE kwarg handling), async_set_hvac_mode
and async_set_preset_mode (including the #196/#334 "exit ECO first" and
direction-vs-override axis design), hvac_action, turn_on/turn_off, and
JSONRPCError/SHCException swallowing (with LOGGER.warning) across all of the
above async setters.

Historical bugfixes exercised here:
  #273 — BOOST mode must not surface an unhandled exception when setting
         temperature (early-return guard + JSONRPCError/SHCException safety net).
  #242 — ECO / low preset: TRV_GEN2 is controlled via ROOM_CLIMATE_CONTROL.
  #253 — TRV_I has no climate entity by design (not in boschshcpy MODEL_MAPPING).
  #334 — AUTOMATIC is HVACMode.AUTO (not a preset); presets are override-only
         (boost/eco). AUTO added to hvac_modes, removed from preset_modes.
  #196 — turn_off/async_set_hvac_mode must work from ECO (exit ECO first,
         instead of silently no-oping).
  hass#120 — HeatingCircuit min/max temp read the device-reported range instead
         of always falling back to the 5/30 constant.

Pattern: pure-unit tests, no HA test harness. Entities are built via
``Cls.__new__(Cls)`` (bypassing ``SHCEntity.__init__``) plus a
``SimpleNamespace`` fake device, or via the real constructor where __init__
itself is under test. Async methods are driven with a small ``_run()`` helper
(a fresh event loop per call) instead of pytest-asyncio.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from boschshcpy import HeatingCircuitService, RoomClimateControlService
from boschshcpy.exceptions import JSONRPCError, SHCException
from homeassistant.components.climate.const import (
    ATTR_HVAC_MODE,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature

from custom_components.bosch_shc.climate import (
    PRESET_BOOST,
    PRESET_ECO,
    ClimateControl,
    HeatingCircuit,
    async_setup_entry,
)
from custom_components.bosch_shc.const import OPT_EXCLUDED_DEVICES

from .conftest import run_setup_entry

# ===========================================================================
# Shared enum shorthands
# ===========================================================================

OM_CC = RoomClimateControlService.OperationMode
OM_HC = HeatingCircuitService.OperationMode
_AUTO = OM_CC.AUTOMATIC
_MANUAL = OM_CC.MANUAL


# JSONRPCError requires (code, message) — use a subclass for brevity.
class _JRPC(JSONRPCError):
    def __init__(self, msg="err"):
        super().__init__(-32001, msg)


# ===========================================================================
# Async execution helper
# ===========================================================================

def _run(coro):
    """Run a coroutine synchronously (no pytest-asyncio needed)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


_run_async = _run  # alias used by the #273/#242 regression tests below


# ===========================================================================
# ClimateControl — device/entity builders
# ===========================================================================

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
    op = OM_CC(operation_mode_value)
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


def _make_device_cooling(
    *,
    supports_cooling=True,
    cooling_mode=False,
    summer_mode=False,
    operation_mode_value="AUTOMATIC",
):
    op = OM_CC(operation_mode_value)
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


def _make_cc_device_full(
    *,
    name="Living Room Climate",
    manufacturer="Bosch",
    device_model="RCC",
    root_device_id="root-cc-1",
    id_="dev-cc-1",
    room_id="room-1",
    status="AVAILABLE",
    deleted=False,
    temperature=21.0,
    setpoint_temperature=20.0,
    boost_mode=False,
    supports_boost_mode=True,
    low=False,
    summer_mode=False,
    operation_mode_value="AUTOMATIC",
    supports_cooling=False,
    cooling_mode=False,
):
    """Full fake SHCClimateControl device satisfying both SHCEntity.__init__ and
    ClimateControl.__init__ attribute access (real constructor, not __new__).
    """
    op = OM_CC(operation_mode_value)
    return SimpleNamespace(
        name=name,
        manufacturer=manufacturer,
        device_model=device_model,
        root_device_id=root_device_id,
        id=id_,
        room_id=room_id,
        status=status,
        deleted=deleted,
        device_services=[],
        temperature=temperature,
        setpoint_temperature=setpoint_temperature,
        boost_mode=boost_mode,
        supports_boost_mode=supports_boost_mode,
        low=low,
        summer_mode=summer_mode,
        operation_mode=op,
        supports_cooling=supports_cooling,
        cooling_mode=cooling_mode,
    )


# ===========================================================================
# HeatingCircuit — device/entity builders
# ===========================================================================

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


def _make_hc_device(
    *, operation_mode=None, on=False, setpoint=20.0, root_device_id="r", id_="d"
):
    return SimpleNamespace(
        operation_mode=operation_mode or OM_HC.AUTOMATIC,
        on=on,
        setpoint_temperature=setpoint,
        root_device_id=root_device_id,
        id=id_,
        async_set_operation_mode=AsyncMock(),
        async_set_setpoint_temperature=AsyncMock(),
    )


def _wrap_hc(device):
    """Wrap an already-built fake device into a HeatingCircuit (no __init__)."""
    entity = HeatingCircuit.__new__(HeatingCircuit)
    entity._device = device
    entity._attr_unique_id = "r_d"
    return entity


def _make_hc_device_full(
    *,
    name="Heating Circuit 1",
    manufacturer="Bosch",
    device_model="HC",
    root_device_id="root-hc-1",
    id_="dev-hc-1",
    status="AVAILABLE",
    deleted=False,
    setpoint_temperature=20.0,
    on=False,
):
    """Full fake SHCHeatingCircuit device satisfying SHCEntity.__init__ and
    HeatingCircuit.__init__ attribute access (real constructor, not __new__).
    """
    return SimpleNamespace(
        name=name,
        manufacturer=manufacturer,
        device_model=device_model,
        root_device_id=root_device_id,
        id=id_,
        status=status,
        deleted=deleted,
        device_services=[],
        setpoint_temperature=setpoint_temperature,
        on=on,
        operation_mode=HeatingCircuitService.OperationMode.AUTOMATIC,
    )


def _hc(operation_mode, on, setpoint=21.0):
    """Bypass SHCEntity.__init__ (needs hass/registry); exercise the properties."""
    entity = HeatingCircuit.__new__(HeatingCircuit)
    entity._device = SimpleNamespace(
        operation_mode=operation_mode, on=on, setpoint_temperature=setpoint
    )
    return entity


# ===========================================================================
# async_setup_entry / session builders
# ===========================================================================

def _make_climate_device(device_id="clim-1", room_id="room-1"):
    """Device double for a climate control."""
    return SimpleNamespace(
        id=device_id,
        root_device_id="shc-root",
        room_id=room_id,
        name="Room Climate",
        serial=f"serial-{device_id}",
        manufacturer="Bosch",
        device_model="ClimateModel",
        status="AVAILABLE",
        deleted=False,
        device_services=[],
        subscribe_callback=lambda eid, cb: None,
        unsubscribe_callback=lambda eid: None,
        temperature=20.0,
        setpoint_temperature=21.0,
        summer_mode=False,
        supports_cooling=False,
        cooling_mode=False,
        operation_mode=_AUTO,
        supports_boost_mode=False,
        boost_mode=False,
        low=False,
        has_demand=False,
    )


def _make_heating_circuit_device(device_id="hc-1", room_id="room-2"):
    """Device double for a heating circuit."""
    return SimpleNamespace(
        id=device_id,
        root_device_id="shc-root",
        room_id=room_id,
        name="Heating Circuit",
        serial=f"serial-{device_id}",
        manufacturer="Bosch",
        device_model="HCModel",
        status="AVAILABLE",
        deleted=False,
        device_services=[],
        subscribe_callback=lambda eid, cb: None,
        unsubscribe_callback=lambda eid: None,
    )


def _make_room(name="Living Room"):
    return SimpleNamespace(name=name)


# ===========================================================================
# Error-handling entity builders (JSONRPCError / SHCException swallowing)
# ===========================================================================

def _make_climate_control(*, summer_mode=False, low=False, boost_mode=False, supports_eco=True):
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


def _make_hc_entity():
    """HeatingCircuit bypassing SHCEntity.__init__, with a real operation_mode."""
    entity = HeatingCircuit.__new__(HeatingCircuit)
    entity._device = SimpleNamespace(
        name="HC1",
        id="hc-1",
        root_device_id="root-1",
        setpoint_temperature=20.0,
        operation_mode=HeatingCircuitService.OperationMode.AUTOMATIC,
        on=False,
        async_set_setpoint_temperature=AsyncMock(),
        async_set_operation_mode=AsyncMock(),
    )
    entity._attr_unique_id = "root-1_hc-1"
    entity._attr_min_temp = 5.0
    entity._attr_max_temp = 30.0
    return entity


# ===========================================================================
# #273 — BOOST mode: early-return guard (no write at all)
# ===========================================================================

class TestAsyncSetupEntry:
    """Drive async_setup_entry with various device combinations."""

    def _setup(
        self,
        mock_config_entry,
        mock_session,
        climate_controls,
        heating_circuits,
        rooms,
        entry_id="entry-1",
    ) -> list:
        mock_session.device_helper.climate_controls = climate_controls
        mock_session.device_helper.heating_circuits = heating_circuits
        mock_session.room = lambda room_id: rooms[room_id]
        mock_config_entry.entry_id = entry_id
        return asyncio.run(
            run_setup_entry(async_setup_entry, mock_config_entry, mock_session)
        )

    def test_no_devices_adds_nothing(self, mock_config_entry, mock_session):
        """entities list empty → async_add_entities never called."""
        added = self._setup(mock_config_entry, mock_session, [], [], {})
        assert added == []

    def test_one_climate_control_added(self, mock_config_entry, mock_session):
        """One climate → one ClimateControl entity appended."""
        dev = _make_cc_device_full(room_id="r1")
        rooms = {"r1": _make_room("Kitchen")}
        added = self._setup(mock_config_entry, mock_session, [dev], [], rooms)
        assert len(added) == 1
        assert isinstance(added[0], ClimateControl)

    def test_climate_control_name_uses_room_name(self, mock_config_entry, mock_session):
        """Device name = room name; the entity name comes from the
        translation_key 'room_climate_control' (#333), so _attr_name is None
        and the friendly name resolves to '<room> <translated>' — no doubling.
        """
        dev = _make_cc_device_full(room_id="r2")
        rooms = {"r2": _make_room("Bedroom")}
        added = self._setup(mock_config_entry, mock_session, [dev], [], rooms)
        entity = added[0]
        assert entity.device_name == "Bedroom"
        assert entity._attr_name is None
        assert entity._attr_translation_key == "room_climate_control"

    def test_one_heating_circuit_added(self, mock_config_entry, mock_session):
        """One heating circuit → one HeatingCircuit entity."""
        dev = _make_hc_device_full()
        added = self._setup(mock_config_entry, mock_session, [], [dev], {})
        assert len(added) == 1
        assert isinstance(added[0], HeatingCircuit)

    def test_heating_circuit_name_from_device(self, mock_config_entry, mock_session):
        """HeatingCircuit name = heating_circuit.name."""
        dev = _make_hc_device_full(name="HC South Wing")
        added = self._setup(mock_config_entry, mock_session, [], [dev], {})
        entity = added[0]
        assert entity.name == "HC South Wing"

    def test_both_climate_and_heating_circuit(self, mock_config_entry, mock_session):
        """Mix of devices → both entity types in one list."""
        cc_dev = _make_cc_device_full(room_id="r3")
        hc_dev = _make_hc_device_full()
        rooms = {"r3": _make_room("Office")}
        added = self._setup(
            mock_config_entry, mock_session, [cc_dev], [hc_dev], rooms
        )
        assert len(added) == 2
        types = {type(e) for e in added}
        assert ClimateControl in types
        assert HeatingCircuit in types

    def test_multiple_climate_controls(self, mock_config_entry, mock_session):
        """Two climates → two ClimateControl entities."""
        dev1 = _make_cc_device_full(root_device_id="r1", id_="d1", room_id="room-a")
        dev2 = _make_cc_device_full(root_device_id="r2", id_="d2", room_id="room-b")
        rooms = {
            "room-a": _make_room("Room A"),
            "room-b": _make_room("Room B"),
        }
        added = self._setup(mock_config_entry, mock_session, [dev1, dev2], [], rooms)
        assert len(added) == 2

    def test_entry_id_passed_to_entity(self, mock_config_entry, mock_session):
        """ClimateControl receives the correct entry_id."""
        dev = _make_cc_device_full(room_id="rx")
        rooms = {"rx": _make_room("X")}
        added = self._setup(
            mock_config_entry,
            mock_session,
            [dev],
            [],
            rooms,
            entry_id="my-entry-99",
        )
        entity = added[0]
        assert entity._entry_id == "my-entry-99"


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


# ===========================================================================
# #273 — JSONRPCError swallowed (safety net catch)
# ===========================================================================

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


# ===========================================================================
# #242 — ECO / low preset: verified by design
# ===========================================================================

class TestEcoPreset:
    """preset_mode == PRESET_ECO when device.low is True.

    P2-B (#196): the old guard that silently skipped setpoint writes in ECO
    mode has been removed. only skip the write when truly OFF.

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


# ===========================================================================
# Normal HEAT mode: temperature written and rounded
# ===========================================================================

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


# ===========================================================================
# PR #304 — COOL HVACMode on ClimateControl (supports_cooling)
# ===========================================================================

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


# ===========================================================================
# #334 — HVACMode.AUTO back as hvac_mode; preset axis is override-only
# ===========================================================================

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
        op = OM_CC("AUTOMATIC")
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
        op = OM_CC("AUTOMATIC")
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
        device = _make_device(summer_mode=False, supports_cooling=False,
                              operation_mode_value="MANUAL")
        entity = _make_entity(device)
        _run_async(entity.async_set_hvac_mode(HVACMode.AUTO))
        device.async_set_operation_mode.assert_awaited_with(OM_CC.AUTOMATIC)
        device.async_set_summer_mode.assert_awaited_with(False)

    def test_set_hvac_mode_heat_sets_manual(self):
        """#334: HEAT sets operationMode=MANUAL."""
        device = _make_device(summer_mode=False, supports_cooling=False,
                              operation_mode_value="AUTOMATIC")
        entity = _make_entity(device)
        _run_async(entity.async_set_hvac_mode(HVACMode.HEAT))
        device.async_set_operation_mode.assert_awaited_with(OM_CC.MANUAL)
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
        device = _make_device(summer_mode=False, cooling_mode=True, supports_cooling=True)
        entity = _make_entity(device)
        assert entity.hvac_action == HVACAction.COOLING

    def test_hvac_action_off_when_summer_mode(self):
        device = _make_device(summer_mode=True)
        entity = _make_entity(device)
        assert entity.hvac_action == HVACAction.OFF


class TestTurnOnOff334:
    """#334: turn_on defaults to AUTO (schedule); turn_off still uses OFF/summer_mode."""

    def test_turn_on_from_off_sets_auto(self):
        """#334: turn_on → async_set_hvac_mode(AUTO) → sets operationMode=AUTOMATIC."""
        device = _make_device(summer_mode=True, supports_cooling=False)
        entity = _make_entity(device)
        _run_async(entity.async_turn_on())
        # AUTO sets summer_mode=False and operationMode=AUTOMATIC
        device.async_set_summer_mode.assert_awaited_with(False)
        device.async_set_operation_mode.assert_awaited_with(OM_CC.AUTOMATIC)

    def test_turn_off_sets_summer_mode(self):
        device = _make_device(summer_mode=False, operation_mode_value="AUTOMATIC")
        entity = _make_entity(device)
        _run_async(entity.async_turn_off())
        device.async_set_summer_mode.assert_awaited_with(True)


# ===========================================================================
# CLIMATE.PY — line 338: supports_cooling=True → async_set_cooling_mode(False)
# called on AUTO (from test_coverage_gaps.py)
# ===========================================================================


class TestClimateSupportsCoolingAutoMode:
    """Line 338: supports_cooling=True → async_set_cooling_mode(False) called on AUTO."""

    def test_set_hvac_mode_auto_with_cooling(self):
        """Line 338: AUTO mode + supports_cooling → async_set_cooling_mode called."""
        ent = ClimateControl.__new__(ClimateControl)

        device = MagicMock()
        device.supports_cooling = True
        device.supports_boost_mode = False
        device.boost_mode = False
        # supports_eco must be False to prevent preset_mode returning PRESET_ECO
        device.supports_eco = False
        device.async_set_summer_mode = AsyncMock()
        device.async_set_cooling_mode = AsyncMock()
        device.async_set_operation_mode = AsyncMock()
        ent._device = device

        _run(ent.async_set_hvac_mode(HVACMode.AUTO))

        device.async_set_cooling_mode.assert_called_once_with(False)
        device.async_set_summer_mode.assert_called()


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
    Returns None when no presets available.
    """

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
# ClimateControl — min_temp / max_temp
# ===========================================================================

class TestTempBounds:
    def test_min_temp(self):
        entity = _make_cc(_make_cc_device())
        assert entity.min_temp == 5.0

    def test_max_temp(self):
        entity = _make_cc(_make_cc_device())
        assert entity.max_temp == 30.0


# ===========================================================================
# ClimateControl — device_name
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
    """Guard paths in async_set_temperature."""

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
# ClimateControl — async_set_temperature: AUTOMATIC → MANUAL switch (extra coverage)
# ===========================================================================

class TestClimateSetTemperatureManualSwitch:
    """async_set_temperature must switch AUTOMATIC → MANUAL first when no
    ATTR_HVAC_MODE kwarg is given."""

    def _make_entity(self, operation_mode=_AUTO):
        """Build a ClimateControl bypassing __init__ via __new__."""
        ent = ClimateControl.__new__(ClimateControl)
        ent._device = SimpleNamespace(
            id="clim-ent",
            root_device_id="shc-root",
            name="Climate",
            manufacturer="Bosch",
            device_model="CC",
            status="AVAILABLE",
            deleted=False,
            device_services=[],
            subscribe_callback=lambda eid, cb: None,
            unsubscribe_callback=lambda eid: None,
            temperature=20.0,
            setpoint_temperature=21.0,
            summer_mode=False,
            supports_cooling=False,
            cooling_mode=False,
            operation_mode=operation_mode,
            supports_boost_mode=False,
            boost_mode=False,
            low=False,
            has_demand=False,
            async_set_low=AsyncMock(),
            async_set_summer_mode=AsyncMock(),
            async_set_cooling_mode=AsyncMock(),
            async_set_boost_mode=AsyncMock(),
            async_set_operation_mode=AsyncMock(),
            async_set_setpoint_temperature=AsyncMock(),
        )
        ent._entry_id = "entry-test"
        ent._attr_name = "Room Climate Test"
        ent._room_label = "Room Climate Test"
        ent._attr_unique_id = "shc-root_clim-ent"
        ent._attr_target_temperature_step = 0.5
        ent._enable_turn_on_off_backwards_compatibility = False
        return ent

    def test_set_temperature_auto_mode_switches_to_manual_first(self):
        """When operation_mode==AUTOMATIC and no ATTR_HVAC_MODE in kwargs,
        async_set_temperature must call async_set_operation_mode(MANUAL)
        BEFORE the setpoint write.
        """
        ent = self._make_entity(operation_mode=_AUTO)

        # Track call order
        call_order = []

        async def _set_op_mode(val):
            call_order.append(("operation_mode", val))

        async def _set_setpoint(val):
            call_order.append(("setpoint_temperature", val))

        ent._device.async_set_operation_mode = _set_op_mode
        ent._device.async_set_setpoint_temperature = _set_setpoint

        asyncio.run(ent.async_set_temperature(**{ATTR_TEMPERATURE: 22.0}))

        assert ("operation_mode", _MANUAL) in call_order, (
            f"Expected async_set_operation_mode(MANUAL) to be called, got {call_order}"
        )
        keys = [k for k, _ in call_order]
        assert "operation_mode" in keys
        assert "setpoint_temperature" in keys
        op_idx = keys.index("operation_mode")
        sp_idx = keys.index("setpoint_temperature")
        assert op_idx < sp_idx, (
            "async_set_operation_mode(MANUAL) must be called before async_set_setpoint_temperature"
        )

    def test_set_temperature_auto_mode_then_writes_setpoint(self):
        """After switching to MANUAL, the setpoint must be written."""
        ent = self._make_entity(operation_mode=_AUTO)

        asyncio.run(ent.async_set_temperature(**{ATTR_TEMPERATURE: 22.0}))

        ent._device.async_set_setpoint_temperature.assert_awaited_with(22.0)

    def test_set_temperature_with_explicit_hvac_mode_skips_manual_switch(self):
        """When ATTR_HVAC_MODE is explicitly provided, the MANUAL switch must NOT happen.
        This ensures the (kwargs.get(ATTR_HVAC_MODE) is None) guard works.
        """
        ent = self._make_entity(operation_mode=_AUTO)

        asyncio.run(ent.async_set_temperature(
            **{ATTR_TEMPERATURE: 22.0, ATTR_HVAC_MODE: HVACMode.AUTO}
        ))

        # async_set_operation_mode must NOT have been called with MANUAL
        for c in ent._device.async_set_operation_mode.await_args_list:
            assert c != call(_MANUAL), (
                "With explicit ATTR_HVAC_MODE, should NOT switch to MANUAL"
            )

    def test_set_temperature_explicit_auto_still_writes_setpoint(self):
        """Regression hass#369: set_temperature(temperature=X, hvac_mode="auto")
        on a device already in AUTOMATIC must actually write the setpoint,
        not silently drop it. A before/after rawscan from the reporter showed
        the official app writing setpointTemperature directly while
        operationMode stays AUTOMATIC — the schedule resumes on its own via
        nextChange, so there is nothing here that should block the write.
        """
        ent = self._make_entity(operation_mode=_AUTO)

        asyncio.run(ent.async_set_temperature(
            **{ATTR_TEMPERATURE: 20.0, ATTR_HVAC_MODE: HVACMode.AUTO}
        ))

        ent._device.async_set_setpoint_temperature.assert_awaited_with(20.0)
        # operationMode is (re-)written to AUTOMATIC (harmless, already the
        # current mode) but never to MANUAL.
        for c in ent._device.async_set_operation_mode.await_args_list:
            assert c != call(_MANUAL)

    def test_set_temperature_manual_mode_skips_manual_switch(self):
        """When operation_mode is already MANUAL, no extra mode switch call."""
        ent = self._make_entity(operation_mode=_MANUAL)

        asyncio.run(ent.async_set_temperature(**{ATTR_TEMPERATURE: 21.0}))

        # async_set_operation_mode must not have been called at all
        ent._device.async_set_operation_mode.assert_not_awaited()

    def test_set_temperature_explicit_heat_writes_setpoint_despite_stale_off_cache(self):
        """Regression: boschshcpy's async_put_state_element() only awaits the
        HTTP PUT, it never updates the local device cache — so right after
        async_set_hvac_mode(HEAT) awaits async_set_summer_mode(False), the
        device's cached summer_mode is still stale True until the next
        long-poll. set_temperature(hvac_mode="heat", temperature=21) on a
        device that was OFF must still write the setpoint — it must not
        re-read the stale cache and bail out via the OFF guard, silently
        dropping the temperature the caller explicitly asked for."""
        ent = self._make_entity(operation_mode=_MANUAL)
        ent._device.summer_mode = True  # stale "device is off" cache

        asyncio.run(ent.async_set_temperature(
            **{ATTR_TEMPERATURE: 21.0, ATTR_HVAC_MODE: HVACMode.HEAT}
        ))

        ent._device.async_set_summer_mode.assert_awaited_with(False)
        ent._device.async_set_setpoint_temperature.assert_awaited_with(21.0)

    def test_set_temperature_explicit_heat_write_failure_falls_back_to_stale_cache_off_guard(self):
        """Regression: if the HEAT mode write itself fails (JSONRPCError/
        SHCException, caught+logged inside _async_apply_hvac_mode), the
        device is still actually OFF — set_temperature must NOT trust the
        (never-applied) requested mode and must fall back to the real
        cached state, so the OFF guard correctly skips the setpoint write
        instead of attempting (and failing) it a second time."""
        ent = self._make_entity(operation_mode=_MANUAL)
        ent._device.summer_mode = True  # device is genuinely OFF
        ent._device.async_set_summer_mode = AsyncMock(
            side_effect=JSONRPCError(-1, "network error")
        )

        asyncio.run(ent.async_set_temperature(
            **{ATTR_TEMPERATURE: 21.0, ATTR_HVAC_MODE: HVACMode.HEAT}
        ))

        # The failed mode write must not be trusted — setpoint must NOT be
        # written since the device is still actually off.
        ent._device.async_set_setpoint_temperature.assert_not_awaited()


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
# ClimateControl — async_set_hvac_mode ECO guard / exit-ECO-first (#196/#334)
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

        device.async_set_summer_mode.assert_not_awaited()

    def test_turn_on_noop_when_already_on_heat(self):
        device = _make_cc_device(summer_mode=False, operation_mode_value="MANUAL")
        entity = _make_cc(device)

        _run(entity.async_turn_on())

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

        device.async_set_summer_mode.assert_not_awaited()


# ===========================================================================
# ClimateControl — hvac_action
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
        """Getattr guard: device without has_demand attribute → IDLE."""
        device = _make_cc_device(summer_mode=False, operation_mode_value="AUTOMATIC")
        del device.has_demand  # simulate older lib without attribute
        entity = _make_cc(device)
        assert entity.hvac_action == HVACAction.IDLE


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
# ClimateControl — error handling (JSONRPCError / SHCException swallowed
# in async_set_hvac_mode / async_set_preset_mode, with LOGGER.warning)
# ===========================================================================


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


# ===========================================================================
# async_setup_entry — device exclusion, room-name handling
# ===========================================================================

class TestClimateSetupExcluded:
    def _run(self, mock_config_entry, mock_session) -> list:
        return asyncio.run(
            run_setup_entry(async_setup_entry, mock_config_entry, mock_session)
        )

    def test_excluded_climate_control_not_added(self, mock_config_entry, mock_session):
        """Excluded climate device must not appear in entities."""
        dev = _make_climate_device(device_id="excl-clim")
        mock_session.device_helper.climate_controls = [dev]
        mock_session.room = lambda room_id: SimpleNamespace(name=f"Room-{room_id}")
        mock_config_entry.options = {OPT_EXCLUDED_DEVICES: ["excl-clim"]}
        added = self._run(mock_config_entry, mock_session)
        assert all(
            getattr(e, "_device", None) is not dev for e in added
        ), "Excluded climate device should not be added"

    def test_non_excluded_climate_control_is_added(
        self, mock_config_entry, mock_session
    ):
        """Non-excluded climate device must appear in entities."""
        dev = _make_climate_device(device_id="keep-clim")
        mock_session.device_helper.climate_controls = [dev]
        mock_session.room = lambda room_id: SimpleNamespace(name=f"Room-{room_id}")
        added = self._run(mock_config_entry, mock_session)
        assert any(
            getattr(e, "_device", None) is dev for e in added
        ), "Non-excluded climate device should be added"

    def test_mixed_climates_only_excluded_is_skipped(
        self, mock_config_entry, mock_session
    ):
        """When one of two climates is excluded, only the non-excluded one is added."""
        keep = _make_climate_device(device_id="clim-keep")
        excl = _make_climate_device(device_id="clim-excl")
        mock_session.device_helper.climate_controls = [keep, excl]
        mock_session.room = lambda room_id: SimpleNamespace(name=f"Room-{room_id}")
        mock_config_entry.options = {OPT_EXCLUDED_DEVICES: ["clim-excl"]}
        added = self._run(mock_config_entry, mock_session)
        device_ids = [getattr(e, "_device", SimpleNamespace()).id for e in added]
        assert "clim-keep" in device_ids
        assert "clim-excl" not in device_ids


# ===========================================================================
# ClimateControl.__init__ / simple property getters (real constructor)
# ===========================================================================

class TestClimateControlInit:
    """Real __init__ (via constructor, not __new__)."""

    def _make_entity(self, **kwargs):
        dev = _make_cc_device_full(**kwargs)
        return ClimateControl(device=dev, name="Test Room Climate", entry_id="e1")

    def test_init_sets_name(self):
        """ClimateControl stores the room label in _room_label and keeps
        _attr_name None (primary entity → friendly name = device name).
        """
        entity = self._make_entity()
        assert entity._room_label == "Test Room Climate"
        assert entity._attr_name is None
        assert entity.device_name == "Test Room Climate"

    def test_init_sets_unique_id(self):
        """self._attr_unique_id = root_device_id + '_' + id."""
        entity = self._make_entity(root_device_id="root-x", id_="dev-y")
        assert entity._attr_unique_id == "root-x_dev-y"

    def test_init_stores_device(self):
        """SHCEntity.__init__ (super()): self._device set."""
        dev = _make_cc_device_full()
        entity = ClimateControl(device=dev, name="N", entry_id="e")
        assert entity._device is dev

    def test_init_stores_entry_id(self):
        """SHCEntity.__init__: self._entry_id set."""
        entity = self._make_entity()
        assert entity._entry_id == "e1"


class TestClimateControlProperties:
    """Simple read-only properties that return fixed values or read _device."""

    def _entity(self, **kwargs):
        dev = _make_cc_device_full(**kwargs)
        entity = ClimateControl.__new__(ClimateControl)
        entity._device = dev
        entity._attr_name = "Prop Test"
        entity._room_label = "Prop Test"  # device_name reads _room_label
        entity._attr_unique_id = f"{dev.root_device_id}_{dev.id}"
        return entity

    # name property
    def test_name_property(self):
        entity = self._entity()
        entity._attr_name = "Custom Name"
        assert entity.name == "Custom Name"

    # temperature_unit
    def test_temperature_unit_is_celsius(self):
        entity = self._entity()
        assert entity.temperature_unit == UnitOfTemperature.CELSIUS

    # current_temperature reads _device.temperature
    def test_current_temperature(self):
        entity = self._entity(temperature=19.5)
        assert entity.current_temperature == 19.5

    def test_current_temperature_different_value(self):
        entity = self._entity(temperature=22.0)
        assert entity.current_temperature == 22.0

    # target_temperature reads _device.setpoint_temperature
    def test_target_temperature(self):
        entity = self._entity(setpoint_temperature=21.0)
        assert entity.target_temperature == 21.0

    def test_target_temperature_different_value(self):
        entity = self._entity(setpoint_temperature=18.5)
        assert entity.target_temperature == 18.5

    # target_temperature_step
    def test_target_temperature_step(self):
        entity = self._entity()
        assert entity.target_temperature_step == 0.5

    # supported_features
    def test_supported_features(self):
        entity = self._entity()
        feats = entity.supported_features
        assert feats & ClimateEntityFeature.TARGET_TEMPERATURE
        assert feats & ClimateEntityFeature.PRESET_MODE
        assert feats & ClimateEntityFeature.TURN_OFF
        assert feats & ClimateEntityFeature.TURN_ON

    def test_supported_features_exact_value(self):
        entity = self._entity()
        expected = (
            ClimateEntityFeature.TARGET_TEMPERATURE
            | ClimateEntityFeature.PRESET_MODE
            | ClimateEntityFeature.TURN_OFF
            | ClimateEntityFeature.TURN_ON
        )
        assert entity.supported_features == expected

    # device_name property (delegates to _name in ClimateControl)
    def test_device_name_matches_name(self):
        entity = self._entity()
        assert entity.device_name == entity.name


class TestHeatingCircuitSetupExcluded:
    def _run(self, mock_config_entry, mock_session) -> list:
        return asyncio.run(
            run_setup_entry(async_setup_entry, mock_config_entry, mock_session)
        )

    def test_excluded_heating_circuit_not_added(self, mock_config_entry, mock_session):
        """Excluded heating circuit must not appear in entities."""
        dev = _make_heating_circuit_device(device_id="excl-hc")
        mock_session.device_helper.heating_circuits = [dev]
        mock_config_entry.options = {OPT_EXCLUDED_DEVICES: ["excl-hc"]}
        added = self._run(mock_config_entry, mock_session)
        assert all(
            getattr(e, "_device", None) is not dev for e in added
        ), "Excluded heating circuit should not be added"

    def test_non_excluded_heating_circuit_is_added(
        self, mock_config_entry, mock_session
    ):
        """Non-excluded heating circuit must appear in entities."""
        dev = _make_heating_circuit_device(device_id="keep-hc")
        mock_session.device_helper.heating_circuits = [dev]
        added = self._run(mock_config_entry, mock_session)
        assert any(
            getattr(e, "_device", None) is dev for e in added
        ), "Non-excluded heating circuit should be added"


# ===========================================================================
# HeatingCircuit — class-level attributes / simple properties
# ===========================================================================

class TestHeatingCircuitClassAttrs:
    """HeatingCircuit class-level _attr_* defaults."""

    def test_temperature_unit_celsius(self):
        entity = _make_hc()
        assert entity._attr_temperature_unit == UnitOfTemperature.CELSIUS

    def test_max_temp_30(self):
        """hass#120: max_temp is now dynamic, falling back to 30.0 when the
        device hasn't reported a setpoint_temperature_range."""
        entity = _make_hc()
        assert entity.max_temp == 30.0

    def test_min_temp_5(self):
        """hass#120: min_temp is now dynamic, falling back to 5.0 when the
        device hasn't reported a setpoint_temperature_range."""
        entity = _make_hc()
        assert entity.min_temp == 5.0

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


class TestHeatingCircuitDynamicBounds:
    """The app reads a per-device setpoint range
    (HeatingCircuitVerticalSliderFragment.setMinMax) rather than a fixed
    constant — a floor-heating circuit commonly reports a raised minimum."""

    def test_falls_back_to_5_30_when_device_has_no_range(self):
        device = _make_hc_device()  # no setpoint_temperature_range attr at all
        entity = _wrap_hc(device)
        assert entity.min_temp == 5.0
        assert entity.max_temp == 30.0

    def test_uses_device_reported_range(self):
        device = _make_hc_device()
        device.setpoint_temperature_range = (10.0, 28.0)
        entity = _wrap_hc(device)
        assert entity.min_temp == 10.0
        assert entity.max_temp == 28.0

    def test_set_temperature_rejects_value_below_device_reported_min(self):
        """async_set_temperature checks min_temp/max_temp before writing —
        a value below the device's real (raised) minimum must not be sent."""
        device = _make_hc_device()
        device.setpoint_temperature_range = (10.0, 28.0)
        entity = _wrap_hc(device)

        _run(entity.async_set_temperature(**{ATTR_TEMPERATURE: 7.0}))

        device.async_set_setpoint_temperature.assert_not_awaited()


class TestHeatingCircuitInit:
    """HeatingCircuit.__init__ sets unique_id correctly."""

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
# HeatingCircuit — async_set_temperature
# ===========================================================================

class TestHeatingCircuitSetTemperature:
    def test_set_temp_writes_rounded_value(self):
        device = _make_hc_device()
        entity = _wrap_hc(device)

        _run(entity.async_set_temperature(**{ATTR_TEMPERATURE: 21.3}))

        device.async_set_setpoint_temperature.assert_awaited_with(21.5)

    def test_set_temp_exact_half_degree(self):
        device = _make_hc_device()
        entity = _wrap_hc(device)

        _run(entity.async_set_temperature(**{ATTR_TEMPERATURE: 19.5}))

        device.async_set_setpoint_temperature.assert_awaited_with(19.5)

    def test_set_temp_none_arg_is_noop(self):
        device = _make_hc_device()
        entity = _wrap_hc(device)

        _run(entity.async_set_temperature())

        device.async_set_setpoint_temperature.assert_not_awaited()

    def test_set_temp_below_min_is_noop(self):
        device = _make_hc_device()
        entity = _wrap_hc(device)

        _run(entity.async_set_temperature(**{ATTR_TEMPERATURE: 4.9}))

        device.async_set_setpoint_temperature.assert_not_awaited()

    def test_set_temp_above_max_is_noop(self):
        device = _make_hc_device()
        entity = _wrap_hc(device)

        _run(entity.async_set_temperature(**{ATTR_TEMPERATURE: 30.1}))

        device.async_set_setpoint_temperature.assert_not_awaited()

    def test_set_temp_at_min_boundary_writes(self):
        device = _make_hc_device()
        entity = _wrap_hc(device)

        _run(entity.async_set_temperature(**{ATTR_TEMPERATURE: 5.0}))

        device.async_set_setpoint_temperature.assert_awaited_with(5.0)

    def test_set_temp_at_max_boundary_writes(self):
        device = _make_hc_device()
        entity = _wrap_hc(device)

        _run(entity.async_set_temperature(**{ATTR_TEMPERATURE: 30.0}))

        device.async_set_setpoint_temperature.assert_awaited_with(30.0)


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


# ===========================================================================
# HeatingCircuit — async_set_hvac_mode
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


class TestHeatingCircuitSetHvacModeAltHelpers:
    """Same branches as TestHeatingCircuitSetHvacMode, exercised via the
    device-factory + wrap helper pair instead of the kwargs-style builder —
    kept as a separate class (distinct test-method names) since it was
    originally in a different source file with its own helper style.
    """

    def test_set_auto_writes_automatic_operation_mode(self):
        device = _make_hc_device(operation_mode=OM_HC.MANUAL)
        entity = _wrap_hc(device)

        _run(entity.async_set_hvac_mode(HVACMode.AUTO))

        device.async_set_operation_mode.assert_awaited_with(OM_HC.AUTOMATIC)

    def test_set_heat_writes_manual_operation_mode(self):
        device = _make_hc_device(operation_mode=OM_HC.AUTOMATIC)
        entity = _wrap_hc(device)

        _run(entity.async_set_hvac_mode(HVACMode.HEAT))

        device.async_set_operation_mode.assert_awaited_with(OM_HC.MANUAL)

    def test_invalid_hvac_mode_is_noop(self):
        device = _make_hc_device()
        entity = _wrap_hc(device)

        _run(entity.async_set_hvac_mode(HVACMode.OFF))

        # OFF not in hvac_modes for HeatingCircuit → noop
        device.async_set_operation_mode.assert_not_awaited()

    def test_invalid_cool_mode_is_noop(self):
        device = _make_hc_device()
        entity = _wrap_hc(device)

        _run(entity.async_set_hvac_mode(HVACMode.COOL))

        device.async_set_operation_mode.assert_not_awaited()


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


# ===========================================================================
# HeatingCircuit — simple smoke tests (module-level test functions)
# ===========================================================================

def test_hvac_mode_auto():
    assert _hc(OM_HC.AUTOMATIC, False).hvac_mode == HVACMode.AUTO


def test_hvac_mode_heat():
    assert _hc(OM_HC.MANUAL, True).hvac_mode == HVACMode.HEAT


def test_hvac_action_heating_when_on():
    assert _hc(OM_HC.MANUAL, True).hvac_action == HVACAction.HEATING


def test_hvac_action_idle_when_off():
    assert _hc(OM_HC.AUTOMATIC, False).hvac_action == HVACAction.IDLE


def test_target_temperature_reads_setpoint():
    assert _hc(OM_HC.MANUAL, True, 19.5).target_temperature == 19.5


def test_current_temperature_is_none():
    assert _hc(OM_HC.MANUAL, True).current_temperature is None
