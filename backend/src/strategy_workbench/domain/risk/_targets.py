"""주문안 목표 주식 수에 리스크 한도를 씌운다(S07 리스크 가드 ①, 주문안을 만들 때).

목표를 줄이기만 한다. 새 종목을 넣거나 늘리지 않고, 종목마다 따로 판정한다(그래서 승인의 이번 주
매매 제외로 목표에서 종목을 빼도 남은 종목의 판정은 그대로다). 주문안이 매매가 막혀 그대로 둔
종목(`frozen`: 거래정지·신호일 가격 없음)은 건드리지 않는다 — 낼 수 없는 매도를 만들지 않는다. 순서:

1. 낙폭 축소·정지 단계면 모든 목표에 노출 비율을 곱해 내림한다(보유도 줄인다).
2. 정지 단계면 지금 보유보다 많이 사지 않는다.
3. 남은 매수는 신호일 표시(관리·거래정지)와 자료가 있어야 하고, 단일 종목 비중 상한과 20일 평균
   거래대금 참여 상한 안으로 줄인다. 이미 상한을 넘은 보유는 팔지 않는다.
4. 파는 쪽은 줄이지 않는다(파는 것이 위험을 줄인다).
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from enum import StrEnum

from strategy_workbench.domain.risk._drawdown import RiskLevel, exposure_bp
from strategy_workbench.domain.risk._limits import BP, RiskLimits


class GuardReason(StrEnum):
    EXPOSURE = "exposure"  # 낙폭 축소로 목표에 노출 비율을 곱했다
    HALT = "halt"  # 낙폭 정지로 사지 않는다
    BLOCKED = "blocked"  # 신호일 관리종목·거래정지 표시
    NO_FACTS = "no_facts"  # 거래대금·표시 자료가 없어 판정할 수 없다(사지 않는다)
    WEIGHT = "weight"  # 단일 종목 비중 상한
    ADV = "adv"  # 20일 평균 거래대금 참여 상한


@dataclass(frozen=True)
class SignalFacts:
    """신호일 종목 자료. `adv20_krw`는 신호일 포함 20거래일 평균 거래대금(원)이다."""

    adv20_krw: int | None
    admin: bool
    halted: bool


@dataclass(frozen=True)
class GuardedLine:
    symbol: str
    current_shares: int
    plan_target: int
    target: int
    reasons: tuple[GuardReason, ...]


@dataclass(frozen=True)
class GuardedTargets:
    level: RiskLevel
    exposure_bp: int
    lines: tuple[GuardedLine, ...]

    def targets(self) -> dict[str, int]:
        return {line.symbol: line.target for line in self.lines}

    def changed(self) -> tuple[GuardedLine, ...]:
        return tuple(line for line in self.lines if line.target != line.plan_target)


def guard_targets(
    *,
    targets: Mapping[str, int],
    holdings: Mapping[str, int],
    frozen: Collection[str],
    prices: Mapping[str, int],
    facts: Mapping[str, SignalFacts] | None,
    equity_krw: int,
    level: RiskLevel,
    limits: RiskLimits,
) -> GuardedTargets:
    exposure = exposure_bp(level, limits.drawdown)
    lines: list[GuardedLine] = []
    for symbol, plan_target in sorted(targets.items()):
        if plan_target < 0:
            raise ValueError(f"target must not be negative — symbol={symbol} target={plan_target}")
        current = holdings.get(symbol, 0)
        if symbol in frozen:
            lines.append(GuardedLine(symbol, current, plan_target, plan_target, ()))
            continue
        reasons: list[GuardReason] = []
        target = plan_target * exposure // BP
        if target != plan_target:
            reasons.append(GuardReason.EXPOSURE)
        if level is RiskLevel.HALT and target > current:
            target = current
            reasons.append(GuardReason.HALT)
        if target > current:
            target, caps = _capped_buy(symbol, current, target, prices, facts, equity_krw, limits)
            reasons += caps
        lines.append(GuardedLine(symbol, current, plan_target, target, tuple(reasons)))
    return GuardedTargets(level, exposure, tuple(lines))


def _capped_buy(
    symbol: str,
    current: int,
    target: int,
    prices: Mapping[str, int],
    facts: Mapping[str, SignalFacts] | None,
    equity_krw: int,
    limits: RiskLimits,
) -> tuple[int, list[GuardReason]]:
    fact = None if facts is None else facts.get(symbol)
    price = prices.get(symbol)
    if fact is None or fact.adv20_krw is None or price is None or price <= 0:
        return current, [GuardReason.NO_FACTS]
    if fact.admin or fact.halted:
        return current, [GuardReason.BLOCKED]
    reasons: list[GuardReason] = []
    weight_cap = equity_krw * limits.max_weight_bp // BP // price
    if target > weight_cap:
        target = max(current, weight_cap)
        reasons.append(GuardReason.WEIGHT)
    adv_cap = current + fact.adv20_krw * limits.max_adv_participation_bp // BP // price
    if target > adv_cap:
        target = adv_cap
        reasons.append(GuardReason.ADV)
    return target, reasons
