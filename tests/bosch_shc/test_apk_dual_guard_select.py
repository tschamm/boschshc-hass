"""Dual-guard tests for APK select entities.

For each guarded select entity, verify:
  (a) NOT created when supports_* = False (even if value attr is present)
  (b) NOT created when supports_* = True but primary value is None
  (c) Created when both supports_* = True AND value is not None

Entities covered:
  - StateAfterPowerOutageSelect (supports_power_switch_configuration + state_after_power_outage)
  - DisplayDirectionSelect (supports_display_direction + display_direction)
  - DisplayedTemperatureSelect (supports_displayed_temperature + displayed_temperature)
  - ValveTypeSelect (supports_wall_thermostat_configuration + valve_type)
  - HeaterTypeSelect (supports_wall_thermostat_configuration + heater_type)
  - TerminalTypeSelect (supports_terminal_configuration + terminal_type)
  - SwitchTypeSelect (switch_type value only — see note below)
  - ActuatorTypeSelect (actuator_type value only — see note below)
  - OutputModeSelect (output_mode value only — see note below)
  - SmartSensitivitySecurityLevelSelect (supports_smart_sensitivity + get_smart_sensitivity)
  - SmartSensitivityComfortLevelSelect (supports_smart_sensitivity + get_smart_sensitivity)

Note on SwitchType/ActuatorType/OutputMode: select.py no longer checks
supports_switch_configuration for these three. That flag only exists on
SHCMicromoduleRelay (where it's just "the switch-config service is
present", already implied by switch_type/actuator_type/output_mode being
non-None); SHCLightControl has no such flag at all, so the old dual guard
meant Light Control II never got these selects even though the values are
already null-safe on that class too. Gating on the value alone is correct
for both device kinds.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from custom_components.bosch_shc.const import DATA_SESSION, DOMAIN
from custom_components.bosch_shc.select import async_setup_entry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_device(**kwargs):
    defaults = dict(name="Dev", id="dev1", root_device_id="root1", serial="SER1")
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


def _make_hass_and_entry(session):
    entry_id = "E1"
    hass = SimpleNamespace(
        data={DOMAIN: {entry_id: {DATA_SESSION: session}}}
    )
    config_entry = SimpleNamespace(options={}, entry_id=entry_id,
                                   unique_id="UID1",
                                   async_on_unload=MagicMock())
    return hass, config_entry


async def _async_setup(session):
    hass, config_entry = _make_hass_and_entry(session)
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


def _types(entities):
    return [type(e).__name__ for e in entities]


# ---------------------------------------------------------------------------
# StateAfterPowerOutageSelect
# ---------------------------------------------------------------------------


class TestStateAfterPowerOutageSelectGuard:
    def test_supports_false_value_present_skipped(self):
        plug = _fake_device(state_after_power_outage=True,
                            supports_power_switch_configuration=False)
        entities = _setup(_make_session(smart_plugs=[plug]))
        assert "StateAfterPowerOutageSelect" not in _types(entities)

    def test_supports_true_value_none_skipped(self):
        plug = _fake_device(state_after_power_outage=None,
                            supports_power_switch_configuration=True)
        entities = _setup(_make_session(smart_plugs=[plug]))
        assert "StateAfterPowerOutageSelect" not in _types(entities)

    def test_both_present_created(self):
        plug = _fake_device(state_after_power_outage=True,
                            supports_power_switch_configuration=True)
        entities = _setup(_make_session(smart_plugs=[plug]))
        assert "StateAfterPowerOutageSelect" in _types(entities)

    def test_supports_false_compact_skipped(self):
        plug = _fake_device(state_after_power_outage=True,
                            supports_power_switch_configuration=False)
        entities = _setup(_make_session(smart_plugs_compact=[plug]))
        assert "StateAfterPowerOutageSelect" not in _types(entities)

    def test_value_none_compact_skipped(self):
        plug = _fake_device(state_after_power_outage=None,
                            supports_power_switch_configuration=True)
        entities = _setup(_make_session(smart_plugs_compact=[plug]))
        assert "StateAfterPowerOutageSelect" not in _types(entities)


# ---------------------------------------------------------------------------
# DisplayDirectionSelect
# ---------------------------------------------------------------------------


class TestDisplayDirectionSelectGuard:
    def test_supports_false_value_present_skipped(self):
        dev = _fake_device(display_direction=True,
                           supports_display_direction=False)
        entities = _setup(_make_session(thermostats=[dev]))
        assert "DisplayDirectionSelect" not in _types(entities)

    def test_supports_true_value_none_skipped(self):
        dev = _fake_device(display_direction=None,
                           supports_display_direction=True)
        entities = _setup(_make_session(thermostats=[dev]))
        assert "DisplayDirectionSelect" not in _types(entities)

    def test_both_present_created(self):
        dev = _fake_device(display_direction=True,
                           supports_display_direction=True)
        entities = _setup(_make_session(thermostats=[dev]))
        assert "DisplayDirectionSelect" in _types(entities)

    def test_roomthermostat_value_none_skipped(self):
        dev = _fake_device(display_direction=None,
                           supports_display_direction=True)
        entities = _setup(_make_session(roomthermostats=[dev]))
        assert "DisplayDirectionSelect" not in _types(entities)


# ---------------------------------------------------------------------------
# DisplayedTemperatureSelect
# ---------------------------------------------------------------------------


class TestDisplayedTemperatureSelectGuard:
    def test_supports_false_value_present_skipped(self):
        dev = _fake_device(displayed_temperature=True,
                           supports_displayed_temperature=False)
        entities = _setup(_make_session(thermostats=[dev]))
        assert "DisplayedTemperatureSelect" not in _types(entities)

    def test_supports_true_value_none_skipped(self):
        dev = _fake_device(displayed_temperature=None,
                           supports_displayed_temperature=True)
        entities = _setup(_make_session(thermostats=[dev]))
        assert "DisplayedTemperatureSelect" not in _types(entities)

    def test_both_present_created(self):
        dev = _fake_device(displayed_temperature=True,
                           supports_displayed_temperature=True)
        entities = _setup(_make_session(thermostats=[dev]))
        assert "DisplayedTemperatureSelect" in _types(entities)


# ---------------------------------------------------------------------------
# ValveTypeSelect
# ---------------------------------------------------------------------------


class TestValveTypeSelectGuard:
    def test_supports_false_value_present_skipped(self):
        dev = _fake_device(valve_type=True,
                           supports_wall_thermostat_configuration=False)
        entities = _setup(_make_session(thermostats=[dev]))
        assert "ValveTypeSelect" not in _types(entities)

    def test_supports_true_value_none_skipped(self):
        dev = _fake_device(valve_type=None,
                           supports_wall_thermostat_configuration=True)
        entities = _setup(_make_session(thermostats=[dev]))
        assert "ValveTypeSelect" not in _types(entities)

    def test_both_present_created(self):
        dev = _fake_device(valve_type=True,
                           supports_wall_thermostat_configuration=True)
        entities = _setup(_make_session(thermostats=[dev]))
        assert "ValveTypeSelect" in _types(entities)


# ---------------------------------------------------------------------------
# HeaterTypeSelect
# ---------------------------------------------------------------------------


class TestHeaterTypeSelectGuard:
    def test_supports_false_value_present_skipped(self):
        dev = _fake_device(heater_type=True,
                           supports_wall_thermostat_configuration=False)
        entities = _setup(_make_session(thermostats=[dev]))
        assert "HeaterTypeSelect" not in _types(entities)

    def test_supports_true_value_none_skipped(self):
        dev = _fake_device(heater_type=None,
                           supports_wall_thermostat_configuration=True)
        entities = _setup(_make_session(thermostats=[dev]))
        assert "HeaterTypeSelect" not in _types(entities)

    def test_both_present_created(self):
        dev = _fake_device(heater_type=True,
                           supports_wall_thermostat_configuration=True)
        entities = _setup(_make_session(thermostats=[dev]))
        assert "HeaterTypeSelect" in _types(entities)


# ---------------------------------------------------------------------------
# TerminalTypeSelect
# ---------------------------------------------------------------------------


class TestTerminalTypeSelectGuard:
    def test_supports_false_value_present_skipped(self):
        dev = _fake_device(terminal_type=True,
                           supports_terminal_configuration=False)
        entities = _setup(_make_session(thermostats=[dev]))
        assert "TerminalTypeSelect" not in _types(entities)

    def test_supports_true_value_none_skipped(self):
        dev = _fake_device(terminal_type=None,
                           supports_terminal_configuration=True)
        entities = _setup(_make_session(thermostats=[dev]))
        assert "TerminalTypeSelect" not in _types(entities)

    def test_both_present_created(self):
        dev = _fake_device(terminal_type=True,
                           supports_terminal_configuration=True)
        entities = _setup(_make_session(thermostats=[dev]))
        assert "TerminalTypeSelect" in _types(entities)

    def test_roomthermostat_value_none_skipped(self):
        dev = _fake_device(terminal_type=None,
                           supports_terminal_configuration=True)
        entities = _setup(_make_session(roomthermostats=[dev]))
        assert "TerminalTypeSelect" not in _types(entities)


# ---------------------------------------------------------------------------
# SwitchTypeSelect
# ---------------------------------------------------------------------------


class TestSwitchTypeSelectGuard:
    def test_value_none_skipped(self):
        relay = _fake_device(switch_type=None)
        entities = _setup(_make_session(micromodule_relays=[relay]))
        assert "SwitchTypeSelect" not in _types(entities)

    def test_value_present_created(self):
        relay = _fake_device(switch_type=True)
        entities = _setup(_make_session(micromodule_relays=[relay]))
        assert "SwitchTypeSelect" in _types(entities)

    def test_light_control_value_none_skipped(self):
        lc = _fake_device(switch_type=None)
        entities = _setup(_make_session(micromodule_light_controls=[lc]))
        assert "SwitchTypeSelect" not in _types(entities)

    def test_light_control_value_present_created(self):
        """Regression test: LightControl has no supports_switch_configuration
        at all, so this must be gated on switch_type alone, not that flag."""
        lc = _fake_device(switch_type=True)
        entities = _setup(_make_session(micromodule_light_controls=[lc]))
        assert "SwitchTypeSelect" in _types(entities)


# ---------------------------------------------------------------------------
# ActuatorTypeSelect
# ---------------------------------------------------------------------------


class TestActuatorTypeSelectGuard:
    def test_value_none_skipped(self):
        relay = _fake_device(actuator_type=None)
        entities = _setup(_make_session(micromodule_relays=[relay]))
        assert "ActuatorTypeSelect" not in _types(entities)

    def test_value_present_created(self):
        relay = _fake_device(actuator_type=True)
        entities = _setup(_make_session(micromodule_relays=[relay]))
        assert "ActuatorTypeSelect" in _types(entities)

    def test_light_control_value_none_skipped(self):
        lc = _fake_device(actuator_type=None)
        entities = _setup(_make_session(micromodule_light_controls=[lc]))
        assert "ActuatorTypeSelect" not in _types(entities)

    def test_light_control_value_present_created(self):
        """Regression test: LightControl has no supports_switch_configuration
        at all, so this must be gated on actuator_type alone, not that flag."""
        lc = _fake_device(actuator_type=True)
        entities = _setup(_make_session(micromodule_light_controls=[lc]))
        assert "ActuatorTypeSelect" in _types(entities)


# ---------------------------------------------------------------------------
# OutputModeSelect
# ---------------------------------------------------------------------------


class TestOutputModeSelectGuard:
    def test_value_none_skipped(self):
        relay = _fake_device(output_mode=None)
        entities = _setup(_make_session(micromodule_relays=[relay]))
        assert "OutputModeSelect" not in _types(entities)

    def test_value_present_created(self):
        relay = _fake_device(output_mode=True)
        entities = _setup(_make_session(micromodule_relays=[relay]))
        assert "OutputModeSelect" in _types(entities)

    def test_light_control_value_none_skipped(self):
        lc = _fake_device(output_mode=None)
        entities = _setup(_make_session(micromodule_light_controls=[lc]))
        assert "OutputModeSelect" not in _types(entities)

    def test_light_control_value_present_created(self):
        """Regression test: LightControl has no supports_switch_configuration
        at all, so this must be gated on output_mode alone, not that flag."""
        lc = _fake_device(output_mode=True)
        entities = _setup(_make_session(micromodule_light_controls=[lc]))
        assert "OutputModeSelect" in _types(entities)


# ---------------------------------------------------------------------------
# SmartSensitivitySecurityLevelSelect + SmartSensitivityComfortLevelSelect
# ---------------------------------------------------------------------------


class TestSmartSensitivitySelectGuard:
    def test_supports_false_callable_present_skipped(self):
        md2 = _fake_device(
            get_smart_sensitivity=lambda c: {"manualLevel": "HIGH"},
            supports_smart_sensitivity=False,
        )
        entities = _setup(_make_session(motion_detectors2=[md2]))
        assert "SmartSensitivitySecurityLevelSelect" not in _types(entities)
        assert "SmartSensitivityComfortLevelSelect" not in _types(entities)

    def test_supports_true_callable_none_skipped(self):
        md2 = _fake_device(
            get_smart_sensitivity=None,
            supports_smart_sensitivity=True,
        )
        entities = _setup(_make_session(motion_detectors2=[md2]))
        assert "SmartSensitivitySecurityLevelSelect" not in _types(entities)
        assert "SmartSensitivityComfortLevelSelect" not in _types(entities)

    def test_both_present_creates_both(self):
        md2 = _fake_device(
            get_smart_sensitivity=lambda c: {"manualLevel": "HIGH"},
            supports_smart_sensitivity=True,
        )
        entities = _setup(_make_session(motion_detectors2=[md2]))
        assert "SmartSensitivitySecurityLevelSelect" in _types(entities)
        assert "SmartSensitivityComfortLevelSelect" in _types(entities)
