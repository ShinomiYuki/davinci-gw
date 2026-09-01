"""PduR 路由组成员关系的识别、预检和引用级增量修改。"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from typing import Protocol, Sequence

from lxml import etree

from davinci_gw.arxml.document import ArxmlDocument
from davinci_gw.arxml.index import ArxmlIndex, autosar_path
from davinci_gw.arxml.namespace import direct_child_text, local_name, qualified
from davinci_gw.domain.errors import ArxmlStructureError
from davinci_gw.domain.models import (
    MutationAction,
    MutationKind,
    MutationOperation,
    ReferenceDataEntry,
    SourceLocation,
)
from davinci_gw.modules import definitions as defs
from davinci_gw.modules.common import (
    MutationContext,
    definition_ref,
    semantic_values,
    unique_named_node,
)


@dataclass(frozen=True, slots=True)
class RoutingGroupMembershipRequest:
    """由路由语义派生的成员关系请求，不承载任何 Excel 路由组字段。"""

    target_channel: str
    target_buffer_path: str
    destination_path: str
    source: SourceLocation
    destination_exists: bool = True


@dataclass(frozen=True, slots=True)
class RoutingGroupMembershipProblem:
    """由协调器转换为带工作簿路径和路由身份的 ValidationIssue。"""

    code: str
    message: str
    source: SourceLocation
    warning: bool = False


@dataclass(frozen=True, slots=True)
class RoutingGroupMembershipPlan:
    operations: tuple[MutationOperation, ...] = ()
    problems: tuple[RoutingGroupMembershipProblem, ...] = ()


@dataclass(frozen=True, slots=True)
class _MemberEntry:
    node: etree._Element
    target_path: str


@dataclass(frozen=True, slots=True)
class _GroupState:
    name: str
    path: str
    node: etree._Element
    members: tuple[_MemberEntry, ...]


@dataclass(frozen=True, slots=True)
class _ApplicationGroupIndex:
    """一次基线解析得到的主通道索引及基线一致性问题。"""

    by_channel: dict[str, _GroupState]
    buffer_channels: dict[str, tuple[str, ...]]
    problems: tuple[RoutingGroupMembershipProblem, ...]


class RoutingGroupModelError(ValueError):
    """真实节点不符合当前明确支持的成员引用方向。"""


class RoutingGroupAdapter(Protocol):
    """为其他真实 Vector 结构保留的最小适配器边界。"""

    group_definition: str
    member_reference_definition: str

    def inspect_members(
        self, group: etree._Element, namespace: str,
    ) -> tuple[_MemberEntry, ...]: ...

    def insert_member(
        self, group: etree._Element, namespace: str, target_path: str,
    ) -> tuple[etree._Element, ...]: ...


class MicrosarRoutingGroupAdapter:
    """支持当前基线中 RoutingGroup 直接持有 DestPdu 引用的结构。"""

    group_definition = defs.PDUR_ROUTING_GROUP
    member_reference_definition = defs.PDUR_ROUTING_GROUP_DEST_REF

    def inspect_members(
        self, group: etree._Element, namespace: str,
    ) -> tuple[_MemberEntry, ...]:
        if (local_name(group) != "ECUC-CONTAINER-VALUE"
                or definition_ref(group, namespace) != self.group_definition):
            raise RoutingGroupModelError("容器定义或节点类型不符合当前 MICROSAR 模型")
        values = group.find(qualified(namespace, "REFERENCE-VALUES"))
        if values is None:
            return ()
        result: list[_MemberEntry] = []
        for entry in values:
            if local_name(entry) != "ECUC-REFERENCE-VALUE":
                raise RoutingGroupModelError("REFERENCE-VALUES 中存在非 ECUC-REFERENCE-VALUE 节点")
            definitions = entry.findall(qualified(namespace, "DEFINITION-REF"))
            targets = entry.findall(qualified(namespace, "VALUE-REF"))
            if (len(definitions) != 1 or len(targets) != 1
                    or definition_ref(entry, namespace) != self.member_reference_definition
                    or definitions[0].get("DEST") != "ECUC-REFERENCE-DEF"
                    or targets[0].get("DEST") != "ECUC-CONTAINER-VALUE"
                    or targets[0].text is None or not targets[0].text.strip()):
                raise RoutingGroupModelError(
                    "成员引用的 DEFINITION-REF、VALUE-REF 或 DEST 属性不符合当前 MICROSAR 模型"
                )
            result.append(_MemberEntry(entry, targets[0].text.strip()))
        return tuple(result)

    def insert_member(
        self, group: etree._Element, namespace: str, target_path: str,
    ) -> tuple[etree._Element, ...]:
        """只插入一个引用值，不触碰组名、GroupId、初始化状态或其他成员。"""
        created: list[etree._Element] = []
        values = group.find(qualified(namespace, "REFERENCE-VALUES"))
        if values is None:
            values = etree.Element(qualified(namespace, "REFERENCE-VALUES"))
            subcontainers = group.find(qualified(namespace, "SUB-CONTAINERS"))
            group.insert(group.index(subcontainers) if subcontainers is not None else len(group), values)
            created.append(values)
        entry = etree.Element(qualified(namespace, "ECUC-REFERENCE-VALUE"))
        definition = etree.SubElement(entry, qualified(namespace, "DEFINITION-REF"))
        definition.set("DEST", "ECUC-REFERENCE-DEF")
        definition.text = self.member_reference_definition
        target = etree.SubElement(entry, qualified(namespace, "VALUE-REF"))
        target.set("DEST", "ECUC-CONTAINER-VALUE")
        target.text = target_path

        insert_at = len(values)
        for index, current in enumerate(values):
            current_target = direct_child_text(current, namespace, "VALUE-REF") or ""
            if current_target > target_path:
                insert_at = index
                break
        values.insert(insert_at, entry)
        created.append(entry)
        return tuple(created)


class RoutingGroupMembershipService:
    """集中承担路由组查询、结构校验、幂等规划和引用级应用。"""

    def __init__(
        self,
        document: ArxmlDocument,
        index: ArxmlIndex | None = None,
        adapter: RoutingGroupAdapter | None = None,
        reference_data: Sequence[ReferenceDataEntry] = (),
    ) -> None:
        self.document = document
        self.index = index or document.build_index()
        self.adapter = adapter or MicrosarRoutingGroupAdapter()
        self.reference_data = tuple(reference_data)
        self._application_index: _ApplicationGroupIndex | None = None
        self._reported_index_warnings = False

    @staticmethod
    def _problem(
        code: str, message: str, source: SourceLocation, *, warning: bool = False,
    ) -> RoutingGroupMembershipProblem:
        return RoutingGroupMembershipProblem(code, message, source, warning)

    @staticmethod
    def _container_owner(node: etree._Element) -> etree._Element | None:
        current: etree._Element | None = node
        while current is not None:
            if local_name(current) == "ECUC-CONTAINER-VALUE":
                return current
            current = current.getparent()
        return None

    @staticmethod
    def _module_owner(node: etree._Element) -> etree._Element | None:
        current: etree._Element | None = node
        while current is not None:
            if local_name(current) == "ECUC-MODULE-CONFIGURATION-VALUES":
                return current
            current = current.getparent()
        return None

    @staticmethod
    def _for_destination(
        problems: tuple[RoutingGroupMembershipProblem, ...], destination_path: str,
    ) -> tuple[RoutingGroupMembershipProblem, ...]:
        """业务错误补充当前 DestPdu；基线警告不得错误绑定到正在处理的路由。"""
        return tuple(
            problem if problem.warning or destination_path in problem.message else replace(
                problem,
                message=f"{problem.message}（目标 PduRDestPdu：{destination_path}）",
            )
            for problem in problems
        )

    def _inspect_group(
        self, node: etree._Element, source: SourceLocation,
    ) -> tuple[_GroupState | None, tuple[RoutingGroupMembershipProblem, ...]]:
        name = direct_child_text(node, self.document.namespace, "SHORT-NAME") or "<未命名>"
        path = autosar_path(node, self.document.namespace)
        try:
            members = self.adapter.inspect_members(node, self.document.namespace)
        except RoutingGroupModelError as exc:
            return None, (self._problem(
                "PDUR_ROUTING_GROUP_MODEL_UNSUPPORTED",
                f"路由组“{name}”（{path}）结构不受支持：{exc}。",
                source,
            ),)
        counts = Counter(member.target_path for member in members)
        duplicates = tuple(sorted(target for target, count in counts.items() if count > 1))
        if duplicates:
            return None, (self._problem(
                "PDUR_ROUTING_GROUP_MEMBER_DUPLICATE",
                f"路由组“{name}”（{path}）存在重复成员引用：{', '.join(duplicates)}。"
                "请先在 DaVinci 中清理重复引用。",
                source,
            ),)
        for member in members:
            targets = self.index.find_by_path(member.target_path)
            if (len(targets) != 1
                    or definition_ref(targets[0], self.document.namespace) != defs.PDUR_DEST):
                return None, (self._problem(
                    "PDUR_ROUTING_GROUP_MEMBER_DANGLING",
                    f"路由组“{name}”（{path}）成员引用“{member.target_path}”无法唯一解析为"
                    " PduRDestPdu；禁止生成含悬空引用的输出。",
                    source,
                ),)
        return _GroupState(name, path, node, members), ()

    def _all_groups(
        self, source: SourceLocation,
    ) -> tuple[tuple[_GroupState, ...], tuple[RoutingGroupMembershipProblem, ...]]:
        nodes = self.index.find_by_definition_ref(self.adapter.group_definition)
        names = [direct_child_text(node, self.document.namespace, "SHORT-NAME") for node in nodes]
        duplicate_names = {name for name, count in Counter(names).items() if name and count > 1}
        if duplicate_names:
            return (), tuple(self._problem(
                "PDUR_ROUTING_GROUP_AMBIGUOUS",
                f"路由组名称“{name}”存在多个候选，无法安全查询成员关系。",
                source,
            ) for name in sorted(duplicate_names))
        states: list[_GroupState] = []
        problems: list[RoutingGroupMembershipProblem] = []
        for node in nodes:
            state, current = self._inspect_group(node, source)
            problems.extend(current)
            if state is not None:
                states.append(state)
        return tuple(states), tuple(problems)

    def _buffer_channels(
        self,
    ) -> tuple[dict[str, tuple[str, ...]], tuple[RoutingGroupMembershipProblem, ...]]:
        """把工作簿中的 TxBuffer 名称解析成完整路径，避免仅按短名连接两侧数据。"""
        channels_by_path: dict[str, list[str]] = {}
        for entry in self.reference_data:
            if not entry.tx_buffer_name:
                continue
            state, node = unique_named_node(
                self.index, self.document.namespace, entry.tx_buffer_name, defs.CANIF_BUFFER,
            )
            if state != "FOUND" or node is None:
                # 只有被应用组成员实际使用的 TxBuffer 才会在下游形成阻断；此处不让无关通道污染结果。
                continue
            path = autosar_path(node, self.document.namespace)
            channels_by_path.setdefault(path, []).append(entry.channel_name)
        normalized = {
            path: tuple(sorted(set(channels))) for path, channels in channels_by_path.items()
        }
        # 同一 TxBuffer 可能只在源端引用数据中复用；仅当应用组成员或当前目标行实际使用它时阻断。
        return normalized, ()

    def _member_channel(
        self,
        state: _GroupState,
        member: _MemberEntry,
        buffer_channels: dict[str, tuple[str, ...]],
    ) -> tuple[str | None, str | None, int | None, RoutingGroupMembershipProblem | None]:
        """沿 DestPdu→EcuC PDU→CanIfTxPdu→TxBuffer 精确反查目标通道。"""
        destination = self.index.find_by_path(member.target_path)[0]
        _, destination_refs = semantic_values(destination, self.document.namespace)
        pdu_refs = destination_refs.get(defs.PDUR_DEST_PDU_REF, ())
        module_refs = destination_refs.get(defs.PDUR_DEST_MODULE_REF, ())
        if len(pdu_refs) != 1 or len(module_refs) != 1:
            return None, None, None, self._problem(
                "PDUR_ROUTING_GROUP_MEMBER_SEMANTIC_AMBIGUOUS",
                f"应用组候选“{state.name}”（{state.path}）的成员“{member.target_path}”"
                f"具有 {len(pdu_refs)} 个目标 PDU 引用和 {len(module_refs)} 个模块引用，无法分类。",
                SourceLocation(),
            )
        tx_nodes: dict[str, etree._Element] = {}
        for referrer in self.index.find_referrers(pdu_refs[0]):
            owner = self._container_owner(referrer)
            if owner is not None and definition_ref(owner, self.document.namespace) == defs.CANIF_TX:
                tx_nodes[autosar_path(owner, self.document.namespace)] = owner
        if not tx_nodes:
            # 没有 CanIfTx 链的纯 CanTp/DoIP 成员属于诊断路由组，不进入普通报文候选。
            return None, None, None, None
        if len(tx_nodes) != 1:
            return None, None, None, self._problem(
                "PDUR_ROUTING_GROUP_TARGET_AMBIGUOUS",
                f"路由组“{state.name}”（{state.path}）成员“{member.target_path}”的 EcuC PDU"
                f"被 {len(tx_nodes)} 个 CanIfTxPdu 引用：{sorted(tx_nodes)}。",
                SourceLocation(),
            )
        tx_path, tx_node = next(iter(tx_nodes.items()))
        module_nodes = self.index.find_by_path(module_refs[0])
        tx_module = self._module_owner(tx_node)
        if (len(module_nodes) != 1
                or definition_ref(module_nodes[0], self.document.namespace) != defs.PDUR_BSW_MODULE
                or tx_module is None):
            return None, None, None, self._problem(
                "PDUR_ROUTING_GROUP_MODULE_UNRESOLVED",
                f"路由组“{state.name}”（{state.path}）成员“{member.target_path}”的模块引用"
                f"“{module_refs[0]}”无法唯一解析为 PduR BswModule。",
                SourceLocation(),
            )
        _, bsw_refs = semantic_values(module_nodes[0], self.document.namespace)
        expected_module_path = autosar_path(tx_module, self.document.namespace)
        if bsw_refs.get(defs.PDUR_BSW_MODULE_REF) != (expected_module_path,):
            return None, None, None, self._problem(
                "PDUR_ROUTING_GROUP_MODULE_MISMATCH",
                f"路由组“{state.name}”（{state.path}）成员“{member.target_path}”实际通过"
                f" CanIfTxPdu“{tx_path}”落在模块“{expected_module_path}”，但其 PduR BswModule"
                f" 引用为 {list(bsw_refs.get(defs.PDUR_BSW_MODULE_REF, ()))}。",
                SourceLocation(),
            )
        parameters, refs = semantic_values(tx_node, self.document.namespace)
        buffers = refs.get(defs.CANIF_TX_BUFFER_REF, ())
        can_ids = parameters.get(defs.CANIF_TX_CAN_ID, ())
        if len(buffers) != 1 or len(can_ids) != 1:
            return None, None, None, self._problem(
                "PDUR_ROUTING_GROUP_TARGET_AMBIGUOUS",
                f"CanIfTxPdu“{tx_path}”的 CAN ID 或 TxBuffer 引用不唯一，"
                f"对应组“{state.name}”、成员“{member.target_path}”。",
                SourceLocation(),
            )
        channels = buffer_channels.get(buffers[0], ())
        if len(channels) != 1:
            return None, None, None, self._problem(
                "PDUR_ROUTING_GROUP_CHANNEL_UNRESOLVED",
                f"路由组“{state.name}”（{state.path}）成员“{member.target_path}”通过"
                f" CanIfTxPdu“{tx_path}”指向 TxBuffer“{buffers[0]}”，但引用数据实际映射"
                f"到 {len(channels)} 个通道 {list(channels)}。",
                SourceLocation(),
            )
        try:
            can_id = int(can_ids[0], 0)
        except ValueError:
            can_id = None
        return channels[0], direct_child_text(tx_node, self.document.namespace, "SHORT-NAME"), can_id, None

    def _build_application_index(self) -> _ApplicationGroupIndex:
        states, group_problems = self._all_groups(SourceLocation())
        buffer_channels, buffer_problems = self._buffer_channels()
        problems = list(group_problems + buffer_problems)
        main_groups: list[tuple[str, _GroupState]] = []
        deviations: list[tuple[_GroupState, _MemberEntry, str, str | None, int | None]] = []
        for state in states:
            if not state.members:
                continue
            resolved: list[tuple[_MemberEntry, str, str | None, int | None]] = []
            non_canif = 0
            state_errors: list[RoutingGroupMembershipProblem] = []
            for member in state.members:
                channel, message, can_id, problem = self._member_channel(
                    state, member, buffer_channels,
                )
                if problem is not None:
                    state_errors.append(problem)
                elif channel is None:
                    non_canif += 1
                else:
                    resolved.append((member, channel, message, can_id))
            if not resolved and non_canif == len(state.members) and not state_errors:
                continue
            if state_errors:
                problems.extend(state_errors)
                continue
            if non_canif:
                problems.append(self._problem(
                    "PDUR_ROUTING_GROUP_MIXED_MODULES",
                    f"路由组“{state.name}”（{state.path}）同时包含 CanIf 与非 CanIf 成员，"
                    "不能作为普通应用路由组。",
                    SourceLocation(),
                ))
                continue
            counts = Counter(channel for _, channel, _, _ in resolved)
            highest = max(counts.values())
            leaders = sorted(channel for channel, count in counts.items() if count == highest)
            if len(leaders) != 1:
                problems.append(self._problem(
                    "PDUR_ROUTING_GROUP_MAIN_CHANNEL_AMBIGUOUS",
                    f"路由组“{state.name}”（{state.path}）的主通道最高计数并列：{dict(counts)}。",
                    SourceLocation(),
                ))
                continue
            main = leaders[0]
            main_groups.append((main, state))
            deviations.extend(
                (state, member, channel, message, can_id)
                for member, channel, message, can_id in resolved if channel != main
            )
        by_channel: dict[str, _GroupState] = {}
        for channel, candidates in sorted(
            (channel, [state for current, state in main_groups if current == channel])
            for channel in {current for current, _ in main_groups}
        ):
            if len(candidates) != 1:
                problems.append(self._problem(
                    "PDUR_ROUTING_GROUP_CHANNEL_AMBIGUOUS",
                    f"目标通道“{channel}”被多个应用路由组判定为主通道："
                    f"{[(state.name, state.path) for state in candidates]}。",
                    SourceLocation(),
                ))
            else:
                by_channel[channel] = candidates[0]
        for state, member, channel, message, can_id in deviations:
            can_id_text = f"0x{can_id:X}" if can_id is not None else "<无效>"
            problems.append(self._problem(
                "PDUR_ROUTING_GROUP_NON_MAIN_MEMBER",
                f"基线一致性警告：路由组“{state.name}”（{state.path}）的成员"
                f"“{member.target_path}”实际落在非主通道“{channel}”，报文“{message or '<未命名>'}”"
                f"、CAN ID {can_id_text}；该成员不会污染通道到主组的映射。",
                SourceLocation(),
                warning=True,
            ))
        return _ApplicationGroupIndex(by_channel, buffer_channels, tuple(problems))

    def application_group_mapping(
        self, source: SourceLocation = SourceLocation(),
    ) -> tuple[dict[str, tuple[str, str]], tuple[RoutingGroupMembershipProblem, ...]]:
        """公开只读的动态映射结果，供集成验证和问题展示使用。"""
        index = self._get_application_index()
        return (
            {channel: (state.name, state.path) for channel, state in index.by_channel.items()},
            tuple(
                problem if problem.warning else replace(problem, source=source)
                for problem in index.problems
            ),
        )

    def _get_application_index(self) -> _ApplicationGroupIndex:
        if self._application_index is None:
            self._application_index = self._build_application_index()
        return self._application_index

    def _index_problems(
        self, source: SourceLocation,
    ) -> tuple[RoutingGroupMembershipProblem, ...]:
        index = self._get_application_index()
        result = tuple(
            problem if problem.warning else replace(problem, source=source)
            for problem in index.problems
            if not problem.warning or not self._reported_index_warnings
        )
        self._reported_index_warnings = True
        return result

    def _group_for_request(
        self, request: RoutingGroupMembershipRequest,
    ) -> tuple[_GroupState | None, tuple[RoutingGroupMembershipProblem, ...]]:
        index = self._get_application_index()
        problems = list(self._index_problems(request.source))
        if any(not problem.warning for problem in problems):
            return None, self._for_destination(tuple(problems), request.destination_path)
        channels = index.buffer_channels.get(request.target_buffer_path, ())
        if channels != (request.target_channel,):
            problems.append(self._problem(
                "PDUR_ROUTING_GROUP_TARGET_IDENTITY_CONFLICT",
                f"目标通道“{request.target_channel}”与 TxBuffer“{request.target_buffer_path}”"
                f"的引用数据映射 {list(channels)} 不一致。",
                request.source,
            ))
            return None, self._for_destination(tuple(problems), request.destination_path)
        state = index.by_channel.get(request.target_channel)
        if state is None:
            problems.append(self._problem(
                "PDUR_APPLICATION_ROUTING_GROUP_NOT_FOUND",
                f"目标通道“{request.target_channel}”未解析到唯一普通应用路由组；"
                "工具不会回退到 CanTp/DoIP 诊断组，也不会创建或猜测路由组。",
                request.source,
            ))
        return state, self._for_destination(tuple(problems), request.destination_path)

    def groups_for_destination(self, destination_path: str) -> tuple[str, ...]:
        """严格查询 DestPdu 所属全部组；不支持或损坏的模型以异常显式阻断。"""
        states, problems = self._all_groups(SourceLocation())
        if problems:
            raise RoutingGroupModelError("；".join(problem.message for problem in problems))
        return tuple(sorted(
            state.name for state in states
            if any(member.target_path == destination_path for member in state.members)
        ))

    def reference_count(self, group_path: str, destination_path: str) -> int:
        """按完整组路径和完整 DestPdu 路径统计精确成员引用。"""
        groups = self.index.find_by_path(group_path)
        if len(groups) != 1:
            raise RoutingGroupModelError(
                f"路由组路径“{group_path}”实际找到{len(groups)}个候选。"
            )
        members = self.adapter.inspect_members(groups[0], self.document.namespace)
        return sum(member.target_path == destination_path for member in members)

    def plan_add(self, request: RoutingGroupMembershipRequest) -> RoutingGroupMembershipPlan:
        state, problems = self._group_for_request(request)
        if state is None or any(not problem.warning for problem in problems):
            return RoutingGroupMembershipPlan((), problems)
        matching = tuple(
            member for member in state.members if member.target_path == request.destination_path
        )
        operations = () if matching else (MutationOperation(
            MutationKind.PDUR_ROUTING_GROUP_MEMBERSHIP,
            state.path,
            "",
            self.adapter.member_reference_definition,
            action=MutationAction.ADD_REFERENCE,
            references=((self.adapter.member_reference_definition, request.destination_path),),
            source_locations=(request.source,),
        ),)
        return RoutingGroupMembershipPlan(
            operations, problems,
        )

    def plan_delete(
        self, requests: tuple[RoutingGroupMembershipRequest, ...],
    ) -> RoutingGroupMembershipPlan:
        if not requests:
            return RoutingGroupMembershipPlan()
        states, problems_tuple = self._all_groups(requests[0].source)
        problems = list(self._for_destination(problems_tuple, requests[0].destination_path))
        operations: dict[tuple[object, ...], MutationOperation] = {}
        for request in requests:
            expected, current = self._group_for_request(request)
            problems.extend(current)
            actual = tuple(
                state for state in states
                if any(member.target_path == request.destination_path for member in state.members)
            )
            if not request.destination_exists:
                if actual:
                    problems.append(self._problem(
                        "PDUR_ROUTING_GROUP_MEMBER_DANGLING",
                        f"目标 PduRDestPdu“{request.destination_path}”已不存在，但路由组"
                        f" {[state.name for state in actual]} 仍引用该路径。",
                        request.source,
                    ))
                continue
            if expected is None:
                continue
            if not actual:
                problems.append(self._problem(
                    "PDUR_ROUTING_GROUP_MEMBER_NOT_FOUND",
                    f"目标 PduRDestPdu“{request.destination_path}”没有任何路由组成员引用；"
                    f"按目标通道应属于“{expected.name}”（{expected.path}）。",
                    request.source,
                ))
                continue
            if len(actual) != 1:
                problems.append(self._problem(
                    "PDUR_ROUTING_GROUP_MULTIPLE_MEMBERSHIP",
                    f"目标 PduRDestPdu“{request.destination_path}”同时属于多个路由组："
                    f"{[(state.name, state.path) for state in actual]}。",
                    request.source,
                ))
                continue
            state = actual[0]
            if state.path != expected.path:
                problems.append(self._problem(
                    "PDUR_ROUTING_GROUP_CHANNEL_MISMATCH",
                    f"目标 PduRDestPdu“{request.destination_path}”实际属于“{state.name}”"
                    f"（{state.path}），但目标通道“{request.target_channel}”的主应用组为"
                    f"“{expected.name}”（{expected.path}）；工具不会自动迁移或纠正。",
                    request.source,
                ))
                continue
            operation = MutationOperation(
                MutationKind.PDUR_ROUTING_GROUP_MEMBERSHIP,
                state.path,
                "",
                self.adapter.member_reference_definition,
                action=MutationAction.REMOVE_REFERENCE,
                references=((self.adapter.member_reference_definition, request.destination_path),),
                source_locations=(request.source,),
            )
            operations[operation.identity] = operation

        removals_by_group = Counter(operation.parent_path for operation in operations.values())
        for state in states:
            if removals_by_group[state.path] and len(state.members) - removals_by_group[state.path] == 0:
                group_operations = tuple(
                    operation for operation in operations.values()
                    if operation.parent_path == state.path
                )
                source = group_operations[0].source_locations[0]
                targets = sorted(
                    operation.reference(self.adapter.member_reference_definition) or "<缺少目标>"
                    for operation in group_operations
                )
                problems.append(self._problem(
                    "PDUR_ROUTING_GROUP_WOULD_BE_EMPTY",
                    f"删除计划会使路由组“{state.name}”（{state.path}）成为空组；"
                    f"本批目标为 {targets}。工具不会删除路由组或保留空组输出。",
                    source,
                ))
        errors = any(not problem.warning for problem in problems)
        return RoutingGroupMembershipPlan(
            () if errors else tuple(operations.values()), tuple(problems),
        )

    def removal_referrers(
        self, operations: tuple[MutationOperation, ...],
    ) -> frozenset[etree._Element]:
        """返回计划移除的精确引用节点，供 DELETE 外部引用投影扣除。"""
        nodes: set[etree._Element] = set()
        for operation in operations:
            if operation.action is not MutationAction.REMOVE_REFERENCE:
                continue
            target = operation.reference(self.adapter.member_reference_definition)
            groups = self.index.find_by_path(operation.parent_path)
            if len(groups) != 1 or target is None:
                raise ArxmlStructureError("路由组成员删除操作无法唯一还原引用节点。")
            members = self.adapter.inspect_members(groups[0], self.document.namespace)
            matching = tuple(member.node for member in members if member.target_path == target)
            if len(matching) != 1:
                raise ArxmlStructureError(
                    f"路由组“{operation.parent_path}”到“{target}”的成员引用实际找到{len(matching)}个。"
                )
            nodes.add(matching[0])
        return frozenset(nodes)

    def apply_add(self, context: MutationContext, operation: MutationOperation) -> None:
        """在 DestPdu 已创建或确认后，最后插入成员引用。"""
        if operation.action is not MutationAction.ADD_REFERENCE:
            raise ArxmlStructureError("ADD 阶段收到非 ADD_REFERENCE 路由组操作。")
        target = operation.reference(self.adapter.member_reference_definition)
        if target is None:
            raise ArxmlStructureError("路由组成员 ADD 缺少目标 PduRDestPdu VALUE-REF。")
        context.node_at(target)
        group = context.node_at(operation.parent_path)
        members = self.adapter.inspect_members(group, context.namespace)
        if sum(member.target_path == target for member in members) > 1:
            raise ArxmlStructureError(f"路由组“{operation.parent_path}”存在重复成员“{target}”。")
        if any(member.target_path == target for member in members):
            return
        context.inserted.extend(self.adapter.insert_member(group, context.namespace, target))

    def apply_remove(self, operation: MutationOperation) -> None:
        """先删除精确成员引用；调用方随后才可删除 DestPdu。"""
        if operation.action is not MutationAction.REMOVE_REFERENCE:
            raise ArxmlStructureError("DELETE 阶段收到非 REMOVE_REFERENCE 路由组操作。")
        target = operation.reference(self.adapter.member_reference_definition)
        groups = self.index.find_by_path(operation.parent_path)
        if len(groups) != 1 or target is None:
            raise ArxmlStructureError("路由组成员 DELETE 缺少唯一组或目标 VALUE-REF。")
        members = self.adapter.inspect_members(groups[0], self.document.namespace)
        matching = tuple(member.node for member in members if member.target_path == target)
        if len(matching) != 1:
            raise ArxmlStructureError(
                f"应用 DELETE 时路由组“{operation.parent_path}”到“{target}”的引用实际找到"
                f"{len(matching)}个。"
            )
        parent = matching[0].getparent()
        if parent is None:
            raise ArxmlStructureError("路由组成员引用缺少 REFERENCE-VALUES 父节点。")
        parent.remove(matching[0])
