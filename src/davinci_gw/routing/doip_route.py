"""在 CAN 前置完整后，按真实引用配置 DoIP 请求及物理响应。"""

from collections import defaultdict
from copy import deepcopy

from davinci_gw.arxml.namespace import qualified
from davinci_gw.domain.models import (
    DiagnosticEndpoint, MutationAction, MutationKind, MutationOperation, MutationPlan,
    ValidationIssue, ValidationSeverity,
)
from davinci_gw.modules import definitions as d
from davinci_gw.modules.common import HandleAllocator, definition_ref, semantic_values
from .diagnostic_route import DiagnosticConflict, DiagnosticMissing, DiagnosticRoutePlanner, DiagnosticSkipped
from .naming import safe_name

INTERFACE = "/MICROSAR/DoIP/DoIPConfigSet/DoIPInterface"
TESTER = INTERFACE + "/DoIPTester"
ACTIVATION = INTERFACE + "/DoIPRoutingActivation"
TARGET = INTERFACE + "/DoIPConnections/DoIPTargetAddress"
CHANNEL = INTERFACE + "/DoIPChannel"
RX = CHANNEL + "/DoIPPduRRxPdu"
TX = CHANNEL + "/DoIPPduRTxPdu"
TARGET_REF = ACTIVATION + "/DoIPTargetAddressRef"
ID_FIELDS = (CHANNEL + "/DoIPChannelId", RX + "/DoIPPduRRxPduId", TX + "/DoIPPduRTxPduId",
             d.PDUR_SRC_HANDLE, d.PDUR_DEST_HANDLE)


class DoIPRoutePlanner(DiagnosticRoutePlanner):
    """复用 CAN 引用定位与已有变更操作，不自动补建 CAN 端点。"""

    def __init__(self, workbook, ecuc, canif, pdur, groups, missing_policy=None):
        super().__init__(workbook, ecuc, canif, pdur, groups)
        if missing_policy not in {None, "BLOCK", "SKIP"}:
            raise ValueError("DoIP 前置缺失策略仅允许 BLOCK、SKIP 或未选择。")
        self.missing_policy = missing_policy
        self._destinations = {}
        self._sources = {}
        for node in self.index.find_by_definition_ref(d.PDUR_DEST):
            values = self.values(node)[1].get(d.PDUR_DEST_PDU_REF, ())
            if len(values) == 1:
                self._destinations.setdefault(values[0], []).append(node)
        for node in self.index.find_by_definition_ref(d.PDUR_SRC):
            values = self.values(node)[1].get(d.PDUR_SRC_PDU_REF, ())
            if len(values) == 1:
                self._sources.setdefault(values[0], []).append(node)

    def module(self, module_definition):
        paths = []
        for node in self.index.find_by_definition_ref(d.PDUR_BSW_MODULE):
            target = self.ref(node, d.PDUR_BSW_MODULE_REF)
            referenced = self.index.find_by_path(target)
            if len(referenced) == 1 and definition_ref(referenced[0], self.ns) == module_definition:
                paths.append(self.path(node))
        return self.one(paths, f"PduR 的 {module_definition} 模块引用")

    def routing_path(self, source, module):
        nodes = [n for n in self._sources.get(source, ())
                 if self.values(n)[1].get(d.PDUR_SRC_MODULE_REF) == (module,)]
        paths = [self.path(n.getparent().getparent()) for n in nodes]
        if not paths:
            if self._sources.get(source):
                raise DiagnosticConflict(f"Source {source} 的已有 PduR 模块引用与 {module} 不符。")
            return None
        return self.one(paths, f"Source {source} 的 PduR 路径")

    def queue(self, path=None, target=None):
        """沿关联 TP 路径取唯一既有 Queue，完全保留参数和缓冲引用。"""
        if path is None:
            nodes = [n for n in self._destinations.get(target, ())
                     if self.values(n)[1].get(d.PDUR_DEST_MODULE_REF) == (self.cantp_module(),)
                     and self.path(n.getparent().getparent()) in self.queue_groups.destinations]
            paths = {self.path(n.getparent().getparent()) for n in nodes}
        else:
            paths = {path}
        if not paths:
            if self._destinations.get(target):
                raise DiagnosticConflict(f"上层 CAN Tx PDU {target} 的已有目标模块或路径传输类型不符。")
            raise DiagnosticMissing(f"上层 CAN Tx PDU {target} 缺少 PduR 请求目标及 QueueRef。")
        queues = set()
        missing_reference = False
        for candidate in paths:
            owner = self.node(candidate, d.PDUR_PATH)
            if self.values(owner)[0].get(d.PDUR_PATH_COMM_TYPE) != ("TRANSPORT_PROTOCOL",):
                raise DiagnosticConflict(f"{candidate} 不是传输协议路径。")
            linked = self.queue_groups.connected_queues(candidate)
            for entries in linked.values():
                for values in entries:
                    if not values:
                        missing_reference = True
                        continue
                    if len(values) != 1:
                        raise DiagnosticConflict(f"{candidate} 的关联目标缺少唯一 QueueRef。")
                    queues.update(values)
        if len(queues) > 1:
            raise DiagnosticConflict(f"关联 CAN 路径 {sorted(paths)} 已有不同 QueueRef：{sorted(queues)}。")
        if missing_reference:
            raise DiagnosticMissing(f"关联 CAN 路径 {sorted(paths)} 的目标缺少 QueueRef。")
        selected = self.one(queues, f"关联 CAN 路径 {sorted(paths)} 的 QueueRef")
        self.node(selected, d.PDUR_QUEUE)
        return selected

    def prerequisite(self, route):
        endpoint = DiagnosticEndpoint(route.can_bus, route.request_id, route.response_id)
        try:
            chain = self.locate(endpoint, False)
        except DiagnosticSkipped as exc:
            raise DiagnosticMissing(str(exc)) from exc
        if chain is None:
            expected = f"CanIfTx 请求 0x{route.request_id:X}"
            if route.response_id is not None:
                expected += f"、CanIfRx 响应 0x{route.response_id:X}"
            if route.functional and self.frames(route.can_bus, route.request_id, False):
                raise DiagnosticConflict(f"{route.can_bus} 请求 0x{route.request_id:X} 的已有 CanIf 帧类型与功能寻址不符。")
            raise DiagnosticMissing(f"{route.can_bus} 的 {expected} 缺少 CAN_TP 端点，无法解析底层 EcuC→CanTp→上层 EcuC 引用。")
        request_queue = self.queue(target=chain.tx_sdu)
        response_path = response_queue = None
        if not route.functional:
            response_path = self.routing_path(chain.rx_sdu, self.cantp_module())
            if response_path is None:
                raise DiagnosticMissing(f"CAN Rx 上层 PDU {chain.rx_sdu} 缺少 PduR 响应 Source。")
            response_queue = self.queue(path=response_path)
        return chain, request_queue, response_path, response_queue

    def parameters(self, node, overrides=None):
        values = self.values(node)[0]
        if any(len(v) != 1 for v in values.values()):
            raise DiagnosticConflict(f"样例 {self.path(node)} 存在多值参数。")
        result = {key: value[0] for key, value in values.items()}
        for field in ID_FIELDS:
            if field in result:
                if field not in self.handles:
                    self.handles[field] = HandleAllocator(self.index, self.ns, field)
                result[field] = str(self.handles[field].allocate())
        result.update(overrides or {})
        return tuple(result.items())

    def clone(self, kind, sample, parent, name, routes, overrides=None, references=None):
        """新对象参数来自明确的同业务、同寻址样例；名称冲突立即阻断。"""
        operation = MutationOperation(
            kind, parent, name, definition_ref(sample, self.ns),
            parameters=self.parameters(sample, overrides),
            references=(references if references is not None else
                        tuple((k, v) for k, values in self.values(sample)[1].items() for v in values)),
            source_locations=tuple(r.source for r in routes),
        )
        if self.index.find_by_path(operation.object_path) or any(
            op.object_path == operation.object_path for op in self.operations
        ):
            raise DiagnosticConflict(f"新增名称已占用：{operation.object_path}。")
        if operation.definition_ref in {RX, TX, d.PDUR_SRC, d.PDUR_DEST}:
            named = [n for n in self.index.find_by_short_name(name)
                     if definition_ref(n, self.ns) == operation.definition_ref]
            planned = [op for op in self.operations if op.short_name == name
                       and op.definition_ref == operation.definition_ref]
            if named or planned:
                raise DiagnosticConflict(f"新增符号名 {name} 已在同类容器中使用，不能重复创建。")
        self.operations.append(operation)
        return operation.object_path

    def business(self, routes):
        route = routes[0]
        testers = [n for n in self.index.find_by_definition_ref(TESTER)
                   if self.values(n)[0].get(TESTER + "/DoIPTesterSA") == (str(route.tester),)]
        tester_path = self.one([self.path(n) for n in testers], f"Tester 0x{route.tester:04X}")
        tester = self.node(tester_path, TESTER)
        activation = self.ref(tester, TESTER + "/DoIPRoutingActivationRef")
        self.node(activation, ACTIVATION)
        channels = []
        for node in self.index.find_by_definition_ref(CHANNEL):
            if self.values(node)[1].get(CHANNEL + "/DoIPChannelSARef") != (tester_path,):
                continue
            target = self.node(self.ref(node, CHANNEL + "/DoIPChannelTARef"), TARGET)
            address = self.ref_value(target, TARGET + "/DoIPTargetAddressValue")
            channels.append((node, target, address))
        existing = [(n, t) for n, t, a in channels if a == str(route.logical_address)]
        if len(existing) > 1:
            raise DiagnosticConflict(f"Tester/地址 0x{route.tester:04X}/0x{route.logical_address:04X} 对应多个 Channel。")
        # 使用用户明确给定的 0x0003 物理样例或 0xE400 功能样例，不按名称推断配置身份。
        sample_address = str(0xE400 if route.functional else 3)
        samples = [(n, t) for n, t, a in channels if a == sample_address]
        if len(samples) != 1:
            raise DiagnosticConflict(f"业务 {route.router_type} 缺少唯一地址 {sample_address} 的寻址样例，请指定参数来源。")
        return tester_path, activation, existing[0] if existing else None, samples[0]

    def ref_value(self, node, definition):
        return self.one(self.values(node)[0].get(definition, ()), f"{self.path(node)} 的 {definition}")

    def child_pdu(self, channel, definition, functional):
        children = self.children(channel, definition)
        if len(children) != (0 if functional and definition == TX else 1):
            raise DiagnosticConflict(f"Channel {self.path(channel)} 的 {definition} 数量与寻址类型不符。")
        if not children:
            return None
        pdu = self.ref(children[0], definition + ("/DoIPPduRRxPduRef" if definition == RX else "/DoIPPduRTxPduRef"))
        self.node(pdu, d.ECUC_PDU)
        return pdu

    def activation_reference(self, activation, target, routes):
        values = self.values(self.node(activation, ACTIVATION))[1].get(TARGET_REF, ())
        if values.count(target) > 1:
            raise DiagnosticConflict(f"RoutingActivation {activation} 已有重复目标 {target}。")
        if target not in values:
            self.operations.append(MutationOperation(
                MutationKind.DOIP_REFERENCE, activation, "", TARGET_REF,
                action=MutationAction.ADD_REFERENCE, references=((TARGET_REF, target),),
                source_locations=tuple(r.source for r in routes),
            ))

    def channel(self, routes):
        route = routes[0]
        tester, activation, existing, samples = self.business(routes)
        base, remote = route.router_type.removesuffix("_Remote"), "_Remote" if route.router_type.endswith("_Remote") else ""
        token = f"DoIp{route.logical_address:03X}"
        sample_channel, sample_target = samples
        sample_rx = self.child_pdu(sample_channel, RX, route.functional)
        sample_tx = self.child_pdu(sample_channel, TX, route.functional)
        if existing:
            channel, target = existing
            rx, tx = self.child_pdu(channel, RX, route.functional), self.child_pdu(channel, TX, route.functional)
            self.activation_reference(activation, self.path(target), routes)
            return rx, tx, sample_rx, sample_tx
        target = self.clone(MutationKind.DOIP_CONTAINER, sample_target,
                            self.path(sample_target.getparent().getparent()),
                            f"GWT_DoIPTargetAddress_{base}_{token}{remote}", routes,
                            {TARGET + "/DoIPTargetAddressValue": str(route.logical_address)})
        channel = self.clone(MutationKind.DOIP_CONTAINER, sample_channel,
                             self.path(sample_channel.getparent().getparent()),
                             f"GWT_DoIPChannel_{base}_{token}{remote}", routes,
                             references=((CHANNEL + "/DoIPChannelSARef", tester), (CHANNEL + "/DoIPChannelTARef", target)))
        pdus = []
        for definition, sample, direction in ((RX, sample_rx, "Rx"), (TX, sample_tx, "Tx")):
            if sample is None:
                pdus.append(None)
                continue
            sample_pdu = self.node(sample, d.ECUC_PDU)
            pdu = self.clone(MutationKind.ECUC_PDU, sample_pdu, self.ecuc.parent_path,
                             f"GWT_Pdu_{base}_{token}_{direction}{remote}", routes)
            child = self.children(sample_channel, definition)[0]
            self.clone(MutationKind.DOIP_CONTAINER, child, channel,
                       f"DoIPPduR{direction}Pdu_{base}_{token}{remote}", routes,
                       references=((definition + f"/DoIPPduR{direction}PduRef", pdu),))
            pdus.append(pdu)
        self.activation_reference(activation, target, routes)
        return *pdus, sample_rx, sample_tx

    def destination(self, path, target, module, queue, name, sample, route):
        existing = [n for n in self._destinations.get(target, ())
                    if self.path(n.getparent().getparent()) == path]
        if existing:
            if len(existing) != 1:
                raise DiagnosticConflict(f"路径 {path} 到 {target} 存在重复目标。")
            values, refs = self.values(existing[0])
            expected = {d.PDUR_DEST_DIRECTION: "TRANSMIT", d.PDUR_DEST_ROUTING_TYPE: "GATEWAY_ROUTING",
                        d.PDUR_DEST_PROCESSING: "IMMEDIATE"}
            if any(values.get(k) != (v,) for k, v in expected.items()) or (
                refs.get(d.PDUR_DEST_MODULE_REF) != (module,) or refs.get(d.PDUR_QUEUE_REF) != (queue,)
            ):
                raise DiagnosticConflict(f"已有目标 {self.path(existing[0])} 的模块、方向或 Queue 冲突。")
            return
        # 同一 Tx PDU 可能关联多个逻辑地址；每条实际路径独立追加目标，避免重复创建。
        if any(op.parent_path == path and op.reference(d.PDUR_DEST_PDU_REF) == target for op in self.operations):
            return
        references = dict((k, self.one(v, f"目标样例 {k}")) for k, v in self.values(sample)[1].items())
        references.update({d.PDUR_DEST_PDU_REF: target, d.PDUR_DEST_MODULE_REF: module, d.PDUR_QUEUE_REF: queue})
        self.clone(MutationKind.PDUR_DEST_PDU, sample, path, name, [route], references=tuple(references.items()))

    def group(self, routes, prerequisites):
        before = len(self.operations)
        rx, tx, sample_rx, sample_tx = self.channel(routes)
        doip_module, can_module = self.module("/MICROSAR/DoIP"), self.cantp_module()
        path = self.routing_path(rx, doip_module)
        sample_path = self.routing_path(sample_rx, doip_module)
        if sample_path is None:
            raise DiagnosticConflict(f"DoIP Rx 样例 {sample_rx} 没有完整 PduR 请求路径。")
        sample_owner = self.node(sample_path, d.PDUR_PATH)
        sample_sources = self.children(sample_owner, d.PDUR_SRC)
        if len(sample_sources) != 1:
            raise DiagnosticConflict(f"样例 {sample_path} 的 Source 不唯一。")
        if self.values(sample_owner)[0].get(d.PDUR_PATH_COMM_TYPE) != ("TRANSPORT_PROTOCOL",) or (
            self.values(sample_sources[0])[0].get(d.PDUR_SRC_DIRECTION) != ("RECEIVE",)
        ):
            raise DiagnosticConflict(f"样例 {sample_path} 不是 TRANSPORT_PROTOCOL、Source RECEIVE 的有效请求路径。")
        route = routes[0]
        base, remote = route.router_type.removesuffix("_Remote"), "_Remote" if route.router_type.endswith("_Remote") else ""
        token = f"DoIp{route.logical_address:03X}"
        if path is None:
            path = self.clone(MutationKind.PDUR_ROUTING_PATH, sample_owner, self.pdur.path_parent,
                              f"GWT_PduRRoutingPath_{token}_{base}_Rx{remote}", routes)
            self.clone(MutationKind.PDUR_SRC_PDU, sample_sources[0], path,
                       f"PduRSrcPdu_{token}_{base}_Rx{remote}", routes,
                       references=((d.PDUR_SRC_PDU_REF, rx), (d.PDUR_SRC_MODULE_REF, doip_module)))
        else:
            owner = self.node(path, d.PDUR_PATH)
            sources = self.children(owner, d.PDUR_SRC)
            if self.values(owner)[0].get(d.PDUR_PATH_COMM_TYPE) != ("TRANSPORT_PROTOCOL",) or len(sources) != 1 or (
                self.values(sources[0])[0].get(d.PDUR_SRC_DIRECTION) != ("RECEIVE",)
            ):
                raise DiagnosticConflict(f"已有 DoIP 请求路径 {path} 不是完整传输协议 Source。")
        request_samples = [n for n in self.children(sample_owner, d.PDUR_DEST)
                           if self.values(n)[1].get(d.PDUR_DEST_MODULE_REF) == (can_module,)]
        if not request_samples:
            raise DiagnosticConflict(f"DoIP 样例 {sample_path} 没有 CAN 请求目标参数。")
        signatures = {tuple(self.values(n)[0].items()) for n in request_samples}
        # Handle ID 按完整作用域分配，不参与样例参数一致性比较。
        signatures = {tuple(sorted((k, v) for k, v in signature if k != d.PDUR_DEST_HANDLE)) for signature in signatures}
        if len(signatures) != 1:
            raise DiagnosticConflict(f"DoIP 样例 {sample_path} 的 CAN 目标参数冲突，请指定参数来源。")
        response_sample = None
        if tx:
            samples = [n for n in self._destinations.get(sample_tx, ())
                       if self.values(n)[1].get(d.PDUR_DEST_MODULE_REF) == (doip_module,)]
            if len(samples) != 1:
                raise DiagnosticConflict(f"DoIP Tx 样例 {sample_tx} 缺少唯一响应目标样例。")
            response_sample = samples[0]
        shared_changed = len(self.operations) > before
        added = 0
        for route in routes:
            previous = len(self.operations)
            chain, request_queue, response_path, response_queue = prerequisites[route]
            channel_name = safe_name(route.can_bus)
            self.destination(path, chain.tx_sdu, can_module, request_queue,
                             f"PduRDestPdu_{base}_{token}To{channel_name}_Tx{remote}", request_samples[0], route)
            if tx:
                self.destination(response_path, tx, doip_module, response_queue,
                                 f"PduRDestPdu_{base}_{channel_name}To{token}_Tx{remote}", response_sample, route)
            added += shared_changed or len(self.operations) > previous
        return added, len(routes) - added

    def plan(self):
        issues, prerequisites, groups = [], {}, defaultdict(list)
        endpoints = {}
        for route in self.workbook.doip_mappings:
            try:
                key = (route.can_bus, route.request_id, route.response_id, route.functional)
                if key not in endpoints:
                    try:
                        endpoints[key] = self.prerequisite(route)
                    except DiagnosticConflict as exc:
                        endpoints[key] = exc
                if isinstance(endpoints[key], DiagnosticConflict):
                    raise endpoints[key]
                prerequisites[route] = endpoints[key]
                groups[(route.tester, route.logical_address)].append(route)
            except DiagnosticConflict as exc:
                missing = isinstance(exc, DiagnosticMissing)
                skipped = missing and self.missing_policy == "SKIP"
                response = "空（功能寻址）" if route.response_id is None else f"0x{route.response_id:X}"
                issues.append(ValidationIssue(
                    code=("DOIP_PREREQUISITE_SKIPPED" if skipped else "DOIP_PREREQUISITE_MISSING")
                         if missing else "DOIP_PREREQUISITE_CONFLICT",
                    message=f"Tester=0x{route.tester:04X}，logicalAddress=0x{route.logical_address:04X}，"
                            f"CANBus={route.can_bus}，请求=0x{route.request_id:X}，响应={response}：{exc}",
                    severity=ValidationSeverity.WARNING if skipped else ValidationSeverity.ERROR,
                    file_path=self.workbook.path, location=route.source,
                    actual_value={"tester": f"0x{route.tester:04X}", "logicalAddress": f"0x{route.logical_address:04X}",
                                  "CANBus": route.can_bus, "RequestCanId": f"0x{route.request_id:X}",
                                  "RespCanId": None if route.response_id is None else f"0x{route.response_id:X}",
                                  "missing_reference" if missing else "conflicting_reference": str(exc)},
                ))
        missing = sum(i.code in {"DOIP_PREREQUISITE_MISSING", "DOIP_PREREQUISITE_SKIPPED"} for i in issues)
        if any(i.severity is ValidationSeverity.ERROR for i in issues):
            if missing and self.missing_policy is None:
                issues.append(ValidationIssue("DOIP_PREREQUISITE_CHOICE_REQUIRED",
                    "存在 DoIP 前置缺失项。请选择：A 阻断本次全部生成（BLOCK）；B 仅跳过缺失映射（SKIP）；C 指定其他处理。未选择前不生成文件。"))
            return MutationPlan(issues=tuple(issues))
        added = existing = 0
        for routes in groups.values():
            try:
                group_added, group_existing = self.group(routes, prerequisites)
                added += group_added
                existing += group_existing
            except DiagnosticConflict as exc:
                issues.append(ValidationIssue("DOIP_CONFIGURATION_CONFLICT", str(exc),
                                              file_path=self.workbook.path, location=routes[0].source))
        for path in {op.parent_path for op in self.operations if op.definition_ref == d.PDUR_DEST}:
            linked = self.queue_groups.connected_queues(path, operations=self.operations)
            queues = {value for entries in linked.values() for value in entries}
            if len(queues) != 1 or len(next(iter(queues), ())) != 1:
                issues.append(ValidationIssue("DOIP_QUEUE_CONFLICT",
                    f"DoIP 新目标使关联路径 {sorted(linked)} 的 Queue 缺失或冲突，禁止修改既有 Queue。"))
        return MutationPlan(operations=tuple(self.operations), issues=tuple(issues),
                            doip_added_count=added, doip_existing_count=existing, doip_skipped_count=missing)

    @staticmethod
    def apply(context, operation):
        """只向现有 RoutingActivation 追加目标引用，保留全部回调和参数。"""
        if operation.action is MutationAction.CREATE:
            context.apply_container(operation)
            return
        owner = context.node_at(operation.parent_path)
        target = operation.reference(TARGET_REF)
        context.node_at(target)
        if target in semantic_values(owner, context.namespace)[1].get(TARGET_REF, ()):
            return
        references = owner.find(qualified(context.namespace, "REFERENCE-VALUES"))
        candidates = [] if references is None else [n for n in references if definition_ref(n, context.namespace) == TARGET_REF]
        if not candidates:
            raise DiagnosticConflict(f"RoutingActivation {operation.parent_path} 缺少 TargetAddressRef XML 样例。")
        entry = deepcopy(candidates[0])
        entry.find(qualified(context.namespace, "VALUE-REF")).text = target
        references.append(entry)
        context.inserted.append(entry)
