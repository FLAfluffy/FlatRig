"""常駐アプリ用の部品: ブリッジを別スレッドで動かす BridgeRunner と、VRChat との連動を決める VrchatFollower。

画面(gui.py)に依存しないので、ここだけでテストできる。
"""

from __future__ import annotations

import asyncio
import enum
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass

from .app import Bridge, State

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunnerStatus:
    running: bool
    state: State | None
    vts_frames: int
    osc_sent: int


class BridgeRunner:
    """Bridge.run を専用スレッドの asyncio ループで動かす。start/stop は何度でも呼べる。"""

    def __init__(self, make_bridge: Callable[[], Bridge]):
        self._make_bridge = make_bridge
        self._bridge: Bridge | None = None
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop: asyncio.Event | None = None
        self._ready = threading.Event()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._bridge = self._make_bridge()
        self._ready.clear()
        self._thread = threading.Thread(target=self._main, args=(self._bridge,), name="bridge", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=5)

    def _main(self, bridge: Bridge) -> None:
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            self._loop = loop
            self._stop = asyncio.Event()
            self._ready.set()
            loop.run_until_complete(bridge.run(self._stop))
        except Exception:
            log.exception("ブリッジが異常終了しました")
        finally:
            bridge.send_shutdown()
            self._loop = None
            self._ready.set()
            loop.close()

    def stop(self, timeout: float = 5.0) -> None:
        """中継を止める(FR_Active=false と全値 0 を送ってから戻る)。"""
        thread, loop, stop = self._thread, self._loop, self._stop
        if thread is None:
            return
        if loop is not None and stop is not None:
            try:
                loop.call_soon_threadsafe(stop.set)
            except RuntimeError:  # ループが既に閉じている
                pass
        thread.join(timeout)
        if thread.is_alive():
            log.warning("ブリッジの停止が %g 秒以内に終わりませんでした", timeout)
        self._thread = None

    def status(self) -> RunnerStatus:
        bridge = self._bridge
        if bridge is None:
            return RunnerStatus(False, None, 0, 0)
        return RunnerStatus(self.running, bridge.state, bridge.vts_frames, bridge.sender.sent_count)


class Action(enum.Enum):
    NONE = "none"
    START = "start"
    STOP = "stop"
    QUIT = "quit"


@dataclass
class VrchatFollower:
    """VRChat の起動状態の変化から、次にすることを決める。

    - 待機モード(wait_mode): VRChat が動いている間だけ中継する。VRChat が終わっても常駐は続ける。
    - 通常モード: 起動時から中継する。exit_with_vrchat なら、一度起動を見た VRChat が終わったらアプリを終了する。
    """

    wait_mode: bool
    exit_with_vrchat: bool
    _prev: bool | None = None

    def tick(self, vrchat_running: bool | None) -> Action:
        if vrchat_running is None:  # 判定できない(Windows 以外など)
            return Action.NONE
        prev, self._prev = self._prev, vrchat_running
        if self.wait_mode:
            if vrchat_running and not prev:
                return Action.START
            if not vrchat_running and prev:
                return Action.STOP
            return Action.NONE
        if prev and not vrchat_running and self.exit_with_vrchat:
            return Action.QUIT
        return Action.NONE
