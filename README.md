# Assist Tools

[🇺🇸 English Version](./README.md) | [🇰🇷 한국어 버전](./README.ko.md)

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=for-the-badge)](https://github.com/hacs/integration)
![version](https://img.shields.io/badge/version-v0.1.0-blue.svg?style=for-the-badge)
[![Built with Codex](https://img.shields.io/badge/Built%20with-OpenAI%20Codex-412991?style=for-the-badge&logo=openai&logoColor=white)](https://openai.com/codex/)
[![kofi](https://img.shields.io/badge/Ko--fi-Support%20Me-F16061?style=for-the-badge&logo=ko-fi)](https://ko-fi.com/plplaaa2)

A Home Assistant HACS custom integration that provides Assist timers, scheduled device control, and climate mode control. It can also schedule devices to turn on or off after a delay.

## Features

- Set timers and schedule device shutdowns
- Control climate and fan modes
- Set fan and air purifier presets, oscillation, and direction
- View device state history

## Automatic and manual installation

### Automatic installation (recommended)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=plplaaa2&repository=ha_assist_tools)

### Manual installation

Copy the entire `custom_components/assist_tools/` directory to the same path in your Home Assistant configuration directory, then restart Home Assistant.

## Setup

1. In **Settings → Devices & services → Add integration → Assist Tools**, select a satellite. If exactly one available media player has the same display name, it is connected automatically. If there is no unique match, a player selection screen appears. A player already used by another satellite timer is not selected automatically. You can change the player later through reconfiguration.
2. The satellite must support `ANNOUNCE`; the media player must support `PLAY_MEDIA` and `STOP`. Configure each satellite once, and do not connect a player to multiple entries.
3. In your existing LLM conversation agent, enable **Assist Timer** under **Tool providers / Home Assistant API selection**. Menu names vary by conversation agent. You do not need to switch to a new conversation agent.
4. In a test voice pipeline, turn off **Prefer handling commands locally**. When enabled, Home Assistant's built-in agent may handle timer requests first, so this provider may not be called.
5. Selecting the built-in Assist API as well can enable control of other devices, but may also expose a built-in timer tool. Select only Assist Timer first to verify that timer tool calls work.
6. The satellite and media player must be able to reach Home Assistant's internal URL. Check the internal URL in Home Assistant network settings.

Ordinary media playback may replace the media currently playing on the selected player. The source MP3 is preserved, and the integration plays a repeated `alarm_60s.mp3`. Playback is limited to 60 seconds, and an automatic stop is also scheduled. The public static URL `/assist_tools/sounds/alarm_60s.mp3` serves only this alarm file.

## Contributors

- [plplaaa2](https://github.com/plplaaa2) — project owner and maintainer
- [OpenAI Codex](https://openai.com/codex/) — AI coding assistant
