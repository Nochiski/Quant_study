"""키움 6테이블 손계산 빌드 — 수급·공매도·외인·대차·마스터·샤드 (DESIGN v2.2 §4 키움).

픽스처 컬럼은 `survey_out/v2/kiwoom.*.json` 의 원장 컬럼 전수와 같다(순서 무관, 전부 TEXT).
`unit_scale` 선언 컬럼은 G4 골든 픽스처가 강제된다(§9) — 여기서는 임시 픽스처를 넘긴다.
서버 실측 픽스처가 필요한 목록은 보고서에 있다.
"""
import json
import sqlite3
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest
from stage import build, gates, model, rules, rules_kiwoom, snapshot

COLLECTED = "2026-08-23T15:23:29"        # UTC 무표기 → KST 2026-08-24

FLOW_COLS = ["ticker", "dt", "cur_prc", "pred_pre", "acc_trde_prica", "ind_invsr", "frgnr_invsr",
             "orgn", "fnnc_invt", "insrnc", "invtrt", "etc_fnnc", "bank", "penfnd_etc",
             "samo_fund", "natn", "etc_corp", "natfor", "src_api", "collected_at"]
SHORT_COLS = ["ticker", "dt", "close_pric", "pred_pre_sig", "pred_pre", "flu_rt", "trde_qty",
              "shrts_qty", "ovr_shrts_qty", "trde_wght", "shrts_trde_prica", "shrts_avg_pric",
              "src_api", "collected_at"]
FRGN_COLS = ["ticker", "dt", "close_pric", "pred_pre", "trde_qty", "chg_qty", "poss_stkcnt",
             "wght", "gain_pos_stkcnt", "frgnr_limit", "frgnr_limit_irds", "limit_exh_rt",
             "src_api", "collected_at"]
LEND_COLS = ["ticker", "dt", "dbrt_trde_cntrcnt", "dbrt_trde_rpy", "dbrt_trde_irds", "rmnd",
             "remn_amt", "src_api", "collected_at"]
MASTER_COLS = ["snap_date", "mrkt_tp", "code", "name", "listCount", "auditInfo", "regDay",
               "lastPrice", "state", "marketCode", "marketName", "upName", "upSizeName",
               "companyClassName", "orderWarning", "nxtEnable", "kind", "collected_at"]
SHARD_COLS = ["src_api", "ticker", "req_start", "req_end", "n_rows", "cap", "status", "first_dt",
              "last_dt", "collected_at", "next_cursor"]

# 투자자 13컬럼 = 백만원. 개인만 음수(순매도) — 부호가 값이므로 스케일 뒤에도 보존돼야 한다
_INV = ("-292", "12", "5", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10")
FLOW_ROWS = [
    ("005930", "20260820", "-262500", "-19000", "1234567") + _INV + ("ka10060", COLLECTED),
    ("0001A0", "20260820", "+51900", "+100", "0") + ("0",) * 13 + ("ka10060", COLLECTED),
]
SHORT_ROWS = [
    ("005930", "20260820", "-262500", "5", "-19000", "-6.75", "1234567", "1000", "50000",
     "+0.08", "262500", "262500", "ka10014", COLLECTED),
]
FRGN_ROWS = [
    ("005930", "20260820", "-262500", "-19000", "1234567", "-5000", "3000000000",
     "+52.35", "5000000000", "5969782550", "0", "+50.25", "ka10008", COLLECTED),
    # 소스 결함 3행(2010-05-12)의 재현: 보유주식수 음수 + 비중 −900% — abs 를 걸면 은폐된다
    ("000020", "20100512", "+1000", "+10", "100", "-1", "-900", "-900.00", "1000", "1000",
     "", "-900.00", "ka10008", COLLECTED),
]
LEND_ROWS = [
    ("005930", "20260820", "1000", "1200", "-200", "50000", "13", "ka20068", COLLECTED),
]
LEND_BROKEN = ("000020", "20260820", "1000", "1200", "-199", "50000", "13", "ka20068", COLLECTED)
MASTER_ROWS = [
    ("20260901", "0", "005930", "삼성전자", "0000005969782550", "정상", "19750611", "00262500",
     "증거금100%|신용가능|대용가능", "0", "KOSPI", "전기·전자", "대형주", "", "0", "Y", "Y",
     COLLECTED),
    ("20260901", "10", "000020", "관리종목사", "0000000010000000", "거래정지", "19991110",
     "00001000", "증거금100%|관리종목", "10", "KOSDAQ", "기계·장비", "소형주", "우량기업부", "2",
     "N", "Y", COLLECTED),
]
SHARD_ROWS = [
    ("ka10014", "005930", "20250101", "20260820", "372", "372", "truncated", "20250101",
     "20260820", COLLECTED, None),
    ("ka10014", "0001A0", "20250101", "20260820", "0", "372", "empty", None, "20260820",
     COLLECTED, None),
]


def _write(path: Path, tables: dict[str, tuple[list[str], Sequence[Sequence[object]]]]
           ) -> None:
    con = sqlite3.connect(path)
    for tbl, (cols, rows) in tables.items():
        con.execute(f"CREATE TABLE {tbl} ({', '.join(c + ' TEXT' for c in cols)})")
        con.executemany(f"INSERT INTO {tbl} VALUES ({','.join('?' * len(cols))})", rows)
    con.commit()
    con.close()


def _make_snapshot(tmp_path: Path, lend: Sequence[Sequence[object]] | None = None,
                   snapshot_id: str = "snap_kw") -> snapshot.Snapshot:
    d = tmp_path / "raw"
    d.mkdir(exist_ok=True)
    db = d / f"kiwoom_{snapshot_id}.db"
    _write(db, {
        "ka10060_investor_flows": (FLOW_COLS, FLOW_ROWS),
        "ka10014_short_selling": (SHORT_COLS, SHORT_ROWS),
        "ka10008_foreign_holdings": (FRGN_COLS, FRGN_ROWS),
        "ka20068_lending_balance": (LEND_COLS, lend if lend is not None else LEND_ROWS),
        "ka10099_stock_master": (MASTER_COLS, MASTER_ROWS),
        "ingest_shard": (SHARD_COLS, SHARD_ROWS),
    })
    return snapshot.make_snapshot({"kiwoom": db}, tmp_path / "snapshots",
                                  snapshot_id=snapshot_id)


@pytest.fixture
def snap(tmp_path: Path) -> snapshot.Snapshot:
    return _make_snapshot(tmp_path)


def _unit_fixtures(tmp_path: Path, name: str, key: dict[str, str],
                   expect: dict[str, str]) -> Path:
    """unit_scale 선언 컬럼 전부에 픽스처를 만든다 — 없으면 G4 가 즉시 실패한다(§9)."""
    rule = rules.RULES[name]
    fx = [{"key": key, "column": c.name, "expect": expect[c.name],
           "measured_sql": f"SELECT {c.src} FROM {rule.sources[0].table}",
           "measured_at": "2026-09-02"}
          for c in rule.columns if c.unit_scale is not None]
    path = tmp_path / f"fx_{name}.json"
    path.write_text(json.dumps(fx, ensure_ascii=False), encoding="utf-8")
    return path


def _build(name: str, snap: snapshot.Snapshot, tmp_path: Path, **kw: object
           ) -> build.BuildResult:
    return build.build_table(rules.RULES[name], snap, tmp_path / "stage", **kw)


def _read(tmp_path: Path, r: build.BuildResult, partitioned: bool = True
          ) -> duckdb.DuckDBPyConnection:
    root = tmp_path / "stage" / r.table / f"v={r.build_id}"
    glob = str(root / ("year=*/*.parquet" if partitioned else "*.parquet"))
    con = duckdb.connect()
    con.execute(f"CREATE VIEW t AS SELECT * FROM read_parquet('{glob}', hive_partitioning=true)")
    return con


def _failed(r: build.BuildResult) -> list[str]:
    return [f"{g.name}:{g.detail}" for g in r.gates if g.status is gates.GateStatus.FAIL]


# ── stg_flow_daily_kiwoom (ka10060) ───────────────────────────────────────────
def test_flow_rules_scale_only_the_thirteen_investor_columns() -> None:
    rule = rules.RULES["stg_flow_daily_kiwoom"]
    scaled = {c.name: c.unit_scale for c in rule.columns if c.unit_scale is not None}
    assert len(scaled) == 13 and set(scaled.values()) == {1_000_000}
    assert rule.column("volume_shr").src == "acc_trde_prica"    # 오표기 — 대금이 아니라 거래량
    assert rule.column("volume_shr").unit_scale is None
    assert rule.column("close_krw").sign == "abs"
    assert rule.lag_known is False                              # 수급 = 공표 시점 미상 (§6)


def test_flow_build_scales_investor_columns_and_keeps_the_sign(
    snap: snapshot.Snapshot, tmp_path: Path
) -> None:
    fx = _unit_fixtures(tmp_path, "stg_flow_daily_kiwoom",
                        {"ticker": "005930", "date": "2026-08-20"},
                        {"ind_invsr_krw": "-292000000", "frgnr_invsr_krw": "12000000",
                         "orgn_krw": "5000000", "fnnc_invt_krw": "1000000",
                         "insrnc_krw": "2000000", "invtrt_krw": "3000000",
                         "etc_fnnc_krw": "4000000", "bank_krw": "5000000",
                         "penfnd_etc_krw": "6000000", "samo_fund_krw": "7000000",
                         "natn_krw": "8000000", "etc_corp_krw": "9000000",
                         "natfor_krw": "10000000"})
    r = _build("stg_flow_daily_kiwoom", snap, tmp_path, fixtures_path=fx)
    assert r.ok, _failed(r)
    con = _read(tmp_path, r)
    assert con.execute("SELECT ind_invsr_krw, frgnr_invsr_krw, volume_shr FROM t "
                       "WHERE ticker = '005930'").fetchone() == (-292_000_000, 12_000_000, 1234567)
    assert next(g for g in r.gates if g.name == "G4").metrics["n_fixtures"] == 13


def test_flow_build_takes_abs_of_cur_prc_and_records_the_direction(
    snap: snapshot.Snapshot, tmp_path: Path
) -> None:
    fx = _unit_fixtures(tmp_path, "stg_flow_daily_kiwoom",
                        {"ticker": "0001A0", "date": "2026-08-20"},
                        dict.fromkeys(
                            (c.name for c in rules.RULES["stg_flow_daily_kiwoom"].columns
                             if c.unit_scale is not None), "0"))
    r = _build("stg_flow_daily_kiwoom", snap, tmp_path, fixtures_path=fx)
    assert r.ok, _failed(r)
    con = _read(tmp_path, r)
    assert con.execute("SELECT ticker, close_krw, close_krw_dir, pred_pre_krw FROM t "
                       "ORDER BY ticker").fetchall() == [
        ("0001A0", 51900, 1, 100), ("005930", 262500, -1, -19000)]


# ── stg_short_daily_kiwoom (ka10014) ──────────────────────────────────────────
def test_short_rules_keep_the_raw_overseas_quantity_without_a_valid_flag() -> None:
    rule = rules.RULES["stg_short_daily_kiwoom"]
    names = {c.name for c in rule.columns}
    assert "ovr_shrts_qty_shr" in names
    assert not [n for n in names if n.endswith("_valid")]   # equity 파생 — §1 1:1 위반 (§4)
    assert rule.column("shrts_trde_prica_krw").unit_scale == 1_000
    assert rule.column("trde_wght_pct").sign == "strip_plus"


def test_short_build_scales_trade_value_by_one_thousand(
    snap: snapshot.Snapshot, tmp_path: Path
) -> None:
    fx = _unit_fixtures(tmp_path, "stg_short_daily_kiwoom",
                        {"ticker": "005930", "date": "2026-08-20"},
                        {"shrts_trde_prica_krw": "262500000"})
    r = _build("stg_short_daily_kiwoom", snap, tmp_path, fixtures_path=fx)
    assert r.ok, _failed(r)
    con = _read(tmp_path, r)
    assert con.execute("SELECT shrts_trde_prica_krw, shrts_avg_pric, ovr_shrts_qty_shr, "
                       "trde_wght_pct, close_krw, close_krw_dir, flu_rt_pct FROM t"
                       ).fetchone() == (262_500_000, 262500, 50000, Decimal("0.08"), 262500,
                                        -1, Decimal("-6.75"))


# ── stg_foreign_daily (ka10008) ───────────────────────────────────────────────
def test_foreign_build_strips_plus_from_wght_but_keeps_the_negative_defect(
    snap: snapshot.Snapshot, tmp_path: Path
) -> None:
    r = _build("stg_foreign_daily", snap, tmp_path)
    assert r.ok, _failed(r)
    con = _read(tmp_path, r)
    assert con.execute("SELECT wght_pct, poss_stkcnt_shr, limit_exh_rt_pct, close_krw FROM t "
                       "WHERE ticker = '000020'").fetchone() == (-900.00, -900, -900.00, 1000)
    assert con.execute("SELECT wght_pct, poss_stkcnt_shr, miss_kind.frgnr_limit_irds FROM t "
                       "WHERE ticker = '005930'").fetchone() == (Decimal("52.35"),
                                                                3_000_000_000, None)
    assert con.execute("SELECT miss_kind.frgnr_limit_irds FROM t WHERE ticker = '000020'"
                       ).fetchone() == ("ledger_blank",)


# ── stg_lending_daily (ka20068) ───────────────────────────────────────────────
def test_lending_build_scales_the_balance_amount_and_holds_the_delta_identity(
    snap: snapshot.Snapshot, tmp_path: Path
) -> None:
    fx = _unit_fixtures(tmp_path, "stg_lending_daily",
                        {"ticker": "005930", "date": "2026-08-20"},
                        {"remn_amt_krw": "13000000"})
    r = _build("stg_lending_daily", snap, tmp_path, fixtures_path=fx)
    assert r.ok, _failed(r)
    con = _read(tmp_path, r)
    assert con.execute("SELECT remn_amt_krw, dbrt_trde_irds, rmnd FROM t").fetchone() == (
        13_000_000, -200, 50000)


def test_lending_gate_g3_catches_a_broken_delta_identity(tmp_path: Path) -> None:
    s = _make_snapshot(tmp_path, lend=[*LEND_ROWS, LEND_BROKEN], snapshot_id="snap_lend")
    fx = _unit_fixtures(tmp_path, "stg_lending_daily",
                        {"ticker": "005930", "date": "2026-08-20"},
                        {"remn_amt_krw": "13000000"})
    r = _build("stg_lending_daily", s, tmp_path, fixtures_path=fx)
    assert r.status is build.BuildStatus.GATE_FAILED
    g3 = next(g for g in r.gates if g.name == "G3")
    assert g3.metrics["lending_delta_violations"] == 1


# ── stg_master_daily (ka10099) ────────────────────────────────────────────────
def test_master_rules_are_a_snapshot_axis_without_current_suffixes() -> None:
    rule = rules.RULES["stg_master_daily"]
    assert rule.write_mode == "first_write_wins"            # INSERT OR IGNORE
    assert rule.partition_expr == "substr(snap_date, 1, 4)"
    assert not [c.name for c in rule.columns if c.name.endswith("_current")]   # §3 ⓑ
    assert rules_kiwoom.MASTER_COVERAGE_FROM == "2026-09-01"


def test_master_build_decomposes_state_and_audit_status(
    snap: snapshot.Snapshot, tmp_path: Path
) -> None:
    # G7 임계 상향: 상장일 19750611 이 내용일 축 하한 1990 에 걸려 셀 격리된다 — 빌더 결함
    # (stg_listing_daily.list_date 와 같은 원인, 보고서 참조). 2행 픽스처라 비율이 0.5 다.
    r = _build("stg_master_daily", snap, tmp_path, gate_thresholds={"G7": 0.6})
    assert r.ok, _failed(r)
    con = _read(tmp_path, r)
    got = con.execute("SELECT ticker, state_parts, is_admin_issue, is_trade_halt, "
                      "is_liquidation, list_shrs FROM t ORDER BY ticker").fetchall()
    assert got == [
        ("000020", ["증거금100%", "관리종목"], True, True, True, 10_000_000),
        ("005930", ["증거금100%", "신용가능", "대용가능"], False, False, False, 5_969_782_550)]
    assert next(g for g in r.gates if g.name == "G6").status is gates.GateStatus.SKIP
    assert con.execute("SELECT reg_date, miss_kind.reg_date FROM t WHERE ticker = '005930'"
                       ).fetchone() == (None, "out_of_range")


# ── stg_shards_kiwoom (ingest_shard) ──────────────────────────────────────────
def test_shards_build_keeps_the_request_window_as_the_natural_key(
    snap: snapshot.Snapshot, tmp_path: Path
) -> None:
    rule = rules.RULES["stg_shards_kiwoom"]
    assert rule.natural_key == ("src_api", "ticker", "req_start", "req_end")
    assert rule.available is model.AVAILABLE_NONE and rule.partition_class == "whole"
    r = _build("stg_shards_kiwoom", snap, tmp_path)
    assert r.ok, _failed(r)
    con = _read(tmp_path, r, partitioned=False)
    got = con.execute("SELECT ticker, status, n_rows, first_dt, next_cursor, available_date "
                      "FROM t ORDER BY ticker").fetchall()
    assert got == [("0001A0", "empty", 0, None, None, None),
                   ("005930", "truncated", 372, date(2025, 1, 1), None, None)]
