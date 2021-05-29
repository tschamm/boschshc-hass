"""Platform for binarysensor integration."""
import asyncio
import logging
from datetime import datetime, timedelta

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from boschshcpy import (
    SHCSession,
    SHCShutterContact,
    SHCSmokeDetectionSystem,
    SHCSmokeDetector,
    SHCWaterLeakageSensor,
)
from homeassistant.components.binary_sensor import (
    DEVICE_CLASS_DOOR,
    DEVICE_CLASS_MOISTURE,
    DEVICE_CLASS_MOTION,
    DEVICE_CLASS_SMOKE,
    DEVICE_CLASS_WINDOW,
    BinarySensorEntity,
)
from homeassistant.const import (
    ATTR_COMMAND,
    ATTR_DEVICE_ID,
    ATTR_ID,
    ATTR_NAME,
    EVENT_HOMEASSISTANT_STOP,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_platform

from .const import (
    ATTR_EVENT_SUBTYPE,
    ATTR_EVENT_TYPE,
    ATTR_LAST_TIME_TRIGGERED,
    DATA_SESSION,
    DOMAIN,
    EVENT_BOSCH_SHC,
    SERVICE_SMOKEDETECTOR_ALARMSTATE,
    SERVICE_SMOKEDETECTOR_CHECK,
)
from .entity import SHCEntity, async_get_device_id

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the SHC binary sensor platform."""
    entities = []
    session: SHCSession = hass.data[DOMAIN][config_entry.entry_id][DATA_SESSION]

    for binary_sensor in session.device_helper.shutter_contacts:
        entities.append(
            ShutterContactSensor(
                device=binary_sensor,
                parent_id=session.information.unique_id,
                entry_id=config_entry.entry_id,
            )
        )

    for binary_sensor in session.device_helper.motion_detectors:
        entities.append(
            MotionDetectionSensor(
                hass=hass,
                device=binary_sensor,
                parent_id=session.information.unique_id,
                entry_id=config_entry.entry_id,
            )
        )

    for binary_sensor in session.device_helper.smoke_detectors:
        entities.append(
            SmokeDetectorSensor(
                device=binary_sensor,
                parent_id=session.information.unique_id,
                hass=hass,
                entry_id=config_entry.entry_id,
            )
        )

    for binary_sensor in session.device_helper.smoke_detection_system:
        entities.append(
            SmokeDetectionSystemSensor(
                device=binary_sensor,
                parent_id=session.information.unique_id,
                hass=hass,
                entry_id=config_entry.entry_id,
            )
        )

    for binary_sensor in session.device_helper.water_leakage_detectors:
        entities.append(
            WaterLeakageDetectorSensor(
                device=binary_sensor,
                parent_id=session.information.unique_id,
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
        """Return the class of this device, from component DEVICE_CLASSES."""
        switcher = {
            "ENTRANCE_DOOR": DEVICE_CLASS_DOOR,
            "REGULAR_WINDOW": DEVICE_CLASS_WINDOW,
            "FRENCH_WINDOW": DEVICE_CLASS_DOOR,
            "GENERIC": DEVICE_CLASS_WINDOW,
        }
        return switcher.get(self._device.device_class, DEVICE_CLASS_WINDOW)


class MotionDetectionSensor(SHCEntity, BinarySensorEntity):
    """Representation of a SHC motion detection sensor."""

    def __init__(self, hass, device, parent_id: str, entry_id: str):
        """Initialize the motion detection device."""
        self.hass = hass
        self._service = None
        super().__init__(device=device, parent_id=parent_id, entry_id=entry_id)

        for service in self._device.device_services:
            if service.id == "LatestMotion":
                self._service = service
                self._service.subscribe_callback(
                    self._device.id + "_eventlistener", self._async_input_events_handler
                )

        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, self._handle_ha_stop)

    @callback
    def _async_input_events_handler(self):
        """Handle device input events."""
        self.hass.bus.async_fire(
            EVENT_BOSCH_SHC,
            {
                ATTR_DEVICE_ID: asyncio.run_coroutine_threadsafe(
                    async_get_device_id(self.hass, self._device.id), self.hass.loop
                ).result(),
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
        _LOGGER.debug(
            "Stopping motion detection event listener for %s", self._device.name
        )
        self._service.unsubscribe_callback(self._device.id + "_eventlistener")

    @property
    def device_class(self):
        """Return the class of this device, from component DEVICE_CLASSES."""
        return DEVICE_CLASS_MOTION

    @property
    def is_on(self):
        """Return the state of the sensor."""
        try:
            latestmotion = datetime.strptime(
                self._device.latestmotion, "%Y-%m-%dT%H:%M:%S.%fZ"
            )
        except ValueError:
            return False

        elapsed = datetime.utcnow() - latestmotion
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

    def __init__(
        self,
        device: SHCSmokeDetector,
        parent_id: str,
        hass: HomeAssistant,
        entry_id: str,
    ):
        """Initialize the smoke detector device."""
        self._hass = hass
        self._service = None
        super().__init__(device=device, parent_id=parent_id, entry_id=entry_id)

        for service in self._device.device_services:
            if service.id == "Alarm":
                self._service = service
                self._service.subscribe_callback(
                    self._device.id + "_eventlistener", self._async_input_events_handler
                )

        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, self._handle_ha_stop)

    @callback
    def _async_input_events_handler(self):
        """Handle device input events."""
        self._hass.bus.async_fire(
            EVENT_BOSCH_SHC,
            {
                ATTR_DEVICE_ID: asyncio.run_coroutine_threadsafe(
                    async_get_device_id(self._hass, self._device.id), self._hass.loop
                ).result(),
                ATTR_ID: self._device.id,
                ATTR_NAME: self._device.name,
                ATTR_EVENT_TYPE: "ALARM",
                ATTR_EVENT_SUBTYPE: self._device.alarmstate.name,
            },
        )

    @callback
    def _handle_ha_stop(self, _):
        """Handle Home Assistant stopping."""
        _LOGGER.debug("Stopping alarm event listener for %s", self._device.name)
        self._service.unsubscribe_callback(self._device.id + "_eventlistener")

    @property
    def is_on(self):
        """Return the state of the sensor."""
        return self._device.alarmstate != SHCSmokeDetector.AlarmService.State.IDLE_OFF

    @property
    def device_class(self):
        """Return the class of this device, from component DEVICE_CLASSES."""
        return DEVICE_CLASS_SMOKE

    @property
    def icon(self):
        """Return the icon of the sensor."""
        return "mdi:smoke-detector"

    async def async_request_smoketest(self):
        """Request smokedetector test."""
        _LOGGER.debug("Requesting smoke test on entity %s", self.name)
        await self._device.smoketest_requested()

    async def async_request_alarmstate(self, command: str):
        """Request smokedetector alarm state."""

        async def async_set_alarmstate(device, command):
            await device.alarmstate(command)

        _LOGGER.debug(
            "Requesting custom alarm state %s on entity %s", command, self.name
        )
        await async_set_alarmstate(self._device, command)

    @property
    def extra_state_attributes(self):
        """Return the state attributes."""
        return {
            "smokedetectorcheck_state": self._device.smokedetectorcheck_state.name,
            "alarmstate": self._device.alarmstate.name,
        }


class WaterLeakageDetectorSensor(SHCEntity, BinarySensorEntity):
    """Representation of a SHC water leakage detector sensor."""

    @property
    def is_on(self):
        """Return the state of the sensor."""
        return (
            self._device.leakage_state
            != SHCWaterLeakageSensor.WaterLeakageSensorService.State.NO_LEAKAGE
        )

    @property
    def device_class(self):
        """Return the class of this device, from component DEVICE_CLASSES."""
        return DEVICE_CLASS_MOISTURE

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

    def __init__(
        self,
        device: SHCSmokeDetectionSystem,
        parent_id: str,
        hass: HomeAssistant,
        entry_id: str,
    ):
        """Initialize the smoke detection system device."""
        self._hass = hass
        self._service = None
        super().__init__(device=device, parent_id=parent_id, entry_id=entry_id)

        for service in self._device.device_services:
            if service.id == "SurveillanceAlarm":
                self._service = service
                self._service.subscribe_callback(
                    self._device.id + "_eventlistener", self._async_input_events_handler
                )

        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, self._handle_ha_stop)

    @callback
    def _async_input_events_handler(self):
        """Handle device input events."""
        self._hass.bus.async_fire(
            EVENT_BOSCH_SHC,
            {
                ATTR_DEVICE_ID: asyncio.run_coroutine_threadsafe(
                    async_get_device_id(self._hass, self._device.id), self._hass.loop
                ).result(),
                ATTR_ID: self._device.id,
                ATTR_NAME: self._device.name,
                ATTR_EVENT_TYPE: "ALARM",
                ATTR_EVENT_SUBTYPE: self._device.alarm.name,
            },
        )

    @callback
    def _handle_ha_stop(self, _):
        """Handle Home Assistant stopping."""
        _LOGGER.debug("Stopping alarm event listener for %s", self._device.name)
        self._service.unsubscribe_callback(self._device.id + "_eventlistener")

    @property
    def is_on(self):
        """Return the state of the sensor."""
        return (
            self._device.alarm
            != SHCSmokeDetectionSystem.SurveillanceAlarmService.State.ALARM_OFF
        )

    @property
    def device_class(self):
        """Return the class of this device, from component DEVICE_CLASSES."""
        return DEVICE_CLASS_SMOKE

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
