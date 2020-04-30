"""Platform for cover integration."""
import logging

from boschshcpy import SHCSession, SHCShutterControl

from homeassistant.components.cover import (
    ATTR_POSITION,
    SUPPORT_CLOSE,
    SUPPORT_OPEN,
    SUPPORT_SET_POSITION,
    SUPPORT_STOP,
    CoverEntity,
)
from homeassistant.const import CONF_IP_ADDRESS

from .const import DOMAIN
from .entity import SHCEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the cover platform."""

    entities = []
    session: SHCSession = hass.data[DOMAIN][config_entry.entry_id]
    ip_address = config_entry.data[CONF_IP_ADDRESS]

    for device in session.device_helper.shutter_controls:
        _LOGGER.debug("Found shutter control: %s (%s)", device.name, device.id)
        room_name=session.room(device.room_id).name
        entities.append(ShutterControlCover(device=device, room_name=room_name, controller_ip=ip_address))

    if entities:
        async_add_entities(entities)


class ShutterControlCover(SHCEntity, CoverEntity):
    """Representation of a SHC shutter control device."""

    @property
    def supported_features(self):
        """Flag supported features."""
        return SUPPORT_OPEN | SUPPORT_CLOSE | SUPPORT_STOP | SUPPORT_SET_POSITION

    @property
    def current_cover_position(self):
        """The current cover position."""
        return self._device.level * 100.0

    def stop_cover(self, **kwargs):
        """Stop the cover."""
        self._device.stop()

    @property
    def is_closed(self):
        """Return if the cover is closed or not."""
        if self.current_cover_position is None:
            return None
        if self.current_cover_position == 0.0:
            return True
        return False

    @property
    def is_opening(self):
        """Return if the cover is opening or not."""
        if (
            self._device.operation_state
            == SHCShutterControl.ShutterControlService.State.OPENING
        ):
            return True
        return False

    @property
    def is_closing(self):
        """Return if the cover is closing or not."""
        if (
            self._device.operation_state
            == SHCShutterControl.ShutterControlService.State.CLOSING
        ):
            return True
        return False

    def open_cover(self, **kwargs):
        """Open the cover."""
        self._device.level = 1.0

    def close_cover(self, **kwargs):
        """Close cover."""
        self._device.level = 0.0

    def set_cover_position(self, **kwargs):
        """Move the cover to a specific position."""
        if ATTR_POSITION in kwargs:
            position = float(kwargs[ATTR_POSITION])
            position = min(100, max(0, position))
            self._device.level = position / 100.0
