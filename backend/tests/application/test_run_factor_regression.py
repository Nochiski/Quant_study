"""완료 run 의 팩터 회귀 조회(검증 랩 V4-05) — 실제 mock 실행 위에서 입력 조립과 경계를 본다."""

from __future__ import annotations

import random
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from strategy_workbench.application.backtest_run.facade.factor_regression import (
    FactorRegressionUnavailableReason,
)
from strategy_workbench.application.portfolio_design.facade.design import (
    PortfolioPipelineResult,
    PortfolioPreviewRequest,
    PortfolioSnapshotMismatchError,
    RawObservationUnavailableError,
)
from strategy_workbench.domain.analytics.facade.factor_regression import (
    FactorPeriodInput,
    FactorSecurityPeriod,
    StrategyPeriodReturn,
    regress_on_factors,
)
from strategy_workbench.domain.equity.facade.research_data import DataLoadStatus
from strategy_workbench.domain.factor.facade.expression import MissingPolicy
from strategy_workbench.domain.portfolio.facade.construction import (
    PortfolioFactorValue,
    PortfolioFieldValue,
    PortfolioObservation,
)
from tests.application.test_trial_ledger import _finish, _Lab, _saved_run, _spec

_HALF_YEAR = {"start": date(2024, 1, 2), "end": date(2024, 6, 28)}


def test_three_name_mock_universe_reports_why_no_regression(tmp_path: Path) -> None:
    lab = _Lab(tmp_path)
    run_id = _finish(lab.runs, _saved_run(lab.save(_spec()), **_HALF_YEAR))
    before = lab.runs.result(run_id)
    report = lab.runs.factor_regression(run_id)
    # mock 유니버스는 3 종목이라 30·70 분위가 비어(⌊0.9⌋ = 0) 어느 구간도 2×3 정렬을 못 한다.
    assert report.report.fit is None
    assert (
        report.report.unavailable_reason
        is FactorRegressionUnavailableReason.INSUFFICIENT_OBSERVATIONS
    )
    assert report.report.observation_count == 0
    assert report.report.strategy_period_count == len(before.series.equity) - 1
    assert sum(item.count for item in report.report.excluded_periods) == (
        report.report.strategy_period_count
    )
    assert report.trial_count == 1
    assert report.alpha_t_threshold == 2.0
    assert report.data_snapshot_id == before.manifest.data_snapshot_id
    assert lab.runs.result(run_id) == before


def _cross_sections(sessions: list[date], seed: int) -> list[PortfolioObservation]:
    """세션마다 10 종목(+ 비구성 종목 하나). 가격은 무작위 걸음, 점수·시총은 무작위."""
    rng = random.Random(seed)
    prices = {f"S{index}": 100.0 for index in range(10)}
    rows: list[PortfolioObservation] = []
    for session in sessions:
        for security_id in prices:
            prices[security_id] *= 1 + rng.gauss(0, 0.02)
            rows.append(
                PortfolioObservation(
                    as_of=session,
                    security_id=security_id,
                    universe_member=True,
                    factor_values=(
                        PortfolioFactorValue("price.momentum_12_1", rng.random(), session),
                        PortfolioFactorValue("financial.book_to_market", rng.random(), session),
                    ),
                    fields=(
                        PortfolioFieldValue("price.adj_close", prices[security_id], session),
                        PortfolioFieldValue("price.market_cap", rng.uniform(1, 50), session),
                        PortfolioFieldValue("financial.book_equity", 1.0, session),
                        # 참거짓 필드는 숫자로 읽지 않는다.
                        PortfolioFieldValue("price.trading_halt", True, session),
                    ),
                )
            )
        rows.append(
            PortfolioObservation(
                as_of=session,
                security_id="OUT",
                universe_member=False,
                factor_values=(),
                fields=(PortfolioFieldValue("price.market_cap", 1e12, session),),
            )
        )
    return rows


def _hand_periods(rows: list[PortfolioObservation]) -> list[FactorPeriodInput]:
    """테스트가 직접 만든 기대 단면 — 구성 종목만, 끝 가격은 다음 세션 행."""
    by_key = {(row.as_of, row.security_id): row for row in rows}
    sessions = sorted({row.as_of for row in rows})

    def number(row: PortfolioObservation, field_id: str) -> float:
        value = next(item.value for item in row.fields if item.field_id == field_id)
        assert isinstance(value, float)
        return value

    periods: list[FactorPeriodInput] = []
    for start, end in zip(sessions, sessions[1:], strict=False):
        securities: list[FactorSecurityPeriod] = []
        for index in range(10):
            row = by_key[(start, f"S{index}")]
            values = {item.factor_id: item.value for item in row.factor_values}
            securities.append(
                FactorSecurityPeriod(
                    security_id=row.security_id,
                    market_cap=number(row, "price.market_cap"),
                    book_equity=1.0,
                    value_score=values["financial.book_to_market"],
                    momentum_score=values["price.momentum_12_1"],
                    start_price=number(row, "price.adj_close"),
                    end_price=number(by_key[(end, row.security_id)], "price.adj_close"),
                )
            )
        periods.append(FactorPeriodInput(start, end, tuple(securities)))
    return periods


def test_regression_uses_run_returns_and_pipeline_cross_sections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lab = _Lab(tmp_path)
    run_id = _finish(lab.runs, _saved_run(lab.save(_spec()), **_HALF_YEAR))
    result = lab.runs.result(run_id)
    sessions = [point.session for point in result.series.equity]
    rows = _cross_sections(sessions, seed=5)
    design = lab.runs._portfolio_design  # pyright: ignore[reportPrivateUsage]  # reason: 파이프라인 응답만 바꾼다
    original = design.run_pipeline
    requests: list[PortfolioPreviewRequest] = []

    def pipeline(request: PortfolioPreviewRequest) -> PortfolioPipelineResult:
        requests.append(request)
        return replace(original(request), observations=tuple(rows))

    monkeypatch.setattr(design, "run_pipeline", pipeline)
    report = lab.runs.factor_regression(run_id)

    (request,) = requests
    assert [factor.factor_id for factor in request.spec.factors] == [
        "price.momentum_12_1",
        "financial.book_to_market",
    ]
    assert request.environment is not None
    assert request.environment.missing is MissingPolicy.DROP
    assert replace(request.environment, missing=result.manifest.environment.missing) == (
        result.manifest.environment
    )
    strategy = [
        StrategyPeriodReturn(before.session, after.session, after.equity / before.equity - 1)
        for before, after in zip(result.series.equity, result.series.equity[1:], strict=False)
    ]
    expected = regress_on_factors(strategy, _hand_periods(rows), annualization_days=252)
    assert expected.fit is not None
    assert report.report == expected
    # 단면을 계산한 판본을 함께 싣는다 — 같은 run 이라도 규칙이 바뀌면 값이 달라질 수 있다.
    assert report.factor_registry_version
    assert report.factor_evaluation_version is not None


def test_changed_data_snapshot_is_not_regressed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lab = _Lab(tmp_path)
    run_id = _finish(lab.runs, _saved_run(lab.save(_spec())))
    design = lab.runs._portfolio_design  # pyright: ignore[reportPrivateUsage]  # reason: 데이터 판만 바꾼다
    original = design.run_pipeline
    monkeypatch.setattr(
        design,
        "run_pipeline",
        lambda request: replace(original(request), data_snapshot_id="other-snapshot"),
    )
    report = lab.runs.factor_regression(run_id).report
    assert report.unavailable_reason is FactorRegressionUnavailableReason.DATA_SNAPSHOT_CHANGED
    assert report.observation_count == 0
    assert report.excluded_periods == ()


def test_unreadable_inputs_are_reported_as_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lab = _Lab(tmp_path)
    run_id = _finish(lab.runs, _saved_run(lab.save(_spec())))
    design = lab.runs._portfolio_design  # pyright: ignore[reportPrivateUsage]  # reason: 원천 부재를 흉내 낸다

    def unavailable(request: PortfolioPreviewRequest) -> PortfolioPipelineResult:
        raise RawObservationUnavailableError(DataLoadStatus.NO_DATA, "no rows for the window")

    monkeypatch.setattr(design, "run_pipeline", unavailable)
    response = lab.runs.factor_regression(run_id)
    report = response.report
    assert report.unavailable_reason is FactorRegressionUnavailableReason.DATA_UNAVAILABLE
    assert report.strategy_period_count > 0
    assert report.excluded_periods == ()
    assert response.factor_evaluation_version is None


def test_snapshot_moving_during_the_read_is_reported_as_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lab = _Lab(tmp_path)
    run_id = _finish(lab.runs, _saved_run(lab.save(_spec())))
    design = lab.runs._portfolio_design  # pyright: ignore[reportPrivateUsage]  # reason: 조회 중 판 경합을 흉내 낸다

    def moved(request: PortfolioPreviewRequest) -> PortfolioPipelineResult:
        raise PortfolioSnapshotMismatchError(expected="a", actual="b")

    monkeypatch.setattr(design, "run_pipeline", moved)
    report = lab.runs.factor_regression(run_id).report
    assert report.unavailable_reason is FactorRegressionUnavailableReason.DATA_SNAPSHOT_CHANGED


def _counting_pipeline(lab: _Lab, monkeypatch: pytest.MonkeyPatch) -> list[PortfolioPreviewRequest]:
    """파이프라인을 부를 때마다 요청을 남긴다(팩터 단면을 몇 번 다시 읽었나)."""
    design = lab.runs._portfolio_design  # pyright: ignore[reportPrivateUsage]  # reason: 호출 수만 센다
    original = design.run_pipeline
    calls: list[PortfolioPreviewRequest] = []

    def counted(request: PortfolioPreviewRequest) -> PortfolioPipelineResult:
        calls.append(request)
        return original(request)

    monkeypatch.setattr(design, "run_pipeline", counted)
    return calls


def test_factor_returns_are_computed_once_per_environment_and_shared_across_strategies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """실데이터 확인 후속 1: 팩터 수익률은 전략과 무관해 같은 실행 설정·판이면 다시 읽지 않는다."""
    lab = _Lab(tmp_path)
    first = _finish(lab.runs, _saved_run(lab.save(_spec()), **_HALF_YEAR))
    second = _finish(lab.runs, _saved_run(lab.save(_spec()), **_HALF_YEAR))
    other_period = _finish(lab.runs, _saved_run(lab.save(_spec())))
    calls = _counting_pipeline(lab, monkeypatch)

    reports = [lab.runs.factor_regression(run_id) for run_id in (first, first, second)]

    assert len(calls) == 1
    assert reports[0] == reports[1]
    assert reports[2].report.strategy_period_count == reports[0].report.strategy_period_count
    # 기간이 다르면 다른 단면이다.
    lab.runs.factor_regression(other_period)
    assert len(calls) == 2


def test_factor_returns_are_computed_in_the_background_once_and_then_served(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lab = _Lab(tmp_path)
    jobs: list[Callable[[], None]] = []
    lab.runs._background = jobs.append  # pyright: ignore[reportPrivateUsage]  # reason: 뒤 계산을 붙잡아 둔다
    run_id = _finish(lab.runs, _saved_run(lab.save(_spec()), **_HALF_YEAR))
    calls = _counting_pipeline(lab, monkeypatch)

    waiting = [lab.runs.factor_regression(run_id) for _ in range(2)]

    # 조회는 기다리지 않고 "계산 중"을 돌려주며, 같은 키의 계산은 하나만 건다.
    assert [item.report.unavailable_reason for item in waiting] == [
        FactorRegressionUnavailableReason.COMPUTING
    ] * 2
    assert all(item.factor_evaluation_version is None for item in waiting)
    assert (len(jobs), len(calls)) == (1, 0)
    jobs[0]()
    done = lab.runs.factor_regression(run_id)
    assert done.report.unavailable_reason is (
        FactorRegressionUnavailableReason.INSUFFICIENT_OBSERVATIONS
    )
    assert done.factor_evaluation_version is not None
    assert (len(jobs), len(calls)) == (1, 1)


def test_different_keys_do_not_create_waiting_background_jobs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """다른 기간의 동시 조회도 실행자 하나만 접수하고, 완료 뒤 재조회로 다음 키를 접수한다."""
    lab = _Lab(tmp_path)
    saved = lab.save(_spec())
    run_ids = [
        _finish(
            lab.runs,
            _saved_run(saved, start=date(2024, 1, day), end=_HALF_YEAR["end"]),
        )
        for day in (2, 3, 4, 5)
    ]
    before = [lab.runs.result(run_id) for run_id in run_ids]
    jobs: list[Callable[[], None]] = []
    lab.runs._background = jobs.append  # pyright: ignore[reportPrivateUsage]  # reason: 접수한 실행자를 붙잡는다
    calls = _counting_pipeline(lab, monkeypatch)
    first = lab.runs.factor_regression(run_ids[0])
    with ThreadPoolExecutor(max_workers=8) as pool:
        waiting = list(pool.map(lab.runs.factor_regression, run_ids * 4))

    assert first.report.unavailable_reason is FactorRegressionUnavailableReason.COMPUTING
    assert all(
        report.report.unavailable_reason is FactorRegressionUnavailableReason.COMPUTING
        for report in waiting
    )
    assert (len(jobs), len(calls)) == (1, 0)
    jobs[0]()
    # 먼저 계산한 키는 캐시에서 읽는다. 기다리던 키는 자동으로 실행되지 않는다.
    assert lab.runs.factor_regression(run_ids[0]).factor_evaluation_version is not None
    assert (len(jobs), len(calls)) == (1, 1)
    next_waiting = lab.runs.factor_regression(run_ids[1])
    assert next_waiting.report.unavailable_reason is FactorRegressionUnavailableReason.COMPUTING
    assert (len(jobs), len(calls)) == (2, 1)
    jobs[1]()
    done = lab.runs.factor_regression(run_ids[1])
    assert done.factor_evaluation_version is not None
    assert (len(jobs), len(calls)) == (2, 2)
    assert [lab.runs.result(run_id) for run_id in run_ids] == before
    assert {report.trial_count for report in waiting} == {done.trial_count}


def test_an_unexpected_failure_is_reported_once_and_retried_on_the_next_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    lab = _Lab(tmp_path)
    run_id = _finish(lab.runs, _saved_run(lab.save(_spec()), **_HALF_YEAR))
    design = lab.runs._portfolio_design  # pyright: ignore[reportPrivateUsage]  # reason: 예상 밖 오류를 흉내 낸다
    original = design.run_pipeline
    broken = [True]

    def flaky(request: PortfolioPreviewRequest) -> PortfolioPipelineResult:
        if broken[0]:
            raise RuntimeError("disk went away")
        return original(request)

    monkeypatch.setattr(design, "run_pipeline", flaky)

    failed = lab.runs.factor_regression(run_id).report
    broken[0] = False
    retried = lab.runs.factor_regression(run_id).report

    assert failed.unavailable_reason is FactorRegressionUnavailableReason.COMPUTATION_FAILED
    assert f"run_id={run_id}" in caplog.text
    assert retried.unavailable_reason is (
        FactorRegressionUnavailableReason.INSUFFICIENT_OBSERVATIONS
    )


def test_a_changed_snapshot_is_kept_and_not_recomputed_on_every_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#544 리뷰 P3-2: 데이터 판은 프로세스 안에서 고정이라 다시 계산해도 같다."""
    lab = _Lab(tmp_path)
    run_id = _finish(lab.runs, _saved_run(lab.save(_spec())))
    design = lab.runs._portfolio_design  # pyright: ignore[reportPrivateUsage]  # reason: 데이터 판만 바꾼다
    original = design.run_pipeline
    calls: list[PortfolioPreviewRequest] = []

    def other_snapshot(request: PortfolioPreviewRequest) -> PortfolioPipelineResult:
        calls.append(request)
        return replace(original(request), data_snapshot_id="other-snapshot")

    monkeypatch.setattr(design, "run_pipeline", other_snapshot)

    reasons = [lab.runs.factor_regression(run_id).report.unavailable_reason for _ in range(3)]

    assert reasons == [FactorRegressionUnavailableReason.DATA_SNAPSHOT_CHANGED] * 3
    assert len(calls) == 1


def test_a_job_that_cannot_start_does_not_leave_the_key_computing_forever(
    tmp_path: Path,
) -> None:
    """#544 리뷰 P3-3: 스레드를 띄우지 못하면 그 요청은 실패하고 다음 조회가 다시 건다."""
    lab = _Lab(tmp_path)
    run_id = _finish(lab.runs, _saved_run(lab.save(_spec()), **_HALF_YEAR))

    def no_threads(job: Callable[[], None]) -> None:
        raise RuntimeError("can't start new thread")

    lab.runs._background = no_threads  # pyright: ignore[reportPrivateUsage]  # reason: 스레드 한도를 흉내 낸다
    with pytest.raises(RuntimeError, match="can't start new thread"):
        lab.runs.factor_regression(run_id)
    lab.runs._background = lambda job: job()  # pyright: ignore[reportPrivateUsage]  # reason: 다시 그 자리에서 돌린다

    report = lab.runs.factor_regression(run_id).report

    assert report.unavailable_reason is FactorRegressionUnavailableReason.INSUFFICIENT_OBSERVATIONS
