"""Dual-guard tests for APK switch entities.

For each guarded switch entity, verify:
  (a) NOT created when supports_* = False (even if value attr is present)
  (b) NOT created when supports_* = True but primary value is None
  (c) Created when both supports_* = True AND value is not None

Entities covered:
  - energy_saving_mode_enabled (smartplug / smartplugcompact)
  - warning_suppressed (smartplug / smartplugcompact)
  - nightly_promise_enabled (twinguard)
  - pre_alarm_enabled (twinguard + smoke_detector)
  - swap_inputs / swap_outputs (micromodule_relay + light_control)
  - humidity_warning_enabled (thermostat + roomthermostat)
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from custom_components.bosch_shc.switch import async_setup_entry

# ---------------------------------------------------------------------------
# Helpers (shared with test_apk_switch_new_entities.py pattern)
# ---------------------------------------------------------------------------


def _fake_device(**kwargs):
    defaults = dict(name="Dev", id="dev1", root_device_id="root1", serial="SER1",
                    supports_silentmode=False)
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_session(**helper_lists):
    defaults = dict(
        smart_plugs=[],
        light_switches_bsm=[],
        micromodule_light_attached=[],
        smart_plugs_compact=[],
        micromodule_relays=[],
        camera_eyes=[],
        camera_360=[],
        camera_outdoor_gen2=[],
        presence_simulation_system=None,
        shutter_contacts2=[],
        thermostats=[],
        roomthermostats=[],
        wallthermostats=[],
        micromodule_shutter_controls=[],
        micromodule_blinds=[],
        micromodule_impulse_relays=[],
        micromodule_dimmers=[],
        motion_detectors2=[],
        twinguards=[],
        smoke_detectors=[],
        micromodule_light_controls=[],
    )
    defaults.update(helper_lists)
    device_helper = SimpleNamespace(**defaults)
    session = SimpleNamespace(
        device_helper=device_helper,
        userdefinedstates=[],
        subscribe=lambda *a, **kw: None,
        _subscribers=[],
    )
    return session


def _make_hass_and_entry(session):
    from unittest.mock import MagicMock
    entry_id = "E1"
    hass = SimpleNamespace()
    config_entry = SimpleNamespace(options={}, entry_id=entry_id,
                                   async_on_unload=MagicMock())
    config_entry.runtime_data = SimpleNamespace(
        session=session,
        shc_device=SimpleNamespace(
            name="SHC", id="shc", identifiers={("bosch_shc", "shc")},
            manufacturer="Bosch", model="SHC"),
    )
    return hass, config_entry


async def _async_setup(session):
    hass, config_entry = _make_hass_and_entry(session)
    entities = []

    def add_entities(new_ents, *args, **kwargs):
        entities.extend(new_ents)

    with patch(
        "custom_components.bosch_shc.switch.async_migrate_to_new_unique_id",
        new=AsyncMock(return_value=None),
    ):
        await async_setup_entry(hass, config_entry, add_entities)
    return entities


def _setup(session):
    return asyncio.run(_async_setup(session))


def _keys(entities):
    return [e.entity_description.key for e in entities]


# ---------------------------------------------------------------------------
# energy_saving_mode_enabled — smartplug
# ---------------------------------------------------------------------------


class TestEnergySavingModeGuard:
    def test_supports_false_value_present_skipped(self):
        """supports_energy_saving_mode=False → entity NOT created even with value."""
        plug = _fake_device(energy_saving_mode_enabled=True,
                            supports_energy_saving_mode=False)
        entities = _setup(_make_session(smart_plugs=[plug]))
        assert "energy_saving_mode_enabled" not in _keys(entities)

    def test_supports_true_value_none_skipped(self):
        """supports_energy_saving_mode=True but value=None → entity NOT created."""
        plug = _fake_device(energy_saving_mode_enabled=None,
                            supports_energy_saving_mode=True)
        entities = _setup(_make_session(smart_plugs=[plug]))
        assert "energy_saving_mode_enabled" not in _keys(entities)

    def test_both_present_created(self):
        """supports=True and value not None → entity created."""
        plug = _fake_device(energy_saving_mode_enabled=False,
                            supports_energy_saving_mode=True)
        entities = _setup(_make_session(smart_plugs=[plug]))
        assert "energy_saving_mode_enabled" in _keys(entities)

    def test_supports_false_value_present_skipped_compact(self):
        plug = _fake_device(energy_saving_mode_enabled=True,
                            supports_energy_saving_mode=False)
        entities = _setup(_make_session(smart_plugs_compact=[plug]))
        assert "energy_saving_mode_enabled" not in _keys(entities)

    def test_supports_true_value_none_skipped_compact(self):
        plug = _fake_device(energy_saving_mode_enabled=None,
                            supports_energy_saving_mode=True)
        entities = _setup(_make_session(smart_plugs_compact=[plug]))
        assert "energy_saving_mode_enabled" not in _keys(entities)


# ---------------------------------------------------------------------------
# warning_suppressed — smartplug / smartplugcompact
# ---------------------------------------------------------------------------


class TestWarningSuppressedGuard:
    def test_supports_false_value_present_skipped(self):
        plug = _fake_device(warning_suppressed=True,
                            supports_power_switch_warning=False)
        entities = _setup(_make_session(smart_plugs=[plug]))
        assert "warning_suppressed" not in _keys(entities)

    def test_supports_true_value_none_skipped(self):
        plug = _fake_device(warning_suppressed=None,
                            supports_power_switch_warning=True)
        entities = _setup(_make_session(smart_plugs=[plug]))
        assert "warning_suppressed" not in _keys(entities)

    def test_both_present_created(self):
        plug = _fake_device(warning_suppressed=False,
                            supports_power_switch_warning=True)
        entities = _setup(_make_session(smart_plugs=[plug]))
        assert "warning_suppressed" in _keys(entities)

    def test_supports_false_skipped_compact(self):
        plug = _fake_device(warning_suppressed=False,
                            supports_power_switch_warning=False)
        entities = _setup(_make_session(smart_plugs_compact=[plug]))
        assert "warning_suppressed" not in _keys(entities)

    def test_supports_true_value_none_skipped_compact(self):
        plug = _fake_device(warning_suppressed=None,
                            supports_power_switch_warning=True)
        entities = _setup(_make_session(smart_plugs_compact=[plug]))
        assert "warning_suppressed" not in _keys(entities)


# ---------------------------------------------------------------------------
# nightly_promise_enabled — twinguard
# ---------------------------------------------------------------------------


class TestNightlyPromiseGuard:
    def test_supports_false_value_present_skipped(self):
        tg = _fake_device(nightly_promise_enabled=True,
                          supports_nightly_promise=False)
        entities = _setup(_make_session(twinguards=[tg]))
        assert "nightly_promise_enabled" not in _keys(entities)

    def test_supports_true_value_none_skipped(self):
        tg = _fake_device(nightly_promise_enabled=None,
                          supports_nightly_promise=True)
        entities = _setup(_make_session(twinguards=[tg]))
        assert "nightly_promise_enabled" not in _keys(entities)

    def test_both_present_created(self):
        tg = _fake_device(nightly_promise_enabled=True,
                          supports_nightly_promise=True)
        entities = _setup(_make_session(twinguards=[tg]))
        assert "nightly_promise_enabled" in _keys(entities)


# ---------------------------------------------------------------------------
# pre_alarm_enabled — twinguard
# ---------------------------------------------------------------------------


class TestPreAlarmGuardTwinguard:
    def test_supports_false_value_present_skipped(self):
        tg = _fake_device(pre_alarm_enabled=True,
                          supports_smoke_sensitivity=False)
        entities = _setup(_make_session(twinguards=[tg]))
        assert "pre_alarm_enabled" not in _keys(entities)

    def test_supports_true_value_none_skipped(self):
        tg = _fake_device(pre_alarm_enabled=None,
                          supports_smoke_sensitivity=True)
        entities = _setup(_make_session(twinguards=[tg]))
        assert "pre_alarm_enabled" not in _keys(entities)

    def test_both_present_created(self):
        tg = _fake_device(pre_alarm_enabled=False,
                          supports_smoke_sensitivity=True)
        entities = _setup(_make_session(twinguards=[tg]))
        assert "pre_alarm_enabled" in _keys(entities)


# ---------------------------------------------------------------------------
# pre_alarm_enabled — smoke_detector
# ---------------------------------------------------------------------------


class TestPreAlarmGuardSmokeDetector:
    def test_supports_false_value_present_skipped(self):
        sd = _fake_device(pre_alarm_enabled=True,
                          supports_smoke_sensitivity=False)
        entities = _setup(_make_session(smoke_detectors=[sd]))
        assert "pre_alarm_enabled" not in _keys(entities)

    def test_supports_true_value_none_skipped(self):
        sd = _fake_device(pre_alarm_enabled=None,
                          supports_smoke_sensitivity=True)
        entities = _setup(_make_session(smoke_detectors=[sd]))
        assert "pre_alarm_enabled" not in _keys(entities)

    def test_both_present_created(self):
        sd = _fake_device(pre_alarm_enabled=False,
                          supports_smoke_sensitivity=True)
        entities = _setup(_make_session(smoke_detectors=[sd]))
        assert "pre_alarm_enabled" in _keys(entities)


# ---------------------------------------------------------------------------
# swap_inputs — micromodule_relay
# ---------------------------------------------------------------------------


class TestSwapInputsGuardRelay:
    def test_supports_false_value_present_skipped(self):
        relay = _fake_device(swap_inputs=True, child_lock=False,
                             supports_switch_configuration=False)
        entities = _setup(_make_session(micromodule_relays=[relay]))
        assert "swap_inputs" not in _keys(entities)

    def test_supports_true_value_none_skipped(self):
        relay = _fake_device(swap_inputs=None, child_lock=False,
                             supports_switch_configuration=True)
        entities = _setup(_make_session(micromodule_relays=[relay]))
        assert "swap_inputs" not in _keys(entities)

    def test_both_present_created(self):
        relay = _fake_device(swap_inputs=False, child_lock=False,
                             supports_switch_configuration=True)
        entities = _setup(_make_session(micromodule_relays=[relay]))
        assert "swap_inputs" in _keys(entities)


# ---------------------------------------------------------------------------
# swap_outputs — micromodule_relay
# ---------------------------------------------------------------------------


class TestSwapOutputsGuardRelay:
    def test_supports_false_value_present_skipped(self):
        relay = _fake_device(swap_outputs=True, child_lock=False,
                             supports_switch_configuration=False)
        entities = _setup(_make_session(micromodule_relays=[relay]))
        assert "swap_outputs" not in _keys(entities)

    def test_supports_true_value_none_skipped(self):
        relay = _fake_device(swap_outputs=None, child_lock=False,
                             supports_switch_configuration=True)
        entities = _setup(_make_session(micromodule_relays=[relay]))
        assert "swap_outputs" not in _keys(entities)

    def test_both_present_created(self):
        relay = _fake_device(swap_outputs=False, child_lock=False,
                             supports_switch_configuration=True)
        entities = _setup(_make_session(micromodule_relays=[relay]))
        assert "swap_outputs" in _keys(entities)


# ---------------------------------------------------------------------------
# swap_inputs — micromodule_light_control
# ---------------------------------------------------------------------------


class TestSwapInputsGuardLightControl:
    def test_supports_false_value_present_skipped(self):
        lc = _fake_device(swap_inputs=True,
                          supports_switch_configuration=False)
        entities = _setup(_make_session(micromodule_light_controls=[lc]))
        assert "swap_inputs" not in _keys(entities)

    def test_supports_true_value_none_skipped(self):
        lc = _fake_device(swap_inputs=None,
                          supports_switch_configuration=True)
        entities = _setup(_make_session(micromodule_light_controls=[lc]))
        assert "swap_inputs" not in _keys(entities)

    def test_both_present_created(self):
        lc = _fake_device(swap_inputs=False,
                          supports_switch_configuration=True)
        entities = _setup(_make_session(micromodule_light_controls=[lc]))
        assert "swap_inputs" in _keys(entities)


# ---------------------------------------------------------------------------
# swap_outputs — micromodule_light_control
# ---------------------------------------------------------------------------


class TestSwapOutputsGuardLightControl:
    def test_supports_false_value_present_skipped(self):
        lc = _fake_device(swap_outputs=True,
                          supports_switch_configuration=False)
        entities = _setup(_make_session(micromodule_light_controls=[lc]))
        assert "swap_outputs" not in _keys(entities)

    def test_supports_true_value_none_skipped(self):
        lc = _fake_device(swap_outputs=None,
                          supports_switch_configuration=True)
        entities = _setup(_make_session(micromodule_light_controls=[lc]))
        assert "swap_outputs" not in _keys(entities)

    def test_both_present_created(self):
        lc = _fake_device(swap_outputs=False,
                          supports_switch_configuration=True)
        entities = _setup(_make_session(micromodule_light_controls=[lc]))
        assert "swap_outputs" in _keys(entities)


# ---------------------------------------------------------------------------
# humidity_warning_enabled — thermostat
# ---------------------------------------------------------------------------


class TestHumidityWarningGuardThermostat:
    def test_supports_false_value_present_skipped(self):
        therm = _fake_device(humidity_warning_enabled=True,
                             supports_display_configuration=False)
        entities = _setup(_make_session(thermostats=[therm]))
        assert "humidity_warning_enabled" not in _keys(entities)

    def test_supports_true_value_none_skipped(self):
        therm = _fake_device(humidity_warning_enabled=None,
                             supports_display_configuration=True)
        entities = _setup(_make_session(thermostats=[therm]))
        assert "humidity_warning_enabled" not in _keys(entities)

    def test_both_present_created(self):
        therm = _fake_device(humidity_warning_enabled=False,
                             supports_display_configuration=True)
        entities = _setup(_make_session(thermostats=[therm]))
        assert "humidity_warning_enabled" in _keys(entities)


# ---------------------------------------------------------------------------
# humidity_warning_enabled — roomthermostat
# ---------------------------------------------------------------------------


class TestHumidityWarningGuardRoomThermostat:
    def test_supports_false_value_present_skipped(self):
        rth = _fake_device(humidity_warning_enabled=True,
                           supports_display_configuration=False)
        entities = _setup(_make_session(roomthermostats=[rth]))
        assert "humidity_warning_enabled" not in _keys(entities)

    def test_supports_true_value_none_skipped(self):
        rth = _fake_device(humidity_warning_enabled=None,
                           supports_display_configuration=True)
        entities = _setup(_make_session(roomthermostats=[rth]))
        assert "humidity_warning_enabled" not in _keys(entities)

    def test_both_present_created(self):
        rth = _fake_device(humidity_warning_enabled=True,
                           supports_display_configuration=True)
        entities = _setup(_make_session(roomthermostats=[rth]))
        assert "humidity_warning_enabled" in _keys(entities)
