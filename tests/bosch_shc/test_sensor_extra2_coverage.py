"""Unit tests for sensor.py — second batch of coverage gaps.

Targets lines not yet covered by test_sensor_unit.py, test_sensor_setup.py,
or test_sensor_extra_coverage.py:

Lines 45-46  : thermostats device_excluded continue
Lines 70-71  : wallthermostats/roomthermostats device_excluded continue
Lines 93-94  : twinguards device_excluded continue
Lines 192-193: smart_plugs / light_switches etc. device_excluded continue
Lines 222-223: smart_plugs_compact device_excluded continue
Lines 266-267: motion_detectors device_excluded continue
Lines 276-277: motion_detectors2 device_excluded continue
Lines 312-313: shutter_contacts2 device_excluded continue (diagnostic path)
Lines 351-352: big battery loop device_excluded continue
Line 354     : big battery loop — supports_batterylevel is False
Lines 461-462: AirQualitySensor.extra_state_attributes with non-None comfortZone
Lines 736-738: BatteryLevelSensor.native_value ValueError/AttributeError → None
Lines 778-784: TwinguardCombinedRatingSensor.native_value ValueError/AttributeError → None
Line 807     : TwinguardDescriptionSensor.native_value returns description string

Pattern: __new__ bypass + SimpleNamespace; asyncio.run for async tests.
No HA harness.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from custom_components.bosch_shc.const import DATA_SESSION, DOMAIN, OPT_EXCLUDED_DEVICES
from custom_components.bosch_shc.sensor import (
    AirQualitySensor,
    BatteryLevelSensor,
    EmmaPowerSensor,
    TwinguardCombinedRatingSensor,
    TwinguardDescriptionSensor,
    async_setup_entry,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

async def _noop_migrate(hass, platform, device, attr_name=None, old_unique_id=None):
    return None


_PATCH_MIGRATE = "custom_components.bosch_shc.sensor.async_migrate_to_new_unique_id"


def _fake_device(device_id="dev1", name="FakeDev", root_device_id="root1",
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


def _emma():
    return _fake_device(
        device_id="com.bosch.tt.emma.applink",
        name="EMMA",
        root_device_id="shc-root",
    )


def _make_fake_session(**lists):
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


def _run_setup_with_options(session, options):
    """Run async_setup_entry with custom options dict. Returns list of entities."""
    hass = SimpleNamespace(data={DOMAIN: {"E1": {DATA_SESSION: session}}})
    config_entry = SimpleNamespace(options=options, entry_id="E1")
    collected = []

    def _add_entities(entity_list):
        collected.extend(entity_list)

    async def _inner():
        with patch(_PATCH_MIGRATE, side_effect=_noop_migrate):
            await async_setup_entry(hass, config_entry, _add_entities)

    asyncio.run(_inner())
    return collected


# ---------------------------------------------------------------------------
# 1. device_excluded continue branches in async_setup_entry
#    One comprehensive test: exclude one device from each loop, verify it is
#    NOT included in the entity list.
# ---------------------------------------------------------------------------

class TestDeviceExcludedContinueBranches:
    """Each excluded device must be skipped (continue), leaving no entity for it."""

    def _excl_opts(self, *device_ids):
        return {OPT_EXCLUDED_DEVICES: list(device_ids)}

    # --- thermostats (line 45-46) ---

    def test_excluded_thermostat_not_in_entities(self):
        dev = _fake_device("trv-excl", name="TRV")
        session = _make_fake_session(thermostats=[dev])
        entities = _run_setup_with_options(session, self._excl_opts("trv-excl"))
        ids = [getattr(e, "_device", None) and e._device.id for e in entities]
        assert "trv-excl" not in ids

    def test_non_excluded_thermostat_still_in_entities(self):
        kept = _fake_device("trv-kept", name="TRV Kept")
        excl = _fake_device("trv-excl", name="TRV Excl")
        session = _make_fake_session(thermostats=[kept, excl])
        entities = _run_setup_with_options(session, self._excl_opts("trv-excl"))
        ids = [getattr(e, "_device", None) and e._device.id for e in entities]
        assert "trv-kept" in ids
        assert "trv-excl" not in ids

    # --- wallthermostats/roomthermostats (line 70-71) ---

    def test_excluded_wallthermostat_not_in_entities(self):
        dev = _fake_device("wt-excl", name="Wall Therm")
        session = _make_fake_session(wallthermostats=[dev])
        entities = _run_setup_with_options(session, self._excl_opts("wt-excl"))
        ids = [getattr(e, "_device", None) and e._device.id for e in entities]
        assert "wt-excl" not in ids

    def test_excluded_roomthermostat_not_in_entities(self):
        dev = _fake_device("rt-excl", name="Room Therm")
        session = _make_fake_session(roomthermostats=[dev])
        entities = _run_setup_with_options(session, self._excl_opts("rt-excl"))
        ids = [getattr(e, "_device", None) and e._device.id for e in entities]
        assert "rt-excl" not in ids

    # --- twinguards (line 93-94) ---

    def test_excluded_twinguard_not_in_entities(self):
        dev = _fake_device("tg-excl", name="Twinguard")
        session = _make_fake_session(twinguards=[dev])
        entities = _run_setup_with_options(session, self._excl_opts("tg-excl"))
        ids = [getattr(e, "_device", None) and e._device.id for e in entities]
        assert "tg-excl" not in ids

    # --- smart_plugs / light_switches_bsm / micromodule_* (line 192-193) ---

    def test_excluded_smart_plug_not_in_entities(self):
        dev = _fake_device("sp-excl", name="Smart Plug")
        session = _make_fake_session(smart_plugs=[dev])
        entities = _run_setup_with_options(session, self._excl_opts("sp-excl"))
        ids = [getattr(e, "_device", None) and e._device.id for e in entities]
        assert "sp-excl" not in ids

    def test_excluded_light_switch_bsm_not_in_entities(self):
        dev = _fake_device("lsb-excl", name="Light Switch")
        session = _make_fake_session(light_switches_bsm=[dev])
        entities = _run_setup_with_options(session, self._excl_opts("lsb-excl"))
        ids = [getattr(e, "_device", None) and e._device.id for e in entities]
        assert "lsb-excl" not in ids

    def test_excluded_micromodule_shutter_not_in_entities(self):
        dev = _fake_device("msc-excl", name="MicromoduleShutter")
        session = _make_fake_session(micromodule_shutter_controls=[dev])
        entities = _run_setup_with_options(session, self._excl_opts("msc-excl"))
        ids = [getattr(e, "_device", None) and e._device.id for e in entities]
        assert "msc-excl" not in ids

    # --- smart_plugs_compact (line 222-223) ---

    def test_excluded_smart_plug_compact_not_in_entities(self):
        dev = _fake_device("spc-excl", name="SPC")
        session = _make_fake_session(smart_plugs_compact=[dev])
        entities = _run_setup_with_options(session, self._excl_opts("spc-excl"))
        ids = [getattr(e, "_device", None) and e._device.id for e in entities]
        assert "spc-excl" not in ids

    # --- motion_detectors (line 266-267) ---

    def test_excluded_motion_detector_not_in_entities(self):
        dev = _fake_device("md-excl", name="Motion")
        session = _make_fake_session(motion_detectors=[dev])
        entities = _run_setup_with_options(session, self._excl_opts("md-excl"))
        ids = [getattr(e, "_device", None) and e._device.id for e in entities]
        assert "md-excl" not in ids

    # --- motion_detectors2 (line 276-277) ---

    def test_excluded_motion_detector2_not_in_entities(self):
        dev = _fake_device("md2-excl", name="Motion2")
        session = _make_fake_session(motion_detectors2=[dev])
        entities = _run_setup_with_options(session, self._excl_opts("md2-excl"))
        ids = [getattr(e, "_device", None) and e._device.id for e in entities]
        assert "md2-excl" not in ids

    # --- shutter_contacts2 battery/diag path (line 312-313) ---

    def test_excluded_shutter_contact2_not_in_battery_entities(self):
        # diagnostic_enabled=True by default; shutter_contacts2 enter the diagnostic
        # battery loop (line 338-359). Excluded device must be skipped there too.
        dev = _fake_device("sc2-excl", name="SC2", supports_batterylevel=True)
        dev.communicationquality = SimpleNamespace(name="GOOD")
        session = _make_fake_session(
            shutter_contacts2=[dev],
        )
        entities = _run_setup_with_options(session, self._excl_opts("sc2-excl"))
        ids = [getattr(e, "_device", None) and e._device.id for e in entities]
        assert "sc2-excl" not in ids

    # --- big battery loop device_excluded (line 351-352) ---

    def test_excluded_device_skipped_in_battery_loop(self):
        """Device in motion_detectors that is excluded → no BatteryLevelSensor."""
        dev = _fake_device(
            "md-bat-excl", name="Motion Bat", supports_batterylevel=True
        )
        session = _make_fake_session(motion_detectors=[dev])
        entities = _run_setup_with_options(session, self._excl_opts("md-bat-excl"))
        from custom_components.bosch_shc.sensor import BatteryLevelSensor
        bat_entities = [e for e in entities if isinstance(e, BatteryLevelSensor)]
        assert bat_entities == []

    # --- supports_batterylevel False (line 354) ---

    def test_device_without_battery_support_skips_battery_entity(self):
        """supports_batterylevel=False → BatteryLevelSensor not added (line 354)."""
        dev = _fake_device(
            "md-no-bat", name="Motion NoBat", supports_batterylevel=False
        )
        session = _make_fake_session(motion_detectors=[dev])
        entities = _run_setup_with_options(session, {})
        from custom_components.bosch_shc.sensor import BatteryLevelSensor
        bat_entities = [e for e in entities if isinstance(e, BatteryLevelSensor)]
        assert bat_entities == []

    # --- All-excluded sanity test ---

    def test_all_device_types_excluded_yields_only_emma(self):
        """Excluding every device in every list leaves only the EMMA sensor."""
        excl_ids = [f"dev-{i}" for i in range(9)]
        devs = [_fake_device(did, name=did) for did in excl_ids]
        session = _make_fake_session(
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
        assert len(entities) == 1
        assert isinstance(entities[0], EmmaPowerSensor)


# ---------------------------------------------------------------------------
# 2. AirQualitySensor.extra_state_attributes — comfortZone is not None
#    (lines 461-462)
# ---------------------------------------------------------------------------

class _FakeAirQualityService:
    """Fake _airqualitylevel_service that returns a concrete comfortZone."""
    def __init__(self, comfort_zone_value):
        self.comfortZone = comfort_zone_value


class TestAirQualitySensorComfortZone:
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
        """comfortZone value (any type) is preserved as-is."""
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


# ---------------------------------------------------------------------------
# 3. BatteryLevelSensor.native_value — ValueError and AttributeError paths
#    (lines 736-738)
# ---------------------------------------------------------------------------

class _RaisingValue:
    """Simulates a device.batterylevel that raises on .value access."""
    def __init__(self, exc_cls=ValueError):
        self._exc_cls = exc_cls

    @property
    def value(self):
        raise self._exc_cls("bad battery level")


class TestBatteryLevelSensorErrorPaths:
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
        """When batterylevel.value is a valid string, it is returned."""
        class _GoodLevel:
            value = "OK"

        s = self._sensor(_GoodLevel())
        assert s.native_value == "OK"

    def test_happy_path_low_battery(self):
        class _LowLevel:
            value = "LOW_BATTERY"

        s = self._sensor(_LowLevel())
        assert s.native_value == "LOW_BATTERY"


# ---------------------------------------------------------------------------
# 4. TwinguardCombinedRatingSensor.native_value — ValueError/AttributeError
#    (lines 778-784)
# ---------------------------------------------------------------------------

class _RaisingCombinedRating:
    """Simulates a combined_rating that raises .name."""
    def __init__(self, exc_cls=ValueError):
        self._exc_cls = exc_cls

    @property
    def name(self):
        raise self._exc_cls("unknown combined rating")


class TestTwinguardCombinedRatingSensorErrorPaths:
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
        assert s.native_value == "GOOD"

    def test_happy_path_bad(self):
        class _Bad:
            name = "BAD"

        s = self._sensor(_Bad())
        assert s.native_value == "BAD"


# ---------------------------------------------------------------------------
# 5. TwinguardDescriptionSensor.native_value returns description (line 807)
# ---------------------------------------------------------------------------

class TestTwinguardDescriptionSensor:
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
