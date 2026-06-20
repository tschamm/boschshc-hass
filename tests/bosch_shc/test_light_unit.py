"""Unit tests for LightSwitch.__init__ (lines 43-77 of light.py).

Calls LightSwitch(device=..., entry_id=...) directly so that the actual
__init__ code is executed and measured by coverage — unlike test_light.py
which bypasses __init__ via __new__.

No HA harness needed: SHCEntity.__init__ only reads device.name,
device.root_device_id, and device.id, then calls _update_attr() which is
a no-op in SHCEntity. homeassistant.helpers.entity.Entity.__init__ is
similarly attribute-only and requires no hass context.
"""

from types import SimpleNamespace

from homeassistant.components.light import ColorMode
from homeassistant.util import color as color_util

from custom_components.bosch_shc.light import LightSwitch


# ---------------------------------------------------------------------------
# Helper: build a fake device accepted by SHCEntity.__init__ + LightSwitch
# ---------------------------------------------------------------------------

def _make_device(**kwargs):
    defaults = dict(
        # SHCEntity.__init__ reads these three
        name="test-light",
        root_device_id="root1",
        id="dev1",
        # LightSwitch.__init__ reads these
        supports_color_hsb=False,
        supports_color_temp=False,
        supports_brightness=True,
        min_color_temperature=150,
        max_color_temperature=500,
        # properties used by other tests (not __init__, but needed for repr)
        binarystate=True,
        brightness=50,
        rgb=0xFFFFFF,
        color=200,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# __init__ line 70-71: BRIGHTNESS mode (brightness=True, no color support)
# ---------------------------------------------------------------------------

def test_init_brightness_only_mode():
    """supports_brightness=True and no color → BRIGHTNESS mode."""
    sw = LightSwitch(device=_make_device(
        supports_brightness=True,
        supports_color_hsb=False,
        supports_color_temp=False,
    ), entry_id="test")
    assert sw._attr_color_mode == ColorMode.BRIGHTNESS
    assert sw._attr_supported_color_modes == {ColorMode.BRIGHTNESS}


# ---------------------------------------------------------------------------
# __init__ lines 46-48: HS mode (hsb only)
# ---------------------------------------------------------------------------

def test_init_hs_only_mode():
    """supports_color_hsb=True → HS color mode."""
    sw = LightSwitch(device=_make_device(
        supports_color_hsb=True,
        supports_color_temp=False,
        supports_brightness=False,
    ), entry_id="test")
    assert sw._attr_color_mode == ColorMode.HS
    assert ColorMode.HS in sw._attr_supported_color_modes
    assert ColorMode.BRIGHTNESS not in sw._attr_supported_color_modes
    assert ColorMode.ONOFF not in sw._attr_supported_color_modes


# ---------------------------------------------------------------------------
# __init__ lines 49-54: COLOR_TEMP mode (color_temp only, no hsb)
# ---------------------------------------------------------------------------

def test_init_color_temp_only_mode():
    """supports_color_temp=True (no hsb) → COLOR_TEMP mode."""
    sw = LightSwitch(device=_make_device(
        supports_color_hsb=False,
        supports_color_temp=True,
        supports_brightness=False,
    ), entry_id="test")
    assert sw._attr_color_mode == ColorMode.COLOR_TEMP
    assert ColorMode.COLOR_TEMP in sw._attr_supported_color_modes
    assert ColorMode.BRIGHTNESS not in sw._attr_supported_color_modes


# ---------------------------------------------------------------------------
# __init__ lines 46-54: HS takes priority when both HSB + COLOR_TEMP set
# ---------------------------------------------------------------------------

def test_init_hs_and_color_temp_hs_priority():
    """Both HSB and COLOR_TEMP → HS takes priority as default color_mode."""
    sw = LightSwitch(device=_make_device(
        supports_color_hsb=True,
        supports_color_temp=True,
        supports_brightness=True,
    ), entry_id="test")
    assert sw._attr_color_mode == ColorMode.HS
    assert ColorMode.HS in sw._attr_supported_color_modes
    assert ColorMode.COLOR_TEMP in sw._attr_supported_color_modes
    assert ColorMode.BRIGHTNESS not in sw._attr_supported_color_modes


# ---------------------------------------------------------------------------
# __init__ lines 73-77: ONOFF mode (no brightness, no color)
# ---------------------------------------------------------------------------

def test_init_onoff_mode():
    """No brightness, no color supports → ONOFF mode."""
    sw = LightSwitch(device=_make_device(
        supports_brightness=False,
        supports_color_hsb=False,
        supports_color_temp=False,
    ), entry_id="test")
    assert sw._attr_color_mode == ColorMode.ONOFF
    assert sw._attr_supported_color_modes == {ColorMode.ONOFF}


# ---------------------------------------------------------------------------
# __init__ lines 56-65: min/max kelvin set when supports_color_hsb=True
# ---------------------------------------------------------------------------

def test_init_kelvin_range_set_when_hsb():
    """min/max_color_temp_kelvin computed from device mired when HSB supported."""
    sw = LightSwitch(device=_make_device(
        supports_color_hsb=True,
        min_color_temperature=150,
        max_color_temperature=500,
    ), entry_id="test")
    assert sw._attr_min_color_temp_kelvin == color_util.color_temperature_mired_to_kelvin(150)
    assert sw._attr_max_color_temp_kelvin == color_util.color_temperature_mired_to_kelvin(500)


# ---------------------------------------------------------------------------
# __init__ lines 56-65: min/max kelvin set when supports_color_temp=True
# ---------------------------------------------------------------------------

def test_init_kelvin_range_set_when_color_temp():
    """min/max_color_temp_kelvin computed from device mired when COLOR_TEMP supported."""
    sw = LightSwitch(device=_make_device(
        supports_color_hsb=False,
        supports_color_temp=True,
        min_color_temperature=153,
        max_color_temperature=454,
    ), entry_id="test")
    assert sw._attr_min_color_temp_kelvin == color_util.color_temperature_mired_to_kelvin(153)
    assert sw._attr_max_color_temp_kelvin == color_util.color_temperature_mired_to_kelvin(454)


# ---------------------------------------------------------------------------
# __init__ lines 66-71: BRIGHTNESS NOT added when HS already present
# (exercises the `len == 0` guard on line 68 being False)
# ---------------------------------------------------------------------------

def test_init_brightness_not_added_when_hs_present():
    """When HS is already in supported_color_modes, BRIGHTNESS must NOT be added."""
    sw = LightSwitch(device=_make_device(
        supports_color_hsb=True,
        supports_color_temp=False,
        supports_brightness=True,
    ), entry_id="test")
    assert ColorMode.BRIGHTNESS not in sw._attr_supported_color_modes
    assert ColorMode.HS in sw._attr_supported_color_modes
    # color_mode must remain HS (not overwritten by brightness branch)
    assert sw._attr_color_mode == ColorMode.HS


# ---------------------------------------------------------------------------
# SHCEntity base fields set correctly via __init__
# ---------------------------------------------------------------------------

def test_init_base_fields_set():
    """Verify SHCEntity.__init__ sets _attr_name and _attr_unique_id correctly."""
    sw = LightSwitch(device=_make_device(
        name="My Light",
        root_device_id="root-X",
        id="dev-Y",
    ), entry_id="entry-42")
    assert sw._attr_name == "My Light"
    assert sw._attr_unique_id == "root-X_dev-Y"
    assert sw._entry_id == "entry-42"
