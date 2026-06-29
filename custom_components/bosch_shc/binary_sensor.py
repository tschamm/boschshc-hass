"""Platform for binarysensor integration."""

from __future__ import annotations

import contextlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from boschshcpy import (
    AlarmService,
    BatteryLevelService,
    SHCDevice,
    SHCMotionDetector2,
    SHCSession,
    SHCShutterContact,
    SHCShutterContact2Plus,
    SHCSmokeDetectionSystem,
    SHCSmokeDetector,
    ShutterContactService,
    SurveillanceAlarmService,
    VibrationSensorService,
    WaterLeakageSensorService,
)
from boschshcpy.exceptions import SHCConnectionError, SHCException
from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_COMMAND,
    ATTR_DEVICE_ID,
    ATTR_ID,
    ATTR_NAME,
    EVENT_HOMEASSISTANT_STOP,
    Platform,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    ATTR_EVENT_SUBTYPE,
    ATTR_EVENT_TYPE,
    ATTR_LAST_TIME_TRIGGERED,
    DATA_SESSION,
    DOMAIN,
    EVENT_BOSCH_SHC,
    LOGGER,
    SERVICE_SMOKEDETECTOR_ALARMSTATE,
    SERVICE_SMOKEDETECTOR_CHECK,
)
from .entity import (
    SHCEntity,
    async_get_device_id,
    async_migrate_to_new_unique_id,
    device_excluded,
)

PARALLEL_UPDATES = 1


async def async_setup_entry(  # noqa: C901
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the SHC binary sensor platform."""
    entities = []
    session: SHCSession = hass.data[DOMAIN][config_entry.entry_id][DATA_SESSION]

    @callback  # type: ignore[untyped-decorator]
    def async_add_shuttercontact(
        device: SHCShutterContact,
    ) -> None:
        """Add Shutter Contact 2 Binary Sensor."""
        binary_sensor = ShutterContactSensor(
            device=device,
            entry_id=config_entry.entry_id,
        )
        async_add_entities([binary_sensor])

    for binary_sensor in list(session.device_helper.shutter_contacts) + list(
        session.device_helper.shutter_contacts2
    ):
        if device_excluded(binary_sensor, config_entry.options):
            continue
        await async_migrate_to_new_unique_id(
            hass, Platform.BINARY_SENSOR, device=binary_sensor
        )
        async_add_shuttercontact(device=binary_sensor)

    # Register listener for new binary sensors and ensure it is torn down on
    # config entry unload.  session.subscribe() appends the tuple to
    # session._subscribers but returns None, so we build the unsubscribe
    # closure ourselves.  add_update_listener is NOT used here because it
    # expects an options-update callback (hass, entry) -> None, not the SHC
    # subscriber tuple.
    _shutter_subscriber = (SHCShutterContact, async_add_shuttercontact)
    session.subscribe(_shutter_subscriber)

    def _unsubscribe_shutter() -> None:
        with contextlib.suppress(ValueError):
            session._subscribers.remove(_shutter_subscriber)  # noqa: SLF001

    config_entry.async_on_unload(_unsubscribe_shutter)

    for binary_sensor in session.device_helper.motion_detectors:
        if device_excluded(binary_sensor, config_entry.options):
            continue
        await async_migrate_to_new_unique_id(
            hass, Platform.BINARY_SENSOR, device=binary_sensor
        )
        entities.append(
            MotionDetectionSensor(
                hass=hass,
                device=binary_sensor,
                entry_id=config_entry.entry_id,
            )
        )

    for binary_sensor in session.device_helper.motion_detectors2:
        if device_excluded(binary_sensor, config_entry.options):
            continue
        await async_migrate_to_new_unique_id(
            hass, Platform.BINARY_SENSOR, device=binary_sensor
        )
        entities.append(
            MotionDetectionSensor(
                hass=hass,
                device=binary_sensor,
                entry_id=config_entry.entry_id,
            )
        )
        await async_migrate_to_new_unique_id(
            hass, Platform.BINARY_SENSOR, device=binary_sensor, attr_name="Occupancy"
        )
        entities.append(
            OccupancyDetectionSensor(
                device=binary_sensor,
                entry_id=config_entry.entry_id,
            )
        )
        entities.append(
            TamperSensor(
                device=binary_sensor,
                entry_id=config_entry.entry_id,
            )
        )

    for binary_sensor in session.device_helper.smoke_detectors:
        if device_excluded(binary_sensor, config_entry.options):
            continue
        await async_migrate_to_new_unique_id(
            hass, Platform.BINARY_SENSOR, device=binary_sensor
        )
        entities.append(
            SmokeDetectorSensor(
                device=binary_sensor,
                hass=hass,
                entry_id=config_entry.entry_id,
            )
        )

    smoke_detection_system = session.device_helper.smoke_detection_system
    if smoke_detection_system and not device_excluded(
        smoke_detection_system, config_entry.options
    ):
        entities.append(
            SmokeDetectionSystemSensor(
                device=smoke_detection_system,
                hass=hass,
                entry_id=config_entry.entry_id,
            )
        )
        twinguards = session.device_helper.twinguards
        if twinguards:
            tracker = TwinguardAlarmTracker(
                session=session,
                smoke_detection_system=smoke_detection_system,
                hass=hass,
            )
            # Initial refresh (async; awaits get_messages on the loop).
            await tracker.async_refresh()

            def _cleanup_tracker() -> None:
                tracker.teardown()

            config_entry.async_on_unload(_cleanup_tracker)
            # async_listen_once returns an unsubscribe callable; register it so the
            # listener is removed on config-entry reload (prevents closure leak).
            config_entry.async_on_unload(
                hass.bus.async_listen_once(
                    EVENT_HOMEASSISTANT_STOP, lambda _: tracker.teardown()
                )
            )

            for binary_sensor in twinguards:
                if device_excluded(binary_sensor, config_entry.options):
                    continue
                entities.append(
                    TwinguardSmokeAlarmSensor(
                        device=binary_sensor,
                        entry_id=config_entry.entry_id,
                        tracker=tracker,
                    )
                )

    for binary_sensor in session.device_helper.water_leakage_detectors:
        if device_excluded(binary_sensor, config_entry.options):
            continue
        await async_migrate_to_new_unique_id(
            hass, Platform.BINARY_SENSOR, device=binary_sensor
        )
        entities.append(
            WaterLeakageDetectorSensor(
                device=binary_sensor,
                entry_id=config_entry.entry_id,
            )
        )

    for binary_sensor in session.device_helper.shutter_contacts2:
        if device_excluded(binary_sensor, config_entry.options):
            continue
        if isinstance(binary_sensor, SHCShutterContact2Plus):
            entities.append(
                ShutterContactVibrationSensor(
                    device=binary_sensor,
                    entry_id=config_entry.entry_id,
                )
            )

    for binary_sensor in (
        list(session.device_helper.motion_detectors)
        + list(session.device_helper.motion_detectors2)
        + list(session.device_helper.shutter_contacts)
        + list(session.device_helper.shutter_contacts2)
        + list(session.device_helper.smoke_detectors)
        + list(session.device_helper.thermostats)
        + list(session.device_helper.twinguards)
        + list(session.device_helper.universal_switches)
        + list(session.device_helper.wallthermostats)
        + list(session.device_helper.roomthermostats)
        + list(session.device_helper.water_leakage_detectors)
        + list(getattr(session.device_helper, "outdoor_sirens", []))
    ):
        if device_excluded(binary_sensor, config_entry.options):
            continue
        await async_migrate_to_new_unique_id(
            hass, Platform.BINARY_SENSOR, device=binary_sensor, attr_name="Battery"
        )
        if binary_sensor.supports_batterylevel:
            entities.append(
                BatterySensor(
                    device=binary_sensor,
                    entry_id=config_entry.entry_id,
                )
            )

    # Room-climate "call for heat" (#205): expose RoomClimateControl.has_demand
    # as a binary_sensor so automations can see when a room is requesting heat.
    for climate in session.device_helper.climate_controls:
        if device_excluded(climate, config_entry.options):
            continue
        entities.append(
            CallForHeatSensor(
                device=climate,
                entry_id=config_entry.entry_id,
            )
        )

    for siren in getattr(session.device_helper, "outdoor_sirens", []):
        if device_excluded(siren, config_entry.options):
            continue
        entities.append(
            SirenAcousticAlarmSensor(device=siren, entry_id=config_entry.entry_id)
        )
        entities.append(
            SirenVisualAlarmSensor(device=siren, entry_id=config_entry.entry_id)
        )
        entities.append(SirenTamperSensor(device=siren, entry_id=config_entry.entry_id))

    platform = entity_platform.current_platform.get()

    platform.async_register_entity_service(
        SERVICE_SMOKEDETECTOR_CHECK,
        {},
        "async_request_smoketest",
    )
    platform.async_register_entity_service(
        SERVICE_SMOKEDETECTOR_ALARMSTATE,
        {
            vol.Required(ATTR_COMMAND): cv.string,
        },
        "async_request_alarmstate",
    )

    if entities:
        async_add_entities(entities)


class CallForHeatSensor(SHCEntity, BinarySensorEntity):  # type: ignore[misc]
    """Room-climate 'call for heat' sensor — on when the room requests heat.

    Reads RoomClimateControl.has_demand (#205). getattr-guarded so it tolerates
    an older boschshcpy without the property (degrades to off rather than crash).
    """

    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_icon = "mdi:radiator"
    _attr_translation_key = "call_for_heat"

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize a call-for-heat binary sensor."""
        super().__init__(device, entry_id)
        self._attr_unique_id = f"{device.root_device_id}_{device.id}_callforheat"

    @property
    def is_on(self) -> bool:
        """Return True when the room climate control is calling for heat."""
        return bool(getattr(self._device, "has_demand", False))


class SirenAcousticAlarmSensor(SHCEntity, BinarySensorEntity):  # type: ignore[misc]
    """Outdoor Siren: acoustic alarm active (read-only, #120)."""

    _attr_device_class = BinarySensorDeviceClass.SOUND
    _attr_translation_key = "siren_acoustic_alarm"

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize the siren acoustic alarm sensor."""
        super().__init__(device, entry_id)
        self._attr_unique_id = f"{device.root_device_id}_{device.id}_acoustic_alarm"

    @property
    def is_on(self) -> bool:
        """Return True when the acoustic alarm is active."""
        return bool(getattr(self._device.siren, "acoustic_alarm_on", False))


class SirenVisualAlarmSensor(SHCEntity, BinarySensorEntity):  # type: ignore[misc]
    """Outdoor Siren: visual (flash) alarm active (read-only, #120)."""

    _attr_device_class = BinarySensorDeviceClass.LIGHT
    _attr_translation_key = "siren_visual_alarm"

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize the siren visual alarm sensor."""
        super().__init__(device, entry_id)
        self._attr_unique_id = f"{device.root_device_id}_{device.id}_visual_alarm"

    @property
    def is_on(self) -> bool:
        """Return True when the visual alarm is active."""
        return bool(getattr(self._device.siren, "visual_alarm_on", False))


class SirenTamperSensor(SHCEntity, BinarySensorEntity):  # type: ignore[misc]
    """Outdoor Siren: tamper detected (read-only, #120)."""

    _attr_device_class = BinarySensorDeviceClass.TAMPER
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "siren_tamper"

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize the siren tamper sensor."""
        super().__init__(device, entry_id)
        self._attr_unique_id = f"{device.root_device_id}_{device.id}_tamper"

    @property
    def is_on(self) -> bool:
        """Return True when a tamper event is active."""
        return bool(getattr(self._device.siren, "tamper_activated", False))


class ShutterContactSensor(SHCEntity, BinarySensorEntity):  # type: ignore[misc]
    """Representation of a SHC shutter contact sensor."""

    @property
    def is_on(self) -> bool:
        """Return the state of the sensor."""
        return bool(self._device.state == ShutterContactService.State.OPEN)

    @property
    def device_class(self) -> BinarySensorDeviceClass:
        """Return the class of this device."""
        switcher = {
            "ENTRANCE_DOOR": BinarySensorDeviceClass.DOOR,
            "REGULAR_WINDOW": BinarySensorDeviceClass.WINDOW,
            "FRENCH_WINDOW": BinarySensorDeviceClass.DOOR,
            "GENERIC": BinarySensorDeviceClass.WINDOW,
        }
        return switcher.get(self._device.device_class, BinarySensorDeviceClass.WINDOW)


class ShutterContactVibrationSensor(SHCEntity, BinarySensorEntity):  # type: ignore[misc]
    """Representation of a SHC shutter contact vibration sensor."""

    _attr_device_class = BinarySensorDeviceClass.VIBRATION
    _attr_translation_key = "vibration"

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize an SHC temperature reporting sensor."""
        super().__init__(device, entry_id)
        self._attr_unique_id = f"{device.root_device_id}_{device.id}_vibration"

    @property
    def is_on(self) -> bool:
        """Return the state of the sensor."""
        return bool(
            self._device.vibrationsensor
            == VibrationSensorService.State.VIBRATION_DETECTED
        )


class MotionDetectionSensor(SHCEntity, BinarySensorEntity):  # type: ignore[misc]
    """Representation of a SHC motion detection sensor."""

    _attr_device_class = BinarySensorDeviceClass.MOTION

    def __init__(self, hass: HomeAssistant, device: SHCDevice, entry_id: str) -> None:
        """Initialize the motion detection device."""
        self.hass = hass
        self._service = None
        self._cached_device_id: str | None = None
        # Guard against phantom events on poll-id resubscribe (~24 h): the SHC
        # re-delivers every service's current state, which must not re-fire as a
        # fresh MOTION event.  Cache the last latestmotion timestamp we fired on;
        # skip the event when the timestamp is unchanged (replay), fire when it
        # advances (genuine new motion).
        self._last_fired_latestmotion: str | None = None
        super().__init__(device=device, entry_id=entry_id)

        for service in self._device.device_services:
            if service.id == "LatestMotion":
                self._service = service
                break

        self._ha_stop_unsub = hass.bus.async_listen_once(
            EVENT_HOMEASSISTANT_STOP, self._handle_ha_stop
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to SHC events and cache device_id."""
        await super().async_added_to_hass()
        self._cached_device_id = await async_get_device_id(self.hass, self._device.id)
        # Subscribe AFTER device_id is cached so events never fire with
        # device_id=None during the startup window (#288-cluster).
        if self._service is not None:
            self._service.subscribe_callback(
                self._device.id + "_eventlistener", self._input_events_handler
            )

    async def async_will_remove_from_hass(self) -> None:
        """Unsubscribe service callback and cancel HA-stop listener on entity removal."""
        if self._ha_stop_unsub is not None:
            self._ha_stop_unsub()
            self._ha_stop_unsub = None
        if self._service is not None:
            self._service.unsubscribe_callback(self._device.id + "_eventlistener")
        await super().async_will_remove_from_hass()

    def _input_events_handler(self) -> None:
        """Handle device input events.

        Replay-guard (#336): on the ~24 h poll-id resubscribe the SHC
        re-delivers the last LatestMotion state unchanged.  Only fire when
        latestmotion has advanced past the last value we fired on.
        """
        current_ts = self._device.latestmotion
        if current_ts == self._last_fired_latestmotion:
            LOGGER.debug(
                "Skipping replayed LatestMotion event for %s (ts=%s unchanged)",
                self._device.name,
                current_ts,
            )
            return
        self._last_fired_latestmotion = current_ts
        self.hass.bus.async_fire(
            EVENT_BOSCH_SHC,
            {
                ATTR_DEVICE_ID: self._cached_device_id,
                ATTR_ID: self._device.id,
                ATTR_NAME: self._device.name,
                ATTR_LAST_TIME_TRIGGERED: current_ts,
                ATTR_EVENT_TYPE: "MOTION",
                ATTR_EVENT_SUBTYPE: "",
            },
        )

    @callback  # type: ignore[untyped-decorator]
    def _handle_ha_stop(self, _: Any) -> None:
        """Handle Home Assistant stopping."""
        self._ha_stop_unsub = None
        LOGGER.debug(
            "Stopping motion detection event listener for %s", self._device.name
        )
        if self._service is not None:
            self._service.unsubscribe_callback(self._device.id + "_eventlistener")

    @property
    def is_on(self) -> bool:
        """Return the state of the sensor."""
        try:
            latestmotion = datetime.strptime(
                self._device.latestmotion, "%Y-%m-%dT%H:%M:%S.%fZ"
            ).replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            # ValueError: unparseable timestamp; TypeError: latestmotion is None.
            # The trailing literal "Z" makes strptime return a naive datetime, so
            # it must be marked UTC-aware to subtract from datetime.now(timezone.utc).
            return False

        elapsed = datetime.now(timezone.utc) - latestmotion
        if elapsed > timedelta(seconds=4 * 60):
            return False
        return True

    @property
    def should_poll(self) -> bool:
        """Retrieve motion state."""
        return True

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        return {
            "last_motion_detected": self._device.latestmotion,
        }


class SmokeDetectorSensor(SHCEntity, BinarySensorEntity):  # type: ignore[misc]
    """Representation of a SHC smoke detector sensor."""

    _attr_device_class = BinarySensorDeviceClass.SMOKE

    def __init__(
        self,
        device: SHCSmokeDetector,
        hass: HomeAssistant,
        entry_id: str,
    ):
        """Initialize the smoke detector device."""
        self._hass = hass
        self._service = None
        self._cached_device_id: str | None = None
        # Guard against phantom events on poll-id resubscribe (#336): cache the
        # last alarmstate name we fired on and skip when it is unchanged.
        self._last_fired_alarmstate: str | None = None
        super().__init__(device=device, entry_id=entry_id)

        for service in self._device.device_services:
            if service.id == "Alarm":
                self._service = service
                break

        self._ha_stop_unsub = hass.bus.async_listen_once(
            EVENT_HOMEASSISTANT_STOP, self._handle_ha_stop
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to SHC events and cache device_id."""
        await super().async_added_to_hass()
        self._cached_device_id = await async_get_device_id(self._hass, self._device.id)
        # Subscribe AFTER device_id is cached so events never fire with
        # device_id=None during the startup window (#288-cluster).
        if self._service is not None:
            self._service.subscribe_callback(
                self._device.id + "_eventlistener", self._input_events_handler
            )

    async def async_will_remove_from_hass(self) -> None:
        """Unsubscribe service callback and cancel HA-stop listener."""
        if self._service is not None:
            self._service.unsubscribe_callback(self._device.id + "_eventlistener")
        if self._ha_stop_unsub is not None:
            self._ha_stop_unsub()
            self._ha_stop_unsub = None
        await super().async_will_remove_from_hass()

    def _input_events_handler(self) -> None:
        """Handle device input events.

        Replay-guard (#336): on the ~24 h poll-id resubscribe the SHC
        re-delivers the last Alarm state unchanged.  Only fire when the
        alarmstate name has changed since the last fired event.
        """
        try:
            current_state = self._device.alarmstate.name
        except (ValueError, KeyError):
            LOGGER.warning("Unexpected alarmstate value for %s", self._device.name)
            return
        if current_state == self._last_fired_alarmstate:
            LOGGER.debug(
                "Skipping replayed Alarm event for %s (state=%s unchanged)",
                self._device.name,
                current_state,
            )
            return
        self._last_fired_alarmstate = current_state
        self._hass.bus.async_fire(
            EVENT_BOSCH_SHC,
            {
                ATTR_DEVICE_ID: self._cached_device_id,
                ATTR_ID: self._device.id,
                ATTR_NAME: self._device.name,
                ATTR_EVENT_TYPE: "ALARM",
                ATTR_EVENT_SUBTYPE: current_state,
            },
        )

    @callback  # type: ignore[untyped-decorator]
    def _handle_ha_stop(self, _: Any) -> None:
        """Handle Home Assistant stopping."""
        self._ha_stop_unsub = None
        LOGGER.debug("Stopping alarm event listener for %s", self._device.name)
        if self._service is not None:
            self._service.unsubscribe_callback(self._device.id + "_eventlistener")

    @property
    def is_on(self) -> bool:
        """Return the state of the sensor."""
        # Only PRIMARY_ALARM and SECONDARY_ALARM are smoke-related states.
        # INTRUSION_ALARM is set by the IDS (intrusion detection system) on all
        # smoke detectors when a surveillance alarm fires — it must NOT be treated
        # as a smoke event, or every detector reports smoke whenever any burglar
        # alarm triggers (issue #191).
        try:
            state = self._device.alarmstate
        except (KeyError, ValueError):
            return False
        return state in (
            AlarmService.State.PRIMARY_ALARM,
            AlarmService.State.SECONDARY_ALARM,
        )

    @property
    def icon(self) -> str:
        """Return the icon of the sensor."""
        return "mdi:smoke-detector"

    async def async_request_smoketest(self) -> None:
        """Request smokedetector test."""
        LOGGER.debug("Requesting smoke test on device %s", self._device.name)
        try:
            await self._device.async_smoketest_requested()
        except (SHCException, SHCConnectionError) as err:
            raise HomeAssistantError(
                f"Smoke test request failed for {self._device.name}: {err}",
                translation_domain=DOMAIN,
                translation_key="smoke_test_failed",
            ) from err

    async def async_request_alarmstate(self, command: str) -> None:
        """Request smokedetector alarm state."""
        LOGGER.debug(
            "Requesting custom alarm state %s on device %s", command, self._device.name
        )
        try:
            await self._device.async_set_alarmstate(command)
        except (SHCException, SHCConnectionError) as err:
            raise HomeAssistantError(
                f"Set alarm state failed for {self._device.name}: {err}",
                translation_domain=DOMAIN,
                translation_key="alarm_state_failed",
            ) from err

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        try:
            check_state = self._device.smokedetectorcheck_state.name
        except ValueError as err:
            LOGGER.warning(
                "Unknown smokedetectorcheck_state for %s: %s", self._device.name, err
            )
            check_state = None
        try:
            alarm_state = self._device.alarmstate.name
        except ValueError as err:
            LOGGER.warning("Unknown alarmstate for %s: %s", self._device.name, err)
            alarm_state = None
        return {
            "smokedetectorcheck_state": check_state,
            "alarmstate": alarm_state,
        }


class WaterLeakageDetectorSensor(SHCEntity, BinarySensorEntity):  # type: ignore[misc]
    """Representation of a SHC water leakage detector sensor."""

    _attr_device_class = BinarySensorDeviceClass.MOISTURE

    @property
    def is_on(self) -> bool:
        """Return the state of the sensor."""
        return bool(
            self._device.leakage_state != WaterLeakageSensorService.State.NO_LEAKAGE
        )

    @property
    def icon(self) -> str:
        """Return the icon of the sensor."""
        return "mdi:water-alert"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        return {
            "push_notification_state": self._device.push_notification_state.name,
            "acoustic_signal_state": self._device.acoustic_signal_state.name,
        }


class SmokeDetectionSystemSensor(SHCEntity, BinarySensorEntity):  # type: ignore[misc]
    """Representation of a SHC smoke detection system sensor."""

    _attr_device_class = BinarySensorDeviceClass.SMOKE

    def __init__(
        self,
        device: SHCSmokeDetectionSystem,
        hass: HomeAssistant,
        entry_id: str,
    ):
        """Initialize the smoke detection system device."""
        self._hass = hass
        self._service = None
        self._cached_device_id: str | None = None
        # Guard against phantom events on poll-id resubscribe (#336): cache the
        # last SurveillanceAlarm state name we fired on and skip when unchanged.
        self._last_fired_alarm: str | None = None
        super().__init__(device=device, entry_id=entry_id)

        for service in self._device.device_services:
            if service.id == "SurveillanceAlarm":
                self._service = service
                break

        self._ha_stop_unsub = hass.bus.async_listen_once(
            EVENT_HOMEASSISTANT_STOP, self._handle_ha_stop
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to SHC events and cache device_id."""
        await super().async_added_to_hass()
        self._cached_device_id = await async_get_device_id(self._hass, self._device.id)
        # Subscribe AFTER device_id is cached so events never fire with
        # device_id=None during the startup window (#288-cluster).
        if self._service is not None:
            self._service.subscribe_callback(
                self._device.id + "_eventlistener", self._input_events_handler
            )

    async def async_will_remove_from_hass(self) -> None:
        """Unsubscribe service callback and cancel HA-stop listener."""
        if self._service is not None:
            self._service.unsubscribe_callback(self._device.id + "_eventlistener")
        if self._ha_stop_unsub is not None:
            self._ha_stop_unsub()
            self._ha_stop_unsub = None
        await super().async_will_remove_from_hass()

    def _input_events_handler(self) -> None:
        """Handle device input events.

        Replay-guard (#336): on the ~24 h poll-id resubscribe the SHC
        re-delivers the last SurveillanceAlarm state unchanged.  Only fire
        when the alarm state name has changed since the last fired event.
        """
        try:
            current_alarm = self._device.alarm.name
        except (ValueError, KeyError):
            LOGGER.warning("Unexpected alarm value for %s", self._device.name)
            return
        if current_alarm == self._last_fired_alarm:
            LOGGER.debug(
                "Skipping replayed SurveillanceAlarm event for %s (state=%s unchanged)",
                self._device.name,
                current_alarm,
            )
            return
        self._last_fired_alarm = current_alarm
        self._hass.bus.async_fire(
            EVENT_BOSCH_SHC,
            {
                ATTR_DEVICE_ID: self._cached_device_id,
                ATTR_ID: self._device.id,
                ATTR_NAME: self._device.name,
                ATTR_EVENT_TYPE: "ALARM",
                ATTR_EVENT_SUBTYPE: current_alarm,
            },
        )

    @callback  # type: ignore[untyped-decorator]
    def _handle_ha_stop(self, _: Any) -> None:
        """Handle Home Assistant stopping."""
        self._ha_stop_unsub = None
        LOGGER.debug("Stopping alarm event listener for %s", self._device.name)
        if self._service is not None:
            self._service.unsubscribe_callback(self._device.id + "_eventlistener")

    @property
    def is_on(self) -> bool:
        """Return the state of the sensor."""
        return bool(self._device.alarm != SurveillanceAlarmService.State.ALARM_OFF)

    @property
    def icon(self) -> str:
        """Return the icon of the sensor."""
        return "mdi:smoke-detector"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        return {
            "alarm_state": self._device.alarm.name,
        }


class TwinguardAlarmTracker:
    """Track which Twinguard device(s) are actively triggering a smoke alarm.

    The SHC does not expose per-device alarm state directly on the Twinguard.
    Instead the shared SMOKE_DETECTION_SYSTEM fires a SurveillanceAlarm callback
    and the /messages endpoint carries SMOKE_ALARM messages whose
    ``arguments.surveillanceEvents[].triggerId`` maps back to the individual
    Twinguard device id.

    ``refresh()`` does blocking HTTP and MUST NOT be called from the event loop.
    Callbacks fire from the async polling loop on the event loop; listeners are
    called directly without call_soon_threadsafe marshalling.
    """

    def __init__(
        self,
        session: SHCSession,
        smoke_detection_system: SHCSmokeDetectionSystem,
        hass: HomeAssistant,
    ) -> None:
        """Initialize the tracker (no I/O; call refresh() separately)."""
        self._session = session
        self._smoke_detection_system = smoke_detection_system
        self._hass = hass
        self._service = None
        self._listeners: list[tuple[HomeAssistant, Callable[[], None]]] = []
        self._active_trigger_ids: set[str] = set()
        self._last_alarm_state: str | None = None
        self._torn_down = False

        for service in self._smoke_detection_system.device_services:
            if service.id == "SurveillanceAlarm":
                self._service = service
                self._service.subscribe_callback(
                    self._smoke_detection_system.id + "_twinguard_alarm_listener",
                    self._handle_alarm_update,
                )
                break

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def alarm_state(self) -> str | None:
        """Return the global surveillance alarm state name."""
        try:
            return str(self._smoke_detection_system.alarm.name)
        except ValueError as err:
            LOGGER.warning(
                "Unknown smoke detection system alarm state for %s: %s",
                self._smoke_detection_system.name,
                err,
            )
            return None

    def register_listener(
        self, hass: HomeAssistant, listener: Callable[[], None]
    ) -> None:
        """Register a listener (called from event loop via async_added_to_hass)."""
        self._listeners.append((hass, listener))

    def unregister_listener(self, listener: Callable[[], None]) -> None:
        """Unregister a listener by the listener callable."""
        self._listeners = [(h, cb) for (h, cb) in self._listeners if cb is not listener]

    def is_alarm_active_for(self, device_id: str) -> bool:
        """Return whether a smoke alarm is active for the given Twinguard device id."""
        return device_id in self._active_trigger_ids

    async def async_refresh(self) -> None:
        """Refresh active trigger ids from the SHC (async; on the event loop).

        Safe to call multiple times; skips notification if state did not change.
        """
        if self._torn_down:
            return
        alarm_state = self.alarm_state
        if alarm_state == SurveillanceAlarmService.State.ALARM_OFF.name:
            new_trigger_ids: set[str] = set()
        else:
            new_trigger_ids = await self._extract_trigger_ids_from_messages()

        if (
            new_trigger_ids == self._active_trigger_ids
            and alarm_state == self._last_alarm_state
        ):
            return

        self._active_trigger_ids = new_trigger_ids
        self._last_alarm_state = alarm_state
        self._notify_listeners()

    def teardown(self) -> None:
        """Unsubscribe from the SHC service and clear all listeners.

        Called on config-entry unload and on EVENT_HOMEASSISTANT_STOP.
        Idempotent — safe to call more than once.
        """
        if self._torn_down:
            return
        self._torn_down = True
        self._listeners = []
        if self._service is not None:
            self._service.unsubscribe_callback(
                self._smoke_detection_system.id + "_twinguard_alarm_listener"
            )

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _handle_alarm_update(self) -> None:
        """Handle a SurveillanceAlarm update (fired on the event loop).

        The async session fires this callback on the loop; schedule the async
        refresh (it awaits get_messages) as a task so the poll loop isn't
        blocked on the follow-up HTTP call.
        """
        self._hass.async_create_task(self.async_refresh())

    async def _extract_trigger_ids_from_messages(self) -> set[str]:
        """Extract active Twinguard trigger ids from SMOKE_ALARM messages."""
        try:
            messages = await self._session.api.get_messages()

            trigger_ids: set[str] = set()
            for message in messages:
                # Defensive: messageCode may not be a dict (malformed payload).
                message_code = message.get("messageCode", {})
                if not isinstance(message_code, dict):
                    continue
                if message_code.get("name") != "SMOKE_ALARM":
                    continue
                if message.get("sourceId") != self._smoke_detection_system.id:
                    continue

                # Defensive: arguments may not be a dict (malformed payload).
                # triggerId==device.id is assumed based on observed message shape;
                # pending rawscan confirmation (#203).
                arguments = message.get("arguments", {})
                if not isinstance(arguments, dict):
                    continue

                events = self._parse_surveillance_events(
                    arguments.get("surveillanceEvents")
                )
                # A message that contains an ALARM_OFF event signals the end of that
                # alarm cycle — skip the whole message so we don't re-add its triggers.
                if any(event.get("type") == "ALARM_OFF" for event in events):
                    continue

                for event in events:
                    trigger_id = event.get("triggerId")
                    if trigger_id:
                        trigger_ids.add(trigger_id)

        except Exception as err:  # noqa: BLE001
            LOGGER.warning("Unable to fetch Bosch SHC messages: %s", err)
            return self._active_trigger_ids

        return trigger_ids

    @staticmethod
    def _parse_surveillance_events(raw_events: Any) -> list[dict[str, Any]]:
        """Parse surveillanceEvents from a Bosch SHC message payload.

        The field may already be a list (native JSON parse) or a JSON-encoded
        string (observed in some firmware versions).
        """
        if isinstance(raw_events, list):
            return [e for e in raw_events if isinstance(e, dict)]
        if not raw_events:
            return []
        try:
            parsed = json.loads(raw_events)
        except (TypeError, json.JSONDecodeError):
            return []
        if not isinstance(parsed, list):
            return []
        return [e for e in parsed if isinstance(e, dict)]

    def _notify_listeners(self) -> None:
        """Notify all registered entity listeners."""
        for _hass, listener in list(self._listeners):
            listener()


class TwinguardSmokeAlarmSensor(SHCEntity, BinarySensorEntity):  # type: ignore[misc]
    """Per-Twinguard binary sensor: True when that device is the active smoke alarm source."""

    _attr_device_class = BinarySensorDeviceClass.SMOKE
    _attr_translation_key = "smoke"

    def __init__(
        self,
        device: SHCDevice,
        entry_id: str,
        tracker: TwinguardAlarmTracker,
    ) -> None:
        """Initialize the Twinguard smoke alarm sensor."""
        super().__init__(device=device, entry_id=entry_id)
        self._attr_unique_id = f"{device.root_device_id}_{device.id}_smoke"
        self._tracker = tracker
        self._tracker_listener = self._handle_tracker_update

    async def async_added_to_hass(self) -> None:
        """Register with tracker when entity is added to HA."""
        await super().async_added_to_hass()
        self._tracker.register_listener(self.hass, self._tracker_listener)

    async def async_will_remove_from_hass(self) -> None:
        """Unregister from tracker when entity is removed."""
        self._tracker.unregister_listener(self._tracker_listener)
        await super().async_will_remove_from_hass()

    @callback  # type: ignore[untyped-decorator]
    def _handle_tracker_update(self) -> None:
        """Called on the event loop when tracker state changes."""
        self.schedule_update_ha_state()

    @property
    def is_on(self) -> bool:
        """Return True when this Twinguard is the source of an active smoke alarm."""
        return self._tracker.is_alarm_active_for(self._device.id)

    @property
    def icon(self) -> str:
        """Return the icon of the sensor."""
        return "mdi:smoke-detector"

    async def async_request_smoketest(self) -> None:
        """Request a Twinguard smoke test."""
        LOGGER.debug("Requesting smoke test on device %s", self._device.name)
        try:
            await self._device.async_smoketest_requested()
        except (SHCException, SHCConnectionError) as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="smoke_test_failed",
            ) from err

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        return {
            "alarm_state": self._tracker.alarm_state,
        }


class BatterySensor(SHCEntity, BinarySensorEntity):  # type: ignore[misc]
    """Representation of a SHC battery reporting sensor."""

    _attr_device_class = BinarySensorDeviceClass.BATTERY

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize an SHC temperature reporting sensor."""
        super().__init__(device, entry_id)
        self._attr_unique_id = f"{device.root_device_id}_{device.id}_battery"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def is_on(self) -> bool:
        """Return the state of the sensor.

        Returns True (battery problem) only for LOW_BATTERY, CRITICAL_LOW, and
        CRITICALLY_LOW_BATTERY.  NOT_AVAILABLE means the device has not yet
        reported battery state — this must NOT be treated as a low-battery
        condition.
        """
        level = self._device.batterylevel
        battery_state = BatteryLevelService.State

        if level == battery_state.NOT_AVAILABLE:
            LOGGER.debug(
                "Battery state of device %s is not available", self._device.name
            )
            return False

        if level == battery_state.CRITICAL_LOW:
            LOGGER.warning(
                "Battery state of device %s is critical low", self._device.name
            )

        if level == battery_state.CRITICALLY_LOW_BATTERY:
            LOGGER.warning(
                "Battery state of device %s is critically low", self._device.name
            )

        if level == battery_state.LOW_BATTERY:
            LOGGER.warning("Battery state of device %s is low", self._device.name)

        return bool(level != battery_state.OK)


class OccupancyDetectionSensor(SHCEntity, BinarySensorEntity):  # type: ignore[misc]
    """Representation of a SHC Motion Detector II [+M] occupancy sensor."""

    _attr_device_class = BinarySensorDeviceClass.OCCUPANCY
    _attr_translation_key = "occupancy"

    def __init__(self, device: SHCMotionDetector2, entry_id: str) -> None:
        """Initialize the occupancy detection sensor."""
        super().__init__(device=device, entry_id=entry_id)
        self._attr_unique_id = f"{device.root_device_id}_{device.id}_occupancy"

    @property
    def is_on(self) -> bool:
        """Return True when the zone is occupied."""
        return bool(self._device.occupied)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return last occupancy change time as an extra attribute."""
        return {
            "last_occupancy_change": self._device.last_occupancy_change_time,
        }


class TamperSensor(SHCEntity, BinarySensorEntity):  # type: ignore[misc]
    """Representation of a SHC Motion Detector II [+M] tamper sensor.

    Reports True when the device housing was opened/tampered with.
    Reads was_tampered from the LatestTamperService via the model accessor.
    """

    _attr_device_class = BinarySensorDeviceClass.TAMPER
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "tamper"

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize the tamper sensor."""
        super().__init__(device=device, entry_id=entry_id)
        self._attr_unique_id = f"{device.root_device_id}_{device.id}_tamper"

    @property
    def is_on(self) -> bool:
        """Return True when the device has been tampered with."""
        return bool(getattr(self._device, "was_tampered", False))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the last tamper time as an extra attribute."""
        return {
            "last_tamper_time": getattr(self._device, "last_tamper_time", None),
        }
