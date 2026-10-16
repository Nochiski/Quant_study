"""공통 달력의 미래 가격 라벨과 날짜별 순위 상관을 손계산으로 검증한다."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import date

import pytest

from strategy_workbench.domain.backtest.facade.environment import ResearchWindowViolationError
from strategy_workbench.domain.portfolio.facade.construction import (
    PortfolioFactorValue,
    PortfolioObservation,
)
from strategy_workbench.domain.signal_evaluation.facade.evaluation import (
    AdjustedCloseObservation,
    ListingInterval,
    PriceLabelSpec,
    PriceLabelStatus,
    RankICStatus,
    SignalEvaluationResult,
    SignalStatus,
    evaluate_signal,
)

D0, D1, D2, D3 = (date(2024, 1, day) for day in (5, 8, 9, 10))
SESSIONS = (D0, D1, D2, D3)


def _signal(
    security_id: str, value: float | None, *, session: date = D0, available: date = D0
) -> PortfolioObservation:
    return PortfolioObservation(
        session, security_id, True, (PortfolioFactorValue("factor", value, available),)
    )


def _price(security_id: str, session: date, close: float) -> AdjustedCloseObservation:
    return AdjustedCloseObservation(session, security_id, close, session)


def _evaluate(
    observations: tuple[PortfolioObservation, ...],
    prices: tuple[AdjustedCloseObservation, ...],
    *,
    listings: tuple[ListingInterval, ...] | None = None,
    spec: PriceLabelSpec | None = None,
    cutoff: date = D3,
    price_snapshot: str = "synthetic",
    sessions: tuple[date, ...] = SESSIONS,
) -> SignalEvaluationResult:
    return evaluate_signal(
        observations=observations,
        factor_id="factor",
        signal_snapshot_id="synthetic",
        price_snapshot_id=price_snapshot,
        sessions=sessions,
        prices=prices,
        listings=listings
        if listings is not None
        else tuple(
            ListingInterval(security_id, D0, D3)
            for security_id in sorted({row.security_id for row in observations})
        ),
        label_spec=spec or PriceLabelSpec((1,)),
        evaluation_as_of=cutoff,
    )


def test_tied_ranks_use_average_rank_and_do_not_depend_on_input_order() -> None:
    signals = tuple(
        _signal(security_id, value)
        for security_id, value in zip("abcd", (1.0, 2.0, 2.0, 4.0), strict=True)
    )
    prices = tuple(
        price
        for security_id, end in zip("abcd", (130.0, 110.0, 110.0, 120.0), strict=True)
        for price in (_price(security_id, D0, 100.0), _price(security_id, D1, end))
    )
    result = _evaluate(signals, prices)
    # 평균 순위 [1,2.5,2.5,4]와 [4,1.5,1.5,3]: 편차곱 합 −1.5, 편차제곱 합은 각각 4.5.
    assert result.daily[0].rank_ic == pytest.approx(-1 / 3)
    assert result.daily[0].coverage_fraction == 1.0
    assert result.daily[0].status is RankICStatus.OK
    reversed_result = _evaluate(tuple(reversed(signals)), tuple(reversed(prices)))
    assert reversed_result == result


def test_common_calendar_does_not_skip_a_suspended_security_day() -> None:
    result = _evaluate(
        (_signal("a", 1.0),),
        (_price("a", D0, 100), _price("a", D2, 120), _price("a", D3, 150)),
        spec=PriceLabelSpec((1, 2)),
    )
    first, second = result.samples
    assert first.label.label_end == D1
    assert first.label.status is PriceLabelStatus.MISSING_EXIT
    assert second.label.label_end == D2
    assert second.label.return_fraction == pytest.approx(0.2)
    delayed = _evaluate(
        (_signal("a", 1.0),),
        (_price("a", D2, 120),),
        spec=PriceLabelSpec((1,), entry_delay_sessions=1),
    )
    assert delayed.samples[0].label.entry_session == D1
    assert delayed.samples[0].label.label_end == D2
    assert delayed.samples[0].label.status is PriceLabelStatus.MISSING_ENTRY


def test_entry_delay_and_horizon_count_price_intervals_from_common_sessions() -> None:
    result = _evaluate(
        (_signal("a", 1.0),),
        tuple(
            _price("a", session, close)
            for session, close in zip(SESSIONS, (10, 20, 50, 80), strict=True)
        ),
        spec=PriceLabelSpec((2,), entry_delay_sessions=1),
    )
    label = result.samples[0].label
    assert (label.entry_session, label.label_end) == (D1, D3)
    assert label.return_fraction == pytest.approx(80 / 20 - 1)


def test_missing_maturity_and_listing_counts_keep_the_original_universe_denominator() -> None:
    signals = tuple(
        _signal(security_id, float(index)) for index, security_id in enumerate("abcdefghij")
    )
    signals = (*signals[:8], _signal("i", 8.0, available=D1), _signal("j", None))
    prices = tuple(
        replace(
            _price(security_id, session, 100 + index if session == D1 else 100),
            available_date=D2 if security_id == "h" else session,
        )
        for index, security_id in enumerate("abcdefghij")
        for session in (D0, D1)
        if not (security_id == "d" and session == D0) and not (security_id == "e" and session == D1)
    )
    listings = tuple(
        ListingInterval(security_id, D0, D0 if security_id == "f" else D3)
        for security_id in "abcdefhij"
    )
    result = _evaluate(signals, prices, listings=listings, cutoff=D1)
    daily = result.daily[0]
    assert daily.rank_ic == pytest.approx(1.0)
    assert (daily.universe_count, daily.paired_count, daily.coverage_fraction) == (10, 3, 0.3)
    assert {item.reason: item.count for item in daily.exclusions} == {
        "missing_entry": 1,
        "missing_exit": 1,
        "listing_ended_unsettled": 1,
        "listing_status_unknown": 1,
        "price_not_available": 1,
        "signal_not_available": 1,
        "signal_missing": 1,
    }
    assert {item.reason: item.count for item in daily.signal_status_counts} == {
        "ok": 8,
        "signal_missing": 1,
        "signal_not_available": 1,
    }
    assert {item.reason: item.count for item in daily.label_status_counts}["ok"] == 5
    assert result.samples[8].signal_status is SignalStatus.NOT_AVAILABLE
    assert sum(item.count for item in daily.exclusions) + daily.paired_count == daily.universe_count


def test_future_prices_are_ignored_until_the_label_end_has_matured() -> None:
    result = _evaluate(
        (_signal("a", 1.0),),
        (_price("a", D0, 100), _price("a", D2, 150)),
        spec=PriceLabelSpec((2, 5)),
        cutoff=D1,
    )
    assert result.samples[0].label.status is PriceLabelStatus.NOT_MATURED
    assert result.samples[0].label.return_fraction is None
    assert result.samples[1].label.status is PriceLabelStatus.CALENDAR_INSUFFICIENT
    assert result.samples[1].label.label_end is None
    assert result.daily[0].rank_ic is None
    assert result.daily[0].coverage_fraction == 0.0


@pytest.mark.parametrize(
    "signals,ends,status",
    [
        ((1.0, 1.0, 1.0), (100, 110, 120), RankICStatus.CONSTANT_SIGNAL),
        ((1.0, 2.0, 3.0), (110, 110, 110), RankICStatus.CONSTANT_LABEL),
        ((1.0, 2.0), (110, 120), RankICStatus.INSUFFICIENT_PAIRS),
    ],
)
def test_undefined_rank_ic_is_nullable_without_fabricated_zero(
    signals: tuple[float, ...], ends: tuple[int, ...], status: RankICStatus
) -> None:
    observations = tuple(_signal(str(index), value) for index, value in enumerate(signals))
    prices = tuple(
        price
        for index, end in enumerate(ends)
        for price in (_price(str(index), D0, 100), _price(str(index), D1, end))
    )
    daily = _evaluate(observations, prices).daily[0]
    assert daily.status is status
    assert daily.rank_ic is None
    assert daily.coverage_fraction == 1.0


def test_nonmembers_are_excluded_before_pairing_without_using_future_survival() -> None:
    result = _evaluate(
        (_signal("a", 1.0), replace(_signal("b", 2.0), universe_member=False)),
        (_price("a", D0, 100), _price("a", D1, 120)),
    )
    assert [row.security_id for row in result.samples] == ["a"]
    assert result.daily[0].universe_count == 1


@pytest.mark.parametrize(
    "spec",
    [
        lambda: PriceLabelSpec(()),
        lambda: PriceLabelSpec((1, 1)),
        lambda: PriceLabelSpec((0,)),
        lambda: PriceLabelSpec((True,)),
        lambda: PriceLabelSpec((1,), -1),
        lambda: PriceLabelSpec((1,), minimum_pairs=1),
    ],
)
def test_invalid_label_spec_is_rejected(spec: Callable[[], PriceLabelSpec]) -> None:
    with pytest.raises(ValueError):
        spec()


def test_sealed_research_window_cannot_be_measured_through_the_new_entrypoint() -> None:
    with pytest.raises(ResearchWindowViolationError):
        _evaluate((_signal("a", 1.0, session=date(2019, 12, 30)),), ())


def test_structural_snapshot_calendar_and_numeric_errors_are_rejected() -> None:
    with pytest.raises(ValueError, match="matching"):
        _evaluate((_signal("a", 1.0),), (), price_snapshot="different")
    with pytest.raises(ValueError, match="calendar"):
        _evaluate((_signal("a", 1.0),), (), sessions=tuple(reversed(SESSIONS)))
    with pytest.raises(ValueError, match="unique"):
        _evaluate((_signal("a", 1.0), _signal("a", 2.0)), ())
    with pytest.raises(ValueError, match="finite"):
        _evaluate((_signal("a", float("nan")),), ())
    with pytest.raises(ValueError, match="finite positive"):
        _price("a", D0, 0.0)


def test_rank_ic_is_reported_per_signal_session_and_explicit_horizon() -> None:
    observations = tuple(
        _signal(security_id, float(index), session=session, available=session)
        for session in (D0, D1)
        for index, security_id in enumerate("abc")
    )
    prices = tuple(
        _price(security_id, session, close)
        for session, closes in ((D0, (100, 100, 100)), (D1, (110, 120, 130)), (D2, (143, 144, 143)))
        for security_id, close in zip("abc", closes, strict=True)
    )
    result = _evaluate(observations, prices, spec=PriceLabelSpec((1, 2)), cutoff=D2)
    daily = {(row.signal_session, row.horizon_sessions): row for row in result.daily}
    assert daily[D0, 1].rank_ic == pytest.approx(1)
    assert daily[D1, 1].rank_ic == pytest.approx(-1)
    assert daily[D0, 2].rank_ic == pytest.approx(0)
    assert daily[D1, 2].rank_ic is None
    assert {
        row.label.status
        for row in result.samples
        if row.signal_session == D1 and row.horizon_sessions == 2
    } == {PriceLabelStatus.NOT_MATURED}
    assert result.label_spec.horizons_sessions == (1, 2)


def test_price_and_signal_duplicate_keys_and_late_signal_sessions_are_rejected() -> None:
    with pytest.raises(ValueError, match="unique"):
        _evaluate((_signal("a", 1),), (_price("a", D0, 100), _price("a", D0, 110)))
    duplicate = replace(
        _signal("a", 1),
        factor_values=(
            PortfolioFactorValue("factor", 1, D0),
            PortfolioFactorValue("factor", 2, D0),
        ),
    )
    with pytest.raises(ValueError, match="duplicate factor"):
        _evaluate((duplicate,), ())
    with pytest.raises(ValueError, match="after evaluation_as_of"):
        _evaluate((_signal("a", 1, session=D2),), (), cutoff=D1)
    with pytest.raises(ValueError, match="before its session"):
        AdjustedCloseObservation(D1, "a", 100, D0)


def test_default_horizons_remain_an_explicit_report_specification() -> None:
    result = _evaluate((_signal("a", 1),), (), spec=PriceLabelSpec())
    assert result.label_spec.horizons_sessions == (5, 21, 63)
    assert [sample.horizon_sessions for sample in result.samples] == [5, 21, 63]
    assert {sample.label.status for sample in result.samples} == {
        PriceLabelStatus.CALENDAR_INSUFFICIENT
    }
