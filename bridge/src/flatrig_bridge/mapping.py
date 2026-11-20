"""パラメータ値の正規化・平滑化・量子化(docs/SPEC.md §3.2, §3.4)。

処理順: VTS の生値 → 正規化(signed −1〜1 / unsigned 0〜1)→ EMA → 量子化 → デッドバンド判定。
関数は副作用なし。状態(EMA の前回値・最後に送った値)は ParamMapper が持つ。
"""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from .config import ParamDef

log = logging.getLogger(__name__)

RANGE_WIDTH_RATIO = 2.0  # 幅が 2 倍以上/半分以下で食い違いとみなす
RANGE_CENTER_SHIFT = 0.25  # 中心のずれが src 幅の 25% 以上で食い違いとみなす
QUANT_STEPS = 127  # VRChat の同期 float(8bit)の刻み
_EPS = 1e-9


class RawParam(Protocol):
    """VTS から受け取った 1 パラメータ(vts_client.VtsParam と同じ形)。"""

    value: float
    min: float | None
    max: float | None


def range_mismatch(src_min: float, src_max: float, vts_min: float, vts_max: float) -> bool:
    """params.json の範囲と VTS が返した範囲が「大きく」食い違うか(SPEC §3.2)。"""
    src_width = src_max - src_min
    vts_width = vts_max - vts_min
    if src_width <= 0 or vts_width <= 0:
        return True
    ratio = vts_width / src_width
    if ratio >= RANGE_WIDTH_RATIO or ratio <= 1 / RANGE_WIDTH_RATIO:
        return True
    shift = abs((vts_min + vts_max) / 2 - (src_min + src_max) / 2)
    return shift >= RANGE_CENTER_SHIFT * src_width


def effective_range(param: ParamDef, vts_min: float | None, vts_max: float | None) -> tuple[float, float]:
    """VTS が有効な範囲を返していればそれを優先し、無ければ params の src_min/src_max。"""
    if vts_min is not None and vts_max is not None and vts_max > vts_min:
        return vts_min, vts_max
    return param.src_min, param.src_max


def normalize(x: float, mode: str, lo: float, hi: float) -> float | None:
    """signed は −1〜1、unsigned は 0〜1 に正規化してクランプする。非有限値は None。"""
    if not math.isfinite(x):
        return None
    width = hi - lo
    if mode == "signed":
        if width <= 0:
            return 0.0
        v = (x - (lo + hi) / 2) / (width / 2)
        return max(-1.0, min(1.0, v))
    if mode == "unsigned":
        if width <= 0:
            return 0.0
        v = (x - lo) / width
        return max(0.0, min(1.0, v))
    raise ValueError(f"unknown mode: {mode}")


def ema(prev: float | None, x: float, alpha: float) -> float:
    """y = α·x + (1−α)·prev。α は新しい値の重み(SPEC §3.4)。"""
    if prev is None:
        return x
    return alpha * x + (1 - alpha) * prev


def quantize(v: float) -> float:
    return round(v * QUANT_STEPS) / QUANT_STEPS


def should_send(value: float, last_sent: float | None, deadband: float) -> bool:
    if last_sent is None:
        return True
    return abs(value - last_sent) >= deadband - _EPS


@dataclass
class _State:
    smoothed: float | None = None
    last_sent: float | None = None
    warned_range: bool = False


class ParamMapper:
    """VTS のスナップショットから、今回送るべき FR_* の値を求める。"""

    def __init__(
        self,
        params: tuple[ParamDef, ...],
        *,
        ema_alpha: float,
        quantize: bool,
        deadband: float,
    ):
        self.params = params
        self.ema_alpha = ema_alpha
        self.quantize = quantize
        self.deadband = deadband
        self._state = {p.avatar: _State() for p in params}

    def reset(self) -> None:
        """再接続時などに平滑化と送信履歴を捨てる。"""
        for state in self._state.values():
            state.smoothed = None
            state.last_sent = None

    def update(self, raw: Mapping[str, RawParam]) -> dict[str, float]:
        """送信が必要な値だけを返す(VTS に無いパラメータは飛ばす)。"""
        out: dict[str, float] = {}
        for p in self.params:
            found = p.find(raw)
            if found is None:
                continue
            vts_name, item = found
            state = self._state[p.avatar]
            self._warn_range_once(p, vts_name, item, state)
            lo, hi = effective_range(p, item.min, item.max)
            v = normalize(item.value, p.mode, lo, hi)
            if v is None:
                continue
            state.smoothed = ema(state.smoothed, v, self.ema_alpha)
            value = quantize(state.smoothed) if self.quantize else state.smoothed
            if should_send(value, state.last_sent, self.deadband):
                state.last_sent = value
                out[p.avatar] = value
        return out

    def current_values(self) -> dict[str, float]:
        """再送用。一度も送っていないパラメータは含めない。"""
        return {name: s.last_sent for name, s in self._state.items() if s.last_sent is not None}

    @staticmethod
    def _warn_range_once(p: ParamDef, vts_name: str, item: RawParam, state: _State) -> None:
        if state.warned_range or item.min is None or item.max is None:
            return
        if range_mismatch(p.src_min, p.src_max, item.min, item.max):
            state.warned_range = True
            log.warning(
                "%s: VTS の範囲 %g〜%g が params の %g〜%g と大きく異なります(VTS の値を使用)",
                vts_name,
                item.min,
                item.max,
                p.src_min,
                p.src_max,
            )
