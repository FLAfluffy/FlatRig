"""VRChat からの OSC 受信(docs/SPEC.md §2, §4.4)。

用途は /avatar/change の検知(全値の再送)と、osc-listen によるデバッグ表示のみ。
待受は 127.0.0.1 だけ(外部ネットワークは待ち受けない。G7)。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import AsyncIOOSCUDPServer

log = logging.getLogger(__name__)

LISTEN_HOST = "127.0.0.1"
AVATAR_CHANGE = "/avatar/change"


class OscListener:
    """UDP で OSC を受け、アドレスごとのハンドラか既定ハンドラを呼ぶ。"""

    def __init__(
        self,
        port: int,
        *,
        on_avatar_change: Callable[[], None] | None = None,
        on_message: Callable[[str, tuple[Any, ...]], None] | None = None,
    ):
        self.port = port
        self._transport: asyncio.BaseTransport | None = None
        self._dispatcher = Dispatcher()
        if on_avatar_change is not None:
            # avatarId は個人を特定しうるため、値は使わずログにも出さない
            self._dispatcher.map(AVATAR_CHANGE, lambda _addr, *_args: on_avatar_change())
        if on_message is not None:
            self._dispatcher.set_default_handler(lambda addr, *args: on_message(addr, args))

    @property
    def listening(self) -> bool:
        return self._transport is not None

    async def start(self) -> bool:
        """待受を始める。ポートが使用中なら警告して False(ブリッジ本体は動き続ける。SPEC §9 U13)。"""
        server = AsyncIOOSCUDPServer((LISTEN_HOST, self.port), self._dispatcher, asyncio.get_running_loop())
        try:
            self._transport, _ = await server.create_serve_endpoint()
        except OSError as exc:
            log.warning(
                "OSC 受信ポート %d を開けません(%s)。他の OSC ソフトと競合している場合は "
                "settings の osc.listen_enabled を false にしてください",
                self.port,
                exc.strerror or exc,
            )
            return False
        log.info("OSC 受信を開始しました: %s:%d", LISTEN_HOST, self.port)
        return True

    def stop(self) -> None:
        if self._transport is not None:
            self._transport.close()
            self._transport = None
