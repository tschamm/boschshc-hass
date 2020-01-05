"""Platform for sensor integration."""
import typing
from homeassistant.helpers.entity import Entity
from homeassistant.components.binary_sensor import DEVICE_CLASS_WINDOW, BinarySensorDevice

from . import DOMAIN

from bshlocal import BSHLocalSession, services_impl


def setup_platform(hass, config, add_entities, discovery_info=None):
    if discovery_info is None:
        return
    session: BSHLocalSession = hass.data[DOMAIN]["session"]

    entities = []
    for device in session.devices:
        for service in device.device_services:
            if service.id == "ShutterContact":
                display_name = f"{device.name}"
                unique_id = f"{device.serial}.TemperatureLevel"
                room_name = session.room(device.room_id).name
                entity = BSHShutterContactBinarySensor(display_name, unique_id, room_name, service)
                entities += [entity]

    add_entities(entities)


class BSHShutterContactBinarySensor(BinarySensorDevice):
    """Representation of a binary sensor."""

    def __init__(self, name: str, unique_id: str, room_name:str, device_service: services_impl.ShutterContactService):
        """Initialize the sensor."""
        self._name = name
        self._unique_id = unique_id
        self._device_service = device_service

        self._room_name = room_name
        
        assert device_service.on_state_changed is None

        def on_state_changed():
            self.async_schedule_update_ha_state()

        device_service.on_state_changed = on_state_changed

    @property
    def should_poll(self):
        return False

    @property
    def device_class(self):
        return DEVICE_CLASS_WINDOW

    @property
    def name(self):
        """Return the name of the sensor."""
        return self._name
        
    @property
    def unique_id(self):
        return self._unique_id

    @property
    def is_on(self):
        """Return the state of the sensor."""
        if self._device_service.value == services_impl.ShutterContactService.State.OPEN:
            return True
        elif self._device_service.value == services_impl.ShutterContactService.State.CLOSED:
            return False
        else:
            return None

    @property
    def state_attributes(self) -> typing.Dict[str, typing.Any]:
        state_attr = super().state_attributes
        if state_attr is None:
            state_attr = dict()
        state_attr["bsh_room_name"] = self._room_name
        return state_attr