"""Bosch Smart Home Controller base entity."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from boschshcpy.device import SHCDevice
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.device_registry import async_get as get_dev_reg
from homeassistant.helpers.entity import Entity

from .const import (
    DOMAIN,
    LOGGER,
    OPT_ALL_LIGHTS_AS_LIGHT,
    OPT_EXCLUDED_DEVICES,
    OPT_EXCLUDED_ROOMS,
    OPT_LIGHTS_AS_LIGHT,
)

# #338: friendlier display names for the light-relay models, so the options
# picker shows "Light/Shutter Control II" instead of the raw "MICROMODULE_*".
_LIGHT_RELAY_FRIENDLY_MODEL = {
    "MICROMODULE_LIGHT_ATTACHED": "Light/Shutter Control II",
    "MICROMODULE_LIGHT_CONTROL": "Light/Shutter Control II",
    "BSM": "In-wall light switch",
}


def light_relay_friendly_model(device: Any) -> str:
    """Friendly model label for a light-relay device (falls back to the model)."""
    model = getattr(device, "device_model", "") or ""
    return _LIGHT_RELAY_FRIENDLY_MODEL.get(model, model)


def light_switch_devices(session: Any) -> list[Any]:
    """Return the on/off light-relay devices that can be a switch OR a light.

    These are the Light/Shutter Control II light channels
    (MICROMODULE_LIGHT_ATTACHED) and the in-wall BSM light switches — both wrap a
    plain PowerSwitch relay and are exposed as a HA `switch` by default, or as a
    `light` when opted in per device (#338).  Buckets are read with getattr so an
    older pinned lib that lacks one of them does not raise.
    """
    return list(getattr(session.device_helper, "light_switches_bsm", [])) + list(
        getattr(session.device_helper, "micromodule_light_attached", [])
    )


def light_switch_as_light(device: Any, options: Mapping[str, Any]) -> bool:
    """True if this light-relay device should be a `light` (#338).

    The global "all" toggle wins; otherwise fall back to the per-device list.
    """
    if options.get(OPT_ALL_LIGHTS_AS_LIGHT, False):
        return True
    opted_in = options.get(OPT_LIGHTS_AS_LIGHT) or []
    return getattr(device, "id", None) in opted_in


def device_excluded(device: Any, options: Mapping[str, Any]) -> bool:
    """True if the Bosch device is excluded by the device/room filter options."""
    excluded_devices = options.get(OPT_EXCLUDED_DEVICES) or []
    excluded_rooms = options.get(OPT_EXCLUDED_ROOMS) or []
    if not excluded_devices and not excluded_rooms:
        return False
    if getattr(device, "id", None) in excluded_devices:
        return True
    if getattr(device, "room_id", None) in excluded_rooms:
        return True
    return False


async def async_get_device_id(hass: HomeAssistant, device_id: str) -> str | None:
    """Get device id from device registry."""
    dev_registry = get_dev_reg(hass)
    device = dev_registry.async_get_device(
        identifiers={(DOMAIN, device_id)}, connections=set()
    )
    return device.id if device is not None else None


async def async_remove_devices(
    hass: HomeAssistant, entity: Entity, entry_id: str
) -> None:
    """Get item that is removed from session."""
    dev_registry = get_dev_reg(hass)
    device = dev_registry.async_get_device(
        identifiers={(DOMAIN, entity.device_id)}, connections=set()
    )
    if device is not None:
        dev_registry.async_update_device(device.id, remove_config_entry_id=entry_id)


async def async_migrate_to_new_unique_id(
    hass: HomeAssistant,
    platform: str,
    device: SHCDevice,
    attr_name: str | None = None,
    old_unique_id: str | None = None,
) -> None:
    """Migrate old unique ids to new unique ids."""
    if old_unique_id is None:
        old_unique_id = (
            f"{device.serial}"
            if attr_name is None
            else f"{device.serial}_{attr_name.lower()}"
        )

    ent_reg = entity_registry.async_get(hass)
    entity_id = ent_reg.async_get_entity_id(platform, DOMAIN, old_unique_id)

    if entity_id is not None:
        new_unique_id = (
            f"{device.root_device_id}_{device.id}"
            if attr_name is None
            else f"{device.root_device_id}_{device.id}_{attr_name.lower()}"
        )
        try:
            ent_reg.async_update_entity(entity_id, new_unique_id=new_unique_id)
        except ValueError:
            LOGGER.warning(
                "Skip migration of id [%s] to [%s] because it already exists",
                old_unique_id,
                new_unique_id,
            )
        else:
            LOGGER.debug(
                "Migrating unique_id from [%s] to [%s]",
                old_unique_id,
                new_unique_id,
            )


class SHCEntity(Entity):  # type: ignore[misc]
    """Representation of a SHC base entity."""

    _attr_has_entity_name = True

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize the generic SHC device."""
        self._device = device
        self._entry_id = entry_id
        # Default to primary entity: _attr_name=None → HA uses only the device name.
        self._attr_name = None
        # Sub-classes with a class-level _attr_translation_key provide a sub-entity
        # label; remove the instance None so HA's translation lookup is not shadowed.
        for _cls in type(self).__mro__:
            if _cls is SHCEntity:
                break
            if "_attr_translation_key" in _cls.__dict__:
                del self._attr_name
                break
        self._attr_unique_id = f"{device.root_device_id}_{device.id}"
        self._update_attr()

    def _update_attr(self) -> None:
        pass

    async def async_added_to_hass(self) -> None:
        """Subscribe to SHC events."""
        await super().async_added_to_hass()

        def on_state_changed() -> None:
            self._update_attr()
            self.schedule_update_ha_state()

        def update_entity_information() -> None:
            if self._device.deleted:
                # This callback fires from boschshcpy's background polling
                # thread, not the event loop — hass.async_create_task() would
                # raise (HA's non-thread-safe-operation guard). hass.create_task()
                # is the thread-safe wrapper (loop.call_soon_threadsafe).
                self.hass.create_task(
                    async_remove_devices(self.hass, self, self._entry_id)
                )
            else:
                self._update_attr()
                self.schedule_update_ha_state()

        for service in self._device.device_services:
            service.subscribe_callback(self.entity_id, on_state_changed)
        self._device.subscribe_callback(self.entity_id, update_entity_information)

    async def async_will_remove_from_hass(self) -> None:
        """Unsubscribe from SHC events."""
        await super().async_will_remove_from_hass()
        for service in self._device.device_services:
            service.unsubscribe_callback(self.entity_id)
        self._device.unsubscribe_callback(self.entity_id)

    @property
    def device_name(self) -> str:
        """Name of the device."""
        return str(self._device.name)

    @property
    def device_id(self) -> str:
        """Device id of the entity."""
        return str(self._device.id)

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device info."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._device.id)},
            name=self.device_name,
            manufacturer=self._device.manufacturer,
            model=self._device.device_model,
            via_device=(DOMAIN, self._device.root_device_id),
        )

    @property
    def available(self) -> bool:
        """Return false if status is unavailable."""
        return bool(self._device.status == "AVAILABLE")

    @property
    def should_poll(self) -> bool:
        """Report polling mode. SHC Entity is communicating via long polling."""
        return False
