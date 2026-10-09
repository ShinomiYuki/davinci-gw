#!/usr/bin/env python3
"""维护空白模板的指定页；以 OOXML 定点修改保留其他页的 Excel 扩展。"""

import argparse
import os
from pathlib import Path
import tempfile
import zipfile

from lxml import etree as ET

from doip_mapping import HEADERS
from write_gateway_config import MAIN_NS, REL_NS, PACKAGE_REL_NS, column_letter, worksheet_path

NS = {"s": MAIN_NS}
CONTENT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"

# 沿用原页的“序号＋字段/说明”成对布局；内容对应工具 1.1.4。
SECTIONS = [
    ("CanIf", "Rx 字段", "Rx 配置与处理", "Tx 字段", "Tx 配置与处理", "共同规则", "共同配置与处理", [
        ("报文名称 / Short Name", "整帧页的源网段报文名称；新建名带模块、CAN ID、完整通道和 Rx。已有引用链匹配对象复用。", "报文名称 / Short Name", "目标网段报文名称；新建名带模块、CAN ID、完整通道和 Tx。", "已有对象", "按实际 CAN ID、类型、HRH/TxBuffer 和 PDU 引用匹配；冲突阻止输出，不覆盖历史对象。"),
        ("源网段报文CANID / 报文类型", "分别写入 CanIfRxPduCanId / CanIfRxPduCanIdType。", "目标网段报文CANID / 报文类型", "分别写入 CanIfTxPduCanId / CanIfTxPduCanIdType。", "Handle ID", "Rx、Tx 分别在定义作用域内分配未占用 ID；不写零等待 DaVinci 修复。"),
        ("源网段报文Length", "写入 CanIfRxPduDlc，并与底层 EcuC PduLength 一致。", "目标网段报文Length", "写入 CanIfTxPduDlc，并与底层 EcuC PduLength 一致。", "硬件引用", "引用数据的 CanIfHrh名称 / CanIfTxBuffer名称解析到已有硬件；不新建或删除硬件。缺依赖时警告并跳过 ADD。"),
        ("源网段RxIndicationUL", "普通整帧按字段写 UL，回调为 PduR_CanIfRxIndication；CAN 诊断固定 CAN_TP / CanTp_RxIndication。", "TxConfirmation", "普通整帧固定 NONE / NULL_PTR；CAN 诊断固定 CAN_TP / CanTp_TxConfirmation。", "CAN 诊断类型 / 长度", "BDCAN、DMCAN、DGCAN：Rx NO_FD、Tx STANDARD_CAN、8 字节；其他网段：Rx/Tx FD、64 字节。"),
        ("源网段报文Dlc Check使能", "整帧 Enable→true、Disable→false；CAN 诊断固定 false。", "目标网段报文Truncation使能", "整帧按使能字段写入；CAN 诊断固定 true。", "Checksum / PnFilter", "整帧页保存输入字段，当前不据此新增对应 ARXML 参数。"),
        ("Rx Pdu Ref", "引用底层 EcuC PDU；CanTp 的 Rx NPdu / Tx FC 引用此底层 PDU。", "Tx Pdu Ref", "引用底层 EcuC PDU；CanTp 的 Tx NPdu / Rx FC 引用此底层 PDU。", "其他固定值", "新建类型 STATIC、ReadNotify=false；Rx ReadData=false。未由当前逻辑创建的参数不自行补默认值。"),
    ]),
    ("Com", "源端字段", "源端配置与处理", "目标端字段", "目标端配置与处理", "共同规则", "共同配置与处理", [
        ("源网段 / 源报文名 / 源信号名", "从对应 DBC/PDU 与 ComSignal 引用定位 Rx 信号。", "目标网段 / 目标报文名 / 目标信号名", "定位 Tx ComSignal；创建 ComGwDestination 的信号引用。", "Mapping", "同一源信号的多个目标共用 Mapping/Source；仅补缺失目标。"),
        ("字节序", "Skill 保留原矩阵的字节序；工具保存输入，不据此重建信号位布局。", "ComSignalAccess", "参与 Mapping 的 Tx 信号设 ACCESS_NEEDED_BY_SWC_OR_COM。", "源端 SignalAccess", "参与 Mapping 的 Rx 信号同样设 ACCESS_NEEDED_BY_SWC_OR_COM。"),
        ("超时时间", "单位秒；非空写 ComTimeout 和 ComRxDataTimeoutAction=REPLACE；工具不重新推算周期。", "超时值", "只作用于源端 Rx ComRxDataTimeoutSubstitutionValue，目标端替代值保留。", "超时替代值", "Skill 从源信号矩阵 AA Invalid Value(Hex) 提取，可独立于超时时间填写；不查目标信号。"),
        ("源信号组合名", "可选，作为标准输入保留；不能代替实际源信号引用。", "操作类型", "ADD / DELETE；DELETE 只移除匹配的目标腿。", "删除共享/超时", "共享 Mapping/Source 保留；仅有充分引用证据时清理源端超时，否则保留并说明；不回退 SignalAccess。"),
    ]),
    ("EcuC", "输入/对象", "配置与处理", "诊断对象", "配置与处理", "共同规则", "共同配置与处理", [
        ("普通源/目标 PDU", "PduLength 来自对应整帧 Length；J1939Requestable=false；CanIf 与 PduR 引用此 PDU。", "诊断底层 PDU", "长度由 CAN 通道规则得到，连接 CanIf 与 CanTp NPdu/FC。", "Short Name", "普通使用 GWT_EcuC_...；诊断底层 GWT_Diag_EcuC_...，上层 GWT_Diag_Tp_EcuC_...。"),
        ("其他参数", "普通 PDU 未创建 DynamicLength、MetaDataLength、Partition 等输入之外参数。", "诊断上层 N-SDU PDU", "连接 CanTp NSdu 与 PduR；长度及非输入参数优先从实际同网段同类样板推导，不等同 CAN 帧 DLC。", "共享保留", "复用真实引用链中的现有 PDU；删除时仍被其他模块引用的 PDU 保留，禁止悬空引用。"),
    ]),
    ("CanTp", "请求端字段", "配置与处理", "应答端字段", "配置与处理", "共同规则", "共同配置与处理", [
        ("CANID_REQ / CANID_RES / CAN通道", "物理寻址：请求 Rx、响应 Tx；按实际硬件和底层 PDU 引用确定网段。", "CANID_REQ / CANID_RES / CAN通道", "物理寻址：请求 Tx、响应 Rx；响应 ID 在两端一致。", "Channel 分组", "新建 GWT_CanTpChannelGW_<网段><请求ID>_<响应ID>；功能寻址只带请求 ID。已有分组按引用复用。"),
        ("N_As / N_Bs / N_Cs", "Tx NSdu 的三个时间参数；输入毫秒，写 ARXML 时除以 1000。", "N_Ar / N_Br / N_Cr", "Rx NSdu 的三个时间参数；输入毫秒，写 ARXML 时除以 1000。", "两端参数", "每端都按 Rx/Tx 对应关系配置。物理 ADD 需八个参数，不取模板示例或未经确认的默认值。"),
        ("BlockSize / STmin", "Rx NSdu：BlockSize 为 0–255 整数，STmin 为毫秒并转换成秒。", "接收/发送类型 / Length", "必须符合对应 CAN 通道规则；CanTpDl 为该通道帧长。", "样板选择", "优先同网段同寻址类型；无样板时只允许全局唯一语义，歧义报错。子容器名称含 ECU/网段或 CAN ID。"),
        ("CANID_RES 为空或 /", "功能寻址仅建请求 Rx；需 N_Ar/N_Br/N_Cr/BlockSize/STmin。", "CANID_RES 为空或 /", "功能寻址仅建请求 Tx；需 N_As/N_Bs/N_Cs。", "功能/删除", "功能寻址不建响应、FC；DELETE 不要求时间参数。两端功能标记必须一致。"),
    ]),
    ("PduR", "RoutingPath / Source", "配置与处理", "Destination", "配置与处理", "Queue / Group", "配置与处理", [
        ("Communication Type", "整帧 COMMUNICATION_INTERFACE；CAN 诊断 TRANSPORT_PROTOCOL。Lock Ref 复用基准唯一引用，Multicore=false。", "Direction / Routing / Processing", "TRANSMIT / GATEWAY_ROUTING / IMMEDIATE；CrossPartition=false。", "普通整帧 Queue", "不新增 TP Queue；普通路径的一对多目标不受 TP 共享队列规则限制。"),
        ("Source PDU / Direction", "RECEIVE；整帧引用 CanIf 对应 EcuC，诊断引用 CanTp 上层 EcuC。BSW 模块引用与真实端点模块一致。", "PDU / BSW Ref", "指向目标 EcuC PDU 和对应 CanIf 或 CanTp 模块。", "TP Queue", "同一 TP 路径的多个目标共用 Queue；已有路径复用既有 Queue 并保留参数，独立新路径分别创建。"),
        ("Source Short Name / Handle", "诊断使用含 ECU、CAN ID、源网段的唯一 GWT_Diag_... 名称；Handle ID 独立分配，避免 Source 通名冲突。", "Destination Short Name", "优先 Diag_TP_<目标CANID>_<网段>；重名时 GWT_Diag_TP_<ID>_<源网段>_To_<目标网段>。", "SharedBufferQueue", "每个新 Queue 含 PduRSharedBufferQueue；普通 Depth=3、TpThreshold=0；7DF/功能寻址 Depth=5、阈值=0。"),
        ("一对多 / ADD 幂等", "已有源路径只补缺失目标；完整路由再次 ADD 不重复创建。", "Length Strategy", "整帧按“路由Length Strategy功能选择”；CAN 诊断固定 UNUSED。整帧 DataProvision=PDUR_DIRECT、Confirmation=false。", "RoutingGroup", "沿目标网段和真实引用确定已有组并维护成员；不凭组名猜测，也不新建/删除/重命名组。"),
        ("DELETE / 共享保留", "仅移除选中的目标腿；共享源路径、端点、硬件与人工配置保留。", "DELETE→ADD", "同键替换先删除再新增；输出检查对象语义、Handle ID、符号名和引用。", "Queue 删除", "最后一个使用者删除后清理 Queue；共享缓冲池保留。存在歧义或残余内部引用时阻止输出。"),
    ]),
    ("DoIP_to_CAN（下一阶段输入）", "地址字段", "映射规则", "CAN 字段", "映射规则", "寻址字段", "映射规则", [
        ("logicalAddress / Tester", "按目标逻辑地址汇总不同 Sheet 的实际 Tester；有几个填几行，不自动补四个。", "CANBus / RequestCanId / RespCanId", "保持该 Tester 关联的目标 CAN 网段和请求/响应；CANBus 映射到引用数据。", "RouterType", "0E80=OBD，0E81=OBD_Remote，0F00=OTA，0F01=OTA_Remote（需求业务：设备管理）。"),
        ("合并 / 去重", "相同 logicalAddress、Tester、CANBus 的相同映射合并；请求/响应冲突时阻止输出。", "Functional", "功能寻址填 YES，响应 ID 留空；每个目标 CANBus 独立一行。物理寻址该列留空。", "阶段边界", "本页只提供 DoIP→CAN 映射；当前工具 1.1.4 不读取此页生成 DoIP、SoAd 或跨协议 PduR。"),
    ]),
]


def text_cell(row, column, value, style):
    cell = ET.SubElement(row, f"{{{MAIN_NS}}}c", r=f"{column_letter(column)}{row.get('r')}", s=str(style), t="inlineStr")
    ET.SubElement(ET.SubElement(cell, f"{{{MAIN_NS}}}is"), f"{{{MAIN_NS}}}t").text = str(value)


def description_sheet(original):
    root = ET.fromstring(original)
    data = root.find("s:sheetData", NS)
    old_cells = {c.get("r"): c for c in data.iter(f"{{{MAIN_NS}}}c")}
    styles = [old_cells[f"{letter}3"].get("s", "0") for letter in "ABCDEDE"]
    heading_style = old_cells["B1"].get("s", "0")
    data.clear()
    merges = root.find("s:mergeCells", NS)
    if merges is None:
        merges = ET.SubElement(root, f"{{{MAIN_NS}}}mergeCells")
    merges.clear()
    number = 1
    for title, *rest in SECTIONS:
        row = ET.SubElement(data, f"{{{MAIN_NS}}}row", r=str(number), ht="26", customHeight="1")
        text_cell(row, 2, title, heading_style)
        ET.SubElement(merges, f"{{{MAIN_NS}}}mergeCell", ref=f"B{number}:G{number}")
        number += 1
        row = ET.SubElement(data, f"{{{MAIN_NS}}}row", r=str(number), ht="26", customHeight="1")
        for col, label in enumerate(["序号"] + rest[:6], 1):
            text_cell(row, col, label, heading_style)
        number += 1
        for index, values in enumerate(rest[6], 1):
            row = ET.SubElement(data, f"{{{MAIN_NS}}}row", r=str(number), ht="92", customHeight="1")
            for col, value in enumerate([index] + list(values), 1):
                text_cell(row, col, value, styles[col - 1])
            number += 1
        number += 1
    merges.set("count", str(len(merges)))
    root.find("s:dimension", NS).set("ref", f"A1:G{number-1}")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def doip_sheet():
    root = ET.Element(f"{{{MAIN_NS}}}worksheet", nsmap={None: MAIN_NS})
    ET.SubElement(root, f"{{{MAIN_NS}}}dimension", ref="A1:G2")
    views = ET.SubElement(root, f"{{{MAIN_NS}}}sheetViews")
    view = ET.SubElement(views, f"{{{MAIN_NS}}}sheetView", workbookViewId="0", showGridLines="0")
    ET.SubElement(view, f"{{{MAIN_NS}}}pane", ySplit="1", topLeftCell="A2", activePane="bottomLeft", state="frozen")
    ET.SubElement(root, f"{{{MAIN_NS}}}sheetFormatPr", defaultRowHeight="20")
    cols = ET.SubElement(root, f"{{{MAIN_NS}}}cols")
    for col, width in enumerate([16, 20, 20, 16, 20, 20, 16], 1):
        ET.SubElement(cols, f"{{{MAIN_NS}}}col", min=str(col), max=str(col), width=str(width), customWidth="1")
    data = ET.SubElement(root, f"{{{MAIN_NS}}}sheetData")
    row = ET.SubElement(data, f"{{{MAIN_NS}}}row", r="1", ht="30", customHeight="1")
    for col, label in enumerate(HEADERS, 1):
        text_cell(row, col, label, "4")
    row = ET.SubElement(data, f"{{{MAIN_NS}}}row", r="2")
    for col in range(1, 8):
        text_cell(row, col, "", "36")
    validations = ET.SubElement(root, f"{{{MAIN_NS}}}dataValidations", count="3")
    for cell, formula in [("C2:C1048576", '"OBD,OBD_Remote,OTA,OTA_Remote"'), ("D2:D1048576", 'INDIRECT("\'引用数据\'!$A$2:$A$13")'), ("G2:G1048576", '"YES"')]:
        item = ET.SubElement(validations, f"{{{MAIN_NS}}}dataValidation", type="list", allowBlank="1", showErrorMessage="1", sqref=cell)
        ET.SubElement(item, f"{{{MAIN_NS}}}formula1").text = formula
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def upgrade(template):
    with zipfile.ZipFile(template) as source:
        parts = {info.filename: source.read(info.filename) for info in source.infolist()}
        infos = source.infolist()
        workbook = ET.fromstring(parts["xl/workbook.xml"])
        sheets = workbook.find("s:sheets", NS)
        if any(s.get("name") == "DoIP_to_CAN" for s in sheets):
            raise ValueError("模板已升级，不重复迁移")
        relationships = ET.fromstring(parts["xl/_rels/workbook.xml.rels"])
        content = ET.fromstring(parts["[Content_Types].xml"])
        removed = set()
        for sheet in list(sheets):
            name = sheet.get("name")
            if name in {"诊断报文路由参数", "Sheet1", "命名规则"}:
                path = worksheet_path(source, name)
                removed.add(path)
                relation_id = sheet.get(f"{{{REL_NS}}}id")
                for relation in list(relationships):
                    if relation.get("Id") == relation_id:
                        relationships.remove(relation)
                sheets.remove(sheet)
            elif name == "直接报文路由需求描述":
                path = worksheet_path(source, name)
                parts[path] = description_sheet(parts[path])
                sheet.set("name", "需求描述")
        next_id = max(int(s.get("sheetId")) for s in sheets) + 1
        new_path = "xl/worksheets/doip_to_can.xml"
        ET.SubElement(sheets, f"{{{MAIN_NS}}}sheet", name="DoIP_to_CAN", sheetId=str(next_id), attrib={f"{{{REL_NS}}}id": "rIdDoIPToCAN"})
        ET.SubElement(relationships, f"{{{PACKAGE_REL_NS}}}Relationship", Id="rIdDoIPToCAN", Type=f"{REL_NS}/worksheet", Target="worksheets/doip_to_can.xml")
        for override in list(content):
            if override.get("PartName", "").lstrip("/") in removed:
                content.remove(override)
        ET.SubElement(content, f"{{{CONTENT_NS}}}Override", PartName="/" + new_path, ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml")
        for view in workbook.findall("s:bookViews/s:workbookView", NS):
            view.set("activeTab", "3")
            view.set("firstSheet", "0")
        for path, root in [("xl/workbook.xml", workbook), ("xl/_rels/workbook.xml.rels", relationships), ("[Content_Types].xml", content)]:
            parts[path] = ET.tostring(root, encoding="utf-8", xml_declaration=True)
        parts[new_path] = doip_sheet()
        if "docProps/app.xml" in parts:
            app = ET.fromstring(parts["docProps/app.xml"])
            app_ns = {"a": "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties",
                      "v": "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"}
            titles = app.find("a:TitlesOfParts/v:vector", app_ns)
            if titles is not None:
                titles.clear()
                titles.set("size", str(len(sheets)))
                titles.set("baseType", "lpstr")
                for sheet in sheets:
                    ET.SubElement(titles, f"{{{app_ns['v']}}}lpstr").text = sheet.get("name")
            count = app.find("a:HeadingPairs/v:vector/v:variant/v:i4", app_ns)
            if count is not None:
                count.text = str(len(sheets))
            parts["docProps/app.xml"] = ET.tostring(app, encoding="utf-8", xml_declaration=True)
    descriptor, filename = tempfile.mkstemp(dir=template.parent, suffix=".xlsx")
    os.close(descriptor)
    temporary = Path(filename)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as destination:
            for info in infos:
                if info.filename not in removed:
                    destination.writestr(info, parts[info.filename])
            destination.writestr(new_path, parts[new_path])
        os.replace(temporary, template)
    finally:
        temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("template", type=Path)
    upgrade(parser.parse_args().template)
