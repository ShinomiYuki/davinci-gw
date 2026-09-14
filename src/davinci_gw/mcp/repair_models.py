"""MCP 自动诊断、热修复与提交阶段使用的内部状态模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any


def utc_now_text() -> str:
    """返回便于持久展示的 UTC 时间。"""
    return datetime.now(UTC).isoformat()


class FailureKind(str, Enum):
    """生成问题的稳定分类；不确定时禁止猜成工具 BUG。"""

    NONE = "NONE"
    DBC_MISSING = "DBC_MISSING"
    INPUT_PROBLEM = "INPUT_PROBLEM"
    BASELINE_PROBLEM = "BASELINE_PROBLEM"
    TOOL_BUG = "TOOL_BUG"
    UNDETERMINED = "UNDETERMINED"


class RepairState(str, Enum):
    """一次热修复从等待授权到交付的有限状态。"""

    QUEUED = "QUEUED"
    PREPARING_WORKTREE = "PREPARING_WORKTREE"
    ANALYZING = "ANALYZING"
    IMPLEMENTING = "IMPLEMENTING"
    REVIEWING = "REVIEWING"
    FIXING_REVIEW = "FIXING_REVIEW"
    REGRESSION = "REGRESSION"
    VERIFYING_ORIGINAL_INPUT = "VERIFYING_ORIGINAL_INPUT"
    BUILDING_CANDIDATE = "BUILDING_CANDIDATE"
    AWAITING_SUBMISSION = "AWAITING_SUBMISSION"
    SUBMITTING = "SUBMITTING"
    COMPLETED = "COMPLETED"
    NEEDS_ATTENTION = "NEEDS_ATTENTION"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

    @property
    def terminal(self) -> bool:
        return self in {self.COMPLETED, self.NEEDS_ATTENTION, self.FAILED, self.CANCELLED}


@dataclass(frozen=True, slots=True)
class AgentDiagnosis:
    """Codex 只读调查返回的结构化结论。"""

    kind: FailureKind
    summary: str
    evidence: tuple[str, ...] = ()
    code_evidence: tuple[str, ...] = ()
    reproduced: bool = False


@dataclass(frozen=True, slots=True)
class DiagnosisRecord:
    """第一次审批之前保存在当前 MCP 进程内的诊断快照。"""

    diagnosis_id: str
    repository_path: Path
    config_path: Path
    baseline_path: Path
    python_path: Path
    kind: FailureKind
    summary: str
    evidence: tuple[str, ...]
    code_evidence: tuple[str, ...]
    reproduced: bool
    issues: tuple[dict[str, Any], ...]
    repair_eligible: bool
    generation_can_continue: bool
    created_at: str = field(default_factory=utc_now_text)

    def to_payload(self) -> dict[str, Any]:
        """转换为 MCP 可直接有界化的公开负载。"""
        return {
            "diagnosis_id": self.diagnosis_id,
            "classification": self.kind.value,
            "summary": self.summary,
            "evidence": list(self.evidence),
            "code_evidence": list(self.code_evidence),
            "reproduced": self.reproduced,
            "repair_eligible": self.repair_eligible,
            "generation_can_continue": self.generation_can_continue,
            "created_at": self.created_at,
        }


@dataclass(frozen=True, slots=True)
class AgentRepairResult:
    """修复代理完成修改和只读审查后的可验证摘要。"""

    implementation_summary: str
    targeted_tests: tuple[str, ...]
    review_summary: str
    review_findings: tuple[str, ...] = ()
    codex_thread_id: str | None = None


@dataclass(slots=True)
class RepairRecord:
    """后台热修复任务的可变运行状态。"""

    repair_id: str
    diagnosis: DiagnosisRecord
    base_commit: str
    branch_name: str
    worktree_path: Path
    session_path: Path
    lock_path: Path | None = None
    state: RepairState = RepairState.QUEUED
    stage: str = "等待后台任务启动"
    progress: int = 0
    created_at: str = field(default_factory=utc_now_text)
    updated_at: str = field(default_factory=utc_now_text)
    implementation_summary: str | None = None
    review_summary: str | None = None
    review_findings: tuple[str, ...] = ()
    targeted_tests: tuple[str, ...] = ()
    full_regression: str | None = None
    original_input_verification: str | None = None
    candidate_archive: Path | None = None
    verified_output: Path | None = None
    codex_thread_id: str | None = None
    commit_hash: str | None = None
    submission: dict[str, Any] | None = None
    error: str | None = None

    def update(self, state: RepairState, stage: str, progress: int) -> None:
        self.state = state
        self.stage = stage
        self.progress = max(0, min(100, progress))
        self.updated_at = utc_now_text()

    def to_payload(self) -> dict[str, Any]:
        """隐藏内部任务和输入正文，仅返回用户可核对的状态。"""
        return {
            "repair_id": self.repair_id,
            "diagnosis_id": self.diagnosis.diagnosis_id,
            "state": self.state.value,
            "stage": self.stage,
            "progress": self.progress,
            "branch_name": self.branch_name,
            "worktree_path": str(self.worktree_path),
            "base_commit": self.base_commit,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "implementation_summary": self.implementation_summary,
            "review_summary": self.review_summary,
            "review_findings": list(self.review_findings),
            "targeted_tests": list(self.targeted_tests),
            "full_regression": self.full_regression,
            "original_input_verification": self.original_input_verification,
            "candidate_archive": str(self.candidate_archive) if self.candidate_archive else None,
            "verified_output": str(self.verified_output) if self.verified_output else None,
            "codex_thread_id": self.codex_thread_id,
            "commit_hash": self.commit_hash,
            "submission": self.submission,
            "error": self.error,
        }
