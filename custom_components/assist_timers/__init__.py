"""Set up timer storage/API/media; related files: manifest, runtime, and LLM."""

import logging
from pathlib import Path
from uuid import uuid4

import probatio
from homeassistant.components.http import StaticPathConfig
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
    DOMAIN,
    MAX_DURATION,
    STORAGE_VERSION,
)
from .llm import register_api
from .runtime import Endpoint, Runtime
from .satellite import SatelliteOutput, validate_entities
from .timer_manager import TimerError, TimerManager, duration_text

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Serve only the bundled alarm and register the shared tool provider."""
    runtime = Runtime()
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
    _LOGGER.info("Assist Timers ready for %s", satellite)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Stop owned alarm output and remove the endpoint during unload."""
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
    return True


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove persisted timer data only when the configuration is deleted."""
    await Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}").async_remove()


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
