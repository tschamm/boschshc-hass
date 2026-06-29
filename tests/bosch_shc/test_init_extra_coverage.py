"""Extra coverage for custom_components/bosch_shc/__init__.py.

Covers missing lines:
- scenario_service_call — entry without runtime_data skipped (continue)
- _register_rawscan_service — early-return when already registered
- rawscan_service_call — unknown command -> ServiceValidationError
- session constructed with correct long_poll_timeout kwarg
- _scheduled_cert_check — early-return when cert_path is falsy
- _scheduled_cert_check — warning-notification branch (days_remaining <= limit)
- async_unload_entry — runtime.presence_unsub is not None -> call it

Run:
  PYTHONPATH="boschshc-hass:boschshcpy" PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \\
  python3 -m pytest tests/bosch_shc/test_init_extra_coverage.py -q -o addopts=""
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import ATTR_COMMAND, ATTR_NAME
from homeassistant.exceptions import ServiceValidationError

from custom_components.bosch_shc.const import (
    ATTR_TITLE,
    CERT_EXPIRY_WARNING_DAYS,
    OPT_LONG_POLL_TIMEOUT,
    SERVICE_TRIGGER_RAWSCAN,
    SERVICE_TRIGGER_SCENARIO,
)

# ---------------------------------------------------------------------------
# Patch targets
# NOTE: must target the package object (`custom_components.bosch_shc.X`),
# NOT `custom_components.bosch_shc.__init__.X`.  The two are distinct objects
# in Python's module cache.  async_setup_entry resolves names from the package
# object at call-time, so only patching the package object takes effect.
# Patching `dr` as a whole module replaces the alias so that `dr.async_get`
# and `dr.async_get_or_create` are automatically intercepted via the mock.
# ---------------------------------------------------------------------------

PATCH_SESSION = "custom_components.bosch_shc.SHCSessionAsync"
PATCH_DR = "custom_components.bosch_shc.dr"
PATCH_PARSE_CERT = "custom_components.bosch_shc.parse_certificate"
PATCH_TRACK_INTERVAL = "custom_components.bosch_shc.async_track_time_interval"
PATCH_IR_CREATE = "homeassistant.helpers.issue_registry.async_create_issue"


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Shared fake builders
# ---------------------------------------------------------------------------

def _make_shc_info(update_state="NO_UPDATE_AVAILABLE"):
    return SimpleNamespace(
        updateState=SimpleNamespace(name=update_state),
        unique_id="aa:bb:cc:dd:ee:ff",
        version="9.0.0",
        name="My SHC",
    )


def _make_fake_hass(*, services_has_service=False, domain_data=None):
    hass = MagicMock()
    hass.data = domain_data if domain_data is not None else {}
    hass.loop = MagicMock()

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
    hass.services.has_service = MagicMock(return_value=services_has_service)
    hass.async_create_task = MagicMock()
    return hass


def _make_fake_entry(entry_id="eid1", title="SHC Test",
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


def _make_fake_session():
    from boschshcpy import SHCSessionAsync as _SHCSessionAsync
    session = MagicMock(spec=_SHCSessionAsync)
    session.information = _make_shc_info()
    session.scenarios = []
    session.async_init = AsyncMock()
    session.start_polling = AsyncMock()
    session.stop_polling = AsyncMock()
    session.subscribe_scenario_callback = MagicMock()
    session.unsubscribe_scenario_callback = MagicMock()
    dh = MagicMock()
    dh.universal_switches = []
    session.device_helper = dh
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
    session.api = api
    return session


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
        patch(PATCH_SESSION, return_value=fake_session),
        patch(PATCH_DR, dr_mock),
        patch(PATCH_PARSE_CERT, return_value=cert_return),
        patch(PATCH_TRACK_INTERVAL, return_value=track_unsub),
        patch(PATCH_IR_CREATE, pn_create or MagicMock()),
    ):
        result = _run(async_setup_entry(hass, entry))

    return result, hass, entry


# ---------------------------------------------------------------------------
# 1 — scenario_service_call skips entry without runtime_data
# ---------------------------------------------------------------------------

class TestScenarioServiceCallSkipsNoRuntimeData:
    """scenario_service_call must skip config entries that have no runtime_data."""

    def test_entry_without_runtime_data_is_skipped(self):
        """An entry missing runtime_data must not crash the service handler."""
        from custom_components.bosch_shc import async_setup

        hass = _make_fake_hass()

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

    def test_entry_with_runtime_data_is_processed(self):
        """Entries that DO have runtime_data are processed (not skipped)."""
        from custom_components.bosch_shc import async_setup

        hass = _make_fake_hass()

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

    def test_second_call_returns_early_no_double_register(self):
        from custom_components.bosch_shc import _register_rawscan_service

        hass = _make_fake_hass()
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

    def test_first_call_registers_service(self):
        from custom_components.bosch_shc import _register_rawscan_service

        hass = _make_fake_hass()
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

    def test_unknown_command_raises_service_validation_error(self):
        """Command not in the async API dispatch map raises ServiceValidationError."""
        fake_session = _make_fake_session()

        hass = _make_fake_hass()
        entry = _make_fake_entry()
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

    def test_valid_command_does_not_raise(self):
        """A valid command must NOT raise ServiceValidationError."""
        fake_session = _make_fake_session()
        fake_session.api.get_devices = AsyncMock(return_value={"devices": []})

        hass = _make_fake_hass()
        entry = _make_fake_entry()
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

    def test_long_poll_timeout_kwarg_forwarded(self):
        """long_poll_timeout option is passed directly to SHCSessionAsync constructor."""
        from custom_components.bosch_shc import async_setup_entry

        fake_session = _make_fake_session()
        dr_mock = _make_dr_mock()
        hass = _make_fake_hass()
        entry = _make_fake_entry(options={OPT_LONG_POLL_TIMEOUT: 30})

        captured_kwargs = {}

        def _capture_constructor(*args, **kwargs):
            captured_kwargs.update(kwargs)
            return fake_session

        with (
            patch(PATCH_SESSION, side_effect=_capture_constructor),
            patch(PATCH_DR, dr_mock),
            patch(PATCH_PARSE_CERT, return_value=None),
            patch(PATCH_TRACK_INTERVAL, return_value=MagicMock()),
            patch(PATCH_IR_CREATE, MagicMock()),
        ):
            result = _run(async_setup_entry(hass, entry))

        assert result is True
        assert captured_kwargs.get("long_poll_timeout") == 30

    def test_default_long_poll_timeout_when_not_in_options(self):
        """When OPT_LONG_POLL_TIMEOUT is absent, default 10 is used."""
        from custom_components.bosch_shc import async_setup_entry

        fake_session = _make_fake_session()
        dr_mock = _make_dr_mock()
        hass = _make_fake_hass()
        entry = _make_fake_entry(options={})  # no timeout option

        captured_kwargs = {}

        def _capture_constructor(*args, **kwargs):
            captured_kwargs.update(kwargs)
            return fake_session

        with (
            patch(PATCH_SESSION, side_effect=_capture_constructor),
            patch(PATCH_DR, dr_mock),
            patch(PATCH_PARSE_CERT, return_value=None),
            patch(PATCH_TRACK_INTERVAL, return_value=MagicMock()),
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

    def _capture_cert_check_fn(self):
        """Run async_setup_entry without cert and capture the scheduled check fn."""
        from custom_components.bosch_shc import async_setup_entry

        fake_session = _make_fake_session()
        hass = _make_fake_hass()
        entry = _make_fake_entry(cert_path="")  # no cert
        dr_mock = _make_dr_mock()
        captured_fn = []

        def _capture_interval(h, fn, interval):
            captured_fn.append(fn)
            return MagicMock()

        with (
            patch(PATCH_SESSION, return_value=fake_session),
            patch(PATCH_DR, dr_mock),
            patch(PATCH_PARSE_CERT, return_value=None),
            patch(PATCH_TRACK_INTERVAL, side_effect=_capture_interval),
            patch(PATCH_IR_CREATE, MagicMock()),
        ):
            _run(async_setup_entry(hass, entry))

        assert captured_fn, "async_track_time_interval was not called"
        return captured_fn[0], hass

    def test_no_cert_check_fn_returns_early(self):
        """When cert_path='', _scheduled_cert_check must return without calling parse."""
        check_fn, hass = self._capture_cert_check_fn()

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

    def _capture_cert_check_fn_with_cert(self, cert_return):
        """Run async_setup_entry WITH a cert and capture the scheduled check fn."""
        from custom_components.bosch_shc import async_setup_entry

        fake_session = _make_fake_session()
        hass = _make_fake_hass()
        entry = _make_fake_entry(cert_path="/fake/cert.pem")
        dr_mock = _make_dr_mock()
        captured_fn = []
        ir_mock = MagicMock()

        def _capture_interval(h, fn, interval):
            captured_fn.append(fn)
            return MagicMock()

        with (
            patch(PATCH_SESSION, return_value=fake_session),
            patch(PATCH_DR, dr_mock),
            patch(PATCH_PARSE_CERT, return_value=cert_return),
            patch(PATCH_TRACK_INTERVAL, side_effect=_capture_interval),
            patch(PATCH_IR_CREATE, ir_mock),
        ):
            _run(async_setup_entry(hass, entry))

        assert captured_fn, "async_track_time_interval was not called"
        return captured_fn[0], hass, ir_mock

    def test_warning_notification_sent_when_cert_expiring_soon(self):
        """days_remaining == CERT_EXPIRY_WARNING_DAYS triggers ir.async_create_issue."""
        days = CERT_EXPIRY_WARNING_DAYS
        not_after = datetime.now(timezone.utc) + timedelta(days=days)
        warn_cert = SimpleNamespace(
            days_remaining=days,
            not_after=not_after,
        )

        check_fn, hass, _ = self._capture_cert_check_fn_with_cert(warn_cert)

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

    def test_no_notification_when_cert_has_plenty_of_time(self):
        """days_remaining > CERT_EXPIRY_WARNING_DAYS -> no ir.async_create_issue in daily check."""
        days = CERT_EXPIRY_WARNING_DAYS + 10
        not_after = datetime.now(timezone.utc) + timedelta(days=days)
        ok_cert = SimpleNamespace(
            days_remaining=days,
            not_after=not_after,
        )

        check_fn, hass, _ = self._capture_cert_check_fn_with_cert(ok_cert)

        ir_mock_daily = MagicMock()

        async def _executor_returning_cert(fn, *args):
            return ok_cert

        hass.async_add_executor_job = _executor_returning_cert

        with patch(PATCH_IR_CREATE, ir_mock_daily):
            _run(check_fn(None))

        assert not ir_mock_daily.called, (
            "ir.async_create_issue must NOT be called when cert is not near expiry"
        )

    def test_reload_triggered_when_cert_expired(self):
        """days_remaining < 0 -> hass.async_create_task called with reload."""
        days_startup = CERT_EXPIRY_WARNING_DAYS + 5
        startup_cert = SimpleNamespace(
            days_remaining=days_startup,
            not_after=datetime.now(timezone.utc) + timedelta(days=days_startup),
        )
        check_fn, hass, _ = self._capture_cert_check_fn_with_cert(startup_cert)

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

    def _make_runtime(self, *, presence_unsub=None, polling_handler=None,
                      cert_check_unsub=None):
        from custom_components.bosch_shc.data import SHCData
        fake_session = _make_fake_session()
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

    def test_presence_unsub_called_when_not_none(self):
        """runtime.presence_unsub() must be called during async_unload_entry."""
        from custom_components.bosch_shc import async_unload_entry

        presence_unsub_called = []
        presence_unsub = MagicMock(side_effect=lambda: presence_unsub_called.append(True))

        runtime, fake_session = self._make_runtime(presence_unsub=presence_unsub)

        hass = _make_fake_hass()
        entry = _make_fake_entry()
        entry.runtime_data = runtime
        hass.services.has_service = MagicMock(return_value=False)
        hass.config_entries.async_entries = MagicMock(return_value=[])

        _run(async_unload_entry(hass, entry))

        assert presence_unsub_called == [True], (
            "runtime.presence_unsub() was not called during async_unload_entry"
        )

    def test_no_presence_unsub_when_none(self):
        """When runtime.presence_unsub is None, unload must not crash."""
        from custom_components.bosch_shc import async_unload_entry

        runtime, fake_session = self._make_runtime(presence_unsub=None)

        hass = _make_fake_hass()
        entry = _make_fake_entry()
        entry.runtime_data = runtime
        hass.services.has_service = MagicMock(return_value=False)
        hass.config_entries.async_entries = MagicMock(return_value=[])

        # Must not raise
        _run(async_unload_entry(hass, entry))

    def test_polling_handler_called_when_not_none(self):
        """polling_handler() is also called during unload."""
        from custom_components.bosch_shc import async_unload_entry

        polling_handler_called = []
        polling_handler = MagicMock(
            side_effect=lambda: polling_handler_called.append(True)
        )

        runtime, _ = self._make_runtime(polling_handler=polling_handler)

        hass = _make_fake_hass()
        entry = _make_fake_entry()
        entry.runtime_data = runtime
        hass.services.has_service = MagicMock(return_value=False)
        hass.config_entries.async_entries = MagicMock(return_value=[])

        _run(async_unload_entry(hass, entry))

        assert polling_handler_called == [True]

    def test_cert_check_unsub_called_when_not_none(self):
        """cert_check_unsub() is called during unload."""
        from custom_components.bosch_shc import async_unload_entry

        cert_unsub_called = []
        cert_unsub = MagicMock(side_effect=lambda: cert_unsub_called.append(True))

        runtime, _ = self._make_runtime(cert_check_unsub=cert_unsub)

        hass = _make_fake_hass()
        entry = _make_fake_entry()
        entry.runtime_data = runtime
        hass.services.has_service = MagicMock(return_value=False)
        hass.config_entries.async_entries = MagicMock(return_value=[])

        _run(async_unload_entry(hass, entry))

        assert cert_unsub_called == [True]
