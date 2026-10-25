"""현재 계열 조회와 불변 실행·기존 선택 계산의 경계를 실제 mock 실행으로 검증한다."""

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from strategy_workbench.application.backtest_run.facade.runs import BacktestRunResult, InlineDraft
from strategy_workbench.application.strategy_design.facade.ports import StrategyRevisionRecord
from strategy_workbench.domain.experiment.facade.statistics import run_deflated_sharpe
from tests.application.test_trial_ledger import _finish, _Lab, _saved_run, _spec


def test_current_ledger_changes_without_rewriting_result_and_survives_restart(
    tmp_path: Path,
) -> None:
    lab = _Lab(tmp_path)
    saved = lab.save(_spec())
    run_id = _finish(lab.runs, _saved_run(saved))
    original = lab.runs.result(run_id)
    first = lab.runs.validation_report(run_id)
    assert first.dsr.trial_count == 1
    assert first.psr == next(m for m in original.metrics if m.metric_id == "probabilistic_sharpe")
    changed = lab.revise(saved, _spec(weight=2))
    _finish(lab.runs, _saved_run(changed))
    second = lab.runs.validation_report(run_id)
    assert second.dsr.trial_count == 2
    assert second.dsr.value == run_deflated_sharpe(original, lab.runs.trial_ledger("s-1"))
    assert second.run_fingerprint == first.run_fingerprint == original.manifest.run_fingerprint
    assert lab.runs.result(run_id) == original
    assert lab.service().validation_report(run_id).dsr == second.dsr


def test_inline_with_explicit_lineage_is_counted_but_unlinked_draft_is_not(tmp_path: Path) -> None:
    lab = _Lab(tmp_path)
    saved = lab.save(_spec())
    request = replace(
        _saved_run(saved), strategy_source=InlineDraft(saved.spec, "inline_draft", "a" * 64)
    )
    unlinked = _finish(lab.runs, request)
    linked = _finish(lab.runs, replace(request, lineage_strategy_id="s-1"))
    assert lab.runs.validation_report(unlinked).dsr.unavailable_reason == "no_lineage"
    evidence = lab.runs.validation_report(linked).dsr
    assert evidence.lineage_id == "s-1"
    assert evidence.trial_count == 1
    assert evidence.value == run_deflated_sharpe(
        lab.runs.result(linked), lab.runs.trial_ledger("s-1")
    )


def test_completed_retired_revision_is_not_readmitted_and_loads_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lab = _Lab(tmp_path)
    saved = lab.save(_spec())
    run_id = _finish(lab.runs, _saved_run(saved))
    now = datetime(2026, 10, 17, tzinfo=UTC)
    monkeypatch.setattr(lab.runs, "_now", lambda: now)
    original_get = lab._strategies.get
    reads: list[int | None] = []

    def retired(strategy_id: str, revision: int | None = None) -> StrategyRevisionRecord:
        reads.append(revision)
        # 완료 조회가 revision 재해소를 시도하면 즉시 실패한다. 계열 확인은 최신 전략 조회다.
        assert revision is None
        record = original_get(strategy_id, revision)
        return replace(
            record,
            spec=replace(record.spec, identity=replace(record.spec.identity, schema_version="1.1")),
        )

    monkeypatch.setattr(lab._strategies, "get", retired)
    original_result = lab.runs.result
    loads: list[str] = []

    def verified(run_id: str) -> BacktestRunResult:
        loads.append(run_id)
        return original_result(run_id)

    monkeypatch.setattr(lab.runs, "result", verified)
    report = lab.runs.validation_report(run_id)
    assert report.observed_at == now
    assert report.dsr.trial_count == 1
    assert reads == [None]
    assert loads == [run_id]


def test_merged_lineage_uses_existing_survivor_without_changing_trial_count(tmp_path: Path) -> None:
    lab = _Lab(tmp_path)
    first = lab.save(_spec())
    second = lab.save(_spec(weight=2))
    run_id = _finish(lab.runs, _saved_run(first))
    _finish(lab.runs, _saved_run(second))
    merged = lab.runs.merge_lineages("s-1", "s-2")
    before = lab.runs.result(run_id)
    report = lab.runs.validation_report(run_id)
    assert report.dsr.lineage_id == "s-2"
    assert report.dsr.merged_lineage_ids == ("s-1",)
    assert report.dsr.trial_count == merged.trial_count == 2
    assert lab.runs.result(run_id) == before
    assert lab.runs.trial_ledger("s-2") == merged
