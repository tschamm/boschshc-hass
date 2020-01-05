"""Platform for sensor integration."""
from homeassistant.const import TEMP_CELSIUS
from homeassistant.helpers.entity import Entity

from . import DOMAIN

from bshlocal import BSHLocalSession, services_impl
import typing


def setup_platform(hass, config, add_entities, discovery_info=None):
    if discovery_info is None:
        return
    session: BSHLocalSession = hass.data[DOMAIN]["session"]

    entities = []
    for device in session.devices:
        for service in device.device_services:
            if service.id == "TemperatureLevel" and device.name != "-RoomClimateControl-":
                display_name = f"{device.name}"
                unique_id = f"{device.serial}.ShutterContact"
                room_name = session.room(device.room_id).name
                entity = BSHTemperatureSensor(display_name, unique_id, room_name, service)
                entities += [entity]

    add_entities(entities)


class BSHTemperatureSensor(Entity):
    """Representation of a sensor."""

    def __init__(self, name: str, unique_id: str, room_name: str, device_service: services_impl.TemperatureLevelService):
        """Initialize the sensor."""
        self._name = name
        self._unique_id = unique_id
        self._room_name = room_name
        self._device_service = device_service

        assert device_service.on_state_changed is None

        def on_state_changed():
            self.async_schedule_update_ha_state()

        device_service.on_state_changed = on_state_changed

    @property
    def should_poll(self):
        return False

    @property
    def name(self):
        """Return the name of the sensor."""
        return self._name
    
    @property
    def unique_id(self):
        return self._unique_id

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._device_service.temperature

    @property
    def unit_of_measurement(self):
        """Return the unit of measurement."""
        return TEMP_CELSIUS

    @property
    def state_attributes(self) -> typing.Dict[str, typing.Any]:
        state_attr = super().state_attributes
        if state_attr is None:
            state_attr = dict()
        state_attr["bsh_room_name"] = self._room_name
        return state_attr