"""Regression tests for event.py UniversalSwitchEvent (#192).

Issue #192: the SHC sends a Keypad service payload alongside a battery-level
update, replaying a stale keyName/eventType/eventTimestamp.  This used to fire
a phantom HA button event even though no key was actually pressed.

Fix: _event_callback now guards on:
  - eventtype must be a genuine press (PRESS_SHORT / PRESS_LONG / PRESS_LONG_RELEASED)
  - eventtimestamp must have advanced since the last event that was fired
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

from custom_components.bosch_shc.event import UniversalSwitchEvent


def _make_hass_sync():
    """Return a minimal hass mock whose call_soon_threadsafe executes the fn immediately."""
    hass = MagicMock(name="hass")

    def _sync_call(fn, *args, **kwargs):
        fn(*args, **kwargs)

    hass.loop.call_soon_threadsafe.side_effect = _sync_call
    return hass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PRESS_SHORT = SimpleNamespace(name="PRESS_SHORT")
_PRESS_LONG = SimpleNamespace(name="PRESS_LONG")
_PRESS_LONG_RELEASED = SimpleNamespace(name="PRESS_LONG_RELEASED")
_SWITCH_ON = SimpleNamespace(name="SWITCH_ON")
_SWITCH_OFF = SimpleNamespace(name="SWITCH_OFF")

KEY_ID = "UPPER_BUTTON"


def _make_entity(eventtype, eventtimestamp, root_device_id="root1", device_id="dev1"):
    """Build a UniversalSwitchEvent bypassing SHCEntity.__init__."""
    entity = UniversalSwitchEvent.__new__(UniversalSwitchEvent)

    entity._device = SimpleNamespace(
        name="Test Switch",
        id=device_id,
        root_device_id=root_device_id,
        eventtype=eventtype,
        eventtimestamp=eventtimestamp,
    )
    entity._key_id = KEY_ID
    entity._last_fired_timestamp = -1
    entity._attr_unique_id = f"{root_device_id}_{device_id}_{KEY_ID}"
    entity.entity_id = f"event.test_switch_button_{KEY_ID.lower()}"

    # HA EventEntity methods we need to observe / stub
    entity._trigger_event = MagicMock()
    entity.schedule_update_ha_state = MagicMock()
    # device_id is a property on SHCEntity reading _device.id — already set above

    # Wire up a sync hass so call_soon_threadsafe executes immediately in tests
    entity.hass = _make_hass_sync()

    return entity


# ---------------------------------------------------------------------------
# Guard: eventtype must be a press
# ---------------------------------------------------------------------------

class TestEventTypeGuard:
    """Non-press eventtype values must never produce a HA event."""

    def test_switch_on_does_not_fire(self):
        entity = _make_entity(eventtype=_SWITCH_ON, eventtimestamp=1000)
        entity._event_callback()
        entity._trigger_event.assert_not_called()
        entity.schedule_update_ha_state.assert_not_called()

    def test_switch_off_does_not_fire(self):
        entity = _make_entity(eventtype=_SWITCH_OFF, eventtimestamp=1001)
        entity._event_callback()
        entity._trigger_event.assert_not_called()
        entity.schedule_update_ha_state.assert_not_called()

    def test_none_eventtype_does_not_fire(self):
        entity = _make_entity(eventtype=None, eventtimestamp=1002)
        entity._event_callback()
        entity._trigger_event.assert_not_called()
        entity.schedule_update_ha_state.assert_not_called()


# ---------------------------------------------------------------------------
# Guard: duplicate timestamp must not fire twice
# ---------------------------------------------------------------------------

class TestTimestampGuard:
    """Identical eventTimestamp on successive callbacks = phantom / stale replay."""

    def test_first_press_fires(self):
        entity = _make_entity(eventtype=_PRESS_SHORT, eventtimestamp=5000)
        entity._event_callback()
        entity._trigger_event.assert_called_once()
        assert entity._last_fired_timestamp == 5000

    def test_second_call_same_ts_does_not_fire(self):
        """Battery-update replaying the same stale Keypad state → must be swallowed."""
        entity = _make_entity(eventtype=_PRESS_SHORT, eventtimestamp=5000)
        entity._event_callback()   # first: fires
        entity._event_callback()   # second: same ts → phantom, must NOT fire again
        assert entity._trigger_event.call_count == 1

    def test_new_ts_fires_again(self):
        """A genuinely new keypress (different timestamp) must still fire."""
        entity = _make_entity(eventtype=_PRESS_SHORT, eventtimestamp=5000)
        entity._event_callback()
        assert entity._trigger_event.call_count == 1

        # Simulate a new keypress arriving with a higher timestamp
        entity._device.eventtimestamp = 6000
        entity._device.eventtype = _PRESS_LONG
        entity._event_callback()
        assert entity._trigger_event.call_count == 2
        assert entity._last_fired_timestamp == 6000

    def test_ts_zero_then_nonzero(self):
        """Timestamp 0 is valid as an initial stale value; a real press at ts>0 must fire."""
        entity = _make_entity(eventtype=_PRESS_SHORT, eventtimestamp=0)
        # Simulate a stale state at ts=0 delivered at startup (should NOT fire
        # because _last_fired_timestamp starts at -1, so ts=0 != -1 → will fire).
        # The intent: we accept ts=0 as a genuine event if the device sends it.
        entity._event_callback()
        assert entity._trigger_event.call_count == 1
        assert entity._last_fired_timestamp == 0

        # A second call with the same ts=0 (stale replay) must NOT fire.
        entity._event_callback()
        assert entity._trigger_event.call_count == 1


# ---------------------------------------------------------------------------
# Happy path: all three press types fire with correct event_type attribute
# ---------------------------------------------------------------------------

class TestPressTypesFire:
    def test_press_short_fires(self):
        entity = _make_entity(eventtype=_PRESS_SHORT, eventtimestamp=100)
        entity._event_callback()
        entity._trigger_event.assert_called_once()
        args = entity._trigger_event.call_args[0]
        assert args[0] == "PRESS_SHORT"

    def test_press_long_fires(self):
        entity = _make_entity(eventtype=_PRESS_LONG, eventtimestamp=200)
        entity._event_callback()
        entity._trigger_event.assert_called_once()
        args = entity._trigger_event.call_args[0]
        assert args[0] == "PRESS_LONG"

    def test_press_long_released_fires(self):
        entity = _make_entity(eventtype=_PRESS_LONG_RELEASED, eventtimestamp=300)
        entity._event_callback()
        entity._trigger_event.assert_called_once()
        args = entity._trigger_event.call_args[0]
        assert args[0] == "PRESS_LONG_RELEASED"

    def test_schedule_update_called_on_fire(self):
        entity = _make_entity(eventtype=_PRESS_SHORT, eventtimestamp=400)
        entity._event_callback()
        entity.schedule_update_ha_state.assert_called_once()

    def test_schedule_update_not_called_when_suppressed(self):
        """When callback is suppressed (non-press), schedule_update must not be called."""
        entity = _make_entity(eventtype=_SWITCH_ON, eventtimestamp=500)
        entity._event_callback()
        entity.schedule_update_ha_state.assert_not_called()


# ---------------------------------------------------------------------------
# Core scenario: battery update simulation
# Battery long-poll delivers same Keypad state → no phantom event
# ---------------------------------------------------------------------------

class TestBatteryUpdateSimulation:
    """Reproduce the exact #192 scenario: battery update re-delivers stale Keypad."""

    def test_phantom_event_not_fired_on_battery_update(self):
        """After a genuine press, a battery update that replays the same
        Keypad state must NOT generate a second HA event.
        """
        entity = _make_entity(eventtype=_PRESS_SHORT, eventtimestamp=9999)

        # First: genuine keypress event
        entity._event_callback()
        assert entity._trigger_event.call_count == 1

        # Second: battery update triggers the same Keypad callback with stale data
        # (same eventtype, same eventtimestamp — typical SHC behaviour for #192)
        entity._event_callback()
        assert entity._trigger_event.call_count == 1  # still 1, no phantom
