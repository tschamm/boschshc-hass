"""Runtime data dataclass for the Bosch SHC integration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from boschshcpy import SHCSessionAsync
from homeassistant.helpers.device_registry import DeviceEntry

if TYPE_CHECKING:
    from .coordinator import SHCZigbeeRoutingCoordinator


@dataclass
class SHCData:
    """Runtime data stored on the config entry."""

    session: SHCSessionAsync
    shc_device: DeviceEntry
    title: str
    polling_handler: Callable[[], None] | None = field(default=None)
    cert_check_unsub: Callable[[], None] | None = field(default=None)
    presence_unsub: Callable[[], None] | None = field(default=None)
    silent_mode_unsubs: list[Callable[[], None]] = field(default_factory=list)
    switch_event_listeners: list[Any] = field(default_factory=list)
    zigbee_routing_coordinator: SHCZigbeeRoutingCoordinator | None = field(default=None)
