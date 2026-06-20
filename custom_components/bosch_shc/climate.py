"""Platform for climate integration."""

from boschshcpy import SHCClimateControl, SHCHeatingCircuit, SHCSession
from boschshcpy.exceptions import JSONRPCError, SHCException
from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import (
    ATTR_HVAC_MODE,
    HVACAction,
    HVACMode,
    ClimateEntityFeature,
    PRESET_BOOST,
    PRESET_ECO,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature

from .const import DATA_SESSION, DOMAIN, LOGGER
from .entity import SHCEntity

# Bosch separates two orthogonal axes that HA's single hvac_mode enum cannot
# express together:
#   * direction : HEATING / COOLING   (roomControlMode)  -> hvac_mode heat/cool
#   * regulation: AUTOMATIC / MANUAL  (operationMode)    -> preset auto/manual
# We therefore map the regulation axis onto preset_mode. The eco ("low") and
# boost overrides remain additional presets that take display precedence while
# active but do not destroy the underlying auto/manual regulation in the SHC.
PRESET_AUTO = "auto"
PRESET_MANUAL = "manual"


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the SHC climate platform."""
    entities = []
    session: SHCSession = hass.data[DOMAIN][config_entry.entry_id][DATA_SESSION]

    for climate in session.device_helper.climate_controls:
        room_id = climate.room_id
        entities.append(
            ClimateControl(
                device=climate,
                entry_id=config_entry.entry_id,
                name=f"Room Climate {session.room(room_id).name}",
            )
        )

    for heating_circuit in session.device_helper.heating_circuits:
        entities.append(
            HeatingCircuit(
                device=heating_circuit,
                entry_id=config_entry.entry_id,
                name=heating_circuit.name,
            )
        )

    if entities:
        async_add_entities(entities)


class ClimateControl(SHCEntity, ClimateEntity):
    """Representation of a SHC room climate control."""

    _attr_target_temperature_step = 0.5
    _enable_turn_on_off_backwards_compatibility = False
    _attr_translation_key = "room_climate"

    def __init__(
        self,
        device: SHCClimateControl,
        name: str,
        entry_id: str,
    ):
        """Initialize the SHC device."""
        super().__init__(device=device, entry_id=entry_id)
        # _attr_has_entity_name is True (set on SHCEntity base).
        # Climate represents a room — use the room/circuit name as the feature label.
        self._attr_name = name
        self._attr_unique_id = f"{device.root_device_id}_{device.id}"

    @property
    def device_name(self):
        """Name of the device."""
        return self._attr_name

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
        """Return the hvac mode (direction axis only: heat / cool / off)."""
        if self._device.summer_mode:
            return HVACMode.OFF

        if self._device.supports_cooling and self._device.cooling_mode:
            return HVACMode.COOL

        return HVACMode.HEAT

    @property
    def hvac_modes(self):
        """Return available hvac modes."""
        modes = [HVACMode.HEAT, HVACMode.OFF]
        if self._device.supports_cooling:
            modes.append(HVACMode.COOL)
        return modes

    @property
    def hvac_action(self):
        """Return the current HVAC action."""
        if self.hvac_mode == HVACMode.OFF:
            return HVACAction.OFF
        # getattr guard: has_demand needs boschshcpy >= 0.2.120; tolerate older libs
        if not getattr(self._device, "has_demand", False):
            return HVACAction.IDLE
        if self._device.supports_cooling and self._device.cooling_mode:
            return HVACAction.COOLING
        return HVACAction.HEATING

    @property
    def preset_mode(self):
        """Return preset mode.

        The preset carries the regulation axis (auto / manual). The eco and
        boost overrides take display precedence while active, since they are
        temporary states the user typically wants to see and clear.
        """
        if self._device.supports_boost_mode and self._device.boost_mode:
            return PRESET_BOOST

        if self._device.low:
            return PRESET_ECO

        if (
            self._device.operation_mode
            == SHCClimateControl.RoomClimateControlService.OperationMode.AUTOMATIC
        ):
            return PRESET_AUTO

        return PRESET_MANUAL

    @property
    def preset_modes(self):
        """Return available preset modes."""
        presets = [PRESET_AUTO, PRESET_MANUAL, PRESET_ECO]
        if self._device.supports_boost_mode:
            presets += [PRESET_BOOST]
        return presets

    @property
    def supported_features(self) -> ClimateEntityFeature:
        """Return supported features."""
        return (
            ClimateEntityFeature.TARGET_TEMPERATURE
            | ClimateEntityFeature.PRESET_MODE
            | ClimateEntityFeature.TURN_OFF
            | ClimateEntityFeature.TURN_ON
        )

    async def async_set_temperature(self, **kwargs):
        """Set the temperature."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return

        hvac_mode = kwargs.get(ATTR_HVAC_MODE)
        if hvac_mode is not None:
            # set_temperature args may provide HVAC mode as well
            await self.async_set_hvac_mode(hvac_mode)

        if self.hvac_mode == HVACMode.OFF or self.preset_mode == PRESET_ECO:
            LOGGER.debug(
                "Skipping setting temperature as device %s is off or in low_mode.",
                self.device_name,
            )
            return

        if self.preset_mode == PRESET_BOOST:
            LOGGER.warning(
                "Cannot set temperature on device %s while in BOOST mode "
                "(SHC rejects setpoint writes in this state).",
                self.device_name,
            )
            return

        if self.min_temp <= temperature <= self.max_temp:
            try:
                await self.hass.async_add_executor_job(
                    setattr,
                    self._device,
                    "setpoint_temperature",
                    float(round(temperature * 2.0) / 2.0),
                )
            except (JSONRPCError, SHCException) as err:
                LOGGER.warning(
                    "Failed to set temperature on device %s: %s",
                    self.device_name,
                    err,
                )

    async def async_set_hvac_mode(self, hvac_mode: str):
        """Set hvac mode (direction axis only).

        Heating/cooling direction is orthogonal to the auto/manual regulation,
        which is handled via preset_mode. Changing direction must therefore not
        touch operation_mode, otherwise cooling+auto / heating+auto would be
        impossible to express.
        """
        if hvac_mode not in self.hvac_modes:
            return
        if self.preset_mode == PRESET_ECO:
            return

        try:
            if hvac_mode == HVACMode.HEAT:
                await self.hass.async_add_executor_job(
                    setattr, self._device, "summer_mode", False
                )
                if self._device.supports_cooling and self._device.cooling_mode:
                    await self.hass.async_add_executor_job(
                        setattr, self._device, "cooling_mode", False
                    )
            elif hvac_mode == HVACMode.COOL:
                if not self._device.supports_cooling:
                    LOGGER.warning(
                        "Device %s does not support cooling.", self.device_name
                    )
                    return
                await self.hass.async_add_executor_job(
                    setattr, self._device, "summer_mode", False
                )
                if not self._device.cooling_mode:
                    await self.hass.async_add_executor_job(
                        setattr, self._device, "cooling_mode", True
                    )
            elif hvac_mode == HVACMode.OFF:
                await self.hass.async_add_executor_job(
                    setattr, self._device, "summer_mode", True
                )
        except (JSONRPCError, SHCException) as err:
            LOGGER.warning(
                "Failed to set HVAC mode on device %s: %s",
                self.device_name,
                err,
            )

    async def async_set_preset_mode(self, preset_mode: str):
        """Set preset mode.

        auto / manual write the regulation axis (operation_mode) and clear any
        eco/boost override. eco / boost set their override on top of the current
        regulation without changing it, so returning to auto/manual restores the
        previous behaviour.
        """
        if preset_mode not in self.preset_modes:
            return

        OperationMode = (
            SHCClimateControl.RoomClimateControlService.OperationMode
        )

        try:
            if preset_mode in (PRESET_AUTO, PRESET_MANUAL):
                if self._device.supports_boost_mode and self._device.boost_mode:
                    await self.hass.async_add_executor_job(
                        setattr, self._device, "boost_mode", False
                    )
                if self._device.low:
                    await self.hass.async_add_executor_job(
                        setattr, self._device, "low", False
                    )
                target = (
                    OperationMode.AUTOMATIC
                    if preset_mode == PRESET_AUTO
                    else OperationMode.MANUAL
                )
                if self._device.operation_mode != target:
                    await self.hass.async_add_executor_job(
                        setattr, self._device, "operation_mode", target
                    )

            elif preset_mode == PRESET_BOOST:
                if not self._device.boost_mode:
                    await self.hass.async_add_executor_job(
                        setattr, self._device, "boost_mode", True
                    )
                if self._device.low:
                    await self.hass.async_add_executor_job(
                        setattr, self._device, "low", False
                    )

            elif preset_mode == PRESET_ECO:
                if self._device.supports_boost_mode and self._device.boost_mode:
                    await self.hass.async_add_executor_job(
                        setattr, self._device, "boost_mode", False
                    )
                if not self._device.low:
                    await self.hass.async_add_executor_job(
                        setattr, self._device, "low", True
                    )
        except (JSONRPCError, SHCException) as err:
            LOGGER.warning(
                "Failed to set preset mode on device %s: %s",
                self.device_name,
                err,
            )

    async def async_turn_on(self) -> None:
        """Turn the climate device on."""
        if self.hvac_mode == HVACMode.OFF:
            await self.async_set_hvac_mode(HVACMode.HEAT)

    async def async_turn_off(self) -> None:
        """Turn the climate device off."""
        if self.hvac_mode != HVACMode.OFF:
            await self.async_set_hvac_mode(HVACMode.OFF)


class HeatingCircuit(SHCEntity, ClimateEntity):
    """Representation of a SHC heating circuit.

    The HeatingCircuit service exposes a setpoint temperature and an operation
    mode (AUTOMATIC/MANUAL); there is no measured room temperature and the on
    state is read-only, so this maps to a HEAT/AUTO climate entity with a
    heating/idle action and no OFF mode.
    """

    _attr_target_temperature_step = 0.5
    _enable_turn_on_off_backwards_compatibility = False
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_max_temp = 30.0
    _attr_min_temp = 5.0
    _attr_hvac_modes = [HVACMode.AUTO, HVACMode.HEAT]
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE

    def __init__(
        self,
        device: SHCHeatingCircuit,
        name: str,
        entry_id: str,
    ) -> None:
        """Initialize the SHC heating circuit."""
        super().__init__(device=device, entry_id=entry_id)
        self._attr_unique_id = f"{device.root_device_id}_{device.id}"

    @property
    def current_temperature(self):
        """Heating circuits expose no measured temperature."""
        return None

    @property
    def target_temperature(self):
        """Return the setpoint temperature."""
        return self._device.setpoint_temperature

    @property
    def hvac_mode(self):
        """Return the hvac mode derived from the operation mode."""
        if (
            self._device.operation_mode
            == SHCHeatingCircuit.HeatingCircuitService.OperationMode.AUTOMATIC
        ):
            return HVACMode.AUTO
        return HVACMode.HEAT

    @property
    def hvac_action(self):
        """Return whether the circuit is currently heating."""
        return HVACAction.HEATING if self._device.on else HVACAction.IDLE

    async def async_set_temperature(self, **kwargs):
        """Set a new setpoint temperature."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        if self.min_temp <= temperature <= self.max_temp:
            try:
                await self.hass.async_add_executor_job(
                    setattr,
                    self._device,
                    "setpoint_temperature",
                    float(round(temperature * 2.0) / 2.0),
                )
            except (JSONRPCError, SHCException) as err:
                LOGGER.warning(
                    "Failed to set temperature on HeatingCircuit %s: %s",
                    self._attr_unique_id,
                    err,
                )

    async def async_set_hvac_mode(self, hvac_mode: str):
        """Set the operation mode."""
        if hvac_mode not in self.hvac_modes:
            return
        mode = (
            SHCHeatingCircuit.HeatingCircuitService.OperationMode.AUTOMATIC
            if hvac_mode == HVACMode.AUTO
            else SHCHeatingCircuit.HeatingCircuitService.OperationMode.MANUAL
        )
        await self.hass.async_add_executor_job(
            setattr, self._device, "operation_mode", mode
        )
