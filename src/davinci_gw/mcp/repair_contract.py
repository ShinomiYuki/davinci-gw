"""修复工具的调用契约常量。

单独成模块，是为了让应用服务层不必为了读一条确认语或一个枚举，
就去导入依赖 openai-codex SDK 的 ``repair_service``。
"""

from __future__ import annotations

START_CONFIRMATION = "确认开始工具BUG自动修复"
SUBMIT_CONFIRMATION = "确认提交工具BUG修复"
CANCEL_CONFIRMATION = "确认取消工具BUG修复"

#: ``submit_bug_repair`` 的 ``mode`` 取值。
SUBMIT_MODES: tuple[str, ...] = ("LOCAL_COMMIT", "DRAFT_PR", "MAINTAINER")
