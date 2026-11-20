"""配布物に同梱する第三者ライセンス文(THIRD_PARTY_LICENSES.txt)を集める。

docs/DEPENDENCIES.md の「配布物に同梱されるもの」と対応させること。見つからないものは警告して、
ファイル内に「要確認」と書く(頒布前に必ず埋める)。
"""

from __future__ import annotations

import sys
from importlib import metadata
from pathlib import Path

PACKAGES = (
    ("websockets", "VTube Studio との接続"),
    ("python-osc", "VRChat への OSC 送受信"),
    ("psd-tools", "PSD の読み込み(FlatRigPSD.exe)"),
    ("numpy", "画像の計算(FlatRigPSD.exe)。同梱ライブラリのライセンスを含む"),
    ("pillow", "画像の読み書き(FlatRigPSD.exe)。同梱ライブラリのライセンスを含む"),
    ("attrs", "psd-tools が使う部品"),
)
RULE = "=" * 78


def _dist_license(name: str) -> str | None:
    try:
        dist = metadata.distribution(name)
    except metadata.PackageNotFoundError:
        return None
    texts = []
    for f in dist.files or ():
        # dist-info 内のライセンス文だけを見る(numpy などは licenses/ に同梱ライブラリの分も入っている)
        in_dist_info = ".dist-info" in str(f.parent)
        if not in_dist_info or not f.name.upper().startswith(("LICENSE", "LICENCE", "COPYING", "NOTICE")):
            continue
        text = Path(dist.locate_file(f)).read_text(encoding="utf-8", errors="replace").strip()
        if text:
            texts.append(text if len(texts) == 0 else f"--- {f} ---\n{text}")
    if texts:
        return "\n\n".join(texts)
    text = (dist.metadata.get("License") or "").strip()
    return text or None


def _python_license() -> str | None:
    path = Path(sys.base_prefix) / "LICENSE.txt"
    return path.read_text(encoding="utf-8", errors="replace").strip() if path.is_file() else None


def _tcltk_licenses() -> list[tuple[str, str]]:
    found = []
    for path in sorted((Path(sys.base_prefix) / "tcl").glob("*/license.terms")):
        found.append((path.parent.name, path.read_text(encoding="utf-8", errors="replace").strip()))
    return found


def _pyinstaller_exception() -> str | None:
    text = _dist_license("pyinstaller")
    if text is None:
        return None
    # 全文は長いので、生成物に関係するブートローダの例外条項の段落だけを抜き出す
    start = text.find("Bootloader Exception")
    return text[start : start + 2000].strip() if start >= 0 else text


def build_text() -> tuple[str, list[str]]:
    sections: list[tuple[str, str | None]] = [("Python (CPython) — 実行環境", _python_license())]
    tcltk = _tcltk_licenses()
    if tcltk:
        sections += [(f"{name} — 画面表示(tkinter)", text) for name, text in tcltk]
    else:
        sections.append(("Tcl/Tk — 画面表示(tkinter)", None))
    sections += [(f"{name} — {purpose}", _dist_license(name)) for name, purpose in PACKAGES]
    sections.append(("PyInstaller ブートローダ(例外条項) — exe 化", _pyinstaller_exception()))

    missing = [title for title, text in sections if not text]
    parts = [
        "FlatRig に同梱している第三者ソフトウェアのライセンス",
        "Third-party software licenses bundled with FlatRig",
        "",
    ]
    for title, text in sections:
        parts += [RULE, title, RULE, text or "(要確認: ライセンス文が見つかりませんでした)", ""]
    return "\n".join(parts), missing


def write(out: Path) -> list[str]:
    text, missing = build_text()
    out.write_text(text, encoding="utf-8")
    return missing


if __name__ == "__main__":
    dest = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("THIRD_PARTY_LICENSES.txt")
    for title in write(dest):
        print(f"警告: ライセンス文が見つかりません: {title}")
    print(f"作成しました: {dest}")
