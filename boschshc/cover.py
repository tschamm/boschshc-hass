"""Platform for cover integration."""
import logging

from boschshcpy import SHCDeviceHelper, SHCSession, SHCShutterControl

from homeassistant.components.cover import (
    ATTR_POSITION,
    SUPPORT_CLOSE,
    SUPPORT_OPEN,
    SUPPORT_SET_POSITION,
    SUPPORT_STOP,
    CoverDevice,
)
from homeassistant.const import CONF_IP_ADDRESS, CONF_NAME

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the cover platform."""

    device = []
    session: SHCSession = hass.data[DOMAIN][config_entry.entry_id]

    for cover in session.device_helper.shutter_controls:
        _LOGGER.debug(f"Found shutter control: {cover.name} ({cover.id})")
        device.append(
            ShutterControlCover(
                device=cover,
                room_name=session.room(cover.room_id).name,
                controller_ip=config_entry.data[CONF_IP_ADDRESS],
            )
        )

    if device:
        async_add_entities(device)


class ShutterControlCover(CoverDevice):
    def __init__(self, device: SHCShutterControl, room_name: str, controller_ip: str):
        self._device = device
        self._room_name = room_name
        self._controller_ip = controller_ip
        self.update()

    async def async_added_to_hass(self):
        await super().async_added_to_hass()

        def on_state_changed():
            self.schedule_update_ha_state()

        for service in self._device.device_services:
            service.on_state_changed = on_state_changed

    async def async_will_remove_from_hass(self):
        await super().async_will_remove_from_hass()
        for service in self._device.device_services:
            service.on_state_changed = None

    @property
    def unique_id(self):
        """Return the unique ID."""
        return self._device.serial

    @property
    def device_id(self):
        """Return the ID."""
        return self._device.id

    @property
    def root_device(self):
        return self._device.root_device_id

    @property
    def name(self):
        """Name of the device."""
        return self._device.name

    @property
    def manufacturer(self):
        """The manufacturer of the device."""
        return self._device.manufacturer

    @property
    def device_info(self):
        """Return the device info."""
        return {
            "identifiers": {(DOMAIN, self.device_id)},
            "name": self.name,
            "manufacturer": self.manufacturer,
            "model": self._device.device_model,
            "sw_version": "",
            "via_device": (DOMAIN, self._controller_ip),
        }

    @property
    def should_poll(self):
        """Polling needed."""
        return False

    @property
    def available(self):
        """Return false if status is unavailable."""
        return True if self._device.status == "AVAILABLE" else False

    @property
    def supported_features(self):
        """Flag supported features."""
        return SUPPORT_OPEN | SUPPORT_CLOSE | SUPPORT_STOP | SUPPORT_SET_POSITION

    @property
    def current_cover_position(self):
        """The current cover position."""
        return self._device.level * 100.0

    def stop_cover(self):
        """Stop the cover."""
        self._device.stop()
        return

    @property
    def is_closed(self):
        """Return if the cover is closed or not."""
        if self.current_cover_position == None:
            return None
        elif self.current_cover_position == 0.0:
            return True
        return False

    @property
    def is_opening(self):
        """Return if the cover is opening or not."""
        if (
            self._device.operation_state
            == SHCShutterControl.ShutterControlService.State.OPENING
        ):
            return True
        else:
            False

    @property
    def is_closing(self):
        """Return if the cover is closing or not."""
        if (
            self._device.operation_state
            == SHCShutterControl.ShutterControlService.State.CLOSING
        ):
            return True
        else:
            False

    def open_cover(self):
        """Open the cover."""
        self._device.level = 1.0

    def close_cover(self):
        """Close cover."""
        self._device.level = 0.0

    def set_cover_position(self, **kwargs):
        """Move the cover to a specific position."""
        if ATTR_POSITION in kwargs:
            position = float(kwargs[ATTR_POSITION])
            position = min(100, max(0, position))
            self._device.level = position / 100.0

    def update(self):
        self._device.update()

    @property
    def state_attributes(self):
        state_attr = super().state_attributes
        if state_attr is None:
            state_attr = dict()
        state_attr["boschshc_room_name"] = self._room_name
        return state_attr
