"""Coverage for previously uncovered lines in event.py and entity.py.

Targets:
  event.py  — async_setup_entry (lines 49-98)
              UniversalSwitchEvent._event_callback dedup/None/non-press guards (141,144,147-152)
              SHCScenarioEvent.__init__ + properties + async_added_to_hass + _event_callback
              (180-189, 194, 199, 204, 213-215, 220-228)
  entity.py — async_get_device_id (14-18)
              async_remove_devices (25-30)
              async_migrate_to_new_unique_id (41-66)
              SHCEntity._update_attr pass (85)
              update_entity_information else-branch: not-deleted (99-100)

Pattern: no HA test harness, no tests.common, no network.
asyncio.run() drives all coroutines (each gets a fresh event loop, Python 3.14).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

# ---------------------------------------------------------------------------
# Imports under test
# ---------------------------------------------------------------------------
from custom_components.bosch_shc.event import (
    SHCScenarioEvent,
    UniversalSwitchEvent,
    async_setup_entry,
)
from custom_components.bosch_shc.entity import (
    SHCEntity,
    async_get_device_id,
    async_migrate_to_new_unique_id,
    async_remove_devices,
)
from custom_components.bosch_shc.const import (
    ATTR_EVENT_TYPE,
    ATTR_LAST_TIME_TRIGGERED,
    DATA_SESSION,
    DATA_SHC,
    DOMAIN,
)
from homeassistant.const import ATTR_ID, ATTR_NAME

# ---------------------------------------------------------------------------
# Shared sentinel press-type
# ---------------------------------------------------------------------------

_PRESS_SHORT = SimpleNamespace(name="PRESS_SHORT")
_SWITCH_ON = SimpleNamespace(name="SWITCH_ON")

_SHC_ENTITY_ADDED = "custom_components.bosch_shc.event.SHCEntity.async_added_to_hass"


# ===========================================================================
# A.  async_setup_entry — event.py lines 49-98
# ===========================================================================


def _make_fake_switch(name="Switch A", dev_id="hdm:sw:1", root_id="root:1",
                      keystates=("UPPER_BUTTON",)):
    """Return a minimal fake universal switch device."""
    return SimpleNamespace(
        name=name,
        id=dev_id,
        root_device_id=root_id,
        keystates=list(keystates),
        device_services=[],
        deleted=False,
        manufacturer="Bosch",
        device_model="US",
        status="AVAILABLE",
        eventtype=_PRESS_SHORT,
        eventtimestamp=0,
    )


def _make_fake_motion(name="Motion", dev_id="hdm:motion:1", root_id="root:m"):
    return SimpleNamespace(
        name=name,
        id=dev_id,
        root_device_id=root_id,
        device_services=[],
        deleted=False,
        manufacturer="Bosch",
        device_model="MD",
        status="AVAILABLE",
        latestmotion="2026-01-01T00:00:00",
    )


def _make_fake_smoke_system(name="Smoke Sys", dev_id="hdm:ss:1", root_id="root:ss"):
    return SimpleNamespace(
        name=name,
        id=dev_id,
        root_device_id=root_id,
        device_services=[],
        deleted=False,
        manufacturer="Bosch",
        device_model="SDS",
        status="AVAILABLE",
        alarm=SimpleNamespace(name="IDLE_OFF"),
    )


def _make_fake_smoke_detector(name="Smoke Det", dev_id="hdm:sd:1", root_id="root:sd"):
    return SimpleNamespace(
        name=name,
        id=dev_id,
        root_device_id=root_id,
        device_services=[],
        deleted=False,
        manufacturer="Bosch",
        device_model="SD",
        status="AVAILABLE",
        alarmstate=SimpleNamespace(name="IDLE_OFF"),
    )


def _make_fake_scenario(name="Night Mode", scenario_id="scn:1"):
    return SimpleNamespace(name=name, id=scenario_id)


def _make_session(
    switches=None,
    scenarios=None,
    motion_detectors=None,
    motion_detectors2=None,
    smoke_detection_system=None,
    smoke_detectors=None,
):
    """Build a fake SHCSession-like object.

    motion_detectors2 must be present in device_helper because event.py
    async_setup_entry iterates (motion_detectors + motion_detectors2).
    """
    return SimpleNamespace(
        device_helper=SimpleNamespace(
            universal_switches=switches or [],
            motion_detectors=motion_detectors or [],
            motion_detectors2=motion_detectors2 or [],
            smoke_detection_system=smoke_detection_system,
            smoke_detectors=smoke_detectors or [],
        ),
        scenarios=scenarios or [],
        information=SimpleNamespace(unique_id="uid-shc-001"),
        subscribe_scenario_callback=MagicMock(),
    )


def _make_fake_shc_device_entry():
    """Fake DeviceEntry for SHCScenarioEvent._shc."""
    return SimpleNamespace(
        name="Bosch SHC",
        id="device-shc-entry-1",
        identifiers={("bosch_shc", "SHC_SERIAL")},
        manufacturer="Bosch",
        model="Smart Home Controller",
    )


def _make_setup_hass(session, shc_entry=None):
    """Return a fake hass with the right data structure for async_setup_entry."""
    shc = shc_entry or _make_fake_shc_device_entry()
    return SimpleNamespace(
        data={
            DOMAIN: {
                "entry1": {
                    DATA_SESSION: session,
                    DATA_SHC: shc,
                }
            }
        }
    )


def _make_entry(entry_id="entry1"):
    return SimpleNamespace(options={}, entry_id=entry_id)


def _collecting_add_fn():
    """Return a (callable, list) pair. callable accepts (entities, update_before_add)."""
    collected = []

    def add_fn(entities, update_before_add=False):
        collected.extend(entities)

    return add_fn, collected


class TestAsyncSetupEntryUniversalSwitch:
    """async_setup_entry creates UniversalSwitchEvent per keystate."""

    def test_one_switch_two_keystates_produces_two_entities(self):
        sw = _make_fake_switch(keystates=["UPPER_BUTTON", "LOWER_BUTTON"])
        session = _make_session(switches=[sw])
        hass = _make_setup_hass(session)
        entry = _make_entry()
        add_fn, collected = _collecting_add_fn()

        async def _run():
            with patch(
                "custom_components.bosch_shc.event.SHCEntity.__init__",
                lambda self, device, entry_id: _patch_shc_init(self, device, entry_id),
            ):
                await async_setup_entry(hass, entry, add_fn)

        def _patch_shc_init(self, device, entry_id):
            self._device = device
            self._entry_id = entry_id
            self._attr_name = device.name
            self._attr_unique_id = f"{device.root_device_id}_{device.id}"

        asyncio.run(_run())
        assert len(collected) == 2
        assert all(isinstance(e, UniversalSwitchEvent) for e in collected)

    def test_switch_entity_key_ids_match_keystates(self):
        sw = _make_fake_switch(keystates=["UPPER_BUTTON", "LOWER_BUTTON"])
        session = _make_session(switches=[sw])
        hass = _make_setup_hass(session)
        entry = _make_entry()
        add_fn, collected = _collecting_add_fn()

        async def _run():
            with patch(
                "custom_components.bosch_shc.event.SHCEntity.__init__",
                lambda self, device, entry_id: _patch_shc_init(self, device, entry_id),
            ):
                await async_setup_entry(hass, entry, add_fn)

        def _patch_shc_init(self, device, entry_id):
            self._device = device
            self._entry_id = entry_id
            self._attr_name = device.name
            self._attr_unique_id = f"{device.root_device_id}_{device.id}"

        asyncio.run(_run())
        key_ids = {e._key_id for e in collected}
        assert key_ids == {"UPPER_BUTTON", "LOWER_BUTTON"}

    def test_no_switches_produces_no_switch_entities(self):
        session = _make_session()
        hass = _make_setup_hass(session)
        entry = _make_entry()
        add_fn, collected = _collecting_add_fn()

        async def _run():
            await async_setup_entry(hass, entry, add_fn)

        asyncio.run(_run())
        assert len([e for e in collected if isinstance(e, UniversalSwitchEvent)]) == 0


class TestAsyncSetupEntryScenario:
    """async_setup_entry creates SHCScenarioEvent per scenario."""

    def test_scenario_entity_created(self):
        scn = _make_fake_scenario(name="Night Mode", scenario_id="scn:1")
        session = _make_session(scenarios=[scn])
        hass = _make_setup_hass(session)
        entry = _make_entry()
        add_fn, collected = _collecting_add_fn()

        async def _run():
            await async_setup_entry(hass, entry, add_fn)

        asyncio.run(_run())
        scenario_entities = [e for e in collected if isinstance(e, SHCScenarioEvent)]
        assert len(scenario_entities) == 1

    def test_scenario_entity_name_set(self):
        scn = _make_fake_scenario(name="Away Mode")
        session = _make_session(scenarios=[scn])
        hass = _make_setup_hass(session)
        entry = _make_entry()
        add_fn, collected = _collecting_add_fn()

        async def _run():
            await async_setup_entry(hass, entry, add_fn)

        asyncio.run(_run())
        e = collected[0]
        assert e._attr_name == "Away Mode Scenario"

    def test_two_scenarios_two_entities(self):
        scns = [_make_fake_scenario("A", "scn:A"), _make_fake_scenario("B", "scn:B")]
        session = _make_session(scenarios=scns)
        hass = _make_setup_hass(session)
        entry = _make_entry()
        add_fn, collected = _collecting_add_fn()

        async def _run():
            await async_setup_entry(hass, entry, add_fn)

        asyncio.run(_run())
        assert len(collected) == 2


class TestAsyncSetupEntryMotionAndSmoke:
    """async_setup_entry creates motion / smoke entities."""

    def test_motion_detector_entity_created(self):
        md = _make_fake_motion()
        session = _make_session(motion_detectors=[md])
        hass = _make_setup_hass(session)
        entry = _make_entry()
        add_fn, collected = _collecting_add_fn()

        async def _run():
            with patch(
                "custom_components.bosch_shc.event.SHCEntity.__init__",
                lambda self, device, entry_id: _patch_shc_init(self, device, entry_id),
            ):
                await async_setup_entry(hass, entry, add_fn)

        def _patch_shc_init(self, device, entry_id):
            self._device = device
            self._entry_id = entry_id
            self._attr_name = device.name
            self._attr_unique_id = f"{device.root_device_id}_{device.id}"

        asyncio.run(_run())
        from custom_components.bosch_shc.event import MotionDetectorEvent
        assert any(isinstance(e, MotionDetectorEvent) for e in collected)

    def test_smoke_detection_system_entity_created_when_present(self):
        sys = _make_fake_smoke_system()
        session = _make_session(smoke_detection_system=sys)
        hass = _make_setup_hass(session)
        entry = _make_entry()
        add_fn, collected = _collecting_add_fn()

        async def _run():
            with patch(
                "custom_components.bosch_shc.event.SHCEntity.__init__",
                lambda self, device, entry_id: _patch_shc_init(self, device, entry_id),
            ):
                await async_setup_entry(hass, entry, add_fn)

        def _patch_shc_init(self, device, entry_id):
            self._device = device
            self._entry_id = entry_id
            self._attr_name = device.name
            self._attr_unique_id = f"{device.root_device_id}_{device.id}"

        asyncio.run(_run())
        from custom_components.bosch_shc.event import SmokeDetectionSystemEvent
        assert any(isinstance(e, SmokeDetectionSystemEvent) for e in collected)

    def test_no_smoke_detection_system_when_none(self):
        """Falsy smoke_detection_system → no SmokeDetectionSystemEvent."""
        session = _make_session(smoke_detection_system=None)
        hass = _make_setup_hass(session)
        entry = _make_entry()
        add_fn, collected = _collecting_add_fn()

        async def _run():
            await async_setup_entry(hass, entry, add_fn)

        asyncio.run(_run())
        from custom_components.bosch_shc.event import SmokeDetectionSystemEvent
        assert not any(isinstance(e, SmokeDetectionSystemEvent) for e in collected)

    def test_smoke_detector_entity_created(self):
        sd = _make_fake_smoke_detector()
        session = _make_session(smoke_detectors=[sd])
        hass = _make_setup_hass(session)
        entry = _make_entry()
        add_fn, collected = _collecting_add_fn()

        async def _run():
            with patch(
                "custom_components.bosch_shc.event.SHCEntity.__init__",
                lambda self, device, entry_id: _patch_shc_init(self, device, entry_id),
            ):
                await async_setup_entry(hass, entry, add_fn)

        def _patch_shc_init(self, device, entry_id):
            self._device = device
            self._entry_id = entry_id
            self._attr_name = device.name
            self._attr_unique_id = f"{device.root_device_id}_{device.id}"

        asyncio.run(_run())
        from custom_components.bosch_shc.event import SmokeDetectorEvent
        assert any(isinstance(e, SmokeDetectorEvent) for e in collected)

    def test_async_add_entities_called_with_update_before_add_true(self):
        """async_setup_entry passes True as update_before_add to async_add_entities."""
        session = _make_session()
        hass = _make_setup_hass(session)
        entry = _make_entry()
        calls = []

        def capturing_add(entities, update_before_add=False):
            calls.append((list(entities), update_before_add))

        async def _run():
            await async_setup_entry(hass, entry, capturing_add)

        asyncio.run(_run())
        assert calls, "async_add_entities was never called"
        assert calls[0][1] is True


# ===========================================================================
# B.  UniversalSwitchEvent dedup guards — event.py lines 141, 144, 147-152
# ===========================================================================


def _make_bare_usw(eventtype=_PRESS_SHORT, eventtimestamp=1000,
                   last_fired=-1, device_id="hdm:sw:x"):
    entity = UniversalSwitchEvent.__new__(UniversalSwitchEvent)
    entity._device = SimpleNamespace(
        name="SW",
        id=device_id,
        root_device_id="root:x",
        eventtype=eventtype,
        eventtimestamp=eventtimestamp,
        device_services=[],
        deleted=False,
        manufacturer="Bosch",
        device_model="US",
        status="AVAILABLE",
    )
    entity._key_id = "UPPER_BUTTON"
    entity._last_fired_timestamp = last_fired
    entity._attr_unique_id = f"root:x_{device_id}_UPPER_BUTTON"
    entity.entity_id = "event.sw_button_upper_button"
    entity._trigger_event = MagicMock()
    entity.schedule_update_ha_state = MagicMock()
    # _event_callback dispatches via hass.loop.call_soon_threadsafe; use a
    # synchronous shim so _trigger_event assertions work in unit tests.
    entity.hass = _make_sync_hass()
    return entity


class TestUniversalSwitchEventDedupGuards:
    """Cover the dedup/none/non-press branches in _event_callback."""

    def test_none_eventtype_returns_early_no_trigger(self):
        """eventtype is None → return immediately, _trigger_event not called."""
        entity = _make_bare_usw(eventtype=None)
        entity._event_callback()
        entity._trigger_event.assert_not_called()
        entity.schedule_update_ha_state.assert_not_called()

    def test_switch_on_non_press_type_returns_early(self):
        """SWITCH_ON is not in press types → return early."""
        entity = _make_bare_usw(eventtype=_SWITCH_ON, eventtimestamp=999)
        entity._event_callback()
        entity._trigger_event.assert_not_called()
        entity.schedule_update_ha_state.assert_not_called()

    def test_duplicate_timestamp_skips_event(self):
        """Same eventtimestamp as _last_fired_timestamp → duplicate guard fires, no trigger."""
        entity = _make_bare_usw(eventtype=_PRESS_SHORT, eventtimestamp=500, last_fired=500)
        entity._event_callback()
        entity._trigger_event.assert_not_called()
        entity.schedule_update_ha_state.assert_not_called()

    def test_duplicate_timestamp_does_not_advance_timestamp(self):
        """Duplicate guard: _last_fired_timestamp stays at the existing value."""
        entity = _make_bare_usw(eventtype=_PRESS_SHORT, eventtimestamp=500, last_fired=500)
        entity._event_callback()
        assert entity._last_fired_timestamp == 500

    def test_new_timestamp_advances_last_fired(self):
        """New eventtimestamp (different from last_fired) advances _last_fired_timestamp."""
        entity = _make_bare_usw(eventtype=_PRESS_SHORT, eventtimestamp=1001, last_fired=1000)
        entity._event_callback()
        assert entity._last_fired_timestamp == 1001

    def test_first_press_short_fires_trigger(self):
        """First genuine PRESS_SHORT with fresh timestamp fires _trigger_event."""
        entity = _make_bare_usw(eventtype=_PRESS_SHORT, eventtimestamp=2000, last_fired=-1)
        entity._event_callback()
        entity._trigger_event.assert_called_once()

    def test_non_press_type_motor_switch_on_no_trigger(self):
        """SWITCH_ON (motor event) must not trigger an event entity fire."""
        entity = _make_bare_usw(eventtype=SimpleNamespace(name="SWITCH_OFF"), eventtimestamp=9)
        entity._event_callback()
        entity._trigger_event.assert_not_called()


# ===========================================================================
# C.  SHCScenarioEvent — lines 180-228
# ===========================================================================


def _make_sync_hass(data_extra=None):
    """Return a fake hass whose loop.call_soon_threadsafe executes synchronously.

    SHCScenarioEvent._event_callback schedules _dispatch_event via
    hass.loop.call_soon_threadsafe.  Without a real event loop in unit
    tests, that call would silently drop.  This fake executes the callable
    immediately so _trigger_event assertions work.
    """
    def _sync_call_soon_threadsafe(fn, *args):
        fn(*args)

    fake_loop = SimpleNamespace(call_soon_threadsafe=_sync_call_soon_threadsafe)
    hass = SimpleNamespace(loop=fake_loop)
    if data_extra:
        hass.data = data_extra
    return hass


def _make_scenario_entity(
    scenario_name="Night Mode",
    scenario_id="scn:42",
    unique_id_suffix="uid-shc-001",
    shc_name="SHC Hub",
    shc_device_id="device-shc-entry-1",
):
    """Build SHCScenarioEvent bypassing all HA infrastructure."""
    scenario = SimpleNamespace(name=scenario_name, id=scenario_id)
    session = SimpleNamespace(
        information=SimpleNamespace(unique_id=unique_id_suffix),
        subscribe_scenario_callback=MagicMock(),
    )
    shc_entry = SimpleNamespace(
        name=shc_name,
        id=shc_device_id,
        identifiers={("bosch_shc", "SHC-SERIAL")},
        manufacturer="Bosch",
        model="SHC",
    )
    hass = _make_sync_hass(
        data_extra={
            DOMAIN: {
                "entry1": {
                    DATA_SHC: shc_entry,
                }
            }
        }
    )
    entity = SHCScenarioEvent(scenario, session, hass, entry_id="entry1")
    entity._trigger_event = MagicMock()
    entity.schedule_update_ha_state = MagicMock()
    # SHCScenarioEvent inherits EventEntity (not SHCEntity); the HA infrastructure
    # normally sets entity.hass after async_added_to_hass.  Inject a synchronous
    # shim so _event_callback tests can call the method directly without a real
    # event loop.  (hass passed to __init__ is only used for DATA_SHC lookup.)
    entity.hass = _make_sync_hass()
    return entity, session, shc_entry


class TestSHCScenarioEventInit:
    """SHCScenarioEvent.__init__ wires attributes correctly (lines 180-189)."""

    def test_attr_name_set(self):
        entity, _, _ = _make_scenario_entity(scenario_name="Away Mode")
        assert entity._attr_name == "Away Mode Scenario"

    def test_unique_id_uses_session_uid_and_scenario_id(self):
        entity, _, _ = _make_scenario_entity(
            unique_id_suffix="SHC-UID-123", scenario_id="scn:99"
        )
        assert entity._attr_unique_id == "SHC-UID-123_scn:99"

    def test_entity_id_starts_with_event_dot(self):
        entity, _, _ = _make_scenario_entity(scenario_name="Night Mode")
        assert entity.entity_id.startswith("event.")

    def test_entity_id_contains_scenario_slug(self):
        entity, _, _ = _make_scenario_entity(scenario_name="Night Mode")
        assert "night_mode" in entity.entity_id

    def test_shc_device_entry_stored(self):
        entity, _, shc_entry = _make_scenario_entity()
        assert entity._shc is shc_entry


class TestSHCScenarioEventProperties:
    """device_name, device_id, device_info properties (lines 192-208)."""

    def test_device_name_returns_shc_name(self):
        entity, _, shc_entry = _make_scenario_entity(shc_name="My SHC")
        assert entity.device_name == "My SHC"

    def test_device_id_returns_shc_id(self):
        entity, _, shc_entry = _make_scenario_entity(shc_device_id="dev-abc")
        assert entity.device_id == "dev-abc"

    def test_device_info_identifiers(self):
        entity, _, shc_entry = _make_scenario_entity()
        info = entity.device_info
        assert info["identifiers"] == shc_entry.identifiers

    def test_device_info_name(self):
        entity, _, _ = _make_scenario_entity(shc_name="Hub XY")
        assert entity.device_info["name"] == "Hub XY"

    def test_device_info_manufacturer(self):
        entity, _, _ = _make_scenario_entity()
        assert entity.device_info["manufacturer"] == "Bosch"

    def test_device_info_model(self):
        entity, _, shc_entry = _make_scenario_entity()
        assert entity.device_info["model"] == shc_entry.model


class TestSHCScenarioEventAsyncAddedToHass:
    """async_added_to_hass subscribes scenario callback (lines 211-216)."""

    def test_subscribe_scenario_callback_called_with_scenario_id(self):
        entity, session, _ = _make_scenario_entity(scenario_id="scn:77")

        async def _run():
            with patch(
                "homeassistant.components.event.EventEntity.async_added_to_hass",
                new=AsyncMock(return_value=None),
            ):
                await entity.async_added_to_hass()

        asyncio.run(_run())
        session.subscribe_scenario_callback.assert_called_once()
        call_args = session.subscribe_scenario_callback.call_args[0]
        assert call_args[0] == "scn:77"
        assert callable(call_args[1])

    def test_subscribed_callback_is_event_callback(self):
        """The subscribed callable must be the _event_callback method."""
        entity, session, _ = _make_scenario_entity(scenario_id="scn:88")

        async def _run():
            with patch(
                "homeassistant.components.event.EventEntity.async_added_to_hass",
                new=AsyncMock(return_value=None),
            ):
                await entity.async_added_to_hass()

        asyncio.run(_run())
        registered_cb = session.subscribe_scenario_callback.call_args[0][1]
        # Fire it directly to confirm it's the real _event_callback
        event_data = {"id": "scn:88", "name": "Night Mode", "lastTimeTriggered": "2026-01-01T00:00:00"}
        registered_cb(event_data)
        entity._trigger_event.assert_called_once()


class TestSHCScenarioEventCallback:
    """_event_callback fires the right event + attributes (lines 219-228)."""

    def test_fires_scenario_event_type(self):
        entity, _, _ = _make_scenario_entity()
        entity._event_callback({"id": "scn:1", "name": "Away", "lastTimeTriggered": "ts1"})
        entity._trigger_event.assert_called_once()
        assert entity._trigger_event.call_args[0][0] == "SCENARIO"

    def test_callback_payload_event_type_attr(self):
        entity, _, _ = _make_scenario_entity()
        entity._event_callback({"id": "scn:2", "name": "Night", "lastTimeTriggered": "ts2"})
        attrs = entity._trigger_event.call_args[0][1]
        assert attrs[ATTR_EVENT_TYPE] == "SCENARIO"

    def test_callback_payload_id_attr(self):
        entity, _, _ = _make_scenario_entity()
        entity._event_callback({"id": "scn:42", "name": "Night", "lastTimeTriggered": "ts"})
        attrs = entity._trigger_event.call_args[0][1]
        assert attrs[ATTR_ID] == "scn:42"

    def test_callback_payload_name_attr(self):
        entity, _, _ = _make_scenario_entity()
        entity._event_callback({"id": "scn:3", "name": "Vacation", "lastTimeTriggered": "ts"})
        attrs = entity._trigger_event.call_args[0][1]
        assert attrs[ATTR_NAME] == "Vacation"

    def test_callback_payload_last_time_triggered(self):
        entity, _, _ = _make_scenario_entity()
        entity._event_callback({"id": "scn:4", "name": "X", "lastTimeTriggered": "2026-06-01T10:00:00"})
        attrs = entity._trigger_event.call_args[0][1]
        assert attrs[ATTR_LAST_TIME_TRIGGERED] == "2026-06-01T10:00:00"

    def test_callback_calls_schedule_update(self):
        entity, _, _ = _make_scenario_entity()
        entity._event_callback({"id": "scn:5", "name": "Y", "lastTimeTriggered": "ts5"})
        entity.schedule_update_ha_state.assert_called_once()


# ===========================================================================
# D.  entity.py — async_get_device_id (lines 14-18)
# ===========================================================================


class TestAsyncGetDeviceId:
    """async_get_device_id returns device.id or None via device registry mock."""

    def test_returns_device_id_when_found(self):
        fake_device = SimpleNamespace(id="reg-device-id-42")
        fake_registry = SimpleNamespace(
            async_get_device=MagicMock(return_value=fake_device)
        )

        async def _run():
            with patch(
                "custom_components.bosch_shc.entity.get_dev_reg",
                return_value=fake_registry,
            ):
                return await async_get_device_id(object(), "dev-123")

        result = asyncio.run(_run())
        assert result == "reg-device-id-42"

    def test_returns_none_when_not_found(self):
        fake_registry = SimpleNamespace(
            async_get_device=MagicMock(return_value=None)
        )

        async def _run():
            with patch(
                "custom_components.bosch_shc.entity.get_dev_reg",
                return_value=fake_registry,
            ):
                return await async_get_device_id(object(), "dev-missing")

        result = asyncio.run(_run())
        assert result is None

    def test_passes_correct_identifiers(self):
        fake_registry = SimpleNamespace(
            async_get_device=MagicMock(return_value=None)
        )

        async def _run():
            with patch(
                "custom_components.bosch_shc.entity.get_dev_reg",
                return_value=fake_registry,
            ):
                await async_get_device_id(object(), "dev-xyz")

        asyncio.run(_run())
        call_kwargs = fake_registry.async_get_device.call_args
        assert call_kwargs[1]["identifiers"] == {(DOMAIN, "dev-xyz")}


# ===========================================================================
# E.  entity.py — async_remove_devices (lines 25-30)
# ===========================================================================


class TestAsyncRemoveDevices:
    """async_remove_devices finds device and calls async_update_device."""

    def _make_entity_ns(self, device_id="hdm:dev:1"):
        return SimpleNamespace(device_id=device_id)

    def test_calls_async_update_device_with_remove_entry(self):
        fake_device = SimpleNamespace(id="reg-id-99")
        update_calls = []
        fake_registry = SimpleNamespace(
            async_get_device=MagicMock(return_value=fake_device),
            async_update_device=lambda dev_id, remove_config_entry_id=None: update_calls.append(
                (dev_id, remove_config_entry_id)
            ),
        )
        entity = self._make_entity_ns(device_id="hdm:dev:77")

        async def _run():
            with patch(
                "custom_components.bosch_shc.entity.get_dev_reg",
                return_value=fake_registry,
            ):
                await async_remove_devices(object(), entity, "entry-E1")

        asyncio.run(_run())
        assert update_calls == [("reg-id-99", "entry-E1")]

    def test_no_update_when_device_not_found(self):
        update_calls = []
        fake_registry = SimpleNamespace(
            async_get_device=MagicMock(return_value=None),
            async_update_device=lambda *a, **kw: update_calls.append(a),
        )
        entity = self._make_entity_ns(device_id="hdm:dev:missing")

        async def _run():
            with patch(
                "custom_components.bosch_shc.entity.get_dev_reg",
                return_value=fake_registry,
            ):
                await async_remove_devices(object(), entity, "entry-E1")

        asyncio.run(_run())
        assert update_calls == []


# ===========================================================================
# F.  entity.py — async_migrate_to_new_unique_id (lines 41-66)
# ===========================================================================


class TestAsyncMigrateToNewUniqueId:
    """async_migrate_to_new_unique_id migrates old→new unique_id via entity registry."""

    def _make_device(self, serial="SER-001", dev_id="hdm:dev:1", root_id="root:1"):
        return SimpleNamespace(
            serial=serial,
            id=dev_id,
            root_device_id=root_id,
        )

    def test_entity_found_migrates_unique_id(self):
        ent_registry = MagicMock()
        ent_registry.async_get_entity_id.return_value = "sensor.some_entity"

        async def _run():
            with patch(
                "custom_components.bosch_shc.entity.entity_registry.async_get",
                return_value=ent_registry,
            ):
                dev = self._make_device(serial="OLD-SER", dev_id="hdm:new:id", root_id="root:new")
                await async_migrate_to_new_unique_id(
                    object(), "sensor", dev
                )

        asyncio.run(_run())
        ent_registry.async_update_entity.assert_called_once()
        call_kwargs = ent_registry.async_update_entity.call_args[1]
        assert call_kwargs["new_unique_id"] == "root:new_hdm:new:id"

    def test_entity_not_found_skips_migration(self):
        ent_registry = MagicMock()
        ent_registry.async_get_entity_id.return_value = None

        async def _run():
            with patch(
                "custom_components.bosch_shc.entity.entity_registry.async_get",
                return_value=ent_registry,
            ):
                dev = self._make_device()
                await async_migrate_to_new_unique_id(object(), "sensor", dev)

        asyncio.run(_run())
        ent_registry.async_update_entity.assert_not_called()

    def test_with_attr_name_appends_lowercase(self):
        ent_registry = MagicMock()
        ent_registry.async_get_entity_id.return_value = "sensor.with_attr"

        async def _run():
            with patch(
                "custom_components.bosch_shc.entity.entity_registry.async_get",
                return_value=ent_registry,
            ):
                dev = self._make_device(serial="SER", dev_id="hdm:d:1", root_id="root:r")
                await async_migrate_to_new_unique_id(
                    object(), "sensor", dev, attr_name="Temperature"
                )

        asyncio.run(_run())
        call_kwargs = ent_registry.async_update_entity.call_args[1]
        assert call_kwargs["new_unique_id"] == "root:r_hdm:d:1_temperature"

    def test_value_error_on_update_logs_warning(self):
        """ValueError from async_update_entity logs a warning, does not raise."""
        ent_registry = MagicMock()
        ent_registry.async_get_entity_id.return_value = "sensor.duplicate"
        ent_registry.async_update_entity.side_effect = ValueError("already exists")

        async def _run():
            with patch(
                "custom_components.bosch_shc.entity.entity_registry.async_get",
                return_value=ent_registry,
            ):
                dev = self._make_device()
                await async_migrate_to_new_unique_id(object(), "sensor", dev)

        asyncio.run(_run())  # must not raise

    def test_old_unique_id_override_used_for_lookup(self):
        ent_registry = MagicMock()
        ent_registry.async_get_entity_id.return_value = None

        async def _run():
            with patch(
                "custom_components.bosch_shc.entity.entity_registry.async_get",
                return_value=ent_registry,
            ):
                dev = self._make_device(serial="SER-X")
                await async_migrate_to_new_unique_id(
                    object(), "sensor", dev, old_unique_id="my-custom-old-id"
                )

        asyncio.run(_run())
        ent_registry.async_get_entity_id.assert_called_once_with(
            "sensor", DOMAIN, "my-custom-old-id"
        )


# ===========================================================================
# G.  entity.py — SHCEntity._update_attr (line 85) + else-branch (99-100)
# ===========================================================================


class TestSHCEntityUpdateAttr:
    """_update_attr default implementation is a no-op pass (line 85)."""

    def test_update_attr_is_noop(self):
        ent = SHCEntity.__new__(SHCEntity)
        ent._device = SimpleNamespace(
            name="Dev",
            id="d1",
            root_device_id="r1",
            manufacturer="Bosch",
            device_model="M",
            status="AVAILABLE",
            deleted=False,
            device_services=[],
        )
        ent._attr_name = "Dev"
        ent._attr_unique_id = "r1_d1"
        # Call must not raise and must return None
        result = ent._update_attr()
        assert result is None


class TestUpdateEntityInformationElseBranch:
    """update_entity_information else-branch (device.deleted is False) — lines 99-100."""

    class TrackingEntity(SHCEntity):
        def __init__(self):
            pass  # skip SHCEntity.__init__

        def _update_attr(self):
            self.update_attr_calls = getattr(self, "update_attr_calls", 0) + 1

        def schedule_update_ha_state(self, force_refresh=False):
            self.schedule_calls = getattr(self, "schedule_calls", 0) + 1

    def test_not_deleted_calls_update_attr_and_schedule(self):
        """When device.deleted is False, else-branch calls _update_attr + schedule."""
        dev_callbacks = []
        ent = self.TrackingEntity()
        ent._device = SimpleNamespace(
            name="Dev",
            id="dev1",
            root_device_id="root1",
            manufacturer="Bosch",
            device_model="M",
            status="AVAILABLE",
            deleted=False,
            device_services=[],
            subscribe_callback=lambda eid, cb: dev_callbacks.append(cb),
            unsubscribe_callback=lambda eid: None,
        )
        ent._entry_id = "entry1"
        ent._attr_name = "Dev"
        ent._attr_unique_id = "root1_dev1"
        ent.entity_id = "switch.test"
        ent.update_attr_calls = 0
        ent.schedule_calls = 0

        with patch(
            "homeassistant.helpers.entity.Entity.async_added_to_hass",
            new=AsyncMock(return_value=None),
        ):
            asyncio.run(ent.async_added_to_hass())

        assert dev_callbacks, "Expected device subscribe_callback to be registered"
        update_entity_information = dev_callbacks[0]

        # Trigger the else-branch: device is NOT deleted
        update_entity_information()
        assert ent.update_attr_calls >= 1
        assert ent.schedule_calls >= 1

    def test_not_deleted_does_not_call_hass_add_job(self):
        """When device.deleted is False, hass.add_job must NOT be called."""
        dev_callbacks = []
        add_job_calls = []
        ent = self.TrackingEntity()
        ent._device = SimpleNamespace(
            name="Dev",
            id="dev2",
            root_device_id="root2",
            manufacturer="Bosch",
            device_model="M",
            status="AVAILABLE",
            deleted=False,
            device_services=[],
            subscribe_callback=lambda eid, cb: dev_callbacks.append(cb),
            unsubscribe_callback=lambda eid: None,
        )
        ent._entry_id = "entry1"
        ent._attr_name = "Dev"
        ent._attr_unique_id = "root2_dev2"
        ent.entity_id = "switch.test2"
        ent.update_attr_calls = 0
        ent.schedule_calls = 0
        ent.hass = SimpleNamespace(add_job=lambda *a: add_job_calls.append(a))

        with patch(
            "homeassistant.helpers.entity.Entity.async_added_to_hass",
            new=AsyncMock(return_value=None),
        ):
            asyncio.run(ent.async_added_to_hass())

        update_entity_information = dev_callbacks[0]
        update_entity_information()
        assert add_job_calls == [], "hass.add_job must NOT be called for non-deleted device"
