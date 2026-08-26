"""引用关系基础数据结构；实际路由删除将在后续轮次实现。"""

from __future__ import annotations

from dataclasses import dataclass

from lxml import etree


@dataclass(frozen=True, slots=True)
class ReferenceLink:
    """一条 VALUE-REF 反向引用关系。"""

    target_path: str
    owner: etree._Element
    value_ref: etree._Element
