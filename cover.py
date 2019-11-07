"""Platform for cover integration."""
import logging

from homeassistant.components.cover import (
    SUPPORT_OPEN,
    SUPPORT_CLOSE,
    SUPPORT_STOP,
    SUPPORT_SET_POSITION,
    ATTR_POSITION,
    CoverDevice,
)
from BoschShcPy import shutter_control

from .const import DOMAIN, SHC_LOGIN

SHC_BRIDGE = "shc_bridge"

_LOGGER = logging.getLogger(__name__)


def setup_platform(hass, config, add_entities, discovery_info=None):
    """Set up the platform."""
    # We only want this platform to be set up via discovery.
    dev = []
    client = hass.data[SHC_BRIDGE]

    for cover in shutter_control.initialize_shutter_controls(client, client.device_list()):
        _LOGGER.debug("Found shutter control: %s" % cover.get_id)
        dev.append(ShutterControlCover(cover, cover.get_name,
                                       cover.get_state, cover.get_level, client))

    if dev:
        add_entities(dev, True)


class ShutterControlCover(CoverDevice):

    def __init__(self, cover, name, state, level, client):
        self._representation = cover
        self._client = client
        self._current_cover_position = level
        self._state = state
        self._name = name
        self._manufacturer = self._representation.get_device.manufacturer
        self._client.register_device(
            self._representation, self.update_callback)
        self._client.register_device(
            self._representation.get_device, self.update_callback)

    def update_callback(self, device):
        _LOGGER.debug(
            "Update notification for shutter control: %s" % device.id)
        self.schedule_update_ha_state(True)

    @property
    def name(self):
        """Name of the device."""
        return self._name

    @property
    def manufacturer(self):
        """The manufacturer of the device."""
        return self._manufacturer

    @property
    def should_poll(self):
        """Polling needed."""
        return False

    @property
    def available(self):
        """Return False if state has not been updated yet."""
        #         _LOGGER.debug("Cover available: %s" % self._representation.get_availability)
        return self._representation.get_availability

    # @property
    # def is_opening(self):
    #     """If the cover is currently opening."""
    #     if self._state == shutter_control.
    #
    # @property
    # def today_energy_kwh(self):
    #     """Total energy usage in kWh."""
    #     return self._today_energy_kwh

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

    # def turn_on(self, **kwargs):
    #     """Turn the switch on."""
    #     self._representation.set_state(True)
    #     self._is_on = True
    #     _LOGGER.debug("New switch state is %s" % self._is_on)
    #
    # def turn_off(self, **kwargs):
    #     """Turn the switch off."""
    #     self._representation.set_state(False)
    #     self._is_on = False
    #     _LOGGER.debug("New switch state is %s" % self._is_on)
    #
    # def toggle(self, **kwargs):
    #     """Toggles the switch."""
    #     self._representation.set_state(not self._representation.get_state())
    #     self._is_on = not self._is_on
    #     _LOGGER.debug("New switch state is %s" % self._is_on)

    def update(self, **kwargs):
        if self._representation.update():
            self._current_cover_position = self._representation.get_level * 100.
            self._state = self._representation.get_state
            self._name = self._representation.get_name
