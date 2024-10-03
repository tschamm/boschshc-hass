"""Platform for climate integration."""

from boschshcpy import SHCClimateControl, SHCSession
from enum import IntFlag
from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import (
    ATTR_HVAC_MODE,
    HVACMode,
    ClimateEntityFeature,
    PRESET_BOOST,
    PRESET_ECO,
    PRESET_NONE,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature

from .const import DATA_SESSION, DOMAIN, LOGGER
from .entity import SHCEntity


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the SHC climate platform."""
    entities = []
    session: SHCSession = hass.data[DOMAIN][config_entry.entry_id][DATA_SESSION]

    for climate in session.device_helper.climate_controls:
        room_id = climate.room_id
        entities.append(
            ClimateControl(
                device=climate,
                parent_id=session.information.unique_id,
                entry_id=config_entry.entry_id,
                name=f"Room Climate {session.room(room_id).name}",
            )
        )

    if entities:
        async_add_entities(entities)


class ClimateControl(SHCEntity, ClimateEntity):
    """Representation of a SHC room climate control."""

    _attr_target_temperature_step = 0.5
    _attr_supported_features = (
        ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
        | ClimateEntityFeature.PRESET_MODE
        | ClimateEntityFeature.TARGET_TEMPERATURE
    )
    _enable_turn_on_off_backwards_compatibility = False

    def __init__(
        self,
        device: SHCClimateControl,
        parent_id: str,
        name: str,
        entry_id: str,
    ):
        """Initialize the SHC device."""
        super().__init__(device=device, parent_id=parent_id, entry_id=entry_id)
        self._name = name
        self._attr_unique_id = f"{device.root_device_id}_{device.id}"

    @property
    def name(self):
        """Name of the entity."""
        return self._name

    @property
    def device_name(self):
        """Name of the device."""
        return self._name

    @property
    def temperature_unit(self):
        """Return the temperature unit."""
        return UnitOfTemperature.CELSIUS

    @property
    def current_temperature(self):
        """Return the current temperature."""
        return self._device.temperature

    @property
    def max_temp(self):
        """Return the maximum temperature allowed."""
        return 30.0

    @property
    def min_temp(self):
        """Return the minimum temperature allowed."""
        return 5.0

    @property
    def target_temperature(self):
        """Return the target temperature setpoint."""
        return self._device.setpoint_temperature

    @property
    def target_temperature_step(self):
        """Return the temperature step."""
        return 0.5

    @property
    def hvac_mode(self):
        """Return the hvac mode."""
        if self._device.summer_mode:
            return HVACMode.OFF

        if (
            self._device.operation_mode
            == SHCClimateControl.RoomClimateControlService.OperationMode.AUTOMATIC
        ):
            return HVACMode.AUTO

        return HVACMode.HEAT

    @property
    def hvac_modes(self):
        """Return available hvac modes."""
        return [HVACMode.AUTO, HVACMode.HEAT, HVACMode.OFF]

    # @property
    # def hvac_action(self):
    #     if self.valve_tappet_position > 5:
    #         return CURRENT_HVAC_HEAT
    #     else:
    #         return CURRENT_HVAC_IDLE

    @property
    def preset_mode(self):
        """Return preset mode."""
        if self._device.supports_boost_mode:
            if self._device.boost_mode:
                return PRESET_BOOST

        if self._device.low:
            return PRESET_ECO

        return PRESET_NONE

    @property
    def preset_modes(self):
        """Return available preset modes."""
        presets = [PRESET_NONE, PRESET_ECO]
        if self._device.supports_boost_mode:
            presets += [PRESET_BOOST]
        return presets

    @property
    def supported_features(self) -> ClimateEntityFeature:
        """Return supported features."""
        return ClimateEntityFeature(
            ClimateEntityFeature.TARGET_TEMPERATURE + ClimateEntityFeature.PRESET_MODE
        )

    def set_temperature(self, **kwargs):
        """Set the temperature."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return

        self.set_hvac_mode(
            kwargs.get(ATTR_HVAC_MODE)
        )  # set_temperature args may provide HVAC mode as well

        if self.hvac_mode == HVACMode.OFF or self.preset_mode == PRESET_ECO:
            LOGGER.debug(
                "Skipping setting temperature as device %s is off or in low_mode.",
                self.device_name,
            )
            return

        if self.min_temp <= temperature <= self.max_temp:
            self._device.setpoint_temperature = float(round(temperature * 2.0) / 2.0)

    def set_hvac_mode(self, hvac_mode: str):
        """Set hvac mode."""
        if hvac_mode not in self.hvac_modes:
            return
        if self.preset_mode == PRESET_ECO:
            return

        if hvac_mode == HVACMode.AUTO:
            self._device.summer_mode = False
            self._device.operation_mode = (
                SHCClimateControl.RoomClimateControlService.OperationMode.AUTOMATIC
            )
        if hvac_mode == HVACMode.HEAT:
            self._device.summer_mode = False
            self._device.operation_mode = (
                SHCClimateControl.RoomClimateControlService.OperationMode.MANUAL
            )
        if hvac_mode == HVACMode.OFF:
            self._device.summer_mode = True

    def set_preset_mode(self, preset_mode: str):
        """Set preset mode."""
        if preset_mode not in self.preset_modes:
            return

        if preset_mode == PRESET_NONE:
            if self._device.supports_boost_mode:
                if self._device.boost_mode:
                    self._device.boost_mode = False

            if self._device.low:
                self._device.low = False

        elif preset_mode == PRESET_BOOST:
            if not self._device.boost_mode:
                self._device.boost_mode = True

            if self._device.low:
                self._device.low = False

        elif preset_mode == PRESET_ECO:
            if self._device.supports_boost_mode:
                if self._device.boost_mode:
                    self._device.boost_mode = False

            if not self._device.low:
                self._device.low = True
