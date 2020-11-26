"""Platform for light integration."""
import logging

from boschshcpy import SHCSession

from homeassistant.components.light import (
    LightEntity, SUPPORT_COLOR_TEMP, SUPPORT_BRIGHTNESS, ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP,
)

from .const import DOMAIN
from .entity import SHCEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the light platform."""
    entities = []
    session: SHCSession = hass.data[DOMAIN][config_entry.entry_id]

    for light in session.device_helper.hue_lights:
        room_name = session.room(light.room_id).name
        entities.append(
            LightSwitch(
                device=light, room_name=room_name, shc_uid=session.information.name
            )
        )

    if entities:
        async_add_entities(entities)


class LightSwitch(SHCEntity, LightEntity):
    """Representation of a SHC controlled light."""
    
    @property
    def supported_features(self):
        """Flag supported features."""
        if self._device.supports_brightness:
            return SUPPORT_BRIGHTNESS
        if self._device.supports_color:
            return SUPPORT_COLOR_TEMP
        return 0

    @property
    def is_on(self):
        """Return light state."""
        return self._device.state

    @property
    def brightness(self) -> int:
        """Return the brightness of this light between 0..255."""
        brightness_value = (
            round(self._device.brightness * 255 / 100) if self._device.brightness else None
        )
        return brightness_value

    def turn_on(self, **kwargs):
        """Turn the light on."""
        if not self.is_on:
            self._device.state = True

        brightness = kwargs.get(ATTR_BRIGHTNESS)

        if brightness is None:
            brightness = self.brightness

        self._device.brightness = round(brightness * 100 / 255)

    def turn_off(self, **kwargs):
        """Turn the light off."""
        self._device.state = False
