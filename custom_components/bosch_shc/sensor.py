"""Platform for sensor integration."""

from __future__ import annotations

from typing import Any

from boschshcpy import SHCEmma, SHCSession
from boschshcpy.device import SHCDevice
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    LIGHT_LUX,
    Platform,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
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
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DATA_SESSION,
    DOMAIN,
    LOGGER,
    OPT_DIAGNOSTIC_ENTITIES,
    OPT_SUPPRESS_POWER_SENSORS,
)
from .entity import SHCEntity, async_migrate_to_new_unique_id, device_excluded

PARALLEL_UPDATES = 1


async def async_setup_entry(  # noqa: C901
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the SHC sensor platform."""
    entities: list[SensorEntity] = []
    session: SHCSession = hass.data[DOMAIN][config_entry.entry_id][DATA_SESSION]
    sensor: SHCDevice
    diagnostic_enabled = config_entry.options.get(OPT_DIAGNOSTIC_ENTITIES, True)
    power_sensors_enabled = not config_entry.options.get(
        OPT_SUPPRESS_POWER_SENSORS, False
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
            if sensor.supports_batterylevel:
                entities.append(
                    BatteryLevelSensor(
                        device=sensor,
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

    if entities:
        async_add_entities(entities)


class TemperatureSensor(SHCEntity, SensorEntity):  # type: ignore[misc]
    """Representation of an SHC temperature reporting sensor."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize an SHC temperature reporting sensor."""
        super().__init__(device, entry_id)
        self._attr_unique_id = f"{device.root_device_id}_{device.id}_temperature"

    @property
    def native_value(self) -> Any:
        """Return the state of the sensor."""
        return self._device.temperature


class TerminalTemperatureSensor(SHCEntity, SensorEntity):  # type: ignore[misc]
    """External floor/terminal sensor temperature of a Room Thermostat II 230V.

    #198 / #330: RTH2_230 with a floor sensor wired to its terminal reports a
    second temperature via TerminalConfiguration (distinct from the room
    TemperatureLevel). Only created when a sensor is actually connected.
    """

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1
    _attr_translation_key = "floor_temperature"

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize the terminal (floor) temperature sensor."""
        super().__init__(device, entry_id)
        self._attr_unique_id = (
            f"{device.root_device_id}_{device.id}_terminal_temperature"
        )

    @property
    def native_value(self) -> Any:
        """Return the external floor/terminal sensor temperature."""
        return self._device.terminal_temperature


class HumiditySensor(SHCEntity, SensorEntity):  # type: ignore[misc]
    """Representation of an SHC humidity reporting sensor."""

    _attr_device_class = SensorDeviceClass.HUMIDITY
    _attr_native_unit_of_measurement = UnitOfRatio.PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize an SHC humidity reporting sensor."""
        super().__init__(device, entry_id)
        self._attr_unique_id = f"{device.root_device_id}_{device.id}_humidity"

    @property
    def native_value(self) -> Any:
        """Return the state of the sensor."""
        return self._device.humidity


class PuritySensor(SHCEntity, SensorEntity):  # type: ignore[misc]
    """Representation of an SHC purity reporting sensor."""

    # Bosch "purity" is an air-purity/VOC ppm value, NOT CO2.  HA Core's own
    # bosch_shc integration assigns no device_class here either; the previous
    # SensorDeviceClass.CO2 mis-classified the reading (and pulled in HA's CO2
    # safety thresholds / statistics handling). #204
    _attr_icon = "mdi:air-filter"
    _attr_native_unit_of_measurement = UnitOfRatio.PARTS_PER_MILLION
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0
    _attr_translation_key = "purity"

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize an SHC purity reporting sensor."""
        super().__init__(device, entry_id)
        self._attr_unique_id = f"{device.root_device_id}_{device.id}_purity"

    @property
    def native_value(self) -> Any:
        """Return the state of the sensor."""
        return self._device.purity


class AirQualitySensor(SHCEntity, SensorEntity):  # type: ignore[misc]
    """Representation of an SHC airquality reporting sensor."""

    _attr_translation_key = "air_quality"

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize an SHC airquality reporting sensor."""
        super().__init__(device, entry_id)
        self._attr_unique_id = f"{device.root_device_id}_{device.id}_airquality"

    @property
    def native_value(self) -> str | None:
        """Return the state of the sensor."""
        try:
            return str(self._device.combined_rating.name)
        except ValueError as err:
            LOGGER.warning("Unknown combined rating for %s: %s", self._device.name, err)
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes.

        comfort_zone is read from the AirQualityLevelService via a service-level
        accessor (_airqualitylevel_service.comfortZone). The SHCTwinguard model
        does not expose a model-level comfort_zone property, so we access the
        underlying service directly and fall back to None when unavailable.
        """
        comfort_zone = None
        try:
            service = getattr(self._device, "_airqualitylevel_service", None)
            if service is not None:
                comfort_zone = service.comfortZone
        except (AttributeError, KeyError):
            pass
        attrs = {
            "rating_description": self._device.description,
        }
        if comfort_zone is not None:
            attrs["comfort_zone"] = comfort_zone
        return attrs


class TemperatureRatingSensor(SHCEntity, SensorEntity):  # type: ignore[misc]
    """Representation of an SHC temperature rating sensor."""

    _attr_translation_key = "temperature_rating"

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize an SHC temperature rating sensor."""
        super().__init__(device, entry_id)
        self._attr_unique_id = f"{device.root_device_id}_{device.id}_temperaturerating"

    @property
    def native_value(self) -> str | None:
        """Return the state of the sensor."""
        try:
            return str(self._device.temperature_rating.name)
        except ValueError as err:
            LOGGER.warning(
                "Unknown temperature rating for %s: %s", self._device.name, err
            )
            return None


class CommunicationQualitySensor(SHCEntity, SensorEntity):  # type: ignore[misc]
    """Representation of an SHC communication quality reporting sensor.

    #339: a pure diagnostic (Diagnostics category) ENUM sensor; state values are
    lowercase slugs so HA can translate them (no more raw ALL-CAPS "GOOD"/"BAD").
    """

    _attr_icon = "mdi:wifi"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_translation_key = "communication_quality"
    _attr_options = ["good", "normal", "medium", "bad", "unknown", "fetching"]

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize an SHC communication quality reporting sensor."""
        super().__init__(device, entry_id)
        self._attr_unique_id = (
            f"{device.root_device_id}_{device.id}_communicationquality"
        )

    @property
    def native_value(self) -> str | None:
        """Return the quality as a lowercase slug (translated for display)."""
        try:
            return str(self._device.communicationquality.name.lower())
        except (ValueError, AttributeError) as err:
            LOGGER.warning(
                "Unknown communication quality for %s: %s", self._device.name, err
            )
            return None


class KeypadTriggerSensor(SHCEntity, SensorEntity):  # type: ignore[misc]
    """Diagnostic: Universal Switch II button->scenario mapping (spec-grounded).

    Reports the switchType; the scenario associations are exposed as state
    attributes. Informational only — the actual press events arrive via the
    Keypad service / device triggers, not this sensor.
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "keypad_trigger"
    _attr_icon = "mdi:gesture-tap-button"

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize a SHC keypad-trigger mapping sensor."""
        super().__init__(device, entry_id)
        self._attr_unique_id = f"{device.root_device_id}_{device.id}_keypadtrigger"

    @property
    def native_value(self) -> Any:
        """Return the switch type of the keypad trigger service."""
        service = self._device.keypadtrigger
        return service.switch_type if service is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return scenario association state attributes."""
        service = self._device.keypadtrigger
        if service is None:
            return None
        return {
            "scenario_id_associations": service.scenario_id_associations,
            "ids_to_trigger": service.ids_to_trigger,
        }


class HumidityRatingSensor(SHCEntity, SensorEntity):  # type: ignore[misc]
    """Representation of an SHC humidity rating sensor."""

    _attr_translation_key = "humidity_rating"

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize an SHC humidity rating sensor."""
        super().__init__(device, entry_id)
        self._attr_unique_id = f"{device.root_device_id}_{device.id}_humidityrating"

    @property
    def native_value(self) -> str | None:
        """Return the state of the sensor."""
        try:
            return str(self._device.humidity_rating.name)
        except ValueError as err:
            LOGGER.warning("Unknown humidity rating for %s: %s", self._device.name, err)
            return None


class PurityRatingSensor(SHCEntity, SensorEntity):  # type: ignore[misc]
    """Representation of an SHC purity rating sensor."""

    _attr_translation_key = "purity_rating"

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize an SHC purity rating sensor."""
        super().__init__(device, entry_id)
        self._attr_unique_id = f"{device.root_device_id}_{device.id}_purityrating"

    @property
    def native_value(self) -> str | None:
        """Return the state of the sensor."""
        try:
            return str(self._device.purity_rating.name)
        except ValueError as err:
            LOGGER.warning("Unknown purity rating for %s: %s", self._device.name, err)
            return None


class PowerSensor(SHCEntity, SensorEntity):  # type: ignore[misc]
    """Representation of an SHC power reporting sensor."""

    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize an SHC power reporting sensor."""
        super().__init__(device, entry_id)
        self._attr_unique_id = f"{device.root_device_id}_{device.id}_power"

    @property
    def native_value(self) -> Any:
        """Return the state of the sensor."""
        return self._device.powerconsumption


class EmmaPowerSensor(SHCEntity, SensorEntity):  # type: ignore[misc]
    """Representation of an SHC power reporting sensor."""

    _attr_entity_registry_enabled_default = False
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1

    def __init__(self, device: SHCEmma, entry_id: str) -> None:
        """Initialize an SHC power reporting sensor."""
        super().__init__(device, entry_id)
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


class EnergySensor(SHCEntity, SensorEntity):  # type: ignore[misc]
    """Representation of an SHC energy reporting sensor."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_suggested_display_precision = 2

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize an SHC energy reporting sensor."""
        super().__init__(device, entry_id)
        self._attr_unique_id = f"{device.root_device_id}_{self._device.id}_energy"

    @property
    def native_value(self) -> Any:
        """Return the state of the sensor."""
        return self._device.energyconsumption / 1000.0


class EnergyYieldSensor(SHCEntity, SensorEntity):  # type: ignore[misc]
    """PV energy yield of a Smart Plug [+M] in Mini-PV mode (#331)."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_suggested_display_precision = 2
    _attr_translation_key = "energy_yield"

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize the energy yield sensor."""
        super().__init__(device, entry_id)
        self._attr_unique_id = f"{device.root_device_id}_{self._device.id}_energy_yield"

    @property
    def native_value(self) -> Any:
        """Return the PV energy yield (kWh), or None when unreported."""
        value = self._device.energy_yield
        return None if value is None else value / 1000.0


class PowerYieldSensor(SHCEntity, SensorEntity):  # type: ignore[misc]
    """PV power yield of a Smart Plug [+M] as a positive value (#331).

    The PowerMeter reports negative powerConsumption while feeding in. This
    sensor exposes that production as a positive number (0 W while consuming),
    so it can be added directly to the HA Energy dashboard.
    """

    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1
    _attr_translation_key = "power_yield"

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize the power yield sensor."""
        super().__init__(device, entry_id)
        self._attr_unique_id = f"{device.root_device_id}_{self._device.id}_power_yield"

    @property
    def native_value(self) -> Any:
        """Return positive PV power (W); 0 while net-consuming."""
        consumption = self._device.powerconsumption
        if consumption is None:
            return None
        return -consumption if consumption < 0 else 0.0


class ValveTappetSensor(SHCEntity, SensorEntity):  # type: ignore[misc]
    """Representation of an SHC valve tappet reporting sensor."""

    _attr_icon = "mdi:gauge"
    _attr_native_unit_of_measurement = UnitOfRatio.PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_suggested_display_precision = 0
    _attr_translation_key = "valve_tappet"

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize an SHC valve tappet reporting sensor."""
        super().__init__(device, entry_id)
        self._attr_unique_id = f"{device.root_device_id}_{device.id}_valvetappet"

    @property
    def native_value(self) -> Any:
        """Return the state of the sensor."""
        return self._device.position

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        try:
            valve_tappet_state = self._device.valvestate.name
        except ValueError as err:
            LOGGER.warning(
                "Unknown valve tappet state for %s: %s", self._device.name, err
            )
            valve_tappet_state = None
        return {
            "valve_tappet_state": valve_tappet_state,
        }


class IlluminanceLevelSensor(SHCEntity, SensorEntity):  # type: ignore[misc]
    """Representation of an SHC illuminance level reporting sensor.

    The Bosch SHC API spec defines illuminance as integer for both Gen1
    (SHCMotionDetector, model "MD") and Gen2 (SHCMotionDetector2, model "MD2").
    Gen1 devices report numeric lux values too (e.g. 13, 9, 22) — see #315.

    Metadata (state_class/device_class/unit) is STATIC so it never flip-flops:
    a previous conditional implementation dropped state_class whenever the
    value was momentarily None (offline / between polls), which re-raised the
    very state_class_removed repair this restores (and emitted "unit changed"
    warnings). Instead the metadata stays put and native_value coerces any
    non-numeric/qualitative value to None, so a hypothetical string-reporting
    firmware degrades to "unknown" rather than conflicting with MEASUREMENT.
    """

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_device_class = SensorDeviceClass.ILLUMINANCE
    _attr_native_unit_of_measurement = LIGHT_LUX
    _attr_suggested_display_precision = 0

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize an SHC illuminance level reporting sensor."""
        super().__init__(device, entry_id)
        self._attr_unique_id = f"{device.root_device_id}_{device.id}_illuminance"

    @property
    def native_value(self) -> float | None:
        """Return the numeric lux value, or None for non-numeric values."""
        value = self._device.illuminance
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return value
        return None


class BatteryLevelSensor(SHCEntity, SensorEntity):  # type: ignore[misc]
    """Granular battery-level diagnostic sensor (ENUM, all 5 BatteryLevelService states).

    Complements the binary BatterySensor (binary_sensor.py) which only signals
    OK vs. not-OK.  This sensor exposes the raw enum value so automations can
    distinguish LOW_BATTERY from CRITICALLY_LOW_BATTERY.

    #339: this duplicates the binary "Battery" sensor for most users, so it is
    disabled by default (power users can enable it per-entity).
    """

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_options = [
        "ok",
        "low_battery",
        "critical_low",
        "critically_low_battery",
        "not_available",
    ]
    _attr_translation_key = "battery_level"

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize a battery-level sensor."""
        super().__init__(device, entry_id)
        self._attr_unique_id = f"{device.root_device_id}_{device.id}_battery_level"

    @property
    def native_value(self) -> str | None:
        """Return the battery level state string, or None on unknown value."""
        try:
            return str(self._device.batterylevel.value.lower())
        except (ValueError, AttributeError) as err:
            LOGGER.warning("Unknown battery level for %s: %s", self._device.name, err)
            return None


class TwinguardCombinedRatingSensor(SHCEntity, SensorEntity):  # type: ignore[misc]
    """Diagnostic ENUM sensor for Twinguard overall combined air-quality rating.

    Surfaces the combinedRating field from AirQualityLevelService (CAT-3e gap).
    Distinct from AirQualitySensor which exposes the same value as its primary
    state — this entity is diagnostic-only so it does not clutter the default
    device view.  net-new unique_id suffix _combined_rating; no migration needed.
    """

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_options = ["good", "medium", "bad"]
    _attr_translation_key = "combined_rating"

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize a Twinguard combined-rating diagnostic sensor."""
        super().__init__(device, entry_id)
        self._attr_unique_id = f"{device.root_device_id}_{device.id}_combined_rating"

    @property
    def native_value(self) -> str | None:
        """Return the combined rating enum name, or None on unknown value."""
        try:
            return str(self._device.combined_rating.name.lower())
        except (ValueError, AttributeError) as err:
            LOGGER.warning("Unknown combined rating for %s: %s", self._device.name, err)
            return None


class TwinguardDescriptionSensor(SHCEntity, SensorEntity):  # type: ignore[misc]
    """Diagnostic sensor for Twinguard air-quality text description.

    Surfaces the description field from AirQualityLevelService (CAT-3e gap).
    net-new unique_id suffix _description; no migration needed.
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "air_quality_description"

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize a Twinguard air-quality description diagnostic sensor."""
        super().__init__(device, entry_id)
        self._attr_unique_id = f"{device.root_device_id}_{device.id}_description"

    @property
    def native_value(self) -> Any:
        """Return the air quality description string."""
        return self._device.description


class WalkStateSensor(SHCEntity, SensorEntity):  # type: ignore[misc]
    """Sensor for the Motion Detector II walk-test state.

    Reports the current WalkTest walkState enum name (WALK_TEST_STARTED /
    STOPPED / UNKNOWN).  The WalkTest service is optional on MD2 hardware;
    this sensor is only created when walk_state is not None.
    """

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_options = ["walk_test_started", "walk_test_stopped", "unknown"]
    _attr_translation_key = "walk_test_state"

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize the walk-state sensor."""
        super().__init__(device, entry_id)
        self._attr_unique_id = f"{device.root_device_id}_{device.id}_walk_state"

    @property
    def native_value(self) -> str | None:
        """Return the current walk state as its enum name."""
        try:
            val = self._device.walk_state
            if val is None:
                return None
            return str(val.name.lower())
        except (AttributeError, ValueError):
            return None


class DetectionStateSensor(SHCEntity, SensorEntity):  # type: ignore[misc]
    """Sensor for the Motion Detector II detection-test state.

    Reports the DetectionTest detectionState enum name (DETECTION_TEST_STARTED
    / STOPPED / UNKNOWN). The DetectionTest service is the local-API equivalent
    of the APK WalkTest service; created only when the device carries it.
    """

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_options = [
        "detection_test_started",
        "detection_test_stopped",
        "detection_test_unknown",
    ]
    _attr_translation_key = "detection_test_state"

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize the detection-state sensor."""
        super().__init__(device, entry_id)
        self._attr_unique_id = f"{device.root_device_id}_{device.id}_detection_state"

    @property
    def native_value(self) -> str | None:
        """Return the current detection-test state as its enum name."""
        try:
            val = self._device.detection_state
            if val is None:
                return None
            return str(val.name.lower())
        except (AttributeError, ValueError):
            return None


class SirenBatterySensor(SHCEntity, SensorEntity):  # type: ignore[misc]
    """Outdoor Siren battery charge (#120)."""

    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = UnitOfRatio.PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "siren_battery"

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize the outdoor siren battery sensor."""
        super().__init__(device, entry_id)
        self._attr_unique_id = f"{device.root_device_id}_{device.id}_siren_battery"

    @property
    def native_value(self) -> Any:
        """Return the remaining battery percentage."""
        return getattr(self._device.power_supply, "battery_percentage_remaining", None)


class SirenMainPowerSensor(SHCEntity, SensorEntity):  # type: ignore[misc]
    """Outdoor Siren active power source (#120)."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "siren_main_power"
    _attr_options = ["battery", "solar", "v12", "v230", "unknown"]

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize the outdoor siren main power source sensor."""
        super().__init__(device, entry_id)
        self._attr_unique_id = f"{device.root_device_id}_{device.id}_siren_main_power"

    @property
    def native_value(self) -> str | None:
        """Return the active power source as a lowercase slug."""
        try:
            return str(self._device.power_supply.main_power_supply.name.lower())
        except AttributeError:
            return None


class SirenSolarChargingSensor(SHCEntity, SensorEntity):  # type: ignore[misc]
    """Outdoor Siren solar-charging quality (#120)."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:solar-power"
    _attr_translation_key = "siren_solar_charging"
    _attr_options = ["bad", "medium", "good", "unknown"]

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize the outdoor siren solar charging quality sensor."""
        super().__init__(device, entry_id)
        self._attr_unique_id = (
            f"{device.root_device_id}_{device.id}_siren_solar_charging"
        )

    @property
    def native_value(self) -> str | None:
        """Return the solar charging score as a lowercase slug."""
        try:
            return str(self._device.power_supply.solar_charging_score.name.lower())
        except AttributeError:
            return None
