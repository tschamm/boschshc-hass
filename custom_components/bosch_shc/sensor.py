"""Platform for sensor integration."""

from __future__ import annotations

from boschshcpy import SHCSession
from boschshcpy.device import SHCDevice

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONCENTRATION_PARTS_PER_MILLION,
    LIGHT_LUX,
    PERCENTAGE,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
    Platform,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DATA_SESSION, DOMAIN, LOGGER, OPT_DIAGNOSTIC_ENTITIES
from .entity import SHCEntity, async_migrate_to_new_unique_id


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the SHC sensor platform."""
    entities: list[SensorEntity] = []
    session: SHCSession = hass.data[DOMAIN][config_entry.entry_id][DATA_SESSION]
    sensor: SHCDevice
    diagnostic_enabled = config_entry.options.get(OPT_DIAGNOSTIC_ENTITIES, True)

    for sensor in session.device_helper.thermostats:
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

    for sensor in (
        session.device_helper.wallthermostats + session.device_helper.roomthermostats
    ):
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

    for sensor in session.device_helper.twinguards:
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

    for sensor in (
        session.device_helper.smart_plugs
        + session.device_helper.light_switches_bsm
        + session.device_helper.micromodule_light_controls
        + session.device_helper.micromodule_shutter_controls
        + session.device_helper.micromodule_blinds
    ):
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

    for sensor in session.device_helper.smart_plugs_compact:
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
        entities.append(
            IlluminanceLevelSensor(
                device=sensor,
                entry_id=config_entry.entry_id,
            )
        )

    for sensor in session.device_helper.motion_detectors2:
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

    sensor = session.emma
    entities.append(
        EmmaPowerSensor(
            device=sensor,
            entry_id=config_entry.entry_id,
        )
    )

    if entities:
        async_add_entities(entities)


class TemperatureSensor(SHCEntity, SensorEntity):
    """Representation of an SHC temperature reporting sensor."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize an SHC temperature reporting sensor."""
        super().__init__(device, entry_id)
        self._attr_name = "Temperature"
        self._attr_unique_id = f"{device.root_device_id}_{device.id}_temperature"

    @property
    def native_value(self):
        """Return the state of the sensor."""
        return self._device.temperature


class HumiditySensor(SHCEntity, SensorEntity):
    """Representation of an SHC humidity reporting sensor."""

    _attr_device_class = SensorDeviceClass.HUMIDITY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize an SHC humidity reporting sensor."""
        super().__init__(device, entry_id)
        self._attr_name = "Humidity"
        self._attr_unique_id = f"{device.root_device_id}_{device.id}_humidity"

    @property
    def native_value(self):
        """Return the state of the sensor."""
        return self._device.humidity


class PuritySensor(SHCEntity, SensorEntity):
    """Representation of an SHC purity reporting sensor."""

    _attr_icon = "mdi:molecule-co2"
    _attr_device_class = SensorDeviceClass.CO2
    _attr_native_unit_of_measurement = CONCENTRATION_PARTS_PER_MILLION
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize an SHC purity reporting sensor."""
        super().__init__(device, entry_id)
        self._attr_name = "Purity"
        self._attr_unique_id = f"{device.root_device_id}_{device.id}_purity"

    @property
    def native_value(self):
        """Return the state of the sensor."""
        return self._device.purity


class AirQualitySensor(SHCEntity, SensorEntity):
    """Representation of an SHC airquality reporting sensor."""

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize an SHC airquality reporting sensor."""
        super().__init__(device, entry_id)
        self._attr_name = "Air Quality"
        self._attr_unique_id = f"{device.root_device_id}_{device.id}_airquality"

    @property
    def native_value(self):
        """Return the state of the sensor."""
        try:
            return self._device.combined_rating.name
        except ValueError as err:
            LOGGER.warning("Unknown combined rating for %s: %s", self._device.name, err)
            return None

    @property
    def extra_state_attributes(self):
        """Return the state attributes."""
        return {
            "rating_description": self._device.description,
        }


class TemperatureRatingSensor(SHCEntity, SensorEntity):
    """Representation of an SHC temperature rating sensor."""

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize an SHC temperature rating sensor."""
        super().__init__(device, entry_id)
        self._attr_name = "Temperature Rating"
        self._attr_unique_id = f"{device.root_device_id}_{device.id}_temperaturerating"

    @property
    def native_value(self):
        """Return the state of the sensor."""
        try:
            return self._device.temperature_rating.name
        except ValueError as err:
            LOGGER.warning(
                "Unknown temperature rating for %s: %s", self._device.name, err
            )
            return None


class CommunicationQualitySensor(SHCEntity, SensorEntity):
    """Representation of an SHC communication quality reporting sensor."""

    _attr_icon = "mdi:wifi"

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize an SHC communication quality reporting sensor."""
        super().__init__(device, entry_id)
        self._attr_name = "Communication Quality"
        self._attr_unique_id = (
            f"{device.root_device_id}_{device.id}_communicationquality"
        )

    @property
    def native_value(self):
        """Return the state of the sensor."""
        try:
            return self._device.communicationquality.name
        except (ValueError, AttributeError) as err:
            LOGGER.warning(
                "Unknown communication quality for %s: %s", self._device.name, err
            )
            return None


class HumidityRatingSensor(SHCEntity, SensorEntity):
    """Representation of an SHC humidity rating sensor."""

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize an SHC humidity rating sensor."""
        super().__init__(device, entry_id)
        self._attr_name = "Humidity Rating"
        self._attr_unique_id = f"{device.root_device_id}_{device.id}_humidityrating"

    @property
    def native_value(self):
        """Return the state of the sensor."""
        try:
            return self._device.humidity_rating.name
        except ValueError as err:
            LOGGER.warning(
                "Unknown humidity rating for %s: %s", self._device.name, err
            )
            return None


class PurityRatingSensor(SHCEntity, SensorEntity):
    """Representation of an SHC purity rating sensor."""

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize an SHC purity rating sensor."""
        super().__init__(device, entry_id)
        self._attr_name = "Purity Rating"
        self._attr_unique_id = f"{device.root_device_id}_{device.id}_purityrating"

    @property
    def native_value(self):
        """Return the state of the sensor."""
        try:
            return self._device.purity_rating.name
        except ValueError as err:
            LOGGER.warning(
                "Unknown purity rating for %s: %s", self._device.name, err
            )
            return None


class PowerSensor(SHCEntity, SensorEntity):
    """Representation of an SHC power reporting sensor."""

    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize an SHC power reporting sensor."""
        super().__init__(device, entry_id)
        self._attr_name = "Power"
        self._attr_unique_id = f"{device.root_device_id}_{device.id}_power"

    @property
    def native_value(self):
        """Return the state of the sensor."""
        return self._device.powerconsumption


class EmmaPowerSensor(SHCEntity, SensorEntity):
    """Representation of an SHC power reporting sensor."""

    from boschshcpy import SHCEmma

    _attr_entity_registry_enabled_default = False
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, device: SHCEmma, entry_id: str) -> None:
        """Initialize an SHC power reporting sensor."""
        super().__init__(device, entry_id)
        self._attr_name = "Power"
        self._attr_unique_id = f"{device.root_device_id}_{device.id}_power"

    async def async_added_to_hass(self):
        """Subscribe to SHC events."""
        await super().async_added_to_hass()

        def update_entity_information():
            self.schedule_update_ha_state()

        self._device.subscribe_callback(self.entity_id, update_entity_information)

    async def async_will_remove_from_hass(self):
        """Unsubscribe from SHC events."""
        await super().async_will_remove_from_hass()
        self._device.unsubscribe_callback(self.entity_id)

    @property
    def native_value(self):
        """Return the state of the sensor. Negative value if power is consumed from the grid, positive if fed to the grid."""
        return self._device.value

    @property
    def extra_state_attributes(self):
        """Return the state attributes."""
        return {
            "power_flow": self._device.localizedSubtitles,
        }


class EnergySensor(SHCEntity, SensorEntity):
    """Representation of an SHC energy reporting sensor."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize an SHC energy reporting sensor."""
        super().__init__(device, entry_id)
        self._attr_name = "Energy"
        self._attr_unique_id = f"{device.root_device_id}_{self._device.id}_energy"

    @property
    def native_value(self):
        """Return the state of the sensor."""
        return self._device.energyconsumption / 1000.0


class ValveTappetSensor(SHCEntity, SensorEntity):
    """Representation of an SHC valve tappet reporting sensor."""

    _attr_icon = "mdi:gauge"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize an SHC valve tappet reporting sensor."""
        super().__init__(device, entry_id)
        self._attr_name = "Valve Tappet"
        self._attr_unique_id = f"{device.root_device_id}_{device.id}_valvetappet"

    @property
    def native_value(self):
        """Return the state of the sensor."""
        return self._device.position

    @property
    def extra_state_attributes(self):
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


class IlluminanceLevelSensor(SHCEntity, SensorEntity):
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

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize an SHC illuminance level reporting sensor."""
        super().__init__(device, entry_id)
        self._attr_name = "Illuminance"
        self._attr_unique_id = f"{device.root_device_id}_{device.id}_illuminance"

    @property
    def native_value(self):
        """Return the numeric lux value, or None for non-numeric values."""
        value = self._device.illuminance
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return value
        return None
