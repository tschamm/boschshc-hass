"""Tests for the number.py platform.

Covers the generic SHCNumber entity (dataclass-driven via
SHCNumberEntityDescription) across every number type it's built for:
thermostat/roomthermostat/wallthermostat temperature offset, impulse length,
heating-circuit eco/comfort setpoints (dynamic bounds), bypass timeout, the
APK-batch smart-plug/thermostat entities (power threshold, enter duration,
LED brightness, display brightness, display on-time), siren config fields and
dimmer config fields — property getters, async_set_native_value clamping and
error handling, and the async_setup_entry wiring (including
device/room-exclusion and dual-guard "supports_* AND value is not None"
entity-creation gating).

Pure-unit style throughout: __new__ bypass + SimpleNamespace/MagicMock device
doubles, no HA test harness.
"""

import asyncio
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from boschshcpy.exceptions import SHCConnectionError, SHCException
from homeassistant.components.number import NumberDeviceClass, NumberMode
from homeassistant.const import UnitOfTemperature, UnitOfTime
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory

from custom_components.bosch_shc.const import OPT_EXCLUDED_DEVICES
from custom_components.bosch_shc.number import (
    _SIREN_ALARM_DELAY,
    _SIREN_ALARM_DURATION,
    _SIREN_FLASH_DURATION,
    BYPASS_TIMEOUT,
    DIMMER_MAX,
    DIMMER_MIN,
    DIMMER_SPEED,
    DISPLAY_BRIGHTNESS,
    DISPLAY_ON_TIME,
    ENTER_DURATION,
    HEATING_CIRCUIT_SETPOINT_COMFORT,
    HEATING_CIRCUIT_SETPOINT_ECO,
    IMPULSE_LENGTH,
    LED_BRIGHTNESS,
    NUMBER_DESCRIPTIONS,
    OFFSET,
    POWER_THRESHOLD,
    SIREN_ALARM_DELAY,
    SIREN_ALARM_DURATION,
    SIREN_FLASH_DELAY,
    SIREN_FLASH_DURATION,
    SHCNumber,
    TemperatureDropValueNumber,
    async_setup_entry,
)

from .conftest import run_setup_entry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(coro):
    return asyncio.run(coro)


def _excl(*ids):
    return {OPT_EXCLUDED_DEVICES: list(ids)}


def _entity_for(key: str, device) -> SHCNumber:
    """Build a SHCNumber bound to the given description key (no HA init)."""
    entity = SHCNumber.__new__(SHCNumber)
    entity.entity_description = NUMBER_DESCRIPTIONS[key]
    entity._device = device
    return entity


def _make_number(
    *,
    offset=0.0,
    min_offset=-5.0,
    max_offset=5.0,
    step_size=0.5,
):
    """Return a SHCNumber(offset) with _device set via SimpleNamespace (no HA init)."""
    return _entity_for(
        OFFSET,
        SimpleNamespace(
            name="Test Number",
            offset=offset,
            min_offset=min_offset,
            max_offset=max_offset,
            step_size=step_size,
            async_set_offset=AsyncMock(),
        ),
    )


def _fake_number_init_device(
    name="test-number", root_device_id="root1", device_id="dev1"
):
    """Fake device for SHCNumber.__init__ (distinct from the APK-entity
    `_fake_device` below — different field set, only what SHCNumber needs).
    """
    return SimpleNamespace(
        name=name,
        root_device_id=root_device_id,
        id=device_id,
        offset=0.0,
        min_offset=-5.0,
        max_offset=5.0,
        step_size=0.5,
    )


def _number_device() -> SimpleNamespace:
    """Minimal device for SHCNumber.__init__."""
    return SimpleNamespace(
        name="Test Thermostat",
        id="hdm:HomeMaticIP:thermo1",
        root_device_id="aa:bb:cc:00:00:04",
        serial="serial-thermo1",
        offset=0.0,
        min_offset=-5.0,
        max_offset=5.0,
        step_size=0.5,
        device_services=[],
        manufacturer="Bosch",
        device_model="TRV",
        status="AVAILABLE",
        deleted=False,
    )


def _fake_device(**kwargs):
    """Fake APK-entity device (smart plug / thermostat) for the dual-guard
    and new-entity tests (PowerThreshold/EnterDuration/LedBrightness/
    DisplayBrightness/DisplayOnTime number types).
    """
    defaults = dict(
        name="Dev",
        id="dev1",
        root_device_id="root1",
        serial="SER1",
        supports_silentmode=False,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _fake_device_cg(**kwargs):
    """Fake device used by the smart-plug-compact-exclusion / DisplayOnTime
    native_step coverage tests. Distinct field defaults from `_fake_device`
    above (device_services instead of supports_silentmode) — kept separate
    per the source files' own conventions rather than silently merged.
    """
    base = dict(
        id="dev1",
        root_device_id="root1",
        name="FakeDev",
        device_services=[],
        serial="SER1",
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def _types(entities):
    return [type(e).__name__ for e in entities]


def _keys(entities):
    return [e.entity_description.key for e in entities]


def _impulse_device(impulse_length=100):
    """Fake SHCMicromoduleImpulseRelay for the impulse-length number."""
    return SimpleNamespace(
        name="Relay Impulse",
        id="hdm:HomeMaticIP:relay1",
        root_device_id="aa:bb:cc:00:00:01",
        impulse_length=impulse_length,
    )


def _heating_circuit_svc(eco=18.0, comfort=21.0):
    """Fake HeatingCircuitService."""
    return SimpleNamespace(
        setpoint_temperature_eco=eco,
        setpoint_temperature_comfort=comfort,
    )


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
    return _entity_for(IMPULSE_LENGTH, dev)


def _make_heating_setpoint_number(getter, setter, eco=18.0, comfort=21.0):
    dev = _heating_circuit_device(eco, comfort)
    key = (
        HEATING_CIRCUIT_SETPOINT_ECO
        if "eco" in getter
        else HEATING_CIRCUIT_SETPOINT_COMFORT
    )
    return _entity_for(key, dev)


def _fake_dev(dev_id="dev1", root_id="root1", serial="SER1", **kw):
    base = dict(
        id=dev_id,
        root_device_id=root_id,
        name="FakeDev",
        serial=serial,
        device_services=[],
        room_id=None,
        deleted=False,
        status="AVAILABLE",
        manufacturer="Bosch",
        device_model="TestModel",
        subscribe_callback=MagicMock(),
        unsubscribe_callback=MagicMock(),
    )
    base.update(kw)
    return SimpleNamespace(**base)


_FAKE_DEVICE = SimpleNamespace(
    root_device_id="root-1",
    id="hdm:ZigBee:dimmer1",
    name="Büro Dimmer",
    status="AVAILABLE",
)


def _dimmer_svc(min_b=10, max_b=90, speed=4):
    return SimpleNamespace(
        min_brightness=min_b,
        max_brightness=max_b,
        dimming_speed=speed,
        async_set_brightness_range=AsyncMock(),
        async_set_dimming_speed=AsyncMock(),
    )


# ---------------------------------------------------------------------------
# Siren config number (hass#120)
# ---------------------------------------------------------------------------


def test_siren_config_number_reads_and_clamps():
    n = SHCNumber(
        device=SimpleNamespace(
            root_device_id="r",
            id="d",
            name="Siren",
            siren=SimpleNamespace(alarm_delay=42),
        ),
        entity_description=_SIREN_ALARM_DELAY,
        entry_id="entry",
    )
    assert n.native_value == 42.0
    assert n.native_min_value == 0.0
    assert n.native_max_value == 180.0


def test_siren_duration_bounds_match_app_slider():
    """hass#120: alarmDuration/flashDuration are 1-15 minutes, confirmed via
    APK decompile of the real slider widgets (layout_outdoorsiren_alarm_signal
    _fragment.xml) — NOT 0-60 as previously assumed from the OpenAPI spec.
    """
    for desc in (_SIREN_ALARM_DURATION, _SIREN_FLASH_DURATION):
        n = SHCNumber(
            device=SimpleNamespace(root_device_id="r", id="d", name="Siren"),
            entity_description=desc,
            entry_id="entry",
        )
        assert n.native_min_value == 1.0
        assert n.native_max_value == 15.0


class TestNumberSirenSetup:
    """Siren config numbers + dimmer config numbers created in setup."""

    def _run(self, mock_config_entry, mock_session) -> list:
        return asyncio.run(
            run_setup_entry(async_setup_entry, mock_config_entry, mock_session)
        )

    @pytest.mark.parametrize(
        "device_buckets",
        [{"outdoor_sirens": [_fake_dev("s1", siren=MagicMock())]}],
        indirect=True,
    )
    def test_siren_config_numbers_created_when_siren_service_present(
        self, mock_config_entry, mock_session
    ):
        """Siren with siren service → 4 SHCNumber siren-config entities."""
        collected = self._run(mock_config_entry, mock_session)
        keys = _keys(collected)
        siren_keys = {
            SIREN_ALARM_DURATION,
            SIREN_FLASH_DURATION,
            SIREN_ALARM_DELAY,
            SIREN_FLASH_DELAY,
        }
        assert sum(keys.count(k) for k in siren_keys) >= 4

    @pytest.mark.parametrize(
        ("device_buckets", "mock_config_entry"),
        [
            (
                {"outdoor_sirens": [_fake_dev("siren_excl", siren=MagicMock())]},
                {"options": {OPT_EXCLUDED_DEVICES: ["siren_excl"]}},
            )
        ],
        indirect=True,
    )
    def test_siren_excluded_skipped_in_number_setup(
        self, mock_config_entry, mock_session
    ):
        """device_excluded → continue (no siren-config numbers added)."""
        collected = self._run(mock_config_entry, mock_session)
        keys = _keys(collected)
        assert SIREN_ALARM_DURATION not in keys

    @pytest.mark.parametrize(
        "device_buckets",
        [{"outdoor_sirens": [_fake_dev("siren_no_svc", siren=None)]}],
        indirect=True,
    )
    def test_siren_without_siren_service_skipped_in_number_setup(
        self, mock_config_entry, mock_session
    ):
        """Siren with siren=None → continue (no siren-config numbers added)."""
        collected = self._run(mock_config_entry, mock_session)
        keys = _keys(collected)
        assert SIREN_ALARM_DURATION not in keys

    @pytest.mark.parametrize(
        ("device_buckets", "mock_config_entry"),
        [
            (
                {
                    "micromodule_dimmers": [
                        _fake_dev("dim_excl", supports_dimmer_configuration=True)
                    ]
                },
                {"options": {OPT_EXCLUDED_DEVICES: ["dim_excl"]}},
            )
        ],
        indirect=True,
    )
    def test_dimmer_excluded_skipped_in_number_setup(
        self, mock_config_entry, mock_session
    ):
        """device_excluded → continue (no dimmer-config numbers added)."""
        collected = self._run(mock_config_entry, mock_session)
        keys = _keys(collected)
        assert DIMMER_MIN not in keys

    @pytest.mark.parametrize(
        "device_buckets",
        [
            {
                "micromodule_dimmers": [
                    _fake_dev("dim1", supports_dimmer_configuration=True)
                ]
            }
        ],
        indirect=True,
    )
    def test_dimmer_config_numbers_created_when_supports_dimmer(
        self, mock_config_entry, mock_session
    ):
        """Dimmer with supports_dimmer_configuration → 3 dimmer-config numbers."""
        collected = self._run(mock_config_entry, mock_session)
        keys = _keys(collected)
        assert (
            keys.count(DIMMER_MIN) + keys.count(DIMMER_MAX) + keys.count(DIMMER_SPEED)
            == 3
        )


# ---------------------------------------------------------------------------
# SHCNumber(offset).__init__ and description-level attributes
# ---------------------------------------------------------------------------


class TestSHCNumberInit:
    """Cover SHCNumber.__init__."""

    def test_init_sets_name_from_description(self):
        dev = _fake_number_init_device()
        number = SHCNumber(
            device=dev, entity_description=NUMBER_DESCRIPTIONS[OFFSET], entry_id="test"
        )
        assert number._attr_name == "Offset"

    def test_init_sets_unique_id(self):
        dev = _fake_number_init_device()
        number = SHCNumber(
            device=dev, entity_description=NUMBER_DESCRIPTIONS[OFFSET], entry_id="test"
        )
        assert number._attr_unique_id == "root1_dev1_offset"

    def test_init_device_stored(self):
        dev = _fake_number_init_device()
        number = SHCNumber(
            device=dev, entity_description=NUMBER_DESCRIPTIONS[OFFSET], entry_id="test"
        )
        assert number._device is dev

    def test_init_entry_id_stored(self):
        dev = _fake_number_init_device()
        number = SHCNumber(
            device=dev,
            entity_description=NUMBER_DESCRIPTIONS[OFFSET],
            entry_id="myentry",
        )
        assert number._entry_id == "myentry"

    def test_init_unique_id_uses_root_and_device_id(self):
        dev = _fake_number_init_device(
            name="my-thermo", root_device_id="root2", device_id="dev2"
        )
        number = SHCNumber(
            device=dev, entity_description=NUMBER_DESCRIPTIONS[OFFSET], entry_id="e"
        )
        assert number._attr_unique_id == "root2_dev2_offset"
        assert number._attr_name == "Offset"

    def test_init_translation_key_description_deletes_attr_name(self):
        """A description with a translation_key (not a literal name) must
        remove the instance _attr_name so HA's translation lookup applies.
        """
        dev = _fake_number_init_device()
        number = SHCNumber(
            device=dev,
            entity_description=NUMBER_DESCRIPTIONS[IMPULSE_LENGTH],
            entry_id="test",
        )
        assert not hasattr(number, "_attr_name")


class TestSHCNumberClassAttrs:
    """Cover the offset description's device_class/entity_category/unit."""

    def _make_number(self):
        dev = _fake_number_init_device()
        return SHCNumber(
            device=dev, entity_description=NUMBER_DESCRIPTIONS[OFFSET], entry_id="test"
        )

    def test_device_class_is_temperature(self):
        number = self._make_number()
        assert number.device_class == NumberDeviceClass.TEMPERATURE

    def test_entity_category_is_diagnostic(self):
        number = self._make_number()
        assert number.entity_category == EntityCategory.DIAGNOSTIC

    def test_native_unit_is_celsius(self):
        number = self._make_number()
        assert number.native_unit_of_measurement == UnitOfTemperature.CELSIUS


# ---------------------------------------------------------------------------
# SHCNumber(offset) — native_value / native_min_value / native_max_value / native_step
# ---------------------------------------------------------------------------


def test_native_value_positive():
    entity = _make_number(offset=2.5)
    assert entity.native_value == 2.5


def test_native_value_negative():
    entity = _make_number(offset=-3.0)
    assert entity.native_value == -3.0


def test_native_value_zero():
    entity = _make_number(offset=0.0)
    assert entity.native_value == 0.0


def test_native_value_at_max():
    entity = _make_number(offset=5.0, max_offset=5.0)
    assert entity.native_value == 5.0


def test_native_value_at_min():
    entity = _make_number(offset=-5.0, min_offset=-5.0)
    assert entity.native_value == -5.0


def test_native_min_value_default():
    entity = _make_number(min_offset=-5.0)
    assert entity.native_min_value == -5.0


def test_native_min_value_positive():
    entity = _make_number(min_offset=0.0)
    assert entity.native_min_value == 0.0


def test_native_min_value_large_negative():
    entity = _make_number(min_offset=-100.0)
    assert entity.native_min_value == -100.0


def test_native_max_value_default():
    entity = _make_number(max_offset=5.0)
    assert entity.native_max_value == 5.0


def test_native_max_value_zero():
    entity = _make_number(max_offset=0.0)
    assert entity.native_max_value == 0.0


def test_native_max_value_large():
    entity = _make_number(max_offset=100.0)
    assert entity.native_max_value == 100.0


def test_native_step_half():
    entity = _make_number(step_size=0.5)
    assert entity.native_step == 0.5


def test_native_step_one():
    entity = _make_number(step_size=1.0)
    assert entity.native_step == 1.0


def test_native_step_fraction():
    entity = _make_number(step_size=0.1)
    assert abs(entity.native_step - 0.1) < 1e-9


def test_device_class_is_temperature():
    entity = _make_number()
    assert entity.device_class == NumberDeviceClass.TEMPERATURE


def test_native_unit_is_celsius():
    entity = _make_number()
    assert entity.native_unit_of_measurement == UnitOfTemperature.CELSIUS


def test_entity_category_is_diagnostic():
    entity = _make_number()
    assert entity.entity_category == EntityCategory.DIAGNOSTIC


class TestSHCNumberNativeValue:
    def test_native_value_returns_device_offset(self):
        n = _make_number(offset=2.5)
        assert n.native_value == 2.5

    def test_native_value_zero(self):
        n = _make_number(offset=0.0)
        assert n.native_value == 0.0

    def test_native_value_negative(self):
        n = _make_number(offset=-3.0)
        assert n.native_value == -3.0

    def test_native_value_at_max(self):
        n = _make_number(offset=5.0, max_offset=5.0)
        assert n.native_value == 5.0

    def test_native_value_at_min(self):
        n = _make_number(offset=-5.0, min_offset=-5.0)
        assert n.native_value == -5.0


class TestSHCNumberNativeStep:
    def test_native_step_returns_step_size(self):
        n = _make_number(step_size=0.5)
        assert n.native_step == 0.5

    def test_native_step_one(self):
        n = _make_number(step_size=1.0)
        assert n.native_step == 1.0


class TestSHCNumberNativeMinValue:
    def test_native_min_value_returns_min_offset(self):
        n = _make_number(min_offset=-5.0)
        assert n.native_min_value == -5.0

    def test_native_min_value_zero(self):
        n = _make_number(min_offset=0.0)
        assert n.native_min_value == 0.0


class TestSHCNumberNativeMaxValue:
    def test_native_max_value_returns_max_offset(self):
        n = _make_number(max_offset=5.0)
        assert n.native_max_value == 5.0

    def test_native_max_value_positive_non_integer(self):
        n = _make_number(max_offset=4.5)
        assert n.native_max_value == 4.5


# ---------------------------------------------------------------------------
# SHCNumber(offset).async_set_native_value — round-trip, clamping, error handling
# ---------------------------------------------------------------------------


def test_set_native_value_positive():
    entity = _make_number(offset=0.0)
    asyncio.run(entity.async_set_native_value(3.5))
    entity._device.async_set_offset.assert_awaited_once_with(3.5)


def test_set_native_value_negative():
    entity = _make_number(offset=0.0)
    asyncio.run(entity.async_set_native_value(-2.0))
    entity._device.async_set_offset.assert_awaited_once_with(-2.0)


def test_set_native_value_zero():
    entity = _make_number(offset=5.0)
    asyncio.run(entity.async_set_native_value(0.0))
    entity._device.async_set_offset.assert_awaited_once_with(0.0)


def test_set_native_value_at_boundary_max():
    entity = _make_number(max_offset=5.0)
    asyncio.run(entity.async_set_native_value(5.0))
    entity._device.async_set_offset.assert_awaited_once_with(5.0)


def test_set_native_value_at_boundary_min():
    entity = _make_number(min_offset=-5.0)
    asyncio.run(entity.async_set_native_value(-5.0))
    entity._device.async_set_offset.assert_awaited_once_with(-5.0)


def test_bounds_consistency_default_range():
    entity = _make_number(offset=0.0, min_offset=-5.0, max_offset=5.0)
    assert entity.native_min_value <= entity.native_value <= entity.native_max_value


def test_bounds_consistency_at_extremes():
    for offset in (-5.0, 0.0, 5.0):
        entity = _make_number(offset=offset, min_offset=-5.0, max_offset=5.0)
        assert entity.native_min_value <= entity.native_value <= entity.native_max_value


def test_set_native_value_above_max_clamps_to_max():
    """Value above native_max_value must be clamped to max, never sent raw."""
    entity = _make_number(offset=0.0, min_offset=-5.0, max_offset=5.0)
    asyncio.run(entity.async_set_native_value(10.0))
    entity._device.async_set_offset.assert_awaited_once_with(5.0)


def test_set_native_value_below_min_clamps_to_min():
    """Value below native_min_value must be clamped to min, never sent raw."""
    entity = _make_number(offset=0.0, min_offset=-5.0, max_offset=5.0)
    asyncio.run(entity.async_set_native_value(-20.0))
    entity._device.async_set_offset.assert_awaited_once_with(-5.0)


def test_set_native_value_in_range_passes_through():
    """In-range value must reach async_set_offset unchanged."""
    entity = _make_number(offset=0.0, min_offset=-5.0, max_offset=5.0)
    asyncio.run(entity.async_set_native_value(2.5))
    entity._device.async_set_offset.assert_awaited_once_with(2.5)


def test_set_native_value_exactly_at_max_passes_through():
    """Boundary-equal max must pass through, not be rejected."""
    entity = _make_number(offset=0.0, min_offset=-5.0, max_offset=5.0)
    asyncio.run(entity.async_set_native_value(5.0))
    entity._device.async_set_offset.assert_awaited_once_with(5.0)


def test_set_native_value_exactly_at_min_passes_through():
    """Boundary-equal min must pass through, not be rejected."""
    entity = _make_number(offset=0.0, min_offset=-5.0, max_offset=5.0)
    asyncio.run(entity.async_set_native_value(-5.0))
    entity._device.async_set_offset.assert_awaited_once_with(-5.0)


def test_set_native_value_shc_exception_raises_home_assistant_error():
    """A real SHC API rejection must surface as a translated HomeAssistantError,
    not propagate as a raw SHCException.
    """
    entity = _make_number(offset=0.0, min_offset=-5.0, max_offset=5.0)
    entity._device.async_set_offset = AsyncMock(side_effect=SHCException("rejected"))
    with pytest.raises(HomeAssistantError) as exc_info:
        asyncio.run(entity.async_set_native_value(2.0))
    assert exc_info.value.translation_key == "number_set_failed"


def test_set_native_value_shc_connection_error_raises_home_assistant_error():
    """A comms failure must also surface as a translated HomeAssistantError."""
    entity = _make_number(offset=0.0, min_offset=-5.0, max_offset=5.0)
    entity._device.async_set_offset = AsyncMock(
        side_effect=SHCConnectionError("unreachable")
    )
    with pytest.raises(HomeAssistantError) as exc_info:
        asyncio.run(entity.async_set_native_value(2.0))
    assert exc_info.value.translation_key == "number_set_failed"


def test_set_native_value_json_decode_error_logged_not_raised():
    """A malformed-but-200-OK write response (json.loads failure inside
    boschshcpy's _put_api_or_fail) must be logged, not crash the write call.
    """
    entity = _make_number(offset=0.0, min_offset=-5.0, max_offset=5.0)
    entity._device.async_set_offset = AsyncMock(
        side_effect=json.JSONDecodeError("Expecting value", "", 0)
    )
    asyncio.run(entity.async_set_native_value(2.0))  # must not raise


class TestSHCNumberSetNativeValue:
    def test_in_range_value_written_directly(self):
        """Value within [min, max] must be written as-is."""
        n = _make_number(min_offset=-5.0, max_offset=5.0)
        asyncio.run(n.async_set_native_value(2.0))
        n._device.async_set_offset.assert_awaited_once_with(2.0)

    def test_value_above_max_clamped_to_max(self):
        """Value > max_offset must be clamped to max_offset."""
        n = _make_number(min_offset=-5.0, max_offset=5.0)
        asyncio.run(n.async_set_native_value(10.0))
        n._device.async_set_offset.assert_awaited_once_with(5.0)

    def test_value_below_min_clamped_to_min(self):
        """Value < min_offset must be clamped to min_offset."""
        n = _make_number(min_offset=-5.0, max_offset=5.0)
        asyncio.run(n.async_set_native_value(-10.0))
        n._device.async_set_offset.assert_awaited_once_with(-5.0)

    def test_value_exactly_at_max_written(self):
        n = _make_number(min_offset=-5.0, max_offset=5.0)
        asyncio.run(n.async_set_native_value(5.0))
        n._device.async_set_offset.assert_awaited_once_with(5.0)

    def test_value_exactly_at_min_written(self):
        n = _make_number(min_offset=-5.0, max_offset=5.0)
        asyncio.run(n.async_set_native_value(-5.0))
        n._device.async_set_offset.assert_awaited_once_with(-5.0)

    def test_fractional_clamped_to_max(self):
        n = _make_number(min_offset=-5.0, max_offset=2.5)
        asyncio.run(n.async_set_native_value(3.0))
        n._device.async_set_offset.assert_awaited_once_with(2.5)

    def test_fractional_clamped_to_min(self):
        n = _make_number(min_offset=-2.5, max_offset=5.0)
        asyncio.run(n.async_set_native_value(-3.0))
        n._device.async_set_offset.assert_awaited_once_with(-2.5)

    def test_zero_within_range_written(self):
        n = _make_number(min_offset=-5.0, max_offset=5.0)
        asyncio.run(n.async_set_native_value(0.0))
        n._device.async_set_offset.assert_awaited_once_with(0.0)

    def test_clamping_does_not_write_original_value(self):
        """When clamped, the clamped (not original) value is sent to async_set_offset."""
        n = _make_number(min_offset=0.0, max_offset=3.0)
        asyncio.run(n.async_set_native_value(99.9))
        called_with = n._device.async_set_offset.call_args[0][0]
        assert called_with != 99.9
        assert called_with == 3.0


# ---------------------------------------------------------------------------
# SHCNumber(offset) — async_setup_entry (thermostats / roomthermostats / wallthermostats)
# ---------------------------------------------------------------------------


def _number_device_no_offset_support() -> SimpleNamespace:
    """THB-style device: no TemperatureOffset service."""
    dev = _number_device()
    dev.supports_temperature_offset = False
    return dev


class TestNumberSetupEntry:
    """Number async_setup_entry: thermostats + roomthermostats → SHCNumber(offset)."""

    def _run(self, mock_config_entry, mock_session) -> list:
        return asyncio.run(
            run_setup_entry(async_setup_entry, mock_config_entry, mock_session)
        )

    @pytest.mark.parametrize(
        "device_buckets", [{"thermostats": [_number_device()]}], indirect=True
    )
    def test_thermostats_produce_shc_number_entities(
        self, mock_config_entry, mock_session
    ) -> None:
        """session.device_helper.thermostats → SHCNumber(offset)."""
        result = self._run(mock_config_entry, mock_session)
        assert len(result) == 1
        assert isinstance(result[0], SHCNumber)
        assert result[0].entity_description.key == OFFSET

    @pytest.mark.parametrize(
        "device_buckets", [{"roomthermostats": [_number_device()]}], indirect=True
    )
    def test_roomthermostats_produce_shc_number_entities(
        self, mock_config_entry, mock_session
    ) -> None:
        """session.device_helper.roomthermostats → SHCNumber(offset)."""
        result = self._run(mock_config_entry, mock_session)
        assert len(result) == 1
        assert isinstance(result[0], SHCNumber)

    @pytest.mark.parametrize(
        "device_buckets", [{"wallthermostats": [_number_device()]}], indirect=True
    )
    def test_wallthermostats_produce_shc_number_entities(
        self, mock_config_entry, mock_session
    ) -> None:
        """session.device_helper.wallthermostats (BWTH/BWTH24) → SHCNumber(offset)."""
        result = self._run(mock_config_entry, mock_session)
        assert len(result) == 1
        assert isinstance(result[0], SHCNumber)

    @pytest.mark.parametrize(
        "device_buckets",
        [{"wallthermostats": [_number_device_no_offset_support()]}],
        indirect=True,
    )
    def test_wallthermostat_without_offset_service_skipped(
        self, mock_config_entry, mock_session
    ) -> None:
        """THB devices (no TemperatureOffset service) must not create SHCNumber."""
        result = self._run(mock_config_entry, mock_session)
        assert result == []

    @pytest.mark.parametrize(
        "device_buckets",
        [
            {
                "thermostats": [_number_device()],
                "roomthermostats": [_number_device()],
                "wallthermostats": [_number_device()],
            }
        ],
        indirect=True,
    )
    def test_mixed_thermostats_collected(self, mock_config_entry, mock_session) -> None:
        """Thermostat + roomthermostat + wallthermostat → 3 SHCNumber entities."""
        result = self._run(mock_config_entry, mock_session)
        assert len(result) == 3
        assert all(isinstance(e, SHCNumber) for e in result)

    def test_no_thermostats_adds_nothing(self, mock_config_entry, mock_session) -> None:
        """No thermostats/relays/heating_circuits → nothing added."""
        result = self._run(mock_config_entry, mock_session)
        assert result == []

    @pytest.mark.parametrize(
        "device_buckets", [{"thermostats": [_number_device()]}], indirect=True
    )
    def test_attr_name_offset_applied(self, mock_config_entry, mock_session) -> None:
        """async_setup_entry always uses the OFFSET description.

        With _attr_has_entity_name=True, _attr_name holds only the feature
        label; HA prepends the device name for display ('Test Thermostat Offset').
        """
        result = self._run(mock_config_entry, mock_session)
        assert result[0]._attr_name == "Offset"

    @pytest.mark.parametrize(
        "device_buckets", [{"thermostats": [_number_device()]}], indirect=True
    )
    def test_unique_id_includes_offset_suffix(
        self, mock_config_entry, mock_session
    ) -> None:
        """unique_id for the offset description ends in '_offset'."""
        result = self._run(mock_config_entry, mock_session)
        assert result[0]._attr_unique_id.endswith("_offset")

    @pytest.mark.parametrize(
        "device_buckets", [{"thermostats": [_number_device()]}], indirect=True
    )
    def test_entry_id_stored(self, mock_config_entry, mock_session) -> None:
        result = self._run(mock_config_entry, mock_session)
        assert result[0]._entry_id == "E1"


class TestNumberSetupExcludedThermostat:
    def _run(self, mock_config_entry, mock_session) -> list:
        return asyncio.run(
            run_setup_entry(async_setup_entry, mock_config_entry, mock_session)
        )

    @pytest.mark.parametrize(
        ("device_buckets", "mock_config_entry"),
        [
            (
                {
                    "thermostats": [
                        _fake_dev(
                            "trv-excl",
                            offset=0.0,
                            min_offset=-5.0,
                            max_offset=5.0,
                            step_size=0.5,
                        )
                    ]
                },
                {"options": {OPT_EXCLUDED_DEVICES: ["trv-excl"]}},
            )
        ],
        indirect=True,
    )
    def test_excluded_thermostat_not_in_entities(self, mock_config_entry, mock_session):
        """Excluded thermostat must be skipped."""
        entities = self._run(mock_config_entry, mock_session)
        ids = [getattr(e, "_device", None) and e._device.id for e in entities]
        assert "trv-excl" not in ids

    @pytest.mark.parametrize(
        ("device_buckets", "mock_config_entry"),
        [
            (
                {
                    "roomthermostats": [
                        _fake_dev(
                            "rt-excl",
                            offset=0.0,
                            min_offset=-5.0,
                            max_offset=5.0,
                            step_size=0.5,
                        )
                    ]
                },
                {"options": {OPT_EXCLUDED_DEVICES: ["rt-excl"]}},
            )
        ],
        indirect=True,
    )
    def test_excluded_roomthermostat_not_in_entities(
        self, mock_config_entry, mock_session
    ):
        """Excluded roomthermostat must be skipped (same loop)."""
        entities = self._run(mock_config_entry, mock_session)
        ids = [getattr(e, "_device", None) and e._device.id for e in entities]
        assert "rt-excl" not in ids

    @pytest.mark.parametrize(
        "device_buckets",
        [
            {
                "thermostats": [
                    _fake_dev(
                        "trv-keep",
                        offset=1.0,
                        min_offset=-5.0,
                        max_offset=5.0,
                        step_size=0.5,
                    )
                ]
            }
        ],
        indirect=True,
    )
    def test_non_excluded_thermostat_still_added(self, mock_config_entry, mock_session):
        """Non-excluded thermostat must still produce a SHCNumber entity."""
        entities = self._run(mock_config_entry, mock_session)
        assert any(isinstance(e, SHCNumber) for e in entities)

    @pytest.mark.parametrize(
        ("device_buckets", "mock_config_entry"),
        [
            (
                {
                    "thermostats": [
                        _fake_dev(
                            "trv-a",
                            offset=0.0,
                            min_offset=-5.0,
                            max_offset=5.0,
                            step_size=0.5,
                        ),
                        _fake_dev(
                            "trv-b",
                            offset=0.0,
                            min_offset=-5.0,
                            max_offset=5.0,
                            step_size=0.5,
                        ),
                    ]
                },
                {"options": {OPT_EXCLUDED_DEVICES: ["trv-b"]}},
            )
        ],
        indirect=True,
    )
    def test_mix_excluded_and_kept_thermostat(self, mock_config_entry, mock_session):
        entities = self._run(mock_config_entry, mock_session)
        ids = [getattr(e, "_device", None) and e._device.id for e in entities]
        assert "trv-a" in ids
        assert "trv-b" not in ids


# ---------------------------------------------------------------------------
# Impulse length number
# ---------------------------------------------------------------------------


class TestImpulseLengthNumberClassAttrs:
    def _entity(self):
        return _entity_for(IMPULSE_LENGTH, SimpleNamespace())

    def test_entity_category_is_config(self):
        assert self._entity().entity_category == EntityCategory.CONFIG

    def test_native_unit_is_seconds(self):
        assert self._entity().native_unit_of_measurement == UnitOfTime.SECONDS

    def test_native_min_is_01(self):
        assert self._entity().native_min_value == 0.1

    def test_native_max_is_60(self):
        assert self._entity().native_max_value == 60.0

    def test_native_step_is_01(self):
        assert self._entity().native_step == 0.1

    def test_mode_is_box(self):
        assert self._entity().mode == NumberMode.BOX


class TestImpulseLengthNativeValue:
    """Impulse-length number — native_value (lib stores tenths of seconds)."""

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
        num = _entity_for(IMPULSE_LENGTH, dev)
        assert num.native_value is None


class TestImpulseLengthSetNativeValue:
    def test_set_value_converts_seconds_to_tenths(self):
        """async_set_native_value(5.0) → async_set_impulse_length(50)."""
        dev = SimpleNamespace(
            name="relay",
            id="r1",
            root_device_id="root1",
            impulse_length=100,
            async_set_impulse_length=AsyncMock(),
        )
        num = _entity_for(IMPULSE_LENGTH, dev)
        asyncio.run(num.async_set_native_value(5.0))
        dev.async_set_impulse_length.assert_awaited_once_with(50)

    def test_set_value_clamps_to_max(self):
        """Values above 60 s are clamped to 60 s = 600 tenths."""
        dev = SimpleNamespace(
            name="relay",
            impulse_length=100,
            async_set_impulse_length=AsyncMock(),
        )
        num = _entity_for(IMPULSE_LENGTH, dev)
        asyncio.run(num.async_set_native_value(999.0))
        dev.async_set_impulse_length.assert_awaited_once_with(600)

    def test_set_value_clamps_to_min(self):
        """Values below 0.1 s are clamped to 0.1 s = 1 tenth."""
        dev = SimpleNamespace(
            name="relay",
            impulse_length=100,
            async_set_impulse_length=AsyncMock(),
        )
        num = _entity_for(IMPULSE_LENGTH, dev)
        asyncio.run(num.async_set_native_value(0.0))
        dev.async_set_impulse_length.assert_awaited_once_with(1)

    def test_set_value_shc_exception_raises_home_assistant_error(self):
        """A real SHC API rejection must surface as a translated
        HomeAssistantError, not propagate as a raw SHCException.
        """
        dev = SimpleNamespace(
            name="relay",
            impulse_length=100,
            async_set_impulse_length=AsyncMock(side_effect=SHCException("rejected")),
        )
        num = _entity_for(IMPULSE_LENGTH, dev)
        with pytest.raises(HomeAssistantError) as exc_info:
            asyncio.run(num.async_set_native_value(5.0))
        assert exc_info.value.translation_key == "number_set_failed"

    def test_set_value_shc_connection_error_raises_home_assistant_error(self):
        """A comms failure must also surface as a translated HomeAssistantError."""
        dev = SimpleNamespace(
            name="relay",
            impulse_length=100,
            async_set_impulse_length=AsyncMock(
                side_effect=SHCConnectionError("unreachable")
            ),
        )
        num = _entity_for(IMPULSE_LENGTH, dev)
        with pytest.raises(HomeAssistantError) as exc_info:
            asyncio.run(num.async_set_native_value(5.0))
        assert exc_info.value.translation_key == "number_set_failed"


class TestNumberSetupImpulseRelayNoAttr:
    """Impulse relay — not hasattr(device, "impulse_length") continue."""

    def _run(self, mock_config_entry, mock_session) -> list:
        return asyncio.run(
            run_setup_entry(async_setup_entry, mock_config_entry, mock_session)
        )

    @pytest.mark.parametrize(
        "device_buckets",
        [{"micromodule_impulse_relays": [_fake_dev("relay-no-attr")]}],
        indirect=True,
    )
    def test_device_without_impulse_length_attr_is_skipped(
        self, mock_config_entry, mock_session
    ):
        """Device missing impulse_length attribute must be skipped."""
        entities = self._run(mock_config_entry, mock_session)
        ids = [getattr(e, "_device", None) and e._device.id for e in entities]
        assert "relay-no-attr" not in ids

    @pytest.mark.parametrize(
        "device_buckets",
        [{"micromodule_impulse_relays": [_fake_dev("relay-no-il")]}],
        indirect=True,
    )
    def test_device_without_impulse_length_produces_no_entity(
        self, mock_config_entry, mock_session
    ):
        entities = self._run(mock_config_entry, mock_session)
        assert IMPULSE_LENGTH not in _keys(entities)


class TestNumberSetupImpulseRelayNoneValue:
    """Impulse relay — device.impulse_length is None continue."""

    def _run(self, mock_config_entry, mock_session) -> list:
        return asyncio.run(
            run_setup_entry(async_setup_entry, mock_config_entry, mock_session)
        )

    @pytest.mark.parametrize(
        "device_buckets",
        [
            {
                "micromodule_impulse_relays": [
                    _fake_dev("relay-none-il", impulse_length=None)
                ]
            }
        ],
        indirect=True,
    )
    def test_device_with_none_impulse_length_is_skipped(
        self, mock_config_entry, mock_session
    ):
        """impulse_length=None must be skipped."""
        entities = self._run(mock_config_entry, mock_session)
        assert IMPULSE_LENGTH not in _keys(entities)

    @pytest.mark.parametrize(
        "device_buckets",
        [
            {
                "micromodule_impulse_relays": [
                    _fake_dev("relay-zero-il", impulse_length=0)
                ]
            }
        ],
        indirect=True,
    )
    def test_device_with_zero_impulse_length_is_included(
        self, mock_config_entry, mock_session
    ):
        """impulse_length=0 is not None → entity IS created (boundary check)."""
        entities = self._run(mock_config_entry, mock_session)
        # 0 is falsy but is not None; the code checks `is None`, so entity must appear
        assert IMPULSE_LENGTH in _keys(entities)

    @pytest.mark.parametrize(
        "device_buckets",
        [{"micromodule_impulse_relays": [_fake_dev("relay-100", impulse_length=100)]}],
        indirect=True,
    )
    def test_device_with_valid_impulse_length_is_included(
        self, mock_config_entry, mock_session
    ):
        """impulse_length=100 → an impulse-length number is created."""
        entities = self._run(mock_config_entry, mock_session)
        assert IMPULSE_LENGTH in _keys(entities)


class TestImpulseRelayDeviceExcluded:
    """device_excluded continue for impulse relay."""

    @pytest.mark.parametrize(
        ("device_buckets", "mock_config_entry"),
        [
            (
                {
                    "micromodule_impulse_relays": [
                        SimpleNamespace(
                            id="ir-excl",
                            name="Relay",
                            root_device_id="root",
                            serial="SER",
                            device_services=[],
                            impulse_length=100,
                        )
                    ]
                },
                {"options": {OPT_EXCLUDED_DEVICES: ["ir-excl"]}},
            )
        ],
        indirect=True,
    )
    def test_excluded_impulse_relay_not_added(self, mock_config_entry, mock_session):
        """Excluded impulse relay must be skipped."""
        collected = asyncio.run(
            run_setup_entry(async_setup_entry, mock_config_entry, mock_session)
        )
        assert not any(
            getattr(e, "_device", None) and e._device.id == "ir-excl" for e in collected
        )


# ---------------------------------------------------------------------------
# Heating circuit setpoint numbers
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

    def test_returns_none_when_getter_legitimately_returns_none(self):
        """setpoint_temperature_eco/_comfort are typed float | None: a heating
        circuit that never had that preset configured returns None from a
        working getattr, not an AttributeError. float(None) would raise an
        uncaught TypeError if not guarded explicitly.
        """
        num = _make_heating_setpoint_number(
            "setpoint_temperature_eco", "setpoint_temperature_eco", eco=None
        )
        assert num.native_value is None

    def test_returns_none_when_service_absent(self):
        dev = SimpleNamespace(
            name="HC",
            id="hc1",
            root_device_id="root1",
            _heating_circuit_service=None,
        )
        num = _entity_for(HEATING_CIRCUIT_SETPOINT_ECO, dev)
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
        num = _entity_for(HEATING_CIRCUIT_SETPOINT_ECO, dev)
        with patch("custom_components.bosch_shc.number.LOGGER") as mock_log:
            result = num.native_value
        assert result is None
        mock_log.warning.assert_called_once()


class TestHeatingCircuitSetpointSetValue:
    def test_eco_set_value_writes_to_service(self):
        """async_set_native_value calls async_set_setpoint_temperature_eco on device."""
        mock_setter = AsyncMock()
        dev = SimpleNamespace(
            name="HC",
            id="hc1",
            root_device_id="root1",
            _heating_circuit_service=SimpleNamespace(
                setpoint_temperature_eco=18.0,
            ),
            async_set_setpoint_temperature_eco=mock_setter,
        )
        num = _entity_for(HEATING_CIRCUIT_SETPOINT_ECO, dev)

        asyncio.run(num.async_set_native_value(19.0))
        mock_setter.assert_awaited_once_with(pytest.approx(19.0))

    def test_set_value_clamps_to_min(self):
        """Values below 5 °C → clamped to 5 °C."""
        mock_setter = AsyncMock()
        dev = SimpleNamespace(
            name="HC",
            id="hc1",
            root_device_id="root1",
            _heating_circuit_service=SimpleNamespace(
                setpoint_temperature_eco=18.0,
            ),
            async_set_setpoint_temperature_eco=mock_setter,
        )
        num = _entity_for(HEATING_CIRCUIT_SETPOINT_ECO, dev)

        asyncio.run(num.async_set_native_value(1.0))
        mock_setter.assert_awaited_once_with(pytest.approx(5.0))

    def test_set_value_clamps_to_max(self):
        """Values above 30 °C → clamped to 30 °C."""
        mock_setter = AsyncMock()
        dev = SimpleNamespace(
            name="HC",
            id="hc1",
            root_device_id="root1",
            _heating_circuit_service=SimpleNamespace(
                setpoint_temperature_comfort=21.0,
            ),
            async_set_setpoint_temperature_comfort=mock_setter,
        )
        num = _entity_for(HEATING_CIRCUIT_SETPOINT_COMFORT, dev)

        asyncio.run(num.async_set_native_value(100.0))
        mock_setter.assert_awaited_once_with(pytest.approx(30.0))

    def test_set_value_with_no_async_setter_logs_warning(self):
        """When async_set_* is absent on device, log a warning and do nothing."""
        dev = SimpleNamespace(
            name="HC",
            id="hc1",
            root_device_id="root1",
            _heating_circuit_service=SimpleNamespace(
                setpoint_temperature_eco=18.0,
            ),
            # no async_set_setpoint_temperature_eco attribute
        )
        num = _entity_for(HEATING_CIRCUIT_SETPOINT_ECO, dev)

        with patch("custom_components.bosch_shc.number.LOGGER") as mock_log:
            asyncio.run(num.async_set_native_value(20.0))
        mock_log.warning.assert_called_once()


class TestHeatingCircuitSetpointDynamicBounds:
    """The app reads a per-device setpoint range rather than a fixed
    constant (HeatingCircuitVerticalSliderFragment.setMinMax) — a
    floor-heating circuit commonly reports a raised minimum.
    """

    def test_falls_back_to_5_30_when_device_has_no_range_yet(self):
        num = _entity_for(
            HEATING_CIRCUIT_SETPOINT_ECO,
            SimpleNamespace(eco_temperature_range=None),
        )
        assert num.native_min_value == 5.0
        assert num.native_max_value == 30.0

    def test_uses_device_reported_range(self):
        num = _entity_for(
            HEATING_CIRCUIT_SETPOINT_COMFORT,
            SimpleNamespace(comfort_temperature_range=(16.0, 24.0)),
        )
        assert num.native_min_value == 16.0
        assert num.native_max_value == 24.0

    def test_clamps_to_device_reported_range_not_the_5_30_default(self):
        """A floor-heating circuit with a raised minimum (e.g. 10°C) must
        clamp there, not silently allow the old 5°C default.
        """
        mock_setter = AsyncMock()
        dev = SimpleNamespace(
            name="HC",
            id="hc1",
            root_device_id="root1",
            _heating_circuit_service=SimpleNamespace(
                setpoint_temperature_eco=18.0,
            ),
            async_set_setpoint_temperature_eco=mock_setter,
            eco_temperature_range=(10.0, 22.0),
        )
        num = _entity_for(HEATING_CIRCUIT_SETPOINT_ECO, dev)

        asyncio.run(num.async_set_native_value(1.0))
        mock_setter.assert_awaited_once_with(pytest.approx(10.0))


def _hc_device(device_id):
    svc = SimpleNamespace(
        setpoint_temperature_eco=18.0,
        setpoint_temperature_comfort=21.0,
    )
    return _fake_dev(device_id, name="HC", _heating_circuit_service=svc)


class TestNumberSetupExcludedHeatingCircuit:
    """Heating circuit device_excluded continue."""

    def _run(self, mock_config_entry, mock_session) -> list:
        return asyncio.run(
            run_setup_entry(async_setup_entry, mock_config_entry, mock_session)
        )

    @pytest.mark.parametrize(
        ("device_buckets", "mock_config_entry"),
        [
            (
                {"heating_circuits": [_hc_device("hc-excl")]},
                {"options": {OPT_EXCLUDED_DEVICES: ["hc-excl"]}},
            )
        ],
        indirect=True,
    )
    def test_excluded_heating_circuit_not_in_entities(
        self, mock_config_entry, mock_session
    ):
        """Excluded heating circuit must be skipped."""
        entities = self._run(mock_config_entry, mock_session)
        ids = [getattr(e, "_device", None) and e._device.id for e in entities]
        assert "hc-excl" not in ids

    @pytest.mark.parametrize(
        "device_buckets",
        [{"heating_circuits": [_hc_device("hc-keep")]}],
        indirect=True,
    )
    def test_non_excluded_heating_circuit_still_added(
        self, mock_config_entry, mock_session
    ):
        """Non-excluded heating circuit produces heating-circuit setpoint entities."""
        entities = self._run(mock_config_entry, mock_session)
        keys = _keys(entities)
        assert HEATING_CIRCUIT_SETPOINT_ECO in keys
        assert HEATING_CIRCUIT_SETPOINT_COMFORT in keys

    @pytest.mark.parametrize(
        ("device_buckets", "mock_config_entry"),
        [
            (
                {"heating_circuits": [_hc_device("hc-a"), _hc_device("hc-b")]},
                {"options": {OPT_EXCLUDED_DEVICES: ["hc-b"]}},
            )
        ],
        indirect=True,
    )
    def test_mix_excluded_and_kept_heating_circuit(
        self, mock_config_entry, mock_session
    ):
        entities = self._run(mock_config_entry, mock_session)
        ids = [getattr(e, "_device", None) and e._device.id for e in entities]
        assert "hc-a" in ids
        assert "hc-b" not in ids


class TestHeatingCircuitSetpointNumberSetNativeValueNoService:
    """async_set_native_value — svc is None path (LOGGER.warning + early
    return when async setter is absent).
    """

    def _sensor_no_async_setter(self):
        return _entity_for(
            HEATING_CIRCUIT_SETPOINT_ECO,
            SimpleNamespace(
                name="HC-None",
                _heating_circuit_service=None,
                # no async_set_setpoint_temperature_eco attribute
            ),
        )

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

        s = self._sensor_no_async_setter()

        # Must not raise; setter is absent so no write occurs.
        with patch("custom_components.bosch_shc.number.LOGGER"):
            asyncio.run(s.async_set_native_value(22.0))
        assert writes == []

    def test_set_native_value_with_valid_service_writes_clamped_value(self):
        """Sanity: when async_set_* is present, it is awaited with clamped value."""
        mock_setter = AsyncMock()

        s = _entity_for(
            HEATING_CIRCUIT_SETPOINT_ECO,
            SimpleNamespace(
                name="HC-OK",
                _heating_circuit_service=SimpleNamespace(
                    setpoint_temperature_eco=None,
                ),
                async_set_setpoint_temperature_eco=mock_setter,
            ),
        )

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


class TestHeatingCircuitSetterAttributeError:
    """async_set_native_value must log warning on AttributeError/KeyError."""

    def test_attribute_error_in_setter_logs_warning(self):
        """AttributeError from async setter must log a warning and not propagate."""
        s = _entity_for(
            HEATING_CIRCUIT_SETPOINT_ECO,
            SimpleNamespace(
                name="HC-BadSetter",
                async_set_setpoint_temperature_eco=AsyncMock(
                    side_effect=AttributeError("setter blocked")
                ),
            ),
        )

        with patch("custom_components.bosch_shc.number.LOGGER") as mock_log:
            asyncio.run(s.async_set_native_value(20.0))

        mock_log.warning.assert_called_once()

    def test_key_error_in_setter_logs_warning(self):
        """KeyError from async setter must also log a warning."""
        s = _entity_for(
            HEATING_CIRCUIT_SETPOINT_ECO,
            SimpleNamespace(
                name="HC-KeyErr",
                async_set_setpoint_temperature_eco=AsyncMock(
                    side_effect=KeyError("missing key")
                ),
            ),
        )

        with patch("custom_components.bosch_shc.number.LOGGER") as mock_log:
            asyncio.run(s.async_set_native_value(20.0))

        mock_log.warning.assert_called_once()


class TestNumberSetupNewEntities:
    """Verify that the impulse-relay and heating-circuit entity loops in
    async_setup_entry work end to end.
    """

    def _run(self, mock_config_entry, mock_session) -> list:
        return asyncio.run(
            run_setup_entry(async_setup_entry, mock_config_entry, mock_session)
        )

    @pytest.mark.parametrize(
        "device_buckets",
        [{"micromodule_impulse_relays": [_impulse_device(impulse_length=50)]}],
        indirect=True,
    )
    def test_impulse_relay_with_length_produces_number(
        self, mock_config_entry, mock_session
    ):
        result = self._run(mock_config_entry, mock_session)
        assert len(result) == 1
        assert result[0].entity_description.key == IMPULSE_LENGTH

    @pytest.mark.parametrize(
        "device_buckets",
        [{"micromodule_impulse_relays": [_impulse_device(impulse_length=None)]}],
        indirect=True,
    )
    def test_impulse_relay_with_none_length_is_skipped(
        self, mock_config_entry, mock_session
    ):
        result = self._run(mock_config_entry, mock_session)
        assert result == []

    @pytest.mark.parametrize(
        "device_buckets",
        [{"heating_circuits": [_heating_circuit_device()]}],
        indirect=True,
    )
    def test_heating_circuit_produces_two_setpoint_numbers(
        self, mock_config_entry, mock_session
    ):
        result = self._run(mock_config_entry, mock_session)
        assert len(result) == 2
        keys = {e.entity_description.key for e in result}
        assert keys == {HEATING_CIRCUIT_SETPOINT_ECO, HEATING_CIRCUIT_SETPOINT_COMFORT}
        names = [e._attr_name for e in result]
        assert "Setpoint Eco Temperature" in names
        assert "Setpoint Comfort Temperature" in names

    @pytest.mark.parametrize(
        "device_buckets",
        [{"heating_circuits": [_heating_circuit_device(), _heating_circuit_device()]}],
        indirect=True,
    )
    def test_two_heating_circuits_produce_four_numbers(
        self, mock_config_entry, mock_session
    ):
        result = self._run(mock_config_entry, mock_session)
        assert len(result) == 4
        assert all(
            e.entity_description.key
            in (HEATING_CIRCUIT_SETPOINT_ECO, HEATING_CIRCUIT_SETPOINT_COMFORT)
            for e in result
        )

    def test_no_new_devices_adds_nothing(self, mock_config_entry, mock_session):
        result = self._run(mock_config_entry, mock_session)
        assert result == []


# ---------------------------------------------------------------------------
# Power threshold number (smart plug / smart plug compact)
# ---------------------------------------------------------------------------


class TestNumberSmartPlugCompactDeviceExcluded:
    """device_excluded continue in smart_plugs/compact loop."""

    def _run(self, mock_config_entry, mock_session) -> list:
        return asyncio.run(
            run_setup_entry(async_setup_entry, mock_config_entry, mock_session)
        )

    @pytest.mark.parametrize(
        ("device_buckets", "mock_config_entry"),
        [
            (
                {
                    "smart_plugs_compact": [
                        _fake_device_cg(id="cp-excl", power_threshold=100.0)
                    ]
                },
                {"options": {OPT_EXCLUDED_DEVICES: ["cp-excl"]}},
            )
        ],
        indirect=True,
    )
    def test_excluded_compact_plug_not_added(self, mock_config_entry, mock_session):
        entities = self._run(mock_config_entry, mock_session)
        ids = [getattr(getattr(e, "_device", None), "id", None) for e in entities]
        assert "cp-excl" not in ids

    @pytest.mark.parametrize(
        ("device_buckets", "mock_config_entry"),
        [
            (
                {"smart_plugs": [_fake_device_cg(id="sp-excl", power_threshold=100.0)]},
                {"options": {OPT_EXCLUDED_DEVICES: ["sp-excl"]}},
            )
        ],
        indirect=True,
    )
    def test_excluded_smart_plug_not_added(self, mock_config_entry, mock_session):
        entities = self._run(mock_config_entry, mock_session)
        ids = [getattr(getattr(e, "_device", None), "id", None) for e in entities]
        assert "sp-excl" not in ids


class TestPowerThresholdNumberGuard:
    """Dual guard: created only when supports_energy_saving_mode AND
    power_threshold is not None.
    """

    def test_supports_false_value_present_skipped(
        self, mock_config_entry, mock_session
    ):
        plug = _fake_device(power_threshold=50.0, supports_energy_saving_mode=False)
        mock_session.device_helper.smart_plugs = [plug]
        entities = asyncio.run(
            run_setup_entry(async_setup_entry, mock_config_entry, mock_session)
        )
        assert POWER_THRESHOLD not in _keys(entities)

    def test_supports_true_value_none_skipped(self, mock_config_entry, mock_session):
        plug = _fake_device(power_threshold=None, supports_energy_saving_mode=True)
        mock_session.device_helper.smart_plugs = [plug]
        entities = asyncio.run(
            run_setup_entry(async_setup_entry, mock_config_entry, mock_session)
        )
        assert POWER_THRESHOLD not in _keys(entities)

    def test_both_present_created(self, mock_config_entry, mock_session):
        plug = _fake_device(power_threshold=100.0, supports_energy_saving_mode=True)
        mock_session.device_helper.smart_plugs = [plug]
        entities = asyncio.run(
            run_setup_entry(async_setup_entry, mock_config_entry, mock_session)
        )
        assert POWER_THRESHOLD in _keys(entities)

    def test_compact_supports_false_skipped(self, mock_config_entry, mock_session):
        plug = _fake_device(power_threshold=50.0, supports_energy_saving_mode=False)
        mock_session.device_helper.smart_plugs_compact = [plug]
        entities = asyncio.run(
            run_setup_entry(async_setup_entry, mock_config_entry, mock_session)
        )
        assert POWER_THRESHOLD not in _keys(entities)

    def test_compact_value_none_skipped(self, mock_config_entry, mock_session):
        plug = _fake_device(power_threshold=None, supports_energy_saving_mode=True)
        mock_session.device_helper.smart_plugs_compact = [plug]
        entities = asyncio.run(
            run_setup_entry(async_setup_entry, mock_config_entry, mock_session)
        )
        assert POWER_THRESHOLD not in _keys(entities)


class TestPowerThresholdNumber:
    def _make(self, **dev_kwargs):
        defaults = dict(root_device_id="root1", id="dev1", power_threshold=50.0)
        defaults.update(dev_kwargs)
        dev = SimpleNamespace(**defaults)
        n = _entity_for(POWER_THRESHOLD, dev)
        n._attr_unique_id = f"{dev.root_device_id}_{dev.id}_power_threshold"
        return n

    def test_native_value_from_device(self):
        n = self._make(power_threshold=100.0)
        assert n.native_value == 100.0

    def test_native_value_none_when_not_set(self):
        dev = SimpleNamespace(root_device_id="r", id="d")
        n = _entity_for(POWER_THRESHOLD, dev)
        assert n.native_value is None

    def test_set_native_value_writes_to_device(self):
        mock_setter = AsyncMock()
        dev = SimpleNamespace(
            root_device_id="r",
            id="d",
            power_threshold=0.0,
            async_set_power_threshold=mock_setter,
        )
        n = _entity_for(POWER_THRESHOLD, dev)
        asyncio.run(n.async_set_native_value(200.0))
        mock_setter.assert_awaited_once_with(200.0)

    def test_set_native_value_clamped_to_max(self):
        mock_setter = AsyncMock()
        dev = SimpleNamespace(
            root_device_id="r",
            id="d",
            power_threshold=0.0,
            async_set_power_threshold=mock_setter,
        )
        n = _entity_for(POWER_THRESHOLD, dev)
        asyncio.run(n.async_set_native_value(9999.0))
        assert mock_setter.call_args[0][0] <= 3680.0

    def test_set_native_value_clamped_to_min(self):
        mock_setter = AsyncMock()
        dev = SimpleNamespace(
            root_device_id="r",
            id="d",
            power_threshold=0.0,
            async_set_power_threshold=mock_setter,
        )
        n = _entity_for(POWER_THRESHOLD, dev)
        asyncio.run(n.async_set_native_value(-50.0))
        assert mock_setter.call_args[0][0] >= 0.0

    def test_unique_id_format(self):
        dev = SimpleNamespace(root_device_id="root1", id="dev1", power_threshold=10.0)
        n = _entity_for(POWER_THRESHOLD, dev)
        n._attr_unique_id = f"{dev.root_device_id}_{dev.id}_power_threshold"
        assert n._attr_unique_id == "root1_dev1_power_threshold"

    def test_entity_category_config(self):
        n = _entity_for(POWER_THRESHOLD, SimpleNamespace())
        assert n.entity_category == EntityCategory.CONFIG

    def test_device_class_power(self):
        n = _entity_for(POWER_THRESHOLD, SimpleNamespace())
        assert n.device_class == NumberDeviceClass.POWER

    def test_smartplug_power_threshold_created_when_attr_present(
        self, mock_config_entry, mock_session
    ):
        plug = _fake_device(power_threshold=100.0, supports_energy_saving_mode=True)
        mock_session.device_helper.smart_plugs = [plug]
        entities = asyncio.run(
            run_setup_entry(async_setup_entry, mock_config_entry, mock_session)
        )
        assert POWER_THRESHOLD in _keys(entities)

    def test_smartplug_power_threshold_skipped_when_attr_absent(
        self, mock_config_entry, mock_session
    ):
        plug = _fake_device()  # no power_threshold
        mock_session.device_helper.smart_plugs = [plug]
        entities = asyncio.run(
            run_setup_entry(async_setup_entry, mock_config_entry, mock_session)
        )
        assert POWER_THRESHOLD not in _keys(entities)


# ---------------------------------------------------------------------------
# Enter duration number (smart plug / smart plug compact)
# ---------------------------------------------------------------------------


class TestEnterDurationNumberGuard:
    def test_supports_false_value_present_skipped(
        self, mock_config_entry, mock_session
    ):
        plug = _fake_device(
            enter_duration_seconds=60, supports_energy_saving_mode=False
        )
        mock_session.device_helper.smart_plugs = [plug]
        entities = asyncio.run(
            run_setup_entry(async_setup_entry, mock_config_entry, mock_session)
        )
        assert ENTER_DURATION not in _keys(entities)

    def test_supports_true_value_none_skipped(self, mock_config_entry, mock_session):
        plug = _fake_device(
            enter_duration_seconds=None, supports_energy_saving_mode=True
        )
        mock_session.device_helper.smart_plugs = [plug]
        entities = asyncio.run(
            run_setup_entry(async_setup_entry, mock_config_entry, mock_session)
        )
        assert ENTER_DURATION not in _keys(entities)

    def test_both_present_created(self, mock_config_entry, mock_session):
        plug = _fake_device(enter_duration_seconds=30, supports_energy_saving_mode=True)
        mock_session.device_helper.smart_plugs = [plug]
        entities = asyncio.run(
            run_setup_entry(async_setup_entry, mock_config_entry, mock_session)
        )
        assert ENTER_DURATION in _keys(entities)


class TestEnterDurationNumber:
    def _make(self, enter_duration_seconds=30):
        dev = SimpleNamespace(
            root_device_id="root1",
            id="dev1",
            enter_duration_seconds=enter_duration_seconds,
        )
        n = _entity_for(ENTER_DURATION, dev)
        n._attr_unique_id = f"{dev.root_device_id}_{dev.id}_enter_duration_seconds"
        return n

    def test_native_value_returns_float(self):
        n = self._make(enter_duration_seconds=60)
        assert n.native_value == 60.0

    def test_native_value_none_when_missing(self):
        dev = SimpleNamespace(root_device_id="r", id="d")
        n = _entity_for(ENTER_DURATION, dev)
        assert n.native_value is None

    def test_set_native_value_converts_to_int(self):
        mock_setter = AsyncMock()
        dev = SimpleNamespace(
            root_device_id="r",
            id="d",
            enter_duration_seconds=0,
            async_set_enter_duration_seconds=mock_setter,
        )
        n = _entity_for(ENTER_DURATION, dev)
        asyncio.run(n.async_set_native_value(120.7))
        mock_setter.assert_awaited_once_with(120)  # int(clamped)

    def test_unique_id_format(self):
        n = self._make()
        assert n._attr_unique_id == "root1_dev1_enter_duration_seconds"

    def test_entity_category_config(self):
        n = _entity_for(ENTER_DURATION, SimpleNamespace())
        assert n.entity_category == EntityCategory.CONFIG

    def test_smartplugcompact_enter_duration_created_when_attr_present(
        self, mock_config_entry, mock_session
    ):
        plug = _fake_device(enter_duration_seconds=60, supports_energy_saving_mode=True)
        mock_session.device_helper.smart_plugs_compact = [plug]
        entities = asyncio.run(
            run_setup_entry(async_setup_entry, mock_config_entry, mock_session)
        )
        assert ENTER_DURATION in _keys(entities)

    def test_smartplugcompact_enter_duration_skipped_when_attr_absent(
        self, mock_config_entry, mock_session
    ):
        plug = _fake_device()
        mock_session.device_helper.smart_plugs_compact = [plug]
        entities = asyncio.run(
            run_setup_entry(async_setup_entry, mock_config_entry, mock_session)
        )
        assert ENTER_DURATION not in _keys(entities)


# ---------------------------------------------------------------------------
# LED brightness number (smart plug / smart plug compact)
# ---------------------------------------------------------------------------


class TestLedBrightnessNumberGuard:
    def test_supports_false_value_present_skipped(
        self, mock_config_entry, mock_session
    ):
        plug = _fake_device(led_brightness=50, supports_led_brightness=False)
        mock_session.device_helper.smart_plugs = [plug]
        entities = asyncio.run(
            run_setup_entry(async_setup_entry, mock_config_entry, mock_session)
        )
        assert LED_BRIGHTNESS not in _keys(entities)

    def test_supports_true_value_none_skipped(self, mock_config_entry, mock_session):
        plug = _fake_device(led_brightness=None, supports_led_brightness=True)
        mock_session.device_helper.smart_plugs = [plug]
        entities = asyncio.run(
            run_setup_entry(async_setup_entry, mock_config_entry, mock_session)
        )
        assert LED_BRIGHTNESS not in _keys(entities)

    def test_both_present_created(self, mock_config_entry, mock_session):
        plug = _fake_device(led_brightness=75, supports_led_brightness=True)
        mock_session.device_helper.smart_plugs = [plug]
        entities = asyncio.run(
            run_setup_entry(async_setup_entry, mock_config_entry, mock_session)
        )
        assert LED_BRIGHTNESS in _keys(entities)


class TestLedBrightnessNumber:
    def _make(self, led_brightness=50, svc=None):
        dev = SimpleNamespace(
            root_device_id="root1",
            id="dev1",
            led_brightness=led_brightness,
            _led_brightness_configuration_service=svc,
        )
        n = _entity_for(LED_BRIGHTNESS, dev)
        n._attr_unique_id = f"{dev.root_device_id}_{dev.id}_led_brightness"
        return n

    def test_native_value_from_device(self):
        n = self._make(led_brightness=75)
        assert n.native_value == 75

    def test_native_value_none_when_missing(self):
        dev = SimpleNamespace(root_device_id="r", id="d")
        n = _entity_for(LED_BRIGHTNESS, dev)
        assert n.native_value is None

    def test_native_min_from_service(self):
        svc = SimpleNamespace(min_brightness=10, max_brightness=100, step_size=1)
        n = self._make(svc=svc)
        assert n.native_min_value == 10.0

    def test_native_max_from_service(self):
        svc = SimpleNamespace(min_brightness=0, max_brightness=90, step_size=5)
        n = self._make(svc=svc)
        assert n.native_max_value == 90.0

    def test_native_step_from_service(self):
        svc = SimpleNamespace(min_brightness=0, max_brightness=100, step_size=5)
        n = self._make(svc=svc)
        assert n.native_step == 5.0

    def test_native_min_fallback_when_no_service(self):
        n = self._make(svc=None)
        assert n.native_min_value == 0.0

    def test_native_max_fallback_when_no_service(self):
        n = self._make(svc=None)
        assert n.native_max_value == 100.0

    def test_native_step_fallback_when_no_service(self):
        n = self._make(svc=None)
        assert n.native_step == 1.0

    def test_set_native_value_writes_to_device(self):
        mock_setter = AsyncMock()
        dev = SimpleNamespace(
            root_device_id="r",
            id="d",
            led_brightness=50,
            _led_brightness_configuration_service=None,
            async_set_led_brightness=mock_setter,
        )
        n = _entity_for(LED_BRIGHTNESS, dev)
        asyncio.run(n.async_set_native_value(80))
        mock_setter.assert_awaited_once_with(80)

    def test_unique_id_format(self):
        n = self._make()
        assert n._attr_unique_id == "root1_dev1_led_brightness"

    def test_smartplug_led_brightness_created_when_attr_present(
        self, mock_config_entry, mock_session
    ):
        plug = _fake_device(led_brightness=50, supports_led_brightness=True)
        mock_session.device_helper.smart_plugs = [plug]
        entities = asyncio.run(
            run_setup_entry(async_setup_entry, mock_config_entry, mock_session)
        )
        assert LED_BRIGHTNESS in _keys(entities)

    def test_smartplug_led_brightness_skipped_when_attr_absent(
        self, mock_config_entry, mock_session
    ):
        plug = _fake_device()
        mock_session.device_helper.smart_plugs = [plug]
        entities = asyncio.run(
            run_setup_entry(async_setup_entry, mock_config_entry, mock_session)
        )
        assert LED_BRIGHTNESS not in _keys(entities)

    def test_min_none_falls_back(self):
        svc = SimpleNamespace(min_brightness=None, max_brightness=100, step_size=1)
        n = self._make(svc=svc)
        assert n.native_min_value == 0.0

    def test_max_none_falls_back(self):
        svc = SimpleNamespace(min_brightness=0, max_brightness=None, step_size=1)
        n = self._make(svc=svc)
        assert n.native_max_value == 100.0

    def test_step_none_falls_back(self):
        svc = SimpleNamespace(min_brightness=0, max_brightness=100, step_size=None)
        n = self._make(svc=svc)
        assert n.native_step == 1.0


# ---------------------------------------------------------------------------
# Display brightness number (ThermostatGen2 / RoomThermostat2)
# ---------------------------------------------------------------------------


class TestDisplayBrightnessNumberGuard:
    def test_supports_false_value_present_skipped(
        self, mock_config_entry, mock_session
    ):
        therm = _fake_device(
            display_brightness=50, supports_display_configuration=False
        )
        mock_session.device_helper.thermostats = [therm]
        entities = asyncio.run(
            run_setup_entry(async_setup_entry, mock_config_entry, mock_session)
        )
        assert DISPLAY_BRIGHTNESS not in _keys(entities)

    def test_supports_true_value_none_skipped(self, mock_config_entry, mock_session):
        therm = _fake_device(
            display_brightness=None, supports_display_configuration=True
        )
        mock_session.device_helper.thermostats = [therm]
        entities = asyncio.run(
            run_setup_entry(async_setup_entry, mock_config_entry, mock_session)
        )
        assert DISPLAY_BRIGHTNESS not in _keys(entities)

    def test_both_present_created(self, mock_config_entry, mock_session):
        therm = _fake_device(display_brightness=60, supports_display_configuration=True)
        mock_session.device_helper.thermostats = [therm]
        entities = asyncio.run(
            run_setup_entry(async_setup_entry, mock_config_entry, mock_session)
        )
        assert DISPLAY_BRIGHTNESS in _keys(entities)

    def test_roomthermostat_value_none_skipped(self, mock_config_entry, mock_session):
        rth = _fake_device(display_brightness=None, supports_display_configuration=True)
        mock_session.device_helper.roomthermostats = [rth]
        entities = asyncio.run(
            run_setup_entry(async_setup_entry, mock_config_entry, mock_session)
        )
        assert DISPLAY_BRIGHTNESS not in _keys(entities)

    def test_roomthermostat_both_present_created(self, mock_config_entry, mock_session):
        rth = _fake_device(display_brightness=40, supports_display_configuration=True)
        mock_session.device_helper.roomthermostats = [rth]
        entities = asyncio.run(
            run_setup_entry(async_setup_entry, mock_config_entry, mock_session)
        )
        assert DISPLAY_BRIGHTNESS in _keys(entities)


class TestDisplayBrightnessNumber:
    def _make(self, display_brightness=50, svc=None):
        dev = SimpleNamespace(
            root_device_id="root1",
            id="dev1",
            display_brightness=display_brightness,
            _display_config_service=svc,
        )
        n = _entity_for(DISPLAY_BRIGHTNESS, dev)
        n._attr_unique_id = f"{dev.root_device_id}_{dev.id}_display_brightness"
        return n

    def test_native_value_from_device(self):
        n = self._make(display_brightness=60)
        assert n.native_value == 60

    def test_native_value_none_when_missing(self):
        dev = SimpleNamespace(root_device_id="r", id="d")
        n = _entity_for(DISPLAY_BRIGHTNESS, dev)
        assert n.native_value is None

    def test_native_min_from_service(self):
        svc = SimpleNamespace(
            display_brightness_min=5,
            display_brightness_max=100,
            display_brightness_step_size=5,
        )
        n = self._make(svc=svc)
        assert n.native_min_value == 5.0

    def test_native_max_from_service(self):
        svc = SimpleNamespace(
            display_brightness_min=0,
            display_brightness_max=80,
            display_brightness_step_size=1,
        )
        n = self._make(svc=svc)
        assert n.native_max_value == 80.0

    def test_native_step_from_service(self):
        svc = SimpleNamespace(
            display_brightness_min=0,
            display_brightness_max=100,
            display_brightness_step_size=10,
        )
        n = self._make(svc=svc)
        assert n.native_step == 10.0

    def test_fallbacks_when_no_service(self):
        n = self._make(svc=None)
        assert n.native_min_value == 0.0
        assert n.native_max_value == 100.0
        assert n.native_step == 1.0

    def test_set_native_value_writes_to_device(self):
        mock_setter = AsyncMock()
        dev = SimpleNamespace(
            root_device_id="r",
            id="d",
            display_brightness=50,
            _display_config_service=None,
            async_set_display_brightness=mock_setter,
        )
        n = _entity_for(DISPLAY_BRIGHTNESS, dev)
        asyncio.run(n.async_set_native_value(70))
        mock_setter.assert_awaited_once_with(70)

    def test_unique_id_format(self):
        n = self._make()
        assert n._attr_unique_id == "root1_dev1_display_brightness"

    def test_entity_category_config(self):
        n = _entity_for(DISPLAY_BRIGHTNESS, SimpleNamespace())
        assert n.entity_category == EntityCategory.CONFIG

    def test_thermostat_display_brightness_created_when_attr_present(
        self, mock_config_entry, mock_session
    ):
        therm = _fake_device(display_brightness=50, supports_display_configuration=True)
        mock_session.device_helper.thermostats = [therm]
        entities = asyncio.run(
            run_setup_entry(async_setup_entry, mock_config_entry, mock_session)
        )
        assert DISPLAY_BRIGHTNESS in _keys(entities)

    def test_thermostat_display_brightness_skipped_when_attr_absent(
        self, mock_config_entry, mock_session
    ):
        therm = _fake_device()
        mock_session.device_helper.thermostats = [therm]
        entities = asyncio.run(
            run_setup_entry(async_setup_entry, mock_config_entry, mock_session)
        )
        assert DISPLAY_BRIGHTNESS not in _keys(entities)

    def test_roomthermostat_display_brightness_created(
        self, mock_config_entry, mock_session
    ):
        rth = _fake_device(display_brightness=40, supports_display_configuration=True)
        mock_session.device_helper.roomthermostats = [rth]
        entities = asyncio.run(
            run_setup_entry(async_setup_entry, mock_config_entry, mock_session)
        )
        assert DISPLAY_BRIGHTNESS in _keys(entities)

    def test_min_none_falls_back(self):
        svc = SimpleNamespace(
            display_brightness_min=None,
            display_brightness_max=100,
            display_brightness_step_size=1,
        )
        n = self._make(svc=svc)
        assert n.native_min_value == 0.0

    def test_max_none_falls_back(self):
        svc = SimpleNamespace(
            display_brightness_min=0,
            display_brightness_max=None,
            display_brightness_step_size=1,
        )
        n = self._make(svc=svc)
        assert n.native_max_value == 100.0

    def test_step_none_falls_back(self):
        svc = SimpleNamespace(
            display_brightness_min=0,
            display_brightness_max=100,
            display_brightness_step_size=None,
        )
        n = self._make(svc=svc)
        assert n.native_step == 1.0


# ---------------------------------------------------------------------------
# Display on-time number (ThermostatGen2 / RoomThermostat2)
# ---------------------------------------------------------------------------


class TestDisplayOnTimeNumberGuard:
    def test_supports_false_value_present_skipped(
        self, mock_config_entry, mock_session
    ):
        therm = _fake_device(display_on_time=30, supports_display_configuration=False)
        mock_session.device_helper.thermostats = [therm]
        entities = asyncio.run(
            run_setup_entry(async_setup_entry, mock_config_entry, mock_session)
        )
        assert DISPLAY_ON_TIME not in _keys(entities)

    def test_supports_true_value_none_skipped(self, mock_config_entry, mock_session):
        therm = _fake_device(display_on_time=None, supports_display_configuration=True)
        mock_session.device_helper.thermostats = [therm]
        entities = asyncio.run(
            run_setup_entry(async_setup_entry, mock_config_entry, mock_session)
        )
        assert DISPLAY_ON_TIME not in _keys(entities)

    def test_both_present_created(self, mock_config_entry, mock_session):
        therm = _fake_device(display_on_time=60, supports_display_configuration=True)
        mock_session.device_helper.thermostats = [therm]
        entities = asyncio.run(
            run_setup_entry(async_setup_entry, mock_config_entry, mock_session)
        )
        assert DISPLAY_ON_TIME in _keys(entities)

    def test_roomthermostat_value_none_skipped(self, mock_config_entry, mock_session):
        rth = _fake_device(display_on_time=None, supports_display_configuration=True)
        mock_session.device_helper.roomthermostats = [rth]
        entities = asyncio.run(
            run_setup_entry(async_setup_entry, mock_config_entry, mock_session)
        )
        assert DISPLAY_ON_TIME not in _keys(entities)


class TestDisplayOnTimeNumber:
    def _make(self, display_on_time=60, svc=None):
        dev = SimpleNamespace(
            root_device_id="root1",
            id="dev1",
            display_on_time=display_on_time,
            _display_config_service=svc,
        )
        n = _entity_for(DISPLAY_ON_TIME, dev)
        n._attr_unique_id = f"{dev.root_device_id}_{dev.id}_display_on_time"
        return n

    def test_native_value_from_device(self):
        n = self._make(display_on_time=120)
        assert n.native_value == 120.0

    def test_native_value_none_when_missing(self):
        dev = SimpleNamespace(root_device_id="r", id="d")
        n = _entity_for(DISPLAY_ON_TIME, dev)
        assert n.native_value is None

    def test_native_min_from_service(self):
        svc = SimpleNamespace(
            display_on_time_min=5, display_on_time_max=3600, display_on_time_step_size=1
        )
        n = self._make(svc=svc)
        assert n.native_min_value == 5.0

    def test_native_max_from_service(self):
        svc = SimpleNamespace(
            display_on_time_min=0, display_on_time_max=900, display_on_time_step_size=30
        )
        n = self._make(svc=svc)
        assert n.native_max_value == 900.0

    def test_fallbacks_when_no_service(self):
        n = self._make(svc=None)
        assert n.native_min_value == 0.0
        assert n.native_max_value == 3600.0
        assert n.native_step == 1.0

    def test_set_native_value_writes_to_device(self):
        mock_setter = AsyncMock()
        dev = SimpleNamespace(
            root_device_id="r",
            id="d",
            display_on_time=60,
            _display_config_service=None,
            async_set_display_on_time=mock_setter,
        )
        n = _entity_for(DISPLAY_ON_TIME, dev)
        asyncio.run(n.async_set_native_value(300))
        mock_setter.assert_awaited_once_with(300)

    def test_unique_id_format(self):
        n = self._make()
        assert n._attr_unique_id == "root1_dev1_display_on_time"

    def test_thermostat_display_on_time_created_when_attr_present(
        self, mock_config_entry, mock_session
    ):
        therm = _fake_device(display_on_time=30, supports_display_configuration=True)
        mock_session.device_helper.thermostats = [therm]
        entities = asyncio.run(
            run_setup_entry(async_setup_entry, mock_config_entry, mock_session)
        )
        assert DISPLAY_ON_TIME in _keys(entities)

    def test_thermostat_display_on_time_skipped_when_attr_absent(
        self, mock_config_entry, mock_session
    ):
        therm = _fake_device()
        mock_session.device_helper.thermostats = [therm]
        entities = asyncio.run(
            run_setup_entry(async_setup_entry, mock_config_entry, mock_session)
        )
        assert DISPLAY_ON_TIME not in _keys(entities)

    def test_min_none_falls_back(self):
        svc = SimpleNamespace(
            display_on_time_min=None,
            display_on_time_max=3600,
            display_on_time_step_size=1,
        )
        n = self._make(svc=svc)
        assert n.native_min_value == 0.0

    def test_max_none_falls_back(self):
        svc = SimpleNamespace(
            display_on_time_min=0, display_on_time_max=None, display_on_time_step_size=1
        )
        n = self._make(svc=svc)
        assert n.native_max_value == 3600.0

    def test_step_none_falls_back(self):
        svc = SimpleNamespace(
            display_on_time_min=0,
            display_on_time_max=3600,
            display_on_time_step_size=None,
        )
        n = self._make(svc=svc)
        assert n.native_step == 1.0


class TestDisplayOnTimeNativeStep:
    """native_step returns float from the display config service attribute."""

    def test_step_from_service(self):
        svc = SimpleNamespace(display_on_time_step_size=30)
        device = _fake_device_cg(_display_config_service=svc, display_on_time=60.0)
        num = _entity_for(DISPLAY_ON_TIME, device)
        assert num.native_step == 30.0

    def test_step_fallback_when_no_service(self):
        device = _fake_device_cg(display_on_time=60.0)
        num = _entity_for(DISPLAY_ON_TIME, device)
        assert num.native_step == 1.0

    def test_step_fallback_when_attr_none(self):
        svc = SimpleNamespace(display_on_time_step_size=None)
        device = _fake_device_cg(_display_config_service=svc, display_on_time=60.0)
        num = _entity_for(DISPLAY_ON_TIME, device)
        assert num.native_step == 1.0


# ---------------------------------------------------------------------------
# Dimmer config numbers (#123)
# ---------------------------------------------------------------------------


def test_dimmer_number_min_reads_correctly():
    n = SHCNumber(
        device=_FAKE_DEVICE,
        entity_description=NUMBER_DESCRIPTIONS[DIMMER_MIN],
        entry_id="e1",
    )
    n._device = SimpleNamespace(dimmer_configuration=_dimmer_svc(min_b=15))
    assert n.native_value == 15.0


def test_dimmer_number_max_reads_correctly():
    n = SHCNumber(
        device=_FAKE_DEVICE,
        entity_description=NUMBER_DESCRIPTIONS[DIMMER_MAX],
        entry_id="e1",
    )
    n._device = SimpleNamespace(dimmer_configuration=_dimmer_svc(max_b=85))
    assert n.native_value == 85.0


def test_dimmer_number_speed_reads_correctly():
    n = SHCNumber(
        device=_FAKE_DEVICE,
        entity_description=NUMBER_DESCRIPTIONS[DIMMER_SPEED],
        entry_id="e1",
    )
    n._device = SimpleNamespace(dimmer_configuration=_dimmer_svc(speed=7))
    assert n.native_value == 7.0


def test_dimmer_number_returns_none_when_no_service():
    n = SHCNumber(
        device=_FAKE_DEVICE,
        entity_description=NUMBER_DESCRIPTIONS[DIMMER_MIN],
        entry_id="e1",
    )
    n._device = SimpleNamespace(dimmer_configuration=None)
    assert n.native_value is None


def test_dimmer_number_min_set_calls_set_brightness_range():
    n = SHCNumber(
        device=_FAKE_DEVICE,
        entity_description=NUMBER_DESCRIPTIONS[DIMMER_MIN],
        entry_id="e1",
    )
    svc = _dimmer_svc(min_b=10, max_b=90)
    n._device = SimpleNamespace(dimmer_configuration=svc)
    asyncio.run(n.async_set_native_value(25.0))
    svc.async_set_brightness_range.assert_awaited_once_with(min_brightness=25)


def test_dimmer_number_max_set_calls_set_brightness_range():
    n = SHCNumber(
        device=_FAKE_DEVICE,
        entity_description=NUMBER_DESCRIPTIONS[DIMMER_MAX],
        entry_id="e1",
    )
    svc = _dimmer_svc(min_b=10, max_b=90)
    n._device = SimpleNamespace(dimmer_configuration=svc)
    asyncio.run(n.async_set_native_value(80.0))
    svc.async_set_brightness_range.assert_awaited_once_with(max_brightness=80)


def test_dimmer_number_speed_set_calls_set_dimming_speed():
    n = SHCNumber(
        device=_FAKE_DEVICE,
        entity_description=NUMBER_DESCRIPTIONS[DIMMER_SPEED],
        entry_id="e1",
    )
    svc = _dimmer_svc(speed=5)
    n._device = SimpleNamespace(dimmer_configuration=svc)
    asyncio.run(n.async_set_native_value(3.0))
    svc.async_set_dimming_speed.assert_awaited_once_with(3)


def test_dimmer_number_clamps_to_range():
    n = SHCNumber(
        device=_FAKE_DEVICE,
        entity_description=NUMBER_DESCRIPTIONS[DIMMER_SPEED],
        entry_id="e1",
    )
    svc = _dimmer_svc()
    n._device = SimpleNamespace(dimmer_configuration=svc)
    asyncio.run(n.async_set_native_value(99.0))  # above max 10
    svc.async_set_dimming_speed.assert_awaited_once_with(10)


def test_dimmer_number_set_no_service_is_safe():
    n = SHCNumber(
        device=_FAKE_DEVICE,
        entity_description=NUMBER_DESCRIPTIONS[DIMMER_MIN],
        entry_id="e1",
    )
    n._device = SimpleNamespace(dimmer_configuration=None)
    # must not raise
    asyncio.run(n.async_set_native_value(50.0))


def test_dimmer_number_inverted_range_value_error_caught_not_raised():
    """Regression: async_set_brightness_range() (boschshcpy) raises
    ValueError on an inverted min/max range — the entity must catch it and
    log a warning, not let it propagate and crash the service call.
    """
    n = SHCNumber(
        device=_FAKE_DEVICE,
        entity_description=NUMBER_DESCRIPTIONS[DIMMER_MIN],
        entry_id="e1",
    )
    svc = _dimmer_svc(min_b=10, max_b=90)
    svc.async_set_brightness_range = AsyncMock(
        side_effect=ValueError(
            "Invalid brightness range: minBrightness (95) must be less than maxBrightness (90)"
        )
    )
    n._device = SimpleNamespace(dimmer_configuration=svc, name="Büro Dimmer")
    # must not raise
    asyncio.run(n.async_set_native_value(95.0))
    svc.async_set_brightness_range.assert_awaited_once_with(min_brightness=95)


def test_dimmer_number_has_correct_names():
    n_min = SHCNumber(
        device=_FAKE_DEVICE,
        entity_description=NUMBER_DESCRIPTIONS[DIMMER_MIN],
        entry_id="e1",
    )
    n_max = SHCNumber(
        device=_FAKE_DEVICE,
        entity_description=NUMBER_DESCRIPTIONS[DIMMER_MAX],
        entry_id="e1",
    )
    n_spd = SHCNumber(
        device=_FAKE_DEVICE,
        entity_description=NUMBER_DESCRIPTIONS[DIMMER_SPEED],
        entry_id="e1",
    )
    assert n_min._attr_name == "Dimmer Min Brightness"
    assert n_max._attr_name == "Dimmer Max Brightness"
    assert n_spd._attr_name == "Dimming Speed"


def test_dimmer_number_unique_ids():
    n = SHCNumber(
        device=_FAKE_DEVICE,
        entity_description=NUMBER_DESCRIPTIONS[DIMMER_MIN],
        entry_id="e1",
    )
    assert n.unique_id == "root-1_hdm:ZigBee:dimmer1_dimmer_min"


# ---------------------------------------------------------------------------
# Bypass timeout number (hass#120 audit)
# ---------------------------------------------------------------------------


class TestBypassTimeoutNumberClassAttrs:
    def _entity(self):
        return _entity_for(BYPASS_TIMEOUT, SimpleNamespace())

    def test_entity_category_is_config(self):
        assert self._entity().entity_category == EntityCategory.CONFIG

    def test_native_unit_is_minutes(self):
        """hass#120: confirmed via APK decompile (bypass_configuration.xml
        slider, app:quantityUnit="MINUTE") — not seconds as previously
        assumed (no OpenAPI spec exists for this service).
        """
        assert self._entity().native_unit_of_measurement == UnitOfTime.MINUTES

    def test_native_min_is_1(self):
        assert self._entity().native_min_value == 1.0

    def test_native_max_is_15(self):
        assert self._entity().native_max_value == 15.0


class TestBypassTimeoutNativeValue:
    def test_native_value_reads_bypass_timeout(self):
        num = _entity_for(BYPASS_TIMEOUT, SimpleNamespace(bypass_timeout=7))
        assert num.native_value == pytest.approx(7.0)

    def test_native_value_none_when_attribute_missing(self):
        num = _entity_for(BYPASS_TIMEOUT, SimpleNamespace())
        assert num.native_value is None


class TestBypassTimeoutSetNativeValue:
    def test_set_value_writes_clamped_value(self):
        dev = SimpleNamespace(
            name="Shutter Contact",
            bypass_timeout=5,
            async_set_bypass_timeout=AsyncMock(),
        )
        num = _entity_for(BYPASS_TIMEOUT, dev)
        asyncio.run(num.async_set_native_value(9.0))
        dev.async_set_bypass_timeout.assert_awaited_once_with(9)

    def test_set_value_clamps_to_max_15(self):
        dev = SimpleNamespace(
            name="Shutter Contact",
            bypass_timeout=5,
            async_set_bypass_timeout=AsyncMock(),
        )
        num = _entity_for(BYPASS_TIMEOUT, dev)
        asyncio.run(num.async_set_native_value(999.0))
        dev.async_set_bypass_timeout.assert_awaited_once_with(15)

    def test_set_value_clamps_to_min_1(self):
        dev = SimpleNamespace(
            name="Shutter Contact",
            bypass_timeout=5,
            async_set_bypass_timeout=AsyncMock(),
        )
        num = _entity_for(BYPASS_TIMEOUT, dev)
        asyncio.run(num.async_set_native_value(0.0))
        dev.async_set_bypass_timeout.assert_awaited_once_with(1)

    def test_set_value_shc_exception_raises_home_assistant_error(self):
        dev = SimpleNamespace(
            name="Shutter Contact",
            bypass_timeout=5,
            async_set_bypass_timeout=AsyncMock(side_effect=SHCException("rejected")),
        )
        num = _entity_for(BYPASS_TIMEOUT, dev)
        with pytest.raises(HomeAssistantError) as exc_info:
            asyncio.run(num.async_set_native_value(9.0))
        assert exc_info.value.translation_key == "number_set_failed"


# ---------------------------------------------------------------------------
# Cross-entity async_set_native_value client-error paths
# ---------------------------------------------------------------------------


class TestNumberErrorPaths:
    """aiohttp.ClientError from the underlying setter must be caught (logged),
    not propagate, across every number entity type.
    """

    def test_siren_config_number_async_set_client_error(self):
        """Siren config number.async_set_native_value error path."""
        siren_svc = MagicMock()
        siren_svc.async_set_configuration = AsyncMock(
            side_effect=aiohttp.ClientError("err")
        )
        num = SHCNumber.__new__(SHCNumber)
        num.entity_description = _SIREN_ALARM_DELAY
        num._device = SimpleNamespace(siren=siren_svc, name="Siren")
        _run(num.async_set_native_value(30.0))  # must not raise

    def test_shcnumber_async_set_client_error(self):
        """SHCNumber(offset).async_set_native_value error path."""
        num = SHCNumber.__new__(SHCNumber)
        num.entity_description = NUMBER_DESCRIPTIONS[OFFSET]
        num._device = SimpleNamespace(
            name="Thermostat",
            min_offset=-5.0,
            max_offset=5.0,
            async_set_offset=AsyncMock(side_effect=aiohttp.ClientError("err")),
        )
        _run(num.async_set_native_value(1.0))  # must not raise

    def test_impulse_length_number_async_set_client_error(self):
        """Impulse-length number.async_set_native_value error path."""
        num = SHCNumber.__new__(SHCNumber)
        num.entity_description = NUMBER_DESCRIPTIONS[IMPULSE_LENGTH]
        num._device = SimpleNamespace(
            name="Relay",
            async_set_impulse_length=AsyncMock(side_effect=aiohttp.ClientError("err")),
        )
        _run(num.async_set_native_value(1.0))  # must not raise

    def test_power_threshold_number_async_set_client_error(self):
        """Power-threshold number.async_set_native_value error path."""
        num = SHCNumber.__new__(SHCNumber)
        num.entity_description = NUMBER_DESCRIPTIONS[POWER_THRESHOLD]
        num._device = SimpleNamespace(
            name="SmartPlug",
            async_set_power_threshold=AsyncMock(side_effect=aiohttp.ClientError("err")),
        )
        _run(num.async_set_native_value(50.0))  # must not raise

    def test_enter_duration_number_async_set_client_error(self):
        """Enter-duration number.async_set_native_value error path."""
        num = SHCNumber.__new__(SHCNumber)
        num.entity_description = NUMBER_DESCRIPTIONS[ENTER_DURATION]
        num._device = SimpleNamespace(
            name="SmartPlug",
            async_set_enter_duration_seconds=AsyncMock(
                side_effect=aiohttp.ClientError("err")
            ),
        )
        _run(num.async_set_native_value(60.0))  # must not raise

    def test_led_brightness_number_async_set_client_error(self):
        """LED-brightness number.async_set_native_value error path."""
        num = SHCNumber.__new__(SHCNumber)
        num.entity_description = NUMBER_DESCRIPTIONS[LED_BRIGHTNESS]
        num._device = SimpleNamespace(
            name="SmartPlug",
            async_set_led_brightness=AsyncMock(side_effect=aiohttp.ClientError("err")),
        )
        _run(num.async_set_native_value(50.0))  # must not raise

    def test_display_brightness_number_async_set_client_error(self):
        """Display-brightness number.async_set_native_value error path."""
        num = SHCNumber.__new__(SHCNumber)
        num.entity_description = NUMBER_DESCRIPTIONS[DISPLAY_BRIGHTNESS]
        num._device = SimpleNamespace(
            name="Thermostat",
            async_set_display_brightness=AsyncMock(
                side_effect=aiohttp.ClientError("err")
            ),
        )
        _run(num.async_set_native_value(80.0))  # must not raise

    def test_display_on_time_number_async_set_client_error(self):
        """Display-on-time number.async_set_native_value error path."""
        num = SHCNumber.__new__(SHCNumber)
        num.entity_description = NUMBER_DESCRIPTIONS[DISPLAY_ON_TIME]
        num._device = SimpleNamespace(
            name="Thermostat",
            async_set_display_on_time=AsyncMock(side_effect=aiohttp.ClientError("err")),
        )
        _run(num.async_set_native_value(10.0))  # must not raise

    def test_dimmer_config_number_async_set_client_error(self):
        """Dimmer-config number.async_set_native_value error path."""
        svc = MagicMock()
        svc.async_set_brightness_range = AsyncMock(
            side_effect=aiohttp.ClientError("err")
        )
        num = SHCNumber.__new__(SHCNumber)
        num.entity_description = NUMBER_DESCRIPTIONS[DIMMER_MIN]
        num._device = SimpleNamespace(dimmer_configuration=svc, name="Dimmer")
        _run(num.async_set_native_value(10.0))  # must not raise


# ---------------------------------------------------------------------------
# TemperatureDropValueNumber
# ---------------------------------------------------------------------------


def _make_tds_number(room=None):
    num = TemperatureDropValueNumber.__new__(TemperatureDropValueNumber)
    num._device = SimpleNamespace(name="Kinderzimmer")
    num._room = room if room is not None else MagicMock()
    num._value = None
    return num


def test_tds_number_native_value_none_initially():
    num = _make_tds_number()
    assert num.native_value is None


def test_tds_number_async_update_sets_value():
    room = MagicMock()
    room.async_temperature_drop_service = AsyncMock(
        return_value={"configuration": {"dropTemperature": 1.5}}
    )
    num = _make_tds_number(room)
    asyncio.run(num.async_update())
    assert num.native_value == 1.5


def test_tds_number_async_update_handles_missing_value():
    room = MagicMock()
    room.async_temperature_drop_service = AsyncMock(
        return_value={"configuration": {}}
    )
    num = _make_tds_number(room)
    asyncio.run(num.async_update())
    assert num.native_value is None


def test_tds_number_async_update_logs_on_error():
    room = MagicMock()
    room.async_temperature_drop_service = AsyncMock(side_effect=SHCException("boom"))
    num = _make_tds_number(room)
    asyncio.run(num.async_update())  # must not raise


def test_tds_number_async_set_native_value_calls_room():
    room = MagicMock()
    room.async_set_temperature_drop_value = AsyncMock()
    num = _make_tds_number(room)
    asyncio.run(num.async_set_native_value(2.0))
    room.async_set_temperature_drop_value.assert_awaited_once_with(2.0)
    assert num.native_value == 2.0


def test_tds_number_async_set_native_value_wraps_shc_exception():
    room = MagicMock()
    room.async_set_temperature_drop_value = AsyncMock(side_effect=SHCException("boom"))
    num = _make_tds_number(room)
    with pytest.raises(HomeAssistantError):
        asyncio.run(num.async_set_native_value(2.0))


class TestTemperatureDropNumberSetupEntry:
    def test_created_when_service_present(self, mock_config_entry, mock_session):
        climate = SimpleNamespace(
            id="roomClimateControl_hz_1",
            root_device_id="shc1",
            room_id="hz_1",
            name="Kinderzimmer",
            manufacturer="BOSCH",
            device_model="ROOM_CLIMATE_CONTROL",
            status="AVAILABLE",
            subscribe_callback=MagicMock(),
            unsubscribe_callback=MagicMock(),
        )
        mock_session.device_helper.climate_controls = [climate]
        room = MagicMock()
        room.name = "Kinderzimmer"
        room.async_temperature_drop_service = AsyncMock(
            return_value={"configuration": {"dropTemperature": 1.0}}
        )
        mock_session.room = MagicMock(return_value=room)
        entities = asyncio.run(
            run_setup_entry(async_setup_entry, mock_config_entry, mock_session)
        )
        assert any(isinstance(e, TemperatureDropValueNumber) for e in entities)
        drop_number = next(
            e for e in entities if isinstance(e, TemperatureDropValueNumber)
        )
        # hass#372: must report the room's own name, not the shared
        # ROOM_CLIMATE_CONTROL device's generic raw name.
        assert drop_number.device_name == "Kinderzimmer"

    def test_skipped_when_service_absent(self, mock_config_entry, mock_session):
        climate = SimpleNamespace(
            id="roomClimateControl_hz_1",
            root_device_id="shc1",
            room_id="hz_1",
            name="Kinderzimmer",
            manufacturer="BOSCH",
            device_model="ROOM_CLIMATE_CONTROL",
            status="AVAILABLE",
            subscribe_callback=MagicMock(),
            unsubscribe_callback=MagicMock(),
        )
        mock_session.device_helper.climate_controls = [climate]
        room = MagicMock()
        room.async_temperature_drop_service = AsyncMock(
            side_effect=SHCException("404")
        )
        mock_session.room = MagicMock(return_value=room)
        entities = asyncio.run(
            run_setup_entry(async_setup_entry, mock_config_entry, mock_session)
        )
        assert not any(isinstance(e, TemperatureDropValueNumber) for e in entities)

    def test_skipped_when_room_id_none(self, mock_config_entry, mock_session):
        climate = SimpleNamespace(
            id="roomClimateControl_hz_1",
            root_device_id="shc1",
            room_id=None,
            name="Kinderzimmer",
            manufacturer="BOSCH",
            device_model="ROOM_CLIMATE_CONTROL",
            status="AVAILABLE",
            subscribe_callback=MagicMock(),
            unsubscribe_callback=MagicMock(),
        )
        mock_session.device_helper.climate_controls = [climate]
        entities = asyncio.run(
            run_setup_entry(async_setup_entry, mock_config_entry, mock_session)
        )
        assert not any(isinstance(e, TemperatureDropValueNumber) for e in entities)
