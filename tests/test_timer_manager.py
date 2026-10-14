"""Exercise lifecycle, restoration, and race safety; related file: timer_manager.py."""

import asyncio
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.fixture
def timer(load_module):
    core = load_module("timer_manager")
    clock = SimpleNamespace(now=1000.0, mono=100.0)
    output = SimpleNamespace(
        async_play=AsyncMock(), async_stop=AsyncMock(), async_announce=AsyncMock()
    )
    save = AsyncMock()
    manager = core.TimerManager(
        output, save, wall_clock=lambda: clock.now, clock=lambda: clock.mono
    )
    return SimpleNamespace(core=core, manager=manager, clock=clock, output=output, save=save)


async def test_id_and_duplicate_slots(timer):
    first = await timer.manager.async_create(300, "라면")
    assert first["timer_id"] and first["name"] == "라면"
    with pytest.raises(timer.core.TimerError, match="already_exists"):
        await timer.manager.async_create(10)
    await timer.manager.async_pause()
    with pytest.raises(timer.core.TimerError, match="already_exists"):
        await timer.manager.async_create(10)
    await timer.manager.async_close()


async def test_pause_resume_preserve_original_duration(timer):
    created = await timer.manager.async_create(300)
    timer.clock.mono += 65
    timer.clock.now += 65
    paused = await timer.manager.async_pause(created["timer_id"])
    assert paused["remaining_seconds"] == 235
    assert paused["duration_seconds"] == 300
    timer.clock.mono += 100
    timer.clock.now += 100
    resumed = await timer.manager.async_resume()
    assert resumed["remaining_seconds"] == 235
    timer.clock.mono += 10
    assert timer.manager.snapshot()["remaining_seconds"] == 225
    await timer.manager.async_close()


async def test_cancel_before_expiry_does_not_stop_unrelated_music(timer):
    created = await timer.manager.async_create(30)
    result = await timer.manager.async_cancel()
    assert result["skip_announcement"] is False
    assert timer.manager.snapshot() == {"state": "idle"}
    await timer.manager.async_expire(created["timer_id"])
    timer.output.async_play.assert_not_awaited()
    timer.output.async_stop.assert_not_awaited()


async def test_expiry_plays_and_cancel_skips_announcement(timer):
    created = await timer.manager.async_create(30)
    await timer.manager.async_expire(created["timer_id"])
    timer.output.async_play.assert_awaited_once()
    assert timer.manager.snapshot()["state"] == "ringing"
    assert timer.manager.timer.alarm_ends_at == 1060
    with pytest.raises(timer.core.TimerError, match="already_exists"):
        await timer.manager.async_create(10)
    result = await timer.manager.async_cancel()
    assert result["skip_announcement"] is True
    timer.output.async_stop.assert_awaited_once()
    assert timer.manager._handle is None


async def test_auto_stop_and_stale_callbacks_do_not_stop_new_timer(timer):
    first = await timer.manager.async_create(1)
    await timer.manager.async_expire(first["timer_id"])
    await timer.manager.async_alarm_finished(first["timer_id"])
    timer.output.async_stop.assert_awaited_once()
    second = await timer.manager.async_create(90)
    await timer.manager.async_alarm_finished(first["timer_id"])
    await timer.manager.async_expire(first["timer_id"])
    assert timer.manager.snapshot()["timer_id"] == second["timer_id"]
    assert timer.output.async_stop.await_count == 1
    assert timer.output.async_play.await_count == 1
    await timer.manager.async_close()


async def test_concurrent_create_has_only_one_winner(timer):
    results = await asyncio.gather(
        timer.manager.async_create(30), timer.manager.async_create(40), return_exceptions=True
    )
    assert sum(isinstance(r, dict) for r in results) == 1
    assert sum(isinstance(r, timer.core.TimerError) for r in results) == 1
    await timer.manager.async_close()


async def test_wrong_id_does_not_touch_alarm(timer):
    created = await timer.manager.async_create(1)
    await timer.manager.async_expire(created["timer_id"])
    with pytest.raises(timer.core.TimerError, match="id_mismatch"):
        await timer.manager.async_cancel("wrong")
    timer.output.async_stop.assert_not_awaited()
    await timer.manager.async_close()


async def test_failed_stop_retains_ownership_and_can_retry(timer):
    created = await timer.manager.async_create(1)
    await timer.manager.async_expire(created["timer_id"])
    timer.output.async_stop.side_effect = OSError("offline")
    with pytest.raises(timer.core.TimerError, match="alarm_stop_failed"):
        await timer.manager.async_cancel()
    assert timer.manager.snapshot()["state"] == "ringing"
    assert timer.manager.snapshot()["last_error"] == "alarm_stop_failed"
    timer.output.async_stop.side_effect = None
    await timer.manager.async_cancel()
    assert timer.manager.snapshot()["state"] == "idle"


async def test_failed_auto_stop_does_not_tight_loop(timer):
    created = await timer.manager.async_create(1)
    await timer.manager.async_expire(created["timer_id"])
    timer.output.async_stop.side_effect = OSError("offline")
    await timer.manager.async_alarm_finished(created["timer_id"])
    assert timer.manager._handle is None
    assert timer.manager.snapshot()["last_error"] == "alarm_stop_failed"
    timer.output.async_stop.side_effect = None
    await timer.manager.async_close()


async def test_play_failure_is_retained_for_status_and_cancel(timer):
    created = await timer.manager.async_create(1)
    timer.output.async_play.side_effect = OSError("offline")
    await timer.manager.async_expire(created["timer_id"])
    assert timer.manager.snapshot()["state"] == "alarm_failed"
    assert timer.manager.snapshot()["last_error"] == "alarm_play_failed"
    result = await timer.manager.async_cancel()
    assert result["skip_announcement"] is True


async def test_restore_active_subtracts_offline_time(timer):
    await timer.manager.async_create(300)
    saved = asdict(timer.manager.timer)
    await timer.manager.async_close()
    timer.clock.now += 120
    manager = timer.core.TimerManager(
        timer.output, timer.save, wall_clock=lambda: timer.clock.now, clock=lambda: timer.clock.mono
    )
    await manager.async_restore(saved)
    assert manager.snapshot()["remaining_seconds"] == 180
    timer.output.async_play.assert_not_awaited()
    await manager.async_close()


async def test_restore_paused_does_not_consume_offline_time(timer):
    await timer.manager.async_create(300)
    timer.clock.mono += 40
    await timer.manager.async_pause()
    saved = asdict(timer.manager.timer)
    await timer.manager.async_close()
    timer.clock.now += 10000
    manager = timer.core.TimerManager(timer.output, timer.save, wall_clock=lambda: timer.clock.now)
    await manager.async_restore(saved)
    assert manager.snapshot()["remaining_seconds"] == 260
    assert manager._handle is None
    await manager.async_close()


async def test_restore_expired_offline_does_not_replay(timer):
    await timer.manager.async_create(30)
    saved = asdict(timer.manager.timer)
    await timer.manager.async_close()
    timer.clock.now += 60
    manager = timer.core.TimerManager(timer.output, timer.save, wall_clock=lambda: timer.clock.now)
    await manager.async_restore(saved)
    assert manager.snapshot()["state"] == "idle"
    timer.output.async_play.assert_not_awaited()


async def test_restore_ringing_stops_existing_audio(timer):
    await timer.manager.async_create(30)
    saved = asdict(timer.manager.timer)
    saved.update(state="ringing", remaining_seconds=0, expires_at=None, alarm_ends_at=1060)
    await timer.manager.async_close()
    manager = timer.core.TimerManager(timer.output, timer.save)
    await manager.async_restore(saved)
    timer.output.async_stop.assert_awaited_once()
    timer.output.async_play.assert_not_awaited()
    assert manager.snapshot()["state"] == "idle"


@pytest.mark.parametrize("value", [0, -1, 604801, True, 1.5, "30"])
async def test_reject_invalid_duration(timer, value):
    with pytest.raises(timer.core.TimerError, match="duration_out_of_range"):
        await timer.manager.async_create(value)
    assert timer.manager.timer is None


@pytest.mark.parametrize("saved", [{}, {"state": "paused"}, "invalid"])
async def test_invalid_restore_is_cleared(timer, saved):
    await timer.manager.async_restore(saved)
    assert timer.manager.timer is None
    timer.save.assert_awaited_with(None)


async def test_failed_create_save_rolls_back(timer):
    timer.save.side_effect = OSError("disk full")
    with pytest.raises(OSError):
        await timer.manager.async_create(30)
    assert timer.manager.timer is None
    assert timer.manager._handle is None


async def test_failed_pause_save_keeps_original_schedule(timer):
    await timer.manager.async_create(30)
    handle = timer.manager._handle
    timer.save.side_effect = OSError("disk full")
    with pytest.raises(OSError):
        await timer.manager.async_pause()
    assert timer.manager.timer.state == "active"
    assert timer.manager._handle is handle
    timer.save.side_effect = None
    await timer.manager.async_close()


def test_korean_duration_format(load_module):
    format_duration = load_module("timer_manager").duration_text
    assert format_duration(300) == "5분"
    assert format_duration(3665) == "1시간 1분 5초"


async def test_real_scheduled_expiry_and_automatic_stop(load_module, monkeypatch):
    core = load_module("timer_manager")
    monkeypatch.setattr(core, "ALARM_SECONDS", 0.01)
    played, stopped = asyncio.Event(), asyncio.Event()
    output = SimpleNamespace(
        async_play=AsyncMock(side_effect=played.set), async_stop=AsyncMock(side_effect=stopped.set)
    )
    manager = core.TimerManager(output, AsyncMock())
    await manager.async_create(1)
    await asyncio.wait_for(played.wait(), 2)
    await asyncio.wait_for(stopped.wait(), 1)
    assert manager.snapshot()["state"] == "idle"
    await manager.async_close()


async def test_expiry_storage_failure_does_not_start_audio(timer):
    created = await timer.manager.async_create(1)
    timer.save.side_effect = OSError("disk full")
    with pytest.raises(OSError):
        await timer.manager.async_expire(created["timer_id"])
    timer.output.async_play.assert_not_awaited()
    assert timer.manager.snapshot()["state"] == "timer_failed"
    assert timer.manager.snapshot()["last_error"] == "timer_save_failed"
    timer.save.side_effect = None
    await timer.manager.async_cancel()


async def test_wall_clock_change_does_not_change_live_remaining_time(timer):
    await timer.manager.async_create(300)
    timer.clock.now -= 10000
    timer.clock.mono += 20
    assert timer.manager.snapshot()["remaining_seconds"] == 280
    await timer.manager.async_close()


async def test_waiting_old_auto_stop_does_not_cancel_new_schedule(timer):
    first = await timer.manager.async_create(1)
    await timer.manager.async_expire(first["timer_id"])
    await timer.manager._lock.acquire()
    cancel = asyncio.create_task(timer.manager.async_cancel(first["timer_id"]))
    create = asyncio.create_task(timer.manager.async_create(90))
    await asyncio.sleep(0)
    stale = asyncio.create_task(timer.manager.async_alarm_finished(first["timer_id"]))
    await asyncio.sleep(0)
    timer.manager._lock.release()
    _, second, _ = await asyncio.gather(cancel, create, stale)
    assert timer.manager.snapshot()["timer_id"] == second["timer_id"]
    assert timer.manager._handle is not None
    assert not timer.manager._handle.cancelled()
    await timer.manager.async_close()


async def test_slow_storage_does_not_extend_countdown(timer):
    async def delayed_save(data):
        timer.clock.mono += 3
        timer.clock.now += 3

    timer.save.side_effect = delayed_save
    created = await timer.manager.async_create(30)
    assert created["remaining_seconds"] == 27
    assert created["expires_at"] - timer.clock.now == 27
    await timer.manager.async_close()
