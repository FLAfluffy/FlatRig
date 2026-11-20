"""常駐アプリの部品: BridgeRunner(別スレッド実行)と VrchatFollower(VRChat 連動)。"""

from __future__ import annotations

import asyncio
import time

import pytest
from mock_vts import MockVts
from osc_helpers import AsyncCollector, start_collector
from test_app import FAST_BACKOFF, make_settings

from flatrig_bridge.app import Bridge, State
from flatrig_bridge.runner import Action, BridgeRunner, VrchatFollower


@pytest.fixture
async def vts():
    server = await MockVts().start()
    yield server
    await server.stop()


@pytest.fixture
async def osc():
    collector = await start_collector()
    yield collector
    collector.close()


async def wait_until(cond, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not cond():
        assert time.monotonic() < deadline, "timeout"
        await asyncio.sleep(0.02)


def make_runner(tmp_path, vts: MockVts, osc: AsyncCollector) -> BridgeRunner:
    settings, params = make_settings(tmp_path, vts.port, osc.port)
    return BridgeRunner(lambda: Bridge(settings, params, backoff=FAST_BACKOFF, auth_retry_sec=0.2))


async def test_runner_streams_and_stops(tmp_path, vts: MockVts, osc: AsyncCollector):
    runner = make_runner(tmp_path, vts, osc)
    assert runner.status().running is False
    runner.start()
    await wait_until(lambda: runner.status().state is State.STREAMING)
    await osc.wait_for(lambda: osc.values("FR_AngleX"))
    status = runner.status()
    assert status.running and status.vts_frames > 0 and status.osc_sent > 0

    await asyncio.to_thread(runner.stop)
    await asyncio.sleep(0.05)
    assert runner.running is False
    assert osc.values("FR_Active")[-1] is False  # 停止時に FR_Active=false を送る


async def test_runner_can_restart(tmp_path, vts: MockVts, osc: AsyncCollector):
    runner = make_runner(tmp_path, vts, osc)
    runner.start()
    await wait_until(lambda: runner.status().state is State.STREAMING)
    await asyncio.to_thread(runner.stop)
    runner.start()  # 待機モードで VRChat を再起動したとき
    await wait_until(lambda: runner.status().state is State.STREAMING)
    await asyncio.to_thread(runner.stop)
    assert runner.running is False


def test_runner_stop_without_start_is_noop():
    BridgeRunner(lambda: None).stop()  # type: ignore[arg-type, return-value]


def ticks(follower: VrchatFollower, seq) -> list[Action]:
    return [follower.tick(v) for v in seq]


def test_follower_wait_mode_follows_vrchat():
    f = VrchatFollower(wait_mode=True, exit_with_vrchat=True)
    assert ticks(f, [False, False, True, True, False, False, True]) == [
        Action.NONE, Action.NONE, Action.START, Action.NONE, Action.STOP, Action.NONE, Action.START,
    ]  # fmt: skip


def test_follower_wait_mode_starts_if_vrchat_already_running():
    f = VrchatFollower(wait_mode=True, exit_with_vrchat=False)
    assert ticks(f, [True, False]) == [Action.START, Action.STOP]


def test_follower_exit_with_vrchat():
    f = VrchatFollower(wait_mode=False, exit_with_vrchat=True)
    # VRChat より先に起動しても、一度 VRChat を見るまでは終了しない
    assert ticks(f, [False, False, True, True, False]) == [Action.NONE] * 4 + [Action.QUIT]


def test_follower_exit_disabled_keeps_running():
    f = VrchatFollower(wait_mode=False, exit_with_vrchat=False)
    assert ticks(f, [True, False, True, False]) == [Action.NONE] * 4
    f.exit_with_vrchat = True  # 画面で ON にした直後から効く
    assert ticks(f, [True, False]) == [Action.NONE, Action.QUIT]


def test_follower_ignores_unknown():
    f = VrchatFollower(wait_mode=True, exit_with_vrchat=True)
    assert ticks(f, [None, True, None, False]) == [Action.NONE, Action.START, Action.NONE, Action.STOP]
