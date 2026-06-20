"""Tests for IlluminanceLevelSensor state_class conditional logic (#315).

The Bosch SHC API spec defines illuminance as integer for both Gen1 (model "MD")
and Gen2 (model "MD2").  In practice Gen1 devices report numeric lux values too.

Fix: state_class=MEASUREMENT, device_class=illuminance, unit=lx are set
conditionally — only when native_value is numeric (int/float).  If a firmware
variant returns a qualitative string the sensor degrades gracefully without
triggering state_class_removed repairs.
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


class TestIlluminanceSensorNumericGen1:
    """Gen1 'MD' devices that report numeric lux (e.g. 13, 9, 22) — #315."""

    def test_native_value_int(self):
        assert _make_sensor(13).native_value == 13

    def test_native_value_zero(self):
        assert _make_sensor(0).native_value == 0

    def test_state_class_is_measurement(self):
        assert _make_sensor(13).state_class == SensorStateClass.MEASUREMENT

    def test_device_class_is_illuminance(self):
        assert _make_sensor(22).device_class == SensorDeviceClass.ILLUMINANCE

    def test_unit_is_lux(self):
        assert _make_sensor(9).native_unit_of_measurement == LIGHT_LUX


class TestIlluminanceSensorStringFallback:
    """Graceful fallback for any firmware returning a qualitative string."""

    def test_native_value_is_string(self):
        assert _make_sensor("MEDIUM").native_value == "MEDIUM"

    def test_no_state_class_for_string(self):
        """HA rejects MEASUREMENT with non-numeric values — must be None."""
        assert _make_sensor("MEDIUM").state_class is None

    def test_no_device_class_for_string(self):
        assert _make_sensor("LOW").device_class is None

    def test_no_unit_for_string(self):
        assert _make_sensor("HIGH").native_unit_of_measurement is None

    def test_gen1_low_value(self):
        assert _make_sensor("LOW").native_value == "LOW"

    def test_gen1_high_value(self):
        assert _make_sensor("HIGH").native_value == "HIGH"


class TestIlluminanceSensorGen2:
    """Gen2 SHCMotionDetector2 ('MD2') always returns int."""

    def test_native_value(self):
        assert _make_sensor(320).native_value == 320

    def test_state_class_measurement(self):
        assert _make_sensor(320).state_class == SensorStateClass.MEASUREMENT

    def test_device_class_illuminance(self):
        assert _make_sensor(500).device_class == SensorDeviceClass.ILLUMINANCE

    def test_unit_lux(self):
        assert _make_sensor(1000).native_unit_of_measurement == LIGHT_LUX

    def test_zero_lux(self):
        assert _make_sensor(0).state_class == SensorStateClass.MEASUREMENT

    def test_float_value(self):
        """Float illuminance values are also treated as numeric."""
        assert _make_sensor(13.5).state_class == SensorStateClass.MEASUREMENT
        assert _make_sensor(13.5).device_class == SensorDeviceClass.ILLUMINANCE
        assert _make_sensor(13.5).native_unit_of_measurement == LIGHT_LUX
