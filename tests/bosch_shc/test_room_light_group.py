"""Unit tests for #244: opt-in per-room "all lights" aggregate light entity.

Covers the SHCRoomLightGroup entity's aggregation logic (is_on/available over
multiple devices, turn_on/turn_off fan-out, partial-failure tolerance) built
directly via its real __init__ (no hass dependency), plus the room-grouping
wiring in light.py's async_setup_entry (option gating, 2+ device threshold,
stale-entity cleanup) driven with a fake hass/config_entry/session, mirroring
the pattern in test_platforms_setup.py.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.components.light import ColorMode

from custom_components.bosch_shc.const import DOMAIN, OPT_ROOM_LIGHT_GROUPS
from custom_components.bosch_shc.light import LightSwitch, SHCRoomLightGroup


def _make_light_device(
    *,
    device_id: str,
    binarystate: bool | None = False,
    status: str = "AVAILABLE",
    room_id: str | None = "hz_1",
    async_set_binarystate=None,
) -> SimpleNamespace:
    """Minimal device for LightSwitch/SHCRoomLightGroup grouping."""
    return SimpleNamespace(
        name=f"Test Light {device_id}",
        id=device_id,
        room_id=room_id,
        root_device_id="aa:bb:cc:00:00:03",
        serial=f"serial-{device_id}",
        supports_color_hsb=False,
        supports_color_temp=False,
        supports_brightness=True,
        min_color_temperature=153,
        max_color_temperature=500,
        binarystate=binarystate,
        device_services=[],
        manufacturer="Bosch",
        device_model="LD",
        status=status,
        deleted=False,
        async_set_binarystate=async_set_binarystate or AsyncMock(),
    )


def _make_room(room_id: str, name: str) -> SimpleNamespace:
    return SimpleNamespace(id=room_id, name=name)


# ── SHCRoomLightGroup: pure entity behaviour ─────────────────────────────────


def test_unique_id_and_device_info():
    devices = [_make_light_device(device_id="d1"), _make_light_device(device_id="d2")]
    group = SHCRoomLightGroup(
        devices=devices, room_id="hz_1", room_name="Wohnzimmer", entry_id="E1"
    )
    assert group.unique_id == "room_hz_1_light_group"
    assert group.device_info["identifiers"] == {(DOMAIN, "room_hz_1_light")}
    assert group.device_info["name"] == "Wohnzimmer"
    assert group.device_info["via_device"] == (DOMAIN, "aa:bb:cc:00:00:03")


def test_color_mode_is_onoff():
    devices = [_make_light_device(device_id="d1"), _make_light_device(device_id="d2")]
    group = SHCRoomLightGroup(
        devices=devices, room_id="hz_1", room_name="Wohnzimmer", entry_id="E1"
    )
    assert group.color_mode == ColorMode.ONOFF
    assert group.supported_color_modes == {ColorMode.ONOFF}


def test_is_on_true_when_any_member_on():
    devices = [
        _make_light_device(device_id="d1", binarystate=False),
        _make_light_device(device_id="d2", binarystate=True),
    ]
    group = SHCRoomLightGroup(
        devices=devices, room_id="hz_1", room_name="Wohnzimmer", entry_id="E1"
    )
    assert group.is_on is True


def test_is_on_false_when_all_members_off():
    devices = [
        _make_light_device(device_id="d1", binarystate=False),
        _make_light_device(device_id="d2", binarystate=False),
    ]
    group = SHCRoomLightGroup(
        devices=devices, room_id="hz_1", room_name="Wohnzimmer", entry_id="E1"
    )
    assert group.is_on is False


def test_is_on_none_when_all_members_unknown():
    devices = [
        _make_light_device(device_id="d1", binarystate=None),
        _make_light_device(device_id="d2", binarystate=None),
    ]
    group = SHCRoomLightGroup(
        devices=devices, room_id="hz_1", room_name="Wohnzimmer", entry_id="E1"
    )
    assert group.is_on is None


def test_available_true_when_any_member_available():
    devices = [
        _make_light_device(device_id="d1", status="UNAVAILABLE"),
        _make_light_device(device_id="d2", status="AVAILABLE"),
    ]
    group = SHCRoomLightGroup(
        devices=devices, room_id="hz_1", room_name="Wohnzimmer", entry_id="E1"
    )
    assert group.available is True


def test_available_false_when_no_member_available():
    devices = [
        _make_light_device(device_id="d1", status="UNAVAILABLE"),
        _make_light_device(device_id="d2", status="UNAVAILABLE"),
    ]
    group = SHCRoomLightGroup(
        devices=devices, room_id="hz_1", room_name="Wohnzimmer", entry_id="E1"
    )
    assert group.available is False


def test_turn_on_sets_binarystate_true_on_all_members():
    d1 = _make_light_device(device_id="d1")
    d2 = _make_light_device(device_id="d2")
    group = SHCRoomLightGroup(
        devices=[d1, d2], room_id="hz_1", room_name="Wohnzimmer", entry_id="E1"
    )
    asyncio.run(group.async_turn_on())
    d1.async_set_binarystate.assert_awaited_once_with(True)
    d2.async_set_binarystate.assert_awaited_once_with(True)


def test_turn_off_sets_binarystate_false_on_all_members():
    d1 = _make_light_device(device_id="d1")
    d2 = _make_light_device(device_id="d2")
    group = SHCRoomLightGroup(
        devices=[d1, d2], room_id="hz_1", room_name="Wohnzimmer", entry_id="E1"
    )
    asyncio.run(group.async_turn_off())
    d1.async_set_binarystate.assert_awaited_once_with(False)
    d2.async_set_binarystate.assert_awaited_once_with(False)


def test_turn_on_one_member_failure_does_not_block_others():
    """A single device's write failure must not prevent the others from being set."""

    async def _raise(_value):
        raise ConnectionError("boom")

    d1 = _make_light_device(device_id="d1", async_set_binarystate=_raise)
    d2 = _make_light_device(device_id="d2")
    group = SHCRoomLightGroup(
        devices=[d1, d2], room_id="hz_1", room_name="Wohnzimmer", entry_id="E1"
    )
    asyncio.run(group.async_turn_on())  # must not raise
    d2.async_set_binarystate.assert_awaited_once_with(True)


def test_subscribes_to_every_member_device_and_service_on_add():
    # subscribe_callback/unsubscribe_callback are plain sync methods on the
    # real lib classes — use MagicMock (not AsyncMock) to match that.
    service = SimpleNamespace(
        subscribe_callback=MagicMock(), unsubscribe_callback=MagicMock()
    )
    d1 = _make_light_device(device_id="d1")
    d1.device_services = [service]
    d1.subscribe_callback = MagicMock()
    d1.unsubscribe_callback = MagicMock()
    group = SHCRoomLightGroup(
        devices=[d1], room_id="hz_1", room_name="Wohnzimmer", entry_id="E1"
    )
    group.entity_id = "light.wohnzimmer_light"
    group.hass = SimpleNamespace()
    group.async_write_ha_state = lambda: None

    asyncio.run(group.async_added_to_hass())
    service.subscribe_callback.assert_called_once()
    d1.subscribe_callback.assert_called_once()

    asyncio.run(group.async_will_remove_from_hass())
    service.unsubscribe_callback.assert_called_once_with("light.wohnzimmer_light")
    d1.unsubscribe_callback.assert_called_once_with("light.wohnzimmer_light")


def test_device_deletion_triggers_config_entry_reload():
    """A member unpaired live (no options change) must trigger a full reload.

    Unlike SHCEntity (one entity = one device, which just detaches itself),
    this group can't locally repair its membership — reloading re-runs
    async_setup_entry, which rebuilds/removes the group from the current
    device list. Same recovery already used by select.py's
    InstallationProfileSelect after a profile write.
    """
    d1 = _make_light_device(device_id="d1")
    d2 = _make_light_device(device_id="d2")
    d1.subscribe_callback = MagicMock()
    d2.subscribe_callback = MagicMock()
    group = SHCRoomLightGroup(
        devices=[d1, d2], room_id="hz_1", room_name="Wohnzimmer", entry_id="E1"
    )
    group.entity_id = "light.wohnzimmer_light"
    group.hass = SimpleNamespace(
        async_create_task=MagicMock(),
        config_entries=SimpleNamespace(async_reload=AsyncMock()),
    )

    asyncio.run(group.async_added_to_hass())
    device_callback = d1.subscribe_callback.call_args.args[1]

    d1.deleted = True
    device_callback()

    group.hass.async_create_task.assert_called_once()
    group.hass.config_entries.async_reload.assert_called_once_with("E1")


def test_device_change_without_deletion_just_refreshes_state():
    """A non-deletion device-level update must NOT trigger a reload."""
    d1 = _make_light_device(device_id="d1")
    d1.subscribe_callback = MagicMock()
    group = SHCRoomLightGroup(
        devices=[d1], room_id="hz_1", room_name="Wohnzimmer", entry_id="E1"
    )
    group.entity_id = "light.wohnzimmer_light"
    group.hass = SimpleNamespace(async_create_task=MagicMock())
    refreshed = []
    group.schedule_update_ha_state = lambda: refreshed.append(True)

    asyncio.run(group.async_added_to_hass())
    device_callback = d1.subscribe_callback.call_args.args[1]
    device_callback()

    assert refreshed == [True]
    group.hass.async_create_task.assert_not_called()


# ── light.py async_setup_entry: room-grouping wiring ─────────────────────────


def _make_config_entry(session: object, room_light_groups: bool) -> SimpleNamespace:
    entry = SimpleNamespace(
        options={OPT_ROOM_LIGHT_GROUPS: room_light_groups}, entry_id="E1"
    )
    entry.runtime_data = SimpleNamespace(session=session)
    return entry


def _collect() -> tuple[list, callable]:
    collected: list = []

    def add(entities: list) -> None:
        collected.extend(entities)

    return collected, add


def _run_setup(session: object, room_light_groups: bool) -> list:
    from custom_components.bosch_shc.light import async_setup_entry

    hass = SimpleNamespace()
    entry = _make_config_entry(session, room_light_groups)
    collected, add = _collect()

    async def _run_inner() -> None:
        with patch(
            "custom_components.bosch_shc.light.async_remove_stale_entity",
            new_callable=AsyncMock,
        ) as remove_mock:
            await async_setup_entry(hass, entry, add)  # type: ignore[arg-type]
            return remove_mock

    with patch(
        "custom_components.bosch_shc.light.async_migrate_to_new_unique_id",
        new_callable=AsyncMock,
    ):
        remove_mock = asyncio.run(_run_inner())

    return collected, remove_mock


def _make_session(devices: list, rooms: list) -> SimpleNamespace:
    return SimpleNamespace(
        device_helper=SimpleNamespace(
            ledvance_lights=devices,
            micromodule_dimmers=[],
            hue_lights=[],
            motion_detectors2=[],
        ),
        rooms=rooms,
    )


def test_option_disabled_creates_no_group():
    devices = [
        _make_light_device(device_id="d1", room_id="hz_1"),
        _make_light_device(device_id="d2", room_id="hz_1"),
    ]
    session = _make_session(devices, [_make_room("hz_1", "Wohnzimmer")])
    entities, remove_mock = _run_setup(session, room_light_groups=False)
    assert not any(isinstance(e, SHCRoomLightGroup) for e in entities)
    assert len(entities) == 2  # the 2 LightSwitch entities only
    # Option off -> cleanup path runs once, for the one room that has devices.
    assert remove_mock.await_count == 1
    assert remove_mock.await_args.args[1:] == ("light", "room_hz_1_light_group")


def test_option_enabled_two_lights_same_room_creates_group():
    devices = [
        _make_light_device(device_id="d1", room_id="hz_1"),
        _make_light_device(device_id="d2", room_id="hz_1"),
    ]
    session = _make_session(devices, [_make_room("hz_1", "Wohnzimmer")])
    entities, remove_mock = _run_setup(session, room_light_groups=True)
    groups = [e for e in entities if isinstance(e, SHCRoomLightGroup)]
    assert len(groups) == 1
    assert groups[0].unique_id == "room_hz_1_light_group"
    assert remove_mock.await_count == 0


def test_option_enabled_single_light_in_room_creates_no_group():
    devices = [_make_light_device(device_id="d1", room_id="hz_1")]
    session = _make_session(devices, [_make_room("hz_1", "Wohnzimmer")])
    entities, remove_mock = _run_setup(session, room_light_groups=True)
    assert not any(isinstance(e, SHCRoomLightGroup) for e in entities)
    assert remove_mock.await_count == 1


def test_option_enabled_lights_in_different_rooms_create_no_group():
    devices = [
        _make_light_device(device_id="d1", room_id="hz_1"),
        _make_light_device(device_id="d2", room_id="hz_2"),
    ]
    rooms = [_make_room("hz_1", "Wohnzimmer"), _make_room("hz_2", "Küche")]
    session = _make_session(devices, rooms)
    entities, remove_mock = _run_setup(session, room_light_groups=True)
    assert not any(isinstance(e, SHCRoomLightGroup) for e in entities)
    assert remove_mock.await_count == 2


def test_missing_rooms_attribute_does_not_crash():
    """Older/fake sessions without `.rooms` must not break setup (getattr-safe)."""
    devices = [_make_light_device(device_id="d1", room_id="hz_1")]
    session = SimpleNamespace(
        device_helper=SimpleNamespace(
            ledvance_lights=devices,
            micromodule_dimmers=[],
            hue_lights=[],
            motion_detectors2=[],
        )
    )
    entities, _ = _run_setup(session, room_light_groups=True)
    assert len(entities) == 1
    assert isinstance(entities[0], LightSwitch)
