![Validate with hassfest](https://github.com/tschamm/boschshc-hass/workflows/Validate%20with%20hassfest/badge.svg)

# boschshc-hass

Home Assistant component for accessing Bosch Smart Home Controller using 3rd party [boschshcpy](https://github.com/tschamm/boschshcpy) python library.

The following platforms are implemented:

* SmartHomeController (as a device)
* Smart Plug (switch)
* Light Control (switch)
* Shutter Control (cover)
* Shutter Contact (binary sensor)
* Smoke Detector (binary sensor)
* Temperature Sensor (sensor)
* Room Climate Control (climate)
* Intrusion Detection Control (Alarm Control Panel)
* Scenarios (switch)

Registration of the component can be done via config flow mechanism, or by adding to `configuration.yaml`:

```
boschshc:
   ip_address: '192.168.1.52'
   ssl_certificate: '/path/to/cert.pem'
   ssl_key: '/path/to/key.pem'
   name: 'SHC-Controller'
```
