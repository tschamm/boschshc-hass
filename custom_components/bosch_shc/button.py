"""Platform for binarysensor integration."""

import asyncio

from boschshcpy import (
    SHCBatteryDevice,
    SHCDevice,
    SHCSession,
    SHCMicromoduleRelay,
)

from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from homeassistant.components.button import (
    ButtonEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_DEVICE_ID,
    ATTR_ID,
    ATTR_NAME,
    EVENT_HOMEASSISTANT_STOP,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback


from .const import (
    DATA_SESSION,
    DOMAIN,
)
from .entity import SHCEntity


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the SHC binary sensor platform."""
    entities = []
    session: SHCSession = hass.data[DOMAIN][config_entry.entry_id][DATA_SESSION]

    for button in session.device_helper.micromodule_impulse_relays:
        entities.append(
            SHCRelayButton(
                device=button,
                parent_id=session.information.unique_id,
                entry_id=config_entry.entry_id,
            )
        )

    if entities:
        async_add_entities(entities)


class SHCRelayButton(SHCEntity, ButtonEntity):
    """Representation of a SHC button."""

    def __init__(
        self,
        device: SHCDevice,
        parent_id: str,
        entry_id: str,
        attr_name: str | None = None,
    ) -> None:
        """Initialize a SHC switch."""
        super().__init__(device, parent_id, entry_id)
        self._attr_name = (
            f"{device.name}" if attr_name is None else f"{device.name} {attr_name}"
        )
        self._attr_unique_id = (
            f"{device.root_device_id}_{device.id}"
            if attr_name is None
            else f"{device.root_device_id}_{device.id}_{attr_name.lower()}"
        )

    def press(self) -> None:
        """Triggers impulse."""
        self._device.trigger_impulse_state()
