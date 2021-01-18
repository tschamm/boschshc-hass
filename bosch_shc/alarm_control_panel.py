"""Platform for alarm control panel integration."""
import logging

from boschshcpy import SHCIntrusionDetectionSystem, SHCSession
from homeassistant.components.alarm_control_panel import (
    SUPPORT_ALARM_ARM_AWAY,
    AlarmControlPanelEntity,
)
from homeassistant.components.alarm_control_panel.const import SUPPORT_ALARM_ARM_HOME
from homeassistant.const import (
    STATE_ALARM_ARMED_AWAY,
    STATE_ALARM_ARMED_CUSTOM_BYPASS,
    STATE_ALARM_ARMED_HOME,
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

    alarm_control_panel = IntrusionDetectionAlarmControlPanel(
        device=intrusion_detection_system,
        parent_id=session.information.name,
        entry_id=config_entry.entry_id,
    )
    # return await async_add_entities([device])
    async_add_entities([alarm_control_panel])


class IntrusionDetectionAlarmControlPanel(SHCEntity, AlarmControlPanelEntity):
    """Representation of SHC intrusion detection control."""

    def __init__(
        self, device: SHCIntrusionDetectionSystem, parent_id: str, entry_id: str
    ):
        super().__init__(device=device, parent_id=parent_id, entry_id=entry_id)
        self._device_state_attributes = {}

    @property
    def state(self):
        """Return the state of the device."""
        if (
            self._device.alarmstate
            == SHCIntrusionDetectionSystem.IntrusionDetectionControlService.State.SYSTEM_ARMING
        ):
            return STATE_ALARM_ARMING
        if (
            self._device.alarmstate
            == SHCIntrusionDetectionSystem.IntrusionDetectionControlService.State.SYSTEM_DISARMED
        ):
            return STATE_ALARM_DISARMED
        if (
            self._device.alarmstate
            == SHCIntrusionDetectionSystem.IntrusionDetectionControlService.State.SYSTEM_ARMED
        ):
            if (
                self._device.alarmprofile
                == SHCIntrusionDetectionSystem.IntrusionDetectionControlService.Profile.FULL_PROTECTION
            ):
                return STATE_ALARM_ARMED_AWAY

            if (
                self._device.alarmprofile
                == SHCIntrusionDetectionSystem.IntrusionDetectionControlService.Profile.PARTIAL_PROTECTION
            ):
                return STATE_ALARM_ARMED_HOME

            if (
                self._device.alarmprofile
                == SHCIntrusionDetectionSystem.IntrusionDetectionControlService.Profile.CUSTOM_PROTECTION
            ):
                return STATE_ALARM_ARMED_CUSTOM_BYPASS
        return None

    @property
    def device_state_attributes(self):
        """Return the state attributes of the device."""
        return self._device_state_attributes

    @property
    def supported_features(self):
        """Return the list of supported features."""
        return SUPPORT_ALARM_ARM_AWAY + SUPPORT_ALARM_ARM_HOME

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

    def alarm_arm_home(self, code=None):
        """Send arm home command."""
        self._device.partial_arm()

    def alarm_trigger(self, code=None):
        """Send trigger/panic command."""
        self._device.trigger()
