"""Unit tests for valve.py SHCValve.__init__ (lines 57-66).

Pattern: call SHCValve.__init__ directly with a fake device (SimpleNamespace).
SHCEntity.__init__ only needs device.name, device.root_device_id, device.id
and calls _update_attr() which is a no-op in SHCValve.
No HA harness, no async_setup_entry.
"""

from types import SimpleNamespace

from homeassistant.components.valve import ValveDeviceClass
from homeassistant.helpers.entity import EntityCategory

from custom_components.bosch_shc.valve import SHCValve


def _fake_device(name="test-valve", root_device_id="root1", device_id="dev1"):
    return SimpleNamespace(
        name=name,
        root_device_id=root_device_id,
        id=device_id,
        position=50,
    )


class TestSHCValveInit:
    """Cover SHCValve.__init__ lines 57-66."""

    def test_init_no_attr_name_sets_name_from_device(self):
        dev = _fake_device()
        valve = SHCValve(device=dev, entry_id="test", attr_name=None)
        assert valve._attr_name == "test-valve"

    def test_init_no_attr_name_sets_unique_id(self):
        dev = _fake_device()
        valve = SHCValve(device=dev, entry_id="test", attr_name=None)
        assert valve._attr_unique_id == "root1_dev1"

    def test_init_with_attr_name_appends_to_name(self):
        dev = _fake_device()
        valve = SHCValve(device=dev, entry_id="test", attr_name="Valve")
        assert valve._attr_name == "test-valve Valve"

    def test_init_with_attr_name_lowercased_in_unique_id(self):
        dev = _fake_device()
        valve = SHCValve(device=dev, entry_id="test", attr_name="Valve")
        assert valve._attr_unique_id == "root1_dev1_valve"

    def test_init_device_stored(self):
        dev = _fake_device()
        valve = SHCValve(device=dev, entry_id="test", attr_name="Valve")
        assert valve._device is dev

    def test_init_entry_id_stored(self):
        dev = _fake_device()
        valve = SHCValve(device=dev, entry_id="myentry", attr_name=None)
        assert valve._entry_id == "myentry"

    def test_init_attr_name_mixed_case_lowercased_in_unique_id(self):
        dev = _fake_device(name="my-cam", root_device_id="root2", device_id="dev2")
        valve = SHCValve(device=dev, entry_id="e", attr_name="ThermoValve")
        assert valve._attr_unique_id == "root2_dev2_thermovalve"
        assert valve._attr_name == "my-cam ThermoValve"


class TestSHCValveClassAttrs:
    """Cover class-level attribute declarations (lines 46-48).

    Access via instance because HA parent classes shadow some attrs with properties.
    """

    def _make_valve(self):
        dev = _fake_device()
        return SHCValve(device=dev, entry_id="test", attr_name="Valve")

    def test_device_class_is_water(self):
        valve = self._make_valve()
        assert valve.device_class == ValveDeviceClass.WATER

    def test_entity_category_is_diagnostic(self):
        valve = self._make_valve()
        assert valve.entity_category == EntityCategory.DIAGNOSTIC

    def test_reports_position_is_true(self):
        valve = self._make_valve()
        assert valve.reports_position is True
