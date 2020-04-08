"""Platform for switch integration."""
import asyncio
import logging

from boschshcpy import (
    SHCCameraEyes,
    SHCDeviceHelper,
    SHCScenario,
    SHCSession,
    SHCSmartPlug,
)
from homeassistant.components.switch import SwitchDevice
from homeassistant.const import CONF_IP_ADDRESS, CONF_NAME
from homeassistant.util import slugify

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Set up the sensor platform."""

    device = []
    session: SHCSession = hass.data[DOMAIN][slugify(config[CONF_NAME])]

    for switch in session.device_helper.smart_plugs:
        _LOGGER.debug("Found smart plug: %s" % switch.id)
        device.append(
            SmartPlugSwitch(
                device=switch,
                room_name=session.room(switch.room_id).name,
                controller_ip=config[CONF_IP_ADDRESS],
            )
        )

    for light in session.device_helper.light_controls:
        _LOGGER.debug("Found light control: %s" % light.id)
        device.append(
            SmartPlugSwitch(
                device=light,
                room_name=session.room(light.room_id).name,
                controller_ip=config[CONF_IP_ADDRESS],
            )
        )

    for cameras in session.device_helper.camera_eyes:
        _LOGGER.debug("Found camera eyes: %s" % cameras.id)
        device.append(
            CameraEyesSwitch(
                device=cameras,
                room_name=session.room(cameras.room_id).name,
                controller_ip=config[CONF_IP_ADDRESS],
            )
        )

    for scenario in session.scenarios:
        _LOGGER.debug("Found scenario: %s" % scenario.id)
        device.append(
            ScenarioSwitch(device=scenario, controller_ip=config[CONF_IP_ADDRESS])
        )

    if device:
        return await async_add_entities(device)


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the sensor platform."""

    device = []
    session: SHCSession = hass.data[DOMAIN][slugify(config_entry.data[CONF_NAME])]

    for switch in session.device_helper.smart_plugs:
        _LOGGER.debug(f"Found smart plug: {switch.name} ({switch.id})")
        device.append(
            SmartPlugSwitch(
                device=switch,
                room_name=session.room(switch.room_id).name,
                controller_ip=config_entry.data[CONF_IP_ADDRESS],
            )
        )

    for light in session.device_helper.light_controls:
        _LOGGER.debug(f"Found light control: {light.name} ({light.id})")
        device.append(
            SmartPlugSwitch(
                device=light,
                room_name=session.room(light.room_id).name,
                controller_ip=config_entry.data[CONF_IP_ADDRESS],
            )
        )

    for camera in session.device_helper.camera_eyes:
        _LOGGER.debug(f"Found camera eyes: {camera.name} ({camera.id})")
        device.append(
            CameraEyesSwitch(
                device=camera,
                room_name=session.room(camera.room_id).name,
                controller_ip=config_entry.data[CONF_IP_ADDRESS],
            )
        )

    for scenario in session.scenarios:
        _LOGGER.debug(f"Found scenario: {scenario.name} ({scenario.id})")
        device.append(
            ScenarioSwitch(
                device=scenario, controller_ip=config_entry.data[CONF_IP_ADDRESS],
            )
        )

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
            "via_device": (DOMAIN, self._controller_ip),
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
        return self._device.energyconsumption / 1000.0

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
            "via_device": (DOMAIN, self._controller_ip),
        }

    @property
    def should_poll(self):
        """Polling needed."""
        return True  # No long polling implemented for camera eyes

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
    def __init__(self, device: SHCSmartPlug, controller_ip: str):
        self._device = device
        self._controller_ip = controller_ip

    @property
    def unique_id(self):
        """Return the unique ID of this switch."""
        return self._device.id

    @property
    def device_id(self):
        """Return the ID of this switch."""
        return self._device.id

    @property
    def name(self):
        """Name of the device."""
        return self._device.name

    @property
    def icon(self):
        return "mdi:script-text"

    @property
    def device_info(self):
        """Return the device info."""
        return {
            "identifiers": {(DOMAIN, self.device_id)},
            "name": self.name,
            "manufacturer": "BOSCH",
            "model": "SHC_Scenario",
            "sw_version": "",
            "via_device": (DOMAIN, self._controller_ip),
        }

    @property
    def should_poll(self):
        """Polling needed."""
        return False

    @property
    def is_on(self):
        """Return the state of the switch."""
        False

    def turn_on(self, **kwargs):
        """Turn the switch on."""
        self._device.trigger()

    def turn_off(self, **kwargs):
        """Turn the switch off."""
        pass

    def toggle(self, **kwargs):
        """Toggles the switch."""
        self._device.trigger()
