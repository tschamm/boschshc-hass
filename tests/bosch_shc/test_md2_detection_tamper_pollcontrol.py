"""Tests for MD2 DetectionTest / Tamper / PollControl / InstallationProfile
entities added for tschamm/boschshc-hass#325.

Run with:
  PYTHONPATH="<lib>:<hass>" PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
  python3 -m pytest tests/bosch_shc/test_md2_detection_tamper_pollcontrol.py -q -o addopts=
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.bosch_shc.button import (
    SHCDetectionTestButton,
    SHCDetectionTestStopButton,
    SHCTamperResetButton,
)
from custom_components.bosch_shc.button import (
    async_setup_entry as button_setup_entry,
)
from custom_components.bosch_shc.select import (
    InstallationProfileSelect,
    OrientationLightResponseSelect,
)
from custom_components.bosch_shc.select import (
    async_setup_entry as select_setup_entry,
)
from custom_components.bosch_shc.sensor import (
    DetectionStateSensor,
)
from custom_components.bosch_shc.switch import SWITCH_TYPES, SHCSwitch


def _fake_md2(**kwargs):
    defaults = dict(
        name="MD2",
        id="md1",
        root_device_id="root1",
        serial="SER1",
        supports_batterylevel=False,
        supports_silentmode=False,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_button_session(**helper_lists):
    defaults = dict(
        smoke_detectors=[], twinguards=[], motion_detectors2=[], userdefinedstates=[]
    )
    defaults.update(helper_lists)
    return SimpleNamespace(
        device_helper=SimpleNamespace(**defaults),
        userdefinedstates=[],
        scenarios=[],
        subscribe=lambda *a, **kw: None,
    )


def _make_select_session(**helper_lists):
    defaults = dict(
        motion_detectors2=[],
        shutter_contacts2=[],
        smart_plugs=[],
        smart_plugs_compact=[],
        smoke_detectors=[],
        twinguards=[],
        thermostats=[],
        roomthermostats=[],
        micromodule_relays=[],
        micromodule_light_controls=[],
    )
    defaults.update(helper_lists)
    return SimpleNamespace(device_helper=SimpleNamespace(**defaults))


def _button_hass_entry(session):
    entry_id = "E1"
    hass = SimpleNamespace()
    shc_device = SimpleNamespace(
        name="SHC",
        id="shc",
        identifiers={("bosch_shc", "shc")},
        manufacturer="Bosch",
        model="SHC",
    )
    entry = SimpleNamespace(
        options={}, entry_id=entry_id, unique_id="UID1", async_on_unload=MagicMock()
    )
    entry.runtime_data = SimpleNamespace(
        session=session, shc_device=shc_device, title="Test SHC"
    )
    return hass, entry


def _setup_buttons(session):
    hass, entry = _button_hass_entry(session)
    entities = []

    async def _run():
        await button_setup_entry(hass, entry, lambda e, *a, **k: entities.extend(e))

    asyncio.run(_run())
    return entities


def _setup_selects(session):
    entry_id = "E1"
    hass = SimpleNamespace()
    entry = SimpleNamespace(
        options={}, entry_id=entry_id, unique_id="UID1", async_on_unload=MagicMock()
    )
    entry.runtime_data = SimpleNamespace(
        session=session, shc_device=None, title="Test SHC"
    )
    entities = []

    async def _run():
        with patch(
            "custom_components.bosch_shc.select.SHCShutterContact2Plus",
            new=type("SHCShutterContact2Plus", (), {}),
        ):
            await select_setup_entry(hass, entry, lambda e, *a, **k: entities.extend(e))

    asyncio.run(_run())
    return entities


def _setup_sensors(md2_list):
    from custom_components.bosch_shc.sensor import async_setup_entry as sensor_setup

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

    async def _run():
        with patch(
            "custom_components.bosch_shc.sensor.async_migrate_to_new_unique_id",
            new=AsyncMock(return_value=None),
        ):
            await sensor_setup(hass, entry, lambda e, *a, **k: entities.extend(e))

    asyncio.run(_run())
    return entities


# ---------------------------------------------------------------------------
# Button setup
# ---------------------------------------------------------------------------


class TestButtonSetup:
    def test_detection_buttons_created_when_supported(self):
        md2 = _fake_md2(supports_detection_test=True)
        entities = _setup_buttons(_make_button_session(motion_detectors2=[md2]))
        types = [type(e).__name__ for e in entities]
        assert "SHCDetectionTestButton" in types
        assert "SHCDetectionTestStopButton" in types

    def test_detection_buttons_skipped_when_unsupported(self):
        md2 = _fake_md2(supports_detection_test=False)
        entities = _setup_buttons(_make_button_session(motion_detectors2=[md2]))
        types = [type(e).__name__ for e in entities]
        assert "SHCDetectionTestButton" not in types

    def test_tamper_reset_created_when_service_supported(self):
        md2 = _fake_md2(
            reset_tampered_state=lambda: None, supports_tamper_reset=True
        )
        entities = _setup_buttons(_make_button_session(motion_detectors2=[md2]))
        types = [type(e).__name__ for e in entities]
        assert "SHCTamperResetButton" in types

    def test_tamper_reset_skipped_when_service_unsupported(self):
        """reset_tampered_state()/async_reset_tampered_state() are defined
        unconditionally on SHCMotionDetector2, so gating must use the real
        supports_tamper_reset presence check, not hasattr on the method."""
        md2 = _fake_md2(supports_tamper_reset=False)
        entities = _setup_buttons(_make_button_session(motion_detectors2=[md2]))
        types = [type(e).__name__ for e in entities]
        assert "SHCTamperResetButton" not in types


# ---------------------------------------------------------------------------
# Button unit behaviour
# ---------------------------------------------------------------------------


class TestDetectionTestButtons:
    def test_start_press(self):
        from boschshcpy.services_impl import DetectionTestService

        dev = _fake_md2(async_set_detection_state_request=AsyncMock())
        b = SHCDetectionTestButton.__new__(SHCDetectionTestButton)
        b._device = dev
        asyncio.run(b.async_press())
        dev.async_set_detection_state_request.assert_called_once_with(
            DetectionTestService.DetectionStateRequest.DETECTION_STATE_START
        )

    def test_stop_press(self):
        from boschshcpy.services_impl import DetectionTestService

        dev = _fake_md2(async_set_detection_state_request=AsyncMock())
        b = SHCDetectionTestStopButton.__new__(SHCDetectionTestStopButton)
        b._device = dev
        asyncio.run(b.async_press())
        dev.async_set_detection_state_request.assert_called_once_with(
            DetectionTestService.DetectionStateRequest.DETECTION_STATE_STOP
        )

    def test_unique_ids(self):
        dev = _fake_md2()
        start = SHCDetectionTestButton.__new__(SHCDetectionTestButton)
        start._attr_unique_id = f"{dev.root_device_id}_{dev.id}_detection_test"
        stop = SHCDetectionTestStopButton.__new__(SHCDetectionTestStopButton)
        stop._attr_unique_id = f"{dev.root_device_id}_{dev.id}_detection_test_stop"
        assert start._attr_unique_id == "root1_md1_detection_test"
        assert stop._attr_unique_id == "root1_md1_detection_test_stop"


class TestTamperResetButton:
    def test_press_calls_async_reset(self):
        dev = _fake_md2(async_reset_tampered_state=AsyncMock())
        b = SHCTamperResetButton.__new__(SHCTamperResetButton)
        b._device = dev
        asyncio.run(b.async_press())
        dev.async_reset_tampered_state.assert_called_once_with()

    def test_translation_key(self):
        # Icon now comes from icons.json keyed by translation_key, not a
        # hardcoded _attr_icon (icon-translations quality-scale rule).
        b = SHCTamperResetButton.__new__(SHCTamperResetButton)
        assert b._attr_translation_key == "reset_tamper"


# ---------------------------------------------------------------------------
# DetectionStateSensor
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# InstallationProfileSelect (#353 — writable, replaces the read-only sensor)
# ---------------------------------------------------------------------------


class TestInstallationProfileSelect:
    def test_current_option(self):
        dev = _fake_md2(profile="GENERIC", supported_profiles=["OUTDOOR", "GENERIC"])
        e = InstallationProfileSelect.__new__(InstallationProfileSelect)
        e._device = dev
        e._attr_options = ["outdoor", "generic"]
        assert e.current_option == "generic"

    def test_current_option_out_of_options_returns_none(self):
        # Profile not advertised in supported_profiles must not be a valid option.
        dev = _fake_md2(profile="SURPRISE", supported_profiles=["OUTDOOR", "GENERIC"])
        e = InstallationProfileSelect.__new__(InstallationProfileSelect)
        e._device = dev
        e._attr_options = ["outdoor", "generic"]
        assert e.current_option is None

    def test_async_select_option_uppercases(self):
        dev = _fake_md2(
            profile="GENERIC",
            supported_profiles=["OUTDOOR", "GENERIC"],
            async_set_profile=AsyncMock(),
        )
        e = InstallationProfileSelect.__new__(InstallationProfileSelect)
        e._device = dev
        e._attr_options = ["outdoor", "generic"]
        e._entry_id = "entry-1"
        e.hass = MagicMock()
        asyncio.run(e.async_select_option("outdoor"))
        dev.async_set_profile.assert_called_once_with("OUTDOOR")

    def test_async_select_option_reloads_entry(self):
        """#356: a profile switch must reload the config entry so capability
        -gated entities (e.g. the MD2 [+M] indicator light) are added/removed
        immediately, instead of only after a manual reload/restart."""
        dev = _fake_md2(
            profile="GENERIC",
            supported_profiles=["OUTDOOR", "GENERIC"],
            async_set_profile=AsyncMock(),
        )
        e = InstallationProfileSelect.__new__(InstallationProfileSelect)
        e._device = dev
        e._attr_options = ["outdoor", "generic"]
        e._entry_id = "entry-1"
        e.hass = MagicMock()
        asyncio.run(e.async_select_option("outdoor"))
        e.hass.async_create_task.assert_called_once()
        e.hass.config_entries.async_reload.assert_called_once_with("entry-1")

    def test_options_lowercased_from_supported_profiles(self):
        md2 = _fake_md2(profile="GENERIC", supported_profiles=["OUTDOOR", "GENERIC"])
        e = InstallationProfileSelect(device=md2, entry_id="entry-1")
        assert e._attr_options == ["outdoor", "generic"]

    def test_setup_created_when_profiles_present(self):
        md2 = _fake_md2(profile="GENERIC", supported_profiles=["OUTDOOR", "GENERIC"])
        types = [
            type(e).__name__
            for e in _setup_selects(_make_select_session(motion_detectors2=[md2]))
        ]
        assert "InstallationProfileSelect" in types

    def test_setup_skipped_when_no_profiles(self):
        md2 = _fake_md2(supported_profiles=[])
        types = [
            type(e).__name__
            for e in _setup_selects(_make_select_session(motion_detectors2=[md2]))
        ]
        assert "InstallationProfileSelect" not in types

    # The device-level "profile" field (and lib SHCDevice.set_profile) is not
    # MD2-specific: real-world rawscans confirm non-empty supportedProfiles
    # on MICROMODULE_RELAY / PLUG_COMPACT / PLUG_COMPACT_DUAL
    # (knowledge-base/rawscan-database.md), so the select must also be wired
    # up for micromodule_relays / smart_plugs / smart_plugs_compact.
    def test_setup_created_for_micromodule_relay_when_profiles_present(self):
        relay = _fake_md2(
            profile="LIGHT", supported_profiles=["LIGHT", "GENERIC", "HEATING_RCC"]
        )
        types = [
            type(e).__name__
            for e in _setup_selects(_make_select_session(micromodule_relays=[relay]))
        ]
        assert "InstallationProfileSelect" in types

    def test_setup_created_for_smart_plug_when_profiles_present(self):
        plug = _fake_md2(
            profile="GENERIC", supported_profiles=["LIGHT", "GENERIC", "HEATING_RCC"]
        )
        types = [
            type(e).__name__
            for e in _setup_selects(_make_select_session(smart_plugs=[plug]))
        ]
        assert "InstallationProfileSelect" in types

    def test_setup_created_for_smart_plug_compact_when_profiles_present(self):
        plug = _fake_md2(
            profile="MINI_PV",
            supported_profiles=["LIGHT", "MINI_PV", "GENERIC", "HEATING_RCC"],
        )
        types = [
            type(e).__name__
            for e in _setup_selects(
                _make_select_session(smart_plugs_compact=[plug])
            )
        ]
        assert "InstallationProfileSelect" in types

    def test_setup_skipped_for_micromodule_relay_when_no_profiles(self):
        relay = _fake_md2(supported_profiles=[])
        types = [
            type(e).__name__
            for e in _setup_selects(_make_select_session(micromodule_relays=[relay]))
        ]
        assert "InstallationProfileSelect" not in types


# ---------------------------------------------------------------------------
# OrientationLightResponseSelect (PollControl)
# ---------------------------------------------------------------------------


class TestOrientationLightResponseSelect:
    def _make(self, interval="LONG"):
        from boschshcpy.services_impl import PollControlService

        dev = _fake_md2(
            long_poll_interval=PollControlService.PollControlState[interval],
            async_set_long_poll_interval=AsyncMock(),
        )
        e = OrientationLightResponseSelect.__new__(OrientationLightResponseSelect)
        e._device = dev
        return e

    def test_current_option(self):
        assert self._make("SHORT").current_option == "SHORT"

    def test_current_option_unknown_not_in_options(self):
        e = self._make("UNKNOWN")
        assert e.current_option is None

    def test_async_select_option(self):
        from boschshcpy.services_impl import PollControlService

        e = self._make("LONG")
        asyncio.run(e.async_select_option("SHORT"))
        e._device.async_set_long_poll_interval.assert_called_once_with(
            PollControlService.PollControlState.SHORT
        )

    def test_options(self):
        e = OrientationLightResponseSelect.__new__(OrientationLightResponseSelect)
        assert e._attr_options == ["LONG", "SHORT"]

    def test_setup_created_when_interval_present(self):
        from boschshcpy.services_impl import PollControlService

        md2 = _fake_md2(long_poll_interval=PollControlService.PollControlState.LONG)
        types = [
            type(e).__name__
            for e in _setup_selects(_make_select_session(motion_detectors2=[md2]))
        ]
        assert "OrientationLightResponseSelect" in types

    def test_setup_skipped_when_no_interval(self):
        md2 = _fake_md2()  # no long_poll_interval
        types = [
            type(e).__name__
            for e in _setup_selects(_make_select_session(motion_detectors2=[md2]))
        ]
        assert "OrientationLightResponseSelect" not in types


# ---------------------------------------------------------------------------
# Tamper protection switch (generic SHCSwitch)
# ---------------------------------------------------------------------------


class TestTamperProtectionSwitch:
    def test_switch_type_defined(self):
        desc = SWITCH_TYPES["tamper_protection_enabled"]
        assert desc.on_key == "tamper_protection_enabled"
        assert desc.on_value is True

    def test_is_on_reads_property(self):
        dev = _fake_md2(tamper_protection_enabled=True)
        sw = SHCSwitch.__new__(SHCSwitch)
        sw._device = dev
        sw.entity_description = SWITCH_TYPES["tamper_protection_enabled"]
        assert sw.is_on is True

    def test_turn_on_calls_async_setter(self):
        dev = _fake_md2(
            tamper_protection_enabled=False,
            async_set_tamper_protection_enabled=AsyncMock(),
        )
        sw = SHCSwitch.__new__(SHCSwitch)
        sw._device = dev
        sw.entity_description = SWITCH_TYPES["tamper_protection_enabled"]
        asyncio.run(sw.async_turn_on())
        dev.async_set_tamper_protection_enabled.assert_called_once_with(True)

    def test_turn_off_calls_async_setter(self):
        dev = _fake_md2(
            tamper_protection_enabled=True,
            async_set_tamper_protection_enabled=AsyncMock(),
        )
        sw = SHCSwitch.__new__(SHCSwitch)
        sw._device = dev
        sw.entity_description = SWITCH_TYPES["tamper_protection_enabled"]
        asyncio.run(sw.async_turn_off())
        dev.async_set_tamper_protection_enabled.assert_called_once_with(False)
