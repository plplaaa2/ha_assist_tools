"""Verify fan capabilities and service boundaries; related files: fan_tools/llm."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.fixture
def fan(hass, load_module):
    # Feature values verified against HA 2026.10.1 fan/const.py.
    for name, features, presets in (
        ("full", 14, ["sleep", "natural"]),
        ("preset", 8, ["auto"]),
        ("oscillate", 2, []),
        ("direction", 4, []),
        ("basic", 1, []),
        ("legacy", 1, ["breeze"]),
    ):
        entity = "fan." + name
        hass.state_values[entity] = SimpleNamespace(
            entity_id=entity,
            name=name,
            state="on",
            attributes={"supported_features": features, "preset_modes": presets},
        )
        hass.exposed.add(entity)
    return load_module("fan_tools")


async def available(fan, hass, ha_contract):
    instance = await fan.AssistFanAPI(hass, "assist_fan", "Assist Fan").async_get_api_instance(
        ha_contract.LLMContext(None)
    )
    return {tool.operation: tool for tool in instance.tools}, instance


async def call(tool, hass, ha_contract, args):
    context = SimpleNamespace(user_id="user")
    return await tool.async_call(
        hass, ha_contract.ToolInput(tool.name, args), ha_contract.LLMContext(None, context=context)
    )


async def test_only_requested_setting_tools_are_exposed(fan, hass, ha_contract):
    tools, instance = await available(fan, hass, ha_contract)
    assert {tool.name for tool in tools.values()} == {
        "AssistFanSetPresetMode",
        "AssistFanSetOscillation",
        "AssistFanSetDirection",
    }
    assert '"fan.basic"' not in instance.api_prompt
    hass.services.async_call.assert_not_awaited()


@pytest.mark.parametrize(
    "operation,key,value,service,entity",
    [
        ("preset", "preset_mode", "sleep", "set_preset_mode", "full"),
        ("preset", "preset_mode", "breeze", "set_preset_mode", "legacy"),
        ("oscillate", "oscillating", True, "oscillate", "oscillate"),
        ("oscillate", "oscillating", False, "oscillate", "full"),
        ("direction", "direction", "forward", "set_direction", "direction"),
        ("direction", "direction", "reverse", "set_direction", "full"),
    ],
)
async def test_exact_service_payload_and_context(
    fan, hass, ha_contract, operation, key, value, service, entity
):
    tools, _ = await available(fan, hass, ha_contract)
    args = {"entity_id": "fan." + entity, key: value}
    result = await call(tools[operation], hass, ha_contract, args)
    assert not result.error and result.data["command_sent"]
    sent = hass.services.async_call.call_args
    assert sent.args == ("fan", service, args)
    assert sent.kwargs["blocking"] and sent.kwargs["context"].user_id == "user"
    assert hass.services.async_call.await_count == 1


@pytest.mark.parametrize(
    "operation,args",
    [
        ("preset", {"entity_id": "fan.preset", "preset_mode": "sleep"}),
        ("oscillate", {"entity_id": "fan.direction", "oscillating": True}),
        ("direction", {"entity_id": "fan.oscillate", "direction": "reverse"}),
        ("direction", {"entity_id": "fan.full", "direction": "left"}),
        ("oscillate", {"entity_id": "fan.full", "oscillating": "true"}),
        ("preset", {"entity_id": "fan.full", "preset_mode": "sleep", "percentage": 50}),
        ("preset", {"entity_id": "climate.fake", "preset_mode": "sleep"}),
    ],
)
async def test_unsupported_or_invalid_requests_never_call_services(
    fan, hass, ha_contract, operation, args
):
    tools, _ = await available(fan, hass, ha_contract)
    result = await call(tools[operation], hass, ha_contract, args)
    assert result.error
    hass.services.async_call.assert_not_awaited()


@pytest.mark.parametrize(
    "change",
    ["hidden", "denied", "inactive", "unavailable", "missing", "feature", "preset", "service"],
)
async def test_execution_rechecks_device(fan, hass, ha_contract, change):
    tools, _ = await available(fan, hass, ha_contract)
    state = hass.state_values["fan.full"]
    if change == "hidden":
        hass.exposed.clear()
    elif change == "denied":
        hass.auth.async_get_user.return_value.permissions.check_entity = lambda *args: False
    elif change == "inactive":
        hass.auth.async_get_user.return_value.is_active = False
    elif change == "unavailable":
        state.state = "unavailable"
    elif change == "missing":
        del hass.state_values["fan.full"]
    elif change == "feature":
        state.attributes["supported_features"] = 0
    elif change == "preset":
        state.attributes["preset_modes"] = ["auto"]
    elif change == "service":
        hass.services.has_service = lambda *args: False
    result = await call(
        tools["preset"], hass, ha_contract, {"entity_id": "fan.full", "preset_mode": "sleep"}
    )
    assert result.error
    hass.services.async_call.assert_not_awaited()


async def test_hidden_targets_and_unsupported_operations_omitted(fan, hass, ha_contract):
    hass.exposed = {"fan.oscillate"}
    tools, instance = await available(fan, hass, ha_contract)
    assert set(tools) == {"oscillate"}
    assert '"fan.full"' not in instance.api_prompt
    hass.exposed.clear()
    tools, _ = await available(fan, hass, ha_contract)
    assert not tools


async def test_failed_service_does_not_retry(fan, hass, ha_contract):
    tools, _ = await available(fan, hass, ha_contract)
    hass.services.async_call = AsyncMock(side_effect=TimeoutError)
    result = await call(
        tools["direction"], hass, ha_contract, {"entity_id": "fan.full", "direction": "reverse"}
    )
    assert result.error and result.data["error"] == "fan_operation_failed"
    assert hass.services.async_call.await_count == 1


async def test_fan_registration_failure_rolls_back_other_providers(
    fan, hass, load_module, ha_contract, monkeypatch
):
    module = load_module("llm")
    original = module.llm.async_register_api

    def register(hass, api):
        if api.id == "assist_fan":
            raise ValueError("fan registration failed")
        return original(hass, api)

    monkeypatch.setattr(module.llm, "async_register_api", register)
    with pytest.raises(ValueError):
        module.register_api(hass)
    assert not ha_contract.apis
