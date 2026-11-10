"""Facade 预处理阶段：输入不可读必须给出可读提示，不得降级为内部失败。"""

from dataclasses import replace

import pytest

from davinci_gw.application import facade as facade_module
from davinci_gw.application import generate as generate_module
from davinci_gw.application.facade import GatewayFacade
from davinci_gw.contracts import OperationStatus, UpdateRequestDto


def _request(config, baseline):
    return UpdateRequestDto(str(config), str(baseline))


def _codes(prepared) -> list[str]:
    return [issue.code for issue in prepared.issues]


def test_prepare_keeps_readable_issue_when_fingerprint_cannot_read_input(
    workbook_factory, arxml_factory, monkeypatch,
):
    """指纹读不到输入时不得报成 INTERNAL_FAILURE，要给出可诊断的 INPUT_FILE_DAMAGED。"""

    def unreadable(path):
        raise PermissionError(13, "拒绝访问")

    monkeypatch.setattr(facade_module, "fingerprint_file", unreadable)

    prepared = GatewayFacade().prepare(_request(
        workbook_factory(signal_rows=()), arxml_factory(),
    ))

    assert prepared.status is OperationStatus.VALIDATION_FAILED
    assert "INPUT_FILE_DAMAGED" in _codes(prepared)
    assert "INTERNAL_FAILURE" not in _codes(prepared)
    assert any("无法读取" in issue.message for issue in prepared.issues)


def test_prepare_keeps_readable_issue_when_config_table_cannot_be_read(
    workbook_factory, arxml_factory, monkeypatch,
):
    """输入校验层读不到配置表时，公共入口要给出与命令行一致的 INPUT_FILE_DAMAGED。"""

    def unreadable(path):
        raise PermissionError(13, "拒绝访问")

    def unreadable_workbook(path):
        raise PermissionError(13, "拒绝访问")

    monkeypatch.setattr(facade_module, "fingerprint_file", unreadable)
    monkeypatch.setattr(generate_module, "read_workbook", unreadable_workbook)

    prepared = GatewayFacade().prepare(_request(
        workbook_factory(signal_rows=()), arxml_factory(),
    ))

    assert prepared.status is OperationStatus.VALIDATION_FAILED
    assert "INPUT_FILE_DAMAGED" in _codes(prepared)
    assert "INTERNAL_FAILURE" not in _codes(prepared)


def test_prepare_still_reports_changed_input_when_fingerprint_differs(
    workbook_factory, arxml_factory, monkeypatch,
):
    """可读输入在预处理期间发生变化时仍必须报 INPUT_CHANGED，不能被上面的放宽吞掉。"""
    real = facade_module.fingerprint_file
    calls = {"count": 0}

    def changing(path):
        calls["count"] += 1
        result = real(path)
        # 第三次调用是复核阶段的配置表指纹，与初始指纹不同即输入已变化。
        return replace(result, sha256="changed") if calls["count"] == 3 else result

    monkeypatch.setattr(facade_module, "fingerprint_file", changing)

    prepared = GatewayFacade().prepare(_request(
        workbook_factory(signal_rows=()), arxml_factory(),
    ))

    assert prepared.status is OperationStatus.INPUT_CHANGED
    assert _codes(prepared) == ["INPUT_CHANGED"]


def test_prepare_does_not_report_changed_input_when_first_fingerprint_failed(
    workbook_factory, arxml_factory, monkeypatch,
):
    """初始指纹读不到、后续读得到时不得误报“输入已变化”。

    放宽不可读场景时不能把“无法比较”当成“比较结果不同”：没有初始指纹就
    放弃指纹复核，而不是拿空值去和最终指纹比对。
    """
    real = facade_module.fingerprint_file
    state = {"failed": False}

    def flaky(path):
        # 只有第一次（初始指纹）失败，复核阶段照常读取。
        if not state["failed"]:
            state["failed"] = True
            raise PermissionError(13, "拒绝访问")
        return real(path)

    monkeypatch.setattr(facade_module, "fingerprint_file", flaky)

    prepared = GatewayFacade().prepare(_request(
        workbook_factory(signal_rows=()), arxml_factory(),
    ))

    assert prepared.status is OperationStatus.SUCCESS
    assert "INPUT_CHANGED" not in _codes(prepared)
