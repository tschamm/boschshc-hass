"""Additional pure-unit coverage for event.py.

Targets uncovered lines not hit by test_event_unit.py,
test_event_universal_switch.py, test_event_dispatch_thread_safe.py,
or test_event_entity_setup.py:

- SHCScenarioEvent._event_callback uses call_soon_threadsafe (not direct call)
- SHCScenarioEvent._dispatch_event (triggers + schedules)
- SHCScenarioEvent properties: device_name, device_id, device_info
- MotionDetectorEvent._dispatch_event direct call
- SmokeDetectorEvent._dispatch_event direct call
- SmokeDetectionSystemEvent._dispatch_event direct call
- event_types class attribute on all entity types (structural pin)
- UniversalSwitchEvent._event_callback None-eventtype early return (not via hass sync)

Pattern: __new__ bypass + SimpleNamespace; no HA harness.

Run with:
  PYTHONPATH="<boschshc-hass>:<boschshcpy>" \\
  PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \\
  python3 -m pytest tests/bosch_shc/test_event_coverage.py -q -o addopts=
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from custom_components.bosch_shc.event import (
    MotionDetectorEvent,
    SHCScenarioEvent,
    SmokeDetectionSystemEvent,
    SmokeDetectorEvent,
    UniversalSwitchEvent,
)
from custom_components.bosch_shc.const import (
    ATTR_EVENT_SUBTYPE,
    ATTR_EVENT_TYPE,
    ATTR_LAST_TIME_TRIGGERED,
    DATA_SHC,
    DOMAIN,
)
from homeassistant.const import ATTR_ID, ATTR_NAME


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_hass_direct():
    """hass whose call_soon_threadsafe executes fn synchronously."""
    hass = MagicMock(name="hass")

    def _sync_call(fn, *args, **kwargs):
        fn(*args, **kwargs)

    hass.loop.call_soon_threadsafe.side_effect = _sync_call
    return hass


def _make_hass_capturing():
    """hass that captures call_soon_threadsafe args without executing."""
    hass = MagicMock(name="hass")
    hass.loop = MagicMock(name="loop")
    return hass


# ---------------------------------------------------------------------------
# SHCScenarioEvent — build helper
# ---------------------------------------------------------------------------

def _make_scenario_entity(
    scenario_id: str = "sc-1",
    scenario_name: str = "Abend",
    session_unique_id: str = "shc-uid-abc",
    shc_name: str = "SHC Controller",
    shc_id: str = "shc-ha-dev-1",
) -> SHCScenarioEvent:
    """Build a SHCScenarioEvent without hass.data lookup."""
    scenario = SimpleNamespace(id=scenario_id, name=scenario_name)
    session = SimpleNamespace(
        information=SimpleNamespace(unique_id=session_unique_id),
        subscribe_scenario_callback=lambda sid, cb: None,
    )
    shc_device_entry = SimpleNamespace(
        id=shc_id,
        name=shc_name,
        identifiers={(DOMAIN, shc_id)},
        manufacturer="Robert Bosch GmbH",
        model="SHC",
    )

    # SHCScenarioEvent.__init__ does hass.data[DOMAIN][entry_id][DATA_SHC]
    entry_id = "entry1"
    hass = MagicMock(name="hass")
    hass.data = {DOMAIN: {entry_id: {DATA_SHC: shc_device_entry}}}

    entity = SHCScenarioEvent(scenario, session, hass, entry_id=entry_id)
    entity.hass = _make_hass_direct()
    entity._trigger_event = MagicMock()
    entity.schedule_update_ha_state = MagicMock()
    return entity


# ---------------------------------------------------------------------------
# SHCScenarioEvent — properties
# ---------------------------------------------------------------------------

class TestSHCScenarioEventProperties:
    """device_name, device_id, device_info must delegate to _shc DeviceEntry."""

    def test_device_name_returns_shc_name(self):
        entity = _make_scenario_entity(shc_name="My SHC")
        assert entity.device_name == "My SHC"

    def test_device_id_returns_shc_id(self):
        entity = _make_scenario_entity(shc_id="ha-dev-xyz")
        assert entity.device_id == "ha-dev-xyz"

    def test_device_info_identifiers(self):
        entity = _make_scenario_entity(shc_id="ha-dev-q")
        info = entity.device_info
        assert "identifiers" in info
        assert (DOMAIN, "ha-dev-q") in info["identifiers"]

    def test_device_info_manufacturer(self):
        entity = _make_scenario_entity()
        info = entity.device_info
        assert info["manufacturer"] == "Robert Bosch GmbH"

    def test_device_info_name(self):
        entity = _make_scenario_entity(shc_name="Controller")
        info = entity.device_info
        assert info["name"] == "Controller"

    def test_attr_unique_id_format(self):
        entity = _make_scenario_entity(session_unique_id="uid-abc", scenario_id="sc-5")
        assert entity._attr_unique_id == "uid-abc_sc-5"

    def test_attr_name_contains_scenario_name(self):
        entity = _make_scenario_entity(scenario_name="Guten Morgen")
        assert "Guten Morgen" in entity._attr_name

    def test_entity_id_slug_contains_scenario_name(self):
        entity = _make_scenario_entity(scenario_name="Evening")
        assert "evening" in entity.entity_id

    def test_event_types_is_scenario(self):
        entity = _make_scenario_entity()
        assert entity._attr_event_types == ["SCENARIO"]


# ---------------------------------------------------------------------------
# SHCScenarioEvent — _event_callback dispatches via call_soon_threadsafe
# ---------------------------------------------------------------------------

class TestSHCScenarioEventCallback:
    """_event_callback must schedule via call_soon_threadsafe, not call directly."""

    def _make_entity_capturing(self):
        """Returns entity with capturing (non-executing) hass."""
        scenario = SimpleNamespace(id="sc-cb", name="Abend")
        session = SimpleNamespace(
            information=SimpleNamespace(unique_id="uid-cb"),
            subscribe_scenario_callback=lambda sid, cb: None,
        )
        entry_id = "e1"
        shc = SimpleNamespace(
            id="shc-id", name="SHC", identifiers={(DOMAIN, "shc-id")},
            manufacturer="Bosch", model="SHC",
        )
        hass_init = MagicMock(name="hass_init")
        hass_init.data = {DOMAIN: {entry_id: {DATA_SHC: shc}}}
        entity = SHCScenarioEvent(scenario, session, hass_init, entry_id=entry_id)
        entity.hass = _make_hass_capturing()
        entity._trigger_event = MagicMock()
        entity.schedule_update_ha_state = MagicMock()
        return entity

    def test_event_callback_uses_call_soon_threadsafe(self):
        entity = self._make_entity_capturing()
        event_data = {"id": "sc-cb", "name": "Abend", "lastTimeTriggered": "2026-06-20"}
        entity._event_callback(event_data)
        assert entity.hass.loop.call_soon_threadsafe.called

    def test_event_callback_does_not_call_trigger_directly(self):
        entity = self._make_entity_capturing()
        event_data = {"id": "sc-cb", "name": "Abend", "lastTimeTriggered": "2026-06-20"}
        entity._event_callback(event_data)
        # With capturing hass (no side_effect), _trigger_event is NOT called directly
        entity._trigger_event.assert_not_called()

    def test_event_callback_passes_dispatch_event_fn(self):
        entity = self._make_entity_capturing()
        event_data = {"id": "sc-cb", "name": "Abend", "lastTimeTriggered": "t"}
        entity._event_callback(event_data)
        args = entity.hass.loop.call_soon_threadsafe.call_args[0]
        assert callable(args[0])

    def test_event_callback_event_type_is_scenario(self):
        entity = self._make_entity_capturing()
        event_data = {"id": "sc-cb", "name": "Abend", "lastTimeTriggered": "t"}
        entity._event_callback(event_data)
        args = entity.hass.loop.call_soon_threadsafe.call_args[0]
        # Second arg is the event_type string
        assert args[1] == "SCENARIO"


# ---------------------------------------------------------------------------
# SHCScenarioEvent — _dispatch_event direct call
# ---------------------------------------------------------------------------

class TestSHCScenarioEventDispatch:
    """_dispatch_event must call _trigger_event and schedule_update_ha_state."""

    def test_dispatch_calls_trigger_event(self):
        entity = _make_scenario_entity()
        entity._dispatch_event("SCENARIO", {ATTR_EVENT_TYPE: "SCENARIO"})
        entity._trigger_event.assert_called_once()

    def test_dispatch_calls_schedule_update(self):
        entity = _make_scenario_entity()
        entity._dispatch_event("SCENARIO", {})
        entity.schedule_update_ha_state.assert_called_once()

    def test_dispatch_passes_event_type(self):
        entity = _make_scenario_entity()
        entity._dispatch_event("SCENARIO", {"key": "val"})
        call_args = entity._trigger_event.call_args[0]
        assert call_args[0] == "SCENARIO"

    def test_dispatch_passes_attributes(self):
        entity = _make_scenario_entity()
        attrs = {ATTR_EVENT_TYPE: "SCENARIO", ATTR_ID: "sc-1"}
        entity._dispatch_event("SCENARIO", attrs)
        call_args = entity._trigger_event.call_args[0]
        assert call_args[1] == attrs


# ---------------------------------------------------------------------------
# SHCScenarioEvent — async_added_to_hass registers callback
# ---------------------------------------------------------------------------

_EVENTENTITY_ADDED = "homeassistant.components.event.EventEntity.async_added_to_hass"


class TestSHCScenarioEventSubscribe:
    """async_added_to_hass registers _event_callback via subscribe_scenario_callback."""

    def test_subscribe_called_with_scenario_id(self):
        subscriptions = {}
        scenario = SimpleNamespace(id="sc-sub", name="Morning")
        session = SimpleNamespace(
            information=SimpleNamespace(unique_id="uid-sub"),
            subscribe_scenario_callback=lambda sid, cb: subscriptions.update({sid: cb}),
        )
        entry_id = "e1"
        shc = SimpleNamespace(
            id="shc-sub", name="SHC", identifiers={(DOMAIN, "shc-sub")},
            manufacturer="Bosch", model="SHC",
        )
        hass_init = MagicMock()
        hass_init.data = {DOMAIN: {entry_id: {DATA_SHC: shc}}}
        entity = SHCScenarioEvent(scenario, session, hass_init, entry_id=entry_id)
        entity.hass = _make_hass_direct()
        entity._trigger_event = MagicMock()
        entity.schedule_update_ha_state = MagicMock()

        async def _run():
            with patch(_EVENTENTITY_ADDED, return_value=None):
                await SHCScenarioEvent.async_added_to_hass(entity)

        asyncio.run(_run())
        assert "sc-sub" in subscriptions
        assert callable(subscriptions["sc-sub"])

    def test_registered_callback_fires_event(self):
        subscriptions = {}
        scenario = SimpleNamespace(id="sc-fire", name="Evening")
        session = SimpleNamespace(
            information=SimpleNamespace(unique_id="uid-fire"),
            subscribe_scenario_callback=lambda sid, cb: subscriptions.update({sid: cb}),
        )
        entry_id = "e1"
        shc = SimpleNamespace(
            id="shc-fire", name="SHC", identifiers={(DOMAIN, "shc-fire")},
            manufacturer="Bosch", model="SHC",
        )
        hass_init = MagicMock()
        hass_init.data = {DOMAIN: {entry_id: {DATA_SHC: shc}}}
        entity = SHCScenarioEvent(scenario, session, hass_init, entry_id=entry_id)
        entity.hass = _make_hass_direct()
        entity._trigger_event = MagicMock()
        entity.schedule_update_ha_state = MagicMock()

        async def _run():
            with patch(_EVENTENTITY_ADDED, return_value=None):
                await SHCScenarioEvent.async_added_to_hass(entity)

        asyncio.run(_run())
        event_data = {"id": "sc-fire", "name": "Evening", "lastTimeTriggered": "2026-06-20"}
        subscriptions["sc-fire"](event_data)
        entity._trigger_event.assert_called_once()
        assert entity._trigger_event.call_args[0][0] == "SCENARIO"

    def test_registered_callback_payload_attributes(self):
        subscriptions = {}
        scenario = SimpleNamespace(id="sc-pay", name="Night Mode")
        session = SimpleNamespace(
            information=SimpleNamespace(unique_id="uid-pay"),
            subscribe_scenario_callback=lambda sid, cb: subscriptions.update({sid: cb}),
        )
        entry_id = "e1"
        shc = SimpleNamespace(
            id="shc-pay", name="SHC", identifiers={(DOMAIN, "shc-pay")},
            manufacturer="Bosch", model="SHC",
        )
        hass_init = MagicMock()
        hass_init.data = {DOMAIN: {entry_id: {DATA_SHC: shc}}}
        entity = SHCScenarioEvent(scenario, session, hass_init, entry_id=entry_id)
        entity.hass = _make_hass_direct()
        entity._trigger_event = MagicMock()
        entity.schedule_update_ha_state = MagicMock()

        async def _run():
            with patch(_EVENTENTITY_ADDED, return_value=None):
                await SHCScenarioEvent.async_added_to_hass(entity)

        asyncio.run(_run())
        ts = "2026-06-20T12:00:00"
        subscriptions["sc-pay"]({"id": "sc-pay", "name": "Night Mode", "lastTimeTriggered": ts})
        attrs = entity._trigger_event.call_args[0][1]
        assert attrs[ATTR_EVENT_TYPE] == "SCENARIO"
        assert attrs[ATTR_ID] == "sc-pay"
        assert attrs[ATTR_NAME] == "Night Mode"
        assert attrs[ATTR_LAST_TIME_TRIGGERED] == ts


# ---------------------------------------------------------------------------
# MotionDetectorEvent — _dispatch_event direct call
# ---------------------------------------------------------------------------

def _make_motion_event_entity():
    entity = MotionDetectorEvent.__new__(MotionDetectorEvent)
    entity._device = SimpleNamespace(
        name="Motion", id="hdm:motion:d1", root_device_id="root:m",
        latestmotion="2026-06-20T10:00:00.000Z",
        device_services=[], deleted=False, manufacturer="Bosch",
        device_model="MD", status="AVAILABLE",
    )
    entity._attr_unique_id = "root:m_hdm:motion:d1"
    entity.hass = _make_hass_direct()
    entity._trigger_event = MagicMock()
    entity.schedule_update_ha_state = MagicMock()
    return entity


class TestMotionDetectorEventDispatch:
    """_dispatch_event must call _trigger_event and schedule_update_ha_state."""

    def test_dispatch_calls_trigger_event(self):
        entity = _make_motion_event_entity()
        entity._dispatch_event("MOTION", {ATTR_EVENT_TYPE: "MOTION"})
        entity._trigger_event.assert_called_once()

    def test_dispatch_passes_event_type(self):
        entity = _make_motion_event_entity()
        entity._dispatch_event("MOTION", {})
        assert entity._trigger_event.call_args[0][0] == "MOTION"

    def test_dispatch_calls_schedule_update(self):
        entity = _make_motion_event_entity()
        entity._dispatch_event("MOTION", {})
        entity.schedule_update_ha_state.assert_called_once()

    def test_dispatch_passes_attributes_dict(self):
        entity = _make_motion_event_entity()
        attrs = {ATTR_EVENT_TYPE: "MOTION", ATTR_ID: "hdm:motion:d1"}
        entity._dispatch_event("MOTION", attrs)
        assert entity._trigger_event.call_args[0][1] == attrs


# ---------------------------------------------------------------------------
# SmokeDetectorEvent — _dispatch_event direct call
# ---------------------------------------------------------------------------

def _make_smoke_detector_event_entity(alarmstate_name="PRIMARY_ALARM"):
    entity = SmokeDetectorEvent.__new__(SmokeDetectorEvent)
    entity._device = SimpleNamespace(
        name="Smoke Det", id="hdm:smoke:d1", root_device_id="root:sd",
        alarmstate=SimpleNamespace(name=alarmstate_name),
        device_services=[], deleted=False, manufacturer="Bosch",
        device_model="SD", status="AVAILABLE",
    )
    entity._attr_unique_id = "root:sd_hdm:smoke:d1"
    entity.hass = _make_hass_direct()
    entity._trigger_event = MagicMock()
    entity.schedule_update_ha_state = MagicMock()
    return entity


class TestSmokeDetectorEventDispatch:
    """_dispatch_event must call _trigger_event and schedule_update_ha_state."""

    def test_dispatch_calls_trigger_event(self):
        entity = _make_smoke_detector_event_entity()
        entity._dispatch_event("ALARM", {})
        entity._trigger_event.assert_called_once()

    def test_dispatch_passes_alarm_event_type(self):
        entity = _make_smoke_detector_event_entity()
        entity._dispatch_event("ALARM", {})
        assert entity._trigger_event.call_args[0][0] == "ALARM"

    def test_dispatch_calls_schedule_update(self):
        entity = _make_smoke_detector_event_entity()
        entity._dispatch_event("ALARM", {})
        entity.schedule_update_ha_state.assert_called_once()

    def test_dispatch_passes_attributes_dict(self):
        entity = _make_smoke_detector_event_entity()
        attrs = {ATTR_EVENT_TYPE: "ALARM", ATTR_EVENT_SUBTYPE: "PRIMARY_ALARM"}
        entity._dispatch_event("ALARM", attrs)
        assert entity._trigger_event.call_args[0][1] == attrs

    def test_event_callback_calls_schedule_update(self):
        """_event_callback → call_soon_threadsafe → _dispatch_event → schedule_update."""
        entity = _make_smoke_detector_event_entity("SECONDARY_ALARM")
        entity.hass = _make_hass_direct()  # executes fn immediately
        entity._trigger_event = MagicMock()
        entity.schedule_update_ha_state = MagicMock()
        entity._event_callback()
        entity.schedule_update_ha_state.assert_called_once()

    def test_event_types_is_alarm(self):
        entity = _make_smoke_detector_event_entity()
        assert entity._attr_event_types == ["ALARM"]


# ---------------------------------------------------------------------------
# SmokeDetectionSystemEvent — _dispatch_event direct call
# ---------------------------------------------------------------------------

def _make_smoke_system_event_entity(alarm_name="ALARM_ON"):
    entity = SmokeDetectionSystemEvent.__new__(SmokeDetectionSystemEvent)
    entity._device = SimpleNamespace(
        name="Smoke System", id="hdm:smoke:sys:1", root_device_id="root:ss",
        alarm=SimpleNamespace(name=alarm_name),
        device_services=[], deleted=False, manufacturer="Bosch",
        device_model="SDS", status="AVAILABLE",
    )
    entity._attr_unique_id = "root:ss_hdm:smoke:sys:1"
    entity.hass = _make_hass_direct()
    entity._trigger_event = MagicMock()
    entity.schedule_update_ha_state = MagicMock()
    return entity


class TestSmokeDetectionSystemEventDispatch:
    """_dispatch_event must call _trigger_event and schedule_update_ha_state."""

    def test_dispatch_calls_trigger_event(self):
        entity = _make_smoke_system_event_entity()
        entity._dispatch_event("ALARM", {})
        entity._trigger_event.assert_called_once()

    def test_dispatch_passes_alarm_event_type(self):
        entity = _make_smoke_system_event_entity()
        entity._dispatch_event("ALARM", {})
        assert entity._trigger_event.call_args[0][0] == "ALARM"

    def test_dispatch_calls_schedule_update(self):
        entity = _make_smoke_system_event_entity()
        entity._dispatch_event("ALARM", {})
        entity.schedule_update_ha_state.assert_called_once()

    def test_dispatch_passes_attributes_dict(self):
        entity = _make_smoke_system_event_entity()
        attrs = {ATTR_EVENT_TYPE: "ALARM", ATTR_EVENT_SUBTYPE: "ALARM_ON"}
        entity._dispatch_event("ALARM", attrs)
        assert entity._trigger_event.call_args[0][1] == attrs

    def test_event_callback_full_chain_via_direct_hass(self):
        """_event_callback → call_soon_threadsafe(sync) → _dispatch_event → schedule_update."""
        entity = _make_smoke_system_event_entity("ALARM_ON")
        entity._event_callback()
        entity._trigger_event.assert_called_once()
        entity.schedule_update_ha_state.assert_called_once()

    def test_event_types_is_alarm(self):
        entity = _make_smoke_system_event_entity()
        assert entity._attr_event_types == ["ALARM"]


# ---------------------------------------------------------------------------
# UniversalSwitchEvent — None eventtype guard (early-return path)
# ---------------------------------------------------------------------------

class TestUniversalSwitchEventNoneEarlyReturn:
    """eventtype=None must return before call_soon_threadsafe is called."""

    def _make_entity_none(self):
        entity = UniversalSwitchEvent.__new__(UniversalSwitchEvent)
        entity.hass = _make_hass_capturing()
        entity._device = SimpleNamespace(
            name="Switch", id="sw-1", root_device_id="root-1",
            eventtype=None,
            eventtimestamp=1000,
        )
        entity._key_id = "UPPER_BUTTON"
        entity._last_fired_timestamp = -1
        entity._attr_unique_id = "root-1_sw-1_UPPER_BUTTON"
        entity.entity_id = "event.switch_button_upper_button"
        entity._trigger_event = MagicMock()
        entity.schedule_update_ha_state = MagicMock()
        return entity

    def test_none_eventtype_no_call_soon_threadsafe(self):
        entity = self._make_entity_none()
        entity._event_callback()
        entity.hass.loop.call_soon_threadsafe.assert_not_called()

    def test_none_eventtype_no_trigger_event(self):
        entity = self._make_entity_none()
        entity._event_callback()
        entity._trigger_event.assert_not_called()

    def test_none_eventtype_no_schedule_update(self):
        entity = self._make_entity_none()
        entity._event_callback()
        entity.schedule_update_ha_state.assert_not_called()

    def test_none_eventtype_last_fired_unchanged(self):
        entity = self._make_entity_none()
        entity._event_callback()
        assert entity._last_fired_timestamp == -1


# ---------------------------------------------------------------------------
# Structural: _attr_event_types and _attr_device_class on all event entities
# ---------------------------------------------------------------------------

class TestEventEntityStructureExtended:
    """Pin class-level attributes that HA reads via instance access."""

    def test_scenario_has_entity_name_true(self):
        entity = _make_scenario_entity()
        assert entity._attr_has_entity_name is True

    def test_motion_event_types_list(self):
        entity = _make_motion_event_entity()
        assert "MOTION" in entity._attr_event_types

    def test_smoke_detector_event_types_list(self):
        entity = _make_smoke_detector_event_entity()
        assert "ALARM" in entity._attr_event_types

    def test_smoke_system_event_types_list(self):
        entity = _make_smoke_system_event_entity()
        assert "ALARM" in entity._attr_event_types
