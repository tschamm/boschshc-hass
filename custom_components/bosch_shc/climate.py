"""Platform for climate integration."""
import logging

from boschshcpy import SHCClimateControl, SHCSession
from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import (
    ATTR_HVAC_MODE,
    HVAC_MODE_AUTO,
    HVAC_MODE_HEAT,
    HVAC_MODE_OFF,
    PRESET_BOOST,
    PRESET_ECO,
    PRESET_NONE,
    SUPPORT_PRESET_MODE,
    SUPPORT_TARGET_TEMPERATURE,
)
from homeassistant.const import ATTR_TEMPERATURE, TEMP_CELSIUS

from .const import DATA_SESSION, DOMAIN
from .entity import SHCEntity

_LOGGER = logging.getLogger(__name__)


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
        return TEMP_CELSIUS

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
            return HVAC_MODE_OFF

        if (
            self._device.operation_mode
            == SHCClimateControl.RoomClimateControlService.OperationMode.AUTOMATIC
        ):
            return HVAC_MODE_AUTO

        return HVAC_MODE_HEAT

    @property
    def hvac_modes(self):
        """Return available hvac modes."""
        return [HVAC_MODE_AUTO, HVAC_MODE_HEAT, HVAC_MODE_OFF]

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
    def supported_features(self):
        """Return supported features."""
        return SUPPORT_TARGET_TEMPERATURE + SUPPORT_PRESET_MODE

    def set_temperature(self, **kwargs):
        """Set the temperature."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return

        self.set_hvac_mode(
            kwargs.get(ATTR_HVAC_MODE)
        )  # set_temperature args may provide HVAC mode as well

        if self.hvac_mode == HVAC_MODE_OFF or self.preset_mode == PRESET_ECO:
            _LOGGER.debug(
                "Skipping setting temperature as device %s is off or in low_mode.",
                self.device_name,
            )
            return

        if self.min_temp <= temperature <= self.max_temp:
            self._device.setpoint_temperature = float(temperature)

    def set_hvac_mode(self, hvac_mode: str):
        """Set hvac mode."""
        if hvac_mode not in self.hvac_modes:
            return
        if self.preset_mode == PRESET_ECO:
            return

        if hvac_mode == HVAC_MODE_AUTO:
            self._device.summer_mode = False
            self._device.operation_mode = (
                SHCClimateControl.RoomClimateControlService.OperationMode.AUTOMATIC
            )
        if hvac_mode == HVAC_MODE_HEAT:
            self._device.summer_mode = False
            self._device.operation_mode = (
                SHCClimateControl.RoomClimateControlService.OperationMode.MANUAL
            )
        if hvac_mode == HVAC_MODE_OFF:
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
