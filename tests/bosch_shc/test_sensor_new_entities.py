"""Tests for new sensor entities: CommunicationQuality for shutter_contacts2,
and AirQualitySensor comfort_zone extra attribute.

Uses __new__ bypass + SimpleNamespace device or fake session with setup.
No HA harness.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from custom_components.bosch_shc.sensor import (
    AirQualitySensor,
    CommunicationQualitySensor,
    async_setup_entry,
)
from custom_components.bosch_shc.const import DATA_SESSION, DOMAIN


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ENTRY_ID = "E1"


async def _noop_migrate(hass, platform, device, attr_name=None, old_unique_id=None):
    return None

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


def _run_setup(session, options=None):
    hass = SimpleNamespace(
        data={DOMAIN: {ENTRY_ID: {DATA_SESSION: session}}}
    )
    config_entry = SimpleNamespace(
        options=options or {"opt_diagnostic_entities": True},
        entry_id=ENTRY_ID,
    )
    collected = []

    def _add_entities(entity_list):
        collected.extend(entity_list)

    async def _inner():
        with patch(_PATCH, side_effect=_noop_migrate):
            await async_setup_entry(hass, config_entry, _add_entities)

    asyncio.run(_inner())
    return collected


def _fake_device(name="Test", device_id="dev1", root_id="root1", **extra):
    return SimpleNamespace(
        name=name,
        id=device_id,
        root_device_id=root_id,
        serial=device_id,
        device_services=[],
        supports_batterylevel=False,
        **extra,
    )


# ---------------------------------------------------------------------------
# CommunicationQuality for shutter_contacts2
# ---------------------------------------------------------------------------

class TestShutterContact2CommQualitySetup:
    """CommunicationQualitySensor added for shutter_contacts2 with the service.

    NOTE: The sensor.py hasattr guard checks for 'communicationquality' on
    the device object. SimpleNamespace devices with the attribute pass; those
    without do not.
    """

    def test_shutter_contact2_with_cq_yields_comm_quality_sensor(self):
        """A shutter_contacts2 device with communicationquality → CommunicationQualitySensor."""
        dev = _fake_device(
            device_id="hdm:SC2:001",
            communicationquality=SimpleNamespace(name="GOOD"),
        )
        session = _make_fake_session(shutter_contacts2=[dev])
        entities = _run_setup(session)
        comm_sensors = [e for e in entities if isinstance(e, CommunicationQualitySensor)]
        sc2_comm = [e for e in comm_sensors if "hdm:SC2:001" in e._attr_unique_id]
        assert len(sc2_comm) == 1

    def test_shutter_contact2_without_cq_attr_is_skipped(self):
        """A shutter_contacts2 device without communicationquality is skipped."""
        dev = _fake_device(device_id="hdm:SC2:002")
        # no communicationquality attribute → hasattr returns False
        session = _make_fake_session(shutter_contacts2=[dev])
        entities = _run_setup(session)
        comm_sensors = [e for e in entities if isinstance(e, CommunicationQualitySensor)]
        sc2_comm = [e for e in comm_sensors if "hdm:SC2:002" in e._attr_unique_id]
        assert len(sc2_comm) == 0

    def test_comm_quality_diagnostic_disabled_skips_shutter_contact(self):
        """diagnostic_entities=False → no CommunicationQualitySensor for shutter_contacts2."""
        from custom_components.bosch_shc.const import OPT_DIAGNOSTIC_ENTITIES
        dev = _fake_device(
            device_id="hdm:SC2:003",
            communicationquality=SimpleNamespace(name="GOOD"),
        )
        session = _make_fake_session(shutter_contacts2=[dev])
        # Passing empty options dict means OPT_DIAGNOSTIC_ENTITIES defaults to True
        # in sensor.py (via config_entry.options.get(OPT_DIAGNOSTIC_ENTITIES, True))
        # so we must explicitly set it False:
        entities = _run_setup(session, options={OPT_DIAGNOSTIC_ENTITIES: False})
        comm_sensors = [e for e in entities if isinstance(e, CommunicationQualitySensor)]
        sc2_comm = [e for e in comm_sensors if "hdm:SC2:003" in e._attr_unique_id]
        assert len(sc2_comm) == 0

    def test_unique_id_format_for_shutter_contact2(self):
        """unique_id must end with _communicationquality."""
        dev = _fake_device(
            name="SC2 test",
            device_id="hdm:SC2:uid-test",
            root_id="root-sc2-uid",
            communicationquality=SimpleNamespace(name="GOOD"),
        )
        session = _make_fake_session(shutter_contacts2=[dev])
        entities = _run_setup(session)
        comm_sensors = [e for e in entities if isinstance(e, CommunicationQualitySensor)]
        sc2_comm = [e for e in comm_sensors if "hdm:SC2:uid-test" in e._attr_unique_id]
        assert len(sc2_comm) == 1
        assert sc2_comm[0]._attr_unique_id.endswith("_communicationquality")


# ---------------------------------------------------------------------------
# AirQualitySensor — comfort_zone extra attribute
# ---------------------------------------------------------------------------

class TestAirQualitySensorComfortZone:
    """AirQualitySensor.extra_state_attributes exposes comfort_zone when present."""

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
