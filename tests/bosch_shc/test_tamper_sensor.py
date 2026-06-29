"""Unit tests for TamperSensor (binary_sensor.py).

Uses __new__ bypass + SimpleNamespace device pattern. No HA harness.
"""
from __future__ import annotations

from types import SimpleNamespace

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.helpers.entity import EntityCategory

from custom_components.bosch_shc.binary_sensor import TamperSensor

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tamper_sensor(was_tampered=False, last_tamper_time="n/a"):
    dev = SimpleNamespace(
        name="Motion Detector II",
        id="hdm:ZigBee:md2-001",
        root_device_id="64-da-a0-xx-xx-xx",
        was_tampered=was_tampered,
        last_tamper_time=last_tamper_time,
    )
    sensor = TamperSensor.__new__(TamperSensor)
    sensor._device = dev
    sensor._attr_name = "Tamper"
    sensor._attr_unique_id = f"{dev.root_device_id}_{dev.id}_tamper"
    return sensor


# ---------------------------------------------------------------------------
# Class-level attributes
# ---------------------------------------------------------------------------

class TestTamperSensorClassAttrs:
    def test_device_class_is_tamper(self):
        sensor = TamperSensor.__new__(TamperSensor)
        assert sensor._attr_device_class == BinarySensorDeviceClass.TAMPER

    def test_entity_category_is_diagnostic(self):
        sensor = TamperSensor.__new__(TamperSensor)
        assert sensor._attr_entity_category == EntityCategory.DIAGNOSTIC


# ---------------------------------------------------------------------------
# is_on property
# ---------------------------------------------------------------------------

class TestTamperSensorIsOn:
    def test_is_on_when_tampered(self):
        sensor = _make_tamper_sensor(was_tampered=True)
        assert sensor.is_on is True

    def test_is_off_when_not_tampered(self):
        sensor = _make_tamper_sensor(was_tampered=False)
        assert sensor.is_on is False

    def test_is_on_with_falsy_was_tampered(self):
        """Getattr default and bool() coercion guard."""
        dev = SimpleNamespace(
            name="MD2",
            id="dev1",
            root_device_id="root1",
            was_tampered=0,
        )
        sensor = TamperSensor.__new__(TamperSensor)
        sensor._device = dev
        assert sensor.is_on is False

    def test_is_off_when_attribute_missing(self):
        """When device has no was_tampered, getattr default=False → is_on=False."""
        dev = SimpleNamespace(
            name="MD2",
            id="dev1",
            root_device_id="root1",
            # no was_tampered
        )
        sensor = TamperSensor.__new__(TamperSensor)
        sensor._device = dev
        assert sensor.is_on is False


# ---------------------------------------------------------------------------
# extra_state_attributes
# ---------------------------------------------------------------------------

class TestTamperSensorExtraAttrs:
    def test_extra_attrs_contains_last_tamper_time(self):
        ts = "2026-06-21T10:00:00.000Z"
        sensor = _make_tamper_sensor(last_tamper_time=ts)
        attrs = sensor.extra_state_attributes
        assert "last_tamper_time" in attrs
        assert attrs["last_tamper_time"] == ts

    def test_extra_attrs_last_tamper_time_none_when_missing(self):
        """When device has no last_tamper_time, falls back to None."""
        dev = SimpleNamespace(
            name="MD2",
            id="dev1",
            root_device_id="root1",
            was_tampered=False,
            # no last_tamper_time
        )
        sensor = TamperSensor.__new__(TamperSensor)
        sensor._device = dev
        attrs = sensor.extra_state_attributes
        assert attrs["last_tamper_time"] is None

    def test_extra_attrs_default_na_time_passthrough(self):
        sensor = _make_tamper_sensor(last_tamper_time="n/a")
        attrs = sensor.extra_state_attributes
        assert attrs["last_tamper_time"] == "n/a"


# ---------------------------------------------------------------------------
# unique_id / attr_name
# ---------------------------------------------------------------------------

class TestTamperSensorIdentifiers:
    def test_unique_id_format(self):
        sensor = _make_tamper_sensor()
        assert sensor._attr_unique_id.endswith("_tamper")

    def test_attr_name_is_tamper(self):
        sensor = _make_tamper_sensor()
        assert sensor._attr_name == "Tamper"
