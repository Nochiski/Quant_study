"""DART DS005 이벤트 15테이블 — 손계산 픽스처 (DESIGN v2.2 §4 DART·§6·§9, SPEC §2-14·§2-19).

원장 실물 컬럼 = 선언 컬럼 + 수집 메타 4(`row_hash`·`req_corp_code`·`dup_seq`·`collected_at`).
개수는 `_LEDGER_COLUMNS` 로 회귀 고정한다 — survey v2 전수 측정치(`survey_out/v2/dart.*.json`)이며
tsstk_aq 29·tsstk_dp 32·piic 19·fric 19·cvbd_is 46·ctrcvs_bgrq 9 는 census 실측 필드수와도 일치한다.
"""
import sqlite3
from pathlib import Path

import duckdb
import pytest
from stage import build, gates, model, rules, rules_dart_events, snapshot

_META = ("row_hash", "req_corp_code", "dup_seq", "collected_at")
_DISC_COLS = ("row_hash", "rcept_no", "rcept_dt", "req_page_no", "dup_seq", "collected_at")
_COLLECTED = "2026-09-01T02:10:00"      # 원장 시각은 UTC 무표기 → +9h = KST 2026-09-01
R_OLD = "20160108000502"                # DESIGN §9 의 tsstk_dp 연도 오타 행 접수번호
R_NEW = "20250311001085"
R_UNMAPPED = "20200101000001"           # 참조표에 없는 접수번호 → available_basis unknown
_MAP = {R_OLD: "20160108", R_NEW: "20250311"}

# 선언 컬럼 수 회귀 고정 (survey v2 컬럼 − 수집 메타 4)
_LEDGER_COLUMNS = {
    "stg_event_tsstk_aq": 29, "stg_event_piic": 19, "stg_event_cvbd_is": 46,
    "stg_event_fric": 19, "stg_event_pifric": 34, "stg_event_cr": 36,
    "stg_event_cmp_mg": 69, "stg_event_cmp_dv": 49, "stg_event_cmp_dvmg": 90,
    "stg_event_stk_extr": 56, "stg_event_tsstk_dp": 32, "stg_event_ctrcvs_bgrq": 9,
    "stg_event_df_ocr": 9, "stg_event_ds_rs_ocr": 9, "stg_event_bnk_mngt_pcbg": 9,
}
EVENT_NAMES = tuple(_LEDGER_COLUMNS)


def _row(rule: model.TableRule, rcept_no: str, collected: str = _COLLECTED,
         **over: str) -> dict[str, str]:
    """원장 한 행. 지정하지 않은 컬럼은 DS005 의 실제 결측 마커 `'-'` 로 채운다."""
    row = {c.src: "-" for c in rule.columns}
    row.update(rcept_no=rcept_no, corp_code="00126380", corp_cls="Y", corp_name="삼성전자")
    row.update(over)
    row.update(row_hash=f"h{rcept_no}{collected}{len(over)}", req_corp_code=row["corp_code"],
               dup_seq="0", collected_at=collected)
    return row


def _disc(rcept_no: str, rcept_dt: str) -> dict[str, str]:
    return {"row_hash": f"d{rcept_no}", "rcept_no": rcept_no, "rcept_dt": rcept_dt,
            "req_page_no": "1", "dup_seq": "0", "collected_at": _COLLECTED}


def _create(con: sqlite3.Connection, table: str, cols: tuple[str, ...],
            rows: list[dict[str, str]]) -> None:
    con.execute(f"CREATE TABLE {table} ({', '.join(c + ' TEXT' for c in cols)})")
    con.executemany(f"INSERT INTO {table} VALUES ({','.join('?' * len(cols))})",
                    [tuple(r[c] for c in cols) for r in rows])


def _snap(tmp_path: Path, tag: str, rule: model.TableRule,
          rows: list[dict[str, str]]) -> snapshot.Snapshot:
    d = tmp_path / f"raw_{tag}"
    d.mkdir()
    con = sqlite3.connect(d / "dart.db")
    _create(con, "dart_disclosure", _DISC_COLS, [_disc(k, v) for k, v in _MAP.items()])
    _create(con, rule.sources[0].table, tuple(c.src for c in rule.columns) + _META, rows)
    con.commit()
    con.close()
    return snapshot.make_snapshot({"dart": d / "dart.db"}, tmp_path / f"snapshots_{tag}",
                                  snapshot_id=tag)


def _build(rule: model.TableRule, snap: snapshot.Snapshot, tmp_path: Path,
           **kw: object) -> build.BuildResult:
    """참조표(stg_rcept_dt_map)를 먼저 커밋해야 lookup available 이 성립한다 (§10 S1b→S2)."""
    stage = tmp_path / "stage"
    m = build.build_table(rules.RULES["stg_rcept_dt_map"], snap, stage)
    if not m.ok:
        raise AssertionError(f"reference table build failed: {m.gates}")
    return build.build_table(rule, snap, stage, **kw)


def _read(tmp_path: Path, r: build.BuildResult) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    glob = str(tmp_path / "stage" / r.table / f"v={r.build_id}" / "**" / "*.parquet")
    con.execute(f"CREATE VIEW t AS SELECT * FROM read_parquet('{glob}', hive_partitioning=true)")
    return con


def _gate(r: build.BuildResult, name: str) -> gates.GateResult:
    return next(g for g in r.gates if g.name == name)


def _failed(r: build.BuildResult) -> list[str]:
    return [g.name + ":" + g.detail for g in r.gates if g.status is gates.GateStatus.FAIL]


# ── 선언 ───────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("name", EVENT_NAMES)
def test_every_event_table_shares_the_ds005_skeleton(name: str) -> None:
    rule = rules.RULES[name]
    assert rule.natural_key == ("rcept_no",) and rule.key_unique is True
    assert rule.partition_class == "receipt_axis"
    assert rule.partition_expr == "substr(rcept_no, 1, 4)" and rule.partition_src == "rcept_no"
    assert rule.observed_src == "collected_at" and rule.write_mode == "append_only"
    assert rule.fanout == 1 and rule.lag_known is False
    assert rule.payload_exclude == ("row_hash", "dup_seq", "collected_at")
    assert rule.available.kind == "lookup" and rule.available.table == "stg_rcept_dt_map"
    assert rule.available.lookup_value == "rcept_dt"
    assert rule.column("rcept_no").expected_len == 14 and rule.column("rcept_no").key is True
    assert rule.column("corp_code").expected_len == 8
    assert rule.column("corp_cls_current").kind == model.KIND_TEXT   # §3 temporality ⓐ
    assert len(rule.columns) == _LEDGER_COLUMNS[name]


def test_registry_exposes_the_fifteen_ds005_tables() -> None:
    assert {t.name for t in rules_dart_events.TABLES} == set(EVENT_NAMES)
    assert set(EVENT_NAMES) <= set(rules.RULES)


def test_ratio_columns_carry_a_unit_suffix_and_amounts_do_not() -> None:
    """§5 단위 접미사 — 비율만 `_pct`/`_ratio`, 단위 미측정인 금액·주식수는 접미사 금지."""
    cvbd = rules.RULES["stg_event_cvbd_is"]
    assert cvbd.column("cvisstk_tisstk_vs_pct").kind == model.KIND_NUMERIC
    assert cvbd.column("bd_intr_ex_pct").decimal_type == "DECIMAL(7,3)"   # survey (5,3) + 여유 2
    assert cvbd.column("bd_fta").name == "bd_fta"                        # 권면총액 — 단위 미측정
    assert rules.RULES["stg_event_cr"].column("cr_rt_ostk_pct").decimal_type == "DECIMAL(15,10)"
    fric = rules.RULES["stg_event_fric"]
    assert fric.column("nstk_ascnt_ps_ostk_ratio").kind == model.KIND_NUMERIC   # 1주당 배정주식수
    assert all(c.unit_scale is None for n in EVENT_NAMES for c in rules.RULES[n].columns)


def test_date_kinds_follow_the_measured_format_per_column() -> None:
    """DS005 는 한글 단일 포맷이 아니다 — piic·pifric 의 공매도 기간만 YYYYMMDD (survey n_ymd8)."""
    piic = rules.RULES["stg_event_piic"]
    assert piic.column("ssl_bgd").kind == model.KIND_DATE_YMD8
    assert piic.column("ssl_edd").kind == model.KIND_DATE_YMD8
    assert rules.RULES["stg_event_pifric"].column("ssl_bgd").kind == model.KIND_DATE_YMD8
    assert rules.RULES["stg_event_cvbd_is"].column("pymd").kind == model.KIND_DATE_KOREAN
    # 기간표기 `'… ~ …'` 는 날짜로 파싱하지 않는다 (§4)
    assert rules.RULES["stg_event_bnk_mngt_pcbg"].column("mngt_pd").kind == model.KIND_TEXT


# ── 빌드: 15테이블 공통 ────────────────────────────────────────────────────────
@pytest.mark.parametrize("name", EVENT_NAMES)
def test_event_table_builds_two_rows_with_derived_available_date(
    name: str, tmp_path: Path
) -> None:
    rule = rules.RULES[name]
    snap = _snap(tmp_path, "s", rule, [_row(rule, R_OLD), _row(rule, R_NEW)])
    r = _build(rule, snap, tmp_path)
    assert r.ok, _failed(r)
    assert (r.n_src, r.n_rows, r.n_dedup, r.n_reject) == (2, 2, 0, 0)
    con = _read(tmp_path, r)
    got = con.execute("SELECT rcept_no, available_date, available_basis, observed_date, year, "
                      "corp_cls_current, corp_name_current FROM t ORDER BY rcept_no").fetchall()
    assert [(g[0], str(g[1]), g[2], str(g[3]), g[4]) for g in got] == [
        (R_OLD, "2016-01-08", "derived", "2026-09-01", 2016),
        (R_NEW, "2025-03-11", "derived", "2026-09-01", 2025)]
    assert got[0][5] == "Y" and got[0][6] == "삼성전자"
    assert _gate(r, "G2").metrics["n_partial"] == 0        # survey: 숫자 컬럼 비숫자 0
    assert _gate(r, "G6").status is gates.GateStatus.PASS  # append_only 판본 보존
    assert _gate(r, "G4").status is gates.GateStatus.SKIP  # unit_scale 컬럼 없음


# ── 값 규칙: 날짜·결측·숫자 ────────────────────────────────────────────────────
def test_tsstk_dp_isolates_the_2106_typo_cell_and_keeps_the_row(tmp_path: Path) -> None:
    """G7 행 격리형 — 내용일 축 [1990, 현재+40] 밖은 셀만 NULL, 행은 남는다 (DESIGN §9 ⑧)."""
    rule = rules.RULES["stg_event_tsstk_dp"]
    rows = [_row(rule, R_OLD, dpprpd_bgd="2106년 01월 08일", dp_dd="2016년 01월 08일"),
            _row(rule, R_NEW, dpprpd_bgd="2025년 03월 11일")]
    r = _build(rule, _snap(tmp_path, "dp", rule, rows), tmp_path, gate_thresholds={"G7": 1.0})
    assert r.ok, _failed(r)
    assert r.n_rows == 2 and r.n_reject == 0
    con = _read(tmp_path, r)
    bad = con.execute("SELECT dpprpd_bgd, miss_kind.dpprpd_bgd, dp_dd, _src_flag FROM t "
                      f"WHERE rcept_no = '{R_OLD}'").fetchone()
    assert bad is not None
    assert bad[0] is None and bad[1] == "out_of_range" and str(bad[2]) == "2016-01-08"
    assert bad[3] == "ok"                                  # 캐스팅 실패가 아니다 (G2 무관)
    g7 = _gate(r, "G7")
    assert g7.metrics["n_out_of_range_cells"] == 1 and g7.metrics["n_out_of_range_rows"] == 0


def test_cvbd_dash_marker_korean_date_and_comma_amount(tmp_path: Path) -> None:
    """`'-'` 결측(pymd 229행 실재) · 만기 2053 정상 · 콤마 금액 · 비율 `_pct` 캐스팅."""
    rule = rules.RULES["stg_event_cvbd_is"]
    rows = [_row(rule, R_NEW, pymd="-", bd_mtd="2053년 03월 26일",
                 bd_fta="2,467,500,000,000", cv_rt="100.00", bd_intr_ex="0.000",
                 cvisstk_tisstk_vs="12.3456", bd_tm="31"),
            _row(rule, R_OLD)]
    r = _build(rule, _snap(tmp_path, "cvbd", rule, rows), tmp_path)
    assert r.ok, _failed(r)
    con = _read(tmp_path, r)
    got = con.execute("SELECT pymd, miss_kind.pymd, bd_mtd, bd_fta, cv_rt_pct, bd_intr_ex_pct, "
                      f"cvisstk_tisstk_vs_pct, bd_tm FROM t WHERE rcept_no = '{R_NEW}'"
                      ).fetchone()
    assert got is not None
    assert got[0] is None and got[1] == "ledger_dash"
    assert str(got[2]) == "2053-03-26"                     # 만기 2053 은 범위 안 — 격리 금지
    assert str(got[3]) == "2467500000000" and str(got[4]) == "100.00"
    assert str(got[5]) == "0.000" and str(got[6]) == "12.3456"
    assert got[7] == "31"                                  # 회차는 비숫자 9건 실재 → TEXT 원문
    assert _gate(r, "G7").metrics["n_out_of_range"] == 0


def test_piic_parses_the_short_sale_window_as_yyyymmdd(tmp_path: Path) -> None:
    rule = rules.RULES["stg_event_piic"]
    rows = [_row(rule, R_OLD, ssl_at="Y", ssl_bgd="20161102", ssl_edd="20161215",
                 nstk_ostk_cnt="1,000,000"),
            _row(rule, R_NEW)]
    r = _build(rule, _snap(tmp_path, "piic", rule, rows), tmp_path)
    assert r.ok, _failed(r)
    con = _read(tmp_path, r)
    got = con.execute("SELECT ssl_at, ssl_bgd, ssl_edd, nstk_ostk_cnt, miss_kind.ssl_bgd FROM t "
                      f"WHERE rcept_no = '{R_OLD}'").fetchone()
    assert got is not None
    assert got[0] == "Y" and str(got[1]) == "2016-11-02" and str(got[2]) == "2016-12-15"
    assert str(got[3]) == "1000000" and got[4] is None


# ── available_date · 접기 · 불변식 ─────────────────────────────────────────────
def test_rcept_map_miss_leaves_available_date_null_and_basis_unknown(tmp_path: Path) -> None:
    """`rcept_no[:8]` 폴백은 v2.2 에서 폐기됐다 — 미스는 NULL (§6)."""
    rule = rules.RULES["stg_event_ctrcvs_bgrq"]
    rows = [_row(rule, R_OLD, rqd="2016년 01월 08일"), _row(rule, R_UNMAPPED)]
    r = _build(rule, _snap(tmp_path, "ctr", rule, rows), tmp_path)
    assert r.ok, _failed(r)
    con = _read(tmp_path, r)
    miss = con.execute("SELECT available_date, available_basis FROM t "
                       f"WHERE rcept_no = '{R_UNMAPPED}'").fetchone()
    assert miss == (None, "unknown")
    assert _gate(r, "G0").metrics["rcept_map_miss"] == 1


def test_identical_payload_recollection_folds_and_keeps_the_first_observed_date(
    tmp_path: Path
) -> None:
    """§5 중복 — payload 투영이 같으면 접고 대표 observed_date 는 min, observed_n 에 행수."""
    rule = rules.RULES["stg_event_df_ocr"]
    rows = [_row(rule, R_OLD, df_amt="1,500,000,000", dfd="2016년 01월 08일"),
            _row(rule, R_OLD, collected="2026-09-02T02:10:00", df_amt="1,500,000,000",
                 dfd="2016년 01월 08일"),
            _row(rule, R_NEW)]
    r = _build(rule, _snap(tmp_path, "df", rule, rows), tmp_path)
    assert r.ok, _failed(r)
    assert (r.n_src, r.n_dedup, r.n_rows) == (3, 1, 2)
    con = _read(tmp_path, r)
    got = con.execute("SELECT observed_n, observed_date, df_amt FROM t "
                      f"WHERE rcept_no = '{R_OLD}'").fetchone()
    assert got is not None
    assert got[0] == 2 and str(got[1]) == "2026-09-01" and str(got[2]) == "1500000000"


def test_cr_invariant_flags_a_reduction_that_increases_shares(tmp_path: Path) -> None:
    """targets.INVARIANTS dart_cr_decsn — 감자 후 발행총수 > 감자 전이면 G3 실패."""
    rule = rules.RULES["stg_event_cr"]
    rows = [_row(rule, R_OLD, bfcr_tisstk_ostk="149,840,002", atcr_tisstk_ostk="142,487,104"),
            _row(rule, R_NEW, bfcr_tisstk_ostk="1,000", atcr_tisstk_ostk="2,000")]
    r = _build(rule, _snap(tmp_path, "cr", rule, rows), tmp_path)
    assert r.status is build.BuildStatus.GATE_FAILED
    assert _gate(r, "G3").metrics["cr_shares_increase_violations"] == 1


def test_duplicate_rcept_no_with_different_payload_fails_key_uniqueness(tmp_path: Path) -> None:
    """재수집으로 corp_name 등이 바뀌면 같은 rcept_no 가 두 행이 되어 G3 가 테이블을 폐기한다."""
    rule = rules.RULES["stg_event_ds_rs_ocr"]
    rows = [_row(rule, R_OLD, ds_rs="해산사유 발생"),
            _row(rule, R_OLD, collected="2026-09-02T02:10:00", corp_name="삼성전자(정정)")]
    r = _build(rule, _snap(tmp_path, "ds", rule, rows), tmp_path)
    assert r.status is build.BuildStatus.GATE_FAILED
    assert _gate(r, "G3").metrics["key_uniqueness_violations"] == 1
    assert _gate(r, "G6").status is gates.GateStatus.PASS   # 관측일이 달라 판본 보존은 통과
