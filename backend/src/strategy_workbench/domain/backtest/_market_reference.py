"""완료 run 옆에 같은 기간 시장 지수(코스피·코스닥)를 참고선으로 놓는다.

지수는 전략이 사고팔 수 있는 종목이 아니라 비교 기준이다. 실행 결과·지문·벤치마크
(`benchmark_security_id`)와 무관하게 결과를 읽을 때 투영하며 저장하지 않는다.

- 곡선: run 의 세션 달력(계좌 기록)마다 초기자본 × 그날 종가 ÷ 첫 세션 종가. 전략 곡선과 벤치마크
  곡선(`_benchmark_series`)이 첫 세션 자산 = 초기자본에서 시작하는 것과 같은 기준이다.
- 지표: 같은 곡선을 Metric Registry 의 `compute_analytics` 로 잰다. 총수익률·최대 낙폭·칼마가 전략과
  같은 공식·같은 사용 불가 사유(1년 미만이면 칼마 없음)를 쓴다.
- 한 세션이라도 종가가 없으면 그 지수는 곡선을 내지 않는다. 빈 날을 앞 값으로 메우지 않는다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from strategy_workbench.domain.analytics.facade.metrics import (
    AnalysisPoint,
    AnalyticsInput,
    DrawdownPoint,
    MetricRegistry,
    MetricValue,
    compute_analytics,
)

from ._models import BacktestRunResult


@dataclass(frozen=True)
class MarketIndex:
    """참고선 지수. `index_name` 은 원장 `index_daily.index_name` 값 그대로다."""

    index_id: str
    index_name: str


# 비교 기준으로 쓰는 지수와 순서. 벤치마크 성격의 인덱스펀드가 따르는 두 시장 대표 지수다.
MARKET_REFERENCE_INDICES: tuple[MarketIndex, ...] = (
    MarketIndex("kospi", "코스피"),
    MarketIndex("kosdaq", "코스닥"),
)


class MarketReferenceStatus(StrEnum):
    AVAILABLE = "available"
    NOT_AVAILABLE = "not_available"


@dataclass(frozen=True)
class MarketReferencePoint:
    session: date
    value: float


@dataclass(frozen=True)
class MarketReferenceSeries:
    index_id: str
    index_name: str
    status: MarketReferenceStatus
    points: tuple[MarketReferencePoint, ...]
    # 같은 레지스트리 계산의 낙폭 곡선(고점 대비). 곡선이 없으면 빈 튜플.
    drawdown: tuple[DrawdownPoint, ...]
    # 전략과 같은 Metric Registry 의 FULL 범위 지표(총수익률·최대 낙폭·칼마 등). 없으면 빈 튜플.
    metrics: tuple[MetricValue, ...]
    detail: str | None

    def __post_init__(self) -> None:
        available = self.status is MarketReferenceStatus.AVAILABLE
        if available != bool(self.points) or available == bool(self.detail):
            raise ValueError(
                "시장 참고선의 상태·곡선·사유 계약을 위반했습니다: "
                f"index={self.index_id} status={self.status!r} points={len(self.points)} "
                f"detail={self.detail!r}"
            )


@dataclass(frozen=True)
class MarketReferenceReport:
    run_id: str
    initial_cash: float
    series: tuple[MarketReferenceSeries, ...]


def project_market_reference(
    result: BacktestRunResult,
    closes: Mapping[str, Mapping[date, float]],
    registry: MetricRegistry,
    *,
    unavailable_reason: str | None = None,
) -> MarketReferenceReport:
    """run 의 세션마다 지수 곡선과 지표를 만든다. `closes` 는 지수 이름 → 세션 → 종가다."""
    manifest = result.manifest
    sessions = tuple(item.session for item in result.artifacts.snapshots)
    initial_cash = manifest.run_spec.initial_cash
    series = tuple(
        _series(
            index,
            sessions,
            closes.get(index.index_name, {}),
            initial_cash,
            registry,
            manifest.run_spec.annualization_days,
            unavailable_reason,
        )
        for index in MARKET_REFERENCE_INDICES
    )
    return MarketReferenceReport(run_id=manifest.run_id, initial_cash=initial_cash, series=series)


def _series(
    index: MarketIndex,
    sessions: tuple[date, ...],
    closes: Mapping[date, float],
    initial_cash: float,
    registry: MetricRegistry,
    annualization_days: int,
    unavailable_reason: str | None,
) -> MarketReferenceSeries:
    def missing(detail: str) -> MarketReferenceSeries:
        return MarketReferenceSeries(
            index.index_id,
            index.index_name,
            MarketReferenceStatus.NOT_AVAILABLE,
            (),
            (),
            (),
            detail,
        )

    if unavailable_reason is not None:
        return missing(unavailable_reason)
    if not sessions:
        return missing(
            f"이 실행에 계좌 기록이 없어 지수를 맞출 세션이 없습니다. index={index.index_name}"
        )
    absent = [session for session in sessions if not (closes.get(session, 0.0) > 0)]
    if absent:
        return missing(
            "실행 기간 중 지수 종가가 없는 날이 있어 곡선을 그리지 않습니다"
            "(빈 날을 메우지 않는다). "
            f"index={index.index_name} missing={len(absent)} first={absent[0]}"
        )
    base = closes[sessions[0]]
    points = tuple(
        MarketReferencePoint(session, initial_cash * closes[session] / base) for session in sessions
    )
    report = compute_analytics(
        AnalyticsInput(
            points=tuple(
                AnalysisPoint(point.session, point.value, gross_exposure=1.0, net_exposure=1.0)
                for point in points
            ),
            traded_notional=0.0,
        ),
        registry,
        annualization_days=annualization_days,
    )
    return MarketReferenceSeries(
        index.index_id,
        index.index_name,
        MarketReferenceStatus.AVAILABLE,
        points,
        report.drawdown_curve,
        tuple(report.metrics),
        None,
    )
