"""常駐アプリの画面(tkinter)。状態表示・[終了] ボタン・タスクトレイ・VRChat との連動。

- 通常起動: すぐに中継を始める。「VRChat を閉じたら終了」が ON なら、VRChat の終了でアプリも終わる。
- --wait-vrchat(Windows 起動時の待機): トレイだけで常駐し、VRChat の起動中だけ中継する。
- --launch-vrchat(ショートカット): VRChat を起動してから通常起動する。
"""

from __future__ import annotations

import logging
import logging.handlers
import queue
import subprocess
import time
from pathlib import Path

from . import __version__, winsys
from .app import Bridge, State
from .config import ConfigError, find_settings, load_params, load_settings
from .prefs import UI_PREFS_NAME, UiPrefs
from .runner import Action, BridgeRunner, VrchatFollower
from .tray import TrayIcon

log = logging.getLogger(__name__)

TITLE = "FlatRig Bridge"
POLL_MS = 500
VRCHAT_CHECK_SEC = 3.0

STATE_TEXT = {
    State.DISCONNECTED: "VTube Studio に接続中…",
    State.AUTHENTICATING: "VTube Studio の許可を待っています",
    State.NO_MODEL: "VTube Studio のモデル待ち",
    State.STREAMING: "中継中",
}


class LastMessageHandler(logging.Handler):
    """画面に出すため、最後の警告/エラーを 1 件だけ覚えておく。"""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.message = ""
        self.at = 0.0

    def emit(self, record: logging.LogRecord) -> None:
        self.message = record.getMessage()
        self.at = time.monotonic()


def _setup_logging(level: str, log_dir: Path) -> LastMessageHandler:
    root = logging.getLogger()
    root.setLevel(level.upper())
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            log_dir / "bridge.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError:
        pass
    last = LastMessageHandler()
    root.addHandler(last)
    return last


def run_gui(settings_arg: str | None, wait_vrchat: bool, launch_vrchat: bool) -> int:
    if not winsys.acquire_single_instance():
        if launch_vrchat:
            winsys.launch_vrchat()  # 待機中のアプリが VRChat の起動を拾う
        else:
            winsys.message_box("FlatRig Bridge はすでに起動しています。\nタスクトレイのアイコンから開けます。")
        return 0

    try:
        settings_path = Path(settings_arg) if settings_arg else find_settings(winsys.app_dir())
        settings = load_settings(settings_path)
        params = load_params(settings.mapping.params_file)
    except ConfigError as exc:
        winsys.message_box("設定エラー:\n" + "\n".join(f"・{e}" for e in exc.errors))
        return 1

    last = _setup_logging(settings.logging.level, settings_path.parent.parent / "logs")
    log.info("FlatRig Bridge %s を起動しました(設定: %s)", __version__, settings_path)
    if launch_vrchat:
        try:
            winsys.launch_vrchat()
        except OSError as exc:
            log.warning("VRChat を起動できませんでした: %s", exc)

    app = BridgeApp(settings_path, lambda: Bridge(settings, params), last, wait_mode=wait_vrchat)
    return app.run()


class BridgeApp:
    def __init__(self, settings_path: Path, make_bridge, last: LastMessageHandler, *, wait_mode: bool):
        import tkinter as tk
        from tkinter import ttk

        self.tk = tk
        self.settings_path = settings_path
        self.prefs_path = settings_path.parent / UI_PREFS_NAME
        self.prefs = UiPrefs.load(self.prefs_path)
        self.runner = BridgeRunner(make_bridge)
        self.follower = VrchatFollower(wait_mode=wait_mode, exit_with_vrchat=self.prefs.exit_with_vrchat)
        self.wait_mode = wait_mode
        self.last = last
        self.events: queue.Queue[str] = queue.Queue()
        self._last_check = 0.0
        self._rate = (time.monotonic(), 0)
        self._quitting = False

        _enable_dpi_awareness()
        root = self.root = tk.Tk()
        root.title(TITLE)
        root.resizable(False, False)
        icon = winsys.resource_path("assets/flatrig.ico")
        if icon.is_file():
            try:
                root.iconbitmap(default=str(icon))
            except tk.TclError:
                pass

        frame = ttk.Frame(root, padding=12)
        frame.grid(sticky="nsew")
        self.state_var = tk.StringVar(value="起動中…")
        self.detail_var = tk.StringVar(value="")
        self.message_var = tk.StringVar(value="")
        ttk.Label(frame, textvariable=self.state_var, font=("", 12, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w"
        )
        ttk.Label(frame, textvariable=self.detail_var).grid(row=1, column=0, columnspan=2, sticky="w")
        ttk.Label(frame, textvariable=self.message_var, foreground="#b45309", wraplength=320).grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(2, 6)
        )

        self.topmost_var = tk.BooleanVar(value=self.prefs.always_on_top)
        self.exit_var = tk.BooleanVar(value=self.prefs.exit_with_vrchat)
        self.autostart_var = tk.BooleanVar(value=winsys.get_autostart())
        ttk.Checkbutton(frame, text="常に手前に表示", variable=self.topmost_var, command=self._on_topmost).grid(
            row=3, column=0, columnspan=2, sticky="w"
        )
        ttk.Checkbutton(
            frame, text="VRChat を閉じたら終了する", variable=self.exit_var, command=self._on_exit_pref
        ).grid(row=4, column=0, columnspan=2, sticky="w")
        ttk.Checkbutton(
            frame,
            text="Windows の起動時に待機する(VRChat の起動中だけ中継)",
            variable=self.autostart_var,
            command=self._on_autostart,
        ).grid(row=5, column=0, columnspan=2, sticky="w")

        ttk.Button(frame, text="VRChat と同時起動するショートカットを作成", command=self._on_shortcut).grid(
            row=6, column=0, sticky="w", pady=(8, 0)
        )
        ttk.Button(frame, text="終了", command=self.quit).grid(row=6, column=1, sticky="e", padx=(8, 0), pady=(8, 0))
        ttk.Label(frame, text="× で閉じるとタスクトレイに隠れます。", foreground="#6b7280").grid(
            row=7, column=0, columnspan=2, sticky="w", pady=(6, 0)
        )

        root.protocol("WM_DELETE_WINDOW", self.hide)
        root.attributes("-topmost", self.prefs.always_on_top)

        self.tray = TrayIcon(
            TITLE,
            icon,
            on_show=lambda: self.events.put("show"),
            on_quit=lambda: self.events.put("quit"),
        )
        self.has_tray = self.tray.start()

    # --- 起動と終了 -----------------------------------------------------------------

    def run(self) -> int:
        if self.wait_mode and self.has_tray:
            self.root.withdraw()  # 待機モードはトレイだけで常駐する
        if not self.wait_mode:
            self.runner.start()
        self.root.after(0, self._poll)
        try:
            self.root.mainloop()
        finally:
            self._shutdown()
        return 0

    def quit(self) -> None:
        if not self._quitting:
            self._quitting = True
            self.root.after(0, self.root.destroy)

    def _shutdown(self) -> None:
        self.runner.stop()
        self.tray.stop()
        log.info("FlatRig Bridge を終了しました")

    def show(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def hide(self) -> None:
        if self.has_tray:
            self.root.withdraw()
        else:
            self.quit()

    # --- 定期処理 -------------------------------------------------------------------

    def _poll(self) -> None:
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            if event == "show":
                self.show()
            elif event == "quit":
                self.quit()
        now = time.monotonic()
        if now - self._last_check >= VRCHAT_CHECK_SEC:
            self._last_check = now
            self._follow_vrchat()
        self._refresh()
        if not self._quitting:
            self.root.after(POLL_MS, self._poll)

    def _follow_vrchat(self) -> None:
        action = self.follower.tick(winsys.vrchat_running())
        if action is Action.START:
            log.info("VRChat の起動を検知したため中継を始めます")
            self.runner.start()
        elif action is Action.STOP:
            log.info("VRChat の終了を検知したため中継を止めます(待機を続けます)")
            self.runner.stop()
        elif action is Action.QUIT:
            log.info("VRChat の終了を検知したため終了します")
            self.quit()

    def _refresh(self) -> None:
        status = self.runner.status()
        if not status.running:
            self.state_var.set("VRChat の起動を待っています" if self.wait_mode else "停止中")
            self.detail_var.set("")
        else:
            self.state_var.set(STATE_TEXT.get(status.state, "起動中…"))
            t0, frames0 = self._rate
            now = time.monotonic()
            if now - t0 >= 1.0:
                hz = (status.vts_frames - frames0) / (now - t0)
                self._rate = (now, status.vts_frames)
                self.detail_var.set(f"VTS 受信 {hz:.0f} Hz ・ OSC 送信 {status.osc_sent} 件")
        recent = self.last.message and time.monotonic() - self.last.at < 30
        self.message_var.set(self.last.message if recent else "")

    # --- 設定の操作 -----------------------------------------------------------------

    def _on_topmost(self) -> None:
        self.prefs.always_on_top = self.topmost_var.get()
        self.root.attributes("-topmost", self.prefs.always_on_top)
        self.prefs.save(self.prefs_path)

    def _on_exit_pref(self) -> None:
        self.prefs.exit_with_vrchat = self.exit_var.get()
        self.follower.exit_with_vrchat = self.prefs.exit_with_vrchat
        self.prefs.save(self.prefs_path)

    def _on_autostart(self) -> None:
        from tkinter import messagebox

        try:
            winsys.set_autostart(self.autostart_var.get(), self.settings_path)
        except OSError as exc:
            self.autostart_var.set(winsys.get_autostart())
            messagebox.showerror(TITLE, f"自動起動の設定を変更できませんでした:\n{exc}")

    def _on_shortcut(self) -> None:
        from tkinter import messagebox

        try:
            path = winsys.create_vrchat_shortcut(self.settings_path)
        except (OSError, ValueError, IndexError, subprocess.SubprocessError) as exc:
            messagebox.showerror(TITLE, f"ショートカットを作成できませんでした:\n{exc}")
            return
        messagebox.showinfo(TITLE, f"デスクトップにショートカットを作成しました:\n{path.name}")


def _enable_dpi_awareness() -> None:
    """高 DPI 環境で文字がぼやけないようにする(失敗しても続行)。"""
    if not winsys.IS_WINDOWS:
        return
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass
