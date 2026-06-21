"""Coverage for climate.py lines 23-46 (async_setup_entry) and
ClimateControl.__init__ + simple property getters (lines 62-64, 69, 79, 84, 99, 104, 161).

Does NOT duplicate test_climate.py / test_climate_unit.py / test_heating_circuit.py.
Pattern: SimpleNamespace fakes + asyncio.new_event_loop(); no HA harness, no network.
"""

import asyncio
from types import SimpleNamespace

from homeassistant.components.climate.const import HVACMode, ClimateEntityFeature
from homeassistant.const import UnitOfTemperature

from custom_components.bosch_shc.climate import ClimateControl, HeatingCircuit
from custom_components.bosch_shc.const import DATA_SESSION, DOMAIN


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_cc_device(
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
    ClimateControl.__init__ attribute access."""
    from boschshcpy.services_impl import RoomClimateControlService
    op = RoomClimateControlService.OperationMode(operation_mode_value)
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


def _make_hc_device(
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
    HeatingCircuit.__init__ attribute access."""
    from boschshcpy import SHCHeatingCircuit
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
        operation_mode=SHCHeatingCircuit.HeatingCircuitService.OperationMode.AUTOMATIC,
    )


def _make_room(name="Living Room"):
    return SimpleNamespace(name=name)


def _make_session(climate_controls=None, heating_circuits=None, rooms=None):
    """Fake SHCSession with device_helper and room() lookup."""
    rooms = rooms or {}
    dh = SimpleNamespace(
        climate_controls=climate_controls or [],
        heating_circuits=heating_circuits or [],
    )

    def _room(room_id):
        return rooms[room_id]

    return SimpleNamespace(device_helper=dh, room=_room)


def _make_hass(session, entry_id="entry-1"):
    return SimpleNamespace(
        data={DOMAIN: {entry_id: {DATA_SESSION: session}}}
    )


def _make_config_entry(entry_id="entry-1"):
    return SimpleNamespace(entry_id=entry_id)


# ---------------------------------------------------------------------------
# async_setup_entry — lines 23-46
# ---------------------------------------------------------------------------

class TestAsyncSetupEntry:
    """Drive async_setup_entry with various device combinations."""

    def _setup(self, climate_controls, heating_circuits, rooms, entry_id="entry-1"):
        from custom_components.bosch_shc.climate import async_setup_entry

        session = _make_session(
            climate_controls=climate_controls,
            heating_circuits=heating_circuits,
            rooms=rooms,
        )
        hass = _make_hass(session, entry_id)
        config_entry = _make_config_entry(entry_id)

        added = []
        _run(async_setup_entry(hass, config_entry, added.append))
        return added

    def test_no_devices_adds_nothing(self):
        """Lines 45-46: entities list empty → async_add_entities never called."""
        added = self._setup([], [], {})
        # async_add_entities receives each entity list (not called means no append)
        assert added == []

    def test_one_climate_control_added(self):
        """Lines 26-34: one climate → one ClimateControl entity appended."""
        dev = _make_cc_device(room_id="r1")
        rooms = {"r1": _make_room("Kitchen")}
        added = self._setup([dev], [], rooms)
        # async_add_entities is called once with the full list
        assert len(added) == 1
        entities = added[0]
        assert len(entities) == 1
        assert isinstance(entities[0], ClimateControl)

    def test_climate_control_name_uses_room_name(self):
        """Line 32-33: entity name = 'Room Climate <room.name>'."""
        dev = _make_cc_device(room_id="r2")
        rooms = {"r2": _make_room("Bedroom")}
        added = self._setup([dev], [], rooms)
        entity = added[0][0]
        assert entity.name == "Room Climate Bedroom"

    def test_one_heating_circuit_added(self):
        """Lines 36-43: one heating circuit → one HeatingCircuit entity."""
        dev = _make_hc_device()
        added = self._setup([], [dev], {})
        assert len(added) == 1
        entities = added[0]
        assert len(entities) == 1
        assert isinstance(entities[0], HeatingCircuit)

    def test_heating_circuit_name_from_device(self):
        """Line 42: HeatingCircuit name = heating_circuit.name."""
        dev = _make_hc_device(name="HC South Wing")
        added = self._setup([], [dev], {})
        entity = added[0][0]
        assert entity.name == "HC South Wing"

    def test_both_climate_and_heating_circuit(self):
        """Lines 26-43: mix of devices → both entity types in one list."""
        cc_dev = _make_cc_device(room_id="r3")
        hc_dev = _make_hc_device()
        rooms = {"r3": _make_room("Office")}
        added = self._setup([cc_dev], [hc_dev], rooms)
        assert len(added) == 1
        entities = added[0]
        assert len(entities) == 2
        types = {type(e) for e in entities}
        assert ClimateControl in types
        assert HeatingCircuit in types

    def test_multiple_climate_controls(self):
        """Two climates → two ClimateControl entities."""
        dev1 = _make_cc_device(root_device_id="r1", id_="d1", room_id="room-a")
        dev2 = _make_cc_device(root_device_id="r2", id_="d2", room_id="room-b")
        rooms = {
            "room-a": _make_room("Room A"),
            "room-b": _make_room("Room B"),
        }
        added = self._setup([dev1, dev2], [], rooms)
        assert len(added[0]) == 2

    def test_entry_id_passed_to_entity(self):
        """ClimateControl receives the correct entry_id."""
        dev = _make_cc_device(room_id="rx")
        rooms = {"rx": _make_room("X")}
        added = self._setup([dev], [], rooms, entry_id="my-entry-99")
        entity = added[0][0]
        assert entity._entry_id == "my-entry-99"


# ---------------------------------------------------------------------------
# ClimateControl.__init__ — lines 62-64
# ---------------------------------------------------------------------------

class TestClimateControlInit:
    """Real __init__ (via constructor, not __new__) covers lines 62-64."""

    def _make_entity(self, **kwargs):
        dev = _make_cc_device(**kwargs)
        return ClimateControl(device=dev, name="Test Room Climate", entry_id="e1")

    def test_init_sets_name(self):
        """Line 63: self._attr_name = name (ClimateControl stores name in _attr_name)."""
        entity = self._make_entity()
        assert entity._attr_name == "Test Room Climate"

    def test_init_sets_unique_id(self):
        """Line 64: self._attr_unique_id = root_device_id + '_' + id."""
        entity = self._make_entity(root_device_id="root-x", id_="dev-y")
        assert entity._attr_unique_id == "root-x_dev-y"

    def test_init_stores_device(self):
        """SHCEntity.__init__ (line 62 super()): self._device set."""
        dev = _make_cc_device()
        entity = ClimateControl(device=dev, name="N", entry_id="e")
        assert entity._device is dev

    def test_init_stores_entry_id(self):
        """SHCEntity.__init__: self._entry_id set."""
        entity = self._make_entity()
        assert entity._entry_id == "e1"


# ---------------------------------------------------------------------------
# ClimateControl simple property getters — lines 69, 79, 84, 99, 104, 161
# ---------------------------------------------------------------------------

class TestClimateControlProperties:
    """Simple read-only properties that return fixed values or read _device."""

    def _entity(self, **kwargs):
        dev = _make_cc_device(**kwargs)
        entity = ClimateControl.__new__(ClimateControl)
        entity._device = dev
        entity._attr_name = "Prop Test"  # device_name / name reads _attr_name
        entity._attr_unique_id = f"{dev.root_device_id}_{dev.id}"
        return entity

    # Line 69: name property
    def test_name_property(self):
        entity = self._entity()
        entity._attr_name = "Custom Name"
        assert entity.name == "Custom Name"

    # Line 79: temperature_unit
    def test_temperature_unit_is_celsius(self):
        entity = self._entity()
        assert entity.temperature_unit == UnitOfTemperature.CELSIUS

    # Line 84: current_temperature reads _device.temperature
    def test_current_temperature(self):
        entity = self._entity(temperature=19.5)
        assert entity.current_temperature == 19.5

    def test_current_temperature_different_value(self):
        entity = self._entity(temperature=22.0)
        assert entity.current_temperature == 22.0

    # Line 99: target_temperature reads _device.setpoint_temperature
    def test_target_temperature(self):
        entity = self._entity(setpoint_temperature=21.0)
        assert entity.target_temperature == 21.0

    def test_target_temperature_different_value(self):
        entity = self._entity(setpoint_temperature=18.5)
        assert entity.target_temperature == 18.5

    # Line 104: target_temperature_step
    def test_target_temperature_step(self):
        entity = self._entity()
        assert entity.target_temperature_step == 0.5

    # Line 161: supported_features
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

    # Line 69: device_name property (delegates to _name in ClimateControl)
    def test_device_name_matches_name(self):
        entity = self._entity()
        assert entity.device_name == entity.name
