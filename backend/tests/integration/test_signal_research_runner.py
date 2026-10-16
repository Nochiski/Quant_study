"""임시 합성 원장의 S01 보존 검증부터 실제 DuckDB·파이프라인·CLI 평가까지 연결한다."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import asdict, replace
from datetime import date
from pathlib import Path

import pytest

from strategy_workbench.adapters.outbound.equity_duckdb.facade.provider import EquityDuckdbAdapter
from strategy_workbench.adapters.outbound.equity_duckdb.facade.signal_labels import (
    DuckdbSignalLabelAdapter,
)
from strategy_workbench.adapters.outbound.research_snapshot.facade.guard import (
    ResearchSnapshotGuard,
)
from strategy_workbench.adapters.outbound.strategy_memory.facade.repository import (
    InMemoryStrategyRepository,
)
from strategy_workbench.application.signal_research.facade.ports import (
    LabelDataQuery,
    LabelDataStatus,
)
from strategy_workbench.application.strategy_design.facade.design import StrategyDesignService
from strategy_workbench.bootstrap.signal_research.facade.command import main
from strategy_workbench.domain.backtest.facade.environment import RunEnvironment
from strategy_workbench.domain.canonical.facade.encoding import canonical_payload_json
from strategy_workbench.domain.factor.facade.expression import FactorGraph, FieldNode
from strategy_workbench.domain.portfolio.facade.construction import (
    PortfolioFactorValue,
    PortfolioObservation,
)
from strategy_workbench.domain.signal_evaluation.facade.evaluation import (
    AdjustmentGapObservation,
    PriceLabelSpec,
    PriceLabelStatus,
    evaluate_signal,
)
from strategy_workbench.domain.strategy.facade.specification import (
    FactorDirection,
    FactorSignal,
    RebalanceFrequency,
    canonical_strategy_json,
)
from tests.equity_fixture import (
    WB_SPANS,
    build_workbench_root,
    import_ledger_module,
    span_table,
    write_equity_table,
)
from tests.test_research_snapshot_integration import _source

REPO = Path(__file__).resolve().parents[3]
START, SIGNAL_END, CUTOFF = date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 5)
CALENDAR_END = date(2024, 1, 12)


def _arguments(tmp_path: Path, frozen: Path) -> list[str]:
    template = StrategyDesignService(
        InMemoryStrategyRepository(), new_id=lambda: "unused"
    ).template()
    spec = replace(
        template,
        factors=(
            FactorSignal(
                factor_id="close-signal",
                label="합성 종가 신호",
                direction=FactorDirection.HIGH,
                weight=1.0,
                graph=FactorGraph((FieldNode("close", "price.close", "field"),), "close"),
            ),
        ),
        portfolio=replace(
            template.portfolio,
            selection_count=1,
            rebalance=RebalanceFrequency.EVERY_N_SESSIONS,
            rebalance_every_n_sessions=1,
        ),
    )
    strategy = tmp_path / "strategy.json"
    environment = tmp_path / "environment.json"
    strategy.write_text(canonical_strategy_json(spec), encoding="utf-8")
    environment.write_text(
        canonical_payload_json(
            asdict(RunEnvironment(start=START, end=SIGNAL_END, universe_id="krx.common-stock"))
        ),
        encoding="utf-8",
    )
    return [
        "--preserved-equity-root",
        str(frozen),
        "--repo-root",
        str(REPO),
        "--strategy-json",
        str(strategy),
        "--environment-json",
        str(environment),
        "--factor-id",
        "close-signal",
        "--horizon-sessions",
        "1",
        "2",
        "--minimum-pairs",
        "2",
        "--evaluation-as-of",
        str(CUTOFF),
        "--label-calendar-through",
        str(CALENDAR_END),
        "--output-json",
        "-",
    ]


def test_cli_exports_actual_pipeline_values_and_separate_labels_from_preserved_duckdb(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = _source(tmp_path)
    frozen = tmp_path / "preserved"
    snapshots = import_ledger_module("equity.research_snapshot")
    created = snapshots.create_snapshot(source, frozen, repo_root=REPO)
    assert created.ok, created.detail
    assert main(_arguments(tmp_path, frozen)) == 0
    output = capsys.readouterr()
    assert output.err == ""
    # 파일 산출물 검사가 아니라 CLI가 반환한 JSON 직렬화·평가 계산 계약을 검사한다.
    payload = json.loads(output.out)
    assert payload["schema_version"] == "signal-research-v1"
    assert payload["input_proof"]["ledger_snapshot_id"] == created.ledger_snapshot_id
    assert not Path(payload["input_proof"]["root_reference"]).is_absolute()
    assert payload["signals"]["data_snapshot_id"] == payload["label_data"]["data_snapshot_id"]
    result = payload["evaluations"][0]
    first_sample = next(
        row
        for row in result["samples"]
        if row["signal_session"] == str(START)
        and row["security_id"] == "005930:1"
        and row["horizon_sessions"] == 1
    )
    assert first_sample["signal_value"] == 72_000
    assert first_sample["label"]["return_fraction"] == pytest.approx(72_500 / 72_000 - 1)
    assert first_sample["label"]["entry_session"] == str(START)
    assert first_sample["label"]["label_end"] == "2024-01-03"
    # 높은 가격인 종목이 작은 비율로 오른다. 독립 손계산 상관은 −1이다.
    first_daily = result["daily"][0]
    assert first_daily["rank_ic"] == pytest.approx(-1)
    assert first_daily["paired_count"] == first_daily["universe_count"] == 2
    assert first_daily["coverage_fraction"] == 1
    assert result["label_spec"]["horizons_sessions"] == [1, 2]
    assert all(row["session"] <= str(CUTOFF) for row in payload["label_data"]["prices"])
    assert all(
        row["last_session"] == str(CALENDAR_END)
        for row in payload["label_data"]["listing_evidence"]
    )
    assert payload["limitations"]


def test_cli_rejects_unpreserved_or_tampered_input_without_mock_fallback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = _source(tmp_path)
    assert main(_arguments(tmp_path, source)) == 2
    assert "S01 snapshot verification failed" in capsys.readouterr().err
    frozen = tmp_path / "preserved"
    snapshots = import_ledger_module("equity.research_snapshot")
    created = snapshots.create_snapshot(source, frozen, repo_root=REPO)
    assert created.ok, created.detail
    (frozen / "unexpected.txt").write_text("변경된 합성 입력", encoding="utf-8")
    assert main(_arguments(tmp_path, frozen)) == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert "S01 snapshot verification failed" in output.err


def test_guard_checks_current_code_provenance_and_reuses_s01_identity(tmp_path: Path) -> None:
    source = _source(tmp_path)
    frozen = tmp_path / "preserved"
    created = import_ledger_module("equity.research_snapshot").create_snapshot(
        source, frozen, repo_root=REPO
    )
    assert created.ok, created.detail
    proof = ResearchSnapshotGuard(frozen, REPO).verify()
    assert proof.ledger_snapshot_id == created.ledger_snapshot_id
    assert proof.contract_sources_sha256 == created.contract_sources_sha256
    assert len(proof.descriptor_sha256) == 64


def test_cli_rejects_other_checkout_even_when_its_preserved_code_verifies(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshots = import_ledger_module("equity.research_snapshot")
    other_repo = tmp_path / "다른 checkout"
    # 로컬 Git 객체만 공유하고 새 커밋·네트워크 없이 별도 코드 판본을 만든다.
    subprocess.run(
        ["git", "clone", "--shared", "--no-checkout", "--quiet", str(REPO), str(other_repo)],
        check=True,
    )
    source_names = subprocess.check_output(
        [
            "git",
            "-C",
            str(REPO),
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "--",
            "database/src",
            "backend/src",
            "backend/rust",
        ],
        text=True,
    ).splitlines()
    for relative in (
        set(source_names)
        | set(snapshots.CONTRACT_FILES)
        | set(snapshots.ENVIRONMENT_FILES)
        | {".gitignore"}
    ):
        source_file = REPO / relative
        if source_file.is_file():
            destination = other_repo / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_file, destination)
    evaluator = (
        other_repo / "backend/src/strategy_workbench/domain/signal_evaluation/_evaluation.py"
    )
    evaluator.write_text(
        evaluator.read_text(encoding="utf-8").replace(
            "return_fraction = exit_price.close / entry_price.close - 1.0",
            "return_fraction = 999.0",
        ),
        encoding="utf-8",
    )
    source = _source(tmp_path)
    frozen = tmp_path / "preserved"
    created = snapshots.create_snapshot(source, frozen, repo_root=other_repo)
    assert created.ok, created.detail
    checked = snapshots.verify_snapshot(frozen, repo_root=other_repo)
    assert checked.ok, checked.detail
    arguments = _arguments(tmp_path, frozen)
    arguments[arguments.index("--repo-root") + 1] = str(other_repo)
    # sys.path에 검증 저장소를 추가해도 이미 import된 실행 코드는 바뀌지 않는다.
    monkeypatch.syspath_prepend(str(other_repo / "backend/src"))
    assert main(arguments) == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert "검증 저장소와 다릅니다" in output.err


@pytest.mark.parametrize("attribute", ["__file__", "spec_origin", "__path__", "spec_paths"])
def test_guard_rejects_mixed_submodule_origin_and_package_search_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, attribute: str
) -> None:
    module = sys.modules["strategy_workbench.domain.signal_evaluation"]
    spec = module.__spec__
    assert spec is not None
    outside = tmp_path / "unverified"
    if attribute == "spec_origin":
        monkeypatch.setattr(spec, "origin", str(outside / "__init__.py"))
    elif attribute == "spec_paths":
        monkeypatch.setattr(spec, "submodule_search_locations", [str(outside)])
    elif attribute == "__path__":
        monkeypatch.setattr(module, "__path__", [*module.__path__, str(outside)])
    else:
        # 최상위 패키지 출처가 정상이어도 이미 import된 하위 파일을 별도로 검사한다.
        module = sys.modules["strategy_workbench.domain.signal_evaluation._evaluation"]
        monkeypatch.setattr(module, "__file__", str(outside / "_evaluation.py"))
    with pytest.raises(ValueError, match="검증 저장소와 다릅니다"):
        ResearchSnapshotGuard(tmp_path / "not-opened", REPO).verify()


def test_cli_accepts_symlink_repo_root_with_unicode_and_spaces(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = _source(tmp_path)
    frozen = tmp_path / "preserved"
    repo_link = tmp_path / "한글 checkout 링크"
    repo_link.symlink_to(REPO, target_is_directory=True)
    created = import_ledger_module("equity.research_snapshot").create_snapshot(
        source, frozen, repo_root=repo_link
    )
    assert created.ok, created.detail
    arguments = _arguments(tmp_path, frozen)
    arguments[arguments.index("--repo-root") + 1] = str(repo_link)
    assert main(arguments) == 0
    output = capsys.readouterr()
    assert output.err == ""
    assert json.loads(output.out)["input_proof"]["ledger_snapshot_id"] == created.ledger_snapshot_id


def test_coverage_end_is_not_inferred_from_the_query_membership_end(tmp_path: Path) -> None:
    equity = EquityDuckdbAdapter(build_workbench_root(tmp_path / "source"))
    loaded = DuckdbSignalLabelAdapter(equity).load(
        LabelDataQuery(START, SIGNAL_END, SIGNAL_END, ("005930:1",))
    )
    assert loaded.status is LabelDataStatus.READY
    assert loaded.data is not None
    assert loaded.data.listing_evidence[0].last_session == CALENDAR_END
    assert loaded.data.listing_evidence[0].end_reason == "coverage_gap"
    assert loaded.data.listings[0].last_session == CALENDAR_END


@pytest.mark.parametrize("reason", ["delisted", "coverage_gap", "data_gap"])
def test_actual_span_end_reason_controls_delisting_and_unknown_coverage(
    tmp_path: Path, reason: str
) -> None:
    root = build_workbench_root(tmp_path / "source")
    spans = [
        (ticker, seq, first, SIGNAL_END, reason)
        if ticker == "005930"
        else (ticker, seq, first, last, end_reason)
        for ticker, seq, first, last, end_reason in WB_SPANS
    ]
    write_equity_table(root, "security_span", span_table(spans))
    # 같은 build ID의 합성 테이블을 직접 바꾼 이 검사는 label adapter 계약만 검사한다.
    # 보존 무결성은 위 CLI E2E에서 별도 검사하며 이 root를 S01 보존 root로 가장하지 않는다.
    equity = EquityDuckdbAdapter(root)
    loaded = DuckdbSignalLabelAdapter(equity).load(
        LabelDataQuery(START, CUTOFF, CALENDAR_END, ("005930:1",))
    )
    if reason == "coverage_gap":
        assert loaded.status is LabelDataStatus.UNSUPPORTED
        assert loaded.detail is not None and "수집 상한" in loaded.detail
        assert loaded.data is None
        return
    assert loaded.status is LabelDataStatus.READY
    assert loaded.data is not None
    result = evaluate_signal(
        observations=(
            PortfolioObservation(
                START, "005930:1", True, (PortfolioFactorValue("factor", 1.0, START),)
            ),
        ),
        factor_id="factor",
        signal_snapshot_id=loaded.data.data_snapshot_id,
        price_snapshot_id=loaded.data.data_snapshot_id,
        sessions=loaded.data.sessions,
        prices=loaded.data.prices,
        listings=loaded.data.listings,
        label_spec=PriceLabelSpec((2,)),
        evaluation_as_of=CUTOFF,
    )
    expected = (
        PriceLabelStatus.LISTING_ENDED_UNSETTLED
        if reason == "delisted"
        else PriceLabelStatus.LISTING_STATUS_UNKNOWN
    )
    assert result.samples[0].label.status is expected
    assert result.daily[0].coverage_fraction == 0
    assert result.samples[0].label.return_fraction is None


def test_masked_gap_only_invalidates_intersecting_labels_and_preserves_population(
    tmp_path: Path,
) -> None:
    equity = EquityDuckdbAdapter(build_workbench_root(tmp_path / "source"))
    signal = date(2024, 1, 5)
    loaded = DuckdbSignalLabelAdapter(equity).load(
        LabelDataQuery(signal, CALENDAR_END, CALENDAR_END, ("035420:1", "005930:1"))
    )
    assert loaded.status is LabelDataStatus.READY
    data = loaded.data
    assert data is not None
    assert data.adjustment_gaps == (
        AdjustmentGapObservation(date(2024, 1, 9), "035420:1"),
        AdjustmentGapObservation(date(2024, 1, 11), "035420:1"),
    )
    price_keys = {(price.session, price.security_id) for price in data.prices}
    assert (date(2024, 1, 8), "035420:1") in price_keys
    assert (CALENDAR_END, "035420:1") in price_keys
    assert not any((gap.session, gap.security_id) in price_keys for gap in data.adjustment_gaps)
    result = evaluate_signal(
        observations=tuple(
            PortfolioObservation(
                session, security_id, True, (PortfolioFactorValue("factor", value, session),)
            )
            for session in (signal, date(2024, 1, 8))
            for value, security_id in enumerate(("035420:1", "005930:1"), 1)
        ),
        factor_id="factor",
        signal_snapshot_id=data.data_snapshot_id,
        price_snapshot_id=data.data_snapshot_id,
        sessions=data.sessions,
        prices=data.prices,
        adjustment_gaps=data.adjustment_gaps,
        listings=data.listings,
        label_spec=PriceLabelSpec((1, 4), minimum_pairs=2),
        evaluation_as_of=CALENDAR_END,
    )
    samples = {
        (row.signal_session, row.security_id, row.horizon_sessions): row for row in result.samples
    }
    assert samples[(signal, "035420:1", 1)].label.status is PriceLabelStatus.OK
    assert samples[(signal, "035420:1", 1)].label.return_fraction == pytest.approx(
        204_000 / 203_500 - 1
    )
    assert samples[(date(2024, 1, 8), "035420:1", 4)].label.status is (
        PriceLabelStatus.ADJUSTMENT_GAP
    )
    assert all(
        row.label.status is PriceLabelStatus.OK
        for row in result.samples
        if row.security_id == "005930:1"
    )
    for daily in result.daily:
        assert daily.universe_count == 2
        expected_pairs = 2 if daily.signal_session == signal and daily.horizon_sessions == 1 else 1
        assert daily.paired_count == expected_pairs
        assert daily.coverage_fraction == expected_pairs / 2
        if expected_pairs == 1:
            assert {item.reason: item.count for item in daily.label_status_counts} == {
                "ok": 1,
                "adjustment_gap": 1,
            }
            assert {item.reason: item.count for item in daily.exclusions} == {"adjustment_gap": 1}


def test_cli_exports_masked_gap_evidence_and_per_label_status_from_preserved_input(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = _source(tmp_path)
    frozen = tmp_path / "preserved"
    created = import_ledger_module("equity.research_snapshot").create_snapshot(
        source, frozen, repo_root=REPO
    )
    assert created.ok, created.detail
    arguments = _arguments(tmp_path, frozen)
    environment_path = tmp_path / "environment.json"
    environment_path.write_text(
        canonical_payload_json(
            asdict(
                RunEnvironment(
                    start=date(2024, 1, 5), end=date(2024, 1, 8), universe_id="krx.common-stock"
                )
            )
        ),
        encoding="utf-8",
    )
    arguments[arguments.index("--evaluation-as-of") + 1] = "2024-01-11"
    assert main(arguments) == 0
    output = capsys.readouterr()
    assert output.err == ""
    payload = json.loads(output.out)
    assert payload["schema_version"] == "signal-research-v1"
    assert payload["label_data"]["adjustment_gaps"] == [
        {"session": "2024-01-09", "security_id": "035420:1"},
        {"session": "2024-01-11", "security_id": "035420:1"},
    ]
    result = payload["evaluations"][0]
    by_key = {
        (row["signal_session"], row["security_id"], row["horizon_sessions"]): row
        for row in result["samples"]
    }
    normal = by_key[("2024-01-05", "035420:1", 1)]
    affected = by_key[("2024-01-05", "035420:1", 2)]
    assert normal["label"]["status"] == "ok"
    assert affected["label"]["status"] == "adjustment_gap"
    assert affected["label"]["return_fraction"] is None
    assert normal["signal_value"] == affected["signal_value"]
    daily = next(
        row
        for row in result["daily"]
        if row["signal_session"] == "2024-01-05" and row["horizon_sessions"] == 2
    )
    assert daily["universe_count"] == 3
    assert daily["paired_count"] == 2
    assert daily["coverage_fraction"] == pytest.approx(2 / 3)
    assert {row["reason"]: row["count"] for row in daily["exclusions"]} == {"adjustment_gap": 1}


def test_suspended_reference_price_is_not_an_entry_or_exit_trade_price(tmp_path: Path) -> None:
    equity = EquityDuckdbAdapter(build_workbench_root(tmp_path / "source"))
    signal = date(2024, 1, 9)
    loaded = DuckdbSignalLabelAdapter(equity).load(
        LabelDataQuery(signal, CALENDAR_END, CALENDAR_END, ("000660:1",))
    )
    assert loaded.status is LabelDataStatus.READY
    assert loaded.data is not None
    assert (date(2024, 1, 10), "000660:1") not in {
        (row.session, row.security_id) for row in loaded.data.prices
    }
    result = evaluate_signal(
        observations=(
            PortfolioObservation(
                signal, "000660:1", True, (PortfolioFactorValue("factor", 1.0, signal),)
            ),
        ),
        factor_id="factor",
        signal_snapshot_id=loaded.data.data_snapshot_id,
        price_snapshot_id=loaded.data.data_snapshot_id,
        sessions=loaded.data.sessions,
        prices=loaded.data.prices,
        listings=loaded.data.listings,
        label_spec=PriceLabelSpec((1, 2)),
        evaluation_as_of=CALENDAR_END,
    )
    assert result.samples[0].label.status is PriceLabelStatus.MISSING_EXIT
    assert result.samples[1].label.status is PriceLabelStatus.OK


def test_missing_adjusted_price_contract_is_not_replaced_with_raw_or_mock(tmp_path: Path) -> None:
    equity = EquityDuckdbAdapter(build_workbench_root(tmp_path / "source", catalog=False))
    loaded = DuckdbSignalLabelAdapter(equity).load(
        LabelDataQuery(START, CUTOFF, CALENDAR_END, ("005930:1",))
    )
    assert loaded.status is LabelDataStatus.UNSUPPORTED
    assert loaded.detail is not None and "price.adj_close" in loaded.detail
    assert loaded.data is None


def test_cli_rejects_duplicate_json_keys_before_constructing_an_adapter(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    arguments = _arguments(tmp_path, tmp_path / "absent-root")
    (tmp_path / "environment.json").write_text(
        '{"start":"2024-01-02","start":"2024-01-03"}', encoding="utf-8"
    )
    assert main(arguments) == 2
    output = capsys.readouterr()
    assert "중복" in output.err
    assert "S01" not in output.err
    assert output.out == ""


def test_cli_unicode_and_space_paths_round_trip_through_s01_subprocess(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = _source(tmp_path)
    frozen = tmp_path / "한글 보존 자료"
    created = import_ledger_module("equity.research_snapshot").create_snapshot(
        source, frozen, repo_root=REPO
    )
    assert created.ok, created.detail
    assert main(_arguments(tmp_path, frozen)) == 0
    output = capsys.readouterr()
    assert output.err == ""
    payload = json.loads(output.out)
    assert "한글 보존 자료" in payload["input_proof"]["root_reference"]
