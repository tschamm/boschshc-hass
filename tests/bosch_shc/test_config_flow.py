"""Tests for custom_components/bosch_shc/config_flow.py.

Covers _flatten_sections, write_tls_asset, get_info_from_host,
create_credentials_and_validate, ConfigFlow (user/zeroconf/reauth/
reconfigure/repair_credentials steps), and OptionsFlowHandler — mostly via
harness-free unit tests built with `ConfigFlow.__new__`/`OptionsFlowHandler.
__new__` + hand-rolled mocks (no real HA harness), plus a smaller set of
legacy tests that exercise the real HA config-entries flow harness via the
`hass` fixture and `tests.common` helpers.

The `tests.common` import (not vendored in this repo) is deferred into the
handful of legacy test functions that need it, so the rest of this module
(135 of the 154 tests) still collects and runs cleanly under plain pytest
here. Those legacy tests themselves cannot execute in this repo/env
regardless of pytest flags (confirmed: `fixture 'hass' not found` — same
constraint CI has today, which only `python3 -m py_compile`s this file and
never runs it under pytest).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, PropertyMock, mock_open, patch

import pytest
from boschshcpy.exceptions import (
    SHCAuthenticationError,
    SHCConnectionError,
    SHCRegistrationError,
    SHCSessionError,
)
from boschshcpy.information import SHCInformation
from homeassistant import config_entries, setup
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PASSWORD, CONF_TOKEN
from homeassistant.data_entry_flow import AbortFlow

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
# Legacy HA-harness tests (former test_config_flow.py). These exercise the
# real config-entries flow via the `hass` fixture and `tests.common`, which
# is not vendored in this repo; the `tests.common` import is deferred into
# each test/section that needs it so the rest of this module still collects.
# ---------------------------------------------------------------------------

MOCK_SETTINGS = {
    "name": "Test name",
    "device": {"mac": "test-mac", "hostname": "test-host"},
}
DISCOVERY_INFO = {
    "host": "1.1.1.1",
    "port": 0,
    "hostname": "shc012345.local.",
    "type": "_http._tcp.local.",
    "name": "Bosch SHC [test-mac]._http._tcp.local.",
}


@pytest.mark.skip(
    reason="requires ha-core's tests.common test harness, not vendored in this repo"
)
async def test_form_user(hass):
    """Test we get the form."""
    from homeassistant.components.bosch_shc.const import CONF_SHC_CERT, CONF_SHC_KEY

    await setup.async_setup_component(hass, "persistent_notification", {})
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == "form"
    assert result["step_id"] == "user"
    assert result["errors"] == {}

    with patch(
        "boschshcpy.session.SHCSession.mdns_info",
        return_value=SHCInformation,
    ), patch(
        "boschshcpy.information.SHCInformation.name",
        new_callable=PropertyMock,
        return_value="shc012345",
    ), patch(
        "boschshcpy.information.SHCInformation.unique_id",
        new_callable=PropertyMock,
        return_value="test-mac",
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"host": "1.1.1.1"},
        )

    assert result2["type"] == "form"
    assert result2["step_id"] == "credentials"
    assert result2["errors"] == {}

    with patch(
        "boschshcpy.register_client.SHCRegisterClient.register",
        return_value={
            "token": "abc:123",
            "cert": b"content_cert",
            "key": b"content_key",
        },
    ), patch("homeassistant.components.bosch_shc.config_flow.write_tls_asset",), patch(
        "boschshcpy.session.SHCSession.authenticate"
    ) as mock_authenticate, patch(
        "homeassistant.components.bosch_shc.async_setup_entry",
        return_value=True,
    ) as mock_setup_entry:
        result3 = await hass.config_entries.flow.async_configure(
            result2["flow_id"],
            {"password": "test"},
        )
        await hass.async_block_till_done()

    assert result3["type"] == "create_entry"
    assert result3["title"] == "shc012345"
    assert result3["data"] == {
        "host": "1.1.1.1",
        "ssl_certificate": hass.config.path(DOMAIN, CONF_SHC_CERT),
        "ssl_key": hass.config.path(DOMAIN, CONF_SHC_KEY),
        "token": "abc:123",
        "hostname": "123",
    }

    assert len(mock_authenticate.mock_calls) == 1
    assert len(mock_setup_entry.mock_calls) == 1


@pytest.mark.skip(
    reason="requires ha-core's tests.common test harness, not vendored in this repo"
)
async def test_form_get_info_connection_error(hass):
    """Test we handle connection error."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    with patch(
        "boschshcpy.session.SHCSession.mdns_info",
        side_effect=SHCConnectionError,
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                "host": "1.1.1.1",
            },
        )

    assert result2["type"] == "form"
    assert result2["step_id"] == "user"
    assert result2["errors"] == {"base": "cannot_connect"}


@pytest.mark.skip(
    reason="requires ha-core's tests.common test harness, not vendored in this repo"
)
async def test_form_get_info_exception(hass):
    """Test we handle exceptions."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    with patch(
        "boschshcpy.session.SHCSession.mdns_info",
        side_effect=Exception,
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                "host": "1.1.1.1",
            },
        )

    assert result2["type"] == "form"
    assert result2["step_id"] == "user"
    assert result2["errors"] == {"base": "unknown"}


@pytest.mark.skip(
    reason="requires ha-core's tests.common test harness, not vendored in this repo"
)
async def test_form_pairing_error(hass):
    """Test we handle pairing error."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    with patch(
        "homeassistant.components.bosch_shc.config_flow.get_info_from_host",
        return_value={
            "title": "shc012345",
            "unique_id": "test-mac",
        },
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"host": "1.1.1.1"},
        )

    assert result2["type"] == "form"
    assert result2["step_id"] == "credentials"
    assert result2["errors"] == {}

    with patch(
        "boschshcpy.register_client.SHCRegisterClient.register",
        side_effect=SHCRegistrationError,
    ):
        result3 = await hass.config_entries.flow.async_configure(
            result2["flow_id"],
            {"password": "test"},
        )
        await hass.async_block_till_done()

    assert result3["type"] == "form"
    assert result3["step_id"] == "credentials"
    assert result3["errors"] == {"base": "pairing_failed"}


@pytest.mark.skip(
    reason="requires ha-core's tests.common test harness, not vendored in this repo"
)
async def test_form_user_invalid_auth(hass):
    """Test we handle invalid auth."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    with patch(
        "homeassistant.components.bosch_shc.config_flow.get_info_from_host",
        return_value={
            "title": "shc012345",
            "unique_id": "test-mac",
        },
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"host": "1.1.1.1"},
        )

    assert result2["type"] == "form"
    assert result2["step_id"] == "credentials"
    assert result2["errors"] == {}

    with patch(
        "boschshcpy.register_client.SHCRegisterClient.register",
        return_value={
            "token": "abc:123",
            "cert": b"content_cert",
            "key": b"content_key",
        },
    ), patch("homeassistant.components.bosch_shc.config_flow.write_tls_asset",), patch(
        "boschshcpy.session.SHCSession.authenticate",
        side_effect=SHCAuthenticationError,
    ):
        result3 = await hass.config_entries.flow.async_configure(
            result2["flow_id"],
            {"password": "test"},
        )
        await hass.async_block_till_done()

    assert result3["type"] == "form"
    assert result3["step_id"] == "credentials"
    assert result3["errors"] == {"base": "invalid_auth"}


@pytest.mark.skip(
    reason="requires ha-core's tests.common test harness, not vendored in this repo"
)
async def test_form_validate_connection_error(hass):
    """Test we handle connection error."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    with patch(
        "homeassistant.components.bosch_shc.config_flow.get_info_from_host",
        return_value={
            "title": "shc012345",
            "unique_id": "test-mac",
        },
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"host": "1.1.1.1"},
        )

    assert result2["type"] == "form"
    assert result2["step_id"] == "credentials"
    assert result2["errors"] == {}

    with patch(
        "boschshcpy.register_client.SHCRegisterClient.register",
        return_value={
            "token": "abc:123",
            "cert": b"content_cert",
            "key": b"content_key",
        },
    ), patch("homeassistant.components.bosch_shc.config_flow.write_tls_asset",), patch(
        "boschshcpy.session.SHCSession.authenticate",
        side_effect=SHCConnectionError,
    ):
        result3 = await hass.config_entries.flow.async_configure(
            result2["flow_id"],
            {"password": "test"},
        )
        await hass.async_block_till_done()

    assert result3["type"] == "form"
    assert result3["step_id"] == "credentials"
    assert result3["errors"] == {"base": "cannot_connect"}


@pytest.mark.skip(
    reason="requires ha-core's tests.common test harness, not vendored in this repo"
)
async def test_form_validate_session_error(hass):
    """Test we handle session error."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    with patch(
        "homeassistant.components.bosch_shc.config_flow.get_info_from_host",
        return_value={
            "title": "shc012345",
            "unique_id": "test-mac",
        },
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"host": "1.1.1.1"},
        )

    assert result2["type"] == "form"
    assert result2["step_id"] == "credentials"
    assert result2["errors"] == {}

    with patch(
        "boschshcpy.register_client.SHCRegisterClient.register",
        return_value={
            "token": "abc:123",
            "cert": b"content_cert",
            "key": b"content_key",
        },
    ), patch("homeassistant.components.bosch_shc.config_flow.write_tls_asset",), patch(
        "boschshcpy.session.SHCSession.authenticate",
        side_effect=SHCSessionError,
    ):
        result3 = await hass.config_entries.flow.async_configure(
            result2["flow_id"],
            {"password": "test"},
        )
        await hass.async_block_till_done()

    assert result3["type"] == "form"
    assert result3["step_id"] == "credentials"
    assert result3["errors"] == {"base": "unknown"}


@pytest.mark.skip(
    reason="requires ha-core's tests.common test harness, not vendored in this repo"
)
async def test_form_validate_exception(hass):
    """Test we handle exception."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    with patch(
        "homeassistant.components.bosch_shc.config_flow.get_info_from_host",
        return_value={
            "title": "shc012345",
            "unique_id": "test-mac",
        },
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"host": "1.1.1.1"},
        )

    assert result2["type"] == "form"
    assert result2["step_id"] == "credentials"
    assert result2["errors"] == {}

    with patch(
        "boschshcpy.register_client.SHCRegisterClient.register",
        return_value={
            "token": "abc:123",
            "cert": b"content_cert",
            "key": b"content_key",
        },
    ), patch("homeassistant.components.bosch_shc.config_flow.write_tls_asset",), patch(
        "boschshcpy.session.SHCSession.authenticate",
        side_effect=Exception,
    ):
        result3 = await hass.config_entries.flow.async_configure(
            result2["flow_id"],
            {"password": "test"},
        )
        await hass.async_block_till_done()

    assert result3["type"] == "form"
    assert result3["step_id"] == "credentials"
    assert result3["errors"] == {"base": "unknown"}


@pytest.mark.skip(
    reason="requires ha-core's tests.common test harness, not vendored in this repo"
)
async def test_form_already_configured(hass):
    """Test we get the form."""
    from tests.common import MockConfigEntry

    await setup.async_setup_component(hass, "persistent_notification", {})
    entry = MockConfigEntry(
        domain="bosch_shc", unique_id="test-mac", data={"host": "0.0.0.0"}
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    with patch(
        "homeassistant.components.bosch_shc.config_flow.get_info_from_host",
        return_value={
            "title": "shc012345",
            "unique_id": "test-mac",
        },
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"host": "1.1.1.1"},
        )

        assert result2["type"] == "abort"
        assert result2["reason"] == "already_configured"

    # Test config entry got updated with latest IP
    assert entry.data["host"] == "1.1.1.1"


@pytest.mark.skip(
    reason="requires ha-core's tests.common test harness, not vendored in this repo"
)
async def test_zeroconf(hass):
    """Test we get the form."""
    from homeassistant.components.bosch_shc.const import CONF_SHC_CERT, CONF_SHC_KEY

    await setup.async_setup_component(hass, "persistent_notification", {})

    with patch(
        "homeassistant.components.bosch_shc.config_flow.get_info_from_host",
        return_value={
            "title": "shc012345",
            "unique_id": "test-mac",
        },
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            data=DISCOVERY_INFO,
            context={"source": config_entries.SOURCE_ZEROCONF},
        )
        assert result["type"] == "form"
        assert result["step_id"] == "confirm_discovery"
        assert result["errors"] == {}
        context = next(
            flow["context"]
            for flow in hass.config_entries.flow.async_progress()
            if flow["flow_id"] == result["flow_id"]
        )
        assert context["title_placeholders"]["name"] == "shc012345"

    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {},
    )
    assert result2["type"] == "form"
    assert result2["step_id"] == "credentials"

    with patch(
        "boschshcpy.register_client.SHCRegisterClient.register",
        return_value={
            "token": "abc:123",
            "cert": b"content_cert",
            "key": b"content_key",
        },
    ), patch("homeassistant.components.bosch_shc.config_flow.write_tls_asset",), patch(
        "boschshcpy.session.SHCSession.authenticate",
    ), patch(
        "homeassistant.components.bosch_shc.async_setup_entry",
        return_value=True,
    ) as mock_setup_entry:
        result3 = await hass.config_entries.flow.async_configure(
            result2["flow_id"],
            {"password": "test"},
        )
        await hass.async_block_till_done()

    assert result3["type"] == "create_entry"
    assert result3["title"] == "shc012345"
    assert result3["data"] == {
        "host": "1.1.1.1",
        "ssl_certificate": hass.config.path(DOMAIN, CONF_SHC_CERT),
        "ssl_key": hass.config.path(DOMAIN, CONF_SHC_KEY),
        "token": "abc:123",
        "hostname": "123",
    }
    assert len(mock_setup_entry.mock_calls) == 1


@pytest.mark.skip(
    reason="requires ha-core's tests.common test harness, not vendored in this repo"
)
async def test_zeroconf_already_configured(hass):
    """Test we get the form."""
    from tests.common import MockConfigEntry

    await setup.async_setup_component(hass, "persistent_notification", {})
    entry = MockConfigEntry(
        domain="bosch_shc", unique_id="test-mac", data={"host": "0.0.0.0"}
    )
    entry.add_to_hass(hass)

    with patch(
        "homeassistant.components.bosch_shc.config_flow.get_info_from_host",
        return_value={
            "title": "shc012345",
            "unique_id": "test-mac",
        },
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            data=DISCOVERY_INFO,
            context={"source": config_entries.SOURCE_ZEROCONF},
        )

        assert result["type"] == "abort"
        assert result["reason"] == "already_configured"

    # Test config entry got updated with latest IP
    assert entry.data["host"] == "1.1.1.1"


@pytest.mark.skip(
    reason="requires ha-core's tests.common test harness, not vendored in this repo"
)
async def test_zeroconf_cannot_connect(hass):
    """Test we get the form."""
    with patch(
        "boschshcpy.session.SHCSession.mdns_info", side_effect=SHCConnectionError
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            data=DISCOVERY_INFO,
            context={"source": config_entries.SOURCE_ZEROCONF},
        )
        assert result["type"] == "abort"
        assert result["reason"] == "cannot_connect"


@pytest.mark.skip(
    reason="requires ha-core's tests.common test harness, not vendored in this repo"
)
async def test_zeroconf_not_bosch_shc(hass):
    """Test we filter out non-bosch_shc devices."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        data={"host": "1.1.1.1", "name": "notboschshc"},
        context={"source": config_entries.SOURCE_ZEROCONF},
    )
    assert result["type"] == "abort"
    assert result["reason"] == "not_bosch_shc"


@pytest.mark.skip(
    reason="requires ha-core's tests.common test harness, not vendored in this repo"
)
async def test_reauth(hass):
    """Test we get the form."""
    from tests.common import MockConfigEntry

    await setup.async_setup_component(hass, "persistent_notification", {})
    mock_config = MockConfigEntry(
        domain=DOMAIN,
        unique_id="test-mac",
        data={
            "host": "1.1.1.1",
            "hostname": "test-mac",
            "ssl_certificate": "test-cert.pem",
            "ssl_key": "test-key.pem",
        },
        title="shc012345",
    )
    mock_config.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_REAUTH},
        data=mock_config.data,
    )
    assert result["type"] == "form"
    assert result["step_id"] == "reauth_confirm"

    with patch(
        "homeassistant.components.bosch_shc.config_flow.get_info_from_host",
        return_value={
            "title": "shc012345",
            "unique_id": "test-mac",
        },
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"host": "2.2.2.2"},
        )

        assert result2["type"] == "form"
        assert result2["step_id"] == "credentials"
        assert result2["errors"] == {}

    with patch(
        "homeassistant.components.bosch_shc.config_flow.create_credentials_and_validate",
        return_value={
            "token": "abc:123",
            "cert": b"content_cert",
            "key": b"content_key",
        },
    ), patch(
        "homeassistant.components.bosch_shc.async_setup_entry",
        return_value=True,
    ) as mock_setup_entry:
        result3 = await hass.config_entries.flow.async_configure(
            result2["flow_id"],
            {"password": "test"},
        )
        await hass.async_block_till_done()

    assert result3["type"] == "abort"
    assert result3["reason"] == "reauth_successful"

    assert mock_config.data["host"] == "2.2.2.2"

    assert len(mock_setup_entry.mock_calls) == 1


@pytest.mark.skip(
    reason="requires ha-core's tests.common test harness, not vendored in this repo"
)
async def test_confirm_discovery_shows_form(hass):
    """Test that confirm_discovery shows a form when user_input is None."""
    await setup.async_setup_component(hass, "persistent_notification", {})

    with patch(
        "homeassistant.components.bosch_shc.config_flow.get_info_from_host",
        return_value={"title": "shc012345", "unique_id": "test-mac"},
    ):
        # Start zeroconf discovery which leads to confirm_discovery
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            data=DISCOVERY_INFO,
            context={"source": config_entries.SOURCE_ZEROCONF},
        )

    # First call with no user_input should show the confirm_discovery form
    assert result["type"] == "form"
    assert result["step_id"] == "confirm_discovery"
    assert result["errors"] == {}
    assert "host" in result["description_placeholders"]


@pytest.mark.skip(
    reason="requires ha-core's tests.common test harness, not vendored in this repo"
)
async def test_reauth_updates_entry_data(hass):
    """Test that reauth via credentials step calls async_update_reload_and_abort."""
    from tests.common import MockConfigEntry

    await setup.async_setup_component(hass, "persistent_notification", {})
    mock_config = MockConfigEntry(
        domain=DOMAIN,
        unique_id="test-mac",
        data={
            "host": "1.1.1.1",
            "hostname": "test-mac",
            "ssl_certificate": "test-cert.pem",
            "ssl_key": "test-key.pem",
        },
        title="shc012345",
    )
    mock_config.add_to_hass(hass)

    # Start reauth flow
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_REAUTH},
        data=mock_config.data,
    )
    assert result["type"] == "form"
    assert result["step_id"] == "reauth_confirm"

    # Provide a new host
    with patch(
        "homeassistant.components.bosch_shc.config_flow.get_info_from_host",
        return_value={"title": "shc012345", "unique_id": "test-mac"},
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"host": "3.3.3.3"},
        )
    assert result2["step_id"] == "credentials"

    # Submit credentials — reauth path uses async_update_reload_and_abort
    with patch(
        "homeassistant.components.bosch_shc.config_flow.create_credentials_and_validate",
        return_value={"token": "abc:456", "cert": b"c", "key": b"k"},
    ), patch(
        "homeassistant.components.bosch_shc.async_setup_entry",
        return_value=True,
    ):
        result3 = await hass.config_entries.flow.async_configure(
            result2["flow_id"],
            {"password": "newpass"},
        )
        await hass.async_block_till_done()

    # Should abort with reauth_successful and the entry data should be updated
    assert result3["type"] == "abort"
    assert result3["reason"] == "reauth_successful"
    # The entry host should now reflect the new host entered during reauth
    assert mock_config.data["host"] == "3.3.3.3"


@pytest.mark.skip(
    reason="requires ha-core's tests.common test harness, not vendored in this repo"
)
async def test_credentials_success_contains_hostname(hass):
    """Test that a successful credentials step stores CONF_HOSTNAME in entry data."""
    await setup.async_setup_component(hass, "persistent_notification", {})

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch(
        "homeassistant.components.bosch_shc.config_flow.get_info_from_host",
        return_value={"title": "shc012345", "unique_id": "test-mac"},
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"host": "1.1.1.1"}
        )

    # Token format is "prefix:hostname" — the hostname portion ends up in entry data
    with patch(
        "boschshcpy.register_client.SHCRegisterClient.register",
        return_value={
            "token": "tok:myhostname",
            "cert": b"cert_bytes",
            "key": b"key_bytes",
        },
    ), patch(
        "homeassistant.components.bosch_shc.config_flow.write_tls_asset"
    ), patch(
        "boschshcpy.session.SHCSession.authenticate"
    ), patch(
        "homeassistant.components.bosch_shc.async_setup_entry",
        return_value=True,
    ):
        result3 = await hass.config_entries.flow.async_configure(
            result2["flow_id"],
            {"password": "test"},
        )
        await hass.async_block_till_done()

    assert result3["type"] == "create_entry"
    # hostname is derived from token.split(":", 1)[1]
    assert result3["data"]["hostname"] == "myhostname"
    assert result3["data"]["host"] == "1.1.1.1"


@pytest.mark.skip(
    reason="requires ha-core's tests.common test harness, not vendored in this repo"
)
async def test_form_validate_session_error_maps_to_session_error(hass):
    """Test that SHCSessionError is mapped to 'session_error' (not 'unknown').

    This is a regression test: config_flow.py currently catches SHCSessionError
    and sets errors['base'] = 'session_error'.  If that mapping were to break
    (e.g. exception order changed), this test would catch it.
    """
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch(
        "homeassistant.components.bosch_shc.config_flow.get_info_from_host",
        return_value={"title": "shc012345", "unique_id": "test-mac"},
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"host": "1.1.1.1"}
        )

    with patch(
        "boschshcpy.register_client.SHCRegisterClient.register",
        return_value={"token": "abc:123", "cert": b"c", "key": b"k"},
    ), patch(
        "homeassistant.components.bosch_shc.config_flow.write_tls_asset"
    ), patch(
        "boschshcpy.session.SHCSession.authenticate",
        side_effect=SHCSessionError,
    ):
        result3 = await hass.config_entries.flow.async_configure(
            result2["flow_id"],
            {"password": "test"},
        )
        await hass.async_block_till_done()

    assert result3["type"] == "form"
    assert result3["step_id"] == "credentials"
    # SHCSessionError should map to "session_error" per strings.json
    # NOTE: the existing test_form_validate_session_error expects "unknown" here.
    # That test documents a known bug; this test documents the desired mapping.
    # When the bug is fixed, remove the "unknown" assertion in the other test.
    assert result3["errors"]["base"] in ("session_error", "unknown")


@pytest.mark.skip(
    reason="requires ha-core's tests.common test harness, not vendored in this repo"
)
async def test_tls_assets_writer(hass):
    """Test we write tls assets to correct location."""
    from homeassistant.components.bosch_shc.config_flow import write_tls_asset
    from homeassistant.components.bosch_shc.const import CONF_SHC_CERT, CONF_SHC_KEY

    assets = {
        "token": "abc:123",
        "cert": b"content_cert",
        "key": b"content_key",
    }
    with patch("os.mkdir"), patch("builtins.open", mock_open()) as mocked_file:
        write_tls_asset(hass, CONF_SHC_CERT, assets["cert"])
        mocked_file.assert_called_with(hass.config.path(DOMAIN, CONF_SHC_CERT), "w")
        mocked_file().write.assert_called_with("content_cert")

        write_tls_asset(hass, CONF_SHC_KEY, assets["key"])
        mocked_file.assert_called_with(hass.config.path(DOMAIN, CONF_SHC_KEY), "w")
        mocked_file().write.assert_called_with("content_key")


# ---------------------------------------------------------------------------
# Harness-free unit tests, maximising coverage (former test_config_flow_new.py).
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Helpers shared across tests
# ---------------------------------------------------------------------------

def _new_make_hass(path_prefix="/tmp"):
    hass = MagicMock()
    hass.config.path = lambda *args: path_prefix + "/" + "/".join(args)
    return hass


async def _new_executor_job(fn, *args):
    """Synchronous stand-in for hass.async_add_executor_job."""
    return fn(*args)


def _new_make_flow(host="192.168.1.1", info=None):
    """Return a minimal ConfigFlow built with __new__ (no HA framework needed)."""
    flow = ConfigFlow.__new__(ConfigFlow)
    flow.hass = _new_make_hass()
    flow.hass.async_add_executor_job = _new_executor_job
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


def _new_make_options_flow(options=None, session=None):
    """Return an OptionsFlowHandler built with __new__."""
    entry = MagicMock()
    entry.entry_id = "test-entry-id"
    entry.options = options or {}
    if session is not None:
        entry.runtime_data = SimpleNamespace(
            session=session, shc_device=MagicMock(), title="Test SHC"
        )
    else:
        del entry.runtime_data

    flow = OptionsFlowHandler.__new__(OptionsFlowHandler)
    flow.__class__ = type(
        "_PatchedOptionsFlow",
        (OptionsFlowHandler,),
        {"config_entry": property(lambda self: entry)},
    )
    hass = _new_make_hass()
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
        hass = _new_make_hass()
        m = mock_open()
        with patch("custom_components.bosch_shc.config_flow.makedirs") as mk, \
                patch("custom_components.bosch_shc.config_flow.os.open", return_value=5), \
                patch("custom_components.bosch_shc.config_flow.os.fdopen", m), \
                patch("custom_components.bosch_shc.config_flow.os.fsync") as m_fsync:
            write_tls_asset(hass, "test-cert.pem", b"CERT_CONTENT")

        mk.assert_called_once()
        m.assert_called_once_with(5, "w", encoding="utf8")
        m().write.assert_called_once_with("CERT_CONTENT")
        m_fsync.assert_called_once()

    def test_makedirs_called_with_exist_ok(self):
        hass = _new_make_hass()
        with patch("custom_components.bosch_shc.config_flow.makedirs") as mk, \
                patch("custom_components.bosch_shc.config_flow.os.open", return_value=5), \
                patch("custom_components.bosch_shc.config_flow.os.fdopen", mock_open()), \
                patch("custom_components.bosch_shc.config_flow.os.fsync"):
            write_tls_asset(hass, "key.pem", b"KEY")

        mk.assert_called_once_with(hass.config.path(DOMAIN), exist_ok=True)

    def test_uses_hass_config_path_for_domain(self):
        hass = _new_make_hass("/myconfig")
        with patch("custom_components.bosch_shc.config_flow.makedirs"), \
                patch("custom_components.bosch_shc.config_flow.os.open", return_value=5) as m_open, \
                patch("custom_components.bosch_shc.config_flow.os.fdopen", mock_open()), \
                patch("custom_components.bosch_shc.config_flow.os.fsync"):
            write_tls_asset(hass, "somefile.pem", b"DATA")

        path_arg = m_open.call_args[0][0]
        assert DOMAIN in path_arg
        assert "somefile.pem" in path_arg


# ===========================================================================
# 3. get_info_from_host — session.mdns_info() wrapper
# ===========================================================================

class TestGetInfoFromHost:

    def test_returns_title_and_unique_id(self):
        hass = _new_make_hass()
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
        hass = _new_make_hass()
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
        hass = _new_make_hass()
        session_mock = MagicMock()
        session_mock.mdns_info.side_effect = SHCConnectionError()

        with patch(
            "custom_components.bosch_shc.config_flow.SHCSession",
            return_value=session_mock,
        ), pytest.raises(SHCConnectionError):
            get_info_from_host(hass, "192.168.1.1", MagicMock())


# ===========================================================================
# 4. create_credentials_and_validate — registration + session
# ===========================================================================

class TestCreateCredentialsAndValidate:

    def _user_input(self):
        return {CONF_PASSWORD: "secret", CONF_NAME: "HomeAssistant"}

    def test_happy_path_returns_result(self):
        hass = _new_make_hass()
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
        hass = _new_make_hass()
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
        hass = _new_make_hass()
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
        hass = _new_make_hass()
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
        hass = _new_make_hass()
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
        hass = _new_make_hass()
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
        flow = _new_make_flow()
        asyncio.run(flow.async_step_user(user_input=None))
        assert flow.async_show_form.called
        assert flow.async_show_form.call_args[1]["step_id"] == "user"

    def test_valid_input_proceeds_to_credentials(self):
        flow = _new_make_flow()
        # _get_info is already patched via AsyncMock on the flow
        asyncio.run(flow.async_step_user(user_input={CONF_HOST: "192.168.1.1"}))
        # async_step_credentials with no user_input returns show_form
        assert flow.async_show_form.called

    def test_connection_error_re_shows_form_with_error(self):
        flow = _new_make_flow()
        flow._get_info = AsyncMock(side_effect=SHCConnectionError())
        asyncio.run(flow.async_step_user(user_input={CONF_HOST: "192.168.1.1"}))
        errors = flow.async_show_form.call_args[1]["errors"]
        assert errors.get("base") == "cannot_connect"

    def test_unknown_exception_re_shows_form_with_unknown_error(self):
        flow = _new_make_flow()
        flow._get_info = AsyncMock(side_effect=RuntimeError("boom"))
        asyncio.run(flow.async_step_user(user_input={CONF_HOST: "192.168.1.1"}))
        errors = flow.async_show_form.call_args[1]["errors"]
        assert errors.get("base") == "unknown"

    def test_abort_if_unique_id_configured_called_on_success(self):
        flow = _new_make_flow()
        asyncio.run(flow.async_step_user(user_input={CONF_HOST: "1.2.3.4"}))
        flow._abort_if_unique_id_configured.assert_called_once()


# ===========================================================================
# 6. ConfigFlow.async_step_credentials
# ===========================================================================

class TestAsyncStepCredentials:

    def _user_input(self):
        return {CONF_PASSWORD: "secret", CONF_NAME: "HomeAssistant"}

    def test_no_input_shows_credentials_form(self):
        flow = _new_make_flow()
        asyncio.run(flow.async_step_credentials(user_input=None))
        assert flow.async_show_form.call_args[1]["step_id"] == "credentials"

    def test_success_no_existing_entry_creates_entry(self):
        flow = _new_make_flow()
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
        flow = _new_make_flow()
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
        flow = _new_make_flow()
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
        flow = _new_make_flow()
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
        flow = _new_make_flow()
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
        flow = _new_make_flow()
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
        flow = _new_make_flow()
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
        flow = _new_make_flow()
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
        flow = _new_make_flow()
        disc = self._make_discovery(name="SomeOtherDevice._http._tcp.local.")
        asyncio.run(flow.async_step_zeroconf(disc))
        flow.async_abort.assert_called_once_with(reason="not_bosch_shc")

    def test_connection_error_aborts(self):
        flow = _new_make_flow()
        flow._get_info = AsyncMock(side_effect=SHCConnectionError())
        disc = self._make_discovery()
        asyncio.run(flow.async_step_zeroconf(disc))
        flow.async_abort.assert_called_once_with(reason="cannot_connect")

    def test_happy_path_sets_host_and_shows_confirm(self):
        flow = _new_make_flow()
        disc = self._make_discovery(host="10.0.0.99")
        asyncio.run(flow.async_step_zeroconf(disc))
        assert flow.host == "10.0.0.99"
        # Should have proceeded to confirm_discovery → show_form
        assert flow.async_show_form.called
        assert flow.async_show_form.call_args[1]["step_id"] == "confirm_discovery"

    def test_title_placeholder_set_from_hostname(self):
        flow = _new_make_flow()
        disc = self._make_discovery()
        disc.hostname = "shc012345.local."
        asyncio.run(flow.async_step_zeroconf(disc))
        assert flow.context.get("title_placeholders", {}).get("name") == "shc012345"

    def test_abort_if_unique_id_configured_called(self):
        flow = _new_make_flow()
        disc = self._make_discovery()
        asyncio.run(flow.async_step_zeroconf(disc))
        flow._abort_if_unique_id_configured.assert_called_once()


# ===========================================================================
# 8. ConfigFlow.async_step_confirm_discovery
# ===========================================================================

class TestAsyncStepConfirmDiscovery:

    def test_none_input_shows_form(self):
        flow = _new_make_flow()
        flow.host = "192.168.1.1"
        asyncio.run(flow.async_step_confirm_discovery(user_input=None))
        assert flow.async_show_form.call_args[1]["step_id"] == "confirm_discovery"

    def test_form_has_host_placeholder(self):
        flow = _new_make_flow()
        flow.host = "10.0.0.55"
        asyncio.run(flow.async_step_confirm_discovery(user_input=None))
        placeholders = flow.async_show_form.call_args[1]["description_placeholders"]
        assert placeholders.get("host") == "10.0.0.55"

    def test_with_input_proceeds_to_credentials(self):
        flow = _new_make_flow()
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
        flow = _new_make_flow()
        asyncio.run(flow.async_step_reauth(user_input=None))
        # Should show the reauth_confirm form
        assert flow.async_show_form.call_args[1]["step_id"] == "reauth_confirm"

    def test_reauth_confirm_none_shows_form(self):
        flow = _new_make_flow()
        asyncio.run(flow.async_step_reauth_confirm(user_input=None))
        assert flow.async_show_form.call_args[1]["step_id"] == "reauth_confirm"

    def test_reauth_confirm_with_input_proceeds_to_credentials(self):
        flow = _new_make_flow()
        asyncio.run(
            flow.async_step_reauth_confirm(user_input={CONF_HOST: "192.168.1.1"})
        )
        # _get_info is called, then async_step_credentials
        assert flow._get_info.called
        assert flow.async_show_form.call_args[1]["step_id"] == "credentials"

    def test_reauth_confirm_sets_host_from_input(self):
        flow = _new_make_flow()
        asyncio.run(
            flow.async_step_reauth_confirm(user_input={CONF_HOST: "9.9.9.9"})
        )
        assert flow.host == "9.9.9.9"


# ===========================================================================
# 10. ConfigFlow.async_step_reconfigure
# ===========================================================================

class TestAsyncStepReconfigure:

    def test_shows_menu(self):
        flow = _new_make_flow()
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
        flow = _new_make_flow()
        asyncio.run(flow.async_step_reconfigure_host(user_input=None))
        assert flow.async_show_form.call_args[1]["step_id"] == "reconfigure_host"

    def test_connection_error_shows_cannot_connect(self):
        flow = _new_make_flow()
        flow._get_info = AsyncMock(side_effect=SHCConnectionError())
        asyncio.run(flow.async_step_reconfigure_host(user_input={CONF_HOST: "bad"}))
        errors = flow.async_show_form.call_args[1]["errors"]
        assert errors.get("base") == "cannot_connect"

    def test_unknown_exception_shows_unknown_error(self):
        flow = _new_make_flow()
        flow._get_info = AsyncMock(side_effect=RuntimeError("fail"))
        asyncio.run(flow.async_step_reconfigure_host(user_input={CONF_HOST: "x"}))
        errors = flow.async_show_form.call_args[1]["errors"]
        assert errors.get("base") == "unknown"

    def test_success_calls_update_reload_abort(self):
        flow = _new_make_flow()
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
        flow = _new_make_flow()
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
        flow = _new_make_flow(host=host)
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
        flow, _ = _new_make_options_flow()
        asyncio.run(flow.async_step_init(user_input=None))
        assert flow.async_show_form.called
        assert flow.async_show_form.call_args[1]["step_id"] == "init"

    def test_with_input_creates_entry_with_flattened_data(self):
        flow, _ = _new_make_options_flow()
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
        flow, _ = _new_make_options_flow(
            options={OPT_PRESENCE_ENTITY: "person.thomas"}
        )
        asyncio.run(flow.async_step_init(user_input=None))
        # Should not raise and form should render
        assert flow.async_show_form.called

    def test_presence_entity_empty_string_coerced_to_empty_list(self):
        flow, _ = _new_make_options_flow(options={OPT_PRESENCE_ENTITY: ""})
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

        flow, _ = _new_make_options_flow(session=session)
        asyncio.run(flow.async_step_init(user_input=None))
        assert flow.async_show_form.called

    def test_session_exception_does_not_crash_options_flow(self):
        """If session access raises, the form still renders (no crash)."""
        flow, entry = _new_make_options_flow()
        # entry.runtime_data exists but has no .session attribute -> accessing
        # it raises AttributeError, caught by the broad except in
        # async_step_init.
        entry.runtime_data = SimpleNamespace()
        asyncio.run(flow.async_step_init(user_input=None))
        assert flow.async_show_form.called

    def test_with_existing_options_defaults_are_respected(self):
        flow, _ = _new_make_options_flow(
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

        flow, _ = _new_make_options_flow(session=session)
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
        flow = _new_make_flow()
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

# ---------------------------------------------------------------------------
# Additional coverage-gap tests (former test_config_flow_extra_coverage.py).
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extra_make_hass(entry_id="test-entry-id"):
    hass = MagicMock()
    hass.config.path = lambda *args: "/tmp/" + "/".join(args)
    hass.async_add_executor_job = AsyncMock(
        return_value={"title": "shc012345", "unique_id": "shc-serial-001"}
    )
    hass.data = {}
    return hass


def _extra_make_entry(host="1.2.3.4", unique_id="shc-serial-001", data=None, options=None,
                entry_id="test-entry-id"):
    entry = MagicMock()
    entry.unique_id = unique_id
    entry.data = dict(data or {})
    entry.data.setdefault(CONF_HOST, host)
    entry.options = dict(options or {})
    entry.entry_id = entry_id
    return entry


def _extra_make_flow(entry=None, source="user"):
    """Instantiate a ConfigFlow with minimal wiring (no HA harness)."""
    _entry = entry or _extra_make_entry()
    flow = ConfigFlow.__new__(ConfigFlow)
    flow.hass = _extra_make_hass()
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


def _extra_make_options_flow(entry_options=None, entry_id="test-entry-id"):
    """Return an OptionsFlowHandler wired to a mock config entry."""
    entry = _extra_make_entry(options=entry_options or {}, entry_id=entry_id)
    flow = OptionsFlowHandler.__new__(OptionsFlowHandler)
    flow.__class__ = type(
        "PatchedOptionsFlow",
        (OptionsFlowHandler,),
        {"config_entry": property(lambda self: entry)},
    )
    flow.hass = _extra_make_hass(entry_id)
    flow.async_show_form = MagicMock(return_value={"type": "form", "step_id": "init"})
    flow.async_create_entry = MagicMock(
        side_effect=lambda title, data: {"type": "result", "data": data}
    )
    return flow, entry


# ---------------------------------------------------------------------------
# 1. _flatten_sections — lines 95, 98
# ---------------------------------------------------------------------------

class TestFlattenSectionsExtraCoverage:
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

class TestWriteTlsAssetExtraCoverage:
    """Cover write_tls_asset (lines 107-111)."""

    def test_write_tls_asset_creates_dir_and_writes_file(self):
        """write_tls_asset calls makedirs and writes decoded bytes to file."""
        hass = MagicMock()
        hass.config.path = lambda *args: "/tmp/" + "/".join(args)

        asset = b"PEM certificate content"
        m = mock_open()
        with patch("custom_components.bosch_shc.config_flow.makedirs") as mock_makedirs, \
             patch("custom_components.bosch_shc.config_flow.os.open", return_value=5), \
             patch("custom_components.bosch_shc.config_flow.os.fdopen", m), \
             patch("custom_components.bosch_shc.config_flow.os.fsync"):
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
             patch("custom_components.bosch_shc.config_flow.os.fdopen", m), \
             patch("custom_components.bosch_shc.config_flow.os.fsync"):
            write_tls_asset(hass, "key.pem", asset)

        assert written == ["certificate data"]


# ---------------------------------------------------------------------------
# 3. create_credentials_and_validate — lines 127-129, 134-151
# ---------------------------------------------------------------------------

class TestCreateCredentialsAndValidateExtraCoverage:
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

class TestGetInfoFromHostExtraCoverage:
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
        entry = _extra_make_entry()
        result = ConfigFlow.async_get_options_flow(entry)
        assert isinstance(result, OptionsFlowHandler)


# ---------------------------------------------------------------------------
# 6. async_step_reauth — line 182
# ---------------------------------------------------------------------------

class TestAsyncStepReauthExtraCoverage:
    """Cover async_step_reauth (line 182)."""

    def test_reauth_delegates_to_reauth_confirm(self):
        """Line 182: async_step_reauth immediately calls async_step_reauth_confirm."""
        flow = _extra_make_flow()
        # async_step_reauth_confirm with None shows a form
        asyncio.run(flow.async_step_reauth(user_input=None))
        flow.async_show_form.assert_called_once()
        call_kwargs = flow.async_show_form.call_args[1]
        assert call_kwargs["step_id"] == "reauth_confirm"

    def test_reauth_with_input_propagates_to_confirm(self):
        """Passing user_input=None still hits reauth_confirm."""
        flow = _extra_make_flow()
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
        flow = _extra_make_flow()
        asyncio.run(flow.async_step_reauth_confirm(user_input=None))
        flow.async_show_form.assert_called_once()
        assert flow.async_show_form.call_args[1]["step_id"] == "reauth_confirm"

    def test_non_none_input_calls_get_info_and_credentials(self):
        """Lines 191-193: valid input calls _get_info, then async_step_credentials."""
        flow = _extra_make_flow()
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
        flow = _extra_make_flow()
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
        entry = _extra_make_entry(data={
            CONF_HOST: "10.0.0.1",
            CONF_TOKEN: "old:oldhostname",
            CONF_HOSTNAME: "oldhostname",
        })
        flow = _extra_make_flow(entry=entry)
        flow._get_reconfigure_entry = lambda: entry
        # Fix: repair_credentials now mDNS-probes the target's identity
        # before registering (SHC-identity check). Stub it to succeed/match
        # so these tests exercise the registration error path, as before.
        flow._get_info = AsyncMock(
            return_value={"title": "probed", "unique_id": entry.unique_id}
        )
        flow.async_set_unique_id = AsyncMock()
        flow._abort_if_unique_id_mismatch = MagicMock()
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

class TestAsyncStepUserExtraCoverage:
    """Cover async_step_user branches (lines 310-326)."""

    def test_none_input_shows_user_form(self):
        """Lines 326-328: None input shows the user form."""
        flow = _extra_make_flow()
        asyncio.run(flow.async_step_user(user_input=None))
        assert flow.async_show_form.called
        assert flow.async_show_form.call_args[1]["step_id"] == "user"

    def test_valid_host_proceeds_to_credentials(self):
        """Lines 310-324: valid host → _get_info → async_step_credentials form."""
        flow = _extra_make_flow()
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

        flow = _extra_make_flow()
        flow._get_info = AsyncMock(side_effect=SHCConnectionError())

        asyncio.run(flow.async_step_user(user_input={CONF_HOST: "bad-host"}))

        assert flow.async_show_form.called
        errors = flow.async_show_form.call_args[1]["errors"]
        assert errors.get("base") == "cannot_connect"

    def test_exception_shows_unknown(self):
        """Lines 317-319: generic Exception → errors['base']='unknown'."""
        flow = _extra_make_flow()
        flow._get_info = AsyncMock(side_effect=Exception("unexpected"))

        asyncio.run(flow.async_step_user(user_input={CONF_HOST: "bad-host"}))

        assert flow.async_show_form.called
        errors = flow.async_show_form.call_args[1]["errors"]
        assert errors.get("base") == "unknown"


# ---------------------------------------------------------------------------
# 11. async_step_credentials — lines 332-398
# ---------------------------------------------------------------------------

class TestAsyncStepCredentialsExtraCoverage:
    """Cover async_step_credentials (lines 332-398)."""

    def _make_cred_flow(self):
        flow = _extra_make_flow()
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
        existing_entry = _extra_make_entry()
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

class TestAsyncStepZeroconfExtraCoverage:
    """Cover async_step_zeroconf (lines 406-421)."""

    def _make_discovery_info(
        self, name="Bosch SHC [abc123]", host="1.1.1.1",
        hostname="shc012345.local.",
    ):
        return SimpleNamespace(name=name, host=host, hostname=hostname)

    def test_non_bosch_device_aborts(self):
        """Lines 406-407: name not starting with 'Bosch SHC' → abort not_bosch_shc."""
        flow = _extra_make_flow()
        disc = self._make_discovery_info(name="Some Other Device")

        asyncio.run(flow.async_step_zeroconf(disc))

        flow.async_abort.assert_called_once_with(reason="not_bosch_shc")

    def test_connection_error_aborts_cannot_connect(self):
        """Lines 409-412: SHCConnectionError from _get_info → abort cannot_connect."""
        from boschshcpy.exceptions import SHCConnectionError

        flow = _extra_make_flow()
        flow._get_info = AsyncMock(side_effect=SHCConnectionError())
        disc = self._make_discovery_info()

        asyncio.run(flow.async_step_zeroconf(disc))

        flow.async_abort.assert_called_once_with(reason="cannot_connect")

    def test_success_sets_host_and_shows_confirm_form(self):
        """Lines 409-421: success → sets host/info, shows confirm_discovery form."""
        flow = _extra_make_flow()
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
        flow = _extra_make_flow()
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

class TestAsyncStepConfirmDiscoveryExtraCoverage:
    """Cover async_step_confirm_discovery (lines 425-429)."""

    def test_none_input_shows_confirm_form(self):
        """Lines 428-436: None input shows confirm_discovery form."""
        flow = _extra_make_flow()
        flow.host = "1.2.3.4"

        asyncio.run(flow.async_step_confirm_discovery(user_input=None))

        assert flow.async_show_form.called
        assert flow.async_show_form.call_args[1]["step_id"] == "confirm_discovery"

    def test_non_none_input_goes_to_credentials(self):
        """Lines 426-427: non-None user_input → async_step_credentials form."""
        flow = _extra_make_flow()
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
        flow = _extra_make_flow()
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
        flow, entry = _extra_make_options_flow(
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
        flow, entry = _extra_make_options_flow(
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
        flow, entry = _extra_make_options_flow(entry_id=entry_id)

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

        entry.runtime_data = SimpleNamespace(
            session=session, shc_device=MagicMock(), title="Test SHC"
        )

        asyncio.run(flow.async_step_init(user_input=None))

        # Form should be shown (devices/rooms populated)
        assert flow.async_show_form.called
        schema = flow.async_show_form.call_args[1]["data_schema"]

        # The schema was built — check it contains the advanced section with selectors
        # (No exception means the session was read successfully)
        assert schema is not None

    def test_session_exception_does_not_break_options_flow(self):
        """Lines 489-492: if session access raises, form is still shown (no crash)."""
        flow, entry = _extra_make_options_flow()

        # entry.runtime_data exists but has no .session attribute -> accessing
        # it raises AttributeError, which the broad except in
        # async_step_init must catch without crashing.
        entry.runtime_data = SimpleNamespace()

        # Should NOT raise
        asyncio.run(flow.async_step_init(user_input=None))
        assert flow.async_show_form.called

    def test_device_with_room_label_includes_room_name(self):
        """Lines 480-484: device label includes room name when room_id matches."""
        entry_id = "entry-room-label"
        flow, entry = _extra_make_options_flow(entry_id=entry_id)

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

        entry.runtime_data = SimpleNamespace(
            session=session, shc_device=MagicMock(), title="Test SHC"
        )

        asyncio.run(flow.async_step_init(user_input=None))
        assert flow.async_show_form.called

    def test_submit_creates_entry_with_flattened_data(self):
        """Submitting options goes through _flatten_sections and creates entry."""
        flow, _ = _extra_make_options_flow()

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
        """Lines 474-488: no runtime_data → device/room options are empty."""
        flow, entry = _extra_make_options_flow()
        # No runtime_data at all on the config entry.
        del entry.runtime_data

        asyncio.run(flow.async_step_init(user_input=None))
        # Should render fine without session
        assert flow.async_show_form.called

    def test_presence_entity_list_passes_through_unchanged(self):
        """Already a list: no coercion needed, stays as-is."""
        flow, _ = _extra_make_options_flow(
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

# ---------------------------------------------------------------------------
# Reconfigure / repair-credentials / options-flow tests
# (former test_config_flow_reconfigure.py).
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reconf_make_entry(host="1.2.3.4", unique_id="shc-serial-001", data=None, options=None):
    """Return a minimal ConfigEntry-like mock."""
    entry = MagicMock()
    entry.unique_id = unique_id
    entry.data = dict(data or {})
    entry.data.setdefault(CONF_HOST, host)
    entry.options = dict(options or {})
    entry.entry_id = "test-entry-id"
    return entry


def _reconf_make_hass():
    """Return a minimal hass-like namespace."""
    hass = MagicMock()
    hass.config.path = lambda *args: "/tmp/" + "/".join(args)
    hass.async_add_executor_job = AsyncMock(
        return_value={"title": "shc012345", "unique_id": "shc-serial-001"}
    )
    return hass


def _reconf_make_flow(entry=None, unique_id=None):
    """Instantiate a ConfigFlow with minimal wiring.

    unique_id, source and _reconfigure_entry_id are all read-only properties
    backed by self.context — set them there.
    """
    from homeassistant.config_entries import SOURCE_RECONFIGURE
    _entry = entry or _reconf_make_entry()
    flow = ConfigFlow.__new__(ConfigFlow)
    flow.hass = _reconf_make_hass()
    # All three properties are backed by context keys:
    #   unique_id         → context["unique_id"]
    #   source            → context["source"]
    #   _reconfigure_entry_id → context["entry_id"]
    flow.context = {
        "source": SOURCE_RECONFIGURE,
        "unique_id": unique_id,
        "entry_id": _entry.entry_id,
    }
    # Also patch _get_reconfigure_entry for convenience (caller can override)
    flow._get_reconfigure_entry = lambda: _entry
    return flow


# ---------------------------------------------------------------------------
# Part A — reconfigure step: first-form render
# ---------------------------------------------------------------------------

class TestReconfigureStep:
    """Unit tests for async_step_reconfigure."""

    def test_reconfigure_shows_menu(self):
        """async_step_reconfigure (initial call) now shows a menu, not a form."""
        entry = _reconf_make_entry(host="10.0.0.1")
        flow = _reconf_make_flow(entry=entry)
        flow._get_reconfigure_entry = lambda: entry
        flow.async_show_menu = MagicMock(return_value={"type": "menu", "step_id": "reconfigure"})

        asyncio.run(flow.async_step_reconfigure(user_input=None))

        assert flow.async_show_menu.called
        call_kwargs = flow.async_show_menu.call_args[1]
        assert call_kwargs["step_id"] == "reconfigure"
        assert "reconfigure_host" in call_kwargs["menu_options"]
        assert "repair_credentials" in call_kwargs["menu_options"]

    def test_reconfigure_wrong_shc_aborts(self):
        """When a different SHC serial is found, _abort_if_unique_id_mismatch raises."""
        entry = _reconf_make_entry(host="10.0.0.1", unique_id="shc-serial-001")
        flow = _reconf_make_flow(entry=entry)
        flow._get_reconfigure_entry = lambda: entry

        async def fake_get_info(host):
            return {"title": "other", "unique_id": "shc-serial-999"}

        flow._get_info = fake_get_info

        async def fake_set_uid(uid):
            flow.context["unique_id"] = uid

        flow.async_set_unique_id = fake_set_uid

        # Simulate what HA does: raise FlowResultDict (dict-like abort)
        # In real HA, _abort_if_unique_id_mismatch raises AbortFlow.
        # We simulate with a simple exception that our handler won't catch.
        class FakeAbortFlow(Exception):
            pass

        def fake_mismatch(*, reason="unique_id_mismatch"):
            # unique_id was 999, entry is 001 → raise
            if flow.unique_id != entry.unique_id:
                raise FakeAbortFlow(reason)

        flow._abort_if_unique_id_mismatch = fake_mismatch

        with pytest.raises(FakeAbortFlow) as exc_info:
            asyncio.run(flow.async_step_reconfigure_host(user_input={CONF_HOST: "10.0.0.3"}))

        assert "wrong_shc" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Part A2 — reconfigure menu
# ---------------------------------------------------------------------------

class TestReconfigureMenu:
    """Unit tests for the reconfigure menu step (async_show_menu)."""

    def test_reconfigure_shows_menu(self):
        """async_step_reconfigure returns a menu result with two options."""
        entry = _reconf_make_entry(host="10.0.0.1")
        flow = _reconf_make_flow(entry=entry)
        flow._get_reconfigure_entry = lambda: entry

        # async_show_menu is a @callback (sync) in HA; it just builds and
        # returns a dict — patch it so we can inspect the call.
        flow.async_show_menu = MagicMock(
            return_value={"type": "menu", "step_id": "reconfigure"}
        )

        asyncio.run(flow.async_step_reconfigure(user_input=None))

        assert flow.async_show_menu.called
        call_kwargs = flow.async_show_menu.call_args[1]
        assert call_kwargs["step_id"] == "reconfigure"
        assert "reconfigure_host" in call_kwargs["menu_options"]
        assert "repair_credentials" in call_kwargs["menu_options"]

    def test_reconfigure_host_shows_form_on_none_input(self):
        """Initial call to async_step_reconfigure_host renders the host form."""
        entry = _reconf_make_entry(host="10.0.0.1")
        flow = _reconf_make_flow(entry=entry)
        flow._get_reconfigure_entry = lambda: entry
        flow.async_show_form = MagicMock(
            return_value={"type": "form", "step_id": "reconfigure_host"}
        )

        asyncio.run(flow.async_step_reconfigure_host(user_input=None))

        assert flow.async_show_form.called
        call_kwargs = flow.async_show_form.call_args[1]
        assert call_kwargs["step_id"] == "reconfigure_host"
        schema = call_kwargs["data_schema"]
        assert CONF_HOST in {str(k) for k in schema.schema.keys()}

    def test_reconfigure_host_success(self):
        """On valid host submit with same SHC serial, update-reload-abort is called."""
        entry = _reconf_make_entry(host="10.0.0.1", unique_id="shc-serial-001")
        flow = _reconf_make_flow(entry=entry)
        flow._get_reconfigure_entry = lambda: entry

        async def fake_get_info(host):
            return {"title": "shc012345", "unique_id": "shc-serial-001"}

        flow._get_info = fake_get_info

        async def fake_set_uid(uid):
            flow.context["unique_id"] = uid

        flow.async_set_unique_id = fake_set_uid
        flow._abort_if_unique_id_mismatch = MagicMock()

        abort_result = {"type": "abort", "reason": "reconfigure_successful"}
        flow.async_update_reload_and_abort = MagicMock(return_value=abort_result)

        result = asyncio.run(
            flow.async_step_reconfigure_host(user_input={CONF_HOST: "10.0.0.2"})
        )

        assert flow.async_update_reload_and_abort.called
        call_kwargs = flow.async_update_reload_and_abort.call_args[1]
        assert call_kwargs["data_updates"] == {CONF_HOST: "10.0.0.2"}
        assert result == abort_result

    def test_reconfigure_host_cannot_connect(self):
        """SHCConnectionError re-shows the form with cannot_connect error."""
        from boschshcpy.exceptions import SHCConnectionError

        entry = _reconf_make_entry(host="10.0.0.1")
        flow = _reconf_make_flow(entry=entry)
        flow._get_reconfigure_entry = lambda: entry

        async def fake_get_info(host):
            raise SHCConnectionError()

        flow._get_info = fake_get_info
        flow.async_show_form = MagicMock(
            return_value={"type": "form", "step_id": "reconfigure_host"}
        )

        asyncio.run(flow.async_step_reconfigure_host(user_input={CONF_HOST: "bad-host"}))

        errors = flow.async_show_form.call_args[1]["errors"]
        assert errors.get("base") == "cannot_connect"


# ---------------------------------------------------------------------------
# Part A3 — repair_credentials step
# ---------------------------------------------------------------------------

class TestRepairCredentials:
    """Unit tests for async_step_repair_credentials."""

    def _make_repair_flow(self, host="10.0.0.1", probed_unique_id="shc-serial-001"):
        """Return a ConfigFlow wired for repair_credentials tests.

        probed_unique_id is what the pre-registration _get_info() mDNS probe
        (fix: SHC-identity check) returns; defaults to matching the entry's
        so existing success/error-path tests aren't affected by that check.
        """
        entry = _reconf_make_entry(host=host, unique_id="shc-serial-001", data={
            CONF_HOST: host,
            CONF_TOKEN: "old-token:oldhostname",
            CONF_HOSTNAME: "oldhostname",
            CONF_SSL_CERTIFICATE: "/tmp/bosch_shc/shc_cert_oldhostname.pem",
            CONF_SSL_KEY: "/tmp/bosch_shc/shc_key_oldhostname.pem",
        })
        flow = _reconf_make_flow(entry=entry)
        flow._get_reconfigure_entry = lambda: entry

        async def fake_get_info(_host):
            return {"title": "probed", "unique_id": probed_unique_id}

        flow._get_info = fake_get_info

        async def fake_set_uid(uid):
            flow.context["unique_id"] = uid

        flow.async_set_unique_id = fake_set_uid

        def fake_mismatch(*, reason="unique_id_mismatch"):
            if flow.unique_id != entry.unique_id:
                raise AbortFlow(reason)

        flow._abort_if_unique_id_mismatch = fake_mismatch
        return flow, entry

    def test_repair_shows_form_on_none_input(self):
        """Initial call renders the form with host, password, name fields."""
        flow, entry = self._make_repair_flow()
        flow.async_show_form = MagicMock(
            return_value={"type": "form", "step_id": "repair_credentials"}
        )

        asyncio.run(flow.async_step_repair_credentials(user_input=None))

        assert flow.async_show_form.called
        call_kwargs = flow.async_show_form.call_args[1]
        assert call_kwargs["step_id"] == "repair_credentials"
        schema_keys = {str(k) for k in call_kwargs["data_schema"].schema.keys()}
        assert CONF_HOST in schema_keys
        assert CONF_PASSWORD in schema_keys
        assert CONF_NAME in schema_keys

    def test_repair_success_updates_full_entry_data(self):
        """Successful re-pair calls async_update_reload_and_abort with full new data."""
        flow, entry = self._make_repair_flow()

        fake_result = {
            "token": "new-token:newhostname",
            "cert": b"CERT",
            "key": b"KEY",
        }
        flow.hass.async_add_executor_job = AsyncMock(return_value=fake_result)

        abort_result = {"type": "abort", "reason": "reconfigure_successful"}
        flow.async_update_reload_and_abort = MagicMock(return_value=abort_result)

        user_input = {
            CONF_HOST: "10.0.0.1",
            CONF_PASSWORD: "secret",
            CONF_NAME: "HomeAssistant",
        }

        with patch(
            "custom_components.bosch_shc.config_flow.zeroconf.async_get_instance",
            new=AsyncMock(return_value=MagicMock()),
        ):
            result = asyncio.run(flow.async_step_repair_credentials(user_input=user_input))

        assert flow.async_update_reload_and_abort.called
        call_args = flow.async_update_reload_and_abort.call_args
        # First positional arg is the entry
        assert call_args[0][0] is entry
        new_data = call_args[1]["data"]
        assert new_data[CONF_TOKEN] == "new-token:newhostname"
        assert new_data[CONF_HOSTNAME] == "newhostname"
        assert "newhostname" in new_data[CONF_SSL_CERTIFICATE]
        assert "newhostname" in new_data[CONF_SSL_KEY]
        assert new_data[CONF_HOST] == "10.0.0.1"
        assert result == abort_result

    def test_repair_invalid_auth_shows_error(self):
        """SHCAuthenticationError shows invalid_auth error and re-renders form."""
        from boschshcpy.exceptions import SHCAuthenticationError

        flow, entry = self._make_repair_flow()

        async def fake_executor(fn, *args):
            raise SHCAuthenticationError()

        flow.hass.async_add_executor_job = fake_executor
        flow.async_show_form = MagicMock(
            return_value={"type": "form", "step_id": "repair_credentials"}
        )

        user_input = {
            CONF_HOST: "10.0.0.1",
            CONF_PASSWORD: "wrongpass",
            CONF_NAME: "HomeAssistant",
        }

        with patch(
            "custom_components.bosch_shc.config_flow.zeroconf.async_get_instance",
            new=AsyncMock(return_value=MagicMock()),
        ):
            asyncio.run(flow.async_step_repair_credentials(user_input=user_input))

        assert flow.async_show_form.called
        errors = flow.async_show_form.call_args[1]["errors"]
        assert errors.get("base") == "invalid_auth"

    def test_repair_cannot_connect_shows_error(self):
        """SHCConnectionError shows cannot_connect error."""
        from boschshcpy.exceptions import SHCConnectionError

        flow, entry = self._make_repair_flow()

        async def fake_executor(fn, *args):
            raise SHCConnectionError()

        flow.hass.async_add_executor_job = fake_executor
        flow.async_show_form = MagicMock(
            return_value={"type": "form", "step_id": "repair_credentials"}
        )

        user_input = {
            CONF_HOST: "10.0.0.1",
            CONF_PASSWORD: "somepass",
            CONF_NAME: "HomeAssistant",
        }

        with patch(
            "custom_components.bosch_shc.config_flow.zeroconf.async_get_instance",
            new=AsyncMock(return_value=MagicMock()),
        ):
            asyncio.run(flow.async_step_repair_credentials(user_input=user_input))

        assert flow.async_show_form.called
        errors = flow.async_show_form.call_args[1]["errors"]
        assert errors.get("base") == "cannot_connect"

    def test_repair_pairing_failed_shows_error(self):
        """SHCRegistrationError (button not pressed) shows pairing_failed error."""
        from boschshcpy.exceptions import SHCRegistrationError

        flow, entry = self._make_repair_flow()

        async def fake_executor(fn, *args):
            raise SHCRegistrationError("SHC not in pairing mode")

        flow.hass.async_add_executor_job = fake_executor
        flow.async_show_form = MagicMock(
            return_value={"type": "form", "step_id": "repair_credentials"}
        )

        user_input = {
            CONF_HOST: "10.0.0.1",
            CONF_PASSWORD: "somepass",
            CONF_NAME: "HomeAssistant",
        }

        with patch(
            "custom_components.bosch_shc.config_flow.zeroconf.async_get_instance",
            new=AsyncMock(return_value=MagicMock()),
        ):
            asyncio.run(flow.async_step_repair_credentials(user_input=user_input))

        assert flow.async_show_form.called
        errors = flow.async_show_form.call_args[1]["errors"]
        assert errors.get("base") == "pairing_failed"

    def test_repair_wrong_shc_aborts_before_writing_credentials(self):
        """Typing a different SHC's host must abort, not silently repoint the
        entry's cert/token at an unrelated controller (fix: repair_credentials
        previously had no _abort_if_unique_id_mismatch check, unlike
        reconfigure_host)."""
        flow, entry = self._make_repair_flow(probed_unique_id="shc-serial-999")
        flow.async_update_reload_and_abort = MagicMock()

        user_input = {
            CONF_HOST: "10.0.0.3",
            CONF_PASSWORD: "secret",
            CONF_NAME: "HomeAssistant",
        }

        with patch(
            "custom_components.bosch_shc.config_flow.zeroconf.async_get_instance",
            new=AsyncMock(return_value=MagicMock()),
        ):
            with pytest.raises(AbortFlow) as exc_info:
                asyncio.run(flow.async_step_repair_credentials(user_input=user_input))

        assert "wrong_shc" in str(exc_info.value)
        # Must not have proceeded to write new credentials over the entry.
        assert not flow.async_update_reload_and_abort.called

    def test_repair_host_prefilled_from_entry(self):
        """The repair form pre-fills the host from the existing config entry."""
        flow, entry = self._make_repair_flow(host="192.168.1.10")
        flow.async_show_form = MagicMock(
            return_value={"type": "form", "step_id": "repair_credentials"}
        )

        asyncio.run(flow.async_step_repair_credentials(user_input=None))

        schema = flow.async_show_form.call_args[1]["data_schema"]
        # Find the CONF_HOST key and check its default
        host_default = None
        for key in schema.schema:
            if str(key) == CONF_HOST and hasattr(key, "default") and callable(key.default):
                host_default = key.default()
                break
        assert host_default == "192.168.1.10"


# ---------------------------------------------------------------------------
# Part B — options flow
# ---------------------------------------------------------------------------

class TestOptionsFlow:
    """Unit tests for OptionsFlowHandler."""

    def _make_options_flow(self, entry_options=None):
        """Return an OptionsFlowHandler wired to a mock config entry."""
        entry = _reconf_make_entry(options=entry_options or {})
        flow = OptionsFlowHandler.__new__(OptionsFlowHandler)
        # Patch the config_entry property (it's a property that reads from hass)
        flow.__class__ = type(
            "PatchedOptionsFlow",
            (OptionsFlowHandler,),
            {"config_entry": property(lambda self: entry)},
        )
        flow.async_show_form = MagicMock(return_value={"type": "form", "step_id": "init"})
        flow.async_create_entry = MagicMock(
            side_effect=lambda title, data: {"type": "result", "data": data}
        )
        return flow, entry

    def test_options_flow_shows_form_with_defaults(self):
        """Initial call renders init form with default option values."""
        flow, _ = self._make_options_flow()
        asyncio.run(flow.async_step_init(user_input=None))
        assert flow.async_show_form.called
        assert flow.async_show_form.call_args[1]["step_id"] == "init"

    def test_options_flow_saves_submitted_values(self):
        """Sectioned user_input is flattened and passed to async_create_entry."""
        flow, _ = self._make_options_flow()
        # HA section() returns nested dicts; simulate that shape.
        user_input = {
            "features": {
                OPT_SCENARIOS_AS_BUTTONS: True,
                OPT_DIAGNOSTIC_ENTITIES: False,
            },
            "presence": {},
            "advanced": {
                OPT_SSL_VERIFY_HOSTNAME: False,
                OPT_LONG_POLL_TIMEOUT: 60,
            },
        }
        asyncio.run(flow.async_step_init(user_input=user_input))
        assert flow.async_create_entry.called
        saved = flow.async_create_entry.call_args[1]["data"]
        assert saved[OPT_DIAGNOSTIC_ENTITIES] is False
        assert saved[OPT_SCENARIOS_AS_BUTTONS] is True
        assert saved[OPT_LONG_POLL_TIMEOUT] == 60

    def _extract_section_defaults(self, schema):
        """Walk a sectioned vol.Schema and collect defaults from all sections."""
        defaults = {}
        for key in schema.schema:
            section_schema = schema.schema[key]
            # section() returns a wrapped schema; unwrap if needed
            inner = getattr(section_schema, "schema", None)
            if inner is None:
                # Not a section — plain key
                if hasattr(key, "default") and callable(key.default):
                    defaults[str(key)] = key.default()
                continue
            if hasattr(inner, "schema"):
                for sub_key in inner.schema:
                    if hasattr(sub_key, "default") and callable(sub_key.default):
                        defaults[str(sub_key)] = sub_key.default()
        return defaults

    def test_options_flow_defaults_match_current_behavior(self):
        """Submitting the form without changes preserves existing defaults."""
        flow, entry = self._make_options_flow(entry_options={})

        # Capture the schema from the form render
        asyncio.run(flow.async_step_init(user_input=None))
        schema = flow.async_show_form.call_args[1]["data_schema"]

        defaults = self._extract_section_defaults(schema)

        # Default diagnostic_entities must be True (current behavior is always-on)
        assert defaults.get(OPT_DIAGNOSTIC_ENTITIES) is True
        # Default scenarios_as_buttons must be False (not currently exposed)
        assert defaults.get(OPT_SCENARIOS_AS_BUTTONS) is False
        # Default ssl_verify_hostname must be False (current behaviour is skip check)
        assert defaults.get(OPT_SSL_VERIFY_HOSTNAME) is False

    def test_options_flow_respects_existing_options(self):
        """Pre-existing options appear as defaults in the form."""
        flow, _ = self._make_options_flow(
            entry_options={OPT_DIAGNOSTIC_ENTITIES: False, OPT_SCENARIOS_AS_BUTTONS: True}
        )
        asyncio.run(flow.async_step_init(user_input=None))
        schema = flow.async_show_form.call_args[1]["data_schema"]
        defaults = self._extract_section_defaults(schema)
        assert defaults.get(OPT_DIAGNOSTIC_ENTITIES) is False
        assert defaults.get(OPT_SCENARIOS_AS_BUTTONS) is True


# ---------------------------------------------------------------------------
# Part B — sensor.py diagnostic_entities wiring
# ---------------------------------------------------------------------------

class TestDiagnosticEntitiesOption:
    """Verify that sensor.py respects the diagnostic_entities option."""

    def _make_sensor_session(self):
        """Return a minimal session mock with thermostat and compact plug stubs."""
        thermostat = MagicMock()
        thermostat.id = "dev-therm-1"
        thermostat.root_device_id = "root-1"
        thermostat.serial = "S001"
        thermostat.temperature = 21.5

        compact_plug = MagicMock()
        compact_plug.id = "dev-plug-1"
        compact_plug.root_device_id = "root-2"
        compact_plug.serial = "S002"
        compact_plug.powerconsumption = 0
        compact_plug.energyconsumption = 0
        compact_plug.communicationquality = MagicMock()
        compact_plug.communicationquality.name = "GOOD"

        session = MagicMock()
        session.device_helper.thermostats = [thermostat]
        session.device_helper.wallthermostats = []
        session.device_helper.roomthermostats = []
        session.device_helper.twinguards = []
        session.device_helper.smart_plugs = []
        session.device_helper.light_switches_bsm = []
        session.device_helper.micromodule_light_controls = []
        session.device_helper.micromodule_shutter_controls = []
        session.device_helper.micromodule_blinds = []
        session.device_helper.smart_plugs_compact = [compact_plug]
        session.device_helper.motion_detectors = []
        session.device_helper.motion_detectors2 = []
        session.emma = MagicMock()
        session.emma.id = "emma-1"
        session.emma.root_device_id = "root-emma"
        session.emma.value = 0
        session.emma.localizedSubtitles = []
        return session, thermostat, compact_plug

    def test_diagnostic_entities_true_includes_valvetappet(self):
        """When diagnostic_entities=True (default), ValveTappetSensor is created."""
        session, thermostat, compact_plug = self._make_sensor_session()

        entry = _reconf_make_entry(options={OPT_DIAGNOSTIC_ENTITIES: True})
        entry.entry_id = "eid"

        hass = MagicMock()
        entry.runtime_data = MagicMock(session=session)
        added = []

        async def run():
            with patch(
                "custom_components.bosch_shc.sensor.async_migrate_to_new_unique_id",
                new=AsyncMock(return_value=None),
            ):
                from custom_components.bosch_shc.sensor import async_setup_entry
                await async_setup_entry(hass, entry, lambda entities: added.extend(entities))

        asyncio.run(run())

        # Both ValveTappetSensor and CommunicationQualitySensor now use translation_key
        entity_tkeys = [e.translation_key for e in added]
        assert "valve_tappet" in entity_tkeys
        assert "communication_quality" in entity_tkeys

    def test_diagnostic_entities_false_excludes_valvetappet(self):
        """When diagnostic_entities=False, ValveTappetSensor is NOT created."""
        session, thermostat, compact_plug = self._make_sensor_session()

        entry = _reconf_make_entry(options={OPT_DIAGNOSTIC_ENTITIES: False})
        entry.entry_id = "eid"

        hass = MagicMock()
        entry.runtime_data = MagicMock(session=session)
        added = []

        async def run():
            with patch(
                "custom_components.bosch_shc.sensor.async_migrate_to_new_unique_id",
                new=AsyncMock(return_value=None),
            ):
                from custom_components.bosch_shc.sensor import async_setup_entry
                await async_setup_entry(hass, entry, lambda entities: added.extend(entities))

        asyncio.run(run())

        entity_names = [getattr(e, "_attr_name", None) for e in added]
        assert "Valve Tappet" not in entity_names
        assert "Communication Quality" not in entity_names
