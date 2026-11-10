"""桥接命令行的真实启动路径。

这里刻意起一个子进程执行 ``python -m davinci_gw.bridge``：命令行入口
要自己把 ASGI 应用装好交给 Uvicorn，任何一处名字没接上（例如应用构建
函数没导入）都会在启动阶段直接抛 ``NameError``，而不是安静地跑起来。
只在进程内调用函数是发现不了这类问题的。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest


def _wait_for_descriptor(path: Path, process: subprocess.Popen[str], timeout: float = 60.0) -> dict:
    """等待发现文件出现并返回其内容。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass
        if process.poll() is not None:
            output = process.stdout.read() if process.stdout else ""
            raise AssertionError(f"桥接进程提前退出（返回码 {process.returncode}）：\n{output}")
        time.sleep(0.1)
    raise AssertionError("等待桥接服务写出发现文件超时。")


@pytest.mark.parametrize("core", ["embedded", "http"])
def test_cli_startup_builds_the_app_and_writes_the_descriptor(
    core: str, tmp_path: Path,
) -> None:
    """命令行启动必须真的把服务跑起来并写出发现文件。

    http 模式的核心里程碑不可达也没关系：服务本身照样要能起来、
    监听端口并交付发现文件，这条路径不依赖核心是否可用。
    """
    project = Path(__file__).resolve().parents[2]
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(project / "src")
    descriptor = tmp_path / "bridge.json"
    command = [
        sys.executable, "-m", "davinci_gw.bridge",
        "--core", core,
        "--core-target", "http://127.0.0.1:9/mcp",
        "--host", "127.0.0.1",
        "--port", "0",
        "--token", "cli-startup-token",
        "--descriptor", str(descriptor),
    ]
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        env=environment,
    )
    try:
        payload = _wait_for_descriptor(descriptor, process)
        # 发现文件里的端口必须是实际绑定的端口，不能是配置里的 0。
        assert payload["port"] > 0
        assert payload["url"].endswith(f":{payload['port']}/mcp")
        assert payload["token"] == "cli-startup-token"
        assert payload["core"]["mode"] == core
    finally:
        process.terminate()
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:  # pragma: no cover - 极端情况下的兜底
            process.kill()
            process.wait(timeout=30)
