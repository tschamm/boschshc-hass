"""Platform for alarm control panel integration."""
from boschshcpy import SHCIntrusionSystem, SHCSession
from homeassistant.components.alarm_control_panel import AlarmControlPanelEntity
from homeassistant.components.alarm_control_panel.const import (
    AlarmControlPanelEntityFeature,
)
from homeassistant.const import (
    STATE_ALARM_ARMED_AWAY,
    STATE_ALARM_ARMED_CUSTOM_BYPASS,
    STATE_ALARM_ARMED_HOME,
    STATE_ALARM_ARMING,
    STATE_ALARM_DISARMED,
    STATE_ALARM_PENDING,
    STATE_ALARM_TRIGGERED,
    Platform,
)

from .const import DATA_SESSION, DOMAIN
from .entity import async_migrate_to_new_unique_id


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the alarm control panel platform."""
    devices = []

    session: SHCSession = hass.data[DOMAIN][config_entry.entry_id][DATA_SESSION]

    intrusion_system = session.intrusion_system
    await async_migrate_to_new_unique_id(
        hass,
        Platform.ALARM_CONTROL_PANEL,
        device=intrusion_system,
        attr_name=None,
        old_unique_id=f"{config_entry.entry_id}_{intrusion_system.id}",
    )
    alarm_control_panel = IntrusionSystemAlarmControlPanel(
        device=intrusion_system,
        parent_id=session.information.unique_id,
        entry_id=config_entry.entry_id,
    )
    devices.append(alarm_control_panel)

    async_add_entities(devices)


class IntrusionSystemAlarmControlPanel(AlarmControlPanelEntity):
    """Representation of SHC intrusion detection control."""

    def __init__(self, device: SHCIntrusionSystem, parent_id: str, entry_id: str):
        """Initialize the intrusion detection control."""
        self._device = device
        self._parent_id = parent_id
        self._entry_id = entry_id
        self._attr_unique_id = f"{self._device.root_device_id}_{self._device.id}"

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
        if self._device.alarm_state == SHCIntrusionSystem.AlarmState.ALARM_ON:
            return STATE_ALARM_TRIGGERED
        if self._device.alarm_state == SHCIntrusionSystem.AlarmState.PRE_ALARM:
            return STATE_ALARM_PENDING

        if self._device.arming_state == SHCIntrusionSystem.ArmingState.SYSTEM_ARMING:
            return STATE_ALARM_ARMING
        if self._device.arming_state == SHCIntrusionSystem.ArmingState.SYSTEM_DISARMED:
            return STATE_ALARM_DISARMED

        if self._device.arming_state == SHCIntrusionSystem.ArmingState.SYSTEM_ARMED:
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
        return (
            AlarmControlPanelEntityFeature.ARM_AWAY
            + AlarmControlPanelEntityFeature.ARM_HOME
            + AlarmControlPanelEntityFeature.ARM_CUSTOM_BYPASS
        )

    @property
    def manufacturer(self):
        """Return manufacturer of the device."""
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
