"""桥接命令行的配置校验与客户端发现文件。"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path

import pytest

from davinci_gw.bridge.__main__ import (
    _current_user_sid,
    _discovery_token_error,
    _split_command,
    _write_descriptor,
    main,
)


def test_split_command_keeps_windows_paths_and_leading_dashes() -> None:
    command, args = _split_command('"C:\\tools\\davinci-gw-mcp.exe" -m davinci_gw.mcp')
    assert command == "C:\\tools\\davinci-gw-mcp.exe"
    assert args == ["-m", "davinci_gw.mcp"]


def test_invalid_configuration_is_rejected_before_start(capsys: object) -> None:
    code = main(["--core", "stdio", "--host", "0.0.0.0"])
    assert code == 2


def test_missing_core_target_is_rejected(capsys: object) -> None:
    assert main(["--core", "http"]) == 2


def test_remote_listener_without_tls_is_rejected(capsys: object) -> None:
    """对外监听必须加密，命令行层也要拦住明文远端。"""
    assert main([
        "--core", "stdio", "--core-target", "davinci-gw-mcp.exe",
        "--host", "0.0.0.0", "--allow-remote",
    ]) == 2
    # 只给证书不给私钥同样不成立。
    assert main([
        "--core", "stdio", "--core-target", "davinci-gw-mcp.exe",
        "--host", "0.0.0.0", "--allow-remote", "--tls-certfile", "cert.pem",
    ]) == 2


def test_no_descriptor_requires_an_explicit_token(capsys: object) -> None:
    """关掉发现文件就必须显式给令牌。

    令牌缺省时随机生成，只写在发现文件里；没有发现文件时客户端就无从
    构造 Authorization 头，每次调用都会鉴权失败。
    """
    assert _discovery_token_error(True, None) is not None
    assert _discovery_token_error(True, "fixed-token") is None
    assert _discovery_token_error(False, None) is None
    # 命令行层同样要拦住。
    assert main(["--core", "embedded", "--no-descriptor"]) == 2


def test_descriptor_file_is_written_for_client_discovery(tmp_path: Path) -> None:
    path = tmp_path / "bridge.json"
    _write_descriptor(path, {"url": "http://127.0.0.1:8765/mcp", "token": "t"})
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["url"] == "http://127.0.0.1:8765/mcp"
    assert payload["token"] == "t"


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL 语义")
def test_descriptor_file_is_readable_only_by_the_current_user(tmp_path: Path) -> None:
    """发现文件里有明文令牌，其它本地账号不能读到。

    ``os.chmod(path, 0o600)`` 在 Windows 上只改只读属性，不建立
    owner-only 的 DACL——实测改完之后文件仍是 0o666，别的账号照样能读。
    这里用 icacls 核对真实的访问控制表。
    """
    path = tmp_path / "bridge.json"
    _write_descriptor(path, {"url": "http://127.0.0.1:8765/mcp", "token": "t"})

    acl = _acl_entries(path)
    # 必须恰好剩下一条授权：当前用户自己。
    assert len(acl) == 1, acl
    # 继承项必须清干净，否则其它账号仍能读到令牌。
    assert not any("(I)" in entry for entry in acl), acl
    assert not any("Everyone" in entry for entry in acl), acl
    assert not any("BUILTIN\\Users" in entry for entry in acl), acl
    # 内容本身仍要可读，否则客户端拿不到令牌。
    assert json.loads(path.read_text(encoding="utf-8"))["token"] == "t"


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL 语义")
def test_descriptor_acl_is_idempotent_across_restarts(tmp_path: Path) -> None:
    """同一个发现文件路径重复收紧必须都成功。

    Bridge 重启会复用同一个发现文件路径，收紧动作必须可重复执行：
    之前用 PowerShell ``Set-Acl`` 时，第二次收紧会因 ``SetOwner``
    需要 ``SeSecurityPrivilege`` 而失败，fail-closed 之下 Bridge
    重启直接起不来。
    """
    path = tmp_path / "bridge.json"
    for _ in range(3):
        _write_descriptor(path, {"url": "http://127.0.0.1:8765/mcp", "token": "t"})
        acl = _acl_entries(path)
        assert len(acl) == 1, acl
        assert not any("(I)" in entry for entry in acl), acl
    assert json.loads(path.read_text(encoding="utf-8"))["token"] == "t"


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL 语义")
def test_descriptor_write_succeeds_while_a_client_holds_the_file(tmp_path: Path) -> None:
    """客户端正拿着发现文件时，Bridge 重启仍要能写出新的发现文件。

    Windows 的 ``MoveFileEx`` 要求目标文件没有打开的句柄，客户端正在
    读发现文件时替换会报 ``WinError 5``。写入必须重试到句柄释放为止，
    不能让 Bridge 直接起不来。
    """
    path = tmp_path / "bridge.json"
    _write_descriptor(path, {"url": "http://127.0.0.1:8765/mcp", "token": "first"})
    handle = path.open("r", encoding="utf-8")

    def release() -> None:
        time.sleep(0.2)
        handle.close()

    releaser = threading.Thread(target=release)
    releaser.start()
    try:
        _write_descriptor(path, {"url": "http://127.0.0.1:8765/mcp", "token": "second"})
    finally:
        releaser.join(timeout=10)
        if not handle.closed:
            handle.close()
    assert json.loads(path.read_text(encoding="utf-8"))["token"] == "second"
    # 替换后不留临时文件。
    assert sorted(item.name for item in tmp_path.iterdir()) == ["bridge.json"]


@pytest.mark.skipif(os.name != "nt", reason="Windows 只读属性语义")
def test_descriptor_write_succeeds_when_the_previous_file_is_read_only(tmp_path: Path) -> None:
    """上一次留下的发现文件带只读属性时，Bridge 重启仍要能写出新的。

    Windows 上带只读属性的文件不能被替换（``MoveFileEx`` 报
    ``WinError 5``）。发现文件由程序自己维护，用户手动设的只读
    不应该让 Bridge 起不来。
    """
    path = tmp_path / "bridge.json"
    _write_descriptor(path, {"url": "http://127.0.0.1:8765/mcp", "token": "first"})
    os.chmod(path, 0o444)
    _write_descriptor(path, {"url": "http://127.0.0.1:8765/mcp", "token": "second"})
    assert json.loads(path.read_text(encoding="utf-8"))["token"] == "second"


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL 语义")
def test_descriptor_acl_drops_preexisting_explicit_grants(tmp_path: Path) -> None:
    """文件上已有的显式授权也必须被清掉。

    只做 icacls /grant:r 与 /inheritance:r 时，文件上原有的
    ``Everyone:(R)`` 会原样留下，别的本地账号照样能读到明文令牌；
    收紧流程必须把别人的显式授权逐条摘掉。
    """
    path = tmp_path / "bridge.json"
    path.write_text("{}", encoding="utf-8")
    completed = subprocess.run(
        ["icacls", str(path), "/grant", "Everyone:(R)"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr

    _write_descriptor(path, {"url": "http://127.0.0.1:8765/mcp", "token": "t"})

    acl = _acl_entries(path)
    assert len(acl) == 1, acl
    assert not any("Everyone" in entry for entry in acl), acl
    assert not any("(I)" in entry for entry in acl), acl
    assert json.loads(path.read_text(encoding="utf-8"))["token"] == "t"


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL 语义")
def test_descriptor_acl_is_rebuilt_for_long_non_ascii_paths(tmp_path: Path) -> None:
    """长中文路径下也必须能收紧，已有的显式授权要一并清掉。

    ``icacls`` 的输出走本地代码页，路径一长或含中文就会乱码并折行；
    「读输出、逐条摘授权」的做法会把折行续行误当成一个主体，
    ``/remove:g`` 随之失败，fail-closed 之下 Bridge 反而起不来。
    收紧流程不能依赖解析 icacls 的文本输出。
    """
    directory = tmp_path / ("长目录名称" * 6)
    directory.mkdir(parents=True)
    path = directory / ("发现文件" + "x" * 30 + ".json")
    path.write_text("{}", encoding="utf-8")
    granted = subprocess.run(
        ["icacls", str(path), "/grant", "Everyone:(R)"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
    )
    assert granted.returncode == 0, granted.stdout + granted.stderr

    _write_descriptor(path, {"url": "http://127.0.0.1:8765/mcp", "token": "t"})

    acl = _acl_entries(path)
    assert len(acl) == 1, acl
    assert not any("Everyone" in entry for entry in acl), acl
    assert not any("(I)" in entry for entry in acl), acl
    assert json.loads(path.read_text(encoding="utf-8"))["token"] == "t"


def _acl_entries(path: Path) -> list[str]:
    """读出文件的访问控制表条目（去掉路径前缀与空行）。"""
    completed = subprocess.run(
        ["icacls", str(path)], capture_output=True, text=True, encoding="utf-8",
        errors="replace", check=True,
    )
    entries: list[str] = []
    for line in completed.stdout.splitlines():
        line = line.strip()
        if not line or "Successfully processed" in line:
            continue
        # 每行形如 "C:\path\file USER:(F)"，权限部分跟在路径后面。
        parts = line.split(str(path), 1)
        entries.append(parts[1].strip() if len(parts) > 1 else line)
    return entries


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL 语义")
def test_current_user_sid_is_extracted_from_whoami() -> None:
    """授权必须用 SID：裸用户名在域内同名账户时会授权给错的人。

    ``whoami /user /fo csv /nh`` 输出两列
    （``"DOMAIN\\user","S-1-5-..."``），不是四列。
    """
    sid = _current_user_sid()
    assert sid.upper().startswith("S-1-"), sid
