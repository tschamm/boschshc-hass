"""Unit tests for binary_sensor.py entity classes.

Pattern: bypass __init__ via Cls.__new__(Cls), inject fake device via SimpleNamespace.
No HA harness, no tests.common, no async_setup_entry.
"""

from enum import Enum
from types import SimpleNamespace

import pytest

from boschshcpy import (
    SHCBatteryDevice,
    SHCSmokeDetectionSystem,
    SHCSmokeDetector,
    SHCShutterContact,
    SHCShutterContact2Plus,
    SHCWaterLeakageSensor,
)
from homeassistant.components.binary_sensor import BinarySensorDeviceClass

from custom_components.bosch_shc.binary_sensor import (
    BatterySensor,
    MotionDetectionSensor,
    ShutterContactSensor,
    ShutterContactVibrationSensor,
    SmokeDetectionSystemSensor,
    SmokeDetectorSensor,
    WaterLeakageDetectorSensor,
)


# ---------------------------------------------------------------------------
# ShutterContactSensor
# ---------------------------------------------------------------------------


def _shutter_sensor(state, device_class="GENERIC"):
    s = ShutterContactSensor.__new__(ShutterContactSensor)
    s._device = SimpleNamespace(state=state, device_class=device_class)
    return s


class TestShutterContactSensor:
    def test_open_is_on(self):
        s = _shutter_sensor(SHCShutterContact.ShutterContactService.State.OPEN)
        assert s.is_on is True

    def test_closed_is_off(self):
        s = _shutter_sensor(SHCShutterContact.ShutterContactService.State.CLOSED)
        assert s.is_on is False

    def test_device_class_entrance_door(self):
        s = _shutter_sensor(SHCShutterContact.ShutterContactService.State.CLOSED, "ENTRANCE_DOOR")
        assert s.device_class == BinarySensorDeviceClass.DOOR

    def test_device_class_regular_window(self):
        s = _shutter_sensor(SHCShutterContact.ShutterContactService.State.CLOSED, "REGULAR_WINDOW")
        assert s.device_class == BinarySensorDeviceClass.WINDOW

    def test_device_class_french_window(self):
        s = _shutter_sensor(SHCShutterContact.ShutterContactService.State.CLOSED, "FRENCH_WINDOW")
        assert s.device_class == BinarySensorDeviceClass.DOOR

    def test_device_class_generic(self):
        s = _shutter_sensor(SHCShutterContact.ShutterContactService.State.CLOSED, "GENERIC")
        assert s.device_class == BinarySensorDeviceClass.WINDOW

    def test_device_class_unknown_defaults_to_window(self):
        s = _shutter_sensor(SHCShutterContact.ShutterContactService.State.CLOSED, "UNKNOWN_TYPE")
        assert s.device_class == BinarySensorDeviceClass.WINDOW


# ---------------------------------------------------------------------------
# ShutterContactVibrationSensor
# ---------------------------------------------------------------------------


def _vibration_sensor(state):
    s = ShutterContactVibrationSensor.__new__(ShutterContactVibrationSensor)
    s._device = SimpleNamespace(vibrationsensor=state)
    return s


class TestShutterContactVibrationSensor:
    def test_vibration_detected_is_on(self):
        s = _vibration_sensor(
            SHCShutterContact2Plus.VibrationSensorService.State.VIBRATION_DETECTED
        )
        assert s.is_on is True

    def test_no_vibration_is_off(self):
        s = _vibration_sensor(
            SHCShutterContact2Plus.VibrationSensorService.State.NO_VIBRATION
        )
        assert s.is_on is False

    def test_unknown_state_is_off(self):
        s = _vibration_sensor(
            SHCShutterContact2Plus.VibrationSensorService.State.UNKNOWN
        )
        assert s.is_on is False

    def test_device_class_is_vibration(self):
        s = _vibration_sensor(
            SHCShutterContact2Plus.VibrationSensorService.State.NO_VIBRATION
        )
        assert s._attr_device_class == BinarySensorDeviceClass.VIBRATION


# ---------------------------------------------------------------------------
# MotionDetectionSensor
# ---------------------------------------------------------------------------
# (existing test_motion_sensor.py covers is_on; here we add extra_state_attributes)


def _motion_sensor(latestmotion):
    s = MotionDetectionSensor.__new__(MotionDetectionSensor)
    s._device = SimpleNamespace(latestmotion=latestmotion)
    return s


class TestMotionDetectionSensor:
    def test_extra_state_attributes_contains_last_motion(self):
        ts = "2026-06-20T10:00:00.000Z"
        s = _motion_sensor(ts)
        attrs = s.extra_state_attributes
        assert attrs == {"last_motion_detected": ts}

    def test_extra_state_attributes_none_timestamp(self):
        s = _motion_sensor(None)
        assert s.extra_state_attributes == {"last_motion_detected": None}

    def test_should_poll_is_true(self):
        s = _motion_sensor("2026-06-20T10:00:00.000Z")
        assert s.should_poll is True

    def test_is_on_recent_motion(self):
        from datetime import datetime, timedelta, timezone
        recent = datetime.now(timezone.utc) - timedelta(seconds=10)
        ts = recent.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        assert _motion_sensor(ts).is_on is True

    def test_is_on_old_motion_is_false(self):
        from datetime import datetime, timedelta, timezone
        old = datetime.now(timezone.utc) - timedelta(minutes=10)
        ts = old.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        assert _motion_sensor(ts).is_on is False

    def test_is_on_none_returns_false(self):
        assert _motion_sensor(None).is_on is False

    def test_is_on_garbage_returns_false(self):
        assert _motion_sensor("not-a-date").is_on is False


# ---------------------------------------------------------------------------
# SmokeDetectorSensor
# ---------------------------------------------------------------------------
# Covers the PRIMARY/SECONDARY allowlist and INTRUSION_ALARM exclusion (issue #191)


def _smoke_sensor(alarm_state):
    s = SmokeDetectorSensor.__new__(SmokeDetectorSensor)
    state = SHCSmokeDetector.AlarmService.State[alarm_state]
    check_state = SHCSmokeDetector.SmokeDetectorCheckService.State.NONE
    s._device = SimpleNamespace(
        alarmstate=state,
        smokedetectorcheck_state=check_state,
    )
    return s


class TestSmokeDetectorSensor:
    def test_idle_off_is_not_smoke(self):
        assert _smoke_sensor("IDLE_OFF").is_on is False

    def test_intrusion_alarm_does_not_trigger_smoke(self):
        """IDS intrusion alarm must NOT make smoke sensor report smoke (issue #191)."""
        assert _smoke_sensor("INTRUSION_ALARM").is_on is False

    def test_primary_alarm_is_smoke(self):
        assert _smoke_sensor("PRIMARY_ALARM").is_on is True

    def test_secondary_alarm_is_smoke(self):
        assert _smoke_sensor("SECONDARY_ALARM").is_on is True

    def test_device_class_is_smoke(self):
        s = _smoke_sensor("IDLE_OFF")
        assert s._attr_device_class == BinarySensorDeviceClass.SMOKE

    def test_icon(self):
        s = _smoke_sensor("IDLE_OFF")
        assert s.icon == "mdi:smoke-detector"

    def test_extra_state_attributes_idle(self):
        s = _smoke_sensor("IDLE_OFF")
        attrs = s.extra_state_attributes
        assert attrs["alarmstate"] == "IDLE_OFF"
        assert attrs["smokedetectorcheck_state"] == "NONE"

    def test_extra_state_attributes_primary_alarm(self):
        s = _smoke_sensor("PRIMARY_ALARM")
        attrs = s.extra_state_attributes
        assert attrs["alarmstate"] == "PRIMARY_ALARM"

    def test_extra_state_attributes_smoke_test_ok(self):
        sensor = SmokeDetectorSensor.__new__(SmokeDetectorSensor)
        sensor._device = SimpleNamespace(
            alarmstate=SHCSmokeDetector.AlarmService.State.IDLE_OFF,
            smokedetectorcheck_state=SHCSmokeDetector.SmokeDetectorCheckService.State.SMOKE_TEST_OK,
        )
        attrs = sensor.extra_state_attributes
        assert attrs["smokedetectorcheck_state"] == "SMOKE_TEST_OK"

    def test_extra_state_attributes_smoke_test_requested(self):
        sensor = SmokeDetectorSensor.__new__(SmokeDetectorSensor)
        sensor._device = SimpleNamespace(
            alarmstate=SHCSmokeDetector.AlarmService.State.IDLE_OFF,
            smokedetectorcheck_state=SHCSmokeDetector.SmokeDetectorCheckService.State.SMOKE_TEST_REQUESTED,
        )
        attrs = sensor.extra_state_attributes
        assert attrs["smokedetectorcheck_state"] == "SMOKE_TEST_REQUESTED"

    def test_extra_state_attributes_smoke_test_failed(self):
        sensor = SmokeDetectorSensor.__new__(SmokeDetectorSensor)
        sensor._device = SimpleNamespace(
            alarmstate=SHCSmokeDetector.AlarmService.State.IDLE_OFF,
            smokedetectorcheck_state=SHCSmokeDetector.SmokeDetectorCheckService.State.SMOKE_TEST_FAILED,
        )
        attrs = sensor.extra_state_attributes
        assert attrs["smokedetectorcheck_state"] == "SMOKE_TEST_FAILED"


# ---------------------------------------------------------------------------
# SmokeDetectionSystemSensor
# ---------------------------------------------------------------------------


def _sds_sensor(alarm_state_name):
    s = SmokeDetectionSystemSensor.__new__(SmokeDetectionSystemSensor)
    state = SHCSmokeDetectionSystem.SurveillanceAlarmService.State[alarm_state_name]
    s._device = SimpleNamespace(alarm=state)
    return s


class TestSmokeDetectionSystemSensor:
    def test_alarm_off_is_not_smoke(self):
        assert _sds_sensor("ALARM_OFF").is_on is False

    def test_alarm_on_is_smoke(self):
        assert _sds_sensor("ALARM_ON").is_on is True

    def test_alarm_muted_is_smoke(self):
        """Muted alarm is still considered active smoke."""
        assert _sds_sensor("ALARM_MUTED").is_on is True

    def test_device_class_is_smoke(self):
        s = _sds_sensor("ALARM_OFF")
        assert s._attr_device_class == BinarySensorDeviceClass.SMOKE

    def test_icon(self):
        s = _sds_sensor("ALARM_OFF")
        assert s.icon == "mdi:smoke-detector"

    def test_extra_state_attributes_alarm_off(self):
        s = _sds_sensor("ALARM_OFF")
        assert s.extra_state_attributes == {"alarm_state": "ALARM_OFF"}

    def test_extra_state_attributes_alarm_on(self):
        s = _sds_sensor("ALARM_ON")
        assert s.extra_state_attributes == {"alarm_state": "ALARM_ON"}

    def test_extra_state_attributes_alarm_muted(self):
        s = _sds_sensor("ALARM_MUTED")
        assert s.extra_state_attributes == {"alarm_state": "ALARM_MUTED"}


# ---------------------------------------------------------------------------
# WaterLeakageDetectorSensor
# ---------------------------------------------------------------------------


def _water_sensor(leakage_state, push_notification_state, acoustic_signal_state):
    s = WaterLeakageDetectorSensor.__new__(WaterLeakageDetectorSensor)
    s._device = SimpleNamespace(
        leakage_state=leakage_state,
        push_notification_state=push_notification_state,
        acoustic_signal_state=acoustic_signal_state,
    )
    return s


_TILT_ENABLED = SHCWaterLeakageSensor.WaterLeakageSensorTiltService.State.ENABLED
_TILT_DISABLED = SHCWaterLeakageSensor.WaterLeakageSensorTiltService.State.DISABLED
_WL_DETECTED = SHCWaterLeakageSensor.WaterLeakageSensorService.State.LEAKAGE_DETECTED
_WL_NONE = SHCWaterLeakageSensor.WaterLeakageSensorService.State.NO_LEAKAGE


class TestWaterLeakageDetectorSensor:
    def test_leakage_detected_is_on(self):
        s = _water_sensor(_WL_DETECTED, _TILT_ENABLED, _TILT_ENABLED)
        assert s.is_on is True

    def test_no_leakage_is_off(self):
        s = _water_sensor(_WL_NONE, _TILT_ENABLED, _TILT_ENABLED)
        assert s.is_on is False

    def test_device_class_is_moisture(self):
        s = _water_sensor(_WL_NONE, _TILT_ENABLED, _TILT_ENABLED)
        assert s._attr_device_class == BinarySensorDeviceClass.MOISTURE

    def test_icon(self):
        s = _water_sensor(_WL_NONE, _TILT_ENABLED, _TILT_ENABLED)
        assert s.icon == "mdi:water-alert"

    def test_extra_state_attributes_enabled(self):
        s = _water_sensor(_WL_NONE, _TILT_ENABLED, _TILT_ENABLED)
        attrs = s.extra_state_attributes
        assert attrs["push_notification_state"] == "ENABLED"
        assert attrs["acoustic_signal_state"] == "ENABLED"

    def test_extra_state_attributes_disabled(self):
        s = _water_sensor(_WL_NONE, _TILT_DISABLED, _TILT_DISABLED)
        attrs = s.extra_state_attributes
        assert attrs["push_notification_state"] == "DISABLED"
        assert attrs["acoustic_signal_state"] == "DISABLED"

    def test_extra_state_attributes_mixed(self):
        s = _water_sensor(_WL_DETECTED, _TILT_ENABLED, _TILT_DISABLED)
        attrs = s.extra_state_attributes
        assert attrs["push_notification_state"] == "ENABLED"
        assert attrs["acoustic_signal_state"] == "DISABLED"


# ---------------------------------------------------------------------------
# BatterySensor
# ---------------------------------------------------------------------------


def _battery_sensor(battery_level):
    s = BatterySensor.__new__(BatterySensor)
    s._device = SimpleNamespace(batterylevel=battery_level)
    return s


class TestBatterySensor:
    def test_ok_is_off(self):
        s = _battery_sensor(SHCBatteryDevice.BatteryLevelService.State.OK)
        assert s.is_on is False

    def test_low_battery_is_on(self):
        s = _battery_sensor(SHCBatteryDevice.BatteryLevelService.State.LOW_BATTERY)
        assert s.is_on is True

    def test_critical_low_is_on(self):
        s = _battery_sensor(SHCBatteryDevice.BatteryLevelService.State.CRITICAL_LOW)
        assert s.is_on is True

    def test_critically_low_battery_is_on(self):
        s = _battery_sensor(
            SHCBatteryDevice.BatteryLevelService.State.CRITICALLY_LOW_BATTERY
        )
        assert s.is_on is True

    def test_not_available_is_on(self):
        """NOT_AVAILABLE is != OK, so is_on is True (diagnostic)."""
        s = _battery_sensor(SHCBatteryDevice.BatteryLevelService.State.NOT_AVAILABLE)
        assert s.is_on is True

    def test_device_class_is_battery(self):
        s = _battery_sensor(SHCBatteryDevice.BatteryLevelService.State.OK)
        assert s._attr_device_class == BinarySensorDeviceClass.BATTERY
