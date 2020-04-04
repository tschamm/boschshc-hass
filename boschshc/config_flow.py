"""Config flow for Bosch Smart Home Controller integration."""
import logging

import voluptuous as vol

from homeassistant.const import (
    CONF_NAME,
    CONF_IP_ADDRESS,
    CONF_ICON,
)

from homeassistant import config_entries, core, exceptions

from .const import (
    DOMAIN,
    CONF_SSL_CERTIFICATE,
    CONF_SSL_KEY
)  # pylint:disable=unused-import

_LOGGER = logging.getLogger(__name__)

DATA_SCHEMA=vol.Schema(
    {
        vol.Required(CONF_NAME, default='Home'): str,
        vol.Required(CONF_IP_ADDRESS): str,
        vol.Required(CONF_SSL_CERTIFICATE): str,
        vol.Required(CONF_SSL_KEY): str,
        vol.Optional(CONF_ICON): str,
    }
)

class Session:
    """Session class to make tests pass.

    """
    def __init__(self, host):
        """Initialize."""
        self.host = host

    async def authenticate(self, ssl_certificate, ssl_key) -> bool:
        """Test if we can authenticate with the host."""

        return True

    def validate_auth(self, ssl_certificate, ssl_key) -> bool:
        """Test if we can authenticate with the host."""
        from boschshcpy import SHCSession

        session = SHCSession(self.host, ssl_certificate, ssl_key, False)
        if session.information.version == "n/a":
            return False

        return True        

async def validate_input(hass: core.HomeAssistant, data):
    """Validate the user input allows us to connect.

    Data has the keys from DATA_SCHEMA with values provided by the user.
    """
    session = Session(data[CONF_IP_ADDRESS])

    if not await hass.async_add_executor_job(
        session.validate_auth, data[CONF_SSL_CERTIFICATE], data[CONF_SSL_KEY]
    ):
        raise InvalidAuth

    # if not await session.authenticate(data[CONF_SSL_CERTIFICATE], data[CONF_SSL_KEY]):
    #     raise InvalidAuth

    # If you cannot connect:
    # throw CannotConnect
    # If the authentication is wrong:
    # InvalidAuth

    # Return info that you want to store in the config entry.
    return {"title": data[CONF_NAME]}

class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Bosch SHC."""

    VERSION = 1
    # TODO pick one of the available connection classes in homeassistant/config_entries.py
    CONNECTION_CLASS = config_entries.CONN_CLASS_UNKNOWN

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors = {}
        if user_input is not None:
            try:
                info = await validate_input(self.hass, user_input)

                return self.async_create_entry(title=info["title"], data=user_input)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user", data_schema=DATA_SCHEMA, errors=errors
        )

    async def async_step_import(self, user_input):
        """Handle import."""
        await self.async_set_unique_id(user_input[CONF_IP_ADDRESS])
        self._abort_if_unique_id_configured()

        return await self.async_step_user(user_input)


class CannotConnect(exceptions.HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(exceptions.HomeAssistantError):
    """Error to indicate there is invalid auth."""