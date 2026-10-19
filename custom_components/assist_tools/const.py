"""Shared settings; related files: setup, config flow, LLM, and satellite modules."""

DOMAIN = "assist_tools"
# Preserve the selectable timer provider ID; related files: llm and existing agent settings.
API_ID = "assist_timers"
CONF_SATELLITE = "satellite"
CONF_PLAYER = "media_player"
CONF_WEATHER_ENTITY = "weather_entity"
ALARM_SECONDS = 60
MAX_DURATION = 604800
ALARM_FILE = "alarm_60s.mp3"
ALARM_URL = f"/{DOMAIN}/sounds/{ALARM_FILE}"
STORAGE_VERSION = 1
