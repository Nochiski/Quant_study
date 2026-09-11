"""원장 5개의 "D 일자 수집 완료" 판정. 플랜 P1 Task 1.7.

기대치는 전부 2026-09-09 서버 실측(reviews/2026-09-09-daily-findings-A §6·B §7)이다. 판정 3등급:
  required — 실패면 rc 2(뒤 단계로 안 넘어간다)
  warn     — 로그·알림만
  halt     — 중단 신호(DEFECT-A-01 휴장 오확정 · DEFECT-A-03 KIS 중복 증식 · v3 키 사용): rc 2 + crit
종목 단위 테이블은 절대 하한이 아니라 **요청 유니버스 대비 비율**로 본다(리뷰 B2). 결과는 JSON 으로
`logs/health/<D>.json` 에 남기고 요약 문자열을 돌려준다.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
import sys
from dataclasses import asdict, dataclass
from enum import Enum

from backfill_wise import REQ_COVERED, REQ_NONE  # 종목당 일일 요청 수의 정본
from daily import calendar as _cal

KST = dt.timezone(dt.timedelta(hours=9))


class Level(str, Enum):
    REQUIRED = "required"
    WARN = "warn"
    HALT = "halt"


class Status(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    SKIP = "skip"


@dataclass(frozen=True)
class Check:
    name: str
    level: Level
    status: Status
    value: object
    expected: str
    detail: str = ""


@dataclass(frozen=True)
class HealthReport:
    date: str
    checks: tuple[Check, ...]
    universe_size: int

    @property
    def failed_required(self) -> tuple[Check, ...]:
        return tuple(c for c in self.checks if c.level is Level.REQUIRED and c.status is Status.FAIL)

    @property
    def halts(self) -> tuple[Check, ...]:
        return tuple(c for c in self.checks if c.level is Level.HALT and c.status is Status.FAIL)

    @property
    def ok(self) -> bool:
        return not self.failed_required and not self.halts

    def summary(self) -> str:
        n_pass = sum(1 for c in self.checks if c.status is Status.PASS)
        bad = [f"{c.name}={c.value} (기대 {c.expected})" for c in (*self.failed_required, *self.halts)]
        warn = [c.name for c in self.checks if c.level is Level.WARN and c.status is Status.FAIL]
        head = f"원장 건전성 {self.date}: {'OK' if self.ok else 'FAIL'} pass {n_pass}/{len(self.checks)}"
        if bad:
            head += " | 실패: " + "; ".join(bad)
        if warn:
            head += " | 경고: " + ", ".join(warn)
        return head


@dataclass
class Paths:
    krx: str
    kiwoom: str
    kis: str
    dart: str
    wise: str
    calendar: str = _cal.DEFAULT_PATH
    universe_state: str = ""
    prev_report: str = ""       # 전날 리포트(JSON) — KIS 중복쌍 증가분 비교용

    @classmethod
    def from_home(cls, home: str) -> Paths:
        raw = os.path.join(home, "data", "raw")
        return cls(krx=os.path.join(raw, "krx.db"), kiwoom=os.path.join(raw, "kiwoom.db"),
                   kis=os.path.join(raw, "kis.db"), dart=os.path.join(raw, "dart.db"),
                   wise=os.path.join(raw, "wisereport.db"),
                   calendar=os.path.join(home, "data", "calendar", "kis_holidays.json"),
                   universe_state=os.path.join(home, "data", "daily", "universe_kw.json"))


def _ro(path: str) -> sqlite3.Connection | None:
    if not os.path.exists(path):
        return None
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def _one(con: sqlite3.Connection, sql: str, args: tuple[object, ...] = ()) -> object:
    row = con.execute(sql, args).fetchone()
    return None if row is None else row[0]


def _count(con: sqlite3.Connection, sql: str, args: tuple[object, ...] = ()) -> int:
    """COUNT(*) 류 — NULL 이면 0."""
    v = _one(con, sql, args)
    return int(v) if isinstance(v, int | float | str) else 0


def _num(con: sqlite3.Connection, sql: str, args: tuple[object, ...] = ()) -> float | None:
    """AVG 류 — NULL 이면 None."""
    v = _one(con, sql, args)
    return float(v) if isinstance(v, int | float | str) else None


def _has_table(con: sqlite3.Connection, name: str) -> bool:
    return _one(con, "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)) is not None


def _requested_size(state_path: str) -> int:
    """요청 유니버스 크기 = 상태 파일의 오늘 스냅샷 보통주 + 유예 중 종목. 없으면 0(비율 게이트 skip)."""
    try:
        with open(state_path, encoding="utf-8") as f:
            st = json.load(f)
        return int(st.get("n_requested", 0))
    except (OSError, ValueError, TypeError):
        return 0


# ── KRX 기업행위 후보 (검수 종합 H1 · A §2 A01·A02) ───────────────────────
# 액면가가 그대로여도 상장주식수는 전환·증자·소각으로 매일 조금씩 움직인다 — 09-09 전수 6건 중
# 4건이 그런 변동(0.994~1.050)이었다. 그래서 잡는 기준은 "액면가 변경" 또는 "주식수 비가 1 에서
# 이만큼 넘게 벗어남" 둘이다. 0.5% 는 A02 의 최소 실측(019550 −0.58%)보다 낮게 잡은 값이다.
SHARES_RATIO_TOL = 0.005
# `value` 에 실을 종목 표본 상한 — 리포트 JSON 이 부풀지 않게 자른다(건수는 `n` 이 전부 센다).
CORP_ACTION_SAMPLE = 30
# 판정에 필요한 `krx_*_isu_base_info` 축. 하나라도 없으면 SKIP 이다(옛 스키마 원장 방어).
CORP_ACTION_COLUMNS = ("ISU_SRT_CD", "ISU_ABBRV", "PARVAL", "LIST_SHRS")
_CORP_ACTION_MARKETS = ("stk", "ksq")


def _has_columns(con: sqlite3.Connection, table: str, cols: tuple[str, ...]) -> bool:
    have = {str(r[1]) for r in con.execute(f"PRAGMA table_info({table})")}
    return all(c in have for c in cols)


def _krx_number(v: object) -> float | None:
    """KRX 수치 문자열(천단위 쉼표 포함) → float. 빈 값·`'-'`·비수치는 None."""
    if v is None:
        return None
    s = str(v).replace(",", "").strip()
    if s in ("", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def check_krx_corp_actions(con: sqlite3.Connection, d: str, d_prev: str) -> Check:
    """D 와 직전 거래일의 `*_isu_base_info` 를 `ISU_SRT_CD` 로 조인해 기업행위 후보를 뽑는다.

    원장은 정확한데 하류가 조용히 틀리는 종류다(검수 종합 H1): 액면병합 종목의 종가를 그대로
    이으면 001290 은 +345%, 273060 은 +380% 로 계상되고(실제 −10.9%/−4.1%) 모멘텀·변동성
    크로스섹션이 그 하루에 통째로 뒤집힌다. 행수·status 게이트로는 잡히지 않으므로 "기업행위가
    있었을 법한 종목" 을 매일 목록으로 남겨 `adj_factor` 산출과 대조할 수 있게 한다. warn 등급인
    것은 이것이 실패가 아니라 **확인 대상**이기 때문이다.
    """
    items: list[dict[str, object]] = []
    n_prev = 0
    for market in _CORP_ACTION_MARKETS:
        tbl = f"krx_{market}_isu_base_info"
        if not _has_table(con, tbl) or not _has_columns(con, tbl, CORP_ACTION_COLUMNS):
            return Check("krx.corp_action_candidates", Level.WARN, Status.SKIP, None,
                         f"{tbl} 에 {list(CORP_ACTION_COLUMNS)} 축이 없다 — 판정 불가")
        n_prev += _count(con, f"SELECT COUNT(*) FROM {tbl} WHERE bas_dd_req=?", (d_prev,))
        rows = con.execute(
            f"SELECT c.ISU_SRT_CD, c.ISU_ABBRV, p.PARVAL, c.PARVAL, p.LIST_SHRS, c.LIST_SHRS "
            f"FROM {tbl} c JOIN {tbl} p ON p.ISU_SRT_CD = c.ISU_SRT_CD AND p.bas_dd_req = ? "
            "WHERE c.bas_dd_req = ? AND (c.LIST_SHRS <> p.LIST_SHRS OR c.PARVAL <> p.PARVAL)",
            (d_prev, d)).fetchall()
        for code, name, pv0, pv1, sh0, sh1 in rows:
            a, b = _krx_number(sh0), _krx_number(sh1)
            ratio = round(b / a, 6) if (a and b is not None) else None
            if (_krx_number(pv0) == _krx_number(pv1)
                    and ratio is not None and abs(ratio - 1) <= SHARES_RATIO_TOL):
                continue
            items.append({"code": str(code), "name": str(name), "market": market,
                          "parval": [str(pv0), str(pv1)], "shares": [str(sh0), str(sh1)],
                          "shares_ratio": ratio})
    if n_prev == 0:
        return Check("krx.corp_action_candidates", Level.WARN, Status.SKIP, None,
                     f"직전 거래일({d_prev}) base_info 행 0 — 대조 대상 없음")
    items.sort(key=lambda x: (str(x["market"]), str(x["code"])))
    return Check("krx.corp_action_candidates", Level.WARN,
                 Status.PASS if not items else Status.FAIL,
                 {"n": len(items), "items": items[:CORP_ACTION_SAMPLE]},
                 f"{d_prev}→{d} 액면가 변경 또는 주식수 비 ≠ 1(±{SHARES_RATIO_TOL:.1%} 초과) 0건 "
                 "— 있으면 adj_factor 산출과 대조한다 (검수 H1)")


# ── KRX (A §6-1) ───────────────────────────────────────────────────────────
def check_krx(con: sqlite3.Connection, d: str, d_prev: str, cal: _cal.Calendar) -> list[Check]:
    out: list[Check] = []
    rows = con.execute("SELECT endpoint, status FROM ingest_log WHERE bas_dd=?", (d,)).fetchall()
    n_ok = sum(1 for _, s in rows if s == "ok")
    n_hol = sum(1 for _, s in rows if s == "holiday")
    n_bad = sum(1 for _, s in rows if s not in ("ok", "holiday"))
    contract_ok = len(rows) == 7 and n_bad == 0 and (n_ok == 7 or n_hol == 7)
    out.append(Check("krx.ingest_log", Level.REQUIRED, Status.PASS if contract_ok else Status.FAIL,
                     {"n": len(rows), "ok": n_ok, "holiday": n_hol, "other": n_bad},
                     "7 endpoints, all ok or all holiday, none pending/rate/error"))
    dd = dt.date(int(d[:4]), int(d[4:6]), int(d[6:8]))
    misfire = n_hol > 0 and cal.is_trading_day(dd)
    out.append(Check("krx.holiday_misfire", Level.HALT, Status.FAIL if misfire else Status.PASS,
                     n_hol, "0 holiday rows on a calendar trading day (DEFECT-A-01)"))
    if n_ok == 7:
        cnt = {}
        for tbl in ("krx_stk_bydd_trd", "krx_ksq_bydd_trd", "krx_stk_isu_base_info", "krx_ksq_isu_base_info",
                    "krx_kospi_dd_trd", "krx_kosdaq_dd_trd", "krx_etf_bydd_trd"):
            cnt[tbl] = _count(con, f"SELECT COUNT(*) FROM {tbl} WHERE bas_dd_req=?", (d,)) if _has_table(con, tbl) else 0
        ok = (cnt["krx_stk_bydd_trd"] >= 920 and cnt["krx_ksq_bydd_trd"] >= 1780
              and cnt["krx_kospi_dd_trd"] == 51 and cnt["krx_kosdaq_dd_trd"] == 40
              and cnt["krx_etf_bydd_trd"] >= 1120
              and cnt["krx_stk_bydd_trd"] == cnt["krx_stk_isu_base_info"]
              and cnt["krx_ksq_bydd_trd"] == cnt["krx_ksq_isu_base_info"])
        out.append(Check("krx.rows", Level.REQUIRED, Status.PASS if ok else Status.FAIL, cnt,
                         "stk>=920 ksq>=1780 kospi=51 kosdaq=40 etf>=1120, stk=stk_base, ksq=ksq_base (20거래일 실측)"))
    out.append(check_krx_corp_actions(con, d, d_prev))
    return out


# ── 키움 (A §6-2) ──────────────────────────────────────────────────────────
def check_kiwoom(con: sqlite3.Connection, krx: sqlite3.Connection | None, d: str, d_prev: str,
                 n_req: int) -> list[Check]:
    out: list[Check] = []
    for tbl in ("ka10008_foreign_holdings", "ka10060_investor_flows", "ka20068_lending_balance"):
        n = _count(con, f"SELECT COUNT(*) FROM {tbl} WHERE dt=?", (d,)) if _has_table(con, tbl) else 0
        if n_req <= 0:
            out.append(Check(f"kiwoom.{tbl[:7]}.rows", Level.REQUIRED, Status.SKIP, n, "요청 유니버스 크기 미상 — 비율 판정 불가"))
            continue
        ratio = n / n_req
        out.append(Check(f"kiwoom.{tbl[:7]}.rows", Level.REQUIRED, Status.PASS if ratio >= 0.98 else Status.FAIL,
                         {"n": n, "requested": n_req, "ratio": round(ratio, 4)}, "rows / requested >= 0.98"))
    if _has_table(con, "ka10014_short_selling"):
        n = _count(con, "SELECT COUNT(*) FROM ka10014_short_selling WHERE dt=?", (d,))
        avg = _num(con, "SELECT AVG(n) FROM (SELECT COUNT(*) n FROM ka10014_short_selling WHERE dt<? GROUP BY dt ORDER BY dt DESC LIMIT 20)", (d,))
        ratio = (n / avg) if avg else None
        out.append(Check("kiwoom.ka10014.trend", Level.WARN,
                         Status.SKIP if ratio is None else (Status.PASS if ratio >= 0.80 else Status.FAIL),
                         {"n": n, "avg20": None if avg is None else round(avg, 1)}, "n / avg(최근 20거래일) >= 0.80 (변동 12% 라 절대치 금지)"))
    if _has_table(con, "ka10008_foreign_holdings"):
        row = con.execute(
            "SELECT COUNT(*), SUM(a.poss_stkcnt = b.poss_stkcnt) FROM ka10008_foreign_holdings a "
            "JOIN ka10008_foreign_holdings b ON a.ticker=b.ticker AND a.dt=? AND b.dt=?", (d, d_prev)).fetchone()
        n, same = (int(row[0]), int(row[1] or 0)) if row else (0, 0)
        pct = (100.0 * same / n) if n else None
        out.append(Check("kiwoom.ka10008.stale_pct", Level.REQUIRED,
                         Status.SKIP if pct is None else (Status.PASS if pct <= 30 else Status.FAIL),
                         None if pct is None else round(pct, 1), "<= 30% (정상 9.7% / 08-24 오염 99.0%)"))
        if krx is not None and _has_table(krx, "krx_stk_bydd_trd"):
            k = {r[0]: (int(r[1]), int(r[2])) for r in krx.execute(
                "SELECT ISU_CD, TDD_CLSPRC, ACC_TRDVOL FROM krx_stk_bydd_trd WHERE bas_dd_req=? "
                "UNION ALL SELECT ISU_CD, TDD_CLSPRC, ACC_TRDVOL FROM krx_ksq_bydd_trd WHERE bas_dd_req=?", (d, d))}
            matched = same_c = same_v = 0
            for tk, close, vol in con.execute("SELECT ticker, close_pric, trde_qty FROM ka10008_foreign_holdings WHERE dt=?", (d,)):
                if tk in k:
                    matched += 1
                    try:
                        same_c += int(abs(int(close)) == k[tk][0])
                        same_v += int(int(vol) == k[tk][1])
                    except (TypeError, ValueError):
                        pass
            ok = matched > 0 and same_c == matched and same_v == matched
            out.append(Check("kiwoom.krx_cross", Level.REQUIRED, Status.PASS if ok else (Status.SKIP if matched == 0 else Status.FAIL),
                             {"matched": matched, "same_close": same_c, "same_vol": same_v},
                             "종가·거래량 100% 일치 (08-20 실측 2,602/2,602)"))
    if _has_table(con, "ka10099_stock_master"):
        row = con.execute("SELECT COUNT(*), COUNT(DISTINCT mrkt_tp) FROM ka10099_stock_master WHERE snap_date=?", (d,)).fetchone()
        n, mk = (int(row[0]), int(row[1])) if row else (0, 0)
        out.append(Check("kiwoom.master", Level.REQUIRED, Status.PASS if (mk == 2 and n >= 4200) else Status.FAIL,
                         {"n": n, "markets": mk}, "2 markets, n >= 4200 (실측 4,307~4,309)"))
    return out


# ── KIS (A §6-3) ───────────────────────────────────────────────────────────
def check_kis(con: sqlite3.Connection, d_prev: str, d_minus40: str, n_req: int, prev_pairs: int | None) -> list[Check]:
    out: list[Check] = []
    if not _has_table(con, "kis_credit_balance"):
        return out
    row = con.execute("SELECT COUNT(*), COUNT(DISTINCT req_ticker) FROM kis_credit_balance WHERE deal_date=?", (d_prev,)).fetchone()
    n, tk = (int(row[0]), int(row[1])) if row else (0, 0)
    if n_req <= 0:
        out.append(Check("kis.credit.rows", Level.WARN, Status.SKIP, n, "요청 유니버스 크기 미상"))
    else:
        ratio = n / n_req
        out.append(Check("kis.credit.rows", Level.WARN, Status.PASS if (ratio >= 0.95 and tk == n) else Status.FAIL,
                         {"n": n, "distinct": tk, "requested": n_req, "ratio": round(ratio, 4)},
                         f"deal_date={d_prev} rows/requested >= 0.95 and distinct = rows (D-2, 실측: 조회일 기준 T-3 까지 온다)"))
    from daily import (
        kis_daily,  # payload 동일 중복만 센다(정정은 제외) — 정의를 한 곳에 둔다
    )
    pairs = kis_daily.dup_pairs(con, d_minus40)
    if prev_pairs is None:
        out.append(Check("kis.credit.dup_growth", Level.HALT, Status.SKIP, pairs, "전날 리포트 없음 — 기준선 기록"))
    else:
        out.append(Check("kis.credit.dup_growth", Level.HALT, Status.PASS if pairs <= prev_pairs else Status.FAIL,
                         {"pairs": pairs, "prev": prev_pairs}, "최근 40일 payload 동일 중복쌍 증가 0 (DEFECT-A-03; 값이 다른 정정은 제외)"))
    return out


# ── DART (B §7-1·7-3·7-5) ──────────────────────────────────────────────────
def check_dart(con: sqlite3.Connection, d: str, trading_day: bool, kst_day_start_utc: str) -> list[Check]:
    out: list[Check] = []
    if _has_table(con, "dart_disclosure"):
        n = _count(con, "SELECT COUNT(DISTINCT rcept_no) FROM dart_disclosure WHERE rcept_dt=?", (d,))
        exp = "영업일 >= 400 (실측 410~2,148)" if trading_day else "휴장일 0 허용"
        out.append(Check("dart.disclosure.rows", Level.REQUIRED, Status.PASS if (n >= 400 or not trading_day) else Status.FAIL, n, exp))
        if _has_table(con, "doc_store"):
            row = con.execute(
                "WITH tgt AS (SELECT DISTINCT rcept_no FROM dart_disclosure WHERE rcept_dt=? AND stock_code<>'' AND ("
                " report_nm LIKE '%주식분할결정%' OR report_nm LIKE '%주식병합결정%' OR (report_nm NOT LIKE '%연장신고%' AND ("
                " report_nm LIKE '%사업보고서%' OR report_nm LIKE '%반기보고서%' OR report_nm LIKE '%분기보고서%')))) "
                "SELECT COUNT(*), SUM(s.rcept_no IS NULL), SUM(s.zip_ok=0 AND s.http_status<>'014') "
                "FROM tgt t LEFT JOIN doc_store s USING (rcept_no)", (d,)).fetchone()
            n_t, never, failed = (int(row[0]), int(row[1] or 0), int(row[2] or 0)) if row else (0, 0, 0)
            out.append(Check("dart.docs", Level.REQUIRED, Status.PASS if (never == 0 and failed == 0) else Status.FAIL,
                             {"target": n_t, "never_tried": never, "failed": failed}, "never_tried 0, 014 외 실패 0"))
    if _has_table(con, "dart_call_log"):
        rows = con.execute("SELECT key_id, COUNT(*) FROM dart_call_log WHERE ts > ? GROUP BY key_id", (kst_day_start_utc,)).fetchall()
        by = {k: int(n) for k, n in rows}
        kael = by.get("kael", 0)
        out.append(Check("dart.key.kael", Level.HALT, Status.FAIL if kael > 0 else Status.PASS, kael, "0 (v3 프로덕션 키 사용 금지)"))
        ours = sum(v for k, v in by.items() if k != "kael")
        out.append(Check("dart.budget", Level.WARN, Status.PASS if ours <= 2500 else Status.FAIL, by, "k2+k3 <= 2,500/일 (평시 830~2,000)"))
    return out


# ── WISE (B §7-4) ──────────────────────────────────────────────────────────
def check_wise(con: sqlite3.Connection, d_iso: str, next_iso: str) -> list[Check]:
    """WISE 스냅샷은 대상일 D 저녁 18:05 에 찍힌다(결정 V2-3) — 판정은 실행일이 아니라 D 의 KST 날짜로 한다.

    전환기에는 옛 방식(D+1 아침 06:00) 스냅샷만 있을 수 있어, D 행이 없으면 next_iso(D+1) 로 폴백하고
    어느 쪽을 읽었는지 `wise.snapshot_day` 에 남긴다. 둘 다 없으면 종전대로 FAIL.
    """
    out: list[Check] = []
    if not _has_table(con, "ws_run_log"):
        return out
    sql = ("SELECT run_at, mode, n_stocks, n_req, n_ok, n_bad FROM ws_run_log "
           "WHERE date(run_at, '+9 hours') = ? ORDER BY run_at DESC LIMIT 1")
    day, run = d_iso, con.execute(sql, (d_iso,)).fetchone()
    if run is None:
        day, run = next_iso, con.execute(sql, (next_iso,)).fetchone()
    out.append(Check("wise.snapshot_day", Level.WARN, Status.SKIP if run is None else Status.PASS,
                     None if run is None else ("target_day" if day == d_iso else "next_morning"),
                     f"D({d_iso}) 저녁 스냅샷 — 없으면 D+1({next_iso}) 아침 스냅샷으로 폴백(전환기)"))
    if run is None:
        out.append(Check("wise.run", Level.REQUIRED, Status.FAIL, None,
                         f"{d_iso}(또는 폴백 {next_iso}) 실행 1건 (full, n_bad 0)"))
        return out
    _, mode, n_stocks, n_req, n_ok, n_bad = run
    out.append(Check("wise.run", Level.REQUIRED, Status.PASS if (mode == "full" and n_bad == 0 and n_ok == n_req) else Status.FAIL,
                     {"mode": mode, "n_stocks": n_stocks, "n_req": n_req, "n_ok": n_ok, "n_bad": n_bad}, "mode=full, n_bad=0, n_ok=n_req"))
    cov = none = -1
    if _has_table(con, "ws_coverage"):
        # 그 런이 갱신한(checked_at = 스냅샷 날짜 KST) 커버리지 행만 센다 — 런 뒤에 상태가 바뀐 종목이 있으면
        # 전체 집계로는 등식이 깨진다(09-09 실측: 전체 808/1758 vs 당일 807/1756, n_req 15,617 은 후자와 일치).
        cov = _count(con, "SELECT COUNT(*) FROM ws_coverage WHERE status='covered' AND date(checked_at, '+9 hours')=?", (day,))
        none = _count(con, "SELECT COUNT(*) FROM ws_coverage WHERE status='none' AND date(checked_at, '+9 hours')=?", (day,))
        expected = cov * REQ_COVERED + none * REQ_NONE
        out.append(Check("wise.req_identity", Level.REQUIRED, Status.PASS if expected == n_req else Status.FAIL,
                         {"expected": expected, "actual": n_req, "covered": cov, "none": none},
                         f"{day} checked_at 기준 covered×{REQ_COVERED} + none×{REQ_NONE} == n_req "
                         "(무커버 4 = 목록 1 + 3개년 cF5001, 09-10 검수 D H1 이후)"))
        rate = cov / (cov + none) if (cov + none) else None
        out.append(Check("wise.cov_rate", Level.WARN, Status.SKIP if rate is None else (Status.PASS if rate >= 0.25 else Status.FAIL),
                         None if rate is None else round(rate, 3), ">= 0.25 (실측 0.315; 미만이면 페이지 개편 의심)"))
    if _has_table(con, "ws_raw"):
        row = con.execute("SELECT COUNT(*), COUNT(DISTINCT cmp_cd) FROM ws_raw WHERE fetched_date=?", (day,)).fetchone()
        n, s = (int(row[0]), int(row[1])) if row else (0, 0)
        # 절대 밴드(15,500~15,700 · 2,560~2,570)는 무커버 4콜 전환(09-10)과 규모구분 갱신(09-11: 2,563→2,610)에
        # 모두 오탐을 냈다 — 기대치는 커버리지 항등식과 유니버스에서 유도한다. 커버리지 표가 없으면 하한만 본다.
        if cov >= 0 and none >= 0:
            exp_rows, exp_stocks = cov * REQ_COVERED + none * REQ_NONE, cov + none
            ok = n == exp_rows and s == exp_stocks and s >= 2400
            expected = f"rows == covered×{REQ_COVERED} + none×{REQ_NONE} ({exp_rows:,}) · stocks == covered+none ({exp_stocks:,}) · stocks >= 2,400"
        else:
            ok = n >= 15000 and s >= 2400
            expected = "커버리지 표 없음 — rows >= 15,000 · stocks >= 2,400"
        out.append(Check("wise.raw", Level.REQUIRED, Status.PASS if ok else Status.FAIL, {"rows": n, "stocks": s}, expected))
    return out


def run(d: str, paths: Paths, *, today: dt.date | None = None,
        skip: frozenset[str] = frozenset()) -> HealthReport:
    """skip 에 든 소스(krx·kiwoom·kis·dart·wise)는 판정하지 않고 SKIP 1건으로 기록한다(예: 앱키 분리 전 kiwoom)."""
    cal = _cal.load(paths.calendar)
    dd = dt.date(int(d[:4]), int(d[4:6]), int(d[6:8]))
    d_prev = cal.prev_trading_day(dd).strftime("%Y%m%d")
    d_prev2 = cal.prev_trading_day(dd, n=2).strftime("%Y%m%d")   # KIS 신용잔고 판정일(실측: 조회일 기준 T-3 = D-2)
    d_minus40 = (dd - dt.timedelta(days=40)).strftime("%Y%m%d")
    today = today or dt.datetime.now(KST).date()
    kst_start_utc = (dt.datetime.combine(today, dt.time(0), KST).astimezone(dt.UTC)).strftime("%Y-%m-%dT%H:%M:%S")
    n_req = _requested_size(paths.universe_state)
    prev_pairs: int | None = None
    if paths.prev_report and os.path.exists(paths.prev_report):
        try:
            with open(paths.prev_report, encoding="utf-8") as f:
                prev = json.load(f)
            for c in prev.get("checks", []):
                if c.get("name") == "kis.credit.dup_growth":
                    v = c.get("value")
                    prev_pairs = int(v["pairs"]) if isinstance(v, dict) else int(v)
        except (OSError, ValueError, TypeError, KeyError):
            prev_pairs = None
    checks: list[Check] = []
    krx = _ro(paths.krx)
    kw = _ro(paths.kiwoom)
    kis = _ro(paths.kis)
    dart = _ro(paths.dart)
    wise = _ro(paths.wise)
    try:
        for name in sorted(skip):
            checks.append(Check(f"{name}.skipped", Level.WARN, Status.SKIP, None, "--skip 로 판정 제외(운영 결정)"))
        if krx is not None and "krx" not in skip:
            checks += check_krx(krx, d, d_prev, cal)
        if kw is not None and "kiwoom" not in skip:
            checks += check_kiwoom(kw, krx, d, d_prev, n_req)
        if kis is not None and "kis" not in skip:
            checks += check_kis(kis, d_prev2, d_minus40, n_req, prev_pairs)
        if dart is not None and "dart" not in skip:
            checks += check_dart(dart, d, cal.is_trading_day(dd), kst_start_utc)
        if wise is not None and "wise" not in skip:
            checks += check_wise(wise, dd.isoformat(), (dd + dt.timedelta(days=1)).isoformat())
    finally:
        for c in (krx, kw, kis, dart, wise):
            if c is not None:
                c.close()
    return HealthReport(d, tuple(checks), n_req)


def write_report(report: HealthReport, out_dir: str) -> str:
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{report.date}.json")
    payload = {"date": report.date, "ok": report.ok, "universe_size": report.universe_size,
               "checks": [{**asdict(c), "level": c.level.value, "status": c.status.value} for c in report.checks],
               "summary": report.summary()}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1, default=str)
    return path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", required=True, help="판정 대상 거래일 YYYYMMDD (보통 T-1)")
    ap.add_argument("--home", default=os.environ.get("QL_HOME", "/home/kael/quant-ledger"))
    ap.add_argument("--out", default=None, help="리포트 디렉터리 (기본 <home>/logs/health)")
    ap.add_argument("--skip", default="", help="판정 제외 소스, 쉼표구분 (krx,kiwoom,kis,dart,wise)")
    a = ap.parse_args(argv)
    skip = frozenset(x.strip() for x in a.skip.split(",") if x.strip())
    bad = skip - {"krx", "kiwoom", "kis", "dart", "wise"}
    if bad:
        raise ValueError(f"unknown --skip source: {sorted(bad)} (allowed: krx,kiwoom,kis,dart,wise)")
    paths = Paths.from_home(a.home)
    out_dir = a.out or os.path.join(a.home, "logs", "health")
    cal = _cal.load(paths.calendar)
    dd = dt.date(int(a.date[:4]), int(a.date[4:6]), int(a.date[6:8]))
    paths.prev_report = os.path.join(out_dir, cal.prev_trading_day(dd).strftime("%Y%m%d") + ".json")
    rep = run(a.date, paths, skip=skip)
    path = write_report(rep, out_dir)
    print(rep.summary())
    print(f"report: {path}")
    return 0 if rep.ok else 2


if __name__ == "__main__":
    sys.exit(main())
