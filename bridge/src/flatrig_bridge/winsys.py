"""Windows 専用の小さな機能(プロセス確認・自動起動・多重起動防止・ショートカット)。

標準ライブラリ(ctypes / winreg)だけで実装する。Windows 以外では何もしないか None を返す。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"

VRCHAT_EXE = "vrchat.exe"
VRCHAT_STEAM_URI = "steam://rungameid/438100"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "FlatRigBridge"
MUTEX_NAME = "Local\\FlatRigBridge"
SHORTCUT_NAME = "VRChat (FlatRig 連動).lnk"
WAIT_FLAG = "--wait-vrchat"
LAUNCH_FLAG = "--launch-vrchat"

_mutex_handle = None  # プロセス終了まで保持する


def is_frozen() -> bool:
    """PyInstaller で exe 化されて動いているか。"""
    return bool(getattr(sys, "frozen", False))


def app_dir() -> Path:
    """設定ファイルを探す基準のフォルダ。exe なら exe の場所、そうでなければカレント。"""
    return Path(sys.executable).resolve().parent if is_frozen() else Path.cwd()


def resource_path(relative: str) -> Path:
    """同梱リソース(アイコン等)のパス。"""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / relative


# --- プロセス確認 ------------------------------------------------------------------


def process_names() -> set[str] | None:
    """実行中のプロセスの exe 名(小文字)の集合。Windows 以外や取得失敗では None。"""
    if not IS_WINDOWS:
        return None
    import ctypes
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = (wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W))
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = (wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W))
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)

    snapshot = kernel32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if not snapshot or snapshot == ctypes.c_void_p(-1).value:
        return None
    names: set[str] = set()
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            names.add(entry.szExeFile.lower())
            ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return names


def vrchat_running() -> bool | None:
    names = process_names()
    return None if names is None else VRCHAT_EXE in names


def launch_vrchat() -> None:
    """Steam 経由で VRChat を起動する。"""
    os.startfile(VRCHAT_STEAM_URI)  # type: ignore[attr-defined]  # Windows のみ


# --- 多重起動防止 ------------------------------------------------------------------


def acquire_single_instance() -> bool:
    """2 つ目の起動なら False。Windows 以外は常に True。"""
    global _mutex_handle
    if not IS_WINDOWS:
        return True
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    _mutex_handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    return ctypes.get_last_error() != 183  # ERROR_ALREADY_EXISTS


def message_box(text: str, title: str = "FlatRig Bridge") -> None:
    if not IS_WINDOWS:
        print(text)
        return
    import ctypes

    ctypes.windll.user32.MessageBoxW(None, text, title, 0x40)  # MB_ICONINFORMATION


# --- 自動起動(HKCU\...\Run) ---------------------------------------------------------


def launch_command(settings_path: Path, *extra: str) -> tuple[str, list[str]]:
    """このアプリを起動するための (実行ファイル, 引数)。"""
    args = ["--settings", str(settings_path.resolve()), *extra]
    if is_frozen():
        return str(Path(sys.executable).resolve()), args
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    return str(pythonw if pythonw.is_file() else exe), ["-m", "flatrig_bridge", "gui", *args]


def command_line(exe: str, args: list[str]) -> str:
    return subprocess.list2cmdline([exe, *args])


def get_autostart() -> bool:
    if not IS_WINDOWS:
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, RUN_VALUE)
        return True
    except OSError:
        return False


def set_autostart(enabled: bool, settings_path: Path) -> None:
    """Windows のサインイン時に待機モードで起動するよう登録/解除する。"""
    if not IS_WINDOWS:
        return
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, command_line(*launch_command(settings_path, WAIT_FLAG)))
        else:
            try:
                winreg.DeleteValue(key, RUN_VALUE)
            except FileNotFoundError:
                pass


# --- ショートカット -----------------------------------------------------------------

_SHORTCUT_PS = (
    "$d=[Environment]::GetFolderPath('Desktop');"
    "$s=(New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $d $env:FR_NAME));"
    "$s.TargetPath=$env:FR_TARGET;$s.Arguments=$env:FR_ARGS;$s.WorkingDirectory=$env:FR_DIR;"
    "$s.IconLocation=$env:FR_ICON;$s.Description='VRChat と FlatRig Bridge を同時に起動';$s.Save();"
    "Write-Output (Join-Path $d $env:FR_NAME)"
)


def create_vrchat_shortcut(settings_path: Path) -> Path:
    """デスクトップに「VRChat と同時起動」のショートカットを作る。値は環境変数で渡す(引用符の問題を避ける)。"""
    exe, args = launch_command(settings_path, LAUNCH_FLAG)
    icon = exe if is_frozen() else str(resource_path("assets/flatrig.ico"))
    env = os.environ | {
        "FR_NAME": SHORTCUT_NAME,
        "FR_TARGET": exe,
        "FR_ARGS": subprocess.list2cmdline(args),
        "FR_DIR": str(Path(exe).parent if is_frozen() else app_dir()),
        "FR_ICON": icon,
    }
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", _SHORTCUT_PS],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode != 0:
        raise OSError(result.stderr.strip() or "ショートカットを作成できませんでした")
    return Path(result.stdout.strip().splitlines()[-1])
