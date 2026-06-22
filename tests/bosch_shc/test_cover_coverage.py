"""Supplemental pure-unit tests for cover.py — additional branch coverage.

Covers branches and paths not reached by the three existing cover unit-test files
(test_cover_unit.py, test_cover_position.py, test_cover_position_moving.py):

- _update_attr: STOPPED for non-BBL/non-MICROMODULE model + _app_command=False
  (neither inner branch fires → only last_position=None init)
- _update_attr: STOPPED → _last_position is NOT None (skip the None-init branch)
- _update_attr: BBL MOVING with _last_position is None (no direction set)
- _update_attr: BBL MOVING with target == last_position (neither flag changes)
- _update_attr: BBL MOVING opening direction (target > last)
- _update_attr: MICROMODULE_SHUTTER MOVING SWITCH_ON keycode==1 (open via keypad)
- _update_attr: MICROMODULE_SHUTTER MOVING SWITCH_ON keycode==2 (close via keypad)
- _update_attr: MICROMODULE_SHUTTER MOVING else-branch, _last_position is None
- _update_attr: MICROMODULE_SHUTTER MOVING else-branch, target == last
- _update_attr: MICROMODULE_SHUTTER MOVING else-branch, target > last (opening)
- _update_attr: MICROMODULE_SHUTTER MOVING else-branch, target < last (closing)
- _update_attr: MICROMODULE_BLINDS MOVING, _last_position is None
- _update_attr: MICROMODULE_BLINDS MOVING, target > last (opening)
- _update_attr: MICROMODULE_BLINDS MOVING, target < last (closing)
- _update_attr: MICROMODULE_BLINDS MOVING, target == last (no change)
- _update_attr: CALIBRATING / OPENING / CLOSING states (neither if fires)
- _update_attr: _attr_current_cover_position is set from current_cover_position
- current_cover_position: MICROMODULE_SHUTTER MOVING with _target_position set
- BlindsControlCover._update_attr: MOVING → _attr_current_cover_position uses level

Pattern: __new__ bypass + SimpleNamespace device mocks. No HA harness.

Run:
  PYTHONPATH="<boschshc-hass>:<boschshcpy>" PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \\
    python3 -m pytest tests/bosch_shc/test_cover_coverage.py -q -o addopts=""
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from boschshcpy import SHCShutterControl, SHCMicromoduleShutterControl

from custom_components.bosch_shc.cover import ShutterControlCover, BlindsControlCover
from homeassistant.components.cover import ATTR_POSITION

STOPPED = SHCShutterControl.ShutterControlService.State.STOPPED
MOVING = SHCShutterControl.ShutterControlService.State.MOVING
CALIBRATING = SHCShutterControl.ShutterControlService.State.CALIBRATING
OPENING = SHCShutterControl.ShutterControlService.State.OPENING
CLOSING = SHCShutterControl.ShutterControlService.State.CLOSING
SWITCH_ON = SHCMicromoduleShutterControl.KeypadService.KeyEvent.SWITCH_ON
SWITCH_OFF = SHCMicromoduleShutterControl.KeypadService.KeyEvent.SWITCH_OFF
PRESS_SHORT = SHCMicromoduleShutterControl.KeypadService.KeyEvent.PRESS_SHORT


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cover(device_model, level, operation_state, eventtype=None, keycode=None):
    """Build a ShutterControlCover via __new__, bypassing SHCEntity.__init__."""
    cover = ShutterControlCover.__new__(ShutterControlCover)
    cover._device = SimpleNamespace(
        device_model=device_model,
        level=level,
        operation_state=operation_state,
        eventtype=eventtype,
        keycode=keycode,
        name="test-cover",
        async_stop=AsyncMock(),
        async_set_level=AsyncMock(),
    )
    cover._current_operation_state = None
    cover._target_position = None
    cover._last_position = None
    cover._skip_update = False
    cover._app_command = False
    cover._attr_is_opening = None
    cover._attr_is_closing = None
    cover._attr_current_cover_position = None
    return cover


class _BlindsDevice:
    """Device double for BlindsControlCover — tracks stop_blinds calls."""

    def __init__(self, blinds_level=0.5, level=0.5, operation_state=STOPPED,
                 current_angle=0.5):
        self.device_model = "MICROMODULE_BLINDS"
        self.level = level
        self.blinds_level = blinds_level
        self.operation_state = operation_state
        self.current_angle = current_angle
        self.name = "test-blinds"
        self.target_angle = None
        self._stop_blinds_calls = []

    def stop_blinds(self):
        self._stop_blinds_calls.append(1)


def _make_blinds(blinds_level=0.5, level=0.5, operation_state=STOPPED,
                 current_angle=0.5):
    """Build a BlindsControlCover via __new__, bypassing SHCEntity.__init__."""
    cover = BlindsControlCover.__new__(BlindsControlCover)
    cover._device = _BlindsDevice(
        blinds_level=blinds_level,
        level=level,
        operation_state=operation_state,
        current_angle=current_angle,
    )
    cover._current_operation_state = None
    cover._target_position = None
    cover._last_position = None
    cover._skip_update = False
    cover._app_command = False
    cover._attr_is_opening = None
    cover._attr_is_closing = None
    cover._attr_current_cover_position = None
    return cover


# ---------------------------------------------------------------------------
# _update_attr — STOPPED: non-BBL/non-MICROMODULE device, _app_command=False
# The inner "if device_model in (...) or _app_command" branch does NOT fire,
# so _last_position stays None until the None-init branch sets it.
# ---------------------------------------------------------------------------

class TestStoppedNonBBLNonMMNoAppCommand:
    def test_last_position_initialised_from_level_when_none(self):
        """STOPPED + UNKNOWN_MODEL + _app_command=False → _last_position set by None-init."""
        cover = _make_cover(
            device_model="UNKNOWN_MODEL",
            level=0.45,
            operation_state=STOPPED,
        )
        assert cover._last_position is None
        cover._update_attr()
        # round(0.45 * 100) = 45
        # BBL/MICROMODULE branch did NOT fire; None-init branch (line 130) did
        assert cover._last_position == 45

    def test_existing_last_position_not_overwritten(self):
        """STOPPED + UNKNOWN_MODEL + _last_position already set → no overwrite."""
        cover = _make_cover(
            device_model="UNKNOWN_MODEL",
            level=0.45,
            operation_state=STOPPED,
        )
        cover._last_position = 77  # pre-set: the None-init branch must skip
        cover._update_attr()
        assert cover._last_position == 77

    def test_is_closing_and_is_opening_cleared(self):
        """STOPPED always sets is_closing=False, is_opening=False."""
        cover = _make_cover(
            device_model="UNKNOWN_MODEL",
            level=0.5,
            operation_state=STOPPED,
        )
        cover._attr_is_opening = True
        cover._attr_is_closing = True
        cover._update_attr()
        assert cover._attr_is_closing is False
        assert cover._attr_is_opening is False

    def test_attr_current_cover_position_set(self):
        """_update_attr must cache current_cover_position into _attr_current_cover_position."""
        cover = _make_cover(
            device_model="BBL",
            level=0.72,
            operation_state=STOPPED,
        )
        cover._update_attr()
        # BBL: round(0.72 * 100) = 72
        assert cover._attr_current_cover_position == 72


# ---------------------------------------------------------------------------
# _update_attr — STOPPED: _last_position already set → None-init branch skipped
# ---------------------------------------------------------------------------

class TestStoppedLastPositionAlreadySet:
    def test_bbl_stopped_does_not_reinit_last_position_when_already_set(self):
        """BBL STOPPED with _last_position pre-set must not reinitialise it to None."""
        cover = _make_cover(device_model="BBL", level=0.3, operation_state=STOPPED)
        # After open_cover the position is 100; on STOPPED update we expect 30
        cover._last_position = 100
        cover._skip_update = False
        cover._update_attr()
        # BBL + skip_update=False → inner branch fires, sets to current_cover_position=30
        assert cover._last_position == 30

    def test_unknown_model_stopped_last_position_already_set_no_overwrite(self):
        """UNKNOWN_MODEL STOPPED, _last_position=55 → stays 55 (none of the update branches fire)."""
        cover = _make_cover(device_model="UNKNOWN_MODEL", level=0.3, operation_state=STOPPED)
        cover._last_position = 55
        cover._skip_update = False
        cover._app_command = False
        cover._update_attr()
        assert cover._last_position == 55


# ---------------------------------------------------------------------------
# _update_attr — MOVING BBL: various _last_position / target comparisons
# ---------------------------------------------------------------------------

class TestBBLMoving:
    def test_last_position_none_no_direction_set(self):
        """BBL MOVING + _last_position is None → direction flags unchanged (None)."""
        cover = _make_cover(device_model="BBL", level=0.6, operation_state=MOVING)
        cover._last_position = None
        cover._attr_is_opening = None
        cover._attr_is_closing = None
        cover._update_attr()
        # target_position is set: round(0.6*100) = 60
        assert cover._target_position == 60
        # but no direction because last_position is None
        assert cover._attr_is_opening is None
        assert cover._attr_is_closing is None

    def test_target_equals_last_no_flag_change(self):
        """BBL MOVING with target == last_position → neither flag changes."""
        cover = _make_cover(device_model="BBL", level=0.5, operation_state=MOVING)
        cover._last_position = 50  # same as round(0.5*100)
        cover._attr_is_opening = True  # pre-existing value
        cover._attr_is_closing = False
        cover._update_attr()
        # neither branch (> nor <) fires
        assert cover._attr_is_opening is True
        assert cover._attr_is_closing is False

    def test_opening_direction_target_above_last(self):
        """BBL MOVING target > last → is_opening=True, is_closing=False."""
        cover = _make_cover(device_model="BBL", level=0.8, operation_state=MOVING)
        cover._last_position = 20
        cover._update_attr()
        assert cover._attr_is_opening is True
        assert cover._attr_is_closing is False

    def test_target_position_updated_from_level(self):
        """BBL MOVING → _target_position updated to round(level*100)."""
        cover = _make_cover(device_model="BBL", level=0.37, operation_state=MOVING)
        cover._last_position = 90
        cover._update_attr()
        assert cover._target_position == 37


# ---------------------------------------------------------------------------
# _update_attr — MOVING MICROMODULE_SHUTTER keypad keycode 1 (SWITCH_ON open)
# ---------------------------------------------------------------------------

class TestMicromoduleShutterMovingKeypadOpen:
    def test_switch_on_keycode1_sets_opening(self):
        """SWITCH_ON keycode=1 → is_opening=True, target=100, last_position set."""
        cover = _make_cover(
            device_model="MICROMODULE_SHUTTER",
            level=0.2,
            operation_state=MOVING,
            eventtype=SWITCH_ON,
            keycode=1,
        )
        cover._last_position = 20  # should be overwritten
        cover._update_attr()
        assert cover._attr_is_opening is True
        assert cover._attr_is_closing is False
        assert cover._target_position == 100
        # last_position updated from level: round(0.2*100) = 20
        assert cover._last_position == 20

    def test_switch_on_keycode1_level_saved_to_last_position(self):
        """Keypad SWITCH_ON keycode=1: _last_position is set from level."""
        cover = _make_cover(
            device_model="MICROMODULE_SHUTTER",
            level=0.35,
            operation_state=MOVING,
            eventtype=SWITCH_ON,
            keycode=1,
        )
        cover._update_attr()
        assert cover._last_position == 35  # round(0.35*100)


# ---------------------------------------------------------------------------
# _update_attr — MOVING MICROMODULE_SHUTTER keypad keycode 2 (SWITCH_ON close)
# ---------------------------------------------------------------------------

class TestMicromoduleShutterMovingKeypadClose:
    def test_switch_on_keycode2_sets_closing(self):
        """SWITCH_ON keycode=2 → is_closing=True, target=0, last_position set."""
        cover = _make_cover(
            device_model="MICROMODULE_SHUTTER",
            level=0.8,
            operation_state=MOVING,
            eventtype=SWITCH_ON,
            keycode=2,
        )
        cover._update_attr()
        assert cover._attr_is_closing is True
        assert cover._attr_is_opening is False
        assert cover._target_position == 0
        assert cover._last_position == 80  # round(0.8*100)

    def test_switch_on_keycode2_level_saved_to_last_position(self):
        """Keypad SWITCH_ON keycode=2: _last_position reflects level."""
        cover = _make_cover(
            device_model="MICROMODULE_SHUTTER",
            level=0.65,
            operation_state=MOVING,
            eventtype=SWITCH_ON,
            keycode=2,
        )
        cover._update_attr()
        assert cover._last_position == 65


# ---------------------------------------------------------------------------
# _update_attr — MOVING MICROMODULE_SHUTTER else-branch (not SWITCH_ON)
# Various _last_position / target comparisons
# ---------------------------------------------------------------------------

class TestMicromoduleShutterMovingElseBranch:
    def test_last_position_none_no_direction(self):
        """else-branch + _last_position is None → no direction flags updated."""
        cover = _make_cover(
            device_model="MICROMODULE_SHUTTER",
            level=0.6,
            operation_state=MOVING,
            eventtype=PRESS_SHORT,
            keycode=1,
        )
        cover._last_position = None
        cover._attr_is_opening = None
        cover._attr_is_closing = None
        cover._update_attr()
        assert cover._target_position == 60  # round(0.6*100)
        # no direction because last_position is None
        assert cover._attr_is_opening is None
        assert cover._attr_is_closing is None

    def test_target_equals_last_no_flag_change(self):
        """else-branch + target == last → flags unchanged."""
        cover = _make_cover(
            device_model="MICROMODULE_SHUTTER",
            level=0.5,
            operation_state=MOVING,
            eventtype=PRESS_SHORT,
            keycode=1,
        )
        cover._last_position = 50
        cover._attr_is_opening = False
        cover._attr_is_closing = True
        cover._update_attr()
        assert cover._attr_is_opening is False
        assert cover._attr_is_closing is True

    def test_target_above_last_sets_opening(self):
        """else-branch + target > last → is_opening=True."""
        cover = _make_cover(
            device_model="MICROMODULE_SHUTTER",
            level=0.9,
            operation_state=MOVING,
            eventtype=PRESS_SHORT,
            keycode=1,
        )
        cover._last_position = 40
        cover._update_attr()
        assert cover._attr_is_opening is True
        assert cover._attr_is_closing is False

    def test_target_below_last_sets_closing(self):
        """else-branch + target < last → is_closing=True."""
        cover = _make_cover(
            device_model="MICROMODULE_SHUTTER",
            level=0.1,
            operation_state=MOVING,
            eventtype=PRESS_SHORT,
            keycode=2,
        )
        cover._last_position = 70
        cover._update_attr()
        assert cover._attr_is_closing is True
        assert cover._attr_is_opening is False


# ---------------------------------------------------------------------------
# _update_attr — MOVING MICROMODULE_BLINDS various last_position / target cases
# ---------------------------------------------------------------------------

class TestMicromoduleBlindsMoving:
    def test_last_position_none_no_direction(self):
        """MICROMODULE_BLINDS MOVING + _last_position is None → no direction change."""
        cover = _make_cover(
            device_model="MICROMODULE_BLINDS",
            level=0.7,
            operation_state=MOVING,
        )
        cover._last_position = None
        cover._attr_is_opening = None
        cover._attr_is_closing = None
        cover._update_attr()
        assert cover._target_position == 70
        assert cover._attr_is_opening is None
        assert cover._attr_is_closing is None

    def test_target_above_last_sets_opening(self):
        """MICROMODULE_BLINDS MOVING target > last → is_opening=True."""
        cover = _make_cover(
            device_model="MICROMODULE_BLINDS",
            level=0.8,
            operation_state=MOVING,
        )
        cover._last_position = 30
        cover._update_attr()
        assert cover._attr_is_opening is True
        assert cover._attr_is_closing is False

    def test_target_below_last_sets_closing(self):
        """MICROMODULE_BLINDS MOVING target < last → is_closing=True."""
        cover = _make_cover(
            device_model="MICROMODULE_BLINDS",
            level=0.1,
            operation_state=MOVING,
        )
        cover._last_position = 80
        cover._update_attr()
        assert cover._attr_is_closing is True
        assert cover._attr_is_opening is False

    def test_target_equals_last_no_direction_change(self):
        """MICROMODULE_BLINDS MOVING target == last → flags unchanged."""
        cover = _make_cover(
            device_model="MICROMODULE_BLINDS",
            level=0.5,
            operation_state=MOVING,
        )
        cover._last_position = 50
        cover._attr_is_opening = True
        cover._attr_is_closing = False
        cover._update_attr()
        assert cover._attr_is_opening is True
        assert cover._attr_is_closing is False

    def test_target_position_set_from_level(self):
        """MICROMODULE_BLINDS MOVING → _target_position = round(level*100)."""
        cover = _make_cover(
            device_model="MICROMODULE_BLINDS",
            level=0.44,
            operation_state=MOVING,
        )
        cover._last_position = 50
        cover._update_attr()
        assert cover._target_position == 44


# ---------------------------------------------------------------------------
# _update_attr — CALIBRATING / OPENING / CLOSING states
# These fall through both STOPPED and MOVING branches → no flag changes.
# ---------------------------------------------------------------------------

class TestNonStoppedNonMovingStates:
    @pytest.mark.parametrize("state", [CALIBRATING])
    def test_other_state_does_not_alter_flags(self, state):
        """CALIBRATING: neither branch fires, flags unchanged. (OPENING/CLOSING now
        set the direction flags via the Shutter-II handler — see issue #100 and
        test_cover.py::TestShutterIIOperationStateDirection.)"""
        cover = _make_cover(device_model="BBL", level=0.5, operation_state=state)
        cover._attr_is_opening = True
        cover._attr_is_closing = False
        cover._last_position = 50
        cover._update_attr()
        # flags must be untouched — neither STOPPED nor MOVING branch ran
        assert cover._attr_is_opening is True
        assert cover._attr_is_closing is False
        # _current_operation_state must be refreshed
        assert cover._current_operation_state == state

    @pytest.mark.parametrize("state", [CALIBRATING, OPENING, CLOSING])
    def test_other_state_caches_current_cover_position(self, state):
        """_attr_current_cover_position is always refreshed, regardless of state."""
        cover = _make_cover(device_model="BBL", level=0.33, operation_state=state)
        cover._attr_current_cover_position = 0
        cover._last_position = 50
        cover._update_attr()
        assert cover._attr_current_cover_position == 33  # round(0.33*100)


# ---------------------------------------------------------------------------
# current_cover_position — MICROMODULE_SHUTTER MOVING with _target_position set
# (Existing test_moving_returns_target_when_set covers this but only for the
# generic helper; here we make the operation_state explicit.)
# ---------------------------------------------------------------------------

class TestMicromoduleShutterCurrentPositionMovingTargetSet:
    def test_moving_with_target_position_returns_target(self):
        """MICROMODULE_SHUTTER MOVING + _target_position != None → return _target_position."""
        cover = _make_cover(
            device_model="MICROMODULE_SHUTTER",
            level=0.2,
            operation_state=MOVING,
        )
        cover._target_position = 75
        result = cover.current_cover_position
        assert result == 75

    def test_moving_with_target_position_zero_returns_zero(self):
        """_target_position=0 is not None → must return 0, not fall back to level."""
        cover = _make_cover(
            device_model="MICROMODULE_SHUTTER",
            level=0.9,
            operation_state=MOVING,
        )
        cover._target_position = 0
        assert cover.current_cover_position == 0

    def test_stopped_ignores_target_uses_level(self):
        """MICROMODULE_SHUTTER STOPPED → always uses device.level, ignores _target_position."""
        cover = _make_cover(
            device_model="MICROMODULE_SHUTTER",
            level=0.6,
            operation_state=STOPPED,
        )
        cover._target_position = 99
        assert cover.current_cover_position == 60  # round(0.6*100)


# ---------------------------------------------------------------------------
# BlindsControlCover._update_attr caches ShutterControl.level into
# _attr_current_cover_position (issue #100 — NOT blinds_level/BlindsSceneControl)
# ---------------------------------------------------------------------------

class TestBlindsUpdateAttrCachesLevel:
    def test_stopped_attr_current_cover_position_uses_level(self):
        """BlindsControlCover STOPPED: _attr_current_cover_position reflects
        ShutterControl.level (the live lift), not blinds_level (#100)."""
        cover = _make_blinds(blinds_level=0.6, level=0.3, operation_state=STOPPED)
        cover._update_attr()
        # current_cover_position uses ShutterControl.level → round(0.3*100)
        assert cover._attr_current_cover_position == 30

    def test_moving_attr_current_cover_position_uses_level(self):
        """BlindsControlCover MOVING: _attr_current_cover_position uses
        ShutterControl.level (#100), independent of blinds_level."""
        cover = _make_blinds(blinds_level=0.4, level=0.9, operation_state=MOVING)
        cover._last_position = 20  # direction: 90 > 20 → opening
        cover._update_attr()
        assert cover._attr_current_cover_position == 90  # round(0.9*100)
        # MOVING MICROMODULE_BLINDS branch: target also from device.level
        assert cover._target_position == 90  # round(0.9*100)

    def test_blinds_current_cover_position_rounds_correctly(self):
        """round(0.333 * 100) == 33 (from ShutterControl.level)."""
        cover = _make_blinds(blinds_level=0.5, level=0.333)
        assert cover.current_cover_position == 33


# ---------------------------------------------------------------------------
# MICROMODULE_SHUTTER STOPPED: direction from both BBL branch AND MICROMODULE_SHUTTER
# Verify that a MICROMODULE_SHUTTER STOPPED (non-BBL, not app_command) does update
# _last_position because MICROMODULE_SHUTTER is in the allowed-model set.
# ---------------------------------------------------------------------------

class TestMicromoduleShutterStoppedUpdatesLastPosition:
    def test_micromodule_shutter_stopped_updates_last_position(self):
        """MICROMODULE_SHUTTER STOPPED + _skip_update=False → _last_position refreshed."""
        cover = _make_cover(
            device_model="MICROMODULE_SHUTTER",
            level=0.55,
            operation_state=STOPPED,
        )
        cover._skip_update = False
        cover._app_command = False
        cover._last_position = 10  # pre-set, will be updated
        cover._update_attr()
        assert cover._last_position == 55  # round(0.55*100)

    def test_micromodule_shutter_stopped_app_command_false_clears_app_command(self):
        """MICROMODULE_SHUTTER STOPPED + _skip_update=False: _app_command stays False."""
        cover = _make_cover(
            device_model="MICROMODULE_SHUTTER",
            level=0.55,
            operation_state=STOPPED,
        )
        cover._skip_update = False
        cover._app_command = False
        cover._update_attr()
        assert cover._app_command is False

    def test_micromodule_blinds_stopped_updates_last_position(self):
        """MICROMODULE_BLINDS STOPPED + _skip_update=False → _last_position refreshed."""
        cover = _make_cover(
            device_model="MICROMODULE_BLINDS",
            level=0.33,
            operation_state=STOPPED,
        )
        cover._skip_update = False
        cover._last_position = 90
        cover._update_attr()
        assert cover._last_position == 33  # round(0.33*100)


# ---------------------------------------------------------------------------
# Edge cases: rounding at 0.5 (Python banker's rounding)
# ---------------------------------------------------------------------------

class TestRoundingEdgeCases:
    def test_bbl_current_cover_position_rounds_half(self):
        """round(0.5 * 100) = 50 (exact)."""
        cover = _make_cover(device_model="BBL", level=0.5, operation_state=STOPPED)
        assert cover.current_cover_position == 50

    def test_bbl_current_cover_position_rounds_down_at_0_994(self):
        """round(0.994 * 100) = 99."""
        cover = _make_cover(device_model="BBL", level=0.994, operation_state=STOPPED)
        assert cover.current_cover_position == 99

    def test_micromodule_shutter_level_zero_stopped_is_closed(self):
        """level=0.0 STOPPED MICROMODULE_SHUTTER → is_closed == True."""
        cover = _make_cover(
            device_model="MICROMODULE_SHUTTER",
            level=0.0,
            operation_state=STOPPED,
        )
        assert cover.is_closed is True

    def test_micromodule_shutter_level_zero_moving_not_closed(self):
        """level=0.0 MOVING → is_closed == False (MOVING, not STOPPED)."""
        cover = _make_cover(
            device_model="MICROMODULE_SHUTTER",
            level=0.0,
            operation_state=MOVING,
        )
        assert cover.is_closed is False


# ---------------------------------------------------------------------------
# async_set_cover_position — non-MICROMODULE_SHUTTER (BBL): no keypad_switch_off
# call, no _last_position save (covers the else-path of the device_model check).
# ---------------------------------------------------------------------------

class TestSetCoverPositionBBLBranch:
    def test_bbl_set_cover_position_does_not_save_last_position(self):
        """BBL async_set_cover_position skips the MICROMODULE_SHUTTER last-position block."""
        cover = _make_cover(device_model="BBL", level=0.5, operation_state=STOPPED)
        cover._last_position = 99  # should stay untouched
        asyncio.run(cover.async_set_cover_position(**{ATTR_POSITION: 70}))
        # BBL path does NOT call _micromodule_keypad_switch_off or save last_position
        assert cover._last_position == 99
        assert cover._target_position == 70
        cover._device.async_set_level.assert_awaited_once_with(pytest.approx(0.70))
        assert cover._skip_update is True
        assert cover._app_command is True

    def test_bbl_set_cover_position_boundary_zero(self):
        """async_set_cover_position(0) → async_set_level(0.0)."""
        cover = _make_cover(device_model="BBL", level=0.5, operation_state=STOPPED)
        asyncio.run(cover.async_set_cover_position(**{ATTR_POSITION: 0}))
        cover._device.async_set_level.assert_awaited_once_with(pytest.approx(0.0))
        assert cover._target_position == 0

    def test_bbl_set_cover_position_boundary_100(self):
        """async_set_cover_position(100) → async_set_level(1.0)."""
        cover = _make_cover(device_model="BBL", level=0.0, operation_state=STOPPED)
        asyncio.run(cover.async_set_cover_position(**{ATTR_POSITION: 100}))
        cover._device.async_set_level.assert_awaited_once_with(pytest.approx(1.0))
        assert cover._target_position == 100


# ---------------------------------------------------------------------------
# BlindsControlCover supported_features — accessed via instance (HA wraps as property)
# ---------------------------------------------------------------------------

class TestBlindsControlCoverSupportedFeatures:
    def test_supported_features_includes_tilt(self):
        """BlindsControlCover must advertise tilt features."""
        from homeassistant.components.cover import CoverEntityFeature
        cover = _make_blinds()
        features = cover.supported_features
        assert features & CoverEntityFeature.OPEN_TILT
        assert features & CoverEntityFeature.CLOSE_TILT
        assert features & CoverEntityFeature.SET_TILT_POSITION
        assert features & CoverEntityFeature.STOP_TILT

    def test_shutter_supported_features_no_tilt(self):
        """ShutterControlCover must NOT advertise tilt features."""
        from homeassistant.components.cover import CoverEntityFeature
        cover = _make_cover(device_model="BBL", level=0.5, operation_state=STOPPED)
        features = cover.supported_features
        assert not (features & CoverEntityFeature.OPEN_TILT)
        assert not (features & CoverEntityFeature.CLOSE_TILT)


# ---------------------------------------------------------------------------
# N1: PARALLEL_UPDATES = 1 module-level constant (serialises HA update calls)
# ---------------------------------------------------------------------------

def test_parallel_updates_is_one():
    """cover.py must declare PARALLEL_UPDATES = 1 at module level (N1 requirement)."""
    import custom_components.bosch_shc.cover as cover_module
    assert hasattr(cover_module, "PARALLEL_UPDATES"), (
        "cover.py is missing module-level PARALLEL_UPDATES"
    )
    assert cover_module.PARALLEL_UPDATES == 1
