"""Check Recorder history contracts and limits; related files: history_tools/llm."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

START = datetime(2026, 10, 1, tzinfo=UTC)
END = START + timedelta(hours=12)


def row(at, state, **attrs):
    return SimpleNamespace(last_updated=at, state=state, attributes=attrs)


@pytest.fixture
def history(hass, load_module, monkeypatch):
    module = load_module("history_tools")
    entity = "fan.purifier"
    hass.state_values[entity] = SimpleNamespace(
        entity_id=entity, name="공기청정기", state="unavailable"
    )
    hass.exposed.add(entity)
    records = []

    def query(hass, start, end, entities, **kwargs):
        assert entities == [entity]
        assert kwargs == {
            "include_start_time_state": True,
            "significant_changes_only": False,
            "minimal_response": False,
            "no_attributes": False,
        }
        prior = [r for r in records if r.last_updated <= start]
        return {entity: prior[-1:] + [r for r in records if start < r.last_updated < end]}

    query_mock = Mock(side_effect=query)
    monkeypatch.setattr(module, "get_significant_states", query_mock)

    async def executor(fn, *args):
        return fn(*args)

    hass.data["test_recorder"] = SimpleNamespace(async_add_executor_job=executor)
    return SimpleNamespace(module=module, records=records, query=query_mock)


async def call(history, hass, ha_contract, **kwargs):
    tool = history.module.HistoryTool(["fan.purifier"])
    args = {
        "entity_id": "fan.purifier",
        "start_time": START.isoformat(),
        "end_time": END.isoformat(),
        **kwargs,
    }
    return await tool.async_call(
        hass,
        ha_contract.ToolInput(tool.name, args),
        ha_contract.LLMContext(None, context=SimpleNamespace(user_id="reader")),
    )


async def test_state_and_attribute_changes_with_baseline_and_local_time(history, hass, ha_contract):
    history.records.extend(
        [
            row(START - timedelta(days=1), "off", preset_mode="auto", api_key="excluded"),
            row(START, "on", preset_mode="auto"),
            row(START + timedelta(hours=1), "on", preset_mode="sleep"),
            row(START + timedelta(hours=2), "on", preset_mode="sleep", friendly_name="ignored"),
            row(START + timedelta(hours=6), "off", preset_mode="sleep"),
            row(END, "on", preset_mode="auto"),
        ]
    )
    result = await call(history, hass, ha_contract)
    assert not result.error
    data = result.data
    assert data["initial_state"]["state"] == "off"
    assert len(data["changes"]) == 3
    assert data["changes"][0]["at"] == "2026-10-01T09:00:00+09:00"
    assert data["changes"][1]["attributes"]["preset_mode"] == "sleep"
    assert "api_key" not in str(data) and "friendly_name" not in str(data)
    assert data["coverage"] == "not_guaranteed" and not data["truncated"]
    assert history.query.call_count == 2
    hass.services.async_call.assert_not_awaited()


@pytest.mark.parametrize("count,truncated", [(100, False), (101, True)])
async def test_result_limit_and_early_stop(history, hass, ha_contract, count, truncated):
    history.records.extend(row(START + timedelta(seconds=i + 1), str(i)) for i in range(count))
    result = await call(history, hass, ha_contract)
    assert len(result.data["changes"]) == 100
    assert result.data["truncated"] is truncated
    if truncated:
        assert history.query.call_count == 1


async def test_empty_history_does_not_assert_no_changes(history, hass, ha_contract):
    result = await call(history, hass, ha_contract)
    assert not result.error and result.data["history_found"] is False
    assert result.data["initial_state"] is None and result.data["changes"] == []
    assert result.data["coverage"] == "not_guaranteed"


async def test_initial_snapshot_is_not_an_event(history, hass, ha_contract):
    history.records.append(row(START - timedelta(days=1), "on"))
    result = await call(history, hass, ha_contract)
    assert result.data["history_found"] and result.data["changes"] == []
    assert result.data["initial_state"]["at"] == "2026-10-01T09:00:00+09:00"


@pytest.mark.parametrize(
    "args",
    [
        {"start_time": "2026-10-01T00:00:00"},
        {"start_time": "bad"},
        {"start_time": END.isoformat()},
        {"end_time": (START - timedelta(days=1)).isoformat()},
        {"start_time": (START - timedelta(days=8)).isoformat()},
        {"end_time": (datetime.now(UTC) + timedelta(days=1)).isoformat()},
        {"limit": 101},
        {"limit": 0},
        {"entity_id": "fan.other"},
    ],
)
async def test_invalid_requests_do_not_query(history, hass, ha_contract, args):
    result = await call(history, hass, ha_contract, **args)
    assert result.error
    history.query.assert_not_called()


@pytest.mark.parametrize("change", ["hidden", "denied", "inactive", "missing"])
async def test_read_authorization_required(history, hass, ha_contract, change):
    if change == "hidden":
        hass.exposed.clear()
    elif change == "denied":
        hass.auth.async_get_user.return_value.permissions.check_entity = lambda *args: False
    elif change == "inactive":
        hass.auth.async_get_user.return_value.is_active = False
    elif change == "missing":
        hass.state_values.clear()
    result = await call(history, hass, ha_contract)
    assert result.error
    history.query.assert_not_called()


async def test_read_permission_not_control_and_rechecked_after_query(history, hass, ha_contract):
    policies = []
    hass.auth.async_get_user.return_value.permissions.check_entity = lambda entity, policy: (
        policies.append(policy) or policy == "read"
    )
    original = hass.data["test_recorder"].async_add_executor_job

    async def executor(fn, *args):
        result = await original(fn, *args)
        hass.exposed.clear()
        return result

    hass.data["test_recorder"].async_add_executor_job = executor
    result = await call(history, hass, ha_contract)
    assert result.error and "changes" not in result.data
    assert policies == ["read"]


async def test_recorder_missing_and_query_failure_are_errors(history, hass, ha_contract):
    recorder = hass.data.pop("test_recorder")
    result = await call(history, hass, ha_contract)
    assert result.error and result.data["error"] == "recorder_not_available"
    hass.data["test_recorder"] = recorder
    history.query.side_effect = TimeoutError
    result = await call(history, hass, ha_contract)
    assert result.error and result.data["error"] == "history_query_failed"


def test_default_period_and_seven_day_boundary(history):
    now = datetime.now(UTC)
    assert history.module.period({}, now) == (now - timedelta(days=1), now)
    assert history.module.period({"start_time": (now - timedelta(days=7)).isoformat()}, now)[
        0
    ] == now - timedelta(days=7)


async def test_provider_read_only_targets_and_time_zone(history, hass, ha_contract):
    api = history.module.AssistHistoryAPI(hass, "assist_history", "Assist History")
    result = await api.async_get_api_instance(ha_contract.LLMContext(None))
    assert len(result.tools) == 1 and result.tools[0].annotations.read_only
    assert "Asia/Seoul" in result.api_prompt and "공기청정기" in result.api_prompt
    hass.exposed.clear()
    assert not (await api.async_get_api_instance(ha_contract.LLMContext(None))).tools


async def test_recorder_synthetic_start_snapshots_are_not_changes(history, hass, ha_contract):
    """Model HA LazyState synthetic timestamps; related files: read_history."""

    def query(hass, start, end, entities, **kwargs):
        return {entities[0]: [row(start, "on", preset_mode="sleep")]}

    history.query.side_effect = query
    result = await call(history, hass, ha_contract)
    assert not result.error
    assert result.data["initial_state"]["state"] == "on"
    assert result.data["changes"] == []


async def test_read_only_user_can_query_and_zone_comes_from_ha(history, hass, ha_contract):
    hass.config.time_zone = "America/New_York"
    hass.auth.async_get_user.return_value.permissions.check_entity = lambda entity, policy: (
        policy == "read"
    )
    history.records.append(row(START, "on"))
    result = await call(history, hass, ha_contract)
    assert not result.error
    assert result.data["time_zone"] == "America/New_York"
    assert result.data["changes"][0]["at"] == "2026-09-30T20:00:00-04:00"


async def test_default_tool_call_uses_recent_day(history, hass, ha_contract):
    tool = history.module.HistoryTool(["fan.purifier"])
    result = await tool.async_call(
        hass,
        ha_contract.ToolInput(tool.name, {"entity_id": "fan.purifier"}),
        ha_contract.LLMContext(None),
    )
    assert not result.error
    start = datetime.fromisoformat(result.data["start_time"])
    end = datetime.fromisoformat(result.data["end_time"])
    assert end - start == timedelta(days=1)


async def test_registration_failure_rolls_back_all(
    history, hass, load_module, ha_contract, monkeypatch
):
    module = load_module("llm")
    original = module.llm.async_register_api

    def register(hass, api):
        if api.id == "assist_history":
            raise ValueError("registration failed")
        return original(hass, api)

    monkeypatch.setattr(module.llm, "async_register_api", register)
    with pytest.raises(ValueError):
        module.register_api(hass)
    assert not ha_contract.apis
