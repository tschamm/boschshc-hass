"""Platform for binarysensor integration."""
import logging

from homeassistant.components.binary_sensor import (
    DEVICE_CLASSES,
    BinarySensorDevice,
)
from BoschShcPy import shutter_contact

from .const import DOMAIN, SHC_LOGIN
import homeassistant
SHC_BRIDGE = "shc_bridge"

_LOGGER = logging.getLogger(__name__)


def setup_platform(hass, config, add_entities, discovery_info=None):
    """Set up the platform."""
    # We only want this platform to be set up via disbinarysensory.
    dev = []
    client = hass.data[SHC_BRIDGE]
    
    for binarysensor in shutter_contact.initialize_shutter_contacts(client, client.device_list()):
        _LOGGER.debug("Found shutter contact: %s" % binarysensor.get_id)
        dev.append(ShutterContactSensor(binarysensor, binarysensor.get_name, binarysensor.get_state, client))
    
    if dev:
        add_entities(dev, True)

class ShutterContactSensor(BinarySensorDevice):

    def __init__(self, binarysensor, name, state, client):
        self._representation = binarysensor
        self._client = client
        self._state = state
        self._name = name
        self._client.register_device(self._representation, self.update_callback)
        self._client.register_device(self._representation.get_device, self.update_callback)
    
    def update_callback(self, device):
        _LOGGER.debug("Update notification for shutter contact: %s" % device.id)
        self.schedule_update_ha_state(True)
        
    @property
    def name(self):
        """Name of the device."""
        return self._name

    @property
    def should_poll(self):
        """Polling needed."""
        return False
    
    @property
    def available(self):
        """Return False if state has not been updated yet."""
#         _LOGGER.debug("Cover available: %s" % self._representation.get_availability)
        return self._representation.get_availability
                    
    @property
    def is_on(self):
        """If the binary sensor is currently on or off."""
        return True if self._state == shutter_contact.state.OPEN else False

    @property
    def device_class(self):
        """Return the class of this device, from component DEVICE_CLASSES."""
        switcher = {
            shutter_contact.deviceclass.ENTRANCE_DOOR: homeassistant.components.binary_sensor.DEVICE_CLASS_DOOR,
            shutter_contact.deviceclass.REGULAR_WINDOW: homeassistant.components.binary_sensor.DEVICE_CLASS_WINDOW,
            shutter_contact.deviceclass.FRENCH_WINDOW: homeassistant.components.binary_sensor.DEVICE_CLASS_DOOR,
            shutter_contact.deviceclass.GENERIC: homeassistant.components.binary_sensor.DEVICE_CLASS_WINDOW,
            }
        return switcher.get(self._representation.get_deviceclass, homeassistant.components.binary_sensor.DEVICE_CLASS_WINDOW)
            
    def update(self, **kwargs):
        if self._representation.update():
            self._state = self._representation.get_state
            self._name = self._representation.get_name
        