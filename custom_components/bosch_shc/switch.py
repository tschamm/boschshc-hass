"""Platform for switch integration."""
from __future__ import annotations

from dataclasses import dataclass

from boschshcpy import (
    SHCCamera360,
    SHCCameraEyes,
    SHCLightSwitch,
    SHCSession,
    SHCSmartPlug,
    SHCSmartPlugCompact,
    SHCShutterContact2,
)
from boschshcpy.device import SHCDevice

from homeassistant.components.switch import (
    SwitchDeviceClass,
    SwitchEntity,
    SwitchEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType

from .const import DATA_SESSION, DOMAIN
from .entity import SHCEntity


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
        on_key="state",
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
        on_key="state",
        on_value=SHCSmartPlugCompact.PowerSwitchService.State.ON,
        should_poll=False,
    ),
    "lightswitch": SHCSwitchEntityDescription(
        key="lightswitch",
        device_class=SwitchDeviceClass.SWITCH,
        on_key="state",
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
        should_poll=True,
    ),
    "bypass": SHCSwitchEntityDescription(
        key="bypass",
        device_class=SwitchDeviceClass.SWITCH,
        on_key="bypass",
        on_value=SHCShutterContact2.BypassService.State.BYPASS_INACTIVE,
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

        entities.append(
            SHCSwitch(
                device=switch,
                parent_id=session.information.unique_id,
                entry_id=config_entry.entry_id,
                description=SWITCH_TYPES["smartplug"],
            )
        )
        entities.append(
            SHCSwitch(
                device=switch,
                parent_id=session.information.unique_id,
                entry_id=config_entry.entry_id,
                description=SWITCH_TYPES["smartplug_routing"],
                attr_name="Routing",
            )
        )

    for switch in (
        session.device_helper.light_switches_bsm
        + session.device_helper.micromodule_light_attached
    ):

        entities.append(
            SHCSwitch(
                device=switch,
                parent_id=session.information.unique_id,
                entry_id=config_entry.entry_id,
                description=SWITCH_TYPES["lightswitch"],
            )
        )

    for switch in session.device_helper.smart_plugs_compact:

        entities.append(
            SHCSwitch(
                device=switch,
                parent_id=session.information.unique_id,
                entry_id=config_entry.entry_id,
                description=SWITCH_TYPES["smartplugcompact"],
            )
        )

    for switch in session.device_helper.camera_eyes:

        entities.append(
            SHCSwitch(
                device=switch,
                parent_id=session.information.unique_id,
                entry_id=config_entry.entry_id,
                description=SWITCH_TYPES["cameraeyes"],
            )
        )
        entities.append(
            SHCSwitch(
                device=switch,
                parent_id=session.information.unique_id,
                entry_id=config_entry.entry_id,
                description=SWITCH_TYPES["cameraeyes_cameralight"],
                attr_name="Light",
            )
        )
        entities.append(
            SHCSwitch(
                device=switch,
                parent_id=session.information.unique_id,
                entry_id=config_entry.entry_id,
                description=SWITCH_TYPES["cameraeyes_notification"],
                attr_name="Notification",
            )
        )

    for switch in session.device_helper.camera_360:

        entities.append(
            SHCSwitch(
                device=switch,
                parent_id=session.information.unique_id,
                entry_id=config_entry.entry_id,
                description=SWITCH_TYPES["camera360"],
            )
        )
        entities.append(
            SHCSwitch(
                device=switch,
                parent_id=session.information.unique_id,
                entry_id=config_entry.entry_id,
                description=SWITCH_TYPES["camera360_notification"],
                attr_name="Notification",
            )
        )

    presence_simulation_system = session.device_helper.presence_simulation_system
    if presence_simulation_system:

        entities.append(
            SHCSwitch(
                device=presence_simulation_system,
                parent_id=session.information.unique_id,
                entry_id=config_entry.entry_id,
                description=SWITCH_TYPES["presencesimulation"],
            )
        )

    for switch in session.device_helper.shutter_contacts2:
        entities.append(
            SHCSwitch(
                device=switch,
                parent_id=session.information.unique_id,
                entry_id=config_entry.entry_id,
                description=SWITCH_TYPES["bypass"],
            )
        )

    if entities:
        async_add_entities(entities)


class SHCSwitch(SHCEntity, SwitchEntity):
    """Representation of a SHC switch."""

    entity_description: SHCSwitchEntityDescription

    def __init__(
        self,
        device: SHCDevice,
        parent_id: str,
        entry_id: str,
        description: SHCSwitchEntityDescription,
        attr_name: str | None = None,
    ) -> None:
        """Initialize a SHC switch."""
        super().__init__(device, parent_id, entry_id)
        self.entity_description = description
        self._attr_name = (
            f"{device.name}" if attr_name is None else f"{device.name} {attr_name}"
        )
        self._attr_unique_id = (
            f"{device.serial}"
            if attr_name is None
            else f"{device.serial}_{attr_name.lower()}"
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
