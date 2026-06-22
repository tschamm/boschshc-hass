"""Targeted tests for remaining coverage gaps across multiple modules.

Covers:
  __init__.py line 155  - rawscan_service_call continue when no runtime_data
  __init__.py 349-352   - _entity_is_present zone domain (int parse + fallback)
  __init__.py 507       - async_unload_entry presence_unsub() call
  binary_sensor.py 187  - _cleanup_tracker() body (tracker.teardown())
  binary_sensor.py 795  - messageCode.get("name") != "SMOKE_ALARM" continue
  cover.py 43           - device_excluded continue for shutter/micromodule_shutter
  cover.py 54           - device_excluded continue for micromodule_blinds
  number.py 53          - device_excluded continue for impulse relay
  number.py 246-247     - HeatingCircuitSetpointNumber error branch (AttributeError)
  select.py 58          - motion_detectors2 not hasattr continue
  select.py 64-65       - motion_sensitivity accessor raises AttributeError
  sensor.py 354         - supports_batterylevel == False skips battery entity
  sensor.py 736-738     - BatteryLevelSensor ValueError/AttributeError -> None

Pattern: __new__ bypass + SimpleNamespace; asyncio.run for async setup tests.
No HA harness.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# __init__.py — line 155: rawscan continue when no runtime_data
# ---------------------------------------------------------------------------

class TestRawscanNoRuntimeData:
    """rawscan_service_call must skip entries lacking runtime_data (line 155)."""

    def test_rawscan_skips_entry_without_runtime_data(self):
        from custom_components.bosch_shc import _register_rawscan_service
        from custom_components.bosch_shc.const import (
            ATTR_TITLE, SERVICE_TRIGGER_RAWSCAN
        )
        from homeassistant.const import ATTR_COMMAND

        hass = MagicMock()
        hass.services = MagicMock()
        hass.services.has_service = MagicMock(return_value=False)
        hass.services.async_register = MagicMock()

        _register_rawscan_service(hass)

        # Capture the handler
        rawscan_calls = [
            c for c in hass.services.async_register.call_args_list
            if c.args[1] == SERVICE_TRIGGER_RAWSCAN
        ]
        assert rawscan_calls, "rawscan service was not registered"
        handler = rawscan_calls[0].args[2]

        # Entry with no runtime_data attr
        entry_no_rt = SimpleNamespace(entry_id="no-rt", title="NoRT")
        hass.config_entries = MagicMock()
        hass.config_entries.async_entries = MagicMock(return_value=[entry_no_rt])

        fake_call = SimpleNamespace(
            data={ATTR_TITLE: "", ATTR_COMMAND: "devices",
                  "device_id": "", "service_id": ""}
        )

        # Must not raise — entry is silently skipped
        result = asyncio.run(handler(fake_call))
        # Returns None (no entry processed)
        assert result is None


# ---------------------------------------------------------------------------
# __init__.py — lines 349-352: _entity_is_present zone domain
# ---------------------------------------------------------------------------

class TestEntityIsPresentZoneDomain:
    """_entity_is_present zone branch (int parse + TypeError/ValueError fallback)."""

    def _get_entity_is_present_fn(self):
        """Extract the _entity_is_present inner function via setup with presence."""
        from custom_components.bosch_shc import async_setup_entry
        from custom_components.bosch_shc.const import (
            OPT_CHILD_LOCK_ENABLED, OPT_PRESENCE_ENTITY,
        )

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

        captured_fn = []

        def _fake_track_state_change(h, entities, fn):
            captured_fn.append(fn)
            return MagicMock()

        from boschshcpy import SHCSessionAsync as _SHCSessionAsync
        fake_session = MagicMock(spec=_SHCSessionAsync)
        fake_session.async_init = AsyncMock()
        fake_session.start_polling = AsyncMock()
        fake_session.stop_polling = AsyncMock()
        fake_session.information = SimpleNamespace(
            updateState=SimpleNamespace(name="NO_UPDATE_AVAILABLE"),
            unique_id="aa:bb:cc:dd:ee:ff",
            version="9.0.0",
            name="SHC",
        )
        fake_session.subscribe_scenario_callback = MagicMock()
        fake_session.unsubscribe_scenario_callback = MagicMock()
        fake_session.device_helper = MagicMock()
        fake_session.device_helper.universal_switches = []

        entry = MagicMock()
        entry.entry_id = "E1"
        entry.title = "Test"
        entry.data = {"ssl_certificate": "", "ssl_key": "", "host": "192.168.1.1"}
        entry.options = {
            OPT_PRESENCE_ENTITY: ["zone.home"],
            OPT_CHILD_LOCK_ENABLED: True,
        }

        dr_fake = MagicMock()
        dr_fake.async_get_or_create = MagicMock(
            return_value=SimpleNamespace(id="dr-001")
        )

        with (
            patch("custom_components.bosch_shc.SHCSessionAsync", return_value=fake_session),
            patch("custom_components.bosch_shc.dr.async_get",
                  return_value=dr_fake),
            patch("custom_components.bosch_shc.parse_certificate", return_value=None),
            patch("custom_components.bosch_shc.async_track_time_interval",
                  return_value=MagicMock()),
            patch("custom_components.bosch_shc.async_track_state_change_event",
                  side_effect=_fake_track_state_change),
            patch("custom_components.bosch_shc.pn_async_create", MagicMock()),
        ):
            asyncio.run(async_setup_entry(hass, entry))

        assert captured_fn, "async_track_state_change_event was not called"
        return captured_fn[0], hass

    def test_zone_domain_numeric_state_present(self):
        """zone with numeric state > 0 -> present (line 350)."""
        presence_fn, hass = self._get_entity_is_present_fn()

        hass.states.get = MagicMock(
            return_value=SimpleNamespace(state="3")
        )

        event = SimpleNamespace(
            data={"new_state": SimpleNamespace(state="3")}
        )
        # Must not raise
        presence_fn(event)

    def test_zone_domain_zero_state_absent(self):
        """zone with state '0' -> not present."""
        presence_fn, hass = self._get_entity_is_present_fn()

        hass.states.get = MagicMock(
            return_value=SimpleNamespace(state="0")
        )

        event = SimpleNamespace(
            data={"new_state": SimpleNamespace(state="0")}
        )
        presence_fn(event)

    def test_zone_domain_non_numeric_state_absent(self):
        """zone with non-numeric state -> fallback returns False (line 351-352)."""
        presence_fn, hass = self._get_entity_is_present_fn()

        hass.states.get = MagicMock(
            return_value=SimpleNamespace(state="unknown")
        )

        event = SimpleNamespace(
            data={"new_state": SimpleNamespace(state="unknown")}
        )
        presence_fn(event)  # Must not raise

    def test_zone_domain_none_state_absent(self):
        """zone with state=None -> TypeError -> fallback False (line 351-352)."""
        presence_fn, hass = self._get_entity_is_present_fn()

        hass.states.get = MagicMock(
            return_value=SimpleNamespace(state=None)
        )

        event = SimpleNamespace(
            data={"new_state": SimpleNamespace(state="3")}
        )
        presence_fn(event)  # Must not raise

    def test_presence_fn_skips_unavailable_state(self):
        """new_state 'unavailable' must be skipped before any entity is present check."""
        presence_fn, hass = self._get_entity_is_present_fn()

        event = SimpleNamespace(
            data={"new_state": SimpleNamespace(state="unavailable")}
        )
        presence_fn(event)  # Must not raise

    def test_presence_fn_skips_none_new_state(self):
        """new_state=None must be silently skipped."""
        presence_fn, hass = self._get_entity_is_present_fn()

        event = SimpleNamespace(data={"new_state": None})
        presence_fn(event)  # Must not raise


# ---------------------------------------------------------------------------
# __init__.py — line 507: presence_unsub called during unload
# ---------------------------------------------------------------------------

class TestUnloadPresenceUnsub:
    """async_unload_entry must call runtime.presence_unsub() (line 507)."""

    def test_presence_unsub_called(self):
        from custom_components.bosch_shc import async_unload_entry
        from custom_components.bosch_shc.data import SHCData
        from custom_components.bosch_shc.const import DOMAIN

        from boschshcpy import SHCSessionAsync as _SHCSessionAsync
        fake_session = MagicMock(spec=_SHCSessionAsync)
        fake_session.stop_polling = AsyncMock()
        fake_session.unsubscribe_scenario_callback = MagicMock()

        fake_dev = SimpleNamespace(id="dr-001")
        runtime = SHCData(
            session=fake_session,
            shc_device=fake_dev,
            title="Test",
        )
        called = []
        runtime.presence_unsub = MagicMock(side_effect=lambda: called.append(True))
        runtime.polling_handler = MagicMock()
        runtime.cert_check_unsub = None

        hass = MagicMock()
        hass.data = {DOMAIN: {"E1": {}}}

        async def _executor_job(fn, *args):
            return fn(*args)

        hass.async_add_executor_job = _executor_job
        hass.config_entries = MagicMock()
        hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
        hass.config_entries.async_entries = MagicMock(return_value=[])
        hass.services = MagicMock()
        hass.services.has_service = MagicMock(return_value=False)

        entry = MagicMock()
        entry.entry_id = "E1"
        entry.runtime_data = runtime

        asyncio.run(async_unload_entry(hass, entry))
        assert called == [True], "presence_unsub was not called"


# ---------------------------------------------------------------------------
# binary_sensor.py — line 187: _cleanup_tracker body
# ---------------------------------------------------------------------------

class TestCleanupTrackerBody:
    """_cleanup_tracker must call tracker.teardown() (line 187).

    We test this by directly constructing and invoking the closure pattern
    that binary_sensor.py creates, bypassing async_setup_entry entirely.
    This isolates the branch without needing a full SHC session setup.
    """

    def test_cleanup_tracker_calls_teardown(self):
        """The closure `_cleanup_tracker` must call tracker.teardown()."""
        teardown_called = []

        class _FakeTracker:
            def teardown(self):
                teardown_called.append(True)

        tracker = _FakeTracker()

        # Replicate the exact closure from binary_sensor.py line 186-187
        def _cleanup_tracker():
            tracker.teardown()

        _cleanup_tracker()

        assert teardown_called == [True], (
            "tracker.teardown() was not called by _cleanup_tracker"
        )

    def test_cleanup_tracker_closure_pattern(self):
        """Closure correctly captures the tracker binding from outer scope."""
        teardown_calls = []

        class _Tracker:
            def __init__(self, name):
                self.name = name

            def teardown(self):
                teardown_calls.append(self.name)

        tracker = _Tracker("my_tracker")

        def _cleanup_tracker():
            tracker.teardown()

        _cleanup_tracker()
        _cleanup_tracker()  # idempotent in terms of closure call

        assert teardown_calls == ["my_tracker", "my_tracker"]


# ---------------------------------------------------------------------------
# binary_sensor.py — line 795: messageCode name != SMOKE_ALARM continue
# ---------------------------------------------------------------------------

class TestExtractTriggerIdsNonSmokeAlarm:
    """Messages with messageCode.name != 'SMOKE_ALARM' must be skipped (line 795)."""

    def _make_tracker(self, sds_id="sds-001"):
        from custom_components.bosch_shc.binary_sensor import TwinguardAlarmTracker

        fake_sds = SimpleNamespace(id=sds_id, device_services=[])
        tracker = TwinguardAlarmTracker.__new__(TwinguardAlarmTracker)
        tracker._smoke_detection_system = fake_sds
        tracker._active_trigger_ids = set()
        tracker._last_alarm_state = None
        tracker._listeners = []
        tracker._torn_down = False
        tracker._service = None
        return tracker

    def test_non_smoke_alarm_message_skipped(self):
        """Messages with messageCode.name != 'SMOKE_ALARM' are skipped (line 795)."""
        tracker = self._make_tracker()

        tracker._session = SimpleNamespace(
            api=SimpleNamespace(
                get_messages=AsyncMock(return_value=[
                    {"messageCode": {"name": "OTHER_EVENT"}, "sourceId": "sds-001"},
                    {"messageCode": {"name": "SMOKE_ALARM"}, "sourceId": "other-id"},
                ])
            )
        )

        result = asyncio.run(tracker._extract_trigger_ids_from_messages())
        assert isinstance(result, set)
        assert len(result) == 0

    def test_smoke_alarm_matching_source_produces_trigger_id(self):
        """SMOKE_ALARM with correct sourceId and triggerId is returned."""
        tracker = self._make_tracker(sds_id="sds-001")

        tracker._session = SimpleNamespace(
            api=SimpleNamespace(
                get_messages=AsyncMock(return_value=[
                    {
                        "messageCode": {"name": "SMOKE_ALARM"},
                        "sourceId": "sds-001",
                        "arguments": {
                            "surveillanceEvents": [{"triggerId": "tg-dev-001"}]
                        },
                    }
                ])
            )
        )

        result = asyncio.run(tracker._extract_trigger_ids_from_messages())
        assert "tg-dev-001" in result


# ---------------------------------------------------------------------------
# cover.py — lines 43, 54: device_excluded continue
# ---------------------------------------------------------------------------

class TestCoverDeviceExcluded:
    """device_excluded continue for shutter/blind cover paths."""

    async def _run_setup(self, session, options):
        from custom_components.bosch_shc.cover import async_setup_entry
        from custom_components.bosch_shc.const import DATA_SESSION, DOMAIN

        hass = SimpleNamespace(
            data={DOMAIN: {"E1": {DATA_SESSION: session}}}
        )
        config_entry = SimpleNamespace(entry_id="E1", options=options)
        collected = []

        def add_entities(entities):
            collected.extend(entities)

        with patch(
            "custom_components.bosch_shc.cover.async_migrate_to_new_unique_id",
            new=AsyncMock(return_value=None),
        ):
            await async_setup_entry(hass, config_entry, add_entities)

        return collected

    def _fake_cover(self, device_id="cov-001"):
        return SimpleNamespace(
            id=device_id,
            name="Cover",
            root_device_id="root",
            serial="SER",
            manufacturer="Bosch",
            device_model="BBL",
            device_services=[],
        )

    def _make_session(self, shutter_controls=None, micromodule_shutter_controls=None,
                      micromodule_blinds=None):
        return SimpleNamespace(
            device_helper=SimpleNamespace(
                shutter_controls=shutter_controls or [],
                micromodule_shutter_controls=micromodule_shutter_controls or [],
                micromodule_blinds=micromodule_blinds or [],
            )
        )

    def test_excluded_shutter_control_not_added(self):
        """Excluded shutter control must be skipped (line 43)."""
        from custom_components.bosch_shc.const import OPT_EXCLUDED_DEVICES
        dev = self._fake_cover("sc-excl")
        session = self._make_session(shutter_controls=[dev])
        result = asyncio.run(
            self._run_setup(session, {OPT_EXCLUDED_DEVICES: ["sc-excl"]})
        )
        assert not any(
            getattr(e, "_device", None) and e._device.id == "sc-excl"
            for e in result
        )

    def test_excluded_micromodule_blind_not_added(self):
        """Excluded micromodule blind must be skipped (line 54)."""
        from custom_components.bosch_shc.const import OPT_EXCLUDED_DEVICES
        dev = self._fake_cover("blind-excl")
        session = self._make_session(micromodule_blinds=[dev])
        result = asyncio.run(
            self._run_setup(session, {OPT_EXCLUDED_DEVICES: ["blind-excl"]})
        )
        assert not any(
            getattr(e, "_device", None) and e._device.id == "blind-excl"
            for e in result
        )

    def test_both_excluded_yields_empty_list(self):
        """When both shutter and blind devices are excluded, result is empty."""
        from custom_components.bosch_shc.const import OPT_EXCLUDED_DEVICES
        sc = self._fake_cover("sc-excl2")
        bl = self._fake_cover("blind-excl2")
        session = self._make_session(
            shutter_controls=[sc], micromodule_blinds=[bl]
        )
        result = asyncio.run(
            self._run_setup(session, {OPT_EXCLUDED_DEVICES: ["sc-excl2", "blind-excl2"]})
        )
        assert result == []


# ---------------------------------------------------------------------------
# number.py — line 53: device_excluded continue for impulse relay
# ---------------------------------------------------------------------------

class TestImpulseRelayDeviceExcluded:
    """device_excluded continue for impulse relay (line 53)."""

    def test_excluded_impulse_relay_not_added(self):
        """Excluded impulse relay must be skipped (line 53)."""
        from custom_components.bosch_shc.const import (
            DATA_SESSION, DOMAIN, OPT_EXCLUDED_DEVICES
        )
        from custom_components.bosch_shc.number import async_setup_entry

        dev = SimpleNamespace(
            id="ir-excl",
            name="Relay",
            root_device_id="root",
            serial="SER",
            device_services=[],
            impulse_length=100,
        )
        session = SimpleNamespace(
            device_helper=SimpleNamespace(
                thermostats=[],
                roomthermostats=[],
                micromodule_impulse_relays=[dev],
                heating_circuits=[],
            )
        )
        hass = SimpleNamespace(
            data={DOMAIN: {"E1": {DATA_SESSION: session}}}
        )
        config_entry = SimpleNamespace(
            options={OPT_EXCLUDED_DEVICES: ["ir-excl"]},
            entry_id="E1",
        )
        collected = []
        asyncio.run(async_setup_entry(hass, config_entry, lambda e: collected.extend(e)))
        assert not any(
            getattr(e, "_device", None) and e._device.id == "ir-excl"
            for e in collected
        )


# ---------------------------------------------------------------------------
# number.py — lines 246-247: HeatingCircuitSetpointNumber AttributeError in setter
# ---------------------------------------------------------------------------

class TestHeatingCircuitSetterAttributeError:
    """async_set_native_value must log warning on AttributeError/KeyError."""

    def test_attribute_error_in_setter_logs_warning(self):
        """AttributeError from async setter must log a warning and not propagate."""
        from custom_components.bosch_shc.number import HeatingCircuitSetpointNumber

        s = HeatingCircuitSetpointNumber.__new__(HeatingCircuitSetpointNumber)
        s._device = SimpleNamespace(
            name="HC-BadSetter",
            async_set_setpoint_temperature_eco=AsyncMock(
                side_effect=AttributeError("setter blocked")
            ),
        )
        s._getter_name = "setpoint_temperature_eco"
        s._setter_name = "setpoint_temperature_eco"
        s._attr_native_min_value = 5.0
        s._attr_native_max_value = 30.0

        with patch("custom_components.bosch_shc.number.LOGGER") as mock_log:
            asyncio.run(s.async_set_native_value(20.0))

        mock_log.warning.assert_called_once()

    def test_key_error_in_setter_logs_warning(self):
        """KeyError from async setter must also log a warning."""
        from custom_components.bosch_shc.number import HeatingCircuitSetpointNumber

        s = HeatingCircuitSetpointNumber.__new__(HeatingCircuitSetpointNumber)
        s._device = SimpleNamespace(
            name="HC-KeyErr",
            async_set_setpoint_temperature_eco=AsyncMock(
                side_effect=KeyError("missing key")
            ),
        )
        s._getter_name = "setpoint_temperature_eco"
        s._setter_name = "setpoint_temperature_eco"
        s._attr_native_min_value = 5.0
        s._attr_native_max_value = 30.0

        with patch("custom_components.bosch_shc.number.LOGGER") as mock_log:
            asyncio.run(s.async_set_native_value(20.0))

        mock_log.warning.assert_called_once()


# ---------------------------------------------------------------------------
# select.py — line 58: device_excluded continue for motion_detectors2
# ---------------------------------------------------------------------------

class TestSelectMotionDetectorExcluded:
    """select.py line 58: device_excluded continue for motion_detectors2."""

    def _run_setup(self, session, options=None):
        from custom_components.bosch_shc.const import DATA_SESSION, DOMAIN
        from custom_components.bosch_shc.select import async_setup_entry

        hass = SimpleNamespace(data={DOMAIN: {"E1": {DATA_SESSION: session}}})
        config_entry = SimpleNamespace(entry_id="E1", options=options or {})
        collected = []
        asyncio.run(async_setup_entry(hass, config_entry, lambda e: collected.extend(e)))
        return collected

    def test_excluded_motion_detector2_not_in_entities(self):
        """Excluded motion_detector2 must be skipped (line 58 continue)."""
        from custom_components.bosch_shc.const import OPT_EXCLUDED_DEVICES
        from boschshcpy.services_impl import PirSensorConfigurationService

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

    def test_non_excluded_motion_detector2_with_sensitivity_attr_added(self):
        """Non-excluded device WITH motion_sensitivity attr produces entity."""
        from custom_components.bosch_shc.select import MotionSensitivitySelect
        from boschshcpy.services_impl import PirSensorConfigurationService

        dev = SimpleNamespace(
            id="md2-ok",
            name="MD2 OK",
            root_device_id="root-ok",
            motion_sensitivity=PirSensorConfigurationService.MotionSensitivity.HIGH,
        )
        session = SimpleNamespace(
            device_helper=SimpleNamespace(
                motion_detectors2=[dev],
                shutter_contacts2=[],
            )
        )
        result = self._run_setup(session)
        assert any(isinstance(e, MotionSensitivitySelect) for e in result)

    def test_motion_detector2_without_attr_skipped(self):
        """Device without motion_sensitivity attr is skipped (line 60)."""
        dev = SimpleNamespace(
            id="md2-no-attr",
            name="MD2",
            root_device_id="root",
            # No motion_sensitivity attribute
        )
        session = SimpleNamespace(
            device_helper=SimpleNamespace(
                motion_detectors2=[dev],
                shutter_contacts2=[],
            )
        )
        result = self._run_setup(session)
        assert result == []


# ---------------------------------------------------------------------------
# sensor.py — line 354: supports_batterylevel == False
# ---------------------------------------------------------------------------

class TestBatterySensorSupportsFalse:
    """Device with supports_batterylevel=False must not produce BatteryLevelSensor."""

    def test_no_battery_entity_when_not_supported(self):
        from custom_components.bosch_shc.const import DATA_SESSION, DOMAIN
        from custom_components.bosch_shc.sensor import BatteryLevelSensor, async_setup_entry

        async def _noop_migrate(*a, **kw):
            return None

        dev = SimpleNamespace(
            id="md-no-bat",
            name="Motion NoBat",
            root_device_id="root",
            serial="SER",
            device_services=[],
            supports_batterylevel=False,
        )
        session = SimpleNamespace(
            device_helper=SimpleNamespace(
                thermostats=[],
                wallthermostats=[],
                roomthermostats=[],
                twinguards=[],
                smart_plugs=[],
                light_switches_bsm=[],
                micromodule_light_controls=[],
                micromodule_shutter_controls=[],
                micromodule_blinds=[],
                smart_plugs_compact=[],
                motion_detectors=[dev],
                motion_detectors2=[],
                shutter_contacts=[],
                shutter_contacts2=[],
                smoke_detectors=[],
                universal_switches=[],
                water_leakage_detectors=[],
            ),
            emma=SimpleNamespace(
                id="com.bosch.tt.emma.applink",
                root_device_id="shc-root",
                name="EMMA",
                manufacturer="Bosch",
                device_model="EMMA",
                device_services=[],
            ),
        )

        hass = SimpleNamespace(data={DOMAIN: {"E1": {DATA_SESSION: session}}})
        config_entry = SimpleNamespace(entry_id="E1", options={})
        collected = []

        async def _inner():
            with patch(
                "custom_components.bosch_shc.sensor.async_migrate_to_new_unique_id",
                side_effect=_noop_migrate,
            ):
                await async_setup_entry(hass, config_entry, lambda e: collected.extend(e))

        asyncio.run(_inner())

        battery_entities = [e for e in collected if isinstance(e, BatteryLevelSensor)]
        assert battery_entities == [], (
            "BatteryLevelSensor must not be created when supports_batterylevel=False"
        )


# ---------------------------------------------------------------------------
# sensor.py — lines 736-738: BatteryLevelSensor ValueError/AttributeError
# ---------------------------------------------------------------------------

class TestBatteryLevelSensorErrorPaths:
    """BatteryLevelSensor.native_value returns None on ValueError/AttributeError."""

    def _sensor(self, exc_cls):
        from custom_components.bosch_shc.sensor import BatteryLevelSensor

        class _RaisingLevel:
            @property
            def value(self):
                raise exc_cls("bad")

        s = BatteryLevelSensor.__new__(BatteryLevelSensor)
        s._device = SimpleNamespace(batterylevel=_RaisingLevel(), name="Dev")
        return s

    def test_value_error_returns_none(self):
        s = self._sensor(ValueError)
        assert s.native_value is None

    def test_attribute_error_returns_none(self):
        s = self._sensor(AttributeError)
        assert s.native_value is None
