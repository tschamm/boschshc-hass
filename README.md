![Validate with hassfest](https://github.com/tschamm/boschshc-hass/workflows/Validate%20with%20hassfest/badge.svg)

# boschshc-hass

Home Assistant component `bosch_shc` for accessing Bosch Smart Home Controller (SHC) using [boschshcpy](https://github.com/tschamm/boschshcpy) python library.

The following platforms are implemented:

* SmartHomeController (as a device)
* Smart Plug (switch)
* Light Control (switch)
* Shutter Control (cover)
* Shutter Contact (binary sensor)
* Smoke Detector (binary sensor)
* Motion Detector (binary sensor)
* Thermostat and Wall Thermostat Sensor (sensor)
* Twinguard (sensor)
* LEDVANCE Light (light)
* Room Climate Control (climate)
* Intrusion Detection Control (Alarm Control Panel)
* Scenarios (as service, as well as HA events)


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

* After adding new devices to SHC, reloading the component is necessary before these devices appear in HomeAssistant.
* The integration is not (yet) async.
* Hue Lights added to SHC do not appear in HomeAssistant. Use the default Hue component instead.
* Preparatory step for creating and registering of SSL key pair necessary before loading the integration.
