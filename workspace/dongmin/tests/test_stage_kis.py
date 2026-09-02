"""KIS 7테이블 선언 검증 — 손계산 픽스처로 빌드해 §4·§5·§6 규칙이 실제로 성립하는지 본다.

원장 스키마는 rules 가 참조하는 원장 컬럼 + 수집 메타로 재구성한다(survey v2 대조는 별도).
행은 2~3개만 두고 접기·부호·단위 스케일·결측 마커·키 결함·불변식만 겨냥한다.
"""
import json
import sqlite3
from datetime import date
from pathlib import Path

import duckdb
import pytest
from stage import build, gates, model, rules, rules_dart, rules_kis, rules_krx, rules_wise, snapshot

# 원장 실물에는 있으나 rules 가 선언하지 않는 수집 메타 (payload_exclude + req_*)
_META_COLS: dict[str, tuple[str, ...]] = {
    "stg_flow_split_daily": ("row_hash", "dup_seq", "collected_at", "req_d1", "req_d2",
                             "req_name"),
    "stg_short_daily_kis": ("row_hash", "dup_seq", "collected_at", "req_d1", "req_d2", "req_name"),
    "stg_loan_daily_kis": ("row_hash", "dup_seq", "collected_at", "req_d1", "req_d2", "req_name",
                           "req_mrkt_div_cls_code"),
    "stg_credit_daily": ("row_hash", "dup_seq", "collected_at", "req_d1", "req_d2", "req_name"),
    "stg_delisted_master": ("row_hash", "dup_seq", "collected_at", "req_d1", "req_d2", "req_name"),
    "stg_calls_kis": ("ts",),
    "stg_units_kis": ("ts",),
}
_META_VALUES: dict[str, str] = {
    "row_hash": "h", "dup_seq": "0", "collected_at": "2026-08-26T10:00:00",
    "req_d1": "20260801", "req_d2": "20260824", "req_name": "n", "req_mrkt_div_cls_code": "1",
    "ts": "2026-08-26 10:00:00",
}
_LEDGER_COLUMN_COUNT: dict[str, int] = {          # survey v2 실측 (`survey_out/v2/kis.*.json`)
    "stg_flow_split_daily": 110, "stg_short_daily_kis": 28, "stg_loan_daily_kis": 19,
    "stg_credit_daily": 33, "stg_delisted_master": 74, "stg_calls_kis": 8, "stg_units_kis": 7,
}


def _columns(rule: model.TableRule) -> list[str]:
    return [c.src for c in rule.columns] + list(_META_COLS[rule.name])


def _defaults(rule: model.TableRule) -> dict[str, str]:
    """전 컬럼이 값을 가진 정상 행. 키·길이 제약(expected_len)을 만족한다."""
    row = dict(_META_VALUES)
    for c in rule.columns:
        if c.expected_len is not None:
            row[c.src] = "0" * c.expected_len
        elif c.kind == model.KIND_DATE_YMD8:
            row[c.src] = "20260824"
        elif c.kind == model.KIND_NUMERIC:
            row[c.src] = "1"
        else:
            row[c.src] = "x"
    return {k: v for k, v in row.items() if k in set(_columns(rule))}


def _build(tmp_path: Path, rule: model.TableRule, rows: list[dict[str, str]],
           fixtures: list[dict[str, object]] | None = None,
           **kw: object) -> build.BuildResult:
    cols = _columns(rule)
    raw = tmp_path / "raw"
    raw.mkdir(exist_ok=True)
    db = raw / "kis.db"
    con = sqlite3.connect(db)
    table = rule.sources[0].table
    con.execute(f"CREATE TABLE {table} ({', '.join(f'{c} TEXT' for c in cols)})")
    con.executemany(f"INSERT INTO {table} VALUES ({', '.join('?' * len(cols))})",
                    [tuple(r[c] for c in cols) for r in rows])
    con.commit()
    con.close()
    snap = snapshot.make_snapshot({"kis": db}, tmp_path / "snapshots", snapshot_id="s")
    fp = None
    if fixtures is None:
        fixtures = _unit_scale_fixtures(rule, rows[0])
    if fixtures:
        fp = tmp_path / "fx.json"
        fp.write_text(json.dumps(fixtures), encoding="utf-8")
    return build.build_table(rule, snap, tmp_path / "stage", fixtures_path=fp, **kw)


def _unit_scale_fixtures(rule: model.TableRule,
                         row: dict[str, str]) -> list[dict[str, object]]:
    """§9: unit_scale 컬럼은 픽스처 없으면 G4 실패. 첫 행 원문 × 배수를 기대값으로 만든다."""
    key: dict[str, str] = {}
    for c in rule.columns:
        if not c.key:
            continue
        v = row[c.src]
        key[c.name] = f"{v[:4]}-{v[4:6]}-{v[6:]}" if c.kind == model.KIND_DATE_YMD8 else v
    return [{"key": key, "column": c.name,
             "expect": str(int(row[c.src]) * int(c.unit_scale or 1)),
             "measured_sql": f"-- 테스트 픽스처: {c.src} × {c.unit_scale}",
             "measured_at": "2026-09-03"}
            for c in rule.columns if c.unit_scale is not None]


def _read(tmp_path: Path, r: build.BuildResult) -> duckdb.DuckDBPyConnection:
    """채택 행만 읽는다 — `_reject/` 는 스키마가 달라 같은 glob 에 섞이면 안 된다."""
    con = duckdb.connect()
    root = tmp_path / "stage" / r.table / f"v={r.build_id}"
    rule = rules.RULES[r.table]
    glob = str(root / ("year=*" if rule.partition_class != "whole" else "") / "*.parquet")
    con.execute(f"CREATE VIEW t AS SELECT * FROM read_parquet('{glob}')")
    return con


def _failed(r: build.BuildResult) -> list[str]:
    return [f"{g.name}:{g.detail}" for g in r.gates if g.status is gates.GateStatus.FAIL]


# ── stg_flow_split_daily ─────────────────────────────────────────────────────
def test_flow_folds_pure_recollection_and_scales_million_won(tmp_path: Path) -> None:
    """중복 52,347(값 충돌 0)은 req_* 를 뺀 payload 투영에서 접힌다 — 대표 observed_date = min."""
    rule = rules_kis.STG_FLOW_SPLIT_DAILY
    a = _defaults(rule)
    b = {**a, "req_d2": "20260830", "collected_at": "2026-08-30T10:00:00"}   # 순수 재수집
    r = _build(tmp_path, rule, [a, b])
    assert r.ok, _failed(r)
    assert (r.n_src, r.n_rows, r.n_dedup, r.n_reject) == (2, 1, 1, 0)
    con = _read(tmp_path, r)
    got = con.execute("SELECT prsn_ntby_tr_pbmn_krw, observed_n, CAST(observed_date AS VARCHAR), "
                      "CAST(available_date AS VARCHAR), available_basis FROM t").fetchone()
    assert got == (1_000_000, 2, "2026-08-26", "2026-08-24", "default")


def test_flow_keeps_rows_that_differ_only_in_the_adjusted_price_flag(tmp_path: Path) -> None:
    """§5: 수정주가 플래그는 응답 의미를 바꾸므로 payload 에 남아 접기를 막는다."""
    rule = rules_kis.STG_FLOW_SPLIT_DAILY
    a = {**_defaults(rule), "req_fid_org_adj_prc": "0"}
    b = {**a, "req_fid_org_adj_prc": "1", "collected_at": "2026-08-30T10:00:00"}
    r = _build(tmp_path, rule, [a, b])
    assert r.ok, _failed(r)
    assert (r.n_rows, r.n_dedup) == (2, 0)
    con = _read(tmp_path, r)
    assert con.execute("SELECT fid_org_adj_prc FROM t ORDER BY 1").fetchall() == [("0",), ("1",)]


def test_flow_price_missing_markers_are_classified_not_counted_as_cast_loss(
        tmp_path: Path) -> None:
    """SPEC §2-10: 한 컬럼에 '' 1,835 + '0' 96 공존 — 둘 다 결측이고 캐스팅 실패가 아니다."""
    rule = rules_kis.STG_FLOW_SPLIT_DAILY
    a = {**_defaults(rule), "stck_clpr": "", "stck_oprc": "0"}
    r = _build(tmp_path, rule, [a])
    assert r.ok, _failed(r)
    con = _read(tmp_path, r)
    row = con.execute("SELECT close_krw, open_krw, miss_kind.close_krw, miss_kind.open_krw, "
                      "_src_flag FROM t").fetchone()
    assert row == (None, None, "ledger_blank", "ledger_zero", "ok")


# ── stg_short_daily_kis ──────────────────────────────────────────────────────
def test_short_acml_valid_marks_the_request_window_first_row(tmp_path: Path) -> None:
    """SPEC §2-6: acml_* 는 요청 창 첫 행에서 리셋된다. 같은 행 비교만으로 판정한다(LAG 금지)."""
    rule = rules_kis.STG_SHORT_DAILY_KIS
    first = {**_defaults(rule), "stck_bsop_date": "20100303", "req_d1": "20100303",
             "req_ticker": "010620"}
    later = {**first, "stck_bsop_date": "20100304"}
    r = _build(tmp_path, rule, [first, later])
    assert r.ok, _failed(r)
    con = _read(tmp_path, r)
    assert con.execute("SELECT CAST(date AS VARCHAR), acml_valid FROM t ORDER BY date"
                       ).fetchall() == [("2010-03-03", False), ("2010-03-04", True)]


def test_short_zero_average_price_is_missing_and_units_are_declared(tmp_path: Path) -> None:
    """공매도가 없던 날의 avrg_prc='0' 은 0원이 아니다. *_pbmn 은 원(스케일 변환 없음)."""
    rule = rules_kis.STG_SHORT_DAILY_KIS
    a = {**_defaults(rule), "avrg_prc": "0", "ssts_cntg_qty": "0", "ssts_tr_pbmn": "12345"}
    r = _build(tmp_path, rule, [a])
    assert r.ok, _failed(r)
    con = _read(tmp_path, r)
    assert con.execute("SELECT avrg_prc_krw, miss_kind.avrg_prc_krw, ssts_cntg_qty_shr, "
                       "ssts_tr_pbmn_krw FROM t").fetchone() == (None, "ledger_zero", 0, 12345)


def test_short_negative_accumulated_quantity_fails_the_invariant_gate(tmp_path: Path) -> None:
    """targets.INVARIANTS['kis_short_sale'] — 누적 공매도 음수는 G3 폐기형."""
    rule = rules_kis.STG_SHORT_DAILY_KIS
    r = _build(tmp_path, rule, [{**_defaults(rule), "acml_ssts_cntg_qty": "-5"}])
    assert r.status is build.BuildStatus.GATE_FAILED
    g3 = next(g for g in r.gates if g.name == "G3")
    assert g3.metrics["acml_ssts_qty_neg_violations"] == 1


# ── stg_loan_daily_kis ───────────────────────────────────────────────────────
def test_loan_keeps_negative_balance_and_scales_million_won(tmp_path: Path) -> None:
    """SPEC §2-3: rmnd_stcn 음수 2,691 은 소스 결함이라 abs 금지 · §2-4: rmnd_amt 백만원."""
    rule = rules_kis.STG_LOAN_DAILY_KIS
    a = {**_defaults(rule), "rmnd_stcn": "-2691", "rmnd_amt": "3"}
    fixtures = [{"key": {"ticker": "000000", "date": "2026-08-24"}, "column": "rmnd_amt_krw",
                 "expect": "3000000", "measured_sql": "-- 테스트 픽스처",
                 "measured_at": "2026-09-03"}]
    r = _build(tmp_path, rule, [a], fixtures=fixtures)
    assert r.ok, _failed(r)
    con = _read(tmp_path, r)
    assert con.execute("SELECT rmnd_stcn_shr, rmnd_amt_krw FROM t").fetchone() == (-2691, 3000000)


def test_loan_zero_price_literal_is_missing(tmp_path: Path) -> None:
    """원장 리터럴 '0.00'(281행) 도 결측 마커다 — SPEC §2-10, 빌더 K1 수정 후 동작."""
    rule = rules_kis.STG_LOAN_DAILY_KIS
    a = {**_defaults(rule), "stck_prpr": "0.00", "rmnd_amt": "1"}
    r = _build(tmp_path, rule, [a])
    assert r.ok, _failed(r)
    con = _read(tmp_path, r)
    assert con.execute("SELECT close_krw, miss_kind.close_krw FROM t").fetchone() == (
        None, "ledger_zero")


# ── stg_credit_daily ─────────────────────────────────────────────────────────
def test_credit_folds_req_d2_only_recollection_and_labels_price_basis(tmp_path: Path) -> None:
    """§5 실측: 중복 566,795 는 req_d2 만 상이 → payload 26컬럼 투영에서 전건 접힌다."""
    rule = rules_kis.STG_CREDIT_DAILY
    a = {**_defaults(rule), "deal_date": "20260824", "stlm_date": "20260826"}
    b = {**a, "req_d2": "20260830", "collected_at": "2026-08-30T10:00:00"}
    r = _build(tmp_path, rule, [a, b])
    assert r.ok, _failed(r)
    assert (r.n_rows, r.n_dedup) == (1, 0 + 1)
    con = _read(tmp_path, r)
    assert con.execute(
        "SELECT price_basis_close, price_basis_ohl, CAST(stlm_date AS VARCHAR), "
        "CAST(available_date AS VARCHAR), available_basis FROM t").fetchone() == (
        "adjusted_asof_collect", "raw", "2026-08-26", "2026-08-24", "default")


def test_credit_zero_close_is_missing_but_zero_volume_is_a_value(tmp_path: Path) -> None:
    """§4: stck_prpr='0' 561행 = KIS 결측. 반면 acml_vol='0' 은 무거래 실측(SPEC §2-10)."""
    rule = rules_kis.STG_CREDIT_DAILY
    a = {**_defaults(rule), "stck_prpr": "0", "acml_vol": "0"}
    r = _build(tmp_path, rule, [a])
    assert r.ok, _failed(r)
    con = _read(tmp_path, r)
    assert con.execute("SELECT close_krw, miss_kind.close_krw, acml_vol_shr, "
                       "miss_kind.acml_vol_shr FROM t").fetchone() == (None, "ledger_zero", 0, None)


def test_credit_settlement_before_deal_date_fails_the_invariant_gate(tmp_path: Path) -> None:
    """targets.INVARIANTS['kis_credit_balance'] — 8,970,999행 전건 stlm_date > deal_date."""
    rule = rules_kis.STG_CREDIT_DAILY
    a = {**_defaults(rule), "deal_date": "20260824", "stlm_date": "20260820"}
    r = _build(tmp_path, rule, [a])
    assert r.status is build.BuildStatus.GATE_FAILED
    g3 = next(g for g in r.gates if g.name == "G3")
    assert g3.metrics["stlm_before_deal_violations"] == 1


def test_credit_non_numeric_change_rate_is_counted_as_cast_failed(tmp_path: Path) -> None:
    """survey v2: prdy_ctrt 비숫자 465행('-'+영문 3자). G2 는 이것만 센다."""
    rule = rules_kis.STG_CREDIT_DAILY
    a = {**_defaults(rule), "prdy_ctrt": "-inf"}
    r = _build(tmp_path, rule, [a], gate_thresholds={"G2": 1.0})
    assert r.ok, _failed(r)
    con = _read(tmp_path, r)
    assert con.execute("SELECT prdy_ctrt_pct, miss_kind.prdy_ctrt_pct, _src_flag, "
                       "_cast_fail_cols FROM t").fetchone() == (
        None, "cast_failed", "partial", ["prdy_ctrt_pct"])
    assert next(g for g in r.gates if g.name == "G2").metrics["n_partial"] == 1


# ── stg_delisted_master ──────────────────────────────────────────────────────
def test_delisted_master_state_columns_carry_current_and_ticker_comes_from_pdno(
        tmp_path: Path) -> None:
    """§3 temporality ⓐ + §5 'KIS pdno 뒤 6자'. available_date 는 비부여(§6 판단)."""
    rule = rules_kis.STG_DELISTED_MASTER
    a = {**_defaults(rule), "req_ticker": "900010", "pdno": "00000A900010",
         "lstg_abol_dt": "20200731"}
    r = _build(tmp_path, rule, [a])
    assert r.ok, _failed(r)
    con = _read(tmp_path, r)
    assert con.execute("SELECT ticker, ticker_pdno, admn_item_current, kospi200_item_current, "
                       "CAST(lstg_abol_dt AS VARCHAR), available_date, available_basis "
                       "FROM t").fetchone() == ("900010", "900010", "x", "x", "2020-07-31",
                                                None, None)


def test_delisted_master_zero_date_marker_is_ledger_zero(tmp_path: Path) -> None:
    """KIS 날짜 결측 리터럴 '00000000'(dpsi_erlm_cncl_dt 141행 등)은 ledger_zero 다."""
    rule = rules_kis.STG_DELISTED_MASTER
    a = {**_defaults(rule), "dpsi_erlm_cncl_dt": "00000000"}
    r = _build(tmp_path, rule, [a])
    assert r.ok, _failed(r)
    con = _read(tmp_path, r)
    assert con.execute("SELECT dpsi_erlm_cncl_dt, miss_kind.dpsi_erlm_cncl_dt, _src_flag "
                       "FROM t").fetchone() == (None, "ledger_zero", "ok")


# ── stg_calls_kis / stg_units_kis ────────────────────────────────────────────
def test_calls_log_keeps_rows_without_a_request_window(tmp_path: Path) -> None:
    """d1·d2 는 비키 — kis_stock_info 조회 652행의 빈값은 ledger_blank 로 남고 행은 보존 (K2)."""
    rule = rules_kis.STG_CALLS_KIS
    ok_row = {**_defaults(rule), "verdict": "ok", "n_rows": "100", "code": "FHPST04",
              "d1": "20100101", "d2": "20100601"}
    empty_row = {**ok_row, "verdict": "empty", "n_rows": "0", "d1": "20100602",
                 "d2": "20101201"}
    info_row = {**ok_row, "d1": "", "d2": "", "code": "CTPF1002R/dom_st_search"}
    r = _build(tmp_path, rule, [ok_row, empty_row, info_row])
    assert r.ok, _failed(r)
    assert (r.n_src, r.n_rows, r.n_reject) == (3, 3, 0)
    con = _read(tmp_path, r)
    assert con.execute("SELECT code, window_from, miss_kind.window_from FROM t "
                       "WHERE verdict = 'ok' ORDER BY code").fetchall() == [
        ("CTPF1002R/dom_st_search", None, "ledger_blank"), ("FHPST04", date(2010, 1, 1), None)]
    assert next(g for g in r.gates if g.name == "G6").status is gates.GateStatus.SKIP


def test_calls_log_verdict_vocabulary_is_a_gate(tmp_path: Path) -> None:
    """SPEC §6 회귀 고정: 판정 어휘는 ok·empty 둘뿐(오류 0)."""
    rule = rules_kis.STG_CALLS_KIS
    bad = {**_defaults(rule), "verdict": "rate", "n_rows": "0", "code": "c"}
    r = _build(tmp_path, rule, [bad])
    assert r.status is build.BuildStatus.GATE_FAILED
    g3 = next(g for g in r.gates if g.name == "G3")
    assert g3.metrics["verdict_vocab_violations"] == 1


def test_units_log_folds_identical_same_day_entries(tmp_path: Path) -> None:
    """행 식별자가 없어 데이터 컬럼 전체가 자연키다 — 같은 내용은 접혀 observed_n 이 된다."""
    rule = rules_kis.STG_UNITS_KIS
    a = {**_defaults(rule), "status": "ok", "n_rows": "100", "d1": "20100101", "d2": "20100601"}
    b = {**a, "ts": "2026-08-26 18:00:00"}
    r = _build(tmp_path, rule, [a, b])
    assert r.ok, _failed(r)
    assert (r.n_rows, r.n_dedup) == (1, 1)
    con = _read(tmp_path, r)
    assert con.execute("SELECT observed_n, available_date FROM t").fetchone() == (2, None)


# ── 선언 자체의 성질 ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("rule", rules_kis.TABLES, ids=lambda r: r.name)
def test_every_kis_table_declares_the_whole_ledger_row(rule: model.TableRule) -> None:
    """컬럼 전수 원칙(§5) — 선언 + 수집 메타 = 원장 컬럼 수(survey v2 실측)."""
    assert len(_columns(rule)) == _LEDGER_COLUMN_COUNT[rule.name]
    assert rule.write_mode == "append_only"                       # §3 KIS = row_hash PK
    assert rule.observed_src == ("ts" if "log" in rule.sources[0].table else "collected_at")
    assert rule.lag_known is False                                # §6 주의 — 공표 시점 미상


def test_kis_partition_declarations_match_the_design_table() -> None:
    """§4 파티션 선언 표와 글자 그대로 일치해야 한다 (G0)."""
    got = {t.name: (t.partition_class, t.partition_expr) for t in rules_kis.TABLES}
    assert got == {
        "stg_flow_split_daily": ("date_axis", "substr(stck_bsop_date, 1, 4)"),
        "stg_short_daily_kis": ("date_axis", "substr(stck_bsop_date, 1, 4)"),
        "stg_loan_daily_kis": ("date_axis", "substr(bsop_date, 1, 4)"),
        "stg_credit_daily": ("date_axis", "substr(deal_date, 1, 4)"),
        "stg_delisted_master": ("whole", None),
        "stg_calls_kis": ("whole", None),
        "stg_units_kis": ("whole", None),
    }


def test_kis_tables_are_in_the_registry() -> None:
    names = {t.name for mod in (rules_krx, rules_kis, rules_dart, rules_wise) for t in mod.TABLES}
    assert names <= set(rules.RULES)   # 다른 소스 모듈은 별도 PR
    assert {t.name for t in rules_kis.TABLES} == set(_LEDGER_COLUMN_COUNT)
