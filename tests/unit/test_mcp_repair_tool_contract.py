"""MCP 修复类工具的路径策略与重任务闸门契约测试。

这两条公开承诺此前只在生成类工具上落实：

* ``policies.network_paths_allowed=False`` / ``absolute_windows_paths_only=True``
  —— 修复类工具的四个路径参数必须走同一套本地路径策略；
* ``policies.max_concurrent_heavy_operations=1`` —— 诊断里的预览不得与
  生成流程并行占用同一个重任务名额。
"""

from __future__ import annotations

import asyncio
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

from davinci_gw.application.facade import GatewayFacade
from davinci_gw.contracts import OperationStatus, PreparedSessionDto
from davinci_gw.mcp.service import GatewayMcpService

REPOSITORY = Path(__file__).resolve().parents[2]


class _RecordingRepairs:
    """记录诊断调用；路径策略失守时它会被真的调用到。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    async def diagnose(self, *args: str) -> object:
        self.calls.append(args)
        return SimpleNamespace(
            summary="已确认属于工具 BUG",
            issues=(),
            to_payload=lambda: {"diagnosis_id": "diagnosis", "summary": "已确认属于工具 BUG"},
        )

    async def close(self) -> None:
        return None


class _BlockingFacade(GatewayFacade):
    """拦截 preview：记录并发度并阻塞，用于观察重任务名额。"""

    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()
        self.maximum = 0
        self._active = 0
        self._lock = threading.Lock()

    def preview(self, request, *, observer=None, cancellation=None):  # type: ignore[override]
        with self._lock:
            self._active += 1
            self.maximum = max(self.maximum, self._active)
        self.entered.set()
        try:
            self.release.wait(20)
        finally:
            with self._lock:
                self._active -= 1
        return PreparedSessionDto("op", OperationStatus.SUCCESS, "session")


def _local_inputs(tmp_path: Path) -> dict[str, str]:
    """准备一组合法的本地路径：配置表、基准、源码仓库和解释器。"""
    config = tmp_path / "routes.xlsx"
    baseline = tmp_path / "baseline.arxml"
    config.write_bytes(b"xlsx")
    baseline.write_text("<AUTOSAR/>", encoding="utf-8")
    repository = tmp_path / "repo"
    (repository / ".git").mkdir(parents=True)
    (repository / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    return {
        "config_path": str(config),
        "baseline_path": str(baseline),
        "repository_path": str(repository),
        "python_path": sys.executable,
    }


def _device(path: str) -> str:
    r"""同一文件的 ``\?\`` 设备路径；文件真实存在，不能靠「不存在」被顺带拦下。"""
    return "\\\\?\\" + path


def test_repair_tool_rejects_non_local_paths(tmp_path: Path) -> None:
    """设备路径和 UNC 路径必须在进入协调器之前就被本地路径策略拦下。"""
    local = _local_inputs(tmp_path)
    cases = {
        "config_path": _device(local["config_path"]),
        "baseline_path": _device(local["baseline_path"]),
        "repository_path": _device(local["repository_path"]),
        "python_path": _device(local["python_path"]),
        "config_path_unc": r"\\server\share\routes.xlsx",
        "baseline_path_unc": r"\\server\share\baseline.arxml",
        "repository_path_unc": r"\\server\share\repo",
        "python_path_unc": r"\\server\share\python.exe",
    }
    for field, value in cases.items():
        argument = field.removesuffix("_unc")
        repairs = _RecordingRepairs()
        service = GatewayMcpService(repairs=repairs)  # type: ignore[arg-type]

        async def scenario() -> dict[str, object]:
            arguments = dict(local)
            arguments[argument] = value
            try:
                return await service.diagnose_generation_failure(
                    arguments["config_path"], arguments["baseline_path"],
                    arguments["repository_path"], arguments["python_path"],
                )
            finally:
                await service.close()

        result = asyncio.run(scenario())
        assert result["status"] == "VALIDATION_FAILED", field
        codes = [str(item.get("code")) for item in result["issues"]]  # type: ignore[union-attr]
        assert codes == ["LOCAL_PATH_REQUIRED"], (field, codes)
        assert repairs.calls == [], f"{field} 未在边界被拦下，诊断协调器已被调用"


def test_repair_tool_accepts_local_paths(tmp_path: Path) -> None:
    """合法的本地路径必须照旧通过，并把规范化后的路径交给协调器。"""
    local = _local_inputs(tmp_path)
    repairs = _RecordingRepairs()
    service = GatewayMcpService(repairs=repairs)  # type: ignore[arg-type]

    async def scenario() -> dict[str, object]:
        try:
            return await service.diagnose_generation_failure(
                local["config_path"], local["baseline_path"],
                local["repository_path"], local["python_path"],
            )
        finally:
            await service.close()

    result = asyncio.run(scenario())
    assert result["status"] == "SUCCESS"
    assert len(repairs.calls) == 1
    assert repairs.calls[0] == (
        local["config_path"], local["baseline_path"],
        local["repository_path"], local["python_path"],
    )


def test_diagnosis_does_not_evict_user_prepared_sessions(
    workbook_factory: object, arxml_factory: object, tmp_path: Path,
) -> None:
    """诊断不得占用用户的 Prepared Session 容量，把用户最早的一条挤掉。

    诊断内部也要做一次真实预览，会建立一条 Prepared Session。若它与用户
    共用同一个存储，用户攒满容量后的一次诊断就会挤掉最早的那条，
    那条 preparation_id 随即变成「已被容量淘汰」。
    """
    config = workbook_factory()  # type: ignore[operator]
    baseline = arxml_factory()  # type: ignore[operator]
    capacity = 8

    async def scenario() -> None:
        service = GatewayMcpService()
        try:
            preparation_ids = []
            for _ in range(capacity):
                result = await service.preview_gateway_update(str(config), str(baseline))
                assert result["status"] == "SUCCESS"
                preparation_ids.append(result["preparation_id"])
            diagnosis = await service.diagnose_generation_failure(
                str(config), str(baseline), str(REPOSITORY), sys.executable,
            )
            assert diagnosis["status"] == "SUCCESS"
            output = tmp_path / "still_alive.arxml"
            generated = await service.generate_gateway_arxml(preparation_ids[0], str(output))
            assert generated["status"] == "SUCCESS", generated
            assert output.is_file()
        finally:
            await service.close()

    asyncio.run(scenario())


def test_diagnosis_shares_the_single_heavy_operation_slot(tmp_path: Path) -> None:
    """诊断的预览必须与生成预览互斥，不能并行占住两份 ARXML。"""
    local = _local_inputs(tmp_path)
    facade = _BlockingFacade()
    service = GatewayMcpService(facade=facade)

    async def scenario() -> None:
        try:
            preview_task = asyncio.create_task(service.preview_gateway_update(
                local["config_path"], local["baseline_path"],
            ))
            assert await asyncio.to_thread(facade.entered.wait, 10), "预览没有进入重任务"
            diagnosis_task = asyncio.create_task(service.diagnose_generation_failure(
                local["config_path"], local["baseline_path"],
                str(REPOSITORY), sys.executable,
            ))
            await asyncio.sleep(1.0)
            assert facade.maximum == 1, "诊断与生成预览并行占用了重任务名额"
            assert not diagnosis_task.done()
            facade.release.set()
            assert (await preview_task)["status"] == "SUCCESS"
            assert (await diagnosis_task)["status"] == "SUCCESS"
            assert facade.maximum == 1
        finally:
            facade.release.set()
            await service.close()

    asyncio.run(scenario())
