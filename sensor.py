"""Platform for sensor integration."""
import logging

from homeassistant.const import TEMP_CELSIUS
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, SHC_LOGIN
SHC_BRIDGE = "shc_bridge"

_LOGGER = logging.getLogger(__name__)


def setup_platform(hass, config, add_entities, discovery_info=None):
    """Set up the sensor platform."""
    # We only want this platform to be set up via discovery.
    dev = []
    client = hass.data[SHC_BRIDGE]
    
    # # Setup connection with devices/cloud
    # hub = awesomelights.Hub(host, username, password)

    # # Verify that passed in configuration works
    # if not hub.is_valid_login():
    #     _LOGGER.error("Could not connect to AwesomeLight hub")
    #     return

    # # Add devices
    # add_entities(AwesomeLight(light) for light in hub.lights())
    
    _LOGGER.debug("Found %s sensors" % client.sensors())
    
    dev.append(ExampleSensor())
    if dev:
        add_entities(dev, True)


class ExampleSensor(Entity):
    """Representation of a Sensor."""

    def __init__(self):
        """Initialize the sensor."""
        self._state = None

    @property
    def name(self):
        """Return the name of the sensor."""
        return 'BoschSHC Example Temperature'

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._state

    @property
    def unit_of_measurement(self):
        """Return the unit of measurement."""
        return TEMP_CELSIUS

    def update(self):
        """Fetch new state data for the sensor.

        This is the only method that should fetch new data for Home Assistant.
        """
        self._state = self.hass.data[DOMAIN]['temperature']