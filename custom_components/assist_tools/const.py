"""Shared settings; related files: setup, config flow, LLM, and satellite modules."""

DOMAIN = "assist_tools"
# Preserve the selectable timer provider ID; related files: llm and existing agent settings.
API_ID = "assist_timers"
CONF_SATELLITE = "satellite"
CONF_PLAYER = "media_player"
CONF_GOOGLE_SPEAKER = "google_speaker"
CONF_WEATHER_ENTITY = "weather_entity"
CONF_EMERGENCY_ACTION = "emergency_notify_action"
# Global output language; related: config_flow.py, runtime.py and message providers.
CONF_LANGUAGE = "language"
ALARM_SECONDS = 60
MAX_DURATION = 604800
ALARM_FILE = "alarm_60s.mp3"
ALARM_URL = f"/{DOMAIN}/sounds/{ALARM_FILE}"
# Separate per-speaker sounds; related: config_flow.py, satellite.py.
CONF_TIMER_SOUND = "timer_sound"
CONF_ALARM_SOUND = "alarm_sound"
ALARM_ACTION = "ring_alarm"
STORAGE_VERSION = 1
