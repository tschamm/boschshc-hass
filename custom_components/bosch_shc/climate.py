"""Platform for climate integration."""

from __future__ import annotations

from typing import Any

from boschshcpy import (
    HeatingCircuitService,
    RoomClimateControlService,
    SHCClimateControl,
    SHCHeatingCircuit,
    SHCSession,
)
from boschshcpy.exceptions import JSONRPCError, SHCException
from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import (
    ATTR_HVAC_MODE,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DATA_SESSION, DOMAIN, LOGGER
from .entity import SHCEntity, device_excluded

PARALLEL_UPDATES = 1

# Preset mode strings — transient override states only.
# #334: AUTOMATIC is back as HVACMode.AUTO (green card color).
# AUTO and MANUAL are no longer presets — they are expressed via hvac_mode.
# Only boost and eco remain as presets (override states on top of the hvac_mode axis).
PRESET_BOOST = "boost"
PRESET_ECO = "eco"


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the SHC climate platform."""
    entities: list[Any] = []
    session: SHCSession = hass.data[DOMAIN][config_entry.entry_id][DATA_SESSION]

    for climate in session.device_helper.climate_controls:
        if device_excluded(climate, config_entry.options):
            continue
        room_id = climate.room_id
        try:
            room_name = session.room(room_id).name
        except KeyError:
            room_name = climate.name
        entities.append(
            ClimateControl(
                device=climate,
                entry_id=config_entry.entry_id,
                name=room_name,
            )
        )

    for heating_circuit in session.device_helper.heating_circuits:
        if device_excluded(heating_circuit, config_entry.options):
            continue
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


class ClimateControl(SHCEntity, ClimateEntity):  # type: ignore[misc]
    """Representation of a SHC room climate control.

    #334 rework: AUTOMATIC is HVACMode.AUTO so the HA thermostat card renders
    green (not red) while the schedule is running — matching the HeatingCircuit
    pattern.  COOL remains gated on supports_cooling (field-presence).
    Presets are now override-only: boost (if supportsBoostMode) and eco
    (if supports_low).  AUTO and MANUAL are expressed directly as
    HVACMode.AUTO / HVACMode.HEAT.

    hvac_mode axis:
      summer_mode=True                              → OFF
      supports_cooling=True + cooling_mode=True    → COOL
      operation_mode=AUTOMATIC                      → AUTO
      otherwise (MANUAL)                            → HEAT

    preset_mode axis (override states only):
      boost_mode=True   → "boost"   (only if supportsBoostMode)
      low=True          → "eco"     (only if supports_low)
      otherwise         → None / HA default

    hvac_modes exposed:
      [AUTO, HEAT, (COOL if supports_cooling), OFF]

    preset_modes exposed:
      [boost] if supports_boost_mode, [eco] if supports_low.
      If none, PRESET_MODE feature is not advertised.
    """

    _attr_target_temperature_step = 0.5
    _enable_turn_on_off_backwards_compatibility = False
    _attr_translation_key = "room_climate_control"

    def __init__(
        self,
        device: SHCClimateControl,
        name: str,
        entry_id: str,
    ) -> None:
        """Initialize the SHC device."""
        super().__init__(device=device, entry_id=entry_id)
        # Device name = room name (e.g. "Arbeitszimmer").
        # Entity name comes from translation_key "room_climate_control" in strings.json
        # (e.g. "Raumklima" / "Room climate control"), so the friendly name is
        # "<room> Raumklima" — no doubling. _attr_name = None lets HA resolve
        # the name from the translation_key.
        self._room_label = name
        self._attr_name = None
        self._attr_unique_id = f"{device.root_device_id}_{device.id}"

    @property
    def device_name(self) -> str:
        """Name of the device."""
        return self._room_label

    @property
    def temperature_unit(self) -> str:
        """Return the temperature unit."""
        return UnitOfTemperature.CELSIUS  # type: ignore[no-any-return]

    @property
    def current_temperature(self) -> float | None:
        """Return the current temperature."""
        return self._device.temperature  # type: ignore[no-any-return]

    @property
    def max_temp(self) -> float:
        """Return the maximum temperature allowed."""
        return 30.0

    @property
    def min_temp(self) -> float:
        """Return the minimum temperature allowed."""
        return 5.0

    @property
    def target_temperature(self) -> float | None:
        """Return the target temperature setpoint."""
        return self._device.setpoint_temperature  # type: ignore[no-any-return]

    @property
    def target_temperature_step(self) -> float:
        """Return the temperature step."""
        return 0.5

    @property
    def hvac_mode(self) -> HVACMode:
        """Return the hvac mode.

        Maps the Bosch state fields onto HA hvac_mode:
          summer_mode=True                              → OFF
          supports_cooling=True + cooling_mode=True    → COOL
          operation_mode=AUTOMATIC                      → AUTO  (#334)
          otherwise (MANUAL)                            → HEAT
        """
        if self._device.summer_mode:
            return HVACMode.OFF

        if self._device.supports_cooling and self._device.cooling_mode:
            return HVACMode.COOL

        if (
            self._device.operation_mode
            == RoomClimateControlService.OperationMode.AUTOMATIC
        ):
            return HVACMode.AUTO

        return HVACMode.HEAT

    @property
    def hvac_modes(self) -> list[HVACMode]:
        """Return available hvac modes.

        AUTO is always present (RoomClimateControl always supports AUTOMATIC).
        COOL only when supports_cooling — gated on the room's
        ThermostatSupportedControlMode capability (#334), with the
        roomControlMode field-presence heuristic as the firmware fallback.
        """
        modes = [HVACMode.AUTO, HVACMode.HEAT]
        if self._device.supports_cooling:
            modes.append(HVACMode.COOL)
        modes.append(HVACMode.OFF)
        return modes

    @property
    def hvac_action(self) -> HVACAction:
        """Return the current HVAC action."""
        if self.hvac_mode == HVACMode.OFF:
            return HVACAction.OFF
        if (
            self._device.supports_cooling
            and self._device.cooling_mode
            and self.hvac_mode == HVACMode.COOL
        ):
            return HVACAction.COOLING
        # getattr guard: has_demand needs boschshcpy >= 0.2.120; tolerate older libs
        return (
            HVACAction.HEATING
            if getattr(self._device, "has_demand", False)
            else HVACAction.IDLE
        )

    @property
    def preset_mode(self) -> str | None:
        """Return the current preset mode (transient overrides only).

        boost_mode=True  → "boost"  (only if device supports boost)
        low=True         → "eco"    (only if supports_eco)
        otherwise        → None
        """
        if self._device.supports_boost_mode and self._device.boost_mode:
            return PRESET_BOOST

        # #334 / jumlu #68: gate eco on supports_eco (presence of the eco
        # SETPOINT field), NOT supports_low. SHC-II floor-heating rooms carry
        # low=False without an eco model, so supports_low wrongly reports eco
        # there; supports_eco keys off setpointTemperatureForLevelEco — the only
        # reliable signal the ECO/COMFORT level model is implemented.
        if getattr(self._device, "supports_eco", False) and getattr(
            self._device, "low", False
        ):
            return PRESET_ECO

        return None

    @property
    def preset_modes(self) -> list[str] | None:
        """Return available preset modes.

        #334: auto/manual removed — they are hvac_modes now.
        Only transient overrides remain: boost (if supported) and eco (if supported).
        Returns None when no presets are available (PRESET_MODE feature not set).
        """
        presets = []
        if self._device.supports_boost_mode:
            presets.append(PRESET_BOOST)
        # #334 / jumlu #68: eco only when the eco-setpoint model exists
        # (supports_eco), not merely when the "low" field is present.
        if getattr(self._device, "supports_eco", False):
            presets.append(PRESET_ECO)
        return presets or None

    @property
    def supported_features(self) -> ClimateEntityFeature:
        """Return supported features.

        PRESET_MODE is only advertised when the device actually has presets.
        """
        features = (
            ClimateEntityFeature.TARGET_TEMPERATURE
            | ClimateEntityFeature.TURN_OFF
            | ClimateEntityFeature.TURN_ON
        )
        if self.preset_modes:
            features |= ClimateEntityFeature.PRESET_MODE
        return features

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set the temperature."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return

        # P2-B: call async_set_hvac_mode BEFORE the OFF guard so that a
        # combined temperature+mode call can change the mode first.
        requested_hvac_mode = kwargs.get(ATTR_HVAC_MODE)  # type: ignore[assignment]
        # set_temperature args may provide HVAC mode as well
        hvac_mode_write_succeeded = await self._async_apply_hvac_mode(
            requested_hvac_mode  # type: ignore[arg-type]
        )

        # boschshcpy's async_put_state_element() only awaits the HTTP PUT — it
        # never updates the local device cache, which only refreshes on the
        # next long-poll notification. So self.hvac_mode right after the
        # await above can still read the PRE-write value. When the caller
        # explicitly requested a mode AND that write actually succeeded (e.g.
        # set_temperature(hvac_mode="heat", temperature=21) on a device that
        # was OFF), trust the requested mode instead of re-reading the stale
        # cache — otherwise the OFF guard below fires on stale
        # summer_mode=True and silently drops the temperature write even
        # though async_set_hvac_mode just turned heating back on. If the
        # write FAILED (caught JSONRPCError/SHCException, logged inside
        # _async_apply_hvac_mode), fall back to the real cached state rather
        # than trusting a mode change that never actually applied — otherwise
        # a failed mode write masks itself behind a second, more confusing
        # "failed to set temperature" warning from the setpoint write below.
        effective_hvac_mode = (
            requested_hvac_mode
            if requested_hvac_mode is not None and hvac_mode_write_succeeded
            else self.hvac_mode
        )

        if effective_hvac_mode == HVACMode.OFF:
            LOGGER.debug(
                "Skipping setting temperature as device %s is off.",
                self.device_name,
            )
            return

        if requested_hvac_mode == HVACMode.AUTO and hvac_mode_write_succeeded:
            # Bosch rejects a setpoint write while in AUTOMATIC (schedule) mode.
            # When the caller explicitly requested AUTO and that write
            # succeeded, honour the mode change and return — the schedule
            # controls the temperature. Deliberately keyed on the REQUESTED
            # mode (not effective_hvac_mode) and gated on success: a bare
            # temperature call (no explicit hvac_mode) on a device that's
            # currently AUTOMATIC must still fall through to the
            # AUTOMATIC→MANUAL-then-write logic below, not bail out here; and
            # a failed AUTO write must not silently drop the temperature
            # request either.
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
                # SHC rejects a setpoint write while operationMode=AUTOMATIC
                # (HTTP 400 WRONG_THERMOSTAT_GROUP_MODE).  For a bare
                # set_temperature (no hvac_mode given, e.g. from a script) drop
                # to MANUAL first — matching the Bosch app.  Gated on no explicit
                # hvac_mode so a combined set_temperature(hvac_mode=auto) is not
                # overridden; kept inside the try + range branch so a failed mode
                # write is caught and an out-of-range value can't cancel the
                # schedule. #73 #180
                if (
                    kwargs.get(ATTR_HVAC_MODE) is None
                    and self._device.operation_mode
                    == RoomClimateControlService.OperationMode.AUTOMATIC
                ):
                    await self._device.async_set_operation_mode(
                        RoomClimateControlService.OperationMode.MANUAL
                    )
                await self._device.async_set_setpoint_temperature(
                    float(round(temperature * 2.0) / 2.0)
                )
            except (JSONRPCError, SHCException) as err:
                LOGGER.warning(
                    "Failed to set temperature on device %s: %s",
                    self.device_name,
                    err,
                )

    async def async_set_hvac_mode(self, hvac_mode: str) -> None:
        """Set hvac mode.

        #334: AUTO sets operationMode=AUTOMATIC; HEAT sets MANUAL (+ clears cooling).
        COOL sets cooling_mode=True; OFF sets summer_mode=True.
        ECO (low) is cleared first when active so HVAC writes are never blocked. #196
        """
        await self._async_apply_hvac_mode(hvac_mode)

    async def _async_apply_hvac_mode(self, hvac_mode: str | None) -> bool:
        """Write hvac_mode to the device; return whether it actually applied.

        Split out from async_set_hvac_mode so async_set_temperature can tell
        a successful mode change apart from a silently-caught write failure
        (JSONRPCError/SHCException) or a no-op (mode not in hvac_modes) —
        trusting a mode that was never actually applied would mask the real
        failure behind a second, more confusing setpoint-write error.

        Returns True when hvac_mode is None (nothing requested — not a
        failure), False when hvac_mode isn't a supported mode (no-op) or a
        write raised, True when all writes succeeded.
        """
        if hvac_mode is None:
            return True
        if hvac_mode not in self.hvac_modes:
            return False

        try:
            # Exit ECO (low) before applying any HVAC mode change so that
            # turn_off / mode changes are never silently no-oped. #196
            if self.preset_mode == PRESET_ECO:
                await self._device.async_set_low(False)

            if hvac_mode == HVACMode.AUTO:
                await self._device.async_set_summer_mode(False)
                if self._device.supports_cooling:
                    await self._device.async_set_cooling_mode(False)
                await self._device.async_set_operation_mode(
                    RoomClimateControlService.OperationMode.AUTOMATIC
                )
            elif hvac_mode == HVACMode.HEAT:
                await self._device.async_set_summer_mode(False)
                if self._device.supports_cooling:
                    await self._device.async_set_cooling_mode(False)
                await self._device.async_set_operation_mode(
                    RoomClimateControlService.OperationMode.MANUAL
                )
            elif hvac_mode == HVACMode.COOL:
                await self._device.async_set_summer_mode(False)
                await self._device.async_set_cooling_mode(True)
            elif hvac_mode == HVACMode.OFF:
                if self._device.supports_cooling:
                    await self._device.async_set_cooling_mode(False)
                await self._device.async_set_summer_mode(True)
        except (JSONRPCError, SHCException) as err:
            LOGGER.warning(
                "Failed to set HVAC mode on device %s: %s",
                self.device_name,
                err,
            )
            return False
        return True

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set preset mode (transient override states only).

        "boost"  → boost_mode=True
        "eco"    → low=True  (only if device exposes `low`)
        """
        available = self.preset_modes or []
        if preset_mode not in available:
            return

        try:
            if preset_mode == PRESET_BOOST:
                await self._device.async_set_boost_mode(True)

            elif preset_mode == PRESET_ECO:
                if hasattr(self._device, "low"):
                    # Clear boost first so states don't stack
                    if self._device.supports_boost_mode and self._device.boost_mode:
                        await self._device.async_set_boost_mode(False)
                    await self._device.async_set_low(True)

        except (JSONRPCError, SHCException) as err:
            LOGGER.warning(
                "Failed to set preset mode on device %s: %s",
                self.device_name,
                err,
            )

    async def async_turn_on(self) -> None:
        """Turn the climate device on."""
        if self.hvac_mode == HVACMode.OFF:
            await self.async_set_hvac_mode(HVACMode.AUTO)

    async def async_turn_off(self) -> None:
        """Turn the climate device off."""
        if self.hvac_mode != HVACMode.OFF:
            await self.async_set_hvac_mode(HVACMode.OFF)


class HeatingCircuit(SHCEntity, ClimateEntity):  # type: ignore[misc]
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
        self._attr_name: str | None = name  # type: ignore[assignment]
        self._attr_unique_id = f"{device.root_device_id}_{device.id}"

    @property
    def current_temperature(self) -> float | None:
        """Heating circuits expose no measured temperature."""
        return None

    @property
    def target_temperature(self) -> float | None:
        """Return the setpoint temperature."""
        return self._device.setpoint_temperature  # type: ignore[no-any-return]

    @property
    def hvac_mode(self) -> HVACMode:
        """Return the hvac mode derived from the operation mode."""
        if self._device.operation_mode == HeatingCircuitService.OperationMode.AUTOMATIC:
            return HVACMode.AUTO
        return HVACMode.HEAT

    @property
    def hvac_action(self) -> HVACAction:
        """Return whether the circuit is currently heating."""
        return HVACAction.HEATING if self._device.on else HVACAction.IDLE

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set a new setpoint temperature."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        if self.min_temp <= temperature <= self.max_temp:
            try:
                await self._device.async_set_setpoint_temperature(
                    float(round(temperature * 2.0) / 2.0)
                )
            except (JSONRPCError, SHCException) as err:
                LOGGER.warning(
                    "Failed to set temperature on HeatingCircuit %s: %s",
                    self._attr_unique_id,
                    err,
                )

    async def async_set_hvac_mode(self, hvac_mode: str) -> None:
        """Set the operation mode."""
        if hvac_mode not in self.hvac_modes:
            return
        mode = (
            HeatingCircuitService.OperationMode.AUTOMATIC
            if hvac_mode == HVACMode.AUTO
            else HeatingCircuitService.OperationMode.MANUAL
        )
        try:
            await self._device.async_set_operation_mode(mode)
        except (JSONRPCError, SHCException) as err:
            LOGGER.warning(
                "Failed to set HVAC mode on HeatingCircuit %s: %s",
                self._attr_unique_id,
                err,
            )
