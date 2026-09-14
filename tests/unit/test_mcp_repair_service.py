"""MCP 故障分类、双审批和隔离修复状态测试。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from davinci_gw.contracts import OperationStatus
from davinci_gw.mcp.repair_models import (
    AgentDiagnosis,
    AgentRepairResult,
    DiagnosisRecord,
    FailureKind,
    RepairState,
)
from davinci_gw.mcp.repair_service import (
    CANCEL_CONFIRMATION,
    START_CONFIRMATION,
    SUBMIT_CONFIRMATION,
    RepairContractError,
    RepairCoordinator,
)


class _Issue:
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "severity": "ERROR"}


class _Facade:
    def __init__(self, status: OperationStatus, issues: tuple[_Issue, ...] = ()) -> None:
        self.result = SimpleNamespace(status=status, issues=issues, preview=None)

    def preview(self, _request: object) -> object:
        return self.result


class _Agent:
    def __init__(self, diagnosis: AgentDiagnosis) -> None:
        self.diagnosis = diagnosis
        self.calls = 0

    async def diagnose(self, *_args: object) -> AgentDiagnosis:
        self.calls += 1
        return self.diagnosis

    async def repair(self, *_args: object, **_kwargs: object) -> object:
        raise AssertionError("此测试不应执行真实修复")


class _RepairingAgent(_Agent):
    async def repair(self, worktree: Path, *_args: object, **_kwargs: object) -> AgentRepairResult:
        source = worktree / "src"
        source.mkdir(parents=True, exist_ok=True)
        (source / "fix.py").write_text("FIX = 1\n", encoding="utf-8")
        return AgentRepairResult(
            "完成最小修复", ("pytest tests/unit/test_fix.py",), "完整 diff 无剩余问题", (), "thread-id",
        )


def _inputs(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    config = tmp_path / "routes.xlsx"
    baseline = tmp_path / "baseline.arxml"
    config.write_bytes(b"xlsx")
    baseline.write_text("<AUTOSAR/>", encoding="utf-8")
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / ".git").mkdir()
    (repository / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    return config, baseline, repository, Path(sys.executable)


def _diagnose(
    tmp_path: Path,
    facade: _Facade,
    agent: _Agent,
) -> tuple[RepairCoordinator, DiagnosisRecord]:
    config, baseline, repository, python = _inputs(tmp_path)
    coordinator = RepairCoordinator(facade=facade, agent=agent, session_root=tmp_path / "sessions")  # type: ignore[arg-type]
    record = asyncio.run(coordinator.diagnose(
        str(config), str(baseline), str(repository), str(python),
    ))
    return coordinator, record


def test_session_root_prefers_localappdata_without_evaluating_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    local_app_data = tmp_path / "local-app-data"
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))

    def unexpected_home() -> Path:
        raise AssertionError("已有 LOCALAPPDATA 时不应读取 HOME/USERPROFILE")

    monkeypatch.setattr(Path, "home", unexpected_home)
    coordinator = RepairCoordinator(
        facade=_Facade(OperationStatus.SUCCESS),
        agent=_Agent(AgentDiagnosis(FailureKind.NONE, "无问题")),
    )  # type: ignore[arg-type]
    assert coordinator.session_root == (local_app_data / "DaVinciGW" / "repair-sessions").resolve()


def test_success_and_dbc_skip_never_enter_auto_repair(tmp_path: Path) -> None:
    agent = _Agent(AgentDiagnosis(FailureKind.TOOL_BUG, "不应调用"))
    _, success = _diagnose(tmp_path / "success", _Facade(OperationStatus.SUCCESS), agent)
    assert success.kind is FailureKind.NONE
    assert success.generation_can_continue is True
    assert success.repair_eligible is False
    assert agent.calls == 0

    _, dbc = _diagnose(
        tmp_path / "dbc",
        _Facade(OperationStatus.SUCCESS, (_Issue("SIGNAL_SOURCE_MISSING", "DBC 未导入，已跳过该行"),)),
        agent,
    )
    assert dbc.kind is FailureKind.DBC_MISSING
    assert dbc.generation_can_continue is True
    assert dbc.repair_eligible is False
    assert agent.calls == 0


def test_input_and_baseline_failures_do_not_call_codex(tmp_path: Path) -> None:
    agent = _Agent(AgentDiagnosis(FailureKind.TOOL_BUG, "不应调用"))
    _, input_record = _diagnose(
        tmp_path / "input",
        _Facade(OperationStatus.VALIDATION_FAILED, (_Issue("WORKBOOK_HEADER_MISSING", "第2行缺少字段"),)),
        agent,
    )
    assert input_record.kind is FailureKind.INPUT_PROBLEM
    assert input_record.repair_eligible is False

    _, baseline_record = _diagnose(
        tmp_path / "baseline",
        _Facade(OperationStatus.VALIDATION_FAILED, (_Issue("ARXML_STRUCTURE_INVALID", "引用路径无效"),)),
        agent,
    )
    assert baseline_record.kind is FailureKind.BASELINE_PROBLEM
    assert baseline_record.repair_eligible is False
    assert agent.calls == 0


def test_tool_bug_requires_reproduction_and_code_evidence(tmp_path: Path) -> None:
    weak = _Agent(AgentDiagnosis(FailureKind.TOOL_BUG, "猜测", ("失败",), (), True))
    _, weak_record = _diagnose(
        tmp_path / "weak", _Facade(OperationStatus.VALIDATION_FAILED, (_Issue("PDUR_FAILED", "失败"),)), weak,
    )
    assert weak_record.kind is FailureKind.UNDETERMINED
    assert weak_record.repair_eligible is False

    proven = _Agent(AgentDiagnosis(
        FailureKind.TOOL_BUG, "已证实", ("原始输入稳定失败",), ("src/example.py:42 条件错误",), True,
    ))
    _, proven_record = _diagnose(
        tmp_path / "proven",
        _Facade(OperationStatus.VALIDATION_FAILED, (_Issue("PDUR_FAILED", "失败"),)), proven,
    )
    assert proven_record.kind is FailureKind.TOOL_BUG
    assert proven_record.repair_eligible is True


def test_start_requires_first_confirmation_and_recursive_start_is_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    coordinator, diagnosis = _diagnose(
        tmp_path,
        _Facade(OperationStatus.VALIDATION_FAILED, (_Issue("PDUR_FAILED", "失败"),)),
        _Agent(AgentDiagnosis(
            FailureKind.TOOL_BUG, "已证实", ("复现",), ("src/example.py:1",), True,
        )),
    )
    with pytest.raises(RepairContractError, match="确认语"):
        asyncio.run(coordinator.start(diagnosis.diagnosis_id, "同意"))
    assert not coordinator.repairs

    monkeypatch.setenv("DAVINCI_GW_REPAIR_ACTIVE", "1")
    with pytest.raises(RepairContractError, match="递归"):
        asyncio.run(coordinator.start(diagnosis.diagnosis_id, START_CONFIRMATION))


def test_uncommitted_mcp_build_cannot_start_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    coordinator, diagnosis = _diagnose(
        tmp_path,
        _Facade(OperationStatus.VALIDATION_FAILED, (_Issue("PDUR_FAILED", "失败"),)),
        _Agent(AgentDiagnosis(
            FailureKind.TOOL_BUG, "已证实", ("复现",), ("src/example.py:1",), True,
        )),
    )
    monkeypatch.setattr(
        "davinci_gw.mcp.repair_service.load_build_info",
        lambda _repository: {"commit": "a" * 40, "dirty": True},
    )
    with pytest.raises(RepairContractError, match="未提交源码"):
        asyncio.run(coordinator.start(diagnosis.diagnosis_id, START_CONFIRMATION))
    assert not coordinator.repairs


def test_second_confirmation_and_state_are_required_before_submit(tmp_path: Path) -> None:
    coordinator, diagnosis = _diagnose(
        tmp_path,
        _Facade(OperationStatus.VALIDATION_FAILED, (_Issue("PDUR_FAILED", "失败"),)),
        _Agent(AgentDiagnosis(
            FailureKind.TOOL_BUG, "已证实", ("复现",), ("src/example.py:1",), True,
        )),
    )
    record = SimpleNamespace(state=RepairState.IMPLEMENTING)
    coordinator.repairs["repair"] = record  # type: ignore[assignment]
    with pytest.raises(RepairContractError, match="确认语"):
        asyncio.run(coordinator.submit("repair", "同意", "LOCAL_COMMIT", "fix(mcp): 修复问题"))
    with pytest.raises(RepairContractError, match="完成全部验证"):
        asyncio.run(coordinator.submit(
            "repair", SUBMIT_CONFIRMATION, "LOCAL_COMMIT", "fix(mcp): 修复问题",
        ))
    record.state = RepairState.AWAITING_SUBMISSION
    with pytest.raises(RepairContractError, match="fork_owner"):
        asyncio.run(coordinator.submit(
            "repair", SUBMIT_CONFIRMATION, "DRAFT_PR", "fix(mcp): 修复问题",
            upstream_repository="owner/repo",
        ))


def test_start_uses_isolated_worktree_and_cross_process_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, baseline, repository, python = _inputs(tmp_path)
    diagnosis = DiagnosisRecord(
        "diagnosis", repository, config, baseline, python,
        FailureKind.TOOL_BUG, "已证实", ("复现",), ("src/example.py:1",), True, (), True, False,
    )
    root = tmp_path / "sessions"
    first = RepairCoordinator(facade=_Facade(OperationStatus.SUCCESS), agent=_Agent(
        AgentDiagnosis(FailureKind.TOOL_BUG, "已证实"),
    ), session_root=root)  # type: ignore[arg-type]
    second = RepairCoordinator(facade=_Facade(OperationStatus.SUCCESS), agent=first.agent, session_root=root)  # type: ignore[arg-type]
    first.diagnoses[diagnosis.diagnosis_id] = diagnosis
    second.diagnoses[diagnosis.diagnosis_id] = diagnosis
    monkeypatch.setattr(
        "davinci_gw.mcp.repair_service.load_build_info",
        lambda _repository: {"commit": "a" * 40},
    )

    async def scenario() -> None:
        blocker = asyncio.Event()

        async def hold(record: object) -> None:
            await blocker.wait()

        monkeypatch.setattr(first, "_repair", hold)
        record = await first.start(diagnosis.diagnosis_id, START_CONFIRMATION)
        assert record.branch_name.startswith("hotfix/mcp-auto-")
        assert record.worktree_path.parent == record.session_path
        assert repository not in record.worktree_path.parents
        with pytest.raises(RepairContractError, match="活动修复"):
            await second.start(diagnosis.diagnosis_id, START_CONFIRMATION)
        lock_path = record.lock_path
        await first.cancel(record.repair_id, CANCEL_CONFIRMATION)
        assert record.state is RepairState.CANCELLED
        assert lock_path is not None and not lock_path.exists()
        assert record.lock_path is None

    asyncio.run(scenario())


def test_cancel_requires_confirmation_and_preserves_session(tmp_path: Path) -> None:
    coordinator, diagnosis = _diagnose(
        tmp_path,
        _Facade(OperationStatus.VALIDATION_FAILED, (_Issue("PDUR_FAILED", "失败"),)),
        _Agent(AgentDiagnosis(
            FailureKind.TOOL_BUG, "已证实", ("复现",), ("src/example.py:1",), True,
        )),
    )
    record = SimpleNamespace(
        state=RepairState.IMPLEMENTING, progress=35, lock_path=None,
        update=lambda state, stage, progress: None,
    )
    coordinator.repairs["repair"] = record  # type: ignore[assignment]
    with pytest.raises(RepairContractError, match="确认语"):
        asyncio.run(coordinator.cancel("repair", "取消"))
    # 正确确认不会删除任何工作目录；真实任务取消由后台 task 测试覆盖。
    assert CANCEL_CONFIRMATION == "确认取消工具BUG修复"


def test_repair_pipeline_runs_one_final_regression_and_waits_for_second_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, baseline, repository, python = _inputs(tmp_path)
    diagnosis = DiagnosisRecord(
        "diagnosis", repository, config, baseline, python,
        FailureKind.TOOL_BUG, "已证实", ("复现",), ("src/example.py:1",), True, (), True, False,
    )
    coordinator = RepairCoordinator(
        facade=_Facade(OperationStatus.SUCCESS),
        agent=_RepairingAgent(AgentDiagnosis(FailureKind.TOOL_BUG, "已证实")),
        session_root=tmp_path / "sessions",
    )  # type: ignore[arg-type]
    coordinator.diagnoses[diagnosis.diagnosis_id] = diagnosis
    monkeypatch.setattr(
        "davinci_gw.mcp.repair_service.load_build_info",
        lambda _repository: {"commit": "a" * 40, "dirty": False},
    )
    calls: list[tuple[str, ...]] = []
    full_regression_envs: list[dict[str, str]] = []

    async def fake_run(*args: str, cwd: Path | None = None, **kwargs: object) -> str:
        calls.append(args)
        if args[1:4] == ("-m", "pytest", "-q") and len(args) == 4:
            full_regression_envs.append(kwargs["env"])  # type: ignore[arg-type]
        if args[:2] == ("git", "-C") and "worktree" in args:
            worktree = Path(args[-2])
            worktree.mkdir(parents=True)
            (worktree / "scripts").mkdir()
            (worktree / "tests" / "integration").mkdir(parents=True)
            return ""
        if args[:3] == ("git", "status", "--porcelain"):
            return " M src/fix.py\n"
        if len(args) > 1 and args[1].endswith("cli.py"):
            output = Path(args[args.index("--output") + 1])
            output.write_text("<AUTOSAR/>", encoding="utf-8")
            return "输出验证通过"
        if "build_mcp_release.ps1" in " ".join(args):
            assert cwd is not None
            executable = cwd / "dist" / "mcp" / "davinci-gw-mcp" / "davinci-gw-mcp.exe"
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"exe")
            (executable.parent / "_internal").mkdir()
            archive = cwd / "release" / "davinci-gw-mcp-1.0.0-win-x64.zip"
            archive.parent.mkdir()
            archive.write_bytes(b"zip")
            return "构建成功"
        return "通过"

    monkeypatch.setattr("davinci_gw.mcp.repair_service._run", fake_run)

    async def scenario() -> None:
        record = await coordinator.start(diagnosis.diagnosis_id, START_CONFIRMATION)
        await coordinator.tasks[record.repair_id]
        assert record.state is RepairState.AWAITING_SUBMISSION
        assert record.verified_output and record.verified_output.is_file()
        assert record.candidate_archive and record.candidate_archive.is_file()
        report = record.session_path / "repair-report.json"
        assert '"state": "AWAITING_SUBMISSION"' in report.read_text(encoding="utf-8")
        full_runs = [call for call in calls if call[1:4] == ("-m", "pytest", "-q") and len(call) == 4]
        assert len(full_runs) == 1
        assert full_regression_envs[0]["PYTHONPATH"] == str(record.worktree_path / "src")
        assert full_regression_envs[0]["DAVINCI_GW_REPAIR_ACTIVE"] == "1"
        assert record.commit_hash is None
        await coordinator.cancel(record.repair_id, CANCEL_CONFIRMATION)

    asyncio.run(scenario())
