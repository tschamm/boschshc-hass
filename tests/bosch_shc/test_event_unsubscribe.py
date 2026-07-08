"""Regression tests: event.py entities must unregister their register_event
callbacks on removal, not just leave them registered on the service forever.

boschshcpy's register_event(event, callback) has no matching unregister_event
(only subscribe_callback/unsubscribe_callback, used by SHCEntity's own
async_added_to_hass/async_will_remove_from_hass, are paired). Without an
async_will_remove_from_hass override, a removed/reloaded entity's stale
_event_callback closure stays registered in the service's private
_event_callbacks dict and keeps firing (and referencing a torn-down entity)
forever.

Pattern: __new__ bypass + fake service/session objects. No HA harness.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from boschshcpy.services_impl import KeypadService

from custom_components.bosch_shc.event import (
    LightControlButtonEvent,
    MotionDetectorEvent,
    SHCScenarioEvent,
    SmokeDetectionSystemEvent,
    SmokeDetectorEvent,
    UniversalSwitchEvent,
)

_SHC_ENTITY_WILL_REMOVE = (
    "custom_components.bosch_shc.event.SHCEntity.async_will_remove_from_hass"
)


class FakeService:
    """Minimal stand-in for a boschshcpy SHCDeviceService."""

    def __init__(self, service_id):
        self.id = service_id
        self._callbacks = {}
        self._event_callbacks = {}

    def subscribe_callback(self, entity_id, callback):
        self._callbacks[entity_id] = callback

    def unsubscribe_callback(self, entity_id):
        self._callbacks.pop(entity_id, None)

    def register_event(self, event, callback):
        self._event_callbacks[event] = callback


class TestUniversalSwitchEventUnsubscribe:
    def test_unregisters_keypad_event_callback(self):
        keypad = FakeService("Keypad")
        entity = UniversalSwitchEvent.__new__(UniversalSwitchEvent)
        entity._device = SimpleNamespace(device_services=[keypad])
        entity._key_id = "UPPER_BUTTON"
        keypad.register_event(entity._key_id, entity._event_callback)
        assert "UPPER_BUTTON" in keypad._event_callbacks

        with patch(_SHC_ENTITY_WILL_REMOVE, new=AsyncMock(return_value=None)):
            asyncio.run(entity.async_will_remove_from_hass())

        assert "UPPER_BUTTON" not in keypad._event_callbacks

    def test_unregister_is_a_noop_when_never_registered(self):
        keypad = FakeService("Keypad")
        entity = UniversalSwitchEvent.__new__(UniversalSwitchEvent)
        entity._device = SimpleNamespace(device_services=[keypad])
        entity._key_id = "LOWER_BUTTON"

        with patch(_SHC_ENTITY_WILL_REMOVE, new=AsyncMock(return_value=None)):
            asyncio.run(entity.async_will_remove_from_hass())  # must not raise


class TestLightControlButtonEventUnsubscribe:
    def test_unregisters_all_key_state_callbacks(self):
        keypad = FakeService("Keypad")
        keypad.KeyState = KeypadService.KeyState
        entity = LightControlButtonEvent.__new__(LightControlButtonEvent)
        entity._device = SimpleNamespace(device_services=[keypad])
        for key_state in keypad.KeyState:
            keypad.register_event(key_state.value, entity._event_callback)
        assert len(keypad._event_callbacks) == len(list(keypad.KeyState))

        with patch(_SHC_ENTITY_WILL_REMOVE, new=AsyncMock(return_value=None)):
            asyncio.run(entity.async_will_remove_from_hass())

        assert keypad._event_callbacks == {}


class TestMotionDetectorEventUnsubscribe:
    def test_unregisters_latestmotion_event_callback(self):
        latest_motion = FakeService("LatestMotion")
        entity = MotionDetectorEvent.__new__(MotionDetectorEvent)
        entity._device = SimpleNamespace(
            id="hdm:motion:d1", device_services=[latest_motion]
        )
        latest_motion.register_event("hdm:motion:d1", entity._event_callback)

        with patch(_SHC_ENTITY_WILL_REMOVE, new=AsyncMock(return_value=None)):
            asyncio.run(entity.async_will_remove_from_hass())

        assert "hdm:motion:d1" not in latest_motion._event_callbacks


class TestSmokeDetectionSystemEventUnsubscribe:
    def test_unregisters_surveillancealarm_event_callback(self):
        alarm_service = FakeService("SurveillanceAlarm")
        entity = SmokeDetectionSystemEvent.__new__(SmokeDetectionSystemEvent)
        entity._device = SimpleNamespace(
            id="hdm:smoke:sys:1", device_services=[alarm_service]
        )
        alarm_service.register_event("hdm:smoke:sys:1", entity._event_callback)

        with patch(_SHC_ENTITY_WILL_REMOVE, new=AsyncMock(return_value=None)):
            asyncio.run(entity.async_will_remove_from_hass())

        assert "hdm:smoke:sys:1" not in alarm_service._event_callbacks


class TestSmokeDetectorEventUnsubscribe:
    def test_unregisters_alarm_event_callback(self):
        alarm_service = FakeService("Alarm")
        entity = SmokeDetectorEvent.__new__(SmokeDetectorEvent)
        entity._device = SimpleNamespace(
            id="hdm:smoke:d1", device_services=[alarm_service]
        )
        alarm_service.register_event("hdm:smoke:d1", entity._event_callback)

        with patch(_SHC_ENTITY_WILL_REMOVE, new=AsyncMock(return_value=None)):
            asyncio.run(entity.async_will_remove_from_hass())

        assert "hdm:smoke:d1" not in alarm_service._event_callbacks


class TestSHCScenarioEventUnsubscribe:
    def test_unsubscribes_scenario_callback(self):
        session = SimpleNamespace(unsubscribe_scenario_callback=MagicMock())
        entity = SHCScenarioEvent.__new__(SHCScenarioEvent)
        entity._session = session
        entity._scenario = SimpleNamespace(id="scn:42")

        with patch(
            "homeassistant.components.event.EventEntity.async_will_remove_from_hass",
            new=AsyncMock(return_value=None),
        ):
            asyncio.run(entity.async_will_remove_from_hass())

        session.unsubscribe_scenario_callback.assert_called_once_with("scn:42")
