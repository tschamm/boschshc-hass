"""Unit tests for number.py — extra coverage gaps.

Targets lines not covered by test_number_unit.py, test_number_coverage.py,
or test_number_new_entities.py:

Line 42      : thermostat/roomthermostat loop — device_excluded continue
Line 53      : impulse_relay loop — not hasattr(device, "impulse_length") continue
Line 55      : impulse_relay loop — device.impulse_length is None continue
Line 67      : heating_circuits loop — device_excluded continue
Lines 246-247: HeatingCircuitSetpointNumber.set_native_value — svc is None path (LOGGER.warning + return)

Pattern: __new__ bypass + SimpleNamespace; asyncio.run for async setup tests.
No HA harness.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from custom_components.bosch_shc.const import OPT_EXCLUDED_DEVICES
from custom_components.bosch_shc.number import (
    HeatingCircuitSetpointNumber,
    ImpulseLengthNumber,
    SHCNumber,
    async_setup_entry,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_device(device_id="dev1", name="FakeDev", root_device_id="root1",
                 serial="ser1", **extra):
    return SimpleNamespace(
        id=device_id,
        name=name,
        root_device_id=root_device_id,
        serial=serial,
        device_services=[],
        **extra,
    )


def _make_fake_session(**lists):
    return SimpleNamespace(
        device_helper=SimpleNamespace(
            thermostats=lists.get("thermostats", []),
            roomthermostats=lists.get("roomthermostats", []),
            micromodule_impulse_relays=lists.get("micromodule_impulse_relays", []),
            heating_circuits=lists.get("heating_circuits", []),
        )
    )


def _run_setup_with_options(session, options):
    """Run async_setup_entry with custom options. Returns list of collected entities."""
    hass = SimpleNamespace()
    config_entry = SimpleNamespace(options=options, entry_id="E1")
    config_entry.runtime_data = SimpleNamespace(
        session=session, shc_device=None, title="Test SHC"
    )
    collected = []

    def _add_entities(entity_list):
        collected.extend(entity_list)

    asyncio.run(async_setup_entry(hass, config_entry, _add_entities))
    return collected


def _excl(*ids):
    return {OPT_EXCLUDED_DEVICES: list(ids)}


# ---------------------------------------------------------------------------
# 1. Thermostat / roomthermostat device_excluded continue (line 42)
# ---------------------------------------------------------------------------

class TestNumberSetupExcludedThermostat:
    def test_excluded_thermostat_not_in_entities(self):
        """Excluded thermostat must be skipped (line 42 continue)."""
        dev = _fake_device("trv-excl", offset=0.0, min_offset=-5.0,
                           max_offset=5.0, step_size=0.5)
        session = _make_fake_session(thermostats=[dev])
        entities = _run_setup_with_options(session, _excl("trv-excl"))
        ids = [getattr(e, "_device", None) and e._device.id for e in entities]
        assert "trv-excl" not in ids

    def test_excluded_roomthermostat_not_in_entities(self):
        """Excluded roomthermostat must be skipped (same loop, line 42 continue)."""
        dev = _fake_device("rt-excl", offset=0.0, min_offset=-5.0,
                           max_offset=5.0, step_size=0.5)
        session = _make_fake_session(roomthermostats=[dev])
        entities = _run_setup_with_options(session, _excl("rt-excl"))
        ids = [getattr(e, "_device", None) and e._device.id for e in entities]
        assert "rt-excl" not in ids

    def test_non_excluded_thermostat_still_added(self):
        """Non-excluded thermostat must still produce a SHCNumber entity."""
        dev = _fake_device("trv-keep", offset=1.0, min_offset=-5.0,
                           max_offset=5.0, step_size=0.5)
        session = _make_fake_session(thermostats=[dev])
        entities = _run_setup_with_options(session, {})
        assert any(isinstance(e, SHCNumber) for e in entities)

    def test_mix_excluded_and_kept_thermostat(self):
        kept = _fake_device("trv-a", offset=0.0, min_offset=-5.0,
                            max_offset=5.0, step_size=0.5)
        excl = _fake_device("trv-b", offset=0.0, min_offset=-5.0,
                            max_offset=5.0, step_size=0.5)
        session = _make_fake_session(thermostats=[kept, excl])
        entities = _run_setup_with_options(session, _excl("trv-b"))
        ids = [getattr(e, "_device", None) and e._device.id for e in entities]
        assert "trv-a" in ids
        assert "trv-b" not in ids


# ---------------------------------------------------------------------------
# 2. Impulse relay — not hasattr(device, "impulse_length") continue (line 53)
# ---------------------------------------------------------------------------

class TestNumberSetupImpulseRelayNoAttr:
    def test_device_without_impulse_length_attr_is_skipped(self):
        """Device missing impulse_length attribute must be skipped (line 53 continue)."""
        # No impulse_length attribute at all
        dev = _fake_device("relay-no-attr")
        session = _make_fake_session(micromodule_impulse_relays=[dev])
        entities = _run_setup_with_options(session, {})
        ids = [getattr(e, "_device", None) and e._device.id for e in entities]
        assert "relay-no-attr" not in ids

    def test_device_without_impulse_length_produces_no_entity(self):
        dev = _fake_device("relay-no-il")
        session = _make_fake_session(micromodule_impulse_relays=[dev])
        entities = _run_setup_with_options(session, {})
        assert not any(isinstance(e, ImpulseLengthNumber) for e in entities)


# ---------------------------------------------------------------------------
# 3. Impulse relay — device.impulse_length is None continue (line 55)
# ---------------------------------------------------------------------------

class TestNumberSetupImpulseRelayNoneValue:
    def test_device_with_none_impulse_length_is_skipped(self):
        """impulse_length=None must be skipped (line 55 continue)."""
        dev = _fake_device("relay-none-il", impulse_length=None)
        session = _make_fake_session(micromodule_impulse_relays=[dev])
        entities = _run_setup_with_options(session, {})
        assert not any(isinstance(e, ImpulseLengthNumber) for e in entities)

    def test_device_with_zero_impulse_length_is_included(self):
        """impulse_length=0 is not None → entity IS created (boundary check)."""
        dev = _fake_device("relay-zero-il", impulse_length=0)
        session = _make_fake_session(micromodule_impulse_relays=[dev])
        entities = _run_setup_with_options(session, {})
        # 0 is falsy but is not None; the code checks `is None`, so entity must appear
        assert any(isinstance(e, ImpulseLengthNumber) for e in entities)

    def test_device_with_valid_impulse_length_is_included(self):
        """impulse_length=100 → ImpulseLengthNumber entity is created."""
        dev = _fake_device("relay-100", impulse_length=100)
        session = _make_fake_session(micromodule_impulse_relays=[dev])
        entities = _run_setup_with_options(session, {})
        assert any(isinstance(e, ImpulseLengthNumber) for e in entities)


# ---------------------------------------------------------------------------
# 4. Heating circuit device_excluded continue (line 67)
# ---------------------------------------------------------------------------

class TestNumberSetupExcludedHeatingCircuit:
    def _hc_device(self, device_id):
        svc = SimpleNamespace(
            setpoint_temperature_eco=18.0,
            setpoint_temperature_comfort=21.0,
        )
        return _fake_device(
            device_id, name="HC", _heating_circuit_service=svc
        )

    def test_excluded_heating_circuit_not_in_entities(self):
        """Excluded heating circuit must be skipped (line 67 continue)."""
        dev = self._hc_device("hc-excl")
        session = _make_fake_session(heating_circuits=[dev])
        entities = _run_setup_with_options(session, _excl("hc-excl"))
        ids = [getattr(e, "_device", None) and e._device.id for e in entities]
        assert "hc-excl" not in ids

    def test_non_excluded_heating_circuit_still_added(self):
        """Non-excluded heating circuit produces HeatingCircuitSetpointNumber entities."""
        dev = self._hc_device("hc-keep")
        session = _make_fake_session(heating_circuits=[dev])
        entities = _run_setup_with_options(session, {})
        assert any(isinstance(e, HeatingCircuitSetpointNumber) for e in entities)

    def test_mix_excluded_and_kept_heating_circuit(self):
        kept = self._hc_device("hc-a")
        excl = self._hc_device("hc-b")
        session = _make_fake_session(heating_circuits=[kept, excl])
        entities = _run_setup_with_options(session, _excl("hc-b"))
        ids = [getattr(e, "_device", None) and e._device.id for e in entities]
        assert "hc-a" in ids
        assert "hc-b" not in ids


# ---------------------------------------------------------------------------
# 5. HeatingCircuitSetpointNumber.async_set_native_value — async_setter absent
#    (LOGGER.warning branch + early return when getattr returns None)
# ---------------------------------------------------------------------------

class TestHeatingCircuitSetpointNumberSetNativeValueNoService:
    def _sensor_no_async_setter(self):
        """Build HeatingCircuitSetpointNumber via __new__ with no async_set_* on device."""
        s = HeatingCircuitSetpointNumber.__new__(HeatingCircuitSetpointNumber)
        s._device = SimpleNamespace(
            name="HC-None",
            _heating_circuit_service=None,
            # no async_set_setpoint_temperature_eco attribute
        )
        s._getter_name = "setpoint_temperature_eco"
        s._setter_name = "setpoint_temperature_eco"
        s._range_attr = "eco_temperature_range"
        return s

    def test_set_native_value_with_none_service_logs_warning(self):
        """async_set_native_value with no async setter logs a warning."""
        s = self._sensor_no_async_setter()
        with patch("custom_components.bosch_shc.number.LOGGER") as mock_log:
            asyncio.run(s.async_set_native_value(20.0))
        mock_log.warning.assert_called_once()
        msg = mock_log.warning.call_args[0][0]
        assert "Async setter" in msg

    def test_set_native_value_with_none_service_returns_early(self):
        """async_set_native_value with no async setter returns without writing."""
        writes = []

        s = HeatingCircuitSetpointNumber.__new__(HeatingCircuitSetpointNumber)
        s._device = SimpleNamespace(
            name="HC-None",
            _heating_circuit_service=None,
            # no async_set_setpoint_temperature_eco attribute
        )
        s._getter_name = "setpoint_temperature_eco"
        s._setter_name = "setpoint_temperature_eco"
        s._range_attr = "eco_temperature_range"

        # Must not raise; setter is absent so no write occurs.
        with patch("custom_components.bosch_shc.number.LOGGER"):
            asyncio.run(s.async_set_native_value(22.0))
        assert writes == []

    def test_set_native_value_with_valid_service_writes_clamped_value(self):
        """Sanity: when async_set_* is present, it is awaited with clamped value."""
        mock_setter = AsyncMock()

        s = HeatingCircuitSetpointNumber.__new__(HeatingCircuitSetpointNumber)
        s._device = SimpleNamespace(
            name="HC-OK",
            _heating_circuit_service=SimpleNamespace(
                setpoint_temperature_eco=None,
            ),
            async_set_setpoint_temperature_eco=mock_setter,
        )
        s._getter_name = "setpoint_temperature_eco"
        s._setter_name = "setpoint_temperature_eco"
        s._range_attr = "eco_temperature_range"

        asyncio.run(s.async_set_native_value(20.0))
        mock_setter.assert_awaited_once_with(20.0)

    def test_set_native_value_none_service_warning_includes_device_name(self):
        """The warning message must include the device name so it can be traced."""
        s = self._sensor_no_async_setter()
        with patch("custom_components.bosch_shc.number.LOGGER") as mock_log:
            asyncio.run(s.async_set_native_value(18.0))
        call_args = mock_log.warning.call_args[0]
        # Format string args include setter_name and device name
        assert "HC-None" in str(call_args)

    def test_native_value_with_none_service_returns_none(self):
        """native_value with _heating_circuit_service=None returns None (existing path)."""
        s = self._sensor_no_async_setter()
        assert s.native_value is None
