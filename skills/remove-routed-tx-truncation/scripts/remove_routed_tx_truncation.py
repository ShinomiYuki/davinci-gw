"""按实际引用删除普通路由和 CanTp 诊断路由 Tx 的 Truncation 参数。"""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import re
import sys

from lxml import etree

CANIF = "/MICROSAR/CanIf"
RX = f"{CANIF}/CanIfInitCfg/CanIfRxPduCfg"
TX = f"{CANIF}/CanIfInitCfg/CanIfTxPduCfg"
TRUNCATION = f"{TX}/CanIfTxPduTruncation"
PDU = "/MICROSAR/EcuC/EcucPduCollection/Pdu"
BSW = "/MICROSAR/PduR/PduRBswModules"
ROUTE = "/MICROSAR/PduR/PduRRoutingTables/PduRRoutingTable/PduRRoutingPath"
SRC = f"{ROUTE}/PduRSrcPdu"
DST = f"{ROUTE}/PduRDestPdu"
CANTP = "/MICROSAR/CanTp"
SDU_BASE = f"{CANTP}/CanTpConfig/CanTpChannel"
RX_SDU = f"{SDU_BASE}/CanTpRxNSdu"
TX_SDU = f"{SDU_BASE}/CanTpTxNSdu"
TX_BINDINGS = {
    f"{TX_SDU}/CanTpTxNPdu": ("CanTpTxNPduRef", TX_SDU, "CanTpTxNSduRef", DST),
    f"{RX_SDU}/CanTpTxFcNPdu": ("CanTpTxFcNPduRef", RX_SDU, "CanTpRxNSduRef", SRC),
}


class RouteIndex:
    """索引命名对象和反向引用，保留重复候选。"""

    def __init__(self, tree: etree._ElementTree) -> None:
        self.namespace = etree.QName(tree.getroot()).namespace
        if not self.namespace or etree.QName(tree.getroot()).localname != "AUTOSAR":
            raise ValueError("输入必须是带命名空间的 AUTOSAR ARXML")
        self.by_path = defaultdict(list)
        self.by_definition = defaultdict(list)
        self.referrers = defaultdict(list)
        self.paths = {}
        for node in tree.getroot().iter():
            if not isinstance(node.tag, str):
                continue
            name = self.text(node, "SHORT-NAME")
            if name:
                parent = node.getparent()
                while parent is not None and parent not in self.paths:
                    parent = parent.getparent()
                path = f"{self.paths.get(parent, '')}/{name}"
                self.paths[node] = path
                self.by_path[path].append(node)
            definition = self.text(node, "DEFINITION-REF")
            if definition:
                self.by_definition[definition].append(node)
            target = self.text(node, "VALUE-REF")
            if target:
                self.referrers[target].append(node)

    def text(self, node, name):
        return (node.findtext(f"{{{self.namespace}}}{name}") or "").strip()

    def definition(self, node):
        return self.text(node, "DEFINITION-REF")

    def values(self, node, group, definition, value_tag):
        return tuple(
            self.text(entry, value_tag)
            for entry in node.findall(f"{{{self.namespace}}}{group}/*")
            if self.definition(entry) == definition
        )

    def parameter(self, node, definition):
        return self.values(node, "PARAMETER-VALUES", definition, "VALUE")

    def references(self, node, definition):
        return self.values(node, "REFERENCE-VALUES", definition, "VALUE-REF")

    def unique(self, path, definition):
        nodes = self.by_path.get(path, ())
        if len(nodes) != 1 or self.definition(nodes[0]) != definition:
            raise ValueError(f"引用无法唯一解析为 {definition}：{path}")
        return nodes[0]

    def referenced(self, node, reference, definition):
        paths = self.references(node, reference)
        if len(paths) != 1:
            raise ValueError(f"引用数量不是 1：{reference}")
        return self.unique(paths[0], definition)

    def owner(self, entry):
        node = entry.getparent()
        while node is not None and node not in self.paths:
            node = node.getparent()
        return node

    def require(self, node, definition, value):
        if self.parameter(node, definition) != (value,):
            raise ValueError(f"参数不满足 {definition}={value}")

    def canif_module(self, node, reference):
        module = self.referenced(node, reference, BSW)
        return self.referenced(module, f"{BSW}/PduRBswModuleRef", CANIF)

    def module(self, node):
        for parent in node.iterancestors():
            if parent.tag == f"{{{self.namespace}}}ECUC-MODULE-CONFIGURATION-VALUES":
                return parent
        raise ValueError("对象缺少所属模块")

    def transport_frame(self, sdu, receive):
        """核对 N-SDU 的底层数据 PDU 和实际 CanIf 端点。"""
        definition = self.definition(sdu)
        prefix = "CanTpRx" if receive else "CanTpTx"
        child_definition = f"{definition}/{prefix}NPdu"
        children = [node for node in sdu.findall(f"{{{self.namespace}}}SUB-CONTAINERS/*")
                    if self.definition(node) == child_definition]
        if len(children) != 1:
            raise ValueError("CanTp N-SDU 无唯一底层数据 PDU")
        self.unique(self.paths[children[0]], child_definition)
        pdu = self.referenced(children[0], f"{child_definition}/{prefix}NPduRef", PDU)
        frame_definition = RX if receive else TX
        field = "CanIfRxPduRef" if receive else "CanIfTxPduRef"
        frames = [self.owner(entry) for entry in self.referrers[self.paths[pdu]]
                  if self.definition(entry) == f"{frame_definition}/{field}"]
        if len(frames) != 1 or self.definition(frames[0]) != frame_definition:
            raise ValueError("CanTp 底层数据 PDU 无唯一 CanIf 端点")
        frame = frames[0]
        self.unique(self.paths[frame], frame_definition)
        self.referenced(frame, f"{frame_definition}/{field}", PDU)
        self.unique(self.paths[self.module(frame)], CANIF)
        callback = "CanIfRxPduUserRxIndication" if receive else "CanIfTxPduUserTxConfirmation"
        self.require(frame, f"{frame_definition}/{callback}UL", "CAN_TP")
        self.require(frame, f"{frame_definition}/{callback}Name", "CanTp_RxIndication" if receive else "CanTp_TxConfirmation")

    def transport_route(self, leg):
        """确认已配置的传输路由及两端模块和 PDU 引用。"""
        parent = leg.getparent()
        route = parent.getparent() if parent is not None else None
        if route is None or self.definition(route) != ROUTE:
            raise ValueError("CanTp 上层 PduR 腿缺少 RoutingPath")
        self.unique(self.paths[route], ROUTE)
        self.require(route, f"{ROUTE}/PduRRoutingPathCommunicationType", "TRANSPORT_PROTOCOL")
        sources = [node for node in parent if self.definition(node) == SRC]
        destinations = [node for node in parent if self.definition(node) == DST]
        if len(sources) != 1 or not destinations:
            raise ValueError("传输路由缺少唯一源端或目标端")
        if self.definition(leg) == DST:
            checked_destinations = [leg]
        else:
            checked_destinations = [node for node in destinations
                                    if self.parameter(node, f"{DST}/PduRDestPduDirection") == ("TRANSMIT",)
                                    and self.parameter(node, f"{DST}/PduRDestPduRoutingType") == ("GATEWAY_ROUTING",)]
        if not checked_destinations:
            raise ValueError("传输路由没有可确认的网关目标")
        # 独立的本地转发目标不使用当前 CanTp Tx，不作为其共享业务证据。
        for node in sources + checked_destinations:
            source = self.definition(node) == SRC
            definition, field = (SRC, "PduRSrcPdu") if source else (DST, "PduRDestPdu")
            self.unique(self.paths[node], definition)
            self.require(node, f"{definition}/{field}Direction", "RECEIVE" if source else "TRANSMIT")
            if not source:
                self.require(node, f"{DST}/PduRDestPduRoutingType", "GATEWAY_ROUTING")
            pdu = self.referenced(node, f"{definition}/{field}Ref", PDU)
            module = self.referenced(node, f"{definition}/{field}PduRBswModulesRef", BSW)
            targets = self.references(module, f"{BSW}/PduRBswModuleRef")
            if len(targets) != 1:
                raise ValueError("传输路由模块引用不唯一")
            modules = self.by_path.get(targets[0], ())
            if len(modules) != 1 or self.definition(modules[0]) not in {CANTP, "/MICROSAR/DoIP"}:
                raise ValueError("传输路由包含无法识别的模块")
            if self.definition(modules[0]) == CANTP:
                sdu_definition = RX_SDU if source else TX_SDU
                reference = f"{sdu_definition}/{'CanTpRxNSduRef' if source else 'CanTpTxNSduRef'}"
                sdus = [self.owner(entry) for entry in self.referrers[self.paths[pdu]]
                        if self.definition(entry) == reference]
                if len(sdus) != 1 or self.definition(sdus[0]) != sdu_definition:
                    raise ValueError("传输路由无唯一对应 CanTp N-SDU")
                self.unique(self.paths[sdus[0]], sdu_definition)
                self.referenced(sdus[0], reference, PDU)
                if self.module(sdus[0]) is not modules[0]:
                    raise ValueError("CanTp N-SDU 与 PduR 模块引用不一致")
                self.transport_frame(sdus[0], source)
            else:
                direction = "Rx" if source else "Tx"
                doip_definition = f"/MICROSAR/DoIP/DoIPConfigSet/DoIPInterface/DoIPChannel/DoIPPduR{direction}Pdu"
                reference = f"{doip_definition}/DoIPPduR{direction}PduRef"
                endpoints = [self.owner(entry) for entry in self.referrers[self.paths[pdu]]
                             if self.definition(entry) == reference]
                if len(endpoints) != 1 or self.definition(endpoints[0]) != doip_definition:
                    raise ValueError("传输路由无唯一对应 DoIP 端点")
                self.unique(self.paths[endpoints[0]], doip_definition)
                self.referenced(endpoints[0], reference, PDU)
                if self.module(endpoints[0]) is not modules[0]:
                    raise ValueError("DoIP 端点与 PduR 模块引用不一致")
        return self.paths[route]

    def cantp_routes(self, tx):
        """沿数据或流控 PDU 定位实际参与路由的 N-SDU。"""
        self.require(tx, f"{TX}/CanIfTxPduUserTxConfirmationName", "CanTp_TxConfirmation")
        pdu = self.referenced(tx, f"{TX}/CanIfTxPduRef", PDU)
        bindings = []
        for entry in self.referrers[self.paths[pdu]]:
            owner = self.owner(entry)
            if owner is tx and self.definition(entry) == f"{TX}/CanIfTxPduRef":
                continue
            definition = self.definition(owner) if owner is not None else ""
            if definition not in TX_BINDINGS:
                raise ValueError("CanTp 底层 PDU 被其他业务或不明确对象共享")
            field, sdu_definition, sdu_field, leg_definition = TX_BINDINGS[definition]
            if self.definition(entry) != f"{definition}/{field}":
                raise ValueError("CanTp 底层 PDU 引用类型不明确")
            self.unique(self.paths[owner], definition)
            self.referenced(owner, f"{definition}/{field}", PDU)
            group = owner.getparent()
            sdu = group.getparent() if group is not None else None
            if sdu is None or self.definition(sdu) != sdu_definition:
                raise ValueError("CanTp 底层 PDU 缺少所属 N-SDU")
            self.unique(self.paths[sdu], sdu_definition)
            upper = self.referenced(sdu, f"{sdu_definition}/{sdu_field}", PDU)
            bindings.append((sdu, upper, sdu_field, leg_definition))
        if not bindings or len(bindings) > 2 or len({self.definition(item[0]) for item in bindings}) != len(bindings):
            raise ValueError("CanTp 底层 Tx 对应 N-SDU 不唯一或缺失")
        channels = {item[0].getparent().getparent() for item in bindings}
        if len(channels) != 1:
            raise ValueError("CanTp 底层 Tx 被不同 Channel 共享")
        routes = []
        queues = []
        for sdu, upper, sdu_field, leg_definition in bindings:
            leg_field = "PduRSrcPduRef" if leg_definition == SRC else "PduRDestPduRef"
            sdu_routes = []
            for entry in self.referrers[self.paths[upper]]:
                owner = self.owner(entry)
                if owner is sdu and self.definition(entry) == f"{self.definition(sdu)}/{sdu_field}":
                    continue
                queue_definition = "/MICROSAR/PduR/PduRRoutingTables/PduRQueue/PduRSharedBufferQueue"
                if owner is not None and self.definition(owner) == queue_definition and self.definition(entry) == f"{queue_definition}/PduRImplicitTxBufferRef":
                    queues.append(owner.getparent().getparent())
                    continue
                if owner is None or self.definition(owner) != leg_definition or self.definition(entry) != f"{leg_definition}/{leg_field}":
                    raise ValueError("CanTp 上层 PDU 被其他业务或不明确对象共享")
                sdu_routes.append(self.transport_route(owner))
            if not sdu_routes:
                raise ValueError("CanTp N-SDU 未参与 PduR 路由，保持原样")
            routes.extend(sdu_routes)
        for queue in queues:
            queue_path = self.paths[queue]
            self.unique(queue_path, "/MICROSAR/PduR/PduRRoutingTables/PduRQueue")
            users = self.referrers[queue_path]
            if not users:
                raise ValueError("CanTp 上层缓冲队列无路由引用")
            for entry in users:
                owner = self.owner(entry)
                if owner is None or self.definition(owner) != DST or self.definition(entry) != f"{DST}/PduRQueueRef":
                    raise ValueError("CanTp 上层缓冲队列被不明确对象共享")
                # 队列可以服务其他完整网关路由，不等于当前 Tx 被本地业务使用。
                self.transport_route(owner)
        return sorted(set(routes))

    def direct_destination(self, destination):
        """确认一个目标腿的完整普通 CanIf 源链。"""
        self.unique(self.paths[destination], DST)
        self.require(destination, f"{DST}/PduRDestPduDirection", "TRANSMIT")
        self.require(destination, f"{DST}/PduRDestPduRoutingType", "GATEWAY_ROUTING")
        self.require(destination, f"{DST}/PduRDestPduDataProvision", "PDUR_DIRECT")
        self.canif_module(destination, f"{DST}/PduRDestPduPduRBswModulesRef")
        parent = destination.getparent()
        route = parent.getparent() if parent is not None else None
        if route is None or self.definition(route) != ROUTE:
            raise ValueError("目标腿缺少 RoutingPath 父容器")
        self.unique(self.paths[route], ROUTE)
        self.require(route, f"{ROUTE}/PduRRoutingPathCommunicationType", "COMMUNICATION_INTERFACE")
        sources = [node for node in parent if self.definition(node) == SRC]
        if len(sources) != 1:
            raise ValueError("RoutingPath 源端数量不是 1")
        source = sources[0]
        self.unique(self.paths[source], SRC)
        self.require(source, f"{SRC}/PduRSrcPduDirection", "RECEIVE")
        source_module = self.canif_module(source, f"{SRC}/PduRSrcPduPduRBswModulesRef")
        pdu = self.referenced(source, f"{SRC}/PduRSrcPduRef", PDU)
        receive = []
        for entry in self.referrers[self.paths[pdu]]:
            owner = self.owner(entry)
            if owner is not None and self.definition(owner) == RX and self.definition(entry) == f"{RX}/CanIfRxPduRef":
                receive.append(owner)
        if len(receive) != 1:
            raise ValueError("源 EcuC 无唯一 CanIf Rx 引用")
        rx = receive[0]
        self.unique(self.paths[rx], RX)
        self.referenced(rx, f"{RX}/CanIfRxPduRef", PDU)
        if self.module(rx) is not source_module:
            raise ValueError("CanIf Rx 与源模块引用不一致")
        self.require(rx, f"{RX}/CanIfRxPduUserRxIndicationUL", "PDUR")
        self.require(rx, f"{RX}/CanIfRxPduUserRxIndicationName", "PduR_CanIfRxIndication")
        return self.paths[route]

    def target_routes(self, tx):
        """拒绝未知引用或混合业务共享的目标。"""
        self.unique(self.paths[tx], TX)
        if self.referrers[self.paths[tx]]:
            raise ValueError("CanIf Tx 容器存在其他引用，无法排除业务共享")
        if self.parameter(tx, f"{TX}/CanIfTxPduUserTxConfirmationUL") == ("CAN_TP",):
            return self.cantp_routes(tx)
        self.require(tx, f"{TX}/CanIfTxPduUserTxConfirmationUL", "NONE")
        self.require(tx, f"{TX}/CanIfTxPduUserTxConfirmationName", "NULL_PTR")
        pdu = self.referenced(tx, f"{TX}/CanIfTxPduRef", PDU)
        routes = []
        for entry in self.referrers[self.paths[pdu]]:
            owner = self.owner(entry)
            if owner is tx and self.definition(entry) == f"{TX}/CanIfTxPduRef":
                continue
            if owner is not None and self.definition(owner) == DST and self.definition(entry) == f"{DST}/PduRDestPduRef":
                self.referenced(owner, f"{DST}/PduRDestPduRef", PDU)
                if self.canif_module(owner, f"{DST}/PduRDestPduPduRBswModulesRef") is not self.module(tx):
                    raise ValueError("CanIf Tx 与目标模块引用不一致")
                routes.append(self.direct_destination(owner))
            else:
                raise ValueError(f"目标 EcuC 被其他业务或不明确对象共享：{self.paths.get(owner, self.definition(entry))}")
        if not routes:
            raise ValueError("没有完整普通直接路由目标引用")
        return sorted(set(routes))


def clean(input_path: Path, output_path: Path | None = None) -> dict:
    """预览或写入新文件；不覆盖输入和任何既有输出。"""
    source = input_path.expanduser().resolve(strict=True)
    output = output_path.expanduser().resolve() if output_path is not None else None
    if output is not None:
        if output.suffix.lower() != ".arxml" or output == source or output.exists():
            raise ValueError("输出必须是尚不存在的新 .arxml 文件，禁止覆盖输入或已有文件")
        if not output.parent.is_dir():
            raise ValueError("输出目录不存在")
    parser = etree.XMLParser(resolve_entities=False, no_network=True, remove_blank_text=False, huge_tree=True)
    tree = etree.parse(str(source), parser)
    index = RouteIndex(tree)
    report = {"input": str(source), "output": str(output) if output else None,
              "deleted_parameter_count": 0, "changed": [], "already_absent": [], "skipped": []}
    for tx in index.by_definition[TX]:
        path = index.paths[tx]
        try:
            routes = index.target_routes(tx)
        except ValueError as exc:
            report["skipped"].append({"path": path, "reason": str(exc)})
            continue
        entries = [entry for entry in tx.findall(f"{{{index.namespace}}}PARAMETER-VALUES/*")
                   if index.definition(entry) == TRUNCATION]
        if not entries:
            report["already_absent"].append({"path": path, "routes": routes})
            continue
        report["changed"].append({"path": path, "routes": routes, "count": len(entries)})
        report["deleted_parameter_count"] += len(entries)
        for entry in entries:
            entry.getparent().remove(entry)
    if output is not None:
        options = {"encoding": tree.docinfo.encoding or "UTF-8", "pretty_print": False}
        with source.open("rb") as stream:
            prefix = stream.read(512)
        declaration = re.match(br"\s*<\?xml\s+([^?]+)\?>", prefix)
        options["xml_declaration"] = declaration is not None
        standalone = re.search(br"standalone\s*=\s*['\"](yes|no)['\"]", declaration.group(1)) if declaration else None
        if standalone:
            options["standalone"] = standalone.group(1) == b"yes"
        payload = etree.tostring(tree, **options)
        verified = RouteIndex(etree.ElementTree(etree.fromstring(payload, parser)))
        for item in report["changed"]:
            node = verified.unique(item["path"], TX)
            if any(verified.definition(entry) == TRUNCATION for entry in node.findall(f"{{{verified.namespace}}}PARAMETER-VALUES/*")):
                raise ValueError(f"输出校验失败，参数仍存在：{item['path']}")
        with output.open("xb") as stream:
            try:
                stream.write(payload)
            except BaseException:
                stream.close()
                output.unlink()
                raise
    return report


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", type=Path, help="省略时仅预览")
    args = parser.parse_args()
    try:
        print(json.dumps(clean(args.input, args.output), ensure_ascii=False, indent=2))
    except (OSError, ValueError, etree.XMLSyntaxError) as exc:
        parser.exit(1, f"清理失败：{exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
