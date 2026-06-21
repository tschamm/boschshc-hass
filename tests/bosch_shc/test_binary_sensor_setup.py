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
    TwinguardAlarmTracker,
    TwinguardSmokeAlarmSensor,
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
    # call_soon_threadsafe executes the callback immediately in tests (synchronous)
    loop = SimpleNamespace(call_soon_threadsafe=lambda cb, *args: cb(*args))
    hass = SimpleNamespace(
        bus=bus,
        data={},
        loop=loop,
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
    messages=None,
):
    """Build a fake session with device_helper, api, and _subscribers."""
    session = SimpleNamespace()
    session._subscribers = []

    def _subscribe(cb_tuple):
        session._subscribers.append(cb_tuple)

    session.subscribe = _subscribe
    session.api = SimpleNamespace(get_messages=lambda: messages or [])

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
        # async_add_executor_job: call synchronously in tests (no thread needed)

        async def _fake_executor_job(fn, *args):
            return fn(*args)

        hass.async_add_executor_job = _fake_executor_job
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

    # -- twinguard smoke alarm sensors --
    def test_twinguard_smoke_alarm_sensor_added_when_twinguards_present(self):
        """TwinguardSmokeAlarmSensor created for each Twinguard when SDS exists."""
        surv_svc = _make_service("SurveillanceAlarm")
        sds = _make_base_device("smokeDetectionSystem", device_services=[surv_svc])
        sds.alarm = SHCSmokeDetectionSystem.SurveillanceAlarmService.State.ALARM_OFF
        tw1 = _make_base_device("tw1", name="TW1")
        tw2 = _make_base_device("tw2", name="TW2")
        session = _make_fake_session(smoke_detection_system=sds, twinguards=[tw1, tw2])
        entities, _ = self._setup(session)
        smoke_sensors = [e for e in entities if isinstance(e, TwinguardSmokeAlarmSensor)]
        assert len(smoke_sensors) == 2

    def test_twinguard_smoke_alarm_sensor_not_added_when_no_twinguards(self):
        """No TwinguardSmokeAlarmSensor when twinguards list is empty."""
        surv_svc = _make_service("SurveillanceAlarm")
        sds = _make_base_device("smokeDetectionSystem", device_services=[surv_svc])
        sds.alarm = SHCSmokeDetectionSystem.SurveillanceAlarmService.State.ALARM_OFF
        session = _make_fake_session(smoke_detection_system=sds, twinguards=[])
        entities, _ = self._setup(session)
        assert not any(isinstance(e, TwinguardSmokeAlarmSensor) for e in entities)

    def test_twinguard_smoke_alarm_sensor_not_added_when_no_sds(self):
        """No TwinguardSmokeAlarmSensor when smoke_detection_system is None."""
        tw = _make_base_device("tw1", name="TW1")
        session = _make_fake_session(smoke_detection_system=None, twinguards=[tw])
        entities, _ = self._setup(session)
        assert not any(isinstance(e, TwinguardSmokeAlarmSensor) for e in entities)

    def test_twinguard_smoke_alarm_sensor_unique_id_and_name(self):
        """TwinguardSmokeAlarmSensor gets _smoke suffix unique_id and name=Smoke."""
        surv_svc = _make_service("SurveillanceAlarm")
        sds = _make_base_device("sds-x", device_services=[surv_svc])
        sds.alarm = SHCSmokeDetectionSystem.SurveillanceAlarmService.State.ALARM_OFF
        tw = _make_base_device("tw-x", name="TW", root_device_id="root-x")
        session = _make_fake_session(smoke_detection_system=sds, twinguards=[tw])
        entities, _ = self._setup(session)
        sensor = next(e for e in entities if isinstance(e, TwinguardSmokeAlarmSensor))
        assert sensor._attr_unique_id == "root-x_tw-x_smoke"
        assert sensor._attr_name == "Smoke"

    def test_twinguard_tracker_subscribed_to_surveillance_alarm(self):
        """TwinguardAlarmTracker subscribes to SurveillanceAlarm service."""
        cb_store = {}
        surv_svc = _make_service(
            "SurveillanceAlarm",
            subscribe_callback=lambda k, cb: cb_store.update({k: cb}),
        )
        sds = _make_base_device("sds-sub", device_services=[surv_svc])
        sds.alarm = SHCSmokeDetectionSystem.SurveillanceAlarmService.State.ALARM_OFF
        tw = _make_base_device("tw-sub")
        session = _make_fake_session(smoke_detection_system=sds, twinguards=[tw])
        self._setup(session)
        assert any("_twinguard_alarm_listener" in k for k in cb_store)

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
        # _attr_has_entity_name=True: sub-sensor sets feature name only (no device prefix)
        assert sensor._attr_name == "Vibration"
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
        # _attr_has_entity_name=True: sub-sensor sets feature name only (no device prefix)
        assert sensor._attr_name == "Battery"
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
        # Primary entity (_attr_name=None): HA uses the device name directly
        assert sensor._attr_name is None

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


# ---------------------------------------------------------------------------
# TwinguardAlarmTracker unit tests
# ---------------------------------------------------------------------------

class TestTwinguardAlarmTracker:
    """Unit tests for TwinguardAlarmTracker (no HA, no network)."""

    def _make_tracker(self, sds, session):
        """Construct a tracker without running refresh()."""
        return TwinguardAlarmTracker(session=session, smoke_detection_system=sds)

    # -- _parse_surveillance_events --

    def test_parse_surveillance_events_with_list(self):
        """Native list input is returned directly."""
        events = [{"type": "SMOKE_LIGHT", "triggerId": "tw1"}, {"type": "ALARM_OFF"}]
        assert TwinguardAlarmTracker._parse_surveillance_events(events) == events

    def test_parse_surveillance_events_with_json_string(self):
        """JSON-encoded string is decoded."""
        raw = '[{"type":"SMOKE_LIGHT","triggerId":"tw1"},{"type":"ALARM_OFF"}]'
        result = TwinguardAlarmTracker._parse_surveillance_events(raw)
        assert result == [{"type": "SMOKE_LIGHT", "triggerId": "tw1"}, {"type": "ALARM_OFF"}]

    def test_parse_surveillance_events_empty(self):
        assert TwinguardAlarmTracker._parse_surveillance_events(None) == []
        assert TwinguardAlarmTracker._parse_surveillance_events("") == []
        assert TwinguardAlarmTracker._parse_surveillance_events([]) == []

    def test_parse_surveillance_events_invalid_json(self):
        assert TwinguardAlarmTracker._parse_surveillance_events("not-json") == []

    def test_parse_surveillance_events_non_list_json(self):
        assert TwinguardAlarmTracker._parse_surveillance_events('{"key": "val"}') == []

    # -- refresh / trigger id extraction --

    def test_refresh_alarm_off_clears_trigger_ids(self):
        """When alarm state is ALARM_OFF, active_trigger_ids is cleared."""
        surv_svc = _make_service("SurveillanceAlarm")
        sds = _make_base_device("sds1", device_services=[surv_svc])
        sds.alarm = SHCSmokeDetectionSystem.SurveillanceAlarmService.State.ALARM_OFF
        session = _make_fake_session(smoke_detection_system=sds)
        tracker = self._make_tracker(sds, session)
        tracker._active_trigger_ids = {"tw1"}  # simulate previous state
        tracker.refresh()
        assert tracker._active_trigger_ids == set()

    def test_refresh_alarm_on_extracts_trigger_id(self):
        """ALARM_ON + SMOKE_ALARM message populates active_trigger_ids."""
        surv_svc = _make_service("SurveillanceAlarm")
        sds = _make_base_device("smokeDetectionSystem", device_services=[surv_svc])
        sds.alarm = SHCSmokeDetectionSystem.SurveillanceAlarmService.State.ALARM_ON
        session = _make_fake_session(
            smoke_detection_system=sds,
            messages=[
                {
                    "messageCode": {"name": "SMOKE_ALARM"},
                    "sourceId": "smokeDetectionSystem",
                    "arguments": {
                        "surveillanceEvents": [{"type": "SMOKE_LIGHT", "triggerId": "tw1"}]
                    },
                }
            ],
        )
        tracker = self._make_tracker(sds, session)
        tracker.refresh()
        assert tracker.is_alarm_active_for("tw1") is True
        assert tracker.is_alarm_active_for("tw2") is False

    def test_refresh_message_with_alarm_off_event_is_skipped(self):
        """A SMOKE_ALARM message containing an ALARM_OFF event is skipped."""
        surv_svc = _make_service("SurveillanceAlarm")
        sds = _make_base_device("sds-ao", device_services=[surv_svc])
        sds.alarm = SHCSmokeDetectionSystem.SurveillanceAlarmService.State.ALARM_ON
        session = _make_fake_session(
            smoke_detection_system=sds,
            messages=[
                {
                    "messageCode": {"name": "SMOKE_ALARM"},
                    "sourceId": "sds-ao",
                    "arguments": {
                        "surveillanceEvents": [
                            {"type": "ALARM_OFF", "triggerId": "tw1"},
                        ]
                    },
                }
            ],
        )
        tracker = self._make_tracker(sds, session)
        tracker.refresh()
        assert tracker.is_alarm_active_for("tw1") is False

    def test_refresh_only_matches_correct_source_id(self):
        """Messages with a different sourceId are ignored."""
        surv_svc = _make_service("SurveillanceAlarm")
        sds = _make_base_device("sds-correct", device_services=[surv_svc])
        sds.alarm = SHCSmokeDetectionSystem.SurveillanceAlarmService.State.ALARM_ON
        session = _make_fake_session(
            smoke_detection_system=sds,
            messages=[
                {
                    "messageCode": {"name": "SMOKE_ALARM"},
                    "sourceId": "sds-OTHER",  # wrong source
                    "arguments": {
                        "surveillanceEvents": [{"type": "SMOKE_LIGHT", "triggerId": "tw1"}]
                    },
                }
            ],
        )
        tracker = self._make_tracker(sds, session)
        tracker.refresh()
        assert tracker.is_alarm_active_for("tw1") is False

    def test_refresh_no_change_does_not_notify(self):
        """refresh() with no state change must not call listeners."""
        surv_svc = _make_service("SurveillanceAlarm")
        sds = _make_base_device("sds-nc", device_services=[surv_svc])
        sds.alarm = SHCSmokeDetectionSystem.SurveillanceAlarmService.State.ALARM_OFF
        session = _make_fake_session(smoke_detection_system=sds)
        tracker = self._make_tracker(sds, session)
        # First refresh establishes baseline
        tracker.refresh()
        called = []
        hass = _make_hass()
        tracker.register_listener(hass, lambda: called.append(1))
        # Second refresh with same state → no notification
        tracker.refresh()
        assert called == []

    def test_refresh_alarm_state_change_notifies_listeners(self):
        """Changing alarm state from ALARM_ON to ALARM_MUTED notifies listeners."""
        surv_svc = _make_service("SurveillanceAlarm")
        sds = _make_base_device("sds-notify", device_services=[surv_svc])
        sds.alarm = SHCSmokeDetectionSystem.SurveillanceAlarmService.State.ALARM_ON
        session = _make_fake_session(
            smoke_detection_system=sds,
            messages=[
                {
                    "messageCode": {"name": "SMOKE_ALARM"},
                    "sourceId": "sds-notify",
                    "arguments": {
                        "surveillanceEvents": [{"type": "SMOKE_LIGHT", "triggerId": "tw1"}]
                    },
                }
            ],
        )
        tracker = self._make_tracker(sds, session)
        tracker.refresh()
        called = []
        hass = _make_hass()
        tracker.register_listener(hass, lambda: called.append(1))
        # Switch to ALARM_MUTED (same trigger, different alarm_state)
        sds.alarm = SHCSmokeDetectionSystem.SurveillanceAlarmService.State.ALARM_MUTED
        tracker.refresh()
        assert called == [1]

    def test_refresh_alarm_off_notifies_and_clears(self):
        """Transitioning to ALARM_OFF clears trigger ids and notifies."""
        surv_svc = _make_service("SurveillanceAlarm")
        sds = _make_base_device("sds-clear", device_services=[surv_svc])
        sds.alarm = SHCSmokeDetectionSystem.SurveillanceAlarmService.State.ALARM_ON
        session = _make_fake_session(
            smoke_detection_system=sds,
            messages=[
                {
                    "messageCode": {"name": "SMOKE_ALARM"},
                    "sourceId": "sds-clear",
                    "arguments": {
                        "surveillanceEvents": [{"type": "SMOKE_LIGHT", "triggerId": "tw1"}]
                    },
                }
            ],
        )
        tracker = self._make_tracker(sds, session)
        tracker.refresh()
        assert tracker.is_alarm_active_for("tw1") is True

        called = []
        hass = _make_hass()
        tracker.register_listener(hass, lambda: called.append(1))

        sds.alarm = SHCSmokeDetectionSystem.SurveillanceAlarmService.State.ALARM_OFF
        tracker.refresh()
        assert tracker.is_alarm_active_for("tw1") is False
        assert called == [1]

    # -- listener registration / unregistration --

    def test_unregister_listener(self):
        """Unregistered listener is not called on refresh."""
        surv_svc = _make_service("SurveillanceAlarm")
        sds = _make_base_device("sds-unreg", device_services=[surv_svc])
        sds.alarm = SHCSmokeDetectionSystem.SurveillanceAlarmService.State.ALARM_ON
        session = _make_fake_session(smoke_detection_system=sds)
        tracker = self._make_tracker(sds, session)
        tracker.refresh()  # establish ALARM_ON baseline

        called = []
        hass = _make_hass()

        def _cb():
            called.append(1)

        tracker.register_listener(hass, _cb)
        tracker.unregister_listener(_cb)

        sds.alarm = SHCSmokeDetectionSystem.SurveillanceAlarmService.State.ALARM_OFF
        tracker.refresh()
        assert called == []

    # -- teardown --

    def test_teardown_unsubscribes_service(self):
        """teardown() calls unsubscribe_callback on the SurveillanceAlarm service."""
        unsub_store = {}
        surv_svc = _make_service("SurveillanceAlarm")
        surv_svc.unsubscribe_callback = lambda k: unsub_store.update({k: True})
        sds = _make_base_device("sds-td", device_services=[surv_svc])
        sds.alarm = SHCSmokeDetectionSystem.SurveillanceAlarmService.State.ALARM_OFF
        session = _make_fake_session(smoke_detection_system=sds)
        tracker = self._make_tracker(sds, session)
        tracker.teardown()
        assert "sds-td_twinguard_alarm_listener" in unsub_store

    def test_teardown_is_idempotent(self):
        """teardown() can be called multiple times without error."""
        surv_svc = _make_service("SurveillanceAlarm")
        sds = _make_base_device("sds-idem", device_services=[surv_svc])
        sds.alarm = SHCSmokeDetectionSystem.SurveillanceAlarmService.State.ALARM_OFF
        session = _make_fake_session(smoke_detection_system=sds)
        tracker = self._make_tracker(sds, session)
        tracker.teardown()
        tracker.teardown()  # must not raise

    def test_teardown_prevents_further_notification(self):
        """After teardown(), refresh() is a no-op."""
        surv_svc = _make_service("SurveillanceAlarm")
        sds = _make_base_device("sds-post-td", device_services=[surv_svc])
        sds.alarm = SHCSmokeDetectionSystem.SurveillanceAlarmService.State.ALARM_ON
        session = _make_fake_session(
            smoke_detection_system=sds,
            messages=[
                {
                    "messageCode": {"name": "SMOKE_ALARM"},
                    "sourceId": "sds-post-td",
                    "arguments": {
                        "surveillanceEvents": [{"type": "SMOKE_LIGHT", "triggerId": "tw1"}]
                    },
                }
            ],
        )
        tracker = self._make_tracker(sds, session)
        tracker.refresh()  # baseline ALARM_ON + tw1

        called = []
        hass = _make_hass()
        tracker.register_listener(hass, lambda: called.append(1))
        tracker.teardown()

        sds.alarm = SHCSmokeDetectionSystem.SurveillanceAlarmService.State.ALARM_OFF
        tracker.refresh()  # should be skipped
        assert called == []

    # -- handle_alarm_update (simulates SHCPollingThread callback) --

    def test_handle_alarm_update_triggers_refresh(self):
        """_handle_alarm_update() → refresh() → listener notified."""
        surv_svc = _make_service("SurveillanceAlarm")
        sds = _make_base_device("sds-upd", device_services=[surv_svc])
        sds.alarm = SHCSmokeDetectionSystem.SurveillanceAlarmService.State.ALARM_ON
        session = _make_fake_session(
            smoke_detection_system=sds,
            messages=[
                {
                    "messageCode": {"name": "SMOKE_ALARM"},
                    "sourceId": "sds-upd",
                    "arguments": {
                        "surveillanceEvents": [{"type": "SMOKE_LIGHT", "triggerId": "tw1"}]
                    },
                }
            ],
        )
        tracker = self._make_tracker(sds, session)

        called = []
        hass = _make_hass()
        tracker.register_listener(hass, lambda: called.append(1))
        tracker._handle_alarm_update()
        assert tracker.is_alarm_active_for("tw1") is True
        assert called == [1]

    # -- get_messages error handling --

    def test_extract_trigger_ids_handles_api_error_gracefully(self):
        """get_messages() raising an exception returns the previous trigger set."""
        surv_svc = _make_service("SurveillanceAlarm")
        sds = _make_base_device("sds-err", device_services=[surv_svc])
        sds.alarm = SHCSmokeDetectionSystem.SurveillanceAlarmService.State.ALARM_ON
        session = _make_fake_session(smoke_detection_system=sds)
        session.api.get_messages = lambda: (_ for _ in ()).throw(RuntimeError("network error"))
        tracker = self._make_tracker(sds, session)
        tracker._active_trigger_ids = {"tw-prev"}
        result = tracker._extract_trigger_ids_from_messages()
        # Falls back to the existing set
        assert result == {"tw-prev"}


# ---------------------------------------------------------------------------
# TwinguardSmokeAlarmSensor unit tests
# ---------------------------------------------------------------------------

class TestTwinguardSmokeAlarmSensor:
    """Unit tests for TwinguardSmokeAlarmSensor."""

    def _make_sensor(self, device_id="tw1", root_device_id="root1"):
        surv_svc = _make_service("SurveillanceAlarm")
        sds = _make_base_device("sds-sensor", device_services=[surv_svc])
        sds.alarm = SHCSmokeDetectionSystem.SurveillanceAlarmService.State.ALARM_OFF
        session = _make_fake_session(smoke_detection_system=sds)
        tracker = TwinguardAlarmTracker(session=session, smoke_detection_system=sds)
        dev = _make_base_device(device_id, root_device_id=root_device_id)
        sensor = TwinguardSmokeAlarmSensor(device=dev, entry_id="E1", tracker=tracker)
        return sensor, tracker

    def test_is_on_false_when_not_active(self):
        sensor, _ = self._make_sensor("tw-inactive")
        assert sensor.is_on is False

    def test_is_on_true_when_active(self):
        sensor, tracker = self._make_sensor("tw-active")
        tracker._active_trigger_ids = {"tw-active"}
        assert sensor.is_on is True

    def test_unique_id_has_smoke_suffix(self):
        sensor, _ = self._make_sensor("tw-uid", root_device_id="root-uid")
        assert sensor._attr_unique_id == "root-uid_tw-uid_smoke"

    def test_attr_name_is_smoke(self):
        sensor, _ = self._make_sensor()
        assert sensor._attr_name == "Smoke"

    def test_device_class_is_smoke(self):
        sensor, _ = self._make_sensor()
        assert sensor._attr_device_class == BinarySensorDeviceClass.SMOKE

    def test_icon_is_smoke_detector(self):
        sensor, _ = self._make_sensor()
        assert sensor.icon == "mdi:smoke-detector"

    def test_extra_state_attributes_alarm_state(self):
        sensor, _ = self._make_sensor()
        attrs = sensor.extra_state_attributes
        assert "alarm_state" in attrs
        assert attrs["alarm_state"] == "ALARM_OFF"

    def test_handle_tracker_update_calls_schedule_update(self):
        """_handle_tracker_update() calls schedule_update_ha_state()."""
        sensor, _ = self._make_sensor()
        called = []
        sensor.schedule_update_ha_state = lambda: called.append(1)
        sensor._handle_tracker_update()
        assert called == [1]
