"""Isolation-safe unit tests for button.py (SHCRelayButton) — extended coverage.

test_button.py already covers the bypass-__new__ style.
This file adds tests that exercise __init__ via _update_attr-patching so that
the super().__init__ path (lines 68-72 in button.py) is covered.

Pattern: patch SHCRelayButton._update_attr so SHCEntity.__init__ can run on a
fake device (no hass / no registry needed).  SHCEntity.__init__ only calls
self._update_attr() after setting attributes — patching it prevents any
further platform-specific calls.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from custom_components.bosch_shc.button import SHCRelayButton
from custom_components.bosch_shc.entity import SHCEntity
from homeassistant.components.button import ButtonEntity


# ---------------------------------------------------------------------------
# Fake device — minimal attributes SHCEntity.__init__ reads
# ---------------------------------------------------------------------------

def _fake_device(
    name: str = "Relay 1",
    device_id: str = "hdm:HomeMaticIP:abc123",
    root_device_id: str = "aa:bb:cc:00:00:01",
) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        id=device_id,
        root_device_id=root_device_id,
        device_services=[],
        deleted=False,
        manufacturer="Bosch",
        device_model="MR",
        status="AVAILABLE",
    )


def _relay_via_init(
    name: str = "Relay 1",
    device_id: str = "hdm:HomeMaticIP:abc123",
    root_device_id: str = "aa:bb:cc:00:00:01",
    attr_name: str | None = None,
) -> SHCRelayButton:
    """Create SHCRelayButton by calling __init__ (exercises lines 68-72).

    _update_attr is patched to a no-op so SHCEntity.__init__ completes
    without a real HA instance.
    """
    dev = _fake_device(
        name=name, device_id=device_id, root_device_id=root_device_id
    )
    btn = SHCRelayButton.__new__(SHCRelayButton)
    with patch.object(SHCRelayButton, "_update_attr", lambda self: None):
        SHCRelayButton.__init__(btn, dev, "entry_test", attr_name)
    return btn


# ---------------------------------------------------------------------------
# Tests: __init__ via real call (covers super().__init__ path)
# ---------------------------------------------------------------------------

class TestSHCRelayButtonInit:
    """Exercise the actual __init__ so super().__init__ lines are covered."""

    def test_name_without_attr_name(self):
        # With _attr_has_entity_name=True and _attr_name=None, HA uses the device
        # name as the entity name (primary entity).  _attr_name itself is None.
        btn = _relay_via_init(name="Keller Relay", attr_name=None)
        assert btn._attr_name is None

    def test_name_with_attr_name(self):
        # _attr_name stores only the feature label; HA prepends device name for
        # display (e.g. "Garage CH1").
        btn = _relay_via_init(name="Garage", attr_name="CH1")
        assert btn._attr_name == "CH1"

    def test_unique_id_without_attr_name(self):
        btn = _relay_via_init(
            root_device_id="aa:bb:cc:00:00:01",
            device_id="hdm:HomeMaticIP:abc123",
            attr_name=None,
        )
        assert btn._attr_unique_id == "aa:bb:cc:00:00:01_hdm:HomeMaticIP:abc123"

    def test_unique_id_with_attr_name_lowercased(self):
        btn = _relay_via_init(
            root_device_id="aa:bb:cc:00:00:02",
            device_id="hdm:HomeMaticIP:xyz789",
            attr_name="Channel A",
        )
        assert btn._attr_unique_id == "aa:bb:cc:00:00:02_hdm:HomeMaticIP:xyz789_channel a"

    def test_unique_id_attr_name_already_lowercase(self):
        btn = _relay_via_init(
            root_device_id="root1",
            device_id="dev1",
            attr_name="impulse",
        )
        assert btn._attr_unique_id == "root1_dev1_impulse"

    def test_device_stored(self):
        btn = _relay_via_init(name="Switch A")
        assert btn._device.name == "Switch A"

    def test_entry_id_stored(self):
        btn = _relay_via_init()
        assert btn._entry_id == "entry_test"

    def test_name_attr_name_none_equals_device_name(self):
        # _attr_name=None → primary entity; HA resolves to device name for display.
        btn = _relay_via_init(name="Single Relay", attr_name=None)
        assert btn._attr_name is None

    def test_name_attr_name_provided_appended(self):
        # _attr_name holds only the feature label (no device prefix).
        btn = _relay_via_init(name="Multi Relay", attr_name="Output 2")
        assert btn._attr_name == "Output 2"

    def test_unique_id_attr_name_uppercased_is_lowercased(self):
        btn = _relay_via_init(
            root_device_id="r1", device_id="d1", attr_name="OUTPUT"
        )
        assert btn._attr_unique_id == "r1_d1_output"


# ---------------------------------------------------------------------------
# Tests: press() delegates to device (via both factory styles)
# ---------------------------------------------------------------------------

class TestPress:
    def test_press_calls_trigger_impulse_state_via_init(self):
        btn = _relay_via_init()
        triggered = []
        btn._device.trigger_impulse_state = lambda: triggered.append(True)
        btn.press()
        assert triggered == [True]

    def test_press_called_twice_triggers_twice_via_init(self):
        btn = _relay_via_init()
        count = []
        btn._device.trigger_impulse_state = lambda: count.append(1)
        btn.press()
        btn.press()
        assert len(count) == 2

    def test_press_returns_none(self):
        btn = _relay_via_init()
        btn._device.trigger_impulse_state = lambda: None
        assert btn.press() is None


# ---------------------------------------------------------------------------
# Tests: structural / class-level properties
# ---------------------------------------------------------------------------

class TestStructural:
    def test_is_button_entity(self):
        assert issubclass(SHCRelayButton, ButtonEntity)

    def test_is_shc_entity(self):
        assert issubclass(SHCRelayButton, SHCEntity)

    def test_no_device_class_at_class_level(self):
        assert not hasattr(SHCRelayButton, "_attr_device_class") or (
            SHCRelayButton.__dict__.get("_attr_device_class") is None
        )

    def test_mro_shcrelaybutton_before_buttonentity(self):
        """SHCRelayButton must appear before ButtonEntity in the MRO."""
        mro = SHCRelayButton.__mro__
        idx_self = mro.index(SHCRelayButton)
        idx_btn = mro.index(ButtonEntity)
        assert idx_self < idx_btn

    def test_mro_shcentity_in_chain(self):
        assert SHCEntity in SHCRelayButton.__mro__
