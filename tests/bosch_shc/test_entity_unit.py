"""Unit tests for entity.py (SHCEntity base class).

Pattern: bypass __init__ via SHCEntity.__new__(SHCEntity) + inject fake device
via SimpleNamespace. No HA harness, no tests.common.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.bosch_shc.const import DOMAIN
from custom_components.bosch_shc.entity import SHCEntity


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Properties
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


class FakeService:
    """Records subscribe_callback / unsubscribe_callback calls."""

    def __init__(self):
        self.calls = []

    def subscribe_callback(self, entity_id, cb):
        self.calls.append(("subscribe", entity_id, cb))

    def unsubscribe_callback(self, entity_id):
        self.calls.append(("unsubscribe", entity_id))


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
            subscribe_callback=lambda eid, cb: device_calls.append(("subscribe", eid, cb)),
            unsubscribe_callback=lambda eid: device_calls.append(("unsubscribe", eid)),
        )
        ent._entry_id = "entry1"
        ent._attr_name = "Dev"
        ent._attr_unique_id = "root1_dev1"
        ent.entity_id = "switch.test"

        self._run_added(ent)

        assert any(
            c[0] == "subscribe" and c[1] == "switch.test" for c in svc.calls
        ), f"Expected service subscribe call, got: {svc.calls}"

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
            subscribe_callback=lambda eid, cb: device_calls.append(("subscribe", eid, cb)),
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


class TrackingEntity(SHCEntity):
    """Concrete subclass that tracks _update_attr and schedule_update_ha_state calls."""

    def __init__(self):
        pass  # skip SHCEntity.__init__

    def _update_attr(self):
        self.update_attr_calls = getattr(self, "update_attr_calls", 0) + 1

    def schedule_update_ha_state(self, force_refresh=False):
        self.schedule_calls = getattr(self, "schedule_calls", 0) + 1


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
            subscribe_callback=lambda eid, cb: device_calls.append(("subscribe", eid, cb)),
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

    def test_update_entity_information_deleted_calls_async_create_task(self):
        """When device.deleted is True, update_entity_information should call
        hass.async_create_task directly (not call_soon_threadsafe)."""
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

        # Fake hass with async_create_task (direct call path after thread-safety fix)
        fake_loop = SimpleNamespace(
            call_soon_threadsafe=lambda *a, **kw: None
        )
        fake_hass = SimpleNamespace(
            loop=fake_loop,
            async_create_task=lambda coro: task_calls.append(coro),
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

        # Calling it with deleted=True should call hass.async_create_task directly
        update_entity_information()
        assert len(task_calls) >= 1, (
            "Expected hass.async_create_task to be called for deleted device"
        )
