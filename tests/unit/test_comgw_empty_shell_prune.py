"""第二十六轮回归：克隆多信号模板时不得产出缺少 mandatory 引用的空壳子容器。

AUTOSAR 允许一个 ComGwDestination 挂多个 ComGwSignal。基线里存在这种模板时，
计划只需要其中一个信号，多余的引用条目被删除后其所在 ComGwSignal 子容器会变成
缺少 mandatory ComGwSignalRef 的空壳，产出结构非法的 ARXML；递归语义聚合只看
得到值数，语义比较与输出验证都不会报错。
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from lxml import etree

from davinci_gw.application.generate import generate_inputs
from davinci_gw.arxml.document import ArxmlDocument
from davinci_gw.modules import definitions as defs
from davinci_gw.modules.common import definition_ref, semantic_values
from tests.conftest import signal_row


def _messages(report: object) -> str:
    return "\n".join(issue.message for issue in report.all_issues)


def _add_second_signal(arxml: Path) -> Path:
    """把基线里的 ComGwDestination 模板改成挂两个 ComGwSignal。"""
    tree = etree.parse(str(arxml))
    namespace = etree.QName(tree.getroot()).namespace
    destination = next(
        node for node in tree.getroot().iter(f"{{{namespace}}}ECUC-CONTAINER-VALUE")
        if definition_ref(node, namespace) == defs.COM_GW_DEST
    )
    group = destination.find(f"{{{namespace}}}SUB-CONTAINERS")
    assert group is not None
    signal = next(
        child for child in group
        if definition_ref(child, namespace) == defs.COM_GW_DEST_SIGNAL
    )
    clone = deepcopy(signal)
    clone.find(f"{{{namespace}}}SHORT-NAME").text = "ComGwSignal2"
    group.append(clone)
    tree.write(str(arxml), encoding="UTF-8", xml_declaration=True, pretty_print=True)
    return arxml


def _assert_no_empty_shell(root: etree._Element, namespace: str) -> int:
    """断言所有 ComGwDestination 的 ComGwSignal 子容器都带 ComGwSignalRef。"""
    checked = 0
    for node in root.iter(f"{{{namespace}}}ECUC-CONTAINER-VALUE"):
        if definition_ref(node, namespace) != defs.COM_GW_DEST:
            continue
        checked += 1
        group = node.find(f"{{{namespace}}}SUB-CONTAINERS")
        children = (
            [child for child in list(group)
             if definition_ref(child, namespace) == defs.COM_GW_DEST_SIGNAL]
            if group is not None else []
        )
        for child in children:
            _, references = semantic_values(child, namespace)
            assert references.get(defs.COM_GW_DEST_SIGNAL_REF), (
                "ComGwSignal 子容器缺少 mandatory 的 ComGwSignalRef："
                f"{child.findtext(f'{{{namespace}}}SHORT-NAME')}"
            )
    return checked


def test_generated_destination_drops_empty_shell_from_multi_signal_template(
    workbook_factory: object, arxml_factory: object, tmp_path: Path,
) -> None:
    """多信号模板下新建的 ComGwDestination 不得留下空壳 ComGwSignal 子容器。"""
    baseline = _add_second_signal(arxml_factory())
    output = tmp_path / "signal.arxml"
    report = generate_inputs(
        workbook_factory(direct_rows=(), signal_rows=(signal_row(),)), baseline, output,
    )
    assert report.is_success, _messages(report)
    document = ArxmlDocument.load(output)
    assert _assert_no_empty_shell(document.tree.getroot(), document.namespace) >= 1


def test_multi_signal_template_clone_keeps_only_planned_signal(
    workbook_factory: object, arxml_factory: object, tmp_path: Path,
) -> None:
    """克隆多信号模板后只保留计划写入的信号引用，不继承基线的其它信号。"""
    baseline = _add_second_signal(arxml_factory())
    output = tmp_path / "signal.arxml"
    report = generate_inputs(
        workbook_factory(direct_rows=(), signal_rows=(signal_row(),)), baseline, output,
    )
    assert report.is_success, _messages(report)
    document = ArxmlDocument.load(output)
    planned = "/Cfg/Com/ComConfig/DST_SIG_oDST_MSG_oDST_Tx"
    written: list[tuple[str, ...]] = []
    for node in document.tree.getroot().iter(f"{{{document.namespace}}}ECUC-CONTAINER-VALUE"):
        if definition_ref(node, document.namespace) != defs.COM_GW_DEST:
            continue
        _, references = semantic_values(node, document.namespace, recursive=True)
        values = references.get(defs.COM_GW_DEST_SIGNAL_REF, ())
        if planned not in values:
            continue
        written.append(values)
        group = node.find(f"{{{document.namespace}}}SUB-CONTAINERS")
        children = (
            [child for child in list(group)
             if definition_ref(child, document.namespace) == defs.COM_GW_DEST_SIGNAL]
            if group is not None else []
        )
        assert len(children) == 1, (
            f"新建的 ComGwDestination 残留了模板里的多余 ComGwSignal 子容器：{len(children)} 个"
        )
    assert written, "未找到引用计划信号的 ComGwDestination"
    for values in written:
        assert values == (planned,), f"ComGwDestination 继承了基线的多余信号引用：{values}"
