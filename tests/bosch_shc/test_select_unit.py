"""Unit tests for select.py — MotionSensitivitySelect and VibrationSensitivitySelect.

Uses __new__ bypass + SimpleNamespace device pattern. No HA harness required.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from boschshcpy.exceptions import SHCException
from boschshcpy.services_impl import (
    PirSensorConfigurationService,
    VibrationSensorService,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory

from custom_components.bosch_shc.select import (
    _MOTION_SENSITIVITY_OPTIONS,
    _VIBRATION_SENSITIVITY_OPTIONS,
    InstallationProfileSelect,
    MotionSensitivitySelect,
    VibrationSensitivitySelect,
    async_setup_entry,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_hass():
    return SimpleNamespace()


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


def _make_motion_select(sensitivity_name="HIGH"):
    dev = _ms_device(sensitivity_name=sensitivity_name)
    sel = MotionSensitivitySelect.__new__(MotionSensitivitySelect)
    sel._device = dev
    sel._attr_name = "Motion Sensitivity"
    sel._attr_unique_id = f"{dev.root_device_id}_{dev.id}_motion_sensitivity"
    return sel


def _make_vibration_select(sensitivity_name="HIGH"):
    dev = _vs_device(sensitivity_name=sensitivity_name)
    sel = VibrationSensitivitySelect.__new__(VibrationSensitivitySelect)
    sel._device = dev
    sel._attr_name = "Vibration Sensitivity"
    sel._attr_unique_id = f"{dev.root_device_id}_{dev.id}_vibration_sensitivity"
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
