"""Regression tests for SHCSwitch when the underlying boschshcpy service is None.

Covers:
- Issue #185: MicromoduleRelay turn_on/turn_off raise AttributeError when no
  load is connected (PowerSwitchService is None → switchstate setter crashes).
- Issue #206: Camera360 turn_on/turn_off raise AttributeError when
  PrivacyModeService is None → privacymode setter crashes.
- Issue #246: silent_mode on_value / service mapping verified as correct; no
  crash path exists (silentmode setter already guards for None service).
"""

from types import SimpleNamespace

from boschshcpy import SHCThermostat

from custom_components.bosch_shc.switch import SHCSwitch, SWITCH_TYPES


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
    """Return a SimpleNamespace property descriptor that raises on access/set."""

    class _Raiser:
        def __get__(self, obj, objtype=None):
            raise exc_type("service is None")

        def __set__(self, obj, value):
            raise exc_type("service is None")

    return _Raiser()


# ---------------------------------------------------------------------------
# Issue #185 — MicromoduleRelay with no load (PowerSwitch service is None)
# ---------------------------------------------------------------------------


class _RelayNoLoad:
    """Simulates a MicromoduleRelay whose PowerSwitch service is None.

    Both the getter and setter of `switchstate` raise AttributeError, which is
    what boschshcpy does when `self._powerswitch_service` is None.
    """

    switchstate = _raising_property()


def test_relay_no_load_is_on_returns_none():
    """is_on must return None (not raise) when switchstate getter raises."""
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = _RelayNoLoad()
    sw.entity_description = SWITCH_TYPES["micromodule_relay_switch"]
    assert sw.is_on is None


def test_relay_no_load_turn_on_does_not_raise():
    """turn_on must NOT propagate AttributeError when service is None."""
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = _RelayNoLoad()
    sw.entity_description = SWITCH_TYPES["micromodule_relay_switch"]
    sw.entity_id = "switch.relay_test"
    sw.turn_on()  # must not raise


def test_relay_no_load_turn_off_does_not_raise():
    """turn_off must NOT propagate AttributeError when service is None."""
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = _RelayNoLoad()
    sw.entity_description = SWITCH_TYPES["micromodule_relay_switch"]
    sw.entity_id = "switch.relay_test"
    sw.turn_off()  # must not raise


def test_relay_with_load_turn_on_calls_setter():
    """turn_on must write True to switchstate when the service exists."""
    calls = []

    class _RelayWithLoad:
        @property
        def switchstate(self):
            return "OFF"

        @switchstate.setter
        def switchstate(self, value):
            calls.append(value)

    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = _RelayWithLoad()
    sw.entity_description = SWITCH_TYPES["micromodule_relay_switch"]
    sw.entity_id = "switch.relay_loaded"
    sw.turn_on()
    assert calls == [True]


def test_relay_with_load_turn_off_calls_setter():
    """turn_off must write False to switchstate when the service exists."""
    calls = []

    class _RelayWithLoad:
        @property
        def switchstate(self):
            return "ON"

        @switchstate.setter
        def switchstate(self, value):
            calls.append(value)

    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = _RelayWithLoad()
    sw.entity_description = SWITCH_TYPES["micromodule_relay_switch"]
    sw.entity_id = "switch.relay_loaded"
    sw.turn_off()
    assert calls == [False]


# ---------------------------------------------------------------------------
# Issue #206 — Camera360 with no PrivacyMode service
# ---------------------------------------------------------------------------


class _Camera360NoPrivacy:
    """Simulates SHCCamera360 where _privacymode_service is None.

    boschshcpy's privacymode getter/setter both crash with AttributeError
    when _privacymode_service is None (no guard in the Camera360 class).
    """

    privacymode = _raising_property()


def test_camera360_no_privacy_service_is_on_returns_none():
    """is_on must return None (not raise) when privacymode getter raises."""
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = _Camera360NoPrivacy()
    sw.entity_description = SWITCH_TYPES["camera360"]
    assert sw.is_on is None


def test_camera360_no_privacy_service_turn_on_does_not_raise():
    """turn_on must NOT propagate AttributeError when privacy service is None."""
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = _Camera360NoPrivacy()
    sw.entity_description = SWITCH_TYPES["camera360"]
    sw.entity_id = "switch.cam360_test"
    sw.turn_on()  # must not raise


def test_camera360_no_privacy_service_turn_off_does_not_raise():
    """turn_off must NOT propagate AttributeError when privacy service is None."""
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = _Camera360NoPrivacy()
    sw.entity_description = SWITCH_TYPES["camera360"]
    sw.entity_id = "switch.cam360_test"
    sw.turn_off()  # must not raise


def test_camera360_notification_none_is_on_returns_none():
    """is_on for camera360_notification must return None when service getter raises."""
    sw = SHCSwitch.__new__(SHCSwitch)

    class _NoNotification:
        cameranotification = _raising_property()

    sw._device = _NoNotification()
    sw.entity_description = SWITCH_TYPES["camera360_notification"]
    assert sw.is_on is None


def test_camera360_notification_none_turn_on_does_not_raise():
    """turn_on for camera360_notification must not raise when service is absent."""
    sw = SHCSwitch.__new__(SHCSwitch)

    class _NoNotification:
        cameranotification = _raising_property()

    sw._device = _NoNotification()
    sw.entity_description = SWITCH_TYPES["camera360_notification"]
    sw.entity_id = "switch.cam360_notif"
    sw.turn_on()  # must not raise


# ---------------------------------------------------------------------------
# Issue #246 — silent_mode on_value / mapping verification
# ---------------------------------------------------------------------------


def test_silent_mode_on_value_is_mode_silent_enum():
    """on_value for silent_mode must be the MODE_SILENT enum member."""
    desc = SWITCH_TYPES["silent_mode"]
    assert desc.on_key == "silentmode"
    assert desc.on_value is SHCThermostat.SilentModeService.State.MODE_SILENT


def test_silent_mode_is_on_true_when_mode_silent():
    """is_on returns True when device.silentmode == MODE_SILENT."""
    State = SHCThermostat.SilentModeService.State
    sw = _make_switch(SWITCH_TYPES["silent_mode"], silentmode=State.MODE_SILENT)
    assert sw.is_on is True


def test_silent_mode_is_on_false_when_mode_normal():
    """is_on returns False when device.silentmode == MODE_NORMAL."""
    State = SHCThermostat.SilentModeService.State
    sw = _make_switch(SWITCH_TYPES["silent_mode"], silentmode=State.MODE_NORMAL)
    assert sw.is_on is False
