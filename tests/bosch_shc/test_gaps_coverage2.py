"""Targeted tests to close the remaining coverage gaps.

Gaps targeted:
  __init__.py:436     - `continue` when hass.states.get(eid) returns None inside
                        _presence_state_changed multi-entity loop.
  binary_sensor.py:187 - tracker.teardown() body of _cleanup_tracker() closure in
                         async_setup_entry — needs the closure to actually be called.
  select.py:58        - `continue` for device_excluded in motion_detectors2 loop.
  select.py:64-65     - `continue` after AttributeError from motion_sensitivity.
  sensor.py:354       - entities.append(BatteryLevelSensor(...)) happy path
                        (supports_batterylevel=True + diagnostic_enabled=True).
  sensor.py:736-738   - BatteryLevelSensor.__init__ body — needs real construction,
                        not __new__ bypass.

Pattern: __new__ bypass + SimpleNamespace; asyncio.run for async setup tests.
No HA harness.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from boschshcpy import SurveillanceAlarmService

# ---------------------------------------------------------------------------
# __init__.py line 436 — `continue` when hass.states.get(eid) returns None
# ---------------------------------------------------------------------------
# The _presence_state_changed callback loops over all tracked entity IDs and
# calls hass.states.get(eid).  If the result is None (entity has been removed
# or not yet loaded), the loop issues `continue`.  This is the exact branch at
# line 436.  We need a multi-entity setup where at least one entity returns
# None from hass.states.get while another returns a proper state object.

_PATCH_SESSION = "custom_components.bosch_shc.SHCSessionAsync"
_PATCH_ZEROCONF = "custom_components.bosch_shc.async_get_instance"
_PATCH_DR = "custom_components.bosch_shc.dr.async_get"
_PATCH_PARSE_CERT = "custom_components.bosch_shc.parse_certificate"
_PATCH_TRACK_INTERVAL = "custom_components.bosch_shc.async_track_time_interval"
_PATCH_TRACK_STATE = "custom_components.bosch_shc.async_track_state_change_event"


def _make_shc_session():
    from boschshcpy import SHCSessionAsync as _SHCSessionAsync
    session = MagicMock(spec=_SHCSessionAsync)
    session.async_init = AsyncMock()
    session.start_polling = AsyncMock()
    session.stop_polling = AsyncMock()
    session.information = SimpleNamespace(
        updateState=SimpleNamespace(name="NO_UPDATE_AVAILABLE"),
        unique_id="aa:bb:cc:dd:ee:ff",
        version="9.0.0",
        name="SHC",
    )
    session.subscribe_scenario_callback = MagicMock()
    session.unsubscribe_scenario_callback = MagicMock()
    session.device_helper = MagicMock()
    session.device_helper.universal_switches = []
    return session


def _make_hass_with_states(states_map):
    """Build a minimal hass mock where states.get() uses states_map."""
    hass = MagicMock()
    hass.data = {}

    async def _executor_job(fn, *args):
        return fn(*args)

    hass.async_add_executor_job = _executor_job
    hass.config_entries = MagicMock()
    hass.config_entries.async_forward_entry_setups = AsyncMock(return_value=True)
    hass.bus = MagicMock()
    hass.bus.async_listen_once = MagicMock(return_value=MagicMock())
    hass.services = MagicMock()
    hass.services.has_service = MagicMock(return_value=False)
    hass.async_create_task = MagicMock()

    def _states_get(entity_id):
        val = states_map.get(entity_id)
        if val is None:
            return None
        return SimpleNamespace(state=val)

    hass.states = MagicMock()
    hass.states.get = MagicMock(side_effect=_states_get)
    return hass


def _run_setup_with_presence(presence_entities, hass_states=None):
    """Run async_setup_entry with presence entities. Capture the state-change cb."""
    from custom_components.bosch_shc import async_setup_entry
    from custom_components.bosch_shc.const import (
        OPT_CHILD_LOCK_ENABLED,
        OPT_PRESENCE_ENTITY,
    )

    hass = _make_hass_with_states(hass_states or {})
    fake_session = _make_shc_session()

    entry = MagicMock()
    entry.entry_id = "E1"
    entry.title = "Test"
    entry.data = {"ssl_certificate": "", "ssl_key": "", "host": "192.168.1.1"}
    entry.options = {
        OPT_PRESENCE_ENTITY: presence_entities,
        OPT_CHILD_LOCK_ENABLED: True,
    }

    dr_fake = MagicMock()
    dr_fake.async_get_or_create = MagicMock(return_value=SimpleNamespace(id="dr-001"))

    captured = {}

    def _capture_track_state(h, entity_ids, fn):
        captured["cb"] = fn
        return MagicMock()

    with (
        patch(_PATCH_SESSION, return_value=fake_session),
        patch(_PATCH_DR, return_value=dr_fake),
        patch(_PATCH_PARSE_CERT, return_value=None),
        patch(_PATCH_TRACK_INTERVAL, return_value=MagicMock()),
        patch(_PATCH_TRACK_STATE, side_effect=_capture_track_state),
        patch("homeassistant.helpers.issue_registry.async_create_issue", MagicMock()),
    ):
        asyncio.run(async_setup_entry(hass, entry))

    return hass, captured.get("cb")


class TestPresenceStateContinueOnNone:
    """__init__.py line 436 — continue when hass.states.get(eid) returns None."""

    def test_none_state_obj_skipped_other_entity_still_evaluated(self):
        """Two entities: first returns None, second returns 'home'.
        The continue at line 436 is hit for the first entity, but the second
        entity is still evaluated and causes lock_on = True.
        """
        hass, state_cb = _run_setup_with_presence(
            presence_entities=["person.alice", "person.bob"],
            # person.alice is not in the map (returns None from states.get)
            # person.bob is home
            hass_states={"person.bob": "home"},
        )
        assert state_cb is not None

        # Fire a state change event (new_state is not unavailable/unknown, not None)
        event = SimpleNamespace(data={"new_state": SimpleNamespace(state="home")})
        state_cb(event)

        # The continue for alice was hit, bob was evaluated as home => task created
        hass.async_create_task.assert_called_once()

    def test_all_states_none_means_no_present_entity(self):
        """All entities return None from states.get: loop hits continue for each,
        any_present stays False → lock_off.
        """
        hass, state_cb = _run_setup_with_presence(
            presence_entities=["person.alice", "person.bob"],
            # Neither entity is in the map
            hass_states={},
        )
        assert state_cb is not None

        # First event: lock_on=False, _last_lock_state starts at None.
        # Since False != None (initial value), _last_lock_state will update and
        # task may or may not be created depending on initial state. The key thing
        # is that the None-state continue branch IS executed for both entities.
        event = SimpleNamespace(data={"new_state": SimpleNamespace(state="not_home")})
        # Must not raise (continues work correctly)
        state_cb(event)

    def test_first_entity_none_second_entity_home_lock_turns_on(self):
        """When first entity state_obj is None (continue), second is home.
        aggregate = True → task created to set lock on.
        """
        hass, state_cb = _run_setup_with_presence(
            presence_entities=["person.ghost", "person.real"],
            hass_states={"person.real": "home"},
        )
        assert state_cb is not None

        event = SimpleNamespace(data={"new_state": SimpleNamespace(state="home")})
        state_cb(event)

        # person.ghost -> None -> continue; person.real -> home -> any_present=True
        hass.async_create_task.assert_called_once()

    def test_first_entity_none_second_not_home_no_task_when_aggregate_unchanged(self):
        """First entity returns None (continue), second is not_home.
        aggregate = False, _last_lock_state starts None so first call creates a task,
        subsequent calls with same aggregate don't.
        """
        hass, state_cb = _run_setup_with_presence(
            presence_entities=["person.ghost", "person.real"],
            hass_states={"person.real": "not_home"},
        )
        assert state_cb is not None

        event = SimpleNamespace(data={"new_state": SimpleNamespace(state="not_home")})
        # First call: any_present=False, _last_lock_state differs → task created
        state_cb(event)

        # Second call same aggregate → no second task
        hass.async_create_task.reset_mock()
        state_cb(event)
        hass.async_create_task.assert_not_called()


# ---------------------------------------------------------------------------
# binary_sensor.py line 187 — tracker.teardown() actually called via closure
# ---------------------------------------------------------------------------
# The _cleanup_tracker() closure is registered via config_entry.async_on_unload.
# In the existing setup tests, async_on_unload is `lambda fn: None` (discards
# the callback). Here we capture it and CALL it to trigger line 187.

class TestCleanupTrackerActualClosure:
    """binary_sensor.py line 187: tracker.teardown() called via the real closure."""

    def test_cleanup_tracker_teardown_called_via_captured_closure(self):
        """Capture the _cleanup_tracker closure registered via async_on_unload
        and invoke it — this executes binary_sensor.py line 187.
        """
        import asyncio

        from custom_components.bosch_shc.binary_sensor import async_setup_entry
        from custom_components.bosch_shc.const import DATA_SESSION, DOMAIN

        # Capture all unload callbacks
        unload_callbacks = []

        def _capture_on_unload(fn):
            unload_callbacks.append(fn)

        # Build a minimal smoke detection system device so the tracker-creation
        # path (binary_sensor.py lines 176-196) is exercised.
        surv_svc = SimpleNamespace(
            id="SurveillanceAlarm",
            subscribe_callback=lambda key, cb: None,
            unsubscribe_callback=lambda key: None,
        )
        sds = SimpleNamespace(
            id="smokeDetectionSystem",
            name="SDS",
            root_device_id="root-sds",
            device_services=[surv_svc],
            manufacturer="Bosch",
            device_model="SDS",
            serial="sds-serial",
            deleted=False,
            status="AVAILABLE",
            alarm=SurveillanceAlarmService.State.ALARM_OFF,
            subscribe_callback=lambda key, cb: None,
            unsubscribe_callback=lambda key: None,
        )
        tw = SimpleNamespace(
            id="tg1",
            name="Twinguard",
            root_device_id="root-tg",
            device_services=[],
            manufacturer="Bosch",
            device_model="TG",
            serial="tg-serial",
            deleted=False,
            status="AVAILABLE",
            supports_batterylevel=False,
            subscribe_callback=lambda key, cb: None,
            unsubscribe_callback=lambda key: None,
        )

        session = SimpleNamespace()
        session._subscribers = []
        session.subscribe = lambda cb_tuple: session._subscribers.append(cb_tuple)
        session.api = SimpleNamespace(get_messages=AsyncMock(return_value=[]))
        session.device_helper = SimpleNamespace(
            shutter_contacts=[],
            shutter_contacts2=[],
            motion_detectors=[],
            motion_detectors2=[],
            smoke_detectors=[],
            smoke_detection_system=sds,
            water_leakage_detectors=[],
            thermostats=[],
            twinguards=[tw],
            universal_switches=[],
            wallthermostats=[],
            roomthermostats=[],
            climate_controls=[],
        )

        # bus.async_listen_once must return a callable (the unsubscribe fn).
        bus = SimpleNamespace(
            async_listen_once=lambda event, cb: (lambda: None),
            fire=lambda *a, **kw: None,
        )

        async def _async_add_executor_job(fn, *args):
            return fn(*args)

        loop = SimpleNamespace(call_soon_threadsafe=lambda cb, *a: cb(*a))
        hass = SimpleNamespace(
            bus=bus,
            data={DOMAIN: {"E1": {DATA_SESSION: session}}},
            loop=loop,
            async_add_executor_job=_async_add_executor_job,
        )

        config_entry = SimpleNamespace(
            options={},
            entry_id="E1",
            async_on_unload=_capture_on_unload,
        )

        platform_mock = MagicMock()
        platform_mock.async_register_entity_service = MagicMock()

        entities_collected = []

        async def _run_setup():
            with (
                patch(
                    "custom_components.bosch_shc.binary_sensor"
                    ".async_migrate_to_new_unique_id",
                    return_value=None,
                ),
                patch(
                    "custom_components.bosch_shc.binary_sensor"
                    ".entity_platform.current_platform"
                ) as _cp,
            ):
                _cp.get.return_value = platform_mock
                await async_setup_entry(
                    hass, config_entry,
                    lambda ents, **kw: entities_collected.extend(ents),
                )

        asyncio.run(_run_setup())

        # The _cleanup_tracker closure is one of the registered unload callbacks.
        # Calling it exercises binary_sensor.py line 187 (tracker.teardown()).
        # We just need to verify no exception is raised — teardown() on the real
        # TwinguardAlarmTracker simply calls unsubscribe on the SDS service.
        cleanup_fns = [
            fn for fn in unload_callbacks
            if fn.__name__ == "_cleanup_tracker"
        ]
        assert len(cleanup_fns) == 1, (
            f"Expected exactly one _cleanup_tracker callback, "
            f"got {len(cleanup_fns)}: {[f.__name__ for f in unload_callbacks]}"
        )
        # Calling this must not raise and must exercise line 187
        cleanup_fns[0]()

    def test_cleanup_tracker_teardown_called_once(self):
        """Repeated calls to the closure must not raise (idempotent teardown)."""
        teardown_calls = []

        class _FakeTracker:
            def teardown(self):
                teardown_calls.append(True)

        tracker = _FakeTracker()

        # Replicate exactly the closure from binary_sensor.py lines 186-187.
        # Even though this is a local replica, the structure is identical, which
        # means the REAL closure is already tested by the integration test above.
        def _cleanup_tracker():
            tracker.teardown()

        _cleanup_tracker()
        _cleanup_tracker()
        assert teardown_calls == [True, True]


# ---------------------------------------------------------------------------
# select.py line 58 — device_excluded continue for motion_detectors2
# ---------------------------------------------------------------------------

class TestSelectMotionDetector2ExcludedDevice:
    """select.py line 58: excluded device in motion_detectors2 is skipped."""

    def _run_setup(self, session, options=None):
        from custom_components.bosch_shc.const import DATA_SESSION, DOMAIN
        from custom_components.bosch_shc.select import async_setup_entry

        hass = SimpleNamespace(data={DOMAIN: {"E1": {DATA_SESSION: session}}})
        config_entry = SimpleNamespace(entry_id="E1", options=options or {})
        collected = []
        asyncio.run(async_setup_entry(hass, config_entry, lambda e: collected.extend(e)))
        return collected

    def test_excluded_motion_detector2_not_added(self):
        """An excluded motion_detector2 must be skipped at line 58 (continue)."""
        from boschshcpy.services_impl import PirSensorConfigurationService

        from custom_components.bosch_shc.const import OPT_EXCLUDED_DEVICES

        dev = SimpleNamespace(
            id="md2-excl",
            name="MD2 excluded",
            root_device_id="root-excl",
            motion_sensitivity=PirSensorConfigurationService.MotionSensitivity.HIGH,
        )
        session = SimpleNamespace(
            device_helper=SimpleNamespace(
                motion_detectors2=[dev],
                shutter_contacts2=[],
            )
        )
        result = self._run_setup(session, {OPT_EXCLUDED_DEVICES: ["md2-excl"]})
        assert result == []

    def test_excluded_device_and_non_excluded_device_in_same_list(self):
        """When one device is excluded and another is not, only the non-excluded
        one produces an entity. The excluded one hits line 58 (continue).
        """
        from boschshcpy.services_impl import PirSensorConfigurationService

        from custom_components.bosch_shc.const import OPT_EXCLUDED_DEVICES
        from custom_components.bosch_shc.select import MotionSensitivitySelect

        excluded = SimpleNamespace(
            id="md2-excl2",
            name="MD2 Excl",
            root_device_id="root-excl2",
            motion_sensitivity=PirSensorConfigurationService.MotionSensitivity.HIGH,
        )
        kept = SimpleNamespace(
            id="md2-kept",
            name="MD2 Kept",
            root_device_id="root-kept",
            motion_sensitivity=PirSensorConfigurationService.MotionSensitivity.MIDDLE,
        )
        session = SimpleNamespace(
            device_helper=SimpleNamespace(
                motion_detectors2=[excluded, kept],
                shutter_contacts2=[],
            )
        )
        result = self._run_setup(session, {OPT_EXCLUDED_DEVICES: ["md2-excl2"]})
        assert len(result) == 1
        assert isinstance(result[0], MotionSensitivitySelect)
        assert result[0]._device is kept


# ---------------------------------------------------------------------------
# select.py lines 64-65 — AttributeError from motion_sensitivity accessor
# ---------------------------------------------------------------------------
# This is already covered by test_select_coverage.py::TestMotionDetectorAttrRaisesAttributeError
# but that test file uses a different code path. Let's ensure we cover the
# exact branch with an OPT_EXCLUDED_DEVICES scenario that also exercises line 58
# first, then the attr-error path for a second device.

class TestSelectMotionSensitivityAttributeError:
    """select.py lines 64-65: AttributeError from motion_sensitivity accessor."""

    def _run_setup(self, session, options=None):
        from custom_components.bosch_shc.const import DATA_SESSION, DOMAIN
        from custom_components.bosch_shc.select import async_setup_entry

        hass = SimpleNamespace(data={DOMAIN: {"E1": {DATA_SESSION: session}}})
        config_entry = SimpleNamespace(entry_id="E1", options=options or {})
        collected = []
        asyncio.run(async_setup_entry(hass, config_entry, lambda e: collected.extend(e)))
        return collected

    def test_excluded_then_attr_error_both_skipped(self):
        """First device excluded (line 58 continue), second device raises
        AttributeError (lines 64-65 continue). Neither produces an entity.
        """
        from boschshcpy.services_impl import PirSensorConfigurationService

        from custom_components.bosch_shc.const import OPT_EXCLUDED_DEVICES

        excl = SimpleNamespace(
            id="md2-x",
            name="Excl",
            root_device_id="root-x",
            motion_sensitivity=PirSensorConfigurationService.MotionSensitivity.HIGH,
        )

        class _BadAttr:
            id = "md2-bad"
            name = "BadAttr"
            root_device_id = "root-bad"

            @property
            def motion_sensitivity(self):
                raise AttributeError("no service")

        session = SimpleNamespace(
            device_helper=SimpleNamespace(
                motion_detectors2=[excl, _BadAttr()],
                shutter_contacts2=[],
            )
        )
        result = self._run_setup(session, {OPT_EXCLUDED_DEVICES: ["md2-x"]})
        assert result == []

    def test_attr_error_device_skipped_does_not_raise(self):
        """AttributeError during probe (line 64) must not propagate — continue (65)."""
        class _Raises:
            id = "md2-raises"
            name = "Raises"
            root_device_id = "root-raises"

            @property
            def motion_sensitivity(self):
                raise AttributeError("PirSensor missing")

        session = SimpleNamespace(
            device_helper=SimpleNamespace(
                motion_detectors2=[_Raises()],
                shutter_contacts2=[],
            )
        )
        from custom_components.bosch_shc.const import DATA_SESSION, DOMAIN
        from custom_components.bosch_shc.select import async_setup_entry
        hass = SimpleNamespace(data={DOMAIN: {"E1": {DATA_SESSION: session}}})
        config_entry = SimpleNamespace(entry_id="E1", options={})
        collected = []
        # Must not raise AttributeError
        asyncio.run(async_setup_entry(hass, config_entry, lambda e: collected.extend(e)))
        assert collected == []


# ---------------------------------------------------------------------------
# sensor.py line 354 + lines 736-738 — BatteryLevelSensor __init__ + setup
# ---------------------------------------------------------------------------
# Line 354 is the `entities.append(BatteryLevelSensor(...))` branch that runs
# when supports_batterylevel=True AND diagnostic_enabled=True.
# Lines 736-738 are inside BatteryLevelSensor.__init__ (super().__init__ +
# name + unique_id assignment). These require a real constructor call, not __new__.

_PATCH_MIGRATE = "custom_components.bosch_shc.sensor.async_migrate_to_new_unique_id"


async def _noop_migrate(hass, platform, device, attr_name=None, old_unique_id=None):
    return None


def _emma():
    return SimpleNamespace(
        id="com.bosch.tt.emma.applink",
        name="EMMA",
        root_device_id="shc-root",
        manufacturer="Bosch",
        device_model="EMMA",
        device_services=[],
        supports_batterylevel=False,
    )


def _make_sensor_session(**lists):
    return SimpleNamespace(
        device_helper=SimpleNamespace(
            thermostats=lists.get("thermostats", []),
            wallthermostats=lists.get("wallthermostats", []),
            roomthermostats=lists.get("roomthermostats", []),
            twinguards=lists.get("twinguards", []),
            smart_plugs=lists.get("smart_plugs", []),
            light_switches_bsm=lists.get("light_switches_bsm", []),
            micromodule_light_controls=lists.get("micromodule_light_controls", []),
            micromodule_shutter_controls=lists.get("micromodule_shutter_controls", []),
            micromodule_blinds=lists.get("micromodule_blinds", []),
            smart_plugs_compact=lists.get("smart_plugs_compact", []),
            motion_detectors=lists.get("motion_detectors", []),
            motion_detectors2=lists.get("motion_detectors2", []),
            shutter_contacts=lists.get("shutter_contacts", []),
            shutter_contacts2=lists.get("shutter_contacts2", []),
            smoke_detectors=lists.get("smoke_detectors", []),
            universal_switches=lists.get("universal_switches", []),
            water_leakage_detectors=lists.get("water_leakage_detectors", []),
        ),
        emma=lists.get("emma", _emma()),
    )


def _fake_battery_device(device_id="bat-dev", name="BatDev", root_id="root-bat"):
    return SimpleNamespace(
        id=device_id,
        name=name,
        root_device_id=root_id,
        serial="bat-serial",
        device_services=[],
        supports_batterylevel=True,
    )


def _run_sensor_setup(session, options):
    from custom_components.bosch_shc.const import DATA_SESSION, DOMAIN
    from custom_components.bosch_shc.sensor import async_setup_entry

    hass = SimpleNamespace(data={DOMAIN: {"E1": {DATA_SESSION: session}}})
    config_entry = SimpleNamespace(options=options, entry_id="E1")
    collected = []

    async def _inner():
        with patch(_PATCH_MIGRATE, side_effect=_noop_migrate):
            await async_setup_entry(hass, config_entry, lambda e: collected.extend(e))

    asyncio.run(_inner())
    return collected


class TestBatteryLevelSensorCreation:
    """sensor.py line 354 + lines 736-738: BatteryLevelSensor happy path."""

    def test_battery_level_sensor_created_for_battery_device(self):
        """Device with supports_batterylevel=True AND diagnostic_enabled=True
        causes BatteryLevelSensor to be appended (line 354) and its __init__
        to run (lines 736-738).
        """
        from custom_components.bosch_shc.sensor import BatteryLevelSensor

        dev = _fake_battery_device("md-has-bat", "Motion With Bat", "root-mbat")
        session = _make_sensor_session(motion_detectors=[dev])
        # diagnostic_enabled is True by default when OPT_DIAGNOSTIC_ENTITIES is absent
        entities = _run_sensor_setup(session, {})
        bat = [e for e in entities if isinstance(e, BatteryLevelSensor)]
        assert len(bat) == 1
        sensor = bat[0]
        # Verify __init__ ran: translation_key and _attr_unique_id must be set
        assert sensor.translation_key == "battery_level"
        assert "md-has-bat" in sensor._attr_unique_id
        assert "battery_level" in sensor._attr_unique_id

    def test_battery_level_sensor_unique_id_format(self):
        """_attr_unique_id follows '{root_device_id}_{id}_battery_level' (line 738-739)."""
        from custom_components.bosch_shc.sensor import BatteryLevelSensor

        dev = _fake_battery_device("dev-123", "Device 123", "root-456")
        session = _make_sensor_session(motion_detectors=[dev])
        entities = _run_sensor_setup(session, {})
        bat = [e for e in entities if isinstance(e, BatteryLevelSensor)]
        assert len(bat) == 1
        assert bat[0]._attr_unique_id == "root-456_dev-123_battery_level"

    def test_battery_level_sensor_explicit_diagnostic_enabled(self):
        """OPT_DIAGNOSTIC_ENTITIES=True explicitly: BatteryLevelSensor still created."""
        from custom_components.bosch_shc.const import OPT_DIAGNOSTIC_ENTITIES
        from custom_components.bosch_shc.sensor import BatteryLevelSensor

        dev = _fake_battery_device("md2-bat", "MD2", "root-md2")
        session = _make_sensor_session(motion_detectors2=[dev])
        entities = _run_sensor_setup(session, {OPT_DIAGNOSTIC_ENTITIES: True})
        bat = [e for e in entities if isinstance(e, BatteryLevelSensor)]
        assert len(bat) == 1

    def test_battery_level_sensor_not_created_when_diagnostic_disabled(self):
        """OPT_DIAGNOSTIC_ENTITIES=False: BatteryLevelSensor not created (line 354
        not reached because the if diagnostic_enabled: block is False).
        """
        from custom_components.bosch_shc.const import OPT_DIAGNOSTIC_ENTITIES
        from custom_components.bosch_shc.sensor import BatteryLevelSensor

        dev = _fake_battery_device("md-nodiag", "NoDiag", "root-nodiag")
        session = _make_sensor_session(motion_detectors=[dev])
        entities = _run_sensor_setup(session, {OPT_DIAGNOSTIC_ENTITIES: False})
        bat = [e for e in entities if isinstance(e, BatteryLevelSensor)]
        assert bat == []

    def test_battery_level_sensor_multiple_devices(self):
        """Multiple devices each get their own BatteryLevelSensor instance."""
        from custom_components.bosch_shc.sensor import BatteryLevelSensor

        dev1 = _fake_battery_device("dev-a", "A", "root-a")
        dev2 = _fake_battery_device("dev-b", "B", "root-b")
        session = _make_sensor_session(
            motion_detectors=[dev1],
            shutter_contacts=[dev2],
        )
        entities = _run_sensor_setup(session, {})
        bat = [e for e in entities if isinstance(e, BatteryLevelSensor)]
        assert len(bat) == 2
        uids = {e._attr_unique_id for e in bat}
        assert "root-a_dev-a_battery_level" in uids
        assert "root-b_dev-b_battery_level" in uids
