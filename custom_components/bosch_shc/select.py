"""Platform for select integration."""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any

from boschshcpy import (
    SHCMicromoduleDimmer,
    SHCMotionDetector2,
    SHCOutdoorSiren,
    SHCSession,
    SHCShutterContact2Plus,
)
from boschshcpy.device import SHCDevice
from boschshcpy.exceptions import SHCException
from boschshcpy.services_impl import (
    DimmerConfigurationService,
    DisplayDirection,
    DisplayedTemperatureConfiguration,
    OutdoorSirenService,
    PirSensorConfigurationService,
    PollControlService,
    PowerSwitchConfigurationService,
    SmartSensitivityControlService,
    SmokeSensitivityService,
    SwitchConfiguration,
    TerminalConfiguration,
    VibrationSensorService,
    WallThermostatConfiguration,
)
from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import SHCEntity, device_excluded

LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class SHCSelectEntityDescription[_DeviceT: SHCDevice](SelectEntityDescription):
    """Describes a SHC select entity.

    ``current_option_fn``/``select_option_fn`` capture the per-select-type
    variation (which device attribute/service to read, which enum to look
    values up in, which async setter to await) so a single generic entity
    class (`SHCSelect`) can drive every select type. ``current_option_fn``
    receives the entity's current ``options`` list so it can validate the
    read-back value the same way the original per-class implementations did
    (some filter unknown values against the options list, some don't —
    behavior is preserved exactly per type).

    Generic over the concrete device type _DeviceT (core-prep) so
    current_option_fn/select_option_fn can be typed against the actual
    boschshcpy model class a given select kind reads/writes, instead of the
    generic SHCDevice base.
    """

    unique_id_suffix: str
    current_option_fn: Callable[[_DeviceT, Sequence[str] | None], str | None]
    select_option_fn: Callable[[_DeviceT, str], Coroutine[Any, Any, None]]
    reload_after_select: bool = False


# Motion sensitivity: exclude UNKNOWN from user-visible options.
_MOTION_SENSITIVITY_OPTIONS = [
    PirSensorConfigurationService.MotionSensitivity.HIGH.name,
    PirSensorConfigurationService.MotionSensitivity.MIDDLE.name,
    PirSensorConfigurationService.MotionSensitivity.LOW.name,
]

# Vibration sensitivity: all values are valid user choices (no UNKNOWN).
_VIBRATION_SENSITIVITY_OPTIONS = [
    VibrationSensorService.SensitivityState.VERY_HIGH.name,
    VibrationSensorService.SensitivityState.HIGH.name,
    VibrationSensorService.SensitivityState.MEDIUM.name,
    VibrationSensorService.SensitivityState.LOW.name,
    VibrationSensorService.SensitivityState.VERY_LOW.name,
]

# Orientation-light response time (PollControl longPollInterval): LONG = lower
# battery use / slower, SHORT = more responsive / higher battery use. Exclude
# UNKNOWN from user-visible options.
_POLL_CONTROL_OPTIONS = [
    PollControlService.PollControlState.LONG.name,
    PollControlService.PollControlState.SHORT.name,
]

# State after power outage: OFF / ON / LAST_STATE (exclude UNKNOWN).
_STATE_AFTER_POWER_OUTAGE_OPTIONS = [
    PowerSwitchConfigurationService.StateAfterPowerOutage.OFF.name,
    PowerSwitchConfigurationService.StateAfterPowerOutage.ON.name,
    PowerSwitchConfigurationService.StateAfterPowerOutage.LAST_STATE.name,
]

# Smoke sensitivity: HIGH / MIDDLE / LOW (exclude UNKNOWN).
_SMOKE_SENSITIVITY_OPTIONS = [
    SmokeSensitivityService.SmokeSensitivityLevel.HIGH.name,
    SmokeSensitivityService.SmokeSensitivityLevel.MIDDLE.name,
    SmokeSensitivityService.SmokeSensitivityLevel.LOW.name,
]

# Display direction: NORMAL / REVERSED (exclude UNKNOWN).
_DISPLAY_DIRECTION_OPTIONS = [
    DisplayDirection.Direction.NORMAL.name,
    DisplayDirection.Direction.REVERSED.name,
]

# Displayed temperature: SETPOINT / MEASURED (exclude UNKNOWN).
_DISPLAYED_TEMPERATURE_OPTIONS = [
    DisplayedTemperatureConfiguration.DisplayedTemperature.SETPOINT.name,
    DisplayedTemperatureConfiguration.DisplayedTemperature.MEASURED.name,
]

# Terminal type: all user-selectable values (exclude UNKNOWN).
_TERMINAL_TYPE_OPTIONS = [
    TerminalConfiguration.Type.NOT_CONNECTED.name,
    TerminalConfiguration.Type.FLOOR_SENSOR_CONNECTED.name,
    TerminalConfiguration.Type.FLOOR_SENSOR_USED_FOR_REGULATION.name,
    TerminalConfiguration.Type.FLOOR_SENSOR_DISPLAYED.name,
    TerminalConfiguration.Type.FLOOR_SENSOR_DISPLAYED_AND_USED_FOR_REGULATION.name,
    TerminalConfiguration.Type.VOLT_FREE_SENSOR_CONNECTED.name,
    TerminalConfiguration.Type.VOLT_FREE_SENSOR_CONNECTED_AND_USED_FOR_OPERATION.name,
    TerminalConfiguration.Type.OUTDOOR_SENSOR_CONNECTED.name,
]

# WallThermostatConfiguration valve type: exclude UNKNOWN.
_VALVE_TYPE_OPTIONS = [
    WallThermostatConfiguration.ValveType.NORMALLY_CLOSE.name,
    WallThermostatConfiguration.ValveType.NORMALLY_OPEN.name,
]

# WallThermostatConfiguration heater type: exclude UNKNOWN.
_HEATER_TYPE_OPTIONS = [
    WallThermostatConfiguration.HeaterType.FLOOR_HEATING.name,
    WallThermostatConfiguration.HeaterType.FLOOR_HEATING_LOW_ENERGY.name,
    WallThermostatConfiguration.HeaterType.RADIATOR.name,
    WallThermostatConfiguration.HeaterType.CONVECTOR_PASSIVE.name,
    WallThermostatConfiguration.HeaterType.CONVECTOR_ACTIVE.name,
    WallThermostatConfiguration.HeaterType.VOLT_FREE_HEATING.name,
]

# SwitchConfiguration switch type: exclude UNKNOWN.
_SWITCH_TYPE_OPTIONS = [
    SwitchConfiguration.SwitchType.NONE.name,
    SwitchConfiguration.SwitchType.PUSHBUTTON.name,
    SwitchConfiguration.SwitchType.SWITCH.name,
    SwitchConfiguration.SwitchType.NO_SWITCH.name,
]

# SwitchConfiguration actuator type: exclude UNKNOWN.
_ACTUATOR_TYPE_OPTIONS = [
    SwitchConfiguration.ActuatorType.NORMALLY_CLOSED.name,
    SwitchConfiguration.ActuatorType.NORMALLY_OPEN.name,
    SwitchConfiguration.ActuatorType.UNSUPPORTED.name,
]

# SwitchConfiguration output mode: exclude UNKNOWN.
_OUTPUT_MODE_OPTIONS = [
    SwitchConfiguration.OutputMode.ATTACHED.name,
    SwitchConfiguration.OutputMode.DETACHED.name,
    SwitchConfiguration.OutputMode.DETACHED_SHORT_PRESS.name,
    SwitchConfiguration.OutputMode.DETACHED_LONG_PRESS.name,
    SwitchConfiguration.OutputMode.UNSUPPORTED.name,
]

# SmartSensitivity manual level: HIGH / MIDDLE / LOW (exclude UNKNOWN).
_SMART_SENSITIVITY_OPTIONS = [
    SmartSensitivityControlService.MotionSensitivity.HIGH.name,
    SmartSensitivityControlService.MotionSensitivity.MIDDLE.name,
    SmartSensitivityControlService.MotionSensitivity.LOW.name,
]

_SIREN_SOUND_LEVEL_OPTIONS = ["low", "medium", "high"]

_DIMMER_PHASE_CONTROL_OPTIONS = ["TRAILING", "LEADING"]


# ---------------------------------------------------------------------------
# Reusable description-field factories.
#
# Most select types share one shape: read an enum-valued device attribute,
# return its (validated) `.name`, and write it back via `async_set_<x>` after
# looking the option string up in the matching enum. The few types that
# genuinely deviate (siren/dimmer's KeyError-swallowing lookup, the
# SmartSensitivity context-keyed dict read, the dynamically-scoped
# InstallationProfile options) get their own dedicated functions below
# instead of being forced through these factories.
# ---------------------------------------------------------------------------


def _enum_attr_current_option_fn(
    attr: str, warn_label: str
) -> Callable[[SHCDevice, Sequence[str] | None], str | None]:
    """Build a current_option_fn reading `device.<attr>.name`, options-checked."""

    def _current_option(device: SHCDevice, options: Sequence[str] | None) -> str | None:
        try:
            val = getattr(device, attr)
            if val is None:
                return None
            name = str(val.name)
            if options is not None and name not in options:
                return None
            return name
        except (AttributeError, ValueError) as err:
            LOGGER.warning("Unknown %s for %s: %s", warn_label, device.name, err)
            return None

    return _current_option


def _enum_attr_select_option_fn(
    setter: str, enum_cls: type[Enum]
) -> Callable[[SHCDevice, str], Coroutine[Any, Any, None]]:
    """Build a select_option_fn calling `device.<setter>(enum_cls[option])`."""

    async def _select_option(device: SHCDevice, option: str) -> None:
        await getattr(device, setter)(enum_cls[option])

    return _select_option


def _siren_current_option(
    device: SHCOutdoorSiren, options: Sequence[str] | None
) -> str | None:
    """Read the Outdoor Siren's current sound level (already lowercased)."""
    try:
        return str(device.siren.sound_level.name.lower())
    except (AttributeError, ValueError):
        return None


async def _siren_select_option(device: SHCOutdoorSiren, option: str) -> None:
    """Write the Outdoor Siren's sound level."""
    try:
        level = OutdoorSirenService.SoundLevel[option.upper()]
    except KeyError:
        return
    await device.siren.async_set_configuration(sound_level=level)


def _dimmer_current_option(
    device: SHCMicromoduleDimmer, options: Sequence[str] | None
) -> str | None:
    """Read the micromodule dimmer's edge phase-control mode."""
    service = device.dimmer_configuration
    if service is None:
        return None
    try:
        name = service.edge_phase_control_mode.name
        return name if options is None or name in options else None
    except (AttributeError, ValueError):
        return None


async def _dimmer_select_option(device: SHCMicromoduleDimmer, option: str) -> None:
    """Write the micromodule dimmer's edge phase-control mode."""
    service = device.dimmer_configuration
    if service is None:
        return
    try:
        mode = DimmerConfigurationService.EdgePhaseControlMode[option]
    except KeyError:
        return
    await service.async_set_edge_phase_control_mode(mode)


def _smart_sensitivity_current_option_fn(
    context: Any,
) -> Callable[[SHCMotionDetector2, Sequence[str] | None], str | None]:
    """Build a current_option_fn for one SmartSensitivityControl context."""

    def _current_option(
        device: SHCMotionDetector2, options: Sequence[str] | None
    ) -> str | None:
        sensitivity = device.get_smart_sensitivity(context)
        if sensitivity is None:
            return None
        level = sensitivity.get("manualLevel")
        if level is None:
            return None
        # level may be an enum or a string
        name = level.name if hasattr(level, "name") else str(level)
        if options is not None and name not in options:
            return None
        return name

    return _current_option


def _smart_sensitivity_select_option_fn(
    context: Any,
) -> Callable[[SHCMotionDetector2, str], Coroutine[Any, Any, None]]:
    """Build a select_option_fn for one SmartSensitivityControl context."""

    async def _select_option(device: SHCMotionDetector2, option: str) -> None:
        level = SmartSensitivityControlService.MotionSensitivity[option]
        await device.async_set_smart_sensitivity_manual_level(context, level)

    return _select_option


def _installation_profile_current_option(
    device: SHCDevice, options: Sequence[str] | None
) -> str | None:
    """Return the current installation profile (lowercased).

    Guarded: a profile not in the advertised options (e.g. after a firmware
    vocabulary change) returns None instead of an invalid option.
    """
    val = getattr(device, "profile", None)
    if val is None:
        return None
    val_lower = str(val).lower()
    if val_lower not in (options or []):
        return None
    return val_lower


async def _installation_profile_select_option(device: SHCDevice, option: str) -> None:
    """Write the installation profile (uppercased back to the API value)."""
    await device.async_set_profile(option.upper())


SIREN_SOUND_LEVEL = "siren_sound_level"
MOTION_SENSITIVITY = "motion_sensitivity"
ORIENTATION_LIGHT_RESPONSE = "orientation_light_response_time"
VIBRATION_SENSITIVITY = "vibration_sensitivity"
STATE_AFTER_POWER_OUTAGE = "state_after_power_outage"
SMOKE_SENSITIVITY = "smoke_sensitivity"
DISPLAY_DIRECTION = "display_direction"
DISPLAYED_TEMPERATURE = "displayed_temperature"
TERMINAL_TYPE = "terminal_type"
VALVE_TYPE = "valve_type"
HEATER_TYPE = "heater_type"
SWITCH_TYPE = "switch_type"
ACTUATOR_TYPE = "actuator_type"
OUTPUT_MODE = "output_mode"
SMART_SENSITIVITY_SECURITY_LEVEL = "smart_sensitivity_security_level"
SMART_SENSITIVITY_COMFORT_LEVEL = "smart_sensitivity_comfort_level"
DIMMER_PHASE_CONTROL = "dimmer_phase_control"
INSTALLATION_PROFILE = "installation_profile"


SELECT_DESCRIPTIONS: dict[str, SHCSelectEntityDescription[Any]] = {
    SIREN_SOUND_LEVEL: SHCSelectEntityDescription[SHCOutdoorSiren](
        key=SIREN_SOUND_LEVEL,
        translation_key=SIREN_SOUND_LEVEL,
        unique_id_suffix="sound_level",
        current_option_fn=_siren_current_option,
        select_option_fn=_siren_select_option,
    ),
    MOTION_SENSITIVITY: SHCSelectEntityDescription(
        key=MOTION_SENSITIVITY,
        translation_key=MOTION_SENSITIVITY,
        unique_id_suffix=MOTION_SENSITIVITY,
        current_option_fn=_enum_attr_current_option_fn(
            "motion_sensitivity", "motion_sensitivity"
        ),
        select_option_fn=_enum_attr_select_option_fn(
            "async_set_motion_sensitivity",
            PirSensorConfigurationService.MotionSensitivity,
        ),
    ),
    ORIENTATION_LIGHT_RESPONSE: SHCSelectEntityDescription(
        key=ORIENTATION_LIGHT_RESPONSE,
        translation_key=ORIENTATION_LIGHT_RESPONSE,
        unique_id_suffix="orientation_light_response",
        current_option_fn=_enum_attr_current_option_fn(
            "long_poll_interval", "long_poll_interval"
        ),
        select_option_fn=_enum_attr_select_option_fn(
            "async_set_long_poll_interval", PollControlService.PollControlState
        ),
    ),
    VIBRATION_SENSITIVITY: SHCSelectEntityDescription(
        key=VIBRATION_SENSITIVITY,
        translation_key=VIBRATION_SENSITIVITY,
        unique_id_suffix=VIBRATION_SENSITIVITY,
        current_option_fn=_enum_attr_current_option_fn(
            "sensitivity", "vibration sensitivity"
        ),
        select_option_fn=_enum_attr_select_option_fn(
            "async_set_sensitivity", VibrationSensorService.SensitivityState
        ),
    ),
    STATE_AFTER_POWER_OUTAGE: SHCSelectEntityDescription(
        key=STATE_AFTER_POWER_OUTAGE,
        translation_key=STATE_AFTER_POWER_OUTAGE,
        unique_id_suffix=STATE_AFTER_POWER_OUTAGE,
        current_option_fn=_enum_attr_current_option_fn(
            "state_after_power_outage", "state_after_power_outage"
        ),
        select_option_fn=_enum_attr_select_option_fn(
            "async_set_state_after_power_outage",
            PowerSwitchConfigurationService.StateAfterPowerOutage,
        ),
    ),
    SMOKE_SENSITIVITY: SHCSelectEntityDescription(
        key=SMOKE_SENSITIVITY,
        translation_key=SMOKE_SENSITIVITY,
        unique_id_suffix=SMOKE_SENSITIVITY,
        current_option_fn=_enum_attr_current_option_fn(
            "smoke_sensitivity", "smoke_sensitivity"
        ),
        select_option_fn=_enum_attr_select_option_fn(
            "async_set_smoke_sensitivity",
            SmokeSensitivityService.SmokeSensitivityLevel,
        ),
    ),
    DISPLAY_DIRECTION: SHCSelectEntityDescription(
        key=DISPLAY_DIRECTION,
        translation_key=DISPLAY_DIRECTION,
        unique_id_suffix=DISPLAY_DIRECTION,
        current_option_fn=_enum_attr_current_option_fn(
            "display_direction", "display_direction"
        ),
        select_option_fn=_enum_attr_select_option_fn(
            "async_set_display_direction", DisplayDirection.Direction
        ),
    ),
    DISPLAYED_TEMPERATURE: SHCSelectEntityDescription(
        key=DISPLAYED_TEMPERATURE,
        translation_key=DISPLAYED_TEMPERATURE,
        unique_id_suffix=DISPLAYED_TEMPERATURE,
        current_option_fn=_enum_attr_current_option_fn(
            "displayed_temperature", "displayed_temperature"
        ),
        select_option_fn=_enum_attr_select_option_fn(
            "async_set_displayed_temperature",
            DisplayedTemperatureConfiguration.DisplayedTemperature,
        ),
    ),
    TERMINAL_TYPE: SHCSelectEntityDescription(
        key=TERMINAL_TYPE,
        translation_key=TERMINAL_TYPE,
        unique_id_suffix=TERMINAL_TYPE,
        current_option_fn=_enum_attr_current_option_fn(
            "terminal_type", "terminal_type"
        ),
        select_option_fn=_enum_attr_select_option_fn(
            "async_set_terminal_type", TerminalConfiguration.Type
        ),
    ),
    VALVE_TYPE: SHCSelectEntityDescription(
        key=VALVE_TYPE,
        translation_key=VALVE_TYPE,
        unique_id_suffix=VALVE_TYPE,
        current_option_fn=_enum_attr_current_option_fn("valve_type", "valve_type"),
        select_option_fn=_enum_attr_select_option_fn(
            "async_set_valve_type", WallThermostatConfiguration.ValveType
        ),
    ),
    HEATER_TYPE: SHCSelectEntityDescription(
        key=HEATER_TYPE,
        translation_key=HEATER_TYPE,
        unique_id_suffix=HEATER_TYPE,
        current_option_fn=_enum_attr_current_option_fn("heater_type", "heater_type"),
        select_option_fn=_enum_attr_select_option_fn(
            "async_set_heater_type", WallThermostatConfiguration.HeaterType
        ),
    ),
    SWITCH_TYPE: SHCSelectEntityDescription(
        key=SWITCH_TYPE,
        translation_key=SWITCH_TYPE,
        unique_id_suffix=SWITCH_TYPE,
        current_option_fn=_enum_attr_current_option_fn("switch_type", "switch_type"),
        select_option_fn=_enum_attr_select_option_fn(
            "async_set_switch_type", SwitchConfiguration.SwitchType
        ),
    ),
    ACTUATOR_TYPE: SHCSelectEntityDescription(
        key=ACTUATOR_TYPE,
        translation_key=ACTUATOR_TYPE,
        unique_id_suffix=ACTUATOR_TYPE,
        current_option_fn=_enum_attr_current_option_fn(
            "actuator_type", "actuator_type"
        ),
        select_option_fn=_enum_attr_select_option_fn(
            "async_set_actuator_type", SwitchConfiguration.ActuatorType
        ),
    ),
    OUTPUT_MODE: SHCSelectEntityDescription(
        key=OUTPUT_MODE,
        translation_key=OUTPUT_MODE,
        unique_id_suffix=OUTPUT_MODE,
        current_option_fn=_enum_attr_current_option_fn("output_mode", "output_mode"),
        select_option_fn=_enum_attr_select_option_fn(
            "async_set_output_mode", SwitchConfiguration.OutputMode
        ),
    ),
    SMART_SENSITIVITY_SECURITY_LEVEL: SHCSelectEntityDescription[SHCMotionDetector2](
        key=SMART_SENSITIVITY_SECURITY_LEVEL,
        translation_key=SMART_SENSITIVITY_SECURITY_LEVEL,
        unique_id_suffix="smart_sensitivity_security",
        current_option_fn=_smart_sensitivity_current_option_fn(
            SmartSensitivityControlService.SmartSensitivityContext.SECURITY
        ),
        select_option_fn=_smart_sensitivity_select_option_fn(
            SmartSensitivityControlService.SmartSensitivityContext.SECURITY
        ),
    ),
    SMART_SENSITIVITY_COMFORT_LEVEL: SHCSelectEntityDescription[SHCMotionDetector2](
        key=SMART_SENSITIVITY_COMFORT_LEVEL,
        translation_key=SMART_SENSITIVITY_COMFORT_LEVEL,
        unique_id_suffix="smart_sensitivity_comfort",
        current_option_fn=_smart_sensitivity_current_option_fn(
            SmartSensitivityControlService.SmartSensitivityContext.COMFORT
        ),
        select_option_fn=_smart_sensitivity_select_option_fn(
            SmartSensitivityControlService.SmartSensitivityContext.COMFORT
        ),
    ),
    DIMMER_PHASE_CONTROL: SHCSelectEntityDescription[SHCMicromoduleDimmer](
        key=DIMMER_PHASE_CONTROL,
        translation_key=DIMMER_PHASE_CONTROL,
        unique_id_suffix=DIMMER_PHASE_CONTROL,
        current_option_fn=_dimmer_current_option,
        select_option_fn=_dimmer_select_option,
    ),
    INSTALLATION_PROFILE: SHCSelectEntityDescription(
        key=INSTALLATION_PROFILE,
        translation_key=INSTALLATION_PROFILE,
        unique_id_suffix=INSTALLATION_PROFILE,
        current_option_fn=_installation_profile_current_option,
        select_option_fn=_installation_profile_select_option,
        reload_after_select=True,
    ),
}


async def async_setup_entry(  # noqa: C901
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the SHC select platform."""
    entities: list[SelectEntity] = []
    session: SHCSession = config_entry.runtime_data.session

    for device in session.device_helper.motion_detectors2:
        if device_excluded(device, config_entry.options):
            continue
        if not hasattr(device, "motion_sensitivity"):
            continue
        try:
            # Probe the accessor — raises AttributeError when the service is absent.
            _ = device.motion_sensitivity
        except AttributeError:
            continue
        entities.append(
            MotionSensitivitySelect(
                device=device,
                entry_id=config_entry.entry_id,
            )
        )

    for device in session.device_helper.shutter_contacts2:  # type: ignore[assignment]
        if device_excluded(device, config_entry.options):
            continue
        if not isinstance(device, SHCShutterContact2Plus):
            continue
        entities.append(
            VibrationSensitivitySelect(
                device=device,
                entry_id=config_entry.entry_id,
            )
        )

    # PowerSwitchConfiguration: state after power outage (smart plugs).
    for device in getattr(session.device_helper, "smart_plugs", []) + getattr(
        session.device_helper, "smart_plugs_compact", []
    ):
        if device_excluded(device, config_entry.options):
            continue
        if not getattr(device, "supports_power_switch_configuration", False):
            continue
        if getattr(device, "state_after_power_outage", None) is None:
            continue
        entities.append(
            StateAfterPowerOutageSelect(
                device=device,
                entry_id=config_entry.entry_id,
            )
        )

    # SmokeSensitivity: level select for smoke detectors and twinguards.
    for device in getattr(session.device_helper, "smoke_detectors", []) + getattr(
        session.device_helper, "twinguards", []
    ):
        if device_excluded(device, config_entry.options):
            continue
        if not getattr(device, "supports_smoke_sensitivity", False):
            continue
        if getattr(device, "smoke_sensitivity", None) is None:
            continue
        entities.append(
            SmokeSensitivitySelect(
                device=device,
                entry_id=config_entry.entry_id,
            )
        )

    # DisplayDirection select (ThermostatGen2 / RoomThermostat2).
    for device in getattr(session.device_helper, "thermostats", []) + getattr(
        session.device_helper, "roomthermostats", []
    ):
        if device_excluded(device, config_entry.options):
            continue
        if (
            getattr(device, "supports_display_direction", False)
            and getattr(device, "display_direction", None) is not None
        ):
            entities.append(
                DisplayDirectionSelect(
                    device=device,
                    entry_id=config_entry.entry_id,
                )
            )
        if (
            getattr(device, "supports_displayed_temperature", False)
            and getattr(device, "displayed_temperature", None) is not None
        ):
            entities.append(
                DisplayedTemperatureSelect(
                    device=device,
                    entry_id=config_entry.entry_id,
                )
            )
        # WallThermostatConfiguration: valve + heater type (ThermostatGen2 only).
        if (
            getattr(device, "supports_wall_thermostat_configuration", False)
            and getattr(device, "valve_type", None) is not None
        ):
            entities.append(
                ValveTypeSelect(
                    device=device,
                    entry_id=config_entry.entry_id,
                )
            )
        if (
            getattr(device, "supports_wall_thermostat_configuration", False)
            and getattr(device, "heater_type", None) is not None
        ):
            entities.append(
                HeaterTypeSelect(
                    device=device,
                    entry_id=config_entry.entry_id,
                )
            )
        # TerminalConfiguration type (RoomThermostat2 only).
        if (
            getattr(device, "supports_terminal_configuration", False)
            and getattr(device, "terminal_type", None) is not None
        ):
            entities.append(
                TerminalTypeSelect(
                    device=device,
                    entry_id=config_entry.entry_id,
                )
            )

    # SwitchConfiguration selects (MicromoduleRelay + LightControl); gated
    # per-select below on switch_type/actuator_type/output_mode (null-safe).
    for device in getattr(session.device_helper, "micromodule_relays", []) + getattr(
        session.device_helper, "micromodule_light_controls", []
    ):
        if device_excluded(device, config_entry.options):
            continue
        if getattr(device, "switch_type", None) is not None:
            entities.append(
                SwitchTypeSelect(
                    device=device,
                    entry_id=config_entry.entry_id,
                )
            )
        if getattr(device, "actuator_type", None) is not None:
            entities.append(
                ActuatorTypeSelect(
                    device=device,
                    entry_id=config_entry.entry_id,
                )
            )
        if getattr(device, "output_mode", None) is not None:
            entities.append(
                OutputModeSelect(
                    device=device,
                    entry_id=config_entry.entry_id,
                )
            )

    # SmartSensitivityControl manual level selects (Motion Detector II).
    # Two entities: one for SECURITY context, one for COMFORT context.
    for device in getattr(session.device_helper, "motion_detectors2", []):
        if device_excluded(device, config_entry.options):
            continue
        if not getattr(device, "supports_smart_sensitivity", False):
            continue
        if getattr(device, "get_smart_sensitivity", None) is None:
            continue
        entities.append(
            SmartSensitivitySecurityLevelSelect(
                device=device,
                entry_id=config_entry.entry_id,
            )
        )
        entities.append(
            SmartSensitivityComfortLevelSelect(
                device=device,
                entry_id=config_entry.entry_id,
            )
        )

    # Orientation-light response time (PollControl) for Motion Detector II.
    for device in getattr(session.device_helper, "motion_detectors2", []):
        if device_excluded(device, config_entry.options):
            continue
        if getattr(device, "long_poll_interval", None) is None:
            continue
        entities.append(
            OrientationLightResponseSelect(
                device=device,
                entry_id=config_entry.entry_id,
            )
        )

    # Installation profile (e.g. GENERIC / OUTDOOR / LIGHT / HEATING_RCC /
    # BOILER / MINI_PV) — writable device-level "purpose of use" field.
    # Not MD2-specific: micromodule relays and smart plugs also advertise a
    # non-empty supportedProfiles list on real hardware (see
    # knowledge-base/rawscan-database.md), so the same generic select is
    # wired up for all of them, guarded by the device's own advertised
    # supported_profiles. Writable replacement for the former read-only
    # InstallationProfileSensor (MD2 only).
    for device in (
        list(getattr(session.device_helper, "motion_detectors2", []))
        + list(getattr(session.device_helper, "micromodule_relays", []))
        + list(getattr(session.device_helper, "smart_plugs", []))
        + list(getattr(session.device_helper, "smart_plugs_compact", []))
    ):
        if device_excluded(device, config_entry.options):
            continue
        if not getattr(device, "supported_profiles", None):
            continue
        entities.append(
            InstallationProfileSelect(
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
            SirenSoundLevelSelect(device=siren, entry_id=config_entry.entry_id)
        )

    # DimmerConfiguration phase-control mode (micromodule dimmer, #123).
    for device in getattr(session.device_helper, "micromodule_dimmers", []):
        if device_excluded(device, config_entry.options):
            continue
        if getattr(device, "supports_dimmer_configuration", False):
            entities.append(
                DimmerPhaseControlSelect(device=device, entry_id=config_entry.entry_id)
            )

    if entities:
        async_add_entities(entities)


class SHCSelect[_DeviceT: SHCDevice](SHCEntity, SelectEntity):  # type: ignore[misc]
    """Generic SHC select entity, driven by a SHCSelectEntityDescription.

    `current_option`/`async_select_option` delegate to the description's
    `current_option_fn`/`select_option_fn`, so a single class covers every
    select type — the per-type behavior (which attribute to read, which enum
    to look values up in, which async setter to call) lives in the
    description, not in a dedicated subclass.
    """

    entity_description: SHCSelectEntityDescription[_DeviceT]

    def __init__(self, device: _DeviceT, entry_id: str) -> None:
        """Initialize the select entity."""
        super().__init__(device, entry_id)
        self._device: _DeviceT = device
        self._attr_unique_id = (
            f"{device.root_device_id}_{device.id}_"
            f"{self.entity_description.unique_id_suffix}"
        )

    @property
    def current_option(self) -> str | None:
        """Return the current option."""
        return self.entity_description.current_option_fn(
            self._device, self._attr_options
        )

    async def async_select_option(self, option: str) -> None:
        """Select an option, writing it to the device."""
        try:
            await self.entity_description.select_option_fn(self._device, option)
        except SHCException as err:
            raise HomeAssistantError(
                f"Failed to set {self._device.name} to {option}: {err}",
                translation_domain=DOMAIN,
                translation_key="select_option_failed",
            ) from err
        if self.entity_description.reload_after_select:
            # #356: switching e.g. the profile can add/remove capability-gated
            # entities (the Motion Detector II [+M] indicator light) — reload
            # so the entity list reflects the change immediately, instead of
            # only after the user manually reloads the integration/restarts.
            self.hass.async_create_task(
                self.hass.config_entries.async_reload(self._entry_id)
            )


class SirenSoundLevelSelect(SHCSelect):
    """Select entity for the Outdoor Siren sound level (#120)."""

    entity_description = SELECT_DESCRIPTIONS[SIREN_SOUND_LEVEL]
    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "siren_sound_level"
    _attr_options = _SIREN_SOUND_LEVEL_OPTIONS


class MotionSensitivitySelect(SHCSelect):
    """Select entity for Motion Detector II [+M] motion sensitivity."""

    entity_description = SELECT_DESCRIPTIONS[MOTION_SENSITIVITY]
    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "motion_sensitivity"
    _attr_options = _MOTION_SENSITIVITY_OPTIONS


class OrientationLightResponseSelect(SHCSelect):
    """Select for the Motion Detector II orientation-light response time.

    Backed by the PollControl service (longPollInterval): LONG = lower battery
    consumption / slower response, SHORT = faster response / higher battery use.
    """

    entity_description = SELECT_DESCRIPTIONS[ORIENTATION_LIGHT_RESPONSE]
    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "orientation_light_response_time"
    _attr_options = _POLL_CONTROL_OPTIONS


class VibrationSensitivitySelect(SHCSelect):
    """Select entity for ShutterContact2Plus vibration sensitivity."""

    entity_description = SELECT_DESCRIPTIONS[VIBRATION_SENSITIVITY]
    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "vibration_sensitivity"
    _attr_options = _VIBRATION_SENSITIVITY_OPTIONS


class StateAfterPowerOutageSelect(SHCSelect):
    """Select entity for smart plug power-loss behaviour."""

    entity_description = SELECT_DESCRIPTIONS[STATE_AFTER_POWER_OUTAGE]
    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "state_after_power_outage"
    _attr_options = _STATE_AFTER_POWER_OUTAGE_OPTIONS


class SmokeSensitivitySelect(SHCSelect):
    """Select entity for smoke detector / twinguard smoke sensitivity."""

    entity_description = SELECT_DESCRIPTIONS[SMOKE_SENSITIVITY]
    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "smoke_sensitivity"
    _attr_options = _SMOKE_SENSITIVITY_OPTIONS


class DisplayDirectionSelect(SHCSelect):
    """Select entity for thermostat display orientation."""

    entity_description = SELECT_DESCRIPTIONS[DISPLAY_DIRECTION]
    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "display_direction"
    _attr_options = _DISPLAY_DIRECTION_OPTIONS


class DisplayedTemperatureSelect(SHCSelect):
    """Select entity for which temperature value the thermostat display shows."""

    entity_description = SELECT_DESCRIPTIONS[DISPLAYED_TEMPERATURE]
    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "displayed_temperature"
    _attr_options = _DISPLAYED_TEMPERATURE_OPTIONS


class TerminalTypeSelect(SHCSelect):
    """Select entity for RoomThermostat2 terminal (external sensor) type."""

    entity_description = SELECT_DESCRIPTIONS[TERMINAL_TYPE]
    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "terminal_type"
    _attr_options = _TERMINAL_TYPE_OPTIONS


class ValveTypeSelect(SHCSelect):
    """Select entity for ThermostatGen2 valve type (normally open/close)."""

    entity_description = SELECT_DESCRIPTIONS[VALVE_TYPE]
    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "valve_type"
    _attr_options = _VALVE_TYPE_OPTIONS


class HeaterTypeSelect(SHCSelect):
    """Select entity for ThermostatGen2 heater type."""

    entity_description = SELECT_DESCRIPTIONS[HEATER_TYPE]
    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "heater_type"
    _attr_options = _HEATER_TYPE_OPTIONS


class SwitchTypeSelect(SHCSelect):
    """Select entity for SwitchConfiguration switch type."""

    entity_description = SELECT_DESCRIPTIONS[SWITCH_TYPE]
    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "switch_type"
    _attr_options = _SWITCH_TYPE_OPTIONS


class ActuatorTypeSelect(SHCSelect):
    """Select entity for SwitchConfiguration actuator type."""

    entity_description = SELECT_DESCRIPTIONS[ACTUATOR_TYPE]
    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "actuator_type"
    _attr_options = _ACTUATOR_TYPE_OPTIONS


class OutputModeSelect(SHCSelect):
    """Select entity for SwitchConfiguration output mode."""

    entity_description = SELECT_DESCRIPTIONS[OUTPUT_MODE]
    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "output_mode"
    _attr_options = _OUTPUT_MODE_OPTIONS


class SmartSensitivitySecurityLevelSelect(SHCSelect):
    """Select entity for SmartSensitivityControl manual level — SECURITY context.

    The MD2 SmartSensitivityControl service stores a per-context manualLevel
    as a MotionSensitivity enum (HIGH/MIDDLE/LOW).  Only created when
    get_smart_sensitivity is available on the device.
    """

    entity_description = SELECT_DESCRIPTIONS[SMART_SENSITIVITY_SECURITY_LEVEL]
    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "smart_sensitivity_security_level"
    _attr_options = _SMART_SENSITIVITY_OPTIONS


class SmartSensitivityComfortLevelSelect(SHCSelect):
    """Select entity for SmartSensitivityControl manual level — COMFORT context."""

    entity_description = SELECT_DESCRIPTIONS[SMART_SENSITIVITY_COMFORT_LEVEL]
    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "smart_sensitivity_comfort_level"
    _attr_options = _SMART_SENSITIVITY_OPTIONS


class DimmerPhaseControlSelect(SHCSelect):
    """Select entity for micromodule dimmer phase-control mode (#123)."""

    entity_description = SELECT_DESCRIPTIONS[DIMMER_PHASE_CONTROL]
    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "dimmer_phase_control"
    _attr_options = _DIMMER_PHASE_CONTROL_OPTIONS


class InstallationProfileSelect(SHCSelect):
    """Writable select for the device installation profile (#353).

    Replaces the former read-only InstallationProfileSensor. Options are taken
    from the device's advertised ``supportedProfiles`` (e.g. GENERIC / OUTDOOR
    for the Motion Detector II [+M]); selecting one writes the device-level
    ``profile`` field. Option values are lowercased to match the translation
    state keys, mirroring the previous sensor's ENUM convention.
    """

    entity_description = SELECT_DESCRIPTIONS[INSTALLATION_PROFILE]
    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "installation_profile"

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize the installation-profile select."""
        super().__init__(device, entry_id)
        self._attr_options = [
            p.lower() for p in (getattr(device, "supported_profiles", []) or [])
        ]
