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
    PRESET_NONE,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature

from .const import DATA_SESSION, DOMAIN, LOGGER
from .entity import SHCEntity

PARALLEL_UPDATES = 1


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

    # DEFERRED (#253, #242): TRV I (model "TRV") and TRV_GEN2 (SHCThermostat)
    # are not added as climate entities here.  Both devices lack a direct
    # setpoint-write API; temperature is controlled at room level via the
    # RoomClimateControl virtual device above.  Adding per-TRV climate entities
    # requires an architectural decision (one entity per TRV vs. room-level only)
    # and possibly a lib change to expose the room association.  Tracked in #253
    # and #242 — do not implement without that design decision.

    if entities:
        async_add_entities(entities)


class ClimateControl(SHCEntity, ClimateEntity):
    """Representation of a SHC room climate control."""

    _attr_target_temperature_step = 0.5
    _enable_turn_on_off_backwards_compatibility = False

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
        """Return the hvac mode."""
        if self._device.summer_mode:
            return HVACMode.OFF

        if self._device.supports_cooling and self._device.cooling_mode:
            return HVACMode.COOL

        if (
            self._device.operation_mode
            == SHCClimateControl.RoomClimateControlService.OperationMode.AUTOMATIC
        ):
            return HVACMode.AUTO

        return HVACMode.HEAT

    @property
    def hvac_modes(self):
        """Return available hvac modes."""
        modes = [HVACMode.AUTO, HVACMode.HEAT, HVACMode.OFF]
        if self._device.supports_cooling:
            modes.append(HVACMode.COOL)
        return modes

    @property
    def hvac_action(self):
        """Return the current HVAC action."""
        if self.hvac_mode == HVACMode.OFF:
            return HVACAction.OFF
        # getattr guard: has_demand needs boschshcpy >= 0.2.120; tolerate older libs
        return (
            HVACAction.HEATING
            if getattr(self._device, "has_demand", False)
            else HVACAction.IDLE
        )

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

        # P2-B: call async_set_hvac_mode BEFORE the ECO/OFF guard so that a
        # combined temperature+mode call from ECO state can exit ECO first
        # (async_set_hvac_mode clears low=False when in ECO; the device cache
        # reflects the change immediately after the await).
        await self.async_set_hvac_mode(
            kwargs.get(ATTR_HVAC_MODE)
        )  # set_temperature args may provide HVAC mode as well

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
        """Set hvac mode.

        #196: ECO (low mode) and HVAC mode are independent state fields on the
        SHC.  The old guard (return-if-ECO) silently blocked turn_off from ECO,
        leaving the device stuck.  We now exit ECO first so the HVAC write always
        proceeds.
        """
        if hvac_mode not in self.hvac_modes:
            return

        try:
            # Exit ECO (low) before applying any HVAC mode change so that
            # turn_off / mode changes are never silently no-oped. #196
            if self.preset_mode == PRESET_ECO:
                await self.hass.async_add_executor_job(
                    setattr, self._device, "low", False
                )

            if hvac_mode == HVACMode.AUTO:
                await self.hass.async_add_executor_job(
                    setattr, self._device, "summer_mode", False
                )
                if self._device.supports_cooling:
                    await self.hass.async_add_executor_job(
                        setattr, self._device, "cooling_mode", False
                    )
                await self.hass.async_add_executor_job(
                    setattr,
                    self._device,
                    "operation_mode",
                    SHCClimateControl.RoomClimateControlService.OperationMode.AUTOMATIC,
                )
            if hvac_mode == HVACMode.HEAT:
                await self.hass.async_add_executor_job(
                    setattr, self._device, "summer_mode", False
                )
                if self._device.supports_cooling:
                    await self.hass.async_add_executor_job(
                        setattr, self._device, "cooling_mode", False
                    )
                await self.hass.async_add_executor_job(
                    setattr,
                    self._device,
                    "operation_mode",
                    SHCClimateControl.RoomClimateControlService.OperationMode.MANUAL,
                )
            if hvac_mode == HVACMode.COOL:
                # P2-C: also set operation_mode=MANUAL to match Bosch app
                # behaviour and avoid a stale AUTOMATIC operationMode when
                # cooling_mode is active.
                await self.hass.async_add_executor_job(
                    setattr, self._device, "summer_mode", False
                )
                await self.hass.async_add_executor_job(
                    setattr, self._device, "cooling_mode", True
                )
                await self.hass.async_add_executor_job(
                    setattr,
                    self._device,
                    "operation_mode",
                    SHCClimateControl.RoomClimateControlService.OperationMode.MANUAL,
                )
            if hvac_mode == HVACMode.OFF:
                if self._device.supports_cooling:
                    await self.hass.async_add_executor_job(
                        setattr, self._device, "cooling_mode", False
                    )
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
        """Set preset mode."""
        if preset_mode not in self.preset_modes:
            return

        try:
            if preset_mode == PRESET_NONE:
                if self._device.supports_boost_mode:
                    if self._device.boost_mode:
                        await self.hass.async_add_executor_job(
                            setattr, self._device, "boost_mode", False
                        )

                if self._device.low:
                    await self.hass.async_add_executor_job(
                        setattr, self._device, "low", False
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
                if self._device.supports_boost_mode:
                    if self._device.boost_mode:
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
        try:
            await self.hass.async_add_executor_job(
                setattr, self._device, "operation_mode", mode
            )
        except (JSONRPCError, SHCException) as err:
            LOGGER.warning(
                "Failed to set HVAC mode on HeatingCircuit %s: %s",
                self._attr_unique_id,
                err,
            )
