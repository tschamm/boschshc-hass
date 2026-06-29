"""Unit tests for sensor.py — coverage gaps not in test_sensor_unit.py.

Covers:
  - EmmaPowerSensor: native_value (device.value), extra_state_attributes
  - HumidityRatingSensor: ValueError guard → returns None
  - PurityRatingSensor: ValueError guard → returns None
  - TemperatureRatingSensor: ValueError guard → returns None
  - CommunicationQualitySensor: ValueError guard → returns None
  - CommunicationQualitySensor: AttributeError guard → returns None
  - ValveTappetSensor: extra_state_attributes with IN_START_POSITION state (path
    where valvestate.name does NOT raise, complementing the ValueError path)
  - IlluminanceLevelSensor: bool True / bool False → None (bool isinstance guard)
  - IlluminanceLevelSensor: float value passthrough

Pattern: __new__ bypass + SimpleNamespace device.
No HA harness.
"""
from __future__ import annotations

from types import SimpleNamespace

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import UnitOfPower

from custom_components.bosch_shc.sensor import (
    CommunicationQualitySensor,
    EmmaPowerSensor,
    HumidityRatingSensor,
    IlluminanceLevelSensor,
    PurityRatingSensor,
    TemperatureRatingSensor,
    ValveTappetSensor,
)

# ---------------------------------------------------------------------------
# Helpers for bad-enum simulation
# ---------------------------------------------------------------------------

class _BadEnum:
    """Simulates an enum whose .name property raises ValueError."""

    def __init__(self, exc_class=ValueError, message="unknown"):
        self._exc_class = exc_class
        self._message = message

    @property
    def name(self):
        raise self._exc_class(self._message)


# ---------------------------------------------------------------------------
# EmmaPowerSensor
# ---------------------------------------------------------------------------

def _emma_sensor(value=0.0, localized_subtitles="Consumed"):
    s = EmmaPowerSensor.__new__(EmmaPowerSensor)
    s._device = SimpleNamespace(
        value=value,
        localizedSubtitles=localized_subtitles,
    )
    return s


class TestEmmaPowerSensor:
    def test_native_value_positive(self):
        """Positive value = power fed to grid."""
        assert _emma_sensor(value=1500.0).native_value == 1500.0

    def test_native_value_negative(self):
        """Negative value = power consumed from grid."""
        assert _emma_sensor(value=-800.0).native_value == -800.0

    def test_native_value_zero(self):
        assert _emma_sensor(value=0.0).native_value == 0.0

    def test_extra_state_attributes_power_flow(self):
        s = _emma_sensor(localized_subtitles="Feeding")
        assert s.extra_state_attributes == {"power_flow": "Feeding"}

    def test_extra_state_attributes_key(self):
        """extra_state_attributes must always have 'power_flow' key."""
        s = _emma_sensor()
        assert "power_flow" in s.extra_state_attributes

    def test_device_class_is_power(self):
        assert _emma_sensor().device_class == SensorDeviceClass.POWER

    def test_unit_is_watt(self):
        assert _emma_sensor().native_unit_of_measurement == UnitOfPower.WATT

    def test_state_class_is_measurement(self):
        assert _emma_sensor().state_class == SensorStateClass.MEASUREMENT

    def test_entity_registry_enabled_default_false(self):
        """EmmaPowerSensor must be disabled by default (opt-in diagnostic).

        HA parent classes shadow _attr_entity_registry_enabled_default with a
        property, so we access via an instance to read the actual value.
        """
        s = _emma_sensor()
        assert s.entity_registry_enabled_default is False


# ---------------------------------------------------------------------------
# HumidityRatingSensor — ValueError guard
# ---------------------------------------------------------------------------

def _humidity_rating_sensor_bad():
    s = HumidityRatingSensor.__new__(HumidityRatingSensor)
    s._device = SimpleNamespace(humidity_rating=_BadEnum(), name="test-twinguard")
    return s


class TestHumidityRatingSensorErrorGuard:
    def test_value_error_returns_none(self):
        """ValueError on humidity_rating.name must return None, not crash."""
        s = _humidity_rating_sensor_bad()
        assert s.native_value is None

    def test_normal_rating_returns_name(self):
        """Sanity: a valid enum must still return the .name string."""
        from boschshcpy.services_impl import AirQualityLevelService
        s = HumidityRatingSensor.__new__(HumidityRatingSensor)
        s._device = SimpleNamespace(
            humidity_rating=AirQualityLevelService.RatingState.GOOD,
            name="twinguard-1",
        )
        assert s.native_value == "GOOD"


# ---------------------------------------------------------------------------
# PurityRatingSensor — ValueError guard
# ---------------------------------------------------------------------------

def _purity_rating_sensor_bad():
    s = PurityRatingSensor.__new__(PurityRatingSensor)
    s._device = SimpleNamespace(purity_rating=_BadEnum(), name="test-twinguard")
    return s


class TestPurityRatingSensorErrorGuard:
    def test_value_error_returns_none(self):
        """ValueError on purity_rating.name must return None."""
        s = _purity_rating_sensor_bad()
        assert s.native_value is None

    def test_normal_rating_returns_name(self):
        from boschshcpy.services_impl import AirQualityLevelService
        s = PurityRatingSensor.__new__(PurityRatingSensor)
        s._device = SimpleNamespace(
            purity_rating=AirQualityLevelService.RatingState.BAD,
            name="twinguard-1",
        )
        assert s.native_value == "BAD"


# ---------------------------------------------------------------------------
# TemperatureRatingSensor — ValueError guard
# ---------------------------------------------------------------------------

def _temp_rating_sensor_bad():
    s = TemperatureRatingSensor.__new__(TemperatureRatingSensor)
    s._device = SimpleNamespace(temperature_rating=_BadEnum(), name="test-twinguard")
    return s


class TestTemperatureRatingSensorErrorGuard:
    def test_value_error_returns_none(self):
        """ValueError on temperature_rating.name must return None."""
        s = _temp_rating_sensor_bad()
        assert s.native_value is None

    def test_normal_rating_returns_name(self):
        from boschshcpy.services_impl import AirQualityLevelService
        s = TemperatureRatingSensor.__new__(TemperatureRatingSensor)
        s._device = SimpleNamespace(
            temperature_rating=AirQualityLevelService.RatingState.MEDIUM,
            name="twinguard-1",
        )
        assert s.native_value == "MEDIUM"


# ---------------------------------------------------------------------------
# CommunicationQualitySensor — ValueError + AttributeError guards
# ---------------------------------------------------------------------------

def _comm_sensor(comm_quality, name="test-plug"):
    s = CommunicationQualitySensor.__new__(CommunicationQualitySensor)
    s._device = SimpleNamespace(communicationquality=comm_quality, name=name)
    return s


class TestCommunicationQualitySensorErrorGuard:
    def test_value_error_returns_none(self):
        """ValueError on communicationquality.name must return None."""
        s = _comm_sensor(_BadEnum(ValueError, "bad value"))
        assert s.native_value is None

    def test_attribute_error_returns_none(self):
        """AttributeError (no .name attribute at all) must return None."""
        s = _comm_sensor(_BadEnum(AttributeError, "no name attr"))
        assert s.native_value is None

    def test_communicationquality_none_attribute_error_returns_none(self):
        """If communicationquality itself is None, .name raises AttributeError → None."""
        s = CommunicationQualitySensor.__new__(CommunicationQualitySensor)

        class _NoName:
            @property
            def name(self):
                raise AttributeError("no such attr")

        s._device = SimpleNamespace(communicationquality=_NoName(), name="plug-1")
        assert s.native_value is None


# ---------------------------------------------------------------------------
# ValveTappetSensor — extra_state_attributes happy path variants
# ---------------------------------------------------------------------------

def _valve_tappet_sensor(position, valvestate_name):
    """Build ValveTappetSensor with a fake enum that has a fixed .name."""

    class _FakeState:
        name = valvestate_name

    s = ValveTappetSensor.__new__(ValveTappetSensor)
    s._device = SimpleNamespace(
        position=position,
        valvestate=_FakeState(),
        name="thermostat-1",
    )
    return s


class TestValveTappetSensorExtraAttrs:
    def test_in_start_position_state(self):
        s = _valve_tappet_sensor(0, "IN_START_POSITION")
        assert s.extra_state_attributes == {"valve_tappet_state": "IN_START_POSITION"}

    def test_run_to_next_position_state(self):
        s = _valve_tappet_sensor(50, "VALVE_ADAPTION_IN_PROGRESS")
        assert s.extra_state_attributes == {
            "valve_tappet_state": "VALVE_ADAPTION_IN_PROGRESS"
        }

    def test_position_reflected_in_native_value(self):
        s = _valve_tappet_sensor(75, "VALVE_ADAPTION_SUCCESSFUL")
        assert s.native_value == 75

    def test_position_zero(self):
        s = _valve_tappet_sensor(0, "NOT_AVAILABLE")
        assert s.native_value == 0


# ---------------------------------------------------------------------------
# IlluminanceLevelSensor — bool guard (line 611-612 of sensor.py)
# ---------------------------------------------------------------------------

def _illum_sensor(value):
    s = IlluminanceLevelSensor.__new__(IlluminanceLevelSensor)
    s._device = SimpleNamespace(illuminance=value)
    return s


class TestIlluminanceLevelSensorBoolGuard:
    def test_bool_true_returns_none(self):
        """Bool True is a subclass of int; must return None, not 1."""
        assert _illum_sensor(True).native_value is None

    def test_bool_false_returns_none(self):
        """Bool False is a subclass of int; must return None, not 0."""
        assert _illum_sensor(False).native_value is None

    def test_float_value_returned(self):
        """Float lux value must pass through unchanged."""
        assert _illum_sensor(9.5).native_value == 9.5

    def test_large_int_returned(self):
        assert _illum_sensor(10000).native_value == 10000

    def test_string_returns_none(self):
        assert _illum_sensor("MEDIUM").native_value is None

    def test_none_returns_none(self):
        assert _illum_sensor(None).native_value is None
