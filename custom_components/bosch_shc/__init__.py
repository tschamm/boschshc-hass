"""The Bosch Smart Home Controller integration."""
import voluptuous as vol
import functools as ft
from boschshcpy import SHCSession, SHCUniversalSwitch
from boschshcpy.exceptions import SHCAuthenticationError, SHCConnectionError
from homeassistant.components.zeroconf import async_get_instance
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_DEVICE_ID,
    ATTR_NAME,
    ATTR_COMMAND,
    CONF_HOST,
    EVENT_HOMEASSISTANT_STOP,
    Platform,
)
from homeassistant.core import HomeAssistant

from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr

from .const import (
    ATTR_SERVICE_ID,
    CONF_SSL_CERTIFICATE,
    CONF_SSL_KEY,
    DATA_POLLING_HANDLER,
    DATA_SESSION,
    DATA_SHC,
    DOMAIN,
    LOGGER,
    SERVICE_TRIGGER_SCENARIO,
    SERVICE_TRIGGER_RAWSCAN,
)

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.COVER,
    Platform.EVENT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.CLIMATE,
    Platform.ALARM_CONTROL_PANEL,
    Platform.LIGHT,
    Platform.NUMBER,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
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
        raise ConfigEntryAuthFailed from err
    except SHCConnectionError as err:
        raise ConfigEntryNotReady from err

    shc_info = session.information
    if shc_info.updateState.name == "UPDATE_AVAILABLE":
        LOGGER.warning("Please check for software updates in the Bosch Smart Home App")

    hass.data.setdefault(DOMAIN, {})

    device_registry = dr.async_get(hass)
    device_entry = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        connections={(dr.CONNECTION_NETWORK_MAC, dr.format_mac(shc_info.unique_id))},
        identifiers={(DOMAIN, shc_info.unique_id)},
        manufacturer="Bosch",
        name=entry.title,
        model="SmartHomeController",
        sw_version=shc_info.version,
    )
    hass.data[DOMAIN][entry.entry_id] = {
        DATA_SESSION: session,
        DATA_SHC: device_entry,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def stop_polling(event):
        """Stop polling service."""
        await hass.async_add_executor_job(session.stop_polling)

    await hass.async_add_executor_job(session.start_polling)
    hass.data[DOMAIN][entry.entry_id][
        DATA_POLLING_HANDLER
    ] = hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, stop_polling)

    register_services(hass, entry)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    session: SHCSession = hass.data[DOMAIN][entry.entry_id][DATA_SESSION]

    hass.data[DOMAIN][entry.entry_id][DATA_POLLING_HANDLER]()
    hass.data[DOMAIN][entry.entry_id].pop(DATA_POLLING_HANDLER)
    await hass.async_add_executor_job(session.stop_polling)

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


def register_services(hass, entry):
    """Register services for the component."""
    SCENARIO_TRIGGER_SCHEMA = vol.Schema(
        {
            vol.Required(ATTR_NAME): vol.All(
                cv.string,
                vol.In(hass.data[DOMAIN][entry.entry_id][DATA_SESSION].scenario_names),
            )
        }
    )

    async def scenario_service_call(call):
        """SHC Scenario service call."""
        name = call.data[ATTR_NAME]
        for scenario in hass.data[DOMAIN][entry.entry_id][DATA_SESSION].scenarios:
            if scenario.name == name:
                hass.async_add_executor_job(scenario.trigger)

    hass.services.async_register(
        DOMAIN,
        SERVICE_TRIGGER_SCENARIO,
        scenario_service_call,
        SCENARIO_TRIGGER_SCHEMA,
    )

    RAWSCAN_TRIGGER_SCHEMA = vol.Schema(
        {
            vol.Required(ATTR_COMMAND): vol.All(
                cv.string,
                vol.In(
                    hass.data[DOMAIN][entry.entry_id][DATA_SESSION].rawscan_commands
                ),
            ),
            vol.Optional(ATTR_DEVICE_ID, default=""): cv.string,
            vol.Optional(ATTR_SERVICE_ID, default=""): cv.string,
        }
    )

    async def rawscan_service_call(call):
        """SHC Scenario service call."""
        # device_id = call.data[ATTR_DEVICE_ID]
        rawscan = await hass.async_add_executor_job(
            ft.partial(
                hass.data[DOMAIN][entry.entry_id][DATA_SESSION].rawscan,
                command=call.data[ATTR_COMMAND],
                device_id=call.data[ATTR_DEVICE_ID],
                service_id=call.data[ATTR_SERVICE_ID],
            )
        )
        LOGGER.info(rawscan)

    hass.services.async_register(
        DOMAIN,
        SERVICE_TRIGGER_RAWSCAN,
        rawscan_service_call,
        schema=RAWSCAN_TRIGGER_SCHEMA,
    )
