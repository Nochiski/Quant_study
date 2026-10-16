from __future__ import annotations

from bisect import bisect_left
from collections import Counter
from collections.abc import Sequence
from datetime import date
from math import fsum, isfinite, sqrt

from strategy_workbench.domain.backtest.facade.environment import require_research_window
from strategy_workbench.domain.factor.facade.cross_section import cross_sectional_rank
from strategy_workbench.domain.portfolio.facade.construction import PortfolioObservation

from ._models import (
    AdjustedCloseObservation,
    AdjustmentGapObservation,
    DailyRankIC,
    ExclusionCount,
    ListingInterval,
    PriceLabel,
    PriceLabelSpec,
    PriceLabelStatus,
    RankICStatus,
    SignalEvaluationResult,
    SignalEvaluationSample,
    SignalStatus,
)


def evaluate_signal(
    *,
    observations: Sequence[PortfolioObservation],
    factor_id: str,
    signal_snapshot_id: str,
    price_snapshot_id: str,
    sessions: Sequence[date],
    prices: Sequence[AdjustedCloseObservation],
    listings: Sequence[ListingInterval],
    label_spec: PriceLabelSpec,
    evaluation_as_of: date,
    adjustment_gaps: Sequence[AdjustmentGapObservation] = (),
) -> SignalEvaluationResult:
    """기존 pipeline의 선정 전 팩터 값과 별도 미래 가격을 날짜별로 비교한다.

    신호 방향을 사후 반전하거나 상관이 좋은 horizon을 선택하지 않는다. 각 날짜의 당시
    universe_member가 분모다. 상장 종료·결측을 사후 탈락시킨 완전관측 표본을 만들지 않는다.
    """
    require_research_window(evaluation_as_of, requested_by="signal_evaluation")
    for observation in observations:
        require_research_window(observation.as_of, requested_by="signal_evaluation")
    if not factor_id or not signal_snapshot_id or signal_snapshot_id != price_snapshot_id:
        raise ValueError("factor id and matching nonempty signal/price snapshots are required")
    calendar = tuple(sessions)
    if not calendar or calendar != tuple(sorted(set(calendar))):
        raise ValueError("sessions must be a nonempty ascending unique common trading calendar")
    index_by_session = {session: index for index, session in enumerate(calendar)}
    price_by_key: dict[tuple[date, str], AdjustedCloseObservation] = {}
    for price in prices:
        key = (price.session, price.security_id)
        if price.session not in index_by_session or key in price_by_key:
            raise ValueError(f"price must have a unique common-calendar key — key={key!r}")
        price_by_key[key] = price
    gap_keys: set[tuple[date, str]] = set()
    gap_sessions_by_id: dict[str, list[date]] = {}
    for gap in adjustment_gaps:
        key = (gap.session, gap.security_id)
        if gap.session not in index_by_session or key in gap_keys:
            raise ValueError(f"adjustment gap must have a unique common-calendar key — key={key!r}")
        gap_keys.add(key)
        gap_sessions_by_id.setdefault(gap.security_id, []).append(gap.session)
    for gap_sessions in gap_sessions_by_id.values():
        gap_sessions.sort()
    listing_by_id: dict[str, ListingInterval] = {}
    for listing in listings:
        if listing.security_id in listing_by_id:
            raise ValueError(f"duplicate listing interval — security_id={listing.security_id!r}")
        listing_by_id[listing.security_id] = listing
    seen: set[tuple[date, str]] = set()
    samples: list[SignalEvaluationSample] = []
    for observation in sorted(observations, key=lambda item: (item.as_of, item.security_id)):
        key = (observation.as_of, observation.security_id)
        if key in seen or observation.as_of not in index_by_session:
            raise ValueError(f"signal must have a unique common-calendar key — key={key!r}")
        seen.add(key)
        if observation.as_of > evaluation_as_of:
            raise ValueError(f"signal session is after evaluation_as_of — key={key!r}")
        if not observation.universe_member:
            continue
        factors = [value for value in observation.factor_values if value.factor_id == factor_id]
        if len(factors) > 1:
            raise ValueError(f"duplicate factor signal — key={key!r} factor_id={factor_id!r}")
        signal = factors[0] if factors else None
        value = signal.value if signal is not None else None
        if value is not None and not isfinite(value):
            raise ValueError(f"factor signal must be finite or missing — key={key!r}")
        status = (
            SignalStatus.NOT_AVAILABLE
            if signal is not None and signal.available_date > observation.as_of
            else SignalStatus.MISSING
            if value is None
            else SignalStatus.OK
        )
        for horizon in label_spec.horizons_sessions:
            label = _price_label(
                calendar,
                index_by_session[observation.as_of] + label_spec.entry_delay_sessions,
                horizon,
                observation.security_id,
                price_by_key,
                listing_by_id.get(observation.security_id),
                evaluation_as_of,
                gap_sessions_by_id.get(observation.security_id, ()),
            )
            samples.append(
                SignalEvaluationSample(
                    observation.as_of, observation.security_id, horizon, value, status, label
                )
            )
    grouped: dict[tuple[date, int], list[SignalEvaluationSample]] = {}
    for sample in samples:
        grouped.setdefault((sample.signal_session, sample.horizon_sessions), []).append(sample)
    return SignalEvaluationResult(
        signal_snapshot_id,
        factor_id,
        evaluation_as_of,
        label_spec,
        tuple(samples),
        tuple(
            _daily(session, horizon, group, label_spec.minimum_pairs)
            for (session, horizon), group in sorted(grouped.items())
        ),
    )


def _price_label(
    calendar: tuple[date, ...],
    entry_index: int,
    horizon: int,
    security_id: str,
    prices: dict[tuple[date, str], AdjustedCloseObservation],
    listing: ListingInterval | None,
    cutoff: date,
    adjustment_gap_sessions: Sequence[date],
) -> PriceLabel:
    end_index = entry_index + horizon
    entry = calendar[entry_index] if entry_index < len(calendar) else None
    end = calendar[end_index] if end_index < len(calendar) else None
    if end is None or entry is None:
        return PriceLabel(entry, end, PriceLabelStatus.CALENDAR_INSUFFICIENT)
    if end > cutoff:
        return PriceLabel(entry, end, PriceLabelStatus.NOT_MATURED)
    if listing is None:
        return PriceLabel(entry, end, PriceLabelStatus.LISTING_STATUS_UNKNOWN)
    if end > listing.last_session:
        return PriceLabel(entry, end, PriceLabelStatus.LISTING_ENDED_UNSETTLED)
    if entry < listing.first_session:
        return PriceLabel(entry, end, PriceLabelStatus.OUTSIDE_LISTING)
    gap_index = bisect_left(adjustment_gap_sessions, entry)
    if gap_index < len(adjustment_gap_sessions) and adjustment_gap_sessions[gap_index] <= end:
        return PriceLabel(entry, end, PriceLabelStatus.ADJUSTMENT_GAP)
    entry_price, exit_price = prices.get((entry, security_id)), prices.get((end, security_id))
    if entry_price is None:
        return PriceLabel(entry, end, PriceLabelStatus.MISSING_ENTRY)
    if exit_price is None:
        return PriceLabel(entry, end, PriceLabelStatus.MISSING_EXIT)
    if entry_price.available_date > cutoff or exit_price.available_date > cutoff:
        return PriceLabel(entry, end, PriceLabelStatus.PRICE_NOT_AVAILABLE)
    return_fraction = exit_price.close / entry_price.close - 1.0
    if not isfinite(return_fraction):
        raise ValueError(
            f"nonfinite price label — security_id={security_id!r} entry={entry} end={end}"
        )
    return PriceLabel(entry, end, PriceLabelStatus.OK, return_fraction)


def _daily(
    session: date, horizon: int, samples: list[SignalEvaluationSample], minimum_pairs: int
) -> DailyRankIC:
    pairs: list[tuple[float, float]] = []
    exclusions: Counter[str] = Counter()
    for sample in samples:
        if sample.paired:
            signal, label = sample.signal_value, sample.label.return_fraction
            if signal is None or label is None:
                raise ValueError("paired signal and label must both carry values")
            pairs.append((signal, label))
        else:
            reason = (
                sample.signal_status.value
                if sample.signal_status is not SignalStatus.OK
                else sample.label.status.value
            )
            exclusions[reason] += 1
    count = len(pairs)
    value: float | None = None
    if count < minimum_pairs:
        status = RankICStatus.INSUFFICIENT_PAIRS
    else:
        signal_ranks = cross_sectional_rank([pair[0] for pair in pairs])
        label_ranks = cross_sectional_rank([pair[1] for pair in pairs])
        center_signal, center_label = fsum(signal_ranks) / count, fsum(label_ranks) / count
        signal_deviations = [rank - center_signal for rank in signal_ranks]
        label_deviations = [rank - center_label for rank in label_ranks]
        signal_squares = fsum(value * value for value in signal_deviations)
        label_squares = fsum(value * value for value in label_deviations)
        if signal_squares == 0:
            status = RankICStatus.CONSTANT_SIGNAL
        elif label_squares == 0:
            status = RankICStatus.CONSTANT_LABEL
        else:
            status = RankICStatus.OK
            value = fsum(
                left * right
                for left, right in zip(signal_deviations, label_deviations, strict=True)
            ) / sqrt(signal_squares * label_squares)
    return DailyRankIC(
        session,
        horizon,
        status,
        value,
        len(samples),
        count,
        count / len(samples),
        tuple(ExclusionCount(reason, count) for reason, count in sorted(exclusions.items())),
        tuple(
            ExclusionCount(reason, count)
            for reason, count in sorted(
                Counter(sample.signal_status.value for sample in samples).items()
            )
        ),
        tuple(
            ExclusionCount(reason, count)
            for reason, count in sorted(
                Counter(sample.label.status.value for sample in samples).items()
            )
        ),
    )
