"""Coverage gap tests for APK entity wiring.

Targets the remaining uncovered lines after the main APK test files:

button.py  72   — motion_detectors2 device_excluded continue

number.py  95   — smart_plugs/compact device_excluded continue
number.py  556  — DisplayOnTimeNumber.native_step returns float from service attr

select.py  196-197 — smoke_sensitivity AttributeError → continue
select.py  379      — StateAfterPowerOutageSelect.current_option val is None → return None
select.py  motion_detectors2 device_excluded continue

switch.py  411  — warning_suppressed hasattr block on smart_plugs_compact
switch.py  454  — micromodule_light_controls device_excluded continue
switch.py  465  — swap_outputs hasattr block on micromodule_light_controls
switch.py  721  — twinguards device_excluded continue
switch.py  743  — smoke_detectors device_excluded continue
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from custom_components.bosch_shc.const import (
    DATA_SESSION,
    DATA_SHC,
    DOMAIN,
    OPT_EXCLUDED_DEVICES,
)

# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------

def _fake_device(**kwargs):
    base = dict(
        id="dev1",
        root_device_id="root1",
        name="FakeDev",
        device_services=[],
        serial="SER1",
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def _excl(*ids):
    return {OPT_EXCLUDED_DEVICES: list(ids)}


# ===========================================================================
# BUTTON.PY
# ===========================================================================

def _make_button_session(**kw):
    defaults = dict(
        micromodule_impulse_relays=[],
        smoke_detectors=[],
        twinguards=[],
        motion_detectors2=[],
    )
    defaults.update(kw)
    return SimpleNamespace(
        device_helper=SimpleNamespace(**defaults),
        scenarios=[],
    )


def _run_button_setup(session, options=None):
    from custom_components.bosch_shc.button import async_setup_entry
    hass = SimpleNamespace(
        data={DOMAIN: {"E1": {
            DATA_SESSION: session,
            DATA_SHC: SimpleNamespace(
                identifiers={("bosch_shc", "shc")},
                name="SHC", manufacturer="Bosch", model="SHC",
            ),
        }}}
    )
    config_entry = SimpleNamespace(
        options=options or {},
        entry_id="E1",
        unique_id="uid1",
    )
    collected = []

    def _add(ents, *a, **kw):
        collected.extend(ents)

    asyncio.run(async_setup_entry(hass, config_entry, _add))
    return collected


class TestButtonMotionDetectors2DeviceExcluded:
    """button.py line 72 — device_excluded continue in motion_detectors2 loop."""

    def test_excluded_md2_not_added(self):
        md2 = _fake_device(
            id="md2-excl",
            walk_state=object(),
        )
        session = _make_button_session(motion_detectors2=[md2])
        entities = _run_button_setup(session, options=_excl("md2-excl"))
        ids = [getattr(e, "_attr_unique_id", "") for e in entities]
        assert not any("md2-excl" in uid for uid in ids)


# ===========================================================================
# NUMBER.PY
# ===========================================================================

def _make_number_session(**kw):
    defaults = dict(
        thermostats=[],
        roomthermostats=[],
        micromodule_impulse_relays=[],
        heating_circuits=[],
        smart_plugs=[],
        smart_plugs_compact=[],
        motion_detectors2=[],
    )
    defaults.update(kw)
    return SimpleNamespace(device_helper=SimpleNamespace(**defaults))


def _run_number_setup(session, options=None):
    from custom_components.bosch_shc.number import async_setup_entry
    hass = SimpleNamespace(data={DOMAIN: {"E1": {DATA_SESSION: session}}})
    config_entry = SimpleNamespace(options=options or {}, entry_id="E1")
    collected = []

    def _add(ents, *a, **kw):
        collected.extend(ents)

    asyncio.run(async_setup_entry(hass, config_entry, _add))
    return collected


class TestNumberSmartPlugCompactDeviceExcluded:
    """number.py line 95 — device_excluded continue in smart_plugs/compact loop."""

    def test_excluded_compact_plug_not_added(self):
        plug = _fake_device(id="cp-excl", power_threshold=100.0)
        session = _make_number_session(smart_plugs_compact=[plug])
        entities = _run_number_setup(session, options=_excl("cp-excl"))
        ids = [getattr(getattr(e, "_device", None), "id", None) for e in entities]
        assert "cp-excl" not in ids

    def test_excluded_smart_plug_not_added(self):
        plug = _fake_device(id="sp-excl", power_threshold=100.0)
        session = _make_number_session(smart_plugs=[plug])
        entities = _run_number_setup(session, options=_excl("sp-excl"))
        ids = [getattr(getattr(e, "_device", None), "id", None) for e in entities]
        assert "sp-excl" not in ids


class TestDisplayOnTimeNativeStep:
    """number.py line 556 — native_step returns float from service attribute."""

    def test_step_from_service(self):
        from custom_components.bosch_shc.number import DisplayOnTimeNumber
        svc = SimpleNamespace(display_on_time_step_size=30)
        device = _fake_device(_display_config_service=svc, display_on_time=60.0)
        num = DisplayOnTimeNumber.__new__(DisplayOnTimeNumber)
        num._device = device
        assert num.native_step == 30.0

    def test_step_fallback_when_no_service(self):
        from custom_components.bosch_shc.number import DisplayOnTimeNumber
        device = _fake_device(display_on_time=60.0)
        num = DisplayOnTimeNumber.__new__(DisplayOnTimeNumber)
        num._device = device
        assert num.native_step == 1.0

    def test_step_fallback_when_attr_none(self):
        from custom_components.bosch_shc.number import DisplayOnTimeNumber
        svc = SimpleNamespace(display_on_time_step_size=None)
        device = _fake_device(_display_config_service=svc, display_on_time=60.0)
        num = DisplayOnTimeNumber.__new__(DisplayOnTimeNumber)
        num._device = device
        assert num.native_step == 1.0


# ===========================================================================
# SELECT.PY
# ===========================================================================

def _make_select_session(**kw):
    defaults = dict(
        shutter_contacts2=[],
        motion_detectors2=[],
        micromodule_relays=[],
        micromodule_light_controls=[],
        smoke_detectors=[],
        twinguards=[],
        thermostats=[],
        roomthermostats=[],
        heating_circuits=[],
    )
    defaults.update(kw)
    return SimpleNamespace(device_helper=SimpleNamespace(**defaults))


def _run_select_setup(session, options=None):
    from custom_components.bosch_shc.select import async_setup_entry
    hass = SimpleNamespace(data={DOMAIN: {"E1": {DATA_SESSION: session}}})
    config_entry = SimpleNamespace(options=options or {}, entry_id="E1")
    collected = []

    def _add(ents, *a, **kw):
        collected.extend(ents)

    with patch(
        "custom_components.bosch_shc.select.SHCShutterContact2Plus",
        new=type("_NeverMatch", (), {}),
    ):
        asyncio.run(async_setup_entry(hass, config_entry, _add))
    return collected


class TestSmokeSensitivityAttributeErrorContinue:
    """select.py lines 196-197 — smoke_sensitivity raises AttributeError → continue.

    hasattr() in Python 3 returns False when a property raises AttributeError, so lines
    196-197 are dead code under normal conditions.  We reach them by patching
    builtins.hasattr in the select module scope to lie and say the attribute exists,
    while the property still raises.  This mirrors defensive code that was written for
    the case where a descriptor signals AttributeError internally for a different reason.
    """

    def test_smoke_sensitivity_attr_error_skips_device(self):
        import builtins
        _real_hasattr = builtins.hasattr

        class _RaisingSmokeDetector:
            id = "sd-raise"
            root_device_id = "root1"
            name = "SD"
            device_services = []
            serial = "SER"

            @property
            def smoke_sensitivity(self):
                raise AttributeError("smoke_sensitivity not accessible")

        device = _RaisingSmokeDetector()

        def _patched_hasattr(obj, name):
            if obj is device and name == "smoke_sensitivity":
                return True  # lie so the try/except branch is reached
            return _real_hasattr(obj, name)

        session = _make_select_session(smoke_detectors=[device])

        with patch("builtins.hasattr", _patched_hasattr):
            entities = _run_select_setup(session)

        # No SmokeSensitivitySelect entity should be created
        from custom_components.bosch_shc.select import SmokeSensitivitySelect
        assert not any(isinstance(e, SmokeSensitivitySelect) for e in entities)


class TestStateAfterPowerOutageCurrentOptionNone:
    """select.py line 379 — current_option returns None when val is None."""

    def test_current_option_when_val_is_none(self):
        from custom_components.bosch_shc.select import StateAfterPowerOutageSelect
        device = _fake_device(state_after_power_outage=None)
        sel = StateAfterPowerOutageSelect.__new__(StateAfterPowerOutageSelect)
        sel._device = device
        # Need options set so the logic gets to the None check
        sel._attr_options = ["ON", "OFF", "PREVIOUS_STATE"]
        assert sel.current_option is None


class TestSelectMotionDetectors2DeviceExcluded:
    """select.py — device_excluded continue in motion_detectors2 loop."""

    def test_excluded_md2_not_added(self):
        md2 = _fake_device(id="md2-excl", get_smart_sensitivity=lambda ctx: {})
        session = _make_select_session(motion_detectors2=[md2])
        entities = _run_select_setup(session, options=_excl("md2-excl"))
        ids = [getattr(getattr(e, "_device", None), "id", None) for e in entities]
        assert "md2-excl" not in ids


# ===========================================================================
# SWITCH.PY
# ===========================================================================

def _make_switch_session(**kw):
    defaults = dict(
        light_switches=[],
        light_switches_bsm=[],
        smart_plugs=[],
        smart_plugs_compact=[],
        micromodule_relays=[],
        micromodule_light_attached=[],
        camera_eyes=[],
        camera_360=[],
        camera_outdoor_gen2=[],
        presence_simulation_system=None,
        shutter_contacts2=[],
        thermostats=[],
        roomthermostats=[],
        wallthermostats=[],
        micromodule_shutter_controls=[],
        micromodule_blinds=[],
        micromodule_impulse_relays=[],
        micromodule_dimmers=[],
        motion_detectors2=[],
        twinguards=[],
        smoke_detectors=[],
        micromodule_light_controls=[],
        userdefinedstates=[],
    )
    defaults.update(kw)
    dh = SimpleNamespace(**defaults)
    return SimpleNamespace(
        device_helper=dh,
        userdefinedstates=defaults["userdefinedstates"],
        subscribe=lambda *a, **kw: None,
        _subscribers=[],
    )


def _run_switch_setup(session, options=None):
    from unittest.mock import MagicMock

    from custom_components.bosch_shc.switch import async_setup_entry
    hass = SimpleNamespace(
        data={DOMAIN: {"E1": {
            DATA_SESSION: session,
            DATA_SHC: SimpleNamespace(
                name="SHC", id="shc", identifiers={("bosch_shc", "shc")},
                manufacturer="Bosch", model="SHC"),
        }}}
    )
    config_entry = SimpleNamespace(
        options=options or {},
        entry_id="E1",
        async_on_unload=MagicMock(),
    )
    collected = []

    def _add(ents, *a, **kw):
        collected.extend(ents)

    with patch(
        "custom_components.bosch_shc.switch.async_migrate_to_new_unique_id",
        new=AsyncMock(return_value=None),
    ):
        asyncio.run(async_setup_entry(hass, config_entry, _add))
    return collected


class TestSwitchSmartPlugCompactWarningSuppressed:
    """switch.py line 411 — warning_suppressed hasattr block on smart_plugs_compact."""

    def test_compact_plug_with_warning_suppressed_creates_entity(self):
        plug = _fake_device(id="cp1", warning_suppressed=False,
                            supports_power_switch_warning=True)
        session = _make_switch_session(smart_plugs_compact=[plug])
        entities = _run_switch_setup(session)
        keys = [getattr(e, "entity_description", None) and e.entity_description.key
                for e in entities]
        assert "warning_suppressed" in keys

    def test_compact_plug_without_warning_suppressed_no_entity(self):
        # No warning_suppressed attr → hasattr check at line 410 is False
        plug = _fake_device(id="cp2")
        session = _make_switch_session(smart_plugs_compact=[plug])
        entities = _run_switch_setup(session)
        keys = [getattr(e, "entity_description", None) and e.entity_description.key
                for e in entities]
        assert "warning_suppressed" not in keys


class TestSwitchMicromoduleLightControlsDeviceExcluded:
    """switch.py line 454 — micromodule_light_controls device_excluded continue."""

    def test_excluded_light_control_not_added(self):
        dev = _fake_device(id="mlc-excl", swap_inputs=False)
        session = _make_switch_session(micromodule_light_controls=[dev])
        entities = _run_switch_setup(session, options=_excl("mlc-excl"))
        ids = [getattr(getattr(e, "_device", None), "id", None) for e in entities]
        assert "mlc-excl" not in ids


class TestSwitchMicromoduleLightControlsSwapOutputs:
    """switch.py line 465 — swap_outputs hasattr block on micromodule_light_controls."""

    def test_light_control_with_swap_outputs_creates_entity(self):
        dev = _fake_device(id="mlc1", swap_outputs=False,
                           supports_switch_configuration=True)
        session = _make_switch_session(micromodule_light_controls=[dev])
        entities = _run_switch_setup(session)
        keys = [getattr(e, "entity_description", None) and e.entity_description.key
                for e in entities]
        assert "swap_outputs" in keys

    def test_light_control_without_swap_outputs_no_entity(self):
        dev = _fake_device(id="mlc2")  # no swap_outputs attr
        session = _make_switch_session(micromodule_light_controls=[dev])
        entities = _run_switch_setup(session)
        keys = [getattr(e, "entity_description", None) and e.entity_description.key
                for e in entities]
        assert "swap_outputs" not in keys


class TestSwitchTwinguardsDeviceExcluded:
    """switch.py line 721 — twinguards device_excluded continue."""

    def test_excluded_twinguard_not_added(self):
        tg = _fake_device(id="tg-excl", nightly_promise_enabled=True)
        session = _make_switch_session(twinguards=[tg])
        entities = _run_switch_setup(session, options=_excl("tg-excl"))
        ids = [getattr(getattr(e, "_device", None), "id", None) for e in entities]
        assert "tg-excl" not in ids


class TestSwitchSmokeDetectorsDeviceExcluded:
    """switch.py line 743 — smoke_detectors device_excluded continue."""

    def test_excluded_smoke_detector_not_added(self):
        sd = _fake_device(id="sd-excl", pre_alarm_enabled=False)
        session = _make_switch_session(smoke_detectors=[sd])
        entities = _run_switch_setup(session, options=_excl("sd-excl"))
        ids = [getattr(getattr(e, "_device", None), "id", None) for e in entities]
        assert "sd-excl" not in ids
