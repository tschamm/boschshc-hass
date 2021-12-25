"""Platform for sensor integration."""
from boschshcpy import SHCSession, SHCDevice

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.const import (
    CONCENTRATION_PARTS_PER_MILLION,
    ENERGY_KILO_WATT_HOUR,
    PERCENTAGE,
    POWER_WATT,
    TEMP_CELSIUS,
)

from .const import DATA_SESSION, DOMAIN
from .entity import SHCEntity


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the SHC sensor platform."""
    entities = []
    session: SHCSession = hass.data[DOMAIN][config_entry.entry_id][DATA_SESSION]

    for sensor in session.device_helper.thermostats:
        entities.append(
            TemperatureSensor(
                device=sensor,
                parent_id=session.information.unique_id,
                entry_id=config_entry.entry_id,
            )
        )
        entities.append(
            ValveTappetSensor(
                device=sensor,
                parent_id=session.information.unique_id,
                entry_id=config_entry.entry_id,
            )
        )

    for sensor in session.device_helper.wallthermostats:
        entities.append(
            TemperatureSensor(
                device=sensor,
                parent_id=session.information.unique_id,
                entry_id=config_entry.entry_id,
            )
        )
        entities.append(
            HumiditySensor(
                device=sensor,
                parent_id=session.information.unique_id,
                entry_id=config_entry.entry_id,
            )
        )

    for sensor in session.device_helper.twinguards:
        entities.append(
            TemperatureSensor(
                device=sensor,
                parent_id=session.information.unique_id,
                entry_id=config_entry.entry_id,
            )
        )
        entities.append(
            HumiditySensor(
                device=sensor,
                parent_id=session.information.unique_id,
                entry_id=config_entry.entry_id,
            )
        )
        entities.append(
            PuritySensor(
                device=sensor,
                parent_id=session.information.unique_id,
                entry_id=config_entry.entry_id,
            )
        )
        entities.append(
            AirQualitySensor(
                device=sensor,
                parent_id=session.information.unique_id,
                entry_id=config_entry.entry_id,
            )
        )
        entities.append(
            TemperatureRatingSensor(
                device=sensor,
                parent_id=session.information.unique_id,
                entry_id=config_entry.entry_id,
            )
        )
        entities.append(
            HumidityRatingSensor(
                device=sensor,
                parent_id=session.information.unique_id,
                entry_id=config_entry.entry_id,
            )
        )
        entities.append(
            PurityRatingSensor(
                device=sensor,
                parent_id=session.information.unique_id,
                entry_id=config_entry.entry_id,
            )
        )

    for sensor in session.device_helper.smart_plugs:
        entities.append(
            PowerSensor(
                device=sensor,
                parent_id=session.information.unique_id,
                entry_id=config_entry.entry_id,
            )
        )
        entities.append(
            EnergySensor(
                device=sensor,
                parent_id=session.information.unique_id,
                entry_id=config_entry.entry_id,
            )
        )

    for sensor in session.device_helper.smart_plugs_compact:
        entities.append(
            PowerSensor(
                device=sensor,
                parent_id=session.information.unique_id,
                entry_id=config_entry.entry_id,
            )
        )
        entities.append(
            EnergySensor(
                device=sensor,
                parent_id=session.information.unique_id,
                entry_id=config_entry.entry_id,
            )
        )

    if entities:
        async_add_entities(entities)


class TemperatureSensor(SHCEntity, SensorEntity):
    """Representation of a SHC temperature reporting sensor."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = TEMP_CELSIUS

    def __init__(self, device: SHCDevice, parent_id: str, entry_id: str) -> None:
        """Initialize an SHC temperature reporting sensor."""
        super().__init__(device, parent_id, entry_id)
        self._attr_name = f"{device.name} Temperature"
        self._attr_unique_id = f"{device.serial}_temperature"

    @property
    def native_value(self):
        """Return the state of the sensor."""
        return self._device.temperature


class HumiditySensor(SHCEntity, SensorEntity):
    """Representation of a SHC humidity reporting sensor."""

    _attr_device_class = SensorDeviceClass.HUMIDITY
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(self, device: SHCDevice, parent_id: str, entry_id: str) -> None:
        """Initialize an SHC humidity reporting sensor."""
        super().__init__(device, parent_id, entry_id)
        self._attr_name = f"{device.name} Humidity"
        self._attr_unique_id = f"{device.serial}_humidity"

    @property
    def native_value(self):
        """Return the state of the sensor."""
        return self._device.humidity


class PuritySensor(SHCEntity, SensorEntity):
    """Representation of a SHC purity reporting sensor."""

    _attr_native_unit_of_measurement = CONCENTRATION_PARTS_PER_MILLION
    _attr_icon = "mdi:molecule-co2"

    def __init__(self, device: SHCDevice, parent_id: str, entry_id: str) -> None:
        """Initialize an SHC humidity reporting sensor."""
        super().__init__(device, parent_id, entry_id)
        self._attr_name = f"{device.name} Purity"
        self._attr_unique_id = f"{device.serial}_purity"

    @property
    def native_value(self):
        """Return the state of the sensor."""
        return self._device.purity


class AirQualitySensor(SHCEntity, SensorEntity):
    """Representation of a SHC airquality reporting sensor."""

    def __init__(self, device: SHCDevice, parent_id: str, entry_id: str) -> None:
        """Initialize an SHC humidity reporting sensor."""
        super().__init__(device, parent_id, entry_id)
        self._attr_name = f"{device.name} Air Quality"
        self._attr_unique_id = f"{device.serial}_airquality"
        self._attr_entity_category = "diagnostic"

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
    """Representation of a SHC temperature rating sensor."""

    def __init__(self, device: SHCDevice, parent_id: str, entry_id: str) -> None:
        """Initialize an SHC humidity reporting sensor."""
        super().__init__(device, parent_id, entry_id)
        self._attr_name = f"{device.name} Temperature Rating"
        self._attr_unique_id = f"{device.serial}_temperature_rating"
        self._attr_entity_category = "diagnostic"

    @property
    def native_value(self):
        """Return the state of the sensor."""
        return self._device.temperature_rating.name


class HumidityRatingSensor(SHCEntity, SensorEntity):
    """Representation of a SHC humidity rating sensor."""

    def __init__(self, device: SHCDevice, parent_id: str, entry_id: str) -> None:
        """Initialize an SHC humidity reporting sensor."""
        super().__init__(device, parent_id, entry_id)
        self._attr_name = f"{device.name} Humidity Rating"
        self._attr_unique_id = f"{device.serial}_humidity_rating"
        self._attr_entity_category = "diagnostic"

    @property
    def native_value(self):
        """Return the state of the sensor."""
        return self._device.humidity_rating.name


class PurityRatingSensor(SHCEntity, SensorEntity):
    """Representation of a SHC purity rating sensor."""

    def __init__(self, device: SHCDevice, parent_id: str, entry_id: str) -> None:
        """Initialize an SHC humidity reporting sensor."""
        super().__init__(device, parent_id, entry_id)
        self._attr_name = f"{device.name} Purity Rating"
        self._attr_unique_id = f"{device.serial}_purity_rating"
        self._attr_entity_category = "diagnostic"

    @property
    def native_value(self):
        """Return the state of the sensor."""
        return self._device.purity_rating.name


class PowerSensor(SHCEntity, SensorEntity):
    """Representation of a SHC power reporting sensor."""

    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = POWER_WATT
    _attr_state_class = "measurement"

    def __init__(self, device: SHCDevice, parent_id: str, entry_id: str) -> None:
        """Initialize an SHC humidity reporting sensor."""
        super().__init__(device, parent_id, entry_id)
        self._attr_name = f"{device.name} Power"
        self._attr_unique_id = f"{device.serial}_power"
        self._attr_entity_category = "diagnostic"

    @property
    def native_value(self):
        """Return the state of the sensor."""
        return self._device.powerconsumption


class EnergySensor(SHCEntity, SensorEntity):
    """Representation of a SHC energy reporting sensor."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = ENERGY_KILO_WATT_HOUR
    _attr_state_class = "total_increasing"

    def __init__(self, device: SHCDevice, parent_id: str, entry_id: str) -> None:
        """Initialize an SHC humidity reporting sensor."""
        super().__init__(device, parent_id, entry_id)
        self._attr_name = f"{device.name} Energy"
        self._attr_unique_id = f"{device.serial}_energy"
        self._attr_entity_category = "diagnostic"

    @property
    def native_value(self):
        """Return the state of the sensor."""
        return self._device.energyconsumption / 1000.0


class ValveTappetSensor(SHCEntity, SensorEntity):
    """Representation of a SHC valve tappet reporting sensor."""

    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_icon = "mdi:gauge"

    def __init__(self, device: SHCDevice, parent_id: str, entry_id: str) -> None:
        """Initialize an SHC humidity reporting sensor."""
        super().__init__(device, parent_id, entry_id)
        self._attr_name = f"{device.name} Valvetappet"
        self._attr_unique_id = f"{device.serial}_valvetappet"
        self._attr_entity_category = "diagnostic"

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
