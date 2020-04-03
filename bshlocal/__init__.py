import bshlocal
import voluptuous as vol

from homeassistant import const
from homeassistant.helpers import config_validation as cv

DOMAIN = "bshlocal"

CONF_SSL_CERTIFICATE = "certificate"
CONF_SSL_KEY = "key"

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Required(const.CONF_IP_ADDRESS): cv.string,
                vol.Required(CONF_SSL_CERTIFICATE): cv.string,
                vol.Required(CONF_SSL_KEY): cv.string,
            }
        ),
    },
    extra=vol.ALLOW_EXTRA,
)


async def async_setup(hass, config):
    print(config)
    controller_ip = config[DOMAIN].get(const.CONF_IP_ADDRESS)
    certificate = config[DOMAIN].get(CONF_SSL_CERTIFICATE)
    key = config[DOMAIN].get(CONF_SSL_KEY)
    session = bshlocal.BSHLocalSession(
        controller_ip=controller_ip, certificate=certificate, key=key
    )

    hass.data[DOMAIN] = {"session": session}
    hass.helpers.discovery.load_platform("sensor", DOMAIN, {}, config)
    hass.helpers.discovery.load_platform("binary_sensor", DOMAIN, {}, config)
    hass.helpers.discovery.load_platform("climate", DOMAIN, {}, config)

    session.start_polling()

    return True


async def async_unload_entry(hass, entry):
    session = hass.data[DOMAIN]["session"]
    session.stop_polling()
