"""验证公共契约、Prepared Session、取消与原子发布语义。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields, is_dataclass
from datetime import datetime, timezone
from pathlib import Path

import pytest
from lxml import etree

from davinci_gw.application.facade import GatewayFacade
from davinci_gw.application.sessions import PreparedSessionStore, fingerprint_file
from davinci_gw.arxml.document import ArxmlDocument
from davinci_gw.contracts import (
    ArtifactDto,
    CapabilitiesDto,
    FeatureCapabilityDto,
    FeatureSummaryDto,
    FileFingerprintDto,
    GenerationResultDto,
    InputFileDto,
    IssueDto,
    MetricDto,
    OperationResultDto,
    OperationStatus,
    PreparedSessionDto,
    PreviewResultDto,
    ProgressEventDto,
    SessionState,
    UpdateRequestDto,
)
from davinci_gw.domain.models import MutationPlan
from davinci_gw.runtime import CancellationToken


def _request(config: Path, baseline: Path, output: Path | None = None) -> UpdateRequestDto:
    return UpdateRequestDto(str(config), str(baseline), str(output) if output else None)


def _assert_json_native(value: object) -> None:
    if value is None or isinstance(value, (str, int, float, bool)):
        return
    if isinstance(value, list):
        for item in value:
            _assert_json_native(item)
        return
    if isinstance(value, dict):
        assert all(isinstance(key, str) for key in value)
        for item in value.values():
            _assert_json_native(item)
        return
    raise AssertionError(f"非 JSON 原生类型：{type(value)!r}")


def _assert_no_internal_objects(value: object, seen: set[int] | None = None) -> None:
    seen = seen or set()
    if id(value) in seen:
        return
    seen.add(id(value))
    assert not isinstance(value, (Path, BaseException, ArxmlDocument, MutationPlan))
    assert not isinstance(value, (etree._Element, etree._ElementTree))
    if is_dataclass(value) and not isinstance(value, type):
        for item in fields(value):
            _assert_no_internal_objects(getattr(value, item.name), seen)
    elif isinstance(value, Mapping):
        for key, item in value.items():
            _assert_no_internal_objects(key, seen)
            _assert_no_internal_objects(item, seen)
    elif isinstance(value, (tuple, list, set, frozenset)):
        for item in value:
            _assert_no_internal_objects(item, seen)


def test_every_public_dto_has_json_native_serialization() -> None:
    fingerprint = FileFingerprintDto("C:/input", 1, 2, "abc")
    issue = IssueDto("TEST", "测试", actual_value={"values": [1, True, None]})
    metric = MetricDto("count", "数量", 1)
    feature = FeatureSummaryDto("feature", "功能", "READY", (metric,), (issue,))
    preview = PreviewResultDto(
        "operation", OperationStatus.SUCCESS, (feature,), (issue,),
        (InputFileDto("CONFIG", "C:/input", fingerprint),), "4.84",
    )
    values = (
        UpdateRequestDto("C:/config", "C:/baseline"), fingerprint,
        InputFileDto("CONFIG", "C:/input", fingerprint), issue, metric, feature,
        FeatureCapabilityDto("feature", "功能", ("PREVIEW",)),
        ProgressEventDto("operation", 1, "stage", "阶段", 1, 1, 100.0),
        ArtifactDto("C:/output", 1, "abc"),
        CapabilitiesDto((FeatureCapabilityDto("feature", "功能"),), ("PREVIEW",)),
        preview,
        PreparedSessionDto(
            "operation", OperationStatus.SUCCESS, "session", SessionState.READY,
            preview=preview,
        ),
        OperationResultDto("operation", OperationStatus.SUCCESS),
        GenerationResultDto("operation", OperationStatus.SUCCESS),
    )
    for value in values:
        encoded = value.to_dict()
        _assert_json_native(encoded)
        assert json.loads(value.to_json()) == encoded


def test_contracts_do_not_import_arxml_or_mutation_implementation() -> None:
    contracts_dir = Path(__file__).resolve().parents[2] / "src" / "davinci_gw" / "contracts"
    source = "\n".join(path.read_text(encoding="utf-8") for path in contracts_dir.glob("*.py"))
    for forbidden in ("lxml", "ArxmlDocument", "MutationPlan", "davinci_gw.arxml"):
        assert forbidden not in source


def test_public_dtos_serialize_to_json_native_values(
    workbook_factory: object, arxml_factory: object,
) -> None:
    request = _request(workbook_factory(), arxml_factory())  # type: ignore[operator]
    result = GatewayFacade().prepare(request)
    encoded = result.to_dict()
    assert result.status is OperationStatus.SUCCESS
    assert json.loads(result.to_json()) == encoded
    assert "direct_add_count" not in encoded["preview"]
    assert "signal_route" in {item["feature_id"] for item in encoded["preview"]["features"]}
    assert "document" not in result.to_json()
    assert "plan" not in result.to_json()
    _assert_no_internal_objects(result)
    summaries = {item.feature_id: item for item in result.preview.features}
    direct = {item.key: item.value for item in summaries["direct_message"].metrics}
    signal = {item.key: item.value for item in summaries["signal_route"].metrics}
    assert direct["requested_add"] == 1
    assert direct["added"] + direct["existing"] + direct["skipped"] == 1
    assert signal["requested_add"] == 1
    assert signal["added"] + signal["existing"] + signal["skipped"] == 1


def test_public_metadata_cannot_serialize_internal_domain_object() -> None:
    summary = FeatureSummaryDto("test", "测试", "READY", metadata={"internal": MutationPlan()})
    assert summary.to_dict()["metadata"] == {"internal": "<unsupported>"}


def test_unexpected_exception_is_structured_without_leaking_exception(
    workbook_factory: object, arxml_factory: object, monkeypatch: object,
) -> None:
    import davinci_gw.application.facade as facade_module

    def fail(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("SECRET_EXCEPTION_DETAIL")

    monkeypatch.setattr(facade_module, "validate_inputs", fail)  # type: ignore[attr-defined]
    result = GatewayFacade().validate(_request(
        workbook_factory(), arxml_factory(),  # type: ignore[operator]
    ))
    assert result.status is OperationStatus.INTERNAL_FAILURE
    assert "SECRET_EXCEPTION_DETAIL" not in result.to_json()
    assert not any(isinstance(getattr(result, item.name), BaseException) for item in fields(result))


def test_prepare_commit_reuses_parsed_and_planned_transaction(
    workbook_factory: object, arxml_factory: object, tmp_path: Path, monkeypatch: object,
) -> None:
    from davinci_gw.arxml.document import ArxmlDocument
    from davinci_gw.routing.transaction import TransactionCoordinator

    config = workbook_factory()  # type: ignore[operator]
    baseline = arxml_factory()  # type: ignore[operator]
    baseline_resolved = baseline.resolve()
    counts = {"baseline_load": 0, "plan": 0}
    original_load = ArxmlDocument.load.__func__
    original_plan = TransactionCoordinator.plan_and_apply

    def load_spy(cls: type[ArxmlDocument], path: str | Path) -> ArxmlDocument:
        if Path(path).resolve() == baseline_resolved:
            counts["baseline_load"] += 1
        return original_load(cls, path)

    def plan_spy(self: TransactionCoordinator):
        counts["plan"] += 1
        return original_plan(self)

    monkeypatch.setattr(ArxmlDocument, "load", classmethod(load_spy))  # type: ignore[attr-defined]
    monkeypatch.setattr(TransactionCoordinator, "plan_and_apply", plan_spy)  # type: ignore[attr-defined]
    facade = GatewayFacade()
    prepared = facade.prepare(_request(config, baseline))
    result = facade.commit_prepared(prepared.session_id or "", str(tmp_path / "result.arxml"))
    assert result.status is OperationStatus.SUCCESS
    assert counts == {"baseline_load": 1, "plan": 1}
    consumed = facade.commit_prepared(prepared.session_id or "", str(tmp_path / "again.arxml"))
    assert consumed.status is OperationStatus.SESSION_CONSUMED


def test_preview_can_be_cancelled_without_files(
    workbook_factory: object, arxml_factory: object, tmp_path: Path,
) -> None:
    token = CancellationToken()
    token.cancel()
    result = GatewayFacade().prepare(
        _request(workbook_factory(), arxml_factory(), tmp_path / "never.arxml"),  # type: ignore[operator]
        cancellation=token,
    )
    assert result.status is OperationStatus.CANCELLED
    assert list(tmp_path.glob("*.tmp.arxml")) == []


@pytest.mark.parametrize("stage_id", [
    "before_workbook", "after_workbook", "after_baseline", "before_plan", "after_plan",
    "fingerprint_recheck",
])
def test_prepare_cancels_at_each_safe_stage(
    stage_id: str, workbook_factory: object, arxml_factory: object, tmp_path: Path,
) -> None:
    token = CancellationToken()

    class Observer:
        def on_progress(self, event: object) -> None:
            if getattr(event, "stage_id", None) == stage_id:
                token.cancel()

    output = tmp_path / f"{stage_id}.arxml"
    result = GatewayFacade().prepare(
        _request(workbook_factory(), arxml_factory(), output),  # type: ignore[operator]
        observer=Observer(), cancellation=token,
    )
    assert result.status is OperationStatus.CANCELLED
    assert not output.exists()


def test_one_shot_generate_can_be_cancelled_without_output(
    workbook_factory: object, arxml_factory: object, tmp_path: Path,
) -> None:
    token = CancellationToken()
    token.cancel()
    output = tmp_path / "one_shot_cancelled.arxml"
    result = GatewayFacade().generate(
        _request(workbook_factory(), arxml_factory(), output),  # type: ignore[operator]
        cancellation=token,
    )
    assert result.status is OperationStatus.CANCELLED
    assert not output.exists()


def test_one_shot_generate_has_one_operation_and_one_terminal_event(
    workbook_factory: object, arxml_factory: object, tmp_path: Path,
) -> None:
    events: list[object] = []

    class Observer:
        def on_progress(self, event: object) -> None:
            events.append(event)

    output = tmp_path / "one_shot.arxml"
    result = GatewayFacade().generate(
        _request(workbook_factory(), arxml_factory(), output),  # type: ignore[operator]
        observer=Observer(),
    )
    assert result.status is OperationStatus.SUCCESS
    assert {getattr(event, "operation_id") for event in events} == {result.operation_id}
    assert [getattr(event, "sequence") for event in events] == list(range(1, len(events) + 1))
    assert [getattr(event, "stage_id") for event in events].count("complete") == 1


def test_prepared_session_freezes_relative_input_paths(
    workbook_factory: object, arxml_factory: object, tmp_path: Path, monkeypatch: object,
) -> None:
    config = workbook_factory()  # type: ignore[operator]
    baseline = arxml_factory()  # type: ignore[operator]
    monkeypatch.chdir(tmp_path)  # type: ignore[attr-defined]
    facade = GatewayFacade()
    prepared = facade.prepare(UpdateRequestDto(config.name, baseline.name))
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.chdir(other)  # type: ignore[attr-defined]
    result = facade.commit_prepared(prepared.session_id or "", str(tmp_path / "relative.arxml"))
    assert result.status is OperationStatus.SUCCESS


def test_cancel_after_temp_creation_removes_temp_and_output(
    workbook_factory: object, arxml_factory: object, tmp_path: Path,
) -> None:
    token = CancellationToken()

    class Observer:
        def on_progress(self, event: object) -> None:
            if getattr(event, "stage_id", None) == "publish_check":
                token.cancel()

    facade = GatewayFacade()
    config = workbook_factory()  # type: ignore[operator]
    baseline = arxml_factory()  # type: ignore[operator]
    config_before, baseline_before = fingerprint_file(config), fingerprint_file(baseline)
    prepared = facade.prepare(_request(config, baseline))
    output = tmp_path / "cancelled.arxml"
    result = facade.commit_prepared(
        prepared.session_id or "", str(output), observer=Observer(), cancellation=token,
    )
    assert result.status is OperationStatus.CANCELLED
    assert not output.exists()
    assert list(tmp_path.glob(f".{output.name}.*.tmp.arxml")) == []
    assert fingerprint_file(config) == config_before
    assert fingerprint_file(baseline) == baseline_before


def test_output_io_failure_is_internal_and_cleans_temp(
    workbook_factory: object, arxml_factory: object, tmp_path: Path, monkeypatch: object,
) -> None:
    import davinci_gw.arxml.document as document_module

    def fail_serialize(*_args: object, **_kwargs: object) -> None:
        raise OSError("SECRET_DISK_DETAIL")

    facade = GatewayFacade()
    prepared = facade.prepare(_request(workbook_factory(), arxml_factory()))  # type: ignore[operator]
    monkeypatch.setattr(document_module, "_serialize_tree", fail_serialize)  # type: ignore[attr-defined]
    output = tmp_path / "io_failed.arxml"
    result = facade.commit_prepared(prepared.session_id or "", str(output))
    assert result.status is OperationStatus.INTERNAL_FAILURE
    assert "SECRET_DISK_DETAIL" not in result.to_json()
    assert not output.exists()
    assert list(tmp_path.glob(f".{output.name}.*.tmp.arxml")) == []


def test_input_change_at_publish_boundary_removes_temp(
    workbook_factory: object, arxml_factory: object, tmp_path: Path,
) -> None:
    config = workbook_factory()  # type: ignore[operator]
    baseline = arxml_factory()  # type: ignore[operator]

    class Observer:
        def on_progress(self, event: object) -> None:
            if getattr(event, "stage_id", None) == "publish_check":
                baseline.touch()

    facade = GatewayFacade()
    prepared = facade.prepare(_request(config, baseline))
    output = tmp_path / "changed_at_publish.arxml"
    result = facade.commit_prepared(prepared.session_id or "", str(output), observer=Observer())
    assert result.status is OperationStatus.INPUT_CHANGED
    assert not output.exists()
    assert list(tmp_path.glob(f".{output.name}.*.tmp.arxml")) == []


def test_cancellation_after_atomic_replace_is_still_success(
    workbook_factory: object, arxml_factory: object, tmp_path: Path,
) -> None:
    token = CancellationToken()

    class Observer:
        def on_progress(self, event: object) -> None:
            if getattr(event, "stage_id", None) == "complete":
                token.cancel()

    facade = GatewayFacade()
    prepared = facade.prepare(_request(workbook_factory(), arxml_factory()))  # type: ignore[operator]
    output = tmp_path / "committed.arxml"
    result = facade.commit_prepared(
        prepared.session_id or "", str(output), observer=Observer(), cancellation=token,
    )
    assert result.status is OperationStatus.SUCCESS
    assert output.is_file()


def test_input_change_blocks_commit(
    workbook_factory: object, arxml_factory: object, tmp_path: Path,
) -> None:
    config = workbook_factory()  # type: ignore[operator]
    baseline = arxml_factory()  # type: ignore[operator]
    facade = GatewayFacade()
    prepared = facade.prepare(_request(config, baseline))
    baseline.touch()
    output = tmp_path / "changed.arxml"
    result = facade.commit_prepared(prepared.session_id or "", str(output))
    assert result.status is OperationStatus.INPUT_CHANGED
    assert not output.exists()


def test_session_missing_expired_and_observer_failure_are_isolated(
    workbook_factory: object, arxml_factory: object, tmp_path: Path,
) -> None:
    now = [0.0]
    store = PreparedSessionStore(
        ttl_seconds=1, monotonic=lambda: now[0],
        wall_clock=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    facade = GatewayFacade(session_store=store)
    missing = facade.commit_prepared("missing", str(tmp_path / "missing.arxml"))
    assert missing.status is OperationStatus.SESSION_MISSING

    class BrokenObserver:
        def on_progress(self, _event: object) -> None:
            raise RuntimeError("observer failure")

    prepared = facade.prepare(
        _request(workbook_factory(), arxml_factory()), observer=BrokenObserver(),  # type: ignore[operator]
    )
    assert prepared.status is OperationStatus.SUCCESS
    now[0] = 2.0
    expired = facade.commit_prepared(prepared.session_id or "", str(tmp_path / "expired.arxml"))
    assert expired.status is OperationStatus.SESSION_EXPIRED


def test_explicit_discard_and_capacity_eviction(
    workbook_factory: object, arxml_factory: object, tmp_path: Path,
) -> None:
    facade = GatewayFacade(session_store=PreparedSessionStore(max_sessions=1))
    first = facade.prepare(_request(
        workbook_factory(filename="first_v4.84.xlsx"),  # type: ignore[operator]
        arxml_factory(filename="first.arxml"),  # type: ignore[operator]
    ))
    assert facade.discard_prepared(first.session_id or "").status is OperationStatus.SUCCESS
    assert facade.commit_prepared(
        first.session_id or "", str(tmp_path / "discarded.arxml"),
    ).status is OperationStatus.SESSION_INVALID
    second = facade.prepare(_request(
        workbook_factory(filename="second_v4.84.xlsx"),  # type: ignore[operator]
        arxml_factory(filename="second.arxml"),  # type: ignore[operator]
    ))
    third = facade.prepare(_request(
        workbook_factory(filename="third_v4.84.xlsx"),  # type: ignore[operator]
        arxml_factory(filename="third.arxml"),  # type: ignore[operator]
    ))
    assert third.status is OperationStatus.SUCCESS
    evicted = facade.commit_prepared(second.session_id or "", str(tmp_path / "evicted.arxml"))
    assert evicted.status is OperationStatus.SESSION_MISSING


def test_concurrent_commits_cannot_both_succeed(
    workbook_factory: object, arxml_factory: object, tmp_path: Path,
) -> None:
    facade = GatewayFacade()
    prepared = facade.prepare(_request(workbook_factory(), arxml_factory()))  # type: ignore[operator]
    session_id = prepared.session_id or ""
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(facade.commit_prepared, session_id, str(tmp_path / f"concurrent_{index}.arxml"))
            for index in range(2)
        ]
    statuses = [future.result().status for future in futures]
    assert statuses.count(OperationStatus.SUCCESS) == 1
    assert statuses.count(OperationStatus.SESSION_INVALID) + statuses.count(OperationStatus.SESSION_CONSUMED) == 1


def test_progress_is_monotonic_and_ends_complete(
    workbook_factory: object, arxml_factory: object,
) -> None:
    events: list[object] = []

    class Observer:
        def on_progress(self, event: object) -> None:
            events.append(event)

    result = GatewayFacade().prepare(
        _request(workbook_factory(), arxml_factory()), observer=Observer(),  # type: ignore[operator]
    )
    assert result.status is OperationStatus.SUCCESS
    assert [getattr(item, "sequence") for item in events] == list(range(1, len(events) + 1))
    assert getattr(events[-1], "stage_id") == "complete"


def test_commit_error_emits_terminal_error_event(tmp_path: Path) -> None:
    events: list[object] = []

    class Observer:
        def on_progress(self, event: object) -> None:
            events.append(event)

    result = GatewayFacade().commit_prepared("missing", str(tmp_path / "missing.arxml"), observer=Observer())
    assert isinstance(result, GenerationResultDto)
    assert result.status is OperationStatus.SESSION_MISSING
    assert getattr(events[-1], "stage_id") == "error"
