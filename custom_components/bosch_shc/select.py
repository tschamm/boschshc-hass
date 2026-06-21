"""Platform for select integration."""

from __future__ import annotations

import logging

from boschshcpy import (
    SHCSession,
    SHCShutterContact2Plus,
)
from boschshcpy.services_impl import (
    PirSensorConfigurationService,
    VibrationSensorService,
)
from boschshcpy.device import SHCDevice

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DATA_SESSION, DOMAIN
from .entity import SHCEntity, device_excluded

LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 1

# Motion sensitivity: exclude UNKNOWN from user-visible options.
_MOTION_SENSITIVITY_OPTIONS = [
    PirSensorConfigurationService.MotionSensitivity.HIGH.name,
    PirSensorConfigurationService.MotionSensitivity.MIDDLE.name,
    PirSensorConfigurationService.MotionSensitivity.LOW.name,
]

# Vibration sensitivity: all values are valid user choices (no UNKNOWN).
_VIBRATION_SENSITIVITY_OPTIONS = [
    VibrationSensorService.SensitivityState.VERY_HIGH.name,
    VibrationSensorService.SensitivityState.HIGH.name,
    VibrationSensorService.SensitivityState.MEDIUM.name,
    VibrationSensorService.SensitivityState.LOW.name,
    VibrationSensorService.SensitivityState.VERY_LOW.name,
]


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the SHC select platform."""
    entities: list[SelectEntity] = []
    session: SHCSession = hass.data[DOMAIN][config_entry.entry_id][DATA_SESSION]

    for device in session.device_helper.motion_detectors2:
        if device_excluded(device, config_entry.options):
            continue
        if not hasattr(device, "motion_sensitivity"):
            continue
        try:
            # Probe the accessor — raises AttributeError when the service is absent.
            _ = device.motion_sensitivity
        except AttributeError:
            continue
        entities.append(
            MotionSensitivitySelect(
                device=device,
                entry_id=config_entry.entry_id,
            )
        )

    for device in session.device_helper.shutter_contacts2:
        if device_excluded(device, config_entry.options):
            continue
        if not isinstance(device, SHCShutterContact2Plus):
            continue
        entities.append(
            VibrationSensitivitySelect(
                device=device,
                entry_id=config_entry.entry_id,
            )
        )

    if entities:
        async_add_entities(entities)


class MotionSensitivitySelect(SHCEntity, SelectEntity):
    """Select entity for Motion Detector II [+M] motion sensitivity."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = _MOTION_SENSITIVITY_OPTIONS

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize the motion sensitivity select entity."""
        super().__init__(device, entry_id)
        self._attr_name = "Motion Sensitivity"
        self._attr_unique_id = (
            f"{device.root_device_id}_{device.id}_motion_sensitivity"
        )

    @property
    def current_option(self) -> str | None:
        """Return the current sensitivity option."""
        try:
            return self._device.motion_sensitivity.name
        except (AttributeError, ValueError) as err:
            LOGGER.warning(
                "Unknown motion_sensitivity for %s: %s", self._device.name, err
            )
            return None

    async def async_select_option(self, option: str) -> None:
        """Set the motion sensitivity."""
        MotionSensitivity = PirSensorConfigurationService.MotionSensitivity
        await self.hass.async_add_executor_job(
            self._set_sensitivity, MotionSensitivity[option]
        )

    def _set_sensitivity(
        self, value: PirSensorConfigurationService.MotionSensitivity
    ) -> None:
        self._device.motion_sensitivity = value


class VibrationSensitivitySelect(SHCEntity, SelectEntity):
    """Select entity for ShutterContact2Plus vibration sensitivity."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = _VIBRATION_SENSITIVITY_OPTIONS

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize the vibration sensitivity select entity."""
        super().__init__(device, entry_id)
        self._attr_name = "Vibration Sensitivity"
        self._attr_unique_id = (
            f"{device.root_device_id}_{device.id}_vibration_sensitivity"
        )

    @property
    def current_option(self) -> str | None:
        """Return the current sensitivity option."""
        try:
            return self._device.sensitivity.name
        except (AttributeError, ValueError) as err:
            LOGGER.warning(
                "Unknown vibration sensitivity for %s: %s", self._device.name, err
            )
            return None

    async def async_select_option(self, option: str) -> None:
        """Set the vibration sensitivity."""
        SensitivityState = VibrationSensorService.SensitivityState
        await self.hass.async_add_executor_job(
            self._set_sensitivity, SensitivityState[option]
        )

    def _set_sensitivity(
        self, value: VibrationSensorService.SensitivityState
    ) -> None:
        self._device.sensitivity = value
