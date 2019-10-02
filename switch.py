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
        dev.append(MySwitch(plug, plug.get_name, plug.get_binarystate))
    
    if dev:
        add_entities(dev, True)


class MySwitch(SwitchDevice):

    def __init__(self, plug, name, state):
        self._representation = plug
        self._is_on = state
        self._name = name
        
    @property
    def name(self):
        """Name of the device."""
        return self._name

    @property
    def is_on(self):
        """If the switch is currently on or off."""
        return self._is_on

    def turn_on(self, **kwargs):
        """Turn the switch on."""
        self._representation.set_binarystate(True)
        self._is_on = True
        _LOGGER.debug("New switch state is %s" % self._is_on)

    def turn_off(self, **kwargs):
        """Turn the switch off."""
        self._representation.set_binarystate(False)
        self._is_on = False
        _LOGGER.debug("New switch state is %s" % self._is_on)
    
    def toggle(self, **kwargs):
        """Toggles the switch."""
        self._representation.set_binarystate(not self._representation.get_binarystate())
        self._is_on = not self._is_on
        _LOGGER.debug("New switch state is %s" % self._is_on)
    