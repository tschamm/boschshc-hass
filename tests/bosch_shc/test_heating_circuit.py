"""Tests for the heating-circuit climate entity."""

from types import SimpleNamespace

from boschshcpy import SHCHeatingCircuit
from homeassistant.components.climate.const import HVACAction, HVACMode

from custom_components.bosch_shc.climate import HeatingCircuit

OM = SHCHeatingCircuit.HeatingCircuitService.OperationMode


def _hc(operation_mode, on, setpoint=21.0):
    # bypass SHCEntity.__init__ (needs hass/registry); exercise the properties
    hc = HeatingCircuit.__new__(HeatingCircuit)
    hc._device = SimpleNamespace(
        operation_mode=operation_mode, on=on, setpoint_temperature=setpoint
    )
    return hc


def test_hvac_mode_auto():
    assert _hc(OM.AUTOMATIC, False).hvac_mode == HVACMode.AUTO


def test_hvac_mode_heat():
    assert _hc(OM.MANUAL, True).hvac_mode == HVACMode.HEAT


def test_hvac_action_heating_when_on():
    assert _hc(OM.MANUAL, True).hvac_action == HVACAction.HEATING


def test_hvac_action_idle_when_off():
    assert _hc(OM.AUTOMATIC, False).hvac_action == HVACAction.IDLE


def test_target_temperature_reads_setpoint():
    assert _hc(OM.MANUAL, True, 19.5).target_temperature == 19.5


def test_current_temperature_is_none():
    assert _hc(OM.MANUAL, True).current_temperature is None
