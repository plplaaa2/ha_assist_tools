"""Exercise the mobile action and transcript boundaries; related: emergency_tools.py."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

ACTION = "notify.mobile_app_tablet"


@pytest.mark.parametrize(
    ("utterance", "external", "accepted"),
    [
        ("살려줘", False, True),
        ("구해 줘!", False, True),
        ("제발 저를 도와주세요", False, True),
        ("ＳＯＳ", False, True),
        ("살려줘라고 말하면 어떻게 돼?", False, False),
        ("“구해줘”", False, False),
        ("숙제 좀 도와줘", False, False),
        ("살려줘", True, False),
        (None, False, False),
    ],
)
async def test_emergency_requires_original_direct_request(
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
