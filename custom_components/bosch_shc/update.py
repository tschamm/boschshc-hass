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

from boschshcpy import SHCBatteryDevice, SHCSession
from boschshcpy.device import SHCDevice
from boschshcpy.exceptions import SHCException
from homeassistant.components.update import (
    UpdateDeviceClass,
    UpdateEntity,
    UpdateEntityFeature,
)
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

# The ONLY controller-level state ready to activate (bosch-shc-api-docs
# swUpdateState enum) -- same asymmetry-closing guard as _ACTIVATABLE_STATE.
_CONTROLLER_ACTIVATABLE_STATE = "UPDATE_AVAILABLE"

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
# "Unknown" is NOT here: it's a live-confirmed mid-transfer transient (#373).
_UP_TO_DATE_STATES = frozenset(
    {None, "UpToDate", "UpToDateAwaitingUserInteraction", "Fetching"}
)
# Live-confirmed on #373: app showed "updating" for 7+ min on UpdateAvailable.
_DEVICE_IN_PROGRESS_STATES = frozenset(
    {"UpdateRunning", "TransferringUpdate", "Unknown", "UpdateAvailable"}
)

# The ONLY state the live-confirmed PUT .../activate call is valid from --
# every other pending state legitimately 409s if activated (again) (#373).
_ACTIVATABLE_STATE = "AwaitingActivation"

# Markers, not real versions (#373 follow-up: human-readable, not snake_case).
_UP_TO_DATE_VERSION = "Up to date"
_UPDATE_AVAILABLE_VERSION = "Update available"

# Thermostat models where a firmware install requires a manual on-device (or
# Bosch-app) calibration step afterwards -- live-confirmed on TRV_GEN2 (#373).
_CALIBRATION_MODELS = frozenset({"TRV", "TRV_GEN2", "TRV_GEN2_DUAL"})

# SHC-enforced preconditions, surfaced via release_summary (#373 follow-up).
_BATTERY_DISCLAIMER = (
    "⚠️ The SHC won't start a firmware update while this device reports a "
    "low battery level -- make sure the battery is fresh/normal first."
)
_CALIBRATION_DISCLAIMER = (
    "⚠️ After installing, this thermostat requires a manual calibration "
    "step (press the button on the device, or confirm via the Bosch app) "
    "before the update is fully complete. Home Assistant cannot show this "
    'step -- it will just display "Update pending" until you do it.'
)


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

    # Without this, HA schedules the first poll a full SCAN_INTERVAL from now
    # (#373) -- entities would sit unset for up to 6h after every restart.
    async_add_entities(entities, update_before_add=True)


class ControllerUpdate(UpdateEntity):  # type: ignore[misc]
    """Firmware-update entity for the SHC controller.

    INSTALL triggers boschshcpy's start_software_update (POST
    rootdevices/startSoftwareUpdate) — no version selection, no backup: the
    SHC's own endpoint takes no parameters.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "controller_update"
    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_supported_features = (
        UpdateEntityFeature.INSTALL | UpdateEntityFeature.PROGRESS
    )
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
            try:
                await refresh()
            except Exception as err:  # noqa: BLE001 -- never raise from a poll
                LOGGER.debug("Failed to poll controller update state: %s", err)

    async def async_install(
        self, version: str | None, backup: bool, **kwargs: Any
    ) -> None:
        """Trigger the controller firmware update install.

        NOT YET CONFIRMED against real hardware (the controller was always
        up to date whenever this was live-tested, so this call itself never
        actually fired) — see module docstring for the per-device confirm.
        version/backup are ignored: the underlying endpoint takes no
        parameters (no specific-version install, no pre-update backup).

        Only ever calls the start endpoint from `_CONTROLLER_ACTIVATABLE_STATE`
        (hass#373 bughunt follow-up: `DeviceUpdate` got this guard, this
        sibling class hadn't) — every other state would just 409.
        """
        state = getattr(self._information, "update_state", None)
        if state != _CONTROLLER_ACTIVATABLE_STATE:
            raise HomeAssistantError(
                f"Firmware update for {self._title} cannot be activated "
                f"right now (current state: {state}).",
                translation_domain=DOMAIN,
                translation_key="update_not_ready",
                translation_placeholders={
                    "name": self._title,
                    "state": str(state),
                },
            )
        try:
            await self._information.async_start_software_update()
        except SHCException as err:
            raise HomeAssistantError(
                f"Failed to start the firmware update on {self._title}: {err}",
                translation_domain=DOMAIN,
                translation_key="update_install_failed",
                translation_placeholders={"name": self._title, "error": str(err)},
            ) from err
        finally:
            await self.async_update()


class DeviceUpdate(SHCEntity, UpdateEntity):  # type: ignore[misc]
    """Per-device firmware-update entity (APK-traced probe + install).

    The firmware lifecycle state (devicemanagement/firmware/{id}) is a
    separate endpoint from this device's normal device-service model, so it
    does not arrive via the long-poll callbacks (SHCEntity) like every other
    entity in this integration — it must be explicitly polled, same as
    ControllerUpdate. Created only for FIRMWARE_CAPABLE_MODELS.
    """

    _attr_translation_key = "device_firmware"
    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_supported_features = (
        UpdateEntityFeature.INSTALL | UpdateEntityFeature.PROGRESS
    )
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
        except Exception as err:  # noqa: BLE001 -- never raise from a poll
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
        """Surface the raw lifecycle state + any relevant disclaimers.

        The disclaimers cover SHC-enforced preconditions/limitations this
        integration can't detect or override (#373 follow-up): a low battery
        blocking install, and a post-install calibration step Home Assistant
        has no way to represent.
        """
        disclaimers = []
        if isinstance(self._device, SHCBatteryDevice):
            disclaimers.append(_BATTERY_DISCLAIMER)
        if self._device.device_model in _CALIBRATION_MODELS:
            disclaimers.append(_CALIBRATION_DISCLAIMER)
        if not disclaimers:
            return self._firmware_state
        return "\n\n".join([str(self._firmware_state), *disclaimers])

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
                f"Firmware update for {self.device_name} cannot be activated "
                f"right now (current state: {state}).",
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
                translation_placeholders={
                    "name": self.device_name,
                    "error": str(err),
                },
            ) from err
        finally:
            # Re-poll now so a second click before the next 6h poll doesn't
            # re-activate a since-moved-on state and 409 again (#373).
            await self.async_update()
