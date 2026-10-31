"""S07 일일 위험 보고(2026-10-31 사용자 승인). 판단에 쓰지 않는 보고용 순수 계산이다.

주문·낙폭 단계·목표를 바꾸지 않는다. 계좌 실적은 아직 짧아서(2026-10-21 시작) 변동성·ES·베타·
시나리오는 **보유 방식**으로 잰다 — 마지막 장 마감 기록의 보유를 과거에 그대로 들고 있었다면.

- 비중 wᵢ = 주식 수 × 장 마감가 ÷ 총자산(현금은 수익률 0). 장 마감가는 원주가다.
- 수익률은 수정주가 비율(adjₜ ÷ adjₜ₋₁ − 1)로만 쓴다. 원장 수정주가의 수준은 원주가와 다르다.
- 세션은 코스피 지수 행이 있는 날이다. 그 세션 쌍에 보유 종목 하나라도 가격이 없거나, 하루
  수익률이 가격제한(±30%) 밖이면(`PRICE_LIMIT_FRACTION` 초과 — 조정 공백·기준가 재설정일
  뿐이다) 그 쌍을 빼고 뺀 수를 싣는다. 0이나 앞 값으로 채우지 않는다.
- 시나리오의 낙폭 단계는 지금 상태에서 그 손실로 장 마감했다면 될 단계다(`level_after_close`).
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from strategy_workbench.domain.risk._drawdown import DrawdownState, RiskLevel, level_after_close
from strategy_workbench.domain.risk._limits import DrawdownLimits

# 연율화 거래일 — 백테스트 지표(`compute_analytics`의 `annualization_days` 기본값)와 같은 값이다
ANNUALIZATION_DAYS = 252
VOLATILITY_SESSIONS = (20, 60)
BETA_SESSIONS = (60, 250)
TAIL_SESSIONS = 250
TAIL_PER_MILLE = 25  # 97.5% ES = 나쁜 쪽 2.5%
# 가격제한 ±30%에 호가 반올림 여유를 둔 선. 이보다 큰 하루 수익률은 실제 체결일 수 없다.
PRICE_LIMIT_FRACTION = 0.31
GAP_FRACTION = -0.30  # 종목 하나 하한가 갭
CIRCUIT_INDEX_FRACTION = -0.20  # 서킷브레이커 3단계(그날 장 종료, 매도 불가)


@dataclass(frozen=True)
class ScenarioWindow:
    """과거 구간 재현. 시작일 종가 → 끝일 종가(사용자 승인 B1~B3)."""

    key: str
    start: date
    end: date


HISTORY_SCENARIOS = (
    ScenarioWindow("2020-03", date(2020, 1, 22), date(2020, 3, 19)),
    ScenarioWindow("2024-08-05", date(2024, 8, 2), date(2024, 8, 5)),
    ScenarioWindow("2026-07", date(2026, 6, 22), date(2026, 7, 29)),
)
GAP_SCENARIO = "gap-30"
CIRCUIT_SCENARIO = "circuit-20"


@dataclass(frozen=True)
class Holding:
    symbol: str
    shares: int
    close_krw: int


@dataclass(frozen=True)
class WindowStat:
    """최근 `sessions`개 세션 쌍 중 쓸 수 있었던 `used`개로 낸 값. 둘 미만이면 값이 없다."""

    sessions: int
    used: int
    value: float | None


@dataclass(frozen=True)
class TailStat:
    """하루 97.5% ES·VaR(수익률, 음수가 손실). `tail`은 평균한 나쁜 날 수다."""

    sessions: int
    used: int
    tail: int
    expected_shortfall: float | None
    value_at_risk: float | None


@dataclass(frozen=True)
class AccountPeriod:
    """장 마감 기록 기간의 계좌 실적과 같은 기간 코스피(둘 다 종가 대 종가)."""

    start: date
    end: date
    account_return: float
    kospi_return: float


@dataclass(frozen=True)
class Concentration:
    top1: float
    top3: float
    top5: float
    cash: float
    count: int


@dataclass(frozen=True)
class ScenarioLine:
    """시나리오에서 종목 하나의 수익률. `proxy`면 그 구간 가격이 없어 베타 × 코스피로 대신했다."""

    symbol: str
    weight: float
    stock_return: float
    proxy: bool = False


@dataclass(frozen=True)
class ScenarioResult:
    key: str
    start: date | None
    end: date | None
    portfolio_return: float
    loss_krw: int
    kospi_return: float | None
    level_after: RiskLevel
    lines: tuple[ScenarioLine, ...]
    missing: tuple[str, ...] = ()  # 가격도 베타도 없어 계산에서 뺀 종목


@dataclass(frozen=True)
class DroppedReturn:
    symbol: str
    day: date
    stock_return: float


@dataclass(frozen=True)
class RiskReport:
    day: date
    history_end: date | None  # 가격 이력의 마지막 날(원장은 다음 날 아침에 들어온다)
    equity_krw: int
    cash_krw: int
    weights: tuple[tuple[str, float], ...]  # 비중 큰 순
    names: tuple[tuple[str, str], ...]
    volatility: tuple[WindowStat, ...]  # 하루 표본표준편차
    beta: tuple[WindowStat, ...]
    tail: TailStat
    account_volatility: tuple[WindowStat, ...]  # 계좌 실적. `used`가 `sessions`보다 작으면 값 없음
    account_period: AccountPeriod | None
    concentration: Concentration
    state: DrawdownState
    scenarios: tuple[ScenarioResult, ...]
    dropped: tuple[DroppedReturn, ...]  # 가격제한 밖이라 뺀 하루 수익률
    missing_pairs: int  # 최근 `TAIL_SESSIONS` 세션 쌍 중 가격이 없어 뺀 수


def annualized(daily_volatility: float) -> float:
    return daily_volatility * math.sqrt(ANNUALIZATION_DAYS)


def tail_count(used: int) -> int:
    """평균할 나쁜 날 수 = ⌈used × 2.5%⌉(250 → 7)."""
    return (used * TAIL_PER_MILLE + 999) // 1000


def build_report(
    *,
    day: date,
    holdings: Sequence[Holding],
    cash_krw: int,
    equity_krw: int,
    names: Mapping[str, str],
    closes: Mapping[str, Mapping[date, float]],
    index: Mapping[date, float],
    account: Sequence[tuple[date, int]],
    state: DrawdownState,
    limits: DrawdownLimits,
) -> RiskReport:
    """보고 하나를 낸다.

    Args:
        holdings·cash_krw·equity_krw: 그날 대사가 맞은 장 마감 기록.
        closes: 종목별 수정주가(날짜 → 값). 이 함수는 날짜를 거르지 않는다.
        index: 코스피 종가(날짜 → 값). 이 날짜들이 세션이다.
        account: 대사가 맞은 장 마감 총자산, 날짜 순.
        state·limits: 지금 낙폭 단계와 한도(옮기기만 한다).

    Raises:
        ValueError: 총자산이 0 이하.
    """
    if equity_krw <= 0:
        raise ValueError(f"risk report needs positive equity — day={day} equity_krw={equity_krw}")
    weights = {h.symbol: h.shares * h.close_krw / equity_krw for h in holdings if h.shares}
    sessions = sorted(index)
    dropped = _dropped(weights, closes, sessions)
    blocked = {(item.symbol, item.day) for item in dropped}
    pairs = list(zip(sessions, sessions[1:], strict=False))
    portfolio: list[tuple[date, float | None]] = [
        (end, _portfolio_return(weights, closes, start, end, blocked)) for start, end in pairs
    ]
    market = {end: index[end] / index[start] - 1 for start, end in pairs}
    tail_window = portfolio[-TAIL_SESSIONS:]
    betas = {
        symbol: _stock_beta(closes.get(symbol, {}), pairs[-TAIL_SESSIONS:], market, blocked, symbol)
        for symbol in weights
    }
    ordered = sorted(weights.items(), key=lambda item: (-item[1], item[0]))
    return RiskReport(
        day=day,
        history_end=sessions[-1] if sessions else None,
        equity_krw=equity_krw,
        cash_krw=cash_krw,
        weights=tuple(ordered),
        names=tuple(sorted((s, n) for s, n in names.items() if s in weights)),
        volatility=tuple(_volatility(portfolio[-n:], n) for n in VOLATILITY_SESSIONS),
        beta=tuple(_beta(portfolio[-n:], market, n) for n in BETA_SESSIONS),
        tail=_tail(tail_window),
        account_volatility=tuple(_account_volatility(account, n) for n in VOLATILITY_SESSIONS),
        account_period=_account_period(account, index),
        concentration=_concentration(ordered, cash_krw, equity_krw),
        state=state,
        scenarios=(
            *(
                _history_scenario(window, weights, closes, index, betas, state, limits)
                for window in HISTORY_SCENARIOS
            ),
            _gap_scenario(weights, state, limits),
            _circuit_scenario(weights, betas, state, limits),
        ),
        dropped=tuple(sorted(dropped, key=lambda item: (item.day, item.symbol))),
        missing_pairs=sum(1 for _, value in tail_window if value is None),
    )


def _dropped(
    weights: Mapping[str, float], closes: Mapping[str, Mapping[date, float]], sessions: list[date]
) -> list[DroppedReturn]:
    out: list[DroppedReturn] = []
    for symbol in weights:
        series = closes.get(symbol, {})
        for start, end in zip(sessions, sessions[1:], strict=False):
            if start in series and end in series:
                value = series[end] / series[start] - 1
                if abs(value) > PRICE_LIMIT_FRACTION:
                    out.append(DroppedReturn(symbol, end, value))
    return out


def _portfolio_return(
    weights: Mapping[str, float],
    closes: Mapping[str, Mapping[date, float]],
    start: date,
    end: date,
    blocked: set[tuple[str, date]],
) -> float | None:
    total = 0.0
    for symbol, weight in weights.items():
        series = closes.get(symbol, {})
        if start not in series or end not in series or (symbol, end) in blocked:
            return None
        total += weight * (series[end] / series[start] - 1)
    return total


def _volatility(window: Sequence[tuple[date, float | None]], sessions: int) -> WindowStat:
    values = [value for _, value in window if value is not None]
    return WindowStat(sessions, len(values), statistics.stdev(values) if len(values) >= 2 else None)


def _beta(
    window: Sequence[tuple[date, float | None]], market: Mapping[date, float], sessions: int
) -> WindowStat:
    pairs = [(value, market[day]) for day, value in window if value is not None]
    return WindowStat(sessions, len(pairs), _slope(pairs))


def _slope(pairs: Sequence[tuple[float, float]]) -> float | None:
    if len(pairs) < 2:
        return None
    ys = [y for y, _ in pairs]
    xs = [x for _, x in pairs]
    variance = statistics.variance(xs)
    return statistics.covariance(ys, xs) / variance if variance > 0 else None


def _stock_beta(
    series: Mapping[date, float],
    pairs: Sequence[tuple[date, date]],
    market: Mapping[date, float],
    blocked: set[tuple[str, date]],
    symbol: str,
) -> float | None:
    return _slope(
        [
            (series[end] / series[start] - 1, market[end])
            for start, end in pairs
            if start in series and end in series and (symbol, end) not in blocked
        ]
    )


def _tail(window: Sequence[tuple[date, float | None]]) -> TailStat:
    values = sorted(value for _, value in window if value is not None)
    count = tail_count(len(values))
    if count == 0:
        return TailStat(TAIL_SESSIONS, len(values), 0, None, None)
    worst = values[:count]
    return TailStat(TAIL_SESSIONS, len(values), count, sum(worst) / count, worst[-1])


def _account_volatility(account: Sequence[tuple[date, int]], sessions: int) -> WindowStat:
    returns = [b / a - 1 for (_, a), (_, b) in zip(account, account[1:], strict=False)]
    used = returns[-sessions:]
    enough = len(used) >= sessions
    return WindowStat(sessions, len(used), statistics.stdev(used) if enough else None)


def _account_period(
    account: Sequence[tuple[date, int]], index: Mapping[date, float]
) -> AccountPeriod | None:
    """코스피 종가가 있는 장 마감 기록 날끼리만 견준다(그날 지수는 다음 날 아침에 들어온다)."""
    common = [(day, equity) for day, equity in account if day in index]
    if len(common) < 2:
        return None
    (start, first), (end, last) = common[0], common[-1]
    return AccountPeriod(start, end, last / first - 1, index[end] / index[start] - 1)


def _concentration(
    ordered: Sequence[tuple[str, float]], cash_krw: int, equity_krw: int
) -> Concentration:
    values = [weight for _, weight in ordered]
    return Concentration(
        sum(values[:1]), sum(values[:3]), sum(values[:5]), cash_krw / equity_krw, len(values)
    )


def _scenario(
    key: str,
    window: tuple[date, date] | None,
    lines: Sequence[ScenarioLine],
    kospi: float | None,
    state: DrawdownState,
    limits: DrawdownLimits,
    missing: Sequence[str] = (),
) -> ScenarioResult:
    portfolio = sum(line.weight * line.stock_return for line in lines)
    base = state.last_equity_krw if state.last_equity_krw is not None else limits.initial_peak_krw
    loss_krw = round(base * portfolio)
    return ScenarioResult(
        key,
        None if window is None else window[0],
        None if window is None else window[1],
        portfolio,
        loss_krw,
        kospi,
        level_after_close(state, base + loss_krw, limits),
        tuple(lines),
        tuple(missing),
    )


def _history_scenario(
    window: ScenarioWindow,
    weights: Mapping[str, float],
    closes: Mapping[str, Mapping[date, float]],
    index: Mapping[date, float],
    betas: Mapping[str, float | None],
    state: DrawdownState,
    limits: DrawdownLimits,
) -> ScenarioResult:
    kospi = (
        index[window.end] / index[window.start] - 1
        if window.start in index and window.end in index
        else None
    )
    lines: list[ScenarioLine] = []
    missing: list[str] = []
    for symbol, weight in weights.items():
        series = closes.get(symbol, {})
        beta = betas.get(symbol)
        if window.start in series and window.end in series:
            lines.append(
                ScenarioLine(symbol, weight, series[window.end] / series[window.start] - 1)
            )
        elif beta is not None and kospi is not None:
            # 그 구간에 상장 전이면 그 종목 베타 × 코스피(사용자 승인 B4)
            lines.append(ScenarioLine(symbol, weight, beta * kospi, proxy=True))
        else:
            missing.append(symbol)
    return _scenario(
        window.key, (window.start, window.end), lines, kospi, state, limits, sorted(missing)
    )


def _gap_scenario(
    weights: Mapping[str, float], state: DrawdownState, limits: DrawdownLimits
) -> ScenarioResult:
    """종목마다 −30% 갭 하나. 결과는 가장 큰 손실(비중이 가장 큰 종목) 하나다."""
    if not weights:
        return _scenario(GAP_SCENARIO, None, (), None, state, limits)
    symbol, weight = max(weights.items(), key=lambda item: (item[1], item[0]))
    return _scenario(
        GAP_SCENARIO, None, (ScenarioLine(symbol, weight, GAP_FRACTION),), None, state, limits
    )


def _circuit_scenario(
    weights: Mapping[str, float],
    betas: Mapping[str, float | None],
    state: DrawdownState,
    limits: DrawdownLimits,
) -> ScenarioResult:
    """코스피 −20%(3단계, 그날 장 종료)에서 매도 없이 종목마다 베타 × −20%, 하한가 −30%(B5)."""
    lines: list[ScenarioLine] = []
    missing: list[str] = []
    for symbol, weight in weights.items():
        beta = betas.get(symbol)
        if beta is None:
            missing.append(symbol)
            continue
        lines.append(ScenarioLine(symbol, weight, max(GAP_FRACTION, beta * CIRCUIT_INDEX_FRACTION)))
    return _scenario(
        CIRCUIT_SCENARIO, None, lines, CIRCUIT_INDEX_FRACTION, state, limits, sorted(missing)
    )
