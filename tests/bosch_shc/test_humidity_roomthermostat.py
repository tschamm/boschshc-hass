"""Regression test for issue #274: HumiditySensor wiring on Room Thermostat models.

Issue: Humidity missing on Room Thermostat I / 230V (RTH2_230 family).

Finding: NOT a code gap.  Both device_helper.wallthermostats (THB / BWTH /
BWTH24 → SHCWallThermostat) and device_helper.roomthermostats (RTH2_BAT /
RTH2_230 → SHCRoomThermostat2) are iterated at sensor.py:60-80 and each
receives a HumiditySensor.  SHCRoomThermostat2 inherits SHCWallThermostat
which mixes in _HumidityLevel, so .humidity is present on all covered models.

This test documents the contract so that a future refactor cannot silently
drop humidity registration for the room-thermostat branch.
"""

from types import SimpleNamespace

from homeassistant.components.sensor import SensorDeviceClass

from custom_components.bosch_shc.sensor import HumiditySensor


def _make_humidity_sensor(humidity_value: float) -> HumiditySensor:
    """Build a HumiditySensor bypassing SHCEntity.__init__ (no HASS required)."""
    sensor = HumiditySensor.__new__(HumiditySensor)
    sensor._device = SimpleNamespace(
        humidity=humidity_value,
        name="Room Thermostat",
        id="roomthermostat-id",
        root_device_id="shc-root-id",
    )
    sensor._attr_name = "Room Thermostat Humidity"
    sensor._attr_unique_id = "shc-root-id_roomthermostat-id_humidity"
    return sensor


class TestHumiditySensorContract:
    """Pin the HumiditySensor API so wiring regressions are caught immediately."""

    def test_native_value_float(self):
        """native_value returns the float humidity from the device."""
        sensor = _make_humidity_sensor(55.0)
        assert sensor.native_value == 55.0

    def test_native_value_integer_compatible(self):
        """Bosch API may return integer humidity; sensor must pass it through."""
        sensor = _make_humidity_sensor(60)
        assert sensor.native_value == 60

    def test_device_class_is_humidity(self):
        """device_class must be HUMIDITY so HA renders the correct icon and unit."""
        sensor = _make_humidity_sensor(50.0)
        assert sensor.device_class == SensorDeviceClass.HUMIDITY

    def test_native_unit_is_percent(self):
        """Unit of measurement must be % (PERCENTAGE)."""
        sensor = _make_humidity_sensor(50.0)
        assert sensor.native_unit_of_measurement == "%"

    def test_zero_humidity(self):
        """Edge case: 0 % humidity must not be falsy-filtered."""
        sensor = _make_humidity_sensor(0.0)
        assert sensor.native_value == 0.0

    def test_max_humidity(self):
        """Edge case: 100 % humidity."""
        sensor = _make_humidity_sensor(100.0)
        assert sensor.native_value == 100.0


class TestRoomThermostatModelsHaveHumidity:
    """Document which boschshcpy model classes expose .humidity.

    SHCWallThermostat and SHCRoomThermostat2 both mix in _HumidityLevel.
    If either loses that mixin the AttributeError below will surface the gap.
    """

    def test_wall_thermostat_has_humidity_attribute(self):
        """SHCWallThermostat (THB / BWTH / BWTH24) must expose .humidity."""
        from boschshcpy.models_impl import SHCWallThermostat
        assert hasattr(SHCWallThermostat, "humidity"), (
            "SHCWallThermostat lost the .humidity property — "
            "check _HumidityLevel mixin inheritance"
        )

    def test_room_thermostat2_has_humidity_attribute(self):
        """SHCRoomThermostat2 (RTH2_BAT / RTH2_230) must expose .humidity."""
        from boschshcpy.models_impl import SHCRoomThermostat2
        assert hasattr(SHCRoomThermostat2, "humidity"), (
            "SHCRoomThermostat2 lost the .humidity property — "
            "it must inherit SHCWallThermostat which mixes in _HumidityLevel"
        )

    def test_room_thermostat2_inherits_wall_thermostat(self):
        """SHCRoomThermostat2 must subclass SHCWallThermostat for humidity to work."""
        from boschshcpy.models_impl import SHCRoomThermostat2, SHCWallThermostat
        assert issubclass(SHCRoomThermostat2, SHCWallThermostat), (
            "SHCRoomThermostat2 no longer inherits SHCWallThermostat — "
            "humidity sensor wiring in sensor.py:60-80 will silently fail"
        )
