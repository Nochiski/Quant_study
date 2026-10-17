"""완료 run 의 팩터 회귀 조회(검증 랩 V4-05) — 실제 mock 실행 위에서 입력 조립과 경계를 본다."""

from __future__ import annotations

import random
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
