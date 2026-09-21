"""读取标准诊断页；有明确请求 CAN 端点时按双端 CAN 路由处理。"""

from decimal import Decimal

from davinci_gw.domain.models import DiagnosticEndpoint, DiagnosticRouteChange, OperationType, SourceLocation
from .workbook_schema import DIAGNOSTIC_HEADERS, DIAGNOSTIC_TRANSPORT_FIELDS
from .workbook_reader import _decimal, _integer, _is_blank, _issue, _operation, _required, _rows, _text


def channel_properties(channel: str) -> tuple[str, str, int, str]:
    """返回已确认的 Rx 类型、Tx 类型、长度和 CanTp 寻址类型。"""
    if channel in {"BDCAN", "DMCAN", "DGCAN"}:
        return "STANDARD_NO_FD_CAN", "STANDARD_CAN", 8, "CANTP_PHYSICAL"
    return "STANDARD_FD_CAN", "STANDARD_FD_CAN", 64, "CANTP_CANFD_PHYSICAL"


def read_diagnostics(sheet, mapping, path, issues, references):
    """沿用既有输入问题模型；缺失 N 参数不采用模板或基线默认值。"""
    routes = []
    seen = {}
    channels = {entry.channel_name for entry in references}
    for number, row in _rows(sheet, DIAGNOSTIC_HEADERS, mapping):
        before = len(issues)
        operation = _operation(row.get("操作类型"), path, sheet.title, number, issues)
        entry = _text(row, "诊断入口类型")
        if entry not in {"OBD_CAN", "OBD_ETH"}:
            issues.append(_issue(path, sheet.title, number, "诊断入口类型", entry,
                                 "仅允许 OBD_CAN 或 OBD_ETH。", "DIAGNOSTIC_ENTRY_INVALID"))
            continue
        _required(row, ("诊断请求端报文名称", "诊断应答端报文名称"), path, sheet.title, number, issues)
        endpoints = {}
        request_fields = ("诊断请求端CANID_REQ", "诊断请求端CANID_RES", "诊断请求端CAN通道")
        has_can_request = entry == "OBD_CAN" or any(not _is_blank(row.get(field)) for field in request_fields)
        for role in (("请求", "应答") if has_can_request else ("应答",)):
            prefix = f"诊断{role}端"
            identity = tuple(prefix + suffix for suffix in ("CANID_REQ", "CANID_RES", "CAN通道"))
            functional = _is_blank(row.get(prefix + "CANID_RES")) or row.get(prefix + "CANID_RES") == "/"
            _required(row, (identity[0], identity[2]) if functional else identity, path, sheet.title, number, issues)
            channel = _text(row, prefix + "CAN通道")
            if channel not in channels:
                issues.append(_issue(path, sheet.title, number, prefix + "CAN通道", channel,
                                     "未在引用数据中定义。", "DIAGNOSTIC_CHANNEL_INVALID"))
            ids = []
            for field in identity[:2]:
                value = row.get(field)
                if field.endswith("CANID_RES") and functional:
                    ids.append(None)
                    continue
                # Skill 的 CAN ID 是十六进制文本；数值单元格沿用直接报文读取约定。
                if isinstance(value, str) and not value.lower().startswith("0x"):
                    value = "0x" + value
                ids.append(_integer(value, minimum=0, maximum=0x1FFFFFFF, path=path,
                                    sheet=sheet.title, row_number=number, field=field, issues=issues))
            parameters = []
            if operation is OperationType.ADD:
                transport_fields = (DIAGNOSTIC_TRANSPORT_FIELDS[3:] if role == "请求" else DIAGNOSTIC_TRANSPORT_FIELDS[:3]) if functional else DIAGNOSTIC_TRANSPORT_FIELDS
                _required(row, tuple(prefix + field for field in transport_fields),
                          path, sheet.title, number, issues)
                for field in transport_fields:
                    value = row.get(prefix + field)
                    if _is_blank(value):
                        continue
                    if field == "BlockSize":
                        parsed = _integer(value, minimum=0, maximum=255, path=path, sheet=sheet.title,
                                          row_number=number, field=prefix + field, issues=issues)
                        if parsed is not None:
                            parameters.append((field, str(parsed)))
                    else:
                        parsed = _decimal(value, path, sheet.title, number, prefix + field, issues)
                        if parsed is not None:
                            parameters.append((field, format(parsed / Decimal(1000), "f")))
                if channel in channels:
                    rx, tx, length, _ = channel_properties(channel)
                    for suffix, expected in (("接收报文类型", rx), ("发送报文类型", tx), ("Length", length)):
                        field = prefix + suffix
                        value = row.get(field)
                        if str(value) != str(expected):
                            issues.append(_issue(path, sheet.title, number, field, value,
                                                 f"必须为 {expected}，不能覆盖通道规则。", "DIAGNOSTIC_CHANNEL_PROPERTY_CONFLICT"))
            if channel and ids[0] is not None and (functional or ids[1] is not None):
                endpoints[role] = DiagnosticEndpoint(channel, *ids, tuple(parameters))
        if len(issues) != before or operation is None:
            continue
        request = endpoints.get("请求")
        response = endpoints["应答"]
        if request and (request.response_id is None) != (response.response_id is None):
            issues.append(_issue(path, sheet.title, number, "CANID_RES", None,
                                 "请求与应答端的功能寻址标记必须一致。", "DIAGNOSTIC_ADDRESSING_CONFLICT"))
            continue
        if request and request.channel == response.channel:
            issues.append(_issue(path, sheet.title, number, "诊断应答端CAN通道", response.channel,
                                 "不能与请求端相同。", "DIAGNOSTIC_SAME_CHANNEL"))
            continue
        identity = (operation, entry, *((None,) if request is None else
                    (request.channel, request.request_id, request.response_id)),
                    response.channel, response.request_id, response.response_id)
        if identity in seen:
            issues.append(_issue(path, sheet.title, number, "操作类型", operation.value,
                                 f"与第 {seen[identity]} 行的诊断身份重复。", "DIAGNOSTIC_ROUTE_DUPLICATE"))
            continue
        seen[identity] = number
        routes.append(DiagnosticRouteChange(operation, entry, row["诊断请求端报文名称"],
                       row["诊断应答端报文名称"], request, response, SourceLocation(sheet.title, number)))
    return routes
