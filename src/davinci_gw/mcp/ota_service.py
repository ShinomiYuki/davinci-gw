"""OTA 入口共用现有本地路径限制和串行任务执行器。"""

from __future__ import annotations

from collections import OrderedDict
import logging
import json
from uuid import uuid4

from davinci_gw.application.ota import OtaService
from davinci_gw.mcp.paths import PathPolicyError, validate_input_file, validate_new_output
from davinci_gw.mcp.runtime import BoundedFacadeRuntime
from davinci_gw.mcp.mapping import MAX_RESPONSE_BYTES, envelope, issue

LOGGER = logging.getLogger("davinci_gw.mcp.ota")


class OtaMcpService:
    def __init__(self, runtime: BoundedFacadeRuntime, core=None) -> None:
        self.runtime = runtime
        self.core = core or OtaService()
        self._baselines: OrderedDict[str, str] = OrderedDict()
        self._previews: dict[str, dict] = {}
        self._next_offsets: dict[str, int] = {}

    async def invoke(self, operation: str, **arguments) -> dict:
        def execute(_token) -> dict:
            try:
                if operation in {"check", "preview"}:
                    baseline = validate_input_file(arguments["baseline_path"], "baseline_path", ".arxml")
                    preparation_id = arguments.get("preparation_id", "")
                    offset = arguments.get("offset", 0)
                    if operation == "preview" and preparation_id:
                        if self._baselines.get(preparation_id) != baseline:
                            raise ValueError("预览会话不存在或基准路径不一致。")
                        result = self._previews[preparation_id]
                    else:
                        if offset:
                            raise ValueError("首次预览必须从 offset=0 开始。")
                        result = getattr(self.core, operation)(baseline)
                    if operation == "preview" and result.get("preparation_id"):
                        preparation_id = result["preparation_id"]
                        self._baselines[preparation_id] = baseline
                        self._previews[preparation_id] = result
                        while len(self._baselines) > 8:
                            expired, _ = self._baselines.popitem(last=False)
                            self._previews.pop(expired, None)
                            self._next_offsets.pop(expired, None)
                        expected = self._next_offsets.get(preparation_id, 0)
                        if offset != expected:
                            raise ValueError(f"请按顺序获取完整预览，下一页 offset={expected}。")
                        total = len(result.get("changes", []))
                        end = min(offset + 100, total)
                        page = dict(result, changes=result.get("changes", [])[offset:end],
                                    change_count=total, offset=offset,
                                    next_offset=end if end < total else None,
                                    preview_complete=end == total)
                        if len(json.dumps(page, ensure_ascii=False).encode("utf-8")) > MAX_RESPONSE_BYTES - 20_000:
                            raise ValueError("本页精确预览超过响应限制，请使用 CLI 或 GUI 查看。")
                        self._next_offsets[preparation_id] = end
                        return page
                    if operation == "check":
                        return dict(result, changes=[], change_count=len(result.get("changes", [])),
                                    note="检查仅返回缺漏摘要；调用 preview_ota_update 获取完整精确变更。")
                    return result
                preparation_id = arguments["preparation_id"]
                if not arguments.get("confirmed"):
                    raise ValueError("必须先向用户展示预览并取得明确确认。")
                if preparation_id not in self._baselines:
                    raise ValueError("预览会话不存在，请在当前服务重新预览。")
                if self._next_offsets.get(preparation_id, 0) < len(self._previews[preparation_id].get("changes", [])):
                    raise ValueError("完整预览尚未获取，请先按 next_offset 获取并展示剩余变更。")
                output = validate_new_output(arguments["output_path"], self._baselines[preparation_id])
                result = self.core.generate(preparation_id, output, confirmed=True, cancellation=_token)
                self._baselines.pop(preparation_id, None)
                self._previews.pop(preparation_id, None)
                self._next_offsets.pop(preparation_id, None)
                return dict(result, changes=[], change_count=len(result.get("changes", [])))
            except (PathPolicyError, ValueError) as exc:
                return {"can_generate": False, "issues": [issue("OTA_VALIDATION_FAILED", str(exc))]}
            except Exception:
                LOGGER.exception("OTA operation failed")
                return {"can_generate": False, "issues": [issue("OTA_INTERNAL_FAILURE", "OTA 操作失败，请查看本地日志并重新检查输入。")], "internal_failure": True}

        result = await self.runtime.run(execute)
        result = dict(result)
        issues = result.pop("issues", [])
        issues = [entry if isinstance(entry, dict) else issue("OTA_VALIDATION_FAILED", str(entry)) for entry in issues]
        status = "INTERNAL_FAILURE" if result.pop("internal_failure", False) else "VALIDATION_FAILED" if issues else "SUCCESS"
        tool = {"check": "check_ota_configuration", "preview": "preview_ota_update", "generate": "generate_ota_arxml"}[operation]
        response = envelope(tool, status=status, operation_id=str(uuid4()), summary="OTA 配置操作结果。", issues=issues)
        response.update(result)
        if len(json.dumps(response, ensure_ascii=False).encode("utf-8")) > MAX_RESPONSE_BYTES:
            return envelope(tool, status="VALIDATION_FAILED", operation_id=str(uuid4()),
                            summary="预览页超过响应限制，请使用 CLI 或 GUI 查看完整预览。",
                            issues=[issue("OTA_PREVIEW_TOO_LARGE", "精确内容未截断；当前响应超出安全限制。")])
        return response
