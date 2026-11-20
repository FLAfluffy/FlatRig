"""タスクトレイのアイコン(Windows のみ。ctypes で Shell_NotifyIconW を直接呼ぶ。追加の依存なし)。

アイコンは専用スレッドの隠しウィンドウで管理する。クリックとメニューの結果はコールバックで
そのスレッドから通知するので、画面側はスレッド安全な方法(キュー等)で受け取ること。
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path

from .winsys import IS_WINDOWS

log = logging.getLogger(__name__)

MENU_SHOW = 1
MENU_QUIT = 2


class TrayIcon:
    def __init__(self, tooltip: str, icon_path: Path | None, on_show: Callable[[], None], on_quit: Callable[[], None]):
        self._tooltip = tooltip[:127]
        self._icon_path = icon_path
        self._on_show = on_show
        self._on_quit = on_quit
        self._thread: threading.Thread | None = None
        self._hwnd = None
        self._ready = threading.Event()

    def start(self) -> bool:
        """アイコンを表示する。Windows 以外や失敗時は False。"""
        if not IS_WINDOWS:
            return False
        self._thread = threading.Thread(target=self._run, name="tray", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=5)
        return self._hwnd is not None

    def stop(self) -> None:
        if self._hwnd is not None:
            self._api.PostMessageW(self._hwnd, 0x0010, 0, 0)  # WM_CLOSE
        if self._thread is not None:
            self._thread.join(timeout=3)

    # --- 以下はトレイ用スレッドで動く --------------------------------------------------

    def _run(self) -> None:
        try:
            self._loop()
        except Exception:
            log.exception("タスクトレイのアイコンを表示できませんでした")
        finally:
            self._hwnd = None
            self._ready.set()

    def _loop(self) -> None:
        import ctypes
        from ctypes import wintypes

        LRESULT = ctypes.c_ssize_t
        WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

        class WNDCLASSEXW(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.UINT),
                ("style", wintypes.UINT),
                ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE),
                ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR),
                ("hIconSm", wintypes.HICON),
            ]

        class GUID(ctypes.Structure):
            _fields_ = [
                ("Data1", wintypes.DWORD),
                ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8),
            ]

        class NOTIFYICONDATAW(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("hWnd", wintypes.HWND),
                ("uID", wintypes.UINT),
                ("uFlags", wintypes.UINT),
                ("uCallbackMessage", wintypes.UINT),
                ("hIcon", wintypes.HICON),
                ("szTip", wintypes.WCHAR * 128),
                ("dwState", wintypes.DWORD),
                ("dwStateMask", wintypes.DWORD),
                ("szInfo", wintypes.WCHAR * 256),
                ("uVersion", wintypes.UINT),
                ("szInfoTitle", wintypes.WCHAR * 64),
                ("dwInfoFlags", wintypes.DWORD),
                ("guidItem", GUID),
                ("hBalloonIcon", wintypes.HICON),
            ]

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._api = user32

        user32.DefWindowProcW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
        user32.DefWindowProcW.restype = LRESULT
        user32.RegisterClassExW.argtypes = (ctypes.POINTER(WNDCLASSEXW),)
        user32.RegisterClassExW.restype = wintypes.ATOM
        user32.CreateWindowExW.argtypes = (
            wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
        )  # fmt: skip
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.DestroyWindow.argtypes = (wintypes.HWND,)
        user32.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
        user32.PostQuitMessage.argtypes = (ctypes.c_int,)
        user32.GetMessageW.argtypes = (ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT)
        user32.GetMessageW.restype = wintypes.BOOL
        user32.TranslateMessage.argtypes = (ctypes.POINTER(wintypes.MSG),)
        user32.DispatchMessageW.argtypes = (ctypes.POINTER(wintypes.MSG),)
        user32.DispatchMessageW.restype = LRESULT
        user32.RegisterWindowMessageW.argtypes = (wintypes.LPCWSTR,)
        user32.RegisterWindowMessageW.restype = wintypes.UINT
        user32.LoadImageW.argtypes = (
            wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int, ctypes.c_int, wintypes.UINT,
        )  # fmt: skip
        user32.LoadImageW.restype = wintypes.HANDLE
        user32.LoadIconW.argtypes = (wintypes.HINSTANCE, ctypes.c_void_p)
        user32.LoadIconW.restype = wintypes.HICON
        user32.CreatePopupMenu.restype = wintypes.HMENU
        user32.AppendMenuW.argtypes = (wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR)
        user32.SetMenuDefaultItem.argtypes = (wintypes.HMENU, wintypes.UINT, wintypes.UINT)
        user32.TrackPopupMenu.argtypes = (
            wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND, ctypes.c_void_p,
        )  # fmt: skip
        user32.TrackPopupMenu.restype = wintypes.BOOL
        user32.DestroyMenu.argtypes = (wintypes.HMENU,)
        user32.GetCursorPos.argtypes = (ctypes.POINTER(wintypes.POINT),)
        user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
        shell32.Shell_NotifyIconW.argtypes = (wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW))
        shell32.Shell_NotifyIconW.restype = wintypes.BOOL
        kernel32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE

        WM_DESTROY, WM_CLOSE, WM_NULL, WM_APP = 0x0002, 0x0010, 0x0000, 0x8000
        WM_LBUTTONUP, WM_RBUTTONUP = 0x0202, 0x0205
        NIM_ADD, NIM_DELETE = 0, 2
        NIF_MESSAGE, NIF_ICON, NIF_TIP = 1, 2, 4
        callback_msg = WM_APP + 1
        taskbar_created = user32.RegisterWindowMessageW("TaskbarCreated")

        hinst = kernel32.GetModuleHandleW(None)
        hicon = None
        if self._icon_path is not None and self._icon_path.is_file():
            # IMAGE_ICON, LR_LOADFROMFILE | LR_DEFAULTSIZE
            hicon = user32.LoadImageW(None, str(self._icon_path), 1, 0, 0, 0x10 | 0x40)
        if not hicon:
            hicon = user32.LoadIconW(None, ctypes.c_void_p(32512))  # IDI_APPLICATION

        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = callback_msg
        nid.hIcon = hicon
        nid.szTip = self._tooltip

        def show_menu(hwnd) -> None:
            menu = user32.CreatePopupMenu()
            user32.AppendMenuW(menu, 0, MENU_SHOW, "ウィンドウを表示")
            user32.AppendMenuW(menu, 0x800, 0, None)  # MF_SEPARATOR
            user32.AppendMenuW(menu, 0, MENU_QUIT, "終了")
            user32.SetMenuDefaultItem(menu, MENU_SHOW, 0)
            pt = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            user32.SetForegroundWindow(hwnd)  # これが無いとメニューの外をクリックしても閉じない
            cmd = user32.TrackPopupMenu(menu, 0x0100 | 0x0080 | 0x0002, pt.x, pt.y, 0, hwnd, None)
            user32.PostMessageW(hwnd, WM_NULL, 0, 0)
            user32.DestroyMenu(menu)
            if cmd == MENU_SHOW:
                self._on_show()
            elif cmd == MENU_QUIT:
                self._on_quit()

        def wndproc(hwnd, msg, wparam, lparam):
            try:
                if msg == callback_msg:
                    event = lparam & 0xFFFF
                    if event == WM_LBUTTONUP:
                        self._on_show()
                    elif event == WM_RBUTTONUP:
                        show_menu(hwnd)
                    return 0
                if msg == taskbar_created:  # エクスプローラーの再起動後に付け直す
                    shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))
                    return 0
                if msg == WM_CLOSE:
                    shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
                    user32.DestroyWindow(hwnd)
                    return 0
                if msg == WM_DESTROY:
                    user32.PostQuitMessage(0)
                    return 0
            except Exception:
                log.exception("タスクトレイの処理でエラーが発生しました")
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        self._wndproc = WNDPROC(wndproc)  # 参照を保持しないと GC で消えて落ちる
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = self._wndproc
        wc.hInstance = hinst
        wc.lpszClassName = "FlatRigBridgeTray"
        if not user32.RegisterClassExW(ctypes.byref(wc)):
            raise ctypes.WinError(ctypes.get_last_error())
        hwnd = user32.CreateWindowExW(0, wc.lpszClassName, "FlatRig Bridge", 0, 0, 0, 0, 0, None, None, hinst, None)
        if not hwnd:
            raise ctypes.WinError(ctypes.get_last_error())
        nid.hWnd = hwnd
        if not shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)):
            user32.DestroyWindow(hwnd)
            raise OSError("Shell_NotifyIconW(NIM_ADD) に失敗しました")
        self._hwnd = hwnd
        self._ready.set()

        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
