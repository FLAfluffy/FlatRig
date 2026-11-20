"""CLI(docs/SPEC.md §4.5)。"""

from __future__ import annotations

import argparse
import asyncio
import atexit
import json
import logging
import signal
import sys
from datetime import UTC, datetime
from pathlib import Path

from . import __version__
from .app import Bridge
from .config import (
    SYNC_BIT_LIMIT,
    ConfigError,
    ParamsDoc,
    Settings,
    bit_budget,
    find_settings,
    load_params,
    load_settings,
)
from .mapping import range_mismatch
from .osc_in import OscListener
from .osc_out import OscSender
from .selftest import PATTERNS, run_selftest
from .vts_client import (
    ParameterSnapshot,
    VtsApiError,
    VtsAuthDenied,
    VtsClient,
    VtsConnectionError,
    VtsTimeout,
)

DUMP_FORMAT = "flatrig-vts-dump/1"


def _load(args: argparse.Namespace) -> tuple[Settings, ParamsDoc] | None:
    try:
        settings_path = Path(args.settings) if args.settings else find_settings()
        settings = load_settings(settings_path)
        params = load_params(settings.mapping.params_file)
    except ConfigError as exc:
        print("設定エラー:")
        for err in exc.errors:
            print(f"  - {err}")
        return None
    return settings, params


def _setup_logging(settings: Settings) -> None:
    logging.basicConfig(level=settings.logging.level, format="%(asctime)s %(levelname)s %(message)s")


def _cmd_check_config(args: argparse.Namespace) -> int:
    loaded = _load(args)
    if loaded is None:
        return 1
    settings, params = loaded

    max_phase = settings.mapping.max_phase
    phase1 = bit_budget(params, max_phase=1)
    total = bit_budget(params, max_phase=None)
    active = bit_budget(params, max_phase=max_phase)

    def line(label: str, b) -> str:
        ints = f" + int {b.int_count} × 8" if b.int_count else ""
        return f"  {label}: {b.bits:>3} bit (float {b.float_count} × 8{ints} + bool {b.bool_count} × 1)"

    print(f"設定ファイル  : {settings.path}")
    print(f"パラメータ定義: {params.path}")
    print(
        f"VTS 接続先    : ws://{settings.vts.host}:{settings.vts.port}  "
        f"(プラグイン名 {settings.vts.plugin_name!r} / 開発者 {settings.vts.plugin_developer!r})"
    )
    print(f"OSC 送信先    : {settings.osc.host}:{settings.osc.port}  (ローカルのみ: OK)")
    listen = f"{settings.osc.listen_port}" if settings.osc.listen_enabled else "無効"
    print(f"OSC 受信      : {listen}")
    print("同期ビット予算:")
    print(line("Phase 1 時点        ", phase1))
    print(line("全体                ", total))
    print(line(f"送信対象(max_phase={max_phase})", active))
    print(f"  上限: {SYNC_BIT_LIMIT} bit / 残り: {SYNC_BIT_LIMIT - total.bits} bit")
    print("検証: OK")
    return 0


async def _fetch_snapshot(settings: Settings) -> ParameterSnapshot:
    async with VtsClient(settings.vts) as client:
        await client.authenticate()
        return await client.get_parameters()


def _dump_document(snapshot: ParameterSnapshot) -> dict:
    """保存するのはパラメータ名と数値だけ。モデル名・ID 等は含めない(SPEC §4.5)。"""
    return {
        "format": DUMP_FORMAT,
        "captured_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "parameter_count": len(snapshot.params),
        "parameters": [
            {"name": p.name, "value": p.value, "min": p.min, "max": p.max, "defaultValue": p.default}
            for p in sorted(snapshot.params.values(), key=lambda p: p.name)
        ],
    }


def _print_mapping_report(snapshot: ParameterSnapshot, params: ParamsDoc) -> int:
    """params.json の各 FR_* が VTS 側に見つかるか、範囲が食い違っていないかを表示する。"""
    missing = 0
    print("params.json との対応:")
    for p in params.params:
        match = p.find(snapshot.params)
        if match is None:
            missing += 1
            print(f"  [なし] {p.avatar:<15} ← {' / '.join(p.vts_names)}")
            continue
        vts_name, found = match
        if found.min is None or found.max is None:
            note = "範囲なし(params.json の src_min/src_max を使用)"
        elif range_mismatch(p.src_min, p.src_max, found.min, found.max):
            note = f"範囲が食い違い: VTS {found.min:g}〜{found.max:g} / params {p.src_min:g}〜{p.src_max:g}"
        else:
            note = f"範囲 {found.min:g}〜{found.max:g}"
        if vts_name != p.vts:
            note += "(別名で一致)"
        print(f"  [あり] {p.avatar:<15} ← {vts_name:<17} {note}")
    return missing


def _cmd_dump_params(args: argparse.Namespace) -> int:
    loaded = _load(args)
    if loaded is None:
        return 1
    settings, params = loaded
    _setup_logging(settings)

    try:
        snapshot = asyncio.run(_fetch_snapshot(settings))
    except VtsConnectionError as exc:
        print(f"VTS に接続できません: {exc}")
        print("VTube Studio が起動していて、設定の「APIの起動(プラグインを許可)」が ON か、")
        print("ポート番号が settings の vts.port と一致しているか確認してください。")
        return 1
    except VtsAuthDenied as exc:
        print(f"VTS の許可が得られませんでした: {exc}")
        print("VTube Studio の許可画面で「Allow」を押してから、もう一度実行してください。")
        return 1
    except (VtsTimeout, VtsApiError) as exc:
        print(f"VTS との通信に失敗しました: {exc}")
        return 1

    if not snapshot.model_loaded:
        print("VTube Studio にモデルが読み込まれていません。モデルを読み込んでから実行してください。")
        return 1

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(_dump_document(snapshot), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{len(snapshot.params)} 件のパラメータを保存しました: {out}")
    missing = _print_mapping_report(snapshot, params)
    if missing:
        print(f"注意: {missing} 件が VTS 側に見つかりません(読み込んだモデルに無いパラメータです)。")
    return 0


def _cmd_selftest(args: argparse.Namespace) -> int:
    loaded = _load(args)
    if loaded is None:
        return 1
    settings, params = loaded
    if args.rate <= 0 or args.duration < 0:
        print("--rate は 0 より大きく、--duration は 0 以上にしてください。")
        return 2
    targets = params.params_up_to(settings.mapping.max_phase)
    sender = OscSender.from_settings(settings.osc)
    print(
        f"selftest: {args.pattern} を {args.duration:g} 秒、{len(targets)} パラメータへ送信します "
        f"→ {settings.osc.host}:{settings.osc.port}(Ctrl+C で中断)"
    )
    try:
        frames = run_selftest(sender, targets, pattern=args.pattern, duration=args.duration, rate_hz=args.rate)
    except KeyboardInterrupt:
        print("中断しました(FR_Active=false と全値 0 を送信済み)")
        return 130
    print(f"完了: {frames} フレーム / OSC {sender.sent_count} メッセージ(終了時に FR_Active=false と全値 0 を送信)")
    return 0


def _install_stop_handlers(stop: asyncio.Event) -> None:
    """SIGINT/SIGTERM で stop を立てる。Windows では add_signal_handler が無いので Ctrl+C は例外で抜ける。"""
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError, ValueError):
            pass


async def _run_bridge(bridge: Bridge) -> None:
    stop = asyncio.Event()
    _install_stop_handlers(stop)
    await bridge.run(stop)


def _cmd_run(args: argparse.Namespace) -> int:
    loaded = _load(args)
    if loaded is None:
        return 1
    settings, params = loaded
    _setup_logging(settings)
    bridge = Bridge(settings, params)
    atexit.register(bridge.send_shutdown)  # 例外で抜けた場合の保険(送信は 1 回だけ)
    logging.getLogger(__name__).info(
        "FlatRig Bridge を開始します: VTS ws://%s:%d → OSC %s:%d(Ctrl+C で終了)",
        settings.vts.host,
        settings.vts.port,
        settings.osc.host,
        settings.osc.port,
    )
    try:
        asyncio.run(_run_bridge(bridge))
    except KeyboardInterrupt:
        pass
    finally:
        bridge.send_shutdown()
    return 0


async def _listen(port: int, duration: float | None) -> bool:
    def show(address: str, values: tuple) -> None:
        print(f"{address} {' '.join(repr(v) for v in values)}", flush=True)

    listener = OscListener(port, on_message=show)
    if not await listener.start():
        return False
    stop = asyncio.Event()
    _install_stop_handlers(stop)
    try:
        if duration is None:
            await stop.wait()
        else:
            try:
                await asyncio.wait_for(stop.wait(), timeout=duration)
            except TimeoutError:
                pass
    finally:
        listener.stop()
    return True


def _cmd_osc_listen(args: argparse.Namespace) -> int:
    loaded = _load(args)
    if loaded is None:
        return 1
    settings, _ = loaded
    _setup_logging(settings)
    port = args.port or settings.osc.listen_port
    print(f"OSC 受信内容を表示します: 127.0.0.1:{port}(Ctrl+C で終了)", flush=True)
    try:
        ok = asyncio.run(_listen(port, args.duration))
    except KeyboardInterrupt:
        ok = True
    return 0 if ok else 1


def _cmd_gui(args: argparse.Namespace) -> int:
    from .gui import run_gui  # tkinter は画面を使うときだけ読み込む

    return run_gui(args.settings, args.wait_vrchat, args.launch_vrchat)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="flatrig_bridge",
        description="FlatRig Bridge: VTube Studio のパラメータ値を VRChat の OSC へ中継します。",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    settings_help = "settings ファイルのパス(省略時は config/settings.json → .example.json)"

    p = sub.add_parser("check-config", help="settings/params の検証とビット予算の表示")
    p.add_argument("--settings", help=settings_help)
    p.set_defaults(func=_cmd_check_config)

    p = sub.add_parser("dump-params", help="VTS からパラメータ一覧(名前・値・範囲)を取得して保存")
    p.add_argument("--settings", help=settings_help)
    p.add_argument("--out", default="vts_params_dump.json", help="保存先(既定: vts_params_dump.json)")
    p.set_defaults(func=_cmd_dump_params)

    p = sub.add_parser("selftest", help="VTS なしで全 FR_* に試験波形を OSC 送信(Unity/VRChat 側の確認用)")
    p.add_argument("--settings", help=settings_help)
    p.add_argument("--pattern", choices=PATTERNS, default="sine", help="波形(既定: sine)")
    p.add_argument("--duration", type=float, default=10.0, help="送信する秒数(既定: 10)")
    p.add_argument("--rate", type=float, default=30.0, help="送信レート Hz(既定: 30)")
    p.set_defaults(func=_cmd_selftest)

    p = sub.add_parser("run", help="常駐して VTS → OSC を中継する")
    p.add_argument("--settings", help=settings_help)
    p.set_defaults(func=_cmd_run)

    p = sub.add_parser("gui", help="ウィンドウとタスクトレイで常駐して中継する(配布版の既定)")
    p.add_argument("--settings", help=settings_help)
    p.add_argument("--wait-vrchat", action="store_true", help="VRChat の起動中だけ中継する(Windows 起動時の待機用)")
    p.add_argument("--launch-vrchat", action="store_true", help="Steam 経由で VRChat を起動してから中継する")
    p.set_defaults(func=_cmd_gui)

    p = sub.add_parser("osc-listen", help="VRChat からの OSC 受信内容を表示(デバッグ用)")
    p.add_argument("--settings", help=settings_help)
    p.add_argument("--port", type=int, help="受信ポート(既定: settings の osc.listen_port)")
    p.add_argument("--duration", type=float, help="指定秒数で終了(既定: Ctrl+C まで)")
    p.set_defaults(func=_cmd_osc_listen)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
