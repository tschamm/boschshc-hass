"""Extra coverage for climate.py.

Targets:
- line 30: device_excluded in climate_controls loop
- line 42: device_excluded in heating_circuits loop
- line 233: async_set_temperature switches to MANUAL when operation_mode==AUTOMATIC
            and ATTR_HVAC_MODE is not in kwargs
"""

import asyncio
from types import SimpleNamespace

from boschshcpy import SHCClimateControl

from homeassistant.components.climate.const import ATTR_HVAC_MODE
from homeassistant.const import ATTR_TEMPERATURE

from custom_components.bosch_shc.climate import (
    ClimateControl,
    async_setup_entry,
)
from custom_components.bosch_shc.const import (
    DATA_SESSION,
    DOMAIN,
    OPT_EXCLUDED_DEVICES,
)

# Shorthand for AUTOMATIC enum value
_AUTO = SHCClimateControl.RoomClimateControlService.OperationMode.AUTOMATIC
_MANUAL = SHCClimateControl.RoomClimateControlService.OperationMode.MANUAL


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_climate_device(device_id="clim-1", room_id="room-1"):
    """Device double for a climate control."""
    return SimpleNamespace(
        id=device_id,
        root_device_id="shc-root",
        room_id=room_id,
        name="Room Climate",
        serial=f"serial-{device_id}",
        manufacturer="Bosch",
        device_model="ClimateModel",
        status="AVAILABLE",
        deleted=False,
        device_services=[],
        subscribe_callback=lambda eid, cb: None,
        unsubscribe_callback=lambda eid: None,
        temperature=20.0,
        setpoint_temperature=21.0,
        summer_mode=False,
        supports_cooling=False,
        cooling_mode=False,
        operation_mode=_AUTO,
        supports_boost_mode=False,
        boost_mode=False,
        low=False,
        has_demand=False,
    )


def _make_heating_circuit_device(device_id="hc-1", room_id="room-2"):
    """Device double for a heating circuit."""
    return SimpleNamespace(
        id=device_id,
        root_device_id="shc-root",
        room_id=room_id,
        name="Heating Circuit",
        serial=f"serial-{device_id}",
        manufacturer="Bosch",
        device_model="HCModel",
        status="AVAILABLE",
        deleted=False,
        device_services=[],
        subscribe_callback=lambda eid, cb: None,
        unsubscribe_callback=lambda eid: None,
    )


def _make_hass_and_entry(
    climates=None,
    heating_circuits=None,
    excluded_device_ids=None,
):
    """Return (hass, config_entry) with a faked session."""
    climates = climates or []
    heating_circuits = heating_circuits or []
    excluded = excluded_device_ids or []

    session = SimpleNamespace(
        device_helper=SimpleNamespace(
            climate_controls=climates,
            heating_circuits=heating_circuits,
        ),
        room=lambda room_id: SimpleNamespace(name=f"Room-{room_id}"),
    )

    entry_id = "entry-clim"
    options = {OPT_EXCLUDED_DEVICES: excluded}
    hass = SimpleNamespace(
        data={DOMAIN: {entry_id: {DATA_SESSION: session}}},
    )
    config_entry = SimpleNamespace(entry_id=entry_id, options=options)
    return hass, config_entry


def _run_setup(hass, config_entry):
    added = []

    def _add(entities):
        added.extend(entities)

    asyncio.run(async_setup_entry(hass, config_entry, _add))
    return added


# ---------------------------------------------------------------------------
# Tests for line 30: device_excluded in climate_controls loop
# ---------------------------------------------------------------------------


class TestClimateSetupExcluded:
    def test_excluded_climate_control_not_added(self):
        """Excluded climate device (line 30) must not appear in entities."""
        dev = _make_climate_device(device_id="excl-clim")
        hass, entry = _make_hass_and_entry(
            climates=[dev],
            excluded_device_ids=["excl-clim"],
        )
        added = _run_setup(hass, entry)
        assert all(
            getattr(e, "_device", None) is not dev for e in added
        ), "Excluded climate device should not be added"

    def test_non_excluded_climate_control_is_added(self):
        """Non-excluded climate device must appear in entities."""
        dev = _make_climate_device(device_id="keep-clim")
        hass, entry = _make_hass_and_entry(
            climates=[dev],
            excluded_device_ids=[],
        )
        added = _run_setup(hass, entry)
        assert any(
            getattr(e, "_device", None) is dev for e in added
        ), "Non-excluded climate device should be added"

    def test_mixed_climates_only_excluded_is_skipped(self):
        """When one of two climates is excluded, only the non-excluded one is added."""
        keep = _make_climate_device(device_id="clim-keep")
        excl = _make_climate_device(device_id="clim-excl")
        hass, entry = _make_hass_and_entry(
            climates=[keep, excl],
            excluded_device_ids=["clim-excl"],
        )
        added = _run_setup(hass, entry)
        device_ids = [getattr(e, "_device", SimpleNamespace()).id for e in added]
        assert "clim-keep" in device_ids
        assert "clim-excl" not in device_ids


# ---------------------------------------------------------------------------
# Tests for line 42: device_excluded in heating_circuits loop
# ---------------------------------------------------------------------------


class TestHeatingCircuitSetupExcluded:
    def test_excluded_heating_circuit_not_added(self):
        """Excluded heating circuit (line 42) must not appear in entities."""
        dev = _make_heating_circuit_device(device_id="excl-hc")
        hass, entry = _make_hass_and_entry(
            heating_circuits=[dev],
            excluded_device_ids=["excl-hc"],
        )
        added = _run_setup(hass, entry)
        assert all(
            getattr(e, "_device", None) is not dev for e in added
        ), "Excluded heating circuit should not be added"

    def test_non_excluded_heating_circuit_is_added(self):
        """Non-excluded heating circuit must appear in entities."""
        dev = _make_heating_circuit_device(device_id="keep-hc")
        hass, entry = _make_hass_and_entry(
            heating_circuits=[dev],
            excluded_device_ids=[],
        )
        added = _run_setup(hass, entry)
        assert any(
            getattr(e, "_device", None) is dev for e in added
        ), "Non-excluded heating circuit should be added"


# ---------------------------------------------------------------------------
# Tests for line 233: set_temperature switches to MANUAL first when AUTOMATIC
# ---------------------------------------------------------------------------


class TestClimateSetTemperatureManualSwitch:
    """Tests for the AUTOMATIC→MANUAL branch in async_set_temperature (line 233)."""

    def _make_entity(self, operation_mode=_AUTO):
        """Build a ClimateControl bypassing __init__ via __new__."""
        ent = ClimateControl.__new__(ClimateControl)
        ent._device = SimpleNamespace(
            id="clim-ent",
            root_device_id="shc-root",
            name="Climate",
            manufacturer="Bosch",
            device_model="CC",
            status="AVAILABLE",
            deleted=False,
            device_services=[],
            subscribe_callback=lambda eid, cb: None,
            unsubscribe_callback=lambda eid: None,
            temperature=20.0,
            setpoint_temperature=21.0,
            summer_mode=False,
            supports_cooling=False,
            cooling_mode=False,
            operation_mode=operation_mode,
            supports_boost_mode=False,
            boost_mode=False,
            low=False,
            has_demand=False,
        )
        ent._entry_id = "entry-test"
        ent._attr_name = "Room Climate Test"
        ent._room_label = "Room Climate Test"
        ent._attr_unique_id = "shc-root_clim-ent"
        ent._attr_target_temperature_step = 0.5
        ent._enable_turn_on_off_backwards_compatibility = False
        return ent

    def _make_fake_hass(self):
        """Return a fake hass whose async_add_executor_job records calls."""
        executor_calls = []

        async def _executor_job(fn, *args):
            executor_calls.append((fn, args))
            # Actually execute the call so setattr writes go through
            fn(*args)

        hass = SimpleNamespace(
            async_add_executor_job=_executor_job,
        )
        return hass, executor_calls

    def test_set_temperature_auto_mode_switches_to_manual_first(self):
        """Line 233: when operation_mode==AUTOMATIC and no ATTR_HVAC_MODE in kwargs,
        async_set_temperature must issue a setattr(device, 'operation_mode', MANUAL)
        executor job BEFORE the setpoint write."""
        ent = self._make_entity(operation_mode=_AUTO)
        hass, executor_calls = self._make_fake_hass()
        ent.hass = hass

        asyncio.run(ent.async_set_temperature(**{ATTR_TEMPERATURE: 22.0}))

        # First executor call must set operation_mode to MANUAL
        assert len(executor_calls) >= 2, (
            f"Expected ≥2 executor jobs (mode switch + setpoint), got {executor_calls}"
        )
        first_fn, first_args = executor_calls[0]
        assert first_fn is setattr, "First executor job must be setattr"
        assert first_args[0] is ent._device, "setattr target must be the device"
        assert first_args[1] == "operation_mode", (
            f"Expected 'operation_mode', got '{first_args[1]}'"
        )
        assert first_args[2] == _MANUAL, (
            f"Expected MANUAL operation mode, got {first_args[2]}"
        )

    def test_set_temperature_auto_mode_then_writes_setpoint(self):
        """After switching to MANUAL, the setpoint must be written."""
        ent = self._make_entity(operation_mode=_AUTO)
        hass, executor_calls = self._make_fake_hass()
        ent.hass = hass

        asyncio.run(ent.async_set_temperature(**{ATTR_TEMPERATURE: 22.0}))

        setpoint_calls = [
            (fn, args) for fn, args in executor_calls
            if fn is setattr and len(args) >= 2 and args[1] == "setpoint_temperature"
        ]
        assert setpoint_calls, "Expected a setpoint_temperature setattr executor job"
        _, args = setpoint_calls[0]
        assert args[2] == 22.0, f"Expected setpoint 22.0, got {args[2]}"

    def test_set_temperature_with_explicit_hvac_mode_skips_manual_switch(self):
        """When ATTR_HVAC_MODE is explicitly provided, the MANUAL switch must NOT happen.
        This ensures line 229's guard (kwargs.get(ATTR_HVAC_MODE) is None) works."""
        from homeassistant.components.climate.const import HVACMode
        ent = self._make_entity(operation_mode=_AUTO)
        hass, executor_calls = self._make_fake_hass()
        ent.hass = hass

        asyncio.run(ent.async_set_temperature(
            **{ATTR_TEMPERATURE: 22.0, ATTR_HVAC_MODE: HVACMode.AUTO}
        ))

        mode_switch_calls = [
            (fn, args) for fn, args in executor_calls
            if fn is setattr and len(args) >= 2 and args[1] == "operation_mode"
            and args[2] == _MANUAL
        ]
        assert not mode_switch_calls, (
            "With explicit ATTR_HVAC_MODE, should NOT switch to MANUAL; "
            f"got: {mode_switch_calls}"
        )

    def test_set_temperature_manual_mode_skips_manual_switch(self):
        """When operation_mode is already MANUAL, no extra mode switch executor job."""
        ent = self._make_entity(
            operation_mode=_MANUAL,
        )
        hass, executor_calls = self._make_fake_hass()
        ent.hass = hass

        asyncio.run(ent.async_set_temperature(**{ATTR_TEMPERATURE: 21.0}))

        mode_switch_calls = [
            (fn, args) for fn, args in executor_calls
            if fn is setattr and len(args) >= 2 and args[1] == "operation_mode"
        ]
        assert not mode_switch_calls, (
            "When already MANUAL, should NOT issue an operation_mode switch; "
            f"got: {mode_switch_calls}"
        )
