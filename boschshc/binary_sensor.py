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
from boschshcpy import SHCSession, SHCDeviceHelper, SHCShutterContact, SHCSmokeDetector

from .const import DOMAIN

from homeassistant.const import CONF_NAME, CONF_IP_ADDRESS
from homeassistant.util import slugify

_LOGGER = logging.getLogger(__name__)


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Set up the binary switch platform."""

    device = []
    session: SHCSession = hass.data[DOMAIN][slugify(config[CONF_NAME])]
    
    for binarysensor in session.device_helper.shutter_contacts:
        _LOGGER.debug("Found shutter contact: %s" % binarysensor.id)
        device.append(ShutterContactSensor(
            binarysensor, config[CONF_IP_ADDRESS]))

    # for binarysensor in smoke_detector.initialize_smoke_detectors(client, client.device_list()):
    #     _LOGGER.debug("Found smoke detector: %s" % binarysensor.get_id)
    #     device.append(SmokeDetectorSensor(
    #         binarysensor, binarysensor.get_name, binarysensor.get_state, client))

    if device:
        return await async_add_entities(device)


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the binary switch platform."""

    device = []
    session: SHCSession = hass.data[DOMAIN][slugify(config_entry.data[CONF_NAME])]

    for binarysensor in session.device_helper.shutter_contacts:
        _LOGGER.debug(f"Found shutter contact: {binarysensor.name} ({binarysensor.id})")
        device.append(ShutterContactSensor(
            binarysensor, config_entry.data[CONF_IP_ADDRESS]))

    # for binarysensor in smoke_detector.initialize_smoke_detectors(client, client.device_list()):
    #     _LOGGER.debug("Found smoke detector: %s" % binarysensor.get_id)
    #     dev.append(SmokeDetectorSensor(
    #         binarysensor, binarysensor.get_name, binarysensor.get_state, client))

    if device:
        async_add_entities(device)

    # for item in dev:
    #     item.update()
    

class ShutterContactSensor(BinarySensorDevice):
    def __init__(self, device: SHCShutterContact, controller_ip: str):
        self._device = device
        self._room = self._device.room_id
        self._controller_ip = controller_ip
    
    async def async_added_to_hass(self):
        await super().async_added_to_hass()

        def on_state_changed():
            _LOGGER.debug("Update notification for shutter contact: %s" % self._device.id)
            self.schedule_update_ha_state()

        for service in self._device.device_services:
            service.on_state_changed = on_state_changed

    @property
    def unique_id(self):
        """Return the unique ID of this binary sensor."""
        return self._device.serial

    @property
    def device_id(self):
        """Return the ID of this binary sensor."""
        return self._device.id

    @property
    def root_device(self):
        return self._device.root_device_id

    @property
    def name(self):
        """Name of the device."""
        return self._device.name

    @property
    def manufacturer(self):
        """The manufacturer of the device."""
        return self._device.manufacturer

    @property
    def device_info(self):
        """Return the device info."""
        return {
            "identifiers": {(DOMAIN, self.device_id)},
            "name": self.name,
            "manufacturer": self.manufacturer,
            "model": self._device.device_model,
            "sw_version": "",
            "via_device": (DOMAIN, self._controller_ip)
        }

    @property
    def should_poll(self):
        """Polling needed."""
        return False
    
    @property
    def available(self):
        """Return false if status is unavailable."""
        return True if self._device.status == "AVAILABLE" else False
                    
    @property
    def is_on(self):
        """Return the state of the sensor."""
        return True if self._device.state == SHCShutterContact.ShutterContactService.State.OPEN else False

    @property
    def device_class(self):
        """Return the class of this device, from component DEVICE_CLASSES."""
        switcher = {
            SHCShutterContact.DeviceClass.ENTRANCE_DOOR: DEVICE_CLASS_DOOR,
            SHCShutterContact.DeviceClass.REGULAR_WINDOW: DEVICE_CLASS_WINDOW,
            SHCShutterContact.DeviceClass.FRENCH_WINDOW: DEVICE_CLASS_DOOR,
            SHCShutterContact.DeviceClass.GENERIC: DEVICE_CLASS_WINDOW,
            }
        return switcher.get(self._device.device_class, DEVICE_CLASS_WINDOW)
            
    def update(self, **kwargs):
        self._device.update()
        

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
