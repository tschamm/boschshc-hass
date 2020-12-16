"""Helper functions for Bosch SHC."""
from homeassistant import config_entries
from homeassistant.helpers.device_registry import async_get_registry as get_dev_reg
from homeassistant.helpers.entity_registry import async_get_registry as get_ent_reg

from .const import DOMAIN


async def remove_devices(hass, entity, entry_id):
    """Get item that is removed from session."""
    await entity.async_remove()
    ent_registry = await get_ent_reg(hass)
    if entity.entity_id in ent_registry.entities:
        ent_registry.async_remove(entity.entity_id)
    dev_registry = await get_dev_reg(hass)
    device = dev_registry.async_get_device(
        identifiers={(DOMAIN, entity.device_id)}, connections=set()
    )
    if device is not None:
        dev_registry.async_update_device(
            device.id, remove_config_entry_id=entry_id
        )
