"""DataUpdateCoordinator for Zigbee mesh routing-quality diagnostics.

Follows the documented pattern (developers.home-assistant.io/docs/
integration_fetching_data/) for genuinely-polled data. This is the one piece
of data in the integration that is NOT delivered by the long-poll stream
(iot_class local_push) — SHCSessionAsync.get_zigbee_routing_info is an
on-demand HTTPS GET per device, so it needs its own poll loop instead of the
push-entity pattern every other platform in this integration uses.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import TYPE_CHECKING

from boschshcpy.exceptions import SHCConnectionError, SHCException
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import DOMAIN, LOGGER

if TYPE_CHECKING:
    from boschshcpy import SHCSessionAsync
    from boschshcpy.zigbee_routing import SHCZigbeeRoutingInfo
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

# Matches the interval this feature originally shipped with as a per-entity
# should_poll=True/SCAN_INTERVAL before being refactored onto a coordinator.
ZIGBEE_ROUTING_SCAN_INTERVAL = timedelta(minutes=5)


class SHCZigbeeRoutingCoordinator(
    DataUpdateCoordinator["dict[str, SHCZigbeeRoutingInfo]"]
):
    """Poll Zigbee routing info for every hdm:ZigBee: device on this SHC.

    Only devices whose id starts with "hdm:ZigBee:" support
    SHCSessionAsync.get_zigbee_routing_info (ground truth from a real SHC).
    """

    def __init__(
        self, hass: HomeAssistant, config_entry: ConfigEntry, session: SHCSessionAsync
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            LOGGER,
            config_entry=config_entry,
            name=f"{DOMAIN}_zigbee_routing",
            update_interval=ZIGBEE_ROUTING_SCAN_INTERVAL,
        )
        self._session = session

    async def _async_update_data(self) -> dict[str, SHCZigbeeRoutingInfo]:
        """Fetch routing info for every Zigbee-attached device, concurrently.

        Fired one at a time this would serialize N HTTPS round-trips behind
        async_config_entry_first_refresh() during async_setup_entry, delaying
        every other platform's setup on an install with many Zigbee devices —
        fetch concurrently instead.

        A single device's failure (offline mesh node, transient error) must
        not fail the whole refresh for every other device: caught per
        device via return_exceptions, logged at debug (this fires routinely
        for an offline node, every update_interval), and simply omitted from
        the result for this cycle. Raising UpdateFailed here is reserved for
        a total failure (e.g. the SHC session itself unreachable) — not
        modeled since get_zigbee_routing_info is called per-device and this
        already isolates each call.
        """
        device_ids = [
            device_id
            for device in getattr(self._session, "devices", None) or []
            if (device_id := getattr(device, "id", None))
            and device_id.startswith("hdm:ZigBee:")
        ]
        fetched = await asyncio.gather(
            *(self._session.get_zigbee_routing_info(d) for d in device_ids),
            return_exceptions=True,
        )
        result: dict[str, SHCZigbeeRoutingInfo] = {}
        for device_id, outcome in zip(device_ids, fetched, strict=True):
            if isinstance(outcome, (SHCException, SHCConnectionError)):
                LOGGER.debug(
                    "Failed to fetch Zigbee routing info for %s: %s",
                    device_id,
                    outcome,
                )
                continue
            if isinstance(outcome, BaseException):
                raise outcome
            result[device_id] = outcome
        return result
