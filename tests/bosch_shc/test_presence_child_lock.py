"""Tests for presence-based child lock automation in __init__.py.

Covers:
- Feature disabled when OPT_PRESENCE_ENTITY is empty (zero overhead)
- Transition INTO present_state -> child_lock=True on all devices
- Transition OUT OF present_state -> child_lock=False on all devices
- No-op when new_state == old_state (same state, no transition)
- No-op when new_state is unavailable/unknown
- Unsub callback is cleaned up in async_unload_entry
- Errors from device setters are caught and logged (no crash)
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from custom_components.bosch_shc.const import (
    OPT_PRESENCE_ENTITY,
    OPT_PRESENCE_STATE,
    DOMAIN,
    DATA_SESSION,
)


# ---------------------------------------------------------------------------
# Re-use helpers from test_init_setup (local copies to keep test independent)
# ---------------------------------------------------------------------------

PATCH_SESSION = "custom_components.bosch_shc.__init__.SHCSession"
PATCH_ZEROCONF = "custom_components.bosch_shc.__init__.async_get_instance"
PATCH_DR_GET = "custom_components.bosch_shc.__init__.dr.async_get"
PATCH_PARSE_CERT = "custom_components.bosch_shc.__init__.parse_certificate"
PATCH_TRACK_INTERVAL = "custom_components.bosch_shc.__init__.async_track_time_interval"
PATCH_TRACK_STATE = "custom_components.bosch_shc.__init__.async_track_state_change_event"


def _run(coro):
    return asyncio.run(coro)


def _make_fake_hass():
    hass = MagicMock()
    hass.data = {}

    async def _executor_job(fn, *args):
        return fn(*args)

    hass.async_add_executor_job = _executor_job
    hass.config_entries = MagicMock()
    hass.config_entries.async_forward_entry_setups = AsyncMock(return_value=True)
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
    hass.config_entries.async_reload = AsyncMock()
    hass.bus = MagicMock()
    hass.bus.async_listen_once = MagicMock(return_value=MagicMock(return_value=None))
    hass.bus.fire = MagicMock()
    hass.services = MagicMock()
    hass.services.async_register = MagicMock()
    hass.components = MagicMock()
    hass.components.persistent_notification = MagicMock()
    hass.components.persistent_notification.create = MagicMock()
    hass.async_create_task = MagicMock()
    return hass


def _make_fake_entry(entry_id="test_entry_id", title="Test SHC",
                     cert_path="", key_path="", host="192.168.1.1", options=None):
    entry = MagicMock()
    entry.entry_id = entry_id
    entry.title = title
    entry.data = {
        "ssl_certificate": cert_path,
        "ssl_key": key_path,
        "host": host,
    }
    entry.options = options or {}
    entry.add_update_listener = MagicMock(return_value=MagicMock())
    entry.async_on_unload = MagicMock()
    return entry


def _make_device(device_id="dev-001"):
    dev = MagicMock()
    dev.id = device_id
    dev.child_lock = False
    return dev


def _make_fake_session(thermostats=None, roomthermostats=None,
                       wallthermostats=None, bool_devices=None):
    from boschshcpy import SHCSession as _SHCSession
    session = MagicMock(spec=_SHCSession)
    shc_info = SimpleNamespace(
        updateState=SimpleNamespace(name="NO_UPDATE_AVAILABLE"),
        unique_id="aa:bb:cc:dd:ee:ff",
        version="9.0.0",
        name="My SHC",
    )
    session.information = shc_info
    session.scenarios = []
    session.rawscan_commands = ["devices"]
    session.start_polling = MagicMock()
    session.stop_polling = MagicMock()
    session.subscribe_scenario_callback = MagicMock()
    session.unsubscribe_scenario_callback = MagicMock()

    dh = MagicMock()
    dh.universal_switches = []
    dh.thermostats = thermostats or []
    dh.roomthermostats = roomthermostats or []
    dh.wallthermostats = wallthermostats or []
    dh.micromodule_shutter_controls = bool_devices or []
    dh.micromodule_blinds = []
    dh.micromodule_light_attached = []
    dh.micromodule_relays = []
    dh.micromodule_impulse_relays = []
    dh.micromodule_dimmers = []
    dh.light_switches_bsm = []
    session.device_helper = dh
    return session


def _make_fake_device_registry():
    fake_device_entry = SimpleNamespace(id="fake_device_reg_id_001")
    dr_mock = MagicMock()
    dr_mock.async_get_or_create = MagicMock(return_value=fake_device_entry)
    return dr_mock


def _state_event(new_state_str, old_state_str=None):
    """Build a fake state-change event data dict."""
    new_state = SimpleNamespace(state=new_state_str)
    old_state = SimpleNamespace(state=old_state_str) if old_state_str is not None else None
    event = SimpleNamespace(data={"new_state": new_state, "old_state": old_state})
    return event


# ---------------------------------------------------------------------------
# Setup helper
# ---------------------------------------------------------------------------

def _do_setup(session, options, *, capture_state_cb=False):
    """Run async_setup_entry with given options, return (hass, entry, captured_state_cb)."""
    from custom_components.bosch_shc.__init__ import async_setup_entry

    hass = _make_fake_hass()
    entry = _make_fake_entry(options=options)
    dr_mock = _make_fake_device_registry()
    track_unsub = MagicMock()
    captured = {}

    def _capture_track_state(h, entity_ids, cb):
        captured["state_cb"] = cb
        return MagicMock()

    patches = [
        patch(PATCH_SESSION, return_value=session),
        patch(PATCH_ZEROCONF, new=AsyncMock(return_value=MagicMock())),
        patch(PATCH_DR_GET, return_value=dr_mock),
        patch(PATCH_PARSE_CERT, return_value=None),
        patch(PATCH_TRACK_INTERVAL, return_value=track_unsub),
    ]
    if capture_state_cb:
        patches.append(patch(PATCH_TRACK_STATE, side_effect=_capture_track_state))

    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        if capture_state_cb:
            with patches[5]:
                _run(async_setup_entry(hass, entry))
        else:
            _run(async_setup_entry(hass, entry))

    return hass, entry, captured.get("state_cb")


# ---------------------------------------------------------------------------
# Tests: feature disabled (empty presence entity)
# ---------------------------------------------------------------------------

class TestPresenceDisabled:
    def test_no_state_tracker_registered_when_entity_empty(self):
        """When OPT_PRESENCE_ENTITY is unset, async_track_state_change_event is NOT called."""
        session = _make_fake_session()
        with patch(PATCH_TRACK_STATE) as mock_track:
            _do_setup(session, options={})
            mock_track.assert_not_called()

    def test_no_state_tracker_when_entity_is_empty_string(self):
        session = _make_fake_session()
        with patch(PATCH_TRACK_STATE) as mock_track:
            _do_setup(session, options={OPT_PRESENCE_ENTITY: ""})
            mock_track.assert_not_called()

    def test_presence_unsub_is_none_when_disabled(self):
        session = _make_fake_session()
        hass, entry, _ = _do_setup(session, options={})
        assert entry.runtime_data.presence_unsub is None


# ---------------------------------------------------------------------------
# Tests: feature enabled — state tracker registered
# ---------------------------------------------------------------------------

class TestPresenceEnabled:
    def test_state_tracker_registered_when_entity_set(self):
        """async_track_state_change_event called once with the configured entity."""
        session = _make_fake_session()
        opts = {OPT_PRESENCE_ENTITY: "person.felix"}

        with patch(PATCH_TRACK_STATE) as mock_track:
            mock_track.return_value = MagicMock()

            def _patched_setup():
                from custom_components.bosch_shc.__init__ import async_setup_entry
                hass = _make_fake_hass()
                entry = _make_fake_entry(options=opts)
                with (
                    patch(PATCH_SESSION, return_value=session),
                    patch(PATCH_ZEROCONF, new=AsyncMock(return_value=MagicMock())),
                    patch(PATCH_DR_GET, return_value=_make_fake_device_registry()),
                    patch(PATCH_PARSE_CERT, return_value=None),
                    patch(PATCH_TRACK_INTERVAL, return_value=MagicMock()),
                ):
                    _run(async_setup_entry(hass, entry))
                return entry

            entry = _patched_setup()

        mock_track.assert_called_once()
        call_args = mock_track.call_args
        entity_ids = call_args[0][1]
        assert "person.felix" in entity_ids

    def test_presence_unsub_stored_on_runtime_data(self):
        session = _make_fake_session()
        opts = {OPT_PRESENCE_ENTITY: "person.felix"}
        hass, entry, _ = _do_setup(session, options=opts, capture_state_cb=True)
        assert entry.runtime_data.presence_unsub is not None


# ---------------------------------------------------------------------------
# Tests: child lock ON when entering present_state
# ---------------------------------------------------------------------------

class TestChildLockOn:
    def _setup_with_devices(self, thermostat=None, bool_dev=None):
        therm = thermostat or _make_device("therm-1")
        booldev = bool_dev or _make_device("bool-1")
        session = _make_fake_session(
            thermostats=[therm],
            bool_devices=[booldev],
        )
        opts = {OPT_PRESENCE_ENTITY: "person.felix", OPT_PRESENCE_STATE: "home"}
        hass, entry, state_cb = _do_setup(session, options=opts, capture_state_cb=True)
        return hass, state_cb, therm, booldev

    def test_entering_present_state_sets_child_lock_true_on_thermostat(self):
        hass, state_cb, therm, booldev = self._setup_with_devices()
        assert state_cb is not None

        state_cb(_state_event("home", "not_home"))

        # hass.async_create_task was called (executor job scheduled)
        hass.async_create_task.assert_called_once()
        # Run the created task (it's a coroutine)
        task_coro = hass.async_create_task.call_args[0][0]
        _run(task_coro)
        assert therm.child_lock is True

    def test_entering_present_state_sets_child_lock_true_on_bool_device(self):
        hass, state_cb, therm, booldev = self._setup_with_devices()
        state_cb(_state_event("home", "not_home"))
        task_coro = hass.async_create_task.call_args[0][0]
        _run(task_coro)
        assert booldev.child_lock is True

    def test_custom_present_state_triggers_lock(self):
        therm = _make_device("therm-1")
        session = _make_fake_session(thermostats=[therm])
        opts = {OPT_PRESENCE_ENTITY: "input_boolean.guests", OPT_PRESENCE_STATE: "on"}
        hass, entry, state_cb = _do_setup(session, options=opts, capture_state_cb=True)

        state_cb(_state_event("on", "off"))
        task_coro = hass.async_create_task.call_args[0][0]
        _run(task_coro)
        assert therm.child_lock is True


# ---------------------------------------------------------------------------
# Tests: child lock OFF when leaving present_state
# ---------------------------------------------------------------------------

class TestChildLockOff:
    def _setup_with_therm(self):
        therm = _make_device("therm-1")
        therm.child_lock = True  # initially locked
        session = _make_fake_session(thermostats=[therm])
        opts = {OPT_PRESENCE_ENTITY: "person.felix", OPT_PRESENCE_STATE: "home"}
        hass, entry, state_cb = _do_setup(session, options=opts, capture_state_cb=True)
        return hass, state_cb, therm

    def test_leaving_present_state_sets_child_lock_false(self):
        hass, state_cb, therm = self._setup_with_therm()
        state_cb(_state_event("not_home", "home"))
        task_coro = hass.async_create_task.call_args[0][0]
        _run(task_coro)
        assert therm.child_lock is False


# ---------------------------------------------------------------------------
# Tests: no-op cases
# ---------------------------------------------------------------------------

class TestNoOp:
    def _setup(self):
        therm = _make_device("therm-1")
        session = _make_fake_session(thermostats=[therm])
        opts = {OPT_PRESENCE_ENTITY: "person.felix", OPT_PRESENCE_STATE: "home"}
        hass, entry, state_cb = _do_setup(session, options=opts, capture_state_cb=True)
        return hass, state_cb, therm

    def test_no_task_when_old_state_equals_new_state(self):
        hass, state_cb, therm = self._setup()
        state_cb(_state_event("home", "home"))
        hass.async_create_task.assert_not_called()

    def test_no_task_when_new_state_is_unavailable(self):
        hass, state_cb, therm = self._setup()
        state_cb(_state_event("unavailable", "home"))
        hass.async_create_task.assert_not_called()

    def test_no_task_when_new_state_is_unknown(self):
        hass, state_cb, therm = self._setup()
        state_cb(_state_event("unknown", "home"))
        hass.async_create_task.assert_not_called()

    def test_no_task_when_neither_old_nor_new_is_present_state(self):
        """Transition between two non-present states: no action needed."""
        hass, state_cb, therm = self._setup()
        state_cb(_state_event("not_home", "away"))
        hass.async_create_task.assert_not_called()

    def test_no_task_when_new_state_is_none(self):
        """new_state=None (entity removed): ignore."""
        hass, state_cb, therm = self._setup()
        event = SimpleNamespace(data={"new_state": None, "old_state": SimpleNamespace(state="home")})
        state_cb(event)
        hass.async_create_task.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: error handling — device setter raises, no crash
# ---------------------------------------------------------------------------

class TestErrorHandling:
    def test_jsonrpc_error_caught_no_crash(self):
        from boschshcpy.api import JSONRPCError

        therm = MagicMock()
        therm.id = "therm-broken"
        type(therm).child_lock = property(
            lambda self: False,
            lambda self, v: (_ for _ in ()).throw(JSONRPCError(-1, "network error")),
        )

        session = _make_fake_session(thermostats=[therm])
        opts = {OPT_PRESENCE_ENTITY: "person.felix", OPT_PRESENCE_STATE: "home"}
        hass, entry, state_cb = _do_setup(session, options=opts, capture_state_cb=True)

        state_cb(_state_event("home", "not_home"))
        task_coro = hass.async_create_task.call_args[0][0]
        # Must NOT raise
        _run(task_coro)

    def test_shc_exception_caught_no_crash(self):
        from boschshcpy.exceptions import SHCException

        booldev = MagicMock()
        booldev.id = "bool-broken"
        type(booldev).child_lock = property(
            lambda self: False,
            lambda self, v: (_ for _ in ()).throw(SHCException("timeout")),
        )

        session = _make_fake_session(bool_devices=[booldev])
        opts = {OPT_PRESENCE_ENTITY: "person.felix", OPT_PRESENCE_STATE: "home"}
        hass, entry, state_cb = _do_setup(session, options=opts, capture_state_cb=True)

        state_cb(_state_event("home", "not_home"))
        task_coro = hass.async_create_task.call_args[0][0]
        _run(task_coro)


# ---------------------------------------------------------------------------
# Tests: unload cleans up presence_unsub
# ---------------------------------------------------------------------------

class TestUnloadCleansUp:
    def test_presence_unsub_called_on_unload(self):
        from custom_components.bosch_shc.__init__ import async_setup_entry, async_unload_entry

        session = _make_fake_session()
        hass = _make_fake_hass()
        entry = _make_fake_entry(options={OPT_PRESENCE_ENTITY: "person.felix"})
        dr_mock = _make_fake_device_registry()
        presence_unsub_mock = MagicMock()

        def _capture_track_state(h, entity_ids, cb):
            return presence_unsub_mock

        with (
            patch(PATCH_SESSION, return_value=session),
            patch(PATCH_ZEROCONF, new=AsyncMock(return_value=MagicMock())),
            patch(PATCH_DR_GET, return_value=dr_mock),
            patch(PATCH_PARSE_CERT, return_value=None),
            patch(PATCH_TRACK_INTERVAL, return_value=MagicMock()),
            patch(PATCH_TRACK_STATE, side_effect=_capture_track_state),
        ):
            _run(async_setup_entry(hass, entry))
            _run(async_unload_entry(hass, entry))

        presence_unsub_mock.assert_called_once()

    def test_no_unsub_called_when_presence_disabled(self):
        """presence_unsub is None when feature is off; unload must not crash."""
        from custom_components.bosch_shc.__init__ import async_setup_entry, async_unload_entry

        session = _make_fake_session()
        hass = _make_fake_hass()
        entry = _make_fake_entry(options={})
        dr_mock = _make_fake_device_registry()

        with (
            patch(PATCH_SESSION, return_value=session),
            patch(PATCH_ZEROCONF, new=AsyncMock(return_value=MagicMock())),
            patch(PATCH_DR_GET, return_value=dr_mock),
            patch(PATCH_PARSE_CERT, return_value=None),
            patch(PATCH_TRACK_INTERVAL, return_value=MagicMock()),
        ):
            _run(async_setup_entry(hass, entry))
            result = _run(async_unload_entry(hass, entry))

        assert result is True
