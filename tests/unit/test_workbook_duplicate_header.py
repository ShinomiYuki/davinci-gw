"""第二十三轮：同一张表里出现重复的必需表头时必须阻断，不能静默取错列。

此前 `_header_map` 用「后出现的表头覆盖先出现的」建立列映射，重复表头既不报
错也不提示，用户填在第一列的值被静默忽略，工具改按另一列取值生成路由。行级
重复（重复 CAN 通道名、重复路由键）本来就有报错防护，列级却没有。
"""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook

from davinci_gw.input.workbook_reader import read_workbook
from davinci_gw.input.workbook_schema import (
    DIRECT_HEADERS,
    REFERENCE_HEADERS,
    SIGNAL_HEADERS,
)

_DIRECT_ROW = {
    "源网段报文名称": "SRC_MSG",
    "源网段报文CANID": "0x100",
    "源网段报文Length": 8,
    "源网段报文类型": "STANDARD_CAN",
    "源网段CAN通道": "SRC_CAN",
    "源网段RxIndicationUL": "PDUR",
    "源网段报文Dlc Check使能": "Enable",
    "目标网段报文名称": "DST_MSG",
    "目标网段报文CANID": "0x200",
    "目标网段报文Length": 8,
    "目标网段报文类型": "STANDARD_FD_CAN",
    "目标网段CAN通道": "DST_CAN",
    "路由Length Strategy功能选择": "IGNORE",
    "操作类型": "ADD",
}


def _write_workbook(path: Path, headers: list[str], values: list[object]) -> Path:
    workbook = Workbook()
    reference = workbook.active
    reference.title = "引用数据"
    reference.append(list(REFERENCE_HEADERS))
    reference.append(["SRC_CAN", "BUF_SRC", "HRH_SRC"])
    reference.append(["DST_CAN", "BUF_DST", "HRH_DST"])
    direct = workbook.create_sheet("直接报文路由")
    direct.append(headers)
    direct.append(values)
    signal = workbook.create_sheet("信号路由")
    signal.append(list(SIGNAL_HEADERS))
    workbook.save(path)
    return path


def test_duplicate_required_header_blocks_the_sheet(tmp_path: Path) -> None:
    """重复的必需表头必须报错，并让该表不再产出任何路由行。"""
    headers = list(DIRECT_HEADERS)
    headers.insert(1, "源网段报文名称")
    values: list[object] = []
    for header in headers:
        if header == "源网段报文名称" and values:
            values.append("SRC_OLD")
            continue
        values.append(_DIRECT_ROW.get(header))
    path = _write_workbook(tmp_path / "duplicate_required_v1.0.xlsx", headers, values)

    result = read_workbook(path)

    codes = {issue.code for issue in result.issues}
    assert "WORKBOOK_HEADER_DUPLICATE" in codes, [issue.message for issue in result.issues]
    assert not result.is_valid
    # 不能按错误的那一列取值生成路由。
    assert result.data.direct_routes == ()


def test_duplicate_unrelated_column_does_not_block(tmp_path: Path) -> None:
    """工具根本不读的重复列不应阻断，避免过度拦截。"""
    headers = list(DIRECT_HEADERS) + ["备注", "备注"]
    values = [_DIRECT_ROW.get(header) for header in DIRECT_HEADERS] + ["甲", "乙"]
    path = _write_workbook(tmp_path / "duplicate_extra_v1.0.xlsx", headers, values)

    result = read_workbook(path)

    codes = {issue.code for issue in result.issues}
    assert "WORKBOOK_HEADER_DUPLICATE" not in codes
    assert result.is_valid
    assert len(result.data.direct_routes) == 1
    assert result.data.direct_routes[0].key.source_message_name == "SRC_MSG"


def test_normal_headers_are_unaffected(tmp_path: Path) -> None:
    """正常表头不受影响，仍按用户填写的一列取值。"""
    headers = list(DIRECT_HEADERS)
    values = [_DIRECT_ROW.get(header) for header in headers]
    path = _write_workbook(tmp_path / "normal_v1.0.xlsx", headers, values)

    result = read_workbook(path)

    assert result.is_valid, [issue.message for issue in result.issues]
    assert len(result.data.direct_routes) == 1
    assert result.data.direct_routes[0].key.source_message_name == "SRC_MSG"
