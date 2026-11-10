"""ARXML 安全加载、发现、索引和写出测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
from lxml import etree

from davinci_gw.arxml import document as document_module
from davinci_gw.arxml.document import ArxmlDocument
from davinci_gw.domain.errors import ArxmlStructureError, OutputWriteError
from davinci_gw.modules import definitions as defs
from davinci_gw.modules.common import MutationContext, definition_ref


def test_namespace_schema_and_four_modules(arxml_factory: object) -> None:
    document = ArxmlDocument.load(arxml_factory())
    inspection = document.inspect()
    assert inspection.namespace_uri == "http://autosar.org/schema/r4.0"
    assert inspection.schema_filename == "AUTOSAR_00049.xsd"
    assert set(inspection.modules) == {"CanIf", "Com", "EcuC", "PduR"}
    assert inspection.modules["PduR"].definition_ref == "/MICROSAR/PduR"


def test_module_order_and_extra_module_do_not_matter(arxml_factory: object) -> None:
    document = ArxmlDocument.load(arxml_factory(
        order=("PduR", "EcuC", "CanIf", "Com"), extra=True))
    assert set(document.inspect().modules) == {"CanIf", "Com", "EcuC", "PduR"}


def test_missing_module_is_friendly(arxml_factory: object) -> None:
    source = arxml_factory(missing="PduR")
    with pytest.raises(ArxmlStructureError, match="未找到PduR模块") as captured:
        ArxmlDocument.load(source).inspect()
    assert str(source) in str(captured.value)


def test_duplicate_module_is_friendly(arxml_factory: object) -> None:
    with pytest.raises(ArxmlStructureError, match="多个CanIf模块"):
        ArxmlDocument.load(arxml_factory(duplicate="CanIf")).inspect()


def test_index_allows_duplicate_short_names(arxml_factory: object) -> None:
    index = ArxmlDocument.load(arxml_factory()).build_index()
    assert len(index.find_by_short_name("SharedName")) == 4


def test_definition_path_and_reverse_reference_indexes(arxml_factory: object) -> None:
    document = ArxmlDocument.load(arxml_factory())
    index = document.build_index()
    assert len(index.find_by_definition_ref("/MICROSAR/CanIf")) == 1
    assert len(index.find_by_path("/Cfg/CanIf")) == 1
    # PduR BswModuleRef 现在也参与应用组/诊断组结构分类，因此多一个精确定义引用。
    assert len(index.find_referrers("/Cfg/CanIf")) == 5
    assert index.reference_count("/Cfg/CanIf") == 5


def test_roundtrip_preserves_comment_unknown_and_attribute(
    arxml_factory: object, tmp_path: Path,
) -> None:
    source = arxml_factory()
    output = tmp_path / "copy.arxml"
    ArxmlDocument.load(source).write_atomic(output)
    raw = output.read_bytes()
    assert raw.startswith(b"<?xml")
    assert "必须保留的注释" in raw.decode("utf-8")
    assert b"VENDOR-UNKNOWN" in raw and b'VENDOR-EXT="keep"' in raw
    assert etree.parse(str(output)).getroot() is not None


def test_default_refuses_overwrite(arxml_factory: object, tmp_path: Path) -> None:
    output = tmp_path / "exists.arxml"
    output.write_text("existing", encoding="utf-8")
    with pytest.raises(OutputWriteError, match="已存在"):
        ArxmlDocument.load(arxml_factory()).write_atomic(output)
    assert output.read_text(encoding="utf-8") == "existing"


def test_file_appearing_during_write_is_not_clobbered(arxml_factory: object, tmp_path: Path) -> None:
    """写出期间冒出来的同名文件不能被静默覆盖。

    「默认不覆盖」不能只靠写出前的一次存在性检查：真正落盘前还要序列化
    并复核整个 ARXML，几十兆的文件要花上几秒，这期间同名文件完全可能已被
    别的进程或用户创建。此时必须失败，并且把对方的文件原样留下。
    """
    source = arxml_factory()
    output = tmp_path / "raced.arxml"

    def create_competing_file() -> None:
        output.write_text("别人的文件", encoding="utf-8")

    with pytest.raises(OutputWriteError, match="已存在"):
        ArxmlDocument.load(source).write_atomic(output, before_replace=create_competing_file)
    assert output.read_text(encoding="utf-8") == "别人的文件"


def test_exclusive_publish_leaves_no_temporary_file(arxml_factory: object, tmp_path: Path) -> None:
    """发布成功后目录里只应剩下目标文件。

    硬链接发布是"目标一出现就是完整内容"，临时名要跟着摘掉；漏掉的话
    用户会在输出目录里看到一个多余的 ``.xxx.tmp.arxml``。
    """
    source = arxml_factory()
    output = tmp_path / "clean.arxml"
    ArxmlDocument.load(source).write_atomic(output)
    assert sorted(item.name for item in tmp_path.iterdir()) == sorted([source.name, output.name])
    assert output.read_bytes().startswith(b"<?xml")


def test_exclusive_publish_without_hard_link_support(
    arxml_factory: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """文件系统不支持硬链接时，退路同样不许覆盖已有文件。"""

    def refuse_link(*args: object, **kwargs: object) -> None:
        raise OSError("该文件系统不支持硬链接")

    monkeypatch.setattr(document_module.os, "link", refuse_link)
    source = arxml_factory()
    output = tmp_path / "fallback.arxml"

    def create_competing_file() -> None:
        output.write_text("别人的文件", encoding="utf-8")

    with pytest.raises(OutputWriteError, match="已存在"):
        ArxmlDocument.load(source).write_atomic(output, before_replace=create_competing_file)
    assert output.read_text(encoding="utf-8") == "别人的文件"


def test_input_output_path_must_differ(arxml_factory: object) -> None:
    source = arxml_factory()
    with pytest.raises(OutputWriteError, match="不能与输入基准文件相同"):
        ArxmlDocument.load(source).write_atomic(source, overwrite=True)


def test_mutation_context_rolls_back_existing_parameter_text(arxml_factory: object) -> None:
    document = ArxmlDocument.load(arxml_factory())
    index = document.build_index()
    signal = index.find_by_path("/Cfg/Com/ComConfig/SRC_SIG_oSRC_MSG_oSRC_Rx")[0]
    access = next(
        node for node in signal.iter()
        if definition_ref(node, document.namespace) == defs.COM_SIGNAL_ACCESS
    )
    value = access.find(f"{{{document.namespace}}}VALUE")
    context = MutationContext(document.root, document.namespace, index)
    context.set_text(value, "ACCESS_NEEDED_BY_SWC_OR_COM")
    assert value.text == "ACCESS_NEEDED_BY_SWC_OR_COM"
    context.rollback()
    assert value.text == "ACCESS_UNCLEAR"


def test_failed_serialization_leaves_no_target_or_temp(
    arxml_factory: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import davinci_gw.arxml.document as document_module

    document = ArxmlDocument.load(arxml_factory())
    output = tmp_path / "failed.arxml"

    def fail(*args: object, **kwargs: object) -> None:
        raise OSError("simulated")

    monkeypatch.setattr(document_module, "_serialize_tree", fail)
    with pytest.raises(OutputWriteError, match="写出ARXML失败"):
        document.write_atomic(output)
    assert not output.exists()
    assert not list(tmp_path.glob(".*.tmp.arxml"))
