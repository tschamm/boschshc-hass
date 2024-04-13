"""Support for Bosch SHC event entities."""

from __future__ import annotations

from boschshcpy import (
    SHCUniversalSwitch,
    SHCMotionDetector,
    SHCSession,
    SHCSmokeDetectionSystem,
    SHCSmokeDetector,
)

from homeassistant.components.event import (
    ENTITY_ID_FORMAT,
    EventDeviceClass,
    EventEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_DEVICE_ID,
    ATTR_ID,
    ATTR_NAME,
)

from homeassistant.helpers.device_registry import DeviceEntry

from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import SHCEntity
from .const import (
    ATTR_LAST_TIME_TRIGGERED,
    ATTR_EVENT_TYPE,
    ATTR_EVENT_SUBTYPE,
    DATA_SESSION,
    DATA_SHC,
    DOMAIN,
    LOGGER,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the BoschSHC event entities."""
    entities = []
    session: SHCSession = hass.data[DOMAIN][entry.entry_id][DATA_SESSION]

    entities = []
    for switch_device in session.device_helper.universal_switches:
        for keystate in switch_device.keystates:
            entities.append(
                UniversalSwitchEvent(
                    switch_device,
                    parent_id=session.information.unique_id,
                    entry_id=entry.entry_id,
                    key_id=keystate,
                )
            )

    for scenario in session.scenarios:
        entities.append(
            SHCScenarioEvent(
                scenario,
                session,
                hass,
                entry_id=entry.entry_id,
                parent_id=session.information.unique_id,
            )
        )

    for motion_detector in session.device_helper.motion_detectors:
        entities.append(
            MotionDetectorEvent(
                device=motion_detector,
                parent_id=session.information.unique_id,
                entry_id=entry.entry_id,
            )
        )

    smoke_detection_system = session.device_helper.smoke_detection_system
    if smoke_detection_system:
        entities.append(
            SmokeDetectionSystemEvent(
                device=smoke_detection_system,
                parent_id=session.information.unique_id,
                entry_id=entry.entry_id,
            )
        )

    for smoke_detector in session.device_helper.smoke_detectors:
        entities.append(
            SmokeDetectorEvent(
                device=smoke_detector,
                parent_id=session.information.unique_id,
                entry_id=entry.entry_id,
            )
        )

    async_add_entities(entities, True)


class UniversalSwitchEvent(SHCEntity, EventEntity):
    """Representation of a SHC UniversalSwitch Entity."""

    _attr_device_class = EventDeviceClass.BUTTON
    _attr_event_types = ["PRESS_SHORT", "PRESS_LONG", "PRESS_LONG_RELEASED"]

    def __init__(
        self, device: SHCUniversalSwitch, parent_id: str, entry_id: str, key_id: str
    ) -> None:
        """Initialize the Universal Switch device."""
        super().__init__(device, parent_id, entry_id)

        self._device = device
        self._key_id = key_id
        self.entity_id = ENTITY_ID_FORMAT.format(f"{self._device.name}_button_{key_id}")

        self._attr_name = f"{self._device.name} Button {key_id}"
        self._attr_unique_id = f"{device.root_device_id}_{device.id}_{key_id}"

    async def async_added_to_hass(self) -> None:
        """Call when entity is added to hass."""
        await super().async_added_to_hass()

        for service in self._device.device_services:
            if service.id == "Keypad":
                service.register_event(self._key_id, self._event_callback)

    def _event_callback(self) -> None:
        event_type = self._device.eventtype.name
        event_attributes = {
            ATTR_DEVICE_ID: self.device_id,
            ATTR_EVENT_TYPE: event_type,
            ATTR_ID: self._device.id,
            ATTR_NAME: self._device.name,
            ATTR_LAST_TIME_TRIGGERED: self._device.eventtimestamp,
        }
        if event_type in ["PRESS_SHORT", "PRESS_LONG", "PRESS_LONG_RELEASED"]:
            try:
                self._trigger_event(event_type, event_attributes)
            except ValueError:
                LOGGER.warning(
                    "Invalid event type %s for %s", event_type, self.entity_id
                )
                return
        self.schedule_update_ha_state()


class SHCScenarioEvent(EventEntity):
    """Representation of a SHC Scenario Entity."""

    _attr_device_class = EventDeviceClass.BUTTON
    _attr_event_types = ["SCENARIO"]

    def __init__(self, scenario, session, hass, entry_id: str, parent_id: str) -> None:
        """Initialize the Scenario device."""

        self._scenario = scenario
        self._session = session
        self.entity_id = ENTITY_ID_FORMAT.format(f"scenario_{self._scenario.name}")

        self._attr_name = f"{self._scenario.name} Scenario"
        self._attr_unique_id = f"{parent_id}_{self._scenario.id}"

        self._shc: DeviceEntry = hass.data[DOMAIN][entry_id][DATA_SHC]

    @property
    def device_name(self):
        """Name of the device."""
        return self._shc.name

    @property
    def device_id(self):
        """Device id of the entity."""
        return self._shc.id

    @property
    def device_info(self):
        """Return the device info."""
        return {
            "identifiers": self._shc.identifiers,
            "name": self._shc.name,
            "manufacturer": self._shc.manufacturer,
            "model": self._shc.model,
            "via_device": self._shc.via_device_id,
        }

    async def async_added_to_hass(self) -> None:
        """Call when entity is added to hass."""
        await super().async_added_to_hass()

        self._session.subscribe_scenario_callback(
            self._scenario.id, self._event_callback
        )

    def _event_callback(self, event_data) -> None:
        event_type = "SCENARIO"
        event_attributes = {
            ATTR_EVENT_TYPE: event_type,
            ATTR_ID: event_data["id"],
            ATTR_NAME: event_data["name"],
            ATTR_LAST_TIME_TRIGGERED: event_data["lastTimeTriggered"],
        }
        self._trigger_event(event_type, event_attributes)
        self.schedule_update_ha_state()


class MotionDetectorEvent(SHCEntity, EventEntity):
    """Representation of a SHC MotionDetector Entity."""

    _attr_device_class = EventDeviceClass.MOTION
    _attr_event_types = ["MOTION"]

    def __init__(
        self, device: SHCMotionDetector, parent_id: str, entry_id: str
    ) -> None:
        """Initialize the Universal Switch device."""
        super().__init__(device, parent_id, entry_id)
        self._device = device

    async def async_added_to_hass(self) -> None:
        """Call when entity is added to hass."""
        await super().async_added_to_hass()

        for service in self._device.device_services:
            if service.id == "LatestMotion":
                service.register_event(self._device.id, self._event_callback)

    def _event_callback(self) -> None:
        event_type = "MOTION"
        event_attributes = {
            ATTR_DEVICE_ID: self.device_id,
            ATTR_EVENT_TYPE: event_type,
            ATTR_ID: self._device.id,
            ATTR_NAME: self._device.name,
            ATTR_LAST_TIME_TRIGGERED: self._device.latestmotion,
        }
        self._trigger_event(event_type, event_attributes)
        self.schedule_update_ha_state()


class SmokeDetectionSystemEvent(SHCEntity, EventEntity):
    """Representation of a SHC smoke detection system event entity."""

    _attr_event_types = ["ALARM"]

    def __init__(
        self,
        device: SHCSmokeDetectionSystem,
        parent_id: str,
        entry_id: str,
    ):
        """Initialize the smoke detection system device."""
        super().__init__(device=device, parent_id=parent_id, entry_id=entry_id)
        self._attr_unique_id = f"{device.root_device_id}_{device.id}"

    async def async_added_to_hass(self) -> None:
        """Call when entity is added to hass."""
        await super().async_added_to_hass()

        for service in self._device.device_services:
            if service.id == "SurveillanceAlarm":
                service.register_event(self._device.id, self._event_callback)

    def _event_callback(self) -> None:
        event_type = "ALARM"
        event_attributes = {
            ATTR_DEVICE_ID: self.device_id,
            ATTR_EVENT_TYPE: event_type,
            ATTR_EVENT_SUBTYPE: self._device.alarm.name,
            ATTR_ID: self._device.id,
            ATTR_NAME: self._device.name,
        }
        self._trigger_event(event_type, event_attributes)
        self.schedule_update_ha_state()


class SmokeDetectorEvent(SHCEntity, EventEntity):
    """Representation of a SHC smoke detector event entity."""

    _attr_event_types = ["ALARM"]

    def __init__(
        self,
        device: SHCSmokeDetector,
        parent_id: str,
        entry_id: str,
    ):
        """Initialize the smoke detection system device."""
        super().__init__(device=device, parent_id=parent_id, entry_id=entry_id)
        self._attr_unique_id = f"{device.root_device_id}_{device.id}"

    async def async_added_to_hass(self) -> None:
        """Call when entity is added to hass."""
        await super().async_added_to_hass()

        for service in self._device.device_services:
            if service.id == "Alarm":
                service.register_event(self._device.id, self._event_callback)

    def _event_callback(self) -> None:
        event_type = "ALARM"
        event_attributes = {
            ATTR_DEVICE_ID: self.device_id,
            ATTR_EVENT_TYPE: event_type,
            ATTR_EVENT_SUBTYPE: self._device.alarmstate.name,
            ATTR_ID: self._device.id,
            ATTR_NAME: self._device.name,
        }
        self._trigger_event(event_type, event_attributes)
        self.schedule_update_ha_state()
