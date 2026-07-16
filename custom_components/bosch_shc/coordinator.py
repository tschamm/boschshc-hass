"""DataUpdateCoordinator for Zigbee mesh routing-quality diagnostics.

Follows the documented pattern (developers.home-assistant.io/docs/
integration_fetching_data/) for genuinely-polled data. This is the one piece
of data in the integration that is NOT delivered by the long-poll stream
(iot_class local_push) — SHCSessionAsync.get_zigbee_routing_info is an
on-demand HTTPS GET per device, so it needs its own poll loop instead of the
push-entity pattern every other platform in this integration uses.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from boschshcpy.exceptions import SHCConnectionError, SHCException
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import DOMAIN, LOGGER

if TYPE_CHECKING:
    from boschshcpy import SHCSessionAsync
    from boschshcpy.zigbee_routing import SHCZigbeeRoutingInfo
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant


class SHCZigbeeRoutingCoordinator(
    DataUpdateCoordinator["dict[str, SHCZigbeeRoutingInfo]"]
):
    """Fetch Zigbee routing info for every hdm:ZigBee: device on this SHC.

    Only devices whose id starts with "hdm:ZigBee:" support
    SHCSessionAsync.get_zigbee_routing_info (ground truth from a real SHC).

    No periodic polling (`update_interval=None`): a Bosch SHC engineer
    flagged even a slow periodic interval as an unnecessary battery/
    stability cost, since each query is a live over-the-air round-trip to
    the device, never cached. Data is fetched once at startup (the explicit
    `async_refresh()` call in async_setup_entry) and afterwards only on
    explicit user request, via the `refresh_zigbee_routing` service.
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
            update_interval=None,
        )
        self._session = session

    async def _async_update_data(self) -> dict[str, SHCZigbeeRoutingInfo]:
        """Fetch routing info for every Zigbee-attached device, one at a time.

        Sequential, not concurrent: firing every device's on-demand routing
        query at once spikes load on the SHC and the Zigbee mesh itself
        (each query makes the SHC round-trip live to the physical device,
        nothing is cached) — flagged by a Bosch SHC engineer as a real
        stability/battery concern. A few extra seconds of wall-clock per
        refresh is a fine trade for a refresh that only happens on request.

        A single device's failure (offline mesh node, transient error) must
        not fail the whole refresh for every other device: caught per
        device, logged at debug (this fires routinely for an offline node),
        and simply omitted from the result for this cycle.
        """
        device_ids = [
            device_id
            for device in getattr(self._session, "devices", None) or []
            if (device_id := getattr(device, "id", None))
            and device_id.startswith("hdm:ZigBee:")
        ]
        result: dict[str, SHCZigbeeRoutingInfo] = {}
        for device_id in device_ids:
            info = await self._fetch_one(device_id)
            if info is not None:
                result[device_id] = info
        return result

    async def _fetch_one(self, device_id: str) -> SHCZigbeeRoutingInfo | None:
        """Fetch one device's routing info, or None on a per-device error.

        A separate method (not an inline try/except in the loop above) both
        avoids ruff's PERF203 and keeps the per-device isolation contract
        documented on _async_update_data easy to read at a glance.
        """
        try:
            return await self._session.get_zigbee_routing_info(device_id)
        except (SHCException, SHCConnectionError) as err:
            LOGGER.debug(
                "Failed to fetch Zigbee routing info for %s: %s", device_id, err
            )
            return None
