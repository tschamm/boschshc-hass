"""Unit tests for number.py SHCNumber — property getters and async_set_native_value clamping.

Covers lines not exercised by test_number_unit.py:
  - async_set_native_value: clamping to [native_min_value, native_max_value]
  - native_value: offset passthrough
  - native_step: step_size passthrough
  - native_min_value: min_offset passthrough
  - native_max_value: max_offset passthrough

Pattern: SHCNumber.__new__ bypass + SimpleNamespace device.
No HA harness.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from custom_components.bosch_shc.number import SHCNumber

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _make_number(
    offset=1.0,
    min_offset=-5.0,
    max_offset=5.0,
    step_size=0.5,
):
    """Build SHCNumber via __new__ with a SimpleNamespace device."""
    n = SHCNumber.__new__(SHCNumber)
    n._device = SimpleNamespace(
        offset=offset,
        min_offset=min_offset,
        max_offset=max_offset,
        step_size=step_size,
        async_set_offset=AsyncMock(),
    )
    n._attr_name = "Offset"
    n._attr_unique_id = "root1_dev1_offset"
    return n


# ---------------------------------------------------------------------------
# native_value (line 76-77)
# ---------------------------------------------------------------------------

class TestSHCNumberNativeValue:
    def test_native_value_returns_device_offset(self):
        n = _make_number(offset=2.5)
        assert n.native_value == 2.5

    def test_native_value_zero(self):
        n = _make_number(offset=0.0)
        assert n.native_value == 0.0

    def test_native_value_negative(self):
        n = _make_number(offset=-3.0)
        assert n.native_value == -3.0

    def test_native_value_at_max(self):
        n = _make_number(offset=5.0, max_offset=5.0)
        assert n.native_value == 5.0

    def test_native_value_at_min(self):
        n = _make_number(offset=-5.0, min_offset=-5.0)
        assert n.native_value == -5.0


# ---------------------------------------------------------------------------
# native_step (line 80-82)
# ---------------------------------------------------------------------------

class TestSHCNumberNativeStep:
    def test_native_step_returns_step_size(self):
        n = _make_number(step_size=0.5)
        assert n.native_step == 0.5

    def test_native_step_one(self):
        n = _make_number(step_size=1.0)
        assert n.native_step == 1.0


# ---------------------------------------------------------------------------
# native_min_value (line 85-87)
# ---------------------------------------------------------------------------

class TestSHCNumberNativeMinValue:
    def test_native_min_value_returns_min_offset(self):
        n = _make_number(min_offset=-5.0)
        assert n.native_min_value == -5.0

    def test_native_min_value_zero(self):
        n = _make_number(min_offset=0.0)
        assert n.native_min_value == 0.0


# ---------------------------------------------------------------------------
# native_max_value (line 90-92)
# ---------------------------------------------------------------------------

class TestSHCNumberNativeMaxValue:
    def test_native_max_value_returns_max_offset(self):
        n = _make_number(max_offset=5.0)
        assert n.native_max_value == 5.0

    def test_native_max_value_positive_non_integer(self):
        n = _make_number(max_offset=4.5)
        assert n.native_max_value == 4.5


# ---------------------------------------------------------------------------
# async_set_native_value — clamping
# ---------------------------------------------------------------------------

class TestSHCNumberSetNativeValue:
    def test_in_range_value_written_directly(self):
        """Value within [min, max] must be written as-is."""
        n = _make_number(min_offset=-5.0, max_offset=5.0)
        asyncio.run(n.async_set_native_value(2.0))
        n._device.async_set_offset.assert_awaited_once_with(2.0)

    def test_value_above_max_clamped_to_max(self):
        """Value > max_offset must be clamped to max_offset."""
        n = _make_number(min_offset=-5.0, max_offset=5.0)
        asyncio.run(n.async_set_native_value(10.0))
        n._device.async_set_offset.assert_awaited_once_with(5.0)

    def test_value_below_min_clamped_to_min(self):
        """Value < min_offset must be clamped to min_offset."""
        n = _make_number(min_offset=-5.0, max_offset=5.0)
        asyncio.run(n.async_set_native_value(-10.0))
        n._device.async_set_offset.assert_awaited_once_with(-5.0)

    def test_value_exactly_at_max_written(self):
        n = _make_number(min_offset=-5.0, max_offset=5.0)
        asyncio.run(n.async_set_native_value(5.0))
        n._device.async_set_offset.assert_awaited_once_with(5.0)

    def test_value_exactly_at_min_written(self):
        n = _make_number(min_offset=-5.0, max_offset=5.0)
        asyncio.run(n.async_set_native_value(-5.0))
        n._device.async_set_offset.assert_awaited_once_with(-5.0)

    def test_fractional_clamped_to_max(self):
        n = _make_number(min_offset=-5.0, max_offset=2.5)
        asyncio.run(n.async_set_native_value(3.0))
        n._device.async_set_offset.assert_awaited_once_with(2.5)

    def test_fractional_clamped_to_min(self):
        n = _make_number(min_offset=-2.5, max_offset=5.0)
        asyncio.run(n.async_set_native_value(-3.0))
        n._device.async_set_offset.assert_awaited_once_with(-2.5)

    def test_zero_within_range_written(self):
        n = _make_number(min_offset=-5.0, max_offset=5.0)
        asyncio.run(n.async_set_native_value(0.0))
        n._device.async_set_offset.assert_awaited_once_with(0.0)

    def test_clamping_does_not_write_original_value(self):
        """When clamped, the clamped (not original) value is sent to async_set_offset."""
        n = _make_number(min_offset=0.0, max_offset=3.0)
        asyncio.run(n.async_set_native_value(99.9))
        called_with = n._device.async_set_offset.call_args[0][0]
        assert called_with != 99.9
        assert called_with == 3.0
