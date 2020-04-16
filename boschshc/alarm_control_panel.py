"""Platform for alarm control panel integration."""
import asyncio
import logging

from boschshcpy import SHCIntrusionDetectionSystem, SHCSession

from homeassistant.components.alarm_control_panel import (
    FORMAT_NUMBER,
    SUPPORT_ALARM_ARM_AWAY,
    AlarmControlPanel,
)
from homeassistant.const import (
    CONF_IP_ADDRESS,
    STATE_ALARM_ARMED_AWAY,
    STATE_ALARM_ARMING,
    STATE_ALARM_DISARMED,
)

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the alarm control panel platform."""

    session: SHCSession = hass.data[DOMAIN][config_entry.entry_id]
    intrusion_detection_system = session.device_helper.intrusion_detection_system

    device = IntrusionDetectionAlarmControlPanel(
        device=intrusion_detection_system,
        controller_ip=config_entry.data[CONF_IP_ADDRESS],
    )
    # return await async_add_entities([device])
    async_add_entities([device])


class IntrusionDetectionAlarmControlPanel(AlarmControlPanel):
    """Representation of SHC intrusion detection control."""

    def __init__(self, device: SHCIntrusionDetectionSystem, controller_ip: str):
        self._device = device
        self._controller_ip = controller_ip
        self._device_state_attributes = {}

    async def async_added_to_hass(self):
        """Subscribe to SHC events."""
        await super().async_added_to_hass()

        def on_state_changed():
            self.schedule_update_ha_state()

        for service in self._device.device_services:
            service.subscribe_callback(self.entity_id, on_state_changed)

    async def async_will_remove_from_hass(self):
        """Unsubscribe from SHC events."""
        await super().async_will_remove_from_hass()
        for service in self._device.device_services:
            service.unsubscribe_callback(self.entity_id)

    @property
    def unique_id(self):
        """Return the unique ID of this panel."""
        return self._device.serial

    @property
    def device_id(self):
        """Return the ID of this panel."""
        return self._device.id

    @property
    def root_device(self):
        return self._device.root_device_id

    @property
    def name(self):
        """Name of the device."""
        return self._device.name

    @property
    def manufacturer(self):
        """The manufacturer of the device."""
        return self._device.manufacturer

    @property
    def device_info(self):
        """Return the device info."""
        return {
            "identifiers": {(DOMAIN, self.device_id)},
            "name": self.name,
            "manufacturer": self.manufacturer,
            "model": self._device.device_model,
            "sw_version": "",
            "via_device": (DOMAIN, self._controller_ip),
        }

    @property
    def state(self):
        """Return the state of the device."""
        if (
            self._device.alarmstate
            == SHCIntrusionDetectionSystem.IntrusionDetectionControlService.State.SYSTEM_ARMING
        ):
            return STATE_ALARM_ARMING
        elif (
            self._device.alarmstate
            == SHCIntrusionDetectionSystem.IntrusionDetectionControlService.State.SYSTEM_DISARMED
        ):
            return STATE_ALARM_DISARMED
        elif (
            self._device.alarmstate
            == SHCIntrusionDetectionSystem.IntrusionDetectionControlService.State.SYSTEM_ARMED
        ):
            return STATE_ALARM_ARMED_AWAY
        else:
            return None

    @property
    def device_state_attributes(self):
        """Return the state attributes of the device."""
        return self._device_state_attributes

    @property
    def supported_features(self):
        """Return the list of supported features."""
        return SUPPORT_ALARM_ARM_AWAY

    @property
    def manufacturer(self):
        """The manufacturer of the device."""
        return self._device.manufacturer

    @property
    def should_poll(self):
        """Polling needed."""
        return False

    @property
    def code_format(self):
        """Return the regex for code format or None if no code is required."""
        return None
        # return FORMAT_NUMBER

    @property
    def code_arm_required(self):
        """Whether the code is required for arm actions."""
        return False

    def alarm_disarm(self, code=None):
        """Send disarm command."""
        self._device.disarm()

    def alarm_arm_away(self, code=None):
        """Send arm away command."""
        self._device.arm()

    def alarm_trigger(self, code=None):
        """Send trigger/panic command."""
        self._device.trigger()

    def update(self):
        self._device.update()
