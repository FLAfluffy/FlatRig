"""常駐アプリの周辺: 画面設定の保存、起動コマンド、CLI の gui サブコマンド。"""

from __future__ import annotations

import sys
from pathlib import Path

from flatrig_bridge import winsys
from flatrig_bridge.__main__ import build_parser
from flatrig_bridge.prefs import UiPrefs


def test_prefs_defaults_when_missing(tmp_path: Path):
    prefs = UiPrefs.load(tmp_path / "ui.json")
    assert prefs == UiPrefs(always_on_top=False, exit_with_vrchat=True)


def test_prefs_roundtrip(tmp_path: Path):
    path = tmp_path / "ui.json"
    UiPrefs(always_on_top=True, exit_with_vrchat=False).save(path)
    assert UiPrefs.load(path) == UiPrefs(always_on_top=True, exit_with_vrchat=False)


def test_prefs_broken_file_falls_back(tmp_path: Path):
    path = tmp_path / "ui.json"
    path.write_text("{not json", encoding="utf-8")
    assert UiPrefs.load(path) == UiPrefs()
    path.write_text('{"always_on_top": "yes", "exit_with_vrchat": false, "other": 1}', encoding="utf-8")
    assert UiPrefs.load(path) == UiPrefs(always_on_top=False, exit_with_vrchat=False)


def test_launch_command_from_source(tmp_path: Path):
    settings = tmp_path / "my config" / "settings.json"
    exe, args = winsys.launch_command(settings, winsys.WAIT_FLAG)
    assert Path(exe).parent == Path(sys.executable).parent
    assert args == ["-m", "flatrig_bridge", "gui", "--settings", str(settings.resolve()), "--wait-vrchat"]
    line = winsys.command_line(exe, args)
    assert f'"{settings.resolve()}"' in line  # 空白入りのパスは引用符で囲む


def test_launch_command_frozen(tmp_path: Path, monkeypatch):
    fake_exe = tmp_path / "FlatRigBridge.exe"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(fake_exe))
    exe, args = winsys.launch_command(tmp_path / "config" / "settings.json", winsys.LAUNCH_FLAG)
    assert exe == str(fake_exe.resolve())
    assert args[0] == "--settings" and args[-1] == "--launch-vrchat"
    assert winsys.app_dir() == tmp_path.resolve()


def test_non_windows_helpers_are_inert():
    if winsys.IS_WINDOWS:
        return
    assert winsys.vrchat_running() is None
    assert winsys.acquire_single_instance() is True
    assert winsys.get_autostart() is False
    winsys.set_autostart(True, Path("settings.json"))  # 何もしない


def test_gui_subcommand_parses():
    args = build_parser().parse_args(["gui", "--wait-vrchat", "--settings", "x.json"])
    assert args.wait_vrchat is True and args.launch_vrchat is False and args.settings == "x.json"
