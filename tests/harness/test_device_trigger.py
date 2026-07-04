"""The tests for Bosch SHC Integration device triggers."""
from unittest.mock import MagicMock, AsyncMock
import pytest

from homeassistant.components.device_automation import DeviceAutomationType
from homeassistant.helpers import device_registry as dr

from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    assert_lists_same,
    async_get_device_automations,
    async_mock_service,
    mock_device_registry,
)

from types import SimpleNamespace

from custom_components.bosch_shc.const import (
    ALARM_EVENTS_SUBTYPES_SD,
    ALARM_EVENTS_SUBTYPES_SDS,
    ATTR_EVENT_SUBTYPE,
    ATTR_EVENT_TYPE,
    CONF_SUBTYPE,
    DOMAIN,
    EVENT_BOSCH_SHC,
    INPUTS_EVENTS_SUBTYPES_WRC2,
    INPUTS_EVENTS_SUBTYPES_SWITCH2,
    SUPPORTED_INPUTS_EVENTS_TYPES,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_shc_device(device_id: str, device_model: str, *, extra=None) -> MagicMock:
    """Return a minimal SHCDevice mock."""
    dev = MagicMock()
    dev.id = device_id
    dev.device_model = device_model
    if extra:
        for k, v in extra.items():
            setattr(dev, k, v)
    return dev


def _install_session(hass, config_entry_id: str, devices: list, *, intrusion=None, shc_controller=None):
    """Inject a fake SHC session via config_entry.runtime_data (the modern
    replacement for hass.data[DOMAIN][entry_id]).

    device_trigger.py's get_device_from_id() reads
    `entry.runtime_data.session` for every entry returned by
    hass.config_entries.async_entries(DOMAIN) — the entry must already be
    registered on hass (via MockConfigEntry.add_to_hass) before this runs.
    """
    session = MagicMock()
    session.devices = devices
    session.intrusion_system = intrusion

    if shc_controller:
        info = MagicMock()
        info.unique_id = shc_controller["unique_id"]
        session.information = info
        session.scenario_names = shc_controller.get("scenario_names", [])
    else:
        info = MagicMock()
        info.unique_id = "shc-not-used"
        session.information = info

    entry = hass.config_entries.async_get_entry(config_entry_id)
    entry.runtime_data = SimpleNamespace(session=session)
    return session


def _register_device(
    device_reg: dr.DeviceRegistry, config_entry: MockConfigEntry, shc_id: str
) -> dr.DeviceEntry:
    """Register a device with a Bosch SHC identifier and return its entry."""
    return device_reg.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={(DOMAIN, shc_id)},
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def device_reg(hass):
    """Return an empty mocked device registry."""
    return mock_device_registry(hass)


@pytest.fixture
def calls(hass):
    """Track calls to a mock service."""
    return async_mock_service(hass, "test", "automation")


# ---------------------------------------------------------------------------
# Tests — async_get_triggers
# ---------------------------------------------------------------------------

async def test_get_triggers_wrc2(hass, device_reg):
    """WRC2 device produces PRESS_SHORT/LONG/LONG_RELEASED × LOWER/UPPER_BUTTON."""
    config_entry = MockConfigEntry(domain=DOMAIN, data={})
    config_entry.add_to_hass(hass)

    shc_dev = _make_shc_device("shc-dev-wrc2", "WRC2")
    device_entry = _register_device(device_reg, config_entry, "shc-dev-wrc2")
    _install_session(hass, config_entry.entry_id, [shc_dev])

    triggers = await async_get_device_automations(
        hass, DeviceAutomationType.TRIGGER, device_entry.id
    )

    assert triggers is not None
    # 3 event types × 2 subtypes = 6 triggers
    assert len(triggers) == 6
    types = {t["type"] for t in triggers}
    subtypes = {t["subtype"] for t in triggers}
    assert types == {"PRESS_SHORT", "PRESS_LONG", "PRESS_LONG_RELEASED"}
    assert subtypes == INPUTS_EVENTS_SUBTYPES_WRC2
    for t in triggers:
        assert t["platform"] == "device"
        assert t["domain"] == DOMAIN
        assert t["device_id"] == device_entry.id


async def test_get_triggers_switch2(hass, device_reg):
    """SWITCH2 device produces 3 event types × 4 subtypes = 12 triggers."""
    config_entry = MockConfigEntry(domain=DOMAIN, data={})
    config_entry.add_to_hass(hass)

    shc_dev = _make_shc_device("shc-dev-sw2", "SWITCH2")
    device_entry = _register_device(device_reg, config_entry, "shc-dev-sw2")
    _install_session(hass, config_entry.entry_id, [shc_dev])

    triggers = await async_get_device_automations(
        hass, DeviceAutomationType.TRIGGER, device_entry.id
    )

    assert len(triggers) == 12
    types = {t["type"] for t in triggers}
    subtypes = {t["subtype"] for t in triggers}
    assert types == {"PRESS_SHORT", "PRESS_LONG", "PRESS_LONG_RELEASED"}
    assert subtypes == INPUTS_EVENTS_SUBTYPES_SWITCH2


async def test_get_triggers_motion_detector(hass, device_reg):
    """MD (motion detector) produces a single MOTION trigger with empty subtype."""
    config_entry = MockConfigEntry(domain=DOMAIN, data={})
    config_entry.add_to_hass(hass)

    shc_dev = _make_shc_device("shc-dev-md", "MD")
    device_entry = _register_device(device_reg, config_entry, "shc-dev-md")
    _install_session(hass, config_entry.entry_id, [shc_dev])

    triggers = await async_get_device_automations(
        hass, DeviceAutomationType.TRIGGER, device_entry.id
    )

    assert len(triggers) == 1
    assert triggers[0]["type"] == "MOTION"
    assert triggers[0]["subtype"] == ""
    assert triggers[0]["device_id"] == device_entry.id


async def test_get_triggers_smoke_detector(hass, device_reg):
    """SD (smoke detector) produces one ALARM trigger per ALARM_EVENTS_SUBTYPES_SD."""
    config_entry = MockConfigEntry(domain=DOMAIN, data={})
    config_entry.add_to_hass(hass)

    shc_dev = _make_shc_device("shc-dev-sd", "SD")
    device_entry = _register_device(device_reg, config_entry, "shc-dev-sd")
    _install_session(hass, config_entry.entry_id, [shc_dev])

    triggers = await async_get_device_automations(
        hass, DeviceAutomationType.TRIGGER, device_entry.id
    )

    assert len(triggers) == len(ALARM_EVENTS_SUBTYPES_SD)
    assert all(t["type"] == "ALARM" for t in triggers)
    assert {t["subtype"] for t in triggers} == ALARM_EVENTS_SUBTYPES_SD


async def test_get_triggers_smoke_detection_system(hass, device_reg):
    """SMOKE_DETECTION_SYSTEM produces one ALARM trigger per ALARM_EVENTS_SUBTYPES_SDS."""
    config_entry = MockConfigEntry(domain=DOMAIN, data={})
    config_entry.add_to_hass(hass)

    shc_dev = _make_shc_device("shc-dev-sds", "SMOKE_DETECTION_SYSTEM")
    device_entry = _register_device(device_reg, config_entry, "shc-dev-sds")
    _install_session(hass, config_entry.entry_id, [shc_dev])

    triggers = await async_get_device_automations(
        hass, DeviceAutomationType.TRIGGER, device_entry.id
    )

    assert len(triggers) == len(ALARM_EVENTS_SUBTYPES_SDS)
    assert all(t["type"] == "ALARM" for t in triggers)
    assert {t["subtype"] for t in triggers} == ALARM_EVENTS_SUBTYPES_SDS


async def test_get_triggers_shc_scenarios(hass, device_reg):
    """SHC controller produces one SCENARIO trigger per scenario_name."""
    config_entry = MockConfigEntry(domain=DOMAIN, data={})
    config_entry.add_to_hass(hass)

    scenario_names = ["Away", "Sleep", "Home"]
    shc_unique_id = "shc-controller-001"

    # Register the SHC controller device
    device_entry = _register_device(device_reg, config_entry, shc_unique_id)

    # The session itself is treated as the SHC controller — its id lives in
    # session.information.unique_id, and its scenario_names is on the session.
    session = _install_session(
        hass,
        config_entry.entry_id,
        devices=[],
        shc_controller={"unique_id": shc_unique_id, "scenario_names": scenario_names},
    )
    # Make session itself the "device" returned when device_id matches (SHC path)
    session.id = shc_unique_id
    session.scenario_names = scenario_names

    triggers = await async_get_device_automations(
        hass, DeviceAutomationType.TRIGGER, device_entry.id
    )

    assert len(triggers) == len(scenario_names)
    assert all(t["type"] == "SCENARIO" for t in triggers)
    assert {t["subtype"] for t in triggers} == set(scenario_names)


async def test_get_triggers_multiple_devices_in_session(hass, device_reg):
    """Device list with a non-matching device before the target — covers the
    `continue` branch (line 59) in get_device_from_id."""
    config_entry = MockConfigEntry(domain=DOMAIN, data={})
    config_entry.add_to_hass(hass)

    # Two SHC devices; only the second is registered in HA as a device
    shc_dev_other = _make_shc_device("shc-dev-other", "MD")
    shc_dev_target = _make_shc_device("shc-dev-md2", "MD")
    device_entry = _register_device(device_reg, config_entry, "shc-dev-md2")
    # shc-dev-other has no HA device registry entry → async_get_device returns None → continue
    _install_session(hass, config_entry.entry_id, [shc_dev_other, shc_dev_target])

    triggers = await async_get_device_automations(
        hass, DeviceAutomationType.TRIGGER, device_entry.id
    )

    assert len(triggers) == 1
    assert triggers[0]["type"] == "MOTION"


async def test_get_triggers_ids_device(hass, device_reg):
    """Intrusion Detection System (IDS) device produces triggers via the IDS path."""
    config_entry = MockConfigEntry(domain=DOMAIN, data={})
    config_entry.add_to_hass(hass)

    # IDS device registered in HA
    ids_mock = MagicMock()
    ids_mock.id = "shc-ids-001"
    # IDS device model isn't WRC2/SWITCH2/MD/SD/SDS/SHC — results in 0 triggers
    # but we confirm the IDS path is hit (no exception)
    device_entry = _register_device(device_reg, config_entry, "shc-ids-001")
    _install_session(hass, config_entry.entry_id, devices=[], intrusion=ids_mock)

    triggers = await async_get_device_automations(
        hass, DeviceAutomationType.TRIGGER, device_entry.id
    )
    # IDS model is "IDS" which has no matching trigger block → empty list
    assert isinstance(triggers, list)


async def test_get_triggers_unknown_device(hass, device_reg):
    """Regression: a device whose SHC-id is not registered must NOT crash the
    SHC-controller / IDS fallback path of get_device_from_id.

    Before the fix, the SHC + IDS paths did `device.id` without checking that
    async_get_device returned a device, so an unregistered id raised
    `AttributeError: 'NoneType' ... 'id'`. They now guard with
    `device is not None and device.id == device_id`, so get_device_from_id
    returns (None, "") and async_get_triggers raises the intended
    InvalidDeviceAutomationConfig.

    Setup: empty devices list, no intrusion system, an HA device whose id does
    not correspond to any SHC unique_id → all three lookup paths miss cleanly.
    """
    config_entry = MockConfigEntry(domain=DOMAIN, data={})
    config_entry.add_to_hass(hass)

    # Register a HA device; the SHC session's information.unique_id won't match
    # anything in the registry → async_get_device returns None → AttributeError.
    device_entry = _register_device(device_reg, config_entry, "registered-ha-device")
    _install_session(hass, config_entry.entry_id, devices=[])

    from homeassistant.components.device_automation.exceptions import (
        InvalidDeviceAutomationConfig,
    )

    from custom_components.bosch_shc.device_trigger import async_get_triggers

    # Fixed behaviour: unguarded None no longer crashes; the unknown device
    # falls through to (None, "") and the intended config error is raised.
    with pytest.raises(InvalidDeviceAutomationConfig):
        await async_get_triggers(hass, device_entry.id)


# ---------------------------------------------------------------------------
# Tests — async_attach_trigger (fires on EVENT_BOSCH_SHC bus event)
# ---------------------------------------------------------------------------

async def test_attach_trigger_fires_on_event(hass, device_reg, calls):
    """async_attach_trigger subscribes to EVENT_BOSCH_SHC and fires the action."""
    from homeassistant.components import automation
    from homeassistant.setup import async_setup_component

    config_entry = MockConfigEntry(domain=DOMAIN, data={})
    config_entry.add_to_hass(hass)

    shc_dev = _make_shc_device("shc-dev-wrc2-b", "WRC2")
    device_entry = _register_device(device_reg, config_entry, "shc-dev-wrc2-b")
    _install_session(hass, config_entry.entry_id, [shc_dev])

    device_id = device_entry.id

    assert await async_setup_component(
        hass,
        automation.DOMAIN,
        {
            automation.DOMAIN: [
                {
                    "trigger": {
                        "platform": "device",
                        "domain": DOMAIN,
                        "device_id": device_id,
                        "type": "PRESS_SHORT",
                        "subtype": "LOWER_BUTTON",
                    },
                    "action": {
                        "service": "test.automation",
                        "data_template": {
                            "some": "pressed - {{ trigger.event.data.event_type }}"
                                    " - {{ trigger.event.data.event_subtype }}"
                        },
                    },
                }
            ]
        },
    )
    await hass.async_block_till_done()

    # Fire the matching Bosch SHC event on the bus
    hass.bus.async_fire(
        EVENT_BOSCH_SHC,
        {
            "device_id": device_id,
            ATTR_EVENT_TYPE: "PRESS_SHORT",
            ATTR_EVENT_SUBTYPE: "LOWER_BUTTON",
        },
    )
    await hass.async_block_till_done()

    assert len(calls) == 1
    assert calls[0].data["some"] == "pressed - PRESS_SHORT - LOWER_BUTTON"


async def test_attach_trigger_does_not_fire_for_wrong_device(hass, device_reg, calls):
    """async_attach_trigger must not fire when device_id doesn't match."""
    from homeassistant.components import automation
    from homeassistant.setup import async_setup_component

    config_entry = MockConfigEntry(domain=DOMAIN, data={})
    config_entry.add_to_hass(hass)

    shc_dev = _make_shc_device("shc-dev-wrc2-c", "WRC2")
    device_entry = _register_device(device_reg, config_entry, "shc-dev-wrc2-c")
    _install_session(hass, config_entry.entry_id, [shc_dev])

    device_id = device_entry.id

    assert await async_setup_component(
        hass,
        automation.DOMAIN,
        {
            automation.DOMAIN: [
                {
                    "trigger": {
                        "platform": "device",
                        "domain": DOMAIN,
                        "device_id": device_id,
                        "type": "PRESS_SHORT",
                        "subtype": "LOWER_BUTTON",
                    },
                    "action": {"service": "test.automation"},
                }
            ]
        },
    )
    await hass.async_block_till_done()

    # Fire for a DIFFERENT device — must NOT trigger
    hass.bus.async_fire(
        EVENT_BOSCH_SHC,
        {
            "device_id": "some-other-device-id",
            ATTR_EVENT_TYPE: "PRESS_SHORT",
            ATTR_EVENT_SUBTYPE: "LOWER_BUTTON",
        },
    )
    await hass.async_block_till_done()

    assert len(calls) == 0


async def test_attach_trigger_does_not_fire_for_wrong_subtype(hass, device_reg, calls):
    """async_attach_trigger must not fire when subtype doesn't match."""
    from homeassistant.components import automation
    from homeassistant.setup import async_setup_component

    config_entry = MockConfigEntry(domain=DOMAIN, data={})
    config_entry.add_to_hass(hass)

    shc_dev = _make_shc_device("shc-dev-wrc2-d", "WRC2")
    device_entry = _register_device(device_reg, config_entry, "shc-dev-wrc2-d")
    _install_session(hass, config_entry.entry_id, [shc_dev])

    device_id = device_entry.id

    assert await async_setup_component(
        hass,
        automation.DOMAIN,
        {
            automation.DOMAIN: [
                {
                    "trigger": {
                        "platform": "device",
                        "domain": DOMAIN,
                        "device_id": device_id,
                        "type": "PRESS_SHORT",
                        "subtype": "LOWER_BUTTON",
                    },
                    "action": {"service": "test.automation"},
                }
            ]
        },
    )
    await hass.async_block_till_done()

    # Fire with UPPER_BUTTON instead of LOWER_BUTTON
    hass.bus.async_fire(
        EVENT_BOSCH_SHC,
        {
            "device_id": device_id,
            ATTR_EVENT_TYPE: "PRESS_SHORT",
            ATTR_EVENT_SUBTYPE: "UPPER_BUTTON",
        },
    )
    await hass.async_block_till_done()

    assert len(calls) == 0


async def test_attach_trigger_alarm_event(hass, device_reg, calls):
    """ALARM trigger fires when an SD smoke detector ALARM event arrives."""
    from homeassistant.components import automation
    from homeassistant.setup import async_setup_component

    config_entry = MockConfigEntry(domain=DOMAIN, data={})
    config_entry.add_to_hass(hass)

    shc_dev = _make_shc_device("shc-dev-sd-b", "SD")
    device_entry = _register_device(device_reg, config_entry, "shc-dev-sd-b")
    _install_session(hass, config_entry.entry_id, [shc_dev])

    device_id = device_entry.id

    assert await async_setup_component(
        hass,
        automation.DOMAIN,
        {
            automation.DOMAIN: [
                {
                    "trigger": {
                        "platform": "device",
                        "domain": DOMAIN,
                        "device_id": device_id,
                        "type": "ALARM",
                        "subtype": "PRIMARY_ALARM",
                    },
                    "action": {
                        "service": "test.automation",
                        "data_template": {
                            "some": "alarm - {{ trigger.event.data.event_subtype }}"
                        },
                    },
                }
            ]
        },
    )
    await hass.async_block_till_done()

    hass.bus.async_fire(
        EVENT_BOSCH_SHC,
        {
            "device_id": device_id,
            ATTR_EVENT_TYPE: "ALARM",
            ATTR_EVENT_SUBTYPE: "PRIMARY_ALARM",
        },
    )
    await hass.async_block_till_done()

    assert len(calls) == 1
    assert calls[0].data["some"] == "alarm - PRIMARY_ALARM"
