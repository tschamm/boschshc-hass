"""Unit tests for number.py SHCNumber.__init__ (lines 60-69).

Pattern: call SHCNumber.__init__ directly with a fake device (SimpleNamespace).
SHCEntity.__init__ only needs device.name, device.root_device_id, device.id
and calls _update_attr() which is a no-op in SHCNumber.
No HA harness, no async_setup_entry.
"""

from types import SimpleNamespace

from homeassistant.components.number import NumberDeviceClass
from homeassistant.const import UnitOfTemperature
from homeassistant.helpers.entity import EntityCategory

from custom_components.bosch_shc.number import SHCNumber


def _fake_device(name="test-number", root_device_id="root1", device_id="dev1"):
    return SimpleNamespace(
        name=name,
        root_device_id=root_device_id,
        id=device_id,
        offset=0.0,
        min_offset=-5.0,
        max_offset=5.0,
        step_size=0.5,
    )


class TestSHCNumberInit:
    """Cover SHCNumber.__init__ lines 60-69."""

    def test_init_no_attr_name_sets_name_from_device(self):
        dev = _fake_device()
        number = SHCNumber(device=dev, entry_id="test", attr_name=None)
        assert number._attr_name == "test-number"

    def test_init_no_attr_name_sets_unique_id(self):
        dev = _fake_device()
        number = SHCNumber(device=dev, entry_id="test", attr_name=None)
        assert number._attr_unique_id == "root1_dev1"

    def test_init_with_attr_name_appends_to_name(self):
        dev = _fake_device()
        number = SHCNumber(device=dev, entry_id="test", attr_name="Offset")
        assert number._attr_name == "test-number Offset"

    def test_init_with_attr_name_lowercased_in_unique_id(self):
        dev = _fake_device()
        number = SHCNumber(device=dev, entry_id="test", attr_name="Offset")
        assert number._attr_unique_id == "root1_dev1_offset"

    def test_init_device_stored(self):
        dev = _fake_device()
        number = SHCNumber(device=dev, entry_id="test", attr_name="Offset")
        assert number._device is dev

    def test_init_entry_id_stored(self):
        dev = _fake_device()
        number = SHCNumber(device=dev, entry_id="myentry", attr_name=None)
        assert number._entry_id == "myentry"

    def test_init_attr_name_mixed_case_lowercased_in_unique_id(self):
        dev = _fake_device(name="my-thermo", root_device_id="root2", device_id="dev2")
        number = SHCNumber(device=dev, entry_id="e", attr_name="TempOffset")
        assert number._attr_unique_id == "root2_dev2_tempoffset"
        assert number._attr_name == "my-thermo TempOffset"


class TestSHCNumberClassAttrs:
    """Cover class-level attribute declarations (lines 49-51).

    Access via instance because HA parent classes shadow some attrs with properties.
    """

    def _make_number(self):
        dev = _fake_device()
        return SHCNumber(device=dev, entry_id="test", attr_name="Offset")

    def test_device_class_is_temperature(self):
        number = self._make_number()
        assert number.device_class == NumberDeviceClass.TEMPERATURE

    def test_entity_category_is_diagnostic(self):
        number = self._make_number()
        assert number.entity_category == EntityCategory.DIAGNOSTIC

    def test_native_unit_is_celsius(self):
        number = self._make_number()
        assert number.native_unit_of_measurement == UnitOfTemperature.CELSIUS
