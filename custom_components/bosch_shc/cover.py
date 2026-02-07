"""Platform for cover integration."""

from typing import Any
from boschshcpy import (
    SHCSession,
    SHCShutterControl,
    SHCMicromoduleShutterControl,
    SHCMicromoduleBlinds,
)
from boschshcpy.device import SHCDevice

from homeassistant.components.cover import (
    ATTR_POSITION,
    ATTR_TILT_POSITION,
    CoverEntityFeature,
    CoverDeviceClass,
    CoverEntity,
)
from homeassistant.const import Platform
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DATA_SESSION, DOMAIN
from .entity import SHCEntity, async_migrate_to_new_unique_id


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the SHC cover platform."""
    entities = []
    session: SHCSession = hass.data[DOMAIN][config_entry.entry_id][DATA_SESSION]

    for cover in (
        session.device_helper.shutter_controls
        + session.device_helper.micromodule_shutter_controls
    ):
        await async_migrate_to_new_unique_id(hass, Platform.COVER, device=cover)
        entities.append(
            ShutterControlCover(
                device=cover,
                entry_id=config_entry.entry_id,
            )
        )

    for blind in session.device_helper.micromodule_blinds:
        await async_migrate_to_new_unique_id(hass, Platform.COVER, device=blind)
        entities.append(
            BlindsControlCover(
                device=blind,
                entry_id=config_entry.entry_id,
            )
        )

    if entities:
        async_add_entities(entities)


class ShutterControlCover(SHCEntity, CoverEntity):
    """Representation of a SHC shutter control device."""

    _attr_supported_features = (
        CoverEntityFeature.OPEN
        | CoverEntityFeature.CLOSE
        | CoverEntityFeature.STOP
        | CoverEntityFeature.SET_POSITION
    )

    _current_operation_state = None
    _l1_position = _l2_position = None

    def _update_attr(self) -> None:
        """Recomputes the attributes values either at init or when the device state changes."""
        self._attr_current_cover_position = self.current_cover_position
        self._current_operation_state = self._device.operation_state

        if self._l1_position is None:
            self._l2_position = self._l1_position = self._attr_current_cover_position

        # calculate movement direction between current cover position and second last reported position
        if (
            self._current_operation_state
            == SHCShutterControl.ShutterControlService.State.MOVING
        ):
            if self._l2_position > self._attr_current_cover_position:
                self._attr_is_closing = True
            else:
                self._attr_is_opening = True

        if (
            self._current_operation_state
            == SHCShutterControl.ShutterControlService.State.STOPPED
        ):
            self._attr_is_closing = False
            self._attr_is_opening = False

        self._l2_position = self._l1_position
        self._l1_position = self._attr_current_cover_position

    @property
    def device_class(self) -> CoverDeviceClass | None:
        return (
            CoverDeviceClass.AWNING
            if self._device.device_model == "MICROMODULE_AWNING"
            else CoverDeviceClass.SHUTTER
        )

    @property
    def current_cover_position(self):
        """Return the current cover position."""
        return round(self._device.level * 100.0)

    def stop_cover(self, **kwargs):
        """Stop the cover."""
        self._attr_is_opening = False
        self._attr_is_closing = False
        self._device.stop()

    @property
    def is_closed(self):
        """Return if the cover is closed or not."""
        return self._l2_position == 0

    def open_cover(self, **kwargs):
        """Open the cover."""
        if not self._attr_is_opening and self._device.level < 1.0:
            self._attr_is_opening = True
            self._device.level = 1.0

    def close_cover(self, **kwargs):
        """Close cover."""
        if not self._attr_is_closing and self._device.level > 0:
            self._attr_is_closing = True
            self._device.level = 0.0

    def set_cover_position(self, **kwargs):
        """Move the cover to a specific position."""
        position = kwargs[ATTR_POSITION]
        self._device.level = position / 100.0

    @property
    def extra_state_attributes(self):
        """Return the state attributes."""
        return {
            "operation_state": self._device.operation_state,
        }


class BlindsControlCover(ShutterControlCover, CoverEntity):
    """Representation of a SHC blinds cover device."""

    _attr_device_class = CoverDeviceClass.BLIND
    _attr_supported_features = (
        CoverEntityFeature.OPEN
        | CoverEntityFeature.CLOSE
        | CoverEntityFeature.CLOSE_TILT
        | CoverEntityFeature.OPEN_TILT
        | CoverEntityFeature.SET_TILT_POSITION
        | CoverEntityFeature.SET_POSITION
        | CoverEntityFeature.STOP
        | CoverEntityFeature.STOP_TILT
    )

    def open_cover(self, **kwargs):
        """Open the cover."""
        self._device.blinds_level = 1.0

    def close_cover(self, **kwargs):
        """Close cover."""
        self._device.blinds_level = 0.0

    def set_cover_position(self, **kwargs):
        """Move the cover to a specific position."""
        position = kwargs[ATTR_POSITION]
        self._device.blinds_level = position / 100.0

    def stop_cover_tilt(self, **kwargs: Any) -> None:
        self._device.stop_blinds()

    @property
    def current_cover_tilt_position(self):
        """Return the current cover tilt position."""
        return round((1.0 - self._device.current_angle) * 100.0)

    def open_cover_tilt(self, **kwargs):
        """Open the cover tilt."""
        self._device.target_angle = 1.0 - 1.0

    def close_cover_tilt(self, **kwargs):
        """Close cover tilt."""
        self._device.target_angle = 1.0 - 0.0

    def set_cover_tilt_position(self, **kwargs):
        """Move the cover tilt to a specific position."""
        tilt_position = kwargs[ATTR_TILT_POSITION]
        self._device.target_angle = 1.0 - (tilt_position / 100.0)
