"""网关生成与经审批自动修复工具共用的薄应用服务。"""

from __future__ import annotations

import logging
from collections import OrderedDict
from threading import RLock
from uuid import UUID, uuid4

from davinci_gw.application import GatewayFacade
from davinci_gw.contracts import OperationStatus, ProgressEventDto, UpdateRequestDto
from davinci_gw.mcp.mapping import dto_envelope, envelope, issue
from davinci_gw.mcp.paths import PathPolicyError, validate_input_file, validate_new_output
from davinci_gw.mcp.runtime import BoundedFacadeRuntime
from davinci_gw.mcp.repair_service import RepairContractError, RepairCoordinator
from davinci_gw.mcp.version import MCP_VERSION, load_build_info

LOGGER = logging.getLogger("davinci_gw.mcp.service")


class _ProgressLogObserver:
    """只记录不含文件内容或用户参数的阶段元数据。"""

    def on_progress(self, event: ProgressEventDto) -> None:
        LOGGER.info(
            "operation=%s stage=%s progress=%.2f cancellable=%s",
            event.operation_id, event.stage_id, event.percent, event.cancellable,
        )


class GatewayMcpService:
    """每进程一个 Facade；不解析 CLI 文本，不接触内部 ARXML 对象。"""

    def __init__(
        self,
        *,
        facade: GatewayFacade | None = None,
        runtime: BoundedFacadeRuntime | None = None,
        repairs: RepairCoordinator | None = None,
    ) -> None:
        self.facade = facade or GatewayFacade()
        self.runtime = runtime or BoundedFacadeRuntime()
        self._capabilities = self.facade.get_capabilities()
        self._session_capacity = max(1, self._capabilities.session_capacity)
        self._prepared_baselines: OrderedDict[str, str] = OrderedDict()
        self._lock = RLock()
        self._observer = _ProgressLogObserver()
        self.repairs = repairs or RepairCoordinator(facade=self.facade)

    @staticmethod
    def _path_failure(tool: str, exc: PathPolicyError) -> dict[str, object]:
        return envelope(
            tool,
            status=OperationStatus.VALIDATION_FAILED.value,
            operation_id=str(uuid4()),
            summary="路径安全校验未通过。",
            issues=[issue(exc.code, exc.message, field_name=exc.field_name)],
        )

    @staticmethod
    def _adapter_failure(tool: str) -> dict[str, object]:
        return envelope(
            tool,
            status=OperationStatus.INTERNAL_FAILURE.value,
            operation_id=str(uuid4()),
            summary="MCP 适配层内部处理失败；未公开异常详情。",
            issues=[issue("MCP_INTERNAL_FAILURE", "本地 MCP 处理失败，请查看受控日志。")],
        )

    def get_gateway_capabilities(self) -> dict[str, object]:
        """返回产品、工具和安全能力，不读取用户文件。"""
        try:
            capabilities = self._capabilities.to_dict()
            return envelope(
                "get_gateway_capabilities",
                status=OperationStatus.SUCCESS.value,
                operation_id=str(uuid4()),
                summary="已返回本地网关配置服务能力。",
                product={
                    "name": "davinci-gw-mcp", "version": MCP_VERSION, "transport": "stdio",
                    "build": load_build_info(),
                },
                features=capabilities.get("features", []),
                workflow=[
                    "validate_gateway_inputs", "preview_gateway_update", "generate_gateway_arxml",
                    "diagnose_generation_failure", "start_bug_repair", "get_bug_repair_status",
                    "submit_bug_repair", "cancel_bug_repair",
                ],
                policies={
                    "local_only": True,
                    "absolute_windows_paths_only": True,
                    "network_paths_allowed": False,
                    "overwrite_allowed": False,
                    "generation_requires_preparation_id": True,
                    "max_concurrent_heavy_operations": 1,
                    "max_issues": 200,
                    "max_response_bytes": 512_000,
                    "prepared_session_ttl_seconds": self._capabilities.session_ttl_seconds,
                    "prepared_session_capacity": self._capabilities.session_capacity,
                    "repair_requires_source_repository": True,
                    "repair_start_confirmation": "确认开始工具BUG自动修复",
                    "repair_submit_confirmation": "确认提交工具BUG修复",
                    "repair_recursive_start_allowed": False,
                },
            )
        except Exception as exc:
            LOGGER.error("capabilities adapter failure error_type=%s", type(exc).__name__)
            return self._adapter_failure("get_gateway_capabilities")

    async def validate_gateway_inputs(self, config_path: str, baseline_path: str) -> dict[str, object]:
        """只读校验配置表与基准 ARXML。"""
        tool = "validate_gateway_inputs"
        try:
            config = validate_input_file(config_path, "config_path", ".xlsx")
            baseline = validate_input_file(baseline_path, "baseline_path", ".arxml")
            request = UpdateRequestDto(config, baseline)
            result = await self.runtime.run(
                lambda token: self.facade.validate(request, observer=self._observer, cancellation=token),
            )
            return dto_envelope(
                tool, result,
                "输入校验通过。" if result.status is OperationStatus.SUCCESS else "输入校验未通过。",
                inputs=[{"role": "CONFIG", "path": config}, {"role": "BASELINE", "path": baseline}],
            )
        except PathPolicyError as exc:
            return self._path_failure(tool, exc)
        except Exception as exc:
            LOGGER.error("validate adapter failure error_type=%s", type(exc).__name__)
            return self._adapter_failure(tool)

    async def preview_gateway_update(self, config_path: str, baseline_path: str) -> dict[str, object]:
        """预览并建立一次性 Prepared Session；不写输出文件。"""
        tool = "preview_gateway_update"
        try:
            config = validate_input_file(config_path, "config_path", ".xlsx")
            baseline = validate_input_file(baseline_path, "baseline_path", ".arxml")
            request = UpdateRequestDto(config, baseline)
            prepared = await self.runtime.run(
                lambda token: self.facade.preview(request, observer=self._observer, cancellation=token),
            )
            preview = prepared.preview
            public_issues = prepared.issues or (preview.issues if preview else ())
            payload: dict[str, object] = {
                "preparation_id": prepared.session_id,
                "session_state": prepared.session_state.value if prepared.session_state else None,
                "created_at": prepared.created_at,
                "expires_at": prepared.expires_at,
                "features": [item.to_dict() for item in preview.features] if preview else [],
                "input_files": [item.to_dict() for item in preview.input_files] if preview else [],
                "target_version": preview.target_version if preview else None,
            }
            if prepared.status is OperationStatus.SUCCESS and prepared.session_id:
                with self._lock:
                    self._prepared_baselines[prepared.session_id] = baseline
                    self._prepared_baselines.move_to_end(prepared.session_id)
                    while len(self._prepared_baselines) > self._session_capacity:
                        self._prepared_baselines.popitem(last=False)
            return envelope(
                tool,
                status=prepared.status.value,
                operation_id=prepared.operation_id,
                summary=(
                    "预览成功；请让用户确认摘要后，将 preparation_id 交给生成工具。"
                    if prepared.status is OperationStatus.SUCCESS else "预览未通过，未创建可提交会话。"
                ),
                issues=[item.to_dict() for item in public_issues],
                **payload,
            )
        except PathPolicyError as exc:
            return self._path_failure(tool, exc)
        except Exception as exc:
            LOGGER.error("preview adapter failure error_type=%s", type(exc).__name__)
            return self._adapter_failure(tool)

    async def generate_gateway_arxml(self, preparation_id: str, output_path: str) -> dict[str, object]:
        """仅提交已预览会话，且始终禁止覆盖任何现有文件。"""
        tool = "generate_gateway_arxml"
        try:
            try:
                normalized_id = str(UUID(str(preparation_id)))
            except (ValueError, TypeError, AttributeError):
                return envelope(
                    tool, status=OperationStatus.VALIDATION_FAILED.value, operation_id=str(uuid4()),
                    summary="preparation_id 格式无效。",
                    issues=[issue("PREPARATION_ID_INVALID", "必须使用预览工具返回的 preparation_id。",
                                  field_name="preparation_id")],
                )
            with self._lock:
                baseline = self._prepared_baselines.get(normalized_id)
            if baseline is None:
                return envelope(
                    tool, status=OperationStatus.SESSION_MISSING.value, operation_id=str(uuid4()),
                    summary="找不到本 MCP 进程创建的 Prepared Session。",
                    issues=[issue("SESSION_MISSING", "请在当前 MCP 进程中重新执行预览并使用其 preparation_id。")],
                    preparation_id=normalized_id,
                )
            output = validate_new_output(output_path, baseline)
            result = await self.runtime.run(
                lambda token: self.facade.commit_prepared(
                    normalized_id, output, overwrite=False, observer=self._observer, cancellation=token,
                ),
            )
            with self._lock:
                self._prepared_baselines.pop(normalized_id, None)
            return dto_envelope(
                tool, result,
                "新 ARXML 已安全生成。" if result.status is OperationStatus.SUCCESS else "生成未完成。",
                preparation_id=normalized_id,
            )
        except PathPolicyError as exc:
            return self._path_failure(tool, exc)
        except Exception as exc:
            LOGGER.error("generate adapter failure error_type=%s", type(exc).__name__)
            return self._adapter_failure(tool)

    @staticmethod
    def _repair_contract_failure(tool: str, exc: RepairContractError) -> dict[str, object]:
        return envelope(
            tool, status=OperationStatus.VALIDATION_FAILED.value, operation_id=str(uuid4()),
            summary="自动修复流程条件未满足。",
            issues=[issue("REPAIR_CONTRACT_INVALID", str(exc))],
        )

    async def diagnose_generation_failure(
        self,
        config_path: str,
        baseline_path: str,
        repository_path: str,
        python_path: str,
    ) -> dict[str, object]:
        """稳定复现并分类；诊断阶段不创建分支、不写工程文件。"""
        tool = "diagnose_generation_failure"
        try:
            record = await self.repairs.diagnose(
                config_path, baseline_path, repository_path, python_path,
            )
            payload = record.to_payload()
            payload.pop("summary", None)
            return envelope(
                tool, status=OperationStatus.SUCCESS.value, operation_id=str(uuid4()),
                summary=record.summary, **payload, issues=list(record.issues),
            )
        except RepairContractError as exc:
            return self._repair_contract_failure(tool, exc)
        except Exception as exc:
            LOGGER.error("diagnosis adapter failure error_type=%s", type(exc).__name__)
            return self._adapter_failure(tool)

    async def start_bug_repair(self, diagnosis_id: str, confirmation: str) -> dict[str, object]:
        """取得第一次明确确认后启动隔离后台修复。"""
        tool = "start_bug_repair"
        try:
            record = await self.repairs.start(diagnosis_id, confirmation)
            return envelope(
                tool, status=OperationStatus.SUCCESS.value, operation_id=str(uuid4()),
                summary="已启动隔离热修复；正式提交仍需第二次确认。", repair=record.to_payload(),
            )
        except RepairContractError as exc:
            return self._repair_contract_failure(tool, exc)
        except Exception as exc:
            LOGGER.error("repair start adapter failure error_type=%s", type(exc).__name__)
            return self._adapter_failure(tool)

    def get_bug_repair_status(self, repair_id: str) -> dict[str, object]:
        """读取后台修复状态，不推进或提交任务。"""
        tool = "get_bug_repair_status"
        try:
            record = self.repairs.status(repair_id)
            return envelope(
                tool, status=OperationStatus.SUCCESS.value, operation_id=str(uuid4()),
                summary=record.stage, repair=record.to_payload(),
            )
        except RepairContractError as exc:
            return self._repair_contract_failure(tool, exc)
        except Exception as exc:
            LOGGER.error("repair status adapter failure error_type=%s", type(exc).__name__)
            return self._adapter_failure(tool)

    async def submit_bug_repair(
        self,
        repair_id: str,
        confirmation: str,
        mode: str,
        commit_message: str,
        remote_name: str = "origin",
        upstream_repository: str = "",
        fork_owner: str = "",
    ) -> dict[str, object]:
        """取得第二次明确确认后提交；远程动作由 mode 清晰区分。"""
        tool = "submit_bug_repair"
        try:
            record = await self.repairs.submit(
                repair_id, confirmation, mode, commit_message, remote_name, upstream_repository, fork_owner,
            )
            successful = record.state.value == "COMPLETED"
            return envelope(
                tool,
                status=(OperationStatus.SUCCESS.value if successful else OperationStatus.INTERNAL_FAILURE.value),
                operation_id=str(uuid4()), summary=record.stage, repair=record.to_payload(),
            )
        except RepairContractError as exc:
            return self._repair_contract_failure(tool, exc)
        except Exception as exc:
            LOGGER.error("repair submit adapter failure error_type=%s", type(exc).__name__)
            return self._adapter_failure(tool)

    async def cancel_bug_repair(self, repair_id: str, confirmation: str) -> dict[str, object]:
        """明确确认后取消活动任务，保留 worktree 和报告。"""
        tool = "cancel_bug_repair"
        try:
            record = await self.repairs.cancel(repair_id, confirmation)
            return envelope(
                tool, status=OperationStatus.SUCCESS.value, operation_id=str(uuid4()),
                summary=record.stage, repair=record.to_payload(),
            )
        except RepairContractError as exc:
            return self._repair_contract_failure(tool, exc)
        except Exception as exc:
            LOGGER.error("repair cancel adapter failure error_type=%s", type(exc).__name__)
            return self._adapter_failure(tool)

    async def close(self) -> None:
        """取消活动调用，释放 Prepared Session 与工作线程。"""
        self.runtime.cancel_active()
        with self._lock:
            session_ids = tuple(self._prepared_baselines)
            self._prepared_baselines.clear()
        for session_id in session_ids:
            self.facade.discard_prepared(session_id)
        await self.repairs.close()
        await self.runtime.close()
