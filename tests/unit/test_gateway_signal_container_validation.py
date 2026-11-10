"""第二十七轮回归：输出验证必须独立发现网关端点里的空壳信号子容器。

递归语义只聚合引用值数，给新建的 ComGwDestination 追加一个不含 ComGwSignalRef 的
ComGwSignal 子容器并不改变聚合值数，语义比较与其它检查都看不出来；只有逐个子容器
核对信号引用，才能拦住这种结构非法的 ARXML。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from lxml import etree

from davinci_gw.application.generate import generate_inputs
from davinci_gw.arxml.document import ArxmlDocument
from davinci_gw.domain.errors import OutputValidationError
from davinci_gw.modules import definitions as defs
from davinci_gw.modules.common import definition_ref
from davinci_gw.validation.output import validate_generated_output
from tests.conftest import signal_row


def _messages(report: object) -> str:
    return "\n".join(issue.message for issue in report.all_issues)


def _generated_gateway_endpoint(
    workbook_factory: object, arxml_factory: object, tmp_path: Path,
) -> tuple[object, ArxmlDocument, etree._Element]:
    """跑一次信号路由 ADD，返回计划、输出文档与新建的 ComGwDestination。"""
    output = tmp_path / "signal.arxml"
    report = generate_inputs(
        workbook_factory(direct_rows=(), signal_rows=(signal_row(),)), arxml_factory(), output,
    )
    assert report.is_success, _messages(report)
    document = ArxmlDocument.load(output)
    created = [
        node for node in document.tree.getroot().iter(
            f"{{{document.namespace}}}ECUC-CONTAINER-VALUE"
        )
        if definition_ref(node, document.namespace) == defs.COM_GW_DEST
        and (node.findtext(f"{{{document.namespace}}}SHORT-NAME") or "") != "ComGwDestination"
    ]
    assert len(created) == 1, f"应恰好新建一个 ComGwDestination，实际 {len(created)} 个"
    return report.plan, document, created[0]


def test_generated_gateway_endpoint_passes_signal_container_check(
    workbook_factory: object, arxml_factory: object, tmp_path: Path,
) -> None:
    """正常生成的网关端点里，每个信号子容器都恰好带一个信号引用。"""
    plan, document, destination = _generated_gateway_endpoint(
        workbook_factory, arxml_factory, tmp_path,
    )
    validate_generated_output(document, plan)
    group = destination.find(f"{{{document.namespace}}}SUB-CONTAINERS")
    children = [
        child for child in (list(group) if group is not None else [])
        if definition_ref(child, document.namespace) == defs.COM_GW_DEST_SIGNAL
    ]
    assert children, "新建的 ComGwDestination 应带 ComGwSignal 子容器"


def test_empty_signal_container_in_generated_endpoint_is_rejected(
    workbook_factory: object, arxml_factory: object, tmp_path: Path,
) -> None:
    """空壳 ComGwSignal 子容器不被语义比较发现，必须由输出验证拦下。"""
    plan, document, destination = _generated_gateway_endpoint(
        workbook_factory, arxml_factory, tmp_path,
    )
    namespace = document.namespace
    group = destination.find(f"{{{namespace}}}SUB-CONTAINERS")
    assert group is not None
    shell = etree.SubElement(group, f"{{{namespace}}}ECUC-CONTAINER-VALUE")
    etree.SubElement(shell, f"{{{namespace}}}SHORT-NAME").text = "ComGwSignal"
    etree.SubElement(shell, f"{{{namespace}}}DEFINITION-REF").text = defs.COM_GW_DEST_SIGNAL
    with pytest.raises(OutputValidationError) as error:
        validate_generated_output(document, plan)
    assert "0个信号引用" in str(error.value), str(error.value)
