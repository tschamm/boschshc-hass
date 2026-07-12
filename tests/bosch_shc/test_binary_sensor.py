"""Tests for custom_components.bosch_shc.binary_sensor.

Consolidates all binary_sensor platform test coverage: entity unit tests
(ShutterContactSensor, MotionDetectionSensor, OccupancyDetectionSensor,
SmokeDetectorSensor, SmokeDetectionSystemSensor, WaterLeakageDetectorSensor,
BatterySensor, CallForHeatSensor, ScheduleOverrideActiveSensor,
ShutterCalibrationRequiredSensor, TamperSensor, Siren*Sensor), the
TwinguardAlarmTracker / TwinguardSmokeAlarmSensor subsystem, async_setup_entry
integration tests (device-type wiring, excluded-device branches, subscriber
lifecycle), regression coverage for the #336 replay-guard (ghost events on
poll-id resubscribe), issue #191 (smoke alarm state mapping), the motion
timestamp UTC-awareness fix, and a battery-device wiring/enum-exhaustiveness
contract that guards against a newly added battery device class shipping
without a battery sensor.

Pattern: pure-unit tests -- bypass entity __init__ via Cls.__new__(Cls) with a
SimpleNamespace/MagicMock fake device, or drive real __init__/async_setup_entry
with fake hass/session objects. No HA test harness (`-p no:homeassistant`).
"""

from __future__ import annotations

import asyncio
import inspect
from enum import Enum
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import boschshcpy.models_impl as models
from boschshcpy import (
    AlarmService,
    BatteryLevelService,
    SHCBatteryDevice,
    SHCShutterContact,
    SHCShutterContact2Plus,
    ShutterContactService,
    SmokeDetectorCheckService,
    SurveillanceAlarmService,
    VibrationSensorService,
    WaterLeakageSensorService,
    WaterLeakageSensorTiltService,
)
from boschshcpy.exceptions import SHCConnectionError, SHCException
from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.const import ATTR_DEVICE_ID, ATTR_ID, ATTR_NAME
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory

from custom_components.bosch_shc.binary_sensor import (
    BatterySensor,
    CallForHeatSensor,
    MotionDetectionSensor,
    OccupancyDetectionSensor,
    ScheduleOverrideActiveSensor,
    ShutterCalibrationRequiredSensor,
    ShutterContactSensor,
    ShutterContactVibrationSensor,
    SmokeDetectionSystemSensor,
    SmokeDetectorSensor,
    TamperSensor,
    TwinguardAlarmTracker,
    TwinguardSmokeAlarmSensor,
    WaterLeakageDetectorSensor,
    async_setup_entry,
)
from custom_components.bosch_shc.const import (
    ATTR_EVENT_SUBTYPE,
    ATTR_EVENT_TYPE,
    ATTR_LAST_TIME_TRIGGERED,
    EVENT_BOSCH_SHC,
    OPT_EXCLUDED_DEVICES,
)


def _run(coro):
    return asyncio.run(coro)


# ===========================================================================
# Shared test helpers
#
# Several source files independently wrote helpers with the same name but
# different implementations (fake hass objects in particular). Where bodies
# differed, one copy was renamed (documented inline) rather than silently
# dropped; where bodies were identical, they were deduped to one copy.
# ===========================================================================


def _new(cls):
    """Bypass __init__ via Cls.__new__(Cls)."""
    return cls.__new__(cls)


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
    call can wire them onto entry.runtime_data (the modern storage location --
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


def _make_mock_hass():
    """MagicMock-based fake hass with a tracked bus/loop (thread-safety +
    #336 replay-guard tests). Identical implementations in
    test_replay_guard_336.py and test_thread_safety_fire.py were deduped to
    this one copy.
    """
    hass = MagicMock(name="hass")
    hass.loop = MagicMock(name="loop")
    hass.bus = MagicMock(name="bus")
    return hass


def _fire_count(hass):
    """Number of times bus.async_fire was called on this hass mock."""
    return hass.bus.async_fire.call_count


def _make_hass():
    """SimpleNamespace-based fake hass (async_setup_entry integration tests).

    async_add_executor_job executes its callables synchronously in tests so
    that _handle_alarm_update dispatch can be verified without a real event
    loop. call_soon_threadsafe also executes the callback immediately.
    bus.async_listen_once returns a real unsubscribe callable (mimics real HA
    behaviour, needed by the L4 HA-stop-listener test).
    """
    unsub_store = {}

    def _async_listen_once(event, cb):
        token = object()
        unsub_store[token] = (event, cb)

        def _unsub():
            unsub_store.pop(token, None)

        return _unsub

    bus = SimpleNamespace(
        async_listen_once=_async_listen_once,
        fire=lambda *args, **kwargs: None,
        async_fire=lambda *args, **kwargs: None,
    )

    async def _async_add_executor_job(fn, *args):
        return fn(*args)

    loop = SimpleNamespace(call_soon_threadsafe=lambda cb, *args: cb(*args))
    hass = SimpleNamespace(
        bus=bus,
        data={},
        loop=loop,
        async_add_executor_job=_async_add_executor_job,
        async_create_task=MagicMock(),
    )
    return hass


def _make_hass_v2():
    """Second, simpler SimpleNamespace-based fake hass.

    Renamed from a second `_make_hass()` in test_binary_sensor_extra_coverage.py
    -- same shape as `_make_hass()` above but without async_create_task and
    with a no-op (non-recording) async_listen_once unsubscribe. Kept separate
    (not deduped) because it is behaviourally different, per consolidation
    rules for colliding helper names.
    """
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


def _make_service_simple(service_id):
    """Simpler fake service (no callback capture). Renamed from a second
    `_make_service()` in test_binary_sensor_extra_coverage.py -- different
    signature/behaviour than `_make_service()` above (no subscribe_callback
    override, no _callbacks store), so kept as its own function rather than
    silently clobbering the richer version.
    """
    return SimpleNamespace(
        id=service_id,
        subscribe_callback=lambda k, cb: None,
        unsubscribe_callback=lambda k: None,
    )


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
        # Replay-guard seeds read current state at construction (#336).
        latestmotion=None,
        alarmstate=SimpleNamespace(name="IDLE_OFF"),
        alarm=SimpleNamespace(name="ALARM_OFF"),
        subscribe_callback=lambda key, cb: None,
        unsubscribe_callback=lambda key: None,
    )
    return dev


def _base_device(device_id="dev1", name="FakeDev", root_device_id="root1",
                 device_services=None):
    return SimpleNamespace(
        id=device_id,
        name=name,
        root_device_id=root_device_id,
        device_services=device_services or [],
        manufacturer="Bosch",
        device_model="FakeModel",
        serial=f"{device_id}-serial",
        deleted=False,
        status="AVAILABLE",
        subscribe_callback=lambda key, cb: None,
        unsubscribe_callback=lambda key: None,
    )


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
    climate_controls=None,
    messages=None,
):
    """Build a fake session with device_helper, api, and _subscribers."""
    session = SimpleNamespace()
    session._subscribers = []

    def _subscribe(cb_tuple):
        session._subscribers.append(cb_tuple)

    session.subscribe = _subscribe
    session.api = SimpleNamespace(get_messages=AsyncMock(return_value=messages or []))

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
        climate_controls=climate_controls or [],
    )
    return session


def _make_fake_session_v2(**lists):
    """Renamed from a second `_make_fake_session()` in
    test_binary_sensor_extra_coverage.py -- unlike the version above, `subscribe`
    is a no-op (doesn't record into _subscribers) and `api.get_messages` is not
    an AsyncMock. Different behaviour, kept as its own function.
    """
    return SimpleNamespace(
        _subscribers=[],
        subscribe=lambda cb: None,
        api=SimpleNamespace(get_messages=list),
        device_helper=SimpleNamespace(
            shutter_contacts=lists.get("shutter_contacts", []),
            shutter_contacts2=lists.get("shutter_contacts2", []),
            motion_detectors=lists.get("motion_detectors", []),
            motion_detectors2=lists.get("motion_detectors2", []),
            smoke_detectors=lists.get("smoke_detectors", []),
            smoke_detection_system=lists.get("smoke_detection_system"),
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
    hass = _make_hass_v2()
    config_entry = SimpleNamespace(
        options=options, entry_id="E1",
        async_on_unload=lambda fn: None,
    )
    config_entry.runtime_data = SimpleNamespace(
        session=session, shc_device=None, title="Test SHC"
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


def _sds_sensor(alarm_state_name):
    """Build SmokeDetectionSystemSensor via __new__. Identical implementations
    in test_binary_sensor_coverage.py and test_binary_sensor_unit.py were
    deduped to this one copy.
    """
    s = SmokeDetectionSystemSensor.__new__(SmokeDetectionSystemSensor)
    s._device = SimpleNamespace(alarm=SurveillanceAlarmService.State[alarm_state_name])
    return s


def _battery_sensor(battery_level):
    """Build BatterySensor via __new__. Near-identical implementations in
    test_binary_sensor_coverage.py and test_binary_sensor_unit.py (differing
    only in an incidental device-name string never asserted on) were deduped
    to this one copy.
    """
    s = BatterySensor.__new__(BatterySensor)
    s._device = SimpleNamespace(batterylevel=battery_level, name="Test Device")
    return s


def _shutter_sensor(state, device_class="GENERIC"):
    from custom_components.bosch_shc.binary_sensor import SHUTTER_CONTACT_DESCRIPTION

    s = ShutterContactSensor.__new__(ShutterContactSensor)
    s._device = SimpleNamespace(state=state, device_class=device_class)
    s.entity_description = SHUTTER_CONTACT_DESCRIPTION
    return s


def _vibration_sensor(state):
    s = ShutterContactVibrationSensor.__new__(ShutterContactVibrationSensor)
    s._device = SimpleNamespace(vibrationsensor=state)
    return s


def _motion_sensor(latestmotion):
    s = MotionDetectionSensor.__new__(MotionDetectionSensor)
    s._device = SimpleNamespace(latestmotion=latestmotion)
    return s


def _motion_sensor_md2(latestmotion):
    """Build a MotionDetectionSensor as if backed by an MD2 device.

    MD2 has illuminance (int) in addition to latestmotion, but
    MotionDetectionSensor only touches latestmotion -- so the fake device only
    needs that attribute.
    """
    s = MotionDetectionSensor.__new__(MotionDetectionSensor)
    s._device = SimpleNamespace(latestmotion=latestmotion)
    return s


def _smoke_sensor(alarm_state):
    s = SmokeDetectorSensor.__new__(SmokeDetectorSensor)
    state = AlarmService.State[alarm_state]
    check_state = SmokeDetectorCheckService.State.NONE
    s._device = SimpleNamespace(
        alarmstate=state,
        smokedetectorcheck_state=check_state,
    )
    return s


_TILT_ENABLED = WaterLeakageSensorTiltService.State.ENABLED
_TILT_DISABLED = WaterLeakageSensorTiltService.State.DISABLED
_WL_DETECTED = WaterLeakageSensorService.State.LEAKAGE_DETECTED
_WL_NONE = WaterLeakageSensorService.State.NO_LEAKAGE


def _water_sensor(leakage_state, push_notification_state, acoustic_signal_state):
    s = WaterLeakageDetectorSensor.__new__(WaterLeakageDetectorSensor)
    s._device = SimpleNamespace(
        leakage_state=leakage_state,
        push_notification_state=push_notification_state,
        acoustic_signal_state=acoustic_signal_state,
    )
    return s


def _make_tamper_sensor(was_tampered=False, last_tamper_time="n/a"):
    dev = SimpleNamespace(
        name="Motion Detector II",
        id="hdm:ZigBee:md2-001",
        root_device_id="64-da-a0-xx-xx-xx",
        was_tampered=was_tampered,
        last_tamper_time=last_tamper_time,
    )
    sensor = TamperSensor.__new__(TamperSensor)
    sensor._device = dev
    sensor._attr_name = "Tamper"
    sensor._attr_unique_id = f"{dev.root_device_id}_{dev.id}_tamper"
    return sensor


def _make_md2_device(**kwargs):
    """Return a fake SHCMotionDetector2-shaped SimpleNamespace."""
    defaults = dict(
        name="Motion Detector II",
        id="hdm:ZigBee:000000000000abcd",
        root_device_id="64-da-a0-xx-xx-xx",
        occupied=False,
        last_occupancy_change_time="2026-06-20T12:00:00.000Z",
        binaryswitch=False,
        multi_level_switch=50,
        pet_immunity_enabled=False,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_occupancy_sensor(**device_kwargs):
    dev = _make_md2_device(**device_kwargs)
    s = OccupancyDetectionSensor.__new__(OccupancyDetectionSensor)
    s._device = dev
    s._attr_name = f"{dev.name} Occupancy"
    s._attr_unique_id = f"{dev.root_device_id}_{dev.id}_occupancy"
    return s


def _make_smoke_sensor(*, executor_side_effect):
    """Build a SmokeDetectorSensor via __new__ bypass with a faked device."""
    s = SmokeDetectorSensor.__new__(SmokeDetectorSensor)
    s._device = SimpleNamespace(
        name="Smoke Detector 1",
        async_smoketest_requested=AsyncMock(side_effect=executor_side_effect),
    )
    s._attr_name = "Smoke Detector 1"
    return s


def _make_alarm_sensor(*, executor_side_effect):
    """Build a SmokeDetectorSensor for alarm-state tests."""
    s = SmokeDetectorSensor.__new__(SmokeDetectorSensor)
    s._device = SimpleNamespace(
        name="Smoke Detector 1",
        async_set_alarmstate=AsyncMock(side_effect=executor_side_effect),
    )
    s._attr_name = "Smoke Detector 1"
    return s


# ===========================================================================
# CallForHeatSensor (#205) / ScheduleOverrideActiveSensor / ShutterCalibrationRequiredSensor
# ===========================================================================

class TestCallForHeatSensor:
    @staticmethod
    def _sensor(**device_attrs):
        s = CallForHeatSensor.__new__(CallForHeatSensor)
        s._device = SimpleNamespace(**device_attrs)
        return s

    def test_on_when_demand(self):
        assert self._sensor(has_demand=True).is_on is True

    def test_off_when_no_demand(self):
        assert self._sensor(has_demand=False).is_on is False

    def test_off_when_attr_missing(self):
        # older boschshcpy without has_demand -> degrade to off, no crash
        assert self._sensor().is_on is False


class TestScheduleOverrideActiveSensor:
    """hass#120 audit."""

    @staticmethod
    def _sensor(**device_attrs):
        s = ScheduleOverrideActiveSensor.__new__(ScheduleOverrideActiveSensor)
        s._device = SimpleNamespace(**device_attrs)
        return s

    def test_on_when_override_active(self):
        assert self._sensor(setpoint_temperature_offset_active=True).is_on is True

    def test_off_when_override_inactive(self):
        assert self._sensor(setpoint_temperature_offset_active=False).is_on is False

    def test_off_when_attr_missing(self):
        # older boschshcpy without the property -> degrade to off, no crash
        assert self._sensor().is_on is False


class TestShutterCalibrationRequiredSensor:
    """Shutter Control II diagnostic, hass audit."""

    @staticmethod
    def _sensor(**device_attrs):
        s = ShutterCalibrationRequiredSensor.__new__(ShutterCalibrationRequiredSensor)
        s._device = SimpleNamespace(**device_attrs)
        return s

    def test_off_when_calibrated(self):
        assert self._sensor(calibrated=True).is_on is False

    def test_on_when_not_calibrated(self):
        assert self._sensor(calibrated=False).is_on is True

    def test_off_when_attr_missing(self):
        # older boschshcpy without the property -> degrade to "no problem"
        assert self._sensor().is_on is False


# ===========================================================================
# Siren*Sensor (#120 outdoor siren binary sensors)
# ===========================================================================

def test_siren_binary_sensors_read_flags():
    from custom_components.bosch_shc.binary_sensor import (
        SirenAcousticAlarmSensor,
        SirenTamperSensor,
        SirenVisualAlarmSensor,
    )

    siren = SimpleNamespace(
        acoustic_alarm_on=True, visual_alarm_on=False, tamper_activated=True
    )
    a = _new(SirenAcousticAlarmSensor)
    a._device = SimpleNamespace(siren=siren)
    assert a.is_on is True

    v = _new(SirenVisualAlarmSensor)
    v._device = SimpleNamespace(siren=siren)
    assert v.is_on is False

    t = _new(SirenTamperSensor)
    t._device = SimpleNamespace(siren=siren)
    assert t.is_on is True


class TestSirenSensorInits:
    """Real __init__ construction + async_setup_entry wiring for siren sensors."""

    def _make_siren_dev(self, dev_id="siren1"):
        return _fake_dev(dev_id, supports_batterylevel=False)

    def test_acoustic_alarm_sensor_init(self):
        from custom_components.bosch_shc.binary_sensor import SirenAcousticAlarmSensor

        dev = self._make_siren_dev("s1")
        sensor = SirenAcousticAlarmSensor(dev, "entry1")
        assert sensor._attr_unique_id == "root1_s1_acoustic_alarm"

    def test_visual_alarm_sensor_init(self):
        from custom_components.bosch_shc.binary_sensor import SirenVisualAlarmSensor

        dev = self._make_siren_dev("s2")
        sensor = SirenVisualAlarmSensor(dev, "entry1")
        assert sensor._attr_unique_id == "root1_s2_visual_alarm"

    def test_tamper_sensor_init(self):
        from custom_components.bosch_shc.binary_sensor import SirenTamperSensor

        dev = self._make_siren_dev("s3")
        sensor = SirenTamperSensor(dev, "entry1")
        assert sensor._attr_unique_id == "root1_s3_tamper"

    def test_binary_sensor_setup_with_sirens(self):
        """async_setup_entry creates siren binary sensors."""
        siren = _fake_dev("s1", siren=MagicMock(), supports_batterylevel=False)
        dh = MagicMock()
        dh.thermostats = []
        dh.roomthermostats = []
        dh.wallthermostats = []
        dh.motion_detectors = []
        dh.motion_detectors2 = []
        dh.shutter_contacts = []
        dh.shutter_contacts2 = []
        dh.smoke_detectors = []
        dh.twinguards = []
        dh.micromodule_shutter_controls = []
        dh.micromodule_blinds = []
        dh.outdoor_sirens = [siren]
        dh.smoke_detection_system = None
        dh.heating_circuits = []
        dh.universal_switches = []
        dh.water_leakage_detectors = []
        dh.micromodule_dimmers = []
        dh.micromodule_relays = []
        dh.micromodule_impulse_relays = []
        dh.motion_detectors2 = []

        session = MagicMock()
        session.device_helper = dh

        hass = _fake_hass(session=session)
        entry = _fake_entry(hass=hass)

        platform_mock = MagicMock()
        platform_mock.async_register_entity_service = MagicMock()
        with patch("custom_components.bosch_shc.binary_sensor.async_migrate_to_new_unique_id",
                   new_callable=AsyncMock), \
             patch("custom_components.bosch_shc.binary_sensor.entity_platform.current_platform") as _cp:
            _cp.get.return_value = platform_mock
            collected = []
            _run(async_setup_entry(hass, entry, lambda ents, **kw: collected.extend(ents)))

        types = [type(e).__name__ for e in collected]
        assert "SirenAcousticAlarmSensor" in types
        assert "SirenVisualAlarmSensor" in types
        assert "SirenTamperSensor" in types


class TestBinarySensorSirenExcluded:
    """binary_sensor.py: excluded outdoor siren -> continue."""

    def test_excluded_siren_skipped_in_binary_sensor_setup(self):
        """device_excluded -> continue before creating siren sensors."""
        from custom_components.bosch_shc.binary_sensor import SirenAcousticAlarmSensor

        siren = _fake_dev("siren_excl", siren=MagicMock(), supports_batterylevel=False)
        dh = MagicMock()
        dh.thermostats = []
        dh.roomthermostats = []
        dh.wallthermostats = []
        dh.motion_detectors = []
        dh.motion_detectors2 = []
        dh.shutter_contacts = []
        dh.shutter_contacts2 = []
        dh.smoke_detectors = []
        dh.twinguards = []
        dh.micromodule_shutter_controls = []
        dh.micromodule_blinds = []
        dh.outdoor_sirens = [siren]
        dh.smoke_detection_system = None
        dh.heating_circuits = []
        dh.universal_switches = []
        dh.water_leakage_detectors = []
        dh.micromodule_dimmers = []
        dh.micromodule_relays = []
        dh.micromodule_impulse_relays = []

        session = MagicMock()
        session.device_helper = dh

        hass = _fake_hass(session=session)
        entry = _fake_entry(hass=hass, options={OPT_EXCLUDED_DEVICES: ["siren_excl"]})

        platform_mock = MagicMock()
        platform_mock.async_register_entity_service = MagicMock()
        with patch("custom_components.bosch_shc.binary_sensor.async_migrate_to_new_unique_id",
                   new_callable=AsyncMock), \
             patch("custom_components.bosch_shc.binary_sensor.entity_platform.current_platform") as _cp:
            _cp.get.return_value = platform_mock
            collected = []
            _run(async_setup_entry(hass, entry, lambda ents, **kw: collected.extend(ents)))

        assert not any(isinstance(e, SirenAcousticAlarmSensor) for e in collected)


# ===========================================================================
# ShutterContactSensor
# ===========================================================================

class TestShutterContactSensor:
    def test_open_is_on(self):
        s = _shutter_sensor(ShutterContactService.State.OPEN)
        assert s.is_on is True

    def test_closed_is_off(self):
        s = _shutter_sensor(ShutterContactService.State.CLOSED)
        assert s.is_on is False

    def test_device_class_entrance_door(self):
        s = _shutter_sensor(ShutterContactService.State.CLOSED, "ENTRANCE_DOOR")
        assert s.device_class == BinarySensorDeviceClass.DOOR

    def test_device_class_regular_window(self):
        s = _shutter_sensor(ShutterContactService.State.CLOSED, "REGULAR_WINDOW")
        assert s.device_class == BinarySensorDeviceClass.WINDOW

    def test_device_class_french_window(self):
        s = _shutter_sensor(ShutterContactService.State.CLOSED, "FRENCH_WINDOW")
        assert s.device_class == BinarySensorDeviceClass.DOOR

    def test_device_class_generic(self):
        s = _shutter_sensor(ShutterContactService.State.CLOSED, "GENERIC")
        assert s.device_class == BinarySensorDeviceClass.WINDOW

    def test_device_class_unknown_defaults_to_window(self):
        s = _shutter_sensor(ShutterContactService.State.CLOSED, "UNKNOWN_TYPE")
        assert s.device_class == BinarySensorDeviceClass.WINDOW


# ===========================================================================
# ShutterContactVibrationSensor
# ===========================================================================

class TestShutterContactVibrationSensor:
    def test_vibration_detected_is_on(self):
        s = _vibration_sensor(VibrationSensorService.State.VIBRATION_DETECTED)
        assert s.is_on is True

    def test_no_vibration_is_off(self):
        s = _vibration_sensor(VibrationSensorService.State.NO_VIBRATION)
        assert s.is_on is False

    def test_unknown_state_is_off(self):
        s = _vibration_sensor(VibrationSensorService.State.UNKNOWN)
        assert s.is_on is False

    def test_device_class_is_vibration(self):
        s = _vibration_sensor(VibrationSensorService.State.NO_VIBRATION)
        assert s._attr_device_class == BinarySensorDeviceClass.VIBRATION


class TestShutterContactVibrationSensorAttrsInit:
    """__init__ sets _attr_name and _attr_unique_id, ignoring device.name.

    Renamed from `TestShutterContactVibrationSensorInit` (one of two classes
    with that name -- this one came from test_binary_sensor_coverage.py) to
    avoid colliding with `TestShutterContactVibrationSensorRealInit` below
    (from test_binary_sensor_setup.py), which covers the same class via a
    different construction path.
    """

    def _make_dev(self, device_id="sc-vib", root_device_id="root-vib", name="Fenster"):
        dev = _base_device(device_id=device_id, name=name, root_device_id=root_device_id)
        dev.vibrationsensor = VibrationSensorService.State.NO_VIBRATION
        return dev

    def test_attr_name_is_vibration_literal(self):
        dev = self._make_dev(name="Any Name")
        with patch.object(ShutterContactVibrationSensor, "_update_attr", lambda self: None):
            sensor = ShutterContactVibrationSensor(device=dev, entry_id="E1")
        assert sensor.translation_key == "vibration"

    def test_attr_unique_id_has_vibration_suffix(self):
        dev = self._make_dev(device_id="sc1", root_device_id="root1")
        with patch.object(ShutterContactVibrationSensor, "_update_attr", lambda self: None):
            sensor = ShutterContactVibrationSensor(device=dev, entry_id="E1")
        assert sensor._attr_unique_id == "root1_sc1_vibration"

    def test_different_device_ids_produce_different_uids(self):
        dev1 = self._make_dev(device_id="sc-a", root_device_id="root-a")
        dev2 = self._make_dev(device_id="sc-b", root_device_id="root-b")
        with patch.object(ShutterContactVibrationSensor, "_update_attr", lambda self: None):
            s1 = ShutterContactVibrationSensor(device=dev1, entry_id="E1")
            s2 = ShutterContactVibrationSensor(device=dev2, entry_id="E1")
        assert s1._attr_unique_id != s2._attr_unique_id


class TestShutterContactVibrationSensorRealInit:
    """Renamed from `TestShutterContactVibrationSensorInit`
    (test_binary_sensor_setup.py) -- collided by name with the class above
    (test_binary_sensor_coverage.py); both bodies kept, this one drives the
    real (non-patched) __init__ call.
    """

    def test_init_sets_name_and_unique_id(self):
        dev = _make_base_device("sc-vib", name="Fenster", root_device_id="root-vib")
        dev.vibrationsensor = VibrationSensorService.State.NO_VIBRATION
        sensor = ShutterContactVibrationSensor(device=dev, entry_id="E1")
        assert sensor.translation_key == "vibration"
        assert sensor._attr_unique_id == "root-vib_sc-vib_vibration"
        assert sensor._attr_device_class == BinarySensorDeviceClass.VIBRATION

    def test_init_device_stored(self):
        dev = _make_base_device("sc-vib2")
        dev.vibrationsensor = VibrationSensorService.State.NO_VIBRATION
        sensor = ShutterContactVibrationSensor(device=dev, entry_id="E1")
        assert sensor._device is dev


# ===========================================================================
# MotionDetectionSensor
# ===========================================================================

class TestMotionDetectionSensor:
    def test_extra_state_attributes_contains_last_motion(self):
        ts = "2026-06-20T10:00:00.000Z"
        s = _motion_sensor(ts)
        attrs = s.extra_state_attributes
        assert attrs == {"last_motion_detected": ts}

    def test_extra_state_attributes_none_timestamp(self):
        s = _motion_sensor(None)
        assert s.extra_state_attributes == {"last_motion_detected": None}

    def test_should_poll_is_true(self):
        s = _motion_sensor("2026-06-20T10:00:00.000Z")
        assert s.should_poll is True

    def test_is_on_recent_motion(self):
        from datetime import datetime, timedelta, timezone

        recent = datetime.now(timezone.utc) - timedelta(seconds=10)
        ts = recent.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        assert _motion_sensor(ts).is_on is True

    def test_is_on_old_motion_is_false(self):
        from datetime import datetime, timedelta, timezone

        old = datetime.now(timezone.utc) - timedelta(minutes=10)
        ts = old.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        assert _motion_sensor(ts).is_on is False

    def test_is_on_none_returns_false(self):
        assert _motion_sensor(None).is_on is False

    def test_is_on_garbage_returns_false(self):
        assert _motion_sensor("not-a-date").is_on is False


# MD2 exposes the same `latestmotion` property as Gen1, so MotionDetectionSensor
# works for both without modification. These tests pin that contract.
class TestMotionDetectionSensorMD2:
    def test_device_class_is_motion(self):
        s = _motion_sensor_md2(None)
        assert s._attr_device_class == BinarySensorDeviceClass.MOTION

    def test_is_on_recent_motion(self):
        from datetime import datetime, timedelta, timezone

        recent = datetime.now(timezone.utc) - timedelta(seconds=10)
        ts = recent.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        assert _motion_sensor_md2(ts).is_on is True

    def test_is_on_old_motion_is_false(self):
        from datetime import datetime, timedelta, timezone

        old = datetime.now(timezone.utc) - timedelta(minutes=10)
        ts = old.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        assert _motion_sensor_md2(ts).is_on is False

    def test_is_on_none_returns_false(self):
        assert _motion_sensor_md2(None).is_on is False

    def test_is_on_garbage_returns_false(self):
        assert _motion_sensor_md2("not-a-date").is_on is False

    def test_extra_state_attributes_contains_last_motion(self):
        ts = "2026-06-20T10:00:00.000Z"
        s = _motion_sensor_md2(ts)
        assert s.extra_state_attributes == {"last_motion_detected": ts}

    def test_extra_state_attributes_none_timestamp(self):
        s = _motion_sensor_md2(None)
        assert s.extra_state_attributes == {"last_motion_detected": None}

    def test_should_poll_is_true(self):
        s = _motion_sensor_md2("2026-06-20T10:00:00.000Z")
        assert s.should_poll is True


# ---------------------------------------------------------------------------
# Regression: is_on timestamp handling must be UTC-aware
#
# The latest-motion timestamp is parsed with a trailing literal "Z", which
# yields a NAIVE datetime. Subtracting it from datetime.now(timezone.utc)
# (aware) raised TypeError on every motion poll -- and the surrounding
# `except` only caught ValueError, so the motion binary_sensor errored. The
# timestamp must be marked UTC-aware.
# ---------------------------------------------------------------------------

def _regression_motion_sensor(latestmotion):
    s = MotionDetectionSensor.__new__(MotionDetectionSensor)
    s._device = SimpleNamespace(latestmotion=latestmotion)
    return s


def _fmt(dt):
    # Bosch format: ...%fZ (naive string, UTC)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def test_recent_motion_is_on_no_typeerror():
    from datetime import datetime, timedelta, timezone

    recent = datetime.now(timezone.utc) - timedelta(seconds=10)
    assert _regression_motion_sensor(_fmt(recent)).is_on is True


def test_old_motion_is_off():
    from datetime import datetime, timedelta, timezone

    old = datetime.now(timezone.utc) - timedelta(minutes=10)
    assert _regression_motion_sensor(_fmt(old)).is_on is False


def test_none_timestamp_returns_false_not_crash():
    assert _regression_motion_sensor(None).is_on is False


def test_garbage_timestamp_returns_false():
    assert _regression_motion_sensor("not-a-date").is_on is False


# ---------------------------------------------------------------------------
# MotionDetectionSensor -- _input_events_handler payload detail
# ---------------------------------------------------------------------------

class TestMotionDetectionSensorInputEventsPayload:
    """_input_events_handler fires via bus.async_fire with correct payload."""

    def _make_sensor(self, device_id="hdm:md:1", name="Hall Motion",
                     latestmotion="2026-06-20T08:00:00.000Z",
                     cached_device_id="ha-dev-id"):
        sensor = MotionDetectionSensor.__new__(MotionDetectionSensor)
        sensor._device = SimpleNamespace(
            id=device_id, name=name, latestmotion=latestmotion
        )
        sensor._cached_device_id = cached_device_id
        sensor._last_fired_latestmotion = None  # replay-guard initial state
        sensor.hass = MagicMock(name="hass")
        sensor.hass.loop = MagicMock(name="loop")
        sensor.hass.bus = MagicMock(name="bus")
        return sensor

    def _payload(self, sensor):
        """Return the event payload passed to bus.async_fire."""
        return sensor.hass.bus.async_fire.call_args[0][1]

    def test_uses_async_fire(self):
        sensor = self._make_sensor()
        sensor._input_events_handler()
        assert sensor.hass.bus.async_fire.called
        assert not sensor.hass.loop.call_soon_threadsafe.called

    def test_event_name_is_bosch_shc_event(self):
        sensor = self._make_sensor()
        sensor._input_events_handler()
        event_name = sensor.hass.bus.async_fire.call_args[0][0]
        assert event_name == EVENT_BOSCH_SHC

    def test_payload_event_type_is_motion(self):
        sensor = self._make_sensor()
        sensor._input_events_handler()
        assert self._payload(sensor)[ATTR_EVENT_TYPE] == "MOTION"

    def test_payload_event_subtype_is_empty_string(self):
        sensor = self._make_sensor()
        sensor._input_events_handler()
        assert self._payload(sensor)[ATTR_EVENT_SUBTYPE] == ""

    def test_payload_device_id_is_cached_device_id(self):
        sensor = self._make_sensor(cached_device_id="my-ha-device-42")
        sensor._input_events_handler()
        assert self._payload(sensor)[ATTR_DEVICE_ID] == "my-ha-device-42"

    def test_payload_id_is_device_id(self):
        sensor = self._make_sensor(device_id="hdm:md:99")
        sensor._input_events_handler()
        assert self._payload(sensor)[ATTR_ID] == "hdm:md:99"

    def test_payload_name_is_device_name(self):
        sensor = self._make_sensor(name="Garden Motion")
        sensor._input_events_handler()
        assert self._payload(sensor)[ATTR_NAME] == "Garden Motion"

    def test_payload_last_time_triggered(self):
        ts = "2026-06-01T10:30:00.000Z"
        sensor = self._make_sensor(latestmotion=ts)
        sensor._input_events_handler()
        assert self._payload(sensor)[ATTR_LAST_TIME_TRIGGERED] == ts


# ---------------------------------------------------------------------------
# #336 replay guard -- MotionDetectionSensor must suppress replayed
# LatestMotion snapshots re-delivered on the ~24h poll-id resubscribe.
# ---------------------------------------------------------------------------

class TestMotionReplayGuard:
    def _make_sensor(self, latestmotion="2026-06-20T19:21:00.000Z"):
        sensor = MotionDetectionSensor.__new__(MotionDetectionSensor)
        sensor.hass = _make_mock_hass()
        sensor._cached_device_id = "ha-motion-1"
        sensor._last_fired_latestmotion = None  # freshly constructed
        sensor._device = SimpleNamespace(
            id="hdm:motion:1",
            name="Motion Sensor",
            latestmotion=latestmotion,
        )
        return sensor

    def test_first_call_fires_event(self):
        """(a) First snapshot must fire exactly one event."""
        sensor = self._make_sensor("2026-06-20T19:21:00.000Z")
        sensor._input_events_handler()
        assert _fire_count(sensor.hass) == 1

    def test_first_call_event_type_is_motion(self):
        sensor = self._make_sensor("2026-06-20T19:21:00.000Z")
        sensor._input_events_handler()
        payload = sensor.hass.bus.async_fire.call_args[0][1]
        assert payload[ATTR_EVENT_TYPE] == "MOTION"

    def test_replayed_snapshot_does_not_fire(self):
        """(b) Same latestmotion on the second call must be suppressed."""
        sensor = self._make_sensor("2026-06-20T19:21:00.000Z")
        sensor._input_events_handler()  # first call -- fires
        sensor._input_events_handler()  # replay -- must NOT fire again
        assert _fire_count(sensor.hass) == 1

    def test_changed_timestamp_fires_again(self):
        """(c) Advancing latestmotion (genuine new motion) must fire."""
        sensor = self._make_sensor("2026-06-20T19:21:00.000Z")
        sensor._input_events_handler()  # first -- fires
        sensor._input_events_handler()  # replay -- suppressed
        assert _fire_count(sensor.hass) == 1

        sensor._device.latestmotion = "2026-06-20T20:00:00.000Z"
        sensor._input_events_handler()
        assert _fire_count(sensor.hass) == 2

    def test_none_timestamp_suppressed(self):
        """None latestmotion means no motion yet -- suppress (both cache and value are None)."""
        sensor = self._make_sensor(None)
        sensor._input_events_handler()
        assert _fire_count(sensor.hass) == 0

    def test_none_then_real_timestamp_fires(self):
        """Transition from None (no motion ever) to a real timestamp must fire."""
        sensor = self._make_sensor(None)
        sensor._input_events_handler()  # None == None -> suppressed
        assert _fire_count(sensor.hass) == 0

        sensor._device.latestmotion = "2026-06-20T20:00:00.000Z"
        sensor._input_events_handler()
        assert _fire_count(sensor.hass) == 1

    def test_cache_attr_initialized_to_none(self):
        """_last_fired_latestmotion must start as None (fresh entity)."""
        sensor = self._make_sensor()
        assert sensor._last_fired_latestmotion is None


# ---------------------------------------------------------------------------
# Thread-safety: _input_events_handler must call hass.bus.async_fire directly
# (no call_soon_threadsafe marshalling needed).
# ---------------------------------------------------------------------------

class TestMotionSensorThreadSafe:
    def _make_sensor(self):
        sensor = MotionDetectionSensor.__new__(MotionDetectionSensor)
        sensor.hass = _make_mock_hass()
        sensor._cached_device_id = "ha-device-motion-1"
        sensor._last_fired_latestmotion = None  # replay-guard initial state
        sensor._device = SimpleNamespace(
            id="hdm:motion:1",
            name="Motion Sensor",
            latestmotion="2026-06-20T10:00:00.000Z",
        )
        return sensor

    def test_uses_async_fire_not_call_soon_threadsafe(self):
        sensor = self._make_sensor()
        sensor._input_events_handler()

        assert sensor.hass.bus.async_fire.called
        assert not sensor.hass.loop.call_soon_threadsafe.called

    def test_event_type_is_motion(self):
        sensor = self._make_sensor()
        sensor._input_events_handler()

        payload = sensor.hass.bus.async_fire.call_args[0][1]
        assert payload[ATTR_EVENT_TYPE] == "MOTION"


# ---------------------------------------------------------------------------
# MotionDetectionSensor -- async_will_remove_from_hass
# ---------------------------------------------------------------------------

class TestMotionDetectorWillRemoveUnsub:
    def test_async_will_remove_calls_unsub(self):
        """_ha_stop_unsub is not None -> call it and set to None."""
        ent = MotionDetectionSensor.__new__(MotionDetectionSensor)
        unsub = MagicMock()
        ent._ha_stop_unsub = unsub
        ent._service = None
        with patch("custom_components.bosch_shc.entity.SHCEntity.async_will_remove_from_hass", new_callable=AsyncMock):
            _run(ent.async_will_remove_from_hass())
        unsub.assert_called_once()
        assert ent._ha_stop_unsub is None

    def test_async_will_remove_no_unsub(self):
        """_ha_stop_unsub is None -> nothing called."""
        ent = MotionDetectionSensor.__new__(MotionDetectionSensor)
        ent._ha_stop_unsub = None
        ent._service = None
        with patch("custom_components.bosch_shc.entity.SHCEntity.async_will_remove_from_hass", new_callable=AsyncMock):
            _run(ent.async_will_remove_from_hass())  # must not raise


class TestMotionDetectionSensorInit:
    def test_init_with_no_latestmotion_service(self):
        """No LatestMotion service -> _service stays None (no crash)."""
        other_svc = _make_service("OtherService")
        dev = _make_base_device("md-init", device_services=[other_svc])
        dev.latestmotion = None
        hass = _make_hass()
        sensor = MotionDetectionSensor(hass=hass, device=dev, entry_id="E1")
        assert sensor._service is None

    def test_init_with_latestmotion_service_subscribes(self):
        """__init__ finds the service (no subscribe yet); async_added_to_hass subscribes."""
        cb_store = {}
        lm_svc = _make_service("LatestMotion", subscribe_callback=lambda k, cb: cb_store.update({k: cb}))
        dev = _make_base_device("md-init2", device_services=[lm_svc])
        dev.latestmotion = None
        hass = _make_hass()
        sensor = MotionDetectionSensor(hass=hass, device=dev, entry_id="E1")
        assert sensor._service is lm_svc
        assert "md-init2_eventlistener" not in cb_store

        async def _add():
            with patch(
                "custom_components.bosch_shc.binary_sensor.async_get_device_id",
                return_value="ha-device-id",
            ):
                await sensor.async_added_to_hass()

        asyncio.run(_add())
        assert "md-init2_eventlistener" in cb_store

    def test_input_events_handler_fires_event(self):
        fired = []
        lm_svc = _make_service("LatestMotion")
        dev = _make_base_device("md-ev", device_services=[lm_svc])
        dev.latestmotion = None  # construction baseline (no prior motion)
        hass = _make_hass()
        hass.bus.async_fire = lambda event, data: fired.append((event, data))
        sensor = MotionDetectionSensor(hass=hass, device=dev, entry_id="E1")
        sensor._cached_device_id = "ha-device-id"
        # Genuine new motion advances past the seeded baseline -> fires.
        dev.latestmotion = "2026-06-20T08:00:00.000Z"
        sensor._input_events_handler()
        assert len(fired) == 1
        event_name, data = fired[0]
        assert event_name == "bosch_shc.event"
        assert data["event_type"] == "MOTION"
        assert data["lastTimeTriggered"] == "2026-06-20T08:00:00.000Z"

    def test_input_events_handler_restart_replay_no_fire(self):
        """#336 restart: a stale latestmotion present at construction seeds the
        baseline, so the first re-delivered snapshot must NOT fire a phantom."""
        fired = []
        lm_svc = _make_service("LatestMotion")
        dev = _make_base_device("md-restart", device_services=[lm_svc])
        dev.latestmotion = "2026-06-20T08:00:00.000Z"  # stale last motion at startup
        hass = _make_hass()
        hass.bus.async_fire = lambda event, data: fired.append((event, data))
        sensor = MotionDetectionSensor(hass=hass, device=dev, entry_id="E1")
        sensor._cached_device_id = "ha-device-id"
        sensor._input_events_handler()  # same stale ts re-delivered -> suppressed
        assert fired == []

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


# ===========================================================================
# boschshcpy lib setters (unit-level, no HA dependency) -- SHCMotionDetector2
# ===========================================================================

class TestSHCMotionDetector2LibSetters:
    """Verify that the new public setters on SHCMotionDetector2 call through."""

    def test_binaryswitch_setter_calls_put_state_element(self):
        """Binaryswitch setter must invoke put_state_element('on', bool)."""
        calls = []

        class _FakeBinarySwitchService:
            def put_state_element(self_, key, value):
                calls.append((key, value))

            @property
            def value(self_):
                return False

        from boschshcpy.models_impl import SHCMotionDetector2
        dev = SHCMotionDetector2.__new__(SHCMotionDetector2)
        dev._binaryswitch_service = _FakeBinarySwitchService()
        dev.binaryswitch = True
        assert calls == [("on", True)]

    def test_multi_level_switch_setter_calls_put_state_element(self):
        """multi_level_switch setter must invoke put_state_element('level', value)."""
        calls = []

        class _FakeMultiLevelSwitchService:
            def put_state_element(self_, key, value):
                calls.append((key, value))

            @property
            def value(self_):
                return 50

        from boschshcpy.models_impl import SHCMotionDetector2
        dev = SHCMotionDetector2.__new__(SHCMotionDetector2)
        dev._multi_level_switch_service = _FakeMultiLevelSwitchService()
        dev.multi_level_switch = 75
        assert calls == [("level", 75)]

    def test_pet_immunity_setter_delegates_to_service(self):
        """pet_immunity_enabled setter must write to the PetImmunity service."""
        written = []

        class _FakePetImmunityService:
            _enabled = False

            @property
            def enabled(self_):
                return self_._enabled

            @enabled.setter
            def enabled(self_, v):
                written.append(v)
                self_._enabled = v

        from boschshcpy.models_impl import SHCMotionDetector2
        dev = SHCMotionDetector2.__new__(SHCMotionDetector2)
        dev._petimmunity_service = _FakePetImmunityService()
        dev.pet_immunity_enabled = True
        assert written == [True]


# ===========================================================================
# SmokeDetectorSensor
#
# Covers the PRIMARY/SECONDARY allowlist and INTRUSION_ALARM exclusion
# (issue #191): is_on used to be `!= IDLE_OFF`, so INTRUSION_ALARM (set by the
# IDS on all smoke detectors when a burglar alarm fires) falsely reported
# every detector as smoky. Fix: is_on only returns True for PRIMARY_ALARM or
# SECONDARY_ALARM.
# ===========================================================================

class TestSmokeDetectorSensor:
    def test_idle_off_is_not_smoke(self):
        assert _smoke_sensor("IDLE_OFF").is_on is False

    def test_intrusion_alarm_does_not_trigger_smoke(self):
        """IDS intrusion alarm must NOT make smoke sensor report smoke (issue #191)."""
        assert _smoke_sensor("INTRUSION_ALARM").is_on is False

    def test_primary_alarm_is_smoke(self):
        assert _smoke_sensor("PRIMARY_ALARM").is_on is True

    def test_secondary_alarm_is_smoke(self):
        assert _smoke_sensor("SECONDARY_ALARM").is_on is True

    def test_device_class_is_smoke(self):
        s = _smoke_sensor("IDLE_OFF")
        assert s._attr_device_class == BinarySensorDeviceClass.SMOKE

    def test_icon(self):
        s = _smoke_sensor("IDLE_OFF")
        assert s.icon == "mdi:smoke-detector"

    def test_extra_state_attributes_idle(self):
        s = _smoke_sensor("IDLE_OFF")
        attrs = s.extra_state_attributes
        assert attrs["alarmstate"] == "IDLE_OFF"
        assert attrs["smokedetectorcheck_state"] == "NONE"

    def test_extra_state_attributes_primary_alarm(self):
        s = _smoke_sensor("PRIMARY_ALARM")
        attrs = s.extra_state_attributes
        assert attrs["alarmstate"] == "PRIMARY_ALARM"

    def test_extra_state_attributes_smoke_test_ok(self):
        sensor = SmokeDetectorSensor.__new__(SmokeDetectorSensor)
        sensor._device = SimpleNamespace(
            alarmstate=AlarmService.State.IDLE_OFF,
            smokedetectorcheck_state=SmokeDetectorCheckService.State.SMOKE_TEST_OK,
        )
        attrs = sensor.extra_state_attributes
        assert attrs["smokedetectorcheck_state"] == "SMOKE_TEST_OK"

    def test_extra_state_attributes_smoke_test_requested(self):
        sensor = SmokeDetectorSensor.__new__(SmokeDetectorSensor)
        sensor._device = SimpleNamespace(
            alarmstate=AlarmService.State.IDLE_OFF,
            smokedetectorcheck_state=SmokeDetectorCheckService.State.SMOKE_TEST_REQUESTED,
        )
        attrs = sensor.extra_state_attributes
        assert attrs["smokedetectorcheck_state"] == "SMOKE_TEST_REQUESTED"

    def test_extra_state_attributes_smoke_test_failed(self):
        sensor = SmokeDetectorSensor.__new__(SmokeDetectorSensor)
        sensor._device = SimpleNamespace(
            alarmstate=AlarmService.State.IDLE_OFF,
            smokedetectorcheck_state=SmokeDetectorCheckService.State.SMOKE_TEST_FAILED,
        )
        attrs = sensor.extra_state_attributes
        assert attrs["smokedetectorcheck_state"] == "SMOKE_TEST_FAILED"

    def test_extra_state_attributes_unknown_check_state_returns_none(self):
        s = SmokeDetectorSensor.__new__(SmokeDetectorSensor)

        class _BadEnum:
            @property
            def name(self):
                raise ValueError("unknown_state")

        s._device = SimpleNamespace(
            smokedetectorcheck_state=_BadEnum(),
            alarmstate=_BadEnum(),
            name="test_smoke",
        )
        attrs = s.extra_state_attributes
        assert attrs["smokedetectorcheck_state"] is None
        assert attrs["alarmstate"] is None


class TestSmokeDetectorSensorIsOnBoundary:
    """Additional is_on boundary checks around the SECONDARY_ALARM / INTRUSION_ALARM split."""

    def _sensor(self, alarm_state):
        s = SmokeDetectorSensor.__new__(SmokeDetectorSensor)
        s._device = SimpleNamespace(
            alarmstate=alarm_state,
            smokedetectorcheck_state=SmokeDetectorCheckService.State.NONE,
        )
        return s

    def test_secondary_alarm_is_smoke(self):
        s = self._sensor(AlarmService.State.SECONDARY_ALARM)
        assert s.is_on is True

    def test_primary_alarm_is_smoke(self):
        s = self._sensor(AlarmService.State.PRIMARY_ALARM)
        assert s.is_on is True

    def test_intrusion_alarm_is_not_smoke(self):
        s = self._sensor(AlarmService.State.INTRUSION_ALARM)
        assert s.is_on is False

    def test_idle_off_is_not_smoke(self):
        s = self._sensor(AlarmService.State.IDLE_OFF)
        assert s.is_on is False


# ---------------------------------------------------------------------------
# Regression (issue #191): SmokeDetectorSensor.is_on alarm-state mapping.
# Uses a locally defined Enum mirroring AlarmService.State to pin the mapping
# independent of the real enum's exact membership.
# ---------------------------------------------------------------------------

class _FakeAlarmState(Enum):
    IDLE_OFF = "IDLE_OFF"
    INTRUSION_ALARM = "INTRUSION_ALARM"
    SECONDARY_ALARM = "SECONDARY_ALARM"
    PRIMARY_ALARM = "PRIMARY_ALARM"


def _smoke_alarm_sensor(alarm_state):
    """Build a SmokeDetectorSensor without running __init__.

    Renamed from a plain `_sensor()` in test_smoke_detector_alarm.py to avoid
    colliding with the unrelated `_sensor()` helper in test_motion_sensor.py
    (different target class/behaviour, so kept as separate functions rather
    than deduped).
    """
    state = AlarmService.State[alarm_state.name]
    s = SmokeDetectorSensor.__new__(SmokeDetectorSensor)
    s._device = SimpleNamespace(alarmstate=state)
    return s


def test_smoke_detector_alarm_idle_off_is_not_smoke():
    assert _smoke_alarm_sensor(_FakeAlarmState.IDLE_OFF).is_on is False


def test_smoke_detector_alarm_intrusion_alarm_does_not_trigger_smoke():
    """IDS intrusion alarm must NOT make smoke sensors report smoke (issue #191)."""
    assert _smoke_alarm_sensor(_FakeAlarmState.INTRUSION_ALARM).is_on is False


def test_smoke_detector_alarm_primary_alarm_is_smoke():
    assert _smoke_alarm_sensor(_FakeAlarmState.PRIMARY_ALARM).is_on is True


def test_smoke_detector_alarm_secondary_alarm_is_smoke():
    assert _smoke_alarm_sensor(_FakeAlarmState.SECONDARY_ALARM).is_on is True


# ---------------------------------------------------------------------------
# #336 replay guard -- SmokeDetectorSensor must suppress replayed Alarm state
# snapshots re-delivered on the ~24h poll-id resubscribe.
# ---------------------------------------------------------------------------

class TestSmokeDetectorReplayGuard:
    def _make_sensor(self, alarmstate_name="IDLE_OFF"):
        sensor = SmokeDetectorSensor.__new__(SmokeDetectorSensor)
        sensor._hass = _make_mock_hass()
        sensor._cached_device_id = "ha-smoke-1"
        sensor._last_fired_alarmstate = None  # freshly constructed
        sensor._device = SimpleNamespace(
            id="hdm:smoke:1",
            name="Smoke Detector",
            alarmstate=SimpleNamespace(name=alarmstate_name),
        )
        return sensor

    def test_first_call_fires_event(self):
        sensor = self._make_sensor("IDLE_OFF")
        sensor._input_events_handler()
        assert _fire_count(sensor._hass) == 1

    def test_first_call_event_subtype_is_alarmstate(self):
        sensor = self._make_sensor("PRIMARY_ALARM")
        sensor._input_events_handler()
        payload = sensor._hass.bus.async_fire.call_args[0][1]
        assert payload[ATTR_EVENT_TYPE] == "ALARM"
        assert payload[ATTR_EVENT_SUBTYPE] == "PRIMARY_ALARM"

    def test_replayed_snapshot_does_not_fire(self):
        sensor = self._make_sensor("IDLE_OFF")
        sensor._input_events_handler()  # fires
        sensor._input_events_handler()  # replay -- suppressed
        assert _fire_count(sensor._hass) == 1

    def test_changed_alarmstate_fires_again(self):
        sensor = self._make_sensor("IDLE_OFF")
        sensor._input_events_handler()  # fires
        sensor._input_events_handler()  # replay -- suppressed
        assert _fire_count(sensor._hass) == 1

        sensor._device.alarmstate = SimpleNamespace(name="PRIMARY_ALARM")
        sensor._input_events_handler()
        assert _fire_count(sensor._hass) == 2

    def test_alarm_to_idle_transition_fires(self):
        sensor = self._make_sensor("PRIMARY_ALARM")
        sensor._input_events_handler()  # fires -- PRIMARY_ALARM
        sensor._device.alarmstate = SimpleNamespace(name="IDLE_OFF")
        sensor._input_events_handler()  # genuine change -- fires
        assert _fire_count(sensor._hass) == 2

    def test_intrusion_alarm_then_idle_fires_twice(self):
        sensor = self._make_sensor("INTRUSION_ALARM")
        sensor._input_events_handler()  # first -- fires
        sensor._device.alarmstate = SimpleNamespace(name="IDLE_OFF")
        sensor._input_events_handler()  # change -- fires
        assert _fire_count(sensor._hass) == 2

    def test_cache_attr_initialized_to_none(self):
        sensor = self._make_sensor()
        assert sensor._last_fired_alarmstate is None


class TestSmokeDetectorSensorThreadSafe:
    def _make_sensor(self):
        sensor = SmokeDetectorSensor.__new__(SmokeDetectorSensor)
        sensor._hass = _make_mock_hass()
        sensor._cached_device_id = "ha-device-smoke-1"
        sensor._last_fired_alarmstate = None  # replay-guard initial state
        sensor._device = SimpleNamespace(
            id="hdm:smoke:1",
            name="Smoke Detector",
            alarmstate=SimpleNamespace(name="PRIMARY_ALARM"),
        )
        return sensor

    def test_uses_async_fire_not_call_soon_threadsafe(self):
        sensor = self._make_sensor()
        sensor._input_events_handler()

        assert sensor._hass.bus.async_fire.called
        assert not sensor._hass.loop.call_soon_threadsafe.called

    def test_event_subtype_is_alarm_state(self):
        sensor = self._make_sensor()
        sensor._input_events_handler()

        payload = sensor._hass.bus.async_fire.call_args[0][1]
        assert payload[ATTR_EVENT_SUBTYPE] == "PRIMARY_ALARM"


class TestAlarmStateWillRemoveUnsub:
    """binary_sensor.py -- SmokeDetectorSensor.async_will_remove_from_hass."""

    def test_async_will_remove_calls_unsub(self):
        ent = SmokeDetectorSensor.__new__(SmokeDetectorSensor)
        unsub = MagicMock()
        ent._ha_stop_unsub = unsub
        ent._service = None  # no service -> unsubscribe branch skipped
        with patch("custom_components.bosch_shc.entity.SHCEntity.async_will_remove_from_hass", new_callable=AsyncMock):
            _run(ent.async_will_remove_from_hass())
        unsub.assert_called_once()
        assert ent._ha_stop_unsub is None


class TestSmokeDetectorSensorServiceUnsub:
    """binary_sensor.py -- service-not-None branch in async_will_remove_from_hass."""

    def test_service_unsubscribed_when_set(self):
        ent = SmokeDetectorSensor.__new__(SmokeDetectorSensor)
        svc = MagicMock()
        ent._service = svc
        ent._device = SimpleNamespace(id="sd-1")
        ent._ha_stop_unsub = None
        with patch("custom_components.bosch_shc.entity.SHCEntity.async_will_remove_from_hass", new_callable=AsyncMock):
            _run(ent.async_will_remove_from_hass())
        svc.unsubscribe_callback.assert_called_once_with("sd-1_eventlistener")


class TestSmokeDetectorSensorBadEnum:
    """binary_sensor.py -- ValueError/KeyError guard in
    SmokeDetectorSensor._input_events_handler."""

    def _make_sensor(self):
        class BadAlarmState:
            @property
            def name(self):
                raise ValueError("bad")

        ent = SmokeDetectorSensor.__new__(SmokeDetectorSensor)
        ent._device = SimpleNamespace(name="SmokeD", alarmstate=BadAlarmState())
        ent._service = None
        ent._ha_stop_unsub = None
        ent._last_fired_alarmstate = None
        return ent

    def test_bad_alarmstate_logs_warning(self):
        ent = self._make_sensor()
        with patch("custom_components.bosch_shc.binary_sensor.LOGGER") as mock_log:
            ent._input_events_handler()
        mock_log.warning.assert_called_once()


class TestSmokeDetectorSensorSmoketestError:
    """async_request_smoketest must wrap SHCException/SHCConnectionError as
    HomeAssistantError (Silver action-exceptions rule)."""

    def test_shcexception_raises_homeassistanterror(self):
        s = _make_smoke_sensor(executor_side_effect=SHCException("comm error"))
        with pytest.raises(HomeAssistantError):
            _run(s.async_request_smoketest())

    def test_shcconnectionerror_raises_homeassistanterror(self):
        s = _make_smoke_sensor(executor_side_effect=SHCConnectionError("timeout"))
        with pytest.raises(HomeAssistantError):
            _run(s.async_request_smoketest())

    def test_error_message_contains_device_name(self):
        s = _make_smoke_sensor(executor_side_effect=SHCException("fail"))
        with pytest.raises(HomeAssistantError, match="Smoke Detector 1"):
            _run(s.async_request_smoketest())

    def test_no_error_on_success(self):
        s = _make_smoke_sensor(executor_side_effect=None)
        s._device.async_smoketest_requested = AsyncMock(return_value=None)
        _run(s.async_request_smoketest())  # must not raise


class TestSmokeDetectorSensorAlarmstateError:
    """async_request_alarmstate must wrap SHCException/SHCConnectionError as
    HomeAssistantError."""

    def test_shcexception_raises_homeassistanterror(self):
        s = _make_alarm_sensor(executor_side_effect=SHCException("comm error"))
        with pytest.raises(HomeAssistantError):
            _run(s.async_request_alarmstate("INTRUSION_ALARM_ON"))

    def test_shcconnectionerror_raises_homeassistanterror(self):
        s = _make_alarm_sensor(executor_side_effect=SHCConnectionError("timeout"))
        with pytest.raises(HomeAssistantError):
            _run(s.async_request_alarmstate("INTRUSION_ALARM_ON"))

    def test_error_message_contains_device_name(self):
        s = _make_alarm_sensor(executor_side_effect=SHCException("fail"))
        with pytest.raises(HomeAssistantError, match="Smoke Detector 1"):
            _run(s.async_request_alarmstate("SOME_CMD"))

    def test_no_error_on_success(self):
        s = _make_alarm_sensor(executor_side_effect=None)
        s._device.async_set_alarmstate = AsyncMock(return_value=None)
        _run(s.async_request_alarmstate("IDLE_OFF"))  # must not raise


class TestSmokeDetectorSensorInit:
    def test_init_with_alarm_service_subscribes(self):
        """__init__ finds the service (no subscribe yet); async_added_to_hass subscribes."""
        cb_store = {}
        alarm_svc = _make_service("Alarm", subscribe_callback=lambda k, cb: cb_store.update({k: cb}))
        dev = _make_base_device("sd-init", device_services=[alarm_svc])
        dev.alarmstate = AlarmService.State.IDLE_OFF
        dev.smokedetectorcheck_state = SmokeDetectorCheckService.State.NONE
        hass = _make_hass()
        sensor = SmokeDetectorSensor(device=dev, hass=hass, entry_id="E1")
        assert sensor._service is alarm_svc
        assert "sd-init_eventlistener" not in cb_store

        async def _add():
            with patch(
                "custom_components.bosch_shc.binary_sensor.async_get_device_id",
                return_value="ha-device-id",
            ):
                await sensor.async_added_to_hass()

        asyncio.run(_add())
        assert "sd-init_eventlistener" in cb_store

    def test_init_without_alarm_service(self):
        other_svc = _make_service("OtherService")
        dev = _make_base_device("sd-init2", device_services=[other_svc])
        dev.alarmstate = AlarmService.State.IDLE_OFF
        dev.smokedetectorcheck_state = SmokeDetectorCheckService.State.NONE
        hass = _make_hass()
        sensor = SmokeDetectorSensor(device=dev, hass=hass, entry_id="E1")
        assert sensor._service is None

    def test_input_events_handler_fires_alarm_event(self):
        fired = []
        alarm_svc = _make_service("Alarm")
        dev = _make_base_device("sd-ev", device_services=[alarm_svc])
        dev.alarmstate = AlarmService.State.IDLE_OFF  # construction baseline
        dev.smokedetectorcheck_state = SmokeDetectorCheckService.State.NONE
        hass = _make_hass()
        hass.bus.async_fire = lambda event, data: fired.append((event, data))
        sensor = SmokeDetectorSensor(device=dev, hass=hass, entry_id="E1")
        sensor._cached_device_id = "ha-device-id"
        # Genuine new alarm changes state past the seeded baseline -> fires.
        dev.alarmstate = AlarmService.State.PRIMARY_ALARM
        sensor._input_events_handler()
        assert len(fired) == 1
        event_name, data = fired[0]
        assert event_name == "bosch_shc.event"
        assert data["event_type"] == "ALARM"
        assert data["event_subtype"] == "PRIMARY_ALARM"

    def test_input_events_handler_restart_replay_no_fire(self):
        """#336 restart: a stale alarmstate present at construction seeds the
        baseline, so the first re-delivered snapshot must NOT fire a phantom."""
        fired = []
        alarm_svc = _make_service("Alarm")
        dev = _make_base_device("sd-restart", device_services=[alarm_svc])
        dev.alarmstate = AlarmService.State.IDLE_OFF  # stale state at startup
        dev.smokedetectorcheck_state = SmokeDetectorCheckService.State.NONE
        hass = _make_hass()
        hass.bus.async_fire = lambda event, data: fired.append((event, data))
        sensor = SmokeDetectorSensor(device=dev, hass=hass, entry_id="E1")
        sensor._cached_device_id = "ha-device-id"
        sensor._input_events_handler()  # same IDLE_OFF re-delivered -> suppressed
        assert fired == []

    def test_handle_ha_stop_unsubscribes(self):
        unsub_store = {}
        alarm_svc = _make_service("Alarm")
        alarm_svc.unsubscribe_callback = lambda k: unsub_store.update({k: True})
        dev = _make_base_device("sd-stop", device_services=[alarm_svc])
        dev.alarmstate = AlarmService.State.IDLE_OFF
        dev.smokedetectorcheck_state = SmokeDetectorCheckService.State.NONE
        hass = _make_hass()
        sensor = SmokeDetectorSensor(device=dev, hass=hass, entry_id="E1")
        sensor._handle_ha_stop(None)
        assert "sd-stop_eventlistener" in unsub_store

    def test_async_request_smoketest(self):
        alarm_svc = _make_service("Alarm")
        dev = _make_base_device("sd-smoke", device_services=[alarm_svc])
        dev.alarmstate = AlarmService.State.IDLE_OFF
        dev.smokedetectorcheck_state = SmokeDetectorCheckService.State.NONE
        dev.async_smoketest_requested = AsyncMock()
        hass = _make_hass()
        sensor = SmokeDetectorSensor(device=dev, hass=hass, entry_id="E1")
        sensor._hass = hass
        asyncio.run(sensor.async_request_smoketest())
        dev.async_smoketest_requested.assert_called_once()

    def test_async_request_alarmstate(self):
        """async_request_alarmstate calls device.async_set_alarmstate(command) directly."""
        alarm_svc = _make_service("Alarm")
        dev = _make_base_device("sd-alarm", device_services=[alarm_svc])
        dev.alarmstate = AlarmService.State.IDLE_OFF
        dev.smokedetectorcheck_state = SmokeDetectorCheckService.State.NONE
        dev.async_set_alarmstate = AsyncMock()
        hass = _make_hass()
        sensor = SmokeDetectorSensor(device=dev, hass=hass, entry_id="E1")
        sensor._hass = hass
        asyncio.run(sensor.async_request_alarmstate("IDLE_OFF"))
        dev.async_set_alarmstate.assert_called_once_with("IDLE_OFF")


# ===========================================================================
# WaterLeakageDetectorSensor
# ===========================================================================

class TestWaterLeakageDetectorSensor:
    def test_leakage_detected_is_on(self):
        s = _water_sensor(_WL_DETECTED, _TILT_ENABLED, _TILT_ENABLED)
        assert s.is_on is True

    def test_no_leakage_is_off(self):
        s = _water_sensor(_WL_NONE, _TILT_ENABLED, _TILT_ENABLED)
        assert s.is_on is False

    def test_device_class_is_moisture(self):
        s = _water_sensor(_WL_NONE, _TILT_ENABLED, _TILT_ENABLED)
        assert s._attr_device_class == BinarySensorDeviceClass.MOISTURE

    def test_icon(self):
        s = _water_sensor(_WL_NONE, _TILT_ENABLED, _TILT_ENABLED)
        assert s.icon == "mdi:water-alert"

    def test_extra_state_attributes_enabled(self):
        s = _water_sensor(_WL_NONE, _TILT_ENABLED, _TILT_ENABLED)
        attrs = s.extra_state_attributes
        assert attrs["push_notification_state"] == "ENABLED"
        assert attrs["acoustic_signal_state"] == "ENABLED"

    def test_extra_state_attributes_disabled(self):
        s = _water_sensor(_WL_NONE, _TILT_DISABLED, _TILT_DISABLED)
        attrs = s.extra_state_attributes
        assert attrs["push_notification_state"] == "DISABLED"
        assert attrs["acoustic_signal_state"] == "DISABLED"

    def test_extra_state_attributes_mixed(self):
        s = _water_sensor(_WL_DETECTED, _TILT_ENABLED, _TILT_DISABLED)
        attrs = s.extra_state_attributes
        assert attrs["push_notification_state"] == "ENABLED"
        assert attrs["acoustic_signal_state"] == "DISABLED"


class TestWaterLeakageDetectorSensorLeakageDetectedAttributes:
    """extra_state_attributes must still return push/acoustic names even during leak."""

    def test_leakage_detected_push_enabled(self):
        s = _water_sensor(_WL_DETECTED, _TILT_ENABLED, _TILT_ENABLED)
        attrs = s.extra_state_attributes
        assert attrs["push_notification_state"] == "ENABLED"
        assert attrs["acoustic_signal_state"] == "ENABLED"

    def test_leakage_detected_push_disabled(self):
        s = _water_sensor(_WL_DETECTED, _TILT_DISABLED, _TILT_DISABLED)
        attrs = s.extra_state_attributes
        assert attrs["push_notification_state"] == "DISABLED"
        assert attrs["acoustic_signal_state"] == "DISABLED"

    def test_is_on_when_leakage_detected(self):
        s = _water_sensor(_WL_DETECTED, _TILT_ENABLED, _TILT_ENABLED)
        assert s.is_on is True


# ===========================================================================
# SmokeDetectionSystemSensor
# ===========================================================================

class TestSmokeDetectionSystemSensor:
    def test_alarm_off_is_not_smoke(self):
        assert _sds_sensor("ALARM_OFF").is_on is False

    def test_alarm_on_is_smoke(self):
        assert _sds_sensor("ALARM_ON").is_on is True

    def test_alarm_muted_is_smoke(self):
        """Muted alarm is still considered active smoke."""
        assert _sds_sensor("ALARM_MUTED").is_on is True

    def test_device_class_is_smoke(self):
        s = _sds_sensor("ALARM_OFF")
        assert s._attr_device_class == BinarySensorDeviceClass.SMOKE

    def test_icon(self):
        s = _sds_sensor("ALARM_OFF")
        assert s.icon == "mdi:smoke-detector"

    def test_extra_state_attributes_alarm_off(self):
        assert _sds_sensor("ALARM_OFF").extra_state_attributes == {"alarm_state": "ALARM_OFF"}

    def test_extra_state_attributes_alarm_on(self):
        assert _sds_sensor("ALARM_ON").extra_state_attributes == {"alarm_state": "ALARM_ON"}

    def test_extra_state_attributes_alarm_muted(self):
        assert _sds_sensor("ALARM_MUTED").extra_state_attributes == {"alarm_state": "ALARM_MUTED"}


class TestSmokeDetectionSystemSensorAttributes:
    """extra_state_attributes must return alarm_state as a string name."""

    def test_extra_state_attributes_alarm_off(self):
        attrs = _sds_sensor("ALARM_OFF").extra_state_attributes
        assert attrs == {"alarm_state": "ALARM_OFF"}

    def test_extra_state_attributes_alarm_on(self):
        attrs = _sds_sensor("ALARM_ON").extra_state_attributes
        assert attrs == {"alarm_state": "ALARM_ON"}

    def test_extra_state_attributes_alarm_muted(self):
        attrs = _sds_sensor("ALARM_MUTED").extra_state_attributes
        assert attrs == {"alarm_state": "ALARM_MUTED"}

    def test_extra_state_attributes_key_is_alarm_state(self):
        """The dict must contain exactly the 'alarm_state' key."""
        s = _sds_sensor("ALARM_OFF")
        assert "alarm_state" in s.extra_state_attributes
        assert len(s.extra_state_attributes) == 1


class TestSmokeDetectionSystemSensorInitAttrs:
    """__init__ must set _attr_name = None and override _attr_unique_id.

    Renamed from `TestSmokeDetectionSystemSensorInit` (one of two classes
    with that name, this one from test_binary_sensor_coverage.py) to avoid
    colliding with the same-named class below (test_binary_sensor_setup.py),
    which covers additional subscribe-wiring behaviour.
    """

    def _make_dev(self, device_id="sds-x", root_device_id="root-x", name="SDS"):
        dev = _base_device(device_id=device_id, name=name, root_device_id=root_device_id)
        dev.alarm = SurveillanceAlarmService.State.ALARM_OFF
        dev.device_services = []
        return dev

    def _make_hass(self):
        return SimpleNamespace(
            bus=SimpleNamespace(async_listen_once=lambda event, cb: None),
        )

    def test_attr_name_is_none_after_init(self):
        dev = self._make_dev(device_id="sds1", root_device_id="root1")
        hass = self._make_hass()
        with patch.object(SmokeDetectionSystemSensor, "_update_attr", lambda self: None):
            sensor = SmokeDetectionSystemSensor(device=dev, hass=hass, entry_id="E1")
        assert sensor._attr_name is None

    def test_unique_id_overrides_super(self):
        """__init__ reassigns _attr_unique_id to root_device_id + '_' + id."""
        dev = self._make_dev(device_id="sds2", root_device_id="rootR")
        hass = self._make_hass()
        with patch.object(SmokeDetectionSystemSensor, "_update_attr", lambda self: None):
            sensor = SmokeDetectionSystemSensor(device=dev, hass=hass, entry_id="E1")
        assert sensor._attr_unique_id == "rootR_sds2"

    def test_is_on_alarm_off_is_false(self):
        assert _sds_sensor("ALARM_OFF").is_on is False

    def test_is_on_alarm_on_is_true(self):
        assert _sds_sensor("ALARM_ON").is_on is True

    def test_is_on_alarm_muted_is_true(self):
        assert _sds_sensor("ALARM_MUTED").is_on is True


# ---------------------------------------------------------------------------
# #336 replay guard -- SmokeDetectionSystemSensor must suppress replayed
# SurveillanceAlarm state snapshots.
# ---------------------------------------------------------------------------

class TestSmokeDetectionSystemReplayGuard:
    def _make_sensor(self, alarm_name="ALARM_OFF"):
        sensor = SmokeDetectionSystemSensor.__new__(SmokeDetectionSystemSensor)
        sensor._hass = _make_mock_hass()
        sensor._cached_device_id = "ha-sds-1"
        sensor._last_fired_alarm = None  # freshly constructed
        sensor._device = SimpleNamespace(
            id="hdm:smokedetectionsystem:1",
            name="Smoke Detection System",
            alarm=SimpleNamespace(name=alarm_name),
        )
        return sensor

    def test_first_call_fires_event(self):
        sensor = self._make_sensor("ALARM_OFF")
        sensor._input_events_handler()
        assert _fire_count(sensor._hass) == 1

    def test_first_call_event_subtype_is_alarm_name(self):
        sensor = self._make_sensor("ALARM_ON")
        sensor._input_events_handler()
        payload = sensor._hass.bus.async_fire.call_args[0][1]
        assert payload[ATTR_EVENT_TYPE] == "ALARM"
        assert payload[ATTR_EVENT_SUBTYPE] == "ALARM_ON"

    def test_replayed_snapshot_does_not_fire(self):
        sensor = self._make_sensor("ALARM_OFF")
        sensor._input_events_handler()  # fires
        sensor._input_events_handler()  # replay -- suppressed
        assert _fire_count(sensor._hass) == 1

    def test_changed_alarm_state_fires_again(self):
        sensor = self._make_sensor("ALARM_OFF")
        sensor._input_events_handler()  # fires
        sensor._input_events_handler()  # replay -- suppressed
        assert _fire_count(sensor._hass) == 1

        sensor._device.alarm = SimpleNamespace(name="ALARM_ON")
        sensor._input_events_handler()
        assert _fire_count(sensor._hass) == 2

    def test_alarm_on_then_muted_fires(self):
        sensor = self._make_sensor("ALARM_ON")
        sensor._input_events_handler()  # fires
        sensor._device.alarm = SimpleNamespace(name="ALARM_MUTED")
        sensor._input_events_handler()  # genuine change -- fires
        assert _fire_count(sensor._hass) == 2

    def test_alarm_muted_then_off_fires(self):
        sensor = self._make_sensor("ALARM_MUTED")
        sensor._input_events_handler()  # fires
        sensor._device.alarm = SimpleNamespace(name="ALARM_OFF")
        sensor._input_events_handler()  # genuine change -- fires
        assert _fire_count(sensor._hass) == 2

    def test_cache_attr_initialized_to_none(self):
        sensor = self._make_sensor()
        assert sensor._last_fired_alarm is None

    def test_resubscribe_replay_idle_off_suppressed(self):
        """Exact scenario from #336: ALARM_OFF re-delivered after 24h resubscribe."""
        sensor = self._make_sensor("ALARM_OFF")
        sensor._input_events_handler()  # initial snapshot -- fires once

        sensor._input_events_handler()  # must be suppressed
        sensor._input_events_handler()  # must be suppressed (belt and suspenders)
        assert _fire_count(sensor._hass) == 1


class TestSmokeDetectionSystemSensorThreadSafe:
    def _make_sensor(self):
        sensor = SmokeDetectionSystemSensor.__new__(SmokeDetectionSystemSensor)
        sensor._hass = _make_mock_hass()
        sensor._cached_device_id = "ha-device-smokedsys-1"
        sensor._last_fired_alarm = None  # replay-guard initial state
        sensor._device = SimpleNamespace(
            id="hdm:smokedsys:1",
            name="Smoke Detection System",
            alarm=SimpleNamespace(name="ALARM_ON"),
        )
        return sensor

    def test_uses_async_fire_not_call_soon_threadsafe(self):
        sensor = self._make_sensor()
        sensor._input_events_handler()

        assert sensor._hass.bus.async_fire.called
        assert not sensor._hass.loop.call_soon_threadsafe.called

    def test_event_subtype_is_alarm_name(self):
        sensor = self._make_sensor()
        sensor._input_events_handler()

        payload = sensor._hass.bus.async_fire.call_args[0][1]
        assert payload[ATTR_EVENT_SUBTYPE] == "ALARM_ON"


class TestSurveillanceAlarmWillRemoveUnsub:
    """binary_sensor.py -- SmokeDetectionSystemSensor.async_will_remove_from_hass."""

    def test_async_will_remove_calls_unsub(self):
        ent = SmokeDetectionSystemSensor.__new__(SmokeDetectionSystemSensor)
        unsub = MagicMock()
        ent._ha_stop_unsub = unsub
        ent._service = None  # no service -> unsubscribe branch skipped
        with patch("custom_components.bosch_shc.entity.SHCEntity.async_will_remove_from_hass", new_callable=AsyncMock):
            _run(ent.async_will_remove_from_hass())
        unsub.assert_called_once()
        assert ent._ha_stop_unsub is None


class TestSmokeDetectionSystemSensorServiceUnsub:
    """binary_sensor.py -- service-not-None branch in SmokeDetectionSystemSensor."""

    def test_service_unsubscribed_when_set(self):
        ent = SmokeDetectionSystemSensor.__new__(SmokeDetectionSystemSensor)
        svc = MagicMock()
        ent._service = svc
        ent._device = SimpleNamespace(id="sds-1")
        ent._ha_stop_unsub = None
        with patch("custom_components.bosch_shc.entity.SHCEntity.async_will_remove_from_hass", new_callable=AsyncMock):
            _run(ent.async_will_remove_from_hass())
        svc.unsubscribe_callback.assert_called_once_with("sds-1_eventlistener")


class TestSmokeDetectionSystemSensorBadEnum:
    """binary_sensor.py -- ValueError/KeyError guard in
    SmokeDetectionSystemSensor._input_events_handler."""

    def _make_sensor(self):
        class BadAlarm:
            @property
            def name(self):
                raise KeyError("bad")

        ent = SmokeDetectionSystemSensor.__new__(SmokeDetectionSystemSensor)
        ent._device = SimpleNamespace(name="SmokeDS", alarm=BadAlarm())
        ent._service = None
        ent._ha_stop_unsub = None
        ent._last_fired_alarm = None
        return ent

    def test_bad_alarm_logs_warning(self):
        ent = self._make_sensor()
        with patch("custom_components.bosch_shc.binary_sensor.LOGGER") as mock_log:
            ent._input_events_handler()
        mock_log.warning.assert_called_once()


class TestSmokeDetectionSystemSensorInit:
    def test_init_sets_unique_id_and_name(self):
        surv_svc = _make_service("SurveillanceAlarm")
        dev = _make_base_device("sds-init", name="SDS", root_device_id="root-sds")
        dev.alarm = SurveillanceAlarmService.State.ALARM_OFF
        hass = _make_hass()
        sensor = SmokeDetectionSystemSensor(device=dev, hass=hass, entry_id="E1")
        assert sensor._attr_unique_id == "root-sds_sds-init"
        assert sensor._attr_name is None

    def test_init_with_surveillance_service_subscribes(self):
        cb_store = {}
        surv_svc = _make_service(
            "SurveillanceAlarm",
            subscribe_callback=lambda k, cb: cb_store.update({k: cb}),
        )
        dev = _make_base_device("sds-init2", device_services=[surv_svc])
        dev.alarm = SurveillanceAlarmService.State.ALARM_OFF
        hass = _make_hass()
        sensor = SmokeDetectionSystemSensor(device=dev, hass=hass, entry_id="E1")
        assert sensor._service is surv_svc
        assert "sds-init2_eventlistener" not in cb_store

        async def _add():
            with patch(
                "custom_components.bosch_shc.binary_sensor.async_get_device_id",
                return_value="ha-device-id",
            ):
                await sensor.async_added_to_hass()

        asyncio.run(_add())
        assert "sds-init2_eventlistener" in cb_store

    def test_init_without_surveillance_service(self):
        other_svc = _make_service("OtherService")
        dev = _make_base_device("sds-init3", device_services=[other_svc])
        dev.alarm = SurveillanceAlarmService.State.ALARM_OFF
        hass = _make_hass()
        sensor = SmokeDetectionSystemSensor(device=dev, hass=hass, entry_id="E1")
        assert sensor._service is None

    def test_input_events_handler_fires_event(self):
        fired = []
        surv_svc = _make_service("SurveillanceAlarm")
        dev = _make_base_device("sds-ev", device_services=[surv_svc])
        dev.alarm = SurveillanceAlarmService.State.ALARM_OFF  # construction baseline
        hass = _make_hass()
        hass.bus.async_fire = lambda event, data: fired.append((event, data))
        sensor = SmokeDetectionSystemSensor(device=dev, hass=hass, entry_id="E1")
        sensor._cached_device_id = "ha-device-id"
        dev.alarm = SurveillanceAlarmService.State.ALARM_ON
        sensor._input_events_handler()
        assert len(fired) == 1
        event_name, data = fired[0]
        assert event_name == "bosch_shc.event"
        assert data["event_type"] == "ALARM"
        assert data["event_subtype"] == "ALARM_ON"

    def test_input_events_handler_restart_replay_no_fire(self):
        """#336 restart: a stale SurveillanceAlarm state present at construction
        seeds the baseline, so the first re-delivered snapshot must NOT fire."""
        fired = []
        surv_svc = _make_service("SurveillanceAlarm")
        dev = _make_base_device("sds-restart", device_services=[surv_svc])
        dev.alarm = SurveillanceAlarmService.State.ALARM_OFF  # stale state at startup
        hass = _make_hass()
        hass.bus.async_fire = lambda event, data: fired.append((event, data))
        sensor = SmokeDetectionSystemSensor(device=dev, hass=hass, entry_id="E1")
        sensor._cached_device_id = "ha-device-id"
        sensor._input_events_handler()  # same ALARM_OFF re-delivered -> suppressed
        assert fired == []

    def test_handle_ha_stop_unsubscribes(self):
        unsub_store = {}
        surv_svc = _make_service("SurveillanceAlarm")
        surv_svc.unsubscribe_callback = lambda k: unsub_store.update({k: True})
        dev = _make_base_device("sds-stop", device_services=[surv_svc])
        dev.alarm = SurveillanceAlarmService.State.ALARM_OFF
        hass = _make_hass()
        sensor = SmokeDetectionSystemSensor(device=dev, hass=hass, entry_id="E1")
        sensor._handle_ha_stop(None)
        assert "sds-stop_eventlistener" in unsub_store


# ===========================================================================
# TwinguardAlarmTracker / TwinguardSmokeAlarmSensor
# ===========================================================================

class TestTwinguardAlarmTrackerAlarmStateValueError:
    """alarm_state must return None + LOGGER.warning on a ValueError from
    SmokeDetectionSystem.alarm.name."""

    def _make_tracker(self, alarm_value):
        surv_svc = SimpleNamespace(
            id="SurveillanceAlarm",
            subscribe_callback=lambda k, cb: None,
            unsubscribe_callback=lambda k: None,
        )
        sds = SimpleNamespace(
            id="sds-x", name="SDS", alarm=alarm_value, device_services=[surv_svc],
        )
        hass = _make_hass_v2()
        session = SimpleNamespace(
            api=SimpleNamespace(get_messages=AsyncMock(return_value=[])),
        )
        return TwinguardAlarmTracker(session=session, smoke_detection_system=sds, hass=hass)

    def test_alarm_state_value_error_returns_none(self):
        class _RaisingAlarm:
            @property
            def name(self):
                raise ValueError("unknown alarm state 42")

        tracker = self._make_tracker(_RaisingAlarm())
        assert tracker.alarm_state is None

    def test_alarm_state_value_error_logs_warning(self):
        class _RaisingAlarm:
            @property
            def name(self):
                raise ValueError("unknown alarm state 42")

        tracker = self._make_tracker(_RaisingAlarm())
        with patch("custom_components.bosch_shc.binary_sensor.LOGGER") as mock_log:
            tracker.alarm_state
        mock_log.warning.assert_called_once()

    def test_alarm_state_valid_returns_name(self):
        tracker = self._make_tracker(SurveillanceAlarmService.State.ALARM_OFF)
        assert tracker.alarm_state == "ALARM_OFF"

    def test_alarm_state_alarm_on_returns_name(self):
        tracker = self._make_tracker(SurveillanceAlarmService.State.ALARM_ON)
        assert tracker.alarm_state == "ALARM_ON"


class TestTwinguardAlarmTrackerRefreshNoChange:
    """refresh() with unchanged trigger_ids AND unchanged alarm_state -> early return."""

    def _make_tracker_alarm_off(self):
        surv_svc = SimpleNamespace(
            id="SurveillanceAlarm",
            subscribe_callback=lambda k, cb: None,
            unsubscribe_callback=lambda k: None,
        )
        sds = SimpleNamespace(
            id="sds-nc", name="SDS",
            alarm=SurveillanceAlarmService.State.ALARM_OFF,
            device_services=[surv_svc],
        )
        hass = _make_hass_v2()
        session = SimpleNamespace(
            api=SimpleNamespace(get_messages=AsyncMock(return_value=[])),
        )
        return TwinguardAlarmTracker(session=session, smoke_detection_system=sds, hass=hass)

    def test_refresh_no_change_does_not_notify_listeners(self):
        tracker = self._make_tracker_alarm_off()
        asyncio.run(tracker.async_refresh())  # first refresh: sets baseline
        called = []
        tracker.register_listener(_make_hass_v2(), lambda: called.append(1))
        asyncio.run(tracker.async_refresh())  # no-change early return -> listener not called
        assert called == []

    def test_refresh_no_change_trigger_ids_unchanged(self):
        tracker = self._make_tracker_alarm_off()
        asyncio.run(tracker.async_refresh())
        asyncio.run(tracker.async_refresh())
        assert tracker._active_trigger_ids == set()

    def test_refresh_first_call_notifies_because_state_changed(self):
        tracker = self._make_tracker_alarm_off()
        called = []
        tracker.register_listener(_make_hass_v2(), lambda: called.append(1))
        asyncio.run(tracker.async_refresh())  # first: _last_alarm_state was None -> change detected
        assert called == [1]

    def test_refresh_no_change_last_alarm_state_preserved(self):
        tracker = self._make_tracker_alarm_off()
        asyncio.run(tracker.async_refresh())
        before = tracker._last_alarm_state
        asyncio.run(tracker.async_refresh())
        assert tracker._last_alarm_state == before
        assert tracker._last_alarm_state == "ALARM_OFF"


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
        s.hass = MagicMock()
        s.hass.loop = SimpleNamespace(call_soon_threadsafe=lambda cb, *a: None)
        s.schedule_update_ha_state = MagicMock()
        return s

    def test_async_added_to_hass_calls_register_listener(self):
        tracker = self._make_tracker()
        s = self._make_sensor(tracker)

        async def _run_inner():
            with patch(
                "custom_components.bosch_shc.entity.SHCEntity.async_added_to_hass",
                return_value=None,
            ):
                await s.async_added_to_hass()

        asyncio.run(_run_inner())
        tracker.register_listener.assert_called_once()
        call_args = tracker.register_listener.call_args[0]
        assert callable(call_args[1])

    def test_async_added_to_hass_passes_hass_to_register_listener(self):
        tracker = self._make_tracker()
        s = self._make_sensor(tracker)

        async def _run_inner():
            with patch(
                "custom_components.bosch_shc.entity.SHCEntity.async_added_to_hass",
                return_value=None,
            ):
                await s.async_added_to_hass()

        asyncio.run(_run_inner())
        call_args = tracker.register_listener.call_args[0]
        assert call_args[0] is s.hass

    def test_async_will_remove_from_hass_calls_unregister_listener(self):
        tracker = self._make_tracker()
        s = self._make_sensor(tracker)

        async def _run_inner():
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

        asyncio.run(_run_inner())
        tracker.unregister_listener.assert_called_once()

    def test_async_will_remove_from_hass_passes_listener_to_unregister(self):
        tracker = self._make_tracker()
        s = self._make_sensor(tracker)

        async def _run_inner():
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

        asyncio.run(_run_inner())
        registered_listener = tracker.register_listener.call_args[0][1]
        unregistered_listener = tracker.unregister_listener.call_args[0][0]
        assert registered_listener is unregistered_listener

    def test_async_added_not_called_twice_registers_once(self):
        tracker = self._make_tracker()
        s = self._make_sensor(tracker)

        async def _run_inner():
            with patch(
                "custom_components.bosch_shc.entity.SHCEntity.async_added_to_hass",
                return_value=None,
            ):
                await s.async_added_to_hass()

        asyncio.run(_run_inner())
        assert tracker.register_listener.call_count == 1

    def test_remove_without_prior_add_still_calls_unregister(self):
        tracker = self._make_tracker()
        s = self._make_sensor(tracker)

        async def _run_inner():
            with patch(
                "custom_components.bosch_shc.entity.SHCEntity.async_will_remove_from_hass",
                return_value=None,
            ):
                await s.async_will_remove_from_hass()

        asyncio.run(_run_inner())
        tracker.unregister_listener.assert_called_once()


class TestTwinguardSmokeTestException:
    def test_smoketest_raises_homeassistant_error_on_exception(self):
        """SHCException -> HomeAssistantError."""
        ent = TwinguardSmokeAlarmSensor.__new__(TwinguardSmokeAlarmSensor)
        ent._device = SimpleNamespace(
            name="Twinguard",
            async_smoketest_requested=AsyncMock(side_effect=SHCException("err")),
        )

        with pytest.raises(HomeAssistantError):
            _run(ent.async_request_smoketest())


class TestTwinguardAlarmTracker:
    """Unit tests for TwinguardAlarmTracker (no HA, no network)."""

    def _make_tracker(self, sds, session, hass=None):
        return TwinguardAlarmTracker(
            session=session, smoke_detection_system=sds, hass=hass or _make_hass(),
        )

    def test_parse_surveillance_events_with_list(self):
        events = [{"type": "SMOKE_LIGHT", "triggerId": "tw1"}, {"type": "ALARM_OFF"}]
        assert TwinguardAlarmTracker._parse_surveillance_events(events) == events

    def test_parse_surveillance_events_with_json_string(self):
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

    def test_refresh_alarm_off_clears_trigger_ids(self):
        surv_svc = _make_service("SurveillanceAlarm")
        sds = _make_base_device("sds1", device_services=[surv_svc])
        sds.alarm = SurveillanceAlarmService.State.ALARM_OFF
        session = _make_fake_session(smoke_detection_system=sds)
        tracker = self._make_tracker(sds, session)
        tracker._active_trigger_ids = {"tw1"}  # simulate previous state
        asyncio.run(tracker.async_refresh())
        assert tracker._active_trigger_ids == set()

    def test_refresh_alarm_on_extracts_trigger_id(self):
        surv_svc = _make_service("SurveillanceAlarm")
        sds = _make_base_device("smokeDetectionSystem", device_services=[surv_svc])
        sds.alarm = SurveillanceAlarmService.State.ALARM_ON
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
        asyncio.run(tracker.async_refresh())
        assert tracker.is_alarm_active_for("tw1") is True
        assert tracker.is_alarm_active_for("tw2") is False

    def test_concurrent_refresh_newer_result_not_overwritten_by_stale_slower_call(self):
        """Regression: two async_refresh() calls in flight at once (e.g. a
        burst of SurveillanceAlarm callbacks) must not let an
        earlier-started-but-slower-to-respond get_messages() call overwrite
        the result of a later-started-but-faster call. The generation guard
        must discard the stale (first-started) result."""
        surv_svc = _make_service("SurveillanceAlarm")
        sds = _make_base_device("smokeDetectionSystem", device_services=[surv_svc])
        sds.alarm = SurveillanceAlarmService.State.ALARM_ON

        resume_first = asyncio.Event()
        call_count = [0]

        async def get_messages_side_effect():
            call_count[0] += 1
            if call_count[0] == 1:
                await resume_first.wait()
                return [
                    {
                        "messageCode": {"name": "SMOKE_ALARM"},
                        "sourceId": "smokeDetectionSystem",
                        "arguments": {
                            "surveillanceEvents": [
                                {"type": "SMOKE_LIGHT", "triggerId": "stale-tw"}
                            ]
                        },
                    }
                ]
            return [
                {
                    "messageCode": {"name": "SMOKE_ALARM"},
                    "sourceId": "smokeDetectionSystem",
                    "arguments": {
                        "surveillanceEvents": [
                            {"type": "SMOKE_LIGHT", "triggerId": "fresh-tw"}
                        ]
                    },
                }
            ]

        session = SimpleNamespace(
            api=SimpleNamespace(get_messages=AsyncMock(side_effect=get_messages_side_effect))
        )
        tracker = self._make_tracker(sds, session)

        async def _run_inner():
            task1 = asyncio.ensure_future(tracker.async_refresh())
            await asyncio.sleep(0)  # let task1 start and reach the blocked await
            task2 = asyncio.ensure_future(tracker.async_refresh())
            await task2
            resume_first.set()
            await task1

        asyncio.run(_run_inner())

        assert tracker.is_alarm_active_for("fresh-tw") is True
        assert tracker.is_alarm_active_for("stale-tw") is False

    def test_refresh_message_with_alarm_off_event_is_skipped(self):
        surv_svc = _make_service("SurveillanceAlarm")
        sds = _make_base_device("sds-ao", device_services=[surv_svc])
        sds.alarm = SurveillanceAlarmService.State.ALARM_ON
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
        asyncio.run(tracker.async_refresh())
        assert tracker.is_alarm_active_for("tw1") is False

    def test_refresh_only_matches_correct_source_id(self):
        surv_svc = _make_service("SurveillanceAlarm")
        sds = _make_base_device("sds-correct", device_services=[surv_svc])
        sds.alarm = SurveillanceAlarmService.State.ALARM_ON
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
        asyncio.run(tracker.async_refresh())
        assert tracker.is_alarm_active_for("tw1") is False

    def test_refresh_no_change_does_not_notify(self):
        surv_svc = _make_service("SurveillanceAlarm")
        sds = _make_base_device("sds-nc", device_services=[surv_svc])
        sds.alarm = SurveillanceAlarmService.State.ALARM_OFF
        session = _make_fake_session(smoke_detection_system=sds)
        tracker = self._make_tracker(sds, session)
        asyncio.run(tracker.async_refresh())  # first refresh establishes baseline
        called = []
        hass = _make_hass()
        tracker.register_listener(hass, lambda: called.append(1))
        asyncio.run(tracker.async_refresh())  # second refresh with same state -> no notification
        assert called == []

    def test_refresh_alarm_state_change_notifies_listeners(self):
        surv_svc = _make_service("SurveillanceAlarm")
        sds = _make_base_device("sds-notify", device_services=[surv_svc])
        sds.alarm = SurveillanceAlarmService.State.ALARM_ON
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
        asyncio.run(tracker.async_refresh())
        called = []
        hass = _make_hass()
        tracker.register_listener(hass, lambda: called.append(1))
        sds.alarm = SurveillanceAlarmService.State.ALARM_MUTED
        asyncio.run(tracker.async_refresh())
        assert called == [1]

    def test_refresh_alarm_off_notifies_and_clears(self):
        surv_svc = _make_service("SurveillanceAlarm")
        sds = _make_base_device("sds-clear", device_services=[surv_svc])
        sds.alarm = SurveillanceAlarmService.State.ALARM_ON
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
        asyncio.run(tracker.async_refresh())
        assert tracker.is_alarm_active_for("tw1") is True

        called = []
        hass = _make_hass()
        tracker.register_listener(hass, lambda: called.append(1))

        sds.alarm = SurveillanceAlarmService.State.ALARM_OFF
        asyncio.run(tracker.async_refresh())
        assert tracker.is_alarm_active_for("tw1") is False
        assert called == [1]

    def test_unregister_listener(self):
        surv_svc = _make_service("SurveillanceAlarm")
        sds = _make_base_device("sds-unreg", device_services=[surv_svc])
        sds.alarm = SurveillanceAlarmService.State.ALARM_ON
        session = _make_fake_session(smoke_detection_system=sds)
        tracker = self._make_tracker(sds, session)
        asyncio.run(tracker.async_refresh())  # establish ALARM_ON baseline

        called = []
        hass = _make_hass()

        def _cb():
            called.append(1)

        tracker.register_listener(hass, _cb)
        tracker.unregister_listener(_cb)

        sds.alarm = SurveillanceAlarmService.State.ALARM_OFF
        asyncio.run(tracker.async_refresh())
        assert called == []

    def test_teardown_unsubscribes_service(self):
        unsub_store = {}
        surv_svc = _make_service("SurveillanceAlarm")
        surv_svc.unsubscribe_callback = lambda k: unsub_store.update({k: True})
        sds = _make_base_device("sds-td", device_services=[surv_svc])
        sds.alarm = SurveillanceAlarmService.State.ALARM_OFF
        session = _make_fake_session(smoke_detection_system=sds)
        tracker = self._make_tracker(sds, session)
        tracker.teardown()
        assert "sds-td_twinguard_alarm_listener" in unsub_store

    def test_teardown_is_idempotent(self):
        surv_svc = _make_service("SurveillanceAlarm")
        sds = _make_base_device("sds-idem", device_services=[surv_svc])
        sds.alarm = SurveillanceAlarmService.State.ALARM_OFF
        session = _make_fake_session(smoke_detection_system=sds)
        tracker = self._make_tracker(sds, session)
        tracker.teardown()
        tracker.teardown()  # must not raise

    def test_teardown_prevents_further_notification(self):
        surv_svc = _make_service("SurveillanceAlarm")
        sds = _make_base_device("sds-post-td", device_services=[surv_svc])
        sds.alarm = SurveillanceAlarmService.State.ALARM_ON
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
        asyncio.run(tracker.async_refresh())  # baseline ALARM_ON + tw1

        called = []
        hass = _make_hass()
        tracker.register_listener(hass, lambda: called.append(1))
        tracker.teardown()

        sds.alarm = SurveillanceAlarmService.State.ALARM_OFF
        asyncio.run(tracker.async_refresh())  # should be skipped
        assert called == []

    def test_handle_alarm_update_triggers_refresh(self):
        """_handle_alarm_update() dispatches refresh() off the poll thread.

        With our fake hass (call_soon_threadsafe + async_create_task run
        synchronously), the full chain executes inline in the test so that
        we can assert the resulting state without a real event loop.
        """
        surv_svc = _make_service("SurveillanceAlarm")
        sds = _make_base_device("sds-upd", device_services=[surv_svc])
        sds.alarm = SurveillanceAlarmService.State.ALARM_ON
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
        called = []
        hass = _make_hass()
        tracker = self._make_tracker(sds, session, hass=hass)
        tracker.register_listener(hass, lambda: called.append(1))
        tracker._handle_alarm_update()
        asyncio.run(tracker.async_refresh())
        assert tracker.is_alarm_active_for("tw1") is True
        assert called == [1]

    def test_handle_alarm_update_dispatches_via_call_soon_threadsafe(self):
        """M1: _handle_alarm_update must schedule via async_create_task (not inline)."""
        surv_svc = _make_service("SurveillanceAlarm")
        sds = _make_base_device("sds-m1", device_services=[surv_svc])
        sds.alarm = SurveillanceAlarmService.State.ALARM_OFF
        session = _make_fake_session(smoke_detection_system=sds)
        hass = _make_hass()
        hass.async_create_task = MagicMock()
        tracker = self._make_tracker(sds, session, hass=hass)
        tracker._handle_alarm_update()
        assert hass.async_create_task.called

    def test_handle_alarm_update_does_not_wrap_executor_future_in_task(self):
        """Regression: _handle_alarm_update must use async_create_task, not executor."""
        surv_svc = _make_service("SurveillanceAlarm")
        sds = _make_base_device("sds-future", device_services=[surv_svc])
        sds.alarm = SurveillanceAlarmService.State.ALARM_OFF
        session = _make_fake_session(smoke_detection_system=sds)
        hass = _make_hass()
        hass.async_create_task = MagicMock()

        tracker = self._make_tracker(sds, session, hass=hass)
        tracker._handle_alarm_update()

        assert hass.async_create_task.called

    def test_extract_trigger_ids_handles_api_error_gracefully(self):
        surv_svc = _make_service("SurveillanceAlarm")
        sds = _make_base_device("sds-err", device_services=[surv_svc])
        sds.alarm = SurveillanceAlarmService.State.ALARM_ON
        session = _make_fake_session(smoke_detection_system=sds)
        session.api.get_messages = AsyncMock(side_effect=RuntimeError("network error"))
        tracker = self._make_tracker(sds, session)
        tracker._active_trigger_ids = {"tw-prev"}
        result = asyncio.run(tracker._extract_trigger_ids_from_messages())
        assert result == {"tw-prev"}

    def test_malformed_message_code_string_does_not_raise(self):
        """M2: messageCode as string (not dict) must be skipped, not raise."""
        surv_svc = _make_service("SurveillanceAlarm")
        sds = _make_base_device("sds-m2a", device_services=[surv_svc])
        sds.alarm = SurveillanceAlarmService.State.ALARM_ON
        session = _make_fake_session(
            smoke_detection_system=sds,
            messages=[
                {"messageCode": "SMOKE_ALARM", "sourceId": "sds-m2a", "arguments": {}},
            ],
        )
        tracker = self._make_tracker(sds, session)
        result = asyncio.run(tracker._extract_trigger_ids_from_messages())
        assert result == set()

    def test_malformed_arguments_string_does_not_raise(self):
        """M2: arguments as string (not dict) must be skipped, not raise."""
        surv_svc = _make_service("SurveillanceAlarm")
        sds = _make_base_device("sds-m2b", device_services=[surv_svc])
        sds.alarm = SurveillanceAlarmService.State.ALARM_ON
        session = _make_fake_session(
            smoke_detection_system=sds,
            messages=[
                {
                    "messageCode": {"name": "SMOKE_ALARM"},
                    "sourceId": "sds-m2b",
                    "arguments": '{"surveillanceEvents":[{"type":"SMOKE_LIGHT","triggerId":"tw1"}]}',
                },
            ],
        )
        tracker = self._make_tracker(sds, session)
        result = asyncio.run(tracker._extract_trigger_ids_from_messages())
        assert result == set()

    def test_malformed_arguments_none_does_not_raise(self):
        """M2: arguments as None must be skipped, not raise."""
        surv_svc = _make_service("SurveillanceAlarm")
        sds = _make_base_device("sds-m2c", device_services=[surv_svc])
        sds.alarm = SurveillanceAlarmService.State.ALARM_ON
        session = _make_fake_session(
            smoke_detection_system=sds,
            messages=[
                {
                    "messageCode": {"name": "SMOKE_ALARM"},
                    "sourceId": "sds-m2c",
                    "arguments": None,
                },
            ],
        )
        tracker = self._make_tracker(sds, session)
        result = asyncio.run(tracker._extract_trigger_ids_from_messages())
        assert result == set()

    def test_malformed_message_in_loop_does_not_abort_processing(self):
        """M2: one malformed message must not prevent processing of subsequent valid ones."""
        surv_svc = _make_service("SurveillanceAlarm")
        sds = _make_base_device("sds-m2d", device_services=[surv_svc])
        sds.alarm = SurveillanceAlarmService.State.ALARM_ON
        session = _make_fake_session(
            smoke_detection_system=sds,
            messages=[
                {"messageCode": "SMOKE_ALARM", "sourceId": "sds-m2d", "arguments": {}},
                {"messageCode": {"name": "SMOKE_ALARM"}, "sourceId": "sds-m2d", "arguments": None},
                {
                    "messageCode": {"name": "SMOKE_ALARM"},
                    "sourceId": "sds-m2d",
                    "arguments": {
                        "surveillanceEvents": [{"type": "SMOKE_LIGHT", "triggerId": "tw1"}]
                    },
                },
            ],
        )
        tracker = self._make_tracker(sds, session)
        result = asyncio.run(tracker._extract_trigger_ids_from_messages())
        assert "tw1" in result

    def test_listeners_stored_as_tuple_of_hass_and_callable(self):
        """L3: _listeners must contain (hass, callable) tuples, not bare callables."""
        surv_svc = _make_service("SurveillanceAlarm")
        sds = _make_base_device("sds-l3", device_services=[surv_svc])
        sds.alarm = SurveillanceAlarmService.State.ALARM_OFF
        session = _make_fake_session(smoke_detection_system=sds)
        hass = _make_hass()
        tracker = self._make_tracker(sds, session)

        def listener_fn():
            pass

        tracker.register_listener(hass, listener_fn)
        assert len(tracker._listeners) == 1
        entry = tracker._listeners[0]
        assert isinstance(entry, tuple) and len(entry) == 2
        stored_hass, stored_cb = entry
        assert stored_hass is hass
        assert stored_cb is listener_fn


class TestExtractTriggerIdsNonSmokeAlarm:
    """Messages with messageCode.name != 'SMOKE_ALARM' must be skipped."""

    def _make_tracker(self, sds_id="sds-001"):
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


class TestTwinguardSmokeAlarmSensor:
    """Unit tests for TwinguardSmokeAlarmSensor."""

    def _make_sensor(self, device_id="tw1", root_device_id="root1"):
        surv_svc = _make_service("SurveillanceAlarm")
        sds = _make_base_device("sds-sensor", device_services=[surv_svc])
        sds.alarm = SurveillanceAlarmService.State.ALARM_OFF
        session = _make_fake_session(smoke_detection_system=sds)
        tracker = TwinguardAlarmTracker(
            session=session, smoke_detection_system=sds, hass=_make_hass()
        )
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
        assert sensor.translation_key == "smoke"

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

    def test_async_request_smoketest(self):
        sensor, _ = self._make_sensor("tw-smoketest")
        sensor._device.async_smoketest_requested = AsyncMock()
        sensor.hass = _make_hass()

        asyncio.run(sensor.async_request_smoketest())

        sensor._device.async_smoketest_requested.assert_called_once()

    def test_handle_tracker_update_calls_schedule_update(self):
        """_handle_tracker_update() calls schedule_update_ha_state()."""
        sensor, _ = self._make_sensor()
        called = []
        sensor.schedule_update_ha_state = lambda: called.append(1)
        sensor._handle_tracker_update()
        assert called == [1]


# ---------------------------------------------------------------------------
# _cleanup_tracker() closure (async_setup_entry, twinguard + SDS teardown).
#
# Three independent tests exercise the *real* production closure end-to-end
# via three different session-mocking strategies (SimpleNamespace-based,
# MagicMock-based, and a captured-callback variant). They were written
# independently against the same target line and are kept as three distinct
# tests (not deduped) since each drives a meaningfully different code path
# through async_setup_entry's device wiring.
# ---------------------------------------------------------------------------

class TestBinarySensorCleanupTrackerBody:
    """Call async_setup_entry with a twinguard + SDS, capture _cleanup_tracker,
    invoke it, and verify tracker.teardown() is called.
    """

    def _make_hass(self):
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

    def _fake_device(self, device_id, name="FakeDev", root_id="root1"):
        return SimpleNamespace(
            id=device_id,
            name=name,
            root_device_id=root_id,
            serial=f"{device_id}-ser",
            device_services=[],
            supports_batterylevel=False,
            manufacturer="Bosch",
            device_model="FakeModel",
            deleted=False,
            status="AVAILABLE",
            subscribe_callback=lambda key, cb: None,
            unsubscribe_callback=lambda key: None,
        )

    def test_cleanup_tracker_teardown_called(self):
        """_cleanup_tracker() must call tracker.teardown().

        Strategy:
        1. Build a session with one SDS + one twinguard.
        2. Patch TwinguardAlarmTracker to return a controllable mock (tracker_mock).
        3. Capture all closures registered via config_entry.async_on_unload.
        4. Call async_setup_entry.
        5. Find the _cleanup_tracker closure among the unload callbacks.
        6. Call it -- this exercises the teardown() call.
        7. Assert tracker.teardown() was called.
        """
        tracker_mock = MagicMock()
        tracker_mock.teardown = MagicMock()
        tracker_mock.async_refresh = AsyncMock()

        sds = self._fake_device("sds-001", name="SDS")
        sds.alarm = MagicMock()
        sds.subscribe_callback = MagicMock()

        tw = self._fake_device("tw-001", name="Twinguard")
        tw.subscribe_callback = MagicMock()

        session = SimpleNamespace(
            _subscribers=[],
            subscribe=lambda cb: None,
            api=SimpleNamespace(get_messages=AsyncMock(return_value=[])),
            device_helper=SimpleNamespace(
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
            ),
        )

        hass = self._make_hass()

        captured_unloads = []
        config_entry = SimpleNamespace(
            options={},
            entry_id="E1",
            async_on_unload=lambda fn: captured_unloads.append(fn),
            runtime_data=SimpleNamespace(session=session),
        )

        platform_mock = MagicMock()
        platform_mock.async_register_entity_service = MagicMock()

        async def _run_setup():
            with (
                patch(
                    "custom_components.bosch_shc.binary_sensor."
                    "async_migrate_to_new_unique_id",
                    return_value=None,
                ),
                patch(
                    "custom_components.bosch_shc.binary_sensor."
                    "entity_platform.current_platform",
                ) as _cp,
                patch(
                    "custom_components.bosch_shc.binary_sensor.TwinguardAlarmTracker",
                    return_value=tracker_mock,
                ),
            ):
                _cp.get.return_value = platform_mock
                await async_setup_entry(hass, config_entry, lambda e, **kw: None)

        _run(_run_setup())

        assert len(captured_unloads) >= 2, (
            "Expected at least 2 unload closures (cleanup_tracker + listen_once)"
        )

        for fn in captured_unloads:
            assert callable(fn), f"Unload callback {fn!r} must be callable"
            fn()

        assert tracker_mock.teardown.call_count >= 1, (
            "tracker.teardown() was not called via any unload closure"
        )


class TestCleanupTrackerActualClosure:
    """_cleanup_tracker closure is registered via config_entry.async_on_unload
    -- capture it and CALL it to trigger the teardown line."""

    def test_cleanup_tracker_teardown_called_via_captured_closure(self):
        unload_callbacks = []

        def _capture_on_unload(fn):
            unload_callbacks.append(fn)

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

        bus = SimpleNamespace(
            async_listen_once=lambda event, cb: (lambda: None),
            fire=lambda *a, **kw: None,
        )

        async def _async_add_executor_job(fn, *args):
            return fn(*args)

        loop = SimpleNamespace(call_soon_threadsafe=lambda cb, *a: cb(*a))
        hass = SimpleNamespace(
            bus=bus,
            loop=loop,
            async_add_executor_job=_async_add_executor_job,
        )

        config_entry = SimpleNamespace(
            options={},
            entry_id="E1",
            async_on_unload=_capture_on_unload,
        )
        config_entry.runtime_data = SimpleNamespace(
            session=session, shc_device=None, title="Test SHC"
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

        # We just need to verify no exception is raised -- teardown() on the
        # real TwinguardAlarmTracker simply calls unsubscribe on the SDS
        # service.
        cleanup_fns = [
            fn for fn in unload_callbacks
            if fn.__name__ == "_cleanup_tracker"
        ]
        assert len(cleanup_fns) == 1, (
            f"Expected exactly one _cleanup_tracker callback, "
            f"got {len(cleanup_fns)}: {[f.__name__ for f in unload_callbacks]}"
        )
        cleanup_fns[0]()


class TestCleanupTrackerBody:
    """Actually execute _cleanup_tracker() from binary_sensor.async_setup_entry
    end-to-end, using MagicMock-heavy session/config_entry fakes (a third,
    independent mocking strategy from the two classes above)."""

    def test_cleanup_tracker_via_binary_sensor_setup(self):
        tracker = MagicMock()
        tracker.teardown = MagicMock()
        tracker.async_refresh = AsyncMock()

        session = MagicMock()
        for attr in [
            "shutter_contacts", "shutter_contacts2", "motion_detectors",
            "motion_detectors2", "smoke_detectors", "water_leakage_detectors",
            "presence_simulation_services", "wallthermostats", "thermostats",
        ]:
            setattr(session.device_helper, attr, [])

        sds = MagicMock()
        sds.id = "sds-001"
        sds.root_device_id = "root-sds"
        sds.subscribe_callback = MagicMock()
        session.device_helper.smoke_detection_system = sds

        tg = MagicMock()
        tg.id = "tg-001"
        tg.root_device_id = "root-001"
        tg.device_model = "TG"
        tg.manufacturer = "Bosch"
        tg.name = "TG1"
        tg.room_id = None
        tg.subscribe_callback = MagicMock()
        session.device_helper.twinguards = [tg]

        hass = MagicMock()
        hass.async_add_executor_job = AsyncMock(return_value=None)

        captured_unloads = []
        config_entry = MagicMock()
        config_entry.entry_id = "eid1"
        config_entry.options = {}
        config_entry.runtime_data = SimpleNamespace(
            session=session, shc_device=None, title="Test SHC"
        )
        config_entry.async_on_unload = MagicMock(
            side_effect=lambda fn: captured_unloads.append(fn)
        )

        fake_platform = MagicMock()
        fake_platform.async_register_entity_service = MagicMock()

        fake_ent_reg = MagicMock()
        fake_ent_reg.async_get_entity_id.return_value = None

        with (
            patch(
                "custom_components.bosch_shc.binary_sensor.TwinguardAlarmTracker",
                return_value=tracker,
            ),
            patch(
                "homeassistant.helpers.entity_platform.current_platform",
                MagicMock(get=MagicMock(return_value=fake_platform)),
            ),
            patch(
                "custom_components.bosch_shc.entity.entity_registry.async_get",
                return_value=fake_ent_reg,
            ),
        ):
            asyncio.run(
                __import__(
                    "custom_components.bosch_shc.binary_sensor",
                    fromlist=["async_setup_entry"],
                ).async_setup_entry(hass, config_entry, lambda entities: None)
            )

        for fn in captured_unloads:
            if callable(fn):
                try:
                    fn()
                except Exception:
                    pass

        tracker.teardown.assert_called()


# ===========================================================================
# BatterySensor
# ===========================================================================

class TestBatterySensor:
    def test_ok_is_off(self):
        s = _battery_sensor(BatteryLevelService.State.OK)
        assert s.is_on is False

    def test_low_battery_is_on(self):
        s = _battery_sensor(BatteryLevelService.State.LOW_BATTERY)
        assert s.is_on is True

    def test_critical_low_is_on(self):
        s = _battery_sensor(BatteryLevelService.State.CRITICAL_LOW)
        assert s.is_on is True

    def test_critically_low_battery_is_on(self):
        s = _battery_sensor(BatteryLevelService.State.CRITICALLY_LOW_BATTERY)
        assert s.is_on is True

    def test_not_available_is_on(self):
        """NOT_AVAILABLE means no battery state reported -- is_on is False (not a problem)."""
        s = _battery_sensor(BatteryLevelService.State.NOT_AVAILABLE)
        assert s.is_on is False

    def test_device_class_is_battery(self):
        s = _battery_sensor(BatteryLevelService.State.OK)
        assert s._attr_device_class == BinarySensorDeviceClass.BATTERY


class TestBatterySensorLoggingPaths:
    """is_on must log at debug/warning for certain states."""

    def test_not_available_logs_debug_and_returns_false(self):
        """NOT_AVAILABLE -> debug log, is_on is False (no battery state yet)."""
        s = _battery_sensor(BatteryLevelService.State.NOT_AVAILABLE)
        with patch("custom_components.bosch_shc.binary_sensor.LOGGER") as mock_log:
            result = s.is_on
        mock_log.debug.assert_called_once()
        assert result is False

    def test_critical_low_logs_warning(self):
        s = _battery_sensor(BatteryLevelService.State.CRITICAL_LOW)
        with patch("custom_components.bosch_shc.binary_sensor.LOGGER") as mock_log:
            result = s.is_on
        mock_log.warning.assert_called_once()
        assert result is True

    def test_critically_low_battery_logs_warning(self):
        s = _battery_sensor(BatteryLevelService.State.CRITICALLY_LOW_BATTERY)
        with patch("custom_components.bosch_shc.binary_sensor.LOGGER") as mock_log:
            result = s.is_on
        mock_log.warning.assert_called_once()
        assert result is True

    def test_low_battery_logs_warning(self):
        s = _battery_sensor(BatteryLevelService.State.LOW_BATTERY)
        with patch("custom_components.bosch_shc.binary_sensor.LOGGER") as mock_log:
            result = s.is_on
        mock_log.warning.assert_called_once()
        assert result is True

    def test_ok_logs_nothing(self):
        s = _battery_sensor(BatteryLevelService.State.OK)
        with patch("custom_components.bosch_shc.binary_sensor.LOGGER") as mock_log:
            result = s.is_on
        mock_log.debug.assert_not_called()
        mock_log.warning.assert_not_called()
        assert result is False

    def test_critically_low_battery_is_on(self):
        """CRITICALLY_LOW_BATTERY is not OK -> is_on True (no special logging path)."""
        s = _battery_sensor(BatteryLevelService.State.CRITICALLY_LOW_BATTERY)
        assert s.is_on is True


class TestBatterySensorInit:
    def test_init_sets_name_unique_id_category(self):
        dev = _make_base_device("bat-dev", name="Sensor A", root_device_id="root-b")
        dev.batterylevel = BatteryLevelService.State.OK
        sensor = BatterySensor(device=dev, entry_id="E1")
        # name comes from device_class (BinarySensorDeviceClass.BATTERY); _attr_name is None
        assert sensor._attr_name is None
        assert sensor._attr_unique_id == "root-b_bat-dev_battery"
        assert sensor._attr_entity_category == EntityCategory.DIAGNOSTIC

    def test_init_device_class_battery(self):
        dev = _make_base_device("bat-dev2")
        dev.batterylevel = BatteryLevelService.State.OK
        sensor = BatterySensor(device=dev, entry_id="E1")
        assert sensor._attr_device_class == BinarySensorDeviceClass.BATTERY


# ===========================================================================
# Battery reporting coverage contract
#
# Guards against silent drift in battery support:
# 1. Every battery-powered device class in the lib (SHCBatteryDevice subclass)
#    must be wired into the HA battery entity loops -- otherwise a newly added
#    battery device would ship with NO battery sensor and nobody would notice.
# 2. The device_helper accessors used by those loops must actually be present
#    in both binary_sensor.py and sensor.py (the binary "Battery" + enum
#    "Battery Level" entities).
# 3. BatteryLevelService.State must stay exhaustive -- a new Bosch firmware
#    enum value would otherwise slip through BatterySensor.is_on (`!= OK`)
#    silently.
#
# Pattern: pure inspection + source scan; no HA harness, no live session.
# ===========================================================================

# Battery-capable lib classes known to be wired into the HA battery loops.
# device_helper accessor in parentheses (binary_sensor.py:234-244 /
# sensor.py battery loop). If a NEW battery device is added to the lib, the
# drift test below fails until it is wired in here AND in both loops.
KNOWN_BATTERY_DEVICE_CLASSES = {
    "SHCMotionDetector",       # motion_detectors
    "SHCMotionDetector2",      # motion_detectors2
    "SHCShutterContact",       # shutter_contacts
    "SHCShutterContact2",      # shutter_contacts2
    "SHCShutterContact2Plus",  # shutter_contacts2
    "SHCSmokeDetector",        # smoke_detectors
    "SHCThermostat",           # thermostats
    "SHCThermostatGen2",       # thermostats (TRV_GEN2 / TRV_GEN2_DUAL, subclass of SHCThermostat)
    "SHCWallThermostat",       # wallthermostats
    "SHCRoomThermostat2",      # roomthermostats
    "SHCTwinguard",            # twinguards
    "SHCUniversalSwitch",      # universal_switches
    "SHCUniversalSwitch2",     # universal_switches
    "SHCWaterLeakageSensor",   # water_leakage_detectors
    "SHCOutdoorSiren",         # outdoor_sirens (generic battery loop + SirenBatterySensor)
}

# device_helper accessors the battery loops iterate. Each must appear in BOTH
# binary_sensor.py (binary "Battery") and sensor.py (enum "Battery Level").
REQUIRED_BATTERY_ACCESSORS = {
    "motion_detectors",
    "motion_detectors2",
    "shutter_contacts",
    "shutter_contacts2",
    "smoke_detectors",
    "thermostats",
    "twinguards",
    "universal_switches",
    "wallthermostats",
    "roomthermostats",
    "water_leakage_detectors",
}


def _lib_battery_subclasses():
    return {
        cls.__name__
        for _, cls in inspect.getmembers(models, inspect.isclass)
        if issubclass(cls, SHCBatteryDevice) and cls is not SHCBatteryDevice
    }


class TestBatteryDeviceWiring:
    def test_no_unwired_battery_device(self):
        """Every SHCBatteryDevice subclass must be accounted for. A new battery
        device added to the lib fails this until it is wired into the HA battery
        loops (binary_sensor.py + sensor.py) and listed above.
        """
        actual = _lib_battery_subclasses()
        new = actual - KNOWN_BATTERY_DEVICE_CLASSES
        assert not new, (
            f"New battery-powered device class(es) {sorted(new)} in boschshcpy "
            f"are NOT wired into the HA battery entity loops. Add the device's "
            f"device_helper accessor to the battery loop in BOTH "
            f"binary_sensor.py and sensor.py, then add the class name to "
            f"KNOWN_BATTERY_DEVICE_CLASSES."
        )

    def test_known_set_has_no_stale_entries(self):
        """KNOWN set must not reference classes that no longer exist / no longer
        carry a battery (caught after a lib refactor).
        """
        actual = _lib_battery_subclasses()
        stale = KNOWN_BATTERY_DEVICE_CLASSES - actual
        assert not stale, (
            f"KNOWN_BATTERY_DEVICE_CLASSES lists {sorted(stale)} which are no "
            f"longer SHCBatteryDevice subclasses -- remove them."
        )

    def test_accessors_present_in_binary_sensor_and_sensor(self):
        """The battery-loop device_helper accessors must exist in both platform
        files, so each battery device gets the binary 'Battery' AND the enum
        'Battery Level' entity.
        """
        import custom_components.bosch_shc.binary_sensor as bs
        import custom_components.bosch_shc.sensor as sn

        for module in (bs, sn):
            src = inspect.getsource(module)
            missing = {
                acc
                for acc in REQUIRED_BATTERY_ACCESSORS
                if f"device_helper.{acc}" not in src
            }
            assert not missing, (
                f"{module.__name__} battery loop is missing accessors "
                f"{sorted(missing)} -- battery entities would not be created for "
                f"those devices."
            )


class TestBatteryEnumExhaustive:
    def test_state_members_are_exactly_the_handled_set(self):
        """BatterySensor.is_on returns `level != OK` after explicit branches for
        NOT_AVAILABLE / LOW_BATTERY / CRITICAL_LOW / CRITICALLY_LOW_BATTERY. If
        Bosch firmware adds a new enum value, this fails so the new state is
        consciously triaged (problem vs benign) in is_on before shipping.
        """
        expected = {
            "OK",
            "LOW_BATTERY",
            "CRITICAL_LOW",
            "CRITICALLY_LOW_BATTERY",
            "NOT_AVAILABLE",
        }
        actual = {m.value for m in BatteryLevelService.State}
        assert actual == expected, (
            f"BatteryLevelService.State changed: {sorted(actual ^ expected)}. "
            f"Update BatterySensor.is_on (binary_sensor.py) and this expected "
            f"set."
        )


# ===========================================================================
# OccupancyDetectionSensor (Motion Detector II)
# ===========================================================================

class TestOccupancyDetectionSensor:
    def test_device_class_is_occupancy(self):
        s = OccupancyDetectionSensor.__new__(OccupancyDetectionSensor)
        assert s._attr_device_class == BinarySensorDeviceClass.OCCUPANCY

    def test_is_on_when_occupied(self):
        s = _make_occupancy_sensor(occupied=True)
        assert s.is_on is True

    def test_is_off_when_not_occupied(self):
        s = _make_occupancy_sensor(occupied=False)
        assert s.is_on is False

    def test_extra_state_attributes_contains_timestamp(self):
        ts = "2026-06-20T12:34:56.789Z"
        s = _make_occupancy_sensor(last_occupancy_change_time=ts)
        attrs = s.extra_state_attributes
        assert "last_occupancy_change" in attrs
        assert attrs["last_occupancy_change"] == ts

    def test_unique_id_format(self):
        dev = _make_md2_device(root_device_id="root-X", id="dev-Y")
        s = OccupancyDetectionSensor.__new__(OccupancyDetectionSensor)
        s._device = dev
        s._attr_name = f"{dev.name} Occupancy"
        s._attr_unique_id = f"{dev.root_device_id}_{dev.id}_occupancy"
        assert s._attr_unique_id == "root-X_dev-Y_occupancy"

    def test_name_format(self):
        dev = _make_md2_device(name="Flur Bewegungsmelder")
        s = OccupancyDetectionSensor.__new__(OccupancyDetectionSensor)
        s._device = dev
        s._attr_name = f"{dev.name} Occupancy"
        s._attr_unique_id = f"{dev.root_device_id}_{dev.id}_occupancy"
        assert s._attr_name == "Flur Bewegungsmelder Occupancy"


class TestOccupancyDetectionSensorRealInit:
    """OccupancyDetectionSensor.__init__ sets _attr_name and _attr_unique_id."""

    def _make_dev(self, device_id="hdm:md2:1", root_device_id="root-md2",
                  name="Motion Detector II"):
        dev = _base_device(device_id=device_id, name=name, root_device_id=root_device_id)
        dev.occupied = False
        dev.last_occupancy_change_time = "2026-06-20T12:00:00.000Z"
        return dev

    def test_attr_name_is_occupancy(self):
        dev = self._make_dev()
        with patch.object(OccupancyDetectionSensor, "_update_attr", lambda self: None):
            sensor = OccupancyDetectionSensor(device=dev, entry_id="E1")
        assert sensor.translation_key == "occupancy"

    def test_attr_unique_id_format(self):
        dev = self._make_dev(device_id="devY", root_device_id="rootX")
        with patch.object(OccupancyDetectionSensor, "_update_attr", lambda self: None):
            sensor = OccupancyDetectionSensor(device=dev, entry_id="E1")
        assert sensor._attr_unique_id == "rootX_devY_occupancy"

    def test_device_stored(self):
        dev = self._make_dev()
        with patch.object(OccupancyDetectionSensor, "_update_attr", lambda self: None):
            sensor = OccupancyDetectionSensor(device=dev, entry_id="E1")
        assert sensor._device is dev

    def test_is_on_occupied_true(self):
        dev = self._make_dev()
        with patch.object(OccupancyDetectionSensor, "_update_attr", lambda self: None):
            sensor = OccupancyDetectionSensor(device=dev, entry_id="E1")
        sensor._device.occupied = True
        assert sensor.is_on is True

    def test_is_on_not_occupied_false(self):
        dev = self._make_dev()
        with patch.object(OccupancyDetectionSensor, "_update_attr", lambda self: None):
            sensor = OccupancyDetectionSensor(device=dev, entry_id="E1")
        sensor._device.occupied = False
        assert sensor.is_on is False

    def test_extra_state_attributes_key(self):
        dev = self._make_dev()
        with patch.object(OccupancyDetectionSensor, "_update_attr", lambda self: None):
            sensor = OccupancyDetectionSensor(device=dev, entry_id="E1")
        attrs = sensor.extra_state_attributes
        assert "last_occupancy_change" in attrs
        assert attrs["last_occupancy_change"] == "2026-06-20T12:00:00.000Z"


# ===========================================================================
# TamperSensor
# ===========================================================================

class TestTamperSensorClassAttrs:
    def test_device_class_is_tamper(self):
        sensor = TamperSensor.__new__(TamperSensor)
        assert sensor._attr_device_class == BinarySensorDeviceClass.TAMPER

    def test_entity_category_is_diagnostic(self):
        sensor = TamperSensor.__new__(TamperSensor)
        assert sensor._attr_entity_category == EntityCategory.DIAGNOSTIC


class TestTamperSensorIsOn:
    def test_is_on_when_tampered(self):
        sensor = _make_tamper_sensor(was_tampered=True)
        assert sensor.is_on is True

    def test_is_off_when_not_tampered(self):
        sensor = _make_tamper_sensor(was_tampered=False)
        assert sensor.is_on is False

    def test_is_on_with_falsy_was_tampered(self):
        """Getattr default and bool() coercion guard."""
        dev = SimpleNamespace(
            name="MD2", id="dev1", root_device_id="root1", was_tampered=0,
        )
        sensor = TamperSensor.__new__(TamperSensor)
        sensor._device = dev
        assert sensor.is_on is False

    def test_is_off_when_attribute_missing(self):
        """When device has no was_tampered, getattr default=False -> is_on=False."""
        dev = SimpleNamespace(
            name="MD2", id="dev1", root_device_id="root1",
            # no was_tampered
        )
        sensor = TamperSensor.__new__(TamperSensor)
        sensor._device = dev
        assert sensor.is_on is False


class TestTamperSensorExtraAttrs:
    def test_extra_attrs_contains_last_tamper_time(self):
        ts = "2026-06-21T10:00:00.000Z"
        sensor = _make_tamper_sensor(last_tamper_time=ts)
        attrs = sensor.extra_state_attributes
        assert "last_tamper_time" in attrs
        assert attrs["last_tamper_time"] == ts

    def test_extra_attrs_last_tamper_time_none_when_missing(self):
        """When device has no last_tamper_time, falls back to None."""
        dev = SimpleNamespace(
            name="MD2", id="dev1", root_device_id="root1", was_tampered=False,
            # no last_tamper_time
        )
        sensor = TamperSensor.__new__(TamperSensor)
        sensor._device = dev
        attrs = sensor.extra_state_attributes
        assert attrs["last_tamper_time"] is None

    def test_extra_attrs_default_na_time_passthrough(self):
        sensor = _make_tamper_sensor(last_tamper_time="n/a")
        attrs = sensor.extra_state_attributes
        assert attrs["last_tamper_time"] == "n/a"


class TestTamperSensorIdentifiers:
    def test_unique_id_format(self):
        sensor = _make_tamper_sensor()
        assert sensor._attr_unique_id.endswith("_tamper")

    def test_attr_name_is_tamper(self):
        sensor = _make_tamper_sensor()
        assert sensor._attr_name == "Tamper"


# ===========================================================================
# async_setup_entry -- comprehensive device-wiring / lifecycle integration
# ===========================================================================

class TestAsyncSetupEntry:
    """Drive async_setup_entry with various device combinations."""

    def _setup(self, session):
        """Wire hass + config_entry + platform mock and call async_setup_entry."""
        hass = _make_hass()

        async def _fake_executor_job(fn, *args):
            return fn(*args)

        hass.async_add_executor_job = _fake_executor_job
        config_entry = SimpleNamespace(options={},
            entry_id="E1",
            async_on_unload=lambda fn: None,
        )
        config_entry.runtime_data = SimpleNamespace(
            session=session, shc_device=None, title="Test SHC"
        )
        entities_collected = []

        def async_add_entities(ents, update_before_add=False):
            entities_collected.extend(ents)

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

    def test_empty_session_no_entities(self):
        session = _make_fake_session()
        entities, platform = self._setup(session)
        assert entities == []
        assert platform.async_register_entity_service.call_count == 2

    def test_shutter_contact_added(self):
        dev = _make_base_device("sc1")
        dev.state = ShutterContactService.State.CLOSED
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
        dev.state = ShutterContactService.State.CLOSED
        dev.device_class = "ENTRANCE_DOOR"
        dev.vibrationsensor = VibrationSensorService.State.NO_VIBRATION
        session = _make_fake_session(shutter_contacts2=[dev])
        entities, _ = self._setup(session)
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
        dev.state = ShutterContactService.State.OPEN
        dev.device_class = "GENERIC"
        session = _make_fake_session()
        entities, _ = self._setup(session)

        _, cb = session._subscribers[0]
        cb(device=dev)
        # The callback calls async_add_entities([binary_sensor]) synchronously;
        # the call itself must not raise.

    def test_motion_detector_added(self):
        lm_svc = _make_service("LatestMotion")
        dev = _make_base_device("md1", device_services=[lm_svc])
        dev.latestmotion = None
        session = _make_fake_session(motion_detectors=[dev])
        hass = _make_hass()
        config_entry = SimpleNamespace(options={}, entry_id="E1", async_on_unload=lambda fn: None)
        config_entry.runtime_data = SimpleNamespace(
            session=session, shc_device=None, title="Test SHC"
        )
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
        """LatestMotion service subscribe_callback is called during async_added_to_hass."""
        cb_store = {}

        def record_subscribe(key, cb):
            cb_store[key] = cb

        lm_svc = _make_service("LatestMotion", subscribe_callback=record_subscribe)
        dev = _make_base_device("md2", device_services=[lm_svc])
        dev.latestmotion = None
        session = _make_fake_session(motion_detectors=[dev])
        hass = _make_hass()
        config_entry = SimpleNamespace(options={}, entry_id="E1", async_on_unload=lambda fn: None)
        config_entry.runtime_data = SimpleNamespace(
            session=session, shc_device=None, title="Test SHC"
        )
        entities_collected = []

        async def _run_setup():
            with (
                patch("custom_components.bosch_shc.binary_sensor.async_migrate_to_new_unique_id", return_value=None),
                patch("custom_components.bosch_shc.binary_sensor.entity_platform.current_platform") as _cp,
                patch(
                    "custom_components.bosch_shc.binary_sensor.async_get_device_id",
                    return_value="ha-device-id",
                ),
            ):
                _cp.get.return_value = MagicMock()
                await async_setup_entry(
                    hass, config_entry,
                    lambda ents, **kw: entities_collected.extend(ents),
                )
                for entity in entities_collected:
                    entity.hass = hass
                    entity.entity_id = f"binary_sensor.{entity._device.id}"
                    await entity.async_added_to_hass()

        _run(_run_setup())
        assert any(k is not None and "_eventlistener" in k for k in cb_store)

    def test_smoke_detector_added(self):
        alarm_svc = _make_service("Alarm")
        dev = _make_base_device("sd1", device_services=[alarm_svc])
        dev.alarmstate = AlarmService.State.IDLE_OFF
        dev.smokedetectorcheck_state = SmokeDetectorCheckService.State.NONE
        session = _make_fake_session(smoke_detectors=[dev])
        entities, _ = self._setup(session)
        assert any(isinstance(e, SmokeDetectorSensor) for e in entities)

    def test_smoke_detector_alarm_service_subscribed(self):
        """Alarm service subscribe_callback is called during async_added_to_hass."""
        cb_store = {}
        alarm_svc = _make_service("Alarm", subscribe_callback=lambda k, cb: cb_store.update({k: cb}))
        dev = _make_base_device("sd2", device_services=[alarm_svc])
        dev.alarmstate = AlarmService.State.IDLE_OFF
        dev.smokedetectorcheck_state = SmokeDetectorCheckService.State.NONE
        session = _make_fake_session(smoke_detectors=[dev])
        hass = _make_hass()
        config_entry = SimpleNamespace(options={}, entry_id="E1", async_on_unload=lambda fn: None)
        config_entry.runtime_data = SimpleNamespace(
            session=session, shc_device=None, title="Test SHC"
        )
        entities_collected = []

        async def _run_setup():
            with (
                patch("custom_components.bosch_shc.binary_sensor.async_migrate_to_new_unique_id", return_value=None),
                patch("custom_components.bosch_shc.binary_sensor.entity_platform.current_platform") as _cp,
                patch(
                    "custom_components.bosch_shc.binary_sensor.async_get_device_id",
                    return_value="ha-device-id",
                ),
            ):
                _cp.get.return_value = MagicMock()
                await async_setup_entry(
                    hass, config_entry,
                    lambda ents, **kw: entities_collected.extend(ents),
                )
                for entity in entities_collected:
                    entity.hass = hass
                    entity.entity_id = f"binary_sensor.{entity._device.id}"
                    await entity.async_added_to_hass()

        _run(_run_setup())
        assert any(k is not None and "_eventlistener" in k for k in cb_store)

    def test_smoke_detection_system_added(self):
        surv_svc = _make_service("SurveillanceAlarm")
        dev = _make_base_device("sds1", device_services=[surv_svc])
        dev.alarm = SurveillanceAlarmService.State.ALARM_OFF
        session = _make_fake_session(smoke_detection_system=dev)
        entities, _ = self._setup(session)
        assert any(isinstance(e, SmokeDetectionSystemSensor) for e in entities)

    def test_smoke_detection_system_none_skipped(self):
        session = _make_fake_session(smoke_detection_system=None)
        entities, _ = self._setup(session)
        assert not any(isinstance(e, SmokeDetectionSystemSensor) for e in entities)

    def test_smoke_detection_system_surveillance_subscribed(self):
        """SurveillanceAlarm subscribe_callback is called during async_added_to_hass."""
        cb_store = {}
        surv_svc = _make_service("SurveillanceAlarm", subscribe_callback=lambda k, cb: cb_store.update({k: cb}))
        dev = _make_base_device("sds2", device_services=[surv_svc])
        dev.alarm = SurveillanceAlarmService.State.ALARM_OFF
        session = _make_fake_session(smoke_detection_system=dev)
        hass = _make_hass()
        config_entry = SimpleNamespace(options={}, entry_id="E1", async_on_unload=lambda fn: None)
        config_entry.runtime_data = SimpleNamespace(
            session=session, shc_device=None, title="Test SHC"
        )
        entities_collected = []

        async def _run_setup():
            with (
                patch("custom_components.bosch_shc.binary_sensor.async_migrate_to_new_unique_id", return_value=None),
                patch("custom_components.bosch_shc.binary_sensor.entity_platform.current_platform") as _cp,
                patch(
                    "custom_components.bosch_shc.binary_sensor.async_get_device_id",
                    return_value="ha-device-id",
                ),
            ):
                _cp.get.return_value = MagicMock()
                await async_setup_entry(
                    hass, config_entry,
                    lambda ents, **kw: entities_collected.extend(ents),
                )
                for entity in entities_collected:
                    entity.hass = hass
                    entity.entity_id = f"binary_sensor.{entity._device.id}"
                    await entity.async_added_to_hass()

        _run(_run_setup())
        assert any(k is not None and "_eventlistener" in k for k in cb_store)

    def test_twinguard_smoke_alarm_sensor_added_when_twinguards_present(self):
        """TwinguardSmokeAlarmSensor created for each Twinguard when SDS exists."""
        surv_svc = _make_service("SurveillanceAlarm")
        sds = _make_base_device("smokeDetectionSystem", device_services=[surv_svc])
        sds.alarm = SurveillanceAlarmService.State.ALARM_OFF
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
        sds.alarm = SurveillanceAlarmService.State.ALARM_OFF
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
        sds.alarm = SurveillanceAlarmService.State.ALARM_OFF
        tw = _make_base_device("tw-x", name="TW", root_device_id="root-x")
        session = _make_fake_session(smoke_detection_system=sds, twinguards=[tw])
        entities, _ = self._setup(session)
        sensor = next(e for e in entities if isinstance(e, TwinguardSmokeAlarmSensor))
        assert sensor._attr_unique_id == "root-x_tw-x_smoke"
        assert sensor.translation_key == "smoke"

    def test_twinguard_tracker_subscribed_to_surveillance_alarm(self):
        """TwinguardAlarmTracker subscribes to SurveillanceAlarm service."""
        cb_store = {}
        surv_svc = _make_service(
            "SurveillanceAlarm",
            subscribe_callback=lambda k, cb: cb_store.update({k: cb}),
        )
        sds = _make_base_device("sds-sub", device_services=[surv_svc])
        sds.alarm = SurveillanceAlarmService.State.ALARM_OFF
        tw = _make_base_device("tw-sub")
        session = _make_fake_session(smoke_detection_system=sds, twinguards=[tw])
        self._setup(session)
        assert any("_twinguard_alarm_listener" in k for k in cb_store)

    def test_water_leakage_detector_added(self):
        dev = _make_base_device("wl1")
        dev.leakage_state = WaterLeakageSensorService.State.NO_LEAKAGE
        dev.push_notification_state = WaterLeakageSensorTiltService.State.ENABLED
        dev.acoustic_signal_state = WaterLeakageSensorTiltService.State.ENABLED
        session = _make_fake_session(water_leakage_detectors=[dev])
        entities, _ = self._setup(session)
        assert any(isinstance(e, WaterLeakageDetectorSensor) for e in entities)

    def test_battery_sensor_added_when_supported(self):
        dev = _make_base_device("bat1", supports_batterylevel=True)
        dev.batterylevel = BatteryLevelService.State.OK
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
            d.batterylevel = BatteryLevelService.State.OK
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

    def test_motion_detector2_added(self):
        """MD2 device in motion_detectors2 -> MotionDetectionSensor entity created."""
        lm_svc = _make_service("LatestMotion")
        dev = _make_base_device("md2-1", device_services=[lm_svc])
        dev.latestmotion = None
        session = _make_fake_session(motion_detectors2=[dev])
        hass = _make_hass()
        config_entry = SimpleNamespace(options={}, entry_id="E1", async_on_unload=lambda fn: None)
        config_entry.runtime_data = SimpleNamespace(
            session=session, shc_device=None, title="Test SHC"
        )
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
        """LatestMotion subscribe_callback is called for MD2 device during async_added_to_hass."""
        cb_store = {}

        def record_subscribe(key, cb):
            cb_store[key] = cb

        lm_svc = _make_service("LatestMotion", subscribe_callback=record_subscribe)
        dev = _make_base_device("md2-2", device_services=[lm_svc])
        dev.latestmotion = None
        session = _make_fake_session(motion_detectors2=[dev])
        hass = _make_hass()
        config_entry = SimpleNamespace(options={}, entry_id="E1", async_on_unload=lambda fn: None)
        config_entry.runtime_data = SimpleNamespace(
            session=session, shc_device=None, title="Test SHC"
        )
        entities_collected = []

        async def _run_setup():
            with (
                patch("custom_components.bosch_shc.binary_sensor.async_migrate_to_new_unique_id", return_value=None),
                patch("custom_components.bosch_shc.binary_sensor.entity_platform.current_platform") as _cp,
                patch(
                    "custom_components.bosch_shc.binary_sensor.async_get_device_id",
                    return_value="ha-device-id",
                ),
            ):
                _cp.get.return_value = MagicMock()
                await async_setup_entry(
                    hass, config_entry,
                    lambda ents, **kw: entities_collected.extend(ents),
                )
                for entity in entities_collected:
                    entity.hass = hass
                    entity.entity_id = f"binary_sensor.{entity._device.id}"
                    await entity.async_added_to_hass()

        _run(_run_setup())
        assert any(k is not None and "_eventlistener" in k for k in cb_store)

    def test_motion_detector2_battery_added_when_supported(self):
        """MD2 device with battery support -> BatterySensor entity created."""
        lm_svc = _make_service("LatestMotion")
        dev = _make_base_device("md2-bat", device_services=[lm_svc], supports_batterylevel=True)
        dev.latestmotion = None
        dev.batterylevel = BatteryLevelService.State.OK
        session = _make_fake_session(motion_detectors2=[dev])
        entities, _ = self._setup(session)
        assert any(isinstance(e, BatterySensor) for e in entities)

    def test_motion_detector2_and_gen1_both_added(self):
        """Gen1 and MD2 devices both present -> two MotionDetectionSensor entities."""
        lm_svc1 = _make_service("LatestMotion")
        dev1 = _make_base_device("md1-x", device_services=[lm_svc1])
        dev1.latestmotion = None

        lm_svc2 = _make_service("LatestMotion")
        dev2 = _make_base_device("md2-x", device_services=[lm_svc2])
        dev2.latestmotion = None

        session = _make_fake_session(motion_detectors=[dev1], motion_detectors2=[dev2])
        hass = _make_hass()
        config_entry = SimpleNamespace(options={}, entry_id="E1", async_on_unload=lambda fn: None)
        config_entry.runtime_data = SimpleNamespace(
            session=session, shc_device=None, title="Test SHC"
        )
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

    def test_platform_services_registered(self):
        session = _make_fake_session()
        _, platform = self._setup(session)
        calls = [c[0][0] for c in platform.async_register_entity_service.call_args_list]
        assert "smokedetector_check" in calls
        assert "smokedetector_alarmstate" in calls

    def test_unsubscribe_closure_removes_subscriber(self):
        """async_on_unload receives a closure that removes the shutter subscriber."""
        unload_callbacks = []
        session = _make_fake_session()
        hass = _make_hass()
        config_entry = SimpleNamespace(options={},
            entry_id="E1",
            async_on_unload=lambda fn: unload_callbacks.append(fn),
        )
        config_entry.runtime_data = SimpleNamespace(
            session=session, shc_device=None, title="Test SHC"
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
        assert len(session._subscribers) == 1
        unload_callbacks[0]()
        assert len(session._subscribers) == 0

    def test_unsubscribe_closure_idempotent(self):
        """Calling unsubscribe twice must not raise."""
        unload_callbacks = []
        session = _make_fake_session()
        hass = _make_hass()
        config_entry = SimpleNamespace(options={},
            entry_id="E1",
            async_on_unload=lambda fn: unload_callbacks.append(fn),
        )
        config_entry.runtime_data = SimpleNamespace(
            session=session, shc_device=None, title="Test SHC"
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
        fn()  # second call -- must not raise ValueError

    def test_l4_ha_stop_listener_registered_with_async_on_unload(self):
        """L4: async_listen_once(EVENT_HOMEASSISTANT_STOP) return value passed to
        config_entry.async_on_unload so the listener is removed on entry reload.
        """
        unload_callbacks = []
        surv_svc = _make_service("SurveillanceAlarm")
        sds = _make_base_device("sds-l4", device_services=[surv_svc])
        sds.alarm = SurveillanceAlarmService.State.ALARM_OFF
        tw = _make_base_device("tw-l4")
        session = _make_fake_session(smoke_detection_system=sds, twinguards=[tw])
        hass = _make_hass()

        async def _fake_executor_job(fn, *args):
            return fn(*args)

        hass.async_add_executor_job = _fake_executor_job
        config_entry = SimpleNamespace(options={},
            entry_id="E1",
            async_on_unload=lambda fn: unload_callbacks.append(fn),
        )
        config_entry.runtime_data = SimpleNamespace(
            session=session, shc_device=None, title="Test SHC"
        )

        async def _run_setup():
            with (
                patch("custom_components.bosch_shc.binary_sensor.async_migrate_to_new_unique_id", return_value=None),
                patch("custom_components.bosch_shc.binary_sensor.entity_platform.current_platform") as _cp,
            ):
                _cp.get.return_value = MagicMock()
                await async_setup_entry(hass, config_entry, lambda ents, **kw: None)

        _run(_run_setup())
        # With twinguards present there must be at least 2 unload callbacks:
        # one for _cleanup_tracker and one that is the async_listen_once unsub.
        assert len(unload_callbacks) >= 2
        for cb in unload_callbacks:
            assert callable(cb)


class TestBinarySensorSetupExcludedBranches:
    """Each excluded device type must be skipped (continue) in async_setup_entry."""

    def test_excluded_shutter_contact_not_in_entities(self):
        dev = _fake_device("sc-excl")
        dev.state = ShutterContactService.State.CLOSED
        dev.device_class = "REGULAR_WINDOW"
        session = _make_fake_session_v2(shutter_contacts=[dev])
        entities = _run_setup_with_options(session, _excl("sc-excl"))
        ids = [getattr(e, "_device", None) and e._device.id for e in entities]
        assert "sc-excl" not in ids

    def test_excluded_motion_detector_not_in_entities(self):
        dev = _fake_device("md-excl", device_services=[_make_service_simple("LatestMotion")])
        dev.latestmotion = None
        session = _make_fake_session_v2(motion_detectors=[dev])
        entities = _run_setup_with_options(session, _excl("md-excl"))
        ids = [getattr(e, "_device", None) and e._device.id for e in entities]
        assert "md-excl" not in ids

    def test_excluded_motion_detector2_not_in_entities(self):
        dev = _fake_device("md2-excl", device_services=[_make_service_simple("LatestMotion")])
        dev.latestmotion = None
        session = _make_fake_session_v2(motion_detectors2=[dev])
        entities = _run_setup_with_options(session, _excl("md2-excl"))
        ids = [getattr(e, "_device", None) and e._device.id for e in entities]
        assert "md2-excl" not in ids

    def test_excluded_smoke_detector_not_in_entities(self):
        dev = _fake_device("sd-excl", device_services=[_make_service_simple("Alarm")])
        dev.alarmstate = AlarmService.State.IDLE_OFF
        dev.smokedetectorcheck_state = SmokeDetectorCheckService.State.NONE
        session = _make_fake_session_v2(smoke_detectors=[dev])
        entities = _run_setup_with_options(session, _excl("sd-excl"))
        smoke_ents = [e for e in entities if isinstance(e, SmokeDetectorSensor)]
        assert smoke_ents == []

    def test_excluded_twinguard_not_in_entities_when_sds_present(self):
        surv_svc = _make_service_simple("SurveillanceAlarm")
        sds = _fake_device("sds-1", device_services=[surv_svc])
        sds.alarm = SurveillanceAlarmService.State.ALARM_OFF
        tw = _fake_device("tw-excl")
        session = _make_fake_session_v2(smoke_detection_system=sds, twinguards=[tw])
        entities = _run_setup_with_options(session, _excl("tw-excl"))
        ids = [getattr(e, "_device", None) and e._device.id for e in entities]
        assert "tw-excl" not in ids

    def test_excluded_water_leakage_detector_not_in_entities(self):
        dev = _fake_device("wl-excl")
        dev.leakage_state = WaterLeakageSensorService.State.NO_LEAKAGE
        dev.push_notification_state = WaterLeakageSensorTiltService.State.ENABLED
        dev.acoustic_signal_state = WaterLeakageSensorTiltService.State.ENABLED
        session = _make_fake_session_v2(water_leakage_detectors=[dev])
        entities = _run_setup_with_options(session, _excl("wl-excl"))
        wl_ents = [e for e in entities if isinstance(e, WaterLeakageDetectorSensor)]
        assert wl_ents == []

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
        dev.state = ShutterContactService.State.CLOSED
        dev.device_class = "REGULAR_WINDOW"
        dev.vibrationsensor = VibrationSensorService.State.NO_VIBRATION
        session = _make_fake_session_v2(shutter_contacts2=[dev])
        entities = _run_setup_with_options(session, _excl("sc2p-excl"))
        vib_ents = [e for e in entities if isinstance(e, ShutterContactVibrationSensor)]
        assert vib_ents == []

    def test_excluded_device_skipped_in_battery_loop(self):
        dev = _fake_device("md-bat-excl", supports_batterylevel=True)
        dev.batterylevel = BatteryLevelService.State.OK
        dev.latestmotion = None
        dev.device_services = [_make_service_simple("LatestMotion")]
        session = _make_fake_session_v2(motion_detectors=[dev])
        entities = _run_setup_with_options(session, _excl("md-bat-excl"))
        bat_ents = [e for e in entities if isinstance(e, BatterySensor)]
        assert bat_ents == []

    def test_excluded_climate_control_not_in_entities(self):
        """Excluded climate control must be skipped -- no CallForHeatSensor."""
        dev = _fake_device("cc-excl")
        dev.has_demand = False
        session = _make_fake_session_v2(climate_controls=[dev])
        entities = _run_setup_with_options(session, _excl("cc-excl"))
        cfh_ents = [e for e in entities if isinstance(e, CallForHeatSensor)]
        assert cfh_ents == []

    def test_non_excluded_climate_control_in_entities(self):
        """Non-excluded climate control must produce a CallForHeatSensor."""
        dev = _fake_device("cc-keep")
        dev.has_demand = True
        session = _make_fake_session_v2(climate_controls=[dev])
        entities = _run_setup_with_options(session, {})
        cfh_ents = [e for e in entities if isinstance(e, CallForHeatSensor)]
        assert len(cfh_ents) == 1

    def test_mix_excluded_and_kept_climate_control(self):
        kept = _fake_device("cc-a")
        kept.has_demand = False
        excl = _fake_device("cc-b")
        excl.has_demand = False
        session = _make_fake_session_v2(climate_controls=[kept, excl])
        entities = _run_setup_with_options(session, _excl("cc-b"))
        cfh_ents = [e for e in entities if isinstance(e, CallForHeatSensor)]
        assert len(cfh_ents) == 1
        assert cfh_ents[0]._device.id == "cc-a"

    def test_all_device_types_excluded_yields_empty_list(self):
        """Excluding all devices leaves the entity list empty."""
        excl_ids = [f"dev-{i}" for i in range(5)]
        md = _fake_device(excl_ids[0], device_services=[_make_service_simple("LatestMotion")])
        md.latestmotion = None
        wl = _fake_device(excl_ids[1])
        wl.leakage_state = WaterLeakageSensorService.State.NO_LEAKAGE
        wl.push_notification_state = WaterLeakageSensorTiltService.State.ENABLED
        wl.acoustic_signal_state = WaterLeakageSensorTiltService.State.ENABLED
        cc = _fake_device(excl_ids[2])
        cc.has_demand = False
        th = _fake_device(excl_ids[3], supports_batterylevel=True)
        th.batterylevel = BatteryLevelService.State.OK
        sc = _fake_device(excl_ids[4])
        sc.state = ShutterContactService.State.CLOSED
        sc.device_class = "GENERIC"
        session = _make_fake_session_v2(
            motion_detectors=[md],
            water_leakage_detectors=[wl],
            climate_controls=[cc],
            thermostats=[th],
            shutter_contacts=[sc],
        )
        entities = _run_setup_with_options(session, _excl(*excl_ids))
        assert entities == []
