"""读取七列 DoIP 映射快照，保留真实 Tester 和 CAN 网段。"""

from davinci_gw.domain.models import DoIPMapping, SourceLocation, ValidationSeverity
from .workbook_reader import _integer, _is_blank, _issue, _required, _rows

DOIP_SHEET = "DoIP_to_CAN"
DOIP_HEADERS = ("Tester", "logicalAddress", "RouterType", "CANBus", "RequestCanId", "RespCanId", "Functional")
TESTER_TYPES = {0x0E80: "OBD", 0x0E81: "OBD_Remote", 0x0F00: "OTA", 0x0F01: "OTA_Remote"}


def read_doip_mappings(sheet, mapping, path, issues, references):
    """校验地址、业务和寻址类型；完全相同的映射只执行一次。"""
    channels = {entry.channel_name for entry in references}
    routes, seen, groups = [], {}, {}
    for number, row in _rows(sheet, DOIP_HEADERS, mapping):
        before = len(issues)
        _required(row, DOIP_HEADERS[:5], path, sheet.title, number, issues)
        functional_text = row.get("Functional")
        functional = functional_text == "YES"
        if not _is_blank(functional_text) and not functional:
            issues.append(_issue(path, sheet.title, number, "Functional", functional_text,
                                 "仅允许 YES 或空白。", "DOIP_FUNCTIONAL_INVALID"))
        if functional and not _is_blank(row.get("RespCanId")):
            issues.append(_issue(path, sheet.title, number, "RespCanId", row.get("RespCanId"),
                                 "功能寻址的响应必须留空。", "DOIP_FUNCTIONAL_RESPONSE_INVALID"))
        if not functional:
            _required(row, ("RespCanId",), path, sheet.title, number, issues)
        parsed = {}
        for field, maximum in (("Tester", 0xFFFF), ("logicalAddress", 0xFFFF),
                               ("RequestCanId", 0x1FFFFFFF), ("RespCanId", 0x1FFFFFFF)):
            if field == "RespCanId" and functional:
                parsed[field] = None
                continue
            value = row.get(field)
            if isinstance(value, str) and not value.lower().startswith("0x"):
                value = "0x" + value
            parsed[field] = _integer(value, minimum=0, maximum=maximum, path=path,
                                     sheet=sheet.title, row_number=number, field=field, issues=issues)
        tester, router, channel = parsed["Tester"], row.get("RouterType"), row.get("CANBus")
        if tester not in TESTER_TYPES or TESTER_TYPES[tester] != router:
            issues.append(_issue(path, sheet.title, number, "RouterType", router,
                                 "与 Tester 的业务对应关系不符。", "DOIP_TESTER_INVALID"))
        if channel not in channels:
            issues.append(_issue(path, sheet.title, number, "CANBus", channel,
                                 "未在引用数据中定义。", "DOIP_CHANNEL_INVALID"))
        if len(issues) != before:
            continue
        route = DoIPMapping(tester, parsed["logicalAddress"], router, channel,
                            parsed["RequestCanId"], parsed["RespCanId"], functional,
                            SourceLocation(sheet.title, number))
        group = (route.tester, route.logical_address)
        if group in groups and groups[group] != functional:
            issues.append(_issue(path, sheet.title, number, "Functional", functional_text,
                                 "同一 Tester 和逻辑地址不能混用物理与功能寻址。", "DOIP_ADDRESSING_CONFLICT"))
        groups[group] = functional
        key = (*group, channel)
        signature = (router, route.request_id, route.response_id, functional)
        if key in seen:
            if seen[key] != signature:
                issues.append(_issue(path, sheet.title, number, "CANBus", channel,
                                     "同一映射身份的请求、响应或寻址类型冲突。", "DOIP_MAPPING_CONFLICT"))
            else:
                issue = _issue(path, sheet.title, number, "CANBus", channel,
                               "与已有映射完全相同，已去重。", "DOIP_MAPPING_DUPLICATE")
                from dataclasses import replace
                issues.append(replace(issue, severity=ValidationSeverity.WARNING))
            continue
        seen[key] = signature
        routes.append(route)
    return routes
