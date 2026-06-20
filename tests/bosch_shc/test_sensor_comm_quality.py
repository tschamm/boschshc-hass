"""Unit tests for CommunicationQualitySensor.native_value ValueError guard.

Verifies that unknown communicationquality values return None and log a warning
instead of propagating ValueError.

Pattern: __new__ bypass + SimpleNamespace device. No HA harness.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from custom_components.bosch_shc.sensor import CommunicationQualitySensor


def _make_sensor(quality_obj):
    sensor = CommunicationQualitySensor.__new__(CommunicationQualitySensor)
    sensor._device = SimpleNamespace(
        id="dev-1",
        root_device_id="root-1",
        name="Compact Plug",
        communicationquality=quality_obj,
    )
    sensor._attr_unique_id = "root-1_dev-1_communicationquality"
    sensor._attr_name = "Communication Quality"
    return sensor


class _GoodQuality:
    """Valid quality enum-like — .name works fine."""
    @property
    def name(self):
        return "GOOD"


class _BadQuality:
    """Unknown quality — .name raises ValueError."""
    @property
    def name(self):
        raise ValueError("Unknown quality value 99")


class _NoneQuality:
    """Quality object whose .name raises AttributeError (missing service)."""
    @property
    def name(self):
        raise AttributeError("NoneType has no attribute 'name'")


class TestCommunicationQualitySensor:
    def test_valid_quality_returns_name(self):
        sensor = _make_sensor(_GoodQuality())
        assert sensor.native_value == "GOOD"

    def test_value_error_returns_none_and_logs(self):
        sensor = _make_sensor(_BadQuality())
        with patch("custom_components.bosch_shc.sensor.LOGGER") as mock_log:
            result = sensor.native_value
        assert result is None
        mock_log.warning.assert_called_once()

    def test_attribute_error_returns_none_and_logs(self):
        sensor = _make_sensor(_NoneQuality())
        with patch("custom_components.bosch_shc.sensor.LOGGER") as mock_log:
            result = sensor.native_value
        assert result is None
        mock_log.warning.assert_called_once()
