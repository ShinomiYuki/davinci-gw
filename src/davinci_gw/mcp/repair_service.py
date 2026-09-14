"""MCP 故障分类、隔离热修复和提交审批的最小协调器。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from uuid import uuid4

from davinci_gw.application import GatewayFacade
from davinci_gw.contracts import OperationStatus, UpdateRequestDto
from davinci_gw.mcp.repair_agent import CodexRepairAgent, RepairAgent
from davinci_gw.mcp.repair_models import (
    AgentDiagnosis,
    DiagnosisRecord,
    FailureKind,
    RepairRecord,
    RepairState,
)
from davinci_gw.mcp.version import MCP_VERSION, load_build_info

START_CONFIRMATION = "确认开始工具BUG自动修复"
SUBMIT_CONFIRMATION = "确认提交工具BUG修复"
CANCEL_CONFIRMATION = "确认取消工具BUG修复"
_CONVENTIONAL_COMMIT = re.compile(
    r"^(fix|feat|refactor|perf|test|build|ci|docs|chore)(\([a-z0-9_.-]+\))?!?: .+",
)
_FORBIDDEN_SUFFIXES = {".xlsx", ".xls", ".arxml", ".dbc", ".ldf", ".blf", ".env"}
_INPUT_PREFIXES = (
    "WORKBOOK_", "INPUT_", "PATH_", "CAN_ID_", "OPERATION_", "TIMEOUT_", "SIGNAL_BIT_",
)


class RepairContractError(ValueError):
    """可直接返回给 MCP 用户的修复流程契约错误。"""


async def _run(
    *args: str,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: float = 3600,
) -> str:
    """不经过 shell 执行受控命令，避免把路径或外部文本解释成命令。"""
    process = await asyncio.create_subprocess_exec(
        *args,
        cwd=str(cwd) if cwd else None,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        output, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise RepairContractError(f"命令执行超时：{Path(args[0]).name}") from None
    text = output.decode("utf-8", errors="replace")
    if process.returncode:
        tail = text[-4000:].strip()
        raise RepairContractError(f"命令执行失败（{process.returncode}）：{' '.join(args[:3])}\n{tail}")
    return text


def _absolute_file(value: str, suffix: str, label: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute() or not path.is_file() or path.suffix.casefold() != suffix:
        raise RepairContractError(f"{label}必须是存在的本地绝对 {suffix} 文件。")
    return path.resolve()


def _repository(value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute() or not (path / ".git").exists() or not (path / "pyproject.toml").is_file():
        raise RepairContractError("repository_path 必须指向带源码和 .git 的 davinci-gw 本地仓库。")
    return path.resolve()


def _python(value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute() or not path.is_file() or path.name.casefold() not in {"python.exe", "python"}:
        raise RepairContractError("python_path 必须指向存在的 Python 可执行文件。")
    return path.resolve()


def _issue_dicts(result: Any) -> tuple[dict[str, Any], ...]:
    issues = getattr(result, "issues", ())
    preview = getattr(result, "preview", None)
    if not issues and preview is not None:
        issues = getattr(preview, "issues", ())
    return tuple(item.to_dict() for item in issues)


def _has_dbc_skip(issues: Sequence[dict[str, Any]]) -> bool:
    return any("DBC" in str(item.get("message", "")).upper() and "跳过" in str(item.get("message", ""))
               for item in issues)


def _input_problem(issues: Sequence[dict[str, Any]]) -> bool:
    codes = [str(item.get("code", "")).upper() for item in issues]
    return bool(codes) and all(code.startswith(_INPUT_PREFIXES) for code in codes)


class RepairCoordinator:
    """把高风险动作收口到两次审批之间，并保留失败现场。"""

    def __init__(
        self,
        *,
        facade: GatewayFacade | None = None,
        agent: RepairAgent | None = None,
        session_root: Path | None = None,
    ) -> None:
        self.facade = facade or GatewayFacade()
        self.agent = agent or CodexRepairAgent()
        if session_root is not None:
            root = session_root
        else:
            local_app_data = os.environ.get("LOCALAPPDATA")
            # 不提前求值 Path.home()，确保 onedir 在无 USERPROFILE 的隔离环境也能启动。
            local_root = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
            root = local_root / "DaVinciGW" / "repair-sessions"
        self.session_root = root.resolve()
        self.diagnoses: dict[str, DiagnosisRecord] = {}
        self.repairs: dict[str, RepairRecord] = {}
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self._repo_active: dict[str, str] = {}

    def _acquire_repo_lock(self, repository: Path, repair_id: str) -> Path:
        """跨 MCP 进程原子占用仓库；锁状态不确定时交由用户核对。"""
        lock_root = self.session_root / "locks"
        lock_root.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(os.path.normcase(str(repository)).encode("utf-8")).hexdigest()[:24]
        lock_path = lock_root / f"{digest}.lock"
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                payload = json.loads(lock_path.read_text(encoding="utf-8"))
                active = str(payload.get("repair_id", "unknown"))
            except (OSError, ValueError, TypeError):
                active = "unknown"
            # 不探测或强制清除别的进程，避免误判后破坏仍在运行的修复现场。
            raise RepairContractError(f"该仓库已有活动修复：{active}；如进程已异常退出，请人工核对锁文件。") from None
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump({"pid": os.getpid(), "repair_id": repair_id}, stream)
        return lock_path

    @staticmethod
    def _release_repo_lock(record: RepairRecord) -> None:
        lock_path = record.lock_path
        record.lock_path = None
        if lock_path:
            lock_path.unlink(missing_ok=True)

    async def diagnose(
        self,
        config_path: str,
        baseline_path: str,
        repository_path: str,
        python_path: str,
    ) -> DiagnosisRecord:
        """先用产品真实预览复现，再把无法确定的失败交给只读 Codex 复核。"""
        config = _absolute_file(config_path, ".xlsx", "config_path")
        baseline = _absolute_file(baseline_path, ".arxml", "baseline_path")
        repository = _repository(repository_path)
        python = _python(python_path)
        prepared = await asyncio.to_thread(
            self.facade.preview,
            UpdateRequestDto(str(config), str(baseline)),
        )
        issues = _issue_dicts(prepared)
        succeeded = prepared.status is OperationStatus.SUCCESS
        session_id = getattr(prepared, "session_id", None)
        if session_id:
            self.facade.discard_prepared(str(session_id))
        if succeeded and _has_dbc_skip(issues):
            diagnosis = AgentDiagnosis(
                FailureKind.DBC_MISSING,
                "存在缺少 DBC 对象并已跳过的需求；其他可完成路由仍可生成，不进入代码修复。",
                tuple(str(item.get("message", "")) for item in issues if "DBC" in str(item.get("message", "")).upper()),
                reproduced=True,
            )
        elif succeeded:
            diagnosis = AgentDiagnosis(
                FailureKind.NONE, "原始输入预览成功，未复现阻断输出的问题。", reproduced=True,
            )
        elif _input_problem(issues):
            diagnosis = AgentDiagnosis(
                FailureKind.INPUT_PROBLEM,
                "失败由配置表或调用输入契约问题造成，不允许修改工具代码。",
                tuple(str(item.get("message", "")) for item in issues),
                reproduced=True,
            )
        elif any(str(item.get("code", "")).upper().startswith("ARXML_STRUCTURE_") for item in issues):
            diagnosis = AgentDiagnosis(
                FailureKind.BASELINE_PROBLEM,
                "基线 ARXML 结构无法解析，不允许自动修改输入基线。",
                tuple(str(item.get("message", "")) for item in issues),
                reproduced=True,
            )
        else:
            diagnosis = await self.agent.diagnose(repository, config, baseline, issues)
            if diagnosis.kind is FailureKind.TOOL_BUG and (
                not diagnosis.reproduced or not diagnosis.code_evidence
            ):
                diagnosis = AgentDiagnosis(
                    FailureKind.UNDETERMINED,
                    "现有证据不足以证明是工具 BUG，未取得自动修复资格。",
                    diagnosis.evidence,
                    diagnosis.code_evidence,
                    diagnosis.reproduced,
                )

        diagnosis_id = str(uuid4())
        record = DiagnosisRecord(
            diagnosis_id=diagnosis_id,
            repository_path=repository,
            config_path=config,
            baseline_path=baseline,
            python_path=python,
            kind=diagnosis.kind,
            summary=diagnosis.summary,
            evidence=diagnosis.evidence,
            code_evidence=diagnosis.code_evidence,
            reproduced=diagnosis.reproduced,
            issues=issues,
            repair_eligible=(
                diagnosis.kind is FailureKind.TOOL_BUG
                and diagnosis.reproduced
                and bool(diagnosis.code_evidence)
            ),
            generation_can_continue=succeeded,
        )
        self.diagnoses[diagnosis_id] = record
        return record

    async def start(self, diagnosis_id: str, confirmation: str) -> RepairRecord:
        """审批一通过后才建立分支和 worktree，并在后台执行修复。"""
        if confirmation != START_CONFIRMATION:
            raise RepairContractError(f"开始修复前必须原样提供确认语：{START_CONFIRMATION}")
        if os.environ.get("DAVINCI_GW_REPAIR_ACTIVE") == "1":
            raise RepairContractError("当前进程位于自动修复线程中，禁止递归启动修复。")
        diagnosis = self.diagnoses.get(diagnosis_id)
        if diagnosis is None:
            raise RepairContractError("diagnosis_id 不存在或不属于当前 MCP 进程。")
        if not diagnosis.repair_eligible:
            raise RepairContractError("该诊断不是具备复现和代码证据的工具 BUG，禁止进入自动修复。")
        repo_key = os.path.normcase(str(diagnosis.repository_path))
        active = self._repo_active.get(repo_key)
        if active and not self.repairs[active].state.terminal:
            raise RepairContractError(f"该仓库已有活动修复：{active}")

        build_info = load_build_info(diagnosis.repository_path)
        base_commit = str(build_info.get("commit") or "").strip()
        if not re.fullmatch(r"[0-9a-fA-F]{40}", base_commit):
            raise RepairContractError("MCP 包未记录有效构建提交，无法建立可追溯热修复分支。")
        if build_info.get("dirty") is True:
            raise RepairContractError("当前 MCP 来自未提交源码，无法建立可追溯热修复分支。")
        repair_id = str(uuid4())
        suffix = repair_id.split("-", 1)[0]
        branch = f"hotfix/mcp-auto-{suffix}"
        session = self.session_root / repair_id
        worktree = session / "worktree"
        lock_path = self._acquire_repo_lock(diagnosis.repository_path, repair_id)
        record = RepairRecord(repair_id, diagnosis, base_commit, branch, worktree, session, lock_path)
        self.repairs[repair_id] = record
        self._repo_active[repo_key] = repair_id
        self.tasks[repair_id] = asyncio.create_task(self._repair(record), name=f"davinci-gw-repair-{suffix}")
        return record

    def status(self, repair_id: str) -> RepairRecord:
        record = self.repairs.get(repair_id)
        if record is None:
            raise RepairContractError("repair_id 不存在或不属于当前 MCP 进程。")
        return record

    async def cancel(self, repair_id: str, confirmation: str) -> RepairRecord:
        if confirmation != CANCEL_CONFIRMATION:
            raise RepairContractError(f"取消前必须原样提供确认语：{CANCEL_CONFIRMATION}")
        record = self.status(repair_id)
        task = self.tasks.get(repair_id)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        if not record.state.terminal:
            record.update(RepairState.CANCELLED, "已取消；worktree 和报告已保留", record.progress)
        self._release_repo_lock(record)
        return record

    async def _repair(self, record: RepairRecord) -> None:
        diagnosis = record.diagnosis
        try:
            record.session_path.mkdir(parents=True, exist_ok=False)
            record.update(RepairState.PREPARING_WORKTREE, "正在创建隔离 worktree", 5)
            await _run(
                "git", "-C", str(diagnosis.repository_path), "worktree", "add", "-b",
                record.branch_name, str(record.worktree_path), record.base_commit,
            )
            result = await self.agent.repair(
                record.worktree_path,
                AgentDiagnosis(
                    diagnosis.kind, diagnosis.summary, diagnosis.evidence,
                    diagnosis.code_evidence, diagnosis.reproduced,
                ),
                diagnosis.python_path,
                record.update,
            )
            record.implementation_summary = result.implementation_summary
            record.review_summary = result.review_summary
            record.review_findings = result.review_findings
            record.targeted_tests = result.targeted_tests
            record.codex_thread_id = result.codex_thread_id
            await self._validate_changed_paths(record)
            await _run("git", "diff", "--check", cwd=record.worktree_path)

            record.update(RepairState.REGRESSION, "正在执行唯一一次最终全量回归", 72)
            output = await _run(
                str(diagnosis.python_path), "-m", "pytest", "-q",
                cwd=record.worktree_path, env=self._python_env(record.worktree_path), timeout=7200,
            )
            record.full_regression = output[-2000:].strip()

            record.update(RepairState.VERIFYING_ORIGINAL_INPUT, "正在使用原始输入重新生成并校验", 82)
            verified = record.session_path / "verified-output.arxml"
            output = await _run(
                str(diagnosis.python_path), str(record.worktree_path / "src" / "davinci_gw" / "cli.py"), "generate",
                "--config", str(diagnosis.config_path), "--baseline", str(diagnosis.baseline_path),
                "--output", str(verified), cwd=record.worktree_path, timeout=7200,
                env=self._python_env(record.worktree_path),
            )
            if not verified.is_file():
                raise RepairContractError(f"原始输入复验未生成输出。\n{output[-3000:]}")
            record.verified_output = verified
            record.original_input_verification = output[-2000:].strip()

            record.update(RepairState.BUILDING_CANDIDATE, "正在构建并验证 MCP onedir 候选包", 90)
            await self._build_candidate(record)
            await self._smoke_candidate(record)
            record.update(RepairState.AWAITING_SUBMISSION, "修复已验证，等待第二次提交确认", 100)
            self._write_report(record)
        except asyncio.CancelledError:
            record.update(RepairState.CANCELLED, "已取消；worktree 和报告已保留", record.progress)
            self._write_report(record)
            self._release_repo_lock(record)
            raise
        except Exception as exc:
            record.error = str(exc)
            record.update(RepairState.FAILED, "自动修复失败；worktree 和报告已保留", record.progress)
            self._write_report(record)
            self._release_repo_lock(record)

    @staticmethod
    def _python_env(worktree: Path) -> dict[str, str]:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(worktree / "src")
        env["DAVINCI_GW_REPAIR_ACTIVE"] = "1"
        return env

    async def _validate_changed_paths(self, record: RepairRecord) -> None:
        names = await _run("git", "status", "--porcelain", cwd=record.worktree_path)
        changed: list[str] = []
        for line in names.splitlines():
            if len(line) >= 4:
                changed.append(line[3:].split(" -> ")[-1].strip('"'))
        if not changed:
            raise RepairContractError("修复代理没有产生代码或测试改动。")
        forbidden = [name for name in changed if (
            Path(name).suffix.casefold() in _FORBIDDEN_SUFFIXES
            or Path(name).name.casefold() == ".env"
            or name.replace("\\", "/").startswith(("input/", "release/"))
        )]
        if forbidden:
            raise RepairContractError(f"修复包含禁止提交的输入或产物：{', '.join(forbidden)}")

    async def _smoke_candidate(self, record: RepairRecord) -> None:
        executable = (
            record.worktree_path / "dist" / "mcp" / "davinci-gw-mcp" / "davinci-gw-mcp.exe"
        )
        if not executable.is_file():
            raise RepairContractError("onedir 候选 EXE 不存在。")
        smoke_env = self._python_env(record.worktree_path)
        smoke_env["DAVINCI_GW_MCP_EXE"] = str(executable)
        await _run(
            str(record.diagnosis.python_path), "-m", "pytest", "-q",
            "tests/integration/test_mcp_executable.py", cwd=record.worktree_path,
            env=smoke_env, timeout=1800,
        )

    async def _build_candidate(self, record: RepairRecord) -> None:
        await _run(
            "pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-File",
            str(record.worktree_path / "scripts" / "build_mcp_release.ps1"),
            "-Python", str(record.diagnosis.python_path), "-Version", MCP_VERSION,
            cwd=record.worktree_path, timeout=7200,
        )
        archive = record.worktree_path / "release" / f"davinci-gw-mcp-{MCP_VERSION}-win-x64.zip"
        if not archive.is_file():
            raise RepairContractError("候选 ZIP 未生成。")
        record.candidate_archive = archive

    def _write_report(self, record: RepairRecord) -> None:
        try:
            record.session_path.mkdir(parents=True, exist_ok=True)
            (record.session_path / "repair-report.json").write_text(
                json.dumps(record.to_payload(), ensure_ascii=False, indent=2), encoding="utf-8",
            )
        except OSError:
            pass

    async def submit(
        self,
        repair_id: str,
        confirmation: str,
        mode: str,
        commit_message: str,
        remote_name: str = "origin",
        upstream_repository: str = "",
        fork_owner: str = "",
    ) -> RepairRecord:
        """审批二之后才提交；外部贡献者只能推 fork 并创建 Draft PR。"""
        if confirmation != SUBMIT_CONFIRMATION:
            raise RepairContractError(f"正式提交前必须原样提供确认语：{SUBMIT_CONFIRMATION}")
        record = self.status(repair_id)
        if record.state is not RepairState.AWAITING_SUBMISSION:
            raise RepairContractError("只有完成全部验证并等待提交的修复可以正式提交。")
        if not _CONVENTIONAL_COMMIT.fullmatch(commit_message.strip()):
            raise RepairContractError("提交信息必须是中文内容的 Conventional Commits 格式。")
        if not re.search(r"[\u4e00-\u9fff]", commit_message):
            raise RepairContractError("提交信息必须包含中文说明。")
        normalized_mode = mode.strip().upper()
        if normalized_mode not in {"LOCAL_COMMIT", "DRAFT_PR", "MAINTAINER"}:
            raise RepairContractError("mode 只能是 LOCAL_COMMIT、DRAFT_PR 或 MAINTAINER。")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", remote_name):
            raise RepairContractError("remote_name 格式无效。")
        if normalized_mode == "DRAFT_PR" and (
            not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", upstream_repository)
            or not re.fullmatch(r"[A-Za-z0-9-]+", fork_owner)
        ):
            raise RepairContractError("DRAFT_PR 模式必须提供合法的 upstream_repository 和 fork_owner。")
        record.update(RepairState.SUBMITTING, "正在执行已批准的正式提交", 100)
        try:
            await self._validate_changed_paths(record)
            await _run("git", "add", "--all", cwd=record.worktree_path)
            await _run("git", "diff", "--cached", "--check", cwd=record.worktree_path)
            await _run("git", "commit", "-m", commit_message.strip(), cwd=record.worktree_path)
            commit = (await _run("git", "rev-parse", "HEAD", cwd=record.worktree_path)).strip()
            record.commit_hash = commit
            # 正式提交后重建一次，使包内构建 commit 精确对应可推送的修复提交。
            await self._build_candidate(record)
            await self._smoke_candidate(record)
            if normalized_mode == "DRAFT_PR":
                await _run("git", "push", "-u", remote_name, record.branch_name, cwd=record.worktree_path)
                url = (await _run(
                    "gh", "pr", "create", "--draft", "--repo", upstream_repository,
                    "--base", "main", "--head", f"{fork_owner}:{record.branch_name}",
                    "--title", commit_message.strip(),
                    "--body", "由 davinci-gw MCP 自动修复生成；CI 仅使用脱敏测试夹具。",
                    cwd=record.worktree_path,
                )).strip()
                record.submission = {"mode": normalized_mode, "draft_pr": url}
            elif normalized_mode == "MAINTAINER":
                await self._maintainer_submit(record, remote_name)
            else:
                record.submission = {"mode": normalized_mode}
            record.update(RepairState.COMPLETED, "已完成用户批准的正式提交", 100)
            self._write_report(record)
            self._release_repo_lock(record)
            return record
        except Exception as exc:
            record.error = str(exc)
            record.update(RepairState.NEEDS_ATTENTION, "提交未完成，需人工处理；现场已保留", 100)
            self._write_report(record)
            self._release_repo_lock(record)
            return record

    async def _maintainer_submit(self, record: RepairRecord, remote_name: str) -> None:
        repo = record.diagnosis.repository_path
        status = await _run("git", "status", "--porcelain", cwd=repo)
        branch = (await _run("git", "branch", "--show-current", cwd=repo)).strip()
        if status.strip() or branch != "main":
            raise RepairContractError("维护者提交要求源码仓库位于干净的 main 分支。")
        await _run("git", "fetch", remote_name, "main", cwd=repo)
        remote_main = f"{remote_name}/main"
        local_main = (await _run("git", "rev-parse", "main", cwd=repo)).strip()
        upstream_main = (await _run("git", "rev-parse", remote_main, cwd=repo)).strip()
        common_base = (await _run("git", "merge-base", "main", remote_main, cwd=repo)).strip()
        if common_base not in {local_main, upstream_main}:
            raise RepairContractError("本地 main 与远端 main 已分叉，拒绝自动合并或改写历史。")
        if local_main != upstream_main:
            if common_base == local_main:
                await _run("git", "merge", "--ff-only", remote_main, cwd=repo)
            # 如果本地 main 领先远端，则保留维护者尚未推送的提交，并把修复叠加在其上。
            local_main = (await _run("git", "rev-parse", "main", cwd=repo)).strip()
        if local_main != record.base_commit:
            await _run("git", "rebase", "main", cwd=record.worktree_path)
            await _run(
                str(record.diagnosis.python_path), "-m", "pytest", "-q",
                cwd=record.worktree_path, env=self._python_env(record.worktree_path), timeout=7200,
            )
            drift_output = record.session_path / "verified-after-rebase.arxml"
            await _run(
                str(record.diagnosis.python_path),
                str(record.worktree_path / "src" / "davinci_gw" / "cli.py"), "generate",
                "--config", str(record.diagnosis.config_path),
                "--baseline", str(record.diagnosis.baseline_path),
                "--output", str(drift_output), cwd=record.worktree_path,
                env=self._python_env(record.worktree_path), timeout=7200,
            )
            if not drift_output.is_file():
                raise RepairContractError("main 漂移后的原始输入复验未生成输出。")
            record.commit_hash = (await _run("git", "rev-parse", "HEAD", cwd=record.worktree_path)).strip()
            await self._build_candidate(record)
            await self._smoke_candidate(record)
        await _run("git", "merge", "--ff-only", record.commit_hash or "", cwd=repo)
        await _run("git", "push", remote_name, "main", cwd=repo)
        tag = f"v{MCP_VERSION}"
        if not record.candidate_archive:
            raise RepairContractError("缺少已经验证的 MCP 候选包。")
        assets = [str(record.candidate_archive)]
        sidecar = Path(f"{record.candidate_archive}.sha256")
        if sidecar.is_file():
            assets.append(str(sidecar))
        await _run(
            "gh", "release", "create", tag, *assets,
            "--target", record.commit_hash or "", "--title", f"DaVinci GW MCP {MCP_VERSION}",
            "--generate-notes", cwd=repo,
        )
        record.submission = {"mode": "MAINTAINER", "tag": tag}

    async def close(self) -> None:
        """停止当前进程仍在运行的后台任务；已创建 worktree 均保留。"""
        running = [task for task in self.tasks.values() if not task.done()]
        for task in running:
            task.cancel()
        if running:
            await asyncio.gather(*running, return_exceptions=True)
        for record in self.repairs.values():
            if record.state is RepairState.AWAITING_SUBMISSION:
                record.update(
                    RepairState.NEEDS_ATTENTION,
                    "MCP 进程已关闭；候选和 worktree 已保留，请人工继续提交",
                    record.progress,
                )
                self._write_report(record)
            self._release_repo_lock(record)
