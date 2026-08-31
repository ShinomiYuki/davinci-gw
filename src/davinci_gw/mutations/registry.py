"""以稳定 kind、确定顺序和显式错误分派内部变更操作。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Hashable

from davinci_gw.domain.models import MutationOperation
from davinci_gw.modules.common import MutationContext


class DuplicateMutationHandlerError(ValueError):
    """处理器 ID 或负责 kind 重复。"""
    pass


class UnknownMutationHandlerError(LookupError):
    """计划包含未注册 kind，禁止隐式回退。"""
    pass


@dataclass(frozen=True, slots=True)
class MutationHandler:
    """把稳定标识、负责 kind、顺序和应用函数绑定为不可变处理器。"""
    handler_id: str
    kinds: frozenset[Hashable]
    order: int
    apply: Callable[[MutationContext, MutationOperation], None]


class MutationHandlerRegistry:
    """注册表自身无全局状态，同一 kind 只能归属于一个处理器。"""

    def __init__(self, handlers: tuple[MutationHandler, ...] = ()) -> None:
        self._by_kind: dict[Hashable, MutationHandler] = {}
        self._handler_ids: set[str] = set()
        for handler in handlers:
            self.register(handler)

    def register(self, handler: MutationHandler) -> None:
        """在不覆盖既有 kind 的前提下注册处理器。"""
        if not handler.handler_id or not handler.kinds or not callable(handler.apply):
            raise ValueError("变更处理器必须提供非空标识、至少一个 kind 和可调用 apply。")
        if not isinstance(handler.order, int):
            raise ValueError(f"变更处理器 {handler.handler_id} 的 order 必须是整数。")
        if handler.handler_id in self._handler_ids:
            raise DuplicateMutationHandlerError(f"变更处理器标识重复：{handler.handler_id}")
        duplicate = next((kind for kind in handler.kinds if kind in self._by_kind), None)
        if duplicate is not None:
            raise DuplicateMutationHandlerError(f"变更类型已有处理器：{duplicate}")
        self._handler_ids.add(handler.handler_id)
        for kind in handler.kinds:
            self._by_kind[kind] = handler

    def resolve(self, kind: Hashable) -> MutationHandler:
        """解析 kind；未知类型以显式异常阻止事务。"""
        try:
            return self._by_kind[kind]
        except KeyError as exc:
            raise UnknownMutationHandlerError(f"未注册变更处理器：{kind}") from exc

    def order_for(self, kind: Hashable) -> int:
        """返回 kind 的确定执行优先级。"""
        return self.resolve(kind).order

    def sort_operations(self, operations: tuple[MutationOperation, ...]) -> tuple[MutationOperation, ...]:
        """先按处理器优先级、再按对象路径稳定排序。"""
        return tuple(sorted(
            operations,
            key=lambda item: (self.order_for(item.kind), item.object_path, item.references),
        ))

    def apply_operations(self, context: MutationContext, operations: tuple[MutationOperation, ...]) -> None:
        """按确定顺序调用处理器，不在协调器中维护 kind 分支。"""
        for operation in self.sort_operations(operations):
            self.resolve(operation.kind).apply(context, operation)
