# PyInstaller の設定。packaging/build.py から使う(直接: pyinstaller packaging/flatrig_bridge.spec)。
# 1 つのフォルダに、ウィンドウ版 FlatRigBridge.exe・コマンド版 flatrig-cli.exe・PSD 変換 FlatRigPSD.exe を
# 並べる(ライブラリは共有)。
from pathlib import Path

BRIDGE = Path(SPECPATH).parent
SRC = BRIDGE / "src"
TOOLS = BRIDGE.parent / "tools"
ICON = SRC / "flatrig_bridge" / "assets" / "flatrig.ico"

a = Analysis(
    [
        str(BRIDGE / "packaging" / "gui_main.py"),
        str(BRIDGE / "packaging" / "cli_main.py"),
        str(BRIDGE / "packaging" / "psd_main.py"),
    ],
    pathex=[str(SRC), str(TOOLS)],
    datas=[(str(ICON), "assets")],
    excludes=["pytest", "tkinter.test", "numpy.tests", "PIL.ImageQt", "PIL.ImageTk"],
)
pyz = PYZ(a.pure)


ENTRIES = ("gui_main", "cli_main", "psd_main")


def scripts_for(name):
    # a.scripts には起動用の処理と 3 つの入口が入っている。他の入口を除く
    return [s for s in a.scripts if s[0] == name or s[0] not in ENTRIES]


gui = EXE(
    pyz,
    scripts_for("gui_main"),
    [],
    exclude_binaries=True,
    name="FlatRigBridge",
    console=False,
    icon=str(ICON),
)
cli = EXE(
    pyz,
    scripts_for("cli_main"),
    [],
    exclude_binaries=True,
    name="flatrig-cli",
    console=True,
    icon=str(ICON),
)
psd = EXE(
    pyz,
    scripts_for("psd_main"),
    [],
    exclude_binaries=True,
    name="FlatRigPSD",
    console=False,  # 黒い画面を出さない。メッセージは %LOCALAPPDATA%\FlatRig\FlatRigPSD.log と画面で知らせる
    icon=str(ICON),
)
coll = COLLECT(gui, cli, psd, a.binaries, a.datas, name="FlatRig")  # 配布フォルダの名前(キット全体の名前)
