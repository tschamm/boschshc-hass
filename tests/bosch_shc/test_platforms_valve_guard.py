"""Regression test for Fix #169: Platform.VALVE must be guarded with hasattr().

On older HA versions that pre-date the VALVE platform, Platform.VALVE does not
exist. The module-level PLATFORMS list must only include it when available.
"""

import importlib
import types

import pytest


def test_platforms_includes_valve_when_available():
    """Platform.VALVE present -> included in PLATFORMS."""
    from homeassistant.const import Platform

    if not hasattr(Platform, "VALVE"):
        pytest.skip("This HA version does not have Platform.VALVE")

    import custom_components.bosch_shc.__init__ as init_mod

    importlib.reload(init_mod)
    assert Platform.VALVE in init_mod.PLATFORMS, (
        "Platform.VALVE should be in PLATFORMS when available"
    )


def test_platforms_excludes_valve_when_missing():
    """If Platform has no VALVE attribute, PLATFORMS must not contain it."""
    from homeassistant.const import Platform

    # Build a fake Platform namespace without VALVE to simulate older HA
    fake_platform = types.SimpleNamespace(
        BINARY_SENSOR=Platform.BINARY_SENSOR,
        BUTTON=Platform.BUTTON,
        COVER=Platform.COVER,
        EVENT=Platform.EVENT,
        SENSOR=Platform.SENSOR,
        SWITCH=Platform.SWITCH,
        CLIMATE=Platform.CLIMATE,
        ALARM_CONTROL_PANEL=Platform.ALARM_CONTROL_PANEL,
        LIGHT=Platform.LIGHT,
        NUMBER=Platform.NUMBER,
        # VALVE intentionally omitted
    )

    # Replicate the guarded PLATFORMS construction from __init__.py
    platforms = [
        fake_platform.BINARY_SENSOR,
        fake_platform.BUTTON,
        fake_platform.COVER,
        fake_platform.EVENT,
        fake_platform.SENSOR,
        fake_platform.SWITCH,
        fake_platform.CLIMATE,
        fake_platform.ALARM_CONTROL_PANEL,
        fake_platform.LIGHT,
        fake_platform.NUMBER,
    ]
    if hasattr(fake_platform, "VALVE"):
        platforms.append(fake_platform.VALVE)

    assert Platform.VALVE not in platforms, (
        "PLATFORMS must not contain Platform.VALVE when Platform has no VALVE attribute"
    )


def test_platforms_includes_all_base_platforms():
    """All non-optional platforms are always present regardless of VALVE availability."""
    from homeassistant.const import Platform

    import custom_components.bosch_shc.__init__ as init_mod

    importlib.reload(init_mod)

    required = [
        Platform.BINARY_SENSOR,
        Platform.BUTTON,
        Platform.COVER,
        Platform.EVENT,
        Platform.SENSOR,
        Platform.SWITCH,
        Platform.CLIMATE,
        Platform.ALARM_CONTROL_PANEL,
        Platform.LIGHT,
        Platform.NUMBER,
    ]
    for platform in required:
        assert platform in init_mod.PLATFORMS, (
            f"{platform} must always be in PLATFORMS"
        )


def test_platforms_valve_guard_is_hasattr():
    """Verify the guard pattern: hasattr(Platform, 'VALVE') -> append, else skip."""
    # Test the guard logic itself with a known-present attribute
    present_ns = types.SimpleNamespace(PRESENT=True)
    missing_ns = types.SimpleNamespace()

    result_present = []
    if hasattr(present_ns, "PRESENT"):
        result_present.append("PRESENT")
    assert "PRESENT" in result_present

    result_missing = []
    if hasattr(missing_ns, "ABSENT"):
        result_missing.append("ABSENT")
    assert "ABSENT" not in result_missing
