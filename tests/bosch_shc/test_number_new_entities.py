"""Unit tests for number.py — ImpulseLengthNumber and HeatingCircuitSetpointNumber.

Uses __new__ bypass + SimpleNamespace device pattern. No HA harness.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from homeassistant.components.number import NumberMode
from homeassistant.helpers.entity import EntityCategory
from homeassistant.const import UnitOfTime

from custom_components.bosch_shc.number import (
    ImpulseLengthNumber,
    HeatingCircuitSetpointNumber,
)
from custom_components.bosch_shc.const import DATA_SESSION, DOMAIN


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_hass(session):
    return SimpleNamespace(data={DOMAIN: {"E1": {DATA_SESSION: session}}})


def _make_config_entry():
    return SimpleNamespace(options={}, entry_id="E1")


def _impulse_device(impulse_length=100):
    """Fake SHCMicromoduleImpulseRelay for ImpulseLengthNumber."""
    return SimpleNamespace(
        name="Relay Impulse",
        id="hdm:HomeMaticIP:relay1",
        root_device_id="aa:bb:cc:00:00:01",
        impulse_length=impulse_length,
    )


def _heating_circuit_svc(eco=18.0, comfort=21.0):
    """Fake HeatingCircuitService."""
    svc = SimpleNamespace(
        setpoint_temperature_eco=eco,
        setpoint_temperature_comfort=comfort,
    )
    return svc


def _heating_circuit_device(eco=18.0, comfort=21.0):
    svc = _heating_circuit_svc(eco, comfort)
    return SimpleNamespace(
        name="Heating Circuit",
        id="hdm:Rooms:hc1",
        root_device_id="aa:bb:cc:00:00:02",
        _heating_circuit_service=svc,
    )


def _make_impulse_number(impulse_length=100):
    dev = _impulse_device(impulse_length=impulse_length)
    num = ImpulseLengthNumber.__new__(ImpulseLengthNumber)
    num._device = dev
    num._attr_name = "Impulse Length"
    num._attr_unique_id = f"{dev.root_device_id}_{dev.id}_impulse_length"
    return num


def _make_heating_setpoint_number(getter, setter, eco=18.0, comfort=21.0):
    dev = _heating_circuit_device(eco, comfort)
    num = HeatingCircuitSetpointNumber.__new__(HeatingCircuitSetpointNumber)
    num._device = dev
    num._getter_name = getter
    num._setter_name = setter
    num._attr_name = "Setpoint"
    num._attr_unique_id = f"{dev.root_device_id}_{dev.id}_{setter}"
    return num


# ---------------------------------------------------------------------------
# ImpulseLengthNumber — class-level attributes
# ---------------------------------------------------------------------------

class TestImpulseLengthNumberClassAttrs:
    def test_entity_category_is_config(self):
        num = ImpulseLengthNumber.__new__(ImpulseLengthNumber)
        assert num._attr_entity_category == EntityCategory.CONFIG

    def test_native_unit_is_seconds(self):
        num = ImpulseLengthNumber.__new__(ImpulseLengthNumber)
        assert num._attr_native_unit_of_measurement == UnitOfTime.SECONDS

    def test_native_min_is_01(self):
        num = ImpulseLengthNumber.__new__(ImpulseLengthNumber)
        assert num._attr_native_min_value == 0.1

    def test_native_max_is_60(self):
        num = ImpulseLengthNumber.__new__(ImpulseLengthNumber)
        assert num._attr_native_max_value == 60.0

    def test_native_step_is_01(self):
        num = ImpulseLengthNumber.__new__(ImpulseLengthNumber)
        assert num._attr_native_step == 0.1

    def test_mode_is_box(self):
        num = ImpulseLengthNumber.__new__(ImpulseLengthNumber)
        assert num._attr_mode == NumberMode.BOX


# ---------------------------------------------------------------------------
# ImpulseLengthNumber — native_value (lib stores tenths of seconds)
# ---------------------------------------------------------------------------

class TestImpulseLengthNativeValue:
    def test_native_value_converts_tenths_to_seconds(self):
        """impulse_length=100 (tenths) → 10.0 seconds."""
        num = _make_impulse_number(impulse_length=100)
        assert num.native_value == pytest.approx(10.0)

    def test_native_value_10_tenths_is_1_second(self):
        num = _make_impulse_number(impulse_length=10)
        assert num.native_value == pytest.approx(1.0)

    def test_native_value_1_tenth_is_01_second(self):
        num = _make_impulse_number(impulse_length=1)
        assert num.native_value == pytest.approx(0.1)

    def test_native_value_none_when_impulse_length_none(self):
        num = _make_impulse_number(impulse_length=None)
        assert num.native_value is None

    def test_native_value_none_when_attribute_missing(self):
        dev = SimpleNamespace(name="relay", id="r1", root_device_id="root1")
        # no impulse_length attr → getattr returns None
        num = ImpulseLengthNumber.__new__(ImpulseLengthNumber)
        num._device = dev
        assert num.native_value is None


# ---------------------------------------------------------------------------
# ImpulseLengthNumber — set_native_value
# ---------------------------------------------------------------------------

class TestImpulseLengthSetNativeValue:
    def test_set_value_converts_seconds_to_tenths(self):
        """set_native_value(5.0) → device.impulse_length = 50."""
        written = []

        class _Dev:
            name = "relay"
            id = "r1"
            root_device_id = "root1"
            _len = 100

            @property
            def impulse_length(self_):
                return self_._len

            @impulse_length.setter
            def impulse_length(self_, v):
                written.append(v)
                self_._len = v

        dev = _Dev()
        num = ImpulseLengthNumber.__new__(ImpulseLengthNumber)
        num._device = dev
        num.set_native_value(5.0)
        assert written == [50]

    def test_set_value_clamps_to_max(self):
        """Values above 60 s are clamped to 60 s = 600 tenths."""
        written = []

        class _Dev:
            name = "relay"

            @property
            def impulse_length(self_):
                return 100

            @impulse_length.setter
            def impulse_length(self_, v):
                written.append(v)

        dev = _Dev()
        num = ImpulseLengthNumber.__new__(ImpulseLengthNumber)
        num._device = dev
        num.set_native_value(999.0)
        assert written == [600]

    def test_set_value_clamps_to_min(self):
        """Values below 0.1 s are clamped to 0.1 s = 1 tenth."""
        written = []

        class _Dev:
            name = "relay"

            @property
            def impulse_length(self_):
                return 100

            @impulse_length.setter
            def impulse_length(self_, v):
                written.append(v)

        dev = _Dev()
        num = ImpulseLengthNumber.__new__(ImpulseLengthNumber)
        num._device = dev
        num.set_native_value(0.0)
        assert written == [1]


# ---------------------------------------------------------------------------
# HeatingCircuitSetpointNumber — native_value
# ---------------------------------------------------------------------------


class TestHeatingCircuitSetpointNativeValue:
    def test_eco_returns_eco_temperature(self):
        num = _make_heating_setpoint_number(
            "setpoint_temperature_eco", "setpoint_temperature_eco", eco=18.0
        )
        assert num.native_value == pytest.approx(18.0)

    def test_comfort_returns_comfort_temperature(self):
        num = _make_heating_setpoint_number(
            "setpoint_temperature_comfort", "setpoint_temperature_comfort", comfort=21.0
        )
        assert num.native_value == pytest.approx(21.0)

    def test_returns_none_when_service_absent(self):
        dev = SimpleNamespace(
            name="HC",
            id="hc1",
            root_device_id="root1",
            _heating_circuit_service=None,
        )
        num = HeatingCircuitSetpointNumber.__new__(HeatingCircuitSetpointNumber)
        num._device = dev
        num._getter_name = "setpoint_temperature_eco"
        assert num.native_value is None

    def test_returns_none_when_attribute_error(self):
        """When service raises AttributeError, return None + log warning."""
        class _BadSvc:
            @property
            def setpoint_temperature_eco(self_):
                raise AttributeError("missing")

        dev = SimpleNamespace(
            name="HC",
            id="hc1",
            root_device_id="root1",
            _heating_circuit_service=_BadSvc(),
        )
        num = HeatingCircuitSetpointNumber.__new__(HeatingCircuitSetpointNumber)
        num._device = dev
        num._getter_name = "setpoint_temperature_eco"
        with patch("custom_components.bosch_shc.number.LOGGER") as mock_log:
            result = num.native_value
        assert result is None
        mock_log.warning.assert_called_once()


# ---------------------------------------------------------------------------
# HeatingCircuitSetpointNumber — set_native_value
# ---------------------------------------------------------------------------

class TestHeatingCircuitSetpointSetValue:
    def test_eco_set_value_writes_to_service(self):
        class _Svc:
            setpoint_temperature_eco = 18.0

        svc = _Svc()
        dev = SimpleNamespace(
            name="HC", id="hc1", root_device_id="root1",
            _heating_circuit_service=svc,
        )
        num = HeatingCircuitSetpointNumber.__new__(HeatingCircuitSetpointNumber)
        num._device = dev
        num._getter_name = "setpoint_temperature_eco"
        num._setter_name = "setpoint_temperature_eco"

        num.set_native_value(19.0)
        assert svc.setpoint_temperature_eco == pytest.approx(19.0)

    def test_set_value_clamps_to_min(self):
        """Values below 5 °C → clamped to 5 °C."""
        class _Svc:
            setpoint_temperature_eco = 18.0

        svc = _Svc()
        dev = SimpleNamespace(
            name="HC", id="hc1", root_device_id="root1",
            _heating_circuit_service=svc,
        )
        num = HeatingCircuitSetpointNumber.__new__(HeatingCircuitSetpointNumber)
        num._device = dev
        num._getter_name = "setpoint_temperature_eco"
        num._setter_name = "setpoint_temperature_eco"

        num.set_native_value(1.0)
        assert svc.setpoint_temperature_eco == pytest.approx(5.0)

    def test_set_value_clamps_to_max(self):
        """Values above 30 °C → clamped to 30 °C."""
        class _Svc:
            setpoint_temperature_comfort = 21.0

        svc = _Svc()
        dev = SimpleNamespace(
            name="HC", id="hc1", root_device_id="root1",
            _heating_circuit_service=svc,
        )
        num = HeatingCircuitSetpointNumber.__new__(HeatingCircuitSetpointNumber)
        num._device = dev
        num._getter_name = "setpoint_temperature_comfort"
        num._setter_name = "setpoint_temperature_comfort"

        num.set_native_value(100.0)
        assert svc.setpoint_temperature_comfort == pytest.approx(30.0)

    def test_set_value_with_no_service_logs_warning(self):
        """When service is None, log a warning and do nothing."""
        dev = SimpleNamespace(
            name="HC", id="hc1", root_device_id="root1",
            _heating_circuit_service=None,
        )
        num = HeatingCircuitSetpointNumber.__new__(HeatingCircuitSetpointNumber)
        num._device = dev
        num._getter_name = "setpoint_temperature_eco"
        num._setter_name = "setpoint_temperature_eco"

        with patch("custom_components.bosch_shc.number.LOGGER") as mock_log:
            num.set_native_value(20.0)  # must not raise
        mock_log.warning.assert_called_once()


# ---------------------------------------------------------------------------
# async_setup_entry — new entity loops
# ---------------------------------------------------------------------------

class TestNumberSetupNewEntities:
    """Verify that the new number entity loops in async_setup_entry work."""

    def _run(self, session):
        from custom_components.bosch_shc.number import async_setup_entry
        hass = _make_hass(session)
        entry = _make_config_entry()
        collected = []

        def add(entities):
            collected.extend(entities)

        asyncio.run(async_setup_entry(hass, entry, add))
        return collected

    def test_impulse_relay_with_length_produces_number(self):
        dev = _impulse_device(impulse_length=50)
        session = SimpleNamespace(
            device_helper=SimpleNamespace(
                thermostats=[],
                roomthermostats=[],
                micromodule_impulse_relays=[dev],
                heating_circuits=[],
            )
        )
        result = self._run(session)
        assert len(result) == 1
        assert isinstance(result[0], ImpulseLengthNumber)

    def test_impulse_relay_with_none_length_is_skipped(self):
        dev = _impulse_device(impulse_length=None)
        session = SimpleNamespace(
            device_helper=SimpleNamespace(
                thermostats=[],
                roomthermostats=[],
                micromodule_impulse_relays=[dev],
                heating_circuits=[],
            )
        )
        result = self._run(session)
        assert result == []

    def test_heating_circuit_produces_two_setpoint_numbers(self):
        dev = _heating_circuit_device()
        session = SimpleNamespace(
            device_helper=SimpleNamespace(
                thermostats=[],
                roomthermostats=[],
                micromodule_impulse_relays=[],
                heating_circuits=[dev],
            )
        )
        result = self._run(session)
        assert len(result) == 2
        assert all(isinstance(e, HeatingCircuitSetpointNumber) for e in result)
        names = [e._attr_name for e in result]
        assert "Setpoint Eco Temperature" in names
        assert "Setpoint Comfort Temperature" in names

    def test_two_heating_circuits_produce_four_numbers(self):
        session = SimpleNamespace(
            device_helper=SimpleNamespace(
                thermostats=[],
                roomthermostats=[],
                micromodule_impulse_relays=[],
                heating_circuits=[_heating_circuit_device(), _heating_circuit_device()],
            )
        )
        result = self._run(session)
        assert len(result) == 4
        assert all(isinstance(e, HeatingCircuitSetpointNumber) for e in result)

    def test_no_new_devices_adds_nothing(self):
        session = SimpleNamespace(
            device_helper=SimpleNamespace(
                thermostats=[],
                roomthermostats=[],
                micromodule_impulse_relays=[],
                heating_circuits=[],
            )
        )
        result = self._run(session)
        assert result == []
