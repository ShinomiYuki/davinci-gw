#!/usr/bin/env python3
import argparse
import ctypes
import io
import json
import os
import re
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime
from pathlib import Path

from doip_mapping import mapping_key, mapping_values, normalize_mapping
from extract_diagnostic_candidates import reference_channels


MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
XML_NS = "http://www.w3.org/XML/1998/namespace"
ET.register_namespace("", MAIN_NS)
ET.register_namespace("r", REL_NS)


def desktop_directory():
    if os.name == "nt":
        buffer = ctypes.create_unicode_buffer(260)
        result = ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, buffer)
        if result == 0 and buffer.value:
            return Path(buffer.value)
    return Path.home() / "Desktop"


def resolve_output_path(requested, version, overwrite):
    filename = f"网关路由配置表_v{version}.xlsx"
    if requested is None:
        path = desktop_directory() / filename
    else:
        requested_path = requested.expanduser().resolve()
        if requested_path.exists() and requested_path.is_dir():
            path = requested_path / filename
        elif requested_path.suffix.casefold() == ".xlsx":
            path = requested_path
        else:
            requested_path.mkdir(parents=True, exist_ok=True)
            path = requested_path / filename

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not overwrite:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = path.with_name(f"{path.stem}_{timestamp}{path.suffix}")
    return path


def register_source_namespaces(xml_bytes):
    for _, item in ET.iterparse(io.BytesIO(xml_bytes), events=("start-ns",)):
        prefix, uri = item
        try:
            ET.register_namespace(prefix or "", uri)
        except ValueError:
            pass


def worksheet_path(archive, sheet_name):
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    sheets = workbook.find(f"{{{MAIN_NS}}}sheets")
    relationship_id = None
    for sheet in sheets:
        if sheet.attrib.get("name", "").strip().casefold() == sheet_name.strip().casefold():
            relationship_id = sheet.attrib[f"{{{REL_NS}}}id"]
            break
    if relationship_id is None:
        raise KeyError(f"Missing worksheet: {sheet_name}")

    relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    target = None
    for relationship in relationships.findall(f"{{{PACKAGE_REL_NS}}}Relationship"):
        if relationship.attrib.get("Id") == relationship_id:
            target = relationship.attrib["Target"]
            break
    if target is None:
        raise KeyError(f"Missing workbook relationship: {relationship_id}")
    if target.startswith("/"):
        return target.lstrip("/")
    if target.startswith("xl/"):
        return target
    return f"xl/{target}"


def column_letter(number):
    result = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return result


def set_cell_value(cell, value, numeric=False):
    for child in list(cell):
        cell.remove(child)
    cell.attrib.pop("t", None)
    if value is None or value == "":
        return
    if numeric and isinstance(value, (int, float)):
        value_node = ET.SubElement(cell, f"{{{MAIN_NS}}}v")
        value_node.text = str(value)
        return

    cell.attrib["t"] = "inlineStr"
    inline = ET.SubElement(cell, f"{{{MAIN_NS}}}is")
    text = ET.SubElement(inline, f"{{{MAIN_NS}}}t")
    string_value = str(value)
    if string_value != string_value.strip():
        text.attrib[f"{{{XML_NS}}}space"] = "preserve"
    text.text = string_value


def expected_values(route):
    target_channel = str(route["target_channel"])
    target_type = "STANDARD_CAN" if target_channel in {"BDCAN", "DMCAN"} else "STANDARD_FD_CAN"
    return [
        route["source_message_name"],
        route["source_can_id"],
        route["source_length"],
        "STANDARD_CAN",
        route["source_channel"],
        "PDUR",
        "",
        "Enable",
        route["target_message_name"],
        route["target_can_id"],
        route["target_length"],
        target_type,
        target_channel,
        "",
        "",
        "Enable",
        "IGNORE",
        normalized_operation(route),
    ]


def expected_signal_values(route):
    return [
        route["source_network"],
        route["source_message_name"],
        route["source_signal_name"],
        route.get("byte_order", ""),
        route.get("timeout_value", ""),
        route.get("timeout_time", ""),
        route.get("source_signal_combined_name", ""),
        route["target_network"],
        route["target_message_name"],
        route["target_signal_name"],
        normalized_operation(route),
    ]


def expected_diagnostic_values(route):
    return [
        route["request_message_name"],
        route.get("request_can_id_req", ""),
        route.get("request_rx_type", ""),
        route.get("request_can_id_res", ""),
        route.get("request_tx_type", ""),
        route.get("request_length", ""),
        route.get("request_channel", ""),
        route["response_message_name"],
        route["response_can_id_req"],
        route["response_tx_type"],
        route["response_can_id_res"],
        route["response_rx_type"],
        route["response_length"],
        route["response_channel"],
        route.get("request_n_as", ""),
        route.get("request_n_bs", ""),
        route.get("request_n_cs", ""),
        route.get("request_n_ar", ""),
        route.get("request_n_br", ""),
        route.get("request_n_cr", ""),
        route.get("request_block_size", ""),
        route.get("request_st_min", ""),
        route["response_n_as"],
        route["response_n_bs"],
        route["response_n_cs"],
        route["response_n_ar"],
        route["response_n_br"],
        route["response_n_cr"],
        route["response_block_size"],
        route["response_st_min"],
        str(route["entry_type"]).strip().upper(),
        normalized_operation(route),
    ]


def normalized_operation(route):
    operation = str(route.get("operation", "")).strip().upper()
    if operation not in {"ADD", "DELETE"}:
        raise ValueError(f"Invalid route operation: {operation!r}; expected ADD or DELETE")
    return operation


def update_sheet_xml(
    xml_bytes,
    records,
    value_builder,
    column_count,
    numeric_columns=frozenset(),
    minimum_rows=2,
):
    register_source_namespaces(xml_bytes)
    root = ET.fromstring(xml_bytes)
    sheet_data = root.find(f"{{{MAIN_NS}}}sheetData")
    if sheet_data is None:
        raise ValueError("Worksheet has no sheetData element")

    existing_rows = {int(row.attrib["r"]): row for row in sheet_data.findall(f"{{{MAIN_NS}}}row")}
    style_id = "36"
    template_row = existing_rows.get(2)
    if template_row is not None:
        for cell in template_row.findall(f"{{{MAIN_NS}}}c"):
            if cell.attrib.get("s"):
                style_id = cell.attrib["s"]
                break

    for row_number, row in existing_rows.items():
        if row_number < 2:
            continue
        for cell in row.findall(f"{{{MAIN_NS}}}c"):
            set_cell_value(cell, "")

    for record_index, record in enumerate(records, start=2):
        row = existing_rows.get(record_index)
        if row is None:
            row = ET.Element(
                f"{{{MAIN_NS}}}row",
                {"r": str(record_index), "spans": f"1:{column_count}"},
            )
            sheet_data.append(row)
            existing_rows[record_index] = row
        for cell in list(row.findall(f"{{{MAIN_NS}}}c")):
            row.remove(cell)

        values = value_builder(record)
        for column_number, value in enumerate(values, start=1):
            reference = f"{column_letter(column_number)}{record_index}"
            cell = ET.SubElement(
                row,
                f"{{{MAIN_NS}}}c",
                {"r": reference, "s": style_id},
            )
            set_cell_value(cell, value, numeric=column_number in numeric_columns)

    rows_sorted = sorted(list(sheet_data), key=lambda row: int(row.attrib.get("r", "0")))
    sheet_data[:] = rows_sorted
    max_row = max([minimum_rows, len(records) + 1])
    dimension = root.find(f"{{{MAIN_NS}}}dimension")
    if dimension is not None:
        dimension.attrib["ref"] = f"A1:{column_letter(column_count)}{max_row}"

    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def write_workbook(template, output, routes, signal_routes, diagnostic_routes, doip_can_mappings=()):
    channels = reference_channels(template)
    normalized = [normalize_mapping(record, channels) for record in doip_can_mappings]
    if len({mapping_key(record) for record in normalized}) != len(normalized):
        raise ValueError("DoIP_to_CAN 存在重复的 logicalAddress/Tester/CANBus")
    with zipfile.ZipFile(template, "r") as source:
        direct_sheet_path = worksheet_path(source, "直接报文路由")
        signal_sheet_path = worksheet_path(source, "信号路由")
        diagnostic_sheet_path = worksheet_path(source, "诊断报文路由")
        doip_sheet_path = worksheet_path(source, "DoIP_to_CAN")
        updated_doip_sheet = update_sheet_xml(source.read(doip_sheet_path), normalized, mapping_values, 7)
        updated_direct_sheet = update_sheet_xml(
            source.read(direct_sheet_path),
            routes,
            expected_values,
            18,
            numeric_columns={3, 11},
            minimum_rows=85,
        )
        updated_signal_sheet = update_sheet_xml(
            source.read(signal_sheet_path),
            signal_routes,
            expected_signal_values,
            11,
            numeric_columns={5, 6},
            minimum_rows=2,
        )
        updated_diagnostic_sheet = update_sheet_xml(
            source.read(diagnostic_sheet_path),
            diagnostic_routes,
            expected_diagnostic_values,
            32,
            numeric_columns={6, 13} | set(range(15, 31)),
            minimum_rows=500,
        )

        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".codex_gateway_",
            suffix=".xlsx",
            dir=output.parent,
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            with zipfile.ZipFile(temporary, "w") as destination:
                for info in source.infolist():
                    if info.filename == direct_sheet_path:
                        data = updated_direct_sheet
                    elif info.filename == signal_sheet_path:
                        data = updated_signal_sheet
                    elif info.filename == diagnostic_sheet_path:
                        data = updated_diagnostic_sheet
                    elif info.filename == doip_sheet_path:
                        data = updated_doip_sheet
                    else:
                        data = source.read(info.filename)
                    destination.writestr(info, data)
            os.replace(temporary, output)
        finally:
            if temporary.exists():
                temporary.unlink()


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Write direct-message, signal, CAN diagnostic routes and DoIP-to-CAN mappings "
            "into a copied gateway template."
        )
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument(
        "--template",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "assets" / "网关路由配置表空白模板.xlsx",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    version = str(manifest.get("output_version", "")).strip().lstrip("Vv")
    if not version:
        parser.error("Manifest output_version is required")
    routes = manifest.get("routes")
    if not isinstance(routes, list):
        parser.error("Manifest routes must be a list")
    signal_routes = manifest.get("signal_routes", [])
    if not isinstance(signal_routes, list):
        parser.error("Manifest signal_routes must be a list")
    diagnostic_routes = manifest.get("diagnostic_routes", [])
    if not isinstance(diagnostic_routes, list):
        parser.error("Manifest diagnostic_routes must be a list")
    doip_can_mappings = manifest.get("doip_can_mappings", [])
    if not isinstance(doip_can_mappings, list):
        parser.error("Manifest doip_can_mappings must be a list")
    if not args.template.is_file():
        parser.error(f"Template not found: {args.template}")

    output = resolve_output_path(args.output, version, args.overwrite)
    write_workbook(
        args.template.resolve(),
        output.resolve(),
        routes,
        signal_routes,
        diagnostic_routes,
        doip_can_mappings,
    )
    print(output.resolve())


if __name__ == "__main__":
    main()
