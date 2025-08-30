"""The Bosch Smart Home Controller integration."""

import voluptuous as vol
import functools as ft
from boschshcpy import SHCSession, SHCUniversalSwitch
from boschshcpy.exceptions import SHCAuthenticationError, SHCConnectionError
from homeassistant.components.zeroconf import async_get_instance
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_DEVICE_ID,
    ATTR_ID,
    ATTR_NAME,
    ATTR_COMMAND,
    CONF_HOST,
    EVENT_HOMEASSISTANT_STOP,
    Platform,
)
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    callback,
)
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.event import async_track_time_interval

from .const import (
    ATTR_EVENT_SUBTYPE,
    ATTR_EVENT_TYPE,
    ATTR_LAST_TIME_TRIGGERED,
    ATTR_SERVICE_ID,
    ATTR_TITLE,
    CERT_EXPIRY_WARNING_DAYS,
    CONF_SSL_CERTIFICATE,
    CONF_SSL_KEY,
    DOMAIN_NOTIFICATION_ID,
    DATA_POLLING_HANDLER,
    DATA_SESSION,
    DATA_SHC,
    DATA_TITLE,
    DOMAIN,
    EVENT_BOSCH_SHC,
    LOGGER,
    SERVICE_TRIGGER_SCENARIO,
    SERVICE_TRIGGER_RAWSCAN,
    SUPPORTED_INPUTS_EVENTS_TYPES,
)
from .certificate import parse_certificate

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.COVER,
    Platform.EVENT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.CLIMATE,
    Platform.ALARM_CONTROL_PANEL,
    Platform.LIGHT,
    Platform.NUMBER,
    Platform.VALVE,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Bosch SHC from a config entry."""
    data = entry.data

    # Pre-flight certificate validity check for clearer user feedback
    cert_path = data.get(CONF_SSL_CERTIFICATE, "")
    try:
        cert_info = (
            await hass.async_add_executor_job(parse_certificate, cert_path)
            if cert_path
            else None
        )
    except Exception as err:  # broad: parsing issues shouldn't fully block reauth paths
        LOGGER.warning(
            "Unable to parse Bosch SHC certificate (%s): %s", cert_path, err
        )
        cert_info = None

    if cert_info is not None:
        if cert_info.days_remaining < 0:
            from homeassistant.exceptions import ConfigEntryAuthFailed

            expiry = cert_info.not_after.date()
            LOGGER.error(
                "Bosch SHC client certificate expired on %s. Reconfigure integration (put controller in pairing mode and re-authenticate).",
                expiry,
            )
            raise ConfigEntryAuthFailed(
                f"Client certificate expired on {expiry}. Reconfigure the integration."
            )
        if cert_info.days_remaining <= CERT_EXPIRY_WARNING_DAYS:
            expiry = cert_info.not_after.date()
            LOGGER.warning(
                "Bosch SHC client certificate will expire in %d days (on %s). Put controller in pairing mode and reconfigure integration to renew.",
                cert_info.days_remaining,
                expiry,
            )
            hass.components.persistent_notification.create(
                (
                    f"Bosch SHC client certificate will expire in {cert_info.days_remaining} days (on {expiry}).\n"
                    "To renew: Put the controller into pairing mode (press front button until LEDs flash) and start re-authentication from the integration options."
                ),
                title="Bosch SHC certificate expiring",
                notification_id=DOMAIN_NOTIFICATION_ID,
            )

    zeroconf = await async_get_instance(hass)
    try:
        session: SHCSession = await hass.async_add_executor_job(
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
    device_id = device_entry.id
    hass.data[DOMAIN][entry.entry_id] = {
        DATA_SESSION: session,
        DATA_SHC: device_entry,
        DATA_TITLE: entry.title,
    }

    # Daily certificate re-check scheduling
    from datetime import timedelta
    from .const import DATA_CERT_CHECK_UNSUB

    def _scheduled_cert_check(_now):
        async def _run():
            try:
                info = await hass.async_add_executor_job(parse_certificate, cert_path)
            except Exception:  # silently ignore parsing issues
                return
            if info.days_remaining < 0:
                LOGGER.error(
                    "Bosch SHC client certificate expired on %s (daily check). Triggering reload for re-auth.",
                    info.not_after.date(),
                )
                hass.async_create_task(hass.config_entries.async_reload(entry.entry_id))
            elif info.days_remaining <= CERT_EXPIRY_WARNING_DAYS:
                expiry = info.not_after.date()
                hass.components.persistent_notification.create(
                    (
                        f"Bosch SHC client certificate will expire in {info.days_remaining} days (on {expiry}).\n"
                        "To renew: Put the controller into pairing mode and re-authenticate the integration."
                    ),
                    title="Bosch SHC certificate expiring",
                    notification_id=DOMAIN_NOTIFICATION_ID,
                )

        hass.async_create_task(_run())

    hass.data[DOMAIN][entry.entry_id][DATA_CERT_CHECK_UNSUB] = async_track_time_interval(
        hass, _scheduled_cert_check, timedelta(days=1)
    )

    async def stop_polling(event):
        """Stop polling service."""
        await hass.async_add_executor_job(session.stop_polling)

    await hass.async_add_executor_job(session.start_polling)
    hass.data[DOMAIN][entry.entry_id][DATA_POLLING_HANDLER] = (
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, stop_polling)
    )

    def _scenario_trigger(event_data):
        hass.bus.fire(
            EVENT_BOSCH_SHC,
            {
                ATTR_DEVICE_ID: device_id,
                ATTR_ID: event_data["id"],
                ATTR_NAME: shc_info.name,
                ATTR_LAST_TIME_TRIGGERED: event_data["lastTimeTriggered"],
                ATTR_EVENT_TYPE: "SCENARIO",
                ATTR_EVENT_SUBTYPE: event_data["name"],
            },
        )

    for scenario in hass.data[DOMAIN][entry.entry_id][DATA_SESSION].scenarios:
        session.subscribe_scenario_callback("shc", _scenario_trigger)

    for switch_device in session.device_helper.universal_switches:
        event_listener = SwitchDeviceEventListener(hass, entry, switch_device)
        await event_listener.async_setup()

    register_services(hass, entry)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(async_update_options))

    return True


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Update options."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    session: SHCSession = hass.data[DOMAIN][entry.entry_id][DATA_SESSION]
    session.unsubscribe_scenario_callback("shc")

    hass.data[DOMAIN][entry.entry_id][DATA_POLLING_HANDLER]()
    # cancel daily cert check
    from .const import DATA_CERT_CHECK_UNSUB
    unsub = hass.data[DOMAIN][entry.entry_id].pop(DATA_CERT_CHECK_UNSUB, None)
    if unsub:
        unsub()
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
            vol.Optional(ATTR_TITLE, default=""): cv.string,
            vol.Required(ATTR_NAME): cv.string,
        }
    )

    async def scenario_service_call(call: ServiceCall) -> None:
        """SHC Scenario service call."""
        name = call.data[ATTR_NAME]
        title = call.data[ATTR_TITLE]
        for controller_data in hass.data[DOMAIN].values():
            if title in ("", controller_data[DATA_TITLE]):
                session = controller_data[DATA_SESSION]
                if isinstance(session, SHCSession):
                    for scenario in session.scenarios:
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
            vol.Optional(ATTR_TITLE, default=""): cv.string,
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
        title = call.data[ATTR_TITLE]
        for controller_data in hass.data[DOMAIN].values():
            if title in ("", controller_data[DATA_TITLE]):
                session = controller_data[DATA_SESSION]
                if isinstance(session, SHCSession):
                    rawscan = await hass.async_add_executor_job(
                        ft.partial(
                            session.rawscan,
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


class SwitchDeviceEventListener:
    """Event listener for a Switch device."""

    def __init__(self, hass, entry, device: SHCUniversalSwitch):
        """Initialize the Switch device event listener."""
        self.hass = hass
        self.entry = entry
        self._device = device
        self._keypad_service = None
        self.device_id = None

        for service in self._device.device_services:
            if service.id == "Keypad":
                self._keypad_service = service
                self._keypad_service.subscribe_callback(
                    self._device.id, self._input_events_handler
                )
                break

        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, self._handle_ha_stop)

    def _input_events_handler(self):
        """Handle device input events."""
        event_type = self._device.eventtype.name

        if event_type in SUPPORTED_INPUTS_EVENTS_TYPES:
            self.hass.bus.fire(
                EVENT_BOSCH_SHC,
                {
                    ATTR_DEVICE_ID: self.device_id,
                    ATTR_ID: self._device.id,
                    ATTR_NAME: self._device.name,
                    ATTR_LAST_TIME_TRIGGERED: self._device.eventtimestamp,
                    ATTR_EVENT_SUBTYPE: self._device.keyname.name,
                    ATTR_EVENT_TYPE: self._device.eventtype.name,
                },
            )
        else:
            LOGGER.warning(
                "Switch input event %s for device %s is not supported, please open issue",
                event_type,
                self._device.name,
            )

    async def async_setup(self):
        """Set up the listener."""
        device_registry = dr.async_get(self.hass)
        device_entry = device_registry.async_get_or_create(
            config_entry_id=self.entry.entry_id,
            name=self._device.name,
            identifiers={(DOMAIN, self._device.id)},
            manufacturer=self._device.manufacturer,
            model=self._device.device_model,
            via_device=(DOMAIN, self._device.root_device_id),
        )
        self.device_id = device_entry.id

    def shutdown(self):
        """Shutdown the listener."""
        self._keypad_service.unsubscribe_callback(self._device.id)

    @callback
    def _handle_ha_stop(self, _):
        """Handle Home Assistant stopping."""
        LOGGER.debug("Stopping Switch event listener for %s", self._device.name)
        self.shutdown()
