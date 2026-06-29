"""Tests for IlluminanceLevelSensor metadata + value handling (#315).

The Bosch SHC API spec defines illuminance as integer for both Gen1 (model "MD")
and Gen2 (model "MD2").  Gen1 devices report numeric lux values too.

Fix: state_class=MEASUREMENT, device_class=illuminance, unit=lx are STATIC so
they never flip-flop (a momentary None value used to drop state_class and
re-raise the state_class_removed repair).  native_value coerces any non-numeric
value to None so a hypothetical qualitative-string firmware degrades to
"unknown" instead of conflicting with the measurement state_class.
"""

from types import SimpleNamespace

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import LIGHT_LUX

from custom_components.bosch_shc.sensor import IlluminanceLevelSensor


def _make_sensor(illuminance_value):
    """Build an IlluminanceLevelSensor bypassing SHCEntity.__init__."""
    sensor = IlluminanceLevelSensor.__new__(IlluminanceLevelSensor)
    sensor._device = SimpleNamespace(
        illuminance=illuminance_value,
        name="test-motion",
        id="test-id",
        root_device_id="test-root",
    )
    sensor._attr_name = "test-motion Illuminance"
    sensor._attr_unique_id = "test-root_test-id_illuminance"
    return sensor


class TestStaticMetadata:
    """Metadata is static — independent of the current value."""

    def test_state_class_numeric(self):
        assert _make_sensor(13).state_class == SensorStateClass.MEASUREMENT

    def test_device_class_numeric(self):
        assert _make_sensor(13).device_class == SensorDeviceClass.ILLUMINANCE

    def test_unit_numeric(self):
        assert _make_sensor(13).native_unit_of_measurement == LIGHT_LUX

    def test_metadata_stable_when_value_none(self):
        """Regression #315: a None value must NOT drop state_class/unit
        (that re-raised the state_class_removed repair + unit-change warnings).
        """
        s = _make_sensor(None)
        assert s.state_class == SensorStateClass.MEASUREMENT
        assert s.device_class == SensorDeviceClass.ILLUMINANCE
        assert s.native_unit_of_measurement == LIGHT_LUX

    def test_metadata_stable_for_string(self):
        s = _make_sensor("MEDIUM")
        assert s.state_class == SensorStateClass.MEASUREMENT
        assert s.device_class == SensorDeviceClass.ILLUMINANCE
        assert s.native_unit_of_measurement == LIGHT_LUX


class TestNativeValue:
    """native_value returns numeric lux, else None."""

    def test_int(self):
        assert _make_sensor(13).native_value == 13

    def test_zero(self):
        assert _make_sensor(0).native_value == 0

    def test_float(self):
        assert _make_sensor(13.5).native_value == 13.5

    def test_large_gen2(self):
        assert _make_sensor(1000).native_value == 1000

    def test_none_value(self):
        assert _make_sensor(None).native_value is None

    def test_string_coerced_to_none(self):
        """Qualitative string degrades to None (no measurement conflict)."""
        assert _make_sensor("MEDIUM").native_value is None
        assert _make_sensor("LOW").native_value is None

    def test_bool_coerced_to_none(self):
        """Bool is an int subclass but is not a real lux reading."""
        assert _make_sensor(True).native_value is None
        assert _make_sensor(False).native_value is None
