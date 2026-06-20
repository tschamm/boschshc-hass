"""Platform for binarysensor integration."""

from datetime import datetime, timedelta, timezone

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from boschshcpy import (
    SHCBatteryDevice,
    SHCDevice,
    SHCSession,
    SHCShutterContact,
    SHCShutterContact2Plus,
    SHCSmokeDetectionSystem,
    SHCSmokeDetector,
    SHCWaterLeakageSensor,
)
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
from .entity import SHCEntity, async_get_device_id, async_migrate_to_new_unique_id


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the SHC binary sensor platform."""
    entities = []
    session: SHCSession = hass.data[DOMAIN][config_entry.entry_id][DATA_SESSION]

    @callback
    def async_add_shuttercontact(
        device: SHCShutterContact,
    ) -> None:
        """Add Shutter Contact 2 Binary Sensor."""
        binary_sensor = ShutterContactSensor(
            device=device,
            entry_id=config_entry.entry_id,
        )
        async_add_entities([binary_sensor])

    for binary_sensor in (
        session.device_helper.shutter_contacts + session.device_helper.shutter_contacts2
    ):
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

    def _unsubscribe_shutter():
        try:
            session._subscribers.remove(_shutter_subscriber)
        except ValueError:
            pass

    config_entry.async_on_unload(_unsubscribe_shutter)

    for binary_sensor in session.device_helper.motion_detectors:
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

    for binary_sensor in session.device_helper.smoke_detectors:
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

    binary_sensor = session.device_helper.smoke_detection_system
    if binary_sensor:
        entities.append(
            SmokeDetectionSystemSensor(
                device=binary_sensor,
                hass=hass,
                entry_id=config_entry.entry_id,
            )
        )

    for binary_sensor in session.device_helper.water_leakage_detectors:
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
        if isinstance(binary_sensor, SHCShutterContact2Plus):
            entities.append(
                ShutterContactVibrationSensor(
                    device=binary_sensor,
                    entry_id=config_entry.entry_id,
                )
            )

    for binary_sensor in (
        session.device_helper.motion_detectors
        + session.device_helper.shutter_contacts
        + session.device_helper.shutter_contacts2
        + session.device_helper.smoke_detectors
        + session.device_helper.thermostats
        + session.device_helper.twinguards
        + session.device_helper.universal_switches
        + session.device_helper.wallthermostats
        + session.device_helper.roomthermostats
        + session.device_helper.water_leakage_detectors
    ):
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


class ShutterContactSensor(SHCEntity, BinarySensorEntity):
    """Representation of a SHC shutter contact sensor."""

    @property
    def is_on(self):
        """Return the state of the sensor."""
        return self._device.state == SHCShutterContact.ShutterContactService.State.OPEN

    @property
    def device_class(self):
        """Return the class of this device."""
        switcher = {
            "ENTRANCE_DOOR": BinarySensorDeviceClass.DOOR,
            "REGULAR_WINDOW": BinarySensorDeviceClass.WINDOW,
            "FRENCH_WINDOW": BinarySensorDeviceClass.DOOR,
            "GENERIC": BinarySensorDeviceClass.WINDOW,
        }
        return switcher.get(self._device.device_class, BinarySensorDeviceClass.WINDOW)


class ShutterContactVibrationSensor(SHCEntity, BinarySensorEntity):
    """Representation of a SHC shutter contact vibration sensor."""

    _attr_device_class = BinarySensorDeviceClass.VIBRATION

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize an SHC temperature reporting sensor."""
        super().__init__(device, entry_id)
        self._attr_name = f"{device.name} Vibration"
        self._attr_unique_id = f"{device.root_device_id}_{device.id}_vibration"

    @property
    def is_on(self):
        """Return the state of the sensor."""
        return (
            self._device.vibrationsensor
            == SHCShutterContact2Plus.VibrationSensorService.State.VIBRATION_DETECTED
        )


class MotionDetectionSensor(SHCEntity, BinarySensorEntity):
    """Representation of a SHC motion detection sensor."""

    _attr_device_class = BinarySensorDeviceClass.MOTION

    def __init__(self, hass, device, entry_id: str):
        """Initialize the motion detection device."""
        self.hass = hass
        self._service = None
        self._cached_device_id = None
        super().__init__(device=device, entry_id=entry_id)

        for service in self._device.device_services:
            if service.id == "LatestMotion":
                self._service = service
                self._service.subscribe_callback(
                    self._device.id + "_eventlistener", self._input_events_handler
                )

        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, self._handle_ha_stop)

    async def async_added_to_hass(self):
        """Subscribe to SHC events and cache device_id."""
        await super().async_added_to_hass()
        self._cached_device_id = await async_get_device_id(
            self.hass, self._device.id
        )

    def _input_events_handler(self):
        """Handle device input events."""
        self.hass.bus.fire(
            EVENT_BOSCH_SHC,
            {
                ATTR_DEVICE_ID: self._cached_device_id,
                ATTR_ID: self._device.id,
                ATTR_NAME: self._device.name,
                ATTR_LAST_TIME_TRIGGERED: self._device.latestmotion,
                ATTR_EVENT_TYPE: "MOTION",
                ATTR_EVENT_SUBTYPE: "",
            },
        )

    @callback
    def _handle_ha_stop(self, _):
        """Handle Home Assistant stopping."""
        LOGGER.debug(
            "Stopping motion detection event listener for %s", self._device.name
        )
        self._service.unsubscribe_callback(self._device.id + "_eventlistener")

    @property
    def is_on(self):
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
    def should_poll(self):
        """Retrieve motion state."""
        return True

    @property
    def extra_state_attributes(self):
        """Return the state attributes."""
        return {
            "last_motion_detected": self._device.latestmotion,
        }


class SmokeDetectorSensor(SHCEntity, BinarySensorEntity):
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
        self._cached_device_id = None
        super().__init__(device=device, entry_id=entry_id)

        for service in self._device.device_services:
            if service.id == "Alarm":
                self._service = service
                self._service.subscribe_callback(
                    self._device.id + "_eventlistener", self._input_events_handler
                )

        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, self._handle_ha_stop)

    async def async_added_to_hass(self):
        """Subscribe to SHC events and cache device_id."""
        await super().async_added_to_hass()
        self._cached_device_id = await async_get_device_id(
            self._hass, self._device.id
        )

    def _input_events_handler(self):
        """Handle device input events."""
        self._hass.bus.fire(
            EVENT_BOSCH_SHC,
            {
                ATTR_DEVICE_ID: self._cached_device_id,
                ATTR_ID: self._device.id,
                ATTR_NAME: self._device.name,
                ATTR_EVENT_TYPE: "ALARM",
                ATTR_EVENT_SUBTYPE: self._device.alarmstate.name,
            },
        )

    @callback
    def _handle_ha_stop(self, _):
        """Handle Home Assistant stopping."""
        LOGGER.debug("Stopping alarm event listener for %s", self._device.name)
        self._service.unsubscribe_callback(self._device.id + "_eventlistener")

    @property
    def is_on(self):
        """Return the state of the sensor."""
        # Only PRIMARY_ALARM and SECONDARY_ALARM are smoke-related states.
        # INTRUSION_ALARM is set by the IDS (intrusion detection system) on all
        # smoke detectors when a surveillance alarm fires — it must NOT be treated
        # as a smoke event, or every detector reports smoke whenever any burglar
        # alarm triggers (issue #191).
        return self._device.alarmstate in (
            SHCSmokeDetector.AlarmService.State.PRIMARY_ALARM,
            SHCSmokeDetector.AlarmService.State.SECONDARY_ALARM,
        )

    @property
    def icon(self):
        """Return the icon of the sensor."""
        return "mdi:smoke-detector"

    async def async_request_smoketest(self):
        """Request smokedetector test."""
        LOGGER.debug("Requesting smoke test on entity %s", self.name)
        await self._hass.async_add_executor_job(self._device.smoketest_requested)

    async def async_request_alarmstate(self, command: str):
        """Request smokedetector alarm state."""

        def set_alarmstate(device, command):
            device.alarmstate = command

        LOGGER.debug(
            "Requesting custom alarm state %s on entity %s", command, self.name
        )
        await self._hass.async_add_executor_job(set_alarmstate, self._device, command)

    @property
    def extra_state_attributes(self):
        """Return the state attributes."""
        return {
            "smokedetectorcheck_state": self._device.smokedetectorcheck_state.name,
            "alarmstate": self._device.alarmstate.name,
        }


class WaterLeakageDetectorSensor(SHCEntity, BinarySensorEntity):
    """Representation of a SHC water leakage detector sensor."""

    _attr_device_class = BinarySensorDeviceClass.MOISTURE

    @property
    def is_on(self):
        """Return the state of the sensor."""
        return (
            self._device.leakage_state
            != SHCWaterLeakageSensor.WaterLeakageSensorService.State.NO_LEAKAGE
        )

    @property
    def icon(self):
        """Return the icon of the sensor."""
        return "mdi:water-alert"

    @property
    def extra_state_attributes(self):
        """Return the state attributes."""
        return {
            "push_notification_state": self._device.push_notification_state.name,
            "acoustic_signal_state": self._device.acoustic_signal_state.name,
        }


class SmokeDetectionSystemSensor(SHCEntity, BinarySensorEntity):
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
        self._cached_device_id = None
        super().__init__(device=device, entry_id=entry_id)
        self._attr_unique_id = f"{device.root_device_id}_{device.id}"
        self._attr_name = f"{device.root_device_id} {device.name}"

        for service in self._device.device_services:
            if service.id == "SurveillanceAlarm":
                self._service = service
                self._service.subscribe_callback(
                    self._device.id + "_eventlistener", self._input_events_handler
                )

        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, self._handle_ha_stop)

    async def async_added_to_hass(self):
        """Subscribe to SHC events and cache device_id."""
        await super().async_added_to_hass()
        self._cached_device_id = await async_get_device_id(
            self._hass, self._device.id
        )

    def _input_events_handler(self):
        """Handle device input events."""
        self._hass.bus.fire(
            EVENT_BOSCH_SHC,
            {
                ATTR_DEVICE_ID: self._cached_device_id,
                ATTR_ID: self._device.id,
                ATTR_NAME: self._device.name,
                ATTR_EVENT_TYPE: "ALARM",
                ATTR_EVENT_SUBTYPE: self._device.alarm.name,
            },
        )

    @callback
    def _handle_ha_stop(self, _):
        """Handle Home Assistant stopping."""
        LOGGER.debug("Stopping alarm event listener for %s", self._device.name)
        self._service.unsubscribe_callback(self._device.id + "_eventlistener")

    @property
    def is_on(self):
        """Return the state of the sensor."""
        return (
            self._device.alarm
            != SHCSmokeDetectionSystem.SurveillanceAlarmService.State.ALARM_OFF
        )

    @property
    def icon(self):
        """Return the icon of the sensor."""
        return "mdi:smoke-detector"

    @property
    def extra_state_attributes(self):
        """Return the state attributes."""
        return {
            "alarm_state": self._device.alarm.name,
        }


class BatterySensor(SHCEntity, BinarySensorEntity):
    """Representation of a SHC battery reporting sensor."""

    _attr_device_class = BinarySensorDeviceClass.BATTERY

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize an SHC temperature reporting sensor."""
        super().__init__(device, entry_id)
        self._attr_name = f"{device.name} Battery"
        self._attr_unique_id = f"{device.root_device_id}_{device.id}_battery"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def is_on(self):
        """Return the state of the sensor."""
        if (
            self._device.batterylevel
            == SHCBatteryDevice.BatteryLevelService.State.NOT_AVAILABLE
        ):
            LOGGER.debug("Battery state of device %s is not available", self.name)

        if (
            self._device.batterylevel
            == SHCBatteryDevice.BatteryLevelService.State.CRITICAL_LOW
        ):
            LOGGER.warning("Battery state of device %s is critical low", self.name)

        if (
            self._device.batterylevel
            == SHCBatteryDevice.BatteryLevelService.State.LOW_BATTERY
        ):
            LOGGER.warning("Battery state of device %s is low", self.name)

        return (
            self._device.batterylevel != SHCBatteryDevice.BatteryLevelService.State.OK
        )
