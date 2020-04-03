"""Support for the Bosch Smart Home Controller system."""
import logging
from urllib.error import HTTPError

import voluptuous as vol

from BoschShcPy.shc_information import state

from homeassistant.const import (
    CONF_NAME,
    CONF_IP_ADDRESS,
    CONF_PORT,
    EVENT_HOMEASSISTANT_START, 
    EVENT_HOMEASSISTANT_STOP,
)

from homeassistant.helpers import discovery
from homeassistant import config_entries

from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.entity import Entity, async_generate_entity_id
from homeassistant.helpers import config_per_platform
from homeassistant.util import slugify

from .const import (
    DOMAIN,
    CONF_ACCESS_CERT,
    CONF_ACCESS_KEY,
)

ENTITY_ID_FORMAT = "shc_bridge.{}"
PLATFORMS = "switch", "binary_sensor", "cover", "alarm_control_panel"

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Required(CONF_NAME, default="SHC"): cv.string,
                vol.Required(CONF_IP_ADDRESS): cv.string,
                vol.Required(CONF_ACCESS_CERT): cv.string,
                vol.Required(CONF_ACCESS_KEY): cv.string,
                vol.Required(CONF_PORT, default='8444'): cv.string,
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)

async def async_setup(hass, config):
    """Set up the Bosch SHC bridge"""
    hass.data[DOMAIN] = {}
    entities: Set[str] = set()

    from BoschShcPy import Client
    _LOGGER.debug("Initializing Bosch SHC (via config)")

    for _, entry in config_per_platform(config, DOMAIN):
        name = entry[CONF_NAME]
        bridge = SHCBridge(hass, name, entry, Client)
        if not bridge.login():
            _LOGGER.debug("Failed to login to Bosch Smart Home Controller")
            return False

        _LOGGER.debug("Login to Bosch Smart Home Controller")
        hass.data[DOMAIN][slugify(name)] = bridge

    return True

async def async_setup_entry(hass, config_entry):
    entry = config_entry.data
    name = entry[CONF_NAME]

    from BoschShcPy import Client
    _LOGGER.debug("Initializing Bosch SHC bridge (via config entry)")

    bridge = SHCBridge(hass, name, entry, Client)
    if not bridge.login():
        _LOGGER.debug("Failed to login to Bosch Smart Home Controller")
        return False

    bridge.entity_id = async_generate_entity_id(
        ENTITY_ID_FORMAT, name, None, hass)
    hass.data[DOMAIN][slugify(name)] = bridge

    device_registry = await dr.async_get_registry(hass)
    device_registry.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        # connections={(dr.CONNECTION_NETWORK_MAC, entry[CONF_MAC])},
        # identifiers={(DOMAIN, entry[CONF_MAC])},
        identifiers={(DOMAIN, entry[CONF_IP_ADDRESS])},
        manufacturer="Bosch",
        name=name,
        model="SmartHomeController",
        sw_version=bridge.swversion
    )

    # Use `hass.async_add_job` to avoid a circular dependency between the platform and the component
    for domain in PLATFORMS:
        hass.async_add_job(
            hass.config_entries.async_forward_entry_setup(config_entry, domain)
        )

    # hass.async_add_job(
    #     hass.config_entries.async_forward_entry_setup(config_entry, 'switch'))

    async def stop_subscription_service(event):
        """Stop the subscription service of the bridge."""
        bridge.my_client.stop_subscription()

    async def start_subscription_service(event):
        """Start the subscription service of the bridge."""
        bridge.my_client.start_subscription()
        hass.bus.async_listen_once(
            EVENT_HOMEASSISTANT_STOP, stop_subscription_service
        )

    hass.bus.async_listen_once(
        EVENT_HOMEASSISTANT_START, start_subscription_service)

    return True

class SHCBridge(Entity):
    """A Bosch SHC wrapper class."""

    name = None

    def __init__(self, hass, name, domain_config, client):
        """Initialize the Bosch Smart Home Interface."""
        self.config = domain_config
        self.name = name
        self._hass = hass
        self.entity_id = None;
        self.swversion = None;

        self.my_client = client(
            domain_config[CONF_IP_ADDRESS], domain_config[CONF_PORT], domain_config[CONF_ACCESS_CERT], domain_config[CONF_ACCESS_KEY]
        )

    def login(self):
        """Login to SHC."""
        from BoschShcPy import ErrorException
        try:
            _LOGGER.debug("Trying to connect to Bosch Smart Home Interface API")

            shc_info = self.my_client.shc_information()
            _LOGGER.debug('  version        : %s' % shc_info.version)
            _LOGGER.debug('  updateState    : %s' % shc_info.updateState)
            if shc_info.get_state() == state.NOT_INITIALIZED:
                _LOGGER.error(
                    "Unable to connect to Bosch Smart Home Interface API")
                return False
            elif shc_info.get_state() == state.UPDATE_AVAILABLE:
                _LOGGER.warning('Please check for software updates of the bridge in the Bosch Smart Home App')
            self.swversion = shc_info.version

            # self.my_client.start_subscription()
            return True

        except (HTTPError, ErrorException) as e:
            _LOGGER.error("Unable to connect to Bosch Smart Home Interface API")
            return False
