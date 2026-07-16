"""Unit tests for the switch platform (SHCSwitch / SHCUserDefinedStateSwitch).

Pure-unit, harness-free style throughout: entities are built via
``Cls.__new__(Cls)`` or direct construction with fake ``SimpleNamespace``/
``MagicMock`` devices, sessions and config entries — no HA test harness or
``tests.common``, matching this repo's ``-p no:homeassistant`` CI mode.

Coverage spans: SWITCH_TYPES descriptor metadata and on_key/on_value sanity;
is_on / async_turn_on / async_turn_off for every switch device type, including
AttributeError/None-service guards and SHCException/SHCConnectionError ->
HomeAssistantError translation; should_poll; unique_id/attr_name derivation;
the child-lock regressions (thermostat enum vs. ChildProtection bool, and the
micromodule/BSM wiring gap); the "APK batch 2-6" entities (energy saving mode,
warning suppression, nightly promise, humidity warning, swap inputs/outputs,
smart sensitivity, pre-alarm, tamper protection, intrusion alarm) including
their supports_*/value-is-None creation guards; async_setup_entry across every
device-helper branch (smart plugs, light switches, cameras, thermostats,
shutter contacts, micromodules, user-defined states) plus device-exclusion,
camera-registry suppression, and light-relay opt-in skipping; and
SHCUserDefinedStateSwitch end to end (init, is_on/turn_on/turn_off, should_poll,
device_info, async_added_to_hass/async_will_remove_from_hass subscriber
wiring, and the deleted-device unavailable path). Also covers the
user-defined-state entity_id slugification of device names containing spaces,
umlauts and uppercase letters.
"""

from __future__ import annotations

import asyncio
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from boschshcpy import (
    BypassService,
    CameraAmbientLightService,
    CameraFrontLightService,
    CameraLightService,
    CameraNotificationService,
    PowerSwitchService,
    PrivacyModeService,
    RoutingService,
    SHCShutterContact2Plus,
    SHCUserDefinedState,
    SilentModeService,
    ThermostatService,
)
from boschshcpy.exceptions import SHCConnectionError, SHCException
from homeassistant.components.switch import SwitchDeviceClass
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.util import slugify

from custom_components.bosch_shc.const import (
    DOMAIN,
    OPT_ALL_LIGHTS_AS_LIGHT,
    OPT_AUTOMATION_RULES_AS_ENTITIES,
    OPT_EXCLUDED_DEVICES,
    OPT_SUPPRESS_CAMERA_SWITCHES,
    OPT_TEMPERATURE_DROP_ENTITIES,
)
from custom_components.bosch_shc.switch import (
    SWITCH_TYPES,
    SHCAutomationRuleSwitch,
    SHCSwitch,
    SHCUserDefinedStateSwitch,
    async_setup_entry,
)

from .conftest import run_setup_entry



def _make_switch(description, **device_attrs):
    """Build a bare SHCSwitch (bypassing SHCEntity.__init__) with a fake device."""
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = SimpleNamespace(**device_attrs)
    sw.entity_description = description
    sw.entity_id = "switch.test"
    return sw


def _raising_property(exc_type=AttributeError):
    """Descriptor that raises on get and set."""

    class _Raiser:
        def __get__(self, obj, objtype=None):
            raise exc_type("service is None")

        def __set__(self, obj, value):
            raise exc_type("service is None")

    return _Raiser()


def _async_spy_device(on_key: str):
    """Return (device, mock) where device.async_set_<on_key> is an AsyncMock."""
    mock = AsyncMock()
    device = SimpleNamespace(**{f"async_set_{on_key}": mock})
    return device, mock


class _CameraEyesNoPrivacy:
    privacymode = _raising_property()


class _CameraEyesNoLight:
    cameralight = _raising_property()


class _Gen2NoFrontlight:
    camerafrontlight = _raising_property()


def _init_name_and_id(sw: SHCSwitch, attr_name=None) -> None:
    """Replicate the name/unique_id lines from SHCSwitch.__init__."""
    device = sw._device
    sw._attr_name = (
        f"{device.name}" if attr_name is None else f"{device.name} {attr_name}"
    )
    sw._attr_unique_id = (
        f"{device.root_device_id}_{device.id}"
        if attr_name is None
        else f"{device.root_device_id}_{device.id}_{attr_name.lower()}"
    )


class _RelayNoLoad:
    """Simulates a MicromoduleRelay whose PowerSwitch service is None.

    Both the getter and setter of `switchstate` raise AttributeError, which is
    what boschshcpy does when `self._powerswitch_service` is None.
    """

    switchstate = _raising_property()


class _Camera360NoPrivacy:
    """Simulates SHCCamera360 where _privacymode_service is None.

    boschshcpy's privacymode getter/setter both crash with AttributeError
    when _privacymode_service is None (no guard in the Camera360 class).
    """

    privacymode = _raising_property()


def _spy_switch(description, attr: str):
    """Return (switch, mock) where device.async_set_<attr> is an AsyncMock."""
    mock = AsyncMock()
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = SimpleNamespace(**{f"async_set_{attr}": mock})
    sw.entity_description = description
    sw.entity_id = "switch.spy"
    return sw, mock


class _FakeDevice:
    """Minimal fake SHCDevice that satisfies SHCEntity.__init__."""

    name = "Fake Device"
    id = "dev1"
    root_device_id = "root1"
    device_services = []
    status = "AVAILABLE"
    deleted = False
    manufacturer = "Bosch"
    device_model = "TestModel"


class _NoChildLock:
    child_lock = _raising_property()


class _NoBypass:
    bypass = _raising_property()


class _NoEnabled:
    enabled = _raising_property()


class _NoSilentMode:
    silentmode = _raising_property()


class _NoPetImmunity:
    pet_immunity_enabled = _raising_property()


class _NoState:
    state = _raising_property()


class _NoCameraNotification:
    cameranotification = _raising_property()


class _NoAmbientLight:
    cameraambientlight = _raising_property()


def _make_uds_switch(state=True, name="My State", dev_id="uds1", root_id="mac1"):
    """Build SHCUserDefinedStateSwitch with fake device/session/shc."""
    device = SimpleNamespace(
        name=name,
        id=dev_id,
        root_device_id=root_id,
        state=state,
        deleted=False,
    )
    shc_entry = SimpleNamespace(
        name="SHC Controller",
        id="shc_device_id",
        identifiers={("bosch_shc", "mac1")},
        manufacturer="Bosch",
        model="SHC 2",
    )
    # entry.runtime_data.shc_device backs the __init__ shc_device lookup
    fake_entry = SimpleNamespace(entry_id="entry1")
    fake_entry.runtime_data = SimpleNamespace(shc_device=shc_entry)
    hass = SimpleNamespace(
        config_entries=SimpleNamespace(async_get_entry=lambda eid: fake_entry)
    )

    session = SimpleNamespace(
        subscribe_userdefinedstate_callback=lambda *a, **kw: None,
        unsubscribe_userdefinedstate_callbacks=lambda *a, **kw: None,
    )

    sw = SHCUserDefinedStateSwitch(
        device=device,
        hass=hass,
        session=session,
        entry_id="entry1",
        description=SWITCH_TYPES["user_defined_state"],
    )
    return sw


def _switch(description, child_lock_value):
    # bypass SHCEntity.__init__ (needs hass/registry) — we only exercise is_on
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = SimpleNamespace(child_lock=child_lock_value)
    sw.entity_description = description
    return sw


def _fake_device(**kwargs):
    defaults = dict(name="Dev", id="dev1", root_device_id="root1", serial="SER1",
                    supports_silentmode=False)
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _setup_switch(mock_config_entry, mock_session):
    """Run switch.py's async_setup_entry using the shared conftest fixtures.

    Replaces this file's old bespoke trio of session/entry/hass builders
    (_make_session/_make_hass_and_entry/_async_setup+_setup,
    _make_setup_session/_make_setup_hass_and_entry/_async_setup_full+_setup_full,
    _make_switch_session/_run_switch_setup) — all three were structurally
    identical (an all-empty device_helper bucket dict + a bare hass/entry
    wired for async_setup_entry), just accidentally diverged over time.

    switch.py's async_setup_entry needs a few session/entry fields the shared
    mock_session/mock_config_entry fixtures don't set, because this platform
    is the only one that also manages its own subscriber list for
    dynamically-added user-defined-state switches:
    session.userdefinedstates, session.subscribe, session._subscribers, and
    config_entry.async_on_unload. Default them here if a test didn't already
    set/override them. Also wires hass.config_entries so
    SHCUserDefinedStateSwitch (constructed inline by
    async_add_userdefinedstateswitch for any pre-existing
    session.userdefinedstates) can resolve config_entry.runtime_data.shc_device.
    """
    if not hasattr(mock_session, "userdefinedstates"):
        mock_session.userdefinedstates = []
    if not hasattr(mock_session, "subscribe"):
        mock_session.subscribe = MagicMock()
    if not hasattr(mock_session, "_subscribers"):
        mock_session._subscribers = []
    if not hasattr(mock_config_entry, "async_on_unload"):
        mock_config_entry.async_on_unload = MagicMock()
    if not hasattr(mock_config_entry.runtime_data, "shc_device"):
        mock_config_entry.runtime_data.shc_device = SimpleNamespace(
            name="SHC",
            id="shc_dev",
            identifiers={(DOMAIN, "shc_dev")},
            manufacturer="Bosch",
            model="SHC",
        )
    mock_config_entry.runtime_data.session = mock_session
    hass = SimpleNamespace(
        config_entries=SimpleNamespace(async_get_entry=lambda eid: mock_config_entry)
    )
    collected: list = []

    def add(entities, *args, **kwargs):
        collected.extend(entities)

    with patch(PATCH_MIGRATE, new=AsyncMock(return_value=None)):
        asyncio.run(async_setup_entry(hass, mock_config_entry, add))
    return collected


def _keys(entities):
    return [e.entity_description.key for e in entities]


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


def _make_exclusion_session(*, userdefinedstates=None):
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
        climate_controls=[],
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
    # entry.runtime_data backs both async_setup_entry's own
    # config_entry.runtime_data.session read and SHCUserDefinedStateSwitch's
    # hass.config_entries.async_get_entry(entry_id).runtime_data.shc_device.
    entry.runtime_data = SimpleNamespace(
        session=session, shc_device=shc_device, title=entry.title
    )

    async def _async_none(*args, **kwargs):
        return None

    hass = MagicMock()
    hass.async_add_executor_job = AsyncMock(return_value=None)
    hass.config_entries = MagicMock()
    hass.config_entries.async_get_entry = MagicMock(return_value=entry)
    hass.loop = MagicMock()
    return hass


PATCH_MIGRATE = "custom_components.bosch_shc.switch.async_migrate_to_new_unique_id"


PATCH_DEVICE_EXCLUDED = "custom_components.bosch_shc.switch.device_excluded"


async def _run_setup(hass, entry, async_add_entities):
    from custom_components.bosch_shc.switch import async_setup_entry
    await async_setup_entry(hass, entry, async_add_entities)


def _run(coro):
    return asyncio.run(coro)


def _fake_setup_device(name="Dev", dev_id="dev1", root_id="root1", serial="SER1"):
    """Minimal fake SHC device."""
    return SimpleNamespace(
        name=name,
        id=dev_id,
        root_device_id=root_id,
        serial=serial,
        supports_silentmode=False,
    )


def _fake_thermostat(name="Thermo", dev_id="therm1", root_id="root1", silent=True):
    d = _fake_setup_device(name=name, dev_id=dev_id, root_id=root_id)
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
    d = _fake_setup_device(name=name, dev_id=dev_id, root_id=root_id)
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


def _make_fake_hass(shc_device, entry_id="E1"):
    """Return a bare hass fake whose config_entries.async_get_entry(entry_id)
    resolves to a fake config entry exposing runtime_data.shc_device — the
    minimum SHCUserDefinedStateSwitch.__init__ needs when constructed
    directly (outside async_setup_entry)."""
    fake_entry = SimpleNamespace(entry_id=entry_id)
    fake_entry.runtime_data = SimpleNamespace(shc_device=shc_device)
    return SimpleNamespace(
        config_entries=SimpleNamespace(async_get_entry=lambda eid: fake_entry)
    )


def _make_setup_uds_switch(name="TestState", dev_id="udx1", root_id="mac9", state=True):
    """Construct SHCUserDefinedStateSwitch directly with a fake device/session."""
    uds = _fake_uds(name=name, dev_id=dev_id, root_id=root_id, state=state)
    shc_dev = SimpleNamespace(
        name="SHC",
        id="shc_dev",
        identifiers={("bosch_shc", "shc_dev")},
        manufacturer="Bosch",
        model="SHC",
    )
    hass = _make_fake_hass(shc_dev)
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


VALID_SLUG_RE = re.compile(r"^[a-z0-9_]+$")


def _make_slug(name: str) -> str:
    """Replicate the entity_id object-id production used in SHCUserDefinedStateSwitch."""
    return f"userdefinedstate_{slugify(name)}"


def _fake_device_gaps(**kwargs):
    """From test_apk_coverage_gaps.py — generic fake device for switch gap tests."""
    base = dict(
        id="dev1",
        root_device_id="root1",
        name="FakeDev",
        device_services=[],
        serial="SER1",
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def _excl(*ids):
    return {OPT_EXCLUDED_DEVICES: list(ids)}


_FAKE_DEVICE = SimpleNamespace(
    root_device_id="root-1",
    id="hdm:ZigBee:dev1",
    name="Schlafzimmerfenster",
    status="AVAILABLE",
)


def _fake_dev(dev_id="dev1", root_id="root1", serial="SER1", **kw):
    """From test_coverage_gaps.py — generic fake device for setup-loop tests."""
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


def _make_md2_device(**kwargs):
    """From test_motion_detector2.py — fake SHCMotionDetector2-shaped device."""
    defaults = dict(
        name="Motion Detector II",
        id="hdm:ZigBee:000000000000abcd",
        root_device_id="64-da-a0-xx-xx-xx",
        occupied=False,
        last_occupancy_change_time="2026-06-20T12:00:00.000Z",
        binaryswitch=False,
        multi_level_switch=50,
        pet_immunity_enabled=False,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_pet_switch(**device_kwargs):
    dev = _make_md2_device(**device_kwargs)
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = dev
    sw.entity_description = SWITCH_TYPES["pet_immunity_enabled"]
    sw.entity_id = "switch.test_pet"
    return sw


def _fake_md2(**kwargs):
    """From test_md2_detection_tamper_pollcontrol.py."""
    defaults = dict(
        name="MD2",
        id="md1",
        root_device_id="root1",
        serial="SER1",
        supports_batterylevel=False,
        supports_silentmode=False,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)




# ---------------------------------------------------------------------------
# SmartPlug (smartplug / smartplug_routing)
# ---------------------------------------------------------------------------


def test_smartplug_is_on_true():
    State = PowerSwitchService.State
    sw = _make_switch(SWITCH_TYPES["smartplug"], switchstate=State.ON)
    assert sw.is_on is True


def test_smartplug_is_on_false():
    State = PowerSwitchService.State
    sw = _make_switch(SWITCH_TYPES["smartplug"], switchstate=State.OFF)
    assert sw.is_on is False


def test_smartplug_routing_is_on_enabled():
    State = RoutingService.State
    sw = _make_switch(SWITCH_TYPES["smartplug_routing"], routing=State.ENABLED)
    assert sw.is_on is True


def test_smartplug_routing_is_on_disabled():
    State = RoutingService.State
    sw = _make_switch(SWITCH_TYPES["smartplug_routing"], routing=State.DISABLED)
    assert sw.is_on is False


def test_turn_on_sets_attr_true():
    """async_turn_on must await device.async_set_switchstate(True)."""
    dev, mock = _async_spy_device("switchstate")
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = dev
    sw.entity_description = SWITCH_TYPES["smartplug"]
    sw.entity_id = "switch.test"
    asyncio.run(sw.async_turn_on())
    mock.assert_awaited_once_with(True)


def test_turn_off_sets_attr_false():
    """async_turn_off must await device.async_set_switchstate(False)."""
    dev, mock = _async_spy_device("switchstate")
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = dev
    sw.entity_description = SWITCH_TYPES["smartplug"]
    sw.entity_id = "switch.test"
    asyncio.run(sw.async_turn_off())
    mock.assert_awaited_once_with(False)


def test_turn_on_shc_exception_raises_home_assistant_error():
    """A real API-level rejection must surface as HomeAssistantError, not raw."""
    dev = SimpleNamespace(
        name="Test Switch",
        async_set_switchstate=AsyncMock(side_effect=SHCException("rejected")),
    )
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = dev
    sw.entity_description = SWITCH_TYPES["smartplug"]
    sw.entity_id = "switch.test"
    sw._attr_name = None

    with pytest.raises(HomeAssistantError):
        asyncio.run(sw.async_turn_on())


def test_turn_off_shc_connection_error_raises_home_assistant_error():
    """A comms failure on turn_off must surface as HomeAssistantError, not raw."""
    dev = SimpleNamespace(
        name="Test Switch",
        async_set_switchstate=AsyncMock(side_effect=SHCConnectionError("no route")),
    )
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = dev
    sw.entity_description = SWITCH_TYPES["smartplug"]
    sw.entity_id = "switch.test"
    sw._attr_name = None

    with pytest.raises(HomeAssistantError):
        asyncio.run(sw.async_turn_off())


def test_should_poll_smartplug_is_false():
    sw = _make_switch(SWITCH_TYPES["smartplug"], switchstate=None)
    assert sw.should_poll is False


def test_setup_smart_plugs_creates_two_entities_per_plug(mock_config_entry, mock_session):
    plug = _fake_setup_device(name="Plug A", dev_id="plug1")
    mock_session.device_helper.smart_plugs = [plug]
    entities = _setup_switch(mock_config_entry, mock_session)
    assert len(entities) == 2
    types = {e.entity_description.key for e in entities}
    assert types == {"smartplug", "smartplug_routing"}


def test_setup_smart_plugs_two_plugs_four_entities(mock_config_entry, mock_session):
    p1 = _fake_setup_device(name="Plug 1", dev_id="p1")
    p2 = _fake_setup_device(name="Plug 2", dev_id="p2")
    mock_session.device_helper.smart_plugs = [p1, p2]
    entities = _setup_switch(mock_config_entry, mock_session)
    assert len(entities) == 4


def test_setup_smart_plug_entity_types_are_shcswitch(mock_config_entry, mock_session):
    plug = _fake_setup_device(name="Plug", dev_id="plug1")
    mock_session.device_helper.smart_plugs = [plug]
    entities = _setup_switch(mock_config_entry, mock_session)
    assert all(isinstance(e, SHCSwitch) for e in entities)


def test_shcswitch_unique_id_for_smartplug(mock_config_entry, mock_session):
    plug = _fake_setup_device(name="Plug A", dev_id="plugX", root_id="rootY")
    mock_session.device_helper.smart_plugs = [plug]
    entities = _setup_switch(mock_config_entry, mock_session)
    smartplug_ent = next(
        e for e in entities if e.entity_description.key == "smartplug"
    )
    assert smartplug_ent._attr_unique_id == "rootY_plugX"


def test_shcswitch_unique_id_for_routing(mock_config_entry, mock_session):
    plug = _fake_setup_device(name="Plug B", dev_id="plugZ", root_id="rootQ")
    mock_session.device_helper.smart_plugs = [plug]
    entities = _setup_switch(mock_config_entry, mock_session)
    routing_ent = next(
        e for e in entities if e.entity_description.key == "smartplug_routing"
    )
    assert routing_ent._attr_unique_id == "rootQ_plugZ_routing"




# ---------------------------------------------------------------------------
# SmartPlugCompact
# ---------------------------------------------------------------------------


def test_smartplugcompact_is_on_true():
    State = PowerSwitchService.State
    sw = _make_switch(SWITCH_TYPES["smartplugcompact"], switchstate=State.ON)
    assert sw.is_on is True


def test_smartplugcompact_is_on_false():
    State = PowerSwitchService.State
    sw = _make_switch(SWITCH_TYPES["smartplugcompact"], switchstate=State.OFF)
    assert sw.is_on is False


def test_setup_smart_plug_compact_one_entity(mock_config_entry, mock_session):
    sw = _fake_setup_device(name="Compact", dev_id="spc1")
    mock_session.device_helper.smart_plugs_compact = [sw]
    entities = _setup_switch(mock_config_entry, mock_session)
    assert len(entities) == 1
    assert entities[0].entity_description.key == "smartplugcompact"




# ---------------------------------------------------------------------------
# MicromoduleRelay
# ---------------------------------------------------------------------------


def test_micromodule_relay_is_on_true():
    State = PowerSwitchService.State
    sw = _make_switch(SWITCH_TYPES["micromodule_relay_switch"], switchstate=State.ON)
    assert sw.is_on is True


def test_micromodule_relay_is_on_false():
    State = PowerSwitchService.State
    sw = _make_switch(SWITCH_TYPES["micromodule_relay_switch"], switchstate=State.OFF)
    assert sw.is_on is False


def test_relay_no_load_is_on_returns_none():
    """is_on must return None (not raise) when switchstate getter raises."""
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = _RelayNoLoad()
    sw.entity_description = SWITCH_TYPES["micromodule_relay_switch"]
    assert sw.is_on is None


def test_relay_no_load_turn_on_does_not_raise():
    """async_turn_on must NOT propagate AttributeError when service is None."""
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = _RelayNoLoad()
    sw.entity_description = SWITCH_TYPES["micromodule_relay_switch"]
    sw.entity_id = "switch.relay_test"
    asyncio.run(sw.async_turn_on())  # must not raise


def test_relay_no_load_turn_off_does_not_raise():
    """async_turn_off must NOT propagate AttributeError when service is None."""
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = _RelayNoLoad()
    sw.entity_description = SWITCH_TYPES["micromodule_relay_switch"]
    sw.entity_id = "switch.relay_test"
    asyncio.run(sw.async_turn_off())  # must not raise


def test_relay_with_load_turn_on_calls_setter():
    """async_turn_on must await async_set_switchstate(True) when service exists."""
    mock = AsyncMock()
    device = SimpleNamespace(async_set_switchstate=mock)
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = device
    sw.entity_description = SWITCH_TYPES["micromodule_relay_switch"]
    sw.entity_id = "switch.relay_loaded"
    asyncio.run(sw.async_turn_on())
    mock.assert_awaited_once_with(True)


def test_relay_with_load_turn_off_calls_setter():
    """async_turn_off must await async_set_switchstate(False) when service exists."""
    mock = AsyncMock()
    device = SimpleNamespace(async_set_switchstate=mock)
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = device
    sw.entity_description = SWITCH_TYPES["micromodule_relay_switch"]
    sw.entity_id = "switch.relay_loaded"
    asyncio.run(sw.async_turn_off())
    mock.assert_awaited_once_with(False)


def test_setup_micromodule_relay_creates_two_entities(mock_config_entry, mock_session):
    sw = _fake_setup_device(name="Relay", dev_id="mm1")
    mock_session.device_helper.micromodule_relays = [sw]
    entities = _setup_switch(mock_config_entry, mock_session)
    keys = {e.entity_description.key for e in entities}
    assert "micromodule_relay_switch" in keys
    assert "child_lock" in keys




# ---------------------------------------------------------------------------
# LightSwitch
# ---------------------------------------------------------------------------


def test_lightswitch_is_on_true():
    State = PowerSwitchService.State
    sw = _make_switch(SWITCH_TYPES["lightswitch"], switchstate=State.ON)
    assert sw.is_on is True


def test_lightswitch_is_on_false():
    State = PowerSwitchService.State
    sw = _make_switch(SWITCH_TYPES["lightswitch"], switchstate=State.OFF)
    assert sw.is_on is False


def test_setup_light_switch_bsm_one_entity(mock_config_entry, mock_session):
    sw = _fake_setup_device(name="Light BSM", dev_id="lbsm1")
    mock_session.device_helper.light_switches_bsm = [sw]
    entities = _setup_switch(mock_config_entry, mock_session)
    assert len(entities) == 2  # lightswitch + child_lock
    keys = {e.entity_description.key for e in entities}
    assert "lightswitch" in keys
    assert "child_lock" in keys


def test_setup_micromodule_light_attached_one_entity(mock_config_entry, mock_session):
    sw = _fake_setup_device(name="MM Light", dev_id="mmla1")
    mock_session.device_helper.micromodule_light_attached = [sw]
    entities = _setup_switch(mock_config_entry, mock_session)
    keys = {e.entity_description.key for e in entities}
    assert "lightswitch" in keys
    assert "child_lock" in keys


class TestSwitchMicromoduleLightControlsDeviceExcluded:
    """switch.py line 454 — micromodule_light_controls device_excluded continue."""

    def test_excluded_light_control_not_added(self, mock_config_entry, mock_session):
        dev = _fake_device_gaps(id="mlc-excl", swap_inputs=False)
        mock_session.device_helper.micromodule_light_controls = [dev]
        mock_config_entry.options = _excl("mlc-excl")
        entities = _setup_switch(mock_config_entry, mock_session)
        ids = [getattr(getattr(e, "_device", None), "id", None) for e in entities]
        assert "mlc-excl" not in ids


class TestSwitchLightRelayOptInSkip:
    """Line 418: light_switch_as_light=True → switch skipped (continue)."""

    def _run_switch_setup(self, bsm_lights, options):
        from custom_components.bosch_shc.switch import async_setup_entry

        dh = MagicMock()
        dh.smart_plugs = []
        dh.smart_plugs_compact = []
        dh.micromodule_relays = []
        dh.micromodule_impulse_relays = []
        dh.micromodule_dimmers = []
        dh.light_switches_bsm = bsm_lights
        dh.micromodule_light_attached = []
        dh.micromodule_light_controls = []
        dh.camera_eyes = []
        dh.camera_360 = []
        dh.camera_outdoor_gen2 = []
        dh.presence_simulation_system = None
        dh.shutter_contacts2 = []
        dh.thermostats = []
        dh.roomthermostats = []
        dh.wallthermostats = []
        dh.motion_detectors2 = []
        dh.universal_switches = []
        dh.twinguards = []
        dh.smoke_detectors = []
        dh.smoke_detection_system = None

        session = MagicMock()
        session.device_helper = dh
        session.userdefinedstates = []
        session._subscribers = []
        session.subscribe = MagicMock()

        hass = _fake_hass(session=session)
        entry = _fake_entry(hass=hass, options=options)
        entry.async_on_unload = MagicMock()

        with (
            patch(
                "custom_components.bosch_shc.switch.async_migrate_to_new_unique_id",
                new_callable=AsyncMock,
            ),
            patch(
                "custom_components.bosch_shc.switch.async_remove_stale_entity",
                new_callable=AsyncMock,
            ),
        ):
            collected = []
            _run(async_setup_entry(hass, entry, lambda ents, **kw: collected.extend(ents)))
        return collected

    def test_relay_opted_in_as_light_skipped_in_switch(self):
        """Line 418: light relay opted in as light → no SHCSwitch created for it."""
        dev = _fake_dev("bsm1")
        # Opt in via OPT_ALL_LIGHTS_AS_LIGHT → switch skips it
        collected = self._run_switch_setup(
            [dev], options={OPT_ALL_LIGHTS_AS_LIGHT: True}
        )
        # No switch entity for bsm1 (ChildLock config entities are allowed)
        switch_ids = [getattr(e, "_attr_unique_id", "") for e in collected]
        assert not any(
            "bsm1" in sid
            and "swapoutputs" not in sid.lower()
            and "childlock" not in sid.lower()
            for sid in switch_ids
        )


class TestSHCSwitchTurnOnClientError:
    """Lines 991-992: SHCSwitch.async_turn_on aiohttp.ClientError branch."""

    def _make_switch(self, on_key="switchstate", on_value=True):
        from homeassistant.components.switch import SwitchDeviceClass

        from custom_components.bosch_shc.switch import (
            SHCSwitch,
        )

        desc = SimpleNamespace(
            on_key=on_key,
            on_value=on_value,
            translation_key="lightswitch",
            should_poll=False,
            key="lightswitch",
            name="Light Switch",
            device_class=SwitchDeviceClass.SWITCH,
        )
        sw = SHCSwitch.__new__(SHCSwitch)
        sw.entity_description = desc
        sw.entity_id = "switch.test"
        sw._has_async_update = False
        return sw

    def test_turn_on_client_error_logged_not_raised(self):
        """Lines 991-995: aiohttp.ClientError in async_set_switchstate → debug log."""
        sw = self._make_switch()
        dev = MagicMock()
        dev.async_set_switchstate = AsyncMock(side_effect=aiohttp.ClientError("err"))
        sw._device = dev
        _run(sw.async_turn_on())  # must not raise

    def test_turn_off_client_error_logged_not_raised(self):
        """Lines 1012-1016: aiohttp.ClientError in async_set_switchstate → debug log."""
        sw = self._make_switch()
        dev = MagicMock()
        dev.async_set_switchstate = AsyncMock(side_effect=aiohttp.ClientError("err"))
        sw._device = dev
        _run(sw.async_turn_off())  # must not raise




# ---------------------------------------------------------------------------
# CameraEyes (privacy / cameralight / notification)
# ---------------------------------------------------------------------------


def test_cameraeyes_privacy_on():
    """Privacy DISABLED → camera is ON → is_on True."""
    State = PrivacyModeService.State
    sw = _make_switch(SWITCH_TYPES["cameraeyes"], privacymode=State.DISABLED)
    assert sw.is_on is True


def test_cameraeyes_privacy_off():
    """Privacy ENABLED → camera is OFF → is_on False."""
    State = PrivacyModeService.State
    sw = _make_switch(SWITCH_TYPES["cameraeyes"], privacymode=State.ENABLED)
    assert sw.is_on is False


def test_cameraeyes_cameralight_on():
    State = CameraLightService.State
    sw = _make_switch(SWITCH_TYPES["cameraeyes_cameralight"], cameralight=State.ON)
    assert sw.is_on is True


def test_cameraeyes_cameralight_off():
    State = CameraLightService.State
    sw = _make_switch(SWITCH_TYPES["cameraeyes_cameralight"], cameralight=State.OFF)
    assert sw.is_on is False


def test_cameraeyes_notification_enabled():
    State = CameraNotificationService.State
    sw = _make_switch(
        SWITCH_TYPES["cameraeyes_notification"], cameranotification=State.ENABLED
    )
    assert sw.is_on is True


def test_cameraeyes_notification_disabled():
    State = CameraNotificationService.State
    sw = _make_switch(
        SWITCH_TYPES["cameraeyes_notification"], cameranotification=State.DISABLED
    )
    assert sw.is_on is False


def test_none_guard_cameraeyes_privacymode_is_on():
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = _CameraEyesNoPrivacy()
    sw.entity_description = SWITCH_TYPES["cameraeyes"]
    assert sw.is_on is None


def test_none_guard_cameraeyes_privacymode_turn_on():
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = _CameraEyesNoPrivacy()
    sw.entity_description = SWITCH_TYPES["cameraeyes"]
    sw.entity_id = "switch.test"
    asyncio.run(sw.async_turn_on())  # must not raise


def test_none_guard_cameraeyes_privacymode_turn_off():
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = _CameraEyesNoPrivacy()
    sw.entity_description = SWITCH_TYPES["cameraeyes"]
    sw.entity_id = "switch.test"
    asyncio.run(sw.async_turn_off())  # must not raise


def test_none_guard_cameraeyes_cameralight_is_on():
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = _CameraEyesNoLight()
    sw.entity_description = SWITCH_TYPES["cameraeyes_cameralight"]
    assert sw.is_on is None


def test_none_guard_cameraeyes_cameralight_turn_on():
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = _CameraEyesNoLight()
    sw.entity_description = SWITCH_TYPES["cameraeyes_cameralight"]
    sw.entity_id = "switch.test"
    asyncio.run(sw.async_turn_on())  # must not raise


def test_should_poll_camera_is_true():
    sw = _make_switch(SWITCH_TYPES["cameraeyes"], privacymode=None)
    assert sw.should_poll is True


def test_should_poll_cameraeyes_frontlight_is_true():
    sw = _make_switch(SWITCH_TYPES["cameraeyes_cameralight"], cameralight=None)
    assert sw.should_poll is True


def test_cameraeyes_on_value_is_privacy_disabled():
    """Camera-on = privacy DISABLED (inverted logic)."""
    assert SWITCH_TYPES["cameraeyes"].on_value is (
        PrivacyModeService.State.DISABLED
    )


def test_setup_camera_eyes_three_entities(mock_config_entry, mock_session):
    cam = _fake_setup_device(name="Cam Eyes", dev_id="ceyes1")
    mock_session.device_helper.camera_eyes = [cam]
    entities = _setup_switch(mock_config_entry, mock_session)
    keys = {e.entity_description.key for e in entities}
    assert keys == {"cameraeyes", "cameraeyes_cameralight", "cameraeyes_notification"}


def test_setup_camera_eyes_entity_names(mock_config_entry, mock_session):
    cam = _fake_setup_device(name="Eyes Outdoor", dev_id="ceyes2")
    mock_session.device_helper.camera_eyes = [cam]
    entities = _setup_switch(mock_config_entry, mock_session)
    # With _attr_has_entity_name=True, _attr_name holds only the feature label
    # (None = primary entity; HA prepends device name for display).
    names = {e._attr_name for e in entities}
    assert None in names          # cameraeyes (primary: _attr_name=None)
    assert "Light" in names       # cameraeyes_cameralight
    assert "Notification" in names  # cameraeyes_notification


def test_shcswitch_unique_id_for_camera_notification(mock_config_entry, mock_session):
    cam = _fake_setup_device(name="Cam", dev_id="camA", root_id="rootA")
    mock_session.device_helper.camera_eyes = [cam]
    entities = _setup_switch(mock_config_entry, mock_session)
    notif_ent = next(
        e for e in entities if e.entity_description.key == "cameraeyes_notification"
    )
    assert notif_ent._attr_unique_id == "rootA_camA_notification"




# ---------------------------------------------------------------------------
# Camera360 (privacy / notification)
# ---------------------------------------------------------------------------


def test_camera360_privacy_on():
    State = PrivacyModeService.State
    sw = _make_switch(SWITCH_TYPES["camera360"], privacymode=State.DISABLED)
    assert sw.is_on is True


def test_camera360_privacy_off():
    State = PrivacyModeService.State
    sw = _make_switch(SWITCH_TYPES["camera360"], privacymode=State.ENABLED)
    assert sw.is_on is False


def test_camera360_notification_enabled():
    State = CameraNotificationService.State
    sw = _make_switch(
        SWITCH_TYPES["camera360_notification"], cameranotification=State.ENABLED
    )
    assert sw.is_on is True


def test_camera360_notification_disabled():
    State = CameraNotificationService.State
    sw = _make_switch(
        SWITCH_TYPES["camera360_notification"], cameranotification=State.DISABLED
    )
    assert sw.is_on is False


def test_should_poll_camera360_is_true():
    sw = _make_switch(SWITCH_TYPES["camera360"], privacymode=None)
    assert sw.should_poll is True


def test_camera360_on_value_is_privacy_disabled():
    assert SWITCH_TYPES["camera360"].on_value is (
        PrivacyModeService.State.DISABLED
    )


def test_camera360_no_privacy_service_is_on_returns_none():
    """is_on must return None (not raise) when privacymode getter raises."""
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = _Camera360NoPrivacy()
    sw.entity_description = SWITCH_TYPES["camera360"]
    assert sw.is_on is None


def test_camera360_no_privacy_service_turn_on_does_not_raise():
    """async_turn_on must NOT propagate AttributeError when privacy service is None."""
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = _Camera360NoPrivacy()
    sw.entity_description = SWITCH_TYPES["camera360"]
    sw.entity_id = "switch.cam360_test"
    asyncio.run(sw.async_turn_on())  # must not raise


def test_camera360_no_privacy_service_turn_off_does_not_raise():
    """async_turn_off must NOT propagate AttributeError when privacy service is None."""
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = _Camera360NoPrivacy()
    sw.entity_description = SWITCH_TYPES["camera360"]
    sw.entity_id = "switch.cam360_test"
    asyncio.run(sw.async_turn_off())  # must not raise


def test_camera360_notification_none_is_on_returns_none():
    """is_on for camera360_notification must return None when service getter raises."""
    sw = SHCSwitch.__new__(SHCSwitch)

    class _NoNotification:
        cameranotification = _raising_property()

    sw._device = _NoNotification()
    sw.entity_description = SWITCH_TYPES["camera360_notification"]
    assert sw.is_on is None


def test_camera360_notification_none_turn_on_does_not_raise():
    """async_turn_on for camera360_notification must not raise when service is absent."""
    sw = SHCSwitch.__new__(SHCSwitch)

    class _NoNotification:
        cameranotification = _raising_property()
        # no async_set_cameranotification → AttributeError caught by guard

    sw._device = _NoNotification()
    sw.entity_description = SWITCH_TYPES["camera360_notification"]
    sw.entity_id = "switch.cam360_notif"
    asyncio.run(sw.async_turn_on())  # must not raise


def test_setup_camera_360_two_entities(mock_config_entry, mock_session):
    cam = _fake_setup_device(name="Cam 360", dev_id="c360_1")
    mock_session.device_helper.camera_360 = [cam]
    entities = _setup_switch(mock_config_entry, mock_session)
    keys = {e.entity_description.key for e in entities}
    assert keys == {"camera360", "camera360_notification"}




# ---------------------------------------------------------------------------
# CameraOutdoorGen2 (privacy / frontlight / ambientlight)
# ---------------------------------------------------------------------------


def test_cameraoutdoorgen2_privacy_on():
    State = PrivacyModeService.State
    sw = _make_switch(SWITCH_TYPES["cameraoutdoorgen2"], privacymode=State.DISABLED)
    assert sw.is_on is True


def test_cameraoutdoorgen2_privacy_off():
    State = PrivacyModeService.State
    sw = _make_switch(SWITCH_TYPES["cameraoutdoorgen2"], privacymode=State.ENABLED)
    assert sw.is_on is False


def test_cameraoutdoorgen2_frontlight_on():
    State = CameraFrontLightService.State
    sw = _make_switch(
        SWITCH_TYPES["cameraoutdoorgen2_camerafrontlight"], camerafrontlight=State.ON
    )
    assert sw.is_on is True


def test_cameraoutdoorgen2_frontlight_off():
    State = CameraFrontLightService.State
    sw = _make_switch(
        SWITCH_TYPES["cameraoutdoorgen2_camerafrontlight"], camerafrontlight=State.OFF
    )
    assert sw.is_on is False


def test_cameraoutdoorgen2_ambientlight_on():
    State = CameraAmbientLightService.State
    sw = _make_switch(
        SWITCH_TYPES["cameraoutdoorgen2_cameraambientlight"], cameraambientlight=State.ON
    )
    assert sw.is_on is True


def test_cameraoutdoorgen2_ambientlight_off():
    State = CameraAmbientLightService.State
    sw = _make_switch(
        SWITCH_TYPES["cameraoutdoorgen2_cameraambientlight"], cameraambientlight=State.OFF
    )
    assert sw.is_on is False


def test_none_guard_cameraoutdoorgen2_frontlight_is_on():
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = _Gen2NoFrontlight()
    sw.entity_description = SWITCH_TYPES["cameraoutdoorgen2_camerafrontlight"]
    assert sw.is_on is None


def test_none_guard_cameraoutdoorgen2_frontlight_turn_on():
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = _Gen2NoFrontlight()
    sw.entity_description = SWITCH_TYPES["cameraoutdoorgen2_camerafrontlight"]
    sw.entity_id = "switch.test"
    asyncio.run(sw.async_turn_on())  # must not raise


def test_none_guard_cameraoutdoorgen2_frontlight_turn_off():
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = _Gen2NoFrontlight()
    sw.entity_description = SWITCH_TYPES["cameraoutdoorgen2_camerafrontlight"]
    sw.entity_id = "switch.test"
    asyncio.run(sw.async_turn_off())  # must not raise


def test_should_poll_cameraoutdoorgen2_is_true():
    sw = _make_switch(SWITCH_TYPES["cameraoutdoorgen2"], privacymode=None)
    assert sw.should_poll is True


def test_cameraoutdoorgen2_on_value_is_privacy_disabled():
    assert SWITCH_TYPES["cameraoutdoorgen2"].on_value is (
        PrivacyModeService.State.DISABLED
    )


def test_setup_camera_outdoor_gen2_three_entities(mock_config_entry, mock_session):
    cam = _fake_setup_device(name="Gen2 Cam", dev_id="gen2_1")
    mock_session.device_helper.camera_outdoor_gen2 = [cam]
    entities = _setup_switch(mock_config_entry, mock_session)
    keys = {e.entity_description.key for e in entities}
    assert keys == {
        "cameraoutdoorgen2",
        "cameraoutdoorgen2_camerafrontlight",
        "cameraoutdoorgen2_cameraambientlight",
    }


def test_setup_camera_outdoor_gen2_attr_names(mock_config_entry, mock_session):
    cam = _fake_setup_device(name="Eyes Outdoor II", dev_id="gen2_2")
    mock_session.device_helper.camera_outdoor_gen2 = [cam]
    entities = _setup_switch(mock_config_entry, mock_session)
    # With _attr_has_entity_name=True, _attr_name holds only the feature label.
    names = {e._attr_name for e in entities}
    assert None in names            # cameraoutdoorgen2 (primary)
    assert "Frontlight" in names    # camerafrontlight
    assert "AmbientLight" in names  # cameraambientlight




# ---------------------------------------------------------------------------
# PresenceSimulation
# ---------------------------------------------------------------------------


def test_presencesimulation_is_on_true():
    sw = _make_switch(SWITCH_TYPES["presencesimulation"], enabled=True)
    assert sw.is_on is True


def test_presencesimulation_is_on_false():
    sw = _make_switch(SWITCH_TYPES["presencesimulation"], enabled=False)
    assert sw.is_on is False


def test_turn_on_presencesimulation_writes_true():
    dev, mock = _async_spy_device("enabled")
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = dev
    sw.entity_description = SWITCH_TYPES["presencesimulation"]
    sw.entity_id = "switch.test"
    asyncio.run(sw.async_turn_on())
    mock.assert_awaited_once_with(True)


def test_turn_off_presencesimulation_writes_false():
    dev, mock = _async_spy_device("enabled")
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = dev
    sw.entity_description = SWITCH_TYPES["presencesimulation"]
    sw.entity_id = "switch.test"
    asyncio.run(sw.async_turn_off())
    mock.assert_awaited_once_with(False)


def test_should_poll_presencesimulation_is_false():
    sw = _make_switch(SWITCH_TYPES["presencesimulation"], enabled=False)
    assert sw.should_poll is False


def test_presencesimulation_on_value_is_bool_true():
    assert SWITCH_TYPES["presencesimulation"].on_value is True


def test_setup_presence_simulation_when_present(mock_config_entry, mock_session):
    dev = _fake_setup_device(name="PresenceSim", dev_id="pss1")
    mock_session.device_helper.presence_simulation_system = dev
    entities = _setup_switch(mock_config_entry, mock_session)
    assert len(entities) == 1
    assert entities[0].entity_description.key == "presencesimulation"


def test_setup_presence_simulation_absent(mock_config_entry, mock_session):
    mock_session.device_helper.presence_simulation_system = None
    entities = _setup_switch(mock_config_entry, mock_session)
    assert entities == []




# ---------------------------------------------------------------------------
# Bypass (bypass / bypass_infinite)
# ---------------------------------------------------------------------------


def test_bypass_active():
    State = BypassService.State
    sw = _make_switch(SWITCH_TYPES["bypass"], bypass=State.BYPASS_ACTIVE)
    assert sw.is_on is True


def test_bypass_inactive():
    State = BypassService.State
    sw = _make_switch(SWITCH_TYPES["bypass"], bypass=State.BYPASS_INACTIVE)
    assert sw.is_on is False


def test_should_poll_bypass_is_false():
    State = BypassService.State
    sw = _make_switch(SWITCH_TYPES["bypass"], bypass=State.BYPASS_INACTIVE)
    assert sw.should_poll is False


def test_bypass_on_value_is_bypass_active():
    assert SWITCH_TYPES["bypass"].on_value is (
        BypassService.State.BYPASS_ACTIVE
    )


def test_setup_shutter_contact2_base_two_entities(mock_config_entry, mock_session):
    """hass#120 audit: bypass_infinite is now wired in alongside bypass."""
    sw = _fake_shutter2(name="Shutter", dev_id="sh1")
    mock_session.device_helper.shutter_contacts2 = [sw]
    entities = _setup_switch(mock_config_entry, mock_session)
    keys = {e.entity_description.key for e in entities}
    assert keys == {"bypass", "bypass_infinite"}


def test_setup_shutter_contact2plus_three_entities(mock_config_entry, mock_session):
    """hass#120 audit: bypass_infinite is now wired in alongside bypass +
    vibration_enabled."""
    sw = _fake_shutter2plus(name="Shutter+", dev_id="shp1")
    mock_session.device_helper.shutter_contacts2 = [sw]
    entities = _setup_switch(mock_config_entry, mock_session)
    keys = {e.entity_description.key for e in entities}
    assert keys == {"bypass", "bypass_infinite", "vibration_enabled"}


def test_bypass_switch_uses_translation_key_not_device_name():
    """#342: bypass must drop _attr_name=None so HA uses the 'bypass' name key.

    If _attr_name stayed None, HA's _name_internal returns it (device name) before
    consulting translation_key — defeating the whole fix.
    """
    from custom_components.bosch_shc.switch import SWITCH_TYPES, SHCSwitch

    sw = SHCSwitch(
        device=_FAKE_DEVICE, entry_id="e1", description=SWITCH_TYPES["bypass"]
    )
    assert not hasattr(sw, "_attr_name")
    assert sw.translation_key == "bypass"
    # unique_id stays the primary id (no orphaning / migration needed)
    assert sw.unique_id == "root-1_hdm:ZigBee:dev1"


def test_bypass_infinite_switch_uses_translation_key_despite_attr_name():
    """Bug-hunt (2026-07-11): bypass_infinite has both attr_name and a
    translation_key. The del-guard used to only fire when attr_name is None,
    so this secondary entity kept the literal "BypassInfinite" as its name
    instead of the translated "Bypass Never Expires" — attr_name only needs
    to affect unique_id, not whether translation_key gets a chance to apply.
    """
    from custom_components.bosch_shc.switch import SWITCH_TYPES, SHCSwitch

    sw = SHCSwitch(
        device=_FAKE_DEVICE,
        entry_id="e1",
        description=SWITCH_TYPES["bypass_infinite"],
        attr_name="BypassInfinite",
    )
    assert not hasattr(sw, "_attr_name")
    assert sw.translation_key == "bypass_infinite"
    # unique_id still gets the attr_name suffix (distinguishes it from bypass)
    assert sw.unique_id == "root-1_hdm:ZigBee:dev1_bypassinfinite"




# ---------------------------------------------------------------------------
# ChildLock (bool) / ChildLock thermostat (enum)
# ---------------------------------------------------------------------------


def test_child_lock_bool_true():
    sw = _make_switch(SWITCH_TYPES["child_lock"], child_lock=True)
    assert sw.is_on is True


def test_child_lock_bool_false():
    sw = _make_switch(SWITCH_TYPES["child_lock"], child_lock=False)
    assert sw.is_on is False


def test_child_lock_thermostat_on():
    State = ThermostatService.State
    sw = _make_switch(SWITCH_TYPES["child_lock_thermostat"], child_lock=State.ON)
    assert sw.is_on is True


def test_child_lock_thermostat_off():
    State = ThermostatService.State
    sw = _make_switch(SWITCH_TYPES["child_lock_thermostat"], child_lock=State.OFF)
    assert sw.is_on is False


def test_child_lock_thermostat_bool_true_does_not_match():
    """child_lock_thermostat on_value is an enum — plain True must NOT match."""
    sw = _make_switch(SWITCH_TYPES["child_lock_thermostat"], child_lock=True)
    # ThermostatService.State.ON != True → is_on must be False
    assert sw.is_on is False


def test_should_poll_child_lock_is_false():
    sw = _make_switch(SWITCH_TYPES["child_lock"], child_lock=False)
    assert sw.should_poll is False


def test_child_lock_on_value_is_bool_true():
    assert SWITCH_TYPES["child_lock"].on_value is True


def test_child_lock_thermostat_on_value_is_enum():
    assert SWITCH_TYPES["child_lock_thermostat"].on_value is (
        ThermostatService.State.ON
    )


def test_thermostat_child_lock_description_uses_enum():
    enum_on = ThermostatService.State.ON
    # the root cause: the enum is never equal to the bool True
    assert (enum_on == True) is False  # noqa: E712
    assert SWITCH_TYPES["child_lock_thermostat"].on_value == enum_on
    # the ChildProtection (bool) description stays a bool
    assert SWITCH_TYPES["child_lock"].on_value is True


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


class TestThermostatChildLockIncluded:
    """Thermostats not excluded produce child_lock_thermostat switch entities."""

    def test_thermostat_child_lock_entity_created(self):
        """Included thermostat -> child_lock_thermostat SHCSwitch entity."""
        from custom_components.bosch_shc.switch import SHCSwitch

        session = _make_exclusion_session()
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


class TestChildProtectionBoolDeviceIncluded:
    """Micromodule devices not excluded produce child_lock (bool) SHCSwitch entities."""

    def test_micromodule_shutter_child_lock_included(self):
        """Included micromodule_shutter_controls -> child_lock entity created."""
        from custom_components.bosch_shc.switch import SHCSwitch

        session = _make_exclusion_session()
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


def test_setup_thermostat_without_silentmode_only_child_lock(mock_config_entry, mock_session):
    th = _fake_thermostat(name="Thermo2", dev_id="th2", silent=False)
    mock_session.device_helper.thermostats = [th]
    entities = _setup_switch(mock_config_entry, mock_session)
    keys = {e.entity_description.key for e in entities}
    assert "child_lock_thermostat" in keys
    assert "silent_mode" not in keys


def test_setup_roomthermostat_child_lock_only(mock_config_entry, mock_session):
    rt = _fake_setup_device(name="RoomThermo", dev_id="rt1")
    mock_session.device_helper.roomthermostats = [rt]
    entities = _setup_switch(mock_config_entry, mock_session)
    keys = {e.entity_description.key for e in entities}
    assert keys == {"child_lock_thermostat"}


def test_setup_wallthermostat_child_lock_only(mock_config_entry, mock_session):
    """THB/BWTH wall thermostat gets child_lock_thermostat (ThermostatService enum)."""
    wt = _fake_setup_device(name="WallThermo", dev_id="wt1")
    wt.child_lock = "ON"  # boschshcpy >= 0.2.119 exposes child_lock on wall thermostats
    mock_session.device_helper.wallthermostats = [wt]
    entities = _setup_switch(mock_config_entry, mock_session)
    keys = {e.entity_description.key for e in entities}
    assert keys == {"child_lock_thermostat"}


def test_setup_wallthermostat_uses_enum_description(mock_config_entry, mock_session):
    """Confirms the wallthermostat child_lock entity uses the enum-aware description."""

    wt = _fake_setup_device(name="WallThermo2", dev_id="wt2")
    wt.child_lock = "ON"  # boschshcpy >= 0.2.119 exposes child_lock on wall thermostats
    mock_session.device_helper.wallthermostats = [wt]
    entities = _setup_switch(mock_config_entry, mock_session)
    cl_entities = [e for e in entities if e.entity_description.key == "child_lock_thermostat"]
    assert len(cl_entities) == 1
    assert cl_entities[0].entity_description.on_value == ThermostatService.State.ON


def test_setup_micromodule_shutter_control_child_lock(mock_config_entry, mock_session):
    d = _fake_setup_device(name="MM Shutter", dev_id="msc1")
    mock_session.device_helper.micromodule_shutter_controls = [d]
    entities = _setup_switch(mock_config_entry, mock_session)
    keys = {e.entity_description.key for e in entities}
    assert "child_lock" in keys


def test_setup_micromodule_blinds_child_lock(mock_config_entry, mock_session):
    d = _fake_setup_device(name="MM Blind", dev_id="mbl1")
    mock_session.device_helper.micromodule_blinds = [d]
    entities = _setup_switch(mock_config_entry, mock_session)
    keys = {e.entity_description.key for e in entities}
    assert "child_lock" in keys


def test_setup_micromodule_impulse_relay_child_lock(mock_config_entry, mock_session):
    d = _fake_setup_device(name="MM Impulse", dev_id="mir1")
    mock_session.device_helper.micromodule_impulse_relays = [d]
    entities = _setup_switch(mock_config_entry, mock_session)
    keys = {e.entity_description.key for e in entities}
    assert "child_lock" in keys


def test_setup_micromodule_dimmer_child_lock(mock_config_entry, mock_session):
    d = _fake_setup_device(name="MM Dimmer", dev_id="mdi1")
    mock_session.device_helper.micromodule_dimmers = [d]
    entities = _setup_switch(mock_config_entry, mock_session)
    keys = {e.entity_description.key for e in entities}
    assert "child_lock" in keys


def test_setup_wallthermostat_without_child_lock_skipped_old_lib(mock_config_entry, mock_session):
    """Guard (0.4.112): a wall thermostat from an older boschshcpy (no child_lock
    attribute) must be skipped, not crash, when the lib is pinned to 0.2.117.
    """
    wt = _fake_setup_device(name="OldWallThermo", dev_id="wt-old")
    # ensure the attribute is absent (older lib)
    if hasattr(wt, "child_lock"):
        del wt.child_lock
    mock_session.device_helper.wallthermostats = [wt]
    entities = _setup_switch(mock_config_entry, mock_session)
    keys = {e.entity_description.key for e in entities}
    assert "child_lock_thermostat" not in keys




# ---------------------------------------------------------------------------
# PetImmunity
# ---------------------------------------------------------------------------


class TestMotionDetectors2IncludedPath:
    """When motion_detectors2 devices are NOT excluded, pet_immunity entities are created."""

    def _setup_motion_included(self):
        session = _make_exclusion_session()
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


class TestPetImmunitySwitch:
    """Tests for the pet_immunity_enabled SWITCH_TYPE and SHCSwitch integration."""

    def test_switch_type_exists(self):
        assert "pet_immunity_enabled" in SWITCH_TYPES

    def test_on_key_is_pet_immunity_enabled(self):
        assert SWITCH_TYPES["pet_immunity_enabled"].on_key == "pet_immunity_enabled"

    def test_on_value_is_bool_true(self):
        assert SWITCH_TYPES["pet_immunity_enabled"].on_value is True

    def test_should_poll_is_false(self):
        assert SWITCH_TYPES["pet_immunity_enabled"].should_poll is False

    def test_entity_category_is_config(self):
        from homeassistant.helpers.entity import EntityCategory
        assert SWITCH_TYPES["pet_immunity_enabled"].entity_category == EntityCategory.CONFIG

    def test_is_on_when_enabled(self):
        sw = _make_pet_switch(pet_immunity_enabled=True)
        assert sw.is_on is True

    def test_is_off_when_disabled(self):
        sw = _make_pet_switch(pet_immunity_enabled=False)
        assert sw.is_on is False

    def test_turn_on_sets_true(self):
        """async_turn_on() must call async_set_pet_immunity_enabled(True)."""
        dev = SimpleNamespace(
            pet_immunity_enabled=False,
            async_set_pet_immunity_enabled=AsyncMock(),
        )
        sw = SHCSwitch.__new__(SHCSwitch)
        sw._device = dev
        sw.entity_description = SWITCH_TYPES["pet_immunity_enabled"]
        sw.entity_id = "switch.pet_test"
        asyncio.run(sw.async_turn_on())
        dev.async_set_pet_immunity_enabled.assert_called_once_with(True)

    def test_turn_off_sets_false(self):
        """async_turn_off() must call async_set_pet_immunity_enabled(False)."""
        dev = SimpleNamespace(
            pet_immunity_enabled=True,
            async_set_pet_immunity_enabled=AsyncMock(),
        )
        sw = SHCSwitch.__new__(SHCSwitch)
        sw._device = dev
        sw.entity_description = SWITCH_TYPES["pet_immunity_enabled"]
        sw.entity_id = "switch.pet_test"
        asyncio.run(sw.async_turn_off())
        dev.async_set_pet_immunity_enabled.assert_called_once_with(False)

    def test_attr_name_with_pet_immunity_suffix(self):
        """unique_id uses lowercased attr_name suffix 'petimmunity'."""
        dev = _make_md2_device(
            name="Motion Sensor", root_device_id="rootA", id="devB"
        )
        sw = SHCSwitch.__new__(SHCSwitch)
        sw._device = dev
        sw.entity_description = SWITCH_TYPES["pet_immunity_enabled"]
        # Replicate the SHCSwitch.__init__ name/unique_id logic
        attr_name = "PetImmunity"
        sw._attr_name = f"{dev.name} {attr_name}"
        sw._attr_unique_id = f"{dev.root_device_id}_{dev.id}_{attr_name.lower()}"
        assert sw._attr_name == "Motion Sensor PetImmunity"
        assert sw._attr_unique_id == "rootA_devB_petimmunity"




# ---------------------------------------------------------------------------
# EnergySavingMode
# ---------------------------------------------------------------------------


class TestEnergySavingModeGuard:
    def test_supports_false_value_present_skipped(self, mock_config_entry, mock_session):
        """supports_energy_saving_mode=False → entity NOT created even with value."""
        plug = _fake_device(energy_saving_mode_enabled=True,
                            supports_energy_saving_mode=False)
        mock_session.device_helper.smart_plugs = [plug]
        entities = _setup_switch(mock_config_entry, mock_session)
        assert "energy_saving_mode_enabled" not in _keys(entities)

    def test_supports_true_value_none_skipped(self, mock_config_entry, mock_session):
        """supports_energy_saving_mode=True but value=None → entity NOT created."""
        plug = _fake_device(energy_saving_mode_enabled=None,
                            supports_energy_saving_mode=True)
        mock_session.device_helper.smart_plugs = [plug]
        entities = _setup_switch(mock_config_entry, mock_session)
        assert "energy_saving_mode_enabled" not in _keys(entities)

    def test_both_present_created(self, mock_config_entry, mock_session):
        """supports=True and value not None → entity created."""
        plug = _fake_device(energy_saving_mode_enabled=False,
                            supports_energy_saving_mode=True)
        mock_session.device_helper.smart_plugs = [plug]
        entities = _setup_switch(mock_config_entry, mock_session)
        assert "energy_saving_mode_enabled" in _keys(entities)

    def test_supports_false_value_present_skipped_compact(self, mock_config_entry, mock_session):
        plug = _fake_device(energy_saving_mode_enabled=True,
                            supports_energy_saving_mode=False)
        mock_session.device_helper.smart_plugs_compact = [plug]
        entities = _setup_switch(mock_config_entry, mock_session)
        assert "energy_saving_mode_enabled" not in _keys(entities)

    def test_supports_true_value_none_skipped_compact(self, mock_config_entry, mock_session):
        plug = _fake_device(energy_saving_mode_enabled=None,
                            supports_energy_saving_mode=True)
        mock_session.device_helper.smart_plugs_compact = [plug]
        entities = _setup_switch(mock_config_entry, mock_session)
        assert "energy_saving_mode_enabled" not in _keys(entities)


def test_smartplug_with_energy_saving_creates_entity(mock_config_entry, mock_session):
    plug = _fake_device(energy_saving_mode_enabled=False, supports_energy_saving_mode=True)
    mock_session.device_helper.smart_plugs = [plug]
    entities = _setup_switch(mock_config_entry, mock_session)
    keys = [e.entity_description.key for e in entities]
    assert "energy_saving_mode_enabled" in keys


def test_smartplug_without_energy_saving_skipped(mock_config_entry, mock_session):
    plug = _fake_device()  # no energy_saving_mode_enabled attr
    mock_session.device_helper.smart_plugs = [plug]
    entities = _setup_switch(mock_config_entry, mock_session)
    keys = [e.entity_description.key for e in entities]
    assert "energy_saving_mode_enabled" not in keys


def test_smartplug_energy_saving_unique_id(mock_config_entry, mock_session):
    plug = _fake_device(id="plug1", energy_saving_mode_enabled=True, supports_energy_saving_mode=True)
    mock_session.device_helper.smart_plugs = [plug]
    entities = _setup_switch(mock_config_entry, mock_session)
    esm = next(e for e in entities if e.entity_description.key == "energy_saving_mode_enabled")
    assert esm._attr_unique_id == "root1_plug1_energysavingmode"


def test_smartplug_energy_saving_is_on_true():
    plug = _fake_device(energy_saving_mode_enabled=True)
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = plug
    sw.entity_description = SWITCH_TYPES["energy_saving_mode_enabled"]
    sw.entity_id = "switch.test"
    assert sw.is_on is True


def test_smartplug_energy_saving_is_on_false():
    plug = _fake_device(energy_saving_mode_enabled=False)
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = plug
    sw.entity_description = SWITCH_TYPES["energy_saving_mode_enabled"]
    sw.entity_id = "switch.test"
    assert sw.is_on is False


def test_smartplugcompact_with_energy_saving_creates_entity(mock_config_entry, mock_session):
    plug = _fake_device(energy_saving_mode_enabled=False, supports_energy_saving_mode=True)
    mock_session.device_helper.smart_plugs_compact = [plug]
    entities = _setup_switch(mock_config_entry, mock_session)
    keys = [e.entity_description.key for e in entities]
    assert "energy_saving_mode_enabled" in keys


def test_smartplugcompact_without_energy_saving_skipped(mock_config_entry, mock_session):
    plug = _fake_device()
    mock_session.device_helper.smart_plugs_compact = [plug]
    entities = _setup_switch(mock_config_entry, mock_session)
    keys = [e.entity_description.key for e in entities]
    assert "energy_saving_mode_enabled" not in keys


def test_energy_saving_mode_entity_category_config():
    from homeassistant.helpers.entity import EntityCategory
    assert SWITCH_TYPES["energy_saving_mode_enabled"].entity_category == EntityCategory.CONFIG




# ---------------------------------------------------------------------------
# WarningSuppressed
# ---------------------------------------------------------------------------


class TestWarningSuppressedGuard:
    def test_supports_false_value_present_skipped(self, mock_config_entry, mock_session):
        plug = _fake_device(warning_suppressed=True,
                            supports_power_switch_warning=False)
        mock_session.device_helper.smart_plugs = [plug]
        entities = _setup_switch(mock_config_entry, mock_session)
        assert "warning_suppressed" not in _keys(entities)

    def test_supports_true_value_none_skipped(self, mock_config_entry, mock_session):
        plug = _fake_device(warning_suppressed=None,
                            supports_power_switch_warning=True)
        mock_session.device_helper.smart_plugs = [plug]
        entities = _setup_switch(mock_config_entry, mock_session)
        assert "warning_suppressed" not in _keys(entities)

    def test_both_present_created(self, mock_config_entry, mock_session):
        plug = _fake_device(warning_suppressed=False,
                            supports_power_switch_warning=True)
        mock_session.device_helper.smart_plugs = [plug]
        entities = _setup_switch(mock_config_entry, mock_session)
        assert "warning_suppressed" in _keys(entities)

    def test_supports_false_skipped_compact(self, mock_config_entry, mock_session):
        plug = _fake_device(warning_suppressed=False,
                            supports_power_switch_warning=False)
        mock_session.device_helper.smart_plugs_compact = [plug]
        entities = _setup_switch(mock_config_entry, mock_session)
        assert "warning_suppressed" not in _keys(entities)

    def test_supports_true_value_none_skipped_compact(self, mock_config_entry, mock_session):
        plug = _fake_device(warning_suppressed=None,
                            supports_power_switch_warning=True)
        mock_session.device_helper.smart_plugs_compact = [plug]
        entities = _setup_switch(mock_config_entry, mock_session)
        assert "warning_suppressed" not in _keys(entities)


def test_smartplug_with_warning_suppressed_creates_entity(mock_config_entry, mock_session):
    plug = _fake_device(warning_suppressed=False, supports_power_switch_warning=True)
    mock_session.device_helper.smart_plugs = [plug]
    entities = _setup_switch(mock_config_entry, mock_session)
    keys = [e.entity_description.key for e in entities]
    assert "warning_suppressed" in keys


def test_smartplug_without_warning_suppressed_skipped(mock_config_entry, mock_session):
    plug = _fake_device()
    mock_session.device_helper.smart_plugs = [plug]
    entities = _setup_switch(mock_config_entry, mock_session)
    keys = [e.entity_description.key for e in entities]
    assert "warning_suppressed" not in keys


def test_smartplug_warning_suppressed_is_on_true():
    plug = _fake_device(warning_suppressed=True)
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = plug
    sw.entity_description = SWITCH_TYPES["warning_suppressed"]
    sw.entity_id = "switch.test"
    assert sw.is_on is True


def test_smartplug_warning_suppressed_is_on_false():
    plug = _fake_device(warning_suppressed=False)
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = plug
    sw.entity_description = SWITCH_TYPES["warning_suppressed"]
    sw.entity_id = "switch.test"
    assert sw.is_on is False


def test_smartplug_warning_suppressed_unique_id(mock_config_entry, mock_session):
    plug = _fake_device(id="plug1", warning_suppressed=False, supports_power_switch_warning=True)
    mock_session.device_helper.smart_plugs = [plug]
    entities = _setup_switch(mock_config_entry, mock_session)
    ws = next(e for e in entities if e.entity_description.key == "warning_suppressed")
    assert ws._attr_unique_id == "root1_plug1_warningsuppressed"


def test_warning_suppressed_entity_category_config():
    from homeassistant.helpers.entity import EntityCategory
    assert SWITCH_TYPES["warning_suppressed"].entity_category == EntityCategory.CONFIG


class TestSwitchSmartPlugCompactWarningSuppressed:
    """switch.py line 411 — warning_suppressed hasattr block on smart_plugs_compact."""

    def test_compact_plug_with_warning_suppressed_creates_entity(self, mock_config_entry, mock_session):
        plug = _fake_device_gaps(id="cp1", warning_suppressed=False,
                            supports_power_switch_warning=True)
        mock_session.device_helper.smart_plugs_compact = [plug]
        entities = _setup_switch(mock_config_entry, mock_session)
        keys = [getattr(e, "entity_description", None) and e.entity_description.key
                for e in entities]
        assert "warning_suppressed" in keys

    def test_compact_plug_without_warning_suppressed_no_entity(self, mock_config_entry, mock_session):
        # No warning_suppressed attr → hasattr check at line 410 is False
        plug = _fake_device_gaps(id="cp2")
        mock_session.device_helper.smart_plugs_compact = [plug]
        entities = _setup_switch(mock_config_entry, mock_session)
        keys = [getattr(e, "entity_description", None) and e.entity_description.key
                for e in entities]
        assert "warning_suppressed" not in keys




# ---------------------------------------------------------------------------
# NightlyPromise
# ---------------------------------------------------------------------------


class TestNightlyPromiseGuard:
    def test_supports_false_value_present_skipped(self, mock_config_entry, mock_session):
        tg = _fake_device(nightly_promise_enabled=True,
                          supports_nightly_promise=False)
        mock_session.device_helper.twinguards = [tg]
        entities = _setup_switch(mock_config_entry, mock_session)
        assert "nightly_promise_enabled" not in _keys(entities)

    def test_supports_true_value_none_skipped(self, mock_config_entry, mock_session):
        tg = _fake_device(nightly_promise_enabled=None,
                          supports_nightly_promise=True)
        mock_session.device_helper.twinguards = [tg]
        entities = _setup_switch(mock_config_entry, mock_session)
        assert "nightly_promise_enabled" not in _keys(entities)

    def test_both_present_created(self, mock_config_entry, mock_session):
        tg = _fake_device(nightly_promise_enabled=True,
                          supports_nightly_promise=True)
        mock_session.device_helper.twinguards = [tg]
        entities = _setup_switch(mock_config_entry, mock_session)
        assert "nightly_promise_enabled" in _keys(entities)


def test_twinguard_with_nightly_promise_creates_entity(mock_config_entry, mock_session):
    tg = _fake_device(nightly_promise_enabled=True, supports_nightly_promise=True)
    mock_session.device_helper.twinguards = [tg]
    entities = _setup_switch(mock_config_entry, mock_session)
    keys = [e.entity_description.key for e in entities]
    assert "nightly_promise_enabled" in keys


def test_twinguard_without_nightly_promise_skipped(mock_config_entry, mock_session):
    tg = _fake_device()
    mock_session.device_helper.twinguards = [tg]
    entities = _setup_switch(mock_config_entry, mock_session)
    keys = [e.entity_description.key for e in entities]
    assert "nightly_promise_enabled" not in keys


def test_twinguard_nightly_promise_is_on():
    tg = _fake_device(nightly_promise_enabled=True)
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = tg
    sw.entity_description = SWITCH_TYPES["nightly_promise_enabled"]
    sw.entity_id = "switch.test"
    assert sw.is_on is True


def test_twinguard_nightly_promise_unique_id(mock_config_entry, mock_session):
    tg = _fake_device(id="tg1", nightly_promise_enabled=False, supports_nightly_promise=True)
    mock_session.device_helper.twinguards = [tg]
    entities = _setup_switch(mock_config_entry, mock_session)
    np = next(e for e in entities if e.entity_description.key == "nightly_promise_enabled")
    assert np._attr_unique_id == "root1_tg1_nightlypromise"


def test_nightly_promise_entity_category_config():
    from homeassistant.helpers.entity import EntityCategory
    assert SWITCH_TYPES["nightly_promise_enabled"].entity_category == EntityCategory.CONFIG


class TestSwitchTwinguardsDeviceExcluded:
    """switch.py line 721 — twinguards device_excluded continue."""

    def test_excluded_twinguard_not_added(self, mock_config_entry, mock_session):
        tg = _fake_device_gaps(id="tg-excl", nightly_promise_enabled=True)
        mock_session.device_helper.twinguards = [tg]
        mock_config_entry.options = _excl("tg-excl")
        entities = _setup_switch(mock_config_entry, mock_session)
        ids = [getattr(getattr(e, "_device", None), "id", None) for e in entities]
        assert "tg-excl" not in ids




# ---------------------------------------------------------------------------
# HumidityWarning
# ---------------------------------------------------------------------------


class TestHumidityWarningGuardThermostat:
    def test_supports_false_value_present_skipped(self, mock_config_entry, mock_session):
        therm = _fake_device(humidity_warning_enabled=True,
                             supports_display_configuration=False)
        mock_session.device_helper.thermostats = [therm]
        entities = _setup_switch(mock_config_entry, mock_session)
        assert "humidity_warning_enabled" not in _keys(entities)

    def test_supports_true_value_none_skipped(self, mock_config_entry, mock_session):
        therm = _fake_device(humidity_warning_enabled=None,
                             supports_display_configuration=True)
        mock_session.device_helper.thermostats = [therm]
        entities = _setup_switch(mock_config_entry, mock_session)
        assert "humidity_warning_enabled" not in _keys(entities)

    def test_both_present_created(self, mock_config_entry, mock_session):
        therm = _fake_device(humidity_warning_enabled=False,
                             supports_display_configuration=True)
        mock_session.device_helper.thermostats = [therm]
        entities = _setup_switch(mock_config_entry, mock_session)
        assert "humidity_warning_enabled" in _keys(entities)


class TestHumidityWarningGuardRoomThermostat:
    def test_supports_false_value_present_skipped(self, mock_config_entry, mock_session):
        rth = _fake_device(humidity_warning_enabled=True,
                           supports_display_configuration=False)
        mock_session.device_helper.roomthermostats = [rth]
        entities = _setup_switch(mock_config_entry, mock_session)
        assert "humidity_warning_enabled" not in _keys(entities)

    def test_supports_true_value_none_skipped(self, mock_config_entry, mock_session):
        rth = _fake_device(humidity_warning_enabled=None,
                           supports_display_configuration=True)
        mock_session.device_helper.roomthermostats = [rth]
        entities = _setup_switch(mock_config_entry, mock_session)
        assert "humidity_warning_enabled" not in _keys(entities)

    def test_both_present_created(self, mock_config_entry, mock_session):
        rth = _fake_device(humidity_warning_enabled=True,
                           supports_display_configuration=True)
        mock_session.device_helper.roomthermostats = [rth]
        entities = _setup_switch(mock_config_entry, mock_session)
        assert "humidity_warning_enabled" in _keys(entities)


def test_thermostat_with_humidity_warning_creates_entity(mock_config_entry, mock_session):
    therm = _fake_device(humidity_warning_enabled=False, supports_display_configuration=True)
    mock_session.device_helper.thermostats = [therm]
    entities = _setup_switch(mock_config_entry, mock_session)
    keys = [e.entity_description.key for e in entities]
    assert "humidity_warning_enabled" in keys


def test_thermostat_without_humidity_warning_skipped(mock_config_entry, mock_session):
    therm = _fake_device()
    mock_session.device_helper.thermostats = [therm]
    entities = _setup_switch(mock_config_entry, mock_session)
    keys = [e.entity_description.key for e in entities]
    assert "humidity_warning_enabled" not in keys


def test_roomthermostat_with_humidity_warning_creates_entity(mock_config_entry, mock_session):
    rth = _fake_device(humidity_warning_enabled=True, supports_display_configuration=True)
    mock_session.device_helper.roomthermostats = [rth]
    entities = _setup_switch(mock_config_entry, mock_session)
    keys = [e.entity_description.key for e in entities]
    assert "humidity_warning_enabled" in keys


def test_humidity_warning_is_on_true():
    dev = _fake_device(humidity_warning_enabled=True)
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = dev
    sw.entity_description = SWITCH_TYPES["humidity_warning_enabled"]
    sw.entity_id = "switch.test"
    assert sw.is_on is True


def test_humidity_warning_unique_id(mock_config_entry, mock_session):
    therm = _fake_device(id="t1", humidity_warning_enabled=False, supports_display_configuration=True)
    mock_session.device_helper.thermostats = [therm]
    entities = _setup_switch(mock_config_entry, mock_session)
    hw = next(e for e in entities if e.entity_description.key == "humidity_warning_enabled")
    assert hw._attr_unique_id == "root1_t1_humiditywarning"


def test_humidity_warning_entity_category_config():
    from homeassistant.helpers.entity import EntityCategory
    assert SWITCH_TYPES["humidity_warning_enabled"].entity_category == EntityCategory.CONFIG




# ---------------------------------------------------------------------------
# SwapInputs
# ---------------------------------------------------------------------------


class TestSwapInputsGuardRelay:
    def test_supports_false_value_present_skipped(self, mock_config_entry, mock_session):
        relay = _fake_device(swap_inputs=True, child_lock=False,
                             supports_switch_configuration=False)
        mock_session.device_helper.micromodule_relays = [relay]
        entities = _setup_switch(mock_config_entry, mock_session)
        assert "swap_inputs" not in _keys(entities)

    def test_supports_true_value_none_skipped(self, mock_config_entry, mock_session):
        relay = _fake_device(swap_inputs=None, child_lock=False,
                             supports_switch_configuration=True)
        mock_session.device_helper.micromodule_relays = [relay]
        entities = _setup_switch(mock_config_entry, mock_session)
        assert "swap_inputs" not in _keys(entities)

    def test_both_present_created(self, mock_config_entry, mock_session):
        relay = _fake_device(swap_inputs=False, child_lock=False,
                             supports_switch_configuration=True)
        mock_session.device_helper.micromodule_relays = [relay]
        entities = _setup_switch(mock_config_entry, mock_session)
        assert "swap_inputs" in _keys(entities)


class TestSwapInputsGuardLightControl:
    def test_supports_false_value_present_skipped(self, mock_config_entry, mock_session):
        lc = _fake_device(swap_inputs=True,
                          supports_switch_configuration=False)
        mock_session.device_helper.micromodule_light_controls = [lc]
        entities = _setup_switch(mock_config_entry, mock_session)
        assert "swap_inputs" not in _keys(entities)

    def test_supports_true_value_none_skipped(self, mock_config_entry, mock_session):
        lc = _fake_device(swap_inputs=None,
                          supports_switch_configuration=True)
        mock_session.device_helper.micromodule_light_controls = [lc]
        entities = _setup_switch(mock_config_entry, mock_session)
        assert "swap_inputs" not in _keys(entities)

    def test_both_present_created(self, mock_config_entry, mock_session):
        lc = _fake_device(swap_inputs=False,
                          supports_switch_configuration=True)
        mock_session.device_helper.micromodule_light_controls = [lc]
        entities = _setup_switch(mock_config_entry, mock_session)
        assert "swap_inputs" in _keys(entities)


def test_relay_with_swap_inputs_creates_entity(mock_config_entry, mock_session):
    relay = _fake_device(swap_inputs=False, child_lock=False, supports_switch_configuration=True)
    mock_session.device_helper.micromodule_relays = [relay]
    entities = _setup_switch(mock_config_entry, mock_session)
    keys = [e.entity_description.key for e in entities]
    assert "swap_inputs" in keys


def test_relay_without_swap_inputs_skipped(mock_config_entry, mock_session):
    relay = _fake_device(child_lock=False)
    mock_session.device_helper.micromodule_relays = [relay]
    entities = _setup_switch(mock_config_entry, mock_session)
    keys = [e.entity_description.key for e in entities]
    assert "swap_inputs" not in keys


def test_swap_inputs_is_on_true():
    relay = _fake_device(swap_inputs=True)
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = relay
    sw.entity_description = SWITCH_TYPES["swap_inputs"]
    sw.entity_id = "switch.test"
    assert sw.is_on is True


def test_swap_inputs_is_on_false():
    relay = _fake_device(swap_inputs=False)
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = relay
    sw.entity_description = SWITCH_TYPES["swap_inputs"]
    sw.entity_id = "switch.test"
    assert sw.is_on is False


def test_swap_inputs_unique_id(mock_config_entry, mock_session):
    relay = _fake_device(id="r1", swap_inputs=False, swap_outputs=False,
                         child_lock=False, supports_switch_configuration=True)
    mock_session.device_helper.micromodule_relays = [relay]
    entities = _setup_switch(mock_config_entry, mock_session)
    si = next(e for e in entities if e.entity_description.key == "swap_inputs")
    assert si._attr_unique_id == "root1_r1_swapinputs"


def test_light_control_with_swap_inputs_creates_entity(mock_config_entry, mock_session):
    lc = _fake_device(swap_inputs=False, supports_switch_configuration=True)
    mock_session.device_helper.micromodule_light_controls = [lc]
    entities = _setup_switch(mock_config_entry, mock_session)
    keys = [e.entity_description.key for e in entities]
    assert "swap_inputs" in keys


def test_light_control_without_swap_inputs_skipped(mock_config_entry, mock_session):
    lc = _fake_device()
    mock_session.device_helper.micromodule_light_controls = [lc]
    entities = _setup_switch(mock_config_entry, mock_session)
    keys = [e.entity_description.key for e in entities]
    assert "swap_inputs" not in keys


def test_swap_inputs_entity_category_config():
    from homeassistant.helpers.entity import EntityCategory
    assert SWITCH_TYPES["swap_inputs"].entity_category == EntityCategory.CONFIG




# ---------------------------------------------------------------------------
# SwapOutputs
# ---------------------------------------------------------------------------


class TestSwapOutputsGuardRelay:
    def test_supports_false_value_present_skipped(self, mock_config_entry, mock_session):
        relay = _fake_device(swap_outputs=True, child_lock=False,
                             supports_switch_configuration=False)
        mock_session.device_helper.micromodule_relays = [relay]
        entities = _setup_switch(mock_config_entry, mock_session)
        assert "swap_outputs" not in _keys(entities)

    def test_supports_true_value_none_skipped(self, mock_config_entry, mock_session):
        relay = _fake_device(swap_outputs=None, child_lock=False,
                             supports_switch_configuration=True)
        mock_session.device_helper.micromodule_relays = [relay]
        entities = _setup_switch(mock_config_entry, mock_session)
        assert "swap_outputs" not in _keys(entities)

    def test_both_present_created(self, mock_config_entry, mock_session):
        relay = _fake_device(swap_outputs=False, child_lock=False,
                             supports_switch_configuration=True)
        mock_session.device_helper.micromodule_relays = [relay]
        entities = _setup_switch(mock_config_entry, mock_session)
        assert "swap_outputs" in _keys(entities)


class TestSwapOutputsGuardLightControl:
    def test_supports_false_value_present_skipped(self, mock_config_entry, mock_session):
        lc = _fake_device(swap_outputs=True,
                          supports_switch_configuration=False)
        mock_session.device_helper.micromodule_light_controls = [lc]
        entities = _setup_switch(mock_config_entry, mock_session)
        assert "swap_outputs" not in _keys(entities)

    def test_supports_true_value_none_skipped(self, mock_config_entry, mock_session):
        lc = _fake_device(swap_outputs=None,
                          supports_switch_configuration=True)
        mock_session.device_helper.micromodule_light_controls = [lc]
        entities = _setup_switch(mock_config_entry, mock_session)
        assert "swap_outputs" not in _keys(entities)

    def test_both_present_created(self, mock_config_entry, mock_session):
        lc = _fake_device(swap_outputs=False,
                          supports_switch_configuration=True)
        mock_session.device_helper.micromodule_light_controls = [lc]
        entities = _setup_switch(mock_config_entry, mock_session)
        assert "swap_outputs" in _keys(entities)


def test_relay_with_swap_outputs_creates_entity(mock_config_entry, mock_session):
    relay = _fake_device(swap_outputs=True, child_lock=False, supports_switch_configuration=True)
    mock_session.device_helper.micromodule_relays = [relay]
    entities = _setup_switch(mock_config_entry, mock_session)
    keys = [e.entity_description.key for e in entities]
    assert "swap_outputs" in keys


def test_relay_without_swap_outputs_skipped(mock_config_entry, mock_session):
    relay = _fake_device(child_lock=False)
    mock_session.device_helper.micromodule_relays = [relay]
    entities = _setup_switch(mock_config_entry, mock_session)
    keys = [e.entity_description.key for e in entities]
    assert "swap_outputs" not in keys


def test_swap_outputs_is_on_true():
    relay = _fake_device(swap_outputs=True)
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = relay
    sw.entity_description = SWITCH_TYPES["swap_outputs"]
    sw.entity_id = "switch.test"
    assert sw.is_on is True


def test_swap_outputs_unique_id(mock_config_entry, mock_session):
    relay = _fake_device(id="r1", swap_inputs=False, swap_outputs=False,
                         child_lock=False, supports_switch_configuration=True)
    mock_session.device_helper.micromodule_relays = [relay]
    entities = _setup_switch(mock_config_entry, mock_session)
    so = next(e for e in entities if e.entity_description.key == "swap_outputs")
    assert so._attr_unique_id == "root1_r1_swapoutputs"


def test_swap_outputs_entity_category_config():
    from homeassistant.helpers.entity import EntityCategory
    assert SWITCH_TYPES["swap_outputs"].entity_category == EntityCategory.CONFIG


class TestSwitchMicromoduleLightControlsSwapOutputs:
    """switch.py line 465 — swap_outputs hasattr block on micromodule_light_controls."""

    def test_light_control_with_swap_outputs_creates_entity(self, mock_config_entry, mock_session):
        dev = _fake_device_gaps(id="mlc1", swap_outputs=False,
                           supports_switch_configuration=True)
        mock_session.device_helper.micromodule_light_controls = [dev]
        entities = _setup_switch(mock_config_entry, mock_session)
        keys = [getattr(e, "entity_description", None) and e.entity_description.key
                for e in entities]
        assert "swap_outputs" in keys

    def test_light_control_without_swap_outputs_no_entity(self, mock_config_entry, mock_session):
        dev = _fake_device_gaps(id="mlc2")  # no swap_outputs attr
        mock_session.device_helper.micromodule_light_controls = [dev]
        entities = _setup_switch(mock_config_entry, mock_session)
        keys = [getattr(e, "entity_description", None) and e.entity_description.key
                for e in entities]
        assert "swap_outputs" not in keys




# ---------------------------------------------------------------------------
# PreAlarm
# ---------------------------------------------------------------------------


class TestPreAlarmGuardTwinguard:
    def test_supports_false_value_present_skipped(self, mock_config_entry, mock_session):
        tg = _fake_device(pre_alarm_enabled=True,
                          supports_smoke_sensitivity=False)
        mock_session.device_helper.twinguards = [tg]
        entities = _setup_switch(mock_config_entry, mock_session)
        assert "pre_alarm_enabled" not in _keys(entities)

    def test_supports_true_value_none_skipped(self, mock_config_entry, mock_session):
        tg = _fake_device(pre_alarm_enabled=None,
                          supports_smoke_sensitivity=True)
        mock_session.device_helper.twinguards = [tg]
        entities = _setup_switch(mock_config_entry, mock_session)
        assert "pre_alarm_enabled" not in _keys(entities)

    def test_both_present_created(self, mock_config_entry, mock_session):
        tg = _fake_device(pre_alarm_enabled=False,
                          supports_smoke_sensitivity=True)
        mock_session.device_helper.twinguards = [tg]
        entities = _setup_switch(mock_config_entry, mock_session)
        assert "pre_alarm_enabled" in _keys(entities)


class TestPreAlarmGuardSmokeDetector:
    def test_supports_false_value_present_skipped(self, mock_config_entry, mock_session):
        sd = _fake_device(pre_alarm_enabled=True,
                          supports_smoke_sensitivity=False)
        mock_session.device_helper.smoke_detectors = [sd]
        entities = _setup_switch(mock_config_entry, mock_session)
        assert "pre_alarm_enabled" not in _keys(entities)

    def test_supports_true_value_none_skipped(self, mock_config_entry, mock_session):
        sd = _fake_device(pre_alarm_enabled=None,
                          supports_smoke_sensitivity=True)
        mock_session.device_helper.smoke_detectors = [sd]
        entities = _setup_switch(mock_config_entry, mock_session)
        assert "pre_alarm_enabled" not in _keys(entities)

    def test_both_present_created(self, mock_config_entry, mock_session):
        sd = _fake_device(pre_alarm_enabled=False,
                          supports_smoke_sensitivity=True)
        mock_session.device_helper.smoke_detectors = [sd]
        entities = _setup_switch(mock_config_entry, mock_session)
        assert "pre_alarm_enabled" in _keys(entities)


def test_twinguard_with_pre_alarm_creates_entity(mock_config_entry, mock_session):
    tg = _fake_device(pre_alarm_enabled=False, supports_smoke_sensitivity=True)
    mock_session.device_helper.twinguards = [tg]
    entities = _setup_switch(mock_config_entry, mock_session)
    keys = [e.entity_description.key for e in entities]
    assert "pre_alarm_enabled" in keys


def test_twinguard_without_pre_alarm_skipped(mock_config_entry, mock_session):
    tg = _fake_device()
    mock_session.device_helper.twinguards = [tg]
    entities = _setup_switch(mock_config_entry, mock_session)
    keys = [e.entity_description.key for e in entities]
    assert "pre_alarm_enabled" not in keys


def test_smoke_detector_with_pre_alarm_creates_entity(mock_config_entry, mock_session):
    sd = _fake_device(pre_alarm_enabled=False, supports_smoke_sensitivity=True)
    mock_session.device_helper.smoke_detectors = [sd]
    entities = _setup_switch(mock_config_entry, mock_session)
    keys = [e.entity_description.key for e in entities]
    assert "pre_alarm_enabled" in keys


def test_smoke_detector_without_pre_alarm_skipped(mock_config_entry, mock_session):
    sd = _fake_device()
    mock_session.device_helper.smoke_detectors = [sd]
    entities = _setup_switch(mock_config_entry, mock_session)
    keys = [e.entity_description.key for e in entities]
    assert "pre_alarm_enabled" not in keys


def test_pre_alarm_is_on_true():
    dev = _fake_device(pre_alarm_enabled=True)
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = dev
    sw.entity_description = SWITCH_TYPES["pre_alarm_enabled"]
    sw.entity_id = "switch.test"
    assert sw.is_on is True


def test_pre_alarm_is_on_false():
    dev = _fake_device(pre_alarm_enabled=False)
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = dev
    sw.entity_description = SWITCH_TYPES["pre_alarm_enabled"]
    sw.entity_id = "switch.test"
    assert sw.is_on is False


def test_pre_alarm_entity_category_config():
    from homeassistant.helpers.entity import EntityCategory
    assert SWITCH_TYPES["pre_alarm_enabled"].entity_category == EntityCategory.CONFIG


class TestSwitchSmokeDetectorsDeviceExcluded:
    """switch.py line 743 — smoke_detectors device_excluded continue."""

    def test_excluded_smoke_detector_not_added(self, mock_config_entry, mock_session):
        sd = _fake_device_gaps(id="sd-excl", pre_alarm_enabled=False)
        mock_session.device_helper.smoke_detectors = [sd]
        mock_config_entry.options = _excl("sd-excl")
        entities = _setup_switch(mock_config_entry, mock_session)
        ids = [getattr(getattr(e, "_device", None), "id", None) for e in entities]
        assert "sd-excl" not in ids




# ---------------------------------------------------------------------------
# SmartSensitivity
# ---------------------------------------------------------------------------


def test_md2_with_smart_sensitivity_creates_entity(mock_config_entry, mock_session):
    md2 = _fake_device(
        pet_immunity_enabled=False,
        smart_sensitivity_enabled=True,
    )
    mock_session.device_helper.motion_detectors2 = [md2]
    entities = _setup_switch(mock_config_entry, mock_session)
    keys = [e.entity_description.key for e in entities]
    assert "smart_sensitivity_enabled" in keys


def test_md2_without_smart_sensitivity_skipped(mock_config_entry, mock_session):
    md2 = _fake_device(pet_immunity_enabled=False)
    mock_session.device_helper.motion_detectors2 = [md2]
    entities = _setup_switch(mock_config_entry, mock_session)
    keys = [e.entity_description.key for e in entities]
    assert "smart_sensitivity_enabled" not in keys


def test_smart_sensitivity_is_on_true():
    dev = _fake_device(smart_sensitivity_enabled=True)
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = dev
    sw.entity_description = SWITCH_TYPES["smart_sensitivity_enabled"]
    sw.entity_id = "switch.test"
    assert sw.is_on is True


def test_smart_sensitivity_is_on_false():
    dev = _fake_device(smart_sensitivity_enabled=False)
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = dev
    sw.entity_description = SWITCH_TYPES["smart_sensitivity_enabled"]
    sw.entity_id = "switch.test"
    assert sw.is_on is False


def test_smart_sensitivity_unique_id(mock_config_entry, mock_session):
    md2 = _fake_device(id="md1", pet_immunity_enabled=False,
                       smart_sensitivity_enabled=False)
    mock_session.device_helper.motion_detectors2 = [md2]
    entities = _setup_switch(mock_config_entry, mock_session)
    ss = next(e for e in entities if e.entity_description.key == "smart_sensitivity_enabled")
    assert ss._attr_unique_id == "root1_md1_smartsensitivity"


def test_smart_sensitivity_entity_category_config():
    from homeassistant.helpers.entity import EntityCategory
    assert SWITCH_TYPES["smart_sensitivity_enabled"].entity_category == EntityCategory.CONFIG




# ---------------------------------------------------------------------------
# TamperProtection
# ---------------------------------------------------------------------------


class TestSwitchMotionDetector2TamperProtection:
    """Line 804: smoke_detector/motion_detector2 tamper_protection_enabled."""

    def _run_switch_setup_md2(self, motion_detectors2, options=None):
        from custom_components.bosch_shc.switch import async_setup_entry

        dh = MagicMock()
        dh.smart_plugs = []
        dh.smart_plugs_compact = []
        dh.micromodule_relays = []
        dh.micromodule_impulse_relays = []
        dh.micromodule_dimmers = []
        dh.light_switches_bsm = []
        dh.micromodule_light_attached = []
        dh.micromodule_light_controls = []
        dh.camera_eyes = []
        dh.camera_360 = []
        dh.camera_outdoor_gen2 = []
        dh.presence_simulation_system = None
        dh.shutter_contacts2 = []
        dh.thermostats = []
        dh.roomthermostats = []
        dh.wallthermostats = []
        dh.motion_detectors2 = motion_detectors2
        dh.universal_switches = []
        dh.twinguards = []
        dh.smoke_detectors = []
        dh.smoke_detection_system = None

        session = MagicMock()
        session.device_helper = dh
        session.userdefinedstates = []
        session._subscribers = []
        session.subscribe = MagicMock()

        hass = _fake_hass(session=session)
        entry = _fake_entry(hass=hass, options=options or {})
        entry.async_on_unload = MagicMock()

        with patch("custom_components.bosch_shc.switch.async_migrate_to_new_unique_id",
                   new_callable=AsyncMock):
            collected = []
            _run(async_setup_entry(hass, entry, lambda ents, **kw: collected.extend(ents)))
        return collected

    def test_motion_detector2_with_tamper_protection_switch(self):
        """Line 804: tamper_protection_enabled → TamperProtection switch added."""
        dev = _fake_dev(
            "md2_1",
            supports_silentmode=True,
            pet_immunity_enabled=True,
            tamper_protection_enabled=False,  # hasattr must return True
        )
        collected = self._run_switch_setup_md2([dev])
        unique_ids = [getattr(e, "_attr_unique_id", "") for e in collected]
        # SHCSwitch uses attr_name.lower() in unique_id → "tamperprotection"
        assert any("tamperprotection" in uid for uid in unique_ids)


class TestTamperProtectionSwitch:
    def test_switch_type_defined(self):
        desc = SWITCH_TYPES["tamper_protection_enabled"]
        assert desc.on_key == "tamper_protection_enabled"
        assert desc.on_value is True

    def test_is_on_reads_property(self):
        dev = _fake_md2(tamper_protection_enabled=True)
        sw = SHCSwitch.__new__(SHCSwitch)
        sw._device = dev
        sw.entity_description = SWITCH_TYPES["tamper_protection_enabled"]
        assert sw.is_on is True

    def test_turn_on_calls_async_setter(self):
        dev = _fake_md2(
            tamper_protection_enabled=False,
            async_set_tamper_protection_enabled=AsyncMock(),
        )
        sw = SHCSwitch.__new__(SHCSwitch)
        sw._device = dev
        sw.entity_description = SWITCH_TYPES["tamper_protection_enabled"]
        asyncio.run(sw.async_turn_on())
        dev.async_set_tamper_protection_enabled.assert_called_once_with(True)

    def test_turn_off_calls_async_setter(self):
        dev = _fake_md2(
            tamper_protection_enabled=True,
            async_set_tamper_protection_enabled=AsyncMock(),
        )
        sw = SHCSwitch.__new__(SHCSwitch)
        sw._device = dev
        sw.entity_description = SWITCH_TYPES["tamper_protection_enabled"]
        asyncio.run(sw.async_turn_off())
        dev.async_set_tamper_protection_enabled.assert_called_once_with(False)




# ---------------------------------------------------------------------------
# SilentMode
# ---------------------------------------------------------------------------


def test_silent_mode_is_on_true_when_mode_silent():
    State = SilentModeService.State
    sw = _make_switch(SWITCH_TYPES["silent_mode"], silentmode=State.MODE_SILENT)
    assert sw.is_on is True


def test_silent_mode_is_on_false_when_mode_normal():
    State = SilentModeService.State
    sw = _make_switch(SWITCH_TYPES["silent_mode"], silentmode=State.MODE_NORMAL)
    assert sw.is_on is False


def test_should_poll_silent_mode_is_false():
    State = SilentModeService.State
    sw = _make_switch(SWITCH_TYPES["silent_mode"], silentmode=State.MODE_NORMAL)
    assert sw.should_poll is False


def test_silent_mode_on_value_is_mode_silent():
    assert SWITCH_TYPES["silent_mode"].on_value is (
        SilentModeService.State.MODE_SILENT
    )


def test_silent_mode_on_value_is_mode_silent_enum():
    """on_value for silent_mode must be the MODE_SILENT enum member."""
    desc = SWITCH_TYPES["silent_mode"]
    assert desc.on_key == "silentmode"
    assert desc.on_value is SilentModeService.State.MODE_SILENT


def test_setup_thermostat_with_silentmode_two_entities(mock_config_entry, mock_session):
    th = _fake_thermostat(name="Thermo", dev_id="th1", silent=True)
    mock_session.device_helper.thermostats = [th]
    entities = _setup_switch(mock_config_entry, mock_session)
    keys = {e.entity_description.key for e in entities}
    assert "silent_mode" in keys
    assert "child_lock_thermostat" in keys




# ---------------------------------------------------------------------------
# VibrationEnabled
# ---------------------------------------------------------------------------


def test_vibration_enabled_true():
    sw = _make_switch(SWITCH_TYPES["vibration_enabled"], enabled=True)
    assert sw.is_on is True


def test_vibration_enabled_false():
    sw = _make_switch(SWITCH_TYPES["vibration_enabled"], enabled=False)
    assert sw.is_on is False


def test_should_poll_vibration_enabled_is_false():
    sw = _make_switch(SWITCH_TYPES["vibration_enabled"], enabled=False)
    assert sw.should_poll is False


def test_vibration_enabled_on_value_is_bool_true():
    assert SWITCH_TYPES["vibration_enabled"].on_value is True




# ---------------------------------------------------------------------------
# UserDefinedState (SHCUserDefinedStateSwitch)
# ---------------------------------------------------------------------------


def test_user_defined_state_true():
    sw = _make_switch(SWITCH_TYPES["user_defined_state"], state=True)
    assert sw.is_on is True


def test_user_defined_state_false():
    sw = _make_switch(SWITCH_TYPES["user_defined_state"], state=False)
    assert sw.is_on is False


def test_should_poll_user_defined_state_is_false():
    sw = _make_switch(SWITCH_TYPES["user_defined_state"], state=False)
    assert sw.should_poll is False


def test_user_defined_state_on_value_is_bool_true():
    assert SWITCH_TYPES["user_defined_state"].on_value is True


class TestSHCUserDefinedStateSwitch:
    """Pure-unit tests for SHCUserDefinedStateSwitch (no HA event loop)."""

    def test_is_on_when_state_true(self):
        sw = _make_uds_switch(state=True)
        assert sw.is_on is True

    def test_is_off_when_state_false(self):
        sw = _make_uds_switch(state=False)
        assert sw.is_on is False

    def test_turn_on_sets_state_true(self):
        mock_set = AsyncMock()

        device = SimpleNamespace(
            name="My State",
            id="uds1",
            root_device_id="mac1",
            deleted=False,
            state=False,
            async_set_state=mock_set,
        )
        shc_entry = SimpleNamespace(
            name="SHC", id="shcid", identifiers=set(),
            manufacturer="Bosch", model="SHC2",
        )
        fake_entry = SimpleNamespace(entry_id="entry1")
        fake_entry.runtime_data = SimpleNamespace(shc_device=shc_entry)
        hass = SimpleNamespace(
            config_entries=SimpleNamespace(async_get_entry=lambda eid: fake_entry)
        )
        session = SimpleNamespace(
            subscribe_userdefinedstate_callback=lambda *a, **kw: None,
            unsubscribe_userdefinedstate_callbacks=lambda *a, **kw: None,
        )
        sw = SHCUserDefinedStateSwitch(
            device=device,
            hass=hass,
            session=session,
            entry_id="entry1",
            description=SWITCH_TYPES["user_defined_state"],
        )
        asyncio.run(sw.async_turn_on())
        mock_set.assert_awaited_once_with(True)

    def test_turn_off_sets_state_false(self):
        mock_set = AsyncMock()

        device = SimpleNamespace(
            name="My State",
            id="uds1",
            root_device_id="mac1",
            deleted=False,
            state=True,
            async_set_state=mock_set,
        )
        shc_entry = SimpleNamespace(
            name="SHC", id="shcid", identifiers=set(),
            manufacturer="Bosch", model="SHC2",
        )
        fake_entry = SimpleNamespace(entry_id="entry1")
        fake_entry.runtime_data = SimpleNamespace(shc_device=shc_entry)
        hass = SimpleNamespace(
            config_entries=SimpleNamespace(async_get_entry=lambda eid: fake_entry)
        )
        session = SimpleNamespace(
            subscribe_userdefinedstate_callback=lambda *a, **kw: None,
            unsubscribe_userdefinedstate_callbacks=lambda *a, **kw: None,
        )
        sw = SHCUserDefinedStateSwitch(
            device=device,
            hass=hass,
            session=session,
            entry_id="entry1",
            description=SWITCH_TYPES["user_defined_state"],
        )
        asyncio.run(sw.async_turn_off())
        mock_set.assert_awaited_once_with(False)

    def test_turn_on_shc_exception_raises_home_assistant_error(self):
        """A real API-level rejection must surface as HomeAssistantError, not raw."""
        device = SimpleNamespace(
            name="My State",
            id="uds1",
            root_device_id="mac1",
            deleted=False,
            state=False,
            async_set_state=AsyncMock(side_effect=SHCException("rejected")),
        )
        shc_entry = SimpleNamespace(
            name="SHC", id="shcid", identifiers=set(),
            manufacturer="Bosch", model="SHC2",
        )
        fake_entry = SimpleNamespace(entry_id="entry1")
        fake_entry.runtime_data = SimpleNamespace(shc_device=shc_entry)
        hass = SimpleNamespace(
            config_entries=SimpleNamespace(async_get_entry=lambda eid: fake_entry)
        )
        session = SimpleNamespace(
            subscribe_userdefinedstate_callback=lambda *a, **kw: None,
            unsubscribe_userdefinedstate_callbacks=lambda *a, **kw: None,
        )
        sw = SHCUserDefinedStateSwitch(
            device=device,
            hass=hass,
            session=session,
            entry_id="entry1",
            description=SWITCH_TYPES["user_defined_state"],
        )
        with pytest.raises(HomeAssistantError):
            asyncio.run(sw.async_turn_on())

    def test_turn_off_shc_connection_error_raises_home_assistant_error(self):
        """A comms failure on turn_off must surface as HomeAssistantError, not raw."""
        device = SimpleNamespace(
            name="My State",
            id="uds1",
            root_device_id="mac1",
            deleted=False,
            state=True,
            async_set_state=AsyncMock(side_effect=SHCConnectionError("no route")),
        )
        shc_entry = SimpleNamespace(
            name="SHC", id="shcid", identifiers=set(),
            manufacturer="Bosch", model="SHC2",
        )
        fake_entry = SimpleNamespace(entry_id="entry1")
        fake_entry.runtime_data = SimpleNamespace(shc_device=shc_entry)
        hass = SimpleNamespace(
            config_entries=SimpleNamespace(async_get_entry=lambda eid: fake_entry)
        )
        session = SimpleNamespace(
            subscribe_userdefinedstate_callback=lambda *a, **kw: None,
            unsubscribe_userdefinedstate_callbacks=lambda *a, **kw: None,
        )
        sw = SHCUserDefinedStateSwitch(
            device=device,
            hass=hass,
            session=session,
            entry_id="entry1",
            description=SWITCH_TYPES["user_defined_state"],
        )
        with pytest.raises(HomeAssistantError):
            asyncio.run(sw.async_turn_off())

    def test_should_poll_is_false(self):
        sw = _make_uds_switch()
        assert sw.should_poll is False

    def test_unique_id_no_attr_name(self):
        sw = _make_uds_switch(dev_id="uds9", root_id="mac9")
        assert sw._attr_unique_id == "mac9_uds9"

    def test_device_name_from_shc(self):
        sw = _make_uds_switch()
        assert sw.device_name == "SHC Controller"

    def test_device_id_from_shc(self):
        sw = _make_uds_switch()
        assert sw.device_id == "shc_device_id"

    def test_device_info_identifiers(self):
        sw = _make_uds_switch()
        info = sw.device_info
        assert info["identifiers"] == {("bosch_shc", "mac1")}

    def test_device_info_manufacturer(self):
        sw = _make_uds_switch()
        assert sw.device_info["manufacturer"] == "Bosch"

    def test_device_info_model(self):
        sw = _make_uds_switch()
        assert sw.device_info["model"] == "SHC 2"

    def test_device_info_name(self):
        sw = _make_uds_switch()
        assert sw.device_info["name"] == "SHC Controller"

    def test_attr_name_is_device_name_for_uds(self):
        """UDS entity: _attr_name must equal the UDS state name.

        UDS entities attach to the SHC hub device (not a physical device), so
        _attr_name=None would display the hub name only, losing the state label.
        The correct fix is to use device.name (e.g. 'My State') so HA shows a
        meaningful entity name like 'SHC Controller My State'.
        """
        sw = _make_uds_switch(name="My State")
        assert sw._attr_name == "My State"

    def test_uds_update_calls_device_update(self):
        """SHCUserDefinedStateSwitch.async_update() must call device.async_update() (#335)."""
        import asyncio
        from unittest.mock import AsyncMock

        class _Dev:
            name = "MyState"
            id = "uds1"
            root_device_id = "mac1"
            deleted = False
            state = False
            async_update = AsyncMock()

        shc_entry = SimpleNamespace(
            name="SHC", id="shcid", identifiers=set(),
            manufacturer="Bosch", model="SHC2",
        )
        fake_entry = SimpleNamespace(entry_id="entry1")
        fake_entry.runtime_data = SimpleNamespace(shc_device=shc_entry)
        hass = SimpleNamespace(
            config_entries=SimpleNamespace(async_get_entry=lambda eid: fake_entry)
        )
        session = SimpleNamespace(
            subscribe_userdefinedstate_callback=lambda *a, **kw: None,
            unsubscribe_userdefinedstate_callbacks=lambda *a, **kw: None,
        )
        sw = SHCUserDefinedStateSwitch(
            device=_Dev(),
            hass=hass,
            session=session,
            entry_id="entry1",
            description=SWITCH_TYPES["user_defined_state"],
        )
        asyncio.run(sw.async_update())
        sw._device.async_update.assert_awaited_once()


class TestUserDefinedStatesPath:
    """session.userdefinedstates items each produce a SHCUserDefinedStateSwitch."""

    def _setup_with_uds(self, uds_list):
        session = _make_exclusion_session(userdefinedstates=uds_list)
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


def test_setup_userdefinedstate_one_switch(mock_config_entry, mock_session):
    uds = _fake_uds(name="Home", dev_id="uds1", root_id="mac1", state=True)
    mock_session.userdefinedstates = [uds]
    entities = _setup_switch(mock_config_entry, mock_session)
    assert len(entities) == 1
    assert isinstance(entities[0], SHCUserDefinedStateSwitch)


def test_setup_userdefinedstate_entity_description(mock_config_entry, mock_session):
    uds = _fake_uds(name="Away", dev_id="uds2", root_id="mac1")
    mock_session.userdefinedstates = [uds]
    entities = _setup_switch(mock_config_entry, mock_session)
    assert entities[0].entity_description.key == "user_defined_state"


def test_setup_userdefinedstate_unique_id(mock_config_entry, mock_session):
    uds = _fake_uds(name="Night", dev_id="uds4", root_id="macABC")
    mock_session.userdefinedstates = [uds]
    entities = _setup_switch(mock_config_entry, mock_session)
    assert entities[0]._attr_unique_id == "macABC_uds4"


def test_setup_userdefinedstate_attr_name(mock_config_entry, mock_session):
    uds = _fake_uds(name="Vacation Mode", dev_id="uds5", root_id="mac1")
    mock_session.userdefinedstates = [uds]
    entities = _setup_switch(mock_config_entry, mock_session)
    assert entities[0]._attr_name == "Vacation Mode"


def test_setup_userdefinedstate_multiple(mock_config_entry, mock_session):
    uds1 = _fake_uds(name="Home", dev_id="u1", root_id="mac1")
    uds2 = _fake_uds(name="Away", dev_id="u2", root_id="mac1")
    mock_session.userdefinedstates = [uds1, uds2]
    entities = _setup_switch(mock_config_entry, mock_session)
    assert len(entities) == 2


def test_uds_switch_is_on_true():
    sw = _make_setup_uds_switch(state=True)
    assert sw.is_on is True


def test_uds_switch_is_on_false():
    sw = _make_setup_uds_switch(state=False)
    assert sw.is_on is False


def test_uds_switch_turn_on_sets_state():
    """async_turn_on awaits device.async_set_state(True)."""
    mock_set = AsyncMock()
    device = SimpleNamespace(
        name="X", id="u1", root_device_id="mac1",
        state=False, async_set_state=mock_set,
    )
    shc_dev = SimpleNamespace(
        name="SHC",
        id="shc_dev",
        identifiers={("bosch_shc", "shc_dev")},
        manufacturer="Bosch",
        model="SHC",
    )
    hass = _make_fake_hass(shc_dev)
    sw = SHCUserDefinedStateSwitch(
        device=device,
        hass=hass,
        session=SimpleNamespace(subscribe=MagicMock(), _subscribers=[]),
        entry_id="E1",
        description=SWITCH_TYPES["user_defined_state"],
    )
    asyncio.run(sw.async_turn_on())
    mock_set.assert_awaited_once_with(True)


def test_uds_switch_turn_off_sets_state():
    """async_turn_off awaits device.async_set_state(False)."""
    mock_set = AsyncMock()
    device = SimpleNamespace(
        name="Y", id="u2", root_device_id="mac2",
        state=True, async_set_state=mock_set,
    )
    shc_dev = SimpleNamespace(
        name="SHC",
        id="shc_dev",
        identifiers={("bosch_shc", "shc_dev")},
        manufacturer="Bosch",
        model="SHC",
    )
    hass = _make_fake_hass(shc_dev)
    sw = SHCUserDefinedStateSwitch(
        device=device,
        hass=hass,
        session=SimpleNamespace(subscribe=MagicMock(), _subscribers=[]),
        entry_id="E1",
        description=SWITCH_TYPES["user_defined_state"],
    )
    asyncio.run(sw.async_turn_off())
    mock_set.assert_awaited_once_with(False)


def test_uds_switch_should_poll_false():
    sw = _make_setup_uds_switch()
    assert sw.should_poll is False


def test_uds_switch_unique_id_no_attr_name():
    sw = _make_setup_uds_switch(name="Test", dev_id="ud99", root_id="macXX")
    assert sw._attr_unique_id == "macXX_ud99"


def test_uds_switch_attr_name():
    sw = _make_setup_uds_switch(name="Holiday")
    assert sw._attr_name == "Holiday"


def test_uds_switch_device_name():
    sw = _make_setup_uds_switch()
    assert sw.device_name == "SHC"


def test_uds_switch_device_id():
    sw = _make_setup_uds_switch()
    assert sw.device_id == "shc_dev"


def test_uds_switch_device_info_keys():
    sw = _make_setup_uds_switch()
    info = sw.device_info
    assert set(info.keys()) == {"identifiers", "name", "manufacturer", "model"}


def test_uds_switch_device_info_values():
    sw = _make_setup_uds_switch()
    info = sw.device_info
    assert info["name"] == "SHC"
    assert info["manufacturer"] == "Bosch"
    assert info["model"] == "SHC"


def test_uds_switch_update_calls_device_update():
    """SHCUserDefinedStateSwitch.async_update() must call device.async_update() (#335)."""
    import asyncio
    from unittest.mock import AsyncMock

    class _FakeUDS:
        name = "U"
        id = "u1"
        root_device_id = "mac1"
        state = True
        async_update = AsyncMock()

    shc_dev = SimpleNamespace(
        name="SHC",
        id="shc_dev",
        identifiers={("bosch_shc", "shc_dev")},
        manufacturer="Bosch",
        model="SHC",
    )
    hass = _make_fake_hass(shc_dev)
    sw = SHCUserDefinedStateSwitch(
        device=_FakeUDS(),
        hass=hass,
        session=SimpleNamespace(subscribe=MagicMock(), _subscribers=[]),
        entry_id="E1",
        description=SWITCH_TYPES["user_defined_state"],
    )
    asyncio.run(sw.async_update())
    sw._device.async_update.assert_awaited_once()


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
    hass = _make_fake_hass(shc_dev)
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

    async def _run_setup_coro():
        # Patch SwitchEntity.async_added_to_hass to be a no-op
        with patch(
            "homeassistant.components.switch.SwitchEntity.async_added_to_hass",
            new=AsyncMock(return_value=None),
        ):
            await sw.async_added_to_hass()

    asyncio.run(_run_setup_coro())

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
    hass = _make_fake_hass(shc_dev)
    sw = SHCUserDefinedStateSwitch(
        device=uds,
        hass=hass,
        session=session,
        entry_id="E1",
        description=SWITCH_TYPES["user_defined_state"],
    )

    async def _run_setup_coro():
        with patch(
            "homeassistant.components.switch.SwitchEntity.async_will_remove_from_hass",
            new=AsyncMock(return_value=None),
        ):
            await sw.async_will_remove_from_hass()

    asyncio.run(_run_setup_coro())

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
    hass_inner = _make_fake_hass(shc_dev)
    sw = SHCUserDefinedStateSwitch(
        device=uds,
        hass=hass_inner,
        session=session,
        entry_id="E1",
        description=SWITCH_TYPES["user_defined_state"],
    )
    sw.schedule_update_ha_state = lambda: scheduled.append(True)
    sw.hass = SimpleNamespace(add_job=MagicMock())

    async def _run_setup_coro():
        with patch(
            "homeassistant.components.switch.SwitchEntity.async_added_to_hass",
            new=AsyncMock(return_value=None),
        ):
            await sw.async_added_to_hass()

    asyncio.run(_run_setup_coro())

    # Fire the first callback (on_state_changed)
    first_cb = session.subscribe_userdefinedstate_callback.call_args_list[0][0][1]
    first_cb()
    assert len(scheduled) >= 1


def test_uds_switch_update_entity_information_deleted():
    """update_entity_information callback: deleted device → sets unavailable +
    schedules removal via the thread-safe hass.create_task() — NOT
    hass.async_create_task(), which is not thread-safe and raises when called
    from boschshcpy's background polling thread (this callback's real caller).
    """
    task_calls: list = []
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
    hass_inner = _make_fake_hass(shc_dev)
    sw = SHCUserDefinedStateSwitch(
        device=uds,
        hass=hass_inner,
        session=session,
        entry_id="E1",
        description=SWITCH_TYPES["user_defined_state"],
    )
    sw.schedule_update_ha_state = lambda: scheduled.append(True)
    fake_loop = SimpleNamespace(call_soon_threadsafe=MagicMock())
    # Fake hass exposing only the thread-safe create_task(); a stray
    # async_create_task() call would raise AttributeError here, exactly like
    # the real (non-thread-safe) HA method would raise off-loop.
    mock_hass = SimpleNamespace(
        loop=fake_loop,
        create_task=lambda coro: task_calls.append(coro),
    )
    sw.hass = mock_hass

    async def _run_setup_coro():
        with patch(
            "homeassistant.components.switch.SwitchEntity.async_added_to_hass",
            new=AsyncMock(return_value=None),
        ):
            await sw.async_added_to_hass()

    asyncio.run(_run_setup_coro())

    # Fire the second callback (update_entity_information)
    second_cb = session.subscribe_userdefinedstate_callback.call_args_list[1][0][1]
    second_cb()

    # Entity should be marked unavailable and create_task called (not call_soon_threadsafe)
    assert sw._attr_available is False
    assert len(task_calls) >= 1
    assert not fake_loop.call_soon_threadsafe.called


@pytest.mark.parametrize(
    "name",
    [
        "My State",
        "Schlafzimmer",
        "Küche Licht",
        "Büro",
        "Außenbereich",
        "State With UPPERCASE",
        "Gäste WC",
        "123 Numbers OK",
        "mixed CASE with Ümlauts",
        "state_already_slug",
    ],
)
def test_userdefined_state_slug_is_valid(name: str) -> None:
    """entity_id slug must match ^[a-z0-9_]+$ for any device name."""
    slug = _make_slug(name)
    # The full entity_id would be "switch.<slug>"; test the object-id portion.
    object_id = slug  # already prefixed with "userdefinedstate_"
    assert VALID_SLUG_RE.match(object_id), (
        f"Slug {object_id!r} (from name {name!r}) contains invalid characters"
    )


def test_umlaut_names_do_not_produce_empty_slug() -> None:
    """Umlaut-only names must not yield an empty or underscore-only slug."""
    slug = slugify("Ää Öö Üü")
    assert slug, "slugify must not return an empty string for umlaut names"
    assert VALID_SLUG_RE.match(slug), f"Slug {slug!r} contains invalid characters"


class TestUDSSwitchAsyncUpdateFallback:
    """Line 1135: SHCUserDefinedStateSwitch.async_update executor fallback."""

    def test_async_update_fallback_to_executor(self):
        """Line 1135: _has_async_update=False → executor job."""
        from custom_components.bosch_shc.switch import SHCUserDefinedStateSwitch

        sw = SHCUserDefinedStateSwitch.__new__(SHCUserDefinedStateSwitch)
        sw._has_async_update = False

        entity_description = SimpleNamespace(should_poll=True)
        sw.entity_description = entity_description

        update_called = []

        def sync_update():
            update_called.append(True)

        sw._device = SimpleNamespace(update=sync_update)

        async def fake_executor_job(fn, *args):
            fn(*args)

        sw.hass = SimpleNamespace(async_add_executor_job=fake_executor_job)
        _run(sw.async_update())
        assert update_called


class TestUDSSwitchAvailableProperty:
    """switch.py:1102 — available property on SHCUserDefinedStateSwitch."""

    def test_available_reflects_deleted_flag(self):
        from custom_components.bosch_shc.switch import SHCUserDefinedStateSwitch
        sw = SHCUserDefinedStateSwitch.__new__(SHCUserDefinedStateSwitch)
        sw._device = SimpleNamespace(deleted=False)
        assert sw.available is True
        sw._device.deleted = True
        assert sw.available is False




# ---------------------------------------------------------------------------
# IntrusionAlarm
# ---------------------------------------------------------------------------


class TestSwitchSmokeDetectorIntrusionAlarm:
    """Line 860: smoke_detector with supports_intrusion_alarm."""

    def _run_switch_setup_smoke_detectors(self, smoke_detectors, options=None):
        from custom_components.bosch_shc.switch import async_setup_entry

        dh = MagicMock()
        dh.smart_plugs = []
        dh.smart_plugs_compact = []
        dh.micromodule_relays = []
        dh.micromodule_impulse_relays = []
        dh.micromodule_dimmers = []
        dh.light_switches_bsm = []
        dh.micromodule_light_attached = []
        dh.micromodule_light_controls = []
        dh.camera_eyes = []
        dh.camera_360 = []
        dh.camera_outdoor_gen2 = []
        dh.presence_simulation_system = None
        dh.shutter_contacts2 = []
        dh.thermostats = []
        dh.roomthermostats = []
        dh.wallthermostats = []
        dh.motion_detectors2 = []
        dh.universal_switches = []
        dh.twinguards = []
        dh.smoke_detectors = smoke_detectors
        dh.smoke_detection_system = None

        session = MagicMock()
        session.device_helper = dh
        session.userdefinedstates = []
        session._subscribers = []
        session.subscribe = MagicMock()

        hass = _fake_hass(session=session)
        entry = _fake_entry(hass=hass, options=options or {})
        entry.async_on_unload = MagicMock()

        with patch("custom_components.bosch_shc.switch.async_migrate_to_new_unique_id",
                   new_callable=AsyncMock):
            collected = []
            _run(async_setup_entry(hass, entry, lambda ents, **kw: collected.extend(ents)))
        return collected

    def test_smoke_detector_with_intrusion_alarm_switch(self):
        """Line 860: supports_intrusion_alarm=True → intrusion alarm switch added."""
        dev = _fake_dev("sd1", supports_intrusion_alarm=True,
                        supports_smoke_sensitivity=False)
        collected = self._run_switch_setup_smoke_detectors([dev])
        unique_ids = [getattr(e, "_attr_unique_id", "") for e in collected]
        # SHCSwitch uses attr_name.lower() in unique_id → "intrusionalarm"
        assert any("intrusionalarm" in uid for uid in unique_ids)




# ---------------------------------------------------------------------------
# Cross-cutting (multi-type / generic behavior, not tied to one entity)
# ---------------------------------------------------------------------------


def test_attr_name_no_suffix():
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = SimpleNamespace(name="Plug 1", root_device_id="rootA", id="devB")
    sw.entity_description = SWITCH_TYPES["smartplug"]
    _init_name_and_id(sw, attr_name=None)
    assert sw._attr_name == "Plug 1"


def test_attr_name_with_suffix():
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = SimpleNamespace(name="Plug 1", root_device_id="rootA", id="devB")
    sw.entity_description = SWITCH_TYPES["smartplug_routing"]
    _init_name_and_id(sw, attr_name="Routing")
    assert sw._attr_name == "Plug 1 Routing"


def test_unique_id_no_suffix():
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = SimpleNamespace(name="Dev", root_device_id="root1", id="dev1")
    sw.entity_description = SWITCH_TYPES["smartplug"]
    _init_name_and_id(sw, attr_name=None)
    assert sw._attr_unique_id == "root1_dev1"


def test_unique_id_with_suffix():
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = SimpleNamespace(name="Dev", root_device_id="root1", id="dev1")
    sw.entity_description = SWITCH_TYPES["smartplug_routing"]
    _init_name_and_id(sw, attr_name="Routing")
    assert sw._attr_unique_id == "root1_dev1_routing"


def test_unique_id_suffix_is_lowercased():
    """attr_name is .lower()'d in the unique_id — CamelCase becomes lowercase."""
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = SimpleNamespace(name="Cam", root_device_id="rX", id="dY")
    sw.entity_description = SWITCH_TYPES["cameraeyes_cameralight"]
    _init_name_and_id(sw, attr_name="Light")
    assert sw._attr_unique_id == "rX_dY_light"


def test_attr_name_camera_with_suffix():
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = SimpleNamespace(name="MyCamera", root_device_id="rc", id="dc")
    sw.entity_description = SWITCH_TYPES["cameraeyes_notification"]
    _init_name_and_id(sw, attr_name="Notification")
    assert sw._attr_name == "MyCamera Notification"
    assert sw._attr_unique_id == "rc_dc_notification"


def test_switch_types_all_keys_present():
    """All expected SWITCH_TYPES keys must exist."""
    expected = {
        "smartplug",
        "smartplug_routing",
        "smartplugcompact",
        "micromodule_relay_switch",
        "lightswitch",
        "cameraeyes",
        "cameraeyes_cameralight",
        "cameraeyes_notification",
        "camera360",
        "camera360_notification",
        "cameraoutdoorgen2",
        "cameraoutdoorgen2_camerafrontlight",
        "cameraoutdoorgen2_cameraambientlight",
        "presencesimulation",
        "bypass",
        "bypass_infinite",
        "child_lock",
        "child_lock_thermostat",
        "pet_immunity_enabled",
        "silent_mode",
        "vibration_enabled",
        "user_defined_state",
        # New APK-batch 2-6 switch types (guarded by hasattr in setup):
        "energy_saving_mode_enabled",
        "warning_suppressed",
        "nightly_promise_enabled",
        "humidity_warning_enabled",
        "swap_inputs",
        "swap_outputs",
        "pre_alarm_enabled",
        "smart_sensitivity_enabled",
        "tamper_protection_enabled",
        "intrusion_alarm",
    }
    assert expected == set(SWITCH_TYPES.keys())


class TestSwitchTypeMetadata:
    """Verify device_class, icon, entity_category on each descriptor."""

    def test_smartplug_device_class_outlet(self):
        assert SWITCH_TYPES["smartplug"].device_class == SwitchDeviceClass.OUTLET

    def test_smartplug_routing_device_class_switch(self):
        assert SWITCH_TYPES["smartplug_routing"].device_class == SwitchDeviceClass.SWITCH

    def test_smartplug_routing_icon(self):
        assert SWITCH_TYPES["smartplug_routing"].icon == "mdi:wifi"

    def test_smartplug_routing_entity_category_config(self):
        assert SWITCH_TYPES["smartplug_routing"].entity_category == EntityCategory.CONFIG

    def test_cameraeyes_icon(self):
        assert SWITCH_TYPES["cameraeyes"].icon == "mdi:video"

    def test_cameraeyes_cameralight_icon(self):
        assert SWITCH_TYPES["cameraeyes_cameralight"].icon == "mdi:light-flood-down"

    def test_cameraeyes_cameralight_entity_category_config(self):
        assert (
            SWITCH_TYPES["cameraeyes_cameralight"].entity_category
            == EntityCategory.CONFIG
        )

    def test_cameraeyes_notification_icon(self):
        assert SWITCH_TYPES["cameraeyes_notification"].icon == "mdi:message-badge"

    def test_cameraeyes_notification_entity_category_config(self):
        assert (
            SWITCH_TYPES["cameraeyes_notification"].entity_category
            == EntityCategory.CONFIG
        )

    def test_camera360_icon(self):
        assert SWITCH_TYPES["camera360"].icon == "mdi:video"

    def test_camera360_notification_entity_category_config(self):
        assert (
            SWITCH_TYPES["camera360_notification"].entity_category
            == EntityCategory.CONFIG
        )

    def test_cameraoutdoorgen2_icon(self):
        assert SWITCH_TYPES["cameraoutdoorgen2"].icon == "mdi:video"

    def test_cameraoutdoorgen2_frontlight_entity_category_config(self):
        assert (
            SWITCH_TYPES["cameraoutdoorgen2_camerafrontlight"].entity_category
            == EntityCategory.CONFIG
        )

    def test_cameraoutdoorgen2_ambientlight_icon(self):
        assert SWITCH_TYPES["cameraoutdoorgen2_cameraambientlight"].icon == "mdi:wall-sconce-flat"

    def test_child_lock_icon(self):
        assert SWITCH_TYPES["child_lock"].icon == "mdi:lock"

    def test_child_lock_entity_category_config(self):
        assert SWITCH_TYPES["child_lock"].entity_category == EntityCategory.CONFIG

    def test_child_lock_thermostat_icon(self):
        assert SWITCH_TYPES["child_lock_thermostat"].icon == "mdi:lock"

    def test_child_lock_thermostat_entity_category_config(self):
        assert (
            SWITCH_TYPES["child_lock_thermostat"].entity_category == EntityCategory.CONFIG
        )

    def test_pet_immunity_icon(self):
        assert SWITCH_TYPES["pet_immunity_enabled"].icon == "mdi:paw"

    def test_silent_mode_icon(self):
        assert SWITCH_TYPES["silent_mode"].icon == "mdi:sleep"

    def test_silent_mode_entity_category_config(self):
        assert SWITCH_TYPES["silent_mode"].entity_category == EntityCategory.CONFIG

    def test_presencesimulation_device_class(self):
        assert SWITCH_TYPES["presencesimulation"].device_class == SwitchDeviceClass.SWITCH

    def test_bypass_translation_key_not_hardcoded_icon(self):
        """#342: bypass is clearly named via translation_key; icon lives in
        icons.json (a hardcoded description.icon would win over icons.json's
        lookup and defeat icon translation, per the same rule already
        enforced for _attr_icon by check-icon-translations.py)."""
        assert SWITCH_TYPES["bypass"].icon is None
        assert SWITCH_TYPES["bypass"].translation_key == "bypass"

    def test_user_defined_state_entity_category_config(self):
        assert (
            SWITCH_TYPES["user_defined_state"].entity_category == EntityCategory.CONFIG
        )


class TestEdgeStateIsOn:
    """Cover rarely-tested enum values (NONE, UNKNOWN) and bool boundaries."""

    def test_bypass_unknown_is_off(self):
        """UNKNOWN bypass state → is_on False (not ON_VALUE)."""
        State = BypassService.State
        sw = _make_switch(SWITCH_TYPES["bypass"], bypass=State.UNKNOWN)
        assert sw.is_on is False

    def test_cameraeyes_cameralight_none_is_off(self):
        """CameraLight.NONE → is_on False."""
        State = CameraLightService.State
        sw = _make_switch(SWITCH_TYPES["cameraeyes_cameralight"], cameralight=State.NONE)
        assert sw.is_on is False

    def test_cameraoutdoorgen2_frontlight_none_is_off(self):
        """FrontLight.NONE → is_on False."""
        State = CameraFrontLightService.State
        sw = _make_switch(
            SWITCH_TYPES["cameraoutdoorgen2_camerafrontlight"],
            camerafrontlight=State.NONE,
        )
        assert sw.is_on is False

    def test_cameraoutdoorgen2_ambientlight_none_is_off(self):
        """AmbientLight.NONE → is_on False."""
        State = CameraAmbientLightService.State
        sw = _make_switch(
            SWITCH_TYPES["cameraoutdoorgen2_cameraambientlight"],
            cameraambientlight=State.NONE,
        )
        assert sw.is_on is False

    def test_child_lock_bool_false_is_off(self):
        """child_lock=False → is_on False (bool path)."""
        sw = _make_switch(SWITCH_TYPES["child_lock"], child_lock=False)
        assert sw.is_on is False

    def test_child_lock_thermostat_enum_off_is_off(self):
        """ThermostatService.State.OFF → is_on False (enum path)."""
        State = ThermostatService.State
        sw = _make_switch(SWITCH_TYPES["child_lock_thermostat"], child_lock=State.OFF)
        assert sw.is_on is False

    def test_child_lock_thermostat_bool_false_does_not_match(self):
        """child_lock_thermostat compares against enum State.ON — False must NOT match."""
        sw = _make_switch(SWITCH_TYPES["child_lock_thermostat"], child_lock=False)
        assert sw.is_on is False

    def test_pet_immunity_false_is_off(self):
        sw = _make_switch(SWITCH_TYPES["pet_immunity_enabled"], pet_immunity_enabled=False)
        assert sw.is_on is False

    def test_pet_immunity_true_is_on(self):
        sw = _make_switch(SWITCH_TYPES["pet_immunity_enabled"], pet_immunity_enabled=True)
        assert sw.is_on is True

    def test_camera360_cameranotification_disabled_is_off(self):
        State = CameraNotificationService.State
        sw = _make_switch(
            SWITCH_TYPES["camera360_notification"],
            cameranotification=State.DISABLED,
        )
        assert sw.is_on is False


class TestNoneGuardIsOn:
    """is_on must return None (not raise) for any unregistered service."""

    def test_child_lock_service_none_is_on(self):
        sw = SHCSwitch.__new__(SHCSwitch)
        sw._device = _NoChildLock()
        sw.entity_description = SWITCH_TYPES["child_lock"]
        assert sw.is_on is None

    def test_child_lock_thermostat_service_none_is_on(self):
        sw = SHCSwitch.__new__(SHCSwitch)
        sw._device = _NoChildLock()
        sw.entity_description = SWITCH_TYPES["child_lock_thermostat"]
        assert sw.is_on is None

    def test_bypass_service_none_is_on(self):
        sw = SHCSwitch.__new__(SHCSwitch)
        sw._device = _NoBypass()
        sw.entity_description = SWITCH_TYPES["bypass"]
        assert sw.is_on is None

    def test_presencesimulation_service_none_is_on(self):
        sw = SHCSwitch.__new__(SHCSwitch)
        sw._device = _NoEnabled()
        sw.entity_description = SWITCH_TYPES["presencesimulation"]
        assert sw.is_on is None

    def test_vibration_service_none_is_on(self):
        sw = SHCSwitch.__new__(SHCSwitch)
        sw._device = _NoEnabled()
        sw.entity_description = SWITCH_TYPES["vibration_enabled"]
        assert sw.is_on is None

    def test_silent_mode_service_none_is_on(self):
        sw = SHCSwitch.__new__(SHCSwitch)
        sw._device = _NoSilentMode()
        sw.entity_description = SWITCH_TYPES["silent_mode"]
        assert sw.is_on is None

    def test_pet_immunity_service_none_is_on(self):
        sw = SHCSwitch.__new__(SHCSwitch)
        sw._device = _NoPetImmunity()
        sw.entity_description = SWITCH_TYPES["pet_immunity_enabled"]
        assert sw.is_on is None

    def test_user_defined_state_service_none_is_on(self):
        sw = SHCSwitch.__new__(SHCSwitch)
        sw._device = _NoState()
        sw.entity_description = SWITCH_TYPES["user_defined_state"]
        assert sw.is_on is None

    def test_cameraeyes_notification_service_none_is_on(self):
        sw = SHCSwitch.__new__(SHCSwitch)
        sw._device = _NoCameraNotification()
        sw.entity_description = SWITCH_TYPES["cameraeyes_notification"]
        assert sw.is_on is None

    def test_cameraoutdoorgen2_ambientlight_service_none_is_on(self):
        sw = SHCSwitch.__new__(SHCSwitch)
        sw._device = _NoAmbientLight()
        sw.entity_description = SWITCH_TYPES["cameraoutdoorgen2_cameraambientlight"]
        assert sw.is_on is None


class TestNoneGuardTurnOn:
    """async_turn_on must swallow AttributeError when async_set_<key> is absent."""

    def test_child_lock_service_none_turn_on(self):
        sw = SHCSwitch.__new__(SHCSwitch)
        sw._device = _NoChildLock()
        sw.entity_description = SWITCH_TYPES["child_lock"]
        sw.entity_id = "switch.test"
        asyncio.run(sw.async_turn_on())  # must not raise

    def test_bypass_service_none_turn_on(self):
        sw = SHCSwitch.__new__(SHCSwitch)
        sw._device = _NoBypass()
        sw.entity_description = SWITCH_TYPES["bypass"]
        sw.entity_id = "switch.test"
        asyncio.run(sw.async_turn_on())  # must not raise

    def test_presencesimulation_service_none_turn_on(self):
        sw = SHCSwitch.__new__(SHCSwitch)
        sw._device = _NoEnabled()
        sw.entity_description = SWITCH_TYPES["presencesimulation"]
        sw.entity_id = "switch.test"
        asyncio.run(sw.async_turn_on())  # must not raise

    def test_vibration_service_none_turn_on(self):
        sw = SHCSwitch.__new__(SHCSwitch)
        sw._device = _NoEnabled()
        sw.entity_description = SWITCH_TYPES["vibration_enabled"]
        sw.entity_id = "switch.test"
        asyncio.run(sw.async_turn_on())  # must not raise

    def test_silent_mode_service_none_turn_on(self):
        sw = SHCSwitch.__new__(SHCSwitch)
        sw._device = _NoSilentMode()
        sw.entity_description = SWITCH_TYPES["silent_mode"]
        sw.entity_id = "switch.test"
        asyncio.run(sw.async_turn_on())  # must not raise

    def test_pet_immunity_service_none_turn_on(self):
        sw = SHCSwitch.__new__(SHCSwitch)
        sw._device = _NoPetImmunity()
        sw.entity_description = SWITCH_TYPES["pet_immunity_enabled"]
        sw.entity_id = "switch.test"
        asyncio.run(sw.async_turn_on())  # must not raise

    def test_cameraeyes_notification_service_none_turn_on(self):
        sw = SHCSwitch.__new__(SHCSwitch)
        sw._device = _NoCameraNotification()
        sw.entity_description = SWITCH_TYPES["cameraeyes_notification"]
        sw.entity_id = "switch.test"
        asyncio.run(sw.async_turn_on())  # must not raise

    def test_cameraoutdoorgen2_ambientlight_service_none_turn_on(self):
        sw = SHCSwitch.__new__(SHCSwitch)
        sw._device = _NoAmbientLight()
        sw.entity_description = SWITCH_TYPES["cameraoutdoorgen2_cameraambientlight"]
        sw.entity_id = "switch.test"
        asyncio.run(sw.async_turn_on())  # must not raise

    def test_user_defined_state_service_none_turn_on(self):
        sw = SHCSwitch.__new__(SHCSwitch)
        sw._device = _NoState()
        sw.entity_description = SWITCH_TYPES["user_defined_state"]
        sw.entity_id = "switch.test"
        asyncio.run(sw.async_turn_on())  # must not raise


class TestNoneGuardTurnOff:
    """async_turn_off must swallow AttributeError when async_set_<key> is absent."""

    def test_child_lock_service_none_turn_off(self):
        sw = SHCSwitch.__new__(SHCSwitch)
        sw._device = _NoChildLock()
        sw.entity_description = SWITCH_TYPES["child_lock"]
        sw.entity_id = "switch.test"
        asyncio.run(sw.async_turn_off())  # must not raise

    def test_child_lock_thermostat_service_none_turn_off(self):
        sw = SHCSwitch.__new__(SHCSwitch)
        sw._device = _NoChildLock()
        sw.entity_description = SWITCH_TYPES["child_lock_thermostat"]
        sw.entity_id = "switch.test"
        asyncio.run(sw.async_turn_off())  # must not raise

    def test_bypass_service_none_turn_off(self):
        sw = SHCSwitch.__new__(SHCSwitch)
        sw._device = _NoBypass()
        sw.entity_description = SWITCH_TYPES["bypass"]
        sw.entity_id = "switch.test"
        asyncio.run(sw.async_turn_off())  # must not raise

    def test_presencesimulation_service_none_turn_off(self):
        sw = SHCSwitch.__new__(SHCSwitch)
        sw._device = _NoEnabled()
        sw.entity_description = SWITCH_TYPES["presencesimulation"]
        sw.entity_id = "switch.test"
        asyncio.run(sw.async_turn_off())  # must not raise

    def test_vibration_service_none_turn_off(self):
        sw = SHCSwitch.__new__(SHCSwitch)
        sw._device = _NoEnabled()
        sw.entity_description = SWITCH_TYPES["vibration_enabled"]
        sw.entity_id = "switch.test"
        asyncio.run(sw.async_turn_off())  # must not raise

    def test_silent_mode_service_none_turn_off(self):
        sw = SHCSwitch.__new__(SHCSwitch)
        sw._device = _NoSilentMode()
        sw.entity_description = SWITCH_TYPES["silent_mode"]
        sw.entity_id = "switch.test"
        asyncio.run(sw.async_turn_off())  # must not raise

    def test_pet_immunity_service_none_turn_off(self):
        sw = SHCSwitch.__new__(SHCSwitch)
        sw._device = _NoPetImmunity()
        sw.entity_description = SWITCH_TYPES["pet_immunity_enabled"]
        sw.entity_id = "switch.test"
        asyncio.run(sw.async_turn_off())  # must not raise

    def test_cameraeyes_notification_service_none_turn_off(self):
        sw = SHCSwitch.__new__(SHCSwitch)
        sw._device = _NoCameraNotification()
        sw.entity_description = SWITCH_TYPES["cameraeyes_notification"]
        sw.entity_id = "switch.test"
        asyncio.run(sw.async_turn_off())  # must not raise

    def test_cameraoutdoorgen2_ambientlight_service_none_turn_off(self):
        sw = SHCSwitch.__new__(SHCSwitch)
        sw._device = _NoAmbientLight()
        sw.entity_description = SWITCH_TYPES["cameraoutdoorgen2_cameraambientlight"]
        sw.entity_id = "switch.test"
        asyncio.run(sw.async_turn_off())  # must not raise

    def test_user_defined_state_service_none_turn_off(self):
        sw = SHCSwitch.__new__(SHCSwitch)
        sw._device = _NoState()
        sw.entity_description = SWITCH_TYPES["user_defined_state"]
        sw.entity_id = "switch.test"
        asyncio.run(sw.async_turn_off())  # must not raise


class TestTurnOnOffSetters:
    """Ensure async_turn_on/off await async_set_<key>(True/False)."""

    def test_child_lock_turn_on_writes_true(self):
        sw, mock = _spy_switch(SWITCH_TYPES["child_lock"], "child_lock")
        asyncio.run(sw.async_turn_on())
        mock.assert_awaited_once_with(True)

    def test_child_lock_turn_off_writes_false(self):
        sw, mock = _spy_switch(SWITCH_TYPES["child_lock"], "child_lock")
        asyncio.run(sw.async_turn_off())
        mock.assert_awaited_once_with(False)

    def test_child_lock_thermostat_turn_on_writes_true(self):
        sw, mock = _spy_switch(SWITCH_TYPES["child_lock_thermostat"], "child_lock")
        asyncio.run(sw.async_turn_on())
        mock.assert_awaited_once_with(True)

    def test_child_lock_thermostat_turn_off_writes_false(self):
        sw, mock = _spy_switch(SWITCH_TYPES["child_lock_thermostat"], "child_lock")
        asyncio.run(sw.async_turn_off())
        mock.assert_awaited_once_with(False)

    def test_bypass_turn_on_writes_true(self):
        sw, mock = _spy_switch(SWITCH_TYPES["bypass"], "bypass")
        asyncio.run(sw.async_turn_on())
        mock.assert_awaited_once_with(True)

    def test_bypass_turn_off_writes_false(self):
        sw, mock = _spy_switch(SWITCH_TYPES["bypass"], "bypass")
        asyncio.run(sw.async_turn_off())
        mock.assert_awaited_once_with(False)

    def test_silent_mode_turn_on_writes_true(self):
        sw, mock = _spy_switch(SWITCH_TYPES["silent_mode"], "silentmode")
        asyncio.run(sw.async_turn_on())
        mock.assert_awaited_once_with(True)

    def test_silent_mode_turn_off_writes_false(self):
        sw, mock = _spy_switch(SWITCH_TYPES["silent_mode"], "silentmode")
        asyncio.run(sw.async_turn_off())
        mock.assert_awaited_once_with(False)

    def test_vibration_enabled_turn_on_writes_true(self):
        sw, mock = _spy_switch(SWITCH_TYPES["vibration_enabled"], "enabled")
        asyncio.run(sw.async_turn_on())
        mock.assert_awaited_once_with(True)

    def test_vibration_enabled_turn_off_writes_false(self):
        sw, mock = _spy_switch(SWITCH_TYPES["vibration_enabled"], "enabled")
        asyncio.run(sw.async_turn_off())
        mock.assert_awaited_once_with(False)

    def test_cameraeyes_notification_turn_on_writes_true(self):
        sw, mock = _spy_switch(
            SWITCH_TYPES["cameraeyes_notification"], "cameranotification"
        )
        asyncio.run(sw.async_turn_on())
        mock.assert_awaited_once_with(True)

    def test_cameraeyes_notification_turn_off_writes_false(self):
        sw, mock = _spy_switch(
            SWITCH_TYPES["cameraeyes_notification"], "cameranotification"
        )
        asyncio.run(sw.async_turn_off())
        mock.assert_awaited_once_with(False)

    def test_camera360_notification_turn_on_writes_true(self):
        sw, mock = _spy_switch(
            SWITCH_TYPES["camera360_notification"], "cameranotification"
        )
        asyncio.run(sw.async_turn_on())
        mock.assert_awaited_once_with(True)

    def test_camera360_notification_turn_off_writes_false(self):
        sw, mock = _spy_switch(
            SWITCH_TYPES["camera360_notification"], "cameranotification"
        )
        asyncio.run(sw.async_turn_off())
        mock.assert_awaited_once_with(False)

    def test_cameraoutdoorgen2_ambientlight_turn_on_writes_true(self):
        sw, mock = _spy_switch(
            SWITCH_TYPES["cameraoutdoorgen2_cameraambientlight"], "cameraambientlight"
        )
        asyncio.run(sw.async_turn_on())
        mock.assert_awaited_once_with(True)

    def test_cameraoutdoorgen2_ambientlight_turn_off_writes_false(self):
        sw, mock = _spy_switch(
            SWITCH_TYPES["cameraoutdoorgen2_cameraambientlight"], "cameraambientlight"
        )
        asyncio.run(sw.async_turn_off())
        mock.assert_awaited_once_with(False)

    def test_cameraeyes_privacy_turn_on_writes_true(self):
        sw, mock = _spy_switch(SWITCH_TYPES["cameraeyes"], "privacymode")
        asyncio.run(sw.async_turn_on())
        mock.assert_awaited_once_with(True)

    def test_cameraeyes_privacy_turn_off_writes_false(self):
        sw, mock = _spy_switch(SWITCH_TYPES["cameraeyes"], "privacymode")
        asyncio.run(sw.async_turn_off())
        mock.assert_awaited_once_with(False)

    def test_camera360_privacy_turn_on_writes_true(self):
        sw, mock = _spy_switch(SWITCH_TYPES["camera360"], "privacymode")
        asyncio.run(sw.async_turn_on())
        mock.assert_awaited_once_with(True)

    def test_cameraoutdoorgen2_privacy_turn_on_writes_true(self):
        sw, mock = _spy_switch(SWITCH_TYPES["cameraoutdoorgen2"], "privacymode")
        asyncio.run(sw.async_turn_on())
        mock.assert_awaited_once_with(True)

    def test_lightswitch_turn_on_writes_true(self):
        sw, mock = _spy_switch(SWITCH_TYPES["lightswitch"], "switchstate")
        asyncio.run(sw.async_turn_on())
        mock.assert_awaited_once_with(True)

    def test_lightswitch_turn_off_writes_false(self):
        sw, mock = _spy_switch(SWITCH_TYPES["lightswitch"], "switchstate")
        asyncio.run(sw.async_turn_off())
        mock.assert_awaited_once_with(False)

    def test_smartplugcompact_turn_on_writes_true(self):
        sw, mock = _spy_switch(SWITCH_TYPES["smartplugcompact"], "switchstate")
        asyncio.run(sw.async_turn_on())
        mock.assert_awaited_once_with(True)

    def test_smartplug_routing_turn_on_writes_true(self):
        sw, mock = _spy_switch(SWITCH_TYPES["smartplug_routing"], "routing")
        asyncio.run(sw.async_turn_on())
        mock.assert_awaited_once_with(True)

    def test_smartplug_routing_turn_off_writes_false(self):
        sw, mock = _spy_switch(SWITCH_TYPES["smartplug_routing"], "routing")
        asyncio.run(sw.async_turn_off())
        mock.assert_awaited_once_with(False)


class TestSHCSwitchInit:
    """SHCSwitch.__init__ correctly sets unique_id and attr_name."""

    def test_init_no_attr_name_unique_id(self):
        dev = _FakeDevice()
        sw = SHCSwitch(
            device=dev,
            entry_id="entry1",
            description=SWITCH_TYPES["smartplug"],
        )
        assert sw._attr_unique_id == "root1_dev1"

    def test_init_no_attr_name_attr_name_is_none(self):
        """Primary entity: _attr_name must be None (HA uses device name)."""
        dev = _FakeDevice()
        sw = SHCSwitch(
            device=dev,
            entry_id="entry1",
            description=SWITCH_TYPES["smartplug"],
        )
        assert sw._attr_name is None

    def test_init_with_attr_name_unique_id_has_suffix(self):
        dev = _FakeDevice()
        sw = SHCSwitch(
            device=dev,
            entry_id="entry1",
            description=SWITCH_TYPES["cameraeyes_cameralight"],
            attr_name="Light",
        )
        assert sw._attr_unique_id == "root1_dev1_light"

    def test_init_with_attr_name_stores_attr_name(self):
        dev = _FakeDevice()
        sw = SHCSwitch(
            device=dev,
            entry_id="entry1",
            description=SWITCH_TYPES["cameraeyes_notification"],
            attr_name="Notification",
        )
        assert sw._attr_name == "Notification"

    def test_init_child_lock_attr_name_lowercased_in_unique_id(self):
        dev = _FakeDevice()
        sw = SHCSwitch(
            device=dev,
            entry_id="entry1",
            description=SWITCH_TYPES["child_lock"],
            attr_name="ChildLock",
        )
        assert sw._attr_unique_id == "root1_dev1_childlock"

    def test_init_pet_immunity_attr_name_lowercased(self):
        dev = _FakeDevice()
        sw = SHCSwitch(
            device=dev,
            entry_id="entry1",
            description=SWITCH_TYPES["pet_immunity_enabled"],
            attr_name="PetImmunity",
        )
        assert sw._attr_unique_id == "root1_dev1_petimmunity"

    def test_init_silent_mode_attr_name(self):
        dev = _FakeDevice()
        sw = SHCSwitch(
            device=dev,
            entry_id="entry1",
            description=SWITCH_TYPES["silent_mode"],
            attr_name="SilentMode",
        )
        assert sw._attr_unique_id == "root1_dev1_silentmode"
        assert sw._attr_name == "SilentMode"

    def test_init_entity_description_set(self):
        dev = _FakeDevice()
        sw = SHCSwitch(
            device=dev,
            entry_id="entry1",
            description=SWITCH_TYPES["bypass"],
        )
        assert sw.entity_description is SWITCH_TYPES["bypass"]


class TestSHCSwitchUpdate:
    """SHCSwitch.async_update() must call self._device.async_update() (#335)."""

    def test_update_calls_device_update(self):
        import asyncio
        from unittest.mock import AsyncMock
        sw = SHCSwitch.__new__(SHCSwitch)
        sw._device = SimpleNamespace(async_update=AsyncMock())
        sw._has_async_update = True
        sw.entity_description = SWITCH_TYPES["smartplug"]
        asyncio.run(sw.async_update())
        sw._device.async_update.assert_awaited_once()

    def test_update_camera_polling_type(self):
        """async_update() works for polling switches (cameras) too."""
        import asyncio
        from unittest.mock import AsyncMock
        sw = SHCSwitch.__new__(SHCSwitch)
        sw._device = SimpleNamespace(async_update=AsyncMock())
        sw._has_async_update = True
        sw.entity_description = SWITCH_TYPES["cameraeyes"]
        asyncio.run(sw.async_update())
        sw._device.async_update.assert_awaited_once()


class TestShouldPollRemaining:
    """should_poll for SWITCH_TYPES not covered by test_switch_unit.py."""

    def test_child_lock_should_poll_false(self):
        sw = _make_switch(SWITCH_TYPES["child_lock"], child_lock=False)
        assert sw.should_poll is False

    def test_child_lock_thermostat_should_poll_false(self):
        sw = _make_switch(SWITCH_TYPES["child_lock_thermostat"], child_lock=False)
        assert sw.should_poll is False

    def test_micromodule_relay_should_poll_false(self):
        State = PowerSwitchService.State
        sw = _make_switch(SWITCH_TYPES["micromodule_relay_switch"], switchstate=State.OFF)
        assert sw.should_poll is False

    def test_lightswitch_should_poll_false(self):
        State = PowerSwitchService.State
        sw = _make_switch(SWITCH_TYPES["lightswitch"], switchstate=State.OFF)
        assert sw.should_poll is False

    def test_smartplugcompact_should_poll_false(self):
        State = PowerSwitchService.State
        sw = _make_switch(SWITCH_TYPES["smartplugcompact"], switchstate=State.OFF)
        assert sw.should_poll is False

    def test_pet_immunity_should_poll_false(self):
        sw = _make_switch(SWITCH_TYPES["pet_immunity_enabled"], pet_immunity_enabled=False)
        assert sw.should_poll is False

    def test_smartplug_routing_should_poll_false(self):
        State = RoutingService.State
        sw = _make_switch(SWITCH_TYPES["smartplug_routing"], routing=State.DISABLED)
        assert sw.should_poll is False

    def test_cameraeyes_notification_should_poll_true(self):
        State = CameraNotificationService.State
        sw = _make_switch(
            SWITCH_TYPES["cameraeyes_notification"],
            cameranotification=State.DISABLED,
        )
        assert sw.should_poll is True

    def test_camera360_notification_should_poll_true(self):
        State = CameraNotificationService.State
        sw = _make_switch(
            SWITCH_TYPES["camera360_notification"],
            cameranotification=State.DISABLED,
        )
        assert sw.should_poll is True

    def test_cameraoutdoorgen2_frontlight_should_poll_true(self):
        State = CameraFrontLightService.State
        sw = _make_switch(
            SWITCH_TYPES["cameraoutdoorgen2_camerafrontlight"],
            camerafrontlight=State.OFF,
        )
        assert sw.should_poll is True

    def test_cameraoutdoorgen2_ambientlight_should_poll_true(self):
        State = CameraAmbientLightService.State
        sw = _make_switch(
            SWITCH_TYPES["cameraoutdoorgen2_cameraambientlight"],
            cameraambientlight=State.OFF,
        )
        assert sw.should_poll is True


def test_new_switch_types_should_poll_false():
    new_keys = [
        "energy_saving_mode_enabled",
        "warning_suppressed",
        "nightly_promise_enabled",
        "humidity_warning_enabled",
        "swap_inputs",
        "swap_outputs",
        "pre_alarm_enabled",
        "smart_sensitivity_enabled",
    ]
    for key in new_keys:
        assert SWITCH_TYPES[key].should_poll is False, (
            f"SWITCH_TYPES[{key!r}].should_poll should be False"
        )


class TestAllDeviceTypesExcluded:
    """Excluding every device type covers all continue branches in the loops."""

    def _setup_all_excluded(self):
        """Run async_setup_entry with all devices set to excluded-id."""
        session = _make_exclusion_session()
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
        session = _make_exclusion_session()
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


def test_setup_empty_session_no_entities(mock_config_entry, mock_session):
    entities = _setup_switch(mock_config_entry, mock_session)
    # No regular device entities; no UDS either
    assert entities == []


def test_setup_subscribes_to_session(mock_config_entry, mock_session):
    uds = _fake_uds(name="Home", dev_id="uds1", root_id="mac1")
    mock_session.userdefinedstates = [uds]
    mock_session.subscribe = MagicMock()
    _setup_switch(mock_config_entry, mock_session)
    mock_session.subscribe.assert_called_once()
    args = mock_session.subscribe.call_args[0][0]
    assert args[0] is SHCUserDefinedState


def test_setup_registers_async_on_unload(mock_config_entry, mock_session):
    _setup_switch(mock_config_entry, mock_session)
    mock_config_entry.async_on_unload.assert_called_once()


def test_setup_unload_removes_subscriber(mock_config_entry, mock_session):
    """The unsubscribe closure removes the tuple from session._subscribers."""
    uds = _fake_uds(name="Night", dev_id="uds1", root_id="mac1")
    mock_session.userdefinedstates = [uds]
    mock_session.subscribe = MagicMock()
    mock_session._subscribers = []

    unload_fn = None

    def capture_unload(fn):
        nonlocal unload_fn
        unload_fn = fn

    mock_config_entry.async_on_unload = capture_unload

    _setup_switch(mock_config_entry, mock_session)

    # The subscriber tuple was added by subscribe()
    assert unload_fn is not None
    mock_session.subscribe.assert_called_once()
    subscriber = mock_session.subscribe.call_args[0][0]
    # Simulate it being in _subscribers
    mock_session._subscribers.append(subscriber)
    unload_fn()
    assert subscriber not in mock_session._subscribers


def test_setup_unload_no_error_when_subscriber_already_gone(mock_config_entry, mock_session):
    """Unload closure must not raise if subscriber was already removed."""
    unload_fn = None

    def capture_unload(fn):
        nonlocal unload_fn
        unload_fn = fn

    mock_config_entry.async_on_unload = capture_unload

    _setup_switch(mock_config_entry, mock_session)

    assert unload_fn is not None
    # _subscribers is empty → ValueError swallowed
    unload_fn()  # must not raise


def test_shcswitch_update_calls_device_update():
    """SHCSwitch.async_update() must call device.async_update() (#335)."""
    import asyncio
    from unittest.mock import AsyncMock
    sw = SHCSwitch.__new__(SHCSwitch)
    sw._device = SimpleNamespace(async_update=AsyncMock())
    sw._has_async_update = True
    sw.entity_description = SWITCH_TYPES["cameraeyes"]
    sw.entity_id = "switch.test"
    asyncio.run(sw.async_update())
    sw._device.async_update.assert_awaited_once()


class TestSwitchSuppressCamerasRegistry:
    """Lines 536-546: suppress_cameras removes devices from registry."""

    def _run_switch_setup_cameras(self, cameras_eyes, options):
        from custom_components.bosch_shc.switch import async_setup_entry

        dh = MagicMock()
        dh.smart_plugs = []
        dh.smart_plugs_compact = []
        dh.micromodule_relays = []
        dh.micromodule_impulse_relays = []
        dh.micromodule_dimmers = []
        dh.light_switches_bsm = []
        dh.micromodule_light_attached = []
        dh.micromodule_light_controls = []
        dh.camera_eyes = cameras_eyes
        dh.camera_360 = []
        dh.camera_outdoor_gen2 = []
        dh.presence_simulation_system = None
        dh.shutter_contacts2 = []
        dh.thermostats = []
        dh.roomthermostats = []
        dh.wallthermostats = []
        dh.motion_detectors2 = []
        dh.universal_switches = []
        dh.twinguards = []
        dh.smoke_detectors = []
        dh.smoke_detection_system = None

        session = MagicMock()
        session.device_helper = dh
        session.userdefinedstates = []
        session._subscribers = []
        session.subscribe = MagicMock()

        dev_entry = SimpleNamespace(id="reg_cam1")
        dr_mock = MagicMock()
        dr_mock.async_get_device = MagicMock(return_value=dev_entry)
        dr_mock.async_update_device = MagicMock()

        hass = _fake_hass(session=session)
        entry = _fake_entry(hass=hass, options=options)
        entry.async_on_unload = MagicMock()

        with patch("custom_components.bosch_shc.switch.get_dev_reg",
                   return_value=dr_mock), \
             patch("custom_components.bosch_shc.switch.async_migrate_to_new_unique_id",
                   new_callable=AsyncMock):
            collected = []
            _run(async_setup_entry(hass, entry, lambda ents, **kw: collected.extend(ents)))

        return dr_mock

    def test_suppress_cameras_removes_from_registry(self):
        """Lines 536-548: suppress_cameras=True → async_update_device called."""
        cam = _fake_dev("cam1")
        dr_mock = self._run_switch_setup_cameras(
            [cam], options={OPT_SUPPRESS_CAMERA_SWITCHES: True}
        )
        dr_mock.async_update_device.assert_called()


class TestSHCSwitchAsyncUpdateFallback:
    """Line 1034: SHCSwitch.async_update executor fallback when no async_update."""

    def test_async_update_fallback_to_executor(self):
        """Line 1034: _has_async_update=False → async_add_executor_job called."""
        from custom_components.bosch_shc.switch import SHCSwitch

        sw = SHCSwitch.__new__(SHCSwitch)
        sw._has_async_update = False

        update_called = []

        def sync_update():
            update_called.append(True)

        sw._device = SimpleNamespace(update=sync_update)

        executor_calls = []

        async def fake_executor_job(fn, *args):
            executor_calls.append(fn)
            fn(*args)

        sw.hass = SimpleNamespace(async_add_executor_job=fake_executor_job)
        _run(sw.async_update())
        assert update_called


# ---------------------------------------------------------------------------
# SHCAutomationRuleSwitch
# ---------------------------------------------------------------------------


def _make_rule_switch(rule=None, shc_device=None):
    sw = SHCAutomationRuleSwitch.__new__(SHCAutomationRuleSwitch)
    sw._rule = rule if rule is not None else SimpleNamespace(
        id="r1", name="TV aus", enabled=True
    )
    sw._shc_device = shc_device
    return sw


def test_automation_rule_switch_is_on_true():
    sw = _make_rule_switch(SimpleNamespace(id="r1", name="TV aus", enabled=True))
    assert sw.is_on is True


def test_automation_rule_switch_is_on_false():
    sw = _make_rule_switch(SimpleNamespace(id="r1", name="TV aus", enabled=False))
    assert sw.is_on is False


def test_automation_rule_switch_device_info_none_without_shc_device():
    sw = _make_rule_switch(shc_device=None)
    assert sw.device_info is None


def test_automation_rule_switch_device_info_links_shc_device():
    shc_device = SimpleNamespace(
        identifiers={("bosch_shc", "shc1")},
        name="Bosch SHC",
        manufacturer="Bosch",
        model="SmartHomeController",
    )
    sw = _make_rule_switch(shc_device=shc_device)
    info = sw.device_info
    assert info["identifiers"] == shc_device.identifiers
    assert info["name"] == "Bosch SHC"


def test_automation_rule_switch_async_update_calls_refresh():
    rule = SimpleNamespace(id="r1", name="TV aus", enabled=True)
    rule.async_refresh = AsyncMock()
    sw = _make_rule_switch(rule)
    _run(sw.async_update())
    rule.async_refresh.assert_awaited_once()


def test_automation_rule_switch_async_update_logs_on_error():
    rule = SimpleNamespace(id="r1", name="TV aus", enabled=True)
    rule.async_refresh = AsyncMock(side_effect=SHCException("boom"))
    sw = _make_rule_switch(rule)
    _run(sw.async_update())  # must not raise


def test_automation_rule_switch_turn_on_calls_set_enabled_true():
    rule = SimpleNamespace(id="r1", name="TV aus", enabled=False)
    rule.async_set_enabled = AsyncMock()
    sw = _make_rule_switch(rule)
    _run(sw.async_turn_on())
    rule.async_set_enabled.assert_awaited_once_with(True)


def test_automation_rule_switch_turn_off_calls_set_enabled_false():
    rule = SimpleNamespace(id="r1", name="TV aus", enabled=True)
    rule.async_set_enabled = AsyncMock()
    sw = _make_rule_switch(rule)
    _run(sw.async_turn_off())
    rule.async_set_enabled.assert_awaited_once_with(False)


def test_automation_rule_switch_turn_on_wraps_shc_exception():
    rule = SimpleNamespace(id="r1", name="TV aus", enabled=False)
    rule.async_set_enabled = AsyncMock(side_effect=SHCException("boom"))
    sw = _make_rule_switch(rule)
    with pytest.raises(HomeAssistantError):
        _run(sw.async_turn_on())


def test_automation_rule_switch_turn_off_wraps_shc_exception():
    rule = SimpleNamespace(id="r1", name="TV aus", enabled=True)
    rule.async_set_enabled = AsyncMock(side_effect=SHCException("boom"))
    sw = _make_rule_switch(rule)
    with pytest.raises(HomeAssistantError):
        _run(sw.async_turn_off())


class TestAutomationRulesSwitchSetupEntry:
    @pytest.mark.parametrize(
        "mock_config_entry",
        [{"options": {OPT_AUTOMATION_RULES_AS_ENTITIES: True}}],
        indirect=True,
    )
    def test_creates_switch_per_rule_when_enabled(
        self, mock_config_entry, mock_session
    ):
        mock_config_entry.runtime_data.shc_device = SimpleNamespace(
            identifiers={("bosch_shc", "shc1")},
            name="Bosch SHC",
            manufacturer="Bosch",
            model="SmartHomeController",
        )
        mock_session.automation_rules = [
            SimpleNamespace(id="r1", name="Rule 1", enabled=True),
            SimpleNamespace(id="r2", name="Rule 2", enabled=False),
        ]
        mock_session.userdefinedstates = []
        mock_session.subscribe = MagicMock()
        mock_config_entry.async_on_unload = MagicMock()
        entities = asyncio.run(
            run_setup_entry(async_setup_entry, mock_config_entry, mock_session)
        )
        rule_switches = [e for e in entities if isinstance(e, SHCAutomationRuleSwitch)]
        assert len(rule_switches) == 2

    def test_no_switches_when_option_disabled(self, mock_config_entry, mock_session):
        mock_config_entry.runtime_data.shc_device = SimpleNamespace()
        mock_session.automation_rules = [
            SimpleNamespace(id="r1", name="Rule 1", enabled=True)
        ]
        mock_session.userdefinedstates = []
        mock_session.subscribe = MagicMock()
        mock_config_entry.async_on_unload = MagicMock()
        entities = asyncio.run(
            run_setup_entry(async_setup_entry, mock_config_entry, mock_session)
        )
        assert not any(isinstance(e, SHCAutomationRuleSwitch) for e in entities)


# ---------------------------------------------------------------------------
# TemperatureDropEnabledSwitch
# ---------------------------------------------------------------------------


def _make_tds_switch(room=None):
    from custom_components.bosch_shc.switch import TemperatureDropEnabledSwitch

    sw = TemperatureDropEnabledSwitch.__new__(TemperatureDropEnabledSwitch)
    sw._device = SimpleNamespace(name="Kinderzimmer")
    sw._room = room if room is not None else MagicMock()
    sw._enabled = None
    return sw


def test_tds_switch_is_on_false_initially():
    sw = _make_tds_switch()
    assert sw.is_on is False


def test_tds_switch_async_update_sets_enabled():
    room = MagicMock()
    room.async_temperature_drop_service = AsyncMock(
        return_value={"configuration": {"enabled": True}}
    )
    sw = _make_tds_switch(room)
    _run(sw.async_update())
    assert sw.is_on is True


def test_tds_switch_async_update_logs_on_error():
    room = MagicMock()
    room.async_temperature_drop_service = AsyncMock(side_effect=SHCException("boom"))
    sw = _make_tds_switch(room)
    _run(sw.async_update())  # must not raise


def test_tds_switch_turn_on_calls_room():
    room = MagicMock()
    room.async_set_temperature_drop_enabled = AsyncMock()
    sw = _make_tds_switch(room)
    _run(sw.async_turn_on())
    room.async_set_temperature_drop_enabled.assert_awaited_once_with(True)
    assert sw.is_on is True


def test_tds_switch_turn_off_calls_room():
    room = MagicMock()
    room.async_set_temperature_drop_enabled = AsyncMock()
    sw = _make_tds_switch(room)
    _run(sw.async_turn_off())
    room.async_set_temperature_drop_enabled.assert_awaited_once_with(False)
    assert sw.is_on is False


def test_tds_switch_turn_on_wraps_shc_exception():
    room = MagicMock()
    room.async_set_temperature_drop_enabled = AsyncMock(
        side_effect=SHCException("boom")
    )
    sw = _make_tds_switch(room)
    with pytest.raises(HomeAssistantError):
        _run(sw.async_turn_on())


class TestTemperatureDropSwitchSetupEntry:
    """Opt-in (OPT_TEMPERATURE_DROP_ENTITIES, hass#373 follow-up): a Bosch
    SHC engineer flagged this should_poll entity's default-on 15s poll as
    an unnecessary load, and most setups don't use the feature anyway."""

    @pytest.mark.parametrize(
        "mock_config_entry",
        [{"options": {OPT_TEMPERATURE_DROP_ENTITIES: True}}],
        indirect=True,
    )
    def test_created_when_service_present(self, mock_config_entry, mock_session):
        from custom_components.bosch_shc.switch import TemperatureDropEnabledSwitch

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
            return_value={"configuration": {"enabled": True}}
        )
        mock_session.room = MagicMock(return_value=room)
        mock_session.userdefinedstates = []
        mock_session.subscribe = MagicMock()
        mock_config_entry.async_on_unload = MagicMock()
        entities = asyncio.run(
            run_setup_entry(async_setup_entry, mock_config_entry, mock_session)
        )
        assert any(isinstance(e, TemperatureDropEnabledSwitch) for e in entities)
        drop_switch = next(
            e for e in entities if isinstance(e, TemperatureDropEnabledSwitch)
        )
        # hass#372: must report the room's own name, not the shared
        # ROOM_CLIMATE_CONTROL device's generic raw name.
        assert drop_switch.device_name == "Kinderzimmer"

    @pytest.mark.parametrize(
        "mock_config_entry",
        [{"options": {OPT_TEMPERATURE_DROP_ENTITIES: True}}],
        indirect=True,
    )
    def test_skipped_when_service_absent(self, mock_config_entry, mock_session):
        from custom_components.bosch_shc.switch import TemperatureDropEnabledSwitch

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
        mock_session.userdefinedstates = []
        mock_session.subscribe = MagicMock()
        mock_config_entry.async_on_unload = MagicMock()
        entities = asyncio.run(
            run_setup_entry(async_setup_entry, mock_config_entry, mock_session)
        )
        assert not any(isinstance(e, TemperatureDropEnabledSwitch) for e in entities)

    def test_skipped_by_default(self, mock_config_entry, mock_session):
        """Default (no option set) must not create the entity or even probe
        the service -- OPT_TEMPERATURE_DROP_ENTITIES defaults to off."""
        from custom_components.bosch_shc.switch import TemperatureDropEnabledSwitch

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
            return_value={"configuration": {"enabled": True}}
        )
        mock_session.room = MagicMock(return_value=room)
        mock_session.userdefinedstates = []
        mock_session.subscribe = MagicMock()
        mock_config_entry.async_on_unload = MagicMock()
        entities = asyncio.run(
            run_setup_entry(async_setup_entry, mock_config_entry, mock_session)
        )
        assert not any(isinstance(e, TemperatureDropEnabledSwitch) for e in entities)
        room.async_temperature_drop_service.assert_not_awaited()
