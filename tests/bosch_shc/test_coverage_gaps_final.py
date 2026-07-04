"""Final targeted tests for remaining coverage gaps.

Covers:
  binary_sensor.py:187  - _cleanup_tracker() body: tracker.teardown() called
  select.py:64-65       - except AttributeError: continue when motion_sensitivity
                          accessor succeeds for hasattr() but raises on explicit call
                          (unstable property — passes first access, fails second)

Pattern: __new__ bypass + SimpleNamespace / asyncio.run where needed.
No HA harness.

Run:
  PYTHONPATH="/tmp/hass-cov:/tmp/lib-async" PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \\
  python3 -m pytest tests/bosch_shc/test_coverage_gaps_final.py -q -o addopts=""
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch


def _run(coro):
    return asyncio.run(coro)


# ===========================================================================
# binary_sensor.py line 187 — _cleanup_tracker() body: tracker.teardown()
# ===========================================================================

class TestBinarySensorCleanupTrackerBody:
    """Call async_setup_entry with a twinguard + SDS, capture _cleanup_tracker,
    invoke it, and verify tracker.teardown() is called (line 187).
    """

    def _make_hass(self):
        async def _async_add_executor_job(fn, *args):
            return fn(*args)

        return SimpleNamespace(
            bus=SimpleNamespace(
                async_listen_once=lambda event, cb: (lambda: None),
                fire=lambda *a, **kw: None,
            ),
            loop=SimpleNamespace(call_soon_threadsafe=lambda cb, *a: cb(*a)),
            data={},
            async_add_executor_job=_async_add_executor_job,
        )

    def _fake_device(self, device_id, name="FakeDev", root_id="root1"):
        return SimpleNamespace(
            id=device_id,
            name=name,
            root_device_id=root_id,
            serial=f"{device_id}-ser",
            device_services=[],
            supports_batterylevel=False,
            manufacturer="Bosch",
            device_model="FakeModel",
            deleted=False,
            status="AVAILABLE",
            subscribe_callback=lambda key, cb: None,
            unsubscribe_callback=lambda key: None,
        )

    def test_cleanup_tracker_teardown_called(self):
        """Line 187: _cleanup_tracker() must call tracker.teardown().

        Strategy:
        1. Build a session with one SDS + one twinguard.
        2. Patch TwinguardAlarmTracker to return a controllable mock (tracker_mock).
        3. Capture all closures registered via config_entry.async_on_unload.
        4. Call async_setup_entry.
        5. Find the _cleanup_tracker closure among the unload callbacks.
        6. Call it — this exercises line 187.
        7. Assert tracker.teardown() was called.
        """
        from custom_components.bosch_shc.binary_sensor import async_setup_entry

        tracker_mock = MagicMock()
        tracker_mock.teardown = MagicMock()
        tracker_mock.async_refresh = AsyncMock()

        sds = self._fake_device("sds-001", name="SDS")
        # SDS needs a SurveillanceAlarm alarm attribute (read by SmokeDetectionSystemSensor)
        sds.alarm = MagicMock()
        sds.subscribe_callback = MagicMock()

        tw = self._fake_device("tw-001", name="Twinguard")
        tw.subscribe_callback = MagicMock()

        session = SimpleNamespace(
            _subscribers=[],
            subscribe=lambda cb: None,
            api=SimpleNamespace(get_messages=AsyncMock(return_value=[])),
            device_helper=SimpleNamespace(
                shutter_contacts=[],
                shutter_contacts2=[],
                motion_detectors=[],
                motion_detectors2=[],
                smoke_detectors=[],
                smoke_detection_system=sds,
                water_leakage_detectors=[],
                thermostats=[],
                twinguards=[tw],
                universal_switches=[],
                wallthermostats=[],
                roomthermostats=[],
                climate_controls=[],
            ),
        )

        hass = self._make_hass()

        captured_unloads = []
        config_entry = SimpleNamespace(
            options={},
            entry_id="E1",
            async_on_unload=lambda fn: captured_unloads.append(fn),
            runtime_data=SimpleNamespace(session=session),
        )

        platform_mock = MagicMock()
        platform_mock.async_register_entity_service = MagicMock()

        async def _run_setup():
            with (
                patch(
                    "custom_components.bosch_shc.binary_sensor."
                    "async_migrate_to_new_unique_id",
                    return_value=None,
                ),
                patch(
                    "custom_components.bosch_shc.binary_sensor."
                    "entity_platform.current_platform",
                ) as _cp,
                patch(
                    "custom_components.bosch_shc.binary_sensor.TwinguardAlarmTracker",
                    return_value=tracker_mock,
                ),
            ):
                _cp.get.return_value = platform_mock
                await async_setup_entry(hass, config_entry, lambda e, **kw: None)

        _run(_run_setup())

        # With a twinguard + SDS, binary_sensor.async_setup_entry registers
        # multiple unload callbacks. The _cleanup_tracker closure (line 187) is
        # one of them.  Find it by calling each callable one at a time until
        # tracker.teardown is called — this avoids index fragility if earlier
        # callbacks are added/removed.
        assert len(captured_unloads) >= 2, (
            "Expected at least 2 unload closures (cleanup_tracker + listen_once)"
        )

        # Call each registered callback; _cleanup_tracker calls tracker.teardown().
        for fn in captured_unloads:
            assert callable(fn), f"Unload callback {fn!r} must be callable"
            fn()

        # Line 187: tracker.teardown() must have been called by _cleanup_tracker
        assert tracker_mock.teardown.call_count >= 1, (
            "tracker.teardown() was not called via any unload closure"
        )


# ===========================================================================
# select.py lines 64-65 — except AttributeError: continue
# (unstable property: hasattr passes, explicit access raises)
# ===========================================================================

class TestSelectMotionSensitivityUnstableProperty:
    """Cover select.py lines 64-65: the try/except AttributeError for
    motion_sensitivity when the property is unstable — succeeds on the first
    access (hasattr) but fails on the explicit probe at line 63.

    This requires a property whose first call returns a value (so hasattr
    returns True) but whose second call raises AttributeError.
    """

    def _run_setup(self, session):
        from custom_components.bosch_shc.select import async_setup_entry

        hass = SimpleNamespace(data={})
        config_entry = SimpleNamespace(
            entry_id="E1",
            options={},
            runtime_data=SimpleNamespace(session=session),
        )
        collected = []

        _run(async_setup_entry(hass, config_entry, lambda e: collected.extend(e)))
        return collected

    def test_unstable_motion_sensitivity_property_skipped(self):
        """Device where motion_sensitivity raises on second access is skipped
        (lines 64-65).
        """
        class _UnstableDevice:
            id = "md2-unstable"
            name = "MD2 Unstable"
            root_device_id = "root-unstable"

            def __init__(self):
                self._call_count = 0

            @property
            def motion_sensitivity(self):
                self._call_count += 1
                if self._call_count == 1:
                    # First call from hasattr() — succeeds
                    return "HIGH"
                # Second call from explicit probe `_ = device.motion_sensitivity`
                # — raises AttributeError to hit lines 64-65
                raise AttributeError("MotionSensitivityService vanished between calls")

        dev = _UnstableDevice()
        session = SimpleNamespace(
            device_helper=SimpleNamespace(
                motion_detectors2=[dev],
                shutter_contacts2=[],
            )
        )

        result = self._run_setup(session)

        # The device must be silently skipped (lines 64-65 executed)
        assert result == [], (
            "Device with unstable motion_sensitivity must be skipped (lines 64-65)"
        )

    def test_unstable_property_skips_but_good_device_still_added(self):
        """Unstable device is skipped; a stable device after it is still processed."""
        from boschshcpy.services_impl import PirSensorConfigurationService

        class _UnstableDevice:
            id = "md2-unstable2"
            name = "MD2 Unstable"
            root_device_id = "root-u2"

            def __init__(self):
                self._call_count = 0

            @property
            def motion_sensitivity(self):
                self._call_count += 1
                if self._call_count == 1:
                    return "HIGH"
                raise AttributeError("vanished")

        good_dev = SimpleNamespace(
            id="md2-good",
            name="MD2 Good",
            root_device_id="root-good",
            motion_sensitivity=PirSensorConfigurationService.MotionSensitivity.HIGH,
        )

        session = SimpleNamespace(
            device_helper=SimpleNamespace(
                motion_detectors2=[_UnstableDevice(), good_dev],
                shutter_contacts2=[],
            )
        )

        result = self._run_setup(session)

        # Only the good device produces an entity
        assert len(result) == 1
        assert result[0]._device is good_dev
