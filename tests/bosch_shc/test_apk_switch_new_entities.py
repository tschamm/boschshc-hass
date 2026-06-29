"""Tests for APK-batch 2-6 new switch entities.

Covers:
- EnergySavingMode (smartplug / smartplugcompact)
- PowerSwitchWarning suppression (smartplug / smartplugcompact)
- TwinguardNightlyPromise
- DisplayConfiguration humidityWarningEnabled (thermostats / roomthermostats)
- SwitchConfiguration swapInputs / swapOutputs (micromodule_relays / light_controls)
- SmartSensitivityControl enabled (motion_detectors2)
- SmokeSensitivity preAlarmEnabled (twinguards / smoke_detectors)

Run with:
  PYTHONPATH="<lib>:<hass>" PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
  python3 -m pytest tests/bosch_shc/test_apk_switch_new_entities.py -q -o addopts=
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from custom_components.bosch_shc.const import DATA_SESSION, DATA_SHC, DOMAIN
from custom_components.bosch_shc.switch import (
    SWITCH_TYPES,
    SHCSwitch,
    async_setup_entry,
)

# ---------------------------------------------------------------------------
# Helpers
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
    entry_id = "E1"
    hass = SimpleNamespace(
        data={
            DOMAIN: {entry_id: {
                DATA_SESSION: session,
                DATA_SHC: SimpleNamespace(
                    name="SHC", id="shc", identifiers={("bosch_shc", "shc")},
                    manufacturer="Bosch", model="SHC"),
            }}
        }
    )
    from unittest.mock import MagicMock
    config_entry = SimpleNamespace(options={}, entry_id=entry_id,
                                   async_on_unload=MagicMock())
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


# ---------------------------------------------------------------------------
# EnergySavingMode — smartplug
# ---------------------------------------------------------------------------


def test_smartplug_with_energy_saving_creates_entity():
    plug = _fake_device(energy_saving_mode_enabled=False, supports_energy_saving_mode=True)
    session = _make_session(smart_plugs=[plug])
    entities = _setup(session)
    keys = [e.entity_description.key for e in entities]
    assert "energy_saving_mode_enabled" in keys


def test_smartplug_without_energy_saving_skipped():
    plug = _fake_device()  # no energy_saving_mode_enabled attr
    session = _make_session(smart_plugs=[plug])
    entities = _setup(session)
    keys = [e.entity_description.key for e in entities]
    assert "energy_saving_mode_enabled" not in keys


def test_smartplug_energy_saving_unique_id():
    plug = _fake_device(id="plug1", energy_saving_mode_enabled=True, supports_energy_saving_mode=True)
    session = _make_session(smart_plugs=[plug])
    entities = _setup(session)
    esm = next(e for e in entities if e.entity_description.key == "energy_saving_mode_enabled")
    assert esm._attr_unique_id == "root1_plug1_energysavingmode"


def test_smartplug_energy_saving_is_on_true():
    plug = _fake_device(energy_saving_mode_enabled=True)
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = plug
    sw.entity_description = SWITCH_TYPES["energy_saving_mode_enabled"]
    sw.entity_id = "switch.test"
    assert sw.is_on is True


def test_smartplug_energy_saving_is_on_false():
    plug = _fake_device(energy_saving_mode_enabled=False)
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = plug
    sw.entity_description = SWITCH_TYPES["energy_saving_mode_enabled"]
    sw.entity_id = "switch.test"
    assert sw.is_on is False


# ---------------------------------------------------------------------------
# EnergySavingMode — smartplugcompact
# ---------------------------------------------------------------------------


def test_smartplugcompact_with_energy_saving_creates_entity():
    plug = _fake_device(energy_saving_mode_enabled=False, supports_energy_saving_mode=True)
    session = _make_session(smart_plugs_compact=[plug])
    entities = _setup(session)
    keys = [e.entity_description.key for e in entities]
    assert "energy_saving_mode_enabled" in keys


def test_smartplugcompact_without_energy_saving_skipped():
    plug = _fake_device()
    session = _make_session(smart_plugs_compact=[plug])
    entities = _setup(session)
    keys = [e.entity_description.key for e in entities]
    assert "energy_saving_mode_enabled" not in keys


# ---------------------------------------------------------------------------
# PowerSwitchWarning — smartplug
# ---------------------------------------------------------------------------


def test_smartplug_with_warning_suppressed_creates_entity():
    plug = _fake_device(warning_suppressed=False, supports_power_switch_warning=True)
    session = _make_session(smart_plugs=[plug])
    entities = _setup(session)
    keys = [e.entity_description.key for e in entities]
    assert "warning_suppressed" in keys


def test_smartplug_without_warning_suppressed_skipped():
    plug = _fake_device()
    session = _make_session(smart_plugs=[plug])
    entities = _setup(session)
    keys = [e.entity_description.key for e in entities]
    assert "warning_suppressed" not in keys


def test_smartplug_warning_suppressed_is_on_true():
    plug = _fake_device(warning_suppressed=True)
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = plug
    sw.entity_description = SWITCH_TYPES["warning_suppressed"]
    sw.entity_id = "switch.test"
    assert sw.is_on is True


def test_smartplug_warning_suppressed_is_on_false():
    plug = _fake_device(warning_suppressed=False)
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = plug
    sw.entity_description = SWITCH_TYPES["warning_suppressed"]
    sw.entity_id = "switch.test"
    assert sw.is_on is False


def test_smartplug_warning_suppressed_unique_id():
    plug = _fake_device(id="plug1", warning_suppressed=False, supports_power_switch_warning=True)
    session = _make_session(smart_plugs=[plug])
    entities = _setup(session)
    ws = next(e for e in entities if e.entity_description.key == "warning_suppressed")
    assert ws._attr_unique_id == "root1_plug1_warningsuppressed"


# ---------------------------------------------------------------------------
# TwinguardNightlyPromise
# ---------------------------------------------------------------------------


def test_twinguard_with_nightly_promise_creates_entity():
    tg = _fake_device(nightly_promise_enabled=True, supports_nightly_promise=True)
    session = _make_session(twinguards=[tg])
    entities = _setup(session)
    keys = [e.entity_description.key for e in entities]
    assert "nightly_promise_enabled" in keys


def test_twinguard_without_nightly_promise_skipped():
    tg = _fake_device()
    session = _make_session(twinguards=[tg])
    entities = _setup(session)
    keys = [e.entity_description.key for e in entities]
    assert "nightly_promise_enabled" not in keys


def test_twinguard_nightly_promise_is_on():
    tg = _fake_device(nightly_promise_enabled=True)
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = tg
    sw.entity_description = SWITCH_TYPES["nightly_promise_enabled"]
    sw.entity_id = "switch.test"
    assert sw.is_on is True


def test_twinguard_nightly_promise_unique_id():
    tg = _fake_device(id="tg1", nightly_promise_enabled=False, supports_nightly_promise=True)
    session = _make_session(twinguards=[tg])
    entities = _setup(session)
    np = next(e for e in entities if e.entity_description.key == "nightly_promise_enabled")
    assert np._attr_unique_id == "root1_tg1_nightlypromise"


# ---------------------------------------------------------------------------
# Pre-alarm enabled — twinguard
# ---------------------------------------------------------------------------


def test_twinguard_with_pre_alarm_creates_entity():
    tg = _fake_device(pre_alarm_enabled=False, supports_smoke_sensitivity=True)
    session = _make_session(twinguards=[tg])
    entities = _setup(session)
    keys = [e.entity_description.key for e in entities]
    assert "pre_alarm_enabled" in keys


def test_twinguard_without_pre_alarm_skipped():
    tg = _fake_device()
    session = _make_session(twinguards=[tg])
    entities = _setup(session)
    keys = [e.entity_description.key for e in entities]
    assert "pre_alarm_enabled" not in keys


# ---------------------------------------------------------------------------
# Pre-alarm enabled — smoke_detector
# ---------------------------------------------------------------------------


def test_smoke_detector_with_pre_alarm_creates_entity():
    sd = _fake_device(pre_alarm_enabled=False, supports_smoke_sensitivity=True)
    session = _make_session(smoke_detectors=[sd])
    entities = _setup(session)
    keys = [e.entity_description.key for e in entities]
    assert "pre_alarm_enabled" in keys


def test_smoke_detector_without_pre_alarm_skipped():
    sd = _fake_device()
    session = _make_session(smoke_detectors=[sd])
    entities = _setup(session)
    keys = [e.entity_description.key for e in entities]
    assert "pre_alarm_enabled" not in keys


def test_pre_alarm_is_on_true():
    dev = _fake_device(pre_alarm_enabled=True)
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = dev
    sw.entity_description = SWITCH_TYPES["pre_alarm_enabled"]
    sw.entity_id = "switch.test"
    assert sw.is_on is True


def test_pre_alarm_is_on_false():
    dev = _fake_device(pre_alarm_enabled=False)
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = dev
    sw.entity_description = SWITCH_TYPES["pre_alarm_enabled"]
    sw.entity_id = "switch.test"
    assert sw.is_on is False


# ---------------------------------------------------------------------------
# HumidityWarningEnabled — thermostat / roomthermostat
# ---------------------------------------------------------------------------


def test_thermostat_with_humidity_warning_creates_entity():
    therm = _fake_device(humidity_warning_enabled=False, supports_display_configuration=True)
    session = _make_session(thermostats=[therm])
    entities = _setup(session)
    keys = [e.entity_description.key for e in entities]
    assert "humidity_warning_enabled" in keys


def test_thermostat_without_humidity_warning_skipped():
    therm = _fake_device()
    session = _make_session(thermostats=[therm])
    entities = _setup(session)
    keys = [e.entity_description.key for e in entities]
    assert "humidity_warning_enabled" not in keys


def test_roomthermostat_with_humidity_warning_creates_entity():
    rth = _fake_device(humidity_warning_enabled=True, supports_display_configuration=True)
    session = _make_session(roomthermostats=[rth])
    entities = _setup(session)
    keys = [e.entity_description.key for e in entities]
    assert "humidity_warning_enabled" in keys


def test_humidity_warning_is_on_true():
    dev = _fake_device(humidity_warning_enabled=True)
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = dev
    sw.entity_description = SWITCH_TYPES["humidity_warning_enabled"]
    sw.entity_id = "switch.test"
    assert sw.is_on is True


def test_humidity_warning_unique_id():
    therm = _fake_device(id="t1", humidity_warning_enabled=False, supports_display_configuration=True)
    session = _make_session(thermostats=[therm])
    entities = _setup(session)
    hw = next(e for e in entities if e.entity_description.key == "humidity_warning_enabled")
    assert hw._attr_unique_id == "root1_t1_humiditywarning"


# ---------------------------------------------------------------------------
# SwapInputs / SwapOutputs — micromodule_relays
# ---------------------------------------------------------------------------


def test_relay_with_swap_inputs_creates_entity():
    relay = _fake_device(swap_inputs=False, child_lock=False, supports_switch_configuration=True)
    session = _make_session(micromodule_relays=[relay])
    entities = _setup(session)
    keys = [e.entity_description.key for e in entities]
    assert "swap_inputs" in keys


def test_relay_without_swap_inputs_skipped():
    relay = _fake_device(child_lock=False)
    session = _make_session(micromodule_relays=[relay])
    entities = _setup(session)
    keys = [e.entity_description.key for e in entities]
    assert "swap_inputs" not in keys


def test_relay_with_swap_outputs_creates_entity():
    relay = _fake_device(swap_outputs=True, child_lock=False, supports_switch_configuration=True)
    session = _make_session(micromodule_relays=[relay])
    entities = _setup(session)
    keys = [e.entity_description.key for e in entities]
    assert "swap_outputs" in keys


def test_relay_without_swap_outputs_skipped():
    relay = _fake_device(child_lock=False)
    session = _make_session(micromodule_relays=[relay])
    entities = _setup(session)
    keys = [e.entity_description.key for e in entities]
    assert "swap_outputs" not in keys


def test_swap_inputs_is_on_true():
    relay = _fake_device(swap_inputs=True)
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = relay
    sw.entity_description = SWITCH_TYPES["swap_inputs"]
    sw.entity_id = "switch.test"
    assert sw.is_on is True


def test_swap_inputs_is_on_false():
    relay = _fake_device(swap_inputs=False)
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = relay
    sw.entity_description = SWITCH_TYPES["swap_inputs"]
    sw.entity_id = "switch.test"
    assert sw.is_on is False


def test_swap_outputs_is_on_true():
    relay = _fake_device(swap_outputs=True)
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = relay
    sw.entity_description = SWITCH_TYPES["swap_outputs"]
    sw.entity_id = "switch.test"
    assert sw.is_on is True


def test_swap_outputs_unique_id():
    relay = _fake_device(id="r1", swap_inputs=False, swap_outputs=False,
                         child_lock=False, supports_switch_configuration=True)
    session = _make_session(micromodule_relays=[relay])
    entities = _setup(session)
    so = next(e for e in entities if e.entity_description.key == "swap_outputs")
    assert so._attr_unique_id == "root1_r1_swapoutputs"


def test_swap_inputs_unique_id():
    relay = _fake_device(id="r1", swap_inputs=False, swap_outputs=False,
                         child_lock=False, supports_switch_configuration=True)
    session = _make_session(micromodule_relays=[relay])
    entities = _setup(session)
    si = next(e for e in entities if e.entity_description.key == "swap_inputs")
    assert si._attr_unique_id == "root1_r1_swapinputs"


# ---------------------------------------------------------------------------
# SwapInputs / SwapOutputs — light_controls
# ---------------------------------------------------------------------------


def test_light_control_with_swap_inputs_creates_entity():
    lc = _fake_device(swap_inputs=False, supports_switch_configuration=True)
    session = _make_session(micromodule_light_controls=[lc])
    entities = _setup(session)
    keys = [e.entity_description.key for e in entities]
    assert "swap_inputs" in keys


def test_light_control_without_swap_inputs_skipped():
    lc = _fake_device()
    session = _make_session(micromodule_light_controls=[lc])
    entities = _setup(session)
    keys = [e.entity_description.key for e in entities]
    assert "swap_inputs" not in keys


# ---------------------------------------------------------------------------
# SmartSensitivityEnabled — motion_detectors2
# ---------------------------------------------------------------------------


def test_md2_with_smart_sensitivity_creates_entity():
    md2 = _fake_device(
        pet_immunity_enabled=False,
        smart_sensitivity_enabled=True,
    )
    session = _make_session(motion_detectors2=[md2])
    entities = _setup(session)
    keys = [e.entity_description.key for e in entities]
    assert "smart_sensitivity_enabled" in keys


def test_md2_without_smart_sensitivity_skipped():
    md2 = _fake_device(pet_immunity_enabled=False)
    session = _make_session(motion_detectors2=[md2])
    entities = _setup(session)
    keys = [e.entity_description.key for e in entities]
    assert "smart_sensitivity_enabled" not in keys


def test_smart_sensitivity_is_on_true():
    dev = _fake_device(smart_sensitivity_enabled=True)
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = dev
    sw.entity_description = SWITCH_TYPES["smart_sensitivity_enabled"]
    sw.entity_id = "switch.test"
    assert sw.is_on is True


def test_smart_sensitivity_is_on_false():
    dev = _fake_device(smart_sensitivity_enabled=False)
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = dev
    sw.entity_description = SWITCH_TYPES["smart_sensitivity_enabled"]
    sw.entity_id = "switch.test"
    assert sw.is_on is False


def test_smart_sensitivity_unique_id():
    md2 = _fake_device(id="md1", pet_immunity_enabled=False,
                       smart_sensitivity_enabled=False)
    session = _make_session(motion_detectors2=[md2])
    entities = _setup(session)
    ss = next(e for e in entities if e.entity_description.key == "smart_sensitivity_enabled")
    assert ss._attr_unique_id == "root1_md1_smartsensitivity"


# ---------------------------------------------------------------------------
# Entity category checks
# ---------------------------------------------------------------------------


def test_energy_saving_mode_entity_category_config():
    from homeassistant.helpers.entity import EntityCategory
    assert SWITCH_TYPES["energy_saving_mode_enabled"].entity_category == EntityCategory.CONFIG


def test_warning_suppressed_entity_category_config():
    from homeassistant.helpers.entity import EntityCategory
    assert SWITCH_TYPES["warning_suppressed"].entity_category == EntityCategory.CONFIG


def test_nightly_promise_entity_category_config():
    from homeassistant.helpers.entity import EntityCategory
    assert SWITCH_TYPES["nightly_promise_enabled"].entity_category == EntityCategory.CONFIG


def test_humidity_warning_entity_category_config():
    from homeassistant.helpers.entity import EntityCategory
    assert SWITCH_TYPES["humidity_warning_enabled"].entity_category == EntityCategory.CONFIG


def test_swap_inputs_entity_category_config():
    from homeassistant.helpers.entity import EntityCategory
    assert SWITCH_TYPES["swap_inputs"].entity_category == EntityCategory.CONFIG


def test_swap_outputs_entity_category_config():
    from homeassistant.helpers.entity import EntityCategory
    assert SWITCH_TYPES["swap_outputs"].entity_category == EntityCategory.CONFIG


def test_pre_alarm_entity_category_config():
    from homeassistant.helpers.entity import EntityCategory
    assert SWITCH_TYPES["pre_alarm_enabled"].entity_category == EntityCategory.CONFIG


def test_smart_sensitivity_entity_category_config():
    from homeassistant.helpers.entity import EntityCategory
    assert SWITCH_TYPES["smart_sensitivity_enabled"].entity_category == EntityCategory.CONFIG


# ---------------------------------------------------------------------------
# should_poll — all new switch types are False
# ---------------------------------------------------------------------------


def test_new_switch_types_should_poll_false():
    new_keys = [
        "energy_saving_mode_enabled",
        "warning_suppressed",
        "nightly_promise_enabled",
        "humidity_warning_enabled",
        "swap_inputs",
        "swap_outputs",
        "pre_alarm_enabled",
        "smart_sensitivity_enabled",
    ]
    for key in new_keys:
        assert SWITCH_TYPES[key].should_poll is False, (
            f"SWITCH_TYPES[{key!r}].should_poll should be False"
        )
