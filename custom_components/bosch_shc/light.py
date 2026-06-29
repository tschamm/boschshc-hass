"""Platform for light integration."""

from __future__ import annotations

import asyncio
from typing import Any

import aiohttp
from boschshcpy import PowerSwitchService, SHCLight, SHCSession
from boschshcpy.device import SHCDevice
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_HS_COLOR,
    ColorMode,
    LightEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import async_get as get_dev_reg
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import color as color_util

from .const import (
    DATA_SESSION,
    DOMAIN,
    LOGGER,
    OPT_SUPPRESS_HUE_LIGHTS,
    OPT_SUPPRESS_LEDVANCE_LIGHTS,
    OPT_SUPPRESS_MOTION_INDICATOR_LIGHT,
)
from .entity import (
    SHCEntity,
    async_migrate_to_new_unique_id,
    device_excluded,
    light_switch_as_light,
    light_switch_devices,
)

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the light platform."""
    entities = []
    session: SHCSession = hass.data[DOMAIN][config_entry.entry_id][DATA_SESSION]

    hue_lights: list[SHCLight] = []
    if config_entry.options.get(OPT_SUPPRESS_HUE_LIGHTS, False):
        dev_registry = get_dev_reg(hass)
        for shc_device in session.device_helper.hue_lights:
            dev_entry = dev_registry.async_get_device(
                identifiers={(DOMAIN, shc_device.id)}, connections=set()
            )
            if dev_entry is not None:
                dev_registry.async_update_device(
                    dev_entry.id, remove_config_entry_id=config_entry.entry_id
                )
    else:
        hue_lights = list(session.device_helper.hue_lights)
    ledvance_lights: list[SHCLight] = []
    if config_entry.options.get(OPT_SUPPRESS_LEDVANCE_LIGHTS, False):
        dev_registry = get_dev_reg(hass)
        for shc_device in session.device_helper.ledvance_lights:
            dev_entry = dev_registry.async_get_device(
                identifiers={(DOMAIN, shc_device.id)}, connections=set()
            )
            if dev_entry is not None:
                dev_registry.async_update_device(
                    dev_entry.id, remove_config_entry_id=config_entry.entry_id
                )
    else:
        ledvance_lights = list(session.device_helper.ledvance_lights)
    for light in (
        ledvance_lights + list(session.device_helper.micromodule_dimmers) + hue_lights
    ):
        if device_excluded(light, config_entry.options):
            continue
        await async_migrate_to_new_unique_id(hass, Platform.LIGHT, device=light)
        entities.append(
            LightSwitch(
                device=light,
                entry_id=config_entry.entry_id,
            )
        )

    if not config_entry.options.get(OPT_SUPPRESS_MOTION_INDICATOR_LIGHT, False):
        for light in session.device_helper.motion_detectors2:
            if device_excluded(light, config_entry.options):
                continue
            await async_migrate_to_new_unique_id(
                hass, Platform.LIGHT, device=light, attr_name="MotionLight"
            )
            entities.append(
                MotionDetectorLight(
                    device=light,
                    entry_id=config_entry.entry_id,
                )
            )

    # #338: Light/Shutter Control II light channels (and BSM light switches) that
    # the user opted in to present as a `light`.  These wrap a plain on/off
    # PowerSwitch relay; the switch platform skips the matching `switch` so the
    # device is exposed exactly once.  Default (no opt-in) -> nothing here.
    for light in light_switch_devices(session):
        if device_excluded(light, config_entry.options):
            continue
        if not light_switch_as_light(light, config_entry.options):
            continue
        entities.append(
            RelayLight(
                device=light,
                entry_id=config_entry.entry_id,
            )
        )

    if entities:
        async_add_entities(entities)


class LightSwitch(SHCEntity, LightEntity):  # type: ignore[misc]
    """Representation of a SHC controlled light."""

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize the SHC light switch entity."""
        super().__init__(device=device, entry_id=entry_id)
        self._attr_supported_color_modes: set[ColorMode] = set()

        if self._device.supports_color_hsb:
            self._attr_supported_color_modes.add(ColorMode.HS)
            self._attr_color_mode = ColorMode.HS
        if self._device.supports_color_temp:
            self._attr_supported_color_modes.add(ColorMode.COLOR_TEMP)
            # Only set COLOR_TEMP as default when HS is NOT also supported;
            # when both are present, HS takes priority (set above).
            if not self._device.supports_color_hsb:
                self._attr_color_mode = ColorMode.COLOR_TEMP
        if self._device.supports_color_hsb or self._device.supports_color_temp:
            min_ct = self._device.min_color_temperature
            max_ct = self._device.max_color_temperature
            # Need a real, non-degenerate range: 0 would ZeroDivide in
            # mired_to_kelvin, and min == max gives equal kelvin bounds which
            # HA's LightEntity rejects (a single-temperature bulb edge case).
            if min_ct and max_ct and min_ct != max_ct:
                # #340: Bosch reports the range in MIREDS (minCt/maxCt). Mireds
                # are inverse to kelvin (kelvin = 1e6 / mired), so the SMALLEST
                # mired is the LARGEST kelvin. HA wants kelvin bounds, so cross
                # them: max mired -> min kelvin, min mired -> max kelvin.
                # (Previously assigned straight, which swapped HA's min/max.)
                self._attr_min_color_temp_kelvin = (
                    color_util.color_temperature_mired_to_kelvin(max_ct)
                )
                self._attr_max_color_temp_kelvin = (
                    color_util.color_temperature_mired_to_kelvin(min_ct)
                )
        if self._device.supports_brightness:
            if (
                len(self._attr_supported_color_modes) == 0
            ):  # BRIGHTNESS must be the only supported mode
                self._attr_supported_color_modes.add(ColorMode.BRIGHTNESS)
                self._attr_color_mode = ColorMode.BRIGHTNESS
        elif (
            len(self._attr_supported_color_modes) == 0
        ):  # ONOFF must be the only supported mode
            self._attr_supported_color_modes.add(ColorMode.ONOFF)
            self._attr_color_mode = ColorMode.ONOFF

    @property
    def is_on(self) -> bool | None:
        """Return light state."""
        state = self._device.binarystate
        return None if state is None else bool(state)

    @property
    def brightness(self) -> int | None:
        """Return the brightness of this light between 0..255."""
        raw = self._device.brightness
        if raw is None:
            return None
        return round(float(raw) * 255 / 100)

    @property
    def hs_color(self) -> tuple[float, float] | None:
        """Return the rgb color of this light."""
        rgb_raw = self._device.rgb
        if rgb_raw is None:
            return None
        rgb = (
            int((rgb_raw >> 16) & 0xFF),
            int((rgb_raw >> 8) & 0xFF),
            int(rgb_raw & 0xFF),
        )
        return color_util.color_RGB_to_hs(*rgb)  # type: ignore[no-any-return]

    @property
    def color_temp_kelvin(self) -> int | None:
        """Return the color temp of this light."""
        if not self._device.color:
            return None
        return int(color_util.color_temperature_mired_to_kelvin(self._device.color))

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on."""
        hs_color = kwargs.get(ATTR_HS_COLOR)
        color_temp_kelvin = kwargs.get(ATTR_COLOR_TEMP_KELVIN)
        brightness = kwargs.get(ATTR_BRIGHTNESS)

        if brightness is not None and self._device.supports_brightness:
            # Bosch API does not accept brightness=0; HA uses brightness=0 to
            # mean "off", which is handled via binarystate. Clamp to 1 so that
            # a near-zero HA value (e.g. 1/255) never silently turns off.
            await self._device.async_set_brightness(
                max(round(brightness * 100 / 255), 1)
            )

        if color_temp_kelvin is not None and self._device.supports_color_temp:
            await self._device.async_set_color(
                color_util.color_temperature_kelvin_to_mired(color_temp_kelvin)
            )
            self._attr_color_mode = ColorMode.COLOR_TEMP

        if hs_color is not None and self._device.supports_color_hsb:
            rgb = color_util.color_hs_to_RGB(*hs_color)
            raw_rgb = (rgb[0] << 16) + (rgb[1] << 8) + rgb[2]
            await self._device.async_set_rgb(raw_rgb)
            self._attr_color_mode = ColorMode.HS

        if not self.is_on:
            await self._device.async_set_binarystate(True)

        self.schedule_update_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off."""
        await self._device.async_set_binarystate(False)


class MotionDetectorLight(SHCEntity, LightEntity):  # type: ignore[misc]
    """Representation of the indicator light on a SHC Motion Detector II [+M]."""

    _attr_supported_color_modes: set[ColorMode] = {ColorMode.BRIGHTNESS}
    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_translation_key = "motion_light"

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize the Motion Detector II light entity."""
        super().__init__(device=device, entry_id=entry_id)
        self._attr_unique_id = f"{device.root_device_id}_{device.id}_motionlight"

    @property
    def is_on(self) -> bool:
        """Return the current on/off state."""
        return bool(self._device.binaryswitch)

    @property
    def brightness(self) -> int:
        """Return the brightness scaled to 0-255."""
        level = self._device.multi_level_switch
        if level is None:
            return 0
        return round(float(level) * 255 / 100)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on, optionally setting brightness."""
        brightness = kwargs.get(ATTR_BRIGHTNESS)
        if brightness is not None:
            # Clamp to 1 so near-zero HA values don't silently turn the light off.
            level = max(round(brightness * 100 / 255), 1)
            await self._device.async_set_multi_level_switch(level)
        if not self.is_on:
            await self._device.async_set_binaryswitch(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off."""
        await self._device.async_set_binaryswitch(False)


class RelayLight(SHCEntity, LightEntity):  # type: ignore[misc]
    """A Light/Shutter Control II (or BSM) light relay presented as a `light`.

    These devices wrap a plain on/off PowerSwitch relay (no brightness/colour),
    so this is an ONOFF light.  Created only when the device is opted in via the
    "expose as light" option (#338); otherwise the switch platform owns it.  The
    unique_id is the standard SHCEntity device id — in the `light` domain it does
    not collide with the historical `switch` entity's id (uniqueness is scoped
    per platform), so toggling the option swaps switch<->light cleanly.
    """

    _attr_supported_color_modes: set[ColorMode] = {ColorMode.ONOFF}
    _attr_color_mode = ColorMode.ONOFF

    @property
    def is_on(self) -> bool | None:
        """Return the relay on/off state, or None if the service is unavailable.

        A relay with no connected load can expose a None PowerSwitch service
        (mirrors SHCSwitch.is_on); return None (unknown) rather than crash.
        """
        try:
            return bool(self._device.switchstate == PowerSwitchService.State.ON)
        except AttributeError:
            return None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the relay on."""
        try:
            await self._device.async_set_switchstate(True)
        except AttributeError:
            LOGGER.debug(
                "turn_on skipped for %s: PowerSwitch service unavailable",
                self.entity_id,
            )
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            # Match SHCSwitch: a transient SHC outage must not raise to the
            # service layer (error log / notification) for this relay.
            LOGGER.debug("turn_on failed for %s: %s", self.entity_id, err)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the relay off."""
        try:
            await self._device.async_set_switchstate(False)
        except AttributeError:
            LOGGER.debug(
                "turn_off skipped for %s: PowerSwitch service unavailable",
                self.entity_id,
            )
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            LOGGER.debug("turn_off failed for %s: %s", self.entity_id, err)
