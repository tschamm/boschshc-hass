"""Platform for sensor integration."""
import logging

from boschshcpy import SHCBatteryDevice, SHCSession

from homeassistant.const import (
    CONF_IP_ADDRESS,
    DEVICE_CLASS_BATTERY,
    DEVICE_CLASS_POWER,
    ENERGY_KILO_WATT_HOUR,
    POWER_WATT,
    TEMP_CELSIUS,
    UNIT_PERCENTAGE,
)

from .const import DOMAIN
from .entity import SHCEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the sensor platform."""
    entities = []
    session: SHCSession = hass.data[DOMAIN][config_entry.entry_id]

    for thermostat in session.device_helper.thermostats:
        _LOGGER.debug("Found thermostat: %s (%s)", thermostat.name, thermostat.id)
        entities.append(
            ThermostatSensor(
                device=thermostat,
                room_name=session.room(thermostat.room_id).name,
                controller_ip=config_entry.data[CONF_IP_ADDRESS],
            )
        )

    ip_address = config_entry.data[CONF_IP_ADDRESS]
    entities += get_power_energy_sensor_entities(
        session.device_helper.light_controls, "light control", ip_address, session
    )
    entities += get_power_energy_sensor_entities(
        session.device_helper.smart_plugs, "smart plug", ip_address, session
    )
    entities += get_battery_sensor_entities(
        session.device_helper.smoke_detectors, "smoke detector", ip_address, session
    )
    entities += get_battery_sensor_entities(
        session.device_helper.shutter_contacts, "shutter contact", ip_address, session
    )
    entities += get_battery_sensor_entities(
        session.device_helper.thermostats, "thermostat", ip_address, session
    )

    if entities:
        async_add_entities(entities)


def get_power_energy_sensor_entities(sensors, name, ip_address, session):
    """Return list of initialized entities."""
    entities = []
    for sensor in sensors:
        _LOGGER.debug("Found %s: %s (%s)", name, sensor.name, sensor.id)
        controller_ip = ip_address
        room_name = session.room(sensor.room_id).name
        power_sensor = PowerSensor(sensor, room_name, controller_ip)
        entities += [power_sensor]
        energy_sensor = EnergySensor(sensor, room_name, controller_ip)
        entities += [energy_sensor]
    return entities


def get_battery_sensor_entities(controls, name, ip_address, session):
    """Return list of initialized entities."""
    entities = []
    for sensor in controls:
        _LOGGER.debug("Found %s: %s (%s)", name, sensor.name, sensor.id)
        controller_ip = ip_address
        room_name = session.room(sensor.room_id).name
        battery_sensor = BatterySensor(sensor, room_name, controller_ip)
        entities += [battery_sensor]
    return entities


class ThermostatSensor(SHCEntity):
    """Representation of a SHC temperature reporting sensor."""

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._device.temperature

    @property
    def unit_of_measurement(self):
        """Return the unit of measurement of the sensor."""
        return TEMP_CELSIUS

    @property
    def state_attributes(self):
        """Extend state attribute of the device."""
        state_attr = super().state_attributes
        if state_attr is None:
            state_attr = dict()
        state_attr["valvetappet_position"] = self._device.position
        return state_attr


class PowerSensor(SHCEntity):
    """Representation of a SHC power reporting sensor."""

    @property
    def unique_id(self):
        """Return the unique ID of this sensor."""
        return f"{self._device.serial}_power"

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._device.powerconsumption

    @property
    def device_class(self):
        """Return the class of this device."""
        return DEVICE_CLASS_POWER

    @property
    def unit_of_measurement(self):
        """Return the unit of measurement of the sensor."""
        return POWER_WATT


class EnergySensor(SHCEntity):
    """Representation of a SHC energy reporting sensor."""

    @property
    def unique_id(self):
        """Return the unique ID of this sensor."""
        return f"{self._device.serial}_energy"

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._device.energyconsumption / 1000.0

    @property
    def unit_of_measurement(self):
        """Return the unit of measurement of the sensor."""
        return ENERGY_KILO_WATT_HOUR


class BatterySensor(SHCEntity):
    """Representation of a SHC battery reporting sensor."""

    @property
    def unique_id(self):
        """Return the unique ID of this sensor."""
        return f"{self._device.serial}_battery"

    @property
    def state(self):
        """Return the state of the sensor."""
        if (
            self._device.batterylevel
            == SHCBatteryDevice.BatteryLevelService.State.CRITICAL_LOW
        ):
            logging.warning("Battery state of device %s is critical low.", self.name)
            return 0
        if (
            self._device.batterylevel
            == SHCBatteryDevice.BatteryLevelService.State.LOW_BATTERY
        ):
            return 20
        if self._device.batterylevel == SHCBatteryDevice.BatteryLevelService.State.OK:
            return 100

        return None

    @property
    def device_class(self):
        """Return the class of the sensor."""
        return DEVICE_CLASS_BATTERY

    @property
    def unit_of_measurement(self):
        """Return the unit of measurement of the sensor."""
        return UNIT_PERCENTAGE
