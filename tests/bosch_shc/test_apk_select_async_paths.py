"""Coverage for async_select_option methods and error-log paths in new select entities.

Each select entity has:
  - async_select_option() that calls hass.async_add_executor_job
  - current_option() that logs warnings on AttributeError/ValueError

This file covers those paths.
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
# Helper to build an entity with a mock hass
# ---------------------------------------------------------------------------


def _make_hass():
    """Minimal hass mock that records async_add_executor_job calls."""
    called = []

    async def _executor_job(fn, *args):
        called.append((fn, args))
        return fn(*args)

    hass = SimpleNamespace(
        async_add_executor_job=_executor_job,
        _calls=called,
    )
    return hass


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
        dev = SimpleNamespace(root_device_id="r", id="d", name="X",
                              state_after_power_outage=val)
        e = StateAfterPowerOutageSelect.__new__(StateAfterPowerOutageSelect)
        e._device = dev
        e._attr_unique_id = "r_d_state_after_power_outage"
        e.hass = _make_hass()
        return e

    def test_async_select_option_calls_executor(self):
        e = self._make()
        asyncio.run(e.async_select_option("ON"))
        assert len(e.hass._calls) == 1

    def test_async_select_option_sets_value(self):
        from boschshcpy.services_impl import PowerSwitchConfigurationService
        e = self._make()
        asyncio.run(e.async_select_option("LAST_STATE"))
        assert e._device.state_after_power_outage == PowerSwitchConfigurationService.StateAfterPowerOutage.LAST_STATE

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
        dev = SimpleNamespace(root_device_id="r", id="d", name="X",
                              smoke_sensitivity=val)
        e = SmokeSensitivitySelect.__new__(SmokeSensitivitySelect)
        e._device = dev
        e._attr_unique_id = "r_d_smoke_sensitivity"
        e.hass = _make_hass()
        return e

    def test_async_select_option_calls_executor(self):
        e = self._make()
        asyncio.run(e.async_select_option("MIDDLE"))
        assert len(e.hass._calls) == 1

    def test_async_select_option_sets_value(self):
        from boschshcpy.services_impl import SmokeSensitivityService
        e = self._make()
        asyncio.run(e.async_select_option("LOW"))
        assert e._device.smoke_sensitivity == SmokeSensitivityService.SmokeSensitivityLevel.LOW

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
        dev = SimpleNamespace(root_device_id="r", id="d", name="X",
                              display_direction=val)
        e = DisplayDirectionSelect.__new__(DisplayDirectionSelect)
        e._device = dev
        e._attr_unique_id = "r_d_display_direction"
        e.hass = _make_hass()
        return e

    def test_async_select_option_calls_executor(self):
        e = self._make()
        asyncio.run(e.async_select_option("REVERSED"))
        assert len(e.hass._calls) == 1

    def test_async_select_option_sets_value(self):
        from boschshcpy.services_impl import DisplayDirection
        e = self._make()
        asyncio.run(e.async_select_option("REVERSED"))
        assert e._device.display_direction == DisplayDirection.Direction.REVERSED

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
        dev = SimpleNamespace(root_device_id="r", id="d", name="X",
                              displayed_temperature=val)
        e = DisplayedTemperatureSelect.__new__(DisplayedTemperatureSelect)
        e._device = dev
        e._attr_unique_id = "r_d_displayed_temperature"
        e.hass = _make_hass()
        return e

    def test_async_select_option_calls_executor(self):
        e = self._make()
        asyncio.run(e.async_select_option("MEASURED"))
        assert len(e.hass._calls) == 1

    def test_async_select_option_sets_value(self):
        from boschshcpy.services_impl import DisplayedTemperatureConfiguration
        e = self._make()
        asyncio.run(e.async_select_option("MEASURED"))
        assert e._device.displayed_temperature == DisplayedTemperatureConfiguration.DisplayedTemperature.MEASURED

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
        dev = SimpleNamespace(root_device_id="r", id="d", name="X",
                              terminal_type=val)
        e = TerminalTypeSelect.__new__(TerminalTypeSelect)
        e._device = dev
        e._attr_unique_id = "r_d_terminal_type"
        e.hass = _make_hass()
        return e

    def test_async_select_option_calls_executor(self):
        e = self._make()
        asyncio.run(e.async_select_option("FLOOR_SENSOR_CONNECTED"))
        assert len(e.hass._calls) == 1

    def test_async_select_option_sets_value(self):
        from boschshcpy.services_impl import TerminalConfiguration
        e = self._make()
        asyncio.run(e.async_select_option("FLOOR_SENSOR_CONNECTED"))
        assert e._device.terminal_type == TerminalConfiguration.Type.FLOOR_SENSOR_CONNECTED

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
        dev = SimpleNamespace(root_device_id="r", id="d", name="X",
                              valve_type=val)
        e = ValveTypeSelect.__new__(ValveTypeSelect)
        e._device = dev
        e._attr_unique_id = "r_d_valve_type"
        e.hass = _make_hass()
        return e

    def test_async_select_option_calls_executor(self):
        e = self._make()
        asyncio.run(e.async_select_option("NORMALLY_OPEN"))
        assert len(e.hass._calls) == 1

    def test_async_select_option_sets_value(self):
        from boschshcpy.services_impl import WallThermostatConfiguration
        e = self._make()
        asyncio.run(e.async_select_option("NORMALLY_OPEN"))
        assert e._device.valve_type == WallThermostatConfiguration.ValveType.NORMALLY_OPEN

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
        dev = SimpleNamespace(root_device_id="r", id="d", name="X",
                              heater_type=val)
        e = HeaterTypeSelect.__new__(HeaterTypeSelect)
        e._device = dev
        e._attr_unique_id = "r_d_heater_type"
        e.hass = _make_hass()
        return e

    def test_async_select_option_calls_executor(self):
        e = self._make()
        asyncio.run(e.async_select_option("FLOOR_HEATING"))
        assert len(e.hass._calls) == 1

    def test_async_select_option_sets_value(self):
        from boschshcpy.services_impl import WallThermostatConfiguration
        e = self._make()
        asyncio.run(e.async_select_option("FLOOR_HEATING"))
        assert e._device.heater_type == WallThermostatConfiguration.HeaterType.FLOOR_HEATING

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
        dev = SimpleNamespace(root_device_id="r", id="d", name="X",
                              switch_type=val)
        e = SwitchTypeSelect.__new__(SwitchTypeSelect)
        e._device = dev
        e._attr_unique_id = "r_d_switch_type"
        e.hass = _make_hass()
        return e

    def test_async_select_option_calls_executor(self):
        e = self._make()
        asyncio.run(e.async_select_option("SWITCH"))
        assert len(e.hass._calls) == 1

    def test_async_select_option_sets_value(self):
        from boschshcpy.services_impl import SwitchConfiguration
        e = self._make()
        asyncio.run(e.async_select_option("NONE"))
        assert e._device.switch_type == SwitchConfiguration.SwitchType.NONE

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
        dev = SimpleNamespace(root_device_id="r", id="d", name="X",
                              actuator_type=val)
        e = ActuatorTypeSelect.__new__(ActuatorTypeSelect)
        e._device = dev
        e._attr_unique_id = "r_d_actuator_type"
        e.hass = _make_hass()
        return e

    def test_async_select_option_calls_executor(self):
        e = self._make()
        asyncio.run(e.async_select_option("NORMALLY_CLOSED"))
        assert len(e.hass._calls) == 1

    def test_async_select_option_sets_value(self):
        from boschshcpy.services_impl import SwitchConfiguration
        e = self._make()
        asyncio.run(e.async_select_option("NORMALLY_CLOSED"))
        assert e._device.actuator_type == SwitchConfiguration.ActuatorType.NORMALLY_CLOSED

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
        dev = SimpleNamespace(root_device_id="r", id="d", name="X",
                              output_mode=val)
        e = OutputModeSelect.__new__(OutputModeSelect)
        e._device = dev
        e._attr_unique_id = "r_d_output_mode"
        e.hass = _make_hass()
        return e

    def test_async_select_option_calls_executor(self):
        e = self._make()
        asyncio.run(e.async_select_option("DETACHED"))
        assert len(e.hass._calls) == 1

    def test_async_select_option_sets_value(self):
        from boschshcpy.services_impl import SwitchConfiguration
        e = self._make()
        asyncio.run(e.async_select_option("DETACHED_SHORT_PRESS"))
        assert e._device.output_mode == SwitchConfiguration.OutputMode.DETACHED_SHORT_PRESS

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
