"""Platform for number integration."""

from __future__ import annotations

import asyncio
import logging

import aiohttp
from boschshcpy import SHCSession, SHCThermostat
from boschshcpy.device import SHCDevice
from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfPower, UnitOfTemperature, UnitOfTime
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
        session.device_helper.thermostats
        + session.device_helper.roomthermostats
        + getattr(session.device_helper, "wallthermostats", [])
    ):
        if device_excluded(number, config_entry.options) or not getattr(
            number, "supports_temperature_offset", True
        ):
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

    # EnergySavingMode numbers: power threshold + enter duration (smart plugs).
    for device in getattr(session.device_helper, "smart_plugs", []) + getattr(
        session.device_helper, "smart_plugs_compact", []
    ):
        if device_excluded(device, config_entry.options):
            continue
        if (
            getattr(device, "supports_energy_saving_mode", False)
            and getattr(device, "power_threshold", None) is not None
        ):
            entities.append(
                PowerThresholdNumber(
                    device=device,
                    entry_id=config_entry.entry_id,
                )
            )
        if (
            getattr(device, "supports_energy_saving_mode", False)
            and getattr(device, "enter_duration_seconds", None) is not None
        ):
            entities.append(
                EnterDurationNumber(
                    device=device,
                    entry_id=config_entry.entry_id,
                )
            )
        if (
            getattr(device, "supports_led_brightness", False)
            and getattr(device, "led_brightness", None) is not None
        ):
            entities.append(
                LedBrightnessNumber(
                    device=device,
                    entry_id=config_entry.entry_id,
                )
            )

    # Display config numbers: brightness + on-time (ThermostatGen2 / RoomThermostat2).
    for device in (
        session.device_helper.thermostats + session.device_helper.roomthermostats
    ):
        if device_excluded(device, config_entry.options):
            continue
        if (
            getattr(device, "supports_display_configuration", False)
            and getattr(device, "display_brightness", None) is not None
        ):
            entities.append(
                DisplayBrightnessNumber(
                    device=device,
                    entry_id=config_entry.entry_id,
                )
            )
        if (
            getattr(device, "supports_display_configuration", False)
            and getattr(device, "display_on_time", None) is not None
        ):
            entities.append(
                DisplayOnTimeNumber(
                    device=device,
                    entry_id=config_entry.entry_id,
                )
            )

    for siren in getattr(session.device_helper, "outdoor_sirens", []):
        if device_excluded(siren, config_entry.options):
            continue
        if getattr(siren, "siren", None) is None:
            continue
        entities.append(
            SirenConfigNumber(siren, config_entry.entry_id, *_SIREN_ALARM_DURATION)
        )
        entities.append(
            SirenConfigNumber(siren, config_entry.entry_id, *_SIREN_FLASH_DURATION)
        )
        entities.append(
            SirenConfigNumber(siren, config_entry.entry_id, *_SIREN_ALARM_DELAY)
        )
        entities.append(
            SirenConfigNumber(siren, config_entry.entry_id, *_SIREN_FLASH_DELAY)
        )

    # DimmerConfiguration calibration numbers (micromodule dimmer, #123).
    for device in getattr(session.device_helper, "micromodule_dimmers", []):
        if device_excluded(device, config_entry.options):
            continue
        if getattr(device, "supports_dimmer_configuration", False):
            entities.append(
                DimmerConfigNumber(device, config_entry.entry_id, "min", 0, 100)
            )
            entities.append(
                DimmerConfigNumber(device, config_entry.entry_id, "max", 0, 100)
            )
            entities.append(
                DimmerConfigNumber(device, config_entry.entry_id, "speed", 1, 10)
            )

    if entities:
        async_add_entities(entities)


# (field, translation_key, unit, min, max) — siren config numbers (#120).
# alarmDuration/flashDuration are minutes; alarmDelay/flashDelay are seconds 0-180.
_SIREN_ALARM_DURATION = (
    "alarm_duration",
    "siren_alarm_duration",
    UnitOfTime.MINUTES,
    0,
    60,
)
_SIREN_FLASH_DURATION = (
    "flash_duration",
    "siren_flash_duration",
    UnitOfTime.MINUTES,
    0,
    60,
)
_SIREN_ALARM_DELAY = ("alarm_delay", "siren_alarm_delay", UnitOfTime.SECONDS, 0, 180)
_SIREN_FLASH_DELAY = ("flash_delay", "siren_flash_delay", UnitOfTime.SECONDS, 0, 180)


class SirenConfigNumber(SHCEntity, NumberEntity):
    """Configurable Outdoor Siren duration/delay (#120).

    Each field maps to one key of outdoorSirenConfiguration; the lib re-sends the
    full config block on write (Bosch requires all 5 fields together).
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_step = 1.0
    _attr_mode = NumberMode.BOX

    def __init__(self, device, entry_id, field, translation_key, unit, lo, hi) -> None:
        """Initialize the siren configuration number."""
        super().__init__(device, entry_id)
        self._field = field
        self._attr_translation_key = translation_key
        del (
            self._attr_name
        )  # dynamic translation_key; remove None set by SHCEntity.__init__
        self._attr_native_unit_of_measurement = unit
        self._attr_native_min_value = float(lo)
        self._attr_native_max_value = float(hi)
        self._attr_unique_id = f"{device.root_device_id}_{device.id}_{field}"

    @property
    def native_value(self) -> float | None:
        """Return the current value of the siren configuration field."""
        val = getattr(self._device.siren, self._field, None)
        return None if val is None else float(val)

    async def async_set_native_value(self, value: float) -> None:
        """Set the siren configuration field, clamped to valid range."""
        clamped = int(
            max(self._attr_native_min_value, min(self._attr_native_max_value, value))
        )
        try:
            await self._device.siren.async_set_configuration(**{self._field: clamped})
        except (
            AttributeError,
            KeyError,
            aiohttp.ClientError,
            asyncio.TimeoutError,
        ) as err:
            LOGGER.warning(
                "Unable to set %s for %s: %s", self._field, self._device.name, err
            )


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
        self._attr_name = attr_name
        self._attr_unique_id = (
            f"{device.root_device_id}_{device.id}"
            if attr_name is None
            else f"{device.root_device_id}_{device.id}_{attr_name.lower()}"
        )
        self._device: SHCThermostat = device

    async def async_set_native_value(self, value: float) -> None:
        """Update the current value, clamped to [native_min_value, native_max_value]."""
        clamped = max(self.native_min_value, min(self.native_max_value, value))
        try:
            await self._device.async_set_offset(clamped)
        except (
            AttributeError,
            KeyError,
            aiohttp.ClientError,
            asyncio.TimeoutError,
        ) as err:
            LOGGER.warning("Unable to set offset for %s: %s", self._device.name, err)

    @property
    def native_value(self) -> float:
        """Return the value of the number."""
        return self._device.offset

    @property
    def native_step(self) -> float:
        """Return the step of the number."""
        step = self._device.step_size
        return step if step is not None and step > 0 else 0.5

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
    Range 1-60 s (lib range: 10-600 tenths). Step 0.1 s.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_native_min_value = 0.1
    _attr_native_max_value = 60.0
    _attr_native_step = 0.1
    _attr_mode = NumberMode.BOX
    _attr_translation_key = "impulse_length"

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize the impulse length number."""
        super().__init__(device, entry_id)
        self._attr_unique_id = f"{device.root_device_id}_{device.id}_impulse_length"

    @property
    def native_value(self) -> float | None:
        """Return the impulse length in seconds."""
        raw = getattr(self._device, "impulse_length", None)
        if raw is None:
            return None
        # lib stores in tenths of seconds → divide by 10
        return raw / 10.0

    async def async_set_native_value(self, value: float) -> None:
        """Set the impulse length; convert seconds → tenths of seconds (int)."""
        clamped = max(
            self._attr_native_min_value, min(self._attr_native_max_value, value)
        )
        try:
            await self._device.async_set_impulse_length(round(clamped * 10))
        except (
            AttributeError,
            KeyError,
            aiohttp.ClientError,
            asyncio.TimeoutError,
        ) as err:
            LOGGER.warning(
                "Unable to set impulse length for %s: %s", self._device.name, err
            )


class HeatingCircuitSetpointNumber(SHCEntity, NumberEntity):
    """NumberEntity for HeatingCircuit eco/comfort setpoint temperatures.

    The HeatingCircuitService exposes setpoint_temperature_eco and
    setpoint_temperature_comfort as read/write float properties. Range 5-30 °C,
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

    async def async_set_native_value(self, value: float) -> None:
        """Write the setpoint temperature, clamped to valid range."""
        clamped = max(
            self._attr_native_min_value, min(self._attr_native_max_value, value)
        )
        async_setter = getattr(self._device, f"async_set_{self._setter_name}", None)
        if async_setter is None:
            LOGGER.warning(
                "Async setter async_set_%s unavailable for %s",
                self._setter_name,
                self._device.name,
            )
            return
        try:
            await async_setter(clamped)
        except (
            AttributeError,
            KeyError,
            aiohttp.ClientError,
            asyncio.TimeoutError,
        ) as err:
            LOGGER.warning(
                "Unable to write %s for %s: %s",
                self._setter_name,
                self._device.name,
                err,
            )


class PowerThresholdNumber(SHCEntity, NumberEntity):
    """NumberEntity for the energy-saving power threshold of a smart plug.

    When the plug draws less than this value for enterDurationSeconds, energy
    saving mode turns the outlet off.  Watt range 0-3680 W (16 A socket), step 1 W.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_device_class = NumberDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_native_min_value = 0.0
    _attr_native_max_value = 3680.0
    _attr_native_step = 1.0
    _attr_mode = NumberMode.BOX
    _attr_translation_key = "energy_saving_power_threshold"

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize the power threshold number."""
        super().__init__(device, entry_id)
        self._attr_unique_id = f"{device.root_device_id}_{device.id}_power_threshold"

    @property
    def native_value(self) -> float | None:
        """Return the power threshold in watts."""
        return getattr(self._device, "power_threshold", None)

    async def async_set_native_value(self, value: float) -> None:
        """Set the power threshold, clamped to valid range."""
        clamped = max(
            self._attr_native_min_value, min(self._attr_native_max_value, value)
        )
        try:
            await self._device.async_set_power_threshold(clamped)
        except (
            AttributeError,
            KeyError,
            aiohttp.ClientError,
            asyncio.TimeoutError,
        ) as err:
            LOGGER.warning(
                "Unable to set power threshold for %s: %s", self._device.name, err
            )


class EnterDurationNumber(SHCEntity, NumberEntity):
    """NumberEntity for the energy-saving enter duration of a smart plug.

    Number of seconds the plug must draw below the threshold before turning off.
    Range 1-3600 s, step 1 s.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_native_min_value = 1.0
    _attr_native_max_value = 3600.0
    _attr_native_step = 1.0
    _attr_mode = NumberMode.BOX
    _attr_translation_key = "energy_saving_enter_duration"

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize the enter duration number."""
        super().__init__(device, entry_id)
        self._attr_unique_id = (
            f"{device.root_device_id}_{device.id}_enter_duration_seconds"
        )

    @property
    def native_value(self) -> float | None:
        """Return the enter duration in seconds."""
        val = getattr(self._device, "enter_duration_seconds", None)
        if val is None:
            return None
        return float(val)

    async def async_set_native_value(self, value: float) -> None:
        """Set the enter duration, clamped to valid range."""
        clamped = max(
            self._attr_native_min_value, min(self._attr_native_max_value, value)
        )
        try:
            await self._device.async_set_enter_duration_seconds(int(clamped))
        except (
            AttributeError,
            KeyError,
            aiohttp.ClientError,
            asyncio.TimeoutError,
        ) as err:
            LOGGER.warning(
                "Unable to set enter duration for %s: %s", self._device.name, err
            )


class LedBrightnessNumber(SHCEntity, NumberEntity):
    """NumberEntity for the LED brightness of a smart plug.

    Bounds are read from the lib service (min/max/step from device state).
    Falls back to 0-100 if not yet populated.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.SLIDER
    _attr_translation_key = "led_brightness"

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize the LED brightness number."""
        super().__init__(device, entry_id)
        self._attr_unique_id = f"{device.root_device_id}_{device.id}_led_brightness"

    @property
    def native_min_value(self) -> float:
        """Return min brightness from device service, fallback 0."""
        svc = getattr(self._device, "_led_brightness_configuration_service", None)
        if svc is not None:
            v = getattr(svc, "min_brightness", None)
            if v is not None:
                return float(v)
        return 0.0

    @property
    def native_max_value(self) -> float:
        """Return max brightness from device service, fallback 100."""
        svc = getattr(self._device, "_led_brightness_configuration_service", None)
        if svc is not None:
            v = getattr(svc, "max_brightness", None)
            if v is not None:
                return float(v)
        return 100.0

    @property
    def native_step(self) -> float:
        """Return step size from device service, fallback 1."""
        svc = getattr(self._device, "_led_brightness_configuration_service", None)
        if svc is not None:
            v = getattr(svc, "step_size", None)
            if v is not None:
                return float(v)
        return 1.0

    @property
    def native_value(self) -> float | None:
        """Return current LED brightness."""
        return getattr(self._device, "led_brightness", None)

    async def async_set_native_value(self, value: float) -> None:
        """Set the LED brightness."""
        try:
            await self._device.async_set_led_brightness(value)
        except (
            AttributeError,
            KeyError,
            aiohttp.ClientError,
            asyncio.TimeoutError,
        ) as err:
            LOGGER.warning(
                "Unable to set LED brightness for %s: %s", self._device.name, err
            )


class DisplayBrightnessNumber(SHCEntity, NumberEntity):
    """NumberEntity for the display brightness of ThermostatGen2 / RoomThermostat2."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.SLIDER
    _attr_translation_key = "display_brightness"

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize the display brightness number."""
        super().__init__(device, entry_id)
        self._attr_unique_id = f"{device.root_device_id}_{device.id}_display_brightness"

    @property
    def native_min_value(self) -> float:
        """Return min brightness from device service, fallback 0."""
        svc = getattr(self._device, "_display_config_service", None)
        if svc is not None:
            v = getattr(svc, "display_brightness_min", None)
            if v is not None:
                return float(v)
        return 0.0

    @property
    def native_max_value(self) -> float:
        """Return max brightness from device service, fallback 100."""
        svc = getattr(self._device, "_display_config_service", None)
        if svc is not None:
            v = getattr(svc, "display_brightness_max", None)
            if v is not None:
                return float(v)
        return 100.0

    @property
    def native_step(self) -> float:
        """Return step size from device service, fallback 1."""
        svc = getattr(self._device, "_display_config_service", None)
        if svc is not None:
            v = getattr(svc, "display_brightness_step_size", None)
            if v is not None:
                return float(v)
        return 1.0

    @property
    def native_value(self) -> float | None:
        """Return current display brightness."""
        return getattr(self._device, "display_brightness", None)

    async def async_set_native_value(self, value: float) -> None:
        """Set the display brightness."""
        try:
            await self._device.async_set_display_brightness(value)
        except (
            AttributeError,
            KeyError,
            aiohttp.ClientError,
            asyncio.TimeoutError,
        ) as err:
            LOGGER.warning(
                "Unable to set display brightness for %s: %s", self._device.name, err
            )


class DisplayOnTimeNumber(SHCEntity, NumberEntity):
    """NumberEntity for the display on-time of ThermostatGen2 / RoomThermostat2.

    Display stays lit for this many seconds after interaction. Range from device.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_mode = NumberMode.BOX
    _attr_translation_key = "display_on_time"

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize the display on-time number."""
        super().__init__(device, entry_id)
        self._attr_unique_id = f"{device.root_device_id}_{device.id}_display_on_time"

    @property
    def native_min_value(self) -> float:
        """Return min on-time from device service, fallback 0."""
        svc = getattr(self._device, "_display_config_service", None)
        if svc is not None:
            v = getattr(svc, "display_on_time_min", None)
            if v is not None:
                return float(v)
        return 0.0

    @property
    def native_max_value(self) -> float:
        """Return max on-time from device service, fallback 3600."""
        svc = getattr(self._device, "_display_config_service", None)
        if svc is not None:
            v = getattr(svc, "display_on_time_max", None)
            if v is not None:
                return float(v)
        return 3600.0

    @property
    def native_step(self) -> float:
        """Return step size from device service, fallback 1."""
        svc = getattr(self._device, "_display_config_service", None)
        if svc is not None:
            v = getattr(svc, "display_on_time_step_size", None)
            if v is not None:
                return float(v)
        return 1.0

    @property
    def native_value(self) -> float | None:
        """Return current display on-time in seconds."""
        val = getattr(self._device, "display_on_time", None)
        if val is None:
            return None
        return float(val)

    async def async_set_native_value(self, value: float) -> None:
        """Set the display on-time."""
        try:
            await self._device.async_set_display_on_time(value)
        except (
            AttributeError,
            KeyError,
            aiohttp.ClientError,
            asyncio.TimeoutError,
        ) as err:
            LOGGER.warning(
                "Unable to set display on-time for %s: %s", self._device.name, err
            )


class DimmerConfigNumber(SHCEntity, NumberEntity):
    """NumberEntity for DimmerConfiguration calibration values (#123).

    field="min": calibrated minimum brightness (0-100).
    field="max": calibrated maximum brightness (0-100).
    field="speed": dimming speed 1 (fastest) to 10 (slowest).
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_step = 1.0

    _NAMES = {
        "min": "Dimmer Min Brightness",
        "max": "Dimmer Max Brightness",
        "speed": "Dimming Speed",
    }

    def __init__(
        self, device: SHCDevice, entry_id: str, field: str, lo: float, hi: float
    ) -> None:
        """Initialize the dimmer configuration number."""
        super().__init__(device, entry_id)
        self._field = field
        self._attr_name = self._NAMES[field]
        self._attr_native_min_value = float(lo)
        self._attr_native_max_value = float(hi)
        self._attr_mode = NumberMode.BOX if field == "speed" else NumberMode.SLIDER
        self._attr_unique_id = f"{device.root_device_id}_{device.id}_dimmer_{field}"

    @property
    def native_value(self) -> float | None:
        """Return the current dimmer calibration value."""
        svc = getattr(self._device, "dimmer_configuration", None)
        if svc is None:
            return None
        if self._field == "min":
            return float(svc.min_brightness)
        if self._field == "max":
            return float(svc.max_brightness)
        return float(svc.dimming_speed)

    async def async_set_native_value(self, value: float) -> None:
        """Set the dimmer calibration value, clamped to valid range."""
        svc = getattr(self._device, "dimmer_configuration", None)
        if svc is None:
            return
        clamped = int(
            max(self._attr_native_min_value, min(self._attr_native_max_value, value))
        )
        try:
            if self._field == "min":
                await svc.async_set_brightness_range(min_brightness=clamped)
            elif self._field == "max":
                await svc.async_set_brightness_range(max_brightness=clamped)
            else:
                await svc.async_set_dimming_speed(clamped)
        except (
            AttributeError,
            KeyError,
            aiohttp.ClientError,
            asyncio.TimeoutError,
        ) as err:
            LOGGER.warning(
                "Unable to set dimmer %s for %s: %s",
                self._field,
                self._device.name,
                err,
            )
