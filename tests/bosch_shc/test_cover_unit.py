"""Unit tests for cover.py — missing branch coverage.

Covers lines not reached by test_cover.py or test_cover_position.py:
- _micromodule_keypad_switch_off MICROMODULE_SHUTTER branch (94-96)
- BBL STOPPED with _skip_update=False (113-117)
- BBL MOVING closing direction (133-135)
- Unknown device model MOVING (178-180)
- device_class MICROMODULE_AWNING (184)
- async_stop_cover (206-211)
- is_closed True/False (216)
- async_open_cover (224-229)
- async_close_cover (233-238)
- async_set_cover_position — MICROMODULE_SHUTTER branch (242-249)
- extra_state_attributes (254)
- BlindsControlCover async_open_cover/async_close_cover/async_set_cover_position/
  async_stop_cover/async_stop_cover_tilt/current_cover_tilt_position/
  async_open_cover_tilt/async_close_cover_tilt/async_set_cover_tilt_position
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from boschshcpy import SHCShutterControl, SHCMicromoduleShutterControl

from custom_components.bosch_shc.cover import ShutterControlCover, BlindsControlCover
from homeassistant.components.cover import ATTR_POSITION, ATTR_TILT_POSITION, CoverDeviceClass

MOVING = SHCShutterControl.ShutterControlService.State.MOVING
STOPPED = SHCShutterControl.ShutterControlService.State.STOPPED
SWITCH_ON = SHCMicromoduleShutterControl.KeypadService.KeyEvent.SWITCH_ON
SWITCH_OFF = SHCMicromoduleShutterControl.KeypadService.KeyEvent.SWITCH_OFF


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cover(device_model, level, operation_state, eventtype=None, keycode=None):
    """Build a ShutterControlCover bypassing SHCEntity.__init__."""
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
        # Present Keypad service so _micromodule_keypad_switch_off runs its
        # eventtype write (the #318 guard skips it only when this is None).
        _keypad_service=SimpleNamespace(),
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


class _TrackingDevice:
    """Device double for BlindsControlCover — async method mocks."""

    def __init__(self, device_model="MICROMODULE_BLINDS", level=0.5, blinds_level=0.5,
                 operation_state=STOPPED, current_angle=0.5):
        self.device_model = device_model
        self.level = level
        self.blinds_level = blinds_level
        self.operation_state = operation_state
        self.current_angle = current_angle
        self.name = "test-blinds"
        self.target_angle = None
        self.async_set_level = AsyncMock()
        self.async_stop_blinds = AsyncMock()
        self.async_set_target_angle = AsyncMock()


def _make_blinds(device_model="MICROMODULE_BLINDS", level=0.5, blinds_level=0.5,
                 operation_state=STOPPED, current_angle=0.5):
    """Build a BlindsControlCover bypassing SHCEntity.__init__."""
    cover = BlindsControlCover.__new__(BlindsControlCover)
    cover._device = _TrackingDevice(
        device_model=device_model,
        level=level,
        blinds_level=blinds_level,
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
# Lines 94-96: _micromodule_keypad_switch_off — MICROMODULE_SHUTTER sets SWITCH_OFF
# ---------------------------------------------------------------------------

class TestMicromoduleKepadSwitchOff:
    def test_sets_switch_off_for_micromodule_shutter(self):
        """_micromodule_keypad_switch_off must set eventtype=SWITCH_OFF for MICROMODULE_SHUTTER."""
        cover = _make_cover(
            device_model="MICROMODULE_SHUTTER",
            level=0.5,
            operation_state=STOPPED,
            eventtype=SWITCH_ON,
        )
        cover._micromodule_keypad_switch_off()
        assert cover._device.eventtype == SWITCH_OFF

    def test_no_op_for_non_micromodule(self):
        """_micromodule_keypad_switch_off must be a no-op for other models."""
        cover = _make_cover(
            device_model="BBL",
            level=0.5,
            operation_state=STOPPED,
            eventtype=SWITCH_ON,
        )
        cover._micromodule_keypad_switch_off()
        # eventtype untouched
        assert cover._device.eventtype == SWITCH_ON


# ---------------------------------------------------------------------------
# Lines 113-117: BBL STOPPED _skip_update=False → updates _last_position, clears _app_command
# ---------------------------------------------------------------------------

class TestBBLStoppedUpdateLastPosition:
    def test_bbl_stopped_skip_update_false_updates_last_position(self):
        """STOPPED + BBL + _skip_update=False → _last_position set to current_cover_position."""
        cover = _make_cover(
            device_model="BBL",
            level=0.6,
            operation_state=STOPPED,
        )
        cover._skip_update = False
        cover._app_command = False
        cover._update_attr()
        # BBL current_cover_position = round(0.6 * 100) = 60
        assert cover._last_position == 60

    def test_bbl_stopped_app_command_true_updates_last_position_and_clears_flag(self):
        """STOPPED + non-BBL + _app_command=True → _last_position updated, _app_command cleared."""
        cover = _make_cover(
            device_model="MICROMODULE_SHUTTER",
            level=0.4,
            operation_state=STOPPED,
        )
        cover._skip_update = False
        cover._app_command = True  # triggers the same branch via `or self._app_command`
        # For MICROMODULE_SHUTTER STOPPED: current_cover_position = round(0.4*100) = 40
        cover._update_attr()
        assert cover._last_position == 40
        assert cover._app_command is False

    def test_bbl_stopped_skip_update_true_resets_flag(self):
        """STOPPED + _skip_update=True → resets _skip_update to False (lines 115-117)."""
        cover = _make_cover(
            device_model="BBL",
            level=0.5,
            operation_state=STOPPED,
        )
        cover._skip_update = True
        cover._last_position = 50  # pre-set so initialisation branch is skipped
        cover._update_attr()
        assert cover._skip_update is False
        # _last_position must NOT be overwritten (skip_update branch)
        assert cover._last_position == 50


# ---------------------------------------------------------------------------
# Lines 133-135: BBL MOVING closing direction (target < last)
# ---------------------------------------------------------------------------

class TestBBLMovingClosingDirection:
    def test_bbl_moving_closing_when_target_below_last(self):
        """BBL MOVING with target < last → is_closing=True, is_opening=False."""
        cover = _make_cover(
            device_model="BBL",
            level=0.2,
            operation_state=MOVING,
        )
        cover._last_position = 80  # target (20) < last (80) → closing
        cover._update_attr()
        assert cover._attr_is_closing is True
        assert cover._attr_is_opening is False


# ---------------------------------------------------------------------------
# Lines 178-180: Unknown device model MOVING → LOGGER.debug + flags None
# ---------------------------------------------------------------------------

class TestUnknownModelMoving:
    def test_unknown_model_moving_sets_flags_to_none(self):
        """Unknown device_model during MOVING → _attr_is_closing and _attr_is_opening = None."""
        cover = _make_cover(
            device_model="UNKNOWN_MODEL",
            level=0.5,
            operation_state=MOVING,
        )
        # Pre-set flags to something non-None so we can verify they get cleared
        cover._attr_is_opening = True
        cover._attr_is_closing = False
        cover._update_attr()
        assert cover._attr_is_closing is None
        assert cover._attr_is_opening is None


# ---------------------------------------------------------------------------
# Line 184: device_class → AWNING for MICROMODULE_AWNING, SHUTTER otherwise
# ---------------------------------------------------------------------------

class TestDeviceClass:
    def test_awning_model_returns_awning(self):
        cover = _make_cover(
            device_model="MICROMODULE_AWNING",
            level=0.0,
            operation_state=STOPPED,
        )
        assert cover.device_class == CoverDeviceClass.AWNING

    def test_other_model_returns_shutter(self):
        cover = _make_cover(
            device_model="BBL",
            level=0.0,
            operation_state=STOPPED,
        )
        assert cover.device_class == CoverDeviceClass.SHUTTER


# ---------------------------------------------------------------------------
# Lines 206-211: async_stop_cover
# ---------------------------------------------------------------------------

class TestStopCover:
    def test_stop_cover_calls_device_async_stop(self):
        cover = _make_cover(
            device_model="BBL",
            level=0.5,
            operation_state=MOVING,
        )
        asyncio.run(cover.async_stop_cover())
        cover._device.async_stop.assert_awaited_once()

    def test_stop_cover_sets_flags_and_state(self):
        cover = _make_cover(
            device_model="BBL",
            level=0.5,
            operation_state=MOVING,
        )
        cover._attr_is_opening = True
        cover._attr_is_closing = False
        asyncio.run(cover.async_stop_cover())
        assert cover._attr_is_opening is False
        assert cover._attr_is_closing is False
        assert cover._skip_update is True
        assert cover._app_command is True

    def test_stop_cover_micromodule_shutter_sets_switch_off(self):
        """async_stop_cover on MICROMODULE_SHUTTER must call _micromodule_keypad_switch_off."""
        cover = _make_cover(
            device_model="MICROMODULE_SHUTTER",
            level=0.5,
            operation_state=MOVING,
            eventtype=SWITCH_ON,
        )
        asyncio.run(cover.async_stop_cover())
        assert cover._device.eventtype == SWITCH_OFF


# ---------------------------------------------------------------------------
# Line 216: is_closed — True when STOPPED + level==0.0; False otherwise
# ---------------------------------------------------------------------------

class TestIsClosed:
    def test_is_closed_true_when_stopped_and_level_zero(self):
        cover = _make_cover(
            device_model="BBL",
            level=0.0,
            operation_state=STOPPED,
        )
        assert cover.is_closed is True

    def test_is_closed_false_when_stopped_and_level_nonzero(self):
        cover = _make_cover(
            device_model="BBL",
            level=0.5,
            operation_state=STOPPED,
        )
        assert cover.is_closed is False

    def test_is_closed_false_when_moving_and_level_zero(self):
        cover = _make_cover(
            device_model="BBL",
            level=0.0,
            operation_state=MOVING,
        )
        assert cover.is_closed is False


# ---------------------------------------------------------------------------
# Lines 224-229: async_open_cover
# ---------------------------------------------------------------------------

class TestOpenCover:
    def test_open_cover_sets_level_and_flags(self):
        cover = _make_cover(
            device_model="BBL",
            level=0.0,
            operation_state=STOPPED,
        )
        asyncio.run(cover.async_open_cover())
        cover._device.async_set_level.assert_awaited_once_with(1.0)
        assert cover._attr_is_opening is True
        assert cover._target_position == 100
        assert cover._skip_update is True
        assert cover._app_command is True

    def test_open_cover_micromodule_shutter_sets_switch_off(self):
        cover = _make_cover(
            device_model="MICROMODULE_SHUTTER",
            level=0.0,
            operation_state=STOPPED,
            eventtype=SWITCH_ON,
        )
        asyncio.run(cover.async_open_cover())
        assert cover._device.eventtype == SWITCH_OFF
        cover._device.async_set_level.assert_awaited_once_with(1.0)


# ---------------------------------------------------------------------------
# Lines 233-238: async_close_cover
# ---------------------------------------------------------------------------

class TestCloseCover:
    def test_close_cover_sets_level_and_flags(self):
        cover = _make_cover(
            device_model="BBL",
            level=1.0,
            operation_state=STOPPED,
        )
        asyncio.run(cover.async_close_cover())
        cover._device.async_set_level.assert_awaited_once_with(0.0)
        assert cover._attr_is_closing is True
        assert cover._target_position == 0
        assert cover._skip_update is True
        assert cover._app_command is True

    def test_close_cover_micromodule_shutter_sets_switch_off(self):
        cover = _make_cover(
            device_model="MICROMODULE_SHUTTER",
            level=1.0,
            operation_state=STOPPED,
            eventtype=SWITCH_ON,
        )
        asyncio.run(cover.async_close_cover())
        assert cover._device.eventtype == SWITCH_OFF
        cover._device.async_set_level.assert_awaited_once_with(0.0)


# ---------------------------------------------------------------------------
# Lines 242-249: async_set_cover_position
# ---------------------------------------------------------------------------

class TestSetCoverPosition:
    def test_set_cover_position_bbl(self):
        cover = _make_cover(
            device_model="BBL",
            level=0.0,
            operation_state=STOPPED,
        )
        asyncio.run(cover.async_set_cover_position(**{ATTR_POSITION: 70}))
        cover._device.async_set_level.assert_awaited_once_with(pytest_approx(0.70))
        assert cover._target_position == 70
        assert cover._skip_update is True
        assert cover._app_command is True

    def test_set_cover_position_micromodule_shutter_saves_last_position(self):
        """MICROMODULE_SHUTTER: must call _micromodule_keypad_switch_off and save _last_position."""
        cover = _make_cover(
            device_model="MICROMODULE_SHUTTER",
            level=0.5,
            operation_state=STOPPED,
            eventtype=SWITCH_ON,
        )
        # current_cover_position for MICROMODULE_SHUTTER STOPPED = round(0.5 * 100) = 50
        asyncio.run(cover.async_set_cover_position(**{ATTR_POSITION: 80}))
        assert cover._device.eventtype == SWITCH_OFF
        assert cover._last_position == 50  # saved before setting new level
        cover._device.async_set_level.assert_awaited_once_with(pytest_approx(0.80))
        assert cover._target_position == 80
        assert cover._skip_update is True
        assert cover._app_command is True


def pytest_approx(value, rel=1e-6):
    """Thin wrapper so we don't need to import pytest.approx at module level."""
    import pytest
    return pytest.approx(value, rel=rel)


# ---------------------------------------------------------------------------
# Line 254: extra_state_attributes
# ---------------------------------------------------------------------------

class TestExtraStateAttributes:
    def test_extra_state_attributes_returns_operation_state(self):
        cover = _make_cover(
            device_model="BBL",
            level=0.5,
            operation_state=STOPPED,
        )
        attrs = cover.extra_state_attributes
        assert attrs == {"operation_state": STOPPED}


# ---------------------------------------------------------------------------
# BlindsControlCover.async_open_cover / async_close_cover
# ---------------------------------------------------------------------------

class TestBlindsOpenCloseCover:
    def test_open_cover_sets_level_and_flags(self):
        # #100: lift uses ShutterControl.level, not blinds_level
        cover = _make_blinds(level=0.0)
        asyncio.run(cover.async_open_cover())
        cover._device.async_set_level.assert_awaited_once_with(1.0)
        assert cover._attr_is_opening is True
        assert cover._attr_is_closing is False

    def test_close_cover_sets_level_and_flags(self):
        cover = _make_blinds(level=1.0)
        asyncio.run(cover.async_close_cover())
        cover._device.async_set_level.assert_awaited_once_with(0.0)
        assert cover._attr_is_closing is True
        assert cover._attr_is_opening is False


# ---------------------------------------------------------------------------
# BlindsControlCover.async_set_cover_position
# ---------------------------------------------------------------------------

class TestBlindsSetCoverPosition:
    def test_set_cover_position_uses_level(self):
        # #100: lift uses ShutterControl.level, not blinds_level
        import pytest
        cover = _make_blinds(level=0.0)
        asyncio.run(cover.async_set_cover_position(**{ATTR_POSITION: 65}))
        cover._device.async_set_level.assert_awaited_once_with(pytest.approx(0.65))

    def test_set_cover_position_fully_open(self):
        cover = _make_blinds(level=0.0)
        asyncio.run(cover.async_set_cover_position(**{ATTR_POSITION: 100}))
        cover._device.async_set_level.assert_awaited_once_with(1.0)

    def test_set_cover_position_fully_closed(self):
        cover = _make_blinds(level=1.0)
        asyncio.run(cover.async_set_cover_position(**{ATTR_POSITION: 0}))
        cover._device.async_set_level.assert_awaited_once_with(0.0)


# ---------------------------------------------------------------------------
# Regression: issue #100 — MICROMODULE_BLINDS lift position must come from
# ShutterControl.level (the live lift), NOT blinds_level (BlindsSceneControl,
# which holds the last *scene* level). Reporter-confirmed device (DEGREE_180,
# dev 6c5cb1…) sat at BlindsSceneControl.level=0.0 while fully up, so reading
# blinds_level showed 0% for a fully-open blind ("fully up shows 0%").
# ---------------------------------------------------------------------------

class TestBlindsCurrentCoverPositionIssue100:
    def test_current_cover_position_reads_shuttercontrol_level(self):
        cover = _make_blinds(level=0.42)
        # round(0.42 * 100) = 42 — taken from ShutterControl.level
        assert cover.current_cover_position == 42

    def test_fully_up_blind_reads_100_not_scene_level_zero(self):
        """The exact #100 symptom: blind fully up → ShutterControl.level=1.0
        (=100%) while BlindsSceneControl.level (blinds_level) is 0.0. Position
        must follow the live lift (100%), NOT the stale scene level (0%)."""
        cover = _make_blinds(level=1.0, blinds_level=0.0)
        assert cover.current_cover_position == 100

    def test_position_ignores_blinds_level_entirely(self):
        """Whatever the scene level is, position tracks ShutterControl.level."""
        cover = _make_blinds(level=0.6, blinds_level=0.0)
        assert cover.current_cover_position == 60
        cover = _make_blinds(level=0.0, blinds_level=1.0)
        assert cover.current_cover_position == 0

    def test_fully_open_and_closed(self):
        assert _make_blinds(level=1.0).current_cover_position == 100
        assert _make_blinds(level=0.0).current_cover_position == 0

    def test_stopped_moving_stopped_cycle_uses_level_for_reference_and_direction(self):
        """End-to-end on a real BlindsControlCover instance: _last_position and
        the MOVING direction inference must both track ShutterControl.level, not
        the stale BlindsSceneControl.level (blinds_level). Mirrors an external
        (Bosch-app / wall-switch) move where operationState is only STOPPED/
        MOVING and level jumps to the target early."""
        # 1. rest fully down: ShutterControl.level=0, scene level frozen at 1.0
        cover = _make_blinds(level=0.0, blinds_level=1.0, operation_state=STOPPED)
        cover._update_attr()
        assert cover._last_position == 0           # from level, NOT blinds_level(=100)
        assert cover._attr_current_cover_position == 0

        # 2. external UP move: level jumps to target 1.0 while MOVING
        cover._device.level = 1.0
        cover._device.operation_state = MOVING
        cover._update_attr()
        assert cover._attr_is_opening is True       # 100 > last 0 → opening
        assert cover._attr_is_closing is False
        assert cover._attr_current_cover_position == 100

        # 3. rest fully up: reference refreshes to 100 (from level)
        cover._device.operation_state = STOPPED
        cover._update_attr()
        assert cover._last_position == 100

        # 4. external DOWN move: level jumps to 0.0 while MOVING
        cover._device.level = 0.0
        cover._device.operation_state = MOVING
        cover._update_attr()
        assert cover._attr_is_closing is True       # 0 < last 100 → closing
        assert cover._attr_is_opening is False


# ---------------------------------------------------------------------------
# Regression: BlindsControlCover.async_stop_cover must call async_stop_blinds()
# ---------------------------------------------------------------------------

class TestBlindsStopCover:
    def test_stop_cover_calls_async_stop_blinds(self):
        """BlindsControlCover.async_stop_cover() must call async_stop_blinds()
        (blind endpoint), not the inherited async_stop() (ShutterControl endpoint)."""
        cover = _make_blinds()
        # Ensure there is no sync `stop` method — proves we use the async variant
        assert not hasattr(cover._device, "stop"), (
            "Test setup error: _TrackingDevice must not have a stop() method"
        )
        asyncio.run(cover.async_stop_cover())
        cover._device.async_stop_blinds.assert_awaited_once()

    def test_stop_cover_clears_opening_closing_flags(self):
        cover = _make_blinds()
        cover._attr_is_opening = True
        cover._attr_is_closing = True
        asyncio.run(cover.async_stop_cover())
        assert cover._attr_is_opening is False
        assert cover._attr_is_closing is False

    def test_stop_cover_sets_skip_update_and_app_command(self):
        cover = _make_blinds()
        asyncio.run(cover.async_stop_cover())
        assert cover._skip_update is True
        assert cover._app_command is True


# ---------------------------------------------------------------------------
# BlindsControlCover.async_stop_cover_tilt
# ---------------------------------------------------------------------------

class TestBlindsStopCoverTilt:
    def test_stop_cover_tilt_calls_async_stop_blinds(self):
        cover = _make_blinds()
        asyncio.run(cover.async_stop_cover_tilt())
        cover._device.async_stop_blinds.assert_awaited_once()


# ---------------------------------------------------------------------------
# Line 302: BlindsControlCover.current_cover_tilt_position
# ---------------------------------------------------------------------------

class TestBlindsCurrentCoverTiltPosition:
    def test_tilt_position_calculation(self):
        """current_cover_tilt_position = round((1.0 - current_angle) * 100)."""
        cover = _make_blinds(current_angle=0.3)
        # (1.0 - 0.3) * 100 = 70
        assert cover.current_cover_tilt_position == 70

    def test_tilt_position_fully_open(self):
        cover = _make_blinds(current_angle=0.0)
        assert cover.current_cover_tilt_position == 100

    def test_tilt_position_fully_closed(self):
        cover = _make_blinds(current_angle=1.0)
        assert cover.current_cover_tilt_position == 0


# ---------------------------------------------------------------------------
# BlindsControlCover.async_open_cover_tilt
# ---------------------------------------------------------------------------

class TestBlindsOpenCoverTilt:
    def test_open_cover_tilt_sets_target_angle_zero(self):
        """async_open_cover_tilt → async_set_target_angle(0.0) (1.0 - 1.0)."""
        import pytest
        cover = _make_blinds()
        asyncio.run(cover.async_open_cover_tilt())
        cover._device.async_set_target_angle.assert_awaited_once_with(pytest.approx(0.0))


# ---------------------------------------------------------------------------
# BlindsControlCover.async_close_cover_tilt
# ---------------------------------------------------------------------------

class TestBlindsCloseCoverTilt:
    def test_close_cover_tilt_sets_target_angle_one(self):
        """async_close_cover_tilt → async_set_target_angle(1.0) (1.0 - 0.0)."""
        import pytest
        cover = _make_blinds()
        asyncio.run(cover.async_close_cover_tilt())
        cover._device.async_set_target_angle.assert_awaited_once_with(pytest.approx(1.0))


# ---------------------------------------------------------------------------
# BlindsControlCover.async_set_cover_tilt_position
# ---------------------------------------------------------------------------

class TestBlindsSetCoverTiltPosition:
    def test_set_tilt_position_calculation(self):
        """async_set_cover_tilt_position(40) → async_set_target_angle(0.60)."""
        import pytest
        cover = _make_blinds()
        asyncio.run(cover.async_set_cover_tilt_position(**{ATTR_TILT_POSITION: 40}))
        cover._device.async_set_target_angle.assert_awaited_once_with(pytest.approx(0.60))

    def test_set_tilt_position_fully_open(self):
        """tilt_position=100 → async_set_target_angle(0.0)."""
        import pytest
        cover = _make_blinds()
        asyncio.run(cover.async_set_cover_tilt_position(**{ATTR_TILT_POSITION: 100}))
        cover._device.async_set_target_angle.assert_awaited_once_with(pytest.approx(0.0))

    def test_set_tilt_position_fully_closed(self):
        """tilt_position=0 → async_set_target_angle(1.0)."""
        import pytest
        cover = _make_blinds()
        asyncio.run(cover.async_set_cover_tilt_position(**{ATTR_TILT_POSITION: 0}))
        cover._device.async_set_target_angle.assert_awaited_once_with(pytest.approx(1.0))


# ---------------------------------------------------------------------------
# Regression: issue #294 — MICROMODULE_SHUTTER moved by its physical switch
# ---------------------------------------------------------------------------

def test_micromodule_shutter_physical_down_after_up_shows_closing_issue_294():
    """A MICROMODULE_SHUTTER moved by its physical switch sends Keypad
    eventType=PRESS_SHORT (not SWITCH_ON), so the keycode direction branch never
    fires and direction comes from level vs _last_position. _last_position must
    refresh at every rest (incl. physical moves) — otherwise the reference is
    frozen at the load-time position and the down move keeps showing 'opening'.
    Verified against a live device (deviceModel MICROMODULE_SHUTTER, Keypad
    eventType PRESS_SHORT)."""
    cover = _make_cover(
        "MICROMODULE_SHUTTER", level=0.0, operation_state=STOPPED,
        eventtype="PRESS_SHORT", keycode=2,
    )
    # 1. initial rest at fully closed -> reference initialises to 0
    cover._update_attr()
    assert cover._last_position == 0

    # 2. physical UP move (level reports the target 1.0 == open) -> opening
    cover._device.level = 1.0
    cover._device.operation_state = MOVING
    cover._update_attr()
    assert cover._attr_is_opening is True
    assert cover._attr_is_closing is False

    # 3. comes to rest fully open -> reference MUST refresh to 100 (the fix;
    #    without it this stays 0 for a physical MICROMODULE move)
    cover._device.operation_state = STOPPED
    cover._update_attr()
    assert cover._last_position == 100

    # 4. physical DOWN move (level reports the target 0.0 == closed) -> closing
    cover._device.level = 0.0
    cover._device.operation_state = MOVING
    cover._update_attr()
    assert cover._attr_is_closing is True
    assert cover._attr_is_opening is False
