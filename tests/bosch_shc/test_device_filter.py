"""Tests for the device_excluded() helper in entity.py."""

from types import SimpleNamespace

from custom_components.bosch_shc.entity import device_excluded


def _make_device(device_id="dev-1", room_id="room-1"):
    """Build a minimal fake SHC device using SimpleNamespace."""
    return SimpleNamespace(id=device_id, room_id=room_id, name="Test Device")


class TestDeviceExcluded:
    """Tests for device_excluded()."""

    # ------------------------------------------------------------------
    # Empty / disabled filter
    # ------------------------------------------------------------------

    def test_empty_options_returns_false(self):
        """Empty options dict → nothing excluded."""
        device = _make_device()
        assert device_excluded(device, {}) is False

    def test_empty_lists_returns_false(self):
        """Explicit empty lists → nothing excluded."""
        device = _make_device()
        opts = {"excluded_devices": [], "excluded_rooms": []}
        assert device_excluded(device, opts) is False

    def test_none_lists_returns_false(self):
        """None values for the keys → treated as empty → nothing excluded."""
        device = _make_device()
        opts = {"excluded_devices": None, "excluded_rooms": None}
        assert device_excluded(device, opts) is False

    # ------------------------------------------------------------------
    # Device-id exclusion
    # ------------------------------------------------------------------

    def test_device_id_in_excluded_devices_returns_true(self):
        """Device whose id is in excluded_devices must be excluded."""
        device = _make_device(device_id="dev-42")
        opts = {"excluded_devices": ["dev-42", "dev-99"]}
        assert device_excluded(device, opts) is True

    def test_device_id_not_in_excluded_devices_returns_false(self):
        """Device whose id is NOT in excluded_devices must not be excluded."""
        device = _make_device(device_id="dev-1")
        opts = {"excluded_devices": ["dev-42"]}
        assert device_excluded(device, opts) is False

    # ------------------------------------------------------------------
    # Room-id exclusion
    # ------------------------------------------------------------------

    def test_room_id_in_excluded_rooms_returns_true(self):
        """Device whose room_id is in excluded_rooms must be excluded."""
        device = _make_device(room_id="room-kitchen")
        opts = {"excluded_rooms": ["room-kitchen"]}
        assert device_excluded(device, opts) is True

    def test_room_id_not_in_excluded_rooms_returns_false(self):
        """Device in a non-excluded room must not be excluded."""
        device = _make_device(room_id="room-living")
        opts = {"excluded_rooms": ["room-kitchen"]}
        assert device_excluded(device, opts) is False

    # ------------------------------------------------------------------
    # Device without room_id (e.g. intrusion system)
    # ------------------------------------------------------------------

    def test_device_without_room_id_room_filter_not_excluded(self):
        """Device with no room_id attr → getattr guard → not excluded by room filter."""
        device = SimpleNamespace(id="ids-1", name="Intrusion System")
        # no room_id attribute at all
        opts = {"excluded_rooms": ["room-kitchen"]}
        assert device_excluded(device, opts) is False

    def test_device_without_room_id_device_filter_excluded(self):
        """Device with no room_id can still be excluded by device id."""
        device = SimpleNamespace(id="ids-1", name="Intrusion System")
        opts = {"excluded_devices": ["ids-1"]}
        assert device_excluded(device, opts) is True

    # ------------------------------------------------------------------
    # Device excluded by room but not by id
    # ------------------------------------------------------------------

    def test_device_excluded_by_room_even_if_not_in_device_list(self):
        """Room exclusion works independently of device exclusion list."""
        device = _make_device(device_id="dev-5", room_id="room-bath")
        opts = {"excluded_devices": ["dev-99"], "excluded_rooms": ["room-bath"]}
        assert device_excluded(device, opts) is True

    # ------------------------------------------------------------------
    # Neither excluded
    # ------------------------------------------------------------------

    def test_neither_id_nor_room_excluded_returns_false(self):
        """Device not in either exclusion list → not excluded."""
        device = _make_device(device_id="dev-5", room_id="room-bath")
        opts = {
            "excluded_devices": ["dev-99"],
            "excluded_rooms": ["room-kitchen"],
        }
        assert device_excluded(device, opts) is False
