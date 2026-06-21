"""Unit tests for light.py LightSwitch and MotionDetectorLight runtime properties.

Complements test_light_unit.py (__init__ paths) and test_light_color_mode.py
(color mode switching). Covers:

LightSwitch:
  - is_on: binarystate passthrough
  - brightness: raw scaling to 0-255 + None guard
  - hs_color: rgb→hs conversion
  - turn_on: brightness clamp to 1 (never 0), binarystate toggled when off
  - turn_off: binarystate set False
  - turn_on no-op binarystate when already on

MotionDetectorLight:
  - is_on: binaryswitch passthrough
  - brightness: level scaling 0-255 + None→0 guard
  - turn_on without brightness kwarg
  - turn_on with brightness kwarg (scaling + clamp to 1)
  - turn_on no-op binaryswitch when already on
  - turn_off: binaryswitch set False
  - class-level color mode attrs

Pattern: __new__ bypass + SimpleNamespace device.
No HA harness.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode

from custom_components.bosch_shc.light import LightSwitch, MotionDetectorLight


# ---------------------------------------------------------------------------
# LightSwitch helpers
# ---------------------------------------------------------------------------

def _make_light_switch(
    binarystate=False,
    brightness=80,
    rgb=0xFF0000,
    color=300,
    supports_brightness=True,
    supports_color_temp=True,
    supports_color_hsb=True,
):
    """Build a LightSwitch via __new__ with all runtime attributes injected."""
    light = LightSwitch.__new__(LightSwitch)
    light._device = SimpleNamespace(
        name="Test Light",
        id="light-1",
        root_device_id="root-1",
        binarystate=binarystate,
        brightness=brightness,
        rgb=rgb,
        color=color,
        supports_brightness=supports_brightness,
        supports_color_temp=supports_color_temp,
        supports_color_hsb=supports_color_hsb,
        min_color_temperature=153,
        max_color_temperature=500,
    )
    light._attr_color_mode = ColorMode.HS
    light._attr_supported_color_modes = {ColorMode.HS}
    light.schedule_update_ha_state = MagicMock()
    return light


# ---------------------------------------------------------------------------
# LightSwitch.is_on
# ---------------------------------------------------------------------------

class TestLightSwitchIsOn:
    def test_is_on_true_when_binarystate_true(self):
        light = _make_light_switch(binarystate=True)
        assert light.is_on is True

    def test_is_on_false_when_binarystate_false(self):
        light = _make_light_switch(binarystate=False)
        assert light.is_on is False


# ---------------------------------------------------------------------------
# LightSwitch.brightness
# ---------------------------------------------------------------------------

class TestLightSwitchBrightness:
    def test_brightness_scales_100_percent_to_255(self):
        light = _make_light_switch(brightness=100)
        assert light.brightness == 255

    def test_brightness_scales_50_percent(self):
        light = _make_light_switch(brightness=50)
        assert light.brightness == round(50 * 255 / 100)

    def test_brightness_scales_zero(self):
        light = _make_light_switch(brightness=0)
        assert light.brightness == 0

    def test_brightness_returns_none_when_raw_is_none(self):
        """None raw brightness must return None (no TypeError)."""
        light = _make_light_switch(brightness=None)
        assert light.brightness is None

    def test_brightness_rounds(self):
        """Result must be rounded integer."""
        light = _make_light_switch(brightness=1)
        result = light.brightness
        assert result == round(1 * 255 / 100)
        assert isinstance(result, int)


# ---------------------------------------------------------------------------
# LightSwitch.hs_color
# ---------------------------------------------------------------------------

class TestLightSwitchHsColor:
    def test_hs_color_pure_red(self):
        """0xFF0000 → (0°, 100%) in HS."""
        from homeassistant.util.color import color_RGB_to_hs
        light = _make_light_switch(rgb=0xFF0000)
        hs = light.hs_color
        expected = color_RGB_to_hs(255, 0, 0)
        assert abs(hs[0] - expected[0]) < 1
        assert abs(hs[1] - expected[1]) < 1

    def test_hs_color_pure_green(self):
        from homeassistant.util.color import color_RGB_to_hs
        light = _make_light_switch(rgb=0x00FF00)
        hs = light.hs_color
        expected = color_RGB_to_hs(0, 255, 0)
        assert abs(hs[0] - expected[0]) < 1

    def test_hs_color_white(self):
        """White (0xFFFFFF) saturation must be 0."""
        light = _make_light_switch(rgb=0xFFFFFF)
        hs = light.hs_color
        assert hs[1] == 0.0  # fully desaturated

    def test_hs_color_black(self):
        """Black (0x000000) must not raise."""
        light = _make_light_switch(rgb=0x000000)
        hs = light.hs_color
        assert hs is not None


# ---------------------------------------------------------------------------
# LightSwitch.turn_on — brightness clamp
# ---------------------------------------------------------------------------

class TestLightSwitchTurnOnBrightness:
    def test_turn_on_brightness_clamp_never_zero(self):
        """brightness=1 (0.4% of 255) would round to 0; must be clamped to 1."""
        light = _make_light_switch(binarystate=False)
        light.turn_on(**{ATTR_BRIGHTNESS: 1})
        assert light._device.brightness >= 1

    def test_turn_on_brightness_full_sets_100(self):
        """brightness=255 must map to 100%."""
        light = _make_light_switch(binarystate=True)
        light.turn_on(**{ATTR_BRIGHTNESS: 255})
        assert light._device.brightness == 100

    def test_turn_on_brightness_half(self):
        """brightness=128 must map to round(128*100/255)."""
        light = _make_light_switch(binarystate=True)
        light.turn_on(**{ATTR_BRIGHTNESS: 128})
        assert light._device.brightness == round(128 * 100 / 255)

    def test_turn_on_without_brightness_does_not_set_device_brightness(self):
        """No ATTR_BRIGHTNESS kwarg must not write to device.brightness."""
        light = _make_light_switch(binarystate=True, brightness=50)
        light.turn_on()
        assert light._device.brightness == 50  # unchanged

    def test_turn_on_brightness_ignored_if_device_not_supports(self):
        """If device doesn't support brightness, don't write device.brightness."""
        light = _make_light_switch(binarystate=True, supports_brightness=False, brightness=50)
        light.turn_on(**{ATTR_BRIGHTNESS: 200})
        assert light._device.brightness == 50  # unchanged


# ---------------------------------------------------------------------------
# LightSwitch.turn_on — binarystate toggle
# ---------------------------------------------------------------------------

class TestLightSwitchTurnOnBinarystate:
    def test_turn_on_sets_binarystate_true_when_off(self):
        """When light is off, turn_on must set binarystate=True."""
        light = _make_light_switch(binarystate=False)
        light.turn_on()
        assert light._device.binarystate is True

    def test_turn_on_binarystate_stays_true_when_already_on(self):
        """When already on, turn_on must leave binarystate True (no crash, no toggle)."""
        light = _make_light_switch(binarystate=True)
        light.turn_on()
        assert light._device.binarystate is True

    def test_turn_on_always_calls_schedule_update_ha_state(self):
        light = _make_light_switch(binarystate=True)
        light.turn_on()
        light.schedule_update_ha_state.assert_called_once()


# ---------------------------------------------------------------------------
# LightSwitch.turn_off
# ---------------------------------------------------------------------------

class TestLightSwitchTurnOff:
    def test_turn_off_sets_binarystate_false(self):
        light = _make_light_switch(binarystate=True)
        light.turn_off()
        assert light._device.binarystate is False

    def test_turn_off_already_off_stays_false(self):
        light = _make_light_switch(binarystate=False)
        light.turn_off()
        assert light._device.binarystate is False

    def test_turn_off_accepts_extra_kwargs(self):
        """turn_off(**kwargs) must not raise on extra kwargs."""
        light = _make_light_switch(binarystate=True)
        light.turn_off(some_extra="ignored")
        assert light._device.binarystate is False


# ---------------------------------------------------------------------------
# MotionDetectorLight helpers
# ---------------------------------------------------------------------------

def _make_motion_light(
    binaryswitch=False,
    multi_level_switch=80,
):
    """Build MotionDetectorLight via __new__ with injected device."""
    light = MotionDetectorLight.__new__(MotionDetectorLight)
    light._device = SimpleNamespace(
        name="Motion Light",
        id="md-1",
        root_device_id="root-1",
        binaryswitch=binaryswitch,
        multi_level_switch=multi_level_switch,
    )
    light._attr_name = "Motion Light"
    light._attr_unique_id = "root-1_md-1_motionlight"
    return light


# ---------------------------------------------------------------------------
# MotionDetectorLight class-level attrs
# HA parent classes shadow _attr_supported_color_modes/_attr_color_mode with
# properties, so read via instance (which returns the underlying _attr value).
# ---------------------------------------------------------------------------

class TestMotionDetectorLightClassAttrs:
    def test_supported_color_modes_is_brightness(self):
        light = _make_motion_light()
        assert ColorMode.BRIGHTNESS in light.supported_color_modes

    def test_color_mode_is_brightness(self):
        light = _make_motion_light()
        assert light.color_mode == ColorMode.BRIGHTNESS


# ---------------------------------------------------------------------------
# MotionDetectorLight.is_on
# ---------------------------------------------------------------------------

class TestMotionDetectorLightIsOn:
    def test_is_on_true_when_binaryswitch_true(self):
        light = _make_motion_light(binaryswitch=True)
        assert light.is_on is True

    def test_is_on_false_when_binaryswitch_false(self):
        light = _make_motion_light(binaryswitch=False)
        assert light.is_on is False


# ---------------------------------------------------------------------------
# MotionDetectorLight.brightness
# ---------------------------------------------------------------------------

class TestMotionDetectorLightBrightness:
    def test_brightness_100_maps_to_255(self):
        light = _make_motion_light(multi_level_switch=100)
        assert light.brightness == 255

    def test_brightness_50_scales(self):
        light = _make_motion_light(multi_level_switch=50)
        assert light.brightness == round(50 * 255 / 100)

    def test_brightness_zero_level(self):
        light = _make_motion_light(multi_level_switch=0)
        assert light.brightness == 0

    def test_brightness_none_returns_zero(self):
        """None multi_level_switch must return 0, not raise TypeError."""
        light = _make_motion_light(multi_level_switch=None)
        assert light.brightness == 0


# ---------------------------------------------------------------------------
# MotionDetectorLight.turn_on
# ---------------------------------------------------------------------------

class TestMotionDetectorLightTurnOn:
    def test_turn_on_without_brightness_sets_binaryswitch_when_off(self):
        light = _make_motion_light(binaryswitch=False)
        light.turn_on()
        assert light._device.binaryswitch is True

    def test_turn_on_without_brightness_does_not_touch_multi_level_switch(self):
        light = _make_motion_light(binaryswitch=False, multi_level_switch=50)
        light.turn_on()
        assert light._device.multi_level_switch == 50  # unchanged

    def test_turn_on_with_brightness_sets_level(self):
        """ATTR_BRIGHTNESS=255 must set multi_level_switch=100."""
        light = _make_motion_light(binaryswitch=True)
        light.turn_on(**{ATTR_BRIGHTNESS: 255})
        assert light._device.multi_level_switch == 100

    def test_turn_on_with_brightness_scales_half(self):
        light = _make_motion_light(binaryswitch=True)
        light.turn_on(**{ATTR_BRIGHTNESS: 128})
        expected = round(128 * 100 / 255)
        assert light._device.multi_level_switch == expected

    def test_turn_on_brightness_clamp_to_1_not_zero(self):
        """brightness=1 would round to 0; must be clamped to 1."""
        light = _make_motion_light(binaryswitch=True)
        light.turn_on(**{ATTR_BRIGHTNESS: 1})
        assert light._device.multi_level_switch >= 1

    def test_turn_on_does_not_set_binaryswitch_when_already_on(self):
        """When already on, turn_on must not flip binaryswitch."""
        light = _make_motion_light(binaryswitch=True)
        light.turn_on()
        assert light._device.binaryswitch is True  # stays True, no double-write

    def test_turn_on_sets_binaryswitch_to_true_when_was_off(self):
        light = _make_motion_light(binaryswitch=False)
        light.turn_on(**{ATTR_BRIGHTNESS: 200})
        assert light._device.binaryswitch is True


# ---------------------------------------------------------------------------
# MotionDetectorLight.turn_off
# ---------------------------------------------------------------------------

class TestMotionDetectorLightTurnOff:
    def test_turn_off_sets_binaryswitch_false(self):
        light = _make_motion_light(binaryswitch=True)
        light.turn_off()
        assert light._device.binaryswitch is False

    def test_turn_off_already_off(self):
        light = _make_motion_light(binaryswitch=False)
        light.turn_off()
        assert light._device.binaryswitch is False

    def test_turn_off_accepts_extra_kwargs(self):
        light = _make_motion_light(binaryswitch=True)
        light.turn_off(transition=0)
        assert light._device.binaryswitch is False
