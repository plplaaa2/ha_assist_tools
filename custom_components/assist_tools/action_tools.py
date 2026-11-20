"""Name and describe scheduled control tools; related files: llm, scheduler/control/setup.

Creation resolves one entity and action now. Cancellation uses an exact job ID,
and all replies belong to the LLM rather than the satellite announcement channel.
"""

from datetime import datetime

import probatio
from homeassistant.helpers import llm

from .const import DOMAIN, MAX_DURATION
from .scheduled_actions import SCHEDULED_ACTIONS, WEEKDAYS
from .timer_manager import TimerError

# Guide delayed device and cover control; related: llm.py, test_scheduled_actions.py.
ACTION_PROMPT = """For delayed device control, use AssistScheduledControl tools; never alarm timers.
Use open_cover or close_cover for covers. Choose from the target's listed actions.
Ask if target is ambiguous. Separate action times require separate jobs.
Alarm cancellation does not cancel device jobs. Confirm reservations or
cancellations in Korean, not that the device already changed state.
Never replay uncertain execution or claim success on error.
List jobs before identifying one to cancel or change. Match a pending job by device,
action and existing local execution date/time. If several match, read their local
times and ask which one. Use the exact job_id internally. When listing weekly jobs,
say the selected weekdays, regular local time, and next execution in Korean.
For weekly creation, expand weekdays to mon-fri, weekends to sat-sun, and daily to all seven days.
For "next occurrence only", set execute_at or delay_seconds. For changes to the
weekly rule, send at_time and/or the complete resulting weekdays list. Clarify
the scope when ambiguous. Cancelling a weekly job cancels the entire series.
Use now_local and time_zone from List to resolve relative dates. A next-only
override must precede the following regular occurrence.
"""


# Share weekly and change fields between Assist and HA actions; related: __init__.py.
def weekly_fields(*, required: bool) -> dict:
    marker = probatio.Required if required else probatio.Optional
    return {
        marker("weekdays", description="Selected weekdays, such as [mon, wed, fri]."): [
            probatio.In(WEEKDAYS)
        ],
        marker("at_time", description="Regular time in Home Assistant local time (HH:MM)."): str,
    }


RESCHEDULE_FIELDS = {
    probatio.Required("job_id", description="Exact pending scheduled device job ID."): str,
    probatio.Optional(
        "execute_at",
        description="New execution date/time or next-only override, ISO 8601 with UTC offset.",
    ): str,
    probatio.Optional(
        "delay_seconds", description="Alternative next execution delay, in seconds from now."
    ): probatio.All(int, probatio.Range(min=1, max=MAX_DURATION)),
    **weekly_fields(required=False),
}


async def perform_action_operation(
    runtime, operation: str, args: dict, user_id: str | None, call_id: str | None = None
) -> dict:
    """Share validation between LLM tools and HA actions."""
    manager = runtime.actions
    if manager is None or manager.closed:
        raise TimerError("scheduled_actions_not_ready")
    if operation in ("schedule_action", "schedule_weekly_action"):
        await runtime.control.validate(args["entity_id"], args["action"], user_id)
        if operation == "schedule_weekly_action":
            return await manager.async_create(
                args["entity_id"],
                args["action"],
                1,
                user_id,
                call_id,
                weekdays=args["weekdays"],
                at_time=args["at_time"],
            )
        return await manager.async_create(
            args["entity_id"], args["action"], args["delay_seconds"], user_id, call_id
        )
    if operation == "list_actions":
        return {
            "jobs": await runtime.control.visible_jobs(manager, user_id),
            "now_local": datetime.fromtimestamp(manager.wall_clock(), manager.zone).isoformat(),
            "time_zone": str(manager.zone),
        }
    if operation in ("cancel_action", "reschedule_action"):
        job = manager.jobs.get(args["job_id"])
        if job is None:
            raise TimerError("scheduled_action_not_found")
        if operation == "reschedule_action":
            await runtime.control.validate(job.entity_id, job.action, user_id, available=False)
            return await manager.async_reschedule(**args)
        await runtime.control.authorize_cancel(job, user_id)
        return await manager.async_cancel(job.job_id)
    raise TimerError("unknown_device_operation")


class ActionTool(llm.Tool):
    """A timer-independent scheduled device operation."""

    integration = DOMAIN

    def __init__(self, operation: str, entities: list[str] | None = None) -> None:
        self.operation = operation
        suffix = {
            "schedule_action": "Create",
            "schedule_weekly_action": "CreateWeekly",
            "list_actions": "List",
            "cancel_action": "Cancel",
            "reschedule_action": "Reschedule",
        }[operation]
        self.name = f"AssistScheduledControl{suffix}"
        self.title = f"Scheduled device control: {suffix}"
        self.description = {
            "schedule_action": "Schedule a supported action for one exposed device after a delay, including opening or closing a cover.",
            "list_actions": "List scheduled device controls and their job IDs, including pending reservations and completed, cancelled or failed outcomes; not alarm timers.",
            "cancel_action": "Cancel a pending scheduled device control by its exact job_id; do not cancel alarm timers or undo device actions that already started.",
            "schedule_weekly_action": "Schedule a supported device action at a local time on one or more weekdays.",
            "reschedule_action": "Move a pending device job or change its weekly rule by exact job_id.",
        }[operation]
        fields = {}
        if operation in ("schedule_action", "schedule_weekly_action"):
            fields = {
                probatio.Required(
                    "entity_id",
                    description="Exact exposed device entity ID to control when the delay ends.",
                ): probatio.In(entities or []),
                probatio.Required(
                    "action",
                    description="A listed action for the target: turn_on, turn_off, open_cover, or close_cover.",
                ): probatio.In(SCHEDULED_ACTIONS),
            }
            if operation == "schedule_weekly_action":
                fields.update(weekly_fields(required=True))
            else:
                fields[
                    probatio.Required(
                        "delay_seconds",
                        description="Delay from now in seconds, from 1 to 604800; never execute immediately.",
                    )
                ] = probatio.All(int, probatio.Range(min=1, max=MAX_DURATION))
        elif operation == "reschedule_action":
            fields = RESCHEDULE_FIELDS
        elif operation == "cancel_action":
            fields = {
                probatio.Required(
                    "job_id",
                    description="Exact scheduled control job_id returned by Create or List; not an alarm timer ID.",
                ): probatio.All(str, probatio.Length(min=1, max=64))
            }
        self.parameters = probatio.Schema(fields)
        self.annotations = llm.ToolAnnotations(
            read_only=operation == "list_actions",
            destructive=operation == "cancel_action",
            idempotent=operation in ("list_actions", "cancel_action"),
            open_world=False,
        )

    async def async_call(self, hass, tool_input, llm_context):
        runtime = hass.data[DOMAIN]
        user_id = getattr(llm_context.context, "user_id", None)
        try:
            result = await perform_action_operation(
                runtime, self.operation, tool_input.tool_args, user_id, tool_input.id
            )
        except TimerError as err:
            return llm.ToolResult(data={"error": str(err)}, error=True)
        except Exception:
            runtime.log_operation_error(self.operation)
            return llm.ToolResult(data={"error": "scheduled_action_operation_failed"}, error=True)
        return llm.ToolResult(data={**result, "response_mode": "normal"})
