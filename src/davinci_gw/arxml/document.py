"""安全加载、检查和原子写出完整 ARXML 文档。"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Any, Callable

from lxml import etree

from davinci_gw.domain.errors import ArxmlStructureError, OutputValidationError, OutputWriteError
from davinci_gw.domain.models import ArxmlInspectionResult, ArxmlModuleInfo

from .index import ArxmlIndex, autosar_path
from .namespace import direct_child_text, local_name, namespace_uri, schema_details

TARGET_MODULES = {
    "CanIf": "/MICROSAR/CanIf",
    "Com": "/MICROSAR/Com",
    "EcuC": "/MICROSAR/EcuC",
    "PduR": "/MICROSAR/PduR",
}
XML_DECLARATION_PATTERN = re.compile(br"^\s*<\?xml\s+([^?]+)\?>", re.IGNORECASE)
STANDALONE_PATTERN = re.compile(br"standalone\s*=\s*['\"](yes|no)['\"]", re.IGNORECASE)


def _serialize_tree(tree: etree._ElementTree, target: Path, **options: Any) -> None:
    """集中序列化调用，便于故障注入测试原子写出清理。"""
    tree.write(str(target), **options)


def _package_path(node: etree._Element, namespace: str) -> str:
    names: list[str] = []
    for ancestor in node.iterancestors():
        if local_name(ancestor) == "AR-PACKAGE":
            name = direct_child_text(ancestor, namespace, "SHORT-NAME")
            if name:
                names.append(name)
    return "/" + "/".join(reversed(names)) if names else "/"


class ArxmlDocument:
    """持有一次完整解析的 lxml 树和原始序列化元信息。"""

    def __init__(
        self, source_path: Path, tree: etree._ElementTree, *,
        has_xml_declaration: bool, standalone: bool | None,
    ) -> None:
        self.source_path = source_path
        self.tree = tree
        self.root = tree.getroot()
        self.namespace = namespace_uri(self.root)
        self.encoding = tree.docinfo.encoding or "UTF-8"
        self.has_xml_declaration = has_xml_declaration
        self.standalone = standalone
        self.doctype = tree.docinfo.doctype or None

    @classmethod
    def load(cls, source_path: str | Path) -> "ArxmlDocument":
        """使用禁用实体解析和网络访问的解析器加载 ARXML。"""
        path = Path(source_path).expanduser().resolve()
        with path.open("rb") as stream:
            prefix = stream.read(512)
        declaration = XML_DECLARATION_PATTERN.match(prefix)
        standalone_match = STANDALONE_PATTERN.search(declaration.group(1)) if declaration else None
        standalone = None if standalone_match is None else standalone_match.group(1).lower() == b"yes"
        parser = etree.XMLParser(
            resolve_entities=False,
            no_network=True,
            remove_blank_text=False,
            remove_comments=False,
            huge_tree=True,
        )
        tree = etree.parse(str(path), parser)
        return cls(path, tree, has_xml_declaration=declaration is not None, standalone=standalone)

    def _validate_root(self) -> None:
        if local_name(self.root) != "AUTOSAR" or not self.namespace:
            raise ArxmlStructureError(
                f"基准ARXML“{self.source_path}”的根节点不是带命名空间的AUTOSAR。"
                "请确认文件是完整的AUTOSAR工程导出文件后重试。"
            )

    def discover_modules(self) -> dict[str, ArxmlModuleInfo]:
        """结合节点类型、SHORT-NAME 和 DEFINITION-REF 发现四个必要模块。"""
        candidates: dict[str, list[etree._Element]] = {name: [] for name in TARGET_MODULES}
        for node in self.root.iter():
            if not isinstance(node.tag, str) or local_name(node) != "ECUC-MODULE-CONFIGURATION-VALUES":
                continue
            short_name = direct_child_text(node, self.namespace, "SHORT-NAME")
            definition_ref = direct_child_text(node, self.namespace, "DEFINITION-REF")
            if short_name in TARGET_MODULES and definition_ref == TARGET_MODULES[short_name]:
                candidates[short_name].append(node)
        modules: dict[str, ArxmlModuleInfo] = {}
        for name, expected_ref in TARGET_MODULES.items():
            found = candidates[name]
            if not found:
                raise ArxmlStructureError(
                    f"基准ARXML“{self.source_path}”中未找到{name}模块配置"
                    f"（期望DEFINITION-REF={expected_ref}）。"
                    "请确认导出的ARXML包含CanIf、Com、EcuC、PduR四个模块后重试。"
                )
            if len(found) > 1:
                raise ArxmlStructureError(
                    f"基准ARXML“{self.source_path}”中发现多个{name}模块配置，无法确定更新目标。"
                    "请保留唯一模块配置或重新导出完整ARXML后重试。"
                )
            node = found[0]
            modules[name] = ArxmlModuleInfo(
                short_name=name,
                definition_ref=expected_ref,
                autosar_path=autosar_path(node, self.namespace),
                package_path=_package_path(node, self.namespace),
                node=node,
            )
        return modules

    def inspect(self) -> ArxmlInspectionResult:
        """检查根、Schema 和四个模块并返回结构化结果。"""
        self._validate_root()
        schema_location, schema_filename = schema_details(self.root, self.namespace)
        if not schema_location:
            raise ArxmlStructureError(
                f"基准ARXML“{self.source_path}”根节点缺少xsi:schemaLocation，"
                "请重新导出包含Schema信息的完整ARXML后重试。"
            )
        return ArxmlInspectionResult(
            self.source_path, self.namespace, schema_location, schema_filename,
            self.discover_modules(),
        )

    def build_index(self) -> ArxmlIndex:
        """为当前树建立可重建的路径和引用索引。"""
        self._validate_root()
        return ArxmlIndex(self.root)

    def write_atomic(
        self,
        output_path: str | Path,
        overwrite: bool = False,
        validator: Callable[["ArxmlDocument"], None] | None = None,
    ) -> Path:
        """同目录临时序列化、复核新增内容后再原子替换为目标文件。"""
        output = Path(output_path).expanduser().resolve()
        if output == self.source_path:
            raise OutputWriteError("输出路径不能与输入基准文件相同，请选择新的输出文件。")
        if output.exists() and not overwrite:
            raise OutputWriteError(f"输出文件“{output}”已存在；默认不覆盖，请更换路径或显式允许覆盖。")
        output.parent.mkdir(parents=True, exist_ok=True)
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=output.parent, prefix=f".{output.name}.",
                suffix=".tmp.arxml", delete=False,
            ) as temporary:
                temp_path = Path(temporary.name)
            options: dict[str, Any] = {
                "encoding": self.encoding,
                "xml_declaration": self.has_xml_declaration,
                "pretty_print": False,
            }
            if self.standalone is not None:
                options["standalone"] = self.standalone
            if self.doctype:
                options["doctype"] = self.doctype
            _serialize_tree(self.tree, temp_path, **options)
            with temp_path.open("rb+") as stream:
                stream.flush()
                os.fsync(stream.fileno())
            temporary_document = ArxmlDocument.load(temp_path)
            temporary_document.inspect()
            if validator is not None:
                validator(temporary_document)
            os.replace(temp_path, output)
            temp_path = None
            return output
        except (OutputWriteError, OutputValidationError):
            raise
        except Exception as exc:
            raise OutputWriteError(
                f"写出ARXML失败，目标文件“{output}”未生成完整结果。请检查输出目录权限和磁盘空间后重试。"
            ) from exc
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass
