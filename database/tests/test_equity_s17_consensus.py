"""S17 `consensus_daily` + 뷰 `v_consensus` — stage 절단본 위 실제 `build_table` 왕복
(DESIGN §4-6·§5 · GATES §3 ⑲ · EG6-P01/P02 · EG8-P07 · EG9-P05 · FX-5-001…006 · FX-N-005).

손계산 기대값은 `stg_consensus_monthly`·`stg_v3_revision_daily` 원자료를 직접 열어 확인한 값이다
(산출 SQL 로 얻은 값이 아니다). 절단본 실측:
  wise 886행 / 7티커 / 관측월 2025-08~2026-09 / 지표 {eps, revenue} / 수집일 2026-09-01·09-02
       → distinct (ticker, obs_month, target_period, metric) 476. 같은 키 2판본 410쌍 중
         **값이 실제로 바뀐 관측점 12개**(예: 000660 2026-08 202812 eps 458131.68 → 450128.71)
  v3   494행 / 5티커 / 2026-04-03~08-31 / wide 8지표(003540 은 revenue 전건 결측, 2025/12 행 3개는
       bps·pbr·roe 결측) → unpivot distinct 199
  합 675행 · 격리 0 · 연도 파티션 2(2025 170 · 2026 505)

체인은 `trading_calendar` → `security` → `security_span` → `consensus_daily` → `catalog` 다
(`security_span` 은 EG9 커버율 분모·EG3 미상장 티커 기록형이 읽는 축).
"""
from __future__ import annotations

import json
import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest
from equity import build, catalog, rules_s01, rules_s02, rules_s17, views
from equity.baseline import Baseline, load
from equity.gates import GateStatus
from equity.model import EquityTable

STAGE_SLICE = Path(__file__).parent / "fixtures" / "stage_slice"
SEED = Baseline({**load(Path(rules_s01.__file__).parent / "baseline_seed_s01.json").data,
                 **load(Path(rules_s02.__file__).parent / "baseline_seed_s02.json").data,
                 **load(rules_s17.BASELINE_SEED).data})

N_WISE = 476                 # distinct (ticker, obs_month, target_period, metric) — 손계산
N_V3 = 199                   # 40 + 39 + 40 + 40 + 40 (003540 은 revenue 결측으로 한 지표 적다)
N_ROWS = N_WISE + N_V3       # 675
N_YEARS = 2                  # 2025 · 2026
N_OVERLAP_KEYS = 45          # src 가 둘인 키 = 티커 5 × 월 5 × 202612 × {eps,revenue} − 003540 5
N_COVERAGE_DEGRADED = 36     # v3 collected_date 결측(2026-04-03·04-06·04-08) 행에서 고른 관측
N_REVISED_POINTS = 12        # 두 판본의 consensus 가 실제로 다른 관측점 수 (기록형)
N_FOLDED = 886 - N_WISE      # 410 — 접힌 wise 판본 수 (같은 키의 두 번째 fetched_date 행)
GATE_ORDER = ["EG0", "EG7", "EG1", "EG2", "EG3", "EG3_consensus_daily", "EG6_consensus_daily",
              "EG8_consensus_daily", "EG9_consensus_daily", "EG4", "EG5a"]

# FX-5-001 — 뒤 판본이 덮어쓴 관측점(000660 · obs 2026-08-31 · 202812 · eps). stage 원자료 두 값
REVISED_FIRST = Decimal("458131.6800")      # fetched_date 2026-09-01 (최초 관측)
REVISED_LATER = Decimal("450128.7100")      # fetched_date 2026-09-02 (덮어쓴 값)
# FX-5-002 — 두 관측점의 원본 값(005930 202612 eps). 리비전은 이 둘의 손계산이다
REV_2026_07 = Decimal("47928.7400")
REV_2026_08 = Decimal("48338.6400")


def _build_chain(equity_root: Path) -> build.BuildResult:
    for rule, bid in ((rules_s02.TRADING_CALENDAR, "b_s17_cal"), (rules_s01.SECURITY, "b_s17_sec"),
                      (rules_s02.SECURITY_SPAN, "b_s17_span")):
        r = build.build_table(rule, STAGE_SLICE, equity_root, SEED, build_id=bid)
        assert r.ok, (rule.name, [(g.name, g.status.value, g.detail) for g in r.gates])
    return build.build_table(rules_s17.CONSENSUS_DAILY, STAGE_SLICE, equity_root, SEED,
                             build_id="b_s17_cons")


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, build.BuildResult]:
    """정상 왕복 1회 — 읽기 전용 검사가 여럿이라 모듈에서 한 번만 짓는다."""
    eq = tmp_path_factory.mktemp("s17") / "equity"
    return eq, _build_chain(eq)


@pytest.fixture(scope="module")
def published(built: tuple[Path, build.BuildResult]) -> catalog.CatalogResult:
    root, r = built
    assert r.ok
    p = catalog.publish(root, SEED)
    assert p.ok, [(g.name, g.status.value, g.detail) for g in p.gates]
    return p


def _gate(r: build.BuildResult, name: str):
    return next(g for g in r.gates if g.name == name)


def _variant(tmp_path: Path, name: str, sql: str) -> EquityTable:
    """산출 SQL 만 바꾼 부정 픽스처용 변종. 이름을 바꿔야 정본 MANIFEST 를 안 건드린다."""
    p = tmp_path / f"{name}.sql"
    p.write_text(sql, encoding="utf-8")
    return EquityTable(**{**rules_s17.CONSENSUS_DAILY.__dict__, "name": name, "sql_path": p})


def _body() -> str:
    return rules_s17.SQL_PATH.read_text(encoding="utf-8")


def _stage_con() -> duckdb.DuckDBPyConnection:
    """절단본 stage 를 직접 여는 연결 — 손계산 기대값을 산출과 무관하게 다시 읽는다."""
    con = duckdb.connect()
    for t in ("stg_consensus_monthly", "stg_v3_revision_daily"):
        con.execute(f"CREATE OR REPLACE TEMP VIEW {t} AS SELECT * FROM read_parquet("
                    f"'{STAGE_SLICE / t}/**/*.parquet', hive_partitioning=true, "
                    "union_by_name=true)")
    return con


def _query(out_dir: Path, sql: str) -> list[tuple[object, ...]]:
    con = _stage_con()
    try:
        con.execute("CREATE OR REPLACE TEMP VIEW cd AS SELECT * FROM read_parquet("
                    f"'{out_dir / 'year=*' / '*.parquet'}', hive_partitioning=true)")
        return con.execute(sql).fetchall()
    finally:
        con.close()


# ── 정상 왕복 ────────────────────────────────────────────────────────────────

def test_절단본_빌드가_전_게이트를_통과한다(built: tuple[Path, build.BuildResult]) -> None:
    r = built[1]
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    assert [g.name for g in r.gates] == GATE_ORDER
    assert not [g for g in r.gates if g.status is GateStatus.FAIL]
    assert r.n_rows == N_ROWS and r.n_reject == 0
    assert set(r.inputs) == {"stg_consensus_monthly", "stg_v3_revision_daily", "security_span"}
    assert r.inputs["security_span"] == "b_s17_span"      # equity 입력도 고정된다


def test_EG1_등식은_두_원천의_distinct_키_합이다(built: tuple[Path, build.BuildResult]) -> None:
    """좌변 = 산출 행수, 우변 = wise distinct + v3(unpivot) distinct. 손계산 476 + 199."""
    eg1 = _gate(built[1], "EG1").metrics
    assert eg1["lhs"] == eg1["rhs"] == eg1["expect"] == N_ROWS and eg1["delta"] == 0
    con = _stage_con()
    try:                                    # 산출 CTE 를 안 쓰고 stage 에서 직접 다시 센다
        n_wise, n_v3 = con.execute("""
            SELECT (SELECT count(*) FROM (SELECT DISTINCT ticker,
                        date_trunc('month', obs_date), target_period, metric
                    FROM stg_consensus_monthly)),
                   (SELECT count(*) FROM (
                        SELECT DISTINCT ticker, date_trunc('month', date),
                               replace(target_period, '/', ''), m.metric
                        FROM stg_v3_revision_daily r, (SELECT unnest([
                             'revenue','op','ni','eps','bps','per','pbr','roe_pct']) AS metric) m
                        WHERE CASE m.metric WHEN 'revenue' THEN r.revenue WHEN 'op' THEN r.op
                                 WHEN 'ni' THEN r.ni WHEN 'eps' THEN r.eps WHEN 'bps' THEN r.bps
                                 WHEN 'per' THEN r.per WHEN 'pbr' THEN r.pbr
                                 WHEN 'roe_pct' THEN r.roe_pct END IS NOT NULL))""").fetchone()
    finally:
        con.close()
    assert (int(str(n_wise)), int(str(n_v3))) == (N_WISE, N_V3)


def test_파티션은_year_obs_month_로_갈린다(built: tuple[Path, build.BuildResult]) -> None:
    """DESIGN §2: date_axis 이되 키 식은 `year(obs_month)` — `date` 컬럼은 없다."""
    r = built[1]
    assert r.out_dir is not None
    assert len(r.partitions) == N_YEARS
    assert sum(int(str(p["n_rows"])) for p in r.partitions) == N_ROWS
    assert "date" not in rules_s17.CONSENSUS_DAILY.columns
    assert _query(r.out_dir, "SELECT count(*) FROM cd WHERE year <> year(obs_month)") == [(0,)]


def test_재빌드가_같은_해시를_낸다(tmp_path: Path) -> None:
    """EG5a — 같은 inputs·같은 규칙 판본이면 파티션 content_hash 가 전량 같다."""
    eq = tmp_path / "equity"
    first = _build_chain(eq)
    assert first.ok
    second = build.build_table(rules_s17.CONSENSUS_DAILY, STAGE_SLICE, eq, SEED,
                               build_id="b_s17_cons2")
    assert second.ok, [(g.name, g.status.value, g.detail) for g in second.gates]
    eg5a = _gate(second, "EG5a")
    assert eg5a.status is GateStatus.PASS and eg5a.metrics["n_changed_partitions"] == 0
    assert second.content_hash == first.content_hash


# ── 판본 선택 (EG6-P01) · PIT 부정 픽스처 ────────────────────────────────────

def test_덮어쓴_판본이_최초_관측을_바꾸지_않는다(built: tuple[Path, build.BuildResult]) -> None:
    """이 테이블의 존재 이유. stage 에는 두 값이 다 있고 산출에는 **먼저 알 수 있던 값** 만 있다."""
    con = _stage_con()
    try:
        raw = con.execute("""
            SELECT fetched_date, consensus FROM stg_consensus_monthly
            WHERE ticker = '000660' AND target_period = '202812' AND metric = 'eps'
              AND obs_date = DATE '2026-08-31' ORDER BY fetched_date""").fetchall()
    finally:
        con.close()
    assert [(r[0], r[1]) for r in raw] == [(date(2026, 9, 1), REVISED_FIRST),
                                           (date(2026, 9, 2), REVISED_LATER)]
    got = _query(built[1].out_dir, """
        SELECT est_mean, available_date FROM cd
        WHERE ticker = '000660' AND obs_month = DATE '2026-08-01'
          AND target_period = '202812' AND metric = 'eps' AND src = 'wise'""")
    assert got == [(REVISED_FIRST, date(2026, 9, 1))]


def test_max_판본을_고르면_EG6가_폐기한다(tmp_path: Path) -> None:
    """FX-N-005 — 선택을 max(fetched_date) 로 바꾸면 값이 덮어쓴 판본이 되고 EG6 가 FAIL 한다."""
    eq = tmp_path / "equity"
    assert _build_chain(eq).ok
    sql = _body().replace("ORDER BY w.fetched_date, w.obs_date",
                          "ORDER BY w.fetched_date DESC, w.obs_date")
    assert sql != _body()
    r = build.build_table(_variant(tmp_path, "consensus_daily_max", sql), STAGE_SLICE, eq, SEED,
                          build_id="b_s17_max")
    assert not r.ok
    eg6 = _gate(r, "EG6_consensus_daily")
    assert eg6.status is GateStatus.FAIL
    # 두 판본이 있는 키 전부가 어긋난다 — 값이 같아도 available_date 가 09-02 로 밀린다(look-ahead).
    # 그중 값까지 바뀌는 관측점이 N_REVISED_POINTS 개다.
    assert eg6.metrics["n_wise_pick_mismatch"] == N_FOLDED > N_REVISED_POINTS
    assert eg6.metrics["n_v3_pick_mismatch"] == 0        # v3 축은 건드리지 않았다
    assert _gate(r, "EG4").status is GateStatus.SKIP     # 앞이 FAIL 이면 뒤는 upstream_failed


def test_접힌_판본_수와_리비전_관측점_수를_기록한다(
        built: tuple[Path, build.BuildResult]) -> None:
    m = _gate(built[1], "EG6_consensus_daily").metrics
    assert m["n_wise_pick_mismatch"] == 0 and m["n_v3_pick_mismatch"] == 0
    assert m["n_wise_folded_versions"] == N_FOLDED
    assert m["n_wise_observation_points_revised"] == N_REVISED_POINTS


def test_두_관측점의_원본_값으로_리비전을_손계산한다(
        built: tuple[Path, build.BuildResult]) -> None:
    """FX-5-002 — 기대값은 두 관측점의 **원본 값** 에서만 나온다.

    `stg_v3_revision_compare`(1w/1m/3m/1y lookback)·`stg_consensus_matrix.lookback` 은 기대값
    출처로 무효다(GATES §4 EG4-P03) — 그 컬럼들은 원장이 계산해 둔 리비전이라 산출을 검증하지
    못하고 같은 실수를 되풀이한다.
    """
    got = _query(built[1].out_dir, """
        SELECT obs_month, est_mean FROM cd
        WHERE ticker = '005930' AND target_period = '202612' AND metric = 'eps'
          AND src = 'wise' AND obs_month IN (DATE '2026-07-01', DATE '2026-08-01')
        ORDER BY obs_month""")
    assert got == [(date(2026, 7, 1), REV_2026_07), (date(2026, 8, 1), REV_2026_08)]
    revision = REV_2026_08 / REV_2026_07 - 1        # 48338.64 / 47928.74 − 1
    assert abs(float(revision) - 0.0085522799) < 1e-9


# ── v3 unpivot · 최초 관측 (EG6-P02) ─────────────────────────────────────────

def test_v3_unpivot_대응표는_rules_선언과_SQL이_같다() -> None:
    """`.sql` 의 UNION ALL 가지 순서 = `rules_s17.V3_METRICS`. 손으로 옮긴 표라 대조가 필요하다."""
    head, _, _ = _body().partition(rules_s17.POPULATION_MARKER)
    head = re.sub(r"--[^\n]*", "", head)            # 주석은 대응표가 아니다
    for metric, column, unit in rules_s17.V3_METRICS:
        assert f"'{metric}'" in head and f"'{unit}'" in head
        assert f"WHERE {column} IS NOT NULL" in head
    order = [(head.index(f"'{m}'"), m) for m, _, _ in rules_s17.V3_METRICS]
    assert [m for _, m in sorted(order)] == [m for m, _, _ in rules_s17.V3_METRICS]
    assert "opinion" not in head                     # 투자의견은 S18 축이다
    assert head.count("UNION ALL") == len(rules_s17.V3_METRICS) - 1


def test_v3_wide_한_행이_지표_8행이_된다(built: tuple[Path, build.BuildResult]) -> None:
    """FX-5-006 — 005930 2026-04-03 wide 행 ↔ 산출 8행. 값은 stage 열을 그대로 옮긴 것."""
    con = _stage_con()
    try:
        wide = con.execute("""
            SELECT revenue, op, ni, eps, bps, per, pbr, roe_pct
            FROM stg_v3_revision_daily
            WHERE ticker = '005930' AND date = DATE '2026-04-03'
              AND target_period = '2026/12'""").fetchone()
    finally:
        con.close()
    assert wide is not None
    expect = {m: Decimal(str(v)) for (m, _, _), v in zip(rules_s17.V3_METRICS, wide, strict=True)}
    got = _query(built[1].out_dir, """
        SELECT metric, est_mean, unit, obs_date FROM cd
        WHERE ticker = '005930' AND obs_month = DATE '2026-04-01'
          AND target_period = '202612' AND src = 'v3' ORDER BY metric""")
    assert len(got) == len(rules_s17.V3_METRICS)
    units = {m: u for m, _, u in rules_s17.V3_METRICS}
    for metric, est_mean, unit, obs_date in got:
        assert est_mean == expect[str(metric)] and unit == units[str(metric)]
        assert obs_date == date(2026, 4, 3)


def test_collected_date_결측은_date_로_대체되고_coverage_degraded_가_선다(
        built: tuple[Path, build.BuildResult]) -> None:
    """FX-5-005 — stage 의 fallback 을 재계산하지 않고 그대로 나른다."""
    got = _query(built[1].out_dir, """
        SELECT count(*), count(*) FILTER (available_basis = 'default'),
               count(*) FILTER (coverage_degraded) FROM cd WHERE src = 'v3'
                AND available_date = obs_date AND coverage_degraded""")
    assert got == [(N_COVERAGE_DEGRADED, N_COVERAGE_DEGRADED, N_COVERAGE_DEGRADED)]
    one = _query(built[1].out_dir, """
        SELECT obs_date, available_date, available_basis, coverage_degraded FROM cd
        WHERE ticker = '005930' AND obs_month = DATE '2026-04-01'
          AND target_period = '202612' AND metric = 'revenue' AND src = 'v3'""")
    assert one == [(date(2026, 4, 3), date(2026, 4, 3), "default", True)]


def test_GATES_초안의_min_by_식은_최초_관측을_못_고른다() -> None:
    """GATES §1 EG6-P02 초안 SQL 정정 근거(§9).

    `coalesce(min_by(r.collected_date, r.date), min(r.date))` 는 duckdb 가 인자 NULL 행을
    건너뛰므로 collected_date 가 있는 **더 늦은** 행을 고른다. 절단본에서 그 차이가 실재한다.
    """
    con = _stage_con()
    try:
        draft, correct = con.execute("""
            SELECT (SELECT coalesce(min_by(collected_date, date), min(date))
                    FROM stg_v3_revision_daily
                    WHERE ticker = '000660' AND target_period = '2026/12'
                      AND strftime(date, '%Y-%m') = '2026-04'),
                   (SELECT available_date FROM stg_v3_revision_daily
                    WHERE ticker = '000660' AND target_period = '2026/12'
                      AND strftime(date, '%Y-%m') = '2026-04'
                    ORDER BY date LIMIT 1)""").fetchone()
    finally:
        con.close()
    assert correct == date(2026, 4, 3)          # 첫 관측일, collected_date 결측 → date fallback
    assert draft == date(2026, 4, 10)           # 초안 식이 고르는 값 — 일주일 늦다
    assert draft != correct


# ── 두 원천 (FX-5-004 · EG8-P07) ─────────────────────────────────────────────

def test_겹치는_달은_src_가_다른_2행이다(built: tuple[Path, build.BuildResult]) -> None:
    """FX-5-004 — 접지 않는다. 어느 쪽을 볼지는 소비자 뷰가 정한다."""
    rows = _query(built[1].out_dir, """
        SELECT src, obs_date, est_min IS NULL FROM cd
        WHERE ticker = '005930' AND obs_month = DATE '2026-08-01'
          AND target_period = '202612' AND metric = 'eps' ORDER BY src""")
    assert rows == [("v3", date(2026, 8, 3), True), ("wise", date(2026, 8, 31), False)]
    m = _gate(built[1], "EG3_consensus_daily").metrics
    assert m["n_keys_with_both_src"] == N_OVERLAP_KEYS
    assert m["n_months_with_both_src"] == 5             # 2026-04 ~ 2026-08


def test_EG8은_같은_관측일에서_두_원천을_비교한다(built: tuple[Path, build.BuildResult]) -> None:
    """EG8-P07 — 모집단은 (ticker, 관측일, target_period, metric). 절단본 32/45 = 0.7111."""
    m = _gate(built[1], "EG8_consensus_daily").metrics
    assert m["n_overlap_rows"] == m["n_overlap_measured"] == N_OVERLAP_KEYS
    assert m["n_match"] == 32 and abs(m["match_rate"] - 32 / 45) < 1e-12
    assert m["match_rate"] >= m["v3_wise_match_min"]
    # 어긋나는 쪽은 반올림이 아니다 — 절단본 최대 10.8%(247540 2026-07-31 eps 312.34 vs 350)
    assert m["max_rel_diff"] > 0.1 and m["median_rel_diff"] < 1e-4


def test_target_period_어휘는_YYYYMM_으로_맞춘다(built: tuple[Path, build.BuildResult]) -> None:
    """맞추지 않으면 v3 'YYYY/MM' 과 wise 'YYYYMM' 이 영영 안 만나 EG8 이 공허하게 통과한다."""
    got = _query(built[1].out_dir, """
        SELECT count(*) FILTER (NOT regexp_matches(target_period, '^[0-9]{6}$')),
               count(DISTINCT target_period),
               (SELECT count(*) FROM cd WHERE src = 'v3' AND target_period LIKE '%/%')
        FROM cd""")
    assert got == [(0, 4, 0)]                  # 202512(v3) · 202612 · 202712 · 202812


def test_어긋난_target_period_는_격리된다(tmp_path: Path) -> None:
    """EG7 격리형 — 6자리가 아니면 `_reject/target_period_invalid/`. 행을 버리지 않는다."""
    eq = tmp_path / "equity"
    assert _build_chain(eq).ok
    sql = _body().replace(
        "SELECT ticker, date, replace(target_period, '/', ''), 'roe_pct', '%', roe_pct,",
        "SELECT ticker, date, replace(target_period, '/', '') || 'X', 'roe_pct', '%', roe_pct,")
    assert sql != _body()
    r = build.build_table(_variant(tmp_path, "consensus_daily_badtp", sql), STAGE_SLICE, eq, SEED,
                          build_id="b_s17_badtp")
    assert not r.ok
    assert r.n_reject == 25 and r.n_rows == N_ROWS - 25
    assert _gate(r, "EG7").status is GateStatus.FAIL     # 25/675 > 코드 기본 임계 0.001
    assert _gate(r, "EG7").metrics["reject_by_reason"] == {"target_period_invalid": 25}


# ── 어휘 · 커버리지 ──────────────────────────────────────────────────────────

def test_어휘는_선언과_산출이_같다(built: tuple[Path, build.BuildResult]) -> None:
    m = _gate(built[1], "EG3_consensus_daily").metrics
    assert set(m["n_by_src"]) == set(rules_s17.SRC_VOCAB)
    assert set(m["n_by_metric"]) <= set(rules_s17.METRIC_VOCAB)
    assert set(m["n_by_unit"]) == set(rules_s17.UNIT_VOCAB)
    assert m["n_by_src"] == {"wise": N_WISE, "v3": N_V3}
    assert m["n_by_available_basis"] == {"measured": N_ROWS - N_COVERAGE_DEGRADED,
                                         "default": N_COVERAGE_DEGRADED}
    # v3 는 min/max 를 주지 않는다 — 0 으로 채우면 est_min ≤ est_mean 밴드가 조용히 무너진다
    assert m["n_v3_minmax_present"] == 0 and m["n_wise_band_violation"] == 0
    assert m["n_latest_current_columns"] == 0            # EG6-P03
    assert m["n_ticker_not_in_security_span"] == 0


def test_커버리지_기록은_상장_종목_대비_비율을_남긴다(
        built: tuple[Path, build.BuildResult]) -> None:
    """EG9-P05 판정 + P06 대용 기록(선택편향). 절단본은 14개월 전부 커버 7종목이라 계단 0."""
    m = _gate(built[1], "EG9_consensus_daily").metrics
    assert m["n_months"] == 14
    assert m["cover_drop_max_observed"] == 0.0 and m["cover_rise_max_observed"] == 0.0
    assert m["cover_drop_max_observed"] <= m["cover_ratio_drop_max"]
    assert {int(r["n_cov"]) for r in m["by_month"]} == {7}
    assert 0 < m["cover_vs_span_min"] <= m["cover_vs_span_max"] < 1     # 선택편향 기록


def test_baseline_미등재면_EG8_EG9가_skip_한다(tmp_path: Path) -> None:
    """첫 서버 빌드 상태 — 임계 상수가 없으면 폐기하지 않고 측정치만 남긴다(GATES §7-3).

    모듈 픽스처 root 를 쓰지 않는다 — 여기서 커밋하면 `published` 가 다른 build 를 굽는다.
    """
    eq = tmp_path / "equity"
    assert _build_chain(eq).ok
    r = build.build_table(rules_s17.CONSENSUS_DAILY, STAGE_SLICE, eq,
                          Baseline({k: v for k, v in SEED.data.items()
                                    if k != "consensus_daily"}),
                          build_id="b_s17_nobl")
    assert r.ok
    for name, metric in (("EG8_consensus_daily", "n_match"),
                         ("EG9_consensus_daily", "cover_drop_max_observed")):
        g = _gate(r, name)
        assert g.status is GateStatus.SKIP and g.detail == "no_baseline"
        assert metric not in g.metrics          # 상수를 못 읽으면 측정 전에 멈춘다
        assert str(g.metrics["missing_metric"]).startswith("consensus_daily.")


# ── 뷰 `v_consensus` (DESIGN §5) ─────────────────────────────────────────────

def test_카탈로그는_v_consensus_만_굽는다(published: catalog.CatalogResult) -> None:
    p = published
    assert sorted(p.macros) == [views.SIGNATURES["v_consensus"]]
    assert set(p.skipped) == set(views.SIGNATURES) - {"v_consensus"}
    assert [g.status for g in p.gates] == [GateStatus.SKIP] * 3   # EG11·EG5c·EG3-P05 not_built
    assert "v_consensus" not in catalog.ASOF_VIEWS               # 일별 date 축이 없다


def test_v_consensus_는_available_date_로만_자른다(
        built: tuple[Path, build.BuildResult], published: catalog.CatalogResult) -> None:
    """EG-C ⑨ 의 골자 — `obs_month` 를 날짜 축으로 쓰면 wise 판본이 1년 앞서 보인다.

    절단본에서 wise 는 전부 2026-09-01·02 에 수집됐다. as_of 2026-06-30 에서
    `obs_month <= as_of` 로 자르면 2025-08~2026-06 관측이 다 보이지만, 그날 실제로 알 수 있던
    것은 하나도 없다.
    """
    con = duckdb.connect(str(published.path), read_only=True)
    try:
        pit = con.execute("SELECT count(*) FROM v_consensus(DATE '2026-06-30')").fetchone()
        wise_pit = con.execute("SELECT count(*) FROM v_consensus(DATE '2026-06-30') "
                               "WHERE src = 'wise'").fetchone()
    finally:
        con.close()
    naive = _query(built[1].out_dir, """
        SELECT count(*), count(*) FILTER (src = 'wise') FROM cd
        WHERE obs_month <= DATE '2026-06-30'""")
    assert wise_pit is not None and int(str(wise_pit[0])) == 0     # PIT 축에서는 0
    assert naive[0][1] > 0                                         # obs_month 축에서는 보인다
    assert pit is not None and int(str(pit[0])) < naive[0][0]


def test_v_consensus_는_겹치는_키를_한_행으로_접는다(published: catalog.CatalogResult) -> None:
    """소비자가 같은 관측점을 두 번 세지 않도록 먼저 알 수 있던 행(min(available_date))만 남긴다."""
    con = duckdb.connect(str(published.path), read_only=True)
    try:
        dup, srcs = con.execute("""
            SELECT (SELECT count(*) FROM (
                        SELECT ticker, obs_month, target_period, metric, count(*) c
                        FROM v_consensus(DATE '2026-08-20') GROUP BY ALL HAVING c > 1)),
                   (SELECT count(DISTINCT src) FROM v_consensus(DATE '2026-08-20'))""").fetchone()
        one = con.execute("""
            SELECT src, available_date FROM v_consensus(DATE '2026-08-20')
            WHERE ticker = '005930' AND obs_month = DATE '2026-08-01'
              AND target_period = '202612' AND metric = 'eps'""").fetchall()
    finally:
        con.close()
    assert int(str(dup)) == 0 and int(str(srcs)) == 1              # 절단본 캘린더는 08-20 까지
    assert one == [("v3", date(2026, 8, 4))]        # wise(09-01)보다 먼저 알 수 있던 행


def test_v_consensus_는_두_연결에서_같은_결과를_낸다(published: catalog.CatalogResult) -> None:
    """EG11 규약을 이 뷰에도 적용 — `_asof/` 표본 대상은 아니지만 결정성은 확인한다."""
    sql = ("SELECT s.as_of, v.* FROM (SELECT unnest([DATE '2026-06-30', DATE '2026-08-20']) "
           "AS as_of) s, LATERAL (SELECT * FROM v_consensus(s.as_of)) v")
    hashes = []
    for _ in range(2):
        con = duckdb.connect(str(published.path), read_only=True)
        try:
            hashes.append(con.execute(
                f"SELECT count(*), bit_xor(hash(CAST(t AS VARCHAR))) FROM ({sql}) t").fetchone())
        finally:
            con.close()
    assert hashes[0] == hashes[1] and int(str(hashes[0][0])) > 0


def test_뷰_본문은_절대경로_parquet_를_읽는다(built: tuple[Path, build.BuildResult],
                                              published: catalog.CatalogResult) -> None:
    """P1c — 상대경로면 read_only 재오픈에서 `No files found`."""
    body = published.macros[views.SIGNATURES["v_consensus"]]
    assert "read_parquet([" in body and "hive_partitioning=false" in body
    assert str((built[0] / "consensus_daily").resolve()) in body
    assert "_reject" not in body


def test_뷰_메타는_snapshot_id_와_함께_기록된다(built: tuple[Path, build.BuildResult],
                                                published: catalog.CatalogResult) -> None:
    meta = json.loads((built[0] / catalog.META_NAME).read_text(encoding="utf-8"))
    assert meta["snapshot_id"] == published.snapshot_id
    assert set(meta["builds"]) == {"trading_calendar", "security", "security_span",
                                   "consensus_daily"}
    assert meta["macros"] == [views.SIGNATURES["v_consensus"]]
