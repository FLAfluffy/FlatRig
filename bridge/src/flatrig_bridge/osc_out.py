"""VRChat への OSC 送信(docs/SPEC.md §4.4)。宛先はローカルのみ(G7)。"""

from __future__ import annotations

from collections.abc import Mapping

from pythonosc.osc_message_builder import OscMessageBuilder
from pythonosc.udp_client import UDPClient

from .config import ALLOWED_OSC_HOSTS, OscSettings


class OscSender:
    """float は float32(型タグ f)、bool は T/F で 1 メッセージずつ送る(バンドルは使わない)。"""

    def __init__(self, host: str, port: int, address_prefix: str = "/avatar/parameters/"):
        if host.strip().lower() not in ALLOWED_OSC_HOSTS:
            raise ValueError(f"OSC の送信先はローカルのみです: {host!r} (G7)")
        self.host = host
        self.port = port
        self.address_prefix = address_prefix
        self._client = UDPClient(host, port)
        self.sent_count = 0

    @classmethod
    def from_settings(cls, osc: OscSettings) -> OscSender:
        return cls(osc.host, osc.port, osc.address_prefix)

    def _send(self, name: str, value: float | bool, arg_type: str) -> None:
        builder = OscMessageBuilder(address=f"{self.address_prefix}{name}")
        builder.add_arg(value, arg_type=arg_type)
        self._client.send(builder.build())
        self.sent_count += 1

    def send_float(self, name: str, value: float) -> None:
        self._send(name, float(value), OscMessageBuilder.ARG_TYPE_FLOAT)

    def send_bool(self, name: str, value: bool) -> None:
        arg_type = OscMessageBuilder.ARG_TYPE_TRUE if value else OscMessageBuilder.ARG_TYPE_FALSE
        self._send(name, bool(value), arg_type)

    def send_floats(self, values: Mapping[str, float]) -> None:
        for name, value in values.items():
            self.send_float(name, value)
