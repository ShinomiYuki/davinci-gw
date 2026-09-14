"""MCP 薄适配层的路径、会话、取消与输出契约测试。"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from davinci_gw.application import GatewayFacade
from davinci_gw.application.sessions import PreparedSessionStore
from davinci_gw.mcp.mapping import MAX_ISSUES, envelope, issue
from davinci_gw.mcp.paths import PathPolicyError, validate_input_file, validate_new_output
from davinci_gw.mcp.runtime import BoundedFacadeRuntime
from davinci_gw.mcp.server import SERVER_INSTRUCTIONS, build_server
from davinci_gw.mcp.service import GatewayMcpService
from davinci_gw.runtime import OperationCancelled


class _DiagnosisRepairs:
    async def diagnose(self, *_args: object) -> object:
        return SimpleNamespace(
            summary="已确认属于工具 BUG",
            issues=(),
            to_payload=lambda: {
                "diagnosis_id": "diagnosis",
                "classification": "TOOL_BUG",
                "summary": "已确认属于工具 BUG",
                "repair_eligible": True,
            },
        )

    async def close(self) -> None:
        return None


def test_mcp_adapter_has_no_internal_arxml_cli_or_network_dependencies() -> None:
    package = Path(__file__).resolve().parents[2] / "src" / "davinci_gw" / "mcp"
    source = "\n".join(path.read_text(encoding="utf-8") for path in package.glob("*.py"))
    for forbidden in (
        "from lxml", "import lxml", "ArxmlDocument", "MutationPlan",
        "davinci_gw.cli", ".sessions", "socket.", "requests.", "httpx.", "urllib.request",
    ):
        assert forbidden not in source


@pytest.mark.parametrize("value", [
    "relative.xlsx",
    "https://example.invalid/input.xlsx",
    r"\\server\share\input.xlsx",
    r"\\?\C:\input.xlsx",
    r"\\.\pipe\gateway",
    r"C:\temp\input.xlsx:stream",
    r"C:\temp\CON.xlsx",
])
def test_input_path_policy_rejects_non_local_or_device_paths(value: str) -> None:
    with pytest.raises(PathPolicyError):
        validate_input_file(value, "config_path", ".xlsx")


def test_output_policy_requires_new_arxml_and_never_baseline(
    arxml_factory: object, tmp_path: Path,
) -> None:
    baseline = arxml_factory()  # type: ignore[operator]
    with pytest.raises(PathPolicyError) as same:
        validate_new_output(str(baseline), str(baseline))
    assert same.value.code == "BASELINE_OVERWRITE_FORBIDDEN"
    existing = tmp_path / "existing.arxml"
    existing.write_text("existing", encoding="utf-8")
    with pytest.raises(PathPolicyError) as occupied:
        validate_new_output(str(existing), str(baseline))
    assert occupied.value.code == "OUTPUT_EXISTS"
    assert validate_new_output(str(tmp_path / "new.arxml"), str(baseline)).endswith("new.arxml")


def test_output_policy_resolves_directory_link_alias(
    arxml_factory: object, tmp_path: Path,
) -> None:
    real_directory = tmp_path / "real"
    real_directory.mkdir()
    baseline = arxml_factory(filename="real/baseline.arxml")  # type: ignore[operator]
    link = tmp_path / "linked"
    try:
        link.symlink_to(real_directory, target_is_directory=True)
    except OSError:
        pytest.skip("当前 Windows 权限不允许创建目录符号链接")
    with pytest.raises(PathPolicyError) as aliased:
        validate_new_output(str(link / baseline.name), str(baseline))
    assert aliased.value.code == "BASELINE_OVERWRITE_FORBIDDEN"


def test_issue_envelope_is_error_first_and_bounded() -> None:
    issues = [
        {**issue(f"W{index}", "warning"), "severity": "WARNING"}
        for index in range(MAX_ISSUES + 20)
    ]
    issues.append(issue("E", "error"))
    result = envelope("test", status="VALIDATION_FAILED", operation_id="op", summary="failed", issues=issues)
    assert result["issues"][0]["code"] == "E"
    assert len(result["issues"]) == MAX_ISSUES
    assert result["issue_count"] == MAX_ISSUES + 21
    assert result["issues_truncated"] is True
    assert len(json.dumps(result, ensure_ascii=False).encode("utf-8")) <= 512_000


def test_server_registers_exact_tools_and_safety_annotations() -> None:
    # 说明必须在 Codex 读取的前 512 字符内自包含完整顺序与安全条件。
    assert len(SERVER_INSTRUCTIONS) <= 512
    for text in ("validate_gateway_inputs", "preview_gateway_update", "generate_gateway_arxml",
                 "preparation_id", "绝对路径", "禁止"):
        assert text in SERVER_INSTRUCTIONS[:512]

    async def list_only() -> None:
        server = build_server()
        tools = {tool.name: tool for tool in await server.list_tools()}
        assert set(tools) == {
            "get_gateway_capabilities", "validate_gateway_inputs",
            "preview_gateway_update", "generate_gateway_arxml",
            "diagnose_generation_failure", "start_bug_repair", "get_bug_repair_status",
            "submit_bug_repair", "cancel_bug_repair",
        }
        assert tools["get_gateway_capabilities"].annotations.read_only_hint is True
        assert tools["validate_gateway_inputs"].annotations.idempotent_hint is True
        assert tools["preview_gateway_update"].annotations.idempotent_hint is False
        assert tools["generate_gateway_arxml"].annotations.destructive_hint is True
        assert tools["diagnose_generation_failure"].annotations.read_only_hint is True
        assert tools["diagnose_generation_failure"].annotations.open_world_hint is True
        assert tools["start_bug_repair"].annotations.destructive_hint is True
        assert tools["submit_bug_repair"].annotations.destructive_hint is True
        assert tools["cancel_bug_repair"].annotations.destructive_hint is True
        assert tools["get_bug_repair_status"].annotations.open_world_hint is False
        required = {
            "schema_version", "tool", "operation_id", "status", "success", "summary",
            "issue_count", "issues_truncated", "issues",
        }
        assert all(required <= set(tool.output_schema["required"]) for tool in tools.values())

    asyncio.run(list_only())


def test_diagnosis_result_keeps_summary_without_adapter_failure() -> None:
    async def scenario() -> None:
        service = GatewayMcpService(repairs=_DiagnosisRepairs())  # type: ignore[arg-type]
        try:
            result = await service.diagnose_generation_failure("a", "b", "c", "d")
            assert result["status"] == "SUCCESS"
            assert result["summary"] == "已确认属于工具 BUG"
            assert result["classification"] == "TOOL_BUG"
        finally:
            await service.close()

    asyncio.run(scenario())


def test_service_requires_preview_then_generates_once(
    workbook_factory: object, arxml_factory: object, tmp_path: Path,
) -> None:
    config = workbook_factory()  # type: ignore[operator]
    baseline = arxml_factory()  # type: ignore[operator]
    output = tmp_path / "mcp_generated.arxml"

    async def scenario() -> None:
        service = GatewayMcpService()
        try:
            missing = await service.generate_gateway_arxml("00000000-0000-0000-0000-000000000001", str(output))
            assert missing["status"] == "SESSION_MISSING"
            validated = await service.validate_gateway_inputs(str(config), str(baseline))
            assert validated["status"] == "SUCCESS"
            prepared = await service.preview_gateway_update(str(config), str(baseline))
            assert prepared["status"] == "SUCCESS"
            preparation_id = prepared["preparation_id"]
            assert preparation_id
            generated = await service.generate_gateway_arxml(str(preparation_id), str(output))
            assert generated["status"] == "SUCCESS"
            assert generated["artifact"]["path"] == str(output.resolve())
            assert output.is_file()
            consumed = await service.generate_gateway_arxml(str(preparation_id), str(tmp_path / "again.arxml"))
            assert consumed["status"] == "SESSION_MISSING"
            assert "<AUTOSAR" not in json.dumps(generated, ensure_ascii=False)
        finally:
            await service.close()

    asyncio.run(scenario())


def test_runtime_bridges_async_cancellation_to_core_token() -> None:
    runtime = BoundedFacadeRuntime()
    started = threading.Event()
    cancelled = threading.Event()

    def blocking(token: object) -> str:
        started.set()
        while True:
            try:
                token.checkpoint("test")  # type: ignore[attr-defined]
            except OperationCancelled:
                cancelled.set()
                return "cancelled"
            time.sleep(0.005)

    async def scenario() -> None:
        task = asyncio.create_task(runtime.run(blocking))
        assert await asyncio.to_thread(started.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cancelled.wait(2)
        await runtime.close()

    asyncio.run(scenario())


def test_runtime_executes_at_most_one_heavy_operation() -> None:
    runtime = BoundedFacadeRuntime()
    lock = threading.Lock()
    active = 0
    maximum = 0

    def operation(_token: object) -> str:
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        time.sleep(0.05)
        with lock:
            active -= 1
        return "ok"

    async def scenario() -> None:
        assert await asyncio.gather(runtime.run(operation), runtime.run(operation)) == ["ok", "ok"]
        await runtime.close()

    asyncio.run(scenario())
    assert maximum == 1


def test_adapter_prepared_metadata_uses_public_session_capacity(
    workbook_factory: object, arxml_factory: object,
) -> None:
    config = workbook_factory()  # type: ignore[operator]
    baseline = arxml_factory()  # type: ignore[operator]

    async def scenario() -> None:
        facade = GatewayFacade(session_store=PreparedSessionStore(max_sessions=2))
        service = GatewayMcpService(facade=facade)
        try:
            preparation_ids = []
            for _ in range(3):
                result = await service.preview_gateway_update(str(config), str(baseline))
                assert result["status"] == "SUCCESS"
                preparation_ids.append(result["preparation_id"])
            assert len(service._prepared_baselines) == 2
            assert preparation_ids[0] not in service._prepared_baselines
        finally:
            await service.close()

    asyncio.run(scenario())


def test_all_tools_work_when_network_apis_are_blocked(
    workbook_factory: object, arxml_factory: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = workbook_factory()  # type: ignore[operator]
    baseline = arxml_factory()  # type: ignore[operator]
    output = tmp_path / "network_blocked.arxml"

    def blocked(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("MCP 工具不得调用网络 API")

    async def scenario() -> None:
        service = GatewayMcpService()
        try:
            monkeypatch.setattr(socket, "create_connection", blocked)
            monkeypatch.setattr(socket, "getaddrinfo", blocked)
            assert service.get_gateway_capabilities()["status"] == "SUCCESS"
            assert (await service.validate_gateway_inputs(str(config), str(baseline)))["status"] == "SUCCESS"
            preview = await service.preview_gateway_update(str(config), str(baseline))
            assert preview["status"] == "SUCCESS"
            generated = await service.generate_gateway_arxml(
                str(preview["preparation_id"]), str(output),
            )
            assert generated["status"] == "SUCCESS"
        finally:
            await service.close()

    asyncio.run(scenario())
