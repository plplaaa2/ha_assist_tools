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
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType

from .const import (
    ALARM_FILE,
    ALARM_URL,
    CONF_PLAYER,
    CONF_SATELLITE,
    CONF_WEATHER_ENTITY,
    DOMAIN,
    MAX_DURATION,
    STORAGE_VERSION,
)
from .device_control import DeviceControl
from .llm import register_api
from .runtime import Endpoint, Runtime
from .satellite import SatelliteOutput, validate_entities
from .scheduled_actions import ScheduledActions
from .timer_manager import TimerError, TimerManager, duration_text
from .weather_tools import AssistWeatherAPI

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Serve only the bundled alarm and register the separate tool providers."""
    runtime = Runtime()
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
    satellite = entry.data[CONF_SATELLITE]
    player = entry.data[CONF_PLAYER]
    registry_entry = er.async_get(hass).async_get(satellite)
    if registry_entry is None or registry_entry.device_id is None:
        raise ConfigEntryNotReady("Satellite must be registered with a device")
    try:
        validate_entities(hass, satellite, player)
    except TimerError as err:
        raise ConfigEntryNotReady(str(err)) from err
    runtime: Runtime = hass.data[DOMAIN]
    if any(ep.device_id == registry_entry.device_id for ep in runtime.endpoints.values()):
        raise ConfigEntryNotReady("This satellite device already has a timer configuration")
    if any(ep.output.player == player for ep in runtime.endpoints.values()):
        raise ConfigEntryNotReady("This media player already belongs to another satellite timer")
    store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
    output = SatelliteOutput(hass, satellite, player)
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
    runtime.endpoints[entry.entry_id] = endpoint
    try:
        async with runtime.actions_lock:
            if runtime.actions is None:
                action_store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.scheduled_actions")
                actions = ScheduledActions(runtime.control.async_execute, action_store.async_save)
                try:
                    await actions.async_restore(await action_store.async_load())
                except Exception:
                    await actions.async_close()
                    raise
                runtime.actions = actions
    except Exception as err:
        runtime.endpoints.pop(entry.entry_id, None)
        await endpoint.async_close()
        raise ConfigEntryNotReady("Scheduled action state could not be restored") from err
    _LOGGER.info("Assist Tools ready for %s", satellite)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Stop owned alarm output and remove the endpoint during unload."""
    if entry.data.get(CONF_WEATHER_ENTITY):
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
        await endpoint.async_close()
    runtime.endpoints.pop(entry.entry_id, None)
    async with runtime.actions_lock:
        if not runtime.endpoints and runtime.actions is not None:
            await runtime.actions.async_close()
            runtime.actions = None
    return True


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove persisted timer data only when the configuration is deleted."""
    if entry.data.get(CONF_WEATHER_ENTITY):
        return
    await Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}").async_remove()
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
            duration = duration_text(result["duration_seconds"])
            endings = {
                "pause": "을 일시정지하였습니다.",
                "resume": "을 재개하였습니다.",
                "cancel": "을 중지하였습니다.",
            }
            endpoint.queue_announcement(
                f"설정한 {duration} 알람{endings[operation]}",
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
                probatio.Required("action"): probatio.In(["turn_on", "turn_off"]),
                probatio.Required("delay_seconds"): probatio.All(
                    int, probatio.Range(min=1, max=MAX_DURATION)
                ),
            }
        ),
        "list_actions": probatio.Schema({}),
        "cancel_action": probatio.Schema({probatio.Required("job_id"): str}),
    }
    for name, schema in schemas.items():
        hass.services.async_register(
            DOMAIN, name, handle, schema=schema, supports_response=SupportsResponse.OPTIONAL
        )
