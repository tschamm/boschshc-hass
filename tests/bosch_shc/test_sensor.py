"""Unit tests for sensor.py entity classes and async_setup_entry.

Consolidated from 11 previously-fragmented sensor test files (test_battery_level,
test_humidity_none_guard, test_humidity_roomthermostat, test_illuminance,
test_sensor_comm_quality, test_sensor_diag_339, test_sensor_extra_coverage,
test_sensor_extra2_coverage, test_sensor_new_entities, test_sensor_setup,
test_sensor_unit) plus platform-specific blocks pulled from several
multi-platform coverage-gap files (siren/APK/MD2/battery gap tests that
happen to exercise sensor.py entities). Pattern: bypass __init__ via
Cls.__new__(Cls) and inject a fake device via SimpleNamespace/MagicMock (no
HA harness, no tests.common); a smaller subset exercises the real __init__
chain and async_setup_entry via asyncio.run with async_migrate_to_new_unique_id
patched to a no-op.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from boschshcpy import CommunicationQualityService
from boschshcpy.services_impl import (
    AirQualityLevelService,
    BatteryLevelService,
    ValveTappetService,
)
from boschshcpy.zigbee_routing import SHCZigbeeRoutingInfo
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import (
    CONCENTRATION_PARTS_PER_MILLION,
    LIGHT_LUX,
    PERCENTAGE,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.helpers.entity import EntityCategory

from custom_components.bosch_shc.const import (
    OPT_DIAGNOSTIC_ENTITIES,
    OPT_EXCLUDED_DEVICES,
)
from custom_components.bosch_shc.sensor import (
    AirQualitySensor,
    BatteryLevelSensor,
    CommunicationQualitySensor,
    DetectionStateSensor,
    EmmaPowerSensor,
    EnergySensor,
    EnergyYieldSensor,
    HumidityRatingSensor,
    HumiditySensor,
    IlluminanceLevelSensor,
    KeypadTriggerSensor,
    NextSetpointTemperatureSensor,
    PowerSensor,
    PowerYieldSensor,
    PresenceSimulationRunningEndSensor,
    PresenceSimulationRunningStartSensor,
    PurityRatingSensor,
    PuritySensor,
    ReferenceMovingTimeBottomToTopSensor,
    ReferenceMovingTimeTopToBottomSensor,
    SHCOpenWindowsSensor,
    SirenBatterySensor,
    SirenMainPowerSensor,
    SirenSolarChargingSensor,
    TemperatureRatingSensor,
    TemperatureSensor,
    TerminalTemperatureSensor,
    TwinguardCombinedRatingSensor,
    TwinguardDescriptionSensor,
    ValveTappetSensor,
    WalkStateSensor,
    ZigbeeRoutingQualitySensor,
    async_setup_entry,
)

# ===========================================================================
# Shared helpers
#
# NOTE on naming collisions resolved during consolidation: several source
# files defined a same-named helper (_fake_device, _make_fake_session,
# _run_setup, _make_sensor, _emma, ENTRY_ID, _PATCH/_PATCH_MIGRATE) with
# DIFFERENT bodies/defaults. Identical ones were deduped to a single copy;
# non-identical ones were renamed with a distinguishing suffix (_ne, _excl,
# _for_battery_session, etc.) and every call site within that section was
# updated to match. See the report back to the orchestrator for the full list.
# ===========================================================================

ENTRY_ID = "entry-001"

_PATCH_MIGRATE = "custom_components.bosch_shc.sensor.async_migrate_to_new_unique_id"


async def _noop_migrate(hass, platform, device, attr_name=None, old_unique_id=None):
    return None


def _new(cls):
    return cls.__new__(cls)


def _run(coro):
    return asyncio.run(coro)


def _fake_md2(**kwargs):
    """Fake MD2 (motion detector gen2) device.

    Identical helper previously duplicated verbatim in
    test_apk_walktest_and_sensitivity.py and
    test_md2_detection_tamper_pollcontrol.py — deduped to one copy.
    """
    defaults = dict(
        name="MD2", id="md1", root_device_id="root1", serial="SER1",
        supports_silentmode=False, supports_batterylevel=False,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


_FAKE_DEVICE = SimpleNamespace(
    root_device_id="root-1",
    id="hdm:ZigBee:dev1",
    name="Schlafzimmerfenster",
    status="AVAILABLE",
)


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
    call can wire them onto entry.runtime_data (the modern storage location —
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


def _fake_device(
    name: str = "TestDevice",
    device_id: str = "hdm:Test:0001",
    root_device_id: str = "root-001",
    serial: str = "SER001",
    supports_batterylevel: bool = False,
    **extra: Any,
) -> SimpleNamespace:
    """Minimal fake device accepted by SHCEntity.__init__ and all sensor __init__s."""
    return SimpleNamespace(
        name=name,
        id=device_id,
        root_device_id=root_device_id,
        serial=serial,
        device_services=[],
        supports_batterylevel=supports_batterylevel,
        **extra,
    )


def _make_fake_session(
    *,
    thermostats=(),
    wallthermostats=(),
    roomthermostats=(),
    twinguards=(),
    climate_controls=(),
    smart_plugs=(),
    light_switches_bsm=(),
    micromodule_light_controls=(),
    shutter_controls=(),
    micromodule_shutter_controls=(),
    micromodule_blinds=(),
    smart_plugs_compact=(),
    motion_detectors=(),
    motion_detectors2=(),
    shutter_contacts=(),
    shutter_contacts2=(),
    smoke_detectors=(),
    universal_switches=(),
    water_leakage_detectors=(),
    emma=None,
):
    if emma is None:
        emma = _fake_device(
            name="EMMA",
            device_id="com.bosch.tt.emma.applink",
            supports_batterylevel=False,
        )

    return SimpleNamespace(
        device_helper=SimpleNamespace(
            thermostats=list(thermostats),
            wallthermostats=list(wallthermostats),
            roomthermostats=list(roomthermostats),
            twinguards=list(twinguards),
            climate_controls=list(climate_controls),
            smart_plugs=list(smart_plugs),
            light_switches_bsm=list(light_switches_bsm),
            micromodule_light_controls=list(micromodule_light_controls),
            shutter_controls=list(shutter_controls),
            micromodule_shutter_controls=list(micromodule_shutter_controls),
            micromodule_blinds=list(micromodule_blinds),
            smart_plugs_compact=list(smart_plugs_compact),
            motion_detectors=list(motion_detectors),
            motion_detectors2=list(motion_detectors2),
            shutter_contacts=list(shutter_contacts),
            shutter_contacts2=list(shutter_contacts2),
            smoke_detectors=list(smoke_detectors),
            universal_switches=list(universal_switches),
            water_leakage_detectors=list(water_leakage_detectors),
        ),
        emma=emma,
    )


def _run_setup(session, options=None):
    """Run async_setup_entry with a fake session. Returns list of added entities."""
    hass = SimpleNamespace()
    config_entry = SimpleNamespace(options=options or {}, entry_id=ENTRY_ID)
    config_entry.runtime_data = SimpleNamespace(session=session)
    collected: list = []

    def _add_entities(entity_list):
        collected.extend(entity_list)

    async def _inner():
        with patch(_PATCH_MIGRATE, side_effect=_noop_migrate):
            await async_setup_entry(hass, config_entry, _add_entities)

    asyncio.run(_inner())
    return collected


# --- "_ne" (new-entities) variants: kept distinct from _fake_device/_make_fake_session/
#     _run_setup above because their defaults differ (root_id vs root_device_id key,
#     no climate_controls/shutter_controls, options default, ENTRY_ID value) ---

ENTRY_ID_NE = "E1"


def _fake_device_ne(name="Test", device_id="dev1", root_id="root1", **extra):
    return SimpleNamespace(
        name=name,
        id=device_id,
        root_device_id=root_id,
        serial=device_id,
        device_services=[],
        supports_batterylevel=False,
        **extra,
    )


def _make_fake_session_ne(
    *,
    thermostats=(),
    wallthermostats=(),
    roomthermostats=(),
    twinguards=(),
    smart_plugs=(),
    light_switches_bsm=(),
    micromodule_light_controls=(),
    micromodule_shutter_controls=(),
    micromodule_blinds=(),
    smart_plugs_compact=(),
    motion_detectors=(),
    motion_detectors2=(),
    shutter_contacts=(),
    shutter_contacts2=(),
    smoke_detectors=(),
    universal_switches=(),
    water_leakage_detectors=(),
    emma=None,
):
    if emma is None:
        emma = SimpleNamespace(
            name="EMMA",
            id="com.bosch.tt.emma.applink",
            root_device_id="root-emma",
            device_services=[],
            supports_batterylevel=False,
        )
    return SimpleNamespace(
        device_helper=SimpleNamespace(
            thermostats=list(thermostats),
            wallthermostats=list(wallthermostats),
            roomthermostats=list(roomthermostats),
            twinguards=list(twinguards),
            smart_plugs=list(smart_plugs),
            light_switches_bsm=list(light_switches_bsm),
            micromodule_light_controls=list(micromodule_light_controls),
            micromodule_shutter_controls=list(micromodule_shutter_controls),
            micromodule_blinds=list(micromodule_blinds),
            smart_plugs_compact=list(smart_plugs_compact),
            motion_detectors=list(motion_detectors),
            motion_detectors2=list(motion_detectors2),
            shutter_contacts=list(shutter_contacts),
            shutter_contacts2=list(shutter_contacts2),
            smoke_detectors=list(smoke_detectors),
            universal_switches=list(universal_switches),
            water_leakage_detectors=list(water_leakage_detectors),
        ),
        emma=emma,
    )


def _run_setup_ne(session, options=None):
    hass = SimpleNamespace()
    config_entry = SimpleNamespace(
        options=options or {"opt_diagnostic_entities": True},
        entry_id=ENTRY_ID_NE,
    )
    config_entry.runtime_data = SimpleNamespace(session=session)
    collected = []

    def _add_entities(entity_list):
        collected.extend(entity_list)

    async def _inner():
        with patch(_PATCH_MIGRATE, side_effect=_noop_migrate):
            await async_setup_entry(hass, config_entry, _add_entities)

    asyncio.run(_inner())
    return collected


# --- "_excl" (device-excluded-continue-branches) variants: dict-style session
#     builder, distinct from both variants above ---


def _fake_device_excl(device_id="dev1", name="FakeDev", root_device_id="root1",
                       serial="ser1", supports_batterylevel=False, **extra):
    return SimpleNamespace(
        id=device_id,
        name=name,
        root_device_id=root_device_id,
        serial=serial,
        device_services=[],
        supports_batterylevel=supports_batterylevel,
        **extra,
    )


def _emma_excl():
    return _fake_device_excl(
        device_id="com.bosch.tt.emma.applink",
        name="EMMA",
        root_device_id="shc-root",
    )


def _make_fake_session_excl(**lists):
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
        emma=lists.get("emma", _emma_excl()),
    )


def _run_setup_with_options(session, options):
    """Run async_setup_entry with custom options dict. Returns list of entities."""
    hass = SimpleNamespace()
    config_entry = SimpleNamespace(options=options, entry_id="E1")
    config_entry.runtime_data = SimpleNamespace(session=session)
    collected = []

    def _add_entities(entity_list):
        collected.extend(entity_list)

    async def _inner():
        with patch(_PATCH_MIGRATE, side_effect=_noop_migrate):
            await async_setup_entry(hass, config_entry, _add_entities)

    asyncio.run(_inner())
    return collected


# --- battery-level-creation-via-setup session helpers (gaps_coverage2.py origin) ---


def _emma_for_battery_session():
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
        emma=lists.get("emma", _emma_for_battery_session()),
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
    hass = SimpleNamespace()
    config_entry = SimpleNamespace(options=options, entry_id="E1")
    config_entry.runtime_data = SimpleNamespace(
        session=session, shc_device=None, title="Test SHC"
    )
    collected = []

    async def _inner():
        with patch(_PATCH_MIGRATE, side_effect=_noop_migrate):
            await async_setup_entry(hass, config_entry, lambda e: collected.extend(e))

    asyncio.run(_inner())
    return collected


def _setup_sensors(md2_list):
    """Run sensor.py's async_setup_entry with a single list of MD2 devices."""
    entry_id = "E1"
    emma = SimpleNamespace(
        name="EMMA",
        id="com.bosch.tt.emma.applink",
        root_device_id="root_emma",
        serial="EMMA_SER",
        supports_batterylevel=False,
    )
    device_helper = SimpleNamespace(
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
        motion_detectors=[],
        motion_detectors2=list(md2_list),
        shutter_contacts=[],
        shutter_contacts2=[],
        smoke_detectors=[],
        universal_switches=[],
        water_leakage_detectors=[],
    )
    session = SimpleNamespace(device_helper=device_helper, emma=emma)
    hass = SimpleNamespace()
    shc_device = SimpleNamespace(
        name="SHC",
        id="shc",
        identifiers={("bosch_shc", "shc")},
        manufacturer="Bosch",
        model="SHC",
    )
    entry = SimpleNamespace(options={}, entry_id=entry_id, async_on_unload=MagicMock())
    entry.runtime_data = SimpleNamespace(
        session=session, shc_device=shc_device, title="Test SHC"
    )
    entities = []

    async def _run_inner():
        with patch(_PATCH_MIGRATE, new=AsyncMock(return_value=None)):
            await async_setup_entry(hass, entry, lambda e, *a, **k: entities.extend(e))

    asyncio.run(_run_inner())
    return entities



# ===========================================================================
# Shared helper: _BadEnum (used by AirQualitySensor/TemperatureRatingSensor/
# HumidityRatingSensor/PurityRatingSensor/CommunicationQualitySensor error guards)
# ===========================================================================


class _BadEnum:
    """Simulates an enum whose .name property raises ValueError (or another
    exception class, configurable)."""

    def __init__(self, exc_class=ValueError, message="unknown"):
        self._exc_class = exc_class
        self._message = message

    @property
    def name(self):
        raise self._exc_class(self._message)



# ===========================================================================
# TemperatureSensor / TerminalTemperatureSensor
# ===========================================================================


def _temp_sensor(temperature):
    s = TemperatureSensor.__new__(TemperatureSensor)
    s._device = SimpleNamespace(temperature=temperature)
    return s


class TestTemperatureSensor:
    def test_native_value(self):
        assert _temp_sensor(21.5).native_value == 21.5

    def test_native_value_zero(self):
        assert _temp_sensor(0.0).native_value == 0.0

    def test_native_value_negative(self):
        assert _temp_sensor(-5.0).native_value == -5.0

    def test_device_class(self):
        assert _temp_sensor(22.0).device_class == SensorDeviceClass.TEMPERATURE

    def test_unit(self):
        assert _temp_sensor(22.0).native_unit_of_measurement == UnitOfTemperature.CELSIUS

    def test_state_class(self):
        assert _temp_sensor(22.0).state_class == SensorStateClass.MEASUREMENT


def _terminal_temp_sensor(value):
    s = TerminalTemperatureSensor.__new__(TerminalTemperatureSensor)
    s._device = SimpleNamespace(terminal_temperature=value)
    return s


class TestTerminalTemperatureSensor:
    def test_native_value(self):
        assert _terminal_temp_sensor(20.6).native_value == 20.6

    def test_device_class_temperature(self):
        assert (
            _terminal_temp_sensor(20.6).device_class
            == SensorDeviceClass.TEMPERATURE
        )

    def test_unit_celsius(self):
        assert (
            _terminal_temp_sensor(20.6).native_unit_of_measurement
            == UnitOfTemperature.CELSIUS
        )


class TestTerminalTemperatureSensorInit:
    """sensor.py lines 511-512: TerminalTemperatureSensor.__init__."""

    def test_terminal_temperature_sensor_init(self):
        """Lines 509-514: real __init__ sets unique_id."""
        dev = _fake_dev("t1")
        sensor = TerminalTemperatureSensor(dev, "entry1")
        assert "terminal_temperature" in sensor._attr_unique_id


class TestSensorTerminalTempSetup:
    """Line 120: TerminalTemperatureSensor added when terminal_temperature is not None."""

    def _run_sensor_setup(self, roomthermostats, options=None):
        """TerminalTemperatureSensor is created in the wallthermostats+roomthermostats loop."""
        dh = MagicMock()
        dh.thermostats = []
        dh.roomthermostats = roomthermostats
        dh.wallthermostats = []
        dh.motion_detectors = []
        dh.motion_detectors2 = []
        dh.shutter_contacts = []
        dh.shutter_contacts2 = []
        dh.smoke_detectors = []
        dh.twinguards = []
        dh.smart_plugs = []
        dh.smart_plugs_compact = []
        dh.light_switches_bsm = []
        dh.micromodule_light_controls = []
        dh.micromodule_shutter_controls = []
        dh.micromodule_blinds = []
        dh.universal_switches = []
        dh.water_leakage_detectors = []
        dh.outdoor_sirens = []

        session = MagicMock()
        session.device_helper = dh
        session.emma = _fake_dev("emma1")  # prevent MagicMock EmmaPowerSensor

        hass = _fake_hass(session=session)
        entry = _fake_entry(hass=hass, options=options or {})

        with patch(_PATCH_MIGRATE, new_callable=AsyncMock):
            collected = []
            _run(async_setup_entry(hass, entry, lambda ents, **kw: collected.extend(ents)))
        return collected

    def test_terminal_temperature_sensor_added_when_present(self):
        """Line 120: TerminalTemperatureSensor appended when terminal_temperature not None."""
        dev = _fake_dev("t1", temperature=20.0, terminal_temperature=18.0,
                        supports_humidity=False, supports_batterylevel=False)
        collected = self._run_sensor_setup([dev])
        assert any(isinstance(e, TerminalTemperatureSensor) for e in collected)

    def test_terminal_temperature_sensor_not_added_when_absent(self):
        """Line 119: terminal_temperature=None → sensor not added."""
        dev = _fake_dev("t1", temperature=20.0, terminal_temperature=None,
                        supports_humidity=False, supports_batterylevel=False)
        collected = self._run_sensor_setup([dev])
        assert not any(isinstance(e, TerminalTemperatureSensor) for e in collected)


# ===========================================================================
# HumiditySensor
# ===========================================================================


def _humidity_sensor(humidity):
    s = HumiditySensor.__new__(HumiditySensor)
    s._device = SimpleNamespace(humidity=humidity)
    return s


class TestHumiditySensor:
    def test_native_value(self):
        assert _humidity_sensor(55.0).native_value == 55.0

    def test_native_value_zero(self):
        assert _humidity_sensor(0).native_value == 0

    def test_device_class(self):
        assert _humidity_sensor(55.0).device_class == SensorDeviceClass.HUMIDITY

    def test_unit(self):
        assert _humidity_sensor(55.0).native_unit_of_measurement == PERCENTAGE

    def test_state_class(self):
        assert _humidity_sensor(55.0).state_class == SensorStateClass.MEASUREMENT


def _make_humidity_sensor(humidity_value: float) -> HumiditySensor:
    """Build a HumiditySensor bypassing SHCEntity.__init__ (no HASS required)."""
    sensor = HumiditySensor.__new__(HumiditySensor)
    sensor._device = SimpleNamespace(
        humidity=humidity_value,
        name="Room Thermostat",
        id="roomthermostat-id",
        root_device_id="shc-root-id",
    )
    sensor._attr_name = "Room Thermostat Humidity"
    sensor._attr_unique_id = "shc-root-id_roomthermostat-id_humidity"
    return sensor


class TestHumiditySensorContract:
    """Regression test for issue #274 (Room Thermostat humidity wiring).

    Pin the HumiditySensor API so wiring regressions are caught immediately.
    Both device_helper.wallthermostats (THB / BWTH / BWTH24 -> SHCWallThermostat)
    and device_helper.roomthermostats (RTH2_BAT / RTH2_230 -> SHCRoomThermostat2)
    are iterated at sensor.py:60-80 and each receives a HumiditySensor.
    SHCRoomThermostat2 inherits SHCWallThermostat which mixes in _HumidityLevel,
    so .humidity is present on all covered models.
    """

    def test_native_value_float(self):
        """native_value returns the float humidity from the device."""
        sensor = _make_humidity_sensor(55.0)
        assert sensor.native_value == 55.0

    def test_native_value_integer_compatible(self):
        """Bosch API may return integer humidity; sensor must pass it through."""
        sensor = _make_humidity_sensor(60)
        assert sensor.native_value == 60

    def test_device_class_is_humidity(self):
        """device_class must be HUMIDITY so HA renders the correct icon and unit."""
        sensor = _make_humidity_sensor(50.0)
        assert sensor.device_class == SensorDeviceClass.HUMIDITY

    def test_native_unit_is_percent(self):
        """Unit of measurement must be % (PERCENTAGE)."""
        sensor = _make_humidity_sensor(50.0)
        assert sensor.native_unit_of_measurement == "%"

    def test_zero_humidity(self):
        """Edge case: 0 % humidity must not be falsy-filtered."""
        sensor = _make_humidity_sensor(0.0)
        assert sensor.native_value == 0.0

    def test_max_humidity(self):
        """Edge case: 100 % humidity."""
        sensor = _make_humidity_sensor(100.0)
        assert sensor.native_value == 100.0


class TestRoomThermostatModelsHaveHumidity:
    """Document which boschshcpy model classes expose .humidity.

    SHCWallThermostat and SHCRoomThermostat2 both mix in _HumidityLevel.
    If either loses that mixin the AttributeError below will surface the gap.
    """

    def test_wall_thermostat_has_humidity_attribute(self):
        """SHCWallThermostat (THB / BWTH / BWTH24) must expose .humidity."""
        from boschshcpy.models_impl import SHCWallThermostat
        assert hasattr(SHCWallThermostat, "humidity"), (
            "SHCWallThermostat lost the .humidity property — "
            "check _HumidityLevel mixin inheritance"
        )

    def test_room_thermostat2_has_humidity_attribute(self):
        """SHCRoomThermostat2 (RTH2_BAT / RTH2_230) must expose .humidity."""
        from boschshcpy.models_impl import SHCRoomThermostat2
        assert hasattr(SHCRoomThermostat2, "humidity"), (
            "SHCRoomThermostat2 lost the .humidity property — "
            "it must inherit SHCWallThermostat which mixes in _HumidityLevel"
        )

    def test_room_thermostat2_inherits_wall_thermostat(self):
        """SHCRoomThermostat2 must subclass SHCWallThermostat for humidity to work."""
        from boschshcpy.models_impl import SHCRoomThermostat2, SHCWallThermostat
        assert issubclass(SHCRoomThermostat2, SHCWallThermostat), (
            "SHCRoomThermostat2 no longer inherits SHCWallThermostat — "
            "humidity sensor wiring in sensor.py:60-80 will silently fail"
        )


def _make_humidity_guard_device(supports_humidity=True, humidity=55.0):
    return SimpleNamespace(
        id="dev-1",
        root_device_id="root-1",
        name="Thermostat",
        manufacturer="Bosch",
        device_model="ROOM_THERMOSTAT",
        status="AVAILABLE",
        deleted=False,
        supports_humidity=supports_humidity,
        humidity=humidity,
        temperature=21.0,
        supports_batterylevel=False,
    )


def _make_humidity_guard_device_no_supports_attr(humidity=55.0):
    """Simulate an older lib without supports_humidity attribute."""
    dev = SimpleNamespace(
        id="dev-old",
        root_device_id="root-old",
        name="Old Thermostat",
        manufacturer="Bosch",
        device_model="ROOM_THERMOSTAT",
        status="AVAILABLE",
        deleted=False,
        humidity=humidity,
        temperature=21.0,
        supports_batterylevel=False,
    )
    # Explicitly ensure supports_humidity is NOT set
    assert not hasattr(dev, "supports_humidity")
    return dev


class TestHumiditySensorNoneGuard:
    """Verifies that HumiditySensor is NOT created when the device's
    supports_humidity property returns False (lib >= 0.2.122 adds this
    attribute to _HumidityLevel). Also verifies the getattr fallback: old lib
    without supports_humidity still creates the sensor (True default = safe).
    """

    def test_sensor_skipped_when_supports_humidity_false(self):
        """HumiditySensor must not be appended when supports_humidity is False."""
        dev = _make_humidity_guard_device(supports_humidity=False)
        # Simulate the guard logic from sensor.py async_setup_entry
        entities = []
        if getattr(dev, "supports_humidity", True):
            entities.append("HumiditySensor")
        assert entities == [], "HumiditySensor must not be created when supports_humidity=False"

    def test_sensor_created_when_supports_humidity_true(self):
        """HumiditySensor is created when supports_humidity is True."""
        dev = _make_humidity_guard_device(supports_humidity=True)
        entities = []
        if getattr(dev, "supports_humidity", True):
            entities.append("HumiditySensor")
        assert entities == ["HumiditySensor"]

    def test_sensor_created_when_attribute_absent(self):
        """Old lib without supports_humidity: getattr default=True → sensor created."""
        dev = _make_humidity_guard_device_no_supports_attr()
        entities = []
        if getattr(dev, "supports_humidity", True):
            entities.append("HumiditySensor")
        assert entities == ["HumiditySensor"], "Old lib without supports_humidity must fall back to creating sensor"

    def test_guard_uses_getattr_not_direct_access(self):
        """Verify the guard does not raise AttributeError on old-lib devices."""
        dev = _make_humidity_guard_device_no_supports_attr()
        # Direct attribute access would raise; getattr must not
        try:
            _ = getattr(dev, "supports_humidity", True)
        except AttributeError:
            raise AssertionError("getattr must never raise AttributeError with a default")


# ===========================================================================
# PuritySensor
# ===========================================================================


def _purity_sensor(purity):
    s = PuritySensor.__new__(PuritySensor)
    s._device = SimpleNamespace(purity=purity)
    return s


class TestPuritySensor:
    def test_native_value(self):
        assert _purity_sensor(800).native_value == 800

    def test_native_value_high(self):
        assert _purity_sensor(2000).native_value == 2000

    def test_unit_is_ppm(self):
        assert _purity_sensor(800).native_unit_of_measurement == CONCENTRATION_PARTS_PER_MILLION

    def test_state_class(self):
        assert _purity_sensor(800).state_class == SensorStateClass.MEASUREMENT

    def test_device_class(self):
        # Bosch "purity" is air-purity/VOC ppm, not CO2 — no device_class (#204),
        # matching HA Core's own bosch_shc integration.
        assert _purity_sensor(400).device_class is None

    def test_unit(self):
        assert _purity_sensor(400).native_unit_of_measurement == CONCENTRATION_PARTS_PER_MILLION


# ===========================================================================
# AirQualitySensor
# ===========================================================================


def _air_quality_sensor(combined_rating, description="Good air"):
    s = AirQualitySensor.__new__(AirQualitySensor)
    s._device = SimpleNamespace(combined_rating=combined_rating, description=description)
    return s


class TestAirQualitySensor:
    def test_native_value_good(self):
        rating = AirQualityLevelService.RatingState.GOOD
        s = _air_quality_sensor(rating)
        assert s.native_value == "GOOD"

    def test_native_value_medium(self):
        rating = AirQualityLevelService.RatingState.MEDIUM
        s = _air_quality_sensor(rating)
        assert s.native_value == "MEDIUM"

    def test_native_value_bad(self):
        rating = AirQualityLevelService.RatingState.BAD
        s = _air_quality_sensor(rating)
        assert s.native_value == "BAD"

    def test_extra_state_attributes(self):
        rating = AirQualityLevelService.RatingState.GOOD
        s = _air_quality_sensor(rating, description="Fresh")
        assert s.extra_state_attributes == {"rating_description": "Fresh"}

    def test_native_value_unknown_rating_returns_none(self):
        s = AirQualitySensor.__new__(AirQualitySensor)

        class _BadEnum:
            @property
            def name(self):
                raise ValueError("unknown_rating")

        s._device = SimpleNamespace(combined_rating=_BadEnum(), name="test")
        assert s.native_value is None


class _FakeAirQualityService:
    """Fake _airqualitylevel_service that returns a concrete comfortZone."""
    def __init__(self, comfort_zone_value):
        self.comfortZone = comfort_zone_value


class TestAirQualitySensorComfortZoneGuards:
    """AirQualitySensor.extra_state_attributes — comfortZone is not None
    (sensor.py lines 461-462), plus error/absent-service fallback paths.

    NOTE: this class was named TestAirQualitySensorComfortZone in its source
    file (test_sensor_extra2_coverage.py) which collided with an identically
    named but differently-implemented class from test_sensor_new_entities.py
    (kept below as TestAirQualitySensorComfortZone). Renamed here to disambiguate
    — bodies are not vacuous duplicates (different fixtures/edge cases covered).
    """

    def _make_sensor(self, comfort_zone=None, description="Good air"):
        s = AirQualitySensor.__new__(AirQualitySensor)
        service = _FakeAirQualityService(comfort_zone)
        s._device = SimpleNamespace(
            _airqualitylevel_service=service,
            description=description,
        )
        return s

    def test_comfort_zone_included_when_not_none(self):
        """When comfortZone is a non-None value, it appears in extra_state_attributes."""
        s = self._make_sensor(comfort_zone="COMFORT", description="Very Good")
        attrs = s.extra_state_attributes
        assert "comfort_zone" in attrs
        assert attrs["comfort_zone"] == "COMFORT"

    def test_comfort_zone_value_passed_through(self):
        """ComfortZone value (any type) is preserved as-is."""
        s = self._make_sensor(comfort_zone=42, description="Desc")
        attrs = s.extra_state_attributes
        assert attrs["comfort_zone"] == 42

    def test_comfort_zone_bool_value_true_included(self):
        """True is not None, so it should be included."""
        s = self._make_sensor(comfort_zone=True, description="x")
        attrs = s.extra_state_attributes
        assert "comfort_zone" in attrs
        assert attrs["comfort_zone"] is True

    def test_comfort_zone_none_not_in_attrs(self):
        """When comfortZone is None, the key must NOT appear."""
        s = self._make_sensor(comfort_zone=None, description="Neutral")
        attrs = s.extra_state_attributes
        assert "comfort_zone" not in attrs

    def test_rating_description_always_present(self):
        """rating_description must always be in the attributes."""
        s = self._make_sensor(comfort_zone="GOOD", description="Fresh")
        attrs = s.extra_state_attributes
        assert attrs["rating_description"] == "Fresh"

    def test_no_service_attr_no_comfort_zone(self):
        """If _airqualitylevel_service is absent, comfort_zone key is absent."""
        s = AirQualitySensor.__new__(AirQualitySensor)
        s._device = SimpleNamespace(description="Neutral")
        attrs = s.extra_state_attributes
        assert "comfort_zone" not in attrs
        assert attrs["rating_description"] == "Neutral"

    def test_service_comfortzone_attribute_error_falls_back(self):
        """AttributeError on service.comfortZone → no comfort_zone key (fallback)."""
        class _BadService:
            @property
            def comfortZone(self):
                raise AttributeError("no such attr")

        s = AirQualitySensor.__new__(AirQualitySensor)
        s._device = SimpleNamespace(
            _airqualitylevel_service=_BadService(),
            description="test",
        )
        attrs = s.extra_state_attributes
        assert "comfort_zone" not in attrs


class TestAirQualitySensorComfortZone:
    """AirQualitySensor.extra_state_attributes exposes comfort_zone when present.

    (from test_sensor_new_entities.py — see TestAirQualitySensorComfortZoneGuards
    above for the renamed collision partner.)
    """

    def _make_air_quality_sensor(self, comfort_zone=None, has_service=True):
        svc = None
        if has_service:
            svc = SimpleNamespace(comfortZone=comfort_zone)

        dev = SimpleNamespace(
            name="TwinGuard",
            id="hdm:ZigBee:tw1",
            root_device_id="root-tw",
            combined_rating=SimpleNamespace(name="GOOD"),
            description="Air quality is good.",
            _airqualitylevel_service=svc,
        )
        sensor = AirQualitySensor.__new__(AirQualitySensor)
        sensor._device = dev
        sensor._attr_name = "Air Quality"
        sensor._attr_unique_id = f"{dev.root_device_id}_{dev.id}_airquality"
        return sensor

    def test_comfort_zone_present_in_attrs_when_not_none(self):
        zone = {"temperatureMin": 18, "temperatureMax": 24}
        sensor = self._make_air_quality_sensor(comfort_zone=zone)
        attrs = sensor.extra_state_attributes
        assert "comfort_zone" in attrs
        assert attrs["comfort_zone"] == zone

    def test_comfort_zone_absent_from_attrs_when_none(self):
        """When comfortZone is None (not present in firmware response), omit key."""
        sensor = self._make_air_quality_sensor(comfort_zone=None)
        attrs = sensor.extra_state_attributes
        assert "comfort_zone" not in attrs

    def test_comfort_zone_absent_when_no_service(self):
        """When _airqualitylevel_service is None, omit comfort_zone."""
        sensor = self._make_air_quality_sensor(has_service=False)
        attrs = sensor.extra_state_attributes
        assert "comfort_zone" not in attrs

    def test_rating_description_always_present(self):
        """rating_description must still be present alongside comfort_zone."""
        zone = {"temperatureMin": 19}
        sensor = self._make_air_quality_sensor(comfort_zone=zone)
        attrs = sensor.extra_state_attributes
        assert "rating_description" in attrs
        assert attrs["rating_description"] == "Air quality is good."

    def test_empty_dict_comfort_zone_included(self):
        """Empty dict from comfortZone → not None → key IS present in attrs.

        The implementation guards on 'is not None' (not on truthiness) so an
        empty dict is treated as a valid (though empty) comfort zone reading.
        """
        sensor = self._make_air_quality_sensor(comfort_zone={})
        attrs = sensor.extra_state_attributes
        assert "comfort_zone" in attrs
        assert attrs["comfort_zone"] == {}

    def test_comfort_zone_dict_with_data_included(self):
        """Non-empty dict is truthy → included in attrs."""
        zone = {"x": 1}
        sensor = self._make_air_quality_sensor(comfort_zone=zone)
        attrs = sensor.extra_state_attributes
        assert "comfort_zone" in attrs
        assert attrs["comfort_zone"] == zone


# ===========================================================================
# TemperatureRatingSensor
# ===========================================================================


def _temp_rating_sensor(rating):
    s = TemperatureRatingSensor.__new__(TemperatureRatingSensor)
    s._device = SimpleNamespace(temperature_rating=rating)
    return s


class TestTemperatureRatingSensor:
    def test_native_value_good(self):
        s = _temp_rating_sensor(AirQualityLevelService.RatingState.GOOD)
        assert s.native_value == "GOOD"

    def test_native_value_medium(self):
        s = _temp_rating_sensor(AirQualityLevelService.RatingState.MEDIUM)
        assert s.native_value == "MEDIUM"

    def test_native_value_bad(self):
        s = _temp_rating_sensor(AirQualityLevelService.RatingState.BAD)
        assert s.native_value == "BAD"


def _temp_rating_sensor_bad():
    s = TemperatureRatingSensor.__new__(TemperatureRatingSensor)
    s._device = SimpleNamespace(temperature_rating=_BadEnum(), name="test-twinguard")
    return s


class TestTemperatureRatingSensorErrorGuard:
    def test_value_error_returns_none(self):
        """ValueError on temperature_rating.name must return None."""
        s = _temp_rating_sensor_bad()
        assert s.native_value is None

    def test_normal_rating_returns_name(self):
        s = TemperatureRatingSensor.__new__(TemperatureRatingSensor)
        s._device = SimpleNamespace(
            temperature_rating=AirQualityLevelService.RatingState.MEDIUM,
            name="twinguard-1",
        )
        assert s.native_value == "MEDIUM"

# ===========================================================================
# CommunicationQualitySensor
# ===========================================================================


def _comm_quality_sensor(state):
    s = CommunicationQualitySensor.__new__(CommunicationQualitySensor)
    s._device = SimpleNamespace(communicationquality=state)
    return s


class TestCommunicationQualitySensor:
    # #339: native_value is now a lowercase, translatable slug.
    def test_native_value_good(self):
        state = CommunicationQualityService.State.GOOD
        assert _comm_quality_sensor(state).native_value == "good"

    def test_native_value_not_supported(self):
        state = CommunicationQualityService.State.NOT_SUPPORTED
        assert _comm_quality_sensor(state).native_value == "not_supported"

    def test_native_value_bad(self):
        state = CommunicationQualityService.State.BAD
        assert _comm_quality_sensor(state).native_value == "bad"

    def test_native_value_unknown(self):
        state = CommunicationQualityService.State.UNKNOWN
        assert _comm_quality_sensor(state).native_value == "unknown"

    def test_native_value_fetching(self):
        state = CommunicationQualityService.State.FETCHING
        assert _comm_quality_sensor(state).native_value == "fetching"

    def test_native_value_normal(self):
        state = CommunicationQualityService.State.NORMAL
        assert _comm_quality_sensor(state).native_value == "normal"


def _make_comm_quality_test_sensor(quality_obj):
    """Build a CommunicationQualitySensor with a fake device (ValueError/
    AttributeError guard tests, #339)."""
    sensor = CommunicationQualitySensor.__new__(CommunicationQualitySensor)
    sensor._device = SimpleNamespace(
        id="dev-1",
        root_device_id="root-1",
        name="Compact Plug",
        communicationquality=quality_obj,
    )
    sensor._attr_unique_id = "root-1_dev-1_communicationquality"
    return sensor


class _GoodQuality:
    """Valid quality enum-like — .name works fine."""
    @property
    def name(self):
        return "GOOD"


class _BadQuality:
    """Unknown quality — .name raises ValueError."""
    @property
    def name(self):
        raise ValueError("Unknown quality value 99")


class _NoneQuality:
    """Quality object whose .name raises AttributeError (missing service)."""
    @property
    def name(self):
        raise AttributeError("NoneType has no attribute 'name'")


class TestCommunicationQualitySensorValueGuard:
    """Unit tests for CommunicationQualitySensor.native_value ValueError guard.

    Verifies that unknown communicationquality values return None and log a
    warning instead of propagating ValueError.
    """

    def test_valid_quality_returns_slug(self):
        # #339: native_value is now a lowercase slug (translated for display).
        sensor = _make_comm_quality_test_sensor(_GoodQuality())
        assert sensor.native_value == "good"

    def test_value_error_returns_none_and_logs(self):
        sensor = _make_comm_quality_test_sensor(_BadQuality())
        with patch("custom_components.bosch_shc.sensor.LOGGER") as mock_log:
            result = sensor.native_value
        assert result is None
        mock_log.warning.assert_called_once()

    def test_attribute_error_returns_none_and_logs(self):
        sensor = _make_comm_quality_test_sensor(_NoneQuality())
        with patch("custom_components.bosch_shc.sensor.LOGGER") as mock_log:
            result = sensor.native_value
        assert result is None
        mock_log.warning.assert_called_once()


def _comm():
    return CommunicationQualitySensor.__new__(CommunicationQualitySensor)


def test_comm_quality_is_diagnostic_enum():
    """#339: CommunicationQuality is a Diagnostics-category ENUM sensor whose
    state is a lowercase, translatable slug (no more raw ALL-CAPS "GOOD"/"BAD")."""
    s = _comm()
    assert s.entity_category == EntityCategory.DIAGNOSTIC
    assert s.device_class == SensorDeviceClass.ENUM
    assert s.translation_key == "communication_quality"
    # options are lowercase slugs so HA can translate the displayed label
    assert all(o == o.lower() for o in s.options)


def test_comm_quality_native_value_is_lowercase_slug():
    s = _comm()
    s._device = SimpleNamespace(
        communicationquality=SimpleNamespace(name="GOOD"), name="plug"
    )
    assert s.native_value == "good"
    # the slug must be a declared option (else HA logs an "invalid state" warning)
    assert s.native_value in s.options


def _comm_sensor(comm_quality, name="test-plug"):
    s = CommunicationQualitySensor.__new__(CommunicationQualitySensor)
    s._device = SimpleNamespace(communicationquality=comm_quality, name=name)
    return s


class TestCommunicationQualitySensorErrorGuard:
    def test_value_error_returns_none(self):
        """ValueError on communicationquality.name must return None."""
        s = _comm_sensor(_BadEnum(ValueError, "bad value"))
        assert s.native_value is None

    def test_attribute_error_returns_none(self):
        """AttributeError (no .name attribute at all) must return None."""
        s = _comm_sensor(_BadEnum(AttributeError, "no name attr"))
        assert s.native_value is None

    def test_communicationquality_none_attribute_error_returns_none(self):
        """If communicationquality itself is None, .name raises AttributeError → None."""
        s = CommunicationQualitySensor.__new__(CommunicationQualitySensor)

        class _NoName:
            @property
            def name(self):
                raise AttributeError("no such attr")

        s._device = SimpleNamespace(communicationquality=_NoName(), name="plug-1")
        assert s.native_value is None


class TestShutterContact2CommQualitySetup:
    """CommunicationQuality for shutter_contacts2 (new sensor entity).

    NOTE: The sensor.py hasattr guard checks for 'communicationquality' on
    the device object. SimpleNamespace devices with the attribute pass; those
    without do not.
    """

    def test_shutter_contact2_with_cq_yields_comm_quality_sensor(self):
        """A shutter_contacts2 device with communicationquality → CommunicationQualitySensor."""
        dev = _fake_device_ne(
            device_id="hdm:SC2:001",
            communicationquality=SimpleNamespace(name="GOOD"),
        )
        session = _make_fake_session_ne(shutter_contacts2=[dev])
        entities = _run_setup_ne(session)
        comm_sensors = [e for e in entities if isinstance(e, CommunicationQualitySensor)]
        sc2_comm = [e for e in comm_sensors if "hdm:SC2:001" in e._attr_unique_id]
        assert len(sc2_comm) == 1

    def test_shutter_contact2_without_cq_attr_is_skipped(self):
        """A shutter_contacts2 device without communicationquality is skipped."""
        dev = _fake_device_ne(device_id="hdm:SC2:002")
        # no communicationquality attribute → hasattr returns False
        session = _make_fake_session_ne(shutter_contacts2=[dev])
        entities = _run_setup_ne(session)
        comm_sensors = [e for e in entities if isinstance(e, CommunicationQualitySensor)]
        sc2_comm = [e for e in comm_sensors if "hdm:SC2:002" in e._attr_unique_id]
        assert len(sc2_comm) == 0

    def test_comm_quality_diagnostic_disabled_skips_shutter_contact(self):
        """diagnostic_entities=False → no CommunicationQualitySensor for shutter_contacts2."""
        from custom_components.bosch_shc.const import OPT_DIAGNOSTIC_ENTITIES
        dev = _fake_device_ne(
            device_id="hdm:SC2:003",
            communicationquality=SimpleNamespace(name="GOOD"),
        )
        session = _make_fake_session_ne(shutter_contacts2=[dev])
        # Passing empty options dict means OPT_DIAGNOSTIC_ENTITIES defaults to True
        # in sensor.py (via config_entry.options.get(OPT_DIAGNOSTIC_ENTITIES, True))
        # so we must explicitly set it False:
        entities = _run_setup_ne(session, options={OPT_DIAGNOSTIC_ENTITIES: False})
        comm_sensors = [e for e in entities if isinstance(e, CommunicationQualitySensor)]
        sc2_comm = [e for e in comm_sensors if "hdm:SC2:003" in e._attr_unique_id]
        assert len(sc2_comm) == 0

    def test_unique_id_format_for_shutter_contact2(self):
        """unique_id must end with _communicationquality."""
        dev = _fake_device_ne(
            name="SC2 test",
            device_id="hdm:SC2:uid-test",
            root_id="root-sc2-uid",
            communicationquality=SimpleNamespace(name="GOOD"),
        )
        session = _make_fake_session_ne(shutter_contacts2=[dev])
        entities = _run_setup_ne(session)
        comm_sensors = [e for e in entities if isinstance(e, CommunicationQualitySensor)]
        sc2_comm = [e for e in comm_sensors if "hdm:SC2:uid-test" in e._attr_unique_id]
        assert len(sc2_comm) == 1
        assert sc2_comm[0]._attr_unique_id.endswith("_communicationquality")


# ===========================================================================
# KeypadTriggerSensor (diag sensor)
# ===========================================================================


def test_keypad_trigger_sensor():
    svc = SimpleNamespace(
        switch_type="UNIVERSAL_SWITCH",
        scenario_id_associations=[{"keyName": "LOWER_BUTTON", "scenarioId": "x"}],
        ids_to_trigger=["x"],
    )
    s = _new(KeypadTriggerSensor)
    s._device = SimpleNamespace(keypadtrigger=svc)
    assert s.native_value == "UNIVERSAL_SWITCH"
    assert s.extra_state_attributes["ids_to_trigger"] == ["x"]


def test_keypad_trigger_sensor_no_service_is_safe():
    s = _new(KeypadTriggerSensor)
    s._device = SimpleNamespace(keypadtrigger=None)
    assert s.native_value is None
    assert s.extra_state_attributes is None


def test_device_update_and_keypad_sensor_drop_attr_name():
    """#342: translated names actually resolve (SHCEntity._attr_name shadow fix)."""
    from custom_components.bosch_shc.update import DeviceUpdate

    u = DeviceUpdate(device=_FAKE_DEVICE, entry_id="e1")
    assert not hasattr(u, "_attr_name")
    assert u.translation_key == "device_firmware"

    s = KeypadTriggerSensor(device=_FAKE_DEVICE, entry_id="e1")
    assert not hasattr(s, "_attr_name")
    assert s.translation_key == "keypad_trigger"


class TestSensorKeypadTriggerSetup:
    """sensor.py lines 462-465: KeypadTriggerSensor setup."""

    def _run_sensor_setup_universal_switches(self, switches, options=None):
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
        dh.smart_plugs = []
        dh.smart_plugs_compact = []
        dh.light_switches_bsm = []
        dh.micromodule_light_controls = []
        dh.micromodule_shutter_controls = []
        dh.micromodule_blinds = []
        dh.universal_switches = switches
        dh.water_leakage_detectors = []
        dh.outdoor_sirens = []

        session = MagicMock()
        session.device_helper = dh
        session.emma = _fake_dev("emma1")

        hass = _fake_hass(session=session)
        entry = _fake_entry(hass=hass, options=options or {})

        with patch(_PATCH_MIGRATE, new_callable=AsyncMock):
            collected = []
            _run(async_setup_entry(hass, entry, lambda ents, **kw: collected.extend(ents)))
        return collected

    def test_keypad_trigger_added_when_supported(self):
        """Lines 461-469: diagnostic_enabled (default True) + supports_keypadtrigger → added."""
        sw = _fake_dev("us1", supports_keypadtrigger=True, supports_batterylevel=False)
        # OPT_DIAGNOSTIC_ENTITIES defaults to True — just pass {} to use default
        collected = self._run_sensor_setup_universal_switches([sw])
        types = [type(e).__name__ for e in collected]
        assert "KeypadTriggerSensor" in types


class TestSensorKeypadTriggerExcluded:
    """sensor.py line 463: excluded universal switch → continue in keypad trigger block."""

    def test_excluded_universal_switch_skipped(self):
        """Line 463: device_excluded → continue before KeypadTriggerSensor."""
        sw = _fake_dev("us_excl", supports_keypadtrigger=True, supports_batterylevel=False)

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
        dh.smart_plugs = []
        dh.smart_plugs_compact = []
        dh.light_switches_bsm = []
        dh.micromodule_light_controls = []
        dh.micromodule_shutter_controls = []
        dh.micromodule_blinds = []
        dh.universal_switches = [sw]
        dh.water_leakage_detectors = []
        dh.outdoor_sirens = []

        session = MagicMock()
        session.device_helper = dh
        session.emma = _fake_dev("emma1")

        hass = _fake_hass(session=session)
        entry = _fake_entry(hass=hass, options={OPT_EXCLUDED_DEVICES: ["us_excl"]})

        with patch(_PATCH_MIGRATE, new_callable=AsyncMock):
            collected = []
            _run(async_setup_entry(hass, entry, lambda ents, **kw: collected.extend(ents)))

        assert not any(isinstance(e, KeypadTriggerSensor) for e in collected)


# ===========================================================================
# HumidityRatingSensor
# ===========================================================================


def _humidity_rating_sensor(rating):
    s = HumidityRatingSensor.__new__(HumidityRatingSensor)
    s._device = SimpleNamespace(humidity_rating=rating)
    return s


class TestHumidityRatingSensor:
    def test_native_value_good(self):
        s = _humidity_rating_sensor(AirQualityLevelService.RatingState.GOOD)
        assert s.native_value == "GOOD"

    def test_native_value_medium(self):
        s = _humidity_rating_sensor(AirQualityLevelService.RatingState.MEDIUM)
        assert s.native_value == "MEDIUM"

    def test_native_value_bad(self):
        s = _humidity_rating_sensor(AirQualityLevelService.RatingState.BAD)
        assert s.native_value == "BAD"


def _humidity_rating_sensor_bad():
    s = HumidityRatingSensor.__new__(HumidityRatingSensor)
    s._device = SimpleNamespace(humidity_rating=_BadEnum(), name="test-twinguard")
    return s


class TestHumidityRatingSensorErrorGuard:
    def test_value_error_returns_none(self):
        """ValueError on humidity_rating.name must return None, not crash."""
        s = _humidity_rating_sensor_bad()
        assert s.native_value is None

    def test_normal_rating_returns_name(self):
        """Sanity: a valid enum must still return the .name string."""
        s = HumidityRatingSensor.__new__(HumidityRatingSensor)
        s._device = SimpleNamespace(
            humidity_rating=AirQualityLevelService.RatingState.GOOD,
            name="twinguard-1",
        )
        assert s.native_value == "GOOD"

# ===========================================================================
# PurityRatingSensor
# ===========================================================================


def _purity_rating_sensor(rating):
    s = PurityRatingSensor.__new__(PurityRatingSensor)
    s._device = SimpleNamespace(purity_rating=rating)
    return s


class TestPurityRatingSensor:
    def test_native_value_good(self):
        s = _purity_rating_sensor(AirQualityLevelService.RatingState.GOOD)
        assert s.native_value == "GOOD"

    def test_native_value_medium(self):
        s = _purity_rating_sensor(AirQualityLevelService.RatingState.MEDIUM)
        assert s.native_value == "MEDIUM"

    def test_native_value_bad(self):
        s = _purity_rating_sensor(AirQualityLevelService.RatingState.BAD)
        assert s.native_value == "BAD"


def _purity_rating_sensor_bad():
    s = PurityRatingSensor.__new__(PurityRatingSensor)
    s._device = SimpleNamespace(purity_rating=_BadEnum(), name="test-twinguard")
    return s


class TestPurityRatingSensorErrorGuard:
    def test_value_error_returns_none(self):
        """ValueError on purity_rating.name must return None."""
        s = _purity_rating_sensor_bad()
        assert s.native_value is None

    def test_normal_rating_returns_name(self):
        s = PurityRatingSensor.__new__(PurityRatingSensor)
        s._device = SimpleNamespace(
            purity_rating=AirQualityLevelService.RatingState.BAD,
            name="twinguard-1",
        )
        assert s.native_value == "BAD"

# ===========================================================================
# PowerSensor / EnergySensor / EnergyYieldSensor / PowerYieldSensor
# ===========================================================================


def _power_sensor(powerconsumption):
    s = PowerSensor.__new__(PowerSensor)
    s._device = SimpleNamespace(powerconsumption=powerconsumption)
    return s


class TestPowerSensor:
    def test_native_value(self):
        assert _power_sensor(150.5).native_value == 150.5

    def test_native_value_zero(self):
        assert _power_sensor(0.0).native_value == 0.0

    def test_device_class(self):
        assert _power_sensor(0.0).device_class == SensorDeviceClass.POWER

    def test_unit(self):
        assert _power_sensor(0.0).native_unit_of_measurement == UnitOfPower.WATT

    def test_state_class(self):
        assert _power_sensor(0.0).state_class == SensorStateClass.MEASUREMENT


# ===========================================================================
# EmmaPowerSensor
# ===========================================================================


def _emma_sensor(value=0.0, localized_subtitles="Consumed"):
    s = EmmaPowerSensor.__new__(EmmaPowerSensor)
    s._device = SimpleNamespace(
        value=value,
        localizedSubtitles=localized_subtitles,
    )
    return s


class TestEmmaPowerSensor:
    def test_native_value_positive(self):
        """Positive value = power fed to grid."""
        assert _emma_sensor(value=1500.0).native_value == 1500.0

    def test_native_value_negative(self):
        """Negative value = power consumed from grid."""
        assert _emma_sensor(value=-800.0).native_value == -800.0

    def test_native_value_zero(self):
        assert _emma_sensor(value=0.0).native_value == 0.0

    def test_extra_state_attributes_power_flow(self):
        s = _emma_sensor(localized_subtitles="Feeding")
        assert s.extra_state_attributes == {"power_flow": "Feeding"}

    def test_extra_state_attributes_key(self):
        """extra_state_attributes must always have 'power_flow' key."""
        s = _emma_sensor()
        assert "power_flow" in s.extra_state_attributes

    def test_device_class_is_power(self):
        assert _emma_sensor().device_class == SensorDeviceClass.POWER

    def test_unit_is_watt(self):
        assert _emma_sensor().native_unit_of_measurement == UnitOfPower.WATT

    def test_state_class_is_measurement(self):
        assert _emma_sensor().state_class == SensorStateClass.MEASUREMENT

    def test_entity_registry_enabled_default_false(self):
        """EmmaPowerSensor must be disabled by default (opt-in diagnostic).

        HA parent classes shadow _attr_entity_registry_enabled_default with a
        property, so we access via an instance to read the actual value.
        """
        s = _emma_sensor()
        assert s.entity_registry_enabled_default is False


class TestEmmaPowerSensorLifecycle:
    """Cover async_added_to_hass and async_will_remove_from_hass."""

    def _make_emma(self):
        callbacks = {}
        dev = _fake_device(
            name="EMMA",
            device_id="com.bosch.tt.emma.applink",
            root_device_id="shc-mac",
        )
        dev.subscribe_callback = lambda eid, fn: callbacks.update({eid: fn})
        dev.unsubscribe_callback = lambda eid: callbacks.pop(eid, None)
        dev.device_services = []
        return dev, callbacks

    def test_async_added_to_hass_subscribes_callback(self):
        dev, callbacks = self._make_emma()
        entity = EmmaPowerSensor(device=dev, entry_id=ENTRY_ID)
        # entity_id is set by HA platform normally; inject a fake one
        entity.entity_id = "sensor.emma_power"
        entity.schedule_update_ha_state = MagicMock()

        async def _run():
            await entity.async_added_to_hass()

        asyncio.run(_run())
        assert "sensor.emma_power" in callbacks

    def test_async_will_remove_from_hass_unsubscribes(self):
        dev, callbacks = self._make_emma()
        entity = EmmaPowerSensor(device=dev, entry_id=ENTRY_ID)
        entity.entity_id = "sensor.emma_power"
        entity.schedule_update_ha_state = MagicMock()

        async def _run():
            await entity.async_added_to_hass()
            await entity.async_will_remove_from_hass()

        asyncio.run(_run())
        assert "sensor.emma_power" not in callbacks

    def test_callback_triggers_schedule_update(self):
        dev, callbacks = self._make_emma()
        entity = EmmaPowerSensor(device=dev, entry_id=ENTRY_ID)
        entity.entity_id = "sensor.emma_power"
        entity.schedule_update_ha_state = MagicMock()

        async def _run():
            await entity.async_added_to_hass()

        asyncio.run(_run())
        callbacks["sensor.emma_power"]()  # fire the stored callback
        entity.schedule_update_ha_state.assert_called_once()

    def test_entity_registry_enabled_default_is_false(self):
        d = _fake_device(name="EMMA", device_id="com.bosch.tt.emma.applink")
        s = EmmaPowerSensor(device=d, entry_id=ENTRY_ID)
        assert s.entity_registry_enabled_default is False

    def test_emma_extra_state_attributes_returns_power_flow(self):
        dev, _ = self._make_emma()
        dev.localizedSubtitles = "Grid Supply"
        entity = EmmaPowerSensor(device=dev, entry_id=ENTRY_ID)
        attrs = entity.extra_state_attributes
        assert "power_flow" in attrs
        assert attrs["power_flow"] == "Grid Supply"

    def test_emma_native_value_returns_device_value(self):
        dev, _ = self._make_emma()
        dev.value = -1500.0
        entity = EmmaPowerSensor(device=dev, entry_id=ENTRY_ID)
        assert entity.native_value == -1500.0

    def test_emma_native_value_positive(self):
        dev, _ = self._make_emma()
        dev.value = 300.0
        entity = EmmaPowerSensor(device=dev, entry_id=ENTRY_ID)
        assert entity.native_value == 300.0

    def test_emma_native_value_none(self):
        dev, _ = self._make_emma()
        dev.value = None
        entity = EmmaPowerSensor(device=dev, entry_id=ENTRY_ID)
        assert entity.native_value is None


def _energy_sensor(energyconsumption_wh):
    s = EnergySensor.__new__(EnergySensor)
    s._device = SimpleNamespace(energyconsumption=energyconsumption_wh)
    return s


class TestEnergySensor:
    def test_native_value_converts_wh_to_kwh(self):
        """Energyconsumption is in Wh; native_value must divide by 1000."""
        assert _energy_sensor(5000).native_value == 5.0

    def test_native_value_zero(self):
        assert _energy_sensor(0).native_value == 0.0

    def test_native_value_partial_kwh(self):
        assert _energy_sensor(1500).native_value == 1.5

    def test_device_class(self):
        assert _energy_sensor(0).device_class == SensorDeviceClass.ENERGY

    def test_unit(self):
        assert _energy_sensor(0).native_unit_of_measurement == UnitOfEnergy.KILO_WATT_HOUR

    def test_state_class_total_increasing(self):
        assert _energy_sensor(0).state_class == SensorStateClass.TOTAL_INCREASING


def _energy_yield_sensor(energy_yield):
    s = EnergyYieldSensor.__new__(EnergyYieldSensor)
    s._device = SimpleNamespace(energy_yield=energy_yield)
    return s


def _power_yield_sensor(powerconsumption):
    s = PowerYieldSensor.__new__(PowerYieldSensor)
    s._device = SimpleNamespace(powerconsumption=powerconsumption)
    return s


class TestEnergyYieldSensor:
    def test_wh_to_kwh(self):
        assert _energy_yield_sensor(234.0).native_value == 0.234

    def test_zero(self):
        assert _energy_yield_sensor(0.0).native_value == 0.0

    def test_none_passthrough(self):
        assert _energy_yield_sensor(None).native_value is None

    def test_device_class_energy(self):
        assert _energy_yield_sensor(1.0).device_class == SensorDeviceClass.ENERGY

    def test_state_class_total_increasing(self):
        assert (
            _energy_yield_sensor(1.0).state_class
            == SensorStateClass.TOTAL_INCREASING
        )


class TestPowerYieldSensor:
    def test_positive_yield_from_negative_consumption(self):
        assert _power_yield_sensor(-800.0).native_value == 800.0

    def test_zero_while_consuming(self):
        assert _power_yield_sensor(1.0).native_value == 0.0

    def test_zero_when_zero(self):
        assert _power_yield_sensor(0.0).native_value == 0.0

    def test_none_passthrough(self):
        assert _power_yield_sensor(None).native_value is None

    def test_device_class_power(self):
        assert _power_yield_sensor(-5.0).device_class == SensorDeviceClass.POWER

    def test_unit_watt(self):
        assert (
            _power_yield_sensor(-5.0).native_unit_of_measurement
            == UnitOfPower.WATT
        )


class TestSensorEnergyYieldSmartPlug:
    """sensor.py lines 258-261: EnergyYieldSensor + PowerYieldSensor for smart_plugs."""

    def _run_sensor_setup_smart_plugs(self, smart_plugs, options=None):
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
        dh.smart_plugs = smart_plugs
        dh.smart_plugs_compact = []
        # ALL list attrs needed by the power_sensors_enabled concatenation loop:
        dh.light_switches_bsm = []
        dh.micromodule_light_controls = []
        dh.micromodule_shutter_controls = []
        dh.micromodule_blinds = []
        dh.universal_switches = []
        dh.water_leakage_detectors = []
        dh.outdoor_sirens = []

        session = MagicMock()
        session.device_helper = dh
        session.emma = _fake_dev("emma1")  # give a proper fake to avoid init errors

        hass = _fake_hass(session=session)
        entry = _fake_entry(hass=hass, options=options or {})

        with patch(_PATCH_MIGRATE, new_callable=AsyncMock):
            collected = []
            _run(async_setup_entry(hass, entry, lambda ents, **kw: collected.extend(ents)))
        return collected

    def test_energy_yield_sensors_added_when_supported(self):
        """Lines 257-263: supports_energy_yield=True → EnergyYieldSensor added."""
        dev = _fake_dev("sp1", supports_energy_yield=True,
                        supports_batterylevel=False, serial="SER1")
        collected = self._run_sensor_setup_smart_plugs([dev])
        types = [type(e).__name__ for e in collected]
        assert "EnergyYieldSensor" in types
        assert "PowerYieldSensor" in types


# ===========================================================================
# ValveTappetSensor
# ===========================================================================


def _valve_sensor(position, valvestate):
    s = ValveTappetSensor.__new__(ValveTappetSensor)
    s._device = SimpleNamespace(position=position, valvestate=valvestate)
    return s


class TestValveTappetSensor:
    def test_native_value(self):
        state = ValveTappetService.State.IN_START_POSITION
        assert _valve_sensor(50, state).native_value == 50

    def test_native_value_zero(self):
        state = ValveTappetService.State.IN_START_POSITION
        assert _valve_sensor(0, state).native_value == 0

    def test_extra_state_attributes_adaption_successful(self):
        state = ValveTappetService.State.VALVE_ADAPTION_SUCCESSFUL
        s = _valve_sensor(100, state)
        assert s.extra_state_attributes == {"valve_tappet_state": "VALVE_ADAPTION_SUCCESSFUL"}

    def test_extra_state_attributes_adaption_in_progress(self):
        state = ValveTappetService.State.VALVE_ADAPTION_IN_PROGRESS
        s = _valve_sensor(50, state)
        assert s.extra_state_attributes == {"valve_tappet_state": "VALVE_ADAPTION_IN_PROGRESS"}

    def test_extra_state_attributes_not_available(self):
        state = ValveTappetService.State.NOT_AVAILABLE
        s = _valve_sensor(0, state)
        assert s.extra_state_attributes == {"valve_tappet_state": "NOT_AVAILABLE"}

    def test_extra_state_attributes_value_error_yields_none(self):
        """If valvestate.name raises ValueError, valve_tappet_state must be None."""
        class _BadState:
            @property
            def name(self):
                raise ValueError("unknown state")

        s = ValveTappetSensor.__new__(ValveTappetSensor)
        s._device = SimpleNamespace(position=0, valvestate=_BadState(), name="test-valve")
        assert s.extra_state_attributes == {"valve_tappet_state": None}

    def test_unit_is_percent(self):
        state = ValveTappetService.State.IN_START_POSITION
        assert _valve_sensor(0, state).native_unit_of_measurement == PERCENTAGE

    def test_entity_category_diagnostic(self):
        state = ValveTappetService.State.IN_START_POSITION
        assert _valve_sensor(0, state).entity_category == EntityCategory.DIAGNOSTIC


def _valve_tappet_sensor(position, valvestate_name):
    """Build ValveTappetSensor with a fake enum that has a fixed .name."""

    class _FakeState:
        name = valvestate_name

    s = ValveTappetSensor.__new__(ValveTappetSensor)
    s._device = SimpleNamespace(
        position=position,
        valvestate=_FakeState(),
        name="thermostat-1",
    )
    return s


class TestValveTappetSensorExtraAttrs:
    def test_in_start_position_state(self):
        s = _valve_tappet_sensor(0, "IN_START_POSITION")
        assert s.extra_state_attributes == {"valve_tappet_state": "IN_START_POSITION"}

    def test_run_to_next_position_state(self):
        s = _valve_tappet_sensor(50, "VALVE_ADAPTION_IN_PROGRESS")
        assert s.extra_state_attributes == {
            "valve_tappet_state": "VALVE_ADAPTION_IN_PROGRESS"
        }

    def test_position_reflected_in_native_value(self):
        s = _valve_tappet_sensor(75, "VALVE_ADAPTION_SUCCESSFUL")
        assert s.native_value == 75

    def test_position_zero(self):
        s = _valve_tappet_sensor(0, "NOT_AVAILABLE")
        assert s.native_value == 0


# ===========================================================================
# IlluminanceLevelSensor
# ===========================================================================


def _illum_sensor(illuminance_value):
    """Build an IlluminanceLevelSensor bypassing SHCEntity.__init__.

    Identical helper previously duplicated (differing only in the parameter
    name) in test_sensor_extra_coverage.py (as `_illum_sensor(value)`) and
    test_sensor_unit.py — deduped to one copy.
    """
    s = IlluminanceLevelSensor.__new__(IlluminanceLevelSensor)
    s._device = SimpleNamespace(illuminance=illuminance_value)
    return s


class TestIlluminanceLevelSensor:
    # native_value: numeric passthrough, non-numeric coerced to None (#315)
    def test_gen1_string_coerced_none(self):
        assert _illum_sensor("MEDIUM").native_value is None

    def test_gen2_int_value(self):
        assert _illum_sensor(320).native_value == 320

    def test_gen2_int_zero(self):
        assert _illum_sensor(0).native_value == 0

    def test_none_value(self):
        assert _illum_sensor(None).native_value is None

    # static metadata — stable regardless of value (#315)
    def test_state_class_measurement(self):
        assert _illum_sensor(13).state_class == SensorStateClass.MEASUREMENT

    def test_state_class_stable_when_none(self):
        """A None value must keep MEASUREMENT — not re-raise state_class_removed."""
        assert _illum_sensor(None).state_class == SensorStateClass.MEASUREMENT

    def test_device_class_illuminance(self):
        assert _illum_sensor(9).device_class == SensorDeviceClass.ILLUMINANCE

    def test_unit_lux(self):
        assert _illum_sensor(9).native_unit_of_measurement == LIGHT_LUX


def _make_illuminance_test_sensor(illuminance_value):
    """Build an IlluminanceLevelSensor bypassing SHCEntity.__init__ (#315
    metadata-stability regression test)."""
    sensor = IlluminanceLevelSensor.__new__(IlluminanceLevelSensor)
    sensor._device = SimpleNamespace(
        illuminance=illuminance_value,
        name="test-motion",
        id="test-id",
        root_device_id="test-root",
    )
    sensor._attr_name = "test-motion Illuminance"
    sensor._attr_unique_id = "test-root_test-id_illuminance"
    return sensor


class TestIlluminanceStaticMetadata:
    """Metadata is static — independent of the current value (#315).

    Fix: state_class=MEASUREMENT, device_class=illuminance, unit=lx are STATIC
    so they never flip-flop (a momentary None value used to drop state_class
    and re-raise the state_class_removed repair). native_value coerces any
    non-numeric value to None so a hypothetical qualitative-string firmware
    degrades to "unknown" instead of conflicting with the measurement
    state_class.
    """

    def test_state_class_numeric(self):
        assert _make_illuminance_test_sensor(13).state_class == SensorStateClass.MEASUREMENT

    def test_device_class_numeric(self):
        assert _make_illuminance_test_sensor(13).device_class == SensorDeviceClass.ILLUMINANCE

    def test_unit_numeric(self):
        assert _make_illuminance_test_sensor(13).native_unit_of_measurement == LIGHT_LUX

    def test_metadata_stable_when_value_none(self):
        """Regression #315: a None value must NOT drop state_class/unit
        (that re-raised the state_class_removed repair + unit-change warnings).
        """
        s = _make_illuminance_test_sensor(None)
        assert s.state_class == SensorStateClass.MEASUREMENT
        assert s.device_class == SensorDeviceClass.ILLUMINANCE
        assert s.native_unit_of_measurement == LIGHT_LUX

    def test_metadata_stable_for_string(self):
        s = _make_illuminance_test_sensor("MEDIUM")
        assert s.state_class == SensorStateClass.MEASUREMENT
        assert s.device_class == SensorDeviceClass.ILLUMINANCE
        assert s.native_unit_of_measurement == LIGHT_LUX


class TestIlluminanceNativeValue:
    """native_value returns numeric lux, else None (#315)."""

    def test_int(self):
        assert _make_illuminance_test_sensor(13).native_value == 13

    def test_zero(self):
        assert _make_illuminance_test_sensor(0).native_value == 0

    def test_float(self):
        assert _make_illuminance_test_sensor(13.5).native_value == 13.5

    def test_large_gen2(self):
        assert _make_illuminance_test_sensor(1000).native_value == 1000

    def test_none_value(self):
        assert _make_illuminance_test_sensor(None).native_value is None

    def test_string_coerced_to_none(self):
        """Qualitative string degrades to None (no measurement conflict)."""
        assert _make_illuminance_test_sensor("MEDIUM").native_value is None
        assert _make_illuminance_test_sensor("LOW").native_value is None

    def test_bool_coerced_to_none(self):
        """Bool is an int subclass but is not a real lux reading."""
        assert _make_illuminance_test_sensor(True).native_value is None
        assert _make_illuminance_test_sensor(False).native_value is None


class TestIlluminanceLevelSensorBoolGuard:
    """sensor.py line 611-612: bool isinstance guard."""

    def test_bool_true_returns_none(self):
        """Bool True is a subclass of int; must return None, not 1."""
        assert _illum_sensor(True).native_value is None

    def test_bool_false_returns_none(self):
        """Bool False is a subclass of int; must return None, not 0."""
        assert _illum_sensor(False).native_value is None

    def test_float_value_returned(self):
        """Float lux value must pass through unchanged."""
        assert _illum_sensor(9.5).native_value == 9.5

    def test_large_int_returned(self):
        assert _illum_sensor(10000).native_value == 10000

    def test_string_returns_none(self):
        assert _illum_sensor("MEDIUM").native_value is None

    def test_none_returns_none(self):
        assert _illum_sensor(None).native_value is None


# ===========================================================================
# BatteryLevelSensor
# ===========================================================================


def _make_battery_level_sensor(state):
    """Build a BatteryLevelSensor bypassing __init__, injecting a fake device."""
    s = BatteryLevelSensor.__new__(BatteryLevelSensor)
    s._device = SimpleNamespace(
        batterylevel=state,
        name="Motion Detector",
        root_device_id="root-abc",
        id="dev-123",
    )
    s._attr_name = "Battery Level"
    s._attr_unique_id = "root-abc_dev-123_battery_level"
    return s


class TestBatteryLevelSensorNativeValue:
    """native_value — all 5 BatteryLevelService.State members."""

    def test_ok(self):
        assert _make_battery_level_sensor(BatteryLevelService.State.OK).native_value == "ok"

    def test_low_battery(self):
        assert (
            _make_battery_level_sensor(BatteryLevelService.State.LOW_BATTERY).native_value
            == "low_battery"
        )

    def test_critical_low(self):
        assert (
            _make_battery_level_sensor(BatteryLevelService.State.CRITICAL_LOW).native_value
            == "critical_low"
        )

    def test_critically_low_battery(self):
        assert (
            _make_battery_level_sensor(
                BatteryLevelService.State.CRITICALLY_LOW_BATTERY
            ).native_value
            == "critically_low_battery"
        )

    def test_not_available(self):
        assert (
            _make_battery_level_sensor(BatteryLevelService.State.NOT_AVAILABLE).native_value
            == "not_available"
        )


class _BadBatteryState:
    """Simulates an unknown enum variant: .value raises ValueError."""

    @property
    def value(self):
        raise ValueError("Unknown battery state X")


class _MissingBatteryAttr:
    """Simulates a device with no batterylevel service: .value raises AttributeError."""

    @property
    def value(self):
        raise AttributeError("'NoneType' has no attribute 'value'")


class TestBatteryLevelSensorGuard:
    """native_value — unknown / bad state yields None (guard)."""

    def test_value_error_returns_none(self):
        s = BatteryLevelSensor.__new__(BatteryLevelSensor)
        s._device = SimpleNamespace(
            batterylevel=_BadBatteryState(),
            name="Smoke Detector",
        )
        assert s.native_value is None

    def test_attribute_error_returns_none(self):
        s = BatteryLevelSensor.__new__(BatteryLevelSensor)
        s._device = SimpleNamespace(
            batterylevel=_MissingBatteryAttr(),
            name="Shutter Contact",
        )
        assert s.native_value is None


class TestBatteryLevelSensorMetadata:
    """Class-level metadata."""

    def _sensor(self):
        return _make_battery_level_sensor(BatteryLevelService.State.OK)

    def test_device_class_enum(self):
        assert self._sensor().device_class == SensorDeviceClass.ENUM

    def test_entity_category_diagnostic(self):
        assert self._sensor().entity_category == EntityCategory.DIAGNOSTIC

    def test_options_list(self):
        assert self._sensor().options == [
            "ok",
            "low_battery",
            "critical_low",
            "critically_low_battery",
            "not_available",
        ]

    def test_options_covers_all_enum_members(self):
        """Every BatteryLevelService.State .value (lowercased) must appear in options."""
        opts = set(self._sensor().options)
        for member in BatteryLevelService.State:
            assert member.value.lower() in opts, f"{member.value!r} missing from _attr_options"

    def test_unique_id_suffix(self):
        assert self._sensor()._attr_unique_id == "root-abc_dev-123_battery_level"

    def test_name(self):
        assert self._sensor()._attr_name == "Battery Level"


class _RaisingValue:
    """Simulates a device.batterylevel that raises on .value access."""
    def __init__(self, exc_cls=ValueError):
        self._exc_cls = exc_cls

    @property
    def value(self):
        raise self._exc_cls("bad battery level")


class TestBatteryLevelSensorErrorPaths:
    """BatteryLevelSensor.native_value — ValueError and AttributeError paths
    (sensor.py lines 736-738).

    NOTE (vacuous-duplicate resolution): test_remaining_gaps.py contained a
    second class also named TestBatteryLevelSensorErrorPaths with only
    test_value_error_returns_none / test_attribute_error_returns_none, whose
    bodies were a behavioral exact-duplicate of the two tests below (same
    mocked ValueError/AttributeError-on-.value scenario, same assertion,
    only the throwaway inline helper class differed). That class was dropped
    entirely rather than renamed — see the consolidation report for detail.
    """

    def _sensor(self, batterylevel):
        s = BatteryLevelSensor.__new__(BatteryLevelSensor)
        s._device = SimpleNamespace(batterylevel=batterylevel, name="dev-x")
        return s

    def test_value_error_returns_none(self):
        """ValueError on batterylevel.value must return None (not raise)."""
        s = self._sensor(_RaisingValue(ValueError))
        assert s.native_value is None

    def test_attribute_error_returns_none(self):
        """AttributeError on batterylevel.value must return None (not raise)."""
        s = self._sensor(_RaisingValue(AttributeError))
        assert s.native_value is None

    def test_happy_path_returns_value(self):
        """When batterylevel.value is a valid string, it is returned lowercased."""
        class _GoodLevel:
            value = "OK"

        s = self._sensor(_GoodLevel())
        assert s.native_value == "ok"

    def test_happy_path_low_battery(self):
        class _LowLevel:
            value = "LOW_BATTERY"

        s = self._sensor(_LowLevel())
        assert s.native_value == "low_battery"


class TestBatterySensorSupportsFalse:
    """sensor.py line 354: Device with supports_batterylevel=False must not
    produce BatteryLevelSensor."""

    def test_no_battery_entity_when_not_supported(self):
        async def _noop_migrate_local(*a, **kw):
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

        hass = SimpleNamespace()
        config_entry = SimpleNamespace(entry_id="E1", options={})
        config_entry.runtime_data = SimpleNamespace(session=session)
        collected = []

        async def _inner():
            with patch(_PATCH_MIGRATE, side_effect=_noop_migrate_local):
                await async_setup_entry(hass, config_entry, lambda e: collected.extend(e))

        asyncio.run(_inner())

        battery_entities = [e for e in collected if isinstance(e, BatteryLevelSensor)]
        assert battery_entities == [], (
            "BatteryLevelSensor must not be created when supports_batterylevel=False"
        )


class TestBatteryLevelSensorCreation:
    """sensor.py line 354 + lines 736-738: BatteryLevelSensor happy path
    (via async_setup_entry + _make_sensor_session / _fake_battery_device)."""

    def test_battery_level_sensor_created_for_battery_device(self):
        """Device with supports_batterylevel=True AND diagnostic_enabled=True
        causes BatteryLevelSensor to be appended (line 354) and its __init__
        to run (lines 736-738).
        """
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
        dev = _fake_battery_device("dev-123", "Device 123", "root-456")
        session = _make_sensor_session(motion_detectors=[dev])
        entities = _run_sensor_setup(session, {})
        bat = [e for e in entities if isinstance(e, BatteryLevelSensor)]
        assert len(bat) == 1
        assert bat[0]._attr_unique_id == "root-456_dev-123_battery_level"

    def test_battery_level_sensor_explicit_diagnostic_enabled(self):
        """OPT_DIAGNOSTIC_ENTITIES=True explicitly: BatteryLevelSensor still created."""
        from custom_components.bosch_shc.const import OPT_DIAGNOSTIC_ENTITIES

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

        dev = _fake_battery_device("md-nodiag", "NoDiag", "root-nodiag")
        session = _make_sensor_session(motion_detectors=[dev])
        entities = _run_sensor_setup(session, {OPT_DIAGNOSTIC_ENTITIES: False})
        bat = [e for e in entities if isinstance(e, BatteryLevelSensor)]
        assert bat == []

    def test_battery_level_sensor_multiple_devices(self):
        """Multiple devices each get their own BatteryLevelSensor instance."""
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


class TestBatteryLevelSensorCreationRawMock:
    """sensor.py:354,736-738 — BatteryLevelSensor created when
    supports_batterylevel=True, exercised via a raw MagicMock-based session
    (as opposed to TestBatteryLevelSensorCreation above, which uses the
    _fake_battery_device/_make_sensor_session helpers). Originally also named
    TestBatteryLevelSensorCreation in test_final_coverage_gaps.py — renamed
    here to resolve the collision; kept because its construction mechanism
    and assertions are not an exact duplicate of the other class.
    """

    def test_battery_level_sensor_added(self):
        """Lines 354,736-738: a device with supports_batterylevel=True gets a sensor."""
        device = MagicMock()
        device.id = "dev-battery-001"
        device.root_device_id = "root-001"
        device.serial = "SN001"
        device.name = "Thermostat"
        device.supports_batterylevel = True
        device.batterylevel = MagicMock()

        emma = MagicMock()
        emma.id = "emma-001"
        emma.root_device_id = "root-emma"
        emma.name = "Emma"
        emma.value = 0
        emma.localizedSubtitles = []

        session = MagicMock()
        session.device_helper.thermostats = [device]
        session.device_helper.wallthermostats = []
        session.device_helper.roomthermostats = []
        session.device_helper.twinguards = []
        session.device_helper.universal_switches = []
        session.device_helper.smart_plugs = []
        session.device_helper.light_switches_bsm = []
        session.device_helper.micromodule_light_controls = []
        session.device_helper.micromodule_shutter_controls = []
        session.device_helper.micromodule_blinds = []
        session.device_helper.smart_plugs_compact = []
        session.device_helper.motion_detectors = []
        session.device_helper.motion_detectors2 = []
        session.device_helper.shutter_contacts = []
        session.device_helper.shutter_contacts2 = []
        session.device_helper.smoke_detectors = []
        session.device_helper.water_leakage_detectors = []
        session.emma = emma

        hass = MagicMock()

        entry = MagicMock()
        entry.entry_id = "eid1"
        # Enable diagnostic entities so the battery level loop is reached
        entry.options = {"diagnostic_entities": True}
        entry.runtime_data = SimpleNamespace(
            session=session, shc_device=None, title="Test SHC"
        )

        added = []

        with patch(_PATCH_MIGRATE, new=AsyncMock(return_value=None)):
            asyncio.run(
                __import__(
                    "custom_components.bosch_shc.sensor",
                    fromlist=["async_setup_entry"],
                ).async_setup_entry(hass, entry, lambda e: added.extend(e))
            )

        # BatteryLevelSensor uses _attr_translation_key (Silver gap); check by type
        bat_sensors = [e for e in added if isinstance(e, BatteryLevelSensor)]
        assert bat_sensors, (
            f"BatteryLevelSensor not created. Got: {[type(e).__name__ for e in added]}"
        )

    def test_battery_level_sensor_unique_id(self):
        """Lines 736-738: BatteryLevelSensor unique_id is correctly composed."""
        device = MagicMock()
        device.id = "dev-123"
        device.root_device_id = "root-456"
        device.name = "ThermSensor"
        device.serial = "SN001"

        sensor = BatteryLevelSensor.__new__(BatteryLevelSensor)
        sensor._device = device

        # Call __init__ directly
        BatteryLevelSensor.__init__(sensor, device=device, entry_id="eid1")

        assert sensor.translation_key == "battery_level"
        assert sensor._attr_unique_id == "root-456_dev-123_battery_level"


def test_battery_level_disabled_by_default():
    # #339: it duplicates the binary "Battery" sensor → hidden unless opted in.
    s = BatteryLevelSensor.__new__(BatteryLevelSensor)
    assert s.entity_registry_enabled_default is False
    assert s.entity_category == EntityCategory.DIAGNOSTIC


# ===========================================================================
# TwinguardCombinedRatingSensor / TwinguardDescriptionSensor
# ===========================================================================


class _RaisingCombinedRating:
    """Simulates a combined_rating that raises .name."""
    def __init__(self, exc_cls=ValueError):
        self._exc_cls = exc_cls

    @property
    def name(self):
        raise self._exc_cls("unknown combined rating")


class TestTwinguardCombinedRatingSensorErrorPaths:
    """TwinguardCombinedRatingSensor.native_value — ValueError/AttributeError
    (sensor.py lines 778-784)."""

    def _sensor(self, combined_rating):
        s = TwinguardCombinedRatingSensor.__new__(TwinguardCombinedRatingSensor)
        s._device = SimpleNamespace(combined_rating=combined_rating, name="TG-1")
        return s

    def test_value_error_returns_none(self):
        """ValueError on combined_rating.name must return None."""
        s = self._sensor(_RaisingCombinedRating(ValueError))
        assert s.native_value is None

    def test_attribute_error_returns_none(self):
        """AttributeError on combined_rating.name must return None."""
        s = self._sensor(_RaisingCombinedRating(AttributeError))
        assert s.native_value is None

    def test_happy_path_good(self):
        class _Good:
            name = "GOOD"

        s = self._sensor(_Good())
        assert s.native_value == "good"

    def test_unknown_is_a_valid_option(self):
        """boschshcpy's RatingState falls back to UNKNOWN (missing/unrecognized
        combinedRating value) rather than raising — that value must be in
        _attr_options, or HA's SensorEntity.state raises ValueError instead of
        showing "unknown"."""
        class _Unknown:
            name = "UNKNOWN"

        s = self._sensor(_Unknown())
        assert s.native_value == "unknown"
        assert s.native_value in s.options

    def test_happy_path_bad(self):
        class _Bad:
            name = "BAD"

        s = self._sensor(_Bad())
        assert s.native_value == "bad"


class TestTwinguardDescriptionSensor:
    """TwinguardDescriptionSensor.native_value returns description (sensor.py
    line 807)."""

    def _sensor(self, description):
        s = TwinguardDescriptionSensor.__new__(TwinguardDescriptionSensor)
        s._device = SimpleNamespace(description=description)
        return s

    def test_returns_description_string(self):
        """native_value must return the device.description string."""
        s = self._sensor("Fresh air — comfort zone active")
        assert s.native_value == "Fresh air — comfort zone active"

    def test_returns_none_when_description_is_none(self):
        """native_value returns None when description is None."""
        s = self._sensor(None)
        assert s.native_value is None

    def test_returns_empty_string(self):
        """native_value returns empty string unchanged."""
        s = self._sensor("")
        assert s.native_value == ""

    def test_returns_different_descriptions(self):
        """Sensor state correctly reflects whatever description the device has."""
        for desc in ["GOOD", "BAD", "MEDIUM", "Unknown state"]:
            s = self._sensor(desc)
            assert s.native_value == desc


# ===========================================================================
# WalkStateSensor
# ===========================================================================


class TestWalkStateSensor:
    def _make(self, walk_state_name="UNKNOWN"):
        from boschshcpy.services_impl import WalkTestService
        val = WalkTestService.WalkState[walk_state_name]
        dev = _fake_md2(walk_state=val, supports_walk_test=True)
        s = WalkStateSensor.__new__(WalkStateSensor)
        s._device = dev
        s._attr_unique_id = f"{dev.root_device_id}_{dev.id}_walk_state"
        s._attr_name = "Walk Test State"
        return s

    def test_unique_id(self):
        s = self._make()
        assert s._attr_unique_id == "root1_md1_walk_state"

    def test_native_value_unknown(self):
        s = self._make("UNKNOWN")
        assert s.native_value == "unknown"

    def test_native_value_walk_test_started(self):
        s = self._make("WALK_TEST_STARTED")
        assert s.native_value == "walk_test_started"

    def test_native_value_walk_test_stopped(self):
        s = self._make("WALK_TEST_STOPPED")
        assert s.native_value == "walk_test_stopped"

    def test_native_value_none_when_walk_state_is_none(self):
        dev = _fake_md2(walk_state=None)
        s = WalkStateSensor.__new__(WalkStateSensor)
        s._device = dev
        assert s.native_value is None

    def test_native_value_attribute_error_returns_none(self):
        dev = _fake_md2()  # no walk_state attr
        s = WalkStateSensor.__new__(WalkStateSensor)
        s._device = dev
        assert s.native_value is None

    def test_options_list(self):
        s = self._make()
        assert "walk_test_started" in s.options
        assert "walk_test_stopped" in s.options
        assert "unknown" in s.options


class TestWalkStateSensorSetup:
    """WalkStateSensor — setup entry (part of sensor.py)."""

    def _run_sensor_setup(self, md2_list):
        entry_id = "E1"
        emma = SimpleNamespace(
            name="EMMA", id="com.bosch.tt.emma.applink",
            root_device_id="root_emma", serial="EMMA_SER",
            supports_batterylevel=False,
        )
        device_helper = SimpleNamespace(
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
            motion_detectors=[],
            motion_detectors2=list(md2_list),
            shutter_contacts=[],
            shutter_contacts2=[],
            smoke_detectors=[],
            universal_switches=[],
            water_leakage_detectors=[],
        )
        session = SimpleNamespace(device_helper=device_helper, emma=emma)
        hass = SimpleNamespace()
        config_entry = SimpleNamespace(
            options={},
            entry_id=entry_id,
            async_on_unload=MagicMock(),
        )
        config_entry.runtime_data = SimpleNamespace(
            session=session,
            shc_device=SimpleNamespace(
                name="SHC", id="shc",
                identifiers={("bosch_shc", "shc")},
                manufacturer="Bosch", model="SHC",
            ),
            title="Test SHC",
        )
        entities = []

        def add_entities(new_ents, *args, **kwargs):
            entities.extend(new_ents)

        with patch(_PATCH_MIGRATE, new=AsyncMock(return_value=None)):
            asyncio.run(async_setup_entry(hass, config_entry, add_entities))

        return entities

    def test_walk_state_sensor_created_when_walk_state_present(self):
        from boschshcpy.services_impl import WalkTestService
        md2 = _fake_md2(walk_state=WalkTestService.WalkState.UNKNOWN, supports_walk_test=True)
        entities = self._run_sensor_setup([md2])
        types = [type(e).__name__ for e in entities]
        assert "WalkStateSensor" in types

    def test_walk_state_sensor_skipped_when_walk_state_none(self):
        # supports_walk_test=True but walk_state=None -> not created
        md2 = _fake_md2(walk_state=None, supports_walk_test=True)
        entities = self._run_sensor_setup([md2])
        types = [type(e).__name__ for e in entities]
        assert "WalkStateSensor" not in types

# ===========================================================================
# DetectionStateSensor
# ===========================================================================


class TestDetectionStateSensor:
    def _make(self, name="DETECTION_TEST_STOPPED"):
        from boschshcpy.services_impl import DetectionTestService

        dev = _fake_md2(detection_state=DetectionTestService.DetectionState[name])
        s = DetectionStateSensor.__new__(DetectionStateSensor)
        s._device = dev
        return s

    def test_native_value(self):
        assert self._make("DETECTION_TEST_STARTED").native_value == (
            "detection_test_started"
        )

    def test_native_value_none_when_none(self):
        dev = _fake_md2(detection_state=None)
        s = DetectionStateSensor.__new__(DetectionStateSensor)
        s._device = dev
        assert s.native_value is None

    def test_native_value_attribute_error(self):
        dev = _fake_md2()  # no detection_state attr
        s = DetectionStateSensor.__new__(DetectionStateSensor)
        s._device = dev
        assert s.native_value is None

    def test_setup_created_when_supported(self):
        from boschshcpy.services_impl import DetectionTestService

        md2 = _fake_md2(
            supports_detection_test=True,
            detection_state=DetectionTestService.DetectionState.DETECTION_TEST_STOPPED,
        )
        types = [type(e).__name__ for e in _setup_sensors([md2])]
        assert "DetectionStateSensor" in types

    def test_setup_skipped_when_unsupported(self):
        md2 = _fake_md2(supports_detection_test=False)
        types = [type(e).__name__ for e in _setup_sensors([md2])]
        assert "DetectionStateSensor" not in types

# ===========================================================================
# Siren sensors: SirenBatterySensor / SirenMainPowerSensor / SirenSolarChargingSensor
# (hass#120 audit) + the 4 power-supply-fault binary_sensors that are gated on
# the same supports_power_supply flag.
# ===========================================================================


def test_siren_battery_sensor():
    s = _new(SirenBatterySensor)
    s._device = SimpleNamespace(
        power_supply=SimpleNamespace(battery_percentage_remaining=73)
    )
    assert s.native_value == 73


def test_siren_main_power_and_solar_enum_lowercased():
    mp = _new(SirenMainPowerSensor)
    mp._device = SimpleNamespace(
        power_supply=SimpleNamespace(main_power_supply=SimpleNamespace(name="SOLAR"))
    )
    assert mp.native_value == "solar"
    assert mp.native_value in mp.options

    sc = _new(SirenSolarChargingSensor)
    sc._device = SimpleNamespace(
        power_supply=SimpleNamespace(solar_charging_score=SimpleNamespace(name="GOOD"))
    )
    assert sc.native_value == "good"


class TestSirenSensorInits2:
    """sensor.py lines 1144-1145, 1163-1164, 1186-1187: Siren sensor __init__ methods."""

    def test_siren_battery_sensor_init(self):
        """Lines 1142-1145: SirenBatterySensor.__init__."""
        dev = _fake_dev("s1")
        sensor = SirenBatterySensor(dev, "entry1")
        assert "siren_battery" in sensor._attr_unique_id

    def test_siren_main_power_sensor_init(self):
        """Lines 1161-1164: SirenMainPowerSensor.__init__."""
        dev = _fake_dev("s1")
        sensor = SirenMainPowerSensor(dev, "entry1")
        assert "siren_main_power" in sensor._attr_unique_id

    def test_siren_solar_charging_sensor_init(self):
        """Lines 1184-1187: SirenSolarChargingSensor.__init__."""
        dev = _fake_dev("s1")
        sensor = SirenSolarChargingSensor(dev, "entry1")
        assert "siren_solar_charging" in sensor._attr_unique_id


class TestSirenSensorNativeValueErrors:
    """sensor.py lines 1171-1172, 1196-1197: error paths in siren sensor native_value."""

    def test_siren_main_power_attribute_error(self):
        """Lines 1171-1172: AttributeError → return None."""
        ent = SirenMainPowerSensor.__new__(SirenMainPowerSensor)
        ent._device = SimpleNamespace(power_supply=None)
        # power_supply is None → .main_power_supply raises AttributeError
        assert ent.native_value is None

    def test_siren_solar_charging_attribute_error(self):
        """Lines 1196-1197: AttributeError → return None."""
        ent = SirenSolarChargingSensor.__new__(SirenSolarChargingSensor)
        ent._device = SimpleNamespace(power_supply=None)
        assert ent.native_value is None


class TestSirenPowerSupplyFaultSensors:
    """Outdoor Siren power-supply fault flags (ac_dc_error/battery_defect/
    battery_temperature_abnormal/primary_power_supply_outage) must be wired
    into binary_sensor entities, gated on supports_power_supply, same as the
    existing sensor.py SirenBatterySensor/SirenMainPowerSensor/
    SirenSolarChargingSensor triplet.
    """

    def _make_siren_dev(self, dev_id="siren1", power_supply=None):
        return _fake_dev(
            dev_id,
            supports_batterylevel=False,
            supports_power_supply=True,
            power_supply=power_supply or MagicMock(),
        )

    def test_ac_dc_error_sensor_init(self):
        from custom_components.bosch_shc.binary_sensor import SirenAcDcErrorSensor

        dev = self._make_siren_dev("s1")
        sensor = SirenAcDcErrorSensor(dev, "entry1")
        assert sensor._attr_unique_id == "root1_s1_ac_dc_error"

    def test_battery_defect_sensor_init(self):
        from custom_components.bosch_shc.binary_sensor import SirenBatteryDefectSensor

        dev = self._make_siren_dev("s2")
        sensor = SirenBatteryDefectSensor(dev, "entry1")
        assert sensor._attr_unique_id == "root1_s2_battery_defect"

    def test_battery_temperature_abnormal_sensor_init(self):
        from custom_components.bosch_shc.binary_sensor import (
            SirenBatteryTemperatureAbnormalSensor,
        )

        dev = self._make_siren_dev("s3")
        sensor = SirenBatteryTemperatureAbnormalSensor(dev, "entry1")
        assert sensor._attr_unique_id == "root1_s3_battery_temperature_abnormal"

    def test_primary_power_supply_outage_sensor_init(self):
        from custom_components.bosch_shc.binary_sensor import (
            SirenPrimaryPowerSupplyOutageSensor,
        )

        dev = self._make_siren_dev("s4")
        sensor = SirenPrimaryPowerSupplyOutageSensor(dev, "entry1")
        assert sensor._attr_unique_id == "root1_s4_primary_power_supply_outage"

    def test_is_on_reads_underlying_power_supply_flags(self):
        from custom_components.bosch_shc.binary_sensor import (
            SirenAcDcErrorSensor,
            SirenBatteryDefectSensor,
            SirenBatteryTemperatureAbnormalSensor,
            SirenPrimaryPowerSupplyOutageSensor,
        )

        power_supply = SimpleNamespace(
            ac_dc_error=True,
            battery_defect=True,
            battery_temperature_abnormal=True,
            primary_power_supply_outage=True,
        )
        dev = self._make_siren_dev("s5", power_supply=power_supply)

        assert SirenAcDcErrorSensor(dev, "entry1").is_on is True
        assert SirenBatteryDefectSensor(dev, "entry1").is_on is True
        assert SirenBatteryTemperatureAbnormalSensor(dev, "entry1").is_on is True
        assert SirenPrimaryPowerSupplyOutageSensor(dev, "entry1").is_on is True

    def test_is_on_false_when_power_supply_flags_clear(self):
        from custom_components.bosch_shc.binary_sensor import (
            SirenAcDcErrorSensor,
            SirenBatteryDefectSensor,
            SirenBatteryTemperatureAbnormalSensor,
            SirenPrimaryPowerSupplyOutageSensor,
        )

        power_supply = SimpleNamespace(
            ac_dc_error=False,
            battery_defect=False,
            battery_temperature_abnormal=False,
            primary_power_supply_outage=False,
        )
        dev = self._make_siren_dev("s6", power_supply=power_supply)

        assert SirenAcDcErrorSensor(dev, "entry1").is_on is False
        assert SirenBatteryDefectSensor(dev, "entry1").is_on is False
        assert SirenBatteryTemperatureAbnormalSensor(dev, "entry1").is_on is False
        assert SirenPrimaryPowerSupplyOutageSensor(dev, "entry1").is_on is False

    def _setup_with_siren(self, siren):
        """Run async_setup_entry with a single outdoor siren and return the
        list of created entity type names."""
        from custom_components.bosch_shc.binary_sensor import (
            async_setup_entry as binary_sensor_setup_entry,
        )

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
        dh.climate_controls = []

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
            _run(binary_sensor_setup_entry(hass, entry, lambda ents, **kw: collected.extend(ents)))

        return [type(e).__name__ for e in collected]

    def test_setup_creates_power_supply_fault_sensors_when_supported(self):
        """supports_power_supply=True -> all 4 fault binary_sensors added."""
        siren = _fake_dev(
            "s1",
            siren=MagicMock(),
            supports_batterylevel=False,
            supports_power_supply=True,
        )
        types = self._setup_with_siren(siren)
        assert "SirenAcDcErrorSensor" in types
        assert "SirenBatteryDefectSensor" in types
        assert "SirenBatteryTemperatureAbnormalSensor" in types
        assert "SirenPrimaryPowerSupplyOutageSensor" in types

    def test_setup_skips_power_supply_fault_sensors_when_unsupported(self):
        """supports_power_supply=False (or missing) -> none of the 4 created."""
        siren = _fake_dev(
            "s1",
            siren=MagicMock(),
            supports_batterylevel=False,
            supports_power_supply=False,
        )
        types = self._setup_with_siren(siren)
        assert "SirenAcDcErrorSensor" not in types
        assert "SirenBatteryDefectSensor" not in types
        assert "SirenBatteryTemperatureAbnormalSensor" not in types
        assert "SirenPrimaryPowerSupplyOutageSensor" not in types
        # Baseline read-only siren sensors are still created regardless.
        assert "SirenTamperSensor" in types


class TestSensorSirenSetup:
    """sensor.py lines 445-454: siren sensor setup."""

    def _run_sensor_setup_sirens(self, sirens, options=None):
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
        dh.smart_plugs = []
        dh.smart_plugs_compact = []
        dh.light_switches_bsm = []
        dh.micromodule_light_controls = []
        dh.micromodule_shutter_controls = []
        dh.micromodule_blinds = []
        dh.universal_switches = []
        dh.water_leakage_detectors = []
        dh.outdoor_sirens = sirens

        session = MagicMock()
        session.device_helper = dh
        session.emma = _fake_dev("emma1")

        hass = _fake_hass(session=session)
        entry = _fake_entry(hass=hass, options=options or {})

        with patch(_PATCH_MIGRATE, new_callable=AsyncMock):
            collected = []
            _run(async_setup_entry(hass, entry, lambda ents, **kw: collected.extend(ents)))
        return collected

    def test_siren_battery_solar_added_when_power_supply_supported(self):
        """Lines 447-456: supports_power_supply=True → 3 siren sensors added."""
        siren = _fake_dev("s1", supports_power_supply=True, supports_batterylevel=False)
        collected = self._run_sensor_setup_sirens([siren])
        types = [type(e).__name__ for e in collected]
        assert "SirenBatterySensor" in types
        assert "SirenMainPowerSensor" in types
        assert "SirenSolarChargingSensor" in types

    def test_siren_excluded_skips_sensors(self):
        """Lines 445-446: device_excluded → continue."""
        siren = _fake_dev("s1", supports_power_supply=True, supports_batterylevel=False)
        collected = self._run_sensor_setup_sirens(
            [siren], options={OPT_EXCLUDED_DEVICES: ["s1"]}
        )
        types = [type(e).__name__ for e in collected]
        assert "SirenBatterySensor" not in types


# ===========================================================================
# NextSetpointTemperatureSensor / PresenceSimulationRunning* /
# ReferenceMovingTime* sensors (hass#120 audit — ROOM_CLIMATE_CONTROL / Shutter
# Control II diagnostics)
# ===========================================================================


def test_next_setpoint_temperature_reads_value_and_attributes():
    from boschshcpy.services_impl import RoomClimateControlService

    s = _new(NextSetpointTemperatureSensor)
    s._device = SimpleNamespace(
        next_setpoint_temperature=18.5,
        next_setpoint_temperature_change="2026-07-07T22:00:00Z",
        next_operation_mode=RoomClimateControlService.OperationMode.MANUAL,
    )
    assert s.native_value == 18.5
    attrs = s.extra_state_attributes
    assert attrs["next_change_at"] == "2026-07-07T22:00:00Z"
    assert attrs["next_operation_mode"] == "MANUAL"


def test_next_setpoint_temperature_safe_when_attrs_missing():
    """Older boschshcpy without these properties -> None, no crash."""
    s = _new(NextSetpointTemperatureSensor)
    s._device = SimpleNamespace()
    assert s.native_value is None
    attrs = s.extra_state_attributes
    assert attrs["next_change_at"] is None
    assert attrs["next_operation_mode"] is None


def test_next_setpoint_temperature_device_name_uses_room_name():
    """hass#372: must report the room's own name, not the shared
    ROOM_CLIMATE_CONTROL device's generic raw name.
    """
    device = SimpleNamespace(
        id="roomClimateControl_hz_1",
        root_device_id="shc1",
        name="-RoomClimateControl-",
    )
    s = NextSetpointTemperatureSensor(device=device, entry_id="entry1", room_name="Büro")
    assert s.device_name == "Büro"


def test_next_setpoint_temperature_device_name_falls_back_without_room_name():
    device = SimpleNamespace(
        id="roomClimateControl_hz_1",
        root_device_id="shc1",
        name="-RoomClimateControl-",
    )
    s = NextSetpointTemperatureSensor(device=device, entry_id="entry1")
    assert s.device_name == "-RoomClimateControl-"


def test_presence_simulation_running_start_reads_value():
    s = _new(PresenceSimulationRunningStartSensor)
    s._device = SimpleNamespace(running_start_time="2026-07-07T18:00:00Z")
    assert s.native_value == "2026-07-07T18:00:00Z"


def test_presence_simulation_running_start_none_when_not_running():
    s = _new(PresenceSimulationRunningStartSensor)
    s._device = SimpleNamespace(running_start_time=None)
    assert s.native_value is None


def test_presence_simulation_running_end_reads_value():
    s = _new(PresenceSimulationRunningEndSensor)
    s._device = SimpleNamespace(running_end_time="2026-07-07T23:00:00Z")
    assert s.native_value == "2026-07-07T23:00:00Z"


def test_presence_simulation_running_end_safe_when_attr_missing():
    """Older boschshcpy without the property -> None, no crash."""
    s = _new(PresenceSimulationRunningEndSensor)
    s._device = SimpleNamespace()
    assert s.native_value is None


def test_reference_moving_time_top_to_bottom_converts_ms_to_seconds():
    s = _new(ReferenceMovingTimeTopToBottomSensor)
    s._device = SimpleNamespace(reference_moving_time_top_to_bottom_ms=12500)
    assert s.native_value == 12.5


def test_reference_moving_time_top_to_bottom_none_when_uncalibrated():
    s = _new(ReferenceMovingTimeTopToBottomSensor)
    s._device = SimpleNamespace(reference_moving_time_top_to_bottom_ms=None)
    assert s.native_value is None


def test_reference_moving_time_bottom_to_top_converts_ms_to_seconds():
    s = _new(ReferenceMovingTimeBottomToTopSensor)
    s._device = SimpleNamespace(reference_moving_time_bottom_to_top_ms=9800)
    assert s.native_value == 9.8


def test_reference_moving_time_bottom_to_top_safe_when_attr_missing():
    """Older boschshcpy without the property -> None, no crash."""
    s = _new(ReferenceMovingTimeBottomToTopSensor)
    s._device = SimpleNamespace()
    assert s.native_value is None

# ===========================================================================
# async_setup_entry — full entity-count + type tests, and real __init__ chains
# ===========================================================================


class TestAsyncSetupEntryEntityCounts:
    """Verify the right entity types + counts are produced for each device group."""

    def test_empty_session_yields_only_emma(self):
        session = _make_fake_session()
        entities = _run_setup(session)
        # Only the EmmaPowerSensor is always added
        assert len(entities) == 2
        assert isinstance(entities[0], EmmaPowerSensor)

    def test_one_thermostat_yields_temperature_and_valve_tappet(self):
        dev = _fake_device(name="TRV1", device_id="hdm:TRV:001")
        session = _make_fake_session(thermostats=[dev])
        entities = _run_setup(session)
        # 2 from thermostat + 1 EMMA
        assert len(entities) == 4
        types = [type(e) for e in entities]
        assert TemperatureSensor in types
        assert ValveTappetSensor in types
        assert EmmaPowerSensor in types

    def test_two_thermostats_yield_four_sensors_plus_emma(self):
        devs = [
            _fake_device(name="TRV1", device_id="hdm:TRV:001"),
            _fake_device(name="TRV2", device_id="hdm:TRV:002"),
        ]
        session = _make_fake_session(thermostats=devs)
        entities = _run_setup(session)
        assert len(entities) == 6  # 2×(Temp+Valve) + EMMA
        assert sum(isinstance(e, TemperatureSensor) for e in entities) == 2
        assert sum(isinstance(e, ValveTappetSensor) for e in entities) == 2

    def test_one_wallthermostat_yields_temperature_and_humidity(self):
        dev = _fake_device(name="WT1", device_id="hdm:WT:001")
        session = _make_fake_session(wallthermostats=[dev])
        entities = _run_setup(session)
        # 2 + EMMA
        assert len(entities) == 4
        types = [type(e) for e in entities]
        assert TemperatureSensor in types
        assert HumiditySensor in types

    def test_one_roomthermostat_yields_temperature_and_humidity(self):
        dev = _fake_device(name="RT1", device_id="hdm:RT:001")
        session = _make_fake_session(roomthermostats=[dev])
        entities = _run_setup(session)
        assert len(entities) == 4
        types = [type(e) for e in entities]
        assert TemperatureSensor in types
        assert HumiditySensor in types

    def test_wallthermostat_and_roomthermostat_combine(self):
        wt = _fake_device(name="WT1", device_id="hdm:WT:001")
        rt = _fake_device(name="RT1", device_id="hdm:RT:001")
        session = _make_fake_session(wallthermostats=[wt], roomthermostats=[rt])
        entities = _run_setup(session)
        # 4 (2 devices × 2 sensors each) + EMMA
        assert len(entities) == 6
        assert sum(isinstance(e, TemperatureSensor) for e in entities) == 2
        assert sum(isinstance(e, HumiditySensor) for e in entities) == 2

    def test_one_twinguard_yields_nine_sensors(self):
        dev = _fake_device(name="TG1", device_id="hdm:TG:001")
        session = _make_fake_session(twinguards=[dev])
        entities = _run_setup(session)
        # Temp, Humidity, Purity, AirQuality, TempRating, HumidityRating, PurityRating
        # + CombinedRating (diag), Description (diag) + EMMA
        assert len(entities) == 11
        types = [type(e) for e in entities]
        assert TemperatureSensor in types
        assert HumiditySensor in types
        assert PuritySensor in types
        assert AirQualitySensor in types
        assert TemperatureRatingSensor in types
        assert HumidityRatingSensor in types
        assert PurityRatingSensor in types
        assert TwinguardCombinedRatingSensor in types
        assert TwinguardDescriptionSensor in types

    def test_smart_plug_yields_power_and_energy(self):
        dev = _fake_device(name="SmartPlug1", device_id="hdm:SP:001")
        session = _make_fake_session(smart_plugs=[dev])
        entities = _run_setup(session)
        # 2 + EMMA
        assert len(entities) == 4
        types = [type(e) for e in entities]
        assert PowerSensor in types
        assert EnergySensor in types

    def test_light_switch_bsm_yields_power_and_energy(self):
        dev = _fake_device(name="LSB1", device_id="hdm:LSB:001")
        session = _make_fake_session(light_switches_bsm=[dev])
        entities = _run_setup(session)
        assert len(entities) == 4
        types = [type(e) for e in entities]
        assert PowerSensor in types
        assert EnergySensor in types

    def test_micromodule_light_control_yields_power_and_energy(self):
        dev = _fake_device(name="MLC1", device_id="hdm:MLC:001")
        session = _make_fake_session(micromodule_light_controls=[dev])
        entities = _run_setup(session)
        assert len(entities) == 4
        assert sum(isinstance(e, PowerSensor) for e in entities) == 1
        assert sum(isinstance(e, EnergySensor) for e in entities) == 1

    def test_micromodule_shutter_control_yields_power_and_energy(self):
        dev = _fake_device(name="MSC1", device_id="hdm:MSC:001")
        session = _make_fake_session(micromodule_shutter_controls=[dev])
        entities = _run_setup(session)
        # Power + Energy + 2 reference-moving-time diagnostics (diagnostic
        # entities default enabled) + EMMA.
        assert len(entities) == 6
        assert sum(isinstance(e, PowerSensor) for e in entities) == 1
        assert sum(isinstance(e, EnergySensor) for e in entities) == 1
        assert (
            sum(isinstance(e, ReferenceMovingTimeTopToBottomSensor) for e in entities)
            == 1
        )
        assert (
            sum(isinstance(e, ReferenceMovingTimeBottomToTopSensor) for e in entities)
            == 1
        )

    def test_micromodule_blinds_yields_power_and_energy(self):
        dev = _fake_device(name="MB1", device_id="hdm:MB:001")
        session = _make_fake_session(micromodule_blinds=[dev])
        entities = _run_setup(session)
        # Power + Energy + 2 reference-moving-time diagnostics + EMMA.
        assert len(entities) == 6
        assert sum(isinstance(e, PowerSensor) for e in entities) == 1
        assert sum(isinstance(e, EnergySensor) for e in entities) == 1

    def test_shutter_control_yields_reference_moving_time_diagnostics(self):
        dev = _fake_device(name="BBL1", device_id="hdm:BBL:001")
        session = _make_fake_session(shutter_controls=[dev])
        entities = _run_setup(session)
        # No power/energy for plain shutter_controls, only the 2 diagnostics + EMMA.
        assert len(entities) == 4
        assert (
            sum(isinstance(e, ReferenceMovingTimeTopToBottomSensor) for e in entities)
            == 1
        )
        assert (
            sum(isinstance(e, ReferenceMovingTimeBottomToTopSensor) for e in entities)
            == 1
        )

    def test_shutter_control_diagnostics_suppressed_when_diagnostic_disabled(self):
        dev = _fake_device(name="BBL1", device_id="hdm:BBL:001")
        session = _make_fake_session(shutter_controls=[dev])
        entities = _run_setup(session, options={"diagnostic_entities": False})
        assert len(entities) == 2  # only EMMA
        assert not any(
            isinstance(
                e,
                (
                    ReferenceMovingTimeTopToBottomSensor,
                    ReferenceMovingTimeBottomToTopSensor,
                ),
            )
            for e in entities
        )

    def test_smart_plug_compact_yields_power_energy_and_comm_quality(self):
        dev = _fake_device(name="SPC1", device_id="hdm:SPC:001")
        session = _make_fake_session(smart_plugs_compact=[dev])
        entities = _run_setup(session)
        # 3 + EMMA
        assert len(entities) == 5
        types = [type(e) for e in entities]
        assert PowerSensor in types
        assert EnergySensor in types
        assert CommunicationQualitySensor in types

    def test_motion_detector_yields_illuminance(self):
        dev = _fake_device(name="MD1", device_id="hdm:MD:001")
        session = _make_fake_session(motion_detectors=[dev])
        entities = _run_setup(session)
        # 1 + EMMA
        assert len(entities) == 3
        assert isinstance(entities[0], IlluminanceLevelSensor)

    def test_motion_detector2_yields_illuminance_and_temperature(self):
        """Gen2 MD2 produces IlluminanceLevelSensor + TemperatureSensor + CommunicationQualitySensor."""
        dev = _fake_device(name="MD2", device_id="hdm:MD2:001")
        session = _make_fake_session(motion_detectors2=[dev])
        entities = _run_setup(session)
        # IlluminanceLevelSensor + TemperatureSensor + CommunicationQualitySensor (diag) + EMMA
        assert len(entities) == 5
        types = [type(e) for e in entities]
        assert IlluminanceLevelSensor in types
        assert TemperatureSensor in types
        assert CommunicationQualitySensor in types

    def test_motion_detector2_and_gen1_both_yield_illuminance(self):
        """Gen1 + Gen2 MD each produce their own IlluminanceLevelSensor."""
        dev1 = _fake_device(name="MD1", device_id="hdm:MD:001")
        dev2 = _fake_device(name="MD2-G2", device_id="hdm:MD2:001")
        session = _make_fake_session(motion_detectors=[dev1], motion_detectors2=[dev2])
        entities = _run_setup(session)
        # MD1: 1 IlluminanceLevelSensor; MD2: Illuminance + Temperature + CommunicationQuality + EMMA
        assert len(entities) == 6
        assert sum(isinstance(e, IlluminanceLevelSensor) for e in entities) == 2

    def test_no_entities_without_devices_adds_nothing_except_emma(self):
        """async_add_entities is called once (EMMA always present); result has 1 entity."""
        session = _make_fake_session()
        entities = _run_setup(session)
        assert len(entities) == 2  # EMMA always present

    def test_mixed_devices_all_entity_types_present(self):
        """One of every device group → all sensor types present."""
        session = _make_fake_session(
            thermostats=[_fake_device("TRV", "hdm:TRV:1")],
            wallthermostats=[_fake_device("WT", "hdm:WT:1")],
            twinguards=[_fake_device("TG", "hdm:TG:1")],
            smart_plugs=[_fake_device("SP", "hdm:SP:1")],
            smart_plugs_compact=[_fake_device("SPC", "hdm:SPC:1")],
            motion_detectors=[_fake_device("MD", "hdm:MD:1")],
        )
        entities = _run_setup(session)
        types = {type(e) for e in entities}
        assert TemperatureSensor in types
        assert ValveTappetSensor in types
        assert HumiditySensor in types
        assert PuritySensor in types
        assert AirQualitySensor in types
        assert TemperatureRatingSensor in types
        assert HumidityRatingSensor in types
        assert PurityRatingSensor in types
        assert PowerSensor in types
        assert EnergySensor in types
        assert CommunicationQualitySensor in types
        assert IlluminanceLevelSensor in types
        assert EmmaPowerSensor in types
        assert TwinguardCombinedRatingSensor in types
        assert TwinguardDescriptionSensor in types


class TestRealInitUniqueIdAndName:
    """Verify that real __init__ (not __new__ bypass) sets unique_id and name."""

    def _dev(self, name="MyDev", did="hdm:Dev:001", rid="root-abc", serial="SER1"):
        return _fake_device(name=name, device_id=did, root_device_id=rid, serial=serial)

    def test_temperature_sensor_unique_id(self):
        d = self._dev()
        s = TemperatureSensor(device=d, entry_id=ENTRY_ID)
        assert s.unique_id == f"{d.root_device_id}_{d.id}_temperature"

    def test_temperature_sensor_name(self):
        # name comes from device_class (SensorDeviceClass.TEMPERATURE); _attr_name is None
        d = self._dev(name="Living Room TRV")
        s = TemperatureSensor(device=d, entry_id=ENTRY_ID)
        assert s._attr_name is None

    def test_humidity_sensor_unique_id(self):
        d = self._dev()
        s = HumiditySensor(device=d, entry_id=ENTRY_ID)
        assert s.unique_id == f"{d.root_device_id}_{d.id}_humidity"

    def test_humidity_sensor_name(self):
        # name comes from device_class (SensorDeviceClass.HUMIDITY); _attr_name is None
        d = self._dev(name="Bedroom WT")
        s = HumiditySensor(device=d, entry_id=ENTRY_ID)
        assert s._attr_name is None

    def test_purity_sensor_unique_id(self):
        d = self._dev()
        s = PuritySensor(device=d, entry_id=ENTRY_ID)
        assert s.unique_id == f"{d.root_device_id}_{d.id}_purity"

    def test_purity_sensor_name(self):
        d = self._dev(name="TG Office")
        s = PuritySensor(device=d, entry_id=ENTRY_ID)
        assert s.translation_key == "purity"

    def test_air_quality_sensor_unique_id(self):
        d = self._dev()
        s = AirQualitySensor(device=d, entry_id=ENTRY_ID)
        assert s.unique_id == f"{d.root_device_id}_{d.id}_airquality"

    def test_air_quality_sensor_name(self):
        d = self._dev(name="Hall TG")
        s = AirQualitySensor(device=d, entry_id=ENTRY_ID)
        assert s.translation_key == "air_quality"

    def test_temperature_rating_sensor_unique_id(self):
        d = self._dev()
        s = TemperatureRatingSensor(device=d, entry_id=ENTRY_ID)
        assert s.unique_id == f"{d.root_device_id}_{d.id}_temperaturerating"

    def test_temperature_rating_sensor_name(self):
        d = self._dev(name="Kitchen TG")
        s = TemperatureRatingSensor(device=d, entry_id=ENTRY_ID)
        assert s.translation_key == "temperature_rating"

    def test_humidity_rating_sensor_unique_id(self):
        d = self._dev()
        s = HumidityRatingSensor(device=d, entry_id=ENTRY_ID)
        assert s.unique_id == f"{d.root_device_id}_{d.id}_humidityrating"

    def test_humidity_rating_sensor_name(self):
        d = self._dev(name="Bedroom TG")
        s = HumidityRatingSensor(device=d, entry_id=ENTRY_ID)
        assert s.translation_key == "humidity_rating"

    def test_purity_rating_sensor_unique_id(self):
        d = self._dev()
        s = PurityRatingSensor(device=d, entry_id=ENTRY_ID)
        assert s.unique_id == f"{d.root_device_id}_{d.id}_purityrating"

    def test_purity_rating_sensor_name(self):
        d = self._dev(name="Cellar TG")
        s = PurityRatingSensor(device=d, entry_id=ENTRY_ID)
        assert s.translation_key == "purity_rating"

    def test_power_sensor_unique_id(self):
        d = self._dev()
        s = PowerSensor(device=d, entry_id=ENTRY_ID)
        assert s.unique_id == f"{d.root_device_id}_{d.id}_power"

    def test_power_sensor_name(self):
        # name comes from device_class (SensorDeviceClass.POWER); _attr_name is None
        d = self._dev(name="Smart Plug A")
        s = PowerSensor(device=d, entry_id=ENTRY_ID)
        assert s._attr_name is None

    def test_energy_sensor_unique_id(self):
        d = self._dev()
        s = EnergySensor(device=d, entry_id=ENTRY_ID)
        assert s.unique_id == f"{d.root_device_id}_{d.id}_energy"

    def test_energy_sensor_name(self):
        # name comes from device_class (SensorDeviceClass.ENERGY); _attr_name is None
        d = self._dev(name="Smart Plug B")
        s = EnergySensor(device=d, entry_id=ENTRY_ID)
        assert s._attr_name is None

    def test_communication_quality_sensor_unique_id(self):
        d = self._dev()
        s = CommunicationQualitySensor(device=d, entry_id=ENTRY_ID)
        assert s.unique_id == f"{d.root_device_id}_{d.id}_communicationquality"

    def test_communication_quality_sensor_name(self):
        d = self._dev(name="SPC 1")
        s = CommunicationQualitySensor(device=d, entry_id=ENTRY_ID)
        assert s.translation_key == "communication_quality"

    def test_valve_tappet_sensor_unique_id(self):
        d = self._dev()
        s = ValveTappetSensor(device=d, entry_id=ENTRY_ID)
        assert s.unique_id == f"{d.root_device_id}_{d.id}_valvetappet"

    def test_valve_tappet_sensor_name(self):
        d = self._dev(name="Bedroom TRV")
        s = ValveTappetSensor(device=d, entry_id=ENTRY_ID)
        assert s.translation_key == "valve_tappet"

    def test_illuminance_sensor_unique_id(self):
        d = self._dev()
        s = IlluminanceLevelSensor(device=d, entry_id=ENTRY_ID)
        assert s.unique_id == f"{d.root_device_id}_{d.id}_illuminance"

    def test_illuminance_sensor_name(self):
        # name comes from device_class (SensorDeviceClass.ILLUMINANCE); _attr_name is None
        d = self._dev(name="Motion Hall")
        s = IlluminanceLevelSensor(device=d, entry_id=ENTRY_ID)
        assert s._attr_name is None

    def test_emma_power_sensor_unique_id(self):
        d = _fake_device(
            name="EMMA", device_id="com.bosch.tt.emma.applink", root_device_id="shc-mac"
        )
        s = EmmaPowerSensor(device=d, entry_id=ENTRY_ID)
        assert s.unique_id == "shc-mac_com.bosch.tt.emma.applink_power"

    def test_emma_power_sensor_name(self):
        # name comes from device_class (SensorDeviceClass.POWER); _attr_name is None
        d = _fake_device(
            name="EMMA", device_id="com.bosch.tt.emma.applink", root_device_id="shc-mac"
        )
        s = EmmaPowerSensor(device=d, entry_id=ENTRY_ID)
        assert s._attr_name is None

    def test_entry_id_stored_on_base_entity(self):
        d = self._dev()
        s = TemperatureSensor(device=d, entry_id=ENTRY_ID)
        assert s._entry_id == ENTRY_ID

    def test_device_stored_on_base_entity(self):
        d = self._dev(name="SomeDevice")
        s = HumiditySensor(device=d, entry_id=ENTRY_ID)
        assert s._device is d


# ===========================================================================
# device_excluded continue branches in async_setup_entry
# ===========================================================================


class TestDeviceExcludedContinueBranches:
    """Each excluded device must be skipped (continue), leaving no entity for it.

    Targets: thermostats(45-46), wallthermostats/roomthermostats(70-71),
    twinguards(93-94), smart_plugs/light_switches/micromodule_*(192-193),
    smart_plugs_compact(222-223), motion_detectors(266-267),
    motion_detectors2(276-277), shutter_contacts2 diag path(312-313),
    big battery loop(351-352), supports_batterylevel False(354).
    """

    def _excl_opts(self, *device_ids):
        return {OPT_EXCLUDED_DEVICES: list(device_ids)}

    # --- thermostats (line 45-46) ---

    def test_excluded_thermostat_not_in_entities(self):
        dev = _fake_device_excl("trv-excl", name="TRV")
        session = _make_fake_session_excl(thermostats=[dev])
        entities = _run_setup_with_options(session, self._excl_opts("trv-excl"))
        ids = [getattr(e, "_device", None) and e._device.id for e in entities]
        assert "trv-excl" not in ids

    def test_non_excluded_thermostat_still_in_entities(self):
        kept = _fake_device_excl("trv-kept", name="TRV Kept")
        excl = _fake_device_excl("trv-excl", name="TRV Excl")
        session = _make_fake_session_excl(thermostats=[kept, excl])
        entities = _run_setup_with_options(session, self._excl_opts("trv-excl"))
        ids = [getattr(e, "_device", None) and e._device.id for e in entities]
        assert "trv-kept" in ids
        assert "trv-excl" not in ids

    # --- wallthermostats/roomthermostats (line 70-71) ---

    def test_excluded_wallthermostat_not_in_entities(self):
        dev = _fake_device_excl("wt-excl", name="Wall Therm")
        session = _make_fake_session_excl(wallthermostats=[dev])
        entities = _run_setup_with_options(session, self._excl_opts("wt-excl"))
        ids = [getattr(e, "_device", None) and e._device.id for e in entities]
        assert "wt-excl" not in ids

    def test_excluded_roomthermostat_not_in_entities(self):
        dev = _fake_device_excl("rt-excl", name="Room Therm")
        session = _make_fake_session_excl(roomthermostats=[dev])
        entities = _run_setup_with_options(session, self._excl_opts("rt-excl"))
        ids = [getattr(e, "_device", None) and e._device.id for e in entities]
        assert "rt-excl" not in ids

    # --- twinguards (line 93-94) ---

    def test_excluded_twinguard_not_in_entities(self):
        dev = _fake_device_excl("tg-excl", name="Twinguard")
        session = _make_fake_session_excl(twinguards=[dev])
        entities = _run_setup_with_options(session, self._excl_opts("tg-excl"))
        ids = [getattr(e, "_device", None) and e._device.id for e in entities]
        assert "tg-excl" not in ids

    # --- smart_plugs / light_switches_bsm / micromodule_* (line 192-193) ---

    def test_excluded_smart_plug_not_in_entities(self):
        dev = _fake_device_excl("sp-excl", name="Smart Plug")
        session = _make_fake_session_excl(smart_plugs=[dev])
        entities = _run_setup_with_options(session, self._excl_opts("sp-excl"))
        ids = [getattr(e, "_device", None) and e._device.id for e in entities]
        assert "sp-excl" not in ids

    def test_excluded_light_switch_bsm_not_in_entities(self):
        dev = _fake_device_excl("lsb-excl", name="Light Switch")
        session = _make_fake_session_excl(light_switches_bsm=[dev])
        entities = _run_setup_with_options(session, self._excl_opts("lsb-excl"))
        ids = [getattr(e, "_device", None) and e._device.id for e in entities]
        assert "lsb-excl" not in ids

    def test_excluded_micromodule_shutter_not_in_entities(self):
        dev = _fake_device_excl("msc-excl", name="MicromoduleShutter")
        session = _make_fake_session_excl(micromodule_shutter_controls=[dev])
        entities = _run_setup_with_options(session, self._excl_opts("msc-excl"))
        ids = [getattr(e, "_device", None) and e._device.id for e in entities]
        assert "msc-excl" not in ids

    # --- smart_plugs_compact (line 222-223) ---

    def test_excluded_smart_plug_compact_not_in_entities(self):
        dev = _fake_device_excl("spc-excl", name="SPC")
        session = _make_fake_session_excl(smart_plugs_compact=[dev])
        entities = _run_setup_with_options(session, self._excl_opts("spc-excl"))
        ids = [getattr(e, "_device", None) and e._device.id for e in entities]
        assert "spc-excl" not in ids

    # --- motion_detectors (line 266-267) ---

    def test_excluded_motion_detector_not_in_entities(self):
        dev = _fake_device_excl("md-excl", name="Motion")
        session = _make_fake_session_excl(motion_detectors=[dev])
        entities = _run_setup_with_options(session, self._excl_opts("md-excl"))
        ids = [getattr(e, "_device", None) and e._device.id for e in entities]
        assert "md-excl" not in ids

    # --- motion_detectors2 (line 276-277) ---

    def test_excluded_motion_detector2_not_in_entities(self):
        dev = _fake_device_excl("md2-excl", name="Motion2")
        session = _make_fake_session_excl(motion_detectors2=[dev])
        entities = _run_setup_with_options(session, self._excl_opts("md2-excl"))
        ids = [getattr(e, "_device", None) and e._device.id for e in entities]
        assert "md2-excl" not in ids

    # --- shutter_contacts2 battery/diag path (line 312-313) ---

    def test_excluded_shutter_contact2_not_in_battery_entities(self):
        # diagnostic_enabled=True by default; shutter_contacts2 enter the diagnostic
        # battery loop (line 338-359). Excluded device must be skipped there too.
        dev = _fake_device_excl("sc2-excl", name="SC2", supports_batterylevel=True)
        dev.communicationquality = SimpleNamespace(name="GOOD")
        session = _make_fake_session_excl(
            shutter_contacts2=[dev],
        )
        entities = _run_setup_with_options(session, self._excl_opts("sc2-excl"))
        ids = [getattr(e, "_device", None) and e._device.id for e in entities]
        assert "sc2-excl" not in ids

    # --- big battery loop device_excluded (line 351-352) ---

    def test_excluded_device_skipped_in_battery_loop(self):
        """Device in motion_detectors that is excluded → no BatteryLevelSensor."""
        dev = _fake_device_excl(
            "md-bat-excl", name="Motion Bat", supports_batterylevel=True
        )
        session = _make_fake_session_excl(motion_detectors=[dev])
        entities = _run_setup_with_options(session, self._excl_opts("md-bat-excl"))
        bat_entities = [e for e in entities if isinstance(e, BatteryLevelSensor)]
        assert bat_entities == []

    # --- supports_batterylevel False (line 354) ---

    def test_device_without_battery_support_skips_battery_entity(self):
        """supports_batterylevel=False → BatteryLevelSensor not added (line 354)."""
        dev = _fake_device_excl(
            "md-no-bat", name="Motion NoBat", supports_batterylevel=False
        )
        session = _make_fake_session_excl(motion_detectors=[dev])
        entities = _run_setup_with_options(session, {})
        bat_entities = [e for e in entities if isinstance(e, BatteryLevelSensor)]
        assert bat_entities == []

    # --- All-excluded sanity test ---

    def test_all_device_types_excluded_yields_only_emma(self):
        """Excluding every device in every list leaves only the EMMA sensor."""
        excl_ids = [f"dev-{i}" for i in range(9)]
        devs = [_fake_device_excl(did, name=did) for did in excl_ids]
        session = _make_fake_session_excl(
            thermostats=[devs[0]],
            wallthermostats=[devs[1]],
            twinguards=[devs[2]],
            smart_plugs=[devs[3]],
            smart_plugs_compact=[devs[4]],
            motion_detectors=[devs[5]],
            motion_detectors2=[devs[6]],
            shutter_contacts2=[devs[7]],
            roomthermostats=[devs[8]],
        )
        entities = _run_setup_with_options(
            session, {OPT_EXCLUDED_DEVICES: excl_ids}
        )
        assert len(entities) == 2
        assert isinstance(entities[0], EmmaPowerSensor)


# ===========================================================================
# ZigbeeRoutingQualitySensor
#
# Ground truth: only devices whose id starts with "hdm:ZigBee:" support
# SHCSessionAsync.get_zigbee_routing_info. That call is not delivered by the
# long-poll stream, so this entity is backed by a DataUpdateCoordinator
# (SHCZigbeeRoutingCoordinator, coordinator.py) per HA's documented pattern
# for polled data, rather than per-entity should_poll/async_update. The
# coordinator itself is unit-tested in test_coordinator.py; these tests cover
# the entity's read side (native_value/extra_state_attributes/available) and
# async_setup_entry wiring, using a lightweight fake coordinator (fixed
# `.data` dict) rather than a real DataUpdateCoordinator instance.
# ===========================================================================

_ZIGBEE_DEVICE_HELPER_LIST_ATTRS = (
    "thermostats", "wallthermostats", "roomthermostats", "twinguards",
    "smart_plugs", "light_switches_bsm", "micromodule_light_controls",
    "micromodule_shutter_controls", "micromodule_blinds", "smart_plugs_compact",
    "motion_detectors", "motion_detectors2", "shutter_contacts",
    "shutter_contacts2", "smoke_detectors", "universal_switches",
    "water_leakage_detectors",
)


def _empty_zigbee_device_helper():
    """A device_helper exposing every bucket sensor.py accesses directly as [].

    Optional buckets (climate_controls/shutter_controls/outdoor_sirens/
    presence_simulation_system) are read via getattr(..., default) in
    sensor.py, so they can be left unset here.
    """
    return SimpleNamespace(**{attr: [] for attr in _ZIGBEE_DEVICE_HELPER_LIST_ATTRS})


def _fake_coordinator(data=None, last_update_success=True):
    """A minimal stand-in for SHCZigbeeRoutingCoordinator: fixed `.data`, no
    actual scheduling/refresh logic (that lives in coordinator.py, tested
    separately)."""
    coordinator = MagicMock()
    coordinator.data = {} if data is None else data
    coordinator.last_update_success = last_update_success
    coordinator.async_add_listener = MagicMock(return_value=MagicMock())
    return coordinator


def _run_zigbee_setup(devices, options=None, device_lookup=None, coordinator=None):
    """Run async_setup_entry with a session exposing `.devices` (the Zigbee loop
    iterates session.devices directly, not a device_helper bucket) and a
    zigbee_routing_coordinator wired onto runtime_data, mirroring how
    __init__.py creates and stores it."""
    by_id = {d.id: d for d in devices}

    def _default_lookup(device_id):
        try:
            return by_id[device_id]
        except KeyError:
            raise KeyError(device_id) from None

    session = SimpleNamespace(
        device_helper=_empty_zigbee_device_helper(),
        emma=None,
        devices=devices,
        device=device_lookup or _default_lookup,
    )
    hass = _fake_hass(session=session)
    entry = _fake_entry(hass=hass, options=options or {})
    entry.runtime_data.zigbee_routing_coordinator = coordinator or _fake_coordinator()

    with patch(_PATCH_MIGRATE, new_callable=AsyncMock):
        collected: list = []
        _run(async_setup_entry(hass, entry, lambda ents, **kw: collected.extend(ents)))
    return collected, session


class TestZigbeeRoutingQualitySensorSetup:
    """async_setup_entry wiring for ZigbeeRoutingQualitySensor."""

    def test_created_only_for_zigbee_devices(self):
        zb = _fake_dev("hdm:ZigBee:001", name="Router Plug")
        other = _fake_dev("hdm:SC2:001", name="Contact")
        collected, _ = _run_zigbee_setup([zb, other])
        zb_entities = [e for e in collected if isinstance(e, ZigbeeRoutingQualitySensor)]
        assert len(zb_entities) == 1
        assert zb_entities[0]._device.id == "hdm:ZigBee:001"

    def test_no_zigbee_devices_yields_no_entity(self):
        other = _fake_dev("hdm:SC2:002", name="Contact")
        collected, _ = _run_zigbee_setup([other])
        assert not any(isinstance(e, ZigbeeRoutingQualitySensor) for e in collected)

    def test_gated_behind_diagnostic_option(self):
        zb = _fake_dev("hdm:ZigBee:002")
        collected, _ = _run_zigbee_setup(
            [zb], options={OPT_DIAGNOSTIC_ENTITIES: False}
        )
        assert not any(isinstance(e, ZigbeeRoutingQualitySensor) for e in collected)

    def test_excluded_device_skipped(self):
        zb = _fake_dev("hdm:ZigBee:003")
        collected, _ = _run_zigbee_setup(
            [zb], options={OPT_EXCLUDED_DEVICES: ["hdm:ZigBee:003"]}
        )
        assert not any(isinstance(e, ZigbeeRoutingQualitySensor) for e in collected)

    def test_no_devices_attribute_on_session_is_safe(self):
        """A session without `.devices` (e.g. an older/fake session) must not crash."""
        session = SimpleNamespace(
            device_helper=_empty_zigbee_device_helper(), emma=None
        )
        hass = SimpleNamespace()
        config_entry = SimpleNamespace(options={}, entry_id=ENTRY_ID)
        config_entry.runtime_data = SimpleNamespace(session=session)
        collected: list = []

        async def _inner():
            with patch(_PATCH_MIGRATE, side_effect=_noop_migrate):
                await async_setup_entry(
                    hass, config_entry, lambda ents: collected.extend(ents)
                )

        asyncio.run(_inner())
        assert len(collected) == 1  # only the always-on open-windows sensor

    def test_no_coordinator_on_runtime_data_is_safe(self):
        """runtime_data without a zigbee_routing_coordinator attribute (e.g. a
        stale/partial fake in another test) must not crash and must not create
        the entity."""
        zb = _fake_dev("hdm:ZigBee:007")
        session = SimpleNamespace(
            device_helper=_empty_zigbee_device_helper(), emma=None, devices=[zb]
        )
        hass = SimpleNamespace()
        config_entry = SimpleNamespace(options={}, entry_id=ENTRY_ID)
        config_entry.runtime_data = SimpleNamespace(session=session)
        collected: list = []

        async def _inner():
            with patch(_PATCH_MIGRATE, side_effect=_noop_migrate):
                await async_setup_entry(
                    hass, config_entry, lambda ents: collected.extend(ents)
                )

        asyncio.run(_inner())
        assert not any(isinstance(e, ZigbeeRoutingQualitySensor) for e in collected)

    def test_unique_id_format(self):
        zb = _fake_dev("hdm:ZigBee:004", root_id="root9")
        collected, _ = _run_zigbee_setup([zb])
        s = next(e for e in collected if isinstance(e, ZigbeeRoutingQualitySensor))
        assert s._attr_unique_id == "root9_hdm:ZigBee:004_zigbee_routing_quality"

    def test_disabled_by_default_diagnostic_enum(self):
        zb = _fake_dev("hdm:ZigBee:005")
        collected, _ = _run_zigbee_setup([zb])
        s = next(e for e in collected if isinstance(e, ZigbeeRoutingQualitySensor))
        assert s.entity_category == EntityCategory.DIAGNOSTIC
        assert s.entity_registry_enabled_default is False
        assert s.device_class == SensorDeviceClass.ENUM
        assert s.translation_key == "zigbee_routing_quality"
        assert set(s.options) == {
            "good", "medium", "bad", "no_connection",
            "device_not_initialized", "not_supported", "unknown",
        }

    def test_should_poll_false(self):
        """Coordinator entities never poll — the coordinator itself does."""
        zb = _fake_dev("hdm:ZigBee:006")
        collected, _ = _run_zigbee_setup([zb])
        s = next(e for e in collected if isinstance(e, ZigbeeRoutingQualitySensor))
        assert s.should_poll is False

    def test_coordinator_wired_from_runtime_data(self):
        zb = _fake_dev("hdm:ZigBee:008")
        coordinator = _fake_coordinator()
        collected, _ = _run_zigbee_setup([zb], coordinator=coordinator)
        s = next(e for e in collected if isinstance(e, ZigbeeRoutingQualitySensor))
        assert s.coordinator is coordinator


class TestZigbeeRoutingQualitySensorState:
    """native_value / extra_state_attributes / availability, reading from
    self.coordinator.data instead of a per-entity cached _routing_info."""

    def _sensor(self, session=None, data=None, last_update_success=True, status="AVAILABLE"):
        s = ZigbeeRoutingQualitySensor.__new__(ZigbeeRoutingQualitySensor)
        s._device = _fake_dev("hdm:ZigBee:x", name="Router Plug", status=status)
        s._session = session or MagicMock()
        s.coordinator = _fake_coordinator(data=data, last_update_success=last_update_success)
        return s

    def test_native_value_none_when_device_missing_from_coordinator_data(self):
        s = self._sensor(data={})
        assert s.native_value is None
        assert s.extra_state_attributes is None

    def test_native_value_lowercase_slug(self):
        s = self._sensor(
            data={
                "hdm:ZigBee:x": SimpleNamespace(
                    aggregated_quality=SimpleNamespace(name="GOOD"), route=[]
                )
            }
        )
        assert s.native_value == "good"
        assert s.native_value in s.options

    def test_native_value_unknown_value_error_returns_none(self):
        class _Bad:
            @property
            def name(self):
                raise ValueError("unrecognized quality")

        s = self._sensor(
            data={"hdm:ZigBee:x": SimpleNamespace(aggregated_quality=_Bad(), route=[])}
        )
        with patch("custom_components.bosch_shc.sensor.LOGGER") as mock_log:
            assert s.native_value is None
            mock_log.debug.assert_called_once()

    def test_route_attribute_resolves_object_hops(self):
        """Documented contract: hop objects exposing .device_id/.quality."""
        session = MagicMock()
        session.device.side_effect = lambda did: SimpleNamespace(name=f"Name-{did}")
        s = self._sensor(
            session=session,
            data={
                "hdm:ZigBee:x": SimpleNamespace(
                    aggregated_quality=SimpleNamespace(name="GOOD"),
                    route=[
                        SimpleNamespace(device_id="self1", quality=SimpleNamespace(name="GOOD")),
                        SimpleNamespace(device_id="hop2", quality=SimpleNamespace(name="MEDIUM")),
                    ],
                )
            },
        )
        attrs = s.extra_state_attributes
        assert attrs["route"] == [
            {"device": "Name-self1", "quality": "good"},
            {"device": "Name-hop2", "quality": "medium"},
        ]

    def test_route_attribute_resolves_tuple_hops(self):
        """Ground-truth example response shape: 2-tuples (device, quality)."""
        session = MagicMock()
        session.device.side_effect = lambda did: SimpleNamespace(name=f"Name-{did}")
        s = self._sensor(
            session=session,
            data={
                "hdm:ZigBee:x": SimpleNamespace(
                    aggregated_quality=SimpleNamespace(name="NO_CONNECTION"),
                    route=[("self1", SimpleNamespace(name="GOOD"))],
                )
            },
        )
        attrs = s.extra_state_attributes
        assert attrs["route"] == [{"device": "Name-self1", "quality": "good"}]

    def test_native_value_and_route_attributes_with_real_boschshcpy_objects(self):
        """Same as test_route_attribute_resolves_object_hops, but feeding a
        real boschshcpy.zigbee_routing.SHCZigbeeRoutingInfo (built from a raw
        dict, exactly as boschshcpy's own tests do) instead of a SimpleNamespace
        fake. Closes a coverage gap: every other test in this class only ever
        exercises _zigbee_hop_device_and_quality's getattr(...) path via fakes
        that happen to shape-match; this confirms it also works against the
        real ZigbeeRoutingHop NamedTuple / ZigbeeRoutingQuality enum shape the
        coordinator will actually hand it in production.
        """
        raw = {
            "device": "hdm:ZigBee:x",
            "aggregatedQuality": "GOOD",
            "route": [
                {"deviceId": "hdm:ZigBee:x", "quality": "GOOD"},
                {"deviceId": "hdm:ZigBee:routerplug01", "quality": "MEDIUM"},
            ],
        }
        routing_info = SHCZigbeeRoutingInfo(raw)

        session = MagicMock()
        session.device.side_effect = lambda did: SimpleNamespace(name=f"Name-{did}")
        s = self._sensor(session=session, data={"hdm:ZigBee:x": routing_info})

        assert s.native_value == "good"
        assert s.native_value in s.options

        attrs = s.extra_state_attributes
        assert attrs["route"] == [
            {"device": "Name-hdm:ZigBee:x", "quality": "good"},
            {"device": "Name-hdm:ZigBee:routerplug01", "quality": "medium"},
        ]

    def test_route_attribute_falls_back_to_raw_id_when_unresolvable(self):
        """An unknown hop device id (session.device raises) falls back to the raw id."""
        session = MagicMock()
        session.device.side_effect = KeyError("unknown")
        s = self._sensor(
            session=session,
            data={
                "hdm:ZigBee:x": SimpleNamespace(
                    aggregated_quality=SimpleNamespace(name="GOOD"),
                    route=[SimpleNamespace(device_id="ghost", quality=SimpleNamespace(name="BAD"))],
                )
            },
        )
        attrs = s.extra_state_attributes
        assert attrs["route"] == [{"device": "ghost", "quality": "bad"}]

    def test_empty_route_on_no_connection(self):
        s = self._sensor(
            data={
                "hdm:ZigBee:x": SimpleNamespace(
                    aggregated_quality=SimpleNamespace(name="NO_CONNECTION"), route=[]
                )
            }
        )
        assert s.native_value == "no_connection"
        assert s.extra_state_attributes == {"route": []}

    def test_available_true_when_device_present_and_coordinator_healthy(self):
        s = self._sensor(
            data={
                "hdm:ZigBee:x": SimpleNamespace(
                    aggregated_quality=SimpleNamespace(name="GOOD"), route=[]
                )
            }
        )
        assert s.available is True

    def test_available_false_when_device_missing_from_coordinator_data(self):
        """A per-device fetch failure on the last coordinator refresh cycle
        omits that device from `.data` — this is how a single offline mesh
        node goes unavailable without failing every other Zigbee sensor."""
        s = self._sensor(data={})
        assert s.available is False

    def test_available_false_when_coordinator_refresh_failed_entirely(self):
        s = self._sensor(
            data={
                "hdm:ZigBee:x": SimpleNamespace(
                    aggregated_quality=SimpleNamespace(name="GOOD"), route=[]
                )
            },
            last_update_success=False,
        )
        assert s.available is False

    def test_available_false_when_device_itself_unavailable(self):
        s = self._sensor(
            data={
                "hdm:ZigBee:x": SimpleNamespace(
                    aggregated_quality=SimpleNamespace(name="GOOD"), route=[]
                )
            },
            status="UNAVAILABLE",
        )
        assert s.available is False


# ---------------------------------------------------------------------------
# SHCOpenWindowsSensor -- whole-home open-doors/open-windows summary
# ---------------------------------------------------------------------------

class TestSHCOpenWindowsSensor:
    def _sensor(self, session=None, shc_device=None):
        return SHCOpenWindowsSensor(
            session=session if session is not None else MagicMock(),
            entry_id="entry1",
            shc_device=shc_device,
        )

    def test_native_value_defaults_zero(self):
        s = self._sensor()
        assert s.native_value == 0

    def test_device_info_none_without_shc_device(self):
        s = self._sensor()
        assert s.device_info is None

    def test_device_info_uses_shc_device_identifiers(self):
        shc_device = SimpleNamespace(id="shc-device-1", identifiers={("bosch_shc", "shc1")})
        s = self._sensor(shc_device=shc_device)
        assert s.device_info["identifiers"] == {("bosch_shc", "shc1")}

    def test_async_update_populates_counts_and_attributes(self):
        session = MagicMock()
        session.api.get_open_windows = AsyncMock(
            return_value={
                "openDoors": [{"name": "Front Door", "roomName": "Hall"}],
                "openWindows": [
                    {"name": "Kitchen Window"},
                    {"name": "Office Window"},
                ],
                "openOthers": [],
            }
        )
        s = self._sensor(session=session)
        asyncio.run(s.async_update())
        assert s.native_value == 3
        assert s.extra_state_attributes == {
            "open_doors": ["Front Door"],
            "open_windows": ["Kitchen Window", "Office Window"],
            "open_others": [],
        }

    def test_async_update_handles_shc_exception(self):
        from boschshcpy.exceptions import SHCException

        session = MagicMock()
        session.api.get_open_windows = AsyncMock(side_effect=SHCException("boom"))
        s = self._sensor(session=session)
        asyncio.run(s.async_update())  # must not raise
        assert s.native_value == 0
