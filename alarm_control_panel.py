"""Platform for cover integration."""
import logging

import homeassistant.components.alarm_control_panel as alarm
from homeassistant.const import (
    STATE_ALARM_ARMED_AWAY,
    STATE_ALARM_ARMING,
    STATE_ALARM_DISARMED,
    STATE_ALARM_PENDING,
    STATE_ALARM_TRIGGERED,
)
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from BoschShcPy import intrusion_detection

from .const import DOMAIN, SHC_LOGIN
SHC_BRIDGE = "shc_bridge"

_LOGGER = logging.getLogger(__name__)


def setup_platform(hass, config, add_entities, discovery_info=None):
    """Set up the Ness Alarm alarm control panel devices."""
    if discovery_info is None:
        return

    client = hass.data[SHC_BRIDGE]
    service = intrusion_detection.IntrusionDetection(client)
    service.update()

    device = IntrusionDetectionAlarmControlPanel(service, service.get_name, service.get_state, client)
    add_entities([device])

class IntrusionDetectionAlarmControlPanel(alarm.AlarmControlPanel):

    def __init__(self, service, name, state, client):
        self._representation = service
        self._client = client
        self.set_arming_state(state)
        self._name = name
        self._device_state_attributes = {}
        self._representation.register_polling(
            self._client, self.update_callback)
        self._representation.update()

    # async def async_added_to_hass(self):
    #     """Register callbacks."""
    #     async_dispatcher_connect(
    #         self.hass, SIGNAL_ARMING_STATE_CHANGED, self._handle_arming_state_change
    #     )

    # @callback
    # def _handle_arming_state_change(self, arming_state):
    #     """Handle arming state update."""
    #     if arming_state == intrusion_detection.operation_state.SYSTEM_ARMING:
    #         self._state = STATE_ALARM_ARMING
    #     elif arming_state == intrusion_detection.operation_state.SYSTEM_DISARMED:
    #         self._state = STATE_ALARM_DISARMED
    #     elif arming_state == intrusion_detection.operation_state.SYSTEM_ARMED:
    #         self._state = STATE_ALARM_ARMED_AWAY
    #     # elif arming_state == ArmingState.TRIGGERED:
    #     #     self._state = STATE_ALARM_TRIGGERED
    #     else:
    #         _LOGGER.warning("Unhandled arming state: %s", arming_state)

    #     self.async_schedule_update_ha_state()

    def update_callback(self, device):
        _LOGGER.debug("Update notification for intrusion detection: %s" % device.id)
        self.schedule_update_ha_state(True)
        
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
        return self._manufacturer

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

    def set_arming_state(self, arming_state):
        if arming_state == intrusion_detection.operation_state.SYSTEM_ARMING:
            self._state = STATE_ALARM_ARMING
        elif arming_state == intrusion_detection.operation_state.SYSTEM_DISARMED:
            self._state = STATE_ALARM_DISARMED
        elif arming_state == intrusion_detection.operation_state.SYSTEM_ARMED:
            self._state = STATE_ALARM_ARMED_AWAY
        # elif arming_state == ArmingState.TRIGGERED:
        #     self._state = STATE_ALARM_TRIGGERED
        else:
            _LOGGER.warning("Unhandled arming state: %s", arming_state)

    def update(self, **kwargs):
        if self._representation.update():
            self.set_arming_state(self._representation.get_state)
            self._name = self._representation.get_name
        
