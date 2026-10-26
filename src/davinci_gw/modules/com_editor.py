"""Com 编辑器：定位信号/IPdu，新增网关映射并配置源端超时。"""

from __future__ import annotations

import re
from copy import deepcopy

from lxml import etree

from davinci_gw.arxml.document import ArxmlDocument
from davinci_gw.arxml.index import ArxmlIndex
from davinci_gw.arxml.index import autosar_path
from davinci_gw.arxml.namespace import direct_child_text, local_name, qualified
from davinci_gw.domain.errors import ArxmlStructureError
from davinci_gw.domain.models import MutationAction, MutationKind, MutationOperation, SourceLocation

from . import definitions as defs
from .common import (
    MutationContext,
    definition_ref,
    inspect_operation,
    regenerate_uuids,
    semantic_values,
    unique_template_parent_path,
)


class ComEditor:
    """负责 ComGwMapping 及参与路由的既有 ComSignal 参数。"""

    def __init__(self, document: ArxmlDocument, index: ArxmlIndex | None = None) -> None:
        self.document = document
        self.index = index or document.build_index()
        self._parameter_templates: dict[str, etree._Element] = {}
        self._endpoint_evidence: dict[etree._Element, tuple[
            set[str], set[str], tuple[str, ...], tuple[str, ...], bool,
        ]] = {}
        self.mapping_parent = unique_template_parent_path(
            document.root, document.namespace, defs.COM_GW_MAPPING, index=self.index,
        )

    @staticmethod
    def _logical_network(name: str) -> str | None:
        """只识别明确的 Com 逻辑网段标记，不推断物理 CAN 通道。"""
        match = re.search(r"(?:^|_)(?:o)?([A-Za-z0-9]+)Messagelis(?:_|$)", name)
        if match is None:
            match = re.search(r"_o([A-Za-z0-9]+)(?:_(?:Rx|Tx)(?:_|$)|$)", name)
        return match[1] if match else None

    @staticmethod
    def _message_identity(name: str) -> str | None:
        return name.split("_o", 1)[0] if "_o" in name else None

    @staticmethod
    def _signal_identity_matches(actual: str, expected: str, network: str) -> bool:
        """仅接受精确原名或已确认的网段 CAN 加十进制编号后缀。"""
        return actual == expected or re.fullmatch(
            rf"{re.escape(expected)}_{re.escape(network)}CAN_[0-9]+", actual,
        ) is not None

    def _ipdu_evidence(
        self, ipdu: etree._Element,
    ) -> tuple[set[str], set[str], tuple[str, ...], tuple[str, ...], bool]:
        """收集 PDU、分组和原始信号引用；生成对象名不决定方向。"""
        if ipdu in self._endpoint_evidence:
            return self._endpoint_evidence[ipdu]
        parameters, references = semantic_values(ipdu, self.document.namespace)
        messages: set[str] = set()
        networks: set[str] = set()
        valid = True
        pdu_paths = references.get(defs.COM_PDU_ID_REF, ())
        if len(pdu_paths) != 1:
            valid = False
        for path in pdu_paths:
            nodes = self.index.find_by_path(path)
            if len(nodes) != 1 or definition_ref(nodes[0], self.document.namespace) != defs.ECUC_PDU:
                valid = False
            message = self._message_identity(path.rsplit("/", 1)[-1])
            if message:
                messages.add(message)
        for path in references.get(defs.COM_IPDU_GROUP_REF, ()):
            nodes = self.index.find_by_path(path)
            if len(nodes) != 1 or definition_ref(nodes[0], self.document.namespace) != defs.COM_IPDU_GROUP:
                valid = False
            network = self._logical_network(path.rsplit("/", 1)[-1])
            if network:
                networks.add(network)
        signal_paths = references.get(defs.COM_IPDU_SIGNAL_REF, ())
        for path in signal_paths:
            nodes = self.index.find_by_path(path)
            if len(nodes) != 1 or definition_ref(nodes[0], self.document.namespace) != defs.COM_SIGNAL:
                valid = False
                continue
            _, signal_refs = semantic_values(nodes[0], self.document.namespace)
            originals = signal_refs.get(defs.COM_SYSTEM_SIGNAL_REF, ())
            if len(originals) > 1:
                valid = False
            for original in originals:
                if not original.startswith("/") or len(original.rstrip("/").split("/")) < 4 or original.endswith("/"):
                    valid = False
                    continue
                parent = original.rstrip("/").rsplit("/", 2)[-2]
                message = self._message_identity(parent) or parent
                network = self._logical_network(parent)
                if message:
                    messages.add(message)
                if network:
                    networks.add(network)
        if not messages:
            name = (pdu_paths[0].rsplit("/", 1)[-1] if len(pdu_paths) == 1 else
                    direct_child_text(ipdu, self.document.namespace, "SHORT-NAME") or "")
            message = self._message_identity(name)
            if message or name:
                messages.add(message or name)
        result = messages, networks, parameters.get(defs.COM_IPDU_DIRECTION, ()), signal_paths, valid
        self._endpoint_evidence[ipdu] = result
        return result

    def locate_signal_endpoint(
        self, message_name: str, signal_name: str, network: str, direction: str,
    ) -> tuple[str, etree._Element | None, etree._Element | None, int]:
        """按 Com 方向、唯一 PDU 和直接信号引用定位；结构冲突优先于名称。"""
        candidates: list[etree._Element] = []
        other_pairs: list[tuple[etree._Element, etree._Element, bool]] = []
        unresolved = False
        for ipdu in self.index.find_by_definition_ref(defs.COM_IPDU):
            messages, networks, directions, paths, valid = self._ipdu_evidence(ipdu)
            if message_name not in messages:
                continue
            conflict = not valid or len(messages) != 1 or len(networks) > 1 or len(directions) != 1
            if networks and network not in networks:
                # 保留既有 LIN 唯一结构回退，CAN 不跨网段回退。
                is_lin = any(n.startswith("LIN") for n in networks)
                if conflict:
                    if is_lin:
                        return "REFERENCE_CONFLICT", ipdu, None, 0
                    continue
                if directions == (direction,):
                    signals, differing = self._endpoint_signals(paths, signal_name, next(iter(networks)))
                    if differing == "CONFLICT" and is_lin:
                        return "REFERENCE_CONFLICT", ipdu, None, 0
                    other_pairs.extend((ipdu, signal, is_lin)
                                       for signal in signals)
                continue
            if conflict:
                return "REFERENCE_CONFLICT", ipdu, None, 0
            if not networks:
                unresolved = True
                continue
            if directions != (direction,):
                if directions not in {("RECEIVE",), ("SEND",)}:
                    return "REFERENCE_CONFLICT", ipdu, None, 0
                continue
            candidates.append(ipdu)
        if len(candidates) > 1:
            return "IPDU_AMBIGUOUS", None, None, len(candidates)
        if candidates:
            ipdu = candidates[0]
            _, references = semantic_values(ipdu, self.document.namespace)
            signals, differing = self._endpoint_signals(
                references.get(defs.COM_IPDU_SIGNAL_REF, ()), signal_name, network,
            )
            if len(signals) > 1:
                return "SIGNAL_AMBIGUOUS", ipdu, None, len(signals)
            if differing == "CONFLICT":
                return "REFERENCE_CONFLICT", ipdu, None, 0
            if signals:
                return "FOUND", ipdu, signals[0], 1
            return ("IDENTITY_UNCONFIRMED" if differing else "SIGNAL_MISSING"), ipdu, None, 0
        if any(pair[2] for pair in other_pairs):
            if len(other_pairs) > 1:
                return "SIGNAL_AMBIGUOUS", None, None, len(other_pairs)
            ipdu, signal, _ = other_pairs[0]
            return "FOUND", ipdu, signal, 1
        return ("OWNERSHIP_UNCONFIRMED" if unresolved else "IPDU_MISSING"), None, None, 0

    def _endpoint_signals(
        self, paths: tuple[str, ...], signal_name: str, network: str,
    ) -> tuple[tuple[etree._Element, ...], str]:
        candidates: list[etree._Element] = []
        differing = ""
        for path in dict.fromkeys(paths):
            nodes = self.index.find_by_path(path)
            if len(nodes) != 1 or definition_ref(nodes[0], self.document.namespace) != defs.COM_SIGNAL:
                return (), "CONFLICT"
            signal = nodes[0]
            _, references = semantic_values(signal, self.document.namespace)
            originals = references.get(defs.COM_SYSTEM_SIGNAL_REF, ())
            if len(originals) > 1:
                return (), "CONFLICT"
            name = direct_child_text(signal, self.document.namespace, "SHORT-NAME") or ""
            generated_identity = name.split("_o", 1)[0]
            identity = originals[0].rsplit("/", 1)[-1] if originals else generated_identity
            if self._signal_identity_matches(identity, signal_name, network):
                candidates.append(signal)
            elif (identity.startswith(signal_name + "_")
                  or self._signal_identity_matches(generated_identity, signal_name, network)):
                differing = "DIFFERENT"
        return tuple(candidates), differing

    def mapping_operation(
        self, short_name: str, locations: tuple[SourceLocation, ...],
    ) -> MutationOperation:
        """构造 ComGwMapping 外层操作。"""
        return MutationOperation(
            MutationKind.COM_GW_MAPPING, self.mapping_parent, short_name, defs.COM_GW_MAPPING,
            source_locations=locations,
        )

    def find_mapping_by_source(
        self, signal_path: str,
    ) -> tuple[str, etree._Element | None]:
        """兼容真实工程/旧工具命名：按 Source 引用识别已有 Mapping。"""
        candidates = self.mappings_by_source(signal_path)
        if not candidates:
            return "MISSING", None
        if len(candidates) != 1:
            return "AMBIGUOUS", None
        return "FOUND", candidates[0]

    def mappings_by_source(self, signal_path: str) -> tuple[etree._Element, ...]:
        """返回全部以指定 ComSignal 为 Source 的 Mapping，供 DELETE 判断共享。"""
        candidates: list[etree._Element] = []
        for mapping in self.index.find_by_definition_ref(defs.COM_GW_MAPPING):
            _, references = semantic_values(mapping, self.document.namespace, recursive=True)
            if signal_path in references.get(defs.COM_GW_SOURCE_SIGNAL_REF, ()):
                candidates.append(mapping)
        return tuple(candidates)

    def gateway_references_for_signal_identity(
        self, message_name: str, signal_name: str, direction: str, *, network: str,
    ) -> tuple[str, ...]:
        """按网关角色与原始身份保留 DELETE 悬空引用保护，无法归属的悬空引用也阻断。"""
        reference_definition = (
            defs.COM_GW_SOURCE_SIGNAL_REF
            if direction == "RECEIVE" else defs.COM_GW_DEST_SIGNAL_REF
        )
        matches: set[str] = set()
        for mapping in self.index.find_by_definition_ref(defs.COM_GW_MAPPING):
            _, references = semantic_values(mapping, self.document.namespace, recursive=True)
            for path in references.get(reference_definition, ()):
                short_name = path.rstrip("/").rsplit("/", 1)[-1]
                nodes = self.index.find_by_path(path)
                # 对象已删除且名字不可识别时，不能证明悬空引用与本次 DELETE 无关。
                if not nodes:
                    matches.add(path)
                    continue
                for node in nodes:
                    _, refs = semantic_values(node, self.document.namespace)
                    originals = refs.get(defs.COM_SYSTEM_SIGNAL_REF, ())
                    identities = originals or (short_name,)
                    for identity in identities:
                        if originals and (not identity.startswith("/") or len(identity.split("/")) < 4):
                            matches.add(path)
                            continue
                        name = identity.rsplit("/", 1)[-1].split("_o", 1)[0]
                        parent = identity.rsplit("/", 2)[-2] if originals else short_name.partition("_o")[2]
                        identity_network = self._logical_network(parent) or network
                        if (self._signal_identity_matches(name, signal_name, identity_network)
                                and (self._message_identity(parent) or parent) == message_name):
                            matches.add(path)
        return tuple(sorted(matches))

    def find_destination_by_signal(
        self, mapping: etree._Element, signal_path: str,
    ) -> tuple[str, etree._Element | None]:
        """在一个 Mapping 内按目标信号引用识别已有 Destination。"""
        candidates: list[etree._Element] = []
        subcontainers = mapping.find(qualified(self.document.namespace, "SUB-CONTAINERS"))
        for child in list(subcontainers) if subcontainers is not None else []:
            if definition_ref(child, self.document.namespace) != defs.COM_GW_DEST:
                continue
            _, references = semantic_values(child, self.document.namespace, recursive=True)
            if references.get(defs.COM_GW_DEST_SIGNAL_REF) == (signal_path,):
                candidates.append(child)
        if not candidates:
            return "MISSING", None
        if len(candidates) != 1:
            return "AMBIGUOUS", None
        return "FOUND", candidates[0]

    @staticmethod
    def source_operation(
        mapping_path: str, short_name: str, signal_path: str,
        locations: tuple[SourceLocation, ...],
    ) -> MutationOperation:
        """构造唯一 ComGwSource 及其嵌套 ComGwSignal 引用。"""
        return MutationOperation(
            MutationKind.COM_GW_SOURCE, mapping_path, short_name, defs.COM_GW_SOURCE,
            references=((defs.COM_GW_SOURCE_SIGNAL_REF, signal_path),),
            source_locations=locations,
        )

    @staticmethod
    def destination_operation(
        mapping_path: str, short_name: str, signal_path: str,
        locations: tuple[SourceLocation, ...],
    ) -> MutationOperation:
        """构造一个 ComGwDestination；同源多目标共享 Mapping 和 Source。"""
        return MutationOperation(
            MutationKind.COM_GW_DESTINATION, mapping_path, short_name, defs.COM_GW_DEST,
            references=((defs.COM_GW_DEST_SIGNAL_REF, signal_path),),
            source_locations=locations,
        )

    @staticmethod
    def timeout_operation(
        signal_path: str, timeout: str | None, substitution: str | None,
        locations: tuple[SourceLocation, ...],
    ) -> MutationOperation | None:
        """直接使用上游标准超时时间；空值不写零，也不反推 Cycle Time。"""
        if timeout is None and substitution is None:
            return None
        parameters = []
        if timeout is not None:
            parameters.extend(((defs.COM_TIMEOUT_ACTION, "REPLACE"), (defs.COM_TIMEOUT, timeout)))
        if substitution is not None:
            parameters.append((defs.COM_TIMEOUT_SUBSTITUTION, substitution))
        return MutationOperation(
            MutationKind.COM_SIGNAL_TIMEOUT, signal_path, "", defs.COM_SIGNAL,
            action=MutationAction.UPSERT_PARAMETERS,
            parameters=tuple(parameters), source_locations=locations,
        )

    @staticmethod
    def access_operation(
        signal_path: str, locations: tuple[SourceLocation, ...],
    ) -> MutationOperation:
        """把实际参与网关路由的 Rx/Tx 信号标记为 SWC 或 COM 需要访问。"""
        return MutationOperation(
            MutationKind.COM_SIGNAL_ACCESS, signal_path, "", defs.COM_SIGNAL,
            action=MutationAction.UPSERT_PARAMETERS,
            parameters=((defs.COM_SIGNAL_ACCESS, "ACCESS_NEEDED_BY_SWC_OR_COM"),),
            source_locations=locations,
        )

    def inspect(self, operation: MutationOperation) -> str:
        """比较 Mapping/Source/Destination 完整语义。"""
        return inspect_operation(
            self.index, self.document.namespace, operation,
            recursive=operation.kind in {MutationKind.COM_GW_SOURCE, MutationKind.COM_GW_DESTINATION},
        )[0]

    def inspect_timeout(self, operation: MutationOperation) -> str:
        """已有超时完全一致则跳过，缺失则补充，不同或重复则冲突。"""
        found = self.index.find_by_path(operation.parent_path)
        if len(found) != 1:
            return "CONFLICT"
        actual, _ = semantic_values(found[0], self.document.namespace)
        missing = False
        for key, value in operation.parameters:
            values = actual.get(key, ())
            if not values:
                missing = True
            elif values != (value,):
                return "CONFLICT"
        return "MISSING" if missing else "EXISTING"

    def inspect_access(self, operation: MutationOperation) -> str:
        """目标值已存在则幂等跳过；缺失或单个旧值允许更新，重复参数阻断。"""
        found = self.index.find_by_path(operation.parent_path)
        if len(found) != 1:
            return "CONFLICT"
        parameters = found[0].find(qualified(self.document.namespace, "PARAMETER-VALUES"))
        if parameters is None:
            return "CONFLICT"
        entries = tuple(
            node for node in parameters
            if definition_ref(node, self.document.namespace) == defs.COM_SIGNAL_ACCESS
        )
        if len(entries) > 1:
            return "CONFLICT"
        if not entries:
            return "MISSING"
        values = tuple(
            child for child in entries[0]
            if isinstance(child.tag, str) and local_name(child) == "VALUE"
        )
        if len(values) != 1:
            return "CONFLICT"
        if values[0].text == "ACCESS_NEEDED_BY_SWC_OR_COM":
            return "EXISTING"
        return "MISSING"

    def _parameter_template(self, definition: str) -> etree._Element:
        cached = self._parameter_templates.get(definition)
        if cached is not None:
            return cached
        for node in self.index.find_by_definition_ref(definition):
            if local_name(node).endswith("PARAM-VALUE"):
                self._parameter_templates[definition] = node
                return node
        raise ArxmlStructureError(f"基准ARXML中缺少 ComSignal 参数模板“{definition}”。")

    def ensure_access_template(self, operation: MutationOperation) -> None:
        """只有目标信号缺少参数节点时才要求基线提供同类型模板。"""
        signal = self.index.find_by_path(operation.parent_path)
        if len(signal) != 1:
            raise ArxmlStructureError(f"ComSignal“{operation.parent_path}”无法唯一定位。")
        parameters = signal[0].find(qualified(self.document.namespace, "PARAMETER-VALUES"))
        entries = () if parameters is None else tuple(
            node for node in parameters
            if definition_ref(node, self.document.namespace) == defs.COM_SIGNAL_ACCESS
        )
        if not entries:
            self._parameter_template(defs.COM_SIGNAL_ACCESS)

    def ensure_timeout_templates(self, operation: MutationOperation) -> None:
        """在修改前确认全部计划超时参数都有真实同类型模板。"""
        for parameter_definition, _ in operation.parameters:
            self._parameter_template(parameter_definition)

    def _build_timeout_parameter(
        self, operation: MutationOperation, parameter_definition: str, value: str,
    ) -> etree._Element:
        """深复制单个超时参数并重建潜在 UUID，避免带入模板标识。"""
        parameter = deepcopy(self._parameter_template(parameter_definition))
        for child in parameter:
            if isinstance(child.tag, str) and local_name(child) == "VALUE":
                child.text = value
                break
        regenerate_uuids(
            parameter, f"{operation.parent_path}:timeout:{parameter_definition}",
        )
        return parameter

    def _build_access_parameter(self, operation: MutationOperation) -> etree._Element:
        """从真实枚举参数克隆 Access 节点，并使用独立种子生成潜在 UUID。"""
        parameter = deepcopy(self._parameter_template(defs.COM_SIGNAL_ACCESS))
        for child in parameter:
            if isinstance(child.tag, str) and local_name(child) == "VALUE":
                child.text = "ACCESS_NEEDED_BY_SWC_OR_COM"
                break
        regenerate_uuids(parameter, f"{operation.parent_path}:access:{defs.COM_SIGNAL_ACCESS}")
        return parameter

    def timeout_candidate_uuids(self, operation: MutationOperation) -> tuple[str, ...]:
        """返回超时参数克隆会生成的 UUID，供完整计划预检。"""
        values: list[str] = []
        for definition, value in operation.parameters:
            parameter = self._build_timeout_parameter(operation, definition, value)
            values.extend(node.get("UUID") for node in parameter.iter() if node.get("UUID"))
        return tuple(values)

    def access_candidate_uuids(self, operation: MutationOperation) -> tuple[str, ...]:
        """已有参数仅改值不产生 UUID；参数缺失时返回克隆节点的确定性 UUID。"""
        signal = self.index.find_by_path(operation.parent_path)
        if len(signal) != 1:
            return ()
        parameters = signal[0].find(qualified(self.document.namespace, "PARAMETER-VALUES"))
        entries = () if parameters is None else tuple(
            node for node in parameters
            if definition_ref(node, self.document.namespace) == defs.COM_SIGNAL_ACCESS
        )
        if entries:
            return ()
        parameter = self._build_access_parameter(operation)
        return tuple(node.get("UUID") for node in parameter.iter() if node.get("UUID"))

    def apply_access(self, context: MutationContext, operation: MutationOperation) -> None:
        """更新既有 Access 值；参数缺失时从真实同类型节点克隆补齐。"""
        signal = context.node_at(operation.parent_path)
        parameters = signal.find(qualified(context.namespace, "PARAMETER-VALUES"))
        if parameters is None:
            raise ArxmlStructureError(f"ComSignal“{operation.parent_path}”缺少PARAMETER-VALUES。")
        entries = tuple(
            node for node in parameters
            if definition_ref(node, context.namespace) == defs.COM_SIGNAL_ACCESS
        )
        if len(entries) > 1:
            raise ArxmlStructureError(
                f"ComSignal“{operation.parent_path}”存在重复 ComSignalAccess 参数。"
            )
        if entries:
            value = next(
                (child for child in entries[0]
                 if isinstance(child.tag, str) and local_name(child) == "VALUE"),
                None,
            )
            if value is None:
                raise ArxmlStructureError(
                    f"ComSignal“{operation.parent_path}”的 ComSignalAccess 缺少 VALUE。"
                )
            if value.text != "ACCESS_NEEDED_BY_SWC_OR_COM":
                context.set_text(value, "ACCESS_NEEDED_BY_SWC_OR_COM")
            return
        parameter = self._build_access_parameter(operation)
        parameters.append(parameter)
        context.inserted.append(parameter)

    def apply_timeout(self, context: MutationContext, operation: MutationOperation) -> None:
        """只向已有源 ComSignal 补充缺失超时参数，不触碰目标信号。"""
        signal = context.node_at(operation.parent_path)
        parameters = signal.find(qualified(context.namespace, "PARAMETER-VALUES"))
        if parameters is None:
            raise ArxmlStructureError(f"源ComSignal“{operation.parent_path}”缺少PARAMETER-VALUES。")
        actual, _ = semantic_values(signal, context.namespace)
        for parameter_definition, value in operation.parameters:
            if actual.get(parameter_definition) == (value,):
                continue
            parameter = self._build_timeout_parameter(operation, parameter_definition, value)
            parameters.append(parameter)
            context.inserted.append(parameter)

    def apply(self, context: MutationContext, operation: MutationOperation) -> None:
        """应用一个 Com 映射容器或既有 ComSignal 参数操作。"""
        if operation.kind is MutationKind.COM_SIGNAL_ACCESS:
            self.apply_access(context, operation)
        elif operation.kind is MutationKind.COM_SIGNAL_TIMEOUT:
            self.apply_timeout(context, operation)
        else:
            context.apply_container(operation)
