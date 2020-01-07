# boschshc-hass
Home Assistant component using BoschSHCPy package for accessing Bosch Smart Home Components

I separated everything into two components:
* [BoschSHCPy](https://github.com/tschamm/boschshcpy) is a python3 package for accessing the Bosch Smart Home Components
* [boschshc-hass](https://github.com/tschamm/boschshc-hass) makes usage of the BoschSHCPy package and provides the integration into home assistant.

The following SHC components are currently implemented:
* SmartHomeController (as a Device) 
* Smart Plug (Switch)
* Shutter Control (Cover)
* Shutter Contact (Binary Sensor)
* Smoke Detector (Binary Sensor)
* Intrusion Detection Service (Alarm Control Panel)

For updating the state of the components, the long polling mechanism is used.

I would be glad if anybody wants to join for improving it :slight_smile:  At least the underlying REST access can be improved to support async access.
