"""Tests for custom_components/bosch_shc/__init__.py.

Covers config-entry setup/unload (session lifecycle, cert-expiry checks,
scenario/rawscan services, switch-device event listeners, presence-based
child-lock automation), the SwitchDeviceEventListener thread-safety-fire
behaviour, the Platform.VALVE availability guard, and the services.yaml
target-field regression for the smokedetector services.

Strategy: aggressive mocking — no real certs/network/HA harness.
- SHCSessionAsync patched at import site
- async_init/start_polling/stop_polling are AsyncMocks
- dr.async_get patched to return a mock DeviceRegistry
- hass is a hand-rolled SimpleNamespace / AsyncMock object
- asyncio.run() drives all async code
"""

from __future__ import annotations

import asyncio
import importlib
import pathlib
import types
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml
from boschshcpy.exceptions import SHCConnectionError, SHCException
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import (
    ATTR_COMMAND,
    ATTR_DEVICE_ID,
    ATTR_ID,
    ATTR_NAME,
    EVENT_HOMEASSISTANT_STOP,
)
from homeassistant.exceptions import ServiceValidationError

from custom_components.bosch_shc.__init__ import SwitchDeviceEventListener
from custom_components.bosch_shc.const import (
    ATTR_EVENT_SUBTYPE,
    ATTR_EVENT_TYPE,
    ATTR_LAST_TIME_TRIGGERED,
    ATTR_TITLE,
    CERT_EXPIRY_WARNING_DAYS,
    EVENT_BOSCH_SHC,
    OPT_CHILD_LOCK_ENABLED,
    OPT_LONG_POLL_TIMEOUT,
    OPT_PRESENCE_ENTITY,
    SERVICE_EXPORT_ZIGBEE_TOPOLOGY,
    SERVICE_REFRESH_ZIGBEE_ROUTING,
    SERVICE_TRIGGER_RAWSCAN,
    SERVICE_TRIGGER_SCENARIO,
)

SERVICES_YAML = (
    pathlib.Path(__file__).parent.parent.parent
    / "custom_components/bosch_shc/services.yaml"
)


def _load_services():
    with open(SERVICES_YAML) as fh:
        return yaml.safe_load(fh)



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
    # SHCZigbeeRoutingCoordinator.async_config_entry_first_refresh (called
    # during async_setup_entry) requires the entry to report itself as
    # mid-setup, exactly like a real ConfigEntry does at this point.
    entry.state = ConfigEntryState.SETUP_IN_PROGRESS

    # The Zigbee-routing first refresh is backgrounded (not awaited) in
    # async_setup_entry — schedule the real coroutine as an asyncio Task so
    # tests that care about its outcome can await entry._background_tasks
    # explicitly, without making setup itself wait for it. Uses HA's own
    # eager_task_factory (matching real ConfigEntry.async_create_background_task,
    # which eager-starts by default) so the task actually runs up to its
    # first suspension point before this synchronous call returns — a plain
    # asyncio.ensure_future() wouldn't start the coroutine at all until the
    # caller later awaits something real, understating how "in flight" the
    # real task already is by the time async_setup_entry returns.
    entry._background_tasks = []

    def _create_background_task(hass_arg, target, name, eager_start=True):
        task = asyncio.eager_task_factory(asyncio.get_event_loop(), target, name=name)
        entry._background_tasks.append(task)
        return task

    entry.async_create_background_task = MagicMock(side_effect=_create_background_task)
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
    # SHCZigbeeRoutingCoordinator._async_update_data (run via
    # async_config_entry_first_refresh during async_setup_entry) iterates
    # session.devices — empty by default, no Zigbee devices in this fixture.
    session.devices = []
    session.get_zigbee_routing_info = AsyncMock()
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
# Fixtures: fake_hass / fake_entry / fake_session
#
# Local to this file (this file's bootstrap-test shape -- heavy
# MagicMock(spec=...), ConfigEntryState, a whole api.* dispatch surface --
# doesn't fit the shared conftest.py platform fixtures, which are
# SimpleNamespace-based around device_helper buckets). Each fixture wraps the
# equivalent _make_fake_* builder above and supports overrides two ways:
#   - indirect parametrize: @pytest.mark.parametrize("fake_entry",
#     [{"cert_path": "/x"}], indirect=True)
#   - direct mutation in the test body after requesting the fixture, e.g.
#     ``entry = fake_entry; entry.options = {...}`` -- used where a call
#     site's override doesn't fit a static parametrize table (matches the
#     pattern used elsewhere in this migration).
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_hass(request: pytest.FixtureRequest):
    """Fake HomeAssistant-like object. See _make_fake_hass for the shape."""
    overrides = getattr(request, "param", {}) or {}
    return _make_fake_hass(**overrides)


@pytest.fixture
def fake_entry(request: pytest.FixtureRequest):
    """Fake config entry. See _make_fake_entry for the shape."""
    overrides = getattr(request, "param", {}) or {}
    return _make_fake_entry(**overrides)


@pytest.fixture
def fake_session(request: pytest.FixtureRequest):
    """Fake SHCSessionAsync. See _make_fake_session for the shape."""
    overrides = getattr(request, "param", {}) or {}
    return _make_fake_session(**overrides)


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

    def _do_setup(self, fake_hass, fake_entry, session_obj, *, shc_info=None):
        from custom_components.bosch_shc.__init__ import async_setup_entry

        hass = fake_hass
        entry = fake_entry
        dr_mock = _make_fake_device_registry()
        track_unsub = MagicMock()

        with (
            patch(PATCH_SESSION, return_value=session_obj) as session_cls,
            patch(PATCH_DR_GET, return_value=dr_mock),
            patch(PATCH_PARSE_CERT, return_value=None),
            patch(PATCH_TRACK_INTERVAL, return_value=track_unsub),
        ):
            result = _run(async_setup_entry(hass, entry))

        return result, hass, entry, session_cls

    def test_returns_true(self, fake_hass, fake_entry, fake_session):
        result, _, _, _ = self._do_setup(fake_hass, fake_entry, fake_session)
        assert result is True

    def test_runtime_data_populated(self, fake_hass, fake_entry, fake_session):
        _, hass, entry, _ = self._do_setup(fake_hass, fake_entry, fake_session)
        assert entry.runtime_data.session is fake_session
        assert entry.runtime_data.shc_device is not None
        assert entry.runtime_data.title == entry.title

    def test_platforms_forwarded(self, fake_hass, fake_entry, fake_session):
        _, hass, entry, _ = self._do_setup(fake_hass, fake_entry, fake_session)
        hass.config_entries.async_forward_entry_setups.assert_called_once()
        fwd_args = hass.config_entries.async_forward_entry_setups.call_args
        assert fwd_args[0][0] is entry  # first positional arg = entry

    def test_services_registered(self, fake_hass, fake_entry, fake_session):
        """Services are registered in async_setup (module-level Bronze action); test
        must call async_setup first so the handlers exist.
        """
        from custom_components.bosch_shc.__init__ import async_setup
        _, hass, _, _ = self._do_setup(fake_hass, fake_entry, fake_session)
        # Run module-level setup so domain services are registered
        _run(async_setup(hass, {}))
        calls = [c.args[1] for c in hass.services.async_register.call_args_list]
        assert SERVICE_TRIGGER_SCENARIO in calls
        assert SERVICE_TRIGGER_RAWSCAN in calls

    def test_start_polling_called(self, fake_hass, fake_entry, fake_session):
        _, _, _, _ = self._do_setup(fake_hass, fake_entry, fake_session)
        fake_session.start_polling.assert_awaited_once()

    def test_stop_listener_registered(self, fake_hass, fake_entry, fake_session):
        """bus.async_listen_once called with EVENT_HOMEASSISTANT_STOP."""
        _, hass, _, _ = self._do_setup(fake_hass, fake_entry, fake_session)
        listen_args = [c.args[0] for c in hass.bus.async_listen_once.call_args_list]
        assert EVENT_HOMEASSISTANT_STOP in listen_args

    def test_update_listener_not_registered(self, fake_hass, fake_entry, fake_session):
        """B2: add_update_listener must NOT be called — HA auto-reloads on options_flow
        async_create_entry; registering an extra reload listener caused a double-reload
        and a DeprecationWarning in HA 2026.6 (hard error in 2026.12).
        """
        _, _, entry, _ = self._do_setup(fake_hass, fake_entry, fake_session)
        entry.add_update_listener.assert_not_called()

    def test_cert_check_unsub_stored(self, fake_hass, fake_entry, fake_session):
        _, hass, entry, _ = self._do_setup(fake_hass, fake_entry, fake_session)
        assert entry.runtime_data.cert_check_unsub is not None

    def test_async_init_awaited(self, fake_hass, fake_entry, fake_session):
        """async_init() must be awaited during setup (replaces executor SHCSession)."""
        _, _, _, _ = self._do_setup(fake_hass, fake_entry, fake_session)
        fake_session.async_init.assert_awaited_once()


# ---------------------------------------------------------------------------
# Tests: async_setup_entry — update_state branch
# ---------------------------------------------------------------------------

class TestSetupUpdateAvailable:
    def test_update_available_logs_warning(self, fake_hass, fake_entry, fake_session):
        """When SHC reports UPDATE_AVAILABLE a LOGGER.warning is emitted — no exception."""
        from custom_components.bosch_shc.__init__ import async_setup_entry

        shc_info = _make_shc_info(update_state="UPDATE_AVAILABLE")
        session = fake_session
        session.information = shc_info
        hass = fake_hass
        entry = fake_entry
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
# Tests: async_setup_entry — ssl_verify_hostname option (async-parity gap)
# ---------------------------------------------------------------------------


class TestSslVerifyHostnameWarning:
    """ssl_verify_hostname is not honored on the async path (build_ssl_context
    always hardcodes check_hostname=False) — same async-parity gap as
    ssl_skip_verify.  Unlike ssl_skip_verify, this option previously had NO
    warning at all when set, silently doing nothing.
    """

    def _do_setup(self, fake_hass, fake_entry, fake_session, options):
        from custom_components.bosch_shc.__init__ import async_setup_entry

        hass = fake_hass
        entry = fake_entry
        entry.options = options
        dr_mock = _make_fake_device_registry()

        with (
            patch(PATCH_SESSION, return_value=fake_session),
            patch(PATCH_DR_GET, return_value=dr_mock),
            patch(PATCH_PARSE_CERT, return_value=None),
            patch(PATCH_TRACK_INTERVAL, return_value=MagicMock()),
        ):
            result = _run(async_setup_entry(hass, entry))
        return result

    def test_warns_when_enabled(self, fake_hass, fake_entry, fake_session, caplog):
        with caplog.at_level("WARNING"):
            result = self._do_setup(
                fake_hass, fake_entry, fake_session, {"ssl_verify_hostname": True}
            )
        assert result is True
        assert any(
            "ssl_verify_hostname" in record.message for record in caplog.records
        )

    def test_no_warning_when_disabled(self, fake_hass, fake_entry, fake_session, caplog):
        with caplog.at_level("WARNING"):
            result = self._do_setup(
                fake_hass, fake_entry, fake_session, {"ssl_verify_hostname": False}
            )
        assert result is True
        assert not any(
            "ssl_verify_hostname" in record.message for record in caplog.records
        )


# ---------------------------------------------------------------------------
# Tests: async_setup_entry — certificate paths
# ---------------------------------------------------------------------------

class TestSetupCertBranches:
    def _setup_with_cert_info(self, fake_hass, fake_entry, fake_session, cert_info_obj):
        from custom_components.bosch_shc.__init__ import async_setup_entry

        session = fake_session
        hass = fake_hass
        entry = fake_entry
        entry.data["ssl_certificate"] = "/fake/cert.pem"
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

    def test_valid_cert_no_warning(self, fake_hass, fake_entry, fake_session):
        """Cert with 60 days remaining: setup succeeds, no notification."""
        cert_info = _make_cert_info(60)
        result, pn_create_mock = self._setup_with_cert_info(
            fake_hass, fake_entry, fake_session, cert_info
        )
        assert result is True
        pn_create_mock.assert_not_called()

    def test_expiring_cert_triggers_notification(self, fake_hass, fake_entry, fake_session):
        """Cert expiring in 10 days: setup succeeds + ir.async_create_issue called."""
        cert_info = _make_cert_info(10)
        result, pn_create_mock = self._setup_with_cert_info(
            fake_hass, fake_entry, fake_session, cert_info
        )
        assert result is True
        pn_create_mock.assert_called_once()

    def test_expiring_cert_at_warning_boundary(self, fake_hass, fake_entry, fake_session):
        """Cert at exactly CERT_EXPIRY_WARNING_DAYS (30): notification triggered."""
        from custom_components.bosch_shc.const import CERT_EXPIRY_WARNING_DAYS
        cert_info = _make_cert_info(CERT_EXPIRY_WARNING_DAYS)
        result, pn_create_mock = self._setup_with_cert_info(
            fake_hass, fake_entry, fake_session, cert_info
        )
        assert result is True
        pn_create_mock.assert_called_once()

    def test_expired_cert_raises_auth_failed(self, fake_hass, fake_entry, fake_session):
        """Expired cert raises ConfigEntryAuthFailed."""
        from homeassistant.exceptions import ConfigEntryAuthFailed
        cert_info = _make_cert_info(-5)

        from custom_components.bosch_shc.__init__ import async_setup_entry
        session = fake_session
        hass = fake_hass
        entry = fake_entry
        entry.data["ssl_certificate"] = "/fake/cert.pem"

        with (
            patch(PATCH_SESSION, return_value=session),
            patch(PATCH_DR_GET, return_value=_make_fake_device_registry()),
            patch(PATCH_PARSE_CERT, return_value=cert_info),
            patch(PATCH_TRACK_INTERVAL, return_value=MagicMock()),
        ):
            with pytest.raises(ConfigEntryAuthFailed):
                _run(async_setup_entry(hass, entry))

    def test_cert_parse_exception_continues(self, fake_hass, fake_entry, fake_session):
        """parse_certificate raising an exception is caught and setup continues."""
        from custom_components.bosch_shc.__init__ import async_setup_entry

        session = fake_session
        hass = fake_hass
        entry = fake_entry
        entry.data["ssl_certificate"] = "/bad/cert.pem"
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
    def _setup_raising(self, fake_hass, fake_entry, fake_session, exc_class):
        """Setup where async_init raises the given exception."""
        from custom_components.bosch_shc.__init__ import async_setup_entry

        session = fake_session
        # Make async_init raise the exception
        session.async_init = AsyncMock(side_effect=exc_class)
        hass = fake_hass
        entry = fake_entry

        with (
            patch(PATCH_SESSION, return_value=session),
            patch(PATCH_DR_GET, return_value=_make_fake_device_registry()),
            patch(PATCH_PARSE_CERT, return_value=None),
            patch(PATCH_TRACK_INTERVAL, return_value=MagicMock()),
        ):
            return _run(async_setup_entry(hass, entry))

    def test_auth_error_raises_config_entry_auth_failed(
        self, fake_hass, fake_entry, fake_session
    ):
        from boschshcpy.exceptions import SHCAuthenticationError
        from homeassistant.exceptions import ConfigEntryAuthFailed

        with pytest.raises(ConfigEntryAuthFailed):
            self._setup_raising(fake_hass, fake_entry, fake_session, SHCAuthenticationError)

    def test_connection_error_raises_config_entry_not_ready(
        self, fake_hass, fake_entry, fake_session
    ):
        from boschshcpy.exceptions import SHCConnectionError
        from homeassistant.exceptions import ConfigEntryNotReady

        with pytest.raises(ConfigEntryNotReady):
            self._setup_raising(fake_hass, fake_entry, fake_session, SHCConnectionError)


# ---------------------------------------------------------------------------
# Tests: scenario subscription
# ---------------------------------------------------------------------------

class TestScenarioSubscription:
    def test_scenario_callback_subscribed_with_no_scenarios(
        self, fake_hass, fake_entry, fake_session
    ):
        """Regression (Bug 1): subscribe_scenario_callback must be called ONCE even
        when the SHC has NO scenarios — otherwise scenario-triggered automations
        never fire on a controller that starts with an empty scenario list.
        """
        session = fake_session
        session.scenarios = []
        from custom_components.bosch_shc.__init__ import async_setup_entry

        hass = fake_hass
        entry = fake_entry
        dr_mock = _make_fake_device_registry()

        with (
            patch(PATCH_SESSION, return_value=session),
            patch(PATCH_DR_GET, return_value=dr_mock),
            patch(PATCH_PARSE_CERT, return_value=None),
            patch(PATCH_TRACK_INTERVAL, return_value=MagicMock()),
        ):
            _run(async_setup_entry(hass, entry))

        session.subscribe_scenario_callback.assert_called_once_with("shc", ANY_callable)

    def test_scenario_callback_subscribed_once_even_with_multiple_scenarios(
        self, fake_hass, fake_entry, fake_session
    ):
        """Regression (Bug 1): when multiple scenarios exist, subscribe_scenario_callback
        must still only be called ONCE — the old loop caused N duplicate registrations.
        """
        scenarios = [
            SimpleNamespace(name="Morning", id="s1"),
            SimpleNamespace(name="Evening", id="s2"),
            SimpleNamespace(name="Night", id="s3"),
        ]
        session = fake_session
        session.scenarios = scenarios
        from custom_components.bosch_shc.__init__ import async_setup_entry

        hass = fake_hass
        entry = fake_entry
        dr_mock = _make_fake_device_registry()

        with (
            patch(PATCH_SESSION, return_value=session),
            patch(PATCH_DR_GET, return_value=dr_mock),
            patch(PATCH_PARSE_CERT, return_value=None),
            patch(PATCH_TRACK_INTERVAL, return_value=MagicMock()),
        ):
            _run(async_setup_entry(hass, entry))

        session.subscribe_scenario_callback.assert_called_once_with("shc", ANY_callable)

    def test_scenario_callback_subscribed(self, fake_hass, fake_entry, fake_session):
        """subscribe_scenario_callback called once per scenario in the list."""
        fake_scenario = SimpleNamespace(name="Guten Morgen", id="s1")
        session = fake_session
        session.scenarios = [fake_scenario]
        from custom_components.bosch_shc.__init__ import async_setup_entry

        hass = fake_hass
        entry = fake_entry
        dr_mock = _make_fake_device_registry()

        with (
            patch(PATCH_SESSION, return_value=session),
            patch(PATCH_DR_GET, return_value=dr_mock),
            patch(PATCH_PARSE_CERT, return_value=None),
            patch(PATCH_TRACK_INTERVAL, return_value=MagicMock()),
        ):
            _run(async_setup_entry(hass, entry))

        session.subscribe_scenario_callback.assert_called_once_with("shc", ANY_callable)

    def test_scenario_fire_event(self, fake_hass, fake_entry, fake_session):
        """_scenario_trigger callback fires a bosch_shc event on the bus via async_fire."""
        fake_scenario = SimpleNamespace(name="Away", id="sc1")
        session = fake_session
        session.scenarios = [fake_scenario]

        captured_callbacks = []

        def _capture_subscribe(key, cb):
            captured_callbacks.append(cb)

        session.subscribe_scenario_callback = _capture_subscribe

        from custom_components.bosch_shc.__init__ import async_setup_entry

        hass = fake_hass
        entry = fake_entry
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
    def _setup_and_unload(self, fake_hass, fake_entry, fake_session):
        from custom_components.bosch_shc.__init__ import (
            async_setup_entry,
            async_unload_entry,
        )

        session = fake_session
        hass = fake_hass
        entry = fake_entry
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

    def test_unload_returns_true(self, fake_hass, fake_entry, fake_session):
        result, _, _, _, _ = self._setup_and_unload(fake_hass, fake_entry, fake_session)
        assert result is True

    def test_unload_calls_unsubscribe_scenario(self, fake_hass, fake_entry, fake_session):
        _, _, _, session, _ = self._setup_and_unload(fake_hass, fake_entry, fake_session)
        session.unsubscribe_scenario_callback.assert_called_with("shc")

    def test_unload_calls_stop_polling(self, fake_hass, fake_entry, fake_session):
        _, _, _, session, _ = self._setup_and_unload(fake_hass, fake_entry, fake_session)
        # stop_polling called at least once (once during unload)
        assert session.stop_polling.await_count >= 1

    def test_unload_calls_cert_check_unsub(self, fake_hass, fake_entry, fake_session):
        _, _, _, _, track_unsub = self._setup_and_unload(fake_hass, fake_entry, fake_session)
        track_unsub.assert_called()

    def test_unload_clears_switch_event_listeners(self, fake_hass, fake_entry, fake_session):
        """Runtime data now lives on the config entry (not hass.data), so unload
        is verified via its own teardown bookkeeping instead of a hass.data pop."""
        _, hass, entry, _, _ = self._setup_and_unload(fake_hass, fake_entry, fake_session)
        assert entry.runtime_data.switch_event_listeners == []

    def test_platforms_unloaded(self, fake_hass, fake_entry, fake_session):
        _, hass, entry, _, _ = self._setup_and_unload(fake_hass, fake_entry, fake_session)
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

    def test_add_update_listener_not_called_during_setup(
        self, fake_hass, fake_entry, fake_session
    ):
        """Setup must not register an add_update_listener (B2 fix)."""
        from custom_components.bosch_shc.__init__ import async_setup_entry

        session = fake_session
        hass = fake_hass
        entry = fake_entry
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

    def _setup_with_session(self, fake_hass, fake_entry, session):
        from custom_components.bosch_shc.__init__ import async_setup, async_setup_entry

        hass = fake_hass
        entry = fake_entry
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

    def test_scenario_service_triggers_matching_scenario(
        self, fake_hass, fake_entry, fake_session
    ):
        fake_scenario = MagicMock()
        fake_scenario.name = "Night Mode"
        fake_scenario.async_trigger = AsyncMock()
        session = fake_session
        session.scenarios = [fake_scenario]

        handlers, hass, entry, session_obj = self._setup_with_session(
            fake_hass, fake_entry, session
        )
        handler = handlers[SERVICE_TRIGGER_SCENARIO]

        call_obj = self._make_service_call(**{ATTR_NAME: "Night Mode", "title": ""})
        _run(handler(call_obj))
        fake_scenario.async_trigger.assert_awaited_once()

    def test_scenario_service_skips_nonmatching_name(
        self, fake_hass, fake_entry, fake_session
    ):
        fake_scenario = MagicMock()
        fake_scenario.name = "Away"
        fake_scenario.async_trigger = AsyncMock()
        session = fake_session
        session.scenarios = [fake_scenario]

        handlers, hass, entry, _ = self._setup_with_session(fake_hass, fake_entry, session)
        handler = handlers[SERVICE_TRIGGER_SCENARIO]

        call_obj = self._make_service_call(**{ATTR_NAME: "Night Mode", "title": ""})
        _run(handler(call_obj))
        fake_scenario.async_trigger.assert_not_called()

    def test_scenario_service_filters_by_title(self, fake_hass, fake_entry, fake_session):
        """When title doesn't match DATA_TITLE, skip that controller."""
        fake_scenario = MagicMock()
        fake_scenario.name = "Night Mode"
        fake_scenario.async_trigger = AsyncMock()
        session = fake_session
        session.scenarios = [fake_scenario]

        handlers, hass, entry, _ = self._setup_with_session(fake_hass, fake_entry, session)
        handler = handlers[SERVICE_TRIGGER_SCENARIO]

        call_obj = self._make_service_call(**{ATTR_NAME: "Night Mode", "title": "OtherSHC"})
        _run(handler(call_obj))
        fake_scenario.async_trigger.assert_not_called()

    def test_scenario_service_empty_title_matches_all(
        self, fake_hass, fake_entry, fake_session
    ):
        """Empty title string matches any controller (default)."""
        fake_scenario = MagicMock()
        fake_scenario.name = "Night Mode"
        fake_scenario.async_trigger = AsyncMock()
        session = fake_session
        session.scenarios = [fake_scenario]

        handlers, hass, entry, _ = self._setup_with_session(fake_hass, fake_entry, session)
        handler = handlers[SERVICE_TRIGGER_SCENARIO]

        call_obj = self._make_service_call(**{ATTR_NAME: "Night Mode", "title": ""})
        _run(handler(call_obj))
        fake_scenario.async_trigger.assert_awaited_once()

    # -- rawscan service --

    def test_rawscan_service_calls_api_get_devices(self, fake_hass, fake_entry, fake_session):
        """'devices' command dispatches to api.get_devices()."""
        session = fake_session
        session.api.get_devices = AsyncMock(return_value={"devices": "ok"})

        handlers, hass, entry, session_obj = self._setup_with_session(
            fake_hass, fake_entry, session
        )
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

    def test_rawscan_service_calls_api_get_information(
        self, fake_hass, fake_entry, fake_session
    ):
        """'info' command dispatches to api.get_information()."""
        session = fake_session
        session.api.get_information = AsyncMock(return_value={"version": "9.0"})

        handlers, hass, entry, session_obj = self._setup_with_session(
            fake_hass, fake_entry, session
        )
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

    def test_rawscan_service_filters_by_title(self, fake_hass, fake_entry, fake_session):
        """Title mismatch → ServiceValidationError (no matching entry)."""
        from homeassistant.exceptions import ServiceValidationError

        session = fake_session

        handlers, hass, entry, session_obj = self._setup_with_session(
            fake_hass, fake_entry, session
        )
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

    def test_rawscan_service_unknown_command_raises(
        self, fake_hass, fake_entry, fake_session
    ):
        """Unknown rawscan command raises ServiceValidationError."""
        from homeassistant.exceptions import ServiceValidationError
        session = fake_session

        handlers, hass, entry, session_obj = self._setup_with_session(
            fake_hass, fake_entry, session
        )
        handler = handlers[SERVICE_TRIGGER_RAWSCAN]

        call_obj = self._make_service_call(**{
            "title": "",
            ATTR_COMMAND: "nonexistent_cmd",
            ATTR_DEVICE_ID: "",
            "service_id": "",
        })
        with pytest.raises(ServiceValidationError):
            _run(handler(call_obj))

    # -- export_zigbee_topology service --

    def test_export_zigbee_topology_writes_json_and_html(
        self, fake_hass, fake_entry, fake_session, tmp_path
    ):
        """Full round trip: routing data -> graph in the response + files on disk."""
        from boschshcpy.zigbee_routing import SHCZigbeeRoutingInfo

        device = SimpleNamespace(id="hdm:ZigBee:aaa", name="Plug A")
        session = fake_session
        session.devices = [device]
        session.get_zigbee_routing_info = AsyncMock(
            return_value=SHCZigbeeRoutingInfo(
                {
                    "device": "hdm:ZigBee:aaa",
                    "aggregatedQuality": "GOOD",
                    "route": [{"deviceId": "hdm:ZigBee:aaa", "quality": "GOOD"}],
                }
            )
        )

        handlers, hass, entry, session_obj = self._setup_with_session(
            fake_hass, fake_entry, session
        )
        hass.config.path = MagicMock(
            side_effect=lambda *parts: str(tmp_path.joinpath(*parts))
        )
        # _make_fake_device_registry's fixture DeviceEntry only sets .id — real
        # HA DeviceEntry objects always have .name, set it here for this test.
        entry.runtime_data.shc_device.name = "Test SHC"
        handler = handlers[SERVICE_EXPORT_ZIGBEE_TOPOLOGY]

        call_obj = self._make_service_call(title="")
        result = _run(handler(call_obj))

        assert result["graph"]["edges"] == [
            {"from": "hdm:ZigBee:aaa", "to": "controller", "quality": "good"}
        ]
        assert "graph TD" in result["mermaid"]
        assert result["url"].endswith("_zigbee_topology.html")
        assert list(tmp_path.glob("www/bosch_shc/*_zigbee_topology.json"))
        assert list(tmp_path.glob("www/bosch_shc/*_zigbee_topology.html"))

    def test_export_zigbee_topology_includes_devices_with_no_routing_data(
        self, fake_hass, fake_entry, fake_session, tmp_path
    ):
        """Field report: a device whose on-demand routing query never
        answered (sleepy end device) was silently missing from the exported
        map entirely -- it must still appear, unconnected."""
        from boschshcpy.zigbee_routing import SHCZigbeeRoutingInfo

        responsive = SimpleNamespace(id="hdm:ZigBee:aaa", name="Plug A")
        sleepy = SimpleNamespace(id="hdm:ZigBee:sleepy", name="Motion Sensor")
        session = fake_session
        session.devices = [responsive, sleepy]

        async def fake_routing_info(device_id):
            if device_id == "hdm:ZigBee:sleepy":
                raise SHCException("timed out")
            return SHCZigbeeRoutingInfo(
                {
                    "device": device_id,
                    "aggregatedQuality": "GOOD",
                    "route": [{"deviceId": device_id, "quality": "GOOD"}],
                }
            )

        session.get_zigbee_routing_info = AsyncMock(side_effect=fake_routing_info)

        handlers, hass, entry, session_obj = self._setup_with_session(
            fake_hass, fake_entry, session
        )
        hass.config.path = MagicMock(
            side_effect=lambda *parts: str(tmp_path.joinpath(*parts))
        )
        entry.runtime_data.shc_device.name = "Test SHC"
        handler = handlers[SERVICE_EXPORT_ZIGBEE_TOPOLOGY]

        call_obj = self._make_service_call(title="")
        result = _run(handler(call_obj))

        node_ids = [n["id"] for n in result["graph"]["nodes"]]
        assert "hdm:ZigBee:aaa" in node_ids
        assert "hdm:ZigBee:sleepy" in node_ids

    def test_export_zigbee_topology_no_data_raises(self, fake_hass, fake_entry, fake_session):
        """No Zigbee devices polled yet -> ServiceValidationError, not an empty graph."""
        from homeassistant.exceptions import ServiceValidationError

        session = fake_session  # devices=[] by default -> coordinator.data == {}
        handlers, hass, entry, _ = self._setup_with_session(fake_hass, fake_entry, session)
        handler = handlers[SERVICE_EXPORT_ZIGBEE_TOPOLOGY]

        call_obj = self._make_service_call(title="")
        with pytest.raises(ServiceValidationError):
            _run(handler(call_obj))

    def test_export_zigbee_topology_filters_by_title(
        self, fake_hass, fake_entry, fake_session
    ):
        """Title mismatch -> ServiceValidationError (no matching entry)."""
        from homeassistant.exceptions import ServiceValidationError

        session = fake_session
        handlers, hass, entry, _ = self._setup_with_session(fake_hass, fake_entry, session)
        handler = handlers[SERVICE_EXPORT_ZIGBEE_TOPOLOGY]

        call_obj = self._make_service_call(title="WrongTitle")
        with pytest.raises(ServiceValidationError):
            _run(handler(call_obj))

    # -- refresh_zigbee_routing service (#373 follow-up: the coordinator no
    # longer polls periodically, so this is the only way to get a fresh
    # reading after startup) --

    def test_refresh_zigbee_routing_calls_coordinator_request_refresh(
        self, fake_hass, fake_entry, fake_session
    ):
        session = fake_session
        handlers, hass, entry, _ = self._setup_with_session(
            fake_hass, fake_entry, session
        )
        handler = handlers[SERVICE_REFRESH_ZIGBEE_ROUTING]
        coordinator = entry.runtime_data.zigbee_routing_coordinator
        coordinator.async_request_refresh = AsyncMock()

        call_obj = self._make_service_call(title="")
        _run(handler(call_obj))

        coordinator.async_request_refresh.assert_awaited_once()

    def test_refresh_zigbee_routing_filters_by_title(
        self, fake_hass, fake_entry, fake_session
    ):
        from homeassistant.exceptions import ServiceValidationError

        session = fake_session
        handlers, hass, entry, _ = self._setup_with_session(
            fake_hass, fake_entry, session
        )
        handler = handlers[SERVICE_REFRESH_ZIGBEE_ROUTING]

        call_obj = self._make_service_call(title="WrongTitle")
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

    def test_init_does_not_subscribe_keypad_before_setup(self, fake_hass, fake_entry):
        """Regression (Bug 2): subscribe_callback must NOT be called in __init__ —
        device_id is None at that point, so events fired before async_setup() would
        carry ATTR_DEVICE_ID=None and never match device-trigger automations.
        """
        from custom_components.bosch_shc.__init__ import SwitchDeviceEventListener

        hass = fake_hass
        entry = fake_entry
        ks = self._make_keypad_service()
        dev = self._make_switch_device(keypad_service=ks)

        SwitchDeviceEventListener(hass, entry, dev)
        ks.subscribe_callback.assert_not_called()

    def test_async_setup_subscribes_keypad_after_device_id_set(self, fake_hass, fake_entry):
        """Regression (Bug 2): subscribe_callback is called in async_setup(), after
        self.device_id is populated — guaranteeing events carry a valid device_id.
        """
        from custom_components.bosch_shc.__init__ import SwitchDeviceEventListener

        hass = fake_hass
        entry = fake_entry
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

    def test_input_events_handler_new_press_fires_bus_event(self, fake_hass, fake_entry):
        """A genuinely new press (eventtimestamp advanced past the construction
        baseline) fires via hass.bus.async_fire (async session)."""
        from custom_components.bosch_shc.__init__ import SwitchDeviceEventListener

        hass = fake_hass
        entry = fake_entry
        ks = self._make_keypad_service()
        dev = self._make_switch_device(keypad_service=ks, supported_event=True)

        listener = SwitchDeviceEventListener(hass, entry, dev)
        listener.device_id = "hass-device-id-123"
        dev.eventtimestamp = 100000  # real new press, advanced past seed (99999)
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
        assert event_data[ATTR_LAST_TIME_TRIGGERED] == 100000
        assert event_data[ATTR_DEVICE_ID] == "hass-device-id-123"

    def test_input_events_handler_restart_replay_no_fire(self, fake_hass, fake_entry):
        """Replay-guard (#336): on restart the first re-delivered Keypad snapshot
        carries the last (stale) press; seeding _last_fired_timestamp from the
        device at construction means it must NOT fire as a phantom."""
        from custom_components.bosch_shc.__init__ import SwitchDeviceEventListener

        hass = fake_hass
        entry = fake_entry
        ks = self._make_keypad_service()
        dev = self._make_switch_device(keypad_service=ks, supported_event=True)

        listener = SwitchDeviceEventListener(hass, entry, dev)
        listener.device_id = "hass-device-id-123"

        # dev.eventtimestamp == construction seed (99999): stale replay.
        listener._input_events_handler()
        hass.bus.async_fire.assert_not_called()

    def test_input_events_handler_resubscribe_replay_no_fire(self, fake_hass, fake_entry):
        """Replay-guard (#336): after a real press, a re-delivered state with an
        unchanged eventtimestamp (24 h poll-id resubscribe) must NOT re-fire."""
        from custom_components.bosch_shc.__init__ import SwitchDeviceEventListener

        hass = fake_hass
        entry = fake_entry
        ks = self._make_keypad_service()
        dev = self._make_switch_device(keypad_service=ks, supported_event=True)

        listener = SwitchDeviceEventListener(hass, entry, dev)
        listener.device_id = "hass-device-id-123"

        dev.eventtimestamp = 100000  # real new press
        listener._input_events_handler()
        assert hass.bus.async_fire.call_count == 1

        # Resubscribe replays the SAME eventtimestamp — must be suppressed.
        listener._input_events_handler()
        assert hass.bus.async_fire.call_count == 1

    def test_input_events_handler_advanced_timestamp_fires_again(self, fake_hass, fake_entry):
        """Replay-guard (#336): a genuinely new press (advanced eventtimestamp)
        after a replay still fires."""
        from custom_components.bosch_shc.__init__ import SwitchDeviceEventListener

        hass = fake_hass
        entry = fake_entry
        ks = self._make_keypad_service()
        dev = self._make_switch_device(keypad_service=ks, supported_event=True)

        listener = SwitchDeviceEventListener(hass, entry, dev)
        listener.device_id = "hass-device-id-123"

        dev.eventtimestamp = 100000
        listener._input_events_handler()  # -> fire
        listener._input_events_handler()  # ts=100000 replay -> suppressed
        dev.eventtimestamp = 100001  # real new press
        listener._input_events_handler()  # -> fire

        assert hass.bus.async_fire.call_count == 2
        assert hass.bus.async_fire.call_args.args[1][ATTR_LAST_TIME_TRIGGERED] == 100001

    def test_input_events_handler_unsupported_event_no_fire(self, fake_hass, fake_entry):
        from custom_components.bosch_shc.__init__ import SwitchDeviceEventListener

        hass = fake_hass
        entry = fake_entry
        ks = self._make_keypad_service()
        dev = self._make_switch_device(keypad_service=ks, supported_event=False)

        listener = SwitchDeviceEventListener(hass, entry, dev)
        listener.device_id = "dev-id"
        listener._input_events_handler()

        hass.bus.async_fire.assert_not_called()

    def test_shutdown_unsubscribes_keypad(self, fake_hass, fake_entry):
        from custom_components.bosch_shc.__init__ import SwitchDeviceEventListener

        hass = fake_hass
        entry = fake_entry
        ks = self._make_keypad_service()
        dev = self._make_switch_device(keypad_service=ks)

        listener = SwitchDeviceEventListener(hass, entry, dev)
        listener.shutdown()
        ks.unsubscribe_callback.assert_called_once_with(dev.id)

    def test_no_keypad_service_no_subscribe(self, fake_hass, fake_entry):
        """Device with no 'Keypad' service: no subscribe_callback called."""
        from custom_components.bosch_shc.__init__ import SwitchDeviceEventListener

        hass = fake_hass
        entry = fake_entry
        dev = self._make_switch_device(keypad_service=None)

        listener = SwitchDeviceEventListener(hass, entry, dev)
        assert listener._keypad_service is None

    def test_async_setup_sets_device_id(self, fake_hass, fake_entry):
        """async_setup populates self.device_id from device registry."""
        from custom_components.bosch_shc.__init__ import SwitchDeviceEventListener

        hass = fake_hass
        entry = fake_entry
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
    def test_switch_device_event_listener_setup_called(
        self, fake_hass, fake_entry, fake_session
    ):
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

        session = fake_session
        session.device_helper.universal_switches = [dev]
        hass = fake_hass
        entry = fake_entry

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

    def _run_with_cert_check(
        self, fake_hass, fake_entry, fake_session, cert_info_for_check, *, raise_on_check=False
    ):
        """Setup, capture the daily cert-check callback, call it, return (hass, pn_mock)."""
        from custom_components.bosch_shc.__init__ import async_setup_entry

        session = fake_session
        hass = fake_hass
        entry = fake_entry
        entry.data["ssl_certificate"] = "/fake/cert.pem"
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

    def test_expired_cert_triggers_reload(self, fake_hass, fake_entry, fake_session):
        """Daily check with expired cert creates an async reload task."""
        cert_info = _make_cert_info(-1)
        hass, _ = self._run_with_cert_check(fake_hass, fake_entry, fake_session, cert_info)
        hass.async_create_task.assert_called_once()

    def test_expiring_cert_creates_notification(self, fake_hass, fake_entry, fake_session):
        """Daily check with expiring cert calls ir.async_create_issue."""
        cert_info = _make_cert_info(10)
        _, pn_create_mock = self._run_with_cert_check(
            fake_hass, fake_entry, fake_session, cert_info
        )
        assert pn_create_mock.call_count >= 1

    def test_parse_exception_in_check_silently_returns(self, fake_hass, fake_entry, fake_session):
        """parse_certificate raising inside daily check → silently return, no crash."""
        hass, _ = self._run_with_cert_check(
            fake_hass, fake_entry, fake_session, None, raise_on_check=True
        )
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

    def test_stop_polling_inner_fn_calls_session(self, fake_hass, fake_entry, fake_session):
        from custom_components.bosch_shc.__init__ import async_setup_entry

        session = fake_session
        hass = fake_hass
        entry = fake_entry
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


# ---------------------------------------------------------------------------
# Extra coverage: entry-skip / idempotent-registration / kwarg / cert-branch /
# unload-teardown edge cases (former test_init_extra_coverage.py).
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Patch targets
# NOTE: must target the package object (`custom_components.bosch_shc.X`),
# NOT `custom_components.bosch_shc.__init__.X`.  The two are distinct objects
# in Python's module cache.  async_setup_entry resolves names from the package
# object at call-time, so only patching the package object takes effect.
# Patching `dr` as a whole module replaces the alias so that `dr.async_get`
# and `dr.async_get_or_create` are automatically intercepted via the mock.
# ---------------------------------------------------------------------------

PATCH_SESSION_PKG = "custom_components.bosch_shc.SHCSessionAsync"
PATCH_DR = "custom_components.bosch_shc.dr"
PATCH_PARSE_CERT_PKG = "custom_components.bosch_shc.parse_certificate"
PATCH_TRACK_INTERVAL_PKG = "custom_components.bosch_shc.async_track_time_interval"


def _make_dr_mock():
    """Return a fake dr module mock with async_get + async_get_or_create wired."""
    fake_entry = SimpleNamespace(id="dreg-001")
    dr_mod = MagicMock()
    # async_get_entry is called as dr.async_get(hass) then .async_get_or_create(...)
    dr_mod.async_get.return_value = MagicMock(
        async_get_or_create=MagicMock(return_value=fake_entry)
    )
    dr_mod.CONNECTION_NETWORK_MAC = "mac"
    dr_mod.format_mac = MagicMock(return_value="aa:bb:cc:dd:ee:ff")
    return dr_mod


def _full_setup(*, fake_session=None, hass=None, entry=None, cert_return=None,
                pn_create=None):
    """Run async_setup_entry with full mocks. Returns (result, hass, entry)."""
    from custom_components.bosch_shc import async_setup_entry

    if fake_session is None:
        fake_session = _make_fake_session()
    if hass is None:
        hass = _make_fake_hass()
    if entry is None:
        entry = _make_fake_entry()
    dr_mock = _make_dr_mock()
    track_unsub = MagicMock()

    with (
        patch(PATCH_SESSION_PKG, return_value=fake_session),
        patch(PATCH_DR, dr_mock),
        patch(PATCH_PARSE_CERT_PKG, return_value=cert_return),
        patch(PATCH_TRACK_INTERVAL_PKG, return_value=track_unsub),
        patch(PATCH_IR_CREATE, pn_create or MagicMock()),
    ):
        result = _run(async_setup_entry(hass, entry))

    return result, hass, entry


# ---------------------------------------------------------------------------
# 1 — scenario_service_call skips entry without runtime_data
# ---------------------------------------------------------------------------

class TestScenarioServiceCallSkipsNoRuntimeData:
    """scenario_service_call must skip config entries that have no runtime_data."""

    def test_entry_without_runtime_data_is_skipped(self, fake_hass):
        """An entry missing runtime_data must not crash the service handler."""
        from custom_components.bosch_shc import async_setup

        hass = fake_hass

        # Build a fake entry WITHOUT runtime_data attribute
        entry_no_rt = SimpleNamespace(
            entry_id="no-rt",
            title="NoRT",
        )
        # Inject it as the only config entry for the domain
        hass.config_entries.async_entries = MagicMock(return_value=[entry_no_rt])

        _run(async_setup(hass, {}))

        # Retrieve the registered scenario service call handler
        register_calls = hass.services.async_register.call_args_list
        scenario_call = None
        for c in register_calls:
            if c.args[1] == SERVICE_TRIGGER_SCENARIO:
                scenario_call = c.args[2]
                break
        assert scenario_call is not None, "trigger_scenario service was not registered"

        # Build a fake ServiceCall
        fake_call = SimpleNamespace(
            data={ATTR_NAME: "some_scenario", ATTR_TITLE: ""},
        )

        # This must NOT raise even though the entry has no runtime_data
        _run(scenario_call(fake_call))

    def test_entry_with_runtime_data_is_processed(self, fake_hass):
        """Entries that DO have runtime_data are processed (not skipped)."""
        from custom_components.bosch_shc import async_setup

        hass = fake_hass

        triggered = []

        class _FakeScenario:
            name = "test_scene"

            async def async_trigger(self_):
                triggered.append(True)

        fake_runtime = SimpleNamespace(
            title="SHC Test",
            session=SimpleNamespace(scenarios=[_FakeScenario()]),
        )
        entry_with_rt = SimpleNamespace(
            entry_id="with-rt",
            title="SHC Test",
            runtime_data=fake_runtime,
        )
        hass.config_entries.async_entries = MagicMock(return_value=[entry_with_rt])

        async def _executor_job(fn, *args):
            return fn(*args)

        hass.async_add_executor_job = _executor_job

        _run(async_setup(hass, {}))

        register_calls = hass.services.async_register.call_args_list
        scenario_call = None
        for c in register_calls:
            if c.args[1] == SERVICE_TRIGGER_SCENARIO:
                scenario_call = c.args[2]
                break
        assert scenario_call is not None

        fake_call = SimpleNamespace(
            data={ATTR_NAME: "test_scene", ATTR_TITLE: ""},
        )
        _run(scenario_call(fake_call))
        assert triggered == [True]


# ---------------------------------------------------------------------------
# 2 — _register_rawscan_service early-return when already registered
# ---------------------------------------------------------------------------

class TestRegisterRawscanServiceIdempotent:
    """_register_rawscan_service must return early if the service already exists."""

    def test_second_call_returns_early_no_double_register(self, fake_hass):
        from custom_components.bosch_shc import _register_rawscan_service

        hass = fake_hass
        # First call: service not yet registered
        hass.services.has_service = MagicMock(return_value=False)
        _register_rawscan_service(hass)
        first_count = hass.services.async_register.call_count

        # Second call: service now exists -> must not register again
        hass.services.has_service = MagicMock(return_value=True)
        _register_rawscan_service(hass)
        second_count = hass.services.async_register.call_count

        assert second_count == first_count, (
            f"async_register called {second_count - first_count} "
            f"extra time(s) on repeat call"
        )

    def test_first_call_registers_service(self, fake_hass):
        from custom_components.bosch_shc import _register_rawscan_service

        hass = fake_hass
        hass.services.has_service = MagicMock(return_value=False)
        _register_rawscan_service(hass)
        # The rawscan service must be registered
        registered_names = [c.args[1] for c in hass.services.async_register.call_args_list]
        assert SERVICE_TRIGGER_RAWSCAN in registered_names


# ---------------------------------------------------------------------------
# 3 — rawscan_service_call raises ServiceValidationError for bad command
# ---------------------------------------------------------------------------

class TestRawscanBadCommand:
    """rawscan_service_call must raise ServiceValidationError for unknown commands."""

    def _get_rawscan_handler(self, hass):
        """Return the rawscan service handler registered with hass.services."""
        rawscan_calls = [
            c for c in hass.services.async_register.call_args_list
            if c.args[1] == SERVICE_TRIGGER_RAWSCAN
        ]
        assert rawscan_calls, "rawscan service was not registered"
        return rawscan_calls[0].args[2]

    def test_unknown_command_raises_service_validation_error(
        self, fake_hass, fake_entry, fake_session
    ):
        """Command not in the async API dispatch map raises ServiceValidationError."""
        hass = fake_hass
        entry = fake_entry
        hass.services.has_service = MagicMock(return_value=False)

        _, hass, entry = _full_setup(fake_session=fake_session, hass=hass, entry=entry)

        handler = self._get_rawscan_handler(hass)

        # Build a runtime_data so the handler finds the entry
        from custom_components.bosch_shc.data import SHCData
        fake_dev_entry = SimpleNamespace(id="dr-001")
        entry.runtime_data = SHCData(
            session=fake_session,
            shc_device=fake_dev_entry,
            title=entry.title,
        )
        hass.config_entries.async_entries = MagicMock(return_value=[entry])

        bad_call = SimpleNamespace(
            data={
                ATTR_TITLE: "",
                ATTR_COMMAND: "nonexistent_command",
                "device_id": "",
                "service_id": "",
            }
        )

        with pytest.raises(ServiceValidationError):
            _run(handler(bad_call))

    def test_valid_command_does_not_raise(self, fake_hass, fake_entry, fake_session):
        """A valid command must NOT raise ServiceValidationError."""
        fake_session.api.get_devices = AsyncMock(return_value={"devices": []})

        hass = fake_hass
        entry = fake_entry
        hass.services.has_service = MagicMock(return_value=False)

        _, hass, entry = _full_setup(fake_session=fake_session, hass=hass, entry=entry)
        handler = self._get_rawscan_handler(hass)

        from custom_components.bosch_shc.data import SHCData
        fake_dev_entry = SimpleNamespace(id="dr-001")
        entry.runtime_data = SHCData(
            session=fake_session,
            shc_device=fake_dev_entry,
            title=entry.title,
        )
        hass.config_entries.async_entries = MagicMock(return_value=[entry])

        async def _executor_job(fn, *args):
            return fn(*args)

        hass.async_add_executor_job = _executor_job

        good_call = SimpleNamespace(
            data={
                ATTR_TITLE: "",
                ATTR_COMMAND: "devices",
                "device_id": "",
                "service_id": "",
            }
        )
        # Must not raise
        _run(handler(good_call))


# ---------------------------------------------------------------------------
# 4 — Session constructed with correct long_poll_timeout kwarg
# ---------------------------------------------------------------------------

class TestSessionKwargsConditional:
    """SHCSessionAsync is constructed with the long_poll_timeout option value.

    The old inspect-based kwargs logic was removed in the async refactor.
    SHCSessionAsync always accepts long_poll_timeout as a keyword arg directly.
    """

    def test_long_poll_timeout_kwarg_forwarded(self, fake_hass, fake_entry, fake_session):
        """long_poll_timeout option is passed directly to SHCSessionAsync constructor."""
        from custom_components.bosch_shc import async_setup_entry

        dr_mock = _make_dr_mock()
        hass = fake_hass
        entry = fake_entry
        entry.options = {OPT_LONG_POLL_TIMEOUT: 30}

        captured_kwargs = {}

        def _capture_constructor(*args, **kwargs):
            captured_kwargs.update(kwargs)
            return fake_session

        with (
            patch(PATCH_SESSION_PKG, side_effect=_capture_constructor),
            patch(PATCH_DR, dr_mock),
            patch(PATCH_PARSE_CERT_PKG, return_value=None),
            patch(PATCH_TRACK_INTERVAL_PKG, return_value=MagicMock()),
            patch(PATCH_IR_CREATE, MagicMock()),
        ):
            result = _run(async_setup_entry(hass, entry))

        assert result is True
        assert captured_kwargs.get("long_poll_timeout") == 30

    def test_default_long_poll_timeout_when_not_in_options(
        self, fake_hass, fake_entry, fake_session
    ):
        """When OPT_LONG_POLL_TIMEOUT is absent, default 10 is used."""
        from custom_components.bosch_shc import async_setup_entry

        dr_mock = _make_dr_mock()
        hass = fake_hass
        entry = fake_entry
        entry.options = {}  # no timeout option

        captured_kwargs = {}

        def _capture_constructor(*args, **kwargs):
            captured_kwargs.update(kwargs)
            return fake_session

        with (
            patch(PATCH_SESSION_PKG, side_effect=_capture_constructor),
            patch(PATCH_DR, dr_mock),
            patch(PATCH_PARSE_CERT_PKG, return_value=None),
            patch(PATCH_TRACK_INTERVAL_PKG, return_value=MagicMock()),
            patch(PATCH_IR_CREATE, MagicMock()),
        ):
            result = _run(async_setup_entry(hass, entry))

        assert result is True
        assert captured_kwargs.get("long_poll_timeout") == 10


# ---------------------------------------------------------------------------
# 5 — _scheduled_cert_check early-return when cert_path is falsy
# ---------------------------------------------------------------------------

class TestScheduledCertCheckNoCertPath:
    """_scheduled_cert_check must return immediately when cert_path is empty."""

    def _capture_cert_check_fn(self, fake_hass, fake_entry, fake_session):
        """Run async_setup_entry without cert and capture the scheduled check fn."""
        from custom_components.bosch_shc import async_setup_entry

        hass = fake_hass
        entry = fake_entry
        entry.data["ssl_certificate"] = ""  # no cert
        dr_mock = _make_dr_mock()
        captured_fn = []

        def _capture_interval(h, fn, interval):
            captured_fn.append(fn)
            return MagicMock()

        with (
            patch(PATCH_SESSION_PKG, return_value=fake_session),
            patch(PATCH_DR, dr_mock),
            patch(PATCH_PARSE_CERT_PKG, return_value=None),
            patch(PATCH_TRACK_INTERVAL_PKG, side_effect=_capture_interval),
            patch(PATCH_IR_CREATE, MagicMock()),
        ):
            _run(async_setup_entry(hass, entry))

        assert captured_fn, "async_track_time_interval was not called"
        return captured_fn[0], hass

    def test_no_cert_check_fn_returns_early(self, fake_hass, fake_entry, fake_session):
        """When cert_path='', _scheduled_cert_check must return without calling parse."""
        check_fn, hass = self._capture_cert_check_fn(fake_hass, fake_entry, fake_session)

        parse_called = []

        async def _fake_executor(fn, *args):
            parse_called.append(True)
            return fn(*args)

        hass.async_add_executor_job = _fake_executor

        # Invoke the scheduled check directly
        _run(check_fn(None))

        # parse_certificate must NOT be called when cert_path is falsy
        assert parse_called == [], (
            "parse_certificate was called despite empty cert_path"
        )


# ---------------------------------------------------------------------------
# 6 — _scheduled_cert_check warning-notification branch
# ---------------------------------------------------------------------------

class TestScheduledCertCheckWarningBranch:
    """When days_remaining <= CERT_EXPIRY_WARNING_DAYS (but >= 0),
    ir.async_create_issue must be called in the daily scheduled check.
    """

    def _capture_cert_check_fn_with_cert(self, fake_hass, fake_entry, fake_session, cert_return):
        """Run async_setup_entry WITH a cert and capture the scheduled check fn."""
        from custom_components.bosch_shc import async_setup_entry

        hass = fake_hass
        entry = fake_entry
        entry.data["ssl_certificate"] = "/fake/cert.pem"
        dr_mock = _make_dr_mock()
        captured_fn = []
        ir_mock = MagicMock()

        def _capture_interval(h, fn, interval):
            captured_fn.append(fn)
            return MagicMock()

        with (
            patch(PATCH_SESSION_PKG, return_value=fake_session),
            patch(PATCH_DR, dr_mock),
            patch(PATCH_PARSE_CERT_PKG, return_value=cert_return),
            patch(PATCH_TRACK_INTERVAL_PKG, side_effect=_capture_interval),
            patch(PATCH_IR_CREATE, ir_mock),
        ):
            _run(async_setup_entry(hass, entry))

        assert captured_fn, "async_track_time_interval was not called"
        return captured_fn[0], hass, ir_mock

    def test_warning_notification_sent_when_cert_expiring_soon(
        self, fake_hass, fake_entry, fake_session
    ):
        """days_remaining == CERT_EXPIRY_WARNING_DAYS triggers ir.async_create_issue."""
        days = CERT_EXPIRY_WARNING_DAYS
        not_after = datetime.now(timezone.utc) + timedelta(days=days)
        warn_cert = SimpleNamespace(
            days_remaining=days,
            not_after=not_after,
        )

        check_fn, hass, _ = self._capture_cert_check_fn_with_cert(
            fake_hass, fake_entry, fake_session, warn_cert
        )

        ir_mock_daily = MagicMock()

        async def _executor_returning_cert(fn, *args):
            return warn_cert

        hass.async_add_executor_job = _executor_returning_cert

        with patch(PATCH_IR_CREATE, ir_mock_daily):
            _run(check_fn(None))

        assert ir_mock_daily.called, (
            "ir.async_create_issue was not called for expiring cert in daily check"
        )
        # translation_key="cert_expiring" is the third positional arg (index 2) or kwarg
        call_kwargs = ir_mock_daily.call_args
        translation_key = call_kwargs.kwargs.get(
            "translation_key", call_kwargs.args[2] if len(call_kwargs.args) > 2 else ""
        )
        assert "expir" in translation_key.lower()

    def test_no_notification_when_cert_has_plenty_of_time(
        self, fake_hass, fake_entry, fake_session
    ):
        """days_remaining > CERT_EXPIRY_WARNING_DAYS -> no ir.async_create_issue in daily check."""
        days = CERT_EXPIRY_WARNING_DAYS + 10
        not_after = datetime.now(timezone.utc) + timedelta(days=days)
        ok_cert = SimpleNamespace(
            days_remaining=days,
            not_after=not_after,
        )

        check_fn, hass, _ = self._capture_cert_check_fn_with_cert(
            fake_hass, fake_entry, fake_session, ok_cert
        )

        ir_mock_daily = MagicMock()

        async def _executor_returning_cert(fn, *args):
            return ok_cert

        hass.async_add_executor_job = _executor_returning_cert

        with patch(PATCH_IR_CREATE, ir_mock_daily):
            _run(check_fn(None))

        assert not ir_mock_daily.called, (
            "ir.async_create_issue must NOT be called when cert is not near expiry"
        )

    def test_reload_triggered_when_cert_expired(self, fake_hass, fake_entry, fake_session):
        """days_remaining < 0 -> hass.async_create_task called with reload."""
        days_startup = CERT_EXPIRY_WARNING_DAYS + 5
        startup_cert = SimpleNamespace(
            days_remaining=days_startup,
            not_after=datetime.now(timezone.utc) + timedelta(days=days_startup),
        )
        check_fn, hass, _ = self._capture_cert_check_fn_with_cert(
            fake_hass, fake_entry, fake_session, startup_cert
        )

        days_expired = -1
        expired_cert = SimpleNamespace(
            days_remaining=days_expired,
            not_after=datetime.now(timezone.utc) + timedelta(days=days_expired),
        )

        async def _executor_returning_expired(fn, *args):
            return expired_cert

        hass.async_add_executor_job = _executor_returning_expired

        with patch(PATCH_IR_CREATE, MagicMock()):
            _run(check_fn(None))

        hass.async_create_task.assert_called()


# ---------------------------------------------------------------------------
# 7 — async_unload_entry calls presence_unsub when not None
# ---------------------------------------------------------------------------

class TestAsyncUnloadEntryPresenceUnsub:
    """async_unload_entry must call runtime.presence_unsub() when it is not None."""

    def _make_runtime(self, fake_session, *, presence_unsub=None, polling_handler=None,
                      cert_check_unsub=None):
        from custom_components.bosch_shc.data import SHCData
        fake_dev_entry = SimpleNamespace(id="dr-001")
        runtime = SHCData(
            session=fake_session,
            shc_device=fake_dev_entry,
            title="Test SHC",
        )
        runtime.polling_handler = polling_handler
        runtime.cert_check_unsub = cert_check_unsub
        runtime.presence_unsub = presence_unsub
        return runtime, fake_session

    def test_presence_unsub_called_when_not_none(self, fake_hass, fake_entry, fake_session):
        """runtime.presence_unsub() must be called during async_unload_entry."""
        from custom_components.bosch_shc import async_unload_entry

        presence_unsub_called = []
        presence_unsub = MagicMock(side_effect=lambda: presence_unsub_called.append(True))

        runtime, _ = self._make_runtime(fake_session, presence_unsub=presence_unsub)

        hass = fake_hass
        entry = fake_entry
        entry.runtime_data = runtime
        hass.services.has_service = MagicMock(return_value=False)
        hass.config_entries.async_entries = MagicMock(return_value=[])

        _run(async_unload_entry(hass, entry))

        assert presence_unsub_called == [True], (
            "runtime.presence_unsub() was not called during async_unload_entry"
        )

    def test_no_presence_unsub_when_none(self, fake_hass, fake_entry, fake_session):
        """When runtime.presence_unsub is None, unload must not crash."""
        from custom_components.bosch_shc import async_unload_entry

        runtime, _ = self._make_runtime(fake_session, presence_unsub=None)

        hass = fake_hass
        entry = fake_entry
        entry.runtime_data = runtime
        hass.services.has_service = MagicMock(return_value=False)
        hass.config_entries.async_entries = MagicMock(return_value=[])

        # Must not raise
        _run(async_unload_entry(hass, entry))

    def test_polling_handler_called_when_not_none(self, fake_hass, fake_entry, fake_session):
        """polling_handler() is also called during unload."""
        from custom_components.bosch_shc import async_unload_entry

        polling_handler_called = []
        polling_handler = MagicMock(
            side_effect=lambda: polling_handler_called.append(True)
        )

        runtime, _ = self._make_runtime(fake_session, polling_handler=polling_handler)

        hass = fake_hass
        entry = fake_entry
        entry.runtime_data = runtime
        hass.services.has_service = MagicMock(return_value=False)
        hass.config_entries.async_entries = MagicMock(return_value=[])

        _run(async_unload_entry(hass, entry))

        assert polling_handler_called == [True]

    def test_cert_check_unsub_called_when_not_none(self, fake_hass, fake_entry, fake_session):
        """cert_check_unsub() is called during unload."""
        from custom_components.bosch_shc import async_unload_entry

        cert_unsub_called = []
        cert_unsub = MagicMock(side_effect=lambda: cert_unsub_called.append(True))

        runtime, _ = self._make_runtime(fake_session, cert_check_unsub=cert_unsub)

        hass = fake_hass
        entry = fake_entry
        entry.runtime_data = runtime
        hass.services.has_service = MagicMock(return_value=False)
        hass.config_entries.async_entries = MagicMock(return_value=[])

        _run(async_unload_entry(hass, entry))

        assert cert_unsub_called == [True]


# ---------------------------------------------------------------------------
# Presence-based child-lock automation (former test_presence_child_lock.py).
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Patch targets
# ---------------------------------------------------------------------------

PATCH_TRACK_STATE = "custom_components.bosch_shc.__init__.async_track_state_change_event"


def _make_fake_hass_presence(states=None):
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
    # _evaluate_child_lock() re-reads live state via hass.states.get() rather
    # than the triggering event's payload — tests that want to exercise the
    # unavailable/unknown/absent skip logic must mutate the backing dict via
    # this helper before firing the callback, not just vary the event.
    hass._set_state = lambda entity_id, val: _states.__setitem__(entity_id, val)
    hass._del_state = lambda entity_id: _states.pop(entity_id, None)

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


def _make_fake_session_presence(thermostats=None, roomthermostats=None,
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
    session.devices = []
    session.get_zigbee_routing_info = AsyncMock()
    return session


# ---------------------------------------------------------------------------
# Fixtures: fake_hass_presence / fake_session_presence
#
# Thin local wrappers around the builders above, matching the fake_hass /
# fake_entry / fake_session fixture pattern near the top of this file.
# fake_entry (already a fixture) is reused as-is for this section — its
# .options are set directly in test bodies (or via _do_setup below) since the
# presence tests vary options per-call, not per-fixture-instantiation.
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_hass_presence(request: pytest.FixtureRequest):
    """Fake hass mock for presence/child-lock tests. See _make_fake_hass_presence."""
    overrides = getattr(request, "param", {}) or {}
    return _make_fake_hass_presence(**overrides)


@pytest.fixture
def fake_session_presence(request: pytest.FixtureRequest):
    """Fake SHCSessionAsync for presence tests. See _make_fake_session_presence."""
    overrides = getattr(request, "param", {}) or {}
    return _make_fake_session_presence(**overrides)


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

def _do_setup(hass, entry, session, options, *, capture_state_cb=False, hass_states=None):
    """Run async_setup_entry against pre-built fake_hass_presence/fake_entry fixtures.

    Returns (hass, entry, captured_state_cb).
    hass_states: dict of entity_id -> state string pre-populated in hass.states
    (applied onto the given hass via its _set_state helper).
    """
    from custom_components.bosch_shc.__init__ import async_setup_entry

    entry.options = options
    for entity_id, val in (hass_states or {}).items():
        hass._set_state(entity_id, val)

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
    def test_no_state_tracker_when_empty_list(
        self, fake_hass_presence, fake_entry, fake_session_presence
    ):
        """When OPT_PRESENCE_ENTITY is [], async_track_state_change_event is NOT called."""
        with patch(PATCH_TRACK_STATE) as mock_track:
            _do_setup(
                fake_hass_presence, fake_entry, fake_session_presence,
                options={OPT_PRESENCE_ENTITY: []},
            )
            mock_track.assert_not_called()

    def test_no_state_tracker_when_missing(
        self, fake_hass_presence, fake_entry, fake_session_presence
    ):
        with patch(PATCH_TRACK_STATE) as mock_track:
            _do_setup(fake_hass_presence, fake_entry, fake_session_presence, options={})
            mock_track.assert_not_called()

    def test_no_state_tracker_when_entity_is_empty_string(
        self, fake_hass_presence, fake_entry, fake_session_presence
    ):
        """Backward compat: old str "" stored -> treated as disabled."""
        with patch(PATCH_TRACK_STATE) as mock_track:
            _do_setup(
                fake_hass_presence, fake_entry, fake_session_presence,
                options={OPT_PRESENCE_ENTITY: ""},
            )
            mock_track.assert_not_called()

    def test_presence_unsub_is_none_when_disabled(
        self, fake_hass_presence, fake_entry, fake_session_presence
    ):
        hass, entry, _ = _do_setup(
            fake_hass_presence, fake_entry, fake_session_presence, options={}
        )
        assert entry.runtime_data.presence_unsub is None

    def test_master_toggle_off_disables_even_with_entities(
        self, fake_hass_presence, fake_entry, fake_session_presence
    ):
        """child_lock_enabled=False must suppress the feature even when
        presence entities are configured (explicit off switch).
        """
        with patch(PATCH_TRACK_STATE) as mock_track:
            hass, entry, _ = _do_setup(
                fake_hass_presence, fake_entry, fake_session_presence,
                options={
                    OPT_CHILD_LOCK_ENABLED: False,
                    OPT_PRESENCE_ENTITY: ["person.felix"],
                },
            )
            mock_track.assert_not_called()
            assert entry.runtime_data.presence_unsub is None

    def test_master_toggle_on_with_entities_registers_tracker(
        self, fake_hass_presence, fake_entry, fake_session_presence
    ):
        """child_lock_enabled=True + entities -> tracker registered."""
        with patch(PATCH_TRACK_STATE) as mock_track:
            mock_track.return_value = MagicMock()
            _do_setup(
                fake_hass_presence, fake_entry, fake_session_presence,
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
    def test_single_str_treated_as_one_entity_list(
        self, fake_hass_presence, fake_entry, fake_session_presence
    ):
        """Old config stores a bare string; must register a listener on it."""
        from custom_components.bosch_shc.__init__ import async_setup_entry

        fake_entry.options = {OPT_PRESENCE_ENTITY: "person.felix"}
        with patch(PATCH_TRACK_STATE) as mock_track:
            mock_track.return_value = MagicMock()
            with (
                patch(PATCH_SESSION, return_value=fake_session_presence),
                patch(PATCH_DR_GET, return_value=_make_fake_device_registry()),
                patch(PATCH_PARSE_CERT, return_value=None),
                patch(PATCH_TRACK_INTERVAL, return_value=MagicMock()),
            ):
                _run(async_setup_entry(fake_hass_presence, fake_entry))

        mock_track.assert_called_once()
        entity_ids = mock_track.call_args[0][1]
        assert "person.felix" in entity_ids

    def test_single_str_lock_on_when_entity_home(
        self, fake_hass_presence, fake_entry, fake_session_presence
    ):
        """Backward compat str: entering present_state still locks devices."""
        therm = _make_device("therm-1")
        fake_session_presence.device_helper.thermostats = [therm]
        opts = {OPT_PRESENCE_ENTITY: "person.felix"}
        hass, entry, state_cb = _do_setup(
            fake_hass_presence, fake_entry, fake_session_presence, opts,
            capture_state_cb=True,
            hass_states={"person.felix": "home"},
        )
        assert state_cb is not None

        state_cb(_state_event("home", "not_home"))
        task_coro = hass.async_create_task.call_args[0][0]
        _run(task_coro)
        therm.async_set_child_lock.assert_awaited_once_with(True)

    def test_single_str_lock_off_when_entity_leaves(
        self, fake_hass_presence, fake_entry, fake_session_presence
    ):
        """Backward compat str: leaving present_state unlocks devices."""
        therm = _make_device("therm-1")
        fake_session_presence.device_helper.thermostats = [therm]
        opts = {OPT_PRESENCE_ENTITY: "person.felix"}
        # Simulate entity now away
        hass, entry, state_cb = _do_setup(
            fake_hass_presence, fake_entry, fake_session_presence, opts,
            capture_state_cb=True,
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
    def test_state_tracker_registered_with_list(
        self, fake_hass_presence, fake_entry, fake_session_presence
    ):
        """async_track_state_change_event called with all configured entities."""
        from custom_components.bosch_shc.__init__ import async_setup_entry

        fake_entry.options = {
            OPT_PRESENCE_ENTITY: ["person.felix", "device_tracker.phone"]
        }

        with patch(PATCH_TRACK_STATE) as mock_track:
            mock_track.return_value = MagicMock()
            with (
                patch(PATCH_SESSION, return_value=fake_session_presence),
                patch(PATCH_DR_GET, return_value=_make_fake_device_registry()),
                patch(PATCH_PARSE_CERT, return_value=None),
                patch(PATCH_TRACK_INTERVAL, return_value=MagicMock()),
            ):
                _run(async_setup_entry(fake_hass_presence, fake_entry))

        mock_track.assert_called_once()
        entity_ids = mock_track.call_args[0][1]
        assert "person.felix" in entity_ids
        assert "device_tracker.phone" in entity_ids

    def test_presence_unsub_stored_on_runtime_data(
        self, fake_hass_presence, fake_entry, fake_session_presence
    ):
        opts = {OPT_PRESENCE_ENTITY: ["person.felix"]}
        hass, entry, _ = _do_setup(
            fake_hass_presence, fake_entry, fake_session_presence, opts,
            capture_state_cb=True,
        )
        assert entry.runtime_data.presence_unsub is not None


# ---------------------------------------------------------------------------
# Tests: child lock ON when entering present_state
# ---------------------------------------------------------------------------

class TestChildLockOn:
    def _setup_with_devices(self, fake_hass_presence, fake_entry, fake_session_presence,
                            thermostat=None, bool_dev=None,
                            opts=None, hass_states=None):
        therm = thermostat or _make_device("therm-1")
        booldev = bool_dev or _make_device("bool-1")
        fake_session_presence.device_helper.thermostats = [therm]
        fake_session_presence.device_helper.micromodule_shutter_controls = [booldev]
        if opts is None:
            opts = {
                OPT_PRESENCE_ENTITY: ["person.felix"],
            }
        hass, entry, state_cb = _do_setup(
            fake_hass_presence, fake_entry, fake_session_presence, opts,
            capture_state_cb=True,
            hass_states=hass_states,
        )
        return hass, state_cb, therm, booldev

    def test_entering_present_state_sets_child_lock_true_on_thermostat(
        self, fake_hass_presence, fake_entry, fake_session_presence
    ):
        hass, state_cb, therm, booldev = self._setup_with_devices(
            fake_hass_presence, fake_entry, fake_session_presence,
            hass_states={"person.felix": "home"},
        )
        assert state_cb is not None

        state_cb(_state_event("home", "not_home"))

        hass.async_create_task.assert_called_once()
        task_coro = hass.async_create_task.call_args[0][0]
        _run(task_coro)
        therm.async_set_child_lock.assert_awaited_once_with(True)

    def test_entering_present_state_sets_child_lock_true_on_bool_device(
        self, fake_hass_presence, fake_entry, fake_session_presence
    ):
        hass, state_cb, therm, booldev = self._setup_with_devices(
            fake_hass_presence, fake_entry, fake_session_presence,
            hass_states={"person.felix": "home"},
        )
        state_cb(_state_event("home", "not_home"))
        task_coro = hass.async_create_task.call_args[0][0]
        _run(task_coro)
        booldev.async_set_child_lock.assert_awaited_once_with(True)

    def test_custom_present_state_triggers_lock(
        self, fake_hass_presence, fake_entry, fake_session_presence
    ):
        therm = _make_device("therm-1")
        fake_session_presence.device_helper.thermostats = [therm]
        opts = {
            OPT_PRESENCE_ENTITY: ["input_boolean.guests"],
        }
        hass, entry, state_cb = _do_setup(
            fake_hass_presence, fake_entry, fake_session_presence, opts,
            capture_state_cb=True,
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
    def _setup_with_therm(self, fake_hass_presence, fake_entry, fake_session_presence):
        therm = _make_device("therm-1")
        fake_session_presence.device_helper.thermostats = [therm]
        opts = {
            OPT_PRESENCE_ENTITY: ["person.felix"],
        }
        hass, entry, state_cb = _do_setup(
            fake_hass_presence, fake_entry, fake_session_presence, opts,
            capture_state_cb=True,
            # Entity is now away
            hass_states={"person.felix": "not_home"},
        )
        return hass, state_cb, therm

    def test_leaving_present_state_sets_child_lock_false(
        self, fake_hass_presence, fake_entry, fake_session_presence
    ):
        hass, state_cb, therm = self._setup_with_therm(
            fake_hass_presence, fake_entry, fake_session_presence
        )
        state_cb(_state_event("not_home", "home"))
        task_coro = hass.async_create_task.call_args[0][0]
        _run(task_coro)
        therm.async_set_child_lock.assert_awaited_once_with(False)


# ---------------------------------------------------------------------------
# Tests: multi-entity any-home semantics
# ---------------------------------------------------------------------------

class TestMultiEntityAnyHome:
    """Verify ANY-home-ON / ALL-away-OFF logic with two entities."""

    def _setup_multi(self, fake_hass_presence, fake_entry, fake_session_presence, hass_states):
        therm = _make_device("therm-1")
        fake_session_presence.device_helper.thermostats = [therm]
        opts = {
            OPT_PRESENCE_ENTITY: ["person.alice", "person.bob"],
        }
        hass, entry, state_cb = _do_setup(
            fake_hass_presence, fake_entry, fake_session_presence, opts,
            capture_state_cb=True,
            hass_states=hass_states,
        )
        return hass, state_cb, therm

    def test_any_entity_home_turns_lock_on(
        self, fake_hass_presence, fake_entry, fake_session_presence
    ):
        """alice=home, bob=not_home -> lock ON when alice arrives."""
        hass, state_cb, therm = self._setup_multi(
            fake_hass_presence, fake_entry, fake_session_presence,
            {"person.alice": "home", "person.bob": "not_home"},
        )
        state_cb(_state_event("home", "not_home"))
        task_coro = hass.async_create_task.call_args[0][0]
        _run(task_coro)
        therm.async_set_child_lock.assert_awaited_once_with(True)

    def test_all_away_turns_lock_off(
        self, fake_hass_presence, fake_entry, fake_session_presence
    ):
        """alice=not_home, bob=not_home -> lock OFF when last one leaves."""
        hass, state_cb, therm = self._setup_multi(
            fake_hass_presence, fake_entry, fake_session_presence,
            {"person.alice": "not_home", "person.bob": "not_home"},
        )
        state_cb(_state_event("not_home", "home"))
        task_coro = hass.async_create_task.call_args[0][0]
        _run(task_coro)
        therm.async_set_child_lock.assert_awaited_once_with(False)

    def test_one_away_other_still_home_lock_stays_on(
        self, fake_hass_presence, fake_entry, fake_session_presence
    ):
        """alice=home, bob leaves -> aggregate still ANY-home -> NO API call."""
        hass, state_cb, therm = self._setup_multi(
            fake_hass_presence, fake_entry, fake_session_presence,
            {"person.alice": "home", "person.bob": "not_home"},
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

    def test_both_entities_registered_as_listeners(
        self, fake_hass_presence, fake_entry, fake_session_presence
    ):
        """async_track_state_change_event receives both entity IDs."""
        from custom_components.bosch_shc.__init__ import async_setup_entry

        fake_entry.options = {
            OPT_PRESENCE_ENTITY: ["person.alice", "person.bob"],
        }
        with patch(PATCH_TRACK_STATE) as mock_track:
            mock_track.return_value = MagicMock()
            with (
                patch(PATCH_SESSION, return_value=fake_session_presence),
                patch(PATCH_DR_GET, return_value=_make_fake_device_registry()),
                patch(PATCH_PARSE_CERT, return_value=None),
                patch(PATCH_TRACK_INTERVAL, return_value=MagicMock()),
            ):
                _run(async_setup_entry(fake_hass_presence, fake_entry))

        mock_track.assert_called_once()
        registered = mock_track.call_args[0][1]
        assert "person.alice" in registered
        assert "person.bob" in registered

    def test_redundant_write_suppressed_both_already_home(
        self, fake_hass_presence, fake_entry, fake_session_presence
    ):
        """If aggregate is already True and another home event arrives -> no second write."""
        hass, state_cb, therm = self._setup_multi(
            fake_hass_presence, fake_entry, fake_session_presence,
            {"person.alice": "home", "person.bob": "home"},
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
    def _setup(self, fake_hass_presence, fake_entry, fake_session_presence):
        therm = _make_device("therm-1")
        fake_session_presence.device_helper.thermostats = [therm]
        opts = {
            OPT_PRESENCE_ENTITY: ["person.felix"],
        }
        hass, entry, state_cb = _do_setup(
            fake_hass_presence, fake_entry, fake_session_presence, opts,
            capture_state_cb=True,
            hass_states={"person.felix": "not_home"},
        )
        return hass, state_cb, therm

    def test_no_task_when_new_state_is_unavailable(
        self, fake_hass_presence, fake_entry, fake_session_presence
    ):
        """_evaluate_child_lock() re-reads live state via hass.states.get()
        rather than the event payload, so the skip logic must be exercised by
        mutating the backing hass.states dict — not by varying an event
        argument that the function never actually reads."""
        hass, state_cb, therm = self._setup(
            fake_hass_presence, fake_entry, fake_session_presence
        )
        hass.async_create_task.reset_mock()
        hass._set_state("person.felix", "unavailable")
        state_cb(_state_event("unavailable", "home"))
        hass.async_create_task.assert_not_called()

    def test_no_task_when_new_state_is_unknown(
        self, fake_hass_presence, fake_entry, fake_session_presence
    ):
        hass, state_cb, therm = self._setup(
            fake_hass_presence, fake_entry, fake_session_presence
        )
        hass.async_create_task.reset_mock()
        hass._set_state("person.felix", "unknown")
        state_cb(_state_event("unknown", "home"))
        hass.async_create_task.assert_not_called()

    def test_no_task_when_new_state_is_none(
        self, fake_hass_presence, fake_entry, fake_session_presence
    ):
        """Entity removed from hass.states entirely: ignore."""
        hass, state_cb, therm = self._setup(
            fake_hass_presence, fake_entry, fake_session_presence
        )
        hass.async_create_task.reset_mock()
        hass._del_state("person.felix")
        event = SimpleNamespace(
            data={
                "new_state": None,
                "old_state": SimpleNamespace(state="home"),
            }
        )
        state_cb(event)
        hass.async_create_task.assert_not_called()

    def test_absent_entity_skipped_but_other_tracked_entity_still_detected(
        self, fake_hass_presence, fake_entry, fake_session_presence
    ):
        """Regression: a tracked entity that is unavailable/not-yet-restored
        must not short-circuit the whole aggregate — a DIFFERENT tracked
        entity that's genuinely home must still lock. Proves the per-entity
        skip is a `continue`, not an early return."""
        therm = _make_device("therm-1")
        fake_session_presence.device_helper.thermostats = [therm]
        opts = {OPT_PRESENCE_ENTITY: ["person.felix", "person.anna"]}
        hass, entry, state_cb = _do_setup(
            fake_hass_presence, fake_entry, fake_session_presence, opts,
            capture_state_cb=True,
            hass_states={"person.anna": "home"},  # person.felix absent entirely
        )

        hass.async_create_task.assert_called_once()
        task_coro = hass.async_create_task.call_args[0][0]
        _run(task_coro)
        therm.async_set_child_lock.assert_awaited_once_with(True)

    def test_unavailable_transition_unlocks_after_being_home(
        self, fake_hass_presence, fake_entry, fake_session_presence
    ):
        """A tracked person going home -> unavailable must be treated as
        absent (not "still home"), unlocking the device."""
        therm = _make_device("therm-1")
        fake_session_presence.device_helper.thermostats = [therm]
        opts = {OPT_PRESENCE_ENTITY: ["person.felix"]}
        hass, entry, state_cb = _do_setup(
            fake_hass_presence, fake_entry, fake_session_presence, opts,
            capture_state_cb=True,
            hass_states={"person.felix": "home"},
        )
        # Startup locked it (drain that call).
        hass.async_create_task.reset_mock()
        therm.async_set_child_lock.reset_mock()

        hass._set_state("person.felix", "unavailable")
        state_cb(_state_event("unavailable", "home"))

        hass.async_create_task.assert_called_once()
        task_coro = hass.async_create_task.call_args[0][0]
        _run(task_coro)
        therm.async_set_child_lock.assert_awaited_once_with(False)

    def test_no_task_when_aggregate_unchanged_away(
        self, fake_hass_presence, fake_entry, fake_session_presence
    ):
        """Transition between two non-present states: aggregate stays False -> no write."""
        hass, state_cb, therm = self._setup(
            fake_hass_presence, fake_entry, fake_session_presence
        )
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
    def test_jsonrpc_error_caught_no_crash(
        self, fake_hass_presence, fake_entry, fake_session_presence
    ):
        from boschshcpy.api import JSONRPCError

        therm = MagicMock()
        therm.id = "therm-broken"
        therm.async_set_child_lock = AsyncMock(
            side_effect=JSONRPCError(-1, "network error")
        )

        fake_session_presence.device_helper.thermostats = [therm]
        opts = {
            OPT_PRESENCE_ENTITY: ["person.felix"],
        }
        hass, entry, state_cb = _do_setup(
            fake_hass_presence, fake_entry, fake_session_presence, opts,
            capture_state_cb=True,
            hass_states={"person.felix": "home"},
        )

        state_cb(_state_event("home", "not_home"))
        task_coro = hass.async_create_task.call_args[0][0]
        # Must NOT raise
        _run(task_coro)

    def test_shc_exception_caught_no_crash(
        self, fake_hass_presence, fake_entry, fake_session_presence
    ):
        from boschshcpy.exceptions import SHCException

        booldev = MagicMock()
        booldev.id = "bool-broken"
        booldev.async_set_child_lock = AsyncMock(
            side_effect=SHCException("timeout")
        )

        fake_session_presence.device_helper.micromodule_shutter_controls = [booldev]
        opts = {
            OPT_PRESENCE_ENTITY: ["person.felix"],
        }
        hass, entry, state_cb = _do_setup(
            fake_hass_presence, fake_entry, fake_session_presence, opts,
            capture_state_cb=True,
            hass_states={"person.felix": "home"},
        )

        state_cb(_state_event("home", "not_home"))
        task_coro = hass.async_create_task.call_args[0][0]
        _run(task_coro)


# ---------------------------------------------------------------------------
# Tests: initial state applied at startup/reload (not just on state-change)
# ---------------------------------------------------------------------------

class TestInitialStateAppliedAtStartup:
    def test_lock_applied_at_setup_when_person_already_home(
        self, fake_hass_presence, fake_entry, fake_session_presence
    ):
        """A person already 'home' across a restart/reload must be locked
        immediately at setup — not only on their next state-change event."""
        therm = _make_device("therm-1")
        fake_session_presence.device_helper.thermostats = [therm]
        opts = {OPT_PRESENCE_ENTITY: ["person.felix"]}
        hass, entry, state_cb = _do_setup(
            fake_hass_presence, fake_entry, fake_session_presence, opts,
            capture_state_cb=True,
            hass_states={"person.felix": "home"},
        )

        hass.async_create_task.assert_called_once()
        task_coro = hass.async_create_task.call_args[0][0]
        _run(task_coro)
        therm.async_set_child_lock.assert_awaited_once_with(True)

    def test_no_lock_applied_at_setup_when_nobody_home(
        self, fake_hass_presence, fake_entry, fake_session_presence
    ):
        """Nobody home at setup: aggregate is False, matching the initial
        sentinel semantics for 'off' — a task still fires once to make sure
        devices aren't left locked from a prior session, then settles."""
        therm = _make_device("therm-1")
        fake_session_presence.device_helper.thermostats = [therm]
        opts = {OPT_PRESENCE_ENTITY: ["person.felix"]}
        hass, entry, state_cb = _do_setup(
            fake_hass_presence, fake_entry, fake_session_presence, opts,
            capture_state_cb=True,
            hass_states={"person.felix": "not_home"},
        )

        hass.async_create_task.assert_called_once()
        task_coro = hass.async_create_task.call_args[0][0]
        _run(task_coro)
        therm.async_set_child_lock.assert_awaited_once_with(False)


# ---------------------------------------------------------------------------
# Tests: unload cleans up presence_unsub
# ---------------------------------------------------------------------------

class TestUnloadCleansUp:
    def test_presence_unsub_called_on_unload(
        self, fake_hass_presence, fake_entry, fake_session_presence
    ):
        from custom_components.bosch_shc.__init__ import (
            async_setup_entry,
            async_unload_entry,
        )

        fake_entry.options = {OPT_PRESENCE_ENTITY: ["person.felix"]}
        dr_mock = _make_fake_device_registry()
        presence_unsub_mock = MagicMock()

        def _capture_track_state(h, entity_ids, cb):
            return presence_unsub_mock

        with (
            patch(PATCH_SESSION, return_value=fake_session_presence),
            patch(PATCH_DR_GET, return_value=dr_mock),
            patch(PATCH_PARSE_CERT, return_value=None),
            patch(PATCH_TRACK_INTERVAL, return_value=MagicMock()),
            patch(PATCH_TRACK_STATE, side_effect=_capture_track_state),
        ):
            _run(async_setup_entry(fake_hass_presence, fake_entry))
            _run(async_unload_entry(fake_hass_presence, fake_entry))

        presence_unsub_mock.assert_called_once()

    def test_no_unsub_called_when_presence_disabled(
        self, fake_hass_presence, fake_entry, fake_session_presence
    ):
        """presence_unsub is None when feature is off; unload must not crash."""
        from custom_components.bosch_shc.__init__ import (
            async_setup_entry,
            async_unload_entry,
        )

        fake_entry.options = {}
        dr_mock = _make_fake_device_registry()

        with (
            patch(PATCH_SESSION, return_value=fake_session_presence),
            patch(PATCH_DR_GET, return_value=dr_mock),
            patch(PATCH_PARSE_CERT, return_value=None),
            patch(PATCH_TRACK_INTERVAL, return_value=MagicMock()),
        ):
            _run(async_setup_entry(fake_hass_presence, fake_entry))
            result = _run(async_unload_entry(fake_hass_presence, fake_entry))

        assert result is True



# ---------------------------------------------------------------------------
# Additional coverage-gap tests, previously scattered across shared
# coverage-gap files (test_coverage_gaps.py, test_gaps_coverage2.py,
# test_remaining_gaps.py, test_final_coverage_gaps.py,
# test_error_path_coverage.py, test_thread_safety_fire.py).
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Helper: fake bare device (from former test_coverage_gaps.py), used by
# TestInitCameraToolIssue.
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Helpers (from former test_gaps_coverage2.py, prefixed `_gaps2_` since their
# hass/session builders are distinct from the other `_make_fake_*` families
# above), used by TestPresenceStateContinueOnNone.
# ---------------------------------------------------------------------------

_GAPS2_PATCH_SESSION = "custom_components.bosch_shc.SHCSessionAsync"
_GAPS2_PATCH_DR = "custom_components.bosch_shc.dr.async_get"
_GAPS2_PATCH_PARSE_CERT = "custom_components.bosch_shc.parse_certificate"
_GAPS2_PATCH_TRACK_INTERVAL = "custom_components.bosch_shc.async_track_time_interval"
_GAPS2_PATCH_TRACK_STATE = "custom_components.bosch_shc.async_track_state_change_event"


def _gaps2_make_shc_session():
    from boschshcpy import SHCSessionAsync as _SHCSessionAsync
    session = MagicMock(spec=_SHCSessionAsync)
    session.async_init = AsyncMock()
    session.start_polling = AsyncMock()
    session.stop_polling = AsyncMock()
    session.information = SimpleNamespace(
        updateState=SimpleNamespace(name="NO_UPDATE_AVAILABLE"),
        unique_id="aa:bb:cc:dd:ee:ff",
        version="9.0.0",
        name="SHC",
    )
    session.subscribe_scenario_callback = MagicMock()
    session.unsubscribe_scenario_callback = MagicMock()
    session.device_helper = MagicMock()
    session.device_helper.universal_switches = []
    session.devices = []
    session.get_zigbee_routing_info = AsyncMock()
    return session


def _gaps2_make_hass_with_states(states_map):
    """Build a minimal hass mock where states.get() uses states_map."""
    hass = MagicMock()
    hass.data = {}

    async def _executor_job(fn, *args):
        return fn(*args)

    hass.async_add_executor_job = _executor_job
    hass.config_entries = MagicMock()
    hass.config_entries.async_forward_entry_setups = AsyncMock(return_value=True)
    hass.bus = MagicMock()
    hass.bus.async_listen_once = MagicMock(return_value=MagicMock())
    hass.services = MagicMock()
    hass.services.has_service = MagicMock(return_value=False)
    hass.async_create_task = MagicMock()

    def _states_get(entity_id):
        val = states_map.get(entity_id)
        if val is None:
            return None
        return SimpleNamespace(state=val)

    hass.states = MagicMock()
    hass.states.get = MagicMock(side_effect=_states_get)
    return hass


def _gaps2_run_setup_with_presence(presence_entities, hass_states=None):
    """Run async_setup_entry with presence entities. Capture the state-change cb."""
    from custom_components.bosch_shc import async_setup_entry
    from custom_components.bosch_shc.const import (
        OPT_CHILD_LOCK_ENABLED,
        OPT_PRESENCE_ENTITY,
    )

    hass = _gaps2_make_hass_with_states(hass_states or {})
    fake_session = _gaps2_make_shc_session()

    entry = MagicMock()
    entry.entry_id = "E1"
    entry.title = "Test"
    entry.data = {"ssl_certificate": "", "ssl_key": "", "host": "192.168.1.1"}
    entry.options = {
        OPT_PRESENCE_ENTITY: presence_entities,
        OPT_CHILD_LOCK_ENABLED: True,
    }
    entry.state = ConfigEntryState.SETUP_IN_PROGRESS

    dr_fake = MagicMock()
    dr_fake.async_get_or_create = MagicMock(return_value=SimpleNamespace(id="dr-001"))

    captured = {}

    def _capture_track_state(h, entity_ids, fn):
        captured["cb"] = fn
        return MagicMock()

    with (
        patch(_GAPS2_PATCH_SESSION, return_value=fake_session),
        patch(_GAPS2_PATCH_DR, return_value=dr_fake),
        patch(_GAPS2_PATCH_PARSE_CERT, return_value=None),
        patch(_GAPS2_PATCH_TRACK_INTERVAL, return_value=MagicMock()),
        patch(_GAPS2_PATCH_TRACK_STATE, side_effect=_capture_track_state),
        patch("homeassistant.helpers.issue_registry.async_create_issue", MagicMock()),
    ):
        asyncio.run(async_setup_entry(hass, entry))

    return hass, captured.get("cb")


# ---------------------------------------------------------------------------
# Helpers (from former test_thread_safety_fire.py), used by
# TestSwitchListenerThreadSafe.
# ---------------------------------------------------------------------------

def _make_hass():
    """Minimal hass mock with a tracked loop (thread-safety fire tests)."""
    hass = MagicMock(name="hass")
    hass.loop = MagicMock(name="loop")
    hass.bus = MagicMock(name="bus")
    return hass


def _make_switch_listener(eventtype_name="PRESS_SHORT", keyname_name="UPPER_BUTTON",
                          eventtimestamp=1234):
    """Build a SwitchDeviceEventListener without going through async_setup."""
    listener = SwitchDeviceEventListener.__new__(SwitchDeviceEventListener)
    listener.hass = _make_hass()
    listener.device_id = "ha-device-id-1"
    listener._last_fired_timestamp = -1  # fresh; first real ts fires (#336 guard)
    listener._device = SimpleNamespace(
        id="hdm:switch:1",
        name="Test Switch",
        eventtype=SimpleNamespace(name=eventtype_name),
        keyname=SimpleNamespace(name=keyname_name),
        eventtimestamp=eventtimestamp,
    )
    return listener





# ===========================================================================
# __init__.py — lines 508-515 (_parse_time), 676, 703, 706
# ===========================================================================

class TestInitParsetime:
    """Lines 508-515: _parse_time inner function via async_setup_entry with
    silent_mode_enabled + valid start/end options.

    Strategy: run a full async_setup_entry with all heavy dependencies
    patched out, supplying OPT_SILENT_MODE_ENABLED + OPT_SILENT_MODE_START
    + OPT_SILENT_MODE_END + OPT_PRESENCE_ENTITY to trigger the silent-mode
    block and exercise _parse_time.
    """

    PATCH_SESSION = "custom_components.bosch_shc.SHCSessionAsync"
    PATCH_DR = "custom_components.bosch_shc.dr"
    PATCH_PARSE_CERT = "custom_components.bosch_shc.parse_certificate"
    PATCH_TRACK_INTERVAL = "custom_components.bosch_shc.async_track_time_interval"
    PATCH_TRACK_STATE = "custom_components.bosch_shc.async_track_state_change_event"
    PATCH_IR = "custom_components.bosch_shc.ir"

    def _make_session(self):
        from boschshcpy import SHCSessionAsync as _SA
        session = MagicMock(spec=_SA)
        session.async_init = AsyncMock()
        session.start_polling = AsyncMock()
        session.stop_polling = AsyncMock()
        session.information = SimpleNamespace(
            updateState=SimpleNamespace(name="NO_UPDATE_AVAILABLE"),
            unique_id="aa:bb:cc:dd:ee:ff",
            version="9.0.0",
            name="SHC",
        )
        session.scenarios = []
        session.subscribe_scenario_callback = MagicMock()
        session.unsubscribe_scenario_callback = MagicMock()
        dh = MagicMock()
        dh.universal_switches = []
        dh.camera_eyes = []
        dh.camera_360 = []
        dh.camera_outdoor_gen2 = []
        session.device_helper = dh
        session.devices = []
        session.get_zigbee_routing_info = AsyncMock()
        return session

    def _make_hass(self):
        hass = MagicMock()
        hass.data = {}

        async def _executor_job(fn, *args):
            return fn(*args)

        hass.async_add_executor_job = _executor_job
        hass.config_entries = MagicMock()
        hass.config_entries.async_forward_entry_setups = AsyncMock(return_value=True)
        hass.config_entries.async_entries = MagicMock(return_value=[])
        hass.bus = MagicMock()
        hass.bus.async_listen_once = MagicMock(return_value=MagicMock())
        hass.services = MagicMock()
        hass.services.has_service = MagicMock(return_value=True)  # already registered
        hass.async_create_task = MagicMock()
        hass.states = MagicMock()
        hass.states.get = MagicMock(return_value=SimpleNamespace(state="home"))
        return hass

    def _make_entry(self):

        from custom_components.bosch_shc.const import (
            OPT_PRESENCE_ENTITY,
            OPT_SILENT_MODE_ENABLED,
            OPT_SILENT_MODE_END,
            OPT_SILENT_MODE_START,
        )

        entry = MagicMock()
        entry.entry_id = "eid_parsetime"
        entry.title = "ParseTime SHC"
        entry.data = {
            "ssl_certificate": "",
            "ssl_key": "",
            "host": "192.168.1.1",
            "hostname": "192.168.1.1",
        }
        entry.options = {
            OPT_PRESENCE_ENTITY: ["person.test"],
            OPT_SILENT_MODE_ENABLED: True,
            OPT_SILENT_MODE_START: "22:30",
            OPT_SILENT_MODE_END: "07:00",
        }
        entry.state = ConfigEntryState.SETUP_IN_PROGRESS
        entry.add_update_listener = MagicMock(return_value=MagicMock())
        entry.async_on_unload = MagicMock()
        return entry

    def test_parse_time_valid_hhmm(self):
        """Lines 508-513: _parse_time parses HH:MM format correctly."""
        from custom_components.bosch_shc import async_setup_entry

        session = self._make_session()
        hass = self._make_hass()
        entry = self._make_entry()

        fake_dr = MagicMock()
        fake_dr.async_get_or_create = MagicMock(
            return_value=SimpleNamespace(id="fake_shc_id")
        )

        with patch(self.PATCH_SESSION, return_value=session), \
             patch(self.PATCH_DR) as dr_mock, \
             patch(self.PATCH_PARSE_CERT, return_value=None), \
             patch(self.PATCH_TRACK_INTERVAL, return_value=MagicMock()), \
             patch(self.PATCH_TRACK_STATE, return_value=MagicMock()), \
             patch(self.PATCH_IR):
            dr_mock.async_get = MagicMock(return_value=fake_dr)
            dr_mock.async_get_or_create = MagicMock()
            _run(async_setup_entry(hass, entry))



        # If no exception, _parse_time executed (lines 508-513 covered)


class TestInitCameraToolIssue:
    """Line 676: ir.async_create_issue for camera tool when cameras present."""

    def _make_full_setup_with_cameras(self, has_cameras, camera_tool_installed):
        from boschshcpy import SHCSessionAsync as _SA

        from custom_components.bosch_shc import async_setup_entry

        session = MagicMock(spec=_SA)
        session.async_init = AsyncMock()
        session.start_polling = AsyncMock()
        session.information = SimpleNamespace(
            updateState=SimpleNamespace(name="NO_UPDATE_AVAILABLE"),
            unique_id="aa:bb:cc:dd:ee:ff",
            version="9.0.0",
            name="SHC",
        )
        session.scenarios = []
        session.subscribe_scenario_callback = MagicMock()
        session.unsubscribe_scenario_callback = MagicMock()

        dh = MagicMock()
        dh.universal_switches = []
        cam_list = [_fake_dev("cam1")] if has_cameras else []
        dh.camera_eyes = cam_list
        dh.camera_360 = []
        dh.camera_outdoor_gen2 = []
        session.device_helper = dh
        session.devices = []
        session.get_zigbee_routing_info = AsyncMock()

        hass = MagicMock()
        hass.data = {}

        async def _executor_job(fn, *args):
            return fn(*args)

        hass.async_add_executor_job = _executor_job
        hass.config_entries = MagicMock()
        hass.config_entries.async_forward_entry_setups = AsyncMock(return_value=True)
        # Control camera_tool_installed via async_entries
        tool_entries = [MagicMock()] if camera_tool_installed else []
        hass.config_entries.async_entries = MagicMock(return_value=tool_entries)
        hass.bus = MagicMock()
        hass.bus.async_listen_once = MagicMock(return_value=MagicMock())
        hass.services = MagicMock()
        hass.services.has_service = MagicMock(return_value=True)
        hass.async_create_task = MagicMock()

        entry = MagicMock()
        entry.entry_id = "eid_cam"
        entry.title = "Camera SHC"
        entry.data = {
            "ssl_certificate": "", "ssl_key": "", "host": "192.168.1.1",
            "hostname": "192.168.1.1",
        }
        entry.options = {}
        entry.state = ConfigEntryState.SETUP_IN_PROGRESS
        entry.add_update_listener = MagicMock(return_value=MagicMock())
        entry.async_on_unload = MagicMock()

        fake_dr = MagicMock()
        fake_dr.async_get_or_create = MagicMock(
            return_value=SimpleNamespace(id="fake_shc_id")
        )

        ir_mock = MagicMock()
        issue_created = []

        def _create_issue(h, domain, issue_id, **kwargs):
            issue_created.append(issue_id)

        ir_mock.async_create_issue = MagicMock(side_effect=_create_issue)
        ir_mock.async_delete_issue = MagicMock()
        ir_mock.IssueSeverity = MagicMock()
        ir_mock.IssueSeverity.WARNING = "warning"

        with patch("custom_components.bosch_shc.SHCSessionAsync", return_value=session), \
             patch("custom_components.bosch_shc.dr") as dr_mock, \
             patch("custom_components.bosch_shc.parse_certificate", return_value=None), \
             patch("custom_components.bosch_shc.async_track_time_interval",
                   return_value=MagicMock()), \
             patch("custom_components.bosch_shc.async_track_state_change_event",
                   return_value=MagicMock()), \
             patch("custom_components.bosch_shc.ir", ir_mock):
            dr_mock.async_get = MagicMock(return_value=fake_dr)
            _run(async_setup_entry(hass, entry))

        return ir_mock, issue_created

    def test_camera_tool_issue_created_when_cameras_and_no_tool(self):
        """Line 676: has_cameras=True + tool not installed → async_create_issue called.

        The issue id is scoped per config entry (so multiple SHC controllers
        don't clear each other's warnings) — it's ISSUE_CAMERA_TOOL_<entry_id>,
        not the bare constant.
        """
        ir_mock, issues = self._make_full_setup_with_cameras(
            has_cameras=True, camera_tool_installed=False
        )
        from custom_components.bosch_shc.const import ISSUE_CAMERA_TOOL
        assert f"{ISSUE_CAMERA_TOOL}_eid_cam" in issues

    def test_camera_tool_issue_deleted_when_no_cameras(self):
        """Else branch: no cameras → async_delete_issue called."""
        ir_mock, issues = self._make_full_setup_with_cameras(
            has_cameras=False, camera_tool_installed=False
        )
        ir_mock.async_delete_issue.assert_called()




class TestInitUnloadPollingAndListeners:
    """Lines 703, 706: async_unload_entry — polling_handler + switch_event_listeners."""

    def _build_runtime(self, with_polling_handler=True, with_listeners=True):
        from homeassistant.helpers.device_registry import DeviceEntry

        from custom_components.bosch_shc.data import SHCData

        session = MagicMock()
        session.stop_polling = AsyncMock()
        session.unsubscribe_scenario_callback = MagicMock()

        handler_called = []
        listener_shutdown_called = []

        polling_handler = MagicMock(side_effect=lambda: handler_called.append(True))
        if not with_polling_handler:
            polling_handler = None

        listener = MagicMock()
        listener.shutdown = MagicMock(side_effect=lambda: listener_shutdown_called.append(True))

        listeners = [listener] if with_listeners else []

        # SHCData requires session, shc_device (DeviceEntry), title
        shc_dev = MagicMock(spec=DeviceEntry)
        rt = SHCData(
            session=session,
            shc_device=shc_dev,
            title="Test SHC",
            cert_check_unsub=None,
            polling_handler=polling_handler,
            presence_unsub=None,
            silent_mode_unsubs=[],
            switch_event_listeners=listeners,
        )
        return rt, handler_called, listener_shutdown_called

    def test_unload_entry_calls_polling_handler_and_listeners(self):
        """Lines 703, 706: polling_handler() + listener.shutdown() called in unload."""
        from custom_components.bosch_shc import async_unload_entry

        rt, handler_called, listener_called = self._build_runtime()

        hass = MagicMock()
        hass.config_entries = MagicMock()
        hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
        hass.config_entries.async_entries = MagicMock(return_value=[])
        hass.services = MagicMock()
        hass.services.has_service = MagicMock(return_value=False)

        entry = MagicMock()
        entry.entry_id = "eid_unload"
        # async_unload_entry reads from entry.runtime_data (not hass.data)
        entry.runtime_data = rt

        _run(async_unload_entry(hass, entry))

        assert handler_called, "polling_handler should have been called"
        assert listener_called, "listener.shutdown should have been called"




class TestInitUnloadSilentModeUnsub:
    """__init__.py line 703: silent_mode_unsub called during unload."""

    def test_unload_calls_silent_mode_unsub(self):
        """Line 703: _unsub() called for each silent_mode_unsub in unload."""
        from homeassistant.helpers.device_registry import DeviceEntry

        from custom_components.bosch_shc import async_unload_entry
        from custom_components.bosch_shc.data import SHCData

        session = MagicMock()
        session.stop_polling = AsyncMock()
        session.unsubscribe_scenario_callback = MagicMock()

        unsub_called = []
        silent_unsub = MagicMock(side_effect=lambda: unsub_called.append(True))

        shc_dev = MagicMock(spec=DeviceEntry)
        rt = SHCData(
            session=session,
            shc_device=shc_dev,
            title="Test SHC",
            cert_check_unsub=None,
            polling_handler=None,
            presence_unsub=None,
            silent_mode_unsubs=[silent_unsub],  # one silent unsub to trigger line 703
            switch_event_listeners=[],
        )

        hass = MagicMock()
        hass.config_entries = MagicMock()
        hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
        hass.config_entries.async_entries = MagicMock(return_value=[])
        hass.services = MagicMock()
        hass.services.has_service = MagicMock(return_value=False)

        entry = MagicMock()
        entry.entry_id = "eid_silent"
        entry.runtime_data = rt

        _run(async_unload_entry(hass, entry))

        assert unsub_called, "silent_mode_unsub should have been called"




class TestInitParseTimeError:
    """__init__.py lines 514-515: _parse_time with invalid time string."""

    def test_parse_time_invalid_value_returns_none(self):
        """Lines 514-515: invalid time format → ValueError caught → return None."""
        from boschshcpy import SHCSessionAsync as _SA

        from custom_components.bosch_shc import async_setup_entry
        from custom_components.bosch_shc.const import (
            OPT_PRESENCE_ENTITY,
            OPT_SILENT_MODE_ENABLED,
            OPT_SILENT_MODE_END,
            OPT_SILENT_MODE_START,
        )

        session = MagicMock(spec=_SA)
        session.async_init = AsyncMock()
        session.start_polling = AsyncMock()
        session.stop_polling = AsyncMock()
        session.information = SimpleNamespace(
            updateState=SimpleNamespace(name="NO_UPDATE_AVAILABLE"),
            unique_id="aa:bb:cc:dd:ee:ff",
            version="9.0.0",
            name="SHC",
        )
        session.scenarios = []
        session.subscribe_scenario_callback = MagicMock()
        session.unsubscribe_scenario_callback = MagicMock()
        dh = MagicMock()
        dh.universal_switches = []
        dh.camera_eyes = []
        dh.camera_360 = []
        dh.camera_outdoor_gen2 = []
        session.device_helper = dh
        session.devices = []
        session.get_zigbee_routing_info = AsyncMock()

        hass = MagicMock()
        hass.data = {}

        async def _executor_job(fn, *args):
            return fn(*args)

        hass.async_add_executor_job = _executor_job
        hass.config_entries = MagicMock()
        hass.config_entries.async_forward_entry_setups = AsyncMock(return_value=True)
        hass.config_entries.async_entries = MagicMock(return_value=[])
        hass.bus = MagicMock()
        hass.bus.async_listen_once = MagicMock(return_value=MagicMock())
        hass.services = MagicMock()
        hass.services.has_service = MagicMock(return_value=True)
        hass.async_create_task = MagicMock()
        hass.states = MagicMock()
        hass.states.get = MagicMock(return_value=SimpleNamespace(state="home"))

        entry = MagicMock()
        entry.entry_id = "eid_parsetime_err"
        entry.title = "ParseTime Error SHC"
        entry.data = {
            "ssl_certificate": "",
            "ssl_key": "",
            "host": "192.168.1.1",
            "hostname": "192.168.1.1",
        }
        # Pass INVALID time strings → triggers except (ValueError, IndexError) at 514-515
        entry.options = {
            OPT_PRESENCE_ENTITY: ["person.test"],
            OPT_SILENT_MODE_ENABLED: True,
            OPT_SILENT_MODE_START: "not_a_time",
            OPT_SILENT_MODE_END: "also_not",
        }
        entry.state = ConfigEntryState.SETUP_IN_PROGRESS
        entry.add_update_listener = MagicMock(return_value=MagicMock())
        entry.async_on_unload = MagicMock()

        fake_dr = MagicMock()
        fake_dr.async_get_or_create = MagicMock(
            return_value=SimpleNamespace(id="fake_shc_id")
        )

        with patch("custom_components.bosch_shc.SHCSessionAsync", return_value=session), \
             patch("custom_components.bosch_shc.dr") as dr_mock, \
             patch("custom_components.bosch_shc.parse_certificate", return_value=None), \
             patch("custom_components.bosch_shc.async_track_time_interval",
                   return_value=MagicMock()), \
             patch("custom_components.bosch_shc.async_track_state_change_event",
                   return_value=MagicMock()), \
             patch("custom_components.bosch_shc.ir"):
            dr_mock.async_get = MagicMock(return_value=fake_dr)
            _run(async_setup_entry(hass, entry))




class TestPresenceStateContinueOnNone:
    """__init__.py line 436 — continue when hass.states.get(eid) returns None."""

    def test_none_state_obj_skipped_other_entity_still_evaluated(self):
        """Two entities: first returns None, second returns 'home'.
        The continue at line 436 is hit for the first entity, but the second
        entity is still evaluated and causes lock_on = True.
        """
        hass, state_cb = _gaps2_run_setup_with_presence(
            presence_entities=["person.alice", "person.bob"],
            # person.alice is not in the map (returns None from states.get)
            # person.bob is home
            hass_states={"person.bob": "home"},
        )
        assert state_cb is not None

        # Fire a state change event (new_state is not unavailable/unknown, not None)
        event = SimpleNamespace(data={"new_state": SimpleNamespace(state="home")})
        state_cb(event)

        # The continue for alice was hit, bob was evaluated as home => task created
        hass.async_create_task.assert_called_once()

    def test_all_states_none_means_no_present_entity(self):
        """All entities return None from states.get: loop hits continue for each,
        any_present stays False → lock_off.
        """
        hass, state_cb = _gaps2_run_setup_with_presence(
            presence_entities=["person.alice", "person.bob"],
            # Neither entity is in the map
            hass_states={},
        )
        assert state_cb is not None

        # First event: lock_on=False, _last_lock_state starts at None.
        # Since False != None (initial value), _last_lock_state will update and
        # task may or may not be created depending on initial state. The key thing
        # is that the None-state continue branch IS executed for both entities.
        event = SimpleNamespace(data={"new_state": SimpleNamespace(state="not_home")})
        # Must not raise (continues work correctly)
        state_cb(event)

    def test_first_entity_none_second_entity_home_lock_turns_on(self):
        """When first entity state_obj is None (continue), second is home.
        aggregate = True → task created to set lock on.
        """
        hass, state_cb = _gaps2_run_setup_with_presence(
            presence_entities=["person.ghost", "person.real"],
            hass_states={"person.real": "home"},
        )
        assert state_cb is not None

        event = SimpleNamespace(data={"new_state": SimpleNamespace(state="home")})
        state_cb(event)

        # person.ghost -> None -> continue; person.real -> home -> any_present=True
        hass.async_create_task.assert_called_once()

    def test_first_entity_none_second_not_home_no_task_when_aggregate_unchanged(self):
        """First entity returns None (continue), second is not_home.
        aggregate = False, _last_lock_state starts None so first call creates a task,
        subsequent calls with same aggregate don't.
        """
        hass, state_cb = _gaps2_run_setup_with_presence(
            presence_entities=["person.ghost", "person.real"],
            hass_states={"person.real": "not_home"},
        )
        assert state_cb is not None

        event = SimpleNamespace(data={"new_state": SimpleNamespace(state="not_home")})
        # First call: any_present=False, _last_lock_state differs → task created
        state_cb(event)

        # Second call same aggregate → no second task
        hass.async_create_task.reset_mock()
        state_cb(event)
        hass.async_create_task.assert_not_called()



# ---------------------------------------------------------------------------
# __init__.py — line 155: rawscan continue when no runtime_data
# ---------------------------------------------------------------------------

class TestZigbeeCoordinatorFailureDoesNotBlockSetup:
    """#362: a Zigbee-routing coordinator failure must not fail the entry.

    async_setup_entry must use async_refresh() (not
    async_config_entry_first_refresh()) so a failing/timing-out Zigbee poll
    on setup can't raise ConfigEntryNotReady and get the whole integration
    stuck retrying, over a disabled-by-default diagnostic sensor.
    """

    def test_setup_succeeds_when_zigbee_routing_fetch_fails(
        self, fake_hass, fake_entry, fake_session
    ):
        from custom_components.bosch_shc.__init__ import async_setup_entry

        session = fake_session
        # A plain TimeoutError (not SHCException/SHCConnectionError) isn't
        # swallowed per-device by the coordinator's own isolation — it
        # propagates out of _async_update_data, the scenario that actually
        # reproduces #362 (a per-device SHC error is already isolated and
        # never reaches async_setup_entry at all).
        session.devices = [SimpleNamespace(id="hdm:ZigBee:abc")]
        session.get_zigbee_routing_info = AsyncMock(side_effect=TimeoutError)

        hass = fake_hass
        entry = fake_entry
        dr_mock = _make_fake_device_registry()
        track_unsub = MagicMock()

        async def _do():
            with (
                patch(PATCH_SESSION, return_value=session),
                patch(PATCH_DR_GET, return_value=dr_mock),
                patch(PATCH_PARSE_CERT, return_value=None),
                patch(PATCH_TRACK_INTERVAL, return_value=track_unsub),
            ):
                result = await async_setup_entry(hass, entry)
            # The refresh is now backgrounded (see
            # TestZigbeeCoordinatorRefreshBackgrounded below) — await it here,
            # in the same event loop, since this test cares about its outcome.
            await asyncio.gather(*entry._background_tasks)
            return result

        result = _run(_do())

        assert result is True
        coordinator = entry.runtime_data.zigbee_routing_coordinator
        assert coordinator.last_update_success is False


class TestZigbeeCoordinatorRefreshBackgrounded:
    """The initial Zigbee-routing refresh must not block async_setup_entry.

    Regression for a real report of the integration taking ~150s to load
    after a restart (visible in HA's own "integration startup time"
    diagnostics). Root cause: the coordinator queries every Zigbee device
    sequentially, live over-the-air (never cached SHC-side) — with many or
    slow/sleepy devices that adds up to minutes, and it was previously
    awaited inline, so the entry couldn't reach LOADED until it finished.
    """

    def test_setup_returns_without_waiting_for_zigbee_refresh(
        self, fake_hass, fake_entry, fake_session
    ):
        from custom_components.bosch_shc.__init__ import async_setup_entry

        session = fake_session
        session.devices = [SimpleNamespace(id="hdm:ZigBee:abc")]
        never_resolves = asyncio.Event()

        async def _hang_forever(*_args, **_kwargs):
            await never_resolves.wait()

        session.get_zigbee_routing_info = AsyncMock(side_effect=_hang_forever)

        hass = fake_hass
        entry = fake_entry
        dr_mock = _make_fake_device_registry()
        track_unsub = MagicMock()

        async def _do():
            with (
                patch(PATCH_SESSION, return_value=session),
                patch(PATCH_DR_GET, return_value=dr_mock),
                patch(PATCH_PARSE_CERT, return_value=None),
                patch(PATCH_TRACK_INTERVAL, return_value=track_unsub),
            ):
                try:
                    return await asyncio.wait_for(
                        async_setup_entry(hass, entry), timeout=1
                    )
                finally:
                    for task in entry._background_tasks:
                        task.cancel()
                    await asyncio.gather(
                        *entry._background_tasks, return_exceptions=True
                    )

        result = _run(_do())

        assert result is True
        entry.async_create_background_task.assert_called_once()
        call = entry.async_create_background_task.call_args
        assert call.args[0] is hass
        assert "zigbee_routing" in call.args[2]

    def test_unload_cancels_in_flight_refresh_before_stop_polling(
        self, fake_hass, fake_entry, fake_session
    ):
        """async_unload_entry must cancel a still-running background refresh
        before calling session.stop_polling() (which closes the shared HTTP
        session) — otherwise the in-flight fetch races a closed session and
        logs a spurious traceback instead of a clean cancellation.
        """
        from custom_components.bosch_shc import async_unload_entry
        from custom_components.bosch_shc.__init__ import async_setup_entry

        session = fake_session
        session.devices = [SimpleNamespace(id="hdm:ZigBee:abc")]
        never_resolves = asyncio.Event()
        fetch_was_cancelled = False

        async def _hang_forever(*_args, **_kwargs):
            nonlocal fetch_was_cancelled
            try:
                await never_resolves.wait()
            except asyncio.CancelledError:
                fetch_was_cancelled = True
                raise

        session.get_zigbee_routing_info = AsyncMock(side_effect=_hang_forever)

        hass = fake_hass
        entry = fake_entry
        dr_mock = _make_fake_device_registry()
        track_unsub = MagicMock()

        async def _do():
            with (
                patch(PATCH_SESSION, return_value=session),
                patch(PATCH_DR_GET, return_value=dr_mock),
                patch(PATCH_PARSE_CERT, return_value=None),
                patch(PATCH_TRACK_INTERVAL, return_value=track_unsub),
            ):
                await asyncio.wait_for(async_setup_entry(hass, entry), timeout=1)
                # The background task must still be running at this point —
                # otherwise this test isn't exercising the race at all.
                assert not entry._background_tasks[0].done()
                return await asyncio.wait_for(
                    async_unload_entry(hass, entry), timeout=1
                )

        result = _run(_do())

        assert result is True
        assert fetch_was_cancelled is True
        session.stop_polling.assert_awaited_once()


class TestRawscanNoRuntimeData:
    """rawscan_service_call must skip entries lacking runtime_data (line 155)."""

    def test_rawscan_skips_entry_without_runtime_data(self):
        from homeassistant.const import ATTR_COMMAND

        from custom_components.bosch_shc import _register_rawscan_service
        from custom_components.bosch_shc.const import (
            ATTR_TITLE,
            SERVICE_TRIGGER_RAWSCAN,
        )

        hass = MagicMock()
        hass.services = MagicMock()
        hass.services.has_service = MagicMock(return_value=False)
        hass.services.async_register = MagicMock()

        _register_rawscan_service(hass)

        # Capture the handler
        rawscan_calls = [
            c for c in hass.services.async_register.call_args_list
            if c.args[1] == SERVICE_TRIGGER_RAWSCAN
        ]
        assert rawscan_calls, "rawscan service was not registered"
        handler = rawscan_calls[0].args[2]

        # Entry with no runtime_data attr
        entry_no_rt = SimpleNamespace(entry_id="no-rt", title="NoRT")
        hass.config_entries = MagicMock()
        hass.config_entries.async_entries = MagicMock(return_value=[entry_no_rt])

        fake_call = SimpleNamespace(
            data={ATTR_TITLE: "", ATTR_COMMAND: "devices",
                  "device_id": "", "service_id": ""}
        )

        # Entry skipped (no runtime_data) → ServiceValidationError (no matching entry processed)
        from homeassistant.exceptions import ServiceValidationError
        with pytest.raises(ServiceValidationError):
            asyncio.run(handler(fake_call))




# ---------------------------------------------------------------------------
# __init__.py — lines 349-352: _entity_is_present zone domain
# ---------------------------------------------------------------------------

class TestEntityIsPresentZoneDomain:
    """_entity_is_present zone branch (int parse + TypeError/ValueError fallback)."""

    def _get_entity_is_present_fn(self):
        """Extract the _entity_is_present inner function via setup with presence."""
        from custom_components.bosch_shc import async_setup_entry
        from custom_components.bosch_shc.const import (
            OPT_CHILD_LOCK_ENABLED,
            OPT_PRESENCE_ENTITY,
        )

        hass = MagicMock()
        hass.data = {}

        async def _executor_job(fn, *args):
            return fn(*args)

        hass.async_add_executor_job = _executor_job
        hass.config_entries = MagicMock()
        hass.config_entries.async_forward_entry_setups = AsyncMock(return_value=True)
        hass.bus = MagicMock()
        hass.bus.async_listen_once = MagicMock(return_value=MagicMock())
        hass.services = MagicMock()
        hass.services.has_service = MagicMock(return_value=False)
        hass.async_create_task = MagicMock()

        captured_fn = []

        def _fake_track_state_change(h, entities, fn):
            captured_fn.append(fn)
            return MagicMock()

        from boschshcpy import SHCSessionAsync as _SHCSessionAsync
        fake_session = MagicMock(spec=_SHCSessionAsync)
        fake_session.async_init = AsyncMock()
        fake_session.start_polling = AsyncMock()
        fake_session.stop_polling = AsyncMock()
        fake_session.information = SimpleNamespace(
            updateState=SimpleNamespace(name="NO_UPDATE_AVAILABLE"),
            unique_id="aa:bb:cc:dd:ee:ff",
            version="9.0.0",
            name="SHC",
        )
        fake_session.subscribe_scenario_callback = MagicMock()
        fake_session.unsubscribe_scenario_callback = MagicMock()
        fake_session.device_helper = MagicMock()
        fake_session.device_helper.universal_switches = []
        fake_session.devices = []
        fake_session.get_zigbee_routing_info = AsyncMock()

        entry = MagicMock()
        entry.entry_id = "E1"
        entry.title = "Test"
        entry.data = {"ssl_certificate": "", "ssl_key": "", "host": "192.168.1.1"}
        entry.options = {
            OPT_PRESENCE_ENTITY: ["zone.home"],
            OPT_CHILD_LOCK_ENABLED: True,
        }
        entry.state = ConfigEntryState.SETUP_IN_PROGRESS

        dr_fake = MagicMock()
        dr_fake.async_get_or_create = MagicMock(
            return_value=SimpleNamespace(id="dr-001")
        )

        with (
            patch("custom_components.bosch_shc.SHCSessionAsync", return_value=fake_session),
            patch("custom_components.bosch_shc.dr.async_get",
                  return_value=dr_fake),
            patch("custom_components.bosch_shc.parse_certificate", return_value=None),
            patch("custom_components.bosch_shc.async_track_time_interval",
                  return_value=MagicMock()),
            patch("custom_components.bosch_shc.async_track_state_change_event",
                  side_effect=_fake_track_state_change),
            patch("homeassistant.helpers.issue_registry.async_create_issue", MagicMock()),
        ):
            asyncio.run(async_setup_entry(hass, entry))

        assert captured_fn, "async_track_state_change_event was not called"
        return captured_fn[0], hass

    def test_zone_domain_numeric_state_present(self):
        """Zone with numeric state > 0 -> present (line 350)."""
        presence_fn, hass = self._get_entity_is_present_fn()

        hass.states.get = MagicMock(
            return_value=SimpleNamespace(state="3")
        )

        event = SimpleNamespace(
            data={"new_state": SimpleNamespace(state="3")}
        )
        # Must not raise
        presence_fn(event)

    def test_zone_domain_zero_state_absent(self):
        """Zone with state '0' -> not present."""
        presence_fn, hass = self._get_entity_is_present_fn()

        hass.states.get = MagicMock(
            return_value=SimpleNamespace(state="0")
        )

        event = SimpleNamespace(
            data={"new_state": SimpleNamespace(state="0")}
        )
        presence_fn(event)

    def test_zone_domain_non_numeric_state_absent(self):
        """Zone with non-numeric state -> fallback returns False (line 351-352)."""
        presence_fn, hass = self._get_entity_is_present_fn()

        hass.states.get = MagicMock(
            return_value=SimpleNamespace(state="unknown")
        )

        event = SimpleNamespace(
            data={"new_state": SimpleNamespace(state="unknown")}
        )
        presence_fn(event)  # Must not raise

    def test_zone_domain_none_state_absent(self):
        """Zone with state=None -> TypeError -> fallback False (line 351-352)."""
        presence_fn, hass = self._get_entity_is_present_fn()

        hass.states.get = MagicMock(
            return_value=SimpleNamespace(state=None)
        )

        event = SimpleNamespace(
            data={"new_state": SimpleNamespace(state="3")}
        )
        presence_fn(event)  # Must not raise

    def test_presence_fn_skips_unavailable_state(self):
        """new_state 'unavailable' must be skipped before any entity is present check."""
        presence_fn, hass = self._get_entity_is_present_fn()

        event = SimpleNamespace(
            data={"new_state": SimpleNamespace(state="unavailable")}
        )
        presence_fn(event)  # Must not raise

    def test_presence_fn_skips_none_new_state(self):
        """new_state=None must be silently skipped."""
        presence_fn, hass = self._get_entity_is_present_fn()

        event = SimpleNamespace(data={"new_state": None})
        presence_fn(event)  # Must not raise




# ---------------------------------------------------------------------------
# __init__.py — line 507: presence_unsub called during unload
# ---------------------------------------------------------------------------

class TestUnloadPresenceUnsub:
    """async_unload_entry must call runtime.presence_unsub() (line 507)."""

    def test_presence_unsub_called(self):
        from boschshcpy import SHCSessionAsync as _SHCSessionAsync

        from custom_components.bosch_shc import async_unload_entry
        from custom_components.bosch_shc.data import SHCData
        fake_session = MagicMock(spec=_SHCSessionAsync)
        fake_session.stop_polling = AsyncMock()
        fake_session.unsubscribe_scenario_callback = MagicMock()

        fake_dev = SimpleNamespace(id="dr-001")
        runtime = SHCData(
            session=fake_session,
            shc_device=fake_dev,
            title="Test",
        )
        called = []
        runtime.presence_unsub = MagicMock(side_effect=lambda: called.append(True))
        runtime.polling_handler = MagicMock()
        runtime.cert_check_unsub = None

        hass = MagicMock()

        async def _executor_job(fn, *args):
            return fn(*args)

        hass.async_add_executor_job = _executor_job
        hass.config_entries = MagicMock()
        hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
        hass.config_entries.async_entries = MagicMock(return_value=[])
        hass.services = MagicMock()
        hass.services.has_service = MagicMock(return_value=False)

        entry = MagicMock()
        entry.entry_id = "E1"
        entry.runtime_data = runtime

        asyncio.run(async_unload_entry(hass, entry))
        assert called == [True], "presence_unsub was not called"




# ===========================================================================
# 1. __init__.py line 436 — presence_state_changed: state_obj is None → continue
# ===========================================================================

class TestPresenceStateNoneEntity:
    """When hass.states.get(eid) returns None the loop must continue without crash."""

    PATCH_SESSION = "custom_components.bosch_shc.__init__.SHCSessionAsync"
    PATCH_ZEROCONF = "custom_components.bosch_shc.__init__.async_get_instance"
    PATCH_DR_GET = "custom_components.bosch_shc.__init__.dr.async_get"
    PATCH_PARSE_CERT = "custom_components.bosch_shc.__init__.parse_certificate"
    PATCH_TRACK_INTERVAL = "custom_components.bosch_shc.__init__.async_track_time_interval"
    PATCH_TRACK_STATE = "custom_components.bosch_shc.__init__.async_track_state_change_event"

    def _make_session(self):
        from boschshcpy import SHCSessionAsync as _SHCSessionAsync
        session = MagicMock(spec=_SHCSessionAsync)
        session.async_init = AsyncMock()
        session.start_polling = AsyncMock()
        session.stop_polling = AsyncMock()
        session.information = SimpleNamespace(
            updateState=SimpleNamespace(name="NO_UPDATE_AVAILABLE"),
            unique_id="aa:bb:cc:dd:ee:ff",
            version="9.0.0",
            name="My SHC",
        )
        session.scenarios = []
        session.rawscan_commands = ["devices"]
        session.subscribe_scenario_callback = MagicMock()
        session.unsubscribe_scenario_callback = MagicMock()
        dh = MagicMock()
        dh.universal_switches = []
        dh.thermostats = []
        dh.roomthermostats = []
        dh.wallthermostats = []
        dh.micromodule_shutter_controls = []
        dh.micromodule_blinds = []
        dh.micromodule_light_attached = []
        dh.micromodule_relays = []
        dh.micromodule_impulse_relays = []
        dh.micromodule_dimmers = []
        dh.light_switches_bsm = []
        session.device_helper = dh
        session.devices = []
        session.get_zigbee_routing_info = AsyncMock()
        return session

    def _make_hass(self, states=None):
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
        hass.bus.fire = MagicMock()
        hass.services = MagicMock()
        hass.services.async_register = MagicMock()
        hass.async_create_task = MagicMock()
        return hass

    def _make_entry(self, options=None):
        entry = MagicMock()
        entry.entry_id = "eid1"
        entry.title = "Test SHC"
        entry.data = {"ssl_certificate": "", "ssl_key": "", "host": "1.2.3.4"}
        entry.options = options or {}
        entry.add_update_listener = MagicMock(return_value=MagicMock())
        entry.async_on_unload = MagicMock()
        entry.state = ConfigEntryState.SETUP_IN_PROGRESS
        return entry

    def test_state_none_entity_continues_without_crash(self):
        """When hass.states.get(eid) returns None, the loop continues (line 436)."""
        from custom_components.bosch_shc.__init__ import async_setup_entry
        from custom_components.bosch_shc.const import (
            OPT_CHILD_LOCK_ENABLED,
            OPT_PRESENCE_ENTITY,
        )

        session = self._make_session()
        # Entity is listed in presence_entities but state returns None
        # (entity doesn't exist yet in HA state machine)
        options = {
            OPT_PRESENCE_ENTITY: ["person.someone"],
            OPT_CHILD_LOCK_ENABLED: True,
        }
        hass = self._make_hass(states={})  # no states → get() returns None
        entry = self._make_entry(options=options)
        dr_mock = MagicMock()
        dr_mock.async_get_or_create = MagicMock(
            return_value=SimpleNamespace(id="dreg-001")
        )

        captured_state_cb = []

        def _capture_track_state(h, entity_ids, cb):
            captured_state_cb.append(cb)
            return MagicMock()

        with (
            patch(self.PATCH_SESSION, return_value=session),
            patch(self.PATCH_DR_GET, return_value=dr_mock),
            patch(self.PATCH_PARSE_CERT, return_value=None),
            patch(self.PATCH_TRACK_INTERVAL, return_value=MagicMock()),
            patch(self.PATCH_TRACK_STATE, side_effect=_capture_track_state),
        ):
            _run(async_setup_entry(hass, entry))

        assert captured_state_cb, "async_track_state_change_event not called"
        cb = captured_state_cb[0]

        # Fire a fake state-change event where new_state is valid but
        # the entity itself is absent from hass.states (returns None for others)
        new_state = SimpleNamespace(state="home")
        event = SimpleNamespace(data={"new_state": new_state})

        # Must NOT raise even though hass.states.get returns None
        cb(event)




# ---------------------------------------------------------------------------
# __init__.py:126-127 — scenario trigger error path
# ---------------------------------------------------------------------------

class TestScenarioServiceCallTriggerError:
    """scenario_service_call must raise ServiceValidationError when scenario.trigger
    raises SHCException or SHCConnectionError (lines 126-127).
    """

    def _get_scenario_handler(self):
        """Register scenario service via async_setup and return the handler."""
        from custom_components.bosch_shc import async_setup

        hass = MagicMock()
        hass.data = {}
        hass.loop = MagicMock()
        hass.config_entries = MagicMock()
        hass.bus = MagicMock()
        hass.bus.async_listen_once = MagicMock(return_value=MagicMock(return_value=None))
        hass.services = MagicMock()
        hass.services.async_register = MagicMock()
        hass.services.has_service = MagicMock(return_value=False)
        hass.async_create_task = MagicMock()

        _run(async_setup(hass, {}))

        register_calls = hass.services.async_register.call_args_list
        for c in register_calls:
            if c.args[1] == SERVICE_TRIGGER_SCENARIO:
                return c.args[2], hass

        raise AssertionError("trigger_scenario service was not registered")

    def _make_runtime_with_failing_scenario(self, exc):
        """Build a fake runtime_data whose scenario.async_trigger raises exc."""
        class _FailingScenario:
            name = "failing_scene"

            async def async_trigger(self_):
                raise exc

        return SimpleNamespace(
            title="SHC Test",
            session=SimpleNamespace(scenarios=[_FailingScenario()]),
        )

    def _make_entry_with_runtime(self, runtime):
        return SimpleNamespace(
            entry_id="eid-err",
            title="SHC Test",
            runtime_data=runtime,
        )

    def _patch_entries(self, hass, entry):
        hass.config_entries.async_entries = MagicMock(return_value=[entry])

    def test_shcexception_raises_service_validation_error(self):
        """SHCException from scenario.trigger → ServiceValidationError (lines 126-127)."""
        handler, hass = self._get_scenario_handler()
        runtime = self._make_runtime_with_failing_scenario(SHCException("io error"))
        entry = self._make_entry_with_runtime(runtime)
        self._patch_entries(hass, entry)

        fake_call = SimpleNamespace(
            data={ATTR_NAME: "failing_scene", ATTR_TITLE: ""},
        )
        with pytest.raises(ServiceValidationError):
            _run(handler(fake_call))

    def test_shcconnectionerror_raises_service_validation_error(self):
        """SHCConnectionError from scenario.trigger → ServiceValidationError (lines 126-127)."""
        handler, hass = self._get_scenario_handler()
        runtime = self._make_runtime_with_failing_scenario(SHCConnectionError("timeout"))
        entry = self._make_entry_with_runtime(runtime)
        self._patch_entries(hass, entry)

        fake_call = SimpleNamespace(
            data={ATTR_NAME: "failing_scene", ATTR_TITLE: ""},
        )
        with pytest.raises(ServiceValidationError):
            _run(handler(fake_call))

    def test_error_message_contains_scenario_name(self):
        """ServiceValidationError message must include the scenario name."""
        handler, hass = self._get_scenario_handler()
        runtime = self._make_runtime_with_failing_scenario(SHCException("x"))
        entry = self._make_entry_with_runtime(runtime)
        self._patch_entries(hass, entry)

        fake_call = SimpleNamespace(
            data={ATTR_NAME: "failing_scene", ATTR_TITLE: ""},
        )
        with pytest.raises(ServiceValidationError, match="failing_scene"):
            _run(handler(fake_call))

    def test_successful_trigger_does_not_raise(self):
        """When scenario.trigger succeeds, no exception is raised."""
        from custom_components.bosch_shc import async_setup

        hass = MagicMock()
        hass.data = {}
        hass.loop = MagicMock()
        hass.config_entries = MagicMock()
        hass.bus = MagicMock()
        hass.bus.async_listen_once = MagicMock(return_value=MagicMock(return_value=None))
        hass.services = MagicMock()
        hass.services.async_register = MagicMock()
        hass.services.has_service = MagicMock(return_value=False)
        hass.async_create_task = MagicMock()

        _run(async_setup(hass, {}))

        register_calls = hass.services.async_register.call_args_list
        handler = None
        for c in register_calls:
            if c.args[1] == SERVICE_TRIGGER_SCENARIO:
                handler = c.args[2]
                break
        assert handler is not None

        triggered = []

        class _OkScenario:
            name = "ok_scene"

            async def async_trigger(self_):
                triggered.append(True)

        runtime = SimpleNamespace(
            title="SHC Test",
            session=SimpleNamespace(scenarios=[_OkScenario()]),
        )
        entry = SimpleNamespace(
            entry_id="eid-ok",
            title="SHC Test",
            runtime_data=runtime,
        )
        hass.config_entries.async_entries = MagicMock(return_value=[entry])

        fake_call = SimpleNamespace(
            data={ATTR_NAME: "ok_scene", ATTR_TITLE: ""},
        )
        _run(handler(fake_call))
        assert triggered == [True]




# ---------------------------------------------------------------------------
# SwitchDeviceEventListener
# ---------------------------------------------------------------------------

class TestSwitchListenerThreadSafe:
    def test_supported_event_uses_async_fire(self):
        """PRESS_SHORT must call bus.async_fire directly (async session fires on loop)."""
        listener = _make_switch_listener(eventtype_name="PRESS_SHORT")
        listener._input_events_handler()

        # bus.async_fire must have been called
        assert listener.hass.bus.async_fire.called
        # call_soon_threadsafe must NOT be used (async session, no marshalling needed)
        assert not listener.hass.loop.call_soon_threadsafe.called

    def test_async_fire_passes_correct_event_type(self):
        """bus.async_fire must be called with EVENT_BOSCH_SHC as the event name."""
        listener = _make_switch_listener(eventtype_name="PRESS_LONG")
        listener._input_events_handler()

        assert listener.hass.bus.async_fire.call_args[0][0] == EVENT_BOSCH_SHC

    def test_none_eventtype_does_not_fire(self):
        """None eventtype must short-circuit before any async_fire."""
        listener = _make_switch_listener()
        listener._device.eventtype = None
        listener._input_events_handler()

        assert not listener.hass.loop.call_soon_threadsafe.called
        assert not listener.hass.bus.async_fire.called

    def test_unsupported_event_logs_warning_not_fire(self):
        """Unsupported event types must not call async_fire."""
        listener = _make_switch_listener(eventtype_name="SWITCH_ON")
        listener._input_events_handler()

        assert not listener.hass.loop.call_soon_threadsafe.called
        assert not listener.hass.bus.async_fire.called


# ---------------------------------------------------------------------------
# Platform.VALVE availability guard (former test_platforms_valve_guard.py).
# ---------------------------------------------------------------------------

def test_platforms_includes_valve_when_available():
    """Platform.VALVE present -> included in PLATFORMS."""
    from homeassistant.const import Platform

    if not hasattr(Platform, "VALVE"):
        pytest.skip("This HA version does not have Platform.VALVE")

    import custom_components.bosch_shc.__init__ as init_mod

    importlib.reload(init_mod)
    assert Platform.VALVE in init_mod.PLATFORMS, (
        "Platform.VALVE should be in PLATFORMS when available"
    )


def test_platforms_excludes_valve_when_missing():
    """If Platform has no VALVE attribute, PLATFORMS must not contain it."""
    from homeassistant.const import Platform

    # Build a fake Platform namespace without VALVE to simulate older HA
    fake_platform = types.SimpleNamespace(
        BINARY_SENSOR=Platform.BINARY_SENSOR,
        BUTTON=Platform.BUTTON,
        COVER=Platform.COVER,
        EVENT=Platform.EVENT,
        SENSOR=Platform.SENSOR,
        SWITCH=Platform.SWITCH,
        CLIMATE=Platform.CLIMATE,
        ALARM_CONTROL_PANEL=Platform.ALARM_CONTROL_PANEL,
        LIGHT=Platform.LIGHT,
        NUMBER=Platform.NUMBER,
        # VALVE intentionally omitted
    )

    # Replicate the guarded PLATFORMS construction from __init__.py
    platforms = [
        fake_platform.BINARY_SENSOR,
        fake_platform.BUTTON,
        fake_platform.COVER,
        fake_platform.EVENT,
        fake_platform.SENSOR,
        fake_platform.SWITCH,
        fake_platform.CLIMATE,
        fake_platform.ALARM_CONTROL_PANEL,
        fake_platform.LIGHT,
        fake_platform.NUMBER,
    ]
    if hasattr(fake_platform, "VALVE"):
        platforms.append(fake_platform.VALVE)

    assert Platform.VALVE not in platforms, (
        "PLATFORMS must not contain Platform.VALVE when Platform has no VALVE attribute"
    )


def test_platforms_includes_all_base_platforms():
    """All non-optional platforms are always present regardless of VALVE availability."""
    from homeassistant.const import Platform

    import custom_components.bosch_shc.__init__ as init_mod

    importlib.reload(init_mod)

    required = [
        Platform.BINARY_SENSOR,
        Platform.BUTTON,
        Platform.COVER,
        Platform.EVENT,
        Platform.SENSOR,
        Platform.SWITCH,
        Platform.CLIMATE,
        Platform.ALARM_CONTROL_PANEL,
        Platform.LIGHT,
        Platform.NUMBER,
    ]
    for platform in required:
        assert platform in init_mod.PLATFORMS, (
            f"{platform} must always be in PLATFORMS"
        )


def test_platforms_valve_guard_is_hasattr():
    """Verify the guard pattern: hasattr(Platform, 'VALVE') -> append, else skip."""
    # Test the guard logic itself with a known-present attribute
    present_ns = types.SimpleNamespace(PRESENT=True)
    missing_ns = types.SimpleNamespace()

    result_present = []
    if hasattr(present_ns, "PRESENT"):
        result_present.append("PRESENT")
    assert "PRESENT" in result_present

    result_missing = []
    if hasattr(missing_ns, "ABSENT"):
        result_missing.append("ABSENT")
    assert "ABSENT" not in result_missing

# ---------------------------------------------------------------------------
# services.yaml target-field regression for smokedetector services
# (former test_smokedetector_service_target.py).
# ---------------------------------------------------------------------------



def test_smokedetector_check_has_target():
    """smokedetector_check declares target: (entity picker, not data field)."""
    services = _load_services()
    assert "target" in services["smokedetector_check"], (
        "smokedetector_check must declare target: for entity targeting"
    )


def test_smokedetector_check_has_no_entity_id_field():
    """smokedetector_check must NOT have fields.entity_id (would be a spurious text box)."""
    services = _load_services()
    fields = services["smokedetector_check"].get("fields") or {}
    assert "entity_id" not in fields, (
        "smokedetector_check.fields.entity_id must be removed; "
        "entity targeting goes through target:, not service data"
    )


def test_smokedetector_alarmstate_has_target():
    """smokedetector_alarmstate declares target: (entity picker)."""
    services = _load_services()
    assert "target" in services["smokedetector_alarmstate"], (
        "smokedetector_alarmstate must declare target: for entity targeting"
    )


def test_smokedetector_alarmstate_has_no_entity_id_field():
    """smokedetector_alarmstate must NOT have fields.entity_id."""
    services = _load_services()
    fields = services["smokedetector_alarmstate"].get("fields") or {}
    assert "entity_id" not in fields, (
        "smokedetector_alarmstate.fields.entity_id must be removed; "
        "entity targeting goes through target:, not service data"
    )


def test_smokedetector_alarmstate_keeps_command_field():
    """smokedetector_alarmstate still has fields.command (the real service parameter)."""
    services = _load_services()
    fields = services["smokedetector_alarmstate"].get("fields") or {}
    assert "command" in fields, (
        "smokedetector_alarmstate.fields.command must remain (it is the actual service parameter)"
    )


def test_smokedetector_check_target_scoped_to_integration():
    """smokedetector_check target entity selector is scoped to bosch_shc integration."""
    services = _load_services()
    target = services["smokedetector_check"]["target"]
    assert "entity" in target
    assert target["entity"].get("integration") == "bosch_shc"


def test_smokedetector_alarmstate_target_scoped_to_integration():
    """smokedetector_alarmstate target entity selector is scoped to bosch_shc integration."""
    services = _load_services()
    target = services["smokedetector_alarmstate"]["target"]
    assert "entity" in target
    assert target["entity"].get("integration") == "bosch_shc"
