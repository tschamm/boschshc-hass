"""Additional pure-unit coverage for switch.py — complements test_switch_unit.py.

Covers areas not yet exercised by the existing switch test files:
- AttributeError / None guards for child_lock, bypass, vibration_enabled,
  presencesimulation, child_lock_thermostat, silent_mode, pet_immunity_enabled,
  user_defined_state, cameraeyes_notification (turn_off),
  cameraoutdoorgen2_cameraambientlight (all three guard paths)
- SWITCH_TYPES descriptor metadata: device_class, icon, entity_category
- Boundary/edge-state coverage: bypass UNKNOWN, cameralight NONE,
  frontlight NONE, ambientlight NONE
- SHCSwitch real __init__ call (not bypassed) for unique_id and attr_name
- SHCUserDefinedStateSwitch is_on / turn_on / turn_off / device_name /
  device_id / device_info / should_poll (pure unit, no HA runtime)

Run with (from boschshc-hass root):
  PYTHONPATH="<boschshc-hass>:<boschshcpy>" PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \\
  python3 -m pytest tests/bosch_shc/test_switch_coverage.py -q -o addopts=
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from boschshcpy import (
    SHCCamera360,
    SHCCameraEyes,
    SHCCameraOutdoorGen2,
    SHCShutterContact2,
    SHCThermostat,
)
from homeassistant.components.switch import SwitchDeviceClass
from homeassistant.helpers.entity import EntityCategory

from custom_components.bosch_shc.switch import (
    SWITCH_TYPES,
    SHCSwitch,
    SHCUserDefinedStateSwitch,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _raising_property(exc_type=AttributeError):
    """Return a class-level descriptor that raises exc_type on get/set."""

    class _Raiser:
        def __get__(self, obj, objtype=None):
            raise exc_type("service is None")

        def __set__(self, obj, value):
            raise exc_type("service is None")

    return _Raiser()


def _make_switch(description, **device_attrs):
    """Build a bare SHCSwitch bypassing SHCEntity.__init__ with a fake device."""
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = SimpleNamespace(**device_attrs)
    sw.entity_description = description
    sw.entity_id = "switch.test"
    return sw


def _spy_switch(description, attr: str):
    """Return (switch, mock) where device.async_set_<attr> is an AsyncMock."""
    mock = AsyncMock()
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = SimpleNamespace(**{f"async_set_{attr}": mock})
    sw.entity_description = description
    sw.entity_id = "switch.spy"
    return sw, mock


class _FakeDevice:
    """Minimal fake SHCDevice that satisfies SHCEntity.__init__."""

    name = "Fake Device"
    id = "dev1"
    root_device_id = "root1"
    device_services = []
    status = "AVAILABLE"
    deleted = False
    manufacturer = "Bosch"
    device_model = "TestModel"


# ---------------------------------------------------------------------------
# 1 — SWITCH_TYPES descriptor metadata
# ---------------------------------------------------------------------------


class TestSwitchTypeMetadata:
    """Verify device_class, icon, entity_category on each descriptor."""

    def test_smartplug_device_class_outlet(self):
        assert SWITCH_TYPES["smartplug"].device_class == SwitchDeviceClass.OUTLET

    def test_smartplug_routing_device_class_switch(self):
        assert SWITCH_TYPES["smartplug_routing"].device_class == SwitchDeviceClass.SWITCH

    def test_smartplug_routing_icon(self):
        assert SWITCH_TYPES["smartplug_routing"].icon == "mdi:wifi"

    def test_smartplug_routing_entity_category_config(self):
        assert SWITCH_TYPES["smartplug_routing"].entity_category == EntityCategory.CONFIG

    def test_cameraeyes_icon(self):
        assert SWITCH_TYPES["cameraeyes"].icon == "mdi:video"

    def test_cameraeyes_cameralight_icon(self):
        assert SWITCH_TYPES["cameraeyes_cameralight"].icon == "mdi:light-flood-down"

    def test_cameraeyes_cameralight_entity_category_config(self):
        assert (
            SWITCH_TYPES["cameraeyes_cameralight"].entity_category
            == EntityCategory.CONFIG
        )

    def test_cameraeyes_notification_icon(self):
        assert SWITCH_TYPES["cameraeyes_notification"].icon == "mdi:message-badge"

    def test_cameraeyes_notification_entity_category_config(self):
        assert (
            SWITCH_TYPES["cameraeyes_notification"].entity_category
            == EntityCategory.CONFIG
        )

    def test_camera360_icon(self):
        assert SWITCH_TYPES["camera360"].icon == "mdi:video"

    def test_camera360_notification_entity_category_config(self):
        assert (
            SWITCH_TYPES["camera360_notification"].entity_category
            == EntityCategory.CONFIG
        )

    def test_cameraoutdoorgen2_icon(self):
        assert SWITCH_TYPES["cameraoutdoorgen2"].icon == "mdi:video"

    def test_cameraoutdoorgen2_frontlight_entity_category_config(self):
        assert (
            SWITCH_TYPES["cameraoutdoorgen2_camerafrontlight"].entity_category
            == EntityCategory.CONFIG
        )

    def test_cameraoutdoorgen2_ambientlight_icon(self):
        assert SWITCH_TYPES["cameraoutdoorgen2_cameraambientlight"].icon == "mdi:wall-sconce-flat"

    def test_child_lock_icon(self):
        assert SWITCH_TYPES["child_lock"].icon == "mdi:lock"

    def test_child_lock_entity_category_config(self):
        assert SWITCH_TYPES["child_lock"].entity_category == EntityCategory.CONFIG

    def test_child_lock_thermostat_icon(self):
        assert SWITCH_TYPES["child_lock_thermostat"].icon == "mdi:lock"

    def test_child_lock_thermostat_entity_category_config(self):
        assert (
            SWITCH_TYPES["child_lock_thermostat"].entity_category == EntityCategory.CONFIG
        )

    def test_pet_immunity_icon(self):
        assert SWITCH_TYPES["pet_immunity_enabled"].icon == "mdi:paw"

    def test_silent_mode_icon(self):
        assert SWITCH_TYPES["silent_mode"].icon == "mdi:sleep"

    def test_silent_mode_entity_category_config(self):
        assert SWITCH_TYPES["silent_mode"].entity_category == EntityCategory.CONFIG

    def test_presencesimulation_device_class(self):
        assert SWITCH_TYPES["presencesimulation"].device_class == SwitchDeviceClass.SWITCH

    def test_bypass_no_icon(self):
        """bypass has no icon configured."""
        assert SWITCH_TYPES["bypass"].icon is None

    def test_user_defined_state_entity_category_config(self):
        assert (
            SWITCH_TYPES["user_defined_state"].entity_category == EntityCategory.CONFIG
        )


# ---------------------------------------------------------------------------
# 2 — Boundary / edge-state is_on coverage
# ---------------------------------------------------------------------------


class TestEdgeStateIsOn:
    """Cover rarely-tested enum values (NONE, UNKNOWN) and bool boundaries."""

    def test_bypass_unknown_is_off(self):
        """UNKNOWN bypass state → is_on False (not ON_VALUE)."""
        State = SHCShutterContact2.BypassService.State
        sw = _make_switch(SWITCH_TYPES["bypass"], bypass=State.UNKNOWN)
        assert sw.is_on is False

    def test_cameraeyes_cameralight_none_is_off(self):
        """CameraLight.NONE → is_on False."""
        State = SHCCameraEyes.CameraLightService.State
        sw = _make_switch(SWITCH_TYPES["cameraeyes_cameralight"], cameralight=State.NONE)
        assert sw.is_on is False

    def test_cameraoutdoorgen2_frontlight_none_is_off(self):
        """FrontLight.NONE → is_on False."""
        State = SHCCameraOutdoorGen2.CameraFrontLightService.State
        sw = _make_switch(
            SWITCH_TYPES["cameraoutdoorgen2_camerafrontlight"],
            camerafrontlight=State.NONE,
        )
        assert sw.is_on is False

    def test_cameraoutdoorgen2_ambientlight_none_is_off(self):
        """AmbientLight.NONE → is_on False."""
        State = SHCCameraOutdoorGen2.CameraAmbientLightService.State
        sw = _make_switch(
            SWITCH_TYPES["cameraoutdoorgen2_cameraambientlight"],
            cameraambientlight=State.NONE,
        )
        assert sw.is_on is False

    def test_child_lock_bool_false_is_off(self):
        """child_lock=False → is_on False (bool path)."""
        sw = _make_switch(SWITCH_TYPES["child_lock"], child_lock=False)
        assert sw.is_on is False

    def test_child_lock_thermostat_enum_off_is_off(self):
        """ThermostatService.State.OFF → is_on False (enum path)."""
        State = SHCThermostat.ThermostatService.State
        sw = _make_switch(SWITCH_TYPES["child_lock_thermostat"], child_lock=State.OFF)
        assert sw.is_on is False

    def test_child_lock_thermostat_bool_false_does_not_match(self):
        """child_lock_thermostat compares against enum State.ON — False must NOT match."""
        sw = _make_switch(SWITCH_TYPES["child_lock_thermostat"], child_lock=False)
        assert sw.is_on is False

    def test_pet_immunity_false_is_off(self):
        sw = _make_switch(SWITCH_TYPES["pet_immunity_enabled"], pet_immunity_enabled=False)
        assert sw.is_on is False

    def test_pet_immunity_true_is_on(self):
        sw = _make_switch(SWITCH_TYPES["pet_immunity_enabled"], pet_immunity_enabled=True)
        assert sw.is_on is True

    def test_camera360_cameranotification_disabled_is_off(self):
        State = SHCCamera360.CameraNotificationService.State
        sw = _make_switch(
            SWITCH_TYPES["camera360_notification"],
            cameranotification=State.DISABLED,
        )
        assert sw.is_on is False


# ---------------------------------------------------------------------------
# 3 — AttributeError guard: turn_on / turn_off paths not yet covered
# ---------------------------------------------------------------------------


class _NoChildLock:
    child_lock = _raising_property()


class _NoBypass:
    bypass = _raising_property()


class _NoEnabled:
    enabled = _raising_property()


class _NoSilentMode:
    silentmode = _raising_property()


class _NoPetImmunity:
    pet_immunity_enabled = _raising_property()


class _NoState:
    state = _raising_property()


class _NoCameraNotification:
    cameranotification = _raising_property()


class _NoAmbientLight:
    cameraambientlight = _raising_property()


class TestNoneGuardIsOn:
    """is_on must return None (not raise) for any unregistered service."""

    def test_child_lock_service_none_is_on(self):
        sw = SHCSwitch.__new__(SHCSwitch)
        sw._device = _NoChildLock()
        sw.entity_description = SWITCH_TYPES["child_lock"]
        assert sw.is_on is None

    def test_child_lock_thermostat_service_none_is_on(self):
        sw = SHCSwitch.__new__(SHCSwitch)
        sw._device = _NoChildLock()
        sw.entity_description = SWITCH_TYPES["child_lock_thermostat"]
        assert sw.is_on is None

    def test_bypass_service_none_is_on(self):
        sw = SHCSwitch.__new__(SHCSwitch)
        sw._device = _NoBypass()
        sw.entity_description = SWITCH_TYPES["bypass"]
        assert sw.is_on is None

    def test_presencesimulation_service_none_is_on(self):
        sw = SHCSwitch.__new__(SHCSwitch)
        sw._device = _NoEnabled()
        sw.entity_description = SWITCH_TYPES["presencesimulation"]
        assert sw.is_on is None

    def test_vibration_service_none_is_on(self):
        sw = SHCSwitch.__new__(SHCSwitch)
        sw._device = _NoEnabled()
        sw.entity_description = SWITCH_TYPES["vibration_enabled"]
        assert sw.is_on is None

    def test_silent_mode_service_none_is_on(self):
        sw = SHCSwitch.__new__(SHCSwitch)
        sw._device = _NoSilentMode()
        sw.entity_description = SWITCH_TYPES["silent_mode"]
        assert sw.is_on is None

    def test_pet_immunity_service_none_is_on(self):
        sw = SHCSwitch.__new__(SHCSwitch)
        sw._device = _NoPetImmunity()
        sw.entity_description = SWITCH_TYPES["pet_immunity_enabled"]
        assert sw.is_on is None

    def test_user_defined_state_service_none_is_on(self):
        sw = SHCSwitch.__new__(SHCSwitch)
        sw._device = _NoState()
        sw.entity_description = SWITCH_TYPES["user_defined_state"]
        assert sw.is_on is None

    def test_cameraeyes_notification_service_none_is_on(self):
        sw = SHCSwitch.__new__(SHCSwitch)
        sw._device = _NoCameraNotification()
        sw.entity_description = SWITCH_TYPES["cameraeyes_notification"]
        assert sw.is_on is None

    def test_cameraoutdoorgen2_ambientlight_service_none_is_on(self):
        sw = SHCSwitch.__new__(SHCSwitch)
        sw._device = _NoAmbientLight()
        sw.entity_description = SWITCH_TYPES["cameraoutdoorgen2_cameraambientlight"]
        assert sw.is_on is None


class TestNoneGuardTurnOn:
    """async_turn_on must swallow AttributeError when async_set_<key> is absent."""

    def test_child_lock_service_none_turn_on(self):
        sw = SHCSwitch.__new__(SHCSwitch)
        sw._device = _NoChildLock()
        sw.entity_description = SWITCH_TYPES["child_lock"]
        sw.entity_id = "switch.test"
        asyncio.run(sw.async_turn_on())  # must not raise

    def test_bypass_service_none_turn_on(self):
        sw = SHCSwitch.__new__(SHCSwitch)
        sw._device = _NoBypass()
        sw.entity_description = SWITCH_TYPES["bypass"]
        sw.entity_id = "switch.test"
        asyncio.run(sw.async_turn_on())  # must not raise

    def test_presencesimulation_service_none_turn_on(self):
        sw = SHCSwitch.__new__(SHCSwitch)
        sw._device = _NoEnabled()
        sw.entity_description = SWITCH_TYPES["presencesimulation"]
        sw.entity_id = "switch.test"
        asyncio.run(sw.async_turn_on())  # must not raise

    def test_vibration_service_none_turn_on(self):
        sw = SHCSwitch.__new__(SHCSwitch)
        sw._device = _NoEnabled()
        sw.entity_description = SWITCH_TYPES["vibration_enabled"]
        sw.entity_id = "switch.test"
        asyncio.run(sw.async_turn_on())  # must not raise

    def test_silent_mode_service_none_turn_on(self):
        sw = SHCSwitch.__new__(SHCSwitch)
        sw._device = _NoSilentMode()
        sw.entity_description = SWITCH_TYPES["silent_mode"]
        sw.entity_id = "switch.test"
        asyncio.run(sw.async_turn_on())  # must not raise

    def test_pet_immunity_service_none_turn_on(self):
        sw = SHCSwitch.__new__(SHCSwitch)
        sw._device = _NoPetImmunity()
        sw.entity_description = SWITCH_TYPES["pet_immunity_enabled"]
        sw.entity_id = "switch.test"
        asyncio.run(sw.async_turn_on())  # must not raise

    def test_cameraeyes_notification_service_none_turn_on(self):
        sw = SHCSwitch.__new__(SHCSwitch)
        sw._device = _NoCameraNotification()
        sw.entity_description = SWITCH_TYPES["cameraeyes_notification"]
        sw.entity_id = "switch.test"
        asyncio.run(sw.async_turn_on())  # must not raise

    def test_cameraoutdoorgen2_ambientlight_service_none_turn_on(self):
        sw = SHCSwitch.__new__(SHCSwitch)
        sw._device = _NoAmbientLight()
        sw.entity_description = SWITCH_TYPES["cameraoutdoorgen2_cameraambientlight"]
        sw.entity_id = "switch.test"
        asyncio.run(sw.async_turn_on())  # must not raise

    def test_user_defined_state_service_none_turn_on(self):
        sw = SHCSwitch.__new__(SHCSwitch)
        sw._device = _NoState()
        sw.entity_description = SWITCH_TYPES["user_defined_state"]
        sw.entity_id = "switch.test"
        asyncio.run(sw.async_turn_on())  # must not raise


class TestNoneGuardTurnOff:
    """async_turn_off must swallow AttributeError when async_set_<key> is absent."""

    def test_child_lock_service_none_turn_off(self):
        sw = SHCSwitch.__new__(SHCSwitch)
        sw._device = _NoChildLock()
        sw.entity_description = SWITCH_TYPES["child_lock"]
        sw.entity_id = "switch.test"
        asyncio.run(sw.async_turn_off())  # must not raise

    def test_child_lock_thermostat_service_none_turn_off(self):
        sw = SHCSwitch.__new__(SHCSwitch)
        sw._device = _NoChildLock()
        sw.entity_description = SWITCH_TYPES["child_lock_thermostat"]
        sw.entity_id = "switch.test"
        asyncio.run(sw.async_turn_off())  # must not raise

    def test_bypass_service_none_turn_off(self):
        sw = SHCSwitch.__new__(SHCSwitch)
        sw._device = _NoBypass()
        sw.entity_description = SWITCH_TYPES["bypass"]
        sw.entity_id = "switch.test"
        asyncio.run(sw.async_turn_off())  # must not raise

    def test_presencesimulation_service_none_turn_off(self):
        sw = SHCSwitch.__new__(SHCSwitch)
        sw._device = _NoEnabled()
        sw.entity_description = SWITCH_TYPES["presencesimulation"]
        sw.entity_id = "switch.test"
        asyncio.run(sw.async_turn_off())  # must not raise

    def test_vibration_service_none_turn_off(self):
        sw = SHCSwitch.__new__(SHCSwitch)
        sw._device = _NoEnabled()
        sw.entity_description = SWITCH_TYPES["vibration_enabled"]
        sw.entity_id = "switch.test"
        asyncio.run(sw.async_turn_off())  # must not raise

    def test_silent_mode_service_none_turn_off(self):
        sw = SHCSwitch.__new__(SHCSwitch)
        sw._device = _NoSilentMode()
        sw.entity_description = SWITCH_TYPES["silent_mode"]
        sw.entity_id = "switch.test"
        asyncio.run(sw.async_turn_off())  # must not raise

    def test_pet_immunity_service_none_turn_off(self):
        sw = SHCSwitch.__new__(SHCSwitch)
        sw._device = _NoPetImmunity()
        sw.entity_description = SWITCH_TYPES["pet_immunity_enabled"]
        sw.entity_id = "switch.test"
        asyncio.run(sw.async_turn_off())  # must not raise

    def test_cameraeyes_notification_service_none_turn_off(self):
        sw = SHCSwitch.__new__(SHCSwitch)
        sw._device = _NoCameraNotification()
        sw.entity_description = SWITCH_TYPES["cameraeyes_notification"]
        sw.entity_id = "switch.test"
        asyncio.run(sw.async_turn_off())  # must not raise

    def test_cameraoutdoorgen2_ambientlight_service_none_turn_off(self):
        sw = SHCSwitch.__new__(SHCSwitch)
        sw._device = _NoAmbientLight()
        sw.entity_description = SWITCH_TYPES["cameraoutdoorgen2_cameraambientlight"]
        sw.entity_id = "switch.test"
        asyncio.run(sw.async_turn_off())  # must not raise

    def test_user_defined_state_service_none_turn_off(self):
        sw = SHCSwitch.__new__(SHCSwitch)
        sw._device = _NoState()
        sw.entity_description = SWITCH_TYPES["user_defined_state"]
        sw.entity_id = "switch.test"
        asyncio.run(sw.async_turn_off())  # must not raise


# ---------------------------------------------------------------------------
# 4 — turn_on / turn_off setter coverage for remaining SWITCH_TYPES
# ---------------------------------------------------------------------------


class TestTurnOnOffSetters:
    """Ensure async_turn_on/off await async_set_<key>(True/False)."""

    def test_child_lock_turn_on_writes_true(self):
        sw, mock = _spy_switch(SWITCH_TYPES["child_lock"], "child_lock")
        asyncio.run(sw.async_turn_on())
        mock.assert_awaited_once_with(True)

    def test_child_lock_turn_off_writes_false(self):
        sw, mock = _spy_switch(SWITCH_TYPES["child_lock"], "child_lock")
        asyncio.run(sw.async_turn_off())
        mock.assert_awaited_once_with(False)

    def test_child_lock_thermostat_turn_on_writes_true(self):
        sw, mock = _spy_switch(SWITCH_TYPES["child_lock_thermostat"], "child_lock")
        asyncio.run(sw.async_turn_on())
        mock.assert_awaited_once_with(True)

    def test_child_lock_thermostat_turn_off_writes_false(self):
        sw, mock = _spy_switch(SWITCH_TYPES["child_lock_thermostat"], "child_lock")
        asyncio.run(sw.async_turn_off())
        mock.assert_awaited_once_with(False)

    def test_bypass_turn_on_writes_true(self):
        sw, mock = _spy_switch(SWITCH_TYPES["bypass"], "bypass")
        asyncio.run(sw.async_turn_on())
        mock.assert_awaited_once_with(True)

    def test_bypass_turn_off_writes_false(self):
        sw, mock = _spy_switch(SWITCH_TYPES["bypass"], "bypass")
        asyncio.run(sw.async_turn_off())
        mock.assert_awaited_once_with(False)

    def test_silent_mode_turn_on_writes_true(self):
        sw, mock = _spy_switch(SWITCH_TYPES["silent_mode"], "silentmode")
        asyncio.run(sw.async_turn_on())
        mock.assert_awaited_once_with(True)

    def test_silent_mode_turn_off_writes_false(self):
        sw, mock = _spy_switch(SWITCH_TYPES["silent_mode"], "silentmode")
        asyncio.run(sw.async_turn_off())
        mock.assert_awaited_once_with(False)

    def test_vibration_enabled_turn_on_writes_true(self):
        sw, mock = _spy_switch(SWITCH_TYPES["vibration_enabled"], "enabled")
        asyncio.run(sw.async_turn_on())
        mock.assert_awaited_once_with(True)

    def test_vibration_enabled_turn_off_writes_false(self):
        sw, mock = _spy_switch(SWITCH_TYPES["vibration_enabled"], "enabled")
        asyncio.run(sw.async_turn_off())
        mock.assert_awaited_once_with(False)

    def test_cameraeyes_notification_turn_on_writes_true(self):
        sw, mock = _spy_switch(
            SWITCH_TYPES["cameraeyes_notification"], "cameranotification"
        )
        asyncio.run(sw.async_turn_on())
        mock.assert_awaited_once_with(True)

    def test_cameraeyes_notification_turn_off_writes_false(self):
        sw, mock = _spy_switch(
            SWITCH_TYPES["cameraeyes_notification"], "cameranotification"
        )
        asyncio.run(sw.async_turn_off())
        mock.assert_awaited_once_with(False)

    def test_camera360_notification_turn_on_writes_true(self):
        sw, mock = _spy_switch(
            SWITCH_TYPES["camera360_notification"], "cameranotification"
        )
        asyncio.run(sw.async_turn_on())
        mock.assert_awaited_once_with(True)

    def test_camera360_notification_turn_off_writes_false(self):
        sw, mock = _spy_switch(
            SWITCH_TYPES["camera360_notification"], "cameranotification"
        )
        asyncio.run(sw.async_turn_off())
        mock.assert_awaited_once_with(False)

    def test_cameraoutdoorgen2_ambientlight_turn_on_writes_true(self):
        sw, mock = _spy_switch(
            SWITCH_TYPES["cameraoutdoorgen2_cameraambientlight"], "cameraambientlight"
        )
        asyncio.run(sw.async_turn_on())
        mock.assert_awaited_once_with(True)

    def test_cameraoutdoorgen2_ambientlight_turn_off_writes_false(self):
        sw, mock = _spy_switch(
            SWITCH_TYPES["cameraoutdoorgen2_cameraambientlight"], "cameraambientlight"
        )
        asyncio.run(sw.async_turn_off())
        mock.assert_awaited_once_with(False)

    def test_cameraeyes_privacy_turn_on_writes_true(self):
        sw, mock = _spy_switch(SWITCH_TYPES["cameraeyes"], "privacymode")
        asyncio.run(sw.async_turn_on())
        mock.assert_awaited_once_with(True)

    def test_cameraeyes_privacy_turn_off_writes_false(self):
        sw, mock = _spy_switch(SWITCH_TYPES["cameraeyes"], "privacymode")
        asyncio.run(sw.async_turn_off())
        mock.assert_awaited_once_with(False)

    def test_camera360_privacy_turn_on_writes_true(self):
        sw, mock = _spy_switch(SWITCH_TYPES["camera360"], "privacymode")
        asyncio.run(sw.async_turn_on())
        mock.assert_awaited_once_with(True)

    def test_cameraoutdoorgen2_privacy_turn_on_writes_true(self):
        sw, mock = _spy_switch(SWITCH_TYPES["cameraoutdoorgen2"], "privacymode")
        asyncio.run(sw.async_turn_on())
        mock.assert_awaited_once_with(True)

    def test_lightswitch_turn_on_writes_true(self):
        sw, mock = _spy_switch(SWITCH_TYPES["lightswitch"], "switchstate")
        asyncio.run(sw.async_turn_on())
        mock.assert_awaited_once_with(True)

    def test_lightswitch_turn_off_writes_false(self):
        sw, mock = _spy_switch(SWITCH_TYPES["lightswitch"], "switchstate")
        asyncio.run(sw.async_turn_off())
        mock.assert_awaited_once_with(False)

    def test_smartplugcompact_turn_on_writes_true(self):
        sw, mock = _spy_switch(SWITCH_TYPES["smartplugcompact"], "switchstate")
        asyncio.run(sw.async_turn_on())
        mock.assert_awaited_once_with(True)

    def test_smartplug_routing_turn_on_writes_true(self):
        sw, mock = _spy_switch(SWITCH_TYPES["smartplug_routing"], "routing")
        asyncio.run(sw.async_turn_on())
        mock.assert_awaited_once_with(True)

    def test_smartplug_routing_turn_off_writes_false(self):
        sw, mock = _spy_switch(SWITCH_TYPES["smartplug_routing"], "routing")
        asyncio.run(sw.async_turn_off())
        mock.assert_awaited_once_with(False)


# ---------------------------------------------------------------------------
# 5 — SHCSwitch real __init__ (not bypassed)
# ---------------------------------------------------------------------------


class TestSHCSwitchInit:
    """SHCSwitch.__init__ correctly sets unique_id and attr_name."""

    def test_init_no_attr_name_unique_id(self):
        dev = _FakeDevice()
        sw = SHCSwitch(
            device=dev,
            entry_id="entry1",
            description=SWITCH_TYPES["smartplug"],
        )
        assert sw._attr_unique_id == "root1_dev1"

    def test_init_no_attr_name_attr_name_is_none(self):
        """Primary entity: _attr_name must be None (HA uses device name)."""
        dev = _FakeDevice()
        sw = SHCSwitch(
            device=dev,
            entry_id="entry1",
            description=SWITCH_TYPES["smartplug"],
        )
        assert sw._attr_name is None

    def test_init_with_attr_name_unique_id_has_suffix(self):
        dev = _FakeDevice()
        sw = SHCSwitch(
            device=dev,
            entry_id="entry1",
            description=SWITCH_TYPES["cameraeyes_cameralight"],
            attr_name="Light",
        )
        assert sw._attr_unique_id == "root1_dev1_light"

    def test_init_with_attr_name_stores_attr_name(self):
        dev = _FakeDevice()
        sw = SHCSwitch(
            device=dev,
            entry_id="entry1",
            description=SWITCH_TYPES["cameraeyes_notification"],
            attr_name="Notification",
        )
        assert sw._attr_name == "Notification"

    def test_init_child_lock_attr_name_lowercased_in_unique_id(self):
        dev = _FakeDevice()
        sw = SHCSwitch(
            device=dev,
            entry_id="entry1",
            description=SWITCH_TYPES["child_lock"],
            attr_name="ChildLock",
        )
        assert sw._attr_unique_id == "root1_dev1_childlock"

    def test_init_pet_immunity_attr_name_lowercased(self):
        dev = _FakeDevice()
        sw = SHCSwitch(
            device=dev,
            entry_id="entry1",
            description=SWITCH_TYPES["pet_immunity_enabled"],
            attr_name="PetImmunity",
        )
        assert sw._attr_unique_id == "root1_dev1_petimmunity"

    def test_init_silent_mode_attr_name(self):
        dev = _FakeDevice()
        sw = SHCSwitch(
            device=dev,
            entry_id="entry1",
            description=SWITCH_TYPES["silent_mode"],
            attr_name="SilentMode",
        )
        assert sw._attr_unique_id == "root1_dev1_silentmode"
        assert sw._attr_name == "SilentMode"

    def test_init_entity_description_set(self):
        dev = _FakeDevice()
        sw = SHCSwitch(
            device=dev,
            entry_id="entry1",
            description=SWITCH_TYPES["bypass"],
        )
        assert sw.entity_description is SWITCH_TYPES["bypass"]


# ---------------------------------------------------------------------------
# 6 — SHCSwitch.update() delegates to device
# ---------------------------------------------------------------------------


class TestSHCSwitchUpdate:
    """SHCSwitch.update() must call self._device.update()."""

    def test_update_calls_device_update(self):
        called = []

        class _Dev:
            def update(self_):
                called.append(True)

        sw = SHCSwitch.__new__(SHCSwitch)
        sw._device = _Dev()
        sw.entity_description = SWITCH_TYPES["smartplug"]
        sw.update()
        assert called == [True]

    def test_update_camera_polling_type(self):
        """update() works for polling switches (cameras) too."""
        called = []

        class _CamDev:
            def update(self_):
                called.append("camera")

        sw = SHCSwitch.__new__(SHCSwitch)
        sw._device = _CamDev()
        sw.entity_description = SWITCH_TYPES["cameraeyes"]
        sw.update()
        assert called == ["camera"]


# ---------------------------------------------------------------------------
# 7 — SHCUserDefinedStateSwitch pure-unit (no HA runtime)
# ---------------------------------------------------------------------------


def _make_uds_switch(state=True, name="My State", dev_id="uds1", root_id="mac1"):
    """Build SHCUserDefinedStateSwitch with fake device/session/shc."""
    device = SimpleNamespace(
        name=name,
        id=dev_id,
        root_device_id=root_id,
        state=state,
        deleted=False,
    )
    shc_entry = SimpleNamespace(
        name="SHC Controller",
        id="shc_device_id",
        identifiers={("bosch_shc", "mac1")},
        manufacturer="Bosch",
        model="SHC 2",
    )
    # hass.data[DOMAIN][entry_id][DATA_SHC] = shc_entry
    hass = SimpleNamespace(
        data={"bosch_shc": {"entry1": {"shc": shc_entry}}}
    )
    # Patch hass.data so the __init__ DataSHC lookup works
    from custom_components.bosch_shc.const import DATA_SHC, DOMAIN
    hass.data = {DOMAIN: {"entry1": {DATA_SHC: shc_entry}}}

    session = SimpleNamespace(
        subscribe_userdefinedstate_callback=lambda *a, **kw: None,
        unsubscribe_userdefinedstate_callbacks=lambda *a, **kw: None,
    )

    sw = SHCUserDefinedStateSwitch(
        device=device,
        hass=hass,
        session=session,
        entry_id="entry1",
        description=SWITCH_TYPES["user_defined_state"],
    )
    return sw


class TestSHCUserDefinedStateSwitch:
    """Pure-unit tests for SHCUserDefinedStateSwitch (no HA event loop)."""

    def test_is_on_when_state_true(self):
        sw = _make_uds_switch(state=True)
        assert sw.is_on is True

    def test_is_off_when_state_false(self):
        sw = _make_uds_switch(state=False)
        assert sw.is_on is False

    def test_turn_on_sets_state_true(self):
        mock_set = AsyncMock()

        from custom_components.bosch_shc.const import DATA_SHC, DOMAIN
        device = SimpleNamespace(
            name="My State",
            id="uds1",
            root_device_id="mac1",
            deleted=False,
            state=False,
            async_set_state=mock_set,
        )
        shc_entry = SimpleNamespace(
            name="SHC", id="shcid", identifiers=set(),
            manufacturer="Bosch", model="SHC2",
        )
        hass = SimpleNamespace(data={DOMAIN: {"entry1": {DATA_SHC: shc_entry}}})
        session = SimpleNamespace(
            subscribe_userdefinedstate_callback=lambda *a, **kw: None,
            unsubscribe_userdefinedstate_callbacks=lambda *a, **kw: None,
        )
        sw = SHCUserDefinedStateSwitch(
            device=device,
            hass=hass,
            session=session,
            entry_id="entry1",
            description=SWITCH_TYPES["user_defined_state"],
        )
        asyncio.run(sw.async_turn_on())
        mock_set.assert_awaited_once_with(True)

    def test_turn_off_sets_state_false(self):
        mock_set = AsyncMock()

        from custom_components.bosch_shc.const import DATA_SHC, DOMAIN
        device = SimpleNamespace(
            name="My State",
            id="uds1",
            root_device_id="mac1",
            deleted=False,
            state=True,
            async_set_state=mock_set,
        )
        shc_entry = SimpleNamespace(
            name="SHC", id="shcid", identifiers=set(),
            manufacturer="Bosch", model="SHC2",
        )
        hass = SimpleNamespace(data={DOMAIN: {"entry1": {DATA_SHC: shc_entry}}})
        session = SimpleNamespace(
            subscribe_userdefinedstate_callback=lambda *a, **kw: None,
            unsubscribe_userdefinedstate_callbacks=lambda *a, **kw: None,
        )
        sw = SHCUserDefinedStateSwitch(
            device=device,
            hass=hass,
            session=session,
            entry_id="entry1",
            description=SWITCH_TYPES["user_defined_state"],
        )
        asyncio.run(sw.async_turn_off())
        mock_set.assert_awaited_once_with(False)

    def test_should_poll_is_false(self):
        sw = _make_uds_switch()
        assert sw.should_poll is False

    def test_entity_id_uses_slugified_name(self):
        """entity_id must be switch.userdefinedstate_<slug>."""
        sw = _make_uds_switch(name="My State")
        assert sw.entity_id == "switch.userdefinedstate_my_state"

    def test_unique_id_no_attr_name(self):
        sw = _make_uds_switch(dev_id="uds9", root_id="mac9")
        assert sw._attr_unique_id == "mac9_uds9"

    def test_device_name_from_shc(self):
        sw = _make_uds_switch()
        assert sw.device_name == "SHC Controller"

    def test_device_id_from_shc(self):
        sw = _make_uds_switch()
        assert sw.device_id == "shc_device_id"

    def test_device_info_identifiers(self):
        sw = _make_uds_switch()
        info = sw.device_info
        assert info["identifiers"] == {("bosch_shc", "mac1")}

    def test_device_info_manufacturer(self):
        sw = _make_uds_switch()
        assert sw.device_info["manufacturer"] == "Bosch"

    def test_device_info_model(self):
        sw = _make_uds_switch()
        assert sw.device_info["model"] == "SHC 2"

    def test_device_info_name(self):
        sw = _make_uds_switch()
        assert sw.device_info["name"] == "SHC Controller"

    def test_attr_name_is_device_name_for_uds(self):
        """UDS entity: _attr_name must equal the UDS state name.

        UDS entities attach to the SHC hub device (not a physical device), so
        _attr_name=None would display the hub name only, losing the state label.
        The correct fix is to use device.name (e.g. 'My State') so HA shows a
        meaningful entity name like 'SHC Controller My State'.
        """
        sw = _make_uds_switch(name="My State")
        assert sw._attr_name == "My State"

    def test_uds_update_calls_device_update(self):
        """SHCUserDefinedStateSwitch.update() must call device.update()."""
        called = []

        class _Dev:
            name = "MyState"
            id = "uds1"
            root_device_id = "mac1"
            deleted = False
            state = False

            def update(self_):
                called.append(True)

        from custom_components.bosch_shc.const import DATA_SHC, DOMAIN
        shc_entry = SimpleNamespace(
            name="SHC", id="shcid", identifiers=set(),
            manufacturer="Bosch", model="SHC2",
        )
        hass = SimpleNamespace(data={DOMAIN: {"entry1": {DATA_SHC: shc_entry}}})
        session = SimpleNamespace(
            subscribe_userdefinedstate_callback=lambda *a, **kw: None,
            unsubscribe_userdefinedstate_callbacks=lambda *a, **kw: None,
        )
        sw = SHCUserDefinedStateSwitch(
            device=_Dev(),
            hass=hass,
            session=session,
            entry_id="entry1",
            description=SWITCH_TYPES["user_defined_state"],
        )
        sw.update()
        assert called == [True]

    def test_entity_id_umlaut_slug(self):
        """Device names with umlauts must produce a valid entity_id slug."""
        sw = _make_uds_switch(name="Küche")
        assert sw.entity_id.startswith("switch.userdefinedstate_")
        # Must not contain non-slug chars
        import re
        slug_part = sw.entity_id[len("switch."):]
        assert re.match(r"^[a-z0-9_]+$", slug_part), (
            f"entity_id slug {slug_part!r} contains invalid characters"
        )


# ---------------------------------------------------------------------------
# 8 — should_poll cross-check for remaining types
# ---------------------------------------------------------------------------


class TestShouldPollRemaining:
    """should_poll for SWITCH_TYPES not covered by test_switch_unit.py."""

    def test_child_lock_should_poll_false(self):
        sw = _make_switch(SWITCH_TYPES["child_lock"], child_lock=False)
        assert sw.should_poll is False

    def test_child_lock_thermostat_should_poll_false(self):
        sw = _make_switch(SWITCH_TYPES["child_lock_thermostat"], child_lock=False)
        assert sw.should_poll is False

    def test_micromodule_relay_should_poll_false(self):
        from boschshcpy import SHCMicromoduleRelay
        State = SHCMicromoduleRelay.PowerSwitchService.State
        sw = _make_switch(SWITCH_TYPES["micromodule_relay_switch"], switchstate=State.OFF)
        assert sw.should_poll is False

    def test_lightswitch_should_poll_false(self):
        from boschshcpy import SHCLightSwitch
        State = SHCLightSwitch.PowerSwitchService.State
        sw = _make_switch(SWITCH_TYPES["lightswitch"], switchstate=State.OFF)
        assert sw.should_poll is False

    def test_smartplugcompact_should_poll_false(self):
        from boschshcpy import SHCSmartPlugCompact
        State = SHCSmartPlugCompact.PowerSwitchService.State
        sw = _make_switch(SWITCH_TYPES["smartplugcompact"], switchstate=State.OFF)
        assert sw.should_poll is False

    def test_pet_immunity_should_poll_false(self):
        sw = _make_switch(SWITCH_TYPES["pet_immunity_enabled"], pet_immunity_enabled=False)
        assert sw.should_poll is False

    def test_smartplug_routing_should_poll_false(self):
        from boschshcpy import SHCSmartPlug
        State = SHCSmartPlug.RoutingService.State
        sw = _make_switch(SWITCH_TYPES["smartplug_routing"], routing=State.DISABLED)
        assert sw.should_poll is False

    def test_cameraeyes_notification_should_poll_true(self):
        from boschshcpy import SHCCameraEyes
        State = SHCCameraEyes.CameraNotificationService.State
        sw = _make_switch(
            SWITCH_TYPES["cameraeyes_notification"],
            cameranotification=State.DISABLED,
        )
        assert sw.should_poll is True

    def test_camera360_notification_should_poll_true(self):
        State = SHCCamera360.CameraNotificationService.State
        sw = _make_switch(
            SWITCH_TYPES["camera360_notification"],
            cameranotification=State.DISABLED,
        )
        assert sw.should_poll is True

    def test_cameraoutdoorgen2_frontlight_should_poll_true(self):
        State = SHCCameraOutdoorGen2.CameraFrontLightService.State
        sw = _make_switch(
            SWITCH_TYPES["cameraoutdoorgen2_camerafrontlight"],
            camerafrontlight=State.OFF,
        )
        assert sw.should_poll is True

    def test_cameraoutdoorgen2_ambientlight_should_poll_true(self):
        State = SHCCameraOutdoorGen2.CameraAmbientLightService.State
        sw = _make_switch(
            SWITCH_TYPES["cameraoutdoorgen2_cameraambientlight"],
            cameraambientlight=State.OFF,
        )
        assert sw.should_poll is True
