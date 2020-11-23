"""Platform for light integration."""
import logging

from boschshcpy import SHCSession

from homeassistant.components.light import (
    LightEntity,
)

from .const import DOMAIN
from .entity import SHCEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the light platform."""
    entities = []
    session: SHCSession = hass.data[DOMAIN][config_entry.entry_id]

    for light in session.device_helper.ledvance_lights:
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
    def is_on(self):
        """Return light state."""
        return self._device.state

    def turn_on(self, **kwargs):
        """Turn the light on."""
        self._device.state = True

    def turn_off(self, **kwargs):
        """Turn the light off."""
        self._device.state = False

    def toggle(self, **kwargs):
        """Toggles the light."""
        self._device.state = not self.is_on
