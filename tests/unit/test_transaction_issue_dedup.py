"""第二十八轮回归：DoIP 阶段合并时必须去掉与既有阶段重复的基线问题。

DELETE、ADD 与 DoIP 三个阶段各用各的只读索引服务，同一条基线问题可能被多个阶段
各发现一次。`_merge_plans` 已对 DELETE 与 ADD 的结果按完整可见身份去重，DoIP 阶段
的结果此前是直接拼接的，同一问题会在最终报告里出现两次。
"""

from __future__ import annotations

from davinci_gw.domain.models import MutationPlan, ValidationIssue, ValidationSeverity
from davinci_gw.routing.transaction import _deduplicate_issues, _merge_doip_plan


def _issue(message: str = "同一条基线一致性警告") -> ValidationIssue:
    return ValidationIssue(
        code="PDUR_ROUTING_GROUP_BASELINE_WARNING",
        message=message,
        severity=ValidationSeverity.WARNING,
    )


def test_deduplicate_issues_keeps_one_copy_per_visible_identity() -> None:
    """同一条问题出现多次时只保留一条，不同问题各自保留。"""
    issues = (_issue(), _issue(), _issue("另一条基线警告"))
    deduplicated = _deduplicate_issues(issues)
    assert [issue.message for issue in deduplicated] == [
        "同一条基线一致性警告", "另一条基线警告",
    ]


def test_merge_doip_plan_drops_issue_repeated_from_earlier_stage() -> None:
    """DoIP 阶段重复发现的基线问题不得在总报告里出现两次。"""
    combined = MutationPlan(issues=(_issue(),))
    doip_plan = MutationPlan(issues=(_issue(),))
    merged = _merge_doip_plan(combined, doip_plan)
    assert [issue.message for issue in merged.issues] == ["同一条基线一致性警告"]


def test_merge_doip_plan_keeps_distinct_issues() -> None:
    """DoIP 阶段自己的问题必须原样保留，不能被去重误伤。"""
    combined = MutationPlan(issues=(_issue(),))
    doip_plan = MutationPlan(issues=(_issue("DoIP 阶段自己的问题"),))
    merged = _merge_doip_plan(combined, doip_plan)
    assert [issue.message for issue in merged.issues] == [
        "同一条基线一致性警告", "DoIP 阶段自己的问题",
    ]
