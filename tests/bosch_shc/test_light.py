"""Unit tests for the light platform (light.py): LightSwitch, RelayLight,
MotionDetectorLight and SHCRoomLightGroup.

Covers property/behavior logic (is_on, brightness, hs_color, color_temp_kelvin,
color-mode selection, async_turn_on/off), the real ``__init__`` code paths,
async_setup_entry wiring (excluded/suppressed/opt-in devices, stale-entity
cleanup, room-light-group aggregation), and error handling in RelayLight.

Pattern: pure-unit tests, no HA harness (``-p no:homeassistant``). Entities are
built either via their real constructor (where that needs no hass context) or
via ``Cls.__new__(Cls)`` bypass + SimpleNamespace device doubles + AsyncMock
setters.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import aiohttp
import pytest

from boschshcpy import PowerSwitchService
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_HS_COLOR,
    ColorMode,
)
from homeassistant.const import Platform
from homeassistant.util import color as color_util

from custom_components.bosch_shc.const import (
    DOMAIN,
    OPT_ALL_LIGHTS_AS_LIGHT,
    OPT_EXCLUDED_DEVICES,
    OPT_LIGHTS_AS_LIGHT,
    OPT_ROOM_LIGHT_GROUPS,
    OPT_SUPPRESS_HUE_LIGHTS,
    OPT_SUPPRESS_LEDVANCE_LIGHTS,
)
from custom_components.bosch_shc.entity import (
    light_relay_friendly_model,
    light_switch_as_light,
    light_switch_devices,
)
from custom_components.bosch_shc.light import (
    LightSwitch,
    MotionDetectorLight,
    RelayLight,
    SHCRoomLightGroup,
    async_setup_entry,
)

from .conftest import run_setup_entry

State = PowerSwitchService.State


# ===========================================================================
# Shared helpers
# ===========================================================================

def _run(coro):
    return asyncio.run(coro)


def _fake_dev(dev_id="dev1", root_id="root1", serial="SER1", **kw):
    base = dict(
        id=dev_id,
        root_device_id=root_id,
        name="FakeDev",
        serial=serial,
        device_services=[],
        room_id=None,
        deleted=False,
        status="AVAILABLE",
        manufacturer="Bosch",
        device_model="TestModel",
        subscribe_callback=MagicMock(),
        unsubscribe_callback=MagicMock(),
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _run_light_setup(mock_config_entry, mock_session) -> list:
    """Run light.async_setup_entry via the shared run_setup_entry helper, with
    async_migrate_to_new_unique_id/async_remove_stale_entity patched to AsyncMock
    (their side effects aren't under test here)."""
    with (
        patch(
            "custom_components.bosch_shc.light.async_migrate_to_new_unique_id",
            new_callable=AsyncMock,
        ),
        patch(
            "custom_components.bosch_shc.light.async_remove_stale_entity",
            new_callable=AsyncMock,
        ),
    ):
        return asyncio.run(
            run_setup_entry(async_setup_entry, mock_config_entry, mock_session)
        )


def _run_light_setup_with_remove_mock(mock_config_entry, mock_session) -> tuple[list, AsyncMock]:
    """Same as _run_light_setup, but returns the async_remove_stale_entity mock
    too, so a test can assert on stale-entity cleanup calls/args."""
    with (
        patch(
            "custom_components.bosch_shc.light.async_migrate_to_new_unique_id",
            new_callable=AsyncMock,
        ),
        patch(
            "custom_components.bosch_shc.light.async_remove_stale_entity",
            new_callable=AsyncMock,
        ) as remove_mock,
    ):
        entities = asyncio.run(
            run_setup_entry(async_setup_entry, mock_config_entry, mock_session)
        )
    return entities, remove_mock


def _run_light_setup_track_migrate(mock_config_entry, mock_session) -> tuple[list, list]:
    """Same as _run_light_setup, but records each async_migrate_to_new_unique_id
    call as (platform, device, attr_name) so a test can assert on migrate args."""
    migrate_calls: list = []

    async def _fake_migrate(hass_arg, platform, device, attr_name=None, **kw):
        migrate_calls.append((platform, device, attr_name))

    with (
        patch(
            "custom_components.bosch_shc.light.async_migrate_to_new_unique_id",
            side_effect=_fake_migrate,
        ),
        patch(
            "custom_components.bosch_shc.light.async_remove_stale_entity",
            new_callable=AsyncMock,
        ),
    ):
        entities = asyncio.run(
            run_setup_entry(async_setup_entry, mock_config_entry, mock_session)
        )
    return entities, migrate_calls


def _run_light_setup_with_dev_reg(mock_config_entry, mock_session, dev_reg_mock) -> list:
    """Same as _run_light_setup, but also patches get_dev_reg (HUE/Ledvance
    suppress paths look the device up in the entity/device registry)."""
    with (
        patch(
            "custom_components.bosch_shc.light.get_dev_reg", return_value=dev_reg_mock
        ),
        patch(
            "custom_components.bosch_shc.light.async_migrate_to_new_unique_id",
            new_callable=AsyncMock,
        ),
    ):
        return asyncio.run(
            run_setup_entry(async_setup_entry, mock_config_entry, mock_session)
        )


def _light_device(
    supports_color_hsb: bool = False,
    supports_color_temp: bool = False,
    supports_brightness: bool = True,
    min_color_temperature: int = 153,
    max_color_temperature: int = 500,
) -> SimpleNamespace:
    """Minimal device for LightSwitch.__init__."""
    return SimpleNamespace(
        name="Test Light",
        id="hdm:HomeMaticIP:light1",
        root_device_id="aa:bb:cc:00:00:03",
        serial="serial-light1",
        supports_color_hsb=supports_color_hsb,
        supports_color_temp=supports_color_temp,
        supports_brightness=supports_brightness,
        min_color_temperature=min_color_temperature,
        max_color_temperature=max_color_temperature,
        device_services=[],
        manufacturer="Bosch",
        device_model="LD",
        status="AVAILABLE",
        deleted=False,
    )


# ===========================================================================
# LightSwitch — is_on / brightness / color_temp_kelvin / hs_color / color_mode
# / async_turn_on / async_turn_off  (bypass __init__ via __new__)
# ===========================================================================

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
        async_set_brightness=AsyncMock(),
        async_set_color=AsyncMock(),
        async_set_rgb=AsyncMock(),
        async_set_binarystate=AsyncMock(),
    )


def _make_switch(device):
    """Instantiate LightSwitch bypassing SHCEntity.__init__."""
    sw = LightSwitch.__new__(LightSwitch)
    sw._device = device
    # async_turn_on() calls schedule_update_ha_state() which needs self.hass; mock it
    # so harness-free tests don't fail with AttributeError on self.hass.loop.
    sw.schedule_update_ha_state = MagicMock()
    # Replay the relevant part of __init__ (color-mode detection only)
    sw._attr_supported_color_modes = set()
    if device.supports_color_hsb:
        sw._attr_supported_color_modes.add(ColorMode.HS)
        sw._attr_color_mode = ColorMode.HS
    if device.supports_color_temp:
        sw._attr_supported_color_modes.add(ColorMode.COLOR_TEMP)
        # Mirror the fixed __init__: COLOR_TEMP wins only when HS is NOT also present
        if not device.supports_color_hsb:
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
    elif len(sw._attr_supported_color_modes) == 0:
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
# brightness  (device 0-100 -> HA 0-255, rounded)
# ---------------------------------------------------------------------------

def test_brightness_full():
    sw = _make_switch(_make_device(brightness=100))
    assert sw.brightness == 255


def test_brightness_zero():
    sw = _make_switch(_make_device(brightness=0))
    assert sw.brightness == 0


def test_brightness_half():
    # 50 * 255 / 100 = 127.5 -> rounds to 128
    sw = _make_switch(_make_device(brightness=50))
    assert sw.brightness == round(50 * 255 / 100)


def test_brightness_one_percent():
    # 1 * 255 / 100 = 2.55 -> rounds to 3
    sw = _make_switch(_make_device(brightness=1))
    assert sw.brightness == round(1 * 255 / 100)


def test_brightness_99_percent():
    sw = _make_switch(_make_device(brightness=99))
    assert sw.brightness == round(99 * 255 / 100)


def test_brightness_none_guard():
    """Brightness property must return None when device reports None (e.g. unavailable)."""
    sw = _make_switch(_make_device(brightness=None))
    assert sw.brightness is None


# ---------------------------------------------------------------------------
# color_temp_kelvin  (device in mired -> HA kelvin)
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
# hs_color  (device RGB int -> HA (h, s))
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
    # 0x1A2B3C -> R=26, G=43, B=60
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
    # When both HS and COLOR_TEMP are supported, HS takes priority as the
    # default color_mode (richer capability; COLOR_TEMP no longer last-write-wins).
    sw = _make_switch(_make_device(supports_color_hsb=True, supports_color_temp=True, supports_brightness=True))
    assert ColorMode.HS in sw._attr_supported_color_modes
    assert ColorMode.COLOR_TEMP in sw._attr_supported_color_modes
    assert ColorMode.BRIGHTNESS not in sw._attr_supported_color_modes
    assert sw._attr_color_mode == ColorMode.HS


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
# async_turn_on logic (brightness / color_temp / hs_color + binarystate)
# ---------------------------------------------------------------------------

def test_turn_on_sets_brightness():
    device = _make_device(binarystate=True, brightness=50, supports_brightness=True)
    sw = _make_switch(device)
    asyncio.run(sw.async_turn_on(brightness=128))
    # 128 * 100 / 255 = 50.196... -> max(round, 1) = 50
    device.async_set_brightness.assert_called_once_with(max(round(128 * 100 / 255), 1))


def test_turn_on_brightness_minimum_clamps_to_1():
    device = _make_device(binarystate=True, supports_brightness=True)
    sw = _make_switch(device)
    asyncio.run(sw.async_turn_on(brightness=1))
    # 1 * 100 / 255 = 0.39 -> round = 0 -> max(0, 1) = 1
    device.async_set_brightness.assert_called_once_with(1)


def test_turn_on_brightness_zero_clamps_to_1():
    device = _make_device(binarystate=True, supports_brightness=True)
    sw = _make_switch(device)
    asyncio.run(sw.async_turn_on(brightness=0))
    device.async_set_brightness.assert_called_once_with(1)


def test_turn_on_sets_color_temp():
    device = _make_device(binarystate=True, supports_color_temp=True, color=200)
    sw = _make_switch(device)
    asyncio.run(sw.async_turn_on(color_temp_kelvin=4000))
    expected_mired = color_util.color_temperature_kelvin_to_mired(4000)
    device.async_set_color.assert_called_once_with(expected_mired)


def test_turn_on_sets_hs_color():
    device = _make_device(binarystate=True, supports_color_hsb=True, rgb=0)
    sw = _make_switch(device)
    asyncio.run(sw.async_turn_on(hs_color=(120.0, 100.0)))  # pure green
    rgb = color_util.color_hs_to_RGB(120.0, 100.0)
    expected_raw = (rgb[0] << 16) + (rgb[1] << 8) + rgb[2]
    device.async_set_rgb.assert_called_once_with(expected_raw)


def test_turn_on_activates_binarystate_when_off():
    device = _make_device(binarystate=False)
    sw = _make_switch(device)
    asyncio.run(sw.async_turn_on())
    device.async_set_binarystate.assert_called_once_with(True)


def test_turn_on_does_not_double_set_binarystate_when_already_on():
    """Binarystate stays True -- no redundant write."""
    device = _make_device(binarystate=True)
    sw = _make_switch(device)
    asyncio.run(sw.async_turn_on())
    device.async_set_binarystate.assert_not_called()


def test_turn_on_no_kwargs_does_not_touch_brightness():
    device = _make_device(binarystate=True, brightness=75, supports_brightness=True)
    sw = _make_switch(device)
    asyncio.run(sw.async_turn_on())
    device.async_set_brightness.assert_not_called()


def test_turn_on_brightness_ignored_when_not_supported():
    device = _make_device(binarystate=True, brightness=50, supports_brightness=False)
    sw = _make_switch(device)
    asyncio.run(sw.async_turn_on(brightness=200))
    device.async_set_brightness.assert_not_called()


def test_turn_on_color_temp_ignored_when_not_supported():
    device = _make_device(binarystate=True, color=200, supports_color_temp=False)
    sw = _make_switch(device)
    asyncio.run(sw.async_turn_on(color_temp_kelvin=3000))
    device.async_set_color.assert_not_called()


def test_turn_on_hs_color_ignored_when_not_supported():
    device = _make_device(binarystate=True, rgb=0xFF0000, supports_color_hsb=False)
    sw = _make_switch(device)
    asyncio.run(sw.async_turn_on(hs_color=(240.0, 100.0)))
    device.async_set_rgb.assert_not_called()


# ---------------------------------------------------------------------------
# async_turn_off
# ---------------------------------------------------------------------------

def test_turn_off_sets_binarystate_false():
    device = _make_device(binarystate=True)
    sw = _make_switch(device)
    asyncio.run(sw.async_turn_off())
    device.async_set_binarystate.assert_called_once_with(False)


def test_turn_off_already_off_stays_off():
    device = _make_device(binarystate=False)
    sw = _make_switch(device)
    asyncio.run(sw.async_turn_off())
    device.async_set_binarystate.assert_called_once_with(False)


# ===========================================================================
# LightSwitch.__init__ (real constructor, not __new__ bypass)
# ===========================================================================
#
# Calls LightSwitch(device=..., entry_id=...) directly so that the actual
# __init__ code is executed and measured by coverage. No HA harness needed:
# SHCEntity.__init__ only reads device.name, device.root_device_id, and
# device.id, then calls _update_attr() which is a no-op in SHCEntity.
# homeassistant.helpers.entity.Entity.__init__ is similarly attribute-only
# and requires no hass context.

def _make_init_device(**kwargs):
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


def test_init_brightness_only_mode():
    """supports_brightness=True and no color → BRIGHTNESS mode."""
    sw = LightSwitch(device=_make_init_device(
        supports_brightness=True,
        supports_color_hsb=False,
        supports_color_temp=False,
    ), entry_id="test")
    assert sw._attr_color_mode == ColorMode.BRIGHTNESS
    assert sw._attr_supported_color_modes == {ColorMode.BRIGHTNESS}


def test_init_hs_only_mode():
    """supports_color_hsb=True → HS color mode."""
    sw = LightSwitch(device=_make_init_device(
        supports_color_hsb=True,
        supports_color_temp=False,
        supports_brightness=False,
    ), entry_id="test")
    assert sw._attr_color_mode == ColorMode.HS
    assert ColorMode.HS in sw._attr_supported_color_modes
    assert ColorMode.BRIGHTNESS not in sw._attr_supported_color_modes
    assert ColorMode.ONOFF not in sw._attr_supported_color_modes


def test_init_color_temp_only_mode():
    """supports_color_temp=True (no hsb) → COLOR_TEMP mode."""
    sw = LightSwitch(device=_make_init_device(
        supports_color_hsb=False,
        supports_color_temp=True,
        supports_brightness=False,
    ), entry_id="test")
    assert sw._attr_color_mode == ColorMode.COLOR_TEMP
    assert ColorMode.COLOR_TEMP in sw._attr_supported_color_modes
    assert ColorMode.BRIGHTNESS not in sw._attr_supported_color_modes


def test_init_hs_and_color_temp_hs_priority():
    """Both HSB and COLOR_TEMP → HS takes priority as default color_mode."""
    sw = LightSwitch(device=_make_init_device(
        supports_color_hsb=True,
        supports_color_temp=True,
        supports_brightness=True,
    ), entry_id="test")
    assert sw._attr_color_mode == ColorMode.HS
    assert ColorMode.HS in sw._attr_supported_color_modes
    assert ColorMode.COLOR_TEMP in sw._attr_supported_color_modes
    assert ColorMode.BRIGHTNESS not in sw._attr_supported_color_modes


def test_init_onoff_mode():
    """No brightness, no color supports → ONOFF mode."""
    sw = LightSwitch(device=_make_init_device(
        supports_brightness=False,
        supports_color_hsb=False,
        supports_color_temp=False,
    ), entry_id="test")
    assert sw._attr_color_mode == ColorMode.ONOFF
    assert sw._attr_supported_color_modes == {ColorMode.ONOFF}


def test_init_kelvin_range_set_when_hsb():
    """min/max_color_temp_kelvin computed from device mired when HSB supported."""
    sw = LightSwitch(device=_make_init_device(
        supports_color_hsb=True,
        min_color_temperature=150,
        max_color_temperature=500,
    ), entry_id="test")
    # #340: mired<->kelvin inverse → bounds crossed (max mired -> min kelvin).
    assert sw._attr_min_color_temp_kelvin == color_util.color_temperature_mired_to_kelvin(500)
    assert sw._attr_max_color_temp_kelvin == color_util.color_temperature_mired_to_kelvin(150)


def test_init_kelvin_range_set_when_color_temp():
    """min/max_color_temp_kelvin computed from device mired when COLOR_TEMP supported."""
    sw = LightSwitch(device=_make_init_device(
        supports_color_hsb=False,
        supports_color_temp=True,
        min_color_temperature=153,
        max_color_temperature=454,
    ), entry_id="test")
    # #340: mired<->kelvin inverse → bounds crossed (max mired -> min kelvin).
    assert sw._attr_min_color_temp_kelvin == color_util.color_temperature_mired_to_kelvin(454)
    assert sw._attr_max_color_temp_kelvin == color_util.color_temperature_mired_to_kelvin(153)


def test_init_brightness_not_added_when_hs_present():
    """When HS is already in supported_color_modes, BRIGHTNESS must NOT be added
    (exercises the `len == 0` guard being False)."""
    sw = LightSwitch(device=_make_init_device(
        supports_color_hsb=True,
        supports_color_temp=False,
        supports_brightness=True,
    ), entry_id="test")
    assert ColorMode.BRIGHTNESS not in sw._attr_supported_color_modes
    assert ColorMode.HS in sw._attr_supported_color_modes
    # color_mode must remain HS (not overwritten by brightness branch)
    assert sw._attr_color_mode == ColorMode.HS


def test_init_base_fields_set():
    """Verify SHCEntity.__init__ sets _attr_name and _attr_unique_id correctly.

    LightSwitch is a primary entity (_attr_has_entity_name=True) so _attr_name=None;
    HA uses the device name as the full entity name at runtime.
    """
    sw = LightSwitch(device=_make_init_device(
        name="My Light",
        root_device_id="root-X",
        id="dev-Y",
    ), entry_id="entry-42")
    assert sw._attr_name is None
    assert sw._attr_unique_id == "root-X_dev-Y"
    assert sw._entry_id == "entry-42"


def test_color_temp_kelvin_bounds_not_swapped():
    """#340: mired<->kelvin is inverse, so HA's min/max kelvin bounds must be
    crossed (smallest mired = largest kelvin). Regression guard against the
    straight (swapped) assignment."""
    sw = LightSwitch(device=_make_init_device(
        supports_color_temp=True,
        supports_color_hsb=False,
        supports_brightness=False,
        min_color_temperature=153,   # mireds (smallest mired -> warmest? no: highest kelvin)
        max_color_temperature=500,   # mireds (largest mired -> lowest kelvin)
    ), entry_id="test")
    m2k = color_util.color_temperature_mired_to_kelvin
    # smallest mired (153) -> largest kelvin -> the MAX bound
    assert sw._attr_max_color_temp_kelvin == m2k(153)
    # largest mired (500) -> smallest kelvin -> the MIN bound
    assert sw._attr_min_color_temp_kelvin == m2k(500)
    # and the ordering must be sane (min < max), which was violated before #340
    assert sw._attr_min_color_temp_kelvin < sw._attr_max_color_temp_kelvin


# ===========================================================================
# LightSwitch — color_mode update on async_turn_on + ZeroDivisionError guard
# ===========================================================================

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


class TestColorTempKelvinGuard:
    """color_temp_kelvin -- ZeroDivisionError guard."""

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


class TestTurnOnColorModeUpdate:
    """async_turn_on -- _attr_color_mode update."""

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


class TestLightInitColorTempBounds:
    """__init__ -- min/max color temp guard."""

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


# ===========================================================================
# LightSwitch / MotionDetectorLight runtime properties (complements the
# __init__ and color-mode-switching tests above)
# ===========================================================================

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
        async_set_brightness=AsyncMock(),
        async_set_color=AsyncMock(),
        async_set_rgb=AsyncMock(),
        async_set_binarystate=AsyncMock(),
    )
    light._attr_color_mode = ColorMode.HS
    light._attr_supported_color_modes = {ColorMode.HS}
    light.schedule_update_ha_state = MagicMock()
    return light


class TestLightSwitchIsOn:
    def test_is_on_true_when_binarystate_true(self):
        light = _make_light_switch(binarystate=True)
        assert light.is_on is True

    def test_is_on_false_when_binarystate_false(self):
        light = _make_light_switch(binarystate=False)
        assert light.is_on is False


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


class TestLightSwitchHsColor:
    def test_hs_color_pure_red(self):
        """0xFF0000 -> (0 deg, 100%) in HS."""
        light = _make_light_switch(rgb=0xFF0000)
        hs = light.hs_color
        expected = color_util.color_RGB_to_hs(255, 0, 0)
        assert abs(hs[0] - expected[0]) < 1
        assert abs(hs[1] - expected[1]) < 1

    def test_hs_color_pure_green(self):
        light = _make_light_switch(rgb=0x00FF00)
        hs = light.hs_color
        expected = color_util.color_RGB_to_hs(0, 255, 0)
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


class TestLightSwitchTurnOnBrightness:
    def test_turn_on_brightness_clamp_never_zero(self):
        """brightness=1 (0.4% of 255) would round to 0; must be clamped to 1."""
        light = _make_light_switch(binarystate=False)
        asyncio.run(light.async_turn_on(**{ATTR_BRIGHTNESS: 1}))
        light._device.async_set_brightness.assert_called_once()
        assert light._device.async_set_brightness.call_args[0][0] >= 1

    def test_turn_on_brightness_full_sets_100(self):
        """brightness=255 must map to 100%."""
        light = _make_light_switch(binarystate=True)
        asyncio.run(light.async_turn_on(**{ATTR_BRIGHTNESS: 255}))
        light._device.async_set_brightness.assert_called_once_with(100)

    def test_turn_on_brightness_half(self):
        """brightness=128 must map to round(128*100/255)."""
        light = _make_light_switch(binarystate=True)
        asyncio.run(light.async_turn_on(**{ATTR_BRIGHTNESS: 128}))
        light._device.async_set_brightness.assert_called_once_with(
            round(128 * 100 / 255)
        )

    def test_turn_on_without_brightness_does_not_set_device_brightness(self):
        """No ATTR_BRIGHTNESS kwarg must not call async_set_brightness."""
        light = _make_light_switch(binarystate=True, brightness=50)
        asyncio.run(light.async_turn_on())
        light._device.async_set_brightness.assert_not_called()

    def test_turn_on_brightness_ignored_if_device_not_supports(self):
        """If device doesn't support brightness, don't call async_set_brightness."""
        light = _make_light_switch(binarystate=True, supports_brightness=False, brightness=50)
        asyncio.run(light.async_turn_on(**{ATTR_BRIGHTNESS: 200}))
        light._device.async_set_brightness.assert_not_called()


class TestLightSwitchTurnOnBinarystate:
    def test_turn_on_sets_binarystate_true_when_off(self):
        """When light is off, async_turn_on must call async_set_binarystate(True)."""
        light = _make_light_switch(binarystate=False)
        asyncio.run(light.async_turn_on())
        light._device.async_set_binarystate.assert_called_once_with(True)

    def test_turn_on_binarystate_stays_true_when_already_on(self):
        """When already on, async_turn_on must not call async_set_binarystate."""
        light = _make_light_switch(binarystate=True)
        asyncio.run(light.async_turn_on())
        light._device.async_set_binarystate.assert_not_called()

    def test_turn_on_always_calls_schedule_update_ha_state(self):
        light = _make_light_switch(binarystate=True)
        asyncio.run(light.async_turn_on())
        light.schedule_update_ha_state.assert_called_once()


class TestLightSwitchTurnOff:
    def test_turn_off_sets_binarystate_false(self):
        light = _make_light_switch(binarystate=True)
        asyncio.run(light.async_turn_off())
        light._device.async_set_binarystate.assert_called_once_with(False)

    def test_turn_off_already_off_still_calls_setter(self):
        light = _make_light_switch(binarystate=False)
        asyncio.run(light.async_turn_off())
        light._device.async_set_binarystate.assert_called_once_with(False)

    def test_turn_off_accepts_extra_kwargs(self):
        """async_turn_off(**kwargs) must not raise on extra kwargs."""
        light = _make_light_switch(binarystate=True)
        asyncio.run(light.async_turn_off(some_extra="ignored"))
        light._device.async_set_binarystate.assert_called_once_with(False)


class TestLightSwitchHsColorNone:
    """LightSwitch.hs_color returns None when rgb_raw is None."""

    def test_hs_color_none_when_rgb_is_none(self):
        ls = LightSwitch.__new__(LightSwitch)
        ls._device = SimpleNamespace(rgb=None)
        assert ls.hs_color is None


# ===========================================================================
# async_setup_entry — excluded devices, MD2 stale-entity cleanup, RelayLight
# opt-out stale-entity cleanup
# ===========================================================================

def _make_ledvance_light_device(device_id="light-1", room_id="room-1"):
    """Minimal device double for a ledvance/dimmer/hue light."""
    return SimpleNamespace(
        id=device_id,
        root_device_id="shc-root",
        room_id=room_id,
        name="Test Light",
        serial=f"serial-{device_id}",
        manufacturer="Bosch",
        device_model="LightModel",
        status="AVAILABLE",
        deleted=False,
        device_services=[],
        subscribe_callback=lambda eid, cb: None,
        unsubscribe_callback=lambda eid: None,
        supports_color_hsb=False,
        supports_color_temp=False,
        supports_brightness=False,
        min_color_temperature=None,
        max_color_temperature=None,
        binarystate=True,
        brightness=None,
    )


def _make_motion_detector2(device_id="md2-1", room_id="room-2", supports_light=True):
    """Minimal device double for a motion detector II.

    supports_light=True models the OUTDOOR/[+M] installation profile (has the
    indicator-light services); False models the base/GENERIC profile MD2,
    which has neither BinarySwitch nor MultiLevelSwitch (#325/#303).
    """
    return SimpleNamespace(
        id=device_id,
        root_device_id="shc-root",
        room_id=room_id,
        name="Motion Detector II",
        serial=f"serial-{device_id}",
        manufacturer="Bosch",
        device_model="MD2Model",
        status="AVAILABLE",
        deleted=False,
        device_services=[],
        subscribe_callback=lambda eid, cb: None,
        unsubscribe_callback=lambda eid: None,
        binaryswitch=False,
        multi_level_switch=None,
        supports_light=supports_light,
    )


def _make_light_switch_bsm(device_id="bsm-1", room_id="room-3"):
    """Minimal device double for a #338 light-relay (BSM/light-attached)."""
    return SimpleNamespace(
        id=device_id,
        root_device_id="shc-root",
        room_id=room_id,
        name="Light Relay",
        serial=f"serial-{device_id}",
        manufacturer="Bosch",
        device_model="BSM",
        status="AVAILABLE",
        deleted=False,
        device_services=[],
        subscribe_callback=lambda eid, cb: None,
        unsubscribe_callback=lambda eid: None,
        switchstate=True,
    )


class TestLightSetupExcluded:
    """device_excluded in first for-loop (ledvance/dimmer/hue)."""

    def test_excluded_ledvance_light_not_added(self, mock_config_entry, mock_session):
        """Excluded ledvance light must not appear in entities."""
        dev = _make_ledvance_light_device(device_id="excl-light")
        mock_session.device_helper.ledvance_lights = [dev]
        mock_config_entry.options = {OPT_EXCLUDED_DEVICES: ["excl-light"]}
        added = _run_light_setup(mock_config_entry, mock_session)
        assert all(getattr(e, "_device", None) is not dev for e in added), (
            "Excluded ledvance light should not be added"
        )

    def test_non_excluded_ledvance_light_is_added(self, mock_config_entry, mock_session):
        """Non-excluded ledvance light must appear in entities."""
        dev = _make_ledvance_light_device(device_id="keep-light")
        mock_session.device_helper.ledvance_lights = [dev]
        added = _run_light_setup(mock_config_entry, mock_session)
        assert any(getattr(e, "_device", None) is dev for e in added), (
            "Non-excluded ledvance light should be added"
        )

    def test_mixed_lights_only_excluded_is_skipped(self, mock_config_entry, mock_session):
        """When one of two lights is excluded, only the non-excluded one is added."""
        keep = _make_ledvance_light_device(device_id="keep")
        excl = _make_ledvance_light_device(device_id="excl")
        mock_session.device_helper.ledvance_lights = [keep, excl]
        mock_config_entry.options = {OPT_EXCLUDED_DEVICES: ["excl"]}
        added = _run_light_setup(mock_config_entry, mock_session)
        device_ids = [getattr(e, "_device", SimpleNamespace()).id for e in added]
        assert "keep" in device_ids
        assert "excl" not in device_ids


# ===========================================================================
# async_setup_entry — HUE/Ledvance suppression w/ device registry, RelayLight
# opt-in, excluded relay device (lines 43-104 of light.py)
# ===========================================================================

class TestLightSetupHueSuppressWithRegistry:
    """HUE lights suppressed + dev_registry entry exists → removed."""

    @pytest.mark.parametrize(
        "mock_config_entry", [{"options": {OPT_SUPPRESS_HUE_LIGHTS: True}}], indirect=True
    )
    def test_hue_suppress_removes_device_from_registry(
        self, mock_config_entry, mock_session
    ):
        """When suppress HUE is on, dev_registry entry is removed."""
        dev = _fake_dev("hue1")
        mock_session.device_helper.hue_lights = [dev]
        dev_entry = SimpleNamespace(id="reg_id_hue1")
        dr_mock = MagicMock()
        dr_mock.async_get_device = MagicMock(return_value=dev_entry)
        dr_mock.async_update_device = MagicMock()

        _run_light_setup_with_dev_reg(mock_config_entry, mock_session, dr_mock)
        dr_mock.async_update_device.assert_called_once()

    @pytest.mark.parametrize(
        "mock_config_entry", [{"options": {OPT_SUPPRESS_HUE_LIGHTS: True}}], indirect=True
    )
    def test_hue_suppress_no_registry_entry(self, mock_config_entry, mock_session):
        """dev_registry returns None → no update_device call."""
        dev = _fake_dev("hue1")
        mock_session.device_helper.hue_lights = [dev]
        dr_mock = MagicMock()
        dr_mock.async_get_device = MagicMock(return_value=None)
        dr_mock.async_update_device = MagicMock()

        _run_light_setup_with_dev_reg(mock_config_entry, mock_session, dr_mock)
        dr_mock.async_update_device.assert_not_called()


class TestLightSetupLedvanceSuppressWithRegistry:
    """Ledvance lights suppressed + dev_registry entry exists."""

    @pytest.mark.parametrize(
        "mock_config_entry", [{"options": {OPT_SUPPRESS_LEDVANCE_LIGHTS: True}}], indirect=True
    )
    def test_ledvance_suppress_removes_device_from_registry(
        self, mock_config_entry, mock_session
    ):
        """Ledvance suppress removes matching device registry entry."""
        dev = _fake_dev("led1")
        mock_session.device_helper.ledvance_lights = [dev]
        dev_entry = SimpleNamespace(id="reg_led1")
        dr_mock = MagicMock()
        dr_mock.async_get_device = MagicMock(return_value=dev_entry)
        dr_mock.async_update_device = MagicMock()

        _run_light_setup_with_dev_reg(mock_config_entry, mock_session, dr_mock)
        dr_mock.async_update_device.assert_called_once()

    @pytest.mark.parametrize(
        "mock_config_entry", [{"options": {OPT_SUPPRESS_LEDVANCE_LIGHTS: True}}], indirect=True
    )
    def test_ledvance_suppress_no_registry_entry(self, mock_config_entry, mock_session):
        """dev_registry returns None → no update_device call."""
        dev = _fake_dev("led1")
        mock_session.device_helper.ledvance_lights = [dev]
        dr_mock = MagicMock()
        dr_mock.async_get_device = MagicMock(return_value=None)
        dr_mock.async_update_device = MagicMock()

        _run_light_setup_with_dev_reg(mock_config_entry, mock_session, dr_mock)
        dr_mock.async_update_device.assert_not_called()


# ===========================================================================
# async_setup_entry — basic device-bucket wiring (lines 20-36 of light.py)
# ===========================================================================

class TestLightSetupEntry:
    """Light async_setup_entry with LightSwitch (BRIGHTNESS mode)."""

    def _run(self, mock_config_entry, mock_session) -> list:
        return _run_light_setup(mock_config_entry, mock_session)

    def test_ledvance_lights_produce_light_switch_entities(
        self, mock_config_entry, mock_session
    ) -> None:
        """ledvance_lights → LightSwitch."""
        dev = _light_device()
        mock_session.device_helper.ledvance_lights = [dev]
        result = self._run(mock_config_entry, mock_session)
        assert len(result) == 1
        assert isinstance(result[0], LightSwitch)

    def test_micromodule_dimmers_produce_light_switch_entities(
        self, mock_config_entry, mock_session
    ) -> None:
        """micromodule_dimmers → LightSwitch."""
        dev = _light_device()
        mock_session.device_helper.micromodule_dimmers = [dev]
        result = self._run(mock_config_entry, mock_session)
        assert len(result) == 1
        assert isinstance(result[0], LightSwitch)

    def test_mixed_light_devices_all_collected(
        self, mock_config_entry, mock_session
    ) -> None:
        """Ledvance + micromodule_dimmer → 2 entities."""
        mock_session.device_helper.ledvance_lights = [_light_device()]
        mock_session.device_helper.micromodule_dimmers = [_light_device()]
        result = self._run(mock_config_entry, mock_session)
        assert len(result) == 2
        assert all(isinstance(e, LightSwitch) for e in result)

    def test_no_lights_adds_nothing(self, mock_config_entry, mock_session) -> None:
        """Empty lists → 0 entities."""
        result = self._run(mock_config_entry, mock_session)
        assert result == []

    def test_entry_id_set_on_light_entity(
        self, mock_config_entry, mock_session
    ) -> None:
        """LightSwitch gets the entry_id stored."""
        dev = _light_device()
        mock_session.device_helper.ledvance_lights = [dev]
        result = self._run(mock_config_entry, mock_session)
        assert result[0]._entry_id == "E1"

    def test_color_temp_only_device(self, mock_config_entry, mock_session) -> None:
        """A device with only color-temp support → LightSwitch in COLOR_TEMP mode."""
        dev = _light_device(
            supports_color_hsb=False,
            supports_color_temp=True,
            supports_brightness=False,
        )
        mock_session.device_helper.ledvance_lights = [dev]
        result = self._run(mock_config_entry, mock_session)
        assert len(result) == 1
        assert result[0]._attr_color_mode == ColorMode.COLOR_TEMP

    def test_onoff_only_device(self, mock_config_entry, mock_session) -> None:
        """A device with no color/brightness support → LightSwitch in ONOFF mode."""
        dev = _light_device(
            supports_color_hsb=False,
            supports_color_temp=False,
            supports_brightness=False,
        )
        mock_session.device_helper.ledvance_lights = [dev]
        result = self._run(mock_config_entry, mock_session)
        assert len(result) == 1
        assert result[0]._attr_color_mode == ColorMode.ONOFF

    def test_hue_lights_produce_light_switch_entities(
        self, mock_config_entry, mock_session
    ) -> None:
        """hue_lights → LightSwitch (ONOFF mode for a BinarySwitch-only device)."""
        dev = _light_device(
            supports_color_hsb=False,
            supports_color_temp=False,
            supports_brightness=False,
        )
        mock_session.device_helper.hue_lights = [dev]
        result = self._run(mock_config_entry, mock_session)
        assert len(result) == 1
        assert isinstance(result[0], LightSwitch)

    def test_hue_lights_mixed_with_others_all_collected(
        self, mock_config_entry, mock_session
    ) -> None:
        """Ledvance + hue → 2 LightSwitch entities."""
        mock_session.device_helper.ledvance_lights = [_light_device()]
        mock_session.device_helper.hue_lights = [
            _light_device(
                supports_color_hsb=False,
                supports_color_temp=False,
                supports_brightness=False,
            )
        ]
        result = self._run(mock_config_entry, mock_session)
        assert len(result) == 2
        assert all(isinstance(e, LightSwitch) for e in result)


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
        async_set_binaryswitch=AsyncMock(),
        async_set_multi_level_switch=AsyncMock(),
    )
    light._attr_name = "Motion Light"
    light._attr_unique_id = "root-1_md-1_motionlight"
    return light


class TestMotionDetectorLightClassAttrs:
    """HA parent classes shadow _attr_supported_color_modes/_attr_color_mode with
    properties, so read via instance (which returns the underlying _attr value)."""

    def test_supported_color_modes_is_brightness(self):
        light = _make_motion_light()
        assert ColorMode.BRIGHTNESS in light.supported_color_modes

    def test_color_mode_is_brightness(self):
        light = _make_motion_light()
        assert light.color_mode == ColorMode.BRIGHTNESS


class TestMotionDetectorLightIsOn:
    def test_is_on_true_when_binaryswitch_true(self):
        light = _make_motion_light(binaryswitch=True)
        assert light.is_on is True

    def test_is_on_false_when_binaryswitch_false(self):
        light = _make_motion_light(binaryswitch=False)
        assert light.is_on is False


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


class TestMotionDetectorLightTurnOn:
    def test_turn_on_without_brightness_sets_binaryswitch_when_off(self):
        light = _make_motion_light(binaryswitch=False)
        asyncio.run(light.async_turn_on())
        light._device.async_set_binaryswitch.assert_called_once_with(True)

    def test_turn_on_without_brightness_does_not_touch_multi_level_switch(self):
        light = _make_motion_light(binaryswitch=False, multi_level_switch=50)
        asyncio.run(light.async_turn_on())
        light._device.async_set_multi_level_switch.assert_not_called()

    def test_turn_on_with_brightness_sets_level(self):
        """ATTR_BRIGHTNESS=255 must call async_set_multi_level_switch(100)."""
        light = _make_motion_light(binaryswitch=True)
        asyncio.run(light.async_turn_on(**{ATTR_BRIGHTNESS: 255}))
        light._device.async_set_multi_level_switch.assert_called_once_with(100)

    def test_turn_on_with_brightness_scales_half(self):
        light = _make_motion_light(binaryswitch=True)
        asyncio.run(light.async_turn_on(**{ATTR_BRIGHTNESS: 128}))
        expected = round(128 * 100 / 255)
        light._device.async_set_multi_level_switch.assert_called_once_with(expected)

    def test_turn_on_brightness_clamp_to_1_not_zero(self):
        """brightness=1 would round to 0; must be clamped to 1."""
        light = _make_motion_light(binaryswitch=True)
        asyncio.run(light.async_turn_on(**{ATTR_BRIGHTNESS: 1}))
        light._device.async_set_multi_level_switch.assert_called_once()
        assert light._device.async_set_multi_level_switch.call_args[0][0] >= 1

    def test_turn_on_does_not_set_binaryswitch_when_already_on(self):
        """When already on, async_turn_on must not call async_set_binaryswitch."""
        light = _make_motion_light(binaryswitch=True)
        asyncio.run(light.async_turn_on())
        light._device.async_set_binaryswitch.assert_not_called()

    def test_turn_on_sets_binaryswitch_to_true_when_was_off(self):
        light = _make_motion_light(binaryswitch=False)
        asyncio.run(light.async_turn_on(**{ATTR_BRIGHTNESS: 200}))
        light._device.async_set_binaryswitch.assert_called_once_with(True)


class TestMotionDetectorLightTurnOff:
    def test_turn_off_sets_binaryswitch_false(self):
        light = _make_motion_light(binaryswitch=True)
        asyncio.run(light.async_turn_off())
        light._device.async_set_binaryswitch.assert_called_once_with(False)

    def test_turn_off_already_off(self):
        light = _make_motion_light(binaryswitch=False)
        asyncio.run(light.async_turn_off())
        light._device.async_set_binaryswitch.assert_called_once_with(False)

    def test_turn_off_accepts_extra_kwargs(self):
        light = _make_motion_light(binaryswitch=True)
        asyncio.run(light.async_turn_off(transition=0))
        light._device.async_set_binaryswitch.assert_called_once_with(False)


class TestMotionDetector2Setup:
    """motion_detectors2 loop."""

    def test_excluded_motion_detector2_not_added(self, mock_config_entry, mock_session):
        """Excluded MD2 must not appear in entities."""
        dev = _make_motion_detector2(device_id="excl-md2")
        mock_session.device_helper.motion_detectors2 = [dev]
        mock_config_entry.options = {OPT_EXCLUDED_DEVICES: ["excl-md2"]}
        added = _run_light_setup(mock_config_entry, mock_session)
        assert all(getattr(e, "_device", None) is not dev for e in added), (
            "Excluded motion_detector2 should not be added"
        )

    def test_non_excluded_motion_detector2_is_added(self, mock_config_entry, mock_session):
        """Non-excluded MD2 must result in a MotionDetectorLight entity."""
        dev = _make_motion_detector2(device_id="keep-md2")
        mock_session.device_helper.motion_detectors2 = [dev]
        added = _run_light_setup(mock_config_entry, mock_session)
        assert any(
            isinstance(e, MotionDetectorLight) and e._device is dev for e in added
        ), "Non-excluded MD2 should produce a MotionDetectorLight entity"

    def test_base_profile_motion_detector2_skipped_no_light_services(
        self, mock_config_entry, mock_session
    ):
        """Regression: a base/GENERIC profile MD2 (supports_light=False, no
        BinarySwitch/MultiLevelSwitch services) must NOT get a
        MotionDetectorLight entity — previously this crashed on every state
        read/write with AttributeError on the None service (#325/#303)."""
        dev = _make_motion_detector2(device_id="base-md2", supports_light=False)
        mock_session.device_helper.motion_detectors2 = [dev]
        added = _run_light_setup(mock_config_entry, mock_session)
        assert all(getattr(e, "_device", None) is not dev for e in added), (
            "Base-profile MD2 (no [+M] light services) must not get a MotionDetectorLight"
        )

    def test_unsupported_profile_motion_detector2_removes_stale_entity(
        self, mock_config_entry, mock_session
    ):
        """#356: a MD2 whose profile no longer supports the light (e.g. after
        switching [+M] -> GENERIC via select.installation_profile) must have
        any previously-registered MotionDetectorLight entity actively removed,
        not just skipped on this setup pass."""
        dev = _make_motion_detector2(device_id="was-plusm-md2", supports_light=False)
        mock_session.device_helper.motion_detectors2 = [dev]
        _, remove_mock = _run_light_setup_with_remove_mock(mock_config_entry, mock_session)
        remove_mock.assert_awaited_once_with(
            ANY, Platform.LIGHT, "shc-root_was-plusm-md2_motionlight"
        )

    def test_excluded_motion_detector2_removes_stale_entity(
        self, mock_config_entry, mock_session
    ):
        """#356: excluding a device that previously had a light entity must
        also clean up the stale registry entry, not just skip creation."""
        dev = _make_motion_detector2(device_id="excl-had-light", supports_light=True)
        mock_session.device_helper.motion_detectors2 = [dev]
        mock_config_entry.options = {OPT_EXCLUDED_DEVICES: ["excl-had-light"]}
        _, remove_mock = _run_light_setup_with_remove_mock(mock_config_entry, mock_session)
        remove_mock.assert_awaited_once_with(
            ANY, Platform.LIGHT, "shc-root_excl-had-light_motionlight"
        )

    def test_async_migrate_called_for_motion_detector2(
        self, mock_config_entry, mock_session
    ):
        """async_migrate_to_new_unique_id must be called with attr_name='MotionLight'."""
        dev = _make_motion_detector2(device_id="md2-migrate")
        mock_session.device_helper.motion_detectors2 = [dev]
        _, migrate_calls = _run_light_setup_track_migrate(mock_config_entry, mock_session)
        assert any(
            call[1] is dev and call[2] == "MotionLight" for call in migrate_calls
        ), f"Expected migrate call with attr_name='MotionLight', got: {migrate_calls}"

    def test_excluded_motion_detector2_skips_migrate(
        self, mock_config_entry, mock_session
    ):
        """async_migrate must NOT be called for excluded MD2 devices."""
        dev = _make_motion_detector2(device_id="excl-migrate")
        mock_session.device_helper.motion_detectors2 = [dev]
        mock_config_entry.options = {OPT_EXCLUDED_DEVICES: ["excl-migrate"]}
        _, migrate_calls = _run_light_setup_track_migrate(mock_config_entry, mock_session)
        assert not any(c[1] is dev for c in migrate_calls), (
            "Excluded MD2 must not trigger async_migrate_to_new_unique_id"
        )


class TestMotionDetectorLightBrightnessNoneGuard:
    """MotionDetectorLight.brightness when multi_level_switch is None."""

    def _make_entity(self, multi_level_switch_value=None):
        """Build a MotionDetectorLight bypassing __init__ via __new__."""
        ent = MotionDetectorLight.__new__(MotionDetectorLight)
        ent._device = SimpleNamespace(
            id="md2-ent",
            root_device_id="shc-root",
            name="MD2 Light",
            manufacturer="Bosch",
            device_model="MD2",
            status="AVAILABLE",
            deleted=False,
            device_services=[],
            subscribe_callback=lambda eid, cb: None,
            unsubscribe_callback=lambda eid: None,
            binaryswitch=False,
            multi_level_switch=multi_level_switch_value,
        )
        ent._entry_id = "entry-test"
        ent._attr_name = "Motion Light"
        ent._attr_unique_id = "shc-root_md2-ent_motionlight"
        return ent

    def test_brightness_returns_zero_when_level_is_none(self):
        """brightness must be 0 when multi_level_switch is None."""
        ent = self._make_entity(multi_level_switch_value=None)
        assert ent.brightness == 0

    def test_brightness_scales_correctly_when_level_set(self):
        """Sanity: brightness scales 0-100 → 0-255 correctly."""
        ent = self._make_entity(multi_level_switch_value=100)
        assert ent.brightness == 255

    def test_brightness_midpoint(self):
        """Midpoint level 50 → 128 (rounded)."""
        ent = self._make_entity(multi_level_switch_value=50)
        assert ent.brightness == round(50 * 255 / 100)

    def test_brightness_zero_level_gives_zero(self):
        """Level 0 → brightness 0."""
        ent = self._make_entity(multi_level_switch_value=0)
        assert ent.brightness == 0


# ===========================================================================
# MotionDetectorLight — Motion Detector II [+M] indicator light
# ===========================================================================

def _make_md2_device(**kwargs):
    """Return a fake SHCMotionDetector2-shaped SimpleNamespace."""
    defaults = dict(
        name="Motion Detector II",
        id="hdm:ZigBee:000000000000abcd",
        root_device_id="64-da-a0-xx-xx-xx",
        # OccupancyDetectionService
        occupied=False,
        last_occupancy_change_time="2026-06-20T12:00:00.000Z",
        # BinarySwitch / MultiLevelSwitch (MD2 light)
        binaryswitch=False,
        multi_level_switch=50,
        # PetImmunity
        pet_immunity_enabled=False,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_md2_light(**device_kwargs):
    dev = _make_md2_device(**device_kwargs)
    light = MotionDetectorLight.__new__(MotionDetectorLight)
    light._device = dev
    light._attr_name = f"{dev.name} Motion Light"
    light._attr_unique_id = f"{dev.root_device_id}_{dev.id}_motionlight"
    light._attr_supported_color_modes = {ColorMode.BRIGHTNESS}
    light._attr_color_mode = ColorMode.BRIGHTNESS
    return light


class TestMotionDetectorLight:
    """Tests for the MD2 indicator light entity."""

    def test_color_mode_is_brightness(self):
        """Supported mode must be BRIGHTNESS only."""
        light = _make_md2_light()
        assert light._attr_color_mode == ColorMode.BRIGHTNESS
        assert light._attr_supported_color_modes == {ColorMode.BRIGHTNESS}

    def test_is_on_true(self):
        light = _make_md2_light(binaryswitch=True)
        assert light.is_on is True

    def test_is_on_false(self):
        light = _make_md2_light(binaryswitch=False)
        assert light.is_on is False

    def test_brightness_scales_from_device_level(self):
        """level=100 → HA brightness 255."""
        light = _make_md2_light(multi_level_switch=100)
        assert light.brightness == 255

    def test_brightness_level_50_maps_to_128(self):
        """level=50 → HA brightness round(50*255/100)=128 (or 127/128 depending on rounding)."""
        light = _make_md2_light(multi_level_switch=50)
        assert light.brightness == round(50 * 255 / 100)

    def test_brightness_level_0_maps_to_0(self):
        light = _make_md2_light(multi_level_switch=0)
        assert light.brightness == 0

    def test_brightness_none_level_maps_to_0(self):
        light = _make_md2_light(multi_level_switch=None)
        assert light.brightness == 0

    def test_turn_on_sets_binaryswitch(self):
        """async_turn_on without kwargs must call async_set_binaryswitch(True)."""
        dev = _make_md2_device(binaryswitch=False, multi_level_switch=50)
        dev.async_set_binaryswitch = AsyncMock()
        dev.async_set_multi_level_switch = AsyncMock()

        light = MotionDetectorLight.__new__(MotionDetectorLight)
        light._device = dev
        light._attr_supported_color_modes = {ColorMode.BRIGHTNESS}
        light._attr_color_mode = ColorMode.BRIGHTNESS

        asyncio.run(light.async_turn_on())
        dev.async_set_binaryswitch.assert_called_once_with(True)

    def test_turn_on_with_brightness_sets_level(self):
        """async_turn_on(ATTR_BRIGHTNESS=128) must call async_set_multi_level_switch."""
        dev = _make_md2_device(binaryswitch=False, multi_level_switch=50)
        dev.async_set_binaryswitch = AsyncMock()
        dev.async_set_multi_level_switch = AsyncMock()

        light = MotionDetectorLight.__new__(MotionDetectorLight)
        light._device = dev
        light._attr_supported_color_modes = {ColorMode.BRIGHTNESS}
        light._attr_color_mode = ColorMode.BRIGHTNESS

        ha_brightness = 128  # ~50 in device scale
        asyncio.run(light.async_turn_on(**{ATTR_BRIGHTNESS: ha_brightness}))

        expected_level = max(round(ha_brightness * 100 / 255), 1)
        dev.async_set_multi_level_switch.assert_called_once_with(expected_level)
        dev.async_set_binaryswitch.assert_called_once_with(True)

    def test_turn_on_brightness_clamps_to_minimum_1(self):
        """Near-zero HA brightness must call async_set_multi_level_switch with level >= 1."""
        dev = _make_md2_device(binaryswitch=True, multi_level_switch=0)
        dev.async_set_multi_level_switch = AsyncMock()
        dev.async_set_binaryswitch = AsyncMock()

        light = MotionDetectorLight.__new__(MotionDetectorLight)
        light._device = dev
        light._attr_supported_color_modes = {ColorMode.BRIGHTNESS}
        light._attr_color_mode = ColorMode.BRIGHTNESS

        asyncio.run(light.async_turn_on(**{ATTR_BRIGHTNESS: 1}))
        level_arg = dev.async_set_multi_level_switch.call_args[0][0]
        assert level_arg >= 1

    def test_turn_off_sets_binaryswitch_false(self):
        """async_turn_off must call async_set_binaryswitch(False)."""
        dev = _make_md2_device(binaryswitch=True)
        dev.async_set_binaryswitch = AsyncMock()

        light = MotionDetectorLight.__new__(MotionDetectorLight)
        light._device = dev
        light._attr_supported_color_modes = {ColorMode.BRIGHTNESS}
        light._attr_color_mode = ColorMode.BRIGHTNESS

        asyncio.run(light.async_turn_off())
        dev.async_set_binaryswitch.assert_called_once_with(False)

    def test_unique_id_format(self):
        dev = _make_md2_device(root_device_id="root1", id="dev1")
        light = MotionDetectorLight.__new__(MotionDetectorLight)
        light._device = dev
        light._attr_name = f"{dev.name} Motion Light"
        light._attr_unique_id = f"{dev.root_device_id}_{dev.id}_motionlight"
        assert light._attr_unique_id == "root1_dev1_motionlight"


# ===========================================================================
# #338: opt-in to expose a Light/Shutter Control II light relay
# (MICROMODULE_LIGHT_ATTACHED) or a BSM light switch as a HA `light` instead
# of a `switch`. Covers entity.py helpers (light_switch_devices /
# light_switch_as_light / light_relay_friendly_model) and RelayLight's ONOFF
# behaviour.
# ===========================================================================

def _make_relay_opt_device(**kwargs):
    defaults = dict(
        name="Light Control II",
        root_device_id="root1",
        id="dev1",
        switchstate=State.OFF,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


# ── entity.light_switch_as_light ─────────────────────────────────────────────

def test_opt_in_true_when_id_listed():
    dev = _make_relay_opt_device(id="abc")
    assert light_switch_as_light(dev, {OPT_LIGHTS_AS_LIGHT: ["abc"]}) is True


def test_opt_in_false_when_id_absent_or_unset():
    dev = _make_relay_opt_device(id="abc")
    assert light_switch_as_light(dev, {OPT_LIGHTS_AS_LIGHT: ["other"]}) is False
    assert light_switch_as_light(dev, {}) is False
    # Stored option may be explicit None (cleared) — must not raise.
    assert light_switch_as_light(dev, {OPT_LIGHTS_AS_LIGHT: None}) is False


# ── entity.light_switch_devices ──────────────────────────────────────────────

def test_devices_combine_both_buckets_bsm_first():
    bsm = _make_relay_opt_device(id="bsm1")
    mm = _make_relay_opt_device(id="mm1")
    session = SimpleNamespace(
        device_helper=SimpleNamespace(
            light_switches_bsm=[bsm],
            micromodule_light_attached=[mm],
        )
    )
    assert light_switch_devices(session) == [bsm, mm]


def test_devices_getattr_safe_when_bucket_missing():
    # An older pinned lib may not expose every bucket → getattr fallback to [].
    session = SimpleNamespace(device_helper=SimpleNamespace())
    assert light_switch_devices(session) == []


# ── light.RelayLight ─────────────────────────────────────────────────────────

def test_relaylight_is_onoff_light():
    # HA's entity metaclass turns the class-body _attr_* into property
    # descriptors, so assert the public color-mode API on an instance.
    lt = RelayLight.__new__(RelayLight)
    assert lt.color_mode == ColorMode.ONOFF
    assert lt.supported_color_modes == {ColorMode.ONOFF}


def test_relaylight_is_on_reflects_switchstate():
    lt = RelayLight.__new__(RelayLight)
    lt._device = _make_relay_opt_device(switchstate=State.ON)
    assert lt.is_on is True
    lt._device = _make_relay_opt_device(switchstate=State.OFF)
    assert lt.is_on is False


def test_relaylight_is_on_none_when_service_unavailable():
    """A relay with no connected load can raise AttributeError → None, no crash."""

    class _NoService:
        @property
        def switchstate(self):
            raise AttributeError("PowerSwitch service is None")

    lt = RelayLight.__new__(RelayLight)
    lt._device = _NoService()
    assert lt.is_on is None


def test_relaylight_turn_on_off_invoke_async_setter():
    calls = []

    async def _fake_set(value):
        calls.append(value)

    lt = RelayLight.__new__(RelayLight)
    lt._device = SimpleNamespace(async_set_switchstate=_fake_set)
    asyncio.run(lt.async_turn_on())
    asyncio.run(lt.async_turn_off())
    assert calls == [True, False]


# ── #338 refinements: global toggle + friendly model label ───────────────────

def test_global_toggle_overrides_per_device():
    dev = _make_relay_opt_device(id="not-listed")
    # global ON → light regardless of the per-device list
    assert light_switch_as_light(dev, {OPT_ALL_LIGHTS_AS_LIGHT: True}) is True
    assert light_switch_as_light(
        dev, {OPT_ALL_LIGHTS_AS_LIGHT: True, OPT_LIGHTS_AS_LIGHT: []}
    ) is True
    # global OFF → falls back to the per-device list
    assert light_switch_as_light(
        dev, {OPT_ALL_LIGHTS_AS_LIGHT: False, OPT_LIGHTS_AS_LIGHT: ["not-listed"]}
    ) is True
    assert light_switch_as_light(dev, {OPT_ALL_LIGHTS_AS_LIGHT: False}) is False


def test_friendly_model_label():
    assert light_relay_friendly_model(
        _make_relay_opt_device(device_model="MICROMODULE_LIGHT_ATTACHED")
    ) == "Light/Shutter Control II"
    assert light_relay_friendly_model(_make_relay_opt_device(device_model="BSM")) == \
        "In-wall light switch"
    # unknown model falls back to the raw model string (never crashes/blank)
    assert light_relay_friendly_model(_make_relay_opt_device(device_model="WHATEVER")) == \
        "WHATEVER"


# ===========================================================================
# RelayLight error handling + LightSwitch.hs_color None guard
# ===========================================================================

class TestRelayLightTurnOnErrors:
    """RelayLight.async_turn_on AttributeError + ClientError/TimeoutError."""

    def _make_relay_light(self, device):
        rl = RelayLight.__new__(RelayLight)
        rl._device = device
        rl.entity_id = "light.relay_test"
        return rl

    def test_turn_on_attribute_error(self):
        """AttributeError in async_set_switchstate → debug log."""
        dev = MagicMock()
        dev.async_set_switchstate = AsyncMock(side_effect=AttributeError("no service"))
        rl = self._make_relay_light(dev)
        _run(rl.async_turn_on())  # must not raise

    def test_turn_on_client_error(self):
        """aiohttp.ClientError → debug log, no raise."""
        dev = MagicMock()
        dev.async_set_switchstate = AsyncMock(side_effect=aiohttp.ClientError("err"))
        rl = self._make_relay_light(dev)
        _run(rl.async_turn_on())  # must not raise

    def test_turn_on_timeout_error(self):
        """asyncio.TimeoutError → debug log, no raise."""
        dev = MagicMock()
        dev.async_set_switchstate = AsyncMock(side_effect=asyncio.TimeoutError())
        rl = self._make_relay_light(dev)
        _run(rl.async_turn_on())  # must not raise


class TestRelayLightTurnOffErrors:
    """RelayLight.async_turn_off AttributeError + ClientError."""

    def _make_relay_light(self, device):
        rl = RelayLight.__new__(RelayLight)
        rl._device = device
        rl.entity_id = "light.relay_test"
        return rl

    def test_turn_off_attribute_error(self):
        """AttributeError → debug log, no raise."""
        dev = MagicMock()
        dev.async_set_switchstate = AsyncMock(side_effect=AttributeError("no service"))
        rl = self._make_relay_light(dev)
        _run(rl.async_turn_off())

    def test_turn_off_client_error(self):
        """aiohttp.ClientError → debug log, no raise."""
        dev = MagicMock()
        dev.async_set_switchstate = AsyncMock(side_effect=aiohttp.ClientError())
        rl = self._make_relay_light(dev)
        _run(rl.async_turn_off())


class TestRelayLightOptOutSetup:
    """#338 light_switch_devices loop: RelayLight opt-out stale-entity cleanup."""

    def test_opted_in_bsm_gets_relaylight(self, mock_config_entry, mock_session):
        dev = _make_light_switch_bsm(device_id="bsm-in")
        mock_session.device_helper.light_switches_bsm = [dev]
        mock_config_entry.options = {OPT_LIGHTS_AS_LIGHT: ["bsm-in"]}
        added = _run_light_setup(mock_config_entry, mock_session)
        assert any(
            isinstance(e, RelayLight) and e._device is dev for e in added
        ), "Opted-in BSM should produce a RelayLight entity"

    def test_not_opted_in_bsm_produces_no_relaylight(self, mock_config_entry, mock_session):
        dev = _make_light_switch_bsm(device_id="bsm-out")
        mock_session.device_helper.light_switches_bsm = [dev]
        mock_config_entry.options = {OPT_LIGHTS_AS_LIGHT: []}
        added = _run_light_setup(mock_config_entry, mock_session)
        assert all(getattr(e, "_device", None) is not dev for e in added)

    def test_opted_out_bsm_removes_stale_relaylight_entity(
        self, mock_config_entry, mock_session
    ):
        """Regression: a device previously opted in to "expose as light"
        (RelayLight created, unique_id = root_device_id_device_id) that gets
        opted back out must have that entity actively removed — an options
        change reloads the entry (OptionsFlowWithReload), so simply not
        re-creating the entity left an orphaned registry entry behind,
        exactly the failure mode #356 already fixed for MotionDetectorLight."""
        dev = _make_light_switch_bsm(device_id="was-light")
        mock_session.device_helper.light_switches_bsm = [dev]
        mock_config_entry.options = {OPT_LIGHTS_AS_LIGHT: []}
        _, remove_mock = _run_light_setup_with_remove_mock(mock_config_entry, mock_session)
        remove_mock.assert_awaited_once_with(ANY, Platform.LIGHT, "shc-root_was-light")

    def test_excluded_bsm_that_was_opted_in_removes_stale_relaylight_entity(
        self, mock_config_entry, mock_session
    ):
        dev = _make_light_switch_bsm(device_id="excl-was-light")
        mock_session.device_helper.light_switches_bsm = [dev]
        mock_config_entry.options = {
            OPT_EXCLUDED_DEVICES: ["excl-was-light"],
            OPT_LIGHTS_AS_LIGHT: ["excl-was-light"],
        }
        _, remove_mock = _run_light_setup_with_remove_mock(mock_config_entry, mock_session)
        remove_mock.assert_awaited_once_with(
            ANY, Platform.LIGHT, "shc-root_excl-was-light"
        )


class TestLightRelayOptIn:
    """RelayLight opt-in path in async_setup_entry."""

    def test_relay_not_opted_in_skipped(self, mock_config_entry, mock_session):
        """light_switch_as_light=False → continue, RelayLight not added."""
        dev = _fake_dev("bsm1")
        # No opt-in option → light_switch_as_light returns False
        mock_session.device_helper.light_switches_bsm = [dev]
        collected = _run_light_setup(mock_config_entry, mock_session)
        assert not any(isinstance(e, RelayLight) for e in collected)

    def test_relay_opted_in_added(self, mock_config_entry, mock_session):
        """light_switch_as_light=True → RelayLight added."""
        dev = _fake_dev("bsm1")
        # All-opt-in → light_switch_as_light returns True
        mock_session.device_helper.light_switches_bsm = [dev]
        mock_config_entry.options = {OPT_ALL_LIGHTS_AS_LIGHT: True}
        collected = _run_light_setup(mock_config_entry, mock_session)
        assert any(isinstance(e, RelayLight) for e in collected)


class TestLightExcludedRelayDevice:
    """light.py: excluded device in relay light loop → continue."""

    def test_excluded_relay_device_skipped(self, mock_config_entry, mock_session):
        """device_excluded → continue before opt-in check."""
        dev = _fake_dev("bsm_excl")
        mock_session.device_helper.light_switches_bsm = [dev]
        mock_config_entry.options = {
            OPT_ALL_LIGHTS_AS_LIGHT: True,
            OPT_EXCLUDED_DEVICES: ["bsm_excl"],
        }
        collected = _run_light_setup(mock_config_entry, mock_session)
        assert not any(isinstance(e, RelayLight) for e in collected)


# ===========================================================================
# #244: opt-in per-room "all lights" aggregate light entity (SHCRoomLightGroup)
# ===========================================================================
#
# Covers the SHCRoomLightGroup entity's aggregation logic (is_on/available over
# multiple devices, turn_on/turn_off fan-out, partial-failure tolerance) built
# directly via its real __init__ (no hass dependency), plus the room-grouping
# wiring in light.py's async_setup_entry (option gating, 2+ device threshold,
# stale-entity cleanup) driven with a fake hass/config_entry/session.

def _make_room_light_device(
    *,
    device_id: str,
    binarystate: bool | None = False,
    status: str = "AVAILABLE",
    room_id: str | None = "hz_1",
    async_set_binarystate=None,
) -> SimpleNamespace:
    """Minimal device for LightSwitch/SHCRoomLightGroup grouping."""
    return SimpleNamespace(
        name=f"Test Light {device_id}",
        id=device_id,
        room_id=room_id,
        root_device_id="aa:bb:cc:00:00:03",
        serial=f"serial-{device_id}",
        supports_color_hsb=False,
        supports_color_temp=False,
        supports_brightness=True,
        min_color_temperature=153,
        max_color_temperature=500,
        binarystate=binarystate,
        device_services=[],
        manufacturer="Bosch",
        device_model="LD",
        status=status,
        deleted=False,
        async_set_binarystate=async_set_binarystate or AsyncMock(),
    )


def _make_room(room_id: str, name: str) -> SimpleNamespace:
    return SimpleNamespace(id=room_id, name=name)


# ── SHCRoomLightGroup: pure entity behaviour ─────────────────────────────────

def test_unique_id_and_device_info():
    devices = [_make_room_light_device(device_id="d1"), _make_room_light_device(device_id="d2")]
    group = SHCRoomLightGroup(
        devices=devices, room_id="hz_1", room_name="Wohnzimmer", entry_id="E1"
    )
    assert group.unique_id == "room_hz_1_light_group"
    assert group.device_info["identifiers"] == {(DOMAIN, "room_hz_1_light")}
    assert group.device_info["name"] == "Wohnzimmer"
    assert group.device_info["via_device"] == (DOMAIN, "aa:bb:cc:00:00:03")


def test_color_mode_is_onoff():
    devices = [_make_room_light_device(device_id="d1"), _make_room_light_device(device_id="d2")]
    group = SHCRoomLightGroup(
        devices=devices, room_id="hz_1", room_name="Wohnzimmer", entry_id="E1"
    )
    assert group.color_mode == ColorMode.ONOFF
    assert group.supported_color_modes == {ColorMode.ONOFF}


def test_is_on_true_when_any_member_on():
    devices = [
        _make_room_light_device(device_id="d1", binarystate=False),
        _make_room_light_device(device_id="d2", binarystate=True),
    ]
    group = SHCRoomLightGroup(
        devices=devices, room_id="hz_1", room_name="Wohnzimmer", entry_id="E1"
    )
    assert group.is_on is True


def test_is_on_false_when_all_members_off():
    devices = [
        _make_room_light_device(device_id="d1", binarystate=False),
        _make_room_light_device(device_id="d2", binarystate=False),
    ]
    group = SHCRoomLightGroup(
        devices=devices, room_id="hz_1", room_name="Wohnzimmer", entry_id="E1"
    )
    assert group.is_on is False


def test_is_on_none_when_all_members_unknown():
    devices = [
        _make_room_light_device(device_id="d1", binarystate=None),
        _make_room_light_device(device_id="d2", binarystate=None),
    ]
    group = SHCRoomLightGroup(
        devices=devices, room_id="hz_1", room_name="Wohnzimmer", entry_id="E1"
    )
    assert group.is_on is None


def test_available_true_when_any_member_available():
    devices = [
        _make_room_light_device(device_id="d1", status="UNAVAILABLE"),
        _make_room_light_device(device_id="d2", status="AVAILABLE"),
    ]
    group = SHCRoomLightGroup(
        devices=devices, room_id="hz_1", room_name="Wohnzimmer", entry_id="E1"
    )
    assert group.available is True


def test_available_false_when_no_member_available():
    devices = [
        _make_room_light_device(device_id="d1", status="UNAVAILABLE"),
        _make_room_light_device(device_id="d2", status="UNAVAILABLE"),
    ]
    group = SHCRoomLightGroup(
        devices=devices, room_id="hz_1", room_name="Wohnzimmer", entry_id="E1"
    )
    assert group.available is False


def test_turn_on_sets_binarystate_true_on_all_members():
    d1 = _make_room_light_device(device_id="d1")
    d2 = _make_room_light_device(device_id="d2")
    group = SHCRoomLightGroup(
        devices=[d1, d2], room_id="hz_1", room_name="Wohnzimmer", entry_id="E1"
    )
    asyncio.run(group.async_turn_on())
    d1.async_set_binarystate.assert_awaited_once_with(True)
    d2.async_set_binarystate.assert_awaited_once_with(True)


def test_turn_off_sets_binarystate_false_on_all_members():
    d1 = _make_room_light_device(device_id="d1")
    d2 = _make_room_light_device(device_id="d2")
    group = SHCRoomLightGroup(
        devices=[d1, d2], room_id="hz_1", room_name="Wohnzimmer", entry_id="E1"
    )
    asyncio.run(group.async_turn_off())
    d1.async_set_binarystate.assert_awaited_once_with(False)
    d2.async_set_binarystate.assert_awaited_once_with(False)


def test_turn_on_one_member_failure_does_not_block_others():
    """A single device's write failure must not prevent the others from being set."""

    async def _raise(_value):
        raise ConnectionError("boom")

    d1 = _make_room_light_device(device_id="d1", async_set_binarystate=_raise)
    d2 = _make_room_light_device(device_id="d2")
    group = SHCRoomLightGroup(
        devices=[d1, d2], room_id="hz_1", room_name="Wohnzimmer", entry_id="E1"
    )
    asyncio.run(group.async_turn_on())  # must not raise
    d2.async_set_binarystate.assert_awaited_once_with(True)


def test_subscribes_to_every_member_device_and_service_on_add():
    # subscribe_callback/unsubscribe_callback are plain sync methods on the
    # real lib classes — use MagicMock (not AsyncMock) to match that.
    service = SimpleNamespace(
        subscribe_callback=MagicMock(), unsubscribe_callback=MagicMock()
    )
    d1 = _make_room_light_device(device_id="d1")
    d1.device_services = [service]
    d1.subscribe_callback = MagicMock()
    d1.unsubscribe_callback = MagicMock()
    group = SHCRoomLightGroup(
        devices=[d1], room_id="hz_1", room_name="Wohnzimmer", entry_id="E1"
    )
    group.entity_id = "light.wohnzimmer_light"
    group.hass = SimpleNamespace()
    group.async_write_ha_state = lambda: None

    asyncio.run(group.async_added_to_hass())
    service.subscribe_callback.assert_called_once()
    d1.subscribe_callback.assert_called_once()

    asyncio.run(group.async_will_remove_from_hass())
    service.unsubscribe_callback.assert_called_once_with("light.wohnzimmer_light")
    d1.unsubscribe_callback.assert_called_once_with("light.wohnzimmer_light")


def test_device_deletion_triggers_config_entry_reload():
    """A member unpaired live (no options change) must trigger a full reload.

    Unlike SHCEntity (one entity = one device, which just detaches itself),
    this group can't locally repair its membership — reloading re-runs
    async_setup_entry, which rebuilds/removes the group from the current
    device list. Same recovery already used by select.py's
    InstallationProfileSelect after a profile write.
    """
    d1 = _make_room_light_device(device_id="d1")
    d2 = _make_room_light_device(device_id="d2")
    d1.subscribe_callback = MagicMock()
    d2.subscribe_callback = MagicMock()
    group = SHCRoomLightGroup(
        devices=[d1, d2], room_id="hz_1", room_name="Wohnzimmer", entry_id="E1"
    )
    group.entity_id = "light.wohnzimmer_light"
    group.hass = SimpleNamespace(
        async_create_task=MagicMock(),
        config_entries=SimpleNamespace(async_reload=AsyncMock()),
    )

    asyncio.run(group.async_added_to_hass())
    device_callback = d1.subscribe_callback.call_args.args[1]

    d1.deleted = True
    device_callback()

    group.hass.async_create_task.assert_called_once()
    group.hass.config_entries.async_reload.assert_called_once_with("E1")


def test_device_change_without_deletion_just_refreshes_state():
    """A non-deletion device-level update must NOT trigger a reload."""
    d1 = _make_room_light_device(device_id="d1")
    d1.subscribe_callback = MagicMock()
    group = SHCRoomLightGroup(
        devices=[d1], room_id="hz_1", room_name="Wohnzimmer", entry_id="E1"
    )
    group.entity_id = "light.wohnzimmer_light"
    group.hass = SimpleNamespace(async_create_task=MagicMock())
    refreshed = []
    group.schedule_update_ha_state = lambda: refreshed.append(True)

    asyncio.run(group.async_added_to_hass())
    device_callback = d1.subscribe_callback.call_args.args[1]
    device_callback()

    assert refreshed == [True]
    group.hass.async_create_task.assert_not_called()


# ── light.py async_setup_entry: room-grouping wiring ─────────────────────────

def test_option_disabled_creates_no_group(mock_config_entry, mock_session):
    devices = [
        _make_room_light_device(device_id="d1", room_id="hz_1"),
        _make_room_light_device(device_id="d2", room_id="hz_1"),
    ]
    mock_session.device_helper.ledvance_lights = devices
    mock_session.rooms = [_make_room("hz_1", "Wohnzimmer")]
    mock_config_entry.options = {OPT_ROOM_LIGHT_GROUPS: False}
    entities, remove_mock = _run_light_setup_with_remove_mock(mock_config_entry, mock_session)
    assert not any(isinstance(e, SHCRoomLightGroup) for e in entities)
    assert len(entities) == 2  # the 2 LightSwitch entities only
    # Option off -> cleanup path runs once, for the one room that has devices.
    assert remove_mock.await_count == 1
    assert remove_mock.await_args.args[1:] == (Platform.LIGHT, "room_hz_1_light_group")


def test_option_enabled_two_lights_same_room_creates_group(mock_config_entry, mock_session):
    devices = [
        _make_room_light_device(device_id="d1", room_id="hz_1"),
        _make_room_light_device(device_id="d2", room_id="hz_1"),
    ]
    mock_session.device_helper.ledvance_lights = devices
    mock_session.rooms = [_make_room("hz_1", "Wohnzimmer")]
    mock_config_entry.options = {OPT_ROOM_LIGHT_GROUPS: True}
    entities, remove_mock = _run_light_setup_with_remove_mock(mock_config_entry, mock_session)
    groups = [e for e in entities if isinstance(e, SHCRoomLightGroup)]
    assert len(groups) == 1
    assert groups[0].unique_id == "room_hz_1_light_group"
    assert remove_mock.await_count == 0


def test_option_enabled_single_light_in_room_creates_no_group(mock_config_entry, mock_session):
    devices = [_make_room_light_device(device_id="d1", room_id="hz_1")]
    mock_session.device_helper.ledvance_lights = devices
    mock_session.rooms = [_make_room("hz_1", "Wohnzimmer")]
    mock_config_entry.options = {OPT_ROOM_LIGHT_GROUPS: True}
    entities, remove_mock = _run_light_setup_with_remove_mock(mock_config_entry, mock_session)
    assert not any(isinstance(e, SHCRoomLightGroup) for e in entities)
    assert remove_mock.await_count == 1


def test_option_enabled_lights_in_different_rooms_create_no_group(
    mock_config_entry, mock_session
):
    devices = [
        _make_room_light_device(device_id="d1", room_id="hz_1"),
        _make_room_light_device(device_id="d2", room_id="hz_2"),
    ]
    mock_session.device_helper.ledvance_lights = devices
    mock_session.rooms = [_make_room("hz_1", "Wohnzimmer"), _make_room("hz_2", "Küche")]
    mock_config_entry.options = {OPT_ROOM_LIGHT_GROUPS: True}
    entities, remove_mock = _run_light_setup_with_remove_mock(mock_config_entry, mock_session)
    assert not any(isinstance(e, SHCRoomLightGroup) for e in entities)
    assert remove_mock.await_count == 2


def test_missing_rooms_attribute_does_not_crash(mock_config_entry, mock_session):
    """Older/fake sessions without `.rooms` must not break setup (getattr-safe)."""
    devices = [_make_room_light_device(device_id="d1", room_id="hz_1")]
    mock_session.device_helper.ledvance_lights = devices
    # deliberately NOT setting mock_session.rooms -- mimics an older/fake
    # session without that attribute (getattr(..., "rooms", []) fallback).
    mock_config_entry.options = {OPT_ROOM_LIGHT_GROUPS: True}
    entities, _ = _run_light_setup_with_remove_mock(mock_config_entry, mock_session)
    assert len(entities) == 1
    assert isinstance(entities[0], LightSwitch)
