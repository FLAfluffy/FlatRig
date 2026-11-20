from __future__ import annotations

import json
import socket
from collections.abc import Iterator
from pathlib import Path

import pytest
from osc_helpers import parse_osc

from flatrig_bridge.__main__ import main
from flatrig_bridge.config import load_params
from flatrig_bridge.osc_out import OscSender
from flatrig_bridge.selftest import pattern_value, run_selftest

CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"


class Receiver:
    def __init__(self) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(1.0)
        self.port = self.sock.getsockname()[1]

    def drain(self) -> list[tuple[str, str, list]]:
        out = []
        self.sock.settimeout(0.2)
        try:
            while True:
                out.append(parse_osc(self.sock.recv(4096)))
        except TimeoutError:
            return out


@pytest.fixture
def receiver() -> Iterator[Receiver]:
    r = Receiver()
    yield r
    r.sock.close()


# --- OscSender ----------------------------------------------------------------


def test_send_float_is_float32(receiver: Receiver):
    sender = OscSender("127.0.0.1", receiver.port)
    sender.send_float("FR_AngleX", 0.5)
    assert receiver.drain() == [("/avatar/parameters/FR_AngleX", "f", [0.5])]
    assert sender.sent_count == 1


def test_send_int_value_still_float(receiver: Receiver):
    OscSender("127.0.0.1", receiver.port).send_float("FR_EyeLOpen", 1)
    assert receiver.drain() == [("/avatar/parameters/FR_EyeLOpen", "f", [1.0])]


def test_send_bool_true_false(receiver: Receiver):
    sender = OscSender("127.0.0.1", receiver.port)
    sender.send_bool("FR_Active", True)
    sender.send_bool("FR_Active", False)
    assert receiver.drain() == [
        ("/avatar/parameters/FR_Active", "T", [True]),
        ("/avatar/parameters/FR_Active", "F", [False]),
    ]


def test_custom_prefix(receiver: Receiver):
    OscSender("localhost", receiver.port, "/test/").send_float("X", -0.25)
    assert receiver.drain() == [("/test/X", "f", [-0.25])]


@pytest.mark.parametrize("host", ["192.168.0.2", "example.com", "0.0.0.0"])
def test_non_local_host_rejected(host: str):
    with pytest.raises(ValueError, match="G7"):
        OscSender(host, 9000)


# --- selftest -----------------------------------------------------------------


@pytest.mark.parametrize("pattern", ["sine", "ramp", "zero"])
@pytest.mark.parametrize("mode", ["signed", "unsigned"])
def test_pattern_value_in_range(pattern: str, mode: str):
    lo = -1.0 if mode == "signed" else 0.0
    for i in range(200):
        v = pattern_value(pattern, mode, i * 0.05, i % 14, 4.0)
        assert lo <= v <= 1.0


def test_pattern_sine_values():
    assert pattern_value("sine", "signed", 1.0, 0, 4.0) == pytest.approx(1.0)
    assert pattern_value("sine", "unsigned", 3.0, 0, 4.0) == pytest.approx(0.0)
    with pytest.raises(ValueError):
        pattern_value("square", "signed", 0, 0, 4.0)


def test_run_selftest_with_fake_clock(receiver: Receiver):
    params = load_params(CONFIG_DIR / "params.example.json").params_up_to(1)
    now = [0.0]
    frames = run_selftest(
        OscSender("127.0.0.1", receiver.port),
        params,
        pattern="sine",
        duration=0.1,
        rate_hz=30,
        clock=lambda: now[0],
        sleep=lambda s: now.__setitem__(0, now[0] + 1 / 30),
    )
    msgs = receiver.drain()
    assert frames == 3
    assert msgs[0] == ("/avatar/parameters/FR_Active", "T", [True])
    assert msgs[-6] == ("/avatar/parameters/FR_Active", "F", [False])
    assert all(m[2] == [0.0] for m in msgs[-5:])  # 終了時は全値 0
    assert len(msgs) == 1 + frames * 5 + 1 + 5


def test_run_selftest_sends_shutdown_on_interrupt(receiver: Receiver):
    params = load_params(CONFIG_DIR / "params.example.json").params_up_to(1)

    def boom(_: float) -> None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run_selftest(OscSender("127.0.0.1", receiver.port), params, pattern="ramp", duration=5, sleep=boom)
    msgs = receiver.drain()
    assert ("/avatar/parameters/FR_Active", "F", [False]) in msgs


def _settings(tmp_path: Path, port: int, max_phase: int = 2) -> Path:
    data = json.loads((CONFIG_DIR / "settings.example.json").read_text(encoding="utf-8"))
    data["osc"]["port"] = port
    data["mapping"]["max_phase"] = max_phase
    (tmp_path / "params.example.json").write_text(
        (CONFIG_DIR / "params.example.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_cli_selftest_covers_all_params(tmp_path: Path, receiver: Receiver, capsys: pytest.CaptureFixture[str]):
    settings = _settings(tmp_path, receiver.port)
    assert main(["selftest", "--settings", str(settings), "--pattern", "sine", "--duration", "0.2"]) == 0
    addresses = {m[0].rsplit("/", 1)[1] for m in receiver.drain()}
    expected = {p.avatar for p in load_params(CONFIG_DIR / "params.example.json").params} | {"FR_Active"}
    assert addresses == expected
    assert "完了" in capsys.readouterr().out


def test_cli_selftest_respects_max_phase(tmp_path: Path, receiver: Receiver):
    settings = _settings(tmp_path, receiver.port, max_phase=1)
    assert main(["selftest", "--settings", str(settings), "--duration", "0.1"]) == 0
    floats = {m[0].rsplit("/", 1)[1] for m in receiver.drain() if m[1] == "f"}
    assert floats == {"FR_AngleX", "FR_AngleY", "FR_EyeLOpen", "FR_EyeROpen", "FR_MouthOpenY"}


def test_cli_selftest_rejects_bad_rate(tmp_path: Path, receiver: Receiver):
    assert main(["selftest", "--settings", str(_settings(tmp_path, receiver.port)), "--rate", "0"]) == 2
