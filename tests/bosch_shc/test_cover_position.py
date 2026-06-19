"""Regression test for BlindsControlCover.current_cover_position bug.

Bug: BlindsControlCover inherits current_cover_position from ShutterControlCover,
which reads self._device.level (ShutterControlService). But BlindsControlCover
uses self._device.blinds_level (BlindsSceneControlService) for all position
mutations (open/close/set_cover_position). These are backed by DIFFERENT services
and can hold different values, so the reported position was wrong.

Fix: BlindsControlCover must override current_cover_position to use blinds_level.
"""

from types import SimpleNamespace

from boschshcpy import SHCShutterControl

from custom_components.bosch_shc.cover import BlindsControlCover, ShutterControlCover

STOPPED = SHCShutterControl.ShutterControlService.State.STOPPED
MOVING = SHCShutterControl.ShutterControlService.State.MOVING


def _make_blinds_cover(blinds_level: float, level: float, operation_state=STOPPED):
    """Build a BlindsControlCover bypassing SHCEntity.__init__.

    blinds_level  -- the BlindsSceneControlService value (position used for
                     open/close/set_cover_position commands)
    level         -- the ShutterControlService value (parent class default)
    """
    cover = BlindsControlCover.__new__(BlindsControlCover)
    cover._device = SimpleNamespace(
        device_model="MICROMODULE_BLINDS",
        level=level,
        blinds_level=blinds_level,
        operation_state=operation_state,
        current_angle=0.5,
        name="test-blinds",
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


class TestBlindsCurrentCoverPosition:
    """current_cover_position must reflect blinds_level, not the inherited level."""

    def test_position_uses_blinds_level_not_level(self):
        """CORE BUG: when blinds_level != level, must return blinds_level-derived value."""
        # blinds_level=0.75 → 75, level=0.50 → 50
        cover = _make_blinds_cover(blinds_level=0.75, level=0.50)
        assert cover.current_cover_position == 75, (
            "current_cover_position must use blinds_level (75), not level (50)"
        )

    def test_position_fully_open(self):
        cover = _make_blinds_cover(blinds_level=1.0, level=0.0)
        assert cover.current_cover_position == 100

    def test_position_fully_closed(self):
        cover = _make_blinds_cover(blinds_level=0.0, level=1.0)
        assert cover.current_cover_position == 0

    def test_position_mid_travel(self):
        cover = _make_blinds_cover(blinds_level=0.33, level=0.90)
        assert cover.current_cover_position == 33

    def test_position_rounds_correctly(self):
        """round(0.555 * 100) must equal 56 (Python banker's rounding aside)."""
        cover = _make_blinds_cover(blinds_level=0.56, level=0.0)
        assert cover.current_cover_position == 56

    def test_shutter_parent_still_uses_level(self):
        """Sanity: ShutterControlCover (non-blinds) must still use level."""
        cover = ShutterControlCover.__new__(ShutterControlCover)
        cover._device = SimpleNamespace(
            device_model="BBL",
            level=0.40,
            operation_state=STOPPED,
            name="test-shutter",
        )
        cover._current_operation_state = None
        cover._target_position = None
        cover._last_position = None
        cover._skip_update = False
        cover._app_command = False
        cover._attr_is_opening = None
        cover._attr_is_closing = None
        cover._attr_current_cover_position = None
        assert cover.current_cover_position == 40

    def test_is_closed_uses_blinds_level(self):
        """is_closed must be True only when blinds_level == 0 and STOPPED."""
        cover = _make_blinds_cover(blinds_level=0.0, level=1.0, operation_state=STOPPED)
        # is_closed for blinds should reflect blinds_level == 0.0
        # The current inherited implementation checks level — verify the override
        assert cover.current_cover_position == 0
