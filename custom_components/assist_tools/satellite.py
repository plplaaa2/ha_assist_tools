"""Separate announcement speech from normal alarm media; related files: setup/LLM.

Playback is always directed at the explicitly configured player. No area-wide
targets or firmware-specific timer switches are used.
"""

import asyncio
import logging
from collections.abc import Callable

from homeassistant.components.assist_satellite import AssistSatelliteEntityFeature
from homeassistant.components.media_player import MediaPlayerEntityFeature
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers.network import get_url

from .const import ALARM_URL
from .timer_manager import TimerError

_LOGGER = logging.getLogger(__name__)


def validate_entities(hass: HomeAssistant, satellite: str, player: str) -> None:
    """Require announcement, play, and stop capabilities on selected entities."""
    sat = hass.states.get(satellite)
    media = hass.states.get(player)
    if sat is None or not satellite.startswith("assist_satellite."):
        raise TimerError("invalid_satellite")
    if media is None or not player.startswith("media_player."):
        raise TimerError("invalid_player")
    if not sat.attributes.get("supported_features", 0) & AssistSatelliteEntityFeature.ANNOUNCE:
        raise TimerError("announce_not_supported")
    required = MediaPlayerEntityFeature.PLAY_MEDIA | MediaPlayerEntityFeature.STOP
    if media.attributes.get("supported_features", 0) & required != required:
        raise TimerError("play_stop_not_supported")


class SatelliteOutput:
    """Route control to one satellite and one normal media player."""

    def __init__(self, hass: HomeAssistant, satellite: str, player: str) -> None:
        self.hass = hass
        self.satellite = satellite
        self.player = player
        self._announcement_lock = asyncio.Lock()

    async def _call(self, domain: str, service: str, data: dict) -> None:
        async with asyncio.timeout(10):
            await self.hass.services.async_call(domain, service, data, blocking=True)

    async def async_play(self) -> None:
        """Play the finite alarm as normal media, never as an announcement."""
        validate_entities(self.hass, self.satellite, self.player)
        state = self.hass.states.get(self.player)
        if state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            raise TimerError("player_unavailable")
        url = get_url(self.hass, prefer_external=False, allow_cloud=False).rstrip("/")
        await self._call(
            "media_player",
            "play_media",
            {
                "entity_id": self.player,
                "media_content_id": url + ALARM_URL,
                "media_content_type": "music",
            },
        )

    async def async_stop(self) -> None:
        """Send a real STOP command, not just cancellation of a Python task."""
        await self._call("media_player", "media_stop", {"entity_id": self.player})

    async def async_announce(self, message: str, still_valid: Callable[[], bool]) -> None:
        """Wait for the originating pipeline to finish before announcing."""
        async with self._announcement_lock:
            for _ in range(300):
                if not still_valid():
                    return
                state = self.hass.states.get(self.satellite)
                if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
                    _LOGGER.warning("Confirmation satellite is unavailable: %s", self.satellite)
                    return
                if state.state == "idle":
                    break
                await asyncio.sleep(0.1)
            else:
                _LOGGER.warning(
                    "Confirmation skipped because satellite remained busy: %s", self.satellite
                )
                return
            if still_valid():
                # Announce has no pre-chime; only the requested Korean message.
                async with asyncio.timeout(30):
                    await self.hass.services.async_call(
                        "assist_satellite",
                        "announce",
                        {"entity_id": self.satellite, "message": message, "preannounce": False},
                        blocking=True,
                    )
