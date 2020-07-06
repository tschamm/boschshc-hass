"""Platform for binarysensor integration."""
import logging

from boschshcpy import SHCSession, SHCShutterContact, SHCSmokeDetector

from homeassistant.components.binary_sensor import (
    DEVICE_CLASS_DOOR,
    DEVICE_CLASS_SMOKE,
    DEVICE_CLASS_WINDOW,
    BinarySensorEntity,
)
from homeassistant.const import CONF_IP_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_platform

from .const import DOMAIN
from .entity import SHCEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the SHC binary sensor platform."""
    entities = []
    session: SHCSession = hass.data[DOMAIN][config_entry.entry_id]
    ip_address = config_entry.data[CONF_IP_ADDRESS]

    for device in session.device_helper.shutter_contacts:
        _LOGGER.debug("Found shutter contact: %s (%s)", device.name, device.id)
        room_name=session.room(device.room_id).name
        entities.append(ShutterContactSensor(device=device, room_name=room_name, controller_ip=ip_address))

    for device in session.device_helper.smoke_detectors:
        _LOGGER.debug("Found smoke detector: %s (%s)", device.name, device.id)
        room_name=session.room(device.room_id).name
        entities.append(SmokeDetectorSensor(device=device, room_name=room_name, controller_ip=ip_address, hass=hass))
        entities.append(SmokeDetectorCheckStateSensor(device=device, room_name=room_name, controller_ip=ip_address, hass=hass))

    # for device in session.device_helper.twinguards:
    #     _LOGGER.debug("Found twinguard smoke detector: %s (%s)", device.name, device.id)
    #     room_name=session.room(device.room_id).name
    #     entities.append(SmokeDetectorSensor(device=device, room_name=room_name, controller_ip=ip_address, hass=hass))
    #     entities.append(SmokeDetectorCheckStateSensor(device=device, room_name=room_name, controller_ip=ip_address, hass=hass))

    if entities:
        async_add_entities(entities)

    platform = entity_platform.current_platform.get()

    platform.async_register_entity_service(
        "smokedetector_check", {}, "async_request_smoketest",
    )


class ShutterContactSensor(SHCEntity, BinarySensorEntity):
    """Representation of a SHC shutter contact sensor."""

    @property
    def is_on(self):
        """Return the state of the sensor."""
        if self._device.state == SHCShutterContact.ShutterContactService.State.OPEN:
            return True
        if self._device.state == SHCShutterContact.ShutterContactService.State.CLOSED:
            return False
        return None

    @property
    def device_class(self):
        """Return the class of this device, from component DEVICE_CLASSES."""
        switcher = {
            SHCShutterContact.DeviceClass.ENTRANCE_DOOR: DEVICE_CLASS_DOOR,
            SHCShutterContact.DeviceClass.REGULAR_WINDOW: DEVICE_CLASS_WINDOW,
            SHCShutterContact.DeviceClass.FRENCH_WINDOW: DEVICE_CLASS_DOOR,
            SHCShutterContact.DeviceClass.GENERIC: DEVICE_CLASS_WINDOW,
        }
        return switcher.get(self._device.device_class, DEVICE_CLASS_WINDOW)


class SmokeDetectorSensor(SHCEntity, BinarySensorEntity):
    """Representation of a SHC smoke detector sensor."""

    def __init__(
        self,
        device: SHCSmokeDetector,
        room_name: str,
        controller_ip: str,
        hass: HomeAssistant,
    ):
        """Initialize the SHC device."""
        super().__init__(
            device=device, room_name=room_name, controller_ip=controller_ip
        )
        self._hass = hass

    @property
    def unique_id(self):
        """Return the unique ID of this sensor."""
        return f"{self._device.serial}"

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

    async def async_request_smoketest(self):
        """Request smokedetector test."""
        _LOGGER.debug("Requesting smoke test on entity %s", self.name)
        await self._hass.async_add_executor_job(self._device.smoketest_requested)

class SmokeDetectorCheckStateSensor(SHCEntity, BinarySensorEntity):
    """Representation of a SHC smoke detector check state sensor."""

    def __init__(
        self,
        device: SHCSmokeDetector,
        room_name: str,
        controller_ip: str,
        hass: HomeAssistant,
    ):
        """Initialize the SHC device."""
        super().__init__(
            device=device, room_name=room_name, controller_ip=controller_ip
        )
        self._hass = hass

    @property
    def unique_id(self):
        """Return the unique ID of this sensor."""
        return f"{self._device.serial}_checkstate"

    @property
    def name(self):
        """Name of the device."""
        return f"{self._device.name} Check State"

    @property
    def is_on(self):
        """Return the state of the sensor."""
        if self._device.smokedetectorcheck_state == SHCSmokeDetector.SmokeDetectorCheckService.State.SMOKE_TEST_OK:
            return True

        return False

    # @property
    # def available(self):
    #     """Return false if status is unavailable."""
    #     if self._device.smokedetectorcheck_state != SHCSmokeDetector.SmokeDetectorCheckService.State.SMOKE_TEST_OK:
    #         return False
    #     return True

    # @property
    # def device_class(self):
    #     """Return the class of this device, from component DEVICE_CLASSES."""
    #     return DEVICE_CLASS_SMOKE

    async def async_request_smoketest(self):
        """Request smokedetector test."""
        _LOGGER.debug("Requesting smoke test on entity %s", self.name)
        await self._hass.async_add_executor_job(self._device.smoketest_requested)
