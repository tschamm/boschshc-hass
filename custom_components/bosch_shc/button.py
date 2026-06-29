"""Platform for button integration."""

from __future__ import annotations

from typing import Any

from boschshcpy import (
    SHCDevice,
    SHCSession,
)
from boschshcpy.services_impl import DetectionTestService, WalkTestService
from homeassistant.components.button import (
    ButtonEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DATA_SESSION,
    DATA_SHC,
    DOMAIN,
    LOGGER,
    OPT_SCENARIOS_AS_BUTTONS,
    OPT_SCENARIOS_FILTER,
)
from .entity import SHCEntity, device_excluded

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the SHC binary sensor platform."""
    entities: list[ButtonEntity] = []
    session: SHCSession = hass.data[DOMAIN][config_entry.entry_id][DATA_SESSION]

    for button in getattr(session.device_helper, "micromodule_impulse_relays", []):
        if device_excluded(button, config_entry.options):
            continue
        entities.append(
            SHCRelayButton(
                device=button,
                entry_id=config_entry.entry_id,
            )
        )

    for button in getattr(session.device_helper, "smoke_detectors", []):
        if device_excluded(button, config_entry.options):
            continue
        entities.append(
            SHCSmokeTestButton(
                device=button,
                entry_id=config_entry.entry_id,
            )
        )

    for button in getattr(session.device_helper, "twinguards", []):
        if device_excluded(button, config_entry.options):
            continue
        entities.append(
            SHCSmokeTestButton(
                device=button,
                entry_id=config_entry.entry_id,
            )
        )

    # WalkTest start + stop buttons for Motion Detector II (guarded — optional service).
    for button in getattr(session.device_helper, "motion_detectors2", []):
        if device_excluded(button, config_entry.options):
            continue
        if not getattr(button, "supports_walk_test", False):
            continue
        if button.walk_state is None:
            # WalkTest service not present on this device
            continue
        entities.append(
            SHCWalkTestButton(
                device=button,
                entry_id=config_entry.entry_id,
            )
        )
        entities.append(
            SHCWalkTestStopButton(
                device=button,
                entry_id=config_entry.entry_id,
            )
        )

    # DetectionTest start/stop + tamper reset for Motion Detector II.
    # The local API exposes the walk test through the DetectionTest service
    # (vs the APK-derived WalkTest service above); a given MD2 carries one or
    # the other, so both are wired and each is guarded by its own service.
    for button in getattr(session.device_helper, "motion_detectors2", []):
        if device_excluded(button, config_entry.options):
            continue
        if getattr(button, "supports_detection_test", False):
            entities.append(
                SHCDetectionTestButton(
                    device=button,
                    entry_id=config_entry.entry_id,
                )
            )
            entities.append(
                SHCDetectionTestStopButton(
                    device=button,
                    entry_id=config_entry.entry_id,
                )
            )
        # resetTamperedState — LatestTamper is a standard MD2 service.
        if hasattr(button, "reset_tampered_state"):
            entities.append(
                SHCTamperResetButton(
                    device=button,
                    entry_id=config_entry.entry_id,
                )
            )

    if config_entry.options.get(OPT_SCENARIOS_AS_BUTTONS, False):
        entry_unique_id = config_entry.unique_id
        entry_id = config_entry.entry_id
        shc_device: DeviceEntry = hass.data[DOMAIN][entry_id][DATA_SHC]
        scenario_filter = config_entry.options.get(OPT_SCENARIOS_FILTER) or []

        def _make_scenario_button(scenario: Any) -> SHCScenarioButton | None:
            """Build a SHCScenarioButton, returning None on malformed payload."""
            try:
                return SHCScenarioButton(
                    scenario=scenario,
                    entry_unique_id=entry_unique_id,
                    entry_id=entry_id,
                    shc_device=shc_device,
                )
            except (KeyError, AttributeError) as err:
                # A malformed scenario payload must not take out the whole
                # button platform — skip just that scenario.
                LOGGER.warning("Skipping scenario button (bad payload): %s", err)
                return None

        entities.extend(
            btn
            for scenario in session.scenarios
            if not scenario_filter or scenario.id in scenario_filter
            if (btn := _make_scenario_button(scenario)) is not None
        )

    for siren in getattr(session.device_helper, "outdoor_sirens", []):
        if device_excluded(siren, config_entry.options):
            continue
        entities.append(
            SHCSirenTestAlarmButton(device=siren, entry_id=config_entry.entry_id)
        )

    # DimmerConfiguration preview buttons: flash at max/min for calibration (#123).
    for device in getattr(session.device_helper, "micromodule_dimmers", []):
        if device_excluded(device, config_entry.options):
            continue
        if getattr(device, "supports_dimmer_configuration", False):
            entities.append(
                DimmerPreviewMaxButton(device=device, entry_id=config_entry.entry_id)
            )
            entities.append(
                DimmerPreviewMinButton(device=device, entry_id=config_entry.entry_id)
            )

    if entities:
        async_add_entities(entities)


class SHCRelayButton(SHCEntity, ButtonEntity):  # type: ignore[misc]
    """Representation of a SHC button."""

    def __init__(
        self,
        device: SHCDevice,
        entry_id: str,
        attr_name: str | None = None,
    ) -> None:
        """Initialize a SHC switch."""
        super().__init__(device, entry_id)
        self._attr_name = attr_name  # type: ignore[assignment]
        self._attr_unique_id = (
            f"{device.root_device_id}_{device.id}"
            if attr_name is None
            else f"{device.root_device_id}_{device.id}_{attr_name.lower()}"
        )

    async def async_press(self) -> None:
        """Trigger the relay impulse (awaited — the session is async; #336)."""
        await self._device.async_trigger_impulse_state()


class SHCSmokeTestButton(SHCEntity, ButtonEntity):  # type: ignore[misc]
    """Button entity that requests a smoke detector self-test."""

    _attr_icon = "mdi:smoke-detector-alert"
    _attr_translation_key = "smoke_test"

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize the smoke-test button."""
        super().__init__(device, entry_id)
        self._attr_unique_id = f"{device.root_device_id}_{device.id}_smoke_test"

    async def async_press(self) -> None:
        """Trigger the device self-test (awaited — the session is async; #336)."""
        await self._device.async_smoketest_requested()


class SHCSirenTestAlarmButton(SHCEntity, ButtonEntity):  # type: ignore[misc]
    """Button that fires a short Outdoor Siren test alarm (#120)."""

    _attr_icon = "mdi:bullhorn"
    _attr_translation_key = "siren_test_alarm"

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize the siren test-alarm button."""
        super().__init__(device, entry_id)
        self._attr_unique_id = f"{device.root_device_id}_{device.id}_test_alarm"

    async def async_press(self) -> None:
        """Trigger a short test alarm at the configured sound level."""
        await self._device.async_trigger_test_alarm()


class SHCScenarioButton(ButtonEntity):  # type: ignore[misc]
    """Button entity that triggers a single Bosch SHC scenario.

    Scenarios are not SHC devices, so this entity does NOT inherit SHCEntity.
    unique_id is scoped to the config entry so each SHC controller gets its
    own set of scenario buttons even when multiple controllers are present.
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:script-text-play"
    _attr_should_poll = False

    def __init__(
        self,
        scenario: Any,
        entry_unique_id: str | None,
        entry_id: str,
        shc_device: DeviceEntry | None = None,
    ) -> None:
        """Initialize a scenario button."""
        self._scenario = scenario
        self._shc_device = shc_device
        prefix = entry_unique_id or entry_id
        self._attr_unique_id = f"{prefix}_scenario_{scenario.id}"
        self._attr_name = scenario.name

    @property
    def device_info(self) -> dict[str, Any] | None:
        """Return the device info (links this button to the SHC controller device)."""
        if self._shc_device is None:
            return None
        return {
            "identifiers": self._shc_device.identifiers,
            "name": self._shc_device.name,
            "manufacturer": self._shc_device.manufacturer,
            "model": self._shc_device.model,
        }

    async def async_press(self) -> None:
        """Trigger the scenario (awaited — the session is async; #336)."""
        await self._scenario.async_trigger()


class SHCWalkTestButton(SHCEntity, ButtonEntity):  # type: ignore[misc]
    """Button entity that starts a WalkTest on a Motion Detector II.

    The WalkTest service is optional on MD2 hardware; this entity is only
    created when walk_state is not None (i.e. the service is present).
    Pressing starts the test; a separate stop button is also created.
    """

    _attr_icon = "mdi:walk"
    _attr_translation_key = "walk_test"

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize the walk-test start button."""
        super().__init__(device, entry_id)
        self._attr_unique_id = f"{device.root_device_id}_{device.id}_walk_test"

    async def async_press(self) -> None:
        """Send WALK_STATE_START request to the WalkTest service."""
        await self._device.async_set_walk_state_request(
            WalkTestService.WalkStateRequest.WALK_STATE_START
        )


class SHCWalkTestStopButton(SHCEntity, ButtonEntity):  # type: ignore[misc]
    """Button entity that stops a WalkTest on a Motion Detector II.

    Stops an in-progress walk test by sending WALK_STATE_STOP to the
    WalkTest service.  Only created when the WalkTest service is present.
    """

    _attr_icon = "mdi:stop"
    _attr_translation_key = "walk_test_stop"

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize the walk-test stop button."""
        super().__init__(device, entry_id)
        self._attr_unique_id = f"{device.root_device_id}_{device.id}_walk_test_stop"

    async def async_press(self) -> None:
        """Send WALK_STATE_STOP request to the WalkTest service."""
        await self._device.async_set_walk_state_request(
            WalkTestService.WalkStateRequest.WALK_STATE_STOP
        )


class SHCDetectionTestButton(SHCEntity, ButtonEntity):  # type: ignore[misc]
    """Button that starts a detection (walk) test via the DetectionTest service.

    The local Bosch API exposes the walk test through DetectionTest; only
    created when the device carries that service (supports_detection_test).
    """

    _attr_icon = "mdi:walk"
    _attr_translation_key = "detection_test"

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize the detection-test start button."""
        super().__init__(device, entry_id)
        self._attr_unique_id = f"{device.root_device_id}_{device.id}_detection_test"

    async def async_press(self) -> None:
        """Send DETECTION_STATE_START to the DetectionTest service."""
        await self._device.async_set_detection_state_request(
            DetectionTestService.DetectionStateRequest.DETECTION_STATE_START
        )


class SHCDetectionTestStopButton(SHCEntity, ButtonEntity):  # type: ignore[misc]
    """Button that stops an in-progress detection (walk) test."""

    _attr_icon = "mdi:stop"
    _attr_translation_key = "detection_test_stop"

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize the detection-test stop button."""
        super().__init__(device, entry_id)
        self._attr_unique_id = (
            f"{device.root_device_id}_{device.id}_detection_test_stop"
        )

    async def async_press(self) -> None:
        """Send DETECTION_STATE_STOP to the DetectionTest service."""
        await self._device.async_set_detection_state_request(
            DetectionTestService.DetectionStateRequest.DETECTION_STATE_STOP
        )


class SHCTamperResetButton(SHCEntity, ButtonEntity):  # type: ignore[misc]
    """Button that resets an active tamper condition (LatestTamper service)."""

    _attr_icon = "mdi:restart-alert"
    _attr_translation_key = "reset_tamper"

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize the tamper-reset button."""
        super().__init__(device, entry_id)
        self._attr_unique_id = f"{device.root_device_id}_{device.id}_reset_tamper"

    async def async_press(self) -> None:
        """POST resetTamperedState to confirm the device is back in place."""
        await self._device.async_reset_tampered_state()


class DimmerPreviewMaxButton(SHCEntity, ButtonEntity):  # type: ignore[misc]
    """Button that flashes the dimmer at max brightness for load calibration (#123)."""

    _attr_icon = "mdi:brightness-7"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "preview_max_brightness"

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize the dimmer preview-max brightness button."""
        super().__init__(device, entry_id)
        self._attr_unique_id = f"{device.root_device_id}_{device.id}_dimmer_preview_max"

    async def async_press(self) -> None:
        """Flash the dimmer at maximum brightness for load calibration."""
        svc = getattr(self._device, "dimmer_configuration", None)
        if svc is not None:
            await svc.async_preview_max_brightness()


class DimmerPreviewMinButton(SHCEntity, ButtonEntity):  # type: ignore[misc]
    """Button that flashes the dimmer at min brightness for load calibration (#123)."""

    _attr_icon = "mdi:brightness-2"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "preview_min_brightness"

    def __init__(self, device: SHCDevice, entry_id: str) -> None:
        """Initialize the dimmer preview-min brightness button."""
        super().__init__(device, entry_id)
        self._attr_unique_id = f"{device.root_device_id}_{device.id}_dimmer_preview_min"

    async def async_press(self) -> None:
        """Flash the dimmer at minimum brightness for load calibration."""
        svc = getattr(self._device, "dimmer_configuration", None)
        if svc is not None:
            await svc.async_preview_min_brightness()
