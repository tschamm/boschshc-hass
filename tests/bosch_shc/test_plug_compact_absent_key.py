"""Test: PLUG_COMPACT scenario — state_after_power_outage absent from state dict.

PLUG_COMPACT ("Brunnen") advertises PowerSwitchConfiguration but its state dict
only contains `supportedStatesAfterPowerOutage`, NOT `stateAfterPowerOutage`.

Before the fix: service getter returned UNKNOWN (not None), which bypassed the
`getattr(device, "state_after_power_outage", None) is None` guard in select.py
and created an unavailable StateAfterPowerOutageSelect entity.

After the fix: getter returns None → guard triggers → entity is NOT created.

These tests confirm:
1. When device.state_after_power_outage is None (absent field), entity is skipped.
2. When device.state_after_power_outage is UNKNOWN (present but unrecognised),
   entity IS created but current_option returns None (entity stays unavailable
   until next poll but does not error).
3. When device.state_after_power_outage is a valid enum, entity IS created and
   current_option returns the option name.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from boschshcpy.services_impl import PowerSwitchConfigurationService

from custom_components.bosch_shc.const import DATA_SESSION, DOMAIN
from custom_components.bosch_shc.select import (
    StateAfterPowerOutageSelect,
    async_setup_entry,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_device(**kwargs):
    defaults = dict(name="Brunnen", id="dev1", root_device_id="root1", serial="SER1")
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_session(**helper_lists):
    defaults = dict(
        motion_detectors2=[],
        shutter_contacts2=[],
        smart_plugs=[],
        smart_plugs_compact=[],
        smoke_detectors=[],
        twinguards=[],
        thermostats=[],
        roomthermostats=[],
        micromodule_relays=[],
        micromodule_light_controls=[],
    )
    defaults.update(helper_lists)
    device_helper = SimpleNamespace(**defaults)
    return SimpleNamespace(device_helper=device_helper)


async def _async_setup(session):
    entry_id = "E1"
    hass = SimpleNamespace(data={DOMAIN: {entry_id: {DATA_SESSION: session}}})
    config_entry = SimpleNamespace(
        options={}, entry_id=entry_id, unique_id="UID1",
        async_on_unload=MagicMock(),
    )
    entities = []

    def add_entities(new_ents, *args, **kwargs):
        entities.extend(new_ents)

    with patch(
        "custom_components.bosch_shc.select.SHCShutterContact2Plus",
        new=type("SHCShutterContact2Plus", (), {}),
    ):
        await async_setup_entry(hass, config_entry, add_entities)
    return entities


def _setup(session):
    return asyncio.run(_async_setup(session))


def _entity_types(entities):
    return [type(e).__name__ for e in entities]


# ---------------------------------------------------------------------------
# Core guard tests
# ---------------------------------------------------------------------------


class TestPlugCompactAbsentStateAfterPowerOutage:
    """Entity must NOT be created when state_after_power_outage is None."""

    def test_plug_compact_absent_field_skips_entity(self):
        """Simulate PLUG_COMPACT: service present but stateAfterPowerOutage key absent."""
        plug = _fake_device(
            supports_power_switch_configuration=True,
            state_after_power_outage=None,  # getter now returns None when key absent
        )
        entities = _setup(_make_session(smart_plugs_compact=[plug]))
        assert "StateAfterPowerOutageSelect" not in _entity_types(entities)

    def test_regular_plug_absent_field_skips_entity(self):
        plug = _fake_device(
            supports_power_switch_configuration=True,
            state_after_power_outage=None,
        )
        entities = _setup(_make_session(smart_plugs=[plug]))
        assert "StateAfterPowerOutageSelect" not in _entity_types(entities)

    def test_plug_compact_with_valid_value_creates_entity(self):
        plug = _fake_device(
            supports_power_switch_configuration=True,
            state_after_power_outage=PowerSwitchConfigurationService.StateAfterPowerOutage.OFF,
        )
        entities = _setup(_make_session(smart_plugs_compact=[plug]))
        assert "StateAfterPowerOutageSelect" in _entity_types(entities)


# ---------------------------------------------------------------------------
# current_option behaviour when value is UNKNOWN vs None
# ---------------------------------------------------------------------------


class TestStateAfterPowerOutageCurrentOption:
    """current_option returns None both for None value and UNKNOWN enum."""

    def _make_entity(self, value):
        dev = SimpleNamespace(
            root_device_id="root1", id="dev1", name="Plug",
            state_after_power_outage=value,
        )
        e = StateAfterPowerOutageSelect.__new__(StateAfterPowerOutageSelect)
        e._device = dev
        e._attr_options = ["OFF", "ON", "LAST_STATE"]
        return e

    def test_current_option_none_value_returns_none(self):
        """Absent key (value=None) → current_option is None."""
        e = self._make_entity(None)
        assert e.current_option is None

    def test_current_option_unknown_enum_returns_none(self):
        """UNKNOWN enum (present key, unrecognized value) → current_option is None."""
        e = self._make_entity(
            PowerSwitchConfigurationService.StateAfterPowerOutage.UNKNOWN
        )
        assert e.current_option is None

    def test_current_option_off_returns_off(self):
        e = self._make_entity(
            PowerSwitchConfigurationService.StateAfterPowerOutage.OFF
        )
        assert e.current_option == "OFF"

    def test_current_option_on_returns_on(self):
        e = self._make_entity(
            PowerSwitchConfigurationService.StateAfterPowerOutage.ON
        )
        assert e.current_option == "ON"

    def test_current_option_last_state_returns_last_state(self):
        e = self._make_entity(
            PowerSwitchConfigurationService.StateAfterPowerOutage.LAST_STATE
        )
        assert e.current_option == "LAST_STATE"
