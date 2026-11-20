"""配布版 FlatRigBridge.exe の入口(ウィンドウ版)。引数は gui サブコマンドに渡す。"""

import sys

from flatrig_bridge.__main__ import main

if __name__ == "__main__":
    sys.exit(main(["gui", *sys.argv[1:]]))
