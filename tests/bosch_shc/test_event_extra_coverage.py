"""Extra coverage for event.py.

Targets:
- line 57: device_excluded for universal_switches in async_setup_entry
- line 83: device_excluded for motion_detectors / motion_detectors2
- line 104: device_excluded for smoke_detectors
"""

import asyncio
from types import SimpleNamespace

from custom_components.bosch_shc.const import (
    DATA_SESSION,
    DOMAIN,
    OPT_EXCLUDED_DEVICES,
)
from custom_components.bosch_shc.event import (
    MotionDetectorEvent,
    SmokeDetectorEvent,
    UniversalSwitchEvent,
    async_setup_entry,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_switch_device(device_id="sw-1", room_id="room-1", keystates=None):
    """Device double for a universal switch."""
    return SimpleNamespace(
        id=device_id,
        root_device_id="shc-root",
        room_id=room_id,
        name="Universal Switch",
        serial=f"serial-{device_id}",
        manufacturer="Bosch",
        device_model="SW1",
        status="AVAILABLE",
        deleted=False,
        device_services=[],
        subscribe_callback=lambda eid, cb: None,
        unsubscribe_callback=lambda eid: None,
        keystates=keystates if keystates is not None else ["KEY1"],
    )


def _make_motion_device(device_id="md-1", room_id="room-2"):
    """Device double for a motion detector."""
    return SimpleNamespace(
        id=device_id,
        root_device_id="shc-root",
        room_id=room_id,
        name="Motion Detector",
        serial=f"serial-{device_id}",
        manufacturer="Bosch",
        device_model="MD1",
        status="AVAILABLE",
        deleted=False,
        device_services=[],
        subscribe_callback=lambda eid, cb: None,
        unsubscribe_callback=lambda eid: None,
    )


def _make_smoke_detector(device_id="sd-1", room_id="room-3"):
    """Device double for a smoke detector."""
    return SimpleNamespace(
        id=device_id,
        root_device_id="shc-root",
        room_id=room_id,
        name="Smoke Detector",
        serial=f"serial-{device_id}",
        manufacturer="Bosch",
        device_model="SD1",
        status="AVAILABLE",
        deleted=False,
        device_services=[],
        subscribe_callback=lambda eid, cb: None,
        unsubscribe_callback=lambda eid: None,
    )


def _make_scenario(scenario_id="sc-1"):
    """Minimal scenario double (never filtered by device exclusion)."""
    return SimpleNamespace(id=scenario_id, name=f"Scenario-{scenario_id}")


def _make_hass_and_entry(
    universal_switches=None,
    motion_detectors=None,
    motion_detectors2=None,
    smoke_detectors=None,
    smoke_detection_system=None,
    scenarios=None,
    excluded_device_ids=None,
):
    """Return (hass, config_entry) with a faked session and options."""
    universal_switches = universal_switches or []
    motion_detectors = motion_detectors or []
    motion_detectors2 = motion_detectors2 or []
    smoke_detectors = smoke_detectors or []
    scenarios = scenarios or []
    excluded = excluded_device_ids or []

    session = SimpleNamespace(
        device_helper=SimpleNamespace(
            universal_switches=universal_switches,
            motion_detectors=motion_detectors,
            motion_detectors2=motion_detectors2,
            smoke_detectors=smoke_detectors,
            smoke_detection_system=smoke_detection_system,
        ),
        scenarios=scenarios,
    )

    entry_id = "entry-event"
    options = {OPT_EXCLUDED_DEVICES: excluded}
    hass = SimpleNamespace(
        data={DOMAIN: {entry_id: {DATA_SESSION: session}}},
    )
    config_entry = SimpleNamespace(entry_id=entry_id, options=options)
    return hass, config_entry


def _run_setup(hass, config_entry):
    """Run async_setup_entry synchronously, return the entities list."""
    added = []

    def _add(entities, update_before_add=False):
        added.extend(entities)

    asyncio.run(async_setup_entry(hass, config_entry, _add))
    return added


# ---------------------------------------------------------------------------
# Tests for line 57: device_excluded in universal_switches loop
# ---------------------------------------------------------------------------


class TestUniversalSwitchExcluded:
    def test_excluded_switch_not_added(self):
        """Excluded switch device (line 57) must not produce any UniversalSwitchEvent."""
        dev = _make_switch_device(device_id="excl-sw", keystates=["KEY1", "KEY2"])
        hass, entry = _make_hass_and_entry(
            universal_switches=[dev],
            excluded_device_ids=["excl-sw"],
        )
        added = _run_setup(hass, entry)
        sw_events = [e for e in added if isinstance(e, UniversalSwitchEvent)]
        # No events for the excluded device
        assert all(
            e._device is not dev for e in sw_events
        ), "Excluded switch should not produce UniversalSwitchEvent entities"

    def test_non_excluded_switch_produces_events(self):
        """Non-excluded switch must produce one UniversalSwitchEvent per keystate."""
        dev = _make_switch_device(device_id="keep-sw", keystates=["KEY1", "KEY2"])
        hass, entry = _make_hass_and_entry(
            universal_switches=[dev],
            excluded_device_ids=[],
        )
        added = _run_setup(hass, entry)
        sw_events = [e for e in added if isinstance(e, UniversalSwitchEvent)]
        assert len(sw_events) == 2, (
            f"Expected 2 UniversalSwitchEvent (one per keystate), got {len(sw_events)}"
        )

    def test_mixed_switches_only_excluded_is_skipped(self):
        """One excluded switch + one non-excluded: only non-excluded events appear."""
        keep = _make_switch_device(device_id="sw-keep", keystates=["K1"])
        excl = _make_switch_device(device_id="sw-excl", keystates=["K1"])
        hass, entry = _make_hass_and_entry(
            universal_switches=[keep, excl],
            excluded_device_ids=["sw-excl"],
        )
        added = _run_setup(hass, entry)
        sw_events = [e for e in added if isinstance(e, UniversalSwitchEvent)]
        assert all(e._device is not excl for e in sw_events)
        assert any(e._device is keep for e in sw_events)


# ---------------------------------------------------------------------------
# Tests for line 83: device_excluded in motion_detectors(+2) loop
# ---------------------------------------------------------------------------


class TestMotionDetectorExcluded:
    def test_excluded_motion_detector_not_added(self):
        """Excluded motion detector (line 83) must not produce a MotionDetectorEvent."""
        dev = _make_motion_device(device_id="excl-md")
        hass, entry = _make_hass_and_entry(
            motion_detectors=[dev],
            excluded_device_ids=["excl-md"],
        )
        added = _run_setup(hass, entry)
        md_events = [e for e in added if isinstance(e, MotionDetectorEvent)]
        assert all(
            e._device is not dev for e in md_events
        ), "Excluded motion detector should not produce MotionDetectorEvent"

    def test_non_excluded_motion_detector_is_added(self):
        """Non-excluded motion detector must produce a MotionDetectorEvent."""
        dev = _make_motion_device(device_id="keep-md")
        hass, entry = _make_hass_and_entry(
            motion_detectors=[dev],
            excluded_device_ids=[],
        )
        added = _run_setup(hass, entry)
        md_events = [e for e in added if isinstance(e, MotionDetectorEvent)]
        assert any(
            e._device is dev for e in md_events
        ), "Non-excluded motion detector should produce a MotionDetectorEvent"

    def test_excluded_motion_detector2_not_added(self):
        """Excluded MD2 (also covered by line 83 via combined list) must be skipped."""
        dev = _make_motion_device(device_id="excl-md2")
        hass, entry = _make_hass_and_entry(
            motion_detectors2=[dev],
            excluded_device_ids=["excl-md2"],
        )
        added = _run_setup(hass, entry)
        md_events = [e for e in added if isinstance(e, MotionDetectorEvent)]
        assert all(
            e._device is not dev for e in md_events
        ), "Excluded MD2 should not produce a MotionDetectorEvent"

    def test_non_excluded_motion_detector2_is_added(self):
        """Non-excluded MD2 must produce a MotionDetectorEvent."""
        dev = _make_motion_device(device_id="keep-md2")
        hass, entry = _make_hass_and_entry(
            motion_detectors2=[dev],
            excluded_device_ids=[],
        )
        added = _run_setup(hass, entry)
        md_events = [e for e in added if isinstance(e, MotionDetectorEvent)]
        assert any(
            e._device is dev for e in md_events
        ), "Non-excluded MD2 should produce a MotionDetectorEvent"

    def test_mixed_motion_detectors_only_excluded_skipped(self):
        """One excluded + one non-excluded MD: only non-excluded appears."""
        keep = _make_motion_device(device_id="md-keep")
        excl = _make_motion_device(device_id="md-excl")
        hass, entry = _make_hass_and_entry(
            motion_detectors=[keep, excl],
            excluded_device_ids=["md-excl"],
        )
        added = _run_setup(hass, entry)
        md_events = [e for e in added if isinstance(e, MotionDetectorEvent)]
        assert all(e._device is not excl for e in md_events)
        assert any(e._device is keep for e in md_events)


# ---------------------------------------------------------------------------
# Tests for line 104: device_excluded in smoke_detectors loop
# ---------------------------------------------------------------------------


class TestSmokeDetectorExcluded:
    def test_excluded_smoke_detector_not_added(self):
        """Excluded smoke detector (line 104) must not produce a SmokeDetectorEvent."""
        dev = _make_smoke_detector(device_id="excl-sd")
        hass, entry = _make_hass_and_entry(
            smoke_detectors=[dev],
            excluded_device_ids=["excl-sd"],
        )
        added = _run_setup(hass, entry)
        sd_events = [e for e in added if isinstance(e, SmokeDetectorEvent)]
        assert all(
            e._device is not dev for e in sd_events
        ), "Excluded smoke detector should not produce SmokeDetectorEvent"

    def test_non_excluded_smoke_detector_is_added(self):
        """Non-excluded smoke detector must produce a SmokeDetectorEvent."""
        dev = _make_smoke_detector(device_id="keep-sd")
        hass, entry = _make_hass_and_entry(
            smoke_detectors=[dev],
            excluded_device_ids=[],
        )
        added = _run_setup(hass, entry)
        sd_events = [e for e in added if isinstance(e, SmokeDetectorEvent)]
        assert any(
            e._device is dev for e in sd_events
        ), "Non-excluded smoke detector should produce a SmokeDetectorEvent"

    def test_mixed_smoke_detectors_only_excluded_skipped(self):
        """One excluded + one non-excluded SD: only non-excluded appears."""
        keep = _make_smoke_detector(device_id="sd-keep")
        excl = _make_smoke_detector(device_id="sd-excl")
        hass, entry = _make_hass_and_entry(
            smoke_detectors=[keep, excl],
            excluded_device_ids=["sd-excl"],
        )
        added = _run_setup(hass, entry)
        sd_events = [e for e in added if isinstance(e, SmokeDetectorEvent)]
        assert all(e._device is not excl for e in sd_events)
        assert any(e._device is keep for e in sd_events)

    def test_excluded_smoke_detector_alongside_non_excluded(self):
        """Regression: excluding one SD must not affect the other in the same list."""
        keep1 = _make_smoke_detector(device_id="sd-a")
        keep2 = _make_smoke_detector(device_id="sd-b")
        excl = _make_smoke_detector(device_id="sd-excl")
        hass, entry = _make_hass_and_entry(
            smoke_detectors=[keep1, excl, keep2],
            excluded_device_ids=["sd-excl"],
        )
        added = _run_setup(hass, entry)
        sd_events = [e for e in added if isinstance(e, SmokeDetectorEvent)]
        device_ids = {e._device.id for e in sd_events}
        assert "sd-a" in device_ids
        assert "sd-b" in device_ids
        assert "sd-excl" not in device_ids
