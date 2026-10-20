"""Shared settings; related files: setup, config flow, LLM, and satellite modules."""

DOMAIN = "assist_tools"
# Preserve the selectable timer provider ID; related files: llm and existing agent settings.
API_ID = "assist_timers"
CONF_SATELLITE = "satellite"
CONF_PLAYER = "media_player"
CONF_WEATHER_ENTITY = "weather_entity"
CONF_TV_METHOD = "tv_method"
CONF_TV_REMOTE = "tv_remote"
CONF_TV_PLAYER = "tv_player"
CONF_TV_APPS = "tv_apps"
ALARM_SECONDS = 60
MAX_DURATION = 604800
ALARM_FILE = "alarm_60s.mp3"
ALARM_URL = f"/{DOMAIN}/sounds/{ALARM_FILE}"
STORAGE_VERSION = 1
