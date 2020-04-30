"""Platform for switch integration."""
import logging

from boschshcpy import SHCCameraEyes, SHCSession, SHCSmartPlug

from homeassistant.components.switch import (
    DEVICE_CLASS_OUTLET,
    DEVICE_CLASS_SWITCH,
    SwitchEntity,
)
from homeassistant.const import CONF_IP_ADDRESS

from .const import DOMAIN
from .entity import SHCEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the sensor platform."""

    entities = []
    session: SHCSession = hass.data[DOMAIN][config_entry.entry_id]
    ip_address = config_entry.data[CONF_IP_ADDRESS]

    for device in session.device_helper.smart_plugs:
        _LOGGER.debug("Found smart plug: %s (%s)", device.name, device.id)
        room_name=session.room(device.room_id).name
        entities.append(SmartPlugSwitch(device=device, room_name=room_name, controller_ip=ip_address))

    for device in session.device_helper.light_controls:
        _LOGGER.debug("Found light controls: %s (%s)", device.name, device.id)
        room_name=session.room(device.room_id).name
        entities.append(SmartPlugSwitch(device=device, room_name=room_name, controller_ip=ip_address))

    for device in session.device_helper.camera_eyes:
        _LOGGER.debug("Found camera eyes: %s (%s)", device.name, device.id)
        room_name=session.room(device.room_id).name
        entities.append(CameraEyesSwitch(device=device, room_name=room_name, controller_ip=ip_address))

    if entities:
        async_add_entities(entities)


class SmartPlugSwitch(SHCEntity, SwitchEntity):
    """Representation of a smart plug switch."""

    @property
    def device_class(self):
        """Return the class of this device."""
        return (
            DEVICE_CLASS_OUTLET
            if self._device.device_model == "PSM"
            else DEVICE_CLASS_SWITCH
        )

    @property
    def is_on(self):
        """Returns if the switch is currently on or off."""
        if self._device.state == SHCSmartPlug.PowerSwitchService.State.ON:
            return True
        if self._device.state == SHCSmartPlug.PowerSwitchService.State.OFF:
            return False

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
        self._device.state = True

    def turn_off(self, **kwargs):
        """Turn the switch off."""
        self._device.state = False

    def toggle(self, **kwargs):
        """Toggles the switch."""
        self._device.state = not self.is_on


class CameraEyesSwitch(SHCEntity, SwitchEntity):
    """Representation of camera eyes as switch."""

    @property
    def should_poll(self):
        """Polling needed."""
        return True  # No long polling implemented for camera eyes

    @property
    def is_on(self):
        """Return the state of the switch."""
        if self._device.cameralight == SHCCameraEyes.CameraLightService.State.ON:
            return True
        if self._device.cameralight == SHCCameraEyes.CameraLightService.State.OFF:
            return False

        return None

    def turn_on(self, **kwargs):
        """Turn the switch on."""
        self._device.cameralight = True

    def turn_off(self, **kwargs):
        """Turn the switch off."""
        self._device.cameralight = False

    def toggle(self, **kwargs):
        """Toggles the switch."""
        self._device.cameralight = not self.is_on
