"""Unit tests for cover.py position fallback and BlindsControlCover skip flags.

Covers:
- ShutterControlCover.current_cover_position: MICROMODULE_SHUTTER fallback when
  _target_position is None (returns device.level * 100 instead of None)
- BlindsControlCover.async_open_cover/async_close_cover/async_set_cover_position:
  _skip_update and _app_command flags set after commanding

Pattern: __new__ bypass + SimpleNamespace device. No HA harness.

Run with:
  PYTHONPATH="<boschshc-hass>:<boschshcpy>" PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
    python3 -m pytest tests/bosch_shc/test_cover_position_moving.py -q -o addopts=""
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from boschshcpy import ShutterControlService

from custom_components.bosch_shc.cover import BlindsControlCover, ShutterControlCover

STOPPED = ShutterControlService.State.STOPPED
MOVING = ShutterControlService.State.MOVING


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_shutter(device_model="MICROMODULE_SHUTTER", level=0.5, operation_state=MOVING):
    cover = ShutterControlCover.__new__(ShutterControlCover)
    cover._device = SimpleNamespace(
        device_model=device_model,
        level=level,
        operation_state=operation_state,
        name="test-shutter",
        async_set_level=AsyncMock(),
        async_stop=AsyncMock(),
    )
    cover._target_position = None
    cover._last_position = None
    cover._skip_update = False
    cover._app_command = False
    cover._attr_is_opening = None
    cover._attr_is_closing = None
    cover._attr_current_cover_position = None
    cover._current_operation_state = None
    return cover


def _make_blinds(blinds_level=0.5):
    cover = BlindsControlCover.__new__(BlindsControlCover)
    cover._device = SimpleNamespace(
        device_model="MICROMODULE_BLINDS",
        level=blinds_level,
        blinds_level=blinds_level,
        operation_state=STOPPED,
        name="test-blinds",
        async_set_level=AsyncMock(),
        async_stop_blinds=AsyncMock(),
    )
    cover._target_position = None
    cover._last_position = None
    cover._skip_update = False
    cover._app_command = False
    cover._attr_is_opening = None
    cover._attr_is_closing = None
    cover._attr_current_cover_position = None
    cover._current_operation_state = None
    return cover


# ---------------------------------------------------------------------------
# ShutterControlCover.current_cover_position
# ---------------------------------------------------------------------------

class TestShutterCurrentCoverPositionFallback:
    def test_stopped_returns_level(self):
        """STOPPED state always returns the current device level."""
        cover = _make_shutter(level=0.7, operation_state=STOPPED)
        cover._target_position = None
        assert cover.current_cover_position == 70

    def test_moving_returns_target_when_set(self):
        """MOVING with _target_position set must return target."""
        cover = _make_shutter(level=0.3, operation_state=MOVING)
        cover._target_position = 90
        assert cover.current_cover_position == 90

    def test_moving_falls_back_to_level_when_target_is_none(self):
        """MOVING with _target_position=None must fall back to device level."""
        cover = _make_shutter(level=0.3, operation_state=MOVING)
        cover._target_position = None
        result = cover.current_cover_position
        assert result == 30, f"Expected 30 (30% from 0.3 level), got {result}"

    def test_bbl_always_returns_level(self):
        """BBL model must always use device level regardless of operation state."""
        cover = _make_shutter(device_model="BBL", level=0.6, operation_state=MOVING)
        cover._target_position = None
        assert cover.current_cover_position == 60


# ---------------------------------------------------------------------------
# BlindsControlCover.async_open_cover/async_close_cover/async_set_cover_position
# ---------------------------------------------------------------------------

class TestBlindsControlCoverCommandFlags:
    def test_open_cover_sets_target_skip_app(self):
        cover = _make_blinds()
        asyncio.run(cover.async_open_cover())
        assert cover._target_position == 100
        assert cover._skip_update is True
        assert cover._app_command is True
        assert cover._attr_is_opening is True
        assert cover._attr_is_closing is False

    def test_close_cover_sets_target_skip_app(self):
        cover = _make_blinds()
        asyncio.run(cover.async_close_cover())
        assert cover._target_position == 0
        assert cover._skip_update is True
        assert cover._app_command is True
        assert cover._attr_is_closing is True
        assert cover._attr_is_opening is False

    def test_set_cover_position_sets_target_skip_app(self):
        cover = _make_blinds()
        asyncio.run(cover.async_set_cover_position(position=65))
        assert cover._target_position == 65
        assert cover._skip_update is True
        assert cover._app_command is True

    def test_open_cover_sets_level_to_1(self):
        # #100: lift command uses ShutterControl.level, not blinds_level
        cover = _make_blinds()
        asyncio.run(cover.async_open_cover())
        cover._device.async_set_level.assert_awaited_once_with(1.0)

    def test_close_cover_sets_level_to_0(self):
        cover = _make_blinds()
        asyncio.run(cover.async_close_cover())
        cover._device.async_set_level.assert_awaited_once_with(0.0)

    def test_set_cover_position_divides_by_100(self):
        import pytest
        cover = _make_blinds()
        asyncio.run(cover.async_set_cover_position(position=40))
        cover._device.async_set_level.assert_awaited_once_with(pytest.approx(0.4))
