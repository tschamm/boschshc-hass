"""Tests for APK-batch 2-6 new select entities.

Covers:
- StateAfterPowerOutageSelect (smart plugs)
- SmokeSensitivitySelect (smoke_detectors / twinguards)
- DisplayDirectionSelect (thermostats / roomthermostats)
- DisplayedTemperatureSelect (thermostats / roomthermostats)
- TerminalTypeSelect (thermostats / roomthermostats)
- ValveTypeSelect (thermostats / roomthermostats)
- HeaterTypeSelect (thermostats / roomthermostats)
- SwitchTypeSelect (micromodule_relays / micromodule_light_controls)
- ActuatorTypeSelect (micromodule_relays / micromodule_light_controls)
- OutputModeSelect (micromodule_relays / micromodule_light_controls)

Run with:
  PYTHONPATH="<lib>:<hass>" PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
  python3 -m pytest tests/bosch_shc/test_apk_select_new_entities.py -q -o addopts=
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from custom_components.bosch_shc.select import (
    StateAfterPowerOutageSelect,
    SmokeSensitivitySelect,
    DisplayDirectionSelect,
    DisplayedTemperatureSelect,
    TerminalTypeSelect,
    ValveTypeSelect,
    HeaterTypeSelect,
    SwitchTypeSelect,
    ActuatorTypeSelect,
    OutputModeSelect,
    async_setup_entry,
)
from custom_components.bosch_shc.const import DATA_SESSION, DOMAIN


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
        # shutter_contacts2 is empty in all tests so this path won't run
        new=type("SHCShutterContact2Plus", (), {}),
    ):
        await async_setup_entry(hass, config_entry, add_entities)
    return entities


def _setup(session):
    return asyncio.run(_async_setup(session))


# ---------------------------------------------------------------------------
# StateAfterPowerOutageSelect
# ---------------------------------------------------------------------------


class TestStateAfterPowerOutageSelect:
    def _make(self, state_after_power_outage_name="OFF"):
        from boschshcpy.services_impl import PowerSwitchConfigurationService
        val = PowerSwitchConfigurationService.StateAfterPowerOutage[
            state_after_power_outage_name
        ]
        dev = SimpleNamespace(root_device_id="root1", id="dev1",
                              name="Plug", state_after_power_outage=val)
        e = StateAfterPowerOutageSelect.__new__(StateAfterPowerOutageSelect)
        e._device = dev
        e._attr_unique_id = (
            f"{dev.root_device_id}_{dev.id}_state_after_power_outage"
        )
        e._attr_name = "State After Power Outage"
        return e

    def test_unique_id_format(self):
        e = self._make()
        assert e._attr_unique_id == "root1_dev1_state_after_power_outage"

    def test_current_option_off(self):
        e = self._make("OFF")
        assert e.current_option == "OFF"

    def test_current_option_on(self):
        e = self._make("ON")
        assert e.current_option == "ON"

    def test_current_option_last_state(self):
        e = self._make("LAST_STATE")
        assert e.current_option == "LAST_STATE"

    def test_current_option_unknown_returns_none(self):
        from boschshcpy.services_impl import PowerSwitchConfigurationService
        dev = SimpleNamespace(root_device_id="r", id="d", name="X",
                              state_after_power_outage=PowerSwitchConfigurationService.StateAfterPowerOutage.UNKNOWN)
        e = StateAfterPowerOutageSelect.__new__(StateAfterPowerOutageSelect)
        e._device = dev
        assert e.current_option is None

    def test_set_state_writes_to_device(self):
        from boschshcpy.services_impl import PowerSwitchConfigurationService
        written = []
        dev = SimpleNamespace(root_device_id="r", id="d", name="X",
                              state_after_power_outage=None)
        e = StateAfterPowerOutageSelect.__new__(StateAfterPowerOutageSelect)
        e._device = dev

        def setter(v):
            written.append(v)

        dev.state_after_power_outage = None
        e._set_state(PowerSwitchConfigurationService.StateAfterPowerOutage.ON)
        assert dev.state_after_power_outage == PowerSwitchConfigurationService.StateAfterPowerOutage.ON

    def test_created_when_attr_present(self):
        plug = _fake_device(state_after_power_outage=True, supports_power_switch_configuration=True)
        session = _make_session(smart_plugs=[plug])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "StateAfterPowerOutageSelect" in types

    def test_skipped_when_attr_absent(self):
        plug = _fake_device()
        session = _make_session(smart_plugs=[plug])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "StateAfterPowerOutageSelect" not in types

    def test_created_for_smartplugcompact(self):
        plug = _fake_device(state_after_power_outage=True, supports_power_switch_configuration=True)
        session = _make_session(smart_plugs_compact=[plug])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "StateAfterPowerOutageSelect" in types


# ---------------------------------------------------------------------------
# SmokeSensitivitySelect
# ---------------------------------------------------------------------------


class TestSmokeSensitivitySelect:
    def _make(self, level_name="HIGH"):
        from boschshcpy.services_impl import SmokeSensitivityService
        val = SmokeSensitivityService.SmokeSensitivityLevel[level_name]
        dev = SimpleNamespace(root_device_id="root1", id="dev1",
                              name="Smoke", smoke_sensitivity=val)
        e = SmokeSensitivitySelect.__new__(SmokeSensitivitySelect)
        e._device = dev
        e._attr_unique_id = f"{dev.root_device_id}_{dev.id}_smoke_sensitivity"
        return e

    def test_unique_id_format(self):
        e = self._make()
        assert e._attr_unique_id == "root1_dev1_smoke_sensitivity"

    def test_current_option_high(self):
        e = self._make("HIGH")
        assert e.current_option == "HIGH"

    def test_current_option_middle(self):
        e = self._make("MIDDLE")
        assert e.current_option == "MIDDLE"

    def test_current_option_low(self):
        e = self._make("LOW")
        assert e.current_option == "LOW"

    def test_current_option_unknown_returns_none(self):
        from boschshcpy.services_impl import SmokeSensitivityService
        dev = SimpleNamespace(root_device_id="r", id="d", name="X",
                              smoke_sensitivity=SmokeSensitivityService.SmokeSensitivityLevel.UNKNOWN)
        e = SmokeSensitivitySelect.__new__(SmokeSensitivitySelect)
        e._device = dev
        assert e.current_option is None

    def test_current_option_none_returns_none(self):
        dev = SimpleNamespace(root_device_id="r", id="d", name="X",
                              smoke_sensitivity=None)
        e = SmokeSensitivitySelect.__new__(SmokeSensitivitySelect)
        e._device = dev
        assert e.current_option is None

    def test_set_level_writes_to_device(self):
        from boschshcpy.services_impl import SmokeSensitivityService
        dev = SimpleNamespace(root_device_id="r", id="d", name="X",
                              smoke_sensitivity=None)
        e = SmokeSensitivitySelect.__new__(SmokeSensitivitySelect)
        e._device = dev
        e._set_level(SmokeSensitivityService.SmokeSensitivityLevel.MIDDLE)
        assert dev.smoke_sensitivity == SmokeSensitivityService.SmokeSensitivityLevel.MIDDLE

    def test_created_for_smoke_detector_when_attr_present(self):
        sd = _fake_device(smoke_sensitivity=True, supports_smoke_sensitivity=True)
        session = _make_session(smoke_detectors=[sd])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "SmokeSensitivitySelect" in types

    def test_skipped_when_attr_absent(self):
        sd = _fake_device()
        session = _make_session(smoke_detectors=[sd])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "SmokeSensitivitySelect" not in types

    def test_skipped_when_service_present_but_field_absent(self):
        """Service registered but state dict has no smokeSensitivity key → None → skip."""
        sd = _fake_device(supports_smoke_sensitivity=True, smoke_sensitivity=None)
        session = _make_session(smoke_detectors=[sd])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "SmokeSensitivitySelect" not in types

    def test_created_for_twinguard(self):
        tg = _fake_device(smoke_sensitivity=True, supports_smoke_sensitivity=True)
        session = _make_session(twinguards=[tg])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "SmokeSensitivitySelect" in types

    def test_smoke_sensitivity_probe_raises_attribute_error_skips(self):
        """When accessing device.smoke_sensitivity raises AttributeError, skip entity."""
        class BadDev:
            root_device_id = "r"
            id = "d"
            name = "X"
            serial = "S"
            supports_silentmode = False
            supports_smoke_sensitivity = True

            @property
            def smoke_sensitivity(self):
                raise AttributeError("no service")

        dev = BadDev()
        session = _make_session(smoke_detectors=[dev])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "SmokeSensitivitySelect" not in types


# ---------------------------------------------------------------------------
# DisplayDirectionSelect
# ---------------------------------------------------------------------------


class TestDisplayDirectionSelect:
    def _make(self, direction_name="NORMAL"):
        from boschshcpy.services_impl import DisplayDirection
        val = DisplayDirection.Direction[direction_name]
        dev = SimpleNamespace(root_device_id="root1", id="dev1",
                              name="Therm", display_direction=val)
        e = DisplayDirectionSelect.__new__(DisplayDirectionSelect)
        e._device = dev
        e._attr_unique_id = f"{dev.root_device_id}_{dev.id}_display_direction"
        return e

    def test_unique_id(self):
        e = self._make()
        assert e._attr_unique_id == "root1_dev1_display_direction"

    def test_current_option_normal(self):
        e = self._make("NORMAL")
        assert e.current_option == "NORMAL"

    def test_current_option_reversed(self):
        e = self._make("REVERSED")
        assert e.current_option == "REVERSED"

    def test_current_option_unknown_returns_none(self):
        from boschshcpy.services_impl import DisplayDirection
        dev = SimpleNamespace(root_device_id="r", id="d", name="X",
                              display_direction=DisplayDirection.Direction.UNKNOWN)
        e = DisplayDirectionSelect.__new__(DisplayDirectionSelect)
        e._device = dev
        assert e.current_option is None

    def test_current_option_none_returns_none(self):
        dev = SimpleNamespace(root_device_id="r", id="d", name="X",
                              display_direction=None)
        e = DisplayDirectionSelect.__new__(DisplayDirectionSelect)
        e._device = dev
        assert e.current_option is None

    def test_set_direction_writes(self):
        from boschshcpy.services_impl import DisplayDirection
        dev = SimpleNamespace(root_device_id="r", id="d", name="X",
                              display_direction=None)
        e = DisplayDirectionSelect.__new__(DisplayDirectionSelect)
        e._device = dev
        e._set_direction(DisplayDirection.Direction.REVERSED)
        assert dev.display_direction == DisplayDirection.Direction.REVERSED

    def test_created_for_thermostat(self):
        dev = _fake_device(display_direction=True, supports_display_direction=True)
        session = _make_session(thermostats=[dev])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "DisplayDirectionSelect" in types

    def test_skipped_when_attr_absent(self):
        dev = _fake_device()
        session = _make_session(thermostats=[dev])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "DisplayDirectionSelect" not in types

    def test_created_for_roomthermostat(self):
        dev = _fake_device(display_direction=True, supports_display_direction=True)
        session = _make_session(roomthermostats=[dev])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "DisplayDirectionSelect" in types


# ---------------------------------------------------------------------------
# DisplayedTemperatureSelect
# ---------------------------------------------------------------------------


class TestDisplayedTemperatureSelect:
    def _make(self, option_name="SETPOINT"):
        from boschshcpy.services_impl import DisplayedTemperatureConfiguration
        val = DisplayedTemperatureConfiguration.DisplayedTemperature[option_name]
        dev = SimpleNamespace(root_device_id="root1", id="dev1",
                              name="Therm", displayed_temperature=val)
        e = DisplayedTemperatureSelect.__new__(DisplayedTemperatureSelect)
        e._device = dev
        e._attr_unique_id = f"{dev.root_device_id}_{dev.id}_displayed_temperature"
        return e

    def test_unique_id(self):
        e = self._make()
        assert e._attr_unique_id == "root1_dev1_displayed_temperature"

    def test_current_option_setpoint(self):
        e = self._make("SETPOINT")
        assert e.current_option == "SETPOINT"

    def test_current_option_measured(self):
        e = self._make("MEASURED")
        assert e.current_option == "MEASURED"

    def test_current_option_unknown_returns_none(self):
        from boschshcpy.services_impl import DisplayedTemperatureConfiguration
        dev = SimpleNamespace(root_device_id="r", id="d", name="X",
                              displayed_temperature=DisplayedTemperatureConfiguration.DisplayedTemperature.UNKNOWN)
        e = DisplayedTemperatureSelect.__new__(DisplayedTemperatureSelect)
        e._device = dev
        assert e.current_option is None

    def test_current_option_none_returns_none(self):
        dev = SimpleNamespace(root_device_id="r", id="d", name="X",
                              displayed_temperature=None)
        e = DisplayedTemperatureSelect.__new__(DisplayedTemperatureSelect)
        e._device = dev
        assert e.current_option is None

    def test_set_displayed_writes(self):
        from boschshcpy.services_impl import DisplayedTemperatureConfiguration
        dev = SimpleNamespace(root_device_id="r", id="d", name="X",
                              displayed_temperature=None)
        e = DisplayedTemperatureSelect.__new__(DisplayedTemperatureSelect)
        e._device = dev
        e._set_displayed(DisplayedTemperatureConfiguration.DisplayedTemperature.MEASURED)
        assert dev.displayed_temperature == DisplayedTemperatureConfiguration.DisplayedTemperature.MEASURED

    def test_created_for_thermostat(self):
        dev = _fake_device(displayed_temperature=True, supports_displayed_temperature=True)
        session = _make_session(thermostats=[dev])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "DisplayedTemperatureSelect" in types

    def test_skipped_when_attr_absent(self):
        dev = _fake_device()
        session = _make_session(thermostats=[dev])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "DisplayedTemperatureSelect" not in types

    def test_created_for_roomthermostat(self):
        dev = _fake_device(displayed_temperature=True, supports_displayed_temperature=True)
        session = _make_session(roomthermostats=[dev])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "DisplayedTemperatureSelect" in types


# ---------------------------------------------------------------------------
# TerminalTypeSelect
# ---------------------------------------------------------------------------


class TestTerminalTypeSelect:
    def _make(self, option_name="NOT_CONNECTED"):
        from boschshcpy.services_impl import TerminalConfiguration
        val = TerminalConfiguration.Type[option_name]
        dev = SimpleNamespace(root_device_id="root1", id="dev1",
                              name="RTH", terminal_type=val)
        e = TerminalTypeSelect.__new__(TerminalTypeSelect)
        e._device = dev
        e._attr_unique_id = f"{dev.root_device_id}_{dev.id}_terminal_type"
        return e

    def test_unique_id(self):
        e = self._make()
        assert e._attr_unique_id == "root1_dev1_terminal_type"

    def test_current_option_not_connected(self):
        e = self._make("NOT_CONNECTED")
        assert e.current_option == "NOT_CONNECTED"

    def test_current_option_floor_sensor(self):
        e = self._make("FLOOR_SENSOR_CONNECTED")
        assert e.current_option == "FLOOR_SENSOR_CONNECTED"

    def test_current_option_unknown_returns_none(self):
        from boschshcpy.services_impl import TerminalConfiguration
        dev = SimpleNamespace(root_device_id="r", id="d", name="X",
                              terminal_type=TerminalConfiguration.Type.UNKNOWN)
        e = TerminalTypeSelect.__new__(TerminalTypeSelect)
        e._device = dev
        assert e.current_option is None

    def test_current_option_none_returns_none(self):
        dev = SimpleNamespace(root_device_id="r", id="d", name="X",
                              terminal_type=None)
        e = TerminalTypeSelect.__new__(TerminalTypeSelect)
        e._device = dev
        assert e.current_option is None

    def test_set_type_writes(self):
        from boschshcpy.services_impl import TerminalConfiguration
        dev = SimpleNamespace(root_device_id="r", id="d", name="X",
                              terminal_type=None)
        e = TerminalTypeSelect.__new__(TerminalTypeSelect)
        e._device = dev
        e._set_type(TerminalConfiguration.Type.FLOOR_SENSOR_CONNECTED)
        assert dev.terminal_type == TerminalConfiguration.Type.FLOOR_SENSOR_CONNECTED

    def test_created_when_attr_present(self):
        dev = _fake_device(terminal_type=True, supports_terminal_configuration=True)
        session = _make_session(thermostats=[dev])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "TerminalTypeSelect" in types

    def test_skipped_when_attr_absent(self):
        dev = _fake_device()
        session = _make_session(thermostats=[dev])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "TerminalTypeSelect" not in types

    def test_created_for_roomthermostat(self):
        dev = _fake_device(terminal_type=True, supports_terminal_configuration=True)
        session = _make_session(roomthermostats=[dev])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "TerminalTypeSelect" in types


# ---------------------------------------------------------------------------
# ValveTypeSelect
# ---------------------------------------------------------------------------


class TestValveTypeSelect:
    def _make(self, option_name="NORMALLY_CLOSE"):
        from boschshcpy.services_impl import WallThermostatConfiguration
        val = WallThermostatConfiguration.ValveType[option_name]
        dev = SimpleNamespace(root_device_id="root1", id="dev1",
                              name="TRV", valve_type=val)
        e = ValveTypeSelect.__new__(ValveTypeSelect)
        e._device = dev
        e._attr_unique_id = f"{dev.root_device_id}_{dev.id}_valve_type"
        return e

    def test_unique_id(self):
        e = self._make()
        assert e._attr_unique_id == "root1_dev1_valve_type"

    def test_current_option_normally_close(self):
        e = self._make("NORMALLY_CLOSE")
        assert e.current_option == "NORMALLY_CLOSE"

    def test_current_option_normally_open(self):
        e = self._make("NORMALLY_OPEN")
        assert e.current_option == "NORMALLY_OPEN"

    def test_current_option_unknown_returns_none(self):
        from boschshcpy.services_impl import WallThermostatConfiguration
        dev = SimpleNamespace(root_device_id="r", id="d", name="X",
                              valve_type=WallThermostatConfiguration.ValveType.UNKNOWN)
        e = ValveTypeSelect.__new__(ValveTypeSelect)
        e._device = dev
        assert e.current_option is None

    def test_current_option_none_returns_none(self):
        dev = SimpleNamespace(root_device_id="r", id="d", name="X",
                              valve_type=None)
        e = ValveTypeSelect.__new__(ValveTypeSelect)
        e._device = dev
        assert e.current_option is None

    def test_set_valve_writes(self):
        from boschshcpy.services_impl import WallThermostatConfiguration
        dev = SimpleNamespace(root_device_id="r", id="d", name="X",
                              valve_type=None)
        e = ValveTypeSelect.__new__(ValveTypeSelect)
        e._device = dev
        e._set_valve(WallThermostatConfiguration.ValveType.NORMALLY_OPEN)
        assert dev.valve_type == WallThermostatConfiguration.ValveType.NORMALLY_OPEN

    def test_created_when_attr_present(self):
        dev = _fake_device(valve_type=True, supports_wall_thermostat_configuration=True)
        session = _make_session(thermostats=[dev])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "ValveTypeSelect" in types

    def test_skipped_when_attr_absent(self):
        dev = _fake_device()
        session = _make_session(thermostats=[dev])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "ValveTypeSelect" not in types

    def test_created_for_roomthermostat(self):
        dev = _fake_device(valve_type=True, supports_wall_thermostat_configuration=True)
        session = _make_session(roomthermostats=[dev])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "ValveTypeSelect" in types


# ---------------------------------------------------------------------------
# HeaterTypeSelect
# ---------------------------------------------------------------------------


class TestHeaterTypeSelect:
    def _make(self, option_name="RADIATOR"):
        from boschshcpy.services_impl import WallThermostatConfiguration
        val = WallThermostatConfiguration.HeaterType[option_name]
        dev = SimpleNamespace(root_device_id="root1", id="dev1",
                              name="TRV", heater_type=val)
        e = HeaterTypeSelect.__new__(HeaterTypeSelect)
        e._device = dev
        e._attr_unique_id = f"{dev.root_device_id}_{dev.id}_heater_type"
        return e

    def test_unique_id(self):
        e = self._make()
        assert e._attr_unique_id == "root1_dev1_heater_type"

    def test_current_option_radiator(self):
        e = self._make("RADIATOR")
        assert e.current_option == "RADIATOR"

    def test_current_option_floor_heating(self):
        e = self._make("FLOOR_HEATING")
        assert e.current_option == "FLOOR_HEATING"

    def test_current_option_unknown_returns_none(self):
        from boschshcpy.services_impl import WallThermostatConfiguration
        dev = SimpleNamespace(root_device_id="r", id="d", name="X",
                              heater_type=WallThermostatConfiguration.HeaterType.UNKNOWN)
        e = HeaterTypeSelect.__new__(HeaterTypeSelect)
        e._device = dev
        assert e.current_option is None

    def test_current_option_none_returns_none(self):
        dev = SimpleNamespace(root_device_id="r", id="d", name="X",
                              heater_type=None)
        e = HeaterTypeSelect.__new__(HeaterTypeSelect)
        e._device = dev
        assert e.current_option is None

    def test_set_heater_writes(self):
        from boschshcpy.services_impl import WallThermostatConfiguration
        dev = SimpleNamespace(root_device_id="r", id="d", name="X",
                              heater_type=None)
        e = HeaterTypeSelect.__new__(HeaterTypeSelect)
        e._device = dev
        e._set_heater(WallThermostatConfiguration.HeaterType.CONVECTOR_PASSIVE)
        assert dev.heater_type == WallThermostatConfiguration.HeaterType.CONVECTOR_PASSIVE

    def test_created_when_attr_present(self):
        dev = _fake_device(heater_type=True, supports_wall_thermostat_configuration=True)
        session = _make_session(thermostats=[dev])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "HeaterTypeSelect" in types

    def test_skipped_when_attr_absent(self):
        dev = _fake_device()
        session = _make_session(thermostats=[dev])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "HeaterTypeSelect" not in types


# ---------------------------------------------------------------------------
# SwitchTypeSelect
# ---------------------------------------------------------------------------


class TestSwitchTypeSelect:
    def _make(self, option_name="PUSHBUTTON"):
        from boschshcpy.services_impl import SwitchConfiguration
        val = SwitchConfiguration.SwitchType[option_name]
        dev = SimpleNamespace(root_device_id="root1", id="dev1",
                              name="Relay", switch_type=val)
        e = SwitchTypeSelect.__new__(SwitchTypeSelect)
        e._device = dev
        e._attr_unique_id = f"{dev.root_device_id}_{dev.id}_switch_type"
        return e

    def test_unique_id(self):
        e = self._make()
        assert e._attr_unique_id == "root1_dev1_switch_type"

    def test_current_option_pushbutton(self):
        e = self._make("PUSHBUTTON")
        assert e.current_option == "PUSHBUTTON"

    def test_current_option_switch(self):
        e = self._make("SWITCH")
        assert e.current_option == "SWITCH"

    def test_current_option_none_not_in_options_returns_none(self):
        from boschshcpy.services_impl import SwitchConfiguration
        dev = SimpleNamespace(root_device_id="r", id="d", name="X",
                              switch_type=SwitchConfiguration.SwitchType.UNKNOWN)
        e = SwitchTypeSelect.__new__(SwitchTypeSelect)
        e._device = dev
        assert e.current_option is None

    def test_current_option_none_value_returns_none(self):
        dev = SimpleNamespace(root_device_id="r", id="d", name="X",
                              switch_type=None)
        e = SwitchTypeSelect.__new__(SwitchTypeSelect)
        e._device = dev
        assert e.current_option is None

    def test_set_switch_type_writes(self):
        from boschshcpy.services_impl import SwitchConfiguration
        dev = SimpleNamespace(root_device_id="r", id="d", name="X",
                              switch_type=None)
        e = SwitchTypeSelect.__new__(SwitchTypeSelect)
        e._device = dev
        e._set_switch_type(SwitchConfiguration.SwitchType.NONE)
        assert dev.switch_type == SwitchConfiguration.SwitchType.NONE

    def test_created_for_relay_when_attr_present(self):
        relay = _fake_device(switch_type=True, supports_switch_configuration=True)
        session = _make_session(micromodule_relays=[relay])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "SwitchTypeSelect" in types

    def test_skipped_when_attr_absent(self):
        relay = _fake_device()
        session = _make_session(micromodule_relays=[relay])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "SwitchTypeSelect" not in types

    def test_created_for_light_control(self):
        lc = _fake_device(switch_type=True, supports_switch_configuration=True)
        session = _make_session(micromodule_light_controls=[lc])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "SwitchTypeSelect" in types


# ---------------------------------------------------------------------------
# ActuatorTypeSelect
# ---------------------------------------------------------------------------


class TestActuatorTypeSelect:
    def _make(self, option_name="NORMALLY_OPEN"):
        from boschshcpy.services_impl import SwitchConfiguration
        val = SwitchConfiguration.ActuatorType[option_name]
        dev = SimpleNamespace(root_device_id="root1", id="dev1",
                              name="Relay", actuator_type=val)
        e = ActuatorTypeSelect.__new__(ActuatorTypeSelect)
        e._device = dev
        e._attr_unique_id = f"{dev.root_device_id}_{dev.id}_actuator_type"
        return e

    def test_unique_id(self):
        e = self._make()
        assert e._attr_unique_id == "root1_dev1_actuator_type"

    def test_current_option_normally_open(self):
        e = self._make("NORMALLY_OPEN")
        assert e.current_option == "NORMALLY_OPEN"

    def test_current_option_normally_closed(self):
        e = self._make("NORMALLY_CLOSED")
        assert e.current_option == "NORMALLY_CLOSED"

    def test_current_option_unknown_returns_none(self):
        from boschshcpy.services_impl import SwitchConfiguration
        dev = SimpleNamespace(root_device_id="r", id="d", name="X",
                              actuator_type=SwitchConfiguration.ActuatorType.UNKNOWN)
        e = ActuatorTypeSelect.__new__(ActuatorTypeSelect)
        e._device = dev
        assert e.current_option is None

    def test_current_option_none_returns_none(self):
        dev = SimpleNamespace(root_device_id="r", id="d", name="X",
                              actuator_type=None)
        e = ActuatorTypeSelect.__new__(ActuatorTypeSelect)
        e._device = dev
        assert e.current_option is None

    def test_set_actuator_type_writes(self):
        from boschshcpy.services_impl import SwitchConfiguration
        dev = SimpleNamespace(root_device_id="r", id="d", name="X",
                              actuator_type=None)
        e = ActuatorTypeSelect.__new__(ActuatorTypeSelect)
        e._device = dev
        e._set_actuator_type(SwitchConfiguration.ActuatorType.NORMALLY_CLOSED)
        assert dev.actuator_type == SwitchConfiguration.ActuatorType.NORMALLY_CLOSED

    def test_created_for_relay(self):
        relay = _fake_device(actuator_type=True, supports_switch_configuration=True)
        session = _make_session(micromodule_relays=[relay])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "ActuatorTypeSelect" in types

    def test_skipped_when_attr_absent(self):
        relay = _fake_device()
        session = _make_session(micromodule_relays=[relay])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "ActuatorTypeSelect" not in types

    def test_created_for_light_control(self):
        lc = _fake_device(actuator_type=True, supports_switch_configuration=True)
        session = _make_session(micromodule_light_controls=[lc])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "ActuatorTypeSelect" in types


# ---------------------------------------------------------------------------
# OutputModeSelect
# ---------------------------------------------------------------------------


class TestOutputModeSelect:
    def _make(self, option_name="ATTACHED"):
        from boschshcpy.services_impl import SwitchConfiguration
        val = SwitchConfiguration.OutputMode[option_name]
        dev = SimpleNamespace(root_device_id="root1", id="dev1",
                              name="Relay", output_mode=val)
        e = OutputModeSelect.__new__(OutputModeSelect)
        e._device = dev
        e._attr_unique_id = f"{dev.root_device_id}_{dev.id}_output_mode"
        return e

    def test_unique_id(self):
        e = self._make()
        assert e._attr_unique_id == "root1_dev1_output_mode"

    def test_current_option_attached(self):
        e = self._make("ATTACHED")
        assert e.current_option == "ATTACHED"

    def test_current_option_detached(self):
        e = self._make("DETACHED")
        assert e.current_option == "DETACHED"

    def test_current_option_unknown_returns_none(self):
        from boschshcpy.services_impl import SwitchConfiguration
        dev = SimpleNamespace(root_device_id="r", id="d", name="X",
                              output_mode=SwitchConfiguration.OutputMode.UNKNOWN)
        e = OutputModeSelect.__new__(OutputModeSelect)
        e._device = dev
        assert e.current_option is None

    def test_current_option_none_returns_none(self):
        dev = SimpleNamespace(root_device_id="r", id="d", name="X",
                              output_mode=None)
        e = OutputModeSelect.__new__(OutputModeSelect)
        e._device = dev
        assert e.current_option is None

    def test_set_output_mode_writes(self):
        from boschshcpy.services_impl import SwitchConfiguration
        dev = SimpleNamespace(root_device_id="r", id="d", name="X",
                              output_mode=None)
        e = OutputModeSelect.__new__(OutputModeSelect)
        e._device = dev
        e._set_output_mode(SwitchConfiguration.OutputMode.DETACHED_SHORT_PRESS)
        assert dev.output_mode == SwitchConfiguration.OutputMode.DETACHED_SHORT_PRESS

    def test_created_for_relay(self):
        relay = _fake_device(output_mode=True, supports_switch_configuration=True)
        session = _make_session(micromodule_relays=[relay])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "OutputModeSelect" in types

    def test_skipped_when_attr_absent(self):
        relay = _fake_device()
        session = _make_session(micromodule_relays=[relay])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "OutputModeSelect" not in types

    def test_created_for_light_control(self):
        lc = _fake_device(output_mode=True, supports_switch_configuration=True)
        session = _make_session(micromodule_light_controls=[lc])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "OutputModeSelect" in types
