"""CAN 诊断引用链规划；仅从完整引用定位端点，不使用 CanTp 名称判定通道。"""

from dataclasses import dataclass

from davinci_gw.arxml.index import autosar_path
from davinci_gw.domain.errors import ArxmlStructureError
from davinci_gw.domain.models import (
    MutationAction, MutationKind, MutationOperation, MutationPlan, OperationType, RetentionDecision,
    ValidationIssue, ValidationSeverity,
)
from davinci_gw.input.diagnostic_reader import channel_properties
from davinci_gw.modules import definitions as d
from davinci_gw.modules.common import HandleAllocator, definition_ref, semantic_values, unique_named_node
from .naming import safe_name


class DiagnosticConflict(ArxmlStructureError):
    """输入或基线不足以唯一确定诊断配置。"""


class DiagnosticSkipped(DiagnosticConflict):
    """通道导入引用缺失，与既有 DBC 缺失约定一致地跳过整行。"""


@dataclass(frozen=True)
class CanDiagnosticChain:
    channel_path: str
    rx_sdu: str
    tx_sdu: str
    object_paths: tuple[str, ...]


class DiagnosticRoutePlanner:
    """复用协调器编辑器及 MutationOperation，规划原子请求/响应配置。"""

    def __init__(self, workbook, ecuc, canif, pdur, groups):
        self.workbook, self.ecuc, self.canif, self.pdur, self.groups = workbook, ecuc, canif, pdur, groups
        self.index = canif.index
        self.ns = canif.document.namespace
        self.references = {entry.channel_name: entry for entry in workbook.reference_data}
        self.operations = []
        self.handles = {}
        self.endpoint_cache = {}
        self.planned_sources = {}
        self.planned_destinations = {}

    def path(self, node):
        return autosar_path(node, self.ns)

    def values(self, node):
        return semantic_values(node, self.ns)

    def node(self, path, definition=None):
        nodes = self.index.find_by_path(path)
        if len(nodes) != 1 or (definition and definition_ref(nodes[0], self.ns) != definition):
            raise DiagnosticConflict(f"引用目标无法唯一解析为 {definition}：{path}。")
        return nodes[0]

    def one(self, values, detail):
        unique = set(values)
        if len(unique) != 1:
            raise DiagnosticConflict(f"{detail}无法唯一确定，候选={sorted(unique)}。请明确输入或修复基线。")
        return next(iter(unique))

    def ref(self, node, definition):
        return self.one(self.values(node)[1].get(definition, ()), f"{self.path(node)} 的 {definition}")

    def children(self, node, definition):
        return tuple(n for n in node.iter() if n is not node and definition_ref(n, self.ns) == definition)

    def hardware(self, channel, rx):
        entry = self.references.get(channel)
        if entry is None:
            raise DiagnosticConflict(f"引用数据缺少 CAN 通道 {channel}。")
        name = entry.hrh_name if rx else entry.tx_buffer_name
        definition = d.CANIF_HRH if rx else d.CANIF_BUFFER
        state, node = unique_named_node(self.index, self.ns, name or "", definition)
        if not name or state == "MISSING":
            raise DiagnosticSkipped(f"通道 {channel} 的硬件引用 {name!r} 缺失，定义={definition}；"
                                    "可能尚未导入对应 DBC 或引用数据不完整，已跳过整条诊断需求。")
        if state != "FOUND":
            raise DiagnosticConflict(f"通道 {channel} 的硬件引用 {name} 无法唯一定位，定义={definition}。")
        return self.path(node)

    def frames(self, channel, can_id, rx):
        definition = d.CANIF_RX if rx else d.CANIF_TX
        id_def = d.CANIF_RX_CAN_ID if rx else d.CANIF_TX_CAN_ID
        hw_def = d.CANIF_RX_HRH_REF if rx else d.CANIF_TX_BUFFER_REF
        hardware = self.hardware(channel, rx)
        return tuple(n for n in self.index.find_by_definition_ref(definition)
                     if self.values(n)[0].get(id_def) == (str(can_id),)
                     and self.values(n)[1].get(hw_def) == (hardware,))

    def check_frame(self, node, endpoint, rx):
        # 类型和 DLC 的通道默认值仅约束新建对象，复用时保留真实基线。
        expected = {
            (d.CANIF_RX_INDICATION_UL if rx else d.CANIF_TX_CONFIRM_UL): "CAN_TP",
            (d.CANIF_RX_INDICATION_NAME if rx else d.CANIF_TX_CONFIRM_NAME): "CanTp_RxIndication" if rx else "CanTp_TxConfirmation",
            (d.CANIF_RX_DLC_CHECK if rx else d.CANIF_TX_TRUNCATION): "false" if rx else "true",
        }
        for key, value in expected.items():
            if self.values(node)[0].get(key) != (value,):
                raise DiagnosticConflict(f"已有 CanIf 属性冲突，保留基线：{self.path(node)} 的 {key} 应为 {value}。")

    def locate(self, endpoint, request_side):
        if endpoint.response_id is None:
            return self.locate_functional(endpoint, request_side)
        rx_id, tx_id = ((endpoint.request_id, endpoint.response_id) if request_side else
                        (endpoint.response_id, endpoint.request_id))
        rx_frames, tx_frames = self.frames(endpoint.channel, rx_id, True), self.frames(endpoint.channel, tx_id, False)
        if not rx_frames and not tx_frames:
            return None
        if len(rx_frames) != 1 or len(tx_frames) != 1:
            raise DiagnosticConflict(f"{endpoint.channel} 0x{rx_id:X}/0x{tx_id:X} 的 CanIf 端点不完整或歧义："
                                     f"{[self.path(n) for n in rx_frames + tx_frames]}。")
        rx, tx = rx_frames[0], tx_frames[0]
        self.check_frame(rx, endpoint, True)
        self.check_frame(tx, endpoint, False)
        lower_rx, lower_tx = self.ref(rx, d.CANIF_RX_PDU_REF), self.ref(tx, d.CANIF_TX_PDU_REF)
        self.node(lower_rx, d.ECUC_PDU)
        self.node(lower_tx, d.ECUC_PDU)
        candidates = []
        for channel in self.index.find_by_definition_ref(d.CANTP_CHANNEL):
            rxs, txs = self.children(channel, d.CANTP_RX), self.children(channel, d.CANTP_TX)
            if len(rxs) != 1 or len(txs) != 1:
                continue
            checks = ((rxs[0], d.CANTP_RX_NPDU, "CanTpRxNPduRef", lower_rx),
                      (rxs[0], d.CANTP_TX_FC, "CanTpTxFcNPduRef", lower_tx),
                      (txs[0], d.CANTP_RX_FC, "CanTpRxFcNPduRef", lower_rx),
                      (txs[0], d.CANTP_TX_NPDU, "CanTpTxNPduRef", lower_tx))
            if all(len(self.children(owner, definition)) == 1 and
                   self.values(self.children(owner, definition)[0])[1].get(f"{definition}/{field}") == (target,)
                   for owner, definition, field, target in checks):
                candidates.append((channel, rxs[0], txs[0]))
        if len(candidates) != 1:
            raise DiagnosticConflict(f"底层 PDU {lower_rx} / {lower_tx} 无法定位唯一完整 CanTp Channel；"
                                     f"候选={[self.path(c[0]) for c in candidates]}。")
        channel, rx_sdu, tx_sdu = candidates[0]
        upper_rx, upper_tx = self.ref(rx_sdu, d.CANTP_RX_SDU_REF), self.ref(tx_sdu, d.CANTP_TX_SDU_REF)
        self.node(upper_rx, d.ECUC_PDU)
        self.node(upper_tx, d.ECUC_PDU)
        if len({lower_rx, lower_tx, upper_rx, upper_tx}) != 4:
            raise DiagnosticConflict(f"两层 EcuC 或方向被合并：{self.path(channel)}。")
        if endpoint.transport_parameters:
            for node, overrides in ((rx_sdu, self.transport_values(endpoint, True)),
                                    (tx_sdu, self.transport_values(endpoint, False))):
                actual = self.values(node)[0]
                for key, value in overrides.items():
                    from decimal import Decimal
                    values = actual.get(key, ())
                    if len(values) != 1 or Decimal(values[0]) != Decimal(value):
                        raise DiagnosticConflict(f"输入传输参数与基线冲突，禁止覆盖：{self.path(node)} 的 {key}，"
                                                 f"基线={values}，输入={value} 秒（BlockSize 为整数）。")
        return CanDiagnosticChain(self.path(channel), upper_rx, upper_tx,
                                  (self.path(rx_sdu), self.path(tx_sdu), lower_rx, lower_tx, upper_rx, upper_tx,
                                   self.path(rx), self.path(tx)))

    def locate_functional(self, endpoint, rx):
        frames = self.frames(endpoint.channel, endpoint.request_id, rx)
        if not frames:
            return None
        rx_type, tx_type, length, ta = channel_properties(endpoint.channel)
        id_type = d.CANIF_RX_CAN_ID_TYPE if rx else d.CANIF_TX_CAN_ID_TYPE
        frames = tuple(n for n in frames if self.values(n)[0].get(id_type) == (rx_type if rx else tx_type,))
        if not frames:
            return None
        if len(frames) != 1:
            raise DiagnosticConflict(f"功能寻址 0x{endpoint.request_id:X}/{endpoint.channel} 无唯一匹配帧类型的 CanIf："
                                     f"{[self.path(n) for n in frames]}。")
        frame = frames[0]
        self.check_frame(frame, endpoint, rx)
        lower = self.ref(frame, d.CANIF_RX_PDU_REF if rx else d.CANIF_TX_PDU_REF)
        definition, npdu = (d.CANTP_RX, d.CANTP_RX_NPDU) if rx else (d.CANTP_TX, d.CANTP_TX_NPDU)
        reference = npdu + ("/CanTpRxNPduRef" if rx else "/CanTpTxNPduRef")
        candidates = [n for n in self.index.find_by_definition_ref(definition)
                      if any(self.values(c)[1].get(reference) == (lower,) for c in self.children(n, npdu))]
        if len(candidates) != 1:
            raise DiagnosticConflict(f"功能寻址底层 PDU {lower} 没有唯一 CanTp N-SDU。")
        sdu = candidates[0]
        if (len(self.children(sdu, npdu)) != 1
                or self.children(sdu, d.CANTP_TX_FC if rx else d.CANTP_RX_FC)):
            raise DiagnosticConflict(f"功能寻址 N-SDU 包含多余 N-PDU 或 FC 配置：{self.path(sdu)}。")
        prefix = "CanTpRx" if rx else "CanTpTx"
        actual = self.values(sdu)[0]
        expected_ta = ta.replace("PHYSICAL", "FUNCTIONAL")
        if actual.get(definition + "/" + prefix + "TaType") != (expected_ta,):
            raise DiagnosticConflict(f"{self.path(sdu)} 不是输入通道对应的功能寻址类型 {expected_ta}。")
        from decimal import Decimal
        for key, value in self.transport_values(endpoint, rx).items():
            values = actual.get(key, ())
            if len(values) != 1 or Decimal(values[0]) != Decimal(value):
                raise DiagnosticConflict(f"功能寻址参数与基线冲突：{self.path(sdu)}，{key}={values}，输入={value}。")
        upper = self.ref(sdu, d.CANTP_RX_SDU_REF if rx else d.CANTP_TX_SDU_REF)
        self.node(lower, d.ECUC_PDU)
        self.node(upper, d.ECUC_PDU)
        if upper == lower:
            raise DiagnosticConflict(f"功能寻址两层 EcuC 被合并：{upper}。")
        channel = sdu.getparent().getparent()
        # 共用 Channel 时删除仅针对该 N-SDU，不删除其他报文的同级配置。
        return CanDiagnosticChain(self.path(channel), upper if rx else "", "" if rx else upper,
                                  (self.path(sdu), lower, upper, self.path(frame)))

    @staticmethod
    def transport_values(endpoint, rx):
        values = dict(endpoint.transport_parameters)
        definition = d.CANTP_RX if rx else d.CANTP_TX
        fields = (("N_Ar", "CanTpNar"), ("N_Br", "CanTpNbr"), ("N_Cr", "CanTpNcr"),
                  ("BlockSize", "CanTpBs"), ("STmin", "CanTpSTmin")) if rx else (
                  ("N_As", "CanTpNas"), ("N_Bs", "CanTpNbs"), ("N_Cs", "CanTpNcs"))
        return {f"{definition}/{field}": values[key] for key, field in fields if key in values}

    def allocate(self, definition):
        if definition not in self.handles:
            self.handles[definition] = HandleAllocator(self.index, self.ns, definition)
        return str(self.handles[definition].allocate())

    def create(self, kind, parent, name, definition, parameters, references, route):
        operation = MutationOperation(kind, parent, name, definition, parameters=tuple(parameters.items()),
                                      references=tuple(references.items()) if isinstance(references, dict) else tuple(references),
                                      source_locations=(route.source,))
        if self.index.find_by_path(operation.object_path):
            raise DiagnosticConflict(f"新建名称已占用，但语义链不完整：{operation.object_path}。")
        self.operations.append(operation)
        return operation.object_path

    def transport_templates(self, definition, ta):
        prefix = "CanTpRx" if definition == d.CANTP_RX else "CanTpTx"
        key = f"{definition}/{prefix}TaType"
        candidates = tuple(n for n in self.index.find_by_definition_ref(definition)
                           if any(v.endswith(ta.rsplit("_", 1)[-1]) for v in self.values(n)[0].get(key, ())))
        exact = tuple(n for n in candidates if self.values(n)[0].get(key) == (ta,))
        return exact or candidates

    def template_parameters(self, definition, overrides, handle_fields=(), candidates=None):
        """仅复用同类基线中唯一的非输入参数组合，绝不选首个候选兜底。"""
        signatures = set()
        for node in self.index.find_by_definition_ref(definition) if candidates is None else candidates:
            parameters, _ = self.values(node)
            if any(len(v) != 1 for v in parameters.values()):
                continue
            signatures.add(tuple(sorted((k, v[0]) for k, v in parameters.items()
                                        if k not in overrides and k not in handle_fields)))
        signature = self.one(signatures, f"同类基线 {definition} 的参数")
        return dict(signature) | overrides | {key: self.allocate(key) for key in handle_fields}

    def endpoint(self, route, endpoint, request_side):
        identity = (endpoint.channel, endpoint.request_id, endpoint.response_id, request_side)
        if identity in self.endpoint_cache:
            previous, chain = self.endpoint_cache[identity]
            if previous.transport_parameters != endpoint.transport_parameters:
                raise DiagnosticConflict(f"同一端点 {identity} 的输入传输参数冲突。")
            return chain
        existing = self.locate(endpoint, request_side)
        if existing:
            self.endpoint_cache[identity] = (endpoint, existing)
            return existing
        rx_type, tx_type, length, ta = channel_properties(endpoint.channel)
        functional = endpoint.response_id is None
        if functional:
            ta = ta.replace("PHYSICAL", "FUNCTIONAL")
        rx_id, tx_id = ((endpoint.request_id, endpoint.response_id) if request_side else
                        (endpoint.response_id, endpoint.request_id))
        rx_name, tx_name = ((route.request_name, route.response_name) if request_side else
                            (route.response_name, route.request_name))
        def name(module, logical, can_id, direction):
            return f"GWT_Diag_{module}_{safe_name(logical)}_{can_id:X}_{safe_name(endpoint.channel)}_{direction}"
        lower = {}
        upper = {}
        objects = []
        # 上层 N-SDU 长度独立于 CAN 帧 DLC，必须由基线 CanTp 的真实上层引用推导。
        templates = {}
        for rx, logical, can_id, direction, frame_type in (
            (True, rx_name, rx_id, "Rx", rx_type), (False, tx_name, tx_id, "Tx", tx_type),
        ):
            if functional and rx != request_side:
                continue
            if functional:
                logical, can_id = route.request_name, endpoint.request_id
            sdu_definition = d.CANTP_RX if rx else d.CANTP_TX
            sdu_ref = d.CANTP_RX_SDU_REF if rx else d.CANTP_TX_SDU_REF
            templates[rx] = self.transport_templates(sdu_definition, ta)
            sdu_lengths = [value for node in templates[rx]
                           for value in self.values(self.node(self.ref(node, sdu_ref), d.ECUC_PDU))[0].get(d.ECUC_PDU_LENGTH, ())]
            sdu_length = self.one(sdu_lengths, f"{ta} {'Rx' if rx else 'Tx'} 上层 EcuC PduLength")
            for target, module, size in ((lower, "EcuC", length), (upper, "Tp_EcuC", sdu_length)):
                target[rx] = self.create(MutationKind.ECUC_PDU, self.ecuc.parent_path,
                    name(module, logical, can_id, direction), d.ECUC_PDU,
                    {d.ECUC_PDU_LENGTH: str(size), d.ECUC_PDU_J1939: "false"}, {}, route)
                objects.append(target[rx])
            definition = d.CANIF_RX if rx else d.CANIF_TX
            parameters = {
                (d.CANIF_RX_CAN_ID if rx else d.CANIF_TX_CAN_ID): str(can_id),
                (d.CANIF_RX_CAN_ID_TYPE if rx else d.CANIF_TX_CAN_ID_TYPE): frame_type,
                (d.CANIF_RX_DLC if rx else d.CANIF_TX_DLC): str(length),
                (d.CANIF_RX_INDICATION_NAME if rx else d.CANIF_TX_CONFIRM_NAME): "CanTp_RxIndication" if rx else "CanTp_TxConfirmation",
                (d.CANIF_RX_INDICATION_UL if rx else d.CANIF_TX_CONFIRM_UL): "CAN_TP",
                (d.CANIF_RX_DLC_CHECK if rx else d.CANIF_TX_TRUNCATION): "false" if rx else "true",
                (d.CANIF_RX_HANDLE if rx else d.CANIF_TX_HANDLE): str((self.canif.rx_handles if rx else self.canif.tx_handles).allocate()),
                (d.CANIF_RX_TYPE if rx else d.CANIF_TX_TYPE): "STATIC",
                (d.CANIF_RX_READ_NOTIFY if rx else d.CANIF_TX_READ_NOTIFY): "false",
            }
            if rx:
                parameters[d.CANIF_RX_READ_DATA] = "false"
            objects.append(self.create(MutationKind.CANIF_RX_PDU if rx else MutationKind.CANIF_TX_PDU,
                self.canif.rx_parent_path if rx else self.canif.tx_parent_path,
                name("CanIf", logical, can_id, direction), definition, parameters,
                {(d.CANIF_RX_PDU_REF if rx else d.CANIF_TX_PDU_REF): lower[rx],
                 (d.CANIF_RX_HRH_REF if rx else d.CANIF_TX_BUFFER_REF): self.hardware(endpoint.channel, rx)}, route))
        parents = [self.path(node.getparent().getparent()) for node in self.index.find_by_definition_ref(d.CANTP_CHANNEL)]
        channel_path = self.create(MutationKind.CANTP_CONTAINER, self.one(parents, "CanTp Channel 父路径"),
            f"GWT_CanTpChannelGW_{safe_name(endpoint.channel)}{endpoint.request_id:X}"
            f"{'' if functional else '_' + format(endpoint.response_id, 'X')}",
            d.CANTP_CHANNEL, self.template_parameters(d.CANTP_CHANNEL, {}), {}, route)
        objects.append(channel_path)
        # 数据与流控共用同一个底层 N-PDU，按收发方向的联合 ID 空间分配。
        npdu_handles = {}
        for receive, definitions in (
            (True, (d.CANTP_RX_NPDU + "/CanTpRxNPduId", d.CANTP_RX_FC + "/CanTpRxFcNPduId")),
            (False, (d.CANTP_TX_NPDU + "/CanTpTxNPduConfirmationPduId", d.CANTP_TX_FC + "/CanTpTxFcNPduConfirmationPduId")),
        ):
            if receive not in lower:
                continue
            key = ("npdu", receive)
            if key not in self.handles:
                allocator = HandleAllocator(self.index, self.ns, definitions[0])
                allocator.used.update(HandleAllocator(self.index, self.ns, definitions[1]).used)
                self.handles[key] = allocator
            npdu_handles[receive] = str(self.handles[key].allocate())
        for rx in (True, False):
            if functional and rx != request_side:
                continue
            definition = d.CANTP_RX if rx else d.CANTP_TX
            prefix = "CanTpRx" if rx else "CanTpTx"
            parameters = self.transport_values(endpoint, rx) | {f"{definition}/{prefix}Dl": str(length),
                                                              f"{definition}/{prefix}TaType": ta}
            sdu_path = self.create(MutationKind.CANTP_CONTAINER, channel_path, prefix + "NSdu", definition,
                self.template_parameters(definition, parameters, (f"{definition}/{prefix}NSduId",), templates[rx]),
                {(d.CANTP_RX_SDU_REF if rx else d.CANTP_TX_SDU_REF): upper[rx]}, route)
            children = ((d.CANTP_RX_NPDU, "CanTpRxNPduRef", "CanTpRxNPduId", True),
                        (d.CANTP_TX_FC, "CanTpTxFcNPduRef", "CanTpTxFcNPduConfirmationPduId", False)) if rx else (
                        (d.CANTP_RX_FC, "CanTpRxFcNPduRef", "CanTpRxFcNPduId", True),
                        (d.CANTP_TX_NPDU, "CanTpTxNPduRef", "CanTpTxNPduConfirmationPduId", False))
            for child_def, ref, handle, receive in children:
                if functional and receive != rx:
                    continue
                self.create(MutationKind.CANTP_CONTAINER, sdu_path, child_def.rsplit("/", 1)[-1], child_def,
                    self.template_parameters(child_def, {f"{child_def}/{handle}": npdu_handles[receive]}),
                    {f"{child_def}/{ref}": lower[receive]}, route)
        chain = CanDiagnosticChain(channel_path, upper.get(True, ""), upper.get(False, ""), tuple(objects))
        self.endpoint_cache[identity] = (endpoint, chain)
        return chain

    def issue(self, route, exc):
        endpoint = route.response_endpoint
        return ValidationIssue(code="DIAGNOSTIC_CONFIGURATION_CONFLICT", message=(
            f"{route.source.sheet_name} 第 {route.source.row_number} 行：{route.request_name}/"
            f"{route.response_name}，0x{endpoint.request_id:X}/{format(endpoint.response_id, 'X') if endpoint.response_id is not None else '功能寻址'}，"
            f"{route.request_endpoint.channel if route.request_endpoint else 'CAN侧独立配置'} → {endpoint.channel}：{exc}"),
            file_path=self.workbook.path, location=route.source)

    def plan(self):
        issues = []
        added = existing = skipped = 0
        for route in self.workbook.diagnostic_routes:
            if route.operation is not OperationType.ADD:
                continue
            before = len(self.operations)
            saved_endpoints = self.endpoint_cache.copy()
            saved_sources = self.planned_sources.copy()
            saved_destinations = self.planned_destinations.copy()
            try:
                for endpoint, request_side in ((route.request_endpoint, True), (route.response_endpoint, False)):
                    if endpoint:
                        for rx in (True, False):
                            if endpoint.response_id is not None or rx == request_side:
                                self.hardware(endpoint.channel, rx)
                response = self.endpoint(route, route.response_endpoint, False)
                if route.request_endpoint:
                    request = self.endpoint(route, route.request_endpoint, True)
                    self.route_pair(route, request, response)
                if len(self.operations) == before:
                    existing += 1
                else:
                    added += 1
            except DiagnosticSkipped as exc:
                from dataclasses import replace
                del self.operations[before:]
                self.endpoint_cache = saved_endpoints
                self.planned_sources = saved_sources
                self.planned_destinations = saved_destinations
                issues.append(replace(self.issue(route, exc), code="DIAGNOSTIC_CHANNEL_REFERENCE_SKIPPED",
                                      severity=ValidationSeverity.WARNING))
                skipped += 1
            except (DiagnosticConflict, ValueError) as exc:
                del self.operations[before:]
                self.endpoint_cache = saved_endpoints
                self.planned_sources = saved_sources
                self.planned_destinations = saved_destinations
                issues.append(self.issue(route, exc))
        return MutationPlan(operations=tuple(self.operations), issues=tuple(issues),
                            diagnostic_added_count=added, diagnostic_existing_count=existing,
                            diagnostic_skipped_count=skipped)

    def route_pair(self, route, request, response):
        """仅连接两个 CanTp 端点，既有一对多路径只补缺失的目标腿。"""
        legs = (
            (request.rx_sdu, response.tx_sdu, route.request_endpoint.request_id,
             route.request_endpoint.channel, route.response_endpoint.channel, False),
            (response.rx_sdu, request.tx_sdu, route.response_endpoint.response_id,
             route.response_endpoint.channel, route.request_endpoint.channel, True),
        )
        for source, target, can_id, source_channel, target_channel, is_response in legs:
            if can_id is None:
                continue
            if (source, target) in self.planned_destinations:
                continue
            module = self.cantp_module()
            sources = [node for node in self.index.find_by_definition_ref(d.PDUR_SRC)
                       if self.values(node)[1].get(d.PDUR_SRC_PDU_REF) == (source,)
                       and self.values(node)[1].get(d.PDUR_SRC_MODULE_REF) == (module,)]
            if len(sources) > 1:
                raise DiagnosticConflict(f"源上层 PDU {source} 的 CAN TP 路径歧义：{[self.path(n) for n in sources]}。")
            destinations = []
            if sources:
                path_node = sources[0].getparent().getparent()
                if self.values(path_node)[0].get(d.PDUR_PATH_COMM_TYPE) != ("TRANSPORT_PROTOCOL",):
                    raise DiagnosticConflict(f"{self.path(path_node)} 不是 Transport Protocol 路由。")
                path = self.path(path_node)
                destinations = [node for node in self.children(path_node, d.PDUR_DEST)
                                if self.values(node)[1].get(d.PDUR_DEST_PDU_REF) == (target,)
                                and self.values(node)[1].get(d.PDUR_DEST_MODULE_REF) == (module,)]
            elif source in self.planned_sources:
                path = self.planned_sources[source]
            else:
                path = self.create(MutationKind.PDUR_ROUTING_PATH, self.pdur.path_parent,
                    f"GWT_Diag_PduR_{can_id:X}_{safe_name(source_channel)}_To_{safe_name(target_channel)}",
                    d.PDUR_PATH, {d.PDUR_PATH_COMM_TYPE: "TRANSPORT_PROTOCOL", d.PDUR_PATH_MULTICORE: "false"},
                    {d.PDUR_PATH_LOCK_REF: self.pdur.lock_ref}, route)
                self.create(MutationKind.PDUR_SRC_PDU, path, "Source", d.PDUR_SRC,
                    {d.PDUR_SRC_HANDLE: str(self.pdur.src_handles.allocate()), d.PDUR_SRC_DIRECTION: "RECEIVE"},
                    {d.PDUR_SRC_PDU_REF: source, d.PDUR_SRC_MODULE_REF: module}, route)
                self.planned_sources[source] = path
            if len(destinations) > 1:
                raise DiagnosticConflict(f"目标上层 PDU {target} 在路径 {path} 中重复。")
            if destinations:
                destination = destinations[0]
                queue = self.ref(destination, d.PDUR_QUEUE_REF)
                self.node(queue)
                dest_path = self.path(destination)
            else:
                queue = self.new_queue(route, can_id, source_channel, target_channel)
                dest_path = self.create(MutationKind.PDUR_DEST_PDU, path,
                    f"Diag_TP_{(route.request_endpoint.response_id if is_response else route.response_endpoint.request_id):X}_{safe_name(target_channel)}", d.PDUR_DEST,
                    {d.PDUR_DEST_HANDLE: str(self.pdur.dest_handles.allocate()),
                     d.PDUR_DEST_DIRECTION: "TRANSMIT", d.PDUR_DEST_ROUTING_TYPE: "GATEWAY_ROUTING",
                     d.PDUR_DEST_PROCESSING: "IMMEDIATE", d.PDUR_DEST_LENGTH_STRATEGY: "UNUSED",
                     d.PDUR_DEST_CROSS_PARTITION: "false"},
                    {d.PDUR_DEST_PDU_REF: target, d.PDUR_DEST_MODULE_REF: module, d.PDUR_QUEUE_REF: queue}, route)
            if is_response:
                self.response_group(route, dest_path, target_channel)
            self.planned_destinations[(source, target)] = dest_path

    def new_queue(self, route, can_id, source_channel, target_channel):
        """每个新增目标腿独占一个 Queue；共享缓冲池引用只取基线唯一语义。"""
        parents = [self.path(n.getparent().getparent()) for n in self.index.find_by_definition_ref(d.PDUR_QUEUE)]
        name = f"GWT_Diag_PduRQueue_{can_id:X}_{safe_name(source_channel)}_To_{safe_name(target_channel)}"
        queue = self.create(MutationKind.PDUR_QUEUE, self.one(parents, "PduRQueue 父路径"), name,
                            d.PDUR_QUEUE, {}, {}, route)
        signatures = set()
        for node in self.index.find_by_definition_ref(d.PDUR_SHARED_QUEUE):
            refs = self.values(node)[1]
            signatures.add(tuple(sorted((key, value) for key, values in refs.items() for value in values)))
        references = self.one(signatures, "PduRSharedBufferQueue 的缓冲池引用")
        functional = (route.response_endpoint.response_id is None or can_id == 0x7DF
                      or can_id in self.workbook.diagnostic_functional_ids)
        overrides = {d.PDUR_SHARED_QUEUE + "/PduRQueueDepth": "5" if functional else "3",
                     d.PDUR_SHARED_QUEUE + "/PduRTpThreshold": "0"}
        self.create(MutationKind.PDUR_QUEUE, queue, "PduRSharedBufferQueue", d.PDUR_SHARED_QUEUE,
                    self.template_parameters(d.PDUR_SHARED_QUEUE, overrides), references, route)
        return queue

    def cantp_module(self):
        modules = []
        for node in self.index.find_by_definition_ref(d.PDUR_BSW_MODULE):
            _, refs = self.values(node)
            for target in refs.get(d.PDUR_BSW_MODULE_REF, ()):
                referenced = self.index.find_by_path(target)
                if len(referenced) == 1 and definition_ref(referenced[0], self.ns) == "/MICROSAR/CanTp":
                    modules.append(self.path(node))
        return self.one(modules, "PduR 的 CanTp BSW 模块引用")

    def destination_channel(self, destination):
        """只沿 CanTp 上层引用反查底层 Tx Buffer，不读路径名称。"""
        upper = self.ref(destination, d.PDUR_DEST_PDU_REF)
        lower = set()
        for sdu in self.index.find_by_definition_ref(d.CANTP_TX):
            if self.values(sdu)[1].get(d.CANTP_TX_SDU_REF) == (upper,):
                for npdu in self.children(sdu, d.CANTP_TX_NPDU):
                    lower.update(self.values(npdu)[1].get(f"{d.CANTP_TX_NPDU}/CanTpTxNPduRef", ()))
        buffers = set()
        for frame in self.index.find_by_definition_ref(d.CANIF_TX):
            refs = self.values(frame)[1]
            if set(refs.get(d.CANIF_TX_PDU_REF, ())) & lower:
                buffers.update(refs.get(d.CANIF_TX_BUFFER_REF, ()))
        channels = set()
        for channel, entry in self.references.items():
            state, node = unique_named_node(self.index, self.ns, entry.tx_buffer_name or "", d.CANIF_BUFFER)
            if state == "FOUND" and self.path(node) in buffers:
                channels.add(channel)
        return channels

    def response_group(self, route, destination_path, channel):
        current = self.groups.groups_for_destination(destination_path)
        if len(current) == 1:
            return
        if current:
            raise DiagnosticConflict(f"响应目标 {destination_path} 属于多个组：{current}。")
        module = self.cantp_module()
        candidates = set()
        for destination in self.index.find_by_definition_ref(d.PDUR_DEST):
            if self.values(destination)[1].get(d.PDUR_DEST_MODULE_REF) != (module,):
                continue
            if self.destination_channel(destination) == {channel}:
                candidates.update(path for _, path in self.groups.group_memberships_for_destination(self.path(destination)))
        group = self.one(candidates, f"响应通道 {channel} 的实际诊断 RoutingPathGroup")
        self.operations.append(MutationOperation(MutationKind.PDUR_ROUTING_GROUP_MEMBERSHIP,
            group, "", d.PDUR_ROUTING_GROUP_DEST_REF, action=MutationAction.ADD_REFERENCE,
            references=((d.PDUR_ROUTING_GROUP_DEST_REF, destination_path),), source_locations=(route.source,)))

    def plan_delete(self):
        """先移除明确的 CAN 目标腿，再按剩余反向引用保留共享端点整组。"""
        operations = {}
        issues, decisions, endpoints = [], [], []
        queues = {}
        deleted = missing = 0
        def remove(path, route):
            node = self.node(path)
            definition = definition_ref(node, self.ns)
            kind = {d.PDUR_DEST: MutationKind.PDUR_DEST_PDU, d.PDUR_SRC: MutationKind.PDUR_SRC_PDU,
                    d.PDUR_PATH: MutationKind.PDUR_ROUTING_PATH, d.ECUC_PDU: MutationKind.ECUC_PDU,
                    d.CANIF_RX: MutationKind.CANIF_RX_PDU, d.CANIF_TX: MutationKind.CANIF_TX_PDU,
                    d.PDUR_QUEUE: MutationKind.PDUR_QUEUE}.get(
                    definition, MutationKind.CANTP_CONTAINER)
            parent, _, name = path.rpartition("/")
            operations[path] = MutationOperation(kind, parent, name, definition,
                action=MutationAction.REMOVE, source_locations=(route.source,))
        for route in self.workbook.diagnostic_routes:
            if route.operation is not OperationType.DELETE:
                continue
            try:
                response = self.locate(route.response_endpoint, False)
                request = self.locate(route.request_endpoint, True) if route.request_endpoint else None
                if not response and not request:
                    missing += 1
                    continue
                if route.request_endpoint and (request is None or response is None):
                    raise DiagnosticConflict("双 CAN DELETE 的引用链仅存在一端，无法确认目标腿，禁止部分删除。")
                before = len(operations)
                if request:
                    module = self.cantp_module()
                    for source, target in ((request.rx_sdu, response.tx_sdu), (response.rx_sdu, request.tx_sdu)):
                        if not source or not target:
                            continue
                        for src in self.index.find_by_definition_ref(d.PDUR_SRC):
                            refs = self.values(src)[1]
                            if refs.get(d.PDUR_SRC_PDU_REF) != (source,) or refs.get(d.PDUR_SRC_MODULE_REF) != (module,):
                                continue
                            path = src.getparent().getparent()
                            destinations = self.children(path, d.PDUR_DEST)
                            matches = [n for n in destinations if self.values(n)[1].get(d.PDUR_DEST_PDU_REF) == (target,)
                                       and self.values(n)[1].get(d.PDUR_DEST_MODULE_REF) == (module,)]
                            if len(matches) > 1:
                                raise DiagnosticConflict(f"{self.path(path)} 中目标 {target} 重复，禁止歧义删除。")
                            for destination in matches:
                                dest_path = self.path(destination)
                                for queue in self.values(destination)[1].get(d.PDUR_QUEUE_REF, ()):
                                    queues[queue] = route
                                for _, group in self.groups.group_memberships_for_destination(dest_path):
                                    operation = MutationOperation(MutationKind.PDUR_ROUTING_GROUP_MEMBERSHIP,
                                        group, "", d.PDUR_ROUTING_GROUP_DEST_REF, action=MutationAction.REMOVE_REFERENCE,
                                        references=((d.PDUR_ROUTING_GROUP_DEST_REF, dest_path),), source_locations=(route.source,))
                                    operations[(group, dest_path)] = operation
                                remove(dest_path, route)
                            if matches and all(self.path(n) in operations for n in destinations):
                                remove(self.path(src), route)
                                remove(self.path(path), route)
                    if len(operations) == before:
                        missing += 1
                        continue
                    else:
                        deleted += 1
                else:
                    deleted += 1
                endpoints.extend((chain, route) for chain in (request, response) if chain is not None)
            except (DiagnosticConflict, ValueError) as exc:
                issues.append(self.issue(route, exc))
        removed = {op.object_path for op in operations.values() if op.action is MutationAction.REMOVE}
        ignored_members = {(op.parent_path, value) for op in operations.values()
                           if op.action is MutationAction.REMOVE_REFERENCE for _, value in op.references}
        # 先清理已无目标腿使用的 Queue；共享缓冲池本身始终保留。
        for queue, route in queues.items():
            node = self.node(queue, d.PDUR_QUEUE)
            targets = {self.path(n) for n in node.iter() if n.find(f"{{{self.ns}}}SHORT-NAME") is not None}
            if all(any(self.path(ref) == p or self.path(ref).startswith(p + "/")
                       for p in removed | {queue})
                   for target in targets for ref in self.index.find_referrers(target)):
                remove(queue, route)
                removed.add(queue)
        for chain, route in endpoints:
            paths = set(chain.object_paths)
            external = []
            # 只检查反向引用所属路径是否仍存活，不选择、解析或校验任何 DoIP 对象。
            targets = {self.path(n) for path in paths for n in self.node(path).iter()
                       if n.find(f"{{{self.ns}}}SHORT-NAME") is not None}
            for path in targets:
                for referrer in self.index.find_referrers(path):
                    owner = self.path(referrer)
                    if any(owner == p or owner.startswith(p + "/") for p in paths | removed):
                        continue
                    if (owner, path) not in ignored_members:
                        external.append(owner)
            if external:
                decisions.append(RetentionDecision(chain.channel_path,
                    f"仍有未删除引用 {sorted(set(external))}，保留 CAN 诊断端点整组。", "DIAGNOSTIC_SHARED", (route.source,)))
                continue
            for path in paths:
                remove(path, route)
                removed.add(path)
        # N-SDU 清理后仅删除已经空置且没有外部引用的 Channel 外壳。
        for chain, route in endpoints:
            if chain.channel_path in removed:
                continue
            channel = self.node(chain.channel_path, d.CANTP_CHANNEL)
            children = self.children(channel, d.CANTP_RX) + self.children(channel, d.CANTP_TX)
            if children and all(self.path(child) in removed for child in children):
                if all(any(self.path(ref) == p or self.path(ref).startswith(p + "/") for p in removed)
                       for ref in self.index.find_referrers(chain.channel_path)):
                    remove(chain.channel_path, route)
                    removed.add(chain.channel_path)
        result = sorted(operations.values(), key=lambda op: (
            op.action is not MutationAction.REMOVE_REFERENCE, -op.object_path.count("/"), op.object_path))
        return MutationPlan(operations=tuple(result), issues=tuple(issues), decisions=tuple(decisions),
                            diagnostic_deleted_count=deleted, diagnostic_missing_count=missing)
