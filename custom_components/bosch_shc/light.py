"""Platform for light integration."""

from boschshcpy import SHCSession
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_HS_COLOR,
    ColorMode,
    LightEntity,
)
from homeassistant.const import Platform
from homeassistant.util import color as color_util

from .const import DATA_SESSION, DOMAIN
from .entity import SHCEntity, async_migrate_to_new_unique_id


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the light platform."""
    entities = []
    session: SHCSession = hass.data[DOMAIN][config_entry.entry_id][DATA_SESSION]

    for light in (
        session.device_helper.ledvance_lights
        + session.device_helper.micromodule_dimmers
    ):
        await async_migrate_to_new_unique_id(hass, Platform.LIGHT, device=light)
        entities.append(
            LightSwitch(
                device=light,
                entry_id=config_entry.entry_id,
            )
        )

    if entities:
        async_add_entities(entities)


class LightSwitch(SHCEntity, LightEntity):
    """Representation of a SHC controlled light."""

    def __init__(self, device, entry_id) -> None:
        super().__init__(device=device, entry_id=entry_id)
        self._attr_supported_color_modes: set[ColorMode] = set()

        if self._device.supports_color_hsb:
            self._attr_supported_color_modes.add(ColorMode.HS)
            self._attr_color_mode = ColorMode.HS
        if self._device.supports_color_temp:
            self._attr_supported_color_modes.add(ColorMode.COLOR_TEMP)
            self._attr_color_mode = ColorMode.COLOR_TEMP
        if self._device.supports_color_hsb or self._device.supports_color_temp:
            self._attr_min_color_temp_kelvin = (
                color_util.color_temperature_mired_to_kelvin(
                    self._device.min_color_temperature
                )
            )
            self._attr_max_color_temp_kelvin = (
                color_util.color_temperature_mired_to_kelvin(
                    self._device.max_color_temperature
                )
            )
        if self._device.supports_brightness:
            if (
                len(self._attr_supported_color_modes) == 0
            ):  # BRIGHTNESS must be the only supported mode
                self._attr_supported_color_modes.add(ColorMode.BRIGHTNESS)
                self._attr_color_mode = ColorMode.BRIGHTNESS
        else:
            if (
                len(self._attr_supported_color_modes) == 0
            ):  # ONOFF must be the only supported mode
                self._attr_supported_color_modes.add(ColorMode.ONOFF)
                self._attr_color_mode = ColorMode.ONOFF

    @property
    def is_on(self):
        """Return light state."""
        return self._device.binarystate

    @property
    def brightness(self) -> int:
        """Return the brightness of this light between 0..255."""
        return round(self._device.brightness * 255 / 100)

    @property
    def hs_color(self):
        """Return the rgb color of this light."""
        rgb_raw = self._device.rgb
        rgb = ((rgb_raw >> 16) & 0xFF, (rgb_raw >> 8) & 0xFF, rgb_raw & 0xFF)
        return color_util.color_RGB_to_hs(*rgb)

    @property
    def color_temp_kelvin(self):
        """Return the color temp of this light."""
        return color_util.color_temperature_mired_to_kelvin(self._device.color)

    def turn_on(self, **kwargs):
        """Turn the light on."""
        hs_color = kwargs.get(ATTR_HS_COLOR)
        color_temp_kelvin = kwargs.get(ATTR_COLOR_TEMP_KELVIN)
        brightness = kwargs.get(ATTR_BRIGHTNESS)

        if brightness is not None and self._device.supports_brightness:
            self._device.brightness = max(round(brightness * 100 / 255), 1)

        if color_temp_kelvin is not None and self._device.supports_color_temp:
            self._device.color = color_util.color_temperature_kelvin_to_mired(
                color_temp_kelvin
            )

        if hs_color is not None and self._device.supports_color_hsb:
            rgb = color_util.color_hs_to_RGB(*hs_color)
            raw_rgb = (rgb[0] << 16) + (rgb[1] << 8) + rgb[2]
            self._device.rgb = raw_rgb

        if not self.is_on:
            self._device.binarystate = True

    def turn_off(self, **kwargs):
        """Turn the light off."""
        self._device.binarystate = False
