"""Tests for custom_components/bosch_shc/__init__.py.

Strategy: aggressive mocking — no real certs/network/HA harness.
- SHCSessionAsync patched at import site
- async_init/start_polling/stop_polling are AsyncMocks
- dr.async_get patched to return a mock DeviceRegistry
- hass is a hand-rolled SimpleNamespace / AsyncMock object
- asyncio.run() drives all async code
"""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import (
    ATTR_COMMAND,
    ATTR_DEVICE_ID,
    ATTR_ID,
    ATTR_NAME,
    EVENT_HOMEASSISTANT_STOP,
)

from custom_components.bosch_shc.const import (
    ATTR_EVENT_SUBTYPE,
    ATTR_EVENT_TYPE,
    ATTR_LAST_TIME_TRIGGERED,
    DATA_CERT_CHECK_UNSUB,
    DATA_SESSION,
    DATA_SHC,
    DATA_TITLE,
    DOMAIN,
    EVENT_BOSCH_SHC,
    SERVICE_TRIGGER_RAWSCAN,
    SERVICE_TRIGGER_SCENARIO,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cert_info(days_remaining: int):
    """Return a fake CertInfo-like object."""
    not_after = datetime.now(timezone.utc) + timedelta(days=days_remaining)
    obj = SimpleNamespace(days_remaining=days_remaining, not_after=not_after)
    return obj


def _make_shc_info(update_state: str = "NO_UPDATE_AVAILABLE"):
    """Return a fake SHC information object."""
    update_state_mock = SimpleNamespace(name=update_state)
    return SimpleNamespace(
        updateState=update_state_mock,
        unique_id="aa:bb:cc:dd:ee:ff",
        version="9.0.0",
        name="My SHC",
    )


def _make_fake_hass(*, domain_data=None):
    """Build a minimal fake HomeAssistant-like object."""
    hass = MagicMock()
    hass.data = domain_data if domain_data is not None else {}

    # async_add_executor_job: call the function synchronously and return it as a coro
    async def _executor_job(fn, *args):
        return fn(*args)

    hass.async_add_executor_job = _executor_job

    # config_entries mock
    hass.config_entries = MagicMock()
    hass.config_entries.async_forward_entry_setups = AsyncMock(return_value=True)
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
    hass.config_entries.async_reload = AsyncMock()

    # bus mock — async_listen_once returns a plain (non-async) cancel callable
    hass.bus = MagicMock()
    hass.bus.async_listen_once = MagicMock(return_value=MagicMock(return_value=None))
    hass.bus.async_fire = MagicMock()
    hass.bus.fire = MagicMock()

    # services mock
    hass.services = MagicMock()
    hass.services.async_register = MagicMock()
    # No service registered yet — so _register_rawscan_service (idempotent via
    # has_service) actually registers on first entry setup.
    hass.services.has_service = MagicMock(return_value=False)

    # async_create_task
    hass.async_create_task = MagicMock()

    return hass


def _make_fake_entry(entry_id="test_entry_id", title="Test SHC",
                     cert_path="", key_path="", host="192.168.1.1", options=None):
    """Build a fake config entry."""
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


def _make_fake_session(*, scenarios=None, universal_switches=None,
                       shc_info=None):
    """Build a fake SHCSessionAsync with AsyncMocks for async methods."""
    from boschshcpy import SHCSessionAsync as _SHCSessionAsync
    session = MagicMock(spec=_SHCSessionAsync)
    session.information = shc_info or _make_shc_info()
    session.scenarios = scenarios or []
    session.async_init = AsyncMock()
    session.start_polling = AsyncMock()
    session.stop_polling = AsyncMock()
    session.subscribe_scenario_callback = MagicMock()
    session.unsubscribe_scenario_callback = MagicMock()
    dh = MagicMock()
    dh.universal_switches = universal_switches or []
    session.device_helper = dh
    # async API object for rawscan dispatch
    api = MagicMock()
    api.get_devices = AsyncMock(return_value=[])
    api.get_services = AsyncMock(return_value=[])
    api.get_rooms = AsyncMock(return_value=[])
    api.get_scenarios = AsyncMock(return_value=[])
    api.get_messages = AsyncMock(return_value=[])
    api.get_information = AsyncMock(return_value={})
    api.get_public_information = AsyncMock(return_value={})
    api.get_domain_intrusion_detection = AsyncMock(return_value={})
    api.get_device = AsyncMock(return_value={})
    api.get_device_services = AsyncMock(return_value=[])
    api.get_device_service = AsyncMock(return_value={})
    api.close = AsyncMock()
    session.api = api
    return session


def _make_fake_device_registry():
    """Return a mock device registry where async_get_or_create returns a fake entry."""
    fake_device_entry = SimpleNamespace(id="fake_device_reg_id_001")
    dr_mock = MagicMock()
    dr_mock.async_get_or_create = MagicMock(return_value=fake_device_entry)
    return dr_mock


# ---------------------------------------------------------------------------
# Patch context: patch all external boundaries before importing __init__ funcs
# ---------------------------------------------------------------------------

PATCH_SESSION = "custom_components.bosch_shc.__init__.SHCSessionAsync"
PATCH_DR_GET = "custom_components.bosch_shc.__init__.dr.async_get"
PATCH_PARSE_CERT = "custom_components.bosch_shc.__init__.parse_certificate"
PATCH_TRACK_INTERVAL = "custom_components.bosch_shc.__init__.async_track_time_interval"
PATCH_IR_CREATE = "homeassistant.helpers.issue_registry.async_create_issue"


def _run(coro):
    """Run a coroutine using asyncio.run (Python 3.10+ safe, creates new event loop)."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Tests: async_setup_entry — happy path (no cert)
# ---------------------------------------------------------------------------

class TestAsyncSetupEntryHappyPath:
    """Baseline setup with no certificate configured."""

    def _do_setup(self, fake_session, *, shc_info=None):
        from custom_components.bosch_shc.__init__ import async_setup_entry

        hass = _make_fake_hass()
        entry = _make_fake_entry()
        dr_mock = _make_fake_device_registry()
        track_unsub = MagicMock()

        with (
            patch(PATCH_SESSION, return_value=fake_session) as session_cls,
            patch(PATCH_DR_GET, return_value=dr_mock),
            patch(PATCH_PARSE_CERT, return_value=None),
            patch(PATCH_TRACK_INTERVAL, return_value=track_unsub),
        ):
            result = _run(async_setup_entry(hass, entry))

        return result, hass, entry, session_cls

    def test_returns_true(self):
        session = _make_fake_session()
        result, _, _, _ = self._do_setup(session)
        assert result is True

    def test_hass_data_populated(self):
        session = _make_fake_session()
        _, hass, entry, _ = self._do_setup(session)
        assert DOMAIN in hass.data
        assert entry.entry_id in hass.data[DOMAIN]
        d = hass.data[DOMAIN][entry.entry_id]
        assert d[DATA_SESSION] is session
        assert DATA_SHC in d
        assert d[DATA_TITLE] == entry.title

    def test_platforms_forwarded(self):
        session = _make_fake_session()
        _, hass, entry, _ = self._do_setup(session)
        hass.config_entries.async_forward_entry_setups.assert_called_once()
        fwd_args = hass.config_entries.async_forward_entry_setups.call_args
        assert fwd_args[0][0] is entry  # first positional arg = entry

    def test_services_registered(self):
        """Services are registered in async_setup (module-level Bronze action); test
        must call async_setup first so the handlers exist.
        """
        from custom_components.bosch_shc.__init__ import async_setup
        session = _make_fake_session()
        _, hass, _, _ = self._do_setup(session)
        # Run module-level setup so domain services are registered
        _run(async_setup(hass, {}))
        calls = [c.args[1] for c in hass.services.async_register.call_args_list]
        assert SERVICE_TRIGGER_SCENARIO in calls
        assert SERVICE_TRIGGER_RAWSCAN in calls

    def test_start_polling_called(self):
        session = _make_fake_session()
        _, _, _, _ = self._do_setup(session)
        session.start_polling.assert_awaited_once()

    def test_stop_listener_registered(self):
        """bus.async_listen_once called with EVENT_HOMEASSISTANT_STOP."""
        session = _make_fake_session()
        _, hass, _, _ = self._do_setup(session)
        listen_args = [c.args[0] for c in hass.bus.async_listen_once.call_args_list]
        assert EVENT_HOMEASSISTANT_STOP in listen_args

    def test_update_listener_not_registered(self):
        """B2: add_update_listener must NOT be called — HA auto-reloads on options_flow
        async_create_entry; registering an extra reload listener caused a double-reload
        and a DeprecationWarning in HA 2026.6 (hard error in 2026.12).
        """
        session = _make_fake_session()
        _, _, entry, _ = self._do_setup(session)
        entry.add_update_listener.assert_not_called()

    def test_cert_check_unsub_stored(self):
        session = _make_fake_session()
        _, hass, entry, _ = self._do_setup(session)
        assert DATA_CERT_CHECK_UNSUB in hass.data[DOMAIN][entry.entry_id]

    def test_async_init_awaited(self):
        """async_init() must be awaited during setup (replaces executor SHCSession)."""
        session = _make_fake_session()
        _, _, _, _ = self._do_setup(session)
        session.async_init.assert_awaited_once()


# ---------------------------------------------------------------------------
# Tests: async_setup_entry — update_state branch
# ---------------------------------------------------------------------------

class TestSetupUpdateAvailable:
    def test_update_available_logs_warning(self):
        """When SHC reports UPDATE_AVAILABLE a LOGGER.warning is emitted — no exception."""
        from custom_components.bosch_shc.__init__ import async_setup_entry

        shc_info = _make_shc_info(update_state="UPDATE_AVAILABLE")
        session = _make_fake_session(shc_info=shc_info)
        hass = _make_fake_hass()
        entry = _make_fake_entry()
        dr_mock = _make_fake_device_registry()

        with (
            patch(PATCH_SESSION, return_value=session),
            patch(PATCH_DR_GET, return_value=dr_mock),
            patch(PATCH_PARSE_CERT, return_value=None),
            patch(PATCH_TRACK_INTERVAL, return_value=MagicMock()),
        ):
            result = _run(async_setup_entry(hass, entry))

        assert result is True


# ---------------------------------------------------------------------------
# Tests: async_setup_entry — certificate paths
# ---------------------------------------------------------------------------

class TestSetupCertBranches:
    def _setup_with_cert_info(self, cert_info_obj):
        from custom_components.bosch_shc.__init__ import async_setup_entry

        session = _make_fake_session()
        hass = _make_fake_hass()
        entry = _make_fake_entry(cert_path="/fake/cert.pem")
        dr_mock = _make_fake_device_registry()
        pn_create_mock = MagicMock()

        with (
            patch(PATCH_SESSION, return_value=session),
            patch(PATCH_DR_GET, return_value=dr_mock),
            patch(PATCH_PARSE_CERT, return_value=cert_info_obj),
            patch(PATCH_TRACK_INTERVAL, return_value=MagicMock()),
            patch(PATCH_IR_CREATE, pn_create_mock),
        ):
            result = _run(async_setup_entry(hass, entry))

        return result, pn_create_mock

    def test_valid_cert_no_warning(self):
        """Cert with 60 days remaining: setup succeeds, no notification."""
        cert_info = _make_cert_info(60)
        result, pn_create_mock = self._setup_with_cert_info(cert_info)
        assert result is True
        pn_create_mock.assert_not_called()

    def test_expiring_cert_triggers_notification(self):
        """Cert expiring in 10 days: setup succeeds + ir.async_create_issue called."""
        cert_info = _make_cert_info(10)
        result, pn_create_mock = self._setup_with_cert_info(cert_info)
        assert result is True
        pn_create_mock.assert_called_once()

    def test_expiring_cert_at_warning_boundary(self):
        """Cert at exactly CERT_EXPIRY_WARNING_DAYS (30): notification triggered."""
        from custom_components.bosch_shc.const import CERT_EXPIRY_WARNING_DAYS
        cert_info = _make_cert_info(CERT_EXPIRY_WARNING_DAYS)
        result, pn_create_mock = self._setup_with_cert_info(cert_info)
        assert result is True
        pn_create_mock.assert_called_once()

    def test_expired_cert_raises_auth_failed(self):
        """Expired cert raises ConfigEntryAuthFailed."""
        from homeassistant.exceptions import ConfigEntryAuthFailed
        cert_info = _make_cert_info(-5)

        from custom_components.bosch_shc.__init__ import async_setup_entry
        session = _make_fake_session()
        hass = _make_fake_hass()
        entry = _make_fake_entry(cert_path="/fake/cert.pem")

        with (
            patch(PATCH_SESSION, return_value=session),
            patch(PATCH_DR_GET, return_value=_make_fake_device_registry()),
            patch(PATCH_PARSE_CERT, return_value=cert_info),
            patch(PATCH_TRACK_INTERVAL, return_value=MagicMock()),
        ):
            with pytest.raises(ConfigEntryAuthFailed):
                _run(async_setup_entry(hass, entry))

    def test_cert_parse_exception_continues(self):
        """parse_certificate raising an exception is caught and setup continues."""
        from custom_components.bosch_shc.__init__ import async_setup_entry

        session = _make_fake_session()
        hass = _make_fake_hass()
        entry = _make_fake_entry(cert_path="/bad/cert.pem")
        dr_mock = _make_fake_device_registry()

        with (
            patch(PATCH_SESSION, return_value=session),
            patch(PATCH_DR_GET, return_value=dr_mock),
            patch(PATCH_PARSE_CERT, side_effect=ValueError("bad pem")),
            patch(PATCH_TRACK_INTERVAL, return_value=MagicMock()),
        ):
            result = _run(async_setup_entry(hass, entry))

        assert result is True


# ---------------------------------------------------------------------------
# Tests: async_setup_entry — SHC connection errors
# ---------------------------------------------------------------------------

class TestSetupConnectionErrors:
    def _setup_raising(self, exc_class):
        """Setup where async_init raises the given exception."""
        from custom_components.bosch_shc.__init__ import async_setup_entry

        session = _make_fake_session()
        # Make async_init raise the exception
        session.async_init = AsyncMock(side_effect=exc_class)
        hass = _make_fake_hass()
        entry = _make_fake_entry()

        with (
            patch(PATCH_SESSION, return_value=session),
            patch(PATCH_DR_GET, return_value=_make_fake_device_registry()),
            patch(PATCH_PARSE_CERT, return_value=None),
            patch(PATCH_TRACK_INTERVAL, return_value=MagicMock()),
        ):
            return _run(async_setup_entry(hass, entry))

    def test_auth_error_raises_config_entry_auth_failed(self):
        from boschshcpy.exceptions import SHCAuthenticationError
        from homeassistant.exceptions import ConfigEntryAuthFailed

        with pytest.raises(ConfigEntryAuthFailed):
            self._setup_raising(SHCAuthenticationError)

    def test_connection_error_raises_config_entry_not_ready(self):
        from boschshcpy.exceptions import SHCConnectionError
        from homeassistant.exceptions import ConfigEntryNotReady

        with pytest.raises(ConfigEntryNotReady):
            self._setup_raising(SHCConnectionError)


# ---------------------------------------------------------------------------
# Tests: scenario subscription
# ---------------------------------------------------------------------------

class TestScenarioSubscription:
    def test_scenario_callback_subscribed_with_no_scenarios(self):
        """Regression (Bug 1): subscribe_scenario_callback must be called ONCE even
        when the SHC has NO scenarios — otherwise scenario-triggered automations
        never fire on a controller that starts with an empty scenario list.
        """
        session = _make_fake_session(scenarios=[])
        from custom_components.bosch_shc.__init__ import async_setup_entry

        hass = _make_fake_hass()
        entry = _make_fake_entry()
        dr_mock = _make_fake_device_registry()

        with (
            patch(PATCH_SESSION, return_value=session),
            patch(PATCH_DR_GET, return_value=dr_mock),
            patch(PATCH_PARSE_CERT, return_value=None),
            patch(PATCH_TRACK_INTERVAL, return_value=MagicMock()),
        ):
            _run(async_setup_entry(hass, entry))

        session.subscribe_scenario_callback.assert_called_once_with("shc", ANY_callable)

    def test_scenario_callback_subscribed_once_even_with_multiple_scenarios(self):
        """Regression (Bug 1): when multiple scenarios exist, subscribe_scenario_callback
        must still only be called ONCE — the old loop caused N duplicate registrations.
        """
        scenarios = [
            SimpleNamespace(name="Morning", id="s1"),
            SimpleNamespace(name="Evening", id="s2"),
            SimpleNamespace(name="Night", id="s3"),
        ]
        session = _make_fake_session(scenarios=scenarios)
        from custom_components.bosch_shc.__init__ import async_setup_entry

        hass = _make_fake_hass()
        entry = _make_fake_entry()
        dr_mock = _make_fake_device_registry()

        with (
            patch(PATCH_SESSION, return_value=session),
            patch(PATCH_DR_GET, return_value=dr_mock),
            patch(PATCH_PARSE_CERT, return_value=None),
            patch(PATCH_TRACK_INTERVAL, return_value=MagicMock()),
        ):
            _run(async_setup_entry(hass, entry))

        session.subscribe_scenario_callback.assert_called_once_with("shc", ANY_callable)

    def test_scenario_callback_subscribed(self):
        """subscribe_scenario_callback called once per scenario in the list."""
        fake_scenario = SimpleNamespace(name="Guten Morgen", id="s1")
        session = _make_fake_session(scenarios=[fake_scenario])
        from custom_components.bosch_shc.__init__ import async_setup_entry

        hass = _make_fake_hass()
        entry = _make_fake_entry()
        dr_mock = _make_fake_device_registry()

        with (
            patch(PATCH_SESSION, return_value=session),
            patch(PATCH_DR_GET, return_value=dr_mock),
            patch(PATCH_PARSE_CERT, return_value=None),
            patch(PATCH_TRACK_INTERVAL, return_value=MagicMock()),
        ):
            _run(async_setup_entry(hass, entry))

        session.subscribe_scenario_callback.assert_called_once_with("shc", ANY_callable)

    def test_scenario_fire_event(self):
        """_scenario_trigger callback fires a bosch_shc event on the bus via async_fire."""
        fake_scenario = SimpleNamespace(name="Away", id="sc1")
        session = _make_fake_session(scenarios=[fake_scenario])

        captured_callbacks = []

        def _capture_subscribe(key, cb):
            captured_callbacks.append(cb)

        session.subscribe_scenario_callback = _capture_subscribe

        from custom_components.bosch_shc.__init__ import async_setup_entry

        hass = _make_fake_hass()
        entry = _make_fake_entry()
        dr_mock = _make_fake_device_registry()

        with (
            patch(PATCH_SESSION, return_value=session),
            patch(PATCH_DR_GET, return_value=dr_mock),
            patch(PATCH_PARSE_CERT, return_value=None),
            patch(PATCH_TRACK_INTERVAL, return_value=MagicMock()),
        ):
            _run(async_setup_entry(hass, entry))

        assert len(captured_callbacks) == 1
        cb = captured_callbacks[0]
        cb({"id": "sc1", "lastTimeTriggered": 1234567, "name": "Away"})
        # _scenario_trigger fires via hass.bus.async_fire (async session fires on the loop)
        hass.bus.async_fire.assert_called_once()
        args = hass.bus.async_fire.call_args.args
        assert args[0] == EVENT_BOSCH_SHC
        fired_data = args[1]
        assert fired_data[ATTR_EVENT_TYPE] == "SCENARIO"
        assert fired_data[ATTR_EVENT_SUBTYPE] == "Away"


# Helper sentinel: any callable
class _AnyCallable:
    def __eq__(self, other):
        return callable(other)

    def __repr__(self):
        return "<any callable>"


ANY_callable = _AnyCallable()


# ---------------------------------------------------------------------------
# Tests: async_unload_entry
# ---------------------------------------------------------------------------

class TestAsyncUnloadEntry:
    def _setup_and_unload(self):
        from custom_components.bosch_shc.__init__ import (
            async_setup_entry,
            async_unload_entry,
        )

        session = _make_fake_session()
        hass = _make_fake_hass()
        entry = _make_fake_entry()
        dr_mock = _make_fake_device_registry()
        track_unsub = MagicMock(return_value=None)  # unsub callable
        poll_handler = MagicMock(return_value=None)

        hass.bus.async_listen_once = MagicMock(return_value=poll_handler)

        with (
            patch(PATCH_SESSION, return_value=session),
            patch(PATCH_DR_GET, return_value=dr_mock),
            patch(PATCH_PARSE_CERT, return_value=None),
            patch(PATCH_TRACK_INTERVAL, return_value=track_unsub),
        ):
            _run(async_setup_entry(hass, entry))

        result = _run(async_unload_entry(hass, entry))
        return result, hass, entry, session, track_unsub

    def test_unload_returns_true(self):
        result, _, _, _, _ = self._setup_and_unload()
        assert result is True

    def test_unload_calls_unsubscribe_scenario(self):
        _, _, _, session, _ = self._setup_and_unload()
        session.unsubscribe_scenario_callback.assert_called_with("shc")

    def test_unload_calls_stop_polling(self):
        _, _, _, session, _ = self._setup_and_unload()
        # stop_polling called at least once (once during unload)
        assert session.stop_polling.await_count >= 1

    def test_unload_calls_cert_check_unsub(self):
        _, _, _, _, track_unsub = self._setup_and_unload()
        track_unsub.assert_called()

    def test_unload_removes_entry_from_hass_data(self):
        _, hass, entry, _, _ = self._setup_and_unload()
        assert entry.entry_id not in hass.data.get(DOMAIN, {})

    def test_platforms_unloaded(self):
        _, hass, entry, _, _ = self._setup_and_unload()
        hass.config_entries.async_unload_platforms.assert_called_once()


# ---------------------------------------------------------------------------
# Tests: B2 — no update listener (options-flow auto-reload)
# ---------------------------------------------------------------------------

class TestB2NoUpdateListener:
    """B2: async_update_options and add_update_listener were removed.

    HA auto-reloads the config entry when OptionsFlow.async_create_entry is
    called (the options flow already does this).  Registering an extra
    add_update_listener that also calls async_reload caused a double-reload
    and a DeprecationWarning in 2026.6 / hard error in 2026.12.
    """

    def test_async_update_options_removed(self):
        """async_update_options must no longer exist in __init__."""
        import custom_components.bosch_shc.__init__ as init_mod
        assert not hasattr(init_mod, "async_update_options"), (
            "async_update_options was removed (B2 fix) but still present"
        )

    def test_add_update_listener_not_called_during_setup(self):
        """Setup must not register an add_update_listener (B2 fix)."""
        from custom_components.bosch_shc.__init__ import async_setup_entry

        session = _make_fake_session()
        hass = _make_fake_hass()
        entry = _make_fake_entry()
        dr_mock = _make_fake_device_registry()

        with (
            patch(PATCH_SESSION, return_value=session),
            patch(PATCH_DR_GET, return_value=dr_mock),
            patch(PATCH_PARSE_CERT, return_value=None),
            patch(PATCH_TRACK_INTERVAL, return_value=MagicMock()),
        ):
            _run(async_setup_entry(hass, entry))

        entry.add_update_listener.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: register_services / service handlers
# ---------------------------------------------------------------------------

class TestServiceHandlers:
    """Test that registered service handlers call session methods correctly."""

    def _setup_with_session(self, session):
        from custom_components.bosch_shc.__init__ import async_setup, async_setup_entry

        hass = _make_fake_hass()
        entry = _make_fake_entry()
        dr_mock = _make_fake_device_registry()

        with (
            patch(PATCH_SESSION, return_value=session),
            patch(PATCH_DR_GET, return_value=dr_mock),
            patch(PATCH_PARSE_CERT, return_value=None),
            patch(PATCH_TRACK_INTERVAL, return_value=MagicMock()),
        ):
            # async_setup registers domain-level services (Bronze action); must run first
            _run(async_setup(hass, {}))
            _run(async_setup_entry(hass, entry))

        # Wire async_entries so service handlers can look up runtime_data
        hass.config_entries.async_entries.return_value = [entry]

        # Extract registered service handlers by service name
        handlers = {}
        for c in hass.services.async_register.call_args_list:
            _, svc_name, handler, *_ = c.args
            handlers[svc_name] = handler
        return handlers, hass, entry, session

    def _make_service_call(self, **data):
        call = MagicMock()
        call.data = data
        return call

    # -- scenario service --

    def test_scenario_service_triggers_matching_scenario(self):
        fake_scenario = MagicMock()
        fake_scenario.name = "Night Mode"
        fake_scenario.async_trigger = AsyncMock()
        session = _make_fake_session(scenarios=[fake_scenario])

        handlers, hass, entry, session_obj = self._setup_with_session(session)
        handler = handlers[SERVICE_TRIGGER_SCENARIO]

        call_obj = self._make_service_call(**{ATTR_NAME: "Night Mode", "title": ""})
        _run(handler(call_obj))
        fake_scenario.async_trigger.assert_awaited_once()

    def test_scenario_service_skips_nonmatching_name(self):
        fake_scenario = MagicMock()
        fake_scenario.name = "Away"
        fake_scenario.async_trigger = AsyncMock()
        session = _make_fake_session(scenarios=[fake_scenario])

        handlers, hass, entry, _ = self._setup_with_session(session)
        handler = handlers[SERVICE_TRIGGER_SCENARIO]

        call_obj = self._make_service_call(**{ATTR_NAME: "Night Mode", "title": ""})
        _run(handler(call_obj))
        fake_scenario.async_trigger.assert_not_called()

    def test_scenario_service_filters_by_title(self):
        """When title doesn't match DATA_TITLE, skip that controller."""
        fake_scenario = MagicMock()
        fake_scenario.name = "Night Mode"
        fake_scenario.async_trigger = AsyncMock()
        session = _make_fake_session(scenarios=[fake_scenario])

        handlers, hass, entry, _ = self._setup_with_session(session)
        handler = handlers[SERVICE_TRIGGER_SCENARIO]

        call_obj = self._make_service_call(**{ATTR_NAME: "Night Mode", "title": "OtherSHC"})
        _run(handler(call_obj))
        fake_scenario.async_trigger.assert_not_called()

    def test_scenario_service_empty_title_matches_all(self):
        """Empty title string matches any controller (default)."""
        fake_scenario = MagicMock()
        fake_scenario.name = "Night Mode"
        fake_scenario.async_trigger = AsyncMock()
        session = _make_fake_session(scenarios=[fake_scenario])

        handlers, hass, entry, _ = self._setup_with_session(session)
        handler = handlers[SERVICE_TRIGGER_SCENARIO]

        call_obj = self._make_service_call(**{ATTR_NAME: "Night Mode", "title": ""})
        _run(handler(call_obj))
        fake_scenario.async_trigger.assert_awaited_once()

    # -- rawscan service --

    def test_rawscan_service_calls_api_get_devices(self):
        """'devices' command dispatches to api.get_devices()."""
        session = _make_fake_session()
        session.api.get_devices = AsyncMock(return_value={"devices": "ok"})

        handlers, hass, entry, session_obj = self._setup_with_session(session)
        handler = handlers[SERVICE_TRIGGER_RAWSCAN]

        call_obj = self._make_service_call(**{
            "title": "",
            ATTR_COMMAND: "devices",
            ATTR_DEVICE_ID: "",
            "service_id": "",
        })
        result = _run(handler(call_obj))
        session_obj.api.get_devices.assert_awaited_once()
        assert result == {"devices": {"devices": "ok"}}

    def test_rawscan_service_calls_api_get_information(self):
        """'info' command dispatches to api.get_information()."""
        session = _make_fake_session()
        session.api.get_information = AsyncMock(return_value={"version": "9.0"})

        handlers, hass, entry, session_obj = self._setup_with_session(session)
        handler = handlers[SERVICE_TRIGGER_RAWSCAN]

        call_obj = self._make_service_call(**{
            "title": "",
            ATTR_COMMAND: "info",
            ATTR_DEVICE_ID: "",
            "service_id": "",
        })
        result = _run(handler(call_obj))
        session_obj.api.get_information.assert_awaited_once()
        assert result == {"info": {"version": "9.0"}}

    def test_rawscan_service_filters_by_title(self):
        """Title mismatch → ServiceValidationError (no matching entry)."""
        from homeassistant.exceptions import ServiceValidationError

        session = _make_fake_session()

        handlers, hass, entry, session_obj = self._setup_with_session(session)
        handler = handlers[SERVICE_TRIGGER_RAWSCAN]

        call_obj = self._make_service_call(**{
            "title": "WrongTitle",
            ATTR_COMMAND: "devices",
            ATTR_DEVICE_ID: "",
            "service_id": "",
        })
        with pytest.raises(ServiceValidationError):
            _run(handler(call_obj))
        session_obj.api.get_devices.assert_not_awaited()

    def test_rawscan_service_unknown_command_raises(self):
        """Unknown rawscan command raises ServiceValidationError."""
        from homeassistant.exceptions import ServiceValidationError
        session = _make_fake_session()

        handlers, hass, entry, session_obj = self._setup_with_session(session)
        handler = handlers[SERVICE_TRIGGER_RAWSCAN]

        call_obj = self._make_service_call(**{
            "title": "",
            ATTR_COMMAND: "nonexistent_cmd",
            ATTR_DEVICE_ID: "",
            "service_id": "",
        })
        with pytest.raises(ServiceValidationError):
            _run(handler(call_obj))


# ---------------------------------------------------------------------------
# Tests: SwitchDeviceEventListener
# ---------------------------------------------------------------------------

class TestSwitchDeviceEventListener:
    """Tests for SwitchDeviceEventListener — inputs, shutdown, unsupported events."""

    def _make_keypad_service(self):
        ks = MagicMock()
        ks.id = "Keypad"
        ks.subscribe_callback = MagicMock()
        ks.unsubscribe_callback = MagicMock()
        return ks

    def _make_switch_device(self, keypad_service=None, supported_event=True):
        dev = MagicMock()
        dev.id = "switch-001"
        dev.name = "Test Switch"
        dev.manufacturer = "Bosch"
        dev.device_model = "WRC2"
        dev.root_device_id = "root-001"
        dev.eventtimestamp = 99999
        dev.device_services = [keypad_service] if keypad_service else []
        if supported_event:
            dev.eventtype = SimpleNamespace(name="PRESS_SHORT")
        else:
            dev.eventtype = SimpleNamespace(name="UNKNOWN_EVENT_TYPE")
        dev.keyname = SimpleNamespace(name="UPPER_BUTTON")
        return dev

    def test_init_does_not_subscribe_keypad_before_setup(self):
        """Regression (Bug 2): subscribe_callback must NOT be called in __init__ —
        device_id is None at that point, so events fired before async_setup() would
        carry ATTR_DEVICE_ID=None and never match device-trigger automations.
        """
        from custom_components.bosch_shc.__init__ import SwitchDeviceEventListener

        hass = _make_fake_hass()
        entry = _make_fake_entry()
        ks = self._make_keypad_service()
        dev = self._make_switch_device(keypad_service=ks)

        SwitchDeviceEventListener(hass, entry, dev)
        ks.subscribe_callback.assert_not_called()

    def test_async_setup_subscribes_keypad_after_device_id_set(self):
        """Regression (Bug 2): subscribe_callback is called in async_setup(), after
        self.device_id is populated — guaranteeing events carry a valid device_id.
        """
        from custom_components.bosch_shc.__init__ import SwitchDeviceEventListener

        hass = _make_fake_hass()
        entry = _make_fake_entry()
        ks = self._make_keypad_service()
        dev = self._make_switch_device(keypad_service=ks)

        fake_dev_entry = SimpleNamespace(id="reg-dev-id-999")
        dr_mock = MagicMock()
        dr_mock.async_get_or_create = MagicMock(return_value=fake_dev_entry)

        listener = SwitchDeviceEventListener(hass, entry, dev)
        # Not subscribed yet
        ks.subscribe_callback.assert_not_called()
        assert listener.device_id is None

        with patch(PATCH_DR_GET, return_value=dr_mock):
            _run(listener.async_setup())

        # Now subscribed, and device_id is already set before subscribe
        ks.subscribe_callback.assert_called_once_with(dev.id, listener._input_events_handler)
        assert listener.device_id == "reg-dev-id-999"

    def test_input_events_handler_fires_bus_event(self):
        """_input_events_handler fires via hass.bus.async_fire (async session)."""
        from custom_components.bosch_shc.__init__ import SwitchDeviceEventListener

        hass = _make_fake_hass()
        entry = _make_fake_entry()
        ks = self._make_keypad_service()
        dev = self._make_switch_device(keypad_service=ks, supported_event=True)

        listener = SwitchDeviceEventListener(hass, entry, dev)
        listener.device_id = "hass-device-id-123"
        listener._input_events_handler()

        # Async session fires callbacks on the loop — async_fire used directly
        hass.bus.async_fire.assert_called_once()
        args = hass.bus.async_fire.call_args.args
        assert args[0] == EVENT_BOSCH_SHC
        event_data = args[1]
        assert event_data[ATTR_EVENT_TYPE] == "PRESS_SHORT"
        assert event_data[ATTR_EVENT_SUBTYPE] == "UPPER_BUTTON"
        assert event_data[ATTR_ID] == dev.id
        assert event_data[ATTR_NAME] == dev.name
        assert event_data[ATTR_LAST_TIME_TRIGGERED] == dev.eventtimestamp
        assert event_data[ATTR_DEVICE_ID] == "hass-device-id-123"

    def test_input_events_handler_unsupported_event_no_fire(self):
        from custom_components.bosch_shc.__init__ import SwitchDeviceEventListener

        hass = _make_fake_hass()
        entry = _make_fake_entry()
        ks = self._make_keypad_service()
        dev = self._make_switch_device(keypad_service=ks, supported_event=False)

        listener = SwitchDeviceEventListener(hass, entry, dev)
        listener.device_id = "dev-id"
        listener._input_events_handler()

        hass.bus.async_fire.assert_not_called()

    def test_shutdown_unsubscribes_keypad(self):
        from custom_components.bosch_shc.__init__ import SwitchDeviceEventListener

        hass = _make_fake_hass()
        entry = _make_fake_entry()
        ks = self._make_keypad_service()
        dev = self._make_switch_device(keypad_service=ks)

        listener = SwitchDeviceEventListener(hass, entry, dev)
        listener.shutdown()
        ks.unsubscribe_callback.assert_called_once_with(dev.id)

    def test_no_keypad_service_no_subscribe(self):
        """Device with no 'Keypad' service: no subscribe_callback called."""
        from custom_components.bosch_shc.__init__ import SwitchDeviceEventListener

        hass = _make_fake_hass()
        entry = _make_fake_entry()
        dev = self._make_switch_device(keypad_service=None)

        listener = SwitchDeviceEventListener(hass, entry, dev)
        assert listener._keypad_service is None

    def test_async_setup_sets_device_id(self):
        """async_setup populates self.device_id from device registry."""
        from custom_components.bosch_shc.__init__ import SwitchDeviceEventListener

        hass = _make_fake_hass()
        entry = _make_fake_entry()
        ks = self._make_keypad_service()
        dev = self._make_switch_device(keypad_service=ks)

        fake_dev_entry = SimpleNamespace(id="reg-dev-id-999")
        dr_mock = MagicMock()
        dr_mock.async_get_or_create = MagicMock(return_value=fake_dev_entry)

        listener = SwitchDeviceEventListener(hass, entry, dev)
        with patch(PATCH_DR_GET, return_value=dr_mock):
            _run(listener.async_setup())

        assert listener.device_id == "reg-dev-id-999"


# ---------------------------------------------------------------------------
# Tests: universal switch wired into setup
# ---------------------------------------------------------------------------

class TestSetupUniversalSwitches:
    def test_switch_device_event_listener_setup_called(self):
        """Each universal switch device gets a SwitchDeviceEventListener."""
        from custom_components.bosch_shc.__init__ import async_setup_entry

        ks = MagicMock()
        ks.id = "Keypad"
        ks.subscribe_callback = MagicMock()

        dev = MagicMock()
        dev.id = "sw-001"
        dev.name = "Switch 1"
        dev.manufacturer = "Bosch"
        dev.device_model = "WRC2"
        dev.root_device_id = "root-001"
        dev.device_services = [ks]

        session = _make_fake_session(universal_switches=[dev])
        hass = _make_fake_hass()
        entry = _make_fake_entry()

        fake_dr = _make_fake_device_registry()

        with (
            patch(PATCH_SESSION, return_value=session),
            patch(PATCH_DR_GET, return_value=fake_dr),
            patch(PATCH_PARSE_CERT, return_value=None),
            patch(PATCH_TRACK_INTERVAL, return_value=MagicMock()),
        ):
            result = _run(async_setup_entry(hass, entry))

        assert result is True
        # Keypad service should have been subscribed
        ks.subscribe_callback.assert_called_once()


# ---------------------------------------------------------------------------
# Tests: _scheduled_cert_check (daily check)
# ---------------------------------------------------------------------------

class TestScheduledCertCheck:
    """Call the _scheduled_cert_check callback directly.

    IMPORTANT: The patch context manager must remain ACTIVE when the callback
    is invoked, because the callback calls parse_certificate through
    hass.async_add_executor_job. We therefore run setup + callback invocation
    inside a single patch context block (using a nested-async helper).
    """

    def _run_with_cert_check(self, cert_info_for_check, *, raise_on_check=False):
        """Setup, capture the daily cert-check callback, call it, return (hass, pn_mock)."""
        from custom_components.bosch_shc.__init__ import async_setup_entry

        session = _make_fake_session()
        hass = _make_fake_hass()
        entry = _make_fake_entry(cert_path="/fake/cert.pem")
        dr_mock = _make_fake_device_registry()
        pn_create_mock = MagicMock()
        captured = {}
        call_count = [0]

        def _parse(*args):
            call_count[0] += 1
            if call_count[0] == 1:
                # Initial call during setup — no cert path provided (empty str fallback)
                return None
            # Second call from inside _scheduled_cert_check
            if raise_on_check:
                raise OSError("gone")
            return cert_info_for_check

        def _capture_track(h, fn, interval):
            captured["fn"] = fn
            return MagicMock()

        async def _inner():
            await async_setup_entry(hass, entry)
            cb = captured.get("fn")
            assert cb is not None, "cert check callback was not captured"
            await cb(None)

        with (
            patch(PATCH_SESSION, return_value=session),
            patch(PATCH_DR_GET, return_value=dr_mock),
            patch(PATCH_PARSE_CERT, side_effect=_parse),
            patch(PATCH_TRACK_INTERVAL, side_effect=_capture_track),
            patch(PATCH_IR_CREATE, pn_create_mock),
        ):
            asyncio.run(_inner())

        return hass, pn_create_mock

    def test_expired_cert_triggers_reload(self):
        """Daily check with expired cert creates an async reload task."""
        cert_info = _make_cert_info(-1)
        hass, _ = self._run_with_cert_check(cert_info)
        hass.async_create_task.assert_called_once()

    def test_expiring_cert_creates_notification(self):
        """Daily check with expiring cert calls ir.async_create_issue."""
        cert_info = _make_cert_info(10)
        _, pn_create_mock = self._run_with_cert_check(cert_info)
        assert pn_create_mock.call_count >= 1

    def test_parse_exception_in_check_silently_returns(self):
        """parse_certificate raising inside daily check → silently return, no crash."""
        hass, _ = self._run_with_cert_check(None, raise_on_check=True)
        hass.async_create_task.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: Platform.VALVE optional guard
# ---------------------------------------------------------------------------

class TestPlatformValveGuard:
    def test_valve_in_platforms_if_available(self):
        from homeassistant.const import Platform

        from custom_components.bosch_shc.__init__ import PLATFORMS

        if hasattr(Platform, "VALVE"):
            assert Platform.VALVE in PLATFORMS
        else:
            assert Platform.VALVE not in PLATFORMS  # will AttributeError before, skip


# ---------------------------------------------------------------------------
# Tests: stop_polling listener (the stop_polling inner function)
# ---------------------------------------------------------------------------

class TestStopPollingListener:
    """Verify that the stop_polling listener actually calls session.stop_polling."""

    def test_stop_polling_inner_fn_calls_session(self):
        from custom_components.bosch_shc.__init__ import async_setup_entry

        session = _make_fake_session()
        hass = _make_fake_hass()
        entry = _make_fake_entry()
        dr_mock = _make_fake_device_registry()

        captured_listeners = {}

        def _capture_listen_once(event_name, fn):
            captured_listeners[event_name] = fn
            return MagicMock()

        hass.bus.async_listen_once = _capture_listen_once

        with (
            patch(PATCH_SESSION, return_value=session),
            patch(PATCH_DR_GET, return_value=dr_mock),
            patch(PATCH_PARSE_CERT, return_value=None),
            patch(PATCH_TRACK_INTERVAL, return_value=MagicMock()),
        ):
            _run(async_setup_entry(hass, entry))

        stop_polling_fn = captured_listeners.get(EVENT_HOMEASSISTANT_STOP)
        assert stop_polling_fn is not None
        # Call it (simulating HA stop)
        _run(stop_polling_fn(MagicMock()))
        # stop_polling should now have been awaited (start + stop)
        assert session.stop_polling.await_count >= 1
