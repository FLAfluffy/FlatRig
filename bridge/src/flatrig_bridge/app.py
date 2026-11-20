"""ブリッジ本体: 状態機械・再接続・送信・統計(docs/SPEC.md §4.3, §4.4)。

DISCONNECTED → AUTHENTICATING → NO_MODEL ⇄ STREAMING。切断・応答途絶・エラーは DISCONNECTED に戻り、
バックオフ(1→2→4→8→10 秒)して再接続する。STREAMING 以外では FR_Active=false を送る。
"""

from __future__ import annotations

import asyncio
import contextlib
import enum
import logging
import time
from collections.abc import Callable, Sequence

from .config import ParamsDoc, Settings
from .hands import HandTracker
from .mapping import ParamMapper
from .osc_in import OscListener
from .osc_out import OscSender
from .vts_client import VtsApiError, VtsAuthDenied, VtsClient, VtsError

log = logging.getLogger(__name__)

ACTIVE_FLAG = "FR_Active"
BACKOFF_SEC: tuple[float, ...] = (1, 2, 4, 8, 10)
AUTH_RETRY_SEC = 10.0


class State(enum.Enum):
    DISCONNECTED = "DISCONNECTED"
    AUTHENTICATING = "AUTHENTICATING"
    NO_MODEL = "NO_MODEL"
    STREAMING = "STREAMING"


class StaleError(VtsError):
    """stale_timeout_sec の間 VTS から応答が無い。"""


def backoff_delay(attempt: int, steps: Sequence[float] = BACKOFF_SEC) -> float:
    return steps[min(attempt, len(steps) - 1)]


class Bridge:
    def __init__(
        self,
        settings: Settings,
        params: ParamsDoc,
        *,
        sender: OscSender | None = None,
        client_factory: Callable[[], VtsClient] | None = None,
        backoff: Sequence[float] = BACKOFF_SEC,
        auth_retry_sec: float = AUTH_RETRY_SEC,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.settings = settings
        self.targets = params.params_up_to(settings.mapping.max_phase)
        self.sender = sender or OscSender.from_settings(settings.osc)
        self._client_factory = client_factory or (lambda: VtsClient(settings.vts))
        self._backoff = backoff
        self._auth_retry_sec = auth_retry_sec
        self._clock = clock
        m = settings.mapping
        self.mapper = ParamMapper(self.targets, ema_alpha=m.ema_alpha, quantize=m.quantize, deadband=m.deadband)
        self.hands = (
            HandTracker(settings.hands, ema_alpha=m.ema_alpha, quantize=m.quantize, deadband=m.deadband, clock=clock)
            if settings.hands.enabled
            else None
        )
        self._hands_unavailable = False
        self.state = State.DISCONNECTED
        self.vts_frames = 0
        self.connections = 0
        self._resend_now = asyncio.Event()
        self._shutdown_sent = False
        self._auth_hint_shown = False

    # --- 状態と送信 ---------------------------------------------------------------

    def _set_state(self, new: State) -> None:
        if new is self.state:
            return
        log.info("状態: %s → %s", self.state.value, new.value)
        self.state = new
        if new is State.STREAMING:
            self.mapper.reset()  # 次の更新で全値を送り直す
            if self.hands is not None:
                self.hands.reset()
            self.sender.send_bool(ACTIVE_FLAG, True)
        else:
            self.sender.send_bool(ACTIVE_FLAG, False)

    def request_resend(self) -> None:
        """/avatar/change 受信時など、すぐに全値を再送する。"""
        log.info("アバターの切替を検知したため全値を再送します")
        self._resend_now.set()

    def _resend(self) -> None:
        streaming = self.state is State.STREAMING
        self.sender.send_bool(ACTIVE_FLAG, streaming)
        if streaming:
            self.sender.send_floats(self.mapper.current_values())
            if self.hands is not None:
                floats, bools = self.hands.current_values()
                self.sender.send_floats(floats)
                self._send_bools(bools)

    def _send_bools(self, values: dict[str, bool]) -> None:
        for name, value in values.items():
            self.sender.send_bool(name, value)

    def send_shutdown(self) -> None:
        """FR_Active=false と全値 0 を 1 回だけ送る(SPEC §4.3 終了時)。"""
        if self._shutdown_sent:
            return
        self._shutdown_sent = True
        try:
            if self.hands is not None:
                self._send_bools(HandTracker.lost_values())
            self.sender.send_bool(ACTIVE_FLAG, False)
            self.sender.send_floats({p.avatar: 0.0 for p in self.targets})
            log.info("終了: FR_Active=false と全値 0 を送信しました")
        except OSError as exc:
            log.warning("終了時の送信に失敗しました: %s", exc)

    # --- メインループ --------------------------------------------------------------

    async def run(self, stop: asyncio.Event) -> None:
        listener: OscListener | None = None
        if self.settings.osc.listen_enabled:
            listener = OscListener(self.settings.osc.listen_port, on_avatar_change=self.request_resend)
            await listener.start()
        self.sender.send_bool(ACTIVE_FLAG, False)
        tasks = [asyncio.create_task(self._resend_loop(stop)), asyncio.create_task(self._stats_loop(stop))]
        try:
            await self._connect_loop(stop)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if listener is not None:
                listener.stop()
            self.send_shutdown()

    async def _connect_loop(self, stop: asyncio.Event) -> None:
        attempt = 0
        while not stop.is_set():
            self._set_state(State.DISCONNECTED)
            client = self._client_factory()
            delay: float
            try:
                await client.connect()
                self.connections += 1
                self._set_state(State.AUTHENTICATING)
                await client.authenticate()
                self._auth_hint_shown = False
                attempt = 0
                await self._poll_loop(client, stop)
                delay = 0.0
            except VtsAuthDenied as exc:
                if not self._auth_hint_shown:
                    log.warning("VTS の認証に失敗しました: %s", exc)
                    log.warning("VTube Studio でプラグイン「%s」を許可してください", self.settings.vts.plugin_name)
                    self._auth_hint_shown = True
                delay = self._auth_retry_sec
            except VtsError as exc:
                delay = backoff_delay(attempt, self._backoff)
                attempt += 1
                log.warning("VTS: %s(%g 秒後に再接続)", exc, delay)
            finally:
                await client.close()
            if delay and not stop.is_set():
                self._set_state(State.DISCONNECTED)
                await _wait(stop, delay)

    async def _poll_loop(self, client: VtsClient, stop: asyncio.Event) -> None:
        interval = 1.0 / self.settings.vts.poll_hz
        stale = self.settings.mapping.stale_timeout_sec
        next_tick = self._clock()
        while not stop.is_set():
            try:
                snapshot = await asyncio.wait_for(client.get_parameters(), timeout=stale)
            except TimeoutError as exc:
                raise StaleError(f"{stale:g} 秒間応答がありません") from exc
            self.vts_frames += 1
            if snapshot.model_loaded:
                self._set_state(State.STREAMING)
                self.sender.send_floats(self.mapper.update(snapshot.params))
                await self._update_hands(client, stale)
            else:
                self._set_state(State.NO_MODEL)
            next_tick += interval
            now = self._clock()
            if next_tick < now:  # 遅れたら追いつこうとせず現在から数え直す
                next_tick = now
            await _wait(stop, next_tick - now)

    async def _update_hands(self, client: VtsClient, stale: float) -> None:
        """手のトラッキング(SPEC §4.9)。VTS が入力値の一覧に対応していなければ、以後は読まない。"""
        if self.hands is None or self._hands_unavailable:
            return
        try:
            inputs = await asyncio.wait_for(client.get_input_parameters(), timeout=stale)
        except TimeoutError as exc:
            raise StaleError(f"{stale:g} 秒間応答がありません") from exc
        except VtsApiError as exc:
            self._hands_unavailable = True
            log.warning("VTS の入力値を読めないため、手のトラッキングを使いません: %s", exc)
            return
        floats, bools = self.hands.update(inputs)
        self.sender.send_floats(floats)
        self._send_bools(bools)

    async def _resend_loop(self, stop: asyncio.Event) -> None:
        interval = self.settings.mapping.resend_interval_sec
        while not stop.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._resend_now.wait(), timeout=interval)
            self._resend_now.clear()
            self._resend()

    async def _stats_loop(self, stop: asyncio.Event) -> None:
        interval = self.settings.logging.stats_interval_sec
        last_t, last_frames, last_sent = self._clock(), self.vts_frames, self.sender.sent_count
        while not stop.is_set():
            await _wait(stop, interval)
            now = self._clock()
            elapsed = max(now - last_t, 1e-6)
            log.info(
                "状態=%s VTS受信 %.1f Hz / OSC送信 %d 件",
                self.state.value,
                (self.vts_frames - last_frames) / elapsed,
                self.sender.sent_count - last_sent,
            )
            last_t, last_frames, last_sent = now, self.vts_frames, self.sender.sent_count


async def _wait(stop: asyncio.Event, timeout: float) -> None:
    """stop が立つか timeout 秒経つまで待つ。"""
    if timeout <= 0:
        await asyncio.sleep(0)
        return
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(stop.wait(), timeout=timeout)
