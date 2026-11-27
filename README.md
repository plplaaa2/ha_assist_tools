# HA Assist Tools

[🇺🇸 English Version](./README.md) | [🇰🇷 한국어 버전](./README.ko.md)

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=for-the-badge)](https://github.com/hacs/integration)
![version](https://img.shields.io/badge/version-v0.3.5-blue.svg?style=for-the-badge)
[![Built with Codex](https://img.shields.io/badge/Built%20with-OpenAI%20Codex-412991?style=for-the-badge&logo=openai&logoColor=white)](https://openai.com/codex/)
[![kofi](https://img.shields.io/badge/Ko--fi-Support%20Me-F16061?style=for-the-badge&logo=ko-fi)](https://ko-fi.com/plplaaa2)

A Home Assistant HACS custom integration that provides Assist timers, scheduled device control, weather, and climate/fan mode control.

## Features

- Send a Companion App notification from Assist when someone asks for emergency help.
- Set timers and schedule device shutdowns
- List, reschedule, or cancel device jobs by voice, and repeat device actions on selected weekdays
- Set one-time or weekly clock alarms on the requesting satellite's speaker
- Control climate and fan modes
- Set fan and air purifier presets, oscillation, and direction
- View device state history
- Query current conditions and up to seven days of forecasts from a selected Home Assistant weather entity

## Automatic and manual installation

### Automatic installation (recommended)

[![Open your Home Assistant instance and open a repository in the Marketplace.](https://my.home-assistant.io/badges/marketplace_repository.svg)](https://my.home-assistant.io/redirect/marketplace_repository/?owner=plplaaa2&repository=ha_assist_tools&category=integration)

### Manual installation

Copy the entire `custom_components/assist_tools/` directory to the same path in your Home Assistant configuration directory, then restart Home Assistant.

## Setup

When adding Assist Tools, choose the feature to configure, including **Assist Timer**, **Assist Weather**, or **Assist Emergency Contact**. Enable each desired provider in your existing LLM conversation agent.

Choose the shared output language on the first setup screen. It applies to timer announcements and emergency notifications across all entries and defaults to Korean. Change it from the reconfigure menu of any existing Assist Tools entry.

### Assist Timer setup

1. In **Settings → Devices & services → Add integration → Assist Tools**, select a satellite. If exactly one available media player has the same display name, it is connected automatically. If there is no unique match, a player selection screen appears. A player already used by another satellite timer is not selected automatically. You can change the player later through reconfiguration.
2. The satellite must support `ANNOUNCE`; the media player must support `PLAY_MEDIA` and `STOP`. Configure each satellite once, and do not connect a player to multiple entries.
3. In your existing LLM conversation agent, enable **Assist Timer** under **Tool providers / Home Assistant API selection**. Menu names vary by conversation agent. You do not need to switch to a new conversation agent.
4. In a test voice pipeline, turn off **Prefer handling commands locally**. When enabled, Home Assistant's built-in agent may handle timer requests first, so this provider may not be called.
5. Selecting the built-in Assist API as well can enable control of other devices, but may also expose a built-in timer tool. Select only Assist Timer first to verify that timer tool calls work.
6. The satellite and media player must be able to reach Home Assistant's internal URL. Check the internal URL in Home Assistant network settings.

Ordinary media playback may replace the media currently playing on the selected player. The source MP3 is preserved, and the integration plays a repeated `alarm_60s.mp3`. Playback is limited to 60 seconds, and an automatic stop is also scheduled. The public static URL `/assist_tools/sounds/alarm_60s.mp3` serves only this alarm file.

With **Assist Timer**, ask for a clock alarm tomorrow at 7 AM or on selected weekdays. You can also list, reschedule, cancel, or silence alarms by voice. Clock alarms ring on the individual speaker bound to the requesting satellite. For text requests with multiple configured speakers, specify the speaker. In the Assist Tools integration options, choose separate completion and clock alarm sounds from **My media** or leave either field clear to use the bundled sound.

### Assist + Google Speaker setup

Choose **Assist + Google Speaker** when adding Assist Tools, then select the microphone-only Assist satellite and the Google Cast speaker. Enable **Assist Timer** and **Assist Scheduled Control** in the existing LLM conversation agent.

With **Assist Scheduled Control**, ask Assist to open the living room curtains at 7 AM on Mondays, Wednesdays, and Fridays, list curtain schedules, move the next run to 8 AM, or remove Wednesdays from the recurring schedule. Weekly rules use Home Assistant local time. A next-run override must precede the following regular run. Cancelling a weekly schedule cancels the series.

Timer alarms play on the Google speaker. The satellite can run timer and scheduled control commands without announcement support. For regular Assist replies on the same speaker, forward the URL from ESPHome's `on_tts_end` to Home Assistant's `media_player.play_media`. The Google speaker must be able to reach Home Assistant's internal URL.

### Assist Emergency Contact setup

Choose **Assist Emergency Contact** when adding Assist Tools and select the receiving device's
`notify.mobile_app_*` action. Enable **Assist Emergency Contact** in your existing LLM
conversation agent. Use the integration's reconfigure menu to change the receiving device.

Direct pleas such as “살려줘”, “구해줘”, “Help me”, and “SOS” request a notification
immediately. The LLM conversation agent judges requests in any language, so recognition varies
by model. Assist reports whether Home Assistant ran the action. Check the receiving device
to verify delivery.

## License

The project code is licensed under the [MIT License](LICENSE). The bundled alarm audio and its loop use the terms of the [original Pixabay sound](https://pixabay.com/sound-effects/beep-alarm-366507/).

## Contributors

- [plplaaa2](https://github.com/plplaaa2) — project owner and maintainer
- [OpenAI Codex](https://openai.com/codex/) — AI coding assistant
