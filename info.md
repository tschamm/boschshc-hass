[![Stars][stars-shield]][bosch_shc]
[![hacs][hacsbadge]][hacs]

[![BuyMeCoffee][buymecoffeebadge-tschamm]][buymecoffee-tschamm]
[![BuyMeCoffee][buymecoffeebadge-mosandlts]][buymecoffee-mosandlts]

[![Discord][discord-shield]][discord]
[![Community Forum][forum-shield]][forum]

{% if prerelease %}
### NB!: This is a Beta version!

{% endif %}

_Custom integration for [Bosch Smart Home][bosch_smart_home] — local-only, no cloud required._

![Bosch Smart Home][bosch_smart_home_icon]

**This component sets up the following platforms.**

| Platform | Description |
| --- | --- |
| `alarm_control_panel` | Intrusion Detection System |
| `binary_sensor` | Shutter Contact (Gen 1 + Gen 2), Motion Detector (Gen 1 + Gen 2 [+M]), Smoke Detector, Smoke Detection System, Water Leakage Sensor, vibration (SC2+), battery state |
| `button` | Micromodule Relay (impulse type) |
| `climate` | Room Climate Control, Heating Circuit |
| `cover` | Shutter Control, Micromodule Shutter / Awning / Blinds |
| `event` | Universal Switch button presses, Scenario events, Motion events, Smoke Detector alarm events |
| `light` | LEDVANCE lights, Hue lights (via SHC), Micromodule Dimmer, Motion Detector II light |
| `number` | Thermostat temperature offset |
| `sensor` | Temperature, Humidity, CO₂ (Twinguard), Air quality, Energy + Power, Illuminance, EMMA grid power, Battery level (diagnostic) |
| `switch` | Smart Plug, Smart Plug Compact, Light Control, Micromodule Relay, cameras (privacy/light/notification), Presence Simulation, Child Lock, Pet Immunity, User Defined States |
| `valve` | Thermostat radiator valve (position) |

{% if not installed %}
## Installation

1. Click install.
2. Restart Home Assistant.
3. Go to **Settings → Devices & Services** — your SHC will be auto-discovered.
   If not, click **+ Add integration** and search for **Bosch SHC**.

{% endif %}

## Configuration is done in the UI

Config flow supports: zeroconf auto-discovery, manual entry, re-auth, reconfigure
(host change or certificate re-pair), and an options flow with presence-based child lock,
diagnostic-entity toggle, scenarios-as-buttons, and advanced TLS/timeout settings.

<a href="https://www.buymeacoffee.com/tschamm" target="_blank"><img src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png" alt="Buy tschamm a Coffee" style="height: 60px !important;width: 217px !important;" ></a>
<a href="https://buymeacoffee.com/mosandlts" target="_blank"><img src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png" alt="Buy mosandlts a Coffee" style="height: 60px !important;width: 217px !important;" ></a>

***

[bosch_smart_home]: https://github.com/BoschSmartHome/bosch-shc-api-docs
[bosch_smart_home_icon]: https://www.home-connect-plus.com/wp-content/uploads/logo-bosch-smart-home-en-website.png
[bosch_shc]: https://github.com/tschamm/boschshc-hass
[stars-shield]: https://img.shields.io/github/stars/tschamm/boschshc-hass
[buymecoffee-tschamm]: https://www.buymeacoffee.com/tschamm
[buymecoffeebadge-tschamm]: https://img.shields.io/badge/buy%20tschamm%20a%20double%20espresso-donate-yellow.svg
[buymecoffee-mosandlts]: https://buymeacoffee.com/mosandlts
[buymecoffeebadge-mosandlts]: https://img.shields.io/badge/buy%20mosandlts%20a%20coffee-donate-yellow.svg
[hacs]: https://github.com/custom-components/hacs
[hacsbadge]: https://img.shields.io/badge/HACS-Default-orange.svg
[discord]: https://discord.gg/Qa5fW2R
[discord-shield]: https://img.shields.io/discord/330944238910963714.svg
[forum-shield]: https://img.shields.io/badge/community-forum-brightgreen.svg
[forum]: https://community.home-assistant.io/
