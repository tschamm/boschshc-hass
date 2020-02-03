"""Platform for switch integration."""
import logging
import asyncio

from homeassistant.components.switch import SwitchDevice
from BoschShcPy import smart_plug, camera_eyes, client

from .const import DOMAIN

from homeassistant.const import CONF_NAME
from homeassistant.util import slugify

_LOGGER = logging.getLogger(__name__)


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Set up the sensor platform."""

    dev = []
    client = hass.data[DOMAIN][slugify(config[CONF_NAME])]

    for plug in smart_plug.initialize_smart_plugs(client, client.device_list()):
        _LOGGER.debug("Found smart plug: %s" % plug.get_id)
        dev.append(SmartPlugSwitch(plug, plug.get_name, plug.get_state,
                                   plug.get_powerConsumption, plug.get_energyConsumption, client))

    for camera in camera_eyes.initialize_camera_eyes(client, client.device_list()):
        _LOGGER.debug("Found camera eyes: %s" % camera.get_id)
        dev.append(CameraEyesSwitch(camera, camera.get_name, camera.get_light_state, client))

    for scenario in client.scenario_list().items:
        _LOGGER.debug("Found scenario: %s" % scenario.get_id)
        dev.append(ScenarioSwitch(scenario, scenario.get_name, client))

    if dev:
        return await async_add_entities(dev)


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the sensor platform."""

    dev = []
    client = hass.data[DOMAIN][slugify(config_entry.data[CONF_NAME])].my_client

    for plug in smart_plug.initialize_smart_plugs(client, client.device_list()):
        _LOGGER.debug("Found smart plug: %s" % plug.get_id)
        dev.append(SmartPlugSwitch(plug, plug.get_name, plug.get_state,
                                   plug.get_powerConsumption, plug.get_energyConsumption, client))

    for camera in camera_eyes.initialize_camera_eyes(client, client.device_list()):
        _LOGGER.debug("Found camera eyes: %s" % camera.get_id)
        dev.append(CameraEyesSwitch(camera, camera.get_name,
                                    camera.get_light_state, client))

    for scenario in client.scenario_list().items:
        _LOGGER.debug("Found scenario: %s" % scenario.get_id)
        dev.append(ScenarioSwitch(scenario, scenario.get_name, client))

    if dev:
        async_add_entities(dev)

class SmartPlugSwitch(SwitchDevice):

    def __init__(self, plug, name, state, powerConsumption, energyConsumption, client):
        self._representation = plug
        self._client = client
        self._is_on = state
        self._today_energy_kwh = energyConsumption
        self._current_power_w = powerConsumption
        self._name = name
        self._client.register_device(self._representation, self.update_callback)
        self._client.register_device(self._representation.get_device, self.update_callback)

    def update_callback(self, device):
        _LOGGER.debug("Update notification for smart plug: %s" % device.id)
        self.schedule_update_ha_state(True)

    @property
    def unique_id(self):
        """Return the unique ID of this switch."""
        return self._representation.get_device.serial

    @property
    def device_id(self):
        """Return the ID of this switch."""
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
    def available(self):
        """Return False if state has not been updated yet."""
        return self._representation.get_availability

    @property
    def is_on(self):
        """If the switch is currently on or off."""
        return self._is_on
        
    @property
    def should_poll(self):
        """Polling needed."""
        return False
    
    @property
    def today_energy_kwh(self):
        """Total energy usage in kWh."""
        return self._today_energy_kwh
    
    @property
    def current_power_w(self):
        """The current power usage in W."""
        return self._current_power_w
    
    def turn_on(self, **kwargs):
        """Turn the switch on."""
        self._representation.set_state(True)
        self._is_on = True
        _LOGGER.debug("New switch state is %s" % self._is_on)

    def turn_off(self, **kwargs):
        """Turn the switch off."""
        self._representation.set_state(False)
        self._is_on = False
        _LOGGER.debug("New switch state is %s" % self._is_on)
    
    def toggle(self, **kwargs):
        """Toggles the switch."""
        self._representation.set_state(not self._representation.get_state())
        self._is_on = not self._is_on
        _LOGGER.debug("New switch state is %s" % self._is_on)
    
    def update(self, **kwargs):
        if self._representation.update():
            self._is_on = self._representation.get_state
            self._today_energy_kwh = self._representation.get_energyConsumption / 1000.
            self._current_power_w = self._representation.get_powerConsumption
            self._name = self._representation.get_name


class CameraEyesSwitch(SwitchDevice):

    def __init__(self, item, name, state, client):
        self._representation = item
        self._client = client
        self._is_on = state
        self._name = name
    #     self._client.register_device(
    #         self._representation, self.update_callback)
    #     self._client.register_device(
    #         self._representation.get_device, self.update_callback)

    # def update_callback(self, device):
    #     _LOGGER.debug("Update notification for camera eyes: %s" % device.id)
    #     self.schedule_update_ha_state(True)

    @property
    def unique_id(self):
        """Return the unique ID of this switch."""
        return self._representation.get_device.serial

    @property
    def device_id(self):
        """Return the ID of this switch."""
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
    def available(self):
        """Return False if state has not been updated yet."""
        return self._representation.get_availability

    @property
    def is_on(self):
        """If the switch is currently on or off."""
        return self._is_on

    @property
    def should_poll(self):
        """Polling needed."""
        return False

    def turn_on(self, **kwargs):
        """Turn the switch on."""
        self._representation.set_light_state(True)
        self._is_on = True
        _LOGGER.debug("New light state is %s" % self._is_on)

    def turn_off(self, **kwargs):
        """Turn the switch off."""
        self._representation.set_light_state(False)
        self._is_on = False
        _LOGGER.debug("New light state is %s" % self._is_on)

    def toggle(self, **kwargs):
        """Toggles the switch."""
        self._representation.set_light_state(not self._representation.get_light_state)
        self._is_on = self._representation.get_light_state
        _LOGGER.debug("New light state is %s" % self._is_on)

    def update(self, **kwargs):
        if self._representation.update():
            self._is_on = self._representation.get_light_state
            self._name = self._representation.get_name


class ScenarioSwitch(SwitchDevice):

    def __init__(self, scenario, name, client):
        self._representation = scenario
        self._client = client
        self._is_on = False
        self._name = name

    @property
    def unique_id(self):
        """Return the unique ID of this switch."""
        return self._representation.get_id

    @property
    def device_id(self):
        """Return the ID of this switch."""
        return self.unique_id

    @property
    def icon(self):
        return "mdi:script-text"

    @property
    def device_info(self):
        """Return the device info."""
        return {
            "identifiers": {(DOMAIN, self.device_id)},
            "name": self._name,
            # "manufacturer": self.manufacturer,
            # "model": self._representation.get_device.deviceModel,
            "sw_version": "",
            "via_device": (DOMAIN, self._client.get_ip_address),
            # "via_device": (DOMAIN, self.root_device),
        }

    @property
    def name(self):
        """Name of the device."""
        return self._name

    @property
    def is_on(self):
        """If the switch is currently on or off."""
        return self._is_on
        
    @property
    def should_poll(self):
        """Polling needed."""
        return False
    
    def turn_on(self, **kwargs):
        """Turn the switch on."""
        self._representation.trigger()

    def turn_off(self, **kwargs):
        """Turn the switch off."""
        # do nothing
        pass
    
    def toggle(self, **kwargs):
        """Toggles the switch."""
        self._representation.trigger()
    
    def update(self, **kwargs):
        if self._representation.update():
            self._name = self._representation.get_name
