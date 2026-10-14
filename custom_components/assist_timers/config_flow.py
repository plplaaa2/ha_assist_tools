"""Configure explicit satellite/player bindings; related files: setup/UI.

One device and one media player can belong to only one entry to prevent alarms
from different satellites stopping each other's playback.
"""

import probatio
from homeassistant import config_entries
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector

from .const import CONF_PLAYER, CONF_SATELLITE, DOMAIN
from .satellite import validate_entities
from .timer_manager import TimerError


def schema(defaults: dict | None = None) -> probatio.Schema:
    """Use entity selectors for all explicit bindings."""
    defaults = defaults or {}
    return probatio.Schema(
        {
            probatio.Required(
                key, **({"default": defaults[key]} if key in defaults else {})
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain=domain))
            for key, domain in (
                (CONF_SATELLITE, "assist_satellite"),
                (CONF_PLAYER, "media_player"),
            )
        }
    )


def validate(hass, data: dict, entry_id: str | None = None) -> str:
    """Return the device ID after checking capabilities and unique ownership."""
    validate_entities(hass, data[CONF_SATELLITE], data[CONF_PLAYER])
    registry = er.async_get(hass)
    satellite = registry.async_get(data[CONF_SATELLITE])
    if satellite is None or satellite.device_id is None:
        raise TimerError("satellite_no_device")
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.entry_id == entry_id:
            continue
        other = registry.async_get(entry.data[CONF_SATELLITE])
        if other and other.device_id == satellite.device_id:
            raise TimerError("satellite_already_configured")
        if entry.data[CONF_PLAYER] == data[CONF_PLAYER]:
            raise TimerError("player_already_configured")
    return satellite.device_id


class AssistTimersConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Create one config entry per originating satellite device."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                device_id = validate(self.hass, user_input)
            except TimerError as err:
                errors["base"] = str(err)
            else:
                await self.async_set_unique_id(device_id)
                self._abort_if_unique_id_configured()
                state = self.hass.states.get(user_input[CONF_SATELLITE])
                return self.async_create_entry(title=state.name, data=user_input)
        return self.async_show_form(step_id="user", data_schema=schema(user_input), errors=errors)

    async def async_step_reconfigure(self, user_input=None):
        """Change the player without silently transferring a live timer."""
        entry = self._get_reconfigure_entry()
        errors = {}
        if user_input is not None:
            try:
                device_id = validate(self.hass, user_input, entry.entry_id)
                if device_id != entry.unique_id:
                    raise TimerError("cannot_change_satellite")
                runtime = self.hass.data.get(DOMAIN)
                endpoint = runtime.endpoints.get(entry.entry_id) if runtime else None
                if endpoint and endpoint.manager.timer is not None:
                    raise TimerError("cancel_timer_before_reconfigure")
            except TimerError as err:
                errors["base"] = str(err)
            else:
                return self.async_update_reload_and_abort(entry, data_updates=user_input)
        return self.async_show_form(
            step_id="reconfigure", data_schema=schema(user_input or entry.data), errors=errors
        )
