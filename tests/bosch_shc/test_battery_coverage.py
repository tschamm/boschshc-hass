"""Battery reporting coverage contract (requested check).

Guards against silent drift in battery support:

1. Every battery-powered device class in the lib (SHCBatteryDevice subclass)
   must be wired into the HA battery entity loops — otherwise a newly added
   battery device would ship with NO battery sensor and nobody would notice.
2. The device_helper accessors used by those loops must actually be present in
   both binary_sensor.py and sensor.py (the binary "Battery" + enum
   "Battery Level" entities).
3. BatteryLevelService.State must stay exhaustive — a new Bosch firmware enum
   value would otherwise slip through BatterySensor.is_on (`!= OK`) silently.

Pattern: pure inspection + source scan; no HA harness, no live session.
Run:
  PYTHONPATH="<hass>:<lib>" PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
    python3 -m pytest tests/bosch_shc/test_battery_coverage.py -q -o addopts=""
"""

import inspect

import boschshcpy.models_impl as models
from boschshcpy import SHCBatteryDevice
from boschshcpy.services_impl import BatteryLevelService


# Battery-capable lib classes known to be wired into the HA battery loops.
# device_helper accessor in parentheses (binary_sensor.py:234-244 /
# sensor.py battery loop). If a NEW battery device is added to the lib, the
# drift test below fails until it is wired in here AND in both loops.
KNOWN_BATTERY_DEVICE_CLASSES = {
    "SHCMotionDetector",       # motion_detectors
    "SHCMotionDetector2",      # motion_detectors2
    "SHCShutterContact",       # shutter_contacts
    "SHCShutterContact2",      # shutter_contacts2
    "SHCShutterContact2Plus",  # shutter_contacts2
    "SHCSmokeDetector",        # smoke_detectors
    "SHCThermostat",           # thermostats
    "SHCWallThermostat",       # wallthermostats
    "SHCRoomThermostat2",      # roomthermostats
    "SHCTwinguard",            # twinguards
    "SHCUniversalSwitch",      # universal_switches
    "SHCUniversalSwitch2",     # universal_switches
    "SHCWaterLeakageSensor",   # water_leakage_detectors
}

# device_helper accessors the battery loops iterate. Each must appear in BOTH
# binary_sensor.py (binary "Battery") and sensor.py (enum "Battery Level").
REQUIRED_BATTERY_ACCESSORS = {
    "motion_detectors",
    "motion_detectors2",
    "shutter_contacts",
    "shutter_contacts2",
    "smoke_detectors",
    "thermostats",
    "twinguards",
    "universal_switches",
    "wallthermostats",
    "roomthermostats",
    "water_leakage_detectors",
}


def _lib_battery_subclasses():
    return {
        cls.__name__
        for _, cls in inspect.getmembers(models, inspect.isclass)
        if issubclass(cls, SHCBatteryDevice) and cls is not SHCBatteryDevice
    }


class TestBatteryDeviceWiring:
    def test_no_unwired_battery_device(self):
        """Every SHCBatteryDevice subclass must be accounted for. A new battery
        device added to the lib fails this until it is wired into the HA battery
        loops (binary_sensor.py + sensor.py) and listed above."""
        actual = _lib_battery_subclasses()
        new = actual - KNOWN_BATTERY_DEVICE_CLASSES
        assert not new, (
            f"New battery-powered device class(es) {sorted(new)} in boschshcpy "
            f"are NOT wired into the HA battery entity loops. Add the device's "
            f"device_helper accessor to the battery loop in BOTH "
            f"binary_sensor.py and sensor.py, then add the class name to "
            f"KNOWN_BATTERY_DEVICE_CLASSES."
        )

    def test_known_set_has_no_stale_entries(self):
        """KNOWN set must not reference classes that no longer exist / no longer
        carry a battery (caught after a lib refactor)."""
        actual = _lib_battery_subclasses()
        stale = KNOWN_BATTERY_DEVICE_CLASSES - actual
        assert not stale, (
            f"KNOWN_BATTERY_DEVICE_CLASSES lists {sorted(stale)} which are no "
            f"longer SHCBatteryDevice subclasses — remove them."
        )

    def test_accessors_present_in_binary_sensor_and_sensor(self):
        """The battery-loop device_helper accessors must exist in both platform
        files, so each battery device gets the binary 'Battery' AND the enum
        'Battery Level' entity."""
        import custom_components.bosch_shc.binary_sensor as bs
        import custom_components.bosch_shc.sensor as sn

        for module in (bs, sn):
            src = inspect.getsource(module)
            missing = {
                acc
                for acc in REQUIRED_BATTERY_ACCESSORS
                if f"device_helper.{acc}" not in src
            }
            assert not missing, (
                f"{module.__name__} battery loop is missing accessors "
                f"{sorted(missing)} — battery entities would not be created for "
                f"those devices."
            )


class TestBatteryEnumExhaustive:
    def test_state_members_are_exactly_the_handled_set(self):
        """BatterySensor.is_on returns `level != OK` after explicit branches for
        NOT_AVAILABLE / LOW_BATTERY / CRITICAL_LOW / CRITICALLY_LOW_BATTERY. If
        Bosch firmware adds a new enum value, this fails so the new state is
        consciously triaged (problem vs benign) in is_on before shipping."""
        expected = {
            "OK",
            "LOW_BATTERY",
            "CRITICAL_LOW",
            "CRITICALLY_LOW_BATTERY",
            "NOT_AVAILABLE",
        }
        actual = {m.value for m in BatteryLevelService.State}
        assert actual == expected, (
            f"BatteryLevelService.State changed: {sorted(actual ^ expected)}. "
            f"Update BatterySensor.is_on (binary_sensor.py) and this expected "
            f"set."
        )
