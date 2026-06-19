"""Regression test for IlluminanceLevelSensor state_class vs Gen1 string value.

Bug: IlluminanceLevelSensor declares SensorStateClass.MEASUREMENT, which requires
a numeric native_value. SHCMotionDetector (Gen1, model "MD") returns a string
from .illuminance (e.g. "MEDIUM"). Returning a string with MEASUREMENT makes HA
reject/log an error for the state update.

SHCMotionDetector2 (Gen2, model "MD2") returns an int — no issue there.

Fix: IlluminanceLevelSensor must NOT set state_class=MEASUREMENT for Gen1 devices
whose .illuminance is a string. Options: (a) drop state_class entirely so it works
for both, or (b) guard native_value / override based on type.

The chosen fix here: drop state_class on the class level (leave it None) — Gen1
is a qualitative enum, Gen2 is numeric; without state_class both are still reported
correctly by HA.
"""

from types import SimpleNamespace

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


class TestIlluminanceSensorGen1:
    """Gen1 SHCMotionDetector returns a string — MEASUREMENT must not be set."""

    def test_gen1_native_value_is_string(self):
        """native_value for Gen1 returns the string as-is (e.g. 'MEDIUM')."""
        sensor = _make_sensor("MEDIUM")
        assert sensor.native_value == "MEDIUM"

    def test_gen1_no_measurement_state_class(self):
        """CORE BUG: state_class=MEASUREMENT must NOT be set when value is a string.

        HA rejects non-numeric values with MEASUREMENT state_class. The fix is
        to remove state_class from the class declaration so it is None by default,
        or to guard it per device type.
        """
        sensor = _make_sensor("MEDIUM")
        # After fix: state_class must be None (not MEASUREMENT) for string-valued sensors
        assert sensor.state_class is None, (
            f"state_class should be None for Gen1 string illuminance, got {sensor.state_class!r}"
        )

    def test_gen1_low_value(self):
        sensor = _make_sensor("LOW")
        assert sensor.native_value == "LOW"

    def test_gen1_high_value(self):
        sensor = _make_sensor("HIGH")
        assert sensor.native_value == "HIGH"


class TestIlluminanceSensorGen2:
    """Gen2 SHCMotionDetector2 returns an int — numeric reporting must work."""

    def test_gen2_native_value_is_int(self):
        sensor = _make_sensor(320)
        assert sensor.native_value == 320

    def test_gen2_zero_value(self):
        sensor = _make_sensor(0)
        assert sensor.native_value == 0

    def test_gen2_high_lux(self):
        sensor = _make_sensor(1000)
        assert sensor.native_value == 1000

    def test_gen2_state_class_is_none(self):
        """After removing class-level state_class, it should be None for all instances.

        If a future improvement restores numeric state_class dynamically per type,
        this test should be updated accordingly.
        """
        sensor = _make_sensor(500)
        assert sensor.state_class is None
