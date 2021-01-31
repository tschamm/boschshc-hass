"""Describe Shelly logbook events."""

from homeassistant.const import ATTR_NAME
from homeassistant.core import callback

from .const import (
    ATTR_BUTTON,
    ATTR_CLICK_TYPE,
    DOMAIN,
    EVENT_BOSCH_SHC_CLICK,
    EVENT_BOSCH_SHC_SCENARIO_TRIGGER,
    EVENT_BOSCH_SHC_MOTION_DETECTED
)


@callback
def async_describe_events(hass, async_describe_event):
    """Describe logbook events."""

    @callback
    def async_describe_bosch_shc_scenario_trigger_event(event):
        """Describe bosch_shc.scenario_trigger logbook event."""

        name = event.data[ATTR_NAME]

        return {
            "name": "Bosch SHC",
            "message": f"'{name}' scenario trigger event was fired.",
        }

    @callback
    def async_describe_bosch_shc_motion_detected_event(event):
        """Describe bosch_shc.motion_detected logbook event."""

        device_name = event.data[ATTR_NAME]

        return {
            "name": "Bosch SHC",
            "message": f"'{device_name}' motion event was fired.",
        }

    @callback
    def async_describe_bosch_shc_click_event(event):
        """Describe bosch_shc.click logbook event."""

        device_name = event.data[ATTR_NAME]
        subtype = event.data[ATTR_BUTTON]
        click_type = event.data[ATTR_CLICK_TYPE]

        return {
            "name": "Bosch SHC",
            "message": f"'{click_type}' click event for {device_name} button '{subtype}' was fired.",
        }

    async_describe_event(
        DOMAIN,
        EVENT_BOSCH_SHC_SCENARIO_TRIGGER,
        async_describe_bosch_shc_scenario_trigger_event,
    )
    async_describe_event(
        DOMAIN,
        EVENT_BOSCH_SHC_MOTION_DETECTED,
        async_describe_bosch_shc_motion_detected_event,
    )
    async_describe_event(
        DOMAIN, EVENT_BOSCH_SHC_CLICK, async_describe_bosch_shc_click_event
    )
