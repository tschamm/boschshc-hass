"""The Bosch Smart Home Controller integration."""

from datetime import timedelta

import voluptuous as vol
import functools as ft
import json
from boschshcpy import SHCSession, SHCUniversalSwitch
from boschshcpy.exceptions import (
    SHCAuthenticationError,
    SHCConnectionError,
)

from .certificate import parse_certificate
from .data import SHCData
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
    SupportsResponse,
    ServiceResponse,
    callback,
)
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    ServiceValidationError,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)

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
    DATA_CERT_CHECK_UNSUB,
    DATA_POLLING_HANDLER,
    DATA_SESSION,
    DATA_SHC,
    DATA_TITLE,
    DOMAIN,
    EVENT_BOSCH_SHC,
    LOGGER,
    OPT_PRESENCE_ENTITY,
    OPT_PRESENCE_STATE,
    SERVICE_TRIGGER_SCENARIO,
    SERVICE_TRIGGER_RAWSCAN,
    SUPPORTED_INPUTS_EVENTS_TYPES,
)

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
]
if hasattr(Platform, "VALVE"):
    PLATFORMS.append(Platform.VALVE)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the Bosch SHC component.

    Domain-level services (trigger_scenario, trigger_rawscan) are registered
    here so they exist even when a config entry fails to load, allowing HA to
    validate automations that reference them.  Entity services
    (smokedetector_check, smokedetector_alarmstate) are registered per-entry in
    their respective platform setup (binary_sensor.py) as allowed by the rule.
    """

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
        for config_entry in hass.config_entries.async_entries(DOMAIN):
            if not hasattr(config_entry, "runtime_data"):
                continue
            runtime: SHCData = config_entry.runtime_data
            if title in ("", runtime.title):
                for scenario in runtime.session.scenarios:
                    if scenario.name == name:
                        await hass.async_add_executor_job(scenario.trigger)

    hass.services.async_register(
        DOMAIN,
        SERVICE_TRIGGER_SCENARIO,
        scenario_service_call,
        SCENARIO_TRIGGER_SCHEMA,
    )

    RAWSCAN_TRIGGER_SCHEMA = vol.Schema(
        {
            vol.Optional(ATTR_TITLE, default=""): cv.string,
            vol.Required(ATTR_COMMAND): cv.string,
            vol.Optional(ATTR_DEVICE_ID, default=""): cv.string,
            vol.Optional(ATTR_SERVICE_ID, default=""): cv.string,
        }
    )

    async def rawscan_service_call(call: ServiceCall) -> ServiceResponse:
        """SHC Rawscan service call."""
        title = call.data[ATTR_TITLE]
        command = call.data[ATTR_COMMAND]
        for config_entry in hass.config_entries.async_entries(DOMAIN):
            if not hasattr(config_entry, "runtime_data"):
                continue
            runtime: SHCData = config_entry.runtime_data
            if title in ("", runtime.title):
                session = runtime.session
                # Runtime validation: confirm the command is valid for this session
                if command not in session.rawscan_commands:
                    raise ServiceValidationError(
                        f"Unknown rawscan command '{command}'. "
                        f"Valid commands: {sorted(session.rawscan_commands)}"
                    )
                rawscan = await hass.async_add_executor_job(
                    ft.partial(
                        session.rawscan,
                        command=command,
                        device_id=call.data[ATTR_DEVICE_ID],
                        service_id=call.data[ATTR_SERVICE_ID],
                    )
                )
                return {command: rawscan}

    hass.services.async_register(
        DOMAIN,
        SERVICE_TRIGGER_RAWSCAN,
        rawscan_service_call,
        schema=RAWSCAN_TRIGGER_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )

    return True


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
        LOGGER.warning("Unable to parse Bosch SHC certificate (%s): %s", cert_path, err)
        cert_info = None

    if cert_info is not None:
        if cert_info.days_remaining < 0:
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
    entry.runtime_data = SHCData(
        session=session,
        shc_device=device_entry,
        title=entry.title,
    )
    # Keep hass.data[DOMAIN] populated so legacy code paths (device_trigger,
    # diagnostics) that still read hass.data work during the transition.
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        DATA_SESSION: session,
        DATA_SHC: device_entry,
        DATA_TITLE: entry.title,
    }

    # Daily certificate re-check scheduling
    async def _scheduled_cert_check(_now):
        # async_track_time_interval dispatches sync callbacks to a worker
        # thread, where hass.async_create_task triggers HA 2026.x's escalated
        # report_non_thread_safe_operation RuntimeError for custom integrations.
        # Making the callback async makes async_track_time_interval schedule it
        # directly on the event loop, eliminating both the wrapper and the bug.
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

    entry.runtime_data.cert_check_unsub = async_track_time_interval(
        hass, _scheduled_cert_check, timedelta(days=1)
    )
    hass.data[DOMAIN][entry.entry_id][DATA_CERT_CHECK_UNSUB] = (
        entry.runtime_data.cert_check_unsub
    )

    # Presence-based child lock: optional; zero overhead when unconfigured.
    presence_entity = entry.options.get(OPT_PRESENCE_ENTITY, "")
    if presence_entity:
        present_state = entry.options.get(OPT_PRESENCE_STATE, "home")

        def _child_lock_devices(session):
            """Return (thermostat_devices, bool_devices) from this SHC session."""
            dh = session.device_helper
            thermostats = (
                dh.thermostats
                + dh.roomthermostats
                + [d for d in dh.wallthermostats if hasattr(d, "child_lock")]
            )
            bool_devices = (
                dh.micromodule_shutter_controls
                + dh.micromodule_blinds
                + dh.micromodule_light_attached
                + dh.micromodule_relays
                + dh.micromodule_impulse_relays
                + dh.micromodule_dimmers
                + dh.light_switches_bsm
            )
            return thermostats, bool_devices

        def _apply_child_lock(lock_state: bool):
            """Set child lock on all SHC devices (blocking; run in executor)."""
            from boschshcpy.exceptions import SHCException
            from boschshcpy.api import JSONRPCError
            thermostats, bool_devices = _child_lock_devices(session)
            for device in thermostats:
                try:
                    device.child_lock = lock_state
                except (JSONRPCError, SHCException) as err:
                    LOGGER.warning(
                        "Failed to set child_lock=%s on thermostat %s: %s",
                        lock_state, device.id, err,
                    )
            for device in bool_devices:
                try:
                    device.child_lock = lock_state
                except (JSONRPCError, SHCException) as err:
                    LOGGER.warning(
                        "Failed to set child_lock=%s on device %s: %s",
                        lock_state, device.id, err,
                    )

        @callback
        def _presence_state_changed(event):
            """Handle presence entity state changes."""
            old_state = event.data.get("old_state")
            new_state = event.data.get("new_state")
            if new_state is None:
                return
            old_state_str = old_state.state if old_state is not None else None
            new_state_str = new_state.state
            # Skip unavailable/unknown and no-op transitions
            if new_state_str in ("unavailable", "unknown"):
                return
            if old_state_str == new_state_str:
                return
            lock_on = new_state_str == present_state
            # Only act on actual transitions into / out of the present state
            was_present = old_state_str == present_state
            if lock_on == was_present:
                return
            hass.async_create_task(
                hass.async_add_executor_job(_apply_child_lock, lock_on)
            )

        entry.runtime_data.presence_unsub = async_track_state_change_event(
            hass, [presence_entity], _presence_state_changed
        )

    async def stop_polling(event):
        """Stop polling service."""
        await hass.async_add_executor_job(session.stop_polling)

    await hass.async_add_executor_job(session.start_polling)
    entry.runtime_data.polling_handler = hass.bus.async_listen_once(
        EVENT_HOMEASSISTANT_STOP, stop_polling
    )
    hass.data[DOMAIN][entry.entry_id][DATA_POLLING_HANDLER] = (
        entry.runtime_data.polling_handler
    )

    def _scenario_trigger(event_data):
        hass.loop.call_soon_threadsafe(
            hass.bus.fire,
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

    session.subscribe_scenario_callback("shc", _scenario_trigger)

    for switch_device in session.device_helper.universal_switches:
        event_listener = SwitchDeviceEventListener(hass, entry, switch_device)
        await event_listener.async_setup()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(async_update_options))

    return True


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Update options."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    runtime: SHCData = entry.runtime_data
    runtime.session.unsubscribe_scenario_callback("shc")

    if runtime.polling_handler is not None:
        runtime.polling_handler()
    if runtime.cert_check_unsub is not None:
        runtime.cert_check_unsub()
    if runtime.presence_unsub is not None:
        runtime.presence_unsub()
    await hass.async_add_executor_job(runtime.session.stop_polling)

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)

    return unload_ok


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
                break

        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, self._handle_ha_stop)

    def _input_events_handler(self):
        """Handle device input events (called from SHCPollingThread)."""
        if self._device.eventtype is None:
            return
        event_type = self._device.eventtype.name

        if event_type in SUPPORTED_INPUTS_EVENTS_TYPES:
            self.hass.loop.call_soon_threadsafe(
                self.hass.bus.fire,
                EVENT_BOSCH_SHC,
                {
                    ATTR_DEVICE_ID: self.device_id,
                    ATTR_ID: self._device.id,
                    ATTR_NAME: self._device.name,
                    ATTR_LAST_TIME_TRIGGERED: self._device.eventtimestamp,
                    ATTR_EVENT_SUBTYPE: self._device.keyname.name,
                    ATTR_EVENT_TYPE: event_type,
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
        if self._keypad_service is not None:
            self._keypad_service.subscribe_callback(
                self._device.id, self._input_events_handler
            )

    def shutdown(self):
        """Shutdown the listener."""
        self._keypad_service.unsubscribe_callback(self._device.id)

    @callback
    def _handle_ha_stop(self, _):
        """Handle Home Assistant stopping."""
        LOGGER.debug("Stopping Switch event listener for %s", self._device.name)
        self.shutdown()
