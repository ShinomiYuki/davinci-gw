"""``--core-env`` 指定的环境变量必须真的到达核心子进程。

这条链路以前只有代码、没有验证：命令行收集 ``KEY=VALUE``，一路传到
``StdioServerParameters.env``，中间任何一环写错，核心拿到的环境都只
会静默地不对——桥接照样起来，调用照样有响应，问题却很难查。

这里用 MCP 核心真实读取的 ``DAVINCI_GW_LOG_DIR`` 做可观测点：指定的
目录里出现 ``mcp.log``，就说明变量确实进了核心进程。
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import pytest

from davinci_gw.bridge.__main__ import _parse_env, main
from davinci_gw.bridge.config import BridgeConfigError
from davinci_gw.bridge.core import StdioCore


def _wait_for(path: Path, timeout: float = 30.0) -> bool:
    """轮询等待文件出现。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return True
        time.sleep(0.05)
    return False


def test_core_env_reaches_the_stdio_core_process(tmp_path: Path) -> None:
    """指定的环境变量必须出现在核心子进程里。"""
    project = Path(__file__).resolve().parents[2]
    log_dir = tmp_path / "core-logs"
    environment = {
        # 核心靠这两个变量工作：前者让它能 import 到源码包，后者让它
        # 把日志写到我们指定的目录——后者就是可观测点。
        "PYTHONPATH": str(project / "src"),
        "DAVINCI_GW_LOG_DIR": str(log_dir),
    }
    core = StdioCore(sys.executable, ("-m", "davinci_gw.mcp"), environment, timeout_seconds=60.0)

    async def scenario() -> None:
        try:
            await core.start()
            assert _wait_for(log_dir / "mcp.log"), "核心没有按指定目录写日志，环境变量没到位"
        finally:
            await core.aclose()

    asyncio.run(scenario())


def test_core_env_parsing_keeps_values_containing_equal_signs() -> None:
    """``KEY=VALUE`` 里值本身带等号时不能被截断。"""
    assert _parse_env(["A=1", "B=2"]) == {"A": "1", "B": "2"}
    assert _parse_env(["A=x=y"]) == {"A": "x=y"}
    assert _parse_env(["A="]) == {"A": ""}


def test_invalid_core_env_is_rejected_before_start() -> None:
    """格式非法的环境变量必须在启动前被拦住。"""
    with pytest.raises(BridgeConfigError):
        _parse_env(["NOEQUALS"])
    with pytest.raises(BridgeConfigError):
        _parse_env(["=novalue"])
    # 命令行层同样要拦住，且不能把服务真的拉起来。
    assert main(["--core", "embedded", "--core-env", "NOEQUALS"]) == 2
