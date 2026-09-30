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

from strategy_workbench.domain.backtest.facade.runs import RawArtifactBundle

from ._errors import InvalidExperimentSpecError

# 한계 금액 기준: 최고 샤프의 이 비율 밑으로 떨어지면 그 금액은 굴릴 수 없다(WORKFLOW V4-04).
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
    # 주문 수량 가운데 체결되지 않은 비율(주 단위). 참여 한도·매수 여력에 막힌 몫이다.
    unfilled_ratio: float | None


@dataclass(frozen=True)
class CapacityLimit:
    # 굴릴 수 있는 가장 큰 시험 금액. 최고 샤프가 0 이하이거나 샤프가 난 금액이 없으면 None.
    amount: float | None
    # 가장 큰 시험 금액까지 기준 밑으로 떨어지지 않았다 — 한계는 시험 범위 밖이다.
    beyond_tested: bool


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
    """체결·주문 artifact 에서 가격 충격 비용과 미체결 비율을 낸다.

    주문 수량은 주문마다 한 번 센다 — 참여 한도 잔량은 같은 주문으로 다음 세션에 이어 체결된다.
    """
    traded = sum(fill.price * float(fill.quantity) for fill in artifacts.fills)
    impact = sum(fill.slippage_per_share * float(fill.quantity) for fill in artifacts.fills)
    ordered = sum(Decimal(order.quantity) for order in artifacts.orders)
    filled = sum(Decimal(fill.quantity) for fill in artifacts.fills)
    return ExecutionCosts(
        impact_cost_bps=impact / traded * _BPS if traded else None,
        unfilled_ratio=float(1 - filled / ordered) if ordered else None,
    )


def capacity_limit(points: Iterable[tuple[float, float | None]]) -> CapacityLimit:
    """`(초기 자본, 세션 샤프)` 에서 한계 금액을 낸다. 샤프가 없는 금액(실패·파산)은 기준 밑이다.

    최고 샤프가 난 금액부터 금액 순으로 올라가며, 샤프가 최고의 `CAPACITY_SHARPE_RATIO` 밑으로
    처음 떨어진 금액의 바로 앞 금액이 한계다 — 뒤에서 다시 오르는 금액은 운으로 보고 보지 않는다.
    """
    ordered: Sequence[tuple[float, float | None]] = sorted(points)
    scored = [(amount, sharpe) for amount, sharpe in ordered if sharpe is not None]
    if not scored:
        return CapacityLimit(None, beyond_tested=False)
    best_amount, best = max(scored, key=lambda point: point[1])
    if best <= 0:
        return CapacityLimit(None, beyond_tested=False)
    limit = best_amount
    for amount, sharpe in ordered:
        if amount <= best_amount:
            continue
        if sharpe is None or sharpe < CAPACITY_SHARPE_RATIO * best:
            return CapacityLimit(limit, beyond_tested=False)
        limit = amount
    return CapacityLimit(limit, beyond_tested=True)
