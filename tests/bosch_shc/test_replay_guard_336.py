"""Regression tests for #336 — ghost events on poll-id resubscribe.

On the ~24 h SHC poll-id resubscribe the controller re-delivers every service's
current state.  Without a replay guard the integration fires MOTION / ALARM
events for unchanged state, triggering automations with nobody home.

Three entity classes had unguarded _input_events_handler:
  - MotionDetectionSensor  (binary_sensor.py)
  - SmokeDetectorSensor    (binary_sensor.py)
  - SmokeDetectionSystemSensor (binary_sensor.py)

Each test class asserts:
  (a) first call fires exactly one event
  (b) second call with UNCHANGED state does NOT fire (replay suppression)
  (c) call with CHANGED state fires again (genuine new event)

Pattern: __new__-bypass + SimpleNamespace device + MagicMock hass.
No HA harness, no async_setup_entry.

Run with:
  PYTHONPATH="<boschshc-hass>:<boschshcpy>" PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
    python3 -m pytest tests/bosch_shc/test_replay_guard_336.py -q -o addopts=""
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from custom_components.bosch_shc.binary_sensor import (
    MotionDetectionSensor,
    SmokeDetectionSystemSensor,
    SmokeDetectorSensor,
)
from custom_components.bosch_shc.const import (
    ATTR_EVENT_SUBTYPE,
    ATTR_EVENT_TYPE,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_hass():
    """Minimal hass mock with a tracked bus.async_fire."""
    hass = MagicMock(name="hass")
    hass.loop = MagicMock(name="loop")
    hass.bus = MagicMock(name="bus")
    return hass


def _fire_count(hass):
    """Number of times bus.async_fire was called on this hass mock."""
    return hass.bus.async_fire.call_count


# ---------------------------------------------------------------------------
# MotionDetectionSensor — replay guard on latestmotion timestamp
# ---------------------------------------------------------------------------

class TestMotionReplayGuard:
    """#336: MotionDetectionSensor must suppress replayed LatestMotion snapshots."""

    def _make_sensor(self, latestmotion="2026-06-20T19:21:00.000Z"):
        sensor = MotionDetectionSensor.__new__(MotionDetectionSensor)
        sensor.hass = _make_hass()
        sensor._cached_device_id = "ha-motion-1"
        sensor._last_fired_latestmotion = None  # freshly constructed
        sensor._device = SimpleNamespace(
            id="hdm:motion:1",
            name="Motion Sensor",
            latestmotion=latestmotion,
        )
        return sensor

    def test_first_call_fires_event(self):
        """(a) First snapshot must fire exactly one event."""
        sensor = self._make_sensor("2026-06-20T19:21:00.000Z")
        sensor._input_events_handler()
        assert _fire_count(sensor.hass) == 1

    def test_first_call_event_type_is_motion(self):
        """Event fired must carry MOTION type."""
        sensor = self._make_sensor("2026-06-20T19:21:00.000Z")
        sensor._input_events_handler()
        payload = sensor.hass.bus.async_fire.call_args[0][1]
        assert payload[ATTR_EVENT_TYPE] == "MOTION"

    def test_replayed_snapshot_does_not_fire(self):
        """(b) Same latestmotion on the second call must be suppressed."""
        sensor = self._make_sensor("2026-06-20T19:21:00.000Z")
        sensor._input_events_handler()  # first call — fires
        sensor._input_events_handler()  # replay — must NOT fire again
        assert _fire_count(sensor.hass) == 1

    def test_changed_timestamp_fires_again(self):
        """(c) Advancing latestmotion (genuine new motion) must fire."""
        sensor = self._make_sensor("2026-06-20T19:21:00.000Z")
        sensor._input_events_handler()  # first — fires
        sensor._input_events_handler()  # replay — suppressed
        assert _fire_count(sensor.hass) == 1

        # Simulate new motion: advance the timestamp
        sensor._device.latestmotion = "2026-06-20T20:00:00.000Z"
        sensor._input_events_handler()
        assert _fire_count(sensor.hass) == 2

    def test_none_timestamp_suppressed(self):
        """None latestmotion means no motion yet — suppress (both cache and value are None)."""
        sensor = self._make_sensor(None)
        sensor._input_events_handler()
        # None == None → suppressed; this is correct: no motion has been recorded.
        assert _fire_count(sensor.hass) == 0

    def test_none_then_real_timestamp_fires(self):
        """Transition from None (no motion ever) to a real timestamp must fire."""
        # Cache starts at None; device also starts at None → suppressed.
        sensor = self._make_sensor(None)
        sensor._input_events_handler()  # None == None → suppressed
        assert _fire_count(sensor.hass) == 0

        # Now a real timestamp arrives → different from cached None → fire.
        sensor._device.latestmotion = "2026-06-20T20:00:00.000Z"
        sensor._input_events_handler()
        assert _fire_count(sensor.hass) == 1

    def test_cache_attr_initialized_to_none(self):
        """_last_fired_latestmotion must start as None (fresh entity)."""
        sensor = self._make_sensor()
        assert sensor._last_fired_latestmotion is None


# ---------------------------------------------------------------------------
# SmokeDetectorSensor — replay guard on alarmstate
# ---------------------------------------------------------------------------

class TestSmokeDetectorReplayGuard:
    """#336: SmokeDetectorSensor must suppress replayed Alarm state snapshots."""

    def _make_sensor(self, alarmstate_name="IDLE_OFF"):
        sensor = SmokeDetectorSensor.__new__(SmokeDetectorSensor)
        sensor._hass = _make_hass()
        sensor._cached_device_id = "ha-smoke-1"
        sensor._last_fired_alarmstate = None  # freshly constructed
        sensor._device = SimpleNamespace(
            id="hdm:smoke:1",
            name="Smoke Detector",
            alarmstate=SimpleNamespace(name=alarmstate_name),
        )
        return sensor

    def test_first_call_fires_event(self):
        """(a) First snapshot must fire exactly one event."""
        sensor = self._make_sensor("IDLE_OFF")
        sensor._input_events_handler()
        assert _fire_count(sensor._hass) == 1

    def test_first_call_event_subtype_is_alarmstate(self):
        """Event fired must carry the alarmstate name as ATTR_EVENT_SUBTYPE."""
        sensor = self._make_sensor("PRIMARY_ALARM")
        sensor._input_events_handler()
        payload = sensor._hass.bus.async_fire.call_args[0][1]
        assert payload[ATTR_EVENT_TYPE] == "ALARM"
        assert payload[ATTR_EVENT_SUBTYPE] == "PRIMARY_ALARM"

    def test_replayed_snapshot_does_not_fire(self):
        """(b) Same alarmstate on the second call must be suppressed."""
        sensor = self._make_sensor("IDLE_OFF")
        sensor._input_events_handler()  # fires
        sensor._input_events_handler()  # replay — suppressed
        assert _fire_count(sensor._hass) == 1

    def test_changed_alarmstate_fires_again(self):
        """(c) Genuine alarm transition (IDLE_OFF → PRIMARY_ALARM) must fire."""
        sensor = self._make_sensor("IDLE_OFF")
        sensor._input_events_handler()  # fires
        sensor._input_events_handler()  # replay — suppressed
        assert _fire_count(sensor._hass) == 1

        sensor._device.alarmstate = SimpleNamespace(name="PRIMARY_ALARM")
        sensor._input_events_handler()
        assert _fire_count(sensor._hass) == 2

    def test_alarm_to_idle_transition_fires(self):
        """Genuine alarm → idle transition (ALARM cleared) must fire."""
        sensor = self._make_sensor("PRIMARY_ALARM")
        sensor._input_events_handler()  # fires — PRIMARY_ALARM
        sensor._device.alarmstate = SimpleNamespace(name="IDLE_OFF")
        sensor._input_events_handler()  # genuine change — fires
        assert _fire_count(sensor._hass) == 2

    def test_intrusion_alarm_then_idle_fires_twice(self):
        """INTRUSION_ALARM → IDLE_OFF counts as a state change and must fire."""
        sensor = self._make_sensor("INTRUSION_ALARM")
        sensor._input_events_handler()  # first — fires
        sensor._device.alarmstate = SimpleNamespace(name="IDLE_OFF")
        sensor._input_events_handler()  # change — fires
        assert _fire_count(sensor._hass) == 2

    def test_cache_attr_initialized_to_none(self):
        """_last_fired_alarmstate must start as None (fresh entity)."""
        sensor = self._make_sensor()
        assert sensor._last_fired_alarmstate is None


# ---------------------------------------------------------------------------
# SmokeDetectionSystemSensor — replay guard on SurveillanceAlarm state
# ---------------------------------------------------------------------------

class TestSmokeDetectionSystemReplayGuard:
    """#336: SmokeDetectionSystemSensor must suppress replayed SurveillanceAlarm snapshots."""

    def _make_sensor(self, alarm_name="ALARM_OFF"):
        sensor = SmokeDetectionSystemSensor.__new__(SmokeDetectionSystemSensor)
        sensor._hass = _make_hass()
        sensor._cached_device_id = "ha-sds-1"
        sensor._last_fired_alarm = None  # freshly constructed
        sensor._device = SimpleNamespace(
            id="hdm:smokedetectionsystem:1",
            name="Smoke Detection System",
            alarm=SimpleNamespace(name=alarm_name),
        )
        return sensor

    def test_first_call_fires_event(self):
        """(a) First snapshot must fire exactly one event."""
        sensor = self._make_sensor("ALARM_OFF")
        sensor._input_events_handler()
        assert _fire_count(sensor._hass) == 1

    def test_first_call_event_subtype_is_alarm_name(self):
        """Event fired must carry the alarm name as ATTR_EVENT_SUBTYPE."""
        sensor = self._make_sensor("ALARM_ON")
        sensor._input_events_handler()
        payload = sensor._hass.bus.async_fire.call_args[0][1]
        assert payload[ATTR_EVENT_TYPE] == "ALARM"
        assert payload[ATTR_EVENT_SUBTYPE] == "ALARM_ON"

    def test_replayed_snapshot_does_not_fire(self):
        """(b) Same alarm state on the second call must be suppressed."""
        sensor = self._make_sensor("ALARM_OFF")
        sensor._input_events_handler()  # fires
        sensor._input_events_handler()  # replay — suppressed
        assert _fire_count(sensor._hass) == 1

    def test_changed_alarm_state_fires_again(self):
        """(c) Genuine ALARM_OFF → ALARM_ON transition must fire."""
        sensor = self._make_sensor("ALARM_OFF")
        sensor._input_events_handler()  # fires
        sensor._input_events_handler()  # replay — suppressed
        assert _fire_count(sensor._hass) == 1

        sensor._device.alarm = SimpleNamespace(name="ALARM_ON")
        sensor._input_events_handler()
        assert _fire_count(sensor._hass) == 2

    def test_alarm_on_then_muted_fires(self):
        """ALARM_ON → ALARM_MUTED is a genuine state change — must fire."""
        sensor = self._make_sensor("ALARM_ON")
        sensor._input_events_handler()  # fires
        sensor._device.alarm = SimpleNamespace(name="ALARM_MUTED")
        sensor._input_events_handler()  # genuine change — fires
        assert _fire_count(sensor._hass) == 2

    def test_alarm_muted_then_off_fires(self):
        """ALARM_MUTED → ALARM_OFF (alarm cleared) must fire."""
        sensor = self._make_sensor("ALARM_MUTED")
        sensor._input_events_handler()  # fires
        sensor._device.alarm = SimpleNamespace(name="ALARM_OFF")
        sensor._input_events_handler()  # genuine change — fires
        assert _fire_count(sensor._hass) == 2

    def test_cache_attr_initialized_to_none(self):
        """_last_fired_alarm must start as None (fresh entity)."""
        sensor = self._make_sensor()
        assert sensor._last_fired_alarm is None

    def test_resubscribe_replay_idle_off_suppressed(self):
        """Exact scenario from #336: ALARM_OFF re-delivered after 24h resubscribe."""
        sensor = self._make_sensor("ALARM_OFF")
        sensor._input_events_handler()  # initial snapshot — fires once

        # Simulate 24h resubscribe: same ALARM_OFF re-delivered
        sensor._input_events_handler()  # must be suppressed
        sensor._input_events_handler()  # must be suppressed (belt and suspenders)
        assert _fire_count(sensor._hass) == 1
