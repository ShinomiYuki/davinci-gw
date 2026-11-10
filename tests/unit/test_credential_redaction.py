"""凭据脱敏的公共出口：问题表、详情弹窗与 MCP 响应共用同一套判定。"""

from __future__ import annotations

from pathlib import Path

from davinci_gw.application.public_mapping import issue_to_dto
from davinci_gw.domain.models import ValidationIssue
from davinci_gw.security.redaction import REDACTED, is_sensitive_name, redact_value


def test_redact_value_keeps_business_values_and_removes_credentials() -> None:
    assert redact_value("报文名 CanIfMsg") == "报文名 CanIfMsg"
    assert redact_value("0x1A2") == "0x1A2"
    assert redact_value("/ARXML/Path/To/Object") == "/ARXML/Path/To/Object"
    # 键值形态
    assert REDACTED in redact_value("api_token=abc123")
    assert REDACTED in redact_value('{"api_token": "abc123"}')
    # URL 用户信息
    assert "pass" not in redact_value("https://user:pass@host/mcp")
    # 裸凭据头
    assert "abc123" not in redact_value("Authorization: Bearer abc123")


def test_sensitive_name_layers_avoid_false_positives() -> None:
    assert is_sensitive_name("--api-token")
    assert is_sensitive_name("x-api-key")
    assert is_sensitive_name("cookie")
    assert not is_sensitive_name("cookie_recipe")
    assert not is_sensitive_name("session_count")
    assert not is_sensitive_name("key_recipe")
    assert not is_sensitive_name("api_url")


def test_issue_actual_value_is_redacted_at_the_public_boundary() -> None:
    """问题表的实际值可能来自工作簿单元格，凭据形态不能出现在对外 DTO 里。"""
    issue = ValidationIssue(
        code="INPUT_MISMATCH",
        message="取值不匹配",
        file_path=Path("C:/input/config.xlsx"),
        actual_value="Authorization: Bearer sk-live-SECRETVALUE",
    )
    dto = issue_to_dto(issue)
    assert "SECRETVALUE" not in str(dto.actual_value)
    assert REDACTED in str(dto.actual_value)


def test_issue_actual_value_business_text_is_untouched() -> None:
    issue = ValidationIssue(
        code="INPUT_MISMATCH",
        message="取值不匹配",
        actual_value="CanIf_TransmitMsg",
    )
    assert issue_to_dto(issue).actual_value == "CanIf_TransmitMsg"


def test_nested_actual_values_are_redacted() -> None:
    issue = ValidationIssue(
        code="INPUT_MISMATCH",
        message="取值不匹配",
        actual_value={"header": "Cookie: session=abc123", "name": "CanIf_TransmitMsg"},
    )
    rendered = str(issue_to_dto(issue).actual_value)
    assert "abc123" not in rendered
    assert "CanIf_TransmitMsg" in rendered
