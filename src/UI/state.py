"""GUI 状态机及唯一的控件启用规则。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class GuiState(str, Enum):
    """单窗口工作流的互斥状态。"""

    EMPTY = "EMPTY"
    READY = "READY"
    PREPARING = "PREPARING"
    PREVIEW_VALID = "PREVIEW_VALID"
    PREVIEW_INVALID = "PREVIEW_INVALID"
    GENERATING = "GENERATING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True, slots=True)
class ControlState:
    """界面只读取该快照，不在控件层推导业务状态。"""

    inputs_enabled: bool
    preview_enabled: bool
    generate_enabled: bool
    cancel_enabled: bool


def controls_for(state: GuiState, *, inputs_valid: bool, cancellable: bool) -> ControlState:
    """返回给定状态下的唯一控件规则。"""
    busy = state in {GuiState.PREPARING, GuiState.GENERATING}
    return ControlState(
        inputs_enabled=not busy,
        preview_enabled=inputs_valid and not busy,
        generate_enabled=state is GuiState.PREVIEW_VALID,
        cancel_enabled=busy and cancellable,
    )
