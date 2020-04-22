"""Platform for cover integration."""
import logging

from boschshcpy import SHCDeviceHelper, SHCSession, SHCShutterControl

from homeassistant.components.cover import (
    ATTR_POSITION,
    SUPPORT_CLOSE,
    SUPPORT_OPEN,
    SUPPORT_SET_POSITION,
    SUPPORT_STOP,
    CoverDevice,
)
from homeassistant.const import CONF_IP_ADDRESS, CONF_NAME

from .const import DOMAIN
from .entity import SHCEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the cover platform."""

    device = []
    session: SHCSession = hass.data[DOMAIN][config_entry.entry_id]

    for cover in session.device_helper.shutter_controls:
        _LOGGER.debug(f"Found shutter control: {cover.name} ({cover.id})")
        device.append(
            ShutterControlCover(
                device=cover,
                room_name=session.room(cover.room_id).name,
                controller_ip=config_entry.data[CONF_IP_ADDRESS],
            )
        )

    if device:
        async_add_entities(device)


class ShutterControlCover(SHCEntity, CoverDevice):
    """Representation of a SHC shutter control device."""

    @property
    def supported_features(self):
        """Flag supported features."""
        return SUPPORT_OPEN | SUPPORT_CLOSE | SUPPORT_STOP | SUPPORT_SET_POSITION

    @property
    def current_cover_position(self):
        """The current cover position."""
        return self._device.level * 100.0

    def stop_cover(self):
        """Stop the cover."""
        self._device.stop()
        return

    @property
    def is_closed(self):
        """Return if the cover is closed or not."""
        if self.current_cover_position == None:
            return None
        elif self.current_cover_position == 0.0:
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
        else:
            False

    @property
    def is_closing(self):
        """Return if the cover is closing or not."""
        if (
            self._device.operation_state
            == SHCShutterControl.ShutterControlService.State.CLOSING
        ):
            return True
        else:
            False

    def open_cover(self):
        """Open the cover."""
        self._device.level = 1.0

    def close_cover(self):
        """Close cover."""
        self._device.level = 0.0

    def set_cover_position(self, **kwargs):
        """Move the cover to a specific position."""
        if ATTR_POSITION in kwargs:
            position = float(kwargs[ATTR_POSITION])
            position = min(100, max(0, position))
            self._device.level = position / 100.0
