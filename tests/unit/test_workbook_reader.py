"""标准配置表输入契约测试。"""

from __future__ import annotations

from decimal import Decimal

import pytest

from tests.conftest import DIRECT_HEADERS, SIGNAL_HEADERS, direct_row, signal_row
from davinci_gw.domain.models import OperationType
from davinci_gw.input.workbook_reader import read_workbook


def messages(result: object) -> str:
    """拼接校验消息，便于断言用户提示。"""
    return "\n".join(issue.message for issue in result.issues)


def test_reads_three_execution_sheets(workbook_factory: object) -> None:
    result = read_workbook(workbook_factory())
    assert result.is_valid
    assert result.data.target_version == "4.84"
    assert len(result.data.reference_data) == 2
    assert len(result.data.direct_routes) == 1
    assert len(result.data.signal_routes) == 1


def test_header_order_can_change(workbook_factory: object) -> None:
    result = read_workbook(workbook_factory(
        direct_headers=tuple(reversed(DIRECT_HEADERS)),
        signal_headers=tuple(reversed(SIGNAL_HEADERS)),
    ))
    assert result.is_valid
    assert result.data.direct_routes[0].key.source_message_name == "SRC_MSG"


@pytest.mark.parametrize("sheet", ["引用数据", "直接报文路由", "信号路由"])
def test_missing_required_sheet(workbook_factory: object, sheet: str) -> None:
    result = read_workbook(workbook_factory(omitted_sheets=(sheet,)))
    assert not result.is_valid
    assert sheet in messages(result)


def test_missing_required_header(workbook_factory: object) -> None:
    result = read_workbook(workbook_factory(direct_headers=DIRECT_HEADERS[:-1]))
    assert not result.is_valid
    assert "操作类型" in messages(result)
    assert "第1行" in messages(result)


def test_blank_and_formatted_rows_are_ignored(workbook_factory: object) -> None:
    result = read_workbook(workbook_factory(formatted_blank_rows=80))
    assert result.is_valid
    assert len(result.data.direct_routes) == 1


def test_operation_is_trimmed_and_normalized(workbook_factory: object) -> None:
    result = read_workbook(workbook_factory(
        direct_rows=(direct_row(**{"操作类型": " add "}),),
        signal_rows=(signal_row(**{"操作类型": "delete"}),),
    ))
    assert result.is_valid
    assert result.data.direct_routes[0].operation is OperationType.ADD
    assert result.data.signal_routes[0].operation is OperationType.DELETE


def test_invalid_operation_has_friendly_location(workbook_factory: object) -> None:
    path = workbook_factory(direct_rows=(direct_row(**{"操作类型": "REMOVE"}),))
    result = read_workbook(path)
    text = messages(result)
    assert not result.is_valid
    assert str(path) in text and "直接报文路由" in text and "第2行" in text
    assert "REMOVE" in text and "ADD或DELETE" in text


def test_hex_and_integer_can_ids_are_normalized(workbook_factory: object) -> None:
    result = read_workbook(workbook_factory(direct_rows=(direct_row(
        **{"源网段报文CANID": "0x47A", "目标网段报文CANID": 291}),
    )))
    assert result.is_valid
    route = result.data.direct_routes[0]
    assert route.key.source_can_id == 0x47A
    assert route.key.target_can_id == 291
    assert route.source_can_id_text == "0x47A"


@pytest.mark.parametrize("value", [-1, "0x20000000", "abc", True])
def test_invalid_can_id(workbook_factory: object, value: object) -> None:
    result = read_workbook(workbook_factory(direct_rows=(direct_row(
        **{"源网段报文CANID": value}),)))
    assert not result.is_valid
    assert "源网段报文CANID" in messages(result)


@pytest.mark.parametrize("value", [-1, 65, 1.5, "eight"])
def test_invalid_length(workbook_factory: object, value: object) -> None:
    result = read_workbook(workbook_factory(direct_rows=(direct_row(
        **{"源网段报文Length": value}),)))
    assert not result.is_valid
    assert "源网段报文Length" in messages(result)


@pytest.mark.parametrize("field,value", [("超时值", -1), ("超时时间", "bad")])
def test_invalid_timeout(workbook_factory: object, field: str, value: object) -> None:
    result = read_workbook(workbook_factory(signal_rows=(signal_row(**{field: value}),)))
    assert not result.is_valid
    assert field in messages(result)


def test_optional_timeout_is_decimal(workbook_factory: object) -> None:
    result = read_workbook(workbook_factory(signal_rows=(signal_row(
        **{"超时值": "1.25", "超时时间": 2}),)))
    assert result.is_valid
    assert result.data.signal_routes[0].timeout_value == Decimal("1.25")


def test_delete_only_requires_identity_fields(workbook_factory: object) -> None:
    row = direct_row(**{"操作类型": "DELETE"})
    for field in ("源网段报文Length", "源网段报文类型", "源网段RxIndicationUL",
                  "源网段报文Dlc Check使能", "目标网段报文Length", "目标网段报文类型",
                  "目标网段报文Truncation使能", "路由Length Strategy功能选择"):
        row[field] = None
    result = read_workbook(workbook_factory(direct_rows=(row,)))
    assert result.is_valid


def test_add_requires_strategy_fields(workbook_factory: object) -> None:
    result = read_workbook(workbook_factory(direct_rows=(direct_row(
        **{"路由Length Strategy功能选择": None}),)))
    assert not result.is_valid
    assert "路由Length Strategy功能选择" in messages(result)


def test_duplicate_direct_route(workbook_factory: object) -> None:
    row = direct_row()
    result = read_workbook(workbook_factory(direct_rows=(row, row)))
    assert not result.is_valid
    assert "重复" in messages(result)


def test_duplicate_signal_route(workbook_factory: object) -> None:
    row = signal_row()
    result = read_workbook(workbook_factory(signal_rows=(row, row)))
    assert not result.is_valid
    assert "重复" in messages(result)


@pytest.mark.parametrize("kind", ["direct", "signal"])
def test_add_delete_pair_is_valid_replacement(workbook_factory: object, kind: str) -> None:
    if kind == "direct":
        result = read_workbook(workbook_factory(direct_rows=(
            direct_row(), direct_row(**{"操作类型": "DELETE"}))))
    else:
        result = read_workbook(workbook_factory(signal_rows=(
            signal_row(), signal_row(**{"操作类型": "DELETE"}))))
    assert result.is_valid
    changes = result.data.direct_routes if kind == "direct" else result.data.signal_routes
    assert {change.operation.value for change in changes} == {"ADD", "DELETE"}


def test_one_to_many_is_not_duplicate(workbook_factory: object) -> None:
    result = read_workbook(workbook_factory(
        direct_rows=(direct_row(), direct_row(**{"目标网段报文名称": "DST_2"})),
        signal_rows=(signal_row(), signal_row(**{"目标信号名": "DST_SIG_2"})),
    ))
    assert result.is_valid
    assert len(result.data.direct_routes) == 2
    assert len(result.data.signal_routes) == 2


def test_duplicate_reference_channel(workbook_factory: object) -> None:
    rows = (
        {"CAN通道名称": "CAN_A", "CanIfTxBuffer名称": "A", "CanIfHrh名称": "B"},
        {"CAN通道名称": "CAN_A", "CanIfTxBuffer名称": "C", "CanIfHrh名称": "D"},
    )
    result = read_workbook(workbook_factory(reference_rows=rows))
    assert not result.is_valid
    assert "CAN通道名称" in messages(result) and "重复" in messages(result)


def test_reference_preserves_empty_string_and_none(workbook_factory: object) -> None:
    rows = (
        {"CAN通道名称": "CAN_EMPTY", "CanIfTxBuffer名称": "", "CanIfHrh名称": None},
    )
    result = read_workbook(workbook_factory(reference_rows=rows))
    assert result.is_valid
    assert result.data.reference_data[0].tx_buffer_name == ""
    assert result.data.reference_data[0].hrh_name is None


def test_filename_version_rule(workbook_factory: object) -> None:
    result = read_workbook(workbook_factory(filename="Gateway_V04.084.XLSX"))
    assert result.is_valid
    assert result.data.target_version == "4.84"


def test_filename_without_version_is_rejected(workbook_factory: object) -> None:
    result = read_workbook(workbook_factory(filename="gateway.xlsx"))
    assert not result.is_valid
    assert "*_vX.x.xlsx" in messages(result)


def test_version_sheet_is_not_target_version(workbook_factory: object) -> None:
    result = read_workbook(workbook_factory(filename="gateway.xlsx"))
    assert result.data.target_version is None
    assert "V01" not in (result.data.target_version or "")
