"""Platform for number integration."""

from __future__ import annotations

import logging

from boschshcpy import SHCThermostat, SHCSession
from boschshcpy.device import SHCDevice

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DATA_SESSION, DOMAIN
from .entity import SHCEntity, device_excluded

LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the SHC number platform."""
    entities: list[NumberEntity] = []
    session: SHCSession = hass.data[DOMAIN][config_entry.entry_id][DATA_SESSION]

    for number in (
        session.device_helper.thermostats + session.device_helper.roomthermostats
    ):
        if device_excluded(number, config_entry.options):
            continue
        entities.append(
            SHCNumber(
                device=number,
                entry_id=config_entry.entry_id,
                attr_name="Offset",
            )
        )

    for device in session.device_helper.micromodule_impulse_relays:
        if device_excluded(device, config_entry.options):
            continue
        if not hasattr(device, "impulse_length"):
            continue
        if device.impulse_length is None:
            continue
        entities.append(
            ImpulseLengthNumber(
                device=device,
                entry_id=config_entry.entry_id,
            )
        )

    for device in session.device_helper.heating_circuits:
        if device_excluded(device, config_entry.options):
            continue
        entities.append(
            HeatingCircuitSetpointNumber(
                device=device,
                entry_id=config_entry.entry_id,
                attr_name="SetpointEco",
                label="Setpoint Eco Temperature",
                getter_name="setpoint_temperature_eco",
                setter_name="setpoint_temperature_eco",
            )
        )
        entities.append(
            HeatingCircuitSetpointNumber(
                device=device,
                entry_id=config_entry.entry_id,
                attr_name="SetpointComfort",
                label="Setpoint Comfort Temperature",
                getter_name="setpoint_temperature_comfort",
                setter_name="setpoint_temperature_comfort",
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
        self._attr_name = None if attr_name is None else attr_name
        self._attr_unique_id = (
            f"{device.root_device_id}_{device.id}"
            if attr_name is None
            else f"{device.root_device_id}_{device.id}_{attr_name.lower()}"
        )
        self._device: SHCThermostat = device

    def set_native_value(self, value: float) -> None:
        """Update the current value, clamped to [native_min_value, native_max_value]."""
        clamped = max(self.native_min_value, min(self.native_max_value, value))
        self._device.offset = clamped

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


class ImpulseLengthNumber(SHCEntity, NumberEntity):
    """NumberEntity for the impulse length of a MicromoduleImpulseRelay.

    The lib stores impulse_length in tenths of seconds (integer units of 100 ms).
    We expose it in seconds for a user-friendly display.
    Range 1–60 s (lib range: 10–600 tenths). Step 0.1 s.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_native_min_value = 0.1
    _attr_native_max_value = 60.0
    _attr_native_step = 0.1
    _attr_mode = NumberMode.BOX

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize the impulse length number."""
        super().__init__(device, entry_id)
        self._attr_name = "Impulse Length"
        self._attr_unique_id = (
            f"{device.root_device_id}_{device.id}_impulse_length"
        )

    @property
    def native_value(self) -> float | None:
        """Return the impulse length in seconds."""
        raw = getattr(self._device, "impulse_length", None)
        if raw is None:
            return None
        # lib stores in tenths of seconds → divide by 10
        return raw / 10.0

    def set_native_value(self, value: float) -> None:
        """Set the impulse length; convert seconds → tenths of seconds (int)."""
        clamped = max(
            self._attr_native_min_value, min(self._attr_native_max_value, value)
        )
        self._device.impulse_length = round(clamped * 10)


class HeatingCircuitSetpointNumber(SHCEntity, NumberEntity):
    """NumberEntity for HeatingCircuit eco/comfort setpoint temperatures.

    The HeatingCircuitService exposes setpoint_temperature_eco and
    setpoint_temperature_comfort as read/write float properties. Range 5–30 °C,
    step 0.5 °C (Bosch app convention).
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_native_min_value = 5.0
    _attr_native_max_value = 30.0
    _attr_native_step = 0.5
    _attr_mode = NumberMode.BOX

    def __init__(
        self,
        device: SHCDevice,
        entry_id: str,
        attr_name: str,
        label: str,
        getter_name: str,
        setter_name: str,
    ) -> None:
        """Initialize the heating circuit setpoint number."""
        super().__init__(device, entry_id)
        self._attr_name = label
        self._attr_unique_id = (
            f"{device.root_device_id}_{device.id}_{attr_name.lower()}"
        )
        self._getter_name = getter_name
        self._setter_name = setter_name

    @property
    def native_value(self) -> float | None:
        """Return the setpoint temperature."""
        try:
            svc = getattr(self._device, "_heating_circuit_service", None)
            if svc is None:
                return None
            return getattr(svc, self._getter_name)
        except (AttributeError, KeyError) as err:
            LOGGER.warning(
                "Unable to read %s for %s: %s",
                self._getter_name,
                self._device.name,
                err,
            )
            return None

    def set_native_value(self, value: float) -> None:
        """Write the setpoint temperature, clamped to valid range."""
        clamped = max(
            self._attr_native_min_value, min(self._attr_native_max_value, value)
        )
        try:
            svc = getattr(self._device, "_heating_circuit_service", None)
            if svc is None:
                LOGGER.warning(
                    "HeatingCircuitService unavailable for %s", self._device.name
                )
                return
            setattr(svc, self._setter_name, clamped)
        except (AttributeError, KeyError) as err:
            LOGGER.warning(
                "Unable to write %s for %s: %s",
                self._setter_name,
                self._device.name,
                err,
            )
