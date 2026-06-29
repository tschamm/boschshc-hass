"""Coverage tests for select.py missing lines.

Targets:
  Line 58  — `if not hasattr(device, "motion_sensitivity"): continue`
             Device in motion_detectors2 without motion_sensitivity attr is skipped.
  Lines 64-65 — `_ = device.motion_sensitivity` raises AttributeError → continue
               Device has the attr but accessing it raises AttributeError.
  Line 75  — `if not isinstance(device, SHCShutterContact2Plus): continue`
             Plain SHCShutterContact2 (not Plus) is skipped from vibration select.

These lines are not hit by test_select_unit.py which already tests the happy path
and the plain-SC2 skip.  This file uses a focused session-level setup to drive
the specific branches.

Pattern: asyncio.run(async_setup_entry(hass, entry, add)) with targeted fakes.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from boschshcpy import SHCShutterContact2Plus
from boschshcpy.services_impl import (
    PirSensorConfigurationService,
    VibrationSensorService,
)

from custom_components.bosch_shc.const import (
    DATA_SESSION,
    DOMAIN,
    OPT_EXCLUDED_DEVICES,
)
from custom_components.bosch_shc.select import (
    MotionSensitivitySelect,
    VibrationSensitivitySelect,
    async_setup_entry,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_hass(session):
    return SimpleNamespace(data={DOMAIN: {"E1": {DATA_SESSION: session}}})


def _make_entry(options=None, entry_id="E1"):
    return SimpleNamespace(options=options or {}, entry_id=entry_id)


def _run_setup(session, entry):
    hass = _make_hass(session)
    collected = []

    def add(entities):
        collected.extend(entities)

    asyncio.run(async_setup_entry(hass, entry, add))
    return collected


def _make_session(motion_detectors2=None, shutter_contacts2=None):
    return SimpleNamespace(
        device_helper=SimpleNamespace(
            motion_detectors2=motion_detectors2 or [],
            shutter_contacts2=shutter_contacts2 or [],
        )
    )


def _good_md2_device(dev_id="md2-001"):
    """Motion detector 2 that successfully exposes motion_sensitivity."""
    return SimpleNamespace(
        name="MD2",
        id=dev_id,
        root_device_id="root-md2",
        motion_sensitivity=PirSensorConfigurationService.MotionSensitivity.HIGH,
    )


def _make_fake_sc2plus(dev_id="sc2p-001", root_id="root-sc2p"):
    """SHCShutterContact2Plus subclass that passes isinstance() and is attribute-safe."""
    class _FakePlus(SHCShutterContact2Plus):
        name = None
        id = None
        root_device_id = None
        serial = None
        sensitivity = VibrationSensorService.SensitivityState.HIGH

        def __init__(self, _id, _root):
            self.name = "SC2+"
            self.id = _id
            self.root_device_id = _root
            self.serial = "SER_PLUS"

    return _FakePlus(dev_id, root_id)


# ---------------------------------------------------------------------------
# Line 58 — device without motion_sensitivity attr is skipped
# ---------------------------------------------------------------------------

class TestMotionDetectorNoAttr:
    def test_device_without_attr_skipped(self):
        """Device in motion_detectors2 with no motion_sensitivity → skipped (line 58)."""
        dev = SimpleNamespace(
            name="MD2 no-pir",
            id="md2-no-attr",
            root_device_id="root-no-attr",
            # no motion_sensitivity attribute at all
        )
        session = _make_session(motion_detectors2=[dev])
        result = _run_setup(session, _make_entry())
        assert result == []

    def test_device_with_attr_added(self):
        """Sanity: device WITH motion_sensitivity attr → MotionSensitivitySelect created."""
        dev = _good_md2_device()
        session = _make_session(motion_detectors2=[dev])
        result = _run_setup(session, _make_entry())
        assert len(result) == 1
        assert isinstance(result[0], MotionSensitivitySelect)

    def test_mixed_one_without_attr_one_with(self):
        """Only device with the attr produces an entity; the other is silently skipped."""
        no_attr = SimpleNamespace(
            name="MD2-no", id="md2-no", root_device_id="root-no"
        )
        with_attr = _good_md2_device(dev_id="md2-ok")
        session = _make_session(motion_detectors2=[no_attr, with_attr])
        result = _run_setup(session, _make_entry())
        assert len(result) == 1
        assert result[0]._device is with_attr


# ---------------------------------------------------------------------------
# Lines 64-65 — device has motion_sensitivity attr but accessing it raises
#               AttributeError → continue
# ---------------------------------------------------------------------------

class TestMotionDetectorAttrRaisesAttributeError:
    def test_attr_raises_attribute_error_skipped(self):
        """Hasattr passes but accessing device.motion_sensitivity raises AttributeError
        (e.g. the getter calls an internal service that is absent).
        The device must be silently skipped (lines 64-65).
        """
        class _BadAttrDevice:
            name = "MD2 bad-getter"
            id = "md2-bad"
            root_device_id = "root-bad"

            @property
            def motion_sensitivity(self):
                raise AttributeError("PirSensor service not registered")

        dev = _BadAttrDevice()
        session = _make_session(motion_detectors2=[dev])
        result = _run_setup(session, _make_entry())
        assert result == []

    def test_attr_raises_good_device_still_added(self):
        """When one device raises AttributeError and another is fine, only the good
        device produces an entity.
        """
        class _BadAttrDevice:
            name = "MD2-bad"
            id = "md2-bad2"
            root_device_id = "root-bad2"

            @property
            def motion_sensitivity(self):
                raise AttributeError("service absent")

        good = _good_md2_device(dev_id="md2-good2")
        session = _make_session(motion_detectors2=[_BadAttrDevice(), good])
        result = _run_setup(session, _make_entry())
        assert len(result) == 1
        assert isinstance(result[0], MotionSensitivitySelect)

    def test_attr_error_device_does_not_raise(self):
        """Accessing a broken motion_sensitivity must not propagate the exception."""
        class _Breaks:
            name = "MD2-break"
            id = "md2-break"
            root_device_id = "root-break"

            @property
            def motion_sensitivity(self):
                raise AttributeError("internal fail")

        session = _make_session(motion_detectors2=[_Breaks()])
        # Should complete without raising
        result = _run_setup(session, _make_entry())
        assert result == []


# ---------------------------------------------------------------------------
# Line 75 — non-SHCShutterContact2Plus device in shutter_contacts2 is skipped
# ---------------------------------------------------------------------------

class TestShutterContact2NotPlus:
    def test_plain_sc2_skipped(self):
        """A device that is NOT an instance of SHCShutterContact2Plus → skipped (line 75)."""
        plain = SimpleNamespace(
            name="SC2 plain",
            id="sc2-plain",
            root_device_id="root-plain",
        )
        session = _make_session(shutter_contacts2=[plain])
        result = _run_setup(session, _make_entry())
        assert result == []

    def test_sc2plus_added(self):
        """A real SHCShutterContact2Plus subclass passes isinstance → entity added."""
        dev = _make_fake_sc2plus()
        session = _make_session(shutter_contacts2=[dev])
        result = _run_setup(session, _make_entry())
        assert len(result) == 1
        assert isinstance(result[0], VibrationSensitivitySelect)

    def test_mixed_plain_and_plus(self):
        """Plain SC2 is skipped; SC2Plus produces VibrationSensitivitySelect."""
        plain = SimpleNamespace(
            name="SC2", id="sc2-plain2", root_device_id="root-plain2"
        )
        plus = _make_fake_sc2plus(dev_id="sc2p-002", root_id="root-sc2p-2")
        session = _make_session(shutter_contacts2=[plain, plus])
        result = _run_setup(session, _make_entry())
        assert len(result) == 1
        assert isinstance(result[0], VibrationSensitivitySelect)

    def test_multiple_plain_all_skipped(self):
        plain1 = SimpleNamespace(name="SC2-1", id="sc2-1", root_device_id="r1")
        plain2 = SimpleNamespace(name="SC2-2", id="sc2-2", root_device_id="r2")
        session = _make_session(shutter_contacts2=[plain1, plain2])
        result = _run_setup(session, _make_entry())
        assert result == []

    def test_excluded_sc2plus_skipped_before_isinstance_check(self):
        """An excluded SC2Plus device is filtered out at line 74 (device_excluded)
        before reaching the isinstance check on line 75/76.
        """
        dev = _make_fake_sc2plus(dev_id="sc2p-excl")
        session = _make_session(shutter_contacts2=[dev])
        entry = _make_entry(options={OPT_EXCLUDED_DEVICES: ["sc2p-excl"]})
        result = _run_setup(session, entry)
        assert result == []
