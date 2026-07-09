"""Tests for the select platform (custom_components.bosch_shc.select).

Covers every SelectEntity subclass exposed by the integration: the
configuration-service selects gated by a `supports_*` flag plus a non-None
value (StateAfterPowerOutageSelect, DisplayDirectionSelect,
DisplayedTemperatureSelect, ValveTypeSelect, HeaterTypeSelect,
TerminalTypeSelect, SwitchTypeSelect, ActuatorTypeSelect, OutputModeSelect,
SmokeSensitivitySelect), the MD2/SC2+ sensitivity selects
(MotionSensitivitySelect, VibrationSensitivitySelect,
SmartSensitivitySecurityLevelSelect, SmartSensitivityComfortLevelSelect,
OrientationLightResponseSelect, InstallationProfileSelect), and the
siren/dimmer selects (SirenSoundLevelSelect, DimmerPhaseControlSelect).

Also exercises `async_setup_entry` wiring (device_excluded skipping, guard
combinations, AttributeError/unstable-property resilience) and the
async_select_option write-failure -> HomeAssistantError translation.

Pure-unit style throughout: entities are built via `__new__` bypass or the
real constructor plus SimpleNamespace/MagicMock device doubles; no HA test
harness is used (matches `-p no:homeassistant` in CI).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from boschshcpy.exceptions import SHCException
from boschshcpy.services_impl import (
    PirSensorConfigurationService,
    PowerSwitchConfigurationService,
    VibrationSensorService,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory

from custom_components.bosch_shc.const import OPT_EXCLUDED_DEVICES
from custom_components.bosch_shc.select import (
    ActuatorTypeSelect,
    DimmerPhaseControlSelect,
    DisplayDirectionSelect,
    DisplayedTemperatureSelect,
    HeaterTypeSelect,
    InstallationProfileSelect,
    MotionSensitivitySelect,
    OrientationLightResponseSelect,
    OutputModeSelect,
    SirenSoundLevelSelect,
    SmartSensitivityComfortLevelSelect,
    SmartSensitivitySecurityLevelSelect,
    SmokeSensitivitySelect,
    StateAfterPowerOutageSelect,
    SwitchTypeSelect,
    TerminalTypeSelect,
    ValveTypeSelect,
    VibrationSensitivitySelect,
    _MOTION_SENSITIVITY_OPTIONS,
    _VIBRATION_SENSITIVITY_OPTIONS,
    async_setup_entry,
)

# ---------------------------------------------------------------------------
# Shared helpers
#
# These consolidate near-identical helper functions that used to be defined
# once per source file. Where two source files had functionally-equivalent
# but textually-different versions (e.g. differing only in which of the
# session's device_helper lists were pre-populated), the superset/most
# general version was kept and all call sites verified compatible. Where two
# source files had genuinely different behaviour under the same name
# (e.g. test_select_coverage.py's and test_select_unit.py's own
# `_make_fake_sc2plus`), the more general implementation (configurable
# `name`) was kept since it is a strict superset of the other's behaviour for
# every call site actually used (all pass dev_id/root_id as keywords).
# ---------------------------------------------------------------------------


def _new(cls):
    return cls.__new__(cls)


def _run(coro):
    return asyncio.run(coro)


def _make_hass():
    return SimpleNamespace()


def _fake_device(**kwargs):
    defaults = dict(name="Dev", id="dev1", root_device_id="root1", serial="SER1")
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _fake_md2(**kwargs):
    defaults = dict(
        name="MD2", id="md1", root_device_id="root1", serial="SER1",
        supports_silentmode=False, supports_batterylevel=False,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _device_raising(**kwargs):
    """Create a device whose given property(ies) raise AttributeError."""

    class _RaisingDev:
        root_device_id = "r"
        id = "d"
        name = "X"

    for k, v in kwargs.items():
        setattr(
            _RaisingDev,
            k,
            property(lambda self, _k=k, _v=v: (_ for _ in ()).throw(AttributeError(_k))),
        )

    return _RaisingDev()


def _excl(*ids):
    return {OPT_EXCLUDED_DEVICES: list(ids)}


def _make_session(**helper_lists):
    """Session double exposing every device_helper list select.py's
    async_setup_entry reads from (all empty by default; override per-test).
    """
    defaults = dict(
        motion_detectors2=[],
        shutter_contacts2=[],
        smart_plugs=[],
        smart_plugs_compact=[],
        smoke_detectors=[],
        twinguards=[],
        thermostats=[],
        roomthermostats=[],
        micromodule_relays=[],
        micromodule_light_controls=[],
    )
    defaults.update(helper_lists)
    device_helper = SimpleNamespace(**defaults)
    return SimpleNamespace(device_helper=device_helper)


def _make_excluded_session(device_list_name, device):
    return _make_session(**{device_list_name: [device]})


def _make_hass_and_entry(session, options=None):
    entry_id = "E1"
    hass = SimpleNamespace()
    config_entry = SimpleNamespace(
        options=options or {},
        entry_id=entry_id,
        unique_id="UID1",
        async_on_unload=MagicMock(),
    )
    config_entry.runtime_data = SimpleNamespace(session=session)
    return hass, config_entry


async def _async_setup(session, options=None):
    hass, config_entry = _make_hass_and_entry(session, options)
    entities = []

    def add_entities(new_ents, *args, **kwargs):
        entities.extend(new_ents)

    with patch(
        "custom_components.bosch_shc.select.SHCShutterContact2Plus",
        new=type("SHCShutterContact2Plus", (), {}),
    ):
        await async_setup_entry(hass, config_entry, add_entities)
    return entities


def _setup(session, options=None):
    return asyncio.run(_async_setup(session, options))


def _run_setup_with_exclusion(session, excluded_id):
    return _setup(session, _excl(excluded_id))


def _types(entities):
    return [type(e).__name__ for e in entities]


_entity_types = _types


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


def _fake_hass(entry_id="E1", session=None, shc=None, options=None):
    """Minimal hass. session/shc are cached so a paired _fake_entry(hass=...)
    call can wire them onto entry.runtime_data (the modern storage location —
    this integration no longer uses hass.data[DOMAIN])."""
    shc_obj = shc or SimpleNamespace(
        identifiers={("bosch_shc", "shc")},
        name="SHC", manufacturer="Bosch", model="SHC", id="shc1",
    )
    h = MagicMock()
    h.data = {}
    h._fake_session = session
    h._fake_shc = shc_obj

    async def _executor_job(fn, *args):
        return fn(*args)

    h.async_add_executor_job = _executor_job
    h.config_entries = MagicMock()
    h.bus = MagicMock()
    h.bus.async_listen_once = MagicMock(return_value=MagicMock())
    h.async_create_task = MagicMock()
    return h


def _fake_entry(entry_id="E1", title="Test SHC", options=None, hass=None):
    """Build a fake config entry with runtime_data wired from `hass` (as
    produced by _fake_hass) when provided."""
    entry = MagicMock()
    entry.entry_id = entry_id
    entry.title = title
    entry.options = options or {}
    entry.unique_id = "uid1"
    entry.async_on_unload = MagicMock()
    entry.runtime_data = SimpleNamespace(
        session=getattr(hass, "_fake_session", None) if hass is not None else None,
        shc_device=getattr(hass, "_fake_shc", None) if hass is not None else None,
        title=title,
    )
    return entry


def _phase_svc(mode_name="TRAILING"):
    from boschshcpy.services_impl import DimmerConfigurationService

    mode = DimmerConfigurationService.EdgePhaseControlMode(mode_name)
    return SimpleNamespace(
        edge_phase_control_mode=mode,
        EdgePhaseControlMode=DimmerConfigurationService.EdgePhaseControlMode,
        async_set_edge_phase_control_mode=AsyncMock(),
    )


# --------------------------- #120 select -----------------------------------


def test_siren_sound_level_current_option():
    sel = _new(SirenSoundLevelSelect)
    sel._device = SimpleNamespace(
        siren=SimpleNamespace(sound_level=SimpleNamespace(name="HIGH"))
    )
    assert sel.current_option == "high"
    assert sel.current_option in sel._attr_options


# ===========================================================================
# SELECT.PY — 353-357, 363-366, 386-387, 394-395, 399-403, 462-466,
#              940-941, 954-955, 961
# ===========================================================================

class TestSelectSirenSoundLevelSetup:
    """Lines 353-357: SirenSoundLevelSelect setup in async_setup_entry."""

    def _run_select_setup(self, sirens, options=None):
        from custom_components.bosch_shc.select import async_setup_entry

        dh = MagicMock()
        dh.thermostats = []
        dh.roomthermostats = []
        dh.wallthermostats = []
        dh.motion_detectors2 = []
        dh.shutter_contacts2 = []
        dh.outdoor_sirens = sirens
        dh.micromodule_dimmers = []

        session = MagicMock()
        session.device_helper = dh

        hass = _fake_hass(session=session)
        entry = _fake_entry(hass=hass, options=options or {})

        # select.py does NOT import async_migrate_to_new_unique_id — no patch needed
        collected = []
        _run(async_setup_entry(hass, entry, lambda ents, **kw: collected.extend(ents)))
        return collected

    def test_siren_with_siren_service_adds_select(self):
        """Lines 353-359: siren with siren service → SirenSoundLevelSelect added."""
        from custom_components.bosch_shc.select import SirenSoundLevelSelect

        siren = _fake_dev("s1", siren=MagicMock())
        collected = self._run_select_setup([siren])
        assert any(isinstance(e, SirenSoundLevelSelect) for e in collected)

    def test_siren_without_siren_service_skipped(self):
        """Line 355-356: siren without siren service → skipped."""
        from custom_components.bosch_shc.select import SirenSoundLevelSelect

        siren = _fake_dev("s1", siren=None)
        collected = self._run_select_setup([siren])
        assert not any(isinstance(e, SirenSoundLevelSelect) for e in collected)

    def test_siren_excluded_skipped(self):
        """Line 353-354: device_excluded → continue."""
        from custom_components.bosch_shc.select import SirenSoundLevelSelect

        siren = _fake_dev("s1", siren=MagicMock())
        collected = self._run_select_setup(
            [siren], options={OPT_EXCLUDED_DEVICES: ["s1"]}
        )
        assert not any(isinstance(e, SirenSoundLevelSelect) for e in collected)


class TestSirenSoundLevelSelectInit:
    """Lines 386-387: SirenSoundLevelSelect.__init__."""

    def test_siren_sound_level_select_init(self):
        """Lines 384-387: real __init__ sets unique_id."""
        from custom_components.bosch_shc.select import SirenSoundLevelSelect

        dev = _fake_dev("s1")
        sel = SirenSoundLevelSelect(dev, "entry1")
        assert sel._attr_unique_id == "root1_s1_sound_level"


class TestSirenSoundLevelSelectCurrentOption:
    """Lines 394-395: SirenSoundLevelSelect.current_option."""

    def test_current_option_valid(self):
        """Lines 392-395: normal path returns lowercased sound level."""
        from custom_components.bosch_shc.select import SirenSoundLevelSelect

        sel = SirenSoundLevelSelect.__new__(SirenSoundLevelSelect)
        sel._device = SimpleNamespace(
            siren=SimpleNamespace(sound_level=SimpleNamespace(name="HIGH"))
        )
        assert sel.current_option == "high"

    def test_current_option_attribute_error(self):
        """current_option returns None on AttributeError."""
        from custom_components.bosch_shc.select import SirenSoundLevelSelect

        sel = SirenSoundLevelSelect.__new__(SirenSoundLevelSelect)
        sel._device = SimpleNamespace(siren=None)
        assert sel.current_option is None


class TestSirenSoundLevelSelectAsyncSelect:
    """Lines 399-403: SirenSoundLevelSelect.async_select_option."""

    def test_async_select_invalid_option_keyerror(self):
        """Lines 401-402: invalid option raises KeyError → return early."""
        from custom_components.bosch_shc.select import SirenSoundLevelSelect

        sel = SirenSoundLevelSelect.__new__(SirenSoundLevelSelect)
        sel._device = MagicMock()
        # "INVALID" not in SoundLevel enum → KeyError → returns without setting
        _run(sel.async_select_option("INVALID_LEVEL"))
        sel._device.siren.async_set_configuration.assert_not_called()

    def test_async_select_valid_option(self):
        """Lines 399-403: valid option → async_set_configuration called."""

        from custom_components.bosch_shc.select import SirenSoundLevelSelect

        sel = SirenSoundLevelSelect.__new__(SirenSoundLevelSelect)
        siren = MagicMock()
        siren.async_set_configuration = AsyncMock()
        sel._device = SimpleNamespace(siren=siren)
        _run(sel.async_select_option("high"))
        siren.async_set_configuration.assert_called_once()

# ===========================================================================
# MotionSensitivitySelect / VibrationSensitivitySelect — targeted coverage
# ===========================================================================

def _make_entry(options=None, entry_id="E1"):
    return SimpleNamespace(options=options or {}, entry_id=entry_id)


def _run_setup(session, entry):
    hass = _make_hass()
    entry.runtime_data = SimpleNamespace(session=session)
    collected = []

    def add(entities):
        collected.extend(entities)

    asyncio.run(async_setup_entry(hass, entry, add))
    return collected


def _good_md2_device(dev_id="md2-001"):
    """Motion detector 2 that successfully exposes motion_sensitivity."""
    return SimpleNamespace(
        name="MD2",
        id=dev_id,
        root_device_id="root-md2",
        motion_sensitivity=PirSensorConfigurationService.MotionSensitivity.HIGH,
    )


# ---------------------------------------------------------------------------
# Line 58 — device without motion_sensitivity attr is skipped
# ---------------------------------------------------------------------------

class TestMotionDetectorNoAttr:
    def test_device_without_attr_skipped(self):
        """Device in motion_detectors2 with no motion_sensitivity → skipped (line 58)."""
        dev = SimpleNamespace(
            name="MD2 no-pir",
            id="md2-no-attr",
            root_device_id="root-no-attr",
            # no motion_sensitivity attribute at all
        )
        session = _make_session(motion_detectors2=[dev])
        result = _run_setup(session, _make_entry())
        assert result == []

    def test_device_with_attr_added(self):
        """Sanity: device WITH motion_sensitivity attr → MotionSensitivitySelect created."""
        dev = _good_md2_device()
        session = _make_session(motion_detectors2=[dev])
        result = _run_setup(session, _make_entry())
        assert len(result) == 1
        assert isinstance(result[0], MotionSensitivitySelect)

    def test_mixed_one_without_attr_one_with(self):
        """Only device with the attr produces an entity; the other is silently skipped."""
        no_attr = SimpleNamespace(
            name="MD2-no", id="md2-no", root_device_id="root-no"
        )
        with_attr = _good_md2_device(dev_id="md2-ok")
        session = _make_session(motion_detectors2=[no_attr, with_attr])
        result = _run_setup(session, _make_entry())
        assert len(result) == 1
        assert result[0]._device is with_attr


# ---------------------------------------------------------------------------
# Lines 64-65 — device has motion_sensitivity attr but accessing it raises
#               AttributeError → continue
# ---------------------------------------------------------------------------

class TestMotionDetectorAttrRaisesAttributeError:
    def test_attr_raises_attribute_error_skipped(self):
        """Hasattr passes but accessing device.motion_sensitivity raises AttributeError
        (e.g. the getter calls an internal service that is absent).
        The device must be silently skipped (lines 64-65).
        """
        class _BadAttrDevice:
            name = "MD2 bad-getter"
            id = "md2-bad"
            root_device_id = "root-bad"

            @property
            def motion_sensitivity(self):
                raise AttributeError("PirSensor service not registered")

        dev = _BadAttrDevice()
        session = _make_session(motion_detectors2=[dev])
        result = _run_setup(session, _make_entry())
        assert result == []

    def test_attr_raises_good_device_still_added(self):
        """When one device raises AttributeError and another is fine, only the good
        device produces an entity.
        """
        class _BadAttrDevice:
            name = "MD2-bad"
            id = "md2-bad2"
            root_device_id = "root-bad2"

            @property
            def motion_sensitivity(self):
                raise AttributeError("service absent")

        good = _good_md2_device(dev_id="md2-good2")
        session = _make_session(motion_detectors2=[_BadAttrDevice(), good])
        result = _run_setup(session, _make_entry())
        assert len(result) == 1
        assert isinstance(result[0], MotionSensitivitySelect)

    def test_attr_error_device_does_not_raise(self):
        """Accessing a broken motion_sensitivity must not propagate the exception."""
        class _Breaks:
            name = "MD2-break"
            id = "md2-break"
            root_device_id = "root-break"

            @property
            def motion_sensitivity(self):
                raise AttributeError("internal fail")

        session = _make_session(motion_detectors2=[_Breaks()])
        # Should complete without raising
        result = _run_setup(session, _make_entry())
        assert result == []

# ===========================================================================
# MotionSensitivitySelect / VibrationSensitivitySelect — unit tests + setup
# ===========================================================================

def _make_config_entry(session):
    entry = SimpleNamespace(options={}, entry_id="E1")
    entry.runtime_data = SimpleNamespace(session=session)
    return entry


def _ms_device(sensitivity_name="HIGH", **kwargs):
    """Minimal SHCMotionDetector2-like device for MotionSensitivitySelect."""
    defaults = dict(
        name="Motion Detector II",
        id="hdm:ZigBee:md2-001",
        root_device_id="64-da-a0-xx-xx-xx",
        motion_sensitivity=PirSensorConfigurationService.MotionSensitivity[
            sensitivity_name
        ],
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_motion_select(sensitivity_name="HIGH"):
    dev = _ms_device(sensitivity_name=sensitivity_name)
    sel = MotionSensitivitySelect.__new__(MotionSensitivitySelect)
    sel._device = dev
    sel._attr_name = "Motion Sensitivity"
    sel._attr_unique_id = f"{dev.root_device_id}_{dev.id}_motion_sensitivity"
    return sel

# ---------------------------------------------------------------------------
# MotionSensitivitySelect — class-level attributes
# ---------------------------------------------------------------------------

class TestMotionSensitivitySelectClassAttrs:
    def test_entity_category_is_config(self):
        sel = _make_motion_select()
        assert sel.entity_category == EntityCategory.CONFIG

    def test_options_exclude_unknown(self):
        assert "UNKNOWN" not in _MOTION_SENSITIVITY_OPTIONS

    def test_options_contain_high_middle_low(self):
        assert set(_MOTION_SENSITIVITY_OPTIONS) == {"HIGH", "MIDDLE", "LOW"}

    def test_unique_id_format(self):
        sel = _make_motion_select()
        assert "_motion_sensitivity" in sel._attr_unique_id

    def test_attr_name(self):
        sel = _make_motion_select()
        assert sel._attr_name == "Motion Sensitivity"


# ---------------------------------------------------------------------------
# MotionSensitivitySelect — current_option
# ---------------------------------------------------------------------------

class TestMotionSensitivitySelectCurrentOption:
    def test_returns_high(self):
        sel = _make_motion_select("HIGH")
        assert sel.current_option == "HIGH"

    def test_returns_middle(self):
        sel = _make_motion_select("MIDDLE")
        assert sel.current_option == "MIDDLE"

    def test_returns_low(self):
        sel = _make_motion_select("LOW")
        assert sel.current_option == "LOW"

    def test_attribute_error_returns_none_with_warning(self):
        """When motion_sensitivity raises AttributeError, return None and warn."""

        class _Dev:
            name = "broken"

            @property
            def motion_sensitivity(self):
                raise AttributeError("service not available")

        sel = MotionSensitivitySelect.__new__(MotionSensitivitySelect)
        sel._device = _Dev()
        with patch("custom_components.bosch_shc.select.LOGGER") as mock_log:
            result = sel.current_option
        assert result is None
        mock_log.warning.assert_called_once()

    def test_value_error_returns_none_with_warning(self):
        """When motion_sensitivity.name raises ValueError, return None and warn."""
        class _BadSensitivity:
            @property
            def name(self):
                raise ValueError("bad value")

        class _Dev:
            name = "broken"
            motion_sensitivity = _BadSensitivity()

        sel = MotionSensitivitySelect.__new__(MotionSensitivitySelect)
        sel._device = _Dev()
        with patch("custom_components.bosch_shc.select.LOGGER") as mock_log:
            result = sel.current_option
        assert result is None
        mock_log.warning.assert_called_once()


# ---------------------------------------------------------------------------
# MotionSensitivitySelect — async_select_option / _set_sensitivity
# ---------------------------------------------------------------------------

class TestMotionSensitivitySelectSetOption:
    def test_async_select_option_awaits_device_method(self):
        """async_select_option must await device.async_set_motion_sensitivity."""
        from unittest.mock import AsyncMock
        sel = _make_motion_select("HIGH")
        sel._device = SimpleNamespace(
            name="MD2",
            motion_sensitivity=PirSensorConfigurationService.MotionSensitivity.HIGH,
            async_set_motion_sensitivity=AsyncMock(),
        )
        asyncio.run(sel.async_select_option("LOW"))
        sel._device.async_set_motion_sensitivity.assert_awaited_once_with(
            PirSensorConfigurationService.MotionSensitivity.LOW
        )

    def test_async_select_option_passes_correct_enum_value(self):
        """async_select_option converts option string to enum before passing."""
        from unittest.mock import AsyncMock
        sel = _make_motion_select("HIGH")
        sel._device = SimpleNamespace(
            name="MD2",
            motion_sensitivity=PirSensorConfigurationService.MotionSensitivity.HIGH,
            async_set_motion_sensitivity=AsyncMock(),
        )
        asyncio.run(sel.async_select_option("LOW"))
        called_with = sel._device.async_set_motion_sensitivity.call_args[0][0]
        assert called_with == PirSensorConfigurationService.MotionSensitivity.LOW


def test_motion_detectors2_excluded_device_skipped():
    dev = SimpleNamespace(name="MD2", id="md2_excl", root_device_id="r",
                          serial="S", motion_sensitivity=True)
    session = _make_excluded_session("motion_detectors2", dev)
    entities = _run_setup_with_exclusion(session, "md2_excl")
    types = [type(e).__name__ for e in entities]
    assert "MotionSensitivitySelect" not in types


# ===========================================================================
# select.py lines 64-65 — except AttributeError: continue
# (unstable property: hasattr passes, explicit access raises)
# ===========================================================================

class TestSelectMotionSensitivityUnstableProperty:
    """Cover select.py lines 64-65: the try/except AttributeError for
    motion_sensitivity when the property is unstable — succeeds on the first
    access (hasattr) but fails on the explicit probe at line 63.

    This requires a property whose first call returns a value (so hasattr
    returns True) but whose second call raises AttributeError.
    """

    def _run_setup(self, session):
        from custom_components.bosch_shc.select import async_setup_entry

        hass = SimpleNamespace(data={})
        config_entry = SimpleNamespace(
            entry_id="E1",
            options={},
            runtime_data=SimpleNamespace(session=session),
        )
        collected = []

        _run(async_setup_entry(hass, config_entry, lambda e: collected.extend(e)))
        return collected

    def test_unstable_motion_sensitivity_property_skipped(self):
        """Device where motion_sensitivity raises on second access is skipped
        (lines 64-65).
        """
        class _UnstableDevice:
            id = "md2-unstable"
            name = "MD2 Unstable"
            root_device_id = "root-unstable"

            def __init__(self):
                self._call_count = 0

            @property
            def motion_sensitivity(self):
                self._call_count += 1
                if self._call_count == 1:
                    # First call from hasattr() — succeeds
                    return "HIGH"
                # Second call from explicit probe `_ = device.motion_sensitivity`
                # — raises AttributeError to hit lines 64-65
                raise AttributeError("MotionSensitivityService vanished between calls")

        dev = _UnstableDevice()
        session = SimpleNamespace(
            device_helper=SimpleNamespace(
                motion_detectors2=[dev],
                shutter_contacts2=[],
            )
        )

        result = self._run_setup(session)

        # The device must be silently skipped (lines 64-65 executed)
        assert result == [], (
            "Device with unstable motion_sensitivity must be skipped (lines 64-65)"
        )

    def test_unstable_property_skips_but_good_device_still_added(self):
        """Unstable device is skipped; a stable device after it is still processed."""
        from boschshcpy.services_impl import PirSensorConfigurationService

        class _UnstableDevice:
            id = "md2-unstable2"
            name = "MD2 Unstable"
            root_device_id = "root-u2"

            def __init__(self):
                self._call_count = 0

            @property
            def motion_sensitivity(self):
                self._call_count += 1
                if self._call_count == 1:
                    return "HIGH"
                raise AttributeError("vanished")

        good_dev = SimpleNamespace(
            id="md2-good",
            name="MD2 Good",
            root_device_id="root-good",
            motion_sensitivity=PirSensorConfigurationService.MotionSensitivity.HIGH,
        )

        session = SimpleNamespace(
            device_helper=SimpleNamespace(
                motion_detectors2=[_UnstableDevice(), good_dev],
                shutter_contacts2=[],
            )
        )

        result = self._run_setup(session)

        # Only the good device produces an entity
        assert len(result) == 1
        assert result[0]._device is good_dev


    # Note: an additional test_cleanup_tracker_teardown_called_once() used to
    # live here. It only replicated the closure inline (its own docstring
    # admitted "even though this is a local replica... the REAL closure is
    # already tested by the integration test above") and provided no coverage
    # beyond test_cleanup_tracker_teardown_called_via_captured_closure() above.
    # Removed as redundant test weight.


# ---------------------------------------------------------------------------
# select.py line 58 — device_excluded continue for motion_detectors2
# ---------------------------------------------------------------------------

class TestSelectMotionDetector2ExcludedDevice:
    """select.py line 58: excluded device in motion_detectors2 is skipped."""

    def _run_setup(self, session, options=None):
        from custom_components.bosch_shc.select import async_setup_entry

        hass = SimpleNamespace()
        config_entry = SimpleNamespace(entry_id="E1", options=options or {})
        config_entry.runtime_data = SimpleNamespace(
            session=session, shc_device=None, title="Test SHC"
        )
        collected = []
        asyncio.run(async_setup_entry(hass, config_entry, lambda e: collected.extend(e)))
        return collected

    def test_excluded_motion_detector2_not_added(self):
        """An excluded motion_detector2 must be skipped at line 58 (continue)."""
        from boschshcpy.services_impl import PirSensorConfigurationService

        from custom_components.bosch_shc.const import OPT_EXCLUDED_DEVICES

        dev = SimpleNamespace(
            id="md2-excl",
            name="MD2 excluded",
            root_device_id="root-excl",
            motion_sensitivity=PirSensorConfigurationService.MotionSensitivity.HIGH,
        )
        session = SimpleNamespace(
            device_helper=SimpleNamespace(
                motion_detectors2=[dev],
                shutter_contacts2=[],
            )
        )
        result = self._run_setup(session, {OPT_EXCLUDED_DEVICES: ["md2-excl"]})
        assert result == []

    def test_excluded_device_and_non_excluded_device_in_same_list(self):
        """When one device is excluded and another is not, only the non-excluded
        one produces an entity. The excluded one hits line 58 (continue).
        """
        from boschshcpy.services_impl import PirSensorConfigurationService

        from custom_components.bosch_shc.const import OPT_EXCLUDED_DEVICES
        from custom_components.bosch_shc.select import MotionSensitivitySelect

        excluded = SimpleNamespace(
            id="md2-excl2",
            name="MD2 Excl",
            root_device_id="root-excl2",
            motion_sensitivity=PirSensorConfigurationService.MotionSensitivity.HIGH,
        )
        kept = SimpleNamespace(
            id="md2-kept",
            name="MD2 Kept",
            root_device_id="root-kept",
            motion_sensitivity=PirSensorConfigurationService.MotionSensitivity.MIDDLE,
        )
        session = SimpleNamespace(
            device_helper=SimpleNamespace(
                motion_detectors2=[excluded, kept],
                shutter_contacts2=[],
            )
        )
        result = self._run_setup(session, {OPT_EXCLUDED_DEVICES: ["md2-excl2"]})
        assert len(result) == 1
        assert isinstance(result[0], MotionSensitivitySelect)
        assert result[0]._device is kept


# ---------------------------------------------------------------------------
# select.py lines 64-65 — AttributeError from motion_sensitivity accessor
# ---------------------------------------------------------------------------
# This is already covered by test_select_coverage.py::TestMotionDetectorAttrRaisesAttributeError
# but that test file uses a different code path. Let's ensure we cover the
# exact branch with an OPT_EXCLUDED_DEVICES scenario that also exercises line 58
# first, then the attr-error path for a second device.

class TestSelectMotionSensitivityAttributeError:
    """select.py lines 64-65: AttributeError from motion_sensitivity accessor."""

    def _run_setup(self, session, options=None):
        from custom_components.bosch_shc.select import async_setup_entry

        hass = SimpleNamespace()
        config_entry = SimpleNamespace(entry_id="E1", options=options or {})
        config_entry.runtime_data = SimpleNamespace(
            session=session, shc_device=None, title="Test SHC"
        )
        collected = []
        asyncio.run(async_setup_entry(hass, config_entry, lambda e: collected.extend(e)))
        return collected

    def test_excluded_then_attr_error_both_skipped(self):
        """First device excluded (line 58 continue), second device raises
        AttributeError (lines 64-65 continue). Neither produces an entity.
        """
        from boschshcpy.services_impl import PirSensorConfigurationService

        from custom_components.bosch_shc.const import OPT_EXCLUDED_DEVICES

        excl = SimpleNamespace(
            id="md2-x",
            name="Excl",
            root_device_id="root-x",
            motion_sensitivity=PirSensorConfigurationService.MotionSensitivity.HIGH,
        )

        class _BadAttr:
            id = "md2-bad"
            name = "BadAttr"
            root_device_id = "root-bad"

            @property
            def motion_sensitivity(self):
                raise AttributeError("no service")

        session = SimpleNamespace(
            device_helper=SimpleNamespace(
                motion_detectors2=[excl, _BadAttr()],
                shutter_contacts2=[],
            )
        )
        result = self._run_setup(session, {OPT_EXCLUDED_DEVICES: ["md2-x"]})
        assert result == []

    def test_attr_error_device_skipped_does_not_raise(self):
        """AttributeError during probe (line 64) must not propagate — continue (65)."""
        class _Raises:
            id = "md2-raises"
            name = "Raises"
            root_device_id = "root-raises"

            @property
            def motion_sensitivity(self):
                raise AttributeError("PirSensor missing")

        session = SimpleNamespace(
            device_helper=SimpleNamespace(
                motion_detectors2=[_Raises()],
                shutter_contacts2=[],
            )
        )
        from custom_components.bosch_shc.select import async_setup_entry
        hass = SimpleNamespace()
        config_entry = SimpleNamespace(entry_id="E1", options={})
        config_entry.runtime_data = SimpleNamespace(
            session=session, shc_device=None, title="Test SHC"
        )
        collected = []
        # Must not raise AttributeError
        asyncio.run(async_setup_entry(hass, config_entry, lambda e: collected.extend(e)))
        assert collected == []


# ---------------------------------------------------------------------------
# select.py — line 58: device_excluded continue for motion_detectors2
# ---------------------------------------------------------------------------

class TestSelectMotionDetectorExcluded:
    """select.py line 58: device_excluded continue for motion_detectors2."""

    def _run_setup(self, session, options=None):
        from custom_components.bosch_shc.select import async_setup_entry

        hass = SimpleNamespace()
        config_entry = SimpleNamespace(entry_id="E1", options=options or {})
        config_entry.runtime_data = SimpleNamespace(session=session)
        collected = []
        asyncio.run(async_setup_entry(hass, config_entry, lambda e: collected.extend(e)))
        return collected

    def test_excluded_motion_detector2_not_in_entities(self):
        """Excluded motion_detector2 must be skipped (line 58 continue)."""
        from boschshcpy.services_impl import PirSensorConfigurationService

        from custom_components.bosch_shc.const import OPT_EXCLUDED_DEVICES

        dev = SimpleNamespace(
            id="md2-excl",
            name="MD2 excluded",
            root_device_id="root-excl",
            motion_sensitivity=PirSensorConfigurationService.MotionSensitivity.HIGH,
        )
        session = SimpleNamespace(
            device_helper=SimpleNamespace(
                motion_detectors2=[dev],
                shutter_contacts2=[],
            )
        )
        result = self._run_setup(session, {OPT_EXCLUDED_DEVICES: ["md2-excl"]})
        assert result == []

    def test_non_excluded_motion_detector2_with_sensitivity_attr_added(self):
        """Non-excluded device WITH motion_sensitivity attr produces entity."""
        from boschshcpy.services_impl import PirSensorConfigurationService

        from custom_components.bosch_shc.select import MotionSensitivitySelect

        dev = SimpleNamespace(
            id="md2-ok",
            name="MD2 OK",
            root_device_id="root-ok",
            motion_sensitivity=PirSensorConfigurationService.MotionSensitivity.HIGH,
        )
        session = SimpleNamespace(
            device_helper=SimpleNamespace(
                motion_detectors2=[dev],
                shutter_contacts2=[],
            )
        )
        result = self._run_setup(session)
        assert any(isinstance(e, MotionSensitivitySelect) for e in result)

    def test_motion_detector2_without_attr_skipped(self):
        """Device without motion_sensitivity attr is skipped (line 60)."""
        dev = SimpleNamespace(
            id="md2-no-attr",
            name="MD2",
            root_device_id="root",
            # No motion_sensitivity attribute
        )
        session = SimpleNamespace(
            device_helper=SimpleNamespace(
                motion_detectors2=[dev],
                shutter_contacts2=[],
            )
        )
        result = self._run_setup(session)
        assert result == []


# ===========================================================================
# 4. select.py:58 — device_excluded=True; lines 64-65 — AttributeError
# ===========================================================================

class TestSelectSetupExcludedAndAttributeError:
    """Tests for select.py async_setup_entry edge cases."""

    def _make_hass(self, devices):
        session = MagicMock()
        session.device_helper.motion_detectors2 = devices
        self._session = session
        hass = MagicMock()
        return hass

    def _make_entry(self, excluded_ids=None):
        entry = MagicMock()
        entry.entry_id = "eid1"
        entry.options = (
            {"excluded_devices": excluded_ids} if excluded_ids else {}
        )
        entry.runtime_data = SimpleNamespace(
            session=self._session, shc_device=None, title="Test SHC"
        )
        return entry

    def test_excluded_device_skipped(self):
        """Line 58: device_excluded returns True → device skipped."""
        device = MagicMock()
        device.id = "dev-to-exclude"
        device.name = "Motion2"

        hass = self._make_hass([device])
        entry = self._make_entry(excluded_ids=["dev-to-exclude"])

        added = []

        with patch(
            "custom_components.bosch_shc.select.device_excluded",
            return_value=True,
        ):
            asyncio.run(
                __import__(
                    "custom_components.bosch_shc.select",
                    fromlist=["async_setup_entry"],
                ).async_setup_entry(hass, entry, lambda e: added.extend(e))
            )

        assert added == [], "Excluded device must not add any entity"

    def test_motion_sensitivity_attribute_error_skips(self):
        """Lines 64-65: AttributeError on motion_sensitivity → device skipped."""
        # Create a class where motion_sensitivity property raises AttributeError
        class FakeDevice:
            id = "dev-no-svc"
            name = "Motion2"

            @property
            def motion_sensitivity(self):
                raise AttributeError("MotionSensitivityService not present")

        device = FakeDevice()

        hass = self._make_hass([device])
        entry = self._make_entry()

        added = []

        with patch(
            "custom_components.bosch_shc.select.device_excluded",
            return_value=False,
        ):
            asyncio.run(
                __import__(
                    "custom_components.bosch_shc.select",
                    fromlist=["async_setup_entry"],
                ).async_setup_entry(hass, entry, lambda e: added.extend(e))
            )

        assert added == [], "Device with AttributeError on motion_sensitivity must not add entity"

    def test_device_without_motion_sensitivity_attr_skipped(self):
        """Line 59-60: hasattr check fails (no motion_sensitivity attr) → skip."""
        # Use a plain SimpleNamespace — no motion_sensitivity attr
        device = SimpleNamespace(id="dev-no-attr", name="OldMotion2")

        hass = self._make_hass([device])
        entry = self._make_entry()

        added = []

        with patch(
            "custom_components.bosch_shc.select.device_excluded",
            return_value=False,
        ):
            asyncio.run(
                __import__(
                    "custom_components.bosch_shc.select",
                    fromlist=["async_setup_entry"],
                ).async_setup_entry(hass, entry, lambda e: added.extend(e))
            )

        assert added == [], "Device without motion_sensitivity attr must not add entity"


class TestOrientationLightResponseCurrentOptionError:
    """Lines 462-466: OrientationLightResponseSelect.current_option exception."""

    def test_current_option_exception_logged(self):
        """Lines 462-466: AttributeError from missing long_poll_interval → None."""
        from custom_components.bosch_shc.select import OrientationLightResponseSelect

        sel = OrientationLightResponseSelect.__new__(OrientationLightResponseSelect)
        sel._attr_options = ["ORIENTATION", "RESPONSE"]
        # SimpleNamespace raises AttributeError for missing 'long_poll_interval'
        # → hits the except (AttributeError, ValueError) block at lines 462-466
        sel._device = SimpleNamespace(name="MD2")
        result = sel.current_option
        assert result is None


# ---------------------------------------------------------------------------
# OrientationLightResponseSelect (PollControl)
# ---------------------------------------------------------------------------


class TestOrientationLightResponseSelect:
    def _make(self, interval="LONG"):
        from boschshcpy.services_impl import PollControlService

        dev = _fake_md2(
            long_poll_interval=PollControlService.PollControlState[interval],
            async_set_long_poll_interval=AsyncMock(),
        )
        e = OrientationLightResponseSelect.__new__(OrientationLightResponseSelect)
        e._device = dev
        return e

    def test_current_option(self):
        assert self._make("SHORT").current_option == "SHORT"

    def test_current_option_unknown_not_in_options(self):
        e = self._make("UNKNOWN")
        assert e.current_option is None

    def test_async_select_option(self):
        from boschshcpy.services_impl import PollControlService

        e = self._make("LONG")
        asyncio.run(e.async_select_option("SHORT"))
        e._device.async_set_long_poll_interval.assert_called_once_with(
            PollControlService.PollControlState.SHORT
        )

    def test_options(self):
        e = OrientationLightResponseSelect.__new__(OrientationLightResponseSelect)
        assert e._attr_options == ["LONG", "SHORT"]

    def test_setup_created_when_interval_present(self):
        from boschshcpy.services_impl import PollControlService

        md2 = _fake_md2(long_poll_interval=PollControlService.PollControlState.LONG)
        types = [
            type(e).__name__
            for e in _setup(_make_session(motion_detectors2=[md2]))
        ]
        assert "OrientationLightResponseSelect" in types

    def test_setup_skipped_when_no_interval(self):
        md2 = _fake_md2()  # no long_poll_interval
        types = [
            type(e).__name__
            for e in _setup(_make_session(motion_detectors2=[md2]))
        ]
        assert "OrientationLightResponseSelect" not in types


# ---------------------------------------------------------------------------
# Line 75 — non-SHCShutterContact2Plus device in shutter_contacts2 is skipped
# ---------------------------------------------------------------------------

class TestShutterContact2NotPlus:
    def test_plain_sc2_skipped(self):
        """A device that is NOT an instance of SHCShutterContact2Plus → skipped (line 75)."""
        plain = SimpleNamespace(
            name="SC2 plain",
            id="sc2-plain",
            root_device_id="root-plain",
        )
        session = _make_session(shutter_contacts2=[plain])
        result = _run_setup(session, _make_entry())
        assert result == []

    def test_sc2plus_added(self):
        """A real SHCShutterContact2Plus subclass passes isinstance → entity added."""
        dev = _make_fake_sc2plus()
        session = _make_session(shutter_contacts2=[dev])
        result = _run_setup(session, _make_entry())
        assert len(result) == 1
        assert isinstance(result[0], VibrationSensitivitySelect)

    def test_mixed_plain_and_plus(self):
        """Plain SC2 is skipped; SC2Plus produces VibrationSensitivitySelect."""
        plain = SimpleNamespace(
            name="SC2", id="sc2-plain2", root_device_id="root-plain2"
        )
        plus = _make_fake_sc2plus(dev_id="sc2p-002", root_id="root-sc2p-2")
        session = _make_session(shutter_contacts2=[plain, plus])
        result = _run_setup(session, _make_entry())
        assert len(result) == 1
        assert isinstance(result[0], VibrationSensitivitySelect)

    def test_multiple_plain_all_skipped(self):
        plain1 = SimpleNamespace(name="SC2-1", id="sc2-1", root_device_id="r1")
        plain2 = SimpleNamespace(name="SC2-2", id="sc2-2", root_device_id="r2")
        session = _make_session(shutter_contacts2=[plain1, plain2])
        result = _run_setup(session, _make_entry())
        assert result == []

    def test_excluded_sc2plus_skipped_before_isinstance_check(self):
        """An excluded SC2Plus device is filtered out at line 74 (device_excluded)
        before reaching the isinstance check on line 75/76.
        """
        dev = _make_fake_sc2plus(dev_id="sc2p-excl")
        session = _make_session(shutter_contacts2=[dev])
        entry = _make_entry(options={OPT_EXCLUDED_DEVICES: ["sc2p-excl"]})
        result = _run_setup(session, entry)
        assert result == []


def _vs_device(sensitivity_name="HIGH", **kwargs):
    """Minimal SHCShutterContact2Plus-like device for VibrationSensitivitySelect."""
    defaults = dict(
        name="Shutter Contact 2 Plus",
        id="hdm:ZigBee:sc2p-001",
        root_device_id="64-da-a0-yy-yy-yy",
        sensitivity=VibrationSensorService.SensitivityState[sensitivity_name],
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_vibration_select(sensitivity_name="HIGH"):
    dev = _vs_device(sensitivity_name=sensitivity_name)
    sel = VibrationSensitivitySelect.__new__(VibrationSensitivitySelect)
    sel._device = dev
    sel._attr_name = "Vibration Sensitivity"
    sel._attr_unique_id = f"{dev.root_device_id}_{dev.id}_vibration_sensitivity"
    return sel


# ---------------------------------------------------------------------------
# VibrationSensitivitySelect — class-level attributes
# ---------------------------------------------------------------------------

class TestVibrationSensitivitySelectClassAttrs:
    def test_entity_category_is_config(self):
        sel = _make_vibration_select()
        assert sel.entity_category == EntityCategory.CONFIG

    def test_options_contain_all_five_levels(self):
        expected = {"VERY_HIGH", "HIGH", "MEDIUM", "LOW", "VERY_LOW"}
        assert set(_VIBRATION_SENSITIVITY_OPTIONS) == expected

    def test_unique_id_format(self):
        sel = _make_vibration_select()
        assert "_vibration_sensitivity" in sel._attr_unique_id

    def test_attr_name(self):
        sel = _make_vibration_select()
        assert sel._attr_name == "Vibration Sensitivity"


# ---------------------------------------------------------------------------
# VibrationSensitivitySelect — current_option
# ---------------------------------------------------------------------------

class TestVibrationSensitivitySelectCurrentOption:
    def test_returns_high(self):
        sel = _make_vibration_select("HIGH")
        assert sel.current_option == "HIGH"

    def test_returns_very_high(self):
        sel = _make_vibration_select("VERY_HIGH")
        assert sel.current_option == "VERY_HIGH"

    def test_returns_medium(self):
        sel = _make_vibration_select("MEDIUM")
        assert sel.current_option == "MEDIUM"

    def test_returns_low(self):
        sel = _make_vibration_select("LOW")
        assert sel.current_option == "LOW"

    def test_returns_very_low(self):
        sel = _make_vibration_select("VERY_LOW")
        assert sel.current_option == "VERY_LOW"

    def test_attribute_error_returns_none_with_warning(self):

        class _Dev:
            name = "broken"

            @property
            def sensitivity(self):
                raise AttributeError("service not available")

        sel = VibrationSensitivitySelect.__new__(VibrationSensitivitySelect)
        sel._device = _Dev()
        with patch("custom_components.bosch_shc.select.LOGGER") as mock_log:
            result = sel.current_option
        assert result is None
        mock_log.warning.assert_called_once()


# ---------------------------------------------------------------------------
# VibrationSensitivitySelect — async_select_option / _set_sensitivity
# ---------------------------------------------------------------------------

class TestVibrationSensitivitySelectSetOption:
    def test_async_select_option_awaits_device_method(self):
        """async_select_option must await device.async_set_sensitivity."""
        from unittest.mock import AsyncMock
        sel = _make_vibration_select("HIGH")
        sel._device = SimpleNamespace(
            name="SC2+",
            sensitivity=VibrationSensorService.SensitivityState.HIGH,
            async_set_sensitivity=AsyncMock(),
        )
        asyncio.run(sel.async_select_option("VERY_LOW"))
        sel._device.async_set_sensitivity.assert_awaited_once_with(
            VibrationSensorService.SensitivityState.VERY_LOW
        )

    def test_async_select_option_passes_correct_enum_value(self):
        """async_select_option converts option string to enum before passing."""
        from unittest.mock import AsyncMock
        sel = _make_vibration_select("HIGH")
        sel._device = SimpleNamespace(
            name="SC2+",
            sensitivity=VibrationSensorService.SensitivityState.HIGH,
            async_set_sensitivity=AsyncMock(),
        )
        asyncio.run(sel.async_select_option("MEDIUM"))
        called_with = sel._device.async_set_sensitivity.call_args[0][0]
        assert called_with == VibrationSensorService.SensitivityState.MEDIUM



# ===========================================================================
# Dual-guard tests: supports_* flag + value combinations
# ===========================================================================


# ---------------------------------------------------------------------------
# StateAfterPowerOutageSelect
# ---------------------------------------------------------------------------


class TestStateAfterPowerOutageSelectGuard:
    def test_supports_false_value_present_skipped(self):
        plug = _fake_device(state_after_power_outage=True,
                            supports_power_switch_configuration=False)
        entities = _setup(_make_session(smart_plugs=[plug]))
        assert "StateAfterPowerOutageSelect" not in _types(entities)

    def test_supports_true_value_none_skipped(self):
        plug = _fake_device(state_after_power_outage=None,
                            supports_power_switch_configuration=True)
        entities = _setup(_make_session(smart_plugs=[plug]))
        assert "StateAfterPowerOutageSelect" not in _types(entities)

    def test_both_present_created(self):
        plug = _fake_device(state_after_power_outage=True,
                            supports_power_switch_configuration=True)
        entities = _setup(_make_session(smart_plugs=[plug]))
        assert "StateAfterPowerOutageSelect" in _types(entities)

    def test_supports_false_compact_skipped(self):
        plug = _fake_device(state_after_power_outage=True,
                            supports_power_switch_configuration=False)
        entities = _setup(_make_session(smart_plugs_compact=[plug]))
        assert "StateAfterPowerOutageSelect" not in _types(entities)

    def test_value_none_compact_skipped(self):
        plug = _fake_device(state_after_power_outage=None,
                            supports_power_switch_configuration=True)
        entities = _setup(_make_session(smart_plugs_compact=[plug]))
        assert "StateAfterPowerOutageSelect" not in _types(entities)

# ===========================================================================
# APK new select entities: unit + async_setup_entry coverage
# ===========================================================================

# ---------------------------------------------------------------------------
# StateAfterPowerOutageSelect
# ---------------------------------------------------------------------------


class TestStateAfterPowerOutageSelect:
    def _make(self, state_after_power_outage_name="OFF"):
        from boschshcpy.services_impl import PowerSwitchConfigurationService
        val = PowerSwitchConfigurationService.StateAfterPowerOutage[
            state_after_power_outage_name
        ]
        dev = SimpleNamespace(root_device_id="root1", id="dev1",
                              name="Plug", state_after_power_outage=val)
        e = StateAfterPowerOutageSelect.__new__(StateAfterPowerOutageSelect)
        e._device = dev
        e._attr_unique_id = (
            f"{dev.root_device_id}_{dev.id}_state_after_power_outage"
        )
        e._attr_name = "State After Power Outage"
        return e

    def test_unique_id_format(self):
        e = self._make()
        assert e._attr_unique_id == "root1_dev1_state_after_power_outage"

    def test_current_option_off(self):
        e = self._make("OFF")
        assert e.current_option == "OFF"

    def test_current_option_on(self):
        e = self._make("ON")
        assert e.current_option == "ON"

    def test_current_option_last_state(self):
        e = self._make("LAST_STATE")
        assert e.current_option == "LAST_STATE"

    def test_current_option_unknown_returns_none(self):
        from boschshcpy.services_impl import PowerSwitchConfigurationService
        dev = SimpleNamespace(root_device_id="r", id="d", name="X",
                              state_after_power_outage=PowerSwitchConfigurationService.StateAfterPowerOutage.UNKNOWN)
        e = StateAfterPowerOutageSelect.__new__(StateAfterPowerOutageSelect)
        e._device = dev
        assert e.current_option is None

    def test_async_select_option_calls_device_method(self):
        from unittest.mock import AsyncMock

        from boschshcpy.services_impl import PowerSwitchConfigurationService
        dev = SimpleNamespace(
            root_device_id="r", id="d", name="X",
            state_after_power_outage=None,
            async_set_state_after_power_outage=AsyncMock(),
        )
        e = StateAfterPowerOutageSelect.__new__(StateAfterPowerOutageSelect)
        e._device = dev
        asyncio.run(e.async_select_option("ON"))
        dev.async_set_state_after_power_outage.assert_awaited_once_with(
            PowerSwitchConfigurationService.StateAfterPowerOutage.ON
        )

    def test_created_when_attr_present(self):
        plug = _fake_device(state_after_power_outage=True, supports_power_switch_configuration=True)
        session = _make_session(smart_plugs=[plug])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "StateAfterPowerOutageSelect" in types

    def test_skipped_when_attr_absent(self):
        plug = _fake_device()
        session = _make_session(smart_plugs=[plug])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "StateAfterPowerOutageSelect" not in types

    def test_created_for_smartplugcompact(self):
        plug = _fake_device(state_after_power_outage=True, supports_power_switch_configuration=True)
        session = _make_session(smart_plugs_compact=[plug])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "StateAfterPowerOutageSelect" in types

# ===========================================================================
# PLUG_COMPACT: state_after_power_outage absent-from-state-dict regression
# ===========================================================================

# ---------------------------------------------------------------------------
# Core guard tests
# ---------------------------------------------------------------------------


class TestPlugCompactAbsentStateAfterPowerOutage:
    """Entity must NOT be created when state_after_power_outage is None."""

    def test_plug_compact_absent_field_skips_entity(self):
        """Simulate PLUG_COMPACT: service present but stateAfterPowerOutage key absent."""
        plug = _fake_device(
            supports_power_switch_configuration=True,
            state_after_power_outage=None,  # getter now returns None when key absent
        )
        entities = _setup(_make_session(smart_plugs_compact=[plug]))
        assert "StateAfterPowerOutageSelect" not in _entity_types(entities)

    def test_regular_plug_absent_field_skips_entity(self):
        plug = _fake_device(
            supports_power_switch_configuration=True,
            state_after_power_outage=None,
        )
        entities = _setup(_make_session(smart_plugs=[plug]))
        assert "StateAfterPowerOutageSelect" not in _entity_types(entities)

    def test_plug_compact_with_valid_value_creates_entity(self):
        plug = _fake_device(
            supports_power_switch_configuration=True,
            state_after_power_outage=PowerSwitchConfigurationService.StateAfterPowerOutage.OFF,
        )
        entities = _setup(_make_session(smart_plugs_compact=[plug]))
        assert "StateAfterPowerOutageSelect" in _entity_types(entities)


# ---------------------------------------------------------------------------
# current_option behaviour when value is UNKNOWN vs None
# ---------------------------------------------------------------------------


class TestStateAfterPowerOutageCurrentOption:
    """current_option returns None both for None value and UNKNOWN enum."""

    def _make_entity(self, value):
        dev = SimpleNamespace(
            root_device_id="root1", id="dev1", name="Plug",
            state_after_power_outage=value,
        )
        e = StateAfterPowerOutageSelect.__new__(StateAfterPowerOutageSelect)
        e._device = dev
        e._attr_options = ["OFF", "ON", "LAST_STATE"]
        return e

    def test_current_option_none_value_returns_none(self):
        """Absent key (value=None) → current_option is None."""
        e = self._make_entity(None)
        assert e.current_option is None

    def test_current_option_unknown_enum_returns_none(self):
        """UNKNOWN enum (present key, unrecognized value) → current_option is None."""
        e = self._make_entity(
            PowerSwitchConfigurationService.StateAfterPowerOutage.UNKNOWN
        )
        assert e.current_option is None

    def test_current_option_off_returns_off(self):
        e = self._make_entity(
            PowerSwitchConfigurationService.StateAfterPowerOutage.OFF
        )
        assert e.current_option == "OFF"

    def test_current_option_on_returns_on(self):
        e = self._make_entity(
            PowerSwitchConfigurationService.StateAfterPowerOutage.ON
        )
        assert e.current_option == "ON"

    def test_current_option_last_state_returns_last_state(self):
        e = self._make_entity(
            PowerSwitchConfigurationService.StateAfterPowerOutage.LAST_STATE
        )
        assert e.current_option == "LAST_STATE"


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


# ---------------------------------------------------------------------------
# StateAfterPowerOutageSelect — async + error path
# ---------------------------------------------------------------------------


class TestStateAfterPowerOutageSelectAsync:
    def _make(self, state_after_power_outage_name="OFF"):
        from boschshcpy.services_impl import PowerSwitchConfigurationService
        val = PowerSwitchConfigurationService.StateAfterPowerOutage[
            state_after_power_outage_name
        ]
        dev = SimpleNamespace(
            root_device_id="r", id="d", name="X",
            state_after_power_outage=val,
            async_set_state_after_power_outage=AsyncMock(),
        )
        e = StateAfterPowerOutageSelect.__new__(StateAfterPowerOutageSelect)
        e._device = dev
        e._attr_unique_id = "r_d_state_after_power_outage"
        return e

    def test_async_select_option_calls_device_method(self):
        e = self._make()
        asyncio.run(e.async_select_option("ON"))
        e._device.async_set_state_after_power_outage.assert_awaited_once()

    def test_async_select_option_sets_value(self):
        from boschshcpy.services_impl import PowerSwitchConfigurationService
        e = self._make()
        asyncio.run(e.async_select_option("LAST_STATE"))
        e._device.async_set_state_after_power_outage.assert_awaited_once_with(
            PowerSwitchConfigurationService.StateAfterPowerOutage.LAST_STATE
        )

    def test_current_option_attribute_error_returns_none(self):
        dev = _device_raising(state_after_power_outage="raises")
        e = StateAfterPowerOutageSelect.__new__(StateAfterPowerOutageSelect)
        e._device = dev
        assert e.current_option is None


def test_smart_plugs_excluded_skips_state_after_power_outage():
    dev = SimpleNamespace(name="Plug", id="plug_excl", root_device_id="r",
                          serial="S", state_after_power_outage=True)
    session = _make_excluded_session("smart_plugs", dev)
    entities = _run_setup_with_exclusion(session, "plug_excl")
    types = [type(e).__name__ for e in entities]
    assert "StateAfterPowerOutageSelect" not in types


# ---------------------------------------------------------------------------
# SmokeSensitivitySelect
# ---------------------------------------------------------------------------


class TestSmokeSensitivitySelect:
    def _make(self, level_name="HIGH"):
        from boschshcpy.services_impl import SmokeSensitivityService
        val = SmokeSensitivityService.SmokeSensitivityLevel[level_name]
        dev = SimpleNamespace(root_device_id="root1", id="dev1",
                              name="Smoke", smoke_sensitivity=val)
        e = SmokeSensitivitySelect.__new__(SmokeSensitivitySelect)
        e._device = dev
        e._attr_unique_id = f"{dev.root_device_id}_{dev.id}_smoke_sensitivity"
        return e

    def test_unique_id_format(self):
        e = self._make()
        assert e._attr_unique_id == "root1_dev1_smoke_sensitivity"

    def test_current_option_high(self):
        e = self._make("HIGH")
        assert e.current_option == "HIGH"

    def test_current_option_middle(self):
        e = self._make("MIDDLE")
        assert e.current_option == "MIDDLE"

    def test_current_option_low(self):
        e = self._make("LOW")
        assert e.current_option == "LOW"

    def test_current_option_unknown_returns_none(self):
        from boschshcpy.services_impl import SmokeSensitivityService
        dev = SimpleNamespace(root_device_id="r", id="d", name="X",
                              smoke_sensitivity=SmokeSensitivityService.SmokeSensitivityLevel.UNKNOWN)
        e = SmokeSensitivitySelect.__new__(SmokeSensitivitySelect)
        e._device = dev
        assert e.current_option is None

    def test_current_option_none_returns_none(self):
        dev = SimpleNamespace(root_device_id="r", id="d", name="X",
                              smoke_sensitivity=None)
        e = SmokeSensitivitySelect.__new__(SmokeSensitivitySelect)
        e._device = dev
        assert e.current_option is None

    def test_async_select_option_calls_device_method(self):
        from unittest.mock import AsyncMock

        from boschshcpy.services_impl import SmokeSensitivityService
        dev = SimpleNamespace(
            root_device_id="r", id="d", name="X",
            smoke_sensitivity=None,
            async_set_smoke_sensitivity=AsyncMock(),
        )
        e = SmokeSensitivitySelect.__new__(SmokeSensitivitySelect)
        e._device = dev
        asyncio.run(e.async_select_option("MIDDLE"))
        dev.async_set_smoke_sensitivity.assert_awaited_once_with(
            SmokeSensitivityService.SmokeSensitivityLevel.MIDDLE
        )

    def test_created_for_smoke_detector_when_attr_present(self):
        sd = _fake_device(smoke_sensitivity=True, supports_smoke_sensitivity=True)
        session = _make_session(smoke_detectors=[sd])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "SmokeSensitivitySelect" in types

    def test_skipped_when_attr_absent(self):
        sd = _fake_device()
        session = _make_session(smoke_detectors=[sd])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "SmokeSensitivitySelect" not in types

    def test_skipped_when_service_present_but_field_absent(self):
        """Service registered but state dict has no smokeSensitivity key → None → skip."""
        sd = _fake_device(supports_smoke_sensitivity=True, smoke_sensitivity=None)
        session = _make_session(smoke_detectors=[sd])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "SmokeSensitivitySelect" not in types

    def test_created_for_twinguard(self):
        tg = _fake_device(smoke_sensitivity=True, supports_smoke_sensitivity=True)
        session = _make_session(twinguards=[tg])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "SmokeSensitivitySelect" in types

    def test_smoke_sensitivity_probe_raises_attribute_error_skips(self):
        """When accessing device.smoke_sensitivity raises AttributeError, skip entity."""
        class BadDev:
            root_device_id = "r"
            id = "d"
            name = "X"
            serial = "S"
            supports_silentmode = False
            supports_smoke_sensitivity = True

            @property
            def smoke_sensitivity(self):
                raise AttributeError("no service")

        dev = BadDev()
        session = _make_session(smoke_detectors=[dev])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "SmokeSensitivitySelect" not in types

# ===========================================================================
# Remaining coverage-gap tests pulled from shared multi-platform files
# ===========================================================================




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

        session = _make_session(smoke_detectors=[device])

        with patch("builtins.hasattr", _patched_hasattr):
            entities = _setup(session)

        # No SmokeSensitivitySelect entity should be created
        from custom_components.bosch_shc.select import SmokeSensitivitySelect
        assert not any(isinstance(e, SmokeSensitivitySelect) for e in entities)


# ---------------------------------------------------------------------------
# SmokeSensitivitySelect — async + error path
# ---------------------------------------------------------------------------


class TestSmokeSensitivitySelectAsync:
    def _make(self):
        from boschshcpy.services_impl import SmokeSensitivityService
        val = SmokeSensitivityService.SmokeSensitivityLevel.HIGH
        dev = SimpleNamespace(
            root_device_id="r", id="d", name="X",
            smoke_sensitivity=val,
            async_set_smoke_sensitivity=AsyncMock(),
        )
        e = SmokeSensitivitySelect.__new__(SmokeSensitivitySelect)
        e._device = dev
        e._attr_unique_id = "r_d_smoke_sensitivity"
        return e

    def test_async_select_option_calls_device_method(self):
        e = self._make()
        asyncio.run(e.async_select_option("MIDDLE"))
        e._device.async_set_smoke_sensitivity.assert_awaited_once()

    def test_async_select_option_sets_value(self):
        from boschshcpy.services_impl import SmokeSensitivityService
        e = self._make()
        asyncio.run(e.async_select_option("LOW"))
        e._device.async_set_smoke_sensitivity.assert_awaited_once_with(
            SmokeSensitivityService.SmokeSensitivityLevel.LOW
        )

    def test_current_option_attribute_error_returns_none(self):
        dev = _device_raising(smoke_sensitivity="raises")
        e = SmokeSensitivitySelect.__new__(SmokeSensitivitySelect)
        e._device = dev
        assert e.current_option is None


def test_smoke_detector_excluded_skips_smoke_sensitivity():
    dev = SimpleNamespace(name="SD", id="sd_excl", root_device_id="r",
                          serial="S", smoke_sensitivity=True)
    session = _make_excluded_session("smoke_detectors", dev)
    entities = _run_setup_with_exclusion(session, "sd_excl")
    types = [type(e).__name__ for e in entities]
    assert "SmokeSensitivitySelect" not in types


# ---------------------------------------------------------------------------
# DisplayDirectionSelect
# ---------------------------------------------------------------------------


class TestDisplayDirectionSelectGuard:
    def test_supports_false_value_present_skipped(self):
        dev = _fake_device(display_direction=True,
                           supports_display_direction=False)
        entities = _setup(_make_session(thermostats=[dev]))
        assert "DisplayDirectionSelect" not in _types(entities)

    def test_supports_true_value_none_skipped(self):
        dev = _fake_device(display_direction=None,
                           supports_display_direction=True)
        entities = _setup(_make_session(thermostats=[dev]))
        assert "DisplayDirectionSelect" not in _types(entities)

    def test_both_present_created(self):
        dev = _fake_device(display_direction=True,
                           supports_display_direction=True)
        entities = _setup(_make_session(thermostats=[dev]))
        assert "DisplayDirectionSelect" in _types(entities)

    def test_roomthermostat_value_none_skipped(self):
        dev = _fake_device(display_direction=None,
                           supports_display_direction=True)
        entities = _setup(_make_session(roomthermostats=[dev]))
        assert "DisplayDirectionSelect" not in _types(entities)


# ---------------------------------------------------------------------------
# DisplayDirectionSelect
# ---------------------------------------------------------------------------


class TestDisplayDirectionSelect:
    def _make(self, direction_name="NORMAL"):
        from boschshcpy.services_impl import DisplayDirection
        val = DisplayDirection.Direction[direction_name]
        dev = SimpleNamespace(root_device_id="root1", id="dev1",
                              name="Therm", display_direction=val)
        e = DisplayDirectionSelect.__new__(DisplayDirectionSelect)
        e._device = dev
        e._attr_unique_id = f"{dev.root_device_id}_{dev.id}_display_direction"
        return e

    def test_unique_id(self):
        e = self._make()
        assert e._attr_unique_id == "root1_dev1_display_direction"

    def test_current_option_normal(self):
        e = self._make("NORMAL")
        assert e.current_option == "NORMAL"

    def test_current_option_reversed(self):
        e = self._make("REVERSED")
        assert e.current_option == "REVERSED"

    def test_current_option_unknown_returns_none(self):
        from boschshcpy.services_impl import DisplayDirection
        dev = SimpleNamespace(root_device_id="r", id="d", name="X",
                              display_direction=DisplayDirection.Direction.UNKNOWN)
        e = DisplayDirectionSelect.__new__(DisplayDirectionSelect)
        e._device = dev
        assert e.current_option is None

    def test_current_option_none_returns_none(self):
        dev = SimpleNamespace(root_device_id="r", id="d", name="X",
                              display_direction=None)
        e = DisplayDirectionSelect.__new__(DisplayDirectionSelect)
        e._device = dev
        assert e.current_option is None

    def test_async_select_option_calls_device_method(self):
        from unittest.mock import AsyncMock

        from boschshcpy.services_impl import DisplayDirection
        dev = SimpleNamespace(
            root_device_id="r", id="d", name="X",
            display_direction=None,
            async_set_display_direction=AsyncMock(),
        )
        e = DisplayDirectionSelect.__new__(DisplayDirectionSelect)
        e._device = dev
        asyncio.run(e.async_select_option("REVERSED"))
        dev.async_set_display_direction.assert_awaited_once_with(
            DisplayDirection.Direction.REVERSED
        )

    def test_created_for_thermostat(self):
        dev = _fake_device(display_direction=True, supports_display_direction=True)
        session = _make_session(thermostats=[dev])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "DisplayDirectionSelect" in types

    def test_skipped_when_attr_absent(self):
        dev = _fake_device()
        session = _make_session(thermostats=[dev])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "DisplayDirectionSelect" not in types

    def test_created_for_roomthermostat(self):
        dev = _fake_device(display_direction=True, supports_display_direction=True)
        session = _make_session(roomthermostats=[dev])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "DisplayDirectionSelect" in types


# ---------------------------------------------------------------------------
# DisplayDirectionSelect — async + error path
# ---------------------------------------------------------------------------


class TestDisplayDirectionSelectAsync:
    def _make(self):
        from boschshcpy.services_impl import DisplayDirection
        val = DisplayDirection.Direction.NORMAL
        dev = SimpleNamespace(
            root_device_id="r", id="d", name="X",
            display_direction=val,
            async_set_display_direction=AsyncMock(),
        )
        e = DisplayDirectionSelect.__new__(DisplayDirectionSelect)
        e._device = dev
        e._attr_unique_id = "r_d_display_direction"
        return e

    def test_async_select_option_calls_device_method(self):
        e = self._make()
        asyncio.run(e.async_select_option("REVERSED"))
        e._device.async_set_display_direction.assert_awaited_once()

    def test_async_select_option_sets_value(self):
        from boschshcpy.services_impl import DisplayDirection
        e = self._make()
        asyncio.run(e.async_select_option("REVERSED"))
        e._device.async_set_display_direction.assert_awaited_once_with(
            DisplayDirection.Direction.REVERSED
        )

    def test_current_option_attribute_error_returns_none(self):
        dev = _device_raising(display_direction="raises")
        e = DisplayDirectionSelect.__new__(DisplayDirectionSelect)
        e._device = dev
        assert e.current_option is None


def test_thermostat_excluded_skips_display_direction():
    dev = SimpleNamespace(name="TH", id="th_excl", root_device_id="r",
                          serial="S", display_direction=True)
    session = _make_excluded_session("thermostats", dev)
    entities = _run_setup_with_exclusion(session, "th_excl")
    types = [type(e).__name__ for e in entities]
    assert "DisplayDirectionSelect" not in types


# ---------------------------------------------------------------------------
# DisplayedTemperatureSelect
# ---------------------------------------------------------------------------


class TestDisplayedTemperatureSelectGuard:
    def test_supports_false_value_present_skipped(self):
        dev = _fake_device(displayed_temperature=True,
                           supports_displayed_temperature=False)
        entities = _setup(_make_session(thermostats=[dev]))
        assert "DisplayedTemperatureSelect" not in _types(entities)

    def test_supports_true_value_none_skipped(self):
        dev = _fake_device(displayed_temperature=None,
                           supports_displayed_temperature=True)
        entities = _setup(_make_session(thermostats=[dev]))
        assert "DisplayedTemperatureSelect" not in _types(entities)

    def test_both_present_created(self):
        dev = _fake_device(displayed_temperature=True,
                           supports_displayed_temperature=True)
        entities = _setup(_make_session(thermostats=[dev]))
        assert "DisplayedTemperatureSelect" in _types(entities)


# ---------------------------------------------------------------------------
# DisplayedTemperatureSelect
# ---------------------------------------------------------------------------


class TestDisplayedTemperatureSelect:
    def _make(self, option_name="SETPOINT"):
        from boschshcpy.services_impl import DisplayedTemperatureConfiguration
        val = DisplayedTemperatureConfiguration.DisplayedTemperature[option_name]
        dev = SimpleNamespace(root_device_id="root1", id="dev1",
                              name="Therm", displayed_temperature=val)
        e = DisplayedTemperatureSelect.__new__(DisplayedTemperatureSelect)
        e._device = dev
        e._attr_unique_id = f"{dev.root_device_id}_{dev.id}_displayed_temperature"
        return e

    def test_unique_id(self):
        e = self._make()
        assert e._attr_unique_id == "root1_dev1_displayed_temperature"

    def test_current_option_setpoint(self):
        e = self._make("SETPOINT")
        assert e.current_option == "SETPOINT"

    def test_current_option_measured(self):
        e = self._make("MEASURED")
        assert e.current_option == "MEASURED"

    def test_current_option_unknown_returns_none(self):
        from boschshcpy.services_impl import DisplayedTemperatureConfiguration
        dev = SimpleNamespace(root_device_id="r", id="d", name="X",
                              displayed_temperature=DisplayedTemperatureConfiguration.DisplayedTemperature.UNKNOWN)
        e = DisplayedTemperatureSelect.__new__(DisplayedTemperatureSelect)
        e._device = dev
        assert e.current_option is None

    def test_current_option_none_returns_none(self):
        dev = SimpleNamespace(root_device_id="r", id="d", name="X",
                              displayed_temperature=None)
        e = DisplayedTemperatureSelect.__new__(DisplayedTemperatureSelect)
        e._device = dev
        assert e.current_option is None

    def test_async_select_option_calls_device_method(self):
        from unittest.mock import AsyncMock

        from boschshcpy.services_impl import DisplayedTemperatureConfiguration
        dev = SimpleNamespace(
            root_device_id="r", id="d", name="X",
            displayed_temperature=None,
            async_set_displayed_temperature=AsyncMock(),
        )
        e = DisplayedTemperatureSelect.__new__(DisplayedTemperatureSelect)
        e._device = dev
        asyncio.run(e.async_select_option("MEASURED"))
        dev.async_set_displayed_temperature.assert_awaited_once_with(
            DisplayedTemperatureConfiguration.DisplayedTemperature.MEASURED
        )

    def test_created_for_thermostat(self):
        dev = _fake_device(displayed_temperature=True, supports_displayed_temperature=True)
        session = _make_session(thermostats=[dev])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "DisplayedTemperatureSelect" in types

    def test_skipped_when_attr_absent(self):
        dev = _fake_device()
        session = _make_session(thermostats=[dev])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "DisplayedTemperatureSelect" not in types

    def test_created_for_roomthermostat(self):
        dev = _fake_device(displayed_temperature=True, supports_displayed_temperature=True)
        session = _make_session(roomthermostats=[dev])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "DisplayedTemperatureSelect" in types


# ---------------------------------------------------------------------------
# DisplayedTemperatureSelect — async + error path
# ---------------------------------------------------------------------------


class TestDisplayedTemperatureSelectAsync:
    def _make(self):
        from boschshcpy.services_impl import DisplayedTemperatureConfiguration
        val = DisplayedTemperatureConfiguration.DisplayedTemperature.SETPOINT
        dev = SimpleNamespace(
            root_device_id="r", id="d", name="X",
            displayed_temperature=val,
            async_set_displayed_temperature=AsyncMock(),
        )
        e = DisplayedTemperatureSelect.__new__(DisplayedTemperatureSelect)
        e._device = dev
        e._attr_unique_id = "r_d_displayed_temperature"
        return e

    def test_async_select_option_calls_device_method(self):
        e = self._make()
        asyncio.run(e.async_select_option("MEASURED"))
        e._device.async_set_displayed_temperature.assert_awaited_once()

    def test_async_select_option_sets_value(self):
        from boschshcpy.services_impl import DisplayedTemperatureConfiguration
        e = self._make()
        asyncio.run(e.async_select_option("MEASURED"))
        e._device.async_set_displayed_temperature.assert_awaited_once_with(
            DisplayedTemperatureConfiguration.DisplayedTemperature.MEASURED
        )

    def test_current_option_attribute_error_returns_none(self):
        dev = _device_raising(displayed_temperature="raises")
        e = DisplayedTemperatureSelect.__new__(DisplayedTemperatureSelect)
        e._device = dev
        assert e.current_option is None


# ---------------------------------------------------------------------------
# TerminalTypeSelect
# ---------------------------------------------------------------------------


class TestTerminalTypeSelectGuard:
    def test_supports_false_value_present_skipped(self):
        dev = _fake_device(terminal_type=True,
                           supports_terminal_configuration=False)
        entities = _setup(_make_session(thermostats=[dev]))
        assert "TerminalTypeSelect" not in _types(entities)

    def test_supports_true_value_none_skipped(self):
        dev = _fake_device(terminal_type=None,
                           supports_terminal_configuration=True)
        entities = _setup(_make_session(thermostats=[dev]))
        assert "TerminalTypeSelect" not in _types(entities)

    def test_both_present_created(self):
        dev = _fake_device(terminal_type=True,
                           supports_terminal_configuration=True)
        entities = _setup(_make_session(thermostats=[dev]))
        assert "TerminalTypeSelect" in _types(entities)

    def test_roomthermostat_value_none_skipped(self):
        dev = _fake_device(terminal_type=None,
                           supports_terminal_configuration=True)
        entities = _setup(_make_session(roomthermostats=[dev]))
        assert "TerminalTypeSelect" not in _types(entities)


# ---------------------------------------------------------------------------
# TerminalTypeSelect
# ---------------------------------------------------------------------------


class TestTerminalTypeSelect:
    def _make(self, option_name="NOT_CONNECTED"):
        from boschshcpy.services_impl import TerminalConfiguration
        val = TerminalConfiguration.Type[option_name]
        dev = SimpleNamespace(root_device_id="root1", id="dev1",
                              name="RTH", terminal_type=val)
        e = TerminalTypeSelect.__new__(TerminalTypeSelect)
        e._device = dev
        e._attr_unique_id = f"{dev.root_device_id}_{dev.id}_terminal_type"
        return e

    def test_unique_id(self):
        e = self._make()
        assert e._attr_unique_id == "root1_dev1_terminal_type"

    def test_current_option_not_connected(self):
        e = self._make("NOT_CONNECTED")
        assert e.current_option == "NOT_CONNECTED"

    def test_current_option_floor_sensor(self):
        e = self._make("FLOOR_SENSOR_CONNECTED")
        assert e.current_option == "FLOOR_SENSOR_CONNECTED"

    def test_current_option_unknown_returns_none(self):
        from boschshcpy.services_impl import TerminalConfiguration
        dev = SimpleNamespace(root_device_id="r", id="d", name="X",
                              terminal_type=TerminalConfiguration.Type.UNKNOWN)
        e = TerminalTypeSelect.__new__(TerminalTypeSelect)
        e._device = dev
        assert e.current_option is None

    def test_current_option_none_returns_none(self):
        dev = SimpleNamespace(root_device_id="r", id="d", name="X",
                              terminal_type=None)
        e = TerminalTypeSelect.__new__(TerminalTypeSelect)
        e._device = dev
        assert e.current_option is None

    def test_async_select_option_calls_device_method(self):
        from unittest.mock import AsyncMock

        from boschshcpy.services_impl import TerminalConfiguration
        dev = SimpleNamespace(
            root_device_id="r", id="d", name="X",
            terminal_type=None,
            async_set_terminal_type=AsyncMock(),
        )
        e = TerminalTypeSelect.__new__(TerminalTypeSelect)
        e._device = dev
        asyncio.run(e.async_select_option("FLOOR_SENSOR_CONNECTED"))
        dev.async_set_terminal_type.assert_awaited_once_with(
            TerminalConfiguration.Type.FLOOR_SENSOR_CONNECTED
        )

    def test_created_when_attr_present(self):
        dev = _fake_device(terminal_type=True, supports_terminal_configuration=True)
        session = _make_session(thermostats=[dev])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "TerminalTypeSelect" in types

    def test_skipped_when_attr_absent(self):
        dev = _fake_device()
        session = _make_session(thermostats=[dev])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "TerminalTypeSelect" not in types

    def test_created_for_roomthermostat(self):
        dev = _fake_device(terminal_type=True, supports_terminal_configuration=True)
        session = _make_session(roomthermostats=[dev])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "TerminalTypeSelect" in types


# ---------------------------------------------------------------------------
# TerminalTypeSelect — async + error path
# ---------------------------------------------------------------------------


class TestTerminalTypeSelectAsync:
    def _make(self):
        from boschshcpy.services_impl import TerminalConfiguration
        val = TerminalConfiguration.Type.NOT_CONNECTED
        dev = SimpleNamespace(
            root_device_id="r", id="d", name="X",
            terminal_type=val,
            async_set_terminal_type=AsyncMock(),
        )
        e = TerminalTypeSelect.__new__(TerminalTypeSelect)
        e._device = dev
        e._attr_unique_id = "r_d_terminal_type"
        return e

    def test_async_select_option_calls_device_method(self):
        e = self._make()
        asyncio.run(e.async_select_option("FLOOR_SENSOR_CONNECTED"))
        e._device.async_set_terminal_type.assert_awaited_once()

    def test_async_select_option_sets_value(self):
        from boschshcpy.services_impl import TerminalConfiguration
        e = self._make()
        asyncio.run(e.async_select_option("FLOOR_SENSOR_CONNECTED"))
        e._device.async_set_terminal_type.assert_awaited_once_with(
            TerminalConfiguration.Type.FLOOR_SENSOR_CONNECTED
        )

    def test_current_option_attribute_error_returns_none(self):
        dev = _device_raising(terminal_type="raises")
        e = TerminalTypeSelect.__new__(TerminalTypeSelect)
        e._device = dev
        assert e.current_option is None


# ---------------------------------------------------------------------------
# ValveTypeSelect
# ---------------------------------------------------------------------------


class TestValveTypeSelectGuard:
    def test_supports_false_value_present_skipped(self):
        dev = _fake_device(valve_type=True,
                           supports_wall_thermostat_configuration=False)
        entities = _setup(_make_session(thermostats=[dev]))
        assert "ValveTypeSelect" not in _types(entities)

    def test_supports_true_value_none_skipped(self):
        dev = _fake_device(valve_type=None,
                           supports_wall_thermostat_configuration=True)
        entities = _setup(_make_session(thermostats=[dev]))
        assert "ValveTypeSelect" not in _types(entities)

    def test_both_present_created(self):
        dev = _fake_device(valve_type=True,
                           supports_wall_thermostat_configuration=True)
        entities = _setup(_make_session(thermostats=[dev]))
        assert "ValveTypeSelect" in _types(entities)


# ---------------------------------------------------------------------------
# ValveTypeSelect
# ---------------------------------------------------------------------------


class TestValveTypeSelect:
    def _make(self, option_name="NORMALLY_CLOSE"):
        from boschshcpy.services_impl import WallThermostatConfiguration
        val = WallThermostatConfiguration.ValveType[option_name]
        dev = SimpleNamespace(root_device_id="root1", id="dev1",
                              name="TRV", valve_type=val)
        e = ValveTypeSelect.__new__(ValveTypeSelect)
        e._device = dev
        e._attr_unique_id = f"{dev.root_device_id}_{dev.id}_valve_type"
        return e

    def test_unique_id(self):
        e = self._make()
        assert e._attr_unique_id == "root1_dev1_valve_type"

    def test_current_option_normally_close(self):
        e = self._make("NORMALLY_CLOSE")
        assert e.current_option == "NORMALLY_CLOSE"

    def test_current_option_normally_open(self):
        e = self._make("NORMALLY_OPEN")
        assert e.current_option == "NORMALLY_OPEN"

    def test_current_option_unknown_returns_none(self):
        from boschshcpy.services_impl import WallThermostatConfiguration
        dev = SimpleNamespace(root_device_id="r", id="d", name="X",
                              valve_type=WallThermostatConfiguration.ValveType.UNKNOWN)
        e = ValveTypeSelect.__new__(ValveTypeSelect)
        e._device = dev
        assert e.current_option is None

    def test_current_option_none_returns_none(self):
        dev = SimpleNamespace(root_device_id="r", id="d", name="X",
                              valve_type=None)
        e = ValveTypeSelect.__new__(ValveTypeSelect)
        e._device = dev
        assert e.current_option is None

    def test_async_select_option_calls_device_method(self):
        from unittest.mock import AsyncMock

        from boschshcpy.services_impl import WallThermostatConfiguration
        dev = SimpleNamespace(
            root_device_id="r", id="d", name="X",
            valve_type=None,
            async_set_valve_type=AsyncMock(),
        )
        e = ValveTypeSelect.__new__(ValveTypeSelect)
        e._device = dev
        asyncio.run(e.async_select_option("NORMALLY_OPEN"))
        dev.async_set_valve_type.assert_awaited_once_with(
            WallThermostatConfiguration.ValveType.NORMALLY_OPEN
        )

    def test_created_when_attr_present(self):
        dev = _fake_device(valve_type=True, supports_wall_thermostat_configuration=True)
        session = _make_session(thermostats=[dev])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "ValveTypeSelect" in types

    def test_skipped_when_attr_absent(self):
        dev = _fake_device()
        session = _make_session(thermostats=[dev])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "ValveTypeSelect" not in types

    def test_created_for_roomthermostat(self):
        dev = _fake_device(valve_type=True, supports_wall_thermostat_configuration=True)
        session = _make_session(roomthermostats=[dev])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "ValveTypeSelect" in types


# ---------------------------------------------------------------------------
# ValveTypeSelect — async + error path
# ---------------------------------------------------------------------------


class TestValveTypeSelectAsync:
    def _make(self):
        from boschshcpy.services_impl import WallThermostatConfiguration
        val = WallThermostatConfiguration.ValveType.NORMALLY_CLOSE
        dev = SimpleNamespace(
            root_device_id="r", id="d", name="X",
            valve_type=val,
            async_set_valve_type=AsyncMock(),
        )
        e = ValveTypeSelect.__new__(ValveTypeSelect)
        e._device = dev
        e._attr_unique_id = "r_d_valve_type"
        return e

    def test_async_select_option_calls_device_method(self):
        e = self._make()
        asyncio.run(e.async_select_option("NORMALLY_OPEN"))
        e._device.async_set_valve_type.assert_awaited_once()

    def test_async_select_option_sets_value(self):
        from boschshcpy.services_impl import WallThermostatConfiguration
        e = self._make()
        asyncio.run(e.async_select_option("NORMALLY_OPEN"))
        e._device.async_set_valve_type.assert_awaited_once_with(
            WallThermostatConfiguration.ValveType.NORMALLY_OPEN
        )

    def test_current_option_attribute_error_returns_none(self):
        dev = _device_raising(valve_type="raises")
        e = ValveTypeSelect.__new__(ValveTypeSelect)
        e._device = dev
        assert e.current_option is None


# ---------------------------------------------------------------------------
# HeaterTypeSelect
# ---------------------------------------------------------------------------


class TestHeaterTypeSelectGuard:
    def test_supports_false_value_present_skipped(self):
        dev = _fake_device(heater_type=True,
                           supports_wall_thermostat_configuration=False)
        entities = _setup(_make_session(thermostats=[dev]))
        assert "HeaterTypeSelect" not in _types(entities)

    def test_supports_true_value_none_skipped(self):
        dev = _fake_device(heater_type=None,
                           supports_wall_thermostat_configuration=True)
        entities = _setup(_make_session(thermostats=[dev]))
        assert "HeaterTypeSelect" not in _types(entities)

    def test_both_present_created(self):
        dev = _fake_device(heater_type=True,
                           supports_wall_thermostat_configuration=True)
        entities = _setup(_make_session(thermostats=[dev]))
        assert "HeaterTypeSelect" in _types(entities)


# ---------------------------------------------------------------------------
# HeaterTypeSelect
# ---------------------------------------------------------------------------


class TestHeaterTypeSelect:
    def _make(self, option_name="RADIATOR"):
        from boschshcpy.services_impl import WallThermostatConfiguration
        val = WallThermostatConfiguration.HeaterType[option_name]
        dev = SimpleNamespace(root_device_id="root1", id="dev1",
                              name="TRV", heater_type=val)
        e = HeaterTypeSelect.__new__(HeaterTypeSelect)
        e._device = dev
        e._attr_unique_id = f"{dev.root_device_id}_{dev.id}_heater_type"
        return e

    def test_unique_id(self):
        e = self._make()
        assert e._attr_unique_id == "root1_dev1_heater_type"

    def test_current_option_radiator(self):
        e = self._make("RADIATOR")
        assert e.current_option == "RADIATOR"

    def test_current_option_floor_heating(self):
        e = self._make("FLOOR_HEATING")
        assert e.current_option == "FLOOR_HEATING"

    def test_current_option_unknown_returns_none(self):
        from boschshcpy.services_impl import WallThermostatConfiguration
        dev = SimpleNamespace(root_device_id="r", id="d", name="X",
                              heater_type=WallThermostatConfiguration.HeaterType.UNKNOWN)
        e = HeaterTypeSelect.__new__(HeaterTypeSelect)
        e._device = dev
        assert e.current_option is None

    def test_current_option_none_returns_none(self):
        dev = SimpleNamespace(root_device_id="r", id="d", name="X",
                              heater_type=None)
        e = HeaterTypeSelect.__new__(HeaterTypeSelect)
        e._device = dev
        assert e.current_option is None

    def test_async_select_option_calls_device_method(self):
        from unittest.mock import AsyncMock

        from boschshcpy.services_impl import WallThermostatConfiguration
        dev = SimpleNamespace(
            root_device_id="r", id="d", name="X",
            heater_type=None,
            async_set_heater_type=AsyncMock(),
        )
        e = HeaterTypeSelect.__new__(HeaterTypeSelect)
        e._device = dev
        asyncio.run(e.async_select_option("CONVECTOR_PASSIVE"))
        dev.async_set_heater_type.assert_awaited_once_with(
            WallThermostatConfiguration.HeaterType.CONVECTOR_PASSIVE
        )

    def test_created_when_attr_present(self):
        dev = _fake_device(heater_type=True, supports_wall_thermostat_configuration=True)
        session = _make_session(thermostats=[dev])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "HeaterTypeSelect" in types

    def test_skipped_when_attr_absent(self):
        dev = _fake_device()
        session = _make_session(thermostats=[dev])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "HeaterTypeSelect" not in types


# ---------------------------------------------------------------------------
# HeaterTypeSelect — async + error path
# ---------------------------------------------------------------------------


class TestHeaterTypeSelectAsync:
    def _make(self):
        from boschshcpy.services_impl import WallThermostatConfiguration
        val = WallThermostatConfiguration.HeaterType.RADIATOR
        dev = SimpleNamespace(
            root_device_id="r", id="d", name="X",
            heater_type=val,
            async_set_heater_type=AsyncMock(),
        )
        e = HeaterTypeSelect.__new__(HeaterTypeSelect)
        e._device = dev
        e._attr_unique_id = "r_d_heater_type"
        return e

    def test_async_select_option_calls_device_method(self):
        e = self._make()
        asyncio.run(e.async_select_option("FLOOR_HEATING"))
        e._device.async_set_heater_type.assert_awaited_once()

    def test_async_select_option_sets_value(self):
        from boschshcpy.services_impl import WallThermostatConfiguration
        e = self._make()
        asyncio.run(e.async_select_option("FLOOR_HEATING"))
        e._device.async_set_heater_type.assert_awaited_once_with(
            WallThermostatConfiguration.HeaterType.FLOOR_HEATING
        )

    def test_current_option_attribute_error_returns_none(self):
        dev = _device_raising(heater_type="raises")
        e = HeaterTypeSelect.__new__(HeaterTypeSelect)
        e._device = dev
        assert e.current_option is None


# ---------------------------------------------------------------------------
# SwitchTypeSelect
# ---------------------------------------------------------------------------


class TestSwitchTypeSelectGuard:
    def test_value_none_skipped(self):
        relay = _fake_device(switch_type=None)
        entities = _setup(_make_session(micromodule_relays=[relay]))
        assert "SwitchTypeSelect" not in _types(entities)

    def test_value_present_created(self):
        relay = _fake_device(switch_type=True)
        entities = _setup(_make_session(micromodule_relays=[relay]))
        assert "SwitchTypeSelect" in _types(entities)

    def test_light_control_value_none_skipped(self):
        lc = _fake_device(switch_type=None)
        entities = _setup(_make_session(micromodule_light_controls=[lc]))
        assert "SwitchTypeSelect" not in _types(entities)

    def test_light_control_value_present_created(self):
        """Regression test: LightControl has no supports_switch_configuration
        at all, so this must be gated on switch_type alone, not that flag."""
        lc = _fake_device(switch_type=True)
        entities = _setup(_make_session(micromodule_light_controls=[lc]))
        assert "SwitchTypeSelect" in _types(entities)


# ---------------------------------------------------------------------------
# SwitchTypeSelect
# ---------------------------------------------------------------------------


class TestSwitchTypeSelect:
    def _make(self, option_name="PUSHBUTTON"):
        from boschshcpy.services_impl import SwitchConfiguration
        val = SwitchConfiguration.SwitchType[option_name]
        dev = SimpleNamespace(root_device_id="root1", id="dev1",
                              name="Relay", switch_type=val)
        e = SwitchTypeSelect.__new__(SwitchTypeSelect)
        e._device = dev
        e._attr_unique_id = f"{dev.root_device_id}_{dev.id}_switch_type"
        return e

    def test_unique_id(self):
        e = self._make()
        assert e._attr_unique_id == "root1_dev1_switch_type"

    def test_current_option_pushbutton(self):
        e = self._make("PUSHBUTTON")
        assert e.current_option == "PUSHBUTTON"

    def test_current_option_switch(self):
        e = self._make("SWITCH")
        assert e.current_option == "SWITCH"

    def test_current_option_none_not_in_options_returns_none(self):
        from boschshcpy.services_impl import SwitchConfiguration
        dev = SimpleNamespace(root_device_id="r", id="d", name="X",
                              switch_type=SwitchConfiguration.SwitchType.UNKNOWN)
        e = SwitchTypeSelect.__new__(SwitchTypeSelect)
        e._device = dev
        assert e.current_option is None

    def test_current_option_none_value_returns_none(self):
        dev = SimpleNamespace(root_device_id="r", id="d", name="X",
                              switch_type=None)
        e = SwitchTypeSelect.__new__(SwitchTypeSelect)
        e._device = dev
        assert e.current_option is None

    def test_async_select_option_calls_device_method(self):
        from unittest.mock import AsyncMock

        from boschshcpy.services_impl import SwitchConfiguration
        dev = SimpleNamespace(
            root_device_id="r", id="d", name="X",
            switch_type=None,
            async_set_switch_type=AsyncMock(),
        )
        e = SwitchTypeSelect.__new__(SwitchTypeSelect)
        e._device = dev
        asyncio.run(e.async_select_option("NONE"))
        dev.async_set_switch_type.assert_awaited_once_with(
            SwitchConfiguration.SwitchType.NONE
        )

    def test_created_for_relay_when_attr_present(self):
        relay = _fake_device(switch_type=True, supports_switch_configuration=True)
        session = _make_session(micromodule_relays=[relay])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "SwitchTypeSelect" in types

    def test_skipped_when_attr_absent(self):
        relay = _fake_device()
        session = _make_session(micromodule_relays=[relay])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "SwitchTypeSelect" not in types

    def test_created_for_light_control(self):
        lc = _fake_device(switch_type=True, supports_switch_configuration=True)
        session = _make_session(micromodule_light_controls=[lc])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "SwitchTypeSelect" in types


# ---------------------------------------------------------------------------
# SwitchTypeSelect — async + error path
# ---------------------------------------------------------------------------


class TestSwitchTypeSelectAsync:
    def _make(self):
        from boschshcpy.services_impl import SwitchConfiguration
        val = SwitchConfiguration.SwitchType.PUSHBUTTON
        dev = SimpleNamespace(
            root_device_id="r", id="d", name="X",
            switch_type=val,
            async_set_switch_type=AsyncMock(),
        )
        e = SwitchTypeSelect.__new__(SwitchTypeSelect)
        e._device = dev
        e._attr_unique_id = "r_d_switch_type"
        return e

    def test_async_select_option_calls_device_method(self):
        e = self._make()
        asyncio.run(e.async_select_option("SWITCH"))
        e._device.async_set_switch_type.assert_awaited_once()

    def test_async_select_option_sets_value(self):
        from boschshcpy.services_impl import SwitchConfiguration
        e = self._make()
        asyncio.run(e.async_select_option("NONE"))
        e._device.async_set_switch_type.assert_awaited_once_with(
            SwitchConfiguration.SwitchType.NONE
        )

    def test_current_option_attribute_error_returns_none(self):
        dev = _device_raising(switch_type="raises")
        e = SwitchTypeSelect.__new__(SwitchTypeSelect)
        e._device = dev
        assert e.current_option is None


def test_relay_excluded_skips_switch_type():
    dev = SimpleNamespace(name="R", id="r_excl", root_device_id="r",
                          serial="S", switch_type=True)
    session = _make_excluded_session("micromodule_relays", dev)
    entities = _run_setup_with_exclusion(session, "r_excl")
    types = [type(e).__name__ for e in entities]
    assert "SwitchTypeSelect" not in types


# ---------------------------------------------------------------------------
# ActuatorTypeSelect
# ---------------------------------------------------------------------------


class TestActuatorTypeSelectGuard:
    def test_value_none_skipped(self):
        relay = _fake_device(actuator_type=None)
        entities = _setup(_make_session(micromodule_relays=[relay]))
        assert "ActuatorTypeSelect" not in _types(entities)

    def test_value_present_created(self):
        relay = _fake_device(actuator_type=True)
        entities = _setup(_make_session(micromodule_relays=[relay]))
        assert "ActuatorTypeSelect" in _types(entities)

    def test_light_control_value_none_skipped(self):
        lc = _fake_device(actuator_type=None)
        entities = _setup(_make_session(micromodule_light_controls=[lc]))
        assert "ActuatorTypeSelect" not in _types(entities)

    def test_light_control_value_present_created(self):
        """Regression test: LightControl has no supports_switch_configuration
        at all, so this must be gated on actuator_type alone, not that flag."""
        lc = _fake_device(actuator_type=True)
        entities = _setup(_make_session(micromodule_light_controls=[lc]))
        assert "ActuatorTypeSelect" in _types(entities)


# ---------------------------------------------------------------------------
# ActuatorTypeSelect
# ---------------------------------------------------------------------------


class TestActuatorTypeSelect:
    def _make(self, option_name="NORMALLY_OPEN"):
        from boschshcpy.services_impl import SwitchConfiguration
        val = SwitchConfiguration.ActuatorType[option_name]
        dev = SimpleNamespace(root_device_id="root1", id="dev1",
                              name="Relay", actuator_type=val)
        e = ActuatorTypeSelect.__new__(ActuatorTypeSelect)
        e._device = dev
        e._attr_unique_id = f"{dev.root_device_id}_{dev.id}_actuator_type"
        return e

    def test_unique_id(self):
        e = self._make()
        assert e._attr_unique_id == "root1_dev1_actuator_type"

    def test_current_option_normally_open(self):
        e = self._make("NORMALLY_OPEN")
        assert e.current_option == "NORMALLY_OPEN"

    def test_current_option_normally_closed(self):
        e = self._make("NORMALLY_CLOSED")
        assert e.current_option == "NORMALLY_CLOSED"

    def test_current_option_unknown_returns_none(self):
        from boschshcpy.services_impl import SwitchConfiguration
        dev = SimpleNamespace(root_device_id="r", id="d", name="X",
                              actuator_type=SwitchConfiguration.ActuatorType.UNKNOWN)
        e = ActuatorTypeSelect.__new__(ActuatorTypeSelect)
        e._device = dev
        assert e.current_option is None

    def test_current_option_none_returns_none(self):
        dev = SimpleNamespace(root_device_id="r", id="d", name="X",
                              actuator_type=None)
        e = ActuatorTypeSelect.__new__(ActuatorTypeSelect)
        e._device = dev
        assert e.current_option is None

    def test_async_select_option_calls_device_method(self):
        from unittest.mock import AsyncMock

        from boschshcpy.services_impl import SwitchConfiguration
        dev = SimpleNamespace(
            root_device_id="r", id="d", name="X",
            actuator_type=None,
            async_set_actuator_type=AsyncMock(),
        )
        e = ActuatorTypeSelect.__new__(ActuatorTypeSelect)
        e._device = dev
        asyncio.run(e.async_select_option("NORMALLY_CLOSED"))
        dev.async_set_actuator_type.assert_awaited_once_with(
            SwitchConfiguration.ActuatorType.NORMALLY_CLOSED
        )

    def test_created_for_relay(self):
        relay = _fake_device(actuator_type=True, supports_switch_configuration=True)
        session = _make_session(micromodule_relays=[relay])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "ActuatorTypeSelect" in types

    def test_skipped_when_attr_absent(self):
        relay = _fake_device()
        session = _make_session(micromodule_relays=[relay])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "ActuatorTypeSelect" not in types

    def test_created_for_light_control(self):
        lc = _fake_device(actuator_type=True, supports_switch_configuration=True)
        session = _make_session(micromodule_light_controls=[lc])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "ActuatorTypeSelect" in types


# ---------------------------------------------------------------------------
# ActuatorTypeSelect — async + error path
# ---------------------------------------------------------------------------


class TestActuatorTypeSelectAsync:
    def _make(self):
        from boschshcpy.services_impl import SwitchConfiguration
        val = SwitchConfiguration.ActuatorType.NORMALLY_OPEN
        dev = SimpleNamespace(
            root_device_id="r", id="d", name="X",
            actuator_type=val,
            async_set_actuator_type=AsyncMock(),
        )
        e = ActuatorTypeSelect.__new__(ActuatorTypeSelect)
        e._device = dev
        e._attr_unique_id = "r_d_actuator_type"
        return e

    def test_async_select_option_calls_device_method(self):
        e = self._make()
        asyncio.run(e.async_select_option("NORMALLY_CLOSED"))
        e._device.async_set_actuator_type.assert_awaited_once()

    def test_async_select_option_sets_value(self):
        from boschshcpy.services_impl import SwitchConfiguration
        e = self._make()
        asyncio.run(e.async_select_option("NORMALLY_CLOSED"))
        e._device.async_set_actuator_type.assert_awaited_once_with(
            SwitchConfiguration.ActuatorType.NORMALLY_CLOSED
        )

    def test_current_option_attribute_error_returns_none(self):
        dev = _device_raising(actuator_type="raises")
        e = ActuatorTypeSelect.__new__(ActuatorTypeSelect)
        e._device = dev
        assert e.current_option is None


# ---------------------------------------------------------------------------
# OutputModeSelect
# ---------------------------------------------------------------------------


class TestOutputModeSelectGuard:
    def test_value_none_skipped(self):
        relay = _fake_device(output_mode=None)
        entities = _setup(_make_session(micromodule_relays=[relay]))
        assert "OutputModeSelect" not in _types(entities)

    def test_value_present_created(self):
        relay = _fake_device(output_mode=True)
        entities = _setup(_make_session(micromodule_relays=[relay]))
        assert "OutputModeSelect" in _types(entities)

    def test_light_control_value_none_skipped(self):
        lc = _fake_device(output_mode=None)
        entities = _setup(_make_session(micromodule_light_controls=[lc]))
        assert "OutputModeSelect" not in _types(entities)

    def test_light_control_value_present_created(self):
        """Regression test: LightControl has no supports_switch_configuration
        at all, so this must be gated on output_mode alone, not that flag."""
        lc = _fake_device(output_mode=True)
        entities = _setup(_make_session(micromodule_light_controls=[lc]))
        assert "OutputModeSelect" in _types(entities)


# ---------------------------------------------------------------------------
# OutputModeSelect
# ---------------------------------------------------------------------------


class TestOutputModeSelect:
    def _make(self, option_name="ATTACHED"):
        from boschshcpy.services_impl import SwitchConfiguration
        val = SwitchConfiguration.OutputMode[option_name]
        dev = SimpleNamespace(root_device_id="root1", id="dev1",
                              name="Relay", output_mode=val)
        e = OutputModeSelect.__new__(OutputModeSelect)
        e._device = dev
        e._attr_unique_id = f"{dev.root_device_id}_{dev.id}_output_mode"
        return e

    def test_unique_id(self):
        e = self._make()
        assert e._attr_unique_id == "root1_dev1_output_mode"

    def test_current_option_attached(self):
        e = self._make("ATTACHED")
        assert e.current_option == "ATTACHED"

    def test_current_option_detached(self):
        e = self._make("DETACHED")
        assert e.current_option == "DETACHED"

    def test_current_option_unknown_returns_none(self):
        from boschshcpy.services_impl import SwitchConfiguration
        dev = SimpleNamespace(root_device_id="r", id="d", name="X",
                              output_mode=SwitchConfiguration.OutputMode.UNKNOWN)
        e = OutputModeSelect.__new__(OutputModeSelect)
        e._device = dev
        assert e.current_option is None

    def test_current_option_none_returns_none(self):
        dev = SimpleNamespace(root_device_id="r", id="d", name="X",
                              output_mode=None)
        e = OutputModeSelect.__new__(OutputModeSelect)
        e._device = dev
        assert e.current_option is None

    def test_async_select_option_calls_device_method(self):
        from unittest.mock import AsyncMock

        from boschshcpy.services_impl import SwitchConfiguration
        dev = SimpleNamespace(
            root_device_id="r", id="d", name="X",
            output_mode=None,
            async_set_output_mode=AsyncMock(),
        )
        e = OutputModeSelect.__new__(OutputModeSelect)
        e._device = dev
        asyncio.run(e.async_select_option("DETACHED_SHORT_PRESS"))
        dev.async_set_output_mode.assert_awaited_once_with(
            SwitchConfiguration.OutputMode.DETACHED_SHORT_PRESS
        )

    def test_created_for_relay(self):
        relay = _fake_device(output_mode=True, supports_switch_configuration=True)
        session = _make_session(micromodule_relays=[relay])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "OutputModeSelect" in types

    def test_skipped_when_attr_absent(self):
        relay = _fake_device()
        session = _make_session(micromodule_relays=[relay])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "OutputModeSelect" not in types

    def test_created_for_light_control(self):
        lc = _fake_device(output_mode=True, supports_switch_configuration=True)
        session = _make_session(micromodule_light_controls=[lc])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "OutputModeSelect" in types


# ---------------------------------------------------------------------------
# OutputModeSelect — async + error path
# ---------------------------------------------------------------------------


class TestOutputModeSelectAsync:
    def _make(self):
        from boschshcpy.services_impl import SwitchConfiguration
        val = SwitchConfiguration.OutputMode.ATTACHED
        dev = SimpleNamespace(
            root_device_id="r", id="d", name="X",
            output_mode=val,
            async_set_output_mode=AsyncMock(),
        )
        e = OutputModeSelect.__new__(OutputModeSelect)
        e._device = dev
        e._attr_unique_id = "r_d_output_mode"
        return e

    def test_async_select_option_calls_device_method(self):
        e = self._make()
        asyncio.run(e.async_select_option("DETACHED"))
        e._device.async_set_output_mode.assert_awaited_once()

    def test_async_select_option_sets_value(self):
        from boschshcpy.services_impl import SwitchConfiguration
        e = self._make()
        asyncio.run(e.async_select_option("DETACHED_SHORT_PRESS"))
        e._device.async_set_output_mode.assert_awaited_once_with(
            SwitchConfiguration.OutputMode.DETACHED_SHORT_PRESS
        )

    def test_current_option_attribute_error_returns_none(self):
        dev = _device_raising(output_mode="raises")
        e = OutputModeSelect.__new__(OutputModeSelect)
        e._device = dev
        assert e.current_option is None


class TestSelectMotionDetectors2DeviceExcluded:
    """select.py — device_excluded continue in motion_detectors2 loop."""

    def test_excluded_md2_not_added(self):
        md2 = _fake_device(id="md2-excl", get_smart_sensitivity=lambda ctx: {})
        session = _make_session(motion_detectors2=[md2])
        entities = _setup(session, _excl("md2-excl"))
        ids = [getattr(getattr(e, "_device", None), "id", None) for e in entities]
        assert "md2-excl" not in ids


# ---------------------------------------------------------------------------
# SmartSensitivitySecurityLevelSelect
# ---------------------------------------------------------------------------


class TestSmartSensitivitySecurityLevelSelect:
    def _make(self, manual_level="HIGH"):
        from boschshcpy.services_impl import SmartSensitivityControlService
        level_val = SmartSensitivityControlService.MotionSensitivity[manual_level]
        sensitivity_dict = {
            "context": "SECURITY",
            "automaticLevel": "HIGH",
            "manualLevel": level_val,
        }

        def _get_sensitivity(c):
            return sensitivity_dict

        dev = _fake_md2(get_smart_sensitivity=_get_sensitivity)
        e = SmartSensitivitySecurityLevelSelect.__new__(
            SmartSensitivitySecurityLevelSelect
        )
        e._device = dev
        e._attr_unique_id = (
            f"{dev.root_device_id}_{dev.id}_smart_sensitivity_security"
        )
        e._attr_name = "Security Sensitivity Level"
        return e

    def test_unique_id(self):
        e = self._make()
        assert e._attr_unique_id == "root1_md1_smart_sensitivity_security"

    def test_current_option_high(self):
        e = self._make("HIGH")
        assert e.current_option == "HIGH"

    def test_current_option_middle(self):
        e = self._make("MIDDLE")
        assert e.current_option == "MIDDLE"

    def test_current_option_low(self):
        e = self._make("LOW")
        assert e.current_option == "LOW"

    def test_current_option_none_when_get_returns_none(self):
        def _get_none(c):
            return None

        dev = _fake_md2(get_smart_sensitivity=_get_none)
        e = SmartSensitivitySecurityLevelSelect.__new__(
            SmartSensitivitySecurityLevelSelect
        )
        e._device = dev
        assert e.current_option is None

    def test_current_option_none_when_manual_level_absent(self):
        def _get_no_level(c):
            return {"context": "SECURITY"}  # no manualLevel key

        dev = _fake_md2(get_smart_sensitivity=_get_no_level)
        e = SmartSensitivitySecurityLevelSelect.__new__(
            SmartSensitivitySecurityLevelSelect
        )
        e._device = dev
        assert e.current_option is None

    def test_async_select_option_calls_device_setter(self):
        from boschshcpy.services_impl import SmartSensitivityControlService
        ctx = SmartSensitivityControlService.SmartSensitivityContext.SECURITY
        dev = _fake_md2(
            get_smart_sensitivity=lambda c: {"manualLevel": "HIGH"},
            async_set_smart_sensitivity_manual_level=AsyncMock(),
        )
        e = SmartSensitivitySecurityLevelSelect.__new__(
            SmartSensitivitySecurityLevelSelect
        )
        e._device = dev
        asyncio.run(e.async_select_option("MIDDLE"))
        dev.async_set_smart_sensitivity_manual_level.assert_called_once_with(
            ctx, SmartSensitivityControlService.MotionSensitivity.MIDDLE
        )

    def test_created_when_get_smart_sensitivity_present(self):
        md2 = _fake_md2(get_smart_sensitivity=lambda c: {"manualLevel": "HIGH"}, supports_smart_sensitivity=True)
        session = _make_session(motion_detectors2=[md2])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "SmartSensitivitySecurityLevelSelect" in types

    def test_skipped_when_get_smart_sensitivity_absent(self):
        md2 = _fake_md2()  # no get_smart_sensitivity attr
        session = _make_session(motion_detectors2=[md2])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "SmartSensitivitySecurityLevelSelect" not in types

    def test_entity_category_config(self):
        from homeassistant.helpers.entity import EntityCategory
        e = SmartSensitivitySecurityLevelSelect.__new__(
            SmartSensitivitySecurityLevelSelect
        )
        assert e._attr_entity_category == EntityCategory.CONFIG

    def test_options_list_contains_high_middle_low(self):
        e = SmartSensitivitySecurityLevelSelect.__new__(
            SmartSensitivitySecurityLevelSelect
        )
        assert "HIGH" in e._attr_options
        assert "MIDDLE" in e._attr_options
        assert "LOW" in e._attr_options

    def test_current_option_string_level(self):
        """Level may be a plain string (not an enum) — should still work."""
        def _get_str_level(c):
            return {"context": "SECURITY", "manualLevel": "LOW"}

        dev = _fake_md2(get_smart_sensitivity=_get_str_level)
        e = SmartSensitivitySecurityLevelSelect.__new__(
            SmartSensitivitySecurityLevelSelect
        )
        e._device = dev
        assert e.current_option == "LOW"

    def test_current_option_none_when_name_not_in_options(self):
        """Level has .name but it is not in HIGH/MIDDLE/LOW (e.g. UNKNOWN enum)."""
        from boschshcpy.services_impl import SmartSensitivityControlService
        unknown = SmartSensitivityControlService.MotionSensitivity.UNKNOWN

        def _get_unknown(c):
            return {"context": "SECURITY", "manualLevel": unknown}

        dev = _fake_md2(get_smart_sensitivity=_get_unknown)
        e = SmartSensitivitySecurityLevelSelect.__new__(
            SmartSensitivitySecurityLevelSelect
        )
        e._device = dev
        assert e.current_option is None


# ---------------------------------------------------------------------------
# SmartSensitivityComfortLevelSelect
# ---------------------------------------------------------------------------


class TestSmartSensitivityComfortLevelSelect:
    def _make(self, manual_level="MIDDLE"):
        from boschshcpy.services_impl import SmartSensitivityControlService
        level_val = SmartSensitivityControlService.MotionSensitivity[manual_level]

        def _get_sensitivity(c):
            return {
                "context": "COMFORT",
                "automaticLevel": "MIDDLE",
                "manualLevel": level_val,
            }

        dev = _fake_md2(get_smart_sensitivity=_get_sensitivity)
        e = SmartSensitivityComfortLevelSelect.__new__(
            SmartSensitivityComfortLevelSelect
        )
        e._device = dev
        e._attr_unique_id = (
            f"{dev.root_device_id}_{dev.id}_smart_sensitivity_comfort"
        )
        e._attr_name = "Comfort Sensitivity Level"
        return e

    def test_unique_id(self):
        e = self._make()
        assert e._attr_unique_id == "root1_md1_smart_sensitivity_comfort"

    def test_current_option_middle(self):
        e = self._make("MIDDLE")
        assert e.current_option == "MIDDLE"

    def test_current_option_high(self):
        e = self._make("HIGH")
        assert e.current_option == "HIGH"

    def test_current_option_low(self):
        e = self._make("LOW")
        assert e.current_option == "LOW"

    def test_current_option_none_when_get_returns_none(self):
        def _get_none(c):
            return None

        dev = _fake_md2(get_smart_sensitivity=_get_none)
        e = SmartSensitivityComfortLevelSelect.__new__(
            SmartSensitivityComfortLevelSelect
        )
        e._device = dev
        assert e.current_option is None

    def test_current_option_none_when_manual_level_absent(self):
        def _get_no_level(c):
            return {"context": "COMFORT"}  # no manualLevel key

        dev = _fake_md2(get_smart_sensitivity=_get_no_level)
        e = SmartSensitivityComfortLevelSelect.__new__(
            SmartSensitivityComfortLevelSelect
        )
        e._device = dev
        assert e.current_option is None

    def test_async_select_option_calls_device_setter(self):
        from boschshcpy.services_impl import SmartSensitivityControlService
        ctx = SmartSensitivityControlService.SmartSensitivityContext.COMFORT
        dev = _fake_md2(
            get_smart_sensitivity=lambda c: {"manualLevel": "MIDDLE"},
            async_set_smart_sensitivity_manual_level=AsyncMock(),
        )
        e = SmartSensitivityComfortLevelSelect.__new__(
            SmartSensitivityComfortLevelSelect
        )
        e._device = dev
        asyncio.run(e.async_select_option("HIGH"))
        dev.async_set_smart_sensitivity_manual_level.assert_called_once_with(
            ctx, SmartSensitivityControlService.MotionSensitivity.HIGH
        )

    def test_created_when_guard_present(self):
        md2 = _fake_md2(
            get_smart_sensitivity=lambda c: {"manualLevel": "MIDDLE"},
            supports_smart_sensitivity=True,
        )
        session = _make_session(motion_detectors2=[md2])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "SmartSensitivityComfortLevelSelect" in types

    def test_skipped_when_guard_absent(self):
        md2 = _fake_md2()  # no get_smart_sensitivity attr
        session = _make_session(motion_detectors2=[md2])
        entities = _setup(session)
        types = [type(e).__name__ for e in entities]
        assert "SmartSensitivityComfortLevelSelect" not in types

    def test_entity_category_config(self):
        from homeassistant.helpers.entity import EntityCategory
        e = SmartSensitivityComfortLevelSelect.__new__(
            SmartSensitivityComfortLevelSelect
        )
        assert e._attr_entity_category == EntityCategory.CONFIG

    def test_options_list_contains_high_middle_low(self):
        e = SmartSensitivityComfortLevelSelect.__new__(
            SmartSensitivityComfortLevelSelect
        )
        assert "HIGH" in e._attr_options
        assert "MIDDLE" in e._attr_options
        assert "LOW" in e._attr_options

    def test_current_option_string_level(self):
        """Level may be a plain string (not an enum) — should still work."""
        def _get_str_level(c):
            return {"context": "COMFORT", "manualLevel": "HIGH"}

        dev = _fake_md2(get_smart_sensitivity=_get_str_level)
        e = SmartSensitivityComfortLevelSelect.__new__(
            SmartSensitivityComfortLevelSelect
        )
        e._device = dev
        assert e.current_option == "HIGH"

    def test_current_option_none_when_name_not_in_options(self):
        """Level has .name but it is not in HIGH/MIDDLE/LOW (e.g. UNKNOWN enum)."""
        from boschshcpy.services_impl import SmartSensitivityControlService
        unknown = SmartSensitivityControlService.MotionSensitivity.UNKNOWN

        def _get_unknown(c):
            return {"context": "COMFORT", "manualLevel": unknown}

        dev = _fake_md2(get_smart_sensitivity=_get_unknown)
        e = SmartSensitivityComfortLevelSelect.__new__(
            SmartSensitivityComfortLevelSelect
        )
        e._device = dev
        assert e.current_option is None


class TestSelectDimmerPhaseControlSetup:
    """Lines 363-366: DimmerPhaseControlSelect setup."""

    def _run_select_setup(self, dimmers, options=None):
        from custom_components.bosch_shc.select import async_setup_entry

        dh = MagicMock()
        dh.thermostats = []
        dh.roomthermostats = []
        dh.wallthermostats = []
        dh.motion_detectors2 = []
        dh.shutter_contacts2 = []
        dh.outdoor_sirens = []
        dh.micromodule_dimmers = dimmers

        session = MagicMock()
        session.device_helper = dh

        hass = _fake_hass(session=session)
        entry = _fake_entry(hass=hass, options=options or {})

        # select.py does NOT import async_migrate_to_new_unique_id — no patch needed
        collected = []
        _run(async_setup_entry(hass, entry, lambda ents, **kw: collected.extend(ents)))
        return collected

    def test_dimmer_with_phase_control_adds_select(self):
        """Lines 363-368: supports_dimmer_configuration=True → DimmerPhaseControlSelect."""
        from custom_components.bosch_shc.select import DimmerPhaseControlSelect

        dev = _fake_dev("dim1", supports_dimmer_configuration=True)
        collected = self._run_select_setup([dev])
        assert any(isinstance(e, DimmerPhaseControlSelect) for e in collected)


class TestDimmerPhaseControlSelectInit:
    """Lines 940-941: DimmerPhaseControlSelect.__init__."""

    def test_init_sets_unique_id(self):
        """Lines 938-943: real __init__ sets unique_id."""
        from custom_components.bosch_shc.select import DimmerPhaseControlSelect

        dev = _fake_dev("dim1")
        sel = DimmerPhaseControlSelect(dev, "entry1")
        assert "dim1" in sel._attr_unique_id
        assert "dimmer_phase_control" in sel._attr_unique_id


class TestDimmerPhaseControlSelectCurrentOption:
    """Lines 954-955: DimmerPhaseControlSelect.current_option error path."""

    def test_current_option_service_none(self):
        """Line 948-950: dimmer_configuration is None → return None."""
        from custom_components.bosch_shc.select import DimmerPhaseControlSelect

        sel = DimmerPhaseControlSelect.__new__(DimmerPhaseControlSelect)
        sel._device = SimpleNamespace(dimmer_configuration=None, name="Dimmer")
        assert sel.current_option is None

    def test_current_option_attribute_error(self):
        """Lines 954-955: AttributeError accessing edge_phase_control_mode → return None."""
        from custom_components.bosch_shc.select import DimmerPhaseControlSelect

        # Use SimpleNamespace as service — accessing .edge_phase_control_mode raises
        # AttributeError (attribute not defined) → hits except block at lines 954-955
        svc = SimpleNamespace()  # no edge_phase_control_mode attribute
        sel = DimmerPhaseControlSelect.__new__(DimmerPhaseControlSelect)
        sel._attr_options = ["TRAILING", "LEADING"]
        sel._device = SimpleNamespace(dimmer_configuration=svc, name="Dimmer")
        assert sel.current_option is None


class TestDimmerPhaseControlAsyncSelectNone:
    """Line 961: DimmerPhaseControlSelect.async_select_option returns early when service is None."""

    def test_async_select_returns_when_service_none(self):
        """Line 959-961: service is None → returns without calling anything."""
        from custom_components.bosch_shc.select import DimmerPhaseControlSelect

        sel = DimmerPhaseControlSelect.__new__(DimmerPhaseControlSelect)
        sel._device = SimpleNamespace(dimmer_configuration=None, name="Dimmer")
        _run(sel.async_select_option("TRAILING"))  # must not raise


class TestSelectDimmerExcluded:
    """select.py line 364: excluded dimmer device → continue."""

    def test_excluded_dimmer_skipped_in_select_setup(self):
        """Line 364: device_excluded → continue before DimmerPhaseControlSelect."""
        from custom_components.bosch_shc.select import (
            DimmerPhaseControlSelect,
            async_setup_entry,
        )

        dev = _fake_dev("dim_excl", supports_dimmer_configuration=True)

        dh = MagicMock()
        dh.thermostats = []
        dh.roomthermostats = []
        dh.wallthermostats = []
        dh.motion_detectors2 = []
        dh.shutter_contacts2 = []
        dh.outdoor_sirens = []
        dh.micromodule_dimmers = [dev]

        session = MagicMock()
        session.device_helper = dh

        hass = _fake_hass(session=session)
        entry = _fake_entry(hass=hass, options={OPT_EXCLUDED_DEVICES: ["dim_excl"]})

        collected = []
        _run(async_setup_entry(hass, entry, lambda ents, **kw: collected.extend(ents)))
        assert not any(isinstance(e, DimmerPhaseControlSelect) for e in collected)


def test_dimmer_phase_select_current_option():
    s = _new(DimmerPhaseControlSelect)
    s._device = SimpleNamespace(dimmer_configuration=_phase_svc("TRAILING"))
    assert s.current_option == "TRAILING"


def test_dimmer_phase_select_returns_none_when_no_service():
    s = _new(DimmerPhaseControlSelect)
    s._device = SimpleNamespace(dimmer_configuration=None)
    assert s.current_option is None


def test_dimmer_phase_select_set_option():
    from boschshcpy.services_impl import DimmerConfigurationService

    s = _new(DimmerPhaseControlSelect)
    svc = _phase_svc("TRAILING")
    s._device = SimpleNamespace(dimmer_configuration=svc)
    asyncio.run(s.async_select_option("LEADING"))
    svc.async_set_edge_phase_control_mode.assert_awaited_once_with(
        DimmerConfigurationService.EdgePhaseControlMode.LEADING
    )


def test_dimmer_phase_select_invalid_option_is_safe():
    s = _new(DimmerPhaseControlSelect)
    svc = _phase_svc("TRAILING")
    s._device = SimpleNamespace(dimmer_configuration=svc)
    asyncio.run(s.async_select_option("BOGUS"))  # must not raise
    svc.async_set_edge_phase_control_mode.assert_not_awaited()


class TestInstallationProfileCurrentOptionNone:
    """InstallationProfileSelect.current_option returns None when profile is None."""

    def test_current_option_none_when_profile_is_none(self):
        """getattr returns None → return None immediately."""
        from custom_components.bosch_shc.select import InstallationProfileSelect

        ent = InstallationProfileSelect.__new__(InstallationProfileSelect)
        ent._attr_options = ["generic", "outdoor"]
        ent._device = SimpleNamespace(profile=None)
        assert ent.current_option is None


# ---------------------------------------------------------------------------
# InstallationProfileSelect (#353 — writable, replaces the read-only sensor)
# ---------------------------------------------------------------------------


class TestInstallationProfileSelect:
    def test_current_option(self):
        dev = _fake_md2(profile="GENERIC", supported_profiles=["OUTDOOR", "GENERIC"])
        e = InstallationProfileSelect.__new__(InstallationProfileSelect)
        e._device = dev
        e._attr_options = ["outdoor", "generic"]
        assert e.current_option == "generic"

    def test_current_option_out_of_options_returns_none(self):
        # Profile not advertised in supported_profiles must not be a valid option.
        dev = _fake_md2(profile="SURPRISE", supported_profiles=["OUTDOOR", "GENERIC"])
        e = InstallationProfileSelect.__new__(InstallationProfileSelect)
        e._device = dev
        e._attr_options = ["outdoor", "generic"]
        assert e.current_option is None

    def test_async_select_option_uppercases(self):
        dev = _fake_md2(
            profile="GENERIC",
            supported_profiles=["OUTDOOR", "GENERIC"],
            async_set_profile=AsyncMock(),
        )
        e = InstallationProfileSelect.__new__(InstallationProfileSelect)
        e._device = dev
        e._attr_options = ["outdoor", "generic"]
        e._entry_id = "entry-1"
        e.hass = MagicMock()
        asyncio.run(e.async_select_option("outdoor"))
        dev.async_set_profile.assert_called_once_with("OUTDOOR")

    def test_async_select_option_reloads_entry(self):
        """#356: a profile switch must reload the config entry so capability
        -gated entities (e.g. the MD2 [+M] indicator light) are added/removed
        immediately, instead of only after a manual reload/restart."""
        dev = _fake_md2(
            profile="GENERIC",
            supported_profiles=["OUTDOOR", "GENERIC"],
            async_set_profile=AsyncMock(),
        )
        e = InstallationProfileSelect.__new__(InstallationProfileSelect)
        e._device = dev
        e._attr_options = ["outdoor", "generic"]
        e._entry_id = "entry-1"
        e.hass = MagicMock()
        asyncio.run(e.async_select_option("outdoor"))
        e.hass.async_create_task.assert_called_once()
        e.hass.config_entries.async_reload.assert_called_once_with("entry-1")

    def test_options_lowercased_from_supported_profiles(self):
        md2 = _fake_md2(profile="GENERIC", supported_profiles=["OUTDOOR", "GENERIC"])
        e = InstallationProfileSelect(device=md2, entry_id="entry-1")
        assert e._attr_options == ["outdoor", "generic"]

    def test_setup_created_when_profiles_present(self):
        md2 = _fake_md2(profile="GENERIC", supported_profiles=["OUTDOOR", "GENERIC"])
        types = [
            type(e).__name__
            for e in _setup(_make_session(motion_detectors2=[md2]))
        ]
        assert "InstallationProfileSelect" in types

    def test_setup_skipped_when_no_profiles(self):
        md2 = _fake_md2(supported_profiles=[])
        types = [
            type(e).__name__
            for e in _setup(_make_session(motion_detectors2=[md2]))
        ]
        assert "InstallationProfileSelect" not in types

    # The device-level "profile" field (and lib SHCDevice.set_profile) is not
    # MD2-specific: real-world rawscans confirm non-empty supportedProfiles
    # on MICROMODULE_RELAY / PLUG_COMPACT / PLUG_COMPACT_DUAL
    # (knowledge-base/rawscan-database.md), so the select must also be wired
    # up for micromodule_relays / smart_plugs / smart_plugs_compact.
    def test_setup_created_for_micromodule_relay_when_profiles_present(self):
        relay = _fake_md2(
            profile="LIGHT", supported_profiles=["LIGHT", "GENERIC", "HEATING_RCC"]
        )
        types = [
            type(e).__name__
            for e in _setup(_make_session(micromodule_relays=[relay]))
        ]
        assert "InstallationProfileSelect" in types

    def test_setup_created_for_smart_plug_when_profiles_present(self):
        plug = _fake_md2(
            profile="GENERIC", supported_profiles=["LIGHT", "GENERIC", "HEATING_RCC"]
        )
        types = [
            type(e).__name__
            for e in _setup(_make_session(smart_plugs=[plug]))
        ]
        assert "InstallationProfileSelect" in types

    def test_setup_created_for_smart_plug_compact_when_profiles_present(self):
        plug = _fake_md2(
            profile="MINI_PV",
            supported_profiles=["LIGHT", "MINI_PV", "GENERIC", "HEATING_RCC"],
        )
        types = [
            type(e).__name__
            for e in _setup(
                _make_session(smart_plugs_compact=[plug])
            )
        ]
        assert "InstallationProfileSelect" in types

    def test_setup_skipped_for_micromodule_relay_when_no_profiles(self):
        relay = _fake_md2(supported_profiles=[])
        types = [
            type(e).__name__
            for e in _setup(_make_session(micromodule_relays=[relay]))
        ]
        assert "InstallationProfileSelect" not in types


# ---------------------------------------------------------------------------
# SmartSensitivitySecurityLevelSelect + SmartSensitivityComfortLevelSelect
# ---------------------------------------------------------------------------


class TestSmartSensitivitySelectGuard:
    def test_supports_false_callable_present_skipped(self):
        md2 = _fake_device(
            get_smart_sensitivity=lambda c: {"manualLevel": "HIGH"},
            supports_smart_sensitivity=False,
        )
        entities = _setup(_make_session(motion_detectors2=[md2]))
        assert "SmartSensitivitySecurityLevelSelect" not in _types(entities)
        assert "SmartSensitivityComfortLevelSelect" not in _types(entities)

    def test_supports_true_callable_none_skipped(self):
        md2 = _fake_device(
            get_smart_sensitivity=None,
            supports_smart_sensitivity=True,
        )
        entities = _setup(_make_session(motion_detectors2=[md2]))
        assert "SmartSensitivitySecurityLevelSelect" not in _types(entities)
        assert "SmartSensitivityComfortLevelSelect" not in _types(entities)

    def test_both_present_creates_both(self):
        md2 = _fake_device(
            get_smart_sensitivity=lambda c: {"manualLevel": "HIGH"},
            supports_smart_sensitivity=True,
        )
        entities = _setup(_make_session(motion_detectors2=[md2]))
        assert "SmartSensitivitySecurityLevelSelect" in _types(entities)
        assert "SmartSensitivityComfortLevelSelect" in _types(entities)


# ---------------------------------------------------------------------------
# async_setup_entry — integration tests
# ---------------------------------------------------------------------------

def _make_fake_sc2plus(
    name="SC2+", dev_id="hdm:ZigBee:sc2p-001", root_id="64-da-a0-yy-yy-yy",
):
    """Build a SHCShutterContact2Plus-typed fake without calling the real __init__.

    Mirrors the pattern from test_switch_setup.py: local subclass shadows the
    parent read-only properties so isinstance() passes and attrs are settable.
    The sensitivity property is also shadowed to avoid requiring a real service.
    """
    from boschshcpy import SHCShutterContact2Plus

    class _FakePlus(SHCShutterContact2Plus):
        # Shadow parent read-only properties with plain class-level attrs
        name = None  # type: ignore[assignment]
        id = None  # type: ignore[assignment]
        root_device_id = None  # type: ignore[assignment]
        serial = None  # type: ignore[assignment]
        # Shadow the sensitivity property so we don't need _vibrationsensor_service
        sensitivity = VibrationSensorService.SensitivityState.HIGH  # type: ignore[assignment]

        def __init__(self, _name, _id, _root):
            self.name = _name
            self.id = _id
            self.root_device_id = _root
            self.serial = "SER_PLUS"

    return _FakePlus(name, dev_id, root_id)


def _make_fake_sc2(name="SC2", dev_id="hdm:ZigBee:sc2-001", root_id="root-sc2"):
    """Build a SHCShutterContact2-typed fake (not SHCShutterContact2Plus)."""
    from boschshcpy import SHCShutterContact2

    class _FakeSC2(SHCShutterContact2):
        name = None  # type: ignore[assignment]
        id = None  # type: ignore[assignment]
        root_device_id = None  # type: ignore[assignment]
        serial = None  # type: ignore[assignment]

        def __init__(self, _name, _id, _root):
            self.name = _name
            self.id = _id
            self.root_device_id = _root
            self.serial = "SER_SC2"

    return _FakeSC2(name, dev_id, root_id)


class TestSelectSetupEntry:
    """select.py async_setup_entry produces the right entities."""

    def _run(self, session):
        hass = _make_hass()
        entry = _make_config_entry(session)
        collected = []

        def add(entities):
            collected.extend(entities)

        asyncio.run(async_setup_entry(hass, entry, add))  # type: ignore[arg-type]
        return collected

    def _md2_session(self, devices, shutter_contacts2=None):
        return SimpleNamespace(
            device_helper=SimpleNamespace(
                motion_detectors2=devices,
                shutter_contacts2=shutter_contacts2 or [],
            )
        )

    def test_motion_detector2_produces_motion_select(self):
        dev = _ms_device()
        session = self._md2_session([dev])
        result = self._run(session)
        assert len(result) == 1
        assert isinstance(result[0], MotionSensitivitySelect)

    def test_device_without_motion_sensitivity_is_skipped(self):
        dev = SimpleNamespace(
            name="MD2 no-pir",
            id="hdm:ZigBee:nopir",
            root_device_id="root-nopir",
            # no motion_sensitivity attribute
        )
        session = self._md2_session([dev])
        result = self._run(session)
        assert result == []

    def test_shutter_contact2_plus_produces_vibration_select(self):
        """A real SHCShutterContact2Plus subclass passes isinstance check."""
        dev = _make_fake_sc2plus()
        session = self._md2_session([], shutter_contacts2=[dev])
        result = self._run(session)
        assert len(result) == 1
        assert isinstance(result[0], VibrationSensitivitySelect)

    def test_plain_shutter_contact2_skipped_from_vibration(self):
        """A plain SHCShutterContact2 (not SHCShutterContact2Plus) is skipped."""
        dev = _make_fake_sc2()
        session = self._md2_session([], shutter_contacts2=[dev])
        result = self._run(session)
        assert result == []

    def test_no_devices_adds_nothing(self):
        session = self._md2_session([], shutter_contacts2=[])
        result = self._run(session)
        assert result == []

    def test_unique_id_format_motion(self):
        dev = _ms_device()
        session = self._md2_session([dev])
        result = self._run(session)
        expected_uid = f"{dev.root_device_id}_{dev.id}_motion_sensitivity"
        assert result[0]._attr_unique_id == expected_uid

    def test_unique_id_format_vibration(self):
        """VibrationSensitivitySelect unique_id ends with _vibration_sensitivity."""
        dev = _make_fake_sc2plus(
            dev_id="hdm:ZigBee:sc2p-uid", root_id="root-sc2p"
        )
        session = self._md2_session([], shutter_contacts2=[dev])
        result = self._run(session)
        expected_uid = f"{dev.root_device_id}_{dev.id}_vibration_sensitivity"
        assert result[0]._attr_unique_id == expected_uid


# ---------------------------------------------------------------------------
# async_select_option — device-write failures surface as HomeAssistantError
#
# Regression for the 0.10.4 "known, not fixed" gap: the try/except around
# select.py's async_select_option methods only ever guarded the option-name
# -> enum parsing step, never the actual device write call underneath. A
# rejected/failed write (SHCException/SHCConnectionError) used to propagate
# as a raw, unhandled exception instead of the established
# HomeAssistantError(translation_domain=DOMAIN, translation_key=...)
# convention already used by alarm_control_panel.py. Covers 3 representative
# classes (plain write, context-arg write, write-then-reload-task write) —
# not all 18, which would be excessive given they share one code shape.
# ---------------------------------------------------------------------------

class TestAsyncSelectOptionWriteFailureSurfacesAsHomeAssistantError:
    def test_motion_sensitivity_write_failure_raises_home_assistant_error(self):
        """MotionSensitivitySelect: a failed async_set_motion_sensitivity call
        must raise HomeAssistantError, not the raw SHCException.
        """
        sel = _make_motion_select("HIGH")
        sel._device = SimpleNamespace(
            name="MD2",
            motion_sensitivity=PirSensorConfigurationService.MotionSensitivity.HIGH,
            async_set_motion_sensitivity=AsyncMock(
                side_effect=SHCException("rejected")
            ),
        )
        with pytest.raises(HomeAssistantError):
            asyncio.run(sel.async_select_option("LOW"))

    def test_vibration_sensitivity_write_failure_raises_home_assistant_error(self):
        """VibrationSensitivitySelect: a failed async_set_sensitivity call
        must raise HomeAssistantError, not the raw SHCException.
        """
        sel = _make_vibration_select("HIGH")
        sel._device = SimpleNamespace(
            name="SC2+",
            sensitivity=VibrationSensorService.SensitivityState.HIGH,
            async_set_sensitivity=AsyncMock(side_effect=SHCException("rejected")),
        )
        with pytest.raises(HomeAssistantError):
            asyncio.run(sel.async_select_option("MEDIUM"))

    def test_installation_profile_write_failure_raises_and_skips_reload(self):
        """InstallationProfileSelect: a failed async_set_profile call must raise
        HomeAssistantError and must NOT schedule the post-write config-entry
        reload task (the reload line comes after the write and should be
        unreachable on failure).
        """
        ent = InstallationProfileSelect.__new__(InstallationProfileSelect)
        ent._attr_options = ["generic", "outdoor"]
        ent._device = SimpleNamespace(
            name="MD2",
            profile="GENERIC",
            async_set_profile=AsyncMock(side_effect=SHCException("rejected")),
        )
        ent._entry_id = "E1"
        ent.hass = SimpleNamespace(
            async_create_task=lambda *_a, **_kw: pytest.fail(
                "reload task must not be scheduled when the write failed"
            )
        )
        with pytest.raises(HomeAssistantError):
            asyncio.run(ent.async_select_option("outdoor"))

