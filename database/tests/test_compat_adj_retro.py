"""QL-E(T-18 · T-40 · T-41) — v3 소비자 `daily_prices` 를 v3 가 쌓던 모양으로.

v3 는 키움 ka10081 수정주가(`upd_stkpc_tp=1`)를 매일 그날 기준일로 받는다. 그 값은 KRX 기준가 사슬 K 다(T-40):
  ks(e) = 전일 종가 ÷ 기준가(다를 때), K(d) = Π ks,  adj_close(d) = 종가(d) × K(d) ÷ K(L)
그리고 매일 최근 5행을 그날 기준 수정값으로 덮는다(T-41): 행 d 의 시·고·저·종가 = 원값 × K(d)/K(c),
거래량 = 원값 × K(c)/K(d), c = min(d 뒤 4번째 행, L). 거래대금은 그대로다.

합성 판(D = 2026-07-31, 창 14일 = 07-17~07-31, equity 판 `m_`). 기준가는 기본 '전일 종가'(사건 없음)이고 사건 날만 다르다.
  EV    145210 — 07-24 10:1 병합(기준가 = 전일 종가 × 10). 서버 실측 모양 — v3 10-08 사본 2025-03-21 adj 11,260
  PO    035720 — 07-27 유상증자류 기준가 하향, 기준가가 호가 단위로 반올림됨(29,950 — 이론가 29,987.5, 범주 C)
  HALT  011930 — 07-20~07-23 거래정지(참고가 행), 07-24 재개 기준가 재평가 4,200(직전 5,000 — 범주 B, 사건 공시 없음)
  PAST  005930 — 창 앞(07-16) 사건만 — 창 안 단계 없음
  NONE  000660 — 사건 없음
`compat_v3_0807_kchain.csv` 는 로컬 equity 판(10-03 `price_daily`)과 로컬 v3 08-07 사본의 같은 행이다(145210 07-01~,
011930 04-27~ · 97행) — 리뷰어 재현(T-40 · 0행 차이)과 같은 대조를 compat 출력으로 한다.
"""
from __future__ import annotations

import csv
import datetime as dt
import itertools
import json
import sqlite3
from pathlib import Path

import pytest
from compat import CompatError, export
from compat.quant_db import SCHEMA_SQL_PATH
from conftest import _make_stage_tree
from test_compat_export import _price_row, _uni_row

D = "20260731"
BUILD = "m_20260731T231000_000000Z"
FROM_ISO = "2026-07-17"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "compat_v3_0807_kchain.csv"

EV, PO, HALT, PAST, NONE = "145210", "035720", "011930", "005930", "000660"
TICKERS = (EV, PO, HALT, PAST, NONE)
OLD = (dt.date(2025, 3, 20), dt.date(2025, 3, 21))       # 창 밖 옛 날짜(서버 실측 145210 행)
PRE = tuple(dt.date(2026, 7, 13) + dt.timedelta(days=i) for i in range(4))   # 07-13~07-16 창 앞
WINDOW = tuple(d for d in (dt.date(2026, 7, 17) + dt.timedelta(days=i) for i in range(15))
               if d.weekday() < 5)
DAYS = (*OLD, *PRE, *WINDOW)
E_EV = dt.date(2026, 7, 24)
E_PO = dt.date(2026, 7, 27)
E_HALT = dt.date(2026, 7, 24)
HALTED = tuple(d for d in WINDOW if dt.date(2026, 7, 20) <= d < E_HALT)
E_PAST = dt.date(2026, 7, 16)
PO_BASE = 29_950                                         # 이론가 29,987.5 → 호가 50원 단위
HALT_REF, HALT_BASE = 5_000, 4_200


def _close(t: str, d: dt.date) -> int:
    i = DAYS.index(d)
    if t == EV:
        return 1_126 if d in OLD else (1_000 + 10 * i if d >= E_EV else 100 + i)
    if t == PO:
        return 30_000 + 100 * i if d < E_PO else 30_000 + 50 * i
    if t == HALT:
        if d in HALTED or d == WINDOW[0]:              # 정지 전날 종가 = 참고가
            return HALT_REF
        return HALT_BASE + 10 * i if d >= E_HALT else 5_100 + i
    if t == PAST:
        return 70_000 + 100 * i if d < E_PAST else 35_000 + 100 * i
    return 50_000 + 100 * i


def _base(t: str, d: dt.date, prev: int | None) -> int | None:
    """그날 KRX 기준가 — 사건 날만 전일 종가와 다르다."""
    if prev is None:
        return _close(t, d)
    if t == EV and d == E_EV:
        return prev * 10
    if t == PO and d == E_PO:
        return PO_BASE
    if t == HALT and d == E_HALT:
        return HALT_BASE
    if t == PAST and d == E_PAST:
        return prev // 2
    return prev


def _price(t: str, d: dt.date, prev: int | None) -> dict:
    close = _close(t, d)
    row = _price_row(t, d, close, value=close * 1_000_000, mktcap=10**12)
    row.update(open=close - 10, high=close + 20, low=close - 30, volume_shr=1_000_000 + DAYS.index(d),
               base_price_krw=_base(t, d, prev))
    if t == HALT and d in HALTED:                       # 정지 참고가 행 — O/H/L 공란·거래량 0
        row.update(open=None, high=None, low=None, volume_shr=0, value_krw=0,
                   price_kind="reference")
    return row


def _prices() -> list[dict]:
    rows = []
    for t in TICKERS:
        prev = None
        for d in DAYS:
            rows.append(_price(t, d, prev))
            prev = _close(t, d)
    return rows


def k_ratio(t: str, d: dt.date, x: dt.date) -> float:
    """K(d) ÷ K(x)(x ≥ d) = 1 ÷ Π_{d < e ≤ x} ks(e) — 명세 쪽 계산(픽스처 기준가로)."""
    r = 1.0
    for prev_d, e in itertools.pairwise(DAYS):
        if d < e <= x:
            prev, base = _close(t, prev_d), _base(t, e, _close(t, prev_d))
            assert base is not None
            r /= prev / base
    return r


def _c(d: dt.date, last: dt.date) -> dt.date:
    """v3 가 d 를 마지막으로 덮은 날 — d 뒤 4번째 행, 없으면 마지막 행."""
    rows = [x for x in DAYS if d < x <= last]
    return rows[3] if len(rows) >= 4 else last


def _roots(base: Path, prices: list[dict] | None = None,
           sec_type: dict[str, str] | None = None) -> tuple[Path, Path]:
    prices = _prices() if prices is None else prices
    uni = [_uni_row(r["ticker"], r["date"], "KOSPI", (sec_type or {}).get(r["ticker"], "common"))
           for r in prices]
    eq = base / "eq"
    for table, rows in (("price_daily", prices), ("universe_daily", uni)):
        _make_stage_tree(eq, table, rows, build_id=BUILD)
    (base / "st" / "stage").mkdir(parents=True)
    return eq / "stage", base / "st" / "stage"


@pytest.fixture(scope="module")
def roots(tmp_path_factory) -> tuple[Path, Path]:
    return _roots(tmp_path_factory.mktemp("qle"))


NO_EQUITY_DAY = "2025-03-19"                            # 대상에만 있는 날(equity 행 없음)
SEED = 7                                                # 창 앞 행의 시·고·저 표식 — 이번 실행이 건드리면 바뀐다


def _bf(t: str, d: dt.date) -> int:
    """대상의 창 밖 종가 — v3 옛 백필 모양(원종가가 아닌 다른 기준 값, 여기선 절반). 창 밖 adj_close 를 이 값이 아니라
    equity 원종가로 계산하는지 본다(재리뷰 NIT)."""
    return _close(t, d) // 2


def _seed_target(target: Path) -> None:
    """창 밖 옛 행 — 앞선 반영본(시·고·저 표식, 종가는 백필 모양, adj = 그 종가)."""
    con = sqlite3.connect(str(target))
    try:
        con.executescript(SCHEMA_SQL_PATH.read_text(encoding="utf-8"))
        rows = [(t, d.isoformat(), SEED, SEED, SEED, _bf(t, d), 1, 1, float(_bf(t, d)))
                for t in TICKERS for d in (*OLD, *PRE)]
        rows.append((EV, NO_EQUITY_DAY, SEED, SEED, SEED, 1_100, 1, 1, 1_100.0))
        con.executemany("INSERT INTO daily_prices VALUES (?,?,?,?,?,?,?,?,?)", rows)
        con.commit()
    finally:
        con.close()


def _run(roots: tuple[Path, Path], target: Path, date: str = D, window_days: int = 14):
    return export(equity_root=roots[0], stage_root=roots[1], date=date, basis="morning",
                  target=target, tables=["daily_prices"], full=True, window_days=window_days)


def _all(target: Path) -> dict[tuple[str, str], tuple]:
    con = sqlite3.connect(str(target))
    try:
        return {(r[0], r[1]): r[2:] for r in con.execute(
            "SELECT stock_code, trade_date, open, high, low, close, volume, amount, adj_close "
            "FROM daily_prices")}
    finally:
        con.close()


@pytest.fixture
def done(roots, tmp_path: Path):
    target = tmp_path / "quant.db"
    _seed_target(target)
    return _run(roots, target), _all(target)


LAST = WINDOW[-1]


# ── 창 안 ────────────────────────────────────────────────────────────────────
def test_latest_row_is_raw_and_adj_equals_close(done) -> None:
    """① 마지막 행 = 원값(시·고·저·종가·거래량), adj_close = 종가(정확히 같다)."""
    _, got = done
    for t in TICKERS:
        o, h, low, c, v, _amt, adj = got[(t, LAST.isoformat())]
        assert (o, h, low, c, v) == (c - 10, c + 20, c - 30, _close(t, LAST),
                                     1_000_000 + DAYS.index(LAST)), t
        assert adj == c, t


def test_adj_close_is_the_krx_base_price_chain(done) -> None:
    """② adj_close = 원종가 × K(d)/K(L) — 병합 10배, 호가 반올림 기준가(29,950 ÷ 30,600 그대로, 범주 C), 정지 해제
    재평가(4,200 ÷ 5,000, 범주 B). equity 계수가 아니라 KRX 기준가로 센다."""
    _, got = done
    for t in TICKERS:
        for d in WINDOW:
            want = _close(t, d) * k_ratio(t, d, LAST)
            assert got[(t, d.isoformat())][6] == pytest.approx(want, rel=1e-12), (t, d)
    d = WINDOW[WINDOW.index(E_PO) - 1]
    assert got[(PO, d.isoformat())][6] == pytest.approx(PO_BASE, rel=1e-12)   # 종가 × 29,950/종가
    d = HALTED[-1]
    assert got[(HALT, d.isoformat())][6] == pytest.approx(HALT_BASE, rel=1e-12)   # 5,000 × 0.84
    d = WINDOW[WINDOW.index(E_EV) - 1]
    assert got[(EV, d.isoformat())][6] == pytest.approx(_close(EV, d) * 10, rel=1e-12)


def test_four_rows_before_an_event_are_overwritten_like_v3(done) -> None:
    """T-41 — 사건 직전 4행의 시·고·저·종가 = 원값 × K(d)/K(c), 거래량 = 원값 × K(c)/K(d), 거래대금은 원값.
    5번째 앞 행부터는 원값이다(그 행을 마지막으로 덮은 날이 사건 전)."""
    _, got = done
    for t, e in ((EV, E_EV), (PO, E_PO), (HALT, E_HALT)):
        i = WINDOW.index(e)
        for d in WINDOW[max(0, i - 6):i]:
            f = k_ratio(t, d, _c(d, LAST))
            src = _price(t, d, None)
            o, h, low, c, v, amt, _ = got[(t, d.isoformat())]
            fill = src["close"]
            assert (o, h, low, c) == tuple(round((x if x is not None else fill) * f) for x in (
                src["open"], src["high"], src["low"], src["close"])), (t, d)
            assert v == round(src["volume_shr"] / f), (t, d)
            assert amt == round(src["value_krw"] / 1e6), (t, d)
            if d < WINDOW[i - 4]:
                assert c == _close(t, d), (t, d)              # 덮인 날이 사건 전 — 원값
            else:
                assert c != _close(t, d), (t, d)


def test_briefing_return_on_the_event_day_is_the_real_return(done) -> None:
    """T-41 의 목적 — 07:00 브리핑 등락률 `(d.close − p.close) / p.close` 가 사건일에 가짜 급등락(병합 +900%)이
    아니라 KRX 수익률(종가 ÷ 기준가 − 1)이다."""
    _, got = done
    for t, e in ((EV, E_EV), (HALT, E_HALT), (PO, E_PO)):
        p = WINDOW[WINDOW.index(e) - 1]
        ret = got[(t, e.isoformat())][3] / got[(t, p.isoformat())][3] - 1
        base = _base(t, e, _close(t, p))
        assert base is not None
        assert ret == pytest.approx(_close(t, e) / base - 1, abs=1e-3), t
    assert got[(EV, E_EV.isoformat())][3] / _close(EV, WINDOW[WINDOW.index(E_EV) - 1]) > 9   # 원값이면 +900%


def test_tickers_without_a_step_in_the_window_are_raw(done) -> None:
    """③ 창 안에 기준가 단계가 없으면 창 앞 사건이 있어도 원값 그대로, adj_close = close."""
    _, got = done
    for t in (PAST, NONE):
        for d in WINDOW:
            o, h, low, c, v, _amt, adj = got[(t, d.isoformat())]
            assert (o, h, low, c, v) == (c - 10, c + 20, c - 30, _close(t, d),
                                         1_000_000 + DAYS.index(d)), (t, d)
            assert adj == c, (t, d)


def test_adj_returns_are_the_krx_returns(done) -> None:
    """④ adj_close 하루 수익률 = 종가 ÷ 기준가 − 1(기준가 = 전일 종가면 보통 수익률) — 비율 소비자 불변."""
    _, got = done
    for t in TICKERS:
        for p, d in itertools.pairwise(WINDOW):
            base = _base(t, d, _close(t, p))
            assert base is not None
            ours = got[(t, d.isoformat())][6] / got[(t, p.isoformat())][6]
            assert ours == pytest.approx(_close(t, d) / base, rel=1e-12), (t, d)


# ── 창 밖 ────────────────────────────────────────────────────────────────────
def test_step_tickers_get_outside_rows_rewritten_from_equity(done) -> None:
    """⑤ 창 안에 단계가 든 종목은 대상의 창 밖 옛 행을 다시 쓴다 — adj_close = equity 원종가 × K(d)/K(L)(대상의 백필
    모양 종가 × 비가 아니다, 재리뷰 NIT), 시·고·저·종가·거래량 = T-41 값(MINOR-1 자가 복구), 거래대금은 그대로.
    equity 행이 없는 날은 시·고·저·종가·거래량을 두고 adj_close 만 NULL(P1)."""
    res, got = done
    for t in (EV, PO, HALT):
        for d in (*OLD, *PRE):
            f = k_ratio(t, d, _c(d, LAST))
            src = _price(t, d, None)
            o, h, low, c, v, amt, adj = got[(t, d.isoformat())]
            assert (o, h, low, c) == tuple(round(src[k] * f) for k in ("open", "high", "low", "close")), (t, d)
            assert (v, amt) == (round(src["volume_shr"] / f), 1), (t, d)
            assert adj == pytest.approx(_close(t, d) * k_ratio(t, d, LAST), rel=1e-12), (t, d)
            assert adj != pytest.approx(_bf(t, d) * k_ratio(t, d, LAST), rel=1e-6), (t, d)
    assert got[(EV, NO_EQUITY_DAY)] == (SEED, SEED, SEED, 1_100, 1, 1, None)
    rebase = res.tables["daily_prices"].rebase
    assert rebase is not None
    assert rebase["before"] == FROM_ISO
    assert rebase["tickers"] == sorted([EV, PO, HALT])
    assert (rebase["n_rows"], rebase["n_null"]) == (19, 1)   # 3종목 × 옛 6행 + 빈 날 1


def test_tickers_without_a_step_in_the_window_leave_outside_rows_alone(done) -> None:
    """⑤ 창 앞 사건(PAST 07-16)은 이번 창의 단계가 아니다 — 창 밖 행은 그대로(그 사건 때 다시 맞췄다)."""
    _, got = done
    for t in (PAST, NONE):
        for d in (*OLD, *PRE):
            c = _bf(t, d)
            assert got[(t, d.isoformat())] == (SEED, SEED, SEED, c, 1, 1, float(c)), (t, d)


def test_145210_reverse_split_shape_matches_v3(done) -> None:
    """서버 실측(10-08): v3 사본 145210 2025-03-21 adj 11,260 = 10 × 종가 1,126(옛 compat 5,300.3 — 전방 조정)."""
    _, got = done
    assert got[(EV, "2025-03-21")][3] == 1_126
    assert got[(EV, "2025-03-21")][6] == pytest.approx(11_260, rel=1e-12)


def test_window_start_boundary(roots, tmp_path: Path) -> None:
    """창 첫 행 근처 — 사건(07-24)이 창 둘째 행이면(D = 07-27, 창 4일 = 07-23~) 창 첫 행은 덮어쓰기 값(×10)이고,
    창 앞 3행(07-20~22)도 사건 종목이라 T-41 값(×10)으로 다시 쓴다(MINOR-1) — 07-31 실행 값과 같다."""
    target = tmp_path / "quant.db"
    _seed_target(target)
    _run(roots, target)                                 # 07-31 기준 반영본(창 07-17~)
    _run(roots, target, date="20260727", window_days=4)
    got = _all(target)
    last = dt.date(2026, 7, 27)
    first = dt.date(2026, 7, 23)
    assert got[(EV, first.isoformat())][3] == round(_close(EV, first) * k_ratio(EV, first, last))
    for d in (dt.date(2026, 7, 20), dt.date(2026, 7, 21), dt.date(2026, 7, 22)):
        assert got[(EV, d.isoformat())][3] == _close(EV, d) * 10            # 07-31 실행 값과 같다
        assert got[(EV, d.isoformat())][6] == pytest.approx(_close(EV, d) * k_ratio(EV, d, last))


def test_missed_runs_self_heal_pre_window_rows(roots, tmp_path: Path) -> None:
    """MINOR-1 — 사건일(07-24) 반영을 놓쳐 사건 직전 행(07-20~23)이 원값으로 남은 채 창(07-24~)이 그 행들을 지나쳐도,
    사건이 창 안에 있는 동안의 다음 반영이 그 행들을 T-41 값(×10)으로 바로잡는다."""
    target = tmp_path / "quant.db"
    _run(roots, target, date="20260723", window_days=600)          # 사건 전날까지의 반영본(원값)
    pre = [d for d in WINDOW if dt.date(2026, 7, 20) <= d < E_EV]
    assert all(_all(target)[(EV, d.isoformat())][3] == _close(EV, d) for d in pre)
    _run(roots, target, date="20260728", window_days=4)            # 창 07-24~07-28
    got = _all(target)
    for d in pre:
        assert got[(EV, d.isoformat())][3] == _close(EV, d) * 10, d
        assert got[(EV, d.isoformat())][4] == round((1_000_000 + DAYS.index(d)) / 10), d


def _calendar(base: Path) -> Path:
    d = base / "calendar"
    d.mkdir(parents=True, exist_ok=True)
    days = (dt.date(2026, 1, 1) + dt.timedelta(days=i) for i in range(365))
    hol = [x.strftime("%Y%m%d") for x in days if x.weekday() >= 5]
    (d / "kis_holidays_2026.json").write_text(json.dumps({"year": 2026, "holidays": hol}),
                                              encoding="utf-8")
    return d


def test_in_place_window_needs_five_sessions(roots, tmp_path: Path) -> None:
    """MINOR-1 — 제자리 반영 창이 5거래일보다 좁으면(07-27~31 창 3일 = 07-28·29·30·31 4세션) 쓰기 전에 멈춘다(rc 2).
    5세션(창 4일 → 07-27~31)이면 지난다. 달력을 못 읽으면 영업일을 가정하지 않고 멈춘다."""
    cal = _calendar(tmp_path)
    target = tmp_path / "quant.db"
    kw = {"equity_root": roots[0], "stage_root": roots[1], "date": D, "basis": "morning",
          "target": target, "tables": ["daily_prices"], "full": True, "in_place": True}
    with pytest.raises(CompatError, match="5거래일"):
        export(window_days=3, calendar_dir=cal, **kw)
    assert not target.exists()
    assert export(window_days=4, calendar_dir=cal, **kw).status == "ok"
    with pytest.raises(CompatError, match="세션을 셀 수 없다"):
        export(window_days=14, calendar_dir=tmp_path / "nocal", **kw)


def test_rebase_tickers_are_v3_stocks_only(tmp_path: Path) -> None:
    """재리뷰 NIT — 창 안 단계가 있어도 v3 종목 집합 밖(우선주 등)은 다시 맞춤 목록에 들지 않는다."""
    prices = _prices() + [{**r, "ticker": "145215"} for r in _prices() if r["ticker"] == EV]
    roots = _roots(tmp_path / "pref", prices, sec_type={"145215": "preferred"})
    target = tmp_path / "quant.db"
    _seed_target(target)
    res = _run(roots, target)
    rebase = res.tables["daily_prices"].rebase
    assert rebase is not None and rebase["tickers"] == sorted([EV, PO, HALT])


def test_daily_runs_equal_one_full_run(roots, tmp_path: Path) -> None:
    """매일 증분(창 7일 ≥ 5세션)을 이어 돌린 결과 = 마지막 날 한 번의 전체 반영 — 덮어쓰기 행이 창을 떠날 때 최종값이다."""
    daily, full = tmp_path / "daily.db", tmp_path / "full.db"
    _run(roots, daily, date="20260716", window_days=600)
    for d in WINDOW:
        _run(roots, daily, date=d.strftime("%Y%m%d"), window_days=7)
    _run(roots, full, window_days=600)
    a, b = _all(daily), _all(full)
    assert a.keys() == b.keys()
    for key, row in b.items():
        assert a[key][:6] == row[:6], key
        assert a[key][6] == pytest.approx(row[6], rel=1e-12), key


def test_rerun_is_idempotent(roots, tmp_path: Path) -> None:
    target = tmp_path / "quant.db"
    _seed_target(target)
    _run(roots, target)
    first = _all(target)
    _run(roots, target)
    assert _all(target) == first


def test_no_rebase_when_no_step_falls_in_the_window(roots, tmp_path: Path) -> None:
    """D = 07-21(창 4일 = 07-17~) — 07-24·27 사건은 아직 없고 07-16 사건은 창 앞이다. 창 밖 행은 그대로,
    기록은 빈 종목 목록."""
    target = tmp_path / "quant.db"
    _seed_target(target)
    before = _all(target)
    res = _run(roots, target, date="20260721", window_days=4)
    after = _all(target)
    for key, val in before.items():
        if key[1] < FROM_ISO:
            assert after[key] == val, key
    rebase = res.tables["daily_prices"].rebase
    assert rebase is not None and rebase["tickers"] == []


# ── v3 08-07 사본 형식(실데이터) ─────────────────────────────────────────────
def _fixture_rows() -> list[dict]:
    with FIXTURE.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _num(x: str) -> int | None:
    return None if x == "" else int(x)


def test_matches_the_v3_0807_copy_rows(tmp_path: Path) -> None:
    """T-40 · T-41 — 로컬 v3 08-07 사본의 145210(07-24 병합·정지)·011930(05-15 병합·정지) 97행과 같다:
    시·고·저·종가·거래량·거래대금 정확히(±1), adj_close 1원 또는 0.15% 안(키움 원 단위 반올림)."""
    fx = _fixture_rows()
    prices = []
    for r in fx:
        d = dt.date.fromisoformat(r["date"])
        row = _price_row(r["ticker"], d, int(r["close"]), value=_num(r["value"]), mktcap=10**12)
        row.update(open=_num(r["open"]), high=_num(r["high"]), low=_num(r["low"]),
                   volume_shr=int(r["volume"]), base_price_krw=_num(r["base"]),
                   price_kind=r["price_kind"])
        prices.append(row)
    roots = _roots(tmp_path / "fx", prices)
    target = tmp_path / "quant.db"
    export(equity_root=roots[0], stage_root=roots[1], date="20260807", basis="morning",
           target=target, tables=["daily_prices"], full=True, window_days=110)
    got = _all(target)
    assert len(got) == len(fx) == 97
    for r in fx:
        o, h, low, c, v, amt, adj = got[(r["ticker"], r["date"])]
        want = tuple(int(r[k]) for k in ("v3_open", "v3_high", "v3_low", "v3_close", "v3_volume",
                                         "v3_amount"))
        assert all(abs(x - y) <= 1 for x, y in zip((o, h, low, c, v, amt), want, strict=True)), (
            r["ticker"], r["date"], (o, h, low, c, v, amt), want)
        v3_adj = float(r["v3_adj_close"])
        assert abs(adj - v3_adj) <= 1.0 or abs(adj / v3_adj - 1) <= 0.0015, (r, adj)
