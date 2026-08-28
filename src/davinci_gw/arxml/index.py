"""可重建的 SHORT-NAME、DEFINITION-REF、路径和反向引用索引。"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from lxml import etree

from .namespace import direct_child_text, local_name, namespace_uri


def autosar_path(element: etree._Element, namespace: str) -> str:
    """由祖先中可识别对象的直接 SHORT-NAME 构造无前缀 AUTOSAR 路径。"""
    parts: list[str] = []
    current: etree._Element | None = element
    while current is not None:
        name = direct_child_text(current, namespace, "SHORT-NAME")
        if name:
            parts.append(name)
        current = current.getparent()
    return "/" + "/".join(reversed(parts)) if parts else "/"


class ArxmlIndex:
    """面向查询的内存索引；修改 XML 树后应调用 :meth:`rebuild`。"""

    def __init__(self, root: etree._Element) -> None:
        self.root = root
        self.namespace = namespace_uri(root)
        self._by_short_name: dict[str, list[etree._Element]] = {}
        self._by_definition_ref: dict[str, list[etree._Element]] = {}
        self._by_path: dict[str, list[etree._Element]] = {}
        self._referrers: dict[str, list[etree._Element]] = {}
        self.rebuild()

    def rebuild(self) -> None:
        """从当前 XML 树重新构建全部索引，不依赖全局唯一 SHORT-NAME。"""
        short_names: defaultdict[str, list[etree._Element]] = defaultdict(list)
        definitions: defaultdict[str, list[etree._Element]] = defaultdict(list)
        paths: defaultdict[str, list[etree._Element]] = defaultdict(list)
        referrers: defaultdict[str, list[etree._Element]] = defaultdict(list)
        named_paths: dict[etree._Element, str] = {}
        for element in self.root.iter():
            if not isinstance(element.tag, str):
                continue
            short_name: str | None = None
            definition: str | None = None
            value_ref: str | None = None
            # 一次直接子节点遍历同时提取索引字段，避免对 81MB 树上的每个节点
            # 先 find(SHORT-NAME) 再重复遍历子节点。
            for child in element:
                if not isinstance(child.tag, str) or child.text is None:
                    continue
                text = child.text.strip()
                if not text:
                    continue
                name = local_name(child)
                if name == "SHORT-NAME":
                    short_name = text
                elif name == "DEFINITION-REF":
                    definition = text
                elif name == "VALUE-REF":
                    value_ref = text
            if short_name:
                short_names[short_name].append(element)
                # root.iter() 保证祖先先于后代出现；从最近的已命名祖先继承路径，
                # 避免每个对象都重新一路扫描到 AUTOSAR 根节点。
                parent = element.getparent()
                parent_path: str | None = None
                while parent is not None:
                    parent_path = named_paths.get(parent)
                    if parent_path is not None:
                        break
                    parent = parent.getparent()
                path = f"{parent_path}/{short_name}" if parent_path else f"/{short_name}"
                named_paths[element] = path
                paths[path].append(element)
            if definition is not None:
                definitions[definition].append(element)
            if value_ref is not None:
                referrers[value_ref].append(element)
        self._by_short_name = dict(short_names)
        self._by_definition_ref = dict(definitions)
        self._by_path = dict(paths)
        self._referrers = dict(referrers)

    @staticmethod
    def _result(values: Iterable[etree._Element] | None) -> tuple[etree._Element, ...]:
        return tuple(values or ())

    def find_by_short_name(self, short_name: str) -> tuple[etree._Element, ...]:
        """按 SHORT-NAME 查询，始终允许返回多个节点。"""
        return self._result(self._by_short_name.get(short_name))

    def find_by_definition_ref(self, definition_ref: str) -> tuple[etree._Element, ...]:
        """按直接 DEFINITION-REF 查询其所属配置对象。"""
        return self._result(self._by_definition_ref.get(definition_ref))

    def find_by_path(self, path: str) -> tuple[etree._Element, ...]:
        """按完整 AUTOSAR 路径查询；重复路径也不会被静默覆盖。"""
        normalized = "/" + path.strip("/") if path != "/" else "/"
        return self._result(self._by_path.get(normalized))

    def find_referrers(self, target_path: str) -> tuple[etree._Element, ...]:
        """返回 VALUE-REF 指向指定目标路径的引用对象。"""
        return self._result(self._referrers.get(target_path))

    def reference_count(self, target_path: str) -> int:
        """统计一个目标路径当前被多少 VALUE-REF 引用。"""
        return len(self._referrers.get(target_path, ()))
