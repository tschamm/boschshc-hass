"""Regression tests for child-lock switch handling.

Two bugs were fixed:
1. Thermostats expose child lock as a `ThermostatService.State` enum, but the
   shared description compared against the bool `True`. `State.ON == True` is
   False, so the thermostat child-lock switch read OFF permanently.
2. Micromodule dimmers and BSM light switches carry the ChildProtection
   service but were never wired into the child-lock loop -> no entity.
"""

from types import SimpleNamespace

from boschshcpy import ThermostatService

from custom_components.bosch_shc.switch import SWITCH_TYPES, SHCSwitch


def test_thermostat_child_lock_description_uses_enum():
    enum_on = ThermostatService.State.ON
    # the root cause: the enum is never equal to the bool True
    assert (enum_on == True) is False  # noqa: E712
    assert SWITCH_TYPES["child_lock_thermostat"].on_value == enum_on
    # the ChildProtection (bool) description stays a bool
    assert SWITCH_TYPES["child_lock"].on_value is True


def _switch(description, child_lock_value):
    # bypass SHCEntity.__init__ (needs hass/registry) — we only exercise is_on
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = SimpleNamespace(child_lock=child_lock_value)
    sw.entity_description = description
    return sw


def test_is_on_thermostat_enum_on_reads_true():
    State = ThermostatService.State
    sw = _switch(SWITCH_TYPES["child_lock_thermostat"], State.ON)
    assert sw.is_on is True


def test_is_on_thermostat_enum_off_reads_false():
    State = ThermostatService.State
    sw = _switch(SWITCH_TYPES["child_lock_thermostat"], State.OFF)
    assert sw.is_on is False


def test_is_on_childprotection_bool():
    assert _switch(SWITCH_TYPES["child_lock"], True).is_on is True
    assert _switch(SWITCH_TYPES["child_lock"], False).is_on is False


def test_is_on_missing_attribute_returns_none():
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = SimpleNamespace()  # no child_lock attribute at all
    sw.entity_description = SWITCH_TYPES["child_lock"]
    assert sw.is_on is None
