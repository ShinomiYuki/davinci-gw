"""验证 Feature 与 MutationHandler 扩展无需修改核心分支。"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from davinci_gw.application.facade import GatewayFacade
from davinci_gw.arxml.document import ArxmlDocument
from davinci_gw.contracts import FeatureSummaryDto, MetricDto, UpdateRequestDto
from davinci_gw.domain.models import MutationKind, MutationOperation
from davinci_gw.features import (
    DuplicateFeatureError,
    FeatureContext,
    FeatureRegistry,
    default_feature_registry,
)
from davinci_gw.input.workbook_reader import read_workbook
from davinci_gw.mutations import (
    DuplicateMutationHandlerError,
    MutationHandler,
    MutationHandlerRegistry,
    UnknownMutationHandlerError,
)
from davinci_gw.routing.transaction import TransactionCoordinator


@dataclass(frozen=True)
class DiagnosticFeature:
    feature_id: str = "virtual_test_route"
    display_name: str = "诊断虚拟路由"
    capabilities: tuple[str, ...] = ("PREVIEW",)

    def summarize(self, context: FeatureContext) -> FeatureSummaryDto:
        del context
        return FeatureSummaryDto(
            self.feature_id, self.display_name, "READY",
            (MetricDto("virtual_count", "虚拟数量", 7),),
        )


def test_virtual_feature_appears_without_facade_or_dto_changes() -> None:
    registry = FeatureRegistry((DiagnosticFeature(),))
    assert registry.capabilities()[0].feature_id == "virtual_test_route"
    summary = registry.summarize(FeatureContext(None, None))[0]
    assert summary.to_dict()["metrics"][0]["value"] == 7


def test_virtual_feature_appears_through_unchanged_facade(
    workbook_factory: object, arxml_factory: object,
) -> None:
    registry = default_feature_registry()
    registry.register(DiagnosticFeature())
    facade = GatewayFacade(feature_registry=registry)
    assert "virtual_test_route" in {
        item.feature_id for item in facade.get_capabilities().features
    }
    prepared = facade.prepare(UpdateRequestDto(
        str(workbook_factory()), str(arxml_factory()),  # type: ignore[operator]
    ))
    assert "virtual_test_route" in {
        item.feature_id for item in (prepared.preview.features if prepared.preview else ())
    }


def test_feature_registry_rejects_duplicate_id() -> None:
    with pytest.raises(DuplicateFeatureError):
        FeatureRegistry((DiagnosticFeature(), DiagnosticFeature()))


def test_custom_mutation_handler_executes_without_dispatch_branch() -> None:
    called: list[str] = []
    registry = MutationHandlerRegistry((MutationHandler(
        "custom", frozenset({MutationKind.ECUC_PDU}), 5,
        lambda _context, operation: called.append(operation.object_path),
    ),))
    operation = MutationOperation(MutationKind.ECUC_PDU, "/Test", "Item", "/Definition")
    registry.apply_operations(object(), (operation,))  # type: ignore[arg-type]
    assert called == ["/Test/Item"]


def test_transaction_coordinator_accepts_custom_handler_registry(
    workbook_factory: object, arxml_factory: object,
) -> None:
    calls: list[MutationKind] = []
    registry = MutationHandlerRegistry((MutationHandler(
        "all_test_kinds", frozenset(MutationKind), 10,
        lambda _context, operation: calls.append(operation.kind),
    ),))
    workbook = read_workbook(workbook_factory()).data  # type: ignore[operator]
    document = ArxmlDocument.load(arxml_factory())  # type: ignore[operator]
    plan = TransactionCoordinator(document, workbook, registry).plan_and_apply()
    assert not plan.errors
    assert calls


def test_mutation_registry_duplicate_unknown_and_order_are_explicit() -> None:
    first = MutationHandler("first", frozenset({MutationKind.ECUC_PDU}), 20, lambda *_: None)
    second = MutationHandler("second", frozenset({MutationKind.CANIF_RX_PDU}), 10, lambda *_: None)
    registry = MutationHandlerRegistry((first, second))
    operations = (
        MutationOperation(MutationKind.ECUC_PDU, "/Z", "B", "/D"),
        MutationOperation(MutationKind.CANIF_RX_PDU, "/Z", "A", "/D"),
    )
    assert [item.kind for item in registry.sort_operations(operations)] == [
        MutationKind.CANIF_RX_PDU, MutationKind.ECUC_PDU,
    ]
    with pytest.raises(DuplicateMutationHandlerError):
        registry.register(MutationHandler("duplicate", frozenset({MutationKind.ECUC_PDU}), 1, lambda *_: None))
    with pytest.raises(UnknownMutationHandlerError):
        MutationHandlerRegistry().resolve(MutationKind.COM_GW_MAPPING)
