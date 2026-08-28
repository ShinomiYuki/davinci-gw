"""在可丢弃工作副本上统一编排 DELETE 投影与后续 ADD。"""

from __future__ import annotations

from davinci_gw.arxml.document import ArxmlDocument
from davinci_gw.domain.models import MutationPlan, WorkbookData
from davinci_gw.mutations import MutationHandlerRegistry

from .add import AddCoordinator
from .delete import DeleteCoordinator


def _merge_plans(delete: MutationPlan, add: MutationPlan) -> MutationPlan:
    """合并两个阶段的操作、问题、决策和用户可见统计。"""
    return MutationPlan(
        operations=delete.operations + add.operations,
        issues=delete.issues + add.issues,
        direct_added_count=add.direct_added_count,
        direct_existing_count=add.direct_existing_count,
        direct_skipped_count=add.direct_skipped_count,
        signal_added_count=add.signal_added_count,
        signal_existing_count=add.signal_existing_count,
        signal_skipped_count=add.signal_skipped_count,
        direct_deleted_count=delete.direct_deleted_count,
        direct_missing_count=delete.direct_missing_count,
        direct_retained_count=delete.direct_retained_count,
        direct_conflict_count=delete.direct_conflict_count,
        signal_deleted_count=delete.signal_deleted_count,
        signal_missing_count=delete.signal_missing_count,
        signal_retained_count=delete.signal_retained_count,
        signal_timeout_removed_count=delete.signal_timeout_removed_count,
        signal_timeout_retained_count=delete.signal_timeout_retained_count,
        signal_conflict_count=delete.signal_conflict_count,
        expected_new_uuids=add.expected_new_uuids,
        decisions=delete.decisions,
    )


class TransactionCoordinator:
    """使用单个不可复用工作树实现全部成功或全部丢弃的事务。"""

    def __init__(
        self,
        document: ArxmlDocument,
        workbook: WorkbookData,
        handler_registry: MutationHandlerRegistry | None = None,
    ) -> None:
        """可选注入处理器注册表，默认保持现有四模块行为。"""
        self.document = document
        self.workbook = workbook
        self.handler_registry = handler_registry

    def plan_and_apply(self) -> MutationPlan:
        """先形成 DELETE 投影，再在新索引上规划 ADD，最后应用新增。"""
        # 初始树只建立一次索引，DELETE 规划和应用共享它。删除实际改变树后，
        # apply 只在 DELETE→ADD 边界重建一次，后续 ADD 全阶段继续共享投影索引。
        initial_index = self.document.build_index()
        delete_coordinator = DeleteCoordinator(self.document, self.workbook, initial_index)
        delete_plan = delete_coordinator.plan()
        if delete_plan.errors:
            return delete_plan
        projected_index = delete_coordinator.apply(delete_plan)

        # 此处重新构造全部编辑器，它们共享删除后的新索引；绝不读取删除前的路径或引用计数。
        add_coordinator = AddCoordinator(
            self.document, self.workbook, projected_index, self.handler_registry,
        )
        add_plan = add_coordinator.plan()
        combined = _merge_plans(delete_plan, add_plan)
        if combined.errors:
            return combined
        add_coordinator.apply(add_plan)
        return combined
