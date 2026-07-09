"""Tests for valve.py: SHCValve, async_setup_entry, and the related
ValveTappetSensor in sensor.py.

Covers:
- SHCValve.current_valve_position defensive guards against ValueError,
  KeyError and AttributeError raised by the underlying device (issue #243:
  unknown ValveTappet firmware states such as NO_MOTOR_ERROR must not
  propagate and kill the entity).
- ValveTappetSensor.extra_state_attributes must likewise never raise on an
  unknown valvestate enum value.
- SHCValve.__init__ attribute wiring and class-level attribute declarations.
- async_setup_entry: thermostats -> SHCValve entities, including
  device/room exclusion handling (device_excluded continue branch).

Pure-unit style throughout: __new__ bypass + SimpleNamespace device doubles,
no HA test harness.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from homeassistant.components.valve import ValveDeviceClass
from homeassistant.helpers.entity import EntityCategory

from custom_components.bosch_shc.const import (
    OPT_EXCLUDED_DEVICES,
    OPT_EXCLUDED_ROOMS,
)
from custom_components.bosch_shc.sensor import ValveTappetSensor
from custom_components.bosch_shc.valve import SHCValve, async_setup_entry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_valve(position_raises=None, position_value=50):
    """Build an SHCValve bypassing SHCEntity.__init__."""
    valve = SHCValve.__new__(SHCValve)

    if position_raises is not None:
        exc = position_raises

        class _RaisesOnPosition:
            name = "test-thermostat"

            @property
            def position(self):
                raise exc

        valve._device = _RaisesOnPosition()
    else:
        valve._device = SimpleNamespace(
            name="test-thermostat",
            position=position_value,
        )
    return valve


def _make_valve_with_broken_position(exc):
    """Build SHCValve where .position raises exc."""

    class _BrokenDevice:
        id = "dev-1"
        root_device_id = "root-1"
        name = "Broken Valve"

        @property
        def position(self):
            raise exc

    valve = SHCValve.__new__(SHCValve)
    valve._device = _BrokenDevice()
    valve._attr_name = "Valve"
    valve._attr_unique_id = "root-1_dev-1_valve"
    return valve


def _make_valve_tappet_sensor(
    valvestate_raises=None, valvestate_name="VALVE_ADAPTION_SUCCESSFUL", position_value=42
):
    """Build a ValveTappetSensor bypassing SHCEntity.__init__."""
    sensor = ValveTappetSensor.__new__(ValveTappetSensor)

    if valvestate_raises is not None:
        exc = valvestate_raises

        class _FakeValvestate:
            @property
            def name(self):
                raise exc

        class _RaisesOnValvestate:
            name = "test-thermostat"
            position = position_value

            @property
            def valvestate(self):
                return _FakeValvestate()

        sensor._device = _RaisesOnValvestate()
    else:
        valvestate = SimpleNamespace(name=valvestate_name)
        sensor._device = SimpleNamespace(
            name="test-thermostat",
            position=position_value,
            valvestate=valvestate,
        )
    return sensor


def _fake_device(name="test-valve", root_device_id="root1", device_id="dev1"):
    return SimpleNamespace(
        name=name,
        root_device_id=root_device_id,
        id=device_id,
        position=50,
    )


def _fake_thermostat(dev_id="thermo-001", room_id=None, position=50, root_id="root-thermo"):
    """Minimal thermostat double compatible with device_excluded() and SHCValve."""
    return SimpleNamespace(
        name="Thermostat 1",
        id=dev_id,
        root_device_id=root_id,
        room_id=room_id,
        manufacturer="Bosch",
        device_model="TRV",
        status="AVAILABLE",
        device_services=[],
        deleted=False,
        position=position,
    )


def _make_entry(options=None, entry_id="E1", session=None):
    entry = SimpleNamespace(options=options or {}, entry_id=entry_id)
    entry.runtime_data = SimpleNamespace(session=session)
    return entry


def _run_setup(session, entry):
    entry.runtime_data.session = session
    hass = SimpleNamespace()
    collected = []

    def add(entities):
        collected.extend(entities)

    asyncio.run(async_setup_entry(hass, entry, add))
    return collected


def _session(thermostats):
    return SimpleNamespace(device_helper=SimpleNamespace(thermostats=thermostats))


def _make_hass() -> SimpleNamespace:
    return SimpleNamespace()


def _make_config_entry(session: object) -> SimpleNamespace:
    entry = SimpleNamespace(options={}, entry_id="E1")
    entry.runtime_data = SimpleNamespace(session=session)
    return entry


def _collect() -> tuple[list, callable]:
    """Return (collected_list, async_add_entities callable)."""
    collected: list = []

    def add(entities: list) -> None:
        collected.extend(entities)

    return collected, add


def _valve_device() -> SimpleNamespace:
    """Minimal device for SHCValve.__init__."""
    return SimpleNamespace(
        name="Test Valve",
        id="hdm:HomeMaticIP:valve1",
        root_device_id="aa:bb:cc:00:00:05",
        serial="serial-valve1",
        position=50,
        device_services=[],
        manufacturer="Bosch",
        device_model="TRV",
        status="AVAILABLE",
        deleted=False,
    )


# ---------------------------------------------------------------------------
# valve.py — async_setup_entry  (lines 27-40): thermostats -> SHCValve
# ---------------------------------------------------------------------------


class TestValveSetupEntry:
    """Valve async_setup_entry: thermostats → SHCValve."""

    def _run(self, session: object) -> list:
        hass = _make_hass()
        entry = _make_config_entry(session)
        collected, add = _collect()

        asyncio.run(async_setup_entry(hass, entry, add))  # type: ignore[arg-type]
        return collected

    def test_thermostats_produce_shc_valve_entities(self) -> None:
        """session.device_helper.thermostats → SHCValve."""
        dev = _valve_device()
        session = SimpleNamespace(device_helper=SimpleNamespace(thermostats=[dev]))
        result = self._run(session)
        assert len(result) == 1
        assert isinstance(result[0], SHCValve)

    def test_no_thermostats_adds_nothing(self) -> None:
        """No thermostats → nothing added."""
        session = SimpleNamespace(device_helper=SimpleNamespace(thermostats=[]))
        result = self._run(session)
        assert result == []

    def test_attr_name_valve_applied(self) -> None:
        """async_setup_entry always passes attr_name='Valve'.

        With _attr_has_entity_name=True, _attr_name holds only the feature
        label; HA prepends the device name for display ('Test Valve Valve').
        """
        dev = _valve_device()
        session = SimpleNamespace(device_helper=SimpleNamespace(thermostats=[dev]))
        result = self._run(session)
        assert result[0]._attr_name == "Valve"

    def test_unique_id_includes_valve_suffix(self) -> None:
        """unique_id ends in '_valve'."""
        dev = _valve_device()
        session = SimpleNamespace(device_helper=SimpleNamespace(thermostats=[dev]))
        result = self._run(session)
        assert result[0]._attr_unique_id.endswith("_valve")

    def test_multiple_thermostats_all_collected(self) -> None:
        """Two thermostats → two SHCValve entities."""
        session = SimpleNamespace(
            device_helper=SimpleNamespace(thermostats=[_valve_device(), _valve_device()])
        )
        result = self._run(session)
        assert len(result) == 2
        assert all(isinstance(e, SHCValve) for e in result)

    def test_entry_id_stored(self) -> None:
        dev = _valve_device()
        session = SimpleNamespace(device_helper=SimpleNamespace(thermostats=[dev]))
        result = self._run(session)
        assert result[0]._entry_id == "E1"


# ---------------------------------------------------------------------------
# valve.py — async_setup_entry: device_excluded continue branch (line 34)
# ---------------------------------------------------------------------------


class TestValveSetupEntryExcluded:
    def test_excluded_device_not_added(self):
        """Thermostat in OPT_EXCLUDED_DEVICES → continue → not in entities."""
        thermo = _fake_thermostat(dev_id="thermo-excl")
        session = _session([thermo])
        entry = _make_entry(options={OPT_EXCLUDED_DEVICES: ["thermo-excl"]})
        result = _run_setup(session, entry)
        assert result == []

    def test_non_excluded_device_is_added(self):
        """Sanity: thermostat NOT excluded → SHCValve entity is created."""
        thermo = _fake_thermostat(dev_id="thermo-ok")
        session = _session([thermo])
        entry = _make_entry()
        result = _run_setup(session, entry)
        assert len(result) == 1
        assert isinstance(result[0], SHCValve)

    def test_mixed_one_excluded_one_not(self):
        """One excluded, one not → only the non-excluded ends up in entities."""
        excl = _fake_thermostat(dev_id="thermo-excl")
        ok = _fake_thermostat(dev_id="thermo-ok")
        session = _session([excl, ok])
        entry = _make_entry(options={OPT_EXCLUDED_DEVICES: ["thermo-excl"]})
        result = _run_setup(session, entry)
        assert len(result) == 1
        assert result[0]._device is ok

    def test_excluded_by_room_not_added(self):
        """Room-level exclusion also hits the continue on line 34."""
        thermo = _fake_thermostat(dev_id="thermo-room", room_id="room-99")
        session = _session([thermo])
        entry = _make_entry(options={OPT_EXCLUDED_ROOMS: ["room-99"]})
        result = _run_setup(session, entry)
        assert result == []

    def test_all_excluded_yields_empty(self):
        """All thermostats excluded → async_add_entities never called."""
        t1 = _fake_thermostat(dev_id="t1")
        t2 = _fake_thermostat(dev_id="t2")
        session = _session([t1, t2])
        entry = _make_entry(options={OPT_EXCLUDED_DEVICES: ["t1", "t2"]})
        result = _run_setup(session, entry)
        assert result == []

    def test_no_thermostats_yields_empty(self):
        """No thermostats at all → empty result (async_add_entities not called)."""
        session = _session([])
        entry = _make_entry()
        result = _run_setup(session, entry)
        assert result == []

    def test_valve_entry_id_set_correctly(self):
        """The SHCValve entity's _entry_id matches the config entry's entry_id."""
        thermo = _fake_thermostat(dev_id="thermo-id-check")
        session = _session([thermo])
        entry = _make_entry(entry_id="myentry")
        result = _run_setup(session, entry)
        assert result[0]._entry_id == "myentry"

    def test_valve_attr_name_is_valve(self):
        """async_setup_entry passes attr_name='Valve' to SHCValve."""
        thermo = _fake_thermostat(dev_id="thermo-name")
        session = _session([thermo])
        entry = _make_entry()
        result = _run_setup(session, entry)
        assert result[0]._attr_name == "Valve"

    def test_valve_unique_id_includes_valve_suffix(self):
        thermo = _fake_thermostat(dev_id="thermo-uid", root_id="root-uid")
        session = _session([thermo])
        entry = _make_entry()
        result = _run_setup(session, entry)
        assert result[0]._attr_unique_id == "root-uid_thermo-uid_valve"


# ---------------------------------------------------------------------------
# SHCValve.current_valve_position — defensive guards
# ---------------------------------------------------------------------------


class TestSHCValvePosition:
    """current_valve_position must never propagate ValueError or KeyError."""

    def test_returns_position_normally(self):
        valve = _make_valve(position_value=35)
        assert valve.current_valve_position == 35

    def test_returns_none_on_value_error(self):
        """Simulate firmware sending a value the int() cast rejects."""
        valve = _make_valve(position_raises=ValueError("unexpected firmware value"))
        result = valve.current_valve_position
        assert result is None

    def test_returns_none_on_key_error(self):
        """Simulate missing 'position' key in service state dict."""
        valve = _make_valve(position_raises=KeyError("position"))
        result = valve.current_valve_position
        assert result is None

    def test_does_not_raise_on_value_error(self):
        """Explicitly confirm no exception escapes."""
        valve = _make_valve(position_raises=ValueError("NO_MOTOR_ERROR"))
        try:
            valve.current_valve_position
        except (ValueError, KeyError) as exc:
            pytest.fail(f"current_valve_position raised {exc!r}")

    def test_returns_zero_position(self):
        """Zero (fully closed) must not be confused with None."""
        valve = _make_valve(position_value=0)
        assert valve.current_valve_position == 0

    def test_returns_hundred_position(self):
        """100 (fully open) must be returned as-is."""
        valve = _make_valve(position_value=100)
        assert valve.current_valve_position == 100

    def test_rounds_fractional_position_instead_of_truncating(self):
        """Regression: int() truncates toward zero (63.9 -> 63); round() must
        be used instead, same precision class as the Twinguard fix (#352)."""
        valve = _make_valve(position_value=63.9)
        assert valve.current_valve_position == 64


class TestSHCValveCurrentPositionErrors:
    def test_value_error_returns_none(self):
        valve = _make_valve_with_broken_position(ValueError("bad enum"))
        result = valve.current_valve_position
        assert result is None

    def test_key_error_returns_none(self):
        valve = _make_valve_with_broken_position(KeyError("missing"))
        result = valve.current_valve_position
        assert result is None

    def test_attribute_error_returns_none(self):
        """AttributeError must be caught after the fix (Addresses #243)."""
        valve = _make_valve_with_broken_position(AttributeError("no position"))
        result = valve.current_valve_position
        assert result is None

    def test_valid_position_returned(self):
        """Normal operation — position returned as-is."""
        valve = SHCValve.__new__(SHCValve)
        valve._device = SimpleNamespace(
            id="dev-1", root_device_id="root-1", name="OK Valve", position=42
        )
        valve._attr_name = "Valve"
        valve._attr_unique_id = "root-1_dev-1_valve"
        assert valve.current_valve_position == 42


# ---------------------------------------------------------------------------
# SHCValve.__init__ and class-level attributes
# ---------------------------------------------------------------------------


class TestSHCValveInit:
    """Cover SHCValve.__init__ lines 57-66."""

    def test_init_no_attr_name_sets_name_none(self):
        # With _attr_has_entity_name=True (from SHCEntity), _attr_name=None means
        # HA uses the device name as the entity name.
        dev = _fake_device()
        valve = SHCValve(device=dev, entry_id="test", attr_name=None)
        assert valve._attr_name is None

    def test_init_no_attr_name_sets_unique_id(self):
        dev = _fake_device()
        valve = SHCValve(device=dev, entry_id="test", attr_name=None)
        assert valve._attr_unique_id == "root1_dev1"

    def test_init_with_attr_name_sets_attr_name(self):
        # _attr_name stores only the suffix; HA auto-prepends the device name at runtime.
        dev = _fake_device()
        valve = SHCValve(device=dev, entry_id="test", attr_name="Valve")
        assert valve._attr_name == "Valve"

    def test_init_with_attr_name_lowercased_in_unique_id(self):
        dev = _fake_device()
        valve = SHCValve(device=dev, entry_id="test", attr_name="Valve")
        assert valve._attr_unique_id == "root1_dev1_valve"

    def test_init_device_stored(self):
        dev = _fake_device()
        valve = SHCValve(device=dev, entry_id="test", attr_name="Valve")
        assert valve._device is dev

    def test_init_entry_id_stored(self):
        dev = _fake_device()
        valve = SHCValve(device=dev, entry_id="myentry", attr_name=None)
        assert valve._entry_id == "myentry"

    def test_init_attr_name_mixed_case_lowercased_in_unique_id(self):
        dev = _fake_device(name="my-cam", root_device_id="root2", device_id="dev2")
        valve = SHCValve(device=dev, entry_id="e", attr_name="ThermoValve")
        assert valve._attr_unique_id == "root2_dev2_thermovalve"
        assert valve._attr_name == "ThermoValve"


class TestSHCValveClassAttrs:
    """Cover class-level attribute declarations (lines 46-48).

    Access via instance because HA parent classes shadow some attrs with properties.
    """

    def _make_valve(self):
        dev = _fake_device()
        return SHCValve(device=dev, entry_id="test", attr_name="Valve")

    def test_device_class_is_water(self):
        valve = self._make_valve()
        assert valve.device_class == ValveDeviceClass.WATER

    def test_entity_category_is_diagnostic(self):
        valve = self._make_valve()
        assert valve.entity_category == EntityCategory.DIAGNOSTIC

    def test_reports_position_is_true(self):
        valve = self._make_valve()
        assert valve.reports_position is True

    def test_no_custom_open_close_in_shcvalve(self):
        """Valve is read-only: SHCValve does not override open_valve / close_valve."""
        # SHCValve must NOT define open_valve, close_valve, or set_valve_position
        # in its own __dict__ (i.e. not overriding the parent no-ops).
        assert "open_valve" not in SHCValve.__dict__
        assert "close_valve" not in SHCValve.__dict__
        assert "set_valve_position" not in SHCValve.__dict__


# ---------------------------------------------------------------------------
# ValveTappetSensor.extra_state_attributes — unknown valvestate guard
#
# ValveTappetSensor lives in sensor.py, not valve.py; grouped here (last) as
# it doesn't correspond to any entity class defined in this platform file.
# ---------------------------------------------------------------------------


class TestValveTappetSensorExtraAttributes:
    """extra_state_attributes must return None for valve_tappet_state on unknown enum value."""

    def test_returns_state_name_normally(self):
        sensor = _make_valve_tappet_sensor(valvestate_name="VALVE_ADAPTION_SUCCESSFUL")
        attrs = sensor.extra_state_attributes
        assert attrs["valve_tappet_state"] == "VALVE_ADAPTION_SUCCESSFUL"

    def test_returns_none_on_unknown_firmware_value(self):
        """Issue #243: unknown state enum value must yield None, not raise."""
        sensor = _make_valve_tappet_sensor(
            valvestate_raises=ValueError("'NO_MOTOR_ERROR' is not a valid State")
        )
        attrs = sensor.extra_state_attributes
        assert attrs["valve_tappet_state"] is None

    def test_does_not_raise_on_unknown_firmware_value(self):
        """Explicitly assert no ValueError escapes."""
        sensor = _make_valve_tappet_sensor(valvestate_raises=ValueError("NO_MOTOR_ERROR"))
        try:
            sensor.extra_state_attributes
        except ValueError as exc:
            pytest.fail(f"extra_state_attributes raised ValueError: {exc!r}")

    def test_attribute_key_always_present(self):
        """The key 'valve_tappet_state' must always be present in the dict."""
        sensor = _make_valve_tappet_sensor(valvestate_raises=ValueError("unknown"))
        attrs = sensor.extra_state_attributes
        assert "valve_tappet_state" in attrs

    def test_native_value_unaffected_by_valvestate_error(self):
        """native_value (position) must still work even when valvestate raises."""
        sensor = _make_valve_tappet_sensor(
            valvestate_raises=ValueError("NO_MOTOR_ERROR"),
            position_value=72,
        )
        assert sensor.native_value == 72

    def test_known_state_in_start_position(self):
        sensor = _make_valve_tappet_sensor(valvestate_name="IN_START_POSITION")
        assert sensor.extra_state_attributes["valve_tappet_state"] == "IN_START_POSITION"

    def test_known_state_not_available(self):
        sensor = _make_valve_tappet_sensor(valvestate_name="NOT_AVAILABLE")
        assert sensor.extra_state_attributes["valve_tappet_state"] == "NOT_AVAILABLE"
