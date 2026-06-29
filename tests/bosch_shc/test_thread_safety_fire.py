"""Unit tests for event dispatching in __init__.py and binary_sensor.py.

Verifies that _input_events_handler and _scenario_trigger call
hass.bus.async_fire directly (callbacks fire on the event loop; no
call_soon_threadsafe marshalling needed).

Pattern: __new__ bypass + SimpleNamespace device + MagicMock hass.
No HA harness, no async_setup_entry.

Run with:
  PYTHONPATH="<boschshc-hass>:<boschshcpy>" PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
    python3 -m pytest tests/bosch_shc/test_thread_safety_fire.py -q -o addopts=""
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from custom_components.bosch_shc.__init__ import SwitchDeviceEventListener
from custom_components.bosch_shc.binary_sensor import (
    MotionDetectionSensor,
    SmokeDetectionSystemSensor,
    SmokeDetectorSensor,
)
from custom_components.bosch_shc.const import (
    ATTR_EVENT_SUBTYPE,
    ATTR_EVENT_TYPE,
    EVENT_BOSCH_SHC,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_hass():
    """Minimal hass mock with a tracked loop."""
    hass = MagicMock(name="hass")
    hass.loop = MagicMock(name="loop")
    hass.bus = MagicMock(name="bus")
    return hass


def _make_switch_listener(eventtype_name="PRESS_SHORT", keyname_name="UPPER_BUTTON",
                          eventtimestamp=1234):
    """Build a SwitchDeviceEventListener without going through async_setup."""
    listener = SwitchDeviceEventListener.__new__(SwitchDeviceEventListener)
    listener.hass = _make_hass()
    listener.device_id = "ha-device-id-1"
    listener._device = SimpleNamespace(
        id="hdm:switch:1",
        name="Test Switch",
        eventtype=SimpleNamespace(name=eventtype_name),
        keyname=SimpleNamespace(name=keyname_name),
        eventtimestamp=eventtimestamp,
    )
    return listener


# ---------------------------------------------------------------------------
# SwitchDeviceEventListener
# ---------------------------------------------------------------------------

class TestSwitchListenerThreadSafe:
    def test_supported_event_uses_async_fire(self):
        """PRESS_SHORT must call bus.async_fire directly (async session fires on loop)."""
        listener = _make_switch_listener(eventtype_name="PRESS_SHORT")
        listener._input_events_handler()

        # bus.async_fire must have been called
        assert listener.hass.bus.async_fire.called
        # call_soon_threadsafe must NOT be used (async session, no marshalling needed)
        assert not listener.hass.loop.call_soon_threadsafe.called

    def test_async_fire_passes_correct_event_type(self):
        """bus.async_fire must be called with EVENT_BOSCH_SHC as the event name."""
        listener = _make_switch_listener(eventtype_name="PRESS_LONG")
        listener._input_events_handler()

        assert listener.hass.bus.async_fire.call_args[0][0] == EVENT_BOSCH_SHC

    def test_none_eventtype_does_not_fire(self):
        """None eventtype must short-circuit before any async_fire."""
        listener = _make_switch_listener()
        listener._device.eventtype = None
        listener._input_events_handler()

        assert not listener.hass.loop.call_soon_threadsafe.called
        assert not listener.hass.bus.async_fire.called

    def test_unsupported_event_logs_warning_not_fire(self):
        """Unsupported event types must not call async_fire."""
        listener = _make_switch_listener(eventtype_name="SWITCH_ON")
        listener._input_events_handler()

        assert not listener.hass.loop.call_soon_threadsafe.called
        assert not listener.hass.bus.async_fire.called


# ---------------------------------------------------------------------------
# MotionDetectionSensor
# ---------------------------------------------------------------------------

class TestMotionSensorThreadSafe:
    def _make_sensor(self):
        sensor = MotionDetectionSensor.__new__(MotionDetectionSensor)
        sensor.hass = _make_hass()
        sensor._cached_device_id = "ha-device-motion-1"
        sensor._last_fired_latestmotion = None  # replay-guard initial state
        sensor._device = SimpleNamespace(
            id="hdm:motion:1",
            name="Motion Sensor",
            latestmotion="2026-06-20T10:00:00.000Z",
        )
        return sensor

    def test_uses_async_fire_not_call_soon_threadsafe(self):
        sensor = self._make_sensor()
        sensor._input_events_handler()

        assert sensor.hass.bus.async_fire.called
        assert not sensor.hass.loop.call_soon_threadsafe.called

    def test_event_type_is_motion(self):
        sensor = self._make_sensor()
        sensor._input_events_handler()

        payload = sensor.hass.bus.async_fire.call_args[0][1]
        assert payload[ATTR_EVENT_TYPE] == "MOTION"


# ---------------------------------------------------------------------------
# SmokeDetectorSensor
# ---------------------------------------------------------------------------

class TestSmokeDetectorSensorThreadSafe:
    def _make_sensor(self):
        sensor = SmokeDetectorSensor.__new__(SmokeDetectorSensor)
        sensor._hass = _make_hass()
        sensor._cached_device_id = "ha-device-smoke-1"
        sensor._last_fired_alarmstate = None  # replay-guard initial state
        sensor._device = SimpleNamespace(
            id="hdm:smoke:1",
            name="Smoke Detector",
            alarmstate=SimpleNamespace(name="PRIMARY_ALARM"),
        )
        return sensor

    def test_uses_async_fire_not_call_soon_threadsafe(self):
        sensor = self._make_sensor()
        sensor._input_events_handler()

        assert sensor._hass.bus.async_fire.called
        assert not sensor._hass.loop.call_soon_threadsafe.called

    def test_event_subtype_is_alarm_state(self):
        sensor = self._make_sensor()
        sensor._input_events_handler()

        payload = sensor._hass.bus.async_fire.call_args[0][1]
        assert payload[ATTR_EVENT_SUBTYPE] == "PRIMARY_ALARM"


# ---------------------------------------------------------------------------
# SmokeDetectionSystemSensor
# ---------------------------------------------------------------------------

class TestSmokeDetectionSystemSensorThreadSafe:
    def _make_sensor(self):
        sensor = SmokeDetectionSystemSensor.__new__(SmokeDetectionSystemSensor)
        sensor._hass = _make_hass()
        sensor._cached_device_id = "ha-device-smokedsys-1"
        sensor._last_fired_alarm = None  # replay-guard initial state
        sensor._device = SimpleNamespace(
            id="hdm:smokedsys:1",
            name="Smoke Detection System",
            alarm=SimpleNamespace(name="ALARM_ON"),
        )
        return sensor

    def test_uses_async_fire_not_call_soon_threadsafe(self):
        sensor = self._make_sensor()
        sensor._input_events_handler()

        assert sensor._hass.bus.async_fire.called
        assert not sensor._hass.loop.call_soon_threadsafe.called

    def test_event_subtype_is_alarm_name(self):
        sensor = self._make_sensor()
        sensor._input_events_handler()

        payload = sensor._hass.bus.async_fire.call_args[0][1]
        assert payload[ATTR_EVENT_SUBTYPE] == "ALARM_ON"
