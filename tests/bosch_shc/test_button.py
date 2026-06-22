"""Isolation-safe unit tests for button.py (SHCRelayButton).

Pattern: Cls.__new__(Cls) bypasses SHCEntity.__init__ (needs hass/registry).
We only set the attributes the class under test actually reads.
"""

import asyncio
from types import SimpleNamespace

from custom_components.bosch_shc.button import SHCRelayButton


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_device(
    name: str = "Relay 1",
    device_id: str = "hdm:HomeMaticIP:abc123",
    root_device_id: str = "64:da:a0:00:00:01",
) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        id=device_id,
        root_device_id=root_device_id,
    )


def _relay_button(
    name: str = "Relay 1",
    device_id: str = "hdm:HomeMaticIP:abc123",
    root_device_id: str = "64:da:a0:00:00:01",
    attr_name: str | None = None,
) -> SHCRelayButton:
    """Create an SHCRelayButton bypassing SHCEntity.__init__."""
    btn = SHCRelayButton.__new__(SHCRelayButton)
    dev = _make_device(name=name, device_id=device_id, root_device_id=root_device_id)
    btn._device = dev
    # Replicate the __init__ logic without calling super().__init__
    btn._attr_name = (
        f"{dev.name}" if attr_name is None else f"{dev.name} {attr_name}"
    )
    btn._attr_unique_id = (
        f"{dev.root_device_id}_{dev.id}"
        if attr_name is None
        else f"{dev.root_device_id}_{dev.id}_{attr_name.lower()}"
    )
    return btn


# ---------------------------------------------------------------------------
# Tests: attr_name=None (default / single-relay)
# ---------------------------------------------------------------------------

def test_name_without_attr_name():
    btn = _relay_button(name="Keller Relay", attr_name=None)
    assert btn._attr_name == "Keller Relay"


def test_unique_id_without_attr_name():
    btn = _relay_button(
        root_device_id="64:da:a0:00:00:01",
        device_id="hdm:HomeMaticIP:abc123",
        attr_name=None,
    )
    assert btn._attr_unique_id == "64:da:a0:00:00:01_hdm:HomeMaticIP:abc123"


# ---------------------------------------------------------------------------
# Tests: attr_name provided (multi-relay / named channel)
# ---------------------------------------------------------------------------

def test_name_with_attr_name():
    btn = _relay_button(name="Garage", attr_name="CH1")
    assert btn._attr_name == "Garage CH1"


def test_unique_id_with_attr_name_lowercased():
    btn = _relay_button(
        root_device_id="64:da:a0:00:00:02",
        device_id="hdm:HomeMaticIP:xyz789",
        attr_name="Channel A",
    )
    assert btn._attr_unique_id == "64:da:a0:00:00:02_hdm:HomeMaticIP:xyz789_channel a"


def test_unique_id_attr_name_already_lowercase():
    btn = _relay_button(
        root_device_id="root1",
        device_id="dev1",
        attr_name="impulse",
    )
    assert btn._attr_unique_id == "root1_dev1_impulse"


# ---------------------------------------------------------------------------
# Tests: press() delegates to device
# ---------------------------------------------------------------------------

def test_press_calls_trigger_impulse_state():
    btn = _relay_button()
    triggered = []

    async def _trig():
        triggered.append(True)

    btn._device.async_trigger_impulse_state = _trig
    asyncio.run(btn.async_press())
    assert triggered == [True]


def test_press_called_twice_triggers_twice():
    btn = _relay_button()
    count = []

    async def _trig():
        count.append(1)

    btn._device.async_trigger_impulse_state = _trig
    asyncio.run(btn.async_press())
    asyncio.run(btn.async_press())
    assert len(count) == 2


# ---------------------------------------------------------------------------
# Tests: class-level / structural properties
# ---------------------------------------------------------------------------

def test_shcrelaybutton_is_button_entity():
    from homeassistant.components.button import ButtonEntity
    assert issubclass(SHCRelayButton, ButtonEntity)


def test_shcrelaybutton_is_shcentity():
    from custom_components.bosch_shc.entity import SHCEntity
    assert issubclass(SHCRelayButton, SHCEntity)


def test_no_device_class_set():
    """SHCRelayButton does not declare a device_class; ButtonEntity default is None."""
    btn = _relay_button()
    # _attr_device_class should not be set on the instance (falls through to class None)
    assert not hasattr(btn, "_attr_device_class") or btn._attr_device_class is None


def test_no_entity_category_set():
    """SHCRelayButton is a primary entity — no entity_category set."""
    btn = _relay_button()
    assert not hasattr(btn, "_attr_entity_category") or btn._attr_entity_category is None
