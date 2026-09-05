"""S02 `index_daily` — stage 절단본 위 실제 `build_table` 왕복 (DESIGN §4-1 · GATES §3 ⑥).

손계산 기대값은 `stg_index_daily` 원자료를 직접 열어 확인한 값이다. 절단본 실측:
16,376행 = 4,094 거래일 × 4계열(index_class 2종 KOSPI·KOSDAQ, index_name 코스피·코스피 200·
코스닥·코스닥 150). 파생이 없는 1:1 사본이라 값 변형은 전부 버그다.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import duckdb
from equity import build, rules_s02
from equity.baseline import load
from equity.gates import GateStatus
from equity.model import EquityTable

STAGE_SLICE = Path(__file__).parent / "fixtures" / "stage_slice"
SEED = load(Path(rules_s02.__file__).parent / "baseline_seed_s02.json")
STG_GLOB = str(STAGE_SLICE / "stg_index_daily" / "v=*" / "year=*" / "*.parquet")

N_ROWS = 16376
N_YEARS = 17                        # 2010 ~ 2026
FIRST_DAY = "2010-01-04"


def _build(tmp_path: Path, rule: EquityTable | None = None) -> build.BuildResult:
    return build.build_table(rule or rules_s02.INDEX_DAILY, STAGE_SLICE, tmp_path / "equity",
                             SEED, build_id="b_s02_idx")


def _variant(tmp_path: Path, name: str, sql: str) -> EquityTable:
    p = tmp_path / f"{name}.sql"
    p.write_text(sql, encoding="utf-8")
    return EquityTable(**{**rules_s02.INDEX_DAILY.__dict__, "name": name, "sql_path": p})


def _query(out_dir: Path, sql: str) -> list[tuple[object, ...]]:
    con = duckdb.connect()
    try:
        con.execute("CREATE OR REPLACE TEMP VIEW idx AS SELECT * FROM read_parquet("
                    f"'{out_dir / 'year=*' / '*.parquet'}', hive_partitioning=true)")
        con.execute(f"CREATE OR REPLACE TEMP VIEW stg AS SELECT * FROM read_parquet('{STG_GLOB}')")
        return con.execute(sql).fetchall()
    finally:
        con.close()


def _gate(r: build.BuildResult, name: str):
    return next(g for g in r.gates if g.name == name)


# ── 정상 왕복 ────────────────────────────────────────────────────────────────

def test_절단본_빌드가_전_게이트를_통과한다(tmp_path: Path) -> None:
    r = _build(tmp_path)
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    assert [g.name for g in r.gates] == ["EG0", "EG7", "EG1", "EG2", "EG3", "EG4", "EG5a"]
    assert not [g for g in r.gates if g.status is GateStatus.FAIL]
    assert _gate(r, "EG2").status is GateStatus.PASS          # 차원표가 아니라 팩트다
    assert r.n_rows == N_ROWS and r.n_reject == 0
    eg1 = _gate(r, "EG1").metrics
    assert eg1["lhs"] == N_ROWS and eg1["rhs"] == N_ROWS


def test_date_axis는_연도_디렉토리로_갈린다(tmp_path: Path) -> None:
    r = _build(tmp_path)
    assert r.ok and r.out_dir is not None
    years = sorted(p.name for p in r.out_dir.iterdir() if p.is_dir())
    assert years == [f"year={y}" for y in range(2010, 2027)]
    assert len(years) == N_YEARS
    assert len(r.partitions) == N_YEARS
    assert sum(int(str(p["n_rows"])) for p in r.partitions) == N_ROWS
    # 파티션 키가 date 의 연도와 어긋나면 연도 프루닝이 조용히 틀린 행을 준다
    assert _query(r.out_dir, "SELECT count(*) FROM idx WHERE year <> year(date)") == [(0,)]


def test_stg_index_daily와_1대1이다(tmp_path: Path) -> None:
    """grain 3축 · 전 사실 컬럼을 값까지 대조한다. 조인 없이 사본이므로 차집합이 0 이어야 한다."""
    r = _build(tmp_path)
    assert r.ok and r.out_dir is not None
    cols = ("index_class, index_name, date, close_idx, change_idx, fluc_pct, open_idx, "
            "high_idx, low_idx, volume_shr, value_krw, mktcap_krw")
    assert _query(r.out_dir, f"SELECT count(*) FROM (SELECT {cols} FROM idx "
                             f"EXCEPT SELECT {cols} FROM stg)") == [(0,)]
    assert _query(r.out_dir, f"SELECT count(*) FROM (SELECT {cols} FROM stg "
                             f"EXCEPT SELECT {cols} FROM idx)") == [(0,)]


def test_index_class는_2종_index_name은_4종(tmp_path: Path) -> None:
    """`index_class` 를 키에서 빼면 양시장 동명 업종지수가 충돌한다(rules_krx 주석)."""
    r = _build(tmp_path)
    assert r.ok and r.out_dir is not None
    assert _query(r.out_dir, "SELECT index_class, index_name, count(*) FROM idx "
                             "GROUP BY 1, 2 ORDER BY 1, 2") == [
        ("KOSDAQ", "코스닥", 4094), ("KOSDAQ", "코스닥 150", 4094),
        ("KOSPI", "코스피", 4094), ("KOSPI", "코스피 200", 4094)]


def test_첫_거래일_지수값이_원자료와_같다(tmp_path: Path) -> None:
    """FX-1-010 — 손계산(원자료 직접 확인): 코스피 1696.14 · 코스닥 528.09."""
    r = _build(tmp_path)
    assert r.ok and r.out_dir is not None
    rows = _query(r.out_dir, "SELECT index_name, close_idx, open_idx, mktcap_krw FROM idx "
                             f"WHERE date = DATE '{FIRST_DAY}' ORDER BY index_name")
    assert rows == [
        ("코스닥", Decimal("528.09"), Decimal("517.03"), Decimal(87697001353305)),
        ("코스닥 150", Decimal("1000.00"), None, None),
        ("코스피", Decimal("1696.14"), Decimal("1681.71"), Decimal(894123953078265)),
        ("코스피 200", Decimal("223.49"), Decimal("221.67"), Decimal(777079135448075))]


def test_결측은_결측으로_남는다(tmp_path: Path) -> None:
    """코스닥 150 기준일은 change_idx·open_idx 가 NULL 이다. 0 으로 채우면 그날 수익률이 굳는다."""
    r = _build(tmp_path)
    assert r.ok and r.out_dir is not None
    assert _query(r.out_dir, "SELECT change_idx, open_idx, high_idx, low_idx, volume_shr FROM idx "
                             f"WHERE index_name = '코스닥 150' AND date = DATE '{FIRST_DAY}'") == [
        (None, None, None, None, None)]


def test_PIT는_available_date가_date이고_basis는_default(tmp_path: Path) -> None:
    r = _build(tmp_path)
    assert r.ok and r.out_dir is not None
    assert _query(r.out_dir, "SELECT count(*) FROM idx WHERE available_date IS DISTINCT FROM date "
                             "OR available_basis IS DISTINCT FROM 'default'") == [(0,)]
    eg2 = _gate(r, "EG2").metrics
    assert eg2["n_available_null"] == 0 and eg2["n_available_before_content"] == 0
    assert eg2["n_basis_outside_vocab"] == 0
    assert eg2["basis_vocab"] == ["default"] and eg2["content_date_column"] == "date"


# ── 부정 픽스처 (게이트가 fail 을 내야 통과) ─────────────────────────────────

def test_available_date가_date보다_이르면_EG2가_폐기한다(tmp_path: Path) -> None:
    """"그날 종가를 전날에 알았다" 는 룩어헤드. 행수·값은 전부 정상이라 EG1 은 통과한다."""
    body = rules_s02.INDEX_DAILY.sql_path.read_text(encoding="utf-8").strip().rstrip(";")
    rule = _variant(tmp_path, "idx_lookahead", f"""
        WITH base AS (SELECT * FROM ({body}))
        SELECT base.* REPLACE (CAST(base.date - INTERVAL 1 DAY AS DATE) AS available_date)
        FROM base""")
    r = _build(tmp_path, rule)
    assert r.status is build.BuildStatus.GATE_FAILED
    assert _gate(r, "EG1").status is GateStatus.PASS
    eg2 = _gate(r, "EG2")
    assert eg2.status is GateStatus.FAIL and eg2.metrics["n_available_before_content"] == N_ROWS
    assert _gate(r, "EG3").detail == "upstream_failed"


def test_basis_어휘_밖이면_EG2가_폐기한다(tmp_path: Path) -> None:
    body = rules_s02.INDEX_DAILY.sql_path.read_text(encoding="utf-8").strip().rstrip(";")
    rule = _variant(tmp_path, "idx_bad_basis", f"""
        WITH base AS (SELECT * FROM ({body}))
        SELECT base.* REPLACE ('measured' AS available_basis) FROM base""")
    r = _build(tmp_path, rule)
    assert r.status is build.BuildStatus.GATE_FAILED
    eg2 = _gate(r, "EG2")
    assert eg2.status is GateStatus.FAIL and eg2.metrics["n_basis_outside_vocab"] == N_ROWS


def test_index_name을_뭉개면_EG3가_폐기한다(tmp_path: Path) -> None:
    """계열명은 조인 키다 — 정규화·번역으로 뭉개면 grain 3축이 있어도 키가 겹친다.

    손계산: 시장별로 2계열이 한 이름이 되므로 중복 그룹 = 4,094일 × 2시장 = 8,188.
    """
    body = rules_s02.INDEX_DAILY.sql_path.read_text(encoding="utf-8").strip().rstrip(";")
    rule = _variant(tmp_path, "idx_collapsed_name", f"""
        WITH base AS (SELECT * FROM ({body}))
        SELECT base.* REPLACE ('코스피' AS index_name) FROM base""")
    r = _build(tmp_path, rule)
    assert r.status is build.BuildStatus.GATE_FAILED
    eg3 = _gate(r, "EG3")
    assert eg3.status is GateStatus.FAIL and eg3.metrics["n_duplicate_keys"] == 8188
