"""Regression test for BlindsControlCover.current_cover_position (issue #100).

History: an earlier change (#299) made BlindsControlCover read the lift position
from self._device.blinds_level (BlindsSceneControlService). Issue #100 then
showed — on a real DEGREE_180 MICROMODULE_BLINDS (dev 6c5cb1…) — that
BlindsSceneControl.level holds the last *scene* level, not the live lift: it
sat at 0.0 while the blind was fully up, so HA reported 0% for a fully-open
blind ("fully up shows 0%").

Fix: the live lift is ShutterControl.level (inherited self._device.level), the
same source the parent ShutterControlCover uses for non-MICROMODULE_SHUTTER
models (and the BBL mapping). Slat tilt stays on BlindsControl. This file now
asserts position follows ShutterControl.level and is independent of
blinds_level.
"""

from types import SimpleNamespace

from boschshcpy import SHCShutterControl

from custom_components.bosch_shc.cover import BlindsControlCover, ShutterControlCover

STOPPED = SHCShutterControl.ShutterControlService.State.STOPPED
MOVING = SHCShutterControl.ShutterControlService.State.MOVING


def _make_blinds_cover(level: float, blinds_level: float = 0.0,
                       operation_state=STOPPED):
    """Build a BlindsControlCover bypassing SHCEntity.__init__.

    level         -- the ShutterControlService value (the live lift; position)
    blinds_level  -- the BlindsSceneControlService value (last scene level;
                     deliberately set to a DIFFERENT value to prove position
                     no longer follows it)
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
    """current_cover_position must reflect ShutterControl.level (the live lift),
    independent of blinds_level (BlindsSceneControl, the stale scene value)."""

    def test_position_uses_level_not_blinds_level(self):
        """CORE #100 FIX: when level != blinds_level, return the level value."""
        # level=0.50 → 50, blinds_level=0.75 → would have been 75 (the old bug)
        cover = _make_blinds_cover(level=0.50, blinds_level=0.75)
        assert cover.current_cover_position == 50, (
            "current_cover_position must use ShutterControl.level (50), "
            "not blinds_level (75)"
        )

    def test_fully_up_shows_100_not_scene_zero(self):
        """The exact reported symptom: fully up = ShutterControl.level 1.0 but
        BlindsSceneControl.level 0.0 → must be 100%, not 0%."""
        cover = _make_blinds_cover(level=1.0, blinds_level=0.0)
        assert cover.current_cover_position == 100

    def test_position_fully_closed(self):
        cover = _make_blinds_cover(level=0.0, blinds_level=1.0)
        assert cover.current_cover_position == 0

    def test_position_mid_travel(self):
        cover = _make_blinds_cover(level=0.33, blinds_level=0.90)
        assert cover.current_cover_position == 33

    def test_position_rounds_correctly(self):
        """round(0.56 * 100) must equal 56."""
        cover = _make_blinds_cover(level=0.56, blinds_level=0.0)
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

    def test_is_closed_uses_level(self):
        """is_closed (inherited) must reflect ShutterControl.level == 0 + STOPPED."""
        cover = _make_blinds_cover(level=0.0, blinds_level=1.0,
                                   operation_state=STOPPED)
        assert cover.current_cover_position == 0
        assert cover.is_closed is True
