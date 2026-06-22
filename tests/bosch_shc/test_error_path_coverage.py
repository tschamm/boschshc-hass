"""Tests for 6 uncovered error-path lines (Silver action-exceptions rule).

Targets:
- __init__.py:126-127  scenario trigger → SHCException/SHCConnectionError → ServiceValidationError
- binary_sensor.py:520-521  async_request_smoketest → SHCException → HomeAssistantError
- binary_sensor.py:539-540  async_request_alarmstate → SHCException → HomeAssistantError

Pattern: __new__ bypass + SimpleNamespace; asyncio.run for async methods.
No HA harness.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from boschshcpy.exceptions import SHCConnectionError, SHCException
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from custom_components.bosch_shc.binary_sensor import SmokeDetectorSensor
from custom_components.bosch_shc.const import (
    ATTR_TITLE,
    SERVICE_TRIGGER_SCENARIO,
)
from homeassistant.const import ATTR_NAME


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(coro):
    return asyncio.run(coro)


def _make_smoke_sensor(*, executor_side_effect):
    """Build a SmokeDetectorSensor via __new__ bypass with a faked hass."""
    s = SmokeDetectorSensor.__new__(SmokeDetectorSensor)
    s._device = SimpleNamespace(
        name="Smoke Detector 1",
        smoketest_requested=MagicMock(side_effect=executor_side_effect),
    )
    s._attr_name = "Smoke Detector 1"

    hass = MagicMock()
    hass.async_add_executor_job = AsyncMock(side_effect=executor_side_effect)
    s._hass = hass
    return s


def _make_alarm_sensor(*, executor_side_effect):
    """Build a SmokeDetectorSensor for alarm-state tests."""
    s = SmokeDetectorSensor.__new__(SmokeDetectorSensor)
    s._device = SimpleNamespace(
        name="Smoke Detector 1",
    )
    s._attr_name = "Smoke Detector 1"

    hass = MagicMock()
    hass.async_add_executor_job = AsyncMock(side_effect=executor_side_effect)
    s._hass = hass
    return s


# ---------------------------------------------------------------------------
# binary_sensor.py:520-521 — async_request_smoketest error path
# ---------------------------------------------------------------------------

class TestSmokeDetectorSensorSmoketestError:
    """async_request_smoketest must wrap SHCException as HomeAssistantError."""

    def test_shcexception_raises_homeassistanterror(self):
        """SHCException from smoketest_requested → HomeAssistantError (lines 520-521)."""
        s = _make_smoke_sensor(executor_side_effect=SHCException("comm error"))
        with pytest.raises(HomeAssistantError):
            _run(s.async_request_smoketest())

    def test_shcconnectionerror_raises_homeassistanterror(self):
        """SHCConnectionError from smoketest_requested → HomeAssistantError (lines 520-521)."""
        s = _make_smoke_sensor(executor_side_effect=SHCConnectionError("timeout"))
        with pytest.raises(HomeAssistantError):
            _run(s.async_request_smoketest())

    def test_error_message_contains_device_name(self):
        """HomeAssistantError message must mention the device name."""
        s = _make_smoke_sensor(executor_side_effect=SHCException("fail"))
        with pytest.raises(HomeAssistantError, match="Smoke Detector 1"):
            _run(s.async_request_smoketest())

    def test_no_error_on_success(self):
        """When smoketest_requested succeeds, no exception is raised."""
        s = _make_smoke_sensor(executor_side_effect=None)
        s._hass.async_add_executor_job = AsyncMock(return_value=None)
        _run(s.async_request_smoketest())  # must not raise


# ---------------------------------------------------------------------------
# binary_sensor.py:539-540 — async_request_alarmstate error path
# ---------------------------------------------------------------------------

class TestSmokeDetectorSensorAlarmstateError:
    """async_request_alarmstate must wrap SHCException as HomeAssistantError."""

    def test_shcexception_raises_homeassistanterror(self):
        """SHCException from set_alarmstate → HomeAssistantError (lines 539-540)."""
        s = _make_alarm_sensor(executor_side_effect=SHCException("comm error"))
        with pytest.raises(HomeAssistantError):
            _run(s.async_request_alarmstate("INTRUSION_ALARM_ON"))

    def test_shcconnectionerror_raises_homeassistanterror(self):
        """SHCConnectionError from set_alarmstate → HomeAssistantError (lines 539-540)."""
        s = _make_alarm_sensor(executor_side_effect=SHCConnectionError("timeout"))
        with pytest.raises(HomeAssistantError):
            _run(s.async_request_alarmstate("INTRUSION_ALARM_ON"))

    def test_error_message_contains_device_name(self):
        """HomeAssistantError message must mention the device name."""
        s = _make_alarm_sensor(executor_side_effect=SHCException("fail"))
        with pytest.raises(HomeAssistantError, match="Smoke Detector 1"):
            _run(s.async_request_alarmstate("SOME_CMD"))

    def test_no_error_on_success(self):
        """When set_alarmstate succeeds, no exception is raised."""
        s = _make_alarm_sensor(executor_side_effect=None)
        s._hass.async_add_executor_job = AsyncMock(return_value=None)
        _run(s.async_request_alarmstate("IDLE_OFF"))  # must not raise


# ---------------------------------------------------------------------------
# __init__.py:126-127 — scenario trigger error path
# ---------------------------------------------------------------------------

class TestScenarioServiceCallTriggerError:
    """scenario_service_call must raise ServiceValidationError when scenario.trigger
    raises SHCException or SHCConnectionError (lines 126-127)."""

    def _get_scenario_handler(self):
        """Register scenario service via async_setup and return the handler."""
        from custom_components.bosch_shc import async_setup

        hass = MagicMock()
        hass.data = {}
        hass.loop = MagicMock()
        hass.config_entries = MagicMock()
        hass.bus = MagicMock()
        hass.bus.async_listen_once = MagicMock(return_value=MagicMock(return_value=None))
        hass.services = MagicMock()
        hass.services.async_register = MagicMock()
        hass.services.has_service = MagicMock(return_value=False)
        hass.async_create_task = MagicMock()

        _run(async_setup(hass, {}))

        register_calls = hass.services.async_register.call_args_list
        for c in register_calls:
            if c.args[1] == SERVICE_TRIGGER_SCENARIO:
                return c.args[2], hass

        raise AssertionError("trigger_scenario service was not registered")

    def _make_runtime_with_failing_scenario(self, exc):
        """Build a fake runtime_data whose scenario.trigger raises exc."""
        class _FailingScenario:
            name = "failing_scene"

            def trigger(self_):
                raise exc

        return SimpleNamespace(
            title="SHC Test",
            session=SimpleNamespace(scenarios=[_FailingScenario()]),
        )

    def _make_entry_with_runtime(self, runtime):
        return SimpleNamespace(
            entry_id="eid-err",
            title="SHC Test",
            runtime_data=runtime,
        )

    def _patch_entries(self, hass, entry):
        hass.config_entries.async_entries = MagicMock(return_value=[entry])

        async def _executor_job(fn, *args):
            return fn(*args)

        hass.async_add_executor_job = _executor_job

    def test_shcexception_raises_service_validation_error(self):
        """SHCException from scenario.trigger → ServiceValidationError (lines 126-127)."""
        handler, hass = self._get_scenario_handler()
        runtime = self._make_runtime_with_failing_scenario(SHCException("io error"))
        entry = self._make_entry_with_runtime(runtime)
        self._patch_entries(hass, entry)

        fake_call = SimpleNamespace(
            data={ATTR_NAME: "failing_scene", ATTR_TITLE: ""},
        )
        with pytest.raises(ServiceValidationError):
            _run(handler(fake_call))

    def test_shcconnectionerror_raises_service_validation_error(self):
        """SHCConnectionError from scenario.trigger → ServiceValidationError (lines 126-127)."""
        handler, hass = self._get_scenario_handler()
        runtime = self._make_runtime_with_failing_scenario(SHCConnectionError("timeout"))
        entry = self._make_entry_with_runtime(runtime)
        self._patch_entries(hass, entry)

        fake_call = SimpleNamespace(
            data={ATTR_NAME: "failing_scene", ATTR_TITLE: ""},
        )
        with pytest.raises(ServiceValidationError):
            _run(handler(fake_call))

    def test_error_message_contains_scenario_name(self):
        """ServiceValidationError message must include the scenario name."""
        handler, hass = self._get_scenario_handler()
        runtime = self._make_runtime_with_failing_scenario(SHCException("x"))
        entry = self._make_entry_with_runtime(runtime)
        self._patch_entries(hass, entry)

        fake_call = SimpleNamespace(
            data={ATTR_NAME: "failing_scene", ATTR_TITLE: ""},
        )
        with pytest.raises(ServiceValidationError, match="failing_scene"):
            _run(handler(fake_call))

    def test_successful_trigger_does_not_raise(self):
        """When scenario.trigger succeeds, no exception is raised."""
        from custom_components.bosch_shc import async_setup

        hass = MagicMock()
        hass.data = {}
        hass.loop = MagicMock()
        hass.config_entries = MagicMock()
        hass.bus = MagicMock()
        hass.bus.async_listen_once = MagicMock(return_value=MagicMock(return_value=None))
        hass.services = MagicMock()
        hass.services.async_register = MagicMock()
        hass.services.has_service = MagicMock(return_value=False)
        hass.async_create_task = MagicMock()

        _run(async_setup(hass, {}))

        register_calls = hass.services.async_register.call_args_list
        handler = None
        for c in register_calls:
            if c.args[1] == SERVICE_TRIGGER_SCENARIO:
                handler = c.args[2]
                break
        assert handler is not None

        triggered = []

        class _OkScenario:
            name = "ok_scene"

            def trigger(self_):
                triggered.append(True)

        runtime = SimpleNamespace(
            title="SHC Test",
            session=SimpleNamespace(scenarios=[_OkScenario()]),
        )
        entry = SimpleNamespace(
            entry_id="eid-ok",
            title="SHC Test",
            runtime_data=runtime,
        )
        hass.config_entries.async_entries = MagicMock(return_value=[entry])

        async def _executor_job(fn, *args):
            return fn(*args)

        hass.async_add_executor_job = _executor_job

        fake_call = SimpleNamespace(
            data={ATTR_NAME: "ok_scene", ATTR_TITLE: ""},
        )
        _run(handler(fake_call))
        assert triggered == [True]
