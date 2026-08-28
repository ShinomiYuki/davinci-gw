"""CanIf 编辑器：规划和应用 Rx/Tx PDU 新增。"""

from __future__ import annotations

from davinci_gw.arxml.document import ArxmlDocument
from davinci_gw.arxml.index import ArxmlIndex
from davinci_gw.domain.models import DirectRouteChange, MutationKind, MutationOperation, SourceLocation

from . import definitions as defs
from .common import HandleAllocator, MutationContext, inspect_operation, unique_template_parent_path


def enabled(value: str | None) -> str:
    """将配置表 Enable/Disable 规范为 AUTOSAR 布尔文本。"""
    return "true" if value and value.strip().upper() == "ENABLE" else "false"


class CanIfEditor:
    """只负责 CanIf Rx/Tx PDU，Handle ID 在各自定义作用域独立分配。"""

    def __init__(self, document: ArxmlDocument, index: ArxmlIndex | None = None) -> None:
        self.document = document
        self.index = index or document.build_index()
        self.rx_parent_path = unique_template_parent_path(
            document.root, document.namespace, defs.CANIF_RX, index=self.index,
        )
        self.tx_parent_path = unique_template_parent_path(
            document.root, document.namespace, defs.CANIF_TX, index=self.index,
        )
        self.rx_handles = HandleAllocator(self.index, document.namespace, defs.CANIF_RX_HANDLE)
        self.tx_handles = HandleAllocator(self.index, document.namespace, defs.CANIF_TX_HANDLE)

    def rx_operation(
        self, route: DirectRouteChange, short_name: str, ecuc_path: str, hrh_path: str,
        locations: tuple[SourceLocation, ...], *, allocate_handle: bool,
    ) -> MutationOperation:
        """构造 CanIf Rx PDU 操作，只有确定缺失后才消耗 Handle ID。"""
        parameters = (
            (defs.CANIF_RX_CAN_ID, str(route.key.source_can_id)),
            (defs.CANIF_RX_CAN_ID_TYPE, route.source_message_type or ""),
            (defs.CANIF_RX_DLC, str(route.source_length)),
            (defs.CANIF_RX_INDICATION_NAME, "PduR_CanIfRxIndication"),
            (defs.CANIF_RX_INDICATION_UL, route.source_rx_indication_ul or ""),
            (defs.CANIF_RX_HANDLE, str(
                self.rx_handles.allocate() if allocate_handle else self.rx_handles.peek()
            )),
            (defs.CANIF_RX_READ_DATA, "false"),
            (defs.CANIF_RX_READ_NOTIFY, "false"),
            (defs.CANIF_RX_DLC_CHECK, enabled(route.source_dlc_check_enabled)),
            (defs.CANIF_RX_TYPE, "STATIC"),
        )
        return MutationOperation(
            MutationKind.CANIF_RX_PDU, self.rx_parent_path, short_name, defs.CANIF_RX,
            parameters=parameters,
            references=((defs.CANIF_RX_HRH_REF, hrh_path), (defs.CANIF_RX_PDU_REF, ecuc_path)),
            source_locations=locations,
        )

    def tx_operation(
        self, route: DirectRouteChange, short_name: str, ecuc_path: str, buffer_path: str,
        locations: tuple[SourceLocation, ...], *, allocate_handle: bool,
    ) -> MutationOperation:
        """构造 CanIf Tx PDU 操作。"""
        parameters = (
            (defs.CANIF_TX_CAN_ID, str(route.key.target_can_id)),
            (defs.CANIF_TX_CAN_ID_TYPE, route.target_message_type or ""),
            (defs.CANIF_TX_DLC, str(route.target_length)),
            (defs.CANIF_TX_CONFIRM_NAME, "NULL_PTR"),
            (defs.CANIF_TX_CONFIRM_UL, "NONE"),
            (defs.CANIF_TX_HANDLE, str(
                self.tx_handles.allocate() if allocate_handle else self.tx_handles.peek()
            )),
            (defs.CANIF_TX_READ_NOTIFY, "false"),
            (defs.CANIF_TX_TYPE, "STATIC"),
            (defs.CANIF_TX_TRUNCATION, enabled(route.target_truncation_enabled)),
        )
        return MutationOperation(
            MutationKind.CANIF_TX_PDU, self.tx_parent_path, short_name, defs.CANIF_TX,
            parameters=parameters,
            references=((defs.CANIF_TX_BUFFER_REF, buffer_path), (defs.CANIF_TX_PDU_REF, ecuc_path)),
            source_locations=locations,
        )

    def inspect(self, operation: MutationOperation) -> str:
        """比较 CanIf 完整语义；已有对象的 Handle ID 只要求作用域唯一。"""
        ignored = frozenset({defs.CANIF_RX_HANDLE, defs.CANIF_TX_HANDLE})
        return inspect_operation(
            self.index, self.document.namespace, operation, ignore_parameters=ignored,
        )[0]

    @staticmethod
    def apply(context: MutationContext, operation: MutationOperation) -> None:
        """应用一个 CanIf Rx 或 Tx PDU 操作。"""
        context.apply_container(operation)
