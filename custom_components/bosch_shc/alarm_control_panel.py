"""Platform for alarm control panel integration."""

from __future__ import annotations

from typing import Any

from boschshcpy import SHCIntrusionSystem, SHCSession
from boschshcpy.exceptions import SHCException
from homeassistant.components.alarm_control_panel import AlarmControlPanelEntity
from homeassistant.components.alarm_control_panel.const import (
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import async_migrate_to_new_unique_id

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the alarm control panel platform."""
    devices = []

    session: SHCSession = config_entry.runtime_data.session

    intrusion_system = session.intrusion_system
    await async_migrate_to_new_unique_id(
        hass,
        Platform.ALARM_CONTROL_PANEL,
        device=intrusion_system,  # type: ignore[arg-type]
        attr_name=None,
        old_unique_id=f"{config_entry.entry_id}_{intrusion_system.id}",
    )
    alarm_control_panel = IntrusionSystemAlarmControlPanel(
        device=intrusion_system,
        entry_id=config_entry.entry_id,
    )
    devices.append(alarm_control_panel)

    async_add_entities(devices)


class IntrusionSystemAlarmControlPanel(AlarmControlPanelEntity):  # type: ignore[misc]
    """Representation of SHC intrusion detection control."""

    _attr_has_entity_name = True
    _attr_name = None  # primary entity — HA uses the device name as the entity name

    def __init__(self, device: SHCIntrusionSystem, entry_id: str) -> None:
        """Initialize the intrusion detection control."""
        self._device = device
        self._entry_id = entry_id
        self._attr_unique_id = f"{self._device.root_device_id}_{self._device.id}"

    async def async_added_to_hass(self) -> None:
        """Subscribe to SHC events."""
        await super().async_added_to_hass()

        def on_state_changed() -> None:
            self.schedule_update_ha_state()

        self._device.subscribe_callback(self.entity_id, on_state_changed)

    async def async_will_remove_from_hass(self) -> None:
        """Unsubscribe from SHC events."""
        await super().async_will_remove_from_hass()
        self._device.unsubscribe_callback(self.entity_id)

    @property
    def device_id(self) -> str:
        """Return the ID of the system."""
        return self._device.id

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device info."""
        info = DeviceInfo(
            identifiers={(DOMAIN, self._device.id)},
            name=self._device.name,
            manufacturer=self._device.manufacturer,
            model=self._device.device_model,
        )
        root_device_id = self._device.root_device_id
        if root_device_id is not None:
            info["via_device"] = (DOMAIN, root_device_id)
        return info

    @property
    def available(self) -> bool:
        """Return false if status is unavailable."""
        return bool(self._device.system_availability)

    @property
    def should_poll(self) -> bool:
        """Report polling mode. System is communicating via long polling."""
        return False

    @property
    def alarm_state(self) -> AlarmControlPanelState | None:
        """Return the state of the device."""
        if self._device.alarm_state == SHCIntrusionSystem.AlarmState.ALARM_ON:
            return AlarmControlPanelState.TRIGGERED
        if self._device.alarm_state == SHCIntrusionSystem.AlarmState.ALARM_MUTED:
            return AlarmControlPanelState.TRIGGERED
        if self._device.alarm_state == SHCIntrusionSystem.AlarmState.PRE_ALARM:
            return AlarmControlPanelState.PENDING

        if self._device.arming_state == SHCIntrusionSystem.ArmingState.SYSTEM_ARMING:
            return AlarmControlPanelState.ARMING
        if self._device.arming_state == SHCIntrusionSystem.ArmingState.SYSTEM_DISARMED:
            return AlarmControlPanelState.DISARMED

        if self._device.arming_state == SHCIntrusionSystem.ArmingState.SYSTEM_ARMED:
            if (
                self._device.active_configuration_profile
                == SHCIntrusionSystem.Profile.FULL_PROTECTION
            ):
                return AlarmControlPanelState.ARMED_AWAY

            if (
                self._device.active_configuration_profile
                == SHCIntrusionSystem.Profile.PARTIAL_PROTECTION
            ):
                return AlarmControlPanelState.ARMED_HOME

            if (
                self._device.active_configuration_profile
                == SHCIntrusionSystem.Profile.CUSTOM_PROTECTION
            ):
                return AlarmControlPanelState.ARMED_CUSTOM_BYPASS
        return None

    @property
    def supported_features(self) -> AlarmControlPanelEntityFeature:
        """Return the list of supported features."""
        return (
            AlarmControlPanelEntityFeature.ARM_AWAY
            | AlarmControlPanelEntityFeature.ARM_HOME
            | AlarmControlPanelEntityFeature.ARM_CUSTOM_BYPASS
        )

    @property
    def manufacturer(self) -> str:
        """Return manufacturer of the device."""
        return str(self._device.manufacturer)

    @property
    def code_format(self) -> None:
        """Return the regex for code format or None if no code is required."""
        return None
        # return FORMAT_NUMBER

    @property
    def code_arm_required(self) -> bool:
        """Whether the code is required for arm actions."""
        return False

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        """Send disarm command."""
        try:
            await self._device.async_disarm()
        except SHCException as err:
            raise HomeAssistantError(
                f"Disarm failed for {self._device.name}: {err}",
                translation_domain=DOMAIN,
                translation_key="alarm_state_failed",
            ) from err

    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        """Send arm away command."""
        try:
            await self._device.async_arm_full_protection()
        except SHCException as err:
            raise HomeAssistantError(
                f"Arm away failed for {self._device.name}: {err}",
                translation_domain=DOMAIN,
                translation_key="alarm_state_failed",
            ) from err

    async def async_alarm_arm_home(self, code: str | None = None) -> None:
        """Send arm home command."""
        try:
            await self._device.async_arm_partial_protection()
        except SHCException as err:
            raise HomeAssistantError(
                f"Arm home failed for {self._device.name}: {err}",
                translation_domain=DOMAIN,
                translation_key="alarm_state_failed",
            ) from err

    async def async_alarm_arm_custom_bypass(self, code: str | None = None) -> None:
        """Send arm individual protection (custom bypass) command."""
        try:
            await self._device.async_arm_individual_protection()
        except SHCException as err:
            raise HomeAssistantError(
                f"Arm custom bypass failed for {self._device.name}: {err}",
                translation_domain=DOMAIN,
                translation_key="alarm_state_failed",
            ) from err

    async def async_alarm_mute(self) -> None:
        """Mute alarm command.

        Note: HA core's AlarmControlPanelEntity has no alarm_mute hook /
        feature flag, so this is currently unreachable from the UI/services —
        kept for programmatic/future use, not a bug in itself.
        """
        try:
            await self._device.async_mute()
        except SHCException as err:
            raise HomeAssistantError(
                f"Mute failed for {self._device.name}: {err}",
                translation_domain=DOMAIN,
                translation_key="alarm_state_failed",
            ) from err

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional IDS state attributes.

        Exposes alarm_state_incidents, security_gaps, and remaining_time_until_armed
        from the SHCIntrusionSystem domain model.
        """
        return {
            "incidents": self._device.alarm_state_incidents,
            "security_gaps": self._device.security_gaps,
            "remaining_time_until_armed": self._device.remaining_time_until_armed,
        }
