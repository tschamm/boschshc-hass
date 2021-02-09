[![hacs][hacsbadge]][hacs]

[![Stars][stars-shield]][bosch_shc]
[![Project Maintenance][maintenance-shield]][user_profile]
[![BuyMeCoffee][buymecoffeebadge]][buymecoffee]

[![Discord][discord-shield]][discord]
[![Community Forum][forum-shield]][forum]

_Component to integrate with [Bosch Smart Home][bosch_smart_home] system._

![Bosch Smart Home][bosch_smart_home_icon]

**This component will set up the following platforms.**

Platform        | Description
----------------|------------------------------------
`alarm_control_panel` | Intrusion detection control system.
`binary_sensor` | Shutter contact, smoke detector, motion detector.
`climate` | Room climate control.
`light` | LEDVANCE lights.
`sensor`        | Thermostat, wall thermostat, twinguard, battery state of battery powered devices, smart plug and light control (energy and power).
`switch`        | Smart plug, light control.

{% if not installed %}
## Installation

1. Click install.
1. In the HA UI go to "Configuration" -> "Integrations" click "+" and search for "Bosch SHC".

{% endif %}


## Configuration is done in the UI

<!---->
<a href="https://www.buymeacoffee.com/tschamm" target="_blank"><img src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png" alt="Buy Me A Coffee" style="height: 45px !important;width: 163px !important;" ></a>
***

[bosch_smart_home]: https://github.com/BoschSmartHome/bosch-shc-api-docs
[bosch_smart_home_icon]: https://avatars.githubusercontent.com/u/56956610?s=100&v=4
[bosch_shc]: https://github.com/tschamm/boschshc-hass
[stars-shield]: https://img.shields.io/github/stars/tschamm/boschshc-hass?style=for-the-badge
[buymecoffee]: https://www.buymeacoffee.com/tschamm
[buymecoffeebadge]: https://img.shields.io/badge/buy%20me%20a%20coffee-donate-yellow.svg?style=for-the-badge
[hacs]: https://github.com/custom-components/hacs
[hacsbadge]: https://img.shields.io/badge/HACS-Default-orange.svg?style=for-the-badge
[discord]: https://discord.gg/Qa5fW2R
[discord-shield]: https://img.shields.io/discord/330944238910963714.svg?style=for-the-badge
[forum-shield]: https://img.shields.io/badge/community-forum-brightgreen.svg?style=for-the-badge
[forum]: https://community.home-assistant.io/
[license]: https://github.com/tschamm/boschshc-hass/blob/main/LICENSE
[maintenance-shield]: https://img.shields.io/badge/maintainer-Thomas%20Schamm%20%40%C2%A0tschamm-blue?style=for-the-badge
[user_profile]: https://github.com/tschamm