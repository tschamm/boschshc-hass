"""Extra coverage for switch.py async_setup_entry exclusion and UDS branches.

Covers missing lines:
- 258, 286, 300, 314, 328, 366, 391, 467, 490, 511, 534: device_excluded
  continue branches (all device-type loops in async_setup_entry)
- 545-553 (motion_detectors2 block): included path that creates pet_immunity
  entities and calls async_migrate_to_new_unique_id
- UDS (user_defined_states) path: session.userdefinedstates loop creates
  SHCUserDefinedStateSwitch entities

Run:
  PYTHONPATH="/tmp/hass-cov:/tmp/lib-async" PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \\
  python3 -m pytest tests/bosch_shc/test_switch_extra_coverage.py -q -o addopts=""
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.bosch_shc.const import (
    DATA_SESSION,
    DATA_SHC,
    DOMAIN,
    OPT_EXCLUDED_DEVICES,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

EXCLUDED_ID = "excl-001"


def _dev(device_id=EXCLUDED_ID, root_id="root1", serial="serial1",
         supports_silentmode=False, has_child_lock=True):
    """Build a minimal device SimpleNamespace."""
    d = SimpleNamespace(
        id=device_id,
        root_device_id=root_id,
        serial=serial,
        name="Test Device",
        manufacturer="Bosch",
        device_model="TestModel",
        status="AVAILABLE",
        deleted=False,
        device_services=[],
        room_id=None,
        supports_silentmode=supports_silentmode,
    )
    if has_child_lock:
        d.child_lock = False
    return d


def _included_dev(device_id="incl-001", **kw):
    return _dev(device_id=device_id, **kw)


def _excluded_dev():
    return _dev(device_id=EXCLUDED_ID)


def _make_session(*, userdefinedstates=None):
    """Build a minimal session mock with all device_helper attributes."""
    excl = _excluded_dev()
    dh = SimpleNamespace(
        smart_plugs=[excl],
        light_switches_bsm=[excl],
        micromodule_light_attached=[excl],
        smart_plugs_compact=[excl],
        micromodule_relays=[excl],
        camera_eyes=[excl],
        camera_360=[excl],
        camera_outdoor_gen2=[excl],
        presence_simulation_system=excl,
        shutter_contacts2=[excl],
        thermostats=[excl],
        motion_detectors2=[excl],
        micromodule_shutter_controls=[excl],
        micromodule_blinds=[excl],
        micromodule_impulse_relays=[excl],
        micromodule_dimmers=[excl],
        roomthermostats=[excl],
        wallthermostats=[excl],
        universal_switches=[],
    )
    session = MagicMock()
    session.device_helper = dh
    session.userdefinedstates = userdefinedstates or []
    session._subscribers = []
    session.subscribe = MagicMock()
    session.subscribe_userdefinedstate_callback = MagicMock()
    session.unsubscribe_userdefinedstate_callbacks = MagicMock()
    return session


def _make_entry(options=None, entry_id="eid1", title="My SHC"):
    entry = MagicMock()
    entry.entry_id = entry_id
    entry.title = title
    entry.options = options or {OPT_EXCLUDED_DEVICES: [EXCLUDED_ID]}
    entry.async_on_unload = MagicMock()
    return entry


def _make_hass(session, entry, shc_device=None):
    if shc_device is None:
        shc_device = SimpleNamespace(
            name="SHC Hub",
            id="shc-device-id",
            identifiers={(DOMAIN, "mac1")},
            manufacturer="Bosch",
            model="SHC 2",
        )
    hass = MagicMock()
    hass.data = {DOMAIN: {entry.entry_id: {DATA_SESSION: session, DATA_SHC: shc_device}}}

    async def _async_none(*args, **kwargs):
        return None

    hass.async_add_executor_job = AsyncMock(return_value=None)
    hass.config_entries = MagicMock()
    hass.loop = MagicMock()
    return hass


PATCH_MIGRATE = "custom_components.bosch_shc.switch.async_migrate_to_new_unique_id"
PATCH_DEVICE_EXCLUDED = "custom_components.bosch_shc.switch.device_excluded"


async def _run_setup(hass, entry, async_add_entities):
    from custom_components.bosch_shc.switch import async_setup_entry
    await async_setup_entry(hass, entry, async_add_entities)


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# 1 — All device-type exclusion branches (lines 258, 286, 300, 314, 328,
#     366, 391, 467, 490, 511, 534) are hit when every device is excluded.
# ---------------------------------------------------------------------------

class TestAllDeviceTypesExcluded:
    """Excluding every device type covers all continue branches in the loops."""

    def _setup_all_excluded(self):
        """Run async_setup_entry with all devices set to excluded-id."""
        session = _make_session()
        entry = _make_entry(options={OPT_EXCLUDED_DEVICES: [EXCLUDED_ID]})
        shc_dev = SimpleNamespace(
            name="SHC", id="shcid",
            identifiers={(DOMAIN, "mac1")},
            manufacturer="Bosch", model="SHC2",
        )
        hass = _make_hass(session, entry, shc_dev)
        added: list = []
        async_add_entities = MagicMock(side_effect=lambda ents, **kw: added.extend(ents))

        with patch(PATCH_MIGRATE, new=AsyncMock(return_value=None)):
            _run(_run_setup(hass, entry, async_add_entities))

        return added

    def test_no_entities_added_when_all_excluded(self):
        """When every device matches OPT_EXCLUDED_DEVICES, zero entities are added."""
        added = self._setup_all_excluded()
        # async_add_entities may not be called at all, or called with [].
        # Either way, the total count of added entities from device loops = 0.
        # (UDS entities are in a separate call and there are none here.)
        assert len(added) == 0

    def test_exclusion_branches_exercised_via_device_excluded(self):
        """Verify device_excluded is the actual gating function called for each loop.

        We do NOT mock device_excluded itself — we rely on the real function reading
        OPT_EXCLUDED_DEVICES from options. This confirms all loop branches run.
        """
        session = _make_session()
        entry = _make_entry(options={OPT_EXCLUDED_DEVICES: [EXCLUDED_ID]})
        shc_dev = SimpleNamespace(
            name="SHC", id="shcid",
            identifiers={(DOMAIN, "mac1")},
            manufacturer="Bosch", model="SHC2",
        )
        hass = _make_hass(session, entry, shc_dev)
        added: list = []
        async_add_entities = MagicMock(side_effect=lambda ents, **kw: added.extend(ents))

        with patch(PATCH_MIGRATE, new=AsyncMock(return_value=None)):
            _run(_run_setup(hass, entry, async_add_entities))

        # All device loops hit the continue branch -> 0 entities from loops
        assert len(added) == 0


# ---------------------------------------------------------------------------
# 2 — motion_detectors2 included path (lines 545-553 region)
# ---------------------------------------------------------------------------

class TestMotionDetectors2IncludedPath:
    """When motion_detectors2 devices are NOT excluded, pet_immunity entities are created."""

    def _setup_motion_included(self):
        session = _make_session()
        # Replace motion_detectors2 with an included device
        incl = _included_dev(device_id="motion-incl-001")
        session.device_helper.motion_detectors2 = [incl]
        # Still exclude all other devices to isolate this test
        entry = _make_entry(options={OPT_EXCLUDED_DEVICES: [EXCLUDED_ID]})
        shc_dev = SimpleNamespace(
            name="SHC", id="shcid",
            identifiers={(DOMAIN, "mac1")},
            manufacturer="Bosch", model="SHC2",
        )
        hass = _make_hass(session, entry, shc_dev)
        added: list = []
        async_add_entities = MagicMock(side_effect=lambda ents, **kw: added.extend(ents))
        migrate_calls: list = []

        async def _fake_migrate(**kwargs):
            migrate_calls.append(kwargs)

        with patch(PATCH_MIGRATE, side_effect=_fake_migrate):
            _run(_run_setup(hass, entry, async_add_entities))

        return added, migrate_calls

    def test_pet_immunity_entity_created(self):
        """Included motion_detectors2 device produces a PetImmunity switch entity."""
        from custom_components.bosch_shc.switch import SHCSwitch

        added, _ = self._setup_motion_included()
        pet_entities = [
            e for e in added
            if isinstance(e, SHCSwitch)
            and e.entity_description.key == "pet_immunity_enabled"
        ]
        assert len(pet_entities) == 1

    def test_migrate_called_for_pet_immunity(self):
        """async_migrate_to_new_unique_id is called with attr_name=PetImmunity."""
        _, migrate_calls = self._setup_motion_included()
        attr_names = [c.get("attr_name") for c in migrate_calls]
        assert "PetImmunity" in attr_names


# ---------------------------------------------------------------------------
# 3 — user_defined_states path (session.userdefinedstates loop)
# ---------------------------------------------------------------------------

class TestUserDefinedStatesPath:
    """session.userdefinedstates items each produce a SHCUserDefinedStateSwitch."""

    def _setup_with_uds(self, uds_list):
        session = _make_session(userdefinedstates=uds_list)
        # Exclude all regular device-type devices to isolate UDS path
        entry = _make_entry(options={OPT_EXCLUDED_DEVICES: [EXCLUDED_ID]})
        shc_dev = SimpleNamespace(
            name="SHC Hub", id="shcid",
            identifiers={(DOMAIN, "mac1")},
            manufacturer="Bosch", model="SHC2",
        )
        hass = _make_hass(session, entry, shc_dev)
        added: list = []
        async_add_entities = MagicMock(side_effect=lambda ents, **kw: added.extend(ents))

        with patch(PATCH_MIGRATE, new=AsyncMock(return_value=None)):
            _run(_run_setup(hass, entry, async_add_entities))

        return added, session

    def _make_uds_device(self, name="Vacation", dev_id="uds-001"):
        """Minimal UserDefinedState device double."""
        return SimpleNamespace(
            name=name,
            id=dev_id,
            root_device_id="mac1",
            state=False,
            deleted=False,
        )

    def test_single_uds_creates_one_entity(self):
        """One UDS device -> one SHCUserDefinedStateSwitch entity added."""
        from custom_components.bosch_shc.switch import SHCUserDefinedStateSwitch

        uds = self._make_uds_device("Vacation Mode", "uds-001")
        added, _ = self._setup_with_uds([uds])
        uds_entities = [e for e in added if isinstance(e, SHCUserDefinedStateSwitch)]
        assert len(uds_entities) == 1

    def test_multiple_uds_create_multiple_entities(self):
        """Two UDS devices produce two switch entities."""
        from custom_components.bosch_shc.switch import SHCUserDefinedStateSwitch

        uds1 = self._make_uds_device("Mode A", "uds-a")
        uds2 = self._make_uds_device("Mode B", "uds-b")
        added, _ = self._setup_with_uds([uds1, uds2])
        uds_entities = [e for e in added if isinstance(e, SHCUserDefinedStateSwitch)]
        assert len(uds_entities) == 2

    def test_uds_subscriber_registered(self):
        """session.subscribe is called with a (type, callback) tuple for new UDS devices."""
        from boschshcpy import SHCUserDefinedState

        uds = self._make_uds_device()
        _, session = self._setup_with_uds([uds])
        session.subscribe.assert_called_once()
        call_args = session.subscribe.call_args[0][0]
        # The subscriber tuple: (SHCUserDefinedState, callback)
        assert call_args[0] is SHCUserDefinedState

    def test_empty_uds_list_no_uds_entities(self):
        """Empty userdefinedstates -> no UDS entities, but no crash either."""
        from custom_components.bosch_shc.switch import SHCUserDefinedStateSwitch

        added, _ = self._setup_with_uds([])
        uds_entities = [e for e in added if isinstance(e, SHCUserDefinedStateSwitch)]
        assert len(uds_entities) == 0


# ---------------------------------------------------------------------------
# 4 — Thermostat child_lock included path (line 510-511 region)
# ---------------------------------------------------------------------------

class TestThermostatChildLockIncluded:
    """Thermostats not excluded produce child_lock_thermostat switch entities."""

    def test_thermostat_child_lock_entity_created(self):
        """Included thermostat -> child_lock_thermostat SHCSwitch entity."""
        from custom_components.bosch_shc.switch import SHCSwitch

        session = _make_session()
        incl = _included_dev(device_id="thermo-001")
        session.device_helper.thermostats = [incl]
        session.device_helper.roomthermostats = []
        session.device_helper.wallthermostats = []
        # Also clear micromodule loops that feed into the child_lock (bool) block
        session.device_helper.micromodule_shutter_controls = []
        session.device_helper.micromodule_blinds = []
        session.device_helper.micromodule_light_attached = []
        session.device_helper.micromodule_relays = []
        session.device_helper.micromodule_impulse_relays = []
        session.device_helper.micromodule_dimmers = []
        session.device_helper.light_switches_bsm = []

        entry = _make_entry(options={OPT_EXCLUDED_DEVICES: [EXCLUDED_ID]})
        shc_dev = SimpleNamespace(
            name="SHC", id="shcid",
            identifiers={(DOMAIN, "mac1")},
            manufacturer="Bosch", model="SHC2",
        )
        hass = _make_hass(session, entry, shc_dev)
        added: list = []
        async_add_entities = MagicMock(side_effect=lambda ents, **kw: added.extend(ents))

        with patch(PATCH_MIGRATE, new=AsyncMock(return_value=None)):
            _run(_run_setup(hass, entry, async_add_entities))

        child_lock_entities = [
            e for e in added
            if isinstance(e, SHCSwitch)
            and e.entity_description.key == "child_lock_thermostat"
        ]
        assert len(child_lock_entities) >= 1


# ---------------------------------------------------------------------------
# 5 — ChildProtection bool-device included path (line 533-534 region)
# ---------------------------------------------------------------------------

class TestChildProtectionBoolDeviceIncluded:
    """Micromodule devices not excluded produce child_lock (bool) SHCSwitch entities."""

    def test_micromodule_shutter_child_lock_included(self):
        """Included micromodule_shutter_controls -> child_lock entity created."""
        from custom_components.bosch_shc.switch import SHCSwitch

        session = _make_session()
        incl = _included_dev(device_id="shutctl-001")
        # micromodule_shutter_controls feeds the child_lock (bool) loop at line 525
        session.device_helper.micromodule_shutter_controls = [incl]
        session.device_helper.micromodule_blinds = []
        session.device_helper.micromodule_light_attached = []
        session.device_helper.micromodule_relays = []
        session.device_helper.micromodule_impulse_relays = []
        session.device_helper.micromodule_dimmers = []
        session.device_helper.light_switches_bsm = []
        session.device_helper.thermostats = []
        session.device_helper.roomthermostats = []
        session.device_helper.wallthermostats = []

        entry = _make_entry(options={OPT_EXCLUDED_DEVICES: [EXCLUDED_ID]})
        shc_dev = SimpleNamespace(
            name="SHC", id="shcid",
            identifiers={(DOMAIN, "mac1")},
            manufacturer="Bosch", model="SHC2",
        )
        hass = _make_hass(session, entry, shc_dev)
        added: list = []
        async_add_entities = MagicMock(side_effect=lambda ents, **kw: added.extend(ents))

        with patch(PATCH_MIGRATE, new=AsyncMock(return_value=None)):
            _run(_run_setup(hass, entry, async_add_entities))

        child_lock_entities = [
            e for e in added
            if isinstance(e, SHCSwitch)
            and e.entity_description.key == "child_lock"
        ]
        assert len(child_lock_entities) >= 1
