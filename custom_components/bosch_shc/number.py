"""Platform for number integration."""

from __future__ import annotations

import asyncio
import logging

import aiohttp
from boschshcpy import SHCSession, SHCThermostat
from boschshcpy.device import SHCDevice
from boschshcpy.exceptions import SHCConnectionError, SHCException
from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfPower, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import SHCEntity, device_excluded

LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 1


async def async_setup_entry(  # noqa: C901
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the SHC number platform."""
    entities: list[NumberEntity] = []
    session: SHCSession = config_entry.runtime_data.session

    for number in (
        list(session.device_helper.thermostats)
        + list(session.device_helper.roomthermostats)
        + list(getattr(session.device_helper, "wallthermostats", []))
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

    for device in session.device_helper.heating_circuits:  # type: ignore[assignment]
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
                range_attr="eco_temperature_range",
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
                range_attr="comfort_temperature_range",
            )
        )

    # Bypass auto-expiry timeout (hass#120 audit): fully modeled in
    # boschshcpy but never wired into an HA entity.
    for device in getattr(session.device_helper, "shutter_contacts2", []):
        if device_excluded(device, config_entry.options):
            continue
        entities.append(
            BypassTimeoutNumber(
                device=device,
                entry_id=config_entry.entry_id,
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
    for device in (  # type: ignore[assignment]
        list(session.device_helper.thermostats)
        + list(session.device_helper.roomthermostats)
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
# alarmDuration/flashDuration are minutes 1-15; alarmDelay/flashDelay are
# seconds 0-180 — bounds confirmed via APK decompile of the official app's
# slider widgets (layout_outdoorsiren_alarm_signal_fragment.xml /
# layout_outdoorsiren_alarm_delay_fragment.xml), not just the OpenAPI spec
# (already proven unreliable for this device's write paths, hass#120).
_SIREN_ALARM_DURATION = (
    "alarm_duration",
    "siren_alarm_duration",
    UnitOfTime.MINUTES,
    1,
    15,
)
_SIREN_FLASH_DURATION = (
    "flash_duration",
    "siren_flash_duration",
    UnitOfTime.MINUTES,
    1,
    15,
)
_SIREN_ALARM_DELAY = ("alarm_delay", "siren_alarm_delay", UnitOfTime.SECONDS, 0, 180)
_SIREN_FLASH_DELAY = ("flash_delay", "siren_flash_delay", UnitOfTime.SECONDS, 0, 180)


class SirenConfigNumber(SHCEntity, NumberEntity):  # type: ignore[misc]
    """Configurable Outdoor Siren duration/delay (#120).

    Each field maps to one key of outdoorSirenConfiguration; the lib re-sends the
    full config block on write (Bosch requires all 5 fields together).
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_step = 1.0
    _attr_mode = NumberMode.BOX

    def __init__(
        self,
        device: SHCDevice,
        entry_id: str,
        field: str,
        translation_key: str,
        unit: str,
        lo: int,
        hi: int,
    ) -> None:
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
        except (SHCException, SHCConnectionError) as err:
            raise HomeAssistantError(
                f"Failed to set {self._device.name} to {value}: {err}",
                translation_domain=DOMAIN,
                translation_key="number_set_failed",
            ) from err
        except (
            AttributeError,
            KeyError,
            aiohttp.ClientError,
            asyncio.TimeoutError,
        ) as err:
            LOGGER.warning(
                "Unable to set %s for %s: %s", self._field, self._device.name, err
            )


class SHCNumber(SHCEntity, NumberEntity):  # type: ignore[misc]
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
        self._attr_name = attr_name  # type: ignore[assignment]
        self._attr_unique_id = (
            f"{device.root_device_id}_{device.id}"
            if attr_name is None
            else f"{device.root_device_id}_{device.id}_{attr_name.lower()}"
        )
        self._device: SHCThermostat = device  # type: ignore[assignment]

    async def async_set_native_value(self, value: float) -> None:
        """Update the current value, clamped to [native_min_value, native_max_value]."""
        clamped = max(self.native_min_value, min(self.native_max_value, value))
        try:
            await self._device.async_set_offset(clamped)
        except (SHCException, SHCConnectionError) as err:
            raise HomeAssistantError(
                f"Failed to set {self._device.name} to {value}: {err}",
                translation_domain=DOMAIN,
                translation_key="number_set_failed",
            ) from err
        except (
            AttributeError,
            KeyError,
            aiohttp.ClientError,
            asyncio.TimeoutError,
        ) as err:
            LOGGER.warning("Unable to set offset for %s: %s", self._device.name, err)

    @property
    def native_value(self) -> float | None:
        """Return the value of the number."""
        offset = self._device.offset
        return float(offset) if offset is not None else None

    @property
    def native_step(self) -> float:
        """Return the step of the number."""
        step = self._device.step_size
        return step if step is not None and step > 0 else 0.5

    @property
    def native_min_value(self) -> float:
        """Return the min value of the number."""
        return float(self._device.min_offset)

    @property
    def native_max_value(self) -> float:
        """Return the max value of the number."""
        return float(self._device.max_offset)


class ImpulseLengthNumber(SHCEntity, NumberEntity):  # type: ignore[misc]
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
        return float(raw) / 10.0

    async def async_set_native_value(self, value: float) -> None:
        """Set the impulse length; convert seconds → tenths of seconds (int)."""
        clamped = max(
            self._attr_native_min_value, min(self._attr_native_max_value, value)
        )
        try:
            await self._device.async_set_impulse_length(round(clamped * 10))
        except (SHCException, SHCConnectionError) as err:
            raise HomeAssistantError(
                f"Failed to set {self._device.name} to {value}: {err}",
                translation_domain=DOMAIN,
                translation_key="number_set_failed",
            ) from err
        except (
            AttributeError,
            KeyError,
            aiohttp.ClientError,
            asyncio.TimeoutError,
        ) as err:
            LOGGER.warning(
                "Unable to set impulse length for %s: %s", self._device.name, err
            )


class HeatingCircuitSetpointNumber(SHCEntity, NumberEntity):  # type: ignore[misc]
    """NumberEntity for HeatingCircuit eco/comfort setpoint temperatures.

    The HeatingCircuitService exposes setpoint_temperature_eco and
    setpoint_temperature_comfort as read/write float properties. Step 0.5 °C
    (Bosch app convention); min/max are read from the device's own
    eco_temperature_range/comfort_temperature_range (hass#120 audit — the app
    reads a per-device range rather than a fixed constant), falling back to
    the previous 5-30 °C constant until the SHC has reported it.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
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
        range_attr: str,
    ) -> None:
        """Initialize the heating circuit setpoint number."""
        super().__init__(device, entry_id)
        self._attr_name = label  # type: ignore[assignment]
        self._attr_unique_id = (
            f"{device.root_device_id}_{device.id}_{attr_name.lower()}"
        )
        self._getter_name = getter_name
        self._setter_name = setter_name
        self._range_attr = range_attr

    @property
    def native_min_value(self) -> float:
        """Return the minimum settable temperature for this preset."""
        rng = getattr(self._device, self._range_attr, None)
        return rng[0] if rng is not None else 5.0

    @property
    def native_max_value(self) -> float:
        """Return the maximum settable temperature for this preset."""
        rng = getattr(self._device, self._range_attr, None)
        return rng[1] if rng is not None else 30.0

    @property
    def native_value(self) -> float | None:
        """Return the setpoint temperature."""
        try:
            svc = getattr(self._device, "_heating_circuit_service", None)
            if svc is None:
                return None
            value = getattr(svc, self._getter_name)
            # setpoint_temperature_eco/_comfort are typed float | None: a
            # heating circuit that never had that preset configured
            # legitimately returns None here, not an AttributeError.
            return None if value is None else float(value)
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
        clamped = max(self.native_min_value, min(self.native_max_value, value))
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
        except (SHCException, SHCConnectionError) as err:
            raise HomeAssistantError(
                f"Failed to set {self._device.name} to {value}: {err}",
                translation_domain=DOMAIN,
                translation_key="number_set_failed",
            ) from err
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


class PowerThresholdNumber(SHCEntity, NumberEntity):  # type: ignore[misc]
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
        except (SHCException, SHCConnectionError) as err:
            raise HomeAssistantError(
                f"Failed to set {self._device.name} to {value}: {err}",
                translation_domain=DOMAIN,
                translation_key="number_set_failed",
            ) from err
        except (
            AttributeError,
            KeyError,
            aiohttp.ClientError,
            asyncio.TimeoutError,
        ) as err:
            LOGGER.warning(
                "Unable to set power threshold for %s: %s", self._device.name, err
            )


class EnterDurationNumber(SHCEntity, NumberEntity):  # type: ignore[misc]
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
        except (SHCException, SHCConnectionError) as err:
            raise HomeAssistantError(
                f"Failed to set {self._device.name} to {value}: {err}",
                translation_domain=DOMAIN,
                translation_key="number_set_failed",
            ) from err
        except (
            AttributeError,
            KeyError,
            aiohttp.ClientError,
            asyncio.TimeoutError,
        ) as err:
            LOGGER.warning(
                "Unable to set enter duration for %s: %s", self._device.name, err
            )


class LedBrightnessNumber(SHCEntity, NumberEntity):  # type: ignore[misc]
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
            await self._device.async_set_led_brightness(round(value))
        except (SHCException, SHCConnectionError) as err:
            raise HomeAssistantError(
                f"Failed to set {self._device.name} to {value}: {err}",
                translation_domain=DOMAIN,
                translation_key="number_set_failed",
            ) from err
        except (
            AttributeError,
            KeyError,
            aiohttp.ClientError,
            asyncio.TimeoutError,
        ) as err:
            LOGGER.warning(
                "Unable to set LED brightness for %s: %s", self._device.name, err
            )


class DisplayBrightnessNumber(SHCEntity, NumberEntity):  # type: ignore[misc]
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
            await self._device.async_set_display_brightness(round(value))
        except (SHCException, SHCConnectionError) as err:
            raise HomeAssistantError(
                f"Failed to set {self._device.name} to {value}: {err}",
                translation_domain=DOMAIN,
                translation_key="number_set_failed",
            ) from err
        except (
            AttributeError,
            KeyError,
            aiohttp.ClientError,
            asyncio.TimeoutError,
        ) as err:
            LOGGER.warning(
                "Unable to set display brightness for %s: %s", self._device.name, err
            )


class DisplayOnTimeNumber(SHCEntity, NumberEntity):  # type: ignore[misc]
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
            await self._device.async_set_display_on_time(round(value))
        except (SHCException, SHCConnectionError) as err:
            raise HomeAssistantError(
                f"Failed to set {self._device.name} to {value}: {err}",
                translation_domain=DOMAIN,
                translation_key="number_set_failed",
            ) from err
        except (
            AttributeError,
            KeyError,
            aiohttp.ClientError,
            asyncio.TimeoutError,
        ) as err:
            LOGGER.warning(
                "Unable to set display on-time for %s: %s", self._device.name, err
            )


class DimmerConfigNumber(SHCEntity, NumberEntity):  # type: ignore[misc]
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
        self._attr_name = self._NAMES[field]  # type: ignore[assignment]
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
        except (SHCException, SHCConnectionError) as err:
            raise HomeAssistantError(
                f"Failed to set {self._device.name} to {value}: {err}",
                translation_domain=DOMAIN,
                translation_key="number_set_failed",
            ) from err
        except (
            AttributeError,
            KeyError,
            ValueError,
            aiohttp.ClientError,
            asyncio.TimeoutError,
        ) as err:
            # ValueError: async_set_brightness_range() rejects an inverted
            # range (min >= max) — min/max are independent HA number
            # entities, so setting one past the other's cached value is a
            # realistic user action, not a programming error.
            LOGGER.warning(
                "Unable to set dimmer %s for %s: %s",
                self._field,
                self._device.name,
                err,
            )


class BypassTimeoutNumber(SHCEntity, NumberEntity):  # type: ignore[misc]
    """Bypass auto-expiry timeout for a door/window contact (hass#120 audit).

    Fully modeled in boschshcpy (BypassService.timeout /
    SHCShutterContact2.bypass_timeout) but never wired into an HA entity.
    Unit/bounds (1-15 minutes) confirmed via APK decompile of the official
    app's bypass_configuration.xml slider (app:quantityUnit="MINUTE") — the
    library previously assumed seconds (no OpenAPI spec exists for this
    service to confirm either way).
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_native_min_value = 1.0
    _attr_native_max_value = 15.0
    _attr_native_step = 1.0
    _attr_mode = NumberMode.BOX
    _attr_translation_key = "bypass_timeout"

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize the bypass timeout number."""
        super().__init__(device, entry_id)
        self._attr_unique_id = f"{device.root_device_id}_{device.id}_bypass_timeout"

    @property
    def native_value(self) -> float | None:
        """Return the bypass auto-expiry timeout in minutes."""
        raw = getattr(self._device, "bypass_timeout", None)
        return None if raw is None else float(raw)

    async def async_set_native_value(self, value: float) -> None:
        """Set the bypass auto-expiry timeout, clamped to 1-15 minutes."""
        clamped = max(
            self._attr_native_min_value, min(self._attr_native_max_value, value)
        )
        try:
            await self._device.async_set_bypass_timeout(round(clamped))
        except (SHCException, SHCConnectionError) as err:
            raise HomeAssistantError(
                f"Failed to set {self._device.name} to {value}: {err}",
                translation_domain=DOMAIN,
                translation_key="number_set_failed",
            ) from err
        except (
            AttributeError,
            KeyError,
            aiohttp.ClientError,
            asyncio.TimeoutError,
        ) as err:
            LOGGER.warning(
                "Unable to write bypass_timeout for %s: %s",
                self._device.name,
                err,
            )
