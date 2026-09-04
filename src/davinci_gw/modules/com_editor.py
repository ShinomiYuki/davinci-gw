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
        self.mapping_parent = unique_template_parent_path(
            document.root, document.namespace, defs.COM_GW_MAPPING, index=self.index,
        )

    def _ipdu_candidates(
        self, message_name: str, direction: str, *, network: str | None = None,
    ) -> tuple[etree._Element, ...]:
        """按报文、方向以及可选的网段证据返回 ComIPdu 候选。"""
        suffix = "_Rx" if direction == "RECEIVE" else "_Tx"
        prefix = f"{message_name}_o"
        network_markers = (() if network is None else
                           (f"_{network}Messagelis_", f"_o{network}_"))
        candidates: list[etree._Element] = []
        for node in self.index.find_by_definition_ref(defs.COM_IPDU):
            name = direct_child_text(node, self.document.namespace, "SHORT-NAME") or ""
            if not name.startswith(prefix) or not name.endswith(suffix):
                continue
            if network_markers and not any(marker in name for marker in network_markers):
                continue
            candidates.append(node)
        return tuple(candidates)

    def locate_ipdu(
        self, message_name: str, network: str, direction: str,
    ) -> tuple[str, etree._Element | None]:
        """按报文名、方向和网段标识定位唯一 ComIPdu。"""
        candidates = self._ipdu_candidates(message_name, direction, network=network)
        if not candidates:
            return "MISSING", None
        if len(candidates) != 1:
            return "AMBIGUOUS", None
        return "FOUND", candidates[0]

    def _signal_candidates(
        self, ipdu: etree._Element, message_name: str, signal_name: str,
    ) -> tuple[etree._Element, ...]:
        """返回由指定 ComIPdu 直接引用且报文/信号名前缀精确一致的信号。"""
        _, references = semantic_values(ipdu, self.document.namespace)
        paths = references.get(defs.COM_IPDU_SIGNAL_REF, ())
        prefix = f"{signal_name}_o{message_name}_o"
        candidates: list[etree._Element] = []
        for path in paths:
            for node in self.index.find_by_path(path):
                name = direct_child_text(node, self.document.namespace, "SHORT-NAME") or ""
                if definition_ref(node, self.document.namespace) == defs.COM_SIGNAL and name.startswith(prefix):
                    candidates.append(node)
        return tuple(candidates)

    def locate_signal(
        self, ipdu: etree._Element, message_name: str, signal_name: str,
    ) -> tuple[str, etree._Element | None]:
        """只在 ComIPdu 的 ComIPduSignalRef 中定位信号，验证报文归属关系。"""
        candidates = self._signal_candidates(ipdu, message_name, signal_name)
        if not candidates:
            return "MISSING", None
        if len(candidates) != 1:
            return "AMBIGUOUS", None
        return "FOUND", candidates[0]

    def locate_signal_endpoint(
        self, message_name: str, signal_name: str, network: str, direction: str,
    ) -> tuple[str, etree._Element | None, etree._Element | None, int]:
        """精确定位信号端点，并为逻辑 LIN 网段提供项目无关的唯一性回退。

        CAN 端点仍要求 SHORT-NAME 中存在配置表网段标记。只有常规网段定位为零，
        且候选 ComIPdu 名称明确带 ``_oLIN..._`` 时，才改用“报文名、信号名、
        方向、ComIPduSignalRef”四项结构证据。LIN 逻辑节点名与物理通道名因项目而异，
        因此这里既不维护静态映射，也不按字符串相似度猜测；结构候选不唯一就阻断。
        """
        ipdus = self._ipdu_candidates(message_name, direction, network=network)
        if len(ipdus) > 1:
            return "IPDU_AMBIGUOUS", None, None, len(ipdus)
        if len(ipdus) == 1:
            signals = self._signal_candidates(ipdus[0], message_name, signal_name)
            if not signals:
                return "SIGNAL_MISSING", ipdus[0], None, 0
            if len(signals) != 1:
                return "SIGNAL_AMBIGUOUS", ipdus[0], None, len(signals)
            return "FOUND", ipdus[0], signals[0], 1

        all_ipdus = self._ipdu_candidates(message_name, direction)
        lin_ipdus = tuple(
            ipdu for ipdu in all_ipdus
            if re.search(
                r"_oLIN[^_]*_",
                direct_child_text(ipdu, self.document.namespace, "SHORT-NAME") or "",
            ) is not None
        )
        endpoint_pairs: list[tuple[etree._Element, etree._Element]] = []
        for ipdu in all_ipdus:
            endpoint_pairs.extend(
                (ipdu, signal)
                for signal in self._signal_candidates(ipdu, message_name, signal_name)
            )
        lin_pairs = tuple(pair for pair in endpoint_pairs if pair[0] in lin_ipdus)
        # “只看报文名和信号名”仍以全局结构唯一为前提：若同名端点同时存在于
        # CAN 与 LIN，不能因为只筛 LIN 就掩盖歧义并错误选择物理通道。
        if len(endpoint_pairs) == 1 and len(lin_pairs) == 1:
            ipdu, signal = endpoint_pairs[0]
            return "FOUND", ipdu, signal, 1
        if lin_pairs and len(endpoint_pairs) > 1:
            return "SIGNAL_AMBIGUOUS", None, None, len(endpoint_pairs)
        if lin_ipdus:
            ipdu = lin_ipdus[0] if len(lin_ipdus) == 1 else None
            return "SIGNAL_MISSING", ipdu, None, 0
        return "IPDU_MISSING", None, None, 0

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
        self, message_name: str, signal_name: str, direction: str,
    ) -> tuple[str, ...]:
        """返回网关 Mapping 中仍与报文/信号身份匹配的引用路径。

        DELETE 遇到已被 DBC 移除的 ComIPdu 时，不能仅凭端点缺失就断言路由
        已不存在；旧 Mapping 仍可能保留指向已删除 ComSignal 的悬空引用。这里
        只检查 ComGwSource/ComGwDestination 的真实引用，并以完整信号名前缀和
        Rx/Tx 边界匹配，避免相似报文或信号名称造成误判。
        """
        reference_definition = (
            defs.COM_GW_SOURCE_SIGNAL_REF
            if direction == "RECEIVE" else defs.COM_GW_DEST_SIGNAL_REF
        )
        prefix = f"{signal_name}_o{message_name}_o"
        suffix = "_Rx" if direction == "RECEIVE" else "_Tx"
        matches: set[str] = set()
        for mapping in self.index.find_by_definition_ref(defs.COM_GW_MAPPING):
            _, references = semantic_values(mapping, self.document.namespace, recursive=True)
            for path in references.get(reference_definition, ()):
                short_name = path.rstrip("/").rsplit("/", 1)[-1]
                if short_name.startswith(prefix) and short_name.endswith(suffix):
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
        if timeout is None:
            return None
        parameters = [(defs.COM_TIMEOUT_ACTION, "REPLACE"), (defs.COM_TIMEOUT, timeout)]
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
