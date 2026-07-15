"""Platform for sensor integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

from boschshcpy import (
    SHCBatteryDevice,
    SHCClimateControl,
    SHCEmma,
    SHCLightControl,
    SHCLightSwitchBSM,
    SHCMicromoduleShutterControl,
    SHCMotionDetector,
    SHCMotionDetector2,
    SHCOutdoorSiren,
    SHCPresenceSimulationSystem,
    SHCSession,
    SHCShutterContact2,
    SHCShutterControl,
    SHCSmartPlug,
    SHCSmartPlugCompact,
    SHCThermostat,
    SHCTwinguard,
    SHCUniversalSwitch,
    SHCWallThermostat,
)
from boschshcpy.device import SHCDevice
from boschshcpy.exceptions import SHCException
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    LIGHT_LUX,
    Platform,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfTime,
)

try:
    from homeassistant.const import UnitOfRatio
except ImportError:
    from homeassistant.const import (
        CONCENTRATION_PARTS_PER_MILLION as _ppm,
    )
    from homeassistant.const import (
        PERCENTAGE as _pct,
    )

    class UnitOfRatio:  # type: ignore[no-redef]
        """Shim for HA < 2024.x test environments."""

        PERCENTAGE = _pct
        PARTS_PER_MILLION = _ppm


from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntry, DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    LOGGER,
    OPT_DIAGNOSTIC_ENTITIES,
    OPT_SUPPRESS_POWER_SENSORS,
)
from .coordinator import SHCZigbeeRoutingCoordinator
from .entity import SHCEntity, async_migrate_to_new_unique_id, device_excluded

PARALLEL_UPDATES = 1


async def async_setup_entry(  # noqa: C901
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the SHC sensor platform."""
    entities: list[SensorEntity] = []
    session: SHCSession = config_entry.runtime_data.session
    sensor: SHCDevice
    diagnostic_enabled = config_entry.options.get(OPT_DIAGNOSTIC_ENTITIES, True)
    power_sensors_enabled = not config_entry.options.get(
        OPT_SUPPRESS_POWER_SENSORS, False
    )

    # Presence simulation running-window (hass#120 audit): fully modeled in
    # boschshcpy but never wired into an HA entity. Both are DIAGNOSTIC
    # category, so gate on diagnostic_enabled like every other diagnostic
    # sensor in this file.
    if diagnostic_enabled:
        presence_simulation_system = getattr(
            session.device_helper, "presence_simulation_system", None
        )
        if presence_simulation_system and not device_excluded(
            presence_simulation_system, config_entry.options
        ):
            entities.append(
                PresenceSimulationRunningStartSensor(
                    device=presence_simulation_system, entry_id=config_entry.entry_id
                )
            )
            entities.append(
                PresenceSimulationRunningEndSensor(
                    device=presence_simulation_system, entry_id=config_entry.entry_id
                )
            )

    if diagnostic_enabled:
        for climate in getattr(session.device_helper, "climate_controls", []):
            if device_excluded(climate, config_entry.options):
                continue
            try:
                room_name = session.room(climate.room_id).name
            except (KeyError, AttributeError):
                room_name = None
            entities.append(
                NextSetpointTemperatureSensor(
                    device=climate,
                    entry_id=config_entry.entry_id,
                    room_name=room_name,
                )
            )

    for sensor in session.device_helper.thermostats:
        if device_excluded(sensor, config_entry.options):
            continue
        await async_migrate_to_new_unique_id(
            hass, Platform.SENSOR, device=sensor, attr_name="Temperature"
        )
        entities.append(
            TemperatureSensor(
                device=sensor,
                entry_id=config_entry.entry_id,
            )
        )
        if diagnostic_enabled:
            await async_migrate_to_new_unique_id(
                hass, Platform.SENSOR, device=sensor, attr_name="Valvetappet"
            )
            entities.append(
                ValveTappetSensor(
                    device=sensor,
                    entry_id=config_entry.entry_id,
                )
            )

    for sensor in list(session.device_helper.wallthermostats) + list(
        session.device_helper.roomthermostats
    ):
        if device_excluded(sensor, config_entry.options):
            continue
        await async_migrate_to_new_unique_id(
            hass, Platform.SENSOR, device=sensor, attr_name="Temperature"
        )
        entities.append(
            TemperatureSensor(
                device=sensor,
                entry_id=config_entry.entry_id,
            )
        )
        await async_migrate_to_new_unique_id(
            hass, Platform.SENSOR, device=sensor, attr_name="Humidity"
        )
        if getattr(sensor, "supports_humidity", True):
            entities.append(
                HumiditySensor(
                    device=sensor,
                    entry_id=config_entry.entry_id,
                )
            )
        # #198 / #330: Room Thermostat II 230V with an external floor sensor
        # wired to its terminal exposes a second temperature via
        # TerminalConfiguration — surface it as a dedicated sensor. Only when a
        # sensor is actually connected (terminal_temperature is not None).
        if getattr(sensor, "terminal_temperature", None) is not None:
            entities.append(
                TerminalTemperatureSensor(
                    device=sensor,
                    entry_id=config_entry.entry_id,
                )
            )

    for sensor in session.device_helper.twinguards:
        if device_excluded(sensor, config_entry.options):
            continue
        await async_migrate_to_new_unique_id(
            hass, Platform.SENSOR, device=sensor, attr_name="Temperature"
        )
        entities.append(
            TemperatureSensor(
                device=sensor,
                entry_id=config_entry.entry_id,
            )
        )
        await async_migrate_to_new_unique_id(
            hass, Platform.SENSOR, device=sensor, attr_name="Humidity"
        )
        if getattr(sensor, "supports_humidity", True):
            entities.append(
                HumiditySensor(
                    device=sensor,
                    entry_id=config_entry.entry_id,
                )
            )
        await async_migrate_to_new_unique_id(
            hass, Platform.SENSOR, device=sensor, attr_name="Purity"
        )
        entities.append(
            PuritySensor(
                device=sensor,
                entry_id=config_entry.entry_id,
            )
        )
        await async_migrate_to_new_unique_id(
            hass, Platform.SENSOR, device=sensor, attr_name="AirQuality"
        )
        entities.append(
            AirQualitySensor(
                device=sensor,
                entry_id=config_entry.entry_id,
            )
        )
        await async_migrate_to_new_unique_id(
            hass,
            Platform.SENSOR,
            device=sensor,
            attr_name="TemperatureRating",
            old_unique_id=f"{sensor.serial}_temperature_rating",
        )
        entities.append(
            TemperatureRatingSensor(
                device=sensor,
                entry_id=config_entry.entry_id,
            )
        )
        await async_migrate_to_new_unique_id(
            hass,
            Platform.SENSOR,
            device=sensor,
            attr_name="HumidityRating",
            old_unique_id=f"{sensor.serial}_humidity_rating",
        )
        entities.append(
            HumidityRatingSensor(
                device=sensor,
                entry_id=config_entry.entry_id,
            )
        )
        await async_migrate_to_new_unique_id(
            hass,
            Platform.SENSOR,
            device=sensor,
            attr_name="PurityRating",
            old_unique_id=f"{sensor.serial}_purity_rating",
        )
        entities.append(
            PurityRatingSensor(
                device=sensor,
                entry_id=config_entry.entry_id,
            )
        )
        if diagnostic_enabled:
            entities.append(
                TwinguardCombinedRatingSensor(
                    device=sensor,
                    entry_id=config_entry.entry_id,
                )
            )
            entities.append(
                TwinguardDescriptionSensor(
                    device=sensor,
                    entry_id=config_entry.entry_id,
                )
            )

    if power_sensors_enabled:
        for sensor in (
            list(session.device_helper.smart_plugs)
            + list(session.device_helper.light_switches_bsm)
            + list(session.device_helper.micromodule_light_controls)
            + list(session.device_helper.micromodule_shutter_controls)
            + list(session.device_helper.micromodule_blinds)
        ):
            if device_excluded(sensor, config_entry.options):
                continue
            await async_migrate_to_new_unique_id(
                hass,
                Platform.SENSOR,
                device=sensor,
                attr_name="Power",
                old_unique_id=f"{sensor.serial}_power",
            )
            entities.append(
                PowerSensor(
                    device=sensor,
                    entry_id=config_entry.entry_id,
                )
            )
            await async_migrate_to_new_unique_id(
                hass,
                Platform.SENSOR,
                device=sensor,
                attr_name="Energy",
                old_unique_id=f"{sensor.serial}_energy",
            )
            entities.append(
                EnergySensor(
                    device=sensor,
                    entry_id=config_entry.entry_id,
                )
            )
            # #331: Smart Plug [+M] in Mini-PV mode reports PV yield separately.
            if getattr(sensor, "supports_energy_yield", False):
                entities.append(
                    EnergyYieldSensor(device=sensor, entry_id=config_entry.entry_id)
                )
                entities.append(
                    PowerYieldSensor(device=sensor, entry_id=config_entry.entry_id)
                )

    # Shutter Control II diagnostic fields (hass audit): reference moving
    # times recorded during the device's own calibration run. Diagnostic-only
    # (EntityCategory.DIAGNOSTIC), gated behind the same option as the other
    # audit-added diagnostic sensors.
    if diagnostic_enabled:
        for shutter in (
            list(getattr(session.device_helper, "shutter_controls", []))
            + list(getattr(session.device_helper, "micromodule_shutter_controls", []))
            + list(getattr(session.device_helper, "micromodule_blinds", []))
        ):
            if device_excluded(shutter, config_entry.options):
                continue
            entities.append(
                ReferenceMovingTimeTopToBottomSensor(
                    device=shutter, entry_id=config_entry.entry_id
                )
            )
            entities.append(
                ReferenceMovingTimeBottomToTopSensor(
                    device=shutter, entry_id=config_entry.entry_id
                )
            )

    for sensor in session.device_helper.smart_plugs_compact:
        if device_excluded(sensor, config_entry.options):
            continue
        if power_sensors_enabled:
            await async_migrate_to_new_unique_id(
                hass,
                Platform.SENSOR,
                device=sensor,
                attr_name="Power",
                old_unique_id=f"{sensor.serial}_power",
            )
            entities.append(
                PowerSensor(
                    device=sensor,
                    entry_id=config_entry.entry_id,
                )
            )
            await async_migrate_to_new_unique_id(
                hass,
                Platform.SENSOR,
                device=sensor,
                attr_name="Energy",
                old_unique_id=f"{sensor.serial}_energy",
            )
            entities.append(
                EnergySensor(
                    device=sensor,
                    entry_id=config_entry.entry_id,
                )
            )
            if getattr(sensor, "supports_energy_yield", False):
                entities.append(
                    EnergyYieldSensor(device=sensor, entry_id=config_entry.entry_id)
                )
                entities.append(
                    PowerYieldSensor(device=sensor, entry_id=config_entry.entry_id)
                )
        if diagnostic_enabled:
            await async_migrate_to_new_unique_id(
                hass,
                Platform.SENSOR,
                device=sensor,
                attr_name="CommunicationQuality",
                old_unique_id=f"{sensor.serial}_communication_quality",
            )
            entities.append(
                CommunicationQualitySensor(
                    device=sensor,
                    entry_id=config_entry.entry_id,
                )
            )

    for sensor in session.device_helper.motion_detectors:
        if device_excluded(sensor, config_entry.options):
            continue
        entities.append(
            IlluminanceLevelSensor(
                device=sensor,
                entry_id=config_entry.entry_id,
            )
        )

    for sensor in session.device_helper.motion_detectors2:
        if device_excluded(sensor, config_entry.options):
            continue
        entities.append(
            IlluminanceLevelSensor(
                device=sensor,
                entry_id=config_entry.entry_id,
            )
        )
        await async_migrate_to_new_unique_id(
            hass,
            Platform.SENSOR,
            device=sensor,
            attr_name="Temperature",
        )
        entities.append(
            TemperatureSensor(
                device=sensor,
                entry_id=config_entry.entry_id,
            )
        )
        # WalkTest state sensor: only created when WalkTest service is present.
        if (
            getattr(sensor, "supports_walk_test", False)
            and sensor.walk_state is not None
        ):
            entities.append(
                WalkStateSensor(
                    device=sensor,
                    entry_id=config_entry.entry_id,
                )
            )
        # DetectionTest state sensor: the local-API counterpart of WalkTest.
        if getattr(sensor, "supports_detection_test", False):
            entities.append(
                DetectionStateSensor(
                    device=sensor,
                    entry_id=config_entry.entry_id,
                )
            )
        # Installation profile is exposed as a writable `select` entity
        # (InstallationProfileSelect), not a sensor — see select.py (#353).
        if diagnostic_enabled:
            await async_migrate_to_new_unique_id(
                hass,
                Platform.SENSOR,
                device=sensor,
                attr_name="CommunicationQuality",
            )
            entities.append(
                CommunicationQualitySensor(
                    device=sensor,
                    entry_id=config_entry.entry_id,
                )
            )

    if diagnostic_enabled:
        for sensor in session.device_helper.shutter_contacts2:
            if device_excluded(sensor, config_entry.options):
                continue
            if not hasattr(sensor, "communicationquality"):
                continue
            await async_migrate_to_new_unique_id(
                hass,
                Platform.SENSOR,
                device=sensor,
                attr_name="CommunicationQuality",
            )
            entities.append(
                CommunicationQualitySensor(
                    device=sensor,
                    entry_id=config_entry.entry_id,
                )
            )

    if power_sensors_enabled:
        sensor = session.emma
        if sensor is not None:
            entities.append(
                EmmaPowerSensor(
                    device=sensor,
                    entry_id=config_entry.entry_id,
                )
            )

    if diagnostic_enabled:
        for sensor in (
            list(session.device_helper.motion_detectors)
            + list(session.device_helper.motion_detectors2)
            + list(session.device_helper.shutter_contacts)
            + list(session.device_helper.shutter_contacts2)
            + list(session.device_helper.smoke_detectors)
            + list(session.device_helper.thermostats)
            + list(session.device_helper.twinguards)
            + list(session.device_helper.universal_switches)
            + list(session.device_helper.wallthermostats)
            + list(session.device_helper.roomthermostats)
            + list(session.device_helper.water_leakage_detectors)
            + list(getattr(session.device_helper, "outdoor_sirens", []))
        ):
            if device_excluded(sensor, config_entry.options):
                continue
            if getattr(sensor, "supports_batterylevel", False):
                entities.append(
                    BatteryLevelSensor(
                        device=cast(SHCBatteryDevice, sensor),
                        entry_id=config_entry.entry_id,
                    )
                )

    for siren in getattr(session.device_helper, "outdoor_sirens", []):
        if device_excluded(siren, config_entry.options):
            continue
        if getattr(siren, "supports_power_supply", False):
            entities.append(
                SirenBatterySensor(device=siren, entry_id=config_entry.entry_id)
            )
            entities.append(
                SirenMainPowerSensor(device=siren, entry_id=config_entry.entry_id)
            )
            entities.append(
                SirenSolarChargingSensor(device=siren, entry_id=config_entry.entry_id)
            )

    # KeypadTrigger mapping (Universal Switch II button->scenario): diagnostic,
    # only created when the device actually exposes the service (spec-grounded).
    if diagnostic_enabled:
        for sensor in session.device_helper.universal_switches:
            if device_excluded(sensor, config_entry.options):
                continue
            if getattr(sensor, "supports_keypadtrigger", False):
                entities.append(
                    KeypadTriggerSensor(
                        device=sensor,
                        entry_id=config_entry.entry_id,
                    )
                )

    # getattr: some test fixtures use a bare SimpleNamespace runtime_data lacking
    # this field, and setup must degrade to zero Zigbee entities, not crash.
    zigbee_routing_coordinator = getattr(
        config_entry.runtime_data, "zigbee_routing_coordinator", None
    )
    if diagnostic_enabled and zigbee_routing_coordinator is not None:
        for zb_device in getattr(session, "devices", None) or []:
            zb_id = getattr(zb_device, "id", None)
            if not zb_id or not zb_id.startswith("hdm:ZigBee:"):
                continue
            if device_excluded(zb_device, config_entry.options):
                continue
            entities.append(
                ZigbeeRoutingQualitySensor(
                    device=zb_device,
                    session=session,
                    entry_id=config_entry.entry_id,
                    coordinator=zigbee_routing_coordinator,
                )
            )

    entities.append(
        SHCOpenWindowsSensor(
            session=session,
            entry_id=config_entry.entry_id,
            shc_device=getattr(config_entry.runtime_data, "shc_device", None),
        )
    )

    if entities:
        async_add_entities(entities)


# ===========================================================================
# EntityDescription-driven core (CORE-PREP: mirrors home-assistant/core's own
# bosch_shc/sensor.py shape, so a future upstream port only has to delete the
# thin per-sensor-type subclasses below and reference SENSOR_DESCRIPTIONS
# directly from async_setup_entry). Guard/error-handling logic that used to
# live in each class's own native_value/extra_state_attributes property now
# lives in the small helper functions referenced by value_fn/attributes_fn —
# behavior is unchanged, only where the logic is defined.
# ===========================================================================


# Device-type unions used to parametrize SHCSensorEntityDescription[_DeviceT]
# below. Each alias covers exactly the concrete device classes the
# corresponding value_fn/attributes_fn is actually called with, per
# device_helper's per-collection return types (see async_setup_entry above) —
# kept as named aliases for readability and reuse across a description entry,
# its leaf entity class, and any dedicated value_fn helper function.
type _TemperatureDevice = (
    SHCThermostat | SHCWallThermostat | SHCTwinguard | SHCMotionDetector2
)
type _HumidityDevice = SHCWallThermostat | SHCTwinguard
type _CommunicationQualityDevice = (
    SHCSmartPlugCompact | SHCShutterContact2 | SHCMotionDetector2
)
type _PowerMeterDevice = (
    SHCSmartPlug
    | SHCLightSwitchBSM
    | SHCLightControl
    | SHCMicromoduleShutterControl
    | SHCSmartPlugCompact
)
type _IlluminanceDevice = SHCMotionDetector | SHCMotionDetector2


@dataclass(frozen=True, kw_only=True)
class SHCSensorEntityDescription[_DeviceT: SHCDevice](SensorEntityDescription):
    """Describes a SHC sensor."""

    value_fn: Callable[[_DeviceT], StateType]
    attributes_fn: Callable[[_DeviceT], dict[str, Any] | None] | None = None


TEMPERATURE_SENSOR = "temperature"
TERMINAL_TEMPERATURE_SENSOR = "terminal_temperature"
HUMIDITY_SENSOR = "humidity"
PURITY_SENSOR = "purity"
AIR_QUALITY_SENSOR = "airquality"
TEMPERATURE_RATING_SENSOR = "temperaturerating"
HUMIDITY_RATING_SENSOR = "humidityrating"
PURITY_RATING_SENSOR = "purityrating"
COMMUNICATION_QUALITY_SENSOR = "communicationquality"
KEYPAD_TRIGGER_SENSOR = "keypadtrigger"
POWER_SENSOR = "power"
ENERGY_SENSOR = "energy"
ENERGY_YIELD_SENSOR = "energy_yield"
POWER_YIELD_SENSOR = "power_yield"
VALVE_TAPPET_SENSOR = "valvetappet"
ILLUMINANCE_SENSOR = "illuminance"
BATTERY_LEVEL_SENSOR = "battery_level"
COMBINED_RATING_SENSOR = "combined_rating"
AIR_QUALITY_DESCRIPTION_SENSOR = "description"
WALK_STATE_SENSOR = "walk_state"
DETECTION_STATE_SENSOR = "detection_state"
SIREN_BATTERY_SENSOR = "siren_battery"
SIREN_MAIN_POWER_SENSOR = "siren_main_power"
SIREN_SOLAR_CHARGING_SENSOR = "siren_solar_charging"
NEXT_SETPOINT_TEMPERATURE_SENSOR = "next_setpoint_temperature"
PRESENCE_SIMULATION_RUNNING_START_SENSOR = "running_start"
PRESENCE_SIMULATION_RUNNING_END_SENSOR = "running_end"
REFERENCE_MOVING_TIME_TTB_SENSOR = "reference_moving_time_ttb"
REFERENCE_MOVING_TIME_BTT_SENSOR = "reference_moving_time_btt"


def _air_quality_value(device: SHCTwinguard) -> str | None:
    """Return the Twinguard combined air-quality rating name."""
    try:
        return str(device.combined_rating.name)
    except ValueError as err:
        LOGGER.warning("Unknown combined rating for %s: %s", device.name, err)
        return None


def _air_quality_attributes(device: SHCTwinguard) -> dict[str, Any]:
    """Return rating_description (+ comfort_zone when available).

    comfort_zone is read from the AirQualityLevelService via a service-level
    accessor (_airqualitylevel_service.comfortZone). The SHCTwinguard model
    does not expose a model-level comfort_zone property, so we access the
    underlying service directly and fall back to omitting it when unavailable.
    """
    comfort_zone = None
    try:
        service = getattr(device, "_airqualitylevel_service", None)
        if service is not None:
            comfort_zone = service.comfortZone
    except (AttributeError, KeyError):
        pass
    attrs: dict[str, Any] = {"rating_description": device.description}
    if comfort_zone is not None:
        attrs["comfort_zone"] = comfort_zone
    return attrs


def _temperature_rating_value(device: SHCTwinguard) -> str | None:
    """Return the Twinguard temperature rating name."""
    try:
        return str(device.temperature_rating.name)
    except ValueError as err:
        LOGGER.warning("Unknown temperature rating for %s: %s", device.name, err)
        return None


def _humidity_rating_value(device: SHCTwinguard) -> str | None:
    """Return the Twinguard humidity rating name."""
    try:
        return str(device.humidity_rating.name)
    except ValueError as err:
        LOGGER.warning("Unknown humidity rating for %s: %s", device.name, err)
        return None


def _purity_rating_value(device: SHCTwinguard) -> str | None:
    """Return the Twinguard purity rating name."""
    try:
        return str(device.purity_rating.name)
    except ValueError as err:
        LOGGER.warning("Unknown purity rating for %s: %s", device.name, err)
        return None


def _communication_quality_value(device: _CommunicationQualityDevice) -> str | None:
    """Return the communication quality as a lowercase, translatable slug."""
    try:
        return str(device.communicationquality.name.lower())
    except (ValueError, AttributeError) as err:
        LOGGER.warning("Unknown communication quality for %s: %s", device.name, err)
        return None


def _keypad_trigger_attributes(device: SHCUniversalSwitch) -> dict[str, Any] | None:
    """Return scenario association state attributes."""
    service = device.keypadtrigger
    if service is None:
        return None
    return {
        "scenario_id_associations": service.scenario_id_associations,
        "ids_to_trigger": service.ids_to_trigger,
    }


def _energy_yield_value(device: _PowerMeterDevice) -> float | None:
    """Return the PV energy yield (kWh), or None when unreported."""
    value = device.energy_yield
    return None if value is None else value / 1000.0


def _power_yield_value(device: _PowerMeterDevice) -> float | None:
    """Return positive PV power (W); 0 while net-consuming."""
    consumption = device.powerconsumption
    if consumption is None:
        return None
    return -consumption if consumption < 0 else 0.0


def _valve_tappet_attributes(device: SHCThermostat) -> dict[str, Any]:
    """Return the valve tappet state attribute."""
    try:
        valve_tappet_state = device.valvestate.name
    except ValueError as err:
        LOGGER.warning("Unknown valve tappet state for %s: %s", device.name, err)
        valve_tappet_state = None
    return {"valve_tappet_state": valve_tappet_state}


def _illuminance_value(device: _IlluminanceDevice) -> float | None:
    """Return the numeric lux value, or None for non-numeric values (#315)."""
    value = device.illuminance
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    return None


def _battery_level_value(device: SHCBatteryDevice) -> str | None:
    """Return the battery level state string, or None on unknown value."""
    try:
        return str(device.batterylevel.value.lower())
    except (ValueError, AttributeError) as err:
        LOGGER.warning("Unknown battery level for %s: %s", device.name, err)
        return None


def _combined_rating_value(device: SHCTwinguard) -> str | None:
    """Return the combined rating enum name, or None on unknown value."""
    try:
        return str(device.combined_rating.name.lower())
    except (ValueError, AttributeError) as err:
        LOGGER.warning("Unknown combined rating for %s: %s", device.name, err)
        return None


def _walk_state_value(device: SHCMotionDetector2) -> str | None:
    """Return the current walk state as its enum name."""
    try:
        val = device.walk_state
        if val is None:
            return None
        return str(val.name.lower())
    except (AttributeError, ValueError):
        return None


def _detection_state_value(device: SHCMotionDetector2) -> str | None:
    """Return the current detection-test state as its enum name."""
    try:
        val = device.detection_state
        if val is None:
            return None
        return str(val.name.lower())
    except (AttributeError, ValueError):
        return None


def _siren_main_power_value(device: SHCOutdoorSiren) -> str | None:
    """Return the active power source as a lowercase slug."""
    power_supply = device.power_supply
    if power_supply is None:
        return None
    try:
        return str(power_supply.main_power_supply.name.lower())
    except AttributeError:
        return None


def _siren_solar_charging_value(device: SHCOutdoorSiren) -> str | None:
    """Return the solar charging score as a lowercase slug."""
    power_supply = device.power_supply
    if power_supply is None:
        return None
    try:
        return str(power_supply.solar_charging_score.name.lower())
    except AttributeError:
        return None


def _next_setpoint_temperature_attributes(device: SHCClimateControl) -> dict[str, Any]:
    """Return the next change time and operation mode as attributes."""
    next_mode = getattr(device, "next_operation_mode", None)
    return {
        "next_change_at": getattr(device, "next_setpoint_temperature_change", None),
        "next_operation_mode": next_mode.value if next_mode is not None else None,
    }


def _reference_moving_time_top_to_bottom_value(
    device: SHCShutterControl,
) -> float | None:
    """Return the recorded top-to-bottom moving time in seconds, if known."""
    value_ms = getattr(device, "reference_moving_time_top_to_bottom_ms", None)
    return value_ms / 1000 if value_ms is not None else None


def _reference_moving_time_bottom_to_top_value(
    device: SHCShutterControl,
) -> float | None:
    """Return the recorded bottom-to-top moving time in seconds, if known."""
    value_ms = getattr(device, "reference_moving_time_bottom_to_top_ms", None)
    return value_ms / 1000 if value_ms is not None else None


SENSOR_DESCRIPTIONS: dict[str, SHCSensorEntityDescription[Any]] = {
    TEMPERATURE_SENSOR: SHCSensorEntityDescription[_TemperatureDevice](
        key=TEMPERATURE_SENSOR,
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda device: device.temperature,
    ),
    TERMINAL_TEMPERATURE_SENSOR: SHCSensorEntityDescription[SHCWallThermostat](
        key=TERMINAL_TEMPERATURE_SENSOR,
        translation_key="floor_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        # Only SHCRoomThermostat2 (a SHCWallThermostat subtype) actually
        # defines terminal_temperature; async_setup_entry only constructs
        # this entity when getattr(sensor, "terminal_temperature", None) is
        # not None, so this mirrors that same defensive-getattr access
        # instead of requiring a device param narrower than what's actually
        # passed at the (shared wallthermostats+roomthermostats) call site.
        value_fn=lambda device: getattr(device, "terminal_temperature", None),
    ),
    HUMIDITY_SENSOR: SHCSensorEntityDescription[_HumidityDevice](
        key=HUMIDITY_SENSOR,
        device_class=SensorDeviceClass.HUMIDITY,
        native_unit_of_measurement=UnitOfRatio.PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda device: device.humidity,
    ),
    PURITY_SENSOR: SHCSensorEntityDescription[SHCTwinguard](
        key=PURITY_SENSOR,
        # Bosch "purity" is an air-purity/VOC ppm value, NOT CO2. HA Core's own
        # bosch_shc integration assigns no device_class here either; a
        # previous SensorDeviceClass.CO2 mis-classified the reading. #204
        translation_key="purity",
        native_unit_of_measurement=UnitOfRatio.PARTS_PER_MILLION,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda device: device.purity,
    ),
    AIR_QUALITY_SENSOR: SHCSensorEntityDescription[SHCTwinguard](
        key=AIR_QUALITY_SENSOR,
        translation_key="air_quality",
        value_fn=_air_quality_value,
        attributes_fn=_air_quality_attributes,
    ),
    TEMPERATURE_RATING_SENSOR: SHCSensorEntityDescription[SHCTwinguard](
        key=TEMPERATURE_RATING_SENSOR,
        translation_key="temperature_rating",
        value_fn=_temperature_rating_value,
    ),
    HUMIDITY_RATING_SENSOR: SHCSensorEntityDescription[SHCTwinguard](
        key=HUMIDITY_RATING_SENSOR,
        translation_key="humidity_rating",
        value_fn=_humidity_rating_value,
    ),
    PURITY_RATING_SENSOR: SHCSensorEntityDescription[SHCTwinguard](
        key=PURITY_RATING_SENSOR,
        translation_key="purity_rating",
        value_fn=_purity_rating_value,
    ),
    COMMUNICATION_QUALITY_SENSOR: SHCSensorEntityDescription[
        _CommunicationQualityDevice
    ](
        # #339: a pure diagnostic (Diagnostics category) ENUM sensor; state
        # values are lowercase slugs so HA can translate them.
        key=COMMUNICATION_QUALITY_SENSOR,
        translation_key="communication_quality",
        device_class=SensorDeviceClass.ENUM,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        options=["good", "normal", "not_supported", "bad", "unknown", "fetching"],
        value_fn=_communication_quality_value,
    ),
    KEYPAD_TRIGGER_SENSOR: SHCSensorEntityDescription[SHCUniversalSwitch](
        # Diagnostic: Universal Switch II button->scenario mapping
        # (spec-grounded). Reports the switchType; the scenario associations
        # are exposed as state attributes. Informational only — the actual
        # press events arrive via the Keypad service / device triggers, not
        # this sensor.
        key=KEYPAD_TRIGGER_SENSOR,
        translation_key="keypad_trigger",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda device: (
            device.keypadtrigger.switch_type
            if device.keypadtrigger is not None
            else None
        ),
        attributes_fn=_keypad_trigger_attributes,
    ),
    POWER_SENSOR: SHCSensorEntityDescription[_PowerMeterDevice](
        key=POWER_SENSOR,
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda device: device.powerconsumption,
    ),
    ENERGY_SENSOR: SHCSensorEntityDescription[_PowerMeterDevice](
        key=ENERGY_SENSOR,
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        value_fn=lambda device: device.energyconsumption / 1000.0,
    ),
    ENERGY_YIELD_SENSOR: SHCSensorEntityDescription[_PowerMeterDevice](
        # PV energy yield of a Smart Plug [+M] in Mini-PV mode (#331).
        key=ENERGY_YIELD_SENSOR,
        translation_key="energy_yield",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        value_fn=_energy_yield_value,
    ),
    POWER_YIELD_SENSOR: SHCSensorEntityDescription[_PowerMeterDevice](
        # PV power yield of a Smart Plug [+M] as a positive value (#331). The
        # PowerMeter reports negative powerConsumption while feeding in; this
        # sensor exposes that production as a positive number (0 W while
        # consuming), so it can be added directly to the HA Energy dashboard.
        key=POWER_YIELD_SENSOR,
        translation_key="power_yield",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=_power_yield_value,
    ),
    VALVE_TAPPET_SENSOR: SHCSensorEntityDescription[SHCThermostat](
        key=VALVE_TAPPET_SENSOR,
        translation_key="valve_tappet",
        native_unit_of_measurement=UnitOfRatio.PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        suggested_display_precision=0,
        value_fn=lambda device: device.position,
        attributes_fn=_valve_tappet_attributes,
    ),
    ILLUMINANCE_SENSOR: SHCSensorEntityDescription[_IlluminanceDevice](
        # Metadata (state_class/device_class/unit) stays static; native_value
        # alone coerces a non-numeric value to None, so metadata never
        # flip-flops (#315).
        key=ILLUMINANCE_SENSOR,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.ILLUMINANCE,
        native_unit_of_measurement=LIGHT_LUX,
        suggested_display_precision=0,
        value_fn=_illuminance_value,
    ),
    BATTERY_LEVEL_SENSOR: SHCSensorEntityDescription[SHCBatteryDevice](
        # Granular battery-level diagnostic sensor (ENUM, all 5
        # BatteryLevelService states). Complements the binary BatterySensor
        # (binary_sensor.py) which only signals OK vs. not-OK. #339: this
        # duplicates the binary "Battery" sensor for most users, so it is
        # disabled by default (power users can enable it per-entity).
        key=BATTERY_LEVEL_SENSOR,
        translation_key="battery_level",
        device_class=SensorDeviceClass.ENUM,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        options=[
            "ok",
            "low_battery",
            "critical_low",
            "critically_low_battery",
            "not_available",
        ],
        value_fn=_battery_level_value,
    ),
    COMBINED_RATING_SENSOR: SHCSensorEntityDescription[SHCTwinguard](
        # Diagnostic ENUM sensor for Twinguard overall combined air-quality
        # rating. Surfaces the combinedRating field from
        # AirQualityLevelService (CAT-3e gap). Distinct from AirQualitySensor
        # which exposes the same value as its primary state — this entity is
        # diagnostic-only so it does not clutter the default device view.
        key=COMBINED_RATING_SENSOR,
        translation_key="combined_rating",
        device_class=SensorDeviceClass.ENUM,
        entity_category=EntityCategory.DIAGNOSTIC,
        # "unknown" is a real RatingState member (boschshcpy falls back to it
        # on a missing/unrecognized combinedRating value, not just a
        # hypothetical) — omitting it made HA's SensorEntity.state raise
        # ValueError instead of showing "unknown" whenever that fallback fired.
        options=["good", "medium", "bad", "unknown"],
        value_fn=_combined_rating_value,
    ),
    AIR_QUALITY_DESCRIPTION_SENSOR: SHCSensorEntityDescription[SHCTwinguard](
        # Diagnostic sensor for Twinguard air-quality text description.
        # Surfaces the description field from AirQualityLevelService (CAT-3e
        # gap).
        key=AIR_QUALITY_DESCRIPTION_SENSOR,
        translation_key="air_quality_description",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda device: device.description,
    ),
    WALK_STATE_SENSOR: SHCSensorEntityDescription[SHCMotionDetector2](
        # Sensor for the Motion Detector II walk-test state. Reports the
        # current WalkTest walkState enum name (WALK_TEST_STARTED / STOPPED /
        # UNKNOWN). The WalkTest service is optional on MD2 hardware; this
        # sensor is only created when walk_state is not None.
        key=WALK_STATE_SENSOR,
        translation_key="walk_test_state",
        device_class=SensorDeviceClass.ENUM,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        options=["walk_test_started", "walk_test_stopped", "unknown"],
        value_fn=_walk_state_value,
    ),
    DETECTION_STATE_SENSOR: SHCSensorEntityDescription[SHCMotionDetector2](
        # Sensor for the Motion Detector II detection-test state. Reports the
        # DetectionTest detectionState enum name (DETECTION_TEST_STARTED /
        # STOPPED / UNKNOWN). The DetectionTest service is the local-API
        # equivalent of the APK WalkTest service; created only when the
        # device carries it.
        key=DETECTION_STATE_SENSOR,
        translation_key="detection_test_state",
        device_class=SensorDeviceClass.ENUM,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        options=[
            "detection_test_started",
            "detection_test_stopped",
            "detection_test_unknown",
        ],
        value_fn=_detection_state_value,
    ),
    SIREN_BATTERY_SENSOR: SHCSensorEntityDescription[SHCOutdoorSiren](
        # Outdoor Siren battery charge (#120).
        key=SIREN_BATTERY_SENSOR,
        translation_key="siren_battery",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=UnitOfRatio.PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda device: getattr(
            device.power_supply, "battery_percentage_remaining", None
        ),
    ),
    SIREN_MAIN_POWER_SENSOR: SHCSensorEntityDescription[SHCOutdoorSiren](
        # Outdoor Siren active power source (#120).
        key=SIREN_MAIN_POWER_SENSOR,
        translation_key="siren_main_power",
        device_class=SensorDeviceClass.ENUM,
        entity_category=EntityCategory.DIAGNOSTIC,
        options=["battery", "solar", "v12", "v230", "unknown"],
        value_fn=_siren_main_power_value,
    ),
    SIREN_SOLAR_CHARGING_SENSOR: SHCSensorEntityDescription[SHCOutdoorSiren](
        # Outdoor Siren solar-charging quality (#120).
        key=SIREN_SOLAR_CHARGING_SENSOR,
        translation_key="siren_solar_charging",
        device_class=SensorDeviceClass.ENUM,
        entity_category=EntityCategory.DIAGNOSTIC,
        options=["bad", "medium", "good", "unknown"],
        value_fn=_siren_solar_charging_value,
    ),
    NEXT_SETPOINT_TEMPERATURE_SENSOR: SHCSensorEntityDescription[SHCClimateControl](
        # Room-climate "next scheduled change" info (hass#120 audit). Change
        # time and next operation mode are exposed as attributes rather than
        # a second entity, matching the diagnostic-attribute pattern used by
        # the keypad-trigger sensor.
        key=NEXT_SETPOINT_TEMPERATURE_SENSOR,
        translation_key="next_setpoint_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda device: getattr(device, "next_setpoint_temperature", None),
        attributes_fn=_next_setpoint_temperature_attributes,
    ),
    PRESENCE_SIMULATION_RUNNING_START_SENSOR: SHCSensorEntityDescription[
        SHCPresenceSimulationSystem
    ](
        # When the current presence-simulation session started (hass#120
        # audit). Fully modeled in boschshcpy (PresenceSimulationConfiguration
        # Service.running_start_time) but never wired into an HA entity.
        # None whenever no simulation session is currently running (the
        # app's own NO_TIME_SET sentinel, "-", already normalizes to None in
        # the library).
        key=PRESENCE_SIMULATION_RUNNING_START_SENSOR,
        translation_key="presence_simulation_running_start",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda device: getattr(device, "running_start_time", None),
    ),
    PRESENCE_SIMULATION_RUNNING_END_SENSOR: SHCSensorEntityDescription[
        SHCPresenceSimulationSystem
    ](
        # When the current presence-simulation session will end (hass#120
        # audit). See the running-start description above for details.
        key=PRESENCE_SIMULATION_RUNNING_END_SENSOR,
        translation_key="presence_simulation_running_end",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda device: getattr(device, "running_end_time", None),
    ),
    REFERENCE_MOVING_TIME_TTB_SENSOR: SHCSensorEntityDescription[SHCShutterControl](
        # Shutter Control II: recorded top-to-bottom reference moving time.
        # Reads ShutterControl.reference_moving_time_top_to_bottom_ms,
        # recorded by the device's own end-position calibration run (hass
        # audit). Exposed in seconds; None on devices that have never
        # completed a calibration run.
        key=REFERENCE_MOVING_TIME_TTB_SENSOR,
        translation_key="reference_moving_time_top_to_bottom",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_reference_moving_time_top_to_bottom_value,
    ),
    REFERENCE_MOVING_TIME_BTT_SENSOR: SHCSensorEntityDescription[SHCShutterControl](
        # Shutter Control II: recorded bottom-to-top reference moving time.
        # See the top-to-bottom description above for details.
        key=REFERENCE_MOVING_TIME_BTT_SENSOR,
        translation_key="reference_moving_time_bottom_to_top",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_reference_moving_time_bottom_to_top_value,
    ),
}


class SHCSensor[_DeviceT: SHCDevice](SHCEntity, SensorEntity):  # type: ignore[misc]
    """Representation of a SHC sensor driven by a SHCSensorEntityDescription.

    Every plain (non-coordinator, non-callback-subscribing) sensor kind in
    this platform is expressed as one entry in SENSOR_DESCRIPTIONS and built
    through this single class — matching the pattern home-assistant/core's
    own (smaller-scope) bosch_shc/sensor.py port already uses. The per-kind
    subclasses below exist only so existing call sites/tests can keep
    constructing e.g. ``TemperatureSensor(device, entry_id)`` directly; each
    one is a one-line binding of a SENSOR_DESCRIPTIONS entry and contributes
    no behavior of its own.

    Generic over the concrete device type _DeviceT so entity_description's
    value_fn/attributes_fn can be typed against the actual boschshcpy model
    class each sensor kind reads from, instead of the generic SHCDevice base
    (core-prep: matches the "narrow self._device per leaf class" pattern
    already established elsewhere in this codebase for the same reason).
    """

    entity_description: SHCSensorEntityDescription[_DeviceT]

    def __init__(
        self,
        device: _DeviceT,
        entity_description: SHCSensorEntityDescription[_DeviceT],
        entry_id: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(device, entry_id)
        self._device: _DeviceT = device
        self.entity_description = entity_description
        self._attr_unique_id = (
            f"{device.root_device_id}_{device.id}_{entity_description.key}"
        )
        # A description translation_key should drive a translated entity name
        # even though this class has no class-level _attr_translation_key for
        # SHCEntity.__init__ to detect (same pattern as switch.py's SHCSwitch).
        if entity_description.translation_key:
            del self._attr_name

    @property
    def native_value(self) -> StateType:
        """Return the state of the sensor."""
        return self.entity_description.value_fn(self._device)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the state attributes."""
        if self.entity_description.attributes_fn is not None:
            return self.entity_description.attributes_fn(self._device)
        return None


class TemperatureSensor(SHCSensor[_TemperatureDevice]):  # type: ignore[misc]
    """Representation of an SHC temperature reporting sensor."""

    entity_description = SENSOR_DESCRIPTIONS[TEMPERATURE_SENSOR]

    def __init__(self, device: _TemperatureDevice, entry_id: str) -> None:
        """Initialize an SHC temperature reporting sensor."""
        super().__init__(device, self.entity_description, entry_id)


class TerminalTemperatureSensor(SHCSensor[SHCWallThermostat]):  # type: ignore[misc]
    """External floor/terminal sensor temperature of a Room Thermostat II 230V.

    #198 / #330: RTH2_230 with a floor sensor wired to its terminal reports a
    second temperature via TerminalConfiguration (distinct from the room
    TemperatureLevel). Only created when a sensor is actually connected.
    """

    entity_description = SENSOR_DESCRIPTIONS[TERMINAL_TEMPERATURE_SENSOR]

    def __init__(self, device: SHCWallThermostat, entry_id: str) -> None:
        """Initialize the terminal (floor) temperature sensor."""
        super().__init__(device, self.entity_description, entry_id)


class HumiditySensor(SHCSensor[_HumidityDevice]):  # type: ignore[misc]
    """Representation of an SHC humidity reporting sensor."""

    entity_description = SENSOR_DESCRIPTIONS[HUMIDITY_SENSOR]

    def __init__(self, device: _HumidityDevice, entry_id: str) -> None:
        """Initialize an SHC humidity reporting sensor."""
        super().__init__(device, self.entity_description, entry_id)


class PuritySensor(SHCSensor[SHCTwinguard]):  # type: ignore[misc]
    """Representation of an SHC purity reporting sensor."""

    entity_description = SENSOR_DESCRIPTIONS[PURITY_SENSOR]

    def __init__(self, device: SHCTwinguard, entry_id: str) -> None:
        """Initialize an SHC purity reporting sensor."""
        super().__init__(device, self.entity_description, entry_id)


class AirQualitySensor(SHCSensor[SHCTwinguard]):  # type: ignore[misc]
    """Representation of an SHC airquality reporting sensor."""

    entity_description = SENSOR_DESCRIPTIONS[AIR_QUALITY_SENSOR]

    def __init__(self, device: SHCTwinguard, entry_id: str) -> None:
        """Initialize an SHC airquality reporting sensor."""
        super().__init__(device, self.entity_description, entry_id)


class TemperatureRatingSensor(SHCSensor[SHCTwinguard]):  # type: ignore[misc]
    """Representation of an SHC temperature rating sensor."""

    entity_description = SENSOR_DESCRIPTIONS[TEMPERATURE_RATING_SENSOR]

    def __init__(self, device: SHCTwinguard, entry_id: str) -> None:
        """Initialize an SHC temperature rating sensor."""
        super().__init__(device, self.entity_description, entry_id)


class CommunicationQualitySensor(SHCSensor[_CommunicationQualityDevice]):  # type: ignore[misc]
    """Representation of an SHC communication quality reporting sensor.

    #339: a pure diagnostic (Diagnostics category) ENUM sensor; state values
    are lowercase slugs so HA can translate them (no more raw ALL-CAPS
    "GOOD"/"BAD").
    """

    entity_description = SENSOR_DESCRIPTIONS[COMMUNICATION_QUALITY_SENSOR]

    def __init__(self, device: _CommunicationQualityDevice, entry_id: str) -> None:
        """Initialize an SHC communication quality reporting sensor."""
        super().__init__(device, self.entity_description, entry_id)


class KeypadTriggerSensor(SHCSensor[SHCUniversalSwitch]):  # type: ignore[misc]
    """Diagnostic: Universal Switch II button->scenario mapping (spec-grounded).

    Reports the switchType; the scenario associations are exposed as state
    attributes. Informational only — the actual press events arrive via the
    Keypad service / device triggers, not this sensor.
    """

    entity_description = SENSOR_DESCRIPTIONS[KEYPAD_TRIGGER_SENSOR]

    def __init__(self, device: SHCUniversalSwitch, entry_id: str) -> None:
        """Initialize a SHC keypad-trigger mapping sensor."""
        super().__init__(device, self.entity_description, entry_id)


class HumidityRatingSensor(SHCSensor[SHCTwinguard]):  # type: ignore[misc]
    """Representation of an SHC humidity rating sensor."""

    entity_description = SENSOR_DESCRIPTIONS[HUMIDITY_RATING_SENSOR]

    def __init__(self, device: SHCTwinguard, entry_id: str) -> None:
        """Initialize an SHC humidity rating sensor."""
        super().__init__(device, self.entity_description, entry_id)


class PurityRatingSensor(SHCSensor[SHCTwinguard]):  # type: ignore[misc]
    """Representation of an SHC purity rating sensor."""

    entity_description = SENSOR_DESCRIPTIONS[PURITY_RATING_SENSOR]

    def __init__(self, device: SHCTwinguard, entry_id: str) -> None:
        """Initialize an SHC purity rating sensor."""
        super().__init__(device, self.entity_description, entry_id)


class PowerSensor(SHCSensor[_PowerMeterDevice]):  # type: ignore[misc]
    """Representation of an SHC power reporting sensor."""

    entity_description = SENSOR_DESCRIPTIONS[POWER_SENSOR]

    def __init__(self, device: _PowerMeterDevice, entry_id: str) -> None:
        """Initialize an SHC power reporting sensor."""
        super().__init__(device, self.entity_description, entry_id)


class EmmaPowerSensor(SHCEntity, SensorEntity):  # type: ignore[misc]
    """Representation of an SHC power reporting sensor.

    Not description-driven like the rest of this file: EMMA delivers its
    updates via a direct per-entity subscribe_callback/unsubscribe_callback
    pair (async_added_to_hass/async_will_remove_from_hass below) rather than
    through SHCEntity's own generic device-service subscription, so it keeps
    its own dedicated class.
    """

    _attr_entity_registry_enabled_default = False
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1

    def __init__(self, device: SHCEmma, entry_id: str) -> None:
        """Initialize an SHC power reporting sensor."""
        super().__init__(device, entry_id)
        self._device: SHCEmma = device  # type: ignore[assignment]
        self._attr_unique_id = f"{device.root_device_id}_{device.id}_power"

    async def async_added_to_hass(self) -> None:
        """Subscribe to SHC events."""
        await super().async_added_to_hass()

        def update_entity_information() -> None:
            self.schedule_update_ha_state()

        self._device.subscribe_callback(self.entity_id, update_entity_information)

    async def async_will_remove_from_hass(self) -> None:
        """Unsubscribe from SHC events."""
        await super().async_will_remove_from_hass()
        self._device.unsubscribe_callback(self.entity_id)

    @property
    def native_value(self) -> Any:
        """Return the state of the sensor. Negative value if power is consumed from the grid, positive if fed to the grid."""
        return self._device.value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        return {
            "power_flow": self._device.localizedSubtitles,
        }


class EnergySensor(SHCSensor[_PowerMeterDevice]):  # type: ignore[misc]
    """Representation of an SHC energy reporting sensor."""

    entity_description = SENSOR_DESCRIPTIONS[ENERGY_SENSOR]

    def __init__(self, device: _PowerMeterDevice, entry_id: str) -> None:
        """Initialize an SHC energy reporting sensor."""
        super().__init__(device, self.entity_description, entry_id)


class EnergyYieldSensor(SHCSensor[_PowerMeterDevice]):  # type: ignore[misc]
    """PV energy yield of a Smart Plug [+M] in Mini-PV mode (#331)."""

    entity_description = SENSOR_DESCRIPTIONS[ENERGY_YIELD_SENSOR]

    def __init__(self, device: _PowerMeterDevice, entry_id: str) -> None:
        """Initialize the energy yield sensor."""
        super().__init__(device, self.entity_description, entry_id)


class PowerYieldSensor(SHCSensor[_PowerMeterDevice]):  # type: ignore[misc]
    """PV power yield of a Smart Plug [+M] as a positive value (#331)."""

    entity_description = SENSOR_DESCRIPTIONS[POWER_YIELD_SENSOR]

    def __init__(self, device: _PowerMeterDevice, entry_id: str) -> None:
        """Initialize the power yield sensor."""
        super().__init__(device, self.entity_description, entry_id)


class ValveTappetSensor(SHCSensor[SHCThermostat]):  # type: ignore[misc]
    """Representation of an SHC valve tappet reporting sensor."""

    entity_description = SENSOR_DESCRIPTIONS[VALVE_TAPPET_SENSOR]

    def __init__(self, device: SHCThermostat, entry_id: str) -> None:
        """Initialize an SHC valve tappet reporting sensor."""
        super().__init__(device, self.entity_description, entry_id)


class IlluminanceLevelSensor(SHCSensor[_IlluminanceDevice]):  # type: ignore[misc]
    """Representation of an SHC illuminance level reporting sensor."""

    entity_description = SENSOR_DESCRIPTIONS[ILLUMINANCE_SENSOR]

    def __init__(self, device: _IlluminanceDevice, entry_id: str) -> None:
        """Initialize an SHC illuminance level reporting sensor."""
        super().__init__(device, self.entity_description, entry_id)


class BatteryLevelSensor(SHCSensor[SHCBatteryDevice]):  # type: ignore[misc]
    """Granular battery-level diagnostic sensor (ENUM, all 5 BatteryLevelService states)."""

    entity_description = SENSOR_DESCRIPTIONS[BATTERY_LEVEL_SENSOR]

    def __init__(self, device: SHCBatteryDevice, entry_id: str) -> None:
        """Initialize a battery-level sensor."""
        super().__init__(device, self.entity_description, entry_id)


class TwinguardCombinedRatingSensor(SHCSensor[SHCTwinguard]):  # type: ignore[misc]
    """Diagnostic ENUM sensor for Twinguard overall combined air-quality rating."""

    entity_description = SENSOR_DESCRIPTIONS[COMBINED_RATING_SENSOR]

    def __init__(self, device: SHCTwinguard, entry_id: str) -> None:
        """Initialize a Twinguard combined-rating diagnostic sensor."""
        super().__init__(device, self.entity_description, entry_id)


class TwinguardDescriptionSensor(SHCSensor[SHCTwinguard]):  # type: ignore[misc]
    """Diagnostic sensor for Twinguard air-quality text description."""

    entity_description = SENSOR_DESCRIPTIONS[AIR_QUALITY_DESCRIPTION_SENSOR]

    def __init__(self, device: SHCTwinguard, entry_id: str) -> None:
        """Initialize a Twinguard air-quality description diagnostic sensor."""
        super().__init__(device, self.entity_description, entry_id)


class WalkStateSensor(SHCSensor[SHCMotionDetector2]):  # type: ignore[misc]
    """Sensor for the Motion Detector II walk-test state."""

    entity_description = SENSOR_DESCRIPTIONS[WALK_STATE_SENSOR]

    def __init__(self, device: SHCMotionDetector2, entry_id: str) -> None:
        """Initialize the walk-state sensor."""
        super().__init__(device, self.entity_description, entry_id)


class DetectionStateSensor(SHCSensor[SHCMotionDetector2]):  # type: ignore[misc]
    """Sensor for the Motion Detector II detection-test state."""

    entity_description = SENSOR_DESCRIPTIONS[DETECTION_STATE_SENSOR]

    def __init__(self, device: SHCMotionDetector2, entry_id: str) -> None:
        """Initialize the detection-state sensor."""
        super().__init__(device, self.entity_description, entry_id)


class SirenBatterySensor(SHCSensor[SHCOutdoorSiren]):  # type: ignore[misc]
    """Outdoor Siren battery charge (#120)."""

    entity_description = SENSOR_DESCRIPTIONS[SIREN_BATTERY_SENSOR]

    def __init__(self, device: SHCOutdoorSiren, entry_id: str) -> None:
        """Initialize the outdoor siren battery sensor."""
        super().__init__(device, self.entity_description, entry_id)


class SirenMainPowerSensor(SHCSensor[SHCOutdoorSiren]):  # type: ignore[misc]
    """Outdoor Siren active power source (#120)."""

    entity_description = SENSOR_DESCRIPTIONS[SIREN_MAIN_POWER_SENSOR]

    def __init__(self, device: SHCOutdoorSiren, entry_id: str) -> None:
        """Initialize the outdoor siren main power source sensor."""
        super().__init__(device, self.entity_description, entry_id)


class SirenSolarChargingSensor(SHCSensor[SHCOutdoorSiren]):  # type: ignore[misc]
    """Outdoor Siren solar-charging quality (#120)."""

    entity_description = SENSOR_DESCRIPTIONS[SIREN_SOLAR_CHARGING_SENSOR]

    def __init__(self, device: SHCOutdoorSiren, entry_id: str) -> None:
        """Initialize the outdoor siren solar charging quality sensor."""
        super().__init__(device, self.entity_description, entry_id)


class NextSetpointTemperatureSensor(SHCSensor[SHCClimateControl]):  # type: ignore[misc]
    """Room-climate "next scheduled change" info (hass#120 audit)."""

    entity_description = SENSOR_DESCRIPTIONS[NEXT_SETPOINT_TEMPERATURE_SENSOR]
    _unrecorded_attributes = frozenset({"next_change_at"})

    def __init__(
        self, device: SHCClimateControl, entry_id: str, room_name: str | None = None
    ) -> None:
        """Initialize the next-setpoint-temperature sensor."""
        super().__init__(device, self.entity_description, entry_id)
        self._room_name = room_name

    @property
    def device_name(self) -> str:
        """Name of the device (the room, matching ClimateControl's own device_info).

        The raw ROOM_CLIMATE_CONTROL device's own name is a generic
        placeholder ("-RoomClimateControl-") -- every entity sharing this
        device's identifiers must resolve the real room name the same way,
        or whichever platform's device registry write lands last wins and
        overwrites the others (hass#372).
        """
        return (
            self._room_name if self._room_name is not None else str(self._device.name)
        )


class PresenceSimulationRunningStartSensor(SHCSensor[SHCPresenceSimulationSystem]):  # type: ignore[misc]
    """When the current presence-simulation session started (hass#120 audit)."""

    entity_description = SENSOR_DESCRIPTIONS[PRESENCE_SIMULATION_RUNNING_START_SENSOR]

    def __init__(self, device: SHCPresenceSimulationSystem, entry_id: str) -> None:
        """Initialize the presence-simulation running-start sensor."""
        super().__init__(device, self.entity_description, entry_id)


class PresenceSimulationRunningEndSensor(SHCSensor[SHCPresenceSimulationSystem]):  # type: ignore[misc]
    """When the current presence-simulation session will end (hass#120 audit)."""

    entity_description = SENSOR_DESCRIPTIONS[PRESENCE_SIMULATION_RUNNING_END_SENSOR]

    def __init__(self, device: SHCPresenceSimulationSystem, entry_id: str) -> None:
        """Initialize the presence-simulation running-end sensor."""
        super().__init__(device, self.entity_description, entry_id)


class ReferenceMovingTimeTopToBottomSensor(SHCSensor[SHCShutterControl]):  # type: ignore[misc]
    """Shutter Control II: recorded top-to-bottom reference moving time."""

    entity_description = SENSOR_DESCRIPTIONS[REFERENCE_MOVING_TIME_TTB_SENSOR]

    def __init__(self, device: SHCShutterControl, entry_id: str) -> None:
        """Initialize the top-to-bottom reference moving time sensor."""
        super().__init__(device, self.entity_description, entry_id)


class ReferenceMovingTimeBottomToTopSensor(SHCSensor[SHCShutterControl]):  # type: ignore[misc]
    """Shutter Control II: recorded bottom-to-top reference moving time."""

    entity_description = SENSOR_DESCRIPTIONS[REFERENCE_MOVING_TIME_BTT_SENSOR]

    def __init__(self, device: SHCShutterControl, entry_id: str) -> None:
        """Initialize the bottom-to-top reference moving time sensor."""
        super().__init__(device, self.entity_description, entry_id)


def _zigbee_hop_device_and_quality(hop: Any) -> tuple[Any, Any]:
    """Extract (device_id, quality) from one Zigbee routing hop.

    The documented contract is an object exposing .device_id/.quality, but the
    ground-truth example response ("route hops [(self, GOOD), (router-plug,
    GOOD)]") reads like a plain 2-tuple — accept either shape defensively.
    """
    device_id = getattr(hop, "device_id", None)
    quality = getattr(hop, "quality", None)
    if device_id is None and quality is None and isinstance(hop, (tuple, list)):
        if len(hop) == 2:
            device_id, quality = hop
    return device_id, quality


class SHCOpenWindowsSensor(SensorEntity):  # type: ignore[misc]
    """Whole-home summary of open doors/windows (official OpenAPI spec).

    Not tied to one SHC device -- scoped to the config entry like
    SHCEnableAllDiagnosticsButton -- so this does not inherit SHCEntity.
    The underlying `doors-windows/openwindows` endpoint is a plain GET, not
    delivered by the long-poll stream, so this is should_poll=True like the
    other new probe-based entities added this session.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "open_windows_doors"
    _attr_should_poll = True
    _attr_native_unit_of_measurement = "doors/windows"

    def __init__(
        self,
        session: SHCSession,
        entry_id: str,
        shc_device: DeviceEntry | None = None,
    ) -> None:
        """Initialize the open-windows/doors summary sensor."""
        self._session = session
        self._entry_id = entry_id
        self._shc_device = shc_device
        prefix = shc_device.id if shc_device is not None else entry_id
        self._attr_unique_id = f"{prefix}_open_windows_doors"
        self._open_doors: list[dict[str, Any]] = []
        self._open_windows: list[dict[str, Any]] = []
        self._open_others: list[dict[str, Any]] = []

    @property
    def device_info(self) -> DeviceInfo | None:
        """Return the device info (links this sensor to the SHC controller device)."""
        if self._shc_device is None:
            return None
        return DeviceInfo(identifiers=self._shc_device.identifiers)

    @property
    def native_value(self) -> int:
        """Return the total count of open doors, windows, and other openings."""
        return len(self._open_doors) + len(self._open_windows) + len(self._open_others)

    @property
    def extra_state_attributes(self) -> dict[str, list[str]]:
        """Return the names of each currently-open door/window/other opening."""
        return {
            "open_doors": [d.get("name", "") for d in self._open_doors],
            "open_windows": [w.get("name", "") for w in self._open_windows],
            "open_others": [o.get("name", "") for o in self._open_others],
        }

    async def async_update(self) -> None:
        """Poll the whole-home open-doors/open-windows summary."""
        try:
            data = await self._session.api.get_open_windows()
        except SHCException as err:
            LOGGER.debug("Failed to poll open-windows summary: %s", err)
            return
        self._open_doors = data.get("openDoors", [])
        self._open_windows = data.get("openWindows", [])
        self._open_others = data.get("openOthers", [])


class ZigbeeRoutingQualitySensor(  # type: ignore[misc]
    CoordinatorEntity[SHCZigbeeRoutingCoordinator], SHCEntity, SensorEntity
):
    """Zigbee mesh routing quality diagnostic sensor.

    Ground truth from a real SHC: only devices whose id is a Zigbee endpoint
    (``device.id`` starts with ``hdm:ZigBee:``) support
    ``SHCSessionAsync.get_zigbee_routing_info``. Unlike almost every other
    entity in this integration (iot_class local_push, state changes arrive via
    the long-poll stream), this data is NOT delivered by the long-poll stream
    at all — it requires an active HTTPS GET. Per HA's documented pattern for
    polled data (developers.home-assistant.io/docs/
    integration_fetching_data/), this is backed by a
    ``SHCZigbeeRoutingCoordinator`` (created once in ``__init__.py``, shared
    across every Zigbee device's sensor) rather than a per-entity
    should_poll/async_update — reads its state from
    ``self.coordinator.data``, keyed by this device's id.

    Not description-driven like the rest of this file: this class needs a
    coordinator + session reference the generic SHCSensor constructor does
    not accept, and mixes in CoordinatorEntity.
    """

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_translation_key = "zigbee_routing_quality"
    _attr_options = [
        "good",
        "medium",
        "bad",
        "no_connection",
        "device_not_initialized",
        "not_supported",
        "unknown",
    ]

    def __init__(
        self,
        device: SHCDevice,
        session: SHCSession,
        entry_id: str,
        coordinator: SHCZigbeeRoutingCoordinator,
    ) -> None:
        """Initialize the Zigbee routing quality sensor."""
        CoordinatorEntity.__init__(self, coordinator)
        SHCEntity.__init__(self, device, entry_id)
        self._session = session
        self._attr_unique_id = (
            f"{device.root_device_id}_{device.id}_zigbee_routing_quality"
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to both the underlying device's SHC events and the coordinator."""
        await SHCEntity.async_added_to_hass(self)
        self.async_on_remove(
            self.coordinator.async_add_listener(self._handle_coordinator_update)
        )

    @property
    def _routing_info(self) -> Any:
        """This device's routing info from the latest coordinator refresh, if any."""
        return (self.coordinator.data or {}).get(self._device.id)

    @property
    def available(self) -> bool:
        """False if the device is down, or absent from coordinator.data."""
        return (
            self.coordinator.last_update_success
            and SHCEntity.available.fget(self)  # type: ignore[attr-defined]
            and self._device.id in (self.coordinator.data or {})
        )

    @property
    def native_value(self) -> str | None:
        """Return the aggregated routing quality as a lowercase slug."""
        routing_info = self._routing_info
        if routing_info is None:
            return None
        try:
            return str(routing_info.aggregated_quality.name.lower())
        except (AttributeError, ValueError) as err:
            LOGGER.debug(
                "Unknown Zigbee routing quality for %s: %s", self._device.name, err
            )
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the routing hop list, resolving hop device ids to names."""
        routing_info = self._routing_info
        if routing_info is None:
            return None
        route = []
        for hop in getattr(routing_info, "route", None) or []:
            hop_device_id, hop_quality = _zigbee_hop_device_and_quality(hop)
            device_name: Any = hop_device_id
            if hop_device_id is not None:
                try:
                    device_name = self._session.device(hop_device_id).name
                except (KeyError, AttributeError):
                    device_name = hop_device_id
            quality_slug = None
            if hop_quality is not None:
                try:
                    quality_slug = str(hop_quality.name.lower())
                except AttributeError:
                    quality_slug = str(hop_quality).lower()
            route.append({"device": device_name, "quality": quality_slug})
        return {"route": route}
