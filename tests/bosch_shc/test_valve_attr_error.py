"""Unit tests for valve.py AttributeError catch in SHCValve.current_valve_position.

Verifies that AttributeError (e.g. from accessing .position on a device that
doesn't have it in a Bosch FW edge-case) is caught and returns None.

Pattern: __new__ bypass + SimpleNamespace device with a broken position property.
No HA harness.
"""
from __future__ import annotations

from types import SimpleNamespace

from custom_components.bosch_shc.valve import SHCValve


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
