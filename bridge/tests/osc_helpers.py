"""テスト用の OSC 解析と非同期 UDP 受信。"""

from __future__ import annotations

import asyncio
import struct


def _pad_end(data: bytes, start: int) -> int:
    end = data.index(b"\x00", start)
    return (end + 4) & ~3


def parse_osc(data: bytes) -> tuple[str, str, list]:
    """1 メッセージを (address, 型タグ, 引数) に分解する(f / i / s / T / F)。"""
    addr_end = _pad_end(data, 0)
    address = data[: data.index(b"\x00")].decode()
    tag_end = _pad_end(data, addr_end)
    tags = data[addr_end : data.index(b"\x00", addr_end)].decode()
    assert tags.startswith(",")
    args, pos = [], tag_end
    for t in tags[1:]:
        if t == "f":
            args.append(struct.unpack(">f", data[pos : pos + 4])[0])
            pos += 4
        elif t == "i":
            args.append(struct.unpack(">i", data[pos : pos + 4])[0])
            pos += 4
        elif t == "s":
            end = _pad_end(data, pos)
            args.append(data[pos : data.index(b"\x00", pos)].decode())
            pos = end
        elif t in "TF":
            args.append(t == "T")
        else:
            raise AssertionError(f"unexpected tag {t}")
    return address, tags[1:], args


class AsyncCollector(asyncio.DatagramProtocol):
    """受信した OSC メッセージを順に溜める。"""

    def __init__(self) -> None:
        self.messages: list[tuple[str, str, list]] = []
        self.transport: asyncio.DatagramTransport | None = None
        self._changed = asyncio.Event()

    def connection_made(self, transport) -> None:
        self.transport = transport

    def datagram_received(self, data: bytes, addr) -> None:
        self.messages.append(parse_osc(data))
        self._changed.set()

    @property
    def port(self) -> int:
        assert self.transport is not None
        return self.transport.get_extra_info("sockname")[1]

    def values(self, name: str) -> list:
        return [m[2][0] for m in self.messages if m[0] == f"/avatar/parameters/{name}"]

    async def wait_for(self, predicate, timeout: float = 5.0) -> None:
        async def loop() -> None:
            while not predicate():
                self._changed.clear()
                await self._changed.wait()

        await asyncio.wait_for(loop(), timeout)

    def close(self) -> None:
        if self.transport is not None:
            self.transport.close()


async def start_collector() -> AsyncCollector:
    loop = asyncio.get_running_loop()
    _, proto = await loop.create_datagram_endpoint(AsyncCollector, local_addr=("127.0.0.1", 0))
    return proto
