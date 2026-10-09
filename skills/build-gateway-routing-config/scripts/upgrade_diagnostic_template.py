#!/usr/bin/env python3
import os
import re
import tempfile
import zipfile
from pathlib import Path

from write_gateway_config import worksheet_path


def clear_data_rows(xml_text):
    """清空示例数据但保留原单元格样式，避免空白模板继续携带伪需求。"""

    def clear_row(match):
        row = match.group(0)
        row_number = int(match.group(1))
        if row_number == 1:
            return row
        return re.sub(r"(<c\b[^>]*?)(?:>.*?</c>|/>)", r"\1/>", row)

    return re.sub(
        r'<row\b[^>]*\br="(\d+)"[^>]*>.*?</row>',
        clear_row,
        xml_text,
        flags=re.DOTALL,
    )


def upgrade_diagnostic_sheet(xml_bytes):
    text = xml_bytes.decode("utf-8")
    text = clear_data_rows(text)

    if 'r="AE1"' not in text:
        text = text.replace('dimension ref="A1:AD500"', 'dimension ref="A1:AF500"', 1)
        text = text.replace('spans="1:30"', 'spans="1:32"')

        header = re.search(
            r'<row\b[^>]*\br="1"[^>]*>.*?</row>', text, re.DOTALL
        )
        if header is None:
            raise ValueError("诊断报文路由缺少表头行。")
        header_text = header.group(0).replace(
            "</row>",
            '<c r="AE1" s="4" t="inlineStr"><is><t>诊断入口类型</t></is></c>'
            '<c r="AF1" s="4" t="inlineStr"><is><t>操作类型</t></is></c>'
            "</row>",
        )
        text = text[: header.start()] + header_text + text[header.end() :]

        columns_end = text.find("</cols>")
        if columns_end < 0:
            raise ValueError("诊断报文路由缺少列宽定义。")
        new_columns = (
            '<col min="31" max="31" width="16" customWidth="1"/>'
            '<col min="32" max="32" width="14" customWidth="1"/>'
        )
        text = text[:columns_end] + new_columns + text[columns_end:]

        validations = (
            '<dataValidation type="list" allowBlank="0" showInputMessage="1" '
            'showErrorMessage="1" errorTitle="诊断入口类型错误" '
            'error="请从OBD_CAN或OBD_ETH中选择" sqref="AE2:AE500">'
            '<formula1>"OBD_CAN,OBD_ETH"</formula1></dataValidation>'
            '<dataValidation type="list" allowBlank="0" showInputMessage="1" '
            'showErrorMessage="1" errorTitle="操作类型错误" '
            'error="请从ADD或DELETE中选择" sqref="AF2:AF500">'
            '<formula1>"ADD,DELETE"</formula1></dataValidation>'
        )
        validation_open = re.search(r'<dataValidations\b[^>]*count="(\d+)"[^>]*>', text)
        if validation_open is None:
            raise ValueError("诊断报文路由缺少既有数据校验定义。")
        updated_open = re.sub(
            r'count="\d+"',
            f'count="{int(validation_open.group(1)) + 2}"',
            validation_open.group(0),
            count=1,
        )
        text = text[: validation_open.start()] + updated_open + text[validation_open.end() :]
        text = text.replace(
            "</dataValidations>", validations + "</dataValidations>", 1
        )

    return text.encode("utf-8")


def upgrade(template):
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".gateway_diagnostic_template_", suffix=".xlsx", dir=template.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(template, "r") as source:
            diagnostic_path = worksheet_path(source, "诊断报文路由")
            try:
                parameter_path = worksheet_path(source, "诊断报文路由参数")
            except KeyError:
                parameter_path = None  # 新模板已删除此旧页。
            diagnostic = upgrade_diagnostic_sheet(source.read(diagnostic_path))
            parameters = clear_data_rows(source.read(parameter_path).decode("utf-8")).encode(
                "utf-8"
            ) if parameter_path else None

            with zipfile.ZipFile(temporary, "w") as target:
                for info in source.infolist():
                    data = source.read(info.filename)
                    if info.filename == diagnostic_path:
                        data = diagnostic
                    elif info.filename == parameter_path:
                        data = parameters
                    target.writestr(info, data)
        os.replace(temporary, template)
    finally:
        if temporary.exists():
            temporary.unlink()


if __name__ == "__main__":
    upgrade(
        Path(__file__).resolve().parent.parent
        / "assets"
        / "网关路由配置表空白模板.xlsx"
    )
