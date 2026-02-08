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
    _target_position = None
    _last_position = None
    _skip_update = False
    _app_command = False

    def _micromodule_keypad_switch_off(self) -> None:
        if self._device.device_model == "MICROMODULE_SHUTTER":
            # Stopping a micromodule shutter requires setting the eventtype to SWITCH_OFF, in case the manual switch was not put to off position
            self._device.eventtype = (
                SHCMicromoduleShutterControl.KeypadService.KeyEvent.SWITCH_OFF
            )

    def _update_attr(self) -> None:
        """Recomputes the attributes values either at init or when the device state changes."""
        self._attr_current_cover_position = self.current_cover_position
        self._current_operation_state = self._device.operation_state

        if (
            self._current_operation_state
            == SHCShutterControl.ShutterControlService.State.STOPPED
        ):
            self._attr_is_closing = False
            self._attr_is_opening = False
            if not self._skip_update:
                if self._device.device_model == "BBL" or self._app_command:
                    self._last_position = self.current_cover_position
                    self._app_command = False
            else:
                # In case of HA commands, the first STOPPED state is not reliable, so we skip it and reset the flag for the next update
                self._skip_update = False

            # Initiallize the last position for MM at start
            if self._last_position is None:
                self._last_position = self.current_cover_position

        if (
            self._current_operation_state
            == SHCShutterControl.ShutterControlService.State.MOVING
        ):
            if self._device.device_model == "BBL":
                self._target_position = round(self._device.level * 100.0)
                if self._last_position is not None:
                    if self._target_position > self._last_position:
                        self._attr_is_closing = False
                        self._attr_is_opening = True
                    elif self._target_position < self._last_position:
                        self._attr_is_closing = True
                        self._attr_is_opening = False
            elif self._device.device_model == "MICROMODULE_SHUTTER":
                if (
                    self._device.eventtype
                    == SHCMicromoduleShutterControl.KeypadService.KeyEvent.SWITCH_ON
                    and self._device.keycode == 1
                ):
                    # When the event is triggered by the physical switch, we can determine the movement direction based on the keycode (1 for open, 2 for close), as the level attribute is not reliable during movement
                    self._last_position = round(self._device.level * 100.0)
                    self._attr_is_closing = False
                    self._attr_is_opening = True
                    self._target_position = 100
                elif (
                    self._device.eventtype
                    == SHCMicromoduleShutterControl.KeypadService.KeyEvent.SWITCH_ON
                    and self._device.keycode == 2
                ):
                    self._last_position = round(self._device.level * 100.0)
                    self._attr_is_closing = True
                    self._attr_is_opening = False
                    self._target_position = 0
                else:
                    self._target_position = round(self._device.level * 100.0)
                    if self._target_position > self._last_position:
                        self._attr_is_closing = False
                        self._attr_is_opening = True
                    elif self._target_position < self._last_position:
                        self._attr_is_closing = True
                        self._attr_is_opening = False

            else:
                # for other devices, we cannot determine the movement direction, so we set both to None
                print("  No plan what to do")
                self._attr_is_closing = None
                self._attr_is_opening = None

    @property
    def device_class(self) -> CoverDeviceClass | None:
        return (
            CoverDeviceClass.AWNING
            if self._device.device_model == "MICROMODULE_AWNING"
            else CoverDeviceClass.SHUTTER
        )

    @property
    def current_cover_position(self):
        """Return the current or target cover position."""
        if self._device.device_model == "MICROMODULE_SHUTTER":
            return (
                round(self._device.level * 100.0)
                if self._device.operation_state
                == SHCShutterControl.ShutterControlService.State.STOPPED
                else self._target_position
            )
        else:
            # for BBL devices, we can rely on the level attribute to determine the current position, even when moving
            return round(self._device.level * 100.0)

    def stop_cover(self, **kwargs):
        """Stop the cover."""
        self._micromodule_keypad_switch_off()
        self._attr_is_opening = False
        self._attr_is_closing = False
        self._device.stop()
        self._skip_update = True
        self._app_command = True

    @property
    def is_closed(self):
        """Return if the cover is closed or not."""
        return (
            self._device.operation_state
            == SHCShutterControl.ShutterControlService.State.STOPPED
            and self._device.level == 0.0
        )

    def open_cover(self, **kwargs):
        """Open the cover."""
        self._micromodule_keypad_switch_off()
        self._attr_is_opening = True
        self._device.level = 1.0
        self._target_position = 100
        self._skip_update = True
        self._app_command = True

    def close_cover(self, **kwargs):
        """Close cover."""
        self._micromodule_keypad_switch_off()
        self._attr_is_closing = True
        self._device.level = 0.0
        self._target_position = 0
        self._skip_update = True
        self._app_command = True

    def set_cover_position(self, **kwargs):
        """Move the cover to a specific position."""
        if self._device.device_model == "MICROMODULE_SHUTTER":
            self._micromodule_keypad_switch_off()
            self._last_position = self.current_cover_position
        position = kwargs[ATTR_POSITION]
        self._target_position = position
        self._device.level = position / 100.0
        self._skip_update = True
        self._app_command = True

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
