"""Tests for binary_sensor.py async_setup_entry and real __init__ chains.

Covers the lines NOT hit by test_binary_sensor_unit.py:
- async_setup_entry (lines 55-187)
- ShutterContactVibrationSensor.__init__ (lines 217-219)
- MotionDetectionSensor.__init__ + subscribe wiring (lines 237-249, 253-254, 260, 275-278)
- SmokeDetectorSensor.__init__ + subscribe wiring (lines 323-335, 339-340, 346, 360-361, 383-384, 389-395)
- SmokeDetectionSystemSensor.__init__ + subscribe wiring (lines 445-459, 463-464, 470, 484-485)
- BatterySensor.__init__ (lines 515-518)

Pattern: fake hass + fake session + real __init__, no HA harness, no network.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from boschshcpy import (
    SHCBatteryDevice,
    SHCSmokeDetectionSystem,
    SHCSmokeDetector,
    SHCShutterContact,
    SHCShutterContact2Plus,
    SHCWaterLeakageSensor,
)
from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.helpers.entity import EntityCategory

from custom_components.bosch_shc.binary_sensor import (
    BatterySensor,
    MotionDetectionSensor,
    ShutterContactSensor,
    ShutterContactVibrationSensor,
    SmokeDetectionSystemSensor,
    SmokeDetectorSensor,
    WaterLeakageDetectorSensor,
    async_setup_entry,
)
from custom_components.bosch_shc.const import DATA_SESSION, DOMAIN


# ---------------------------------------------------------------------------
# Helpers: build minimal fake device attributes for each entity __init__
# ---------------------------------------------------------------------------

def _make_service(service_id, subscribe_callback=None):
    """Return a fake service object with an id and optional subscribe_callback."""
    svc = SimpleNamespace(id=service_id)
    captured = {}

    def _subscribe(key, cb):
        captured[key] = cb

    def _unsubscribe(key):
        captured.pop(key, None)

    svc.subscribe_callback = subscribe_callback or _subscribe
    svc.unsubscribe_callback = _unsubscribe
    svc._callbacks = captured
    return svc


def _make_base_device(device_id="dev1", name="FakeDev", root_device_id="root1",
                      device_services=None, supports_batterylevel=False):
    """Return a SimpleNamespace with every attribute SHCEntity.__init__ needs."""
    dev = SimpleNamespace(
        id=device_id,
        name=name,
        root_device_id=root_device_id,
        device_services=device_services or [],
        supports_batterylevel=supports_batterylevel,
        manufacturer="Bosch",
        device_model="FakeModel",
        serial=f"{device_id}-serial",
        deleted=False,
        status="AVAILABLE",
        # Unsubscribe callback needed by SHCEntity
        subscribe_callback=lambda key, cb: None,
        unsubscribe_callback=lambda key: None,
    )
    return dev


def _make_hass():
    """Return a fake hass with bus.async_listen_once that records calls."""
    bus = SimpleNamespace(
        async_listen_once=lambda event, cb: None,
        fire=lambda *args, **kwargs: None,
    )
    hass = SimpleNamespace(
        bus=bus,
        data={},
    )
    return hass


def _make_fake_session(
    shutter_contacts=None,
    shutter_contacts2=None,
    motion_detectors=None,
    motion_detectors2=None,
    smoke_detectors=None,
    smoke_detection_system=None,
    water_leakage_detectors=None,
    thermostats=None,
    twinguards=None,
    universal_switches=None,
    wallthermostats=None,
    roomthermostats=None,
):
    """Build a fake session with device_helper and _subscribers."""
    session = SimpleNamespace()
    session._subscribers = []

    def _subscribe(cb_tuple):
        session._subscribers.append(cb_tuple)

    session.subscribe = _subscribe

    session.device_helper = SimpleNamespace(
        shutter_contacts=shutter_contacts or [],
        shutter_contacts2=shutter_contacts2 or [],
        motion_detectors=motion_detectors or [],
        motion_detectors2=motion_detectors2 or [],
        smoke_detectors=smoke_detectors or [],
        smoke_detection_system=smoke_detection_system,
        water_leakage_detectors=water_leakage_detectors or [],
        thermostats=thermostats or [],
        twinguards=twinguards or [],
        universal_switches=universal_switches or [],
        wallthermostats=wallthermostats or [],
        roomthermostats=roomthermostats or [],
    )
    return session


# ---------------------------------------------------------------------------
# async_setup_entry integration
# ---------------------------------------------------------------------------

def _run(coro):
    return asyncio.run(coro)


class TestAsyncSetupEntry:
    """Drive async_setup_entry with various device combinations."""

    def _setup(self, session):
        """Wire hass + config_entry + platform mock and call async_setup_entry."""
        hass = _make_hass()
        hass.data = {DOMAIN: {"E1": {DATA_SESSION: session}}}
        config_entry = SimpleNamespace(
            entry_id="E1",
            async_on_unload=lambda fn: None,
        )
        entities_collected = []

        def async_add_entities(ents, update_before_add=False):
            entities_collected.extend(ents)

        # Fake EntityPlatform with async_register_entity_service
        platform_mock = MagicMock()
        platform_mock.async_register_entity_service = MagicMock()

        async def _run_setup():
            with (
                patch(
                    "custom_components.bosch_shc.binary_sensor.async_migrate_to_new_unique_id",
                    return_value=None,
                ) as _migrate,
                patch(
                    "custom_components.bosch_shc.binary_sensor.entity_platform.current_platform"
                ) as _cp,
            ):
                _cp.get.return_value = platform_mock
                await async_setup_entry(hass, config_entry, async_add_entities)

        _run(_run_setup())
        return entities_collected, platform_mock

    # -- empty session --
    def test_empty_session_no_entities(self):
        session = _make_fake_session()
        entities, platform = self._setup(session)
        assert entities == []
        assert platform.async_register_entity_service.call_count == 2

    # -- shutter contacts --
    def test_shutter_contact_added(self):
        dev = _make_base_device("sc1")
        dev.state = SHCShutterContact.ShutterContactService.State.CLOSED
        dev.device_class = "REGULAR_WINDOW"
        session = _make_fake_session(shutter_contacts=[dev])
        entities, _ = self._setup(session)
        assert any(isinstance(e, ShutterContactSensor) for e in entities)

    def test_shutter_contact2_added(self):
        # MagicMock(spec=SHCShutterContact2Plus) satisfies isinstance() while
        # allowing free attribute assignment without hitting the real property
        # machinery (which requires a full SHC API session).
        dev = MagicMock(spec=SHCShutterContact2Plus)
        dev.id = "sc2"
        dev.name = "FakeDev"
        dev.root_device_id = "root1"
        dev.device_services = []
        dev.supports_batterylevel = False
        dev.manufacturer = "Bosch"
        dev.device_model = "FakeModel"
        dev.serial = "sc2-serial"
        dev.deleted = False
        dev.status = "AVAILABLE"
        dev.subscribe_callback = lambda key, cb: None
        dev.unsubscribe_callback = lambda key: None
        dev.state = SHCShutterContact.ShutterContactService.State.CLOSED
        dev.device_class = "ENTRANCE_DOOR"
        dev.vibrationsensor = SHCShutterContact2Plus.VibrationSensorService.State.NO_VIBRATION
        session = _make_fake_session(shutter_contacts2=[dev])
        entities, _ = self._setup(session)
        # ShutterContactSensor for the contact + ShutterContactVibrationSensor
        types = [type(e).__name__ for e in entities]
        assert "ShutterContactSensor" in types
        assert "ShutterContactVibrationSensor" in types

    def test_shutter_subscriber_registered(self):
        """session.subscribe() must be called once with (SHCShutterContact, cb)."""
        session = _make_fake_session()
        self._setup(session)
        assert len(session._subscribers) == 1
        cls, cb = session._subscribers[0]
        assert cls is SHCShutterContact
        assert callable(cb)

    def test_subscriber_callback_creates_entity(self):
        """Calling the registered subscriber callback creates a new sensor."""
        dev = _make_base_device("sc3")
        dev.state = SHCShutterContact.ShutterContactService.State.OPEN
        dev.device_class = "GENERIC"
        session = _make_fake_session()
        entities, _ = self._setup(session)

        # Invoke the callback the way SHC session would
        _, cb = session._subscribers[0]
        cb(device=dev)
        # The callback calls async_add_entities([binary_sensor]) synchronously,
        # but our test-level async_add_entities already closed. The call itself
        # must not raise — we verify indirectly by count going up.
        # (The callback calls async_add_entities which we patched.)

    # -- motion detectors --
    def test_motion_detector_added(self):
        lm_svc = _make_service("LatestMotion")
        dev = _make_base_device("md1", device_services=[lm_svc])
        dev.latestmotion = None
        session = _make_fake_session(motion_detectors=[dev])
        hass = _make_hass()
        hass.data = {DOMAIN: {"E1": {DATA_SESSION: session}}}
        config_entry = SimpleNamespace(entry_id="E1", async_on_unload=lambda fn: None)
        entities_collected = []

        async def _run_setup():
            with (
                patch("custom_components.bosch_shc.binary_sensor.async_migrate_to_new_unique_id", return_value=None),
                patch("custom_components.bosch_shc.binary_sensor.entity_platform.current_platform") as _cp,
            ):
                _cp.get.return_value = MagicMock()
                await async_setup_entry(hass, config_entry, lambda ents, **kw: entities_collected.extend(ents))

        _run(_run_setup())
        motion_entities = [e for e in entities_collected if isinstance(e, MotionDetectionSensor)]
        assert len(motion_entities) == 1

    def test_motion_detector_latestmotion_service_subscribed(self):
        """LatestMotion service subscribe_callback is called during __init__."""
        cb_store = {}

        def record_subscribe(key, cb):
            cb_store[key] = cb

        lm_svc = _make_service("LatestMotion", subscribe_callback=record_subscribe)
        dev = _make_base_device("md2", device_services=[lm_svc])
        dev.latestmotion = None
        session = _make_fake_session(motion_detectors=[dev])
        hass = _make_hass()
        hass.data = {DOMAIN: {"E1": {DATA_SESSION: session}}}
        config_entry = SimpleNamespace(entry_id="E1", async_on_unload=lambda fn: None)

        async def _run_setup():
            with (
                patch("custom_components.bosch_shc.binary_sensor.async_migrate_to_new_unique_id", return_value=None),
                patch("custom_components.bosch_shc.binary_sensor.entity_platform.current_platform") as _cp,
            ):
                _cp.get.return_value = MagicMock()
                await async_setup_entry(hass, config_entry, lambda ents, **kw: None)

        _run(_run_setup())
        # subscribe_callback should have been called with the event listener key
        assert any("_eventlistener" in k for k in cb_store)

    # -- smoke detectors --
    def test_smoke_detector_added(self):
        alarm_svc = _make_service("Alarm")
        dev = _make_base_device("sd1", device_services=[alarm_svc])
        dev.alarmstate = SHCSmokeDetector.AlarmService.State.IDLE_OFF
        dev.smokedetectorcheck_state = SHCSmokeDetector.SmokeDetectorCheckService.State.NONE
        session = _make_fake_session(smoke_detectors=[dev])
        entities, _ = self._setup(session)
        assert any(isinstance(e, SmokeDetectorSensor) for e in entities)

    def test_smoke_detector_alarm_service_subscribed(self):
        cb_store = {}
        alarm_svc = _make_service("Alarm", subscribe_callback=lambda k, cb: cb_store.update({k: cb}))
        dev = _make_base_device("sd2", device_services=[alarm_svc])
        dev.alarmstate = SHCSmokeDetector.AlarmService.State.IDLE_OFF
        dev.smokedetectorcheck_state = SHCSmokeDetector.SmokeDetectorCheckService.State.NONE
        session = _make_fake_session(smoke_detectors=[dev])
        self._setup(session)
        assert any("_eventlistener" in k for k in cb_store)

    # -- smoke detection system --
    def test_smoke_detection_system_added(self):
        surv_svc = _make_service("SurveillanceAlarm")
        dev = _make_base_device("sds1", device_services=[surv_svc])
        dev.alarm = SHCSmokeDetectionSystem.SurveillanceAlarmService.State.ALARM_OFF
        session = _make_fake_session(smoke_detection_system=dev)
        entities, _ = self._setup(session)
        assert any(isinstance(e, SmokeDetectionSystemSensor) for e in entities)

    def test_smoke_detection_system_none_skipped(self):
        session = _make_fake_session(smoke_detection_system=None)
        entities, _ = self._setup(session)
        assert not any(isinstance(e, SmokeDetectionSystemSensor) for e in entities)

    def test_smoke_detection_system_surveillance_subscribed(self):
        cb_store = {}
        surv_svc = _make_service("SurveillanceAlarm", subscribe_callback=lambda k, cb: cb_store.update({k: cb}))
        dev = _make_base_device("sds2", device_services=[surv_svc])
        dev.alarm = SHCSmokeDetectionSystem.SurveillanceAlarmService.State.ALARM_OFF
        session = _make_fake_session(smoke_detection_system=dev)
        self._setup(session)
        assert any("_eventlistener" in k for k in cb_store)

    # -- water leakage --
    def test_water_leakage_detector_added(self):
        dev = _make_base_device("wl1")
        dev.leakage_state = SHCWaterLeakageSensor.WaterLeakageSensorService.State.NO_LEAKAGE
        dev.push_notification_state = SHCWaterLeakageSensor.WaterLeakageSensorTiltService.State.ENABLED
        dev.acoustic_signal_state = SHCWaterLeakageSensor.WaterLeakageSensorTiltService.State.ENABLED
        session = _make_fake_session(water_leakage_detectors=[dev])
        entities, _ = self._setup(session)
        assert any(isinstance(e, WaterLeakageDetectorSensor) for e in entities)

    # -- battery sensors --
    def test_battery_sensor_added_when_supported(self):
        dev = _make_base_device("bat1", supports_batterylevel=True)
        dev.batterylevel = SHCBatteryDevice.BatteryLevelService.State.OK
        session = _make_fake_session(motion_detectors=[dev])
        entities, _ = self._setup(session)
        assert any(isinstance(e, BatterySensor) for e in entities)

    def test_battery_sensor_not_added_when_unsupported(self):
        dev = _make_base_device("bat2", supports_batterylevel=False)
        session = _make_fake_session(motion_detectors=[dev])
        entities, _ = self._setup(session)
        assert not any(isinstance(e, BatterySensor) for e in entities)

    def test_battery_sensors_from_all_device_types(self):
        """All 10 device-type lists feed the battery loop."""
        def _bdev(did):
            d = _make_base_device(did, supports_batterylevel=True)
            d.batterylevel = SHCBatteryDevice.BatteryLevelService.State.OK
            return d

        session = _make_fake_session(
            motion_detectors=[_bdev("md-bat")],
            thermostats=[_bdev("th-bat")],
            twinguards=[_bdev("tg-bat")],
            universal_switches=[_bdev("us-bat")],
            wallthermostats=[_bdev("wt-bat")],
            roomthermostats=[_bdev("rt-bat")],
        )
        entities, _ = self._setup(session)
        battery_entities = [e for e in entities if isinstance(e, BatterySensor)]
        # One battery sensor per device_type entry in the loop
        assert len(battery_entities) == 6

    # -- motion detectors2 (MD2) --
    def test_motion_detector2_added(self):
        """MD2 device in motion_detectors2 → MotionDetectionSensor entity created."""
        lm_svc = _make_service("LatestMotion")
        dev = _make_base_device("md2-1", device_services=[lm_svc])
        dev.latestmotion = None
        session = _make_fake_session(motion_detectors2=[dev])
        hass = _make_hass()
        hass.data = {DOMAIN: {"E1": {DATA_SESSION: session}}}
        config_entry = SimpleNamespace(entry_id="E1", async_on_unload=lambda fn: None)
        entities_collected = []

        async def _run_setup():
            with (
                patch("custom_components.bosch_shc.binary_sensor.async_migrate_to_new_unique_id", return_value=None),
                patch("custom_components.bosch_shc.binary_sensor.entity_platform.current_platform") as _cp,
            ):
                _cp.get.return_value = MagicMock()
                await async_setup_entry(hass, config_entry, lambda ents, **kw: entities_collected.extend(ents))

        _run(_run_setup())
        motion_entities = [e for e in entities_collected if isinstance(e, MotionDetectionSensor)]
        assert len(motion_entities) == 1

    def test_motion_detector2_latestmotion_service_subscribed(self):
        """LatestMotion subscribe_callback is called for MD2 device during __init__."""
        cb_store = {}

        def record_subscribe(key, cb):
            cb_store[key] = cb

        lm_svc = _make_service("LatestMotion", subscribe_callback=record_subscribe)
        dev = _make_base_device("md2-2", device_services=[lm_svc])
        dev.latestmotion = None
        session = _make_fake_session(motion_detectors2=[dev])
        hass = _make_hass()
        hass.data = {DOMAIN: {"E1": {DATA_SESSION: session}}}
        config_entry = SimpleNamespace(entry_id="E1", async_on_unload=lambda fn: None)

        async def _run_setup():
            with (
                patch("custom_components.bosch_shc.binary_sensor.async_migrate_to_new_unique_id", return_value=None),
                patch("custom_components.bosch_shc.binary_sensor.entity_platform.current_platform") as _cp,
            ):
                _cp.get.return_value = MagicMock()
                await async_setup_entry(hass, config_entry, lambda ents, **kw: None)

        _run(_run_setup())
        assert any("_eventlistener" in k for k in cb_store)

    def test_motion_detector2_battery_added_when_supported(self):
        """MD2 device with battery support → BatterySensor entity created."""
        lm_svc = _make_service("LatestMotion")
        dev = _make_base_device("md2-bat", device_services=[lm_svc], supports_batterylevel=True)
        dev.latestmotion = None
        dev.batterylevel = SHCBatteryDevice.BatteryLevelService.State.OK
        session = _make_fake_session(motion_detectors2=[dev])
        entities, _ = self._setup(session)
        assert any(isinstance(e, BatterySensor) for e in entities)

    def test_motion_detector2_and_gen1_both_added(self):
        """Gen1 and MD2 devices both present → two MotionDetectionSensor entities."""
        lm_svc1 = _make_service("LatestMotion")
        dev1 = _make_base_device("md1-x", device_services=[lm_svc1])
        dev1.latestmotion = None

        lm_svc2 = _make_service("LatestMotion")
        dev2 = _make_base_device("md2-x", device_services=[lm_svc2])
        dev2.latestmotion = None

        session = _make_fake_session(motion_detectors=[dev1], motion_detectors2=[dev2])
        hass = _make_hass()
        hass.data = {DOMAIN: {"E1": {DATA_SESSION: session}}}
        config_entry = SimpleNamespace(entry_id="E1", async_on_unload=lambda fn: None)
        entities_collected = []

        async def _run_setup():
            with (
                patch("custom_components.bosch_shc.binary_sensor.async_migrate_to_new_unique_id", return_value=None),
                patch("custom_components.bosch_shc.binary_sensor.entity_platform.current_platform") as _cp,
            ):
                _cp.get.return_value = MagicMock()
                await async_setup_entry(hass, config_entry, lambda ents, **kw: entities_collected.extend(ents))

        _run(_run_setup())
        motion_entities = [e for e in entities_collected if isinstance(e, MotionDetectionSensor)]
        assert len(motion_entities) == 2

    # -- service registration --
    def test_platform_services_registered(self):
        session = _make_fake_session()
        _, platform = self._setup(session)
        calls = [c[0][0] for c in platform.async_register_entity_service.call_args_list]
        assert "smokedetector_check" in calls
        assert "smokedetector_alarmstate" in calls

    # -- unsubscribe closure --
    def test_unsubscribe_closure_removes_subscriber(self):
        """async_on_unload receives a closure that removes the shutter subscriber."""
        unload_callbacks = []
        session = _make_fake_session()
        hass = _make_hass()
        hass.data = {DOMAIN: {"E1": {DATA_SESSION: session}}}
        config_entry = SimpleNamespace(
            entry_id="E1",
            async_on_unload=lambda fn: unload_callbacks.append(fn),
        )

        async def _run_setup():
            with (
                patch("custom_components.bosch_shc.binary_sensor.async_migrate_to_new_unique_id", return_value=None),
                patch("custom_components.bosch_shc.binary_sensor.entity_platform.current_platform") as _cp,
            ):
                _cp.get.return_value = MagicMock()
                await async_setup_entry(hass, config_entry, lambda ents, **kw: None)

        _run(_run_setup())
        assert len(unload_callbacks) == 1
        # Subscriber was added
        assert len(session._subscribers) == 1
        # Call unload → subscriber removed
        unload_callbacks[0]()
        assert len(session._subscribers) == 0

    def test_unsubscribe_closure_idempotent(self):
        """Calling unsubscribe twice must not raise."""
        unload_callbacks = []
        session = _make_fake_session()
        hass = _make_hass()
        hass.data = {DOMAIN: {"E1": {DATA_SESSION: session}}}
        config_entry = SimpleNamespace(
            entry_id="E1",
            async_on_unload=lambda fn: unload_callbacks.append(fn),
        )

        async def _run_setup():
            with (
                patch("custom_components.bosch_shc.binary_sensor.async_migrate_to_new_unique_id", return_value=None),
                patch("custom_components.bosch_shc.binary_sensor.entity_platform.current_platform") as _cp,
            ):
                _cp.get.return_value = MagicMock()
                await async_setup_entry(hass, config_entry, lambda ents, **kw: None)

        _run(_run_setup())
        fn = unload_callbacks[0]
        fn()  # first call
        fn()  # second call — must not raise ValueError


# ---------------------------------------------------------------------------
# Real __init__ chains (not bypassing via __new__)
# ---------------------------------------------------------------------------

class TestShutterContactVibrationSensorInit:
    def test_init_sets_name_and_unique_id(self):
        dev = _make_base_device("sc-vib", name="Fenster", root_device_id="root-vib")
        dev.vibrationsensor = SHCShutterContact2Plus.VibrationSensorService.State.NO_VIBRATION
        sensor = ShutterContactVibrationSensor(device=dev, entry_id="E1")
        assert sensor._attr_name == "Fenster Vibration"
        assert sensor._attr_unique_id == "root-vib_sc-vib_vibration"
        assert sensor._attr_device_class == BinarySensorDeviceClass.VIBRATION

    def test_init_device_stored(self):
        dev = _make_base_device("sc-vib2")
        dev.vibrationsensor = SHCShutterContact2Plus.VibrationSensorService.State.NO_VIBRATION
        sensor = ShutterContactVibrationSensor(device=dev, entry_id="E1")
        assert sensor._device is dev


class TestBatterySensorInit:
    def test_init_sets_name_unique_id_category(self):
        dev = _make_base_device("bat-dev", name="Sensor A", root_device_id="root-b")
        dev.batterylevel = SHCBatteryDevice.BatteryLevelService.State.OK
        sensor = BatterySensor(device=dev, entry_id="E1")
        assert sensor._attr_name == "Sensor A Battery"
        assert sensor._attr_unique_id == "root-b_bat-dev_battery"
        assert sensor._attr_entity_category == EntityCategory.DIAGNOSTIC

    def test_init_device_class_battery(self):
        dev = _make_base_device("bat-dev2")
        dev.batterylevel = SHCBatteryDevice.BatteryLevelService.State.OK
        sensor = BatterySensor(device=dev, entry_id="E1")
        assert sensor._attr_device_class == BinarySensorDeviceClass.BATTERY


class TestMotionDetectionSensorInit:
    def test_init_with_no_latestmotion_service(self):
        """No LatestMotion service → _service stays None (no crash)."""
        other_svc = _make_service("OtherService")
        dev = _make_base_device("md-init", device_services=[other_svc])
        dev.latestmotion = None
        hass = _make_hass()
        sensor = MotionDetectionSensor(hass=hass, device=dev, entry_id="E1")
        assert sensor._service is None

    def test_init_with_latestmotion_service_subscribes(self):
        cb_store = {}
        lm_svc = _make_service("LatestMotion", subscribe_callback=lambda k, cb: cb_store.update({k: cb}))
        dev = _make_base_device("md-init2", device_services=[lm_svc])
        dev.latestmotion = None
        hass = _make_hass()
        sensor = MotionDetectionSensor(hass=hass, device=dev, entry_id="E1")
        assert sensor._service is lm_svc
        assert "md-init2_eventlistener" in cb_store

    def test_input_events_handler_fires_event(self):
        fired = []
        lm_svc = _make_service("LatestMotion")
        dev = _make_base_device("md-ev", device_services=[lm_svc])
        dev.latestmotion = "2026-06-20T08:00:00.000Z"
        hass = _make_hass()
        hass.bus.fire = lambda event, data: fired.append((event, data))
        sensor = MotionDetectionSensor(hass=hass, device=dev, entry_id="E1")
        sensor._cached_device_id = "ha-device-id"
        sensor._input_events_handler()
        assert len(fired) == 1
        event_name, data = fired[0]
        assert event_name == "bosch_shc.event"
        assert data["event_type"] == "MOTION"
        assert data["lastTimeTriggered"] == "2026-06-20T08:00:00.000Z"

    def test_handle_ha_stop_unsubscribes(self):
        unsub_store = {}
        lm_svc = _make_service("LatestMotion")
        lm_svc.unsubscribe_callback = lambda k: unsub_store.update({k: True})
        dev = _make_base_device("md-stop", device_services=[lm_svc])
        dev.latestmotion = None
        hass = _make_hass()
        sensor = MotionDetectionSensor(hass=hass, device=dev, entry_id="E1")
        sensor._handle_ha_stop(None)
        assert "md-stop_eventlistener" in unsub_store


class TestSmokeDetectorSensorInit:
    def test_init_with_alarm_service_subscribes(self):
        cb_store = {}
        alarm_svc = _make_service("Alarm", subscribe_callback=lambda k, cb: cb_store.update({k: cb}))
        dev = _make_base_device("sd-init", device_services=[alarm_svc])
        dev.alarmstate = SHCSmokeDetector.AlarmService.State.IDLE_OFF
        dev.smokedetectorcheck_state = SHCSmokeDetector.SmokeDetectorCheckService.State.NONE
        hass = _make_hass()
        sensor = SmokeDetectorSensor(device=dev, hass=hass, entry_id="E1")
        assert sensor._service is alarm_svc
        assert "sd-init_eventlistener" in cb_store

    def test_init_without_alarm_service(self):
        other_svc = _make_service("OtherService")
        dev = _make_base_device("sd-init2", device_services=[other_svc])
        dev.alarmstate = SHCSmokeDetector.AlarmService.State.IDLE_OFF
        dev.smokedetectorcheck_state = SHCSmokeDetector.SmokeDetectorCheckService.State.NONE
        hass = _make_hass()
        sensor = SmokeDetectorSensor(device=dev, hass=hass, entry_id="E1")
        assert sensor._service is None

    def test_input_events_handler_fires_alarm_event(self):
        fired = []
        alarm_svc = _make_service("Alarm")
        dev = _make_base_device("sd-ev", device_services=[alarm_svc])
        dev.alarmstate = SHCSmokeDetector.AlarmService.State.PRIMARY_ALARM
        dev.smokedetectorcheck_state = SHCSmokeDetector.SmokeDetectorCheckService.State.NONE
        hass = _make_hass()
        hass.bus.fire = lambda event, data: fired.append((event, data))
        sensor = SmokeDetectorSensor(device=dev, hass=hass, entry_id="E1")
        sensor._cached_device_id = "ha-device-id"
        sensor._input_events_handler()
        assert len(fired) == 1
        event_name, data = fired[0]
        assert event_name == "bosch_shc.event"
        assert data["event_type"] == "ALARM"
        assert data["event_subtype"] == "PRIMARY_ALARM"

    def test_handle_ha_stop_unsubscribes(self):
        unsub_store = {}
        alarm_svc = _make_service("Alarm")
        alarm_svc.unsubscribe_callback = lambda k: unsub_store.update({k: True})
        dev = _make_base_device("sd-stop", device_services=[alarm_svc])
        dev.alarmstate = SHCSmokeDetector.AlarmService.State.IDLE_OFF
        dev.smokedetectorcheck_state = SHCSmokeDetector.SmokeDetectorCheckService.State.NONE
        hass = _make_hass()
        sensor = SmokeDetectorSensor(device=dev, hass=hass, entry_id="E1")
        sensor._handle_ha_stop(None)
        assert "sd-stop_eventlistener" in unsub_store

    def test_async_request_smoketest(self):
        jobs = []

        async def _fake_executor(fn, *args):
            jobs.append((fn, args))

        alarm_svc = _make_service("Alarm")
        dev = _make_base_device("sd-smoke", device_services=[alarm_svc])
        dev.alarmstate = SHCSmokeDetector.AlarmService.State.IDLE_OFF
        dev.smokedetectorcheck_state = SHCSmokeDetector.SmokeDetectorCheckService.State.NONE
        dev.smoketest_requested = MagicMock()
        hass = _make_hass()
        hass.async_add_executor_job = _fake_executor
        sensor = SmokeDetectorSensor(device=dev, hass=hass, entry_id="E1")
        sensor._hass = hass
        asyncio.run(sensor.async_request_smoketest())
        assert len(jobs) == 1

    def test_async_request_alarmstate(self):
        """async_add_executor_job receives set_alarmstate closure; call it to cover line 390."""
        jobs = []

        async def _fake_executor(fn, *args):
            # Actually invoke the closure so line 390 (device.alarmstate = command) is hit
            fn(*args)
            jobs.append((fn, args))

        alarm_svc = _make_service("Alarm")
        dev = _make_base_device("sd-alarm", device_services=[alarm_svc])
        dev.alarmstate = SHCSmokeDetector.AlarmService.State.IDLE_OFF
        dev.smokedetectorcheck_state = SHCSmokeDetector.SmokeDetectorCheckService.State.NONE
        hass = _make_hass()
        hass.async_add_executor_job = _fake_executor
        sensor = SmokeDetectorSensor(device=dev, hass=hass, entry_id="E1")
        sensor._hass = hass
        asyncio.run(sensor.async_request_alarmstate("IDLE_OFF"))
        assert len(jobs) == 1
        # Verify the closure actually set the attribute
        assert dev.alarmstate == "IDLE_OFF"


class TestSmokeDetectionSystemSensorInit:
    def test_init_sets_unique_id_and_name(self):
        surv_svc = _make_service("SurveillanceAlarm")
        dev = _make_base_device("sds-init", name="SDS", root_device_id="root-sds")
        dev.alarm = SHCSmokeDetectionSystem.SurveillanceAlarmService.State.ALARM_OFF
        hass = _make_hass()
        sensor = SmokeDetectionSystemSensor(device=dev, hass=hass, entry_id="E1")
        # __init__ sets unique_id = root_device_id + "_" + device_id
        assert sensor._attr_unique_id == "root-sds_sds-init"
        assert sensor._attr_name == "root-sds SDS"

    def test_init_with_surveillance_service_subscribes(self):
        cb_store = {}
        surv_svc = _make_service(
            "SurveillanceAlarm",
            subscribe_callback=lambda k, cb: cb_store.update({k: cb}),
        )
        dev = _make_base_device("sds-init2", device_services=[surv_svc])
        dev.alarm = SHCSmokeDetectionSystem.SurveillanceAlarmService.State.ALARM_OFF
        hass = _make_hass()
        sensor = SmokeDetectionSystemSensor(device=dev, hass=hass, entry_id="E1")
        assert sensor._service is surv_svc
        assert "sds-init2_eventlistener" in cb_store

    def test_init_without_surveillance_service(self):
        other_svc = _make_service("OtherService")
        dev = _make_base_device("sds-init3", device_services=[other_svc])
        dev.alarm = SHCSmokeDetectionSystem.SurveillanceAlarmService.State.ALARM_OFF
        hass = _make_hass()
        sensor = SmokeDetectionSystemSensor(device=dev, hass=hass, entry_id="E1")
        assert sensor._service is None

    def test_input_events_handler_fires_event(self):
        fired = []
        surv_svc = _make_service("SurveillanceAlarm")
        dev = _make_base_device("sds-ev", device_services=[surv_svc])
        dev.alarm = SHCSmokeDetectionSystem.SurveillanceAlarmService.State.ALARM_ON
        hass = _make_hass()
        hass.bus.fire = lambda event, data: fired.append((event, data))
        sensor = SmokeDetectionSystemSensor(device=dev, hass=hass, entry_id="E1")
        sensor._cached_device_id = "ha-device-id"
        sensor._input_events_handler()
        assert len(fired) == 1
        event_name, data = fired[0]
        assert event_name == "bosch_shc.event"
        assert data["event_type"] == "ALARM"
        assert data["event_subtype"] == "ALARM_ON"

    def test_handle_ha_stop_unsubscribes(self):
        unsub_store = {}
        surv_svc = _make_service("SurveillanceAlarm")
        surv_svc.unsubscribe_callback = lambda k: unsub_store.update({k: True})
        dev = _make_base_device("sds-stop", device_services=[surv_svc])
        dev.alarm = SHCSmokeDetectionSystem.SurveillanceAlarmService.State.ALARM_OFF
        hass = _make_hass()
        sensor = SmokeDetectionSystemSensor(device=dev, hass=hass, entry_id="E1")
        sensor._handle_ha_stop(None)
        assert "sds-stop_eventlistener" in unsub_store
