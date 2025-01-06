"""Platform for switch integration."""

from __future__ import annotations

from dataclasses import dataclass

from boschshcpy import (
    SHCCamera360,
    SHCCameraEyes,
    SHCLightSwitch,
    SHCSession,
    SHCSmartPlug,
    SHCMicromoduleRelay,
    SHCSmartPlugCompact,
    SHCShutterContact2,
    SHCShutterContact2Plus,
    SHCThermostat,
    SHCUserDefinedState,
)
from boschshcpy.device import SHCDevice

from homeassistant.components.switch import (
    ENTITY_ID_FORMAT,
    SwitchDeviceClass,
    SwitchEntity,
    SwitchEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType

from .const import DATA_SESSION, DOMAIN, DATA_SHC
from .entity import SHCEntity, async_migrate_to_new_unique_id


@dataclass
class SHCSwitchRequiredKeysMixin:
    """Mixin for SHC switch required keys."""

    on_key: str
    on_value: StateType
    should_poll: bool


@dataclass
class SHCSwitchEntityDescription(
    SwitchEntityDescription,
    SHCSwitchRequiredKeysMixin,
):
    """Class describing SHC switch entities."""


SWITCH_TYPES: dict[str, SHCSwitchEntityDescription] = {
    "smartplug": SHCSwitchEntityDescription(
        key="smartplug",
        device_class=SwitchDeviceClass.OUTLET,
        on_key="switchstate",
        on_value=SHCSmartPlug.PowerSwitchService.State.ON,
        should_poll=False,
    ),
    "smartplug_routing": SHCSwitchEntityDescription(
        key="smartplug_routing",
        device_class=SwitchDeviceClass.SWITCH,
        on_key="routing",
        on_value=SHCSmartPlug.RoutingService.State.ENABLED,
        should_poll=False,
        entity_category=EntityCategory.CONFIG,
        icon="mdi:wifi",
    ),
    "smartplugcompact": SHCSwitchEntityDescription(
        key="smartplugcompact",
        device_class=SwitchDeviceClass.OUTLET,
        on_key="switchstate",
        on_value=SHCSmartPlugCompact.PowerSwitchService.State.ON,
        should_poll=False,
    ),
    "micromodule_relay_switch": SHCSwitchEntityDescription(
        key="micromodule_relay_switch",
        device_class=SwitchDeviceClass.OUTLET,
        on_key="switchstate",
        on_value=SHCMicromoduleRelay.PowerSwitchService.State.ON,
        should_poll=False,
    ),
    "lightswitch": SHCSwitchEntityDescription(
        key="lightswitch",
        device_class=SwitchDeviceClass.SWITCH,
        on_key="switchstate",
        on_value=SHCLightSwitch.PowerSwitchService.State.ON,
        should_poll=False,
    ),
    "cameraeyes": SHCSwitchEntityDescription(
        key="cameraeyes",
        device_class=SwitchDeviceClass.SWITCH,
        on_key="privacymode",
        on_value=SHCCameraEyes.PrivacyModeService.State.DISABLED,
        should_poll=True,
        icon="mdi:video",
    ),
    "cameraeyes_cameralight": SHCSwitchEntityDescription(
        key="cameraeyes_cameralight",
        device_class=SwitchDeviceClass.SWITCH,
        on_key="cameralight",
        on_value=SHCCameraEyes.CameraLightService.State.ON,
        should_poll=True,
        entity_category=EntityCategory.CONFIG,
        icon="mdi:light-flood-down",
    ),
    "cameraeyes_notification": SHCSwitchEntityDescription(
        key="cameraeyes_notification",
        device_class=SwitchDeviceClass.SWITCH,
        on_key="cameranotification",
        on_value=SHCCameraEyes.CameraNotificationService.State.ENABLED,
        should_poll=True,
        entity_category=EntityCategory.CONFIG,
        icon="mdi:message-badge",
    ),
    "camera360": SHCSwitchEntityDescription(
        key="camera360",
        device_class=SwitchDeviceClass.SWITCH,
        on_key="privacymode",
        on_value=SHCCamera360.PrivacyModeService.State.DISABLED,
        should_poll=True,
        icon="mdi:video",
    ),
    "camera360_notification": SHCSwitchEntityDescription(
        key="camera360_notification",
        device_class=SwitchDeviceClass.SWITCH,
        on_key="cameranotification",
        on_value=SHCCamera360.CameraNotificationService.State.ENABLED,
        should_poll=True,
        entity_category=EntityCategory.CONFIG,
        icon="mdi:message-badge",
    ),
    "presencesimulation": SHCSwitchEntityDescription(
        key="presencesimulation",
        device_class=SwitchDeviceClass.SWITCH,
        on_key="enabled",
        on_value=True,
        should_poll=False,
    ),
    "bypass": SHCSwitchEntityDescription(
        key="bypass",
        device_class=SwitchDeviceClass.SWITCH,
        on_key="bypass",
        on_value=SHCShutterContact2.BypassService.State.BYPASS_ACTIVE,
        should_poll=False,
    ),
    "child_lock": SHCSwitchEntityDescription(
        key="child_lock",
        device_class=SwitchDeviceClass.SWITCH,
        on_key="child_lock",
        on_value=SHCThermostat.ThermostatService.State.ON,
        entity_category=EntityCategory.CONFIG,
        should_poll=False,
        icon="mdi:lock",
    ),
    "silent_mode": SHCSwitchEntityDescription(
        key="silent_mode",
        device_class=SwitchDeviceClass.SWITCH,
        on_key="silentmode",
        on_value=SHCThermostat.SilentModeService.State.MODE_SILENT,
        entity_category=EntityCategory.CONFIG,
        should_poll=False,
        icon="mdi:sleep",
    ),
    "vibration_enabled": SHCSwitchEntityDescription(
        key="vibration_enabled",
        device_class=SwitchDeviceClass.SWITCH,
        on_key="enabled",
        on_value=True,
        entity_category=EntityCategory.CONFIG,
        should_poll=False,
    ),
    "user_defined_state": SHCSwitchEntityDescription(
        key="user_defined_state",
        device_class=SwitchDeviceClass.SWITCH,
        on_key="state",
        on_value=True,
        entity_category=EntityCategory.CONFIG,
        should_poll=False,
    ),
}


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the SHC switch platform."""
    entities: list[SwitchEntity] = []
    session: SHCSession = hass.data[DOMAIN][config_entry.entry_id][DATA_SESSION]

    for switch in session.device_helper.smart_plugs:
        await async_migrate_to_new_unique_id(
            hass=hass, platform=Platform.SWITCH, device=switch
        )
        entities.append(
            SHCSwitch(
                device=switch,
                entry_id=config_entry.entry_id,
                description=SWITCH_TYPES["smartplug"],
            )
        )
        await async_migrate_to_new_unique_id(
            hass=hass, platform=Platform.SWITCH, device=switch, attr_name="Routing"
        )
        entities.append(
            SHCSwitch(
                device=switch,
                entry_id=config_entry.entry_id,
                description=SWITCH_TYPES["smartplug_routing"],
                attr_name="Routing",
            )
        )

    for switch in (
        session.device_helper.light_switches_bsm
        + session.device_helper.micromodule_light_attached
    ):
        await async_migrate_to_new_unique_id(
            hass=hass, platform=Platform.SWITCH, device=switch
        )
        entities.append(
            SHCSwitch(
                device=switch,
                entry_id=config_entry.entry_id,
                description=SWITCH_TYPES["lightswitch"],
            )
        )

    for switch in session.device_helper.smart_plugs_compact:
        await async_migrate_to_new_unique_id(
            hass=hass, platform=Platform.SWITCH, device=switch
        )
        entities.append(
            SHCSwitch(
                device=switch,
                entry_id=config_entry.entry_id,
                description=SWITCH_TYPES["smartplugcompact"],
            )
        )

    for switch in session.device_helper.micromodule_relays:
        await async_migrate_to_new_unique_id(
            hass=hass, platform=Platform.SWITCH, device=switch
        )
        entities.append(
            SHCSwitch(
                device=switch,
                entry_id=config_entry.entry_id,
                description=SWITCH_TYPES["micromodule_relay_switch"],
            )
        )

    for switch in session.device_helper.camera_eyes:
        await async_migrate_to_new_unique_id(
            hass=hass,
            platform=Platform.SWITCH,
            device=switch,
        )
        entities.append(
            SHCSwitch(
                device=switch,
                entry_id=config_entry.entry_id,
                description=SWITCH_TYPES["cameraeyes"],
            )
        )
        await async_migrate_to_new_unique_id(
            hass=hass, platform=Platform.SWITCH, device=switch, attr_name="Light"
        )
        entities.append(
            SHCSwitch(
                device=switch,
                entry_id=config_entry.entry_id,
                description=SWITCH_TYPES["cameraeyes_cameralight"],
                attr_name="Light",
            )
        )
        await async_migrate_to_new_unique_id(
            hass=hass, platform=Platform.SWITCH, device=switch, attr_name="Notification"
        )
        entities.append(
            SHCSwitch(
                device=switch,
                entry_id=config_entry.entry_id,
                description=SWITCH_TYPES["cameraeyes_notification"],
                attr_name="Notification",
            )
        )

    for switch in session.device_helper.camera_360:
        await async_migrate_to_new_unique_id(
            hass=hass, platform=Platform.SWITCH, device=switch
        )
        entities.append(
            SHCSwitch(
                device=switch,
                entry_id=config_entry.entry_id,
                description=SWITCH_TYPES["camera360"],
            )
        )
        await async_migrate_to_new_unique_id(
            hass=hass, platform=Platform.SWITCH, device=switch, attr_name="Notification"
        )
        entities.append(
            SHCSwitch(
                device=switch,
                entry_id=config_entry.entry_id,
                description=SWITCH_TYPES["camera360_notification"],
                attr_name="Notification",
            )
        )

    presence_simulation_system = session.device_helper.presence_simulation_system
    if presence_simulation_system:
        await async_migrate_to_new_unique_id(
            hass=hass,
            platform=Platform.SWITCH,
            device=presence_simulation_system,
        )
        entities.append(
            SHCSwitch(
                device=presence_simulation_system,
                entry_id=config_entry.entry_id,
                description=SWITCH_TYPES["presencesimulation"],
            )
        )

    for switch in session.device_helper.shutter_contacts2:
        await async_migrate_to_new_unique_id(
            hass=hass, platform=Platform.SWITCH, device=switch
        )
        entities.append(
            SHCSwitch(
                device=switch,
                entry_id=config_entry.entry_id,
                description=SWITCH_TYPES["bypass"],
            )
        )
        if isinstance(switch, SHCShutterContact2Plus):
            entities.append(
                SHCSwitch(
                    device=switch,
                    entry_id=config_entry.entry_id,
                    description=SWITCH_TYPES["vibration_enabled"],
                    attr_name="VibrationEnabled",
                )
            )

    for switch in session.device_helper.thermostats:
        if switch.supports_silentmode:
            entities.append(
                SHCSwitch(
                    device=switch,
                    entry_id=config_entry.entry_id,
                    description=SWITCH_TYPES["silent_mode"],
                    attr_name="SilentMode",
                )
            )

    for switch in (
        session.device_helper.thermostats
        + session.device_helper.roomthermostats
        + session.device_helper.micromodule_shutter_controls
        + session.device_helper.micromodule_blinds
        + session.device_helper.micromodule_light_attached
        + session.device_helper.micromodule_relays
        + session.device_helper.micromodule_impulse_relays
    ):
        entities.append(
            SHCSwitch(
                device=switch,
                entry_id=config_entry.entry_id,
                description=SWITCH_TYPES["child_lock"],
                attr_name="ChildLock",
            )
        )

    if entities:
        async_add_entities(entities)

    @callback
    def async_add_userdefinedstateswitch(
        switch: SHCUserDefinedStateSwitch,
    ) -> None:
        """Add User Defined State Switch."""
        switch = SHCUserDefinedStateSwitch(
            device=switch,
            hass=hass,
            session=session,
            entry_id=config_entry.entry_id,
            description=SWITCH_TYPES["user_defined_state"],
        )
        async_add_entities([switch])

    # add all current items in session
    for switch in session.userdefinedstates:
        async_add_userdefinedstateswitch(switch=switch)

    # register listener for new switches
    config_entry.async_on_unload(
        config_entry.add_update_listener(  # This likely needs a call_soon_threadsafe as calling into async_add_userdefinedstateswitch must be called from the event loop.
            session.subscribe((SHCUserDefinedState, async_add_userdefinedstateswitch))
        )
    )


class SHCSwitch(SHCEntity, SwitchEntity):
    """Representation of a SHC switch."""

    entity_description: SHCSwitchEntityDescription

    def __init__(
        self,
        device: SHCDevice,
        entry_id: str,
        description: SHCSwitchEntityDescription,
        attr_name: str | None = None,
    ) -> None:
        """Initialize a SHC switch."""
        super().__init__(device, entry_id)
        self.entity_description = description
        self._attr_name = (
            f"{device.name}" if attr_name is None else f"{device.name} {attr_name}"
        )
        self._attr_unique_id = (
            f"{device.root_device_id}_{device.id}"
            if attr_name is None
            else f"{device.root_device_id}_{device.id}_{attr_name.lower()}"
        )

    @property
    def is_on(self) -> bool:
        """Return the state of the switch."""
        return (
            getattr(self._device, self.entity_description.on_key)
            == self.entity_description.on_value
        )

    def turn_on(self, **kwargs) -> None:
        """Turn the switch on."""
        setattr(self._device, self.entity_description.on_key, True)

    def turn_off(self, **kwargs) -> None:
        """Turn the switch off."""
        setattr(self._device, self.entity_description.on_key, False)

    @property
    def should_poll(self) -> bool:
        """Switch needs polling."""
        return self.entity_description.should_poll

    def update(self) -> None:
        """Trigger an update of the device."""
        self._device.update()


class SHCUserDefinedStateSwitch(SwitchEntity):
    """Representation of a SHC User Defined State Entity."""

    entity_description: SHCSwitchEntityDescription

    def __init__(
        self,
        device: SHCUserDefinedState,
        hass: HomeAssistant,
        session: SHCSession,
        entry_id: str,
        description: SHCSwitchEntityDescription,
        attr_name: str | None = None,
    ) -> None:
        """Initialize a SHC switch."""
        self._device = device
        self._session = session
        self._entry_id = entry_id
        self.entity_description = description
        self._attr_name = (
            f"{device.name}" if attr_name is None else f"{device.name} {attr_name}"
        )
        self.entity_id = ENTITY_ID_FORMAT.format(
            f"userdefinedstate_{self._device.name}"
        )
        self._attr_unique_id = (
            f"{device.root_device_id}_{device.id}"
            if attr_name is None
            else f"{device.root_device_id}_{device.id}_{attr_name.lower()}"
        )
        self._shc: DeviceEntry = hass.data[DOMAIN][entry_id][DATA_SHC]

    async def async_added_to_hass(self):
        """Subscribe to SHC events."""
        await super().async_added_to_hass()

        def on_state_changed():
            self.schedule_update_ha_state()

        def update_entity_information():
            if self._device.deleted:
                self._attr_available = False
                # async_will_remove_from_hass isn't intended to be called
                # directly and should only be called by the entity platform
                # it should be split into another function
                self.hass.add_job(self.async_will_remove_from_hass)
            self.schedule_update_ha_state()

        self._session.subscribe_userdefinedstate_callback(
            self._device.id, on_state_changed
        )
        self._session.subscribe_userdefinedstate_callback(
            self._device.id, update_entity_information
        )

    async def async_will_remove_from_hass(self):
        """Unsubscribe from SHC events."""
        await super().async_will_remove_from_hass()
        self._session.unsubscribe_userdefinedstate_callbacks(self._device.id)

    @property
    def is_on(self) -> bool:
        """Return the state of the switch."""
        return (
            getattr(self._device, self.entity_description.on_key)
            == self.entity_description.on_value
        )

    def turn_on(self, **kwargs) -> None:
        """Turn the switch on."""
        setattr(self._device, self.entity_description.on_key, True)

    def turn_off(self, **kwargs) -> None:
        """Turn the switch off."""
        setattr(self._device, self.entity_description.on_key, False)

    @property
    def should_poll(self) -> bool:
        """Switch needs polling."""
        return self.entity_description.should_poll

    def update(self) -> None:
        """Trigger an update of the device."""
        self._device.update()

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
