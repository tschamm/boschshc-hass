"""Unit tests for alarm_control_panel.py IntrusionSystemAlarmControlPanel.

Covers the alarm_state property (all branches), action methods, and remaining
property getters not exercised by test_alarm_unit.py.

Pattern: __new__ bypass + SimpleNamespace device.
No HA harness.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from boschshcpy import SHCIntrusionSystem
from homeassistant.components.alarm_control_panel.const import (
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
)

from custom_components.bosch_shc.alarm_control_panel import (
    IntrusionSystemAlarmControlPanel,
)

AlarmState = SHCIntrusionSystem.AlarmState
ArmingState = SHCIntrusionSystem.ArmingState
Profile = SHCIntrusionSystem.Profile


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
    """Build an IntrusionSystemAlarmControlPanel via __new__ + injected device."""
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
        disarm=MagicMock(),
        arm_full_protection=MagicMock(),
        arm_partial_protection=MagicMock(),
        arm_individual_protection=MagicMock(),
        mute=MagicMock(),
    )
    panel = IntrusionSystemAlarmControlPanel.__new__(IntrusionSystemAlarmControlPanel)
    panel._device = device
    panel._entry_id = "entry-1"
    panel._attr_unique_id = f"{root_device_id}_{device_id}"
    return panel


# ---------------------------------------------------------------------------
# alarm_state — alarm_state branch (lines 100-105)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# alarm_state — arming_state branch (lines 107-109)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# alarm_state — SYSTEM_ARMED + profile (lines 112-129)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# alarm_state — alarm_state ALARM_OFF falls through to arming_state check
# ---------------------------------------------------------------------------

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
# Action methods (lines 157-175)
# ---------------------------------------------------------------------------

class TestAlarmActions:
    def test_alarm_disarm_calls_device_disarm(self):
        p = _make_panel()
        p.alarm_disarm()
        p._device.disarm.assert_called_once_with()

    def test_alarm_disarm_with_code(self):
        p = _make_panel()
        p.alarm_disarm(code="1234")
        p._device.disarm.assert_called_once_with()

    def test_alarm_arm_away_calls_arm_full_protection(self):
        p = _make_panel()
        p.alarm_arm_away()
        p._device.arm_full_protection.assert_called_once_with()

    def test_alarm_arm_home_calls_arm_partial_protection(self):
        p = _make_panel()
        p.alarm_arm_home()
        p._device.arm_partial_protection.assert_called_once_with()

    def test_alarm_arm_custom_bypass_calls_arm_individual_protection(self):
        p = _make_panel()
        p.alarm_arm_custom_bypass()
        p._device.arm_individual_protection.assert_called_once_with()

    def test_alarm_mute_calls_mute(self):
        p = _make_panel()
        p.alarm_mute()
        p._device.mute.assert_called_once_with()


# ---------------------------------------------------------------------------
# Property getters
# ---------------------------------------------------------------------------

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
        from custom_components.bosch_shc.const import DOMAIN
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
        from custom_components.bosch_shc.const import DOMAIN
        p = _make_panel(root_device_id="root-via")
        assert p.device_info["via_device"] == (DOMAIN, "root-via")
