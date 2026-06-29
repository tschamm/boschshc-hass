"""Regression test for SmokeDetectorSensor.is_on alarm-state mapping (issue #191).

Bug: is_on used `!= IDLE_OFF`, so INTRUSION_ALARM (set by the IDS on all smoke
detectors when a burglar alarm fires) falsely reported every detector as smoky.
Fix: is_on only returns True for PRIMARY_ALARM or SECONDARY_ALARM.
"""

from enum import Enum
from types import SimpleNamespace

from boschshcpy import AlarmService

from custom_components.bosch_shc.binary_sensor import SmokeDetectorSensor


class _FakeAlarmState(Enum):
    IDLE_OFF = "IDLE_OFF"
    INTRUSION_ALARM = "INTRUSION_ALARM"
    SECONDARY_ALARM = "SECONDARY_ALARM"
    PRIMARY_ALARM = "PRIMARY_ALARM"


def _sensor(alarm_state):
    """Build a SmokeDetectorSensor without running __init__."""
    s = SmokeDetectorSensor.__new__(SmokeDetectorSensor)
    # AlarmService.State is referenced via AlarmService.State in
    # is_on — patch _device.alarmstate with the real imported enum so the
    # identity check works correctly.

    state = AlarmService.State[alarm_state.name]
    s._device = SimpleNamespace(alarmstate=state)
    return s


def test_idle_off_is_not_smoke():
    assert _sensor(_FakeAlarmState.IDLE_OFF).is_on is False


def test_intrusion_alarm_does_not_trigger_smoke(
):
    """IDS intrusion alarm must NOT make smoke sensors report smoke (issue #191)."""
    assert _sensor(_FakeAlarmState.INTRUSION_ALARM).is_on is False


def test_primary_alarm_is_smoke():
    assert _sensor(_FakeAlarmState.PRIMARY_ALARM).is_on is True


def test_secondary_alarm_is_smoke():
    assert _sensor(_FakeAlarmState.SECONDARY_ALARM).is_on is True
