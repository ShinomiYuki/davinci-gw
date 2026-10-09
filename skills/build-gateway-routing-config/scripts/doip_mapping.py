"""DoIP→CAN 映射页的七列契约；不生成 AUTOSAR 配置。"""

import re

HEADERS = ("Tester", "logicalAddress", "RouterType", "CANBus", "RequestCanId", "RespCanId", "Functional")
TESTER_TYPES = {"0x0E80": "OBD", "0x0E81": "OBD_Remote", "0x0F00": "OTA", "0x0F01": "OTA_Remote"}


def hex_value(value, maximum, width=0):
    if isinstance(value, bool):
        raise ValueError("地址不能是布尔值")
    if isinstance(value, int):
        number = value
    elif isinstance(value, str) and re.fullmatch(r"(?:0x)?[0-9A-Fa-f]+", value.strip(), re.I):
        number = int(value.strip(), 16)
    else:
        raise ValueError(f"无效的十六进制地址：{value!r}")
    if not 0 <= number <= maximum:
        raise ValueError(f"地址超出范围：{value!r}")
    return f"0x{number:0{width}X}"


def normalize_mapping(record, channels):
    tester = hex_value(record.get("Tester"), 0xFFFF, 4)
    if tester not in TESTER_TYPES:
        raise ValueError(f"没有定义 Tester {tester} 的 RouterType")
    if record.get("RouterType") != TESTER_TYPES[tester]:
        raise ValueError(f"Tester {tester} 与 RouterType 不一致")
    channel = str(record.get("CANBus", "")).strip().upper()
    if channel not in channels:
        raise ValueError(f"CANBus {channel!r} 未在引用数据中定义")
    functional = str(record.get("Functional") or "").strip().upper()
    if functional not in {"", "YES"}:
        raise ValueError("Functional 只能为空或 YES")
    response = record.get("RespCanId")
    if functional == "YES":
        if response not in (None, "", "/", "NA"):
            raise ValueError("功能寻址不能填写 RespCanId")
        response = ""
    else:
        response = hex_value(response, 0x1FFFFFFF)
    return {
        "Tester": tester,
        "logicalAddress": hex_value(record.get("logicalAddress"), 0xFFFF, 4),
        "RouterType": TESTER_TYPES[tester],
        "CANBus": channel,
        "RequestCanId": hex_value(record.get("RequestCanId"), 0x1FFFFFFF),
        "RespCanId": response,
        "Functional": functional,
    }


def mapping_key(record):
    return record["logicalAddress"], record["Tester"], record["CANBus"]


def mapping_values(record):
    return [record.get(header, "") for header in HEADERS]
