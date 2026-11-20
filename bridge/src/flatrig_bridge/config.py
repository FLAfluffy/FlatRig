"""settings / params の読込と検証(docs/SPEC.md §3, §4.1.1, §4.4)。

jsonschema は使わず手書きで検証する。エラーはまとめて ConfigError で返す。
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

T = TypeVar("T")

ALLOWED_OSC_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})  # G7: ローカル以外へは送らない
SYNC_BIT_LIMIT = 256
PARAM_MODES = frozenset({"signed", "unsigned"})
FLOAT_BITS = 8
BOOL_BITS = 1
INT_BITS = 8
FLAG_BITS = {"bool": BOOL_BITS, "int": INT_BITS}  # flags の型ごとの同期ビット数
PLUGIN_TEXT_MIN, PLUGIN_TEXT_MAX = 3, 32

SETTINGS_SEARCH = (
    Path("config/settings.json"),
    Path("config/settings.example.json"),
    Path("bridge/config/settings.json"),
    Path("bridge/config/settings.example.json"),
)


class ConfigError(Exception):
    """設定の検証エラー。errors に全件を持つ。"""

    def __init__(self, errors: list[str]):
        self.errors = list(errors)
        super().__init__("\n".join(self.errors))


@dataclass(frozen=True)
class VtsSettings:
    host: str
    port: int
    plugin_name: str
    plugin_developer: str
    token_file: Path
    source: str
    poll_hz: float
    request_timeout_sec: float


@dataclass(frozen=True)
class OscSettings:
    host: str
    port: int
    listen_enabled: bool
    listen_port: int
    address_prefix: str


@dataclass(frozen=True)
class MappingSettings:
    params_file: Path
    max_phase: int
    quantize: bool
    deadband: float
    resend_interval_sec: float
    ema_alpha: float
    stale_timeout_sec: float


@dataclass(frozen=True)
class LoggingSettings:
    level: str
    stats_interval_sec: float


@dataclass(frozen=True)
class HandsSettings:
    """VTube Studio の手のトラッキングを腕へ送る(SPEC §4.9)。settings の hands セクションは省略できる。"""

    enabled: bool = True
    swap: bool = False  # VTS の左右の手を入れ替える(既定: VTS の HandRight → キャラクターの右手)
    hold_sec: float = 0.4  # 手を見失ってから「見つからない」にするまでの時間(ちらつき防止)


@dataclass(frozen=True)
class Settings:
    path: Path
    vts: VtsSettings
    osc: OscSettings
    mapping: MappingSettings
    logging: LoggingSettings
    hands: HandsSettings = HandsSettings()


@dataclass(frozen=True)
class ParamDef:
    avatar: str
    vts: str
    mode: str
    src_min: float
    src_max: float
    synced: bool
    bits: int
    phase: int
    vts_aliases: tuple[str, ...] = ()

    @property
    def vts_names(self) -> tuple[str, ...]:
        """探す順の VTS パラメータ名(本名 → 別名)。モデルによって ID が違うため(SPEC §3.1)。"""
        return (self.vts, *self.vts_aliases)

    def find(self, raw: Mapping[str, T]) -> tuple[str, T] | None:
        """raw の中で最初に見つかった名前とその値。"""
        for name in self.vts_names:
            item = raw.get(name)
            if item is not None:
                return name, item
        return None


@dataclass(frozen=True)
class FlagDef:
    avatar: str
    sent_by: str
    synced: bool
    bits: int
    type: str = "bool"  # "bool" / "int"(表情の番号など。ブリッジは送らない)


@dataclass(frozen=True)
class ParamsDoc:
    path: Path
    prefix: str
    params: tuple[ParamDef, ...]
    flags: tuple[FlagDef, ...]

    def params_up_to(self, max_phase: int) -> tuple[ParamDef, ...]:
        return tuple(p for p in self.params if p.phase <= max_phase)


@dataclass(frozen=True)
class BitBudget:
    float_count: int
    bool_count: int
    bits: int
    int_count: int = 0

    @property
    def ok(self) -> bool:
        return self.bits <= SYNC_BIT_LIMIT


@dataclass
class _Reader:
    """dict から型付きで値を取り出し、エラーを溜める。"""

    errors: list[str] = field(default_factory=list)

    def get(self, obj: dict[str, Any], key: str, kind: type | tuple[type, ...], where: str) -> Any:
        if key not in obj:
            self.errors.append(f"{where}.{key}: 必須項目がありません")
            return None
        value = obj[key]
        # bool は int のサブクラスなので、数値項目に bool が来たら拒否する
        if isinstance(value, bool) and bool not in _as_tuple(kind):
            self.errors.append(f"{where}.{key}: 型が不正です({value!r})")
            return None
        if not isinstance(value, kind):
            self.errors.append(f"{where}.{key}: 型が不正です({value!r})")
            return None
        if isinstance(value, float) and not math.isfinite(value):
            self.errors.append(f"{where}.{key}: 有限の数値が必要です")
            return None
        return value

    def section(self, obj: dict[str, Any], key: str) -> dict[str, Any]:
        value = obj.get(key)
        if not isinstance(value, dict):
            self.errors.append(f"{key}: セクションがありません")
            return {}
        return value


def _as_tuple(kind: type | tuple[type, ...]) -> tuple[type, ...]:
    return kind if isinstance(kind, tuple) else (kind,)


_NUM = (int, float)


def _load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError([f"{path}: ファイルがありません"]) from exc
    except json.JSONDecodeError as exc:
        raise ConfigError([f"{path}: JSON として読めません({exc})"]) from exc
    if not isinstance(data, dict):
        raise ConfigError([f"{path}: 最上位はオブジェクトである必要があります"])
    return data


def find_settings(cwd: Path | None = None) -> Path:
    """--settings 省略時の既定パス(SPEC §4.1.1)。"""
    base = cwd or Path.cwd()
    for candidate in SETTINGS_SEARCH:
        path = base / candidate
        if path.is_file():
            return path
    raise ConfigError(["settings が見つかりません。--settings で指定してください"])


def _check_port(r: _Reader, value: Any, where: str) -> None:
    if isinstance(value, int) and not 1 <= value <= 65535:
        r.errors.append(f"{where}: ポート番号は 1〜65535 です({value})")


def _check_positive(r: _Reader, value: Any, where: str) -> None:
    if isinstance(value, _NUM) and value <= 0:
        r.errors.append(f"{where}: 0 より大きい値が必要です({value})")


def load_settings(path: Path) -> Settings:
    path = Path(path)
    data = _load_json(path)
    base = path.parent
    r = _Reader()

    v = r.section(data, "vts")
    vts_host = r.get(v, "host", str, "vts")
    vts_port = r.get(v, "port", int, "vts")
    plugin_name = r.get(v, "plugin_name", str, "vts")
    plugin_dev = r.get(v, "plugin_developer", str, "vts")
    token_file = r.get(v, "token_file", str, "vts")
    source = r.get(v, "source", str, "vts")
    poll_hz = r.get(v, "poll_hz", _NUM, "vts")
    req_timeout = r.get(v, "request_timeout_sec", _NUM, "vts")
    _check_port(r, vts_port, "vts.port")
    for key, text in (("plugin_name", plugin_name), ("plugin_developer", plugin_dev)):
        if isinstance(text, str) and not PLUGIN_TEXT_MIN <= len(text) <= PLUGIN_TEXT_MAX:
            r.errors.append(f"vts.{key}: {PLUGIN_TEXT_MIN}〜{PLUGIN_TEXT_MAX} 文字にしてください({len(text)} 文字)")
    if source is not None and source != "live2d":
        r.errors.append(f'vts.source: v1 は "live2d" のみ対応です({source!r})')
    _check_positive(r, poll_hz, "vts.poll_hz")
    _check_positive(r, req_timeout, "vts.request_timeout_sec")

    o = r.section(data, "osc")
    osc_host = r.get(o, "host", str, "osc")
    osc_port = r.get(o, "port", int, "osc")
    listen_enabled = r.get(o, "listen_enabled", bool, "osc")
    listen_port = r.get(o, "listen_port", int, "osc")
    prefix = r.get(o, "address_prefix", str, "osc")
    if isinstance(osc_host, str) and osc_host.strip().lower() not in ALLOWED_OSC_HOSTS:
        r.errors.append(f"osc.host: 送信先はローカル(127.0.0.1 / localhost / ::1)のみ許可されます({osc_host!r}) (G7)")
    _check_port(r, osc_port, "osc.port")
    _check_port(r, listen_port, "osc.listen_port")
    if listen_enabled and osc_port is not None and osc_port == listen_port:
        r.errors.append("osc.listen_port: 送信ポートと同じにはできません")
    if isinstance(prefix, str) and not (prefix.startswith("/") and prefix.endswith("/")):
        r.errors.append(f'osc.address_prefix: "/" で始まり "/" で終わる必要があります({prefix!r})')

    m = r.section(data, "mapping")
    params_file = r.get(m, "params_file", str, "mapping")
    max_phase = m.get("max_phase", 2)
    if isinstance(max_phase, bool) or not isinstance(max_phase, int) or max_phase < 1:
        r.errors.append(f"mapping.max_phase: 1 以上の整数が必要です({max_phase!r})")
    quantize = r.get(m, "quantize", bool, "mapping")
    deadband = r.get(m, "deadband", _NUM, "mapping")
    resend = r.get(m, "resend_interval_sec", _NUM, "mapping")
    ema_alpha = r.get(m, "ema_alpha", _NUM, "mapping")
    stale = r.get(m, "stale_timeout_sec", _NUM, "mapping")
    if isinstance(deadband, _NUM) and not 0 <= deadband < 1:
        r.errors.append(f"mapping.deadband: 0 以上 1 未満にしてください({deadband})")
    if isinstance(ema_alpha, _NUM) and not 0 < ema_alpha <= 1:
        r.errors.append(f"mapping.ema_alpha: 0 より大きく 1 以下にしてください({ema_alpha})")
    _check_positive(r, resend, "mapping.resend_interval_sec")
    _check_positive(r, stale, "mapping.stale_timeout_sec")

    lg = r.section(data, "logging")
    level = r.get(lg, "level", str, "logging")
    stats = r.get(lg, "stats_interval_sec", _NUM, "logging")
    if isinstance(level, str) and level.upper() not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
        r.errors.append(f"logging.level: DEBUG/INFO/WARNING/ERROR のいずれかです({level!r})")
    _check_positive(r, stats, "logging.stats_interval_sec")

    hands = HandsSettings()
    if "hands" in data:
        h = r.section(data, "hands")
        defaults = HandsSettings()
        h_enabled = h.get("enabled", defaults.enabled)
        h_swap = h.get("swap", defaults.swap)
        h_hold = h.get("hold_sec", defaults.hold_sec)
        for key, value in (("enabled", h_enabled), ("swap", h_swap)):
            if not isinstance(value, bool):
                r.errors.append(f"hands.{key}: true / false が必要です({value!r})")
        if isinstance(h_hold, bool) or not isinstance(h_hold, _NUM) or not math.isfinite(h_hold) or h_hold < 0:
            r.errors.append(f"hands.hold_sec: 0 以上の数値が必要です({h_hold!r})")
        elif isinstance(h_enabled, bool) and isinstance(h_swap, bool):
            hands = HandsSettings(enabled=h_enabled, swap=h_swap, hold_sec=float(h_hold))

    if r.errors:
        raise ConfigError([f"{path}: {e}" for e in r.errors])

    return Settings(
        path=path,
        vts=VtsSettings(
            host=vts_host,
            port=vts_port,
            plugin_name=plugin_name,
            plugin_developer=plugin_dev,
            token_file=(base / token_file),
            source=source,
            poll_hz=float(poll_hz),
            request_timeout_sec=float(req_timeout),
        ),
        osc=OscSettings(
            host=osc_host,
            port=osc_port,
            listen_enabled=listen_enabled,
            listen_port=listen_port,
            address_prefix=prefix,
        ),
        mapping=MappingSettings(
            params_file=(base / params_file),
            max_phase=max_phase,
            quantize=quantize,
            deadband=float(deadband),
            resend_interval_sec=float(resend),
            ema_alpha=float(ema_alpha),
            stale_timeout_sec=float(stale),
        ),
        logging=LoggingSettings(level=level.upper(), stats_interval_sec=float(stats)),
        hands=hands,
    )


def load_params(path: Path) -> ParamsDoc:
    path = Path(path)
    data = _load_json(path)
    r = _Reader()

    prefix = r.get(data, "avatar_param_prefix", str, "params")
    raw_params = data.get("params")
    raw_flags = data.get("flags", [])
    if not isinstance(raw_params, list) or not raw_params:
        r.errors.append("params: 1 件以上のリストが必要です")
        raw_params = []
    if not isinstance(raw_flags, list):
        r.errors.append("flags: リストが必要です")
        raw_flags = []

    params: list[ParamDef] = []
    for i, item in enumerate(raw_params):
        where = f"params[{i}]"
        if not isinstance(item, dict):
            r.errors.append(f"{where}: オブジェクトが必要です")
            continue
        before = len(r.errors)
        avatar = r.get(item, "avatar", str, where)
        vts = r.get(item, "vts", str, where)
        mode = r.get(item, "mode", str, where)
        src_min = r.get(item, "src_min", _NUM, where)
        src_max = r.get(item, "src_max", _NUM, where)
        synced = r.get(item, "synced", bool, where)
        bits = r.get(item, "bits", int, where)
        phase = r.get(item, "phase", int, where)
        aliases = item.get("vts_aliases", [])
        if not isinstance(aliases, list) or not all(isinstance(a, str) and a for a in aliases):
            r.errors.append(f"{where}.vts_aliases: 文字列のリストが必要です({aliases!r})")
        if mode is not None and mode not in PARAM_MODES:
            r.errors.append(f"{where}.mode: signed / unsigned のいずれかです({mode!r})")
        if isinstance(src_min, _NUM) and isinstance(src_max, _NUM) and not src_min < src_max:
            r.errors.append(f"{where}: src_min < src_max である必要があります({src_min}, {src_max})")
        if synced and bits is not None and bits != FLOAT_BITS:
            r.errors.append(f"{where}.bits: 同期 float は {FLOAT_BITS} bit です({bits})")
        if phase is not None and phase < 1:
            r.errors.append(f"{where}.phase: 1 以上が必要です({phase})")
        if len(r.errors) == before:
            params.append(
                ParamDef(avatar, vts, mode, float(src_min), float(src_max), synced, bits, phase, tuple(aliases))
            )

    flags: list[FlagDef] = []
    for i, item in enumerate(raw_flags):
        where = f"flags[{i}]"
        if not isinstance(item, dict):
            r.errors.append(f"{where}: オブジェクトが必要です")
            continue
        before = len(r.errors)
        avatar = r.get(item, "avatar", str, where)
        kind = r.get(item, "type", str, where)
        sent_by = r.get(item, "sent_by", str, where)
        synced = r.get(item, "synced", bool, where)
        bits = r.get(item, "bits", int, where)
        if kind is not None and kind not in FLAG_BITS:
            r.errors.append(f"{where}.type: bool / int のいずれかです({kind!r})")
        elif kind is not None and synced and bits is not None and bits != FLAG_BITS[kind]:
            r.errors.append(f"{where}.bits: 同期 {kind} は {FLAG_BITS[kind]} bit です({bits})")
        if len(r.errors) == before:
            flags.append(FlagDef(avatar, sent_by, synced, bits, kind))

    names = [p.avatar for p in params] + [f.avatar for f in flags]
    for name in sorted({n for n in names if names.count(n) > 1}):
        r.errors.append(f"avatar 名が重複しています: {name}")
    vts_names = [n for p in params for n in set(p.vts_names)]
    for name in sorted({n for n in vts_names if vts_names.count(n) > 1}):
        r.errors.append(f"vts 名が重複しています: {name}")
    if isinstance(prefix, str):
        for name in names:
            if not name.startswith(prefix):
                r.errors.append(f"avatar 名が接頭辞 {prefix!r} で始まっていません: {name} (G6)")

    doc = ParamsDoc(path=path, prefix=prefix or "", params=tuple(params), flags=tuple(flags))
    total = bit_budget(doc, max_phase=None)
    if not total.ok:
        r.errors.append(f"同期ビットの合計が上限を超えています: {total.bits} > {SYNC_BIT_LIMIT} bit")

    if r.errors:
        raise ConfigError([f"{path}: {e}" for e in r.errors])
    return doc


def bit_budget(doc: ParamsDoc, max_phase: int | None) -> BitBudget:
    """同期ビット数。max_phase=None は全フェーズ。flags は常に含める(SPEC §3.3)。"""
    floats = [p for p in doc.params if p.synced and (max_phase is None or p.phase <= max_phase)]
    synced_flags = [f for f in doc.flags if f.synced]
    bits = sum(p.bits for p in floats) + sum(f.bits for f in synced_flags)
    bools = sum(1 for f in synced_flags if f.type == "bool")
    ints = sum(1 for f in synced_flags if f.type == "int")
    return BitBudget(float_count=len(floats), bool_count=bools, bits=bits, int_count=ints)
