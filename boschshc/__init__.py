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
    from boschshcpy import SHCSession

    data = entry.data
    name = data[CONF_NAME]

    _LOGGER.debug("Connecting to Bosch Smart Home Controller API")

    session = await hass.async_add_executor_job(SHCSession, data[CONF_IP_ADDRESS], data[CONF_SSL_CERTIFICATE], data[CONF_SSL_KEY])

    shc_info = session.information
    if shc_info.version == "n/a":
        _LOGGER.error("Unable to connect to Bosch Smart Home Controller API")
        return False
    elif shc_info.updateState.name == "UPDATE_AVAILABLE":
        _LOGGER.warning('Please check for software updates in the Bosch Smart Home App')

    hass.data[DOMAIN][entry.entry_id] = session

    await hass.async_add_executor_job(session.start_polling)

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