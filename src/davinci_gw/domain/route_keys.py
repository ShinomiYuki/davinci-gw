"""直接报文路由和信号路由的稳定、不可变、可哈希唯一键。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DirectRouteKey:
    """一条源报文到一条目标报文的一对一路由标识。"""

    source_message_name: str
    source_can_id: int
    source_channel: str
    target_message_name: str
    target_can_id: int
    target_channel: str


@dataclass(frozen=True, slots=True)
class SignalRouteKey:
    """一条源信号到一条目标信号的一对一路由标识。"""

    source_network: str
    source_message_name: str
    source_signal_name: str
    target_network: str
    target_message_name: str
    target_signal_name: str
