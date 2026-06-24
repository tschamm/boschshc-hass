"""Platform for the Bosch Smart Home Controller software update (#186).

The controller reports its installed/available firmware via the read-only
public /information endpoint (softwareUpdateState). There is no local API to
trigger an install, so this is a read-only Update entity (no INSTALL feature):
it surfaces "update available" in HA; the update itself is started from the
Bosch Smart Home app.
"""

from __future__ import annotations

from datetime import timedelta

from boschshcpy import SHCSession

from homeassistant.components.update import UpdateEntity, UpdateEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DATA_SESSION, DOMAIN

PARALLEL_UPDATES = 1

# Firmware updates change rarely; poll the controller's /information block a few
# times a day rather than on the default fast entity interval.
SCAN_INTERVAL = timedelta(hours=6)

# swUpdateState values that mean an install is currently running.
_IN_PROGRESS_STATES = {"DOWNLOADING", "INSTALLING", "UPDATE_IN_PROGRESS"}


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the SHC controller update entity."""
    session: SHCSession = hass.data[DOMAIN][config_entry.entry_id][DATA_SESSION]
    information = session.information
    if information is None or information.unique_id is None:
        return
    async_add_entities(
        [ControllerUpdate(information, config_entry.title, config_entry.entry_id)]
    )


class ControllerUpdate(UpdateEntity):
    """Read-only firmware-update indicator for the SHC controller."""

    _attr_has_entity_name = True
    _attr_translation_key = "controller_update"
    _attr_supported_features = UpdateEntityFeature(0)
    _attr_should_poll = True

    def __init__(self, information, title: str, entry_id: str) -> None:
        self._information = information
        self._entry_id = entry_id
        self._attr_unique_id = f"{information.unique_id}_software_update"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, information.unique_id)},
            name=title,
            manufacturer="Bosch",
            model="SmartHomeController",
        )

    @property
    def installed_version(self) -> str | None:
        return self._information.version

    @property
    def latest_version(self) -> str | None:
        # available_version is only meaningful when an update is offered;
        # otherwise report the installed version so HA shows "up to date".
        available = getattr(self._information, "available_version", None)
        if available:
            return available
        return self._information.version

    @property
    def in_progress(self) -> bool:
        state = getattr(self._information, "update_state", None)
        return state in _IN_PROGRESS_STATES

    async def async_update(self) -> None:
        """Refresh the controller's software-update state (#186).

        Re-fetches /information so an update that appears after startup shows up.
        getattr-guarded so an older boschshcpy without async_refresh degrades to
        a static (boot-time) value instead of crashing.
        """
        refresh = getattr(self._information, "async_refresh", None)
        if refresh is not None:
            await refresh()
