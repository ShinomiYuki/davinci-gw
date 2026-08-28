"""EcuC 编辑器：规划和应用网关源端/目标端 PDU 新增。"""

from __future__ import annotations

from davinci_gw.arxml.document import ArxmlDocument
from davinci_gw.arxml.index import ArxmlIndex
from davinci_gw.domain.models import MutationKind, MutationOperation, SourceLocation

from . import definitions as defs
from .common import MutationContext, inspect_operation, unique_template_parent_path


class EcucEditor:
    """只负责 EcuC EcucPduCollection 中的 PDU，不调用其他模块编辑器。"""

    def __init__(self, document: ArxmlDocument, index: ArxmlIndex | None = None) -> None:
        self.document = document
        self.index = index or document.build_index()
        self.parent_path = unique_template_parent_path(
            document.root, document.namespace, defs.ECUC_PDU, index=self.index,
        )

    def pdu_operation(
        self, short_name: str, length: int, locations: tuple[SourceLocation, ...],
    ) -> MutationOperation:
        """构造一个完整 EcuC PDU 计划操作。"""
        return MutationOperation(
            MutationKind.ECUC_PDU, self.parent_path, short_name, defs.ECUC_PDU,
            parameters=((defs.ECUC_PDU_LENGTH, str(length)), (defs.ECUC_PDU_J1939, "false")),
            source_locations=locations,
        )

    def inspect(self, operation: MutationOperation) -> str:
        """判断计划 PDU 是缺失、已完全存在还是冲突。"""
        return inspect_operation(self.index, self.document.namespace, operation)[0]

    @staticmethod
    def apply(context: MutationContext, operation: MutationOperation) -> None:
        """应用一个 EcuC PDU 操作。"""
        context.apply_container(operation)
