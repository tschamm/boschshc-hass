"""Tests for cover.py: ShutterControlCover and BlindsControlCover.

Covers async_setup_entry (shutter/micromodule-shutter/blinds discovery and
device_excluded filtering), _update_attr direction-flag inference for every
device_model x operationState combination (BBL, MICROMODULE_SHUTTER,
MICROMODULE_BLINDS, CALIBRATING, and the Shutter-II OPENING/CLOSING states
from issue #100), current_cover_position fallbacks (including the #100 fix
that BlindsControlCover must read the lift from ShutterControl.level, not
the stale BlindsSceneControl.level), the async_open/close/stop/set_position
commands for both entity classes (including the #318 no-Keypad-service guard
and the #293/#294 None-_last_position guards), device_class/is_closed/
extra_state_attributes, and BlindsControlCover's tilt controls.

Pattern: `__new__` bypass + SimpleNamespace/lightweight device doubles, no HA
test harness (`-p no:homeassistant`).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from boschshcpy import KeypadService, ShutterControlService
from homeassistant.components.cover import (
    ATTR_POSITION,
    ATTR_TILT_POSITION,
    CoverDeviceClass,
    CoverEntityFeature,
)

from custom_components.bosch_shc.const import OPT_EXCLUDED_DEVICES
from custom_components.bosch_shc.cover import (
    BlindsControlCover,
    ShutterControlCover,
    async_setup_entry,
)

from .conftest import run_setup_entry

STOPPED = ShutterControlService.State.STOPPED
MOVING = ShutterControlService.State.MOVING
OPENING = ShutterControlService.State.OPENING
CLOSING = ShutterControlService.State.CLOSING
CALIBRATING = ShutterControlService.State.CALIBRATING
SWITCH_ON = KeypadService.KeyEvent.SWITCH_ON
SWITCH_OFF = KeypadService.KeyEvent.SWITCH_OFF
PRESS_SHORT = KeypadService.KeyEvent.PRESS_SHORT


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_cover(
    device_model="MICROMODULE_SHUTTER",
    level=0.5,
    operation_state=MOVING,
    eventtype=None,
    keycode=None,
):
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


class _TrackingDevice:
    """Device double for BlindsControlCover — async method mocks."""

    def __init__(
        self,
        device_model="MICROMODULE_BLINDS",
        level=0.5,
        blinds_level=0.5,
        operation_state=STOPPED,
        current_angle=0.5,
    ):
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


def _make_blinds(
    device_model="MICROMODULE_BLINDS",
    level=0.5,
    blinds_level=0.5,
    operation_state=STOPPED,
    current_angle=0.5,
):
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


def _make_blinds_cover(level: float, blinds_level: float = 0.0, operation_state=STOPPED):
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


def pytest_approx(value, rel=1e-6):
    """Thin wrapper so we don't need to import pytest.approx at module level."""
    return pytest.approx(value, rel=rel)


# ---- helpers for async_setup_entry tests -----------------------------------

def _cover_device(
    device_model: str = "BBL",
    level: float = 0.5,
    operation_state=STOPPED,
) -> SimpleNamespace:
    """Minimal device for ShutterControlCover.__init__ + _update_attr."""
    return SimpleNamespace(
        name="Test Cover",
        id="hdm:HomeMaticIP:cover1",
        root_device_id="aa:bb:cc:dd:ee:ff",
        serial="serial-cover1",
        device_model=device_model,
        level=level,
        operation_state=operation_state,
        device_services=[],
        manufacturer="Bosch",
        status="AVAILABLE",
        deleted=False,
    )


def _blinds_device(
    blinds_level: float = 0.5,
    level: float = 0.5,
    current_angle: float = 0.3,
) -> SimpleNamespace:
    """Minimal device for BlindsControlCover.__init__ + _update_attr."""
    return SimpleNamespace(
        name="Test Blinds",
        id="hdm:HomeMaticIP:blind1",
        root_device_id="aa:bb:cc:dd:ee:ff",
        serial="serial-blind1",
        device_model="MICROMODULE_BLINDS",
        level=level,
        blinds_level=blinds_level,
        current_angle=current_angle,
        operation_state=STOPPED,
        device_services=[],
        manufacturer="Bosch",
        status="AVAILABLE",
        deleted=False,
    )


# ---------------------------------------------------------------------------
# async_setup_entry (lines 34-59): shutter/micromodule-shutter/blinds discovery
# ---------------------------------------------------------------------------

def _fake_cover_device(device_id="cov-001"):
    """Minimal excludable device double (id/model only, no level/state)."""
    return SimpleNamespace(
        id=device_id,
        name="Cover",
        root_device_id="root",
        serial="SER",
        manufacturer="Bosch",
        device_model="BBL",
        device_services=[],
    )


class TestCoverSetupEntry:
    """Cover async_setup_entry with ShutterControlCover and BlindsControlCover."""

    def _run(self, mock_config_entry, mock_session) -> list:
        with patch(
            "custom_components.bosch_shc.cover.async_migrate_to_new_unique_id",
            new_callable=AsyncMock,
        ):
            return asyncio.run(
                run_setup_entry(async_setup_entry, mock_config_entry, mock_session)
            )

    @pytest.mark.parametrize(
        "device_buckets", [{"shutter_controls": [_cover_device()]}], indirect=True
    )
    def test_shutter_controls_produce_shutter_cover_entities(
        self, mock_config_entry, mock_session
    ) -> None:
        """shutter_controls → ShutterControlCover, one per device."""
        result = self._run(mock_config_entry, mock_session)
        assert len(result) == 1
        assert isinstance(result[0], ShutterControlCover)

    @pytest.mark.parametrize(
        "device_buckets",
        [{"micromodule_shutter_controls": [_cover_device(device_model="MICROMODULE_SHUTTER", level=0.0)]}],
        indirect=True,
    )
    def test_micromodule_shutter_controls_produce_shutter_cover_entities(
        self, mock_config_entry, mock_session
    ) -> None:
        """micromodule_shutter_controls → ShutterControlCover."""
        result = self._run(mock_config_entry, mock_session)
        assert len(result) == 1
        assert isinstance(result[0], ShutterControlCover)

    @pytest.mark.parametrize(
        "device_buckets", [{"micromodule_blinds": [_blinds_device()]}], indirect=True
    )
    def test_micromodule_blinds_produce_blinds_cover_entities(
        self, mock_config_entry, mock_session
    ) -> None:
        """micromodule_blinds → BlindsControlCover."""
        result = self._run(mock_config_entry, mock_session)
        assert len(result) == 1
        assert isinstance(result[0], BlindsControlCover)

    @pytest.mark.parametrize(
        "device_buckets",
        [
            {
                "shutter_controls": [_cover_device()],
                "micromodule_shutter_controls": [
                    _cover_device(device_model="MICROMODULE_SHUTTER")
                ],
                "micromodule_blinds": [_blinds_device()],
            }
        ],
        indirect=True,
    )
    def test_mixed_devices_all_collected(
        self, mock_config_entry, mock_session
    ) -> None:
        """Shutter + micromodule_shutter + blinds → 3 entities total."""
        result = self._run(mock_config_entry, mock_session)
        assert len(result) == 3
        assert isinstance(result[0], ShutterControlCover)
        assert isinstance(result[1], ShutterControlCover)
        assert isinstance(result[2], BlindsControlCover)

    def test_no_devices_adds_nothing(
        self, mock_config_entry, mock_session
    ) -> None:
        """Empty lists → async_add_entities never called → 0 collected."""
        result = self._run(mock_config_entry, mock_session)
        assert result == []

    @pytest.mark.parametrize(
        "device_buckets", [{"shutter_controls": [_cover_device()]}], indirect=True
    )
    def test_entry_id_set_on_entities(
        self, mock_config_entry, mock_session
    ) -> None:
        """Entities get the config_entry entry_id stored as _entry_id."""
        result = self._run(mock_config_entry, mock_session)
        assert result[0]._entry_id == "E1"

    @pytest.mark.parametrize(
        "device_buckets",
        [{"shutter_controls": [_cover_device(), _cover_device()]}],
        indirect=True,
    )
    def test_multiple_shutter_controls(
        self, mock_config_entry, mock_session
    ) -> None:
        """Two shutter_controls → two ShutterControlCover entities."""
        result = self._run(mock_config_entry, mock_session)
        assert len(result) == 2
        assert all(isinstance(e, ShutterControlCover) for e in result)


class TestCoverDeviceExcluded:
    """device_excluded continue for shutter/blind cover paths."""

    def _run(self, mock_config_entry, mock_session) -> list:
        with patch(
            "custom_components.bosch_shc.cover.async_migrate_to_new_unique_id",
            new=AsyncMock(return_value=None),
        ):
            return asyncio.run(
                run_setup_entry(async_setup_entry, mock_config_entry, mock_session)
            )

    @pytest.mark.parametrize(
        ("device_buckets", "mock_config_entry"),
        [
            (
                {"shutter_controls": [_fake_cover_device("sc-excl")]},
                {"options": {OPT_EXCLUDED_DEVICES: ["sc-excl"]}},
            )
        ],
        indirect=True,
    )
    def test_excluded_shutter_control_not_added(
        self, mock_config_entry, mock_session
    ):
        """Excluded shutter control must be skipped (line 43)."""
        result = self._run(mock_config_entry, mock_session)
        assert not any(
            getattr(e, "_device", None) and e._device.id == "sc-excl"
            for e in result
        )

    @pytest.mark.parametrize(
        ("device_buckets", "mock_config_entry"),
        [
            (
                {"micromodule_blinds": [_fake_cover_device("blind-excl")]},
                {"options": {OPT_EXCLUDED_DEVICES: ["blind-excl"]}},
            )
        ],
        indirect=True,
    )
    def test_excluded_micromodule_blind_not_added(
        self, mock_config_entry, mock_session
    ):
        """Excluded micromodule blind must be skipped (line 54)."""
        result = self._run(mock_config_entry, mock_session)
        assert not any(
            getattr(e, "_device", None) and e._device.id == "blind-excl"
            for e in result
        )

    @pytest.mark.parametrize(
        ("device_buckets", "mock_config_entry"),
        [
            (
                {
                    "shutter_controls": [_fake_cover_device("sc-excl2")],
                    "micromodule_blinds": [_fake_cover_device("blind-excl2")],
                },
                {"options": {OPT_EXCLUDED_DEVICES: ["sc-excl2", "blind-excl2"]}},
            )
        ],
        indirect=True,
    )
    def test_both_excluded_yields_empty_list(
        self, mock_config_entry, mock_session
    ):
        """When both shutter and blind devices are excluded, result is empty."""
        result = self._run(mock_config_entry, mock_session)
        assert result == []


# ---------------------------------------------------------------------------
# Bug #293/#294: MICROMODULE_SHUTTER else-branch with _last_position = None
# ---------------------------------------------------------------------------

class TestMicromoduleShutterNoneGuard:
    """_update_attr must NOT raise when _last_position is None at first MOVING update."""

    def test_no_raise_when_last_position_none_moving_up(self):
        """Level > _last_position would crash; with None guard it must not raise."""
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
# MOVING MICROMODULE_SHUTTER keypad keycode 1 (SWITCH_ON open) / 2 (close)
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
# MOVING MICROMODULE_SHUTTER else-branch (not SWITCH_ON) — various
# _last_position / target comparisons
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
# BBL direction (already had None guard — regression check) + MOVING coverage
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
# Unknown device_model MOVING → both flags cleared to None
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
# CALIBRATING operationState (APK ground-truth, a real 5th enum member
# alongside STOPPED/MOVING/OPENING/CLOSING) must not leave stale direction
# flags frozen from before the calibration run started.
# ---------------------------------------------------------------------------

class TestCalibratingClearsDirectionFlags:
    def test_calibrating_clears_stale_direction_flags(self):
        cover = _make_cover(
            device_model="BBL",
            level=0.5,
            operation_state=CALIBRATING,
        )
        cover._attr_is_opening = True
        cover._attr_is_closing = False
        cover._update_attr()
        assert cover._attr_is_opening is False
        assert cover._attr_is_closing is False


class TestNonStoppedNonMovingStates:
    @pytest.mark.parametrize("state", [CALIBRATING])
    def test_other_state_clears_direction_flags(self, state):
        """CALIBRATING has its own dedicated branch (a real 5th operationState,
        APK ground-truth) that clears both direction flags, since there is no
        meaningful open/close direction during an end-position auto-detect
        run. Without it the flags would stay frozen at whatever they held
        before calibration started. (OPENING/CLOSING set the direction flags
        via the Shutter-II handler — see issue #100 and
        TestShutterIIOperationStateDirection below.)
        """
        cover = _make_cover(device_model="BBL", level=0.5, operation_state=state)
        cover._attr_is_opening = True
        cover._attr_is_closing = False
        cover._last_position = 50
        cover._update_attr()
        assert cover._attr_is_opening is False
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
# Issue #100: Shutter Control II reports direction via operationState
# (enum [STOPPED, OPENING, CLOSING] — never MOVING). The STOPPED/MOVING
# branches never matched these states, so physical-switch / Bosch-app moves
# left is_opening/is_closing unset. The new OPENING/CLOSING handlers map the
# state straight to the HA flags, additively, without touching _target_position.
# ---------------------------------------------------------------------------

class TestShutterIIOperationStateDirection:
    def test_blinds_opening_state_sets_is_opening(self):
        cover = _make_cover(
            device_model="MICROMODULE_BLINDS",
            level=0.4,
            operation_state=OPENING,
        )
        cover._update_attr()
        assert cover._attr_is_opening is True
        assert cover._attr_is_closing is False

    def test_blinds_closing_state_sets_is_closing(self):
        cover = _make_cover(
            device_model="MICROMODULE_BLINDS",
            level=0.4,
            operation_state=CLOSING,
        )
        cover._update_attr()
        assert cover._attr_is_closing is True
        assert cover._attr_is_opening is False

    def test_shutter_opening_state_sets_is_opening(self):
        cover = _make_cover(
            device_model="MICROMODULE_SHUTTER",
            level=0.4,
            operation_state=OPENING,
        )
        cover._update_attr()
        assert cover._attr_is_opening is True
        assert cover._attr_is_closing is False

    def test_opening_state_does_not_snap_target_position(self):
        """The handler must NOT touch _target_position (no position snap)."""
        cover = _make_cover(
            device_model="MICROMODULE_SHUTTER",
            level=0.4,
            operation_state=OPENING,
        )
        cover._target_position = 30
        cover._update_attr()
        assert cover._target_position == 30  # unchanged

    def test_opening_works_without_last_position(self):
        """Direction from operationState needs no _last_position reference."""
        cover = _make_cover(
            device_model="MICROMODULE_BLINDS",
            level=0.4,
            operation_state=OPENING,
        )
        assert cover._last_position is None
        cover._update_attr()  # must not raise
        assert cover._attr_is_opening is True


# ---------------------------------------------------------------------------
# STOPPED branch — last_position initialisation / refresh behaviour
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


class TestStoppedNonBBLNonMMNoAppCommand:
    """STOPPED: non-BBL/non-MICROMODULE device, _app_command=False. The inner
    "if device_model in (...) or _app_command" branch does NOT fire, so
    _last_position stays None until the None-init branch sets it.
    """

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


class TestMicromoduleShutterStoppedUpdatesLastPosition:
    """Verify that a MICROMODULE_SHUTTER STOPPED (non-BBL, not app_command) does
    update _last_position because MICROMODULE_SHUTTER is in the allowed-model set.
    """

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
# Regression: issue #294 — MICROMODULE_SHUTTER moved by its physical switch
# ---------------------------------------------------------------------------

def test_micromodule_shutter_physical_down_after_up_shows_closing_issue_294():
    """A MICROMODULE_SHUTTER moved by its physical switch sends Keypad
    eventType=PRESS_SHORT (not SWITCH_ON), so the keycode direction branch never
    fires and direction comes from level vs _last_position. _last_position must
    refresh at every rest (incl. physical moves) — otherwise the reference is
    frozen at the load-time position and the down move keeps showing 'opening'.
    Verified against a live device (deviceModel MICROMODULE_SHUTTER, Keypad
    eventType PRESS_SHORT).
    """
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


# ---------------------------------------------------------------------------
# Issue #318: MICROMODULE_SHUTTER with NO Keypad service (no physical wall
# switch wired). The lib's eventtype setter dereferences a None keypad service,
# so open/close/stop crashed with
# "'NoneType' object has no attribute 'eventType'". _micromodule_keypad_switch_off
# must skip the eventtype write when the device has no keypad service.
# ---------------------------------------------------------------------------

class TestMicromoduleShutterNoKeypad:
    @staticmethod
    def _no_keypad_cover():
        cover = ShutterControlCover.__new__(ShutterControlCover)

        class _NoKeypadDevice:
            device_model = "MICROMODULE_SHUTTER"
            _keypad_service = None  # device exposes no Keypad service
            level = 0.0
            async_set_level = AsyncMock()
            async_stop = AsyncMock()

            @property
            def eventtype(self):
                raise AttributeError("'NoneType' object has no attribute 'eventType'")

            @eventtype.setter
            def eventtype(self, value):
                # Mirrors the released-lib crash when _keypad_service is None.
                raise AttributeError(
                    "'NoneType' object has no attribute 'eventType'"
                )

        cover._device = _NoKeypadDevice()
        cover._target_position = None
        cover._last_position = None
        cover._skip_update = False
        cover._app_command = False
        cover._attr_is_opening = None
        cover._attr_is_closing = None
        cover._attr_current_cover_position = None
        return cover

    def test_open_cover_does_not_crash_without_keypad(self):
        cover = self._no_keypad_cover()
        asyncio.run(cover.async_open_cover())  # must not raise (issue #318)
        assert cover._attr_is_opening is True

    def test_close_cover_does_not_crash_without_keypad(self):
        cover = self._no_keypad_cover()
        asyncio.run(cover.async_close_cover())  # must not raise
        assert cover._attr_is_closing is True

    def test_stop_cover_does_not_crash_without_keypad(self):
        cover = self._no_keypad_cover()
        asyncio.run(cover.async_stop_cover())  # must not raise
        assert cover._attr_is_opening is False
        assert cover._attr_is_closing is False


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
# device_class / is_closed / extra_state_attributes
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
# ShutterControlCover.current_cover_position
# ---------------------------------------------------------------------------

class TestShutterCurrentCoverPositionFallback:
    def test_stopped_returns_level(self):
        """STOPPED state always returns the current device level."""
        cover = _make_cover(level=0.7, operation_state=STOPPED)
        cover._target_position = None
        assert cover.current_cover_position == 70

    def test_moving_returns_target_when_set(self):
        """MOVING with _target_position set, HA-initiated, must return target."""
        cover = _make_cover(level=0.3, operation_state=MOVING)
        cover._target_position = 90
        cover._app_command = True
        assert cover.current_cover_position == 90

    def test_moving_falls_back_to_level_when_target_is_none(self):
        """MOVING with _target_position=None must fall back to device level."""
        cover = _make_cover(level=0.3, operation_state=MOVING)
        cover._target_position = None
        result = cover.current_cover_position
        assert result == 30, f"Expected 30 (30% from 0.3 level), got {result}"

    def test_bbl_always_returns_level(self):
        """BBL model must always use device level regardless of operation state."""
        cover = _make_cover(device_model="BBL", level=0.6, operation_state=MOVING)
        cover._target_position = None
        assert cover.current_cover_position == 60

    def test_opening_via_ha_returns_target(self):
        """OPENING triggered by HA (async_open_cover) trusts the HA-side target."""
        cover = _make_cover(level=0.3, operation_state=OPENING)
        cover._target_position = 100
        cover._app_command = True
        assert cover.current_cover_position == 100

    def test_opening_via_app_or_switch_uses_live_level(self):
        """OPENING triggered outside HA (Bosch app / physical switch): a stale
        _target_position left over from a prior HA move must not be returned —
        the live device level is authoritative instead."""
        cover = _make_cover(level=0.65, operation_state=OPENING)
        cover._target_position = 0  # stale target from an earlier HA close
        cover._app_command = False
        assert cover.current_cover_position == 65

    def test_closing_via_app_or_switch_uses_live_level(self):
        """CLOSING triggered outside HA must use the live device level too."""
        cover = _make_cover(level=0.2, operation_state=CLOSING)
        cover._target_position = 100  # stale target from an earlier HA open
        cover._app_command = False
        assert cover.current_cover_position == 20


class TestMicromoduleShutterCurrentPositionMovingTargetSet:
    """Existing test_moving_returns_target_when_set covers this but only for the
    generic helper; here we make the operation_state explicit.
    """

    def test_moving_with_target_position_returns_target(self):
        """MICROMODULE_SHUTTER MOVING, HA-initiated + _target_position != None → return _target_position."""
        cover = _make_cover(
            device_model="MICROMODULE_SHUTTER",
            level=0.2,
            operation_state=MOVING,
        )
        cover._target_position = 75
        cover._app_command = True
        result = cover.current_cover_position
        assert result == 75

    def test_moving_with_target_position_zero_returns_zero(self):
        """_target_position=0, HA-initiated, is not None → must return 0, not fall back to level."""
        cover = _make_cover(
            device_model="MICROMODULE_SHUTTER",
            level=0.9,
            operation_state=MOVING,
        )
        cover._target_position = 0
        cover._app_command = True
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
# ShutterControlCover command methods: async_stop_cover / async_open_cover /
# async_close_cover / async_set_cover_position
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

    def test_open_cover_clears_stale_is_closing(self):
        """Regression: opening while a prior async_close_cover left
        is_closing=True must not leave both flags True at once."""
        cover = _make_cover(
            device_model="BBL",
            level=0.0,
            operation_state=STOPPED,
        )
        cover._attr_is_closing = True
        asyncio.run(cover.async_open_cover())
        assert cover._attr_is_opening is True
        assert cover._attr_is_closing is False


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

    def test_close_cover_clears_stale_is_opening(self):
        """Regression: closing while a prior async_open_cover left
        is_opening=True must not leave both flags True at once."""
        cover = _make_cover(
            device_model="BBL",
            level=1.0,
            operation_state=STOPPED,
        )
        cover._attr_is_opening = True
        asyncio.run(cover.async_close_cover())
        assert cover._attr_is_closing is True
        assert cover._attr_is_opening is False


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


class TestSetCoverPositionBBLBranch:
    """async_set_cover_position — non-MICROMODULE_SHUTTER (BBL): no
    keypad_switch_off call, no _last_position save (covers the else-path of
    the device_model check).
    """

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
# BlindsControlCover.current_cover_position (issue #100)
#
# History: an earlier change (#299) made BlindsControlCover read the lift
# position from self._device.blinds_level (BlindsSceneControlService). Issue
# #100 then showed — on a real DEGREE_180 MICROMODULE_BLINDS (dev 6c5cb1…) —
# that BlindsSceneControl.level holds the last *scene* level, not the live
# lift: it sat at 0.0 while the blind was fully up, so HA reported 0% for a
# fully-open blind ("fully up shows 0%").
#
# Fix: the live lift is ShutterControl.level (inherited self._device.level),
# the same source the parent ShutterControlCover uses for non-
# MICROMODULE_SHUTTER models (and the BBL mapping). Slat tilt stays on
# BlindsControl. Position now follows ShutterControl.level, independent of
# blinds_level.
# ---------------------------------------------------------------------------

class TestBlindsCurrentCoverPosition:
    """current_cover_position must reflect ShutterControl.level (the live lift),
    independent of blinds_level (BlindsSceneControl, the stale scene value).
    """

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
        BlindsSceneControl.level 0.0 → must be 100%, not 0%.
        """
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


class TestBlindsUpdateAttrCachesLevel:
    """BlindsControlCover._update_attr caches ShutterControl.level into
    _attr_current_cover_position (issue #100 — NOT blinds_level/BlindsSceneControl).
    """

    def test_stopped_attr_current_cover_position_uses_level(self):
        """BlindsControlCover STOPPED: _attr_current_cover_position reflects
        ShutterControl.level (the live lift), not blinds_level (#100).
        """
        cover = _make_blinds(blinds_level=0.6, level=0.3, operation_state=STOPPED)
        cover._update_attr()
        # current_cover_position uses ShutterControl.level → round(0.3*100)
        assert cover._attr_current_cover_position == 30

    def test_moving_attr_current_cover_position_uses_level(self):
        """BlindsControlCover MOVING: _attr_current_cover_position uses
        ShutterControl.level (#100), independent of blinds_level.
        """
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


class TestBlindsCurrentCoverPositionIssue100:
    """Regression: issue #100 — MICROMODULE_BLINDS lift position must come from
    ShutterControl.level (the live lift), NOT blinds_level (BlindsSceneControl,
    which holds the last *scene* level). Reporter-confirmed device (DEGREE_180,
    dev 6c5cb1…) sat at BlindsSceneControl.level=0.0 while fully up, so reading
    blinds_level showed 0% for a fully-open blind ("fully up shows 0%").
    """

    def test_current_cover_position_reads_shuttercontrol_level(self):
        cover = _make_blinds(level=0.42)
        # round(0.42 * 100) = 42 — taken from ShutterControl.level
        assert cover.current_cover_position == 42

    def test_fully_up_blind_reads_100_not_scene_level_zero(self):
        """The exact #100 symptom: blind fully up → ShutterControl.level=1.0
        (=100%) while BlindsSceneControl.level (blinds_level) is 0.0. Position
        must follow the live lift (100%), NOT the stale scene level (0%).
        """
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
        MOVING and level jumps to the target early.
        """
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
# BlindsControlCover command methods: async_open_cover / async_close_cover /
# async_set_cover_position / async_stop_cover / async_stop_cover_tilt / tilt
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
        cover = _make_blinds()
        asyncio.run(cover.async_set_cover_position(position=40))
        cover._device.async_set_level.assert_awaited_once_with(pytest.approx(0.4))


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


class TestBlindsSetCoverPosition:
    def test_set_cover_position_uses_level(self):
        # #100: lift uses ShutterControl.level, not blinds_level
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


class TestBlindsStopCover:
    def test_stop_cover_calls_async_stop_blinds(self):
        """BlindsControlCover.async_stop_cover() must call async_stop_blinds()
        (blind endpoint), not the inherited async_stop() (ShutterControl endpoint).
        """
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


class TestBlindsStopCoverTilt:
    def test_stop_cover_tilt_calls_async_stop_blinds(self):
        cover = _make_blinds()
        asyncio.run(cover.async_stop_cover_tilt())
        cover._device.async_stop_blinds.assert_awaited_once()

    def test_stop_cover_tilt_clears_stale_direction_flags(self):
        """Regression: async_stop_blinds() is the same physical stop endpoint
        as async_stop_cover() (it halts the lift, not just tilt), so a
        mid-lift-move stop-tilt must clear is_opening/is_closing too, not
        just halt the motor while leaving the entity looking like it's still
        moving."""
        cover = _make_blinds()
        cover._attr_is_opening = True
        cover._attr_is_closing = True
        asyncio.run(cover.async_stop_cover_tilt())
        assert cover._attr_is_opening is False
        assert cover._attr_is_closing is False

    def test_stop_cover_tilt_sets_skip_update_and_app_command(self):
        cover = _make_blinds()
        asyncio.run(cover.async_stop_cover_tilt())
        assert cover._skip_update is True
        assert cover._app_command is True


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


class TestBlindsOpenCoverTilt:
    def test_open_cover_tilt_sets_target_angle_zero(self):
        """async_open_cover_tilt → async_set_target_angle(0.0) (1.0 - 1.0)."""
        cover = _make_blinds()
        asyncio.run(cover.async_open_cover_tilt())
        cover._device.async_set_target_angle.assert_awaited_once_with(pytest.approx(0.0))


class TestBlindsCloseCoverTilt:
    def test_close_cover_tilt_sets_target_angle_one(self):
        """async_close_cover_tilt → async_set_target_angle(1.0) (1.0 - 0.0)."""
        cover = _make_blinds()
        asyncio.run(cover.async_close_cover_tilt())
        cover._device.async_set_target_angle.assert_awaited_once_with(pytest.approx(1.0))


class TestBlindsSetCoverTiltPosition:
    def test_set_tilt_position_calculation(self):
        """async_set_cover_tilt_position(40) → async_set_target_angle(0.60)."""
        cover = _make_blinds()
        asyncio.run(cover.async_set_cover_tilt_position(**{ATTR_TILT_POSITION: 40}))
        cover._device.async_set_target_angle.assert_awaited_once_with(pytest.approx(0.60))

    def test_set_tilt_position_fully_open(self):
        """tilt_position=100 → async_set_target_angle(0.0)."""
        cover = _make_blinds()
        asyncio.run(cover.async_set_cover_tilt_position(**{ATTR_TILT_POSITION: 100}))
        cover._device.async_set_target_angle.assert_awaited_once_with(pytest.approx(0.0))

    def test_set_tilt_position_fully_closed(self):
        """tilt_position=0 → async_set_target_angle(1.0)."""
        cover = _make_blinds()
        asyncio.run(cover.async_set_cover_tilt_position(**{ATTR_TILT_POSITION: 0}))
        cover._device.async_set_target_angle.assert_awaited_once_with(pytest.approx(1.0))


# ---------------------------------------------------------------------------
# supported_features
# ---------------------------------------------------------------------------

class TestBlindsControlCoverSupportedFeatures:
    def test_supported_features_includes_tilt(self):
        """BlindsControlCover must advertise tilt features."""
        cover = _make_blinds()
        features = cover.supported_features
        assert features & CoverEntityFeature.OPEN_TILT
        assert features & CoverEntityFeature.CLOSE_TILT
        assert features & CoverEntityFeature.SET_TILT_POSITION
        assert features & CoverEntityFeature.STOP_TILT

    def test_shutter_supported_features_no_tilt(self):
        """ShutterControlCover must NOT advertise tilt features."""
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
