"""S02 `trading_calendar` — stage 절단본 위 실제 `build_table` 왕복 (DESIGN §4-1 · GATES §6 EG17).

손계산 기대값은 전부 `tests/fixtures/stage_slice/stg_index_daily` 원자료를 직접 열어 확인한
값이다(산출 SQL 로 얻은 값이 아니다). 절단본 캘린더 = 4,094 거래일 · 2010-01-04 ~ 2026-08-20.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import duckdb
from equity import build, rules_s02
from equity.baseline import Baseline, load
from equity.gates import GateStatus
from equity.model import EquityTable

STAGE_SLICE = Path(__file__).parent / "fixtures" / "stage_slice"
SEED = load(Path(rules_s02.__file__).parent / "baseline_seed_s02.json")

N_TRADING_DAYS = 4094               # 절단본 stg_index_daily distinct date (원자료 실측)
FIRST_TD = date(2010, 1, 4)
LAST_TD = date(2026, 8, 20)


def _seed_as(name: str) -> Baseline:
    """변종 테이블 이름으로 같은 상수를 다시 등재한다 — baseline 키가 `{table}.{metric}` 이라
    이름을 바꾼 부정 픽스처는 별도 등재 없이는 skip(no_baseline) 이 되어 술어가 안 돈다."""
    return Baseline({**SEED.data, name: SEED.table("trading_calendar")})


def _build(tmp_path: Path, rule: EquityTable | None = None,
           baseline: Baseline | None = None) -> build.BuildResult:
    rule = rule or rules_s02.TRADING_CALENDAR
    return build.build_table(rule, STAGE_SLICE, tmp_path / "equity",
                             baseline or _seed_as(rule.name), build_id="b_s02_cal")


def _variant(tmp_path: Path, name: str, sql: str) -> EquityTable:
    """산출 SQL 만 바꾼 부정 픽스처용 변종. 이름을 바꿔야 정본 MANIFEST 를 건드리지 않는다."""
    p = tmp_path / f"{name}.sql"
    p.write_text(sql, encoding="utf-8")
    return EquityTable(**{**rules_s02.TRADING_CALENDAR.__dict__, "name": name, "sql_path": p})


def _rows(out_dir: Path) -> list[tuple[object, ...]]:
    con = duckdb.connect()
    try:
        return con.execute(f"SELECT date, prev_td, next_td FROM "
                           f"read_parquet('{out_dir / 'part0.parquet'}') ORDER BY date").fetchall()
    finally:
        con.close()


def _gate(r: build.BuildResult, name: str):
    return next(g for g in r.gates if g.name == name)


# ── 정상 왕복 ────────────────────────────────────────────────────────────────

def test_절단본_빌드가_전_게이트를_통과한다(tmp_path: Path) -> None:
    r = _build(tmp_path)
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    assert [g.name for g in r.gates] == ["EG0", "EG7", "EG1", "EG2", "EG3", "EG17", "EG4", "EG5a"]
    assert not [g for g in r.gates if g.status is GateStatus.FAIL]
    assert _gate(r, "EG2").detail == "dimension_table"        # 캘린더는 차원표 — PIT 축 없음
    assert r.n_rows == N_TRADING_DAYS and r.n_reject == 0
    assert r.out_dir is not None and (r.out_dir / "part0.parquet").exists()


def test_행수는_stg_index_daily_distinct_date다(tmp_path: Path) -> None:
    r = _build(tmp_path)
    assert r.ok
    con = duckdb.connect()
    try:
        src = con.execute(
            "SELECT count(DISTINCT date) FROM read_parquet("
            f"'{STAGE_SLICE / 'stg_index_daily' / 'v=*' / 'year=*' / '*.parquet'}')").fetchone()
        assert src is not None and int(str(src[0])) == N_TRADING_DAYS
    finally:
        con.close()
    eg1 = _gate(r, "EG1")
    assert eg1.metrics["lhs"] == N_TRADING_DAYS and eg1.metrics["rhs"] == N_TRADING_DAYS


def test_첫날_prev는_NULL_마지막날_next는_NULL(tmp_path: Path) -> None:
    r = _build(tmp_path)
    assert r.ok and r.out_dir is not None
    rows = _rows(r.out_dir)
    assert rows[0] == (FIRST_TD, None, date(2010, 1, 5))      # 손계산: 첫 거래일 · 다음 1/5
    assert rows[-1] == (LAST_TD, date(2026, 8, 19), None)     # 손계산: 마지막 거래일
    assert sum(1 for _, prev, _ in rows if prev is None) == 1
    assert sum(1 for _, _, nxt in rows if nxt is None) == 1


def test_금요일의_다음_거래일은_월요일(tmp_path: Path) -> None:
    """2010-01-08(금) → 2010-01-11(월). 주말 2일은 캘린더에 없다."""
    r = _build(tmp_path)
    assert r.ok and r.out_dir is not None
    by_date = {d: (p, n) for d, p, n in _rows(r.out_dir)}
    assert by_date[date(2010, 1, 8)][1] == date(2010, 1, 11)
    assert by_date[date(2010, 1, 11)][0] == date(2010, 1, 8)


def test_연휴_직후_prev는_직전_거래일(tmp_path: Path) -> None:
    """절단본 최장 공백 — 2017-09-29 → 2017-10-10(추석 + 임시공휴일 10-02). FX-1-009."""
    r = _build(tmp_path)
    assert r.ok and r.out_dir is not None
    by_date = {d: (p, n) for d, p, n in _rows(r.out_dir)}
    assert by_date[date(2017, 10, 10)][0] == date(2017, 9, 29)
    assert by_date[date(2017, 9, 29)][1] == date(2017, 10, 10)


def test_주말이_없고_날짜가_단조증가한다(tmp_path: Path) -> None:
    r = _build(tmp_path)
    assert r.ok and r.out_dir is not None
    dates = [d for d, _, _ in _rows(r.out_dir)]
    assert [d for d in dates if d.weekday() >= 5] == []       # 5=토 6=일
    assert dates == sorted(set(dates)) and len(dates) == N_TRADING_DAYS
    eg17 = _gate(r, "EG17").metrics
    assert eg17["n_weekend"] == 0 and eg17["n_duplicate_date"] == 0
    assert eg17["n_next_td_broken"] == 0 and eg17["n_prev_td_broken"] == 0
    assert eg17["min_date"] == "2010-01-04" and eg17["max_date"] == "2026-08-20"


# ── 부정 픽스처 (게이트가 fail 을 내야 통과) ─────────────────────────────────

def test_중복_날짜를_주입하면_EG3가_폐기한다(tmp_path: Path) -> None:
    """마지막 날짜를 첫 날짜로 접는다 — 행수는 그대로라 EG1 은 통과하고 PK 만 깨진다.

    행수 등식만 보면 못 잡는 결함이라는 것이 이 부정 픽스처의 요지다.
    """
    body = rules_s02.TRADING_CALENDAR.sql_path.read_text(encoding="utf-8").strip().rstrip(";")
    rule = _variant(tmp_path, "cal_dup_date", f"""
        WITH base AS (SELECT * FROM ({body}))
        SELECT CASE WHEN base.date = (SELECT max(b.date) FROM base b)
                    THEN (SELECT min(b.date) FROM base b) ELSE base.date END AS date,
               base.prev_td, base.next_td, base.reject_reason
        FROM base""")
    r = _build(tmp_path, rule)
    assert r.status is build.BuildStatus.GATE_FAILED
    assert _gate(r, "EG1").status is GateStatus.PASS          # 행수는 4,094 그대로
    eg3 = _gate(r, "EG3")
    assert eg3.status is GateStatus.FAIL and eg3.metrics["n_duplicate_keys"] == 1
    assert _gate(r, "EG17").detail == "upstream_failed"
    assert not (tmp_path / "equity" / "cal_dup_date").exists()


def test_next_td_체인이_밀리면_EG17이_폐기한다(tmp_path: Path) -> None:
    """`lead(date, 2)` — 날짜·행수·주말은 전부 정상이고 체인만 하루 밀린 상태."""
    rule = _variant(tmp_path, "cal_shifted_chain", """
        SELECT d.date AS date,
               lag(d.date) OVER (ORDER BY d.date) AS prev_td,
               lead(d.date, 2) OVER (ORDER BY d.date) AS next_td,
               NULL::VARCHAR AS reject_reason
        FROM (SELECT DISTINCT date FROM stg_index_daily) d""")
    r = _build(tmp_path, rule)
    assert r.status is build.BuildStatus.GATE_FAILED
    assert _gate(r, "EG3").status is GateStatus.PASS          # PK 는 멀쩡하다
    eg17 = _gate(r, "EG17")
    assert eg17.status is GateStatus.FAIL
    assert eg17.metrics["n_next_td_broken"] == N_TRADING_DAYS - 1
    assert eg17.metrics["n_prev_td_broken"] == 0
    assert "next_td chain broken" in eg17.detail


def test_주말이_섞이면_EG17이_폐기한다(tmp_path: Path) -> None:
    rule = _variant(tmp_path, "cal_weekend", """
        SELECT d.date AS date,
               lag(d.date) OVER (ORDER BY d.date) AS prev_td,
               lead(d.date) OVER (ORDER BY d.date) AS next_td,
               NULL::VARCHAR AS reject_reason
        FROM (SELECT DISTINCT CAST(date + INTERVAL 1 DAY AS DATE) AS date
              FROM stg_index_daily) d""")
    r = _build(tmp_path, rule)
    assert r.status is build.BuildStatus.GATE_FAILED
    eg17 = _gate(r, "EG17")
    assert eg17.status is GateStatus.FAIL and eg17.metrics["n_weekend"] > 0


# ── baseline 규약 ────────────────────────────────────────────────────────────

def test_baseline_없으면_EG17은_skip_no_baseline(tmp_path: Path) -> None:
    """첫 빌드 규약 — 상수 미등재는 폐기가 아니라 skip. 다만 통과로 세지 않는다(GATES §0-2)."""
    r = _build(tmp_path, baseline=Baseline({}))
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    eg17 = _gate(r, "EG17")
    assert eg17.status is GateStatus.SKIP and eg17.detail == "no_baseline"
    assert eg17.metrics["missing_metric"] == "trading_calendar.calendar_start"


def test_하한이_baseline과_다르면_EG17_폐기(tmp_path: Path) -> None:
    """원천이 앞에서 잘리는 사고 — 행수 등식은 새 원천에 맞춰 같이 줄어 통과한다."""
    tc = {**SEED.table("trading_calendar"), "calendar_start": "2010-01-05"}
    r = _build(tmp_path, baseline=Baseline({**SEED.data, "trading_calendar": tc}))
    assert r.status is build.BuildStatus.GATE_FAILED
    eg17 = _gate(r, "EG17")
    assert eg17.status is GateStatus.FAIL and "min(date) expected 2010-01-05" in eg17.detail


# ── EG17 상한 유도 (규칙 e1.15.0 · 플랜 v2 §4 B.2) ───────────────────────────

def test_상한은_baseline_상수가_아니라_stage에서_유도한다(tmp_path: Path) -> None:
    """`backfill_end` 를 아예 안 등재해도(또는 틀린 값을 등재해도) 판정이 선다 —
    거래일이 하루 늘 때마다 사람이 상수를 올려야 하던 고장(v1 §7 D)을 없앤 것이 이 변경이다."""
    tc = {k: v for k, v in SEED.table("trading_calendar").items() if k != "backfill_end"}
    r = _build(tmp_path, baseline=Baseline({**SEED.data, "trading_calendar": tc}))
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    eg17 = _gate(r, "EG17")
    assert eg17.status is GateStatus.PASS
    assert eg17.metrics["max_date"] == LAST_TD.isoformat()
    assert eg17.metrics["stage_max_date"] == LAST_TD.isoformat()
    assert eg17.metrics["backfill_end"] == LAST_TD.isoformat()
    assert eg17.metrics["backfill_end_basis"] == "derived:max(stg_price_daily.date)"
    assert eg17.metrics["previous_max_date"] is None            # 첫 빌드 — 퇴행 판정축 없음
    assert "stg_price_daily" in rules_s02.TRADING_CALENDAR.inputs


def _short_calendar(tmp_path: Path, name: str) -> EquityTable:
    """마지막 거래일 하루가 빠진 캘린더. EG1 우변도 같이 줄여 **행수 등식은 서게** 만든다 —
    원천이 조용히 짧아지는 사고는 행수로는 안 보인다는 것이 EG17 상한 술어의 존재 이유다."""
    cut = f"WHERE date < DATE '{LAST_TD.isoformat()}'"
    p = tmp_path / f"{name}.sql"
    p.write_text(f"""
        SELECT d.date AS date,
               lag(d.date) OVER (ORDER BY d.date) AS prev_td,
               lead(d.date) OVER (ORDER BY d.date) AS next_td,
               NULL::VARCHAR AS reject_reason
        FROM (SELECT DISTINCT date FROM stg_index_daily {cut}) d""", encoding="utf-8")
    return EquityTable(**{**rules_s02.TRADING_CALENDAR.__dict__, "name": name, "sql_path": p,
                          "eg1_rhs_sql": "SELECT count(DISTINCT date) FROM stg_index_daily "
                                         + cut})


def test_캘린더_상한이_stage_가격_상한과_다르면_EG17_폐기(tmp_path: Path) -> None:
    """캘린더(KRX 지수)만 하루 짧은 판 — 격자와 가격 원장이 어긋난다."""
    r = _build(tmp_path, rule=_short_calendar(tmp_path, "cal_short"))
    assert r.status is build.BuildStatus.GATE_FAILED
    assert _gate(r, "EG1").status is GateStatus.PASS          # 행수 등식은 선다
    eg17 = _gate(r, "EG17")
    assert eg17.status is GateStatus.FAIL
    assert f"!= stage max(stg_price_daily.date) {LAST_TD.isoformat()}" in eg17.detail


def test_직전_빌드보다_짧아지면_EG17_폐기(tmp_path: Path) -> None:
    """퇴행 금지 — 옛 `backfill_end` 상수가 하던 '조용히 짧아진 원천' 감시를 직전 빌드가 한다."""
    eq = tmp_path / "equity"
    ok = build.build_table(rules_s02.TRADING_CALENDAR, STAGE_SLICE, eq, SEED, build_id="b_cal_1")
    assert ok.ok, [(g.name, g.status.value, g.detail) for g in ok.gates]
    assert _gate(ok, "EG17").metrics["max_date"] == LAST_TD.isoformat()
    rule = _short_calendar(tmp_path, "trading_calendar")      # 같은 표의 다음 판
    r = build.build_table(rule, STAGE_SLICE, eq, SEED, build_id="b_cal_2")
    assert r.status is build.BuildStatus.GATE_FAILED
    eg17 = _gate(r, "EG17")
    assert eg17.metrics["previous_max_date"] == LAST_TD.isoformat()
    assert "calendar regressed" in eg17.detail


def test_seed_baseline이_설계값을_싣는다() -> None:
    assert SEED.get("trading_calendar", "calendar_start") == "2010-01-04"
    dates = SEED.get("trading_calendar", "asof_sample_dates")
    tickers = SEED.get("trading_calendar", "asof_sample_tickers")
    assert isinstance(dates, list) and len(dates) == 5
    assert isinstance(tickers, list) and len(tickers) == 15   # ★ 절단본 대체 표본(서버는 20)
    measured = SEED.data["_measured"]
    assert isinstance(measured, list)
    for e in measured:
        assert set(e) >= {"table", "metric", "inputs", "sql", "value", "measured_at", "growing"}
