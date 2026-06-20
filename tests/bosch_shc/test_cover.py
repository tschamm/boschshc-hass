"""Regression tests for cover.py direction-flag and None-guard bugs.

Bug #293/#294: in the MICROMODULE_SHUTTER else-branch, `_last_position` can
be None when the shutter is already MOVING at the first update (HA restart
mid-move) → TypeError: '>' not supported between 'int' and 'NoneType'.

Bug #299: BlindsControlCover (MICROMODULE_BLINDS) hit the final else-branch
in _update_attr, setting is_opening/is_closing to None and losing the flags
that open_cover/close_cover set.
"""

from types import SimpleNamespace

from boschshcpy import SHCShutterControl, SHCMicromoduleShutterControl

from custom_components.bosch_shc.cover import ShutterControlCover

MOVING = SHCShutterControl.ShutterControlService.State.MOVING
STOPPED = SHCShutterControl.ShutterControlService.State.STOPPED
SWITCH_ON = SHCMicromoduleShutterControl.KeypadService.KeyEvent.SWITCH_ON
SWITCH_OFF = SHCMicromoduleShutterControl.KeypadService.KeyEvent.SWITCH_OFF


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
    )
    # Class-level defaults (mirroring the class body declarations)
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
# Bug #293/#294: MICROMODULE_SHUTTER else-branch with _last_position = None
# ---------------------------------------------------------------------------

class TestMicromoduleShutterNoneGuard:
    """_update_attr must NOT raise when _last_position is None at first MOVING update."""

    def test_no_raise_when_last_position_none_moving_up(self):
        """level > _last_position would crash; with None guard it must not raise."""
        cover = _make_cover(
            device_model="MICROMODULE_SHUTTER",
            level=0.5,
            operation_state=MOVING,
            eventtype=SWITCH_OFF,  # triggers the else-branch
            keycode=0,
        )
        assert cover._last_position is None
        # Must not raise TypeError
        cover._update_attr()

    def test_no_raise_when_last_position_none_moving_down(self):
        cover = _make_cover(
            device_model="MICROMODULE_SHUTTER",
            level=0.1,
            operation_state=MOVING,
            eventtype=SWITCH_OFF,
            keycode=0,
        )
        assert cover._last_position is None
        cover._update_attr()  # must not raise

    def test_flags_not_set_when_last_position_none(self):
        """When _last_position is None, direction flags must stay at their prior value (None)."""
        cover = _make_cover(
            device_model="MICROMODULE_SHUTTER",
            level=0.8,
            operation_state=MOVING,
            eventtype=SWITCH_OFF,
            keycode=0,
        )
        cover._update_attr()
        # Neither flag must have been set to True (no crash, no spurious flag)
        assert cover._attr_is_opening is None
        assert cover._attr_is_closing is None

    def test_opening_flag_set_when_last_position_known(self):
        """When _last_position is known, direction must be inferred correctly (opening)."""
        cover = _make_cover(
            device_model="MICROMODULE_SHUTTER",
            level=0.8,
            operation_state=MOVING,
            eventtype=SWITCH_OFF,
            keycode=0,
        )
        cover._last_position = 20  # target (80) > last (20) → opening
        cover._update_attr()
        assert cover._attr_is_opening is True
        assert cover._attr_is_closing is False

    def test_closing_flag_set_when_last_position_known(self):
        """When _last_position is known, direction must be inferred correctly (closing)."""
        cover = _make_cover(
            device_model="MICROMODULE_SHUTTER",
            level=0.1,
            operation_state=MOVING,
            eventtype=SWITCH_OFF,
            keycode=0,
        )
        cover._last_position = 80  # target (10) < last (80) → closing
        cover._update_attr()
        assert cover._attr_is_closing is True
        assert cover._attr_is_opening is False


# ---------------------------------------------------------------------------
# Keycode-based direction (SWITCH_ON k=1 open / k=2 close) — sanity check
# ---------------------------------------------------------------------------

class TestMicromoduleShutterKeycodeDirection:
    def test_keycode_1_is_opening(self):
        cover = _make_cover(
            device_model="MICROMODULE_SHUTTER",
            level=0.3,
            operation_state=MOVING,
            eventtype=SWITCH_ON,
            keycode=1,
        )
        cover._update_attr()
        assert cover._attr_is_opening is True
        assert cover._attr_is_closing is False
        assert cover._target_position == 100

    def test_keycode_2_is_closing(self):
        cover = _make_cover(
            device_model="MICROMODULE_SHUTTER",
            level=0.7,
            operation_state=MOVING,
            eventtype=SWITCH_ON,
            keycode=2,
        )
        cover._update_attr()
        assert cover._attr_is_closing is True
        assert cover._attr_is_opening is False
        assert cover._target_position == 0


# ---------------------------------------------------------------------------
# BBL direction (already had None guard — regression check)
# ---------------------------------------------------------------------------

class TestBBLDirection:
    def test_bbl_opening_with_last_position_known(self):
        cover = _make_cover(
            device_model="BBL",
            level=0.9,
            operation_state=MOVING,
        )
        cover._last_position = 20  # target 90 > last 20
        cover._update_attr()
        assert cover._attr_is_opening is True
        assert cover._attr_is_closing is False

    def test_bbl_no_flag_when_last_position_none(self):
        cover = _make_cover(
            device_model="BBL",
            level=0.9,
            operation_state=MOVING,
        )
        # _last_position is None — guard should suppress comparison
        cover._update_attr()
        assert cover._attr_is_opening is None
        assert cover._attr_is_closing is None


# ---------------------------------------------------------------------------
# Bug #299: MICROMODULE_BLINDS direction via _update_attr
# ---------------------------------------------------------------------------

class TestMicromoduleBlindsDirection:
    def test_blinds_opening_when_target_above_last(self):
        cover = _make_cover(
            device_model="MICROMODULE_BLINDS",
            level=0.7,
            operation_state=MOVING,
        )
        cover._last_position = 30  # target 70 > last 30 → opening
        cover._update_attr()
        assert cover._attr_is_opening is True
        assert cover._attr_is_closing is False

    def test_blinds_closing_when_target_below_last(self):
        cover = _make_cover(
            device_model="MICROMODULE_BLINDS",
            level=0.2,
            operation_state=MOVING,
        )
        cover._last_position = 80  # target 20 < last 80 → closing
        cover._update_attr()
        assert cover._attr_is_closing is True
        assert cover._attr_is_opening is False

    def test_blinds_no_flag_when_last_position_none(self):
        """MICROMODULE_BLINDS must also be guarded against None _last_position."""
        cover = _make_cover(
            device_model="MICROMODULE_BLINDS",
            level=0.5,
            operation_state=MOVING,
        )
        # _last_position stays None
        cover._update_attr()  # must not raise
        assert cover._attr_is_opening is None
        assert cover._attr_is_closing is None

    def test_blinds_target_position_updated(self):
        """_target_position must be set for MICROMODULE_BLINDS during MOVING."""
        cover = _make_cover(
            device_model="MICROMODULE_BLINDS",
            level=0.65,
            operation_state=MOVING,
        )
        cover._last_position = 40
        cover._update_attr()
        assert cover._target_position == 65


# ---------------------------------------------------------------------------
# STOPPED branch — last_position initialised when it was None
# ---------------------------------------------------------------------------

class TestStoppedInitialisesLastPosition:
    def test_last_position_initialised_on_first_stopped(self):
        cover = _make_cover(
            device_model="MICROMODULE_SHUTTER",
            level=0.5,
            operation_state=STOPPED,
        )
        assert cover._last_position is None
        cover._update_attr()
        # After STOPPED, _last_position must be non-None (initialised from level)
        assert cover._last_position is not None
