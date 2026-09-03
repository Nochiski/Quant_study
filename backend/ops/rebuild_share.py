#!/usr/bin/env python3
"""quant-ledger 원장 → 공유용 typed Parquet 리빌드.

원장(SQLite)을 READ_ONLY로 단 한 번 읽어 typed Parquet을 만든다.
검증을 통과한 산출물만 공개 경로로 옮기고, latest 심볼릭을 원자적으로 교체한다.
실패하면 latest는 이전 성공 버전을 그대로 가리킨다.

사용:
    python rebuild_share.py              # 리빌드
    python rebuild_share.py --dry-run    # 변환·검증만 하고 공개하지 않음
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import duckdb

KST = timezone(timedelta(hours=9))
HOME = Path.home()
RAW = HOME / "quant-ledger/data/raw"
# 공개 경로. /srv/quant-share 는 root 소유(chroot·웹루트 요구사항), share/ 만 kael 소유.
SHARE = Path(os.environ.get("QUANT_SHARE_DIR", "/srv/quant-share/share"))
KEEP_VERSIONS = 7

# ── 타입 변환 헬퍼 ────────────────────────────────────────────────────────────
D = lambda c: f"strptime(NULLIF(trim({c}),''), '%Y%m%d')::DATE"          # 날짜
I = lambda c: f"TRY_CAST(NULLIF(trim({c}),'') AS BIGINT)"                # 정수, 빈값→NULL
N = lambda c: f"TRY_CAST(NULLIF(trim({c}),'') AS DECIMAL(18,2))"         # 소수, 빈값→NULL
IP = lambda c: f"TRY_CAST(REPLACE(REPLACE({c},'+',''),'-','') AS BIGINT)"  # 부호제거(가격)
IS_ = lambda c: f"TRY_CAST(REPLACE({c},'+','') AS BIGINT)"               # 부호유지(+만 제거)
NS = lambda c: f"TRY_CAST(REPLACE({c},'+','') AS DECIMAL(18,2))"         # 부호유지 소수

PRICE = ["TDD_CLSPRC", "TDD_OPNPRC", "TDD_HGPRC", "TDD_LWPRC"]
IDXP = ["CLSPRC_IDX", "OPNPRC_IDX", "HGPRC_IDX", "LWPRC_IDX"]
FLOW = ["ind_invsr", "frgnr_invsr", "orgn", "fnnc_invt", "insrnc", "invtrt",
        "etc_fnnc", "bank", "penfnd_etc", "samo_fund", "natn", "etc_corp", "natfor"]


def krx_trd():
    return ([f'{D("BAS_DD")} AS bas_dd', "ISU_CD AS isu_cd", "ISU_NM AS isu_nm",
             "MKT_NM AS mkt_nm", "SECT_TP_NM AS sect_tp_nm"]
            + [f'{I(c)} AS {c.lower()}' for c in PRICE]
            + [f'{I("CMPPREVDD_PRC")} AS cmpprevdd_prc', f'{N("FLUC_RT")} AS fluc_rt']
            + [f'{I(c)} AS {c.lower()}' for c in ["ACC_TRDVOL", "ACC_TRDVAL", "MKTCAP", "LIST_SHRS"]]
            + [f'{D("bas_dd_req")} AS bas_dd_req', "collected_at"])


def krx_idx():
    return ([f'{D("BAS_DD")} AS bas_dd', "IDX_CLSS AS idx_clss", "IDX_NM AS idx_nm"]
            + [f'{N(c)} AS {c.lower()}' for c in IDXP]
            + [f'{N("CMPPREVDD_IDX")} AS cmpprevdd_idx', f'{N("FLUC_RT")} AS fluc_rt']
            + [f'{I(c)} AS {c.lower()}' for c in ["ACC_TRDVOL", "ACC_TRDVAL", "MKTCAP"]]
            + [f'{D("bas_dd_req")} AS bas_dd_req', "collected_at"])


def krx_base():
    return ["ISU_CD AS isu_cd", "ISU_SRT_CD AS isu_srt_cd", "ISU_NM AS isu_nm",
            "ISU_ABBRV AS isu_abbrv", "ISU_ENG_NM AS isu_eng_nm",
            f'{D("LIST_DD")} AS list_dd', "MKT_TP_NM AS mkt_tp_nm",
            "SECUGRP_NM AS secugrp_nm", "SECT_TP_NM AS sect_tp_nm",
            "KIND_STKCERT_TP_NM AS kind_stkcert_tp_nm",
            "PARVAL AS parval",  # '무액면' 31,487건 존재 → TEXT 유지
            f'{I("LIST_SHRS")} AS list_shrs',
            f'{D("bas_dd_req")} AS bas_dd_req', "collected_at"]


def krx_etf():
    return [f'{D("BAS_DD")} AS bas_dd', "ISU_CD AS isu_cd", "ISU_NM AS isu_nm",
            f'{I("TDD_CLSPRC")} AS tdd_clsprc', f'{I("CMPPREVDD_PRC")} AS cmpprevdd_prc',
            f'{N("FLUC_RT")} AS fluc_rt', f'{N("NAV")} AS nav',
            f'{I("TDD_OPNPRC")} AS tdd_opnprc', f'{I("TDD_HGPRC")} AS tdd_hgprc',
            f'{I("TDD_LWPRC")} AS tdd_lwprc', f'{I("ACC_TRDVOL")} AS acc_trdvol',
            f'{I("ACC_TRDVAL")} AS acc_trdval', f'{I("MKTCAP")} AS mktcap',
            f'{I("INVSTASST_NETASST_TOTAMT")} AS invstasst_netasst_totamt',
            f'{I("LIST_SHRS")} AS list_shrs', "IDX_IND_NM AS idx_ind_nm",
            f'{N("OBJ_STKPRC_IDX")} AS obj_stkprc_idx',    # 빈값 107,069건 → NULL (정상)
            f'{N("CMPPREVDD_IDX")} AS cmpprevdd_idx',
            f'{N("FLUC_RT_IDX")} AS fluc_rt_idx',
            f'{D("bas_dd_req")} AS bas_dd_req', "collected_at"]


# (테이블 → (원본DB, 컬럼식, PK, 날짜컬럼))
SPEC = {
    "krx_stk_bydd_trd":      ("krx", krx_trd,  ["bas_dd_req", "isu_cd"], "bas_dd"),
    "krx_ksq_bydd_trd":      ("krx", krx_trd,  ["bas_dd_req", "isu_cd"], "bas_dd"),
    "krx_stk_isu_base_info": ("krx", krx_base, ["bas_dd_req", "isu_cd"], "bas_dd_req"),
    "krx_ksq_isu_base_info": ("krx", krx_base, ["bas_dd_req", "isu_cd"], "bas_dd_req"),
    "krx_etf_bydd_trd":      ("krx", krx_etf,  ["bas_dd_req", "isu_cd"], "bas_dd"),
    "krx_kospi_dd_trd":      ("krx", krx_idx,  ["bas_dd_req", "idx_nm"], "bas_dd"),
    "krx_kosdaq_dd_trd":     ("krx", krx_idx,  ["bas_dd_req", "idx_nm"], "bas_dd"),
    "ingest_log": ("krx", lambda: ["endpoint", f'{D("bas_dd")} AS bas_dd', "n_rows",
                                   "status", "note", "collected_at"],
                   ["endpoint", "bas_dd"], "bas_dd"),
    "ka10008_foreign_holdings": ("kiwoom", lambda: [
        "ticker", f'{D("dt")} AS dt',
        f'{IP("close_pric")} AS close_pric',        # 부호=등락방향 → 제거 (pred_pre와 중복)
        f'{IS_("pred_pre")} AS pred_pre',           # 부호=실제값 → 유지
        f'{I("trde_qty")} AS trde_qty', f'{IS_("chg_qty")} AS chg_qty',
        f'{I("poss_stkcnt")} AS poss_stkcnt', f'{NS("wght")} AS wght',
        f'{I("gain_pos_stkcnt")} AS gain_pos_stkcnt', f'{I("frgnr_limit")} AS frgnr_limit',
        f'{IS_("frgnr_limit_irds")} AS frgnr_limit_irds', f'{NS("limit_exh_rt")} AS limit_exh_rt',
        "src_api", "collected_at"], ["ticker", "dt"], "dt"),
    "ka10014_short_selling": ("kiwoom", lambda: [
        "ticker", f'{D("dt")} AS dt',
        f'{IP("close_pric")} AS close_pric',
        "pred_pre_sig",                             # 부호'코드'(1~5) → TEXT 유지
        f'{IS_("pred_pre")} AS pred_pre', f'{NS("flu_rt")} AS flu_rt',
        f'{I("trde_qty")} AS trde_qty', f'{I("shrts_qty")} AS shrts_qty',
        f'{I("ovr_shrts_qty")} AS ovr_shrts_qty', f'{NS("trde_wght")} AS trde_wght',
        f'{I("shrts_trde_prica")} AS shrts_trde_prica',
        f'{I("shrts_avg_pric")} AS shrts_avg_pric',
        "src_api", "collected_at"], ["ticker", "dt"], "dt"),
    "ka10060_investor_flows": ("kiwoom", lambda: [
        "ticker", f'{D("dt")} AS dt',
        f'{IP("cur_prc")} AS cur_prc', f'{IS_("pred_pre")} AS pred_pre',
        f'{I("acc_trde_prica")} AS acc_trde_prica']
        + [f'{IS_(c)} AS {c}' for c in FLOW]        # 순매수 → 음수 유지
        + ["src_api", "collected_at"], ["ticker", "dt"], "dt"),
    "ka20068_lending_balance": ("kiwoom", lambda: [
        "ticker", f'{D("dt")} AS dt',
        f'{I("dbrt_trde_cntrcnt")} AS dbrt_trde_cntrcnt',
        f'{I("dbrt_trde_rpy")} AS dbrt_trde_rpy',
        f'{IS_("dbrt_trde_irds")} AS dbrt_trde_irds',   # 증감 → 음수 유지
        f'{I("rmnd")} AS rmnd', f'{I("remn_amt")} AS remn_amt',
        "src_api", "collected_at"], ["ticker", "dt"], "dt"),
    "ingest_shard": ("kiwoom", lambda: [
        "src_api", "ticker", f'{D("req_start")} AS req_start', f'{D("req_end")} AS req_end',
        "n_rows", "cap", "status", f'{D("first_dt")} AS first_dt', f'{D("last_dt")} AS last_dt',
        "collected_at", "next_cursor"],
        ["src_api", "ticker", "req_start", "req_end"], "req_start"),
}

# 변환실패 감지용 (원본 빈값 수 == Parquet NULL 수 여야 함)
NULL_CHECKS = [
    ("krx", "krx_stk_bydd_trd", "TDD_CLSPRC", "tdd_clsprc"),
    ("krx", "krx_stk_bydd_trd", "MKTCAP", "mktcap"),
    ("krx", "krx_ksq_bydd_trd", "TDD_CLSPRC", "tdd_clsprc"),
    ("krx", "krx_kospi_dd_trd", "CLSPRC_IDX", "clsprc_idx"),
    ("krx", "krx_etf_bydd_trd", "OBJ_STKPRC_IDX", "obj_stkprc_idx"),
    ("kiwoom", "ka10008_foreign_holdings", "close_pric", "close_pric"),
    ("kiwoom", "ka10060_investor_flows", "ind_invsr", "ind_invsr"),
    ("kiwoom", "ka20068_lending_balance", "dbrt_trde_irds", "dbrt_trde_irds"),
]

KNOWN_GAPS = """# KNOWN_GAPS — 이 데이터에 없는 것

이 배포본은 **원장 그대로의 typed 변환본**이다. 백테스트에 필요한 파생물은 포함하지 않는다.
아래를 모르고 쓰면 결과가 조용히 틀린다.

## 1. 수정주가가 없다 — 가장 위험

여기 담긴 가격은 **원주가**다. 액면분할·무상증자가 반영돼 있지 않다.
그대로 수익률을 계산하면 분할일에 -50% 같은 값이 나온다. 해당 이벤트는 **428건**이다.

카엘 쪽 조정계수도 아직 쓸 수 없다 — 457건 중 93건이 오염(20.3%)돼 있고,
`052670`에는 +29,948%가 잔류한다.

## 2. 배당 / 총수익(TR) 수익률이 없다

가격 수익률(PR)만 계산 가능하다. 16.6년 CAGR이 총수익 대비 **23~25% 과소**하게 나온다.
팩터마다 부호가 달라서(저변동성 -4.35%p/년, 밸류 +3.01%p) 단순 과소가 아니라 **조직적 편향**이다.

## 3. 유니버스 플래그가 없다

상장폐지일·관리종목·거래정지·정리매매 정보가 없다.

**중요**: 원장 자체에는 생존편향이 없다. `krx_stk_bydd_trd`는 그날 거래된 전 종목을 담는다.
편향은 **최신 종목마스터로 과거를 조회할 때** 생긴다.
폐지일이 필요하면 **원장에서 해당 종목이 마지막으로 등장한 날짜로 근사**해야 한다.

## 4. DART 재무·공시가 없다

본 백필 미착수. **밸류·퀄리티 팩터를 만들 수 없다** (PBR·PER·ROE·부채비율 등 전부).
현재 가능한 것은 **가격·수급 기반 팩터**로 한정된다:
모멘텀 · 변동성 · 거래량 · 투자자 수급 · 공매도 · 대차잔고.

## 재현성

`latest`는 매일 바뀐다. 원장 정정이 반영되면 **과거 값도 바뀔 수 있다.**
백테스트 재현이 필요하면 `latest`가 아니라 **특정 `build_id`를 고정**해서 쓸 것.
사용한 버전은 `manifest.json`의 `build_id`에 있다.
"""


def log(msg: str) -> None:
    print(f"[{datetime.now(KST):%H:%M:%S}] {msg}", flush=True)


def sha256_file(p: Path, chunk: int = 8 << 20) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        while blk := f.read(chunk):
            h.update(blk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="변환·검증만, 공개하지 않음")
    args = ap.parse_args()

    build_id = datetime.now(KST).strftime("%Y%m%dT%H%M")
    build_dir = SHARE / "build" / build_id
    vers_dir = SHARE / "v" / build_id
    t_start = time.perf_counter()

    # ── [0] 게이트: 원장 존재 확인 ──────────────────────────────────────────
    for db in ("krx.db", "kiwoom.db"):
        if not (RAW / db).exists():
            log(f"중단: 원장 없음 — {RAW/db}")
            return 1

    prev = None
    latest = SHARE / "latest"
    if latest.exists():
        try:
            prev = json.loads((latest / "manifest.json").read_text())
        except Exception:
            pass

    # 이전 실행이 남긴 미완성 산출물 정리 (성공 시엔 move로 비워지므로 실패분만 남음)
    build_root = SHARE / "build"
    if build_root.exists():
        for stale in build_root.iterdir():
            shutil.rmtree(stale, ignore_errors=True)
            log(f"이전 미완성 build 정리: {stale.name}")
    build_dir.mkdir(parents=True, exist_ok=True)
    log(f"build_id={build_id}")

    # ── [1] 단일 읽기 ────────────────────────────────────────────────────────
    con = duckdb.connect()
    con.execute("INSTALL sqlite; LOAD sqlite;")
    con.execute(f"ATTACH '{RAW}/krx.db' AS krx (TYPE sqlite, READ_ONLY);")
    con.execute(f"ATTACH '{RAW}/kiwoom.db' AS kiwoom (TYPE sqlite, READ_ONLY);")

    # ── [2] 변환 ────────────────────────────────────────────────────────────
    tables, total_rows = [], 0
    for tname, (alias, colfn, pk, datecol) in SPEC.items():
        out = build_dir / f"{tname}.parquet"
        t0 = time.perf_counter()
        con.execute(
            f"COPY (SELECT {', '.join(colfn())} FROM {alias}.\"{tname}\") "
            f"TO '{out}' (FORMAT parquet, COMPRESSION zstd);"
        )
        el = time.perf_counter() - t0

        src_rows = con.execute(f'SELECT COUNT(*) FROM {alias}."{tname}"').fetchone()[0]
        pq_rows = con.execute(f"SELECT COUNT(*) FROM '{out}'").fetchone()[0]
        dup = con.execute(
            f"SELECT COUNT(*) FROM (SELECT {', '.join(pk)} FROM '{out}' "
            f"GROUP BY ALL HAVING COUNT(*)>1)"
        ).fetchone()[0]
        dmin, dmax = con.execute(
            f"SELECT MIN({datecol}), MAX({datecol}) FROM '{out}'"
        ).fetchone()

        # ── [3] 검증 게이트 ──────────────────────────────────────────────
        if src_rows != pq_rows:
            log(f"중단: 행수 불일치 — {tname} 원본={src_rows:,} parquet={pq_rows:,}")
            return 1
        if dup:
            log(f"중단: PK 중복 {dup:,}건 — {tname} PK={pk}")
            return 1
        if pq_rows == 0:
            log(f"중단: 빈 테이블 — {tname}")
            return 1
        if prev:
            was = next((t["rows"] for t in prev.get("tables", []) if t["name"] == tname), None)
            if was and pq_rows < was:
                log(f"중단: 행수 감소 — {tname} 이전={was:,} 현재={pq_rows:,}")
                return 1

        total_rows += pq_rows
        tables.append({
            "name": tname, "rows": pq_rows,
            "min_date": str(dmin), "max_date": str(dmax),
            "bytes": out.stat().st_size,
            "columns": [d[0] for d in con.execute(f"DESCRIBE SELECT * FROM '{out}'").fetchall()],
        })
        log(f"  {tname:28s} {pq_rows:10,d}행 {el:6.1f}s {out.stat().st_size/1048576:7.1f}MB")

    # 변환실패 감지: 원본 빈값 수 == Parquet NULL 수
    for alias, tname, src_col, pq_col in NULL_CHECKS:
        s = con.execute(
            f'SELECT COUNT(*) FROM {alias}."{tname}" '
            f"WHERE \"{src_col}\" IS NULL OR trim(\"{src_col}\")=''"
        ).fetchone()[0]
        q = con.execute(
            f"SELECT COUNT(*) FROM '{build_dir}/{tname}.parquet' WHERE {pq_col} IS NULL"
        ).fetchone()[0]
        if s != q:
            log(f"중단: 변환실패 NULL — {tname}.{src_col} 원본빈값={s:,} parquetNULL={q:,}")
            return 1
    log("검증 통과: 행수·PK중복·행수감소·변환실패 NULL")

    # ── manifest / KNOWN_GAPS ───────────────────────────────────────────────
    log("원본 해시 계산 중...")
    t_hash = time.perf_counter()
    sources = {}
    for db in ("krx.db", "kiwoom.db"):
        p = RAW / db
        sources[db] = {
            "sha256": sha256_file(p),
            "mtime": datetime.fromtimestamp(p.stat().st_mtime, KST).isoformat(),
            "bytes": p.stat().st_size,
        }
    log(f"해시 완료 ({time.perf_counter()-t_hash:.1f}s)")

    manifest = {
        "build_id": build_id,
        "built_at": datetime.now(KST).isoformat(),
        "generator": "rebuild_share.py",
        "duckdb_version": duckdb.__version__,
        "sources": sources,
        "tables": tables,
        "total_rows": total_rows,
        "known_gaps": ["수정주가 없음(원주가)", "배당/TR수익률 없음",
                       "유니버스 플래그 없음", "DART 재무·공시 없음"],
        "notice": "typed 변환본. 원형 TEXT는 카엘 서버 원장에만 존재. KNOWN_GAPS.md 참조",
    }
    (build_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (build_dir / "KNOWN_GAPS.md").write_text(KNOWN_GAPS, encoding="utf-8")

    if args.dry_run:
        log(f"dry-run 종료 — 산출물은 {build_dir} 에 남아 있음")
        return 0

    # ── [4] 공개 이동 ────────────────────────────────────────────────────────
    vers_dir.parent.mkdir(parents=True, exist_ok=True)
    if vers_dir.exists():
        shutil.rmtree(vers_dir)
    shutil.move(str(build_dir), str(vers_dir))

    # ── [5] latest 원자 교체 (ln -sfn 은 unlink+symlink 라 비원자적) ─────────
    tmp = SHARE / ".latest.tmp"
    if tmp.exists() or tmp.is_symlink():
        tmp.unlink()
    tmp.symlink_to(Path("v") / build_id)          # 상대경로
    subprocess.run(["mv", "-Tf", str(tmp), str(latest)], check=True)
    log(f"공개: latest -> v/{build_id}")

    # ── [6] 보관 정책 ────────────────────────────────────────────────────────
    versions = sorted((SHARE / "v").iterdir(), key=lambda p: p.name, reverse=True)
    for old in versions[KEEP_VERSIONS:]:
        shutil.rmtree(old)
        log(f"보관정책: {old.name} 삭제")

    size = sum(t["bytes"] for t in tables) / 1048576
    log(f"완료 — {len(tables)}개 테이블 {total_rows:,}행 {size:.1f}MB "
        f"({time.perf_counter()-t_start:.1f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
