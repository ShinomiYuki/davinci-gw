"""工具内部统一的业务异常，异常消息可直接转换为中文校验结果。"""

from __future__ import annotations


class DavinciGwError(Exception):
    """所有可预期工具异常的基类。"""


class ArxmlStructureError(DavinciGwError):
    """ARXML 可解析但根结构或必要模块不符合约束。"""


class OutputWriteError(DavinciGwError):
    """安全写出条件不满足或序列化失败。"""


class InputContractError(DavinciGwError):
    """标准输入文件未满足已冻结的输入契约。"""
