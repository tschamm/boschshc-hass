"""Platform for cover integration."""
import logging
import asyncio

from homeassistant.components.cover import (
    SUPPORT_OPEN,
    SUPPORT_CLOSE,
    SUPPORT_STOP,
    SUPPORT_SET_POSITION,
    ATTR_POSITION,
    CoverDevice,
)
from BoschShcPy import shutter_control

from .const import DOMAIN

from homeassistant.const import CONF_NAME
from homeassistant.util import slugify

_LOGGER = logging.getLogger(__name__)


async def asyn_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Set up the platform."""

    dev = []
    client = hass.data[DOMAIN][slugify(config[CONF_NAME])]

    for cover in shutter_control.initialize_shutter_controls(client, client.device_list()):
        _LOGGER.debug("Found shutter control: %s" % cover.get_id)
        dev.append(ShutterControlCover(cover, cover.get_name,
                                       cover.get_state, cover.get_level, client))

    if dev:
        return await async_add_entities(dev)


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the platform."""

    dev = []
    client = hass.data[DOMAIN][slugify(config_entry.data[CONF_NAME])].my_client

    for cover in shutter_control.initialize_shutter_controls(client, client.device_list()):
        _LOGGER.debug("Found shutter control: %s" % cover.get_id)
        dev.append(ShutterControlCover(cover, cover.get_name,
                                       cover.get_state, 100, client))

    if dev:
        async_add_entities(dev)    


class ShutterControlCover(CoverDevice):

    def __init__(self, cover, name, state, level, client):
        self._representation = cover
        self._client = client
        self._current_cover_position = level
        self._last_cover_position = level
        self._state = state
        self._name = name
        self._client.register_device(
            self._representation, self.update_callback)
        self._client.register_device(
            self._representation.get_device, self.update_callback)
        self.update()

    def update_callback(self, device):
        _LOGGER.debug(
            "Update notification for shutter control: %s" % device.id)
        self.schedule_update_ha_state(True)

    @property
    def unique_id(self):
        """Return the unique ID of this cover."""
        return self._representation.get_device.serial

    @property
    def device_id(self):
        """Return the ID of this cover."""
        return self.unique_id

    @property
    def root_device(self):
        return self._representation.get_device.rootDeviceId

    @property
    def device_info(self):
        """Return the device info."""
        return {
            "identifiers": {(DOMAIN, self.device_id)},
            "name": self._name,
            "manufacturer": self.manufacturer,
            "model": self._representation.get_device.deviceModel,
            "sw_version": "",
            "via_device": (DOMAIN, self._client.get_ip_address),
            # "via_device": (DOMAIN, self.root_device),
        }


    @property
    def name(self):
        """Name of the device."""
        return self._name

    @property
    def manufacturer(self):
        """The manufacturer of the device."""
        return self._representation.get_device.manufacturer

    @property
    def should_poll(self):
        """Polling needed."""
        return False

    @property
    def available(self):
        """Return False if state has not been updated yet."""
        return self._representation.get_availability

    @property
    def supported_features(self):
        """Flag supported features."""
        return SUPPORT_OPEN | SUPPORT_CLOSE | SUPPORT_STOP | SUPPORT_SET_POSITION

    @property
    def current_cover_position(self):
        """The current cover position."""
        return self._current_cover_position

    def stop_cover(self, **kwargs):
        """Stop the cover."""
        self._representation.stop()
        return

    @property
    def is_closed(self):
        """Return if the cover is closed or not."""
        if self._representation.get_level == None:
            return None
        elif self._representation.get_level == 0.:
            return True
        return False

    @property
    def is_opening(self):
        """Return if the cover is opening or not."""
        if self._last_cover_position < self._current_cover_position:
            return True
        else:
            False


    @property
    def is_closing(self):
        """Return if the cover is closing or not."""
        if self._last_cover_position > self._current_cover_position:
            return True
        else:
            False


    def open_cover(self, **kwargs):
        """Open the cover."""
        level = 1.
        self._representation.set_level(level)

    def close_cover(self, **kwargs):
        """Close cover."""
        level = 0.
        self._representation.set_level(level)

    def set_cover_position(self, **kwargs):
        """Move the cover to a specific position."""
        if ATTR_POSITION in kwargs:
            position = float(kwargs[ATTR_POSITION])
            position = min(100, max(0, position))
            level = position / 100.0
            self._representation.set_level(level)

    def update(self, **kwargs):
        if self._representation.update():
            self._last_cover_position = self._current_cover_position
            self._current_cover_position = int(self._representation.get_level * 100.)
            self._state = self._representation.get_state
            self._name = self._representation.get_name
