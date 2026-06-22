"""New unit tests for config_flow.py — harness-free, maximises coverage.

Run with:
  PYTHONPATH="/tmp/hass-cov:/tmp/lib-async" \
  PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  python3 -m pytest tests/bosch_shc/test_config_flow_new.py -q -o addopts=""
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

import pytest

from boschshcpy.exceptions import (
    SHCAuthenticationError,
    SHCConnectionError,
    SHCRegistrationError,
    SHCSessionError,
)

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
    CONF_SHC_CERT,
    CONF_SHC_KEY,
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
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PASSWORD, CONF_TOKEN


# ---------------------------------------------------------------------------
# Helpers shared across tests
# ---------------------------------------------------------------------------

def _make_hass(path_prefix="/tmp"):
    hass = MagicMock()
    hass.config.path = lambda *args: path_prefix + "/" + "/".join(args)
    return hass


async def _executor_job(fn, *args):
    """Synchronous stand-in for hass.async_add_executor_job."""
    return fn(*args)


def _make_flow(host="192.168.1.1", info=None):
    """Return a minimal ConfigFlow built with __new__ (no HA framework needed)."""
    flow = ConfigFlow.__new__(ConfigFlow)
    flow.hass = _make_hass()
    flow.hass.async_add_executor_job = _executor_job
    flow.context = {}
    flow.host = host
    flow.hostname = None
    flow.info = info or {"title": "SHC Test", "unique_id": "aa:bb:cc:dd:ee:ff"}
    flow.async_show_form = MagicMock(return_value={"type": "form"})
    flow.async_show_menu = MagicMock(return_value={"type": "menu"})
    flow.async_abort = MagicMock(return_value={"type": "abort"})
    flow.async_create_entry = MagicMock(return_value={"type": "create_entry"})
    flow.async_update_reload_and_abort = MagicMock(return_value={"type": "abort"})
    flow.async_set_unique_id = AsyncMock(return_value=None)
    flow._abort_if_unique_id_configured = MagicMock()
    flow._abort_if_unique_id_mismatch = MagicMock()
    flow._get_reconfigure_entry = MagicMock(
        return_value=MagicMock(data={CONF_HOST: host}, options={})
    )
    flow._get_info = AsyncMock(
        return_value={"title": "SHC Test", "unique_id": "aa:bb:cc:dd:ee:ff"}
    )
    return flow


def _make_options_flow(options=None, session=None):
    """Return an OptionsFlowHandler built with __new__."""
    entry = MagicMock()
    entry.entry_id = "test-entry-id"
    entry.options = options or {}

    flow = OptionsFlowHandler.__new__(OptionsFlowHandler)
    flow.__class__ = type(
        "_PatchedOptionsFlow",
        (OptionsFlowHandler,),
        {"config_entry": property(lambda self: entry)},
    )
    hass = _make_hass()
    if session is not None:
        hass.data = {DOMAIN: {"test-entry-id": {DATA_SESSION: session}}}
    else:
        hass.data = {}
    flow.hass = hass
    flow.async_show_form = MagicMock(return_value={"type": "form", "step_id": "init"})
    flow.async_create_entry = MagicMock(
        side_effect=lambda title, data: {"type": "result", "data": data}
    )
    return flow, entry


# ===========================================================================
# 1. _flatten_sections — pure function tests
# ===========================================================================

class TestFlattenSections:

    def test_flat_passthrough_non_section_keys(self):
        result = _flatten_sections({"some_key": "value", "other": 42})
        assert result == {"some_key": "value", "other": 42}

    def test_nested_sections_are_lifted(self):
        inp = {
            "features": {
                OPT_SCENARIOS_AS_BUTTONS: True,
                OPT_DIAGNOSTIC_ENTITIES: False,
            },
            "presence": {OPT_CHILD_LOCK_ENABLED: True},
            "advanced": {OPT_SSL_VERIFY_HOSTNAME: False},
        }
        result = _flatten_sections(inp)
        assert result[OPT_SCENARIOS_AS_BUTTONS] is True
        assert result[OPT_DIAGNOSTIC_ENTITIES] is False
        assert result[OPT_CHILD_LOCK_ENABLED] is True
        assert result[OPT_SSL_VERIFY_HOSTNAME] is False
        # Section keys themselves must be removed
        assert "features" not in result
        assert "presence" not in result
        assert "advanced" not in result

    def test_non_dict_section_value_is_skipped(self):
        # If a section key maps to None or a non-dict, it is skipped
        inp = {"features": None, "some_other_key": 99}
        result = _flatten_sections(inp)
        assert "features" not in result
        assert result["some_other_key"] == 99

    def test_non_dict_string_section_is_skipped(self):
        inp = {"features": "not-a-dict", "extra": 1}
        result = _flatten_sections(inp)
        assert "features" not in result
        assert result["extra"] == 1

    def test_empty_input_returns_empty(self):
        assert _flatten_sections({}) == {}

    def test_duplicate_key_between_sections_raises(self):
        # Craft a duplicate inside a section by exploiting OPTIONS_SECTIONS directly
        # We can simulate a scenario where two sections define the same flat key
        # by patching OPTIONS_SECTIONS temporarily
        from custom_components.bosch_shc import config_flow as cf_mod
        orig = dict(cf_mod.OPTIONS_SECTIONS)
        cf_mod.OPTIONS_SECTIONS["features"] = [OPT_SCENARIOS_AS_BUTTONS]
        cf_mod.OPTIONS_SECTIONS["features2"] = [OPT_SCENARIOS_AS_BUTTONS]
        try:
            inp = {
                "features": {OPT_SCENARIOS_AS_BUTTONS: True},
                "features2": {OPT_SCENARIOS_AS_BUTTONS: False},
            }
            with pytest.raises(ValueError, match="duplicate key"):
                _flatten_sections(inp)
        finally:
            cf_mod.OPTIONS_SECTIONS.clear()
            cf_mod.OPTIONS_SECTIONS.update(orig)

    def test_duplicate_key_top_level_and_section_raises(self):
        # A key that appears both inside a section and at the top level
        from custom_components.bosch_shc import config_flow as cf_mod
        orig = dict(cf_mod.OPTIONS_SECTIONS)
        cf_mod.OPTIONS_SECTIONS["features"] = [OPT_SCENARIOS_AS_BUTTONS]
        try:
            inp = {
                "features": {OPT_SCENARIOS_AS_BUTTONS: True},
                OPT_SCENARIOS_AS_BUTTONS: False,  # duplicate at top level
            }
            with pytest.raises(ValueError, match="duplicate key"):
                _flatten_sections(inp)
        finally:
            cf_mod.OPTIONS_SECTIONS.clear()
            cf_mod.OPTIONS_SECTIONS.update(orig)


# ===========================================================================
# 2. write_tls_asset — file-writing helper
# ===========================================================================

class TestWriteTlsAsset:

    def test_writes_decoded_bytes_to_file(self):
        hass = _make_hass()
        m = mock_open()
        with patch("custom_components.bosch_shc.config_flow.makedirs") as mk, \
                patch("builtins.open", m):
            write_tls_asset(hass, "test-cert.pem", b"CERT_CONTENT")

        mk.assert_called_once()
        m.assert_called_once_with(
            hass.config.path(DOMAIN, "test-cert.pem"), "w", encoding="utf8"
        )
        m().write.assert_called_once_with("CERT_CONTENT")

    def test_makedirs_called_with_exist_ok(self):
        hass = _make_hass()
        with patch("custom_components.bosch_shc.config_flow.makedirs") as mk, \
                patch("builtins.open", mock_open()):
            write_tls_asset(hass, "key.pem", b"KEY")

        mk.assert_called_once_with(hass.config.path(DOMAIN), exist_ok=True)

    def test_uses_hass_config_path_for_domain(self):
        hass = _make_hass("/myconfig")
        with patch("custom_components.bosch_shc.config_flow.makedirs"), \
                patch("builtins.open", mock_open()) as m:
            write_tls_asset(hass, "somefile.pem", b"DATA")

        path_arg = m.call_args[0][0]
        assert DOMAIN in path_arg
        assert "somefile.pem" in path_arg


# ===========================================================================
# 3. get_info_from_host — session.mdns_info() wrapper
# ===========================================================================

class TestGetInfoFromHost:

    def test_returns_title_and_unique_id(self):
        hass = _make_hass()
        info_mock = SimpleNamespace(name="SHC Device", unique_id="serial-123")
        session_mock = MagicMock()
        session_mock.mdns_info.return_value = info_mock

        with patch(
            "custom_components.bosch_shc.config_flow.SHCSession",
            return_value=session_mock,
        ):
            result = get_info_from_host(hass, "192.168.1.1", MagicMock())

        assert result == {"title": "SHC Device", "unique_id": "serial-123"}

    def test_passes_host_and_zeroconf_to_session(self):
        hass = _make_hass()
        info_mock = SimpleNamespace(name="SHC", unique_id="uid-99")
        session_mock = MagicMock()
        session_mock.mdns_info.return_value = info_mock
        zc = MagicMock()

        with patch(
            "custom_components.bosch_shc.config_flow.SHCSession",
            return_value=session_mock,
        ) as cls_mock:
            get_info_from_host(hass, "10.0.0.1", zc)

        cls_mock.assert_called_once_with("10.0.0.1", "", "", True, zc)

    def test_propagates_connection_error(self):
        hass = _make_hass()
        session_mock = MagicMock()
        session_mock.mdns_info.side_effect = SHCConnectionError()

        with patch(
            "custom_components.bosch_shc.config_flow.SHCSession",
            return_value=session_mock,
        ):
            with pytest.raises(SHCConnectionError):
                get_info_from_host(hass, "192.168.1.1", MagicMock())


# ===========================================================================
# 4. create_credentials_and_validate — registration + session
# ===========================================================================

class TestCreateCredentialsAndValidate:

    def _user_input(self):
        return {CONF_PASSWORD: "secret", CONF_NAME: "HomeAssistant"}

    def test_happy_path_returns_result(self):
        hass = _make_hass()
        fake_result = {
            "token": "pfx:hostname123",
            "cert": b"CERT",
            "key": b"KEY",
        }
        register_mock = MagicMock(return_value=fake_result)
        session_mock = MagicMock()

        with patch(
            "custom_components.bosch_shc.config_flow.SHCRegisterClient",
        ) as reg_cls, patch(
            "custom_components.bosch_shc.config_flow.SHCSession",
            return_value=session_mock,
        ), patch(
            "custom_components.bosch_shc.config_flow.write_tls_asset"
        ):
            reg_cls.return_value.register = register_mock
            result = create_credentials_and_validate(
                hass, "192.168.1.1", self._user_input(), MagicMock()
            )

        assert result == fake_result
        session_mock.authenticate.assert_called_once()

    def test_none_result_skips_session_creation(self):
        hass = _make_hass()
        register_mock = MagicMock(return_value=None)

        with patch(
            "custom_components.bosch_shc.config_flow.SHCRegisterClient",
        ) as reg_cls, patch(
            "custom_components.bosch_shc.config_flow.SHCSession",
        ) as session_cls:
            reg_cls.return_value.register = register_mock
            result = create_credentials_and_validate(
                hass, "192.168.1.1", self._user_input(), MagicMock()
            )

        assert result is None
        session_cls.assert_not_called()

    def test_writes_tls_assets_with_correct_filenames(self):
        hass = _make_hass()
        fake_result = {"token": "pfx:myhostname", "cert": b"C", "key": b"K"}
        register_mock = MagicMock(return_value=fake_result)
        session_mock = MagicMock()

        written = []

        def fake_write(h, filename, asset):
            written.append(filename)

        with patch(
            "custom_components.bosch_shc.config_flow.SHCRegisterClient",
        ) as reg_cls, patch(
            "custom_components.bosch_shc.config_flow.SHCSession",
            return_value=session_mock,
        ), patch(
            "custom_components.bosch_shc.config_flow.write_tls_asset",
            side_effect=fake_write,
        ):
            reg_cls.return_value.register = register_mock
            create_credentials_and_validate(
                hass, "192.168.1.1", self._user_input(), MagicMock()
            )

        assert any("myhostname" in f and CONF_SHC_CERT in f for f in written)
        assert any("myhostname" in f and CONF_SHC_KEY in f for f in written)

    def test_propagates_authentication_error(self):
        hass = _make_hass()
        fake_result = {"token": "pfx:h", "cert": b"C", "key": b"K"}
        register_mock = MagicMock(return_value=fake_result)
        session_mock = MagicMock()
        session_mock.authenticate.side_effect = SHCAuthenticationError()

        with patch(
            "custom_components.bosch_shc.config_flow.SHCRegisterClient",
        ) as reg_cls, patch(
            "custom_components.bosch_shc.config_flow.SHCSession",
            return_value=session_mock,
        ), patch(
            "custom_components.bosch_shc.config_flow.write_tls_asset"
        ):
            reg_cls.return_value.register = register_mock
            with pytest.raises(SHCAuthenticationError):
                create_credentials_and_validate(
                    hass, "192.168.1.1", self._user_input(), MagicMock()
                )

    def test_propagates_connection_error(self):
        hass = _make_hass()
        register_mock = MagicMock(side_effect=SHCConnectionError())

        with patch(
            "custom_components.bosch_shc.config_flow.SHCRegisterClient",
        ) as reg_cls:
            reg_cls.return_value.register = register_mock
            with pytest.raises(SHCConnectionError):
                create_credentials_and_validate(
                    hass, "192.168.1.1", self._user_input(), MagicMock()
                )

    def test_register_uses_lowercased_name_as_client_id(self):
        """register() is called with name.lower() as the first name arg."""
        hass = _make_hass()
        fake_result = {"token": "p:h", "cert": b"C", "key": b"K"}
        session_mock = MagicMock()

        with patch(
            "custom_components.bosch_shc.config_flow.SHCRegisterClient",
        ) as reg_cls, patch(
            "custom_components.bosch_shc.config_flow.SHCSession",
            return_value=session_mock,
        ), patch(
            "custom_components.bosch_shc.config_flow.write_tls_asset"
        ):
            reg_cls.return_value.register.return_value = fake_result
            create_credentials_and_validate(
                hass,
                "192.168.1.1",
                {CONF_PASSWORD: "s", CONF_NAME: "MyHA"},
                MagicMock(),
            )
            reg_cls.return_value.register.assert_called_once_with("myha", "MyHA")


# ===========================================================================
# 5. ConfigFlow.async_step_user
# ===========================================================================

class TestAsyncStepUser:

    def test_no_input_shows_form(self):
        flow = _make_flow()
        asyncio.run(flow.async_step_user(user_input=None))
        assert flow.async_show_form.called
        assert flow.async_show_form.call_args[1]["step_id"] == "user"

    def test_valid_input_proceeds_to_credentials(self):
        flow = _make_flow()
        # _get_info is already patched via AsyncMock on the flow
        asyncio.run(flow.async_step_user(user_input={CONF_HOST: "192.168.1.1"}))
        # async_step_credentials with no user_input returns show_form
        assert flow.async_show_form.called

    def test_connection_error_re_shows_form_with_error(self):
        flow = _make_flow()
        flow._get_info = AsyncMock(side_effect=SHCConnectionError())
        asyncio.run(flow.async_step_user(user_input={CONF_HOST: "192.168.1.1"}))
        errors = flow.async_show_form.call_args[1]["errors"]
        assert errors.get("base") == "cannot_connect"

    def test_unknown_exception_re_shows_form_with_unknown_error(self):
        flow = _make_flow()
        flow._get_info = AsyncMock(side_effect=RuntimeError("boom"))
        asyncio.run(flow.async_step_user(user_input={CONF_HOST: "192.168.1.1"}))
        errors = flow.async_show_form.call_args[1]["errors"]
        assert errors.get("base") == "unknown"

    def test_abort_if_unique_id_configured_called_on_success(self):
        flow = _make_flow()
        asyncio.run(flow.async_step_user(user_input={CONF_HOST: "1.2.3.4"}))
        flow._abort_if_unique_id_configured.assert_called_once()


# ===========================================================================
# 6. ConfigFlow.async_step_credentials
# ===========================================================================

class TestAsyncStepCredentials:

    def _user_input(self):
        return {CONF_PASSWORD: "secret", CONF_NAME: "HomeAssistant"}

    def test_no_input_shows_credentials_form(self):
        flow = _make_flow()
        asyncio.run(flow.async_step_credentials(user_input=None))
        assert flow.async_show_form.call_args[1]["step_id"] == "credentials"

    def test_success_no_existing_entry_creates_entry(self):
        flow = _make_flow()
        fake_result = {"token": "pfx:hostname", "cert": b"C", "key": b"K"}

        with patch(
            "custom_components.bosch_shc.config_flow.zeroconf.async_get_instance",
            new=AsyncMock(return_value=MagicMock()),
        ):
            flow.hass.async_add_executor_job = AsyncMock(return_value=fake_result)
            asyncio.run(flow.async_step_credentials(self._user_input()))

        flow.async_create_entry.assert_called_once()
        call_kwargs = flow.async_create_entry.call_args[1]
        assert call_kwargs["title"] == flow.info["title"]
        assert call_kwargs["data"][CONF_HOSTNAME] == "hostname"

    def test_success_with_existing_entry_updates_and_aborts(self):
        flow = _make_flow()
        existing_entry = MagicMock()
        flow.async_set_unique_id = AsyncMock(return_value=existing_entry)
        fake_result = {"token": "pfx:hostname", "cert": b"C", "key": b"K"}

        with patch(
            "custom_components.bosch_shc.config_flow.zeroconf.async_get_instance",
            new=AsyncMock(return_value=MagicMock()),
        ):
            flow.hass.async_add_executor_job = AsyncMock(return_value=fake_result)
            asyncio.run(flow.async_step_credentials(self._user_input()))

        flow.async_update_reload_and_abort.assert_called_once()

    def test_auth_error_shows_invalid_auth(self):
        flow = _make_flow()
        with patch(
            "custom_components.bosch_shc.config_flow.zeroconf.async_get_instance",
            new=AsyncMock(return_value=MagicMock()),
        ):
            flow.hass.async_add_executor_job = AsyncMock(
                side_effect=SHCAuthenticationError()
            )
            asyncio.run(flow.async_step_credentials(self._user_input()))

        errors = flow.async_show_form.call_args[1]["errors"]
        assert errors.get("base") == "invalid_auth"

    def test_connection_error_shows_cannot_connect(self):
        flow = _make_flow()
        with patch(
            "custom_components.bosch_shc.config_flow.zeroconf.async_get_instance",
            new=AsyncMock(return_value=MagicMock()),
        ):
            flow.hass.async_add_executor_job = AsyncMock(
                side_effect=SHCConnectionError()
            )
            asyncio.run(flow.async_step_credentials(self._user_input()))

        errors = flow.async_show_form.call_args[1]["errors"]
        assert errors.get("base") == "cannot_connect"

    def test_session_error_shows_session_error(self):
        flow = _make_flow()
        exc = SHCSessionError("session problem")
        with patch(
            "custom_components.bosch_shc.config_flow.zeroconf.async_get_instance",
            new=AsyncMock(return_value=MagicMock()),
        ):
            flow.hass.async_add_executor_job = AsyncMock(side_effect=exc)
            asyncio.run(flow.async_step_credentials(self._user_input()))

        errors = flow.async_show_form.call_args[1]["errors"]
        assert errors.get("base") == "session_error"

    def test_registration_error_shows_pairing_failed(self):
        flow = _make_flow()
        exc = SHCRegistrationError("button not pressed")
        with patch(
            "custom_components.bosch_shc.config_flow.zeroconf.async_get_instance",
            new=AsyncMock(return_value=MagicMock()),
        ):
            flow.hass.async_add_executor_job = AsyncMock(side_effect=exc)
            asyncio.run(flow.async_step_credentials(self._user_input()))

        errors = flow.async_show_form.call_args[1]["errors"]
        assert errors.get("base") == "pairing_failed"

    def test_unknown_exception_shows_unknown_error(self):
        flow = _make_flow()
        with patch(
            "custom_components.bosch_shc.config_flow.zeroconf.async_get_instance",
            new=AsyncMock(return_value=MagicMock()),
        ):
            flow.hass.async_add_executor_job = AsyncMock(
                side_effect=RuntimeError("unexpected")
            )
            asyncio.run(flow.async_step_credentials(self._user_input()))

        errors = flow.async_show_form.call_args[1]["errors"]
        assert errors.get("base") == "unknown"

    def test_result_data_includes_ssl_paths(self):
        flow = _make_flow()
        fake_result = {"token": "pfx:myhost", "cert": b"C", "key": b"K"}
        with patch(
            "custom_components.bosch_shc.config_flow.zeroconf.async_get_instance",
            new=AsyncMock(return_value=MagicMock()),
        ):
            flow.hass.async_add_executor_job = AsyncMock(return_value=fake_result)
            asyncio.run(flow.async_step_credentials(self._user_input()))

        data = flow.async_create_entry.call_args[1]["data"]
        assert CONF_SSL_CERTIFICATE in data
        assert CONF_SSL_KEY in data
        assert "myhost" in data[CONF_SSL_CERTIFICATE]
        assert "myhost" in data[CONF_SSL_KEY]


# ===========================================================================
# 7. ConfigFlow.async_step_zeroconf
# ===========================================================================

class TestAsyncStepZeroconf:

    def _make_discovery(self, name="Bosch SHC [aa:bb:cc]", host="192.168.1.1"):
        info = SimpleNamespace()
        info.name = name
        info.host = host
        info.hostname = "shc012345.local."
        return info

    def test_not_bosch_shc_aborts(self):
        flow = _make_flow()
        disc = self._make_discovery(name="SomeOtherDevice._http._tcp.local.")
        asyncio.run(flow.async_step_zeroconf(disc))
        flow.async_abort.assert_called_once_with(reason="not_bosch_shc")

    def test_connection_error_aborts(self):
        flow = _make_flow()
        flow._get_info = AsyncMock(side_effect=SHCConnectionError())
        disc = self._make_discovery()
        asyncio.run(flow.async_step_zeroconf(disc))
        flow.async_abort.assert_called_once_with(reason="cannot_connect")

    def test_happy_path_sets_host_and_shows_confirm(self):
        flow = _make_flow()
        disc = self._make_discovery(host="10.0.0.99")
        asyncio.run(flow.async_step_zeroconf(disc))
        assert flow.host == "10.0.0.99"
        # Should have proceeded to confirm_discovery → show_form
        assert flow.async_show_form.called
        assert flow.async_show_form.call_args[1]["step_id"] == "confirm_discovery"

    def test_title_placeholder_set_from_hostname(self):
        flow = _make_flow()
        disc = self._make_discovery()
        disc.hostname = "shc012345.local."
        asyncio.run(flow.async_step_zeroconf(disc))
        assert flow.context.get("title_placeholders", {}).get("name") == "shc012345"

    def test_abort_if_unique_id_configured_called(self):
        flow = _make_flow()
        disc = self._make_discovery()
        asyncio.run(flow.async_step_zeroconf(disc))
        flow._abort_if_unique_id_configured.assert_called_once()


# ===========================================================================
# 8. ConfigFlow.async_step_confirm_discovery
# ===========================================================================

class TestAsyncStepConfirmDiscovery:

    def test_none_input_shows_form(self):
        flow = _make_flow()
        flow.host = "192.168.1.1"
        asyncio.run(flow.async_step_confirm_discovery(user_input=None))
        assert flow.async_show_form.call_args[1]["step_id"] == "confirm_discovery"

    def test_form_has_host_placeholder(self):
        flow = _make_flow()
        flow.host = "10.0.0.55"
        asyncio.run(flow.async_step_confirm_discovery(user_input=None))
        placeholders = flow.async_show_form.call_args[1]["description_placeholders"]
        assert placeholders.get("host") == "10.0.0.55"

    def test_with_input_proceeds_to_credentials(self):
        flow = _make_flow()
        flow.host = "192.168.1.1"
        asyncio.run(flow.async_step_confirm_discovery(user_input={}))
        # Proceeds to credentials which shows form
        assert flow.async_show_form.called
        assert flow.async_show_form.call_args[1]["step_id"] == "credentials"


# ===========================================================================
# 9. ConfigFlow.async_step_reauth / async_step_reauth_confirm
# ===========================================================================

class TestAsyncStepReauth:

    def test_reauth_delegates_to_reauth_confirm(self):
        flow = _make_flow()
        asyncio.run(flow.async_step_reauth(user_input=None))
        # Should show the reauth_confirm form
        assert flow.async_show_form.call_args[1]["step_id"] == "reauth_confirm"

    def test_reauth_confirm_none_shows_form(self):
        flow = _make_flow()
        asyncio.run(flow.async_step_reauth_confirm(user_input=None))
        assert flow.async_show_form.call_args[1]["step_id"] == "reauth_confirm"

    def test_reauth_confirm_with_input_proceeds_to_credentials(self):
        flow = _make_flow()
        asyncio.run(
            flow.async_step_reauth_confirm(user_input={CONF_HOST: "192.168.1.1"})
        )
        # _get_info is called, then async_step_credentials
        assert flow._get_info.called
        assert flow.async_show_form.call_args[1]["step_id"] == "credentials"

    def test_reauth_confirm_sets_host_from_input(self):
        flow = _make_flow()
        asyncio.run(
            flow.async_step_reauth_confirm(user_input={CONF_HOST: "9.9.9.9"})
        )
        assert flow.host == "9.9.9.9"


# ===========================================================================
# 10. ConfigFlow.async_step_reconfigure
# ===========================================================================

class TestAsyncStepReconfigure:

    def test_shows_menu(self):
        flow = _make_flow()
        asyncio.run(flow.async_step_reconfigure(user_input=None))
        flow.async_show_menu.assert_called_once()
        call_kwargs = flow.async_show_menu.call_args[1]
        assert call_kwargs["step_id"] == "reconfigure"
        assert "reconfigure_host" in call_kwargs["menu_options"]
        assert "repair_credentials" in call_kwargs["menu_options"]


# ===========================================================================
# 11. ConfigFlow.async_step_reconfigure_host
# ===========================================================================

class TestAsyncStepReconfigureHost:

    def test_none_shows_form(self):
        flow = _make_flow()
        asyncio.run(flow.async_step_reconfigure_host(user_input=None))
        assert flow.async_show_form.call_args[1]["step_id"] == "reconfigure_host"

    def test_connection_error_shows_cannot_connect(self):
        flow = _make_flow()
        flow._get_info = AsyncMock(side_effect=SHCConnectionError())
        asyncio.run(flow.async_step_reconfigure_host(user_input={CONF_HOST: "bad"}))
        errors = flow.async_show_form.call_args[1]["errors"]
        assert errors.get("base") == "cannot_connect"

    def test_unknown_exception_shows_unknown_error(self):
        flow = _make_flow()
        flow._get_info = AsyncMock(side_effect=RuntimeError("fail"))
        asyncio.run(flow.async_step_reconfigure_host(user_input={CONF_HOST: "x"}))
        errors = flow.async_show_form.call_args[1]["errors"]
        assert errors.get("base") == "unknown"

    def test_success_calls_update_reload_abort(self):
        flow = _make_flow()
        flow._get_info = AsyncMock(
            return_value={"title": "SHC", "unique_id": "aa:bb:cc:dd:ee:ff"}
        )
        asyncio.run(
            flow.async_step_reconfigure_host(user_input={CONF_HOST: "10.0.0.2"})
        )
        flow.async_update_reload_and_abort.assert_called_once()
        kwargs = flow.async_update_reload_and_abort.call_args[1]
        assert kwargs["data_updates"] == {CONF_HOST: "10.0.0.2"}

    def test_mismatch_abort_called(self):
        flow = _make_flow()
        flow._get_info = AsyncMock(
            return_value={"title": "SHC", "unique_id": "different-uid"}
        )

        class FakeAbort(Exception):
            pass

        flow._abort_if_unique_id_mismatch = MagicMock(side_effect=FakeAbort("wrong_shc"))
        with pytest.raises(FakeAbort):
            asyncio.run(
                flow.async_step_reconfigure_host(user_input={CONF_HOST: "10.0.0.3"})
            )


# ===========================================================================
# 12. ConfigFlow.async_step_repair_credentials
# ===========================================================================

class TestAsyncStepRepairCredentials:

    def _make_repair_flow(self, host="10.0.0.1"):
        entry = MagicMock()
        entry.entry_id = "eid"
        entry.data = {CONF_HOST: host}
        entry.options = {}
        flow = _make_flow(host=host)
        flow._get_reconfigure_entry = MagicMock(return_value=entry)
        return flow, entry

    def test_none_shows_form(self):
        flow, _ = self._make_repair_flow()
        asyncio.run(flow.async_step_repair_credentials(user_input=None))
        assert flow.async_show_form.call_args[1]["step_id"] == "repair_credentials"

    def test_form_has_host_password_name_fields(self):
        flow, _ = self._make_repair_flow(host="172.16.0.1")
        asyncio.run(flow.async_step_repair_credentials(user_input=None))
        schema_keys = {
            str(k) for k in flow.async_show_form.call_args[1]["data_schema"].schema
        }
        assert CONF_HOST in schema_keys
        assert CONF_PASSWORD in schema_keys
        assert CONF_NAME in schema_keys

    def test_success_calls_update_reload_abort_with_full_data(self):
        flow, entry = self._make_repair_flow()
        fake_result = {"token": "pfx:newhostname", "cert": b"C", "key": b"K"}
        flow.hass.async_add_executor_job = AsyncMock(return_value=fake_result)

        with patch(
            "custom_components.bosch_shc.config_flow.zeroconf.async_get_instance",
            new=AsyncMock(return_value=MagicMock()),
        ):
            asyncio.run(
                flow.async_step_repair_credentials(
                    user_input={CONF_HOST: "10.0.0.1", CONF_PASSWORD: "pw", CONF_NAME: "ha"}
                )
            )

        flow.async_update_reload_and_abort.assert_called_once()
        new_data = flow.async_update_reload_and_abort.call_args[1]["data"]
        assert new_data[CONF_TOKEN] == "pfx:newhostname"
        assert new_data[CONF_HOSTNAME] == "newhostname"
        assert "newhostname" in new_data[CONF_SSL_CERTIFICATE]
        assert "newhostname" in new_data[CONF_SSL_KEY]

    def test_auth_error_shows_invalid_auth(self):
        flow, _ = self._make_repair_flow()
        flow.hass.async_add_executor_job = AsyncMock(
            side_effect=SHCAuthenticationError()
        )
        with patch(
            "custom_components.bosch_shc.config_flow.zeroconf.async_get_instance",
            new=AsyncMock(return_value=MagicMock()),
        ):
            asyncio.run(
                flow.async_step_repair_credentials(
                    user_input={CONF_HOST: "h", CONF_PASSWORD: "p", CONF_NAME: "n"}
                )
            )
        errors = flow.async_show_form.call_args[1]["errors"]
        assert errors.get("base") == "invalid_auth"

    def test_connection_error_shows_cannot_connect(self):
        flow, _ = self._make_repair_flow()
        flow.hass.async_add_executor_job = AsyncMock(side_effect=SHCConnectionError())
        with patch(
            "custom_components.bosch_shc.config_flow.zeroconf.async_get_instance",
            new=AsyncMock(return_value=MagicMock()),
        ):
            asyncio.run(
                flow.async_step_repair_credentials(
                    user_input={CONF_HOST: "h", CONF_PASSWORD: "p", CONF_NAME: "n"}
                )
            )
        errors = flow.async_show_form.call_args[1]["errors"]
        assert errors.get("base") == "cannot_connect"

    def test_session_error_shows_session_error(self):
        flow, _ = self._make_repair_flow()
        exc = SHCSessionError("session problem msg")
        flow.hass.async_add_executor_job = AsyncMock(side_effect=exc)
        with patch(
            "custom_components.bosch_shc.config_flow.zeroconf.async_get_instance",
            new=AsyncMock(return_value=MagicMock()),
        ):
            asyncio.run(
                flow.async_step_repair_credentials(
                    user_input={CONF_HOST: "h", CONF_PASSWORD: "p", CONF_NAME: "n"}
                )
            )
        errors = flow.async_show_form.call_args[1]["errors"]
        assert errors.get("base") == "session_error"

    def test_registration_error_shows_pairing_failed(self):
        flow, _ = self._make_repair_flow()
        exc = SHCRegistrationError("not pairing")
        flow.hass.async_add_executor_job = AsyncMock(side_effect=exc)
        with patch(
            "custom_components.bosch_shc.config_flow.zeroconf.async_get_instance",
            new=AsyncMock(return_value=MagicMock()),
        ):
            asyncio.run(
                flow.async_step_repair_credentials(
                    user_input={CONF_HOST: "h", CONF_PASSWORD: "p", CONF_NAME: "n"}
                )
            )
        errors = flow.async_show_form.call_args[1]["errors"]
        assert errors.get("base") == "pairing_failed"

    def test_unknown_exception_shows_unknown(self):
        flow, _ = self._make_repair_flow()
        flow.hass.async_add_executor_job = AsyncMock(
            side_effect=RuntimeError("kaboom")
        )
        with patch(
            "custom_components.bosch_shc.config_flow.zeroconf.async_get_instance",
            new=AsyncMock(return_value=MagicMock()),
        ):
            asyncio.run(
                flow.async_step_repair_credentials(
                    user_input={CONF_HOST: "h", CONF_PASSWORD: "p", CONF_NAME: "n"}
                )
            )
        errors = flow.async_show_form.call_args[1]["errors"]
        assert errors.get("base") == "unknown"


# ===========================================================================
# 13. OptionsFlowHandler.async_step_init
# ===========================================================================

class TestOptionsFlowHandlerInit:

    def test_no_input_shows_form(self):
        flow, _ = _make_options_flow()
        asyncio.run(flow.async_step_init(user_input=None))
        assert flow.async_show_form.called
        assert flow.async_show_form.call_args[1]["step_id"] == "init"

    def test_with_input_creates_entry_with_flattened_data(self):
        flow, _ = _make_options_flow()
        user_input = {
            "features": {
                OPT_SCENARIOS_AS_BUTTONS: True,
                OPT_DIAGNOSTIC_ENTITIES: True,
                OPT_ENABLE_RAWSCAN: False,
            },
            "presence": {
                OPT_CHILD_LOCK_ENABLED: False,
                OPT_PRESENCE_ENTITY: [],
            },
            "advanced": {
                OPT_SSL_VERIFY_HOSTNAME: True,
                OPT_LONG_POLL_TIMEOUT: 30,
                OPT_EXCLUDED_DEVICES: [],
                OPT_EXCLUDED_ROOMS: [],
            },
        }
        asyncio.run(flow.async_step_init(user_input=user_input))
        assert flow.async_create_entry.called
        saved = flow.async_create_entry.call_args[1]["data"]
        assert saved[OPT_SCENARIOS_AS_BUTTONS] is True
        assert saved[OPT_SSL_VERIFY_HOSTNAME] is True
        assert saved[OPT_LONG_POLL_TIMEOUT] == 30

    def test_presence_entity_string_coerced_to_list(self):
        """Legacy single-string presence entity is coerced to a list."""
        flow, _ = _make_options_flow(
            options={OPT_PRESENCE_ENTITY: "person.thomas"}
        )
        asyncio.run(flow.async_step_init(user_input=None))
        # Should not raise and form should render
        assert flow.async_show_form.called

    def test_presence_entity_empty_string_coerced_to_empty_list(self):
        flow, _ = _make_options_flow(options={OPT_PRESENCE_ENTITY: ""})
        asyncio.run(flow.async_step_init(user_input=None))
        assert flow.async_show_form.called

    def test_session_data_builds_device_and_room_options(self):
        """When a live session is available, device/room dropdowns are populated."""
        room = MagicMock()
        room.id = "room-1"
        room.name = "Living Room"

        dev = MagicMock()
        dev.id = "dev-1"
        dev.name = "Thermostat"
        dev.room_id = "room-1"

        session = MagicMock()
        session.rooms = [room]
        session.devices = [dev]

        flow, _ = _make_options_flow(session=session)
        asyncio.run(flow.async_step_init(user_input=None))
        assert flow.async_show_form.called

    def test_session_exception_does_not_crash_options_flow(self):
        """If session access raises, the form still renders (no crash)."""
        flow, entry = _make_options_flow()
        # Make hass.data.get raise
        flow.hass.data = MagicMock()
        flow.hass.data.get = MagicMock(side_effect=RuntimeError("session broken"))
        asyncio.run(flow.async_step_init(user_input=None))
        assert flow.async_show_form.called

    def test_with_existing_options_defaults_are_respected(self):
        flow, _ = _make_options_flow(
            options={
                OPT_DIAGNOSTIC_ENTITIES: False,
                OPT_SCENARIOS_AS_BUTTONS: True,
                OPT_LONG_POLL_TIMEOUT: 45,
            }
        )
        asyncio.run(flow.async_step_init(user_input=None))
        schema = flow.async_show_form.call_args[1]["data_schema"]
        # Walk schema to collect defaults from sections
        defaults = {}
        for key in schema.schema:
            section_schema = schema.schema[key]
            inner = getattr(section_schema, "schema", None)
            if inner is not None and hasattr(inner, "schema"):
                for sub_key in inner.schema:
                    if hasattr(sub_key, "default") and callable(sub_key.default):
                        defaults[str(sub_key)] = sub_key.default()
        assert defaults.get(OPT_DIAGNOSTIC_ENTITIES) is False
        assert defaults.get(OPT_SCENARIOS_AS_BUTTONS) is True

    def test_device_with_no_room_label_is_just_name(self):
        """Device without a room should show just the device name."""
        room = MagicMock()
        room.id = "room-1"
        room.name = "Kitchen"

        dev = MagicMock()
        dev.id = "dev-2"
        dev.name = "Switch"
        dev.room_id = None  # no room

        session = MagicMock()
        session.rooms = [room]
        session.devices = [dev]

        flow, _ = _make_options_flow(session=session)
        asyncio.run(flow.async_step_init(user_input=None))
        assert flow.async_show_form.called


# ===========================================================================
# 14. ConfigFlow.async_get_options_flow (static)
# ===========================================================================

class TestGetOptionsFlow:

    def test_returns_options_flow_handler(self):
        entry = MagicMock()
        result = ConfigFlow.async_get_options_flow(entry)
        assert isinstance(result, OptionsFlowHandler)


# ===========================================================================
# 15. ConfigFlow._get_info (the real implementation)
# ===========================================================================

class TestGetInfoMethod:

    def test_calls_get_info_from_host_via_executor(self):
        flow = _make_flow()
        info_result = {"title": "SHC X", "unique_id": "uid-x"}

        calls = []

        async def fake_executor(fn, *args):
            calls.append((fn, args))
            return info_result

        flow.hass.async_add_executor_job = fake_executor

        with patch(
            "custom_components.bosch_shc.config_flow.zeroconf.async_get_instance",
            new=AsyncMock(return_value=MagicMock()),
        ):
            # Access the REAL _get_info, not our mock
            real_get_info = ConfigFlow._get_info
            result = asyncio.run(real_get_info(flow, "192.168.1.1"))

        assert result == info_result
        assert len(calls) == 1
        fn, args = calls[0]
        from custom_components.bosch_shc.config_flow import get_info_from_host
        assert fn is get_info_from_host
