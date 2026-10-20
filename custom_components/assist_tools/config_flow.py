"""Configure explicit satellite/player bindings; related files: setup/UI.

One device and one media player can belong to only one entry to prevent alarms
from different satellites stopping each other's playback.
"""

import probatio
from homeassistant import config_entries
from homeassistant.components.weather import DOMAIN as WEATHER_DOMAIN
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    CONF_PLAYER,
    CONF_SATELLITE,
    CONF_TV_APPS,
    CONF_TV_METHOD,
    CONF_TV_PLAYER,
    CONF_TV_REMOTE,
    CONF_WEATHER_ENTITY,
    DOMAIN,
)
from .satellite import validate_entities
from .timer_manager import TimerError


def schema(
    defaults: dict | None = None, fields: tuple = (CONF_SATELLITE, CONF_PLAYER)
) -> probatio.Schema:
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
            if key in fields
        }
    )


def weather_schema(default: str | None = None) -> probatio.Schema:
    """Select the weather entity used by Assist Weather; related: weather_tools."""
    return probatio.Schema(
        {
            probatio.Required(
                CONF_WEATHER_ENTITY, **({"default": default} if default else {})
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain=WEATHER_DOMAIN))
        }
    )


def tv_schema(defaults: dict | None = None, fields: tuple = ()) -> probatio.Schema:
    """Build Google TV selectors; related files: google_tv_tools and translations."""
    defaults = defaults or {}
    schema_fields = {}
    if CONF_TV_METHOD in fields:
        schema_fields[
            probatio.Required(
                CONF_TV_METHOD,
                **({"default": defaults[CONF_TV_METHOD]} if CONF_TV_METHOD in defaults else {}),
            )
        ] = SelectSelector(
            SelectSelectorConfig(
                options=[
                    {"value": "remote", "label": "Android TV Remote"},
                    {"value": "adb", "label": "Android Debug Bridge (ADB)"},
                ],
                mode=SelectSelectorMode.DROPDOWN,
            )
        )
    if CONF_TV_REMOTE in fields:
        schema_fields[
            probatio.Required(
                CONF_TV_REMOTE,
                **({"default": defaults[CONF_TV_REMOTE]} if CONF_TV_REMOTE in defaults else {}),
            )
        ] = selector.EntitySelector(selector.EntitySelectorConfig(domain="remote"))
    if CONF_TV_PLAYER in fields:
        schema_fields[
            probatio.Required(
                CONF_TV_PLAYER,
                **({"default": defaults[CONF_TV_PLAYER]} if CONF_TV_PLAYER in defaults else {}),
            )
        ] = selector.EntitySelector(selector.EntitySelectorConfig(domain="media_player"))
    if CONF_TV_APPS in fields:
        schema_fields[
            probatio.Required(
                CONF_TV_APPS,
                **({"default": defaults[CONF_TV_APPS]} if CONF_TV_APPS in defaults else {}),
            )
        ] = probatio.All(str, probatio.Length(min=1, max=4000))
    return probatio.Schema(schema_fields)


def matching_player(hass, satellite_id: str) -> str | None:
    """Match a unique display name; related files: satellite.py and translations.

    Ignore unusable/claimed players, and never guess between multiple matches.
    """
    satellite = hass.states.get(satellite_id)
    if satellite is None:
        return None
    name = satellite.name.strip().casefold()
    used = {
        entry.data[CONF_PLAYER]
        for entry in hass.config_entries.async_entries(DOMAIN)
        if CONF_PLAYER in entry.data
    }
    matches = []
    for player in hass.states.async_all("media_player"):
        if player.name.strip().casefold() != name or player.entity_id in used:
            continue
        if player.state in ("unavailable", "unknown"):
            continue
        try:
            validate_entities(hass, satellite_id, player.entity_id)
        except TimerError:
            continue
        matches.append(player.entity_id)
    return matches[0] if len(matches) == 1 else None


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
        satellite_id = entry.data.get(CONF_SATELLITE)
        player_id = entry.data.get(CONF_PLAYER)
        if satellite_id is None or player_id is None:
            continue
        other = registry.async_get(satellite_id)
        if other and other.device_id == satellite.device_id:
            raise TimerError("satellite_already_configured")
        if player_id == data[CONF_PLAYER]:
            raise TimerError("player_already_configured")
    return satellite.device_id


class AssistTimersConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configure a satellite timer or a selected weather provider."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        if user_input is None:
            return self.async_show_form(
                step_id="user",
                data_schema=probatio.Schema(
                    {
                        probatio.Required("setup_type"): SelectSelector(
                            SelectSelectorConfig(
                                options=[
                                    {"value": "timer", "label": "Assist Timer"},
                                    {"value": "weather", "label": "Assist Weather"},
                                    {"value": "google_tv", "label": "Assist Google TV"},
                                ],
                                mode=SelectSelectorMode.DROPDOWN,
                            )
                        )
                    }
                ),
            )
        if user_input.get("setup_type") == "weather" or "weather_entity" in user_input:
            return await self.async_step_weather(
                {key: value for key, value in user_input.items() if key != "setup_type"} or None
            )
        if user_input.get("setup_type") == "google_tv" or CONF_TV_METHOD in user_input:
            return await self.async_step_google_tv_method(
                {key: value for key, value in user_input.items() if key != "setup_type"} or None
            )
        if "setup_type" in user_input:
            return await self.async_step_satellite()
        return await self.async_step_satellite(user_input)

    async def async_step_satellite(self, user_input=None):
        errors = {}
        if user_input is not None:
            user_input = dict(user_input)
            if CONF_PLAYER not in user_input:
                player = matching_player(self.hass, user_input[CONF_SATELLITE])
                if player is None:
                    self._satellite_input = user_input
                    return await self.async_step_player()
                user_input[CONF_PLAYER] = player
            try:
                device_id = validate(self.hass, user_input)
            except TimerError as err:
                errors["base"] = str(err)
            else:
                await self.async_set_unique_id(device_id)
                self._abort_if_unique_id_configured()
                state = self.hass.states.get(user_input[CONF_SATELLITE])
                return self.async_create_entry(title=state.name, data=user_input)
        return self.async_show_form(
            step_id="satellite", data_schema=schema(user_input, (CONF_SATELLITE,)), errors=errors
        )

    async def async_step_player(self, user_input=None):
        """Ask for a player only when automatic matching cannot select one."""
        if user_input is not None:
            return await self.async_step_user({**self._satellite_input, **user_input})
        return self.async_show_form(step_id="player", data_schema=schema(fields=(CONF_PLAYER,)))

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

    async def async_step_weather(self, user_input=None):
        """Select one unique weather entity for the Assist Weather provider."""
        errors = {}
        if user_input is not None:
            entity_id = user_input[CONF_WEATHER_ENTITY]
            state = self.hass.states.get(entity_id)
            if not entity_id.startswith(f"{WEATHER_DOMAIN}.") or state is None:
                errors["base"] = "entity_not_found"
            else:
                await self.async_set_unique_id(entity_id)
                self._abort_if_unique_id_configured()
                title = state.attributes.get("friendly_name", entity_id)
                return self.async_create_entry(title=title, data=user_input)
        return self.async_show_form(
            step_id="weather",
            data_schema=weather_schema(user_input.get(CONF_WEATHER_ENTITY) if user_input else None),
            errors=errors,
        )

    async def async_step_google_tv_method(self, user_input=None):
        if user_input is not None:
            self._tv_method = user_input[CONF_TV_METHOD]
            return await self.async_step_google_tv_entities()
        return self.async_show_form(
            step_id="google_tv_method",
            data_schema=tv_schema(fields=(CONF_TV_METHOD,)),
        )

    async def async_step_google_tv_entities(self, user_input=None):
        method = getattr(self, "_tv_method", "remote")
        fields = (
            (CONF_TV_REMOTE, CONF_TV_APPS)
            if method == "remote"
            else (
                CONF_TV_PLAYER,
                CONF_TV_APPS,
            )
        )
        errors = {}
        if user_input is not None:
            import json

            try:
                apps = json.loads(user_input[CONF_TV_APPS])
                if not isinstance(apps, list) or not apps or len(apps) > 30:
                    raise ValueError
                if any(
                    not isinstance(app, dict)
                    or not isinstance(app.get("name"), str)
                    or not app["name"].strip()
                    or not isinstance(app.get("launch"), str)
                    or not app["launch"].strip()
                    for app in apps
                ):
                    raise ValueError
            except ValueError, TypeError:
                errors[CONF_TV_APPS] = "invalid_apps"
            else:
                data = {CONF_TV_METHOD: method, **user_input, CONF_TV_APPS: apps}
                entity_id = data[CONF_TV_REMOTE if method == "remote" else CONF_TV_PLAYER]
                state = self.hass.states.get(entity_id)
                if state is None:
                    errors["base"] = "entity_not_found"
                else:
                    await self.async_set_unique_id(f"google_tv_{method}_{entity_id}")
                    self._abort_if_unique_id_configured()
                    name = state.attributes.get("friendly_name", state.name)
                    return self.async_create_entry(title=f"Google TV: {name}", data=data)
        return self.async_show_form(
            step_id="google_tv_entities",
            data_schema=tv_schema(
                {
                    **(user_input or {}),
                    CONF_TV_APPS: (user_input or {}).get(
                        CONF_TV_APPS, '[{"name":"YouTube","launch":"https://www.youtube.com"}]'
                    ),
                },
                fields,
            ),
            errors=errors,
        )
