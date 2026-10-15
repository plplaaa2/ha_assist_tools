"""Coordinate satellite endpoints and shared device jobs; related files: setup/LLM.

Each endpoint owns its player, persisted state, and pending announcements. Tool
results are cached by call ID so retried calls cannot repeat state changes.
"""

import asyncio
import logging
from collections import OrderedDict

from .timer_manager import TimerError, TimerManager

_LOGGER = logging.getLogger(__name__)


class Endpoint:
    """One configured satellite and its independently owned timer."""

    def __init__(self, device_id: str, manager: TimerManager, output) -> None:
        self.device_id = device_id
        self.manager = manager
        self.output = output
        self._operations = asyncio.Lock()
        self._cache: OrderedDict[str, tuple[tuple, dict]] = OrderedDict()
        self._announcements: set[asyncio.Task] = set()
        self.closed = False

    async def async_operation(self, operation: str, args: dict, call_id: str | None = None) -> dict:
        """Deduplicate writes and reject ID mismatches before any output."""
        async with self._operations:
            if self.closed:
                raise TimerError("integration_unloaded")
            signature = (operation, tuple(sorted(args.items())))
            if call_id and call_id in self._cache:
                previous_signature, result = self._cache[call_id]
                if previous_signature != signature:
                    raise TimerError("tool_call_id_conflict")
                return {**result, "replayed": True}
            if operation == "create":
                result = await self.manager.async_create(
                    args["duration_seconds"], args.get("name", "")
                )
            elif operation == "status":
                result = self.manager.snapshot()
                if args.get("timer_id") and result.get("timer_id") != args["timer_id"]:
                    raise TimerError("timer_id_mismatch")
                return result
            elif operation in ("pause", "resume", "cancel"):
                method = getattr(self.manager, f"async_{operation}")
                result = await method(args.get("timer_id"))
            else:
                raise TimerError("unknown_operation")
            if call_id:
                self._cache[call_id] = (signature, dict(result))
                if len(self._cache) > 128:
                    self._cache.popitem(last=False)
            return result

    def queue_announcement(self, message: str, expected_state: str, timer_id: str | None) -> None:
        """Do not interrupt a pipeline or announce an obsolete operation."""
        if self.closed:
            return
        expected_id = None if expected_state == "cancelled" else timer_id

        def valid() -> bool:
            current = self.manager.snapshot()
            wanted = "idle" if expected_state == "cancelled" else expected_state
            return (
                not self.closed
                and current.get("timer_id") == expected_id
                and current["state"] == wanted
            )

        async def announce() -> None:
            try:
                await self.output.async_announce(message, valid)
            except Exception:
                _LOGGER.exception("Timer confirmation announcement failed")

        task = asyncio.create_task(announce())
        self._announcements.add(task)
        task.add_done_callback(self._announcements.discard)

    async def async_close(self) -> None:
        """Drain confirmation tasks before closing the timer owner."""
        self.closed = True
        tasks = list(self._announcements)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self.manager.async_close()


class Runtime:
    """Keep one API registry and isolated endpoints for all configured satellites."""

    def __init__(self) -> None:
        self.endpoints: dict[str, Endpoint] = {}
        self.unregister_api = None
        self.actions = None
        self.control = None
        self.actions_lock = asyncio.Lock()

    def for_device(self, device_id: str | None) -> Endpoint:
        """Use a trusted origin ID, never a model-supplied entity name."""
        matches = [
            ep for ep in self.endpoints.values() if ep.device_id == device_id and not ep.closed
        ]
        if not device_id or not matches:
            raise TimerError("requesting_satellite_not_configured")
        if len(matches) != 1:
            raise TimerError("requesting_satellite_ambiguous")
        return matches[0]

    def log_operation_error(self, operation: str) -> None:
        """Log failure details locally without including conversation text."""
        _LOGGER.exception("Assist timer operation failed: %s", operation)

    def for_request(self, device_id: str | None, satellite: str | None = None) -> Endpoint:
        """Route voice by origin and text by selection; related file: llm.py.

        A known voice satellite cannot redirect its tools to another satellite.
        Text requests may select only a configured live endpoint.
        """
        live = [ep for ep in self.endpoints.values() if not ep.closed]
        origins = [ep for ep in live if device_id and ep.device_id == device_id]
        if origins:
            origin = self.for_device(device_id)
            if satellite and origin.output.satellite != satellite:
                raise TimerError("cannot_redirect_voice_satellite")
            return origin
        if satellite:
            matches = [ep for ep in live if ep.output.satellite == satellite]
            if len(matches) != 1:
                raise TimerError("target_satellite_not_configured")
            return matches[0]
        if len(live) == 1:
            return live[0]
        if not live:
            raise TimerError("no_satellites_configured")
        raise TimerError("target_satellite_required")
