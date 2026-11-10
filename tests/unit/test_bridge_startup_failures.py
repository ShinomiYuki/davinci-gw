"""桥接服务启动失败时的退出行为回归。

两条断言都落在真实可观测点上：

1. 端口被占用：起一个真实的 ``python -m davinci_gw.bridge`` 子进程，
   让它去绑定一个已被占用的端口，要求进程干净退出（返回码非 0、
   给出中文错误说明、不抛 traceback）。
2. 发现文件写不出去：``--descriptor`` 指向一个已存在的目录，
   要求同样干净退出。

上一轮只修了第 2 条，第 1 条的启动失败路径仍会抛 traceback：
``_serve`` 等待服务启动时用 ``task.result()`` 取结果，而这个调用会把
服务启动阶段的异常原样重抛。
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parents[2]


def _run_bridge(*extra: str, timeout: float = 180.0) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(PROJECT / "src")
    return subprocess.run(
        [sys.executable, "-m", "davinci_gw.bridge", "--core", "embedded", *extra],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, cwd=str(PROJECT), env=environment,
    )


def _assert_clean_exit(completed: subprocess.CompletedProcess[str]) -> None:
    stderr = completed.stderr
    assert "Traceback (most recent call last)" not in stderr, stderr
    assert completed.returncode != 0, f"启动失败却以 0 退出：\n{stderr}"


def test_bridge_exits_cleanly_when_the_port_is_already_taken() -> None:
    """端口被占用时 Bridge 要干净退出，不能抛 traceback。

    端口占用是常见的启动失败（上一个 Bridge 还在跑、端口被别的服务
    占了）。使用者需要看到一句中文说明，而不是一整段 Python traceback。
    """
    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    holder.bind(("127.0.0.1", 0))
    holder.listen(1)
    port = holder.getsockname()[1]
    try:
        completed = _run_bridge(
            "--port", str(port),
            "--token", "port-conflict-token",
            "--no-descriptor",
        )
    finally:
        holder.close()

    _assert_clean_exit(completed)
    assert "桥接配置错误" in completed.stderr or "端口" in completed.stderr, completed.stderr


def test_port_probe_does_not_mistake_ipv6_for_an_occupied_port() -> None:
    """IPv6 监听地址不能被误判成「端口被占用」。

    端口预探测用 socket 绑定来试。若对 ``[::1]`` 用了 IPv4 的
    地址族，绑定必然失败，Bridge 就会把一个完全正常的 IPv6 启动
    拦下来并报「端口已被占用」——这是误伤，比漏报更糟。
    """
    from davinci_gw.bridge.__main__ import _probe_port_available

    # IPv6 回环（配置里带方括号）必须被认成可用。
    assert _probe_port_available("[::1]", 0) is True
    # 认不出地址族的主机名不拦。
    assert _probe_port_available("localhost", 0) is True
    # 真正被占用的 IPv4 端口要能被认出来。
    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    holder.bind(("127.0.0.1", 0))
    holder.listen(1)
    try:
        assert _probe_port_available("127.0.0.1", holder.getsockname()[1]) is False
    finally:
        holder.close()


def test_wildcard_bind_still_starts_when_a_specific_address_holds_the_port() -> None:
    """通配绑定时，端口被具体地址占用也必须能正常起来。

    Windows 允许通配地址与具体地址共存：``127.0.0.1:port`` 被占用
    时，绑 ``0.0.0.0:port`` 照样成功。端口预探测在通配模式下因此
    查不出「冲突」——但这不是缺陷：那个端口本来就可用，Uvicorn 能
    绑上。

    这条用例把该结论固定下来：将来若有人把预探测改成「查不出冲突
    就报错」，这里会失败。
    """
    from davinci_gw.bridge.__main__ import _probe_port_available

    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    holder.bind(("127.0.0.1", 0))
    holder.listen(1)
    port = holder.getsockname()[1]
    try:
        # 预探测在通配模式下查不出冲突（这是 Windows 的语义，不是 bug）。
        assert _probe_port_available("0.0.0.0", port) is True
        # 而真正绑定通配地址是允许的。
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as wildcard:
            wildcard.bind(("0.0.0.0", port))
    finally:
        holder.close()


def test_bridge_exits_cleanly_when_the_descriptor_cannot_be_written(tmp_path: Path) -> None:
    """发现文件写不出去时 Bridge 要干净退出，不能抛 traceback。

    端口已经监起来了，发现文件却写不出去，客户端拿不到令牌——
    这种半启动状态对使用者毫无意义。进程必须给出明确的中文错误
    说明并以配置错误码（2）退出，而不是带着一段 traceback 崩掉。

    触发条件：``--descriptor`` 指向一个已存在的目录，``os.replace``
    必然失败。
    """
    occupied = tmp_path / "occupied"
    occupied.mkdir()

    completed = _run_bridge("--descriptor", str(occupied), "--port", "0")

    stderr = completed.stderr
    assert "Traceback (most recent call last)" not in stderr, stderr
    assert "桥接配置错误" in stderr, stderr
    assert "无法写入客户端发现文件" in stderr, stderr
    assert completed.returncode == 2, f"退出码 {completed.returncode}\n{stderr}"


@pytest.mark.parametrize("core", ["embedded", "http"])
def test_bridge_still_starts_normally(core: str, tmp_path: Path) -> None:
    """正常启动路径不能被这条修复带坏。

    端口为 0 时由系统分配，发现文件里给出的端口必须是实际绑定的端口，
    且服务要真的能起来。
    """
    import json
    import time

    descriptor = tmp_path / f"{core}.json"
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(PROJECT / "src")
    process = subprocess.Popen(
        [
            sys.executable, "-m", "davinci_gw.bridge",
            "--core", core,
            "--core-target", "http://127.0.0.1:9/mcp",
            "--host", "127.0.0.1",
            "--port", "0",
            "--token", "startup-ok-token",
            "--descriptor", str(descriptor),
        ],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", env=environment,
    )
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if descriptor.exists():
                break
            if process.poll() is not None:
                output = process.stdout.read() if process.stdout else ""
                raise AssertionError(f"桥接进程提前退出（返回码 {process.returncode}）：\n{output}")
            time.sleep(0.1)
        else:
            raise AssertionError("等待桥接服务写出发现文件超时。")
        payload = json.loads(descriptor.read_text(encoding="utf-8"))
        assert payload["port"] > 0
        assert payload["token"] == "startup-ok-token"
    finally:
        process.terminate()
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:  # pragma: no cover - 极端情况下的兜底
            process.kill()
            process.wait(timeout=30)
