"""配布版 FlatRigPSD.exe の入口(PSD → FlatRig のレイヤー素材)。PSD をこの exe にドロップして使う。

ドロップしたときは修正画面(tools/psd_editor.py)を開き、部品分けを確認してから書き出す。
配布版は黒い画面(コンソール)を出さない。メッセージはログファイルに残し、失敗したときは画面で知らせる。
"""

import os
import sys
import tempfile
from pathlib import Path

import psd_editor  # tools/psd_editor.py(spec の pathex で読み込む)
import psd_to_layers  # tools/psd_to_layers.py

USAGE = (
    "PSD ファイルをこの exe のアイコンにドラッグ&ドロップしてください。\n"
    "(コマンドで使う場合: FlatRigPSD.exe edit <PSD>  /  auto <PSD>  /  export <mapping.json>)"
)
TAIL_LINES = 15  # 画面に出すメッセージの行数(全部はログファイルに残る)


def log_path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    return Path(base) / "FlatRig" / "FlatRigPSD.log"


class Tee:
    """print の出力をログファイルに書き、画面に出すために覚えておく。元の出力(コマンドから起動したとき)にも出す。"""

    def __init__(self, path: Path, orig):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.file = open(path, "w", encoding="utf-8")  # noqa: SIM115  終了まで開いておく
        self.orig, self.text = orig, []

    def write(self, s: str) -> int:
        self.file.write(s)
        self.file.flush()
        self.text.append(s)
        if self.orig is not None:
            try:
                self.orig.write(s)
            except Exception:
                pass
        return len(s)

    def flush(self) -> None:
        self.file.flush()

    def tail(self) -> str:
        lines = [x for x in "".join(self.text).splitlines() if x.strip()]
        return "\n".join(lines[-TAIL_LINES:])


def run(argv: list[str]) -> int:
    if not argv:
        print("FlatRigPSD: " + USAGE)
        return 1
    if len(argv) == 1 and argv[0].lower().endswith(".psd"):
        argv = ["edit", argv[0]]  # ドロップしたときは修正画面
    try:
        return psd_to_layers.main(argv)
    except SystemExit as exc:  # argparse のエラーなど
        return int(exc.code or 0) if isinstance(exc.code, int) else 1
    except Exception as exc:
        import traceback

        traceback.print_exc()
        print(f"エラー: {exc}")
        return 1


def show(title: str, text: str, error: bool) -> None:
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        (messagebox.showerror if error else messagebox.showinfo)(title, text, parent=root)
        root.destroy()
    except Exception:
        pass  # 画面を出せないときはログファイルだけ


def splash():
    """PSD を読んで修正画面が開くまでの間に出す小さな画面。修正画面が開くときに消える。"""
    try:
        import tkinter as tk

        root = tk.Tk()
        root.title("FlatRig")
        root.resizable(False, False)
        tk.Label(root, text="PSD を読み込んでいます…", padx=40, pady=20).pack()
        root.update()
        return root
    except Exception:
        return None


def main() -> int:
    argv = sys.argv[1:]
    if not getattr(sys, "frozen", False):
        return run(argv)
    path = log_path()
    try:
        tee = Tee(path, sys.stdout)
    except OSError:
        tee = None
    if tee is not None:
        sys.stdout = sys.stderr = tee
    if argv:
        psd_editor.SPLASH = splash()
    code = run(argv)
    psd_editor.close_splash()
    text = tee.tail() if tee is not None else ""
    where = f"\n\n詳しい記録: {path}" if tee is not None else ""
    if not argv:
        show("FlatRigPSD", USAGE, error=False)
    elif code != 0 and psd_to_layers.CANCELLED_MESSAGE not in text:
        show("FlatRigPSD", f"処理できませんでした。\n\n{text}{where}", error=True)
    elif code == 0 and argv[0] != "edit":
        show("FlatRigPSD", f"{text}{where}", error=False)  # コマンドで使ったときの結果(修正画面は自分で知らせる)
    return code


if __name__ == "__main__":
    sys.exit(main())
