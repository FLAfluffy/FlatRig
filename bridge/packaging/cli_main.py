"""配布版 flatrig-cli.exe の入口(コマンド版。check-config / dump-params / selftest など)。"""

import sys

from flatrig_bridge.__main__ import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
