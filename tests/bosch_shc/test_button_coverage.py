"""Coverage tests for button.py missing lines.

Targets:
  Line 38  — micromodule_impulse_relays device_excluded branch (continue)
  Line 48  — smoke_detectors device_excluded branch (continue)
  Line 58  — twinguards device_excluded branch (continue)
  Lines 67-81 — OPT_SCENARIOS_AS_BUTTONS block including try/except for
               KeyError/AttributeError on a malformed scenario
  Lines 139-142 — SHCScenarioButton.__init__ with entry_unique_id=None
                  (prefix falls back to entry_id)
  Line 146 — SHCScenarioButton.press() calls scenario.trigger()

Pattern: build a minimal fake hass + config_entry + session; call
async_setup_entry() via asyncio.run(); assert collected entities.
No HA harness required.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from custom_components.bosch_shc.button import (
    SHCScenarioButton,
    SHCSmokeTestButton,
    async_setup_entry,
)
from custom_components.bosch_shc.const import (
    OPT_EXCLUDED_DEVICES,
    OPT_SCENARIOS_AS_BUTTONS,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _fake_device(dev_id="dev-001", room_id=None):
    """Minimal device double that device_excluded() can inspect."""
    return SimpleNamespace(
        name="Fake Device",
        id=dev_id,
        root_device_id="root-001",
        room_id=room_id,
        manufacturer="Bosch",
        device_model="MODEL",
        status="AVAILABLE",
        device_services=[],
        deleted=False,
    )


def _fake_shc_device():
    """Minimal DeviceEntry-like double for the SHC controller."""
    return SimpleNamespace(
        identifiers={("bosch_shc", "shc-controller-001")},
        name="Smart Home Controller",
        manufacturer="Bosch",
        model="SmartHomeController",
    )


def _make_hass():
    """Minimal fake hass (unused by button.async_setup_entry, kept for parity)."""
    return SimpleNamespace()


def _make_entry(options=None, entry_id="E1", unique_id="uid-001"):
    return SimpleNamespace(
        options=options or {},
        entry_id=entry_id,
        unique_id=unique_id,
    )


def _run_setup(session, entry):
    entry.runtime_data = SimpleNamespace(
        session=session, shc_device=_fake_shc_device(), title="Test SHC"
    )
    hass = _make_hass()
    collected = []

    def add(entities):
        collected.extend(entities)

    asyncio.run(async_setup_entry(hass, entry, add))
    return collected


def _make_session(
    impulse_relays=None,
    smoke_detectors=None,
    twinguards=None,
    scenarios=None,
):
    return SimpleNamespace(
        device_helper=SimpleNamespace(
            micromodule_impulse_relays=impulse_relays or [],
            smoke_detectors=smoke_detectors or [],
            twinguards=twinguards or [],
        ),
        scenarios=scenarios or [],
    )


# ---------------------------------------------------------------------------
# Line 38 — micromodule_impulse_relays: excluded device is skipped
# ---------------------------------------------------------------------------

class TestImpulseRelayExcluded:
    def test_excluded_relay_is_not_added(self):
        """device_excluded returns True → continue → device not in entities."""
        dev = _fake_device(dev_id="relay-excl")
        session = _make_session(impulse_relays=[dev])
        entry = _make_entry(options={OPT_EXCLUDED_DEVICES: ["relay-excl"]})
        result = _run_setup(session, entry)
        assert result == []

    def test_non_excluded_relay_is_added(self):
        """Sanity: a relay NOT excluded IS added (line 37 false branch)."""
        dev = _fake_device(dev_id="relay-ok")
        session = _make_session(impulse_relays=[dev])
        entry = _make_entry()
        result = _run_setup(session, entry)
        assert len(result) == 1

    def test_mixed_relays_only_non_excluded_added(self):
        excl = _fake_device(dev_id="relay-excl")
        ok = _fake_device(dev_id="relay-ok")
        session = _make_session(impulse_relays=[excl, ok])
        entry = _make_entry(options={OPT_EXCLUDED_DEVICES: ["relay-excl"]})
        result = _run_setup(session, entry)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# Line 48 — smoke_detectors: excluded device is skipped
# ---------------------------------------------------------------------------

class TestSmokeDetectorExcluded:
    def test_excluded_smoke_detector_not_added(self):
        dev = _fake_device(dev_id="smoke-excl")
        session = _make_session(smoke_detectors=[dev])
        entry = _make_entry(options={OPT_EXCLUDED_DEVICES: ["smoke-excl"]})
        result = _run_setup(session, entry)
        assert result == []

    def test_non_excluded_smoke_detector_is_added(self):
        dev = _fake_device(dev_id="smoke-ok")
        session = _make_session(smoke_detectors=[dev])
        entry = _make_entry()
        result = _run_setup(session, entry)
        assert len(result) == 1
        assert isinstance(result[0], SHCSmokeTestButton)

    def test_excluded_smoke_detector_yields_smoke_test_button(self):
        """When not excluded, the entity type is SHCSmokeTestButton."""
        ok = _fake_device(dev_id="smoke-ok2")
        excl = _fake_device(dev_id="smoke-excl2")
        session = _make_session(smoke_detectors=[excl, ok])
        entry = _make_entry(options={OPT_EXCLUDED_DEVICES: ["smoke-excl2"]})
        result = _run_setup(session, entry)
        assert len(result) == 1
        assert isinstance(result[0], SHCSmokeTestButton)


# ---------------------------------------------------------------------------
# Line 58 — twinguards: excluded device is skipped
# ---------------------------------------------------------------------------

class TestTwinguardExcluded:
    def test_excluded_twinguard_not_added(self):
        dev = _fake_device(dev_id="tg-excl")
        session = _make_session(twinguards=[dev])
        entry = _make_entry(options={OPT_EXCLUDED_DEVICES: ["tg-excl"]})
        result = _run_setup(session, entry)
        assert result == []

    def test_non_excluded_twinguard_is_added_as_smoke_test_button(self):
        dev = _fake_device(dev_id="tg-ok")
        session = _make_session(twinguards=[dev])
        entry = _make_entry()
        result = _run_setup(session, entry)
        assert len(result) == 1
        assert isinstance(result[0], SHCSmokeTestButton)

    def test_mixed_twinguards_only_non_excluded_added(self):
        excl = _fake_device(dev_id="tg-excl")
        ok = _fake_device(dev_id="tg-ok")
        session = _make_session(twinguards=[excl, ok])
        entry = _make_entry(options={OPT_EXCLUDED_DEVICES: ["tg-excl"]})
        result = _run_setup(session, entry)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# Lines 67-81 — OPT_SCENARIOS_AS_BUTTONS block
# ---------------------------------------------------------------------------

def _good_scenario(sid="sc-001", name="Morning Lights"):
    return SimpleNamespace(id=sid, name=name, trigger=lambda: None)


class TestScenariosAsButtonsBlock:
    """OPT_SCENARIOS_AS_BUTTONS=True → scenarios become SHCScenarioButton entities."""

    def test_scenarios_as_buttons_false_by_default(self):
        """When option is absent / False, no scenario buttons are added."""
        sc = _good_scenario()
        session = _make_session(scenarios=[sc])
        entry = _make_entry()
        result = _run_setup(session, entry)
        assert result == []

    def test_scenarios_as_buttons_true_adds_button(self):
        sc = _good_scenario(sid="sc-001", name="Morning")
        session = _make_session(scenarios=[sc])
        entry = _make_entry(options={OPT_SCENARIOS_AS_BUTTONS: True})
        result = _run_setup(session, entry)
        assert len(result) == 1
        assert isinstance(result[0], SHCScenarioButton)

    def test_scenario_unique_id_uses_entry_unique_id(self):
        sc = _good_scenario(sid="sc-abc")
        session = _make_session(scenarios=[sc])
        entry = _make_entry(options={OPT_SCENARIOS_AS_BUTTONS: True}, unique_id="uid-xyz")
        result = _run_setup(session, entry)
        assert result[0]._attr_unique_id == "uid-xyz_scenario_sc-abc"

    def test_scenario_name_is_scenario_name(self):
        sc = _good_scenario(sid="sc-001", name="Evening Scene")
        session = _make_session(scenarios=[sc])
        entry = _make_entry(options={OPT_SCENARIOS_AS_BUTTONS: True})
        result = _run_setup(session, entry)
        assert result[0]._attr_name == "Evening Scene"

    def test_multiple_scenarios_all_added(self):
        scenarios = [
            _good_scenario(sid="sc-001", name="Scene A"),
            _good_scenario(sid="sc-002", name="Scene B"),
            _good_scenario(sid="sc-003", name="Scene C"),
        ]
        session = _make_session(scenarios=scenarios)
        entry = _make_entry(options={OPT_SCENARIOS_AS_BUTTONS: True})
        result = _run_setup(session, entry)
        assert len(result) == 3

    def test_keyerror_on_scenario_logs_warning_and_skips(self):
        """A scenario whose attribute access raises KeyError is skipped, not fatal."""
        class _BadScenario:
            @property
            def id(self):
                raise KeyError("id missing")

            @property
            def name(self):
                return "Bad"

        bad = _BadScenario()
        good = _good_scenario(sid="sc-good", name="Good")
        session = _make_session(scenarios=[bad, good])
        entry = _make_entry(options={OPT_SCENARIOS_AS_BUTTONS: True})

        with patch("custom_components.bosch_shc.button.LOGGER") as mock_log:
            result = _run_setup(session, entry)

        mock_log.warning.assert_called_once()
        assert len(result) == 1
        assert isinstance(result[0], SHCScenarioButton)

    def test_attribute_error_on_scenario_logs_warning_and_skips(self):
        """A scenario whose attribute access raises AttributeError is skipped."""
        class _BadScenario:
            @property
            def id(self):
                raise AttributeError("no id attr")

            name = "Bad"

        bad = _BadScenario()
        good = _good_scenario(sid="sc-good2", name="Good2")
        session = _make_session(scenarios=[bad, good])
        entry = _make_entry(options={OPT_SCENARIOS_AS_BUTTONS: True})

        with patch("custom_components.bosch_shc.button.LOGGER") as mock_log:
            result = _run_setup(session, entry)

        mock_log.warning.assert_called_once()
        assert len(result) == 1

    def test_all_bad_scenarios_yields_empty(self):
        """All malformed scenarios → empty entity list (async_add_entities not called)."""
        class _Bad:
            @property
            def id(self):
                raise KeyError("no id")
            name = "Bad"

        session = _make_session(scenarios=[_Bad(), _Bad()])
        entry = _make_entry(options={OPT_SCENARIOS_AS_BUTTONS: True})
        result = _run_setup(session, entry)
        assert result == []


# ---------------------------------------------------------------------------
# Lines 139-142 — SHCScenarioButton.__init__ with entry_unique_id=None
# ---------------------------------------------------------------------------

class TestSHCScenarioButtonInit:
    def test_prefix_uses_entry_unique_id_when_set(self):
        """With a real entry_unique_id the unique_id is prefixed by it."""
        sc = _good_scenario(sid="sc-001")
        btn = SHCScenarioButton(
            scenario=sc, entry_unique_id="uid-abc", entry_id="entry-xyz"
        )
        assert btn._attr_unique_id == "uid-abc_scenario_sc-001"

    def test_prefix_falls_back_to_entry_id_when_unique_id_none(self):
        """entry_unique_id=None → prefix is entry_id (line 140: else entry_id)."""
        sc = _good_scenario(sid="sc-002")
        btn = SHCScenarioButton(
            scenario=sc, entry_unique_id=None, entry_id="fallback-entry"
        )
        assert btn._attr_unique_id == "fallback-entry_scenario_sc-002"

    def test_name_set_from_scenario(self):
        sc = _good_scenario(name="My Scene")
        btn = SHCScenarioButton(
            scenario=sc, entry_unique_id="uid-1", entry_id="entry-1"
        )
        assert btn._attr_name == "My Scene"

    def test_scenario_stored(self):
        sc = _good_scenario()
        btn = SHCScenarioButton(
            scenario=sc, entry_unique_id="uid-1", entry_id="entry-1"
        )
        assert btn._scenario is sc

    def test_icon_is_script_play(self):
        sc = _good_scenario()
        btn = SHCScenarioButton(
            scenario=sc, entry_unique_id=None, entry_id="entry-1"
        )
        assert btn._attr_icon == "mdi:script-text-play"

    def test_should_poll_is_false(self):
        sc = _good_scenario()
        btn = SHCScenarioButton(
            scenario=sc, entry_unique_id=None, entry_id="entry-1"
        )
        assert btn._attr_should_poll is False


# ---------------------------------------------------------------------------
# Line 146 — SHCScenarioButton.press() calls scenario.trigger()
# ---------------------------------------------------------------------------

class TestSHCScenarioButtonPress:
    def test_press_calls_trigger(self):
        """async_press() must await self._scenario.async_trigger()."""
        calls = []
        sc = _good_scenario()

        async def _trig():
            calls.append(True)

        sc.async_trigger = _trig
        btn = SHCScenarioButton(
            scenario=sc, entry_unique_id="uid-1", entry_id="entry-1"
        )
        asyncio.run(btn.async_press())
        assert calls == [True]

    def test_press_called_twice_triggers_twice(self):
        calls = []
        sc = _good_scenario()

        async def _trig():
            calls.append(1)

        sc.async_trigger = _trig
        btn = SHCScenarioButton(
            scenario=sc, entry_unique_id=None, entry_id="entry-1"
        )
        asyncio.run(btn.async_press())
        asyncio.run(btn.async_press())
        assert len(calls) == 2

    def test_press_returns_none(self):
        sc = _good_scenario()

        async def _trig():
            return None

        sc.async_trigger = _trig
        btn = SHCScenarioButton(
            scenario=sc, entry_unique_id="uid-1", entry_id="entry-1"
        )
        assert asyncio.run(btn.async_press()) is None

    def test_setup_scenario_button_press_via_setup_entry(self):
        """End-to-end: button created via async_setup_entry, then pressed."""
        trigger_calls = []
        sc = _good_scenario(sid="sc-e2e", name="E2E Scene")

        async def _trig():
            trigger_calls.append(True)

        sc.async_trigger = _trig

        session = _make_session(scenarios=[sc])
        entry = _make_entry(
            options={OPT_SCENARIOS_AS_BUTTONS: True},
            unique_id="uid-e2e",
        )
        result = _run_setup(session, entry)
        assert len(result) == 1
        asyncio.run(result[0].async_press())
        assert trigger_calls == [True]


# ---------------------------------------------------------------------------
# Quality Scale: has-entity-name + unique_id preservation + device_info
# ---------------------------------------------------------------------------

class TestSHCScenarioButtonQualityScale:
    """Verify Bronze quality-scale rules for SHCScenarioButton."""

    def test_has_entity_name_true(self):
        """_attr_has_entity_name=True (Bronze: has-entity-name).

        SHCScenarioButton does not inherit a shadowing property from its base,
        so checking the class attribute directly is reliable.
        """
        sc = _good_scenario()
        btn = SHCScenarioButton(scenario=sc, entry_unique_id="u", entry_id="e")
        assert btn._attr_has_entity_name is True

    def test_unique_id_format_unchanged_with_entry_unique_id(self):
        """Regression pin: unique_id = f'{entry_unique_id}_scenario_{scenario.id}'.

        This exact format must never change — changing it orphans existing entities.
        """
        sc = _good_scenario(sid="sc-999")
        btn = SHCScenarioButton(
            scenario=sc, entry_unique_id="uid-fixed", entry_id="entry-fallback"
        )
        assert btn._attr_unique_id == "uid-fixed_scenario_sc-999"

    def test_unique_id_format_unchanged_without_entry_unique_id(self):
        """Regression pin: fallback to entry_id when entry_unique_id is None."""
        sc = _good_scenario(sid="sc-888")
        btn = SHCScenarioButton(
            scenario=sc, entry_unique_id=None, entry_id="entry-id-fallback"
        )
        assert btn._attr_unique_id == "entry-id-fallback_scenario_sc-888"

    def test_device_info_links_to_shc_controller(self):
        """device_info returns a dict with the SHC controller identifiers."""
        shc_dev = _fake_shc_device()
        sc = _good_scenario(sid="sc-di")
        btn = SHCScenarioButton(
            scenario=sc, entry_unique_id="uid-1", entry_id="entry-1", shc_device=shc_dev
        )
        info = btn.device_info
        assert info is not None
        assert info["identifiers"] == shc_dev.identifiers
        assert info["name"] == shc_dev.name
        assert info["manufacturer"] == shc_dev.manufacturer
        assert info["model"] == shc_dev.model

    def test_device_info_none_when_no_shc_device(self):
        """device_info returns None when shc_device is not provided (graceful fallback)."""
        sc = _good_scenario(sid="sc-no-dev")
        btn = SHCScenarioButton(
            scenario=sc, entry_unique_id="uid-1", entry_id="entry-1"
        )
        assert btn.device_info is None

    def test_setup_entry_passes_shc_device_to_button(self):
        """async_setup_entry populates shc_device so device_info is not None."""
        sc = _good_scenario(sid="sc-wiring", name="Test Wiring")
        session = _make_session(scenarios=[sc])
        entry = _make_entry(options={OPT_SCENARIOS_AS_BUTTONS: True}, unique_id="uid-w")
        result = _run_setup(session, entry)
        assert len(result) == 1
        btn = result[0]
        assert btn.device_info is not None
        assert btn.device_info["name"] == "Smart Home Controller"
