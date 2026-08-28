"""变更应用处理器的可扩展注册机制。"""

from .registry import (
    DuplicateMutationHandlerError,
    MutationHandler,
    MutationHandlerRegistry,
    UnknownMutationHandlerError,
)

__all__ = [
    "DuplicateMutationHandlerError", "MutationHandler", "MutationHandlerRegistry",
    "UnknownMutationHandlerError",
]
