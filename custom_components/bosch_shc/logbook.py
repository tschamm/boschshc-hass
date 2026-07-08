"""Describe Bosch SHC logbook events."""

from __future__ import annotations

from typing import Any, Callable

from homeassistant.const import ATTR_NAME
from homeassistant.core import HomeAssistant, callback

from .const import ATTR_EVENT_SUBTYPE, ATTR_EVENT_TYPE, DOMAIN, EVENT_BOSCH_SHC


@callback  # type: ignore[untyped-decorator]
def async_describe_events(
    hass: HomeAssistant,
    async_describe_event: Callable[..., None],
) -> None:
    """Describe logbook events."""

    @callback  # type: ignore[untyped-decorator]
    def async_describe_bosch_shc_event(event: Any) -> dict[str, str]:
        """Describe bosch_shc.click logbook event."""
        device_name = event.data[ATTR_NAME]
        event_subtype = event.data[ATTR_EVENT_SUBTYPE]
        event_type = event.data[ATTR_EVENT_TYPE]

        if event_type == "MOTION":
            return {
                "name": "Bosch SHC",
                "message": f"'{device_name}' motion event was fired.",
            }

        if event_type == "ALARM":
            return {
                "name": "Bosch SHC",
                "message": f"'{device_name}' alarm event '{event_subtype}' was fired.",
            }

        if event_type == "SCENARIO":
            return {
                "name": "Bosch SHC",
                "message": f"'{device_name}' scenario trigger event was fired.",
            }

        return {
            "name": "Bosch SHC",
            "message": f"'{event_type}' click event for {device_name} button '{event_subtype}' was fired.",
        }

    async_describe_event(
        DOMAIN,
        EVENT_BOSCH_SHC,
        async_describe_bosch_shc_event,
    )
