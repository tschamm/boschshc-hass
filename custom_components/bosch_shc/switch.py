"""Platform for switch integration."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from typing import Any

import aiohttp
from boschshcpy import (
    BypassService,
    CameraAmbientLightService,
    CameraFrontLightService,
    CameraLightService,
    CameraNotificationService,
    PowerSwitchService,
    PrivacyModeService,
    RoutingService,
    SHCSession,
    SHCShutterContact2Plus,
    SHCUserDefinedState,
    SilentModeService,
    ThermostatService,
)
from boschshcpy.device import SHCDevice
from boschshcpy.exceptions import SHCException
from homeassistant.components.switch import (
    SwitchDeviceClass,
    SwitchEntity,
    SwitchEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceEntry, DeviceInfo
from homeassistant.helpers.device_registry import async_get as get_dev_reg
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    OPT_AUTOMATION_RULES_AS_ENTITIES,
    OPT_SUPPRESS_CAMERA_SWITCHES,
)
from .entity import (
    SHCEntity,
    async_migrate_to_new_unique_id,
    async_remove_stale_entity,
    device_excluded,
    light_switch_as_light,
)

LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class SHCSwitchEntityDescription(SwitchEntityDescription):
    """Class describing SHC switch entities."""

    on_key: str
    on_value: Any
    should_poll: bool


SWITCH_TYPES: dict[str, SHCSwitchEntityDescription] = {
    "smartplug": SHCSwitchEntityDescription(
        key="smartplug",
        device_class=SwitchDeviceClass.OUTLET,
        on_key="switchstate",
        on_value=PowerSwitchService.State.ON,
        should_poll=False,
    ),
    "smartplug_routing": SHCSwitchEntityDescription(
        key="smartplug_routing",
        device_class=SwitchDeviceClass.SWITCH,
        on_key="routing",
        on_value=RoutingService.State.ENABLED,
        should_poll=False,
        entity_category=EntityCategory.CONFIG,
        icon="mdi:wifi",
    ),
    "smartplugcompact": SHCSwitchEntityDescription(
        key="smartplugcompact",
        device_class=SwitchDeviceClass.OUTLET,
        on_key="switchstate",
        on_value=PowerSwitchService.State.ON,
        should_poll=False,
    ),
    "micromodule_relay_switch": SHCSwitchEntityDescription(
        key="micromodule_relay_switch",
        device_class=SwitchDeviceClass.OUTLET,
        on_key="switchstate",
        on_value=PowerSwitchService.State.ON,
        should_poll=False,
    ),
    "lightswitch": SHCSwitchEntityDescription(
        key="lightswitch",
        device_class=SwitchDeviceClass.SWITCH,
        on_key="switchstate",
        on_value=PowerSwitchService.State.ON,
        should_poll=False,
    ),
    "cameraeyes": SHCSwitchEntityDescription(
        key="cameraeyes",
        device_class=SwitchDeviceClass.SWITCH,
        on_key="privacymode",
        on_value=PrivacyModeService.State.DISABLED,
        should_poll=True,
        icon="mdi:video",
    ),
    "cameraeyes_cameralight": SHCSwitchEntityDescription(
        key="cameraeyes_cameralight",
        device_class=SwitchDeviceClass.SWITCH,
        on_key="cameralight",
        on_value=CameraLightService.State.ON,
        should_poll=True,
        entity_category=EntityCategory.CONFIG,
        icon="mdi:light-flood-down",
    ),
    "cameraeyes_notification": SHCSwitchEntityDescription(
        key="cameraeyes_notification",
        device_class=SwitchDeviceClass.SWITCH,
        on_key="cameranotification",
        on_value=CameraNotificationService.State.ENABLED,
        should_poll=True,
        entity_category=EntityCategory.CONFIG,
        icon="mdi:message-badge",
    ),
    "camera360": SHCSwitchEntityDescription(
        key="camera360",
        device_class=SwitchDeviceClass.SWITCH,
        on_key="privacymode",
        on_value=PrivacyModeService.State.DISABLED,
        should_poll=True,
        icon="mdi:video",
    ),
    "camera360_notification": SHCSwitchEntityDescription(
        key="camera360_notification",
        device_class=SwitchDeviceClass.SWITCH,
        on_key="cameranotification",
        on_value=CameraNotificationService.State.ENABLED,
        should_poll=True,
        entity_category=EntityCategory.CONFIG,
        icon="mdi:message-badge",
    ),
    "cameraoutdoorgen2": SHCSwitchEntityDescription(
        key="cameraoutdoorgen2",
        device_class=SwitchDeviceClass.SWITCH,
        on_key="privacymode",
        on_value=PrivacyModeService.State.DISABLED,
        should_poll=True,
        icon="mdi:video",
    ),
    "cameraoutdoorgen2_camerafrontlight": SHCSwitchEntityDescription(
        key="cameraoutdoorgen2_camerafrontlight",
        device_class=SwitchDeviceClass.SWITCH,
        on_key="camerafrontlight",
        on_value=CameraFrontLightService.State.ON,
        should_poll=True,
        entity_category=EntityCategory.CONFIG,
        icon="mdi:light-flood-down",
    ),
    "cameraoutdoorgen2_cameraambientlight": SHCSwitchEntityDescription(
        key="cameraoutdoorgen2_cameraambientlight",
        device_class=SwitchDeviceClass.SWITCH,
        on_key="cameraambientlight",
        on_value=CameraAmbientLightService.State.ON,
        should_poll=True,
        entity_category=EntityCategory.CONFIG,
        icon="mdi:wall-sconce-flat",
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
        # #342: name it clearly ("Alarm bypass") instead of inheriting the bare
        # device name, so users understand it excludes the contact from the
        # intrusion alarm (open/close while armed without triggering it).
        translation_key="bypass",
        device_class=SwitchDeviceClass.SWITCH,
        on_key="bypass",
        on_value=BypassService.State.BYPASS_ACTIVE,
        should_poll=False,
    ),
    "bypass_infinite": SHCSwitchEntityDescription(
        key="bypass_infinite",
        # hass#120 audit: fully modeled in boschshcpy (BypassService.infinite
        # / SHCShutterContact2.bypass_infinite) but never wired into an HA
        # entity. When off, an active bypass auto-expires after
        # bypass_timeout seconds instead of staying active forever.
        translation_key="bypass_infinite",
        device_class=SwitchDeviceClass.SWITCH,
        on_key="bypass_infinite",
        on_value=True,
        entity_category=EntityCategory.CONFIG,
        should_poll=False,
        icon="mdi:timer-off-outline",
    ),
    "child_lock": SHCSwitchEntityDescription(
        key="child_lock",
        device_class=SwitchDeviceClass.SWITCH,
        on_key="child_lock",
        on_value=True,
        entity_category=EntityCategory.CONFIG,
        should_poll=False,
        icon="mdi:lock",
    ),
    "child_lock_thermostat": SHCSwitchEntityDescription(
        key="child_lock_thermostat",
        device_class=SwitchDeviceClass.SWITCH,
        on_key="child_lock",
        # Thermostats expose child lock as a ThermostatService.State enum, not a
        # bool. State.ON != True, so reusing the bool "child_lock" description
        # made the switch read OFF permanently. Compare against the enum member.
        on_value=ThermostatService.State.ON,
        entity_category=EntityCategory.CONFIG,
        should_poll=False,
        icon="mdi:lock",
    ),
    "pet_immunity_enabled": SHCSwitchEntityDescription(
        key="pet_immunity_enabled",
        device_class=SwitchDeviceClass.SWITCH,
        on_key="pet_immunity_enabled",
        on_value=True,
        entity_category=EntityCategory.CONFIG,
        should_poll=False,
        icon="mdi:paw",
    ),
    "energy_saving_mode_enabled": SHCSwitchEntityDescription(
        key="energy_saving_mode_enabled",
        device_class=SwitchDeviceClass.SWITCH,
        on_key="energy_saving_mode_enabled",
        on_value=True,
        entity_category=EntityCategory.CONFIG,
        should_poll=False,
        icon="mdi:leaf",
    ),
    "warning_suppressed": SHCSwitchEntityDescription(
        key="warning_suppressed",
        device_class=SwitchDeviceClass.SWITCH,
        on_key="warning_suppressed",
        on_value=True,
        entity_category=EntityCategory.CONFIG,
        should_poll=False,
        icon="mdi:bell-off",
    ),
    "nightly_promise_enabled": SHCSwitchEntityDescription(
        key="nightly_promise_enabled",
        device_class=SwitchDeviceClass.SWITCH,
        on_key="nightly_promise_enabled",
        on_value=True,
        entity_category=EntityCategory.CONFIG,
        should_poll=False,
        icon="mdi:shield-check",
    ),
    "humidity_warning_enabled": SHCSwitchEntityDescription(
        key="humidity_warning_enabled",
        device_class=SwitchDeviceClass.SWITCH,
        on_key="humidity_warning_enabled",
        on_value=True,
        entity_category=EntityCategory.CONFIG,
        should_poll=False,
        icon="mdi:water-alert",
    ),
    "swap_inputs": SHCSwitchEntityDescription(
        key="swap_inputs",
        device_class=SwitchDeviceClass.SWITCH,
        on_key="swap_inputs",
        on_value=True,
        entity_category=EntityCategory.CONFIG,
        should_poll=False,
        icon="mdi:swap-horizontal",
    ),
    "swap_outputs": SHCSwitchEntityDescription(
        key="swap_outputs",
        device_class=SwitchDeviceClass.SWITCH,
        on_key="swap_outputs",
        on_value=True,
        entity_category=EntityCategory.CONFIG,
        should_poll=False,
        icon="mdi:swap-horizontal-bold",
    ),
    "pre_alarm_enabled": SHCSwitchEntityDescription(
        key="pre_alarm_enabled",
        device_class=SwitchDeviceClass.SWITCH,
        on_key="pre_alarm_enabled",
        on_value=True,
        entity_category=EntityCategory.CONFIG,
        should_poll=False,
        icon="mdi:smoke-detector",
    ),
    "smart_sensitivity_enabled": SHCSwitchEntityDescription(
        key="smart_sensitivity_enabled",
        device_class=SwitchDeviceClass.SWITCH,
        on_key="smart_sensitivity_enabled",
        on_value=True,
        entity_category=EntityCategory.CONFIG,
        should_poll=False,
        icon="mdi:tune",
    ),
    "tamper_protection_enabled": SHCSwitchEntityDescription(
        key="tamper_protection_enabled",
        device_class=SwitchDeviceClass.SWITCH,
        on_key="tamper_protection_enabled",
        on_value=True,
        entity_category=EntityCategory.CONFIG,
        should_poll=False,
        icon="mdi:shield-lock",
    ),
    "silent_mode": SHCSwitchEntityDescription(
        key="silent_mode",
        device_class=SwitchDeviceClass.SWITCH,
        on_key="silentmode",
        on_value=SilentModeService.State.MODE_SILENT,
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
    "intrusion_alarm": SHCSwitchEntityDescription(
        key="intrusion_alarm",
        device_class=SwitchDeviceClass.SWITCH,
        on_key="intrusion_alarm",
        on_value=True,
        should_poll=False,
        icon="mdi:alarm-light",
    ),
}


async def async_setup_entry(  # noqa: C901
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the SHC switch platform."""
    entities: list[SwitchEntity] = []
    session: SHCSession = config_entry.runtime_data.session

    for switch in session.device_helper.smart_plugs:
        if device_excluded(switch, config_entry.options):
            continue
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
        if (
            getattr(switch, "supports_energy_saving_mode", False)
            and getattr(switch, "energy_saving_mode_enabled", None) is not None
        ):
            entities.append(
                SHCSwitch(
                    device=switch,
                    entry_id=config_entry.entry_id,
                    description=SWITCH_TYPES["energy_saving_mode_enabled"],
                    attr_name="EnergySavingMode",
                )
            )
        if (
            getattr(switch, "supports_power_switch_warning", False)
            and getattr(switch, "warning_suppressed", None) is not None
        ):
            entities.append(
                SHCSwitch(
                    device=switch,
                    entry_id=config_entry.entry_id,
                    description=SWITCH_TYPES["warning_suppressed"],
                    attr_name="WarningSuppressed",
                )
            )

    for switch in list(session.device_helper.light_switches_bsm) + list(  # type: ignore[assignment]
        session.device_helper.micromodule_light_attached
    ):
        if device_excluded(switch, config_entry.options):
            continue
        # #338: when this light relay is opted in to be a HA `light`, the light
        # platform creates the on/off entity instead — skip the switch here so
        # the device is not exposed twice.  (Child-lock / swap config switches
        # below stay regardless, they are independent CONFIG entities.)
        if light_switch_as_light(switch, config_entry.options):
            # An options change reloads the entry (OptionsFlowWithReload), so
            # if a switch entity was previously created for this device
            # (before the option was turned on), remove the now-stale
            # registry entry — same unique_id as RelayLight's default, since
            # neither passes attr_name.
            await async_remove_stale_entity(
                hass, Platform.SWITCH, f"{switch.root_device_id}_{switch.id}"
            )
            continue
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

    for switch in session.device_helper.smart_plugs_compact:  # type: ignore[assignment]
        if device_excluded(switch, config_entry.options):
            continue
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
        if (
            getattr(switch, "supports_energy_saving_mode", False)
            and getattr(switch, "energy_saving_mode_enabled", None) is not None
        ):
            entities.append(
                SHCSwitch(
                    device=switch,
                    entry_id=config_entry.entry_id,
                    description=SWITCH_TYPES["energy_saving_mode_enabled"],
                    attr_name="EnergySavingMode",
                )
            )
        if (
            getattr(switch, "supports_power_switch_warning", False)
            and getattr(switch, "warning_suppressed", None) is not None
        ):
            entities.append(
                SHCSwitch(
                    device=switch,
                    entry_id=config_entry.entry_id,
                    description=SWITCH_TYPES["warning_suppressed"],
                    attr_name="WarningSuppressed",
                )
            )

    for switch in session.device_helper.micromodule_relays:  # type: ignore[assignment]
        if device_excluded(switch, config_entry.options):
            continue
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
        if (
            getattr(switch, "supports_switch_configuration", False)
            and getattr(switch, "swap_inputs", None) is not None
        ):
            entities.append(
                SHCSwitch(
                    device=switch,
                    entry_id=config_entry.entry_id,
                    description=SWITCH_TYPES["swap_inputs"],
                    attr_name="SwapInputs",
                )
            )
        if (
            getattr(switch, "supports_switch_configuration", False)
            and getattr(switch, "swap_outputs", None) is not None
        ):
            entities.append(
                SHCSwitch(
                    device=switch,
                    entry_id=config_entry.entry_id,
                    description=SWITCH_TYPES["swap_outputs"],
                    attr_name="SwapOutputs",
                )
            )

    for device in getattr(session.device_helper, "micromodule_light_controls", []):
        if device_excluded(device, config_entry.options):
            continue
        if (
            getattr(device, "supports_switch_configuration", False)
            and getattr(device, "swap_inputs", None) is not None
        ):
            entities.append(
                SHCSwitch(
                    device=device,
                    entry_id=config_entry.entry_id,
                    description=SWITCH_TYPES["swap_inputs"],
                    attr_name="SwapInputs",
                )
            )
        if (
            getattr(device, "supports_switch_configuration", False)
            and getattr(device, "swap_outputs", None) is not None
        ):
            entities.append(
                SHCSwitch(
                    device=device,
                    entry_id=config_entry.entry_id,
                    description=SWITCH_TYPES["swap_outputs"],
                    attr_name="SwapOutputs",
                )
            )

    suppress_cameras = config_entry.options.get(OPT_SUPPRESS_CAMERA_SWITCHES, False)
    if suppress_cameras:
        dev_registry = get_dev_reg(hass)
        for shc_device in (
            list(session.device_helper.camera_eyes)
            + list(session.device_helper.camera_360)
            + list(session.device_helper.camera_outdoor_gen2)
        ):
            dev_entry = dev_registry.async_get_device(
                identifiers={(DOMAIN, shc_device.id)}, connections=set()
            )
            if dev_entry is not None:
                dev_registry.async_update_device(
                    dev_entry.id, remove_config_entry_id=config_entry.entry_id
                )
    for switch in session.device_helper.camera_eyes:  # type: ignore[assignment]
        if suppress_cameras or device_excluded(switch, config_entry.options):
            continue
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

    for switch in session.device_helper.camera_360:  # type: ignore[assignment]
        if suppress_cameras or device_excluded(switch, config_entry.options):
            continue
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

    for switch in session.device_helper.camera_outdoor_gen2:  # type: ignore[assignment]
        if suppress_cameras or device_excluded(switch, config_entry.options):
            continue
        await async_migrate_to_new_unique_id(
            hass=hass,
            platform=Platform.SWITCH,
            device=switch,
        )
        entities.append(
            SHCSwitch(
                device=switch,
                entry_id=config_entry.entry_id,
                description=SWITCH_TYPES["cameraoutdoorgen2"],
            )
        )
        # #289-safe migration: in 0.4.106-0.4.111 both Gen2 camera lights shared
        # attr_name="Light" (uid _light); the Frontlight/AmbientLight split left
        # the old migration pointing at a phantom _light and gave AmbientLight no
        # migration at all.  Map the old id (both historical formats) to the real
        # Frontlight uid; async_migrate skips if the target already exists, so
        # already-upgraded users are unaffected.
        await async_migrate_to_new_unique_id(
            hass=hass,
            platform=Platform.SWITCH,
            device=switch,
            attr_name="Frontlight",
            old_unique_id=f"{switch.serial}_light",
        )
        await async_migrate_to_new_unique_id(
            hass=hass,
            platform=Platform.SWITCH,
            device=switch,
            attr_name="Frontlight",
            old_unique_id=f"{switch.root_device_id}_{switch.id}_light",
        )
        entities.append(
            SHCSwitch(
                device=switch,
                entry_id=config_entry.entry_id,
                description=SWITCH_TYPES["cameraoutdoorgen2_camerafrontlight"],
                attr_name="Frontlight",
            )
        )
        # AmbientLight had no prior migration; the old single _light entity is
        # claimed by Frontlight above, so these no-op for upgraders but cover a
        # registry where only AmbientLight's id survived.
        await async_migrate_to_new_unique_id(
            hass=hass,
            platform=Platform.SWITCH,
            device=switch,
            attr_name="AmbientLight",
            old_unique_id=f"{switch.serial}_light",
        )
        await async_migrate_to_new_unique_id(
            hass=hass,
            platform=Platform.SWITCH,
            device=switch,
            attr_name="AmbientLight",
            old_unique_id=f"{switch.root_device_id}_{switch.id}_light",
        )
        entities.append(
            SHCSwitch(
                device=switch,
                entry_id=config_entry.entry_id,
                description=SWITCH_TYPES["cameraoutdoorgen2_cameraambientlight"],
                attr_name="AmbientLight",
            )
        )

    presence_simulation_system = session.device_helper.presence_simulation_system
    if presence_simulation_system and not device_excluded(
        presence_simulation_system, config_entry.options
    ):
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

    for switch in session.device_helper.shutter_contacts2:  # type: ignore[assignment]
        if device_excluded(switch, config_entry.options):
            continue
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
        entities.append(
            SHCSwitch(
                device=switch,
                entry_id=config_entry.entry_id,
                description=SWITCH_TYPES["bypass_infinite"],
                attr_name="BypassInfinite",
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

    for thermostat_switch in session.device_helper.thermostats:
        if device_excluded(thermostat_switch, config_entry.options):
            continue
        if thermostat_switch.supports_silentmode:
            entities.append(
                SHCSwitch(
                    device=thermostat_switch,
                    entry_id=config_entry.entry_id,
                    description=SWITCH_TYPES["silent_mode"],
                    attr_name="SilentMode",
                )
            )

    # Thermostats / room thermostats / wall thermostats expose child lock as a
    # ThermostatService.State enum (ON/OFF) -> needs the enum-aware description.
    for switch in (  # type: ignore[assignment]
        list(session.device_helper.thermostats)
        + list(session.device_helper.roomthermostats)
        # wall thermostats expose child_lock only with boschshcpy >= 0.2.119;
        # hasattr guard so an older (pinned) lib does not raise on device.child_lock
        + [d for d in session.device_helper.wallthermostats if hasattr(d, "child_lock")]
    ):
        if device_excluded(switch, config_entry.options):
            continue
        entities.append(
            SHCSwitch(
                device=switch,
                entry_id=config_entry.entry_id,
                description=SWITCH_TYPES["child_lock_thermostat"],
                attr_name="ChildLock",
            )
        )

    # ChildProtection devices expose child lock as a bool (childLockActive).
    # micromodule_dimmers and light_switches_bsm also carry the ChildProtection
    # service but were previously not wired -> no child-lock entity was created.
    for switch in (  # type: ignore[assignment]
        list(session.device_helper.micromodule_shutter_controls)
        + list(session.device_helper.micromodule_blinds)
        + list(session.device_helper.micromodule_light_attached)
        + list(session.device_helper.micromodule_relays)
        + list(session.device_helper.micromodule_impulse_relays)
        + list(session.device_helper.micromodule_dimmers)
        + list(session.device_helper.light_switches_bsm)
    ):
        if device_excluded(switch, config_entry.options):
            continue
        entities.append(
            SHCSwitch(
                device=switch,
                entry_id=config_entry.entry_id,
                description=SWITCH_TYPES["child_lock"],
                attr_name="ChildLock",
            )
        )

    for switch in session.device_helper.motion_detectors2:  # type: ignore[assignment]
        if device_excluded(switch, config_entry.options):
            continue
        await async_migrate_to_new_unique_id(
            hass=hass,
            platform=Platform.SWITCH,
            device=switch,
            attr_name="PetImmunity",
        )
        entities.append(
            SHCSwitch(
                device=switch,
                entry_id=config_entry.entry_id,
                description=SWITCH_TYPES["pet_immunity_enabled"],
                attr_name="PetImmunity",
            )
        )
        if hasattr(switch, "smart_sensitivity_enabled"):
            entities.append(
                SHCSwitch(
                    device=switch,
                    entry_id=config_entry.entry_id,
                    description=SWITCH_TYPES["smart_sensitivity_enabled"],
                    attr_name="SmartSensitivity",
                )
            )
        if hasattr(switch, "tamper_protection_enabled"):
            entities.append(
                SHCSwitch(
                    device=switch,
                    entry_id=config_entry.entry_id,
                    description=SWITCH_TYPES["tamper_protection_enabled"],
                    attr_name="TamperProtection",
                )
            )

    for switch in getattr(session.device_helper, "twinguards", []):  # type: ignore[assignment]
        if device_excluded(switch, config_entry.options):
            continue
        if (
            getattr(switch, "supports_nightly_promise", False)
            and getattr(switch, "nightly_promise_enabled", None) is not None
        ):
            entities.append(
                SHCSwitch(
                    device=switch,
                    entry_id=config_entry.entry_id,
                    description=SWITCH_TYPES["nightly_promise_enabled"],
                    attr_name="NightlyPromise",
                )
            )
        if (
            getattr(switch, "supports_smoke_sensitivity", False)
            and getattr(switch, "pre_alarm_enabled", None) is not None
        ):
            entities.append(
                SHCSwitch(
                    device=switch,
                    entry_id=config_entry.entry_id,
                    description=SWITCH_TYPES["pre_alarm_enabled"],
                    attr_name="PreAlarm",
                )
            )

    for switch in getattr(session.device_helper, "smoke_detectors", []):  # type: ignore[assignment]
        if device_excluded(switch, config_entry.options):
            continue
        if (
            getattr(switch, "supports_smoke_sensitivity", False)
            and getattr(switch, "pre_alarm_enabled", None) is not None
        ):
            entities.append(
                SHCSwitch(
                    device=switch,
                    entry_id=config_entry.entry_id,
                    description=SWITCH_TYPES["pre_alarm_enabled"],
                    attr_name="PreAlarm",
                )
            )
        # Smoke Detector II can sound its own intrusion alarm (#174) — expose it
        # as an on/off switch (writes INTRUSION_ALARM_ON_REQUESTED). Gen-1 SD is
        # skipped via supports_intrusion_alarm.
        if getattr(switch, "supports_intrusion_alarm", False):
            entities.append(
                SHCSwitch(
                    device=switch,
                    entry_id=config_entry.entry_id,
                    description=SWITCH_TYPES["intrusion_alarm"],
                    attr_name="IntrusionAlarm",
                )
            )

    # ThermostatGen2 / RoomThermostat2: humidity warning toggle.
    # Guarded by hasattr so old lib (no display_config) doesn't create it.
    for switch in list(session.device_helper.thermostats) + list(  # type: ignore[assignment]
        session.device_helper.roomthermostats
    ):
        if device_excluded(switch, config_entry.options):
            continue
        if (
            getattr(switch, "supports_display_configuration", False)
            and getattr(switch, "humidity_warning_enabled", None) is not None
        ):
            entities.append(
                SHCSwitch(
                    device=switch,
                    entry_id=config_entry.entry_id,
                    description=SWITCH_TYPES["humidity_warning_enabled"],
                    attr_name="HumidityWarning",
                )
            )

    # Temperature-drop service (anti-frost/window-open, APK-traced) -- no-op
    # if a room has no such service (404 -> SHCException, skipped).
    for climate in getattr(session.device_helper, "climate_controls", []):
        if device_excluded(climate, config_entry.options):
            continue
        room_id = climate.room_id
        if room_id is None:
            continue
        room = session.room(room_id)
        try:
            tds = await room.async_temperature_drop_service()
        except SHCException:
            continue
        if tds is None:
            continue
        entities.append(
            TemperatureDropEnabledSwitch(
                device=climate, room=room, entry_id=config_entry.entry_id
            )
        )

    if config_entry.options.get(OPT_AUTOMATION_RULES_AS_ENTITIES, False):
        shc_device_for_rules: DeviceEntry = config_entry.runtime_data.shc_device
        entities.extend(
            SHCAutomationRuleSwitch(
                rule=rule,
                entry_id=config_entry.entry_id,
                shc_device=shc_device_for_rules,
            )
            for rule in session.automation_rules
        )

    if entities:
        async_add_entities(entities)

    @callback  # type: ignore[untyped-decorator]
    def async_add_userdefinedstateswitch(
        device: SHCUserDefinedState,
    ) -> None:
        """Add User Defined State Switch."""
        entity = SHCUserDefinedStateSwitch(
            device=device,
            hass=hass,
            session=session,
            entry_id=config_entry.entry_id,
            description=SWITCH_TYPES["user_defined_state"],
        )
        async_add_entities([entity])

    # add all current items in session
    for switch in session.userdefinedstates:  # type: ignore[assignment]
        async_add_userdefinedstateswitch(device=switch)  # type: ignore[arg-type]

    # Register listener for new user-defined state switches and ensure it is
    # torn down on config entry unload.  session.subscribe() returns None, so
    # we build the unsubscribe closure ourselves.  add_update_listener expects
    # an options-update callback (hass, entry) -> None and must NOT be used here.
    _uds_subscriber = (SHCUserDefinedState, async_add_userdefinedstateswitch)
    session.subscribe(_uds_subscriber)

    def _unsubscribe_uds() -> None:
        with contextlib.suppress(ValueError):
            session._subscribers.remove(_uds_subscriber)  # noqa: SLF001

    config_entry.async_on_unload(_unsubscribe_uds)


class SHCSwitch(SHCEntity, SwitchEntity):  # type: ignore[misc]
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
        self._attr_name = None if attr_name is None else attr_name  # type: ignore[assignment]
        self._attr_unique_id = (
            f"{device.root_device_id}_{device.id}"
            if attr_name is None
            else f"{device.root_device_id}_{device.id}_{attr_name.lower()}"
        )
        # #342/#362-hunt: a description translation_key (e.g. bypass_infinite
        # -> "Bypass Never Expires") should drive a translated entity name
        # regardless of attr_name — attr_name only disambiguates unique_id
        # for a second entity on the same device. HA's name resolver returns
        # a literal _attr_name before ever consulting translation_key, so
        # drop it here whenever a translation_key is present.
        if description.translation_key:
            del self._attr_name
        self._has_async_update = hasattr(self._device, "async_update")  # [S3]

    @property
    def is_on(self) -> bool | None:
        """Return the state of the switch.

        Defensive: cameras registered via SHC local API can have a None
        underlying service (e.g. PrivacyModeService for cameraeyes / camera360
        switches), causing boschshcpy to crash with AttributeError on every
        state update. Return None (unavailable) instead of crash-looping the
        state writer. See mosandlt/boschshc-hass branch fix/code-quality-improvements.
        """
        try:
            return bool(
                getattr(self._device, self.entity_description.on_key)
                == self.entity_description.on_value
            )
        except AttributeError:
            return None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on.

        Guard against AttributeError: some devices (e.g. MicromoduleRelay with
        no connected load, Camera360 with no PrivacyMode service) expose a
        property whose underlying service is None.  Swallow and log instead of
        crash-looping the entity.  Fixes issues #185 (relay) and #206
        (camera_360).
        """
        try:
            await getattr(
                self._device,
                f"async_set_{self.entity_description.on_key}",
            )(True)
        except AttributeError:
            LOGGER.debug(
                "turn_on skipped for %s: service not available (no load/service?)",
                self.entity_id,
            )
        except (aiohttp.ClientError, asyncio.TimeoutError):
            LOGGER.debug(
                "turn_on skipped for %s: service not available (no load/service?)",
                self.entity_id,
            )
        except SHCException as err:
            raise HomeAssistantError(
                f"Failed to turn on {self._device.name}: {err}",
                translation_domain=DOMAIN,
                translation_key="switch_action_failed",
            ) from err

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off.

        Same guard as async_turn_on — see that docstring.
        """
        try:
            await getattr(
                self._device,
                f"async_set_{self.entity_description.on_key}",
            )(False)
        except AttributeError:
            LOGGER.debug(
                "turn_off skipped for %s: service not available (no load/service?)",
                self.entity_id,
            )
        except (aiohttp.ClientError, asyncio.TimeoutError):
            LOGGER.debug(
                "turn_off skipped for %s: service not available (no load/service?)",
                self.entity_id,
            )
        except SHCException as err:
            raise HomeAssistantError(
                f"Failed to turn off {self._device.name}: {err}",
                translation_domain=DOMAIN,
                translation_key="switch_action_failed",
            ) from err

    @property
    def should_poll(self) -> bool:
        """Switch needs polling."""
        return self.entity_description.should_poll

    async def async_update(self) -> None:
        """Trigger an on-demand refresh of the device (async session).

        The integration runs SHCSessionAsync, so the device's sync update() would
        leave an un-awaited coroutine in the service state (TypeError on the next
        read, #335). Use the async refresh; fall back to the executor + sync
        update() only if the installed lib predates async_update.
        """
        if self._has_async_update:  # [S3] cached at init
            await self._device.async_update()
        else:
            await self.hass.async_add_executor_job(self._device.update)


class SHCUserDefinedStateSwitch(SwitchEntity):  # type: ignore[misc]
    """Representation of a SHC User Defined State Entity."""

    entity_description: SHCSwitchEntityDescription
    _attr_has_entity_name = True

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
        # UDS entity: the state name IS the entity's distinguishing name.
        # With has_entity_name=True and _attr_name=None HA would show the SHC hub
        # name only; set _attr_name to the UDS state name so the entity is
        # identifiable (e.g. "Vacation Mode").
        self._attr_name = device.name if attr_name is None else attr_name

        self._attr_unique_id = (
            f"{device.root_device_id}_{device.id}"
            if attr_name is None
            else f"{device.root_device_id}_{device.id}_{attr_name.lower()}"
        )
        self._shc: DeviceEntry = hass.config_entries.async_get_entry(
            entry_id
        ).runtime_data.shc_device  # type: ignore[union-attr]
        self._has_async_update = hasattr(self._device, "async_update")  # [S3]

    async def async_added_to_hass(self) -> None:
        """Subscribe to SHC events."""
        await super().async_added_to_hass()

        def on_state_changed() -> None:
            self.schedule_update_ha_state()

        def update_entity_information() -> None:
            if self._device.deleted:
                self._attr_available = False
                # This callback fires from boschshcpy's background polling
                # thread, not the event loop — hass.async_create_task() would
                # raise (HA's non-thread-safe-operation guard). hass.create_task()
                # is the thread-safe wrapper (loop.call_soon_threadsafe).
                self.hass.create_task(self.async_will_remove_from_hass())
            self.schedule_update_ha_state()

        self._session.subscribe_userdefinedstate_callback(
            self._device.id, on_state_changed
        )
        self._session.subscribe_userdefinedstate_callback(
            self._device.id, update_entity_information
        )

    async def async_will_remove_from_hass(self) -> None:
        """Unsubscribe from SHC events."""
        await super().async_will_remove_from_hass()
        self._session.unsubscribe_userdefinedstate_callbacks(self._device.id)

    @property
    def available(self) -> bool:
        """Return False when the UDS has been deleted from the SHC."""
        return not self._device.deleted

    @property
    def is_on(self) -> bool:
        """Return the state of the switch."""
        return bool(
            getattr(self._device, self.entity_description.on_key)
            == self.entity_description.on_value
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        try:
            await getattr(
                self._device,
                f"async_set_{self.entity_description.on_key}",
            )(True)
        except SHCException as err:
            raise HomeAssistantError(
                f"Failed to turn on {self._device.name}: {err}",
                translation_domain=DOMAIN,
                translation_key="switch_action_failed",
            ) from err

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        try:
            await getattr(
                self._device,
                f"async_set_{self.entity_description.on_key}",
            )(False)
        except SHCException as err:
            raise HomeAssistantError(
                f"Failed to turn off {self._device.name}: {err}",
                translation_domain=DOMAIN,
                translation_key="switch_action_failed",
            ) from err

    @property
    def should_poll(self) -> bool:
        """Switch needs polling."""
        return self.entity_description.should_poll

    async def async_update(self) -> None:
        """Trigger an on-demand refresh of the device (async session).

        The sync device.update() leaves an un-awaited coroutine in the service
        state under SHCSessionAsync (TypeError, #335) — use the async refresh.
        """
        if self._has_async_update:  # [S3] cached at init
            # SHCUserDefinedState is a standalone class, not an SHCDevice
            # subclass -- async_update/update are only present on lib
            # versions the runtime hasattr() check above has already probed.
            await self._device.async_update()  # type: ignore[attr-defined]
        else:
            await self.hass.async_add_executor_job(
                self._device.update  # type: ignore[attr-defined]
            )

    @property
    def device_name(self) -> str | None:
        """Name of the device."""
        return self._shc.name  # type: ignore[no-any-return]

    @property
    def device_id(self) -> str:
        """Device id of the entity."""
        return self._shc.id  # type: ignore[no-any-return]

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device info."""
        return DeviceInfo(
            identifiers=self._shc.identifiers,
            name=self._shc.name,
            manufacturer=self._shc.manufacturer,
            model=self._shc.model,
        )


class SHCAutomationRuleSwitch(SwitchEntity):  # type: ignore[misc]
    """Enable/disable a single Bosch automation rule (system/automation).

    Not an SHC device -- Bosch's own local rule engine, entirely separate
    from Home Assistant's automations (#OPT_AUTOMATION_RULES_AS_ENTITIES).
    Rule state isn't part of the long-poll device-service push model, so it
    must be explicitly polled (default HA polling interval).
    """

    _attr_has_entity_name = True
    _attr_translation_key = "automation_rule"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_should_poll = True

    def __init__(
        self,
        rule: Any,
        entry_id: str,
        shc_device: DeviceEntry | None = None,
    ) -> None:
        """Initialize an automation rule switch."""
        self._rule = rule
        self._shc_device = shc_device
        self._attr_unique_id = f"{entry_id}_automation_rule_{rule.id}"
        self._attr_name = rule.name

    @property
    def device_info(self) -> DeviceInfo | None:
        """Return the device info (links this switch to the SHC controller device)."""
        if self._shc_device is None:
            return None
        return DeviceInfo(
            identifiers=self._shc_device.identifiers,
            name=self._shc_device.name,
            manufacturer=self._shc_device.manufacturer,
            model=self._shc_device.model,
        )

    @property
    def is_on(self) -> bool:
        """Return True if this automation rule is enabled."""
        return bool(self._rule.enabled)

    async def async_update(self) -> None:
        """Poll this rule's current enabled state."""
        try:
            await self._rule.async_refresh()
        except SHCException as err:
            LOGGER.debug("Failed to poll automation rule %s: %s", self._rule.name, err)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable this automation rule."""
        try:
            await self._rule.async_set_enabled(True)
        except SHCException as err:
            raise HomeAssistantError(
                f"Failed to enable automation rule {self._rule.name}: {err}",
                translation_domain=DOMAIN,
                translation_key="automation_rule_update_failed",
            ) from err

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable this automation rule."""
        try:
            await self._rule.async_set_enabled(False)
        except SHCException as err:
            raise HomeAssistantError(
                f"Failed to disable automation rule {self._rule.name}: {err}",
                translation_domain=DOMAIN,
                translation_key="automation_rule_update_failed",
            ) from err


class TemperatureDropEnabledSwitch(SHCEntity, SwitchEntity):  # type: ignore[misc]
    """Enable/disable a room's temperature-drop (anti-frost/window-open) service.

    Not in the official OpenAPI spec; APK ground-truth
    (RestRequests.getTemperatureDropService/putTemperatureDropService), live-
    confirmed across 12 real rooms. Reads/writes go through the room (not the
    climate device) -- a separate resource, so it must be explicitly polled.
    """

    _attr_translation_key = "temperature_drop_enabled"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_should_poll = True

    def __init__(self, device: SHCDevice, room: Any, entry_id: str) -> None:
        """Initialize the temperature-drop enabled switch."""
        super().__init__(device, entry_id)
        self._room = room
        self._attr_unique_id = (
            f"{device.root_device_id}_{device.id}_temperature_drop_enabled"
        )
        self._enabled: bool | None = None

    @property
    def is_on(self) -> bool:
        """Return True if the temperature-drop service is enabled."""
        return bool(self._enabled)

    async def async_update(self) -> None:
        """Poll this room's temperature-drop service configuration."""
        try:
            data = await self._room.async_temperature_drop_service()
        except SHCException as err:
            LOGGER.debug(
                "Failed to poll temperature-drop service for %s: %s",
                self.device_name,
                err,
            )
            return
        self._enabled = bool((data or {}).get("configuration", {}).get("enabled"))

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable the temperature-drop service."""
        try:
            await self._room.async_set_temperature_drop_enabled(True)
        except SHCException as err:
            raise HomeAssistantError(
                f"Failed to enable temperature drop for {self.device_name}: {err}",
                translation_domain=DOMAIN,
                translation_key="switch_action_failed",
            ) from err
        self._enabled = True

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable the temperature-drop service."""
        try:
            await self._room.async_set_temperature_drop_enabled(False)
        except SHCException as err:
            raise HomeAssistantError(
                f"Failed to disable temperature drop for {self.device_name}: {err}",
                translation_domain=DOMAIN,
                translation_key="switch_action_failed",
            ) from err
        self._enabled = False
