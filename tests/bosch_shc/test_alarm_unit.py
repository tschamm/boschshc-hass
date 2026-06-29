"""Isolation-safe unit tests for uncovered lines in alarm_control_panel.py.

Targets: lines 19-37 (async_setup_entry), 45-47 (__init__),
         51-56 (async_added_to_hass), 60-61 (async_will_remove_from_hass).

Pattern: NO HA harness, NO tests.common.
  - async_setup_entry: fully mocked hass/config_entry/async_add_entities + patch
    entity.async_migrate_to_new_unique_id to a coroutine no-op.
  - __init__: direct construction with a SimpleNamespace device.
  - async_added_to_hass / async_will_remove_from_hass: Cls.__new__ + inject fakes,
    asyncio.run() to drive the coroutines.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from boschshcpy import SHCIntrusionSystem

from custom_components.bosch_shc.alarm_control_panel import (
    IntrusionSystemAlarmControlPanel,
    async_setup_entry,
)
from custom_components.bosch_shc.const import DATA_SESSION, DOMAIN

AlarmState = SHCIntrusionSystem.AlarmState
ArmingState = SHCIntrusionSystem.ArmingState
Profile = SHCIntrusionSystem.Profile


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_device(
    root_device_id="root-1",
    device_id="dev-1",
    name="Intrusion",
    manufacturer="Bosch",
    device_model="IDS",
    alarm_state=AlarmState.ALARM_OFF,
    arming_state=ArmingState.SYSTEM_DISARMED,
    profile=Profile.FULL_PROTECTION,
    system_availability=True,
):
    """Build a minimal fake intrusion-system device."""
    subscriptions: dict = {}

    def subscribe_callback(entity_id, cb):
        subscriptions[entity_id] = cb

    def unsubscribe_callback(entity_id):
        subscriptions.pop(entity_id, None)

    return SimpleNamespace(
        root_device_id=root_device_id,
        id=device_id,
        name=name,
        manufacturer=manufacturer,
        device_model=device_model,
        alarm_state=alarm_state,
        arming_state=arming_state,
        active_configuration_profile=profile,
        system_availability=system_availability,
        subscribe_callback=subscribe_callback,
        unsubscribe_callback=unsubscribe_callback,
        disarm=MagicMock(),
        arm_full_protection=MagicMock(),
        arm_partial_protection=MagicMock(),
        arm_individual_protection=MagicMock(),
        mute=MagicMock(),
        _subscriptions=subscriptions,
    )


# ---------------------------------------------------------------------------
# __init__ (lines 45-47)
# ---------------------------------------------------------------------------

def test_init_sets_device_entry_id_and_unique_id():
    """Direct __init__ — verify attributes are wired correctly."""
    device = _fake_device(root_device_id="r1", device_id="d1")
    panel = IntrusionSystemAlarmControlPanel(device=device, entry_id="entry-42")

    assert panel._device is device
    assert panel._entry_id == "entry-42"
    assert panel._attr_unique_id == "r1_d1"


def test_init_unique_id_uses_root_and_device_id():
    """unique_id is root_device_id + _ + device.id, not entry_id + device.id."""
    device = _fake_device(root_device_id="rootXYZ", device_id="devABC")
    panel = IntrusionSystemAlarmControlPanel(device=device, entry_id="ignored-entry")
    assert panel._attr_unique_id == "rootXYZ_devABC"


# ---------------------------------------------------------------------------
# async_added_to_hass (lines 51-56)
# ---------------------------------------------------------------------------

def test_async_added_to_hass_subscribes_callback():
    """Subscribes an on_state_changed callback keyed by entity_id."""
    device = _fake_device()
    panel = IntrusionSystemAlarmControlPanel.__new__(IntrusionSystemAlarmControlPanel)
    panel._device = device
    panel._entry_id = "e1"
    panel._attr_unique_id = "r1_d1"
    panel.entity_id = "alarm_control_panel.test"
    panel.schedule_update_ha_state = MagicMock()

    asyncio.run(panel.async_added_to_hass())

    assert "alarm_control_panel.test" in device._subscriptions


def test_async_added_to_hass_callback_calls_schedule_update():
    """The registered callback must call schedule_update_ha_state."""
    device = _fake_device()
    panel = IntrusionSystemAlarmControlPanel.__new__(IntrusionSystemAlarmControlPanel)
    panel._device = device
    panel._entry_id = "e1"
    panel._attr_unique_id = "r1_d1"
    panel.entity_id = "alarm_control_panel.cb_test"
    schedule_update = MagicMock()
    panel.schedule_update_ha_state = schedule_update

    asyncio.run(panel.async_added_to_hass())

    # Fire the callback that was registered
    device._subscriptions["alarm_control_panel.cb_test"]()
    assert schedule_update.called


# ---------------------------------------------------------------------------
# async_will_remove_from_hass (lines 60-61)
# ---------------------------------------------------------------------------

def test_async_will_remove_from_hass_unsubscribes():
    """Unsubscribes the callback keyed by entity_id on removal."""
    device = _fake_device()
    panel = IntrusionSystemAlarmControlPanel.__new__(IntrusionSystemAlarmControlPanel)
    panel._device = device
    panel._entry_id = "e1"
    panel._attr_unique_id = "r1_d1"
    panel.entity_id = "alarm_control_panel.remove_test"
    panel.schedule_update_ha_state = MagicMock()

    # First subscribe, then remove
    asyncio.run(panel.async_added_to_hass())
    assert "alarm_control_panel.remove_test" in device._subscriptions

    asyncio.run(panel.async_will_remove_from_hass())
    assert "alarm_control_panel.remove_test" not in device._subscriptions


def test_async_will_remove_from_hass_tolerates_not_subscribed():
    """Unsubscribe when never subscribed must not raise."""
    device = _fake_device()
    panel = IntrusionSystemAlarmControlPanel.__new__(IntrusionSystemAlarmControlPanel)
    panel._device = device
    panel._entry_id = "e1"
    panel._attr_unique_id = "r1_d1"
    panel.entity_id = "alarm_control_panel.never_added"

    # Should not raise even without a prior subscribe
    asyncio.run(panel.async_will_remove_from_hass())


# ---------------------------------------------------------------------------
# async_setup_entry (lines 19-37)
# ---------------------------------------------------------------------------

async def _run_setup_entry():
    """Inner coroutine — called via asyncio.run in the sync test below."""
    entry_id = "cfg-entry-1"
    device = _fake_device(root_device_id="r2", device_id="d2")

    session = SimpleNamespace(intrusion_system=device)

    hass = SimpleNamespace(
        data={DOMAIN: {entry_id: {DATA_SESSION: session}}},
    )

    config_entry = SimpleNamespace(options={}, entry_id=entry_id)

    added: list = []

    def async_add_entities(entities):
        added.extend(entities)

    # Patch async_migrate_to_new_unique_id to a coroutine no-op
    with patch(
        "custom_components.bosch_shc.alarm_control_panel.async_migrate_to_new_unique_id",
        new=AsyncMock(return_value=None),
    ):
        await async_setup_entry(hass, config_entry, async_add_entities)

    return added, device, entry_id


def test_async_setup_entry_adds_one_entity():
    """async_setup_entry appends exactly one IntrusionSystemAlarmControlPanel."""
    added, device, entry_id = asyncio.run(_run_setup_entry())

    assert len(added) == 1
    assert isinstance(added[0], IntrusionSystemAlarmControlPanel)


def test_async_setup_entry_entity_uses_correct_device():
    """The entity's _device is the intrusion_system from the session."""
    added, device, entry_id = asyncio.run(_run_setup_entry())

    panel = added[0]
    assert panel._device is device


def test_async_setup_entry_entity_uses_correct_entry_id():
    """The entity's _entry_id matches config_entry.entry_id."""
    added, device, entry_id = asyncio.run(_run_setup_entry())

    panel = added[0]
    assert panel._entry_id == entry_id


def test_async_setup_entry_calls_migrate_with_old_unique_id():
    """async_migrate_to_new_unique_id is called with old_unique_id=entry_id_device_id."""
    entry_id = "cfg-entry-migrate"
    device = _fake_device(root_device_id="r3", device_id="dev-migrate")

    session = SimpleNamespace(intrusion_system=device)
    hass = SimpleNamespace(
        data={DOMAIN: {entry_id: {DATA_SESSION: session}}},
    )
    config_entry = SimpleNamespace(options={}, entry_id=entry_id)

    migrate_mock = AsyncMock(return_value=None)

    with patch(
        "custom_components.bosch_shc.alarm_control_panel.async_migrate_to_new_unique_id",
        new=migrate_mock,
    ):
        asyncio.run(async_setup_entry(hass, config_entry, lambda e: None))

    migrate_mock.assert_awaited_once()
    kwargs = migrate_mock.call_args
    assert kwargs.kwargs.get("old_unique_id") == f"{entry_id}_{device.id}"
    assert kwargs.kwargs.get("device") is device
    assert kwargs.kwargs.get("attr_name") is None
