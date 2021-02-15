[![Validate with hassfest][hassfestbadge]][hassfest]
[![hacs_badge][hacsbadge]][hacs]
<!-- [![Validate with HACS][validatehacsbadge]][validatehacs] -->

[![Stars][stars-shield]][bosch_shc]
[![BuyMeCoffee][buymecoffeebadge]][buymecoffee]

# Bosch Smart Home Controller (SHC) for Home Assistant

![Bosch Smart Home](https://www.home-connect-plus.com/dist/static/partners/bosch_smart_home/Bosch_Smart_Home_290px_@2x.png)

Custom Home Assistant integration for accessing Bosch Smart Home Controller (SHC).

The integration is using [boschshcpy](https://github.com/tschamm/boschshcpy) as backend towards the API.

The SmartHomeController is added as a device. The component provides access to:

* Service calls:
  * `bosch_shc.trigger_scenario` service call to trigger a scenario by its name
  * `bosch_shc.smokedetector_check` service call to trigger a check routine of the smokedetector
* `bosch_shc.event` events:
  * Button events for Universal Switches devices (lower and upper button, short and long press)
  * Scenario events for triggered scenarios registered in SHC device, each scenario is identified by its name
  * Motion events for detected motion for Motion Detector devices
  * Alarm events for triggered alarms for Smoke Detector devices

The following platforms are implemented:

* Alarm Control Panel
  * Intrusion Detection Control
* Binary Sensor
  * Shutter Contact
  * Smoke Detector
  * Motion Detector
* Climate
  * Room Climate Control
* Cover
  * Shutter Control
* Light
  * LEDVANCE Light
* Sensor
  * Thermostat
  * Wall Thermostat
  * Twinguard
  * Battery: all battery powered devices
  * Smart Plug and Light Control (energy and power)
* Switch
  * Smart Plug
  * Light Control

# Installation

For installation, follow these steps to add Bosch Smart Home devices to `HomeAssistant`.

1. Install bosch_shc custom component
2. Generate a certificate/key pair
3. Register a new client on the SHC device
4. Configure bosch_shc integration in HA.

1.) To install `bosch_shc` as custom component, inside your HA configuration directory create a new folder called  `custom_components`. This is the folder that Home Assistant will look at when looking for custom code. Install the custom component there:
Just copy paste the content of the `boschshc-hass/bosch_shc` folder in your  `config/custom_components`  directory. As example, you will get the  `entity.py`  file in the following path:  `config/custom_components/bosch_shc/entity.py`.
Afterwards, restart `HomeAssistant`.

2.) + 3.) Follow the [official guide](https://github.com/BoschSmartHome/bosch-shc-api-docs/tree/master/postman#register-a-new-client-to-the-bosch-smart-home-controller) for setting up a new SSL certificate public / private key pair and for registering this certificate on the Bosch SHC step by step. As a result, you obtained a generated SSL certificate key pair which is registered for accessing and controlling the SHC.

4.) For configuration of `bosch_shc` custom component, follow the steps described in [configuration](#configuration). During configuration, you have to enter the obtained credentials from step 2.) by providing the path to your public and private key pair of your SSL certificate.


# Configuration

Configuration of the component `bosch_shc` is done via config flow mechanism, either by `zeroconf` detection or by manual configuration:

If the `SHC` is running in the same network as the `HomeAssistant`, it is even found directly via `zeroconf`.

### Configuration of the discovered integration

#### 1.) Discovered integration

<img
  src='images/config_step1.png'
  alt='Discovered integration.'
  width='437pt'
/>

#### 2.) Confirmation of host

<img
  src='images/config_step2.png'
  alt='Confirmation of host.'
  width='605pt'
/>

#### 3.) Enter credentials: SSL certificate public and private key pair

<img
  src='images/config_step3.png'
  alt='Enter credentials: SSL certificate public / private key pair.'
  width='515pt'
/>

#### 4.) Successful configuration entry created

<img
  src='images/config_step4.png'
  alt='Successful configuration entry created.'
  width='629pt'
/>

#### 5.) Integration is listed as a configured integration

<img
  src='images/config_step5.png'
  alt='Integration is listed as a configured integration.'
  width='467pt'
/>

# Additional information

Follow this [thread](https://community.home-assistant.io/t/bosch-smart-home/115864) for discussions on the Bosch Smart Home Controller Home Assistant integration.

# Known Issues

* Preparation step for creating and registering of SSL key pair necessary before loading the integration.
* Encrypted SSL private key and SSL host verification is not supported due to limitations of `requests` library.
* The integration is not (yet) async.
* After adding new devices to SHC, reloading the component is necessary before these devices appear in HomeAssistant.
* Hue Lights added to SHC do not appear in HomeAssistant. Please use the provided [hue component](https://www.home-assistant.io/integrations/hue/) instead.
* Arming and disarming of alarm control panel does not support using a code.

[buymecoffee]: https://www.buymeacoffee.com/tschamm
[buymecoffeebadge]: https://img.shields.io/badge/buy%20me%20a%20double%20espresso-donate-yellow.svg?style=for-the-badge
[hassfest]: https://github.com/tschamm/boschshc-hass/actions
[hassfestbadge]: https://img.shields.io/github/workflow/status/tschamm/boschshc-hass/Validate%20with%20hassfest?style=for-the-badge
[validatehacs]: https://github.com/tschamm/boschshc-hass/actions
[validatehacsbadge]: https://img.shields.io/github/workflow/status/tschamm/boschshc-hass/Validate%20HACS?style=for-the-badge
[hacs]: https://github.com/custom-components/hacs
[hacsbadge]: https://img.shields.io/badge/HACS-Default-orange.svg?style=for-the-badge
[bosch_shc]: https://github.com/tschamm/boschshc-hass
[stars-shield]: https://img.shields.io/github/stars/tschamm/boschshc-hass?style=for-the-badge
