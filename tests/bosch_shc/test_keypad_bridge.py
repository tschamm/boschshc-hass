"""Tests for keypad_bridge.py (#395: physical-pushbutton-to-HA bridge)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.bosch_shc.keypad_bridge import (
    DATA_KEYPAD_BRIDGE_MAP,
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


def _make_session(devices):
    session = SimpleNamespace()
    session.device_helper = SimpleNamespace(
        shutter_controls=devices,
        micromodule_shutter_controls=[],
        micromodule_blinds=[],
        micromodule_light_controls=[],
    )
    session.information = SimpleNamespace(macAddress="AA:BB:CC:DD:EE:FF")
    session.async_create_userdefinedstate = AsyncMock()
    session.async_create_automation_rule = AsyncMock()
    session.async_delete_automation_rule = AsyncMock()
    session.async_delete_userdefinedstate = AsyncMock()
    return session


def _run(coro):
    return asyncio.run(coro)


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
                    "d1_1": {"userdefinedstate_id": "u1", "automation_id": "a1"}
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
                    "d1_1": {"userdefinedstate_id": "u1", "automation_id": "a1"}
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
    def test_creates_two_buttons_per_keypad_device(self):
        device = _make_device("hdm:ZigBee:abc", "Rollladen Büro")
        session = _make_session([device])
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
        assert bridge_map["hdm:ZigBee:abc_1"] == {
            "userdefinedstate_id": "u1",
            "automation_id": "a1",
        }
        assert bridge_map["hdm:ZigBee:abc_2"] == {
            "userdefinedstate_id": "u2",
            "automation_id": "a2",
        }

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
        entry = _make_entry(
            session=session, options={"excluded_devices": ["d1"]}
        )
        hass = _make_hass()

        _run(async_sync_keypad_bridge(hass, entry, enabled=True))

        session.async_create_userdefinedstate.assert_not_awaited()

    def test_idempotent_skips_already_created_entries(self):
        device = _make_device("d1", "Already bridged")
        session = _make_session([device])
        entry = _make_entry(
            data={
                DATA_KEYPAD_BRIDGE_MAP: {
                    "d1_1": {"userdefinedstate_id": "u1", "automation_id": "a1"},
                    "d1_2": {"userdefinedstate_id": "u2", "automation_id": "a2"},
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
        """A device excluded after being bridged must have its entry cleaned up."""
        device = _make_device("d1", "Now excluded")
        session = _make_session([device])
        entry = _make_entry(
            data={
                DATA_KEYPAD_BRIDGE_MAP: {
                    "d1_1": {"userdefinedstate_id": "u1", "automation_id": "a1"},
                    "d1_2": {"userdefinedstate_id": "u2", "automation_id": "a2"},
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


class TestUserDefinedStateNameLength:
    def test_long_device_name_is_truncated_to_30_chars(self):
        """UserDefinedState.name is capped at 30 chars by the Controller
        itself -- a longer name gets a bare 400 (live-confirmed, #395)."""
        from custom_components.bosch_shc.keypad_bridge import _uds_name

        name = _uds_name("A Very Long Shutter Device Name Indeed", 1)
        assert len(name) <= 30
        assert name.endswith(" Btn1")

    def test_short_device_name_not_truncated(self):
        from custom_components.bosch_shc.keypad_bridge import _uds_name

        name = _uds_name("Shutter", 2)
        assert name == "Shutter Btn2"

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
        from boschshcpy.exceptions import SHCException

        device = _make_device("d1", "Shutter")
        session = _make_session([device])
        session.async_create_userdefinedstate.return_value = SimpleNamespace(id="u1")
        session.async_create_automation_rule.side_effect = SHCException("boom")
        entry = _make_entry(session=session)
        hass = _make_hass()

        _run(async_sync_keypad_bridge(hass, entry, enabled=True))

        # Rolled back once per (failed) button — 2 keycodes for this device.
        assert session.async_delete_userdefinedstate.await_count == 2
        # Nothing ended up in the map (unchanged from the empty starting
        # point), so no entry-data write was needed at all.
        hass.config_entries.async_update_entry.assert_not_called()


class TestAutomationBodyShape:
    def test_trigger_and_action_configuration_shape(self):
        """The built automation must match the live-confirmed KeypadButtonPressTrigger
        / UserDefinedStateAction envelope (bosch-shc-api-docs #10)."""
        from custom_components.bosch_shc.keypad_bridge import _build_automation

        spec = _build_automation("[HA] Test Button 1", "hdm:ZigBee:abc", 1, "uds-1")

        assert spec["name"] == "[HA] Test Button 1"
        assert len(spec["triggers"]) == 2
        for trigger in spec["triggers"]:
            assert trigger["type"] == "KeypadButtonPressTrigger"
            import json

            config = json.loads(trigger["configuration"])
            assert config["deviceId"] == "hdm:ZigBee:abc"
            assert config["keyCode"] == 1
            assert config["buttonEvent"] in ("PRESS_SHORT", "PRESS_LONG")

        assert len(spec["actions"]) == 2
        import json

        active_action = next(
            a for a in spec["actions"] if json.loads(a["configuration"])["state"] == "ACTIVE"
        )
        inactive_action = next(
            a for a in spec["actions"] if json.loads(a["configuration"])["state"] == "INACTIVE"
        )
        assert active_action["type"] == "UserDefinedStateAction"
        assert active_action["delayInSeconds"] == 0
        assert json.loads(active_action["configuration"])["stateId"] == "uds-1"
        assert inactive_action["delayInSeconds"] > 0


class TestFailureHandling:
    def test_create_failure_for_one_device_does_not_block_others(self):
        d1 = _make_device("d1", "Fails")
        d2 = _make_device("d2", "Works")
        session = _make_session([d1, d2])
        from boschshcpy.exceptions import SHCException

        session.async_create_userdefinedstate.side_effect = [
            SHCException("boom"),
            SHCException("boom"),
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

        sent_data = hass.config_entries.async_update_entry.call_args.kwargs["data"]
        bridge_map = sent_data[DATA_KEYPAD_BRIDGE_MAP]
        assert "d1_1" not in bridge_map
        assert "d1_2" not in bridge_map
        assert "d2_1" in bridge_map
        assert "d2_2" in bridge_map
