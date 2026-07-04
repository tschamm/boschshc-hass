"""Coverage test for valve.py line 34 — device_excluded continue branch.

Line 34: `if device_excluded(valve, config_entry.options): continue`

The existing test_valve.py and test_valve_unit.py test SHCValve in isolation
but never call async_setup_entry with an excluded thermostat.  This file
exercises that branch so line 34 is hit.

Pattern: fake hass + config_entry + session; asyncio.run(async_setup_entry).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from custom_components.bosch_shc.const import (
    OPT_EXCLUDED_DEVICES,
    OPT_EXCLUDED_ROOMS,
)
from custom_components.bosch_shc.valve import SHCValve, async_setup_entry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_thermostat(dev_id="thermo-001", room_id=None, position=50, root_id="root-thermo"):
    """Minimal thermostat double compatible with device_excluded() and SHCValve."""
    return SimpleNamespace(
        name="Thermostat 1",
        id=dev_id,
        root_device_id=root_id,
        room_id=room_id,
        manufacturer="Bosch",
        device_model="TRV",
        status="AVAILABLE",
        device_services=[],
        deleted=False,
        position=position,
    )


def _make_entry(options=None, entry_id="E1", session=None):
    entry = SimpleNamespace(options=options or {}, entry_id=entry_id)
    entry.runtime_data = SimpleNamespace(session=session)
    return entry


def _run_setup(session, entry):
    entry.runtime_data.session = session
    hass = SimpleNamespace()
    collected = []

    def add(entities):
        collected.extend(entities)

    asyncio.run(async_setup_entry(hass, entry, add))
    return collected


def _session(thermostats):
    return SimpleNamespace(
        device_helper=SimpleNamespace(thermostats=thermostats)
    )


# ---------------------------------------------------------------------------
# Line 34 — excluded thermostat is skipped
# ---------------------------------------------------------------------------

class TestValveSetupEntryExcluded:
    def test_excluded_device_not_added(self):
        """Thermostat in OPT_EXCLUDED_DEVICES → continue → not in entities."""
        thermo = _fake_thermostat(dev_id="thermo-excl")
        session = _session([thermo])
        entry = _make_entry(options={OPT_EXCLUDED_DEVICES: ["thermo-excl"]})
        result = _run_setup(session, entry)
        assert result == []

    def test_non_excluded_device_is_added(self):
        """Sanity: thermostat NOT excluded → SHCValve entity is created."""
        thermo = _fake_thermostat(dev_id="thermo-ok")
        session = _session([thermo])
        entry = _make_entry()
        result = _run_setup(session, entry)
        assert len(result) == 1
        assert isinstance(result[0], SHCValve)

    def test_mixed_one_excluded_one_not(self):
        """One excluded, one not → only the non-excluded ends up in entities."""
        excl = _fake_thermostat(dev_id="thermo-excl")
        ok = _fake_thermostat(dev_id="thermo-ok")
        session = _session([excl, ok])
        entry = _make_entry(options={OPT_EXCLUDED_DEVICES: ["thermo-excl"]})
        result = _run_setup(session, entry)
        assert len(result) == 1
        assert result[0]._device is ok

    def test_excluded_by_room_not_added(self):
        """Room-level exclusion also hits the continue on line 34."""
        thermo = _fake_thermostat(dev_id="thermo-room", room_id="room-99")
        session = _session([thermo])
        entry = _make_entry(options={OPT_EXCLUDED_ROOMS: ["room-99"]})
        result = _run_setup(session, entry)
        assert result == []

    def test_all_excluded_yields_empty(self):
        """All thermostats excluded → async_add_entities never called."""
        t1 = _fake_thermostat(dev_id="t1")
        t2 = _fake_thermostat(dev_id="t2")
        session = _session([t1, t2])
        entry = _make_entry(options={OPT_EXCLUDED_DEVICES: ["t1", "t2"]})
        result = _run_setup(session, entry)
        assert result == []

    def test_no_thermostats_yields_empty(self):
        """No thermostats at all → empty result (async_add_entities not called)."""
        session = _session([])
        entry = _make_entry()
        result = _run_setup(session, entry)
        assert result == []

    def test_valve_entry_id_set_correctly(self):
        """The SHCValve entity's _entry_id matches the config entry's entry_id."""
        thermo = _fake_thermostat(dev_id="thermo-id-check")
        session = _session([thermo])
        entry = _make_entry(entry_id="myentry")
        result = _run_setup(session, entry)
        assert result[0]._entry_id == "myentry"

    def test_valve_attr_name_is_valve(self):
        """async_setup_entry passes attr_name='Valve' to SHCValve."""
        thermo = _fake_thermostat(dev_id="thermo-name")
        session = _session([thermo])
        entry = _make_entry()
        result = _run_setup(session, entry)
        assert result[0]._attr_name == "Valve"

    def test_valve_unique_id_includes_valve_suffix(self):
        thermo = _fake_thermostat(dev_id="thermo-uid", root_id="root-uid")
        session = _session([thermo])
        entry = _make_entry()
        result = _run_setup(session, entry)
        assert result[0]._attr_unique_id == "root-uid_thermo-uid_valve"
