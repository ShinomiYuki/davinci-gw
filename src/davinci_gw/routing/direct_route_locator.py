"""按真实引用链定位直接报文路由，不依赖工具生成的对象名称。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re

from lxml import etree

from davinci_gw.arxml.document import ArxmlDocument
from davinci_gw.arxml.index import ArxmlIndex, autosar_path
from davinci_gw.arxml.namespace import direct_child_text, qualified
from davinci_gw.domain.models import DirectRouteChange
from davinci_gw.modules import definitions as defs
from davinci_gw.modules.canif_editor import enabled
from davinci_gw.modules.common import definition_ref, semantic_values

from .routing_group_membership import RoutingGroupMembershipService, RoutingGroupModelError

SUPPORTED_CAN_TYPES = {
    "STANDARD_CAN", "STANDARD_FD_CAN", "EXTENDED_CAN", "EXTENDED_FD_CAN",
}


class DirectSemanticState(str, Enum):
    """语义定位结果；冲突和歧义必须阻断，不能降级为缺失。"""

    MISSING = "MISSING"
    FOUND = "FOUND"
    PARTIAL_CONFLICT = "PARTIAL_CONFLICT"
    AMBIGUOUS = "AMBIGUOUS"


@dataclass(frozen=True, slots=True)
class DirectSourceChain:
    """一条完整的 CanIfRx → EcuC → PduRSrcPdu → RoutingPath 源链。"""

    canif_path: str
    ecuc_path: str
    source_pdu_path: str
    routing_path: str


@dataclass(frozen=True, slots=True)
class DirectTargetEndpoint:
    """一个由目标通道和报文语义唯一确认的 CanIfTx/EcuC 端点。"""

    canif_path: str
    ecuc_path: str
    application_destination_paths: tuple[str, ...]
    referrer_paths: tuple[str, ...]

    @property
    def reusable(self) -> bool:
        """只有已有普通应用 PduR 目标腿提供证据时才允许跨路由复用。"""
        return bool(self.application_destination_paths)


@dataclass(frozen=True, slots=True)
class DirectRouteLeg:
    """源 RoutingPath 下唯一指向目标 EcuC 的 PduRDestPdu。"""

    destination_path: str
    target_canif_path: str
    target_ecuc_path: str
    group_memberships: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class SourceResolution:
    state: DirectSemanticState
    chain: DirectSourceChain | None = None
    candidates: tuple[str, ...] = ()
    detail: str = ""


@dataclass(frozen=True, slots=True)
class TargetResolution:
    state: DirectSemanticState
    endpoint: DirectTargetEndpoint | None = None
    candidates: tuple[str, ...] = ()
    detail: str = ""


@dataclass(frozen=True, slots=True)
class LegResolution:
    state: DirectSemanticState
    leg: DirectRouteLeg | None = None
    candidates: tuple[str, ...] = ()
    detail: str = ""


@dataclass(frozen=True, slots=True)
class DirectRouteSemanticResult:
    source: SourceResolution
    target: TargetResolution
    leg: LegResolution


def message_identity_matches(short_name: str | None, message_name: str) -> bool:
    """按下划线边界匹配完整报文名，避免 ``ABC_1`` 命中 ``ABC_10``。"""
    if not short_name:
        return False
    return re.search(rf"(?:^|_){re.escape(message_name)}(?=_|$)", short_name) is not None


def _container_owner(node: etree._Element) -> etree._Element | None:
    current: etree._Element | None = node
    while current is not None:
        if direct_child_text(current, etree.QName(current).namespace or "", "SHORT-NAME") is not None:
            return current
        current = current.getparent()
    return None


def _subcontainers(
    node: etree._Element, namespace: str, definition: str,
) -> tuple[etree._Element, ...]:
    group = node.find(qualified(namespace, "SUB-CONTAINERS"))
    if group is None:
        return ()
    return tuple(child for child in group if definition_ref(child, namespace) == definition)


def _required_values_match(
    actual: dict[str, tuple[str, ...]], expected: dict[str, str],
) -> tuple[bool, tuple[str, ...]]:
    conflicts = tuple(
        f"{definition}={list(actual.get(definition, ()))}，期望 {value!r}"
        for definition, value in expected.items()
        if actual.get(definition) != (value,)
    )
    return not conflicts, conflicts


class DirectRouteSemanticLocator:
    """集中定位 ADD/DELETE 共用的直接报文身份和引用链。"""

    def __init__(
        self,
        document: ArxmlDocument,
        index: ArxmlIndex,
        routing_groups: RoutingGroupMembershipService,
        *,
        source_module_ref: str,
        destination_module_ref: str,
        lock_ref: str,
    ) -> None:
        self.document = document
        self.index = index
        self.routing_groups = routing_groups
        self.namespace = document.namespace
        self.source_module_ref = source_module_ref
        self.destination_module_ref = destination_module_ref
        self.lock_ref = lock_ref
        self._source_cache: dict[tuple[object, ...], SourceResolution] = {}
        self._target_cache: dict[tuple[object, ...], TargetResolution] = {}

    def _ecuc_matches(self, path: str, length: int | None) -> tuple[bool, str]:
        nodes = self.index.find_by_path(path)
        if len(nodes) != 1 or definition_ref(nodes[0], self.namespace) != defs.ECUC_PDU:
            return False, f"EcuC PDU“{path}”实际候选数为 {len(nodes)}"
        parameters, _ = semantic_values(nodes[0], self.namespace)
        expected = {defs.ECUC_PDU_LENGTH: str(length), defs.ECUC_PDU_J1939: "false"}
        matched, conflicts = _required_values_match(parameters, expected)
        return matched, "；".join(conflicts)

    def _path_matches(self, path: etree._Element) -> tuple[bool, str]:
        parameters, references = semantic_values(path, self.namespace)
        matched_parameters, parameter_conflicts = _required_values_match(parameters, {
            defs.PDUR_PATH_COMM_TYPE: "COMMUNICATION_INTERFACE",
            defs.PDUR_PATH_MULTICORE: "false",
        })
        matched_references, reference_conflicts = _required_values_match(references, {
            defs.PDUR_PATH_LOCK_REF: self.lock_ref,
        })
        conflicts = parameter_conflicts + reference_conflicts
        return matched_parameters and matched_references, "；".join(conflicts)

    def _locate_source(
        self,
        route: DirectRouteChange,
        hrh_path: str,
        *,
        accept_configured_can_type: bool,
    ) -> SourceResolution:
        key = route.key
        related: list[str] = []
        conflicts: list[str] = []
        configured_type_notes: dict[str, str] = {}
        chains: dict[tuple[str, str, str, str], DirectSourceChain] = {}
        expected_parameters = {
            defs.CANIF_RX_CAN_ID: str(key.source_can_id),
            defs.CANIF_RX_DLC: str(route.source_length),
            defs.CANIF_RX_INDICATION_NAME: "PduR_CanIfRxIndication",
            defs.CANIF_RX_INDICATION_UL: route.source_rx_indication_ul or "",
            defs.CANIF_RX_READ_DATA: "false",
            defs.CANIF_RX_READ_NOTIFY: "false",
            defs.CANIF_RX_DLC_CHECK: enabled(route.source_dlc_check_enabled),
            defs.CANIF_RX_TYPE: "STATIC",
        }
        for canif in self.index.find_by_definition_ref(defs.CANIF_RX):
            name = direct_child_text(canif, self.namespace, "SHORT-NAME")
            parameters, references = semantic_values(canif, self.namespace)
            same_hrh = references.get(defs.CANIF_RX_HRH_REF) == (hrh_path,)
            same_id = parameters.get(defs.CANIF_RX_CAN_ID) == (str(key.source_can_id),)
            same_name = message_identity_matches(name, key.source_message_name)
            if not same_hrh or not same_id:
                continue
            canif_path = autosar_path(canif, self.namespace)
            related.append(canif_path)
            actual_types = parameters.get(defs.CANIF_RX_CAN_ID_TYPE, ())
            candidate_expected = dict(expected_parameters)
            # ADD 只新增目标腿时不会改写完整既有 Rx。报文名、CAN ID、HRH 和
            # PDU 引用仍必须唯一一致；仅 CAN 类型允许采用基准现值，避免标准表的
            # 默认 STANDARD_CAN 否定 DBC 已配置的 STANDARD_FD_CAN。
            if not (
                accept_configured_can_type
                and len(actual_types) == 1
                and actual_types[0] in SUPPORTED_CAN_TYPES
            ):
                candidate_expected[defs.CANIF_RX_CAN_ID_TYPE] = (
                    route.source_message_type or ""
                )
            matched, differences = _required_values_match(parameters, candidate_expected)
            pdu_refs = references.get(defs.CANIF_RX_PDU_REF, ())
            if not same_id or not same_name or not matched or len(pdu_refs) != 1:
                conflicts.append(
                    f"{canif_path}：名称匹配={same_name}，CAN ID 匹配={same_id}，"
                    f"PDU 引用数={len(pdu_refs)}，参数冲突={list(differences)}"
                )
                continue
            ecuc_path = pdu_refs[0]
            ecuc_matches, ecuc_detail = self._ecuc_matches(ecuc_path, route.source_length)
            if not ecuc_matches:
                conflicts.append(f"{canif_path}：{ecuc_detail}")
                continue
            found_chain = False
            for referrer in self.index.find_referrers(ecuc_path):
                source = _container_owner(referrer)
                if source is None or definition_ref(source, self.namespace) != defs.PDUR_SRC:
                    continue
                source_parameters, source_references = semantic_values(source, self.namespace)
                source_ok, source_conflicts = _required_values_match(source_parameters, {
                    defs.PDUR_SRC_DIRECTION: "RECEIVE",
                })
                refs_ok, ref_conflicts = _required_values_match(source_references, {
                    defs.PDUR_SRC_PDU_REF: ecuc_path,
                    defs.PDUR_SRC_MODULE_REF: self.source_module_ref,
                })
                subcontainers = source.getparent()
                path = subcontainers.getparent() if subcontainers is not None else None
                if path is None or definition_ref(path, self.namespace) != defs.PDUR_PATH:
                    conflicts.append(f"{autosar_path(source, self.namespace)}：缺少 RoutingPath 父容器")
                    continue
                path_ok, path_detail = self._path_matches(path)
                if not source_ok or not refs_ok or not path_ok:
                    conflicts.append(
                        f"{autosar_path(source, self.namespace)}："
                        f"{'; '.join(source_conflicts + ref_conflicts)}{path_detail}"
                    )
                    continue
                found_chain = True
                chain = DirectSourceChain(
                    canif_path,
                    ecuc_path,
                    autosar_path(source, self.namespace),
                    autosar_path(path, self.namespace),
                )
                chains[(chain.canif_path, chain.ecuc_path, chain.source_pdu_path, chain.routing_path)] = chain
                if (
                    accept_configured_can_type
                    and actual_types != (route.source_message_type or "",)
                ):
                    configured_type_notes[chain.canif_path] = (
                        f"完整既有源 CanIfRxPdu“{chain.canif_path}”的报文类型为"
                        f"“{actual_types[0]}”，配置表填写“{route.source_message_type}”；"
                        "本次 ADD 采用基准 ARXML 现值且不修改源 Rx。"
                    )
            if not found_chain:
                conflicts.append(f"{canif_path}：没有完整的 CanIf PduRSrcPdu 源链")
        if conflicts:
            return SourceResolution(
                DirectSemanticState.PARTIAL_CONFLICT,
                candidates=tuple(sorted(set(related))), detail="；".join(conflicts),
            )
        if not chains:
            return SourceResolution(DirectSemanticState.MISSING)
        if len(chains) != 1:
            return SourceResolution(
                DirectSemanticState.AMBIGUOUS,
                candidates=tuple(sorted(chain.routing_path for chain in chains.values())),
                detail="同一源报文定位到多条完整 PduR 源链",
            )
        chain = next(iter(chains.values()))
        return SourceResolution(
            DirectSemanticState.FOUND,
            chain,
            detail=configured_type_notes.get(chain.canif_path, ""),
        )

    def _ordinary_application_destinations(self, ecuc_path: str) -> tuple[str, ...]:
        application_mapping, _ = self.routing_groups.application_group_mapping()
        application_group_paths = {path for _, path in application_mapping.values()}
        destinations: list[str] = []
        for referrer in self.index.find_referrers(ecuc_path):
            destination = _container_owner(referrer)
            if destination is None or definition_ref(destination, self.namespace) != defs.PDUR_DEST:
                continue
            parameters, references = semantic_values(destination, self.namespace)
            destination_path = autosar_path(destination, self.namespace)
            direct_ok, _ = _required_values_match(parameters, {
                defs.PDUR_DEST_DIRECTION: "TRANSMIT",
            })
            refs_ok, _ = _required_values_match(references, {
                defs.PDUR_DEST_PDU_REF: ecuc_path,
                defs.PDUR_DEST_MODULE_REF: self.destination_module_ref,
            })
            if not direct_ok or not refs_ok:
                continue
            subcontainers = destination.getparent()
            path = subcontainers.getparent() if subcontainers is not None else None
            sources = _subcontainers(path, self.namespace, defs.PDUR_SRC) if path is not None else ()
            if len(sources) != 1:
                continue
            source_parameters, source_references = semantic_values(sources[0], self.namespace)
            source_ok, _ = _required_values_match(source_parameters, {
                defs.PDUR_SRC_DIRECTION: "RECEIVE",
            })
            source_refs_ok, _ = _required_values_match(source_references, {
                defs.PDUR_SRC_MODULE_REF: self.source_module_ref,
            })
            if not source_ok or not source_refs_ok:
                # CanTp/DoIP 等诊断源即使最终落到 CanIf，也不能成为普通报文端点复用证据。
                continue
            try:
                memberships = self.routing_groups.group_memberships_for_destination(destination_path)
            except RoutingGroupModelError:
                # 组模型异常会在目标腿或成员规划阶段形成可定位的业务错误；
                # 此处只回答“是否已有可证明安全的普通应用复用证据”。
                continue
            if any(path in application_group_paths for _, path in memberships):
                destinations.append(destination_path)
        return tuple(sorted(set(destinations)))

    def _locate_target(self, route: DirectRouteChange, buffer_path: str) -> TargetResolution:
        key = route.key
        related: list[str] = []
        conflicts: list[str] = []
        endpoints: dict[tuple[str, str], DirectTargetEndpoint] = {}
        expected_parameters = {
            defs.CANIF_TX_CAN_ID: str(key.target_can_id),
            defs.CANIF_TX_CAN_ID_TYPE: route.target_message_type or "",
            defs.CANIF_TX_DLC: str(route.target_length),
            defs.CANIF_TX_CONFIRM_NAME: "NULL_PTR",
            defs.CANIF_TX_CONFIRM_UL: "NONE",
            defs.CANIF_TX_READ_NOTIFY: "false",
            defs.CANIF_TX_TYPE: "STATIC",
            defs.CANIF_TX_TRUNCATION: enabled(route.target_truncation_enabled),
        }
        for canif in self.index.find_by_definition_ref(defs.CANIF_TX):
            name = direct_child_text(canif, self.namespace, "SHORT-NAME")
            parameters, references = semantic_values(canif, self.namespace)
            same_buffer = references.get(defs.CANIF_TX_BUFFER_REF) == (buffer_path,)
            same_id = parameters.get(defs.CANIF_TX_CAN_ID) == (str(key.target_can_id),)
            same_name = message_identity_matches(name, key.target_message_name)
            if not same_buffer or not same_id or not same_name:
                # CAN ID 与 TxBuffer 只是端点身份的一部分。T13J 中不同报文名可共享这两项；
                # 名称不匹配的端点不是当前路由候选，不能把合法新增误报为自发端点占用。
                continue
            canif_path = autosar_path(canif, self.namespace)
            related.append(canif_path)
            matched, differences = _required_values_match(parameters, expected_parameters)
            pdu_refs = references.get(defs.CANIF_TX_PDU_REF, ())
            if not matched or len(pdu_refs) != 1:
                conflicts.append(
                    f"{canif_path}：名称匹配={same_name}，CAN ID 匹配={same_id}，"
                    f"PDU 引用数={len(pdu_refs)}，参数冲突={list(differences)}"
                )
                continue
            ecuc_path = pdu_refs[0]
            ecuc_matches, ecuc_detail = self._ecuc_matches(ecuc_path, route.target_length)
            if not ecuc_matches:
                conflicts.append(f"{canif_path}：{ecuc_detail}")
                continue
            referrer_paths = tuple(sorted({
                autosar_path(owner, self.namespace)
                for referrer in self.index.find_referrers(ecuc_path)
                if (owner := _container_owner(referrer)) is not None
            }))
            endpoint = DirectTargetEndpoint(
                canif_path,
                ecuc_path,
                self._ordinary_application_destinations(ecuc_path),
                referrer_paths,
            )
            endpoints[(canif_path, ecuc_path)] = endpoint
        if conflicts:
            return TargetResolution(
                DirectSemanticState.PARTIAL_CONFLICT,
                candidates=tuple(sorted(set(related))), detail="；".join(conflicts),
            )
        if not endpoints:
            return TargetResolution(DirectSemanticState.MISSING)
        if len(endpoints) != 1:
            return TargetResolution(
                DirectSemanticState.AMBIGUOUS,
                candidates=tuple(sorted(path for path, _ in endpoints)),
                detail="同一目标报文定位到多个 CanIfTx/EcuC 端点",
            )
        endpoint = next(iter(endpoints.values()))
        if not endpoint.reusable:
            return TargetResolution(
                DirectSemanticState.PARTIAL_CONFLICT,
                candidates=(endpoint.canif_path, endpoint.ecuc_path) + endpoint.referrer_paths,
                detail="目标端点已存在，但没有普通 CanIf→PduR 应用路由使用证据；"
                "工具不会把本地自发/COM 发送端点当作网关目标，也不会重复创建同一总线身份",
            )
        return TargetResolution(DirectSemanticState.FOUND, endpoint)

    def _locate_leg(
        self,
        route: DirectRouteChange,
        source: SourceResolution,
        target: TargetResolution,
        buffer_path: str,
    ) -> LegResolution:
        if source.state is not DirectSemanticState.FOUND or source.chain is None:
            return LegResolution(DirectSemanticState.MISSING)
        path_nodes = self.index.find_by_path(source.chain.routing_path)
        if len(path_nodes) != 1:
            return LegResolution(
                DirectSemanticState.AMBIGUOUS,
                candidates=(source.chain.routing_path,), detail="源 RoutingPath 无法唯一解析",
            )
        valid_target_by_ecuc: dict[str, list[DirectTargetEndpoint]] = {}
        related_target_by_ecuc: dict[str, list[str]] = {}
        if target.endpoint is not None:
            valid_target_by_ecuc.setdefault(target.endpoint.ecuc_path, []).append(target.endpoint)
        # 即使路径外还存在同 ID 的自发端点，也只按当前源路径实际引用的 EcuC 识别路由腿。
        for canif in self.index.find_by_definition_ref(defs.CANIF_TX):
            name = direct_child_text(canif, self.namespace, "SHORT-NAME")
            parameters, references = semantic_values(canif, self.namespace)
            same_buffer = references.get(defs.CANIF_TX_BUFFER_REF) == (buffer_path,)
            same_id = parameters.get(defs.CANIF_TX_CAN_ID) == (str(route.key.target_can_id),)
            same_name = message_identity_matches(name, route.key.target_message_name)
            pdu_refs = references.get(defs.CANIF_TX_PDU_REF, ())
            if not same_buffer or not same_id or len(pdu_refs) != 1:
                continue
            canif_path = autosar_path(canif, self.namespace)
            ecuc_path = pdu_refs[0]
            related_target_by_ecuc.setdefault(ecuc_path, []).append(canif_path)
            # 已位于精确源 RoutingPath 下的既有目标腿，身份由 CAN ID、TxBuffer、报文名、
            # EcuC 引用和 PduR 引用链共同确认。E0Y 真实基线的 Truncation 与标准表创建策略
            # 不同，但重复创建第二套端点更危险；这里仍严格校验类型和 DLC，保留基线策略。
            params_ok, _ = _required_values_match(parameters, {
                defs.CANIF_TX_CAN_ID: str(route.key.target_can_id),
                defs.CANIF_TX_CAN_ID_TYPE: route.target_message_type or "",
                defs.CANIF_TX_DLC: str(route.target_length),
            })
            ecuc_ok, _ = self._ecuc_matches(ecuc_path, route.target_length)
            if not same_id or not same_name or not params_ok or not ecuc_ok:
                continue
            endpoint = DirectTargetEndpoint(canif_path, ecuc_path, (), ())
            if all(item.canif_path != canif_path for item in valid_target_by_ecuc.get(ecuc_path, ())):
                valid_target_by_ecuc.setdefault(ecuc_path, []).append(endpoint)
        candidates: list[tuple[str, DirectTargetEndpoint]] = []
        conflicts: list[str] = []
        for destination in _subcontainers(path_nodes[0], self.namespace, defs.PDUR_DEST):
            destination_path = autosar_path(destination, self.namespace)
            parameters, references = semantic_values(destination, self.namespace)
            pdu_refs = references.get(defs.PDUR_DEST_PDU_REF, ())
            if len(pdu_refs) != 1 or pdu_refs[0] not in related_target_by_ecuc:
                continue
            endpoints = valid_target_by_ecuc.get(pdu_refs[0], ())
            if len(endpoints) != 1:
                conflicts.append(
                    f"{destination_path}：目标 EcuC“{pdu_refs[0]}”对应相关 CanIfTxPdu "
                    f"{related_target_by_ecuc[pdu_refs[0]]}，但满足报文名、CAN ID、类型和 DLC 的"
                    f"端点数为 {len(endpoints)}"
                )
                continue
            params_ok, parameter_conflicts = _required_values_match(parameters, {
                defs.PDUR_DEST_DIRECTION: "TRANSMIT",
                defs.PDUR_DEST_ROUTING_TYPE: "GATEWAY_ROUTING",
                defs.PDUR_DEST_PROCESSING: "IMMEDIATE",
                defs.PDUR_DEST_LENGTH_STRATEGY: (route.length_strategy or "").upper(),
                defs.PDUR_DEST_CROSS_PARTITION: "false",
                defs.PDUR_DEST_DATA_PROVISION: "PDUR_DIRECT",
                defs.PDUR_DEST_CONFIRMATION: "false",
            })
            refs_ok, reference_conflicts = _required_values_match(references, {
                defs.PDUR_DEST_PDU_REF: pdu_refs[0],
                defs.PDUR_DEST_MODULE_REF: self.destination_module_ref,
            })
            if not params_ok or not refs_ok:
                conflicts.append(
                    f"{destination_path}：{'; '.join(parameter_conflicts + reference_conflicts)}"
                )
                continue
            candidates.append((destination_path, endpoints[0]))
        if conflicts:
            return LegResolution(
                DirectSemanticState.PARTIAL_CONFLICT,
                candidates=tuple(sorted(set(item.split('：', 1)[0] for item in conflicts))),
                detail="；".join(conflicts),
            )
        if not candidates:
            return LegResolution(DirectSemanticState.MISSING)
        if len(candidates) != 1:
            return LegResolution(
                DirectSemanticState.AMBIGUOUS,
                candidates=tuple(sorted(item[0] for item in candidates)),
                detail="当前源 RoutingPath 下存在多个语义相同的目标腿",
            )
        destination_path, endpoint = candidates[0]
        try:
            memberships = self.routing_groups.group_memberships_for_destination(destination_path)
        except RoutingGroupModelError as exc:
            return LegResolution(
                DirectSemanticState.PARTIAL_CONFLICT,
                candidates=(destination_path,), detail=str(exc),
            )
        return LegResolution(DirectSemanticState.FOUND, DirectRouteLeg(
            destination_path, endpoint.canif_path, endpoint.ecuc_path, memberships,
        ))

    def locate(
        self,
        route: DirectRouteChange,
        *,
        hrh_path: str,
        buffer_path: str,
        accept_configured_source_can_type: bool = False,
    ) -> DirectRouteSemanticResult:
        """返回独立的源链、目标端点和当前源路径目标腿定位结果。"""
        source_key = (
            route.key.source_message_name, route.key.source_can_id, route.key.source_channel,
            route.source_length, route.source_message_type, route.source_rx_indication_ul,
            route.source_dlc_check_enabled, hrh_path, accept_configured_source_can_type,
        )
        target_key = (
            route.key.target_message_name, route.key.target_can_id, route.key.target_channel,
            route.target_length, route.target_message_type, route.target_truncation_enabled,
            buffer_path,
        )
        source = self._source_cache.get(source_key)
        if source is None:
            source = self._locate_source(
                route,
                hrh_path,
                accept_configured_can_type=accept_configured_source_can_type,
            )
            self._source_cache[source_key] = source
        target = self._target_cache.get(target_key)
        if target is None:
            target = self._locate_target(route, buffer_path)
            self._target_cache[target_key] = target
        leg = self._locate_leg(route, source, target, buffer_path)
        return DirectRouteSemanticResult(source, target, leg)
