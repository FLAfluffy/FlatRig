from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import pytest

from flatrig_bridge.config import ParamDef
from flatrig_bridge.mapping import (
    ParamMapper,
    effective_range,
    ema,
    normalize,
    quantize,
    should_send,
)

STEP = 1 / 127
DEADBAND = 0.00787


@dataclass
class Raw:
    value: float
    min: float | None = None
    max: float | None = None


def pdef(avatar="FR_AngleX", vts="ParamAngleX", mode="signed", lo=-30.0, hi=30.0, phase=1) -> ParamDef:
    return ParamDef(avatar, vts, mode, lo, hi, True, 8, phase)


# --- normalize ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("x", "expected"),
    [(-30, -1.0), (0, 0.0), (15, 0.5), (30, 1.0), (45, 1.0), (-100, -1.0)],
)
def test_normalize_signed(x, expected):
    assert normalize(x, "signed", -30, 30) == pytest.approx(expected)


def test_normalize_signed_asymmetric_range():
    # 中心 5、半幅 15
    assert normalize(5, "signed", -10, 20) == pytest.approx(0.0)
    assert normalize(20, "signed", -10, 20) == pytest.approx(1.0)


@pytest.mark.parametrize(("x", "expected"), [(0, 0.0), (0.25, 0.25), (1, 1.0), (1.7, 1.0), (-0.2, 0.0)])
def test_normalize_unsigned(x, expected):
    assert normalize(x, "unsigned", 0, 1) == pytest.approx(expected)


@pytest.mark.parametrize("mode", ["signed", "unsigned"])
def test_normalize_zero_width_range(mode):
    assert normalize(3.0, mode, 1.0, 1.0) == 0.0
    assert normalize(3.0, mode, 2.0, 1.0) == 0.0


@pytest.mark.parametrize("x", [math.nan, math.inf, -math.inf])
def test_normalize_non_finite(x):
    assert normalize(x, "signed", -1, 1) is None


def test_normalize_unknown_mode():
    with pytest.raises(ValueError):
        normalize(0, "linear", 0, 1)


# --- 範囲の優先順位 -----------------------------------------------------------


def test_effective_range_prefers_vts():
    assert effective_range(pdef(), -45.0, 45.0) == (-45.0, 45.0)


@pytest.mark.parametrize(("lo", "hi"), [(None, None), (None, 30.0), (10.0, 10.0), (20.0, -20.0)])
def test_effective_range_falls_back_to_src(lo, hi):
    assert effective_range(pdef(), lo, hi) == (-30.0, 30.0)


# --- EMA・量子化・デッドバンド -------------------------------------------------


def test_ema():
    assert ema(None, 0.8, 0.6) == 0.8
    assert ema(0.0, 1.0, 0.6) == pytest.approx(0.6)
    assert ema(0.5, 1.0, 1.0) == 1.0  # α=1 で平滑化なし


def test_quantize():
    assert quantize(0.0) == 0.0
    assert quantize(1.0) == 1.0
    assert quantize(-1.0) == -1.0
    assert quantize(0.5) == pytest.approx(64 / 127)
    assert quantize(STEP * 0.4) == 0.0
    assert quantize(STEP * 0.6) == pytest.approx(STEP)


def test_should_send():
    assert should_send(0.0, None, DEADBAND)
    assert not should_send(0.0, 0.0, DEADBAND)
    assert should_send(STEP, 0.0, DEADBAND)  # 1 刻みの変化は送る
    assert not should_send(0.005, 0.0, DEADBAND)


# --- ParamMapper --------------------------------------------------------------


def mapper(*params: ParamDef, alpha=1.0, quantize=True) -> ParamMapper:
    return ParamMapper(params or (pdef(),), ema_alpha=alpha, quantize=quantize, deadband=DEADBAND)


def test_mapper_first_update_sends_and_repeat_does_not():
    m = mapper()
    assert m.update({"ParamAngleX": Raw(15.0, -30, 30)}) == {"FR_AngleX": pytest.approx(quantize(0.5))}
    assert m.update({"ParamAngleX": Raw(15.0, -30, 30)}) == {}
    assert m.update({"ParamAngleX": Raw(15.1, -30, 30)}) == {}  # 1 刻み未満
    assert "FR_AngleX" in m.update({"ParamAngleX": Raw(16.0, -30, 30)})


def test_mapper_values_are_on_quantized_grid():
    m = mapper()
    v = m.update({"ParamAngleX": Raw(7.3, -30, 30)})["FR_AngleX"]
    assert round(v * 127) == pytest.approx(v * 127)


def test_mapper_without_quantize():
    m = mapper(quantize=False)
    assert m.update({"ParamAngleX": Raw(7.3, -30, 30)})["FR_AngleX"] == pytest.approx(7.3 / 30)


def test_mapper_uses_src_range_when_vts_has_none():
    m = mapper()
    assert m.update({"ParamAngleX": Raw(30.0)})["FR_AngleX"] == 1.0


def test_mapper_ema_smooths_step():
    m = mapper(alpha=0.5, quantize=False)
    m.update({"ParamAngleX": Raw(0.0, -30, 30)})
    assert m.update({"ParamAngleX": Raw(30.0, -30, 30)})["FR_AngleX"] == pytest.approx(0.5)
    assert m.update({"ParamAngleX": Raw(30.0, -30, 30)})["FR_AngleX"] == pytest.approx(0.75)


def test_mapper_skips_missing_and_non_finite():
    eye = pdef("FR_EyeLOpen", "ParamEyeLOpen", "unsigned", 0, 1)
    m = mapper(pdef(), eye)
    assert m.update({"ParamEyeLOpen": Raw(0.5, 0, 1)}) == {"FR_EyeLOpen": pytest.approx(quantize(0.5))}
    assert m.update({"ParamAngleX": Raw(math.nan, -30, 30)}) == {}
    assert m.current_values() == {"FR_EyeLOpen": pytest.approx(quantize(0.5))}


def test_mapper_reset_forces_resend():
    m = mapper()
    m.update({"ParamAngleX": Raw(15.0, -30, 30)})
    m.reset()
    assert m.current_values() == {}
    assert "FR_AngleX" in m.update({"ParamAngleX": Raw(15.0, -30, 30)})


def test_mapper_warns_range_mismatch_once(caplog: pytest.LogCaptureFixture):
    caplog.set_level(logging.WARNING)
    m = mapper()
    m.update({"ParamAngleX": Raw(10.0, -90, 90)})
    m.update({"ParamAngleX": Raw(20.0, -90, 90)})
    warnings = [r for r in caplog.records if "大きく異なります" in r.getMessage()]
    assert len(warnings) == 1


def test_mapper_uses_alias_when_primary_missing():
    mouth = ParamDef("FR_MouthOpenY", "ParamMouthOpenY", "unsigned", 0.0, 1.0, True, 8, 1, ("ParamMouthOpen",))
    m = mapper(mouth)
    assert m.update({"ParamMouthOpen": Raw(0.5, 0, 1)}) == {"FR_MouthOpenY": pytest.approx(quantize(0.5))}
    # 本名があれば本名を優先
    assert m.update({"ParamMouthOpen": Raw(0.5, 0, 1), "ParamMouthOpenY": Raw(1.0, 0, 1)}) == {"FR_MouthOpenY": 1.0}
