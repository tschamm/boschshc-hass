"""Platform for climate integration."""
import logging
import math
import typing

from boschshcpy import SHCSession, services_impl

from homeassistant.components.climate import ClimateEntity, const
from homeassistant.const import ATTR_TEMPERATURE, TEMP_CELSIUS

from . import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the climate platform."""

    entities = []
    session: SHCSession = hass.data[DOMAIN][config_entry.entry_id]

    for device in session.device_helper.climate_controls:
        if device.name == "-RoomClimateControl-":
            temperature_level_service = device.device_service("TemperatureLevel")
            room_climate_control_service = device.device_service("RoomClimateControl")
            room_id = device.room_id
            room_name = session.room(room_id).name

            # Need to find all thermostat devices, these are different from the "room climate" devices.
            thermostats = []
            for potential_thermostat in session.device_helper.thermostats:
                if "ValveTappet" not in potential_thermostat.device_service_ids:
                    continue
                if potential_thermostat.room_id != room_id:
                    continue

                thermostats += [potential_thermostat]

            valve_tappet_services = [
                thermostat.device_service("ValveTappet") for thermostat in thermostats
            ]
            display_name = f"Room Climate {room_name}"
            unique_id = f"{device.serial}"

            entity = ClimateControl(
                display_name,
                unique_id,
                room_name,
                temperature_level_service,
                room_climate_control_service,
                valve_tappet_services,
            )
            entities += [entity]

    if entities:
        async_add_entities(entities)


class ClimateControl(ClimateEntity):
    def __init__(
        self,
        name: str,
        unique_id: str,
        room_name: str,
        temperature_level_service: services_impl.TemperatureLevelService,
        room_climate_control_service: services_impl.RoomClimateControlService,
        valve_tappet_services: typing.List[services_impl.ValveTappetService],
    ):

        self._name = name
        self._unique_id = unique_id
        self._room_name = room_name
        self._temperature_level_service = temperature_level_service
        self._room_climate_control_service = room_climate_control_service
        self._valve_tappet_services = valve_tappet_services

    async def async_added_to_hass(self):
        await super().async_added_to_hass()

        def on_state_changed():
            self.schedule_update_ha_state()

        self._temperature_level_service.subscribe_callback(
            self.entity_id, on_state_changed
        )
        self._room_climate_control_service.subscribe_callback(
            self.entity_id, on_state_changed
        )
        for valve_tappet_service in self._valve_tappet_services:
            valve_tappet_service.subscribe_callback(self.entity_id, on_state_changed)

    async def async_will_remove_from_hass(self):
        await super().async_will_remove_from_hass()
        self._temperature_level_service.unsubscribe_callback(self.entity_id)
        self._room_climate_control_service.unsubscribe_callback(self.entity_id)
        for valve_tappet_service in self._valve_tappet_services:
            valve_tappet_service.unsubscribe_callback(self.entity_id)

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
    def temperature_unit(self):
        return TEMP_CELSIUS

    @property
    def current_temperature(self):
        return self._temperature_level_service.temperature

    @property
    def max_temp(self):
        return 30.0

    @property
    def min_temp(self):
        return 5.0

    @property
    def target_temperature(self):
        return self._room_climate_control_service.setpoint_temperature

    @property
    def target_temperature_step(self):
        return 0.5

    @property
    def valve_tappet_position(self):
        total = sum(
            [
                valve_tappet_service.position
                for valve_tappet_service in self._valve_tappet_services
            ]
        )
        if len(self._valve_tappet_services) > 0:
            return min(
                100,
                max(
                    0, int(math.ceil(float(total) / len(self._valve_tappet_services))),
                ),
            )
        else:
            return 0

    @property
    def hvac_mode(self):
        if (
            self._room_climate_control_service.operation_mode
            == services_impl.RoomClimateControlService.OperationMode.AUTOMATIC
        ):
            return const.HVAC_MODE_AUTO
        elif (
            self._room_climate_control_service.operation_mode
            == services_impl.RoomClimateControlService.OperationMode.MANUAL
        ):
            return const.HVAC_MODE_HEAT
        else:
            _LOGGER.warning(
                f"Unknown operation mode! {self._room_climate_control_service.operation_mode} != {services_impl.RoomClimateControlService.OperationMode.MANUAL}"
            )

    @property
    def hvac_modes(self):
        return [const.HVAC_MODE_AUTO, const.HVAC_MODE_HEAT]

    @property
    def hvac_action(self):
        if self.valve_tappet_position > 5:
            return const.CURRENT_HVAC_HEAT
        else:
            return const.CURRENT_HVAC_IDLE

    @property
    def preset_mode(self):
        if self._room_climate_control_service.supports_boost_mode:
            if self._room_climate_control_service.boost_mode:
                return const.PRESET_BOOST

        if self._room_climate_control_service.low:
            return const.PRESET_ECO

        return const.PRESET_NONE

    @property
    def preset_modes(self):
        presets = [const.PRESET_NONE, const.PRESET_ECO]
        if self._room_climate_control_service.supports_boost_mode:
            presets += [const.PRESET_BOOST]
        return presets

    @property
    def supported_features(self):
        return const.SUPPORT_TARGET_TEMPERATURE + const.SUPPORT_PRESET_MODE

    async def async_set_temperature(self, **kwargs):
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return

        if self.min_temp <= temperature <= self.max_temp:
            self._room_climate_control_service.setpoint_temperature = float(temperature)

    async def async_set_hvac_mode(self, hvac_mode: str):
        if hvac_mode not in self.hvac_modes:
            return

        if hvac_mode == const.HVAC_MODE_AUTO:
            self._room_climate_control_service.operation_mode = (
                services_impl.RoomClimateControlService.OperationMode.AUTOMATIC
            )
        elif hvac_mode == const.HVAC_MODE_HEAT:
            self._room_climate_control_service.operation_mode = (
                services_impl.RoomClimateControlService.OperationMode.MANUAL
            )

    async def async_set_preset_mode(self, preset_mode: str):
        if preset_mode not in self.preset_modes:
            return

        if preset_mode == const.PRESET_NONE:
            if self._room_climate_control_service.supports_boost_mode:
                if self._room_climate_control_service.boost_mode:
                    self._room_climate_control_service.boost_mode = False

            if self._room_climate_control_service.low:
                self._room_climate_control_service.low = False

        elif preset_mode == const.PRESET_BOOST:
            if not self._room_climate_control_service.boost_mode:
                self._room_climate_control_service.boost_mode = True

            if self._room_climate_control_service.low:
                self._room_climate_control_service.low = False

        elif preset_mode == const.PRESET_ECO:
            if self._room_climate_control_service.supports_boost_mode:
                if self._room_climate_control_service.boost_mode:
                    self._room_climate_control_service.boost_mode = False

            if not self._room_climate_control_service.low:
                self._room_climate_control_service.low = True

    @property
    def state_attributes(self):
        state_attr = super().state_attributes
        if state_attr is None:
            state_attr = dict()

        state_attr["valve_tappet_position"] = self.valve_tappet_position
        state_attr["boschshc_room_name"] = self._room_name
        return state_attr
