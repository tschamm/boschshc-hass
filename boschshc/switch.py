"""Platform for switch integration."""
import logging
import asyncio

from homeassistant.components.switch import SwitchDevice
from boschshcpy import SHCSession, SHCDeviceHelper, SHCSmartPlug, SHCCameraEyes

from .const import DOMAIN

from homeassistant.const import CONF_NAME, CONF_IP_ADDRESS
from homeassistant.util import slugify

_LOGGER = logging.getLogger(__name__)


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Set up the sensor platform."""

    device = []
    session: SHCSession = hass.data[DOMAIN][slugify(config[CONF_NAME])]

    for switch in session.device_helper.smart_plugs:
        _LOGGER.debug("Found smart plug: %s" % switch.id)
        device.append(SmartPlugSwitch(
            device=switch,
            room_name=session.room(switch.room_id).name,
            controller_ip=config[CONF_IP_ADDRESS])
        )

    for light in session.device_helper.light_controls:
        _LOGGER.debug("Found light control: %s" % light.id)
        device.append(SmartPlugSwitch(
            device=light,
            room_name=session.room(light.room_id).name,
            controller_ip=config[CONF_IP_ADDRESS])
        )

    for cameras in session.device_helper.camera_eyes:
        _LOGGER.debug("Found camera eyes: %s" % cameras.id)
        device.append(CameraEyesSwitch(
            device=cameras,
            room_name=session.room(cameras.room_id).name,
            controller_ip=config[CONF_IP_ADDRESS])
        )

    # for scenario in client.scenario_list().items:
    #     _LOGGER.debug("Found scenario: %s" % scenario.get_id)
    #     dev.append(ScenarioSwitch(scenario, scenario.get_name, client))

    if device:
        return await async_add_entities(device)


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the sensor platform."""

    device = []
    session: SHCSession = hass.data[DOMAIN][slugify(config_entry.data[CONF_NAME])]

    for switch in session.device_helper.smart_plugs:
        _LOGGER.debug(
            f"Found smart plug: {switch.name} ({switch.id})")
        device.append(SmartPlugSwitch(
            device=switch,
            room_name=session.room(switch.room_id).name,
            controller_ip=config_entry.data[CONF_IP_ADDRESS])
        )

    for light in session.device_helper.light_controls:
        _LOGGER.debug(
            f"Found light control: {light.name} ({light.id})")
        device.append(SmartPlugSwitch(
            device=light,
            room_name=session.room(light.room_id).name,
            controller_ip=config_entry.data[CONF_IP_ADDRESS])
        )

    for camera in session.device_helper.camera_eyes:
        _LOGGER.debug(
            f"Found camera eyes: {camera.name} ({camera.id})")
        device.append(CameraEyesSwitch(
            device=camera,
            room_name=session.room(camera.room_id).name,
            controller_ip=config_entry.data[CONF_IP_ADDRESS])
        )

    # for light in smart_plug.initialize_light_control(client, client.device_list()):
    #     _LOGGER.debug("Found light control: %s" % light.get_id)
    #     dev.append(SmartPlugSwitch(light, light.get_name, light.get_state,
    #                                light.get_powerConsumption, light.get_energyConsumption, client))

    # for camera in camera_eyes.initialize_camera_eyes(client, client.device_list()):
    #     _LOGGER.debug("Found camera eyes: %s" % camera.get_id)
    #     dev.append(CameraEyesSwitch(camera, camera.get_name,
    #                                 camera.get_light_state, client))

    # for scenario in client.scenario_list().items:
    #     _LOGGER.debug("Found scenario: %s" % scenario.get_id)
    #     dev.append(ScenarioSwitch(scenario, scenario.get_name, client))

    if device:
        async_add_entities(device)


class SmartPlugSwitch(SwitchDevice):
    def __init__(self, device: SHCSmartPlug, room_name: str, controller_ip: str):
        self._device = device
        self._room_name = room_name
        self._controller_ip = controller_ip

    async def async_added_to_hass(self):
        await super().async_added_to_hass()

        def on_state_changed():
            self.schedule_update_ha_state()

        for service in self._device.device_services:
            service.on_state_changed = on_state_changed

    async def async_will_remove_from_hass(self):
        await super().async_will_remove_from_hass()
        for service in self._device.device_services:
            service.on_state_changed = None

    @property
    def unique_id(self):
        """Return the unique ID of this switch."""
        return self._device.serial

    @property
    def device_id(self):
        """Return the ID of this switch."""
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
        """Return the state of the switch."""
        if self._device.state == SHCSmartPlug.PowerSwitchService.State.ON:
            return True
        elif self._device.state == SHCSmartPlug.PowerSwitchService.State.OFF:
            return False
        else:
            return None
    
    @property
    def today_energy_kwh(self):
        """Total energy usage in kWh."""
        return self._device.energyconsumption / 1000.
    
    @property
    def current_power_w(self):
        """The current power usage in W."""
        return self._device.powerconsumption
    
    def turn_on(self, **kwargs):
        """Turn the switch on."""
        self._device.set_state(True)

    def turn_off(self, **kwargs):
        """Turn the switch off."""
        self._device.set_state(False)
    
    def toggle(self, **kwargs):
        """Toggles the switch."""
        self._device.set_state(not self.is_on)
    
    def update(self, **kwargs):
        self._device.update()

    @property
    def state_attributes(self):
        state_attr = super().state_attributes
        if state_attr is None:
            state_attr = dict()
        state_attr["boschshc_room_name"] = self._room_name
        return state_attr


class CameraEyesSwitch(SwitchDevice):
    def __init__(self, device: SHCCameraEyes, room_name: str, controller_ip: str):
        self._device = device
        self._room_name = room_name
        self._controller_ip = controller_ip

    async def async_added_to_hass(self):
        await super().async_added_to_hass()

        def on_state_changed():
            self.schedule_update_ha_state()

        for service in self._device.device_services:
            service.on_state_changed = on_state_changed

    async def async_will_remove_from_hass(self):
        await super().async_will_remove_from_hass()
        for service in self._device.device_services:
            service.on_state_changed = None

    @property
    def unique_id(self):
        """Return the unique ID of this switch."""
        return self._device.serial

    @property
    def device_id(self):
        """Return the ID of this switch."""
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
        return True # No long polling implemented for camera eyes

    @property
    def available(self):
        """Return false if status is unavailable."""
        return True if self._device.status == "AVAILABLE" else False

    @property
    def is_on(self):
        """Return the state of the switch."""
        if self._device.lightstate == SHCCameraEyes.CameraLightService.State.ON:
            return True
        elif self._device.lightstate == SHCCameraEyes.CameraLightService.State.OFF:
            return False
        else:
            return None

    def turn_on(self, **kwargs):
        """Turn the switch on."""
        self._device.set_cameralight(True)

    def turn_off(self, **kwargs):
        """Turn the switch off."""
        self._device.set_cameralight(False)

    def toggle(self, **kwargs):
        """Toggles the switch."""
        self._device.set_cameralight(not self.is_on)

    def update(self, **kwargs):
        self._device.update()

    @property
    def state_attributes(self):
        state_attr = super().state_attributes
        if state_attr is None:
            state_attr = dict()
        state_attr["boschshc_room_name"] = self._room_name
        return state_attr



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

