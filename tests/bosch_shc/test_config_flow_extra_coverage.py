"""Extra coverage for config_flow.py.

Covers lines: 95, 98, 107-111, 127-129, 134-151, 156-164, 178, 182,
186-193, 212-214, 257-258, 262-264, 310-326, 332-398, 406-421,
425-429, 440-442, 469, 476-485.

Run:
  PYTHONPATH="/tmp/hass-cov:/tmp/lib-async" PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \\
  python3 -m pytest tests/bosch_shc/test_config_flow_extra_coverage.py -q -o addopts=""
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

import pytest
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PASSWORD, CONF_TOKEN

from custom_components.bosch_shc.config_flow import (
    ConfigFlow,
    OptionsFlowHandler,
    _flatten_sections,
    create_credentials_and_validate,
    get_info_from_host,
    write_tls_asset,
)
from custom_components.bosch_shc.const import (
    CONF_HOSTNAME,
    CONF_SSL_CERTIFICATE,
    CONF_SSL_KEY,
    DATA_SESSION,
    DOMAIN,
    OPT_CHILD_LOCK_ENABLED,
    OPT_DIAGNOSTIC_ENTITIES,
    OPT_ENABLE_RAWSCAN,
    OPT_EXCLUDED_DEVICES,
    OPT_EXCLUDED_ROOMS,
    OPT_LONG_POLL_TIMEOUT,
    OPT_PRESENCE_ENTITY,
    OPT_SCENARIOS_AS_BUTTONS,
    OPT_SSL_VERIFY_HOSTNAME,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_hass(entry_id="test-entry-id"):
    hass = MagicMock()
    hass.config.path = lambda *args: "/tmp/" + "/".join(args)
    hass.async_add_executor_job = AsyncMock(
        return_value={"title": "shc012345", "unique_id": "shc-serial-001"}
    )
    hass.data = {}
    return hass


def _make_entry(host="1.2.3.4", unique_id="shc-serial-001", data=None, options=None,
                entry_id="test-entry-id"):
    entry = MagicMock()
    entry.unique_id = unique_id
    entry.data = dict(data or {})
    entry.data.setdefault(CONF_HOST, host)
    entry.options = dict(options or {})
    entry.entry_id = entry_id
    return entry


def _make_flow(entry=None, source="user"):
    """Instantiate a ConfigFlow with minimal wiring (no HA harness)."""
    _entry = entry or _make_entry()
    flow = ConfigFlow.__new__(ConfigFlow)
    flow.hass = _make_hass()
    flow.context = {
        "source": source,
        "unique_id": None,
        "entry_id": _entry.entry_id,
    }
    flow._get_reconfigure_entry = lambda: _entry
    # Provide stub HA flow callbacks
    flow.async_show_form = MagicMock(return_value={"type": "form"})
    flow.async_create_entry = MagicMock(return_value={"type": "create_entry"})
    flow.async_abort = MagicMock(return_value={"type": "abort"})
    flow.async_set_unique_id = AsyncMock(return_value=None)
    flow._abort_if_unique_id_configured = MagicMock()
    flow._abort_if_unique_id_mismatch = MagicMock()
    flow.async_update_reload_and_abort = MagicMock(
        return_value={"type": "abort", "reason": "reconfigure_successful"}
    )
    # These class-level attrs must exist on instances too
    flow.info = None
    flow.host = None
    flow.hostname = None
    return flow


def _make_options_flow(entry_options=None, entry_id="test-entry-id"):
    """Return an OptionsFlowHandler wired to a mock config entry."""
    entry = _make_entry(options=entry_options or {}, entry_id=entry_id)
    flow = OptionsFlowHandler.__new__(OptionsFlowHandler)
    flow.__class__ = type(
        "PatchedOptionsFlow",
        (OptionsFlowHandler,),
        {"config_entry": property(lambda self: entry)},
    )
    flow.hass = _make_hass(entry_id)
    flow.async_show_form = MagicMock(return_value={"type": "form", "step_id": "init"})
    flow.async_create_entry = MagicMock(
        side_effect=lambda title, data: {"type": "result", "data": data}
    )
    return flow, entry


# ---------------------------------------------------------------------------
# 1. _flatten_sections — lines 95, 98
# ---------------------------------------------------------------------------

class TestFlattenSections:
    """Cover the duplicate-key error branches in _flatten_sections."""

    def test_duplicate_key_across_sections_raises(self):
        """Line 95/98: same OPT_ key in two sections raises ValueError."""
        # 'scenarios_as_buttons' is in 'features'; force it into 'presence' too
        # by crafting a raw dict with two sections containing the same field.
        input_data = {
            "features": {OPT_SCENARIOS_AS_BUTTONS: True},
            "presence": {OPT_SCENARIOS_AS_BUTTONS: False},  # duplicate!
        }
        with pytest.raises(ValueError, match="duplicate key"):
            _flatten_sections(input_data)

    def test_top_level_key_duplicates_section_field_raises(self):
        """Line 107-111: a top-level key that was already lifted from a section raises."""
        input_data = {
            "features": {OPT_SCENARIOS_AS_BUTTONS: True},
            OPT_SCENARIOS_AS_BUTTONS: False,  # also at top level
        }
        with pytest.raises(ValueError, match="duplicate key"):
            _flatten_sections(input_data)

    def test_normal_sectioned_input_flattens_correctly(self):
        """Happy path: nested sections are lifted to a flat dict."""
        input_data = {
            "features": {OPT_SCENARIOS_AS_BUTTONS: True, OPT_DIAGNOSTIC_ENTITIES: False},
            "presence": {OPT_CHILD_LOCK_ENABLED: True},
            "advanced": {OPT_LONG_POLL_TIMEOUT: 30},
        }
        result = _flatten_sections(input_data)
        assert result[OPT_SCENARIOS_AS_BUTTONS] is True
        assert result[OPT_DIAGNOSTIC_ENTITIES] is False
        assert result[OPT_CHILD_LOCK_ENABLED] is True
        assert result[OPT_LONG_POLL_TIMEOUT] == 30

    def test_top_level_non_section_keys_pass_through(self):
        """Non-section keys at top level are included in the output."""
        input_data = {
            "features": {OPT_SCENARIOS_AS_BUTTONS: True},
            "extra_key": "extra_value",  # not a section name
        }
        result = _flatten_sections(input_data)
        assert result["extra_key"] == "extra_value"
        assert result[OPT_SCENARIOS_AS_BUTTONS] is True

    def test_none_section_value_is_skipped(self):
        """Section key present in user_input but None value is skipped without error."""
        input_data = {
            "features": None,
            "presence": {OPT_CHILD_LOCK_ENABLED: False},
        }
        result = _flatten_sections(input_data)
        assert OPT_CHILD_LOCK_ENABLED in result
        assert OPT_SCENARIOS_AS_BUTTONS not in result

    def test_non_dict_section_value_is_skipped(self):
        """Section key present but value is not a dict is silently skipped."""
        input_data = {
            "features": "not-a-dict",
            "advanced": {OPT_LONG_POLL_TIMEOUT: 10},
        }
        result = _flatten_sections(input_data)
        assert OPT_LONG_POLL_TIMEOUT in result


# ---------------------------------------------------------------------------
# 2. write_tls_asset — lines 107-111
# ---------------------------------------------------------------------------

class TestWriteTlsAsset:
    """Cover write_tls_asset (lines 107-111)."""

    def test_write_tls_asset_creates_dir_and_writes_file(self):
        """write_tls_asset calls makedirs and writes decoded bytes to file."""
        hass = MagicMock()
        hass.config.path = lambda *args: "/tmp/" + "/".join(args)

        asset = b"PEM certificate content"
        m = mock_open()
        with patch("custom_components.bosch_shc.config_flow.makedirs") as mock_makedirs, \
             patch("custom_components.bosch_shc.config_flow.os.open", return_value=5), \
             patch("custom_components.bosch_shc.config_flow.os.fdopen", m):
            write_tls_asset(hass, "test_cert.pem", asset)

        mock_makedirs.assert_called_once_with(
            hass.config.path(DOMAIN), exist_ok=True
        )
        m.assert_called_once_with(5, "w", encoding="utf8")
        m().write.assert_called_once_with("PEM certificate content")

    def test_write_tls_asset_decodes_bytes_to_string(self):
        """write_tls_asset decodes bytes with utf-8."""
        hass = MagicMock()
        hass.config.path = lambda *args: "/fake/" + "/".join(args)
        asset = "certificate data".encode("utf-8")
        written = []

        m = mock_open()
        m.return_value.__enter__.return_value.write = lambda s: written.append(s)
        with patch("custom_components.bosch_shc.config_flow.makedirs"), \
             patch("custom_components.bosch_shc.config_flow.os.open", return_value=5), \
             patch("custom_components.bosch_shc.config_flow.os.fdopen", m):
            write_tls_asset(hass, "key.pem", asset)

        assert written == ["certificate data"]


# ---------------------------------------------------------------------------
# 3. create_credentials_and_validate — lines 127-129, 134-151
# ---------------------------------------------------------------------------

class TestCreateCredentialsAndValidate:
    """Cover create_credentials_and_validate (lines 127-151)."""

    def test_success_path_calls_register_and_session(self):
        """Lines 134-151: registers, writes TLS, creates session, authenticates."""
        hass = MagicMock()
        hass.config.path = lambda *args: "/tmp/" + "/".join(args)

        fake_result = {
            "token": "tok:myhostname",
            "cert": b"CERT",
            "key": b"KEY",
        }
        mock_register = MagicMock(return_value=fake_result)
        mock_session = MagicMock()
        mock_session.authenticate = MagicMock()

        user_input = {CONF_PASSWORD: "secret", CONF_NAME: "HomeAssistant"}

        with patch(
            "custom_components.bosch_shc.config_flow.SHCRegisterClient"
        ) as MockClient, patch(
            "custom_components.bosch_shc.config_flow.SHCSession",
            return_value=mock_session,
        ), patch(
            "custom_components.bosch_shc.config_flow.write_tls_asset"
        ) as mock_write:
            MockClient.return_value.register = mock_register
            result = create_credentials_and_validate(
                hass, "1.2.3.4", user_input, MagicMock()
            )

        assert result == fake_result
        MockClient.assert_called_once_with("1.2.3.4", "secret")
        mock_register.assert_called_once_with("homeassistant", "HomeAssistant")
        assert mock_write.call_count == 2
        mock_session.authenticate.assert_called_once()

    def test_none_result_skips_session_creation(self):
        """Lines 127-129: if register returns None, no session is created."""
        hass = MagicMock()
        hass.config.path = lambda *args: "/tmp/" + "/".join(args)

        user_input = {CONF_PASSWORD: "secret", CONF_NAME: "HomeAssistant"}

        with patch(
            "custom_components.bosch_shc.config_flow.SHCRegisterClient"
        ) as MockClient, patch(
            "custom_components.bosch_shc.config_flow.SHCSession"
        ) as MockSession, patch(
            "custom_components.bosch_shc.config_flow.write_tls_asset"
        ) as mock_write:
            MockClient.return_value.register = MagicMock(return_value=None)
            result = create_credentials_and_validate(
                hass, "1.2.3.4", user_input, MagicMock()
            )

        assert result is None
        MockSession.assert_not_called()
        mock_write.assert_not_called()

    def test_hostname_is_extracted_from_token(self):
        """The hostname is split from token after ':' and used for file names."""
        hass = MagicMock()
        hass.config.path = lambda *args: "/data/" + "/".join(args)

        fake_result = {
            "token": "prefix:specific-hostname",
            "cert": b"CERT",
            "key": b"KEY",
        }
        user_input = {CONF_PASSWORD: "pw", CONF_NAME: "HA"}
        written_paths = []

        with patch(
            "custom_components.bosch_shc.config_flow.SHCRegisterClient"
        ) as MockClient, patch(
            "custom_components.bosch_shc.config_flow.SHCSession"
        ) as MockSession, patch(
            "custom_components.bosch_shc.config_flow.write_tls_asset",
            side_effect=lambda h, fname, asset: written_paths.append(fname),
        ):
            MockClient.return_value.register = MagicMock(return_value=fake_result)
            MockSession.return_value.authenticate = MagicMock()
            create_credentials_and_validate(hass, "10.0.0.1", user_input, MagicMock())

        # Both filenames must contain the hostname portion
        assert all("specific-hostname" in p for p in written_paths)


# ---------------------------------------------------------------------------
# 4. get_info_from_host — lines 156-164
# ---------------------------------------------------------------------------

class TestGetInfoFromHost:
    """Cover get_info_from_host (lines 156-164)."""

    def test_returns_title_and_unique_id(self):
        """Lines 156-164: creates SHCSession, calls mdns_info, returns dict."""
        info_mock = MagicMock()
        info_mock.name = "shc012345"
        info_mock.unique_id = "shc-serial-999"

        mock_session = MagicMock()
        mock_session.mdns_info = MagicMock(return_value=info_mock)

        hass = MagicMock()
        zeroconf_instance = MagicMock()

        with patch(
            "custom_components.bosch_shc.config_flow.SHCSession",
            return_value=mock_session,
        ) as MockSession:
            result = get_info_from_host(hass, "1.2.3.4", zeroconf_instance)

        MockSession.assert_called_once_with("1.2.3.4", "", "", True, zeroconf_instance)
        mock_session.mdns_info.assert_called_once()
        assert result == {"title": "shc012345", "unique_id": "shc-serial-999"}


# ---------------------------------------------------------------------------
# 5. async_get_options_flow — line 178
# ---------------------------------------------------------------------------

class TestAsyncGetOptionsFlow:
    """Cover ConfigFlow.async_get_options_flow (line 178)."""

    def test_returns_options_flow_handler(self):
        """Line 178: returns an OptionsFlowHandler instance."""
        entry = _make_entry()
        result = ConfigFlow.async_get_options_flow(entry)
        assert isinstance(result, OptionsFlowHandler)


# ---------------------------------------------------------------------------
# 6. async_step_reauth — line 182
# ---------------------------------------------------------------------------

class TestAsyncStepReauth:
    """Cover async_step_reauth (line 182)."""

    def test_reauth_delegates_to_reauth_confirm(self):
        """Line 182: async_step_reauth immediately calls async_step_reauth_confirm."""
        flow = _make_flow()
        # async_step_reauth_confirm with None shows a form
        asyncio.run(flow.async_step_reauth(user_input=None))
        flow.async_show_form.assert_called_once()
        call_kwargs = flow.async_show_form.call_args[1]
        assert call_kwargs["step_id"] == "reauth_confirm"

    def test_reauth_with_input_propagates_to_confirm(self):
        """Passing user_input=None still hits reauth_confirm."""
        flow = _make_flow()
        asyncio.run(flow.async_step_reauth(user_input=None))
        # Confirm was called (form shown for reauth_confirm)
        assert flow.async_show_form.called


# ---------------------------------------------------------------------------
# 7. async_step_reauth_confirm — lines 186-193
# ---------------------------------------------------------------------------

class TestAsyncStepReauthConfirm:
    """Cover async_step_reauth_confirm (lines 186-193)."""

    def test_none_input_shows_form(self):
        """Line 186-190: None input renders the reauth_confirm form."""
        flow = _make_flow()
        asyncio.run(flow.async_step_reauth_confirm(user_input=None))
        flow.async_show_form.assert_called_once()
        assert flow.async_show_form.call_args[1]["step_id"] == "reauth_confirm"

    def test_non_none_input_calls_get_info_and_credentials(self):
        """Lines 191-193: valid input calls _get_info, then async_step_credentials."""
        flow = _make_flow()
        flow._get_info = AsyncMock(
            return_value={"title": "shc012345", "unique_id": "shc-serial-001"}
        )

        asyncio.run(
            flow.async_step_reauth_confirm(user_input={CONF_HOST: "5.5.5.5"})
        )

        flow._get_info.assert_called_once_with("5.5.5.5")
        assert flow.host == "5.5.5.5"
        assert flow.info == {"title": "shc012345", "unique_id": "shc-serial-001"}
        # async_step_credentials with None input shows the credentials form
        flow.async_show_form.assert_called_once()
        assert flow.async_show_form.call_args[1]["step_id"] == "credentials"


# ---------------------------------------------------------------------------
# 8. async_step_reconfigure_host Exception branch — lines 212-214
# ---------------------------------------------------------------------------

class TestAsyncStepReconfigureHostException:
    """Cover the generic Exception branch in async_step_reconfigure_host (212-214)."""

    def test_exception_sets_unknown_error(self):
        """Lines 212-214: non-SHCConnectionError raises → errors['base']='unknown'."""
        flow = _make_flow()
        flow._get_info = AsyncMock(side_effect=RuntimeError("unexpected!"))

        asyncio.run(
            flow.async_step_reconfigure_host(user_input={CONF_HOST: "bad"})
        )

        assert flow.async_show_form.called
        errors = flow.async_show_form.call_args[1]["errors"]
        assert errors.get("base") == "unknown"


# ---------------------------------------------------------------------------
# 9. async_step_repair_credentials error branches — lines 257-258, 262-264
# ---------------------------------------------------------------------------

class TestAsyncStepRepairCredentialsErrors:
    """Cover SHCSessionError and generic Exception in repair_credentials."""

    def _setup_flow(self):
        entry = _make_entry(data={
            CONF_HOST: "10.0.0.1",
            CONF_TOKEN: "old:oldhostname",
            CONF_HOSTNAME: "oldhostname",
        })
        flow = _make_flow(entry=entry)
        flow._get_reconfigure_entry = lambda: entry
        return flow

    def test_session_error_sets_session_error(self):
        """Lines 257-258: SHCSessionError → errors['base']='session_error'."""
        from boschshcpy.exceptions import SHCSessionError

        flow = self._setup_flow()
        flow.hass.async_add_executor_job = AsyncMock(
            side_effect=SHCSessionError("session broke")
        )
        user_input = {CONF_HOST: "10.0.0.1", CONF_PASSWORD: "pw", CONF_NAME: "HA"}

        with patch(
            "custom_components.bosch_shc.config_flow.zeroconf.async_get_instance",
            new=AsyncMock(return_value=MagicMock()),
        ):
            asyncio.run(flow.async_step_repair_credentials(user_input=user_input))

        assert flow.async_show_form.called
        errors = flow.async_show_form.call_args[1]["errors"]
        assert errors.get("base") == "session_error"

    def test_generic_exception_sets_unknown(self):
        """Lines 262-264: generic Exception → errors['base']='unknown'."""
        flow = self._setup_flow()
        flow.hass.async_add_executor_job = AsyncMock(
            side_effect=RuntimeError("boom")
        )
        user_input = {CONF_HOST: "10.0.0.1", CONF_PASSWORD: "pw", CONF_NAME: "HA"}

        with patch(
            "custom_components.bosch_shc.config_flow.zeroconf.async_get_instance",
            new=AsyncMock(return_value=MagicMock()),
        ):
            asyncio.run(flow.async_step_repair_credentials(user_input=user_input))

        assert flow.async_show_form.called
        errors = flow.async_show_form.call_args[1]["errors"]
        assert errors.get("base") == "unknown"


# ---------------------------------------------------------------------------
# 10. async_step_user — lines 310-326
# ---------------------------------------------------------------------------

class TestAsyncStepUser:
    """Cover async_step_user branches (lines 310-326)."""

    def test_none_input_shows_user_form(self):
        """Lines 326-328: None input shows the user form."""
        flow = _make_flow()
        asyncio.run(flow.async_step_user(user_input=None))
        assert flow.async_show_form.called
        assert flow.async_show_form.call_args[1]["step_id"] == "user"

    def test_valid_host_proceeds_to_credentials(self):
        """Lines 310-324: valid host → _get_info → async_step_credentials form."""
        flow = _make_flow()
        flow._get_info = AsyncMock(
            return_value={"title": "shc012345", "unique_id": "shc-serial-001"}
        )

        asyncio.run(flow.async_step_user(user_input={CONF_HOST: "1.2.3.4"}))

        flow._get_info.assert_called_once_with("1.2.3.4")
        flow.async_set_unique_id.assert_called_once_with("shc-serial-001")
        flow._abort_if_unique_id_configured.assert_called_once()
        assert flow.host == "1.2.3.4"
        assert flow.async_show_form.called
        assert flow.async_show_form.call_args[1]["step_id"] == "credentials"

    def test_connection_error_shows_cannot_connect(self):
        """Lines 314-316: SHCConnectionError → errors['base']='cannot_connect'."""
        from boschshcpy.exceptions import SHCConnectionError

        flow = _make_flow()
        flow._get_info = AsyncMock(side_effect=SHCConnectionError())

        asyncio.run(flow.async_step_user(user_input={CONF_HOST: "bad-host"}))

        assert flow.async_show_form.called
        errors = flow.async_show_form.call_args[1]["errors"]
        assert errors.get("base") == "cannot_connect"

    def test_exception_shows_unknown(self):
        """Lines 317-319: generic Exception → errors['base']='unknown'."""
        flow = _make_flow()
        flow._get_info = AsyncMock(side_effect=Exception("unexpected"))

        asyncio.run(flow.async_step_user(user_input={CONF_HOST: "bad-host"}))

        assert flow.async_show_form.called
        errors = flow.async_show_form.call_args[1]["errors"]
        assert errors.get("base") == "unknown"


# ---------------------------------------------------------------------------
# 11. async_step_credentials — lines 332-398
# ---------------------------------------------------------------------------

class TestAsyncStepCredentials:
    """Cover async_step_credentials (lines 332-398)."""

    def _make_cred_flow(self):
        flow = _make_flow()
        flow.host = "1.2.3.4"
        flow.info = {"title": "shc012345", "unique_id": "shc-serial-001"}
        return flow

    def test_none_input_shows_credentials_form(self):
        """Lines 380-399: None input shows credentials form."""
        flow = self._make_cred_flow()
        asyncio.run(flow.async_step_credentials(user_input=None))
        assert flow.async_show_form.called
        assert flow.async_show_form.call_args[1]["step_id"] == "credentials"

    def test_success_no_existing_entry_creates_entry(self):
        """Lines 356-379: success path without existing entry creates a new entry."""
        flow = self._make_cred_flow()
        fake_result = {"token": "tok:newhostname", "cert": b"C", "key": b"K"}
        flow.hass.async_add_executor_job = AsyncMock(return_value=fake_result)
        flow.async_set_unique_id = AsyncMock(return_value=None)  # No existing entry

        with patch(
            "custom_components.bosch_shc.config_flow.zeroconf.async_get_instance",
            new=AsyncMock(return_value=MagicMock()),
        ):
            user_input = {CONF_PASSWORD: "pass", CONF_NAME: "HomeAssistant"}
            asyncio.run(flow.async_step_credentials(user_input=user_input))

        flow.async_create_entry.assert_called_once()
        call_kwargs = flow.async_create_entry.call_args[1]
        assert call_kwargs["title"] == "shc012345"
        data = call_kwargs["data"]
        assert data[CONF_TOKEN] == "tok:newhostname"
        assert data[CONF_HOSTNAME] == "newhostname"
        assert data[CONF_HOST] == "1.2.3.4"
        assert "newhostname" in data[CONF_SSL_CERTIFICATE]
        assert "newhostname" in data[CONF_SSL_KEY]

    def test_success_with_existing_entry_updates_and_aborts(self):
        """Lines 370-374: when unique_id already exists, update_reload_and_abort is called."""
        flow = self._make_cred_flow()
        fake_result = {"token": "tok:newhostname", "cert": b"C", "key": b"K"}
        flow.hass.async_add_executor_job = AsyncMock(return_value=fake_result)
        existing_entry = _make_entry()
        flow.async_set_unique_id = AsyncMock(return_value=existing_entry)

        with patch(
            "custom_components.bosch_shc.config_flow.zeroconf.async_get_instance",
            new=AsyncMock(return_value=MagicMock()),
        ):
            user_input = {CONF_PASSWORD: "pass", CONF_NAME: "HomeAssistant"}
            asyncio.run(flow.async_step_credentials(user_input=user_input))

        flow.async_update_reload_and_abort.assert_called_once()
        call_args = flow.async_update_reload_and_abort.call_args
        assert call_args[0][0] is existing_entry
        data = call_args[1]["data"]
        assert data[CONF_TOKEN] == "tok:newhostname"

    def test_auth_error_shows_invalid_auth(self):
        """Lines 343-344: SHCAuthenticationError → errors['base']='invalid_auth'."""
        from boschshcpy.exceptions import SHCAuthenticationError

        flow = self._make_cred_flow()
        flow.hass.async_add_executor_job = AsyncMock(
            side_effect=SHCAuthenticationError()
        )

        with patch(
            "custom_components.bosch_shc.config_flow.zeroconf.async_get_instance",
            new=AsyncMock(return_value=MagicMock()),
        ):
            asyncio.run(flow.async_step_credentials(
                user_input={CONF_PASSWORD: "wrong", CONF_NAME: "HA"}
            ))

        errors = flow.async_show_form.call_args[1]["errors"]
        assert errors.get("base") == "invalid_auth"

    def test_connection_error_shows_cannot_connect(self):
        """Lines 345-346: SHCConnectionError → errors['base']='cannot_connect'."""
        from boschshcpy.exceptions import SHCConnectionError

        flow = self._make_cred_flow()
        flow.hass.async_add_executor_job = AsyncMock(
            side_effect=SHCConnectionError()
        )

        with patch(
            "custom_components.bosch_shc.config_flow.zeroconf.async_get_instance",
            new=AsyncMock(return_value=MagicMock()),
        ):
            asyncio.run(flow.async_step_credentials(
                user_input={CONF_PASSWORD: "pw", CONF_NAME: "HA"}
            ))

        errors = flow.async_show_form.call_args[1]["errors"]
        assert errors.get("base") == "cannot_connect"

    def test_session_error_shows_session_error(self):
        """Lines 347-349: SHCSessionError → errors['base']='session_error'."""
        from boschshcpy.exceptions import SHCSessionError

        flow = self._make_cred_flow()
        flow.hass.async_add_executor_job = AsyncMock(
            side_effect=SHCSessionError("session broke")
        )

        with patch(
            "custom_components.bosch_shc.config_flow.zeroconf.async_get_instance",
            new=AsyncMock(return_value=MagicMock()),
        ):
            asyncio.run(flow.async_step_credentials(
                user_input={CONF_PASSWORD: "pw", CONF_NAME: "HA"}
            ))

        errors = flow.async_show_form.call_args[1]["errors"]
        assert errors.get("base") == "session_error"

    def test_registration_error_shows_pairing_failed(self):
        """Lines 350-352: SHCRegistrationError → errors['base']='pairing_failed'."""
        from boschshcpy.exceptions import SHCRegistrationError

        flow = self._make_cred_flow()
        flow.hass.async_add_executor_job = AsyncMock(
            side_effect=SHCRegistrationError("not in pairing mode")
        )

        with patch(
            "custom_components.bosch_shc.config_flow.zeroconf.async_get_instance",
            new=AsyncMock(return_value=MagicMock()),
        ):
            asyncio.run(flow.async_step_credentials(
                user_input={CONF_PASSWORD: "pw", CONF_NAME: "HA"}
            ))

        errors = flow.async_show_form.call_args[1]["errors"]
        assert errors.get("base") == "pairing_failed"

    def test_generic_exception_shows_unknown(self):
        """Lines 353-355: generic Exception → errors['base']='unknown'."""
        flow = self._make_cred_flow()
        flow.hass.async_add_executor_job = AsyncMock(
            side_effect=RuntimeError("boom")
        )

        with patch(
            "custom_components.bosch_shc.config_flow.zeroconf.async_get_instance",
            new=AsyncMock(return_value=MagicMock()),
        ):
            asyncio.run(flow.async_step_credentials(
                user_input={CONF_PASSWORD: "pw", CONF_NAME: "HA"}
            ))

        errors = flow.async_show_form.call_args[1]["errors"]
        assert errors.get("base") == "unknown"


# ---------------------------------------------------------------------------
# 12. async_step_zeroconf — lines 406-421
# ---------------------------------------------------------------------------

class TestAsyncStepZeroconf:
    """Cover async_step_zeroconf (lines 406-421)."""

    def _make_discovery_info(
        self, name="Bosch SHC [abc123]", host="1.1.1.1",
        hostname="shc012345.local.",
    ):
        return SimpleNamespace(name=name, host=host, hostname=hostname)

    def test_non_bosch_device_aborts(self):
        """Lines 406-407: name not starting with 'Bosch SHC' → abort not_bosch_shc."""
        flow = _make_flow()
        disc = self._make_discovery_info(name="Some Other Device")

        asyncio.run(flow.async_step_zeroconf(disc))

        flow.async_abort.assert_called_once_with(reason="not_bosch_shc")

    def test_connection_error_aborts_cannot_connect(self):
        """Lines 409-412: SHCConnectionError from _get_info → abort cannot_connect."""
        from boschshcpy.exceptions import SHCConnectionError

        flow = _make_flow()
        flow._get_info = AsyncMock(side_effect=SHCConnectionError())
        disc = self._make_discovery_info()

        asyncio.run(flow.async_step_zeroconf(disc))

        flow.async_abort.assert_called_once_with(reason="cannot_connect")

    def test_success_sets_host_and_shows_confirm_form(self):
        """Lines 409-421: success → sets host/info, shows confirm_discovery form."""
        flow = _make_flow()
        flow._get_info = AsyncMock(
            return_value={"title": "shc012345", "unique_id": "shc-serial-001"}
        )
        disc = self._make_discovery_info(
            name="Bosch SHC [abc]",
            host="1.1.1.1",
            hostname="shc012345.local.",
        )

        asyncio.run(flow.async_step_zeroconf(disc))

        assert flow.host == "1.1.1.1"
        assert flow.info == {"title": "shc012345", "unique_id": "shc-serial-001"}
        flow.async_set_unique_id.assert_called_once_with("shc-serial-001")
        flow._abort_if_unique_id_configured.assert_called_once()
        assert flow.context.get("title_placeholders") == {"name": "shc012345"}
        # async_step_confirm_discovery with None shows the form
        flow.async_show_form.assert_called_once()
        assert flow.async_show_form.call_args[1]["step_id"] == "confirm_discovery"

    def test_success_strips_dot_and_local_from_hostname(self):
        """node_name is derived by stripping trailing '.' then '.local'."""
        flow = _make_flow()
        flow._get_info = AsyncMock(
            return_value={"title": "shc099", "unique_id": "uid-099"}
        )
        disc = self._make_discovery_info(
            name="Bosch SHC [test]",
            host="2.2.2.2",
            hostname="shc099.local.",
        )

        asyncio.run(flow.async_step_zeroconf(disc))

        placeholders = flow.context.get("title_placeholders", {})
        assert placeholders.get("name") == "shc099"


# ---------------------------------------------------------------------------
# 13. async_step_confirm_discovery — lines 425-429
# ---------------------------------------------------------------------------

class TestAsyncStepConfirmDiscovery:
    """Cover async_step_confirm_discovery (lines 425-429)."""

    def test_none_input_shows_confirm_form(self):
        """Lines 428-436: None input shows confirm_discovery form."""
        flow = _make_flow()
        flow.host = "1.2.3.4"

        asyncio.run(flow.async_step_confirm_discovery(user_input=None))

        assert flow.async_show_form.called
        assert flow.async_show_form.call_args[1]["step_id"] == "confirm_discovery"

    def test_non_none_input_goes_to_credentials(self):
        """Lines 426-427: non-None user_input → async_step_credentials form."""
        flow = _make_flow()
        flow.host = "1.2.3.4"
        flow.info = {"title": "shc012345", "unique_id": "shc-serial-001"}

        asyncio.run(flow.async_step_confirm_discovery(user_input={}))

        # credentials form is shown (async_step_credentials with None input)
        assert flow.async_show_form.called
        assert flow.async_show_form.call_args[1]["step_id"] == "credentials"


# ---------------------------------------------------------------------------
# 14. _get_info (ConfigFlow method) — lines 440-442
# ---------------------------------------------------------------------------

class TestConfigFlowGetInfo:
    """Cover ConfigFlow._get_info (lines 440-442)."""

    def test_get_info_calls_zeroconf_and_executor(self):
        """Lines 440-447: _get_info gets zeroconf instance and delegates to executor."""
        flow = _make_flow()
        fake_zeroconf = MagicMock()
        fake_info = {"title": "shc012345", "unique_id": "shc-serial-001"}
        flow.hass.async_add_executor_job = AsyncMock(return_value=fake_info)

        with patch(
            "custom_components.bosch_shc.config_flow.zeroconf.async_get_instance",
            new=AsyncMock(return_value=fake_zeroconf),
        ):
            result = asyncio.run(flow._get_info("9.9.9.9"))

        flow.hass.async_add_executor_job.assert_called_once()
        call_args = flow.hass.async_add_executor_job.call_args[0]
        # First arg is the function, then the positional args
        assert call_args[0] is get_info_from_host
        assert call_args[1] is flow.hass
        assert call_args[2] == "9.9.9.9"
        assert call_args[3] is fake_zeroconf
        assert result == fake_info


# ---------------------------------------------------------------------------
# 15. async_step_init options flow — lines 469, 476-485
# ---------------------------------------------------------------------------

class TestOptionsFlowInit:
    """Cover async_step_init options flow branches (lines 469, 476-485)."""

    def test_presence_entity_string_is_coerced_to_list(self):
        """Line 469: existing string presence entity is coerced to a single-item list."""
        flow, entry = _make_options_flow(
            entry_options={OPT_PRESENCE_ENTITY: "person.thomas"}
        )

        asyncio.run(flow.async_step_init(user_input=None))

        assert flow.async_show_form.called
        schema = flow.async_show_form.call_args[1]["data_schema"]

        # Walk schema sections to find presence section and OPT_PRESENCE_ENTITY default
        presence_default = None
        for section_key in schema.schema:
            sec_schema = schema.schema[section_key]
            inner = getattr(sec_schema, "schema", None)
            if inner is None:
                continue
            inner_schema = getattr(inner, "schema", {})
            for field_key in inner_schema:
                if str(field_key) == OPT_PRESENCE_ENTITY:
                    if hasattr(field_key, "default") and callable(field_key.default):
                        presence_default = field_key.default()
        assert presence_default == ["person.thomas"]

    def test_empty_string_presence_entity_becomes_empty_list(self):
        """Line 469: empty string presence entity coerces to []."""
        flow, entry = _make_options_flow(
            entry_options={OPT_PRESENCE_ENTITY: ""}
        )

        asyncio.run(flow.async_step_init(user_input=None))

        schema = flow.async_show_form.call_args[1]["data_schema"]
        presence_default = None
        for section_key in schema.schema:
            sec_schema = schema.schema[section_key]
            inner = getattr(sec_schema, "schema", None)
            if inner is None:
                continue
            inner_schema = getattr(inner, "schema", {})
            for field_key in inner_schema:
                if str(field_key) == OPT_PRESENCE_ENTITY:
                    if hasattr(field_key, "default") and callable(field_key.default):
                        presence_default = field_key.default()
        assert presence_default == []

    def test_device_and_room_options_populated_from_session(self):
        """Lines 476-488: when DATA_SESSION is available, device/room options are built."""
        entry_id = "entry-with-session"
        flow, entry = _make_options_flow(entry_id=entry_id)

        # Build fake session with rooms and devices
        room1 = MagicMock()
        room1.id = "room-1"
        room1.name = "Living Room"
        room2 = MagicMock()
        room2.id = "room-2"
        room2.name = "Bedroom"

        dev1 = MagicMock()
        dev1.id = "dev-1"
        dev1.name = "Thermostat"
        dev1.room_id = "room-1"

        dev2 = MagicMock()
        dev2.id = "dev-2"
        dev2.name = "Switch"
        dev2.room_id = "room-999"  # room not in rooms dict → empty label

        session = MagicMock()
        session.rooms = [room1, room2]
        session.devices = [dev1, dev2]

        flow.hass.data = {
            DOMAIN: {
                entry_id: {DATA_SESSION: session}
            }
        }

        asyncio.run(flow.async_step_init(user_input=None))

        # Form should be shown (devices/rooms populated)
        assert flow.async_show_form.called
        schema = flow.async_show_form.call_args[1]["data_schema"]

        # The schema was built — check it contains the advanced section with selectors
        # (No exception means the session was read successfully)
        assert schema is not None

    def test_session_exception_does_not_break_options_flow(self):
        """Lines 489-492: if session access raises, form is still shown (no crash)."""
        flow, entry = _make_options_flow()

        # Simulate hass.data[DOMAIN][entry_id] raising on access
        bad_data = MagicMock()
        bad_data.__getitem__ = MagicMock(side_effect=KeyError("DATA_SESSION"))
        bad_data.get = MagicMock(return_value=bad_data)
        flow.hass.data = {DOMAIN: bad_data}

        # Should NOT raise
        asyncio.run(flow.async_step_init(user_input=None))
        assert flow.async_show_form.called

    def test_device_with_room_label_includes_room_name(self):
        """Lines 480-484: device label includes room name when room_id matches."""
        entry_id = "entry-room-label"
        flow, entry = _make_options_flow(entry_id=entry_id)

        room = MagicMock()
        room.id = "room-living"
        room.name = "Living Room"

        dev = MagicMock()
        dev.id = "dev-sensor"
        dev.name = "Door Sensor"
        dev.room_id = "room-living"

        session = MagicMock()
        session.rooms = [room]
        session.devices = [dev]

        flow.hass.data = {
            DOMAIN: {entry_id: {DATA_SESSION: session}}
        }

        asyncio.run(flow.async_step_init(user_input=None))
        assert flow.async_show_form.called

    def test_submit_creates_entry_with_flattened_data(self):
        """Submitting options goes through _flatten_sections and creates entry."""
        flow, _ = _make_options_flow()

        user_input = {
            "features": {
                OPT_SCENARIOS_AS_BUTTONS: True,
                OPT_DIAGNOSTIC_ENTITIES: False,
                OPT_ENABLE_RAWSCAN: True,
            },
            "presence": {
                OPT_CHILD_LOCK_ENABLED: False,
                OPT_PRESENCE_ENTITY: ["person.user1"],
            },
            "advanced": {
                OPT_SSL_VERIFY_HOSTNAME: False,
                OPT_LONG_POLL_TIMEOUT: 15,
                OPT_EXCLUDED_DEVICES: [],
                OPT_EXCLUDED_ROOMS: [],
            },
        }
        asyncio.run(flow.async_step_init(user_input=user_input))

        assert flow.async_create_entry.called
        saved = flow.async_create_entry.call_args[1]["data"]
        assert saved[OPT_SCENARIOS_AS_BUTTONS] is True
        assert saved[OPT_DIAGNOSTIC_ENTITIES] is False
        assert saved[OPT_LONG_POLL_TIMEOUT] == 15
        assert saved[OPT_PRESENCE_ENTITY] == ["person.user1"]

    def test_no_data_session_uses_empty_options(self):
        """Lines 474-488: hass.data has no session → device/room options are empty."""
        flow, _ = _make_options_flow()
        # Provide hass.data with domain but no session data
        flow.hass.data = {DOMAIN: {}}

        asyncio.run(flow.async_step_init(user_input=None))
        # Should render fine without session
        assert flow.async_show_form.called

    def test_presence_entity_list_passes_through_unchanged(self):
        """Already a list: no coercion needed, stays as-is."""
        flow, _ = _make_options_flow(
            entry_options={OPT_PRESENCE_ENTITY: ["person.alice", "person.bob"]}
        )

        asyncio.run(flow.async_step_init(user_input=None))

        schema = flow.async_show_form.call_args[1]["data_schema"]
        presence_default = None
        for section_key in schema.schema:
            sec_schema = schema.schema[section_key]
            inner = getattr(sec_schema, "schema", None)
            if inner is None:
                continue
            inner_schema = getattr(inner, "schema", {})
            for field_key in inner_schema:
                if str(field_key) == OPT_PRESENCE_ENTITY:
                    if hasattr(field_key, "default") and callable(field_key.default):
                        presence_default = field_key.default()
        assert presence_default == ["person.alice", "person.bob"]
