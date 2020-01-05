"""Example Load Platform integration."""

import logging
import voluptuous as vol
from homeassistant.helpers import config_validation as cv
DOMAIN = 'bshlocal'

CONF_SHC_IP = "shc_ip"
CONF_CERTIFICATE = "cert"
CONF_KEY = "key"

CONFIG_SCHEMA = vol.Schema({
    DOMAIN: vol.Schema({
        vol.Required(CONF_SHC_IP): cv.string,
        vol.Required(CONF_CERTIFICATE): cv.string,
        vol.Required(CONF_KEY): cv.string
    }),
}, extra=vol.ALLOW_EXTRA)

async def async_setup(hass, config):
    try:
        import bshlocal
        controller_ip = config[DOMAIN].get(CONF_SHC_IP)
        certificate = config[DOMAIN].get(CONF_CERTIFICATE)
        key = config[DOMAIN].get(CONF_KEY)
        session = bshlocal.BSHLocalSession(controller_ip=controller_ip, certificate=certificate, key=key)
                                                
    except Exception as ex:
        logging.getLogger("bshlocal").error(str(ex))
        return False
        
    hass.data[DOMAIN] = {"session": session}
    hass.helpers.discovery.load_platform('sensor', DOMAIN, {}, config)
    hass.helpers.discovery.load_platform('binary_sensor', DOMAIN, {}, config)
    hass.helpers.discovery.load_platform('climate', DOMAIN, {}, config)

    session.start_polling()
        
    return True

async def async_unload(self):
    session = hass.data[DOMAIN]["session"]
    print("Stopping polling")
    await session.stop_polling()
    print("DONE")
