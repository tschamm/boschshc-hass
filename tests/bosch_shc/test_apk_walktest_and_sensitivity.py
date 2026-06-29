"""Tests for APK-batch 3 WalkTest entities + SmartSensitivity select entities.

Covers:
- SHCWalkTestButton (start)
- SHCWalkTestStopButton (stop)
- WalkStateSensor
- SmartSensitivitySecurityLevelSelect
- SmartSensitivityComfortLevelSelect

Run with:
  PYTHONPATH="<lib>:<hass>" PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
  python3 -m pytest tests/bosch_shc/test_apk_walktest_and_sensitivity.py -q -o addopts=
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.bosch_shc.button import (
    SHCWalkTestButton,
    SHCWalkTestStopButton,
)
from custom_components.bosch_shc.button import (
    async_setup_entry as button_setup_entry,
)
from custom_components.bosch_shc.const import DATA_SESSION, DATA_SHC, DOMAIN
from custom_components.bosch_shc.select import (
    SmartSensitivityComfortLevelSelect,
    SmartSensitivitySecurityLevelSelect,
)
from custom_components.bosch_shc.select import (
    async_setup_entry as select_setup_entry,
)
from custom_components.bosch_shc.sensor import WalkStateSensor

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_md2(**kwargs):
    defaults = dict(name="MD2", id="md1", root_device_id="root1", serial="SER1",
                    supports_silentmode=False, supports_batterylevel=False)
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _executor_hass():
    """Hass stub whose async_add_executor_job runs the sync fn inline.

    The integration runs the sync session, so entities call the device's
    SYNC setter via hass.async_add_executor_job (not the async_* coroutine).
    """
    async def _run(fn, *args):
        return fn(*args)
    return SimpleNamespace(async_add_executor_job=_run)


def _make_button_session(**helper_lists):
    defaults = dict(
        smoke_detectors=[],
        twinguards=[],
        motion_detectors2=[],
        userdefinedstates=[],
    )
    defaults.update(helper_lists)
    device_helper = SimpleNamespace(**defaults)
    return SimpleNamespace(
        device_helper=device_helper,
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
    device_helper = SimpleNamespace(**defaults)
    return SimpleNamespace(device_helper=device_helper)


def _make_button_hass_and_entry(session):
    entry_id = "E1"
    hass = SimpleNamespace(
        data={
            DOMAIN: {entry_id: {
                DATA_SESSION: session,
                DATA_SHC: SimpleNamespace(
                    name="SHC", id="shc", identifiers={("bosch_shc", "shc")},
                    manufacturer="Bosch", model="SHC"),
            }}
        }
    )
    config_entry = SimpleNamespace(
        options={},
        entry_id=entry_id,
        unique_id="UID1",
        async_on_unload=MagicMock(),
    )
    return hass, config_entry


def _make_select_hass_and_entry(session):
    entry_id = "E1"
    hass = SimpleNamespace(
        data={DOMAIN: {entry_id: {DATA_SESSION: session}}}
    )
    config_entry = SimpleNamespace(options={}, entry_id=entry_id,
                                   unique_id="UID1",
                                   async_on_unload=MagicMock())
    return hass, config_entry


async def _async_setup_buttons(session):
    hass, config_entry = _make_button_hass_and_entry(session)
    entities = []

    def add_entities(new_ents, *args, **kwargs):
        entities.extend(new_ents)

    await button_setup_entry(hass, config_entry, add_entities)
    return entities


def _setup_buttons(session):
    return asyncio.run(_async_setup_buttons(session))


async def _async_setup_selects(session):
    hass, config_entry = _make_select_hass_and_entry(session)
    entities = []

    def add_entities(new_ents, *args, **kwargs):
        entities.extend(new_ents)

    with patch(
        "custom_components.bosch_shc.select.SHCShutterContact2Plus",
        new=type("SHCShutterContact2Plus", (), {}),
    ):
        await select_setup_entry(hass, config_entry, add_entities)
    return entities


def _setup_selects(session):
    return asyncio.run(_async_setup_selects(session))


# ---------------------------------------------------------------------------
# WalkTest start button — setup entry
# ---------------------------------------------------------------------------


class TestWalkTestButtonSetup:
    def test_walk_test_button_created_when_walk_state_present(self):
        from boschshcpy.services_impl import WalkTestService
        md2 = _fake_md2(walk_state=WalkTestService.WalkState.UNKNOWN, supports_walk_test=True)
        session = _make_button_session(motion_detectors2=[md2])
        entities = _setup_buttons(session)
        types = [type(e).__name__ for e in entities]
        assert "SHCWalkTestButton" in types

    def test_walk_test_button_skipped_when_no_walk_state_attr(self):
        md2 = _fake_md2()  # no walk_state attr
        session = _make_button_session(motion_detectors2=[md2])
        entities = _setup_buttons(session)
        types = [type(e).__name__ for e in entities]
        assert "SHCWalkTestButton" not in types

    def test_walk_test_button_skipped_when_walk_state_is_none(self):
        # supports_walk_test=True but walk_state=None -> skipped at line 75
        md2 = _fake_md2(walk_state=None, supports_walk_test=True)
        session = _make_button_session(motion_detectors2=[md2])
        entities = _setup_buttons(session)
        types = [type(e).__name__ for e in entities]
        assert "SHCWalkTestButton" not in types

    def test_walk_test_stop_button_created_alongside_start(self):
        from boschshcpy.services_impl import WalkTestService
        md2 = _fake_md2(walk_state=WalkTestService.WalkState.UNKNOWN, supports_walk_test=True)
        session = _make_button_session(motion_detectors2=[md2])
        entities = _setup_buttons(session)
        types = [type(e).__name__ for e in entities]
        assert "SHCWalkTestStopButton" in types

    def test_walk_test_stop_button_skipped_when_no_walk_state(self):
        md2 = _fake_md2()  # no walk_state attr
        session = _make_button_session(motion_detectors2=[md2])
        entities = _setup_buttons(session)
        types = [type(e).__name__ for e in entities]
        assert "SHCWalkTestStopButton" not in types


# ---------------------------------------------------------------------------
# SHCWalkTestButton — unit tests
# ---------------------------------------------------------------------------


class TestSHCWalkTestButton:
    def _make(self):
        dev = _fake_md2()
        b = SHCWalkTestButton.__new__(SHCWalkTestButton)
        b._device = dev
        b._attr_unique_id = f"{dev.root_device_id}_{dev.id}_walk_test"
        b._attr_name = "Walk Test"
        return b

    def test_unique_id(self):
        b = self._make()
        assert b._attr_unique_id == "root1_md1_walk_test"

    def test_async_press_calls_async_set_walk_state_request(self):
        from boschshcpy.services_impl import WalkTestService
        dev = _fake_md2(async_set_walk_state_request=AsyncMock())
        b = SHCWalkTestButton.__new__(SHCWalkTestButton)
        b._device = dev
        asyncio.run(b.async_press())
        dev.async_set_walk_state_request.assert_called_once_with(
            WalkTestService.WalkStateRequest.WALK_STATE_START
        )

    def test_async_press_with_real_enum_value(self):
        from boschshcpy.services_impl import WalkTestService
        dev = _fake_md2(async_set_walk_state_request=AsyncMock())
        b = SHCWalkTestButton.__new__(SHCWalkTestButton)
        b._device = dev
        asyncio.run(b.async_press())
        call_arg = dev.async_set_walk_state_request.call_args[0][0]
        assert call_arg == WalkTestService.WalkStateRequest.WALK_STATE_START

    def test_icon(self):
        b = self._make()
        assert b._attr_icon == "mdi:walk"


# ---------------------------------------------------------------------------
# SHCWalkTestStopButton — unit tests
# ---------------------------------------------------------------------------


class TestSHCWalkTestStopButton:
    def _make(self):
        dev = _fake_md2()
        b = SHCWalkTestStopButton.__new__(SHCWalkTestStopButton)
        b._device = dev
        b._attr_unique_id = f"{dev.root_device_id}_{dev.id}_walk_test_stop"
        b._attr_name = "Walk Test Stop"
        return b

    def test_unique_id(self):
        b = self._make()
        assert b._attr_unique_id == "root1_md1_walk_test_stop"

    def test_async_press_calls_async_set_walk_state_request_stop(self):
        from boschshcpy.services_impl import WalkTestService
        dev = _fake_md2(async_set_walk_state_request=AsyncMock())
        b = SHCWalkTestStopButton.__new__(SHCWalkTestStopButton)
        b._device = dev
        asyncio.run(b.async_press())
        dev.async_set_walk_state_request.assert_called_once_with(
            WalkTestService.WalkStateRequest.WALK_STATE_STOP
        )

    def test_async_press_uses_walk_state_stop_not_start(self):
        from boschshcpy.services_impl import WalkTestService
        dev = _fake_md2(async_set_walk_state_request=AsyncMock())
        b = SHCWalkTestStopButton.__new__(SHCWalkTestStopButton)
        b._device = dev
        asyncio.run(b.async_press())
        call_arg = dev.async_set_walk_state_request.call_args[0][0]
        assert call_arg == WalkTestService.WalkStateRequest.WALK_STATE_STOP
        assert call_arg != WalkTestService.WalkStateRequest.WALK_STATE_START

    def test_icon(self):
        b = self._make()
        assert b._attr_icon == "mdi:stop"


# ---------------------------------------------------------------------------
# WalkStateSensor — unit tests
# ---------------------------------------------------------------------------


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
        assert "walk_test_started" in s._attr_options
        assert "walk_test_stopped" in s._attr_options
        assert "unknown" in s._attr_options


# ---------------------------------------------------------------------------
# WalkStateSensor — setup entry (part of sensor.py)
# ---------------------------------------------------------------------------


class TestWalkStateSensorSetup:
    def _run_sensor_setup(self, md2_list):
        from custom_components.bosch_shc.const import DATA_SHC
        from custom_components.bosch_shc.sensor import async_setup_entry as sensor_setup

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
        hass = SimpleNamespace(
            data={
                DOMAIN: {entry_id: {
                    DATA_SESSION: session,
                    DATA_SHC: SimpleNamespace(
                        name="SHC", id="shc",
                        identifiers={("bosch_shc", "shc")},
                        manufacturer="Bosch", model="SHC",
                    ),
                }}
            }
        )
        config_entry = SimpleNamespace(
            options={},
            entry_id=entry_id,
            async_on_unload=MagicMock(),
        )
        entities = []

        def add_entities(new_ents, *args, **kwargs):
            entities.extend(new_ents)

        with patch(
            "custom_components.bosch_shc.sensor.async_migrate_to_new_unique_id",
            new=AsyncMock(return_value=None),
        ):
            asyncio.run(sensor_setup(hass, config_entry, add_entities))

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


# ---------------------------------------------------------------------------
# SmartSensitivitySecurityLevelSelect
# ---------------------------------------------------------------------------


class TestSmartSensitivitySecurityLevelSelect:
    def _make(self, manual_level="HIGH"):
        from boschshcpy.services_impl import SmartSensitivityControlService
        level_val = SmartSensitivityControlService.MotionSensitivity[manual_level]
        sensitivity_dict = {
            "context": "SECURITY",
            "automaticLevel": "HIGH",
            "manualLevel": level_val,
        }

        def _get_sensitivity(c):
            return sensitivity_dict

        dev = _fake_md2(get_smart_sensitivity=_get_sensitivity)
        e = SmartSensitivitySecurityLevelSelect.__new__(
            SmartSensitivitySecurityLevelSelect
        )
        e._device = dev
        e._attr_unique_id = (
            f"{dev.root_device_id}_{dev.id}_smart_sensitivity_security"
        )
        e._attr_name = "Security Sensitivity Level"
        return e

    def test_unique_id(self):
        e = self._make()
        assert e._attr_unique_id == "root1_md1_smart_sensitivity_security"

    def test_current_option_high(self):
        e = self._make("HIGH")
        assert e.current_option == "HIGH"

    def test_current_option_middle(self):
        e = self._make("MIDDLE")
        assert e.current_option == "MIDDLE"

    def test_current_option_low(self):
        e = self._make("LOW")
        assert e.current_option == "LOW"

    def test_current_option_none_when_get_returns_none(self):
        def _get_none(c):
            return None

        dev = _fake_md2(get_smart_sensitivity=_get_none)
        e = SmartSensitivitySecurityLevelSelect.__new__(
            SmartSensitivitySecurityLevelSelect
        )
        e._device = dev
        assert e.current_option is None

    def test_current_option_none_when_manual_level_absent(self):
        def _get_no_level(c):
            return {"context": "SECURITY"}  # no manualLevel key

        dev = _fake_md2(get_smart_sensitivity=_get_no_level)
        e = SmartSensitivitySecurityLevelSelect.__new__(
            SmartSensitivitySecurityLevelSelect
        )
        e._device = dev
        assert e.current_option is None

    def test_async_select_option_calls_device_setter(self):
        from boschshcpy.services_impl import SmartSensitivityControlService
        ctx = SmartSensitivityControlService.SmartSensitivityContext.SECURITY
        dev = _fake_md2(
            get_smart_sensitivity=lambda c: {"manualLevel": "HIGH"},
            async_set_smart_sensitivity_manual_level=AsyncMock(),
        )
        e = SmartSensitivitySecurityLevelSelect.__new__(
            SmartSensitivitySecurityLevelSelect
        )
        e._device = dev
        asyncio.run(e.async_select_option("MIDDLE"))
        dev.async_set_smart_sensitivity_manual_level.assert_called_once_with(
            ctx, SmartSensitivityControlService.MotionSensitivity.MIDDLE
        )

    def test_created_when_get_smart_sensitivity_present(self):
        md2 = _fake_md2(get_smart_sensitivity=lambda c: {"manualLevel": "HIGH"}, supports_smart_sensitivity=True)
        session = _make_select_session(motion_detectors2=[md2])
        entities = _setup_selects(session)
        types = [type(e).__name__ for e in entities]
        assert "SmartSensitivitySecurityLevelSelect" in types

    def test_skipped_when_get_smart_sensitivity_absent(self):
        md2 = _fake_md2()  # no get_smart_sensitivity attr
        session = _make_select_session(motion_detectors2=[md2])
        entities = _setup_selects(session)
        types = [type(e).__name__ for e in entities]
        assert "SmartSensitivitySecurityLevelSelect" not in types

    def test_entity_category_config(self):
        from homeassistant.helpers.entity import EntityCategory
        e = SmartSensitivitySecurityLevelSelect.__new__(
            SmartSensitivitySecurityLevelSelect
        )
        assert e._attr_entity_category == EntityCategory.CONFIG

    def test_options_list_contains_high_middle_low(self):
        e = SmartSensitivitySecurityLevelSelect.__new__(
            SmartSensitivitySecurityLevelSelect
        )
        assert "HIGH" in e._attr_options
        assert "MIDDLE" in e._attr_options
        assert "LOW" in e._attr_options

    def test_current_option_string_level(self):
        """Level may be a plain string (not an enum) — should still work."""
        def _get_str_level(c):
            return {"context": "SECURITY", "manualLevel": "LOW"}

        dev = _fake_md2(get_smart_sensitivity=_get_str_level)
        e = SmartSensitivitySecurityLevelSelect.__new__(
            SmartSensitivitySecurityLevelSelect
        )
        e._device = dev
        assert e.current_option == "LOW"

    def test_current_option_none_when_name_not_in_options(self):
        """Level has .name but it is not in HIGH/MIDDLE/LOW (e.g. UNKNOWN enum)."""
        from boschshcpy.services_impl import SmartSensitivityControlService
        unknown = SmartSensitivityControlService.MotionSensitivity.UNKNOWN

        def _get_unknown(c):
            return {"context": "SECURITY", "manualLevel": unknown}

        dev = _fake_md2(get_smart_sensitivity=_get_unknown)
        e = SmartSensitivitySecurityLevelSelect.__new__(
            SmartSensitivitySecurityLevelSelect
        )
        e._device = dev
        assert e.current_option is None


# ---------------------------------------------------------------------------
# SmartSensitivityComfortLevelSelect
# ---------------------------------------------------------------------------


class TestSmartSensitivityComfortLevelSelect:
    def _make(self, manual_level="MIDDLE"):
        from boschshcpy.services_impl import SmartSensitivityControlService
        level_val = SmartSensitivityControlService.MotionSensitivity[manual_level]

        def _get_sensitivity(c):
            return {
                "context": "COMFORT",
                "automaticLevel": "MIDDLE",
                "manualLevel": level_val,
            }

        dev = _fake_md2(get_smart_sensitivity=_get_sensitivity)
        e = SmartSensitivityComfortLevelSelect.__new__(
            SmartSensitivityComfortLevelSelect
        )
        e._device = dev
        e._attr_unique_id = (
            f"{dev.root_device_id}_{dev.id}_smart_sensitivity_comfort"
        )
        e._attr_name = "Comfort Sensitivity Level"
        return e

    def test_unique_id(self):
        e = self._make()
        assert e._attr_unique_id == "root1_md1_smart_sensitivity_comfort"

    def test_current_option_middle(self):
        e = self._make("MIDDLE")
        assert e.current_option == "MIDDLE"

    def test_current_option_high(self):
        e = self._make("HIGH")
        assert e.current_option == "HIGH"

    def test_current_option_low(self):
        e = self._make("LOW")
        assert e.current_option == "LOW"

    def test_current_option_none_when_get_returns_none(self):
        def _get_none(c):
            return None

        dev = _fake_md2(get_smart_sensitivity=_get_none)
        e = SmartSensitivityComfortLevelSelect.__new__(
            SmartSensitivityComfortLevelSelect
        )
        e._device = dev
        assert e.current_option is None

    def test_current_option_none_when_manual_level_absent(self):
        def _get_no_level(c):
            return {"context": "COMFORT"}  # no manualLevel key

        dev = _fake_md2(get_smart_sensitivity=_get_no_level)
        e = SmartSensitivityComfortLevelSelect.__new__(
            SmartSensitivityComfortLevelSelect
        )
        e._device = dev
        assert e.current_option is None

    def test_async_select_option_calls_device_setter(self):
        from boschshcpy.services_impl import SmartSensitivityControlService
        ctx = SmartSensitivityControlService.SmartSensitivityContext.COMFORT
        dev = _fake_md2(
            get_smart_sensitivity=lambda c: {"manualLevel": "MIDDLE"},
            async_set_smart_sensitivity_manual_level=AsyncMock(),
        )
        e = SmartSensitivityComfortLevelSelect.__new__(
            SmartSensitivityComfortLevelSelect
        )
        e._device = dev
        asyncio.run(e.async_select_option("HIGH"))
        dev.async_set_smart_sensitivity_manual_level.assert_called_once_with(
            ctx, SmartSensitivityControlService.MotionSensitivity.HIGH
        )

    def test_created_when_guard_present(self):
        md2 = _fake_md2(
            get_smart_sensitivity=lambda c: {"manualLevel": "MIDDLE"},
            supports_smart_sensitivity=True,
        )
        session = _make_select_session(motion_detectors2=[md2])
        entities = _setup_selects(session)
        types = [type(e).__name__ for e in entities]
        assert "SmartSensitivityComfortLevelSelect" in types

    def test_skipped_when_guard_absent(self):
        md2 = _fake_md2()  # no get_smart_sensitivity attr
        session = _make_select_session(motion_detectors2=[md2])
        entities = _setup_selects(session)
        types = [type(e).__name__ for e in entities]
        assert "SmartSensitivityComfortLevelSelect" not in types

    def test_entity_category_config(self):
        from homeassistant.helpers.entity import EntityCategory
        e = SmartSensitivityComfortLevelSelect.__new__(
            SmartSensitivityComfortLevelSelect
        )
        assert e._attr_entity_category == EntityCategory.CONFIG

    def test_options_list_contains_high_middle_low(self):
        e = SmartSensitivityComfortLevelSelect.__new__(
            SmartSensitivityComfortLevelSelect
        )
        assert "HIGH" in e._attr_options
        assert "MIDDLE" in e._attr_options
        assert "LOW" in e._attr_options

    def test_current_option_string_level(self):
        """Level may be a plain string (not an enum) — should still work."""
        def _get_str_level(c):
            return {"context": "COMFORT", "manualLevel": "HIGH"}

        dev = _fake_md2(get_smart_sensitivity=_get_str_level)
        e = SmartSensitivityComfortLevelSelect.__new__(
            SmartSensitivityComfortLevelSelect
        )
        e._device = dev
        assert e.current_option == "HIGH"

    def test_current_option_none_when_name_not_in_options(self):
        """Level has .name but it is not in HIGH/MIDDLE/LOW (e.g. UNKNOWN enum)."""
        from boschshcpy.services_impl import SmartSensitivityControlService
        unknown = SmartSensitivityControlService.MotionSensitivity.UNKNOWN

        def _get_unknown(c):
            return {"context": "COMFORT", "manualLevel": unknown}

        dev = _fake_md2(get_smart_sensitivity=_get_unknown)
        e = SmartSensitivityComfortLevelSelect.__new__(
            SmartSensitivityComfortLevelSelect
        )
        e._device = dev
        assert e.current_option is None
