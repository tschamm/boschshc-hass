"""Isolation-safe unit tests for light.py (LightSwitch).

Tests bypass SHCEntity.__init__ via Cls.__new__(Cls) and set _device directly.
No HA harness required.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

from homeassistant.components.light import ColorMode
from homeassistant.util import color as color_util

from custom_components.bosch_shc.light import LightSwitch


# ---------------------------------------------------------------------------
# Helper: build a LightSwitch without calling SHCEntity.__init__
# ---------------------------------------------------------------------------

def _make_device(
    *,
    binarystate=True,
    brightness=100,
    rgb=0xFFFFFF,
    color=200,  # mired
    supports_color_hsb=False,
    supports_color_temp=False,
    supports_brightness=True,
    min_color_temperature=150,
    max_color_temperature=500,
):
    return SimpleNamespace(
        binarystate=binarystate,
        brightness=brightness,
        rgb=rgb,
        color=color,
        supports_color_hsb=supports_color_hsb,
        supports_color_temp=supports_color_temp,
        supports_brightness=supports_brightness,
        min_color_temperature=min_color_temperature,
        max_color_temperature=max_color_temperature,
    )


def _make_switch(device):
    """Instantiate LightSwitch bypassing SHCEntity.__init__."""
    sw = LightSwitch.__new__(LightSwitch)
    sw._device = device
    # Replay the relevant part of __init__ (color-mode detection only)
    sw._attr_supported_color_modes = set()
    if device.supports_color_hsb:
        sw._attr_supported_color_modes.add(ColorMode.HS)
        sw._attr_color_mode = ColorMode.HS
    if device.supports_color_temp:
        sw._attr_supported_color_modes.add(ColorMode.COLOR_TEMP)
        sw._attr_color_mode = ColorMode.COLOR_TEMP
    if device.supports_color_hsb or device.supports_color_temp:
        sw._attr_min_color_temp_kelvin = color_util.color_temperature_mired_to_kelvin(
            device.min_color_temperature
        )
        sw._attr_max_color_temp_kelvin = color_util.color_temperature_mired_to_kelvin(
            device.max_color_temperature
        )
    if device.supports_brightness:
        if len(sw._attr_supported_color_modes) == 0:
            sw._attr_supported_color_modes.add(ColorMode.BRIGHTNESS)
            sw._attr_color_mode = ColorMode.BRIGHTNESS
    else:
        if len(sw._attr_supported_color_modes) == 0:
            sw._attr_supported_color_modes.add(ColorMode.ONOFF)
            sw._attr_color_mode = ColorMode.ONOFF
    return sw


# ---------------------------------------------------------------------------
# is_on
# ---------------------------------------------------------------------------

def test_is_on_true():
    sw = _make_switch(_make_device(binarystate=True))
    assert sw.is_on is True


def test_is_on_false():
    sw = _make_switch(_make_device(binarystate=False))
    assert sw.is_on is False


def test_is_on_none():
    sw = _make_switch(_make_device(binarystate=None))
    assert sw.is_on is None


# ---------------------------------------------------------------------------
# brightness  (device 0-100 → HA 0-255, rounded)
# ---------------------------------------------------------------------------

def test_brightness_full():
    sw = _make_switch(_make_device(brightness=100))
    assert sw.brightness == 255


def test_brightness_zero():
    sw = _make_switch(_make_device(brightness=0))
    assert sw.brightness == 0


def test_brightness_half():
    # 50 * 255 / 100 = 127.5 → rounds to 128
    sw = _make_switch(_make_device(brightness=50))
    assert sw.brightness == round(50 * 255 / 100)


def test_brightness_one_percent():
    # 1 * 255 / 100 = 2.55 → rounds to 3
    sw = _make_switch(_make_device(brightness=1))
    assert sw.brightness == round(1 * 255 / 100)


def test_brightness_99_percent():
    sw = _make_switch(_make_device(brightness=99))
    assert sw.brightness == round(99 * 255 / 100)


# ---------------------------------------------------------------------------
# color_temp_kelvin  (device in mired → HA kelvin)
# ---------------------------------------------------------------------------

def test_color_temp_kelvin_200mired():
    sw = _make_switch(_make_device(supports_color_temp=True, color=200))
    expected = color_util.color_temperature_mired_to_kelvin(200)
    assert sw.color_temp_kelvin == expected


def test_color_temp_kelvin_370mired():
    sw = _make_switch(_make_device(supports_color_temp=True, color=370))
    expected = color_util.color_temperature_mired_to_kelvin(370)
    assert sw.color_temp_kelvin == expected


def test_color_temp_kelvin_min_mired():
    sw = _make_switch(_make_device(supports_color_temp=True, color=153))
    expected = color_util.color_temperature_mired_to_kelvin(153)
    assert sw.color_temp_kelvin == expected


def test_color_temp_kelvin_max_mired():
    sw = _make_switch(_make_device(supports_color_temp=True, color=500))
    expected = color_util.color_temperature_mired_to_kelvin(500)
    assert sw.color_temp_kelvin == expected


# ---------------------------------------------------------------------------
# hs_color  (device RGB int → HA (h, s))
# ---------------------------------------------------------------------------

def test_hs_color_white():
    sw = _make_switch(_make_device(supports_color_hsb=True, rgb=0xFFFFFF))
    hs = sw.hs_color
    expected = color_util.color_RGB_to_hs(255, 255, 255)
    assert hs == expected


def test_hs_color_red():
    sw = _make_switch(_make_device(supports_color_hsb=True, rgb=0xFF0000))
    hs = sw.hs_color
    expected = color_util.color_RGB_to_hs(255, 0, 0)
    assert hs == expected


def test_hs_color_green():
    sw = _make_switch(_make_device(supports_color_hsb=True, rgb=0x00FF00))
    hs = sw.hs_color
    expected = color_util.color_RGB_to_hs(0, 255, 0)
    assert hs == expected


def test_hs_color_blue():
    sw = _make_switch(_make_device(supports_color_hsb=True, rgb=0x0000FF))
    hs = sw.hs_color
    expected = color_util.color_RGB_to_hs(0, 0, 255)
    assert hs == expected


def test_hs_color_black():
    sw = _make_switch(_make_device(supports_color_hsb=True, rgb=0x000000))
    hs = sw.hs_color
    expected = color_util.color_RGB_to_hs(0, 0, 0)
    assert hs == expected


def test_hs_color_arbitrary():
    # 0x1A2B3C → R=26, G=43, B=60
    sw = _make_switch(_make_device(supports_color_hsb=True, rgb=0x1A2B3C))
    hs = sw.hs_color
    expected = color_util.color_RGB_to_hs(0x1A, 0x2B, 0x3C)
    assert hs == expected


# ---------------------------------------------------------------------------
# supported_color_modes + color_mode (set in __init__)
# ---------------------------------------------------------------------------

def test_color_mode_onoff_only():
    sw = _make_switch(_make_device(supports_brightness=False, supports_color_hsb=False, supports_color_temp=False))
    assert sw._attr_color_mode == ColorMode.ONOFF
    assert sw._attr_supported_color_modes == {ColorMode.ONOFF}


def test_color_mode_brightness_only():
    sw = _make_switch(_make_device(supports_brightness=True, supports_color_hsb=False, supports_color_temp=False))
    assert sw._attr_color_mode == ColorMode.BRIGHTNESS
    assert sw._attr_supported_color_modes == {ColorMode.BRIGHTNESS}


def test_color_mode_hs_only():
    sw = _make_switch(_make_device(supports_color_hsb=True, supports_color_temp=False, supports_brightness=True))
    assert sw._attr_color_mode == ColorMode.HS
    assert ColorMode.HS in sw._attr_supported_color_modes
    # BRIGHTNESS must NOT be added when HS is present
    assert ColorMode.BRIGHTNESS not in sw._attr_supported_color_modes


def test_color_mode_color_temp_only():
    sw = _make_switch(_make_device(supports_color_temp=True, supports_color_hsb=False, supports_brightness=True))
    assert sw._attr_color_mode == ColorMode.COLOR_TEMP
    assert ColorMode.COLOR_TEMP in sw._attr_supported_color_modes
    assert ColorMode.BRIGHTNESS not in sw._attr_supported_color_modes


def test_color_mode_hs_and_color_temp():
    sw = _make_switch(_make_device(supports_color_hsb=True, supports_color_temp=True, supports_brightness=True))
    assert ColorMode.HS in sw._attr_supported_color_modes
    assert ColorMode.COLOR_TEMP in sw._attr_supported_color_modes
    assert ColorMode.BRIGHTNESS not in sw._attr_supported_color_modes
    # color_mode ends up COLOR_TEMP (last assignment in __init__)
    assert sw._attr_color_mode == ColorMode.COLOR_TEMP


def test_min_max_color_temp_kelvin_set_when_color_hsb():
    sw = _make_switch(_make_device(
        supports_color_hsb=True,
        min_color_temperature=150,
        max_color_temperature=500,
    ))
    assert sw._attr_min_color_temp_kelvin == color_util.color_temperature_mired_to_kelvin(150)
    assert sw._attr_max_color_temp_kelvin == color_util.color_temperature_mired_to_kelvin(500)


def test_min_max_color_temp_kelvin_set_when_color_temp():
    sw = _make_switch(_make_device(
        supports_color_temp=True,
        min_color_temperature=153,
        max_color_temperature=454,
    ))
    assert sw._attr_min_color_temp_kelvin == color_util.color_temperature_mired_to_kelvin(153)
    assert sw._attr_max_color_temp_kelvin == color_util.color_temperature_mired_to_kelvin(454)


# ---------------------------------------------------------------------------
# turn_on logic (brightness / color_temp / hs_color + binarystate)
# ---------------------------------------------------------------------------

def test_turn_on_sets_brightness():
    device = _make_device(binarystate=True, brightness=50, supports_brightness=True)
    sw = _make_switch(device)
    sw.turn_on(brightness=128)
    # 128 * 100 / 255 = 50.196... → max(round, 1) = 50
    assert device.brightness == max(round(128 * 100 / 255), 1)


def test_turn_on_brightness_minimum_clamps_to_1():
    device = _make_device(binarystate=True, supports_brightness=True)
    sw = _make_switch(device)
    sw.turn_on(brightness=1)
    # 1 * 100 / 255 = 0.39 → round = 0 → max(0, 1) = 1
    assert device.brightness == 1


def test_turn_on_brightness_zero_clamps_to_1():
    device = _make_device(binarystate=True, supports_brightness=True)
    sw = _make_switch(device)
    sw.turn_on(brightness=0)
    assert device.brightness == 1


def test_turn_on_sets_color_temp():
    device = _make_device(binarystate=True, supports_color_temp=True, color=200)
    sw = _make_switch(device)
    sw.turn_on(color_temp_kelvin=4000)
    expected_mired = color_util.color_temperature_kelvin_to_mired(4000)
    assert device.color == expected_mired


def test_turn_on_sets_hs_color():
    device = _make_device(binarystate=True, supports_color_hsb=True, rgb=0)
    sw = _make_switch(device)
    sw.turn_on(hs_color=(120.0, 100.0))  # pure green
    rgb = color_util.color_hs_to_RGB(120.0, 100.0)
    expected_raw = (rgb[0] << 16) + (rgb[1] << 8) + rgb[2]
    assert device.rgb == expected_raw


def test_turn_on_activates_binarystate_when_off():
    device = _make_device(binarystate=False)
    sw = _make_switch(device)
    sw.turn_on()
    assert device.binarystate is True


def test_turn_on_does_not_double_set_binarystate_when_already_on():
    """binarystate stays True — no redundant write."""
    device = _make_device(binarystate=True)
    sw = _make_switch(device)
    original = device.binarystate
    sw.turn_on()
    assert device.binarystate == original


def test_turn_on_no_kwargs_does_not_touch_brightness():
    device = _make_device(binarystate=True, brightness=75, supports_brightness=True)
    sw = _make_switch(device)
    sw.turn_on()
    assert device.brightness == 75  # unchanged


def test_turn_on_brightness_ignored_when_not_supported():
    device = _make_device(binarystate=True, brightness=50, supports_brightness=False)
    sw = _make_switch(device)
    sw.turn_on(brightness=200)
    assert device.brightness == 50  # must not change


def test_turn_on_color_temp_ignored_when_not_supported():
    device = _make_device(binarystate=True, color=200, supports_color_temp=False)
    sw = _make_switch(device)
    sw.turn_on(color_temp_kelvin=3000)
    assert device.color == 200  # must not change


def test_turn_on_hs_color_ignored_when_not_supported():
    device = _make_device(binarystate=True, rgb=0xFF0000, supports_color_hsb=False)
    sw = _make_switch(device)
    sw.turn_on(hs_color=(240.0, 100.0))
    assert device.rgb == 0xFF0000  # must not change


# ---------------------------------------------------------------------------
# turn_off
# ---------------------------------------------------------------------------

def test_turn_off_sets_binarystate_false():
    device = _make_device(binarystate=True)
    sw = _make_switch(device)
    sw.turn_off()
    assert device.binarystate is False


def test_turn_off_already_off_stays_off():
    device = _make_device(binarystate=False)
    sw = _make_switch(device)
    sw.turn_off()
    assert device.binarystate is False
