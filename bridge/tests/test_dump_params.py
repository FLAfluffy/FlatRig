"""dump-params CLI をモック VTS に対して実行する。"""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from mock_vts import MockVts

from flatrig_bridge.__main__ import main

CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"


class MockThread:
    """CLI は内部で asyncio.run を使うため、モックは別スレッドのループで動かす。"""

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.vts = MockVts()
        self._thread = threading.Thread(target=self.loop.run_forever, daemon=True)

    def __enter__(self) -> MockVts:
        self._thread.start()
        asyncio.run_coroutine_threadsafe(self.vts.start(), self.loop).result(5)
        return self.vts

    def __exit__(self, *exc: object) -> None:
        asyncio.run_coroutine_threadsafe(self.vts.stop(), self.loop).result(5)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(5)


@pytest.fixture
def vts() -> Iterator[MockVts]:
    with MockThread() as server:
        yield server


def write_settings(tmp_path: Path, port: int) -> Path:
    data = json.loads((CONFIG_DIR / "settings.example.json").read_text(encoding="utf-8"))
    data["vts"]["port"] = port
    data["vts"]["request_timeout_sec"] = 1.0
    (tmp_path / "params.example.json").write_text(
        (CONFIG_DIR / "params.example.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_dump_params_saves_numbers_only(vts: MockVts, tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    vts.issue_token = "TOKEN-MUST-NOT-LEAK"
    out = tmp_path / "dump.json"
    assert main(["dump-params", "--settings", str(write_settings(tmp_path, vts.port)), "--out", str(out)]) == 0

    text = out.read_text(encoding="utf-8")
    doc = json.loads(text)
    assert doc["format"] == "flatrig-vts-dump/1"
    assert doc["parameter_count"] == 5
    assert set(doc["parameters"][0]) == {"name", "value", "min", "max", "defaultValue"}
    # モデル情報・トークンを保存/表示しない
    stdout = capsys.readouterr().out
    for secret in ("SecretModelName", "secret-model-id", "TOKEN-MUST-NOT-LEAK"):
        assert secret not in text
        assert secret not in stdout
    assert "[あり] FR_AngleX" in stdout
    assert "[なし] FR_AngleZ" in stdout  # モックに無い Phase 2 のパラメータ


def test_dump_params_reports_range_mismatch(vts: MockVts, tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    vts.parameters[0]["min"], vts.parameters[0]["max"] = -90.0, 90.0
    settings = write_settings(tmp_path, vts.port)
    assert main(["dump-params", "--settings", str(settings), "--out", str(tmp_path / "d.json")]) == 0
    assert "範囲が食い違い" in capsys.readouterr().out


def test_dump_params_model_not_loaded(vts: MockVts, tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    vts.model_loaded = False
    out = tmp_path / "dump.json"
    assert main(["dump-params", "--settings", str(write_settings(tmp_path, vts.port)), "--out", str(out)]) == 1
    assert "モデルが読み込まれていません" in capsys.readouterr().out
    assert not out.exists()


def test_dump_params_denied(vts: MockVts, tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    vts.deny_token_request = True
    settings = write_settings(tmp_path, vts.port)
    assert main(["dump-params", "--settings", str(settings), "--out", str(tmp_path / "d.json")]) == 1
    assert "Allow" in capsys.readouterr().out


def test_dump_params_no_vts(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    settings = write_settings(tmp_path, port)
    assert main(["dump-params", "--settings", str(settings), "--out", str(tmp_path / "d.json")]) == 1
    assert "APIの起動(プラグインを許可)" in capsys.readouterr().out


def test_dump_params_reports_alias_match(vts: MockVts, tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    for p in vts.parameters:
        if p["name"] == "ParamMouthOpenY":
            p["name"] = "ParamMouthOpen"
    settings = write_settings(tmp_path, vts.port)
    assert main(["dump-params", "--settings", str(settings), "--out", str(tmp_path / "d.json")]) == 0
    out = capsys.readouterr().out
    assert "[あり] FR_MouthOpenY   ← ParamMouthOpen" in out
    assert "別名で一致" in out
