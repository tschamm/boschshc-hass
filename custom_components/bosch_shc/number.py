"""Platform for switch integration."""

from __future__ import annotations

from boschshcpy import SHCThermostat, SHCSession
from boschshcpy.device import SHCDevice

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DATA_SESSION, DOMAIN
from .entity import SHCEntity


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the SHC switch platform."""
    entities: list[NumberEntity] = []
    session: SHCSession = hass.data[DOMAIN][config_entry.entry_id][DATA_SESSION]

    for number in (
        session.device_helper.thermostats + session.device_helper.roomthermostats
    ):
        entities.append(
            SHCNumber(
                device=number,
                entry_id=config_entry.entry_id,
                attr_name="Offset",
            )
        )

    if entities:
        async_add_entities(entities)


class SHCNumber(SHCEntity, NumberEntity):
    """Representation of a SHC number."""

    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(
        self,
        device: SHCDevice,
        entry_id: str,
        attr_name: str | None = None,
    ) -> None:
        """Initialize a SHC number."""
        super().__init__(device, entry_id)
        self._attr_name = (
            f"{device.name}" if attr_name is None else f"{device.name} {attr_name}"
        )
        self._attr_unique_id = (
            f"{device.root_device_id}_{device.id}"
            if attr_name is None
            else f"{device.root_device_id}_{device.id}_{attr_name.lower()}"
        )
        self._device: SHCThermostat = device

    def set_native_value(self, value: float) -> None:
        """Update the current value."""
        self._device.offset = value

    @property
    def native_value(self) -> float:
        """Return the value of the number."""
        return self._device.offset

    @property
    def native_step(self) -> float:
        """Return the step of the number."""
        return self._device.step_size

    @property
    def native_min_value(self) -> float:
        """Return the min value of the number."""
        return self._device.min_offset

    @property
    def native_max_value(self) -> float:
        """Return the max value of the number."""
        return self._device.max_offset
