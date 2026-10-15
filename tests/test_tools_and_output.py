"""Test routing, output separation, and API contracts; related files: LLM/runtime.

HA boundaries are contract doubles; real hardware playback requires on-device QA.
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from conftest import ROOT
from mutagen.mp3 import MP3


@pytest.fixture
async def endpoints(load_module, hass):
    core = load_module("timer_manager")
    runtime_module = load_module("runtime")
    runtime = runtime_module.Runtime()
    for device in ("kitchen", "bedroom"):
        output = SimpleNamespace(
            satellite=f"assist_satellite.{device}",
            async_play=AsyncMock(),
            async_stop=AsyncMock(),
            async_announce=AsyncMock(),
        )
        manager = core.TimerManager(output, AsyncMock())
        endpoint = runtime_module.Endpoint(device, manager, output)
        endpoint.queue_announcement = lambda *args: None
        runtime.endpoints[device] = endpoint
    hass.data["assist_timers"] = runtime
    yield runtime
    for endpoint in runtime.endpoints.values():
        await endpoint.async_close()


async def test_satellites_are_independent(endpoints, load_module):
    kitchen = endpoints.for_device("kitchen")
    bedroom = endpoints.for_device("bedroom")
    first = await kitchen.async_operation("create", {"duration_seconds": 30})
    second = await bedroom.async_operation("create", {"duration_seconds": 40})
    assert first["timer_id"] != second["timer_id"]
    await kitchen.manager.async_expire(first["timer_id"])
    await kitchen.async_operation("cancel", {})
    kitchen.output.async_stop.assert_awaited_once()
    bedroom.output.async_stop.assert_not_awaited()
    assert bedroom.manager.snapshot()["state"] == "active"
    with pytest.raises(load_module("timer_manager").TimerError, match="not_configured"):
        endpoints.for_device(None)


async def test_duplicate_call_id_does_not_repeat_mutation(endpoints):
    endpoint = endpoints.for_device("kitchen")
    first = await endpoint.async_operation("create", {"duration_seconds": 30}, "same-call")
    again = await endpoint.async_operation("create", {"duration_seconds": 30}, "same-call")
    assert again["timer_id"] == first["timer_id"]
    assert again["replayed"] is True


async def test_conflicting_call_id_is_rejected(endpoints, load_module):
    endpoint = endpoints.for_device("kitchen")
    await endpoint.async_operation("create", {"duration_seconds": 30}, "same-call")
    with pytest.raises(load_module("timer_manager").TimerError, match="call_id_conflict"):
        await endpoint.async_operation("cancel", {}, "same-call")
    assert endpoint.manager.snapshot()["state"] == "active"


async def test_concurrent_duplicate_calls_create_once(endpoints):
    endpoint = endpoints.for_device("kitchen")
    results = await asyncio.gather(
        *(
            endpoint.async_operation("create", {"duration_seconds": 30}, "same-call")
            for _ in range(3)
        )
    )
    assert len({result["timer_id"] for result in results}) == 1
    assert sum(result.get("replayed", False) for result in results) == 2


async def test_api_exposes_text_targets_but_keeps_voice_origin_fixed(
    endpoints, load_module, hass, ha_contract
):
    api = load_module("llm").AssistTimersAPI(hass=hass, id="assist_timers", name="Assist Timers")
    instance = await api.async_get_api_instance(ha_contract.LLMContext("kitchen"))
    assert len(instance.tools) == 5
    assert all(tool.integration == "assist_timers" for tool in instance.tools)
    assert "satellite" not in instance.tools[0].parameters.schema
    unknown = await api.async_get_api_instance(ha_contract.LLMContext("phone"))
    assert len(unknown.tools) == 5
    assert any(str(key) == "satellite" for key in unknown.tools[0].parameters.schema)


async def test_text_single_satellite_auto_selects(endpoints, load_module, hass, ha_contract):
    bedroom = endpoints.endpoints.pop("bedroom")
    await bedroom.async_close()
    tool = load_module("llm").TimerTool("create")
    result = await tool.async_call(
        hass,
        ha_contract.ToolInput(tool.name, {"duration_seconds": 30}),
        ha_contract.LLMContext(None),
    )
    assert not result.error
    assert result.data["satellite"] == "assist_satellite.kitchen"
    assert result.data["response_mode"] == "normal"


async def test_providers_expose_only_their_own_tools(endpoints, load_module, hass, ha_contract):
    """Check simultaneous providers; related files: llm and action_tools."""
    endpoints.actions = SimpleNamespace(closed=False)
    endpoints.control = SimpleNamespace(
        targets=AsyncMock(return_value=[{"entity_id": "light.room", "name": "Room"}])
    )
    module = load_module("llm")
    context = ha_contract.LLMContext("kitchen")
    timer = await module.AssistTimersAPI(
        hass=hass, id="assist_timers", name="Assist Timer"
    ).async_get_api_instance(context)
    devices = await module.AssistDevicesAPI(
        hass=hass, id="assist_devices", name="Assist Scheduled Control"
    ).async_get_api_instance(context)
    assert len(timer.tools) == 5
    assert all(tool.name.startswith("AssistTimers") for tool in timer.tools)
    assert {tool.name for tool in devices.tools} == {
        "AssistScheduledControlCreate",
        "AssistScheduledControlList",
        "AssistScheduledControlCancel",
    }
    assert "Available device names" not in timer.api_prompt
    assert "Configured satellite names" not in devices.api_prompt


async def test_text_multiple_satellites_require_selection(
    endpoints, load_module, hass, ha_contract
):
    tool = load_module("llm").TimerTool("create")
    result = await tool.async_call(
        hass,
        ha_contract.ToolInput(tool.name, {"duration_seconds": 30}),
        ha_contract.LLMContext(None),
    )
    assert result.error and result.data["error"] == "target_satellite_required"
    assert all(ep.manager.timer is None for ep in endpoints.endpoints.values())


async def test_text_explicit_target_and_controls(endpoints, load_module, hass, ha_contract):
    tools = load_module("llm")
    bedroom = endpoints.for_device("bedroom")
    bedroom.queue_announcement = AsyncMock()
    for op, extra in (
        ("create", {"duration_seconds": 30}),
        ("pause", {}),
        ("resume", {}),
        ("cancel", {}),
    ):
        tool = tools.TimerTool(op)
        result = await tool.async_call(
            hass,
            ha_contract.ToolInput(
                tool.name, {"satellite": "assist_satellite.bedroom", **extra}, op
            ),
            ha_contract.LLMContext("browser"),
        )
        assert not result.error and result.data["response_mode"] == "normal"
    bedroom.queue_announcement.assert_not_called()
    assert endpoints.for_device("kitchen").manager.timer is None


def test_voice_cannot_redirect_to_other_satellite(endpoints, load_module):
    with pytest.raises(load_module("timer_manager").TimerError, match="cannot_redirect"):
        endpoints.for_request("kitchen", "assist_satellite.bedroom")


async def test_confirmations_and_ringing_cancel_response(endpoints, load_module, hass, ha_contract):
    tools = load_module("llm")
    endpoint = endpoints.for_device("kitchen")
    messages = []
    endpoint.queue_announcement = lambda message, state, timer_id: messages.append(message)
    context = ha_contract.LLMContext("kitchen")

    async def invoke(operation, args=None, call_id=None):
        tool = tools.TimerTool(operation)
        return await tool.async_call(
            hass, ha_contract.ToolInput(tool.name, args or {}, call_id or operation), context
        )

    created = await invoke("create", {"duration_seconds": 300})
    assert not created.error
    assert messages == []
    assert created.data["response_mode"] == "normal"
    assert created.data["announcement_queued"] is False
    await invoke("pause")
    assert messages[-1] == "설정한 5분 알람을 일시정지하였습니다."
    await invoke("cancel")
    assert messages[-1] == "설정한 5분 알람을 중지하였습니다."
    second = await invoke("create", {"duration_seconds": 60}, "create-2")
    await endpoint.manager.async_expire(second.data["timer_id"])
    count = len(messages)
    stopped = await invoke("cancel", call_id="cancel-2")
    endpoint.output.async_stop.assert_awaited_once()
    assert len(messages) == count
    assert stopped.data["response_mode"] == "normal"
    assert "silent" not in stopped.data


async def test_tool_failure_reports_error_without_announcement(
    endpoints, load_module, hass, ha_contract
):
    endpoint = endpoints.for_device("kitchen")
    endpoint.queue_announcement = AsyncMock()
    tool = load_module("llm").TimerTool("pause")
    result = await tool.async_call(
        hass, ha_contract.ToolInput(tool.name, {}), ha_contract.LLMContext("kitchen")
    )
    assert result.error and result.data["error"] == "timer_not_found"
    endpoint.queue_announcement.assert_not_called()


async def test_service_create_does_not_announce(endpoints, load_module, hass):
    handlers = {}
    hass.services.async_register = lambda domain, name, handler, **kwargs: handlers.update(
        {name: handler}
    )
    endpoint = endpoints.for_device("kitchen")
    endpoint.output.satellite = "assist_satellite.kitchen"
    endpoint.queue_announcement = AsyncMock()
    load_module("__init__").register_services(hass, endpoints)
    result = await handlers["create"](
        SimpleNamespace(
            service="create",
            data={"satellite": "assist_satellite.kitchen", "duration_seconds": 300},
            return_response=True,
        )
    )
    assert result["state"] == "active"
    endpoint.queue_announcement.assert_not_called()


async def test_normal_media_alarm_and_real_stop(load_module, hass):
    satellite = load_module("satellite")
    output = satellite.SatelliteOutput(hass, "assist_satellite.kitchen", "media_player.kitchen")
    await output.async_play()
    play_call = hass.services.async_call.await_args
    assert play_call.args[:2] == ("media_player", "play_media")
    data = play_call.args[2]
    assert data["entity_id"] == "media_player.kitchen"
    assert data["media_content_id"] == "http://ha.test:8123/assist_timers/sounds/alarm_60s.mp3"
    assert "announce" not in data
    await output.async_stop()
    assert hass.services.async_call.await_args.args == (
        "media_player",
        "media_stop",
        {"entity_id": "media_player.kitchen"},
    )


async def test_response_uses_announce_without_chime(load_module, hass):
    output = load_module("satellite").SatelliteOutput(
        hass, "assist_satellite.kitchen", "media_player.kitchen"
    )
    await output.async_announce("설정한 5분 알람이 설정되었습니다.", lambda: True)
    call = hass.services.async_call.await_args
    assert call.args[:2] == ("assist_satellite", "announce")
    assert call.args[2]["preannounce"] is False


async def test_obsolete_announcement_is_not_played(load_module, hass):
    output = load_module("satellite").SatelliteOutput(
        hass, "assist_satellite.kitchen", "media_player.kitchen"
    )
    await output.async_announce("old", lambda: False)
    hass.services.async_call.assert_not_awaited()


async def test_announcement_waits_for_busy_pipeline(load_module, hass):
    output = load_module("satellite").SatelliteOutput(
        hass, "assist_satellite.kitchen", "media_player.kitchen"
    )
    hass.state_values["assist_satellite.kitchen"].state = "processing"
    task = asyncio.create_task(output.async_announce("ready", lambda: True))
    await asyncio.sleep(0)
    hass.services.async_call.assert_not_awaited()
    hass.state_values["assist_satellite.kitchen"].state = "idle"
    await task
    hass.services.async_call.assert_awaited_once()


def test_reject_player_without_stop(load_module, hass):
    hass.state_values["media_player.kitchen"].attributes["supported_features"] = 512
    with pytest.raises(load_module("timer_manager").TimerError, match="play_stop"):
        load_module("satellite").validate_entities(
            hass, "assist_satellite.kitchen", "media_player.kitchen"
        )


async def test_stale_queued_confirmation_does_not_describe_new_timer(load_module):
    core, rt = load_module("timer_manager"), load_module("runtime")
    output = SimpleNamespace(async_play=AsyncMock(), async_stop=AsyncMock())
    captured = []

    async def announce(message, valid):
        captured.append((message, valid()))

    output.async_announce = announce
    manager = core.TimerManager(output, AsyncMock())
    endpoint = rt.Endpoint("kitchen", manager, output)
    first = await manager.async_create(30)
    await manager.async_cancel()
    await manager.async_create(40)
    endpoint.queue_announcement("old", "active", first["timer_id"])
    await asyncio.sleep(0)
    assert captured == [("old", False)]
    await endpoint.async_close()


def test_bounded_alarm_duration():
    path = ROOT / "custom_components/assist_timers/alarm_sounds/alarm_60s.mp3"
    assert 60 <= MP3(path).info.length < 60.1


def test_platform_discovery_does_not_duplicate_standalone_tools(load_module, hass, ha_contract):
    assert (
        load_module("llm").async_get_tools(hass, ha_contract.LLMContext("kitchen"), "assist")
        is None
    )


def test_metadata_and_translations_match():
    base = ROOT / "custom_components/assist_timers"
    manifest = json.loads((base / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.1.0" and manifest["config_flow"] is True
    english = json.loads((base / "strings.json").read_text(encoding="utf-8"))
    korean = json.loads((base / "translations/ko.json").read_text(encoding="utf-8"))
    assert english["config"]["error"].keys() == korean["config"]["error"].keys()
    assert set(korean["config"]["step"]["user"]["data"]) == {"satellite"}
    assert set(korean["config"]["step"]["player"]["data"]) == {"media_player"}


async def test_setup_unload_and_storage_lifecycle(load_module, hass, ha_contract):
    setup = load_module("__init__")
    entry = SimpleNamespace(
        entry_id="test-entry",
        data={"satellite": "assist_satellite.kitchen", "media_player": "media_player.kitchen"},
    )
    assert await setup.async_setup(hass, {})
    assert "assist_timers" in ha_contract.apis
    assert ha_contract.apis["assist_timers"].name == "Assist Timer"
    assert ha_contract.apis["assist_devices"].name == "Assist Scheduled Control"
    assert ha_contract.apis["assist_climate"].name == "Assist Climate"
    assert ha_contract.apis["assist_fan"].name == "Assist Fan"
    assert ha_contract.apis["assist_history"].name == "Assist History"
    assert await setup.async_setup_entry(hass, entry)
    endpoint = hass.data["assist_timers"].endpoints[entry.entry_id]
    first = await endpoint.manager.async_create(300)
    assert await setup.async_unload_entry(hass, entry)
    assert hass.storage["assist_timers.test-entry"]["timer_id"] == first["timer_id"]
    assert await setup.async_setup_entry(hass, entry)
    assert (
        hass.data["assist_timers"].endpoints[entry.entry_id].manager.snapshot()["state"] == "active"
    )
    assert await setup.async_unload_entry(hass, entry)
    await setup.async_remove_entry(hass, entry)
    assert "assist_timers.test-entry" not in hass.storage
    hass.data["assist_timers"].unregister_api()
    assert not ha_contract.apis


def test_config_validation_prevents_shared_players(load_module, hass):
    config = load_module("config_flow")
    entries = [
        SimpleNamespace(
            entry_id="other",
            data={"satellite": "assist_satellite.other", "media_player": "media_player.kitchen"},
        )
    ]
    hass.registry_values["assist_satellite.other"] = SimpleNamespace(device_id="other")
    hass.config_entries.async_entries = lambda domain: entries
    with pytest.raises(load_module("timer_manager").TimerError, match="player_already"):
        config.validate(
            hass, {"satellite": "assist_satellite.kitchen", "media_player": "media_player.kitchen"}
        )


def test_matching_player_uses_unique_display_name(load_module, hass):
    assert (
        load_module("config_flow").matching_player(hass, "assist_satellite.kitchen")
        == "media_player.kitchen"
    )


@pytest.mark.parametrize("automatic", [True, False])
async def test_setup_flow_auto_connects_or_offers_manual_player(load_module, hass, automatic):
    flow = load_module("config_flow").AssistTimersConfigFlow()
    flow.hass = hass
    flow.async_set_unique_id = AsyncMock()
    flow._abort_if_unique_id_configured = lambda: None
    flow.async_create_entry = lambda **kwargs: kwargs
    flow.async_show_form = lambda **kwargs: kwargs
    if not automatic:
        hass.state_values["media_player.kitchen"].name = "Different display name"
    result = await flow.async_step_user({"satellite": "assist_satellite.kitchen"})
    if not automatic:
        assert result["step_id"] == "player"
        result = await flow.async_step_player({"media_player": "media_player.kitchen"})
    assert result["data"] == {
        "satellite": "assist_satellite.kitchen",
        "media_player": "media_player.kitchen",
    }
    flow.async_set_unique_id.assert_awaited_with("kitchen")


@pytest.mark.parametrize(
    "reason", ["different_name", "ambiguous", "unsupported", "unavailable", "claimed"]
)
def test_matching_player_requires_manual_selection(load_module, hass, reason):
    player = hass.state_values["media_player.kitchen"]
    if reason == "different_name":
        player.name = "Bedroom"
    elif reason == "ambiguous":
        hass.state_values["media_player.second"] = SimpleNamespace(
            name="Kitchen",
            entity_id="media_player.second",
            state="idle",
            attributes={"supported_features": 4608},
        )
    elif reason == "unsupported":
        player.attributes["supported_features"] = 0
    elif reason == "unavailable":
        player.state = "unavailable"
    else:
        hass.config_entries.async_entries = lambda domain: [
            SimpleNamespace(data={"media_player": "media_player.kitchen"})
        ]
    assert load_module("config_flow").matching_player(hass, "assist_satellite.kitchen") is None
