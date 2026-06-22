"""Coverage for async_select_option methods and error-log paths in new select entities.

Each select entity has:
  - async_select_option() that calls await self._device.async_set_<x>(value)
  - current_option() that logs warnings on AttributeError/ValueError

This file covers those paths.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

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
# Helper
# ---------------------------------------------------------------------------


def _device_raising(**kwargs):
    """Create a device whose property raises AttributeError."""
    class _RaisingDev:
        root_device_id = "r"
        id = "d"
        name = "X"

    for k, v in kwargs.items():
        setattr(_RaisingDev, k, property(lambda self, _k=k, _v=v: (_ for _ in ()).throw(AttributeError(_k))))

    return _RaisingDev()


# ---------------------------------------------------------------------------
# StateAfterPowerOutageSelect — async + error path
# ---------------------------------------------------------------------------


class TestStateAfterPowerOutageSelectAsync:
    def _make(self, state_after_power_outage_name="OFF"):
        from boschshcpy.services_impl import PowerSwitchConfigurationService
        val = PowerSwitchConfigurationService.StateAfterPowerOutage[
            state_after_power_outage_name
        ]
        dev = SimpleNamespace(
            root_device_id="r", id="d", name="X",
            state_after_power_outage=val,
            async_set_state_after_power_outage=AsyncMock(),
        )
        e = StateAfterPowerOutageSelect.__new__(StateAfterPowerOutageSelect)
        e._device = dev
        e._attr_unique_id = "r_d_state_after_power_outage"
        return e

    def test_async_select_option_calls_device_method(self):
        e = self._make()
        asyncio.run(e.async_select_option("ON"))
        e._device.async_set_state_after_power_outage.assert_awaited_once()

    def test_async_select_option_sets_value(self):
        from boschshcpy.services_impl import PowerSwitchConfigurationService
        e = self._make()
        asyncio.run(e.async_select_option("LAST_STATE"))
        e._device.async_set_state_after_power_outage.assert_awaited_once_with(
            PowerSwitchConfigurationService.StateAfterPowerOutage.LAST_STATE
        )

    def test_current_option_attribute_error_returns_none(self):
        dev = _device_raising(state_after_power_outage="raises")
        e = StateAfterPowerOutageSelect.__new__(StateAfterPowerOutageSelect)
        e._device = dev
        assert e.current_option is None


# ---------------------------------------------------------------------------
# SmokeSensitivitySelect — async + error path
# ---------------------------------------------------------------------------


class TestSmokeSensitivitySelectAsync:
    def _make(self):
        from boschshcpy.services_impl import SmokeSensitivityService
        val = SmokeSensitivityService.SmokeSensitivityLevel.HIGH
        dev = SimpleNamespace(
            root_device_id="r", id="d", name="X",
            smoke_sensitivity=val,
            async_set_smoke_sensitivity=AsyncMock(),
        )
        e = SmokeSensitivitySelect.__new__(SmokeSensitivitySelect)
        e._device = dev
        e._attr_unique_id = "r_d_smoke_sensitivity"
        return e

    def test_async_select_option_calls_device_method(self):
        e = self._make()
        asyncio.run(e.async_select_option("MIDDLE"))
        e._device.async_set_smoke_sensitivity.assert_awaited_once()

    def test_async_select_option_sets_value(self):
        from boschshcpy.services_impl import SmokeSensitivityService
        e = self._make()
        asyncio.run(e.async_select_option("LOW"))
        e._device.async_set_smoke_sensitivity.assert_awaited_once_with(
            SmokeSensitivityService.SmokeSensitivityLevel.LOW
        )

    def test_current_option_attribute_error_returns_none(self):
        dev = _device_raising(smoke_sensitivity="raises")
        e = SmokeSensitivitySelect.__new__(SmokeSensitivitySelect)
        e._device = dev
        assert e.current_option is None


# ---------------------------------------------------------------------------
# DisplayDirectionSelect — async + error path
# ---------------------------------------------------------------------------


class TestDisplayDirectionSelectAsync:
    def _make(self):
        from boschshcpy.services_impl import DisplayDirection
        val = DisplayDirection.Direction.NORMAL
        dev = SimpleNamespace(
            root_device_id="r", id="d", name="X",
            display_direction=val,
            async_set_display_direction=AsyncMock(),
        )
        e = DisplayDirectionSelect.__new__(DisplayDirectionSelect)
        e._device = dev
        e._attr_unique_id = "r_d_display_direction"
        return e

    def test_async_select_option_calls_device_method(self):
        e = self._make()
        asyncio.run(e.async_select_option("REVERSED"))
        e._device.async_set_display_direction.assert_awaited_once()

    def test_async_select_option_sets_value(self):
        from boschshcpy.services_impl import DisplayDirection
        e = self._make()
        asyncio.run(e.async_select_option("REVERSED"))
        e._device.async_set_display_direction.assert_awaited_once_with(
            DisplayDirection.Direction.REVERSED
        )

    def test_current_option_attribute_error_returns_none(self):
        dev = _device_raising(display_direction="raises")
        e = DisplayDirectionSelect.__new__(DisplayDirectionSelect)
        e._device = dev
        assert e.current_option is None


# ---------------------------------------------------------------------------
# DisplayedTemperatureSelect — async + error path
# ---------------------------------------------------------------------------


class TestDisplayedTemperatureSelectAsync:
    def _make(self):
        from boschshcpy.services_impl import DisplayedTemperatureConfiguration
        val = DisplayedTemperatureConfiguration.DisplayedTemperature.SETPOINT
        dev = SimpleNamespace(
            root_device_id="r", id="d", name="X",
            displayed_temperature=val,
            async_set_displayed_temperature=AsyncMock(),
        )
        e = DisplayedTemperatureSelect.__new__(DisplayedTemperatureSelect)
        e._device = dev
        e._attr_unique_id = "r_d_displayed_temperature"
        return e

    def test_async_select_option_calls_device_method(self):
        e = self._make()
        asyncio.run(e.async_select_option("MEASURED"))
        e._device.async_set_displayed_temperature.assert_awaited_once()

    def test_async_select_option_sets_value(self):
        from boschshcpy.services_impl import DisplayedTemperatureConfiguration
        e = self._make()
        asyncio.run(e.async_select_option("MEASURED"))
        e._device.async_set_displayed_temperature.assert_awaited_once_with(
            DisplayedTemperatureConfiguration.DisplayedTemperature.MEASURED
        )

    def test_current_option_attribute_error_returns_none(self):
        dev = _device_raising(displayed_temperature="raises")
        e = DisplayedTemperatureSelect.__new__(DisplayedTemperatureSelect)
        e._device = dev
        assert e.current_option is None


# ---------------------------------------------------------------------------
# TerminalTypeSelect — async + error path
# ---------------------------------------------------------------------------


class TestTerminalTypeSelectAsync:
    def _make(self):
        from boschshcpy.services_impl import TerminalConfiguration
        val = TerminalConfiguration.Type.NOT_CONNECTED
        dev = SimpleNamespace(
            root_device_id="r", id="d", name="X",
            terminal_type=val,
            async_set_terminal_type=AsyncMock(),
        )
        e = TerminalTypeSelect.__new__(TerminalTypeSelect)
        e._device = dev
        e._attr_unique_id = "r_d_terminal_type"
        return e

    def test_async_select_option_calls_device_method(self):
        e = self._make()
        asyncio.run(e.async_select_option("FLOOR_SENSOR_CONNECTED"))
        e._device.async_set_terminal_type.assert_awaited_once()

    def test_async_select_option_sets_value(self):
        from boschshcpy.services_impl import TerminalConfiguration
        e = self._make()
        asyncio.run(e.async_select_option("FLOOR_SENSOR_CONNECTED"))
        e._device.async_set_terminal_type.assert_awaited_once_with(
            TerminalConfiguration.Type.FLOOR_SENSOR_CONNECTED
        )

    def test_current_option_attribute_error_returns_none(self):
        dev = _device_raising(terminal_type="raises")
        e = TerminalTypeSelect.__new__(TerminalTypeSelect)
        e._device = dev
        assert e.current_option is None


# ---------------------------------------------------------------------------
# ValveTypeSelect — async + error path
# ---------------------------------------------------------------------------


class TestValveTypeSelectAsync:
    def _make(self):
        from boschshcpy.services_impl import WallThermostatConfiguration
        val = WallThermostatConfiguration.ValveType.NORMALLY_CLOSE
        dev = SimpleNamespace(
            root_device_id="r", id="d", name="X",
            valve_type=val,
            async_set_valve_type=AsyncMock(),
        )
        e = ValveTypeSelect.__new__(ValveTypeSelect)
        e._device = dev
        e._attr_unique_id = "r_d_valve_type"
        return e

    def test_async_select_option_calls_device_method(self):
        e = self._make()
        asyncio.run(e.async_select_option("NORMALLY_OPEN"))
        e._device.async_set_valve_type.assert_awaited_once()

    def test_async_select_option_sets_value(self):
        from boschshcpy.services_impl import WallThermostatConfiguration
        e = self._make()
        asyncio.run(e.async_select_option("NORMALLY_OPEN"))
        e._device.async_set_valve_type.assert_awaited_once_with(
            WallThermostatConfiguration.ValveType.NORMALLY_OPEN
        )

    def test_current_option_attribute_error_returns_none(self):
        dev = _device_raising(valve_type="raises")
        e = ValveTypeSelect.__new__(ValveTypeSelect)
        e._device = dev
        assert e.current_option is None


# ---------------------------------------------------------------------------
# HeaterTypeSelect — async + error path
# ---------------------------------------------------------------------------


class TestHeaterTypeSelectAsync:
    def _make(self):
        from boschshcpy.services_impl import WallThermostatConfiguration
        val = WallThermostatConfiguration.HeaterType.RADIATOR
        dev = SimpleNamespace(
            root_device_id="r", id="d", name="X",
            heater_type=val,
            async_set_heater_type=AsyncMock(),
        )
        e = HeaterTypeSelect.__new__(HeaterTypeSelect)
        e._device = dev
        e._attr_unique_id = "r_d_heater_type"
        return e

    def test_async_select_option_calls_device_method(self):
        e = self._make()
        asyncio.run(e.async_select_option("FLOOR_HEATING"))
        e._device.async_set_heater_type.assert_awaited_once()

    def test_async_select_option_sets_value(self):
        from boschshcpy.services_impl import WallThermostatConfiguration
        e = self._make()
        asyncio.run(e.async_select_option("FLOOR_HEATING"))
        e._device.async_set_heater_type.assert_awaited_once_with(
            WallThermostatConfiguration.HeaterType.FLOOR_HEATING
        )

    def test_current_option_attribute_error_returns_none(self):
        dev = _device_raising(heater_type="raises")
        e = HeaterTypeSelect.__new__(HeaterTypeSelect)
        e._device = dev
        assert e.current_option is None


# ---------------------------------------------------------------------------
# SwitchTypeSelect — async + error path
# ---------------------------------------------------------------------------


class TestSwitchTypeSelectAsync:
    def _make(self):
        from boschshcpy.services_impl import SwitchConfiguration
        val = SwitchConfiguration.SwitchType.PUSHBUTTON
        dev = SimpleNamespace(
            root_device_id="r", id="d", name="X",
            switch_type=val,
            async_set_switch_type=AsyncMock(),
        )
        e = SwitchTypeSelect.__new__(SwitchTypeSelect)
        e._device = dev
        e._attr_unique_id = "r_d_switch_type"
        return e

    def test_async_select_option_calls_device_method(self):
        e = self._make()
        asyncio.run(e.async_select_option("SWITCH"))
        e._device.async_set_switch_type.assert_awaited_once()

    def test_async_select_option_sets_value(self):
        from boschshcpy.services_impl import SwitchConfiguration
        e = self._make()
        asyncio.run(e.async_select_option("NONE"))
        e._device.async_set_switch_type.assert_awaited_once_with(
            SwitchConfiguration.SwitchType.NONE
        )

    def test_current_option_attribute_error_returns_none(self):
        dev = _device_raising(switch_type="raises")
        e = SwitchTypeSelect.__new__(SwitchTypeSelect)
        e._device = dev
        assert e.current_option is None


# ---------------------------------------------------------------------------
# ActuatorTypeSelect — async + error path
# ---------------------------------------------------------------------------


class TestActuatorTypeSelectAsync:
    def _make(self):
        from boschshcpy.services_impl import SwitchConfiguration
        val = SwitchConfiguration.ActuatorType.NORMALLY_OPEN
        dev = SimpleNamespace(
            root_device_id="r", id="d", name="X",
            actuator_type=val,
            async_set_actuator_type=AsyncMock(),
        )
        e = ActuatorTypeSelect.__new__(ActuatorTypeSelect)
        e._device = dev
        e._attr_unique_id = "r_d_actuator_type"
        return e

    def test_async_select_option_calls_device_method(self):
        e = self._make()
        asyncio.run(e.async_select_option("NORMALLY_CLOSED"))
        e._device.async_set_actuator_type.assert_awaited_once()

    def test_async_select_option_sets_value(self):
        from boschshcpy.services_impl import SwitchConfiguration
        e = self._make()
        asyncio.run(e.async_select_option("NORMALLY_CLOSED"))
        e._device.async_set_actuator_type.assert_awaited_once_with(
            SwitchConfiguration.ActuatorType.NORMALLY_CLOSED
        )

    def test_current_option_attribute_error_returns_none(self):
        dev = _device_raising(actuator_type="raises")
        e = ActuatorTypeSelect.__new__(ActuatorTypeSelect)
        e._device = dev
        assert e.current_option is None


# ---------------------------------------------------------------------------
# OutputModeSelect — async + error path
# ---------------------------------------------------------------------------


class TestOutputModeSelectAsync:
    def _make(self):
        from boschshcpy.services_impl import SwitchConfiguration
        val = SwitchConfiguration.OutputMode.ATTACHED
        dev = SimpleNamespace(
            root_device_id="r", id="d", name="X",
            output_mode=val,
            async_set_output_mode=AsyncMock(),
        )
        e = OutputModeSelect.__new__(OutputModeSelect)
        e._device = dev
        e._attr_unique_id = "r_d_output_mode"
        return e

    def test_async_select_option_calls_device_method(self):
        e = self._make()
        asyncio.run(e.async_select_option("DETACHED"))
        e._device.async_set_output_mode.assert_awaited_once()

    def test_async_select_option_sets_value(self):
        from boschshcpy.services_impl import SwitchConfiguration
        e = self._make()
        asyncio.run(e.async_select_option("DETACHED_SHORT_PRESS"))
        e._device.async_set_output_mode.assert_awaited_once_with(
            SwitchConfiguration.OutputMode.DETACHED_SHORT_PRESS
        )

    def test_current_option_attribute_error_returns_none(self):
        dev = _device_raising(output_mode="raises")
        e = OutputModeSelect.__new__(OutputModeSelect)
        e._device = dev
        assert e.current_option is None


# ---------------------------------------------------------------------------
# Setup entry device_excluded coverage
# ---------------------------------------------------------------------------


def _make_excluded_session(device_list_name, device):
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
    defaults[device_list_name] = [device]
    device_helper = SimpleNamespace(**defaults)
    return SimpleNamespace(device_helper=device_helper)


def _run_setup_with_exclusion(session, excluded_id):
    from custom_components.bosch_shc.const import OPT_EXCLUDED_DEVICES
    entry_id = "E1"
    hass = SimpleNamespace(
        data={DOMAIN: {entry_id: {DATA_SESSION: session}}}
    )
    config_entry = SimpleNamespace(
        options={OPT_EXCLUDED_DEVICES: [excluded_id]},
        entry_id=entry_id,
        unique_id="UID1",
        async_on_unload=MagicMock(),
    )
    entities = []

    def add_entities(new_ents, *args, **kwargs):
        entities.extend(new_ents)

    with patch(
        "custom_components.bosch_shc.select.SHCShutterContact2Plus",
        new=type("SHCShutterContact2Plus", (), {}),
    ):
        asyncio.run(async_setup_entry(hass, config_entry, add_entities))
    return entities


def test_motion_detectors2_excluded_device_skipped():
    dev = SimpleNamespace(name="MD2", id="md2_excl", root_device_id="r",
                          serial="S", motion_sensitivity=True)
    session = _make_excluded_session("motion_detectors2", dev)
    entities = _run_setup_with_exclusion(session, "md2_excl")
    types = [type(e).__name__ for e in entities]
    assert "MotionSensitivitySelect" not in types


def test_smart_plugs_excluded_skips_state_after_power_outage():
    dev = SimpleNamespace(name="Plug", id="plug_excl", root_device_id="r",
                          serial="S", state_after_power_outage=True)
    session = _make_excluded_session("smart_plugs", dev)
    entities = _run_setup_with_exclusion(session, "plug_excl")
    types = [type(e).__name__ for e in entities]
    assert "StateAfterPowerOutageSelect" not in types


def test_smoke_detector_excluded_skips_smoke_sensitivity():
    dev = SimpleNamespace(name="SD", id="sd_excl", root_device_id="r",
                          serial="S", smoke_sensitivity=True)
    session = _make_excluded_session("smoke_detectors", dev)
    entities = _run_setup_with_exclusion(session, "sd_excl")
    types = [type(e).__name__ for e in entities]
    assert "SmokeSensitivitySelect" not in types


def test_thermostat_excluded_skips_display_direction():
    dev = SimpleNamespace(name="TH", id="th_excl", root_device_id="r",
                          serial="S", display_direction=True)
    session = _make_excluded_session("thermostats", dev)
    entities = _run_setup_with_exclusion(session, "th_excl")
    types = [type(e).__name__ for e in entities]
    assert "DisplayDirectionSelect" not in types


def test_relay_excluded_skips_switch_type():
    dev = SimpleNamespace(name="R", id="r_excl", root_device_id="r",
                          serial="S", switch_type=True)
    session = _make_excluded_session("micromodule_relays", dev)
    entities = _run_setup_with_exclusion(session, "r_excl")
    types = [type(e).__name__ for e in entities]
    assert "SwitchTypeSelect" not in types
