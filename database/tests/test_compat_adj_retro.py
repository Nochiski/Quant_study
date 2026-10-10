"""QL-E(T-18) — v3 소비자 `daily_prices.adj_close` 를 v3 와 같은 소급 조정 기준으로.

v3 는 키움 ka10081 수정주가(`upd_stkpc_tp=1`)를 그날 기준일로 다시 받아 종목의 전 기간 adj_close 를
덮는다(v3 `scripts/_backfill_mode_helpers.py` run_adj_price_backfill — 20:05 시세 수집이 최근 5행을
adj_close NULL 로 덮으므로 사실상 매일 전 종목). 그래서 **최신 행 = 원종가**이고 과거 행은 그 뒤 사건으로
소급 조정된 값이다. equity `price_adj_daily` 는 전방 조정(첫 관측 수준 고정)이라 값 수준이 다르다.

compat 은 v3 쪽으로만 기준을 옮긴다(equity·fi 는 그대로):
  adj_close(d) = close(d) × g(d) / g(창 안 마지막 행),  g = cum_share_factor ÷ cum_price_only_factor
창 안에서 g 가 바뀐(사건이 접힌) 종목은 대상 파일의 창 밖 옛 행도 같은 기준으로 다시 쓴다.

합성 판(D = 2026-07-31, 창 14일 = 07-17~07-31, equity 판 `m_`):
  EV    145210 — 2017·2024 사건으로 g = 2.358… × 1.996… 인 채로 07-24 에 10:1 액면병합(share 0.09999998…).
                 서버 실측 모양 그대로 — v3 10-08 사본 2025-03-21 adj 11,260(= 10 × 종가 1,126),
                 옛 compat 5,300.3(= 1,126 × 4.707)
  PO    035720 — 07-27 에 가격 전용 계수(⑤ price_only 1.25)만 접힌다
  PAST  005930 — 창 앞 사건만(g = 2.5 고정) — 창 안 사건 없음
  NONE  000660 — 사건 없음(g = 1)
"""
from __future__ import annotations

import datetime as dt
import itertools
import sqlite3
from pathlib import Path

import pytest
from compat import export
from compat.quant_db import SCHEMA_SQL_PATH
from conftest import _make_stage_tree
from test_compat_export import _price_row, _uni_row

D = "20260731"
BUILD = "m_20260731T231000_000000Z"
FROM_ISO = "2026-07-17"

EV, PO, PAST, NONE = "145210", "035720", "005930", "000660"
G_PRE = 2.3580246913580245 * 1.9962616822429908        # 2017-12-19 · 2024-09-25 사건 누적
G_POST = G_PRE * 0.09999998105911195                    # 2026-07-24 10:1 액면병합(mktcap_neutral)
FOLD_EV = dt.date(2026, 7, 24)
FOLD_PO = dt.date(2026, 7, 27)
PO_FACTOR = 1.25

OLD = (dt.date(2025, 3, 20), dt.date(2025, 3, 21))       # 창 밖 옛 날짜(서버 실측 145210 행)
BEFORE = dt.date(2026, 7, 16)                            # 창 바로 앞
WINDOW = tuple(dt.date(2026, 7, 17) + dt.timedelta(days=i) for i in range(15)
               if (dt.date(2026, 7, 17) + dt.timedelta(days=i)).weekday() < 5)


def _close(ticker: str, d: dt.date) -> int:
    if ticker == EV:
        if d in OLD:
            return 1_126
        base = 100 + (d - BEFORE).days                   # 액면병합 전 100원대
        return base * 10 if d >= FOLD_EV else base
    return {PO: 30_000, PAST: 70_000, NONE: 50_000}[ticker] + (d - BEFORE).days * 100


def _factors(ticker: str, d: dt.date) -> tuple[float, float]:
    """(cum_share_factor, cum_price_only_factor)."""
    if ticker == EV:
        return (G_POST if d >= FOLD_EV else G_PRE), 1.0
    if ticker == PO:
        return 1.0, (PO_FACTOR if d >= FOLD_PO else 1.0)
    if ticker == PAST:
        return 2.5, 1.0
    return 1.0, 1.0


def g(ticker: str, d: dt.date) -> float:
    share, price_only = _factors(ticker, d)
    return share / price_only


def _adj_row(ticker: str, d: dt.date) -> dict:
    """`price_adj_daily` 실물 18열(e1.26.0) — adj = 원주가 × share ÷ price_only."""
    share, price_only = _factors(ticker, d)
    adj = _close(ticker, d) * share / price_only
    return {"ticker": ticker, "date": d, "adj_open": adj, "adj_high": adj, "adj_low": adj,
            "adj_close": adj, "adj_volume_shr": 1_000_000.0, "cum_price_factor": 1.0,
            "cum_share_factor": share, "cum_price_only_factor": price_only,
            "n_factors_applied": 0, "n_price_only_applied": 0, "n_unadjusted_events": 0,
            "n_price_unresolved_events": 0, "available_date": d, "available_basis": "derived",
            "basis": "krx", "corp_action_pending": False}


def _roots(base: Path) -> tuple[Path, Path]:
    tickers = (EV, PO, PAST, NONE)
    days = (*OLD, BEFORE, *WINDOW)
    prices = [_price_row(t, d, _close(t, d), value=1_000_000_000, mktcap=10**12)
              for t in tickers for d in days]
    adj = [_adj_row(t, d) for t in tickers for d in days]
    uni = [_uni_row(t, d, "KOSPI", "common") for t in tickers for d in days]
    eq = base / "eq"
    for table, rows in (("price_daily", prices), ("price_adj_daily", adj),
                        ("universe_daily", uni)):
        _make_stage_tree(eq, table, rows, build_id=BUILD)
    (base / "st" / "stage").mkdir(parents=True)
    return eq / "stage", base / "st" / "stage"


@pytest.fixture(scope="module")
def roots(tmp_path_factory) -> tuple[Path, Path]:
    return _roots(tmp_path_factory.mktemp("qle"))


# 대상 파일의 창 밖 옛 행 — 앞선 반영본(사건 전 기준: 그때의 최신 행 = 원종가)
NO_EQUITY_DAY = "2025-03-19"                            # equity 에 행이 없는 날(대상에만 있다)


def _seed_target(target: Path) -> None:
    con = sqlite3.connect(str(target))
    try:
        con.executescript(SCHEMA_SQL_PATH.read_text(encoding="utf-8"))
        rows = []
        for t in (EV, PO, PAST, NONE):
            for d in (*OLD, BEFORE):
                c = _close(t, d)
                rows.append((t, d.isoformat(), c, c, c, c, 1, 1, float(c)))
        rows.append((EV, NO_EQUITY_DAY, 1_100, 1_100, 1_100, 1_100, 1, 1, 1_100.0))
        con.executemany("INSERT INTO daily_prices VALUES (?,?,?,?,?,?,?,?,?)", rows)
        con.commit()
    finally:
        con.close()


def _run(roots: tuple[Path, Path], target: Path, date: str = D):
    return export(equity_root=roots[0], stage_root=roots[1], date=date, basis="morning",
                  target=target, tables=["daily_prices"], full=True, window_days=14)


def _adj(target: Path) -> dict[tuple[str, str], tuple[int, float | None]]:
    con = sqlite3.connect(str(target))
    try:
        return {(r[0], r[1]): (r[2], r[3]) for r in con.execute(
            "SELECT stock_code, trade_date, close, adj_close FROM daily_prices")}
    finally:
        con.close()


@pytest.fixture
def done(roots, tmp_path: Path):
    target = tmp_path / "quant.db"
    _seed_target(target)
    return _run(roots, target), _adj(target)


# ── 창 안 ────────────────────────────────────────────────────────────────────
def test_latest_row_is_the_raw_close(done) -> None:
    """① 각 종목 창 안 마지막 행 = 원종가(v3 ka10081 수정주가와 같은 기준)."""
    _, got = done
    last = WINDOW[-1].isoformat()
    for t in (EV, PO, PAST, NONE):
        close, adj = got[(t, last)]
        assert adj == close, t                          # 근사가 아니라 정확히 같다


def test_rows_before_the_event_are_scaled_by_the_factor_ratio(done) -> None:
    """② 사건 전 행 = 종가 × g(d) ÷ g(마지막) — 액면병합 10:1 이면 10배, 가격 전용 1.25 면 1.25배."""
    _, got = done
    for d in WINDOW:
        close, adj = got[(EV, d.isoformat())]
        want = close * (G_PRE / G_POST) if d < FOLD_EV else close
        assert adj == pytest.approx(want, rel=1e-12), d
        close, adj = got[(PO, d.isoformat())]
        assert adj == pytest.approx(close * PO_FACTOR if d < FOLD_PO else close, rel=1e-12), d
    close, adj = got[(EV, "2026-07-23")]
    assert (close, adj) == (107, pytest.approx(1_070.0, rel=1e-6))     # 액면병합 전 107원 × 10


def test_tickers_without_an_event_in_the_window_keep_adj_equal_close(done) -> None:
    """③ 창 안에 사건이 없으면 창 앞 사건이 있어도(g 고정) adj_close = close 다(전방 조정이면 2.5배)."""
    _, got = done
    for t in (PAST, NONE):
        for d in WINDOW:
            close, adj = got[(t, d.isoformat())]
            assert adj == close, (t, d)


def test_returns_are_the_same_as_forward_adjusted(done) -> None:
    """④ 하루 수익률은 전방 조정(equity adj_close)과 같다 — 비율만 쓰는 소비자(모멘텀 등)는 불변."""
    _, got = done
    for t in (EV, PO, PAST, NONE):
        for prev, cur in itertools.pairwise(WINDOW):
            ours = got[(t, cur.isoformat())][1] / got[(t, prev.isoformat())][1]
            fwd = (_close(t, cur) * g(t, cur)) / (_close(t, prev) * g(t, prev))
            assert ours == pytest.approx(fwd, rel=1e-12), (t, cur)


# ── 창 밖 ────────────────────────────────────────────────────────────────────
def test_event_tickers_rows_outside_the_window_are_rebased(done) -> None:
    """⑤ 창 안에서 g 가 바뀐 종목은 대상의 창 밖 옛 행도 같은 기준으로 다시 쓴다(v3 가 사건 뒤 종목
    전 기간을 다시 받는 것과 같은 결과). equity 행이 없는 날은 기준을 모르므로 NULL(P1)."""
    res, got = done
    close, adj = got[(EV, BEFORE.isoformat())]
    assert adj == pytest.approx(close * G_PRE / G_POST, rel=1e-12)
    close, adj = got[(PO, BEFORE.isoformat())]
    assert adj == pytest.approx(close * PO_FACTOR, rel=1e-12)
    assert got[(EV, NO_EQUITY_DAY)] == (1_100, None)
    rebase = res.tables["daily_prices"].rebase
    assert rebase is not None
    assert rebase["before"] == FROM_ISO
    assert rebase["tickers"] == sorted([EV, PO])
    assert (rebase["n_rows"], rebase["n_null"]) == (7, 1)   # EV 옛 3행 + 빈 날 1 · PO 옛 3행


def test_tickers_without_an_event_in_the_window_leave_outside_rows_alone(done) -> None:
    """⑤ 창 안 사건이 없는 종목의 창 밖 행은 건드리지 않는다(쓴 범위 = 창 + 다시 맞춘 종목)."""
    _, got = done
    for t in (PAST, NONE):
        for d in (*OLD, BEFORE):
            c = _close(t, d)
            assert got[(t, d.isoformat())] == (c, float(c)), (t, d)


def test_145210_reverse_split_shape_matches_v3(done) -> None:
    """서버 실측(10-08): v3 사본 145210 2025-03-21 adj 11,260 = 10 × 종가 1,126, 옛 compat 5,300.3
    (= 1,126 × 누적계수 4.707 — 전방 조정). 새 기준은 v3 와 같다(계수 0.09999998 이라 0.002원 차)."""
    _, got = done
    close, adj = got[(EV, "2025-03-21")]
    assert close == 1_126
    assert adj == pytest.approx(11_260, rel=1e-6)
    assert adj != pytest.approx(1_126 * G_PRE, rel=1e-3)


def test_rerun_is_idempotent(roots, tmp_path: Path) -> None:
    """같은 판으로 다시 돌려도 값이 같다 — 창 밖 행도 옛 adj 가 아니라 종가 × 계수비로 다시 계산한다."""
    target = tmp_path / "quant.db"
    _seed_target(target)
    _run(roots, target)
    first = _adj(target)
    _run(roots, target)
    assert _adj(target) == first


def test_no_rebase_when_no_event_falls_in_the_window(roots, tmp_path: Path) -> None:
    """D = 07-22(창 07-08~07-22) — 사건이 창 밖 뒤라 아직 모른다. 창 밖 행은 그대로, 기록은 빈 종목."""
    target = tmp_path / "quant.db"
    _seed_target(target)
    before = _adj(target)
    res = _run(roots, target, date="20260722")
    after = _adj(target)
    for key, val in before.items():
        if key[1] < "2026-07-08":
            assert after[key] == val, key
    rebase = res.tables["daily_prices"].rebase
    assert rebase is not None and rebase["tickers"] == []
    # 창 안 EV 는 그날 기준(07-22 = 원종가)
    assert after[(EV, "2026-07-22")][1] == after[(EV, "2026-07-22")][0]
