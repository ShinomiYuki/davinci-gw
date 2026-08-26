"""Com 编辑器：定位信号/IPdu，新增网关映射并配置源端超时。"""

from __future__ import annotations

from copy import deepcopy

from lxml import etree

from davinci_gw.arxml.document import ArxmlDocument
from davinci_gw.arxml.index import ArxmlIndex
from davinci_gw.arxml.index import autosar_path
from davinci_gw.arxml.namespace import direct_child_text, local_name, qualified
from davinci_gw.domain.errors import ArxmlStructureError
from davinci_gw.domain.models import MutationKind, MutationOperation, SourceLocation

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
    """只负责 ComGwMapping、Source、Destination 和已有源 ComSignal 超时参数。"""

    def __init__(self, document: ArxmlDocument, index: ArxmlIndex | None = None) -> None:
        self.document = document
        self.index = index or document.build_index()
        self.mapping_parent = unique_template_parent_path(
            document.root, document.namespace, defs.COM_GW_MAPPING,
        )

    def locate_ipdu(
        self, message_name: str, network: str, direction: str,
    ) -> tuple[str, etree._Element | None]:
        """按报文名、方向和网段标识定位唯一 ComIPdu。"""
        suffix = "_Rx" if direction == "RECEIVE" else "_Tx"
        prefix = f"{message_name}_o"
        network_markers = (f"_{network}Messagelis_", f"_o{network}_")
        candidates: list[etree._Element] = []
        for node in self.document.root.iter(qualified(self.document.namespace, "ECUC-CONTAINER-VALUE")):
            if definition_ref(node, self.document.namespace) != defs.COM_IPDU:
                continue
            name = direct_child_text(node, self.document.namespace, "SHORT-NAME") or ""
            if (name.startswith(prefix) and name.endswith(suffix)
                    and any(marker in name for marker in network_markers)):
                candidates.append(node)
        if not candidates:
            return "MISSING", None
        if len(candidates) != 1:
            return "AMBIGUOUS", None
        return "FOUND", candidates[0]

    def locate_signal(
        self, ipdu: etree._Element, message_name: str, signal_name: str,
    ) -> tuple[str, etree._Element | None]:
        """只在 ComIPdu 的 ComIPduSignalRef 中定位信号，验证报文归属关系。"""
        _, references = semantic_values(ipdu, self.document.namespace)
        paths = references.get(defs.COM_IPDU_SIGNAL_REF, ())
        prefix = f"{signal_name}_o{message_name}_o"
        candidates: list[etree._Element] = []
        for path in paths:
            found = self.index.find_by_path(path)
            for node in found:
                name = direct_child_text(node, self.document.namespace, "SHORT-NAME") or ""
                if definition_ref(node, self.document.namespace) == defs.COM_SIGNAL and name.startswith(prefix):
                    candidates.append(node)
        if not candidates:
            return "MISSING", None
        if len(candidates) != 1:
            return "AMBIGUOUS", None
        return "FOUND", candidates[0]

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
        candidates: list[etree._Element] = []
        for mapping in self.document.root.iter(qualified(self.document.namespace, "ECUC-CONTAINER-VALUE")):
            if definition_ref(mapping, self.document.namespace) != defs.COM_GW_MAPPING:
                continue
            _, references = semantic_values(mapping, self.document.namespace, recursive=True)
            if signal_path in references.get(defs.COM_GW_SOURCE_SIGNAL_REF, ()):
                candidates.append(mapping)
        if not candidates:
            return "MISSING", None
        if len(candidates) != 1:
            return "AMBIGUOUS", None
        return "FOUND", candidates[0]

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
        if timeout is None:
            return None
        parameters = [(defs.COM_TIMEOUT_ACTION, "REPLACE"), (defs.COM_TIMEOUT, timeout)]
        if substitution is not None:
            parameters.append((defs.COM_TIMEOUT_SUBSTITUTION, substitution))
        return MutationOperation(
            MutationKind.COM_SIGNAL_TIMEOUT, signal_path, "", defs.COM_SIGNAL,
            parameters=tuple(parameters), source_locations=locations,
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

    def _parameter_template(self, definition: str) -> etree._Element:
        for node in self.document.root.iter():
            if definition_ref(node, self.document.namespace) == definition and local_name(node).endswith("PARAM-VALUE"):
                return node
        raise ArxmlStructureError(f"基准ARXML中缺少超时参数模板“{definition}”。")

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

    def timeout_candidate_uuids(self, operation: MutationOperation) -> tuple[str, ...]:
        """返回超时参数克隆会生成的 UUID，供完整计划预检。"""
        values: list[str] = []
        for definition, value in operation.parameters:
            parameter = self._build_timeout_parameter(operation, definition, value)
            values.extend(node.get("UUID") for node in parameter.iter() if node.get("UUID"))
        return tuple(values)

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
        """应用一个 Com 映射容器或源端超时操作。"""
        if operation.kind is MutationKind.COM_SIGNAL_TIMEOUT:
            self.apply_timeout(context, operation)
        else:
            context.apply_container(operation)
