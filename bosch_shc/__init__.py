"""The Bosch Smart Home Controller integration."""
import asyncio
import logging

import voluptuous as vol
from boschshcpy import SHCSession
from boschshcpy.exceptions import SHCAuthenticationError, SHCConnectionError, SHCmDNSError
from homeassistant.components.zeroconf import async_get_instance
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, EVENT_HOMEASSISTANT_STOP, ATTR_ID, ATTR_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr

from .const import (
    ATTR_NAME, ATTR_LAST_TIME_TRIGGERED, CONF_SSL_CERTIFICATE, CONF_SSL_KEY, DOMAIN, EVENT_BOSCH_SHC_SCENARIO_TRIGGER, SERVICE_TRIGGER_SCENARIO
)

PLATFORMS = [
    "binary_sensor",
    "cover",
    "switch",
    "sensor",
    "climate",
    "alarm_control_panel",
    "light",
]

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: dict):
    """Set up the Bosch SHC component."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Set up Bosch SHC from a config entry."""
    data = entry.data

    zeroconf = await async_get_instance(hass)
    try:
        session = await hass.async_add_executor_job(
            SHCSession,
            data[CONF_HOST],
            data[CONF_SSL_CERTIFICATE],
            data[CONF_SSL_KEY],
            False,
            zeroconf,
        )
    except SHCAuthenticationError as err:
        _LOGGER.warning("Unable to authenticate on Bosch Smart Home Controller API")
        raise ConfigEntryNotReady from err
    except (SHCConnectionError, SHCmDNSError) as err:
        raise ConfigEntryNotReady from err

    shc_info = session.information
    if shc_info.updateState.name == "UPDATE_AVAILABLE":
        _LOGGER.warning("Please check for software updates in the Bosch Smart Home App")

    hass.data[DOMAIN][entry.entry_id] = session

    device_registry = await dr.async_get_registry(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        connections={(dr.CONNECTION_NETWORK_MAC, dr.format_mac(shc_info.mac_address))},
        identifiers={(DOMAIN, shc_info.name)},
        manufacturer="Bosch",
        name=entry.title,
        model="SmartHomeController",
        sw_version=shc_info.version,
    )

    for component in PLATFORMS:
        hass.async_create_task(hass.config_entries.async_forward_entry_setup(entry, component))

    async def stop_polling(event):
        """Stop polling service."""
        await hass.async_add_executor_job(session.stop_polling)

    await hass.async_add_executor_job(session.start_polling)
    session.reset_connection_listener = hass.bus.async_listen_once(
        EVENT_HOMEASSISTANT_STOP, stop_polling
    )

    @callback
    def _async_scenario_trigger(scenario_id, name, last_time_triggered):
        hass.bus.async_fire(
            EVENT_BOSCH_SHC_SCENARIO_TRIGGER,
            {
                ATTR_ID: scenario_id,
                ATTR_NAME: name,
                ATTR_LAST_TIME_TRIGGERED: last_time_triggered
            }
        )

    session.subscribe_scenario_callback(_async_scenario_trigger)

    register_services(hass, entry)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Unload a config entry."""
    session: SHCSession = hass.data[DOMAIN][entry.entry_id]
    session.unsubscribe_scenario_callback()

    if session.reset_connection_listener is not None:
        session.reset_connection_listener()
        session.reset_connection_listener = None
        await hass.async_add_executor_job(session.stop_polling)

    unload_ok = all(
        await asyncio.gather(
            *[
                hass.config_entries.async_forward_entry_unload(entry, component)
                for component in PLATFORMS
            ]
        )
    )
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


def register_services(hass, entry):
    """Register services for the component."""
    service_scenario_trigger_schema = vol.Schema(
        {
            vol.Required(ATTR_NAME): vol.All(
                cv.string, vol.In(hass.data[DOMAIN][entry.entry_id].scenario_names)
            )
        }
    )

    async def scenario_service_call(call):
        """SHC Scenario service call."""
        name = call.data[ATTR_NAME]
        for scenario in hass.data[DOMAIN][entry.entry_id].scenarios:
            if scenario.name == name:
                hass.async_add_executor_job(scenario.trigger)

    hass.services.async_register(
        DOMAIN,
        SERVICE_TRIGGER_SCENARIO,
        scenario_service_call,
        service_scenario_trigger_schema,
    )
