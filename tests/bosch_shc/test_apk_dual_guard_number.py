"""Dual-guard tests for APK number entities.

For each guarded number entity, verify:
  (a) NOT created when supports_* = False (even if value attr is present)
  (b) NOT created when supports_* = True but primary value is None
  (c) Created when both supports_* = True AND value is not None

Entities covered:
  - PowerThresholdNumber (supports_energy_saving_mode + power_threshold)
  - EnterDurationNumber (supports_energy_saving_mode + enter_duration_seconds)
  - LedBrightnessNumber (supports_led_brightness + led_brightness)
  - DisplayBrightnessNumber (supports_display_configuration + display_brightness)
  - DisplayOnTimeNumber (supports_display_configuration + display_on_time)
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

from custom_components.bosch_shc.number import async_setup_entry
from custom_components.bosch_shc.const import DATA_SESSION, DOMAIN


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_device(**kwargs):
    defaults = dict(name="Dev", id="dev1", root_device_id="root1",
                    serial="SER1", supports_silentmode=False)
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_session(**helper_lists):
    defaults = dict(
        thermostats=[],
        roomthermostats=[],
        micromodule_impulse_relays=[],
        heating_circuits=[],
    )
    defaults.update(helper_lists)
    device_helper = SimpleNamespace(**defaults)
    return SimpleNamespace(device_helper=device_helper)


def _make_hass_and_entry(session):
    entry_id = "E1"
    hass = SimpleNamespace(
        data={DOMAIN: {entry_id: {DATA_SESSION: session}}}
    )
    config_entry = SimpleNamespace(options={}, entry_id=entry_id,
                                   async_on_unload=MagicMock())
    return hass, config_entry


async def _async_setup(session):
    hass, config_entry = _make_hass_and_entry(session)
    entities = []

    def add_entities(new_ents, *args, **kwargs):
        entities.extend(new_ents)

    await async_setup_entry(hass, config_entry, add_entities)
    return entities


def _setup(session):
    return asyncio.run(_async_setup(session))


def _types(entities):
    return [type(e).__name__ for e in entities]


# ---------------------------------------------------------------------------
# PowerThresholdNumber
# ---------------------------------------------------------------------------


class TestPowerThresholdNumberGuard:
    def test_supports_false_value_present_skipped(self):
        plug = _fake_device(power_threshold=50.0,
                            supports_energy_saving_mode=False)
        entities = _setup(_make_session(smart_plugs=[plug]))
        assert "PowerThresholdNumber" not in _types(entities)

    def test_supports_true_value_none_skipped(self):
        plug = _fake_device(power_threshold=None,
                            supports_energy_saving_mode=True)
        entities = _setup(_make_session(smart_plugs=[plug]))
        assert "PowerThresholdNumber" not in _types(entities)

    def test_both_present_created(self):
        plug = _fake_device(power_threshold=100.0,
                            supports_energy_saving_mode=True)
        entities = _setup(_make_session(smart_plugs=[plug]))
        assert "PowerThresholdNumber" in _types(entities)

    def test_compact_supports_false_skipped(self):
        plug = _fake_device(power_threshold=50.0,
                            supports_energy_saving_mode=False)
        entities = _setup(_make_session(smart_plugs_compact=[plug]))
        assert "PowerThresholdNumber" not in _types(entities)

    def test_compact_value_none_skipped(self):
        plug = _fake_device(power_threshold=None,
                            supports_energy_saving_mode=True)
        entities = _setup(_make_session(smart_plugs_compact=[plug]))
        assert "PowerThresholdNumber" not in _types(entities)


def _make_session_with_smartplug(plug):
    defaults = dict(
        thermostats=[],
        roomthermostats=[],
        micromodule_impulse_relays=[],
        heating_circuits=[],
        smart_plugs=[plug],
        smart_plugs_compact=[],
    )
    device_helper = SimpleNamespace(**defaults)
    return SimpleNamespace(device_helper=device_helper)


def _make_session_with_compact(plug):
    defaults = dict(
        thermostats=[],
        roomthermostats=[],
        micromodule_impulse_relays=[],
        heating_circuits=[],
        smart_plugs=[],
        smart_plugs_compact=[plug],
    )
    device_helper = SimpleNamespace(**defaults)
    return SimpleNamespace(device_helper=device_helper)


# We need a session factory that includes smart_plugs for energy saving tests
def _make_full_session(**kwargs):
    defaults = dict(
        thermostats=[],
        roomthermostats=[],
        micromodule_impulse_relays=[],
        heating_circuits=[],
        smart_plugs=[],
        smart_plugs_compact=[],
    )
    defaults.update(kwargs)
    device_helper = SimpleNamespace(**defaults)
    return SimpleNamespace(device_helper=device_helper)


# ---------------------------------------------------------------------------
# EnterDurationNumber
# ---------------------------------------------------------------------------


class TestEnterDurationNumberGuard:
    def test_supports_false_value_present_skipped(self):
        plug = _fake_device(enter_duration_seconds=60,
                            supports_energy_saving_mode=False)
        entities = _setup(_make_full_session(smart_plugs=[plug]))
        assert "EnterDurationNumber" not in _types(entities)

    def test_supports_true_value_none_skipped(self):
        plug = _fake_device(enter_duration_seconds=None,
                            supports_energy_saving_mode=True)
        entities = _setup(_make_full_session(smart_plugs=[plug]))
        assert "EnterDurationNumber" not in _types(entities)

    def test_both_present_created(self):
        plug = _fake_device(enter_duration_seconds=30,
                            supports_energy_saving_mode=True)
        entities = _setup(_make_full_session(smart_plugs=[plug]))
        assert "EnterDurationNumber" in _types(entities)


# ---------------------------------------------------------------------------
# LedBrightnessNumber
# ---------------------------------------------------------------------------


class TestLedBrightnessNumberGuard:
    def test_supports_false_value_present_skipped(self):
        plug = _fake_device(led_brightness=50,
                            supports_led_brightness=False)
        entities = _setup(_make_full_session(smart_plugs=[plug]))
        assert "LedBrightnessNumber" not in _types(entities)

    def test_supports_true_value_none_skipped(self):
        plug = _fake_device(led_brightness=None,
                            supports_led_brightness=True)
        entities = _setup(_make_full_session(smart_plugs=[plug]))
        assert "LedBrightnessNumber" not in _types(entities)

    def test_both_present_created(self):
        plug = _fake_device(led_brightness=75,
                            supports_led_brightness=True)
        entities = _setup(_make_full_session(smart_plugs=[plug]))
        assert "LedBrightnessNumber" in _types(entities)


# ---------------------------------------------------------------------------
# DisplayBrightnessNumber
# ---------------------------------------------------------------------------


class TestDisplayBrightnessNumberGuard:
    def test_supports_false_value_present_skipped(self):
        therm = _fake_device(display_brightness=50,
                             supports_display_configuration=False)
        entities = _setup(_make_full_session(thermostats=[therm]))
        assert "DisplayBrightnessNumber" not in _types(entities)

    def test_supports_true_value_none_skipped(self):
        therm = _fake_device(display_brightness=None,
                             supports_display_configuration=True)
        entities = _setup(_make_full_session(thermostats=[therm]))
        assert "DisplayBrightnessNumber" not in _types(entities)

    def test_both_present_created(self):
        therm = _fake_device(display_brightness=60,
                             supports_display_configuration=True)
        entities = _setup(_make_full_session(thermostats=[therm]))
        assert "DisplayBrightnessNumber" in _types(entities)

    def test_roomthermostat_value_none_skipped(self):
        rth = _fake_device(display_brightness=None,
                           supports_display_configuration=True)
        entities = _setup(_make_full_session(roomthermostats=[rth]))
        assert "DisplayBrightnessNumber" not in _types(entities)

    def test_roomthermostat_both_present_created(self):
        rth = _fake_device(display_brightness=40,
                           supports_display_configuration=True)
        entities = _setup(_make_full_session(roomthermostats=[rth]))
        assert "DisplayBrightnessNumber" in _types(entities)


# ---------------------------------------------------------------------------
# DisplayOnTimeNumber
# ---------------------------------------------------------------------------


class TestDisplayOnTimeNumberGuard:
    def test_supports_false_value_present_skipped(self):
        therm = _fake_device(display_on_time=30,
                             supports_display_configuration=False)
        entities = _setup(_make_full_session(thermostats=[therm]))
        assert "DisplayOnTimeNumber" not in _types(entities)

    def test_supports_true_value_none_skipped(self):
        therm = _fake_device(display_on_time=None,
                             supports_display_configuration=True)
        entities = _setup(_make_full_session(thermostats=[therm]))
        assert "DisplayOnTimeNumber" not in _types(entities)

    def test_both_present_created(self):
        therm = _fake_device(display_on_time=60,
                             supports_display_configuration=True)
        entities = _setup(_make_full_session(thermostats=[therm]))
        assert "DisplayOnTimeNumber" in _types(entities)

    def test_roomthermostat_value_none_skipped(self):
        rth = _fake_device(display_on_time=None,
                           supports_display_configuration=True)
        entities = _setup(_make_full_session(roomthermostats=[rth]))
        assert "DisplayOnTimeNumber" not in _types(entities)
