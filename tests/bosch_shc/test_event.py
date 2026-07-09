"""Unit tests for the event platform (custom_components.bosch_shc.event).

Covers async_setup_entry (including per-device-type OPT_EXCLUDED_DEVICES
filtering), every EventEntity subclass (UniversalSwitchEvent,
MotionDetectorEvent, SmokeDetectionSystemEvent, SmokeDetectorEvent,
SHCScenarioEvent, LightControlButtonEvent) - their __init__ wiring,
async_added_to_hass subscribe wiring, async_will_remove_from_hass
unsubscribe wiring, _event_callback dedup/guard logic, _dispatch_event
attribute payloads, device_* properties - plus a few entity.py helpers
(async_get_device_id, async_remove_devices, async_migrate_to_new_unique_id,
SHCEntity._update_attr) that were historically covered alongside event.py,
and slugify-based entity_id validity checks for Bosch device/scenario names.

Pattern: this is a pure-unit test suite. Most tests bypass __init__ via
Cls.__new__(Cls) + SimpleNamespace/MagicMock mocks; a few exercise the real
__init__ directly. No HA test harness (run with -p no:homeassistant).
asyncio.run() drives coroutines (each gets a fresh event loop, Python 3.14
requires this - get_event_loop() raises RuntimeError with no loop set).
"""

from __future__ import annotations

import asyncio
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import ATTR_DEVICE_ID, ATTR_ID, ATTR_NAME
from homeassistant.util import slugify

from boschshcpy.services_impl import KeypadService

from custom_components.bosch_shc.const import (
    ATTR_EVENT_SUBTYPE,
    ATTR_EVENT_TYPE,
    ATTR_LAST_TIME_TRIGGERED,
    DOMAIN,
    OPT_EXCLUDED_DEVICES,
)
from custom_components.bosch_shc.entity import (
    SHCEntity,
    async_get_device_id,
    async_migrate_to_new_unique_id,
    async_remove_devices,
)
from custom_components.bosch_shc.event import (
    LightControlButtonEvent,
    MotionDetectorEvent,
    SHCScenarioEvent,
    SmokeDetectionSystemEvent,
    SmokeDetectorEvent,
    UniversalSwitchEvent,
    async_setup_entry,
)

# ---------------------------------------------------------------------------
# Shared sentinel event-type values
# ---------------------------------------------------------------------------

_PRESS_SHORT = SimpleNamespace(name="PRESS_SHORT")
_PRESS_LONG = SimpleNamespace(name="PRESS_LONG")
_PRESS_LONG_RELEASED = SimpleNamespace(name="PRESS_LONG_RELEASED")
_SWITCH_ON = SimpleNamespace(name="SWITCH_ON")
_SWITCH_OFF = SimpleNamespace(name="SWITCH_OFF")

_SHC_ENTITY_ADDED = "custom_components.bosch_shc.event.SHCEntity.async_added_to_hass"
_SHC_ENTITY_WILL_REMOVE = (
    "custom_components.bosch_shc.event.SHCEntity.async_will_remove_from_hass"
)
_EVENTENTITY_ADDED = "homeassistant.components.event.EventEntity.async_added_to_hass"


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Generic hass fakes
# ---------------------------------------------------------------------------

def _make_hass_direct():
    """Hass whose call_soon_threadsafe executes fn synchronously."""
    hass = MagicMock(name="hass")

    def _sync_call(fn, *args, **kwargs):
        fn(*args, **kwargs)

    hass.loop.call_soon_threadsafe.side_effect = _sync_call
    return hass


def _make_hass_capturing():
    """Hass that captures call_soon_threadsafe args without executing."""
    hass = MagicMock(name="hass")
    hass.loop = MagicMock(name="loop")
    return hass


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


def _make_sync_hass(shc_device=None):
    """Return a fake hass whose loop.call_soon_threadsafe executes synchronously.

    SHCScenarioEvent._event_callback schedules _dispatch_event via
    hass.loop.call_soon_threadsafe.  Without a real event loop in unit
    tests, that call would silently drop.  This fake executes the callable
    immediately so _trigger_event assertions work.

    When `shc_device` is given, hass.config_entries.async_get_entry(...)
    resolves to a fake config entry carrying it on runtime_data.shc_device -
    read directly by SHCScenarioEvent.__init__.
    """
    def _sync_call_soon_threadsafe(fn, *args):
        fn(*args)

    fake_loop = SimpleNamespace(call_soon_threadsafe=_sync_call_soon_threadsafe)
    hass = SimpleNamespace(loop=fake_loop)
    if shc_device is not None:
        fake_lookup_entry = SimpleNamespace(
            runtime_data=SimpleNamespace(shc_device=shc_device)
        )
        hass.config_entries = SimpleNamespace(
            async_get_entry=lambda eid: fake_lookup_entry
        )
    return hass


# ---------------------------------------------------------------------------
# Shared fixture helpers (async_setup_entry style: hass/entry with
# runtime_data.session, plus a generic fake device)
# ---------------------------------------------------------------------------

def _fake_dev(dev_id="dev1", root_id="root1", serial="SER1", **kw):
    base = dict(
        id=dev_id,
        root_device_id=root_id,
        name="FakeDev",
        serial=serial,
        device_services=[],
        room_id=None,
        deleted=False,
        status="AVAILABLE",
        manufacturer="Bosch",
        device_model="TestModel",
        subscribe_callback=MagicMock(),
        unsubscribe_callback=MagicMock(),
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _fake_hass(entry_id="E1", session=None, shc=None, options=None):
    """Minimal hass. session/shc are cached so a paired _fake_entry(hass=...)
    call can wire them onto entry.runtime_data (the modern storage location -
    this integration no longer uses hass.data[DOMAIN])."""
    shc_obj = shc or SimpleNamespace(
        identifiers={("bosch_shc", "shc")},
        name="SHC", manufacturer="Bosch", model="SHC", id="shc1",
    )
    h = MagicMock()
    h.data = {}
    h._fake_session = session
    h._fake_shc = shc_obj

    async def _executor_job(fn, *args):
        return fn(*args)

    h.async_add_executor_job = _executor_job
    h.config_entries = MagicMock()
    h.bus = MagicMock()
    h.bus.async_listen_once = MagicMock(return_value=MagicMock())
    h.async_create_task = MagicMock()
    return h


def _fake_entry(entry_id="E1", title="Test SHC", options=None, hass=None):
    """Build a fake config entry with runtime_data wired from `hass` (as
    produced by _fake_hass) when provided."""
    entry = MagicMock()
    entry.entry_id = entry_id
    entry.title = title
    entry.options = options or {}
    entry.unique_id = "uid1"
    entry.async_on_unload = MagicMock()
    entry.runtime_data = SimpleNamespace(
        session=getattr(hass, "_fake_session", None) if hass is not None else None,
        shc_device=getattr(hass, "_fake_shc", None) if hass is not None else None,
        title=title,
    )
    return entry


# ===========================================================================
# async_setup_entry — entity creation
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


def _make_setup_hass(shc_entry=None, entry_id="entry1"):
    """Return a fake hass whose config_entries.async_get_entry(entry_id)
    resolves to runtime_data.shc_device - read by SHCScenarioEvent.__init__
    when async_setup_entry creates scenario entities."""
    shc = shc_entry or _make_fake_shc_device_entry()
    fake_lookup_entry = SimpleNamespace(runtime_data=SimpleNamespace(shc_device=shc))
    return SimpleNamespace(
        config_entries=SimpleNamespace(
            async_get_entry=lambda eid: fake_lookup_entry
        )
    )


def _make_entry(session, entry_id="entry1", shc_entry=None):
    """Build a fake config entry with runtime_data.session - read directly
    by event.py's async_setup_entry (not via hass.data)."""
    shc = shc_entry or _make_fake_shc_device_entry()
    return SimpleNamespace(
        options={},
        entry_id=entry_id,
        runtime_data=SimpleNamespace(session=session, shc_device=shc, title="Test SHC"),
    )


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
        hass = _make_setup_hass()
        entry = _make_entry(session)
        add_fn, collected = _collecting_add_fn()

        async def _run_setup():
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

        asyncio.run(_run_setup())
        assert len(collected) == 2
        assert all(isinstance(e, UniversalSwitchEvent) for e in collected)

    def test_switch_entity_key_ids_match_keystates(self):
        sw = _make_fake_switch(keystates=["UPPER_BUTTON", "LOWER_BUTTON"])
        session = _make_session(switches=[sw])
        hass = _make_setup_hass()
        entry = _make_entry(session)
        add_fn, collected = _collecting_add_fn()

        async def _run_setup():
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

        asyncio.run(_run_setup())
        key_ids = {e._key_id for e in collected}
        assert key_ids == {"UPPER_BUTTON", "LOWER_BUTTON"}

    def test_no_switches_produces_no_switch_entities(self):
        session = _make_session()
        hass = _make_setup_hass()
        entry = _make_entry(session)
        add_fn, collected = _collecting_add_fn()

        async def _run_setup():
            await async_setup_entry(hass, entry, add_fn)

        asyncio.run(_run_setup())
        assert len([e for e in collected if isinstance(e, UniversalSwitchEvent)]) == 0


class TestAsyncSetupEntryScenario:
    """async_setup_entry creates SHCScenarioEvent per scenario."""

    def test_scenario_entity_created(self):
        scn = _make_fake_scenario(name="Night Mode", scenario_id="scn:1")
        session = _make_session(scenarios=[scn])
        hass = _make_setup_hass()
        entry = _make_entry(session)
        add_fn, collected = _collecting_add_fn()

        async def _run_setup():
            await async_setup_entry(hass, entry, add_fn)

        asyncio.run(_run_setup())
        scenario_entities = [e for e in collected if isinstance(e, SHCScenarioEvent)]
        assert len(scenario_entities) == 1

    def test_scenario_entity_name_set(self):
        scn = _make_fake_scenario(name="Away Mode")
        session = _make_session(scenarios=[scn])
        hass = _make_setup_hass()
        entry = _make_entry(session)
        add_fn, collected = _collecting_add_fn()

        async def _run_setup():
            await async_setup_entry(hass, entry, add_fn)

        asyncio.run(_run_setup())
        e = collected[0]
        assert e._attr_name == "Away Mode Scenario"

    def test_two_scenarios_two_entities(self):
        scns = [_make_fake_scenario("A", "scn:A"), _make_fake_scenario("B", "scn:B")]
        session = _make_session(scenarios=scns)
        hass = _make_setup_hass()
        entry = _make_entry(session)
        add_fn, collected = _collecting_add_fn()

        async def _run_setup():
            await async_setup_entry(hass, entry, add_fn)

        asyncio.run(_run_setup())
        assert len(collected) == 2


class TestAsyncSetupEntryMotionAndSmoke:
    """async_setup_entry creates motion / smoke entities."""

    def test_motion_detector_entity_created(self):
        md = _make_fake_motion()
        session = _make_session(motion_detectors=[md])
        hass = _make_setup_hass()
        entry = _make_entry(session)
        add_fn, collected = _collecting_add_fn()

        async def _run_setup():
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

        asyncio.run(_run_setup())
        assert any(isinstance(e, MotionDetectorEvent) for e in collected)

    def test_smoke_detection_system_entity_created_when_present(self):
        sys_dev = _make_fake_smoke_system()
        session = _make_session(smoke_detection_system=sys_dev)
        hass = _make_setup_hass()
        entry = _make_entry(session)
        add_fn, collected = _collecting_add_fn()

        async def _run_setup():
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

        asyncio.run(_run_setup())
        assert any(isinstance(e, SmokeDetectionSystemEvent) for e in collected)

    def test_no_smoke_detection_system_when_none(self):
        """Falsy smoke_detection_system -> no SmokeDetectionSystemEvent."""
        session = _make_session(smoke_detection_system=None)
        hass = _make_setup_hass()
        entry = _make_entry(session)
        add_fn, collected = _collecting_add_fn()

        async def _run_setup():
            await async_setup_entry(hass, entry, add_fn)

        asyncio.run(_run_setup())
        assert not any(isinstance(e, SmokeDetectionSystemEvent) for e in collected)

    def test_smoke_detector_entity_created(self):
        sd = _make_fake_smoke_detector()
        session = _make_session(smoke_detectors=[sd])
        hass = _make_setup_hass()
        entry = _make_entry(session)
        add_fn, collected = _collecting_add_fn()

        async def _run_setup():
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

        asyncio.run(_run_setup())
        assert any(isinstance(e, SmokeDetectorEvent) for e in collected)

    def test_async_add_entities_called_with_update_before_add_true(self):
        """async_setup_entry passes True as update_before_add to async_add_entities."""
        session = _make_session()
        hass = _make_setup_hass()
        entry = _make_entry(session)
        calls = []

        def capturing_add(entities, update_before_add=False):
            calls.append((list(entities), update_before_add))

        async def _run_setup():
            await async_setup_entry(hass, entry, capturing_add)

        asyncio.run(_run_setup())
        assert calls, "async_add_entities was never called"
        assert calls[0][1] is True


# ---------------------------------------------------------------------------
# async_setup_entry — OPT_EXCLUDED_DEVICES filtering, per device type
# ---------------------------------------------------------------------------


def _make_switch_device(device_id="sw-1", room_id="room-1", keystates=None):
    """Device double for a universal switch."""
    return SimpleNamespace(
        id=device_id,
        root_device_id="shc-root",
        room_id=room_id,
        name="Universal Switch",
        serial=f"serial-{device_id}",
        manufacturer="Bosch",
        device_model="SW1",
        status="AVAILABLE",
        deleted=False,
        device_services=[],
        subscribe_callback=lambda eid, cb: None,
        unsubscribe_callback=lambda eid: None,
        keystates=keystates if keystates is not None else ["KEY1"],
    )


def _make_motion_device(device_id="md-1", room_id="room-2"):
    """Device double for a motion detector."""
    return SimpleNamespace(
        id=device_id,
        root_device_id="shc-root",
        room_id=room_id,
        name="Motion Detector",
        serial=f"serial-{device_id}",
        manufacturer="Bosch",
        device_model="MD1",
        status="AVAILABLE",
        deleted=False,
        device_services=[],
        subscribe_callback=lambda eid, cb: None,
        unsubscribe_callback=lambda eid: None,
    )


def _make_smoke_detector(device_id="sd-1", room_id="room-3"):
    """Device double for a smoke detector."""
    return SimpleNamespace(
        id=device_id,
        root_device_id="shc-root",
        room_id=room_id,
        name="Smoke Detector",
        serial=f"serial-{device_id}",
        manufacturer="Bosch",
        device_model="SD1",
        status="AVAILABLE",
        deleted=False,
        device_services=[],
        subscribe_callback=lambda eid, cb: None,
        unsubscribe_callback=lambda eid: None,
    )


def _make_hass_and_entry(
    universal_switches=None,
    motion_detectors=None,
    motion_detectors2=None,
    smoke_detectors=None,
    smoke_detection_system=None,
    scenarios=None,
    excluded_device_ids=None,
):
    """Return (hass, config_entry) with a faked session and options."""
    universal_switches = universal_switches or []
    motion_detectors = motion_detectors or []
    motion_detectors2 = motion_detectors2 or []
    smoke_detectors = smoke_detectors or []
    scenarios = scenarios or []
    excluded = excluded_device_ids or []

    session = SimpleNamespace(
        device_helper=SimpleNamespace(
            universal_switches=universal_switches,
            motion_detectors=motion_detectors,
            motion_detectors2=motion_detectors2,
            smoke_detectors=smoke_detectors,
            smoke_detection_system=smoke_detection_system,
        ),
        scenarios=scenarios,
    )

    entry_id = "entry-event"
    options = {OPT_EXCLUDED_DEVICES: excluded}
    hass = SimpleNamespace()
    config_entry = SimpleNamespace(entry_id=entry_id, options=options)
    config_entry.runtime_data = SimpleNamespace(session=session, shc_device=None, title="Test SHC")
    return hass, config_entry


def _run_setup(hass, config_entry):
    """Run async_setup_entry synchronously, return the entities list."""
    added = []

    def _add(entities, update_before_add=False):
        added.extend(entities)

    asyncio.run(async_setup_entry(hass, config_entry, _add))
    return added


class TestUniversalSwitchExcluded:
    def test_excluded_switch_not_added(self):
        """Excluded switch device (line 57) must not produce any UniversalSwitchEvent."""
        dev = _make_switch_device(device_id="excl-sw", keystates=["KEY1", "KEY2"])
        hass, entry = _make_hass_and_entry(
            universal_switches=[dev],
            excluded_device_ids=["excl-sw"],
        )
        added = _run_setup(hass, entry)
        sw_events = [e for e in added if isinstance(e, UniversalSwitchEvent)]
        # No events for the excluded device
        assert all(
            e._device is not dev for e in sw_events
        ), "Excluded switch should not produce UniversalSwitchEvent entities"

    def test_non_excluded_switch_produces_events(self):
        """Non-excluded switch must produce one UniversalSwitchEvent per keystate."""
        dev = _make_switch_device(device_id="keep-sw", keystates=["KEY1", "KEY2"])
        hass, entry = _make_hass_and_entry(
            universal_switches=[dev],
            excluded_device_ids=[],
        )
        added = _run_setup(hass, entry)
        sw_events = [e for e in added if isinstance(e, UniversalSwitchEvent)]
        assert len(sw_events) == 2, (
            f"Expected 2 UniversalSwitchEvent (one per keystate), got {len(sw_events)}"
        )

    def test_mixed_switches_only_excluded_is_skipped(self):
        """One excluded switch + one non-excluded: only non-excluded events appear."""
        keep = _make_switch_device(device_id="sw-keep", keystates=["K1"])
        excl = _make_switch_device(device_id="sw-excl", keystates=["K1"])
        hass, entry = _make_hass_and_entry(
            universal_switches=[keep, excl],
            excluded_device_ids=["sw-excl"],
        )
        added = _run_setup(hass, entry)
        sw_events = [e for e in added if isinstance(e, UniversalSwitchEvent)]
        assert all(e._device is not excl for e in sw_events)
        assert any(e._device is keep for e in sw_events)


class TestMotionDetectorExcluded:
    def test_excluded_motion_detector_not_added(self):
        """Excluded motion detector (line 83) must not produce a MotionDetectorEvent."""
        dev = _make_motion_device(device_id="excl-md")
        hass, entry = _make_hass_and_entry(
            motion_detectors=[dev],
            excluded_device_ids=["excl-md"],
        )
        added = _run_setup(hass, entry)
        md_events = [e for e in added if isinstance(e, MotionDetectorEvent)]
        assert all(
            e._device is not dev for e in md_events
        ), "Excluded motion detector should not produce MotionDetectorEvent"

    def test_non_excluded_motion_detector_is_added(self):
        """Non-excluded motion detector must produce a MotionDetectorEvent."""
        dev = _make_motion_device(device_id="keep-md")
        hass, entry = _make_hass_and_entry(
            motion_detectors=[dev],
            excluded_device_ids=[],
        )
        added = _run_setup(hass, entry)
        md_events = [e for e in added if isinstance(e, MotionDetectorEvent)]
        assert any(
            e._device is dev for e in md_events
        ), "Non-excluded motion detector should produce a MotionDetectorEvent"

    def test_excluded_motion_detector2_not_added(self):
        """Excluded MD2 (also covered by line 83 via combined list) must be skipped."""
        dev = _make_motion_device(device_id="excl-md2")
        hass, entry = _make_hass_and_entry(
            motion_detectors2=[dev],
            excluded_device_ids=["excl-md2"],
        )
        added = _run_setup(hass, entry)
        md_events = [e for e in added if isinstance(e, MotionDetectorEvent)]
        assert all(
            e._device is not dev for e in md_events
        ), "Excluded MD2 should not produce a MotionDetectorEvent"

    def test_non_excluded_motion_detector2_is_added(self):
        """Non-excluded MD2 must produce a MotionDetectorEvent."""
        dev = _make_motion_device(device_id="keep-md2")
        hass, entry = _make_hass_and_entry(
            motion_detectors2=[dev],
            excluded_device_ids=[],
        )
        added = _run_setup(hass, entry)
        md_events = [e for e in added if isinstance(e, MotionDetectorEvent)]
        assert any(
            e._device is dev for e in md_events
        ), "Non-excluded MD2 should produce a MotionDetectorEvent"

    def test_mixed_motion_detectors_only_excluded_skipped(self):
        """One excluded + one non-excluded MD: only non-excluded appears."""
        keep = _make_motion_device(device_id="md-keep")
        excl = _make_motion_device(device_id="md-excl")
        hass, entry = _make_hass_and_entry(
            motion_detectors=[keep, excl],
            excluded_device_ids=["md-excl"],
        )
        added = _run_setup(hass, entry)
        md_events = [e for e in added if isinstance(e, MotionDetectorEvent)]
        assert all(e._device is not excl for e in md_events)
        assert any(e._device is keep for e in md_events)


class TestSmokeDetectorExcluded:
    def test_excluded_smoke_detector_not_added(self):
        """Excluded smoke detector (line 104) must not produce a SmokeDetectorEvent."""
        dev = _make_smoke_detector(device_id="excl-sd")
        hass, entry = _make_hass_and_entry(
            smoke_detectors=[dev],
            excluded_device_ids=["excl-sd"],
        )
        added = _run_setup(hass, entry)
        sd_events = [e for e in added if isinstance(e, SmokeDetectorEvent)]
        assert all(
            e._device is not dev for e in sd_events
        ), "Excluded smoke detector should not produce SmokeDetectorEvent"

    def test_non_excluded_smoke_detector_is_added(self):
        """Non-excluded smoke detector must produce a SmokeDetectorEvent."""
        dev = _make_smoke_detector(device_id="keep-sd")
        hass, entry = _make_hass_and_entry(
            smoke_detectors=[dev],
            excluded_device_ids=[],
        )
        added = _run_setup(hass, entry)
        sd_events = [e for e in added if isinstance(e, SmokeDetectorEvent)]
        assert any(
            e._device is dev for e in sd_events
        ), "Non-excluded smoke detector should produce a SmokeDetectorEvent"

    def test_mixed_smoke_detectors_only_excluded_skipped(self):
        """One excluded + one non-excluded SD: only non-excluded appears."""
        keep = _make_smoke_detector(device_id="sd-keep")
        excl = _make_smoke_detector(device_id="sd-excl")
        hass, entry = _make_hass_and_entry(
            smoke_detectors=[keep, excl],
            excluded_device_ids=["sd-excl"],
        )
        added = _run_setup(hass, entry)
        sd_events = [e for e in added if isinstance(e, SmokeDetectorEvent)]
        assert all(e._device is not excl for e in sd_events)
        assert any(e._device is keep for e in sd_events)

    def test_excluded_smoke_detector_alongside_non_excluded(self):
        """Regression: excluding one SD must not affect the other in the same list."""
        keep1 = _make_smoke_detector(device_id="sd-a")
        keep2 = _make_smoke_detector(device_id="sd-b")
        excl = _make_smoke_detector(device_id="sd-excl")
        hass, entry = _make_hass_and_entry(
            smoke_detectors=[keep1, excl, keep2],
            excluded_device_ids=["sd-excl"],
        )
        added = _run_setup(hass, entry)
        sd_events = [e for e in added if isinstance(e, SmokeDetectorEvent)]
        device_ids = {e._device.id for e in sd_events}
        assert "sd-a" in device_ids
        assert "sd-b" in device_ids
        assert "sd-excl" not in device_ids


# ---------------------------------------------------------------------------
# async_setup_entry — micromodule_light_controls loop (excluded / no-keypad)
# ---------------------------------------------------------------------------


class TestEventSetupLightControls:
    """Lines 82-86: micromodule_light_controls loop - excluded and no-keypad branches."""

    def _run_event_setup(self, light_controls, options=None):
        dh = MagicMock()
        dh.universal_switches = []
        dh.motion_detectors = []
        dh.motion_detectors2 = []
        dh.smoke_detectors = []
        dh.micromodule_light_controls = light_controls

        session = MagicMock()
        session.device_helper = dh
        session.scenarios = []
        session.device_helper.smoke_detection_system = None

        hass = _fake_hass(session=session)
        entry = _fake_entry(hass=hass, options=options or {})

        collected = []
        _run(async_setup_entry(hass, entry, lambda ents, *a, **kw: collected.extend(ents)))
        return collected

    def test_light_control_excluded(self):
        """Line 82-83: device_excluded=True -> continue."""
        dev = _fake_dev("lc1", has_keypad=True)
        collected = self._run_event_setup(
            light_controls=[dev],
            options={OPT_EXCLUDED_DEVICES: ["lc1"]},
        )
        assert len(collected) == 0

    def test_light_control_no_keypad(self):
        """Lines 84-85: has_keypad=False -> continue."""
        dev = _fake_dev("lc1")  # no has_keypad attr -> getattr returns False
        collected = self._run_event_setup(light_controls=[dev])
        assert len(collected) == 0

    def test_light_control_with_keypad_added(self):
        """Lines 86-90: has_keypad=True -> LightControlButtonEvent added."""
        dev = _fake_dev("lc1", has_keypad=True)
        dev.root_device_id = "root1"
        dev.name = "LightControl"
        collected = self._run_event_setup(light_controls=[dev])
        assert any(isinstance(e, LightControlButtonEvent) for e in collected)


# ===========================================================================
# UniversalSwitchEvent
# ===========================================================================


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

    def test_last_fired_timestamp_initialized_to_minus_one(self):
        dev = self._make_dev()
        entity = UniversalSwitchEvent.__new__(UniversalSwitchEvent)
        with patch.object(UniversalSwitchEvent, "_update_attr", lambda self: None):
            UniversalSwitchEvent.__init__(entity, dev, "entry1", "UPPER_BUTTON")
        assert entity._last_fired_timestamp == -1

    def test_lower_button_key_id_in_name_and_uid(self):
        dev = self._make_dev(name="Hallway SW", device_id="hdm:sw:5", root_device_id="root:5")
        entity = UniversalSwitchEvent.__new__(UniversalSwitchEvent)
        with patch.object(UniversalSwitchEvent, "_update_attr", lambda self: None):
            UniversalSwitchEvent.__init__(entity, dev, "entry1", "LOWER_BUTTON")
        assert "LOWER_BUTTON" in entity._attr_name
        assert entity._attr_unique_id.endswith("_LOWER_BUTTON")


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

        async def _run_added():
            with patch(_SHC_ENTITY_ADDED, return_value=None):
                await UniversalSwitchEvent.async_added_to_hass(entity)

        asyncio.run(_run_added())
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

        async def _run_added():
            with patch(_SHC_ENTITY_ADDED, return_value=None):
                await UniversalSwitchEvent.async_added_to_hass(entity)

        asyncio.run(_run_added())
        assert "LOWER_BUTTON" in keypad_svc.registered
        # Battery service has no register_event attribute -> correctly skipped
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

        async def _run_added():
            with patch(_SHC_ENTITY_ADDED, return_value=None):
                await UniversalSwitchEvent.async_added_to_hass(entity)

        asyncio.run(_run_added())
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

        async def _run_added():
            with patch(_SHC_ENTITY_ADDED, return_value=None):
                await UniversalSwitchEvent.async_added_to_hass(entity)

        asyncio.run(_run_added())  # must not raise


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


class TestUniversalSwitchEventDispatch:
    """_event_callback must call _dispatch_event directly (no call_soon_threadsafe)."""

    def _make_entity(self):
        entity = UniversalSwitchEvent.__new__(UniversalSwitchEvent)
        entity.hass = _make_hass_capturing()
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
    entity.hass = _make_hass_sync()
    return entity


class TestUniversalSwitchEventDedupGuards:
    """Cover the dedup/none/non-press branches in _event_callback."""

    def test_none_eventtype_returns_early_no_trigger(self):
        """Eventtype is None -> return immediately, _trigger_event not called."""
        entity = _make_bare_usw(eventtype=None)
        entity._event_callback()
        entity._trigger_event.assert_not_called()
        entity.schedule_update_ha_state.assert_not_called()

    def test_switch_on_non_press_type_returns_early(self):
        """SWITCH_ON is not in press types -> return early."""
        entity = _make_bare_usw(eventtype=_SWITCH_ON, eventtimestamp=999)
        entity._event_callback()
        entity._trigger_event.assert_not_called()
        entity.schedule_update_ha_state.assert_not_called()

    def test_duplicate_timestamp_skips_event(self):
        """Same eventtimestamp as _last_fired_timestamp -> duplicate guard fires, no trigger."""
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


KEY_ID = "UPPER_BUTTON"


def _make_entity(eventtype, eventtimestamp, root_device_id="root1", device_id="dev1"):
    """Build a UniversalSwitchEvent bypassing SHCEntity.__init__.

    Used by the #192 phantom-event regression suite below (event type guard,
    timestamp dedup guard, happy-path press types, battery-update replay).
    """
    entity = UniversalSwitchEvent.__new__(UniversalSwitchEvent)

    entity._device = SimpleNamespace(
        name="Test Switch",
        id=device_id,
        root_device_id=root_device_id,
        eventtype=eventtype,
        eventtimestamp=eventtimestamp,
    )
    entity._key_id = KEY_ID
    entity._last_fired_timestamp = -1
    entity._attr_unique_id = f"{root_device_id}_{device_id}_{KEY_ID}"
    entity.entity_id = f"event.test_switch_button_{KEY_ID.lower()}"

    # HA EventEntity methods we need to observe / stub
    entity._trigger_event = MagicMock()
    entity.schedule_update_ha_state = MagicMock()
    # device_id is a property on SHCEntity reading _device.id - already set above

    # Wire up a sync hass so call_soon_threadsafe executes immediately in tests
    entity.hass = _make_hass_sync()

    return entity


class TestEventTypeGuard:
    """Non-press eventtype values must never produce a HA event (#192)."""

    def test_switch_on_does_not_fire(self):
        entity = _make_entity(eventtype=_SWITCH_ON, eventtimestamp=1000)
        entity._event_callback()
        entity._trigger_event.assert_not_called()
        entity.schedule_update_ha_state.assert_not_called()

    def test_switch_off_does_not_fire(self):
        entity = _make_entity(eventtype=_SWITCH_OFF, eventtimestamp=1001)
        entity._event_callback()
        entity._trigger_event.assert_not_called()
        entity.schedule_update_ha_state.assert_not_called()

    def test_none_eventtype_does_not_fire(self):
        entity = _make_entity(eventtype=None, eventtimestamp=1002)
        entity._event_callback()
        entity._trigger_event.assert_not_called()
        entity.schedule_update_ha_state.assert_not_called()


class TestTimestampGuard:
    """Identical eventTimestamp on successive callbacks = phantom / stale replay (#192)."""

    def test_first_press_fires(self):
        entity = _make_entity(eventtype=_PRESS_SHORT, eventtimestamp=5000)
        entity._event_callback()
        entity._trigger_event.assert_called_once()
        assert entity._last_fired_timestamp == 5000

    def test_second_call_same_ts_does_not_fire(self):
        """Battery-update replaying the same stale Keypad state -> must be swallowed."""
        entity = _make_entity(eventtype=_PRESS_SHORT, eventtimestamp=5000)
        entity._event_callback()   # first: fires
        entity._event_callback()   # second: same ts -> phantom, must NOT fire again
        assert entity._trigger_event.call_count == 1

    def test_new_ts_fires_again(self):
        """A genuinely new keypress (different timestamp) must still fire."""
        entity = _make_entity(eventtype=_PRESS_SHORT, eventtimestamp=5000)
        entity._event_callback()
        assert entity._trigger_event.call_count == 1

        # Simulate a new keypress arriving with a higher timestamp
        entity._device.eventtimestamp = 6000
        entity._device.eventtype = _PRESS_LONG
        entity._event_callback()
        assert entity._trigger_event.call_count == 2
        assert entity._last_fired_timestamp == 6000

    def test_ts_zero_then_nonzero(self):
        """Timestamp 0 is valid as an initial stale value; a real press at ts>0 must fire."""
        entity = _make_entity(eventtype=_PRESS_SHORT, eventtimestamp=0)
        # Simulate a stale state at ts=0 delivered at startup (should NOT fire
        # because _last_fired_timestamp starts at -1, so ts=0 != -1 -> will fire).
        # The intent: we accept ts=0 as a genuine event if the device sends it.
        entity._event_callback()
        assert entity._trigger_event.call_count == 1
        assert entity._last_fired_timestamp == 0

        # A second call with the same ts=0 (stale replay) must NOT fire.
        entity._event_callback()
        assert entity._trigger_event.call_count == 1


class TestPressTypesFire:
    """Happy path: all three press types fire with correct event_type attribute."""

    def test_press_short_fires(self):
        entity = _make_entity(eventtype=_PRESS_SHORT, eventtimestamp=100)
        entity._event_callback()
        entity._trigger_event.assert_called_once()
        args = entity._trigger_event.call_args[0]
        assert args[0] == "PRESS_SHORT"

    def test_press_long_fires(self):
        entity = _make_entity(eventtype=_PRESS_LONG, eventtimestamp=200)
        entity._event_callback()
        entity._trigger_event.assert_called_once()
        args = entity._trigger_event.call_args[0]
        assert args[0] == "PRESS_LONG"

    def test_press_long_released_fires(self):
        entity = _make_entity(eventtype=_PRESS_LONG_RELEASED, eventtimestamp=300)
        entity._event_callback()
        entity._trigger_event.assert_called_once()
        args = entity._trigger_event.call_args[0]
        assert args[0] == "PRESS_LONG_RELEASED"

    def test_schedule_update_called_on_fire(self):
        entity = _make_entity(eventtype=_PRESS_SHORT, eventtimestamp=400)
        entity._event_callback()
        entity.schedule_update_ha_state.assert_called_once()

    def test_schedule_update_not_called_when_suppressed(self):
        """When callback is suppressed (non-press), schedule_update must not be called."""
        entity = _make_entity(eventtype=_SWITCH_ON, eventtimestamp=500)
        entity._event_callback()
        entity.schedule_update_ha_state.assert_not_called()


class TestBatteryUpdateSimulation:
    """Reproduce the exact #192 scenario: battery update re-delivers stale Keypad."""

    def test_phantom_event_not_fired_on_battery_update(self):
        """After a genuine press, a battery update that replays the same
        Keypad state must NOT generate a second HA event.
        """
        entity = _make_entity(eventtype=_PRESS_SHORT, eventtimestamp=9999)

        # First: genuine keypress event
        entity._event_callback()
        assert entity._trigger_event.call_count == 1

        # Second: battery update triggers the same Keypad callback with stale data
        # (same eventtype, same eventtimestamp - typical SHC behaviour for #192)
        entity._event_callback()
        assert entity._trigger_event.call_count == 1  # still 1, no phantom


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


# ===========================================================================
# MotionDetectorEvent
# ===========================================================================


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
    entity._last_fired_timestamp = ""  # dedup guard - must differ from latestmotion
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

        async def _run_added():
            with patch(_SHC_ENTITY_ADDED, return_value=None):
                await MotionDetectorEvent.async_added_to_hass(entity)

        asyncio.run(_run_added())
        assert "hdm:motion:77" in lms.registered
        assert callable(lms.registered["hdm:motion:77"])

    def test_non_latest_motion_service_skipped(self):
        """A service with id != 'LatestMotion' must not be registered."""
        lms = FakeLatestMotionService()
        other = SimpleNamespace(id="Battery", subscribe_callback=lambda eid, cb: None)
        entity = _make_motion_entity(device_id="hdm:motion:11", extra_services=[other, lms])

        async def _run_added():
            with patch(_SHC_ENTITY_ADDED, return_value=None):
                await MotionDetectorEvent.async_added_to_hass(entity)

        asyncio.run(_run_added())
        assert "hdm:motion:11" in lms.registered
        assert not hasattr(other, "registered")

    def test_registered_callback_fires_event(self):
        lms = FakeLatestMotionService()
        entity = _make_motion_entity(
            device_id="hdm:motion:55",
            latestmotion="2026-06-01T08:00:00",
            extra_services=[lms],
        )

        async def _run_added():
            with patch(_SHC_ENTITY_ADDED, return_value=None):
                await MotionDetectorEvent.async_added_to_hass(entity)

        asyncio.run(_run_added())
        lms.registered["hdm:motion:55"]()
        entity._trigger_event.assert_called_once()
        assert entity._trigger_event.call_args[0][0] == "MOTION"


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


class TestMotionDetectorEventDispatchesDirectly:
    """_event_callback must call _dispatch_event directly (no call_soon_threadsafe)."""

    def _make_entity(self):
        entity = MotionDetectorEvent.__new__(MotionDetectorEvent)
        entity.hass = _make_hass_capturing()
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
        # device_id is a read-only property derived from _device.id - no direct assign
        entity._attr_unique_id = "root-1_md-1"
        entity._trigger_event = MagicMock()
        entity.schedule_update_ha_state = MagicMock()
        entity._last_fired_timestamp = ""  # dedup guard - empty so callback fires
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


class TestMotionEventDedupGuard:
    """event.py:360 - dedup guard: same ts returns early."""

    def _make_entity(self):
        entity = MotionDetectorEvent.__new__(MotionDetectorEvent)
        entity._device = SimpleNamespace(
            name="Motion",
            id="md-1",
            root_device_id="root-1",
            latestmotion="2026-06-28T10:00:00.000Z",
        )
        entity._last_fired_timestamp = "2026-06-28T10:00:00.000Z"  # same as device
        entity._trigger_event = MagicMock()
        entity.schedule_update_ha_state = MagicMock()
        return entity

    def test_same_ts_no_dispatch(self):
        entity = self._make_entity()
        entity._event_callback()
        entity._trigger_event.assert_not_called()


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


# ===========================================================================
# SmokeDetectionSystemEvent
# ===========================================================================


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

        async def _run_added():
            with patch(_SHC_ENTITY_ADDED, return_value=None):
                await SmokeDetectionSystemEvent.async_added_to_hass(entity)

        asyncio.run(_run_added())
        assert "hdm:smoke:sys:10" in sas.registered

    def test_non_surveillance_service_skipped(self):
        sas = FakeSurveillanceAlarmService()
        other = SimpleNamespace(id="Battery", subscribe_callback=lambda eid, cb: None)
        entity = _make_smoke_system_entity(
            device_id="hdm:smoke:sys:20", extra_services=[other, sas]
        )

        async def _run_added():
            with patch(_SHC_ENTITY_ADDED, return_value=None):
                await SmokeDetectionSystemEvent.async_added_to_hass(entity)

        asyncio.run(_run_added())
        assert "hdm:smoke:sys:20" in sas.registered
        assert not hasattr(other, "registered")

    def test_registered_callback_fires_event(self):
        sas = FakeSurveillanceAlarmService()
        entity = _make_smoke_system_entity(
            device_id="hdm:smoke:sys:30",
            alarm_name="ALARM",
            extra_services=[sas],
        )

        async def _run_added():
            with patch(_SHC_ENTITY_ADDED, return_value=None):
                await SmokeDetectionSystemEvent.async_added_to_hass(entity)

        asyncio.run(_run_added())
        sas.registered["hdm:smoke:sys:30"]()
        entity._trigger_event.assert_called_once()
        assert entity._trigger_event.call_args[0][0] == "ALARM"


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
        """_event_callback -> call_soon_threadsafe(sync) -> _dispatch_event -> schedule_update."""
        entity = _make_smoke_system_event_entity("ALARM_ON")
        entity._event_callback()
        entity._trigger_event.assert_called_once()
        entity.schedule_update_ha_state.assert_called_once()

    def test_event_types_is_alarm(self):
        entity = _make_smoke_system_event_entity()
        assert entity._attr_event_types == ["ALARM"]


class TestSmokeDetectionSystemEventDispatchesDirectly:
    """_event_callback must call _dispatch_event directly (no call_soon_threadsafe)."""

    def _make_entity(self):
        entity = SmokeDetectionSystemEvent.__new__(SmokeDetectionSystemEvent)
        entity.hass = _make_hass_capturing()
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


class TestSmokeDetectionSystemEventBadEnum:
    """event.py:404-406 - ValueError/KeyError in SmokeDetectionSystemEvent._event_callback."""

    def _make_entity(self):
        class BadAlarm:
            @property
            def name(self):
                raise ValueError("unknown enum")

        entity = SmokeDetectionSystemEvent.__new__(SmokeDetectionSystemEvent)
        entity._device = SimpleNamespace(
            name="Smoke System",
            id="ss-1",
            root_device_id="root-1",
            alarm=BadAlarm(),
        )
        entity._attr_unique_id = "root-1_ss-1"
        entity._trigger_event = MagicMock()
        entity.schedule_update_ha_state = MagicMock()
        return entity

    def test_bad_alarm_no_dispatch(self):
        entity = self._make_entity()
        with patch("custom_components.bosch_shc.event.LOGGER") as mock_log:
            entity._event_callback()
        mock_log.warning.assert_called_once()
        entity._trigger_event.assert_not_called()


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


# ===========================================================================
# SmokeDetectorEvent
# ===========================================================================


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

        async def _run_added():
            with patch(_SHC_ENTITY_ADDED, return_value=None):
                await SmokeDetectorEvent.async_added_to_hass(entity)

        asyncio.run(_run_added())
        assert "hdm:smoke:det:10" in als.registered

    def test_non_alarm_service_skipped(self):
        als = FakeAlarmService()
        other = SimpleNamespace(id="Battery", subscribe_callback=lambda eid, cb: None)
        entity = _make_smoke_detector_entity(
            device_id="hdm:smoke:det:20", extra_services=[other, als]
        )

        async def _run_added():
            with patch(_SHC_ENTITY_ADDED, return_value=None):
                await SmokeDetectorEvent.async_added_to_hass(entity)

        asyncio.run(_run_added())
        assert "hdm:smoke:det:20" in als.registered
        assert not hasattr(other, "registered")

    def test_registered_callback_fires_event(self):
        als = FakeAlarmService()
        entity = _make_smoke_detector_entity(
            device_id="hdm:smoke:det:30",
            alarmstate_name="PRIMARY_SMOKE_ALARM",
            extra_services=[als],
        )

        async def _run_added():
            with patch(_SHC_ENTITY_ADDED, return_value=None):
                await SmokeDetectorEvent.async_added_to_hass(entity)

        asyncio.run(_run_added())
        als.registered["hdm:smoke:det:30"]()
        entity._trigger_event.assert_called_once()
        assert entity._trigger_event.call_args[0][0] == "ALARM"


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
        """_event_callback -> call_soon_threadsafe -> _dispatch_event -> schedule_update."""
        entity = _make_smoke_detector_event_entity("SECONDARY_ALARM")
        entity.hass = _make_hass_direct()  # executes fn immediately
        entity._trigger_event = MagicMock()
        entity.schedule_update_ha_state = MagicMock()
        entity._event_callback()
        entity.schedule_update_ha_state.assert_called_once()

    def test_event_types_is_alarm(self):
        entity = _make_smoke_detector_event_entity()
        assert entity._attr_event_types == ["ALARM"]


class TestSmokeDetectorEventDispatchesDirectly:
    """_event_callback must call _dispatch_event directly (no call_soon_threadsafe)."""

    def _make_entity(self):
        entity = SmokeDetectorEvent.__new__(SmokeDetectorEvent)
        entity.hass = _make_hass_capturing()
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


class TestSmokeDetectorEventBadEnum:
    """event.py:449-451 - ValueError/KeyError in SmokeDetectorEvent._event_callback."""

    def _make_entity(self):
        class BadAlarmState:
            @property
            def name(self):
                raise KeyError("unknown")

        entity = SmokeDetectorEvent.__new__(SmokeDetectorEvent)
        entity._device = SimpleNamespace(
            name="Smoke Det",
            id="sd-1",
            root_device_id="root-1",
            alarmstate=BadAlarmState(),
        )
        entity._attr_unique_id = "root-1_sd-1"
        entity._trigger_event = MagicMock()
        entity.schedule_update_ha_state = MagicMock()
        return entity

    def test_bad_alarmstate_no_dispatch(self):
        entity = self._make_entity()
        with patch("custom_components.bosch_shc.event.LOGGER") as mock_log:
            entity._event_callback()
        mock_log.warning.assert_called_once()
        entity._trigger_event.assert_not_called()


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


# ===========================================================================
# SHCScenarioEvent
# ===========================================================================


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

    # SHCScenarioEvent.__init__ does
    # hass.config_entries.async_get_entry(entry_id).runtime_data.shc_device
    entry_id = "entry1"
    hass = MagicMock(name="hass")
    hass.config_entries.async_get_entry = MagicMock(
        return_value=SimpleNamespace(
            runtime_data=SimpleNamespace(shc_device=shc_device_entry)
        )
    )

    entity = SHCScenarioEvent(scenario, session, hass, entry_id=entry_id)
    entity.hass = _make_hass_direct()
    entity._trigger_event = MagicMock()
    entity.schedule_update_ha_state = MagicMock()
    return entity


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

    def test_event_types_is_scenario(self):
        entity = _make_scenario_entity()
        assert entity._attr_event_types == ["SCENARIO"]


class TestSHCScenarioEventCallback:
    """_event_callback must call _dispatch_event directly, not via call_soon_threadsafe."""

    def _make_entity(self):
        """Returns entity with capturing hass."""
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
        hass_init.config_entries.async_get_entry = MagicMock(
            return_value=SimpleNamespace(
                runtime_data=SimpleNamespace(shc_device=shc)
            )
        )
        entity = SHCScenarioEvent(scenario, session, hass_init, entry_id=entry_id)
        entity.hass = _make_hass_capturing()
        entity._trigger_event = MagicMock()
        entity.schedule_update_ha_state = MagicMock()
        return entity

    def test_event_callback_calls_dispatch_directly(self):
        entity = self._make_entity()
        event_data = {"id": "sc-cb", "name": "Abend", "lastTimeTriggered": "2026-06-20"}
        entity._event_callback(event_data)
        assert not entity.hass.loop.call_soon_threadsafe.called
        entity._trigger_event.assert_called_once()

    def test_event_callback_event_type_is_scenario(self):
        entity = self._make_entity()
        event_data = {"id": "sc-cb", "name": "Abend", "lastTimeTriggered": "t"}
        entity._event_callback(event_data)
        call_args = entity._trigger_event.call_args[0]
        assert call_args[0] == "SCENARIO"


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
        hass_init.config_entries.async_get_entry = MagicMock(
            return_value=SimpleNamespace(
                runtime_data=SimpleNamespace(shc_device=shc)
            )
        )
        entity = SHCScenarioEvent(scenario, session, hass_init, entry_id=entry_id)
        entity.hass = _make_hass_direct()
        entity._trigger_event = MagicMock()
        entity.schedule_update_ha_state = MagicMock()

        async def _run_added():
            with patch(_EVENTENTITY_ADDED, return_value=None):
                await SHCScenarioEvent.async_added_to_hass(entity)

        asyncio.run(_run_added())
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
        hass_init.config_entries.async_get_entry = MagicMock(
            return_value=SimpleNamespace(
                runtime_data=SimpleNamespace(shc_device=shc)
            )
        )
        entity = SHCScenarioEvent(scenario, session, hass_init, entry_id=entry_id)
        entity.hass = _make_hass_direct()
        entity._trigger_event = MagicMock()
        entity.schedule_update_ha_state = MagicMock()

        async def _run_added():
            with patch(_EVENTENTITY_ADDED, return_value=None):
                await SHCScenarioEvent.async_added_to_hass(entity)

        asyncio.run(_run_added())
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
        hass_init.config_entries.async_get_entry = MagicMock(
            return_value=SimpleNamespace(
                runtime_data=SimpleNamespace(shc_device=shc)
            )
        )
        entity = SHCScenarioEvent(scenario, session, hass_init, entry_id=entry_id)
        entity.hass = _make_hass_direct()
        entity._trigger_event = MagicMock()
        entity.schedule_update_ha_state = MagicMock()

        async def _run_added():
            with patch(_EVENTENTITY_ADDED, return_value=None):
                await SHCScenarioEvent.async_added_to_hass(entity)

        asyncio.run(_run_added())
        ts = "2026-06-20T12:00:00"
        subscriptions["sc-pay"]({"id": "sc-pay", "name": "Night Mode", "lastTimeTriggered": ts})
        attrs = entity._trigger_event.call_args[0][1]
        assert attrs[ATTR_EVENT_TYPE] == "SCENARIO"
        assert attrs[ATTR_ID] == "sc-pay"
        assert attrs[ATTR_NAME] == "Night Mode"
        assert attrs[ATTR_LAST_TIME_TRIGGERED] == ts


def _make_scenario_entity_v2(
    scenario_name="Night Mode",
    scenario_id="scn:42",
    unique_id_suffix="uid-shc-001",
    shc_name="SHC Hub",
    shc_device_id="device-shc-entry-1",
):
    """Build SHCScenarioEvent bypassing all HA infrastructure.

    Alternate to _make_scenario_entity(): constructs the real __init__ using
    _make_sync_hass (a synchronous SimpleNamespace-based hass) instead of a
    MagicMock, and returns (entity, session, shc_entry) for tests that need
    to assert on the session/shc scaffolding too.
    """
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
    hass = _make_sync_hass(shc_device=shc_entry)
    entity = SHCScenarioEvent(scenario, session, hass, entry_id="entry1")
    entity._trigger_event = MagicMock()
    entity.schedule_update_ha_state = MagicMock()
    # SHCScenarioEvent inherits EventEntity (not SHCEntity); the HA infrastructure
    # normally sets entity.hass after async_added_to_hass.  Inject a synchronous
    # shim so _event_callback tests can call the method directly without a real
    # event loop.  (hass passed to __init__ is only used for the
    # runtime_data.shc_device lookup.)
    entity.hass = _make_sync_hass()
    return entity, session, shc_entry


class TestSHCScenarioEventInit:
    """SHCScenarioEvent.__init__ wires attributes correctly (lines 180-189)."""

    def test_attr_name_set(self):
        entity, _, _ = _make_scenario_entity_v2(scenario_name="Away Mode")
        assert entity._attr_name == "Away Mode Scenario"

    def test_unique_id_uses_session_uid_and_scenario_id(self):
        entity, _, _ = _make_scenario_entity_v2(
            unique_id_suffix="SHC-UID-123", scenario_id="scn:99"
        )
        assert entity._attr_unique_id == "SHC-UID-123_scn:99"

    def test_shc_device_entry_stored(self):
        entity, _, shc_entry = _make_scenario_entity_v2()
        assert entity._shc is shc_entry


class TestSHCScenarioEventPropertiesV2:
    """device_name, device_id, device_info properties (lines 192-208)."""

    def test_device_name_returns_shc_name(self):
        entity, _, shc_entry = _make_scenario_entity_v2(shc_name="My SHC")
        assert entity.device_name == "My SHC"

    def test_device_id_returns_shc_id(self):
        entity, _, shc_entry = _make_scenario_entity_v2(shc_device_id="dev-abc")
        assert entity.device_id == "dev-abc"

    def test_device_info_identifiers(self):
        entity, _, shc_entry = _make_scenario_entity_v2()
        info = entity.device_info
        assert info["identifiers"] == shc_entry.identifiers

    def test_device_info_name(self):
        entity, _, _ = _make_scenario_entity_v2(shc_name="Hub XY")
        assert entity.device_info["name"] == "Hub XY"

    def test_device_info_manufacturer(self):
        entity, _, _ = _make_scenario_entity_v2()
        assert entity.device_info["manufacturer"] == "Bosch"

    def test_device_info_model(self):
        entity, _, shc_entry = _make_scenario_entity_v2()
        assert entity.device_info["model"] == shc_entry.model


class TestSHCScenarioEventAsyncAddedToHass:
    """async_added_to_hass subscribes scenario callback (lines 211-216)."""

    def test_subscribe_scenario_callback_called_with_scenario_id(self):
        entity, session, _ = _make_scenario_entity_v2(scenario_id="scn:77")

        async def _run_added():
            with patch(
                "homeassistant.components.event.EventEntity.async_added_to_hass",
                new=AsyncMock(return_value=None),
            ):
                await entity.async_added_to_hass()

        asyncio.run(_run_added())
        session.subscribe_scenario_callback.assert_called_once()
        call_args = session.subscribe_scenario_callback.call_args[0]
        assert call_args[0] == "scn:77"
        assert callable(call_args[1])

    def test_subscribed_callback_is_event_callback(self):
        """The subscribed callable must be the _event_callback method."""
        entity, session, _ = _make_scenario_entity_v2(scenario_id="scn:88")

        async def _run_added():
            with patch(
                "homeassistant.components.event.EventEntity.async_added_to_hass",
                new=AsyncMock(return_value=None),
            ):
                await entity.async_added_to_hass()

        asyncio.run(_run_added())
        registered_cb = session.subscribe_scenario_callback.call_args[0][1]
        # Fire it directly to confirm it's the real _event_callback
        event_data = {"id": "scn:88", "name": "Night Mode", "lastTimeTriggered": "2026-01-01T00:00:00"}
        registered_cb(event_data)
        entity._trigger_event.assert_called_once()


class TestSHCScenarioEventCallbackPayload:
    """_event_callback fires the right event + attributes (lines 219-228)."""

    def test_fires_scenario_event_type(self):
        entity, _, _ = _make_scenario_entity_v2()
        entity._event_callback({"id": "scn:1", "name": "Away", "lastTimeTriggered": "ts1"})
        entity._trigger_event.assert_called_once()
        assert entity._trigger_event.call_args[0][0] == "SCENARIO"

    def test_callback_payload_event_type_attr(self):
        entity, _, _ = _make_scenario_entity_v2()
        entity._event_callback({"id": "scn:2", "name": "Night", "lastTimeTriggered": "ts2"})
        attrs = entity._trigger_event.call_args[0][1]
        assert attrs[ATTR_EVENT_TYPE] == "SCENARIO"

    def test_callback_payload_id_attr(self):
        entity, _, _ = _make_scenario_entity_v2()
        entity._event_callback({"id": "scn:42", "name": "Night", "lastTimeTriggered": "ts"})
        attrs = entity._trigger_event.call_args[0][1]
        assert attrs[ATTR_ID] == "scn:42"

    def test_callback_payload_name_attr(self):
        entity, _, _ = _make_scenario_entity_v2()
        entity._event_callback({"id": "scn:3", "name": "Vacation", "lastTimeTriggered": "ts"})
        attrs = entity._trigger_event.call_args[0][1]
        assert attrs[ATTR_NAME] == "Vacation"

    def test_callback_payload_last_time_triggered(self):
        entity, _, _ = _make_scenario_entity_v2()
        entity._event_callback({"id": "scn:4", "name": "X", "lastTimeTriggered": "2026-06-01T10:00:00"})
        attrs = entity._trigger_event.call_args[0][1]
        assert attrs[ATTR_LAST_TIME_TRIGGERED] == "2026-06-01T10:00:00"

    def test_callback_calls_schedule_update(self):
        entity, _, _ = _make_scenario_entity_v2()
        entity._event_callback({"id": "scn:5", "name": "Y", "lastTimeTriggered": "ts5"})
        entity.schedule_update_ha_state.assert_called_once()


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


# ===========================================================================
# LightControlButtonEvent
# ===========================================================================


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


class TestLightControlButtonEventCallback:
    """Lines 226-235: LightControlButtonEvent._event_callback full fire path."""

    def _make_entity(self, event_type_raw, ts, last_ts=-1):
        ent = LightControlButtonEvent.__new__(LightControlButtonEvent)
        ent._attr_event_types = [
            "PRESS_SHORT", "PRESS_LONG", "PRESS_LONG_RELEASED",
            "SWITCH_ON", "SWITCH_OFF",
        ]
        ent._last_fired_timestamp = last_ts
        ent._device = SimpleNamespace(
            eventtype=event_type_raw,
            eventtimestamp=ts,
            id="lc1",
            name="LC",
        )
        # device_id is a property from SHCEntity: returns self._device.id
        # Setting _device is enough - no need to set device_id directly
        ent.entity_id = "event.lc"
        ent._dispatch_event = MagicMock()
        return ent

    def test_event_callback_fires_on_new_timestamp(self):
        """Lines 237-255: eventtype valid + timestamp advanced -> _dispatch_event called."""
        et = SimpleNamespace(name="PRESS_SHORT")
        ent = self._make_entity(et, ts=1000, last_ts=500)
        ent._event_callback()
        ent._dispatch_event.assert_called_once()
        assert ent._last_fired_timestamp == 1000

    def test_event_callback_skips_none_eventtype(self):
        """_event_callback returns early when eventtype is None."""
        ent = self._make_entity(None, ts=1000)
        ent._event_callback()
        ent._dispatch_event.assert_not_called()

    def test_event_callback_skips_unknown_event_type(self):
        """_event_callback returns early when event type not in _attr_event_types."""
        et = SimpleNamespace(name="SWITCH_XXX")
        ent = self._make_entity(et, ts=1000)
        ent._event_callback()
        ent._dispatch_event.assert_not_called()

    def test_event_callback_skips_same_timestamp(self):
        """_event_callback returns early when timestamp unchanged."""
        et = SimpleNamespace(name="PRESS_SHORT")
        ent = self._make_entity(et, ts=1000, last_ts=1000)
        ent._event_callback()
        ent._dispatch_event.assert_not_called()


class TestLightControlDispatchEventValueError:
    """Lines 262-264: LightControlButtonEvent._dispatch_event ValueError branch."""

    def test_dispatch_event_value_error_returns_early(self):
        """Lines 262-264: ValueError in _trigger_event -> warning log, return."""
        ent = LightControlButtonEvent.__new__(LightControlButtonEvent)
        ent.entity_id = "event.lc"
        ent._trigger_event = MagicMock(side_effect=ValueError("bad type"))
        ent.schedule_update_ha_state = MagicMock()

        ent._dispatch_event("BAD_TYPE", {})
        ent.schedule_update_ha_state.assert_not_called()


class TestEventLightControlAddedToHass:
    """event.py lines 226-235: LightControlButtonEvent.async_added_to_hass."""

    def test_async_added_to_hass_registers_keypad_events(self):
        """Lines 226-235: Keypad service -> register_event called for each KeyState."""
        # Build a fake Keypad service with KeyState enum
        key_state_1 = SimpleNamespace(value="KEY_1")
        key_state_2 = SimpleNamespace(value="KEY_2")
        keypad_service = SimpleNamespace(
            id="Keypad",
            KeyState=[key_state_1, key_state_2],
            register_event=MagicMock(),
            subscribe_callback=MagicMock(),
        )
        non_keypad_service = SimpleNamespace(
            id="LatestMotion",
            subscribe_callback=MagicMock(),
        )

        dev = _fake_dev("lc1", device_services=[keypad_service, non_keypad_service])

        ent = LightControlButtonEvent.__new__(LightControlButtonEvent)
        ent._device = dev
        ent._entry_id = "E1"
        ent._last_fired_timestamp = -1
        ent._attr_event_types = ["PRESS_SHORT", "PRESS_LONG"]
        ent.entity_id = "event.lc1_button"
        ent._attr_unique_id = "root1_lc1_button"
        # hass isn't needed because Entity.async_added_to_hass() is a no-op
        ent.hass = MagicMock()

        _run(ent.async_added_to_hass())

        # register_event should have been called once per KeyState
        assert keypad_service.register_event.call_count == 2
        keypad_service.register_event.assert_any_call("KEY_1", ent._event_callback)
        keypad_service.register_event.assert_any_call("KEY_2", ent._event_callback)


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


# ---------------------------------------------------------------------------
# Fake service double shared by the Unsubscribe suites above (register_event
# has no matching unregister_event upstream; SHCEntity's
# async_will_remove_from_hass must clean up the private _event_callbacks dict
# via subscribe_callback/unsubscribe_callback pairing instead).
# ---------------------------------------------------------------------------


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


# ===========================================================================
# Structural checks (class-level attributes HA reads via instance access)
# ===========================================================================


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


# ===========================================================================
# entity.py helpers (async_get_device_id, async_remove_devices,
# async_migrate_to_new_unique_id, SHCEntity._update_attr / else-branch)
# ===========================================================================


class TestAsyncGetDeviceId:
    """async_get_device_id returns device.id or None via device registry mock."""

    def test_returns_device_id_when_found(self):
        fake_device = SimpleNamespace(id="reg-device-id-42")
        fake_registry = SimpleNamespace(
            async_get_device=MagicMock(return_value=fake_device)
        )

        async def _run_get():
            with patch(
                "custom_components.bosch_shc.entity.get_dev_reg",
                return_value=fake_registry,
            ):
                return await async_get_device_id(object(), "dev-123")

        result = asyncio.run(_run_get())
        assert result == "reg-device-id-42"

    def test_returns_none_when_not_found(self):
        fake_registry = SimpleNamespace(
            async_get_device=MagicMock(return_value=None)
        )

        async def _run_get():
            with patch(
                "custom_components.bosch_shc.entity.get_dev_reg",
                return_value=fake_registry,
            ):
                return await async_get_device_id(object(), "dev-missing")

        result = asyncio.run(_run_get())
        assert result is None

    def test_passes_correct_identifiers(self):
        fake_registry = SimpleNamespace(
            async_get_device=MagicMock(return_value=None)
        )

        async def _run_get():
            with patch(
                "custom_components.bosch_shc.entity.get_dev_reg",
                return_value=fake_registry,
            ):
                await async_get_device_id(object(), "dev-xyz")

        asyncio.run(_run_get())
        call_kwargs = fake_registry.async_get_device.call_args
        assert call_kwargs[1]["identifiers"] == {(DOMAIN, "dev-xyz")}


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

        async def _run_remove():
            with patch(
                "custom_components.bosch_shc.entity.get_dev_reg",
                return_value=fake_registry,
            ):
                await async_remove_devices(object(), entity, "entry-E1")

        asyncio.run(_run_remove())
        assert update_calls == [("reg-id-99", "entry-E1")]

    def test_no_update_when_device_not_found(self):
        update_calls = []
        fake_registry = SimpleNamespace(
            async_get_device=MagicMock(return_value=None),
            async_update_device=lambda *a, **kw: update_calls.append(a),
        )
        entity = self._make_entity_ns(device_id="hdm:dev:missing")

        async def _run_remove():
            with patch(
                "custom_components.bosch_shc.entity.get_dev_reg",
                return_value=fake_registry,
            ):
                await async_remove_devices(object(), entity, "entry-E1")

        asyncio.run(_run_remove())
        assert update_calls == []


class TestAsyncMigrateToNewUniqueId:
    """async_migrate_to_new_unique_id migrates old->new unique_id via entity registry."""

    def _make_device(self, serial="SER-001", dev_id="hdm:dev:1", root_id="root:1"):
        return SimpleNamespace(
            serial=serial,
            id=dev_id,
            root_device_id=root_id,
        )

    def test_entity_found_migrates_unique_id(self):
        ent_registry = MagicMock()
        ent_registry.async_get_entity_id.return_value = "sensor.some_entity"

        async def _run_migrate():
            with patch(
                "custom_components.bosch_shc.entity.entity_registry.async_get",
                return_value=ent_registry,
            ):
                dev = self._make_device(serial="OLD-SER", dev_id="hdm:new:id", root_id="root:new")
                await async_migrate_to_new_unique_id(
                    object(), "sensor", dev
                )

        asyncio.run(_run_migrate())
        ent_registry.async_update_entity.assert_called_once()
        call_kwargs = ent_registry.async_update_entity.call_args[1]
        assert call_kwargs["new_unique_id"] == "root:new_hdm:new:id"

    def test_entity_not_found_skips_migration(self):
        ent_registry = MagicMock()
        ent_registry.async_get_entity_id.return_value = None

        async def _run_migrate():
            with patch(
                "custom_components.bosch_shc.entity.entity_registry.async_get",
                return_value=ent_registry,
            ):
                dev = self._make_device()
                await async_migrate_to_new_unique_id(object(), "sensor", dev)

        asyncio.run(_run_migrate())
        ent_registry.async_update_entity.assert_not_called()

    def test_with_attr_name_appends_lowercase(self):
        ent_registry = MagicMock()
        ent_registry.async_get_entity_id.return_value = "sensor.with_attr"

        async def _run_migrate():
            with patch(
                "custom_components.bosch_shc.entity.entity_registry.async_get",
                return_value=ent_registry,
            ):
                dev = self._make_device(serial="SER", dev_id="hdm:d:1", root_id="root:r")
                await async_migrate_to_new_unique_id(
                    object(), "sensor", dev, attr_name="Temperature"
                )

        asyncio.run(_run_migrate())
        call_kwargs = ent_registry.async_update_entity.call_args[1]
        assert call_kwargs["new_unique_id"] == "root:r_hdm:d:1_temperature"

    def test_value_error_on_update_logs_warning(self):
        """ValueError from async_update_entity logs a warning, does not raise."""
        ent_registry = MagicMock()
        ent_registry.async_get_entity_id.return_value = "sensor.duplicate"
        ent_registry.async_update_entity.side_effect = ValueError("already exists")

        async def _run_migrate():
            with patch(
                "custom_components.bosch_shc.entity.entity_registry.async_get",
                return_value=ent_registry,
            ):
                dev = self._make_device()
                await async_migrate_to_new_unique_id(object(), "sensor", dev)

        asyncio.run(_run_migrate())  # must not raise

    def test_old_unique_id_override_used_for_lookup(self):
        ent_registry = MagicMock()
        ent_registry.async_get_entity_id.return_value = None

        async def _run_migrate():
            with patch(
                "custom_components.bosch_shc.entity.entity_registry.async_get",
                return_value=ent_registry,
            ):
                dev = self._make_device(serial="SER-X")
                await async_migrate_to_new_unique_id(
                    object(), "sensor", dev, old_unique_id="my-custom-old-id"
                )

        asyncio.run(_run_migrate())
        ent_registry.async_get_entity_id.assert_called_once_with(
            "sensor", DOMAIN, "my-custom-old-id"
        )


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
    """update_entity_information else-branch (device.deleted is False) - lines 99-100."""

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


# ===========================================================================
# entity_id slugify validity (Bosch device/scenario names -> valid HA
# object_id)
# ===========================================================================


VALID_OBJECT_ID_RE = re.compile(r"^[a-z0-9_]+$")


def _is_valid_object_id(slug: str) -> bool:
    """Return True if slug is a valid HA object_id (all lower, ascii, underscores)."""
    return bool(VALID_OBJECT_ID_RE.match(slug))


@pytest.mark.parametrize(
    "name",
    [
        "Haus verlassen",
        "Lichtschalter WZ 2",
        "Außentür",
        "Guten Morgen",
        "Überwachung EIN",
        "Öffner Küche",
        "UPPER CASE NAME",
        "mixed_CASE_with_underscores",
        "Türsensor Flur",
        "Rauchmelder Büro",
        "Fenster öffnen",
    ],
)
def test_slugify_produces_valid_object_id(name: str) -> None:
    """slugify(name) must produce a valid HA object_id for all Bosch device/scenario names."""
    slug = slugify(name)
    assert slug, f"slugify({name!r}) returned empty string"
    assert _is_valid_object_id(slug), (
        f"slugify({name!r}) = {slug!r} is not a valid HA object_id "
        f"(must match ^[a-z0-9_]+$)"
    )


def test_universal_switch_entity_id_slug() -> None:
    """Simulate the UniversalSwitchEvent entity_id slug construction."""
    device_name = "Lichtschalter WZ 2"
    key_id = "UPPER1"
    slug = f"{slugify(device_name)}_button_{key_id.casefold()}"
    assert _is_valid_object_id(slug), (
        f"UniversalSwitchEvent slug {slug!r} is not a valid HA object_id"
    )


def test_scenario_entity_id_slug() -> None:
    """Simulate the SHCScenarioEvent entity_id slug construction."""
    scenario_name = "Haus verlassen"
    slug = f"scenario_{slugify(scenario_name)}"
    assert _is_valid_object_id(slug), (
        f"SHCScenarioEvent slug {slug!r} is not a valid HA object_id"
    )


def test_umlaut_transliteration() -> None:
    """Umlauts (ä/ö/ü/ß/Ä/Ö/Ü) must be transliterated, not dropped."""
    assert slugify("Außentür") != ""
    assert slugify("Überwachung") != ""
    # Result must be valid object_id
    assert _is_valid_object_id(slugify("Außentür"))
    assert _is_valid_object_id(slugify("Überwachung"))
    assert _is_valid_object_id(slugify("Öffner Küche"))
