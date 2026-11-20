from __future__ import annotations

import json
import logging
import socket
from pathlib import Path

import pytest
from mock_vts import ERROR_TOKEN_DENIED, MockVts

from flatrig_bridge.config import VtsSettings
from flatrig_bridge.mapping import range_mismatch
from flatrig_bridge.vts_client import (
    TokenStore,
    VtsApiError,
    VtsAuthDenied,
    VtsClient,
    VtsConnectionError,
    VtsTimeout,
    parse_input_parameter_list,
    parse_parameter_list,
)


def make_settings(port: int, token_file: Path, timeout: float = 1.0) -> VtsSettings:
    return VtsSettings(
        host="127.0.0.1",
        port=port,
        plugin_name="FlatRig Bridge",
        plugin_developer="FLA_fluffy",
        token_file=token_file,
        source="live2d",
        poll_hz=30.0,
        request_timeout_sec=timeout,
    )


@pytest.fixture
async def vts():
    server = await MockVts().start()
    yield server
    await server.stop()


@pytest.fixture
def token_file(tmp_path: Path) -> Path:
    return tmp_path / "config" / ".vts_token.json"


# --- 認証 -------------------------------------------------------------------


async def test_first_auth_requests_and_saves_token(vts: MockVts, token_file: Path):
    async with VtsClient(make_settings(vts.port, token_file)) as client:
        await client.authenticate()
    assert vts.types() == ["AuthenticationTokenRequest", "AuthenticationRequest"]
    assert json.loads(token_file.read_text(encoding="utf-8")) == {"authenticationToken": "mock-token-0001"}


async def test_saved_token_is_reused(vts: MockVts, token_file: Path):
    settings = make_settings(vts.port, token_file)
    async with VtsClient(settings) as client:
        await client.authenticate()
    vts.received.clear()
    async with VtsClient(settings) as client:
        await client.authenticate()
    assert vts.types() == ["AuthenticationRequest"]


async def test_invalid_saved_token_is_discarded_and_renewed(vts: MockVts, token_file: Path):
    TokenStore(token_file).save("stale-token")
    vts.issue_token = "fresh-token"
    async with VtsClient(make_settings(vts.port, token_file)) as client:
        await client.authenticate()
    assert vts.types() == ["AuthenticationRequest", "AuthenticationTokenRequest", "AuthenticationRequest"]
    assert TokenStore(token_file).load() == "fresh-token"


async def test_plugin_name_and_developer_are_consistent(vts: MockVts, token_file: Path):
    async with VtsClient(make_settings(vts.port, token_file)) as client:
        await client.authenticate()
    for msg in vts.received:
        assert msg["apiName"] == "VTubeStudioPublicAPI"
        assert msg["data"]["pluginName"] == "FlatRig Bridge"
        assert msg["data"]["pluginDeveloper"] == "FLA_fluffy"
    ids = [m["requestID"] for m in vts.received]
    assert len(set(ids)) == len(ids)


async def test_denied_token_request(vts: MockVts, token_file: Path):
    vts.deny_token_request = True
    async with VtsClient(make_settings(vts.port, token_file)) as client:
        with pytest.raises(VtsAuthDenied) as exc:
            await client.authenticate()
    assert str(ERROR_TOKEN_DENIED) in str(exc.value)
    assert not token_file.exists()


async def test_token_not_logged(vts: MockVts, token_file: Path, caplog: pytest.LogCaptureFixture):
    vts.issue_token = "SUPER-SECRET-TOKEN"
    caplog.set_level(logging.DEBUG)
    async with VtsClient(make_settings(vts.port, token_file)) as client:
        await client.authenticate()
        await client.get_parameters()
        assert "SUPER-SECRET-TOKEN" not in repr(client.token_store)
    # モックサーバ側(websockets.server)のログはテスト用なので対象外
    client_logs = [r.getMessage() for r in caplog.records if not r.name.startswith("websockets.server")]
    assert client_logs
    assert not any("SUPER-SECRET-TOKEN" in m for m in client_logs)


def test_corrupt_token_file_is_discarded(token_file: Path):
    token_file.parent.mkdir(parents=True)
    token_file.write_text("{broken", encoding="utf-8")
    assert TokenStore(token_file).load() is None
    assert not token_file.exists()


# --- パラメータ取得 ------------------------------------------------------------


async def test_get_parameters(vts: MockVts, token_file: Path):
    async with VtsClient(make_settings(vts.port, token_file)) as client:
        await client.authenticate()
        snap = await client.get_parameters()
    assert snap.model_loaded
    angle = snap.params["ParamAngleX"]
    assert (angle.value, angle.min, angle.max, angle.default) == (12.0, -30.0, 30.0, 0.0)
    assert len(snap.params) == 5


async def test_model_not_loaded(vts: MockVts, token_file: Path):
    vts.model_loaded = False
    async with VtsClient(make_settings(vts.port, token_file)) as client:
        await client.authenticate()
        snap = await client.get_parameters()
    assert not snap.model_loaded
    assert snap.params == {}


async def test_api_error_is_raised(vts: MockVts, token_file: Path):
    vts.api_errors["Live2DParameterListRequest"] = (1, "Internal server error")
    async with VtsClient(make_settings(vts.port, token_file)) as client:
        await client.authenticate()
        with pytest.raises(VtsApiError) as exc:
            await client.get_parameters()
    assert exc.value.error_id == 1
    assert exc.value.request_type == "Live2DParameterListRequest"


def test_parse_is_robust_to_missing_fields():
    data = {
        "parameters": [
            {"name": "ParamA", "value": 0.5},  # min/max なし
            {"name": "ParamB", "value": "x", "min": 0, "max": 1},  # 値が数値でない → 捨てる
            {"value": 1.0},  # 名前なし → 捨てる
            {"name": "ParamC", "value": 1, "min": 0, "max": float("nan")},  # NaN は None 扱い
            "not-a-dict",
        ]
    }  # modelLoaded なし → パラメータの有無で判断
    snap = parse_parameter_list(data)
    assert snap.model_loaded
    assert set(snap.params) == {"ParamA", "ParamC"}
    assert snap.params["ParamA"].min is None
    assert snap.params["ParamC"].max is None


def test_parse_empty():
    assert not parse_parameter_list({}).model_loaded
    assert not parse_parameter_list({"modelLoaded": True, "parameters": []}).model_loaded


# --- 接続エラー ---------------------------------------------------------------


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def test_connection_refused(token_file: Path):
    with pytest.raises(VtsConnectionError):
        await VtsClient(make_settings(_free_port(), token_file)).connect()


async def test_timeout_when_no_response(vts: MockVts, token_file: Path):
    vts.silent_types.add("Live2DParameterListRequest")
    async with VtsClient(make_settings(vts.port, token_file, timeout=0.3)) as client:
        await client.authenticate()
        with pytest.raises(VtsTimeout):
            await client.get_parameters()


async def test_server_disconnect(vts: MockVts, token_file: Path):
    vts.close_on_types.add("Live2DParameterListRequest")
    async with VtsClient(make_settings(vts.port, token_file)) as client:
        await client.authenticate()
        with pytest.raises(VtsConnectionError):
            await client.get_parameters()
        assert not client.connected


async def test_reconnect_after_disconnect_reuses_token(vts: MockVts, token_file: Path):
    settings = make_settings(vts.port, token_file)
    vts.close_on_types.add("Live2DParameterListRequest")
    client = VtsClient(settings)
    await client.connect()
    await client.authenticate()
    with pytest.raises(VtsConnectionError):
        await client.get_parameters()
    vts.close_on_types.clear()
    vts.received.clear()
    await client.connect()
    await client.authenticate()
    snap = await client.get_parameters()
    await client.close()
    assert snap.model_loaded
    assert vts.types() == ["AuthenticationRequest", "Live2DParameterListRequest"]


# --- 範囲の食い違い判定(SPEC §3.2) -------------------------------------------


@pytest.mark.parametrize(
    ("vts_range", "expected"),
    [
        ((-30, 30), False),
        ((-25, 35), False),  # 中心ずれ 5 < 15
        ((-15, 45), True),  # 中心ずれ 15 = 25%
        ((-60, 60), True),  # 幅 2 倍
        ((-15, 15), True),  # 幅 半分
        ((-40, 40), False),
        ((1, 1), True),  # 幅ゼロ
    ],
)
def test_range_mismatch(vts_range, expected):
    assert range_mismatch(-30, 30, *vts_range) is expected


@pytest.mark.parametrize(("host", "expected"), [("localhost", "127.0.0.1"), ("LocalHost", "127.0.0.1"), ("::1", "::1")])
def test_localhost_is_connected_via_ipv4(token_file: Path, host: str, expected: str):
    settings = make_settings(8001, token_file)
    settings = type(settings)(**{**settings.__dict__, "host": host})
    assert VtsClient(settings).url == f"ws://{expected}:8001"


def test_parse_input_parameter_list():
    data = {
        "defaultParameters": [
            {"name": "HandRightFound", "value": 1, "min": 0, "max": 1},
            {"name": "HandRightPositionX", "value": -3.5},
            {"name": "Broken"},
            "junk",
        ],
        "customParameters": [{"name": "HandRightFound", "value": 0}, {"name": "MyParam", "value": 0.25}],
    }
    assert parse_input_parameter_list(data) == {"HandRightFound": 1.0, "HandRightPositionX": -3.5, "MyParam": 0.25}
    assert parse_input_parameter_list({}) == {}
