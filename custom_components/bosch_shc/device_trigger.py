"""Provides device triggers for Bosch Smart Home Controller integration."""
from typing import List

import voluptuous as vol
from boschshcpy import SHCUniversalSwitch
from homeassistant.components.automation import AutomationActionType
from homeassistant.components.device_automation import TRIGGER_BASE_SCHEMA
from homeassistant.components.device_automation.exceptions import (
    InvalidDeviceAutomationConfig,
)
from homeassistant.components.homeassistant.triggers import event as event_trigger
from homeassistant.const import (
    ATTR_DEVICE_ID,
    CONF_DEVICE_ID,
    CONF_DOMAIN,
    CONF_EVENT,
    CONF_PLATFORM,
    CONF_TYPE,
)
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.typing import ConfigType

from .const import (
    ATTR_BUTTON,
    ATTR_CLICK_TYPE,
    CONF_SUBTYPE,
    DOMAIN,
    EVENT_BOSCH_SHC_CLICK,
    INPUTS_EVENTS_SUBTYPES,
    SUPPORTED_INPUTS_EVENTS_TYPES,
)

TRIGGER_SCHEMA = TRIGGER_BASE_SCHEMA.extend(
    {
        vol.Required(CONF_TYPE): vol.In(SUPPORTED_INPUTS_EVENTS_TYPES),
        vol.Required(CONF_SUBTYPE): vol.In(INPUTS_EVENTS_SUBTYPES),
    }
)


async def get_device_from_id(hass, device_id) -> SHCUniversalSwitch:
    """Get the switch device for the given device id."""
    dev_registry = await dr.async_get_registry(hass)
    for config_entry in hass.data[DOMAIN]:
        session = hass.data[DOMAIN][config_entry]

        for switch_device in session.device_helper.universal_switches:
            device = dev_registry.async_get_device(
                identifiers={(DOMAIN, switch_device.id)}, connections=set()
            )
            if device.id == device_id:
                return switch_device

    return None


# async def async_validate_trigger_config(hass, config):
#     """Validate config."""
#     config = TRIGGER_SCHEMA(config)

#     # if device is available verify parameters against device capabilities
#     device = await get_device_from_id(hass, config[CONF_DEVICE_ID])
#     if not device:
#         return config

#     trigger = (config[CONF_TYPE], config[CONF_SUBTYPE])

#     input_triggers = []
#     for event_type in SUPPORTED_INPUTS_EVENTS_TYPES:
#         for subtype in INPUTS_EVENTS_SUBTYPES:
#             input_triggers.append((event_type, subtype))

#     if trigger in input_triggers:
#         return config

#     raise InvalidDeviceAutomationConfig(
#         f"Invalid ({CONF_TYPE},{CONF_SUBTYPE}): {trigger}"
#     )


async def async_get_triggers(hass: HomeAssistant, device_id: str) -> List[dict]:
    """List device triggers for Shelly devices."""
    triggers = []

    device = await get_device_from_id(hass, device_id)
    if not device:
        raise InvalidDeviceAutomationConfig(f"Device not found: {device_id}")

    input_triggers = []
    for trigger in SUPPORTED_INPUTS_EVENTS_TYPES:
        for subtype in INPUTS_EVENTS_SUBTYPES:
            input_triggers.append((trigger, subtype))

    for trigger, subtype in input_triggers:
        triggers.append(
            {
                CONF_PLATFORM: "device",
                CONF_DEVICE_ID: device_id,
                CONF_DOMAIN: DOMAIN,
                CONF_TYPE: trigger,
                CONF_SUBTYPE: subtype,
            }
        )

    return triggers


async def async_attach_trigger(
    hass: HomeAssistant,
    config: ConfigType,
    action: AutomationActionType,
    automation_info: dict,
) -> CALLBACK_TYPE:
    """Attach a trigger."""

    config = TRIGGER_SCHEMA(config)
    event_config = event_trigger.TRIGGER_SCHEMA(
        {
            event_trigger.CONF_PLATFORM: CONF_EVENT,
            event_trigger.CONF_EVENT_TYPE: EVENT_BOSCH_SHC_CLICK,
            event_trigger.CONF_EVENT_DATA: {
                ATTR_DEVICE_ID: config[CONF_DEVICE_ID],
                ATTR_BUTTON: config[CONF_SUBTYPE],
                ATTR_CLICK_TYPE: config[CONF_TYPE],
            },
        }
    )
    event_config = event_trigger.TRIGGER_SCHEMA(event_config)
    return await event_trigger.async_attach_trigger(
        hass, event_config, action, automation_info, platform_type="device"
    )
