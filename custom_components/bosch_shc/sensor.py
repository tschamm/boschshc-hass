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
    PERCENTAGE,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DATA_SESSION, DOMAIN
from .entity import SHCEntity, async_migrate_to_new_unique_id


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the SHC sensor platform."""
    entities: list[SensorEntity] = []
    session: SHCSession = hass.data[DOMAIN][config_entry.entry_id][DATA_SESSION]

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
        self._attr_name = f"{device.name} Temperature"
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
        self._attr_name = f"{device.name} Humidity"
        self._attr_unique_id = f"{device.root_device_id}_{device.id}_humidity"

    @property
    def native_value(self):
        """Return the state of the sensor."""
        return self._device.humidity


class PuritySensor(SHCEntity, SensorEntity):
    """Representation of an SHC purity reporting sensor."""

    _attr_icon = "mdi:molecule-co2"
    _attr_native_unit_of_measurement = CONCENTRATION_PARTS_PER_MILLION
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize an SHC purity reporting sensor."""
        super().__init__(device, entry_id)
        self._attr_name = f"{device.name} Purity"
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
        self._attr_name = f"{device.name} AirQuality"
        self._attr_unique_id = f"{device.root_device_id}_{device.id}_airquality"

    @property
    def native_value(self):
        """Return the state of the sensor."""
        return self._device.combined_rating.name

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
        self._attr_name = f"{device.name} TemperatureRating"
        self._attr_unique_id = f"{device.root_device_id}_{device.id}_temperaturerating"

    @property
    def native_value(self):
        """Return the state of the sensor."""
        return self._device.temperature_rating.name


class CommunicationQualitySensor(SHCEntity, SensorEntity):
    """Representation of an SHC communication quality reporting sensor."""

    _attr_icon = "mdi:wifi"

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize an SHC communication quality reporting sensor."""
        super().__init__(device, entry_id)
        self._attr_name = f"{device.name} Communication Quality"
        self._attr_unique_id = (
            f"{device.root_device_id}_{device.id}_communicationquality"
        )

    @property
    def native_value(self):
        """Return the state of the sensor."""
        return self._device.communicationquality.name


class HumidityRatingSensor(SHCEntity, SensorEntity):
    """Representation of an SHC humidity rating sensor."""

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize an SHC humidity rating sensor."""
        super().__init__(device, entry_id)
        self._attr_name = f"{device.name} Humidity Rating"
        self._attr_unique_id = f"{device.root_device_id}_{device.id}_humidityrating"

    @property
    def native_value(self):
        """Return the state of the sensor."""
        return self._device.humidity_rating.name


class PurityRatingSensor(SHCEntity, SensorEntity):
    """Representation of an SHC purity rating sensor."""

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize an SHC purity rating sensor."""
        super().__init__(device, entry_id)
        self._attr_name = f"{device.name} Purity Rating"
        self._attr_unique_id = f"{device.root_device_id}_{device.id}_purityrating"

    @property
    def native_value(self):
        """Return the state of the sensor."""
        return self._device.purity_rating.name


class PowerSensor(SHCEntity, SensorEntity):
    """Representation of an SHC power reporting sensor."""

    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize an SHC power reporting sensor."""
        super().__init__(device, entry_id)
        self._attr_name = f"{device.name} Power"
        self._attr_unique_id = f"{device.root_device_id}_{device.id}_power"

    @property
    def native_value(self):
        """Return the state of the sensor."""
        return self._device.powerconsumption


class EnergySensor(SHCEntity, SensorEntity):
    """Representation of an SHC energy reporting sensor."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize an SHC energy reporting sensor."""
        super().__init__(device, entry_id)
        self._attr_name = f"{self._device.name} Energy"
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
        self._attr_name = f"{device.name} Valvetappet"
        self._attr_unique_id = f"{device.root_device_id}_{device.id}_valvetappet"

    @property
    def native_value(self):
        """Return the state of the sensor."""
        return self._device.position

    @property
    def extra_state_attributes(self):
        """Return the state attributes."""
        return {
            "valve_tappet_state": self._device.valvestate.name,
        }


class IlluminanceLevelSensor(SHCEntity, SensorEntity):
    """Representation of an SHC illuminance level reporting sensor."""

    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize an SHC illuminance level reporting sensor."""
        super().__init__(device, entry_id)
        self._attr_name = f"{device.name} Illuminance"
        self._attr_unique_id = f"{device.root_device_id}_{device.id}_illuminance"

    @property
    def native_value(self):
        """Return the state of the sensor."""
        return self._device.illuminance
