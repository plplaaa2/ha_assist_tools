"""Name and describe scheduled control tools; related files: llm, scheduler/control/setup.

Creation resolves one entity and action now. Cancellation uses an exact job ID,
and all replies belong to the LLM rather than the satellite announcement channel.
"""

import probatio
from homeassistant.helpers import llm

from .const import DOMAIN, MAX_DURATION
from .timer_manager import TimerError

# Keep concise routing and ambiguity guards; related files: provider tests.
ACTION_PROMPT = """Use AssistScheduledControlCreate for delayed turn_on/turn_off with exact
entity_id and delay_seconds; never an alarm timer or immediate execution.
Ask if the target is ambiguous. Separate on/off times require separate jobs.
Use AssistScheduledControlList for job IDs, then AssistScheduledControlCancel.
Alarm cancellation does not cancel device jobs. Confirm reservations/cancellation
in Korean, not that the device already changed. Never replay failed, expired or
uncertain execution, or claim success on error.
"""


async def perform_action_operation(
    runtime, operation: str, args: dict, user_id: str | None, call_id: str | None = None
) -> dict:
    """Share validation between LLM tools and HA actions."""
    manager = runtime.actions
    if manager is None or manager.closed:
        raise TimerError("scheduled_actions_not_ready")
    if operation == "schedule_action":
        await runtime.control.validate(args["entity_id"], args["action"], user_id)
        return await manager.async_create(
            args["entity_id"], args["action"], args["delay_seconds"], user_id, call_id
        )
    if operation == "list_actions":
        return {"jobs": await runtime.control.visible_jobs(manager, user_id)}
    if operation == "cancel_action":
        job = manager.jobs.get(args["job_id"])
        if job is None:
            raise TimerError("scheduled_action_not_found")
        await runtime.control.authorize_cancel(job, user_id)
        return await manager.async_cancel(job.job_id)
    raise TimerError("unknown_device_operation")


class ActionTool(llm.Tool):
    """A timer-independent scheduled device operation."""

    integration = DOMAIN

    def __init__(self, operation: str, entities: list[str] | None = None) -> None:
        self.operation = operation
        suffix = {"schedule_action": "Create", "list_actions": "List", "cancel_action": "Cancel"}[
            operation
        ]
        self.name = f"AssistScheduledControl{suffix}"
        self.title = f"Scheduled device control: {suffix}"
        self.description = {
            "schedule_action": "Schedule turning one exposed device on or off after a delay; do not execute now.",
            "list_actions": "List scheduled device controls and their job IDs, including pending reservations and completed, cancelled or failed outcomes; not alarm timers.",
            "cancel_action": "Cancel a pending scheduled device control by its exact job_id; do not cancel alarm timers or undo device actions that already started.",
        }[operation]
        fields = {}
        if operation == "schedule_action":
            fields = {
                probatio.Required(
                    "entity_id",
                    description="Exact exposed device entity ID to control when the delay ends.",
                ): probatio.In(entities or []),
                probatio.Required(
                    "action",
                    description="Device operation to execute after the delay: turn_on or turn_off.",
                ): probatio.In(["turn_on", "turn_off"]),
                probatio.Required(
                    "delay_seconds",
                    description="Delay from now in seconds, from 1 to 604800; never execute immediately.",
                ): probatio.All(int, probatio.Range(min=1, max=MAX_DURATION)),
            }
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
            idempotent=operation != "schedule_action",
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
