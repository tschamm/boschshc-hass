"""Regression test for MotionDetectionSensor.is_on timestamp handling.

The latest-motion timestamp is parsed with a trailing literal "Z", which
yields a NAIVE datetime. Subtracting it from datetime.now(timezone.utc) (aware)
raised TypeError on every motion poll — and the surrounding `except` only
caught ValueError, so the motion binary_sensor errored. The timestamp must be
marked UTC-aware.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from custom_components.bosch_shc.binary_sensor import MotionDetectionSensor


def _sensor(latestmotion):
    s = MotionDetectionSensor.__new__(MotionDetectionSensor)
    s._device = SimpleNamespace(latestmotion=latestmotion)
    return s


def _fmt(dt):
    # Bosch format: ...%fZ (naive string, UTC)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def test_recent_motion_is_on_no_typeerror():
    recent = datetime.now(timezone.utc) - timedelta(seconds=10)
    assert _sensor(_fmt(recent)).is_on is True


def test_old_motion_is_off():
    old = datetime.now(timezone.utc) - timedelta(minutes=10)
    assert _sensor(_fmt(old)).is_on is False


def test_none_timestamp_returns_false_not_crash():
    assert _sensor(None).is_on is False


def test_garbage_timestamp_returns_false():
    assert _sensor("not-a-date").is_on is False
