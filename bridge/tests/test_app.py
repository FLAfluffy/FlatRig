"""ブリッジ本体の E2E: モック VTS → Bridge → UDP 受信(SPEC §4.3, §4.6)。"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
import time
from pathlib import Path

import pytest
from mock_vts import MockVts
from osc_helpers import AsyncCollector, start_collector
from pythonosc.udp_client import SimpleUDPClient

from flatrig_bridge.app import BACKOFF_SEC, Bridge, State, backoff_delay
from flatrig_bridge.config import load_params, load_settings
from flatrig_bridge.mapping import quantize

CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"
FAST_BACKOFF = (0.05, 0.1)


def free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def make_settings(tmp_path: Path, vts_port: int, osc_port: int, *, listen: bool = False, **mapping):
    data = json.loads((CONFIG_DIR / "settings.example.json").read_text(encoding="utf-8"))
    data["vts"].update(port=vts_port, poll_hz=50, request_timeout_sec=1.0)
    data["osc"].update(port=osc_port, listen_enabled=listen, listen_port=free_udp_port())
    data["mapping"].update({"resend_interval_sec": 0.2, "stale_timeout_sec": 0.4, "ema_alpha": 1.0} | mapping)
    data["logging"]["stats_interval_sec"] = 0.2
    (tmp_path / "params.example.json").write_text(
        (CONFIG_DIR / "params.example.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    settings = load_settings(path)
    return settings, load_params(settings.mapping.params_file)


class Harness:
    def __init__(self, bridge: Bridge, osc: AsyncCollector):
        self.bridge = bridge
        self.osc = osc
        self.stop = asyncio.Event()
        self.task: asyncio.Task | None = None

    def start(self) -> Harness:
        self.task = asyncio.create_task(self.bridge.run(self.stop))
        return self

    async def wait_state(self, state: State, timeout: float = 5.0) -> None:
        deadline = time.monotonic() + timeout
        while self.bridge.state is not state:
            assert time.monotonic() < deadline, f"state={self.bridge.state}, expected {state}"
            await asyncio.sleep(0.01)

    async def finish(self) -> None:
        self.stop.set()
        assert self.task is not None
        await asyncio.wait_for(self.task, 5)
        await asyncio.sleep(0.05)  # 終了時の UDP を受け取る


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


def make_harness(tmp_path, vts: MockVts, osc: AsyncCollector, *, listen=False, backoff=FAST_BACKOFF, **mapping):
    settings, params = make_settings(tmp_path, vts.port, osc.port, listen=listen, **mapping)
    bridge = Bridge(settings, params, backoff=backoff, auth_retry_sec=0.2)
    return Harness(bridge, osc)


# --- 正常系 E2E ---------------------------------------------------------------


async def test_e2e_values_reach_osc(tmp_path, vts: MockVts, osc: AsyncCollector):
    h = make_harness(tmp_path, vts, osc).start()
    await h.wait_state(State.STREAMING)
    await osc.wait_for(lambda: osc.values("FR_MouthOpenY"))
    # 12/30 → 0.4 → 量子化。目は unsigned でそのまま
    assert osc.values("FR_AngleX")[0] == pytest.approx(quantize(0.4), abs=1e-6)
    assert osc.values("FR_AngleY")[0] == pytest.approx(quantize(-0.2), abs=1e-6)
    assert osc.values("FR_EyeROpen")[0] == pytest.approx(quantize(0.5), abs=1e-6)
    assert osc.values("FR_AngleZ") == []  # VTS に無いパラメータは送らない
    assert True in osc.values("FR_Active")
    await h.finish()


async def test_e2e_follows_value_changes(tmp_path, vts: MockVts, osc: AsyncCollector):
    h = make_harness(tmp_path, vts, osc).start()
    await osc.wait_for(lambda: osc.values("FR_AngleX"))
    vts.parameters[0]["value"] = -30.0
    await osc.wait_for(lambda: osc.values("FR_AngleX")[-1] == pytest.approx(-1.0))
    await h.finish()


def hand_inputs(found: float, x: float, y: float) -> list[dict]:
    return [
        {"name": "HandRightFound", "value": found, "min": 0.0, "max": 1.0, "defaultValue": 0.0},
        {"name": "HandRightPositionX", "value": x, "min": -10.0, "max": 10.0, "defaultValue": 0.0},
        {"name": "HandRightPositionY", "value": y, "min": -10.0, "max": 10.0, "defaultValue": 0.0},
        {"name": "HandLeftFound", "value": 0.0, "min": 0.0, "max": 1.0, "defaultValue": 0.0},
    ]


async def test_e2e_hands_reach_osc(tmp_path, vts: MockVts, osc: AsyncCollector):
    vts.input_parameters = hand_inputs(1.0, 5.0, -2.5)
    h = make_harness(tmp_path, vts, osc).start()
    await osc.wait_for(lambda: osc.values("FR_HandRY") and osc.values("FR_HandLFound"))
    assert osc.values("FR_HandRX")[0] == pytest.approx(quantize(0.5), abs=1e-6)
    assert osc.values("FR_HandRY")[0] == pytest.approx(quantize(-0.25), abs=1e-6)
    assert osc.values("FR_HandRFound")[-1] is True
    assert osc.values("FR_HandLFound")[-1] is False
    assert osc.values("FR_HandLX") == []
    await h.finish()
    assert osc.values("FR_HandRFound")[-1] is False  # 終了時


async def test_hands_skipped_when_vts_has_no_inputs(tmp_path, vts: MockVts, osc: AsyncCollector, caplog):
    h = make_harness(tmp_path, vts, osc).start()
    await osc.wait_for(lambda: osc.values("FR_AngleX"))
    await asyncio.sleep(0.1)
    await h.finish()
    assert vts.types().count("InputParameterListRequest") == 1  # 未対応なら以後は読まない
    assert "手のトラッキングを使いません" in caplog.text
    assert osc.values("FR_HandRX") == []


async def test_active_transitions_and_shutdown(tmp_path, vts: MockVts, osc: AsyncCollector):
    h = make_harness(tmp_path, vts, osc).start()
    await h.wait_state(State.STREAMING)
    await h.finish()
    active = osc.values("FR_Active")
    assert active[0] is False  # 起動直後
    assert True in active
    assert active[-1] is False  # 終了時
    n = len(h.bridge.targets)
    tail = osc.messages[-(n + 1) :]
    assert tail[0] == ("/avatar/parameters/FR_Active", "F", [False])
    assert all(m[2] == [0.0] for m in tail[1:])
    assert {m[0].rsplit("/", 1)[1] for m in tail[1:]} == {p.avatar for p in h.bridge.targets}


async def test_shutdown_on_cancel(tmp_path, vts: MockVts, osc: AsyncCollector):
    h = make_harness(tmp_path, vts, osc).start()
    await h.wait_state(State.STREAMING)
    assert h.task is not None
    h.task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await h.task
    await asyncio.sleep(0.05)
    assert osc.values("FR_Active")[-1] is False
    sent = len(osc.messages)
    h.bridge.send_shutdown()  # 2 回目は送らない
    await asyncio.sleep(0.05)
    assert len(osc.messages) == sent


async def test_periodic_resend(tmp_path, vts: MockVts, osc: AsyncCollector):
    h = make_harness(tmp_path, vts, osc).start()
    await osc.wait_for(lambda: osc.values("FR_AngleX"))
    # 値が変わらなくても resend_interval_sec(0.2 秒)ごとに再送される
    await osc.wait_for(lambda: len(osc.values("FR_AngleX")) >= 3, timeout=2)
    await h.finish()


async def test_model_not_loaded_then_loaded(tmp_path, vts: MockVts, osc: AsyncCollector):
    vts.model_loaded = False
    h = make_harness(tmp_path, vts, osc).start()
    await h.wait_state(State.NO_MODEL)
    await asyncio.sleep(0.3)
    assert True not in osc.values("FR_Active")
    vts.model_loaded = True
    await h.wait_state(State.STREAMING)
    await osc.wait_for(lambda: True in osc.values("FR_Active"))
    await h.finish()


# --- 切断・途絶・認証 -----------------------------------------------------------


async def test_reconnect_after_disconnect_within_10s(tmp_path, vts: MockVts, osc: AsyncCollector):
    h = make_harness(tmp_path, vts, osc, backoff=BACKOFF_SEC).start()  # 実際のバックオフ(初回 1 秒)
    await h.wait_state(State.STREAMING)
    vts.close_on_types.add("Live2DParameterListRequest")
    await h.wait_state(State.DISCONNECTED, timeout=2)
    assert osc.values("FR_Active")[-1] is False
    vts.close_on_types.clear()
    started = time.monotonic()
    await h.wait_state(State.STREAMING, timeout=10)
    assert time.monotonic() - started < 10
    assert h.bridge.connections == 2
    assert vts.types().count("AuthenticationTokenRequest") == 1  # 再接続ではトークンを再利用
    await h.finish()


async def test_vts_down_at_start_then_up(tmp_path, osc: AsyncCollector):
    vts = MockVts()
    await vts.start()
    port = vts.port
    await vts.stop()
    settings, params = make_settings(tmp_path, port, osc.port)
    h = Harness(Bridge(settings, params, backoff=FAST_BACKOFF), osc).start()
    await asyncio.sleep(0.3)
    assert h.bridge.state is State.DISCONNECTED
    from websockets.asyncio.server import serve

    server = MockVts()
    server._server = await serve(server._handle, "127.0.0.1", port)
    try:
        await h.wait_state(State.STREAMING, timeout=3)
    finally:
        await h.finish()
        await server.stop()


async def test_stale_response_triggers_reconnect(tmp_path, vts: MockVts, osc: AsyncCollector):
    h = make_harness(tmp_path, vts, osc).start()
    await h.wait_state(State.STREAMING)
    vts.silent_types.add("Live2DParameterListRequest")
    await h.wait_state(State.DISCONNECTED, timeout=2)  # stale_timeout_sec=0.4
    assert osc.values("FR_Active")[-1] is False
    vts.silent_types.clear()
    await h.wait_state(State.STREAMING, timeout=3)
    await h.finish()


async def test_auth_denied_hint_shown_once(tmp_path, vts: MockVts, osc: AsyncCollector, caplog):
    caplog.set_level(logging.WARNING)
    vts.deny_token_request = True
    h = make_harness(tmp_path, vts, osc).start()
    await asyncio.sleep(0.9)  # auth_retry_sec=0.2 で数回再試行される
    assert vts.types().count("AuthenticationTokenRequest") >= 2
    hints = [r for r in caplog.records if "許可してください" in r.getMessage()]
    assert len(hints) == 1
    assert True not in osc.values("FR_Active")
    vts.deny_token_request = False
    await h.wait_state(State.STREAMING, timeout=3)
    await h.finish()


# --- /avatar/change と受信ポート ------------------------------------------------


async def test_avatar_change_triggers_resend(tmp_path, vts: MockVts, osc: AsyncCollector):
    h = make_harness(tmp_path, vts, osc, listen=True, resend_interval_sec=30).start()
    await osc.wait_for(lambda: osc.values("FR_AngleX"))
    await asyncio.sleep(0.1)
    before = len(osc.values("FR_AngleX"))
    SimpleUDPClient("127.0.0.1", h.bridge.settings.osc.listen_port).send_message("/avatar/change", "avtr_dummy")
    await osc.wait_for(lambda: len(osc.values("FR_AngleX")) > before, timeout=2)
    await h.finish()


async def test_busy_listen_port_does_not_stop_bridge(tmp_path, vts: MockVts, osc: AsyncCollector, caplog):
    caplog.set_level(logging.WARNING)
    h = make_harness(tmp_path, vts, osc, listen=True)
    blocker = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    blocker.bind(("127.0.0.1", h.bridge.settings.osc.listen_port))
    try:
        h.start()
        await h.wait_state(State.STREAMING)
        assert any("listen_enabled" in r.getMessage() for r in caplog.records)
        await h.finish()
    finally:
        blocker.close()


# --- その他 -------------------------------------------------------------------


def test_backoff_sequence():
    assert [backoff_delay(i) for i in range(7)] == [1, 2, 4, 8, 10, 10, 10]


async def test_stats_log(tmp_path, vts: MockVts, osc: AsyncCollector, caplog):
    caplog.set_level(logging.INFO)
    h = make_harness(tmp_path, vts, osc).start()
    await h.wait_state(State.STREAMING)
    await asyncio.sleep(0.5)
    await h.finish()
    stats = [r.getMessage() for r in caplog.records if "VTS受信" in r.getMessage()]
    assert any("状態=STREAMING" in m for m in stats)


async def test_max_phase_limits_targets(tmp_path, vts: MockVts, osc: AsyncCollector):
    h = make_harness(tmp_path, vts, osc, max_phase=1)
    assert [p.avatar for p in h.bridge.targets] == [
        "FR_AngleX",
        "FR_AngleY",
        "FR_EyeLOpen",
        "FR_EyeROpen",
        "FR_MouthOpenY",
    ]
