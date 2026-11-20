"""常駐アプリの画面設定(config/ui.json)。壊れていたら既定値に戻す。

Windows 起動時の待機はレジストリ側が正なので、ここには持たない。
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, fields
from pathlib import Path

log = logging.getLogger(__name__)

UI_PREFS_NAME = "ui.json"


@dataclass
class UiPrefs:
    always_on_top: bool = False
    exit_with_vrchat: bool = True

    @classmethod
    def load(cls, path: Path) -> UiPrefs:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return cls()
        except (OSError, ValueError) as exc:
            log.warning("%s を読めないため既定値を使います: %s", path.name, exc)
            return cls()
        prefs = cls()
        if isinstance(data, dict):
            for f in fields(cls):
                if isinstance(data.get(f.name), bool):
                    setattr(prefs, f.name, data[f.name])
        return prefs

    def save(self, path: Path) -> None:
        try:
            path.write_text(json.dumps(asdict(self), indent=2) + "\n", encoding="utf-8")
        except OSError as exc:
            log.warning("%s を保存できませんでした: %s", path.name, exc)
