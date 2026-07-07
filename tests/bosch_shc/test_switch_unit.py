"""Comprehensive unit tests for SHCSwitch — harness-free.

All tests bypass SHCEntity.__init__ via SHCSwitch.__new__ + fake device.
No HA, no tests.common. Run with:
  PYTHONPATH=...:boschshcpy PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
  python3 -m pytest tests/bosch_shc/test_switch_unit.py -q -o addopts= -p no:cacheprovider
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from boschshcpy import (
    BypassService,
    CameraAmbientLightService,
    CameraFrontLightService,
    CameraLightService,
    CameraNotificationService,
    PowerSwitchService,
    PrivacyModeService,
    RoutingService,
    SilentModeService,
    ThermostatService,
)
from boschshcpy.exceptions import SHCConnectionError, SHCException
from homeassistant.exceptions import HomeAssistantError

from custom_components.bosch_shc.switch import SWITCH_TYPES, SHCSwitch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_switch(description, **device_attrs):
    """Build a bare SHCSwitch (bypassing SHCEntity.__init__) with a fake device."""
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = SimpleNamespace(**device_attrs)
    sw.entity_description = description
    sw.entity_id = "switch.test"
    return sw


def _raising_property(exc_type=AttributeError):
    """Descriptor that raises on get and set."""

    class _Raiser:
        def __get__(self, obj, objtype=None):
            raise exc_type("service is None")

        def __set__(self, obj, value):
            raise exc_type("service is None")

    return _Raiser()


def _async_spy_device(on_key: str):
    """Return (device, mock) where device.async_set_<on_key> is an AsyncMock."""
    mock = AsyncMock()
    device = SimpleNamespace(**{f"async_set_{on_key}": mock})
    return device, mock


# ---------------------------------------------------------------------------
# SmartPlug
# ---------------------------------------------------------------------------


def test_smartplug_is_on_true():
    State = PowerSwitchService.State
    sw = _make_switch(SWITCH_TYPES["smartplug"], switchstate=State.ON)
    assert sw.is_on is True


def test_smartplug_is_on_false():
    State = PowerSwitchService.State
    sw = _make_switch(SWITCH_TYPES["smartplug"], switchstate=State.OFF)
    assert sw.is_on is False


def test_smartplug_routing_is_on_enabled():
    State = RoutingService.State
    sw = _make_switch(SWITCH_TYPES["smartplug_routing"], routing=State.ENABLED)
    assert sw.is_on is True


def test_smartplug_routing_is_on_disabled():
    State = RoutingService.State
    sw = _make_switch(SWITCH_TYPES["smartplug_routing"], routing=State.DISABLED)
    assert sw.is_on is False


# ---------------------------------------------------------------------------
# SmartPlugCompact
# ---------------------------------------------------------------------------


def test_smartplugcompact_is_on_true():
    State = PowerSwitchService.State
    sw = _make_switch(SWITCH_TYPES["smartplugcompact"], switchstate=State.ON)
    assert sw.is_on is True


def test_smartplugcompact_is_on_false():
    State = PowerSwitchService.State
    sw = _make_switch(SWITCH_TYPES["smartplugcompact"], switchstate=State.OFF)
    assert sw.is_on is False


# ---------------------------------------------------------------------------
# MicromoduleRelay
# ---------------------------------------------------------------------------


def test_micromodule_relay_is_on_true():
    State = PowerSwitchService.State
    sw = _make_switch(SWITCH_TYPES["micromodule_relay_switch"], switchstate=State.ON)
    assert sw.is_on is True


def test_micromodule_relay_is_on_false():
    State = PowerSwitchService.State
    sw = _make_switch(SWITCH_TYPES["micromodule_relay_switch"], switchstate=State.OFF)
    assert sw.is_on is False


# ---------------------------------------------------------------------------
# LightSwitch
# ---------------------------------------------------------------------------


def test_lightswitch_is_on_true():
    State = PowerSwitchService.State
    sw = _make_switch(SWITCH_TYPES["lightswitch"], switchstate=State.ON)
    assert sw.is_on is True


def test_lightswitch_is_on_false():
    State = PowerSwitchService.State
    sw = _make_switch(SWITCH_TYPES["lightswitch"], switchstate=State.OFF)
    assert sw.is_on is False


# ---------------------------------------------------------------------------
# CameraEyes — privacy (on = privacy DISABLED = camera on)
# ---------------------------------------------------------------------------


def test_cameraeyes_privacy_on():
    """Privacy DISABLED → camera is ON → is_on True."""
    State = PrivacyModeService.State
    sw = _make_switch(SWITCH_TYPES["cameraeyes"], privacymode=State.DISABLED)
    assert sw.is_on is True


def test_cameraeyes_privacy_off():
    """Privacy ENABLED → camera is OFF → is_on False."""
    State = PrivacyModeService.State
    sw = _make_switch(SWITCH_TYPES["cameraeyes"], privacymode=State.ENABLED)
    assert sw.is_on is False


# ---------------------------------------------------------------------------
# CameraEyes — cameralight
# ---------------------------------------------------------------------------


def test_cameraeyes_cameralight_on():
    State = CameraLightService.State
    sw = _make_switch(SWITCH_TYPES["cameraeyes_cameralight"], cameralight=State.ON)
    assert sw.is_on is True


def test_cameraeyes_cameralight_off():
    State = CameraLightService.State
    sw = _make_switch(SWITCH_TYPES["cameraeyes_cameralight"], cameralight=State.OFF)
    assert sw.is_on is False


# ---------------------------------------------------------------------------
# CameraEyes — notification
# ---------------------------------------------------------------------------


def test_cameraeyes_notification_enabled():
    State = CameraNotificationService.State
    sw = _make_switch(
        SWITCH_TYPES["cameraeyes_notification"], cameranotification=State.ENABLED
    )
    assert sw.is_on is True


def test_cameraeyes_notification_disabled():
    State = CameraNotificationService.State
    sw = _make_switch(
        SWITCH_TYPES["cameraeyes_notification"], cameranotification=State.DISABLED
    )
    assert sw.is_on is False


# ---------------------------------------------------------------------------
# Camera360 — privacy
# ---------------------------------------------------------------------------


def test_camera360_privacy_on():
    State = PrivacyModeService.State
    sw = _make_switch(SWITCH_TYPES["camera360"], privacymode=State.DISABLED)
    assert sw.is_on is True


def test_camera360_privacy_off():
    State = PrivacyModeService.State
    sw = _make_switch(SWITCH_TYPES["camera360"], privacymode=State.ENABLED)
    assert sw.is_on is False


# ---------------------------------------------------------------------------
# Camera360 — notification
# ---------------------------------------------------------------------------


def test_camera360_notification_enabled():
    State = CameraNotificationService.State
    sw = _make_switch(
        SWITCH_TYPES["camera360_notification"], cameranotification=State.ENABLED
    )
    assert sw.is_on is True


def test_camera360_notification_disabled():
    State = CameraNotificationService.State
    sw = _make_switch(
        SWITCH_TYPES["camera360_notification"], cameranotification=State.DISABLED
    )
    assert sw.is_on is False


# ---------------------------------------------------------------------------
# CameraOutdoorGen2 — privacy
# ---------------------------------------------------------------------------


def test_cameraoutdoorgen2_privacy_on():
    State = PrivacyModeService.State
    sw = _make_switch(SWITCH_TYPES["cameraoutdoorgen2"], privacymode=State.DISABLED)
    assert sw.is_on is True


def test_cameraoutdoorgen2_privacy_off():
    State = PrivacyModeService.State
    sw = _make_switch(SWITCH_TYPES["cameraoutdoorgen2"], privacymode=State.ENABLED)
    assert sw.is_on is False


# ---------------------------------------------------------------------------
# CameraOutdoorGen2 — frontlight
# ---------------------------------------------------------------------------


def test_cameraoutdoorgen2_frontlight_on():
    State = CameraFrontLightService.State
    sw = _make_switch(
        SWITCH_TYPES["cameraoutdoorgen2_camerafrontlight"], camerafrontlight=State.ON
    )
    assert sw.is_on is True


def test_cameraoutdoorgen2_frontlight_off():
    State = CameraFrontLightService.State
    sw = _make_switch(
        SWITCH_TYPES["cameraoutdoorgen2_camerafrontlight"], camerafrontlight=State.OFF
    )
    assert sw.is_on is False


# ---------------------------------------------------------------------------
# CameraOutdoorGen2 — ambientlight
# ---------------------------------------------------------------------------


def test_cameraoutdoorgen2_ambientlight_on():
    State = CameraAmbientLightService.State
    sw = _make_switch(
        SWITCH_TYPES["cameraoutdoorgen2_cameraambientlight"], cameraambientlight=State.ON
    )
    assert sw.is_on is True


def test_cameraoutdoorgen2_ambientlight_off():
    State = CameraAmbientLightService.State
    sw = _make_switch(
        SWITCH_TYPES["cameraoutdoorgen2_cameraambientlight"], cameraambientlight=State.OFF
    )
    assert sw.is_on is False


# ---------------------------------------------------------------------------
# PresenceSimulation (bool on_value)
# ---------------------------------------------------------------------------


def test_presencesimulation_is_on_true():
    sw = _make_switch(SWITCH_TYPES["presencesimulation"], enabled=True)
    assert sw.is_on is True


def test_presencesimulation_is_on_false():
    sw = _make_switch(SWITCH_TYPES["presencesimulation"], enabled=False)
    assert sw.is_on is False


# ---------------------------------------------------------------------------
# Bypass — ShutterContact2
# ---------------------------------------------------------------------------


def test_bypass_active():
    State = BypassService.State
    sw = _make_switch(SWITCH_TYPES["bypass"], bypass=State.BYPASS_ACTIVE)
    assert sw.is_on is True


def test_bypass_inactive():
    State = BypassService.State
    sw = _make_switch(SWITCH_TYPES["bypass"], bypass=State.BYPASS_INACTIVE)
    assert sw.is_on is False


# ---------------------------------------------------------------------------
# ChildLock — bool
# ---------------------------------------------------------------------------


def test_child_lock_bool_true():
    sw = _make_switch(SWITCH_TYPES["child_lock"], child_lock=True)
    assert sw.is_on is True


def test_child_lock_bool_false():
    sw = _make_switch(SWITCH_TYPES["child_lock"], child_lock=False)
    assert sw.is_on is False


# ---------------------------------------------------------------------------
# ChildLock — thermostat (enum State.ON / State.OFF)
# ---------------------------------------------------------------------------


def test_child_lock_thermostat_on():
    State = ThermostatService.State
    sw = _make_switch(SWITCH_TYPES["child_lock_thermostat"], child_lock=State.ON)
    assert sw.is_on is True


def test_child_lock_thermostat_off():
    State = ThermostatService.State
    sw = _make_switch(SWITCH_TYPES["child_lock_thermostat"], child_lock=State.OFF)
    assert sw.is_on is False


def test_child_lock_thermostat_bool_true_does_not_match():
    """child_lock_thermostat on_value is an enum — plain True must NOT match."""
    sw = _make_switch(SWITCH_TYPES["child_lock_thermostat"], child_lock=True)
    # ThermostatService.State.ON != True → is_on must be False
    assert sw.is_on is False


# ---------------------------------------------------------------------------
# SilentMode
# ---------------------------------------------------------------------------


def test_silent_mode_is_on_true_when_mode_silent():
    State = SilentModeService.State
    sw = _make_switch(SWITCH_TYPES["silent_mode"], silentmode=State.MODE_SILENT)
    assert sw.is_on is True


def test_silent_mode_is_on_false_when_mode_normal():
    State = SilentModeService.State
    sw = _make_switch(SWITCH_TYPES["silent_mode"], silentmode=State.MODE_NORMAL)
    assert sw.is_on is False


# ---------------------------------------------------------------------------
# VibrationEnabled (bool)
# ---------------------------------------------------------------------------


def test_vibration_enabled_true():
    sw = _make_switch(SWITCH_TYPES["vibration_enabled"], enabled=True)
    assert sw.is_on is True


def test_vibration_enabled_false():
    sw = _make_switch(SWITCH_TYPES["vibration_enabled"], enabled=False)
    assert sw.is_on is False


# ---------------------------------------------------------------------------
# UserDefinedState (bool)
# ---------------------------------------------------------------------------


def test_user_defined_state_true():
    sw = _make_switch(SWITCH_TYPES["user_defined_state"], state=True)
    assert sw.is_on is True


def test_user_defined_state_false():
    sw = _make_switch(SWITCH_TYPES["user_defined_state"], state=False)
    assert sw.is_on is False


# ---------------------------------------------------------------------------
# async_turn_on / async_turn_off — AsyncMock called with True / False
# ---------------------------------------------------------------------------


def test_turn_on_sets_attr_true():
    """async_turn_on must await device.async_set_switchstate(True)."""
    dev, mock = _async_spy_device("switchstate")
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = dev
    sw.entity_description = SWITCH_TYPES["smartplug"]
    sw.entity_id = "switch.test"
    asyncio.run(sw.async_turn_on())
    mock.assert_awaited_once_with(True)


def test_turn_off_sets_attr_false():
    """async_turn_off must await device.async_set_switchstate(False)."""
    dev, mock = _async_spy_device("switchstate")
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = dev
    sw.entity_description = SWITCH_TYPES["smartplug"]
    sw.entity_id = "switch.test"
    asyncio.run(sw.async_turn_off())
    mock.assert_awaited_once_with(False)


def test_turn_on_presencesimulation_writes_true():
    dev, mock = _async_spy_device("enabled")
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = dev
    sw.entity_description = SWITCH_TYPES["presencesimulation"]
    sw.entity_id = "switch.test"
    asyncio.run(sw.async_turn_on())
    mock.assert_awaited_once_with(True)


def test_turn_off_presencesimulation_writes_false():
    dev, mock = _async_spy_device("enabled")
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = dev
    sw.entity_description = SWITCH_TYPES["presencesimulation"]
    sw.entity_id = "switch.test"
    asyncio.run(sw.async_turn_off())
    mock.assert_awaited_once_with(False)


# ---------------------------------------------------------------------------
# AttributeError / None guard — cameraeyes privacymode
# is_on still uses a raising property; turn_on/off guard the async_set_ attr
# ---------------------------------------------------------------------------


class _CameraEyesNoPrivacy:
    privacymode = _raising_property()
    # no async_set_privacymode → getattr raises AttributeError (guard catches it)


def test_none_guard_cameraeyes_privacymode_is_on():
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = _CameraEyesNoPrivacy()
    sw.entity_description = SWITCH_TYPES["cameraeyes"]
    assert sw.is_on is None


def test_none_guard_cameraeyes_privacymode_turn_on():
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = _CameraEyesNoPrivacy()
    sw.entity_description = SWITCH_TYPES["cameraeyes"]
    sw.entity_id = "switch.test"
    asyncio.run(sw.async_turn_on())  # must not raise


def test_none_guard_cameraeyes_privacymode_turn_off():
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = _CameraEyesNoPrivacy()
    sw.entity_description = SWITCH_TYPES["cameraeyes"]
    sw.entity_id = "switch.test"
    asyncio.run(sw.async_turn_off())  # must not raise


# ---------------------------------------------------------------------------
# AttributeError / None guard — cameraeyes cameralight
# ---------------------------------------------------------------------------


class _CameraEyesNoLight:
    cameralight = _raising_property()
    # no async_set_cameralight → getattr raises AttributeError (guard catches it)


def test_none_guard_cameraeyes_cameralight_is_on():
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = _CameraEyesNoLight()
    sw.entity_description = SWITCH_TYPES["cameraeyes_cameralight"]
    assert sw.is_on is None


def test_none_guard_cameraeyes_cameralight_turn_on():
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = _CameraEyesNoLight()
    sw.entity_description = SWITCH_TYPES["cameraeyes_cameralight"]
    sw.entity_id = "switch.test"
    asyncio.run(sw.async_turn_on())  # must not raise


# ---------------------------------------------------------------------------
# AttributeError / None guard — cameraoutdoorgen2 frontlight
# ---------------------------------------------------------------------------


class _Gen2NoFrontlight:
    camerafrontlight = _raising_property()
    # no async_set_camerafrontlight → getattr raises AttributeError (guard catches)


def test_none_guard_cameraoutdoorgen2_frontlight_is_on():
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = _Gen2NoFrontlight()
    sw.entity_description = SWITCH_TYPES["cameraoutdoorgen2_camerafrontlight"]
    assert sw.is_on is None


def test_none_guard_cameraoutdoorgen2_frontlight_turn_on():
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = _Gen2NoFrontlight()
    sw.entity_description = SWITCH_TYPES["cameraoutdoorgen2_camerafrontlight"]
    sw.entity_id = "switch.test"
    asyncio.run(sw.async_turn_on())  # must not raise


def test_none_guard_cameraoutdoorgen2_frontlight_turn_off():
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = _Gen2NoFrontlight()
    sw.entity_description = SWITCH_TYPES["cameraoutdoorgen2_camerafrontlight"]
    sw.entity_id = "switch.test"
    asyncio.run(sw.async_turn_off())  # must not raise


# ---------------------------------------------------------------------------
# SHCException / SHCConnectionError -> HomeAssistantError
# ---------------------------------------------------------------------------


def test_turn_on_shc_exception_raises_home_assistant_error():
    """A real API-level rejection must surface as HomeAssistantError, not raw."""
    dev = SimpleNamespace(
        name="Test Switch",
        async_set_switchstate=AsyncMock(side_effect=SHCException("rejected")),
    )
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = dev
    sw.entity_description = SWITCH_TYPES["smartplug"]
    sw.entity_id = "switch.test"
    sw._attr_name = None

    with pytest.raises(HomeAssistantError):
        asyncio.run(sw.async_turn_on())


def test_turn_off_shc_connection_error_raises_home_assistant_error():
    """A comms failure on turn_off must surface as HomeAssistantError, not raw."""
    dev = SimpleNamespace(
        name="Test Switch",
        async_set_switchstate=AsyncMock(side_effect=SHCConnectionError("no route")),
    )
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = dev
    sw.entity_description = SWITCH_TYPES["smartplug"]
    sw.entity_id = "switch.test"
    sw._attr_name = None

    with pytest.raises(HomeAssistantError):
        asyncio.run(sw.async_turn_off())


# ---------------------------------------------------------------------------
# should_poll
# ---------------------------------------------------------------------------


def test_should_poll_camera_is_true():
    sw = _make_switch(SWITCH_TYPES["cameraeyes"], privacymode=None)
    assert sw.should_poll is True


def test_should_poll_camera360_is_true():
    sw = _make_switch(SWITCH_TYPES["camera360"], privacymode=None)
    assert sw.should_poll is True


def test_should_poll_cameraoutdoorgen2_is_true():
    sw = _make_switch(SWITCH_TYPES["cameraoutdoorgen2"], privacymode=None)
    assert sw.should_poll is True


def test_should_poll_cameraeyes_frontlight_is_true():
    sw = _make_switch(SWITCH_TYPES["cameraeyes_cameralight"], cameralight=None)
    assert sw.should_poll is True


def test_should_poll_smartplug_is_false():
    sw = _make_switch(SWITCH_TYPES["smartplug"], switchstate=None)
    assert sw.should_poll is False


def test_should_poll_presencesimulation_is_false():
    sw = _make_switch(SWITCH_TYPES["presencesimulation"], enabled=False)
    assert sw.should_poll is False


def test_should_poll_bypass_is_false():
    State = BypassService.State
    sw = _make_switch(SWITCH_TYPES["bypass"], bypass=State.BYPASS_INACTIVE)
    assert sw.should_poll is False


def test_should_poll_child_lock_is_false():
    sw = _make_switch(SWITCH_TYPES["child_lock"], child_lock=False)
    assert sw.should_poll is False


def test_should_poll_silent_mode_is_false():
    State = SilentModeService.State
    sw = _make_switch(SWITCH_TYPES["silent_mode"], silentmode=State.MODE_NORMAL)
    assert sw.should_poll is False


def test_should_poll_vibration_enabled_is_false():
    sw = _make_switch(SWITCH_TYPES["vibration_enabled"], enabled=False)
    assert sw.should_poll is False


def test_should_poll_user_defined_state_is_false():
    sw = _make_switch(SWITCH_TYPES["user_defined_state"], state=False)
    assert sw.should_poll is False


# ---------------------------------------------------------------------------
# _attr_name / _attr_unique_id (replicate __init__ logic without calling it)
# ---------------------------------------------------------------------------


def _init_name_and_id(sw: SHCSwitch, attr_name=None) -> None:
    """Replicate the name/unique_id lines from SHCSwitch.__init__."""
    device = sw._device
    sw._attr_name = (
        f"{device.name}" if attr_name is None else f"{device.name} {attr_name}"
    )
    sw._attr_unique_id = (
        f"{device.root_device_id}_{device.id}"
        if attr_name is None
        else f"{device.root_device_id}_{device.id}_{attr_name.lower()}"
    )


def test_attr_name_no_suffix():
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = SimpleNamespace(name="Plug 1", root_device_id="rootA", id="devB")
    sw.entity_description = SWITCH_TYPES["smartplug"]
    _init_name_and_id(sw, attr_name=None)
    assert sw._attr_name == "Plug 1"


def test_attr_name_with_suffix():
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = SimpleNamespace(name="Plug 1", root_device_id="rootA", id="devB")
    sw.entity_description = SWITCH_TYPES["smartplug_routing"]
    _init_name_and_id(sw, attr_name="Routing")
    assert sw._attr_name == "Plug 1 Routing"


def test_unique_id_no_suffix():
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = SimpleNamespace(name="Dev", root_device_id="root1", id="dev1")
    sw.entity_description = SWITCH_TYPES["smartplug"]
    _init_name_and_id(sw, attr_name=None)
    assert sw._attr_unique_id == "root1_dev1"


def test_unique_id_with_suffix():
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = SimpleNamespace(name="Dev", root_device_id="root1", id="dev1")
    sw.entity_description = SWITCH_TYPES["smartplug_routing"]
    _init_name_and_id(sw, attr_name="Routing")
    assert sw._attr_unique_id == "root1_dev1_routing"


def test_unique_id_suffix_is_lowercased():
    """attr_name is .lower()'d in the unique_id — CamelCase becomes lowercase."""
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = SimpleNamespace(name="Cam", root_device_id="rX", id="dY")
    sw.entity_description = SWITCH_TYPES["cameraeyes_cameralight"]
    _init_name_and_id(sw, attr_name="Light")
    assert sw._attr_unique_id == "rX_dY_light"


def test_attr_name_camera_with_suffix():
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = SimpleNamespace(name="MyCamera", root_device_id="rc", id="dc")
    sw.entity_description = SWITCH_TYPES["cameraeyes_notification"]
    _init_name_and_id(sw, attr_name="Notification")
    assert sw._attr_name == "MyCamera Notification"
    assert sw._attr_unique_id == "rc_dc_notification"


# ---------------------------------------------------------------------------
# on_key / on_value sanity — SWITCH_TYPES descriptor checks
# ---------------------------------------------------------------------------


def test_switch_types_all_keys_present():
    """All expected SWITCH_TYPES keys must exist."""
    expected = {
        "smartplug",
        "smartplug_routing",
        "smartplugcompact",
        "micromodule_relay_switch",
        "lightswitch",
        "cameraeyes",
        "cameraeyes_cameralight",
        "cameraeyes_notification",
        "camera360",
        "camera360_notification",
        "cameraoutdoorgen2",
        "cameraoutdoorgen2_camerafrontlight",
        "cameraoutdoorgen2_cameraambientlight",
        "presencesimulation",
        "bypass",
        "bypass_infinite",
        "child_lock",
        "child_lock_thermostat",
        "pet_immunity_enabled",
        "silent_mode",
        "vibration_enabled",
        "user_defined_state",
        # New APK-batch 2-6 switch types (guarded by hasattr in setup):
        "energy_saving_mode_enabled",
        "warning_suppressed",
        "nightly_promise_enabled",
        "humidity_warning_enabled",
        "swap_inputs",
        "swap_outputs",
        "pre_alarm_enabled",
        "smart_sensitivity_enabled",
        "tamper_protection_enabled",
        "intrusion_alarm",
    }
    assert expected == set(SWITCH_TYPES.keys())


def test_presencesimulation_on_value_is_bool_true():
    assert SWITCH_TYPES["presencesimulation"].on_value is True


def test_child_lock_on_value_is_bool_true():
    assert SWITCH_TYPES["child_lock"].on_value is True


def test_child_lock_thermostat_on_value_is_enum():
    assert SWITCH_TYPES["child_lock_thermostat"].on_value is (
        ThermostatService.State.ON
    )


def test_vibration_enabled_on_value_is_bool_true():
    assert SWITCH_TYPES["vibration_enabled"].on_value is True


def test_user_defined_state_on_value_is_bool_true():
    assert SWITCH_TYPES["user_defined_state"].on_value is True


def test_bypass_on_value_is_bypass_active():
    assert SWITCH_TYPES["bypass"].on_value is (
        BypassService.State.BYPASS_ACTIVE
    )


def test_silent_mode_on_value_is_mode_silent():
    assert SWITCH_TYPES["silent_mode"].on_value is (
        SilentModeService.State.MODE_SILENT
    )


def test_cameraeyes_on_value_is_privacy_disabled():
    """Camera-on = privacy DISABLED (inverted logic)."""
    assert SWITCH_TYPES["cameraeyes"].on_value is (
        PrivacyModeService.State.DISABLED
    )


def test_camera360_on_value_is_privacy_disabled():
    assert SWITCH_TYPES["camera360"].on_value is (
        PrivacyModeService.State.DISABLED
    )


def test_cameraoutdoorgen2_on_value_is_privacy_disabled():
    assert SWITCH_TYPES["cameraoutdoorgen2"].on_value is (
        PrivacyModeService.State.DISABLED
    )
