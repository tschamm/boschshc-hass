[![Validate with hassfest][hassfestbadge]][hassfest]
[![hacs_badge][hacsbadge]][hacs]
<!-- [![Validate with HACS][validatehacsbadge]][validatehacs] -->

[![BuyMeCoffee][buymecoffeebadge-tschamm]][buymecoffee-tschamm]
[![BuyMeCoffee][buymecoffeebadge-mosandlts]][buymecoffee-mosandlts]
[![Stars][stars-shield]][bosch_shc]

# Bosch Smart Home Controller (SHC) for Home Assistant

![Bosch Smart Home](https://local.apidocs.bosch-smarthome.com/images/bosch_smart_home_logo.png)

Custom Home Assistant integration for the Bosch Smart Home Controller (SHC).
Uses [boschshcpy](https://github.com/tschamm/boschshcpy) (v0.2.122) as the local API backend.
Communication is local-only (no cloud required): the integration speaks directly to the SHC over
mutual-TLS on the local network.

## Supported platforms

| Platform | Devices / features |
|---|---|
| `alarm_control_panel` | Intrusion Detection System |
| `binary_sensor` | Shutter Contact (Gen 1 + Gen 2), Motion Detector (Gen 1 + Gen 2 [+M]), Smoke Detector (Gen 1 + Gen 2), Smoke Detection System, Water Leakage Sensor, Shutter Contact 2 Plus (vibration), Battery state (all battery-powered devices) |
| `button` | Micromodule Relay (impulse/momentary type) |
| `climate` | Room Climate Control (thermostat valve groups), Heating Circuit |
| `cover` | Shutter Control (BBL), Micromodule Shutter, Micromodule Awning, Micromodule Blinds (with tilt) |
| `event` | Universal Switch (WRC2 / SWITCH2) button presses, Scenarios, Motion events, Smoke Detector alarm events, Smoke Detection System alarm events |
| `light` | LEDVANCE lights (on/off, brightness, color), Hue lights (via SHC), Micromodule Dimmer, Motion Detector II integrated light |
| `number` | Thermostat temperature offset |
| `sensor` | Temperature, Humidity, CO₂ purity, Air quality + rating sensors (Twinguard), Energy + Power (Smart Plug, Smart Plug Compact, Light Control, Micromodule variants), Illuminance (Motion Detectors), EMMA grid power, Battery level (diagnostic, optional) |
| `switch` | Smart Plug, Smart Plug Compact, Light Control, Micromodule Relay, Camera Eyes (privacy / light / notification), Camera 360 (privacy / notification), Camera Outdoor Gen2 (privacy / front-light / ambient-light), Presence Simulation, Bypass (Shutter Contact 2), Child Lock (thermostats, Shutter Contact 2 Plus), Pet Immunity (Motion Detector), Silent Mode (thermostat), Vibration detection (Shutter Contact 2 Plus), User Defined States |
| `valve` | Thermostat radiator valve (position, diagnostic) |

## Services / actions

| Action | Description |
|---|---|
| `bosch_shc.trigger_scenario` | Trigger a scenario by name |
| `bosch_shc.trigger_rawscan` | Dump raw JSON from the SHC controller (devices, services, rooms, scenarios, …) |
| `bosch_shc.smokedetector_check` | Trigger a self-test on a Smoke Detector entity |
| `bosch_shc.smokedetector_alarmstate` | Set the alarm state on a Smoke Detector entity |

## Events

- `bosch_shc.event` — button presses (Universal Switch: lower/upper button, short/long press), scenario triggers, motion events, smoke detector alarms.

## Installation

### Via HACS (recommended)

1. Open HACS in Home Assistant.
2. Search for **Bosch SHC** under Integrations and install it.
3. Restart Home Assistant.
4. Go to **Settings → Devices & Services** and configure the **Bosch SHC** integration.

### Manual

1. Copy the `boschshc-hass/custom_components/bosch_shc/` folder into
   `<config>/custom_components/bosch_shc/`.
2. Restart Home Assistant.
3. Follow the [Configuration](#configuration) steps below.

## Configuration

The integration supports auto-discovery via **zeroconf** (mDNS). If the SHC is on the same
network as Home Assistant it will appear automatically in **Settings → Devices & Services →
Discovered**. If not, add it manually with **+ Add integration → Bosch SHC**.

### Initial setup steps

1. **Discovered integration** — press *Configure* to start.
2. **Confirm host** — before clicking *Submit*, press the button on the front of the SHC
   until the LEDs start flashing. This puts the controller into client-registration mode.
3. **System password** — enter the system password you set when you first set up the SHC.
4. **Done** — the integration creates the entry and discovers all paired devices.

<img src='images/config_step1.png' alt='Discovered integration.' width='235pt'/>
<img src='images/config_step2.png' alt='Confirmation of host.' width='477pt'/>
<img src='images/config_step3.png' alt='Enter system password.' width='315pt'/>
<img src='images/config_step4.png' alt='Configuration complete.' width='474pt'/>

### Reconfigure (host change or certificate re-pair)

Open **Settings → Devices & Services → Bosch SHC → ⋮ → Reconfigure**. A menu offers:

- **Change host / IP** — update the SHC address without re-pairing.
- **Re-pair (regenerate certificate)** — full re-registration for a new SHC or after a factory reset.

### Options flow

After setup, open **Settings → Devices & Services → Bosch SHC → Configure** to adjust:

- **Features section** — toggle *Scenarios as buttons* (expose each scenario as a button
  entity) and *Diagnostic entities* (granular battery level sensors, valve-tappet sensors,
  communication quality sensors).
- **Presence-based child lock section** — choose a presence entity and the "home" state;
  when no one is home, child lock is automatically enabled on all supported devices.
- **Advanced section** — SSL hostname verification toggle, long-poll timeout.

### Translations

UI strings are available in 30 languages (bg, ca, cs, de, el, en, es, es-419, et, fr, he,
hu, id, it, ja, ko, lv, nb, nl, no, pl, pt, pt-BR, ru, sk, sv, tr, uk, zh-Hans, zh-Hant).

## Quality

This integration targets the [Home Assistant Bronze quality scale](https://developers.home-assistant.io/docs/integration_quality_scale_index/).
All Bronze rules are implemented except `brands` (pending PR to home-assistant/brands).

- `local_push` IoT class — no cloud, no polling (except camera-type devices which only update on poll).
- Config flow with zeroconf discovery, re-auth, reconfigure, and options flow.
- Unique config-entry enforcement, test-before-configure validation.
- `runtime_data` pattern, `has_entity_name = True` on all entities.
- Domain-level actions (`trigger_scenario`, `trigger_rawscan`) available even when an entry fails to load.

## Reporting a bug

The fastest path to a fix is data. In order of convenience:

1. **Download diagnostics** (easiest) — **Settings → Devices & Services → Bosch SHC → ⋮ →
   Download diagnostics**. Produces a redacted JSON snapshot of every device and its raw service
   state (credentials, host/IP, MAC, and serials removed automatically).
2. **A rawscan** of the specific device — see below.
3. **Debug logs** while reproducing the problem — see below.

Attach the relevant file to the GitHub issue and remove anything you consider private.

## Creating a rawscan (for bug reports)

A rawscan is the raw JSON the Bosch controller exposes for a device. It is the single most useful
thing to attach when a device behaves wrong.

**1. Find the device id**

In **Developer Tools → Actions**, run:

```yaml
action: bosch_shc.trigger_rawscan
data:
  command: devices
```

Find the device and copy its `"id"` (e.g. `hdm:HomeMaticIP:3014...`).

**2. Dump that device's services**

```yaml
action: bosch_shc.trigger_rawscan
data:
  command: device_services
  device_id: hdm:ZigBee:000d6f0000abcdef
```

Available `command` values: `devices`, `device_services`, `device_service`
(needs `device_id` + `service_id`), `services`, `scenarios`, `rooms`,
`information`, `public_information`, `intrusion_detection`, `userdefinedstates`.

For setups with more than one SHC, add `title: <controller name>`.

> The same data is available from the command line via the `boschshc_rawscan` script shipped
> with [`boschshcpy`](https://github.com/tschamm/boschshcpy) (requires the client certificate + key).

## Enabling debug logs (for bug reports)

**Easiest (UI):** go to **Settings → Devices & Services → Bosch SHC → ⋮ → Enable debug logging**.
Reproduce the problem, then choose **Disable debug logging** — Home Assistant downloads the log
automatically. This captures both the integration and the underlying `boschshcpy` library logs.

**YAML alternative:**

```yaml
logger:
  default: info
  logs:
    custom_components.bosch_shc: debug
    boschshcpy: debug
```

## Removal

1. Go to **Settings → Devices & Services**, find **Bosch SHC** and click **⋮ → Delete**.
2. Restart Home Assistant.
3. **Manual cleanup:** the integration writes client certificate and key files to `/config/bosch_shc/`
   (`bosch_shc-cert_<hostname>.pem` / `bosch_shc-key_<hostname>.pem`). These are **not** deleted
   automatically — remove them manually if you no longer use the integration.

## Known limitations

- Encrypted SSL private keys are not supported (limitation of the `requests` library).
- The integration is not yet fully async (async migration in progress).
- New devices added to the SHC require reloading the integration before they appear in Home Assistant.
- Arming/disarming the alarm control panel does not support a PIN code.
- Client certificate renewal is manual. A warning (log + persistent notification) appears 30 days before
  expiry; after expiry the integration requires re-auth (put controller in pairing mode and reconfigure).

## Maintainers / support

| Role | GitHub |
|---|---|
| Original author & maintainer | [@tschamm](https://github.com/tschamm) |
| Co-maintainer | [@mosandlt](https://github.com/mosandlt) |

Community discussion: [Bosch Smart Home thread on the HA community forum](https://community.home-assistant.io/t/bosch-smart-home/115864).

[![Buy tschamm a coffee][buymecoffeebadge-tschamm]][buymecoffee-tschamm]
[![Buy mosandlts a coffee][buymecoffeebadge-mosandlts]][buymecoffee-mosandlts]

[buymecoffee-tschamm]: https://www.buymeacoffee.com/tschamm
[buymecoffeebadge-tschamm]: https://img.shields.io/badge/buy%20tschamm%20a%20double%20espresso-donate-yellow.svg
[buymecoffee-mosandlts]: https://buymeacoffee.com/mosandlts
[buymecoffeebadge-mosandlts]: https://img.shields.io/badge/buy%20mosandlts%20a%20coffee-donate-yellow.svg
[hassfestbadge]: https://github.com/tschamm/boschshc-hass/workflows/Validate%20with%20hassfest/badge.svg
[hassfest]: https://github.com/tschamm/boschshc-hass/actions/workflows/hassfest.yaml
[validatehacsbadge]: https://github.com/tschamm/boschshc-hass/workflows/Validate%20for%20HACS/badge.svg
[validatehacs]: https://github.com/tschamm/boschshc-hass/actions/workflows/hacs.yaml
[hacs]: https://github.com/custom-components/hacs
[hacsbadge]: https://img.shields.io/badge/HACS-Default-orange.svg
[bosch_shc]: https://github.com/tschamm/boschshc-hass
[stars-shield]: https://img.shields.io/github/stars/tschamm/boschshc-hass
