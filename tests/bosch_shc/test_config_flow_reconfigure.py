"""Tests for config_flow reconfigure step and options flow.

These tests use hand-rolled mocks (no HA harness) so they run under
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 as part of the local CI gate.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

from custom_components.bosch_shc.config_flow import ConfigFlow, OptionsFlowHandler
from custom_components.bosch_shc.const import (
    CONF_SHC_CERT,
    CONF_SHC_KEY,
    DOMAIN,
    OPT_DIAGNOSTIC_ENTITIES,
    OPT_SCENARIOS_AS_BUTTONS,
    OPT_SSL_VERIFY_HOSTNAME,
    OPT_LONG_POLL_TIMEOUT,
)
from homeassistant.const import CONF_HOST


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_entry(host="1.2.3.4", unique_id="shc-serial-001", data=None, options=None):
    """Return a minimal ConfigEntry-like mock."""
    entry = MagicMock()
    entry.unique_id = unique_id
    entry.data = dict(data or {})
    entry.data.setdefault(CONF_HOST, host)
    entry.options = dict(options or {})
    entry.entry_id = "test-entry-id"
    return entry


def _make_hass():
    """Return a minimal hass-like namespace."""
    hass = MagicMock()
    hass.config.path = lambda *args: "/tmp/" + "/".join(args)
    hass.async_add_executor_job = AsyncMock(
        return_value={"title": "shc012345", "unique_id": "shc-serial-001"}
    )
    return hass


def _make_flow(entry=None, unique_id=None):
    """Instantiate a ConfigFlow with minimal wiring.

    unique_id, source and _reconfigure_entry_id are all read-only properties
    backed by self.context — set them there.
    """
    from homeassistant.config_entries import SOURCE_RECONFIGURE
    _entry = entry or _make_entry()
    flow = ConfigFlow.__new__(ConfigFlow)
    flow.hass = _make_hass()
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

    def test_reconfigure_shows_form_on_none_input(self):
        """Initial call (user_input=None) renders the reconfigure form."""
        entry = _make_entry(host="10.0.0.1")
        flow = _make_flow(entry=entry)

        # Patch _get_reconfigure_entry to return our mock entry
        flow._get_reconfigure_entry = lambda: entry
        # Patch async_show_form so we can inspect calls without a real HA
        flow.async_show_form = MagicMock(return_value={"type": "form", "step_id": "reconfigure"})

        result = asyncio.run(flow.async_step_reconfigure(user_input=None))

        assert flow.async_show_form.called
        call_kwargs = flow.async_show_form.call_args[1]
        assert call_kwargs["step_id"] == "reconfigure"
        # Default should be pre-filled with current host
        schema = call_kwargs["data_schema"]
        # The schema wraps vol.Required(CONF_HOST, default="10.0.0.1")
        assert CONF_HOST in {str(k) for k in schema.schema.keys()}

    def test_reconfigure_success_calls_update_reload_and_abort(self):
        """On valid input with same SHC serial, update-reload-abort is called."""
        entry = _make_entry(host="10.0.0.1", unique_id="shc-serial-001")
        flow = _make_flow(entry=entry)
        flow._get_reconfigure_entry = lambda: entry

        # _get_info returns same unique_id → mismatch check passes
        async def fake_get_info(host):
            return {"title": "shc012345", "unique_id": "shc-serial-001"}

        flow._get_info = fake_get_info

        # async_set_unique_id sets context["unique_id"] (backed by property)
        async def fake_set_uid(uid):
            flow.context["unique_id"] = uid
            return None

        flow.async_set_unique_id = fake_set_uid
        flow._abort_if_unique_id_mismatch = MagicMock()  # noop — same serial

        abort_result = {"type": "abort", "reason": "reconfigure_successful"}
        flow.async_update_reload_and_abort = MagicMock(return_value=abort_result)

        result = asyncio.run(flow.async_step_reconfigure(user_input={CONF_HOST: "10.0.0.2"}))

        assert flow.async_update_reload_and_abort.called
        call_kwargs = flow.async_update_reload_and_abort.call_args[1]
        assert call_kwargs["data_updates"] == {CONF_HOST: "10.0.0.2"}
        assert result == abort_result

    def test_reconfigure_cannot_connect_shows_error(self):
        """On SHCConnectionError, form is re-shown with cannot_connect error."""
        from boschshcpy.exceptions import SHCConnectionError

        entry = _make_entry(host="10.0.0.1")
        flow = _make_flow(entry=entry)
        flow._get_reconfigure_entry = lambda: entry

        async def fake_get_info(host):
            raise SHCConnectionError()

        flow._get_info = fake_get_info
        flow.async_show_form = MagicMock(return_value={"type": "form", "step_id": "reconfigure"})

        result = asyncio.run(flow.async_step_reconfigure(user_input={CONF_HOST: "bad-host"}))

        assert flow.async_show_form.called
        errors = flow.async_show_form.call_args[1]["errors"]
        assert errors.get("base") == "cannot_connect"

    def test_reconfigure_wrong_shc_aborts(self):
        """When a different SHC serial is found, _abort_if_unique_id_mismatch raises."""
        from homeassistant.exceptions import HomeAssistantError

        entry = _make_entry(host="10.0.0.1", unique_id="shc-serial-001")
        flow = _make_flow(entry=entry)
        flow._get_reconfigure_entry = lambda: entry

        async def fake_get_info(host):
            return {"title": "other", "unique_id": "shc-serial-999"}

        flow._get_info = fake_get_info

        async def fake_set_uid(uid):
            flow.context["unique_id"] = uid
            return None

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
            asyncio.run(flow.async_step_reconfigure(user_input={CONF_HOST: "10.0.0.3"}))

        assert "wrong_shc" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Part B — options flow
# ---------------------------------------------------------------------------

class TestOptionsFlow:
    """Unit tests for OptionsFlowHandler."""

    def _make_options_flow(self, entry_options=None):
        """Return an OptionsFlowHandler wired to a mock config entry."""
        entry = _make_entry(options=entry_options or {})
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
        result = asyncio.run(flow.async_step_init(user_input=user_input))
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

        entry = _make_entry(options={OPT_DIAGNOSTIC_ENTITIES: True})
        entry.entry_id = "eid"

        hass = MagicMock()
        hass.data = {DOMAIN: {"eid": {"session": session}}}
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
        assert "Valve Tappet" in entity_names
        assert "Communication Quality" in entity_names

    def test_diagnostic_entities_false_excludes_valvetappet(self):
        """When diagnostic_entities=False, ValveTappetSensor is NOT created."""
        session, thermostat, compact_plug = self._make_sensor_session()

        entry = _make_entry(options={OPT_DIAGNOSTIC_ENTITIES: False})
        entry.entry_id = "eid"

        hass = MagicMock()
        hass.data = {DOMAIN: {"eid": {"session": session}}}
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
