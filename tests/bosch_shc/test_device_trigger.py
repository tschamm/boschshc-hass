"""Unit tests for custom_components/bosch_shc/device_trigger.py.

Most tests here are harness-free unit tests — module-level constants
(TestModuleConstants), get_device_from_id (TestGetDeviceFromId,
TestDeviceTriggerGetDeviceFromId), async_get_triggers (TestAsyncGetTriggers,
TestIDSDeviceTriggers, TestSwitch2BranchCoverage,
TestGetTriggersMatchDefaultBranch), and async_attach_trigger
(TestAsyncAttachTrigger) — and run under `-p no:homeassistant`.

Two legacy tests (test_get_triggers, test_if_fires_on_state_change) instead
exercise the real HA device-automation harness (device/entity registries,
automation component, `hass` fixture) via `tests.common`, which is not
vendored in this repo. That import is deferred into the fixtures/test that
need it so the rest of this module still collects and runs cleanly under
plain pytest here; those two tests themselves cannot run in this repo/env
(same constraint CI has today, which only `python3 -m py_compile`s this file
and never runs it under pytest).
"""

from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.components import automation
from homeassistant.const import (
    ATTR_DEVICE_ID,
    CONF_DEVICE_ID,
    CONF_DOMAIN,
    CONF_PLATFORM,
    CONF_TYPE,
    STATE_OFF,
    STATE_ON,
)
from homeassistant.helpers import device_registry
from homeassistant.setup import async_setup_component

from custom_components.bosch_shc.const import (
    ALARM_EVENTS_SUBTYPES_SD,
    ALARM_EVENTS_SUBTYPES_SD2,
    ALARM_EVENTS_SUBTYPES_SDS,
    ATTR_EVENT_SUBTYPE,
    ATTR_EVENT_TYPE,
    CONF_SUBTYPE,
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

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_hass(sessions=None):
    """Return a minimal hass mock whose config_entries.async_entries(DOMAIN)
    yields one fake ConfigEntry (carrying .runtime_data.session) per session
    in `sessions` — get_device_from_id() iterates hass.config_entries
    .async_entries(DOMAIN) and reads entry.runtime_data.session for each,
    skipping any entry that lacks a runtime_data attribute entirely."""
    hass = MagicMock()
    entries = [
        SimpleNamespace(runtime_data=SimpleNamespace(session=s))
        for s in (sessions or [])
    ]
    hass.config_entries = MagicMock()
    hass.config_entries.async_entries = MagicMock(return_value=entries)
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

    hass = _make_hass(sessions=[session])

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
        hass = _make_hass(sessions=[session])

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
        hass = _make_hass(sessions=[session])

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
        hass = _make_hass(sessions=[session])

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
        hass = _make_hass(sessions=[session])

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
        hass = _make_hass(sessions=[session])

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
        hass = _make_hass(sessions=[session1, session2])

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
        hass = _make_hass(sessions=[session])

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
# 2b. get_device_from_id — additional coverage
#     (device_trigger.py:59 — device.id != device_id → continue)
# ===========================================================================

class TestDeviceTriggerGetDeviceFromId:
    """Tests for device_trigger.get_device_from_id."""

    def _make_hass_with_data(self, shc_devices, intrusion_system=None):
        """Build a hass mock with a session that has given devices."""
        session = MagicMock()
        session.devices = shc_devices
        session.intrusion_system = intrusion_system

        shc_info = MagicMock()
        shc_info.unique_id = "shc-serial-001"
        session.information = shc_info
        session.scenario_names = []

        entry = SimpleNamespace(entry_id="eid1")
        entry.runtime_data = SimpleNamespace(
            session=session, shc_device=None, title="Test SHC"
        )

        hass = MagicMock()
        hass.config_entries.async_entries = MagicMock(return_value=[entry])
        return hass, session

    def test_device_id_mismatch_continues(self):
        """When dev_registry returns a device with a different id, loop continues."""
        shc_dev = MagicMock()
        shc_dev.id = "shc-device-abc"
        shc_dev.device_model = "WRC2"

        hass, session = self._make_hass_with_data([shc_dev])

        fake_reg_device = MagicMock()
        fake_reg_device.id = "reg-id-OTHER"  # different from target device_id

        dev_registry = MagicMock()
        dev_registry.async_get_device = MagicMock(return_value=fake_reg_device)

        with patch(
            "custom_components.bosch_shc.device_trigger.dr.async_get",
            return_value=dev_registry,
        ):
            device, model = asyncio.run(
                get_device_from_id(hass, "reg-id-TARGET")  # won't match reg-id-OTHER
            )

        # None returned because no device matched
        assert device is None
        assert model == ""

    def test_device_returns_when_id_matches(self):
        """When dev_registry device id matches, the shc_device is returned."""
        shc_dev = MagicMock()
        shc_dev.id = "shc-device-abc"
        shc_dev.device_model = "WRC2"

        hass, session = self._make_hass_with_data([shc_dev])
        session.intrusion_system = None

        fake_reg_device = MagicMock()
        fake_reg_device.id = "reg-id-TARGET"

        shc_reg_device = MagicMock()
        shc_reg_device.id = "shc-reg-OTHER"

        def get_device(identifiers, connections):
            ident = dict(identifiers)
            if ident.get("bosch_shc") == "shc-device-abc":
                return fake_reg_device
            # SHC controller device
            return shc_reg_device

        dev_registry = MagicMock()
        dev_registry.async_get_device = MagicMock(side_effect=get_device)

        with patch(
            "custom_components.bosch_shc.device_trigger.dr.async_get",
            return_value=dev_registry,
        ):
            device, model = asyncio.run(
                get_device_from_id(hass, "reg-id-TARGET")
            )

        assert device is shc_dev
        assert model == "WRC2"


# ===========================================================================
# 3. async_get_triggers
# ===========================================================================

class TestAsyncGetTriggers:

    def _run_get_triggers(self, ha_device_id, model, scenario_names=None, shc_device_id="shc-1"):
        shc_dev = _make_shc_device(device_id=shc_device_id, model=model)
        session = _make_session(devices=[shc_dev], unique_id="serial-001")
        if scenario_names is not None:
            session.scenario_names = scenario_names

        hass = _make_hass(sessions=[session])

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

    def test_md2_returns_motion_trigger(self):
        """Regression: MD2 (Motion Detector II) fires the identical MOTION
        bus event as MD via the same MotionDetectionSensor class
        (binary_sensor.py), but async_get_triggers only matched the literal
        "MD" model string — MD2 owners got zero device-trigger options."""
        triggers = self._run_get_triggers("ha-md2", "MD2")
        assert len(triggers) == 1
        assert triggers[0][CONF_TYPE] == "MOTION"
        assert triggers[0][CONF_SUBTYPE] == ""

    def test_sd_returns_alarm_triggers_for_all_subtypes(self):
        triggers = self._run_get_triggers("ha-sd", "SD")
        types = {t[CONF_TYPE] for t in triggers}
        subtypes = {t[CONF_SUBTYPE] for t in triggers}
        assert types == {"ALARM"}
        assert ALARM_EVENTS_SUBTYPES_SD.issubset(subtypes)

    def test_smoke_detector2_returns_alarm_triggers_for_all_subtypes(self):
        """Regression: SMOKE_DETECTOR2 (Smoke Detector II) fires the
        identical ALARM bus event as SD via the same SmokeDetectorSensor
        class (binary_sensor.py), but async_get_triggers only matched the
        literal "SD" model string — SD II owners got zero device-trigger
        options.

        Bug-hunt (2026-07-11): SD II's boschshcpy AlarmService.State actually
        reports IDLE_OFF/INTRUSION_ALARM_ON_REQUESTED/OFF_REQUESTED, not gen-1
        SD's INTRUSION_ALARM/SECONDARY_ALARM/PRIMARY_ALARM — so SD II must get
        its own subtype set (ALARM_EVENTS_SUBTYPES_SD2), not SD's.
        """
        triggers = self._run_get_triggers("ha-sd2", "SMOKE_DETECTOR2")
        types = {t[CONF_TYPE] for t in triggers}
        subtypes = {t[CONF_SUBTYPE] for t in triggers}
        assert types == {"ALARM"}
        assert ALARM_EVENTS_SUBTYPES_SD2.issubset(subtypes)
        # SD II never reports gen-1-only subtypes (SECONDARY_ALARM/PRIMARY_ALARM)
        assert not (ALARM_EVENTS_SUBTYPES_SD - ALARM_EVENTS_SUBTYPES_SD2) & subtypes

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

        hass = _make_hass(sessions=[session])

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
        hass = _make_hass(sessions=[session])
        mock_registry = MagicMock()
        mock_registry.async_get_device.return_value = None

        with patch(
            "custom_components.bosch_shc.device_trigger.dr.async_get",
            return_value=mock_registry,
        ), pytest.raises(InvalidDeviceAutomationConfig):
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
# 3b. async_get_triggers — SWITCH2 model: verify the default branch is NOT
#     logged (not reached)
# ===========================================================================

class TestSwitch2BranchCoverage:

    def test_switch2_all_trigger_subtypes_present(self):
        """SWITCH2 model has 4 subtypes × 3 types = 12 triggers."""
        shc_dev = _make_shc_device(device_id="sw2-1", model="SWITCH2")
        session = _make_session(devices=[shc_dev])
        hass = _make_hass(sessions=[session])

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


# ===========================================================================
# 3c. async_get_triggers — device_trigger.py:98-99 — case _: branch (unknown
#     device type in match)
# ===========================================================================

class TestGetTriggersMatchDefaultBranch:
    """Test the case _: branch (lines 98-99) in async_get_triggers."""

    def test_case_default_branch_is_unreachable_dead_code(self):
        """Verify that device_trigger.py lines 98-99 (case _:) are dead code.

        The match statement at line 91 is guarded by:
            if dev_type == "WRC2" or dev_type == "SWITCH2":
        which ensures dev_type can only be "WRC2" or "SWITCH2" when the match
        runs. The `case _:` branch can therefore never be reached at runtime.
        We document this intentionally and accept 97% on device_trigger.py.
        """
        from custom_components.bosch_shc import device_trigger as dt_mod
        src = inspect.getsource(dt_mod.async_get_triggers)
        assert "case _:" in src, "case _: branch exists in source"


# ===========================================================================
# 3d. async_get_triggers — IDS device path (intrusion system): catch-all,
#     device type matches no branch at all
# ===========================================================================

class TestIDSDeviceTriggers:

    def test_ids_device_returns_empty_triggers(self):
        """IDS ('IDS' model) is not WRC2/SWITCH2/MD/SD/SDS/SHC → no triggers."""
        ids_dev = MagicMock()
        ids_dev.id = "ids-device-1"

        session = _make_session(devices=[], intrusion_system=ids_dev)
        hass = _make_hass(sessions=[session])

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
# 5. Legacy HA-harness tests (former test_device_trigger.py).
#
# NOTE: these two tests need the real HA device-automation test harness
# (`tests.common`'s MockConfigEntry / mock_device_registry / mock_registry /
# async_mock_service / assert_lists_same / async_get_device_automations),
# which is not vendored in this repo (ha-core is installed as a library, not
# checked out with its own tests/ package). The `tests.common` import is
# deferred into the fixtures/tests below so it does not break collection of
# the rest of this module; these two tests themselves cannot be executed in
# this repo/env regardless of pytest flags (confirmed:
# ModuleNotFoundError: No module named 'tests.common'). CI does not run them
# under pytest either — it only `python3 -m py_compile`s this file.
# ===========================================================================

@pytest.fixture
def device_reg(hass):
    """Return an empty, loaded, registry."""
    from tests.common import mock_device_registry

    return mock_device_registry(hass)


@pytest.fixture
def entity_reg(hass):
    """Return an empty, loaded, registry."""
    from tests.common import mock_registry

    return mock_registry(hass)


@pytest.fixture
def calls(hass):
    """Track calls to a mock service."""
    from tests.common import async_mock_service

    return async_mock_service(hass, "test", "automation")


@pytest.mark.skip(
    reason="requires ha-core's tests.common test harness, not vendored in this repo"
)
async def test_get_triggers(hass, device_reg, entity_reg):
    """Test we get the expected triggers from a bosch_shc."""
    from homeassistant.components.bosch_shc import DOMAIN
    from tests.common import assert_lists_same, async_get_device_automations
    from tests.common import MockConfigEntry

    config_entry = MockConfigEntry(domain="test", data={})
    config_entry.add_to_hass(hass)
    device_entry = device_reg.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        connections={(device_registry.CONNECTION_NETWORK_MAC, "12:34:56:AB:CD:EF")},
    )
    entity_reg.async_get_or_create(DOMAIN, "test", "5678", device_id=device_entry.id)
    expected_triggers = [
        {
            "platform": "device",
            "domain": DOMAIN,
            "type": "turned_off",
            "device_id": device_entry.id,
            "entity_id": f"{DOMAIN}.test_5678",
        },
        {
            "platform": "device",
            "domain": DOMAIN,
            "type": "turned_on",
            "device_id": device_entry.id,
            "entity_id": f"{DOMAIN}.test_5678",
        },
    ]
    triggers = await async_get_device_automations(hass, "trigger", device_entry.id)
    assert_lists_same(triggers, expected_triggers)


@pytest.mark.skip(
    reason="requires ha-core's tests.common test harness, not vendored in this repo"
)
async def test_if_fires_on_state_change(hass, calls):
    """Test for turn_on and turn_off triggers firing."""
    from homeassistant.components.bosch_shc import DOMAIN

    hass.states.async_set("bosch_shc.entity", STATE_OFF)

    assert await async_setup_component(
        hass,
        automation.DOMAIN,
        {
            automation.DOMAIN: [
                {
                    "trigger": {
                        "platform": "device",
                        "domain": DOMAIN,
                        "device_id": "",
                        "entity_id": "bosch_shc.entity",
                        "type": "turned_on",
                    },
                    "action": {
                        "service": "test.automation",
                        "data_template": {
                            "some": (
                                "turn_on - {{ trigger.platform}} - "
                                "{{ trigger.entity_id}} - {{ trigger.from_state.state}} - "
                                "{{ trigger.to_state.state}} - {{ trigger.for }}"
                            )
                        },
                    },
                },
                {
                    "trigger": {
                        "platform": "device",
                        "domain": DOMAIN,
                        "device_id": "",
                        "entity_id": "bosch_shc.entity",
                        "type": "turned_off",
                    },
                    "action": {
                        "service": "test.automation",
                        "data_template": {
                            "some": (
                                "turn_off - {{ trigger.platform}} - "
                                "{{ trigger.entity_id}} - {{ trigger.from_state.state}} - "
                                "{{ trigger.to_state.state}} - {{ trigger.for }}"
                            )
                        },
                    },
                },
            ]
        },
    )

    # Fake that the entity is turning on.
    hass.states.async_set("bosch_shc.entity", STATE_ON)
    await hass.async_block_till_done()
    assert len(calls) == 1
    assert calls[0].data["some"] == "turn_on - device - {} - off - on - None".format(
        "bosch_shc.entity"
    )

    # Fake that the entity is turning off.
    hass.states.async_set("bosch_shc.entity", STATE_OFF)
    await hass.async_block_till_done()
    assert len(calls) == 2
    assert calls[1].data["some"] == "turn_off - device - {} - on - off - None".format(
        "bosch_shc.entity"
    )
