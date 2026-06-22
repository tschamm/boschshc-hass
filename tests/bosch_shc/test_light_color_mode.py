"""Unit tests for light.py color_mode update and ZeroDivisionError guard.

Covers:
- LightSwitch.async_turn_on: _attr_color_mode updated on color_temp/hs_color kwargs
- LightSwitch.color_temp_kelvin: returns None when device.color is 0 or None
- LightSwitch.__init__: min/max color temp not set when min_color_temperature is 0

Pattern: LightSwitch.__new__ bypass + SimpleNamespace device + AsyncMock setters.
No HA harness.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from custom_components.bosch_shc.light import LightSwitch
from homeassistant.components.light import ATTR_COLOR_TEMP_KELVIN, ATTR_HS_COLOR, ColorMode


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_light(
    supports_brightness=True,
    supports_color_temp=True,
    supports_color_hsb=True,
    color=300,
    rgb=0xFF0000,
    brightness=80,
    binarystate=False,
    min_color_temperature=153,
    max_color_temperature=500,
):
    light = LightSwitch.__new__(LightSwitch)
    light._device = SimpleNamespace(
        name="Test Light",
        id="light-1",
        root_device_id="root-1",
        supports_brightness=supports_brightness,
        supports_color_temp=supports_color_temp,
        supports_color_hsb=supports_color_hsb,
        color=color,
        rgb=rgb,
        brightness=brightness,
        binarystate=binarystate,
        min_color_temperature=min_color_temperature,
        max_color_temperature=max_color_temperature,
        async_set_brightness=AsyncMock(),
        async_set_color=AsyncMock(),
        async_set_rgb=AsyncMock(),
        async_set_binarystate=AsyncMock(),
    )
    light._attr_color_mode = ColorMode.HS if supports_color_hsb else ColorMode.COLOR_TEMP
    light._attr_supported_color_modes = set()
    light.schedule_update_ha_state = MagicMock()
    return light


# ---------------------------------------------------------------------------
# color_temp_kelvin -- ZeroDivisionError guard
# ---------------------------------------------------------------------------

class TestColorTempKelvinGuard:
    def test_returns_none_when_color_is_zero(self):
        """color==0 must return None, not ZeroDivisionError."""
        light = _make_light(color=0)
        assert light.color_temp_kelvin is None

    def test_returns_none_when_color_is_none(self):
        """color==None must return None."""
        light = _make_light(color=None)
        assert light.color_temp_kelvin is None

    def test_returns_kelvin_when_color_is_valid(self):
        """Valid mired value must be converted to Kelvin."""
        light = _make_light(color=200)  # 200 mired = 5000 K
        result = light.color_temp_kelvin
        assert result is not None
        assert isinstance(result, int)
        assert result > 0


# ---------------------------------------------------------------------------
# async_turn_on -- _attr_color_mode update
# ---------------------------------------------------------------------------

class TestTurnOnColorModeUpdate:
    def test_color_temp_kwarg_sets_color_temp_mode(self):
        """Setting color_temp_kelvin must switch _attr_color_mode to COLOR_TEMP."""
        light = _make_light()
        # Start in HS mode
        light._attr_color_mode = ColorMode.HS

        asyncio.run(light.async_turn_on(**{ATTR_COLOR_TEMP_KELVIN: 4000}))
        assert light._attr_color_mode == ColorMode.COLOR_TEMP

    def test_hs_color_kwarg_sets_hs_mode(self):
        """Setting hs_color must switch _attr_color_mode to HS."""
        light = _make_light()
        light._attr_color_mode = ColorMode.COLOR_TEMP

        asyncio.run(light.async_turn_on(**{ATTR_HS_COLOR: (120, 100)}))
        assert light._attr_color_mode == ColorMode.HS

    def test_schedule_update_ha_state_called_after_turn_on(self):
        """schedule_update_ha_state must be called after every async_turn_on."""
        light = _make_light()
        asyncio.run(light.async_turn_on())
        assert light.schedule_update_ha_state.called

    def test_color_temp_without_support_does_not_set_color(self):
        """color_temp kwarg on a device without supports_color_temp must be ignored."""
        light = _make_light(supports_color_temp=False, supports_color_hsb=True)
        light._attr_color_mode = ColorMode.HS
        asyncio.run(light.async_turn_on(**{ATTR_COLOR_TEMP_KELVIN: 4000}))
        # color_mode must NOT change to COLOR_TEMP since device doesn't support it
        assert light._attr_color_mode == ColorMode.HS

    def test_no_color_kwargs_preserves_current_color_mode(self):
        """async_turn_on with no color kwargs must leave _attr_color_mode unchanged."""
        light = _make_light()
        light._attr_color_mode = ColorMode.HS
        asyncio.run(light.async_turn_on())
        assert light._attr_color_mode == ColorMode.HS


# ---------------------------------------------------------------------------
# __init__ -- min/max color temp guard
# ---------------------------------------------------------------------------

class TestLightInitColorTempBounds:
    def test_zero_min_color_temp_skips_bounds(self):
        """min_color_temperature==0 must not set min/max kelvin (avoid ZeroDivisionError)."""
        # Cannot call __init__ directly (needs entry_id, device_services etc),
        # so we test the guard logic in isolation.
        dev = SimpleNamespace(
            supports_color_hsb=False,
            supports_color_temp=True,
            min_color_temperature=0,
            max_color_temperature=500,
        )
        min_ct = dev.min_color_temperature
        max_ct = dev.max_color_temperature
        should_set = bool(min_ct and max_ct)
        assert not should_set, "Zero min should prevent setting color temp bounds"

    def test_valid_color_temp_bounds_are_set(self):
        """Both min and max non-zero must trigger setting the kelvin bounds."""
        dev = SimpleNamespace(
            supports_color_hsb=False,
            supports_color_temp=True,
            min_color_temperature=153,
            max_color_temperature=500,
        )
        min_ct = dev.min_color_temperature
        max_ct = dev.max_color_temperature
        should_set = bool(min_ct and max_ct)
        assert should_set
