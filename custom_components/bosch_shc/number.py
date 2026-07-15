"""Platform for number integration."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

import aiohttp
from boschshcpy import (
    SHCHeatingCircuit,
    SHCMicromoduleDimmer,
    SHCMicromoduleRelay,
    SHCOutdoorSiren,
    SHCRoomThermostat2,
    SHCSession,
    SHCShutterContact2,
    SHCSmartPlug,
    SHCSmartPlugCompact,
    SHCThermostat,
    SHCThermostatGen2,
    SHCWallThermostat,
)
from boschshcpy.device import SHCDevice
from boschshcpy.exceptions import SHCException
from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
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


# Device-type unions used to parametrize SHCNumberEntityDescription[_DeviceT]
# below, mirroring sensor.py's core-prep pattern. Each alias covers exactly
# the concrete device classes the corresponding value_fn/set_value_fn is
# actually called with, per device_helper's per-collection return types (see
# async_setup_entry below).
type _TemperatureOffsetDevice = SHCThermostat | SHCWallThermostat | SHCRoomThermostat2
type _EnergySavingDevice = SHCSmartPlug | SHCSmartPlugCompact
type _DisplayConfigDevice = SHCThermostatGen2 | SHCRoomThermostat2


@dataclass(frozen=True, kw_only=True)
class SHCNumberEntityDescription[_DeviceT: SHCDevice](NumberEntityDescription):
    """Describes a SHC number entity.

    ``min_value_fn``/``max_value_fn``/``step_fn`` are only needed when the
    bound is read dynamically from the device/service at runtime (e.g. the
    heating-circuit setpoint range or a *ConfigurationService's reported
    min/max/step) — when absent, ``NumberEntity`` falls back to this
    description's own static ``native_min_value``/``native_max_value``/
    ``native_step`` fields.
    """

    value_fn: Callable[[_DeviceT], float | None]
    set_value_fn: Callable[[_DeviceT, float], Coroutine[Any, Any, None]]
    min_value_fn: Callable[[_DeviceT], float] | None = None
    max_value_fn: Callable[[_DeviceT], float] | None = None
    step_fn: Callable[[_DeviceT], float] | None = None


# ---------------------------------------------------------------------------
# Value/setter factories for number types with per-instance variation (siren
# config fields, dimmer calibration fields, heating circuit eco/comfort).
# ---------------------------------------------------------------------------


def _siren_value_fn(field: str) -> Callable[[SHCOutdoorSiren], float | None]:
    """Return a value_fn reading one field off the device's siren service."""

    def _fn(device: SHCOutdoorSiren) -> float | None:
        val = getattr(device.siren, field, None)
        return None if val is None else float(val)

    return _fn


def _siren_set_value_fn(
    field: str,
) -> Callable[[SHCOutdoorSiren, float], Coroutine[Any, Any, None]]:
    """Return a set_value_fn writing one field of the siren configuration.

    The lib re-sends the full config block on write (Bosch requires all 5
    fields together).
    """

    async def _fn(device: SHCOutdoorSiren, value: float) -> None:
        # async_set_configuration's keyword-only params have per-field types
        # (SoundLevel|None for sound_level, float|None for the rest); `field`
        # is only known at runtime, so the single-entry kwargs dict can't be
        # narrower than dict[str, Any] without lying about it.
        kwargs: dict[str, Any] = {field: int(value)}
        await device.siren.async_set_configuration(**kwargs)

    return _fn


def _dimmer_value_fn(field: str) -> Callable[[SHCMicromoduleDimmer], float | None]:
    """Return a value_fn reading one DimmerConfiguration calibration field."""

    def _fn(device: SHCMicromoduleDimmer) -> float | None:
        svc = getattr(device, "dimmer_configuration", None)
        if svc is None:
            return None
        if field == "min":
            return float(svc.min_brightness)
        if field == "max":
            return float(svc.max_brightness)
        return float(svc.dimming_speed)

    return _fn


def _dimmer_set_value_fn(
    field: str,
) -> Callable[[SHCMicromoduleDimmer, float], Coroutine[Any, Any, None]]:
    """Return a set_value_fn writing one DimmerConfiguration calibration field."""

    async def _fn(device: SHCMicromoduleDimmer, value: float) -> None:
        svc = getattr(device, "dimmer_configuration", None)
        if svc is None:
            return
        ivalue = int(value)
        if field == "min":
            await svc.async_set_brightness_range(min_brightness=ivalue)
        elif field == "max":
            await svc.async_set_brightness_range(max_brightness=ivalue)
        else:
            await svc.async_set_dimming_speed(ivalue)

    return _fn


def _heating_circuit_value_fn(
    getter_name: str,
) -> Callable[[SHCHeatingCircuit], float | None]:
    """Return a value_fn reading a HeatingCircuitService eco/comfort setpoint.

    setpoint_temperature_eco/_comfort are typed float | None: a heating
    circuit that never had that preset configured legitimately returns None
    here, not an AttributeError.
    """

    def _fn(device: SHCHeatingCircuit) -> float | None:
        svc = getattr(device, "_heating_circuit_service", None)
        if svc is None:
            return None
        try:
            value = getattr(svc, getter_name)
            return None if value is None else float(value)
        except (AttributeError, KeyError) as err:
            LOGGER.warning(
                "Unable to read %s for %s: %s", getter_name, device.name, err
            )
            return None

    return _fn


def _heating_circuit_set_value_fn(
    setter_name: str,
) -> Callable[[SHCHeatingCircuit, float], Coroutine[Any, Any, None]]:
    """Return a set_value_fn writing a HeatingCircuitService eco/comfort setpoint."""

    async def _fn(device: SHCHeatingCircuit, value: float) -> None:
        setter = getattr(device, f"async_set_{setter_name}", None)
        if setter is None:
            LOGGER.warning(
                "Async setter async_set_%s unavailable for %s",
                setter_name,
                device.name,
            )
            return
        await setter(value)

    return _fn


def _heating_circuit_min_fn(range_attr: str) -> Callable[[SHCHeatingCircuit], float]:
    """Return a min_value_fn reading a HeatingCircuit's eco/comfort temperature range.

    The app reads a per-device range (hass#120 audit) rather than a fixed
    constant, falling back to the previous 5-30 °C constant until the SHC
    has reported it.
    """

    def _fn(device: SHCHeatingCircuit) -> float:
        rng = getattr(device, range_attr, None)
        return rng[0] if rng is not None else 5.0

    return _fn


def _heating_circuit_max_fn(range_attr: str) -> Callable[[SHCHeatingCircuit], float]:
    def _fn(device: SHCHeatingCircuit) -> float:
        rng = getattr(device, range_attr, None)
        return rng[1] if rng is not None else 30.0

    return _fn


def _service_bound_fn[_DeviceT: SHCDevice](
    service_attr: str, field: str, default: float
) -> Callable[[_DeviceT], float]:
    """Return a min/max/step_fn reading a bound off a device's config service.

    Shared by LedBrightnessNumber/DisplayBrightnessNumber/DisplayOnTimeNumber
    — each reads its bounds from a *ConfigurationService, falling back to a
    static default when the service or field is not yet populated. Generic
    over the caller's concrete device type since it's reused across
    unrelated device unions (_EnergySavingDevice, _DisplayConfigDevice).
    """

    def _fn(device: _DeviceT) -> float:
        svc = getattr(device, service_attr, None)
        if svc is not None:
            val = getattr(svc, field, None)
            if val is not None:
                return float(val)
        return default

    return _fn


# ---------------------------------------------------------------------------
# Description keys
# ---------------------------------------------------------------------------

OFFSET = "offset"
IMPULSE_LENGTH = "impulse_length"
HEATING_CIRCUIT_SETPOINT_ECO = "setpointeco"
HEATING_CIRCUIT_SETPOINT_COMFORT = "setpointcomfort"
POWER_THRESHOLD = "power_threshold"
ENTER_DURATION = "enter_duration_seconds"
LED_BRIGHTNESS = "led_brightness"
DISPLAY_BRIGHTNESS = "display_brightness"
DISPLAY_ON_TIME = "display_on_time"
DIMMER_MIN = "dimmer_min"
DIMMER_MAX = "dimmer_max"
DIMMER_SPEED = "dimmer_speed"
BYPASS_TIMEOUT = "bypass_timeout"
SIREN_ALARM_DURATION = "alarm_duration"
SIREN_FLASH_DURATION = "flash_duration"
SIREN_ALARM_DELAY = "alarm_delay"
SIREN_FLASH_DELAY = "flash_delay"


def _offset_value_fn(device: _TemperatureOffsetDevice) -> float | None:
    offset = device.offset
    return float(offset) if offset is not None else None


async def _offset_set_value_fn(device: _TemperatureOffsetDevice, value: float) -> None:
    await device.async_set_offset(value)


def _offset_min_fn(device: _TemperatureOffsetDevice) -> float:
    return float(device.min_offset)


def _offset_max_fn(device: _TemperatureOffsetDevice) -> float:
    return float(device.max_offset)


def _offset_step_fn(device: _TemperatureOffsetDevice) -> float:
    step = device.step_size
    return step if step is not None and step > 0 else 0.5


def _impulse_length_value_fn(device: SHCMicromoduleRelay) -> float | None:
    raw = getattr(device, "impulse_length", None)
    if raw is None:
        return None
    # lib stores in tenths of seconds → divide by 10
    return float(raw) / 10.0


async def _impulse_length_set_value_fn(
    device: SHCMicromoduleRelay, value: float
) -> None:
    await device.async_set_impulse_length(round(value * 10))


def _power_threshold_value_fn(device: _EnergySavingDevice) -> float | None:
    return getattr(device, "power_threshold", None)


async def _power_threshold_set_value_fn(
    device: _EnergySavingDevice, value: float
) -> None:
    await device.async_set_power_threshold(value)


def _enter_duration_value_fn(device: _EnergySavingDevice) -> float | None:
    val = getattr(device, "enter_duration_seconds", None)
    return None if val is None else float(val)


async def _enter_duration_set_value_fn(
    device: _EnergySavingDevice, value: float
) -> None:
    await device.async_set_enter_duration_seconds(int(value))


def _led_brightness_value_fn(device: _EnergySavingDevice) -> float | None:
    return getattr(device, "led_brightness", None)


async def _led_brightness_set_value_fn(
    device: _EnergySavingDevice, value: float
) -> None:
    await device.async_set_led_brightness(round(value))


def _display_brightness_value_fn(device: _DisplayConfigDevice) -> float | None:
    return getattr(device, "display_brightness", None)


async def _display_brightness_set_value_fn(
    device: _DisplayConfigDevice, value: float
) -> None:
    await device.async_set_display_brightness(round(value))


def _display_on_time_value_fn(device: _DisplayConfigDevice) -> float | None:
    val = getattr(device, "display_on_time", None)
    return None if val is None else float(val)


async def _display_on_time_set_value_fn(
    device: _DisplayConfigDevice, value: float
) -> None:
    await device.async_set_display_on_time(round(value))


def _bypass_timeout_value_fn(device: SHCShutterContact2) -> float | None:
    raw = getattr(device, "bypass_timeout", None)
    return None if raw is None else float(raw)


async def _bypass_timeout_set_value_fn(
    device: SHCShutterContact2, value: float
) -> None:
    await device.async_set_bypass_timeout(round(value))


NUMBER_DESCRIPTIONS: dict[str, SHCNumberEntityDescription[Any]] = {
    OFFSET: SHCNumberEntityDescription[_TemperatureOffsetDevice](
        key=OFFSET,
        name="Offset",
        device_class=NumberDeviceClass.TEMPERATURE,
        entity_category=EntityCategory.DIAGNOSTIC,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=_offset_value_fn,
        set_value_fn=_offset_set_value_fn,
        min_value_fn=_offset_min_fn,
        max_value_fn=_offset_max_fn,
        step_fn=_offset_step_fn,
    ),
    IMPULSE_LENGTH: SHCNumberEntityDescription[SHCMicromoduleRelay](
        key=IMPULSE_LENGTH,
        translation_key=IMPULSE_LENGTH,
        entity_category=EntityCategory.CONFIG,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        native_min_value=0.1,
        native_max_value=60.0,
        native_step=0.1,
        mode=NumberMode.BOX,
        value_fn=_impulse_length_value_fn,
        set_value_fn=_impulse_length_set_value_fn,
    ),
    HEATING_CIRCUIT_SETPOINT_ECO: SHCNumberEntityDescription[SHCHeatingCircuit](
        key=HEATING_CIRCUIT_SETPOINT_ECO,
        name="Setpoint Eco Temperature",
        entity_category=EntityCategory.CONFIG,
        device_class=NumberDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        native_step=0.5,
        mode=NumberMode.BOX,
        value_fn=_heating_circuit_value_fn("setpoint_temperature_eco"),
        set_value_fn=_heating_circuit_set_value_fn("setpoint_temperature_eco"),
        min_value_fn=_heating_circuit_min_fn("eco_temperature_range"),
        max_value_fn=_heating_circuit_max_fn("eco_temperature_range"),
    ),
    HEATING_CIRCUIT_SETPOINT_COMFORT: SHCNumberEntityDescription[SHCHeatingCircuit](
        key=HEATING_CIRCUIT_SETPOINT_COMFORT,
        name="Setpoint Comfort Temperature",
        entity_category=EntityCategory.CONFIG,
        device_class=NumberDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        native_step=0.5,
        mode=NumberMode.BOX,
        value_fn=_heating_circuit_value_fn("setpoint_temperature_comfort"),
        set_value_fn=_heating_circuit_set_value_fn("setpoint_temperature_comfort"),
        min_value_fn=_heating_circuit_min_fn("comfort_temperature_range"),
        max_value_fn=_heating_circuit_max_fn("comfort_temperature_range"),
    ),
    POWER_THRESHOLD: SHCNumberEntityDescription[_EnergySavingDevice](
        key=POWER_THRESHOLD,
        translation_key="energy_saving_power_threshold",
        entity_category=EntityCategory.CONFIG,
        device_class=NumberDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        native_min_value=0.0,
        native_max_value=3680.0,
        native_step=1.0,
        mode=NumberMode.BOX,
        value_fn=_power_threshold_value_fn,
        set_value_fn=_power_threshold_set_value_fn,
    ),
    ENTER_DURATION: SHCNumberEntityDescription[_EnergySavingDevice](
        key=ENTER_DURATION,
        translation_key="energy_saving_enter_duration",
        entity_category=EntityCategory.CONFIG,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        native_min_value=1.0,
        native_max_value=3600.0,
        native_step=1.0,
        mode=NumberMode.BOX,
        value_fn=_enter_duration_value_fn,
        set_value_fn=_enter_duration_set_value_fn,
    ),
    LED_BRIGHTNESS: SHCNumberEntityDescription[_EnergySavingDevice](
        key=LED_BRIGHTNESS,
        translation_key=LED_BRIGHTNESS,
        entity_category=EntityCategory.CONFIG,
        mode=NumberMode.SLIDER,
        value_fn=_led_brightness_value_fn,
        set_value_fn=_led_brightness_set_value_fn,
        min_value_fn=_service_bound_fn(
            "_led_brightness_configuration_service", "min_brightness", 0.0
        ),
        max_value_fn=_service_bound_fn(
            "_led_brightness_configuration_service", "max_brightness", 100.0
        ),
        step_fn=_service_bound_fn(
            "_led_brightness_configuration_service", "step_size", 1.0
        ),
    ),
    DISPLAY_BRIGHTNESS: SHCNumberEntityDescription[_DisplayConfigDevice](
        key=DISPLAY_BRIGHTNESS,
        translation_key=DISPLAY_BRIGHTNESS,
        entity_category=EntityCategory.CONFIG,
        mode=NumberMode.SLIDER,
        value_fn=_display_brightness_value_fn,
        set_value_fn=_display_brightness_set_value_fn,
        min_value_fn=_service_bound_fn(
            "_display_config_service", "display_brightness_min", 0.0
        ),
        max_value_fn=_service_bound_fn(
            "_display_config_service", "display_brightness_max", 100.0
        ),
        step_fn=_service_bound_fn(
            "_display_config_service", "display_brightness_step_size", 1.0
        ),
    ),
    DISPLAY_ON_TIME: SHCNumberEntityDescription[_DisplayConfigDevice](
        key=DISPLAY_ON_TIME,
        translation_key=DISPLAY_ON_TIME,
        entity_category=EntityCategory.CONFIG,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        mode=NumberMode.BOX,
        value_fn=_display_on_time_value_fn,
        set_value_fn=_display_on_time_set_value_fn,
        min_value_fn=_service_bound_fn(
            "_display_config_service", "display_on_time_min", 0.0
        ),
        max_value_fn=_service_bound_fn(
            "_display_config_service", "display_on_time_max", 3600.0
        ),
        step_fn=_service_bound_fn(
            "_display_config_service", "display_on_time_step_size", 1.0
        ),
    ),
    DIMMER_MIN: SHCNumberEntityDescription[SHCMicromoduleDimmer](
        key=DIMMER_MIN,
        name="Dimmer Min Brightness",
        entity_category=EntityCategory.CONFIG,
        native_min_value=0.0,
        native_max_value=100.0,
        native_step=1.0,
        mode=NumberMode.SLIDER,
        value_fn=_dimmer_value_fn("min"),
        set_value_fn=_dimmer_set_value_fn("min"),
    ),
    DIMMER_MAX: SHCNumberEntityDescription[SHCMicromoduleDimmer](
        key=DIMMER_MAX,
        name="Dimmer Max Brightness",
        entity_category=EntityCategory.CONFIG,
        native_min_value=0.0,
        native_max_value=100.0,
        native_step=1.0,
        mode=NumberMode.SLIDER,
        value_fn=_dimmer_value_fn("max"),
        set_value_fn=_dimmer_set_value_fn("max"),
    ),
    DIMMER_SPEED: SHCNumberEntityDescription[SHCMicromoduleDimmer](
        key=DIMMER_SPEED,
        name="Dimming Speed",
        entity_category=EntityCategory.CONFIG,
        native_min_value=1.0,
        native_max_value=10.0,
        native_step=1.0,
        mode=NumberMode.BOX,
        value_fn=_dimmer_value_fn("speed"),
        set_value_fn=_dimmer_set_value_fn("speed"),
    ),
    BYPASS_TIMEOUT: SHCNumberEntityDescription[SHCShutterContact2](
        key=BYPASS_TIMEOUT,
        translation_key=BYPASS_TIMEOUT,
        entity_category=EntityCategory.CONFIG,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        native_min_value=1.0,
        native_max_value=15.0,
        native_step=1.0,
        mode=NumberMode.BOX,
        value_fn=_bypass_timeout_value_fn,
        set_value_fn=_bypass_timeout_set_value_fn,
    ),
}

# Siren config numbers (#120). Bounds confirmed via APK decompile of the
# app's slider widgets, not the OpenAPI spec (already proven unreliable for
# this device's write paths).
_SIREN_ALARM_DURATION = SHCNumberEntityDescription[SHCOutdoorSiren](
    key=SIREN_ALARM_DURATION,
    translation_key="siren_alarm_duration",
    entity_category=EntityCategory.CONFIG,
    native_unit_of_measurement=UnitOfTime.MINUTES,
    native_min_value=1.0,
    native_max_value=15.0,
    native_step=1.0,
    mode=NumberMode.BOX,
    value_fn=_siren_value_fn(SIREN_ALARM_DURATION),
    set_value_fn=_siren_set_value_fn(SIREN_ALARM_DURATION),
)
_SIREN_FLASH_DURATION = SHCNumberEntityDescription[SHCOutdoorSiren](
    key=SIREN_FLASH_DURATION,
    translation_key="siren_flash_duration",
    entity_category=EntityCategory.CONFIG,
    native_unit_of_measurement=UnitOfTime.MINUTES,
    native_min_value=1.0,
    native_max_value=15.0,
    native_step=1.0,
    mode=NumberMode.BOX,
    value_fn=_siren_value_fn(SIREN_FLASH_DURATION),
    set_value_fn=_siren_set_value_fn(SIREN_FLASH_DURATION),
)
_SIREN_ALARM_DELAY = SHCNumberEntityDescription[SHCOutdoorSiren](
    key=SIREN_ALARM_DELAY,
    translation_key="siren_alarm_delay",
    entity_category=EntityCategory.CONFIG,
    native_unit_of_measurement=UnitOfTime.SECONDS,
    native_min_value=0.0,
    native_max_value=180.0,
    native_step=1.0,
    mode=NumberMode.BOX,
    value_fn=_siren_value_fn(SIREN_ALARM_DELAY),
    set_value_fn=_siren_set_value_fn(SIREN_ALARM_DELAY),
)
_SIREN_FLASH_DELAY = SHCNumberEntityDescription[SHCOutdoorSiren](
    key=SIREN_FLASH_DELAY,
    translation_key="siren_flash_delay",
    entity_category=EntityCategory.CONFIG,
    native_unit_of_measurement=UnitOfTime.SECONDS,
    native_min_value=0.0,
    native_max_value=180.0,
    native_step=1.0,
    mode=NumberMode.BOX,
    value_fn=_siren_value_fn(SIREN_FLASH_DELAY),
    set_value_fn=_siren_set_value_fn(SIREN_FLASH_DELAY),
)

NUMBER_DESCRIPTIONS[SIREN_ALARM_DURATION] = _SIREN_ALARM_DURATION
NUMBER_DESCRIPTIONS[SIREN_FLASH_DURATION] = _SIREN_FLASH_DURATION
NUMBER_DESCRIPTIONS[SIREN_ALARM_DELAY] = _SIREN_ALARM_DELAY
NUMBER_DESCRIPTIONS[SIREN_FLASH_DELAY] = _SIREN_FLASH_DELAY


async def async_setup_entry(  # noqa: C901
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the SHC number platform."""
    entities: list[NumberEntity] = []
    session: SHCSession = config_entry.runtime_data.session

    # Temperature-drop service drop value (APK-traced, live-confirmed across
    # 12 real rooms) -- always on, no-op if a room has no such service.
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
            TemperatureDropValueNumber(
                device=climate, room=room, entry_id=config_entry.entry_id
            )
        )

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
                entity_description=NUMBER_DESCRIPTIONS[OFFSET],
                entry_id=config_entry.entry_id,
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
            SHCNumber(
                device=device,
                entity_description=NUMBER_DESCRIPTIONS[IMPULSE_LENGTH],
                entry_id=config_entry.entry_id,
            )
        )

    for device in session.device_helper.heating_circuits:  # type: ignore[assignment]
        if device_excluded(device, config_entry.options):
            continue
        entities.append(
            SHCNumber(
                device=device,
                entity_description=NUMBER_DESCRIPTIONS[HEATING_CIRCUIT_SETPOINT_ECO],
                entry_id=config_entry.entry_id,
            )
        )
        entities.append(
            SHCNumber(
                device=device,
                entity_description=NUMBER_DESCRIPTIONS[
                    HEATING_CIRCUIT_SETPOINT_COMFORT
                ],
                entry_id=config_entry.entry_id,
            )
        )

    # Bypass auto-expiry timeout (hass#120 audit): fully modeled in
    # boschshcpy but never wired into an HA entity.
    for device in getattr(session.device_helper, "shutter_contacts2", []):
        if device_excluded(device, config_entry.options):
            continue
        entities.append(
            SHCNumber(
                device=device,
                entity_description=NUMBER_DESCRIPTIONS[BYPASS_TIMEOUT],
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
                SHCNumber(
                    device=device,
                    entity_description=NUMBER_DESCRIPTIONS[POWER_THRESHOLD],
                    entry_id=config_entry.entry_id,
                )
            )
        if (
            getattr(device, "supports_energy_saving_mode", False)
            and getattr(device, "enter_duration_seconds", None) is not None
        ):
            entities.append(
                SHCNumber(
                    device=device,
                    entity_description=NUMBER_DESCRIPTIONS[ENTER_DURATION],
                    entry_id=config_entry.entry_id,
                )
            )
        if (
            getattr(device, "supports_led_brightness", False)
            and getattr(device, "led_brightness", None) is not None
        ):
            entities.append(
                SHCNumber(
                    device=device,
                    entity_description=NUMBER_DESCRIPTIONS[LED_BRIGHTNESS],
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
                SHCNumber(
                    device=device,
                    entity_description=NUMBER_DESCRIPTIONS[DISPLAY_BRIGHTNESS],
                    entry_id=config_entry.entry_id,
                )
            )
        if (
            getattr(device, "supports_display_configuration", False)
            and getattr(device, "display_on_time", None) is not None
        ):
            entities.append(
                SHCNumber(
                    device=device,
                    entity_description=NUMBER_DESCRIPTIONS[DISPLAY_ON_TIME],
                    entry_id=config_entry.entry_id,
                )
            )

    for siren in getattr(session.device_helper, "outdoor_sirens", []):
        if device_excluded(siren, config_entry.options):
            continue
        if getattr(siren, "siren", None) is None:
            continue
        entities.append(
            SHCNumber(
                device=siren,
                entity_description=NUMBER_DESCRIPTIONS[SIREN_ALARM_DURATION],
                entry_id=config_entry.entry_id,
            )
        )
        entities.append(
            SHCNumber(
                device=siren,
                entity_description=NUMBER_DESCRIPTIONS[SIREN_FLASH_DURATION],
                entry_id=config_entry.entry_id,
            )
        )
        entities.append(
            SHCNumber(
                device=siren,
                entity_description=NUMBER_DESCRIPTIONS[SIREN_ALARM_DELAY],
                entry_id=config_entry.entry_id,
            )
        )
        entities.append(
            SHCNumber(
                device=siren,
                entity_description=NUMBER_DESCRIPTIONS[SIREN_FLASH_DELAY],
                entry_id=config_entry.entry_id,
            )
        )

    # DimmerConfiguration calibration numbers (micromodule dimmer, #123).
    for device in getattr(session.device_helper, "micromodule_dimmers", []):
        if device_excluded(device, config_entry.options):
            continue
        if getattr(device, "supports_dimmer_configuration", False):
            entities.append(
                SHCNumber(
                    device=device,
                    entity_description=NUMBER_DESCRIPTIONS[DIMMER_MIN],
                    entry_id=config_entry.entry_id,
                )
            )
            entities.append(
                SHCNumber(
                    device=device,
                    entity_description=NUMBER_DESCRIPTIONS[DIMMER_MAX],
                    entry_id=config_entry.entry_id,
                )
            )
            entities.append(
                SHCNumber(
                    device=device,
                    entity_description=NUMBER_DESCRIPTIONS[DIMMER_SPEED],
                    entry_id=config_entry.entry_id,
                )
            )

    if entities:
        async_add_entities(entities)


class SHCNumber[_DeviceT: SHCDevice](SHCEntity, NumberEntity):  # type: ignore[misc]
    """Representation of a SHC number, driven by a SHCNumberEntityDescription.

    Generic over the concrete device type _DeviceT so entity_description's
    value_fn/set_value_fn/min_value_fn/max_value_fn/step_fn can be typed
    against the actual boschshcpy model class each number kind reads from
    and writes to, instead of the generic SHCDevice base (core-prep: mirrors
    sensor.py's SHCSensor[_DeviceT] pattern).
    """

    entity_description: SHCNumberEntityDescription[_DeviceT]

    def __init__(
        self,
        device: _DeviceT,
        entity_description: SHCNumberEntityDescription[_DeviceT],
        entry_id: str,
    ) -> None:
        """Initialize a SHC number."""
        super().__init__(device, entry_id)
        self._device: _DeviceT = device
        self.entity_description = entity_description
        if entity_description.translation_key is not None:
            # dynamic translation_key; remove the None SHCEntity.__init__ set,
            # so HA falls back to the translation lookup instead of shadowing it.
            del self._attr_name
        else:
            self._attr_name = entity_description.name  # type: ignore[assignment]
        self._attr_unique_id = (
            f"{device.root_device_id}_{device.id}_{entity_description.key}"
        )

    @property
    def native_value(self) -> float | None:
        """Return the value of the number."""
        return self.entity_description.value_fn(self._device)

    @property
    def native_min_value(self) -> float:
        """Return the min value of the number."""
        if self.entity_description.min_value_fn is not None:
            return self.entity_description.min_value_fn(self._device)
        return super().native_min_value

    @property
    def native_max_value(self) -> float:
        """Return the max value of the number."""
        if self.entity_description.max_value_fn is not None:
            return self.entity_description.max_value_fn(self._device)
        return super().native_max_value

    @property
    def native_step(self) -> float | None:
        """Return the step of the number."""
        if self.entity_description.step_fn is not None:
            return self.entity_description.step_fn(self._device)
        return super().native_step

    async def async_set_native_value(self, value: float) -> None:
        """Update the current value, clamped to [native_min_value, native_max_value]."""
        clamped = max(self.native_min_value, min(self.native_max_value, value))
        try:
            await self.entity_description.set_value_fn(self._device, clamped)
        except SHCException as err:
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
            json.JSONDecodeError,
        ) as err:
            LOGGER.warning(
                "Unable to set %s for %s: %s",
                self.entity_description.key,
                self._device.name,
                err,
            )


class TemperatureDropValueNumber(SHCEntity, NumberEntity):  # type: ignore[misc]
    """How many degrees a room's temperature-drop service lowers the setpoint.

    Not in the official OpenAPI spec; APK ground-truth
    (RestRequests.getTemperatureDropService/putTemperatureDropService), live-
    confirmed across 12 real rooms. Reads/writes go through the room (not the
    climate device) -- a separate resource, so it must be explicitly polled.
    Bounds are conservative engineering defaults (not confirmed from the app's
    own UI limits).
    """

    _attr_translation_key = "temperature_drop_value"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_should_poll = True
    _attr_native_min_value = 0.5
    _attr_native_max_value = 5.0
    _attr_native_step = 0.5
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(self, device: SHCDevice, room: Any, entry_id: str) -> None:
        """Initialize the temperature-drop value number."""
        super().__init__(device, entry_id)
        self._room = room
        self._attr_unique_id = (
            f"{device.root_device_id}_{device.id}_temperature_drop_value"
        )
        self._value: float | None = None

    @property
    def device_name(self) -> str:
        """Name of the device (the room, matching ClimateControl's own device_info)."""
        return str(self._room.name)

    @property
    def native_value(self) -> float | None:
        """Return the configured temperature-drop value."""
        return self._value

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
        value = (data or {}).get("configuration", {}).get("dropTemperature")
        self._value = float(value) if value is not None else None

    async def async_set_native_value(self, value: float) -> None:
        """Set the temperature-drop value."""
        try:
            await self._room.async_set_temperature_drop_value(value)
        except SHCException as err:
            raise HomeAssistantError(
                f"Failed to set temperature drop for {self.device_name}: {err}",
                translation_domain=DOMAIN,
                translation_key="number_set_failed",
            ) from err
        self._value = value
