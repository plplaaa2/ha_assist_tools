# Changelog

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
