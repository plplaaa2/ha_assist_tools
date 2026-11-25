"""Set up alarms and device jobs; related files: runtime, LLM, and device_control."""

import logging
from pathlib import Path
from uuid import uuid4

import probatio
from homeassistant.components.http import StaticPathConfig
from homeassistant.components.weather import DOMAIN as WEATHER_DOMAIN
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import ConfigEntryNotReady, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import dt as dt_util

from .action_tools import RESCHEDULE_FIELDS, weekly_fields
from .const import (
    ALARM_ACTION,
    ALARM_FILE,
    ALARM_URL,
    CONF_EMERGENCY_ACTION,
    CONF_GOOGLE_SPEAKER,
    CONF_LANGUAGE,
    CONF_PLAYER,
    CONF_SATELLITE,
    CONF_WEATHER_ENTITY,
    DOMAIN,
    MAX_DURATION,
    STORAGE_VERSION,
)
from .device_control import DeviceControl
from .emergency_tools import AssistEmergencyAPI
from .llm import register_api
from .runtime import Endpoint, Runtime
from .satellite import SatelliteOutput, validate_entities
from .scheduled_actions import SCHEDULED_ACTIONS, ScheduledActions
from .timer_manager import TimerError, TimerManager, timer_announcement
from .weather_tools import AssistWeatherAPI

_LOGGER = logging.getLogger(__name__)

# Declare config-entry setup; related: config_flow.py and manifest.json.
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Serve only the bundled alarm and register the separate tool providers."""
    runtime = Runtime()
    # Restore one shared language before providers are registered; related: config_flow.py.
    runtime.language_store = Store(hass, 1, f"{DOMAIN}.language")
    saved_language = await runtime.language_store.async_load()
    if isinstance(saved_language, dict) and saved_language.get(CONF_LANGUAGE) in ("ko", "en"):
        runtime.language = saved_language[CONF_LANGUAGE]
    runtime.control = DeviceControl(hass)
    hass.data[DOMAIN] = runtime
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                ALARM_URL, str(Path(__file__).parent / "alarm_sounds" / ALARM_FILE), True
            )
        ]
    )
    runtime.unregister_api = register_api(hass)

    async def shutdown(_event) -> None:
        if runtime.actions is not None:
            await runtime.actions.async_close()
        for endpoint in list(runtime.endpoints.values()):
            try:
                await endpoint.async_close()
            except Exception:
                _LOGGER.exception("Timer shutdown failed")
        if runtime.unregister_api:
            runtime.unregister_api()
            runtime.unregister_api = None

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, shutdown)
    register_services(hass, runtime)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Restore one timer for its configured satellite and media player."""
    # Register the configured emergency provider; related: emergency_tools.py and config_flow.py.
    if CONF_EMERGENCY_ACTION in entry.data:
        from homeassistant.helpers.llm import async_register_api

        entry.async_on_unload(
            async_register_api(hass, AssistEmergencyAPI(hass, entry.data[CONF_EMERGENCY_ACTION]))
        )
        return True
    if entry.data.get(CONF_WEATHER_ENTITY):
        entity_id = entry.data[CONF_WEATHER_ENTITY]
        state = hass.states.get(entity_id)
        if state is None or not entity_id.startswith(f"{WEATHER_DOMAIN}."):
            raise ConfigEntryNotReady("Selected weather entity is not available")
        from homeassistant.helpers.llm import async_register_api

        unregister = async_register_api(
            hass,
            AssistWeatherAPI(hass, entity_id, state.attributes.get("friendly_name", entity_id)),
        )
        entry.async_on_unload(unregister)
        return True
    if CONF_SATELLITE not in entry.data:
        _LOGGER.warning(
            "Skipping unsupported Assist Tools entry %s",
            entry.entry_id,
        )
        return True
    satellite = entry.data[CONF_SATELLITE]
    player = entry.data[CONF_PLAYER]
    # Share the configured output mode with playback validation; related: config_flow.py, satellite.py.
    google_speaker = entry.data.get(CONF_GOOGLE_SPEAKER, False)
    registry_entry = er.async_get(hass).async_get(satellite)
    if registry_entry is None or registry_entry.device_id is None:
        raise ConfigEntryNotReady("Satellite must be registered with a device")
    try:
        validate_entities(hass, satellite, player, google_speaker=google_speaker)
    except TimerError as err:
        raise ConfigEntryNotReady(str(err)) from err
    runtime: Runtime = hass.data[DOMAIN]
    # Serialize ownership checks and publish only after restoration; related: runtime.py.
    async with runtime.actions_lock:
        if any(ep.device_id == registry_entry.device_id for ep in runtime.endpoints.values()):
            raise ConfigEntryNotReady("This satellite device already has a timer configuration")
        if any(ep.output.player == player for ep in runtime.endpoints.values()):
            raise ConfigEntryNotReady(
                "This media player already belongs to another satellite timer"
            )
        store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        output = SatelliteOutput(
            hass,
            satellite,
            player,
            google_speaker=google_speaker,
            sounds=lambda: getattr(entry, "options", {}),
        )
        manager = TimerManager(output, store.async_save)
        endpoint = Endpoint(registry_entry.device_id, manager, output)
        try:
            await manager.async_restore(await store.async_load())
        except Exception as err:
            try:
                await endpoint.async_close()
            except Exception:
                _LOGGER.exception("Could not stop alarm during failed state restoration")
            raise ConfigEntryNotReady("Timer state could not be restored") from err
        try:
            zone = await dt_util.async_get_time_zone(hass.config.time_zone)
            if zone is None:
                raise ConfigEntryNotReady("Home Assistant time zone is unavailable")
            alarm_store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}.alarms")
            endpoint.alarms = ScheduledActions(
                endpoint.async_execute_alarm,
                alarm_store.async_save,
                zone=zone,
                allowed_actions=(ALARM_ACTION,),
            )
            saved_alarms = await alarm_store.async_load()
            if isinstance(saved_alarms, list) and any(
                isinstance(job, dict)
                and job.get("state") == "executing"
                and job.get("action") == ALARM_ACTION
                and job.get("entity_id") == player
                for job in saved_alarms
            ):
                # An interrupted alarm may still be audible. Stop its saved individual output.
                try:
                    output.validate_alarm_player(player)
                    await output.async_stop()
                except Exception:
                    _LOGGER.exception("Could not stop interrupted clock alarm on %s", player)
            await endpoint.alarms.async_restore(saved_alarms)
            if runtime.actions is None:
                action_store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.scheduled_actions")
                # Use HA local time for recurring jobs; related: scheduled_actions.py, llm.py.
                actions = ScheduledActions(
                    runtime.control.async_execute, action_store.async_save, zone=zone
                )
                try:
                    await actions.async_restore(await action_store.async_load())
                except Exception:
                    await actions.async_close()
                    raise
                runtime.actions = actions
        except Exception as err:
            await endpoint.async_close()
            raise ConfigEntryNotReady("Scheduled action state could not be restored") from err
        runtime.endpoints[entry.entry_id] = endpoint
    _LOGGER.info("Assist Tools ready for %s", satellite)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Stop owned alarm output and remove the endpoint during unload."""
    # Provider-only entries have no timer endpoint; related: emergency_tools.py and weather_tools.py.
    if CONF_EMERGENCY_ACTION in entry.data or entry.data.get(CONF_WEATHER_ENTITY):
        return True
    runtime: Runtime = hass.data[DOMAIN]
    endpoint = runtime.endpoints.get(entry.entry_id)
    if (
        endpoint
        and endpoint.manager.timer
        and endpoint.manager.timer.state in ("ringing", "alarm_failed")
    ):
        try:
            await endpoint.manager.async_cancel(endpoint.manager.timer.timer_id)
        except Exception:
            _LOGGER.exception("Could not stop the timer output; unload refused")
            return False
    if endpoint:
        try:
            await endpoint.async_close()
        except Exception:
            _LOGGER.exception("Could not stop clock alarm playback; unload refused")
            return False
    runtime.endpoints.pop(entry.entry_id, None)
    async with runtime.actions_lock:
        if not runtime.endpoints and runtime.actions is not None:
            await runtime.actions.async_close()
            runtime.actions = None
    return True


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove persisted timer data only when the configuration is deleted."""
    # Provider-only entries have no timer storage; related: emergency_tools.py and weather_tools.py.
    if CONF_EMERGENCY_ACTION in entry.data or entry.data.get(CONF_WEATHER_ENTITY):
        return
    await Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}").async_remove()
    await Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}.alarms").async_remove()
    if not any(
        other.entry_id != entry.entry_id for other in hass.config_entries.async_entries(DOMAIN)
    ):
        await Store(hass, STORAGE_VERSION, f"{DOMAIN}.scheduled_actions").async_remove()


def register_services(hass: HomeAssistant, runtime: Runtime) -> None:
    """Expose the same lifecycle for explicit HA actions and manual testing."""

    async def handle(call: ServiceCall) -> dict | None:
        matches = [
            ep
            for ep in runtime.endpoints.values()
            if ep.output.satellite == call.data[CONF_SATELLITE]
        ]
        if len(matches) != 1:
            raise ServiceValidationError("Satellite is not configured")
        endpoint = matches[0]
        operation = "cancel" if call.service == "stop" else call.service
        args = {key: value for key, value in call.data.items() if key != CONF_SATELLITE}
        try:
            result = await endpoint.async_operation(operation, args, uuid4().hex)
        except TimerError as err:
            raise ServiceValidationError(str(err)) from err
        if operation not in ("create", "status") and not result.get("skip_announcement", False):
            endpoint.queue_announcement(
                timer_announcement(result["duration_seconds"], operation, runtime.language),
                result["state"],
                result.get("timer_id"),
            )
        return result if call.return_response else None

    for operation in ("create", "pause", "resume", "cancel", "stop", "status"):
        fields = {probatio.Required(CONF_SATELLITE): str}
        if operation == "create":
            fields.update(
                {
                    probatio.Required("duration_seconds"): probatio.All(
                        int, probatio.Range(min=1, max=MAX_DURATION)
                    ),
                    probatio.Optional("name", default=""): probatio.All(
                        str, probatio.Length(max=80)
                    ),
                }
            )
        else:
            fields[probatio.Optional("timer_id")] = probatio.All(
                str, probatio.Length(min=1, max=64)
            )
        hass.services.async_register(
            DOMAIN,
            operation,
            handle,
            schema=probatio.Schema(fields),
            supports_response=SupportsResponse.OPTIONAL,
        )
    register_action_services(hass, runtime)


def register_action_services(hass: HomeAssistant, runtime: Runtime) -> None:
    """Expose job creation/list/cancel; related files: action_tools and services.yaml."""
    from .action_tools import perform_action_operation

    async def handle(call: ServiceCall) -> dict | None:
        try:
            result = await perform_action_operation(
                runtime,
                call.service,
                dict(call.data),
                getattr(call.context, "user_id", None),
                uuid4().hex,
            )
        except TimerError as err:
            raise ServiceValidationError(str(err)) from err
        return result if call.return_response else None

    schemas = {
        "schedule_action": probatio.Schema(
            {
                probatio.Required("entity_id"): str,
                probatio.Required("action"): probatio.In(SCHEDULED_ACTIONS),
                probatio.Required("delay_seconds"): probatio.All(
                    int, probatio.Range(min=1, max=MAX_DURATION)
                ),
            }
        ),
        "list_actions": probatio.Schema({}),
        "cancel_action": probatio.Schema({probatio.Required("job_id"): str}),
        "schedule_weekly_action": probatio.Schema(
            {
                probatio.Required("entity_id"): str,
                probatio.Required("action"): probatio.In(SCHEDULED_ACTIONS),
                **weekly_fields(required=True),
            }
        ),
        "reschedule_action": probatio.Schema(RESCHEDULE_FIELDS),
    }
    for name, schema in schemas.items():
        hass.services.async_register(
            DOMAIN, name, handle, schema=schema, supports_response=SupportsResponse.OPTIONAL
        )
