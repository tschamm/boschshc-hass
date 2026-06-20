"""Tests for switch.async_setup_entry + SHCUserDefinedStateSwitch — harness-free.

Covers:
- async_setup_entry: all device-helper branches (smart_plugs, light_switches,
  micromodule_relays, camera_eyes, camera_360, camera_outdoor_gen2,
  presence_simulation_system, shutter_contacts2, thermostats, roomthermostats,
  micromodule_* child-lock groups) + userdefinedstates loop + subscriber
  registration + unsubscribe closure.
- SHCUserDefinedStateSwitch: __init__ (entity_id / unique_id / name / device_info),
  is_on / turn_on / turn_off / should_poll / device_name / device_id.

Run with:
  PYTHONPATH="...boschshc-hass:...boschshcpy" PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
  python3 -m pytest tests/bosch_shc/test_switch_setup.py -q -o addopts= -p no:cacheprovider
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from boschshcpy import SHCShutterContact2Plus, SHCUserDefinedState

from custom_components.bosch_shc.switch import (
    SWITCH_TYPES,
    SHCSwitch,
    SHCUserDefinedStateSwitch,
    async_setup_entry,
)
from custom_components.bosch_shc.const import DATA_SESSION, DATA_SHC, DOMAIN


# ---------------------------------------------------------------------------
# Helpers — fake devices
# ---------------------------------------------------------------------------


def _fake_device(name="Dev", dev_id="dev1", root_id="root1", serial="SER1"):
    """Minimal fake SHC device."""
    return SimpleNamespace(
        name=name,
        id=dev_id,
        root_device_id=root_id,
        serial=serial,
        supports_silentmode=False,
    )


def _fake_thermostat(name="Thermo", dev_id="therm1", root_id="root1", silent=True):
    d = _fake_device(name=name, dev_id=dev_id, root_id=root_id)
    d.supports_silentmode = silent
    return d


def _fake_uds(name="MyState", dev_id="uds1", root_id="mac1", state=True):
    """Fake SHCUserDefinedState as a SimpleNamespace (same attrs)."""
    return SimpleNamespace(
        name=name,
        id=dev_id,
        root_device_id=root_id,
        state=state,
        deleted=False,
    )


def _fake_shutter2(name="Shutter", dev_id="sh1", root_id="root1"):
    """Base SHCShutterContact2 (no vibration)."""
    d = _fake_device(name=name, dev_id=dev_id, root_id=root_id)
    return d


def _fake_shutter2plus(name="Shutter+", dev_id="shp1", root_id="root1"):
    """SHCShutterContact2Plus instance (isinstance check in switch.py).

    We must pass isinstance(obj, SHCShutterContact2Plus) without calling the
    real __init__ (which requires api/raw_device).  Use a local subclass that
    overrides the read-only parent properties with plain data attributes.
    """

    class _FakePlus(SHCShutterContact2Plus):
        # Shadow the parent read-only properties with plain instance attrs
        name = None  # type: ignore[assignment]
        id = None  # type: ignore[assignment]
        root_device_id = None  # type: ignore[assignment]
        serial = None  # type: ignore[assignment]
        supports_silentmode = False

        def __init__(self, _name, _id, _root):
            # Bypass the real __init__ entirely
            self.name = _name
            self.id = _id
            self.root_device_id = _root
            self.serial = "SER_PLUS"

    return _FakePlus(name, dev_id, root_id)


# ---------------------------------------------------------------------------
# Fake hass + config_entry factory
# ---------------------------------------------------------------------------


def _make_hass_and_entry(session, shc_device=None):
    """Return (hass, config_entry) with session wired into hass.data."""
    if shc_device is None:
        shc_device = SimpleNamespace(
            name="SHC",
            id="shc_dev",
            identifiers={("bosch_shc", "shc_dev")},
            manufacturer="Bosch",
            model="SHC",
        )

    entry_id = "E1"
    hass = SimpleNamespace(
        data={
            DOMAIN: {
                entry_id: {
                    DATA_SESSION: session,
                    DATA_SHC: shc_device,
                }
            }
        }
    )
    config_entry = SimpleNamespace(
        entry_id=entry_id,
        async_on_unload=MagicMock(),
    )
    return hass, config_entry


def _make_session(**helper_lists):
    """Build a fake session with device_helper + userdefinedstates."""
    defaults = dict(
        smart_plugs=[],
        light_switches_bsm=[],
        micromodule_light_attached=[],
        smart_plugs_compact=[],
        micromodule_relays=[],
        camera_eyes=[],
        camera_360=[],
        camera_outdoor_gen2=[],
        presence_simulation_system=None,
        shutter_contacts2=[],
        thermostats=[],
        roomthermostats=[],
        wallthermostats=[],
        micromodule_shutter_controls=[],
        micromodule_blinds=[],
        micromodule_impulse_relays=[],
        micromodule_dimmers=[],
    )
    defaults.update(helper_lists)

    device_helper = SimpleNamespace(**defaults)
    uds_list = helper_lists.get("userdefinedstates", [])
    session = SimpleNamespace(
        device_helper=device_helper,
        userdefinedstates=uds_list,
        subscribe=MagicMock(),
        _subscribers=[],
    )
    return session


# ---------------------------------------------------------------------------
# async_migrate_to_new_unique_id patch — makes it a no-op coroutine
# ---------------------------------------------------------------------------

_PATCH_MIGRATE = patch(
    "custom_components.bosch_shc.switch.async_migrate_to_new_unique_id",
    new_callable=lambda: lambda *a, **kw: AsyncMock(return_value=None)(),
)


def _run(coro):
    return asyncio.run(coro)


async def _async_setup(session, shc_device=None):
    """Run async_setup_entry and return (entities, config_entry)."""
    hass, config_entry = _make_hass_and_entry(session, shc_device)
    entities = []

    def add_entities(new_ents, *args, **kwargs):
        entities.extend(new_ents)

    with patch(
        "custom_components.bosch_shc.switch.async_migrate_to_new_unique_id",
        new=AsyncMock(return_value=None),
    ):
        await async_setup_entry(hass, config_entry, add_entities)

    return entities, config_entry


def _setup(session, shc_device=None):
    return asyncio.run(_async_setup(session, shc_device))


# ---------------------------------------------------------------------------
# async_setup_entry — empty session (smoke test)
# ---------------------------------------------------------------------------


def test_setup_empty_session_no_entities():
    session = _make_session()
    entities, _ = _setup(session)
    # No regular device entities; no UDS either
    assert entities == []


# ---------------------------------------------------------------------------
# smart_plugs — 2 entities per plug (smartplug + routing)
# ---------------------------------------------------------------------------


def test_setup_smart_plugs_creates_two_entities_per_plug():
    plug = _fake_device(name="Plug A", dev_id="plug1")
    session = _make_session(smart_plugs=[plug])
    entities, _ = _setup(session)
    assert len(entities) == 2
    types = {e.entity_description.key for e in entities}
    assert types == {"smartplug", "smartplug_routing"}


def test_setup_smart_plugs_two_plugs_four_entities():
    p1 = _fake_device(name="Plug 1", dev_id="p1")
    p2 = _fake_device(name="Plug 2", dev_id="p2")
    session = _make_session(smart_plugs=[p1, p2])
    entities, _ = _setup(session)
    assert len(entities) == 4


def test_setup_smart_plug_entity_types_are_shcswitch():
    plug = _fake_device(name="Plug", dev_id="plug1")
    session = _make_session(smart_plugs=[plug])
    entities, _ = _setup(session)
    assert all(isinstance(e, SHCSwitch) for e in entities)


# ---------------------------------------------------------------------------
# light_switches_bsm + micromodule_light_attached → lightswitch
# ---------------------------------------------------------------------------


def test_setup_light_switch_bsm_one_entity():
    sw = _fake_device(name="Light BSM", dev_id="lbsm1")
    session = _make_session(light_switches_bsm=[sw])
    entities, _ = _setup(session)
    assert len(entities) == 2  # lightswitch + child_lock
    keys = {e.entity_description.key for e in entities}
    assert "lightswitch" in keys
    assert "child_lock" in keys


def test_setup_micromodule_light_attached_one_entity():
    sw = _fake_device(name="MM Light", dev_id="mmla1")
    session = _make_session(micromodule_light_attached=[sw])
    entities, _ = _setup(session)
    keys = {e.entity_description.key for e in entities}
    assert "lightswitch" in keys
    assert "child_lock" in keys


# ---------------------------------------------------------------------------
# smart_plugs_compact → smartplugcompact
# ---------------------------------------------------------------------------


def test_setup_smart_plug_compact_one_entity():
    sw = _fake_device(name="Compact", dev_id="spc1")
    session = _make_session(smart_plugs_compact=[sw])
    entities, _ = _setup(session)
    assert len(entities) == 1
    assert entities[0].entity_description.key == "smartplugcompact"


# ---------------------------------------------------------------------------
# micromodule_relays → micromodule_relay_switch + child_lock
# ---------------------------------------------------------------------------


def test_setup_micromodule_relay_creates_two_entities():
    sw = _fake_device(name="Relay", dev_id="mm1")
    session = _make_session(micromodule_relays=[sw])
    entities, _ = _setup(session)
    keys = {e.entity_description.key for e in entities}
    assert "micromodule_relay_switch" in keys
    assert "child_lock" in keys


# ---------------------------------------------------------------------------
# camera_eyes → 3 entities
# ---------------------------------------------------------------------------


def test_setup_camera_eyes_three_entities():
    cam = _fake_device(name="Cam Eyes", dev_id="ceyes1")
    session = _make_session(camera_eyes=[cam])
    entities, _ = _setup(session)
    keys = {e.entity_description.key for e in entities}
    assert keys == {"cameraeyes", "cameraeyes_cameralight", "cameraeyes_notification"}


def test_setup_camera_eyes_entity_names():
    cam = _fake_device(name="Eyes Outdoor", dev_id="ceyes2")
    session = _make_session(camera_eyes=[cam])
    entities, _ = _setup(session)
    names = {e._attr_name for e in entities}
    assert "Eyes Outdoor" in names
    assert "Eyes Outdoor Light" in names
    assert "Eyes Outdoor Notification" in names


# ---------------------------------------------------------------------------
# camera_360 → 2 entities
# ---------------------------------------------------------------------------


def test_setup_camera_360_two_entities():
    cam = _fake_device(name="Cam 360", dev_id="c360_1")
    session = _make_session(camera_360=[cam])
    entities, _ = _setup(session)
    keys = {e.entity_description.key for e in entities}
    assert keys == {"camera360", "camera360_notification"}


# ---------------------------------------------------------------------------
# camera_outdoor_gen2 → 3 entities (privacy + frontlight + ambientlight)
# ---------------------------------------------------------------------------


def test_setup_camera_outdoor_gen2_three_entities():
    cam = _fake_device(name="Gen2 Cam", dev_id="gen2_1")
    session = _make_session(camera_outdoor_gen2=[cam])
    entities, _ = _setup(session)
    keys = {e.entity_description.key for e in entities}
    assert keys == {
        "cameraoutdoorgen2",
        "cameraoutdoorgen2_camerafrontlight",
        "cameraoutdoorgen2_cameraambientlight",
    }


def test_setup_camera_outdoor_gen2_attr_names():
    cam = _fake_device(name="Eyes Outdoor II", dev_id="gen2_2")
    session = _make_session(camera_outdoor_gen2=[cam])
    entities, _ = _setup(session)
    names = {e._attr_name for e in entities}
    assert "Eyes Outdoor II" in names
    assert "Eyes Outdoor II Frontlight" in names
    assert "Eyes Outdoor II AmbientLight" in names


# ---------------------------------------------------------------------------
# presence_simulation_system — optional; present vs absent
# ---------------------------------------------------------------------------


def test_setup_presence_simulation_when_present():
    dev = _fake_device(name="PresenceSim", dev_id="pss1")
    session = _make_session(presence_simulation_system=dev)
    entities, _ = _setup(session)
    assert len(entities) == 1
    assert entities[0].entity_description.key == "presencesimulation"


def test_setup_presence_simulation_absent():
    session = _make_session(presence_simulation_system=None)
    entities, _ = _setup(session)
    assert entities == []


# ---------------------------------------------------------------------------
# shutter_contacts2 — base → 1 bypass; Plus → bypass + vibration_enabled
# ---------------------------------------------------------------------------


def test_setup_shutter_contact2_base_one_entity():
    sw = _fake_shutter2(name="Shutter", dev_id="sh1")
    session = _make_session(shutter_contacts2=[sw])
    entities, _ = _setup(session)
    assert len(entities) == 1
    assert entities[0].entity_description.key == "bypass"


def test_setup_shutter_contact2plus_two_entities():
    sw = _fake_shutter2plus(name="Shutter+", dev_id="shp1")
    session = _make_session(shutter_contacts2=[sw])
    entities, _ = _setup(session)
    keys = {e.entity_description.key for e in entities}
    assert keys == {"bypass", "vibration_enabled"}


# ---------------------------------------------------------------------------
# thermostats — silent_mode (if supports_silentmode) + child_lock_thermostat
# ---------------------------------------------------------------------------


def test_setup_thermostat_with_silentmode_two_entities():
    th = _fake_thermostat(name="Thermo", dev_id="th1", silent=True)
    session = _make_session(thermostats=[th])
    entities, _ = _setup(session)
    keys = {e.entity_description.key for e in entities}
    assert "silent_mode" in keys
    assert "child_lock_thermostat" in keys


def test_setup_thermostat_without_silentmode_only_child_lock():
    th = _fake_thermostat(name="Thermo2", dev_id="th2", silent=False)
    session = _make_session(thermostats=[th])
    entities, _ = _setup(session)
    keys = {e.entity_description.key for e in entities}
    assert "child_lock_thermostat" in keys
    assert "silent_mode" not in keys


# ---------------------------------------------------------------------------
# roomthermostats → child_lock_thermostat (NOT silent_mode)
# ---------------------------------------------------------------------------


def test_setup_roomthermostat_child_lock_only():
    rt = _fake_device(name="RoomThermo", dev_id="rt1")
    session = _make_session(roomthermostats=[rt])
    entities, _ = _setup(session)
    keys = {e.entity_description.key for e in entities}
    assert keys == {"child_lock_thermostat"}


# ---------------------------------------------------------------------------
# wallthermostats (THB/BWTH/BWTH24) → child_lock_thermostat (enum-aware)
# ---------------------------------------------------------------------------


def test_setup_wallthermostat_child_lock_only():
    """THB/BWTH wall thermostat gets child_lock_thermostat (ThermostatService enum)."""
    wt = _fake_device(name="WallThermo", dev_id="wt1")
    wt.child_lock = "ON"  # boschshcpy >= 0.2.119 exposes child_lock on wall thermostats
    session = _make_session(wallthermostats=[wt])
    entities, _ = _setup(session)
    keys = {e.entity_description.key for e in entities}
    assert keys == {"child_lock_thermostat"}


def test_setup_wallthermostat_uses_enum_description():
    """Confirms the wallthermostat child_lock entity uses the enum-aware description."""
    from boschshcpy import SHCThermostat

    wt = _fake_device(name="WallThermo2", dev_id="wt2")
    wt.child_lock = "ON"  # boschshcpy >= 0.2.119 exposes child_lock on wall thermostats
    session = _make_session(wallthermostats=[wt])
    entities, _ = _setup(session)
    cl_entities = [e for e in entities if e.entity_description.key == "child_lock_thermostat"]
    assert len(cl_entities) == 1
    assert cl_entities[0].entity_description.on_value == SHCThermostat.ThermostatService.State.ON


# ---------------------------------------------------------------------------
# micromodule child-lock group (bool child_lock)
# Each device in the combined list → 1 child_lock entity
# ---------------------------------------------------------------------------


def test_setup_micromodule_shutter_control_child_lock():
    d = _fake_device(name="MM Shutter", dev_id="msc1")
    session = _make_session(micromodule_shutter_controls=[d])
    entities, _ = _setup(session)
    keys = {e.entity_description.key for e in entities}
    assert "child_lock" in keys


def test_setup_micromodule_blinds_child_lock():
    d = _fake_device(name="MM Blind", dev_id="mbl1")
    session = _make_session(micromodule_blinds=[d])
    entities, _ = _setup(session)
    keys = {e.entity_description.key for e in entities}
    assert "child_lock" in keys


def test_setup_micromodule_impulse_relay_child_lock():
    d = _fake_device(name="MM Impulse", dev_id="mir1")
    session = _make_session(micromodule_impulse_relays=[d])
    entities, _ = _setup(session)
    keys = {e.entity_description.key for e in entities}
    assert "child_lock" in keys


def test_setup_micromodule_dimmer_child_lock():
    d = _fake_device(name="MM Dimmer", dev_id="mdi1")
    session = _make_session(micromodule_dimmers=[d])
    entities, _ = _setup(session)
    keys = {e.entity_description.key for e in entities}
    assert "child_lock" in keys


# ---------------------------------------------------------------------------
# userdefinedstates — creates SHCUserDefinedStateSwitch entities
# ---------------------------------------------------------------------------


def test_setup_userdefinedstate_one_switch():
    uds = _fake_uds(name="Home", dev_id="uds1", root_id="mac1", state=True)
    session = _make_session(userdefinedstates=[uds])
    entities, _ = _setup(session)
    assert len(entities) == 1
    assert isinstance(entities[0], SHCUserDefinedStateSwitch)


def test_setup_userdefinedstate_entity_description():
    uds = _fake_uds(name="Away", dev_id="uds2", root_id="mac1")
    session = _make_session(userdefinedstates=[uds])
    entities, _ = _setup(session)
    assert entities[0].entity_description.key == "user_defined_state"


def test_setup_userdefinedstate_entity_id_slugified():
    uds = _fake_uds(name="At Home", dev_id="uds3", root_id="mac1")
    session = _make_session(userdefinedstates=[uds])
    entities, _ = _setup(session)
    # ENTITY_ID_FORMAT is "switch.{}" → "switch.userdefinedstate_at_home"
    assert entities[0].entity_id == "switch.userdefinedstate_at_home"


def test_setup_userdefinedstate_unique_id():
    uds = _fake_uds(name="Night", dev_id="uds4", root_id="macABC")
    session = _make_session(userdefinedstates=[uds])
    entities, _ = _setup(session)
    assert entities[0]._attr_unique_id == "macABC_uds4"


def test_setup_userdefinedstate_attr_name():
    uds = _fake_uds(name="Vacation Mode", dev_id="uds5", root_id="mac1")
    session = _make_session(userdefinedstates=[uds])
    entities, _ = _setup(session)
    assert entities[0]._attr_name == "Vacation Mode"


def test_setup_userdefinedstate_multiple():
    uds1 = _fake_uds(name="Home", dev_id="u1", root_id="mac1")
    uds2 = _fake_uds(name="Away", dev_id="u2", root_id="mac1")
    session = _make_session(userdefinedstates=[uds1, uds2])
    entities, _ = _setup(session)
    assert len(entities) == 2


# ---------------------------------------------------------------------------
# subscriber registration and unload closure
# ---------------------------------------------------------------------------


def test_setup_subscribes_to_session():
    uds = _fake_uds(name="Home", dev_id="uds1", root_id="mac1")
    session = _make_session(userdefinedstates=[uds])
    _, _ = _setup(session)
    session.subscribe.assert_called_once()
    args = session.subscribe.call_args[0][0]
    assert args[0] is SHCUserDefinedState


def test_setup_registers_async_on_unload():
    session = _make_session()
    _, config_entry = _setup(session)
    config_entry.async_on_unload.assert_called_once()


def test_setup_unload_removes_subscriber():
    """The unsubscribe closure removes the tuple from session._subscribers."""
    async def _run_it():
        uds = _fake_uds(name="Night", dev_id="uds1", root_id="mac1")
        session = _make_session(userdefinedstates=[uds])
        hass, config_entry = _make_hass_and_entry(session)
        entities = []

        unload_fn = None

        def capture_unload(fn):
            nonlocal unload_fn
            unload_fn = fn

        config_entry.async_on_unload = capture_unload

        with patch(
            "custom_components.bosch_shc.switch.async_migrate_to_new_unique_id",
            new=AsyncMock(return_value=None),
        ):
            await async_setup_entry(hass, config_entry, lambda e, *a, **kw: entities.extend(e))

        # The subscriber tuple was added by subscribe()
        assert unload_fn is not None
        session.subscribe.assert_called_once()
        subscriber = session.subscribe.call_args[0][0]
        # Simulate it being in _subscribers
        session._subscribers.append(subscriber)
        unload_fn()
        assert subscriber not in session._subscribers

    asyncio.run(_run_it())


def test_setup_unload_no_error_when_subscriber_already_gone():
    """Unload closure must not raise if subscriber was already removed."""
    async def _run_it():
        session = _make_session()
        hass, config_entry = _make_hass_and_entry(session)
        entities = []

        unload_fn = None

        def capture_unload(fn):
            nonlocal unload_fn
            unload_fn = fn

        config_entry.async_on_unload = capture_unload

        with patch(
            "custom_components.bosch_shc.switch.async_migrate_to_new_unique_id",
            new=AsyncMock(return_value=None),
        ):
            await async_setup_entry(hass, config_entry, lambda e, *a, **kw: entities.extend(e))

        assert unload_fn is not None
        # _subscribers is empty → ValueError swallowed
        unload_fn()  # must not raise

    asyncio.run(_run_it())


# ---------------------------------------------------------------------------
# SHCUserDefinedStateSwitch — standalone unit tests
# ---------------------------------------------------------------------------


def _make_uds_switch(name="TestState", dev_id="udx1", root_id="mac9", state=True):
    """Construct SHCUserDefinedStateSwitch directly with a fake device/session."""
    uds = _fake_uds(name=name, dev_id=dev_id, root_id=root_id, state=state)
    shc_dev = SimpleNamespace(
        name="SHC",
        id="shc_dev",
        identifiers={("bosch_shc", "shc_dev")},
        manufacturer="Bosch",
        model="SHC",
    )
    hass = SimpleNamespace(
        data={
            DOMAIN: {
                "E1": {
                    DATA_SHC: shc_dev,
                }
            }
        }
    )
    session = SimpleNamespace(
        subscribe=MagicMock(),
        _subscribers=[],
    )
    sw = SHCUserDefinedStateSwitch(
        device=uds,
        hass=hass,
        session=session,
        entry_id="E1",
        description=SWITCH_TYPES["user_defined_state"],
    )
    return sw


def test_uds_switch_is_on_true():
    sw = _make_uds_switch(state=True)
    assert sw.is_on is True


def test_uds_switch_is_on_false():
    sw = _make_uds_switch(state=False)
    assert sw.is_on is False


def test_uds_switch_turn_on_sets_state():
    """turn_on calls setattr(device, 'state', True)."""
    written = []

    class _FakeUDS:
        name = "X"
        id = "u1"
        root_device_id = "mac1"

        @property
        def state(self):
            return written[-1] if written else False

        @state.setter
        def state(self, v):
            written.append(v)

    shc_dev = SimpleNamespace(
        name="SHC",
        id="shc_dev",
        identifiers={("bosch_shc", "shc_dev")},
        manufacturer="Bosch",
        model="SHC",
    )
    hass = SimpleNamespace(data={DOMAIN: {"E1": {DATA_SHC: shc_dev}}})
    sw = SHCUserDefinedStateSwitch(
        device=_FakeUDS(),
        hass=hass,
        session=SimpleNamespace(subscribe=MagicMock(), _subscribers=[]),
        entry_id="E1",
        description=SWITCH_TYPES["user_defined_state"],
    )
    sw.turn_on()
    assert written == [True]


def test_uds_switch_turn_off_sets_state():
    """turn_off calls setattr(device, 'state', False)."""
    written = []

    class _FakeUDS:
        name = "Y"
        id = "u2"
        root_device_id = "mac2"

        @property
        def state(self):
            return written[-1] if written else True

        @state.setter
        def state(self, v):
            written.append(v)

    shc_dev = SimpleNamespace(
        name="SHC",
        id="shc_dev",
        identifiers={("bosch_shc", "shc_dev")},
        manufacturer="Bosch",
        model="SHC",
    )
    hass = SimpleNamespace(data={DOMAIN: {"E1": {DATA_SHC: shc_dev}}})
    sw = SHCUserDefinedStateSwitch(
        device=_FakeUDS(),
        hass=hass,
        session=SimpleNamespace(subscribe=MagicMock(), _subscribers=[]),
        entry_id="E1",
        description=SWITCH_TYPES["user_defined_state"],
    )
    sw.turn_off()
    assert written == [False]


def test_uds_switch_should_poll_false():
    sw = _make_uds_switch()
    assert sw.should_poll is False


def test_uds_switch_entity_id_format():
    sw = _make_uds_switch(name="Night Mode", dev_id="nm1")
    assert sw.entity_id == "switch.userdefinedstate_night_mode"


def test_uds_switch_unique_id_no_attr_name():
    sw = _make_uds_switch(name="Test", dev_id="ud99", root_id="macXX")
    assert sw._attr_unique_id == "macXX_ud99"


def test_uds_switch_attr_name():
    sw = _make_uds_switch(name="Holiday")
    assert sw._attr_name == "Holiday"


def test_uds_switch_device_name():
    sw = _make_uds_switch()
    assert sw.device_name == "SHC"


def test_uds_switch_device_id():
    sw = _make_uds_switch()
    assert sw.device_id == "shc_dev"


def test_uds_switch_device_info_keys():
    sw = _make_uds_switch()
    info = sw.device_info
    assert set(info.keys()) == {"identifiers", "name", "manufacturer", "model"}


def test_uds_switch_device_info_values():
    sw = _make_uds_switch()
    info = sw.device_info
    assert info["name"] == "SHC"
    assert info["manufacturer"] == "Bosch"
    assert info["model"] == "SHC"


# ---------------------------------------------------------------------------
# SHCSwitch unique_id / attr_name from __init__ (via real __init__ call)
# We need a real SHCEntity-compatible __init__; skip SHCEntity.__init__ via
# SHCSwitch.__new__ + manual field assignment.
# ---------------------------------------------------------------------------


def test_shcswitch_unique_id_for_smartplug():
    plug = _fake_device(name="Plug A", dev_id="plugX", root_id="rootY")
    session = _make_session(smart_plugs=[plug])
    entities, _ = _setup(session)
    smartplug_ent = next(
        e for e in entities if e.entity_description.key == "smartplug"
    )
    assert smartplug_ent._attr_unique_id == "rootY_plugX"


def test_shcswitch_unique_id_for_routing():
    plug = _fake_device(name="Plug B", dev_id="plugZ", root_id="rootQ")
    session = _make_session(smart_plugs=[plug])
    entities, _ = _setup(session)
    routing_ent = next(
        e for e in entities if e.entity_description.key == "smartplug_routing"
    )
    assert routing_ent._attr_unique_id == "rootQ_plugZ_routing"


def test_shcswitch_unique_id_for_camera_notification():
    cam = _fake_device(name="Cam", dev_id="camA", root_id="rootA")
    session = _make_session(camera_eyes=[cam])
    entities, _ = _setup(session)
    notif_ent = next(
        e for e in entities if e.entity_description.key == "cameraeyes_notification"
    )
    assert notif_ent._attr_unique_id == "rootA_camA_notification"


# ---------------------------------------------------------------------------
# SHCSwitch.update() — line 598
# ---------------------------------------------------------------------------


def test_shcswitch_update_calls_device_update():
    """SHCSwitch.update() must call device.update()."""
    called = []

    class _Dev:
        def update(self):
            called.append(True)

    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = _Dev()
    sw.entity_description = SWITCH_TYPES["cameraeyes"]
    sw.entity_id = "switch.test"
    sw.update()
    assert called == [True]


# ---------------------------------------------------------------------------
# SHCUserDefinedStateSwitch.update() — line 685
# ---------------------------------------------------------------------------


def test_uds_switch_update_calls_device_update():
    """SHCUserDefinedStateSwitch.update() must call device.update()."""
    called = []

    class _FakeUDS:
        name = "U"
        id = "u1"
        root_device_id = "mac1"
        state = True

        def update(self):
            called.append(True)

    shc_dev = SimpleNamespace(
        name="SHC",
        id="shc_dev",
        identifiers={("bosch_shc", "shc_dev")},
        manufacturer="Bosch",
        model="SHC",
    )
    hass = SimpleNamespace(data={DOMAIN: {"E1": {DATA_SHC: shc_dev}}})
    sw = SHCUserDefinedStateSwitch(
        device=_FakeUDS(),
        hass=hass,
        session=SimpleNamespace(subscribe=MagicMock(), _subscribers=[]),
        entry_id="E1",
        description=SWITCH_TYPES["user_defined_state"],
    )
    sw.update()
    assert called == [True]


# ---------------------------------------------------------------------------
# SHCUserDefinedStateSwitch.async_added_to_hass + async_will_remove_from_hass
# Lines 636-653, 659-660
# ---------------------------------------------------------------------------


def test_uds_switch_async_added_subscribes_callbacks():
    """async_added_to_hass registers two callbacks for the device id."""
    subscribed: list = []
    unsubscribed: list = []

    session = SimpleNamespace(
        subscribe=MagicMock(),
        _subscribers=[],
        subscribe_userdefinedstate_callback=lambda dev_id, fn: subscribed.append((dev_id, fn)),
        unsubscribe_userdefinedstate_callbacks=lambda dev_id: unsubscribed.append(dev_id),
    )
    uds = _fake_uds(name="Night", dev_id="uds_sub1", root_id="mac1")
    shc_dev = SimpleNamespace(
        name="SHC",
        id="shc_dev",
        identifiers={("bosch_shc", "shc_dev")},
        manufacturer="Bosch",
        model="SHC",
    )
    hass = SimpleNamespace(data={DOMAIN: {"E1": {DATA_SHC: shc_dev}}})
    sw = SHCUserDefinedStateSwitch(
        device=uds,
        hass=hass,
        session=session,
        entry_id="E1",
        description=SWITCH_TYPES["user_defined_state"],
    )
    # Patch out HA base class super() calls and schedule_update_ha_state
    sw.schedule_update_ha_state = MagicMock()
    sw.hass = SimpleNamespace(add_job=MagicMock())

    async def _run():
        # Patch SwitchEntity.async_added_to_hass to be a no-op
        with patch(
            "homeassistant.components.switch.SwitchEntity.async_added_to_hass",
            new=AsyncMock(return_value=None),
        ):
            await sw.async_added_to_hass()

    asyncio.run(_run())

    # Two callbacks should have been subscribed for the same device id
    assert len(subscribed) == 2
    assert all(dev_id == "uds_sub1" for dev_id, _ in subscribed)


def test_uds_switch_async_will_remove_unsubscribes():
    """async_will_remove_from_hass unsubscribes callbacks."""
    unsubscribed: list = []

    session = SimpleNamespace(
        subscribe=MagicMock(),
        _subscribers=[],
        subscribe_userdefinedstate_callback=MagicMock(),
        unsubscribe_userdefinedstate_callbacks=lambda dev_id: unsubscribed.append(dev_id),
    )
    uds = _fake_uds(name="Away", dev_id="uds_unsub1", root_id="mac2")
    shc_dev = SimpleNamespace(
        name="SHC",
        id="shc_dev",
        identifiers={("bosch_shc", "shc_dev")},
        manufacturer="Bosch",
        model="SHC",
    )
    hass = SimpleNamespace(data={DOMAIN: {"E1": {DATA_SHC: shc_dev}}})
    sw = SHCUserDefinedStateSwitch(
        device=uds,
        hass=hass,
        session=session,
        entry_id="E1",
        description=SWITCH_TYPES["user_defined_state"],
    )

    async def _run():
        with patch(
            "homeassistant.components.switch.SwitchEntity.async_will_remove_from_hass",
            new=AsyncMock(return_value=None),
        ):
            await sw.async_will_remove_from_hass()

    asyncio.run(_run())

    assert "uds_unsub1" in unsubscribed


def test_uds_switch_on_state_changed_callback_calls_schedule():
    """The on_state_changed inner callback must call schedule_update_ha_state."""
    scheduled: list = []

    session = SimpleNamespace(
        subscribe=MagicMock(),
        _subscribers=[],
        subscribe_userdefinedstate_callback=MagicMock(),
        unsubscribe_userdefinedstate_callbacks=MagicMock(),
    )
    uds = _fake_uds(name="Home", dev_id="uds_cb1", root_id="mac1")
    shc_dev = SimpleNamespace(
        name="SHC",
        id="shc_dev",
        identifiers={("bosch_shc", "shc_dev")},
        manufacturer="Bosch",
        model="SHC",
    )
    hass_inner = SimpleNamespace(data={DOMAIN: {"E1": {DATA_SHC: shc_dev}}})
    sw = SHCUserDefinedStateSwitch(
        device=uds,
        hass=hass_inner,
        session=session,
        entry_id="E1",
        description=SWITCH_TYPES["user_defined_state"],
    )
    sw.schedule_update_ha_state = lambda: scheduled.append(True)
    sw.hass = SimpleNamespace(add_job=MagicMock())

    async def _run():
        with patch(
            "homeassistant.components.switch.SwitchEntity.async_added_to_hass",
            new=AsyncMock(return_value=None),
        ):
            await sw.async_added_to_hass()

    asyncio.run(_run())

    # Fire the first callback (on_state_changed)
    first_cb = session.subscribe_userdefinedstate_callback.call_args_list[0][0][1]
    first_cb()
    assert len(scheduled) >= 1


def test_uds_switch_update_entity_information_deleted():
    """update_entity_information callback: deleted device → sets unavailable + schedules job."""
    add_job_calls: list = []
    scheduled: list = []

    session = SimpleNamespace(
        subscribe=MagicMock(),
        _subscribers=[],
        subscribe_userdefinedstate_callback=MagicMock(),
        unsubscribe_userdefinedstate_callbacks=MagicMock(),
    )
    uds = _fake_uds(name="Gone", dev_id="uds_del1", root_id="mac1")
    uds.deleted = True  # simulate deleted device

    shc_dev = SimpleNamespace(
        name="SHC",
        id="shc_dev",
        identifiers={("bosch_shc", "shc_dev")},
        manufacturer="Bosch",
        model="SHC",
    )
    hass_inner = SimpleNamespace(data={DOMAIN: {"E1": {DATA_SHC: shc_dev}}})
    sw = SHCUserDefinedStateSwitch(
        device=uds,
        hass=hass_inner,
        session=session,
        entry_id="E1",
        description=SWITCH_TYPES["user_defined_state"],
    )
    sw.schedule_update_ha_state = lambda: scheduled.append(True)
    mock_hass = SimpleNamespace(add_job=lambda coro: add_job_calls.append(coro))
    sw.hass = mock_hass

    async def _run():
        with patch(
            "homeassistant.components.switch.SwitchEntity.async_added_to_hass",
            new=AsyncMock(return_value=None),
        ):
            await sw.async_added_to_hass()

    asyncio.run(_run())

    # Fire the second callback (update_entity_information)
    second_cb = session.subscribe_userdefinedstate_callback.call_args_list[1][0][1]
    second_cb()

    # Entity should be marked unavailable and add_job called for removal
    assert sw._attr_available is False
    assert len(add_job_calls) == 1


def test_setup_wallthermostat_without_child_lock_skipped_old_lib():
    """Guard (0.4.112): a wall thermostat from an older boschshcpy (no child_lock
    attribute) must be skipped, not crash, when the lib is pinned to 0.2.117."""
    wt = _fake_device(name="OldWallThermo", dev_id="wt-old")
    # ensure the attribute is absent (older lib)
    if hasattr(wt, "child_lock"):
        del wt.child_lock
    session = _make_session(wallthermostats=[wt])
    entities, _ = _setup(session)
    keys = {e.entity_description.key for e in entities}
    assert "child_lock_thermostat" not in keys
