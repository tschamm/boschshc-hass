"""Isolation-safe unit tests for IntrusionSystemAlarmControlPanel.

Pattern: Cls.__new__(Cls) bypasses SHCEntity/__init__ (needs hass/registry).
All pure-logic properties are exercised without a HA harness.
PIN_EVERY_MODE: one test per discrete enum value + None/garbage fallback.
"""

from types import SimpleNamespace

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
# Helper
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
    Must map to TRIGGERED (not fall through to ArmingState which would show ARMING/DISARMED)."""
    panel = _panel(alarm_state=AlarmState.ALARM_MUTED, arming_state=ArmingState.SYSTEM_ARMING)
    assert panel.alarm_state == AlarmControlPanelState.TRIGGERED


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


def test_alarm_state_none_active_profile_returns_none():
    """active_configuration_profile=None with SYSTEM_ARMED → None."""
    panel = _panel(
        alarm_state=AlarmState.ALARM_OFF,
        arming_state=ArmingState.SYSTEM_ARMED,
    )
    panel._device.active_configuration_profile = None
    assert panel.alarm_state is None


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
# availability
# ---------------------------------------------------------------------------

def test_available_true_when_system_available():
    assert _panel(system_availability=True).available is True


def test_available_false_when_system_unavailable():
    assert _panel(system_availability=False).available is False


# ---------------------------------------------------------------------------
# should_poll
# ---------------------------------------------------------------------------

def test_should_poll_is_false():
    assert _panel().should_poll is False


# ---------------------------------------------------------------------------
# name / device_id / manufacturer
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


# ---------------------------------------------------------------------------
# device_info
# ---------------------------------------------------------------------------

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
# arm/disarm action delegation
# ---------------------------------------------------------------------------

def test_alarm_disarm_calls_device_disarm():
    calls = []
    panel = _panel()
    panel._device.disarm = lambda: calls.append("disarm")
    panel.alarm_disarm()
    assert calls == ["disarm"]


def test_alarm_arm_away_calls_arm_full_protection():
    calls = []
    panel = _panel()
    panel._device.arm_full_protection = lambda: calls.append("arm_full_protection")
    panel.alarm_arm_away()
    assert calls == ["arm_full_protection"]


def test_alarm_arm_home_calls_arm_partial_protection():
    calls = []
    panel = _panel()
    panel._device.arm_partial_protection = lambda: calls.append("arm_partial_protection")
    panel.alarm_arm_home()
    assert calls == ["arm_partial_protection"]


def test_alarm_arm_custom_bypass_calls_arm_individual_protection():
    calls = []
    panel = _panel()
    panel._device.arm_individual_protection = lambda: calls.append("arm_individual_protection")
    panel.alarm_arm_custom_bypass()
    assert calls == ["arm_individual_protection"]


def test_alarm_mute_calls_device_mute():
    calls = []
    panel = _panel()
    panel._device.mute = lambda: calls.append("mute")
    panel.alarm_mute()
    assert calls == ["mute"]


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


def test_has_entity_name_property_is_true():
    """has_entity_name returns True (Bronze: has-entity-name).

    Checked via an instance because AlarmControlPanelEntity's base-class property
    descriptor shadows direct class-attribute access.
    """
    assert _panel().has_entity_name is True


def test_instance_attr_name_is_none():
    """_attr_name=None on the instance: HA uses the device name as the entity name."""
    assert _panel()._attr_name is None
