# boschshc-hass
Home Assistant component using [BoschSHCPy](https://github.com/tschamm/boschshcpy) package for accessing Bosch Smart Home Components

The following SHC components are currently implemented:
* SmartHomeController (as a Device) 
* Smart Plug (Switch)
* Shutter Control (Cover)
* Shutter Contact (Binary Sensor)
* Smoke Detector (Binary Sensor)
* Intrusion Detection Service (Alarm Control Panel)

For updating the state of the components, the long polling mechanism is used.  

The component registration within HA is done via config flow mechanism. Currently, the component is added as a custom component to `~/.homeassistant/custom_components/boschshc`. 
Before adding the integration to Home Assistant, a new client has to be registered. For registration of the client on the controller, the [apitest.py](https://github.com/tschamm/boschshcpy/blob/master/examples/apitest.py) example script can be used.

The following parameters have to be provided:
* 'access_cert': Path to Cert-File
* 'access_key': Path to Key-File, 
* 'ip_address': IP address of the controller, 
* 'name': Name of the client within home assistant, 
* 'port': Port of the controller (defaults to 8444)

All available devices should pop up in the entity registration.

I would be glad if anybody wants to join for improving it :slight_smile:  At least the underlying REST access can be improved to support async access.
