"""VTS なしで全 FR_* に試験波形を送る(docs/SPEC.md §4.5 selftest)。

Unity / VRChat 側でアバターが各パラメータに追従するかを確かめるためのもの。
パラメータごとに位相をずらし、どれが動いているか見分けやすくする。
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable

from .config import ParamDef
from .osc_out import OscSender

PATTERNS = ("sine", "ramp", "zero")
ACTIVE_FLAG = "FR_Active"


def pattern_value(pattern: str, mode: str, t: float, index: int, period: float) -> float:
    """時刻 t における試験値。signed は −1〜1、unsigned は 0〜1。"""
    if pattern == "zero":
        return 0.0
    phase = (t / period + index / 8) % 1.0
    if pattern == "sine":
        s = math.sin(2 * math.pi * phase)
        return s if mode == "signed" else (s + 1) / 2
    if pattern == "ramp":
        return 2 * phase - 1 if mode == "signed" else phase
    raise ValueError(f"unknown pattern: {pattern}")


def run_selftest(
    sender: OscSender,
    params: tuple[ParamDef, ...],
    *,
    pattern: str,
    duration: float,
    rate_hz: float = 30.0,
    period: float = 4.0,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """duration 秒送信して送ったフレーム数を返す。終了時(中断時も)FR_Active=false と全値 0 を送る。"""
    frames = 0
    interval = 1.0 / rate_hz
    start = clock()
    try:
        sender.send_bool(ACTIVE_FLAG, True)
        while (elapsed := clock() - start) < duration:
            for i, p in enumerate(params):
                sender.send_float(p.avatar, pattern_value(pattern, p.mode, elapsed, i, period))
            frames += 1
            sleep(max(0.0, start + frames * interval - clock()))
    finally:
        sender.send_bool(ACTIVE_FLAG, False)
        sender.send_floats({p.avatar: 0.0 for p in params})
    return frames
