"""S07 일일 위험 보고의 순수 계산. 손으로 셀 수 있는 작은 사례로 확인한다.

총자산 100,000원 = A 10주 × 1,000원(비중 0.1) + B 20주 × 2,000원(0.4) + 현금 50,000원(0.5).
A 의 수정주가는 코스피와 같은 비율로 움직이고(베타 1), B 는 최근 세션에만 있고 코스피의 두 배로
움직인다(베타 2). 그래서 두 종목 모두 있는 날의 보유 방식 수익률은 0.1·m + 0.4·2m = 0.9·m 이다.
"""

from __future__ import annotations

import statistics
from datetime import date, timedelta

import pytest

from strategy_workbench.domain.risk.facade.risk import (
    CIRCUIT_SCENARIO,
    GAP_SCENARIO,
    DrawdownLimits,
    DrawdownState,
    Holding,
    RiskLevel,
    RiskReport,
    ScenarioResult,
    build_report,
    level_after_close,
    tail_count,
)

LIMITS = DrawdownLimits(
    initial_peak_krw=100_000,
    warn_bp=1_000,
    reduce_bp=1_500,
    halt_bp=2_000,
    recover_bp=1_000,
    reduced_exposure_bp=5_000,
)
STATE = DrawdownState(RiskLevel.NORMAL, 100_000, None, 0, date(2026, 10, 30), 100_000)
HOLDINGS = (Holding("A", 10, 1_000), Holding("B", 20, 2_000))
MOVES = (0.01, -0.02, 0.015, -0.005, 0.03, -0.01)
WINDOW_START, WINDOW_END = date(2026, 6, 22), date(2026, 7, 29)


def _sessions() -> list[date]:
    first = date(2026, 10, 1)
    return [first + timedelta(days=i) for i in range(len(MOVES) + 1)]


def _index() -> dict[date, float]:
    index = {WINDOW_START: 100.0, WINDOW_END: 70.0}
    level = 70.0
    for i, day in enumerate(_sessions()):
        if i:
            level *= 1 + MOVES[i - 1]
        index[day] = level
    return index


def _closes() -> dict[str, dict[date, float]]:
    b: dict[date, float] = {}
    level = 500.0
    for i, day in enumerate(_sessions()):
        if i:
            level *= 1 + 2 * MOVES[i - 1]
        b[day] = level
    return {"A": dict(_index()), "B": b}


def _report(
    closes: dict[str, dict[date, float]] | None = None,
    account: list[tuple[date, int]] | None = None,
) -> RiskReport:
    return build_report(
        day=date(2026, 10, 30),
        holdings=HOLDINGS,
        cash_krw=50_000,
        equity_krw=100_000,
        names={"A": "가", "B": "나", "Z": "보유 아님"},
        closes=_closes() if closes is None else closes,
        index=_index(),
        account=account or [],
        state=STATE,
        limits=LIMITS,
    )


def _scenario(report: RiskReport, key: str) -> ScenarioResult:
    return next(item for item in report.scenarios if item.key == key)


def test_holding_returns_use_today_weights_and_adjusted_ratios() -> None:
    report = _report()
    expected = [0.9 * move for move in MOVES]
    volatility = report.volatility[0]
    assert (volatility.sessions, volatility.used) == (20, len(MOVES))
    assert volatility.value == pytest.approx(statistics.stdev(expected))
    beta = report.beta[0]
    assert beta.used == len(MOVES)
    assert beta.value == pytest.approx(0.9)
    assert report.weights == (("B", 0.4), ("A", 0.1))
    assert report.names == (("A", "가"), ("B", "나"))
    concentration = report.concentration
    assert (concentration.top1, concentration.top3, concentration.cash) == (0.4, 0.5, 0.5)
    assert report.history_end == _sessions()[-1]


def test_tail_averages_the_worst_two_and_a_half_percent() -> None:
    report = _report()
    tail = report.tail
    # 6일 → ⌈0.15⌉ = 1일: 가장 나쁜 날 하나가 ES 이자 VaR
    assert (tail.used, tail.tail) == (len(MOVES), 1)
    assert tail.expected_shortfall == pytest.approx(0.9 * -0.02)
    assert tail.value_at_risk == pytest.approx(0.9 * -0.02)


@pytest.mark.parametrize(("used", "tail"), [(0, 0), (1, 1), (40, 1), (41, 2), (250, 7)])
def test_tail_count_rounds_up(used: int, tail: int) -> None:
    assert tail_count(used) == tail


def test_missing_price_drops_the_pair_instead_of_filling_zero() -> None:
    closes = _closes()
    gone = _sessions()[3]
    del closes["B"][gone]
    report = _report(closes)
    # 빠진 날 앞뒤 두 쌍을 뺀다(0 으로 채우면 쌍 수가 그대로다)
    kept = [0.9 * move for i, move in enumerate(MOVES) if i not in (2, 3)]
    assert report.volatility[0].used == len(kept)
    assert report.volatility[0].value == pytest.approx(statistics.stdev(kept))
    assert report.missing_pairs == 2 + 2  # 창 앞 B 없는 두 쌍(06-22→07-29, 07-29→10-01) 포함


def test_return_beyond_price_limit_is_listed_and_left_out() -> None:
    closes = _closes()
    jump = _sessions()[2]
    closes["A"] = {day: value * (1.5 if day >= jump else 1) for day, value in closes["A"].items()}
    report = _report(closes)
    assert [(item.symbol, item.day) for item in report.dropped] == [("A", jump)]
    assert report.dropped[0].stock_return == pytest.approx((1 + MOVES[1]) * 1.5 - 1)
    assert report.volatility[0].used == len(MOVES) - 1


def test_history_scenario_proxies_unlisted_holding_with_beta() -> None:
    report = _report()
    july = _scenario(report, "2026-07")
    assert july.kospi_return == pytest.approx(-0.3)
    lines = {line.symbol: line for line in july.lines}
    assert lines["A"].stock_return == pytest.approx(-0.3) and not lines["A"].proxy
    assert lines["B"].stock_return == pytest.approx(2 * -0.3) and lines["B"].proxy
    assert july.portfolio_return == pytest.approx(0.1 * -0.3 + 0.4 * -0.6)
    assert july.loss_krw == -27_000
    assert july.level_after is RiskLevel.HALT
    # 가격도 코스피도 없는 구간은 계산하지 않고 빠진 종목을 적는다
    covid = _scenario(report, "2020-03")
    assert (covid.lines, covid.missing, covid.kospi_return) == ((), ("A", "B"), None)


def test_gap_and_circuit_scenarios() -> None:
    report = _report()
    gap = _scenario(report, GAP_SCENARIO)
    assert [(line.symbol, line.stock_return) for line in gap.lines] == [("B", -0.3)]
    assert gap.portfolio_return == pytest.approx(-0.12)
    assert gap.level_after is RiskLevel.WARN
    circuit = _scenario(report, CIRCUIT_SCENARIO)
    lines = {line.symbol: line.stock_return for line in circuit.lines}
    # A 베타 1 → −20%, B 베타 2 → −40% 이지만 하한가 −30%
    assert lines == {"A": pytest.approx(-0.2), "B": pytest.approx(-0.3)}
    assert circuit.portfolio_return == pytest.approx(-0.14)


def test_account_volatility_needs_full_window_and_period_compares_kospi() -> None:
    days = _sessions()
    short = [(day, 100_000 + i * 1_000) for i, day in enumerate(days)]
    report = _report(account=short)
    assert (report.account_volatility[0].used, report.account_volatility[0].value) == (6, None)
    period = report.account_period
    assert period is not None
    assert (period.start, period.end) == (days[0], days[-1])
    assert period.account_return == pytest.approx(0.06)
    assert period.kospi_return == pytest.approx(_index()[days[-1]] / _index()[days[0]] - 1)
    # 오늘 코스피가 아직 없으면 그 전 날까지만 견준다
    today = days[-1] + timedelta(days=1)
    later = _report(account=[*short, (today, 120_000)]).account_period
    assert later is not None and later.end == days[-1]
    long = [(date(2026, 1, 1) + timedelta(days=i), 100_000 + i) for i in range(22)]
    assert _report(account=long).account_volatility[0].value is not None


def test_level_after_close_follows_drawdown_rules() -> None:
    assert level_after_close(STATE, 91_000, LIMITS) is RiskLevel.NORMAL
    assert level_after_close(STATE, 90_000, LIMITS) is RiskLevel.WARN
    reduced = DrawdownState(RiskLevel.REDUCE, 100_000, None, 1_600, None, 84_000)
    # 축소는 −10% 위로 회복해야 풀린다
    assert level_after_close(reduced, 89_500, LIMITS) is RiskLevel.REDUCE
    assert level_after_close(reduced, 120_000, LIMITS) is RiskLevel.NORMAL
