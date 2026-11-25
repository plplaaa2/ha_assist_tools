"""Test routing, output separation, and API contracts; related files: LLM/runtime.

HA boundaries are contract doubles; real hardware playback requires on-device QA.
"""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from conftest import ROOT
from mutagen.mp3 import MP3


# Verify persisted routing through integration setup; related: llm.py, runtime.py.
async def test_clock_alarm_is_saved_for_the_requesting_speaker(load_module, hass, ha_contract):
    setup = load_module("__init__")
    await setup.async_setup(hass, {})
    for domain in ("assist_satellite", "media_player"):
        kitchen = hass.state_values[f"{domain}.kitchen"]
        hass.state_values[f"{domain}.bedroom"] = SimpleNamespace(
            **{**vars(kitchen), "entity_id": f"{domain}.bedroom", "name": "Bedroom"}
        )
    hass.registry_values["assist_satellite.bedroom"] = SimpleNamespace(device_id="bedroom")
    entries = [
        SimpleNamespace(
            entry_id=room,
            data={"satellite": f"assist_satellite.{room}", "media_player": f"media_player.{room}"},
        )
        for room in ("kitchen", "bedroom")
    ]
    for entry in entries:
        await setup.async_setup_entry(hass, entry)
    try:
        api = load_module("llm").AssistTimersAPI(hass=hass, id="assist_timers", name="Assist Timer")
        context = ha_contract.LLMContext("bedroom")
        instance = await api.async_get_api_instance(context)
        create = next(tool for tool in instance.tools if tool.name == "AssistAlarmsCreate")
        args = {"execute_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat()}
        redirect = await create.async_call(
            hass,
            ha_contract.ToolInput(create.name, {**args, "satellite": "assist_satellite.kitchen"}),
            context,
        )
        assert redirect.data["error"] == "cannot_redirect_voice_satellite"
        unknown = await create.async_call(
            hass, ha_contract.ToolInput(create.name, args), ha_contract.LLMContext("other")
        )
        assert unknown.data["error"] == "requesting_satellite_not_configured"
        text = await create.async_call(
            hass, ha_contract.ToolInput(create.name, args), ha_contract.LLMContext(None)
        )
        assert text.data["error"] == "target_satellite_required"
        result = await create.async_call(
            hass, ha_contract.ToolInput(create.name, create.parameters(args)), context
        )
        assert not result.error
        assert result.data["speaker"] == "media_player.bedroom"
        job_id = result.data["job_id"]
        assert "assist_tools.kitchen.alarms" not in hass.storage
        assert hass.storage["assist_tools.bedroom.alarms"][0]["entity_id"] == "media_player.bedroom"
        weekly = next(tool for tool in instance.tools if tool.name == "AssistAlarmsCreateWeekly")
        repeated = await weekly.async_call(
            hass,
            ha_contract.ToolInput(
                weekly.name,
                weekly.parameters({"weekdays": ["mon", "fri"], "at_time": "07:00"}),
                id="weekly-clock-alarm",
            ),
            context,
        )
        assert not repeated.error
        assert repeated.data["weekdays"] == ["mon", "fri"]
        assert repeated.data["speaker"] == "media_player.bedroom"
        await setup.async_unload_entry(hass, entries[1])
        await setup.async_setup_entry(hass, entries[1])
        endpoint = hass.data["assist_tools"].for_device("bedroom")
        assert endpoint.alarms.jobs[job_id].state == "pending"
        deadline = endpoint.alarms._deadlines[job_id]
        endpoint.alarms.clock = lambda: deadline
        ringing = asyncio.Event()

        async def signal(_domain, service, *_args, **_kwargs):
            if service == "play_media":
                ringing.set()

        hass.services.async_call.side_effect = signal
        task = asyncio.create_task(endpoint.alarms.async_execute(job_id))
        await asyncio.wait_for(ringing.wait(), 1)
        stop = next(tool for tool in instance.tools if tool.name == "AssistAlarmsStop")
        stopped = await stop.async_call(hass, ha_contract.ToolInput(stop.name, {}), context)
        await asyncio.wait_for(task, 1)
        assert stopped.data["stopped"]
        assert [call.args[2]["entity_id"] for call in hass.services.async_call.await_args_list] == [
            "media_player.bedroom",
            "media_player.bedroom",
        ]
    finally:
        for entry in entries:
            await setup.async_unload_entry(hass, entry)
        hass.data["assist_tools"].unregister_api()


async def test_clock_alarm_and_timer_sounds_share_output_without_cross_stopping(
    load_module, hass, monkeypatch
):
    satellite = load_module("satellite")
    defaults = {
        "timer_sound": {"media_content_id": "media-source://media_source/local/timer.mp3"},
        "alarm_sound": {"media_content_id": "media-source://media_source/local/alarm.mp3"},
    }

    async def resolve(_hass, media_id, _player):
        return SimpleNamespace(url=f"/media/{media_id.rsplit('/', 1)[1]}", mime_type="audio/mpeg")

    monkeypatch.setattr(satellite, "async_resolve_media", resolve)
    output = satellite.SatelliteOutput(
        hass, "assist_satellite.kitchen", "media_player.kitchen", sounds=lambda: defaults
    )
    await output.async_play()
    ringing = asyncio.Event()

    async def signal(_domain, service, *_args, **_kwargs):
        if service == "play_media":
            ringing.set()

    hass.services.async_call.side_effect = signal
    task = asyncio.create_task(output.async_ring_alarm("alarm", output.player))
    await asyncio.wait_for(ringing.wait(), 1)
    await output.async_stop()
    assert not await output.async_stop_alarm("another_alarm")
    assert await output.async_stop_alarm("alarm")
    await asyncio.wait_for(task, 1)
    await output.async_stop()
    calls = [call.args for call in hass.services.async_call.await_args_list]
    assert [call[1] for call in calls] == ["play_media", "play_media", "media_stop"]
    assert [call[2]["media_content_id"].rsplit("/", 1)[1] for call in calls[:2]] == [
        "timer.mp3",
        "alarm.mp3",
    ]


async def test_clock_alarm_rejects_group_output(load_module, hass):
    satellite = load_module("satellite")
    output = satellite.SatelliteOutput(hass, "assist_satellite.kitchen", "media_player.kitchen")
    hass.state_values[output.player].attributes["group_members"] = [
        "media_player.kitchen",
        "media_player.bedroom",
    ]
    with pytest.raises(load_module("timer_manager").TimerError, match="individual_speaker"):
        await output.async_ring_alarm("alarm", output.player)
    hass.services.async_call.assert_not_awaited()


async def test_clock_alarm_auto_stops_with_bundled_sound(load_module, hass, monkeypatch):
    satellite = load_module("satellite")
    monkeypatch.setattr(satellite, "ALARM_SECONDS", 0)
    output = satellite.SatelliteOutput(hass, "assist_satellite.kitchen", "media_player.kitchen")
    await output.async_ring_alarm("alarm", output.player)
    calls = [call.args for call in hass.services.async_call.await_args_list]
    assert [call[1] for call in calls] == ["play_media", "media_stop"]
    assert calls[0][2]["media_content_id"].endswith("/alarm_60s.mp3")
    assert not await output.async_stop_alarm()


async def test_sound_options_save_independent_local_audio(load_module, hass):
    config = load_module("config_flow")
    flow = config.AlarmSoundOptions()
    flow.hass = hass
    flow.config_entry = SimpleNamespace(
        data={"satellite": "assist_satellite.kitchen", "media_player": "media_player.kitchen"},
        options={"timer_sound": {"media_content_id": "old"}},
    )
    flow.async_create_entry = lambda **kwargs: kwargs
    flow.async_show_form = lambda **kwargs: kwargs
    flow.add_suggested_values_to_schema = lambda schema, _suggested: schema
    clock_sound = {"media_content_id": "media-source://media_source/local/bedroom.mp3"}
    result = await flow.async_step_init({"alarm_sound": clock_sound})
    assert result["data"] == {"alarm_sound": {**clock_sound, "media_content_type": "audio/mpeg"}}
    rejected = await flow.async_step_init(
        {"timer_sound": {"media_content_id": "https://example.com/a.mp3"}}
    )
    assert rejected["errors"] == {"timer_sound": "invalid_alarm_sound"}


@pytest.mark.parametrize(
    ("data", "reason", "placeholders"),
    [
        ({"weather_entity": "weather.home"}, "weather_settings", {"entity_id": "weather.home"}),
        (
            {"emergency_notify_action": "notify.mobile_app_phone"},
            "emergency_settings",
            {"notify_action": "notify.mobile_app_phone"},
        ),
    ],
)
async def test_non_satellite_options_show_configured_destination(
    load_module, data, reason, placeholders
):
    config = load_module("config_flow")
    entry = SimpleNamespace(data=data)
    flow = config.AssistTimersConfigFlow.async_get_options_flow(entry)
    flow.config_entry = entry
    flow.async_abort = lambda **kwargs: kwargs

    assert await flow.async_step_init() == {
        "reason": reason,
        "description_placeholders": placeholders,
    }


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
    hass.data["assist_tools"] = runtime
    yield runtime
    for endpoint in runtime.endpoints.values():
        await endpoint.async_close()


@pytest.mark.parametrize(
    ("args", "duration", "error"),
    [
        ({"seconds": 90}, 90, None),
        ({"minutes": 2, "seconds": 5}, 125, None),
        ({"hours": 1, "minutes": 20, "seconds": 30}, 4830, None),
        ({"hours": 168}, 604800, None),
        ({"hours": 168, "seconds": 1}, None, "invalid_duration"),
        ({}, None, "invalid_duration"),
        ({"seconds": 0}, None, "invalid_duration"),
        ({"duration_seconds": 30, "minutes": 1}, None, "conflicting_duration_fields"),
    ],
)
async def test_timer_duration_fields(
    endpoints, load_module, hass, ha_contract, args, duration, error
):
    tool = load_module("llm").TimerTool("create")
    validated = tool.parameters(args)
    result = await tool.async_call(
        hass, ha_contract.ToolInput(tool.name, validated), ha_contract.LLMContext("kitchen")
    )
    if error:
        assert result.error and result.data["error"] == error
        assert endpoints.for_device("kitchen").manager.snapshot()["state"] == "idle"
    else:
        assert result.data["duration_seconds"] == duration


@pytest.mark.parametrize("restore_fails", [False, True])
async def test_setup_publishes_only_restored_unique_endpoint(
    load_module, hass, monkeypatch, restore_fails
):
    setup = load_module("__init__")
    monkeypatch.setattr(setup, "register_api", lambda _hass: lambda: None)
    await setup.async_setup(hass, {})
    runtime = hass.data["assist_tools"]
    restoring = asyncio.Event()
    continue_restore = asyncio.Event()
    attempts = 0

    async def restore(_self, _saved):
        nonlocal attempts
        attempts += 1
        restoring.set()
        await continue_restore.wait()
        if restore_fails and attempts == 1:
            raise OSError("storage unavailable")

    monkeypatch.setattr(setup.ScheduledActions, "async_restore", restore)
    data = {"satellite": "assist_satellite.kitchen", "media_player": "media_player.kitchen"}
    first = asyncio.create_task(
        setup.async_setup_entry(hass, SimpleNamespace(entry_id="a", data=data))
    )
    await restoring.wait()
    second_entry = SimpleNamespace(entry_id="b", data=data)
    second = asyncio.create_task(setup.async_setup_entry(hass, second_entry))
    await asyncio.sleep(0)
    assert not runtime.endpoints and not second.done()
    continue_restore.set()
    results = await asyncio.gather(first, second, return_exceptions=True)
    rejected, accepted = results if restore_fails else results[::-1]
    assert isinstance(rejected, setup.ConfigEntryNotReady) and accepted is True
    entry_id = "b" if restore_fails else "a"
    assert list(runtime.endpoints) == [entry_id]
    await setup.async_unload_entry(hass, SimpleNamespace(entry_id=entry_id, data=data))


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
    assert all(tool.integration == "assist_tools" for tool in instance.tools)
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
        "AssistScheduledControlCreateWeekly",
        "AssistScheduledControlList",
        "AssistScheduledControlCancel",
        "AssistScheduledControlReschedule",
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
    assert data["media_content_id"] == "http://ha.test:8123/assist_tools/sounds/alarm_60s.mp3"
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
    path = ROOT / "custom_components/assist_tools/alarm_sounds/alarm_60s.mp3"
    assert 60 <= MP3(path).info.length < 60.1


def test_platform_discovery_does_not_duplicate_standalone_tools(load_module, hass, ha_contract):
    assert (
        load_module("llm").async_get_tools(hass, ha_contract.LLMContext("kitchen"), "assist")
        is None
    )


def test_metadata_and_translations_match():
    base = ROOT / "custom_components/assist_tools"
    manifest = json.loads((base / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["domain"] == base.name == "assist_tools"
    assert manifest["version"] == "0.3.4" and manifest["config_flow"] is True
    english = json.loads((base / "strings.json").read_text(encoding="utf-8"))
    korean = json.loads((base / "translations/ko.json").read_text(encoding="utf-8"))
    assert english["config"]["error"].keys() == korean["config"]["error"].keys()
    assert set(korean["config"]["step"]["user"]["data"]) == {"language", "setup_type"}
    assert set(korean["config"]["step"]["satellite"]["data"]) == {"satellite"}
    assert set(korean["config"]["step"]["weather"]["data"]) == {"weather_entity"}
    assert set(korean["config"]["step"]["player"]["data"]) == {"media_player"}


@pytest.mark.parametrize("google_speaker", [False, True])
async def test_setup_unload_and_storage_lifecycle(load_module, hass, ha_contract, google_speaker):
    setup = load_module("__init__")
    entry = SimpleNamespace(
        entry_id="test-entry",
        data={"satellite": "assist_satellite.kitchen", "media_player": "media_player.kitchen"},
    )
    if google_speaker:
        entry.data["google_speaker"] = True
        hass.state_values["assist_satellite.kitchen"].attributes["supported_features"] = 0
    assert await setup.async_setup(hass, {})
    assert "assist_timers" in ha_contract.apis
    assert ha_contract.apis["assist_timers"].name == "Assist Timer"
    assert ha_contract.apis["assist_devices"].name == "Assist Scheduled Control"
    assert ha_contract.apis["assist_climate"].name == "Assist Climate"
    assert ha_contract.apis["assist_fan"].name == "Assist Fan"
    assert ha_contract.apis["assist_history"].name == "Assist History"
    assert await setup.async_setup_entry(hass, entry)
    endpoint = hass.data["assist_tools"].endpoints[entry.entry_id]
    assert hass.data["assist_tools"].for_device("kitchen") is endpoint
    first = await endpoint.manager.async_create(300)
    assert await setup.async_unload_entry(hass, entry)
    assert hass.storage["assist_tools.test-entry"]["timer_id"] == first["timer_id"]
    assert await setup.async_setup_entry(hass, entry)
    assert (
        hass.data["assist_tools"].endpoints[entry.entry_id].manager.snapshot()["state"] == "active"
    )
    assert await setup.async_unload_entry(hass, entry)
    await setup.async_remove_entry(hass, entry)
    assert "assist_tools.test-entry" not in hass.storage
    hass.data["assist_tools"].unregister_api()
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


def test_matching_player_ignores_entries_without_player(load_module, hass):
    hass.config_entries.async_entries = lambda domain: [
        SimpleNamespace(data={"satellite": "assist_satellite.microphone"}),
        SimpleNamespace(data={"weather_entity": "weather.home"}),
    ]
    assert (
        load_module("config_flow").matching_player(hass, "assist_satellite.kitchen")
        == "media_player.kitchen"
    )


def test_config_validation_ignores_non_timer_entries(load_module, hass):
    config = load_module("config_flow")
    hass.config_entries.async_entries = lambda domain: [
        SimpleNamespace(entry_id="weather", data={"weather_entity": "weather.home"})
    ]
    assert (
        config.validate(
            hass,
            {"satellite": "assist_satellite.kitchen", "media_player": "media_player.kitchen"},
        )
        == "kitchen"
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
    if not automatic:
        assert result["step_id"] == "player"
        result = await flow.async_step_player({"media_player": "media_player.kitchen"})
    assert result["data"] == {
        "satellite": "assist_satellite.kitchen",
        "media_player": "media_player.kitchen",
    }
    flow.async_set_unique_id.assert_awaited_with("kitchen")


async def test_google_speaker_accepts_microphone_only_satellite(load_module, hass):
    config = load_module("config_flow")
    flow = config.AssistTimersConfigFlow()
    flow.hass = hass
    flow.async_show_form = lambda **kwargs: kwargs
    flow.async_set_unique_id = AsyncMock()
    flow._abort_if_unique_id_configured = lambda: None
    flow.async_create_entry = lambda **kwargs: kwargs
    hass.state_values["assist_satellite.kitchen"].attributes["supported_features"] = 0
    selection = {"satellite": "assist_satellite.kitchen", "media_player": "media_player.kitchen"}

    form = await flow.async_step_user({"setup_type": "google_speaker"})
    assert form["step_id"] == "google_speaker"
    ordinary = await flow.async_step_satellite(selection)
    assert ordinary["errors"] == {"base": "announce_not_supported"}
    entry = await flow.async_step_google_speaker(selection)
    assert entry["data"] == {**selection, "google_speaker": True}
    flow.async_set_unique_id.assert_awaited_once_with("kitchen")

    output = load_module("satellite").SatelliteOutput(
        hass, *selection.values(), google_speaker=True
    )
    await output.async_announce("Confirmation", lambda: True)
    await output.async_play()
    assert hass.services.async_call.await_count == 1
    assert hass.services.async_call.await_args.args[:2] == ("media_player", "play_media")


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
