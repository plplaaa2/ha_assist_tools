"""Manage one durable timer per satellite; related files: setup, LLM, and satellite.

This module has no Home Assistant imports so timing and races can be tested
without a running Home Assistant instance. All transitions are serialized.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from typing import Any, Protocol
from uuid import uuid4

from .const import ALARM_SECONDS, MAX_DURATION

_LOGGER = logging.getLogger(__name__)


class TimerError(Exception):
    """An actionable timer operation failure."""


class AlarmOutput(Protocol):
    """Playback operations provided by satellite.py."""

    async def async_play(self) -> None:
        """Start the bounded alarm recording."""

    async def async_stop(self) -> None:
        """Stop the alarm output."""


@dataclass
class TimerRecord:
    """Persisted state for a timer occupying a satellite slot."""

    timer_id: str
    duration_seconds: int
    name: str
    state: str
    remaining_seconds: float
    expires_at: float | None = None
    alarm_ends_at: float | None = None
    last_error: str | None = None


def duration_text(seconds: int) -> str:
    """Format the original duration for Korean confirmation announcements."""
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return " ".join(
        f"{value}{unit}"
        for value, unit in ((hours, "시간"), (minutes, "분"), (seconds, "초"))
        if value
    )


class TimerManager:
    """Serialize lifecycle transitions and reject duplicate timers."""

    def __init__(
        self,
        output: AlarmOutput,
        save: Callable[[dict[str, Any] | None], Awaitable[None]],
        *,
        wall_clock: Callable[[], float] = time.time,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.output = output
        self._save = save
        self._wall_clock = wall_clock
        self._clock = clock
        self.timer: TimerRecord | None = None
        self._deadline: float | None = None
        self._lock = asyncio.Lock()
        self._handle: asyncio.TimerHandle | None = None
        self._tasks: set[asyncio.Task] = set()
        self._closed = False

    def snapshot(self) -> dict[str, Any]:
        """Return a detached state with current remaining time."""
        if self.timer is None:
            return {"state": "idle"}
        data = asdict(self.timer)
        if self.timer.state == "active" and self._deadline is not None:
            data["remaining_seconds"] = max(0, math.ceil(self._deadline - self._clock()))
        else:
            data["remaining_seconds"] = math.ceil(self.timer.remaining_seconds)
        return data

    def _check(self, timer_id: str | None = None) -> TimerRecord:
        if self._closed:
            raise TimerError("integration_unloaded")
        if self.timer is None:
            raise TimerError("timer_not_found")
        if timer_id is not None and self.timer.timer_id != timer_id:
            raise TimerError("timer_id_mismatch")
        return self.timer

    async def _persist(self) -> None:
        await self._save(asdict(self.timer) if self.timer else None)

    def _cancel_schedule(self) -> None:
        if self._handle is not None:
            self._handle.cancel()
            self._handle = None

    def _schedule(self, seconds: float, timer_id: str, alarm: bool = False) -> None:
        self._cancel_schedule()

        def fire() -> None:
            task = asyncio.create_task(
                self.async_alarm_finished(timer_id) if alarm else self.async_expire(timer_id)
            )
            self._tasks.add(task)
            task.add_done_callback(self._task_done)

        self._handle = asyncio.get_running_loop().call_later(max(0, seconds), fire)

    def _task_done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            _LOGGER.error("Timer callback failed", exc_info=task.exception())

    async def async_create(self, duration_seconds: int, name: str = "") -> dict[str, Any]:
        """Allocate an ID and refuse to replace an occupied slot."""
        if type(duration_seconds) is not int or not 1 <= duration_seconds <= MAX_DURATION:
            raise TimerError("duration_out_of_range")
        if not isinstance(name, str) or len(name) > 80:
            raise TimerError("invalid_name")
        async with self._lock:
            if self._closed:
                raise TimerError("integration_unloaded")
            if self.timer is not None:
                raise TimerError("timer_already_exists")
            self.timer = TimerRecord(
                timer_id=uuid4().hex,
                duration_seconds=duration_seconds,
                name=name.strip(),
                state="active",
                remaining_seconds=duration_seconds,
                expires_at=self._wall_clock() + duration_seconds,
            )
            self._deadline = self._clock() + duration_seconds
            try:
                await self._persist()
            except Exception:
                self.timer = None
                self._deadline = None
                raise
            self._schedule(max(0, self._deadline - self._clock()), self.timer.timer_id)
            return self.snapshot()

    async def async_pause(self, timer_id: str | None = None) -> dict[str, Any]:
        """Freeze remaining time without freeing the satellite slot."""
        async with self._lock:
            timer = self._check(timer_id)
            if timer.state == "paused":
                return self.snapshot()
            if timer.state != "active":
                raise TimerError("timer_not_running")
            old = asdict(timer)
            timer.remaining_seconds = max(0, (self._deadline or self._clock()) - self._clock())
            timer.expires_at = None
            timer.state = "paused"
            try:
                await self._persist()
            except Exception:
                self.timer = TimerRecord(**old)
                raise
            self._cancel_schedule()
            self._deadline = None
            return self.snapshot()

    async def async_resume(self, timer_id: str | None = None) -> dict[str, Any]:
        """Resume only the stored remainder."""
        async with self._lock:
            timer = self._check(timer_id)
            if timer.state == "active":
                return self.snapshot()
            if timer.state != "paused":
                raise TimerError("timer_not_paused")
            timer.state = "active"
            timer.expires_at = self._wall_clock() + timer.remaining_seconds
            self._deadline = self._clock() + timer.remaining_seconds
            try:
                await self._persist()
            except Exception:
                timer.state = "paused"
                timer.expires_at = None
                self._deadline = None
                raise
            self._schedule(max(0, self._deadline - self._clock()), timer.timer_id)
            return self.snapshot()

    async def async_cancel(self, timer_id: str | None = None) -> dict[str, Any]:
        """Stop playback before releasing the slot; skip ringing announcements."""
        async with self._lock:
            if self.timer is None and not self._closed:
                return {"state": "idle", "skip_announcement": True}
            timer = self._check(timer_id)
            data = self.snapshot()
            was_ringing = timer.state in ("ringing", "alarm_failed")
            self._cancel_schedule()
            if was_ringing:
                try:
                    await self.output.async_stop()
                except Exception:
                    timer.last_error = "alarm_stop_failed"
                    await self._persist()
                    # Retain ownership so another cancel can retry, and keep the
                    # automatic stop attempt even after a failed manual stop.
                    remaining = max(
                        0, (timer.alarm_ends_at or self._wall_clock()) - self._wall_clock()
                    )
                    self._schedule(remaining, timer.timer_id, alarm=True)
                    raise TimerError("alarm_stop_failed") from None
            old = self.timer
            self.timer = None
            try:
                await self._persist()
            except Exception:
                self.timer = old
                if timer.state == "active":
                    self._schedule(
                        max(0, (self._deadline or self._clock()) - self._clock()), timer.timer_id
                    )
                raise
            self._deadline = None
            return {**data, "state": "cancelled", "skip_announcement": was_ringing}

    async def async_expire(self, timer_id: str) -> None:
        """Start one bounded alarm only if this exact timer is still active."""
        async with self._lock:
            if self._closed or self.timer is None:
                return
            timer = self.timer
            if timer.timer_id != timer_id or timer.state != "active":
                return
            self._cancel_schedule()
            timer.state = "ringing"
            timer.remaining_seconds = 0
            timer.expires_at = None
            timer.alarm_ends_at = self._wall_clock() + ALARM_SECONDS
            self._deadline = None
            try:
                await self._persist()
            except Exception:
                timer.state = "timer_failed"
                timer.last_error = "timer_save_failed"
                # Never play an alarm whose durable transition failed. Retain
                # the slot and report the failure through status/cancel.
                raise
            try:
                await self.output.async_play()
            except Exception:
                timer.state = "alarm_failed"
                timer.last_error = "alarm_play_failed"
                await self._persist()
                _LOGGER.error("Could not start timer alarm %s", timer.timer_id)
            self._schedule(ALARM_SECONDS, timer.timer_id, alarm=True)

    async def async_alarm_finished(self, timer_id: str) -> None:
        """Stop after one minute, without touching a newer timer."""
        if self.timer is None or self.timer.timer_id != timer_id:
            return
        try:
            await self.async_cancel(timer_id)
        except TimerError:
            # No tight retry loop against an unavailable device. The finite
            # recording also bounds playback independently of this callback.
            # Recheck ownership after waiting for the lock: a cancelled alarm
            # may have been replaced by a new timer while this task waited.
            if self.timer is not None and self.timer.timer_id == timer_id:
                self._cancel_schedule()
                _LOGGER.error("Automatic alarm stop failed for %s", timer_id)

    async def async_restore(self, saved: dict[str, Any] | None) -> None:
        """Restore active/paused timers; never replay expired offline alarms."""
        if saved is None:
            return
        try:
            timer = TimerRecord(**saved)
            if (
                not isinstance(timer.timer_id, str)
                or not timer.timer_id
                or not isinstance(timer.name, str)
                or len(timer.name) > 80
                or type(timer.duration_seconds) is not int
                or not 1 <= timer.duration_seconds <= MAX_DURATION
                or not isinstance(timer.remaining_seconds, (int, float))
                or not math.isfinite(timer.remaining_seconds)
                or not 0 <= timer.remaining_seconds <= MAX_DURATION
                or timer.state
                not in ("active", "paused", "ringing", "alarm_failed", "timer_failed")
            ):
                raise ValueError("Invalid timer record")
            if timer.state == "active" and (
                not isinstance(timer.expires_at, (int, float))
                or not math.isfinite(timer.expires_at)
            ):
                raise ValueError("Invalid expiry")
        except TypeError, ValueError:
            _LOGGER.warning("Discarding invalid persisted timer data")
            await self._save(None)
            return
        self.timer = timer
        if timer.state in ("ringing", "alarm_failed"):
            # A pre-restart player may still be sounding; stop it, never replay.
            await self.async_cancel(timer.timer_id)
        elif timer.state == "active":
            remaining = max(0, timer.expires_at - self._wall_clock())
            if remaining == 0:
                self.timer = None
                await self._persist()
                _LOGGER.warning(
                    "Expired offline timer %s was cleared without replay", timer.timer_id
                )
            else:
                timer.remaining_seconds = remaining
                self._deadline = self._clock() + remaining
                self._schedule(remaining, timer.timer_id)

    async def async_close(self) -> None:
        """Cancel callbacks, persist countdowns, and stop owned alarm playback."""
        async with self._lock:
            self._closed = True
            self._cancel_schedule()
            if self.timer and self.timer.state in ("ringing", "alarm_failed"):
                await self.output.async_stop()
                self.timer = None
                await self._persist()
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
