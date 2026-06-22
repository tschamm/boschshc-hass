"""Unit tests for binary_sensor.py — extra coverage gaps.

Targets lines not covered by test_binary_sensor_unit.py,
test_binary_sensor_setup.py, test_binary_sensor_coverage.py, or
test_thread_safety_fire.py:

Lines 84-85    : shutter_contacts device_excluded continue
Lines 109-110  : motion_detectors device_excluded continue
Lines 123-124  : motion_detectors2 device_excluded continue
Lines 152-153  : smoke_detectors device_excluded continue
Lines 199-200  : twinguards device_excluded continue (inside SDS branch)
Lines 210-211  : water_leakage_detectors device_excluded continue
Lines 223-224  : shutter_contacts2 vibration branch device_excluded continue
Lines 246-247  : big battery loop device_excluded continue
Lines 262-263  : climate_controls device_excluded continue
Lines 302-304  : climate_controls device_excluded continue (second CallForHeat path)
Lines 707-713  : TwinguardAlarmTracker.alarm_state — ValueError → None + warning
Line 795       : TwinguardAlarmTracker.refresh() no-change early return
Lines 870-871  : TwinguardSmokeAlarmSensor.async_added_to_hass (register_listener)
Lines 875-876  : TwinguardSmokeAlarmSensor.async_will_remove_from_hass (unregister_listener)

Pattern: __new__ bypass + SimpleNamespace; asyncio.run for async tests.
No HA harness.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from boschshcpy import (
    SHCBatteryDevice,
    SHCSmokeDetectionSystem,
    SHCSmokeDetector,
    SHCShutterContact,
    SHCShutterContact2Plus,
    SHCWaterLeakageSensor,
)

from custom_components.bosch_shc.binary_sensor import (
    BatterySensor,
    CallForHeatSensor,
    TwinguardAlarmTracker,
    TwinguardSmokeAlarmSensor,
    async_setup_entry,
)
from custom_components.bosch_shc.const import DATA_SESSION, DOMAIN, OPT_EXCLUDED_DEVICES


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _fake_device(device_id="dev1", name="FakeDev", root_device_id="root1",
                 serial="ser1", supports_batterylevel=False, device_services=None,
                 **extra):
    return SimpleNamespace(
        id=device_id,
        name=name,
        root_device_id=root_device_id,
        serial=serial,
        device_services=device_services or [],
        supports_batterylevel=supports_batterylevel,
        manufacturer="Bosch",
        device_model="FakeModel",
        deleted=False,
        status="AVAILABLE",
        subscribe_callback=lambda key, cb: None,
        unsubscribe_callback=lambda key: None,
        **extra,
    )


def _make_service(service_id):
    return SimpleNamespace(
        id=service_id,
        subscribe_callback=lambda k, cb: None,
        unsubscribe_callback=lambda k: None,
    )


def _make_hass():
    async def _async_add_executor_job(fn, *args):
        return fn(*args)

    return SimpleNamespace(
        bus=SimpleNamespace(
            async_listen_once=lambda event, cb: (lambda: None),
            fire=lambda *a, **kw: None,
        ),
        loop=SimpleNamespace(call_soon_threadsafe=lambda cb, *a: cb(*a)),
        data={},
        async_add_executor_job=_async_add_executor_job,
    )


def _make_fake_session(**lists):
    return SimpleNamespace(
        _subscribers=[],
        subscribe=lambda cb: None,
        api=SimpleNamespace(get_messages=lambda: []),
        device_helper=SimpleNamespace(
            shutter_contacts=lists.get("shutter_contacts", []),
            shutter_contacts2=lists.get("shutter_contacts2", []),
            motion_detectors=lists.get("motion_detectors", []),
            motion_detectors2=lists.get("motion_detectors2", []),
            smoke_detectors=lists.get("smoke_detectors", []),
            smoke_detection_system=lists.get("smoke_detection_system", None),
            water_leakage_detectors=lists.get("water_leakage_detectors", []),
            thermostats=lists.get("thermostats", []),
            twinguards=lists.get("twinguards", []),
            universal_switches=lists.get("universal_switches", []),
            wallthermostats=lists.get("wallthermostats", []),
            roomthermostats=lists.get("roomthermostats", []),
            climate_controls=lists.get("climate_controls", []),
        ),
    )


def _run_setup_with_options(session, options):
    """Run async_setup_entry with custom options. Returns list of collected entities."""
    hass = _make_hass()
    hass.data = {DOMAIN: {"E1": {DATA_SESSION: session}}}
    config_entry = SimpleNamespace(
        options=options, entry_id="E1",
        async_on_unload=lambda fn: None,
    )
    collected = []

    def _add_entities(entity_list, update_before_add=False):
        collected.extend(entity_list)

    platform_mock = MagicMock()
    platform_mock.async_register_entity_service = MagicMock()

    async def _inner():
        with (
            patch(
                "custom_components.bosch_shc.binary_sensor.async_migrate_to_new_unique_id",
                return_value=None,
            ),
            patch(
                "custom_components.bosch_shc.binary_sensor.entity_platform.current_platform"
            ) as _cp,
        ):
            _cp.get.return_value = platform_mock
            await async_setup_entry(hass, config_entry, _add_entities)

    asyncio.run(_inner())
    return collected


def _excl(*ids):
    return {OPT_EXCLUDED_DEVICES: list(ids)}


# ---------------------------------------------------------------------------
# 1. device_excluded continue branches — comprehensive setup_entry tests
# ---------------------------------------------------------------------------

class TestBinarySensorSetupExcludedBranches:
    """Each excluded device type must be skipped (continue) in async_setup_entry."""

    # --- shutter_contacts (line 84-85) ---

    def test_excluded_shutter_contact_not_in_entities(self):
        dev = _fake_device("sc-excl")
        dev.state = SHCShutterContact.ShutterContactService.State.CLOSED
        dev.device_class = "REGULAR_WINDOW"
        session = _make_fake_session(shutter_contacts=[dev])
        # Shutter contacts are added via async_add_shuttercontact callback (not collected),
        # but the excluded device must not trigger the callback.
        entities = _run_setup_with_options(session, _excl("sc-excl"))
        ids = [getattr(e, "_device", None) and e._device.id for e in entities]
        assert "sc-excl" not in ids

    # --- motion_detectors (line 109-110) ---

    def test_excluded_motion_detector_not_in_entities(self):
        dev = _fake_device("md-excl", device_services=[_make_service("LatestMotion")])
        dev.latestmotion = None
        session = _make_fake_session(motion_detectors=[dev])
        entities = _run_setup_with_options(session, _excl("md-excl"))
        ids = [getattr(e, "_device", None) and e._device.id for e in entities]
        assert "md-excl" not in ids

    # --- motion_detectors2 (line 123-124) ---

    def test_excluded_motion_detector2_not_in_entities(self):
        dev = _fake_device("md2-excl", device_services=[_make_service("LatestMotion")])
        dev.latestmotion = None
        session = _make_fake_session(motion_detectors2=[dev])
        entities = _run_setup_with_options(session, _excl("md2-excl"))
        ids = [getattr(e, "_device", None) and e._device.id for e in entities]
        assert "md2-excl" not in ids

    # --- smoke_detectors (line 152-153) ---

    def test_excluded_smoke_detector_not_in_entities(self):
        dev = _fake_device("sd-excl", device_services=[_make_service("Alarm")])
        dev.alarmstate = SHCSmokeDetector.AlarmService.State.IDLE_OFF
        dev.smokedetectorcheck_state = SHCSmokeDetector.SmokeDetectorCheckService.State.NONE
        session = _make_fake_session(smoke_detectors=[dev])
        entities = _run_setup_with_options(session, _excl("sd-excl"))
        from custom_components.bosch_shc.binary_sensor import SmokeDetectorSensor
        smoke_ents = [e for e in entities if isinstance(e, SmokeDetectorSensor)]
        assert smoke_ents == []

    # --- twinguards (line 199-200, inside SDS branch) ---

    def test_excluded_twinguard_not_in_entities_when_sds_present(self):
        surv_svc = _make_service("SurveillanceAlarm")
        sds = _fake_device("sds-1", device_services=[surv_svc])
        sds.alarm = SHCSmokeDetectionSystem.SurveillanceAlarmService.State.ALARM_OFF
        tw = _fake_device("tw-excl")
        session = _make_fake_session(
            smoke_detection_system=sds, twinguards=[tw]
        )
        entities = _run_setup_with_options(session, _excl("tw-excl"))
        ids = [getattr(e, "_device", None) and e._device.id for e in entities]
        assert "tw-excl" not in ids

    # --- water_leakage_detectors (line 210-211) ---

    def test_excluded_water_leakage_detector_not_in_entities(self):
        dev = _fake_device("wl-excl")
        dev.leakage_state = SHCWaterLeakageSensor.WaterLeakageSensorService.State.NO_LEAKAGE
        dev.push_notification_state = SHCWaterLeakageSensor.WaterLeakageSensorTiltService.State.ENABLED
        dev.acoustic_signal_state = SHCWaterLeakageSensor.WaterLeakageSensorTiltService.State.ENABLED
        session = _make_fake_session(water_leakage_detectors=[dev])
        entities = _run_setup_with_options(session, _excl("wl-excl"))
        from custom_components.bosch_shc.binary_sensor import WaterLeakageDetectorSensor
        wl_ents = [e for e in entities if isinstance(e, WaterLeakageDetectorSensor)]
        assert wl_ents == []

    # --- shutter_contacts2 vibration check (line 223-224) ---

    def test_excluded_shutter_contact2_plus_no_vibration_sensor(self):
        dev = MagicMock(spec=SHCShutterContact2Plus)
        dev.id = "sc2p-excl"
        dev.name = "SC2Plus"
        dev.root_device_id = "root1"
        dev.device_services = []
        dev.supports_batterylevel = False
        dev.manufacturer = "Bosch"
        dev.device_model = "FakeModel"
        dev.serial = "sc2p-serial"
        dev.deleted = False
        dev.status = "AVAILABLE"
        dev.subscribe_callback = lambda k, cb: None
        dev.unsubscribe_callback = lambda k: None
        dev.state = SHCShutterContact.ShutterContactService.State.CLOSED
        dev.device_class = "REGULAR_WINDOW"
        dev.vibrationsensor = (
            SHCShutterContact2Plus.VibrationSensorService.State.NO_VIBRATION
        )
        session = _make_fake_session(shutter_contacts2=[dev])
        entities = _run_setup_with_options(session, _excl("sc2p-excl"))
        from custom_components.bosch_shc.binary_sensor import ShutterContactVibrationSensor
        vib_ents = [e for e in entities if isinstance(e, ShutterContactVibrationSensor)]
        assert vib_ents == []

    # --- big battery loop device_excluded (line 246-247) ---

    def test_excluded_device_skipped_in_battery_loop(self):
        dev = _fake_device("md-bat-excl", supports_batterylevel=True)
        dev.batterylevel = SHCBatteryDevice.BatteryLevelService.State.OK
        dev.latestmotion = None
        dev.device_services = [_make_service("LatestMotion")]
        session = _make_fake_session(motion_detectors=[dev])
        entities = _run_setup_with_options(session, _excl("md-bat-excl"))
        bat_ents = [e for e in entities if isinstance(e, BatterySensor)]
        assert bat_ents == []

    # --- climate_controls (CallForHeatSensor) device_excluded (line 262-263) ---

    def test_excluded_climate_control_not_in_entities(self):
        """Excluded climate control must be skipped — no CallForHeatSensor."""
        dev = _fake_device("cc-excl")
        dev.has_demand = False
        session = _make_fake_session(climate_controls=[dev])
        entities = _run_setup_with_options(session, _excl("cc-excl"))
        cfh_ents = [e for e in entities if isinstance(e, CallForHeatSensor)]
        assert cfh_ents == []

    def test_non_excluded_climate_control_in_entities(self):
        """Non-excluded climate control must produce a CallForHeatSensor."""
        dev = _fake_device("cc-keep")
        dev.has_demand = True
        session = _make_fake_session(climate_controls=[dev])
        entities = _run_setup_with_options(session, {})
        cfh_ents = [e for e in entities if isinstance(e, CallForHeatSensor)]
        assert len(cfh_ents) == 1

    def test_mix_excluded_and_kept_climate_control(self):
        kept = _fake_device("cc-a")
        kept.has_demand = False
        excl = _fake_device("cc-b")
        excl.has_demand = False
        session = _make_fake_session(climate_controls=[kept, excl])
        entities = _run_setup_with_options(session, _excl("cc-b"))
        cfh_ents = [e for e in entities if isinstance(e, CallForHeatSensor)]
        assert len(cfh_ents) == 1
        assert cfh_ents[0]._device.id == "cc-a"

    # --- All excluded combined sanity check ---

    def test_all_device_types_excluded_yields_empty_list(self):
        """Excluding all devices leaves the entity list empty."""
        excl_ids = [f"dev-{i}" for i in range(5)]
        md = _fake_device(excl_ids[0], device_services=[_make_service("LatestMotion")])
        md.latestmotion = None
        wl = _fake_device(excl_ids[1])
        wl.leakage_state = SHCWaterLeakageSensor.WaterLeakageSensorService.State.NO_LEAKAGE
        wl.push_notification_state = SHCWaterLeakageSensor.WaterLeakageSensorTiltService.State.ENABLED
        wl.acoustic_signal_state = SHCWaterLeakageSensor.WaterLeakageSensorTiltService.State.ENABLED
        cc = _fake_device(excl_ids[2])
        cc.has_demand = False
        th = _fake_device(excl_ids[3], supports_batterylevel=True)
        th.batterylevel = SHCBatteryDevice.BatteryLevelService.State.OK
        sc = _fake_device(excl_ids[4])
        sc.state = SHCShutterContact.ShutterContactService.State.CLOSED
        sc.device_class = "GENERIC"
        session = _make_fake_session(
            motion_detectors=[md],
            water_leakage_detectors=[wl],
            climate_controls=[cc],
            thermostats=[th],
            shutter_contacts=[sc],
        )
        entities = _run_setup_with_options(session, _excl(*excl_ids))
        assert entities == []


# ---------------------------------------------------------------------------
# 2. TwinguardAlarmTracker.alarm_state — ValueError → None + LOGGER.warning
#    (lines 707-713)
# ---------------------------------------------------------------------------

class _RaisingAlarm:
    """Simulates SmokeDetectionSystem.alarm where .name raises ValueError."""
    @property
    def name(self):
        raise ValueError("unknown alarm state 42")


class TestTwinguardAlarmTrackerAlarmStateValueError:
    def _make_tracker(self, alarm_value):
        surv_svc = SimpleNamespace(
            id="SurveillanceAlarm",
            subscribe_callback=lambda k, cb: None,
            unsubscribe_callback=lambda k: None,
        )
        sds = SimpleNamespace(
            id="sds-x",
            name="SDS",
            alarm=alarm_value,
            device_services=[surv_svc],
        )
        hass = _make_hass()
        session = SimpleNamespace(
            api=SimpleNamespace(get_messages=AsyncMock(return_value=[])),
        )
        return TwinguardAlarmTracker(
            session=session, smoke_detection_system=sds, hass=hass
        )

    def test_alarm_state_value_error_returns_none(self):
        """ValueError on alarm.name must return None, not crash (line 707-713)."""
        tracker = self._make_tracker(_RaisingAlarm())
        result = tracker.alarm_state
        assert result is None

    def test_alarm_state_value_error_logs_warning(self):
        """ValueError on alarm.name must log a LOGGER.warning."""
        tracker = self._make_tracker(_RaisingAlarm())
        with patch("custom_components.bosch_shc.binary_sensor.LOGGER") as mock_log:
            tracker.alarm_state
        mock_log.warning.assert_called_once()

    def test_alarm_state_valid_returns_name(self):
        """Valid alarm.name must be returned as-is."""
        tracker = self._make_tracker(
            SHCSmokeDetectionSystem.SurveillanceAlarmService.State.ALARM_OFF
        )
        assert tracker.alarm_state == "ALARM_OFF"

    def test_alarm_state_alarm_on_returns_name(self):
        tracker = self._make_tracker(
            SHCSmokeDetectionSystem.SurveillanceAlarmService.State.ALARM_ON
        )
        assert tracker.alarm_state == "ALARM_ON"


# ---------------------------------------------------------------------------
# 3. TwinguardAlarmTracker.refresh() — no-change early return (line 795)
# ---------------------------------------------------------------------------

class TestTwinguardAlarmTrackerRefreshNoChange:
    """refresh() with unchanged trigger_ids AND unchanged alarm_state → early return."""

    def _make_tracker_alarm_off(self):
        surv_svc = SimpleNamespace(
            id="SurveillanceAlarm",
            subscribe_callback=lambda k, cb: None,
            unsubscribe_callback=lambda k: None,
        )
        sds = SimpleNamespace(
            id="sds-nc",
            name="SDS",
            alarm=SHCSmokeDetectionSystem.SurveillanceAlarmService.State.ALARM_OFF,
            device_services=[surv_svc],
        )
        hass = _make_hass()
        session = SimpleNamespace(
            api=SimpleNamespace(get_messages=AsyncMock(return_value=[])),
        )
        return TwinguardAlarmTracker(
            session=session, smoke_detection_system=sds, hass=hass
        )

    def test_refresh_no_change_does_not_notify_listeners(self):
        """Two consecutive refreshes with same state: listener NOT called on 2nd."""
        tracker = self._make_tracker_alarm_off()
        # First refresh: sets baseline
        asyncio.run(tracker.async_refresh())
        called = []
        tracker.register_listener(_make_hass(), lambda: called.append(1))
        # Second refresh with same state: no-change early return → listener not called
        asyncio.run(tracker.async_refresh())
        assert called == []

    def test_refresh_no_change_trigger_ids_unchanged(self):
        """After two ALARM_OFF refreshes, trigger_ids remain empty."""
        tracker = self._make_tracker_alarm_off()
        asyncio.run(tracker.async_refresh())
        asyncio.run(tracker.async_refresh())
        assert tracker._active_trigger_ids == set()

    def test_refresh_first_call_notifies_because_state_changed(self):
        """First refresh changes state from (None, {}) → listener IS called."""
        tracker = self._make_tracker_alarm_off()
        called = []
        tracker.register_listener(_make_hass(), lambda: called.append(1))
        asyncio.run(tracker.async_refresh())  # first call: _last_alarm_state was None → change detected
        assert called == [1]

    def test_refresh_no_change_last_alarm_state_preserved(self):
        """_last_alarm_state is set and preserved across a no-change refresh."""
        tracker = self._make_tracker_alarm_off()
        asyncio.run(tracker.async_refresh())
        before = tracker._last_alarm_state
        asyncio.run(tracker.async_refresh())
        assert tracker._last_alarm_state == before
        assert tracker._last_alarm_state == "ALARM_OFF"


# ---------------------------------------------------------------------------
# 4. TwinguardSmokeAlarmSensor.async_added_to_hass + async_will_remove_from_hass
#    (lines 870-871, 875-876)
# ---------------------------------------------------------------------------

class TestTwinguardSmokeAlarmSensorLifecycle:
    def _make_tracker(self):
        """Return a minimal TwinguardAlarmTracker with register/unregister mocked."""
        tracker = MagicMock(spec=TwinguardAlarmTracker)
        tracker.alarm_state = "ALARM_OFF"
        tracker.is_alarm_active_for = lambda did: False
        return tracker

    def _make_sensor(self, tracker, device_id="tw-1"):
        dev = _fake_device(device_id, name="Twinguard")
        s = TwinguardSmokeAlarmSensor.__new__(TwinguardSmokeAlarmSensor)
        s._device = dev
        s._entry_id = "E1"
        s._attr_name = "Smoke"
        s._attr_unique_id = f"root1_{device_id}_smoke"
        s._tracker = tracker
        s._tracker_listener = s._handle_tracker_update if hasattr(s, "_handle_tracker_update") else lambda: None
        # Fake hass required by super().async_added_to_hass
        s.hass = MagicMock()
        s.hass.loop = SimpleNamespace(call_soon_threadsafe=lambda cb, *a: None)
        s.schedule_update_ha_state = MagicMock()
        return s

    def test_async_added_to_hass_calls_register_listener(self):
        """async_added_to_hass must call tracker.register_listener (lines 870-871)."""
        tracker = self._make_tracker()
        s = self._make_sensor(tracker)

        async def _run():
            # Bypass SHCEntity.async_added_to_hass (needs device_services iteration)
            with patch(
                "custom_components.bosch_shc.entity.SHCEntity.async_added_to_hass",
                return_value=None,
            ):
                await s.async_added_to_hass()

        asyncio.run(_run())
        tracker.register_listener.assert_called_once()
        # Second arg is the listener callable
        call_args = tracker.register_listener.call_args[0]
        assert callable(call_args[1])

    def test_async_added_to_hass_passes_hass_to_register_listener(self):
        """register_listener must receive (hass, listener) where first arg is hass."""
        tracker = self._make_tracker()
        s = self._make_sensor(tracker)

        async def _run():
            with patch(
                "custom_components.bosch_shc.entity.SHCEntity.async_added_to_hass",
                return_value=None,
            ):
                await s.async_added_to_hass()

        asyncio.run(_run())
        call_args = tracker.register_listener.call_args[0]
        assert call_args[0] is s.hass

    def test_async_will_remove_from_hass_calls_unregister_listener(self):
        """async_will_remove_from_hass must call tracker.unregister_listener (lines 875-876)."""
        tracker = self._make_tracker()
        s = self._make_sensor(tracker)

        async def _run():
            with (
                patch(
                    "custom_components.bosch_shc.entity.SHCEntity.async_added_to_hass",
                    return_value=None,
                ),
                patch(
                    "custom_components.bosch_shc.entity.SHCEntity.async_will_remove_from_hass",
                    return_value=None,
                ),
            ):
                await s.async_added_to_hass()
                await s.async_will_remove_from_hass()

        asyncio.run(_run())
        tracker.unregister_listener.assert_called_once()

    def test_async_will_remove_from_hass_passes_listener_to_unregister(self):
        """unregister_listener receives the same listener that was registered."""
        tracker = self._make_tracker()
        s = self._make_sensor(tracker)

        async def _run():
            with (
                patch(
                    "custom_components.bosch_shc.entity.SHCEntity.async_added_to_hass",
                    return_value=None,
                ),
                patch(
                    "custom_components.bosch_shc.entity.SHCEntity.async_will_remove_from_hass",
                    return_value=None,
                ),
            ):
                await s.async_added_to_hass()
                await s.async_will_remove_from_hass()

        asyncio.run(_run())
        registered_listener = tracker.register_listener.call_args[0][1]
        unregistered_listener = tracker.unregister_listener.call_args[0][0]
        assert registered_listener is unregistered_listener

    def test_async_added_not_called_twice_registers_once(self):
        """Each async_added_to_hass call registers exactly one listener."""
        tracker = self._make_tracker()
        s = self._make_sensor(tracker)

        async def _run():
            with patch(
                "custom_components.bosch_shc.entity.SHCEntity.async_added_to_hass",
                return_value=None,
            ):
                await s.async_added_to_hass()

        asyncio.run(_run())
        assert tracker.register_listener.call_count == 1

    def test_remove_without_prior_add_still_calls_unregister(self):
        """Calling async_will_remove_from_hass without prior add calls unregister once."""
        tracker = self._make_tracker()
        s = self._make_sensor(tracker)

        async def _run():
            with patch(
                "custom_components.bosch_shc.entity.SHCEntity.async_will_remove_from_hass",
                return_value=None,
            ):
                await s.async_will_remove_from_hass()

        asyncio.run(_run())
        tracker.unregister_listener.assert_called_once()
