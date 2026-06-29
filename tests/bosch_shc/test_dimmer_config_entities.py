"""Unit tests for DimmerConfiguration entities in number.py, button.py, select.py (#123).

Pure-unit style: build entities via __new__ or direct constructor + SimpleNamespace device.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from custom_components.bosch_shc.button import (
    DimmerPreviewMaxButton,
    DimmerPreviewMinButton,
)
from custom_components.bosch_shc.number import DimmerConfigNumber
from custom_components.bosch_shc.select import DimmerPhaseControlSelect

_FAKE_DEVICE = SimpleNamespace(
    root_device_id="root-1",
    id="hdm:ZigBee:dimmer1",
    name="Büro Dimmer",
    status="AVAILABLE",
)


def _new(cls):
    return cls.__new__(cls)


# ----------------------------- DimmerConfigNumber --------------------------

def _dimmer_svc(min_b=10, max_b=90, speed=4):
    return SimpleNamespace(
        min_brightness=min_b,
        max_brightness=max_b,
        dimming_speed=speed,
        async_set_brightness_range=AsyncMock(),
        async_set_dimming_speed=AsyncMock(),
    )


def test_dimmer_number_min_reads_correctly():
    n = DimmerConfigNumber(_FAKE_DEVICE, "e1", "min", 0, 100)
    n._device = SimpleNamespace(dimmer_configuration=_dimmer_svc(min_b=15))
    assert n.native_value == 15.0


def test_dimmer_number_max_reads_correctly():
    n = DimmerConfigNumber(_FAKE_DEVICE, "e1", "max", 0, 100)
    n._device = SimpleNamespace(dimmer_configuration=_dimmer_svc(max_b=85))
    assert n.native_value == 85.0


def test_dimmer_number_speed_reads_correctly():
    n = DimmerConfigNumber(_FAKE_DEVICE, "e1", "speed", 1, 10)
    n._device = SimpleNamespace(dimmer_configuration=_dimmer_svc(speed=7))
    assert n.native_value == 7.0


def test_dimmer_number_returns_none_when_no_service():
    n = DimmerConfigNumber(_FAKE_DEVICE, "e1", "min", 0, 100)
    n._device = SimpleNamespace(dimmer_configuration=None)
    assert n.native_value is None


def test_dimmer_number_min_set_calls_set_brightness_range():
    n = DimmerConfigNumber(_FAKE_DEVICE, "e1", "min", 0, 100)
    svc = _dimmer_svc(min_b=10, max_b=90)
    n._device = SimpleNamespace(dimmer_configuration=svc)
    asyncio.run(n.async_set_native_value(25.0))
    svc.async_set_brightness_range.assert_awaited_once_with(min_brightness=25)


def test_dimmer_number_max_set_calls_set_brightness_range():
    n = DimmerConfigNumber(_FAKE_DEVICE, "e1", "max", 0, 100)
    svc = _dimmer_svc(min_b=10, max_b=90)
    n._device = SimpleNamespace(dimmer_configuration=svc)
    asyncio.run(n.async_set_native_value(80.0))
    svc.async_set_brightness_range.assert_awaited_once_with(max_brightness=80)


def test_dimmer_number_speed_set_calls_set_dimming_speed():
    n = DimmerConfigNumber(_FAKE_DEVICE, "e1", "speed", 1, 10)
    svc = _dimmer_svc(speed=5)
    n._device = SimpleNamespace(dimmer_configuration=svc)
    asyncio.run(n.async_set_native_value(3.0))
    svc.async_set_dimming_speed.assert_awaited_once_with(3)


def test_dimmer_number_clamps_to_range():
    n = DimmerConfigNumber(_FAKE_DEVICE, "e1", "speed", 1, 10)
    svc = _dimmer_svc()
    n._device = SimpleNamespace(dimmer_configuration=svc)
    asyncio.run(n.async_set_native_value(99.0))  # above max 10
    svc.async_set_dimming_speed.assert_awaited_once_with(10)


def test_dimmer_number_set_no_service_is_safe():
    n = DimmerConfigNumber(_FAKE_DEVICE, "e1", "min", 0, 100)
    n._device = SimpleNamespace(dimmer_configuration=None)
    # must not raise
    asyncio.run(n.async_set_native_value(50.0))


def test_dimmer_number_has_correct_names():
    n_min = DimmerConfigNumber(_FAKE_DEVICE, "e1", "min", 0, 100)
    n_max = DimmerConfigNumber(_FAKE_DEVICE, "e1", "max", 0, 100)
    n_spd = DimmerConfigNumber(_FAKE_DEVICE, "e1", "speed", 1, 10)
    assert n_min._attr_name == "Dimmer Min Brightness"
    assert n_max._attr_name == "Dimmer Max Brightness"
    assert n_spd._attr_name == "Dimming Speed"


def test_dimmer_number_unique_ids():
    n = DimmerConfigNumber(_FAKE_DEVICE, "e1", "min", 0, 100)
    assert n.unique_id == "root-1_hdm:ZigBee:dimmer1_dimmer_min"


# ----------------------- DimmerPreviewMaxButton ----------------------------

def test_dimmer_preview_max_calls_service():
    btn = _new(DimmerPreviewMaxButton)
    svc = SimpleNamespace(async_preview_max_brightness=AsyncMock())
    btn._device = SimpleNamespace(dimmer_configuration=svc)
    asyncio.run(btn.async_press())
    svc.async_preview_max_brightness.assert_awaited_once()


def test_dimmer_preview_min_calls_service():
    btn = _new(DimmerPreviewMinButton)
    svc = SimpleNamespace(async_preview_min_brightness=AsyncMock())
    btn._device = SimpleNamespace(dimmer_configuration=svc)
    asyncio.run(btn.async_press())
    svc.async_preview_min_brightness.assert_awaited_once()


def test_dimmer_preview_buttons_safe_without_service():
    max_btn = _new(DimmerPreviewMaxButton)
    max_btn._device = SimpleNamespace(dimmer_configuration=None)
    asyncio.run(max_btn.async_press())  # no error

    min_btn = _new(DimmerPreviewMinButton)
    min_btn._device = SimpleNamespace(dimmer_configuration=None)
    asyncio.run(min_btn.async_press())  # no error


# ----------------------- DimmerPhaseControlSelect --------------------------

def _phase_svc(mode_name="TRAILING"):
    from boschshcpy.services_impl import DimmerConfigurationService

    class _FakeEnum:
        def __init__(self, name):
            self.name = name

    mode = DimmerConfigurationService.EdgePhaseControlMode(mode_name)
    return SimpleNamespace(
        edge_phase_control_mode=mode,
        EdgePhaseControlMode=DimmerConfigurationService.EdgePhaseControlMode,
        async_set_edge_phase_control_mode=AsyncMock(),
    )


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
