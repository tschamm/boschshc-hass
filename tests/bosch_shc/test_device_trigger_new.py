"""Unit tests for device_trigger.py — harness-free, maximises coverage.

Run with:
  PYTHONPATH="/tmp/hass-cov:/tmp/lib-async" \
  PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  python3 -m pytest tests/bosch_shc/test_device_trigger_new.py -q -o addopts=""
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from custom_components.bosch_shc.const import (
    ALARM_EVENTS_SUBTYPES_SD,
    ALARM_EVENTS_SUBTYPES_SDS,
    ATTR_EVENT_SUBTYPE,
    ATTR_EVENT_TYPE,
    CONF_SUBTYPE,
    DATA_SESSION,
    DOMAIN,
    EVENT_BOSCH_SHC,
    INPUTS_EVENTS_SUBTYPES_SWITCH2,
    SUPPORTED_INPUTS_EVENTS_TYPES,
)
from custom_components.bosch_shc.device_trigger import (
    TRIGGER_SCHEMA,
    async_attach_trigger,
    async_get_triggers,
    get_device_from_id,
)
from homeassistant.const import (
    ATTR_DEVICE_ID,
    CONF_DEVICE_ID,
    CONF_DOMAIN,
    CONF_PLATFORM,
    CONF_TYPE,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_hass(config_entries=None, data=None):
    """Return a minimal hass mock."""
    hass = MagicMock()
    hass.data = data or {}
    return hass


def _make_shc_device(device_id="shc-device-1", model="WRC2", name="Button"):
    dev = MagicMock()
    dev.id = device_id
    dev.name = name
    dev.device_model = model
    return dev


def _make_session(devices=None, intrusion_system=None, unique_id="shc-serial-001"):
    session = MagicMock()
    session.devices = devices or []
    session.intrusion_system = intrusion_system
    session.information = SimpleNamespace(unique_id=unique_id)
    # scenario_names used for SHC-type triggers
    session.scenario_names = ["Away", "Home", "Night"]
    return session


def _build_hass_with_device(
    shc_device_id="shc-device-1",
    model="WRC2",
    ha_device_id="ha-device-id-1",
    entry_id="entry-001",
    intrusion_system=None,
    scenario_names=None,
    shc_serial="shc-serial-001",
):
    """Build a hass mock where the given shc_device_id maps to ha_device_id."""
    shc_dev = _make_shc_device(device_id=shc_device_id, model=model)

    ha_device = MagicMock()
    ha_device.id = ha_device_id

    session = _make_session(
        devices=[shc_dev],
        intrusion_system=intrusion_system,
        unique_id=shc_serial,
    )
    if scenario_names is not None:
        session.scenario_names = scenario_names

    hass = _make_hass(
        data={DOMAIN: {entry_id: {DATA_SESSION: session}}}
    )

    # dr.async_get(hass).async_get_device → return ha_device for matching id
    def fake_async_get_device(identifiers, connections):
        for _, dev_id in identifiers:
            if dev_id == shc_device_id:
                return ha_device
        return None

    mock_registry = MagicMock()
    mock_registry.async_get_device = fake_async_get_device

    return hass, ha_device, session, mock_registry


# ===========================================================================
# 1. Module-level constants
# ===========================================================================

class TestModuleConstants:

    def test_trigger_types_contains_expected_types(self):
        expected = {
            "PRESS_SHORT", "PRESS_LONG", "PRESS_LONG_RELEASED",
            "MOTION", "SCENARIO", "ALARM",
        }
        assert expected == SUPPORTED_INPUTS_EVENTS_TYPES

    def test_trigger_schema_requires_type_and_subtype(self):
        import voluptuous as vol
        with pytest.raises((vol.error.MultipleInvalid, Exception)):
            TRIGGER_SCHEMA({})

    def test_trigger_schema_valid_with_known_type(self):
        valid = {
            CONF_PLATFORM: "device",
            CONF_DEVICE_ID: "dev-id",
            CONF_DOMAIN: DOMAIN,
            CONF_TYPE: "PRESS_SHORT",
            CONF_SUBTYPE: "UPPER_BUTTON",
        }
        result = TRIGGER_SCHEMA(valid)
        assert result[CONF_TYPE] == "PRESS_SHORT"

    def test_trigger_schema_rejects_unknown_type(self):
        import voluptuous as vol
        invalid = {
            CONF_PLATFORM: "device",
            CONF_DEVICE_ID: "dev-id",
            CONF_DOMAIN: DOMAIN,
            CONF_TYPE: "UNKNOWN_TYPE_XYZ",
            CONF_SUBTYPE: "whatever",
        }
        with pytest.raises(vol.error.MultipleInvalid):
            TRIGGER_SCHEMA(invalid)


# ===========================================================================
# 2. get_device_from_id
# ===========================================================================

class TestGetDeviceFromId:

    def test_returns_none_when_no_matching_device(self):
        session = _make_session(devices=[])
        hass = _make_hass(data={DOMAIN: {"e1": {DATA_SESSION: session}}})

        mock_registry = MagicMock()
        mock_registry.async_get_device.return_value = None

        with patch(
            "custom_components.bosch_shc.device_trigger.dr.async_get",
            return_value=mock_registry,
        ):
            device, model = asyncio.run(
                get_device_from_id(hass, "nonexistent-device-id")
            )

        assert device is None
        assert model == ""

    def test_returns_shc_device_when_ids_match(self):
        shc_dev = _make_shc_device(device_id="shc-1", model="WRC2")
        session = _make_session(devices=[shc_dev])
        hass = _make_hass(data={DOMAIN: {"e1": {DATA_SESSION: session}}})

        ha_device = MagicMock()
        ha_device.id = "ha-dev-1"

        def fake_get_device(identifiers, connections):
            for _, dev_id in identifiers:
                if dev_id == "shc-1":
                    return ha_device
            return None

        mock_registry = MagicMock()
        mock_registry.async_get_device = fake_get_device

        with patch(
            "custom_components.bosch_shc.device_trigger.dr.async_get",
            return_value=mock_registry,
        ):
            device, model = asyncio.run(get_device_from_id(hass, "ha-dev-1"))

        assert device is shc_dev
        assert model == "WRC2"

    def test_returns_ids_device_when_intrusion_system_matches(self):
        ids_dev = MagicMock()
        ids_dev.id = "ids-1"

        session = _make_session(devices=[], intrusion_system=ids_dev)
        hass = _make_hass(data={DOMAIN: {"e1": {DATA_SESSION: session}}})

        ha_device = MagicMock()
        ha_device.id = "ha-ids-dev"

        def fake_get_device(identifiers, connections):
            for _, dev_id in identifiers:
                if dev_id == "ids-1":
                    return ha_device
            return None

        mock_registry = MagicMock()
        mock_registry.async_get_device = fake_get_device

        with patch(
            "custom_components.bosch_shc.device_trigger.dr.async_get",
            return_value=mock_registry,
        ):
            device, model = asyncio.run(get_device_from_id(hass, "ha-ids-dev"))

        assert device is ids_dev
        assert model == "IDS"

    def test_returns_session_as_shc_when_matches_information_unique_id(self):
        session = _make_session(devices=[], unique_id="shc-serial-99")
        hass = _make_hass(data={DOMAIN: {"e1": {DATA_SESSION: session}}})

        ha_shc = MagicMock()
        ha_shc.id = "ha-shc-controller"

        def fake_get_device(identifiers, connections):
            for _, dev_id in identifiers:
                if dev_id == "shc-serial-99":
                    return ha_shc
            return None

        mock_registry = MagicMock()
        mock_registry.async_get_device = fake_get_device

        with patch(
            "custom_components.bosch_shc.device_trigger.dr.async_get",
            return_value=mock_registry,
        ):
            device, model = asyncio.run(get_device_from_id(hass, "ha-shc-controller"))

        assert device is session
        assert model == "SHC"

    def test_no_intrusion_system_skips_ids_check(self):
        """When intrusion_system is falsy, that branch is skipped."""
        session = _make_session(devices=[], intrusion_system=None)
        hass = _make_hass(data={DOMAIN: {"e1": {DATA_SESSION: session}}})

        mock_registry = MagicMock()
        mock_registry.async_get_device.return_value = None

        with patch(
            "custom_components.bosch_shc.device_trigger.dr.async_get",
            return_value=mock_registry,
        ):
            device, model = asyncio.run(get_device_from_id(hass, "any-id"))

        assert device is None

    def test_multiple_config_entries_iterates_all(self):
        """If first entry has no match, second entry is checked."""
        shc_dev = _make_shc_device(device_id="shc-2", model="MD")
        session1 = _make_session(devices=[], unique_id="uid-1")
        session2 = _make_session(devices=[shc_dev], unique_id="uid-2")
        hass = _make_hass(
            data={DOMAIN: {
                "e1": {DATA_SESSION: session1},
                "e2": {DATA_SESSION: session2},
            }}
        )

        ha_device = MagicMock()
        ha_device.id = "ha-md-dev"

        def fake_get_device(identifiers, connections):
            for _, dev_id in identifiers:
                if dev_id == "shc-2":
                    return ha_device
            return None

        mock_registry = MagicMock()
        mock_registry.async_get_device = fake_get_device

        with patch(
            "custom_components.bosch_shc.device_trigger.dr.async_get",
            return_value=mock_registry,
        ):
            device, model = asyncio.run(get_device_from_id(hass, "ha-md-dev"))

        assert device is shc_dev
        assert model == "MD"

    def test_continue_when_ha_device_id_does_not_match(self):
        """When ha device id != device_id, continue to next device (line 59)."""
        shc_dev1 = _make_shc_device(device_id="shc-a", model="WRC2")
        shc_dev2 = _make_shc_device(device_id="shc-b", model="MD")
        session = _make_session(devices=[shc_dev1, shc_dev2])
        hass = _make_hass(data={DOMAIN: {"e1": {DATA_SESSION: session}}})

        ha_device_a = MagicMock()
        ha_device_a.id = "ha-dev-a"
        ha_device_b = MagicMock()
        ha_device_b.id = "ha-dev-b"

        def fake_get_device(identifiers, connections):
            for _, dev_id in identifiers:
                if dev_id == "shc-a":
                    return ha_device_a
                if dev_id == "shc-b":
                    return ha_device_b
            return None

        mock_registry = MagicMock()
        mock_registry.async_get_device = fake_get_device

        with patch(
            "custom_components.bosch_shc.device_trigger.dr.async_get",
            return_value=mock_registry,
        ):
            # Ask for "ha-dev-b" — shc_dev1 maps to "ha-dev-a", so continue
            device, model = asyncio.run(get_device_from_id(hass, "ha-dev-b"))

        assert device is shc_dev2
        assert model == "MD"


# ===========================================================================
# 3. async_get_triggers
# ===========================================================================

class TestAsyncGetTriggers:

    def _run_get_triggers(self, ha_device_id, model, scenario_names=None, shc_device_id="shc-1"):
        shc_dev = _make_shc_device(device_id=shc_device_id, model=model)
        session = _make_session(devices=[shc_dev], unique_id="serial-001")
        if scenario_names is not None:
            session.scenario_names = scenario_names

        hass = _make_hass(data={DOMAIN: {"e1": {DATA_SESSION: session}}})

        ha_device = MagicMock()
        ha_device.id = ha_device_id

        def fake_get_device(identifiers, connections):
            for _, dev_id in identifiers:
                if dev_id == shc_device_id:
                    return ha_device
            return None

        mock_registry = MagicMock()
        mock_registry.async_get_device = fake_get_device

        with patch(
            "custom_components.bosch_shc.device_trigger.dr.async_get",
            return_value=mock_registry,
        ):
            return asyncio.run(async_get_triggers(hass, ha_device_id))

    def test_wrc2_returns_press_triggers_for_all_buttons_and_types(self):
        triggers = self._run_get_triggers("ha-wrc2", "WRC2")
        types = {t[CONF_TYPE] for t in triggers}
        subtypes = {t[CONF_SUBTYPE] for t in triggers}
        assert "PRESS_SHORT" in types
        assert "PRESS_LONG" in types
        assert "PRESS_LONG_RELEASED" in types
        assert "UPPER_BUTTON" in subtypes
        assert "LOWER_BUTTON" in subtypes
        # 3 trigger types × 2 subtypes = 6 triggers
        assert len(triggers) == 6

    def test_switch2_returns_press_triggers_for_all_buttons(self):
        triggers = self._run_get_triggers("ha-sw2", "SWITCH2")
        subtypes = {t[CONF_SUBTYPE] for t in triggers}
        assert INPUTS_EVENTS_SUBTYPES_SWITCH2.issubset(subtypes)
        # 3 types × 4 subtypes = 12
        assert len(triggers) == 12

    def test_md_returns_motion_trigger(self):
        triggers = self._run_get_triggers("ha-md", "MD")
        assert len(triggers) == 1
        assert triggers[0][CONF_TYPE] == "MOTION"
        assert triggers[0][CONF_SUBTYPE] == ""

    def test_sd_returns_alarm_triggers_for_all_subtypes(self):
        triggers = self._run_get_triggers("ha-sd", "SD")
        types = {t[CONF_TYPE] for t in triggers}
        subtypes = {t[CONF_SUBTYPE] for t in triggers}
        assert types == {"ALARM"}
        assert ALARM_EVENTS_SUBTYPES_SD.issubset(subtypes)

    def test_smoke_detection_system_returns_alarm_triggers(self):
        triggers = self._run_get_triggers("ha-sds", "SMOKE_DETECTION_SYSTEM")
        types = {t[CONF_TYPE] for t in triggers}
        subtypes = {t[CONF_SUBTYPE] for t in triggers}
        assert types == {"ALARM"}
        assert ALARM_EVENTS_SUBTYPES_SDS.issubset(subtypes)

    def test_shc_returns_scenario_triggers(self):
        """SHC controller type returns one SCENARIO trigger per scenario_name."""
        scenario_names = ["Away", "Home", "Night"]
        session = _make_session(devices=[], unique_id="shc-serial-001")
        session.scenario_names = scenario_names

        hass = _make_hass(data={DOMAIN: {"e1": {DATA_SESSION: session}}})

        ha_shc = MagicMock()
        ha_shc.id = "ha-shc"

        def fake_get_device(identifiers, connections):
            for _, dev_id in identifiers:
                if dev_id == "shc-serial-001":
                    return ha_shc
            return None

        mock_registry = MagicMock()
        mock_registry.async_get_device = fake_get_device

        with patch(
            "custom_components.bosch_shc.device_trigger.dr.async_get",
            return_value=mock_registry,
        ):
            triggers = asyncio.run(async_get_triggers(hass, "ha-shc"))

        assert len(triggers) == 3
        trigger_types = {t[CONF_TYPE] for t in triggers}
        assert trigger_types == {"SCENARIO"}
        trigger_subtypes = {t[CONF_SUBTYPE] for t in triggers}
        assert trigger_subtypes == set(scenario_names)

    def test_unknown_model_returns_no_triggers(self):
        triggers = self._run_get_triggers("ha-unknown", "SOME_WEIRD_MODEL")
        assert triggers == []

    def test_device_not_found_raises_invalid_config(self):
        from homeassistant.components.device_automation.exceptions import (
            InvalidDeviceAutomationConfig,
        )
        session = _make_session(devices=[])
        hass = _make_hass(data={DOMAIN: {"e1": {DATA_SESSION: session}}})
        mock_registry = MagicMock()
        mock_registry.async_get_device.return_value = None

        with patch(
            "custom_components.bosch_shc.device_trigger.dr.async_get",
            return_value=mock_registry,
        ):
            with pytest.raises(InvalidDeviceAutomationConfig):
                asyncio.run(async_get_triggers(hass, "no-such-device"))

    def test_all_trigger_dicts_have_required_keys(self):
        triggers = self._run_get_triggers("ha-wrc2", "WRC2")
        for t in triggers:
            assert CONF_PLATFORM in t
            assert CONF_DEVICE_ID in t
            assert CONF_DOMAIN in t
            assert CONF_TYPE in t
            assert CONF_SUBTYPE in t

    def test_trigger_device_id_matches_input(self):
        triggers = self._run_get_triggers("my-specific-device-id", "MD")
        for t in triggers:
            assert t[CONF_DEVICE_ID] == "my-specific-device-id"

    def test_trigger_domain_is_bosch_shc(self):
        triggers = self._run_get_triggers("ha-md", "MD")
        for t in triggers:
            assert t[CONF_DOMAIN] == DOMAIN


# ===========================================================================
# 4. async_attach_trigger
# ===========================================================================

class TestAsyncAttachTrigger:
    """Tests for async_attach_trigger.

    event_trigger.TRIGGER_SCHEMA runs a loop-aware validator (_validate_event_types)
    that fails outside a running HA event loop.  We patch both the schema and the
    downstream async_attach_trigger so our tests stay harness-free.
    """

    def _make_config(
        self,
        device_id="ha-device-1",
        event_type="PRESS_SHORT",
        subtype="UPPER_BUTTON",
    ):
        return {
            CONF_PLATFORM: "device",
            CONF_DEVICE_ID: device_id,
            CONF_DOMAIN: DOMAIN,
            CONF_TYPE: event_type,
            CONF_SUBTYPE: subtype,
        }

    def _run(self, hass, config, action=None, info=None, captured=None):
        """Run async_attach_trigger with both event_trigger helpers patched."""
        if action is None:
            action = MagicMock()
        remove_mock = MagicMock()

        async def fake_attach(h, event_cfg, act, automation_info, platform_type="device"):
            if captured is not None:
                captured["event_cfg"] = event_cfg
                captured["platform_type"] = platform_type
            return remove_mock

        with patch(
            "custom_components.bosch_shc.device_trigger.event_trigger.TRIGGER_SCHEMA",
            side_effect=lambda x: x,
        ), patch(
            "custom_components.bosch_shc.device_trigger.event_trigger.async_attach_trigger",
            side_effect=fake_attach,
        ) as mock_attach:
            result = asyncio.run(
                async_attach_trigger(hass, config, action, info or {})
            )
        return result, remove_mock, mock_attach

    def test_delegates_to_event_trigger_attach(self):
        hass = _make_hass()
        config = self._make_config()
        result, remove_mock, mock_attach = self._run(hass, config)
        mock_attach.assert_called_once()
        assert result is remove_mock

    def test_event_config_contains_device_id(self):
        hass = _make_hass()
        config = self._make_config(device_id="dev-xyz")
        captured = {}
        self._run(hass, config, captured=captured)
        assert captured["event_cfg"]["event_data"][ATTR_DEVICE_ID] == "dev-xyz"

    def test_event_config_contains_event_type(self):
        hass = _make_hass()
        config = self._make_config(event_type="PRESS_LONG")
        captured = {}
        self._run(hass, config, captured=captured)
        assert captured["event_cfg"]["event_data"][ATTR_EVENT_TYPE] == "PRESS_LONG"

    def test_event_config_contains_event_subtype(self):
        hass = _make_hass()
        config = self._make_config(subtype="LOWER_BUTTON")
        captured = {}
        self._run(hass, config, captured=captured)
        assert captured["event_cfg"]["event_data"][ATTR_EVENT_SUBTYPE] == "LOWER_BUTTON"

    def test_event_type_is_bosch_shc_event(self):
        hass = _make_hass()
        config = self._make_config()
        captured = {}
        self._run(hass, config, captured=captured)
        assert captured["event_cfg"]["event_type"] == EVENT_BOSCH_SHC

    def test_platform_type_is_device(self):
        hass = _make_hass()
        config = self._make_config()
        captured = {}
        self._run(hass, config, captured=captured)
        assert captured["platform_type"] == "device"

    def test_motion_type_is_valid(self):
        hass = _make_hass()
        config = self._make_config(event_type="MOTION", subtype="")
        captured = {}
        self._run(hass, config, captured=captured)
        assert captured["event_cfg"]["event_data"][ATTR_EVENT_TYPE] == "MOTION"

    def test_scenario_type_is_valid(self):
        hass = _make_hass()
        config = self._make_config(event_type="SCENARIO", subtype="Away")
        captured = {}
        self._run(hass, config, captured=captured)
        assert captured["event_cfg"]["event_data"][ATTR_EVENT_TYPE] == "SCENARIO"

    def test_alarm_type_is_valid(self):
        hass = _make_hass()
        config = self._make_config(event_type="ALARM", subtype="INTRUSION_ALARM")
        captured = {}
        self._run(hass, config, captured=captured)
        assert captured["event_cfg"]["event_data"][ATTR_EVENT_TYPE] == "ALARM"

    def test_press_long_released_type_is_valid(self):
        hass = _make_hass()
        config = self._make_config(event_type="PRESS_LONG_RELEASED", subtype="UPPER_BUTTON")
        captured = {}
        self._run(hass, config, captured=captured)
        assert captured["event_cfg"]["event_data"][ATTR_EVENT_TYPE] == "PRESS_LONG_RELEASED"


# ===========================================================================
# 5. IDS device path (intrusion system)
# ===========================================================================

class TestIDSDeviceTriggers:

    def test_ids_device_returns_empty_triggers(self):
        """IDS ('IDS' model) is not WRC2/SWITCH2/MD/SD/SDS/SHC → no triggers."""
        ids_dev = MagicMock()
        ids_dev.id = "ids-device-1"

        session = _make_session(devices=[], intrusion_system=ids_dev)
        hass = _make_hass(data={DOMAIN: {"e1": {DATA_SESSION: session}}})

        ha_ids = MagicMock()
        ha_ids.id = "ha-ids-1"

        def fake_get_device(identifiers, connections):
            for _, dev_id in identifiers:
                if dev_id == "ids-device-1":
                    return ha_ids
            return None

        mock_registry = MagicMock()
        mock_registry.async_get_device = fake_get_device

        with patch(
            "custom_components.bosch_shc.device_trigger.dr.async_get",
            return_value=mock_registry,
        ):
            triggers = asyncio.run(async_get_triggers(hass, "ha-ids-1"))

        # IDS model is not in any of the trigger type branches
        assert triggers == []


# ===========================================================================
# 6. SWITCH2 model: verify the default branch is NOT logged (not reached)
# ===========================================================================

class TestSwitch2BranchCoverage:

    def test_switch2_all_trigger_subtypes_present(self):
        """SWITCH2 model has 4 subtypes × 3 types = 12 triggers."""
        shc_dev = _make_shc_device(device_id="sw2-1", model="SWITCH2")
        session = _make_session(devices=[shc_dev])
        hass = _make_hass(data={DOMAIN: {"e1": {DATA_SESSION: session}}})

        ha_device = MagicMock()
        ha_device.id = "ha-sw2"

        def fake_get_device(identifiers, connections):
            for _, dev_id in identifiers:
                if dev_id == "sw2-1":
                    return ha_device
            return None

        mock_registry = MagicMock()
        mock_registry.async_get_device = fake_get_device

        with patch(
            "custom_components.bosch_shc.device_trigger.dr.async_get",
            return_value=mock_registry,
        ):
            triggers = asyncio.run(async_get_triggers(hass, "ha-sw2"))

        expected_subtypes = {
            "LOWER_LEFT_BUTTON",
            "LOWER_RIGHT_BUTTON",
            "UPPER_LEFT_BUTTON",
            "UPPER_RIGHT_BUTTON",
        }
        found_subtypes = {t[CONF_SUBTYPE] for t in triggers}
        assert expected_subtypes.issubset(found_subtypes)
        assert len(triggers) == 12
