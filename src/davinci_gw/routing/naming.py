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


def direct_source_name(message: str, channel: str) -> str:
    """返回 EcuC/CanIf 接收对象名称。"""
    return f"GWT_{safe_name(message)}_{channel_token(channel)}_Rx"


def direct_target_name(message: str, channel: str) -> str:
    """返回 EcuC/CanIf 发送对象名称。"""
    return f"GWT_{safe_name(message)}_{channel_token(channel)}_Tx"


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
    """返回 ComGwDestination 名称。"""
    return f"ComGwDestination_{safe_name(target_signal)}_{safe_name(target_network)}"
