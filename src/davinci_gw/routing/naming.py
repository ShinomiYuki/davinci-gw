"""集中提供确定性的网关对象命名和通道标识规则。"""

from __future__ import annotations

import re

INVALID_NAME = re.compile(r"[^A-Za-z0-9_]")


def safe_name(value: str) -> str:
    """将外部标识规范为 AUTOSAR SHORT-NAME 可安全使用的片段。"""
    cleaned = INVALID_NAME.sub("_", value.strip())
    return cleaned if cleaned and not cleaned[0].isdigit() else f"N_{cleaned}"


def channel_token(channel: str) -> str:
    """沿用真实工程 GWT 命名习惯，将 CHCAN 等通道缩写为 CH。"""
    cleaned = safe_name(channel)
    return cleaned[:-3].rstrip("_") if cleaned.upper().endswith("CAN") and len(cleaned) > 3 else cleaned


def direct_endpoint_name(module: str, message: str, can_id: int, channel: str, direction: str) -> str:
    """只用于新建 CanIf/EcuC 对象，完整保留通道和 CAN ID。

    命名必须编码**完整身份**：同一报文名在同一通道上可以对应多个 CAN ID，
    名字里少了 CAN ID 就会让两条合法路由撞成同名对象。历史上
    ``ComGwDestination`` 就因为只编码目标网段而出现过这种重名。
    """
    return f"GWT_{module}_{safe_name(message)}_{can_id:X}_{safe_name(channel)}_{direction}"


def pdur_path_name(message: str, can_id: int, channel: str) -> str:
    """返回一对多 PduR RoutingPath 名称，CAN ID 使用无前缀十六进制。"""
    return f"GWT_{safe_name(message)}_{can_id:X}_{channel_token(channel)}"


def pdur_leg_name(message: str, can_id: int, channel: str) -> str:
    """返回 PduR 源或目标 PDU 名称。"""
    return f"{safe_name(message)}_{can_id:X}_{channel_token(channel)}"


def com_mapping_name(source_signal: str, source_network: str) -> str:
    """沿用旧工具 GWT_Sig 命名并用源网段避免跨网段冲突。"""
    return f"GWT_Sig_{safe_name(source_signal)}_{safe_name(source_network)}"


def com_source_name(source_signal: str, source_network: str) -> str:
    """返回 ComGwSource 名称。"""
    return f"ComGwSource_{safe_name(source_signal)}_{safe_name(source_network)}"


def com_destination_name(target_signal: str, target_network: str) -> str:
    """返回 ComGwDestination 名称。

    名称只编码目标网段：同一目标网段里的不同目标信号要靠
    ``unique_destination_name`` 追加序号区分，否则同一父容器下会出现
    两个同名对象。
    """
    return f"ComGwDestination_to_{safe_name(target_network)}"


def unique_destination_name(base: str, used: set[str]) -> str:
    """在一条 Mapping 内给出不重名的 Destination 名。

    同名时按 ``_001``、``_002`` 追加序号——真实工程里的 ComGwDestination
    也用这种带序号的写法区分同一条 Mapping 下的多个目标。
    """
    if base not in used:
        used.add(base)
        return base
    index = 1
    while f"{base}_{index:03d}" in used:
        index += 1
    name = f"{base}_{index:03d}"
    used.add(name)
    return name
