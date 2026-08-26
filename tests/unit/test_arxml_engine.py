"""ARXML 安全加载、发现、索引和写出测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
from lxml import etree

from davinci_gw.arxml.document import ArxmlDocument
from davinci_gw.domain.errors import ArxmlStructureError, OutputWriteError


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
    assert len(index.find_referrers("/Cfg/CanIf")) == 4
    assert index.reference_count("/Cfg/CanIf") == 4


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


def test_input_output_path_must_differ(arxml_factory: object) -> None:
    source = arxml_factory()
    with pytest.raises(OutputWriteError, match="不能与输入基准文件相同"):
        ArxmlDocument.load(source).write_atomic(source, overwrite=True)


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
