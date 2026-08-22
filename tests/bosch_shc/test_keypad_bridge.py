"""Tests for keypad_bridge.py (#395: physical-pushbutton-to-HA bridge)."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from boschshcpy import SwitchConfiguration
from boschshcpy.exceptions import SHCException

from custom_components.bosch_shc.keypad_bridge import (
    _SCHEMA_VERSION,
    DATA_KEYPAD_BRIDGE_MAP,
    _build_automation,
    _build_swd2_automation,
    _swd2_uds_name,
    _uds_name,
    async_sync_keypad_bridge,
)


@pytest.fixture(autouse=True)
def mock_remove_stale_entity():
    """entity_registry access needs a real hass; irrelevant to this module's
    own logic, so patch it out for every test (asserted on explicitly where
    it matters)."""
    with patch(
        "custom_components.bosch_shc.keypad_bridge.async_remove_stale_entity",
        new_callable=AsyncMock,
    ) as mock:
        yield mock


def _make_device(device_id: str, name: str, has_keypad: bool = True) -> SimpleNamespace:
    return SimpleNamespace(id=device_id, name=name, has_keypad=has_keypad, room_id=None)


def _make_light_control_device(
    device_id: str,
    name: str,
    switch_type: SwitchConfiguration.SwitchType | None = (
        SwitchConfiguration.SwitchType.PUSHBUTTON
    ),
    has_keypad: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=device_id, name=name, switch_type=switch_type, has_keypad=has_keypad, room_id=None
    )


def _make_entry(data=None, options=None, session=None):
    entry = SimpleNamespace(
        data=data or {},
        options=options or {},
        entry_id="E1",
    )
    entry.runtime_data = SimpleNamespace(session=session)
    return entry


def _make_hass():
    hass = MagicMock()
    hass.config_entries.async_update_entry = MagicMock()
    return hass


def _make_session(devices, swd2_devices=None, light_control_devices=None):
    session = SimpleNamespace()
    session.device_helper = SimpleNamespace(
        shutter_controls=devices,
        micromodule_shutter_controls=[],
        micromodule_blinds=[],
        shutter_contacts2=swd2_devices or [],
        micromodule_light_controls=light_control_devices or [],
    )
    session.information = SimpleNamespace(macAddress="AA:BB:CC:DD:EE:FF")
    session.async_create_userdefinedstate = AsyncMock()
    session.async_create_automation_rule = AsyncMock()
    session.async_delete_automation_rule = AsyncMock()
    session.async_delete_userdefinedstate = AsyncMock()
    return session


def _run(coro):
    return asyncio.run(coro)


# All 4 keys created for a single keypad-capable device.
_D1_ALL_KEYS = (
    f"d1_1_PRESS_SHORT_{_SCHEMA_VERSION}",
    f"d1_1_PRESS_LONG_{_SCHEMA_VERSION}",
    f"d1_2_PRESS_SHORT_{_SCHEMA_VERSION}",
    f"d1_2_PRESS_LONG_{_SCHEMA_VERSION}",
)


class TestDisabledNoOp:
    def test_disabled_with_empty_map_does_not_update_entry(self):
        session = _make_session([_make_device("d1", "Shutter Büro")])
        entry = _make_entry(session=session)
        hass = _make_hass()

        _run(async_sync_keypad_bridge(hass, entry, enabled=False))

        session.async_create_userdefinedstate.assert_not_awaited()
        hass.config_entries.async_update_entry.assert_not_called()

    def test_disabled_deletes_existing_bridge_entries(self):
        session = _make_session([])
        entry = _make_entry(
            data={
                DATA_KEYPAD_BRIDGE_MAP: {
                    f"d1_1_PRESS_SHORT_{_SCHEMA_VERSION}": {
                        "userdefinedstate_id": "u1",
                        "automation_id": "a1",
                    }
                }
            },
            session=session,
        )
        hass = _make_hass()

        _run(async_sync_keypad_bridge(hass, entry, enabled=False))

        session.async_delete_automation_rule.assert_awaited_once_with("a1")
        session.async_delete_userdefinedstate.assert_awaited_once_with("u1")
        sent_data = hass.config_entries.async_update_entry.call_args.kwargs["data"]
        assert sent_data[DATA_KEYPAD_BRIDGE_MAP] == {}

    def test_disabled_removes_the_stale_switch_entity(self, mock_remove_stale_entity):
        """The deleted UserDefinedState's switch entity must not linger as
        a permanently "unavailable" registry ghost (#395)."""
        from homeassistant.const import Platform

        session = _make_session([])
        entry = _make_entry(
            data={
                DATA_KEYPAD_BRIDGE_MAP: {
                    f"d1_1_PRESS_SHORT_{_SCHEMA_VERSION}": {
                        "userdefinedstate_id": "u1",
                        "automation_id": "a1",
                    }
                }
            },
            session=session,
        )
        hass = _make_hass()

        _run(async_sync_keypad_bridge(hass, entry, enabled=False))

        mock_remove_stale_entity.assert_awaited_once_with(
            hass, Platform.SWITCH, "AA:BB:CC:DD:EE:FF_u1"
        )


class TestEnabledCreatesForEligibleDevices:
    def test_creates_one_entry_per_button_per_press_type(self):
        """Short and long press on the same button must stay distinguishable
        (#395 follow-up: combining them made both fire the same switch)."""
        device = _make_device("hdm:ZigBee:abc", "Rollladen Büro")
        session = _make_session([device])
        session.async_create_userdefinedstate.side_effect = [
            SimpleNamespace(id=f"u{i}") for i in range(4)
        ]
        session.async_create_automation_rule.side_effect = [
            SimpleNamespace(id=f"a{i}") for i in range(4)
        ]
        entry = _make_entry(session=session)
        hass = _make_hass()

        _run(async_sync_keypad_bridge(hass, entry, enabled=True))

        assert session.async_create_userdefinedstate.await_count == 4
        assert session.async_create_automation_rule.await_count == 4
        sent_data = hass.config_entries.async_update_entry.call_args.kwargs["data"]
        bridge_map = sent_data[DATA_KEYPAD_BRIDGE_MAP]
        for key in (
            f"hdm:ZigBee:abc_1_PRESS_SHORT_{_SCHEMA_VERSION}",
            f"hdm:ZigBee:abc_1_PRESS_LONG_{_SCHEMA_VERSION}",
            f"hdm:ZigBee:abc_2_PRESS_SHORT_{_SCHEMA_VERSION}",
            f"hdm:ZigBee:abc_2_PRESS_LONG_{_SCHEMA_VERSION}",
        ):
            assert key in bridge_map

    def test_skips_devices_without_keypad(self):
        device = _make_device("d1", "No Keypad", has_keypad=False)
        session = _make_session([device])
        entry = _make_entry(session=session)
        hass = _make_hass()

        _run(async_sync_keypad_bridge(hass, entry, enabled=True))

        session.async_create_userdefinedstate.assert_not_awaited()

    def test_skips_excluded_devices(self):
        device = _make_device("d1", "Excluded")
        session = _make_session([device])
        entry = _make_entry(session=session, options={"excluded_devices": ["d1"]})
        hass = _make_hass()

        _run(async_sync_keypad_bridge(hass, entry, enabled=True))

        session.async_create_userdefinedstate.assert_not_awaited()

    def test_idempotent_skips_already_created_entries(self):
        device = _make_device("d1", "Already bridged")
        session = _make_session([device])
        entry = _make_entry(
            data={
                DATA_KEYPAD_BRIDGE_MAP: {
                    key: {"userdefinedstate_id": f"u{i}", "automation_id": f"a{i}"}
                    for i, key in enumerate(_D1_ALL_KEYS)
                }
            },
            session=session,
        )
        hass = _make_hass()

        _run(async_sync_keypad_bridge(hass, entry, enabled=True))

        session.async_create_userdefinedstate.assert_not_awaited()
        session.async_create_automation_rule.assert_not_awaited()
        hass.config_entries.async_update_entry.assert_not_called()

    def test_removes_stale_entries_for_now_excluded_device(self):
        """A device excluded after being bridged must have its entries
        cleaned up."""
        device = _make_device("d1", "Now excluded")
        session = _make_session([device])
        entry = _make_entry(
            data={
                DATA_KEYPAD_BRIDGE_MAP: {
                    key: {"userdefinedstate_id": f"u{i}", "automation_id": f"a{i}"}
                    for i, key in enumerate(_D1_ALL_KEYS)
                }
            },
            options={"excluded_devices": ["d1"]},
            session=session,
        )
        hass = _make_hass()

        _run(async_sync_keypad_bridge(hass, entry, enabled=True))

        assert session.async_delete_automation_rule.await_count == 4
        assert session.async_delete_userdefinedstate.await_count == 4
        sent_data = hass.config_entries.async_update_entry.call_args.kwargs["data"]
        assert sent_data[DATA_KEYPAD_BRIDGE_MAP] == {}


class TestUserDefinedStateNameLength:
    def test_long_device_name_is_truncated_to_30_chars(self):
        """UserDefinedState.name is capped at 30 chars by the Controller
        itself -- a longer name gets a bare 400 (live-confirmed, #395)."""
        name = _uds_name("A Very Long Shutter Device Name Indeed", 1, "PRESS_SHORT")
        assert len(name) <= 30
        assert name.endswith(" Btn1S")

    def test_short_and_long_press_produce_different_names(self):
        short_name = _uds_name("Shutter", 2, "PRESS_SHORT")
        long_name = _uds_name("Shutter", 2, "PRESS_LONG")
        assert short_name == "Shutter Btn2S"
        assert long_name == "Shutter Btn2L"
        assert short_name != long_name

    def test_creates_state_with_truncated_name(self):
        device = _make_device(
            "d1", "A Very Long Shutter Device Name That Exceeds The Limit"
        )
        session = _make_session([device])
        session.async_create_userdefinedstate.return_value = SimpleNamespace(id="u1")
        session.async_create_automation_rule.return_value = SimpleNamespace(id="a1")
        entry = _make_entry(session=session)
        hass = _make_hass()

        _run(async_sync_keypad_bridge(hass, entry, enabled=True))

        for call in session.async_create_userdefinedstate.await_args_list:
            assert len(call.args[0]) <= 30


class TestAutomationCreateFailureRollsBackState:
    def test_rolls_back_state_when_automation_create_fails(self):
        device = _make_device("d1", "Shutter")
        session = _make_session([device])
        session.async_create_userdefinedstate.return_value = SimpleNamespace(id="u1")
        session.async_create_automation_rule.side_effect = SHCException("boom")
        entry = _make_entry(session=session)
        hass = _make_hass()

        _run(async_sync_keypad_bridge(hass, entry, enabled=True))

        # Rolled back once per (failed) combination — 4 for this device.
        assert session.async_delete_userdefinedstate.await_count == 4
        # Nothing ended up in the map (unchanged from the empty starting
        # point), so no entry-data write was needed at all.
        hass.config_entries.async_update_entry.assert_not_called()


class TestAutomationBodyShape:
    def test_trigger_and_action_configuration_shape(self):
        """The built automation must match the live-confirmed
        KeypadMicromoduleShadingTrigger / UserDefinedStateAction envelope
        (bosch-shc-api-docs #10), with exactly one trigger — short/long press
        get separate automations (#395 follow-up). The generic
        KeypadButtonPressTrigger the feature originally shipped with was
        rejected by the Bosch app as an invalid trigger for shading devices
        (#385 follow-up)."""
        spec = _build_automation(
            "[HA] Test Button 1", "hdm:ZigBee:abc", 1, "PRESS_SHORT", "uds-1"
        )

        assert spec["name"] == "[HA] Test Button 1"
        assert len(spec["triggers"]) == 1
        trigger = spec["triggers"][0]
        assert trigger["type"] == "KeypadMicromoduleShadingTrigger"
        config = json.loads(trigger["configuration"])
        assert config["deviceId"] == "hdm:ZigBee:abc"
        assert config["buttonId"] == 1
        assert config["buttonEvent"] == "PRESS_SHORT"
        assert "keyName" not in config

        assert len(spec["actions"]) == 2
        active_action = next(
            a
            for a in spec["actions"]
            if json.loads(a["configuration"])["state"] == "ACTIVE"
        )
        inactive_action = next(
            a
            for a in spec["actions"]
            if json.loads(a["configuration"])["state"] == "INACTIVE"
        )
        assert active_action["type"] == "UserDefinedStateAction"
        assert active_action["delayInSeconds"] == 0
        assert json.loads(active_action["configuration"])["stateId"] == "uds-1"
        assert inactive_action["delayInSeconds"] > 0

    def test_short_and_long_press_build_distinct_triggers(self):
        short_spec = _build_automation("x", "hdm:ZigBee:abc", 1, "PRESS_SHORT", "uds-1")
        long_spec = _build_automation("x", "hdm:ZigBee:abc", 1, "PRESS_LONG", "uds-1")

        short_event = json.loads(short_spec["triggers"][0]["configuration"])[
            "buttonEvent"
        ]
        long_event = json.loads(long_spec["triggers"][0]["configuration"])[
            "buttonEvent"
        ]
        assert short_event == "PRESS_SHORT"
        assert long_event == "PRESS_LONG"

    def test_trigger_type_is_overridable(self):
        """#282: Light Control II needs KeypadMicromoduleLightTrigger instead
        of the shading type, with the same field shape (decompile-confirmed
        both share SimpleButtonPressTriggerConfiguration)."""
        spec = _build_automation(
            "x",
            "hdm:ZigBee:abc",
            1,
            "PRESS_SHORT",
            "uds-1",
            trigger_type="KeypadMicromoduleLightTrigger",
        )

        assert spec["triggers"][0]["type"] == "KeypadMicromoduleLightTrigger"
        config = json.loads(spec["triggers"][0]["configuration"])
        assert config == {
            "deviceId": "hdm:ZigBee:abc",
            "buttonId": 1,
            "buttonEvent": "PRESS_SHORT",
        }


class TestLightControlPushbuttonBridge:
    """#282: Light Control II has no live Keypad service in practice, so
    eligibility is gated on switch_type == PUSHBUTTON instead of
    has_keypad (has_keypad=True devices are explicitly excluded too, to
    avoid a duplicate entity if that ever does happen)."""

    def test_creates_entries_for_pushbutton_configured_device(self):
        device = _make_light_control_device("lc1", "Licht Flur")
        session = _make_session([], light_control_devices=[device])
        session.async_create_userdefinedstate.side_effect = [
            SimpleNamespace(id=f"u{i}") for i in range(4)
        ]
        session.async_create_automation_rule.side_effect = [
            SimpleNamespace(id=f"a{i}") for i in range(4)
        ]
        entry = _make_entry(session=session)
        hass = _make_hass()

        _run(async_sync_keypad_bridge(hass, entry, enabled=True))

        assert session.async_create_userdefinedstate.await_count == 4
        for call in session.async_create_automation_rule.await_args_list:
            trigger = call.kwargs["triggers"][0]
            assert trigger["type"] == "KeypadMicromoduleLightTrigger"

        sent_data = hass.config_entries.async_update_entry.call_args.kwargs["data"]
        bridge_map = sent_data[DATA_KEYPAD_BRIDGE_MAP]
        for key in (
            f"lc1_1_PRESS_SHORT_{_SCHEMA_VERSION}",
            f"lc1_1_PRESS_LONG_{_SCHEMA_VERSION}",
            f"lc1_2_PRESS_SHORT_{_SCHEMA_VERSION}",
            f"lc1_2_PRESS_LONG_{_SCHEMA_VERSION}",
        ):
            assert key in bridge_map

    def test_skips_device_not_configured_as_pushbutton(self):
        device = _make_light_control_device(
            "lc1", "Licht Flur", switch_type=SwitchConfiguration.SwitchType.SWITCH
        )
        session = _make_session([], light_control_devices=[device])
        entry = _make_entry(session=session)
        hass = _make_hass()

        _run(async_sync_keypad_bridge(hass, entry, enabled=True))

        session.async_create_userdefinedstate.assert_not_awaited()

    def test_skips_device_with_no_switch_type(self):
        device = _make_light_control_device("lc1", "Licht Flur", switch_type=None)
        session = _make_session([], light_control_devices=[device])
        entry = _make_entry(session=session)
        hass = _make_hass()

        _run(async_sync_keypad_bridge(hass, entry, enabled=True))

        session.async_create_userdefinedstate.assert_not_awaited()

    def test_skips_excluded_device(self):
        device = _make_light_control_device("lc1", "Licht Flur")
        session = _make_session([], light_control_devices=[device])
        entry = _make_entry(session=session, options={"excluded_devices": ["lc1"]})
        hass = _make_hass()

        _run(async_sync_keypad_bridge(hass, entry, enabled=True))

        session.async_create_userdefinedstate.assert_not_awaited()

    def test_skips_device_with_live_keypad_service(self):
        """A device that DOES have a live Keypad service already gets a
        working entity from event.py's LightControlButtonEvent -- bridging
        it too would just be a redundant duplicate for the same button."""
        device = _make_light_control_device("lc1", "Licht Flur", has_keypad=True)
        session = _make_session([], light_control_devices=[device])
        entry = _make_entry(session=session)
        hass = _make_hass()

        _run(async_sync_keypad_bridge(hass, entry, enabled=True))

        session.async_create_userdefinedstate.assert_not_awaited()

    def test_idempotent_skips_already_created_entries(self):
        device = _make_light_control_device("lc1", "Already bridged")
        session = _make_session([], light_control_devices=[device])
        entry = _make_entry(
            data={
                DATA_KEYPAD_BRIDGE_MAP: {
                    f"lc1_1_PRESS_SHORT_{_SCHEMA_VERSION}": {
                        "userdefinedstate_id": "u0",
                        "automation_id": "a0",
                    },
                    f"lc1_1_PRESS_LONG_{_SCHEMA_VERSION}": {
                        "userdefinedstate_id": "u1",
                        "automation_id": "a1",
                    },
                    f"lc1_2_PRESS_SHORT_{_SCHEMA_VERSION}": {
                        "userdefinedstate_id": "u2",
                        "automation_id": "a2",
                    },
                    f"lc1_2_PRESS_LONG_{_SCHEMA_VERSION}": {
                        "userdefinedstate_id": "u3",
                        "automation_id": "a3",
                    },
                }
            },
            session=session,
        )
        hass = _make_hass()

        _run(async_sync_keypad_bridge(hass, entry, enabled=True))

        session.async_create_userdefinedstate.assert_not_awaited()
        session.async_create_automation_rule.assert_not_awaited()
        hass.config_entries.async_update_entry.assert_not_called()

    def test_rolls_back_state_when_automation_create_fails(self):
        device = _make_light_control_device("lc1", "Licht Flur")
        session = _make_session([], light_control_devices=[device])
        session.async_create_userdefinedstate.return_value = SimpleNamespace(id="u1")
        session.async_create_automation_rule.side_effect = SHCException("boom")
        entry = _make_entry(session=session)
        hass = _make_hass()

        _run(async_sync_keypad_bridge(hass, entry, enabled=True))

        assert session.async_delete_userdefinedstate.await_count == 4
        hass.config_entries.async_update_entry.assert_not_called()

    def test_shading_and_light_control_devices_together_get_correct_trigger_types(
        self,
    ):
        """A regression guard for the trigger_type threading: a session
        with both device classes present must not cross-wire triggers."""
        shading_device = _make_device("hdm:ZigBee:shutter1", "Rollladen")
        light_device = _make_light_control_device("lc1", "Licht Flur")
        session = _make_session([shading_device], light_control_devices=[light_device])
        session.async_create_userdefinedstate.side_effect = [
            SimpleNamespace(id=f"u{i}") for i in range(8)
        ]
        session.async_create_automation_rule.side_effect = [
            SimpleNamespace(id=f"a{i}") for i in range(8)
        ]
        entry = _make_entry(session=session)
        hass = _make_hass()

        _run(async_sync_keypad_bridge(hass, entry, enabled=True))

        assert session.async_create_userdefinedstate.await_count == 8
        trigger_types_by_device: dict[str, set[str]] = {}
        for call in session.async_create_automation_rule.await_args_list:
            config = json.loads(call.kwargs["triggers"][0]["configuration"])
            trigger_types_by_device.setdefault(config["deviceId"], set()).add(
                call.kwargs["triggers"][0]["type"]
            )
        assert trigger_types_by_device["hdm:ZigBee:shutter1"] == {
            "KeypadMicromoduleShadingTrigger"
        }
        assert trigger_types_by_device["lc1"] == {"KeypadMicromoduleLightTrigger"}


class TestSWD2ButtonBridge:
    """hass#245/#342/#376: Door/Window Contact II (SWD2) has no Keypad
    service but does fire ShutterContactButtonPressTrigger, live-confirmed
    2026-08-06 (bosch-shc-api-docs #10)."""

    def test_creates_one_entry_per_press_type(self):
        device = _make_device("hdm:ZigBee:swd2a", "Speisekammer-Fenster")
        session = _make_session([], swd2_devices=[device])
        session.async_create_userdefinedstate.side_effect = [
            SimpleNamespace(id="u1"),
            SimpleNamespace(id="u2"),
        ]
        session.async_create_automation_rule.side_effect = [
            SimpleNamespace(id="a1"),
            SimpleNamespace(id="a2"),
        ]
        entry = _make_entry(session=session)
        hass = _make_hass()

        _run(async_sync_keypad_bridge(hass, entry, enabled=True))

        assert session.async_create_userdefinedstate.await_count == 2
        assert session.async_create_automation_rule.await_count == 2
        sent_data = hass.config_entries.async_update_entry.call_args.kwargs["data"]
        bridge_map = sent_data[DATA_KEYPAD_BRIDGE_MAP]
        for key in (
            f"hdm:ZigBee:swd2a_swd2_ON_SHORT_PRESS_{_SCHEMA_VERSION}",
            f"hdm:ZigBee:swd2a_swd2_ON_LONG_PRESS_{_SCHEMA_VERSION}",
        ):
            assert key in bridge_map

    def test_skips_excluded_swd2_device(self):
        device = _make_device("d1", "Excluded Window")
        session = _make_session([], swd2_devices=[device])
        entry = _make_entry(session=session, options={"excluded_devices": ["d1"]})
        hass = _make_hass()

        _run(async_sync_keypad_bridge(hass, entry, enabled=True))

        session.async_create_userdefinedstate.assert_not_awaited()

    def test_idempotent_skips_already_created_entries(self):
        device = _make_device("d1", "Already bridged window")
        session = _make_session([], swd2_devices=[device])
        entry = _make_entry(
            data={
                DATA_KEYPAD_BRIDGE_MAP: {
                    f"d1_swd2_ON_SHORT_PRESS_{_SCHEMA_VERSION}": {
                        "userdefinedstate_id": "u1",
                        "automation_id": "a1",
                    },
                    f"d1_swd2_ON_LONG_PRESS_{_SCHEMA_VERSION}": {
                        "userdefinedstate_id": "u2",
                        "automation_id": "a2",
                    },
                }
            },
            session=session,
        )
        hass = _make_hass()

        _run(async_sync_keypad_bridge(hass, entry, enabled=True))

        session.async_create_userdefinedstate.assert_not_awaited()
        session.async_create_automation_rule.assert_not_awaited()
        hass.config_entries.async_update_entry.assert_not_called()

    def test_uds_name_truncation_and_distinctness(self):
        short_name = _swd2_uds_name("Speisekammer-Fenster", "ON_SHORT_PRESS")
        long_name = _swd2_uds_name("Speisekammer-Fenster", "ON_LONG_PRESS")
        assert short_name == "Speisekammer-Fenster BtnS"
        assert long_name == "Speisekammer-Fenster BtnL"
        assert len(short_name) <= 30
        assert len(long_name) <= 30

    def test_automation_body_shape(self):
        """Live-confirmed shape (real button mapped via the official app,
        read back via GET /automation/rules, 2026-08-06): shutterContactId +
        buttonPressState, no buttonId/keyCode/keyName unlike the Keypad-
        service trigger types."""
        spec = _build_swd2_automation(
            "[HA] Test Window Button",
            "hdm:ZigBee:swd2a",
            "ON_SHORT_PRESS",
            "uds-1",
        )

        assert len(spec["triggers"]) == 1
        trigger = spec["triggers"][0]
        assert trigger["type"] == "ShutterContactButtonPressTrigger"
        config = json.loads(trigger["configuration"])
        assert config["shutterContactId"] == "hdm:ZigBee:swd2a"
        assert config["buttonPressState"] == "ON_SHORT_PRESS"
        assert "buttonId" not in config
        assert "keyName" not in config

        assert len(spec["actions"]) == 2
        active_action = next(
            a
            for a in spec["actions"]
            if json.loads(a["configuration"])["state"] == "ACTIVE"
        )
        inactive_action = next(
            a
            for a in spec["actions"]
            if json.loads(a["configuration"])["state"] == "INACTIVE"
        )
        assert active_action["delayInSeconds"] == 0
        assert inactive_action["delayInSeconds"] > 0

    def test_removes_stale_entries_for_now_excluded_swd2_device(self):
        device = _make_device("d1", "Now excluded window")
        session = _make_session([], swd2_devices=[device])
        entry = _make_entry(
            data={
                DATA_KEYPAD_BRIDGE_MAP: {
                    f"d1_swd2_ON_SHORT_PRESS_{_SCHEMA_VERSION}": {
                        "userdefinedstate_id": "u1",
                        "automation_id": "a1",
                    },
                    f"d1_swd2_ON_LONG_PRESS_{_SCHEMA_VERSION}": {
                        "userdefinedstate_id": "u2",
                        "automation_id": "a2",
                    },
                }
            },
            options={"excluded_devices": ["d1"]},
            session=session,
        )
        hass = _make_hass()

        _run(async_sync_keypad_bridge(hass, entry, enabled=True))

        assert session.async_delete_automation_rule.await_count == 2
        assert session.async_delete_userdefinedstate.await_count == 2
        sent_data = hass.config_entries.async_update_entry.call_args.kwargs["data"]
        assert sent_data[DATA_KEYPAD_BRIDGE_MAP] == {}

    def test_rolls_back_state_when_automation_create_fails(self):
        """The shared _create_bridge_entry rollback must also fire on the
        SWD2 path, not just the original Keypad-device path."""
        device = _make_device("d1", "Rollback window")
        session = _make_session([], swd2_devices=[device])
        session.async_create_userdefinedstate.return_value = SimpleNamespace(id="u1")
        session.async_create_automation_rule.side_effect = SHCException("boom")
        entry = _make_entry(session=session)
        hass = _make_hass()

        _run(async_sync_keypad_bridge(hass, entry, enabled=True))

        assert session.async_delete_userdefinedstate.await_count == 2
        hass.config_entries.async_update_entry.assert_not_called()


class TestFailureHandling:
    def test_create_failure_for_one_device_does_not_block_others(self):
        d1 = _make_device("d1", "Fails")
        d2 = _make_device("d2", "Works")
        session = _make_session([d1, d2])

        session.async_create_userdefinedstate.side_effect = [
            SHCException("boom"),
            SHCException("boom"),
            SHCException("boom"),
            SHCException("boom"),
            SimpleNamespace(id="u1"),
            SimpleNamespace(id="u2"),
            SimpleNamespace(id="u3"),
            SimpleNamespace(id="u4"),
        ]
        session.async_create_automation_rule.side_effect = [
            SimpleNamespace(id=f"a{i}") for i in range(4)
        ]
        entry = _make_entry(session=session)
        hass = _make_hass()

        _run(async_sync_keypad_bridge(hass, entry, enabled=True))

        sent_data = hass.config_entries.async_update_entry.call_args.kwargs["data"]
        bridge_map = sent_data[DATA_KEYPAD_BRIDGE_MAP]
        for key in _D1_ALL_KEYS:
            assert key not in bridge_map
        for key in (
            f"d2_1_PRESS_SHORT_{_SCHEMA_VERSION}",
            f"d2_1_PRESS_LONG_{_SCHEMA_VERSION}",
            f"d2_2_PRESS_SHORT_{_SCHEMA_VERSION}",
            f"d2_2_PRESS_LONG_{_SCHEMA_VERSION}",
        ):
            assert key in bridge_map
