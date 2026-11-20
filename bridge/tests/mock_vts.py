"""VTube Studio 公開 API 互換のモックサーバ(テスト用)。

認証・パラメータ一覧に加え、拒否・APIError・モデル未ロード・切断・無応答を再現できる。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from websockets.asyncio.server import Server, ServerConnection, serve

DEFAULT_PARAMS: list[dict[str, Any]] = [
    {"name": "ParamAngleX", "addedBy": "VTubeStudio", "value": 12.0, "min": -30.0, "max": 30.0, "defaultValue": 0.0},
    {"name": "ParamAngleY", "addedBy": "VTubeStudio", "value": -6.0, "min": -30.0, "max": 30.0, "defaultValue": 0.0},
    {"name": "ParamEyeLOpen", "addedBy": "VTubeStudio", "value": 1.0, "min": 0.0, "max": 1.0, "defaultValue": 1.0},
    {"name": "ParamEyeROpen", "addedBy": "VTubeStudio", "value": 0.5, "min": 0.0, "max": 1.0, "defaultValue": 1.0},
    {"name": "ParamMouthOpenY", "addedBy": "VTubeStudio", "value": 0.0, "min": 0.0, "max": 1.0, "defaultValue": 0.0},
]

ERROR_TOKEN_DENIED = 50  # テスト内でのみ使う識別子(実機の値は dump/実行時に確認)


@dataclass
class MockVts:
    """振る舞いはテストから属性で切り替える。"""

    valid_tokens: set[str] = field(default_factory=set)
    issue_token: str = "mock-token-0001"
    deny_token_request: bool = False
    model_loaded: bool = True
    parameters: list[dict[str, Any]] = field(default_factory=lambda: [dict(p) for p in DEFAULT_PARAMS])
    api_errors: dict[str, tuple[int, str]] = field(default_factory=dict)  # messageType → (errorID, message)
    silent_types: set[str] = field(default_factory=set)  # 応答しない messageType
    close_on_types: set[str] = field(default_factory=set)  # 受信したら切断する messageType
    input_parameters: list[dict[str, Any]] | None = None  # None なら InputParameterListRequest は未対応(APIError)
    model_name: str = "SecretModelName"  # クライアントが保存しないことを確かめるための値
    received: list[dict[str, Any]] = field(default_factory=list)
    _server: Server | None = None

    @property
    def port(self) -> int:
        assert self._server is not None
        return self._server.sockets[0].getsockname()[1]

    async def start(self) -> MockVts:
        self._server = await serve(self._handle, "127.0.0.1", 0)
        return self

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    def types(self) -> list[str]:
        return [m.get("messageType") for m in self.received]

    async def _handle(self, ws: ServerConnection) -> None:
        authed = False
        async for raw in ws:
            msg = json.loads(raw)
            self.received.append(msg)
            mtype = msg.get("messageType")
            if mtype in self.close_on_types:
                await ws.close()
                return
            if mtype in self.silent_types:
                continue
            if mtype in self.api_errors:
                error_id, text = self.api_errors[mtype]
                await ws.send(self._reply(msg, "APIError", {"errorID": error_id, "message": text}))
                continue
            data = msg.get("data") or {}
            if mtype == "AuthenticationTokenRequest":
                if self.deny_token_request:
                    body = {"errorID": ERROR_TOKEN_DENIED, "message": "User has denied API access for your plugin."}
                    await ws.send(self._reply(msg, "APIError", body))
                    continue
                self.valid_tokens.add(self.issue_token)
                await ws.send(
                    self._reply(msg, "AuthenticationTokenResponse", {"authenticationToken": self.issue_token})
                )
            elif mtype == "AuthenticationRequest":
                authed = data.get("authenticationToken") in self.valid_tokens
                body = {"authenticated": authed, "reason": "ok" if authed else "Token invalid."}
                await ws.send(self._reply(msg, "AuthenticationResponse", body))
            elif mtype == "Live2DParameterListRequest":
                if not authed:
                    await ws.send(self._reply(msg, "APIError", {"errorID": 8, "message": "Not authenticated."}))
                    continue
                body = {
                    "modelLoaded": self.model_loaded,
                    "modelName": self.model_name if self.model_loaded else "",
                    "modelID": "secret-model-id" if self.model_loaded else "",
                    "parameters": self.parameters if self.model_loaded else [],
                }
                await ws.send(self._reply(msg, "Live2DParameterListResponse", body))
            elif mtype == "InputParameterListRequest" and self.input_parameters is not None:
                body = {
                    "modelLoaded": self.model_loaded,
                    "defaultParameters": self.input_parameters,
                    "customParameters": [],
                }
                await ws.send(self._reply(msg, "InputParameterListResponse", body))
            else:
                await ws.send(self._reply(msg, "APIError", {"errorID": 2, "message": f"Unknown {mtype}"}))

    @staticmethod
    def _reply(request: dict[str, Any], message_type: str, data: dict[str, Any]) -> str:
        return json.dumps(
            {
                "apiName": "VTubeStudioPublicAPI",
                "apiVersion": "1.0",
                "requestID": request.get("requestID"),
                "messageType": message_type,
                "data": data,
            }
        )
