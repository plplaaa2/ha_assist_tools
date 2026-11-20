"""Separate announcement speech from normal alarm media; related files: setup/LLM.

Playback is always directed at the explicitly configured player. No area-wide
targets or firmware-specific timer switches are used.
"""

import asyncio
import logging
from collections.abc import Callable

from homeassistant.components.assist_satellite import AssistSatelliteEntityFeature
from homeassistant.components.media_player import (
    MediaPlayerEntityFeature,
    async_process_play_media_url,
)
from homeassistant.components.media_source import async_resolve_media
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.network import get_url

from .const import ALARM_SECONDS, ALARM_URL, CONF_ALARM_SOUND, CONF_TIMER_SOUND
from .timer_manager import TimerError

_LOGGER = logging.getLogger(__name__)


def validate_entities(
    hass: HomeAssistant, satellite: str, player: str, *, google_speaker: bool = False
) -> None:
    """Validate alarm output capabilities; related: config_flow.py and __init__.py."""
    sat = hass.states.get(satellite)
    media = hass.states.get(player)
    if sat is None or not satellite.startswith("assist_satellite."):
        raise TimerError("invalid_satellite")
    if media is None or not player.startswith("media_player."):
        raise TimerError("invalid_player")
    if not google_speaker and not (
        sat.attributes.get("supported_features", 0) & AssistSatelliteEntityFeature.ANNOUNCE
    ):
        raise TimerError("announce_not_supported")
    required = MediaPlayerEntityFeature.PLAY_MEDIA | MediaPlayerEntityFeature.STOP
    if media.attributes.get("supported_features", 0) & required != required:
        raise TimerError("play_stop_not_supported")


class SatelliteOutput:
    """Route control to one satellite and one normal media player."""

    def __init__(
        self,
        hass: HomeAssistant,
        satellite: str,
        player: str,
        *,
        google_speaker: bool = False,
        sounds: Callable[[], dict] = dict,
    ) -> None:
        """Configure alarm playback and optional confirmations; related: llm.py."""
        self.hass = hass
        self.satellite = satellite
        self.player = player
        self.google_speaker = google_speaker
        self._sounds = sounds
        self._announcement_lock = asyncio.Lock()
        self._playback_lock = asyncio.Lock()
        self._alarm: tuple[str, asyncio.Event] | None = None
        self._alarms_closed = False
        self._timer_superseded = False

    async def _call(self, domain: str, service: str, data: dict) -> None:
        async with asyncio.timeout(10):
            await self.hass.services.async_call(domain, service, data, blocking=True)

    async def async_play(self) -> None:
        """Play a timer sound without interrupting a ringing clock alarm."""
        async with self._playback_lock:
            if self._alarm is not None:
                raise TimerError("clock_alarm_already_ringing")
            self._timer_superseded = False
            await self._play_sound(CONF_TIMER_SOUND)

    async def _play_sound(self, option: str) -> None:
        validate_entities(
            self.hass, self.satellite, self.player, google_speaker=self.google_speaker
        )
        state = self.hass.states.get(self.player)
        if state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            raise TimerError("player_unavailable")
        sound = self._sounds().get(option)
        if sound:
            try:
                media = await async_resolve_media(self.hass, sound["media_content_id"], self.player)
                content_id = async_process_play_media_url(self.hass, media.url)
                content_type = media.mime_type
            except Exception:
                _LOGGER.warning("Custom sound unavailable for %s; playing the default", self.player)
                sound = None
        if not sound:
            url = get_url(self.hass, prefer_external=False, allow_cloud=False).rstrip("/")
            content_id = url + ALARM_URL
            content_type = "music"
        await self._call(
            "media_player",
            "play_media",
            {
                "entity_id": self.player,
                "media_content_id": content_id,
                "media_content_type": content_type,
            },
        )

    async def async_stop(self) -> None:
        """Stop timer playback while preserving the active clock alarm."""
        async with self._playback_lock:
            if self._alarm is None and not self._timer_superseded:
                await self._stop_player()

    async def _stop_player(self) -> None:
        await self._call("media_player", "media_stop", {"entity_id": self.player})

    # Keep clock playback bound to a single configured player; related: runtime.py, llm.py.
    def validate_alarm_player(self, player: str) -> None:
        if player != self.player:
            raise TimerError("alarm_speaker_changed")
        state = self.hass.states.get(player)
        entity = er.async_get(self.hass).async_get(player)
        device = (
            dr.async_get(self.hass).async_get(entity.device_id)
            if entity and entity.device_id
            else None
        )
        members = state.attributes.get("group_members") if state else None
        model = (device.model or "").casefold() if device else ""
        if (
            (entity and entity.platform in ("group", "universal"))
            or model == "group"
            or model.endswith(" group")
            or (members and set(members) - {player})
        ):
            raise TimerError("alarm_requires_individual_speaker")

    async def async_ring_alarm(self, job_id: str, player: str) -> None:
        async with self._playback_lock:
            self.validate_alarm_player(player)
            if self._alarms_closed or self._alarm is not None:
                raise TimerError("clock_alarm_output_busy")
            finished = asyncio.Event()
            self._alarm = (job_id, finished)
            self._timer_superseded = True
            try:
                await self._play_sound(CONF_ALARM_SOUND)
            except BaseException:
                try:
                    await self._stop_player()
                except Exception:
                    _LOGGER.exception("Could not stop failed clock alarm playback")
                self._alarm = None
                raise
        try:
            async with asyncio.timeout(ALARM_SECONDS):
                await finished.wait()
        except TimeoutError:
            await self.async_stop_alarm(job_id)

    async def async_stop_alarm(self, job_id: str | None = None) -> bool:
        async with self._playback_lock:
            if self._alarm is None or (job_id is not None and self._alarm[0] != job_id):
                return False
            await self._stop_player()
            self._alarm[1].set()
            self._alarm = None
            return True

    async def async_close_alarms(self) -> None:
        self._alarms_closed = True
        try:
            await self.async_stop_alarm()
        except Exception:
            self._alarms_closed = False
            raise

    async def async_announce(self, message: str, still_valid: Callable[[], bool]) -> None:
        """Wait for the originating pipeline to finish before announcing."""
        if self.google_speaker:
            return
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
