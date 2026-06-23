"""Unit tests for #339 sensor tidy-ups.

- CommunicationQuality is a Diagnostics-category ENUM sensor whose state is a
  lowercase, translatable slug (no more raw ALL-CAPS "GOOD"/"BAD").
- BatteryLevel (the granular enum sensor that duplicates the binary "Battery")
  is disabled by default.

HA's entity metaclass turns class-body _attr_* into property descriptors, so we
read the public properties on a __new__ instance — no HA harness needed.
"""

from types import SimpleNamespace

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.helpers.entity import EntityCategory

from custom_components.bosch_shc.sensor import (
    BatteryLevelSensor,
    CommunicationQualitySensor,
)


def _comm():
    return CommunicationQualitySensor.__new__(CommunicationQualitySensor)


def test_comm_quality_is_diagnostic_enum():
    s = _comm()
    assert s.entity_category == EntityCategory.DIAGNOSTIC
    assert s.device_class == SensorDeviceClass.ENUM
    assert s.translation_key == "communication_quality"
    # options are lowercase slugs so HA can translate the displayed label
    assert all(o == o.lower() for o in s.options)


def test_comm_quality_native_value_is_lowercase_slug():
    s = _comm()
    s._device = SimpleNamespace(
        communicationquality=SimpleNamespace(name="GOOD"), name="plug"
    )
    assert s.native_value == "good"
    # the slug must be a declared option (else HA logs an "invalid state" warning)
    assert s.native_value in s.options


def test_battery_level_disabled_by_default():
    # #339: it duplicates the binary "Battery" sensor → hidden unless opted in.
    s = BatteryLevelSensor.__new__(BatteryLevelSensor)
    assert s.entity_registry_enabled_default is False
    assert s.entity_category == EntityCategory.DIAGNOSTIC
