# Changelog

## [0.3.4] - 2026-11-24

- Add Assist Emergency Contact. Select a registered Home Assistant Companion App notification action when setting up the integration and enable the provider in your LLM conversation agent.
- Let the LLM identify direct rescue requests in any language and send a notification immediately. Suppress duplicate submissions within a turn.
- Change the receiving device through the integration's reconfigure menu.
- Select a shared Korean or English language for timer announcements and emergency notifications.
- Change the shared output language from any existing Assist Tools entry's reconfiguration screen.

## [0.3.3] - 2026-11-20

- Add scheduled opening and closing for supported cover entities, such as curtains and blinds, through Assist and Home Assistant actions.
- Reschedule pending device actions and repeat them on selected weekdays at a Home Assistant local time. Change the next occurrence or edit the recurring rule.
- Set one-time and weekly clock alarms with Assist. Alarms ring on the individual speaker bound to the requesting satellite and can be listed, rescheduled, cancelled, or silenced by voice.
- Choose separate timer completion and clock alarm sounds from Home Assistant's local media in the integration options. Each option defaults to the bundled sound.
- Include selected weather entity attributes in Assist Weather responses.

## [0.3.2] - 2026-11-17

- Load Recorder history time zones asynchronously to avoid blocking Home Assistant's event loop.
- Keep the automatic alarm stop scheduled when both speaker stop and persistence fail.
- Publish timer endpoints only after restoration and serialize duplicate ownership checks.
- Accept hours, minutes, and seconds for timers while preserving total-seconds input.
- Add the Marketplace voice assistants category and resolve hassfest configuration and checkout warnings.

## [0.3.1] - 2026-11-12

- Added HACS and hassfest validation for the Home Assistant Marketplace catalog submission.

## [0.3.0] - 2026-11-12

- Added Assist + Google Speaker setup for microphone-only Assist satellites.
- Play timer alarms on a linked Google Cast speaker and route timer and scheduled control requests from the satellite without requiring satellite announcements.

## [0.2.0] - 2026-10-27

### New Features

- Added the Assist Scheduled Control provider to schedule, list, and cancel delayed device actions.
- Added the Assist Climate provider for supported HVAC and fan modes, with per-device capability checks.
- Added the Assist Fan provider for supported presets, oscillation, and blade direction controls.
- Added the read-only Assist History provider for querying entity state changes from Recorder.
- Added the Assist Weather provider for current conditions and daily forecasts from a selected weather entity.
- Added automatic satellite and media-player matching when their display names uniquely match.
- Added durable timer restoration and delayed-action persistence across Home Assistant restarts.
- Added timer tools for text conversations without a registered originating satellite.

### Bug Fixes

- Avoided duplicate speech by leaving successful timer-creation confirmations to the existing conversation agent.
- Prevented timers from being assigned to unavailable, already-claimed, or ambiguous media players.
- Improved timer cancellation, restoration, and stale-callback handling to avoid unintended playback.
- Revalidated device exposure, caller permissions, availability, and supported capabilities before executing controls.
- Prevented overdue or interrupted scheduled actions from being replayed after restart.
- Improved provider registration rollback and configuration validation when setup fails.
