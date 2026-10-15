"""Shared settings; related files: setup, config flow, LLM, and satellite modules."""

DOMAIN = "assist_timers"
API_ID = DOMAIN
CONF_SATELLITE = "satellite"
CONF_PLAYER = "media_player"
ALARM_SECONDS = 60
MAX_DURATION = 604800
ALARM_FILE = "alarm_60s.mp3"
ALARM_URL = f"/assist_timers/sounds/{ALARM_FILE}"
STORAGE_VERSION = 1
