"""Platform for button integration."""

from boschshcpy import (
    SHCDevice,
    SHCSession,
)

from homeassistant.components.button import (
    ButtonEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback


from .const import (
    DATA_SESSION,
    DOMAIN,
    LOGGER,
    OPT_SCENARIOS_AS_BUTTONS,
)
from .entity import SHCEntity, device_excluded

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the SHC binary sensor platform."""
    entities = []
    session: SHCSession = hass.data[DOMAIN][config_entry.entry_id][DATA_SESSION]

    for button in session.device_helper.micromodule_impulse_relays:
        if device_excluded(button, config_entry.options):
            continue
        entities.append(
            SHCRelayButton(
                device=button,
                entry_id=config_entry.entry_id,
            )
        )

    if config_entry.options.get(OPT_SCENARIOS_AS_BUTTONS, False):
        entry_unique_id = config_entry.unique_id
        entry_id = config_entry.entry_id
        for scenario in session.scenarios:
            try:
                entities.append(
                    SHCScenarioButton(
                        scenario=scenario,
                        entry_unique_id=entry_unique_id,
                        entry_id=entry_id,
                    )
                )
            except (KeyError, AttributeError) as err:
                # A malformed scenario payload must not take out the whole
                # button platform — skip just that scenario.
                LOGGER.warning("Skipping scenario button (bad payload): %s", err)

    if entities:
        async_add_entities(entities)


class SHCRelayButton(SHCEntity, ButtonEntity):
    """Representation of a SHC button."""

    def __init__(
        self,
        device: SHCDevice,
        entry_id: str,
        attr_name: str | None = None,
    ) -> None:
        """Initialize a SHC switch."""
        super().__init__(device, entry_id)
        self._attr_name = None if attr_name is None else attr_name
        self._attr_unique_id = (
            f"{device.root_device_id}_{device.id}"
            if attr_name is None
            else f"{device.root_device_id}_{device.id}_{attr_name.lower()}"
        )

    def press(self) -> None:
        """Triggers impulse."""
        self._device.trigger_impulse_state()


class SHCScenarioButton(ButtonEntity):
    """Button entity that triggers a single Bosch SHC scenario.

    Scenarios are not SHC devices, so this entity does NOT inherit SHCEntity.
    unique_id is scoped to the config entry so each SHC controller gets its
    own set of scenario buttons even when multiple controllers are present.
    """

    _attr_icon = "mdi:script-text-play"
    _attr_should_poll = False

    def __init__(self, scenario, entry_unique_id: str | None, entry_id: str) -> None:
        """Initialize a scenario button."""
        self._scenario = scenario
        prefix = entry_unique_id if entry_unique_id else entry_id
        self._attr_unique_id = f"{prefix}_scenario_{scenario.id}"
        self._attr_name = scenario.name

    def press(self) -> None:
        """Trigger the scenario (runs in executor — scenario.trigger() is sync)."""
        self._scenario.trigger()
