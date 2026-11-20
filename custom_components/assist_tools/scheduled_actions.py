"""Persist delayed device jobs; related files: device_control.py, __init__.py, action_tools.py.

The scheduler stores resolved targets, never natural-language commands. It marks
an execution durably before calling the device and never retries uncertain jobs.
"""

import asyncio
import logging
import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta, tzinfo
from datetime import time as local_time
from uuid import uuid4

from .const import MAX_DURATION
from .timer_manager import TimerError

_LOGGER = logging.getLogger(__name__)
TERMINAL = {"completed", "cancelled", "expired", "failed", "interrupted"}
# Share action names across persistence and schemas; related: device_control.py, services.yaml.
POWER_ACTIONS = ("turn_on", "turn_off")
COVER_ACTIONS = ("open_cover", "close_cover")
SCHEDULED_ACTIONS = POWER_ACTIONS + COVER_ACTIONS
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


# Resolve weekly rules in HA local time; related: action_tools.py, __init__.py.
def weekly_rule(weekdays, at_time: str) -> tuple[list[str], str]:
    if (
        not isinstance(weekdays, (list, tuple))
        or not weekdays
        or any(day not in WEEKDAYS for day in weekdays)
    ):
        raise TimerError("invalid_repeat_weekdays")
    try:
        parsed = datetime.strptime(at_time, "%H:%M").time()
        if parsed.isoformat(timespec="minutes") != at_time:
            raise ValueError
    except (TypeError, ValueError) as err:
        raise TimerError("invalid_repeat_time") from err
    return [day for day in WEEKDAYS if day in weekdays], at_time


@dataclass
class ScheduledAction:
    """A resolved device job and its durable outcome."""

    job_id: str
    entity_id: str
    action: str
    delay_seconds: int
    execute_at: float
    owner_user_id: str | None = None
    call_id: str | None = None
    state: str = "pending"
    last_error: str | None = None
    weekdays: list[str] | None = None
    at_time: str | None = None
    creation_rule: str | None = None
    replaced_at: float | None = None
    last_occurrence_at: float | None = None


class ScheduledActions:
    """Schedule many jobs independently of the single alarm-timer slots."""

    def __init__(
        self,
        execute: Callable[[ScheduledAction], Awaitable[None]],
        save: Callable[[list[dict]], Awaitable[None]],
        *,
        wall_clock: Callable[[], float] = time.time,
        clock: Callable[[], float] = time.monotonic,
        zone: tzinfo = UTC,
    ) -> None:
        self.execute = execute
        self.save = save
        self.wall_clock = wall_clock
        self.clock = clock
        self.zone = zone
        self.jobs: dict[str, ScheduledAction] = {}
        self.closed = False
        self._lock = asyncio.Lock()
        self._handles: dict[str, asyncio.TimerHandle] = {}
        self._deadlines: dict[str, float] = {}
        self._tasks: set[asyncio.Task] = set()

    def snapshot(self, job: ScheduledAction) -> dict:
        """Return public data without identity or tool-call metadata."""
        data = asdict(job)
        data.pop("owner_user_id")
        data.pop("call_id")
        data.pop("creation_rule")
        data.pop("replaced_at")
        data.pop("last_occurrence_at")
        data["next_execution_overridden"] = job.replaced_at is not None
        data["execute_at_local"] = datetime.fromtimestamp(job.execute_at, self.zone).isoformat()
        deadline = self._deadlines.get(job.job_id)
        data["remaining_seconds"] = (
            max(0, math.ceil(deadline - self.clock()))
            if deadline is not None and job.state == "pending"
            else 0
        )
        return data

    async def _persist(self) -> None:
        # Keep every active job and a bounded history of terminal outcomes.
        finished = [key for key, job in self.jobs.items() if job.state in TERMINAL]
        pruned = set(finished[:-64])
        await self.save([asdict(job) for key, job in self.jobs.items() if key not in pruned])
        for key in pruned:
            self.jobs.pop(key)

    def _next_weekly(self, weekdays: list[str], at_time: str, after: float) -> float:
        today = datetime.fromtimestamp(after, self.zone).date()
        wall_time = local_time.fromisoformat(at_time)
        for offset in range(15):
            day = today + timedelta(days=offset)
            if WEEKDAYS[day.weekday()] not in weekdays:
                continue
            candidate = datetime.combine(day, wall_time, self.zone)
            stamp = candidate.timestamp()
            # Skip nonexistent times. For an ambiguous time, use its first occurrence.
            if stamp > after and datetime.fromtimestamp(stamp, self.zone) == candidate:
                return stamp
        raise TimerError("invalid_repeat_time")

    def _schedule(self, job: ScheduledAction, seconds: float) -> None:
        previous = self._handles.pop(job.job_id, None)
        if previous:
            previous.cancel()

        def fire() -> None:
            self._handles.pop(job.job_id, None)
            task = asyncio.create_task(self.async_execute(job.job_id))
            self._tasks.add(task)
            task.add_done_callback(self._done)

        self._handles[job.job_id] = asyncio.get_running_loop().call_later(max(0, seconds), fire)

    def _done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception():
            _LOGGER.error("Scheduled device callback failed", exc_info=task.exception())

    async def async_create(
        self,
        entity_id: str,
        action: str,
        delay_seconds: int,
        user_id: str | None = None,
        call_id: str | None = None,
        *,
        weekdays: list[str] | None = None,
        at_time: str | None = None,
    ) -> dict:
        """Save before scheduling and deduplicate repeated tool calls."""
        if action not in SCHEDULED_ACTIONS:
            raise TimerError("unsupported_device_action")
        creation_rule = None
        if weekdays is not None or at_time is not None:
            weekdays, at_time = weekly_rule(weekdays, at_time)
            creation_rule = f"{','.join(weekdays)}@{at_time}"
        elif type(delay_seconds) is not int or not 1 <= delay_seconds <= MAX_DURATION:
            raise TimerError("delay_out_of_range")
        if not isinstance(entity_id, str) or "." not in entity_id:
            raise TimerError("invalid_entity")
        async with self._lock:
            if self.closed:
                raise TimerError("integration_unloaded")
            for job in self.jobs.values():
                if call_id and job.call_id == call_id:
                    if (
                        job.entity_id,
                        job.action,
                        job.delay_seconds,
                        job.owner_user_id,
                        job.creation_rule,
                    ) != (
                        entity_id,
                        action,
                        delay_seconds,
                        user_id,
                        creation_rule,
                    ):
                        raise TimerError("tool_call_id_conflict")
                    return {**self.snapshot(job), "replayed": True}
            if sum(job.state in ("pending", "executing") for job in self.jobs.values()) >= 128:
                raise TimerError("too_many_scheduled_actions")
            now = self.wall_clock()
            execute_at = (
                self._next_weekly(weekdays, at_time, now) if weekdays else now + delay_seconds
            )
            job = ScheduledAction(
                uuid4().hex,
                entity_id,
                action,
                delay_seconds,
                execute_at,
                user_id,
                call_id,
                weekdays=weekdays,
                at_time=at_time,
                creation_rule=creation_rule,
            )
            self.jobs[job.job_id] = job
            self._deadlines[job.job_id] = self.clock() + (execute_at - now)
            try:
                await self._persist()
            except Exception:
                self.jobs.pop(job.job_id)
                self._deadlines.pop(job.job_id)
                raise
            self._schedule(job, self._deadlines[job.job_id] - self.clock())
            _LOGGER.info("Created scheduled device action %s", job.job_id)
            return self.snapshot(job)

    async def async_reschedule(
        self,
        job_id: str,
        *,
        execute_at: str | None = None,
        delay_seconds: int | None = None,
        weekdays: list[str] | None = None,
        at_time: str | None = None,
    ) -> dict:
        """Change the next execution or the weekly rule under the execution lock."""
        async with self._lock:
            if self.closed:
                raise TimerError("integration_unloaded")
            job = self.jobs.get(job_id)
            if job is None:
                raise TimerError("scheduled_action_not_found")
            if job.state != "pending":
                raise TimerError("scheduled_action_not_pending")
            now = self.wall_clock()
            updated = replace(job)
            if weekdays is not None or at_time is not None:
                if execute_at is not None or delay_seconds is not None or not job.weekdays:
                    raise TimerError("invalid_schedule_change")
                updated.weekdays, updated.at_time = weekly_rule(
                    job.weekdays if weekdays is None else weekdays,
                    job.at_time if at_time is None else at_time,
                )
                after = max(now, job.last_occurrence_at or now)
                updated.execute_at = self._next_weekly(updated.weekdays, updated.at_time, after)
                updated.replaced_at = None
            elif execute_at is not None and delay_seconds is None:
                try:
                    parsed = datetime.fromisoformat(execute_at)
                    if parsed.tzinfo is None:
                        raise ValueError
                    updated.execute_at = parsed.timestamp()
                except (TypeError, ValueError, OverflowError, OSError) as err:
                    raise TimerError("invalid_execution_time") from err
                if updated.execute_at <= now:
                    raise TimerError("invalid_execution_time")
            elif (
                execute_at is None
                and type(delay_seconds) is int
                and 1 <= delay_seconds <= MAX_DURATION
            ):
                updated.execute_at = now + delay_seconds
            else:
                raise TimerError("invalid_schedule_change")
            if job.weekdays and weekdays is None and at_time is None:
                original = job.execute_at if job.replaced_at is None else job.replaced_at
                following = self._next_weekly(job.weekdays, job.at_time, original)
                if updated.execute_at >= following:
                    raise TimerError("override_must_precede_following_execution")
                updated.replaced_at = original
            deadline = self.clock() + (updated.execute_at - now)
            self.jobs[job_id] = updated
            try:
                await self._persist()
            except BaseException:
                self.jobs[job_id] = job
                raise
            self._deadlines[job_id] = deadline
            self._schedule(updated, deadline - self.clock())
            return self.snapshot(updated)

    async def async_cancel(self, job_id: str) -> dict:
        """Serialize cancellation against the durable start of an execution."""
        async with self._lock:
            if self.closed:
                raise TimerError("integration_unloaded")
            job = self.jobs.get(job_id)
            if job is None:
                raise TimerError("scheduled_action_not_found")
            if job.state == "cancelled":
                return self.snapshot(job)
            if job.state != "pending":
                raise TimerError("scheduled_action_not_pending")
            job.state = "cancelled"
            self.jobs.pop(job_id)
            self.jobs[job_id] = job
            try:
                await self._persist()
            except Exception:
                job.state = "pending"
                raise
            handle = self._handles.pop(job_id, None)
            if handle:
                handle.cancel()
            self._deadlines.pop(job_id, None)
            _LOGGER.info("Cancelled scheduled device action %s", job_id)
            return self.snapshot(job)

    async def async_execute(self, job_id: str) -> None:
        """Claim the job once, then revalidate and run its exact stored action."""
        async with self._lock:
            job = self.jobs.get(job_id)
            if self.closed or job is None or job.state != "pending":
                return
            remaining = self._deadlines.get(job_id, self.clock()) - self.clock()
            if remaining > 0:
                self._schedule(job, remaining)
                return
            job.state = "executing"
            try:
                await self._persist()
            except Exception:
                job.state = "failed"
                job.last_error = "storage_save_failed"
                if job.weekdays:
                    self._advance_weekly(job)
                raise
            self._deadlines.pop(job_id, None)
        try:
            await self.execute(job)
        except Exception as err:
            state, error = (
                "failed",
                str(err) if isinstance(err, TimerError) else "device_action_failed",
            )
            _LOGGER.error("Scheduled device action failed: %s", job_id)
        else:
            state, error = "completed", None
            _LOGGER.info("Completed scheduled device action %s", job_id)
        async with self._lock:
            job.state, job.last_error = state, error
            if job.weekdays:
                self._advance_weekly(job)
            self.jobs.pop(job_id)
            self.jobs[job_id] = job
            await self._persist()

    def _advance_weekly(self, job: ScheduledAction, *, schedule: bool = True) -> None:
        now = self.wall_clock()
        after = max(now, job.execute_at, job.replaced_at or job.execute_at)
        job.last_occurrence_at = after
        job.execute_at = self._next_weekly(job.weekdays, job.at_time, after)
        job.replaced_at = None
        job.state = "pending"
        remaining = job.execute_at - now
        self._deadlines[job.job_id] = self.clock() + remaining
        if schedule and not self.closed:
            self._schedule(job, remaining)

    async def async_restore(self, saved) -> None:
        """Resume future jobs; overdue/uncertain executions are never replayed."""
        if saved is None:
            return
        if not isinstance(saved, list) or len(saved) > 192:
            raise TimerError("invalid_scheduled_action_storage")
        for data in saved:
            try:
                job = ScheduledAction(**data)
                if (
                    not isinstance(job.job_id, str)
                    or not job.job_id
                    or job.job_id in self.jobs
                    or not isinstance(job.entity_id, str)
                    or "." not in job.entity_id
                    or job.action not in SCHEDULED_ACTIONS
                    or type(job.delay_seconds) is not int
                    or not 1 <= job.delay_seconds <= MAX_DURATION
                    or not isinstance(job.execute_at, (int, float))
                    or not math.isfinite(job.execute_at)
                    or job.state not in TERMINAL | {"pending", "executing"}
                    or (job.owner_user_id is not None and not isinstance(job.owner_user_id, str))
                    or (job.call_id is not None and not isinstance(job.call_id, str))
                ):
                    raise ValueError("Invalid job")
                if job.weekdays is not None or job.at_time is not None:
                    job.weekdays, job.at_time = weekly_rule(job.weekdays, job.at_time)
                if job.replaced_at is not None and (
                    not job.weekdays
                    or type(job.replaced_at) not in (int, float)
                    or not math.isfinite(job.replaced_at)
                ):
                    raise ValueError("Invalid weekly override")
                datetime.fromtimestamp(job.execute_at, self.zone)
                if job.replaced_at is not None:
                    datetime.fromtimestamp(job.replaced_at, self.zone)
                if job.last_occurrence_at is not None:
                    if (
                        not job.weekdays
                        or type(job.last_occurrence_at) not in (int, float)
                        or not math.isfinite(job.last_occurrence_at)
                    ):
                        raise ValueError("Invalid last weekly occurrence")
                    datetime.fromtimestamp(job.last_occurrence_at, self.zone)
            except TypeError, ValueError, TimerError, OverflowError, OSError:
                _LOGGER.warning("Discarding invalid scheduled action record")
                continue
            self.jobs[job.job_id] = job
            if job.state == "executing":
                job.state, job.last_error = "interrupted", "execution_interrupted"
            if job.weekdays and (
                job.state == "interrupted"
                or (job.state == "pending" and job.execute_at <= self.wall_clock())
            ):
                self._advance_weekly(job, schedule=False)
            if job.state == "pending":
                remaining = job.execute_at - self.wall_clock()
                if remaining <= 0:
                    job.state = "expired"
                else:
                    self._deadlines[job.job_id] = self.clock() + remaining
        await self._persist()
        for job in self.jobs.values():
            if job.state == "pending":
                self._schedule(job, self._deadlines[job.job_id] - self.clock())

    async def async_close(self) -> None:
        """Suspend pending jobs and let already claimed executions settle."""
        async with self._lock:
            self.closed = True
            for handle in self._handles.values():
                handle.cancel()
            self._handles.clear()
        tasks = list(self._tasks)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
