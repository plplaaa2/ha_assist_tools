"""Test durable device jobs and HA policy boundaries; related files: scheduler/control.

All device actions use mocks. No physical light, fan, heater, or network is used.
"""

import asyncio
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml
from conftest import ROOT


@pytest.fixture
async def scheduler(load_module):
    module = load_module("scheduled_actions")
    clock = SimpleNamespace(wall=1000.0, mono=100.0)
    execute, save = AsyncMock(), AsyncMock()
    manager = module.ScheduledActions(
        execute, save, wall_clock=lambda: clock.wall, clock=lambda: clock.mono
    )
    yield SimpleNamespace(module=module, manager=manager, clock=clock, execute=execute, save=save)
    await manager.async_close()


async def test_multiple_jobs_do_not_execute_immediately(scheduler):
    first = await scheduler.manager.async_create("light.room", "turn_on", 300)
    second = await scheduler.manager.async_create("light.room", "turn_off", 600)
    assert first["job_id"] != second["job_id"]
    assert len(scheduler.manager.jobs) == 2
    scheduler.execute.assert_not_awaited()
    assert "owner_user_id" not in first and "call_id" not in first


async def test_due_job_runs_once(scheduler):
    job = await scheduler.manager.async_create("light.room", "turn_on", 30)
    await scheduler.manager.async_execute(job["job_id"])
    scheduler.execute.assert_not_awaited()
    scheduler.clock.mono += 30
    await scheduler.manager.async_execute(job["job_id"])
    await scheduler.manager.async_execute(job["job_id"])
    scheduler.execute.assert_awaited_once()
    assert scheduler.manager.jobs[job["job_id"]].state == "completed"


async def test_cancel_before_due_blocks_stale_callback(scheduler):
    job = await scheduler.manager.async_create("switch.room", "turn_off", 10)
    assert (await scheduler.manager.async_cancel(job["job_id"]))["state"] == "cancelled"
    scheduler.clock.mono += 10
    await scheduler.manager.async_execute(job["job_id"])
    scheduler.execute.assert_not_awaited()


async def test_concurrent_repeated_tool_calls_deduplicate(scheduler):
    results = await asyncio.gather(
        *(
            scheduler.manager.async_create("light.room", "turn_on", 300, "user", "call")
            for _ in range(3)
        )
    )
    assert len({job["job_id"] for job in results}) == 1
    assert len(scheduler.manager.jobs) == 1
    with pytest.raises(Exception, match="tool_call_id_conflict"):
        await scheduler.manager.async_create("light.room", "turn_off", 300, "user", "call")


async def test_execution_failure_is_not_retried(scheduler):
    job = await scheduler.manager.async_create("light.room", "turn_on", 1)
    scheduler.execute.side_effect = OSError("offline")
    scheduler.clock.mono += 1
    await scheduler.manager.async_execute(job["job_id"])
    await scheduler.manager.async_execute(job["job_id"])
    record = scheduler.manager.jobs[job["job_id"]]
    assert record.state == "failed" and record.last_error == "device_action_failed"
    scheduler.execute.assert_awaited_once()


async def test_storage_failure_blocks_create_and_execution(scheduler):
    scheduler.save.side_effect = OSError("disk full")
    with pytest.raises(OSError):
        await scheduler.manager.async_create("light.room", "turn_on", 1)
    assert not scheduler.manager.jobs
    scheduler.save.side_effect = None
    job = await scheduler.manager.async_create("light.room", "turn_on", 1)
    scheduler.clock.mono += 1
    scheduler.save.side_effect = OSError("disk full")
    with pytest.raises(OSError):
        await scheduler.manager.async_execute(job["job_id"])
    scheduler.execute.assert_not_awaited()


async def test_restore_future_overdue_and_executing(scheduler):
    job = await scheduler.manager.async_create("light.room", "turn_on", 300)
    future = asdict(scheduler.manager.jobs[job["job_id"]])
    overdue = {**future, "job_id": "overdue", "execute_at": 999}
    uncertain = {**future, "job_id": "uncertain", "state": "executing"}
    await scheduler.manager.async_close()
    restored = scheduler.module.ScheduledActions(
        scheduler.execute, scheduler.save, wall_clock=lambda: 1100, clock=lambda: 500
    )
    await restored.async_restore([future, overdue, uncertain])
    assert restored.snapshot(restored.jobs[job["job_id"]])["remaining_seconds"] == 200
    assert restored.jobs["overdue"].state == "expired"
    assert restored.jobs["uncertain"].state == "interrupted"
    scheduler.execute.assert_not_awaited()
    await restored.async_close()


async def test_restored_call_id_deduplicates(scheduler):
    job = await scheduler.manager.async_create("light.room", "turn_on", 300, None, "same")
    data = asdict(scheduler.manager.jobs[job["job_id"]])
    await scheduler.manager.async_close()
    restored = scheduler.module.ScheduledActions(scheduler.execute, scheduler.save)
    data["execute_at"] = restored.wall_clock() + 300
    await restored.async_restore([data])
    replay = await restored.async_create("light.room", "turn_on", 300, None, "same")
    assert replay["job_id"] == job["job_id"] and replay["replayed"]
    await restored.async_close()


async def test_cancel_cannot_claim_running_execution(scheduler):
    started, release = asyncio.Event(), asyncio.Event()

    async def execute(job):
        started.set()
        await release.wait()

    scheduler.manager.execute = execute
    job = await scheduler.manager.async_create("light.room", "turn_on", 1)
    scheduler.clock.mono += 1
    task = asyncio.create_task(scheduler.manager.async_execute(job["job_id"]))
    await started.wait()
    with pytest.raises(Exception, match="not_pending"):
        await scheduler.manager.async_cancel(job["job_id"])
    release.set()
    await task


async def test_real_scheduled_callback(load_module):
    finished = asyncio.Event()
    manager = load_module("scheduled_actions").ScheduledActions(
        AsyncMock(side_effect=lambda job: finished.set()), AsyncMock()
    )
    job = await manager.async_create("light.room", "turn_on", 1)
    await asyncio.wait_for(finished.wait(), 2)
    assert manager.jobs[job["job_id"]].state == "completed"
    await manager.async_close()


@pytest.mark.parametrize("delay", [0, -1, True, 1.5, 604801])
async def test_invalid_delay_rejected(scheduler, delay):
    with pytest.raises(Exception, match="delay_out_of_range"):
        await scheduler.manager.async_create("light.room", "turn_on", delay)


@pytest.fixture
def device(load_module, hass):
    hass.state_values["light.room"] = SimpleNamespace(
        entity_id="light.room", name="거실 조명", state="off", attributes={}
    )
    hass.exposed.add("light.room")
    return load_module("device_control").DeviceControl(hass)


async def test_device_checks_exposure_at_execution(device, scheduler, hass):
    await device.validate("light.room", "turn_on", None)
    scheduler.manager.execute = device.async_execute
    job = await scheduler.manager.async_create("light.room", "turn_on", 1)
    hass.exposed.clear()
    scheduler.clock.mono += 1
    await scheduler.manager.async_execute(job["job_id"])
    assert scheduler.manager.jobs[job["job_id"]].last_error == "entity_not_exposed"
    hass.services.async_call.assert_not_awaited()


async def test_device_action_keeps_user_context(device, load_module, hass):
    job = load_module("scheduled_actions").ScheduledAction(
        "id", "light.room", "turn_off", 1, 1000, "user"
    )
    await device.async_execute(job)
    call = hass.services.async_call.await_args
    assert call.args == ("light", "turn_off", {"entity_id": "light.room"})
    assert call.kwargs["context"].user_id == "user"


@pytest.mark.parametrize("reason", ["hidden", "unauthorized", "offline", "unsupported_service"])
async def test_device_invalid_target_rejected(device, hass, reason, load_module):
    if reason == "hidden":
        hass.exposed.clear()
    elif reason == "unauthorized":
        hass.auth.async_get_user.return_value.permissions.check_entity = lambda *args: False
    elif reason == "offline":
        hass.state_values["light.room"].state = "unavailable"
    else:
        hass.services.has_service = lambda *args: False
    with pytest.raises(load_module("timer_manager").TimerError):
        await device.validate("light.room", "turn_on", "user")
    hass.services.async_call.assert_not_awaited()


async def test_media_player_requires_actual_power_feature(device, hass):
    hass.exposed.add("media_player.kitchen")
    with pytest.raises(Exception, match="not_supported"):
        await device.validate("media_player.kitchen", "turn_on", None)


async def test_scheduler_independent_from_alarm_timer(
    device, scheduler, load_module, hass, ha_contract
):
    runtime = load_module("runtime").Runtime()
    runtime.actions, runtime.control = scheduler.manager, device
    hass.data["assist_timers"] = runtime
    tool = load_module("action_tools").ActionTool("schedule_action", ["light.room"])
    result = await tool.async_call(
        hass,
        ha_contract.ToolInput(
            tool.name, {"entity_id": "light.room", "action": "turn_off", "delay_seconds": 300}
        ),
        ha_contract.LLMContext(None),
    )
    assert not result.error and result.data["state"] == "pending"
    assert result.data["response_mode"] == "normal"
    hass.services.async_call.assert_not_awaited()
    api = load_module("llm").AssistDevicesAPI(
        hass=hass, id="assist_devices", name="Assist Scheduled Control"
    )
    instance = await api.async_get_api_instance(ha_contract.LLMContext(None))
    assert {t.name for t in instance.tools} == {
        "AssistScheduledControlCreate",
        "AssistScheduledControlList",
        "AssistScheduledControlCancel",
    }
    timer_api = load_module("llm").AssistTimersAPI(
        hass=hass, id="assist_timers", name="Assist Timer"
    )
    timer_instance = await timer_api.async_get_api_instance(ha_contract.LLMContext(None))
    assert timer_instance.tools == []
    assert "Available device names" not in timer_instance.api_prompt
    listed = await load_module("action_tools").perform_action_operation(
        runtime, "list_actions", {}, None
    )
    assert listed["jobs"][0]["job_id"] == result.data["job_id"]
    cancelled = await load_module("action_tools").perform_action_operation(
        runtime, "cancel_action", {"job_id": result.data["job_id"]}, None
    )
    assert cancelled["state"] == "cancelled"


async def test_job_listing_and_cancel_respect_other_user_permissions(device, scheduler, hass):
    await scheduler.manager.async_create("light.room", "turn_on", 300, "other")
    hass.auth.async_get_user.return_value.permissions.check_entity = lambda *args: False
    assert await device.visible_jobs(scheduler.manager, "user") == []
    with pytest.raises(Exception, match="control_not_allowed"):
        await device.authorize_cancel(next(iter(scheduler.manager.jobs.values())), "user")


async def test_invalid_restore_record_not_executed(scheduler):
    await scheduler.manager.async_restore([{"entity_id": "light.room"}])
    assert not scheduler.manager.jobs
    scheduler.execute.assert_not_awaited()


async def test_action_services_schedule_list_and_cancel(device, scheduler, load_module, hass):
    runtime = load_module("runtime").Runtime()
    runtime.actions, runtime.control = scheduler.manager, device
    handlers = {}
    hass.services.async_register = lambda domain, name, handler, **kwargs: handlers.update(
        {name: handler}
    )
    load_module("__init__").register_action_services(hass, runtime)

    def call(name, data):
        return SimpleNamespace(
            service=name, data=data, context=SimpleNamespace(user_id=None), return_response=True
        )

    result = await handlers["schedule_action"](
        call(
            "schedule_action",
            {"entity_id": "light.room", "action": "turn_on", "delay_seconds": 300},
        )
    )
    listed = await handlers["list_actions"](call("list_actions", {}))
    assert listed["jobs"][0]["job_id"] == result["job_id"]
    cancelled = await handlers["cancel_action"](call("cancel_action", {"job_id": result["job_id"]}))
    assert cancelled["state"] == "cancelled"
    hass.services.async_call.assert_not_awaited()


async def test_last_entry_unload_preserves_future_device_job(load_module, hass, ha_contract):
    setup = load_module("__init__")
    entry = SimpleNamespace(
        entry_id="actions-lifecycle",
        data={"satellite": "assist_satellite.kitchen", "media_player": "media_player.kitchen"},
    )
    await setup.async_setup(hass, {})
    try:
        await setup.async_setup_entry(hass, entry)
        runtime = hass.data["assist_timers"]
        created = await runtime.actions.async_create("light.room", "turn_on", 300)
        await setup.async_unload_entry(hass, entry)
        assert runtime.actions is None
        await setup.async_setup_entry(hass, entry)
        assert runtime.actions.jobs[created["job_id"]].state == "pending"
        await setup.async_unload_entry(hass, entry)
        await setup.async_remove_entry(hass, entry)
        assert "assist_timers.scheduled_actions" not in hass.storage
    finally:
        hass.data["assist_timers"].unregister_api()


async def test_revoked_user_permission_blocks_due_action(device, scheduler, hass):
    await device.validate("light.room", "turn_on", "user")
    scheduler.manager.execute = device.async_execute
    created = await scheduler.manager.async_create("light.room", "turn_on", 1, "user")
    hass.auth.async_get_user.return_value.permissions.check_entity = lambda *args: False
    scheduler.clock.mono += 1
    await scheduler.manager.async_execute(created["job_id"])
    assert scheduler.manager.jobs[created["job_id"]].last_error == "entity_control_not_allowed"
    hass.services.async_call.assert_not_awaited()


async def test_recent_cancel_is_retained_in_bounded_history(scheduler):
    created = await scheduler.manager.async_create("light.room", "turn_on", 300)
    for index in range(64):
        job = scheduler.module.ScheduledAction(
            f"old-{index}", "light.room", "turn_on", 1, 1000, state="completed"
        )
        scheduler.manager.jobs[job.job_id] = job
    await scheduler.manager.async_cancel(created["job_id"])
    assert created["job_id"] in scheduler.manager.jobs
    assert len(scheduler.manager.jobs) == 64


def test_action_service_descriptions_are_valid_yaml():
    descriptions = yaml.safe_load(
        (ROOT / "custom_components/assist_timers/services.yaml").read_text(encoding="utf-8")
    )
    assert set(descriptions["schedule_action"]["fields"]) == {
        "entity_id",
        "action",
        "delay_seconds",
    }
    assert "list_actions" in descriptions and "cancel_action" in descriptions
