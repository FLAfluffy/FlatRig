"""VTube Studio の手のトラッキングを、腕を動かす FR_Hand* に変える(docs/SPEC.md §4.9, §5.11)。

VTS の入力値(InputParameterListRequest)の HandRight/HandLeft の Found・PositionX・PositionY を読み、
キャラクターの右手(R)・左手(L)ごとに次を送る:
  FR_Hand{R,L}X  外側が +1(PositionX / 10。VTS は手ごとに外側を +10 にしている)
  FR_Hand{R,L}Y  上が +1(PositionY / 10)
  FR_Hand{R,L}Found  手が見つかっているか(見失っても hold_sec の間は true のまま。位置は最後の値を保つ)
位置は他のパラメータと同じく EMA・量子化・デッドバンドを通す。関数は時刻を clock から受け取る。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from .config import HandsSettings
from .mapping import ema, quantize, should_send

VTS_RANGE = 10.0
SIDES = ("R", "L")  # キャラクターの右手・左手


def vts_prefix(side: str, swap: bool) -> str:
    """キャラクターの side の手に使う VTS の名前の頭(既定: R ← HandRight)。"""
    right = (side == "R") != swap
    return "HandRight" if right else "HandLeft"


def x_name(side: str) -> str:
    return f"FR_Hand{side}X"


def y_name(side: str) -> str:
    return f"FR_Hand{side}Y"


def found_name(side: str) -> str:
    return f"FR_Hand{side}Found"


@dataclass
class _Side:
    smoothed: dict[str, float | None]
    last_sent: dict[str, float | None]
    last_seen: float | None = None
    found_sent: bool | None = None


class HandTracker:
    def __init__(
        self,
        settings: HandsSettings,
        *,
        ema_alpha: float,
        quantize: bool,
        deadband: float,
        clock: Callable[[], float],
    ):
        self.settings = settings
        self.ema_alpha = ema_alpha
        self.quantize = quantize
        self.deadband = deadband
        self._clock = clock
        self._sides: dict[str, _Side] = {}
        self.reset()

    def reset(self) -> None:
        """再接続時など。次の update で見つかっているかを送り直す。"""
        self._sides = {
            side: _Side(
                smoothed={x_name(side): None, y_name(side): None}, last_sent={x_name(side): None, y_name(side): None}
            )
            for side in SIDES
        }

    def update(self, raw: Mapping[str, float]) -> tuple[dict[str, float], dict[str, bool]]:
        """送る必要のある (float, bool) の値。"""
        now = self._clock()
        floats: dict[str, float] = {}
        bools: dict[str, bool] = {}
        for side, state in self._sides.items():
            prefix = vts_prefix(side, self.settings.swap)
            seen = raw.get(f"{prefix}Found", 0.0) > 0.5
            if seen:
                state.last_seen = now
                for name, key in ((x_name(side), "PositionX"), (y_name(side), "PositionY")):
                    value = raw.get(f"{prefix}{key}")
                    if value is None:
                        continue
                    v = max(-1.0, min(1.0, value / VTS_RANGE))
                    state.smoothed[name] = ema(state.smoothed[name], v, self.ema_alpha)
                    out = quantize(state.smoothed[name]) if self.quantize else state.smoothed[name]
                    if should_send(out, state.last_sent[name], self.deadband):
                        state.last_sent[name] = out
                        floats[name] = out
            found = state.last_seen is not None and now - state.last_seen <= self.settings.hold_sec
            if found != state.found_sent:
                state.found_sent = found
                bools[found_name(side)] = found
        return floats, bools

    def current_values(self) -> tuple[dict[str, float], dict[str, bool]]:
        """再送用。"""
        floats = {n: v for s in self._sides.values() for n, v in s.last_sent.items() if v is not None}
        bools = {found_name(side): bool(s.found_sent) for side, s in self._sides.items() if s.found_sent is not None}
        return floats, bools

    @staticmethod
    def lost_values() -> dict[str, bool]:
        """終了時・ブリッジが止まったときに送る(両手とも見つからない)。"""
        return {found_name(side): False for side in SIDES}
