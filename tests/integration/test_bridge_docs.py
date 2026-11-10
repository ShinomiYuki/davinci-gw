"""桥接文档的参数表必须与命令行选项保持一致。

命令行加了选项、文档没跟上，是这类运维文档最容易犯的错：用户照着
文档敲参数，撞上的是"未知参数"，而不是文档里说的那个行为。这里把
真实命令行帮助里的长选项与文档里出现的选项双向比对，两边都得对得上。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

DOC_PATH = Path(__file__).resolve().parents[2] / "docs" / "桌面端Bridge服务.md"

_OPTION_PATTERN = re.compile(r"--[a-z][a-z-]*")


def _cli_long_options() -> set[str]:
    """从真实命令行帮助里取出全部长选项。

    走子进程而不是直接摸解析器：帮助文本才是用户实际看到的东西，
    解析器里的选项名如果没被 ``add_argument`` 之外的路径漏掉，帮助
    文本里也会缺一项，反过来亦然。
    """
    project = Path(__file__).resolve().parents[2]
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(project / "src")
    completed = subprocess.run(
        [sys.executable, "-m", "davinci_gw.bridge", "--help"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=environment, check=True,
    )
    # argparse 的用法块是帮助文本的第一段，后面才是描述与选项说明。
    usage = completed.stdout.split("\n\n", 1)[0]
    return set(_OPTION_PATTERN.findall(usage))


def _documented_options() -> set[str]:
    """文档里出现过的全部选项记号。"""
    return set(_OPTION_PATTERN.findall(DOC_PATH.read_text(encoding="utf-8")))


def test_documentation_covers_every_command_line_option() -> None:
    """命令行有的选项，文档必须写出来。"""
    missing = sorted(_cli_long_options() - _documented_options())
    assert not missing, f"桥接文档缺少命令行选项：{missing}"


def test_documented_options_all_exist_in_the_command_line() -> None:
    """文档里写的选项必须真实存在，避免文档写出想当然的参数名。"""
    unknown = sorted(_documented_options() - _cli_long_options())
    assert not unknown, f"桥接文档写了不存在的命令行选项：{unknown}"


def test_core_env_is_documented_with_its_real_semantics() -> None:
    """``--core-env`` 曾经只存在于代码里，文档整条漏掉。

    它叠加在核心子进程的最小默认环境上，父进程的其它环境变量不会
    自动传下去——这一点必须写清楚，否则用户会以为父进程设的变量在
    核心里同样有效。
    """
    text = DOC_PATH.read_text(encoding="utf-8")
    assert "`--core-env`" in text
    # 只对 stdio 核心生效。
    assert "stdio" in text
    # 最小默认环境的范围要写明。
    assert "SYSTEMROOT" in text
