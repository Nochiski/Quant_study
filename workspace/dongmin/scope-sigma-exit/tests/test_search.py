"""알파 탐색 지표 확인. 실행: `backend/.venv/bin/python -m pytest workspace/dongmin/scope-sigma-exit/tests`."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from search.features import clean_index, composite, pct_rank, price_features, top_by_cap, vol60  # noqa: E402  # reason: sys.path 뒤 import


def test_clean_index_zeroes_jump_and_missing_days():
    close = np.array([[np.nan], [100.0], [110.0], [np.nan], [55.0], [60.5]])
    jump = np.array([[False], [False], [False], [False], [True], [False]])
    p = clean_index(close, jump)
    assert np.isnan(p[0, 0])
    assert p[1:, 0] == pytest.approx([1.0, 1.1, 1.1, 1.1, 1.21])


def test_vol60_needs_40_returns_and_skips_jumps():
    rng = np.random.default_rng(0)
    r = rng.normal(0, 0.02, 80)
    close = 100 * np.exp(np.cumsum(r))[:, None]
    jump = np.zeros_like(close, dtype=bool)
    jump[70, 0] = True
    got = vol60(close, jump, 79)[0]
    lr = np.diff(np.log(close[19:80, 0]))
    keep = np.ones(60, dtype=bool)
    keep[70 - 20] = False
    assert got == pytest.approx(np.std(lr[keep], ddof=1))
    short = close.copy()
    short[:50] = np.nan
    assert np.isnan(vol60(short, jump, 79)[0])


def test_price_features_definitions():
    n = 300
    p = np.linspace(1.0, 2.0, n)[:, None]
    p[-1] = 1.8  # 마지막 날 하락
    start = np.array([0])
    f = price_features(p, start, n - 1)
    assert f["MOM12_1"][0] == pytest.approx(p[n - 1 - 21, 0] / p[n - 1 - 252, 0] - 1)
    assert f["REV1M"][0] == pytest.approx(-(1.8 / p[n - 1 - 21, 0] - 1))
    assert f["H52"][0] == pytest.approx(1.8 / p[n - 2, 0])
    assert np.isnan(price_features(p, np.array([100]), n - 1)["H52"][0])  # 상장 252세션 미만


def test_pct_rank_and_composite():
    v = np.array([3.0, 1.0, 2.0, 2.0, np.nan, 9.0])
    m = np.array([True, True, True, True, True, False])
    assert pct_rank(v, m)[:4] == pytest.approx([1.0, 0.0, 0.5, 0.5])
    assert np.isnan(pct_rank(v, m)[4]) and np.isnan(pct_rank(v, m)[5])
    feats = {"A": np.array([1.0, 2.0, 3.0]), "B": np.array([3.0, 2.0, np.nan])}
    c = composite(feats, (("A", 1), ("B", -1)), np.ones(3, dtype=bool))
    assert c[0] == pytest.approx(0.0) and c[1] == pytest.approx(1.0) and np.isnan(c[2])


def test_top_by_cap():
    cap = np.array([5.0, 9.0, np.nan, 7.0, 1.0])
    elig = np.array([True, True, True, True, False])
    assert top_by_cap(cap, elig, 2).tolist() == [False, True, False, True, False]


def _flat_panel(n_sessions: int, level: float = 100.0):
    from datetime import date, timedelta

    from search.panel import INDEX_NAMES, Panel
    from sim.data import Market

    days, d = [], date(2020, 1, 1)
    while len(days) < n_sessions:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    shape = (n_sessions, 1)
    one = np.ones(shape)
    m = Market(snapshot_id="t", sessions=tuple(days), tickers=("A",), adj_open=one, adj_close=one, volume=one, adv20=one,
               unadjusted_jump=np.zeros(shape, dtype=bool), last_bar=np.array([n_sessions - 1]), index_sessions=tuple(days),
               kospi=np.ones(n_sessions), kospi200=np.ones(n_sessions))
    lv = {name: np.full(n_sessions, level) for _, name in INDEX_NAMES}
    return Panel(market=m, mktcap=one, on_kospi=one.astype(bool), common=one.astype(bool), index_open=lv, index_close=dict(lv))


def test_run_index_charges_trade_cost_and_expense(monkeypatch):
    import search.strategies as st
    from search.family import Candidate

    panel = _flat_panel(300)
    s = panel.market.sessions
    monkeypatch.setattr(st, "FIRST_SIGNAL", s[250])
    c = Candidate("T", "추세", "trend")
    r = st.run_index(c, panel, [250], s[-1])  # 평평한 지수: 200일 평균과 같아 '위' 가 아니다 → 현금
    assert r.equity[-1] == pytest.approx(st.START_CAPITAL)
    panel.index_close["코스피 200"][250] = 101.0  # 신호일만 평균 위 → 다음 날 시가 100 에 산다
    r = st.run_index(c, panel, [250], s[-1])
    invest = st.START_CAPITAL * (1 - 25 / 10_000)
    held_days = len(s) - 251  # 체결일 장 마감부터 보수(체결일도 보유)
    assert r.equity[-1] == pytest.approx(invest * (1 - 0.0015 / 252) ** held_days)
    assert r.traded_krw == pytest.approx(st.START_CAPITAL)


def test_marks_and_rolling_windows():
    from datetime import date

    from search.run import ROLL_STARTS, marks

    s = (date(2020, 1, 31), date(2020, 2, 3), date(2020, 2, 28), date(2020, 3, 2), date(2020, 3, 31))
    assert marks(s) == [0, 2, 4]
    assert len(ROLL_STARTS) == 8 and ROLL_STARTS[-1] == (2023, 7)


def test_tilt_and_exclude_weights():
    from search.features import tilted_caps

    cap = np.array([100.0, 100.0, 100.0, 50.0])
    r = np.array([1.0, 0.0, np.nan, 0.5])
    assert tilted_caps(cap, r, mode="tilt").tolist() == pytest.approx([150.0, 50.0, 100.0, 50.0])
    got = tilted_caps(cap, np.array([0.1, 0.3, np.nan, 0.19]), mode="exclude")
    assert got[1] == 100.0 and got[2] == 100.0 and np.isnan(got[0]) and np.isnan(got[3])
    assert tilted_caps(cap, r, mode="cap").tolist() == cap.tolist()


def test_proportional_weighting_follows_scores():
    from sim.configs import Variant
    from sim.engine import _weights

    v = Variant(name="t", axis="t", weighting="proportional")
    scores = {"A": 3.0, "B": 1.0}
    w = _weights(v, ["A", "B"], [], scores, {}, [], None, 0, {})
    assert w == pytest.approx({"A": 0.75, "B": 0.25})


def test_alt_helpers():
    from datetime import date

    from search.alt import Report, lagged_change, op_yoy, rolling_sum

    mat = np.arange(30, dtype=float)[:, None] * np.array([[1.0, 2.0]])
    assert lagged_change(mat, 29, 2, 20)[0] == pytest.approx(20.0)
    mat[25:, 1] = np.nan
    s = rolling_sum(mat, 29, 10, 8)
    assert s[0] == pytest.approx(sum(range(20, 30))) and np.isnan(s[1])
    reps = [
        Report(date(2024, 5, 14), 2024, "11013", "OFS", 50.0),
        Report(date(2024, 5, 14), 2024, "11013", "CFS", 100.0),
        Report(date(2025, 5, 15), 2025, "11013", "CFS", 130.0),
        Report(date(2025, 5, 15), 2025, "11013", "OFS", 10.0),
    ]
    assert op_yoy(reps, date(2025, 6, 30)) == pytest.approx(30.0)  # 연결끼리
    assert np.isnan(op_yoy(reps, date(2025, 5, 14)))  # 아직 2025 1분기 공시 전 → 2024 1분기, 전년 없음
    assert np.isnan(op_yoy(reps, date(2025, 10, 1)))  # 120일 지남


def test_core_satellite_weights_and_buffer():
    from search.features import core_satellite

    cap = np.array([100.0, 50.0, 10.0, 9.0, 8.0, 7.0, np.nan])
    members = np.array([True, True, True, True, True, True, False])
    score = np.array([np.nan, np.nan, 0.1, 0.9, 0.5, 0.7, 1.0])
    w, sat = core_satellite(cap, members, score, prev=frozenset({2}), n_core=2, n_sat=2, keep=4)
    total = 100 + 50 + 10 + 9 + 8 + 7
    assert w[0] == pytest.approx(100 / total) and w[1] == pytest.approx(50 / total)
    assert sat == frozenset({2, 3})  # 2 는 순위 4 안이라 유지, 빈 자리 1 개는 1위(3)
    rest = 1 - 150 / total
    assert w[2] == pytest.approx(rest / 2) and w[3] == pytest.approx(rest / 2)
    _, sat2 = core_satellite(cap, members, score, prev=frozenset({2}), n_core=2, n_sat=2, keep=3)
    assert sat2 == frozenset({3, 5})  # 순위 4 → 보유 한도 3 밖


def test_event_flags_window():
    from datetime import date

    from search.alt import event_flags

    ev = {"A": [(date(2020, 1, 10), 5.0), (date(2020, 6, 1), 3.0)], "B": [(date(2019, 1, 1), 9.0)]}
    count, amount = event_flags(ev, ("A", "B", "C"), date(2020, 6, 1), 182)
    assert count.tolist() == [2.0, 0.0, 0.0] and amount.tolist() == [8.0, 0.0, 0.0]
    count, _ = event_flags(ev, ("A",), date(2020, 7, 31), 30)
    assert count.tolist() == [0.0]  # 6-01 은 7-01 이전이라 30일 창 밖
