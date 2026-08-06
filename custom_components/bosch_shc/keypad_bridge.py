"""#395: bridge a device's physical detached pushbutton into a regular HA entity.

Pushes a small SHC-side automation per button (Bosch's own local rule
engine, distinct from Home Assistant's automations -- see
automation_rules_as_entities in switch.py for the read side of that same
engine) that pulses a UserDefinedState on press. The UserDefinedState is
already unconditionally exposed as a switch entity (switch.py), so its
on/off transitions are directly usable as an HA automation trigger with no
new HA platform code.

Also covers Door/Window Contact II (SWD2/SWD2_PLUS/SWD2_DUAL, hass#245/#342/
#376): those have no Keypad service, so they use a different trigger type
(ShutterContactButtonPressTrigger) with a different field shape, but the
same UserDefinedState-bridge mechanism.

Endpoints undocumented in the official OpenAPI spec; traced via APK
decompile and confirmed live against a real Controller. See
bosch-shc-api-docs/best_practice/undocumented-local-endpoints.md #10 for
the trigger/action JSON shapes this builds.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from functools import partial
from typing import Any

from boschshcpy.exceptions import SHCException
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import LOGGER
from .entity import async_remove_stale_entity, device_excluded

DATA_KEYPAD_BRIDGE_MAP = "keypad_bridge_map"

# Bumped once (#395 follow-up: wrong trigger type shipped first) to force
# existing bridge entries to be recreated rather than left stale.
_SCHEMA_VERSION = "v2"

# SWD2 has no Keypad service; ShutterContactButtonPressTrigger is its own
# type, keyed by shutterContactId + buttonPressState (see module docstring).
_SWD2_BUTTON_STATES = ("ON_SHORT_PRESS", "ON_LONG_PRESS")
_SWD2_BUTTON_STATE_SUFFIX = {"ON_SHORT_PRESS": "S", "ON_LONG_PRESS": "L"}

# DETACHED_LONG_PRESS two-button convention (cover.py, #385/#395): keycode 1/2.
_KEY_CODES = (1, 2)
# One bridge entity per (key_code, event) -- short/long must stay
# distinguishable (#395 follow-up: combining them fired the same switch).
_BUTTON_EVENTS = ("PRESS_SHORT", "PRESS_LONG")
_BUTTON_EVENT_SUFFIX = {"PRESS_SHORT": "S", "PRESS_LONG": "L"}
# Auto-reset so the switch's "turned on" transition is the trigger, not a
# sticky on state (mirrors real Bosch-app automations' delayed reset action).
_RESET_DELAY_SECONDS = 2
# UserDefinedState.name is capped at 30 chars by the Controller itself
# (live-confirmed; a longer name gets a bare 400). Automation.name has no such limit.
_UDS_NAME_MAX_LEN = 30
_UDS_NAME_SUFFIX = " Btn{}{}"  # shortest unambiguous per-button-per-event suffix


def _uds_name(device_name: str, key_code: int, button_event: str) -> str:
    suffix = _UDS_NAME_SUFFIX.format(key_code, _BUTTON_EVENT_SUFFIX[button_event])
    return device_name[: _UDS_NAME_MAX_LEN - len(suffix)] + suffix


def _swd2_uds_name(device_name: str, button_press_state: str) -> str:
    suffix = " Btn" + _SWD2_BUTTON_STATE_SUFFIX[button_press_state]
    return device_name[: _UDS_NAME_MAX_LEN - len(suffix)] + suffix


def _keypad_capable_devices(session: Any, options: Any) -> list[Any]:
    """Shading devices with a Keypad service.

    Only the shutter/blinds buckets -- confirmed via a real automation on a
    real device that this device class needs KeypadMicromoduleShadingTrigger,
    not the generic KeypadButtonPressTrigger. Light Control II is left out
    for now: it likely needs its own KeypadMicromoduleLightTrigger (a real,
    distinct type -- confirmed to exist), but its field shape is unverified,
    and guessing it risks repeating this exact bug for that device class too.
    """
    devices: list[Any] = []
    for bucket in (
        "shutter_controls",
        "micromodule_shutter_controls",
        "micromodule_blinds",
    ):
        for device in getattr(session.device_helper, bucket, []):
            if device_excluded(device, options):
                continue
            if getattr(device, "has_keypad", False):
                devices.append(device)
    return devices


def _swd2_button_devices(session: Any, options: Any) -> list[Any]:
    """Door/Window Contact II devices (SWD2/SWD2_PLUS/SWD2_DUAL)."""
    return [
        device
        for device in getattr(session.device_helper, "shutter_contacts2", [])
        if not device_excluded(device, options)
    ]


def _reset_actions(userdefinedstate_id: str) -> list[dict[str, Any]]:
    """Pulse a UserDefinedState on, then auto-reset it off.

    Shared by both trigger types below -- only the trigger itself differs
    per device class.
    """
    return [
        {
            "type": "UserDefinedStateAction",
            "delayInSeconds": 0,
            "configuration": json.dumps(
                {"stateId": userdefinedstate_id, "state": "ACTIVE"}
            ),
        },
        {
            "type": "UserDefinedStateAction",
            "delayInSeconds": _RESET_DELAY_SECONDS,
            "configuration": json.dumps(
                {"stateId": userdefinedstate_id, "state": "INACTIVE"}
            ),
        },
    ]


def _build_automation(
    name: str,
    device_id: str,
    key_code: int,
    button_event: str,
    userdefinedstate_id: str,
) -> dict[str, Any]:
    triggers = [
        {
            "type": "KeypadMicromoduleShadingTrigger",
            "configuration": json.dumps(
                {
                    "deviceId": device_id,
                    "buttonId": key_code,
                    "buttonEvent": button_event,
                }
            ),
        }
    ]
    return {
        "name": name,
        "triggers": triggers,
        "actions": _reset_actions(userdefinedstate_id),
    }


def _build_swd2_automation(
    name: str,
    device_id: str,
    button_press_state: str,
    userdefinedstate_id: str,
) -> dict[str, Any]:
    triggers = [
        {
            "type": "ShutterContactButtonPressTrigger",
            "configuration": json.dumps(
                {
                    "shutterContactId": device_id,
                    "buttonPressState": button_press_state,
                }
            ),
        }
    ]
    return {
        "name": name,
        "triggers": triggers,
        "actions": _reset_actions(userdefinedstate_id),
    }


async def _create_bridge_entry(
    session: Any,
    bridge_map: dict[str, dict[str, str]],
    key: str,
    *,
    label: str,
    uds_name: str,
    build_spec: Callable[[str], dict[str, Any]],
) -> None:
    """Create one UserDefinedState + Automation pair, recording it on success.

    Rolls back the just-created state if the automation create fails, so a
    partial failure doesn't leak an orphaned, untracked state.
    """
    try:
        userdefinedstate = await session.async_create_userdefinedstate(uds_name)
    except SHCException as err:
        LOGGER.warning("Keypad bridge: failed to create state for %s: %s", label, err)
        return
    try:
        spec = build_spec(userdefinedstate.id)
        automation = await session.async_create_automation_rule(
            spec["name"], triggers=spec["triggers"], actions=spec["actions"]
        )
    except SHCException as err:
        LOGGER.warning(
            "Keypad bridge: failed to create automation for %s: %s", label, err
        )
        try:
            await session.async_delete_userdefinedstate(userdefinedstate.id)
        except SHCException as cleanup_err:
            LOGGER.debug(
                "Keypad bridge: rollback delete failed for %s: %s", label, cleanup_err
            )
        return
    bridge_map[key] = {
        "userdefinedstate_id": userdefinedstate.id,
        "automation_id": automation.id,
    }


async def async_sync_keypad_bridge(
    hass: HomeAssistant, entry: ConfigEntry, enabled: bool
) -> None:
    """Create or tear down the keypad-bridge SHC objects to match `enabled`.

    Idempotent: only creates what's missing for currently-eligible devices,
    and removes entries for devices no longer eligible (excluded, or the
    feature turned off) using the ids persisted in `entry.data`. Best-effort
    on delete -- a manually-removed SHC object shouldn't block cleanup of
    the rest of the map.
    """
    session = entry.runtime_data.session
    bridge_map: dict[str, dict[str, str]] = dict(
        entry.data.get(DATA_KEYPAD_BRIDGE_MAP, {})
    )

    wanted_keys: set[str] = set()
    if enabled:
        for device in _keypad_capable_devices(session, entry.options):
            for key_code in _KEY_CODES:
                for button_event in _BUTTON_EVENTS:
                    wanted_keys.add(
                        f"{device.id}_{key_code}_{button_event}_{_SCHEMA_VERSION}"
                    )
        for device in _swd2_button_devices(session, entry.options):
            for button_press_state in _SWD2_BUTTON_STATES:
                wanted_keys.add(
                    f"{device.id}_swd2_{button_press_state}_{_SCHEMA_VERSION}"
                )

    # Remove entries no longer wanted (disabled, or device excluded/gone).
    mac = session.information.macAddress if bridge_map else None
    for key in list(bridge_map):
        if key in wanted_keys:
            continue
        entry_ids = bridge_map.pop(key)
        try:
            await session.async_delete_automation_rule(entry_ids["automation_id"])
        except SHCException as err:
            LOGGER.debug(
                "Keypad bridge cleanup: failed to delete automation for %s: %s",
                key,
                err,
            )
        uds_id = entry_ids["userdefinedstate_id"]
        try:
            await session.async_delete_userdefinedstate(uds_id)
        except SHCException as err:
            LOGGER.debug(
                "Keypad bridge cleanup: failed to delete state for %s: %s", key, err
            )
        # The now-deleted UserDefinedState's switch entity (switch.py) would
        # otherwise linger as a permanently "unavailable" registry ghost.
        if mac is not None:
            await async_remove_stale_entity(hass, Platform.SWITCH, f"{mac}_{uds_id}")

    # Create entries that are missing.
    if enabled:
        for device in _keypad_capable_devices(session, entry.options):
            for key_code in _KEY_CODES:
                for button_event in _BUTTON_EVENTS:
                    key = f"{device.id}_{key_code}_{button_event}_{_SCHEMA_VERSION}"
                    if key in bridge_map:
                        continue
                    label = f"{device.name} Button {key_code} {button_event}"
                    await _create_bridge_entry(
                        session,
                        bridge_map,
                        key,
                        label=label,
                        uds_name=_uds_name(device.name, key_code, button_event),
                        build_spec=partial(
                            _build_automation,
                            f"[HA] {label}",
                            device.id,
                            key_code,
                            button_event,
                        ),
                    )

        for device in _swd2_button_devices(session, entry.options):
            for button_press_state in _SWD2_BUTTON_STATES:
                key = f"{device.id}_swd2_{button_press_state}_{_SCHEMA_VERSION}"
                if key in bridge_map:
                    continue
                label = f"{device.name} Button {button_press_state}"
                await _create_bridge_entry(
                    session,
                    bridge_map,
                    key,
                    label=label,
                    uds_name=_swd2_uds_name(device.name, button_press_state),
                    build_spec=partial(
                        _build_swd2_automation,
                        f"[HA] {label}",
                        device.id,
                        button_press_state,
                    ),
                )

    if bridge_map != entry.data.get(DATA_KEYPAD_BRIDGE_MAP, {}):
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, DATA_KEYPAD_BRIDGE_MAP: bridge_map}
        )
