"""Isolation-safe unit tests for the SHCNumber entity.

Pattern: bypass SHCEntity.__init__ via Cls.__new__(Cls), set _device as
SimpleNamespace, and assert pure property logic — no HA harness required.

PIN_EVERY_MODE: one test class per entity characteristic (value, bounds, step,
metadata) with boundary / default / None cases.
"""

from types import SimpleNamespace

from custom_components.bosch_shc.number import SHCNumber
from homeassistant.components.number import NumberDeviceClass
from homeassistant.const import UnitOfTemperature
from homeassistant.helpers.entity import EntityCategory


def _make_number(
    *,
    offset=0.0,
    min_offset=-5.0,
    max_offset=5.0,
    step_size=0.5,
):
    """Return an SHCNumber with _device set via SimpleNamespace (no HA init)."""
    entity = SHCNumber.__new__(SHCNumber)
    entity._device = SimpleNamespace(
        offset=offset,
        min_offset=min_offset,
        max_offset=max_offset,
        step_size=step_size,
    )
    return entity


# ---------------------------------------------------------------------------
# native_value
# ---------------------------------------------------------------------------


def test_native_value_positive():
    entity = _make_number(offset=2.5)
    assert entity.native_value == 2.5


def test_native_value_negative():
    entity = _make_number(offset=-3.0)
    assert entity.native_value == -3.0


def test_native_value_zero():
    entity = _make_number(offset=0.0)
    assert entity.native_value == 0.0


def test_native_value_at_max():
    entity = _make_number(offset=5.0, max_offset=5.0)
    assert entity.native_value == 5.0


def test_native_value_at_min():
    entity = _make_number(offset=-5.0, min_offset=-5.0)
    assert entity.native_value == -5.0


# ---------------------------------------------------------------------------
# native_min_value
# ---------------------------------------------------------------------------


def test_native_min_value_default():
    entity = _make_number(min_offset=-5.0)
    assert entity.native_min_value == -5.0


def test_native_min_value_positive():
    entity = _make_number(min_offset=0.0)
    assert entity.native_min_value == 0.0


def test_native_min_value_large_negative():
    entity = _make_number(min_offset=-100.0)
    assert entity.native_min_value == -100.0


# ---------------------------------------------------------------------------
# native_max_value
# ---------------------------------------------------------------------------


def test_native_max_value_default():
    entity = _make_number(max_offset=5.0)
    assert entity.native_max_value == 5.0


def test_native_max_value_zero():
    entity = _make_number(max_offset=0.0)
    assert entity.native_max_value == 0.0


def test_native_max_value_large():
    entity = _make_number(max_offset=100.0)
    assert entity.native_max_value == 100.0


# ---------------------------------------------------------------------------
# native_step
# ---------------------------------------------------------------------------


def test_native_step_half():
    entity = _make_number(step_size=0.5)
    assert entity.native_step == 0.5


def test_native_step_one():
    entity = _make_number(step_size=1.0)
    assert entity.native_step == 1.0


def test_native_step_fraction():
    entity = _make_number(step_size=0.1)
    assert abs(entity.native_step - 0.1) < 1e-9


# ---------------------------------------------------------------------------
# class-level metadata (device_class, unit, entity_category)
# ---------------------------------------------------------------------------


def test_device_class_is_temperature():
    entity = _make_number()
    assert entity.device_class == NumberDeviceClass.TEMPERATURE


def test_native_unit_is_celsius():
    entity = _make_number()
    assert entity.native_unit_of_measurement == UnitOfTemperature.CELSIUS


def test_entity_category_is_diagnostic():
    entity = _make_number()
    assert entity.entity_category == EntityCategory.DIAGNOSTIC


# ---------------------------------------------------------------------------
# set_native_value round-trip (write-through to _device.offset)
# ---------------------------------------------------------------------------


def test_set_native_value_positive():
    entity = _make_number(offset=0.0)
    entity.set_native_value(3.5)
    assert entity._device.offset == 3.5


def test_set_native_value_negative():
    entity = _make_number(offset=0.0)
    entity.set_native_value(-2.0)
    assert entity._device.offset == -2.0


def test_set_native_value_zero():
    entity = _make_number(offset=5.0)
    entity.set_native_value(0.0)
    assert entity._device.offset == 0.0


def test_set_native_value_at_boundary_max():
    entity = _make_number(max_offset=5.0)
    entity.set_native_value(5.0)
    assert entity._device.offset == 5.0


def test_set_native_value_at_boundary_min():
    entity = _make_number(min_offset=-5.0)
    entity.set_native_value(-5.0)
    assert entity._device.offset == -5.0


# ---------------------------------------------------------------------------
# boundary consistency (min <= value <= max)
# ---------------------------------------------------------------------------


def test_bounds_consistency_default_range():
    entity = _make_number(offset=0.0, min_offset=-5.0, max_offset=5.0)
    assert entity.native_min_value <= entity.native_value <= entity.native_max_value


def test_bounds_consistency_at_extremes():
    for offset in (-5.0, 0.0, 5.0):
        entity = _make_number(offset=offset, min_offset=-5.0, max_offset=5.0)
        assert entity.native_min_value <= entity.native_value <= entity.native_max_value


# ---------------------------------------------------------------------------
# set_native_value clamping (out-of-range values are clamped, not passed through)
# ---------------------------------------------------------------------------


def test_set_native_value_above_max_clamps_to_max():
    """Value above native_max_value must be clamped to max, never sent raw."""
    entity = _make_number(offset=0.0, min_offset=-5.0, max_offset=5.0)
    entity.set_native_value(10.0)
    assert entity._device.offset == 5.0


def test_set_native_value_below_min_clamps_to_min():
    """Value below native_min_value must be clamped to min, never sent raw."""
    entity = _make_number(offset=0.0, min_offset=-5.0, max_offset=5.0)
    entity.set_native_value(-20.0)
    assert entity._device.offset == -5.0


def test_set_native_value_in_range_passes_through():
    """In-range value must reach _device.offset unchanged."""
    entity = _make_number(offset=0.0, min_offset=-5.0, max_offset=5.0)
    entity.set_native_value(2.5)
    assert entity._device.offset == 2.5


def test_set_native_value_exactly_at_max_passes_through():
    """Boundary-equal max must pass through, not be rejected."""
    entity = _make_number(offset=0.0, min_offset=-5.0, max_offset=5.0)
    entity.set_native_value(5.0)
    assert entity._device.offset == 5.0


def test_set_native_value_exactly_at_min_passes_through():
    """Boundary-equal min must pass through, not be rejected."""
    entity = _make_number(offset=0.0, min_offset=-5.0, max_offset=5.0)
    entity.set_native_value(-5.0)
    assert entity._device.offset == -5.0
