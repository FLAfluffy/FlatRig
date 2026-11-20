"""配布フォルダ dist/FlatRig と zip を作る(Windows で実行)。

使い方(bridge フォルダで):
    .\\.venv\\Scripts\\python.exe -m pip install -e ".[build,tools]"
    .\\.venv\\Scripts\\python.exe packaging\\build.py

実設定(settings.json / params.json)・認証トークン・ログは同梱しない。
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

BRIDGE = Path(__file__).resolve().parents[1]
GUIDE = BRIDGE.parent / "docs" / "AVATAR_GUIDE.md"
UNITY_PACKAGE = BRIDGE.parent / "unity-package"  # Unity 用のツール(.unitypackage にして配布フォルダに入れる)
UNITY_PACKAGE_NAME = "FlatRigTools.unitypackage"
GUIDE_NAME = "アバター作成ガイド.html"
LICENSE = BRIDGE.parent / "LICENSE"  # FlatRig 自体のライセンス
sys.path.insert(0, str(BRIDGE / "src"))
sys.path.insert(0, str(BRIDGE / "packaging"))

import collect_licenses  # noqa: E402
import make_guide_html  # noqa: E402
import make_unitypackage  # noqa: E402

from flatrig_bridge import __version__  # noqa: E402

DIST = BRIDGE / "dist"
APP = DIST / "FlatRig"  # flatrig_bridge.spec の COLLECT の name と同じ
CONFIG_FILES = ("settings.example.json", "params.example.json")
FORBIDDEN = {"settings.json", "params.json", ".vts_token.json", "ui.json", "bridge.log"}
# 試作の素材とその書き出し物(PSD・対応表・レイヤー一覧)も入れない
FORBIDDEN |= {"mapping.json", "layers.json", "psd_layers.txt", "rig.json"}
FORBIDDEN_SUFFIXES = (".psd", ".psb", ".clip", ".kra", ".log", ".token")


def main() -> int:
    if sys.platform != "win32":
        print("注意: Windows 以外で作った exe は Windows では動きません(仕組みの確認用)")
    try:
        import psd_tools  # noqa: F401  FlatRigPSD.exe に必要
    except ImportError:
        print('エラー: psd-tools がありません。 pip install -e ".[build,tools]" を実行してください')
        return 1
    try:
        import markdown_it  # noqa: F401  アバター作成ガイドの HTML に必要
    except ImportError:
        print('エラー: markdown-it-py がありません。 pip install -e ".[build,tools]" を実行してください')
        return 1
    import PyInstaller.__main__

    PyInstaller.__main__.run([
        str(BRIDGE / "packaging" / "flatrig_bridge.spec"),
        "--noconfirm",
        "--clean",
        "--distpath", str(DIST),
        "--workpath", str(BRIDGE / "build" / "pyinstaller"),
    ])  # fmt: skip

    config = APP / "config"
    config.mkdir(exist_ok=True)
    for name in CONFIG_FILES:
        shutil.copy2(BRIDGE / "config" / name, config / name)
    shutil.copy2(BRIDGE / "packaging" / "README_dist.txt", APP / "README.txt")
    shutil.copy2(LICENSE, APP / "LICENSE.txt")
    make_guide_html.write(GUIDE, APP / GUIDE_NAME)
    count = make_unitypackage.write(UNITY_PACKAGE, APP / UNITY_PACKAGE_NAME)
    print(f"{UNITY_PACKAGE_NAME}: アセット {count} 個")
    missing = collect_licenses.write(APP / "THIRD_PARTY_LICENSES.txt")

    leaked = [p for p in APP.rglob("*") if p.name in FORBIDDEN or p.suffix.lower() in FORBIDDEN_SUFFIXES]
    if leaked:
        print("エラー: 同梱してはいけないファイルが入っています:")
        for p in leaked:
            print(f"  {p}")
        return 1

    archive = shutil.make_archive(str(DIST / f"FlatRig-{__version__}"), "zip", DIST, APP.name)
    for title in missing:
        print(f"警告: ライセンス文が見つかりません(頒布前に要確認): {title}")
    print(f"完了: {APP}")
    print(f"      {archive}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
