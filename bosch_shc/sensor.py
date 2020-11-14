"""Platform for sensor integration."""
import logging

from boschshcpy import SHCBatteryDevice, SHCSession

from homeassistant.const import (
    DEVICE_CLASS_BATTERY,
    DEVICE_CLASS_POWER,
    ENERGY_KILO_WATT_HOUR,
    POWER_WATT,
    TEMP_CELSIUS,
    PERCENTAGE,
)

from .const import DOMAIN
from .entity import SHCEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the sensor platform."""
    entities = []
    session: SHCSession = hass.data[DOMAIN][config_entry.entry_id]

    for sensor in session.device_helper.thermostats:
        room_name = session.room(sensor.room_id).name
        entities.append(
            TemperatureSensor(
                device=sensor, room_name=room_name, shc_uid=session.information.name
            )
        )
        entities.append(
            BatterySensor(
                device=sensor, room_name=room_name, shc_uid=session.information.name
            )
        )

    for sensor in session.device_helper.wallthermostats:
        room_name = session.room(sensor.room_id).name
        entities.append(
            TemperatureSensor(
                device=sensor, room_name=room_name, shc_uid=session.information.name
            )
        )
        entities.append(
            HumiditySensor(
                device=sensor, room_name=room_name, shc_uid=session.information.name
            )
        )
        entities.append(
            BatterySensor(
                device=sensor, room_name=room_name, shc_uid=session.information.name
            )
        )

    for sensor in session.device_helper.twinguards:
        room_name = session.room(sensor.room_id).name
        entities.append(
            TemperatureSensor(
                device=sensor, room_name=room_name, shc_uid=session.information.name
            )
        )
        entities.append(
            HumiditySensor(
                device=sensor, room_name=room_name, shc_uid=session.information.name
            )
        )
        entities.append(
            BatterySensor(
                device=sensor, room_name=room_name, shc_uid=session.information.name
            )
        )

    for sensor in session.device_helper.light_controls:
        room_name = session.room(sensor.room_id).name
        entities.append(
            PowerSensor(
                device=sensor, room_name=room_name, shc_uid=session.information.name
            )
        )
        entities.append(
            EnergySensor(
                device=sensor, room_name=room_name, shc_uid=session.information.name
            )
        )

    for sensor in session.device_helper.smart_plugs:
        room_name = session.room(sensor.room_id).name
        entities.append(
            PowerSensor(
                device=sensor, room_name=room_name, shc_uid=session.information.name
            )
        )
        entities.append(
            EnergySensor(
                device=sensor, room_name=room_name, shc_uid=session.information.name
            )
        )

    for sensor in session.device_helper.smoke_detectors:
        room_name = session.room(sensor.room_id).name
        entities.append(
            BatterySensor(
                device=sensor, room_name=room_name, shc_uid=session.information.name
            )
        )

    for sensor in session.device_helper.shutter_contacts:
        room_name = session.room(sensor.room_id).name
        entities.append(
            BatterySensor(
                device=sensor, room_name=room_name, shc_uid=session.information.name
            )
        )

    if entities:
        async_add_entities(entities)


class TemperatureSensor(SHCEntity):
    """Representation of a SHC temperature reporting sensor."""

    @property
    def unique_id(self):
        """Return the unique ID of this sensor."""
        return f"{self._device.serial}_temperature"

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._device.temperature

    @property
    def unit_of_measurement(self):
        """Return the unit of measurement of the sensor."""
        return TEMP_CELSIUS


class HumiditySensor(SHCEntity):
    """Representation of a SHC humidity reporting sensor."""

    @property
    def unique_id(self):
        """Return the unique ID of this sensor."""
        return f"{self._device.serial}_humidity"

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._device.humidity

    @property
    def unit_of_measurement(self):
        """Return the unit of measurement of the sensor."""
        return PERCENTAGE


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
        return PERCENTAGE
