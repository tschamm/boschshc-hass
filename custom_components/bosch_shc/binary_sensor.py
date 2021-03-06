"""Platform for binarysensor integration."""
import logging
from datetime import datetime, timedelta

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from boschshcpy import SHCSession, SHCShutterContact, SHCSmokeDetector
from homeassistant.components.binary_sensor import (
    DEVICE_CLASS_DOOR,
    DEVICE_CLASS_MOTION,
    DEVICE_CLASS_SMOKE,
    DEVICE_CLASS_WINDOW,
    BinarySensorEntity,
)
from homeassistant.const import ATTR_COMMAND
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_platform

from .const import DOMAIN, SERVICE_SMOKEDETECTOR_ALARMSTATE, SERVICE_SMOKEDETECTOR_CHECK
from .entity import SHCEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the SHC binary sensor platform."""
    entities = []
    session: SHCSession = hass.data[DOMAIN][config_entry.entry_id]

    for binarysensor in session.device_helper.shutter_contacts:
        entities.append(
            ShutterContactSensor(
                device=binarysensor,
                parent_id=session.information.name,
                entry_id=config_entry.entry_id,
            )
        )

    for binarysensor in session.device_helper.motion_detectors:
        entities.append(
            MotionDetectionSensor(
                device=binarysensor,
                parent_id=session.information.name,
                entry_id=config_entry.entry_id,
            )
        )

    for binarysensor in session.device_helper.smoke_detectors:
        entities.append(
            SmokeDetectorSensor(
                device=binarysensor,
                parent_id=session.information.name,
                hass=hass,
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
        """Polling mode to retrieve motion state."""
        return True

    @property
    def state_attributes(self):
        state_attr = super().state_attributes
        if state_attr is None:
            state_attr = dict()

        state_attr["last_motion_detected"] = self._device.latestmotion
        return state_attr


class SmokeDetectorSensor(SHCEntity, BinarySensorEntity):
    """Representation of a SHC smoke detector sensor."""

    def __init__(
        self,
        device: SHCSmokeDetector,
        parent_id: str,
        hass: HomeAssistant,
        entry_id: str,
    ):
        """Initialize the SHC device."""
        super().__init__(device=device, parent_id=parent_id, entry_id=entry_id)
        self._hass = hass

    @property
    def is_on(self):
        """Return the state of the sensor."""
        if self._device.alarmstate == SHCSmokeDetector.AlarmService.State.IDLE_OFF:
            return False

        return True

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
        await self._hass.async_add_executor_job(self._device.smoketest_requested)

    async def async_request_alarmstate(self, command: str):
        """Request smokedetector alarm state."""

        def set_alarmstate(device, command):
            device.alarmstate = command

        _LOGGER.debug(
            "Requesting custom alarm state %s on entity %s", command, self.name
        )
        await self._hass.async_add_executor_job(set_alarmstate, self._device, command)

    @property
    def state_attributes(self):
        state_attr = super().state_attributes
        if state_attr is None:
            state_attr = dict()

        state_attr[
            "smokedetectorcheck_state"
        ] = self._device.smokedetectorcheck_state.name
        return state_attr
