"""Platform for cover integration."""
import logging
import asyncio

import homeassistant.components.alarm_control_panel as alarm
from homeassistant.const import (
    CONF_NAME,
    STATE_ALARM_ARMED_AWAY,
    STATE_ALARM_ARMING,
    STATE_ALARM_DISARMED,
    STATE_ALARM_PENDING,
    STATE_ALARM_TRIGGERED,
)
from homeassistant.util import slugify

from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from BoschShcPy import intrusion_detection

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Set up the Alarm alarm control panel platform."""

    client = hass.data[DOMAIN][slugify(config[CONF_NAME])]
    service = intrusion_detection.initialize_intrusion_detection(
        client, client.device_list())

    service.update()

    device = IntrusionDetectionAlarmControlPanel(service, service.get_name, service.get_state, client)
    return await async_add_entities([device])


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the Alarm alarm control panel platform."""

    client = hass.data[DOMAIN][slugify(config_entry.data[CONF_NAME])].my_client

    service = intrusion_detection.initialize_intrusion_detection(
        client, client.device_list())
    service.update()

    device = IntrusionDetectionAlarmControlPanel(
        service, service.get_name, service.get_state, client)

    if device:
        async_add_entities([device])

class IntrusionDetectionAlarmControlPanel(alarm.AlarmControlPanel):

    def __init__(self, service, name, state, client):
        self._representation = service
        self._client = client
        self._state = self.get_arming_state(state)
        self._name = name
        self._device_state_attributes = {}
        self._representation.register_polling(
            self._client, self.update_callback)
        self._representation.update()

    def update_callback(self, device):
        _LOGGER.debug("Update notification for intrusion detection: %s" % device.id)
        self.schedule_update_ha_state(True)
        
    @property
    def unique_id(self):
        """Return the unique ID of this panel."""
        return self._representation.get_device.serial

    @property
    def device_id(self):
        """Return the ID of this switch."""
        return self.unique_id

    @property
    def root_device(self):
        return self._representation.get_device.rootDeviceId

    @property
    def device_info(self):
        """Return the device info."""
        return {
            "identifiers": {(DOMAIN, self.device_id)},
            "name": self._name,
            "manufacturer": self.manufacturer,
            "model": self._representation.get_device.deviceModel,
            "sw_version": "",
            "via_device": (DOMAIN, self._client.get_ip_address),
            # "via_device": (DOMAIN, self.root_device),
        }

    @property
    def name(self):
        """Name of the device."""
        return self._name

    @property
    def state(self):
        """Return the state of the device."""
        return self._state

    @property
    def device_state_attributes(self):
        """Return the state attributes of the device."""
        return self._device_state_attributes

    @property
    def manufacturer(self):
        """The manufacturer of the device."""
        return self._representation.get_device.manufacturer

    @property
    def should_poll(self):
        """Polling needed."""
        return True
    
    @property
    def code_format(self):
        """Return the regex for code format or None if no code is required."""
        return None
        # return alarm.FORMAT_NUMBER

    def alarm_disarm(self, code=None):
        """Send disarm command."""
        self._representation.disarm()

    def alarm_arm_away(self, code=None):
        """Send arm away command."""
        self._representation.arm_instant()

    def alarm_arm_home(self, code=None):
        """Send arm home command."""
        self._representation.arm_instant()

    def alarm_trigger(self, code=None):
        """Send trigger/panic command."""
        self._representation.tigger()

    def get_arming_state(self, arming_state):
        state = None

        if arming_state == intrusion_detection.operation_state.SYSTEM_ARMING:
            state = STATE_ALARM_ARMING
        elif arming_state == intrusion_detection.operation_state.SYSTEM_DISARMED:
            state = STATE_ALARM_DISARMED
        elif arming_state == intrusion_detection.operation_state.SYSTEM_ARMED:
            state = STATE_ALARM_ARMED_AWAY
        # elif arming_state == ArmingState.TRIGGERED:
        #     state = STATE_ALARM_TRIGGERED
        else:
            _LOGGER.warning("Unhandled arming state: %s", arming_state)
        
        return state

    def update(self, **kwargs):
        if self._representation.update():
            self._state = self.get_arming_state(self._representation.get_state)
            self._name = self._representation.get_name
        
