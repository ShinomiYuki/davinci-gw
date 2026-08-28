"""PduR 编辑器：规划和应用 RoutingPath、SrcPdu 与 DestPdu。"""

from __future__ import annotations

from davinci_gw.arxml.document import ArxmlDocument
from davinci_gw.arxml.index import ArxmlIndex
from davinci_gw.domain.errors import ArxmlStructureError
from davinci_gw.domain.models import MutationKind, MutationOperation, SourceLocation

from . import definitions as defs
from .common import (
    HandleAllocator,
    MutationContext,
    inspect_operation,
    semantic_values,
    unique_template_parent_path,
    find_templates,
)


def _single_template_reference(
    document: ArxmlDocument, index: ArxmlIndex, definition: str, reference: str, *,
    suffix: str | None = None,
) -> str:
    values: set[str] = set()
    for node in find_templates(
        document.root, document.namespace, definition, index=index,
    ):
        _, refs = semantic_values(node, document.namespace)
        candidates = refs.get(reference, ())
        if suffix is not None:
            candidates = tuple(value for value in candidates if value.rstrip("/").endswith(suffix))
        values.update(candidates)
    if len(values) != 1:
        raise ArxmlStructureError(
            f"定义“{definition}”的模板引用“{reference}”应唯一，实际为{sorted(values)}。"
        )
    return values.pop()


class PduREditor:
    """只负责 PduR 路由表，复用真实模板中的锁和 BSW 模块引用。"""

    def __init__(self, document: ArxmlDocument, index: ArxmlIndex | None = None) -> None:
        self.document = document
        self.index = index or document.build_index()
        self.path_parent = unique_template_parent_path(
            document.root, document.namespace, defs.PDUR_PATH, index=self.index,
        )
        self.lock_ref = _single_template_reference(
            document, self.index, defs.PDUR_PATH, defs.PDUR_PATH_LOCK_REF,
        )
        self.src_module_ref = _single_template_reference(
            document, self.index, defs.PDUR_SRC, defs.PDUR_SRC_MODULE_REF, suffix="/CanIf",
        )
        self.dest_module_ref = _single_template_reference(
            document, self.index, defs.PDUR_DEST, defs.PDUR_DEST_MODULE_REF, suffix="/CanIf",
        )
        self.src_handles = HandleAllocator(self.index, document.namespace, defs.PDUR_SRC_HANDLE)
        self.dest_handles = HandleAllocator(self.index, document.namespace, defs.PDUR_DEST_HANDLE)

    def path_operation(
        self, short_name: str, locations: tuple[SourceLocation, ...],
    ) -> MutationOperation:
        """构造一对多 RoutingPath 外层容器。"""
        return MutationOperation(
            MutationKind.PDUR_ROUTING_PATH, self.path_parent, short_name, defs.PDUR_PATH,
            parameters=((defs.PDUR_PATH_COMM_TYPE, "COMMUNICATION_INTERFACE"),
                        (defs.PDUR_PATH_MULTICORE, "false")),
            references=((defs.PDUR_PATH_LOCK_REF, self.lock_ref),),
            source_locations=locations,
        )

    def source_operation(
        self, path: str, short_name: str, ecuc_path: str,
        locations: tuple[SourceLocation, ...], *, allocate_handle: bool,
    ) -> MutationOperation:
        """构造 RoutingPath 中唯一的源 PDU。"""
        return MutationOperation(
            MutationKind.PDUR_SRC_PDU, path, short_name, defs.PDUR_SRC,
            parameters=((defs.PDUR_SRC_HANDLE, str(
                self.src_handles.allocate() if allocate_handle else self.src_handles.peek()
            )),
                        (defs.PDUR_SRC_DIRECTION, "RECEIVE")),
            references=((defs.PDUR_SRC_PDU_REF, ecuc_path),
                        (defs.PDUR_SRC_MODULE_REF, self.src_module_ref)),
            source_locations=locations,
        )

    def destination_operation(
        self, path: str, short_name: str, ecuc_path: str, length_strategy: str,
        locations: tuple[SourceLocation, ...], *, allocate_handle: bool,
    ) -> MutationOperation:
        """构造一条目标 PDU；多个目标共享相同父 RoutingPath。"""
        return MutationOperation(
            MutationKind.PDUR_DEST_PDU, path, short_name, defs.PDUR_DEST,
            parameters=(
                (defs.PDUR_DEST_HANDLE, str(
                    self.dest_handles.allocate() if allocate_handle else self.dest_handles.peek()
                )),
                (defs.PDUR_DEST_DIRECTION, "TRANSMIT"),
                (defs.PDUR_DEST_ROUTING_TYPE, "GATEWAY_ROUTING"),
                (defs.PDUR_DEST_PROCESSING, "IMMEDIATE"),
                (defs.PDUR_DEST_LENGTH_STRATEGY, length_strategy),
                (defs.PDUR_DEST_CROSS_PARTITION, "false"),
                (defs.PDUR_DEST_DATA_PROVISION, "PDUR_DIRECT"),
                (defs.PDUR_DEST_CONFIRMATION, "false"),
            ),
            references=((defs.PDUR_DEST_PDU_REF, ecuc_path),
                        (defs.PDUR_DEST_MODULE_REF, self.dest_module_ref)),
            source_locations=locations,
        )

    def inspect(self, operation: MutationOperation) -> str:
        """比较完整 PduR 语义，已有源/目标 Handle ID 不参与重新分配比较。"""
        ignored = frozenset({defs.PDUR_SRC_HANDLE, defs.PDUR_DEST_HANDLE})
        return inspect_operation(
            self.index, self.document.namespace, operation, ignore_parameters=ignored,
        )[0]

    @staticmethod
    def apply(context: MutationContext, operation: MutationOperation) -> None:
        """应用一个 PduR 路由表操作。"""
        context.apply_container(operation)
