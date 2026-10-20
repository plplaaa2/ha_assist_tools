"""Expose a selected weather entity to Assist; related files: llm, config_flow, translations."""

import logging
from typing import Any

import probatio
from homeassistant.components.weather import DOMAIN as WEATHER_DOMAIN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm

_LOGGER = logging.getLogger(__name__)


class WeatherTool(llm.Tool):
    """Read current conditions and a daily forecast from a weather entity."""

    name = "assist_weather__get_weather"
    title = "날씨 조회"
    description = (
        "Get current conditions and the daily forecast from the configured Home Assistant weather entity. "
        "Always call this tool before answering weather questions."
    )
    integration = "assist_tools"
    annotations = llm.ToolAnnotations(
        read_only=True, destructive=False, idempotent=True, open_world=False
    )
    parameters = probatio.Schema({})

    def __init__(self, entity_id: str) -> None:
        """Bind this tool to the entity selected in the config flow."""
        self._entity_id = entity_id

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> llm.ToolResult:
        """Return standard weather state attributes and a daily forecast."""
        state = hass.states.get(self._entity_id)
        if state is None or state.state in ("unknown", "unavailable"):
            return llm.ToolResult(
                data={"error": f"날씨 엔티티 '{self._entity_id}'를 사용할 수 없습니다."},
                error=True,
            )

        current: dict[str, Any] = {
            key: state.attributes.get(key)
            for key in (
                "temperature",
                "apparent_temperature",
                "humidity",
                "pressure",
                "wind_speed",
                "wind_bearing",
                "precipitation",
                "uv_index",
                "visibility",
            )
        }
        current["condition"] = state.state
        try:
            response = await hass.services.async_call(
                WEATHER_DOMAIN,
                "get_forecasts",
                {"type": "daily"},
                target={"entity_id": self._entity_id},
                blocking=True,
                return_response=True,
            )
            forecast_data = response.get(self._entity_id, {}).get("forecast", [])
        except Exception as err:
            _LOGGER.warning("Daily forecast lookup failed for %s: %s", self._entity_id, err)
            forecast_data = []

        forecast = [
            {
                key: item.get(key)
                for key in (
                    "datetime",
                    "condition",
                    "temperature",
                    "templow",
                    "precipitation_probability",
                    "precipitation",
                    "wind_speed",
                    "wind_bearing",
                )
                if item.get(key) is not None
            }
            for item in forecast_data[:7]
        ]
        return llm.ToolResult(
            data={
                "entity_id": self._entity_id,
                "location": state.attributes.get("friendly_name", self._entity_id),
                "current": {key: value for key, value in current.items() if value is not None},
                "forecast_daily": forecast,
            }
        )


class AssistWeatherAPI(llm.API):
    """Expose the configured weather entity as a selectable Assist API."""

    def __init__(self, hass: HomeAssistant, entity_id: str, name: str) -> None:
        super().__init__(
            hass=hass,
            id=f"assist_weather_{entity_id.replace('.', '_')}",
            name=f"Assist Weather: {name}",
        )
        self._entity_id = entity_id

    async def async_get_api_instance(self, llm_context: llm.LLMContext) -> llm.APIInstance:
        """Return the weather tool and behavior guidance for this instance."""
        return llm.APIInstance(
            api=self,
            api_prompt=(
                "Call assist_weather__get_weather before answering weather questions. "
                "It returns current conditions and the daily forecast from the configured weather entity. "
                "Give practical advice supported by the returned conditions and forecast; "
                "do not infer conditions or risks absent from the result."
            ),
            llm_context=llm_context,
            tools=[WeatherTool(self._entity_id)],
        )
