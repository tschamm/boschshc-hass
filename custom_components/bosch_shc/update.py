"""Platform for the Bosch Smart Home Controller software update (#186).

The controller reports its installed/available firmware via the read-only
public /information endpoint (softwareUpdateState). Per-device firmware state
is a separate, undocumented, device-agnostic probe
(devicemanagement/firmware/{deviceId}, GET) traced via APK decompile
(FirmwarePresenter/FirmwareStateLoader). Both the read probe and the INSTALL
action (boschshcpy's SHCInformation.start_software_update /
SHCDevice.activate_firmware_update) are live-confirmed: a real TRV_GEN2
went AwaitingActivation -> UpdatePending -> UpToDateAwaitingUserInteraction
after pressing Install, staying fully functional throughout.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from boschshcpy import SHCSession
from boschshcpy.device import SHCDevice
from boschshcpy.exceptions import SHCException
from homeassistant.components.update import UpdateEntity, UpdateEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, LOGGER
from .entity import SHCEntity, device_excluded

PARALLEL_UPDATES = 1

# Firmware updates change rarely; poll a few times a day rather than on the
# default fast entity interval.
SCAN_INTERVAL = timedelta(hours=6)

# swUpdateState values that mean an install is currently running (controller,
# from the public /information endpoint's own, differently-shaped enum).
_IN_PROGRESS_STATES = {"DOWNLOADING", "INSTALLING", "UPDATE_IN_PROGRESS"}

# Models with a firmware-update UI in the Bosch app (one "*FirmwareFragment"
# class per model, APK) — avoids blind-probing every device at startup.
FIRMWARE_CAPABLE_MODELS = frozenset(
    {
        "TRV_GEN2",
        "TRV_GEN2_DUAL",
        "MD2",
        "SMOKE_DETECTOR2",
        "TWINGUARD",
        "OUTDOOR_SIREN",
        "MICROMODULE_LIGHT_CONTROL",
        "MICROMODULE_BLINDS",
        "MICROMODULE_SHUTTER",
        "MICROMODULE_AWNING",
        "PLUG_COMPACT_DUAL",
    }
)

# FirmwareView.FirmwareState values (APK) meaning "nothing to install" --
# anything else, incl. an unrecognized future string, counts as pending.
_UP_TO_DATE_STATES = frozenset(
    {None, "UpToDate", "UpToDateAwaitingUserInteraction", "Unknown", "Fetching"}
)
# States where the app itself shows an active progress indicator.
_DEVICE_IN_PROGRESS_STATES = frozenset({"UpdateRunning", "TransferringUpdate"})

# The ONLY state the live-confirmed PUT .../activate call is valid from --
# every other pending state legitimately 409s if activated (again) (#373).
_ACTIVATABLE_STATE = "AwaitingActivation"

# Markers, not real versions -- the probe returns a lifecycle state, and
# UpdateEntity only needs the two to differ to show "update available".
_UP_TO_DATE_VERSION = "up_to_date"
_UPDATE_AVAILABLE_VERSION = "update_available"


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the SHC controller + per-device update entities."""
    session: SHCSession = config_entry.runtime_data.session
    entities: list[UpdateEntity] = []

    information = session.information
    if information is not None and information.unique_id is not None:
        entities.append(
            ControllerUpdate(information, config_entry.title, config_entry.entry_id)
        )

    device: SHCDevice
    for device in session.devices:
        if device_excluded(device, config_entry.options):
            continue
        if device.device_model in FIRMWARE_CAPABLE_MODELS:
            entities.append(DeviceUpdate(device, config_entry.entry_id))

    async_add_entities(entities)


class ControllerUpdate(UpdateEntity):  # type: ignore[misc]
    """Firmware-update entity for the SHC controller.

    INSTALL triggers boschshcpy's start_software_update (POST
    rootdevices/startSoftwareUpdate) — no version selection, no backup: the
    SHC's own endpoint takes no parameters.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "controller_update"
    _attr_supported_features = UpdateEntityFeature.INSTALL
    _attr_should_poll = True

    def __init__(self, information: Any, title: str, entry_id: str) -> None:
        """Initialize the controller update entity."""
        self._information = information
        self._entry_id = entry_id
        self._title = title
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

    async def async_install(
        self, version: str | None, backup: bool, **kwargs: Any
    ) -> None:
        """Trigger the controller firmware update install.

        NOT YET CONFIRMED against real hardware (the controller was always
        up to date whenever this was live-tested, so this call itself never
        actually fired) — see module docstring for the per-device confirm.
        version/backup are ignored: the underlying endpoint takes no
        parameters (no specific-version install, no pre-update backup).
        """
        try:
            await self._information.async_start_software_update()
        except SHCException as err:
            raise HomeAssistantError(
                f"Failed to start the firmware update on {self._title}: {err}",
                translation_domain=DOMAIN,
                translation_key="update_install_failed",
            ) from err


class DeviceUpdate(SHCEntity, UpdateEntity):  # type: ignore[misc]
    """Per-device firmware-update entity (APK-traced probe + install).

    The firmware lifecycle state (devicemanagement/firmware/{id}) is a
    separate endpoint from this device's normal device-service model, so it
    does not arrive via the long-poll callbacks (SHCEntity) like every other
    entity in this integration — it must be explicitly polled, same as
    ControllerUpdate. Created only for FIRMWARE_CAPABLE_MODELS.
    """

    _attr_translation_key = "device_firmware"
    _attr_supported_features = UpdateEntityFeature.INSTALL
    _attr_should_poll = True

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize the per-device firmware update entity."""
        super().__init__(device, entry_id)
        self._attr_unique_id = f"{device.root_device_id}_{device.id}_software_update"
        self._firmware_state: str | None = None

    async def async_update(self) -> None:
        """Poll this device's firmware lifecycle state (#186 follow-up).

        Errors are logged, not raised — a transient probe failure shouldn't
        make the entity unavailable; it just keeps the last-known state.
        """
        try:
            self._firmware_state = await self._device.async_firmware_update_state()
        except SHCException as err:
            LOGGER.debug(
                "Failed to poll firmware state for %s: %s", self.device_name, err
            )

    @property
    def installed_version(self) -> str | None:
        """Return a fixed marker (the probe has no real version string)."""
        return _UP_TO_DATE_VERSION

    @property
    def latest_version(self) -> str | None:
        """Return a differing marker whenever a firmware state is pending."""
        if self._firmware_state in _UP_TO_DATE_STATES:
            return _UP_TO_DATE_VERSION
        return _UPDATE_AVAILABLE_VERSION

    @property
    def in_progress(self) -> bool:
        """Return True if a firmware update is currently in progress."""
        return self._firmware_state in _DEVICE_IN_PROGRESS_STATES

    @property
    def release_summary(self) -> str | None:
        """Surface the raw lifecycle state for the more-info dialog."""
        return self._firmware_state

    async def async_install(
        self, version: str | None, backup: bool, **kwargs: Any
    ) -> None:
        """Trigger this device's firmware update install.

        Confirmed live against a real TRV_GEN2 — see module docstring.
        version/backup are ignored: the underlying endpoint takes no
        parameters (no specific-version install, no pre-update backup).

        Only ever calls the activate endpoint from `_ACTIVATABLE_STATE`
        (hass#373) — every other pending state means the SHC isn't ready to
        activate yet and would just 409.
        """
        state = self._firmware_state
        if state != _ACTIVATABLE_STATE:
            raise HomeAssistantError(
                f"Firmware update for {self.device_name} is not ready to "
                f"activate yet (current state: {state}). Wait for the "
                "controller to finish preparing it before trying again.",
                translation_domain=DOMAIN,
                translation_key="update_not_ready",
                translation_placeholders={
                    "name": self.device_name,
                    "state": str(state),
                },
            )
        try:
            await self._device.async_activate_firmware_update()
        except SHCException as err:
            raise HomeAssistantError(
                f"Failed to start the firmware update on {self.device_name}: {err}",
                translation_domain=DOMAIN,
                translation_key="update_install_failed",
            ) from err
