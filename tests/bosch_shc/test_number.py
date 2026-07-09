"""Tests for the number.py platform.

Covers SHCNumber (thermostat/roomthermostat/wallthermostat temperature
offset), ImpulseLengthNumber, HeatingCircuitSetpointNumber,
BypassTimeoutNumber, the APK-batch smart-plug/thermostat entities
(PowerThresholdNumber, EnterDurationNumber, LedBrightnessNumber,
DisplayBrightnessNumber, DisplayOnTimeNumber), SirenConfigNumber and
DimmerConfigNumber — property getters, async_set_native_value clamping and
error handling, and the async_setup_entry wiring (including
device/room-exclusion and dual-guard "supports_* AND value is not None"
entity-creation gating).

Pure-unit style throughout: __new__ bypass + SimpleNamespace/MagicMock device
doubles, no HA test harness.
"""

import asyncio
import json
from types import SimpleNamespace
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
    BypassTimeoutNumber,
    DimmerConfigNumber,
    DisplayBrightnessNumber,
    DisplayOnTimeNumber,
    EnterDurationNumber,
    HeatingCircuitSetpointNumber,
    ImpulseLengthNumber,
    LedBrightnessNumber,
    PowerThresholdNumber,
    SHCNumber,
    SirenConfigNumber,
    async_setup_entry,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(coro):
    return asyncio.run(coro)


def _excl(*ids):
    return {OPT_EXCLUDED_DEVICES: list(ids)}


def _make_number(
    *,
    offset=0.0,
    min_offset=-5.0,
    max_offset=5.0,
    step_size=0.5,
):
    """Return an SHCNumber with _device set via SimpleNamespace (no HA init)."""
    entity = SHCNumber.__new__(SHCNumber)
    entity._device = SimpleNamespace(
        name="Test Number",
        offset=offset,
        min_offset=min_offset,
        max_offset=max_offset,
        step_size=step_size,
        async_set_offset=AsyncMock(),
    )
    return entity


def _fake_number_init_device(name="test-number", root_device_id="root1", device_id="dev1"):
    """Fake device for SHCNumber.__init__ (distinct from the APK-entity
    `_fake_device` below — different field set, only what SHCNumber needs)."""
    return SimpleNamespace(
        name=name,
        root_device_id=root_device_id,
        id=device_id,
        offset=0.0,
        min_offset=-5.0,
        max_offset=5.0,
        step_size=0.5,
    )


def _make_hass() -> SimpleNamespace:
    return SimpleNamespace()


def _make_config_entry(session: object) -> SimpleNamespace:
    entry = SimpleNamespace(options={}, entry_id="E1")
    entry.runtime_data = SimpleNamespace(session=session)
    return entry


def _collect() -> tuple[list, callable]:
    """Return (collected_list, async_add_entities callable)."""
    collected: list = []

    def add(entities: list) -> None:
        collected.extend(entities)

    return collected, add


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


def _make_number_session(**kw):
    """Session builder for the excluded-device / dual-guard setup tests."""
    defaults = dict(
        thermostats=[],
        roomthermostats=[],
        micromodule_impulse_relays=[],
        heating_circuits=[],
        smart_plugs=[],
        smart_plugs_compact=[],
        motion_detectors2=[],
    )
    defaults.update(kw)
    return SimpleNamespace(device_helper=SimpleNamespace(**defaults))


def _run_number_setup(session, options=None):
    hass = SimpleNamespace()
    config_entry = SimpleNamespace(options=options or {}, entry_id="E1")
    config_entry.runtime_data = SimpleNamespace(session=session)
    collected = []

    def _add(ents, *a, **kw):
        collected.extend(ents)

    asyncio.run(async_setup_entry(hass, config_entry, _add))
    return collected


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


def _fake_device(**kwargs):
    """Fake APK-entity device (smart plug / thermostat) for the dual-guard
    and new-entity tests (PowerThreshold/EnterDuration/LedBrightness/
    DisplayBrightness/DisplayOnTime Number)."""
    defaults = dict(name="Dev", id="dev1", root_device_id="root1",
                    serial="SER1", supports_silentmode=False)
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _fake_device_cg(**kwargs):
    """Fake device used by the smart-plug-compact-exclusion / DisplayOnTime
    native_step coverage tests. Distinct field defaults from `_fake_device`
    above (device_services instead of supports_silentmode) — kept separate
    per the source files' own conventions rather than silently merged."""
    base = dict(
        id="dev1",
        root_device_id="root1",
        name="FakeDev",
        device_services=[],
        serial="SER1",
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def _make_session(**helper_lists):
    defaults = dict(
        thermostats=[],
        roomthermostats=[],
        micromodule_impulse_relays=[],
        heating_circuits=[],
    )
    defaults.update(helper_lists)
    device_helper = SimpleNamespace(**defaults)
    return SimpleNamespace(device_helper=device_helper)


def _make_full_session(**kwargs):
    """Session factory that includes smart_plugs/smart_plugs_compact keys
    (needed for the energy-saving / led-brightness / display guard tests)."""
    defaults = dict(
        thermostats=[],
        roomthermostats=[],
        micromodule_impulse_relays=[],
        heating_circuits=[],
        smart_plugs=[],
        smart_plugs_compact=[],
    )
    defaults.update(kwargs)
    device_helper = SimpleNamespace(**defaults)
    return SimpleNamespace(device_helper=device_helper)


def _make_hass_and_entry(session):
    entry_id = "E1"
    hass = SimpleNamespace()
    config_entry = SimpleNamespace(options={}, entry_id=entry_id,
                                   async_on_unload=MagicMock())
    config_entry.runtime_data = SimpleNamespace(session=session)
    return hass, config_entry


async def _async_setup(session):
    hass, config_entry = _make_hass_and_entry(session)
    entities = []

    def add_entities(new_ents, *args, **kwargs):
        entities.extend(new_ents)

    await async_setup_entry(hass, config_entry, add_entities)
    return entities


def _setup(session):
    return asyncio.run(_async_setup(session))


def _types(entities):
    return [type(e).__name__ for e in entities]


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


def _make_hass_ne(session):
    """hass double for TestNumberSetupNewEntities (distinct from
    `_make_hass()` above — accepts and ignores a session arg)."""
    return SimpleNamespace()


def _make_config_entry_ne(session):
    """config_entry double for TestNumberSetupNewEntities (distinct from
    `_make_config_entry()` above — also wires shc_device/title onto
    runtime_data)."""
    entry = SimpleNamespace(options={}, entry_id="E1")
    entry.runtime_data = SimpleNamespace(
        session=session, shc_device=None, title="Test SHC"
    )
    return entry


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


def _fake_hass(entry_id="E1", session=None, shc=None, options=None):
    """Minimal hass. session/shc are cached so a paired _fake_entry(hass=...)
    call can wire them onto entry.runtime_data (the modern storage location —
    this integration no longer uses hass.data[DOMAIN])."""
    shc_obj = shc or SimpleNamespace(
        identifiers={("bosch_shc", "shc")},
        name="SHC", manufacturer="Bosch", model="SHC", id="shc1",
    )
    h = MagicMock()
    h.data = {}
    h._fake_session = session
    h._fake_shc = shc_obj

    async def _executor_job(fn, *args):
        return fn(*args)

    h.async_add_executor_job = _executor_job
    h.config_entries = MagicMock()
    h.bus = MagicMock()
    h.bus.async_listen_once = MagicMock(return_value=MagicMock())
    h.async_create_task = MagicMock()
    return h


def _fake_entry(entry_id="E1", title="Test SHC", options=None, hass=None):
    """Build a fake config entry with runtime_data wired from `hass` (as
    produced by _fake_hass) when provided."""
    entry = MagicMock()
    entry.entry_id = entry_id
    entry.title = title
    entry.options = options or {}
    entry.unique_id = "uid1"
    entry.async_on_unload = MagicMock()
    entry.runtime_data = SimpleNamespace(
        session=getattr(hass, "_fake_session", None) if hass is not None else None,
        shc_device=getattr(hass, "_fake_shc", None) if hass is not None else None,
        title=title,
    )
    return entry


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
# SHCNumber.__init__ and class-level attributes
# ---------------------------------------------------------------------------


class TestSHCNumberInit:
    """Cover SHCNumber.__init__ lines 60-69."""

    def test_init_no_attr_name_sets_name_none(self):
        # has_entity_name=True + _attr_name=None means this is the primary entity
        # — HA uses the device name as the display name; .name property returns None.
        dev = _fake_number_init_device()
        number = SHCNumber(device=dev, entry_id="test", attr_name=None)
        assert number._attr_name is None

    def test_init_no_attr_name_sets_unique_id(self):
        dev = _fake_number_init_device()
        number = SHCNumber(device=dev, entry_id="test", attr_name=None)
        assert number._attr_unique_id == "root1_dev1"

    def test_init_with_attr_name_sets_attr_name(self):
        # has_entity_name=True: _attr_name is just the feature label (no device prefix).
        # HA prepends the device name when building the display name.
        dev = _fake_number_init_device()
        number = SHCNumber(device=dev, entry_id="test", attr_name="Offset")
        assert number._attr_name == "Offset"

    def test_init_with_attr_name_lowercased_in_unique_id(self):
        dev = _fake_number_init_device()
        number = SHCNumber(device=dev, entry_id="test", attr_name="Offset")
        assert number._attr_unique_id == "root1_dev1_offset"

    def test_init_device_stored(self):
        dev = _fake_number_init_device()
        number = SHCNumber(device=dev, entry_id="test", attr_name="Offset")
        assert number._device is dev

    def test_init_entry_id_stored(self):
        dev = _fake_number_init_device()
        number = SHCNumber(device=dev, entry_id="myentry", attr_name=None)
        assert number._entry_id == "myentry"

    def test_init_attr_name_mixed_case_lowercased_in_unique_id(self):
        dev = _fake_number_init_device(name="my-thermo", root_device_id="root2", device_id="dev2")
        number = SHCNumber(device=dev, entry_id="e", attr_name="TempOffset")
        assert number._attr_unique_id == "root2_dev2_tempoffset"
        # has_entity_name=True: _attr_name is just the feature label (no device prefix)
        assert number._attr_name == "TempOffset"


class TestSHCNumberClassAttrs:
    """Cover class-level attribute declarations (lines 49-51).

    Access via instance because HA parent classes shadow some attrs with properties.
    """

    def _make_number(self):
        dev = _fake_number_init_device()
        return SHCNumber(device=dev, entry_id="test", attr_name="Offset")

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
# SHCNumber — native_value / native_min_value / native_max_value / native_step
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
# SHCNumber.async_set_native_value — round-trip, clamping, error handling
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
    not propagate as a raw SHCException."""
    entity = _make_number(offset=0.0, min_offset=-5.0, max_offset=5.0)
    entity._device.async_set_offset = AsyncMock(
        side_effect=SHCException("rejected")
    )
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
    boschshcpy's _put_api_or_fail) must be logged, not crash the write call."""
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
# SHCNumber — async_setup_entry (thermostats / roomthermostats / wallthermostats)
# ---------------------------------------------------------------------------


class TestNumberSetupEntry:
    """Number async_setup_entry: thermostats + roomthermostats → SHCNumber."""

    def _run(self, session: object) -> list:
        hass = _make_hass()
        entry = _make_config_entry(session)
        collected, add = _collect()

        asyncio.run(async_setup_entry(hass, entry, add))  # type: ignore[arg-type]
        return collected

    def test_thermostats_produce_shc_number_entities(self) -> None:
        """session.device_helper.thermostats → SHCNumber."""
        dev = _number_device()
        session = _make_number_session(thermostats=[dev])
        result = self._run(session)
        assert len(result) == 1
        assert isinstance(result[0], SHCNumber)

    def test_roomthermostats_produce_shc_number_entities(self) -> None:
        """session.device_helper.roomthermostats → SHCNumber."""
        dev = _number_device()
        session = _make_number_session(roomthermostats=[dev])
        result = self._run(session)
        assert len(result) == 1
        assert isinstance(result[0], SHCNumber)

    def test_wallthermostats_produce_shc_number_entities(self) -> None:
        """session.device_helper.wallthermostats (BWTH/BWTH24) → SHCNumber."""
        dev = _number_device()
        session = _make_number_session(wallthermostats=[dev])
        result = self._run(session)
        assert len(result) == 1
        assert isinstance(result[0], SHCNumber)

    def test_wallthermostat_without_offset_service_skipped(self) -> None:
        """THB devices (no TemperatureOffset service) must not create SHCNumber."""
        import copy
        dev = copy.copy(_number_device())
        dev.supports_temperature_offset = False
        session = _make_number_session(wallthermostats=[dev])
        result = self._run(session)
        assert result == []

    def test_mixed_thermostats_collected(self) -> None:
        """Thermostat + roomthermostat + wallthermostat → 3 SHCNumber entities."""
        session = _make_number_session(
            thermostats=[_number_device()],
            roomthermostats=[_number_device()],
            wallthermostats=[_number_device()],
        )
        result = self._run(session)
        assert len(result) == 3
        assert all(isinstance(e, SHCNumber) for e in result)

    def test_no_thermostats_adds_nothing(self) -> None:
        """No thermostats/relays/heating_circuits → nothing added."""
        session = _make_number_session()
        result = self._run(session)
        assert result == []

    def test_attr_name_offset_applied(self) -> None:
        """async_setup_entry always passes attr_name='Offset'.

        With _attr_has_entity_name=True, _attr_name holds only the feature
        label; HA prepends the device name for display ('Test Thermostat Offset').
        """
        dev = _number_device()
        session = _make_number_session(thermostats=[dev])
        result = self._run(session)
        assert result[0]._attr_name == "Offset"

    def test_unique_id_includes_offset_suffix(self) -> None:
        """unique_id for 'Offset' attr_name ends in '_offset'."""
        dev = _number_device()
        session = _make_number_session(thermostats=[dev])
        result = self._run(session)
        assert result[0]._attr_unique_id.endswith("_offset")

    def test_entry_id_stored(self) -> None:
        dev = _number_device()
        session = _make_number_session(thermostats=[dev])
        result = self._run(session)
        assert result[0]._entry_id == "E1"


class TestNumberSetupExcludedThermostat:
    def test_excluded_thermostat_not_in_entities(self):
        """Excluded thermostat must be skipped (line 42 continue)."""
        dev = _fake_dev("trv-excl", offset=0.0, min_offset=-5.0,
                        max_offset=5.0, step_size=0.5)
        session = _make_fake_session(thermostats=[dev])
        entities = _run_setup_with_options(session, _excl("trv-excl"))
        ids = [getattr(e, "_device", None) and e._device.id for e in entities]
        assert "trv-excl" not in ids

    def test_excluded_roomthermostat_not_in_entities(self):
        """Excluded roomthermostat must be skipped (same loop, line 42 continue)."""
        dev = _fake_dev("rt-excl", offset=0.0, min_offset=-5.0,
                        max_offset=5.0, step_size=0.5)
        session = _make_fake_session(roomthermostats=[dev])
        entities = _run_setup_with_options(session, _excl("rt-excl"))
        ids = [getattr(e, "_device", None) and e._device.id for e in entities]
        assert "rt-excl" not in ids

    def test_non_excluded_thermostat_still_added(self):
        """Non-excluded thermostat must still produce a SHCNumber entity."""
        dev = _fake_dev("trv-keep", offset=1.0, min_offset=-5.0,
                        max_offset=5.0, step_size=0.5)
        session = _make_fake_session(thermostats=[dev])
        entities = _run_setup_with_options(session, {})
        assert any(isinstance(e, SHCNumber) for e in entities)

    def test_mix_excluded_and_kept_thermostat(self):
        kept = _fake_dev("trv-a", offset=0.0, min_offset=-5.0,
                         max_offset=5.0, step_size=0.5)
        excl = _fake_dev("trv-b", offset=0.0, min_offset=-5.0,
                         max_offset=5.0, step_size=0.5)
        session = _make_fake_session(thermostats=[kept, excl])
        entities = _run_setup_with_options(session, _excl("trv-b"))
        ids = [getattr(e, "_device", None) and e._device.id for e in entities]
        assert "trv-a" in ids
        assert "trv-b" not in ids


class TestNumberSmartPlugCompactDeviceExcluded:
    """number.py line 95 — device_excluded continue in smart_plugs/compact loop."""

    def test_excluded_compact_plug_not_added(self):
        plug = _fake_device_cg(id="cp-excl", power_threshold=100.0)
        session = _make_number_session(smart_plugs_compact=[plug])
        entities = _run_number_setup(session, options=_excl("cp-excl"))
        ids = [getattr(getattr(e, "_device", None), "id", None) for e in entities]
        assert "cp-excl" not in ids

    def test_excluded_smart_plug_not_added(self):
        plug = _fake_device_cg(id="sp-excl", power_threshold=100.0)
        session = _make_number_session(smart_plugs=[plug])
        entities = _run_number_setup(session, options=_excl("sp-excl"))
        ids = [getattr(getattr(e, "_device", None), "id", None) for e in entities]
        assert "sp-excl" not in ids


# ---------------------------------------------------------------------------
# ImpulseLengthNumber
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


class TestImpulseLengthNativeValue:
    """ImpulseLengthNumber — native_value (lib stores tenths of seconds)."""

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
        num = ImpulseLengthNumber.__new__(ImpulseLengthNumber)
        num._device = dev
        asyncio.run(num.async_set_native_value(5.0))
        dev.async_set_impulse_length.assert_awaited_once_with(50)

    def test_set_value_clamps_to_max(self):
        """Values above 60 s are clamped to 60 s = 600 tenths."""
        dev = SimpleNamespace(
            name="relay",
            impulse_length=100,
            async_set_impulse_length=AsyncMock(),
        )
        num = ImpulseLengthNumber.__new__(ImpulseLengthNumber)
        num._device = dev
        asyncio.run(num.async_set_native_value(999.0))
        dev.async_set_impulse_length.assert_awaited_once_with(600)

    def test_set_value_clamps_to_min(self):
        """Values below 0.1 s are clamped to 0.1 s = 1 tenth."""
        dev = SimpleNamespace(
            name="relay",
            impulse_length=100,
            async_set_impulse_length=AsyncMock(),
        )
        num = ImpulseLengthNumber.__new__(ImpulseLengthNumber)
        num._device = dev
        asyncio.run(num.async_set_native_value(0.0))
        dev.async_set_impulse_length.assert_awaited_once_with(1)

    def test_set_value_shc_exception_raises_home_assistant_error(self):
        """A real SHC API rejection must surface as a translated
        HomeAssistantError, not propagate as a raw SHCException."""
        dev = SimpleNamespace(
            name="relay",
            impulse_length=100,
            async_set_impulse_length=AsyncMock(side_effect=SHCException("rejected")),
        )
        num = ImpulseLengthNumber.__new__(ImpulseLengthNumber)
        num._device = dev
        # __new__ bypasses SHCEntity.__init__ (which would normally delete
        # _attr_name in favor of translation_key lookup); set it directly so
        # the error-message f-string's self.name access doesn't need a real
        # platform/translation setup to resolve.
        num._attr_name = "Impulse Length"
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
        num = ImpulseLengthNumber.__new__(ImpulseLengthNumber)
        num._device = dev
        num._attr_name = "Impulse Length"
        with pytest.raises(HomeAssistantError) as exc_info:
            asyncio.run(num.async_set_native_value(5.0))
        assert exc_info.value.translation_key == "number_set_failed"


class TestNumberSetupImpulseRelayNoAttr:
    """Impulse relay — not hasattr(device, "impulse_length") continue (line 53)."""

    def test_device_without_impulse_length_attr_is_skipped(self):
        """Device missing impulse_length attribute must be skipped (line 53 continue)."""
        # No impulse_length attribute at all
        dev = _fake_dev("relay-no-attr")
        session = _make_fake_session(micromodule_impulse_relays=[dev])
        entities = _run_setup_with_options(session, {})
        ids = [getattr(e, "_device", None) and e._device.id for e in entities]
        assert "relay-no-attr" not in ids

    def test_device_without_impulse_length_produces_no_entity(self):
        dev = _fake_dev("relay-no-il")
        session = _make_fake_session(micromodule_impulse_relays=[dev])
        entities = _run_setup_with_options(session, {})
        assert not any(isinstance(e, ImpulseLengthNumber) for e in entities)


class TestNumberSetupImpulseRelayNoneValue:
    """Impulse relay — device.impulse_length is None continue (line 55)."""

    def test_device_with_none_impulse_length_is_skipped(self):
        """impulse_length=None must be skipped (line 55 continue)."""
        dev = _fake_dev("relay-none-il", impulse_length=None)
        session = _make_fake_session(micromodule_impulse_relays=[dev])
        entities = _run_setup_with_options(session, {})
        assert not any(isinstance(e, ImpulseLengthNumber) for e in entities)

    def test_device_with_zero_impulse_length_is_included(self):
        """impulse_length=0 is not None → entity IS created (boundary check)."""
        dev = _fake_dev("relay-zero-il", impulse_length=0)
        session = _make_fake_session(micromodule_impulse_relays=[dev])
        entities = _run_setup_with_options(session, {})
        # 0 is falsy but is not None; the code checks `is None`, so entity must appear
        assert any(isinstance(e, ImpulseLengthNumber) for e in entities)

    def test_device_with_valid_impulse_length_is_included(self):
        """impulse_length=100 → ImpulseLengthNumber entity is created."""
        dev = _fake_dev("relay-100", impulse_length=100)
        session = _make_fake_session(micromodule_impulse_relays=[dev])
        entities = _run_setup_with_options(session, {})
        assert any(isinstance(e, ImpulseLengthNumber) for e in entities)


class TestImpulseRelayDeviceExcluded:
    """device_excluded continue for impulse relay (line 53)."""

    def test_excluded_impulse_relay_not_added(self):
        """Excluded impulse relay must be skipped (line 53)."""
        dev = SimpleNamespace(
            id="ir-excl",
            name="Relay",
            root_device_id="root",
            serial="SER",
            device_services=[],
            impulse_length=100,
        )
        session = SimpleNamespace(
            device_helper=SimpleNamespace(
                thermostats=[],
                roomthermostats=[],
                micromodule_impulse_relays=[dev],
                heating_circuits=[],
            )
        )
        hass = SimpleNamespace()
        config_entry = SimpleNamespace(
            options={OPT_EXCLUDED_DEVICES: ["ir-excl"]},
            entry_id="E1",
        )
        config_entry.runtime_data = SimpleNamespace(session=session)
        collected = []
        asyncio.run(async_setup_entry(hass, config_entry, lambda e: collected.extend(e)))
        assert not any(
            getattr(e, "_device", None) and e._device.id == "ir-excl"
            for e in collected
        )


# ---------------------------------------------------------------------------
# HeatingCircuitSetpointNumber
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
        uncaught TypeError if not guarded explicitly."""
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


class TestHeatingCircuitSetpointSetValue:
    def test_eco_set_value_writes_to_service(self):
        """async_set_native_value calls async_set_setpoint_temperature_eco on device."""
        mock_setter = AsyncMock()
        dev = SimpleNamespace(
            name="HC", id="hc1", root_device_id="root1",
            _heating_circuit_service=SimpleNamespace(
                setpoint_temperature_eco=18.0,
            ),
            async_set_setpoint_temperature_eco=mock_setter,
        )
        num = HeatingCircuitSetpointNumber.__new__(HeatingCircuitSetpointNumber)
        num._device = dev
        num._getter_name = "setpoint_temperature_eco"
        num._setter_name = "setpoint_temperature_eco"
        num._range_attr = "eco_temperature_range"

        asyncio.run(num.async_set_native_value(19.0))
        mock_setter.assert_awaited_once_with(pytest.approx(19.0))

    def test_set_value_clamps_to_min(self):
        """Values below 5 °C → clamped to 5 °C."""
        mock_setter = AsyncMock()
        dev = SimpleNamespace(
            name="HC", id="hc1", root_device_id="root1",
            _heating_circuit_service=SimpleNamespace(
                setpoint_temperature_eco=18.0,
            ),
            async_set_setpoint_temperature_eco=mock_setter,
        )
        num = HeatingCircuitSetpointNumber.__new__(HeatingCircuitSetpointNumber)
        num._device = dev
        num._getter_name = "setpoint_temperature_eco"
        num._setter_name = "setpoint_temperature_eco"
        num._range_attr = "eco_temperature_range"

        asyncio.run(num.async_set_native_value(1.0))
        mock_setter.assert_awaited_once_with(pytest.approx(5.0))

    def test_set_value_clamps_to_max(self):
        """Values above 30 °C → clamped to 30 °C."""
        mock_setter = AsyncMock()
        dev = SimpleNamespace(
            name="HC", id="hc1", root_device_id="root1",
            _heating_circuit_service=SimpleNamespace(
                setpoint_temperature_comfort=21.0,
            ),
            async_set_setpoint_temperature_comfort=mock_setter,
        )
        num = HeatingCircuitSetpointNumber.__new__(HeatingCircuitSetpointNumber)
        num._device = dev
        num._getter_name = "setpoint_temperature_comfort"
        num._setter_name = "setpoint_temperature_comfort"
        num._range_attr = "comfort_temperature_range"

        asyncio.run(num.async_set_native_value(100.0))
        mock_setter.assert_awaited_once_with(pytest.approx(30.0))

    def test_set_value_with_no_async_setter_logs_warning(self):
        """When async_set_* is absent on device, log a warning and do nothing."""
        dev = SimpleNamespace(
            name="HC", id="hc1", root_device_id="root1",
            _heating_circuit_service=SimpleNamespace(
                setpoint_temperature_eco=18.0,
            ),
            # no async_set_setpoint_temperature_eco attribute
        )
        num = HeatingCircuitSetpointNumber.__new__(HeatingCircuitSetpointNumber)
        num._device = dev
        num._getter_name = "setpoint_temperature_eco"
        num._setter_name = "setpoint_temperature_eco"
        num._range_attr = "eco_temperature_range"

        with patch("custom_components.bosch_shc.number.LOGGER") as mock_log:
            asyncio.run(num.async_set_native_value(20.0))  # must not raise
        mock_log.warning.assert_called_once()

    def test_set_value_shc_exception_raises_home_assistant_error(self):
        """A real SHC API rejection must surface as a translated
        HomeAssistantError, not be swallowed as a plain LOGGER.warning."""
        mock_setter = AsyncMock(side_effect=SHCException("rejected"))
        dev = SimpleNamespace(
            name="HC", id="hc1", root_device_id="root1",
            _heating_circuit_service=SimpleNamespace(
                setpoint_temperature_eco=18.0,
            ),
            async_set_setpoint_temperature_eco=mock_setter,
        )
        num = HeatingCircuitSetpointNumber.__new__(HeatingCircuitSetpointNumber)
        num._device = dev
        num._getter_name = "setpoint_temperature_eco"
        num._setter_name = "setpoint_temperature_eco"
        num._range_attr = "eco_temperature_range"

        with pytest.raises(HomeAssistantError) as exc_info:
            asyncio.run(num.async_set_native_value(19.0))
        assert exc_info.value.translation_key == "number_set_failed"


class TestHeatingCircuitSetpointDynamicBounds:
    """The app reads a per-device setpoint range rather than a fixed
    constant (HeatingCircuitVerticalSliderFragment.setMinMax) — a
    floor-heating circuit commonly reports a raised minimum."""

    def test_falls_back_to_5_30_when_device_has_no_range_yet(self):
        num = HeatingCircuitSetpointNumber.__new__(HeatingCircuitSetpointNumber)
        num._device = SimpleNamespace(eco_temperature_range=None)
        num._range_attr = "eco_temperature_range"
        assert num.native_min_value == 5.0
        assert num.native_max_value == 30.0

    def test_uses_device_reported_range(self):
        num = HeatingCircuitSetpointNumber.__new__(HeatingCircuitSetpointNumber)
        num._device = SimpleNamespace(comfort_temperature_range=(16.0, 24.0))
        num._range_attr = "comfort_temperature_range"
        assert num.native_min_value == 16.0
        assert num.native_max_value == 24.0

    def test_clamps_to_device_reported_range_not_the_5_30_default(self):
        """A floor-heating circuit with a raised minimum (e.g. 10°C) must
        clamp there, not silently allow the old 5°C default."""
        mock_setter = AsyncMock()
        dev = SimpleNamespace(
            name="HC", id="hc1", root_device_id="root1",
            _heating_circuit_service=SimpleNamespace(
                setpoint_temperature_eco=18.0,
            ),
            async_set_setpoint_temperature_eco=mock_setter,
            eco_temperature_range=(10.0, 22.0),
        )
        num = HeatingCircuitSetpointNumber.__new__(HeatingCircuitSetpointNumber)
        num._device = dev
        num._getter_name = "setpoint_temperature_eco"
        num._setter_name = "setpoint_temperature_eco"
        num._range_attr = "eco_temperature_range"

        asyncio.run(num.async_set_native_value(1.0))
        mock_setter.assert_awaited_once_with(pytest.approx(10.0))


class TestNumberSetupExcludedHeatingCircuit:
    """Heating circuit device_excluded continue (line 67)."""

    def _hc_device(self, device_id):
        svc = SimpleNamespace(
            setpoint_temperature_eco=18.0,
            setpoint_temperature_comfort=21.0,
        )
        return _fake_dev(
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


class TestHeatingCircuitSetpointNumberSetNativeValueNoService:
    """HeatingCircuitSetpointNumber.async_set_native_value — svc is None path
    (LOGGER.warning + early return when async setter is absent)."""

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


class TestHeatingCircuitSetterAttributeError:
    """async_set_native_value must log warning on AttributeError/KeyError."""

    def test_attribute_error_in_setter_logs_warning(self):
        """AttributeError from async setter must log a warning and not propagate."""
        s = HeatingCircuitSetpointNumber.__new__(HeatingCircuitSetpointNumber)
        s._device = SimpleNamespace(
            name="HC-BadSetter",
            async_set_setpoint_temperature_eco=AsyncMock(
                side_effect=AttributeError("setter blocked")
            ),
        )
        s._getter_name = "setpoint_temperature_eco"
        s._setter_name = "setpoint_temperature_eco"
        s._range_attr = "eco_temperature_range"

        with patch("custom_components.bosch_shc.number.LOGGER") as mock_log:
            asyncio.run(s.async_set_native_value(20.0))

        mock_log.warning.assert_called_once()

    def test_key_error_in_setter_logs_warning(self):
        """KeyError from async setter must also log a warning."""
        s = HeatingCircuitSetpointNumber.__new__(HeatingCircuitSetpointNumber)
        s._device = SimpleNamespace(
            name="HC-KeyErr",
            async_set_setpoint_temperature_eco=AsyncMock(
                side_effect=KeyError("missing key")
            ),
        )
        s._getter_name = "setpoint_temperature_eco"
        s._setter_name = "setpoint_temperature_eco"
        s._range_attr = "eco_temperature_range"

        with patch("custom_components.bosch_shc.number.LOGGER") as mock_log:
            asyncio.run(s.async_set_native_value(20.0))

        mock_log.warning.assert_called_once()


class TestNumberSetupNewEntities:
    """Verify that the impulse-relay and heating-circuit entity loops in
    async_setup_entry work end to end."""

    def _run(self, session):
        hass = _make_hass_ne(session)
        entry = _make_config_entry_ne(session)
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


# ---------------------------------------------------------------------------
# BypassTimeoutNumber (hass#120 audit)
# ---------------------------------------------------------------------------


class TestBypassTimeoutNumberClassAttrs:
    def test_entity_category_is_config(self):
        num = BypassTimeoutNumber.__new__(BypassTimeoutNumber)
        assert num._attr_entity_category == EntityCategory.CONFIG

    def test_native_unit_is_minutes(self):
        """hass#120: confirmed via APK decompile (bypass_configuration.xml
        slider, app:quantityUnit="MINUTE") — not seconds as previously
        assumed (no OpenAPI spec exists for this service)."""
        num = BypassTimeoutNumber.__new__(BypassTimeoutNumber)
        assert num._attr_native_unit_of_measurement == UnitOfTime.MINUTES

    def test_native_min_is_1(self):
        num = BypassTimeoutNumber.__new__(BypassTimeoutNumber)
        assert num._attr_native_min_value == 1.0

    def test_native_max_is_15(self):
        num = BypassTimeoutNumber.__new__(BypassTimeoutNumber)
        assert num._attr_native_max_value == 15.0


class TestBypassTimeoutNativeValue:
    def test_native_value_reads_bypass_timeout(self):
        num = BypassTimeoutNumber.__new__(BypassTimeoutNumber)
        num._device = SimpleNamespace(bypass_timeout=7)
        assert num.native_value == pytest.approx(7.0)

    def test_native_value_none_when_attribute_missing(self):
        num = BypassTimeoutNumber.__new__(BypassTimeoutNumber)
        num._device = SimpleNamespace()
        assert num.native_value is None


class TestBypassTimeoutSetNativeValue:
    def test_set_value_writes_clamped_value(self):
        dev = SimpleNamespace(
            name="Shutter Contact",
            bypass_timeout=5,
            async_set_bypass_timeout=AsyncMock(),
        )
        num = BypassTimeoutNumber.__new__(BypassTimeoutNumber)
        num._device = dev
        asyncio.run(num.async_set_native_value(9.0))
        dev.async_set_bypass_timeout.assert_awaited_once_with(9)

    def test_set_value_clamps_to_max_15(self):
        dev = SimpleNamespace(
            name="Shutter Contact",
            bypass_timeout=5,
            async_set_bypass_timeout=AsyncMock(),
        )
        num = BypassTimeoutNumber.__new__(BypassTimeoutNumber)
        num._device = dev
        asyncio.run(num.async_set_native_value(999.0))
        dev.async_set_bypass_timeout.assert_awaited_once_with(15)

    def test_set_value_clamps_to_min_1(self):
        dev = SimpleNamespace(
            name="Shutter Contact",
            bypass_timeout=5,
            async_set_bypass_timeout=AsyncMock(),
        )
        num = BypassTimeoutNumber.__new__(BypassTimeoutNumber)
        num._device = dev
        asyncio.run(num.async_set_native_value(0.0))
        dev.async_set_bypass_timeout.assert_awaited_once_with(1)

    def test_set_value_shc_exception_raises_home_assistant_error(self):
        dev = SimpleNamespace(
            name="Shutter Contact",
            bypass_timeout=5,
            async_set_bypass_timeout=AsyncMock(side_effect=SHCException("rejected")),
        )
        num = BypassTimeoutNumber.__new__(BypassTimeoutNumber)
        num._device = dev
        with pytest.raises(HomeAssistantError) as exc_info:
            asyncio.run(num.async_set_native_value(9.0))
        assert exc_info.value.translation_key == "number_set_failed"


# ---------------------------------------------------------------------------
# PowerThresholdNumber (smart plug / smart plug compact)
# ---------------------------------------------------------------------------


class TestPowerThresholdNumberGuard:
    """Dual guard: created only when supports_energy_saving_mode AND
    power_threshold is not None."""

    def test_supports_false_value_present_skipped(self):
        plug = _fake_device(power_threshold=50.0,
                            supports_energy_saving_mode=False)
        entities = _setup(_make_session(smart_plugs=[plug]))
        assert "PowerThresholdNumber" not in _types(entities)

    def test_supports_true_value_none_skipped(self):
        plug = _fake_device(power_threshold=None,
                            supports_energy_saving_mode=True)
        entities = _setup(_make_session(smart_plugs=[plug]))
        assert "PowerThresholdNumber" not in _types(entities)

    def test_both_present_created(self):
        plug = _fake_device(power_threshold=100.0,
                            supports_energy_saving_mode=True)
        entities = _setup(_make_session(smart_plugs=[plug]))
        assert "PowerThresholdNumber" in _types(entities)

    def test_compact_supports_false_skipped(self):
        plug = _fake_device(power_threshold=50.0,
                            supports_energy_saving_mode=False)
        entities = _setup(_make_session(smart_plugs_compact=[plug]))
        assert "PowerThresholdNumber" not in _types(entities)

    def test_compact_value_none_skipped(self):
        plug = _fake_device(power_threshold=None,
                            supports_energy_saving_mode=True)
        entities = _setup(_make_session(smart_plugs_compact=[plug]))
        assert "PowerThresholdNumber" not in _types(entities)


class TestPowerThresholdNumber:
    def _make(self, **dev_kwargs):
        defaults = dict(root_device_id="root1", id="dev1",
                        power_threshold=50.0)
        defaults.update(dev_kwargs)
        dev = SimpleNamespace(**defaults)
        n = PowerThresholdNumber.__new__(PowerThresholdNumber)
        n._device = dev
        n._attr_unique_id = f"{dev.root_device_id}_{dev.id}_power_threshold"
        n._attr_name = "Energy Saving Power Threshold"
        return n

    def test_native_value_from_device(self):
        n = self._make(power_threshold=100.0)
        assert n.native_value == 100.0

    def test_native_value_none_when_not_set(self):
        dev = SimpleNamespace(root_device_id="r", id="d")
        n = PowerThresholdNumber.__new__(PowerThresholdNumber)
        n._device = dev
        assert n.native_value is None

    def test_set_native_value_writes_to_device(self):
        mock_setter = AsyncMock()
        dev = SimpleNamespace(root_device_id="r", id="d",
                              power_threshold=0.0,
                              async_set_power_threshold=mock_setter)
        n = PowerThresholdNumber.__new__(PowerThresholdNumber)
        n._device = dev
        asyncio.run(n.async_set_native_value(200.0))
        mock_setter.assert_awaited_once_with(200.0)

    def test_set_native_value_clamped_to_max(self):
        mock_setter = AsyncMock()
        dev = SimpleNamespace(root_device_id="r", id="d",
                              power_threshold=0.0,
                              async_set_power_threshold=mock_setter)
        n = PowerThresholdNumber.__new__(PowerThresholdNumber)
        n._device = dev
        asyncio.run(n.async_set_native_value(9999.0))
        assert mock_setter.call_args[0][0] <= 3680.0

    def test_set_native_value_clamped_to_min(self):
        mock_setter = AsyncMock()
        dev = SimpleNamespace(root_device_id="r", id="d",
                              power_threshold=0.0,
                              async_set_power_threshold=mock_setter)
        n = PowerThresholdNumber.__new__(PowerThresholdNumber)
        n._device = dev
        asyncio.run(n.async_set_native_value(-50.0))
        assert mock_setter.call_args[0][0] >= 0.0

    def test_unique_id_format(self):
        dev = SimpleNamespace(root_device_id="root1", id="dev1",
                              power_threshold=10.0)
        n = PowerThresholdNumber.__new__(PowerThresholdNumber)
        n._device = dev
        n._attr_unique_id = f"{dev.root_device_id}_{dev.id}_power_threshold"
        assert n._attr_unique_id == "root1_dev1_power_threshold"

    def test_entity_category_config(self):
        n = PowerThresholdNumber.__new__(PowerThresholdNumber)
        assert n._attr_entity_category == EntityCategory.CONFIG

    def test_device_class_power(self):
        from homeassistant.components.number import NumberDeviceClass as _NDC
        n = PowerThresholdNumber.__new__(PowerThresholdNumber)
        assert n._attr_device_class == _NDC.POWER

    def test_smartplug_power_threshold_created_when_attr_present(self):
        plug = _fake_device(power_threshold=100.0, supports_energy_saving_mode=True)
        session = _make_session(smart_plugs=[plug])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "PowerThresholdNumber" in types

    def test_smartplug_power_threshold_skipped_when_attr_absent(self):
        plug = _fake_device()  # no power_threshold
        session = _make_session(smart_plugs=[plug])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "PowerThresholdNumber" not in types


# ---------------------------------------------------------------------------
# EnterDurationNumber (smart plug / smart plug compact)
# ---------------------------------------------------------------------------


class TestEnterDurationNumberGuard:
    def test_supports_false_value_present_skipped(self):
        plug = _fake_device(enter_duration_seconds=60,
                            supports_energy_saving_mode=False)
        entities = _setup(_make_full_session(smart_plugs=[plug]))
        assert "EnterDurationNumber" not in _types(entities)

    def test_supports_true_value_none_skipped(self):
        plug = _fake_device(enter_duration_seconds=None,
                            supports_energy_saving_mode=True)
        entities = _setup(_make_full_session(smart_plugs=[plug]))
        assert "EnterDurationNumber" not in _types(entities)

    def test_both_present_created(self):
        plug = _fake_device(enter_duration_seconds=30,
                            supports_energy_saving_mode=True)
        entities = _setup(_make_full_session(smart_plugs=[plug]))
        assert "EnterDurationNumber" in _types(entities)


class TestEnterDurationNumber:
    def _make(self, enter_duration_seconds=30):
        dev = SimpleNamespace(root_device_id="root1", id="dev1",
                              enter_duration_seconds=enter_duration_seconds)
        n = EnterDurationNumber.__new__(EnterDurationNumber)
        n._device = dev
        n._attr_unique_id = (
            f"{dev.root_device_id}_{dev.id}_enter_duration_seconds"
        )
        return n

    def test_native_value_returns_float(self):
        n = self._make(enter_duration_seconds=60)
        assert n.native_value == 60.0

    def test_native_value_none_when_missing(self):
        dev = SimpleNamespace(root_device_id="r", id="d")
        n = EnterDurationNumber.__new__(EnterDurationNumber)
        n._device = dev
        assert n.native_value is None

    def test_set_native_value_converts_to_int(self):
        mock_setter = AsyncMock()
        dev = SimpleNamespace(root_device_id="r", id="d",
                              enter_duration_seconds=0,
                              async_set_enter_duration_seconds=mock_setter)
        n = EnterDurationNumber.__new__(EnterDurationNumber)
        n._device = dev
        asyncio.run(n.async_set_native_value(120.7))
        mock_setter.assert_awaited_once_with(120)  # int(clamped)

    def test_unique_id_format(self):
        n = self._make()
        assert n._attr_unique_id == "root1_dev1_enter_duration_seconds"

    def test_entity_category_config(self):
        n = EnterDurationNumber.__new__(EnterDurationNumber)
        assert n._attr_entity_category == EntityCategory.CONFIG

    def test_smartplugcompact_enter_duration_created_when_attr_present(self):
        plug = _fake_device(enter_duration_seconds=60, supports_energy_saving_mode=True)
        session = _make_session(smart_plugs_compact=[plug])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "EnterDurationNumber" in types

    def test_smartplugcompact_enter_duration_skipped_when_attr_absent(self):
        plug = _fake_device()
        session = _make_session(smart_plugs_compact=[plug])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "EnterDurationNumber" not in types


# ---------------------------------------------------------------------------
# LedBrightnessNumber (smart plug / smart plug compact)
# ---------------------------------------------------------------------------


class TestLedBrightnessNumberGuard:
    def test_supports_false_value_present_skipped(self):
        plug = _fake_device(led_brightness=50,
                            supports_led_brightness=False)
        entities = _setup(_make_full_session(smart_plugs=[plug]))
        assert "LedBrightnessNumber" not in _types(entities)

    def test_supports_true_value_none_skipped(self):
        plug = _fake_device(led_brightness=None,
                            supports_led_brightness=True)
        entities = _setup(_make_full_session(smart_plugs=[plug]))
        assert "LedBrightnessNumber" not in _types(entities)

    def test_both_present_created(self):
        plug = _fake_device(led_brightness=75,
                            supports_led_brightness=True)
        entities = _setup(_make_full_session(smart_plugs=[plug]))
        assert "LedBrightnessNumber" in _types(entities)


class TestLedBrightnessNumber:
    def _make(self, led_brightness=50, svc=None):
        dev = SimpleNamespace(root_device_id="root1", id="dev1",
                              led_brightness=led_brightness,
                              _led_brightness_configuration_service=svc)
        n = LedBrightnessNumber.__new__(LedBrightnessNumber)
        n._device = dev
        n._attr_unique_id = f"{dev.root_device_id}_{dev.id}_led_brightness"
        return n

    def test_native_value_from_device(self):
        n = self._make(led_brightness=75)
        assert n.native_value == 75

    def test_native_value_none_when_missing(self):
        dev = SimpleNamespace(root_device_id="r", id="d")
        n = LedBrightnessNumber.__new__(LedBrightnessNumber)
        n._device = dev
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
        dev = SimpleNamespace(root_device_id="r", id="d",
                              led_brightness=50,
                              _led_brightness_configuration_service=None,
                              async_set_led_brightness=mock_setter)
        n = LedBrightnessNumber.__new__(LedBrightnessNumber)
        n._device = dev
        asyncio.run(n.async_set_native_value(80))
        mock_setter.assert_awaited_once_with(80)

    def test_unique_id_format(self):
        n = self._make()
        assert n._attr_unique_id == "root1_dev1_led_brightness"

    def test_smartplug_led_brightness_created_when_attr_present(self):
        plug = _fake_device(led_brightness=50, supports_led_brightness=True)
        session = _make_session(smart_plugs=[plug])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "LedBrightnessNumber" in types

    def test_smartplug_led_brightness_skipped_when_attr_absent(self):
        plug = _fake_device()
        session = _make_session(smart_plugs=[plug])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "LedBrightnessNumber" not in types

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
# DisplayBrightnessNumber (ThermostatGen2 / RoomThermostat2)
# ---------------------------------------------------------------------------


class TestDisplayBrightnessNumberGuard:
    def test_supports_false_value_present_skipped(self):
        therm = _fake_device(display_brightness=50,
                             supports_display_configuration=False)
        entities = _setup(_make_full_session(thermostats=[therm]))
        assert "DisplayBrightnessNumber" not in _types(entities)

    def test_supports_true_value_none_skipped(self):
        therm = _fake_device(display_brightness=None,
                             supports_display_configuration=True)
        entities = _setup(_make_full_session(thermostats=[therm]))
        assert "DisplayBrightnessNumber" not in _types(entities)

    def test_both_present_created(self):
        therm = _fake_device(display_brightness=60,
                             supports_display_configuration=True)
        entities = _setup(_make_full_session(thermostats=[therm]))
        assert "DisplayBrightnessNumber" in _types(entities)

    def test_roomthermostat_value_none_skipped(self):
        rth = _fake_device(display_brightness=None,
                           supports_display_configuration=True)
        entities = _setup(_make_full_session(roomthermostats=[rth]))
        assert "DisplayBrightnessNumber" not in _types(entities)

    def test_roomthermostat_both_present_created(self):
        rth = _fake_device(display_brightness=40,
                           supports_display_configuration=True)
        entities = _setup(_make_full_session(roomthermostats=[rth]))
        assert "DisplayBrightnessNumber" in _types(entities)


class TestDisplayBrightnessNumber:
    def _make(self, display_brightness=50, svc=None):
        dev = SimpleNamespace(root_device_id="root1", id="dev1",
                              display_brightness=display_brightness,
                              _display_config_service=svc)
        n = DisplayBrightnessNumber.__new__(DisplayBrightnessNumber)
        n._device = dev
        n._attr_unique_id = f"{dev.root_device_id}_{dev.id}_display_brightness"
        return n

    def test_native_value_from_device(self):
        n = self._make(display_brightness=60)
        assert n.native_value == 60

    def test_native_value_none_when_missing(self):
        dev = SimpleNamespace(root_device_id="r", id="d")
        n = DisplayBrightnessNumber.__new__(DisplayBrightnessNumber)
        n._device = dev
        assert n.native_value is None

    def test_native_min_from_service(self):
        svc = SimpleNamespace(display_brightness_min=5, display_brightness_max=100,
                              display_brightness_step_size=5)
        n = self._make(svc=svc)
        assert n.native_min_value == 5.0

    def test_native_max_from_service(self):
        svc = SimpleNamespace(display_brightness_min=0, display_brightness_max=80,
                              display_brightness_step_size=1)
        n = self._make(svc=svc)
        assert n.native_max_value == 80.0

    def test_native_step_from_service(self):
        svc = SimpleNamespace(display_brightness_min=0, display_brightness_max=100,
                              display_brightness_step_size=10)
        n = self._make(svc=svc)
        assert n.native_step == 10.0

    def test_fallbacks_when_no_service(self):
        n = self._make(svc=None)
        assert n.native_min_value == 0.0
        assert n.native_max_value == 100.0
        assert n.native_step == 1.0

    def test_set_native_value_writes_to_device(self):
        mock_setter = AsyncMock()
        dev = SimpleNamespace(root_device_id="r", id="d",
                              display_brightness=50,
                              _display_config_service=None,
                              async_set_display_brightness=mock_setter)
        n = DisplayBrightnessNumber.__new__(DisplayBrightnessNumber)
        n._device = dev
        asyncio.run(n.async_set_native_value(70))
        mock_setter.assert_awaited_once_with(70)

    def test_unique_id_format(self):
        n = self._make()
        assert n._attr_unique_id == "root1_dev1_display_brightness"

    def test_entity_category_config(self):
        n = DisplayBrightnessNumber.__new__(DisplayBrightnessNumber)
        assert n._attr_entity_category == EntityCategory.CONFIG

    def test_thermostat_display_brightness_created_when_attr_present(self):
        therm = _fake_device(display_brightness=50, supports_display_configuration=True)
        session = _make_session(thermostats=[therm])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "DisplayBrightnessNumber" in types

    def test_thermostat_display_brightness_skipped_when_attr_absent(self):
        therm = _fake_device()
        session = _make_session(thermostats=[therm])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "DisplayBrightnessNumber" not in types

    def test_roomthermostat_display_brightness_created(self):
        rth = _fake_device(display_brightness=40, supports_display_configuration=True)
        session = _make_session(roomthermostats=[rth])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "DisplayBrightnessNumber" in types

    def test_min_none_falls_back(self):
        svc = SimpleNamespace(display_brightness_min=None, display_brightness_max=100,
                              display_brightness_step_size=1)
        n = self._make(svc=svc)
        assert n.native_min_value == 0.0

    def test_max_none_falls_back(self):
        svc = SimpleNamespace(display_brightness_min=0, display_brightness_max=None,
                              display_brightness_step_size=1)
        n = self._make(svc=svc)
        assert n.native_max_value == 100.0

    def test_step_none_falls_back(self):
        svc = SimpleNamespace(display_brightness_min=0, display_brightness_max=100,
                              display_brightness_step_size=None)
        n = self._make(svc=svc)
        assert n.native_step == 1.0


# ---------------------------------------------------------------------------
# DisplayOnTimeNumber (ThermostatGen2 / RoomThermostat2)
# ---------------------------------------------------------------------------


class TestDisplayOnTimeNumberGuard:
    def test_supports_false_value_present_skipped(self):
        therm = _fake_device(display_on_time=30,
                             supports_display_configuration=False)
        entities = _setup(_make_full_session(thermostats=[therm]))
        assert "DisplayOnTimeNumber" not in _types(entities)

    def test_supports_true_value_none_skipped(self):
        therm = _fake_device(display_on_time=None,
                             supports_display_configuration=True)
        entities = _setup(_make_full_session(thermostats=[therm]))
        assert "DisplayOnTimeNumber" not in _types(entities)

    def test_both_present_created(self):
        therm = _fake_device(display_on_time=60,
                             supports_display_configuration=True)
        entities = _setup(_make_full_session(thermostats=[therm]))
        assert "DisplayOnTimeNumber" in _types(entities)

    def test_roomthermostat_value_none_skipped(self):
        rth = _fake_device(display_on_time=None,
                           supports_display_configuration=True)
        entities = _setup(_make_full_session(roomthermostats=[rth]))
        assert "DisplayOnTimeNumber" not in _types(entities)


class TestDisplayOnTimeNumber:
    def _make(self, display_on_time=60, svc=None):
        dev = SimpleNamespace(root_device_id="root1", id="dev1",
                              display_on_time=display_on_time,
                              _display_config_service=svc)
        n = DisplayOnTimeNumber.__new__(DisplayOnTimeNumber)
        n._device = dev
        n._attr_unique_id = f"{dev.root_device_id}_{dev.id}_display_on_time"
        return n

    def test_native_value_from_device(self):
        n = self._make(display_on_time=120)
        assert n.native_value == 120.0

    def test_native_value_none_when_missing(self):
        dev = SimpleNamespace(root_device_id="r", id="d")
        n = DisplayOnTimeNumber.__new__(DisplayOnTimeNumber)
        n._device = dev
        assert n.native_value is None

    def test_native_min_from_service(self):
        svc = SimpleNamespace(display_on_time_min=5, display_on_time_max=3600,
                              display_on_time_step_size=1)
        n = self._make(svc=svc)
        assert n.native_min_value == 5.0

    def test_native_max_from_service(self):
        svc = SimpleNamespace(display_on_time_min=0, display_on_time_max=900,
                              display_on_time_step_size=30)
        n = self._make(svc=svc)
        assert n.native_max_value == 900.0

    def test_fallbacks_when_no_service(self):
        n = self._make(svc=None)
        assert n.native_min_value == 0.0
        assert n.native_max_value == 3600.0
        assert n.native_step == 1.0

    def test_set_native_value_writes_to_device(self):
        mock_setter = AsyncMock()
        dev = SimpleNamespace(root_device_id="r", id="d",
                              display_on_time=60,
                              _display_config_service=None,
                              async_set_display_on_time=mock_setter)
        n = DisplayOnTimeNumber.__new__(DisplayOnTimeNumber)
        n._device = dev
        asyncio.run(n.async_set_native_value(300))
        mock_setter.assert_awaited_once_with(300)

    def test_unique_id_format(self):
        n = self._make()
        assert n._attr_unique_id == "root1_dev1_display_on_time"

    def test_thermostat_display_on_time_created_when_attr_present(self):
        therm = _fake_device(display_on_time=30, supports_display_configuration=True)
        session = _make_session(thermostats=[therm])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "DisplayOnTimeNumber" in types

    def test_thermostat_display_on_time_skipped_when_attr_absent(self):
        therm = _fake_device()
        session = _make_session(thermostats=[therm])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "DisplayOnTimeNumber" not in types

    def test_min_none_falls_back(self):
        svc = SimpleNamespace(display_on_time_min=None, display_on_time_max=3600,
                              display_on_time_step_size=1)
        n = self._make(svc=svc)
        assert n.native_min_value == 0.0

    def test_max_none_falls_back(self):
        svc = SimpleNamespace(display_on_time_min=0, display_on_time_max=None,
                              display_on_time_step_size=1)
        n = self._make(svc=svc)
        assert n.native_max_value == 3600.0

    def test_step_none_falls_back(self):
        svc = SimpleNamespace(display_on_time_min=0, display_on_time_max=3600,
                              display_on_time_step_size=None)
        n = self._make(svc=svc)
        assert n.native_step == 1.0


class TestDisplayOnTimeNativeStep:
    """number.py line 556 — native_step returns float from service attribute."""

    def test_step_from_service(self):
        svc = SimpleNamespace(display_on_time_step_size=30)
        device = _fake_device_cg(_display_config_service=svc, display_on_time=60.0)
        num = DisplayOnTimeNumber.__new__(DisplayOnTimeNumber)
        num._device = device
        assert num.native_step == 30.0

    def test_step_fallback_when_no_service(self):
        device = _fake_device_cg(display_on_time=60.0)
        num = DisplayOnTimeNumber.__new__(DisplayOnTimeNumber)
        num._device = device
        assert num.native_step == 1.0

    def test_step_fallback_when_attr_none(self):
        svc = SimpleNamespace(display_on_time_step_size=None)
        device = _fake_device_cg(_display_config_service=svc, display_on_time=60.0)
        num = DisplayOnTimeNumber.__new__(DisplayOnTimeNumber)
        num._device = device
        assert num.native_step == 1.0


# ---------------------------------------------------------------------------
# SirenConfigNumber (hass#120)
# ---------------------------------------------------------------------------


def test_siren_config_number_reads_and_clamps():
    n = SirenConfigNumber(
        SimpleNamespace(root_device_id="r", id="d", name="Siren"),
        "entry",
        *_SIREN_ALARM_DELAY,
    )
    n._device = SimpleNamespace(siren=SimpleNamespace(alarm_delay=42))
    assert n.native_value == 42.0
    assert n._attr_native_min_value == 0.0
    assert n._attr_native_max_value == 180.0


def test_siren_duration_bounds_match_app_slider():
    """hass#120: alarmDuration/flashDuration are 1-15 minutes, confirmed via
    APK decompile of the real slider widgets (layout_outdoorsiren_alarm_signal
    _fragment.xml) — NOT 0-60 as previously assumed from the OpenAPI spec."""
    for cfg in (_SIREN_ALARM_DURATION, _SIREN_FLASH_DURATION):
        n = SirenConfigNumber(
            SimpleNamespace(root_device_id="r", id="d", name="Siren"),
            "entry",
            *cfg,
        )
        assert n._attr_native_min_value == 1.0
        assert n._attr_native_max_value == 15.0


class TestNumberSirenSetup:
    """Siren config numbers + dimmer config numbers created in setup."""

    def _run_number_setup(self, sirens=None, dimmers=None, options=None):
        dh = MagicMock()
        dh.thermostats = []
        dh.roomthermostats = []
        dh.wallthermostats = []
        dh.micromodule_impulse_relays = []
        dh.heating_circuits = []

        session = MagicMock()
        session.device_helper = dh

        hass = _fake_hass(session=session)
        entry = _fake_entry(hass=hass, options=options or {})

        collected = []
        _run(async_setup_entry(hass, entry, lambda ents, **kw: collected.extend(ents)))
        return collected

    def test_siren_config_numbers_created_when_siren_service_present(self):
        """siren with siren service → SirenConfigNumber entities."""

        siren = _fake_dev("s1", siren=MagicMock())

        dh = MagicMock()
        dh.thermostats = []
        dh.roomthermostats = []
        dh.wallthermostats = []
        dh.micromodule_impulse_relays = []
        dh.heating_circuits = []

        session = MagicMock()
        session.device_helper = dh

        hass = _fake_hass(session=session)
        entry = _fake_entry(hass=hass)

        # Patch the dh to return our siren
        with patch.object(dh, "outdoor_sirens", [siren], create=True):
            collected = []
            _run(async_setup_entry(hass, entry, lambda ents, **kw: collected.extend(ents)))

        types = [type(e).__name__ for e in collected]
        assert types.count("SirenConfigNumber") >= 4  # alarm/flash duration+delay

    def test_siren_excluded_skipped_in_number_setup(self):
        """device_excluded → continue (no SirenConfigNumber added)."""

        siren_excl = _fake_dev("siren_excl", siren=MagicMock())

        dh = MagicMock()
        dh.thermostats = []
        dh.roomthermostats = []
        dh.wallthermostats = []
        dh.micromodule_impulse_relays = []
        dh.heating_circuits = []

        session = MagicMock()
        session.device_helper = dh

        hass = _fake_hass(session=session)
        entry = _fake_entry(hass=hass, options={OPT_EXCLUDED_DEVICES: ["siren_excl"]})

        with patch.object(dh, "outdoor_sirens", [siren_excl], create=True):
            collected = []
            _run(async_setup_entry(hass, entry, lambda ents, **kw: collected.extend(ents)))

        types = [type(e).__name__ for e in collected]
        assert "SirenConfigNumber" not in types

    def test_siren_without_siren_service_skipped_in_number_setup(self):
        """siren with siren=None → continue (no SirenConfigNumber added)."""

        siren_no_svc = _fake_dev("siren_no_svc", siren=None)

        dh = MagicMock()
        dh.thermostats = []
        dh.roomthermostats = []
        dh.wallthermostats = []
        dh.micromodule_impulse_relays = []
        dh.heating_circuits = []

        session = MagicMock()
        session.device_helper = dh

        hass = _fake_hass(session=session)
        entry = _fake_entry(hass=hass)

        with patch.object(dh, "outdoor_sirens", [siren_no_svc], create=True):
            collected = []
            _run(async_setup_entry(hass, entry, lambda ents, **kw: collected.extend(ents)))

        types = [type(e).__name__ for e in collected]
        assert "SirenConfigNumber" not in types

    def test_dimmer_excluded_skipped_in_number_setup(self):
        """device_excluded → continue (no DimmerConfigNumber added)."""

        dev_excl = _fake_dev("dim_excl", supports_dimmer_configuration=True)

        dh = MagicMock()
        dh.thermostats = []
        dh.roomthermostats = []
        dh.wallthermostats = []
        dh.micromodule_impulse_relays = []
        dh.heating_circuits = []

        session = MagicMock()
        session.device_helper = dh

        hass = _fake_hass(session=session)
        entry = _fake_entry(hass=hass, options={OPT_EXCLUDED_DEVICES: ["dim_excl"]})

        with patch.object(dh, "micromodule_dimmers", [dev_excl], create=True):
            collected = []
            _run(async_setup_entry(hass, entry, lambda ents, **kw: collected.extend(ents)))

        types = [type(e).__name__ for e in collected]
        assert "DimmerConfigNumber" not in types

    def test_dimmer_config_numbers_created_when_supports_dimmer(self):
        """dimmer with supports_dimmer_configuration → DimmerConfigNumber."""

        dev = _fake_dev("dim1", supports_dimmer_configuration=True)

        dh = MagicMock()
        dh.thermostats = []
        dh.roomthermostats = []
        dh.wallthermostats = []
        dh.micromodule_impulse_relays = []
        dh.heating_circuits = []

        session = MagicMock()
        session.device_helper = dh

        hass = _fake_hass(session=session)
        entry = _fake_entry(hass=hass)

        with patch.object(dh, "micromodule_dimmers", [dev], create=True):
            collected = []
            _run(async_setup_entry(hass, entry, lambda ents, **kw: collected.extend(ents)))

        types = [type(e).__name__ for e in collected]
        assert types.count("DimmerConfigNumber") >= 3  # min/max/speed


# ---------------------------------------------------------------------------
# DimmerConfigNumber (#123)
# ---------------------------------------------------------------------------


def test_dimmer_number_min_reads_correctly():
    n = DimmerConfigNumber(_FAKE_DEVICE, "e1", "min", 0, 100)
    n._device = SimpleNamespace(dimmer_configuration=_dimmer_svc(min_b=15))
    assert n.native_value == 15.0


def test_dimmer_number_max_reads_correctly():
    n = DimmerConfigNumber(_FAKE_DEVICE, "e1", "max", 0, 100)
    n._device = SimpleNamespace(dimmer_configuration=_dimmer_svc(max_b=85))
    assert n.native_value == 85.0


def test_dimmer_number_speed_reads_correctly():
    n = DimmerConfigNumber(_FAKE_DEVICE, "e1", "speed", 1, 10)
    n._device = SimpleNamespace(dimmer_configuration=_dimmer_svc(speed=7))
    assert n.native_value == 7.0


def test_dimmer_number_returns_none_when_no_service():
    n = DimmerConfigNumber(_FAKE_DEVICE, "e1", "min", 0, 100)
    n._device = SimpleNamespace(dimmer_configuration=None)
    assert n.native_value is None


def test_dimmer_number_min_set_calls_set_brightness_range():
    n = DimmerConfigNumber(_FAKE_DEVICE, "e1", "min", 0, 100)
    svc = _dimmer_svc(min_b=10, max_b=90)
    n._device = SimpleNamespace(dimmer_configuration=svc)
    asyncio.run(n.async_set_native_value(25.0))
    svc.async_set_brightness_range.assert_awaited_once_with(min_brightness=25)


def test_dimmer_number_max_set_calls_set_brightness_range():
    n = DimmerConfigNumber(_FAKE_DEVICE, "e1", "max", 0, 100)
    svc = _dimmer_svc(min_b=10, max_b=90)
    n._device = SimpleNamespace(dimmer_configuration=svc)
    asyncio.run(n.async_set_native_value(80.0))
    svc.async_set_brightness_range.assert_awaited_once_with(max_brightness=80)


def test_dimmer_number_speed_set_calls_set_dimming_speed():
    n = DimmerConfigNumber(_FAKE_DEVICE, "e1", "speed", 1, 10)
    svc = _dimmer_svc(speed=5)
    n._device = SimpleNamespace(dimmer_configuration=svc)
    asyncio.run(n.async_set_native_value(3.0))
    svc.async_set_dimming_speed.assert_awaited_once_with(3)


def test_dimmer_number_clamps_to_range():
    n = DimmerConfigNumber(_FAKE_DEVICE, "e1", "speed", 1, 10)
    svc = _dimmer_svc()
    n._device = SimpleNamespace(dimmer_configuration=svc)
    asyncio.run(n.async_set_native_value(99.0))  # above max 10
    svc.async_set_dimming_speed.assert_awaited_once_with(10)


def test_dimmer_number_set_no_service_is_safe():
    n = DimmerConfigNumber(_FAKE_DEVICE, "e1", "min", 0, 100)
    n._device = SimpleNamespace(dimmer_configuration=None)
    # must not raise
    asyncio.run(n.async_set_native_value(50.0))


def test_dimmer_number_inverted_range_value_error_caught_not_raised():
    """Regression: async_set_brightness_range() (boschshcpy) raises
    ValueError on an inverted min/max range — DimmerConfigNumber must catch
    it and log a warning, not let it propagate and crash the service call."""
    n = DimmerConfigNumber(_FAKE_DEVICE, "e1", "min", 0, 100)
    svc = _dimmer_svc(min_b=10, max_b=90)
    svc.async_set_brightness_range = AsyncMock(
        side_effect=ValueError("Invalid brightness range: minBrightness (95) must be less than maxBrightness (90)")
    )
    n._device = SimpleNamespace(dimmer_configuration=svc, name="Büro Dimmer")
    # must not raise
    asyncio.run(n.async_set_native_value(95.0))
    svc.async_set_brightness_range.assert_awaited_once_with(min_brightness=95)


def test_dimmer_number_has_correct_names():
    n_min = DimmerConfigNumber(_FAKE_DEVICE, "e1", "min", 0, 100)
    n_max = DimmerConfigNumber(_FAKE_DEVICE, "e1", "max", 0, 100)
    n_spd = DimmerConfigNumber(_FAKE_DEVICE, "e1", "speed", 1, 10)
    assert n_min._attr_name == "Dimmer Min Brightness"
    assert n_max._attr_name == "Dimmer Max Brightness"
    assert n_spd._attr_name == "Dimming Speed"


def test_dimmer_number_unique_ids():
    n = DimmerConfigNumber(_FAKE_DEVICE, "e1", "min", 0, 100)
    assert n.unique_id == "root-1_hdm:ZigBee:dimmer1_dimmer_min"


# ---------------------------------------------------------------------------
# Cross-entity async_set_native_value client-error paths
# ---------------------------------------------------------------------------


class TestNumberErrorPaths:
    """aiohttp.ClientError from the underlying setter must be caught (logged),
    not propagate, across every number entity type."""

    def test_siren_config_number_async_set_client_error(self):
        """SirenConfigNumber.async_set_native_value error path."""
        num = SirenConfigNumber.__new__(SirenConfigNumber)
        siren_svc = MagicMock()
        siren_svc.async_set_configuration = AsyncMock(
            side_effect=aiohttp.ClientError("err")
        )
        num._device = SimpleNamespace(siren=siren_svc, name="Siren")
        num._field = "alarm_duration_seconds"
        num._attr_native_min_value = 0
        num._attr_native_max_value = 3600
        _run(num.async_set_native_value(30.0))  # must not raise

    def test_shcnumber_async_set_client_error(self):
        """SHCNumber.async_set_native_value error path."""
        num = SHCNumber.__new__(SHCNumber)
        # SHCNumber.native_min_value = self._device.min_offset
        # SHCNumber.native_max_value = self._device.max_offset
        num._device = SimpleNamespace(
            name="Thermostat",
            min_offset=-5.0,
            max_offset=5.0,
            async_set_offset=AsyncMock(side_effect=aiohttp.ClientError("err")),
        )
        _run(num.async_set_native_value(1.0))  # must not raise

    def test_impulse_length_number_async_set_client_error(self):
        """ImpulseLengthNumber.async_set_native_value error path."""
        num = ImpulseLengthNumber.__new__(ImpulseLengthNumber)
        num._device = SimpleNamespace(
            name="Relay",
            async_set_impulse_length=AsyncMock(
                side_effect=aiohttp.ClientError("err")
            ),
        )
        num._attr_native_min_value = 0.1
        num._attr_native_max_value = 10.0
        _run(num.async_set_native_value(1.0))  # must not raise

    def test_power_threshold_number_async_set_client_error(self):
        """PowerThresholdNumber.async_set_native_value error path."""
        num = PowerThresholdNumber.__new__(PowerThresholdNumber)
        num._device = SimpleNamespace(
            name="SmartPlug",
            async_set_power_threshold=AsyncMock(
                side_effect=aiohttp.ClientError("err")
            ),
        )
        num._attr_native_min_value = 0.0
        num._attr_native_max_value = 3680.0
        _run(num.async_set_native_value(50.0))  # must not raise

    def test_enter_duration_number_async_set_client_error(self):
        """EnterDurationNumber.async_set_native_value error path."""
        num = EnterDurationNumber.__new__(EnterDurationNumber)
        num._device = SimpleNamespace(
            name="SmartPlug",
            async_set_enter_duration_seconds=AsyncMock(
                side_effect=aiohttp.ClientError("err")
            ),
        )
        num._attr_native_min_value = 1.0
        num._attr_native_max_value = 3600.0
        _run(num.async_set_native_value(60.0))  # must not raise

    def test_led_brightness_number_async_set_client_error(self):
        """LedBrightnessNumber.async_set_native_value error path."""
        num = LedBrightnessNumber.__new__(LedBrightnessNumber)
        num._device = SimpleNamespace(
            name="SmartPlug",
            async_set_led_brightness=AsyncMock(
                side_effect=aiohttp.ClientError("err")
            ),
        )
        _run(num.async_set_native_value(50.0))  # must not raise

    def test_display_brightness_number_async_set_client_error(self):
        """DisplayBrightnessNumber.async_set_native_value error path."""
        num = DisplayBrightnessNumber.__new__(DisplayBrightnessNumber)
        num._device = SimpleNamespace(
            name="Thermostat",
            async_set_display_brightness=AsyncMock(
                side_effect=aiohttp.ClientError("err")
            ),
        )
        _run(num.async_set_native_value(80.0))  # must not raise

    def test_display_on_time_number_async_set_client_error(self):
        """DisplayOnTimeNumber.async_set_native_value error path."""
        num = DisplayOnTimeNumber.__new__(DisplayOnTimeNumber)
        num._device = SimpleNamespace(
            name="Thermostat",
            async_set_display_on_time=AsyncMock(
                side_effect=aiohttp.ClientError("err")
            ),
        )
        _run(num.async_set_native_value(10.0))  # must not raise

    def test_dimmer_config_number_async_set_client_error(self):
        """DimmerConfigNumber.async_set_native_value error path."""
        svc = MagicMock()
        svc.async_set_brightness_range = AsyncMock(
            side_effect=aiohttp.ClientError("err")
        )
        num = DimmerConfigNumber.__new__(DimmerConfigNumber)
        num._field = "min"
        num._device = SimpleNamespace(dimmer_configuration=svc, name="Dimmer")
        num._attr_native_min_value = 0.0
        num._attr_native_max_value = 100.0
        _run(num.async_set_native_value(10.0))  # must not raise
