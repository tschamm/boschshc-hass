"""Platform for cover integration."""
import logging

from homeassistant.components.alarm_control_panel import (
    ATTR_CODE,
    ATTR_CODE_FORMAT,
    AlarmControlPanel,
)
from BoschShcPy import intrusion_detection

from .const import DOMAIN, SHC_LOGIN
SHC_BRIDGE = "shc_bridge"

_LOGGER = logging.getLogger(__name__)


def setup_platform(hass, config, add_entities, discovery_info=None):
    """Set up the platform."""
    # We only want this platform to be set up via discovery.
    dev = []
    client = hass.data[SHC_BRIDGE]
    
    service = intrusion_detection.IntrusionDetection(client)
    dev.append(IntrusionDetectionAlarmControlPanel(service, service.get_name, service.get_state, client))
    
    if dev:
        add_entities(dev, True)

class IntrusionDetectionAlarmControlPanel(AlarmControlPanel):

    def __init__(self, cover, name, state, client):
        self._representation = cover
        self._client = client
        self._state = state
        self._name = name
        self._representation.register_polling(self._client, self.update_callback)
    
    def update_callback(self, device):
        _LOGGER.debug("Update notification for intrusion detection: %s" % device.id)
        self.schedule_update_ha_state(True)
        
    @property
    def name(self):
        """Name of the device."""
        return self._name

    @property
    def manufacturer(self):
        """The manufacturer of the device."""
        return self._manufacturer

    @property
    def should_poll(self):
        """Polling needed."""
        return False
    
    @property
    def code_format(self):
        """Regex for code format or None if no code is required."""
        return None

    @property
    def changed_by(self):
        """Last change triggered by."""
        return None

    def alarm_disarm(self, code=None):
        """Send disarm command."""
#         if code == ATTR_CODE:
        self._representation.disarm

    def alarm_arm_home(self, code=None):
        """Send arm home command."""
        self._representation.arm

    def alarm_arm_away(self, code=None):
        """Send arm away command."""
        self._representation.arm

    def alarm_arm_night(self, code=None):
        """Send arm night command."""
        self._representation.arm

    def alarm_trigger(self, code=None):
        """Send alarm trigger command."""
        self._representation.trigger

    
    # def turn_on(self, **kwargs):
    #     """Turn the switch on."""
    #     self._representation.set_state(True)
    #     self._is_on = True
    #     _LOGGER.debug("New switch state is %s" % self._is_on)
    #
    # def turn_off(self, **kwargs):
    #     """Turn the switch off."""
    #     self._representation.set_state(False)
    #     self._is_on = False
    #     _LOGGER.debug("New switch state is %s" % self._is_on)
    #
    # def toggle(self, **kwargs):
    #     """Toggles the switch."""
    #     self._representation.set_state(not self._representation.get_state())
    #     self._is_on = not self._is_on
    #     _LOGGER.debug("New switch state is %s" % self._is_on)
    
    def update(self, **kwargs):
        if self._representation.update():
            self._state = self._representation.get_state
            self._name = self._representation.get_name
        