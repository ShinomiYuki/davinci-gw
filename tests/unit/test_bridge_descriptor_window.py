"""发现文件明文窗口与写入失败退出的回归。

两条断言都挂在真实可观测点上：

1. 明文窗口：拦截「收紧访问控制表」这个真实动作，在它发生的**那一刻**
   直接读磁盘上文件的真实内容与真实访问控制表。要求收紧发生时文件里
   还没有明文令牌，且收紧之后访问控制表里没有其它主体。
   ——这比并发采样可靠：窗口只有几十毫秒，靠定时采样会漏检。
2. 写入失败退出：真实起一个 ``python -m davinci_gw.bridge`` 子进程，
   让发现文件写不出去，要求进程干净退出（返回码 2、给出中文错误说明、
   不抛 traceback）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from davinci_gw.bridge import __main__ as bridge_main
from davinci_gw.bridge.__main__ import _write_descriptor

PROJECT = Path(__file__).resolve().parents[2]

# 判「其它账号可读」用的账户名片段。
_OTHER_PRINCIPALS = ("everyone", "builtin\\users", "authenticated users", "users:")


def _acl_text(path: Path) -> str:
    completed = subprocess.run(
        ["icacls", str(path)], capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False,
    )
    return completed.stdout + completed.stderr


def _acl_grants_others(acl: str) -> bool:
    lowered = acl.lower()
    return any(marker in lowered for marker in _OTHER_PRINCIPALS)


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL 语义")
def test_descriptor_never_exposes_the_plaintext_token_to_other_accounts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """明文令牌落盘时，文件必须已经是 owner-only。

    发现文件里写着明文访问令牌。写入顺序必须是「先建空文件 → 收紧
    访问控制表 → 写入内容」：反过来（先写入内容再收紧）会让令牌在
    磁盘上短暂地按父目录继承的权限存在，其它本地账号能读到。

    本用例在共享目录（显式授予 ``Everyone:(R)``）里写入，并拦截真正
    执行收紧的动作，在收紧发生的那一刻读取磁盘上的真实内容与真实
    访问控制表。
    """
    shared = tmp_path / "shared"
    shared.mkdir()
    granted = subprocess.run(
        ["icacls", str(shared), "/grant", "Everyone:(R)"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
    )
    assert granted.returncode == 0, granted.stdout + granted.stderr

    token = "SECRET-PLAINTEXT-TOKEN"
    records: list[tuple[str, str]] = []
    original = bridge_main._restrict_descriptor_permissions

    def spy(path: Path) -> None:
        # 收紧之前，磁盘上这个文件里有什么。
        before = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
        original(path)
        # 收紧之后，它的访问控制表里还有没有别的���体。
        records.append((before, _acl_text(path)))

    monkeypatch.setattr(bridge_main, "_restrict_descriptor_permissions", spy)

    target = shared / "bridge.json"
    _write_descriptor(target, {"url": "http://127.0.0.1:8765/mcp", "token": token})

    # 收紧动作必须真的发生过，否则这条断言是恒真的。
    assert records, "没有观察到任何一次权限收紧，用例无法证明窗口不存在"
    for index, (before, acl_after) in enumerate(records, 1):
        assert token not in before, (
            f"第 {index} 次收紧发生时，磁盘上已经有明文令牌：{before[:120]!r}"
        )
        assert not _acl_grants_others(acl_after), (
            f"第 {index} 次收紧之后，访问控制表里仍有其它主体：{acl_after}"
        )
    # 写完之后内容仍要完整可读。
    assert json.loads(target.read_text(encoding="utf-8"))["token"] == token


def test_bridge_exits_cleanly_when_the_descriptor_cannot_be_written(tmp_path: Path) -> None:
    """发现文件写不出去时，Bridge 要干净退出，不能抛 traceback。

    端口已经监起来了，发现文件却写不出去，客户端拿不到令牌——
    这种半启动状态对使用者毫无意义。进程必须给出明确的中文错误
    说明并以配置错误码（2）退出，而不是带着一段 traceback 崩掉。

    触发条件：``--descriptor`` 指向一个已存在的目录，``os.replace``
    必然失败。
    """
    occupied = tmp_path / "occupied"
    occupied.mkdir()

    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(PROJECT / "src")
    completed = subprocess.run(
        [
            sys.executable, "-m", "davinci_gw.bridge",
            "--core", "embedded",
            "--descriptor", str(occupied),
            "--port", "0",
        ],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=180, cwd=str(PROJECT), env=environment,
    )
    stderr = completed.stderr
    assert "Traceback (most recent call last)" not in stderr, stderr
    assert "桥接配置错误" in stderr, stderr
    assert "无法写入客户端发现文件" in stderr, stderr
    assert completed.returncode == 2, f"退出码 {completed.returncode}\n{stderr}"
