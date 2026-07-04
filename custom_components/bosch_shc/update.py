"""Platform for the Bosch Smart Home Controller software update (#186).

The controller reports its installed/available firmware via the read-only
public /information endpoint (softwareUpdateState). There is no local API to
trigger an install, so this is a read-only Update entity (no INSTALL feature):
it surfaces "update available" in HA; the update itself is started from the
Bosch Smart Home app.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from boschshcpy import SHCSession
from boschshcpy.device import SHCDevice
from homeassistant.components.update import UpdateEntity, UpdateEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DATA_SESSION, DOMAIN
from .entity import SHCEntity, device_excluded

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
    """Set up the SHC controller + per-device update entities."""
    session: SHCSession = hass.data[DOMAIN][config_entry.entry_id][DATA_SESSION]
    entities: list[UpdateEntity] = []

    information = session.information
    if information is not None and information.unique_id is not None:
        entities.append(
            ControllerUpdate(information, config_entry.title, config_entry.entry_id)
        )

    # Per-device firmware update entities — only for devices that actually
    # expose a SoftwareUpdate service (spec-grounded, getattr-guarded; most
    # devices do not carry it, so this loop is usually inert).
    device: SHCDevice
    for device in session.devices:
        if device_excluded(device, config_entry.options):
            continue
        if getattr(device, "supports_software_update", False):
            entities.append(DeviceUpdate(device, config_entry.entry_id))

    async_add_entities(entities)


class ControllerUpdate(UpdateEntity):  # type: ignore[misc]
    """Read-only firmware-update indicator for the SHC controller."""

    _attr_has_entity_name = True
    _attr_translation_key = "controller_update"
    _attr_supported_features = UpdateEntityFeature(0)
    _attr_should_poll = True

    def __init__(self, information: Any, title: str, entry_id: str) -> None:
        """Initialize the controller update entity."""
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
        """Return the currently installed firmware version."""
        return self._information.version  # type: ignore[no-any-return]

    @property
    def latest_version(self) -> str | None:
        """Return the latest available firmware version."""
        # available_version is only meaningful when an update is offered;
        # otherwise report the installed version so HA shows "up to date".
        available = getattr(self._information, "available_version", None)
        if available:
            return available  # type: ignore[no-any-return]
        return self._information.version  # type: ignore[no-any-return]

    @property
    def in_progress(self) -> bool:
        """Return True if a firmware update is currently in progress."""
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


class DeviceUpdate(SHCEntity, UpdateEntity):  # type: ignore[misc]
    """Read-only per-device firmware-update indicator (spec-grounded).

    Surfaces a device's installed/available firmware from its SoftwareUpdate
    service. Like the controller entity (#186) the local API exposes no install
    action, so there is no INSTALL feature. State changes arrive via the normal
    long-poll callbacks (SHCEntity), so no polling is needed. Created only for
    devices that actually expose the service, so it stays inert otherwise.
    """

    _attr_translation_key = "device_firmware"
    _attr_supported_features = UpdateEntityFeature(0)

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize the per-device firmware update entity."""
        super().__init__(device, entry_id)
        self._attr_unique_id = f"{device.root_device_id}_{device.id}_software_update"

    @property
    def installed_version(self) -> str | None:
        """Return the currently installed firmware version."""
        service = self._device.software_update
        return service.sw_installed_version if service is not None else None

    @property
    def latest_version(self) -> str | None:
        """Return the latest available firmware version."""
        service = self._device.software_update
        if service is None:
            return None
        # Surface the available version whenever an update is offered OR an
        # install is running, so the badge stays consistent with in_progress
        # (HA shows a contradictory "installing + up to date" if latest_version
        # drops back to installed mid-install). Otherwise echo the installed
        # version so HA shows "up to date".
        offered_or_running = {
            service.SwUpdateState.UPDATE_AVAILABLE,
            service.SwUpdateState.DOWNLOADING,
            service.SwUpdateState.INSTALLING,
            service.SwUpdateState.UPDATE_IN_PROGRESS,
            # A failed install doesn't apply the pending version, so the
            # update stays outstanding — without this, latest_version fell
            # back to sw_installed_version right when the user most needs to
            # see there's still a pending update.
            service.SwUpdateState.UPDATE_FAILED,
        }
        if (
            service.sw_update_state in offered_or_running
            and service.sw_update_available_version
        ):
            return service.sw_update_available_version  # type: ignore[no-any-return]
        return service.sw_installed_version  # type: ignore[no-any-return]

    @property
    def in_progress(self) -> bool:
        """Return True if a firmware update is currently in progress."""
        service = self._device.software_update
        if service is None:
            return False
        return service.sw_update_state in (
            service.SwUpdateState.DOWNLOADING,
            service.SwUpdateState.INSTALLING,
            service.SwUpdateState.UPDATE_IN_PROGRESS,
        )
