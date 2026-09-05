"""S02 `security_span` — stage 절단본 위 실제 `build_table` 왕복 (DESIGN §4-1 · GATES §3 ③·§6).

손계산 기대값은 `stg_listing_daily`·`stg_etf_price_daily` 원자료를 직접 열어 확인한 값이다.
절단본 실측: 존재 (ticker, date) 41,066 = listing 36,972 + etf 4,094 (서로소, P8) · 구간 17 ·
재상장 2종(036220 · 101970) · 폐지 축 3종(000030 · 900050 · 900060).
"""
from __future__ import annotations

from datetime import date
from itertools import pairwise
from pathlib import Path

import duckdb
from equity import build, rules_s02
from equity.baseline import Baseline, load
from equity.gates import GateStatus
from equity.model import EquityTable

STAGE_SLICE = Path(__file__).parent / "fixtures" / "stage_slice"
SEED = load(Path(rules_s02.__file__).parent / "baseline_seed_s02.json")

N_EXIST_PAIRS = 41066               # (ticker, date) 합집합 — listing 36,972 + etf 4,094
N_SPANS = 17
BACKFILL_END = date(2026, 8, 20)

# 원자료에서 직접 읽은 구간표 — (ticker, span_seq) → (first_date, last_date, n_days, end_reason)
HAND: dict[tuple[str, int], tuple[date, date, int, str]] = {
    ("000030", 1): (date(2014, 11, 19), date(2019, 2, 12), 1037, "delisted"),
    ("0001A0", 1): (date(2026, 1, 30), BACKFILL_END, 135, "coverage_gap"),
    ("000660", 1): (date(2010, 1, 4), BACKFILL_END, 4094, "coverage_gap"),
    ("003540", 1): (date(2010, 1, 4), BACKFILL_END, 4094, "coverage_gap"),
    ("003545", 1): (date(2010, 1, 4), BACKFILL_END, 4094, "coverage_gap"),
    ("003547", 1): (date(2010, 1, 4), BACKFILL_END, 4094, "coverage_gap"),
    ("005930", 1): (date(2010, 1, 4), BACKFILL_END, 4094, "coverage_gap"),
    ("005935", 1): (date(2010, 1, 4), BACKFILL_END, 4094, "coverage_gap"),
    ("036220", 1): (date(2010, 1, 4), date(2016, 5, 4), 1570, "delisted"),
    ("036220", 2): (date(2024, 3, 13), BACKFILL_END, 593, "coverage_gap"),
    ("069500", 1): (date(2010, 1, 4), BACKFILL_END, 4094, "coverage_gap"),
    ("101970", 1): (date(2012, 7, 26), date(2015, 3, 16), 648, "delisted"),
    ("101970", 2): (date(2025, 3, 28), BACKFILL_END, 341, "coverage_gap"),
    ("161890", 1): (date(2012, 10, 19), BACKFILL_END, 3396, "coverage_gap"),
    ("247540", 1): (date(2019, 3, 5), BACKFILL_END, 1834, "coverage_gap"),
    ("900050", 1): (date(2010, 1, 4), date(2017, 9, 26), 1916, "delisted"),
    ("900060", 1): (date(2010, 1, 4), date(2013, 10, 10), 938, "delisted"),
}


def _seed_as(name: str) -> Baseline:
    """변종 테이블 이름으로 같은 상수를 다시 등재한다 — baseline 키가 `{table}.{metric}` 이라
    이름을 바꾼 부정 픽스처는 별도 등재 없이는 skip(no_baseline) 이 되어 술어가 안 돈다."""
    return Baseline({**SEED.data, name: SEED.table("security_span")})


def _build(tmp_path: Path, rule: EquityTable | None = None,
           baseline: Baseline | None = None) -> build.BuildResult:
    rule = rule or rules_s02.SECURITY_SPAN
    return build.build_table(rule, STAGE_SLICE, tmp_path / "equity",
                             baseline or _seed_as(rule.name), build_id="b_s02_span")


def _variant(tmp_path: Path, name: str, sql: str) -> EquityTable:
    p = tmp_path / f"{name}.sql"
    p.write_text(sql, encoding="utf-8")
    return EquityTable(**{**rules_s02.SECURITY_SPAN.__dict__, "name": name, "sql_path": p})


def _spans(out_dir: Path) -> dict[tuple[str, int], tuple[date, date, int, str]]:
    con = duckdb.connect()
    try:
        rows = con.execute(
            "SELECT ticker, span_seq, first_date, last_date, n_days, end_reason FROM "
            f"read_parquet('{out_dir / 'part0.parquet'}')").fetchall()
    finally:
        con.close()
    return {(str(t), int(s)): (f, ll, int(n), str(e)) for t, s, f, ll, n, e in rows}


def _gate(r: build.BuildResult, name: str):
    return next(g for g in r.gates if g.name == name)


# ── 정상 왕복 ────────────────────────────────────────────────────────────────

def test_절단본_빌드가_전_게이트를_통과한다(tmp_path: Path) -> None:
    r = _build(tmp_path)
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    assert [g.name for g in r.gates] == ["EG0", "EG7", "EG1", "EG2", "EG3", "EG3x", "EG16a",
                                         "EG4", "EG5a"]
    assert not [g for g in r.gates if g.status is GateStatus.FAIL]
    assert _gate(r, "EG2").detail == "dimension_table"
    assert r.n_rows == N_SPANS and r.n_reject == 0


def test_EG1_좌변은_행수가_아니라_n_days의_합이다(tmp_path: Path) -> None:
    """구간 하나가 통째로 사라져도 행수 등식은 안 보인다 — 그래서 좌변이 Σ n_days 다."""
    r = _build(tmp_path)
    assert r.ok
    eg1 = _gate(r, "EG1").metrics
    assert eg1["lhs"] == N_EXIST_PAIRS and eg1["rhs"] == N_EXIST_PAIRS
    assert eg1["lhs"] != r.n_rows


def test_구간표가_원자료_손계산과_전건_일치한다(tmp_path: Path) -> None:
    r = _build(tmp_path)
    assert r.ok and r.out_dir is not None
    assert _spans(r.out_dir) == HAND
    assert sum(v[2] for v in HAND.values()) == N_EXIST_PAIRS


def test_재상장_2종은_구간이_둘로_갈린다(tmp_path: Path) -> None:
    """036220 · 101970. 하나로 뭉치면 8~10년 공백이 상장 구간으로 둔갑한다(생존편향)."""
    r = _build(tmp_path)
    assert r.ok and r.out_dir is not None
    spans = _spans(r.out_dir)
    assert spans[("036220", 1)][1] == date(2016, 5, 4)
    assert spans[("036220", 2)][0] == date(2024, 3, 13)
    assert spans[("101970", 1)][1] == date(2015, 3, 16)
    assert spans[("101970", 2)][0] == date(2025, 3, 28)
    assert {t for (t, s) in spans if s >= 2} == {"036220", "101970"}
    assert _gate(r, "EG16a").metrics["n_multi_span_tickers"] == 2


def test_생존_종목은_coverage_gap_폐지_종목은_delisted(tmp_path: Path) -> None:
    """`last_date` 가 커버리지 끝이면 폐지가 아니다 — 생존 종목을 delisted 로 적으면 폐지."""
    r = _build(tmp_path)
    assert r.ok and r.out_dir is not None
    spans = _spans(r.out_dir)
    assert spans[("005930", 1)] == (date(2010, 1, 4), BACKFILL_END, 4094, "coverage_gap")
    assert spans[("000030", 1)][1:] == (date(2019, 2, 12), 1037, "delisted")
    ends = {v[3] for v in spans.values()}
    assert ends == {"coverage_gap", "delisted"}               # 'data_gap' 은 예약(P11)
    assert all(v[3] == "coverage_gap" for v in spans.values() if v[1] == BACKFILL_END)


def test_ETF는_listing에_없어도_구간을_갖는다(tmp_path: Path) -> None:
    """합집합을 빠뜨리면 069500 행 자체가 사라진다 = ETF 전량 유니버스 이탈."""
    r = _build(tmp_path)
    assert r.ok and r.out_dir is not None
    spans = _spans(r.out_dir)
    assert spans[("069500", 1)] == (date(2010, 1, 4), BACKFILL_END, 4094, "coverage_gap")
    assert ("069500", 2) not in spans


def test_구간은_비중첩이고_span_seq는_1부터_first_date순(tmp_path: Path) -> None:
    r = _build(tmp_path)
    assert r.ok and r.out_dir is not None
    spans = _spans(r.out_dir)
    by_ticker: dict[str, list[tuple[int, date, date]]] = {}
    for (t, s), (f, ll, _, _) in spans.items():
        by_ticker.setdefault(t, []).append((s, f, ll))
    for t, items in by_ticker.items():
        items.sort()
        assert [s for s, _, _ in items] == list(range(1, len(items) + 1)), t
        for (_, _, prev_last), (_, nxt_first, _) in pairwise(items):
            assert nxt_first > prev_last, t                   # 다음 first > 이전 last
    eg3x = _gate(r, "EG3x").metrics
    assert eg3x["n_overlapping_spans"] == 0
    assert eg3x["n_listing_dates"] == eg3x["n_calendar_days"] == 4094
    assert eg3x["n_dates_off_calendar"] == 0


# ── 부정 픽스처 (게이트가 fail 을 내야 통과) ─────────────────────────────────

def test_구간이_중첩되면_EG3x가_폐기한다(tmp_path: Path) -> None:
    """036220 첫 구간의 last_date 만 커버리지 끝까지 늘린다 — Σ n_days 는 그대로다.

    행수도 합계도 안 변하므로 EG1·EG3 는 통과한다. 이 결함을 잡는 것은 EG3x 뿐이다.
    """
    body = rules_s02.SECURITY_SPAN.sql_path.read_text(encoding="utf-8").strip().rstrip(";")
    rule = _variant(tmp_path, "span_overlap", f"""
        WITH base AS (SELECT * FROM ({body}))
        SELECT base.ticker, base.span_seq, base.first_date,
               CASE WHEN base.ticker = '036220' AND base.span_seq = 1
                    THEN (SELECT max(b.last_date) FROM base b)
                    ELSE base.last_date END AS last_date,
               base.n_days, base.end_reason, base.reject_reason
        FROM base""")
    r = _build(tmp_path, rule)
    assert r.status is build.BuildStatus.GATE_FAILED
    assert _gate(r, "EG1").status is GateStatus.PASS
    assert _gate(r, "EG3").status is GateStatus.PASS
    eg3x = _gate(r, "EG3x")
    assert eg3x.status is GateStatus.FAIL and eg3x.metrics["n_overlapping_spans"] == 1
    assert "overlapping spans" in eg3x.detail
    assert _gate(r, "EG16a").detail == "upstream_failed"


def test_end_reason_어휘_밖이면_EG3x가_폐기한다(tmp_path: Path) -> None:
    body = rules_s02.SECURITY_SPAN.sql_path.read_text(encoding="utf-8").strip().rstrip(";")
    rule = _variant(tmp_path, "span_bad_reason", f"""
        WITH base AS (SELECT * FROM ({body}))
        SELECT base.ticker, base.span_seq, base.first_date, base.last_date, base.n_days,
               CASE WHEN base.end_reason = 'delisted' THEN 'DELISTED' ELSE base.end_reason END
                    AS end_reason,
               base.reject_reason
        FROM base""")
    r = _build(tmp_path, rule)
    assert r.status is build.BuildStatus.GATE_FAILED
    eg3x = _gate(r, "EG3x")
    assert eg3x.status is GateStatus.FAIL
    assert eg3x.metrics["n_end_reason_outside_vocab"] == 5    # 손계산: delisted 구간 5개


def test_가짜_재상장이_늘면_EG16a가_폐기한다(tmp_path: Path) -> None:
    """수집 결손 하루가 구간을 쪼개는 사고. baseline 을 넘어선 재상장은 폐기 사유다."""
    bad = Baseline({**SEED.data, "security_span": {"respan_count": 3}})
    r = _build(tmp_path, baseline=bad)
    assert r.status is build.BuildStatus.GATE_FAILED
    eg16a = _gate(r, "EG16a")
    assert eg16a.status is GateStatus.FAIL
    assert eg16a.metrics == {"n_multi_span_tickers": 2, "respan_count": 3, "delta": -1}


def test_baseline_없으면_EG16a만_skip하고_EG3x는_돈다(tmp_path: Path) -> None:
    """상수 없는 술어를 상수 있는 술어와 한 게이트에 묶으면 안 되는 이유 (GATES §0-3)."""
    r = _build(tmp_path, baseline=Baseline({}))
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    assert _gate(r, "EG3x").status is GateStatus.PASS
    eg16a = _gate(r, "EG16a")
    assert eg16a.status is GateStatus.SKIP and eg16a.detail == "no_baseline"
    assert eg16a.metrics["missing_metric"] == "security_span.respan_count"
