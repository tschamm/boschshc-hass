"""Platform for binarysensor integration."""
import logging
import asyncio

from homeassistant.components.binary_sensor import (
    DEVICE_CLASSES,
    DEVICE_CLASS_SMOKE,
    DEVICE_CLASS_DOOR,
    DEVICE_CLASS_WINDOW,
    BinarySensorDevice,
)
from BoschShcPy import shutter_contact, smoke_detector

from .const import DOMAIN

from homeassistant.const import CONF_NAME
from homeassistant.util import slugify

_LOGGER = logging.getLogger(__name__)


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Set up the binary switch platform."""

    dev = []
    client = hass.data[DOMAIN][slugify(config[CONF_NAME])]
    
    for binarysensor in shutter_contact.initialize_shutter_contacts(client, client.device_list()):
        _LOGGER.debug("Found shutter contact: %s" % binarysensor.get_id)
        dev.append(ShutterContactSensor(binarysensor, binarysensor.get_name, binarysensor.get_state, client))

    for binarysensor in smoke_detector.initialize_smoke_detectors(client, client.device_list()):
        _LOGGER.debug("Found smoke detector: %s" % binarysensor.get_id)
        dev.append(SmokeDetectorSensor(binarysensor, binarysensor.get_name, binarysensor.get_state, client))

    if dev:
        return await async_add_entities(dev)


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the binary switch platform."""

    dev = []
    client = hass.data[DOMAIN][slugify(config_entry.data[CONF_NAME])].my_client

    for binarysensor in shutter_contact.initialize_shutter_contacts(client, client.device_list()):
        _LOGGER.debug("Found shutter contact: %s" % binarysensor.get_id)
        dev.append(ShutterContactSensor(
            binarysensor, binarysensor.get_name, binarysensor.get_state, client))

    for binarysensor in smoke_detector.initialize_smoke_detectors(client, client.device_list()):
        _LOGGER.debug("Found smoke detector: %s" % binarysensor.get_id)
        dev.append(SmokeDetectorSensor(
            binarysensor, binarysensor.get_name, binarysensor.get_state, client))

    if dev:
        async_add_entities(dev)

class ShutterContactSensor(BinarySensorDevice):

    def __init__(self, binarysensor, name, state, client):
        self._representation = binarysensor
        self._client = client
        self._state = state
        self._name = name
        self._manufacturer = self._representation.get_device.manufacturer
        self._client.register_device(self._representation, self.update_callback)
        self._client.register_device(self._representation.get_device, self.update_callback)
    
    def update_callback(self, device):
        _LOGGER.debug("Update notification for shutter contact: %s" % device.id)
        self.schedule_update_ha_state(True)
        

    @property
    def unique_id(self):
        """Return the unique ID of this binary sensor."""
        return self._representation.get_device.serial

    @property
    def device_id(self):
        """Return the ID of this binary sensor."""
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
            shutter_contact.deviceclass.ENTRANCE_DOOR: DEVICE_CLASS_DOOR,
            shutter_contact.deviceclass.REGULAR_WINDOW: DEVICE_CLASS_WINDOW,
            shutter_contact.deviceclass.FRENCH_WINDOW: DEVICE_CLASS_DOOR,
            shutter_contact.deviceclass.GENERIC: DEVICE_CLASS_WINDOW,
            }
        return switcher.get(self._representation.get_deviceclass, DEVICE_CLASS_WINDOW)
            
    def update(self, **kwargs):
        if self._representation.update():
            print("Update called!")
            self._state = self._representation.get_state
            self._name = self._representation.get_name


class SmokeDetectorSensor(BinarySensorDevice):

    def __init__(self, binarysensor, name, state, client):
        self._representation = binarysensor
        self._client = client
        self._state = state
        self._name = name
        self._manufacturer = self._representation.get_device.manufacturer
        self._client.register_device(self._representation, self.update_callback)
        self._client.register_device(self._representation.get_device, self.update_callback)

    def update_callback(self, device):
        _LOGGER.debug("Update notification for smoke detector: %s" % device.id)
        self.schedule_update_ha_state(True)


    @property
    def unique_id(self):
        """Return the unique ID of this smoke detector."""
        return self._representation.get_device.serial

    @property
    def device_id(self):
        """Return the ID of this smoke detector."""
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
            "via_device": DOMAIN,
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
    def is_on(self):
        """If the binary sensor is currently on or off."""
        return False if self._state == smoke_detector.state.IDLE_OFF else True

    @property
    def device_class(self):
        """Return the class of this device, from component DEVICE_CLASSES."""
        return DEVICE_CLASS_SMOKE

    def update(self, **kwargs):
        if self._representation.update():
            self._state = self._representation.get_state
            self._name = self._representation.get_name
