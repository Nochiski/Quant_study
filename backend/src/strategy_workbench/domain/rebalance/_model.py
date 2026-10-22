"""주간 리밸런싱 목표 주식 수(S07). 신호 순위·보유·현금·기준가격만으로 정하는 순수 계산이다.

규칙(2026-10-21 승인 플랜):

- 보유 종목은 순위가 `keep_rank` 안이면 남기고, 밖이거나 순위가 없으면 전량 판다. 매매가 막힌
  보유 종목은 팔지 않고 둔다.
- 남은 자리는 보유하지 않은 종목을 순위대로 채운다. 매매가 막혔거나 기준가격이 없는 종목은
  건너뛰고 다음 순위로 채우되 `keep_rank` 밖 종목은 사지 않는다(다음 주에 바로 팔 종목이다).
- 종목당 목표 금액 = 총자산 × (1 − 현금 비율) ÷ 자리 수. 새로 사는 종목은 이 금액으로 맞추고,
  남긴 종목은 목표 금액에서 밴드를 넘게 벗어날 때만 맞춘다.
- 주식 수 = 목표 금액 ÷ (기준가격 × (1 + 수수료율)) 내림.
- 보유 종목이 신호에서 빠졌으면(순위 없음) 파는 줄로 두되 확인 필요(`review`)로 표시한다. 신호에서
  빠지는 일은 데이터 누락으로도 생긴다(2026-09-28: 상위 30의 두 종목이 6~7거래일 빠졌다 복귀).
  사람이 매주 승인하며 그 주 매매에서 뺄 수 있다(10-21 사용자 결정).

금액은 원 단위 정수, 비율은 bp 정수로 계산해 부동소수 반올림이 끼지 않는다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

BP = 10_000


class RebalanceAction(StrEnum):
    SELL = "sell"  # 전량 매도
    RESIZE = "resize"  # 남긴 종목의 주식 수 조정
    BUY = "buy"  # 새로 편입
    HOLD = "hold"  # 남긴 종목 그대로
    SKIP = "skip"  # 편입 차례였지만 살 수 없어 건너뜀


_ACTION_ORDER = {action: index for index, action in enumerate(RebalanceAction)}


@dataclass(frozen=True)
class RebalancePolicy:
    slots: int = 10
    keep_rank: int = 30
    cash_reserve_bp: int = 200
    resize_band_bp: int = 2_500
    fee_rate_bp: int = 35

    def __post_init__(self) -> None:
        for name in ("slots", "keep_rank", "cash_reserve_bp", "resize_band_bp", "fee_rate_bp"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(f"policy field must be an integer — {name}={value!r}")
        if self.slots < 1 or self.keep_rank < self.slots:
            raise ValueError(
                f"invalid slots/keep_rank — slots={self.slots} keep_rank={self.keep_rank}"
            )
        if not 0 <= self.cash_reserve_bp < BP:
            raise ValueError(f"cash_reserve_bp out of range — got={self.cash_reserve_bp}")
        if self.resize_band_bp < 0 or self.fee_rate_bp < 0:
            raise ValueError(
                f"negative band or fee — resize_band_bp={self.resize_band_bp} "
                f"fee_rate_bp={self.fee_rate_bp}"
            )

    def shares_for(self, value_krw: int, price_krw: int) -> int:
        """목표 금액을 수수료를 얹은 가격으로 나눠 내림한다."""
        return value_krw * BP // (price_krw * (BP + self.fee_rate_bp))


@dataclass(frozen=True)
class RebalanceLine:
    symbol: str
    action: RebalanceAction
    rank: int | None
    current_shares: int
    target_shares: int
    reference_price_krw: int | None
    reason: str
    review: bool = False

    @property
    def delta_shares(self) -> int:
        return self.target_shares - self.current_shares


@dataclass(frozen=True)
class RebalancePlan:
    policy: RebalancePolicy
    cash_krw: int
    equity_krw: int
    target_value_krw: int
    lines: tuple[RebalanceLine, ...]

    def target_shares(self) -> dict[str, int]:
        """주문할 목표 주식 수. 건너뛴 종목은 넣지 않고, 파는 종목은 0이다."""
        return {
            line.symbol: line.target_shares
            for line in self.lines
            if line.action != RebalanceAction.SKIP
        }


def plan_rebalance(
    policy: RebalancePolicy,
    *,
    ranks: Mapping[str, int],
    holdings: Mapping[str, int],
    prices: Mapping[str, int],
    cash_krw: int,
    blocked: Mapping[str, str],
    previous_ranks: Mapping[str, int] | None = None,
    missing_reasons: Mapping[str, str] | None = None,
) -> RebalancePlan:
    """순위(1이 가장 좋다)·보유 주식 수·기준가격(원)·현금(원)·매매가 막힌 종목과 사유로
    목표를 정한다."""
    _validate(ranks, holdings, prices, cash_krw)
    equity = cash_krw + sum(shares * prices[symbol] for symbol, shares in holdings.items())
    target_value = equity * (BP - policy.cash_reserve_bp) // BP // policy.slots
    lines: list[RebalanceLine] = []

    kept: list[str] = []
    for symbol, shares in sorted(holdings.items()):
        rank = ranks.get(symbol)
        if symbol in blocked or (rank is not None and rank <= policy.keep_rank):
            kept.append(symbol)
            continue
        if rank is None:
            seen = _last_seen(symbol, previous_ranks)
            why = (missing_reasons or {}).get(symbol, "이유 미상")
            reason = f"신호에서 빠짐 — {why} ({seen})"
            lines.append(
                _line(symbol, RebalanceAction.SELL, rank, shares, 0, prices, reason, review=True)
            )
            continue
        reason = f"순위 {rank}위가 {policy.keep_rank}위 밖"
        lines.append(_line(symbol, RebalanceAction.SELL, rank, shares, 0, prices, reason))
    # 막힌 종목을 먼저 남기고, 나머지는 순위 순으로 자리 수까지. 넘친 종목은 판다.
    kept.sort(key=lambda symbol: (symbol not in blocked, ranks.get(symbol, 0), symbol))
    for symbol in kept[policy.slots :]:
        reason = f"자리 {policy.slots}개를 넘어 순위가 낮은 종목부터 판다"
        lines.append(
            _line(
                symbol, RebalanceAction.SELL, ranks.get(symbol), holdings[symbol], 0, prices, reason
            )
        )
    kept = kept[: policy.slots]

    for symbol in kept:
        rank, shares, price = ranks.get(symbol), holdings[symbol], prices[symbol]
        if symbol in blocked:
            reason = f"매매가 막혀 그대로 둔다 — {blocked[symbol]}"
            lines.append(_line(symbol, RebalanceAction.HOLD, rank, shares, shares, prices, reason))
            continue
        gap = abs(shares * price - target_value)
        resized = policy.shares_for(target_value, price)
        if gap * BP > target_value * policy.resize_band_bp and resized != shares:
            reason = f"목표 금액에서 ±{policy.resize_band_bp / 100:g}% 넘게 벗어남"
            lines.append(
                _line(symbol, RebalanceAction.RESIZE, rank, shares, resized, prices, reason)
            )
        else:
            reason = f"순위 {rank}위, 목표 금액 ±{policy.resize_band_bp / 100:g}% 안"
            lines.append(_line(symbol, RebalanceAction.HOLD, rank, shares, shares, prices, reason))

    free = policy.slots - len(kept)
    for rank, symbol in sorted((rank, symbol) for symbol, rank in ranks.items()):
        if free == 0 or rank > policy.keep_rank:
            break
        if symbol in holdings:
            continue
        price = prices.get(symbol)
        if symbol in blocked:
            reason = f"매매가 막혀 건너뜀 — {blocked[symbol]}"
        elif price is None or price <= 0:
            reason = "기준가격 없음"
        elif policy.shares_for(target_value, price) == 0:
            reason = f"1주 가격 {price:,}원이 목표 금액보다 크다"
        else:
            reason = (
                f"상위 {policy.slots} 신규 편입(순위 {rank}위)"
                if rank <= policy.slots
                else f"앞 순위를 살 수 없어 순위 {rank}위로 채움"
            )
            shares = policy.shares_for(target_value, price)
            lines.append(_line(symbol, RebalanceAction.BUY, rank, 0, shares, prices, reason))
            free -= 1
            continue
        lines.append(_line(symbol, RebalanceAction.SKIP, rank, 0, 0, prices, reason))

    lines.sort(key=lambda line: (_ACTION_ORDER[line.action], line.rank or 0, line.symbol))
    return RebalancePlan(policy, cash_krw, equity, target_value, tuple(lines))


def signal_anomalies(
    current: Mapping[str, int],
    previous: Mapping[str, int],
    *,
    top: int = 30,
    max_top_vanished: int = 1,
    max_count_drop_bp: int = 300,
) -> tuple[str, ...]:
    """직전 빌드와 견줘 데이터 누락이 의심되면 사유를 돌려준다. 비어 있으면 정상이다.

    09-01~10-20 빌드 37개에 대면 09-28(상위 30 두 종목이 빠졌다 복귀) 하루만 걸린다. 종목 수 감소는
    그 기간 최대 0.7%였다.
    """
    reasons: list[str] = []
    vanished = sorted(
        symbol for symbol, rank in previous.items() if rank <= top and symbol not in current
    )
    if len(vanished) > max_top_vanished:
        reasons.append(f"직전 빌드 상위 {top}위 중 {len(vanished)}종목이 사라짐 {vanished}")
    if previous and (len(previous) - len(current)) * BP > len(previous) * max_count_drop_bp:
        reasons.append(f"점수 받은 종목 수가 {len(previous)} → {len(current)}로 줄었음")
    return tuple(reasons)


def _last_seen(symbol: str, previous_ranks: Mapping[str, int] | None) -> str:
    if previous_ranks is None:
        return "직전 빌드 정보 없음"
    rank = previous_ranks.get(symbol)
    return "직전 빌드에도 없음" if rank is None else f"직전 빌드 {rank}위"


def _line(
    symbol: str,
    action: RebalanceAction,
    rank: int | None,
    current: int,
    target: int,
    prices: Mapping[str, int],
    reason: str,
    *,
    review: bool = False,
) -> RebalanceLine:
    return RebalanceLine(
        symbol, action, rank, current, target, prices.get(symbol), reason, review=review
    )


def _validate(
    ranks: Mapping[str, int],
    holdings: Mapping[str, int],
    prices: Mapping[str, int],
    cash_krw: int,
) -> None:
    if not isinstance(cash_krw, int) or cash_krw < 0:
        raise ValueError(f"cash must be a non-negative integer — cash_krw={cash_krw!r}")
    for symbol, rank in ranks.items():
        if not isinstance(rank, int) or rank < 1:
            raise ValueError(f"rank must be a positive integer — symbol={symbol} rank={rank!r}")
    if len(set(ranks.values())) != len(ranks):
        raise ValueError(f"ranks must be unique — n_symbols={len(ranks)}")
    for symbol, shares in holdings.items():
        if not isinstance(shares, int) or shares <= 0:
            raise ValueError(f"holding must be positive shares — symbol={symbol} shares={shares!r}")
        price = prices.get(symbol)
        if not isinstance(price, int) or price <= 0:
            raise ValueError(
                f"held symbol needs a positive reference price — symbol={symbol} price={price!r}"
            )
