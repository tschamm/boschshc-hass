"""Regression tests for valve.py and the ValveTappetSensor in sensor.py.

Issue #243: when the Bosch firmware sends an unknown ValveTappet state value
(e.g. NO_MOTOR_ERROR, a 7th enum member not present in boschshcpy), the call
to ValveTappetService.State(value) raises ValueError, which previously
propagated through valvestate → extra_state_attributes, killing the entity.

These tests verify that:
1. SHCValve.current_valve_position returns None (no raise) when the
   underlying device raises ValueError or KeyError.
2. ValveTappetSensor.extra_state_attributes returns {"valve_tappet_state": None}
   (no raise) when self._device.valvestate raises ValueError.
"""

from types import SimpleNamespace

import pytest

from custom_components.bosch_shc.sensor import ValveTappetSensor
from custom_components.bosch_shc.valve import SHCValve


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


def _make_valve_tappet_sensor(valvestate_raises=None, valvestate_name="VALVE_ADAPTION_SUCCESSFUL", position_value=42):
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


# ---------------------------------------------------------------------------
# ValveTappetSensor.extra_state_attributes — unknown valvestate guard
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
        sensor = _make_valve_tappet_sensor(
            valvestate_raises=ValueError("NO_MOTOR_ERROR")
        )
        try:
            sensor.extra_state_attributes
        except ValueError as exc:
            pytest.fail(f"extra_state_attributes raised ValueError: {exc!r}")

    def test_attribute_key_always_present(self):
        """The key 'valve_tappet_state' must always be present in the dict."""
        sensor = _make_valve_tappet_sensor(
            valvestate_raises=ValueError("unknown")
        )
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
