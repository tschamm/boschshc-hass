"""Isolation-safe unit tests for event.py.

Covers the event entity classes not already tested by
test_event_universal_switch.py / test_event_slugify.py:
  - UniversalSwitchEvent: __init__ wiring, Keypad subscribe, ValueError branch,
    event attribute payload
  - MotionDetectorEvent:  __init__, async_added_to_hass subscribe, _event_callback
  - SmokeDetectionSystemEvent: same
  - SmokeDetectorEvent: same

Pattern: Cls.__new__(Cls) bypasses SHCEntity.__init__ (needs hass/registry).
Attributes are injected via SimpleNamespace / direct assignment.
async_added_to_hass is driven via asyncio.run() (Python 3.14 requires a fresh loop
per asyncio.run() call — get_event_loop() raises RuntimeError in 3.14 when no loop
is set on the thread).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from custom_components.bosch_shc.event import (
    LightControlButtonEvent,
    MotionDetectorEvent,
    SmokeDetectionSystemEvent,
    SmokeDetectorEvent,
    UniversalSwitchEvent,
)
from custom_components.bosch_shc.const import (
    ATTR_EVENT_TYPE,
    ATTR_EVENT_SUBTYPE,
    ATTR_LAST_TIME_TRIGGERED,
)
from homeassistant.const import ATTR_DEVICE_ID, ATTR_ID, ATTR_NAME


# ---------------------------------------------------------------------------
# Shared fake press-type sentinels
# ---------------------------------------------------------------------------

_PRESS_SHORT = SimpleNamespace(name="PRESS_SHORT")
_PRESS_LONG = SimpleNamespace(name="PRESS_LONG")
_PRESS_LONG_RELEASED = SimpleNamespace(name="PRESS_LONG_RELEASED")
_SWITCH_ON = SimpleNamespace(name="SWITCH_ON")

_SHC_ENTITY_ADDED = "custom_components.bosch_shc.event.SHCEntity.async_added_to_hass"


def _make_hass_sync():
    """Return a minimal hass mock whose call_soon_threadsafe executes the fn immediately.

    This allows unit tests that drive _event_callback() directly to still verify
    _trigger_event/schedule_update_ha_state without needing a real event loop.
    """
    hass = MagicMock(name="hass")

    def _sync_call(fn, *args, **kwargs):
        fn(*args, **kwargs)

    hass.loop.call_soon_threadsafe.side_effect = _sync_call
    return hass


def _make_universal_switch_entity(
    eventtype=_PRESS_SHORT,
    eventtimestamp: int = 1000,
    name: str = "Test Switch",
    device_id: str = "hdm:switch:1",
    root_device_id: str = "root:1",
    key_id: str = "UPPER_BUTTON",
    extra_services: list | None = None,
) -> UniversalSwitchEvent:
    """Build UniversalSwitchEvent bypassing SHCEntity.__init__."""
    entity = UniversalSwitchEvent.__new__(UniversalSwitchEvent)
    entity._device = SimpleNamespace(
        name=name,
        id=device_id,
        root_device_id=root_device_id,
        eventtype=eventtype,
        eventtimestamp=eventtimestamp,
        device_services=extra_services or [],
        deleted=False,
        manufacturer="Bosch",
        device_model="US",
        status="AVAILABLE",
    )
    entity._key_id = key_id
    entity._last_fired_timestamp = -1
    entity._attr_unique_id = f"{root_device_id}_{device_id}_{key_id}"
    entity.entity_id = f"event.{name.lower().replace(' ', '_')}_button_{key_id.lower()}"
    entity.hass = _make_hass_sync()
    entity._trigger_event = MagicMock()
    entity.schedule_update_ha_state = MagicMock()
    return entity


# ---------------------------------------------------------------------------
# UniversalSwitchEvent — __init__ wiring
# ---------------------------------------------------------------------------

class TestUniversalSwitchEventInit:
    """__init__ sets name, unique_id, entity_id, and _last_fired_timestamp."""

    def _make_dev(self, name="SW", device_id="hdm:sw:1", root_device_id="root:1"):
        return SimpleNamespace(
            name=name,
            id=device_id,
            root_device_id=root_device_id,
            device_services=[],
            deleted=False,
            manufacturer="Bosch",
            device_model="US",
            status="AVAILABLE",
        )

    def test_name_set_correctly(self):
        # _attr_name contains only the suffix ("Button LOWER_BUTTON").
        # With _attr_has_entity_name=True HA auto-prepends the device name at runtime.
        dev = self._make_dev(name="Living Room Switch", device_id="hdm:sw:42", root_device_id="root:x")
        entity = UniversalSwitchEvent.__new__(UniversalSwitchEvent)
        with patch.object(UniversalSwitchEvent, "_update_attr", lambda self: None):
            UniversalSwitchEvent.__init__(entity, dev, "entry1", "LOWER_BUTTON")
        assert entity._attr_name == "Button LOWER_BUTTON"

    def test_unique_id_set_correctly(self):
        dev = self._make_dev(name="SW", device_id="hdm:sw:99", root_device_id="root:r")
        entity = UniversalSwitchEvent.__new__(UniversalSwitchEvent)
        with patch.object(UniversalSwitchEvent, "_update_attr", lambda self: None):
            UniversalSwitchEvent.__init__(entity, dev, "entry1", "UPPER_BUTTON")
        assert entity._attr_unique_id == "root:r_hdm:sw:99_UPPER_BUTTON"

    def test_entity_id_slugified_lowercase(self):
        dev = self._make_dev(name="Außen Schalter", device_id="hdm:sw:1", root_device_id="root:1")
        entity = UniversalSwitchEvent.__new__(UniversalSwitchEvent)
        with patch.object(UniversalSwitchEvent, "_update_attr", lambda self: None):
            UniversalSwitchEvent.__init__(entity, dev, "entry1", "UPPER_BUTTON")
        assert entity.entity_id.startswith("event.")
        assert entity.entity_id == entity.entity_id.lower()

    def test_last_fired_timestamp_initialized_to_minus_one(self):
        dev = self._make_dev()
        entity = UniversalSwitchEvent.__new__(UniversalSwitchEvent)
        with patch.object(UniversalSwitchEvent, "_update_attr", lambda self: None):
            UniversalSwitchEvent.__init__(entity, dev, "entry1", "UPPER_BUTTON")
        assert entity._last_fired_timestamp == -1

    def test_key_id_casefolded_in_entity_id(self):
        dev = self._make_dev(name="Switch")
        entity = UniversalSwitchEvent.__new__(UniversalSwitchEvent)
        with patch.object(UniversalSwitchEvent, "_update_attr", lambda self: None):
            UniversalSwitchEvent.__init__(entity, dev, "entry1", "UPPER_BUTTON")
        assert "upper_button" in entity.entity_id

    def test_lower_button_key_id_in_name_and_uid(self):
        dev = self._make_dev(name="Hallway SW", device_id="hdm:sw:5", root_device_id="root:5")
        entity = UniversalSwitchEvent.__new__(UniversalSwitchEvent)
        with patch.object(UniversalSwitchEvent, "_update_attr", lambda self: None):
            UniversalSwitchEvent.__init__(entity, dev, "entry1", "LOWER_BUTTON")
        assert "LOWER_BUTTON" in entity._attr_name
        assert entity._attr_unique_id.endswith("_LOWER_BUTTON")


# ---------------------------------------------------------------------------
# UniversalSwitchEvent — Keypad subscribe wiring (async_added_to_hass)
# ---------------------------------------------------------------------------

class FakeKeypadService:
    id = "Keypad"

    def __init__(self):
        self.registered: dict = {}

    def register_event(self, key_id: str, callback) -> None:
        self.registered[key_id] = callback

    def subscribe_callback(self, eid, cb) -> None:
        pass


class FakeNonKeypadService:
    id = "Battery"

    def subscribe_callback(self, eid, cb) -> None:
        pass


class TestUniversalSwitchEventSubscribe:
    """async_added_to_hass must register _event_callback with the Keypad service."""

    def test_keypad_service_registers_event_for_key_id(self):
        keypad_svc = FakeKeypadService()
        entity = _make_universal_switch_entity(
            key_id="UPPER_BUTTON",
            extra_services=[keypad_svc, FakeNonKeypadService()],
        )
        entity._entry_id = "entry1"
        entity.hass = _make_hass_sync()

        async def _run():
            with patch(_SHC_ENTITY_ADDED, return_value=None):
                await UniversalSwitchEvent.async_added_to_hass(entity)

        asyncio.run(_run())
        assert "UPPER_BUTTON" in keypad_svc.registered
        assert callable(keypad_svc.registered["UPPER_BUTTON"])

    def test_non_keypad_service_not_registered(self):
        keypad_svc = FakeKeypadService()
        non_kp = FakeNonKeypadService()
        entity = _make_universal_switch_entity(
            key_id="LOWER_BUTTON",
            extra_services=[non_kp, keypad_svc],
        )
        entity._entry_id = "entry1"
        entity.hass = _make_hass_sync()

        async def _run():
            with patch(_SHC_ENTITY_ADDED, return_value=None):
                await UniversalSwitchEvent.async_added_to_hass(entity)

        asyncio.run(_run())
        assert "LOWER_BUTTON" in keypad_svc.registered
        # Battery service has no register_event attribute → correctly skipped
        assert not hasattr(non_kp, "registered")

    def test_registered_callback_fires_event(self):
        keypad_svc = FakeKeypadService()
        entity = _make_universal_switch_entity(
            eventtype=_PRESS_SHORT,
            eventtimestamp=5000,
            key_id="UPPER_BUTTON",
            extra_services=[keypad_svc],
        )
        entity._entry_id = "entry1"
        entity.hass = _make_hass_sync()

        async def _run():
            with patch(_SHC_ENTITY_ADDED, return_value=None):
                await UniversalSwitchEvent.async_added_to_hass(entity)

        asyncio.run(_run())
        keypad_svc.registered["UPPER_BUTTON"]()
        entity._trigger_event.assert_called_once()
        assert entity._trigger_event.call_args[0][0] == "PRESS_SHORT"

    def test_no_keypad_service_registers_nothing(self):
        """If device has no Keypad service, no crash."""
        entity = _make_universal_switch_entity(
            key_id="UPPER_BUTTON",
            extra_services=[FakeNonKeypadService()],
        )
        entity._entry_id = "entry1"
        entity.hass = _make_hass_sync()

        async def _run():
            with patch(_SHC_ENTITY_ADDED, return_value=None):
                await UniversalSwitchEvent.async_added_to_hass(entity)

        asyncio.run(_run())  # must not raise


# ---------------------------------------------------------------------------
# UniversalSwitchEvent — ValueError branch in _event_callback
# ---------------------------------------------------------------------------

class TestUniversalSwitchEventValueError:
    """_trigger_event raising ValueError must be caught; schedule_update not called."""

    def test_value_error_is_caught_no_schedule_update(self):
        entity = _make_universal_switch_entity(eventtype=_PRESS_SHORT, eventtimestamp=200)
        entity._trigger_event = MagicMock(side_effect=ValueError("bad type"))
        entity._event_callback()
        entity._trigger_event.assert_called_once()
        entity.schedule_update_ha_state.assert_not_called()

    def test_value_error_timestamp_still_advanced(self):
        """Even on ValueError the timestamp guard advances to prevent re-raise on replay."""
        entity = _make_universal_switch_entity(eventtype=_PRESS_SHORT, eventtimestamp=300)
        entity._trigger_event = MagicMock(side_effect=ValueError("bad"))
        entity._event_callback()
        assert entity._last_fired_timestamp == 300

    def test_press_long_value_error_does_not_propagate(self):
        entity = _make_universal_switch_entity(eventtype=_PRESS_LONG, eventtimestamp=400)
        entity._trigger_event = MagicMock(side_effect=ValueError("bad"))
        entity._event_callback()  # must not raise

    def test_press_long_released_value_error_does_not_propagate(self):
        entity = _make_universal_switch_entity(eventtype=_PRESS_LONG_RELEASED, eventtimestamp=500)
        entity._trigger_event = MagicMock(side_effect=ValueError("bad"))
        entity._event_callback()  # must not raise


# ---------------------------------------------------------------------------
# UniversalSwitchEvent — event attribute payload
# ---------------------------------------------------------------------------

class TestUniversalSwitchEventPayload:
    """_event_callback must pass the right attributes dict to _trigger_event."""

    def test_press_short_payload(self):
        entity = _make_universal_switch_entity(
            eventtype=_PRESS_SHORT, eventtimestamp=7000,
            device_id="hdm:sw:77", name="Kitchen Switch",
        )
        entity._event_callback()
        event_type, attrs = entity._trigger_event.call_args[0]
        assert event_type == "PRESS_SHORT"
        assert attrs[ATTR_EVENT_TYPE] == "PRESS_SHORT"
        assert attrs[ATTR_ID] == "hdm:sw:77"
        assert attrs[ATTR_NAME] == "Kitchen Switch"
        assert attrs[ATTR_LAST_TIME_TRIGGERED] == 7000

    def test_press_long_payload(self):
        entity = _make_universal_switch_entity(
            eventtype=_PRESS_LONG, eventtimestamp=8000,
            device_id="hdm:sw:88", name="Hallway Switch",
        )
        entity._event_callback()
        event_type, attrs = entity._trigger_event.call_args[0]
        assert event_type == "PRESS_LONG"
        assert attrs[ATTR_EVENT_TYPE] == "PRESS_LONG"
        assert attrs[ATTR_LAST_TIME_TRIGGERED] == 8000

    def test_press_long_released_payload(self):
        entity = _make_universal_switch_entity(
            eventtype=_PRESS_LONG_RELEASED, eventtimestamp=9000,
        )
        entity._event_callback()
        assert entity._trigger_event.call_args[0][0] == "PRESS_LONG_RELEASED"

    def test_device_id_in_attrs(self):
        entity = _make_universal_switch_entity(
            eventtype=_PRESS_SHORT, eventtimestamp=100,
            device_id="hdm:sw:id99",
        )
        entity._event_callback()
        attrs = entity._trigger_event.call_args[0][1]
        assert attrs[ATTR_DEVICE_ID] == "hdm:sw:id99"


# ---------------------------------------------------------------------------
# MotionDetectorEvent — __init__, subscribe wiring, _event_callback
# ---------------------------------------------------------------------------

class FakeLatestMotionService:
    id = "LatestMotion"

    def __init__(self):
        self.registered: dict = {}

    def register_event(self, key_id: str, callback) -> None:
        self.registered[key_id] = callback

    def subscribe_callback(self, eid, cb) -> None:
        pass


def _make_motion_entity(
    name: str = "Motion Sensor",
    device_id: str = "hdm:motion:1",
    root_device_id: str = "root:m",
    latestmotion: str = "2026-01-01T12:00:00",
    extra_services: list | None = None,
) -> MotionDetectorEvent:
    entity = MotionDetectorEvent.__new__(MotionDetectorEvent)
    entity._device = SimpleNamespace(
        name=name,
        id=device_id,
        root_device_id=root_device_id,
        latestmotion=latestmotion,
        device_services=extra_services or [],
        deleted=False,
        manufacturer="Bosch",
        device_model="MD",
        status="AVAILABLE",
    )
    entity.entity_id = "event.motion_sensor"
    entity._entry_id = "entry1"
    entity.hass = _make_hass_sync()
    entity._trigger_event = MagicMock()
    entity.schedule_update_ha_state = MagicMock()
    return entity


class TestMotionDetectorEventInit:
    """__init__ via real call exercises super().__init__ (line 239-240)."""

    def test_init_sets_device(self):
        dev = SimpleNamespace(
            name="Motion Det",
            id="hdm:motion:init:1",
            root_device_id="root:mi",
            device_services=[],
            deleted=False,
            manufacturer="Bosch",
            device_model="MD",
            status="AVAILABLE",
            latestmotion="ts",
        )
        entity = MotionDetectorEvent.__new__(MotionDetectorEvent)
        with patch.object(MotionDetectorEvent, "_update_attr", lambda self: None):
            MotionDetectorEvent.__init__(entity, dev, "entry_init")
        assert entity._device is dev

    def test_init_unique_id_from_super(self):
        dev = SimpleNamespace(
            name="Motion Init",
            id="hdm:motion:init:2",
            root_device_id="root:mi2",
            device_services=[],
            deleted=False,
            manufacturer="Bosch",
            device_model="MD",
            status="AVAILABLE",
            latestmotion="ts",
        )
        entity = MotionDetectorEvent.__new__(MotionDetectorEvent)
        with patch.object(MotionDetectorEvent, "_update_attr", lambda self: None):
            MotionDetectorEvent.__init__(entity, dev, "entry_init")
        # SHCEntity.__init__ sets _attr_unique_id = f"{root_device_id}_{id}"
        assert entity._attr_unique_id == "root:mi2_hdm:motion:init:2"


class TestMotionDetectorEvent:
    def test_callback_fires_motion(self):
        entity = _make_motion_entity()
        entity._event_callback()
        entity._trigger_event.assert_called_once()
        assert entity._trigger_event.call_args[0][0] == "MOTION"

    def test_callback_payload_event_type(self):
        entity = _make_motion_entity(device_id="hdm:motion:42", name="Garden Motion")
        entity._event_callback()
        attrs = entity._trigger_event.call_args[0][1]
        assert attrs[ATTR_EVENT_TYPE] == "MOTION"
        assert attrs[ATTR_ID] == "hdm:motion:42"
        assert attrs[ATTR_NAME] == "Garden Motion"

    def test_callback_payload_last_time_triggered(self):
        entity = _make_motion_entity(latestmotion="2026-06-01T10:30:00")
        entity._event_callback()
        attrs = entity._trigger_event.call_args[0][1]
        assert attrs[ATTR_LAST_TIME_TRIGGERED] == "2026-06-01T10:30:00"

    def test_callback_device_id_in_attrs(self):
        entity = _make_motion_entity(device_id="hdm:motion:99")
        entity._event_callback()
        attrs = entity._trigger_event.call_args[0][1]
        assert attrs[ATTR_DEVICE_ID] == "hdm:motion:99"

    def test_callback_calls_schedule_update(self):
        entity = _make_motion_entity()
        entity._event_callback()
        entity.schedule_update_ha_state.assert_called_once()

    def test_latest_motion_service_registered(self):
        lms = FakeLatestMotionService()
        entity = _make_motion_entity(device_id="hdm:motion:77", extra_services=[lms])

        async def _run():
            with patch(_SHC_ENTITY_ADDED, return_value=None):
                await MotionDetectorEvent.async_added_to_hass(entity)

        asyncio.run(_run())
        assert "hdm:motion:77" in lms.registered
        assert callable(lms.registered["hdm:motion:77"])

    def test_non_latest_motion_service_skipped(self):
        """A service with id != 'LatestMotion' must not be registered."""
        lms = FakeLatestMotionService()
        other = SimpleNamespace(id="Battery", subscribe_callback=lambda eid, cb: None)
        entity = _make_motion_entity(device_id="hdm:motion:11", extra_services=[other, lms])

        async def _run():
            with patch(_SHC_ENTITY_ADDED, return_value=None):
                await MotionDetectorEvent.async_added_to_hass(entity)

        asyncio.run(_run())
        assert "hdm:motion:11" in lms.registered
        assert not hasattr(other, "registered")

    def test_registered_callback_fires_event(self):
        lms = FakeLatestMotionService()
        entity = _make_motion_entity(
            device_id="hdm:motion:55",
            latestmotion="2026-06-01T08:00:00",
            extra_services=[lms],
        )

        async def _run():
            with patch(_SHC_ENTITY_ADDED, return_value=None):
                await MotionDetectorEvent.async_added_to_hass(entity)

        asyncio.run(_run())
        lms.registered["hdm:motion:55"]()
        entity._trigger_event.assert_called_once()
        assert entity._trigger_event.call_args[0][0] == "MOTION"


# ---------------------------------------------------------------------------
# SmokeDetectionSystemEvent — subscribe wiring, _event_callback
# ---------------------------------------------------------------------------

class FakeSurveillanceAlarmService:
    id = "SurveillanceAlarm"

    def __init__(self):
        self.registered: dict = {}

    def register_event(self, key_id: str, callback) -> None:
        self.registered[key_id] = callback

    def subscribe_callback(self, eid, cb) -> None:
        pass


def _make_smoke_system_entity(
    name: str = "Smoke System",
    device_id: str = "hdm:smoke:sys:1",
    root_device_id: str = "root:s",
    alarm_name: str = "IDLE_OFF",
    extra_services: list | None = None,
) -> SmokeDetectionSystemEvent:
    entity = SmokeDetectionSystemEvent.__new__(SmokeDetectionSystemEvent)
    entity._device = SimpleNamespace(
        name=name,
        id=device_id,
        root_device_id=root_device_id,
        alarm=SimpleNamespace(name=alarm_name),
        device_services=extra_services or [],
        deleted=False,
        manufacturer="Bosch",
        device_model="SDS",
        status="AVAILABLE",
    )
    entity.entity_id = "event.smoke_system"
    entity._entry_id = "entry1"
    entity.hass = _make_hass_sync()
    entity._trigger_event = MagicMock()
    entity.schedule_update_ha_state = MagicMock()
    return entity


class TestSmokeDetectionSystemEventInit:
    """__init__ via real call exercises super().__init__ (line 274-275)."""

    def test_init_unique_id_overrides_super(self):
        """SmokeDetectionSystemEvent.__init__ overrides _attr_unique_id after super()."""
        dev = SimpleNamespace(
            name="Smoke Sys Init",
            id="hdm:smoke:sys:init:1",
            root_device_id="root:ssi",
            device_services=[],
            deleted=False,
            manufacturer="Bosch",
            device_model="SDS",
            status="AVAILABLE",
            alarm=SimpleNamespace(name="IDLE_OFF"),
        )
        entity = SmokeDetectionSystemEvent.__new__(SmokeDetectionSystemEvent)
        with patch.object(SmokeDetectionSystemEvent, "_update_attr", lambda self: None):
            SmokeDetectionSystemEvent.__init__(entity, dev, "entry_init")
        assert entity._attr_unique_id == "root:ssi_hdm:smoke:sys:init:1"

    def test_init_stores_device(self):
        dev = SimpleNamespace(
            name="Smoke Sys",
            id="hdm:smoke:sys:init:2",
            root_device_id="root:ssi2",
            device_services=[],
            deleted=False,
            manufacturer="Bosch",
            device_model="SDS",
            status="AVAILABLE",
            alarm=SimpleNamespace(name="IDLE_OFF"),
        )
        entity = SmokeDetectionSystemEvent.__new__(SmokeDetectionSystemEvent)
        with patch.object(SmokeDetectionSystemEvent, "_update_attr", lambda self: None):
            SmokeDetectionSystemEvent.__init__(entity, dev, "entry_init")
        assert entity._device is dev


class TestSmokeDetectionSystemEvent:
    def test_callback_fires_alarm(self):
        entity = _make_smoke_system_entity()
        entity._event_callback()
        entity._trigger_event.assert_called_once()
        assert entity._trigger_event.call_args[0][0] == "ALARM"

    def test_callback_payload_event_type(self):
        entity = _make_smoke_system_entity(device_id="hdm:smoke:1", name="House Smoke System")
        entity._event_callback()
        attrs = entity._trigger_event.call_args[0][1]
        assert attrs[ATTR_EVENT_TYPE] == "ALARM"
        assert attrs[ATTR_ID] == "hdm:smoke:1"
        assert attrs[ATTR_NAME] == "House Smoke System"

    def test_callback_payload_alarm_subtype_idle_off(self):
        entity = _make_smoke_system_entity(alarm_name="IDLE_OFF")
        entity._event_callback()
        attrs = entity._trigger_event.call_args[0][1]
        assert attrs[ATTR_EVENT_SUBTYPE] == "IDLE_OFF"

    def test_callback_payload_alarm_subtype_alarm(self):
        entity = _make_smoke_system_entity(alarm_name="ALARM")
        entity._event_callback()
        attrs = entity._trigger_event.call_args[0][1]
        assert attrs[ATTR_EVENT_SUBTYPE] == "ALARM"

    def test_callback_device_id_in_attrs(self):
        entity = _make_smoke_system_entity(device_id="hdm:smoke:sys:99")
        entity._event_callback()
        attrs = entity._trigger_event.call_args[0][1]
        assert attrs[ATTR_DEVICE_ID] == "hdm:smoke:sys:99"

    def test_callback_calls_schedule_update(self):
        entity = _make_smoke_system_entity()
        entity._event_callback()
        entity.schedule_update_ha_state.assert_called_once()

    def test_surveillance_alarm_service_registered(self):
        sas = FakeSurveillanceAlarmService()
        entity = _make_smoke_system_entity(
            device_id="hdm:smoke:sys:10", extra_services=[sas]
        )

        async def _run():
            with patch(_SHC_ENTITY_ADDED, return_value=None):
                await SmokeDetectionSystemEvent.async_added_to_hass(entity)

        asyncio.run(_run())
        assert "hdm:smoke:sys:10" in sas.registered

    def test_non_surveillance_service_skipped(self):
        sas = FakeSurveillanceAlarmService()
        other = SimpleNamespace(id="Battery", subscribe_callback=lambda eid, cb: None)
        entity = _make_smoke_system_entity(
            device_id="hdm:smoke:sys:20", extra_services=[other, sas]
        )

        async def _run():
            with patch(_SHC_ENTITY_ADDED, return_value=None):
                await SmokeDetectionSystemEvent.async_added_to_hass(entity)

        asyncio.run(_run())
        assert "hdm:smoke:sys:20" in sas.registered
        assert not hasattr(other, "registered")

    def test_registered_callback_fires_event(self):
        sas = FakeSurveillanceAlarmService()
        entity = _make_smoke_system_entity(
            device_id="hdm:smoke:sys:30",
            alarm_name="ALARM",
            extra_services=[sas],
        )

        async def _run():
            with patch(_SHC_ENTITY_ADDED, return_value=None):
                await SmokeDetectionSystemEvent.async_added_to_hass(entity)

        asyncio.run(_run())
        sas.registered["hdm:smoke:sys:30"]()
        entity._trigger_event.assert_called_once()
        assert entity._trigger_event.call_args[0][0] == "ALARM"


# ---------------------------------------------------------------------------
# SmokeDetectorEvent — subscribe wiring, _event_callback
# ---------------------------------------------------------------------------

class FakeAlarmService:
    id = "Alarm"

    def __init__(self):
        self.registered: dict = {}

    def register_event(self, key_id: str, callback) -> None:
        self.registered[key_id] = callback

    def subscribe_callback(self, eid, cb) -> None:
        pass


def _make_smoke_detector_entity(
    name: str = "Smoke Detector",
    device_id: str = "hdm:smoke:det:1",
    root_device_id: str = "root:sd",
    alarmstate_name: str = "IDLE_OFF",
    extra_services: list | None = None,
) -> SmokeDetectorEvent:
    entity = SmokeDetectorEvent.__new__(SmokeDetectorEvent)
    entity._device = SimpleNamespace(
        name=name,
        id=device_id,
        root_device_id=root_device_id,
        alarmstate=SimpleNamespace(name=alarmstate_name),
        device_services=extra_services or [],
        deleted=False,
        manufacturer="Bosch",
        device_model="SD",
        status="AVAILABLE",
    )
    entity.entity_id = "event.smoke_detector"
    entity._entry_id = "entry1"
    entity.hass = _make_hass_sync()
    entity._trigger_event = MagicMock()
    entity.schedule_update_ha_state = MagicMock()
    return entity


class TestSmokeDetectorEventInit:
    """__init__ via real call exercises super().__init__ (line 309-310)."""

    def test_init_unique_id_overrides_super(self):
        dev = SimpleNamespace(
            name="Smoke Det Init",
            id="hdm:smoke:det:init:1",
            root_device_id="root:sdi",
            device_services=[],
            deleted=False,
            manufacturer="Bosch",
            device_model="SD",
            status="AVAILABLE",
            alarmstate=SimpleNamespace(name="IDLE_OFF"),
        )
        entity = SmokeDetectorEvent.__new__(SmokeDetectorEvent)
        with patch.object(SmokeDetectorEvent, "_update_attr", lambda self: None):
            SmokeDetectorEvent.__init__(entity, dev, "entry_init")
        assert entity._attr_unique_id == "root:sdi_hdm:smoke:det:init:1"

    def test_init_stores_device(self):
        dev = SimpleNamespace(
            name="SD",
            id="hdm:smoke:det:init:2",
            root_device_id="root:sdi2",
            device_services=[],
            deleted=False,
            manufacturer="Bosch",
            device_model="SD",
            status="AVAILABLE",
            alarmstate=SimpleNamespace(name="IDLE_OFF"),
        )
        entity = SmokeDetectorEvent.__new__(SmokeDetectorEvent)
        with patch.object(SmokeDetectorEvent, "_update_attr", lambda self: None):
            SmokeDetectorEvent.__init__(entity, dev, "entry_init")
        assert entity._device is dev


class TestSmokeDetectorEvent:
    def test_callback_fires_alarm(self):
        entity = _make_smoke_detector_entity()
        entity._event_callback()
        entity._trigger_event.assert_called_once()
        assert entity._trigger_event.call_args[0][0] == "ALARM"

    def test_callback_payload_event_type(self):
        entity = _make_smoke_detector_entity(
            device_id="hdm:smoke:det:42", name="Bedroom Detector"
        )
        entity._event_callback()
        attrs = entity._trigger_event.call_args[0][1]
        assert attrs[ATTR_EVENT_TYPE] == "ALARM"
        assert attrs[ATTR_ID] == "hdm:smoke:det:42"
        assert attrs[ATTR_NAME] == "Bedroom Detector"

    def test_callback_payload_alarmstate_idle_off(self):
        entity = _make_smoke_detector_entity(alarmstate_name="IDLE_OFF")
        entity._event_callback()
        attrs = entity._trigger_event.call_args[0][1]
        assert attrs[ATTR_EVENT_SUBTYPE] == "IDLE_OFF"

    def test_callback_payload_alarmstate_intrusion_alarm(self):
        entity = _make_smoke_detector_entity(alarmstate_name="INTRUSION_ALARM")
        entity._event_callback()
        attrs = entity._trigger_event.call_args[0][1]
        assert attrs[ATTR_EVENT_SUBTYPE] == "INTRUSION_ALARM"

    def test_callback_payload_alarmstate_primary_smoke_alarm(self):
        entity = _make_smoke_detector_entity(alarmstate_name="PRIMARY_SMOKE_ALARM")
        entity._event_callback()
        attrs = entity._trigger_event.call_args[0][1]
        assert attrs[ATTR_EVENT_SUBTYPE] == "PRIMARY_SMOKE_ALARM"

    def test_callback_device_id_in_attrs(self):
        entity = _make_smoke_detector_entity(device_id="hdm:smoke:det:77")
        entity._event_callback()
        attrs = entity._trigger_event.call_args[0][1]
        assert attrs[ATTR_DEVICE_ID] == "hdm:smoke:det:77"

    def test_callback_calls_schedule_update(self):
        entity = _make_smoke_detector_entity()
        entity._event_callback()
        entity.schedule_update_ha_state.assert_called_once()

    def test_alarm_service_registered(self):
        als = FakeAlarmService()
        entity = _make_smoke_detector_entity(
            device_id="hdm:smoke:det:10", extra_services=[als]
        )

        async def _run():
            with patch(_SHC_ENTITY_ADDED, return_value=None):
                await SmokeDetectorEvent.async_added_to_hass(entity)

        asyncio.run(_run())
        assert "hdm:smoke:det:10" in als.registered

    def test_non_alarm_service_skipped(self):
        als = FakeAlarmService()
        other = SimpleNamespace(id="Battery", subscribe_callback=lambda eid, cb: None)
        entity = _make_smoke_detector_entity(
            device_id="hdm:smoke:det:20", extra_services=[other, als]
        )

        async def _run():
            with patch(_SHC_ENTITY_ADDED, return_value=None):
                await SmokeDetectorEvent.async_added_to_hass(entity)

        asyncio.run(_run())
        assert "hdm:smoke:det:20" in als.registered
        assert not hasattr(other, "registered")

    def test_registered_callback_fires_event(self):
        als = FakeAlarmService()
        entity = _make_smoke_detector_entity(
            device_id="hdm:smoke:det:30",
            alarmstate_name="PRIMARY_SMOKE_ALARM",
            extra_services=[als],
        )

        async def _run():
            with patch(_SHC_ENTITY_ADDED, return_value=None):
                await SmokeDetectorEvent.async_added_to_hass(entity)

        asyncio.run(_run())
        als.registered["hdm:smoke:det:30"]()
        entity._trigger_event.assert_called_once()
        assert entity._trigger_event.call_args[0][0] == "ALARM"


# ---------------------------------------------------------------------------
# Class-level structural checks (use instances — HA uses attrgetter properties)
# ---------------------------------------------------------------------------

class TestEventEntityStructure:
    def test_universal_switch_event_types_on_instance(self):
        entity = _make_universal_switch_entity()
        # HA stores _attr_event_types as a property; access via instance
        assert entity._attr_event_types == ["PRESS_SHORT", "PRESS_LONG", "PRESS_LONG_RELEASED"]

    def test_motion_detector_event_types_on_instance(self):
        entity = _make_motion_entity()
        assert entity._attr_event_types == ["MOTION"]

    def test_smoke_detection_system_event_types_on_instance(self):
        entity = _make_smoke_system_entity()
        assert entity._attr_event_types == ["ALARM"]

    def test_smoke_detector_event_types_on_instance(self):
        entity = _make_smoke_detector_entity()
        assert entity._attr_event_types == ["ALARM"]

    def test_universal_switch_device_class_on_instance(self):
        from homeassistant.components.event import EventDeviceClass
        entity = _make_universal_switch_entity()
        assert entity._attr_device_class == EventDeviceClass.BUTTON

    def test_motion_detector_device_class_on_instance(self):
        from homeassistant.components.event import EventDeviceClass
        entity = _make_motion_entity()
        assert entity._attr_device_class == EventDeviceClass.MOTION

    def test_universal_switch_is_event_entity(self):
        from homeassistant.components.event import EventEntity
        assert issubclass(UniversalSwitchEvent, EventEntity)

    def test_motion_detector_is_event_entity(self):
        from homeassistant.components.event import EventEntity
        assert issubclass(MotionDetectorEvent, EventEntity)

    def test_smoke_detection_system_is_event_entity(self):
        from homeassistant.components.event import EventEntity
        assert issubclass(SmokeDetectionSystemEvent, EventEntity)

    def test_smoke_detector_is_event_entity(self):
        from homeassistant.components.event import EventEntity
        assert issubclass(SmokeDetectorEvent, EventEntity)


# ---------------------------------------------------------------------------
# LightControlButtonEvent (#282)
# ---------------------------------------------------------------------------


def _make_light_control_event(
    eventtype=_PRESS_SHORT,
    eventtimestamp: int = 1000,
    last_fired: int = -1,
):
    entity = LightControlButtonEvent.__new__(LightControlButtonEvent)
    entity._device = SimpleNamespace(
        name="Lichtsteuerung",
        id="hdm:lc:1",
        root_device_id="root:1",
        eventtype=eventtype,
        eventtimestamp=eventtimestamp,
        device_services=[],
        deleted=False,
        manufacturer="Bosch",
        device_model="MICROMODULE_LIGHT_ATTACHED",
        status="AVAILABLE",
    )
    entity._last_fired_timestamp = last_fired
    entity.entity_id = "event.lichtsteuerung_button"
    entity.hass = _make_hass_sync()
    entity._trigger_event = MagicMock()
    entity.schedule_update_ha_state = MagicMock()
    return entity


class TestLightControlButtonEvent:
    def test_fires_on_press(self):
        e = _make_light_control_event(eventtype=_PRESS_SHORT, eventtimestamp=42)
        e._event_callback()
        e._trigger_event.assert_called_once()
        args = e._trigger_event.call_args[0]
        assert args[0] == "PRESS_SHORT"
        assert args[1][ATTR_LAST_TIME_TRIGGERED] == 42

    def test_switch_on_event_fires(self):
        e = _make_light_control_event(eventtype=_SWITCH_ON, eventtimestamp=7)
        e._event_callback()
        assert e._trigger_event.call_args[0][0] == "SWITCH_ON"

    def test_none_eventtype_no_op(self):
        e = _make_light_control_event(eventtype=None)
        e._event_callback()
        e._trigger_event.assert_not_called()

    def test_unknown_type_ignored(self):
        e = _make_light_control_event(eventtype=SimpleNamespace(name="MOTION"))
        e._event_callback()
        e._trigger_event.assert_not_called()

    def test_duplicate_timestamp_suppressed(self):
        e = _make_light_control_event(eventtimestamp=99, last_fired=99)
        e._event_callback()
        e._trigger_event.assert_not_called()

    def test_advancing_timestamp_updates_guard(self):
        e = _make_light_control_event(eventtimestamp=5, last_fired=-1)
        e._event_callback()
        assert e._last_fired_timestamp == 5

    def test_is_event_entity(self):
        from homeassistant.components.event import EventEntity
        assert issubclass(LightControlButtonEvent, EventEntity)
