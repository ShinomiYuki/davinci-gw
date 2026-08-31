"""PduR 路由组成员关系的识别、预检和引用级增量修改。"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from typing import Protocol

from lxml import etree

from davinci_gw.arxml.document import ArxmlDocument
from davinci_gw.arxml.index import ArxmlIndex, autosar_path
from davinci_gw.arxml.namespace import direct_child_text, local_name, qualified
from davinci_gw.domain.errors import ArxmlStructureError
from davinci_gw.domain.models import (
    MutationAction,
    MutationKind,
    MutationOperation,
    SourceLocation,
)
from davinci_gw.modules import definitions as defs
from davinci_gw.modules.common import MutationContext, definition_ref


@dataclass(frozen=True, slots=True)
class RoutingGroupMembershipRequest:
    """一条已规范化路由腿的成员关系请求，不包含 Excel 原始字符串。"""

    group_names: tuple[str, ...]
    destination_path: str
    source: SourceLocation
    destination_exists: bool = True


@dataclass(frozen=True, slots=True)
class RoutingGroupMembershipProblem:
    """由协调器转换为带工作簿路径和路由身份的 ValidationIssue。"""

    code: str
    message: str
    source: SourceLocation


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
    ) -> None:
        self.document = document
        self.index = index or document.build_index()
        self.adapter = adapter or MicrosarRoutingGroupAdapter()

    @staticmethod
    def _problem(
        code: str, message: str, source: SourceLocation,
    ) -> RoutingGroupMembershipProblem:
        return RoutingGroupMembershipProblem(code, message, source)

    @staticmethod
    def _for_destination(
        problems: tuple[RoutingGroupMembershipProblem, ...], destination_path: str,
    ) -> tuple[RoutingGroupMembershipProblem, ...]:
        """让所有成员关系错误都携带可直接检索的 DestPdu 完整路径。"""
        return tuple(
            problem if destination_path in problem.message else replace(
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

    def _named_group(
        self, name: str, source: SourceLocation,
    ) -> tuple[_GroupState | None, tuple[RoutingGroupMembershipProblem, ...]]:
        named = self.index.find_by_short_name(name)
        candidates = tuple(
            node for node in named
            if definition_ref(node, self.document.namespace) == self.adapter.group_definition
        )
        if not candidates:
            actual_definitions = sorted({
                definition_ref(node, self.document.namespace) or "<缺少 DEFINITION-REF>"
                for node in named
            })
            if named:
                return None, (self._problem(
                    "PDUR_ROUTING_GROUP_MODEL_UNSUPPORTED",
                    f"名称“{name}”存在对象，但定义为 {actual_definitions}，不符合当前支持的"
                    f"“{self.adapter.group_definition}”模型。",
                    source,
                ),)
            return None, (self._problem(
                "PDUR_ROUTING_GROUP_NOT_FOUND",
                f"指定路由组“{name}”不存在；工具不会按目标网段猜测或自动创建路由组。",
                source,
            ),)
        if len(candidates) != 1:
            paths = sorted(autosar_path(node, self.document.namespace) for node in candidates)
            return None, (self._problem(
                "PDUR_ROUTING_GROUP_AMBIGUOUS",
                f"指定路由组“{name}”存在 {len(candidates)} 个候选：{paths}。",
                source,
            ),)
        return self._inspect_group(candidates[0], source)

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
        operations: list[MutationOperation] = []
        problems: list[RoutingGroupMembershipProblem] = []
        for name in request.group_names:
            state, current = self._named_group(name, request.source)
            problems.extend(self._for_destination(current, request.destination_path))
            if state is None:
                continue
            matching = tuple(
                member for member in state.members
                if member.target_path == request.destination_path
            )
            if matching:
                continue
            operations.append(MutationOperation(
                MutationKind.PDUR_ROUTING_GROUP_MEMBERSHIP,
                state.path,
                "",
                self.adapter.member_reference_definition,
                action=MutationAction.ADD_REFERENCE,
                references=((self.adapter.member_reference_definition, request.destination_path),),
                source_locations=(request.source,),
            ))
        return RoutingGroupMembershipPlan(
            () if problems else tuple(operations), tuple(problems),
        )

    def plan_delete(
        self, requests: tuple[RoutingGroupMembershipRequest, ...],
    ) -> RoutingGroupMembershipPlan:
        if not requests:
            return RoutingGroupMembershipPlan()
        states, problems_tuple = self._all_groups(requests[0].source)
        problems = list(self._for_destination(
            problems_tuple, requests[0].destination_path,
        ))
        by_name = {state.name: state for state in states}
        operations: dict[tuple[object, ...], MutationOperation] = {}
        for request in requests:
            for name in request.group_names:
                if name not in by_name:
                    state, current = self._named_group(name, request.source)
                    problems.extend(self._for_destination(current, request.destination_path))
                    if state is not None:
                        by_name[name] = state
            actual_names = {
                state.name for state in states
                if any(member.target_path == request.destination_path for member in state.members)
            }
            if not request.destination_exists:
                if actual_names:
                    problems.append(self._problem(
                        "PDUR_ROUTING_GROUP_MEMBER_DANGLING",
                        f"目标 PduRDestPdu“{request.destination_path}”已不存在，但路由组"
                        f" {sorted(actual_names)} 仍引用该路径。",
                        request.source,
                    ))
                continue
            declared = set(request.group_names)
            missing = sorted(declared - actual_names)
            if missing:
                problems.append(self._problem(
                    "PDUR_ROUTING_GROUP_MEMBER_NOT_FOUND",
                    f"目标 PduRDestPdu“{request.destination_path}”在指定路由组 {missing} 中没有成员引用；"
                    "路由仍存在时不能把该不一致视为幂等成功。",
                    request.source,
                ))
            remaining = sorted(actual_names - declared)
            if remaining:
                problems.append(self._problem(
                    "PDUR_ROUTING_GROUP_UNDECLARED_MEMBERSHIP",
                    f"目标 PduRDestPdu“{request.destination_path}”还属于未在本行声明的路由组"
                    f" {remaining}；不得删除该目标腿。",
                    request.source,
                ))
            for name in sorted(declared & actual_names):
                state = by_name[name]
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
        return RoutingGroupMembershipPlan(
            () if problems else tuple(operations.values()), tuple(problems),
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
