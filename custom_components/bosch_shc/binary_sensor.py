"""Platform for binarysensor integration."""

from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from boschshcpy import (
    AlarmService,
    BatteryLevelService,
    SHCBatteryDevice,
    SHCClimateControl,
    SHCDevice,
    SHCMotionDetector,
    SHCMotionDetector2,
    SHCOutdoorSiren,
    SHCSession,
    SHCShutterContact,
    SHCShutterContact2Plus,
    SHCShutterControl,
    SHCSmokeDetectionSystem,
    SHCSmokeDetector,
    SHCTwinguard,
    SHCWaterLeakageSensor,
    ShutterContactService,
    SurveillanceAlarmService,
    VibrationSensorService,
    WaterLeakageSensorService,
)
from boschshcpy.exceptions import SHCException
from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_COMMAND,
    ATTR_DEVICE_ID,
    ATTR_ID,
    ATTR_NAME,
    EVENT_HOMEASSISTANT_STOP,
    Platform,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    ATTR_EVENT_SUBTYPE,
    ATTR_EVENT_TYPE,
    ATTR_LAST_TIME_TRIGGERED,
    DOMAIN,
    EVENT_BOSCH_SHC,
    LOGGER,
    SERVICE_SMOKEDETECTOR_ALARMSTATE,
    SERVICE_SMOKEDETECTOR_CHECK,
)
from .entity import (
    SHCEntity,
    async_get_device_id,
    async_migrate_to_new_unique_id,
    device_excluded,
)

PARALLEL_UPDATES = 1


async def async_setup_entry(  # noqa: C901
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the SHC binary sensor platform."""
    entities: list[BinarySensorEntity] = []
    session: SHCSession = config_entry.runtime_data.session

    @callback  # type: ignore[untyped-decorator]
    def async_add_shuttercontact(
        device: SHCShutterContact,
    ) -> None:
        """Add Shutter Contact 2 Binary Sensor."""
        binary_sensor = ShutterContactSensor(
            device=device,
            entry_id=config_entry.entry_id,
            entity_description=SHUTTER_CONTACT_DESCRIPTION,
        )
        async_add_entities([binary_sensor])

    for shutter_device in list(session.device_helper.shutter_contacts) + list(
        session.device_helper.shutter_contacts2
    ):
        if device_excluded(shutter_device, config_entry.options):
            continue
        await async_migrate_to_new_unique_id(
            hass, Platform.BINARY_SENSOR, device=shutter_device
        )
        async_add_shuttercontact(device=shutter_device)

    # session.subscribe() returns None, so unsubscribe is built manually below.
    _shutter_subscriber = (SHCShutterContact, async_add_shuttercontact)
    session.subscribe(_shutter_subscriber)

    def _unsubscribe_shutter() -> None:
        with contextlib.suppress(ValueError):
            session._subscribers.remove(_shutter_subscriber)  # noqa: SLF001

    config_entry.async_on_unload(_unsubscribe_shutter)

    for motion_device in session.device_helper.motion_detectors:
        if device_excluded(motion_device, config_entry.options):
            continue
        await async_migrate_to_new_unique_id(
            hass, Platform.BINARY_SENSOR, device=motion_device
        )
        entities.append(
            MotionDetectionSensor(
                hass=hass,
                device=motion_device,
                entry_id=config_entry.entry_id,
            )
        )

    for motion2_device in session.device_helper.motion_detectors2:
        if device_excluded(motion2_device, config_entry.options):
            continue
        await async_migrate_to_new_unique_id(
            hass, Platform.BINARY_SENSOR, device=motion2_device
        )
        entities.append(
            MotionDetectionSensor(
                hass=hass,
                device=motion2_device,
                entry_id=config_entry.entry_id,
            )
        )
        await async_migrate_to_new_unique_id(
            hass, Platform.BINARY_SENSOR, device=motion2_device, attr_name="Occupancy"
        )
        entities.append(
            OccupancyDetectionSensor(
                device=motion2_device,
                entry_id=config_entry.entry_id,
            )
        )
        entities.append(
            TamperSensor(
                device=motion2_device,
                entry_id=config_entry.entry_id,
            )
        )

    for smoke_device in session.device_helper.smoke_detectors:
        if device_excluded(smoke_device, config_entry.options):
            continue
        await async_migrate_to_new_unique_id(
            hass, Platform.BINARY_SENSOR, device=smoke_device
        )
        entities.append(
            SmokeDetectorSensor(
                device=smoke_device,
                hass=hass,
                entry_id=config_entry.entry_id,
            )
        )

    smoke_detection_system = session.device_helper.smoke_detection_system
    if smoke_detection_system and not device_excluded(
        smoke_detection_system, config_entry.options
    ):
        entities.append(
            SmokeDetectionSystemSensor(
                device=smoke_detection_system,
                hass=hass,
                entry_id=config_entry.entry_id,
            )
        )

    # The tracker/per-Twinguard alarm sensors below are independent of
    # smoke_detection_system's own exclusion state: that flag only controls
    # whether the (system-level) SmokeDetectionSystemSensor entity above is
    # created, not whether the virtual device may still be used as the
    # message source for the individually-exclusion-checked Twinguards.
    # Nesting this under the exclusion check above previously meant
    # excluding smoke_detection_system silently dropped every Twinguard
    # alarm sensor too, even ones never excluded themselves.
    if smoke_detection_system:
        twinguards = session.device_helper.twinguards
        if twinguards:
            tracker = TwinguardAlarmTracker(
                session=session,
                smoke_detection_system=smoke_detection_system,
                hass=hass,
            )
            # Initial refresh (async; awaits get_messages on the loop).
            await tracker.async_refresh()

            def _cleanup_tracker() -> None:
                tracker.teardown()

            config_entry.async_on_unload(_cleanup_tracker)
            # async_listen_once returns an unsubscribe callable; register it so the
            # listener is removed on config-entry reload (prevents closure leak).
            config_entry.async_on_unload(
                hass.bus.async_listen_once(
                    EVENT_HOMEASSISTANT_STOP, lambda _: tracker.teardown()
                )
            )

            for twinguard_device in twinguards:
                if device_excluded(twinguard_device, config_entry.options):
                    continue
                entities.append(
                    TwinguardSmokeAlarmSensor(
                        device=twinguard_device,
                        entry_id=config_entry.entry_id,
                        tracker=tracker,
                    )
                )

    for water_leak_device in session.device_helper.water_leakage_detectors:
        if device_excluded(water_leak_device, config_entry.options):
            continue
        await async_migrate_to_new_unique_id(
            hass, Platform.BINARY_SENSOR, device=water_leak_device
        )
        entities.append(
            WaterLeakageDetectorSensor(
                device=water_leak_device,
                entry_id=config_entry.entry_id,
            )
        )

    for shutter2_device in session.device_helper.shutter_contacts2:
        if device_excluded(shutter2_device, config_entry.options):
            continue
        if isinstance(shutter2_device, SHCShutterContact2Plus):
            entities.append(
                ShutterContactVibrationSensor(
                    device=shutter2_device,
                    entry_id=config_entry.entry_id,
                )
            )

    for battery_device in (
        list(session.device_helper.motion_detectors)
        + list(session.device_helper.motion_detectors2)
        + list(session.device_helper.shutter_contacts)
        + list(session.device_helper.shutter_contacts2)
        + list(session.device_helper.smoke_detectors)
        + list(session.device_helper.thermostats)
        + list(session.device_helper.twinguards)
        + list(session.device_helper.universal_switches)
        + list(session.device_helper.wallthermostats)
        + list(session.device_helper.roomthermostats)
        + list(session.device_helper.water_leakage_detectors)
        + list(getattr(session.device_helper, "outdoor_sirens", []))
    ):
        if device_excluded(battery_device, config_entry.options):
            continue
        await async_migrate_to_new_unique_id(
            hass, Platform.BINARY_SENSOR, device=battery_device, attr_name="Battery"
        )
        if battery_device.supports_batterylevel:
            entities.append(
                BatterySensor(
                    device=battery_device,
                    entry_id=config_entry.entry_id,
                )
            )

    # Room-climate "call for heat" (#205): expose RoomClimateControl.has_demand
    # as a binary_sensor so automations can see when a room is requesting heat.
    for climate in session.device_helper.climate_controls:
        if device_excluded(climate, config_entry.options):
            continue
        try:
            room_name = session.room(climate.room_id).name
        except (KeyError, AttributeError):
            room_name = None
        entities.append(
            CallForHeatSensor(
                device=climate,
                entry_id=config_entry.entry_id,
                device_name=room_name,
            )
        )
        entities.append(
            ScheduleOverrideActiveSensor(
                device=climate,
                entry_id=config_entry.entry_id,
                device_name=room_name,
            )
        )

    for siren in getattr(session.device_helper, "outdoor_sirens", []):
        if device_excluded(siren, config_entry.options):
            continue
        entities.append(
            SirenAcousticAlarmSensor(device=siren, entry_id=config_entry.entry_id)
        )
        entities.append(
            SirenVisualAlarmSensor(device=siren, entry_id=config_entry.entry_id)
        )
        entities.append(SirenTamperSensor(device=siren, entry_id=config_entry.entry_id))
        if getattr(siren, "supports_power_supply", False):
            entities.append(
                SirenAcDcErrorSensor(device=siren, entry_id=config_entry.entry_id)
            )
            entities.append(
                SirenBatteryDefectSensor(device=siren, entry_id=config_entry.entry_id)
            )
            entities.append(
                SirenBatteryTemperatureAbnormalSensor(
                    device=siren, entry_id=config_entry.entry_id
                )
            )
            entities.append(
                SirenPrimaryPowerSupplyOutageSensor(
                    device=siren, entry_id=config_entry.entry_id
                )
            )

    # Shutter Control II diagnostic field (hass audit): surfaces whether the
    # device still needs its end-position calibration run, mirroring the
    # app's own "recalibrate" prompt and pairing with the recalibration
    # button (button.py ShutterRecalibrateButton).
    for shutter in (
        list(getattr(session.device_helper, "shutter_controls", []))
        + list(getattr(session.device_helper, "micromodule_shutter_controls", []))
        + list(getattr(session.device_helper, "micromodule_blinds", []))
    ):
        if device_excluded(shutter, config_entry.options):
            continue
        entities.append(
            ShutterCalibrationRequiredSensor(
                device=shutter, entry_id=config_entry.entry_id
            )
        )

    platform = entity_platform.current_platform.get()
    # current_platform is only unset outside of an active platform setup context,
    # which cannot happen here — this function only ever runs as the platform's
    # own async_setup_entry, so the context var is always populated.
    assert platform is not None
    platform.async_register_entity_service(
        SERVICE_SMOKEDETECTOR_CHECK,
        {},
        "async_request_smoketest",
    )
    platform.async_register_entity_service(
        SERVICE_SMOKEDETECTOR_ALARMSTATE,
        {
            vol.Required(ATTR_COMMAND): cv.string,
        },
        "async_request_alarmstate",
    )

    if entities:
        async_add_entities(entities)


@dataclass(frozen=True, kw_only=True)
class SHCBinarySensorEntityDescription[_DeviceT: SHCDevice](
    BinarySensorEntityDescription
):
    """Describes a plain, read-only SHC binary sensor.

    Core-prep note: this is the shared shape every simple (non-event,
    non-stateful) binary sensor class below is expressed through --
    subclasses assign ``entity_description`` as a CLASS attribute (not a
    constructor argument) so each class keeps its own name/identity (needed
    for existing isinstance()/direct-construction test coverage and for
    unambiguous entity-registry history), while the actual varying
    is_on/attribute-read logic lives in one place. ``unique_id_suffix=None``
    keeps the SHCEntity-default ``{root}_{id}`` unique_id (only
    WaterLeakageDetectorSensor relies on that, matching pre-refactor
    behavior).

    Generic over ``_DeviceT`` (PEP 695) so each concrete description instance
    can be parametrized with the actual boschshcpy device/model subtype its
    ``is_on_fn``/``attributes_fn`` lambdas rely on, instead of every lambda
    body reaching for subtype-specific attributes through the generic
    ``SHCDevice`` base (mypy-strict core-prep).
    """

    is_on_fn: Callable[[_DeviceT], bool]
    attributes_fn: Callable[[_DeviceT], dict[str, Any]] | None = None
    unique_id_suffix: str | None = None


class SHCBinarySensor[_DeviceT: SHCDevice](SHCEntity, BinarySensorEntity):  # type: ignore[misc]
    """Base for a plain read-only SHC binary sensor driven by entity_description."""

    entity_description: SHCBinarySensorEntityDescription[_DeviceT]
    _device: _DeviceT

    def __init__(
        self, device: _DeviceT, entry_id: str, device_name: str | None = None
    ) -> None:
        """Initialize, deriving unique_id from entity_description.unique_id_suffix."""
        super().__init__(device, entry_id)
        self._device_name_override = device_name
        if self.entity_description.unique_id_suffix is not None:
            self._attr_unique_id = (
                f"{device.root_device_id}_{device.id}_"
                f"{self.entity_description.unique_id_suffix}"
            )

    @property
    def device_name(self) -> str:
        """Name of the device (overridable).

        Some virtual devices' own raw name is a generic placeholder shared
        by every entity attached to that same device; whichever platform's
        registry write lands last would otherwise win, see hass#372.
        """
        if self._device_name_override is not None:
            return self._device_name_override
        return super().device_name

    @property
    def is_on(self) -> bool:
        """Return the state of the sensor."""
        return self.entity_description.is_on_fn(self._device)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the state attributes, if this sensor defines any."""
        if self.entity_description.attributes_fn is None:
            return None
        return self.entity_description.attributes_fn(self._device)


_CALL_FOR_HEAT_DESCRIPTION = SHCBinarySensorEntityDescription[SHCClimateControl](
    key="call_for_heat",
    is_on_fn=lambda device: bool(getattr(device, "has_demand", False)),
    unique_id_suffix="callforheat",
)


class CallForHeatSensor(SHCBinarySensor[SHCClimateControl]):  # type: ignore[misc]
    """Room-climate 'call for heat' sensor — on when the room requests heat.

    Reads RoomClimateControl.has_demand (#205). getattr-guarded so it tolerates
    an older boschshcpy without the property (degrades to off rather than crash).
    """

    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_translation_key = "call_for_heat"
    entity_description = _CALL_FOR_HEAT_DESCRIPTION


_SCHEDULE_OVERRIDE_ACTIVE_DESCRIPTION = SHCBinarySensorEntityDescription[
    SHCClimateControl
](
    key="schedule_override_active",
    is_on_fn=lambda device: bool(
        getattr(device, "setpoint_temperature_offset_active", False)
    ),
    unique_id_suffix="schedule_override_active",
)


class ScheduleOverrideActiveSensor(SHCBinarySensor[SHCClimateControl]):  # type: ignore[misc]
    """Room-climate schedule-override indicator (hass#120 audit).

    Reads RoomClimateControl.setpoint_temperature_offset_active — the app's
    "manual override of the schedule is active" indicator
    (RoomClimateControlSetpointAndCurrentTemperatureFragment.showTemperature
    OffsetActive / dashboard tile showOffsetApplied), never read before this
    audit. getattr-guarded so it tolerates an older boschshcpy pin.
    """

    _attr_translation_key = "schedule_override_active"
    entity_description = _SCHEDULE_OVERRIDE_ACTIVE_DESCRIPTION


_SHUTTER_CALIBRATION_REQUIRED_DESCRIPTION = SHCBinarySensorEntityDescription[
    SHCShutterControl
](
    key="shutter_calibration_required",
    is_on_fn=lambda device: not bool(getattr(device, "calibrated", True)),
    unique_id_suffix="calibration_required",
)


class ShutterCalibrationRequiredSensor(SHCBinarySensor[SHCShutterControl]):  # type: ignore[misc]
    """Shutter Control II: end-position calibration missing (hass audit).

    Reads ShutterControl.calibrated — the app surfaces an uncalibrated
    shutter as needing a manual recalibration run (see button.py
    ShutterRecalibrateButton). Defaults to "no problem" if the attribute is
    absent (older boschshcpy pin), matching this file's other getattr-guarded
    audit sensors.
    """

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "shutter_calibration_required"
    entity_description = _SHUTTER_CALIBRATION_REQUIRED_DESCRIPTION


_SIREN_ACOUSTIC_ALARM_DESCRIPTION = SHCBinarySensorEntityDescription[SHCOutdoorSiren](
    key="siren_acoustic_alarm",
    is_on_fn=lambda device: bool(getattr(device.siren, "acoustic_alarm_on", False)),
    unique_id_suffix="acoustic_alarm",
)


class SirenAcousticAlarmSensor(SHCBinarySensor[SHCOutdoorSiren]):  # type: ignore[misc]
    """Outdoor Siren: acoustic alarm active (read-only, #120)."""

    _attr_device_class = BinarySensorDeviceClass.SOUND
    _attr_translation_key = "siren_acoustic_alarm"
    entity_description = _SIREN_ACOUSTIC_ALARM_DESCRIPTION


_SIREN_VISUAL_ALARM_DESCRIPTION = SHCBinarySensorEntityDescription[SHCOutdoorSiren](
    key="siren_visual_alarm",
    is_on_fn=lambda device: bool(getattr(device.siren, "visual_alarm_on", False)),
    unique_id_suffix="visual_alarm",
)


class SirenVisualAlarmSensor(SHCBinarySensor[SHCOutdoorSiren]):  # type: ignore[misc]
    """Outdoor Siren: visual (flash) alarm active (read-only, #120)."""

    _attr_device_class = BinarySensorDeviceClass.LIGHT
    _attr_translation_key = "siren_visual_alarm"
    entity_description = _SIREN_VISUAL_ALARM_DESCRIPTION


_SIREN_TAMPER_DESCRIPTION = SHCBinarySensorEntityDescription[SHCOutdoorSiren](
    key="siren_tamper",
    is_on_fn=lambda device: bool(getattr(device.siren, "tamper_activated", False)),
    unique_id_suffix="tamper",
)


class SirenTamperSensor(SHCBinarySensor[SHCOutdoorSiren]):  # type: ignore[misc]
    """Outdoor Siren: tamper detected (read-only, #120)."""

    _attr_device_class = BinarySensorDeviceClass.TAMPER
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "siren_tamper"
    entity_description = _SIREN_TAMPER_DESCRIPTION


_SIREN_AC_DC_ERROR_DESCRIPTION = SHCBinarySensorEntityDescription[SHCOutdoorSiren](
    key="siren_ac_dc_error",
    is_on_fn=lambda device: bool(getattr(device.power_supply, "ac_dc_error", False)),
    unique_id_suffix="ac_dc_error",
)


class SirenAcDcErrorSensor(SHCBinarySensor[SHCOutdoorSiren]):  # type: ignore[misc]
    """Outdoor Siren: AC/DC power-supply fault (read-only, #120)."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "siren_ac_dc_error"
    entity_description = _SIREN_AC_DC_ERROR_DESCRIPTION


_SIREN_BATTERY_DEFECT_DESCRIPTION = SHCBinarySensorEntityDescription[SHCOutdoorSiren](
    key="siren_battery_defect",
    is_on_fn=lambda device: bool(getattr(device.power_supply, "battery_defect", False)),
    unique_id_suffix="battery_defect",
)


class SirenBatteryDefectSensor(SHCBinarySensor[SHCOutdoorSiren]):  # type: ignore[misc]
    """Outdoor Siren: battery defect (read-only, #120)."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "siren_battery_defect"
    entity_description = _SIREN_BATTERY_DEFECT_DESCRIPTION


_SIREN_BATTERY_TEMPERATURE_ABNORMAL_DESCRIPTION = SHCBinarySensorEntityDescription[
    SHCOutdoorSiren
](
    key="siren_battery_temperature_abnormal",
    is_on_fn=lambda device: bool(
        getattr(device.power_supply, "battery_temperature_abnormal", False)
    ),
    unique_id_suffix="battery_temperature_abnormal",
)


class SirenBatteryTemperatureAbnormalSensor(  # type: ignore[misc]
    SHCBinarySensor[SHCOutdoorSiren]
):
    """Outdoor Siren: battery temperature abnormal (read-only, #120)."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "siren_battery_temperature_abnormal"
    entity_description = _SIREN_BATTERY_TEMPERATURE_ABNORMAL_DESCRIPTION


_SIREN_PRIMARY_POWER_SUPPLY_OUTAGE_DESCRIPTION = SHCBinarySensorEntityDescription[
    SHCOutdoorSiren
](
    key="siren_primary_power_supply_outage",
    is_on_fn=lambda device: bool(
        getattr(device.power_supply, "primary_power_supply_outage", False)
    ),
    unique_id_suffix="primary_power_supply_outage",
)


class SirenPrimaryPowerSupplyOutageSensor(  # type: ignore[misc]
    SHCBinarySensor[SHCOutdoorSiren]
):
    """Outdoor Siren: primary (mains) power supply outage (read-only, #120)."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "siren_primary_power_supply_outage"
    entity_description = _SIREN_PRIMARY_POWER_SUPPLY_OUTAGE_DESCRIPTION


@dataclass(frozen=True, kw_only=True)
class SHCShutterContactSensorEntityDescription(BinarySensorEntityDescription):
    """Describes a SHC shutter contact binary sensor."""

    is_on_fn: Callable[[SHCShutterContact], bool]


SHUTTER_CONTACT_DESCRIPTION = SHCShutterContactSensorEntityDescription(
    key="shutter_contact",
    is_on_fn=lambda device: bool(device.state is ShutterContactService.State.OPEN),
)


class ShutterContactSensor(SHCEntity, BinarySensorEntity):  # type: ignore[misc]
    """Representation of a SHC shutter contact sensor."""

    entity_description: SHCShutterContactSensorEntityDescription
    _device: SHCShutterContact

    def __init__(
        self,
        device: SHCShutterContact,
        entry_id: str,
        entity_description: SHCShutterContactSensorEntityDescription,
    ) -> None:
        """Initialize an SHC shutter contact sensor."""
        self.entity_description = entity_description
        super().__init__(device, entry_id)

    @property
    def is_on(self) -> bool:
        """Return the state of the sensor."""
        return self.entity_description.is_on_fn(self._device)

    @property
    def device_class(self) -> BinarySensorDeviceClass:
        """Return the class of this device."""
        switcher = {
            "ENTRANCE_DOOR": BinarySensorDeviceClass.DOOR,
            "REGULAR_WINDOW": BinarySensorDeviceClass.WINDOW,
            "FRENCH_WINDOW": BinarySensorDeviceClass.DOOR,
            "GENERIC": BinarySensorDeviceClass.WINDOW,
        }
        return switcher.get(
            self._device.device_class or "GENERIC", BinarySensorDeviceClass.WINDOW
        )


_VIBRATION_DESCRIPTION = SHCBinarySensorEntityDescription[SHCShutterContact2Plus](
    key="vibration",
    is_on_fn=lambda device: bool(
        device.vibrationsensor is VibrationSensorService.State.VIBRATION_DETECTED
    ),
    unique_id_suffix="vibration",
)


class ShutterContactVibrationSensor(SHCBinarySensor[SHCShutterContact2Plus]):  # type: ignore[misc]
    """Representation of a SHC shutter contact vibration sensor."""

    _attr_device_class = BinarySensorDeviceClass.VIBRATION
    _attr_translation_key = "vibration"
    entity_description = _VIBRATION_DESCRIPTION


class MotionDetectionSensor(SHCEntity, BinarySensorEntity):  # type: ignore[misc]
    """Representation of a SHC motion detection sensor."""

    _attr_device_class = BinarySensorDeviceClass.MOTION
    _unrecorded_attributes = frozenset({"last_motion_detected"})
    _device: SHCMotionDetector | SHCMotionDetector2

    def __init__(
        self,
        hass: HomeAssistant,
        device: SHCMotionDetector | SHCMotionDetector2,
        entry_id: str,
    ) -> None:
        """Initialize the motion detection device."""
        self.hass = hass
        self._service = None
        self._cached_device_id: str | None = None
        # Guard against phantom events on poll-id resubscribe (~24 h): the SHC
        # re-delivers every service's current state, which must not re-fire as a
        # fresh MOTION event.  Cache the last latestmotion timestamp we fired on;
        # skip the event when the timestamp is unchanged (replay), fire when it
        # advances (genuine new motion).
        self._last_fired_latestmotion: str | None = None
        self._ha_stop_unsub: Callable[[], None] | None = None
        super().__init__(device=device, entry_id=entry_id)
        # Seed from current state so the first snapshot re-delivered after an HA
        # restart / config-entry reload is a baseline, not a phantom MOTION (the
        # restart counterpart of the 24 h resubscribe replay, #336).
        self._last_fired_latestmotion = self._device.latestmotion

        for service in self._device.device_services:
            if service.id == "LatestMotion":
                self._service = service
                break

        self._ha_stop_unsub = hass.bus.async_listen_once(
            EVENT_HOMEASSISTANT_STOP, self._handle_ha_stop
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to SHC events and cache device_id."""
        await super().async_added_to_hass()
        self._cached_device_id = await async_get_device_id(self.hass, self._device.id)
        # Subscribe AFTER device_id is cached so events never fire with
        # device_id=None during the startup window (#288-cluster).
        if self._service is not None:
            self._service.subscribe_callback(
                self._device.id + "_eventlistener", self._input_events_handler
            )

    async def async_will_remove_from_hass(self) -> None:
        """Unsubscribe service callback and cancel HA-stop listener on entity removal."""
        if self._ha_stop_unsub is not None:
            self._ha_stop_unsub()
            self._ha_stop_unsub = None
        if self._service is not None:
            self._service.unsubscribe_callback(self._device.id + "_eventlistener")
        await super().async_will_remove_from_hass()

    def _input_events_handler(self) -> None:
        """Handle device input events.

        Replay-guard (#336): on the ~24 h poll-id resubscribe the SHC
        re-delivers the last LatestMotion state unchanged.  Only fire when
        latestmotion has advanced past the last value we fired on.
        """
        current_ts = self._device.latestmotion
        if current_ts == self._last_fired_latestmotion:
            LOGGER.debug(
                "Skipping replayed LatestMotion event for %s (ts=%s unchanged)",
                self._device.name,
                current_ts,
            )
            return
        self._last_fired_latestmotion = current_ts
        self.hass.bus.async_fire(
            EVENT_BOSCH_SHC,
            {
                ATTR_DEVICE_ID: self._cached_device_id,
                ATTR_ID: self._device.id,
                ATTR_NAME: self._device.name,
                ATTR_LAST_TIME_TRIGGERED: current_ts,
                ATTR_EVENT_TYPE: "MOTION",
                ATTR_EVENT_SUBTYPE: "",
            },
        )

    @callback  # type: ignore[untyped-decorator]
    def _handle_ha_stop(self, _: Any) -> None:
        """Handle Home Assistant stopping."""
        self._ha_stop_unsub = None
        LOGGER.debug(
            "Stopping motion detection event listener for %s", self._device.name
        )
        if self._service is not None:
            self._service.unsubscribe_callback(self._device.id + "_eventlistener")

    @property
    def is_on(self) -> bool:
        """Return the state of the sensor."""
        try:
            latestmotion = datetime.strptime(
                self._device.latestmotion, "%Y-%m-%dT%H:%M:%S.%fZ"
            ).replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            # ValueError: unparseable timestamp; TypeError: latestmotion is None.
            # The trailing literal "Z" makes strptime return a naive datetime, so
            # it must be marked UTC-aware to subtract from datetime.now(timezone.utc).
            return False

        elapsed = datetime.now(timezone.utc) - latestmotion
        if elapsed > timedelta(seconds=4 * 60):
            return False
        return True

    @property
    def should_poll(self) -> bool:
        """Retrieve motion state."""
        return True

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        return {
            "last_motion_detected": self._device.latestmotion,
        }


class SmokeDetectorSensor(SHCEntity, BinarySensorEntity):  # type: ignore[misc]
    """Representation of a SHC smoke detector sensor."""

    _attr_device_class = BinarySensorDeviceClass.SMOKE

    def __init__(
        self,
        device: SHCSmokeDetector,
        hass: HomeAssistant,
        entry_id: str,
    ):
        """Initialize the smoke detector device."""
        self._hass = hass
        self._service = None
        self._cached_device_id: str | None = None
        # Guard against phantom events on poll-id resubscribe (#336): cache the
        # last alarmstate name we fired on and skip when it is unchanged.
        self._last_fired_alarmstate: str | None = None
        super().__init__(device=device, entry_id=entry_id)
        self._device: SHCSmokeDetector = device  # type: ignore[assignment]
        # Seed from current state so the first snapshot re-delivered after an HA
        # restart / config-entry reload is a baseline, not a phantom ALARM event
        # (the restart counterpart of the 24 h resubscribe replay, #336).
        try:
            self._last_fired_alarmstate = self._device.alarmstate.name
        except (ValueError, KeyError):
            self._last_fired_alarmstate = None

        for service in self._device.device_services:
            if service.id == "Alarm":
                self._service = service
                break

        self._ha_stop_unsub: Callable[[], None] | None = hass.bus.async_listen_once(
            EVENT_HOMEASSISTANT_STOP, self._handle_ha_stop
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to SHC events and cache device_id."""
        await super().async_added_to_hass()
        self._cached_device_id = await async_get_device_id(self._hass, self._device.id)
        # Subscribe AFTER device_id is cached so events never fire with
        # device_id=None during the startup window (#288-cluster).
        if self._service is not None:
            self._service.subscribe_callback(
                self._device.id + "_eventlistener", self._input_events_handler
            )

    async def async_will_remove_from_hass(self) -> None:
        """Unsubscribe service callback and cancel HA-stop listener."""
        if self._service is not None:
            self._service.unsubscribe_callback(self._device.id + "_eventlistener")
        if self._ha_stop_unsub is not None:
            self._ha_stop_unsub()
            self._ha_stop_unsub = None
        await super().async_will_remove_from_hass()

    def _input_events_handler(self) -> None:
        """Handle device input events.

        Replay-guard (#336): on the ~24 h poll-id resubscribe the SHC
        re-delivers the last Alarm state unchanged.  Only fire when the
        alarmstate name has changed since the last fired event.
        """
        try:
            current_state = self._device.alarmstate.name
        except (ValueError, KeyError):
            LOGGER.warning("Unexpected alarmstate value for %s", self._device.name)
            return
        if current_state == self._last_fired_alarmstate:
            LOGGER.debug(
                "Skipping replayed Alarm event for %s (state=%s unchanged)",
                self._device.name,
                current_state,
            )
            return
        self._last_fired_alarmstate = current_state
        self._hass.bus.async_fire(
            EVENT_BOSCH_SHC,
            {
                ATTR_DEVICE_ID: self._cached_device_id,
                ATTR_ID: self._device.id,
                ATTR_NAME: self._device.name,
                ATTR_EVENT_TYPE: "ALARM",
                ATTR_EVENT_SUBTYPE: current_state,
            },
        )

    @callback  # type: ignore[untyped-decorator]
    def _handle_ha_stop(self, _: Any) -> None:
        """Handle Home Assistant stopping."""
        self._ha_stop_unsub = None
        LOGGER.debug("Stopping alarm event listener for %s", self._device.name)
        if self._service is not None:
            self._service.unsubscribe_callback(self._device.id + "_eventlistener")

    @property
    def is_on(self) -> bool:
        """Return the state of the sensor."""
        # Only PRIMARY_ALARM and SECONDARY_ALARM are smoke-related states.
        # INTRUSION_ALARM is set by the IDS (intrusion detection system) on all
        # smoke detectors when a surveillance alarm fires — it must NOT be treated
        # as a smoke event, or every detector reports smoke whenever any burglar
        # alarm triggers (issue #191).
        try:
            state = self._device.alarmstate
        except (KeyError, ValueError):
            return False
        return state in (
            AlarmService.State.PRIMARY_ALARM,
            AlarmService.State.SECONDARY_ALARM,
        )

    @property
    def icon(self) -> str:
        """Return the icon of the sensor."""
        return "mdi:smoke-detector"

    async def async_request_smoketest(self) -> None:
        """Request smokedetector test."""
        LOGGER.debug("Requesting smoke test on device %s", self._device.name)
        try:
            await self._device.async_smoketest_requested()
        except SHCException as err:
            raise HomeAssistantError(
                f"Smoke test request failed for {self._device.name}: {err}",
                translation_domain=DOMAIN,
                translation_key="smoke_test_failed",
            ) from err

    async def async_request_alarmstate(self, command: str) -> None:
        """Request smokedetector alarm state."""
        LOGGER.debug(
            "Requesting custom alarm state %s on device %s", command, self._device.name
        )
        try:
            await self._device.async_set_alarmstate(command)
        except SHCException as err:
            raise HomeAssistantError(
                f"Set alarm state failed for {self._device.name}: {err}",
                translation_domain=DOMAIN,
                translation_key="alarm_state_failed",
            ) from err

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        try:
            check_state = self._device.smokedetectorcheck_state.name
        except ValueError as err:
            LOGGER.warning(
                "Unknown smokedetectorcheck_state for %s: %s", self._device.name, err
            )
            check_state = None
        try:
            alarm_state = self._device.alarmstate.name
        except ValueError as err:
            LOGGER.warning("Unknown alarmstate for %s: %s", self._device.name, err)
            alarm_state = None
        return {
            "smokedetectorcheck_state": check_state,
            "alarmstate": alarm_state,
        }


_WATER_LEAKAGE_DESCRIPTION = SHCBinarySensorEntityDescription[SHCWaterLeakageSensor](
    key="water_leakage",
    is_on_fn=lambda device: bool(
        device.leakage_state is not WaterLeakageSensorService.State.NO_LEAKAGE
    ),
    attributes_fn=lambda device: {
        "push_notification_state": device.push_notification_state.name,
        "acoustic_signal_state": device.acoustic_signal_state.name,
    },
    # unique_id_suffix intentionally omitted: keeps the pre-refactor default
    # SHCEntity unique_id (`{root}_{id}`, no suffix).
)


class WaterLeakageDetectorSensor(SHCBinarySensor[SHCWaterLeakageSensor]):  # type: ignore[misc]
    """Representation of a SHC water leakage detector sensor."""

    _attr_device_class = BinarySensorDeviceClass.MOISTURE
    _attr_icon = "mdi:water-alert"
    entity_description = _WATER_LEAKAGE_DESCRIPTION


class SmokeDetectionSystemSensor(SHCEntity, BinarySensorEntity):  # type: ignore[misc]
    """Representation of a SHC smoke detection system sensor."""

    _attr_device_class = BinarySensorDeviceClass.SMOKE

    def __init__(
        self,
        device: SHCSmokeDetectionSystem,
        hass: HomeAssistant,
        entry_id: str,
    ):
        """Initialize the smoke detection system device."""
        self._hass = hass
        self._service = None
        self._cached_device_id: str | None = None
        # Guard against phantom events on poll-id resubscribe (#336): cache the
        # last SurveillanceAlarm state name we fired on and skip when unchanged.
        self._last_fired_alarm: str | None = None
        super().__init__(device=device, entry_id=entry_id)
        self._device: SHCSmokeDetectionSystem = device  # type: ignore[assignment]
        # Seed from current state so the first snapshot re-delivered after an HA
        # restart / config-entry reload is a baseline, not a phantom ALARM event
        # (the restart counterpart of the 24 h resubscribe replay, #336).
        try:
            self._last_fired_alarm = self._device.alarm.name
        except (ValueError, KeyError):
            self._last_fired_alarm = None

        for service in self._device.device_services:
            if service.id == "SurveillanceAlarm":
                self._service = service
                break

        self._ha_stop_unsub: Callable[[], None] | None = hass.bus.async_listen_once(
            EVENT_HOMEASSISTANT_STOP, self._handle_ha_stop
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to SHC events and cache device_id."""
        await super().async_added_to_hass()
        self._cached_device_id = await async_get_device_id(self._hass, self._device.id)
        # Subscribe AFTER device_id is cached so events never fire with
        # device_id=None during the startup window (#288-cluster).
        if self._service is not None:
            self._service.subscribe_callback(
                self._device.id + "_eventlistener", self._input_events_handler
            )

    async def async_will_remove_from_hass(self) -> None:
        """Unsubscribe service callback and cancel HA-stop listener."""
        if self._service is not None:
            self._service.unsubscribe_callback(self._device.id + "_eventlistener")
        if self._ha_stop_unsub is not None:
            self._ha_stop_unsub()
            self._ha_stop_unsub = None
        await super().async_will_remove_from_hass()

    def _input_events_handler(self) -> None:
        """Handle device input events.

        Replay-guard (#336): on the ~24 h poll-id resubscribe the SHC
        re-delivers the last SurveillanceAlarm state unchanged.  Only fire
        when the alarm state name has changed since the last fired event.
        """
        try:
            current_alarm = self._device.alarm.name
        except (ValueError, KeyError):
            LOGGER.warning("Unexpected alarm value for %s", self._device.name)
            return
        if current_alarm == self._last_fired_alarm:
            LOGGER.debug(
                "Skipping replayed SurveillanceAlarm event for %s (state=%s unchanged)",
                self._device.name,
                current_alarm,
            )
            return
        self._last_fired_alarm = current_alarm
        self._hass.bus.async_fire(
            EVENT_BOSCH_SHC,
            {
                ATTR_DEVICE_ID: self._cached_device_id,
                ATTR_ID: self._device.id,
                ATTR_NAME: self._device.name,
                ATTR_EVENT_TYPE: "ALARM",
                ATTR_EVENT_SUBTYPE: current_alarm,
            },
        )

    @callback  # type: ignore[untyped-decorator]
    def _handle_ha_stop(self, _: Any) -> None:
        """Handle Home Assistant stopping."""
        self._ha_stop_unsub = None
        LOGGER.debug("Stopping alarm event listener for %s", self._device.name)
        if self._service is not None:
            self._service.unsubscribe_callback(self._device.id + "_eventlistener")

    @property
    def is_on(self) -> bool:
        """Return the state of the sensor."""
        return bool(self._device.alarm is not SurveillanceAlarmService.State.ALARM_OFF)

    @property
    def icon(self) -> str:
        """Return the icon of the sensor."""
        return "mdi:smoke-detector"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        return {
            "alarm_state": self._device.alarm.name,
        }


class TwinguardAlarmTracker:
    """Track which Twinguard device(s) are actively triggering a smoke alarm.

    The SHC does not expose per-device alarm state directly on the Twinguard.
    Instead the shared SMOKE_DETECTION_SYSTEM fires a SurveillanceAlarm callback
    and the /messages endpoint carries SMOKE_ALARM messages whose
    ``arguments.surveillanceEvents[].triggerId`` maps back to the individual
    Twinguard device id.

    ``refresh()`` does blocking HTTP and MUST NOT be called from the event loop.
    Callbacks fire from the async polling loop on the event loop; listeners are
    called directly without call_soon_threadsafe marshalling.
    """

    def __init__(
        self,
        session: SHCSession,
        smoke_detection_system: SHCSmokeDetectionSystem,
        hass: HomeAssistant,
    ) -> None:
        """Initialize the tracker (no I/O; call refresh() separately)."""
        self._session = session
        self._smoke_detection_system = smoke_detection_system
        self._hass = hass
        self._service = None
        self._listeners: list[tuple[HomeAssistant, Callable[[], None]]] = []
        self._active_trigger_ids: set[str] = set()
        self._last_alarm_state: str | None = None
        self._torn_down = False
        # Monotonic counter to detect a newer async_refresh() superseding an
        # in-flight one — see async_refresh().
        self._refresh_generation = 0

        for service in self._smoke_detection_system.device_services:
            if service.id == "SurveillanceAlarm":
                self._service = service
                self._service.subscribe_callback(
                    self._smoke_detection_system.id + "_twinguard_alarm_listener",
                    self._handle_alarm_update,
                )
                break

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def alarm_state(self) -> str | None:
        """Return the global surveillance alarm state name."""
        try:
            return str(self._smoke_detection_system.alarm.name)
        except ValueError as err:
            LOGGER.warning(
                "Unknown smoke detection system alarm state for %s: %s",
                self._smoke_detection_system.name,
                err,
            )
            return None

    def register_listener(
        self, hass: HomeAssistant, listener: Callable[[], None]
    ) -> None:
        """Register a listener (called from event loop via async_added_to_hass)."""
        self._listeners.append((hass, listener))

    def unregister_listener(self, listener: Callable[[], None]) -> None:
        """Unregister a listener by the listener callable."""
        self._listeners = [(h, cb) for (h, cb) in self._listeners if cb is not listener]

    def is_alarm_active_for(self, device_id: str) -> bool:
        """Return whether a smoke alarm is active for the given Twinguard device id."""
        return device_id in self._active_trigger_ids

    async def async_refresh(self) -> None:
        """Refresh active trigger ids from the SHC (async; on the event loop).

        Safe to call multiple times; skips notification if state did not change.

        Concurrency: _handle_alarm_update() fires a new task per
        SurveillanceAlarm callback with no de-dup — a burst of updates (e.g.
        multiple Twinguards, or an ON immediately followed by an OFF) can have
        two async_refresh() calls in flight at once, awaiting get_messages()
        with no ordering guarantee on which HTTP response lands first. Guard
        with a monotonic generation counter so only the most-recently-STARTED
        call's result is applied — an in-flight call whose result arrives
        after a newer call has already started is a stale/superseded read and
        is discarded rather than overwriting fresher state.
        """
        if self._torn_down:
            return
        self._refresh_generation += 1
        my_generation = self._refresh_generation
        alarm_state = self.alarm_state
        if alarm_state == SurveillanceAlarmService.State.ALARM_OFF.name:
            new_trigger_ids: set[str] = set()
        else:
            new_trigger_ids = await self._extract_trigger_ids_from_messages()

        if my_generation != self._refresh_generation:
            # A newer async_refresh() started while we were awaiting
            # get_messages() above — this result is stale, discard it.
            return

        if (
            new_trigger_ids == self._active_trigger_ids
            and alarm_state == self._last_alarm_state
        ):
            return

        self._active_trigger_ids = new_trigger_ids
        self._last_alarm_state = alarm_state
        self._notify_listeners()

    def teardown(self) -> None:
        """Unsubscribe from the SHC service and clear all listeners.

        Called on config-entry unload and on EVENT_HOMEASSISTANT_STOP.
        Idempotent — safe to call more than once.
        """
        if self._torn_down:
            return
        self._torn_down = True
        self._listeners = []
        if self._service is not None:
            self._service.unsubscribe_callback(
                self._smoke_detection_system.id + "_twinguard_alarm_listener"
            )

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _handle_alarm_update(self) -> None:
        """Handle a SurveillanceAlarm update (fired on the event loop).

        The async session fires this callback on the loop; schedule the async
        refresh (it awaits get_messages) as a task so the poll loop isn't
        blocked on the follow-up HTTP call.
        """
        self._hass.async_create_task(self.async_refresh())

    async def _extract_trigger_ids_from_messages(self) -> set[str]:
        """Extract active Twinguard trigger ids from SMOKE_ALARM messages."""
        try:
            messages = await self._session.api.get_messages()

            trigger_ids: set[str] = set()
            for message in messages:
                # Defensive: messageCode may not be a dict (malformed payload).
                message_code = message.get("messageCode", {})
                if not isinstance(message_code, dict):
                    continue
                if message_code.get("name") != "SMOKE_ALARM":
                    continue
                if message.get("sourceId") != self._smoke_detection_system.id:
                    continue

                # Defensive: arguments may not be a dict (malformed payload).
                # triggerId==device.id is assumed based on observed message shape;
                # pending rawscan confirmation (#203).
                arguments = message.get("arguments", {})
                if not isinstance(arguments, dict):
                    continue

                events = self._parse_surveillance_events(
                    arguments.get("surveillanceEvents")
                )
                # A message that contains an ALARM_OFF event signals the end of that
                # alarm cycle — skip the whole message so we don't re-add its triggers.
                if any(event.get("type") == "ALARM_OFF" for event in events):
                    continue

                for event in events:
                    trigger_id = event.get("triggerId")
                    if trigger_id:
                        trigger_ids.add(trigger_id)

        except Exception as err:  # noqa: BLE001
            LOGGER.warning("Unable to fetch Bosch SHC messages: %s", err)
            return self._active_trigger_ids

        return trigger_ids

    @staticmethod
    def _parse_surveillance_events(raw_events: Any) -> list[dict[str, Any]]:
        """Parse surveillanceEvents from a Bosch SHC message payload.

        The field may already be a list (native JSON parse) or a JSON-encoded
        string (observed in some firmware versions).
        """
        if isinstance(raw_events, list):
            return [e for e in raw_events if isinstance(e, dict)]
        if not raw_events:
            return []
        try:
            parsed = json.loads(raw_events)
        except (TypeError, json.JSONDecodeError):
            return []
        if not isinstance(parsed, list):
            return []
        return [e for e in parsed if isinstance(e, dict)]

    def _notify_listeners(self) -> None:
        """Notify all registered entity listeners."""
        for _hass, listener in list(self._listeners):
            listener()


class TwinguardSmokeAlarmSensor(SHCEntity, BinarySensorEntity):  # type: ignore[misc]
    """Per-Twinguard binary sensor: True when that device is the active smoke alarm source."""

    _attr_device_class = BinarySensorDeviceClass.SMOKE
    _attr_translation_key = "smoke"

    def __init__(
        self,
        device: SHCDevice,
        entry_id: str,
        tracker: TwinguardAlarmTracker,
    ) -> None:
        """Initialize the Twinguard smoke alarm sensor."""
        super().__init__(device=device, entry_id=entry_id)
        self._device: SHCTwinguard = device  # type: ignore[assignment]
        self._attr_unique_id = f"{device.root_device_id}_{device.id}_smoke"
        self._tracker = tracker
        self._tracker_listener = self._handle_tracker_update

    async def async_added_to_hass(self) -> None:
        """Register with tracker when entity is added to HA."""
        await super().async_added_to_hass()
        self._tracker.register_listener(self.hass, self._tracker_listener)

    async def async_will_remove_from_hass(self) -> None:
        """Unregister from tracker when entity is removed."""
        self._tracker.unregister_listener(self._tracker_listener)
        await super().async_will_remove_from_hass()

    @callback  # type: ignore[untyped-decorator]
    def _handle_tracker_update(self) -> None:
        """Called on the event loop when tracker state changes."""
        self.schedule_update_ha_state()

    @property
    def is_on(self) -> bool:
        """Return True when this Twinguard is the source of an active smoke alarm."""
        return self._tracker.is_alarm_active_for(self._device.id)

    @property
    def icon(self) -> str:
        """Return the icon of the sensor."""
        return "mdi:smoke-detector"

    async def async_request_smoketest(self) -> None:
        """Request a Twinguard smoke test."""
        LOGGER.debug("Requesting smoke test on device %s", self._device.name)
        try:
            await self._device.async_smoketest_requested()
        except SHCException as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="smoke_test_failed",
            ) from err

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        return {
            "alarm_state": self._tracker.alarm_state,
        }


def _battery_sensor_is_on(device: SHCBatteryDevice) -> bool:
    """Return the state of a battery sensor for the given device.

    Returns True (battery problem) only for LOW_BATTERY, CRITICAL_LOW, and
    CRITICALLY_LOW_BATTERY.  NOT_AVAILABLE means the device has not yet
    reported battery state — this must NOT be treated as a low-battery
    condition.
    """
    level = device.batterylevel
    battery_state = BatteryLevelService.State

    if level == battery_state.NOT_AVAILABLE:
        LOGGER.debug("Battery state of device %s is not available", device.name)
        return False

    if level == battery_state.CRITICAL_LOW:
        LOGGER.warning("Battery state of device %s is critical low", device.name)

    if level == battery_state.CRITICALLY_LOW_BATTERY:
        LOGGER.warning("Battery state of device %s is critically low", device.name)

    if level == battery_state.LOW_BATTERY:
        LOGGER.warning("Battery state of device %s is low", device.name)

    return bool(level != battery_state.OK)


_BATTERY_DESCRIPTION = SHCBinarySensorEntityDescription[SHCBatteryDevice](
    key="battery",
    is_on_fn=_battery_sensor_is_on,
    unique_id_suffix="battery",
)


class BatterySensor(SHCBinarySensor[SHCBatteryDevice]):  # type: ignore[misc]
    """Representation of a SHC battery reporting sensor."""

    _attr_device_class = BinarySensorDeviceClass.BATTERY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    entity_description = _BATTERY_DESCRIPTION


_OCCUPANCY_DESCRIPTION = SHCBinarySensorEntityDescription[SHCMotionDetector2](
    key="occupancy",
    is_on_fn=lambda device: bool(device.occupied),
    attributes_fn=lambda device: {
        "last_occupancy_change": device.last_occupancy_change_time,
    },
    unique_id_suffix="occupancy",
)


class OccupancyDetectionSensor(SHCBinarySensor[SHCMotionDetector2]):  # type: ignore[misc]
    """Representation of a SHC Motion Detector II [+M] occupancy sensor."""

    _attr_device_class = BinarySensorDeviceClass.OCCUPANCY
    _attr_translation_key = "occupancy"
    _unrecorded_attributes = frozenset({"last_occupancy_change"})
    entity_description = _OCCUPANCY_DESCRIPTION


_TAMPER_DESCRIPTION = SHCBinarySensorEntityDescription[SHCMotionDetector2](
    key="tamper",
    is_on_fn=lambda device: bool(getattr(device, "was_tampered", False)),
    attributes_fn=lambda device: {
        "last_tamper_time": getattr(device, "last_tamper_time", None),
    },
    unique_id_suffix="tamper",
)


class TamperSensor(SHCBinarySensor[SHCMotionDetector2]):  # type: ignore[misc]
    """Representation of a SHC Motion Detector II [+M] tamper sensor.

    Reports True when the device housing was opened/tampered with.
    Reads was_tampered from the LatestTamperService via the model accessor.
    """

    _attr_device_class = BinarySensorDeviceClass.TAMPER
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _unrecorded_attributes = frozenset({"last_tamper_time"})
    _attr_translation_key = "tamper"
    entity_description = _TAMPER_DESCRIPTION
