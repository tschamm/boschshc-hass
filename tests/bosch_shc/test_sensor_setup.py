"""Tests for sensor.py async_setup_entry and real __init__ chains.

Covers: async_setup_entry entity creation, TemperatureSensor/__init__,
HumiditySensor/__init__, PuritySensor/__init__, AirQualitySensor/__init__,
TemperatureRatingSensor/__init__, HumidityRatingSensor/__init__,
PurityRatingSensor/__init__, PowerSensor/__init__, EnergySensor/__init__,
CommunicationQualitySensor/__init__, ValveTappetSensor/__init__,
IlluminanceLevelSensor/__init__, EmmaPowerSensor/__init__ + async lifecycle.

No HA harness, no tests.common, no network.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from custom_components.bosch_shc.const import DATA_SESSION, DOMAIN
from custom_components.bosch_shc.sensor import (
    AirQualitySensor,
    CommunicationQualitySensor,
    EmmaPowerSensor,
    EnergySensor,
    HumidityRatingSensor,
    HumiditySensor,
    IlluminanceLevelSensor,
    PowerSensor,
    PurityRatingSensor,
    PuritySensor,
    TemperatureRatingSensor,
    TemperatureSensor,
    TwinguardCombinedRatingSensor,
    TwinguardDescriptionSensor,
    ValveTappetSensor,
    async_setup_entry,
)


# ---------------------------------------------------------------------------
# Helpers — build a fake SHCDevice-like object that satisfies SHCEntity.__init__
# ---------------------------------------------------------------------------


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
        device_services=[],  # SHCEntity.__init__ calls _update_attr only; no iteration here
        supports_batterylevel=supports_batterylevel,
        **extra,
    )


ENTRY_ID = "entry-001"


# ---------------------------------------------------------------------------
# async_migrate_to_new_unique_id — stubbed so setup tests don't need HA registry
# ---------------------------------------------------------------------------

async def _noop_migrate(hass, platform, device, attr_name=None, old_unique_id=None):
    return None


# ---------------------------------------------------------------------------
# async_setup_entry — full entity-count + type tests
# ---------------------------------------------------------------------------

_PATCH = "custom_components.bosch_shc.sensor.async_migrate_to_new_unique_id"


def _make_fake_session(
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


def _run_setup(session):
    """Run async_setup_entry with a fake session. Returns list of added entities."""
    hass = SimpleNamespace(data={DOMAIN: {ENTRY_ID: {DATA_SESSION: session}}})
    config_entry = SimpleNamespace(options={}, entry_id=ENTRY_ID)
    collected: list = []

    def _add_entities(entity_list):
        # async_add_entities is called once with the full list
        collected.extend(entity_list)

    async def _inner():
        with patch(_PATCH, side_effect=_noop_migrate):
            await async_setup_entry(hass, config_entry, _add_entities)

    asyncio.run(_inner())
    return collected


class TestAsyncSetupEntryEntityCounts:
    """Verify the right entity types + counts are produced for each device group."""

    def test_empty_session_yields_only_emma(self):
        session = _make_fake_session()
        entities = _run_setup(session)
        # Only the EmmaPowerSensor is always added
        assert len(entities) == 1
        assert isinstance(entities[0], EmmaPowerSensor)

    def test_one_thermostat_yields_temperature_and_valve_tappet(self):
        dev = _fake_device(name="TRV1", device_id="hdm:TRV:001")
        session = _make_fake_session(thermostats=[dev])
        entities = _run_setup(session)
        # 2 from thermostat + 1 EMMA
        assert len(entities) == 3
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
        assert len(entities) == 5  # 2×(Temp+Valve) + EMMA
        assert sum(isinstance(e, TemperatureSensor) for e in entities) == 2
        assert sum(isinstance(e, ValveTappetSensor) for e in entities) == 2

    def test_one_wallthermostat_yields_temperature_and_humidity(self):
        dev = _fake_device(name="WT1", device_id="hdm:WT:001")
        session = _make_fake_session(wallthermostats=[dev])
        entities = _run_setup(session)
        # 2 + EMMA
        assert len(entities) == 3
        types = [type(e) for e in entities]
        assert TemperatureSensor in types
        assert HumiditySensor in types

    def test_one_roomthermostat_yields_temperature_and_humidity(self):
        dev = _fake_device(name="RT1", device_id="hdm:RT:001")
        session = _make_fake_session(roomthermostats=[dev])
        entities = _run_setup(session)
        assert len(entities) == 3
        types = [type(e) for e in entities]
        assert TemperatureSensor in types
        assert HumiditySensor in types

    def test_wallthermostat_and_roomthermostat_combine(self):
        wt = _fake_device(name="WT1", device_id="hdm:WT:001")
        rt = _fake_device(name="RT1", device_id="hdm:RT:001")
        session = _make_fake_session(wallthermostats=[wt], roomthermostats=[rt])
        entities = _run_setup(session)
        # 4 (2 devices × 2 sensors each) + EMMA
        assert len(entities) == 5
        assert sum(isinstance(e, TemperatureSensor) for e in entities) == 2
        assert sum(isinstance(e, HumiditySensor) for e in entities) == 2

    def test_one_twinguard_yields_nine_sensors(self):
        dev = _fake_device(name="TG1", device_id="hdm:TG:001")
        session = _make_fake_session(twinguards=[dev])
        entities = _run_setup(session)
        # Temp, Humidity, Purity, AirQuality, TempRating, HumidityRating, PurityRating
        # + CombinedRating (diag), Description (diag) + EMMA
        assert len(entities) == 10
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
        assert len(entities) == 3
        types = [type(e) for e in entities]
        assert PowerSensor in types
        assert EnergySensor in types

    def test_light_switch_bsm_yields_power_and_energy(self):
        dev = _fake_device(name="LSB1", device_id="hdm:LSB:001")
        session = _make_fake_session(light_switches_bsm=[dev])
        entities = _run_setup(session)
        assert len(entities) == 3
        types = [type(e) for e in entities]
        assert PowerSensor in types
        assert EnergySensor in types

    def test_micromodule_light_control_yields_power_and_energy(self):
        dev = _fake_device(name="MLC1", device_id="hdm:MLC:001")
        session = _make_fake_session(micromodule_light_controls=[dev])
        entities = _run_setup(session)
        assert len(entities) == 3
        assert sum(isinstance(e, PowerSensor) for e in entities) == 1
        assert sum(isinstance(e, EnergySensor) for e in entities) == 1

    def test_micromodule_shutter_control_yields_power_and_energy(self):
        dev = _fake_device(name="MSC1", device_id="hdm:MSC:001")
        session = _make_fake_session(micromodule_shutter_controls=[dev])
        entities = _run_setup(session)
        assert len(entities) == 3
        assert sum(isinstance(e, PowerSensor) for e in entities) == 1
        assert sum(isinstance(e, EnergySensor) for e in entities) == 1

    def test_micromodule_blinds_yields_power_and_energy(self):
        dev = _fake_device(name="MB1", device_id="hdm:MB:001")
        session = _make_fake_session(micromodule_blinds=[dev])
        entities = _run_setup(session)
        assert len(entities) == 3
        assert sum(isinstance(e, PowerSensor) for e in entities) == 1
        assert sum(isinstance(e, EnergySensor) for e in entities) == 1

    def test_smart_plug_compact_yields_power_energy_and_comm_quality(self):
        dev = _fake_device(name="SPC1", device_id="hdm:SPC:001")
        session = _make_fake_session(smart_plugs_compact=[dev])
        entities = _run_setup(session)
        # 3 + EMMA
        assert len(entities) == 4
        types = [type(e) for e in entities]
        assert PowerSensor in types
        assert EnergySensor in types
        assert CommunicationQualitySensor in types

    def test_motion_detector_yields_illuminance(self):
        dev = _fake_device(name="MD1", device_id="hdm:MD:001")
        session = _make_fake_session(motion_detectors=[dev])
        entities = _run_setup(session)
        # 1 + EMMA
        assert len(entities) == 2
        assert isinstance(entities[0], IlluminanceLevelSensor)

    def test_motion_detector2_yields_illuminance_and_temperature(self):
        """Gen2 MD2 produces IlluminanceLevelSensor + TemperatureSensor + CommunicationQualitySensor."""
        dev = _fake_device(name="MD2", device_id="hdm:MD2:001")
        session = _make_fake_session(motion_detectors2=[dev])
        entities = _run_setup(session)
        # IlluminanceLevelSensor + TemperatureSensor + CommunicationQualitySensor (diag) + EMMA
        assert len(entities) == 4
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
        assert len(entities) == 5
        assert sum(isinstance(e, IlluminanceLevelSensor) for e in entities) == 2

    def test_no_entities_without_devices_adds_nothing_except_emma(self):
        """async_add_entities is called once (EMMA always present); result has 1 entity."""
        session = _make_fake_session()
        entities = _run_setup(session)
        assert len(entities) == 1  # EMMA always present

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


# ---------------------------------------------------------------------------
# Real __init__ — unique_id and name set correctly
# ---------------------------------------------------------------------------


class TestRealInitUniqueIdAndName:
    """Verify that real __init__ (not __new__ bypass) sets unique_id and name."""

    def _dev(self, name="MyDev", did="hdm:Dev:001", rid="root-abc", serial="SER1"):
        return _fake_device(name=name, device_id=did, root_device_id=rid, serial=serial)

    def test_temperature_sensor_unique_id(self):
        d = self._dev()
        s = TemperatureSensor(device=d, entry_id=ENTRY_ID)
        assert s.unique_id == f"{d.root_device_id}_{d.id}_temperature"

    def test_temperature_sensor_name(self):
        # has_entity_name=True: .name returns just the attr part; device prefix is
        # applied by the entity registry at display time — not via .name property.
        d = self._dev(name="Living Room TRV")
        s = TemperatureSensor(device=d, entry_id=ENTRY_ID)
        assert s.name == "Temperature"

    def test_humidity_sensor_unique_id(self):
        d = self._dev()
        s = HumiditySensor(device=d, entry_id=ENTRY_ID)
        assert s.unique_id == f"{d.root_device_id}_{d.id}_humidity"

    def test_humidity_sensor_name(self):
        d = self._dev(name="Bedroom WT")
        s = HumiditySensor(device=d, entry_id=ENTRY_ID)
        assert s.name == "Humidity"

    def test_purity_sensor_unique_id(self):
        d = self._dev()
        s = PuritySensor(device=d, entry_id=ENTRY_ID)
        assert s.unique_id == f"{d.root_device_id}_{d.id}_purity"

    def test_purity_sensor_name(self):
        d = self._dev(name="TG Office")
        s = PuritySensor(device=d, entry_id=ENTRY_ID)
        assert s.name == "Purity"

    def test_air_quality_sensor_unique_id(self):
        d = self._dev()
        s = AirQualitySensor(device=d, entry_id=ENTRY_ID)
        assert s.unique_id == f"{d.root_device_id}_{d.id}_airquality"

    def test_air_quality_sensor_name(self):
        d = self._dev(name="Hall TG")
        s = AirQualitySensor(device=d, entry_id=ENTRY_ID)
        assert s.name == "Air Quality"

    def test_temperature_rating_sensor_unique_id(self):
        d = self._dev()
        s = TemperatureRatingSensor(device=d, entry_id=ENTRY_ID)
        assert s.unique_id == f"{d.root_device_id}_{d.id}_temperaturerating"

    def test_temperature_rating_sensor_name(self):
        d = self._dev(name="Kitchen TG")
        s = TemperatureRatingSensor(device=d, entry_id=ENTRY_ID)
        assert s.name == "Temperature Rating"

    def test_humidity_rating_sensor_unique_id(self):
        d = self._dev()
        s = HumidityRatingSensor(device=d, entry_id=ENTRY_ID)
        assert s.unique_id == f"{d.root_device_id}_{d.id}_humidityrating"

    def test_humidity_rating_sensor_name(self):
        d = self._dev(name="Bedroom TG")
        s = HumidityRatingSensor(device=d, entry_id=ENTRY_ID)
        assert s.name == "Humidity Rating"

    def test_purity_rating_sensor_unique_id(self):
        d = self._dev()
        s = PurityRatingSensor(device=d, entry_id=ENTRY_ID)
        assert s.unique_id == f"{d.root_device_id}_{d.id}_purityrating"

    def test_purity_rating_sensor_name(self):
        d = self._dev(name="Cellar TG")
        s = PurityRatingSensor(device=d, entry_id=ENTRY_ID)
        assert s.name == "Purity Rating"

    def test_power_sensor_unique_id(self):
        d = self._dev()
        s = PowerSensor(device=d, entry_id=ENTRY_ID)
        assert s.unique_id == f"{d.root_device_id}_{d.id}_power"

    def test_power_sensor_name(self):
        d = self._dev(name="Smart Plug A")
        s = PowerSensor(device=d, entry_id=ENTRY_ID)
        assert s.name == "Power"

    def test_energy_sensor_unique_id(self):
        d = self._dev()
        s = EnergySensor(device=d, entry_id=ENTRY_ID)
        assert s.unique_id == f"{d.root_device_id}_{d.id}_energy"

    def test_energy_sensor_name(self):
        d = self._dev(name="Smart Plug B")
        s = EnergySensor(device=d, entry_id=ENTRY_ID)
        assert s.name == "Energy"

    def test_communication_quality_sensor_unique_id(self):
        d = self._dev()
        s = CommunicationQualitySensor(device=d, entry_id=ENTRY_ID)
        assert s.unique_id == f"{d.root_device_id}_{d.id}_communicationquality"

    def test_communication_quality_sensor_name(self):
        # #339: name now comes from translation_key (not a hard-coded _attr_name)
        # so the displayed label localizes; _attr_name is therefore None.
        d = self._dev(name="SPC 1")
        s = CommunicationQualitySensor(device=d, entry_id=ENTRY_ID)
        assert s._attr_name is None
        assert s.translation_key == "communication_quality"

    def test_valve_tappet_sensor_unique_id(self):
        d = self._dev()
        s = ValveTappetSensor(device=d, entry_id=ENTRY_ID)
        assert s.unique_id == f"{d.root_device_id}_{d.id}_valvetappet"

    def test_valve_tappet_sensor_name(self):
        d = self._dev(name="Bedroom TRV")
        s = ValveTappetSensor(device=d, entry_id=ENTRY_ID)
        assert s.name == "Valve Tappet"

    def test_illuminance_sensor_unique_id(self):
        d = self._dev()
        s = IlluminanceLevelSensor(device=d, entry_id=ENTRY_ID)
        assert s.unique_id == f"{d.root_device_id}_{d.id}_illuminance"

    def test_illuminance_sensor_name(self):
        d = self._dev(name="Motion Hall")
        s = IlluminanceLevelSensor(device=d, entry_id=ENTRY_ID)
        assert s.name == "Illuminance"

    def test_emma_power_sensor_unique_id(self):
        d = _fake_device(
            name="EMMA", device_id="com.bosch.tt.emma.applink", root_device_id="shc-mac"
        )
        s = EmmaPowerSensor(device=d, entry_id=ENTRY_ID)
        assert s.unique_id == "shc-mac_com.bosch.tt.emma.applink_power"

    def test_emma_power_sensor_name(self):
        d = _fake_device(
            name="EMMA", device_id="com.bosch.tt.emma.applink", root_device_id="shc-mac"
        )
        s = EmmaPowerSensor(device=d, entry_id=ENTRY_ID)
        assert s.name == "Power"

    def test_entry_id_stored_on_base_entity(self):
        d = self._dev()
        s = TemperatureSensor(device=d, entry_id=ENTRY_ID)
        assert s._entry_id == ENTRY_ID

    def test_device_stored_on_base_entity(self):
        d = self._dev(name="SomeDevice")
        s = HumiditySensor(device=d, entry_id=ENTRY_ID)
        assert s._device is d


# ---------------------------------------------------------------------------
# EmmaPowerSensor async lifecycle
# ---------------------------------------------------------------------------


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
