from bshlocal import BSHLocalSession, services_impl

from homeassistant.const import TEMP_CELSIUS
from homeassistant.helpers.entity import Entity

from . import DOMAIN


def setup_platform(hass, config, add_entities, discovery_info=None):
    if discovery_info is None:
        return
    session: BSHLocalSession = hass.data[DOMAIN]["session"]

    entities = []
    for device in session.devices:
        for service in device.device_services:
            if (
                service.id == "TemperatureLevel"
                and device.name != "-RoomClimateControl-"
            ):
                display_name = f"{device.name}"
                unique_id = f"{device.serial}.ShutterContact"
                room_name = session.room(device.room_id).name
                entity = BSHTemperatureSensor(
                    display_name, unique_id, room_name, service
                )
                entities += [entity]

    add_entities(entities)


class BSHTemperatureSensor(Entity):
    def __init__(
        self,
        name: str,
        unique_id: str,
        room_name: str,
        device_service: services_impl.TemperatureLevelService,
    ):
        self._name = name
        self._unique_id = unique_id
        self._room_name = room_name
        self._device_service = device_service

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        assert self._device_service.on_state_changed is None

        def on_state_changed():
            self.schedule_update_ha_state()

        self._device_service.on_state_changed = on_state_changed

    async def async_will_remove_from_hass(self):
        await super().async_will_remove_from_hass()
        self._device_service.on_state_changed = None

    @property
    def should_poll(self):
        return False

    @property
    def name(self):
        return self._name

    @property
    def unique_id(self):
        return self._unique_id

    @property
    def state(self):
        return self._device_service.temperature

    @property
    def unit_of_measurement(self):
        return TEMP_CELSIUS

    @property
    def state_attributes(self):
        state_attr = super().state_attributes
        if state_attr is None:
            state_attr = dict()
        state_attr["bsh_room_name"] = self._room_name
        return state_attr
