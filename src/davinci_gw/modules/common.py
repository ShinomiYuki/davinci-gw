"""模块编辑器共享的克隆、语义比较、Handle ID、UUID和确定性插入工具。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from uuid import UUID, uuid5

from lxml import etree

from davinci_gw.arxml.index import ArxmlIndex, autosar_path
from davinci_gw.arxml.namespace import direct_child_text, local_name, qualified
from davinci_gw.domain.errors import ArxmlStructureError
from davinci_gw.domain.models import MutationKind, MutationOperation

UUID_NAMESPACE = UUID("9b41f4ce-62d8-5d2f-9127-897299f06d54")
TemplateKey = tuple[MutationKind, str, tuple[str, ...], tuple[str, ...]]


def definition_ref(node: etree._Element, namespace: str) -> str | None:
    """返回 ECUC 配置对象或参数对象的直接 DEFINITION-REF。"""
    return direct_child_text(node, namespace, "DEFINITION-REF")


def _value_text(entry: etree._Element) -> str | None:
    for child in entry:
        if not isinstance(child.tag, str):
            continue
        name = local_name(child)
        if name in {"VALUE", "VALUE-REF"}:
            return child.text.strip() if child.text else ""
    return None


def semantic_values(
    node: etree._Element, namespace: str, *, recursive: bool = False,
) -> tuple[dict[str, tuple[str, ...]], dict[str, tuple[str, ...]]]:
    """提取参数和引用的完整多值语义，避免字典覆盖重复定义。"""
    parameters: dict[str, list[str]] = {}
    references: dict[str, list[str]] = {}
    nodes = node.iter() if recursive else iter((node,))
    for owner in nodes:
        for group in owner:
            if not isinstance(group.tag, str):
                continue
            group_name = local_name(group)
            target = parameters if group_name == "PARAMETER-VALUES" else references if group_name == "REFERENCE-VALUES" else None
            if target is None:
                continue
            for entry in group:
                ref = definition_ref(entry, namespace)
                value = _value_text(entry)
                if ref and value is not None:
                    target.setdefault(ref, []).append(value)
    return (
        {key: tuple(values) for key, values in parameters.items()},
        {key: tuple(values) for key, values in references.items()},
    )


def operation_matches(
    node: etree._Element,
    operation: MutationOperation,
    namespace: str,
    *,
    ignore_parameters: frozenset[str] = frozenset(),
    recursive: bool = False,
) -> bool:
    """比较现有对象与计划的全部相关参数、引用和定义。"""
    if definition_ref(node, namespace) != operation.definition_ref:
        return False
    actual_parameters, actual_references = semantic_values(node, namespace, recursive=recursive)
    expected_parameters = {key: (value,) for key, value in operation.parameters}
    expected_references = {key: (value,) for key, value in operation.references}
    for key in ignore_parameters:
        actual_parameters.pop(key, None)
        expected_parameters.pop(key, None)
    return actual_parameters == expected_parameters and actual_references == expected_references


def find_templates(
    root: etree._Element, namespace: str, definition: str, *, index: ArxmlIndex | None = None,
) -> tuple[etree._Element, ...]:
    """按定义引用查找真实同类型 ECUC 容器模板。"""
    tag = qualified(namespace, "ECUC-CONTAINER-VALUE")
    if index is not None:
        return tuple(node for node in index.find_by_definition_ref(definition) if node.tag == tag)
    return tuple(node for node in root.iter(tag) if definition_ref(node, namespace) == definition)


def unique_template_parent_path(
    root: etree._Element, namespace: str, definition: str, *, index: ArxmlIndex | None = None,
) -> str:
    """从真实同类型兄弟推导唯一父容器路径。"""
    parents: set[etree._Element] = set()
    for node in find_templates(root, namespace, definition, index=index):
        group = node.getparent()
        parent = group.getparent() if group is not None else None
        if parent is not None:
            parents.add(parent)
    paths = {autosar_path(parent, namespace) for parent in parents}
    if len(paths) != 1:
        raise ArxmlStructureError(
            f"定义“{definition}”的父容器应唯一，实际找到{len(paths)}个候选：{sorted(paths)}。"
        )
    return paths.pop()


def inspect_operation(
    index: ArxmlIndex,
    namespace: str,
    operation: MutationOperation,
    *,
    ignore_parameters: frozenset[str] = frozenset(),
    recursive: bool = False,
) -> tuple[str, etree._Element | None]:
    """返回 MISSING、EXISTING 或 CONFLICT，不用 SHORT-NAME 全局唯一假设。"""
    found = index.find_by_path(operation.object_path)
    if not found:
        return "MISSING", None
    if len(found) != 1:
        return "CONFLICT", None
    node = found[0]
    if operation_matches(
        node, operation, namespace,
        ignore_parameters=ignore_parameters, recursive=recursive,
    ):
        return "EXISTING", node
    return "CONFLICT", node


def unique_named_node(
    index: ArxmlIndex, namespace: str, short_name: str, expected_definition: str,
) -> tuple[str, etree._Element | None]:
    """按 SHORT-NAME 查询后再以定义过滤，明确区分缺失和候选不唯一。"""
    found = tuple(node for node in index.find_by_short_name(short_name)
                  if definition_ref(node, namespace) == expected_definition)
    if not found:
        return "MISSING", None
    if len(found) != 1:
        return "AMBIGUOUS", None
    return "FOUND", found[0]


def select_template(
    root: etree._Element,
    namespace: str,
    operation: MutationOperation,
    *,
    index: ArxmlIndex | None = None,
    template_cache: dict[TemplateKey, etree._Element] | None = None,
) -> etree._Element:
    """选择包含计划所需定义的最小真实模板，找不到时拒绝猜测节点结构。"""
    recursive = operation.kind in {MutationKind.COM_GW_SOURCE, MutationKind.COM_GW_DESTINATION}
    required_params = {key for key, _ in operation.parameters}
    required_refs = {key for key, _ in operation.references}
    cache_key = (
        operation.kind, operation.definition_ref,
        tuple(sorted(required_params)), tuple(sorted(required_refs)),
    )
    if template_cache is not None and cache_key in template_cache:
        return template_cache[cache_key]
    candidates: list[tuple[int, etree._Element]] = []
    for node in find_templates(root, namespace, operation.definition_ref, index=index):
        parameters, references = semantic_values(node, namespace, recursive=recursive)
        if required_params <= parameters.keys() and required_refs <= references.keys():
            candidates.append((len(parameters) + len(references), node))
    if not candidates:
        raise ArxmlStructureError(
            f"基准ARXML中找不到可安全克隆的“{operation.definition_ref}”同类型模板，"
            "请先在DaVinci工程中保留一个同类型配置后重试。"
        )
    candidates.sort(key=lambda item: (item[0], autosar_path(item[1], namespace)))
    selected = candidates[0][1]
    if template_cache is not None:
        template_cache[cache_key] = selected
    return selected


def _filter_value_entries(
    node: etree._Element, namespace: str, parameter_defs: set[str], reference_defs: set[str],
) -> None:
    """删除模板中不属于新对象契约的残留参数和引用。"""
    for owner in node.iter():
        for group in list(owner):
            if not isinstance(group.tag, str):
                continue
            name = local_name(group)
            allowed = parameter_defs if name == "PARAMETER-VALUES" else reference_defs if name == "REFERENCE-VALUES" else None
            if allowed is None:
                continue
            for entry in list(group):
                if definition_ref(entry, namespace) not in allowed:
                    group.remove(entry)
            if len(group) == 0:
                owner.remove(group)


def _set_values(node: etree._Element, namespace: str, operation: MutationOperation) -> None:
    expected = dict(operation.parameters) | dict(operation.references)
    found: set[str] = set()
    for entry in node.iter():
        ref = definition_ref(entry, namespace)
        if ref not in expected:
            continue
        for child in entry:
            if isinstance(child.tag, str) and local_name(child) in {"VALUE", "VALUE-REF"}:
                child.text = expected[ref]
                found.add(ref)
                break
    missing = expected.keys() - found
    if missing:
        raise ArxmlStructureError(
            f"同类型模板缺少计划参数或引用：{', '.join(sorted(missing))}。"
        )


def deterministic_uuid(seed: str) -> str:
    """以固定命名空间生成可复现且可测试的 UUID5。"""
    return str(uuid5(UUID_NAMESPACE, f"davinci-gw:{seed}"))


def regenerate_uuids(node: etree._Element, object_path: str) -> None:
    """重建克隆子树中的全部 UUID，禁止保留任何模板 UUID。"""
    for index, current in enumerate(node.iter()):
        if current.get("UUID") is not None:
            current.set("UUID", deterministic_uuid(f"{object_path}:{index}:{local_name(current)}"))


def build_container(
    root: etree._Element, namespace: str, operation: MutationOperation, *,
    index: ArxmlIndex | None = None,
    template_cache: dict[TemplateKey, etree._Element] | None = None,
) -> etree._Element:
    """从真实模板深复制并只保留计划需要的内容，随后重建全部 UUID。"""
    template = select_template(
        root, namespace, operation, index=index, template_cache=template_cache,
    )
    node = deepcopy(template)
    short_name = node.find(qualified(namespace, "SHORT-NAME"))
    if short_name is None:
        raise ArxmlStructureError(f"模板“{operation.definition_ref}”缺少SHORT-NAME。")
    short_name.text = operation.short_name
    if operation.kind in {MutationKind.PDUR_ROUTING_PATH, MutationKind.COM_GW_MAPPING}:
        subcontainers = node.find(qualified(namespace, "SUB-CONTAINERS"))
        if subcontainers is not None:
            for child in list(subcontainers):
                subcontainers.remove(child)
    _filter_value_entries(
        node, namespace,
        {key for key, _ in operation.parameters},
        {key for key, _ in operation.references},
    )
    _set_values(node, namespace, operation)
    regenerate_uuids(node, operation.object_path)
    return node


def insert_deterministically(
    parent: etree._Element, node: etree._Element, namespace: str,
) -> None:
    """在同定义兄弟中按 SHORT-NAME 稳定插入，不重排已有节点。"""
    node_def = definition_ref(node, namespace)
    node_name = direct_child_text(node, namespace, "SHORT-NAME") or ""
    same_indices = [index for index, child in enumerate(parent)
                    if definition_ref(child, namespace) == node_def]
    for index in same_indices:
        child_name = direct_child_text(parent[index], namespace, "SHORT-NAME") or ""
        if child_name > node_name:
            parent.insert(index, node)
            return
    parent.insert((same_indices[-1] + 1) if same_indices else len(parent), node)


class HandleAllocator:
    """在单个参数定义作用域中按最小未占用非负整数稳定分配 Handle ID。"""

    def __init__(self, index: ArxmlIndex, namespace: str, parameter_definition: str) -> None:
        values: list[int] = []
        for node in index.find_by_definition_ref(parameter_definition):
            value = _value_text(node)
            try:
                values.append(int(value or ""))
            except ValueError as exc:
                raise ArxmlStructureError(
                    f"基准ARXML中的Handle ID“{value}”不是整数，定义为“{parameter_definition}”。"
                ) from exc
        # 真实 DaVinci 工程中会保留历史重复值（尤其是派生前的 0），这些值
        # 不是本工具制造的冲突。新增项仍从整个参数定义作用域选取未占用值，
        # 因而不会继续复用任何既有 Handle ID。
        self.used = set(values)

    def allocate(self) -> int:
        """返回最小未占用值，并立即在本次计划中保留。"""
        candidate = self.peek()
        self.used.add(candidate)
        return candidate

    def peek(self) -> int:
        """返回当前最小未占用值但不保留，供幂等探测操作使用。"""
        candidate = 0
        while candidate in self.used:
            candidate += 1
        return candidate


def duplicate_uuids(root: etree._Element) -> tuple[str, ...]:
    """返回树中重复 UUID，供诊断和测试使用。"""
    seen: set[str] = set()
    duplicates: set[str] = set()
    for node in root.iter():
        value = node.get("UUID")
        if not value:
            continue
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return tuple(sorted(duplicates))


@dataclass(slots=True)
class MutationContext:
    """一次应用过程共享的只读索引和新增节点登记表。"""

    root: etree._Element
    namespace: str
    index: ArxmlIndex
    template_cache: dict[TemplateKey, etree._Element] = field(default_factory=dict)
    created: dict[str, etree._Element] = field(default_factory=dict)
    inserted: list[etree._Element] = field(default_factory=list)

    def node_at(self, path: str) -> etree._Element:
        """唯一定位现有或本次刚创建的对象。"""
        if path in self.created:
            return self.created[path]
        found = self.index.find_by_path(path)
        if len(found) != 1:
            raise ArxmlStructureError(f"AUTOSAR路径“{path}”应唯一，实际找到{len(found)}个对象。")
        return found[0]

    def apply_container(self, operation: MutationOperation) -> etree._Element:
        """构造并插入一个计划容器，同时登记以供后续操作引用。"""
        parent_node = self.node_at(operation.parent_path)
        parent = parent_node.find(qualified(self.namespace, "SUB-CONTAINERS"))
        if parent is None:
            raise ArxmlStructureError(f"父对象“{operation.parent_path}”缺少SUB-CONTAINERS。")
        node = build_container(
            self.root, self.namespace, operation, index=self.index,
            template_cache=self.template_cache,
        )
        insert_deterministically(parent, node, self.namespace)
        self.created[operation.object_path] = node
        self.inserted.append(node)
        return node

    def rollback(self) -> None:
        """逆序移除本次插入节点，保证应用异常不会留下部分树修改。"""
        for node in reversed(self.inserted):
            parent = node.getparent()
            if parent is not None:
                parent.remove(node)
        self.inserted.clear()
        self.created.clear()
