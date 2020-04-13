"""Platform for sensor integration."""
import logging

from boschshcpy import SHCSession, SHCSmartPlug, services_impl

from homeassistant.const import (
    CONF_IP_ADDRESS,
    DEVICE_CLASS_POWER,
    ENERGY_KILO_WATT_HOUR,
    POWER_WATT,
    TEMP_CELSIUS,
)
from homeassistant.helpers.entity import Entity

from . import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the sensor platform."""
    entities = []
    session: SHCSession = hass.data[DOMAIN][config_entry.entry_id]

    for device in session.devices:
        for service in device.device_services:
            if (
                service.id == "TemperatureLevel"
                and device.name != "-RoomClimateControl-"
            ):
                display_name = f"{device.name}"
                unique_id = f"{device.serial}"
                room_name = session.room(device.room_id).name
                entity = TemperatureSensor(display_name, unique_id, room_name, service)
                entities += [entity]

    ip_address = config_entry.data[CONF_IP_ADDRESS]
    entities += get_power_energy_sensor_entities(
        session.device_helper.light_controls, "light control", ip_address, session
    )
    entities += get_power_energy_sensor_entities(
        session.device_helper.smart_plugs, "smart plug", ip_address, session
    )

    if entities:
        async_add_entities(entities)


def get_power_energy_sensor_entities(controls, name, ip_address, session):
    """Return list of initialized entities."""
    entities = []
    for light in controls:
        _LOGGER.debug(f"Found {name}: {light.name} ({light.id})")
        controller_ip = ip_address
        room_name = session.room(light.room_id).name
        power_sensor = PowerSensor(controller_ip, room_name, light)
        entities += [power_sensor]
        energy_sensor = EnergySensor(controller_ip, room_name, light)
        entities += [energy_sensor]
    return entities


class TemperatureSensor(Entity):
    """Representation of a SHC temperature reporting sensor."""

    def __init__(
        self,
        name: str,
        unique_id: str,
        room_name: str,
        device_service: services_impl.TemperatureLevelService,
    ):
        """Initialize the SHC device."""
        self._name = name
        self._unique_id = unique_id
        self._room_name = room_name
        self._device_service = device_service

    async def async_added_to_hass(self):
        """Subscribe to SHC events."""
        await super().async_added_to_hass()
        assert self._device_service.on_state_changed is None

        def on_state_changed():
            self.schedule_update_ha_state()

        self._device_service.on_state_changed = on_state_changed

    async def async_will_remove_from_hass(self):
        """Unsubscribe from SHC events."""
        await super().async_will_remove_from_hass()
        self._device_service.on_state_changed = None

    @property
    def should_poll(self):
        """Report polling mode."""
        return False

    @property
    def name(self):
        """Name of the device."""
        return self._name

    @property
    def unique_id(self):
        """Return the unique ID of this sensor."""
        return self._unique_id

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._device_service.temperature

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
        state_attr["boschshc_room_name"] = self._room_name
        return state_attr


class PowerSensor(Entity):
    """Representation of a SHC power reporting sensor."""

    def __init__(
        self, controller_ip: str, room_name: str, device: SHCSmartPlug,
    ):
        """Initialize the SHC device."""
        self._controller_ip = controller_ip
        self._room_name = room_name
        self._device = device

    async def async_added_to_hass(self):
        """Subscribe to SHC events."""
        await super().async_added_to_hass()

        def on_state_changed():
            self.schedule_update_ha_state()

        for service in self._device.device_services:
            service.on_state_changed = on_state_changed

    async def async_will_remove_from_hass(self):
        """Unsubscribe from SHC events."""
        await super().async_will_remove_from_hass()
        for service in self._device.device_services:
            service.on_state_changed = None

    @property
    def should_poll(self):
        """Report polling mode."""
        return False

    @property
    def name(self):
        """Name of the device."""
        return f"{self._device.name} Power"

    @property
    def unique_id(self):
        """Return the unique ID of this sensor."""
        return f"{self._device.serial}_power"

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._device.powerconsumption

    @property
    def device_class(seld):
        """Return the class of this device."""
        return DEVICE_CLASS_POWER

    @property
    def unit_of_measurement(self):
        """Return the unit of measurement of the sensor."""
        return POWER_WATT

    @property
    def device_id(self):
        """Return the ID of this device."""
        return self._device.id

    @property
    def manufacturer(self):
        """Manufacturer of the device."""
        return self._device.manufacturer

    @property
    def device_info(self):
        """Return the device info."""
        return {
            "identifiers": {(DOMAIN, self.device_id)},
            "name": self.name,
            "manufacturer": self.manufacturer,
            "model": self._device.device_model,
            "sw_version": "",
            "via_device": (DOMAIN, self._controller_ip),
        }

    @property
    def state_attributes(self):
        """Extend state attribute of the device."""
        state_attr = super().state_attributes
        if state_attr is None:
            state_attr = dict()
        state_attr["boschshc_room_name"] = self._room_name
        return state_attr


class EnergySensor(Entity):
    """Representation of a SHC energy reporting sensor."""

    def __init__(
        self, controller_ip: str, room_name: str, device: SHCSmartPlug,
    ):
        """Initialize the SHC device."""
        self._controller_ip = controller_ip
        self._room_name = room_name
        self._device = device

    async def async_added_to_hass(self):
        """Subscribe to SHC events."""
        await super().async_added_to_hass()

        def on_state_changed():
            self.schedule_update_ha_state()

        for service in self._device.device_services:
            service.on_state_changed = on_state_changed

    async def async_will_remove_from_hass(self):
        """Unsubscribe from SHC events."""
        await super().async_will_remove_from_hass()
        for service in self._device.device_services:
            service.on_state_changed = None

    @property
    def should_poll(self):
        """Report polling mode."""
        return False

    @property
    def name(self):
        """Name of the device."""
        return f"{self._device.name} Energy"

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

    @property
    def device_id(self):
        """Return the ID of this device."""
        return self._device.id

    @property
    def manufacturer(self):
        """Manufacturer of the device."""
        return self._device.manufacturer

    @property
    def device_info(self):
        """Return the device info."""
        return {
            "identifiers": {(DOMAIN, self.device_id)},
            "name": self.name,
            "manufacturer": self.manufacturer,
            "model": self._device.device_model,
            "sw_version": "",
            "via_device": (DOMAIN, self._controller_ip),
        }

    @property
    def state_attributes(self):
        """Extend state attribute of the device."""
        state_attr = super().state_attributes
        if state_attr is None:
            state_attr = dict()
        state_attr["boschshc_room_name"] = self._room_name
        return state_attr
