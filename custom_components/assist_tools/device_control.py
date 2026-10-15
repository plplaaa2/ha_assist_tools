"""Validate and execute explicit on/off targets; related files: scheduler and LLM.

Assist exposure and caller permissions are checked both now and at execution.
No arbitrary service names, scripts, areas, or raw spoken commands are accepted.
"""

import asyncio

from homeassistant.auth.permissions.const import POLICY_CONTROL
from homeassistant.components.homeassistant import exposed_entities
from homeassistant.core import Context

from .scheduled_actions import ScheduledAction
from .timer_manager import TimerError

DEVICE_DOMAINS = {"light", "switch", "fan", "climate", "media_player", "humidifier", "water_heater"}
# HA 2026.10.1 feature bits from each domain's const.py; zero means base on/off.
POWER_FEATURES = {
    "fan": {"turn_on": 32, "turn_off": 16},
    "climate": {"turn_on": 256, "turn_off": 128},
    "media_player": {"turn_on": 128, "turn_off": 256},
    "water_heater": {"turn_on": 8, "turn_off": 8},
}


class DeviceControl:
    """Keep HA policy checks at the boundary of the pure scheduler."""

    def __init__(self, hass) -> None:
        self.hass = hass

    async def validate(
        self, entity_id: str, action: str, user_id: str | None, *, available: bool = True
    ) -> None:
        if not isinstance(entity_id, str) or entity_id.split(".", 1)[0] not in DEVICE_DOMAINS:
            raise TimerError("unsupported_device")
        if action not in ("turn_on", "turn_off"):
            raise TimerError("unsupported_device_action")
        state = self.hass.states.get(entity_id)
        if state is None:
            raise TimerError("entity_not_found")
        if not exposed_entities.async_should_expose(self.hass, "conversation", entity_id):
            raise TimerError("entity_not_exposed")
        if user_id:
            user = await self.hass.auth.async_get_user(user_id)
            if (
                user is None
                or not user.is_active
                or not user.permissions.check_entity(entity_id, POLICY_CONTROL)
            ):
                raise TimerError("entity_control_not_allowed")
        if not self.hass.services.has_service(entity_id.split(".", 1)[0], action):
            raise TimerError("device_action_not_supported")
        required = POWER_FEATURES.get(entity_id.split(".", 1)[0], {}).get(action, 0)
        if required and not state.attributes.get("supported_features", 0) & required:
            raise TimerError("device_action_not_supported")
        if available and state.state in ("unavailable", "unknown"):
            raise TimerError("device_unavailable")

    async def targets(self, user_id: str | None) -> list[dict]:
        result = []
        for state in self.hass.states.async_all():
            entity_id = state.entity_id
            actions = []
            for action in ("turn_on", "turn_off"):
                try:
                    await self.validate(entity_id, action, user_id)
                except TimerError:
                    continue
                actions.append(action)
            if actions:
                result.append({"entity_id": entity_id, "name": state.name, "actions": actions})
        return result

    async def async_execute(self, job: ScheduledAction) -> None:
        await self.validate(job.entity_id, job.action, job.owner_user_id)
        async with asyncio.timeout(10):
            await self.hass.services.async_call(
                job.entity_id.split(".", 1)[0],
                job.action,
                {"entity_id": job.entity_id},
                blocking=True,
                context=Context(user_id=job.owner_user_id),
            )

    async def visible_jobs(self, manager, user_id: str | None) -> list[dict]:
        result = []
        for job in list(manager.jobs.values()):
            if job.owner_user_id != user_id:
                try:
                    await self.validate(job.entity_id, job.action, user_id, available=False)
                except TimerError:
                    continue
            result.append(manager.snapshot(job))
        return result

    async def authorize_cancel(self, job: ScheduledAction, user_id: str | None) -> None:
        if job.owner_user_id != user_id:
            await self.validate(job.entity_id, job.action, user_id, available=False)
