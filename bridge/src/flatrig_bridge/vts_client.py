"""VTube Studio 公開 API の最小クライアント(docs/SPEC.md §4.2)。

pyvts には依存せず websockets で直接実装する。扱うのはパラメータの名前と数値だけで、
モデル名・モデル ID などのモデル情報は保持しない(G2)。認証トークンはログに出さない。
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidURI

from .config import VtsSettings

log = logging.getLogger(__name__)
# websockets は DEBUG で送受信フレームの中身(認証トークンを含む)をログに出すため、
# クライアントの接続ログは常に INFO 以上に抑える
_ws_log = logging.getLogger(f"{__name__}.ws")
_ws_log.setLevel(logging.INFO)

API_NAME = "VTubeStudioPublicAPI"
API_VERSION = "1.0"
# トークン要求は VTS 上で人間が Allow を押すまで応答が返らないため、通常より長く待つ
TOKEN_PROMPT_TIMEOUT_SEC = 120.0
# 接続の待ち時間。Windows では拒否された接続の失敗にも数秒かかるため、リクエストより長めにとる
CONNECT_TIMEOUT_SEC = 5.0


class VtsError(Exception):
    """VTS 連携のエラーの基底。"""


class VtsConnectionError(VtsError):
    """接続できない/接続が切れた。"""


class VtsTimeout(VtsError):
    """応答が時間内に返らない。"""


class VtsApiError(VtsError):
    """VTS が APIError を返した。"""

    def __init__(self, error_id: int | None, message: str, request_type: str):
        self.error_id = error_id
        self.api_message = message
        self.request_type = request_type
        super().__init__(f"{request_type}: APIError {error_id}: {message}")


class VtsAuthDenied(VtsError):
    """プラグインの許可が拒否された、または認証に失敗した。"""


@dataclass(frozen=True)
class VtsParam:
    name: str
    value: float
    min: float | None
    max: float | None
    default: float | None


@dataclass(frozen=True)
class ParameterSnapshot:
    model_loaded: bool
    params: dict[str, VtsParam]


class TokenStore:
    """認証トークンのファイル保存。中身は repr やログに出さない。"""

    def __init__(self, path: Path):
        self.path = Path(path)

    def __repr__(self) -> str:
        return f"TokenStore({self.path})"

    def load(self) -> str | None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError):
            log.warning("トークンファイルを読めないため破棄します: %s", self.path)
            self.clear()
            return None
        token = data.get("authenticationToken") if isinstance(data, dict) else None
        return token if isinstance(token, str) and token else None

    def save(self, token: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps({"authenticationToken": token}), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:  # Windows では効かないことがある
            pass
        os.replace(tmp, self.path)

    def clear(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


def _to_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def parse_parameter_list(data: dict[str, Any]) -> ParameterSnapshot:
    """Live2DParameterListResponse の data を解釈する。

    フィールド名は実機の応答を正とする(SPEC §9 U3)。欠けた項目に強くし、
    name と value が無いエントリは捨てる。モデル名・モデル ID は読まない。
    """
    raw = data.get("parameters")
    entries = raw if isinstance(raw, list) else []
    params: dict[str, VtsParam] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        value = _to_float(entry.get("value"))
        if not isinstance(name, str) or not name or value is None:
            continue
        params[name] = VtsParam(
            name=name,
            value=value,
            min=_to_float(entry.get("min")),
            max=_to_float(entry.get("max")),
            default=_to_float(entry.get("defaultValue")),
        )
    loaded = data.get("modelLoaded")
    model_loaded = loaded if isinstance(loaded, bool) else bool(params)
    return ParameterSnapshot(model_loaded=model_loaded and bool(params), params=params)


def parse_input_parameter_list(data: dict[str, Any]) -> dict[str, float]:
    """InputParameterListResponse の data(トラッキングの入力値)を 名前 → 値 にする。

    defaultParameters(VTS 組み込み。手の HandRightFound など)と customParameters の両方を読む。
    """
    values: dict[str, float] = {}
    for key in ("defaultParameters", "customParameters"):
        raw = data.get(key)
        for entry in raw if isinstance(raw, list) else []:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            value = _to_float(entry.get("value"))
            if isinstance(name, str) and name and value is not None:
                values.setdefault(name, value)
    return values


class VtsClient:
    """1 本の WebSocket で VTS と通信する。リクエストは 1 件ずつ直列に送る。"""

    def __init__(self, settings: VtsSettings, token_store: TokenStore | None = None):
        self.settings = settings
        self.token_store = token_store or TokenStore(settings.token_file)
        self._ws: ClientConnection | None = None
        self._ids = itertools.count(1)
        self._lock = asyncio.Lock()

    @property
    def url(self) -> str:
        # VTS は IPv4(0.0.0.0)で待ち受ける。Windows では localhost が先に ::1 に解決され、
        # 失敗を待つ間にタイムアウトするため、localhost は 127.0.0.1 に置き換える
        host = "127.0.0.1" if self.settings.host.strip().lower() == "localhost" else self.settings.host
        return f"ws://{host}:{self.settings.port}"

    @property
    def connected(self) -> bool:
        return self._ws is not None

    async def __aenter__(self) -> VtsClient:
        await self.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def connect(self) -> None:
        try:
            self._ws = await asyncio.wait_for(
                connect(self.url, max_size=2**22, open_timeout=None, logger=_ws_log),
                timeout=max(CONNECT_TIMEOUT_SEC, self.settings.request_timeout_sec),
            )
        except TimeoutError as exc:
            raise VtsConnectionError(f"{self.url} への接続がタイムアウトしました") from exc
        except (OSError, InvalidHandshake, InvalidURI) as exc:
            raise VtsConnectionError(f"{self.url} に接続できません({type(exc).__name__})") from exc

    async def close(self) -> None:
        ws, self._ws = self._ws, None
        if ws is not None:
            await ws.close()

    async def request(
        self, message_type: str, data: dict[str, Any] | None = None, timeout: float | None = None
    ) -> dict:
        """リクエストを送り、同じ requestID の応答の data を返す。"""
        if self._ws is None:
            raise VtsConnectionError("未接続です")
        request_id = f"flatrig-{next(self._ids)}"
        payload = {
            "apiName": API_NAME,
            "apiVersion": API_VERSION,
            "requestID": request_id,
            "messageType": message_type,
        }
        if data is not None:
            payload["data"] = data
        limit = timeout if timeout is not None else self.settings.request_timeout_sec
        async with self._lock:
            try:
                await self._ws.send(json.dumps(payload))
                response = await asyncio.wait_for(self._recv_matching(request_id), timeout=limit)
            except TimeoutError as exc:
                raise VtsTimeout(f"{message_type}: {limit:.1f} 秒以内に応答がありません") from exc
            except ConnectionClosed as exc:
                self._ws = None
                raise VtsConnectionError(f"{message_type}: 接続が切れました") from exc
        body = response.get("data")
        body = body if isinstance(body, dict) else {}
        if response.get("messageType") == "APIError":
            error_id = body.get("errorID")
            raise VtsApiError(
                error_id if isinstance(error_id, int) else None,
                str(body.get("message", "")),
                message_type,
            )
        return body

    async def _recv_matching(self, request_id: str) -> dict:
        assert self._ws is not None
        while True:
            raw = await self._ws.recv()
            try:
                message = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                log.debug("JSON でない応答を無視しました")
                continue
            if isinstance(message, dict) and message.get("requestID") == request_id:
                return message
            log.debug("requestID が一致しない応答を無視しました")

    def _plugin_fields(self) -> dict[str, str]:
        return {"pluginName": self.settings.plugin_name, "pluginDeveloper": self.settings.plugin_developer}

    async def _authenticate_with(self, token: str) -> bool:
        body = await self.request("AuthenticationRequest", {**self._plugin_fields(), "authenticationToken": token})
        return body.get("authenticated") is True

    async def authenticate(self) -> None:
        """保存済みトークン → 無効なら破棄して新規要求(VTS 上で人間が Allow を押す)。"""
        token = self.token_store.load()
        if token is not None:
            if await self._authenticate_with(token):
                log.info("VTS 認証に成功しました(保存済みトークン)")
                return
            log.info("保存済みトークンが無効になっていたため破棄します")
            self.token_store.clear()

        log.info("VTube Studio の画面でプラグイン「%s」を許可してください", self.settings.plugin_name)
        try:
            body = await self.request(
                "AuthenticationTokenRequest", self._plugin_fields(), timeout=TOKEN_PROMPT_TIMEOUT_SEC
            )
        except VtsApiError as exc:
            raise VtsAuthDenied(f"トークン要求が拒否されました(APIError {exc.error_id})") from exc
        token = body.get("authenticationToken")
        if not isinstance(token, str) or not token:
            raise VtsAuthDenied("トークン要求の応答にトークンが含まれていません")
        self.token_store.save(token)
        if not await self._authenticate_with(token):
            self.token_store.clear()
            raise VtsAuthDenied("取得したトークンで認証できませんでした")
        log.info("VTS 認証に成功しました(新規トークン)")

    async def get_parameters(self) -> ParameterSnapshot:
        body = await self.request("Live2DParameterListRequest")
        return parse_parameter_list(body)

    async def get_input_parameters(self) -> dict[str, float]:
        """トラッキングの入力値(手の位置など。SPEC §4.9)。"""
        body = await self.request("InputParameterListRequest")
        return parse_input_parameter_list(body)
