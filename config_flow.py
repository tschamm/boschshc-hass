"""Config flow to configure Bosch Smart Home Interface."""

from typing import Set
from homeassistant.helpers import aiohttp_client
from homeassistant.helpers.typing import HomeAssistantType
import voluptuous as vol
import homeassistant.helpers.config_validation as cv
import asyncio
import json
import os

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.util import slugify

from homeassistant.const import (
    CONF_NAME,
    CONF_IP_ADDRESS,
    CONF_PORT,
    CONF_ICON,
)

from .const import (
    DOMAIN,
    CONF_ACCESS_CERT,
    CONF_ACCESS_KEY,
    LOGGER
)
from .errors import AuthenticationRequired, CannotConnect

SHC_MANUFACTURERURL = "http://www.bosch.com"


@callback
def configured_hosts(hass):
    """Return a set of the configured hosts."""
    return set(
        entry.data[CONF_IP_ADDRESS] for entry in hass.config_entries.async_entries(DOMAIN)
    )


@callback
def configured_bridges(hass: HomeAssistantType) -> Set[str]:
    """Return a set of the configured bridges."""
    return set(
        (entry.data[CONF_IP_ADDRESS])
        for entry in (
            hass.config_entries.async_entries(
                DOMAIN) if hass.config_entries else []
        )
    )


@config_entries.HANDLERS.register(DOMAIN)
class ShcFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a Shc config flow."""

    VERSION = 1

    def __init__(self):
        """Initialize the Shc flow."""
        self.host = None

    async def async_step_user(self, user_input=None):
        """Handle a flow initialized by the user."""
        return await self.async_step_init(user_input)

    async def async_step_init(self, user_input=None):
        """Handle a flow start."""
        if user_input is not None:
            self.host = self.context[CONF_IP_ADDRESS] = user_input[CONF_IP_ADDRESS]
            self.user_input = user_input
            return await self.async_step_link()

        errors = {}

        # if user_input is not None:
        #     host = user_input[CONF_IP_ADDRESS]
        #     if host not in configured_bridges(self.hass):
        #         return self.async_create_entry(
        #             title=user_input[CONF_IP_ADDRESS],
        #             data=user_input
        #         )
        #     errors["base"] = "host_exists"

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_NAME, default='SHC'): str,
                    vol.Required(CONF_IP_ADDRESS): str,
                    vol.Required(CONF_ACCESS_CERT): str,
                    vol.Required(CONF_ACCESS_KEY): str,
                    vol.Required(CONF_PORT, default='8444'): str,
                    vol.Optional(CONF_ICON): str,
                }
            ),
            errors=errors,
        )



    #     # # websession = aiohttp_client.async_get_clientsession(self.hass)

    #     # # try:
    #     # #     with async_timeout.timeout(5):
    #     # #         bridges = await discover_nupnp(websession=websession)
    #     # # except asyncio.TimeoutError:
    #     # #     return self.async_abort(reason="discover_timeout")

    #     # # if not bridges:
    #     # #     return self.async_abort(reason="no_bridges")

    #     # # Find already configured hosts
    #     # configured = configured_hosts(self.hass)

    #     # hosts = [bridge.host for bridge in bridges if bridge.host not in configured]

    #     # if not hosts:
    #     #     return self.async_abort(reason="all_configured")

    #     # if len(hosts) == 1:
    #     #     self.host = hosts[0]
    #     #     return await self.async_step_link()

    #     # return self.async_show_form(
    #     #     step_id="init",
    #     #     data_schema=vol.Schema({vol.Required("host"): vol.In(hosts)}),
    #     # )

    async def async_step_link(self, user_input=None):
        """Attempt to link with the Shc bridge.

        Given a configured host, will ask the user to press the link button
        to connect to the bridge.
        """
        errors = {}

        # We will always try linking in case the user has already pressed
        # the link button.
        try:
            # bridge = await get_bridge(self.hass, self.host, username=None)

            if self.host not in configured_bridges(self.hass):
                return await self._entry_from_input()
            errors["base"] = "host_exists"

            # return await self._entry_from_bridge(bridge)
        except AuthenticationRequired:
            errors["base"] = "register_failed"

        except CannotConnect:
            LOGGER.error("Error connecting to the Shc bridge at %s", self.host)
            errors["base"] = "linking"

        except Exception:  # pylint: disable=broad-except
            LOGGER.exception(
                "Unknown error connecting with Shc bridge at %s", self.host
            )
            errors["base"] = "linking"

        # If there was no user input, do not show the errors.
        if user_input is None:
            errors = {}

        return self.async_show_form(step_id="link", errors=errors)


    # async def _entry_from_bridge(self, bridge):
    #     """Return a config entry from an initialized bridge."""
    #     # Remove all other entries of hubs with same ID or host
    #     host = bridge.host
    #     bridge_id = bridge.config.bridgeid

    #     same_hub_entries = [
    #         entry.entry_id
    #         for entry in self.hass.config_entries.async_entries(DOMAIN)
    #         if entry.data["bridge_id"] == bridge_id or entry.data["host"] == host
    #     ]

    #     if same_hub_entries:
    #         await asyncio.wait(
    #             [
    #                 self.hass.config_entries.async_remove(entry_id)
    #                 for entry_id in same_hub_entries
    #             ]
    #         )

    #     return self.async_create_entry(
    #         title=bridge.config.name,
    #         data={"host": host, "bridge_id": bridge_id, "username": bridge.username},
    #     )

    async def _entry_from_input(self):
        """Return a config entry from an initialized bridge."""
        return self.async_create_entry(
            title=self.user_input[CONF_NAME],
            data=self.user_input
        )
