"""The Bosch Smart Home Controller integration."""
import asyncio
import logging

import voluptuous as vol

from homeassistant.const import (
    CONF_NAME,
    CONF_IP_ADDRESS,
)

from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.entity import Entity, async_generate_entity_id

from homeassistant.util import slugify

from .const import (
    DOMAIN,
    CONF_SSL_CERTIFICATE,
    CONF_SSL_KEY
)

ENTITY_ID_FORMAT = DOMAIN + ".{}"

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Required(CONF_NAME, default="Home"): cv.string,
                vol.Required(CONF_IP_ADDRESS): cv.string,
                vol.Required(CONF_SSL_CERTIFICATE): cv.isfile,
                vol.Required(CONF_SSL_KEY): cv.isfile,
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)

PLATFORMS = ["switch"]

_LOGGER = logging.getLogger(__name__)

async def async_setup(hass: HomeAssistant, config: dict):
    """Set up the Bosch SHC component."""
    hass.data.setdefault(DOMAIN, {})
    conf = config.get(DOMAIN)

    if not conf:
        return True

    hass.async_create_task(
        hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_IMPORT}, data=conf,
        )
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Set up Bosch SHC from a config entry."""
    data = entry.data
    name = data[CONF_NAME]

    bridge = SHCBridge(hass, name, data)
    if not bridge.login():
        return False

    bridge.entity_id = async_generate_entity_id(
        ENTITY_ID_FORMAT, name, None, hass)
    hass.data[DOMAIN][entry.entry_id] = bridge

    for component in PLATFORMS:
        hass.async_create_task(
            hass.config_entries.async_forward_entry_setup(entry, component)
        )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Unload a config entry."""
    unload_ok = all(
        await asyncio.gather(
            *[
                hass.config_entries.async_forward_entry_unload(
                    entry, component)
                for component in PLATFORMS
            ]
        )
    )
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


class SHCBridge(Entity):
    """Wrapper class for Bosch SHC bridge."""
    name = None

    def __init__(self, hass: HomeAssistant, name: str, domain_config: dict):
        """Initialize the session via Bosch SHC interface."""
        self.config = domain_config
        self.name = name
        self._hass = hass
        self.entity_id = None
        self.swversion = None
        self.session = None

    def login(self):
        """Login attempt to Bosch SHC."""
        from boschshcpy import SHCSession
        
        _LOGGER.debug("Connecting to Bosch Smart Home Controller API")

        try:
            self.session = SHCSession(
                self.config[CONF_IP_ADDRESS], self.config[CONF_SSL_CERTIFICATE], self.config[CONF_SSL_KEY]
            )
        except OSError as error:
            _LOGGER.error(
                "Could not read SSL certificate, key from %s, %s: %s",
                self.config[CONF_SSL_CERTIFICATE], self.config[CONF_SSL_KEY],
                error,
            )
            return False

        shc_info = self.session.information
        _LOGGER.debug('  version        : %s' % shc_info.version)
        _LOGGER.debug('  updateState    : %s' % shc_info.updateState.name)
        
        if shc_info.updateState.name == "NOT_INITIALIZED":
            _LOGGER.error(
                "Unable to connect to Bosch Smart Home Controller API")
            return False
        elif shc_info.updateState.name == "UPDATE_AVAILABLE":
            _LOGGER.warning(
                'Please check for software updates in the Bosch Smart Home App')
        self.swversion = shc_info.version

        return True
