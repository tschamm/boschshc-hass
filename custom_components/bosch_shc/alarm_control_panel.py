"""Platform for alarm control panel integration."""
import logging

from boschshcpy import SHCIntrusionDetectionSystem, SHCIntrusionSystem, SHCSession
from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
)
from homeassistant.components.alarm_control_panel.const import SUPPORT_ALARM_ARM_HOME, SUPPORT_ALARM_ARM_CUSTOM_BYPASS, SUPPORT_ALARM_ARM_AWAY
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

    devices = []

    session: SHCSession = hass.data[DOMAIN][config_entry.entry_id]

    intrusion_detection_system = session.device_helper.intrusion_detection_system
    alarm_control_panel = IntrusionDetectionAlarmControlPanel(
        device=intrusion_detection_system,
        parent_id=session.information.name,
        entry_id=config_entry.entry_id,
    )
    devices.append(alarm_control_panel)

    intrusion_system = session.intrusion_system
    alarm_control_panel = IntrusionSystemAlarmControlPanel(
        device=intrusion_system,
        parent_id=session.information.name,
        entry_id=config_entry.entry_id,
    )
    devices.append(alarm_control_panel)

    async_add_entities(devices)


class IntrusionDetectionAlarmControlPanel(SHCEntity, AlarmControlPanelEntity):
    """Representation of SHC intrusion detection control."""

    def __init__(
        self, device: SHCIntrusionDetectionSystem, parent_id: str, entry_id: str
    ):
        super().__init__(device=device, parent_id=parent_id, entry_id=entry_id)
        self._device_state_attributes = {}
        _LOGGER.warning("intrusion detection system deprecated in API version 2.1. The Bosch Smart Home Controller update scheduled for May 2021 will no longer support API 1.x.")

    @property
    def name(self):
        """Return the name of this sensor."""
        return f"{self._device.name} (Deprecated)"

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

class IntrusionSystemAlarmControlPanel(AlarmControlPanelEntity):
    """Representation of SHC intrusion detection control."""

    def __init__(
        self, device: SHCIntrusionSystem, parent_id: str, entry_id: str
    ):
        self._device = device
        self._parent_id = parent_id
        self._entry_id = entry_id

    async def async_added_to_hass(self):
        """Subscribe to SHC events."""
        await super().async_added_to_hass()

        def on_state_changed():
            self.schedule_update_ha_state()

        self._device.subscribe_callback(self.entity_id, on_state_changed)

    async def async_will_remove_from_hass(self):
        """Unsubscribe from SHC events."""
        await super().async_will_remove_from_hass()
        self._device.unsubscribe_callback(self.entity_id)

    @property
    def unique_id(self):
        """Return the unique ID of the system."""
        return self._device.id

    @property
    def name(self):
        """Name of the entity."""
        return self._device.name

    @property
    def device_id(self):
        """Return the ID of the system."""
        return self._device.id

    @property
    def device_info(self):
        """Return the device info."""
        return {
            "identifiers": {(DOMAIN, self._device.id)},
            "name": self._device.name,
            "manufacturer": self._device.manufacturer,
            "model": self._device.device_model,
            "via_device": (
                DOMAIN,
                self._parent_id,
            ),
        }

    @property
    def available(self):
        """Return false if status is unavailable."""
        return self._device.system_availability

    @property
    def should_poll(self):
        """Report polling mode. System is communicating via long polling."""
        return False

    @property
    def state(self):
        """Return the state of the device."""
        if (
            self._device.arming_state
            == SHCIntrusionSystem.ArmingState.SYSTEM_ARMING
        ):
            return STATE_ALARM_ARMING
        if (
            self._device.arming_state
            == SHCIntrusionSystem.ArmingState.SYSTEM_DISARMED
        ):
            return STATE_ALARM_DISARMED
        if (
            self._device.arming_state
            == SHCIntrusionSystem.ArmingState.SYSTEM_ARMED
        ):
            if (
                self._device.active_configuration_profile
                == SHCIntrusionSystem.Profile.FULL_PROTECTION
            ):
                return STATE_ALARM_ARMED_AWAY

            if (
                self._device.active_configuration_profile
                == SHCIntrusionSystem.Profile.PARTIAL_PROTECTION
            ):
                return STATE_ALARM_ARMED_HOME

            if (
                self._device.active_configuration_profile
                == SHCIntrusionSystem.Profile.CUSTOM_PROTECTION
            ):
                return STATE_ALARM_ARMED_CUSTOM_BYPASS
        return None

    @property
    def supported_features(self):
        """Return the list of supported features."""
        return SUPPORT_ALARM_ARM_AWAY + SUPPORT_ALARM_ARM_HOME + SUPPORT_ALARM_ARM_CUSTOM_BYPASS

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
        self._device.arm_full_protection()

    def alarm_arm_home(self, code=None):
        """Send arm home command."""
        self._device.arm_partial_protection()

    def alarm_arm_custom_bypass(self, code=None):
        """Send arm home command."""
        self._device.arm_individual_protection()

    def alarm_mute(self):
        """Mute alarm command."""
        self._device.mute()
