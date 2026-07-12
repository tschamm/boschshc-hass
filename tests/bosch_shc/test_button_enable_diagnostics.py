"""Unit tests for SHCEnableAllDiagnosticsButton (button.py).

AsyncMock-driven pure unit tests (mirrors the WalkTest/DetectionTest/
TamperReset button tests in test_button.py) — no full HA fixture needed,
entity_registry is patched directly.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch

from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_registry import RegistryEntryDisabler

from custom_components.bosch_shc.button import SHCEnableAllDiagnosticsButton


def _run(coro):
    return asyncio.run(coro)


def _entity_entry(entity_id, category, disabled_by):
    return SimpleNamespace(
        entity_id=entity_id, entity_category=category, disabled_by=disabled_by
    )


def _button() -> SHCEnableAllDiagnosticsButton:
    button = SHCEnableAllDiagnosticsButton(entry_id="entry1")
    button.hass = MagicMock()
    button.hass.config_entries.async_reload = AsyncMock()
    return button


def test_enables_only_integration_disabled_diagnostic_entities() -> None:
    """Must skip: non-diagnostic entities, and entities a user disabled themselves."""
    entries = [
        _entity_entry("sensor.diag1", EntityCategory.DIAGNOSTIC, RegistryEntryDisabler.INTEGRATION),
        _entity_entry("sensor.diag2", EntityCategory.DIAGNOSTIC, RegistryEntryDisabler.INTEGRATION),
        _entity_entry("sensor.diag_user_disabled", EntityCategory.DIAGNOSTIC, RegistryEntryDisabler.USER),
        _entity_entry("sensor.normal", None, None),
        _entity_entry("switch.config_entity", EntityCategory.CONFIG, RegistryEntryDisabler.INTEGRATION),
    ]
    registry = MagicMock()
    button = _button()

    with (
        patch("custom_components.bosch_shc.button.er.async_get", return_value=registry),
        patch(
            "custom_components.bosch_shc.button.er.async_entries_for_config_entry",
            return_value=entries,
        ),
    ):
        _run(button.async_press())

    registry.async_update_entity.assert_has_calls(
        [
            call("sensor.diag1", disabled_by=None),
            call("sensor.diag2", disabled_by=None),
        ],
        any_order=True,
    )
    assert registry.async_update_entity.call_count == 2
    button.hass.config_entries.async_reload.assert_awaited_once_with("entry1")


def test_no_op_when_nothing_to_enable_does_not_reload() -> None:
    """A reload is disruptive (all entities re-created) — skip it when nothing changed."""
    entries = [
        _entity_entry("sensor.diag_user_disabled", EntityCategory.DIAGNOSTIC, RegistryEntryDisabler.USER),
        _entity_entry("sensor.already_enabled", EntityCategory.DIAGNOSTIC, None),
    ]
    registry = MagicMock()
    button = _button()

    with (
        patch("custom_components.bosch_shc.button.er.async_get", return_value=registry),
        patch(
            "custom_components.bosch_shc.button.er.async_entries_for_config_entry",
            return_value=entries,
        ),
    ):
        _run(button.async_press())

    registry.async_update_entity.assert_not_called()
    button.hass.config_entries.async_reload.assert_not_awaited()


def test_device_info_links_to_shc_controller_device() -> None:
    shc_device = SimpleNamespace(
        identifiers={("bosch_shc", "abc")},
        name="My SHC",
        manufacturer="Bosch",
        model="SmartHomeController",
    )
    button = SHCEnableAllDiagnosticsButton(entry_id="entry1", shc_device=shc_device)

    assert button.device_info == {
        "identifiers": shc_device.identifiers,
        "name": "My SHC",
        "manufacturer": "Bosch",
        "model": "SmartHomeController",
    }


def test_device_info_none_without_shc_device() -> None:
    button = SHCEnableAllDiagnosticsButton(entry_id="entry1")

    assert button.device_info is None
