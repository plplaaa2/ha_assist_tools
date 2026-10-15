"""Persist delayed on/off jobs; related files: device_control, setup, and LLM.

The scheduler stores resolved targets, never natural-language commands. It marks
an execution durably before calling the device and never retries uncertain jobs.
"""

import asyncio
import logging
import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from uuid import uuid4

from .const import MAX_DURATION
from .timer_manager import TimerError

_LOGGER = logging.getLogger(__name__)
TERMINAL = {"completed", "cancelled", "expired", "failed", "interrupted"}


@dataclass
class ScheduledAction:
    """A resolved one-shot job and its durable outcome."""

    job_id: str
    entity_id: str
    action: str
    delay_seconds: int
    execute_at: float
    owner_user_id: str | None = None
    call_id: str | None = None
    state: str = "pending"
    last_error: str | None = None


class ScheduledActions:
    """Schedule many jobs independently of the single alarm-timer slots."""

    def __init__(
        self,
        execute: Callable[[ScheduledAction], Awaitable[None]],
        save: Callable[[list[dict]], Awaitable[None]],
        *,
        wall_clock: Callable[[], float] = time.time,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.execute = execute
        self.save = save
        self.wall_clock = wall_clock
        self.clock = clock
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
        for key in finished[:-64]:
            self.jobs.pop(key)
        await self.save([asdict(job) for job in self.jobs.values()])

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
    ) -> dict:
        """Save before scheduling and deduplicate repeated tool calls."""
        if action not in ("turn_on", "turn_off"):
            raise TimerError("unsupported_device_action")
        if type(delay_seconds) is not int or not 1 <= delay_seconds <= MAX_DURATION:
            raise TimerError("delay_out_of_range")
        if not isinstance(entity_id, str) or "." not in entity_id:
            raise TimerError("invalid_entity")
        async with self._lock:
            if self.closed:
                raise TimerError("integration_unloaded")
            for job in self.jobs.values():
                if call_id and job.call_id == call_id:
                    if (job.entity_id, job.action, job.delay_seconds, job.owner_user_id) != (
                        entity_id,
                        action,
                        delay_seconds,
                        user_id,
                    ):
                        raise TimerError("tool_call_id_conflict")
                    return {**self.snapshot(job), "replayed": True}
            if sum(job.state in ("pending", "executing") for job in self.jobs.values()) >= 128:
                raise TimerError("too_many_scheduled_actions")
            job = ScheduledAction(
                uuid4().hex,
                entity_id,
                action,
                delay_seconds,
                self.wall_clock() + delay_seconds,
                user_id,
                call_id,
            )
            self.jobs[job.job_id] = job
            self._deadlines[job.job_id] = self.clock() + delay_seconds
            try:
                await self._persist()
            except Exception:
                self.jobs.pop(job.job_id)
                self._deadlines.pop(job.job_id)
                raise
            self._schedule(job, self._deadlines[job.job_id] - self.clock())
            _LOGGER.info("Created scheduled device action %s", job.job_id)
            return self.snapshot(job)

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
            self.jobs.pop(job_id)
            self.jobs[job_id] = job
            await self._persist()

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
                    or job.action not in ("turn_on", "turn_off")
                    or type(job.delay_seconds) is not int
                    or not 1 <= job.delay_seconds <= MAX_DURATION
                    or not isinstance(job.execute_at, (int, float))
                    or not math.isfinite(job.execute_at)
                    or job.state not in TERMINAL | {"pending", "executing"}
                    or (job.owner_user_id is not None and not isinstance(job.owner_user_id, str))
                    or (job.call_id is not None and not isinstance(job.call_id, str))
                ):
                    raise ValueError("Invalid job")
            except TypeError, ValueError:
                _LOGGER.warning("Discarding invalid scheduled action record")
                continue
            self.jobs[job.job_id] = job
            if job.state == "executing":
                job.state, job.last_error = "interrupted", "execution_interrupted"
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
