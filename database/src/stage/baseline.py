"""baseline.json (STAGE_DESIGN §9) — ★ 회귀 고정값과 테이블별 게이트 임계.

게이트 상수는 코드가 아니라 여기서 읽는다.

측정은 스냅샷 원장(READ_ONLY ATTACH) 위에서 duckdb 로 한다. 항목마다 SQL 을 그대로 실어
재현 가능하게 둔다(v2.1 의 상수 3개가 술어 없이 적혀 원장과 달랐던 사고의 재발 방지).
갱신은 사람이 승인한다.

  PYTHONPATH=src python -m stage.baseline --snapshot-id <id> [--out data/stage/baseline.json]

전량 재측정은 그날 원장 상태로 24지표를 통째로 느슨하게 만든다. 한 지표만 고쳐야 할 때는
`--only` 를 쓴다 — 지정한 것만 재고 나머지 값·`measured_at` 은 보존한다 (DEFECT-B02).

  PYTHONPATH=src python -m stage.baseline --snapshot-id <id> \
      --only stg_price_daily.volume_match_ratio --note "결정 11 술어로 재측정"
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from .rules_krx import CROSS_JOIN_PREDICATE_SQL
from .snapshot import Snapshot, load_snapshot


@dataclass(frozen=True)
class Metric:
    table: str
    metric: str
    db: str            # 원장 alias (provenance)
    sql: str           # duckdb SQL — 원장은 alias.table 로 참조, 단일 값 1행
    growing: bool      # ★ 수집 중(성장) 여부 — 빌드마다 값이 달라져도 게이트 실패가 아니다


_PRICE_COLS = "ISU_CD, BAS_DD, TDD_CLSPRC, TDD_OPNPRC, ACC_TRDVOL"
_PRICE_UNION = (f"(SELECT {_PRICE_COLS} FROM krx.krx_stk_bydd_trd "
                f"UNION ALL SELECT {_PRICE_COLS} FROM krx.krx_ksq_bydd_trd)")
# G9 교차 조인 술어는 **게이트와 같은 상수**를 쓴다 (DEFECT-B02 — rules_krx.CROSS_JOIN_PREDICATE_SQL).
# 여기가 시각 조건 없는 옛 술어로 남아 있던 탓에 baseline 과 게이트가 다른 모집단을 셌다.
_PRICE_JOIN = (f"FROM {_PRICE_UNION} p JOIN kiwoom.ka10060_investor_flows f ON "
               + CROSS_JOIN_PREDICATE_SQL.format(
                   kw="f", ticker="p.ISU_CD", dt8="p.BAS_DD",
                   day="strptime(p.BAS_DD, '%Y%m%d')"))
_OHL_ZERO = "TDD_OPNPRC = '0' AND TRY_CAST(ACC_TRDVOL AS BIGINT) > 0"
_SHARES_NUM = ("etc", "tesstk_co", "isu_stock_totqy", "redc", "now_to_isu_stock_totqy",
               "profit_incnr", "now_to_dcrs_stock_totqy", "istc_totqy", "distb_stock_co",
               "rdmstk_repy")   # dart_shares 실명 (survey v2)
_SHARES_SQL = "SELECT " + " + ".join(    # 결측 마커('', '-')는 비숫자가 아니다 (§5)
    f"count(*) FILTER (WHERE {c} IS NOT NULL AND {c} NOT IN ('', '-') AND "
    f"TRY_CAST(replace({c}, ',', '') AS DECIMAL(38,4)) IS NULL)" for c in _SHARES_NUM
) + " FROM dart.dart_shares"


def _m(table: str, metric: str, db: str, sql: str, growing: bool = False) -> Metric:
    return Metric(table, metric, db, sql, growing)


METRICS: tuple[Metric, ...] = (
    _m("stg_price_daily", "ohl_zero_vol_pos_rows", "krx",
       f"SELECT count(*) FROM {_PRICE_UNION} WHERE {_OHL_ZERO}"),
    _m("stg_price_daily", "close_match_ratio", "krx+kiwoom",
       "SELECT count(*) FILTER (WHERE TRY_CAST(p.TDD_CLSPRC AS BIGINT) = "
       f"abs(TRY_CAST(f.cur_prc AS BIGINT))) / count(*) {_PRICE_JOIN}"),
    _m("stg_price_daily", "volume_match_ratio", "krx+kiwoom",
       "SELECT count(*) FILTER (WHERE TRY_CAST(p.ACC_TRDVOL AS BIGINT) = "
       f"TRY_CAST(f.acc_trde_prica AS BIGINT)) / count(*) {_PRICE_JOIN}"),
    _m("stg_price_daily", "close_joined", "krx+kiwoom", f"SELECT count(*) {_PRICE_JOIN}"),
    _m("stg_etf_price_daily", "ohl_zero_vol_pos_rows", "krx",
       f"SELECT count(*) FROM krx.krx_etf_bydd_trd WHERE {_OHL_ZERO}"),
    _m("stg_listing_daily", "parval_non_numeric_rows", "krx",
       "SELECT count(*) FROM (SELECT PARVAL FROM krx.krx_stk_isu_base_info UNION ALL "
       "SELECT PARVAL FROM krx.krx_ksq_isu_base_info) WHERE PARVAL <> '' "
       "AND TRY_CAST(replace(PARVAL, ',', '') AS DECIMAL(18,4)) IS NULL"),
    _m("stg_foreign_daily", "poss_stkcnt_negative_rows", "kiwoom",
       "SELECT count(*) FROM kiwoom.ka10008_foreign_holdings "
       "WHERE TRY_CAST(poss_stkcnt AS BIGINT) < 0"),
    _m("stg_loan_daily_kis", "rmnd_stcn_negative_rows", "kis",
       "SELECT count(*) FROM kis.kis_loan_trans WHERE TRY_CAST(rmnd_stcn AS BIGINT) < 0"),
    _m("stg_credit_daily", "whol_loan_gvrt_negative_rows", "kis",
       "SELECT count(*) FROM kis.kis_credit_balance "
       "WHERE TRY_CAST(whol_loan_gvrt AS DOUBLE) < 0"),
    _m("stg_credit_daily", "whol_stln_gvrt_negative_rows", "kis",
       "SELECT count(*) FROM kis.kis_credit_balance "
       "WHERE TRY_CAST(whol_stln_gvrt AS DOUBLE) < 0"),
    _m("stg_credit_daily", "whol_loan_gvrt_min", "kis",
       "SELECT min(TRY_CAST(whol_loan_gvrt AS DOUBLE)) FROM kis.kis_credit_balance"),
    _m("stg_credit_daily", "whol_loan_gvrt_max", "kis",
       "SELECT max(TRY_CAST(whol_loan_gvrt AS DOUBLE)) FROM kis.kis_credit_balance"),
    _m("stg_credit_daily", "dup_groups", "kis",
       "SELECT count(*) FROM (SELECT req_ticker, deal_date, count(*) c "
       "FROM kis.kis_credit_balance GROUP BY 1, 2 HAVING c > 1)"),
    _m("stg_disclosure", "n_rows", "dart", "SELECT count(*) FROM dart.dart_disclosure"),
    _m("stg_disclosure", "dup_groups", "dart",
       "SELECT count(*) FROM (SELECT rcept_no, count(*) c FROM dart.dart_disclosure "
       "GROUP BY 1 HAVING c > 1)"),
    _m("stg_disclosure", "correction_rows", "dart",
       "SELECT count(*) FROM dart.dart_disclosure WHERE report_nm LIKE '[%정정]%'"),
    _m("stg_fin", "n_rows", "dart", "SELECT count(*) FROM dart.dart_fin_raw"),
    _m("stg_fin", "non_krw_rows", "dart",
       "SELECT count(*) FROM dart.dart_fin_raw WHERE currency <> 'KRW'"),
    _m("stg_fin", "bsns_year_mismatch_rows", "dart",
       "SELECT count(*) FROM dart.dart_fin_raw WHERE bsns_year <> req_bsns_year"),
    _m("stg_fin", "account_std_false_rows", "dart",
       "SELECT count(*) FROM dart.dart_fin_raw WHERE account_id = '-표준계정코드 미사용-'"),
    _m("stg_rcept_dt_map", "fin_rcept_map_miss", "dart",
       "SELECT count(*) FROM (SELECT DISTINCT rcept_no FROM dart.dart_fin_raw) f "
       "WHERE NOT EXISTS (SELECT 1 FROM dart.dart_disclosure d WHERE d.rcept_no = f.rcept_no)"),
    _m("stg_doc_index", "n_rows", "dart", "SELECT count(*) FROM dart.doc_store", growing=True),
    _m("stg_audit", "adt_opinion_distinct", "dart",
       "SELECT count(DISTINCT adt_opinion) FROM dart.dart_audit"),
    _m("stg_shares", "non_numeric_cells", "dart", _SHARES_SQL),
    _m("stg_capital", "isu_dcrs_de_year_typo_rows", "dart",
       "SELECT count(*) FROM dart.dart_capital "
       "WHERE TRY_CAST(substr(isu_dcrs_de, 1, 4) AS INTEGER) > 2066"),
    _m("stg_event_tsstk_dp", "dpprpd_bgd_typo_rows", "dart",
       "SELECT count(*) FROM dart.dart_tsstk_dp_decsn WHERE dpprpd_bgd LIKE '2106년%'"),
    _m("stg_consensus_monthly", "n_blobs", "wise",
       "SELECT count(*) FROM wise.ws_raw WHERE ep IN ('cF5001', 'cF5002')", growing=True),
    _m("stg_analyst_summary", "n_blobs", "wise",
       "SELECT count(*) FROM wise.ws_raw WHERE ep = 'c1010001'", growing=True),
)

# 게이트가 읽는 **고정 상수** — 측정값이 아니라 선언값이다. (값, 근거).
# 측정으로 덮이면 안 되므로 `measure()`·`--only` 둘 다 이 선언을 그대로 싣는다.
CONSTANTS: dict[str, dict[str, tuple[float, str]]] = {
    "stg_price_daily": {"volume_match_ratio_tol": (
        5e-5,
        ("G9 허용폭 (결정 11 후속, 09-19 감사 DEFECT-B02). 기준값 7,595,291행 기준 ≈380행 — "
         "여유 7행이던 상태에서 하루치 KRX↔키움 거래량 불일치 증가가 stg_price_daily 를 "
         "폐기시키는 것을 막는다. 저하가 이 폭을 넘으면 그때는 진짜 회귀다"))},
}

# 테이블별 게이트 임계 — (값, 근거). 없는 테이블은 gates.DEFAULT_THRESHOLDS. CLI 가 덮는다.
THRESHOLDS: dict[str, dict[str, tuple[float, str]]] = {
    "stg_listing_daily": {"G2": (0.01, "PARVAL 비수치 73,615/9,201,516 = 0.80% (survey v2)")},
    "stg_shares": {"G2": (0.12, "수량 10컬럼 비숫자 11,115/97,194 = 11.44% (survey v2)")},
    "stg_dividend": {"G2": (0.001, "금액 3컬럼 비숫자 143/384,232 = 0.037%")},
    "stg_hyslr": {"G2": (0.0001, "지분율 '#######' 9/228,226")},
    "stg_credit_daily": {"G2": (0.0001, "prdy_ctrt 비숫자 465/8,970,999")},
    "stg_delisted_master": {"G2": (0.05, "K1 후 잔여 비-8자리 날짜 — 첫 빌드 실측 후 확정"),
                            "G7": (0.01, "mfnd_end_dt 비-(19|20) 5/652 = 0.77%")},
}


def measure(snap: Snapshot, metrics: tuple[Metric, ...] = METRICS,
            thresholds: dict[str, dict[str, tuple[float, str]]] = THRESHOLDS,
            measured_at: str | None = None, memory_limit: str = "3GB") -> dict[str, object]:
    """스냅샷 위에서 전 항목을 측정한다.

    반환 = {table: {metric: value, thresholds: {...}}, _measured: [...], snapshot_id, measured_at}.
    """
    at = measured_at or datetime.now(UTC).strftime("%Y-%m-%d")
    con = duckdb.connect()
    con.execute(f"SET memory_limit = '{memory_limit}'")
    for db, f in snap.files.items():
        con.execute(f"ATTACH '{f.path}' AS \"{db}\" (TYPE sqlite, READ_ONLY)")
    data: dict[str, object] = {"snapshot_id": snap.snapshot_id, "measured_at": at, "_measured": []}
    tables: dict[str, dict[str, object]] = {}
    entries: list[dict[str, object]] = []
    for m in metrics:
        row = con.execute(m.sql).fetchone()
        if row is None:
            raise RuntimeError(f"baseline metric returned no row: {m.table}.{m.metric}")
        v = row[0]
        value: object = int(v) if isinstance(v, int) else (float(v) if v is not None else None)
        tables.setdefault(m.table, {})[m.metric] = value
        entries.append({"table": m.table, "metric": m.metric, "db": m.db, "sql": m.sql,
                        "value": value, "measured_at": at, "growing": m.growing})
    for table, th in thresholds.items():
        tables.setdefault(table, {})["thresholds"] = {g: v for g, (v, _) in th.items()}
        for g, (v, why) in th.items():
            entries.append({"table": table, "metric": f"threshold_{g}", "db": "-", "sql": "-",
                            "value": v, "measured_at": at, "growing": False, "reason": why})
    data.update(tables)
    data["_measured"] = entries
    _apply_constants(data, at)
    return data


def _entries(data: dict[str, object]) -> list[dict[str, object]]:
    got = data.get("_measured")
    entries: list[dict[str, object]] = got if isinstance(got, list) else []
    data["_measured"] = entries
    return entries


def _block(data: dict[str, object], table: str) -> dict[str, object]:
    got = data.get(table)
    block: dict[str, object] = got if isinstance(got, dict) else {}
    data[table] = block
    return block


def _put(entries: list[dict[str, object]], entry: dict[str, object]) -> None:
    """같은 (table, metric) 항목이 있으면 제자리에서 갈고, 없으면 끝에 붙인다."""
    for i, e in enumerate(entries):
        if e.get("table") == entry["table"] and e.get("metric") == entry["metric"]:
            entries[i] = entry
            return
    entries.append(entry)


def _apply_constants(data: dict[str, object], at: str) -> None:
    """선언 상수(CONSTANTS)를 결과에 싣는다 — 전량 측정이든 `--only` 든 항상 같은 값이 남는다."""
    entries = _entries(data)
    for table, consts in CONSTANTS.items():
        block = _block(data, table)
        for metric, (v, why) in consts.items():
            block[metric] = v
            _put(entries, {"table": table, "metric": metric, "db": "-", "sql": "-", "value": v,
                           "measured_at": at, "growing": False, "reason": why})


def measure_only(snap: Snapshot, current: dict[str, object], keys: Collection[str], *,
                 metrics: tuple[Metric, ...] = METRICS, measured_at: str | None = None,
                 note: str = "", memory_limit: str = "3GB") -> dict[str, object]:
    """`{table}.{metric}` 몇 개만 다시 재고 **나머지 값과 그 `measured_at` 은 그대로 둔다**.

    G9 술어가 바뀌었을 때처럼 한 지표만 재측정해야 하는데, 전량 재측정을 돌리면 그날 원장 상태로
    24지표가 통째로 느슨해진다(09-09 리뷰 D5-(b) 가 금지를 권고한 경로). 갱신 사실은 해당
    `_measured` 항목의 `measured_at`·`snapshot_id`·`note` 에 남는다.
    """
    wanted = {k: m for m in metrics for k in (f"{m.table}.{m.metric}",) if k in set(keys)}
    missing = sorted(set(keys) - set(wanted))
    if missing:
        raise ValueError(f"unknown baseline metric(s): {missing} "
                         f"known={sorted(f'{m.table}.{m.metric}' for m in metrics)[:8]}…")
    at = measured_at or datetime.now(UTC).strftime("%Y-%m-%d")
    data = json.loads(json.dumps(current))      # 깊은 복사 — 입력을 제자리 수정하지 않는다
    entries = _entries(data)
    con = duckdb.connect()
    con.execute(f"SET memory_limit = '{memory_limit}'")
    for db, f in snap.files.items():
        con.execute(f"ATTACH '{f.path}' AS \"{db}\" (TYPE sqlite, READ_ONLY)")
    for key, m in sorted(wanted.items()):
        row = con.execute(m.sql).fetchone()
        if row is None:
            raise RuntimeError(f"baseline metric returned no row: {key}")
        v = row[0]
        value: object = int(v) if isinstance(v, int) else (float(v) if v is not None else None)
        _block(data, m.table)[m.metric] = value
        _put(entries, {"table": m.table, "metric": m.metric, "db": m.db, "sql": m.sql,
                       "value": value, "measured_at": at, "growing": m.growing,
                       "snapshot_id": snap.snapshot_id, "note": note or f"--only {key} 재측정"})
    _apply_constants(data, at)
    return data


def write(path: Path, data: dict[str, object]) -> None:
    """원자 교체 (§2 — MANIFEST 와 같은 규약)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def main(argv: list[str] | None = None) -> int:
    base = Path(os.environ.get("QL_HOME") or Path(__file__).resolve().parents[2])
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--snapshot-id", required=True)
    ap.add_argument("--snapshot-root", type=Path, default=base / "data" / "snapshots")
    ap.add_argument("--out", type=Path, default=base / "data" / "stage" / "baseline.json")
    ap.add_argument("--memory-limit", default="3GB")
    ap.add_argument("--only", action="append", default=[], metavar="TABLE.METRIC",
                    help="이 지표만 다시 잰다(반복 가능). 나머지 값과 measured_at 은 보존된다")
    ap.add_argument("--note", default="", help="--only 재측정 사유 — _measured 항목에 남는다")
    a = ap.parse_args(argv)
    snap = load_snapshot(a.snapshot_root / a.snapshot_id)
    if a.only:
        if not a.out.exists():
            print(f"--only 는 기존 baseline 을 갱신한다 — 파일 없음: {a.out}", file=sys.stderr)
            return 2
        current = json.loads(a.out.read_text(encoding="utf-8"))
        data = measure_only(snap, current, a.only, note=a.note, memory_limit=a.memory_limit)
        touched = set(a.only) | {f"{t}.{m}" for t, c in CONSTANTS.items() for m in c}
    else:
        data = measure(snap, memory_limit=a.memory_limit)
        touched = None
    write(a.out, data)
    entries = data["_measured"]
    if not isinstance(entries, list):
        raise RuntimeError(f"baseline _measured is not a list: {type(entries).__name__}")
    for e in entries:
        if touched is not None and f"{e['table']}.{e['metric']}" not in touched:
            continue
        print(f"{e['table']:26s} {e['metric']:30s} {e['value']}" + ("  ★" if e["growing"] else ""))
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
