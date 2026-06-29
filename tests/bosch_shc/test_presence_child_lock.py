"""Tests for presence-based child lock automation in __init__.py.

Covers:
- Feature disabled when OPT_PRESENCE_ENTITY is empty list / empty string / unset
- Backward compat: stored value is a plain str (old single-select)
- Single entity: transition into/out of present_state -> lock on/off
- Multi-entity any-home semantics:
    - ANY entity home -> lock ON
    - ALL away -> lock OFF
    - One away, other still home -> lock stays ON (no change)
- Redundant-write suppression (same aggregate -> no second API call)
- No-op on unavailable/unknown/new_state=None
- Unsub callback is cleaned up in async_unload_entry
- Errors from device async setters are caught and logged (no crash)
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.bosch_shc.const import (
    OPT_CHILD_LOCK_ENABLED,
    OPT_PRESENCE_ENTITY,
)

# ---------------------------------------------------------------------------
# Patch targets
# ---------------------------------------------------------------------------

PATCH_SESSION = "custom_components.bosch_shc.__init__.SHCSessionAsync"
PATCH_DR_GET = "custom_components.bosch_shc.__init__.dr.async_get"
PATCH_PARSE_CERT = "custom_components.bosch_shc.__init__.parse_certificate"
PATCH_TRACK_INTERVAL = "custom_components.bosch_shc.__init__.async_track_time_interval"
PATCH_TRACK_STATE = "custom_components.bosch_shc.__init__.async_track_state_change_event"


def _run(coro):
    return asyncio.run(coro)


def _make_fake_hass(states=None):
    """Build a minimal hass mock.

    states: dict mapping entity_id -> state string. Used by hass.states.get().
    """
    hass = MagicMock()
    hass.data = {}

    _states = dict(states or {})

    def _states_get(entity_id):
        val = _states.get(entity_id)
        if val is None:
            return None
        return SimpleNamespace(state=val)

    hass.states = MagicMock()
    hass.states.get = MagicMock(side_effect=_states_get)

    async def _executor_job(fn, *args):
        return fn(*args)

    hass.async_add_executor_job = _executor_job
    hass.config_entries = MagicMock()
    hass.config_entries.async_forward_entry_setups = AsyncMock(return_value=True)
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
    hass.config_entries.async_reload = AsyncMock()
    hass.bus = MagicMock()
    hass.bus.async_listen_once = MagicMock(return_value=MagicMock(return_value=None))
    hass.bus.async_fire = MagicMock()
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
    """Make a fake SHC device with async_set_child_lock as an AsyncMock."""
    dev = MagicMock()
    dev.id = device_id
    dev.async_set_child_lock = AsyncMock()
    # child_lock attribute tracks the last value written via the AsyncMock
    # We simulate the setter storing the value by side_effect
    _state = [False]

    async def _set_child_lock(value):
        _state[0] = value

    dev.async_set_child_lock.side_effect = _set_child_lock
    dev._child_lock_state = _state
    return dev


def _make_fake_session(thermostats=None, roomthermostats=None,
                       wallthermostats=None, bool_devices=None):
    from boschshcpy import SHCSessionAsync as _SHCSessionAsync
    session = MagicMock(spec=_SHCSessionAsync)
    shc_info = SimpleNamespace(
        updateState=SimpleNamespace(name="NO_UPDATE_AVAILABLE"),
        unique_id="aa:bb:cc:dd:ee:ff",
        version="9.0.0",
        name="My SHC",
    )
    session.information = shc_info
    session.scenarios = []
    session.async_init = AsyncMock()
    session.start_polling = AsyncMock()
    session.stop_polling = AsyncMock()
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
    old_state = (
        SimpleNamespace(state=old_state_str) if old_state_str is not None else None
    )
    return SimpleNamespace(data={"new_state": new_state, "old_state": old_state})


# ---------------------------------------------------------------------------
# Setup helper
# ---------------------------------------------------------------------------

def _do_setup(session, options, *, capture_state_cb=False, hass_states=None):
    """Run async_setup_entry with given options.

    Returns (hass, entry, captured_state_cb).
    hass_states: dict of entity_id -> state string pre-populated in hass.states.
    """
    from custom_components.bosch_shc.__init__ import async_setup_entry

    hass = _make_fake_hass(states=hass_states)
    entry = _make_fake_entry(options=options)
    dr_mock = _make_fake_device_registry()
    track_unsub = MagicMock()
    captured = {}

    def _capture_track_state(h, entity_ids, cb):
        captured["state_cb"] = cb
        captured["entity_ids"] = entity_ids
        return MagicMock()

    patches = [
        patch(PATCH_SESSION, return_value=session),
        patch(PATCH_DR_GET, return_value=dr_mock),
        patch(PATCH_PARSE_CERT, return_value=None),
        patch(PATCH_TRACK_INTERVAL, return_value=track_unsub),
    ]
    if capture_state_cb:
        patches.append(patch(PATCH_TRACK_STATE, side_effect=_capture_track_state))

    with patches[0], patches[1], patches[2], patches[3]:
        if capture_state_cb:
            with patches[4]:
                _run(async_setup_entry(hass, entry))
        else:
            _run(async_setup_entry(hass, entry))

    return hass, entry, captured.get("state_cb")


# ---------------------------------------------------------------------------
# Tests: feature disabled (empty presence entity)
# ---------------------------------------------------------------------------

class TestPresenceDisabled:
    def test_no_state_tracker_when_empty_list(self):
        """When OPT_PRESENCE_ENTITY is [], async_track_state_change_event is NOT called."""
        session = _make_fake_session()
        with patch(PATCH_TRACK_STATE) as mock_track:
            _do_setup(session, options={OPT_PRESENCE_ENTITY: []})
            mock_track.assert_not_called()

    def test_no_state_tracker_when_missing(self):
        session = _make_fake_session()
        with patch(PATCH_TRACK_STATE) as mock_track:
            _do_setup(session, options={})
            mock_track.assert_not_called()

    def test_no_state_tracker_when_entity_is_empty_string(self):
        """Backward compat: old str "" stored -> treated as disabled."""
        session = _make_fake_session()
        with patch(PATCH_TRACK_STATE) as mock_track:
            _do_setup(session, options={OPT_PRESENCE_ENTITY: ""})
            mock_track.assert_not_called()

    def test_presence_unsub_is_none_when_disabled(self):
        session = _make_fake_session()
        hass, entry, _ = _do_setup(session, options={})
        assert entry.runtime_data.presence_unsub is None

    def test_master_toggle_off_disables_even_with_entities(self):
        """child_lock_enabled=False must suppress the feature even when
        presence entities are configured (explicit off switch).
        """
        session = _make_fake_session()
        with patch(PATCH_TRACK_STATE) as mock_track:
            hass, entry, _ = _do_setup(
                session,
                options={
                    OPT_CHILD_LOCK_ENABLED: False,
                    OPT_PRESENCE_ENTITY: ["person.felix"],
                },
            )
            mock_track.assert_not_called()
            assert entry.runtime_data.presence_unsub is None

    def test_master_toggle_on_with_entities_registers_tracker(self):
        """child_lock_enabled=True + entities -> tracker registered."""
        session = _make_fake_session()
        with patch(PATCH_TRACK_STATE) as mock_track:
            mock_track.return_value = MagicMock()
            _do_setup(
                session,
                options={
                    OPT_CHILD_LOCK_ENABLED: True,
                    OPT_PRESENCE_ENTITY: ["person.felix"],
                },
            )
            mock_track.assert_called_once()


# ---------------------------------------------------------------------------
# Tests: backward compat — stored value is a plain str (old single-select)
# ---------------------------------------------------------------------------

class TestBackwardCompatStr:
    def test_single_str_treated_as_one_entity_list(self):
        """Old config stores a bare string; must register a listener on it."""
        session = _make_fake_session()
        opts = {OPT_PRESENCE_ENTITY: "person.felix"}
        with patch(PATCH_TRACK_STATE) as mock_track:
            mock_track.return_value = MagicMock()

            def _inner():
                from custom_components.bosch_shc.__init__ import async_setup_entry
                hass = _make_fake_hass()
                entry = _make_fake_entry(options=opts)
                with (
                    patch(PATCH_SESSION, return_value=session),
                    patch(PATCH_DR_GET, return_value=_make_fake_device_registry()),
                    patch(PATCH_PARSE_CERT, return_value=None),
                    patch(PATCH_TRACK_INTERVAL, return_value=MagicMock()),
                ):
                    _run(async_setup_entry(hass, entry))
                return entry

            _inner()

        mock_track.assert_called_once()
        entity_ids = mock_track.call_args[0][1]
        assert "person.felix" in entity_ids

    def test_single_str_lock_on_when_entity_home(self):
        """Backward compat str: entering present_state still locks devices."""
        therm = _make_device("therm-1")
        session = _make_fake_session(thermostats=[therm])
        opts = {OPT_PRESENCE_ENTITY: "person.felix"}
        hass, entry, state_cb = _do_setup(
            session, options=opts, capture_state_cb=True,
            hass_states={"person.felix": "home"},
        )
        assert state_cb is not None

        state_cb(_state_event("home", "not_home"))
        task_coro = hass.async_create_task.call_args[0][0]
        _run(task_coro)
        therm.async_set_child_lock.assert_awaited_once_with(True)

    def test_single_str_lock_off_when_entity_leaves(self):
        """Backward compat str: leaving present_state unlocks devices."""
        therm = _make_device("therm-1")
        session = _make_fake_session(thermostats=[therm])
        opts = {OPT_PRESENCE_ENTITY: "person.felix"}
        # Simulate entity now away
        hass, entry, state_cb = _do_setup(
            session, options=opts, capture_state_cb=True,
            hass_states={"person.felix": "not_home"},
        )
        state_cb(_state_event("not_home", "home"))
        task_coro = hass.async_create_task.call_args[0][0]
        _run(task_coro)
        therm.async_set_child_lock.assert_awaited_once_with(False)


# ---------------------------------------------------------------------------
# Tests: feature enabled — state tracker registered
# ---------------------------------------------------------------------------

class TestPresenceEnabled:
    def test_state_tracker_registered_with_list(self):
        """async_track_state_change_event called with all configured entities."""
        session = _make_fake_session()
        opts = {OPT_PRESENCE_ENTITY: ["person.felix", "device_tracker.phone"]}

        with patch(PATCH_TRACK_STATE) as mock_track:
            mock_track.return_value = MagicMock()

            def _inner():
                from custom_components.bosch_shc.__init__ import async_setup_entry
                hass = _make_fake_hass()
                entry = _make_fake_entry(options=opts)
                with (
                    patch(PATCH_SESSION, return_value=session),
                    patch(PATCH_DR_GET, return_value=_make_fake_device_registry()),
                    patch(PATCH_PARSE_CERT, return_value=None),
                    patch(PATCH_TRACK_INTERVAL, return_value=MagicMock()),
                ):
                    _run(async_setup_entry(hass, entry))
                return entry

            _inner()

        mock_track.assert_called_once()
        entity_ids = mock_track.call_args[0][1]
        assert "person.felix" in entity_ids
        assert "device_tracker.phone" in entity_ids

    def test_presence_unsub_stored_on_runtime_data(self):
        session = _make_fake_session()
        opts = {OPT_PRESENCE_ENTITY: ["person.felix"]}
        hass, entry, _ = _do_setup(session, options=opts, capture_state_cb=True)
        assert entry.runtime_data.presence_unsub is not None


# ---------------------------------------------------------------------------
# Tests: child lock ON when entering present_state
# ---------------------------------------------------------------------------

class TestChildLockOn:
    def _setup_with_devices(self, thermostat=None, bool_dev=None,
                            opts=None, hass_states=None):
        therm = thermostat or _make_device("therm-1")
        booldev = bool_dev or _make_device("bool-1")
        session = _make_fake_session(
            thermostats=[therm],
            bool_devices=[booldev],
        )
        if opts is None:
            opts = {
                OPT_PRESENCE_ENTITY: ["person.felix"],
            }
        hass, entry, state_cb = _do_setup(
            session, options=opts, capture_state_cb=True,
            hass_states=hass_states,
        )
        return hass, state_cb, therm, booldev

    def test_entering_present_state_sets_child_lock_true_on_thermostat(self):
        hass, state_cb, therm, booldev = self._setup_with_devices(
            hass_states={"person.felix": "home"},
        )
        assert state_cb is not None

        state_cb(_state_event("home", "not_home"))

        hass.async_create_task.assert_called_once()
        task_coro = hass.async_create_task.call_args[0][0]
        _run(task_coro)
        therm.async_set_child_lock.assert_awaited_once_with(True)

    def test_entering_present_state_sets_child_lock_true_on_bool_device(self):
        hass, state_cb, therm, booldev = self._setup_with_devices(
            hass_states={"person.felix": "home"},
        )
        state_cb(_state_event("home", "not_home"))
        task_coro = hass.async_create_task.call_args[0][0]
        _run(task_coro)
        booldev.async_set_child_lock.assert_awaited_once_with(True)

    def test_custom_present_state_triggers_lock(self):
        therm = _make_device("therm-1")
        session = _make_fake_session(thermostats=[therm])
        opts = {
            OPT_PRESENCE_ENTITY: ["input_boolean.guests"],
        }
        hass, entry, state_cb = _do_setup(
            session, options=opts, capture_state_cb=True,
            hass_states={"input_boolean.guests": "on"},
        )

        state_cb(_state_event("on", "off"))
        task_coro = hass.async_create_task.call_args[0][0]
        _run(task_coro)
        therm.async_set_child_lock.assert_awaited_once_with(True)


# ---------------------------------------------------------------------------
# Tests: child lock OFF when leaving present_state
# ---------------------------------------------------------------------------

class TestChildLockOff:
    def _setup_with_therm(self):
        therm = _make_device("therm-1")
        session = _make_fake_session(thermostats=[therm])
        opts = {
            OPT_PRESENCE_ENTITY: ["person.felix"],
        }
        hass, entry, state_cb = _do_setup(
            session, options=opts, capture_state_cb=True,
            # Entity is now away
            hass_states={"person.felix": "not_home"},
        )
        return hass, state_cb, therm

    def test_leaving_present_state_sets_child_lock_false(self):
        hass, state_cb, therm = self._setup_with_therm()
        state_cb(_state_event("not_home", "home"))
        task_coro = hass.async_create_task.call_args[0][0]
        _run(task_coro)
        therm.async_set_child_lock.assert_awaited_once_with(False)


# ---------------------------------------------------------------------------
# Tests: multi-entity any-home semantics
# ---------------------------------------------------------------------------

class TestMultiEntityAnyHome:
    """Verify ANY-home-ON / ALL-away-OFF logic with two entities."""

    def _setup_multi(self, hass_states):
        therm = _make_device("therm-1")
        session = _make_fake_session(thermostats=[therm])
        opts = {
            OPT_PRESENCE_ENTITY: ["person.alice", "person.bob"],
        }
        hass, entry, state_cb = _do_setup(
            session, options=opts, capture_state_cb=True,
            hass_states=hass_states,
        )
        return hass, state_cb, therm

    def test_any_entity_home_turns_lock_on(self):
        """alice=home, bob=not_home -> lock ON when alice arrives."""
        hass, state_cb, therm = self._setup_multi(
            {"person.alice": "home", "person.bob": "not_home"}
        )
        state_cb(_state_event("home", "not_home"))
        task_coro = hass.async_create_task.call_args[0][0]
        _run(task_coro)
        therm.async_set_child_lock.assert_awaited_once_with(True)

    def test_all_away_turns_lock_off(self):
        """alice=not_home, bob=not_home -> lock OFF when last one leaves."""
        hass, state_cb, therm = self._setup_multi(
            {"person.alice": "not_home", "person.bob": "not_home"}
        )
        state_cb(_state_event("not_home", "home"))
        task_coro = hass.async_create_task.call_args[0][0]
        _run(task_coro)
        therm.async_set_child_lock.assert_awaited_once_with(False)

    def test_one_away_other_still_home_lock_stays_on(self):
        """alice=home, bob leaves -> aggregate still ANY-home -> NO API call."""
        hass, state_cb, therm = self._setup_multi(
            {"person.alice": "home", "person.bob": "not_home"}
        )
        # Simulate the handler was already called once (lock ON, _last_lock_state=True)
        # by having alice arrive first:
        state_cb(_state_event("home", "not_home"))  # alice arrives
        first_task = hass.async_create_task.call_args[0][0]
        _run(first_task)
        therm.async_set_child_lock.assert_awaited_once_with(True)

        hass.async_create_task.reset_mock()
        therm.async_set_child_lock.reset_mock()

        # Now bob leaves — but alice is still home. Aggregate stays True.
        state_cb(_state_event("not_home", "home"))  # bob leaves
        # No second API call because _last_lock_state already True
        hass.async_create_task.assert_not_called()

    def test_both_entities_registered_as_listeners(self):
        """async_track_state_change_event receives both entity IDs."""
        session = _make_fake_session()
        opts = {
            OPT_PRESENCE_ENTITY: ["person.alice", "person.bob"],
        }
        with patch(PATCH_TRACK_STATE) as mock_track:
            mock_track.return_value = MagicMock()

            def _inner():
                from custom_components.bosch_shc.__init__ import async_setup_entry
                hass = _make_fake_hass()
                entry = _make_fake_entry(options=opts)
                with (
                    patch(PATCH_SESSION, return_value=session),
                    patch(PATCH_DR_GET, return_value=_make_fake_device_registry()),
                    patch(PATCH_PARSE_CERT, return_value=None),
                    patch(PATCH_TRACK_INTERVAL, return_value=MagicMock()),
                ):
                    _run(async_setup_entry(hass, entry))

            _inner()

        mock_track.assert_called_once()
        registered = mock_track.call_args[0][1]
        assert "person.alice" in registered
        assert "person.bob" in registered

    def test_redundant_write_suppressed_both_already_home(self):
        """If aggregate is already True and another home event arrives -> no second write."""
        hass, state_cb, therm = self._setup_multi(
            {"person.alice": "home", "person.bob": "home"}
        )
        # First arrival
        state_cb(_state_event("home", "not_home"))
        assert hass.async_create_task.call_count == 1
        _run(hass.async_create_task.call_args[0][0])

        # Second arrival while first is still home -> aggregate stays True
        hass.async_create_task.reset_mock()
        therm.async_set_child_lock.reset_mock()
        state_cb(_state_event("home", "not_home"))
        hass.async_create_task.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: no-op cases
# ---------------------------------------------------------------------------

class TestNoOp:
    def _setup(self):
        therm = _make_device("therm-1")
        session = _make_fake_session(thermostats=[therm])
        opts = {
            OPT_PRESENCE_ENTITY: ["person.felix"],
        }
        hass, entry, state_cb = _do_setup(
            session, options=opts, capture_state_cb=True,
            hass_states={"person.felix": "not_home"},
        )
        return hass, state_cb, therm

    def test_no_task_when_new_state_is_unavailable(self):
        hass, state_cb, therm = self._setup()
        state_cb(_state_event("unavailable", "home"))
        hass.async_create_task.assert_not_called()

    def test_no_task_when_new_state_is_unknown(self):
        hass, state_cb, therm = self._setup()
        state_cb(_state_event("unknown", "home"))
        hass.async_create_task.assert_not_called()

    def test_no_task_when_new_state_is_none(self):
        """new_state=None (entity removed): ignore."""
        hass, state_cb, therm = self._setup()
        event = SimpleNamespace(
            data={
                "new_state": None,
                "old_state": SimpleNamespace(state="home"),
            }
        )
        state_cb(event)
        hass.async_create_task.assert_not_called()

    def test_no_task_when_aggregate_unchanged_away(self):
        """Transition between two non-present states: aggregate stays False -> no write."""
        hass, state_cb, therm = self._setup()
        # person.felix is not_home (hass_states); any transition to another
        # non-home state must not trigger a write.
        # First ensure _last_lock_state is primed to False by an initial away event:
        state_cb(_state_event("not_home", "home"))  # leaves -> aggregate False
        first_count = hass.async_create_task.call_count
        if first_count:
            _run(hass.async_create_task.call_args[0][0])

        hass.async_create_task.reset_mock()
        therm.async_set_child_lock.reset_mock()
        # Another away->away transition: no change in aggregate
        state_cb(_state_event("away", "not_home"))
        hass.async_create_task.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: error handling — device async setter raises, no crash
# ---------------------------------------------------------------------------

class TestErrorHandling:
    def test_jsonrpc_error_caught_no_crash(self):
        from boschshcpy.api import JSONRPCError

        therm = MagicMock()
        therm.id = "therm-broken"
        therm.async_set_child_lock = AsyncMock(
            side_effect=JSONRPCError(-1, "network error")
        )

        session = _make_fake_session(thermostats=[therm])
        opts = {
            OPT_PRESENCE_ENTITY: ["person.felix"],
        }
        hass, entry, state_cb = _do_setup(
            session, options=opts, capture_state_cb=True,
            hass_states={"person.felix": "home"},
        )

        state_cb(_state_event("home", "not_home"))
        task_coro = hass.async_create_task.call_args[0][0]
        # Must NOT raise
        _run(task_coro)

    def test_shc_exception_caught_no_crash(self):
        from boschshcpy.exceptions import SHCException

        booldev = MagicMock()
        booldev.id = "bool-broken"
        booldev.async_set_child_lock = AsyncMock(
            side_effect=SHCException("timeout")
        )

        session = _make_fake_session(bool_devices=[booldev])
        opts = {
            OPT_PRESENCE_ENTITY: ["person.felix"],
        }
        hass, entry, state_cb = _do_setup(
            session, options=opts, capture_state_cb=True,
            hass_states={"person.felix": "home"},
        )

        state_cb(_state_event("home", "not_home"))
        task_coro = hass.async_create_task.call_args[0][0]
        _run(task_coro)


# ---------------------------------------------------------------------------
# Tests: unload cleans up presence_unsub
# ---------------------------------------------------------------------------

class TestUnloadCleansUp:
    def test_presence_unsub_called_on_unload(self):
        from custom_components.bosch_shc.__init__ import (
            async_setup_entry,
            async_unload_entry,
        )

        session = _make_fake_session()
        hass = _make_fake_hass()
        entry = _make_fake_entry(options={OPT_PRESENCE_ENTITY: ["person.felix"]})
        dr_mock = _make_fake_device_registry()
        presence_unsub_mock = MagicMock()

        def _capture_track_state(h, entity_ids, cb):
            return presence_unsub_mock

        with (
            patch(PATCH_SESSION, return_value=session),
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
        from custom_components.bosch_shc.__init__ import (
            async_setup_entry,
            async_unload_entry,
        )

        session = _make_fake_session()
        hass = _make_fake_hass()
        entry = _make_fake_entry(options={})
        dr_mock = _make_fake_device_registry()

        with (
            patch(PATCH_SESSION, return_value=session),
            patch(PATCH_DR_GET, return_value=dr_mock),
            patch(PATCH_PARSE_CERT, return_value=None),
            patch(PATCH_TRACK_INTERVAL, return_value=MagicMock()),
        ):
            _run(async_setup_entry(hass, entry))
            result = _run(async_unload_entry(hass, entry))

        assert result is True
