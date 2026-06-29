"""Unit tests for BatteryLevelSensor (sensor.py).

Pattern: bypass __init__ via Cls.__new__(Cls), inject fake device via SimpleNamespace.
No HA harness, no tests.common, no async_setup_entry.

Covers: native_value for all 5 BatteryLevelService.State values, unknown enum
value -> None guard (ValueError + AttributeError), options list, entity_category,
device_class, unique_id suffix, name.
"""

from __future__ import annotations

from types import SimpleNamespace

from boschshcpy.services_impl import BatteryLevelService
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.helpers.entity import EntityCategory

from custom_components.bosch_shc.sensor import BatteryLevelSensor

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _make_sensor(state):
    """Build a BatteryLevelSensor bypassing __init__, injecting a fake device."""
    s = BatteryLevelSensor.__new__(BatteryLevelSensor)
    s._device = SimpleNamespace(
        batterylevel=state,
        name="Motion Detector",
        root_device_id="root-abc",
        id="dev-123",
    )
    s._attr_name = "Battery Level"
    s._attr_unique_id = "root-abc_dev-123_battery_level"
    return s


# ---------------------------------------------------------------------------
# native_value — all 5 BatteryLevelService.State members
# ---------------------------------------------------------------------------


class TestBatteryLevelSensorNativeValue:
    def test_ok(self):
        assert _make_sensor(BatteryLevelService.State.OK).native_value == "ok"

    def test_low_battery(self):
        assert (
            _make_sensor(BatteryLevelService.State.LOW_BATTERY).native_value
            == "low_battery"
        )

    def test_critical_low(self):
        assert (
            _make_sensor(BatteryLevelService.State.CRITICAL_LOW).native_value
            == "critical_low"
        )

    def test_critically_low_battery(self):
        assert (
            _make_sensor(
                BatteryLevelService.State.CRITICALLY_LOW_BATTERY
            ).native_value
            == "critically_low_battery"
        )

    def test_not_available(self):
        assert (
            _make_sensor(BatteryLevelService.State.NOT_AVAILABLE).native_value
            == "not_available"
        )


# ---------------------------------------------------------------------------
# native_value — unknown / bad state yields None (guard)
# ---------------------------------------------------------------------------


class _BadState:
    """Simulates an unknown enum variant: .value raises ValueError."""

    @property
    def value(self):
        raise ValueError("Unknown battery state X")


class _MissingAttr:
    """Simulates a device with no batterylevel service: .value raises AttributeError."""

    @property
    def value(self):
        raise AttributeError("'NoneType' has no attribute 'value'")


class TestBatteryLevelSensorGuard:
    def test_value_error_returns_none(self):
        s = BatteryLevelSensor.__new__(BatteryLevelSensor)
        s._device = SimpleNamespace(
            batterylevel=_BadState(),
            name="Smoke Detector",
        )
        assert s.native_value is None

    def test_attribute_error_returns_none(self):
        s = BatteryLevelSensor.__new__(BatteryLevelSensor)
        s._device = SimpleNamespace(
            batterylevel=_MissingAttr(),
            name="Shutter Contact",
        )
        assert s.native_value is None


# ---------------------------------------------------------------------------
# Class-level metadata
# ---------------------------------------------------------------------------


class TestBatteryLevelSensorMetadata:
    def _sensor(self):
        return _make_sensor(BatteryLevelService.State.OK)

    def test_device_class_enum(self):
        assert self._sensor().device_class == SensorDeviceClass.ENUM

    def test_entity_category_diagnostic(self):
        assert self._sensor().entity_category == EntityCategory.DIAGNOSTIC

    def test_options_list(self):
        assert self._sensor().options == [
            "ok",
            "low_battery",
            "critical_low",
            "critically_low_battery",
            "not_available",
        ]

    def test_options_covers_all_enum_members(self):
        """Every BatteryLevelService.State .value (lowercased) must appear in options."""
        opts = set(self._sensor().options)
        for member in BatteryLevelService.State:
            assert member.value.lower() in opts, f"{member.value!r} missing from _attr_options"

    def test_unique_id_suffix(self):
        assert self._sensor()._attr_unique_id == "root-abc_dev-123_battery_level"

    def test_name(self):
        assert self._sensor()._attr_name == "Battery Level"
