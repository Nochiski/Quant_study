"""fi 엑셀 전용 `fi_fin_prelim` — 잠정실적 고르기 · 단위 검산(N-64 PR ②-1, 계획 9-3 · PG-1~4 · PG-8).

고르기 규칙은 기준일 D 를 바꿔 가며 봐야 해서(PG-4 시점 재현) `fin_prelim_sql` 을 작은 입력 표 위에서 직접 돈다.
판 전체 빌드 · FG6 · 판 기록은 끝의 통합 시험이 `test_factor_inputs.make_roots` 위에서 본다.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest
import test_factor_inputs as tf
from conftest import _make_stage_tree, allow_skips
from equity.rules_s27 import FIN_PRELIM
from factor_inputs import build, queries
from model.contracts import FI_FIN_SUMMARY
from stage import manifest

T1, T2 = "000001", "000002"
EOK = 100_000_000


def _p(rno: str, period_to: str, *, ticker: str = T1, fs_div: str = "CFS", rev: float | None = 120.0,
       op: float | None = 12.0, ni: float | None = 9.0, rev_py: float | None = 100.0,
       op_py: float | None = 10.0, py_ok: bool = True, status: str = "ok",
       avail: str | None = None) -> dict[str, object]:
    """잠정 판 한 행 — 값은 억원으로 받아 원으로 싣는다(equity fin_prelim 단위)."""
    end = dt.date.fromisoformat(period_to)
    start = dt.date(end.year, end.month - 2, 1)
    py_to = end.replace(year=end.year - 1) if py_ok else end      # py_ok=False — 전년동기 기간을 당기로 적은 서식
    rcept = dt.date(int(rno[:4]), int(rno[4:6]), int(rno[6:8]))

    def won(x: float | None) -> Decimal | None:
        return None if x is None else Decimal(int(round(x * EOK)))

    return {"rcept_no": rno, "corp_code": "C" + ticker, "ticker": ticker, "rcept_dt": rcept,
            "is_correction": False, "fs_div": fs_div, "period_from": start, "period_to": end,
            "py_from": start.replace(year=start.year - 1), "py_to": py_to, "unit_raw": "억원",
            "revenue_krw": won(rev), "op_profit_krw": won(op), "pretax_krw": None, "net_income_krw": won(ni),
            "ni_owner_krw": None, "revenue_py_krw": won(rev_py), "op_profit_py_krw": won(op_py),
            "net_income_py_krw": None, "prelim_status": status, "parse_error": None,
            "available_date": dt.date.fromisoformat(avail) if avail else rcept, "available_basis": "derived"}


def _q(period: str, *, ticker: str = T1, rev: float | None = 100.0, op: float | None = 10.0,
       basis: str = "DART:CFS") -> dict[str, object]:
    """이 판 fi_fin_summary 의 확정 분기 행(억원)."""
    return {"ticker": ticker, "period": period, "period_type": "quarter", "revenue": rev, "op": op,
            "ni": None, "fs_basis": basis}


def _run(d: str, prelim: list[dict[str, object]], conf: list[dict[str, object]],
         universe: tuple[str, ...] = (T1, T2)) -> dict[tuple[str, str], dict[str, object]]:
    con = duckdb.connect()
    try:
        con.execute("CREATE TABLE _fi_universe (ticker VARCHAR)")
        con.executemany("INSERT INTO _fi_universe VALUES (?)", [(t,) for t in universe])
        con.execute("CREATE TABLE security (ticker VARCHAR, corp_code VARCHAR)")
        con.executemany("INSERT INTO security VALUES (?, ?)", [(t, "C" + t) for t in (T1, T2, "000003")])
        cols = FIN_PRELIM.columns
        con.execute("CREATE TABLE fin_prelim (" + ", ".join(f"{c} {t}" for c, t in cols.items()) + ")")
        for r in prelim:
            con.execute(f"INSERT INTO fin_prelim VALUES ({', '.join('?' * len(cols))})", [r[c] for c in cols])
        con.execute("CREATE TABLE _fi_fin_summary (" + ", ".join(
            f'"{c.name}" {c.dtype}' for c in FI_FIN_SUMMARY.columns) + ")")
        for r in conf:
            names = list(r)
            con.execute(f"INSERT INTO _fi_fin_summary ({', '.join(names)}) VALUES ({', '.join('?' * len(names))})",
                        list(r.values()))
        p = queries.Params(d=d, fy=d[:4] + "12", price_from="2025-01-01", flow_from="2026-01-01",
                           grace_days=5, credit_lag=1)
        con.execute(queries.fin_prelim_sql(p))
        rel = con.execute("SELECT * FROM _fi_fin_prelim")
        names = [x[0] for x in rel.description]
        return {(str(r[0]), str(r[1])): dict(zip(names, r, strict=True)) for r in rel.fetchall()}
    finally:
        con.close()


def test_확정_분기보다_뒤_기말만_낸다() -> None:
    """확정 > 잠정 — 반기보고서가 이미 있는 2Q 잠정은 버리고 그 뒤 3Q 잠정만 P 로 낸다."""
    got = _run("2026-11-03", [_p("20260720800001", "2026-06-30"), _p("20261025800001", "2026-09-30")],
               [_q("2026/03"), _q("2026/06")])
    assert list(got) == [(T1, "2026/09")]
    r = got[(T1, "2026/09")]
    assert (r["revenue"], r["op"], r["ni"], r["fs_basis"], r["rcept_no"]) == (120.0, 12.0, 9.0, "PRELIM:CFS",
                                                                             "20261025800001")


def test_값이_빈_확정_행은_확정으로_세지_않는다() -> None:
    """4Q 파생값이 아직 없는 사업보고서 행처럼 값이 다 빈 분기 행은 확정이 아니다 — 잠정 칸이 빈칸이 되지 않게."""
    got = _run("2027-02-10", [_p("20270205800001", "2026-12-31")],
               [_q("2026/09"), _q("2026/12", rev=None, op=None)])
    assert list(got) == [(T1, "2026/12")]


@pytest.mark.parametrize(("d", "expect"), [
    ("2026-10-24", None),                      # 잠정 공시 전날 — 빈칸
    ("2026-10-25", ("20261025800001", 120.0)),  # 공시일 — 원본
    ("2026-10-30", ("20261030800001", 125.0)),  # 정정일 — 늦은 접수가 이긴다(Q4)
    ("2026-11-14", None),                      # 분기보고서 공개일 — 확정이 이긴다
])
def test_기준일을_바꿔도_미래_정보가_안_보인다(d: str, expect: tuple[str, float] | None) -> None:
    """PG-4 시점 재현 — 정정은 그날부터, 확정은 공개일부터. available_date > D 인 값은 한 번도 안 보인다."""
    prelim = [_p("20261025800001", "2026-09-30"),
              {**_p("20261030800001", "2026-09-30", rev=125.0), "is_correction": True}]
    conf = [_q("2026/06")] + ([_q("2026/09", rev=126.0)] if d >= "2026-11-14" else [])
    got = _run(d, prelim, conf)
    row = got.get((T1, "2026/09"))
    assert (None if row is None else (row["rcept_no"], row["revenue"])) == expect
    assert all(str(r["available_date"]) <= d for r in got.values())


def test_연결이_있으면_연결_확정이_연결이면_별도_잠정은_버린다() -> None:
    """Q2 — 같은 기말 연결 · 별도 둘이면 연결. 별도만 있고 최신 확정 기준이 연결이면 버린다(기준을 섞지 않는다)."""
    both = _run("2026-11-03", [_p("20261025800001", "2026-09-30"),
                               _p("20261025800002", "2026-09-30", fs_div="OFS", rev=50.0)], [_q("2026/06")])
    assert both[(T1, "2026/09")]["fs_basis"] == "PRELIM:CFS"
    ofs = [_p("20261025800002", "2026-09-30", fs_div="OFS", ticker=T2)]
    assert _run("2026-11-03", ofs, [_q("2026/06", ticker=T2, basis="IFRS(연결)")]) == {}
    assert list(_run("2026-11-03", ofs, [_q("2026/06", ticker=T2, basis="IFRS(별도)")])) == [(T2, "2026/09")]
    assert list(_run("2026-11-03", ofs, [])) == [(T2, "2026/09")]          # 확정이 없는 종목


@pytest.mark.parametrize(("prelim", "conf", "check"), [
    # 전년동기 매출 100억 = 확정 2025/09 100억 → 맞음
    (_p("20261025800001", "2026-09-30"), [_q("2025/09"), _q("2026/06")], "ok"),
    # 단위를 100배 잘못 적은 서식(전년동기 1만억) → 엑셀에 안 냄
    (_p("20261025800001", "2026-09-30", rev=12_000.0, rev_py=10_000.0), [_q("2025/09"), _q("2026/06")],
     "withheld"),
    # 매출이 없으면 같은 부호의 영업이익으로 본다
    (_p("20261025800001", "2026-09-30", rev=None, rev_py=None), [_q("2025/09"), _q("2026/06")], "ok"),
    # 동아에스티 꼴 — 전년동기 기간을 당기로 적었다: 기준 없음으로 내려 직전 확정 분기 대비만 본다
    (_p("20261025800001", "2026-09-30", py_ok=False), [_q("2025/09"), _q("2026/06")], "no_ref"),
    # 기준 없음 · 직전 확정 분기의 100배 → 엑셀에 안 냄
    (_p("20261025800001", "2026-09-30", rev=10_000.0, rev_py=None, op_py=None), [_q("2026/06")], "withheld"),
    # 비교할 확정이 아예 없다
    (_p("20261025800001", "2026-09-30", rev_py=None, op_py=None), [], "no_ref"),
    # 기준 분기 매출이 작으면(억원 반올림이 비율을 흔든다) 그 갈래를 건너뛴다 — 직전 분기 대비로
    (_p("20261025800001", "2026-09-30", rev=3.0, rev_py=9.0, op=None, op_py=None),
     [_q("2025/09", rev=2.0, op=None), _q("2026/06", rev=2.5, op=None)], "no_ref"),
])
def test_단위_검산_pg8(prelim: dict[str, object], conf: list[dict[str, object]], check: str) -> None:
    got = _run("2026-11-03", [prelim], conf)
    assert got[(T1, "2026/09")]["unit_check"] == check


def test_고르지_않는_행() -> None:
    """상태가 ok 가 아님 · 분기 말 달이 아님 · 유니버스 밖 · 공개일이 dart_asof 뒤는 내지 않는다."""
    got = _run("2026-11-03", [
        _p("20261025800001", "2026-09-30", status="parse_failed"),
        _p("20260905800001", "2026-08-31"),                         # 3개월이지만 기말이 8월
        _p("20261025800003", "2026-09-30", ticker="000003"),        # 유니버스 밖
        _p("20261025800004", "2026-09-30", ticker=T2, avail="2026-11-04"),
    ], [])
    assert got == {}


def test_3월_결산은_기말일_축이다() -> None:
    """PG-3 — 3월 결산의 회계 3Q(10~12월) 잠정은 확정 분기 행과 같은 기말일 축(2025/12)에 든다."""
    got = _run("2026-02-10", [_p("20260204800001", "2025-12-31")], [_q("2025/09", basis="DART:OFS")])
    assert list(got) == [(T1, "2025/12")]


def test_확정이_없으면_최근_5기까지만() -> None:
    # (기말, 접수일) — 접수는 기말 다음 달
    ends = (("2025-06-30", "20250728"), ("2025-09-30", "20251028"), ("2025-12-31", "20260128"),
            ("2026-03-31", "20260428"), ("2026-06-30", "20260728"), ("2026-09-30", "20261028"))
    got = _run("2026-11-03", [_p(f"{r}800001", e) for e, r in ends], [])
    assert sorted(p for _, p in got) == ["2025/09", "2025/12", "2026/03", "2026/06", "2026/09"]


# ── 판 전체(통합) — fin_prelim 판이 있는 날 · FG6 · 판 기록 ──────────────────────────
def test_판에_잠정_표가_서고_엔진_입력은_그대로(tmp_path: Path) -> None:
    """E(확정 분기 없음)의 2Q 잠정은 P 로, A(반기보고서 있음)의 2Q 잠정은 버린다. fi_fin_summary 해시는 잠정 판이
    없는 날과 같다(엔진 입력 불변 — 점수 밖, N-64 Q5)."""
    eq, st = tf.make_roots(tmp_path / "src")
    skip_fg4 = ("factor_inputs", "FG4", "no_fixtures", "합성 트리에는 운영 골든(fixtures/golden.json) 종목이 없다")
    with allow_skips(skip_fg4):
        plain = build(tf.D_S, "morning", tmp_path / "plain", st, eq, grace_days=5, min_eligible=5,
                      golden_path=None)
    assert plain.ok
    rows = [{**_p("20260725800001", "2026-06-30", ticker=tf.E), "corp_code": tf.corp(tf.E)},
            {**_p("20260720800002", "2026-06-30", ticker=tf.A), "corp_code": tf.corp(tf.A)}]
    _make_stage_tree(tmp_path / "src" / "eq", "fin_prelim", rows, build_id=tf.EQ_BUILD,
                     types=dict(FIN_PRELIM.columns))
    out = tmp_path / "fi"
    with allow_skips(skip_fg4):
        res = build(tf.D_S, "morning", out, st, eq, grace_days=5, min_eligible=5, golden_path=None)
    assert res.ok, [(g.name, g.status.value, g.detail) for g in res.gates if g.status.value != "pass"]
    fg6 = next(g for g in res.gates if g.name == "FG6")
    assert (fg6.metrics["new_quarter"], fg6.metrics["n_tickers_new_quarter"]) == (None, 0)   # A 는 이미 2026/06 확정
    assert tf.q(out, "fi_fin_prelim", "SELECT ticker, period, revenue, fs_basis FROM t") == [
        (tf.E, "2026/06", 120.0, "PRELIM:CFS")]

    def h(o: Path) -> str:
        m = manifest.load(o / "fi_fin_summary" / "MANIFEST.json")
        return next(b.content_hash for b in m.builds if b.build_id == m.current_build)
    assert h(out) == h(tmp_path / "plain")
