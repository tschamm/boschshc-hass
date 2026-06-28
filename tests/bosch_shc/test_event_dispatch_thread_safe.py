"""Unit tests for event.py _event_callback dispatch behavior.

Verifies that all EventEntity subclasses call _dispatch_event directly
(no call_soon_threadsafe — callbacks fire on the event loop already),
and that _trigger_event is called through _dispatch_event.

Pattern: __new__ bypass + MagicMock hass. No HA harness.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from custom_components.bosch_shc.event import (
    UniversalSwitchEvent,
    MotionDetectorEvent,
    SmokeDetectionSystemEvent,
    SmokeDetectorEvent,
)


def _make_hass():
    hass = MagicMock(name="hass")
    hass.loop = MagicMock(name="loop")
    return hass


# ---------------------------------------------------------------------------
# UniversalSwitchEvent
# ---------------------------------------------------------------------------

class TestUniversalSwitchEventDispatch:
    def _make_entity(self):
        entity = UniversalSwitchEvent.__new__(UniversalSwitchEvent)
        entity.hass = _make_hass()
        entity._device = SimpleNamespace(
            name="Test Switch",
            id="sw-1",
            root_device_id="root-1",
            eventtype=SimpleNamespace(name="PRESS_SHORT"),
            eventtimestamp=1000,
        )
        entity._key_id = "UPPER_BUTTON"
        entity._last_fired_timestamp = -1
        entity.entity_id = "event.test_switch_button_upper_button"
        entity._attr_unique_id = "root-1_sw-1_UPPER_BUTTON"
        entity._trigger_event = MagicMock()
        entity.schedule_update_ha_state = MagicMock()
        return entity

    def test_event_callback_calls_dispatch_directly(self):
        entity = self._make_entity()
        entity._event_callback()

        assert not entity.hass.loop.call_soon_threadsafe.called
        entity._trigger_event.assert_called_once()

    def test_dispatch_event_calls_trigger_event(self):
        entity = self._make_entity()
        entity._dispatch_event("PRESS_SHORT", {"ATTR_EVENT_TYPE": "PRESS_SHORT"})
        entity._trigger_event.assert_called_once()

    def test_dispatch_event_calls_schedule_update(self):
        entity = self._make_entity()
        entity._dispatch_event("PRESS_SHORT", {"ATTR_EVENT_TYPE": "PRESS_SHORT"})
        entity.schedule_update_ha_state.assert_called_once()

    def test_dispatch_event_value_error_logs_warning(self):
        entity = self._make_entity()
        entity._trigger_event.side_effect = ValueError("bad event type")
        with patch("custom_components.bosch_shc.event.LOGGER") as mock_log:
            entity._dispatch_event("PRESS_SHORT", {})
            mock_log.warning.assert_called_once()
        entity.schedule_update_ha_state.assert_not_called()


# ---------------------------------------------------------------------------
# MotionDetectorEvent
# ---------------------------------------------------------------------------

class TestMotionDetectorEventDispatch:
    def _make_entity(self):
        entity = MotionDetectorEvent.__new__(MotionDetectorEvent)
        entity.hass = _make_hass()
        entity._device = SimpleNamespace(
            name="Motion",
            id="md-1",
            root_device_id="root-1",
            latestmotion="2026-06-20T10:00:00.000Z",
            manufacturer="Bosch",
            device_model="MD",
            status="AVAILABLE",
            deleted=False,
        )
        # device_id is a read-only property derived from _device.id — no direct assign
        entity._attr_unique_id = "root-1_md-1"
        entity._trigger_event = MagicMock()
        entity.schedule_update_ha_state = MagicMock()
        entity._last_fired_timestamp = ""  # dedup guard — empty so callback fires
        return entity

    def test_event_callback_calls_dispatch_directly(self):
        entity = self._make_entity()
        entity._event_callback()
        assert not entity.hass.loop.call_soon_threadsafe.called
        entity._trigger_event.assert_called_once()

    def test_dispatch_calls_trigger_and_schedule(self):
        entity = self._make_entity()
        entity._dispatch_event("MOTION", {})
        entity._trigger_event.assert_called_once()
        entity.schedule_update_ha_state.assert_called_once()


# ---------------------------------------------------------------------------
# SmokeDetectionSystemEvent
# ---------------------------------------------------------------------------

class TestSmokeDetectionSystemEventDispatch:
    def _make_entity(self):
        entity = SmokeDetectionSystemEvent.__new__(SmokeDetectionSystemEvent)
        entity.hass = _make_hass()
        entity._device = SimpleNamespace(
            name="Smoke System",
            id="ss-1",
            root_device_id="root-1",
            alarm=SimpleNamespace(name="ALARM_ON"),
            manufacturer="Bosch",
            device_model="SDS",
            status="AVAILABLE",
            deleted=False,
        )
        entity._attr_unique_id = "root-1_ss-1"
        entity._trigger_event = MagicMock()
        entity.schedule_update_ha_state = MagicMock()
        return entity

    def test_event_callback_calls_dispatch_directly(self):
        entity = self._make_entity()
        entity._event_callback()
        assert not entity.hass.loop.call_soon_threadsafe.called
        entity._trigger_event.assert_called_once()


# ---------------------------------------------------------------------------
# SmokeDetectorEvent
# ---------------------------------------------------------------------------

class TestSmokeDetectorEventDispatch:
    def _make_entity(self):
        entity = SmokeDetectorEvent.__new__(SmokeDetectorEvent)
        entity.hass = _make_hass()
        entity._device = SimpleNamespace(
            name="Smoke Detector",
            id="sd-1",
            root_device_id="root-1",
            alarmstate=SimpleNamespace(name="PRIMARY_ALARM"),
            manufacturer="Bosch",
            device_model="SD",
            status="AVAILABLE",
            deleted=False,
        )
        entity._attr_unique_id = "root-1_sd-1"
        entity._trigger_event = MagicMock()
        entity.schedule_update_ha_state = MagicMock()
        return entity

    def test_event_callback_calls_dispatch_directly(self):
        entity = self._make_entity()
        entity._event_callback()
        assert not entity.hass.loop.call_soon_threadsafe.called
        entity._trigger_event.assert_called_once()
