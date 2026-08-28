"""GUI 与本地 MCP 可共同调用的稳定公共应用入口。"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from davinci_gw.application.generate import prepare_transaction
from davinci_gw.application.public_mapping import issues_to_dto, public_issue
from davinci_gw.application.sessions import (
    InputFingerprintChangedError,
    PreparedSessionRecord,
    PreparedSessionStore,
    SessionAccessError,
    fingerprint_file,
)
from davinci_gw.application.validate import validate_inputs
from davinci_gw.contracts import (
    ArtifactDto,
    CapabilitiesDto,
    GenerationResultDto,
    InputFileDto,
    OperationResultDto,
    OperationStatus,
    PreparedSessionDto,
    PreviewResultDto,
    SessionState,
    UpdateRequestDto,
)
from davinci_gw.domain.errors import (
    OutputCommitAbortedError,
    OutputValidationError,
    OutputWriteError,
)
from davinci_gw.features import FeatureContext, FeatureRegistry, default_feature_registry
from davinci_gw.runtime import CancellationToken, OperationCancelled, ProgressObserver, ProgressReporter
from davinci_gw.validation.output import validate_generated_output


class GatewayFacade:
    """捕获全部内部异常，只返回可序列化公共 DTO。"""

    def __init__(
        self,
        *,
        feature_registry: FeatureRegistry | None = None,
        session_store: PreparedSessionStore | None = None,
    ) -> None:
        self.features = feature_registry or default_feature_registry()
        self.sessions = session_store or PreparedSessionStore()

    def capabilities(self) -> CapabilitiesDto:
        """返回当前实例已注册的功能和同步公共操作。"""
        return CapabilitiesDto(
            self.features.capabilities(),
            ("VALIDATE", "PREPARE", "PREVIEW", "COMMIT_PREPARED", "GENERATE", "DISCARD", "CLEANUP"),
        )

    def get_capabilities(self) -> CapabilitiesDto:
        """兼容偏命令式适配器的能力查询命名。"""
        return self.capabilities()

    @staticmethod
    def _token(token: CancellationToken | None) -> CancellationToken:
        return token or CancellationToken()

    @staticmethod
    def _internal_failure(operation_id: str) -> OperationResultDto:
        return OperationResultDto(
            operation_id,
            OperationStatus.INTERNAL_FAILURE,
            (public_issue("INTERNAL_FAILURE", "内部处理失败；未公开异常详情，请通过受控日志诊断。"),),
        )

    def validate(
        self,
        request: UpdateRequestDto,
        *,
        observer: ProgressObserver | None = None,
        cancellation: CancellationToken | None = None,
    ) -> OperationResultDto:
        """只读校验输入，并把所有失败映射为公共状态和问题。"""
        operation_id = str(uuid4())
        reporter, token = ProgressReporter(operation_id, observer), self._token(cancellation)
        try:
            reporter.emit("validate", "校验输入", 0, 1)
            token.checkpoint("validate")
            report = validate_inputs(request.config_path, request.baseline_path)
            issues = issues_to_dto(report.issues)
            status = OperationStatus.SUCCESS if report.is_valid else OperationStatus.VALIDATION_FAILED
            reporter.emit("complete", "校验完成", 1, 1, cancellable=False)
            return OperationResultDto(operation_id, status, issues)
        except OperationCancelled:
            reporter.emit("cancelled", "操作已取消", 1, 1, cancellable=False)
            return OperationResultDto(operation_id, OperationStatus.CANCELLED)
        except Exception:
            reporter.emit("error", "操作失败", 1, 1, cancellable=False)
            return self._internal_failure(operation_id)

    def prepare(
        self,
        request: UpdateRequestDto,
        *,
        observer: ProgressObserver | None = None,
        cancellation: CancellationToken | None = None,
    ) -> PreparedSessionDto:
        """完成一次解析、规划和内存应用，成功时返回可提交会话。"""
        operation_id = str(uuid4())
        reporter, token = ProgressReporter(operation_id, observer), self._token(cancellation)
        initial_config = initial_baseline = None
        stage_positions = {
            "before_workbook": (1, "读取配置表"), "after_workbook": (2, "配置表读取完成"),
            "after_baseline": (3, "基准 ARXML 解析完成"), "before_plan": (4, "规划内存事务"),
            "after_plan": (5, "内存事务完成"),
        }

        def checkpoint(stage_id: str) -> None:
            current, label = stage_positions[stage_id]
            reporter.emit(stage_id, label, current, 7)
            token.checkpoint(stage_id)

        try:
            reporter.emit("fingerprint", "记录输入指纹", 0, 7)
            token.checkpoint("fingerprint")
            config_path = Path(request.config_path).expanduser().resolve()
            baseline_path = Path(request.baseline_path).expanduser().resolve()
            if config_path.is_file() and baseline_path.is_file():
                initial_config = fingerprint_file(config_path)
                initial_baseline = fingerprint_file(baseline_path)
            prepared = prepare_transaction(config_path, baseline_path, checkpoint=checkpoint)
            plan_issues = prepared.plan.issues if prepared.plan else ()
            public_issues = issues_to_dto(prepared.validation.issues + plan_issues + prepared.issues)
            workbook = prepared.validation.workbook_data
            context = FeatureContext(workbook, prepared.plan, public_issues)
            summaries = self.features.summarize(context)
            blocking = any(issue.severity == "ERROR" for issue in public_issues)
            if blocking or prepared.plan is None or prepared.document is None:
                preview = PreviewResultDto(
                    operation_id, OperationStatus.VALIDATION_FAILED, summaries, public_issues,
                    target_version=workbook.target_version if workbook else None,
                )
                reporter.emit("complete", "预处理未通过", 7, 7, cancellable=False)
                return PreparedSessionDto(
                    operation_id, OperationStatus.VALIDATION_FAILED, preview=preview, issues=public_issues,
                )
            reporter.emit("fingerprint_recheck", "复核输入指纹", 6, 7)
            token.checkpoint("fingerprint_recheck")
            final_config = fingerprint_file(config_path)
            final_baseline = fingerprint_file(baseline_path)
            if initial_config != final_config or initial_baseline != final_baseline:
                issue = public_issue("INPUT_CHANGED", "输入文件在预处理期间发生变化，请重新预览。")
                reporter.emit("error", "输入已变化", 7, 7, cancellable=False)
                return PreparedSessionDto(operation_id, OperationStatus.INPUT_CHANGED, issues=(issue,))
            input_files = (
                InputFileDto("CONFIG", str(config_path), final_config),
                InputFileDto("BASELINE", str(baseline_path), final_baseline),
            )
            preview = PreviewResultDto(
                operation_id, OperationStatus.SUCCESS, summaries, public_issues, input_files,
                workbook.target_version if workbook else None,
            )
            normalized_request = UpdateRequestDto(
                str(config_path), str(baseline_path), request.output_path, request.overwrite,
            )
            record = self.sessions.create(
                normalized_request, final_config, final_baseline, prepared, preview,
            )
            reporter.emit("complete", "Prepared Session 已就绪", 7, 7, cancellable=False)
            return PreparedSessionDto(
                operation_id, OperationStatus.SUCCESS, record.session_id, SessionState.READY,
                record.created_at, record.expires_at, preview,
            )
        except OperationCancelled:
            reporter.emit("cancelled", "预处理已取消", 7, 7, cancellable=False)
            return PreparedSessionDto(operation_id, OperationStatus.CANCELLED)
        except InputFingerprintChangedError:
            reporter.emit("error", "输入已变化", 7, 7, cancellable=False)
            issue = public_issue("INPUT_CHANGED", "输入文件在计算指纹时发生变化，请重新预览。")
            return PreparedSessionDto(operation_id, OperationStatus.INPUT_CHANGED, issues=(issue,))
        except SessionAccessError as exc:
            reporter.emit("error", "会话创建失败", 7, 7, cancellable=False)
            issue = public_issue(exc.code, str(exc))
            return PreparedSessionDto(operation_id, exc.status, issues=(issue,))
        except Exception:
            reporter.emit("error", "预处理失败", 7, 7, cancellable=False)
            issue = public_issue("INTERNAL_FAILURE", "内部处理失败；未公开异常详情，请通过受控日志诊断。")
            return PreparedSessionDto(operation_id, OperationStatus.INTERNAL_FAILURE, issues=(issue,))

    def preview(
        self,
        request: UpdateRequestDto,
        *,
        observer: ProgressObserver | None = None,
        cancellation: CancellationToken | None = None,
    ) -> PreparedSessionDto:
        """预览即建立可提交会话，避免预览后再次解析或规划。"""
        return self.prepare(request, observer=observer, cancellation=cancellation)

    def commit_prepared(
        self,
        session_id: str,
        output_path: str,
        *,
        overwrite: bool = False,
        observer: ProgressObserver | None = None,
        cancellation: CancellationToken | None = None,
    ) -> GenerationResultDto:
        """一次性消费会话并安全发布；任何提交前失败都会使会话失效。"""
        operation_id = str(uuid4())
        reporter, token = ProgressReporter(operation_id, observer), self._token(cancellation)
        acquired = False
        try:
            reporter.emit("acquire", "锁定 Prepared Session", 0, 4)
            token.checkpoint("acquire")
            record = self.sessions.acquire_for_commit(session_id)
            acquired = True
            prepared = record.prepared
            if prepared is None or prepared.document is None or prepared.plan is None:
                raise SessionAccessError(
                    OperationStatus.SESSION_INVALID, "SESSION_INVALID", "Prepared Session 内部状态无效。",
                )
            reporter.emit("fingerprint_check", "提交前复核输入", 1, 4)
            token.checkpoint("fingerprint_check")
            if not self._inputs_unchanged(record):
                raise OutputCommitAbortedError("INPUT_CHANGED", "输入文件在预览后发生变化，请重新预览。")

            def before_replace() -> None:
                reporter.emit("publish_check", "原子发布前最终复核", 3, 4)
                if token.is_cancelled:
                    raise OutputCommitAbortedError("CANCELLED", "操作在原子发布前已取消。")
                if not self._inputs_unchanged(record):
                    raise OutputCommitAbortedError("INPUT_CHANGED", "输入文件在原子发布前发生变化。")

            reporter.emit("write_temp", "写入并校验临时文件", 2, 4)
            token.checkpoint("write_temp")
            output = prepared.document.write_atomic(
                output_path,
                overwrite=overwrite,
                validator=lambda generated: validate_generated_output(generated, prepared.plan),
                before_replace=before_replace,
            )
            self.sessions.finish_commit(session_id, success=True)
            acquired = False
            try:
                fingerprint = fingerprint_file(output)
                artifact = ArtifactDto(fingerprint.path, fingerprint.size, fingerprint.sha256)
            except Exception:
                # 原子替换已经成功，后续元数据读取失败不能把已提交结果误报为取消或失败。
                artifact = ArtifactDto(str(output), 0, "")
            reporter.emit("complete", "生成完成", 4, 4, cancellable=False)
            return GenerationResultDto(
                operation_id, OperationStatus.SUCCESS, features=record.preview.features,
                artifact=artifact, session_id=session_id,
            )
        except SessionAccessError as exc:
            reporter.emit("error", "Prepared Session 不可提交", 4, 4, cancellable=False)
            return GenerationResultDto(
                operation_id, exc.status, (public_issue(exc.code, str(exc)),), session_id=session_id,
            )
        except OutputCommitAbortedError as exc:
            status = OperationStatus.CANCELLED if exc.reason == "CANCELLED" else OperationStatus.INPUT_CHANGED
            reporter.emit(
                "cancelled" if status is OperationStatus.CANCELLED else "error",
                "提交已安全终止", 4, 4, cancellable=False,
            )
            return GenerationResultDto(
                operation_id, status, (public_issue(exc.reason, str(exc)),), session_id=session_id,
            )
        except OperationCancelled:
            reporter.emit("cancelled", "提交已取消", 4, 4, cancellable=False)
            return GenerationResultDto(operation_id, OperationStatus.CANCELLED, session_id=session_id)
        except OutputValidationError as exc:
            reporter.emit("error", "输出被安全拒绝", 4, 4, cancellable=False)
            return GenerationResultDto(
                operation_id, OperationStatus.VALIDATION_FAILED,
                (public_issue("OUTPUT_REJECTED", str(exc), category="CONTRACT"),), session_id=session_id,
            )
        except OutputWriteError as exc:
            reporter.emit("error", "输出写入失败", 4, 4, cancellable=False)
            status = OperationStatus.INTERNAL_FAILURE if exc.__cause__ else OperationStatus.VALIDATION_FAILED
            category = "SYSTEM" if exc.__cause__ else "CONTRACT"
            return GenerationResultDto(
                operation_id, status,
                (public_issue("OUTPUT_WRITE_FAILED", str(exc), category=category),), session_id=session_id,
            )
        except Exception:
            reporter.emit("error", "提交失败", 4, 4, cancellable=False)
            return GenerationResultDto(
                operation_id, OperationStatus.INTERNAL_FAILURE,
                (public_issue("INTERNAL_FAILURE", "内部处理失败；目标未被报告为成功。"),), session_id=session_id,
            )
        finally:
            if acquired:
                self.sessions.finish_commit(session_id, success=False)

    def generate(
        self,
        request: UpdateRequestDto,
        *,
        observer: ProgressObserver | None = None,
        cancellation: CancellationToken | None = None,
    ) -> GenerationResultDto:
        """以单一公共操作序列组合 prepare 与 commit。"""
        operation_id = str(uuid4())
        reporter = ProgressReporter(operation_id, observer)

        class PhaseObserver:
            """把两个内部调用折叠为一个公共操作，终态只由 generate 发送一次。"""

            def __init__(self, phase: str) -> None:
                self.phase = phase

            def on_progress(self, event: object) -> None:
                stage_id = getattr(event, "stage_id", "progress")
                if stage_id in {"complete", "error", "cancelled"}:
                    return
                reporter.emit(
                    f"{self.phase}.{stage_id}",
                    str(getattr(event, "stage_name", self.phase)),
                    int(getattr(event, "current", 0)),
                    int(getattr(event, "total", 0)),
                    cancellable=bool(getattr(event, "cancellable", True)),
                )

        prepared = self.prepare(
            request, observer=PhaseObserver("prepare"), cancellation=cancellation,
        )
        if prepared.status is not OperationStatus.SUCCESS or prepared.session_id is None:
            terminal = "cancelled" if prepared.status is OperationStatus.CANCELLED else "error"
            reporter.emit(terminal, "一步生成未完成", 1, 1, cancellable=False)
            return GenerationResultDto(
                operation_id, prepared.status, prepared.issues,
                features=prepared.preview.features if prepared.preview else (),
                session_id=prepared.session_id,
            )
        if not request.output_path:
            self.sessions.discard(prepared.session_id)
            reporter.emit("error", "缺少输出路径", 1, 1, cancellable=False)
            return GenerationResultDto(
                operation_id, OperationStatus.VALIDATION_FAILED,
                (public_issue("OUTPUT_PATH_REQUIRED", "一次性生成必须提供输出路径。", category="CONTRACT"),),
                session_id=prepared.session_id,
            )
        committed = self.commit_prepared(
            prepared.session_id, request.output_path, overwrite=request.overwrite,
            observer=PhaseObserver("commit"), cancellation=cancellation,
        )
        terminal = (
            "complete" if committed.status is OperationStatus.SUCCESS
            else "cancelled" if committed.status is OperationStatus.CANCELLED else "error"
        )
        reporter.emit(terminal, "一步生成完成" if terminal == "complete" else "一步生成未完成", 1, 1,
                      cancellable=False)
        return GenerationResultDto(
            operation_id, committed.status, committed.issues, committed.features,
            committed.artifact, committed.session_id,
        )

    @staticmethod
    def _inputs_unchanged(record: PreparedSessionRecord) -> bool:
        """文件缺失、不可读或任一指纹字段变化都视为预览失效。"""
        try:
            return (
                fingerprint_file(record.request.config_path) == record.config_fingerprint
                and fingerprint_file(record.request.baseline_path) == record.baseline_fingerprint
            )
        except OSError:
            return False

    def discard_prepared(self, session_id: str) -> OperationResultDto:
        """主动使尚未提交的会话失效并释放大对象。"""
        operation_id = str(uuid4())
        try:
            self.sessions.discard(session_id)
            return OperationResultDto(operation_id, OperationStatus.SUCCESS, session_id=session_id)
        except SessionAccessError as exc:
            return OperationResultDto(
                operation_id, exc.status, (public_issue(exc.code, str(exc)),), session_id=session_id,
            )
        except Exception:
            return self._internal_failure(operation_id)

    def cleanup_expired(self) -> OperationResultDto:
        """释放所有已到期 READY 会话持有的内部工作树。"""
        operation_id = str(uuid4())
        try:
            self.sessions.cleanup_expired()
            return OperationResultDto(operation_id, OperationStatus.SUCCESS)
        except Exception:
            return self._internal_failure(operation_id)
