"""Exercise emergency notification boundaries; related: emergency_tools.py."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

ACTION = "notify.mobile_app_tablet"


async def test_global_language_changes_emergency_notification(ha_contract, load_module, hass):
    emergency = load_module("emergency_tools")
    hass.data["assist_tools"] = SimpleNamespace(language="en")
    hass.services.has_service = lambda domain, service: True
    hass.services.async_call = AsyncMock()
    token = emergency.current_chat_log.set(
        SimpleNamespace(content=[emergency.UserContent("Help me")])
    )
    try:
        tool = emergency.EmergencyTool(ACTION)
        result = await tool.async_call(
            hass, ha_contract.ToolInput(tool.name, {}), ha_contract.LLMContext(device_id="kitchen")
        )
    finally:
        emergency.current_chat_log.reset(token)
    assert not result.error
    assert hass.services.async_call.await_args.args[2] == {
        "title": emergency._EN_TITLE,
        "message": emergency._EN_MESSAGE,
    }


async def test_global_language_setting_is_saved_and_applied(load_module, hass):
    flow = load_module("config_flow").AssistTimersConfigFlow()
    flow.hass = hass
    flow.async_abort = lambda **kwargs: kwargs
    runtime = SimpleNamespace(language="ko", language_store=SimpleNamespace(async_save=AsyncMock()))
    hass.data["assist_tools"] = runtime
    result = await flow.async_step_user({"setup_type": "language", "language": "en"})
    assert result == {"reason": "language_updated"}
    runtime.language_store.async_save.assert_awaited_once_with({"language": "en"})
    assert runtime.language == "en"


@pytest.mark.parametrize("entry_type", ["timer", "emergency", "weather"])
async def test_reconfigure_updates_shared_language(load_module, hass, entry_type):
    config = load_module("config_flow")
    flow = config.AssistTimersConfigFlow()
    flow.hass = hass
    flow.async_show_form = lambda **kwargs: kwargs
    flow.async_abort = lambda **kwargs: kwargs
    store = SimpleNamespace(async_save=AsyncMock())
    endpoint = SimpleNamespace(manager=SimpleNamespace(timer=object()))
    runtime = SimpleNamespace(language="ko", language_store=store, endpoints={"entry": endpoint})
    hass.data["assist_tools"] = runtime
    data = {
        "timer": {"satellite": "assist_satellite.kitchen", "media_player": "media_player.kitchen"},
        "emergency": {"emergency_notify_action": ACTION},
        "weather": {"weather_entity": "weather.home"},
    }[entry_type]
    entry = SimpleNamespace(data=data, entry_id="entry", unique_id="kitchen")
    flow._get_reconfigure_entry = lambda: entry
    hass.services.async_services_for_domain = lambda domain: {"mobile_app_tablet": object()}
    form = await flow.async_step_reconfigure()
    assert "language" in str(form["data_schema"].schema)
    payload = {"language": "en"}
    if entry_type != "weather":
        payload.update(data)
    result = await flow.async_step_reconfigure(payload)
    assert result == {"reason": "reconfigure_successful"}
    store.async_save.assert_awaited_once_with({"language": "en"})
    assert runtime.language == "en"


async def test_invalid_emergency_reconfigure_does_not_change_language(load_module, hass):
    config = load_module("config_flow")
    flow = config.AssistTimersConfigFlow()
    flow.hass = hass
    flow.async_show_form = lambda **kwargs: kwargs
    store = SimpleNamespace(async_save=AsyncMock())
    runtime = SimpleNamespace(language="ko", language_store=store)
    hass.data["assist_tools"] = runtime
    flow._get_reconfigure_entry = lambda: SimpleNamespace(data={"emergency_notify_action": ACTION})
    hass.services.async_services_for_domain = lambda domain: {"mobile_app_tablet": object()}
    result = await flow.async_step_reconfigure(
        {"language": "en", "emergency_notify_action": "notify.other"}
    )
    assert result["errors"] == {"emergency_notify_action": "invalid_emergency_action"}
    store.async_save.assert_not_awaited()
    assert runtime.language == "ko"


def test_timer_announcement_languages(load_module):
    announcement = load_module("timer_manager").timer_announcement
    assert announcement(61, "pause") == "설정한 1분 1초 알람을 일시정지하였습니다."
    assert announcement(61, "pause", "en") == "The 1 minute 1 second timer has been paused."


@pytest.mark.parametrize(
    ("utterance", "external", "accepted"),
    [
        ("살려줘", False, True),
        ("구해 줘!", False, True),
        ("제발 저를 도와주세요", False, True),
        ("ＳＯＳ", False, True),
        ("Help me, I'm hurt", False, True),
        ("¡Ayuda, me caí!", False, True),
        ("살려줘", True, False),
        (None, False, False),
    ],
)
async def test_emergency_requires_user_message_and_prevents_duplicate_calls(
    ha_contract, load_module, hass, utterance, external, accepted
):
    emergency = load_module("emergency_tools")
    hass.services.has_service = lambda domain, service: True
    hass.services.async_call = AsyncMock()
    history = [] if utterance is None else [emergency.UserContent(utterance)]
    token = emergency.current_chat_log.set(SimpleNamespace(content=history))
    context = ha_contract.LLMContext(device_id="kitchen", context=object())
    try:
        api = await emergency.AssistEmergencyAPI(hass, ACTION).async_get_api_instance(context)
        tool = api.tools[0]
        request = ha_contract.ToolInput(tool.name, {}, external=external)
        first, second = await asyncio.gather(
            tool.async_call(hass, request, context), tool.async_call(hass, request, context)
        )
    finally:
        emergency.current_chat_log.reset(token)
    assert first.error == second.error == (not accepted)
    if accepted:
        assert {first.data["status"], second.data["status"]} == {"submitted", "already_submitted"}
        hass.services.async_call.assert_awaited_once_with(
            "notify",
            "mobile_app_tablet",
            {"title": emergency._TITLE, "message": emergency._MESSAGE},
            blocking=True,
            context=context.context,
        )
    else:
        hass.services.async_call.assert_not_called()


@pytest.mark.parametrize("service_present", [False, True])
async def test_unavailable_or_failed_notification_is_reported(
    ha_contract, load_module, hass, service_present
):
    emergency = load_module("emergency_tools")
    hass.services.has_service = lambda domain, service: service_present
    hass.services.async_call = AsyncMock(side_effect=RuntimeError("action failed"))
    token = emergency.current_chat_log.set(
        SimpleNamespace(content=[emergency.UserContent("구해줘")])
    )
    try:
        tool = emergency.EmergencyTool(ACTION)
        result = await tool.async_call(
            hass, ha_contract.ToolInput(tool.name, {}), ha_contract.LLMContext(device_id="kitchen")
        )
    finally:
        emergency.current_chat_log.reset(token)
    assert result.error
    assert hass.services.async_call.await_count == int(service_present)


async def test_setup_and_reconfigure_accept_only_registered_mobile_app_actions(load_module, hass):
    config = load_module("config_flow")
    emergency = load_module("emergency_tools")
    flow = config.AssistTimersConfigFlow()
    flow.hass = hass
    flow.async_show_form = lambda **kwargs: kwargs
    flow.async_create_entry = lambda **kwargs: kwargs
    flow.async_set_unique_id = AsyncMock()
    flow._abort_if_unique_id_configured = lambda: None
    flow.async_update_reload_and_abort = lambda entry, **kwargs: kwargs
    hass.services.async_services_for_domain = lambda domain: {
        "mobile_app_tablet": object(),
        "mobile_app_phone": object(),
        "other_push": object(),
        "mobile_app_": object(),
    }
    field = config.CONF_EMERGENCY_ACTION
    assert emergency.mobile_app_actions(hass) == ["notify.mobile_app_phone", ACTION]
    assert (await flow.async_step_user({"setup_type": "emergency"}))["step_id"] == "emergency"
    assert (await flow.async_step_emergency({field: "notify.other_push"}))["errors"] == {
        field: "invalid_emergency_action"
    }
    result = await flow.async_step_emergency({field: ACTION})
    assert result["data"] == {field: ACTION}
    flow.async_set_unique_id.assert_awaited_once_with("assist_emergency_contact")
    flow._get_reconfigure_entry = lambda: SimpleNamespace(data=result["data"])
    assert (await flow.async_step_reconfigure())["step_id"] == "reconfigure_emergency"
    new_data = {field: "notify.mobile_app_phone"}
    assert await flow.async_step_reconfigure_emergency(new_data) == {"data_updates": new_data}
