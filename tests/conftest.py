"""测试夹具：动态生成小型标准工作簿和脱敏 ARXML。"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from copy import copy
from pathlib import Path

import pytest
from lxml import etree
from openpyxl import Workbook


DIRECT_HEADERS = (
    "源网段报文名称", "源网段报文CANID", "源网段报文Length", "源网段报文类型",
    "源网段CAN通道", "源网段RxIndicationUL", "源网段报文Checksum使能",
    "源网段报文Dlc Check使能", "目标网段报文名称", "目标网段报文CANID",
    "目标网段报文Length", "目标网段报文类型", "目标网段CAN通道",
    "目标网段报文Checksum使能", "目标网段报文PnFilter使能",
    "目标网段报文Truncation使能", "路由Length Strategy功能选择", "操作类型",
)
SIGNAL_HEADERS = (
    "源网段", "源报文名", "源信号名", "字节序", "超时值", "超时时间",
    "源信号组合名", "目标网段", "目标报文名", "目标信号名", "操作类型",
)
REFERENCE_HEADERS = ("CAN通道名称", "CanIfTxBuffer名称", "CanIfHrh名称")


def direct_row(**changes: object) -> dict[str, object]:
    """返回一条完整、有效的直接报文 ADD 数据。"""
    row: dict[str, object] = {
        "源网段报文名称": "SRC_MSG", "源网段报文CANID": "0x100",
        "源网段报文Length": 8, "源网段报文类型": "STANDARD_CAN",
        "源网段CAN通道": "SRC_CAN", "源网段RxIndicationUL": "PDUR",
        "源网段报文Checksum使能": None, "源网段报文Dlc Check使能": "Enable",
        "目标网段报文名称": "DST_MSG", "目标网段报文CANID": 0x200,
        "目标网段报文Length": 8, "目标网段报文类型": "STANDARD_FD_CAN",
        "目标网段CAN通道": "DST_CAN", "目标网段报文Checksum使能": None,
        "目标网段报文PnFilter使能": None, "目标网段报文Truncation使能": "Enable",
        "路由Length Strategy功能选择": "IGNORE", "操作类型": "ADD",
    }
    row.update(changes)
    return row


def signal_row(**changes: object) -> dict[str, object]:
    """返回一条完整、有效的信号 ADD 数据。"""
    row: dict[str, object] = {
        "源网段": "SRC", "源报文名": "SRC_MSG", "源信号名": "SRC_SIG",
        "字节序": "Motorola LSB", "超时值": None, "超时时间": 2,
        "源信号组合名": None, "目标网段": "DST", "目标报文名": "DST_MSG",
        "目标信号名": "DST_SIG", "操作类型": "ADD",
    }
    row.update(changes)
    return row


def _append_mapping(sheet: object, headers: Sequence[str], row: Mapping[str, object]) -> None:
    sheet.append([row.get(header) for header in headers])


@pytest.fixture
def workbook_factory(tmp_path: Path) -> Callable[..., Path]:
    """返回可按场景生成工作簿的工厂。"""
    def create(
        *,
        filename: str = "gateway_v4.84.xlsx",
        direct_rows: Iterable[Mapping[str, object]] | None = None,
        signal_rows: Iterable[Mapping[str, object]] | None = None,
        reference_rows: Iterable[Mapping[str, object]] | None = None,
        direct_headers: Sequence[str] = DIRECT_HEADERS,
        signal_headers: Sequence[str] = SIGNAL_HEADERS,
        reference_headers: Sequence[str] = REFERENCE_HEADERS,
        omitted_sheets: Iterable[str] = (),
        formatted_blank_rows: int = 0,
    ) -> Path:
        path = tmp_path / filename
        workbook = Workbook()
        workbook.remove(workbook.active)
        omitted = set(omitted_sheets)
        if "引用数据" not in omitted:
            sheet = workbook.create_sheet("引用数据")
            sheet.append(list(reference_headers))
            rows = reference_rows if reference_rows is not None else (
                {"CAN通道名称": "SRC_CAN", "CanIfTxBuffer名称": "TX", "CanIfHrh名称": "HRH"},
                {"CAN通道名称": "DST_CAN", "CanIfTxBuffer名称": "TX2", "CanIfHrh名称": "HRH2"},
            )
            for row in rows:
                _append_mapping(sheet, reference_headers, row)
        if "直接报文路由" not in omitted:
            sheet = workbook.create_sheet("直接报文路由")
            sheet.append(list(direct_headers))
            for row in (direct_rows if direct_rows is not None else (direct_row(),)):
                _append_mapping(sheet, direct_headers, row)
            for index in range(formatted_blank_rows):
                sheet.cell(row=sheet.max_row + 1, column=1).fill = copy(sheet["A1"].fill)
        if "信号路由" not in omitted:
            sheet = workbook.create_sheet("信号路由")
            sheet.append(list(signal_headers))
            for row in (signal_rows if signal_rows is not None else (signal_row(),)):
                _append_mapping(sheet, signal_headers, row)
        version = workbook.create_sheet("版本")
        version.append(["V01", "V02"])
        workbook.save(path)
        return path
    return create


@pytest.fixture
def arxml_factory(tmp_path: Path) -> Callable[..., Path]:
    """返回可生成模块顺序及异常场景的 ARXML 工厂。"""
    def create(
        *,
        filename: str = "baseline.arxml",
        order: Sequence[str] = ("CanIf", "Com", "EcuC", "PduR"),
        missing: str | None = None,
        duplicate: str | None = None,
        extra: bool = False,
    ) -> Path:
        ns = "http://autosar.org/schema/r4.0"
        xsi = "http://www.w3.org/2001/XMLSchema-instance"
        root = etree.Element(f"{{{ns}}}AUTOSAR", nsmap={None: ns, "xsi": xsi})
        root.set(f"{{{xsi}}}schemaLocation", f"{ns} AUTOSAR_00049.xsd")
        root.append(etree.Comment("必须保留的注释"))
        packages = etree.SubElement(root, f"{{{ns}}}AR-PACKAGES")
        package = etree.SubElement(packages, f"{{{ns}}}AR-PACKAGE")
        etree.SubElement(package, f"{{{ns}}}SHORT-NAME").text = "Cfg"
        elements = etree.SubElement(package, f"{{{ns}}}ELEMENTS")

        def add_module(name: str) -> None:
            node = etree.SubElement(elements, f"{{{ns}}}ECUC-MODULE-CONFIGURATION-VALUES")
            node.set("VENDOR-EXT", "keep")
            etree.SubElement(node, f"{{{ns}}}SHORT-NAME").text = name
            ref = etree.SubElement(node, f"{{{ns}}}DEFINITION-REF")
            ref.set("DEST", "ECUC-MODULE-DEF")
            ref.text = f"/MICROSAR/{name}"
            containers = etree.SubElement(node, f"{{{ns}}}CONTAINERS")
            child = etree.SubElement(containers, f"{{{ns}}}ECUC-CONTAINER-VALUE")
            etree.SubElement(child, f"{{{ns}}}SHORT-NAME").text = "SharedName"
            values = etree.SubElement(child, f"{{{ns}}}REFERENCE-VALUES")
            value = etree.SubElement(values, f"{{{ns}}}ECUC-REFERENCE-VALUE")
            etree.SubElement(value, f"{{{ns}}}DEFINITION-REF").text = "/Defs/Reference"
            etree.SubElement(value, f"{{{ns}}}VALUE-REF").text = "/Cfg/CanIf"

        for module in order:
            if module != missing:
                add_module(module)
                if module == duplicate:
                    add_module(module)
        if extra:
            add_module("CanSM")
        etree.SubElement(elements, f"{{{ns}}}VENDOR-UNKNOWN").text = "preserve"
        path = tmp_path / filename
        etree.ElementTree(root).write(path, encoding="UTF-8", xml_declaration=True, pretty_print=True)
        return path
    return create
