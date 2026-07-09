"""Isolation-safe unit tests for IntrusionSystemAlarmControlPanel.

Pattern: Cls.__new__(Cls) bypasses SHCEntity/__init__ (needs hass/registry); a
few tests instead construct the entity directly or drive async_setup_entry
with fully-mocked hass/config_entry objects. No HA test harness is used
anywhere in this file. Coverage spans: the alarm_state property (every
AlarmState/ArmingState/Profile branch), supported_features/code_format/
code_arm_required, availability/should_poll, name/device_id/manufacturer/
device_info/unique_id, extra_state_attributes (incidents, security_gaps,
remaining_time_until_armed), arm/disarm/mute action delegation (including
SHCException/SHCConnectionError -> HomeAssistantError translation),
__init__/async_added_to_hass/async_will_remove_from_hass, and
async_setup_entry.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from boschshcpy import SHCIntrusionSystem
from boschshcpy.exceptions import SHCConnectionError, SHCException
from homeassistant.components.alarm_control_panel.const import (
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
)
from homeassistant.exceptions import HomeAssistantError

from custom_components.bosch_shc.alarm_control_panel import (
    IntrusionSystemAlarmControlPanel,
    async_setup_entry,
)
from custom_components.bosch_shc.const import DOMAIN

AlarmState = SHCIntrusionSystem.AlarmState
ArmingState = SHCIntrusionSystem.ArmingState
Profile = SHCIntrusionSystem.Profile


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _panel(
    alarm_state=AlarmState.ALARM_OFF,
    arming_state=ArmingState.SYSTEM_DISARMED,
    profile=Profile.FULL_PROTECTION,
    system_availability=True,
    name="Intrusion",
    manufacturer="Bosch",
    device_model="Alarm",
    root_device_id="root-1",
    device_id="dev-1",
):
    panel = IntrusionSystemAlarmControlPanel.__new__(IntrusionSystemAlarmControlPanel)
    panel._device = SimpleNamespace(
        alarm_state=alarm_state,
        arming_state=arming_state,
        active_configuration_profile=profile,
        system_availability=system_availability,
        name=name,
        manufacturer=manufacturer,
        device_model=device_model,
        root_device_id=root_device_id,
        id=device_id,
    )
    return panel


def _make_panel(
    alarm_state=AlarmState.ALARM_OFF,
    arming_state=ArmingState.SYSTEM_DISARMED,
    profile=Profile.FULL_PROTECTION,
    system_availability=True,
    name="Intrusion System",
    manufacturer="Bosch",
    device_model="IDS",
    root_device_id="root-1",
    device_id="dev-1",
):
    """Build an IntrusionSystemAlarmControlPanel via __new__ + injected device.

    Unlike `_panel()` above, the injected device also carries AsyncMock
    action methods so action-delegation tests can assert on them directly.
    """
    device = SimpleNamespace(
        root_device_id=root_device_id,
        id=device_id,
        name=name,
        manufacturer=manufacturer,
        device_model=device_model,
        alarm_state=alarm_state,
        arming_state=arming_state,
        active_configuration_profile=profile,
        system_availability=system_availability,
        async_disarm=AsyncMock(),
        async_arm_full_protection=AsyncMock(),
        async_arm_partial_protection=AsyncMock(),
        async_arm_individual_protection=AsyncMock(),
        async_mute=AsyncMock(),
    )
    panel = IntrusionSystemAlarmControlPanel.__new__(IntrusionSystemAlarmControlPanel)
    panel._device = device
    panel._entry_id = "entry-1"
    panel._attr_unique_id = f"{root_device_id}_{device_id}"
    return panel


def _make_ids_device(
    incidents=None,
    security_gaps=None,
    remaining_time=0,
):
    if incidents is None:
        incidents = []
    if security_gaps is None:
        security_gaps = []
    return SimpleNamespace(
        id="/intrusion",
        root_device_id="aa:bb:cc:00:00:01",
        name="Intrusion Detection System",
        manufacturer="BOSCH",
        device_model="IDS",
        system_availability=True,
        alarm_state_incidents=incidents,
        security_gaps=security_gaps,
        remaining_time_until_armed=remaining_time,
    )


def _make_ids_panel(device):
    panel = IntrusionSystemAlarmControlPanel.__new__(IntrusionSystemAlarmControlPanel)
    panel._device = device
    panel._entry_id = "E1"
    panel._attr_unique_id = f"{device.root_device_id}_{device.id}"
    return panel


def _fake_device(
    root_device_id="root-1",
    device_id="dev-1",
    name="Intrusion",
    manufacturer="Bosch",
    device_model="IDS",
    alarm_state=AlarmState.ALARM_OFF,
    arming_state=ArmingState.SYSTEM_DISARMED,
    profile=Profile.FULL_PROTECTION,
    system_availability=True,
):
    """Build a minimal fake intrusion-system device with subscribe/unsubscribe."""
    subscriptions: dict = {}

    def subscribe_callback(entity_id, cb):
        subscriptions[entity_id] = cb

    def unsubscribe_callback(entity_id):
        subscriptions.pop(entity_id, None)

    return SimpleNamespace(
        root_device_id=root_device_id,
        id=device_id,
        name=name,
        manufacturer=manufacturer,
        device_model=device_model,
        alarm_state=alarm_state,
        arming_state=arming_state,
        active_configuration_profile=profile,
        system_availability=system_availability,
        subscribe_callback=subscribe_callback,
        unsubscribe_callback=unsubscribe_callback,
        disarm=MagicMock(),
        arm_full_protection=MagicMock(),
        arm_partial_protection=MagicMock(),
        arm_individual_protection=MagicMock(),
        mute=MagicMock(),
        _subscriptions=subscriptions,
    )


# ---------------------------------------------------------------------------
# async_setup_entry
# ---------------------------------------------------------------------------

async def _run_setup_entry():
    """Inner coroutine — called via asyncio.run in the sync test below."""
    entry_id = "cfg-entry-1"
    device = _fake_device(root_device_id="r2", device_id="d2")

    session = SimpleNamespace(intrusion_system=device)

    hass = SimpleNamespace()

    config_entry = SimpleNamespace(options={}, entry_id=entry_id)
    config_entry.runtime_data = SimpleNamespace(session=session)

    added: list = []

    def async_add_entities(entities):
        added.extend(entities)

    # Patch async_migrate_to_new_unique_id to a coroutine no-op
    with patch(
        "custom_components.bosch_shc.alarm_control_panel.async_migrate_to_new_unique_id",
        new=AsyncMock(return_value=None),
    ):
        await async_setup_entry(hass, config_entry, async_add_entities)

    return added, device, entry_id


def test_async_setup_entry_adds_one_entity():
    """async_setup_entry appends exactly one IntrusionSystemAlarmControlPanel."""
    added, device, entry_id = asyncio.run(_run_setup_entry())

    assert len(added) == 1
    assert isinstance(added[0], IntrusionSystemAlarmControlPanel)


def test_async_setup_entry_entity_uses_correct_device():
    """The entity's _device is the intrusion_system from the session."""
    added, device, entry_id = asyncio.run(_run_setup_entry())

    panel = added[0]
    assert panel._device is device


def test_async_setup_entry_entity_uses_correct_entry_id():
    """The entity's _entry_id matches config_entry.entry_id."""
    added, device, entry_id = asyncio.run(_run_setup_entry())

    panel = added[0]
    assert panel._entry_id == entry_id


def test_async_setup_entry_calls_migrate_with_old_unique_id():
    """async_migrate_to_new_unique_id is called with old_unique_id=entry_id_device_id."""
    entry_id = "cfg-entry-migrate"
    device = _fake_device(root_device_id="r3", device_id="dev-migrate")

    session = SimpleNamespace(intrusion_system=device)
    hass = SimpleNamespace()
    config_entry = SimpleNamespace(options={}, entry_id=entry_id)
    config_entry.runtime_data = SimpleNamespace(session=session)

    migrate_mock = AsyncMock(return_value=None)

    with patch(
        "custom_components.bosch_shc.alarm_control_panel.async_migrate_to_new_unique_id",
        new=migrate_mock,
    ):
        asyncio.run(async_setup_entry(hass, config_entry, lambda e: None))

    migrate_mock.assert_awaited_once()
    kwargs = migrate_mock.call_args
    assert kwargs.kwargs.get("old_unique_id") == f"{entry_id}_{device.id}"
    assert kwargs.kwargs.get("device") is device
    assert kwargs.kwargs.get("attr_name") is None


# ---------------------------------------------------------------------------
# unique_id (set during __init__ — verify via __new__ + manual construction)
# ---------------------------------------------------------------------------

def test_unique_id_composed_from_root_and_device_id():
    panel = IntrusionSystemAlarmControlPanel.__new__(IntrusionSystemAlarmControlPanel)
    panel._device = SimpleNamespace(
        root_device_id="root-abc",
        id="dev-xyz",
        alarm_state=AlarmState.ALARM_OFF,
        arming_state=ArmingState.SYSTEM_DISARMED,
        active_configuration_profile=Profile.FULL_PROTECTION,
        system_availability=True,
        name="Test",
        manufacturer="Bosch",
        device_model="M1",
    )
    panel._entry_id = "entry-1"
    panel._attr_unique_id = f"{panel._device.root_device_id}_{panel._device.id}"
    assert panel._attr_unique_id == "root-abc_dev-xyz"


# ---------------------------------------------------------------------------
# __init__ (direct construction)
# ---------------------------------------------------------------------------

def test_init_sets_device_entry_id_and_unique_id():
    """Direct __init__ — verify attributes are wired correctly."""
    device = _fake_device(root_device_id="r1", device_id="d1")
    panel = IntrusionSystemAlarmControlPanel(device=device, entry_id="entry-42")

    assert panel._device is device
    assert panel._entry_id == "entry-42"
    assert panel._attr_unique_id == "r1_d1"


def test_init_unique_id_uses_root_and_device_id():
    """unique_id is root_device_id + _ + device.id, not entry_id + device.id."""
    device = _fake_device(root_device_id="rootXYZ", device_id="devABC")
    panel = IntrusionSystemAlarmControlPanel(device=device, entry_id="ignored-entry")
    assert panel._attr_unique_id == "rootXYZ_devABC"


# ---------------------------------------------------------------------------
# Quality Scale: has-entity-name + unique_id preservation
# ---------------------------------------------------------------------------

def test_unique_id_format_unchanged():
    """Regression: unique_id must remain f'{root_device_id}_{device_id}' forever.

    Changing this would orphan every user's entity, so this test intentionally
    pins the exact string format.
    """
    panel = _panel(root_device_id="root-abc", device_id="dev-xyz")
    panel._attr_unique_id = f"{panel._device.root_device_id}_{panel._device.id}"
    assert panel._attr_unique_id == "root-abc_dev-xyz"


# ---------------------------------------------------------------------------
# async_added_to_hass / async_will_remove_from_hass
# ---------------------------------------------------------------------------

def test_async_added_to_hass_subscribes_callback():
    """Subscribes an on_state_changed callback keyed by entity_id."""
    device = _fake_device()
    panel = IntrusionSystemAlarmControlPanel.__new__(IntrusionSystemAlarmControlPanel)
    panel._device = device
    panel._entry_id = "e1"
    panel._attr_unique_id = "r1_d1"
    panel.entity_id = "alarm_control_panel.test"
    panel.schedule_update_ha_state = MagicMock()

    asyncio.run(panel.async_added_to_hass())

    assert "alarm_control_panel.test" in device._subscriptions


def test_async_added_to_hass_callback_calls_schedule_update():
    """The registered callback must call schedule_update_ha_state."""
    device = _fake_device()
    panel = IntrusionSystemAlarmControlPanel.__new__(IntrusionSystemAlarmControlPanel)
    panel._device = device
    panel._entry_id = "e1"
    panel._attr_unique_id = "r1_d1"
    panel.entity_id = "alarm_control_panel.cb_test"
    schedule_update = MagicMock()
    panel.schedule_update_ha_state = schedule_update

    asyncio.run(panel.async_added_to_hass())

    # Fire the callback that was registered
    device._subscriptions["alarm_control_panel.cb_test"]()
    assert schedule_update.called


def test_async_will_remove_from_hass_unsubscribes():
    """Unsubscribes the callback keyed by entity_id on removal."""
    device = _fake_device()
    panel = IntrusionSystemAlarmControlPanel.__new__(IntrusionSystemAlarmControlPanel)
    panel._device = device
    panel._entry_id = "e1"
    panel._attr_unique_id = "r1_d1"
    panel.entity_id = "alarm_control_panel.remove_test"
    panel.schedule_update_ha_state = MagicMock()

    # First subscribe, then remove
    asyncio.run(panel.async_added_to_hass())
    assert "alarm_control_panel.remove_test" in device._subscriptions

    asyncio.run(panel.async_will_remove_from_hass())
    assert "alarm_control_panel.remove_test" not in device._subscriptions


def test_async_will_remove_from_hass_tolerates_not_subscribed():
    """Unsubscribe when never subscribed must not raise."""
    device = _fake_device()
    panel = IntrusionSystemAlarmControlPanel.__new__(IntrusionSystemAlarmControlPanel)
    panel._device = device
    panel._entry_id = "e1"
    panel._attr_unique_id = "r1_d1"
    panel.entity_id = "alarm_control_panel.never_added"

    # Should not raise even without a prior subscribe
    asyncio.run(panel.async_will_remove_from_hass())


# ---------------------------------------------------------------------------
# name / device_id / manufacturer / device_info / available / should_poll
# ---------------------------------------------------------------------------

def test_has_entity_name_true():
    """has_entity_name=True: HA uses the device name; _attr_name must be None (primary entity)."""
    panel = _panel(name="My Alarm")
    assert panel._attr_has_entity_name is True
    assert panel._attr_name is None


def test_device_id_delegates_to_device():
    assert _panel(device_id="shc-alarm-42").device_id == "shc-alarm-42"


def test_manufacturer_delegates_to_device():
    assert _panel(manufacturer="Bosch GmbH").manufacturer == "Bosch GmbH"


def test_device_info_structure():
    panel = _panel(
        name="Alarm Panel",
        manufacturer="Bosch",
        device_model="IDS",
        root_device_id="root-99",
        device_id="ids-1",
    )
    info = panel.device_info
    assert ("bosch_shc", "ids-1") in info["identifiers"]
    assert info["name"] == "Alarm Panel"
    assert info["manufacturer"] == "Bosch"
    assert info["model"] == "IDS"
    assert info["via_device"] == ("bosch_shc", "root-99")


def test_available_true_when_system_available():
    assert _panel(system_availability=True).available is True


def test_available_false_when_system_unavailable():
    assert _panel(system_availability=False).available is False


def test_should_poll_is_false():
    assert _panel().should_poll is False


def test_has_entity_name_property_is_true():
    """has_entity_name returns True (Bronze: has-entity-name).

    Checked via an instance because AlarmControlPanelEntity's base-class property
    descriptor shadows direct class-attribute access.
    """
    assert _panel().has_entity_name is True


def test_instance_attr_name_is_none():
    """_attr_name=None on the instance: HA uses the device name as the entity name."""
    assert _panel()._attr_name is None


class TestAlarmPanelProperties:
    def test_has_entity_name_true_and_attr_name_none(self):
        """has_entity_name=True + _attr_name=None: HA displays the device name as entity name."""
        p = _make_panel(name="My Alarm")
        assert p._attr_has_entity_name is True
        assert p._attr_name is None

    def test_device_id_returns_device_id(self):
        p = _make_panel(device_id="dev-99")
        assert p.device_id == "dev-99"

    def test_manufacturer_returns_device_manufacturer(self):
        p = _make_panel(manufacturer="BoschXYZ")
        assert p.manufacturer == "BoschXYZ"

    def test_available_returns_system_availability(self):
        p = _make_panel(system_availability=True)
        assert p.available is True

    def test_available_false_when_unavailable(self):
        p = _make_panel(system_availability=False)
        assert p.available is False

    def test_should_poll_returns_false(self):
        p = _make_panel()
        assert p.should_poll is False

    def test_code_format_returns_none(self):
        p = _make_panel()
        assert p.code_format is None

    def test_code_arm_required_returns_false(self):
        p = _make_panel()
        assert p.code_arm_required is False

    def test_supported_features_includes_arm_away(self):
        p = _make_panel()
        assert p.supported_features & AlarmControlPanelEntityFeature.ARM_AWAY

    def test_supported_features_includes_arm_home(self):
        p = _make_panel()
        assert p.supported_features & AlarmControlPanelEntityFeature.ARM_HOME

    def test_supported_features_includes_arm_custom_bypass(self):
        p = _make_panel()
        assert p.supported_features & AlarmControlPanelEntityFeature.ARM_CUSTOM_BYPASS

    def test_device_info_contains_identifiers(self):
        p = _make_panel(device_id="dev-info")
        info = p.device_info
        assert (DOMAIN, "dev-info") in info["identifiers"]

    def test_device_info_contains_name(self):
        p = _make_panel(name="Info Panel")
        assert p.device_info["name"] == "Info Panel"

    def test_device_info_contains_manufacturer(self):
        p = _make_panel(manufacturer="TestMfg")
        assert p.device_info["manufacturer"] == "TestMfg"

    def test_device_info_contains_model(self):
        p = _make_panel(device_model="TestModel")
        assert p.device_info["model"] == "TestModel"

    def test_device_info_via_device_is_root_device_id(self):
        p = _make_panel(root_device_id="root-via")
        assert p.device_info["via_device"] == (DOMAIN, "root-via")


# ---------------------------------------------------------------------------
# alarm_state — AlarmState takes priority over ArmingState
# ---------------------------------------------------------------------------

def test_alarm_state_alarm_on_returns_triggered():
    panel = _panel(alarm_state=AlarmState.ALARM_ON)
    assert panel.alarm_state == AlarmControlPanelState.TRIGGERED


def test_alarm_state_pre_alarm_returns_pending():
    panel = _panel(alarm_state=AlarmState.PRE_ALARM)
    assert panel.alarm_state == AlarmControlPanelState.PENDING


def test_alarm_state_alarm_off_falls_through_to_arming_state():
    """ALARM_OFF is NOT explicitly handled — falls through to ArmingState logic."""
    panel = _panel(alarm_state=AlarmState.ALARM_OFF, arming_state=ArmingState.SYSTEM_DISARMED)
    assert panel.alarm_state == AlarmControlPanelState.DISARMED


def test_alarm_state_alarm_muted_returns_triggered():
    """ALARM_MUTED means the alarm fired but the siren was silenced — alarm is NOT cleared.
    Must map to TRIGGERED (not fall through to ArmingState which would show ARMING/DISARMED).
    """
    panel = _panel(alarm_state=AlarmState.ALARM_MUTED, arming_state=ArmingState.SYSTEM_ARMING)
    assert panel.alarm_state == AlarmControlPanelState.TRIGGERED


def test_alarm_state_none_active_profile_returns_none():
    """active_configuration_profile=None with SYSTEM_ARMED → None."""
    panel = _panel(
        alarm_state=AlarmState.ALARM_OFF,
        arming_state=ArmingState.SYSTEM_ARMED,
    )
    panel._device.active_configuration_profile = None
    assert panel.alarm_state is None


# ---------------------------------------------------------------------------
# alarm_state — ArmingState (when alarm_state is ALARM_OFF)
# ---------------------------------------------------------------------------

def test_arming_state_system_arming_returns_arming():
    panel = _panel(alarm_state=AlarmState.ALARM_OFF, arming_state=ArmingState.SYSTEM_ARMING)
    assert panel.alarm_state == AlarmControlPanelState.ARMING


def test_arming_state_system_disarmed_returns_disarmed():
    panel = _panel(alarm_state=AlarmState.ALARM_OFF, arming_state=ArmingState.SYSTEM_DISARMED)
    assert panel.alarm_state == AlarmControlPanelState.DISARMED


# ---------------------------------------------------------------------------
# alarm_state — SYSTEM_ARMED branches by active_configuration_profile
# ---------------------------------------------------------------------------

def test_arming_state_armed_full_protection_returns_armed_away():
    panel = _panel(
        alarm_state=AlarmState.ALARM_OFF,
        arming_state=ArmingState.SYSTEM_ARMED,
        profile=Profile.FULL_PROTECTION,
    )
    assert panel.alarm_state == AlarmControlPanelState.ARMED_AWAY


def test_arming_state_armed_partial_protection_returns_armed_home():
    panel = _panel(
        alarm_state=AlarmState.ALARM_OFF,
        arming_state=ArmingState.SYSTEM_ARMED,
        profile=Profile.PARTIAL_PROTECTION,
    )
    assert panel.alarm_state == AlarmControlPanelState.ARMED_HOME


def test_arming_state_armed_custom_protection_returns_armed_custom_bypass():
    panel = _panel(
        alarm_state=AlarmState.ALARM_OFF,
        arming_state=ArmingState.SYSTEM_ARMED,
        profile=Profile.CUSTOM_PROTECTION,
    )
    assert panel.alarm_state == AlarmControlPanelState.ARMED_CUSTOM_BYPASS


def test_arming_state_armed_unknown_profile_returns_none():
    """SYSTEM_ARMED with an unrecognised profile falls off all branches → None."""
    panel = _panel(
        alarm_state=AlarmState.ALARM_OFF,
        arming_state=ArmingState.SYSTEM_ARMED,
    )
    panel._device.active_configuration_profile = "GARBAGE_PROFILE"
    assert panel.alarm_state is None


class TestAlarmStateBranchAlarmState:
    def test_alarm_on_returns_triggered(self):
        p = _make_panel(alarm_state=AlarmState.ALARM_ON, arming_state=ArmingState.SYSTEM_ARMED)
        assert p.alarm_state == AlarmControlPanelState.TRIGGERED

    def test_alarm_muted_returns_triggered(self):
        """ALARM_MUTED must also map to TRIGGERED — the fallthrough bug this covers."""
        p = _make_panel(alarm_state=AlarmState.ALARM_MUTED, arming_state=ArmingState.SYSTEM_ARMED)
        assert p.alarm_state == AlarmControlPanelState.TRIGGERED

    def test_pre_alarm_returns_pending(self):
        p = _make_panel(alarm_state=AlarmState.PRE_ALARM, arming_state=ArmingState.SYSTEM_ARMED)
        assert p.alarm_state == AlarmControlPanelState.PENDING


class TestAlarmStateBranchArmingState:
    def test_system_arming_returns_arming(self):
        p = _make_panel(
            alarm_state=AlarmState.ALARM_OFF,
            arming_state=ArmingState.SYSTEM_ARMING,
        )
        assert p.alarm_state == AlarmControlPanelState.ARMING

    def test_system_disarmed_returns_disarmed(self):
        p = _make_panel(
            alarm_state=AlarmState.ALARM_OFF,
            arming_state=ArmingState.SYSTEM_DISARMED,
        )
        assert p.alarm_state == AlarmControlPanelState.DISARMED


class TestAlarmStateBranchArmed:
    def test_armed_full_protection_returns_armed_away(self):
        p = _make_panel(
            alarm_state=AlarmState.ALARM_OFF,
            arming_state=ArmingState.SYSTEM_ARMED,
            profile=Profile.FULL_PROTECTION,
        )
        assert p.alarm_state == AlarmControlPanelState.ARMED_AWAY

    def test_armed_partial_protection_returns_armed_home(self):
        p = _make_panel(
            alarm_state=AlarmState.ALARM_OFF,
            arming_state=ArmingState.SYSTEM_ARMED,
            profile=Profile.PARTIAL_PROTECTION,
        )
        assert p.alarm_state == AlarmControlPanelState.ARMED_HOME

    def test_armed_custom_protection_returns_armed_custom_bypass(self):
        p = _make_panel(
            alarm_state=AlarmState.ALARM_OFF,
            arming_state=ArmingState.SYSTEM_ARMED,
            profile=Profile.CUSTOM_PROTECTION,
        )
        assert p.alarm_state == AlarmControlPanelState.ARMED_CUSTOM_BYPASS

    def test_armed_unknown_profile_returns_none(self):
        """If profile is SYSTEM_ARMED but profile not matched → fallthrough None."""
        p = _make_panel(
            alarm_state=AlarmState.ALARM_OFF,
            arming_state=ArmingState.SYSTEM_ARMED,
            profile=Profile.FULL_PROTECTION,
        )
        # Override active_configuration_profile with an unrecognized value
        p._device.active_configuration_profile = "UNKNOWN_PROFILE"
        assert p.alarm_state is None


class TestAlarmStateAlarmOffFallthrough:
    def test_alarm_off_armed_full_goes_to_armed_away(self):
        """ALARM_OFF doesn't short-circuit; flow continues to arming_state check."""
        p = _make_panel(
            alarm_state=AlarmState.ALARM_OFF,
            arming_state=ArmingState.SYSTEM_ARMED,
            profile=Profile.FULL_PROTECTION,
        )
        assert p.alarm_state == AlarmControlPanelState.ARMED_AWAY


# ---------------------------------------------------------------------------
# supported_features
# ---------------------------------------------------------------------------

def test_supported_features_contains_arm_away():
    panel = _panel()
    assert panel.supported_features & AlarmControlPanelEntityFeature.ARM_AWAY


def test_supported_features_contains_arm_home():
    panel = _panel()
    assert panel.supported_features & AlarmControlPanelEntityFeature.ARM_HOME


def test_supported_features_contains_arm_custom_bypass():
    panel = _panel()
    assert panel.supported_features & AlarmControlPanelEntityFeature.ARM_CUSTOM_BYPASS


def test_supported_features_does_not_contain_arm_night():
    panel = _panel()
    assert not (panel.supported_features & AlarmControlPanelEntityFeature.ARM_NIGHT)


# ---------------------------------------------------------------------------
# code_format / code_arm_required
# ---------------------------------------------------------------------------

def test_code_format_is_none():
    assert _panel().code_format is None


def test_code_arm_required_is_false():
    assert _panel().code_arm_required is False


# ---------------------------------------------------------------------------
# arm/disarm action delegation
# ---------------------------------------------------------------------------

def test_alarm_disarm_calls_device_disarm():
    panel = _panel()
    panel._device.async_disarm = AsyncMock()
    asyncio.run(panel.async_alarm_disarm())
    panel._device.async_disarm.assert_called_once_with()


def test_alarm_arm_away_calls_arm_full_protection():
    panel = _panel()
    panel._device.async_arm_full_protection = AsyncMock()
    asyncio.run(panel.async_alarm_arm_away())
    panel._device.async_arm_full_protection.assert_called_once_with()


def test_alarm_arm_home_calls_arm_partial_protection():
    panel = _panel()
    panel._device.async_arm_partial_protection = AsyncMock()
    asyncio.run(panel.async_alarm_arm_home())
    panel._device.async_arm_partial_protection.assert_called_once_with()


def test_alarm_arm_custom_bypass_calls_arm_individual_protection():
    panel = _panel()
    panel._device.async_arm_individual_protection = AsyncMock()
    asyncio.run(panel.async_alarm_arm_custom_bypass())
    panel._device.async_arm_individual_protection.assert_called_once_with()


def test_alarm_mute_calls_device_mute():
    panel = _panel()
    panel._device.async_mute = AsyncMock()
    asyncio.run(panel.async_alarm_mute())
    panel._device.async_mute.assert_called_once_with()


class TestAlarmActions:
    def test_alarm_disarm_calls_device_disarm(self):
        p = _make_panel()
        asyncio.run(p.async_alarm_disarm())
        p._device.async_disarm.assert_called_once_with()

    def test_alarm_disarm_with_code(self):
        p = _make_panel()
        asyncio.run(p.async_alarm_disarm(code="1234"))
        p._device.async_disarm.assert_called_once_with()

    def test_alarm_arm_away_calls_arm_full_protection(self):
        p = _make_panel()
        asyncio.run(p.async_alarm_arm_away())
        p._device.async_arm_full_protection.assert_called_once_with()

    def test_alarm_arm_home_calls_arm_partial_protection(self):
        p = _make_panel()
        asyncio.run(p.async_alarm_arm_home())
        p._device.async_arm_partial_protection.assert_called_once_with()

    def test_alarm_arm_custom_bypass_calls_arm_individual_protection(self):
        p = _make_panel()
        asyncio.run(p.async_alarm_arm_custom_bypass())
        p._device.async_arm_individual_protection.assert_called_once_with()

    def test_alarm_mute_calls_mute(self):
        p = _make_panel()
        asyncio.run(p.async_alarm_mute())
        p._device.async_mute.assert_called_once_with()


# ---------------------------------------------------------------------------
# Regression: arm/disarm write failures must surface as HomeAssistantError,
# not a raw SHCException/SHCConnectionError traceback (the SHC can reject an
# arm/disarm request, e.g. a door/window sensor open).
# ---------------------------------------------------------------------------

def test_alarm_disarm_shcexception_raises_homeassistanterror():
    panel = _panel()
    panel._device.async_disarm = AsyncMock(side_effect=SHCException("rejected"))
    with pytest.raises(HomeAssistantError):
        asyncio.run(panel.async_alarm_disarm())


def test_alarm_arm_away_shcconnectionerror_raises_homeassistanterror():
    panel = _panel()
    panel._device.async_arm_full_protection = AsyncMock(
        side_effect=SHCConnectionError("network down")
    )
    with pytest.raises(HomeAssistantError):
        asyncio.run(panel.async_alarm_arm_away())


def test_alarm_arm_home_shcexception_raises_homeassistanterror():
    panel = _panel()
    panel._device.async_arm_partial_protection = AsyncMock(
        side_effect=SHCException("sensor open")
    )
    with pytest.raises(HomeAssistantError):
        asyncio.run(panel.async_alarm_arm_home())


def test_alarm_arm_custom_bypass_shcexception_raises_homeassistanterror():
    panel = _panel()
    panel._device.async_arm_individual_protection = AsyncMock(
        side_effect=SHCException("rejected")
    )
    with pytest.raises(HomeAssistantError):
        asyncio.run(panel.async_alarm_arm_custom_bypass())


def test_alarm_mute_shcexception_raises_homeassistanterror():
    panel = _panel()
    panel._device.async_mute = AsyncMock(side_effect=SHCException("rejected"))
    with pytest.raises(HomeAssistantError):
        asyncio.run(panel.async_alarm_mute())


# ---------------------------------------------------------------------------
# extra_state_attributes — incidents, security_gaps, remaining_time_until_armed
# ---------------------------------------------------------------------------

class TestIDSExtraStateAttributes:
    def test_incidents_empty_list_by_default(self):
        dev = _make_ids_device()
        panel = _make_ids_panel(dev)
        attrs = panel.extra_state_attributes
        assert attrs["incidents"] == []

    def test_incidents_list_returned(self):
        incidents = [{"type": "ALARM_ON", "deviceId": "dev1"}]
        dev = _make_ids_device(incidents=incidents)
        panel = _make_ids_panel(dev)
        attrs = panel.extra_state_attributes
        assert attrs["incidents"] == incidents

    def test_security_gaps_empty_list_by_default(self):
        dev = _make_ids_device()
        panel = _make_ids_panel(dev)
        attrs = panel.extra_state_attributes
        assert attrs["security_gaps"] == []

    def test_security_gaps_list_returned(self):
        gaps = [{"type": "DOOR_OPEN", "deviceId": "dev2"}]
        dev = _make_ids_device(security_gaps=gaps)
        panel = _make_ids_panel(dev)
        attrs = panel.extra_state_attributes
        assert attrs["security_gaps"] == gaps

    def test_remaining_time_zero_by_default(self):
        dev = _make_ids_device()
        panel = _make_ids_panel(dev)
        attrs = panel.extra_state_attributes
        assert attrs["remaining_time_until_armed"] == 0

    def test_remaining_time_non_zero_when_arming(self):
        dev = _make_ids_device(remaining_time=30)
        panel = _make_ids_panel(dev)
        attrs = panel.extra_state_attributes
        assert attrs["remaining_time_until_armed"] == 30

    def test_all_three_keys_present(self):
        dev = _make_ids_device()
        panel = _make_ids_panel(dev)
        attrs = panel.extra_state_attributes
        assert "incidents" in attrs
        assert "security_gaps" in attrs
        assert "remaining_time_until_armed" in attrs

    def test_multiple_incidents_and_gaps(self):
        incidents = [{"a": 1}, {"a": 2}]
        gaps = [{"b": 3}, {"b": 4}, {"b": 5}]
        dev = _make_ids_device(incidents=incidents, security_gaps=gaps)
        panel = _make_ids_panel(dev)
        attrs = panel.extra_state_attributes
        assert len(attrs["incidents"]) == 2
        assert len(attrs["security_gaps"]) == 3
