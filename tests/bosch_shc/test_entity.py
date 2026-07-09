"""Unit tests for entity.py: SHCEntity base class and its helper functions.

Covers the device_excluded() filter helper, SHCEntity properties, the
async_added_to_hass/async_will_remove_from_hass subscribe/unsubscribe wiring
(including the on_state_changed and update_entity_information callbacks), and
async_remove_stale_entity().

Pattern: bypass __init__ via SHCEntity.__new__(SHCEntity) + inject fake device
via SimpleNamespace. No HA harness, no tests.common.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.bosch_shc.const import DOMAIN
from custom_components.bosch_shc.entity import (
    SHCEntity,
    async_remove_stale_entity,
    device_excluded,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_device(device_id="dev-1", room_id="room-1"):
    """Build a minimal fake SHC device using SimpleNamespace."""
    return SimpleNamespace(id=device_id, room_id=room_id, name="Test Device")


def _make_entity(status="AVAILABLE", deleted=False, device_services=None):
    """Create a bare SHCEntity bypassing __init__, with a fake device."""
    ent = SHCEntity.__new__(SHCEntity)
    ent._device = SimpleNamespace(
        name="Test Device",
        id="dev-123",
        root_device_id="root-456",
        manufacturer="Bosch",
        device_model="TEST-001",
        status=status,
        deleted=deleted,
        device_services=device_services if device_services is not None else [],
    )
    ent._entry_id = "entry-abc"
    # Manually replicate what __init__ would set
    ent._attr_name = f"{ent._device.name}"
    ent._attr_unique_id = f"{ent._device.root_device_id}_{ent._device.id}"
    return ent


class FakeService:
    """Records subscribe_callback / unsubscribe_callback calls."""

    def __init__(self):
        self.calls = []

    def subscribe_callback(self, entity_id, cb):
        self.calls.append(("subscribe", entity_id, cb))

    def unsubscribe_callback(self, entity_id):
        self.calls.append(("unsubscribe", entity_id))


class TrackingEntity(SHCEntity):
    """Concrete subclass that tracks _update_attr and schedule_update_ha_state calls."""

    def __init__(self):
        pass  # skip SHCEntity.__init__

    def _update_attr(self):
        self.update_attr_calls = getattr(self, "update_attr_calls", 0) + 1

    def schedule_update_ha_state(self, force_refresh=False):
        self.schedule_calls = getattr(self, "schedule_calls", 0) + 1


# ---------------------------------------------------------------------------
# device_excluded()
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# SHCEntity properties
# ---------------------------------------------------------------------------


class TestSHCEntityProperties:
    def test_device_name(self):
        ent = _make_entity()
        assert ent.device_name == "Test Device"

    def test_device_id(self):
        ent = _make_entity()
        assert ent.device_id == "dev-123"

    def test_device_info_identifiers(self):
        ent = _make_entity()
        info = ent.device_info
        assert info["identifiers"] == {(DOMAIN, "dev-123")}

    def test_device_info_name(self):
        ent = _make_entity()
        assert ent.device_info["name"] == "Test Device"

    def test_device_info_manufacturer(self):
        ent = _make_entity()
        assert ent.device_info["manufacturer"] == "Bosch"

    def test_device_info_model(self):
        ent = _make_entity()
        assert ent.device_info["model"] == "TEST-001"

    def test_device_info_via_device(self):
        ent = _make_entity()
        assert ent.device_info["via_device"] == (DOMAIN, "root-456")

    def test_available_when_status_available(self):
        ent = _make_entity(status="AVAILABLE")
        assert ent.available is True

    def test_available_when_status_unavailable(self):
        ent = _make_entity(status="UNAVAILABLE")
        assert ent.available is False

    def test_should_poll_is_false(self):
        ent = _make_entity()
        assert ent.should_poll is False

    def test_attr_name_set_from_device_name(self):
        ent = _make_entity()
        assert ent._attr_name == "Test Device"

    def test_attr_unique_id_composed(self):
        ent = _make_entity()
        assert ent._attr_unique_id == "root-456_dev-123"


# ---------------------------------------------------------------------------
# Subscribe wiring (async_added_to_hass)
# ---------------------------------------------------------------------------


class TestAsyncAddedToHass:
    def _run_added(self, ent):
        with patch(
            "homeassistant.helpers.entity.Entity.async_added_to_hass",
            new=AsyncMock(return_value=None),
        ):
            asyncio.run(ent.async_added_to_hass())

    def test_async_added_subscribes_service_callback(self):
        svc = FakeService()
        device_calls = []
        ent = SHCEntity.__new__(SHCEntity)
        ent._device = SimpleNamespace(
            name="Dev",
            id="dev1",
            root_device_id="root1",
            manufacturer="Bosch",
            device_model="M1",
            status="AVAILABLE",
            deleted=False,
            device_services=[svc],
            subscribe_callback=lambda eid, cb: device_calls.append(
                ("subscribe", eid, cb)
            ),
            unsubscribe_callback=lambda eid: device_calls.append(("unsubscribe", eid)),
        )
        ent._entry_id = "entry1"
        ent._attr_name = "Dev"
        ent._attr_unique_id = "root1_dev1"
        ent.entity_id = "switch.test"

        self._run_added(ent)

        assert any(c[0] == "subscribe" and c[1] == "switch.test" for c in svc.calls), (
            f"Expected service subscribe call, got: {svc.calls}"
        )

    def test_async_added_subscribes_device_callback(self):
        svc = FakeService()
        device_calls = []
        ent = SHCEntity.__new__(SHCEntity)
        ent._device = SimpleNamespace(
            name="Dev",
            id="dev1",
            root_device_id="root1",
            manufacturer="Bosch",
            device_model="M1",
            status="AVAILABLE",
            deleted=False,
            device_services=[svc],
            subscribe_callback=lambda eid, cb: device_calls.append(
                ("subscribe", eid, cb)
            ),
            unsubscribe_callback=lambda eid: device_calls.append(("unsubscribe", eid)),
        )
        ent._entry_id = "entry1"
        ent._attr_name = "Dev"
        ent._attr_unique_id = "root1_dev1"
        ent.entity_id = "switch.test"

        self._run_added(ent)

        assert any(
            c[0] == "subscribe" and c[1] == "switch.test" for c in device_calls
        ), f"Expected device subscribe call, got: {device_calls}"


# ---------------------------------------------------------------------------
# Unsubscribe wiring (async_will_remove_from_hass)
# ---------------------------------------------------------------------------


class TestAsyncWillRemoveFromHass:
    def _run_remove(self, ent):
        with patch(
            "homeassistant.helpers.entity.Entity.async_will_remove_from_hass",
            new=AsyncMock(return_value=None),
        ):
            asyncio.run(ent.async_will_remove_from_hass())

    def _make_wired_entity(self):
        svc = FakeService()
        device_calls = []
        ent = SHCEntity.__new__(SHCEntity)
        ent._device = SimpleNamespace(
            name="Dev",
            id="dev1",
            root_device_id="root1",
            manufacturer="Bosch",
            device_model="M1",
            status="AVAILABLE",
            deleted=False,
            device_services=[svc],
            subscribe_callback=lambda eid, cb: None,
            unsubscribe_callback=lambda eid: device_calls.append(("unsubscribe", eid)),
        )
        ent._entry_id = "entry1"
        ent._attr_name = "Dev"
        ent._attr_unique_id = "root1_dev1"
        ent.entity_id = "switch.test"
        return ent, svc, device_calls

    def test_async_will_remove_unsubscribes_service(self):
        ent, svc, _ = self._make_wired_entity()
        self._run_remove(ent)
        assert any(
            c[0] == "unsubscribe" and c[1] == "switch.test" for c in svc.calls
        ), f"Expected service unsubscribe, got: {svc.calls}"

    def test_async_will_remove_unsubscribes_device(self):
        ent, _, device_calls = self._make_wired_entity()
        self._run_remove(ent)
        assert any(
            c[0] == "unsubscribe" and c[1] == "switch.test" for c in device_calls
        ), f"Expected device unsubscribe, got: {device_calls}"


# ---------------------------------------------------------------------------
# on_state_changed callback (extracted after subscribe)
# ---------------------------------------------------------------------------


class TestOnStateChangedCallback:
    def _make_tracking_entity(self):
        svc = FakeService()
        device_calls = []
        ent = TrackingEntity()
        ent._device = SimpleNamespace(
            name="Dev",
            id="dev1",
            root_device_id="root1",
            manufacturer="Bosch",
            device_model="M1",
            status="AVAILABLE",
            deleted=False,
            device_services=[svc],
            subscribe_callback=lambda eid, cb: device_calls.append(
                ("subscribe", eid, cb)
            ),
            unsubscribe_callback=lambda eid: None,
        )
        ent._entry_id = "entry1"
        ent._attr_name = "Dev"
        ent._attr_unique_id = "root1_dev1"
        ent.entity_id = "switch.test"
        ent.update_attr_calls = 0
        ent.schedule_calls = 0
        return ent, svc, device_calls

    def _wire(self, ent):
        with patch(
            "homeassistant.helpers.entity.Entity.async_added_to_hass",
            new=AsyncMock(return_value=None),
        ):
            asyncio.run(ent.async_added_to_hass())

    def test_on_state_changed_calls_update_attr(self):
        ent, svc, _ = self._make_tracking_entity()
        self._wire(ent)

        # Extract the callback registered on the service
        subscribe_calls = [c for c in svc.calls if c[0] == "subscribe"]
        assert subscribe_calls, "No subscribe call recorded on service"
        on_state_changed = subscribe_calls[0][2]

        on_state_changed()
        assert ent.update_attr_calls >= 1

    def test_on_state_changed_calls_schedule_update(self):
        ent, svc, _ = self._make_tracking_entity()
        self._wire(ent)

        subscribe_calls = [c for c in svc.calls if c[0] == "subscribe"]
        assert subscribe_calls, "No subscribe call recorded on service"
        on_state_changed = subscribe_calls[0][2]

        on_state_changed()
        assert ent.schedule_calls >= 1

    def test_update_entity_information_deleted_calls_create_task(self):
        """When device.deleted is True, update_entity_information must call
        the thread-safe hass.create_task() — NOT hass.async_create_task(),
        which is not thread-safe and raises when called from boschshcpy's
        background polling thread (this callback's real caller)."""
        svc = FakeService()
        task_calls = []
        ent = TrackingEntity()
        ent._device = SimpleNamespace(
            name="Dev",
            id="dev1",
            root_device_id="root1",
            manufacturer="Bosch",
            device_model="M1",
            status="AVAILABLE",
            deleted=True,
            device_services=[svc],
            subscribe_callback=lambda eid, cb: None,
            unsubscribe_callback=lambda eid: None,
        )
        ent._entry_id = "entry1"
        ent._attr_name = "Dev"
        ent._attr_unique_id = "root1_dev1"
        ent.entity_id = "switch.test"
        ent.update_attr_calls = 0
        ent.schedule_calls = 0

        # Fake hass exposing only the thread-safe create_task(); a stray
        # async_create_task() call would raise AttributeError here, exactly
        # like the real (non-thread-safe) HA method would raise off-loop.
        fake_loop = SimpleNamespace(call_soon_threadsafe=lambda *a, **kw: None)
        fake_hass = SimpleNamespace(
            loop=fake_loop,
            create_task=lambda coro: task_calls.append(coro),
        )
        ent.hass = fake_hass

        # Capture the update_entity_information callback registered on device
        dev_callbacks = []

        def _subscribe(eid, cb):
            dev_callbacks.append(cb)

        ent._device.subscribe_callback = _subscribe

        with patch(
            "homeassistant.helpers.entity.Entity.async_added_to_hass",
            new=AsyncMock(return_value=None),
        ):
            asyncio.run(ent.async_added_to_hass())

        assert dev_callbacks, "No device subscribe_callback registered"
        update_entity_information = dev_callbacks[0]

        # Calling it with deleted=True should call hass.create_task
        update_entity_information()
        assert len(task_calls) >= 1, (
            "Expected hass.create_task to be called for deleted device"
        )


# ---------------------------------------------------------------------------
# async_remove_stale_entity (#356)
# ---------------------------------------------------------------------------


class TestAsyncRemoveStaleEntity:
    def test_removes_entity_when_registered(self):
        """A registered stale entity is looked up by (domain, DOMAIN, unique_id)
        and removed via entity_registry.async_remove."""
        fake_ent_reg = MagicMock()
        fake_ent_reg.async_get_entity_id.return_value = "light.md2_motion_light"

        with patch(
            "custom_components.bosch_shc.entity.entity_registry.async_get",
            return_value=fake_ent_reg,
        ):
            asyncio.run(
                async_remove_stale_entity(
                    hass=SimpleNamespace(),
                    entity_domain="light",
                    unique_id="root_dev-1_motionlight",
                )
            )

        fake_ent_reg.async_get_entity_id.assert_called_once_with(
            "light", DOMAIN, "root_dev-1_motionlight"
        )
        fake_ent_reg.async_remove.assert_called_once_with("light.md2_motion_light")

    def test_no_op_when_not_registered(self):
        """No matching entity in the registry -> async_remove is never called."""
        fake_ent_reg = MagicMock()
        fake_ent_reg.async_get_entity_id.return_value = None

        with patch(
            "custom_components.bosch_shc.entity.entity_registry.async_get",
            return_value=fake_ent_reg,
        ):
            asyncio.run(
                async_remove_stale_entity(
                    hass=SimpleNamespace(),
                    entity_domain="light",
                    unique_id="root_dev-1_motionlight",
                )
            )

        fake_ent_reg.async_remove.assert_not_called()
