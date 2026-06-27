"""Additional pure-unit coverage for binary_sensor.py.

Targets uncovered lines not hit by test_binary_sensor_unit.py,
test_binary_sensor_setup.py, test_thread_safety_fire.py, or
test_motion_detector2.py:

- SmokeDetectionSystemSensor.extra_state_attributes (alarm_state key)
- SmokeDetectionSystemSensor._attr_name is None after __init__
- SmokeDetectionSystemSensor._attr_unique_id format after override
- BatterySensor.is_on logging paths (NOT_AVAILABLE, LOW_BATTERY, CRITICAL_LOW)
- MotionDetectionSensor._input_events_handler payload (event_type/subtype/name)
- OccupancyDetectionSensor.__init__ real call (name + unique_id)
- ShutterContactVibrationSensor._attr_name / unique_id set by __init__
- SmokeDetectorSensor.is_on SECONDARY_ALARM path specifically via is_on check
- WaterLeakageDetectorSensor extra_state_attributes when leakage detected

Pattern: __new__ bypass + SimpleNamespace; no HA harness.

Run with:
  PYTHONPATH="<boschshc-hass>:<boschshcpy>" \\
  PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \\
  python3 -m pytest tests/bosch_shc/test_binary_sensor_coverage.py -q -o addopts=
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from boschshcpy import (
    SHCBatteryDevice,
    SHCSmokeDetectionSystem,
    SHCSmokeDetector,
    SHCShutterContact2Plus,
    SHCWaterLeakageSensor,
)

from custom_components.bosch_shc.binary_sensor import (
    BatterySensor,
    MotionDetectionSensor,
    OccupancyDetectionSensor,
    ShutterContactVibrationSensor,
    SmokeDetectionSystemSensor,
    SmokeDetectorSensor,
    WaterLeakageDetectorSensor,
)
from custom_components.bosch_shc.const import (
    ATTR_EVENT_TYPE,
    ATTR_EVENT_SUBTYPE,
    ATTR_LAST_TIME_TRIGGERED,
    EVENT_BOSCH_SHC,
)
from homeassistant.const import ATTR_DEVICE_ID, ATTR_ID, ATTR_NAME


# ---------------------------------------------------------------------------
# Helper: base device with all fields SHCEntity.__init__ needs
# ---------------------------------------------------------------------------

def _base_device(device_id="dev1", name="FakeDev", root_device_id="root1",
                 device_services=None):
    return SimpleNamespace(
        id=device_id,
        name=name,
        root_device_id=root_device_id,
        device_services=device_services or [],
        manufacturer="Bosch",
        device_model="FakeModel",
        serial=f"{device_id}-serial",
        deleted=False,
        status="AVAILABLE",
        subscribe_callback=lambda key, cb: None,
        unsubscribe_callback=lambda key: None,
    )


# ---------------------------------------------------------------------------
# SmokeDetectionSystemSensor — extra_state_attributes
# ---------------------------------------------------------------------------

def _sds_sensor(alarm_state_name):
    """Build SmokeDetectionSystemSensor via __new__."""
    s = SmokeDetectionSystemSensor.__new__(SmokeDetectionSystemSensor)
    s._device = SimpleNamespace(
        alarm=SHCSmokeDetectionSystem.SurveillanceAlarmService.State[alarm_state_name]
    )
    return s


class TestSmokeDetectionSystemSensorAttributes:
    """extra_state_attributes must return alarm_state as a string name."""

    def test_extra_state_attributes_alarm_off(self):
        s = _sds_sensor("ALARM_OFF")
        attrs = s.extra_state_attributes
        assert attrs == {"alarm_state": "ALARM_OFF"}

    def test_extra_state_attributes_alarm_on(self):
        s = _sds_sensor("ALARM_ON")
        attrs = s.extra_state_attributes
        assert attrs == {"alarm_state": "ALARM_ON"}

    def test_extra_state_attributes_alarm_muted(self):
        s = _sds_sensor("ALARM_MUTED")
        attrs = s.extra_state_attributes
        assert attrs == {"alarm_state": "ALARM_MUTED"}

    def test_extra_state_attributes_key_is_alarm_state(self):
        """The dict must contain exactly the 'alarm_state' key."""
        s = _sds_sensor("ALARM_OFF")
        assert "alarm_state" in s.extra_state_attributes
        assert len(s.extra_state_attributes) == 1


# ---------------------------------------------------------------------------
# SmokeDetectionSystemSensor — __init__ sets _attr_name = None and overrides uid
# ---------------------------------------------------------------------------

class TestSmokeDetectionSystemSensorInit:
    """__init__ must set _attr_name = None and override _attr_unique_id."""

    def _make_dev(self, device_id="sds-x", root_device_id="root-x", name="SDS"):
        dev = _base_device(device_id=device_id, name=name, root_device_id=root_device_id)
        dev.alarm = SHCSmokeDetectionSystem.SurveillanceAlarmService.State.ALARM_OFF
        dev.device_services = []
        return dev

    def _make_hass(self):
        return SimpleNamespace(
            bus=SimpleNamespace(async_listen_once=lambda event, cb: None),
        )

    def test_attr_name_is_none_after_init(self):
        dev = self._make_dev(device_id="sds1", root_device_id="root1")
        hass = self._make_hass()
        with patch.object(SmokeDetectionSystemSensor, "_update_attr", lambda self: None):
            sensor = SmokeDetectionSystemSensor(device=dev, hass=hass, entry_id="E1")
        assert sensor._attr_name is None

    def test_unique_id_overrides_super(self):
        """__init__ reassigns _attr_unique_id to root_device_id + '_' + id."""
        dev = self._make_dev(device_id="sds2", root_device_id="rootR")
        hass = self._make_hass()
        with patch.object(SmokeDetectionSystemSensor, "_update_attr", lambda self: None):
            sensor = SmokeDetectionSystemSensor(device=dev, hass=hass, entry_id="E1")
        assert sensor._attr_unique_id == "rootR_sds2"

    def test_is_on_alarm_off_is_false(self):
        s = _sds_sensor("ALARM_OFF")
        assert s.is_on is False

    def test_is_on_alarm_on_is_true(self):
        s = _sds_sensor("ALARM_ON")
        assert s.is_on is True

    def test_is_on_alarm_muted_is_true(self):
        s = _sds_sensor("ALARM_MUTED")
        assert s.is_on is True


# ---------------------------------------------------------------------------
# BatterySensor — is_on logging branches
# ---------------------------------------------------------------------------

def _battery_sensor(battery_level):
    s = BatterySensor.__new__(BatterySensor)
    s._device = SimpleNamespace(batterylevel=battery_level, name="Test Device")
    return s


class TestBatterySensorLoggingPaths:
    """is_on must log at debug/warning for certain states."""

    def test_not_available_logs_debug_and_returns_false(self):
        """NOT_AVAILABLE → debug log, is_on is False (no battery state yet)."""
        s = _battery_sensor(SHCBatteryDevice.BatteryLevelService.State.NOT_AVAILABLE)
        with patch("custom_components.bosch_shc.binary_sensor.LOGGER") as mock_log:
            result = s.is_on
        mock_log.debug.assert_called_once()
        assert result is False

    def test_critical_low_logs_warning(self):
        s = _battery_sensor(SHCBatteryDevice.BatteryLevelService.State.CRITICAL_LOW)
        with patch("custom_components.bosch_shc.binary_sensor.LOGGER") as mock_log:
            result = s.is_on
        mock_log.warning.assert_called_once()
        assert result is True

    def test_critically_low_battery_logs_warning(self):
        """CRITICALLY_LOW_BATTERY → warning log + is_on True."""
        s = _battery_sensor(
            SHCBatteryDevice.BatteryLevelService.State.CRITICALLY_LOW_BATTERY
        )
        with patch("custom_components.bosch_shc.binary_sensor.LOGGER") as mock_log:
            result = s.is_on
        mock_log.warning.assert_called_once()
        assert result is True

    def test_low_battery_logs_warning(self):
        s = _battery_sensor(SHCBatteryDevice.BatteryLevelService.State.LOW_BATTERY)
        with patch("custom_components.bosch_shc.binary_sensor.LOGGER") as mock_log:
            result = s.is_on
        mock_log.warning.assert_called_once()
        assert result is True

    def test_ok_logs_nothing(self):
        s = _battery_sensor(SHCBatteryDevice.BatteryLevelService.State.OK)
        with patch("custom_components.bosch_shc.binary_sensor.LOGGER") as mock_log:
            result = s.is_on
        mock_log.debug.assert_not_called()
        mock_log.warning.assert_not_called()
        assert result is False

    def test_critically_low_battery_is_on(self):
        """CRITICALLY_LOW_BATTERY is not OK → is_on True (no special logging path)."""
        s = _battery_sensor(SHCBatteryDevice.BatteryLevelService.State.CRITICALLY_LOW_BATTERY)
        assert s.is_on is True


# ---------------------------------------------------------------------------
# MotionDetectionSensor — _input_events_handler payload detail
# ---------------------------------------------------------------------------

class TestMotionDetectionSensorInputEventsPayload:
    """_input_events_handler fires via bus.async_fire with correct payload."""

    def _make_sensor(self, device_id="hdm:md:1", name="Hall Motion",
                     latestmotion="2026-06-20T08:00:00.000Z",
                     cached_device_id="ha-dev-id"):
        sensor = MotionDetectionSensor.__new__(MotionDetectionSensor)
        sensor._device = SimpleNamespace(
            id=device_id, name=name, latestmotion=latestmotion
        )
        sensor._cached_device_id = cached_device_id
        sensor._last_fired_latestmotion = None  # replay-guard initial state
        sensor.hass = MagicMock(name="hass")
        sensor.hass.loop = MagicMock(name="loop")
        sensor.hass.bus = MagicMock(name="bus")
        return sensor

    def _payload(self, sensor):
        """Return the event payload passed to bus.async_fire."""
        return sensor.hass.bus.async_fire.call_args[0][1]

    def test_uses_async_fire(self):
        sensor = self._make_sensor()
        sensor._input_events_handler()
        assert sensor.hass.bus.async_fire.called
        assert not sensor.hass.loop.call_soon_threadsafe.called

    def test_event_name_is_bosch_shc_event(self):
        sensor = self._make_sensor()
        sensor._input_events_handler()
        event_name = sensor.hass.bus.async_fire.call_args[0][0]
        assert event_name == EVENT_BOSCH_SHC

    def test_payload_event_type_is_motion(self):
        sensor = self._make_sensor()
        sensor._input_events_handler()
        assert self._payload(sensor)[ATTR_EVENT_TYPE] == "MOTION"

    def test_payload_event_subtype_is_empty_string(self):
        sensor = self._make_sensor()
        sensor._input_events_handler()
        assert self._payload(sensor)[ATTR_EVENT_SUBTYPE] == ""

    def test_payload_device_id_is_cached_device_id(self):
        sensor = self._make_sensor(cached_device_id="my-ha-device-42")
        sensor._input_events_handler()
        assert self._payload(sensor)[ATTR_DEVICE_ID] == "my-ha-device-42"

    def test_payload_id_is_device_id(self):
        sensor = self._make_sensor(device_id="hdm:md:99")
        sensor._input_events_handler()
        assert self._payload(sensor)[ATTR_ID] == "hdm:md:99"

    def test_payload_name_is_device_name(self):
        sensor = self._make_sensor(name="Garden Motion")
        sensor._input_events_handler()
        assert self._payload(sensor)[ATTR_NAME] == "Garden Motion"

    def test_payload_last_time_triggered(self):
        ts = "2026-06-01T10:30:00.000Z"
        sensor = self._make_sensor(latestmotion=ts)
        sensor._input_events_handler()
        assert self._payload(sensor)[ATTR_LAST_TIME_TRIGGERED] == ts


# ---------------------------------------------------------------------------
# OccupancyDetectionSensor — __init__ via real call
# ---------------------------------------------------------------------------

class TestOccupancyDetectionSensorRealInit:
    """OccupancyDetectionSensor.__init__ sets _attr_name and _attr_unique_id."""

    def _make_dev(self, device_id="hdm:md2:1", root_device_id="root-md2",
                  name="Motion Detector II"):
        dev = _base_device(device_id=device_id, name=name, root_device_id=root_device_id)
        dev.occupied = False
        dev.last_occupancy_change_time = "2026-06-20T12:00:00.000Z"
        return dev

    def test_attr_name_is_occupancy(self):
        dev = self._make_dev()
        with patch.object(OccupancyDetectionSensor, "_update_attr", lambda self: None):
            sensor = OccupancyDetectionSensor(device=dev, entry_id="E1")
        assert sensor._attr_name == "Occupancy"

    def test_attr_unique_id_format(self):
        dev = self._make_dev(device_id="devY", root_device_id="rootX")
        with patch.object(OccupancyDetectionSensor, "_update_attr", lambda self: None):
            sensor = OccupancyDetectionSensor(device=dev, entry_id="E1")
        assert sensor._attr_unique_id == "rootX_devY_occupancy"

    def test_device_stored(self):
        dev = self._make_dev()
        with patch.object(OccupancyDetectionSensor, "_update_attr", lambda self: None):
            sensor = OccupancyDetectionSensor(device=dev, entry_id="E1")
        assert sensor._device is dev

    def test_is_on_occupied_true(self):
        dev = self._make_dev()
        with patch.object(OccupancyDetectionSensor, "_update_attr", lambda self: None):
            sensor = OccupancyDetectionSensor(device=dev, entry_id="E1")
        sensor._device.occupied = True
        assert sensor.is_on is True

    def test_is_on_not_occupied_false(self):
        dev = self._make_dev()
        with patch.object(OccupancyDetectionSensor, "_update_attr", lambda self: None):
            sensor = OccupancyDetectionSensor(device=dev, entry_id="E1")
        sensor._device.occupied = False
        assert sensor.is_on is False

    def test_extra_state_attributes_key(self):
        dev = self._make_dev()
        with patch.object(OccupancyDetectionSensor, "_update_attr", lambda self: None):
            sensor = OccupancyDetectionSensor(device=dev, entry_id="E1")
        attrs = sensor.extra_state_attributes
        assert "last_occupancy_change" in attrs
        assert attrs["last_occupancy_change"] == "2026-06-20T12:00:00.000Z"


# ---------------------------------------------------------------------------
# ShutterContactVibrationSensor — __init__ name and unique_id
# ---------------------------------------------------------------------------

class TestShutterContactVibrationSensorInit:
    """__init__ sets _attr_name and _attr_unique_id, ignoring device.name."""

    def _make_dev(self, device_id="sc-vib", root_device_id="root-vib", name="Fenster"):
        dev = _base_device(device_id=device_id, name=name, root_device_id=root_device_id)
        dev.vibrationsensor = SHCShutterContact2Plus.VibrationSensorService.State.NO_VIBRATION
        return dev

    def test_attr_name_is_vibration_literal(self):
        dev = self._make_dev(name="Any Name")
        with patch.object(ShutterContactVibrationSensor, "_update_attr", lambda self: None):
            sensor = ShutterContactVibrationSensor(device=dev, entry_id="E1")
        assert sensor._attr_name == "Vibration"

    def test_attr_unique_id_has_vibration_suffix(self):
        dev = self._make_dev(device_id="sc1", root_device_id="root1")
        with patch.object(ShutterContactVibrationSensor, "_update_attr", lambda self: None):
            sensor = ShutterContactVibrationSensor(device=dev, entry_id="E1")
        assert sensor._attr_unique_id == "root1_sc1_vibration"

    def test_different_device_ids_produce_different_uids(self):
        dev1 = self._make_dev(device_id="sc-a", root_device_id="root-a")
        dev2 = self._make_dev(device_id="sc-b", root_device_id="root-b")
        with patch.object(ShutterContactVibrationSensor, "_update_attr", lambda self: None):
            s1 = ShutterContactVibrationSensor(device=dev1, entry_id="E1")
            s2 = ShutterContactVibrationSensor(device=dev2, entry_id="E1")
        assert s1._attr_unique_id != s2._attr_unique_id


# ---------------------------------------------------------------------------
# SmokeDetectorSensor — is_on boundary: SECONDARY_ALARM true, INTRUSION false
# ---------------------------------------------------------------------------

class TestSmokeDetectorSensorIsOnBoundary:
    """Additional is_on boundary checks around the SECONDARY_ALARM / INTRUSION_ALARM split."""

    def _sensor(self, alarm_state):
        s = SmokeDetectorSensor.__new__(SmokeDetectorSensor)
        s._device = SimpleNamespace(
            alarmstate=alarm_state,
            smokedetectorcheck_state=SHCSmokeDetector.SmokeDetectorCheckService.State.NONE,
        )
        return s

    def test_secondary_alarm_is_smoke(self):
        s = self._sensor(SHCSmokeDetector.AlarmService.State.SECONDARY_ALARM)
        assert s.is_on is True

    def test_primary_alarm_is_smoke(self):
        s = self._sensor(SHCSmokeDetector.AlarmService.State.PRIMARY_ALARM)
        assert s.is_on is True

    def test_intrusion_alarm_is_not_smoke(self):
        s = self._sensor(SHCSmokeDetector.AlarmService.State.INTRUSION_ALARM)
        assert s.is_on is False

    def test_idle_off_is_not_smoke(self):
        s = self._sensor(SHCSmokeDetector.AlarmService.State.IDLE_OFF)
        assert s.is_on is False


# ---------------------------------------------------------------------------
# WaterLeakageDetectorSensor — extra_state_attributes when leakage is detected
# ---------------------------------------------------------------------------

class TestWaterLeakageDetectorSensorLeakageDetectedAttributes:
    """extra_state_attributes must still return push/acoustic names even during leak."""

    _WL_DETECTED = SHCWaterLeakageSensor.WaterLeakageSensorService.State.LEAKAGE_DETECTED
    _ENABLED = SHCWaterLeakageSensor.WaterLeakageSensorTiltService.State.ENABLED
    _DISABLED = SHCWaterLeakageSensor.WaterLeakageSensorTiltService.State.DISABLED

    def _sensor(self, leakage, push, acoustic):
        s = WaterLeakageDetectorSensor.__new__(WaterLeakageDetectorSensor)
        s._device = SimpleNamespace(
            leakage_state=leakage,
            push_notification_state=push,
            acoustic_signal_state=acoustic,
        )
        return s

    def test_leakage_detected_push_enabled(self):
        s = self._sensor(self._WL_DETECTED, self._ENABLED, self._ENABLED)
        attrs = s.extra_state_attributes
        assert attrs["push_notification_state"] == "ENABLED"
        assert attrs["acoustic_signal_state"] == "ENABLED"

    def test_leakage_detected_push_disabled(self):
        s = self._sensor(self._WL_DETECTED, self._DISABLED, self._DISABLED)
        attrs = s.extra_state_attributes
        assert attrs["push_notification_state"] == "DISABLED"
        assert attrs["acoustic_signal_state"] == "DISABLED"

    def test_is_on_when_leakage_detected(self):
        s = self._sensor(self._WL_DETECTED, self._ENABLED, self._ENABLED)
        assert s.is_on is True
