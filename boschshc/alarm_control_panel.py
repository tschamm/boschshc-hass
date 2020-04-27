"""Platform for alarm control panel integration."""
import asyncio
import logging

from boschshcpy import SHCIntrusionDetectionSystem, SHCSession

from homeassistant.components.alarm_control_panel import (
    FORMAT_NUMBER,
    SUPPORT_ALARM_ARM_AWAY,
    AlarmControlPanelEntity,
)
from homeassistant.const import (
    CONF_IP_ADDRESS,
    STATE_ALARM_ARMED_AWAY,
    STATE_ALARM_ARMING,
    STATE_ALARM_DISARMED,
)

from .const import DOMAIN
from .entity import SHCEntity

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


class IntrusionDetectionAlarmControlPanel(SHCEntity, AlarmControlPanelEntity):
    """Representation of SHC intrusion detection control."""

    def __init__(self, device: SHCIntrusionDetectionSystem, controller_ip: str):
        super().__init__(device=device, room_name=None, controller_ip=controller_ip)
        self._device_state_attributes = {}

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
