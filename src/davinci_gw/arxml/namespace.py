"""动态识别 AUTOSAR 命名空间、Schema 和命名空间标签。"""

from __future__ import annotations

from pathlib import PurePosixPath

from lxml import etree

XSI_NAMESPACE = "http://www.w3.org/2001/XMLSchema-instance"
SCHEMA_LOCATION_ATTRIBUTE = f"{{{XSI_NAMESPACE}}}schemaLocation"


def local_name(element_or_tag: etree._Element | str) -> str:
    """返回不带命名空间前缀的 XML 本地名。"""
    tag = element_or_tag.tag if hasattr(element_or_tag, "tag") else element_or_tag
    return etree.QName(tag).localname if isinstance(tag, str) else ""


def namespace_uri(root: etree._Element) -> str:
    """从根节点标签动态提取命名空间 URI。"""
    return etree.QName(root).namespace or ""


def qualified(namespace: str, name: str) -> str:
    """构造当前 AUTOSAR 命名空间中的完整标签。"""
    return f"{{{namespace}}}{name}" if namespace else name


def direct_child_text(element: etree._Element, namespace: str, name: str) -> str | None:
    """只读取直接子节点，避免把后代 SHORT-NAME 误认作当前对象名称。"""
    child = element.find(qualified(namespace, name))
    if child is None or child.text is None:
        return None
    text = child.text.strip()
    return text or None


def schema_details(root: etree._Element, namespace: str) -> tuple[str | None, str | None]:
    """读取 xsi:schemaLocation，并定位当前命名空间对应的 XSD 文件名。"""
    location = root.get(SCHEMA_LOCATION_ATTRIBUTE)
    if not location:
        return None, None
    tokens = location.split()
    schema_uri: str | None = None
    for index in range(0, len(tokens) - 1, 2):
        if tokens[index] == namespace:
            schema_uri = tokens[index + 1]
            break
    if schema_uri is None and len(tokens) == 1:
        schema_uri = tokens[0]
    filename = PurePosixPath(schema_uri.replace("\\", "/")).name if schema_uri else None
    return location, filename
