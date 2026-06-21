"""Unit tests for IntrusionSystemAlarmControlPanel.extra_state_attributes.

Verifies that incidents, security_gaps, and remaining_time_until_armed
are exposed correctly.

Uses __new__ bypass + SimpleNamespace device. No HA harness.
"""
from __future__ import annotations

from types import SimpleNamespace

from custom_components.bosch_shc.alarm_control_panel import (
    IntrusionSystemAlarmControlPanel,
)


def _make_ids_device(
    incidents=None,
    security_gaps=None,
    remaining_time=0,
):
    if incidents is None:
        incidents = []
    if security_gaps is None:
        security_gaps = []
    return SimpleNamespace(
        id="/intrusion",
        root_device_id="aa:bb:cc:00:00:01",
        name="Intrusion Detection System",
        manufacturer="BOSCH",
        device_model="IDS",
        system_availability=True,
        alarm_state_incidents=incidents,
        security_gaps=security_gaps,
        remaining_time_until_armed=remaining_time,
    )


def _make_panel(device):
    panel = IntrusionSystemAlarmControlPanel.__new__(IntrusionSystemAlarmControlPanel)
    panel._device = device
    panel._entry_id = "E1"
    panel._attr_unique_id = f"{device.root_device_id}_{device.id}"
    return panel


class TestIDSExtraStateAttributes:
    def test_incidents_empty_list_by_default(self):
        dev = _make_ids_device()
        panel = _make_panel(dev)
        attrs = panel.extra_state_attributes
        assert attrs["incidents"] == []

    def test_incidents_list_returned(self):
        incidents = [{"type": "ALARM_ON", "deviceId": "dev1"}]
        dev = _make_ids_device(incidents=incidents)
        panel = _make_panel(dev)
        attrs = panel.extra_state_attributes
        assert attrs["incidents"] == incidents

    def test_security_gaps_empty_list_by_default(self):
        dev = _make_ids_device()
        panel = _make_panel(dev)
        attrs = panel.extra_state_attributes
        assert attrs["security_gaps"] == []

    def test_security_gaps_list_returned(self):
        gaps = [{"type": "DOOR_OPEN", "deviceId": "dev2"}]
        dev = _make_ids_device(security_gaps=gaps)
        panel = _make_panel(dev)
        attrs = panel.extra_state_attributes
        assert attrs["security_gaps"] == gaps

    def test_remaining_time_zero_by_default(self):
        dev = _make_ids_device()
        panel = _make_panel(dev)
        attrs = panel.extra_state_attributes
        assert attrs["remaining_time_until_armed"] == 0

    def test_remaining_time_non_zero_when_arming(self):
        dev = _make_ids_device(remaining_time=30)
        panel = _make_panel(dev)
        attrs = panel.extra_state_attributes
        assert attrs["remaining_time_until_armed"] == 30

    def test_all_three_keys_present(self):
        dev = _make_ids_device()
        panel = _make_panel(dev)
        attrs = panel.extra_state_attributes
        assert "incidents" in attrs
        assert "security_gaps" in attrs
        assert "remaining_time_until_armed" in attrs

    def test_multiple_incidents_and_gaps(self):
        incidents = [{"a": 1}, {"a": 2}]
        gaps = [{"b": 3}, {"b": 4}, {"b": 5}]
        dev = _make_ids_device(incidents=incidents, security_gaps=gaps)
        panel = _make_panel(dev)
        attrs = panel.extra_state_attributes
        assert len(attrs["incidents"]) == 2
        assert len(attrs["security_gaps"]) == 3
