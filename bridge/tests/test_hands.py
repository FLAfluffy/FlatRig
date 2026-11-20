"""手のトラッキング(SPEC §4.9)。"""

from __future__ import annotations

import pytest

from flatrig_bridge.config import HandsSettings
from flatrig_bridge.hands import HandTracker, vts_prefix
from flatrig_bridge.mapping import quantize


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def make(**kw) -> tuple[HandTracker, Clock]:
    clock = Clock()
    settings = HandsSettings(**kw)
    return HandTracker(settings, ema_alpha=1.0, quantize=True, deadband=0.00787, clock=clock), clock


def test_prefix_and_swap():
    assert vts_prefix("R", False) == "HandRight"
    assert vts_prefix("L", False) == "HandLeft"
    assert vts_prefix("R", True) == "HandLeft"


def test_position_normalized_and_found():
    t, _ = make()
    floats, bools = t.update({"HandRightFound": 1.0, "HandRightPositionX": 10.0, "HandRightPositionY": -5.0})
    assert floats["FR_HandRX"] == pytest.approx(1.0)
    assert floats["FR_HandRY"] == pytest.approx(quantize(-0.5))
    assert bools == {"FR_HandRFound": True, "FR_HandLFound": False}
    floats, bools = t.update({"HandRightFound": 1.0, "HandRightPositionX": 10.0, "HandRightPositionY": -5.0})
    assert floats == {} and bools == {}  # 変わらなければ送らない


def test_out_of_range_is_clamped():
    t, _ = make()
    floats, _ = t.update({"HandLeftFound": 1.0, "HandLeftPositionX": -25.0, "HandLeftPositionY": 3.0})
    assert floats["FR_HandLX"] == pytest.approx(-1.0)


def test_hold_after_lost():
    t, clock = make(hold_sec=0.4)
    t.update({"HandRightFound": 1.0, "HandRightPositionX": 2.0, "HandRightPositionY": 0.0})
    clock.t = 0.3
    floats, bools = t.update({"HandRightFound": 0.0, "HandRightPositionX": 9.0, "HandRightPositionY": 9.0})
    assert floats == {} and bools == {}  # 見失っている間は位置を保ち、hold_sec の間は見つかったまま
    clock.t = 0.5
    _, bools = t.update({"HandRightFound": 0.0})
    assert bools == {"FR_HandRFound": False}


def test_swap_uses_other_hand():
    t, _ = make(swap=True)
    floats, bools = t.update({"HandLeftFound": 1.0, "HandLeftPositionX": 5.0, "HandLeftPositionY": 0.0})
    assert floats["FR_HandRX"] == pytest.approx(quantize(0.5))
    assert bools["FR_HandRFound"] is True


def test_current_values_and_reset():
    t, _ = make()
    t.update({"HandRightFound": 1.0, "HandRightPositionX": 5.0, "HandRightPositionY": 0.0})
    floats, bools = t.current_values()
    assert floats["FR_HandRX"] == pytest.approx(quantize(0.5))
    assert bools["FR_HandRFound"] is True
    t.reset()
    assert t.current_values() == ({}, {})
