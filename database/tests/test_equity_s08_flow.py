"""S08 `flow_daily` — 절단본 위 실빌드 왕복 · KIS 손 하네스 · 부정 픽스처
(DESIGN v1.2 §4-3 · GATES v1.0 §3 ⑪ · §1 EG3-P06·EG7-P06 · §4 FX-3-*).

절단본 실측(손계산, 빌드 SQL 과 독립 — `explore` 질의로 원장·로그를 직접 세었다):
  격자 36,972 = universe_daily 41,066 − ETF 069500 4,094 (14 티커, status listed·suspended
  전부. universe_daily 는 delisted 행을 만들지 않으므로 status 술어는 항등이고 sec_type 만
  실제로 자른다).
  원장 stg_flow_daily_kiwoom 20,313 = 격자 매핑 18,581 + pre_calendar 18 + off_grid 1,714.
    pre_calendar 18 = 000660·003540·005930 각 6행(2009-12-22~12-30, 캘린더 하한 2010-01-04).
    off_grid 1,714 = 036220 1,411(2018-06-21~2024-03-12) + 101970 303(2023-12-26~2025-03-27)
      — 폐지와 재상장 사이의 **티커 재사용** 행이다. 격자로 새면 다른 회사의 수급이 그 종목
      이력에 붙는다(생존편향·거짓 팩터).
  fill_kind 36,972 = measured 18,581 + src_omitted 6,109 + not_collected 12,282.
    src_omitted = shard_done 2,218(036220 1,570 · 101970 648 — ka10060 샤드는 done 이고 요청창
      [2010-01-01, 2026-08-20] 이 전 구간인데 그 날짜의 원장 행이 없다)
                + unit_ok 3,891(000030 1,037 · 900050 1,916 · 900060 938 — 키움 샤드가 없고
      KIS flow 유닛 status='ok' 창이 덮는다).
    not_collected 12,282 = 003545·003547·005935 각 4,094(어느 로그도 안 덮는 티커, P3 의
      '샤드 없는 1,070 티커' 부류).
  12주체 합(orgn 제외) |s| 최대 4,000,000원 · s ≠ 0 인 행 10,219/18,581 · s 는 항상 1e6 배수
    (키움이 백만원 단위로 반올림해 준다 — baseline_seed_s08 investor_sum_tol_krw 근거).
  `orgn_krw` 는 기관 7주체 합과 **다르다** — 18,581 행 중 10,783 행이 다르고 편차 최대
    283,440,000,000원. DESIGN §4-3 이 orgn 을 항등식에서 뺀 근거이자 FIELD_MAP GAP-03
    ('부분' 판정)의 실측이다.

**절단본 한계 두 가지(서버와 다른 축)**
  ① `stg_flow_split_daily` 절단본은 **0행 · 파티션 0개**다(README 의 '0행 테이블은 스키마
     보존용 빈 파티션 1개' 규약이 이 테이블에만 빠졌다). 파티션이 없으면 duckdb
     `read_parquet` 이 IOException 을 던져 입력으로 읽을 수조차 없다 → `_repaired_stage_root`
     가 stage 선언(`rules_kis.STG_FLOW_SPLIT_DAILY`)에서 **스키마만** 복원한 0행 파티션을
     갖는 사본 루트를 만든다. 값은 만들지 않는다.
  ② 그래서 로컬에서 KIS 축은 전부 0이다: 000030·900050·900060 셀은 서버라면 `measured`(kis)
     일 텐데 여기서는 `src_omitted`/`unit_ok` 로 나온다. FX-3-002·005·007(KIS 셀 골든
     픽스처)은 그래서 EG4 에 넣지 않고 `test_KIS_*` 손 하네스가 대신 검사한다.
"""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest
from equity import (
    build,
    rules_s01,
    rules_s02,
    rules_s03,
    rules_s04,
    rules_s05,
    rules_s06,
    rules_s08,
)
from equity.__main__ import main
from equity.baseline import Baseline, load
from equity.gates import GateStatus
from equity.model import FILL_EVIDENCE, FILL_KINDS, EquityTable
from stage import manifest
from stage.model import KIND_NUMERIC, KIND_TEXT
from stage.rules_kis import STG_FLOW_SPLIT_DAILY
from stage.rules_kiwoom import STG_FLOW_DAILY_KIWOOM

STAGE_SLICE = Path(__file__).parent / "fixtures" / "stage_slice"
FLOW = rules_s08.FLOW_DAILY
GAP_TABLE = "stg_flow_split_daily"

UPSTREAM = (rules_s02.TRADING_CALENDAR, rules_s01.SECURITY, rules_s02.SECURITY_SPAN,
            rules_s01.CORP, rules_s01.CORP_TICKER, rules_s04.PRICE_DAILY, rules_s05.CORP_EVENT,
            rules_s06.ADJ_FACTOR, rules_s03.UNIVERSE_DAILY)
GATE_ORDER = ["EG0", "EG7", "EG1", "EG2", "EG3", "EG1_ledger", "EG3_flow_daily", "EG4", "EG5a"]

N_GRID = 36972                       # universe_daily 41,066 − ETF 4,094
N_LEDGER_KIWOOM = 20313
N_MEASURED = 18581
N_REJECT_BY_REASON = {"pre_calendar": 18, "off_grid": 1714}
N_REJECT = 1732
PRE_CALENDAR_BY_TICKER = {"000660": 6, "003540": 6, "005930": 6}
OFF_GRID_BY_TICKER = {"036220": 1411, "101970": 303}
FILL_KIND_COUNTS = {"measured": 18581, "src_omitted": 6109, "not_collected": 12282}
EVIDENCE_COUNTS = {"none": 30863, "shard_done": 2218, "unit_ok": 3891}
SRC_COUNTS = {"kiwoom": 20799, "kis": 3891, "null": 12282}
# (ticker → 격자 행수, 원장 매핑 행수) — stg_listing_daily·stg_flow_daily_kiwoom 손계산
GRID_BY_TICKER = {
    "000030": (1037, 0), "0001A0": (135, 135), "000660": (4094, 4094), "003540": (4094, 4094),
    "003545": (4094, 0), "003547": (4094, 0), "005930": (4094, 4094), "005935": (4094, 0),
    "036220": (2163, 593), "101970": (989, 341), "161890": (3396, 3396), "247540": (1834, 1834),
    "900050": (1916, 0), "900060": (938, 0)}
SHARD_OMITTED_BY_TICKER = {"036220": 1570, "101970": 648}
UNIT_OK_BY_TICKER = {"000030": 1037, "900050": 1916, "900060": 938}
NOT_COLLECTED_TICKERS = {"003545", "003547", "005935"}
INVESTOR_SUM_ABS_MAX = 4_000_000
N_INVESTOR_SUM_NONZERO = 10219
N_ORGN_NE_MEMBER_SUM = 10783
ORGN_DEV_MAX = 283_440_000_000


# ── 절단본 수리 · 시드 ───────────────────────────────────────────────────────

def _stage_type(kind: str, column) -> str:  # noqa: ANN001
    if kind == KIND_TEXT:
        return "VARCHAR"
    if kind == KIND_NUMERIC:
        return column.decimal_type
    return "DATE"


def _repaired_stage_root(base: Path) -> Path:
    """절단본 사본 루트 — `stg_flow_split_daily` 만 스키마 보존 0행 파티션으로 되살린다.

    다른 테이블은 심볼릭 링크라 복사 비용이 없다(`inputs.pin` 은 링크 너머 실파일을
    하드링크한다). 타입은 stage 선언(`ColumnRule.decimal_type`)에서 그대로 가져오므로 서버
    parquet 스키마와 같다 — 절단본에 없는 값을 지어내지 않는다(0행).
    """
    root = base / "stage"
    root.mkdir(parents=True, exist_ok=True)
    for d in sorted(STAGE_SLICE.iterdir()):
        if d.is_dir() and d.name != GAP_TABLE and not (root / d.name).exists():
            (root / d.name).symlink_to(d.resolve())
    table_root = root / GAP_TABLE
    if table_root.exists():
        return root
    build_id = manifest.load(STAGE_SLICE / GAP_TABLE / "MANIFEST.json").current_build
    assert build_id is not None
    part = f"year={date.today().year}"
    vdir = table_root / f"v={build_id}" / part
    vdir.mkdir(parents=True)
    cols = ", ".join(f'CAST(NULL AS {_stage_type(c.kind, c)}) AS "{c.name}"'
                     for c in STG_FLOW_SPLIT_DAILY.columns)
    con = duckdb.connect()
    try:
        con.execute(f"COPY (SELECT {cols} WHERE false) TO '{vdir / 'part0.parquet'}' "
                    "(FORMAT PARQUET)")
    finally:
        con.close()
    (vdir / "_meta.json").write_text(json.dumps(
        {"table": GAP_TABLE, "build_id": build_id, "partition": part, "n_rows": 0,
         "content_hash": "0:empty", "lag_known": False, "coverage_from": None, "gates": []},
        ensure_ascii=False), encoding="utf-8")
    manifest.commit(table_root, manifest.BuildRecord(
        build_id=build_id, snapshot_id="snap_slice_repair", rules_version="2.2.3",
        built_at_utc="2026-09-06T00:00:00+00:00", n_rows=0, content_hash="0:empty",
        partitions=[{"path": f"v={build_id}/{part}", "n_rows": 0}], gates=[]))
    return root


def _seed() -> Baseline:
    merged: dict[str, dict[str, object]] = {}
    for p in (rules_s01.BASELINE_SEED, Path(rules_s02.__file__).parent / "baseline_seed_s02.json",
              rules_s03.BASELINE_SEED, rules_s05.BASELINE_SEED, rules_s06.BASELINE_SEED,
              rules_s08.BASELINE_SEED):
        for k, v in load(p).data.items():
            if not k.startswith("_") and k != "measured_at" and isinstance(v, dict):
                merged.setdefault(k, {}).update(v)
    return Baseline(dict(merged))


SEED = _seed()


def _seed_as(name: str) -> Baseline:
    """변종 이름으로 같은 상수를 재등재 — 게이트가 `bl(rule.name, metric)` 로 읽는다."""
    return Baseline({**SEED.data, name: SEED.table(FLOW.name)})


def _fails(r: build.BuildResult) -> list[str]:
    return [f"{g.name}:{g.detail}" for g in r.gates if g.status is GateStatus.FAIL]


def _gate(r: build.BuildResult, name: str):  # noqa: ANN202
    return next(g for g in r.gates if g.name == name)


def _chain(root: Path, stage_root: Path) -> None:
    for rule in UPSTREAM:
        r = build.build_table(rule, stage_root, root, SEED, build_id=f"b_{rule.name}")
        assert r.ok, _fails(r)


@pytest.fixture(scope="module")
def stage_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _repaired_stage_root(tmp_path_factory.mktemp("s08_stage"))


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory, stage_root: Path) -> build.BuildResult:
    root = tmp_path_factory.mktemp("s08") / "equity"
    _chain(root, stage_root)
    return build.build_table(FLOW, stage_root, root, SEED, build_id="b_s08_fd")


def _query(out_dir: Path, sql: str, stage_root: Path | None = None) -> list[tuple[object, ...]]:
    con = duckdb.connect()
    try:
        con.execute("CREATE VIEW f AS SELECT * FROM read_parquet("
                    f"'{out_dir / 'year=*' / '*.parquet'}', hive_partitioning=false)")
        con.execute("CREATE VIEW rej AS SELECT * FROM read_parquet("
                    f"'{out_dir / '_reject' / 'reject_reason=*' / '*.parquet'}', "
                    "hive_partitioning=true)")
        base = stage_root or STAGE_SLICE
        for t in ("stg_flow_daily_kiwoom", "stg_shards_kiwoom", "stg_units_kis"):
            con.execute(f"CREATE VIEW {t} AS SELECT * FROM read_parquet("
                        f"'{base / t}/**/*.parquet', hive_partitioning=true, union_by_name=true)")
        return con.execute(sql).fetchall()
    finally:
        con.close()


# ── 절단본 왕복 ───────────────────────────────────────────────────────────────

def test_절단본_빌드가_전_게이트를_통과한다(built: build.BuildResult) -> None:
    assert built.ok, _fails(built)
    assert [g.name for g in built.gates] == GATE_ORDER
    assert {g.name: g.status.value for g in built.gates
            if g.status is not GateStatus.PASS} == {"EG5a": "skip"}
    assert (built.n_rows, built.n_reject) == (N_GRID, N_REJECT)
    assert set(built.inputs) == set(FLOW.inputs)
    assert built.inputs["universe_daily"] == "b_universe_daily"
    assert built.inputs["trading_calendar"] == "b_trading_calendar"


def test_격자_밖_행과_빠진_셀은_양방향으로_잡힌다(built: build.BuildResult) -> None:
    m = _gate(built, "EG3_flow_daily").metrics
    assert m["n_row_outside_grid"] == 0 and m["n_grid_cell_missing"] == 0


def test_격자는_universe_daily_에서_ETF만_빠진_것이다(built: build.BuildResult) -> None:
    """DESIGN §4-3 격자 술어. 절단본 universe_daily 41,066 − ETF 069500 4,094 = 36,972."""
    assert built.out_dir is not None
    by_ticker = dict(_query(built.out_dir, "SELECT ticker, count(*) FROM f GROUP BY 1"))
    assert {t: int(str(n)) for t, n in by_ticker.items()} == {
        t: g for t, (g, _) in GRID_BY_TICKER.items()}
    assert "069500" not in by_ticker                 # ETF 는 격자 밖
    assert sum(g for g, _ in GRID_BY_TICKER.values()) == N_GRID
    # 셀당 1행 — 두 원천이 같은 셀을 안 물었다(EG8-P05 겹침 0)
    assert _query(built.out_dir, "SELECT count(*), count(DISTINCT (date, ticker)) FROM f") == [
        (N_GRID, N_GRID)]


def test_EG1_좌변은_셀_수이고_우변은_격자다(built: build.BuildResult) -> None:
    m = _gate(built, "EG1").metrics
    assert (m["lhs"], m["rhs"] - m["n_reject"], m["delta"]) == (N_GRID, N_GRID, 0)


def test_격리는_pre_calendar와_off_grid로_갈린다(built: build.BuildResult) -> None:
    assert built.out_dir is not None
    got = _query(built.out_dir,
                 "SELECT reject_reason, ticker, count(*) FROM rej GROUP BY 1, 2 ORDER BY 1, 2")
    pre = {t: int(str(n)) for r, t, n in got if r == "pre_calendar"}
    off = {t: int(str(n)) for r, t, n in got if r == "off_grid"}
    assert pre == PRE_CALENDAR_BY_TICKER
    assert off == OFF_GRID_BY_TICKER          # 폐지~재상장 사이 티커 재사용 행
    assert _gate(built, "EG7").metrics["reject_by_reason"] == N_REJECT_BY_REASON
    # 격리 행은 격자에 없다 — 같은 (ticker, date) 가 산출에 남아 있으면 안 된다
    assert _query(built.out_dir,
                  "SELECT count(*) FROM rej r JOIN f USING (ticker, date)") == [(0,)]


def test_원장_보존_등식이_원천별로_선다(built: build.BuildResult) -> None:
    m = _gate(built, "EG1_ledger").metrics
    assert m["delta_src_kiwoom"] == 0 and m["delta_src_kis"] == 0
    assert m["n_ledger_kiwoom"] == N_LEDGER_KIWOOM
    assert m["n_measured_kiwoom"] + m["n_reject_kiwoom"] == N_LEDGER_KIWOOM
    assert m["n_ledger_kis"] == 0                    # 절단본 한계 ①
    assert m["missing_investor_columns"] == []
    assert m["investor_columns"] == list(rules_s08.KIWOOM_INVESTOR_COLUMNS)


# ── fill_kind — 로그 축 판정 ─────────────────────────────────────────────────

def test_fill_kind_분포가_로그_축과_맞는다(built: build.BuildResult) -> None:
    m = _gate(built, "EG3_flow_daily").metrics
    assert m["fill_kind_counts"] == FILL_KIND_COUNTS
    assert m["fill_evidence_counts"] == EVIDENCE_COUNTS
    assert m["src_counts"] == SRC_COUNTS
    assert m["coverage_measured"] == pytest.approx(N_MEASURED / N_GRID)
    assert m["evidence_rate"] == 1.0                 # src_omitted 는 전부 로그 근거가 있다


def test_fill_kind_는_티커별로_로그가_있는_쪽을_고른다(built: build.BuildResult) -> None:
    assert built.out_dir is not None
    rows = _query(built.out_dir, """
        SELECT ticker, fill_kind.kind, fill_kind.evidence, src, count(*)
        FROM f GROUP BY 1, 2, 3, 4 ORDER BY 1, 2, 3""")
    got: dict[tuple[str, str, str], int] = {
        (str(t), str(k), str(e)): int(str(n)) for t, k, e, _, n in rows}
    src_by: dict[tuple[str, str], set[object]] = {}
    for t, k, _e, s, _ in rows:
        src_by.setdefault((str(t), str(k)), set()).add(s)
    # ① 원장이 있는 셀 = measured / evidence none
    assert {t: got.get((t, "measured", "none"), 0) for t, _ in GRID_BY_TICKER.items()} == {
        t: m for t, (_, m) in GRID_BY_TICKER.items()}
    # ② ka10060 샤드 done 이고 그날 원장 행이 없는 셀 = src_omitted / shard_done / kiwoom
    assert {t: n for (t, k, e), n in got.items() if k == "src_omitted" and e == "shard_done"} == \
        SHARD_OMITTED_BY_TICKER
    assert src_by[("036220", "src_omitted")] == {"kiwoom"}
    # ③ 키움 샤드가 없고 KIS flow 유닛 ok 창이 덮는 셀 = src_omitted / unit_ok / kis
    assert {t: n for (t, k, e), n in got.items() if k == "src_omitted" and e == "unit_ok"} == \
        UNIT_OK_BY_TICKER
    assert src_by[("900050", "src_omitted")] == {"kis"}
    # ④ 어느 로그도 안 덮는 셀 = not_collected / none / src NULL
    assert {t for (t, k, _e), n in got.items() if k == "not_collected" and n} == \
        NOT_COLLECTED_TICKERS
    assert src_by[("005935", "not_collected")] == {None}
    assert set(FILL_KIND_COUNTS) <= set(FILL_KINDS)
    assert set(EVIDENCE_COUNTS) <= set(FILL_EVIDENCE)


def test_미측정_셀은_전부_NULL이다(built: build.BuildResult) -> None:
    """GATES EG9-P04 '미수집 → 0' 금지. 값 없음은 NULL 이고 이유는 fill_kind 가 든다."""
    assert built.out_dir is not None
    cols = rules_s08.KIWOOM_INVESTOR_COLUMNS
    notnull = " OR ".join(f'"{c}" IS NOT NULL' for c in cols)
    zero = " OR ".join(f'"{c}" = 0' for c in cols)
    assert _query(built.out_dir, f"SELECT count(*) FROM f WHERE fill_kind.kind <> 'measured' "
                                 f"AND ({notnull})") == [(0,)]
    assert _query(built.out_dir, f"SELECT count(*) FROM f WHERE fill_kind.kind <> 'measured' "
                                 f"AND ({zero})") == [(0,)]


def test_격자는_가격_행의_부분집합이다(built: build.BuildResult) -> None:
    """DESIGN §4-3 fill_kind 판정의 'KRX 가격 행 존재일' 조건을 술어에서 뺀 근거(sql 주석).

    격자 = universe_daily 이고 그 EG1 우변이 Σ security_span.n_days = price_daily 행수라
    항등이다. 여기서는 산출 격자를 price_daily 에 직접 재조인해 확인한다.
    """
    assert built.out_dir is not None
    root = built.out_dir.parents[1]
    con = duckdb.connect()
    try:
        con.execute("CREATE VIEW f AS SELECT * FROM read_parquet("
                    f"'{built.out_dir / 'year=*' / '*.parquet'}')")
        con.execute("CREATE VIEW p AS SELECT * FROM read_parquet("
                    f"'{root / 'price_daily' / 'v=b_price_daily' / 'year=*' / '*.parquet'}')")
        assert con.execute("SELECT count(*) FROM f WHERE NOT EXISTS (SELECT 1 FROM p "
                           "WHERE p.ticker = f.ticker AND p.date = f.date)").fetchone() == (0,)
    finally:
        con.close()


# ── 값 · 단위 · 항등식 ───────────────────────────────────────────────────────

def test_측정값은_원장_그대로다(built: build.BuildResult) -> None:
    """×1e6 은 stage 가 이미 했다(rules_kiwoom `_flow_krw` unit_scale) — equity 는 나르기만."""
    assert built.out_dir is not None
    diff = " OR ".join(f'f."{c}" IS DISTINCT FROM k."{c}"'
                       for c in rules_s08.KIWOOM_INVESTOR_COLUMNS)
    assert _query(built.out_dir, f"""
        SELECT count(*) FROM f JOIN stg_flow_daily_kiwoom k USING (ticker, date)
        WHERE {diff}""") == [(0,)]
    assert _gate(built, "EG3_flow_daily").metrics["n_kiwoom_value_mismatch"] == 0
    # 골든 셀 — 005930 2018-05-04(50:1 분할일) 외국인 순매수 = 원장 −53,845 백만원 ×1e6
    assert _query(built.out_dir, "SELECT frgnr_invsr_krw, ind_invsr_krw, natfor_krw, src "
                                 "FROM f WHERE ticker = '005930' AND date = DATE '2018-05-04'"
                  ) == [(-53_845_000_000, 655_449_000_000, 1_123_000_000, "kiwoom")]


def test_12주체_합_항등식(built: build.BuildResult) -> None:
    """EG3-P06 — `orgn` 을 뺀 12주체 순매수 합 = 0 ± tol. 절단본 편차는 백만원 반올림뿐."""
    assert built.out_dir is not None
    sum12 = " + ".join(f'"{c}"' for c in rules_s08.INVESTOR_SUM_COLUMNS)
    rows = _query(built.out_dir, f"""
        SELECT max(abs({sum12})), count(*) FILTER (WHERE ({sum12}) <> 0),
               count(*) FILTER (WHERE ({sum12}) % 1000000 <> 0), count(*)
        FROM f WHERE fill_kind.kind = 'measured'""")
    abs_max, n_nonzero, n_not_mn, n_rows = (int(str(x)) for x in rows[0])
    assert (abs_max, n_nonzero, n_not_mn, n_rows) == (
        INVESTOR_SUM_ABS_MAX, N_INVESTOR_SUM_NONZERO, 0, N_MEASURED)
    m = _gate(built, "EG3_flow_daily").metrics
    assert m["n_investor_sum_violation"] == 0
    assert m["investor_sum_abs_max_krw"] == INVESTOR_SUM_ABS_MAX
    assert m["investor_sum_tol_krw"] == SEED.get(FLOW.name, "investor_sum_tol_krw")
    assert m["n_investor_sum_excluded_null"] == 0
    assert len(rules_s08.INVESTOR_SUM_COLUMNS) == 12


def test_orgn은_기관7주체_합이_아니다(built: build.BuildResult) -> None:
    """DESIGN §4-3 이 orgn 을 항등식에서 뺀 실측 근거(FIELD_MAP GAP-03). 기록형이다."""
    m = _gate(built, "EG3_flow_daily").metrics
    assert m["n_orgn_ne_member_sum"] == N_ORGN_NE_MEMBER_SUM
    assert m["orgn_member_sum_dev_max_krw"] == ORGN_DEV_MAX
    assert rules_s08.ORGN_COLUMN not in rules_s08.INVESTOR_SUM_COLUMNS


def test_PIT_은_date_와_default_다(built: build.BuildResult) -> None:
    assert built.out_dir is not None
    assert _query(built.out_dir, "SELECT count(*) FROM f WHERE available_date IS DISTINCT FROM "
                                 "date OR available_basis <> 'default'") == [(0,)]
    assert _gate(built, "EG2").status is GateStatus.PASS


def test_gap_metric은_원천별_원장_최종일이다(built: build.BuildResult) -> None:
    """서버는 KIS flow 가 2026-08-14 에 끝난다(DESIGN §10 P16) — 그 이후 격자 셀이 gap."""
    m = _gate(built, "EG3_flow_daily").metrics
    assert m["kiwoom_ledger_max_date"] == "2026-08-20"
    assert m["n_grid_cells_after_kiwoom_max"] == 0
    assert m["kis_ledger_max_date"] is None          # 절단본 한계 ① — 원장이 비었다
    assert m["n_grid_cells_after_kis_max"] is None
    assert m["n_src_overlap"] == 0                   # EG8-P05 축
    assert m["n_cells_both_logs"] == 0


def test_재빌드는_파티션_해시가_같다(built: build.BuildResult, stage_root: Path) -> None:
    """EG5a — 같은 inputs·같은 규칙 판본이면 재현된다."""
    assert built.out_dir is not None
    again = build.build_table(FLOW, stage_root, built.out_dir.parents[1], SEED,
                              build_id="b_s08_fd2")
    assert again.ok, _fails(again)
    eg5 = _gate(again, "EG5a")
    assert eg5.status is GateStatus.PASS, eg5.detail
    assert again.content_hash == built.content_hash
    assert len(again.partitions) == 17            # 2010~2026


def test_gate_재판정은_격리_뷰_없이도_돈다(built: build.BuildResult, stage_root: Path,
                                            tmp_path: Path, capsys) -> None:  # noqa: ANN001
    """`python -m equity gate` 는 `reject_view=None` 으로 게이트를 돌린다(`__main__._cmd_gate`).

    EG1_ledger 가 격리 뷰의 `src` 로 원천을 가르므로 그 문맥에서는 합계 등식으로 낮춰 선다.
    """
    assert built.out_dir is not None
    root = built.out_dir.parents[1]
    bl = tmp_path / "baseline.json"
    bl.write_text(json.dumps(SEED.data, ensure_ascii=False, default=str), encoding="utf-8")
    assert main(["--root", str(root), "--stage-root", str(stage_root), "--baseline", str(bl),
                 "gate", "flow_daily"]) == 0
    out = capsys.readouterr().out
    assert "ok table=flow_daily" in out
    assert "'reject_split_by_src': False" in out
    assert "'delta_src_total': 0" in out
    assert "EG1   pass" in out                       # 우변이 입력만 읽어 재판정에서도 선다


# ── KIS 손 하네스 (절단본 한계 ②) ────────────────────────────────────────────

_KIS_ROWS_BASE = {c: 0 for _, c in rules_s08.KIS_MAPPING if c}


def _kis_row(ticker: str, d: date, **vals: int) -> dict[str, object]:
    return {"req_ticker": ticker, "stck_bsop_date": d, **_KIS_ROWS_BASE, **vals}


@pytest.fixture
def kis_harness(tmp_path: Path, make_stage_tree) -> tuple[Path, Path]:  # noqa: ANN001
    """`stg_flow_split_daily` 만 손으로 채운 stage 루트 + 그 위 equity 루트.

    003545(대신증권2우B — 키움 샤드도 KIS 유닛도 없어 절단본에서 not_collected 인 티커)에
    KIS 행 2개를 격자 안에, 1개를 캘린더 하한 이전에 둔다. 값은 손으로 만든 것이며 stage
    원장이 아니다(절단본 KIS 원장은 0행) — 대응표·NULL·격리 경로만 검사한다.
    """
    rows = [
        _kis_row("003545", date(2018, 5, 3), prsn_ntby_tr_pbmn_krw=11_000_000,
                 frgn_ntby_tr_pbmn_krw=-7_000_000, orgn_ntby_tr_pbmn_krw=-4_000_000,
                 scrt_ntby_tr_pbmn_krw=-1_000_000, insu_ntby_tr_pbmn_krw=-2_000_000,
                 ivtr_ntby_tr_pbmn_krw=3_000_000, bank_ntby_tr_pbmn_krw=-5_000_000,
                 fund_ntby_tr_pbmn_krw=6_000_000, pe_fund_ntby_tr_pbmn_krw=-8_000_000,
                 etc_corp_ntby_tr_pbmn_krw=9_000_000),
        _kis_row("003545", date(2018, 5, 4), prsn_ntby_tr_pbmn_krw=1),
        _kis_row("003545", date(2009, 12, 30), prsn_ntby_tr_pbmn_krw=2),   # pre_calendar
    ]
    base = tmp_path / "h"
    root = _repaired_stage_root(base)
    (root / GAP_TABLE).rename(root / f"_{GAP_TABLE}_empty")     # 0행 판을 치우고 손 원장으로
    make_stage_tree(base, GAP_TABLE, [{"ticker": r["req_ticker"], "date": r["stck_bsop_date"],
                                       **{k: v for k, v in r.items()
                                          if k not in ("req_ticker", "stck_bsop_date")}}
                                      for r in rows], partition_class="date_axis")
    eq = base / "equity"
    _chain(eq, root)
    return root, eq


def test_KIS_대응표대로_매핑되고_무대응_주체는_NULL이다(kis_harness) -> None:  # noqa: ANN001
    stage_root, eq = kis_harness
    r = build.build_table(FLOW, stage_root, eq, SEED, build_id="b_s08_kis")
    assert r.ok, _fails(r)
    assert r.out_dir is not None
    assert (r.n_rows, r.n_reject) == (N_GRID, N_REJECT + 1)
    got = _query(r.out_dir, "SELECT " + ", ".join(
        f'"{c}"' for c in rules_s08.KIWOOM_INVESTOR_COLUMNS)
        + ", src, fill_kind.kind FROM f WHERE ticker = '003545' AND date = DATE '2018-05-03'",
        stage_root)
    assert got == [(11_000_000, -7_000_000, -4_000_000, -1_000_000, -2_000_000, 3_000_000,
                    None, -5_000_000, 6_000_000, -8_000_000, None, 9_000_000, None,
                    "kis", "measured")]
    m = _gate(r, "EG1_ledger").metrics
    assert (m["n_ledger_kis"], m["n_measured_kis"], m["n_reject_kis"]) == (3, 2, 1)
    assert m["delta_src_kis"] == 0
    assert _gate(r, "EG3_flow_daily").metrics["n_kis_value_mismatch"] == 0
    # 손 원장 행이 있는 셀은 not_collected 에서 measured 로 바뀐다
    assert _gate(r, "EG3_flow_daily").metrics["fill_kind_counts"] == {
        **FILL_KIND_COUNTS, "measured": N_MEASURED + 2,
        "not_collected": FILL_KIND_COUNTS["not_collected"] - 2}


# ── 부정 픽스처 (GATES §7-5) ────────────────────────────────────────────────

def _variant(built: build.BuildResult, tmp_path: Path, stage_root: Path, name: str,
             wrap: str) -> build.BuildResult:
    """산출 SQL 만 바꾼 변종을 같은 equity_root 에 빌드한다(정본 MANIFEST 를 안 건드린다)."""
    assert built.out_dir is not None
    body = FLOW.sql_path.read_text(encoding="utf-8").strip().rstrip(";")
    p = tmp_path / f"{name}.sql"
    p.write_text(wrap.format(body=body), encoding="utf-8")
    rule = EquityTable(**{**FLOW.__dict__, "name": name, "sql_path": p})
    return build.build_table(rule, stage_root, built.out_dir.parents[1], _seed_as(name),
                             build_id=f"b_{name}", fixtures_path=FLOW.sql_path.parent.parent
                             / "fixtures" / "flow_daily.json")


def test_격자_밖_행이_새면_EG1이_잡는다(built: build.BuildResult, tmp_path: Path,
                                      stage_root: Path) -> None:
    """off_grid 격리를 지우면 티커 재사용 행 1,714 이 격자로 샌다(생존편향).

    EG1 은 좌·우변이 함께 움직여(격리가 준 만큼 좌변이 는다) 이 사고에 눈이 먼다 — 그래서
    `EG3_flow_daily` 가 산출 → `universe_daily` 안티조인으로 따로 잡는다(선언 주석 참조).
    """
    r = _variant(built, tmp_path, stage_root, "flow_grid_leak",
                 "SELECT * REPLACE (CASE WHEN reject_reason = 'pre_calendar' THEN reject_reason "
                 "END AS reject_reason) FROM ({body}) t")
    assert not r.ok
    n_leak = OFF_GRID_BY_TICKER["036220"] + OFF_GRID_BY_TICKER["101970"]
    assert _gate(r, "EG1").status is GateStatus.PASS                  # 등식만으로는 못 잡는다
    g = _gate(r, "EG3_flow_daily")
    assert g.status is GateStatus.FAIL
    assert g.metrics["n_row_outside_grid"] == n_leak
    assert g.metrics["n_grid_cell_missing"] == 0


def test_단위가_뒤집히면_EG3가_잡는다(built: build.BuildResult, tmp_path: Path,
                                    stage_root: Path) -> None:
    """stage 가 이미 한 ×1e6 을 못 보고 백만원 단위로 실으면 값 대조와 항등식이 함께 깨진다."""
    r = _variant(built, tmp_path, stage_root, "flow_unit_flip",
                 "SELECT * REPLACE (ind_invsr_krw / 1000000 AS ind_invsr_krw) "
                 "FROM ({body}) t")
    assert not r.ok
    g = _gate(r, "EG3_flow_daily")
    assert g.status is GateStatus.FAIL
    assert g.metrics["n_kiwoom_value_mismatch"] > 0
    assert g.metrics["n_investor_sum_violation"] > 0


def test_주체가_뒤바뀌면_EG3가_잡는다(built: build.BuildResult, tmp_path: Path,
                                    stage_root: Path) -> None:
    """개인 ↔ 외국인 매핑이 뒤바뀌면 12주체 합은 그대로라 값 대조만 잡는다."""
    r = _variant(built, tmp_path, stage_root, "flow_swap",
                 "SELECT * REPLACE (frgnr_invsr_krw AS ind_invsr_krw, "
                 "ind_invsr_krw AS frgnr_invsr_krw) FROM ({body}) t")
    assert not r.ok
    g = _gate(r, "EG3_flow_daily")
    assert g.status is GateStatus.FAIL
    assert g.metrics["n_kiwoom_value_mismatch"] > 0
    assert g.metrics["n_investor_sum_violation"] == 0


def test_fill_kind_어휘_밖이면_EG3가_잡는다(built: build.BuildResult, tmp_path: Path,
                                           stage_root: Path) -> None:
    # measured 는 그대로 둔다 — 전부 바꾸면 EG1_ledger 가 먼저 깨져 어휘 술어를 못 본다
    r = _variant(built, tmp_path, stage_root, "flow_bad_kind",
                 "SELECT * REPLACE (CASE WHEN fill_kind['kind'] = 'measured' THEN fill_kind "
                 "ELSE {{'kind': 'bogus', 'evidence': fill_kind['evidence']}} END AS fill_kind) "
                 "FROM ({body}) t")
    assert not r.ok
    g = _gate(r, "EG3_flow_daily")
    assert g.status is GateStatus.FAIL
    assert g.metrics["n_fill_kind_outside_vocab"] == N_GRID - N_MEASURED


def test_미수집을_0으로_채우면_EG4가_잡는다(built: build.BuildResult, tmp_path: Path,
                                           stage_root: Path) -> None:
    """GATES EG9-P04 의 취지 — 값 없는 셀에 0 을 넣으면 골든 픽스처 FX-3-014 가 깨진다."""
    r = _variant(built, tmp_path, stage_root, "flow_zero_fill",
                 "SELECT * REPLACE (coalesce(ind_invsr_krw, 0) AS ind_invsr_krw) "
                 "FROM ({body}) t")
    assert not r.ok
    assert _gate(r, "EG4").status is GateStatus.FAIL


# ── 선언 · 시드 ──────────────────────────────────────────────────────────────

def test_13주체_컬럼은_stage_선언에서_나온다() -> None:
    declared = [c.name for c in STG_FLOW_DAILY_KIWOOM.columns if c.unit_scale == 1_000_000]
    assert rules_s08.KIWOOM_INVESTOR_COLUMNS == tuple(declared)
    assert len(declared) == 13
    assert list(FLOW.columns)[3:16] == declared          # date·ticker·src 다음 13개
    assert FLOW.grain == ("date", "ticker", "src")
    assert FLOW.reject_reasons == ("pre_calendar", "off_grid")


def test_KIS_대응표는_stage_컬럼_실명이다() -> None:
    actual = {c.name for c in STG_FLOW_SPLIT_DAILY.columns}
    mapped = [s for _, s in rules_s08.KIS_MAPPING if s]
    assert set(mapped) <= actual, sorted(set(mapped) - actual)
    assert len(mapped) == 10                              # 무대응 3(etc_fnnc·natn·natfor)
    assert [c for c, s in rules_s08.KIS_MAPPING if s is None] == [
        "etc_fnnc_krw", "natn_krw", "natfor_krw"]
    # KIS 대금 컬럼은 stage 가 이미 ×1e6 했다 — equity 가 또 곱하면 안 된다
    assert all(c.unit_scale == 1_000_000 for c in STG_FLOW_SPLIT_DAILY.columns
               if c.name in set(mapped))


def test_시드가_요구_상수를_전부_담는다() -> None:
    seed = load(rules_s08.BASELINE_SEED)
    assert seed.get("flow_daily", "investor_sum_tol_krw") == 6_000_000
    assert seed.get("flow_daily", "threshold_EG7") == 0.05
    # 절단본 실측 최댓값(4e6)보다 크고, 반올림 상한(12주체 × 0.5백만원)과 같다
    assert seed.get("flow_daily", "investor_sum_tol_krw") == 12 * 500_000
    assert seed.get("flow_daily", "investor_sum_tol_krw") > INVESTOR_SUM_ABS_MAX


# ── S08-2 외국인 보유 (F02·F08) ──────────────────────────────────────────────


def test_외국인_보유비중과_한도소진율이_키움_행에_붙는다(built: build.BuildResult) -> None:
    """`stg_foreign_daily`(키움 ka10008) 를 격자에 잇는다 — 서버 768만 행 · 2,602종목 ·
    2009-10-15 ~ 2026-08-24 이고 다섯 컬럼 **결측 0** 이다.

    **원천 축이 없다** — 외국인 보유는 키움 한 곳뿐이라 `src='kis'` 행에는 값이 없다. 격자 등식
    (EG1)은 (date, ticker) 축이라 행 수가 변하지 않고, 채우지 못한 이유는 `fill_kind` 가 이미
    나르는 규약을 따른다. 별도 표를 만들지 않은 이유가 이것이다(BLOCKED_FACTORS §2-2 (가)).
    """
    assert built.ok and built.out_dir is not None
    got = _query(built.out_dir,
                 "SELECT foreign_wght_pct, foreign_limit_exh_pct, foreign_poss_shr FROM f "
                 "WHERE ticker = '005930' AND date = DATE '2018-05-04' AND src = 'kiwoom'")
    assert got == [(Decimal("52.79"), Decimal("52.79"), Decimal("3388514370"))]


def test_외국인_보유는_키움_행에만_실린다(built: build.BuildResult) -> None:
    """KIS 보완 행에는 외국인 보유 원천이 없다 — 0 으로 채우지 않고 NULL 로 둔다."""
    assert built.out_dir is not None
    n_kis, n_filled = _query(built.out_dir,
        "SELECT count(*), count(foreign_wght_pct) + count(foreign_poss_shr) "
        "FROM f WHERE src = 'kis'")[0]
    assert n_kis, "절단본에 KIS 행이 있어야 이 검사가 성립한다"
    assert n_filled == 0
