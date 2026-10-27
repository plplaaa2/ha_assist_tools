"""Assist Weather provider behavior; related files: weather_tools and config_flow."""

from types import SimpleNamespace
from unittest.mock import AsyncMock


async def test_weather_tool_returns_current_state_and_seven_forecast_days(
    load_module, hass, ha_contract
):
    state = SimpleNamespace(
        state="rainy",
        attributes={
            "friendly_name": "Home",
            "temperature": 12,
            "humidity": 75,
            "pressure": 1008,
        },
    )
    hass.state_values["weather.home"] = state
    forecast = [{"datetime": f"2026-10-{19 + day:02}", "condition": "rainy"} for day in range(8)]
    hass.services.async_call = AsyncMock(return_value={"weather.home": {"forecast": forecast}})
    tool = load_module("weather_tools").WeatherTool("weather.home")
    result = await tool.async_call(
        hass,
        ha_contract.ToolInput(tool.name, {}),
        ha_contract.LLMContext(None),
    )
    assert result.data["location"] == "Home"
    assert result.data["current"] == {
        "temperature": 12,
        "humidity": 75,
        "pressure": 1008,
        "condition": "rainy",
    }
    assert len(result.data["forecast_daily"]) == 7
    hass.services.async_call.assert_awaited_once_with(
        "weather",
        "get_forecasts",
        {"type": "daily"},
        target={"entity_id": "weather.home"},
        blocking=True,
        return_response=True,
    )


async def test_weather_tool_keeps_current_conditions_when_forecast_fails(
    load_module, hass, ha_contract
):
    hass.state_values["weather.home"] = SimpleNamespace(
        state="sunny", attributes={"temperature": 20}
    )
    hass.services.async_call = AsyncMock(side_effect=RuntimeError("unsupported"))
    tool = load_module("weather_tools").WeatherTool("weather.home")
    result = await tool.async_call(
        hass,
        ha_contract.ToolInput(tool.name, {}),
        ha_contract.LLMContext(None),
    )
    assert result.data["current"] == {"temperature": 20, "condition": "sunny"}
    assert result.data["forecast_daily"] == []


async def test_weather_config_flow_creates_entry_for_selected_entity(load_module, hass):
    state = SimpleNamespace(attributes={"friendly_name": "Home"})
    hass.state_values["weather.home"] = state
    flow = load_module("config_flow").AssistTimersConfigFlow()
    flow.hass = hass
    flow.async_set_unique_id = AsyncMock()
    flow._abort_if_unique_id_configured = lambda: None
    flow.async_create_entry = lambda **kwargs: kwargs
    result = await flow.async_step_weather({"weather_entity": "weather.home"})
    assert result == {"title": "Home", "data": {"weather_entity": "weather.home"}}
    flow.async_set_unique_id.assert_awaited_once_with("weather.home")


async def test_weather_provider_has_entity_specific_id_and_tool(load_module, hass, ha_contract):
    api = load_module("weather_tools").AssistWeatherAPI(hass, "weather.home", "Home")
    instance = await api.async_get_api_instance(ha_contract.LLMContext(None))
    assert api.id == "assist_weather_weather_home"
    assert api.name == "Assist Weather: Home"
    assert [tool.name for tool in instance.tools] == ["assist_weather__get_weather"]


async def test_weather_setup_choice_and_entry_lifecycle(load_module, hass, ha_contract):
    state = SimpleNamespace(attributes={"friendly_name": "Home"})
    hass.state_values["weather.home"] = state
    flow = load_module("config_flow").AssistTimersConfigFlow()
    flow.hass = hass
    flow.async_show_form = lambda **kwargs: kwargs
    assert (await flow.async_step_user({"setup_type": "weather"}))["step_id"] == "weather"
    assert (await flow.async_step_user({"setup_type": "timer"}))["step_id"] == "satellite"

    registered = []
    original_register = ha_contract.apis
    entry = SimpleNamespace(
        entry_id="weather-home",
        data={"weather_entity": "weather.home"},
        async_on_unload=lambda callback: registered.append(callback),
    )
    setup = load_module("__init__")
    assert await setup.async_setup_entry(hass, entry)
    assert ha_contract.apis["assist_weather_weather_home"].name == "Assist Weather: Home"
    assert len(registered) == 1
    assert await setup.async_unload_entry(hass, entry)
    assert "assist_weather_weather_home" in ha_contract.apis
    registered.pop()()
    assert "assist_weather_weather_home" not in ha_contract.apis
    assert original_register is ha_contract.apis
