"""Platform for switch integration."""
import logging

from homeassistant.components.switch import SwitchDevice
from BoschShcPy import smart_plug

from .const import DOMAIN, SHC_LOGIN
SHC_BRIDGE = "shc_bridge"

_LOGGER = logging.getLogger(__name__)


def setup_platform(hass, config, add_entities, discovery_info=None):
    """Set up the sensor platform."""
    # We only want this platform to be set up via discovery.
    dev = []
    client = hass.data[SHC_BRIDGE]
        
    for plug in smart_plug.initialize_smart_plugs(client, client.device_list()):
        _LOGGER.debug("Found smart plug: %s" % plug.get_id)
        dev.append(SmartPlugSwitch(plug, plug.get_name, plug.get_state, plug.get_powerConsumption, plug.get_energyConsumption, client))
    
    if dev:
        add_entities(dev, True)


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
    def name(self):
        """Name of the device."""
        return self._name

    @property
    def available(self):
        """Return False if state has not been updated yet."""
#         _LOGGER.debug("Switch available: %s" % self._representation.get_availability)
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
            self._today_energy_kwh = self._representation.get_energyConsumption
            self._current_power_w = self._representation.get_powerConsumption
            self._name = self._representation.get_name
        