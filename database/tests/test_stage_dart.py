"""DART 14테이블 (정기 보조원장 6 · 지분 2 · 공시목록 · 기업 · 매핑 · 문서 · 로그 2).

손계산 픽스처. sqlite 는 survey_out/v2 의 원장 컬럼 전수로 만들고, 테이블마다 그 테이블에서
실제로 문제가 되는 것(집계행 · 점표기 날짜 · 연도 오타 셀 격리 · 각주 문자열 cast_failed ·
v1∪v2 접기 · 페이지 경계 접기 · rm 분해 · 참조표 룩업)을 하나씩 건다.
lookup available 테이블은 같은 stage 루트에 `stg_rcept_dt_map` 을 먼저 빌드해야 한다.
"""
import hashlib
import sqlite3
from pathlib import Path
from typing import Any

import duckdb
import pytest

from stage import build, gates, rules, snapshot

# ── 원장 실물 컬럼 (survey_out/v2 <db>.<table>.json 의 columns 전수) ────────────────────────────
COLS: dict[str, list[str]] = {
    "dart_disclosure": ["row_hash", "corp_cls", "corp_code", "corp_name", "flr_nm", "rcept_dt",
                        "rcept_no", "report_nm", "rm", "stock_code", "req_bgn_de", "req_end_de",
                        "req_page_no", "dup_seq", "collected_at"],
    "dart_dividend": ["row_hash", "corp_cls", "corp_code", "corp_name", "frmtrm", "lwfr",
                      "rcept_no", "se", "stlm_dt", "stock_knd", "thstrm", "req_bsns_year",
                      "req_corp_code", "req_reprt_code", "dup_seq", "collected_at"],
    "dart_shares": ["row_hash", "corp_cls", "corp_code", "corp_name", "distb_stock_co", "etc",
                    "istc_totqy", "isu_stock_totqy", "now_to_dcrs_stock_totqy",
                    "now_to_isu_stock_totqy", "profit_incnr", "rcept_no", "rdmstk_repy", "redc",
                    "se", "stlm_dt", "tesstk_co", "req_bsns_year", "req_corp_code",
                    "req_reprt_code", "dup_seq", "collected_at"],
    "dart_capital": ["row_hash", "corp_cls", "corp_code", "corp_name", "isu_dcrs_de",
                     "isu_dcrs_mstvdv_amount", "isu_dcrs_mstvdv_fval_amount", "isu_dcrs_qy",
                     "isu_dcrs_stle", "isu_dcrs_stock_knd", "rcept_no", "stlm_dt",
                     "req_bsns_year", "req_corp_code", "req_reprt_code", "dup_seq",
                     "collected_at"],
    "dart_tesstk": ["row_hash", "acqs_mth1", "acqs_mth2", "acqs_mth3", "bsis_qy",
                    "change_qy_acqs", "change_qy_dsps", "change_qy_incnr", "corp_cls",
                    "corp_code", "corp_name", "rcept_no", "rm", "stlm_dt", "stock_knd",
                    "trmend_qy", "req_bsns_year", "req_corp_code", "req_reprt_code", "dup_seq",
                    "collected_at"],
    "dart_hyslr": ["row_hash", "bsis_posesn_stock_co", "bsis_posesn_stock_qota_rt", "corp_cls",
                   "corp_code", "corp_name", "nm", "rcept_no", "relate", "rm", "stlm_dt",
                   "stock_knd", "trmend_posesn_stock_co", "trmend_posesn_stock_qota_rt",
                   "req_bsns_year", "req_corp_code", "req_reprt_code", "dup_seq",
                   "collected_at"],
    "dart_audit": ["row_hash", "adt_opinion", "adt_reprt_spcmnt_matter", "adtor", "bsns_year",
                   "core_adt_matter", "corp_cls", "corp_code", "corp_name", "emphs_matter",
                   "rcept_no", "stlm_dt", "req_bsns_year", "req_corp_code", "req_reprt_code",
                   "dup_seq", "collected_at"],
    "dart_elestock": ["row_hash", "corp_code", "corp_name", "isu_exctv_ofcps",
                      "isu_exctv_rgist_at", "isu_main_shrholdr", "rcept_dt", "rcept_no",
                      "repror", "sp_stock_lmp_cnt", "sp_stock_lmp_irds_cnt",
                      "sp_stock_lmp_irds_rate", "sp_stock_lmp_rate", "req_corp_code", "dup_seq",
                      "collected_at"],
    "dart_elestock_v1": ["corp_code", "corp_name", "isu_exctv_ofcps", "isu_exctv_rgist_at",
                         "isu_main_shrholdr", "rcept_dt", "rcept_no", "repror",
                         "sp_stock_lmp_cnt", "sp_stock_lmp_irds_cnt", "sp_stock_lmp_irds_rate",
                         "sp_stock_lmp_rate", "collected_at"],
    "dart_majorstock": ["row_hash", "corp_code", "corp_name", "ctr_stkqy", "ctr_stkrt",
                        "rcept_dt", "rcept_no", "report_resn", "report_tp", "repror", "stkqy",
                        "stkqy_irds", "stkrt", "stkrt_irds", "req_corp_code", "dup_seq",
                        "collected_at"],
    "dart_majorstock_v1": ["corp_code", "corp_name", "ctr_stkqy", "ctr_stkrt", "rcept_dt",
                           "rcept_no", "report_resn", "report_tp", "repror", "stkqy",
                           "stkqy_irds", "stkrt", "stkrt_irds", "collected_at"],
    "dart_company": ["row_hash", "acc_mt", "adres", "bizr_no", "ceo_nm", "corp_cls", "corp_code",
                     "corp_name", "corp_name_eng", "est_dt", "fax_no", "hm_url", "induty_code",
                     "ir_url", "jurir_no", "phn_no", "stock_code", "stock_name", "req_corp_code",
                     "dup_seq", "collected_at"],
    "dart_corp_map": ["stock_code", "corp_code", "corp_name"],
    "doc_store": ["rcept_no", "bytes", "sha256", "n_files", "zip_ok", "http_status",
                  "fetched_at"],
    "dart_call_log": ["endpoint", "corp_code", "bsns_year", "reprt_code", "fs_div", "status",
                      "n_rows", "ts", "key_id"],
    "ingest_log": ["name", "corp_code", "bsns_year", "reprt_code", "fs_div", "status", "n_rows",
                   "note", "ts"],
}

R_A = "20250311001085"      # 삼성전자 FY2024 사업보고서 — rcept_dt 2025-03-11
R_B = "20160108000502"      # rcept_dt 2016-01-08
R_MISS = "20230515000777"   # 공시목록에 없는 접수번호 → 참조표 미스 (available unknown)
R_1999 = "19990403000009"   # DART 최초기 공시 (docs/archive/dart_census_DS001.md 실측) — 파티션 축 밖
T0 = "2026-08-30T10:00:00"  # KST 2026-08-30
T1 = "2026-09-01T17:46:42"  # KST 2026-09-02

Row = dict[str, str | None]

_REQ = {"req_corp_code": "00126380", "req_bsns_year": "2024", "req_reprt_code": "11011"}
_RESP = {"rcept_no": R_A, "corp_code": "00126380", "corp_cls": "Y", "corp_name": "삼성전자",
         "stlm_dt": "2024-12-31", "collected_at": T0}
_PERIODIC: Row = {**_REQ, **_RESP}


def _disc(rcept_no: str, rcept_dt: str, **kw: str) -> Row:
    base: Row = {"corp_cls": "Y", "corp_code": "00126380", "corp_name": "삼성전자",
                 "flr_nm": "삼성전자", "rcept_dt": rcept_dt, "rcept_no": rcept_no,
                 "report_nm": "사업보고서 (2024.12)", "rm": "유", "stock_code": "005930",
                 "req_bgn_de": "20250101", "req_end_de": "20251231", "req_page_no": "1",
                 "collected_at": T0}
    return {**base, **kw}


DISCLOSURE: list[Row] = [
    _disc(R_A, "20250311"),
    _disc(R_A, "20250311", req_page_no="2", collected_at=T1),        # 페이지 경계 중복 → 접기
    _disc(R_B, "20160108", report_nm="[기재정정]주요사항보고서", rm="유정"),
    _disc("20240701000123", "20240701", corp_cls="E", corp_name="비상장주식회사",
          flr_nm="비상장주식회사", stock_code="", rm=""),            # 종목코드 없음 · 플래그 없음
    _disc(R_1999, "19990403", report_nm="사업보고서 (1998.12)", rm="코"),
]

DIVIDEND: list[Row] = [
    {**_PERIODIC, "se": "주당 현금배당금(원)", "stock_knd": "보통주",
     "thstrm": "1,444", "frmtrm": "361", "lwfr": "361"},
    {**_PERIODIC, "se": "(연결)당기순이익(백만원)", "stock_knd": "-",
     "thstrm": "34,451,351", "frmtrm": "15,487,100", "lwfr": "-"},
    {**_PERIODIC, "se": "주당 현금배당금(원)", "stock_knd": "보통주", "collected_at": T1,
     "thstrm": "1,444", "frmtrm": "361", "lwfr": "361"},             # 동일 payload 재수집 → 접기
    {**_PERIODIC, "rcept_no": R_MISS, "se": "현금배당수익률(%)", "stock_knd": "보통주",
     "req_bsns_year": "2022", "thstrm": "2.71", "frmtrm": "1.98", "lwfr": "1.98"},
]

_SHARE_QTY = {"isu_stock_totqy": "20,000,000,000", "now_to_isu_stock_totqy": "7,780,466,850",
              "now_to_dcrs_stock_totqy": "1,810,684,300", "redc": "-", "profit_incnr": "-",
              "rdmstk_repy": "-", "etc": "-", "istc_totqy": "5,969,782,550",
              "tesstk_co": "-", "distb_stock_co": "5,969,782,550"}
SHARES: list[Row] = [
    {**_PERIODIC, "se": "보통주", **_SHARE_QTY},
    {**_PERIODIC, "se": "우선주", **_SHARE_QTY, "istc_totqy": "822,886,700"},
    {**_PERIODIC, "se": "합계", **_SHARE_QTY, "istc_totqy": "6,792,669,250"},   # 집계행
    {**_PERIODIC, "se": "기타주식", **_SHARE_QTY, "etc": "주1)"},               # 각주 → 캐스팅 실패
]

CAPITAL: list[Row] = [
    {**_PERIODIC, "isu_dcrs_de": "2015.03.20", "isu_dcrs_stle": "유상증자(주주배정)",
     "isu_dcrs_stock_knd": "보통주", "isu_dcrs_qy": "1,000,000",
     "isu_dcrs_mstvdv_amount": "5,000", "isu_dcrs_mstvdv_fval_amount": "500"},
    {**_PERIODIC, "isu_dcrs_de": "-", "isu_dcrs_stle": "-", "isu_dcrs_stock_knd": "-",
     "isu_dcrs_qy": "-", "isu_dcrs_mstvdv_amount": "-",
     "isu_dcrs_mstvdv_fval_amount": "-"},
    {**_PERIODIC, "isu_dcrs_de": "2923.10.06", "isu_dcrs_stle": "무상증자",
     "isu_dcrs_stock_knd": "보통주", "isu_dcrs_qy": "500", "isu_dcrs_mstvdv_amount": "5,000",
     "isu_dcrs_mstvdv_fval_amount": "500"},                          # 연도 오타 → 셀 격리
]

_TES_QTY = {"bsis_qy": "1,000", "change_qy_acqs": "500", "change_qy_dsps": "-",
            "change_qy_incnr": "-", "trmend_qy": "1,500", "rm": "-"}
TESSTK: list[Row] = [
    {**_PERIODIC, "acqs_mth1": "배당가능이익범위 이내 취득", "acqs_mth2": "직접취득",
     "acqs_mth3": "장내직접취득", "stock_knd": "보통주", **_TES_QTY},
    {**_PERIODIC, "acqs_mth1": "총계", "acqs_mth2": "총계", "acqs_mth3": "총계",
     "stock_knd": "보통주", **_TES_QTY},                             # 집계행
    {**_PERIODIC, "acqs_mth1": "기타 취득", "acqs_mth2": "기타취득", "acqs_mth3": "기타",
     "stock_knd": "-", **_TES_QTY, "bsis_qy": "-", "trmend_qy": "-"},
]

HYSLR: list[Row] = [
    {**_PERIODIC, "nm": "이재용", "relate": "본인", "stock_knd": "보통주",
     "bsis_posesn_stock_co": "97,414,196", "bsis_posesn_stock_qota_rt": "1.63",
     "trmend_posesn_stock_co": "97,414,196", "trmend_posesn_stock_qota_rt": "1.63", "rm": "-"},
    {**_PERIODIC, "nm": "계", "relate": None, "stock_knd": "보통주",
     "bsis_posesn_stock_co": "1,265,061,673", "bsis_posesn_stock_qota_rt": "21.19",
     "trmend_posesn_stock_co": "1,265,061,673", "trmend_posesn_stock_qota_rt": "21.19",
     "rm": "-"},                                                     # 집계행
    {**_PERIODIC, "nm": "홍길동", "relate": "특수관계인", "stock_knd": "보통주",
     "bsis_posesn_stock_co": "-", "bsis_posesn_stock_qota_rt": "#######",
     "trmend_posesn_stock_co": "-", "trmend_posesn_stock_qota_rt": "-", "rm": "-"},
]

_OPINIONS = [("제 56 기(연결)", "적정", "적정"), ("제 55 기(연결)", "적 정", "적정"),
             ("제 54 기(연결)", "부적정", "부적정"), ("제 53 기(연결)", "의견거절", "의견거절"),
             ("제 52 기(연결)", "한정의견", "한정"), ("제 51 기(연결)", "의견없음", "other"),
             ("-", None, None)]
AUDIT: list[Row] = [
    {**_PERIODIC, "bsns_year": label, "adt_opinion": raw, "adtor": "삼일회계법인",
     "adt_reprt_spcmnt_matter": "-", "emphs_matter": None, "core_adt_matter": None}
    for label, raw, _ in _OPINIONS
]

_ELE = {"corp_code": "00126380", "corp_name": "삼성전자", "isu_exctv_ofcps": "대표이사",
        "isu_exctv_rgist_at": "등기임원", "isu_main_shrholdr": "-", "rcept_dt": "2025-03-11",
        "rcept_no": R_A, "sp_stock_lmp_cnt": "97,414,196", "sp_stock_lmp_irds_cnt": "-1,000",
        "sp_stock_lmp_rate": "1.63", "sp_stock_lmp_irds_rate": "-0.01"}
ELESTOCK: list[Row] = [
    {**_ELE, "repror": "이재용", "req_corp_code": "00126380", "collected_at": T1},
    {**_ELE, "repror": "정현호", "rcept_no": R_B, "rcept_dt": "2016-01-08",
     "req_corp_code": "00126380", "collected_at": T1},
]
ELESTOCK_V1: list[Row] = [
    {**_ELE, "repror": "이재용", "collected_at": T0},                # v2 와 payload 동일 → 접기
    {**_ELE, "repror": "김기남", "rcept_no": R_B, "rcept_dt": "2016-01-08",
     "collected_at": T0},                                            # v1 전용 — 구제 대상
]

_MAJOR = {"corp_code": "00126380", "corp_name": "삼성전자", "ctr_stkqy": "-", "ctr_stkrt": "-",
          "rcept_dt": "2025-03-11", "rcept_no": R_A, "report_resn": "장내매수/매도",
          "report_tp": "변동", "stkqy": "12,345,678", "stkqy_irds": "-1,000",
          "stkrt": "10.12", "stkrt_irds": "-0.01"}
MAJORSTOCK: list[Row] = [
    {**_MAJOR, "repror": "국민연금공단", "req_corp_code": "00126380", "collected_at": T1},
]
MAJORSTOCK_V1: list[Row] = [
    {**_MAJOR, "repror": "국민연금공단", "collected_at": T0},        # payload 동일 → 접기
    {**_MAJOR, "repror": "블랙록", "rcept_no": R_B, "rcept_dt": "2016-01-08",
     "collected_at": T0},                                            # v1 전용
]

COMPANY: list[Row] = [
    {"corp_code": "00126380", "stock_code": "005930", "corp_cls": "Y", "acc_mt": "12",
     "est_dt": "19690113", "bizr_no": "1248100998", "jurir_no": "1301110006737",
     "corp_name": "삼성전자", "corp_name_eng": "SAMSUNG ELECTRONICS CO,.LTD",
     "stock_name": "삼성전자", "ceo_nm": "한종희, 경계현", "adres": "경기도 수원시",
     "hm_url": "www.samsung.com", "ir_url": "", "phn_no": "031-200-1114",
     "fax_no": "031-200-7538", "induty_code": "26410", "req_corp_code": "00126380",
     "collected_at": T0},
    {"corp_code": "00364254", "stock_code": "0001A0", "corp_cls": "E", "acc_mt": "03",
     "est_dt": "18960101", "bizr_no": "1018100001", "jurir_no": "1101110000001",
     "corp_name": "폐지주식회사", "corp_name_eng": "DELISTED CO,.LTD",
     "stock_name": "폐지주식회사", "ceo_nm": "홍길동", "adres": "서울특별시",
     "hm_url": "", "ir_url": "", "phn_no": "02-000-0000", "fax_no": "",
     "induty_code": "999", "req_corp_code": "00364254", "collected_at": T0},
]

CORP_MAP: list[Row] = [
    {"stock_code": "005930", "corp_code": "00126380", "corp_name": "삼성전자"},
    {"stock_code": "0001A0", "corp_code": "00364254", "corp_name": "폐지주식회사"},
]

DOC_STORE: list[Row] = [
    {"rcept_no": R_A, "bytes": "352520", "sha256": "a" * 64, "n_files": "3", "zip_ok": "1",
     "http_status": "000", "fetched_at": T0},
    {"rcept_no": R_A, "bytes": "352520", "sha256": "a" * 64, "n_files": "3", "zip_ok": "1",
     "http_status": "000", "fetched_at": T1},                        # 재수집 — payload 동일
    {"rcept_no": R_B, "bytes": "412", "sha256": "", "n_files": "0", "zip_ok": "0",
     "http_status": "404", "fetched_at": T0},                        # 수집 실패분
]

CALL_LOG: list[Row] = [
    {"endpoint": "alotMatter.json", "corp_code": "00126380", "bsns_year": "2024",
     "reprt_code": "11011", "fs_div": None, "status": "000", "n_rows": "12", "ts": T0,
     "key_id": "k1"},
    {"endpoint": "list.json", "corp_code": "", "bsns_year": "20250101",
     "reprt_code": "20251231", "fs_div": "20250311001085", "status": "000", "n_rows": "100",
     "ts": T0, "key_id": "main"},
    {"endpoint": "fnlttSinglAcntAll.json", "corp_code": "00364254", "bsns_year": "2015",
     "reprt_code": "11011", "fs_div": "CFS", "status": "013", "n_rows": "0", "ts": T1,
     "key_id": "k2"},
]

INGEST_LOG: list[Row] = [
    {"name": "dividend", "corp_code": "00126380", "bsns_year": "2024", "reprt_code": "11011",
     "fs_div": "", "status": "ok", "n_rows": "12", "note": "000", "ts": T0},
    {"name": "fin", "corp_code": "00364254", "bsns_year": "2015", "reprt_code": "11011",
     "fs_div": "CFS", "status": "no_data", "n_rows": "0", "note": "013", "ts": T1},
]

LEDGER: dict[str, list[Row]] = {
    "dart_disclosure": DISCLOSURE, "dart_dividend": DIVIDEND, "dart_shares": SHARES,
    "dart_capital": CAPITAL, "dart_tesstk": TESSTK, "dart_hyslr": HYSLR, "dart_audit": AUDIT,
    "dart_elestock": ELESTOCK, "dart_elestock_v1": ELESTOCK_V1,
    "dart_majorstock": MAJORSTOCK, "dart_majorstock_v1": MAJORSTOCK_V1,
    "dart_company": COMPANY, "dart_corp_map": CORP_MAP, "doc_store": DOC_STORE,
    "dart_call_log": CALL_LOG, "ingest_log": INGEST_LOG,
}


_HASH_META = ("row_hash", "dup_seq", "collected_at")


def _with_hash(cols: list[str], row: Row) -> Row:
    """원장 PK `row_hash` = 내용 해시(수집 메타 제외) — 수집기 규약을 픽스처에서 재현한다."""
    if "row_hash" not in cols or row.get("row_hash"):
        return row
    content = "|".join(str(row.get(c) or "") for c in cols if c not in _HASH_META)
    return {**row, "row_hash": hashlib.md5(content.encode("utf-8")).hexdigest()}


def _write_dart(path: Path, ledger: dict[str, list[Row]]) -> None:
    con = sqlite3.connect(path)
    for table, cols in COLS.items():
        con.execute(f"CREATE TABLE {table} ({', '.join(c + ' TEXT' for c in cols)})")
        con.executemany(f"INSERT INTO {table} VALUES ({','.join('?' * len(cols))})",
                        [tuple(_with_hash(cols, row).get(c) for c in cols)
                         for row in ledger.get(table, [])])
    con.commit()
    con.close()


@pytest.fixture
def stage(tmp_path: Path) -> tuple[snapshot.Snapshot, Path]:
    """원장 스냅샷 + 참조표(stg_rcept_dt_map) 선빌드 — lookup available 의 선행 조건."""
    raw = tmp_path / "raw"
    raw.mkdir()
    _write_dart(raw / "dart.db", LEDGER)
    snap = snapshot.make_snapshot({"dart": raw / "dart.db"}, tmp_path / "snapshots",
                                  snapshot_id="snap_dart14")
    build.build_table(rules.RULES["stg_rcept_dt_map"], snap, tmp_path / "stage")
    return snap, tmp_path


def _build(table: str, stage: tuple[snapshot.Snapshot, Path], **kw: Any) -> build.BuildResult:
    snap, tmp_path = stage
    r = build.build_table(rules.RULES[table], snap, tmp_path / "stage", **kw)
    if not r.ok:
        raise AssertionError(f"{table}: " + "; ".join(
            f"{g.name} {g.detail}" for g in r.gates if g.status is gates.GateStatus.FAIL))
    return r


def _read(stage: tuple[snapshot.Snapshot, Path], r: build.BuildResult
          ) -> duckdb.DuckDBPyConnection:
    """채택 행만 읽는다 — `_reject/` 는 파티션 스키마가 달라 같은 glob 에 넣으면 안 된다."""
    _, tmp_path = stage
    whole = rules.RULES[r.table].partition_class == "whole"
    con = duckdb.connect()
    sub = "*.parquet" if whole else "year=*/*.parquet"
    glob = str(tmp_path / "stage" / r.table / f"v={r.build_id}" / sub)
    con.execute(f"CREATE VIEW t AS SELECT * FROM read_parquet('{glob}', hive_partitioning=true)")
    return con


def _one(con: duckdb.DuckDBPyConnection, sql: str) -> tuple[Any, ...]:
    row = con.execute(sql).fetchone()
    if row is None:
        raise AssertionError(f"no row: {sql}")
    return row


def _gate(r: build.BuildResult, name: str) -> gates.GateResult:
    return next(g for g in r.gates if g.name == name)


# ── 선언 (§4 파티션 표 · §3 observed_src · §6 available) ────────────────────────────────────────
def test_rules_declare_the_dart_partition_and_time_axes() -> None:
    receipt = ("stg_dividend", "stg_shares", "stg_capital", "stg_tesstk", "stg_hyslr",
               "stg_audit", "stg_holder_elestock", "stg_holder_majorstock", "stg_disclosure",
               "stg_doc_index")
    for name in receipt:
        rule = rules.RULES[name]
        assert rule.partition_class == "receipt_axis"
        assert rule.partition_expr == "substr(rcept_no, 1, 4)"
        assert rule.write_mode == "append_only" and rule.fanout == 1
        assert rule.lag_known is False
    for name in ("stg_company", "stg_corp_map", "stg_calls_dart", "stg_units_dart"):
        rule = rules.RULES[name]
        assert rule.partition_class == "whole" and rule.partition_expr is None
        assert rule.available.kind == "none"
    assert rules.RULES["stg_doc_index"].observed_src == "fetched_at"
    assert rules.RULES["stg_calls_dart"].observed_src == "ts"
    assert rules.RULES["stg_units_dart"].observed_src == "ts"
    assert rules.RULES["stg_corp_map"].observed_src is None      # 시각 컬럼 0 — 면제 (§3)


def test_rules_route_available_date_by_the_measured_receipt_date() -> None:
    for name in ("stg_dividend", "stg_shares", "stg_capital", "stg_tesstk", "stg_hyslr",
                 "stg_audit"):
        avail = rules.RULES[name].available
        assert avail.kind == "lookup" and avail.table == "stg_rcept_dt_map"
        assert avail.local_key == "rcept_no" and avail.lookup_value == "rcept_dt"
    for name in ("stg_holder_elestock", "stg_holder_majorstock", "stg_disclosure"):
        avail = rules.RULES[name].available
        assert avail.kind == "column" and avail.column == "rcept_dt"
        assert avail.basis == "measured"


# ── stg_dividend ───────────────────────────────────────────────────────────────────────────────
def test_dividend_folds_recollection_and_derives_available_from_the_map(
    stage: tuple[snapshot.Snapshot, Path]
) -> None:
    r = _build("stg_dividend", stage)
    assert r.n_src == 4 and r.n_dedup == 1 and r.n_rows == 3
    con = _read(stage, r)
    got = _one(con, "SELECT thstrm, frmtrm, lwfr, observed_n, available_date, available_basis "
                    "FROM t WHERE se = '주당 현금배당금(원)'")
    assert str(got[0]) == "1444.00" and str(got[1]) == "361.00" and str(got[2]) == "361.00"
    assert got[3] == 2 and str(got[4]) == "2025-03-11" and got[5] == "derived"
    miss = _one(con, "SELECT lwfr, miss_kind.lwfr, _src_flag, stock_knd FROM t "
                     "WHERE se = '(연결)당기순이익(백만원)'")
    assert miss == (None, "ledger_dash", "ok", "-")     # '-' 는 정상 결측이지 캐스팅 실패가 아니다
    unknown = _one(con, "SELECT available_date, available_basis FROM t "
                        "WHERE se = '현금배당수익률(%)'")
    assert unknown == (None, "unknown")                 # 참조표 미스 (§6 — rcept_no[:8] 폴백 폐기)


# ── stg_shares ─────────────────────────────────────────────────────────────────────────────────
def test_shares_marks_aggregate_rows_and_flags_footnote_strings(
    stage: tuple[snapshot.Snapshot, Path]
) -> None:
    r = _build("stg_shares", stage, gate_thresholds={"G2": 0.5})
    assert r.n_rows == 4
    con = _read(stage, r)
    assert con.execute("SELECT se FROM t WHERE row_kind = 'aggregate'").fetchall() == [("합계",)]
    assert con.execute("SELECT count(*) FROM t WHERE row_kind = 'detail'").fetchone() == (3,)
    qty = _one(con, "SELECT istc_totqy_shr, distb_stock_co_shr, redc_shr, miss_kind.redc_shr "
                    "FROM t WHERE se = '보통주'")
    assert qty == (5_969_782_550, 5_969_782_550, None, "ledger_dash")
    bad = _one(con, "SELECT etc_shr, miss_kind.etc_shr, _src_flag, _cast_fail_cols FROM t "
                    "WHERE se = '기타주식'")
    assert bad == (None, "cast_failed", "partial", ["etc_shr"])
    assert _gate(r, "G2").metrics["n_partial"] == 1


# ── stg_capital ────────────────────────────────────────────────────────────────────────────────
def test_capital_parses_dot_dates_and_isolates_the_year_typo_cell(
    stage: tuple[snapshot.Snapshot, Path]
) -> None:
    r = _build("stg_capital", stage, gate_thresholds={"G7": 0.5})
    assert r.n_rows == 3 and r.n_reject == 0            # G7 은 행 격리형 — 행은 남는다 (§9)
    con = _read(stage, r)
    ok = _one(con, "SELECT isu_dcrs_de, isu_dcrs_de_raw, isu_dcrs_qy_shr, "
                   "isu_dcrs_mstvdv_amount_krw, isu_dcrs_mstvdv_fval_amount_krw FROM t "
                   "WHERE isu_dcrs_stle = '유상증자(주주배정)'")
    assert str(ok[0]) == "2015-03-20" and ok[1] == "2015.03.20"
    assert (ok[2], ok[3], ok[4]) == (1_000_000, 5_000, 500)
    dash = _one(con, "SELECT isu_dcrs_de, isu_dcrs_de_raw, miss_kind.isu_dcrs_de FROM t "
                     "WHERE isu_dcrs_stle = '-'")
    assert dash == (None, "-", "ledger_dash")           # 키는 원문이라 행이 살아남는다
    typo = _one(con, "SELECT isu_dcrs_de, isu_dcrs_de_raw, miss_kind.isu_dcrs_de, _src_flag "
                     "FROM t WHERE isu_dcrs_stle = '무상증자'")
    assert typo == (None, "2923.10.06", "out_of_range", "ok")
    assert _gate(r, "G7").metrics["n_out_of_range_cells"] == 1


# ── stg_tesstk ─────────────────────────────────────────────────────────────────────────────────
def test_tesstk_marks_the_total_row_on_the_first_acquisition_method(
    stage: tuple[snapshot.Snapshot, Path]
) -> None:
    r = _build("stg_tesstk", stage)
    assert r.n_rows == 3
    con = _read(stage, r)
    assert con.execute("SELECT acqs_mth1 FROM t WHERE row_kind = 'aggregate'").fetchall() == [
        ("총계",)]
    qty = _one(con, "SELECT bsis_qy_shr, change_qy_acqs_shr, trmend_qy_shr, "
                    "miss_kind.change_qy_dsps_shr FROM t WHERE acqs_mth2 = '직접취득'")
    assert qty == (1_000, 500, 1_500, "ledger_dash")


# ── stg_hyslr ──────────────────────────────────────────────────────────────────────────────────
def test_hyslr_marks_the_total_row_and_flags_the_spreadsheet_overflow(
    stage: tuple[snapshot.Snapshot, Path]
) -> None:
    r = _build("stg_hyslr", stage, gate_thresholds={"G2": 0.5})
    assert r.n_rows == 3
    con = _read(stage, r)
    assert con.execute("SELECT nm FROM t WHERE row_kind = 'aggregate'").fetchall() == [("계",)]
    own = _one(con, "SELECT bsis_posesn_stock_co_shr, bsis_posesn_stock_qota_rt_pct, relate "
                    "FROM t WHERE nm = '이재용'")
    assert (own[0], float(own[1]), own[2]) == (97_414_196, 1.63, "본인")
    bad = _one(con, "SELECT bsis_posesn_stock_qota_rt_pct, "
                    "miss_kind.bsis_posesn_stock_qota_rt_pct, _src_flag FROM t "
                    "WHERE nm = '홍길동'")
    assert bad == (None, "cast_failed", "partial")


# ── stg_audit ──────────────────────────────────────────────────────────────────────────────────
def test_audit_keeps_the_opinion_verbatim_and_matches_negative_first(
    stage: tuple[snapshot.Snapshot, Path]
) -> None:
    r = _build("stg_audit", stage)
    assert r.n_rows == len(_OPINIONS)
    con = _read(stage, r)
    for label, raw, expect in _OPINIONS:
        got = _one(con, "SELECT adt_opinion, adt_opinion_class FROM t "
                        f"WHERE bsns_year_label = '{label}'")
        assert got == (raw, expect), label              # 원문 보존 + 부적정 선매칭 (SPEC §2-13)
    assert _one(con, "SELECT bsns_year, bsns_year_label FROM t "
                     "WHERE bsns_year_label = '제 56 기(연결)'") == ("2024", "제 56 기(연결)")


# ── stg_holder_elestock / _majorstock (v2 ∪ v1) ────────────────────────────────────────────────
def test_elestock_union_folds_the_shared_records_and_rescues_v1_only_rows(
    stage: tuple[snapshot.Snapshot, Path]
) -> None:
    r = _build("stg_holder_elestock", stage)
    assert r.n_src == 4 and r.n_dedup == 1 and r.n_rows == 3
    con = _read(stage, r)
    assert con.execute("SELECT repror, _src FROM t ORDER BY repror").fetchall() == [
        ("김기남", "v1"), ("이재용", "v1"), ("정현호", "v2")]   # 접힌 대표는 먼저 관측된 v1
    got = _one(con, "SELECT observed_n, observed_date, available_date, available_basis, "
                    "sp_stock_lmp_cnt_shr, sp_stock_lmp_irds_cnt_shr, sp_stock_lmp_rate_pct "
                    "FROM t WHERE repror = '이재용'")
    assert got[0] == 2 and str(got[1]) == "2026-08-30"
    assert str(got[2]) == "2025-03-11" and got[3] == "measured"
    assert (got[4], got[5], float(got[6])) == (97_414_196, -1_000, 1.63)


def test_majorstock_union_keeps_one_row_per_reporter(
    stage: tuple[snapshot.Snapshot, Path]
) -> None:
    r = _build("stg_holder_majorstock", stage)
    assert r.n_src == 3 and r.n_dedup == 1 and r.n_rows == 2
    con = _read(stage, r)
    got = _one(con, "SELECT stkqy_shr, stkqy_irds_shr, stkrt_pct, stkrt_irds_pct, report_tp, "
                    "year FROM t WHERE repror = '국민연금공단'")
    assert (got[0], got[1]) == (12_345_678, -1_000)
    assert (float(got[2]), float(got[3])) == (10.12, -0.01)
    assert got[4] == "변동" and got[5] == 2025          # 파티션 = 접수번호 앞 4자리


# ── stg_disclosure ─────────────────────────────────────────────────────────────────────────────
def test_disclosure_folds_page_boundary_duplicates_and_splits_the_rm_flags(
    stage: tuple[snapshot.Snapshot, Path]
) -> None:
    r = _build("stg_disclosure", stage, gate_thresholds={"G7": 0.5})
    con = _read(stage, r)
    base = _one(con, "SELECT observed_n, observed_date, rcept_dt, available_basis, ticker, "
                     f"has_ticker, is_correction, rm_kospi, rm_corrected_later FROM t "
                     f"WHERE rcept_no = '{R_A}'")
    assert base[0] == 2 and str(base[1]) == "2026-08-30" and str(base[2]) == "2025-03-11"
    assert base[3] == "measured" and base[4] == "005930" and base[5] is True
    assert (base[6], base[7], base[8]) == (False, True, False)
    corr = _one(con, "SELECT is_correction, rm_kospi, rm_corrected_later, rm_unknown FROM t "
                     f"WHERE rcept_no = '{R_B}'")
    assert corr == (True, True, True, False)            # '[기재정정]…' + rm='유정' 2플래그
    none = _one(con, "SELECT ticker, has_ticker, rm_kospi, rm_unknown, corp_cls_current FROM t "
                     "WHERE rcept_no = '20240701000123'")
    assert none == ("", False, False, False, "E")


def test_disclosure_keeps_the_1999_receipt_year_on_the_partition_axis(
    stage: tuple[snapshot.Snapshot, Path]
) -> None:
    """관측일 축 하한은 1999(DART 최초 공시 연도) — 1999 접수번호는 격리되지 않는다 (D4 수정)."""
    r = _build("stg_disclosure", stage)
    assert r.n_src == 5 and r.n_dedup == 1 and r.n_reject == 0 and r.n_rows == 4
    assert _gate(r, "G7").metrics["n_out_of_range_rows"] == 0
    con = _read(stage, r)
    assert con.execute(f"SELECT count(*) FROM t WHERE rcept_no = '{R_1999}'").fetchone() == (1,)


# ── stg_company / stg_corp_map ─────────────────────────────────────────────────────────────────
# 두 테이블은 컬럼이 전부 TEXT 라 캐스팅 대상이 0개 — miss_kind 는 자리표시 NULL STRUCT (#18, D1).
def test_company_marks_refetched_state_columns_and_skips_available_date(
    stage: tuple[snapshot.Snapshot, Path]
) -> None:
    r = _build("stg_company", stage)
    assert r.n_rows == 2
    con = _read(stage, r)
    got = _one(con, "SELECT ticker_current, corp_cls_current, corp_name_current, acc_mt, "
                    "est_dt, available_date, available_basis FROM t "
                    "WHERE corp_code = '00126380'")
    assert got[:5] == ("005930", "Y", "삼성전자", "12", "19690113")   # est_dt 는 원문 8자리 TEXT
    assert got[5] is None and got[6] is None            # rcept_no·rcept_dt 없음 → 비부여 (§4)
    delisted = _one(con, "SELECT ticker_current, corp_cls_current, acc_mt FROM t "
                         "WHERE corp_code = '00364254'")
    assert delisted == ("0001A0", "E", "03")            # 티커에 문자 실재 — 숫자 캐스팅 금지


def test_corp_map_has_no_clock_column_so_observed_date_is_null(
    stage: tuple[snapshot.Snapshot, Path]
) -> None:
    r = _build("stg_corp_map", stage)
    assert r.n_rows == 2
    con = _read(stage, r)
    assert con.execute("SELECT ticker, corp_name_current, observed_date FROM t "
                       "ORDER BY corp_code").fetchall() == [
        ("005930", "삼성전자", None), ("0001A0", "폐지주식회사", None)]
    assert _gate(r, "G6").status is gates.GateStatus.PASS


# ── stg_doc_index ──────────────────────────────────────────────────────────────────────────────
def test_doc_index_folds_refetches_and_keeps_one_row_per_receipt(
    stage: tuple[snapshot.Snapshot, Path]
) -> None:
    r = _build("stg_doc_index", stage)
    assert r.n_src == 3 and r.n_dedup == 1 and r.n_rows == 2
    con = _read(stage, r)
    ok = _one(con, f"SELECT bytes, n_files, zip_ok, http_status, observed_n, observed_date, "
                   f"available_date FROM t WHERE rcept_no = '{R_A}'")
    assert (ok[0], ok[1], ok[2], ok[3]) == (352520, 3, True, 0)
    assert ok[4] == 2 and str(ok[5]) == "2026-08-30" and ok[6] is None
    fail = _one(con, f"SELECT sha256, zip_ok, http_status FROM t WHERE rcept_no = '{R_B}'")
    assert fail == ("", False, 404)
    assert _gate(r, "G6").status is gates.GateStatus.PASS   # 재조회 판본 축 (§7)


# ── stg_calls_dart / stg_units_dart ────────────────────────────────────────────────────────────
def test_call_log_keeps_rows_whose_request_axis_columns_are_blank(
    stage: tuple[snapshot.Snapshot, Path]
) -> None:
    r = _build("stg_calls_dart", stage)
    assert r.n_src == 3 and r.n_reject == 0 and r.n_rows == 3   # 빈 요청축은 키가 아니라 무해
    con = _read(stage, r)
    got = _one(con, "SELECT corp_code, bsns_year, fs_div, status, n_rows, observed_date, "
                    "available_date FROM t WHERE endpoint = 'list.json'")
    assert got[:5] == ("", "20250101", "20250311001085", "000", 100)
    assert str(got[5]) == "2026-08-30" and got[6] is None
    assert _one(con, "SELECT fs_div FROM t WHERE endpoint = 'alotMatter.json'") == (None,)


def test_ingest_log_lands_as_a_single_whole_partition(
    stage: tuple[snapshot.Snapshot, Path]
) -> None:
    r = _build("stg_units_dart", stage)
    assert r.n_src == 2 and r.n_rows == 2
    _, tmp_path = stage
    vdir = tmp_path / "stage" / "stg_units_dart" / f"v={r.build_id}"
    assert sorted(p.name for p in vdir.iterdir()) == ["_meta.json", "part0.parquet"]
    con = _read(stage, r)
    got = _one(con, "SELECT status, n_rows, note, observed_date FROM t WHERE name = 'fin'")
    assert got[:3] == ("no_data", 0, "013") and str(got[3]) == "2026-09-02"


def test_같은_날_페이로드가_다른_두_관측은_그날의_마지막_관측으로_접힌다(tmp_path: Path) -> None:
    """2026-09-11 첫 저녁 슬롯: DART 06:47 재스윕과 18:05 저녁 스윕이 같은 KST 날짜에 같은 rcept_no 를
    다른 페이로드로 두 번 관측 → (natural_key, observed_date) 중복으로 G6 이 stg_disclosure·stg_fin 을
    폐기했다. 판본 축이 날짜라 같은 날 두 판은 표현할 수 없다 — 그날의 마지막 관측을 판으로 삼고
    나머지는 n_dedup_same_day 로 센다. 이 폴드는 콜 로그(versioned=False)에는 적용되지 않는다."""
    r_x = "20250311000999"
    morning = _disc(r_x, "20250311", rm="유", collected_at="2026-08-29T22:12:00")      # 08-30 07:12 KST
    evening = _disc(r_x, "20250311", rm="유정", collected_at="2026-08-30T09:42:00")    # 08-30 18:42 KST
    ledger = {**LEDGER, "dart_disclosure": [*LEDGER["dart_disclosure"], morning, evening]}
    raw = tmp_path / "raw"
    raw.mkdir()
    _write_dart(raw / "dart.db", ledger)
    snap = snapshot.make_snapshot({"dart": raw / "dart.db"}, tmp_path / "snapshots",
                                  snapshot_id="snap_sameday")
    build.build_table(rules.RULES["stg_rcept_dt_map"], snap, tmp_path / "stage")
    r = _build("stg_disclosure", (snap, tmp_path), gate_thresholds={"G7": 0.5})
    assert _gate(r, "G6").status is gates.GateStatus.PASS
    con = _read((snap, tmp_path), r)
    rows = con.execute(f"SELECT rm_corrected_later, observed_date, observed_n FROM t "
                       f"WHERE rcept_no = '{r_x}'").fetchall()
    assert len(rows) == 1
    assert rows[0][0] is True and str(rows[0][1]) == "2026-08-30"     # 저녁(마지막) 관측이 그날의 판
    assert _gate(r, "G1").metrics["n_dedup_same_day"] == 1
