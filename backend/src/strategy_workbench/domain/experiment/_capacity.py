"""용량 스윕 — 금액별 체결 비용과 한계 금액(검증 랩 V4-04, spec D8, 수학 노트 8절).

같은 전략·실행 설정을 초기 자본만 바꿔 돌린 실행들에서, 금액이 커질 때 비용 후 샤프가 어디서
꺾이는지 본다. 금액이 작으면 1주 단위 반올림으로, 크면 가격 충격과 참여 한도로 성과가 깎인다. 한계
금액은 최고 샤프의 `CAPACITY_SHARPE_RATIO` 밑으로 처음 떨어지기 직전의 시험 금액이다.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from strategy_workbench.domain.backtest.facade.runs import RawArtifactBundle

from ._errors import InvalidExperimentSpecError

# 한계 금액 기준: 최고 샤프의 이 비율 밑으로 떨어지면 그 금액은 굴릴 수 없다(WORKFLOW V4-04 "최고
# 샤프의 절반"). 고원의 봉우리 기준 `PEAK_NEIGHBOR_RATIO` 와 값만 같고 뜻이 다르다(그쪽은 이웃
# 평균 ÷ 제 점수) — 한쪽을 바꿔도 다른 쪽이 따라 바뀌지 않게 따로 둔다.
CAPACITY_SHARPE_RATIO = 0.5
# 용량 스윕 금액 수. 곡선이 꺾이는 곳을 보려면 둘은 넘어야 하고, 금액마다 전체 구간을 한 번 돈다.
MIN_CAPACITY_AMOUNTS = 3
MAX_CAPACITY_AMOUNTS = 12
_BPS = 10_000


@dataclass(frozen=True)
class ExecutionCosts:
    """한 실행의 체결 비용 요약. 체결·주문이 없으면 해당 칸이 None 이다."""

    # 가격 충격(슬리피지 포함) 합 ÷ 체결 금액 합, bp. 체결가에서 규칙 가격까지 거리다.
    impact_cost_bps: float | None
    # 리밸런스 주문 수량 대비 그 세션 미체결 비율(주 단위) — 참여 한도·매수 여력에 막힌 몫이다.
    # 워크벤치 주문은 모두 DAY 라 잔량은 그 세션에 사라지고 다음 리밸런스가 새 주문으로 다시 낸다.
    # 그래서 같은 부족분이 세션마다 다시 세어진다 — "끝내 못 채운 비율"이 아니다(목표 대비 달성은
    # V4-04 2/2 의 달성 비중 차이가 잰다).
    session_unfilled_ratio: float | None
    # 체결 수량 합 ÷ 체결한 (세션, 종목) 의 유동성 캡 기준 거래량 합 — 거래량 가중 평균 참여율.
    # 기준 거래량이 없는 옛 결과(`backtest-artifacts-v1`)거나 체결이 없으면 None.
    participation_rate: float | None
    # 반올림 오차 = Σ|목표 금액 Δ − 1주 단위로 내린 금액| ÷ Σ|목표 금액 Δ| — 라우터가 리밸런스마다
    # 남긴 기록으로 잰다. 1주 미만이라 주문이 없는 목표도 든다(소액의 반올림 손해). 기록이 없는 옛
    # 결과(`backtest-artifacts-v1`)거나 목표 Δ 가 모두 0 이면 None.
    rounding_error: float | None


class CapacityGap(StrEnum):
    """한계 금액을 확정하지 못했거나 시험 범위 끝에 걸린 이유. 화면은 번역만 한다."""

    # 도는·대기·복구 대기 금액이 있다.
    PENDING = "pending"
    # 취소된 금액이 있다 — 돌지 않은 금액으로 곡선이 꺾였다고 하지 않는다(워크포워드 `cancelled`
    # 와 같은 방향).
    CANCELLED = "cancelled"
    # 샤프가 난 금액이 없거나 최고 샤프가 0 이하다.
    NO_POSITIVE_SHARPE = "no_positive_sharpe"
    # 가장 큰 시험 금액까지 기준 위다 — 한계는 가장 큰 시험 금액이고 실제로는 그보다 클 수 있다.
    BEYOND_TESTED = "beyond_tested"


@dataclass(frozen=True)
class CapacityLimit:
    # 굴릴 수 있는 가장 큰 시험 금액. 확정하지 못했으면 None(`gap`).
    amount: float | None
    # 최고 샤프가 난 금액과 기준선(최고 샤프 × `CAPACITY_SHARPE_RATIO`, 세션 샤프). 화면이 기준을
    # 다시 계산하지 않게 싣는다. 확정하지 못했으면 None.
    best_amount: float | None
    threshold_sharpe: float | None
    gap: CapacityGap | None


def capacity_amounts(values: Iterable[float]) -> tuple[float, ...]:
    """용량 스윕 금액(초기 자본)을 검증해 오름차순으로 낸다.

    Raises:
        InvalidExperimentSpecError: 금액이 양의 유한수가 아니거나 겹치거나 수가 범위 밖이다.
    """
    amounts = tuple(values)
    invalid = [
        amount
        for amount in amounts
        if isinstance(amount, bool)
        or not isinstance(amount, int | float)
        or not math.isfinite(amount)
        or amount <= 0
    ]
    if (
        invalid
        or len(set(amounts)) != len(amounts)
        or not MIN_CAPACITY_AMOUNTS <= len(amounts) <= MAX_CAPACITY_AMOUNTS
    ):
        raise InvalidExperimentSpecError(
            "experiment.capacity.invalid_amounts",
            f"용량 스윕 금액은 서로 다른 양수 {MIN_CAPACITY_AMOUNTS}~{MAX_CAPACITY_AMOUNTS}개여야 "
            f"합니다: count={len(amounts)} invalid={invalid!r} "
            f"duplicates={len(amounts) - len(set(amounts))}",
        )
    return tuple(sorted(float(amount) for amount in amounts))


def execution_costs(artifacts: RawArtifactBundle) -> ExecutionCosts:
    """체결·주문·반올림 artifact 에서 가격 충격 비용·세션 미체결 비율·참여율·반올림 오차를 낸다
    (주문은 모두 DAY 주문).

    기준 거래량은 (세션, 종목) 마다 한 번 센다 — 같은 날 한 종목의 여러 체결은 한 거래량을 나눠
    쓴다.
    """
    traded = sum(fill.price * float(fill.quantity) for fill in artifacts.fills)
    impact = sum(fill.slippage_per_share * float(fill.quantity) for fill in artifacts.fills)
    ordered = sum(Decimal(order.quantity) for order in artifacts.orders)
    filled = sum(Decimal(fill.quantity) for fill in artifacts.fills)
    volumes = {(fill.session, fill.security_id): fill.cap_volume for fill in artifacts.fills}
    known = None not in volumes.values()
    volume = sum(value for value in volumes.values() if value is not None)
    wanted = sum(abs(row.target_notional) for row in artifacts.roundings)
    missed = sum(abs(row.target_notional - row.rounded_notional) for row in artifacts.roundings)
    return ExecutionCosts(
        impact_cost_bps=impact / traded * _BPS if traded else None,
        session_unfilled_ratio=float(1 - filled / ordered) if ordered else None,
        participation_rate=float(filled / volume) if known and volume else None,
        rounding_error=missed / wanted if wanted else None,
    )


def capacity_limit(
    points: Iterable[tuple[float, float | None]], *, pending: bool = False, cancelled: bool = False
) -> CapacityLimit:
    """`(초기 자본, 세션 샤프)` 에서 한계 금액을 낸다. 샤프가 없는 금액(실패·파산)은 기준 밑이다.

    최고 샤프가 난 금액(동점이면 작은 금액)부터 금액 순으로 올라가며, 샤프가 최고의
    `CAPACITY_SHARPE_RATIO` 밑으로 처음 떨어진 금액의 바로 앞 금액이 한계다. 보간하지 않는다. 뒤에서
    다시 오르는 금액은 보지 않는다 — 비단조 곡선에서 용량을 부풀리지 않는 보수 쪽이다. 도는 금액이나
    취소된 금액이 있으면 확정하지 않는다.
    """
    if pending or cancelled:
        return CapacityLimit(
            None, None, None, CapacityGap.PENDING if pending else CapacityGap.CANCELLED
        )
    ordered: Sequence[tuple[float, float | None]] = sorted(points)
    scored = [(amount, sharpe) for amount, sharpe in ordered if sharpe is not None]
    best_amount, best = max(scored, key=lambda point: point[1], default=(None, 0.0))
    if best_amount is None or best <= 0:
        return CapacityLimit(None, None, None, CapacityGap.NO_POSITIVE_SHARPE)
    threshold = CAPACITY_SHARPE_RATIO * best
    limit = best_amount
    for amount, sharpe in ordered:
        if amount <= best_amount:
            continue
        if sharpe is None or sharpe < threshold:
            return CapacityLimit(limit, best_amount, threshold, None)
        limit = amount
    return CapacityLimit(limit, best_amount, threshold, CapacityGap.BEYOND_TESTED)
