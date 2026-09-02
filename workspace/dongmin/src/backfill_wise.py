#!/usr/bin/env python3
"""WISEreport 컨센서스 리비전 수집 — 원장 (data/raw/wisereport.db).

배경 (2026-09-01 스파이크 실측):
  · 페이지 HTML 은 빈 껍데기고 숫자는 AJAX(JSON)로 온다. 브라우저는 불필요 —
    평문 GET + Referer/X-Requested-With 면 뚫린다 (v3 의 Playwright 는 과설계였다).
  · cF5001/cF5002 는 대상연도(yymm)별 "월말 13개월" 시계열을 준다. 창은 고정
    롤링이라 매달 가장 오래된 한 달이 영구 소실된다 — 수집 시작이 빠를수록 이득.
  · 최신 점은 일중에도 움직인다(06/30 실측: v3 오전값 7252 vs 월말 확정 7235).
    일별 해상도는 소스가 주지 않으므로 "매일 1회 스냅샷"으로 우리가 만든다.
  · v3(consensus_revision_daily)는 PK 에 target_period 가 빠져 2027E·2028E 를
    매일 덮어써 버린다. 이 수집기의 존재 이유다.

원장 원칙: 응답 JSON 원문을 그대로(zlib 압축만) 보존하고 해석하지 않는다.
파싱·정규화는 stage 몫. 유일한 판단은 커버리지(전값 None → 무커버) — KIS 의
no_data 확정과 동급인 "수집 대상 관리" 메타데이터다.

속도: requests.Session keep-alive + 스레드 10 + 지터. 초도 전수 ~20분.
일일은 무커버(한국 상장사 ~79%가 커버리지 0)를 주 1회로 미뤄 ~10분.
"""
import argparse
import hashlib
import json
import os
import random
import re
import sqlite3
import sys
import threading
import time
import urllib.parse
import zlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import requests

BASE_DIR = os.environ.get("QL_HOME") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB   = os.path.join(BASE_DIR, "data", "raw", "wisereport.db")
KRX  = os.path.join(BASE_DIR, "data", "raw", "krx.db")
WISE = "https://navercomp.wisereport.co.kr"
UA   = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
WORKERS = 10
JITTER  = (0.05, 0.25)
YYMMS   = 3          # 대상연도: CK=1(당해) 포함 최근 3개 — 2026E·2027E·2028E

DDL = """
CREATE TABLE IF NOT EXISTS ws_raw (
  cmp_cd       TEXT NOT NULL,
  ep           TEXT NOT NULL,       -- cF5001 등
  pkey         TEXT NOT NULL,       -- 요청 구분 파라미터 (yymm 등. 없으면 '')
  fetched_date TEXT NOT NULL,       -- KST 날짜 = 스냅샷 축
  body         BLOB NOT NULL,       -- 응답 원문 zlib. 해석하지 않는다
  sha256       TEXT NOT NULL,
  bytes        INTEGER NOT NULL,    -- 압축 전 크기
  fetched_at   TEXT NOT NULL,
  PRIMARY KEY (cmp_cd, ep, pkey, fetched_date));
CREATE TABLE IF NOT EXISTS ws_call_log (
  ts TEXT NOT NULL, cmp_cd TEXT, ep TEXT, pkey TEXT,
  status TEXT NOT NULL, bytes INTEGER, ms INTEGER);
CREATE TABLE IF NOT EXISTS ws_run_log (
  run_at TEXT NOT NULL, mode TEXT, n_stocks INTEGER, n_req INTEGER,
  n_ok INTEGER, n_bad INTEGER, bad_summary TEXT);
CREATE TABLE IF NOT EXISTS ws_coverage (
  cmp_cd TEXT PRIMARY KEY,
  status TEXT NOT NULL,             -- covered · none
  checked_at TEXT NOT NULL);
"""

_tls = threading.local()
_enc_lock = threading.Lock()
_enc: dict[str, str] = {}      # {"v": token} — 전역 공유 실측 확인 (2026-09-01)


def encparam(refresh: bool = False) -> str:
    """아무 종목의 c1030001 페이지에서 토큰을 뽑는다. 만료 시 refresh=True 로 1회 갱신."""
    with _enc_lock:
        if _enc.get("v") and not refresh:
            return _enc["v"]
        ref = f"{WISE}/v2/company/c1030001.aspx?cmp_cd=005930"
        html = sess().get(ref + "&cn=", headers={"Referer": ref}, timeout=30).text
        m = re.search(r"encparam\s*[:=]\s*['\"]([A-Za-z0-9+/=]+)['\"]", html)
        if not m:
            raise RuntimeError(f"encparam 추출 실패 — 페이지 개편 가능성. url={ref} len={len(html)}")
        _enc["v"] = m.group(1)
        return _enc["v"]


def sess() -> requests.Session:
    """스레드별 keep-alive 세션 — 핸드셰이크 낭비(요청당 ~250ms)를 없앤다."""
    if not hasattr(_tls, "s"):
        s = requests.Session()
        s.headers.update({"User-Agent": UA, "X-Requested-With": "XMLHttpRequest"})
        _tls.s = s
    return _tls.s


def kst_today() -> str:
    return (datetime.utcnow() + timedelta(hours=9)).strftime("%Y-%m-%d")


def now_utc() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")


def fetch(cmp_cd: str, ep: str, pkey: str, url: str) -> tuple[str, str, bytes, int, int]:
    """(cmp_cd, verdict, body, bytes, ms). 실패는 verdict 로만 말하고 예외를 안 던진다."""
    t0 = time.time()
    try:
        r = sess().get(url, headers={"Referer": f"{WISE}/v2/company/c1050001.aspx?cmp_cd={cmp_cd}"},
                       timeout=30)
        ms = int((time.time() - t0) * 1000)
        if r.status_code != 200:
            return cmp_cd, f"http{r.status_code}", b"", 0, ms
        return cmp_cd, "ok", r.content, len(r.content), ms
    except Exception as e:  # noqa: BLE001  # reason: 수집 루프 보호 — 사유는 verdict 로 기록
        return cmp_cd, f"exc/{type(e).__name__}", b"", 0, int((time.time() - t0) * 1000)
    finally:
        time.sleep(random.uniform(*JITTER))


def dt_today() -> str:
    return (datetime.utcnow() + timedelta(hours=9)).strftime("%Y%m%d")


def jobs_for(cmp_cd: str, yymms: list[str]) -> list[tuple[str, str, str]]:
    """(ep, pkey, url) 목록 — 일일 필수 세트 (컨센서스 계열, encparam 불요)."""
    dt = dt_today()
    out = [("c1050001_data", "", f"{WISE}/company/ajax/c1050001_data.aspx?cmp_cd={cmp_cd}")]
    for ym in yymms:
        out.append(("cF5001", ym, f"{WISE}/company/ajax/cF5001.aspx?cmp_cd={cmp_cd}&dt={dt}&yymm={ym}"))
        out.append(("cF5002", ym, f"{WISE}/company/ajax/cF5002.aspx?cmp_cd={cmp_cd}&dt={dt}&yymm={ym}"))
    return out


def validate(ep: str, body: bytes) -> str | None:
    """저장 전 본문 검증 — None 이면 통과. HTTP 200 이어도 내용이 깨진 "조용한 실패"를 거른다.

    실측 근거(2026-09-01): 거래정지 종목(082640)의 페이지가 200 + 118바이트
    안내 스크립트로 왔다. 구조 개편 시에도 같은 방식으로 잡힌다."""
    if not body:
        return "empty"
    if ep == "c1010001":
        t = body.decode("utf-8", errors="replace")
        if "올바른 종목이 아닙니다" in t:
            return "invalid_stock"      # 거래정지·비상장 등 — WISE 페이지 층 차단
        if "접속장애" in t:
            return "errpage"
        if len(body) < 5000:
            return "too_small"
        if "투자의견" not in t and "추정기관수" not in t:
            return "marker_missing"     # 페이지는 왔는데 기대 표가 없다 = 개편 의심
        return None
    if body[:1] != b"{":
        return "errpage" if b"HTML" in body[:120] else "not_json"
    try:
        j = json.loads(body)
    except Exception:  # noqa: BLE001  # reason: 어떤 예외든 판정은 json_broken 하나다
        return "json_broken"
    if isinstance(j, dict) and not (set(j) & {"JsonData", "chart1", "YYMM", "DATA", "dt"}):
        return "struct_changed"         # 알려진 최상위 키가 전무 = 응답 구조 개편
    return None


def is_covered(cf5001_body: bytes) -> bool:
    """커버 판정 — EPS·매출액·목표주가 세 신호 중 하나라도 값이 있으면 covered.

    EPS 단독 기준은 2026-09-01 전수 재검증에서 98종목(5.3%)을 놓쳤다 — 적자기업 등
    EPS 추정 없이 목표주가(84)·매출(14)만 커버되는 종목이 실재한다. 순수 3신호-없음
    표본 200 은 연간표(flag=2)에도 추정 행이 0 이라, 세 신호가 커버리지의 전부다.
    오판 비용이 비대칭(false-none = 영구 손실)이므로 파싱 불능도 covered 로 둔다."""
    try:
        j = json.loads(cf5001_body)
        c1, c2 = json.loads(j["chart1"]), json.loads(j["chart2"])
        return (any(v is not None for v in c1.get("select_item", []))
                or any(v is not None for v in c2.get("select_item", []))
                or any(v is not None for v in c1.get("target_price", [])))
    except Exception:  # noqa: BLE001  # reason: 파싱 불능은 커버 판정 보류(covered 취급)와 같다
        return True


def universe(con_w: sqlite3.Connection) -> list[str]:
    """유니버스 = 키움 ka10099 일별 마스터(최신 스냅샷) 우선, 없으면 KRX 폴백.

    키움 마스터는 master_daily.py 가 매일 적재한다 — KRX 원장(--to 하드코딩으로
    08-20 동결)보다 최신이라 신규상장이 다음 날 자동 편입된다 (2026-09-01 결정).
    보통주 판정은 upSizeName(대/중/소형주) 채움 여부다 — 거래소 규모구분은 보통주에만
    붙는다(실측: 코스피 827 ≈ KRX 보통주 832 · ETF/ETN/우선주/스팩/외국주는 빈값)."""
    kw = os.path.join(BASE_DIR, "data", "raw", "kiwoom.db")
    try:
        con = sqlite3.connect(f"file:{kw}?mode=ro", uri=True)
        d = con.execute("SELECT MAX(snap_date) FROM ka10099_stock_master").fetchone()[0]
        rows = con.execute(
            "SELECT code FROM ka10099_stock_master WHERE snap_date=? AND upSizeName<>''",
            (d,)).fetchall()
        con.close()
        if rows:
            print(f"  · 유니버스 = 키움 마스터 {d} ({len(rows):,}종목)", flush=True)
            return sorted(r[0] for r in rows)
    except sqlite3.Error as e:
        print(f"  ! 키움 마스터 조회 실패({e!r}) — KRX 스냅샷으로 폴백", flush=True)
    con = sqlite3.connect(f"file:{KRX}?mode=ro", uri=True)
    tks: set[str] = set()
    for t in ("krx_stk_isu_base_info", "krx_ksq_isu_base_info"):
        d = con.execute(f"SELECT MAX(bas_dd_req) FROM {t}").fetchone()[0]
        rows = con.execute(
            f"SELECT ISU_SRT_CD FROM {t} WHERE bas_dd_req=? "
            f"AND KIND_STKCERT_TP_NM LIKE '보통주%'", (d,)).fetchall()
        tks |= {r[0] for r in rows}
    con.close()
    return sorted(tks)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="daily", choices=("daily", "full"),
                    help="daily=무커버 스킵 · full=전 종목 (초도·주 1회 재분류)")
    ap.add_argument("--limit", type=int, default=0, help="종목 수 상한 (시험용)")
    a = ap.parse_args()

    con = sqlite3.connect(DB, timeout=60)
    con.execute("PRAGMA busy_timeout=60000")
    con.executescript(DDL)

    tks = universe(con)
    if a.mode == "daily":
        skip = {r[0] for r in con.execute("SELECT cmp_cd FROM ws_coverage WHERE status='none'")}
        tks = [t for t in tks if t not in skip]
        print(f"  · daily — 무커버 {len(skip):,} 종목 스킵", flush=True)
    if a.limit:
        tks = tks[:a.limit]
    today = kst_today()
    done = {r[0] for r in con.execute(
        "SELECT DISTINCT cmp_cd FROM ws_raw WHERE fetched_date=? AND ep='cF5001'", (today,))}
    tks = [t for t in tks if t not in done]
    print(f"  · 대상 {len(tks):,} 종목 (오늘 기수집 {len(done):,} 제외) · 스냅샷 {today}", flush=True)
    if not tks:
        return

    t0 = time.time()
    n_req = n_ok = n_cov = n_none = 0
    badkinds: dict[str, int] = {}

    def collect(cmp_cd: str):
        """한 종목의 일일 세트. 첫 cF5001 이 전값 None 이면 나머지 연도는 건너뛴다."""
        out, ymms = [], []
        # 대상연도 목록 — CK=1 포함 최근 YYMMS 개
        ep, pk, url = jobs_for(cmp_cd, [])[0]
        _, v, body, nb, ms = fetch(cmp_cd, ep, pk, url)
        out.append((ep, pk, v, body, nb, ms))
        if v == "ok":
            try:
                jd = json.loads(body)["JsonData"]
                per = [str(d["YYMM"]) for d in jd if d.get("YYMM")]
                ck = [str(d["YYMM"]) for d in jd if d.get("YYMM") and d.get("CK") == 1]
                base = per.index(ck[0]) if ck else max(len(per) - YYMMS, 0)
                ymms = per[base:base + YYMMS] or per[-YYMMS:]
            except Exception:  # noqa: BLE001  # reason: 목록 파싱 실패 시 기본 3개년으로 진행
                ymms = []
        if not ymms:
            y = int(today[:4])
            ymms = [f"{y}12", f"{y+1}12", f"{y+2}12"]
        covered = True
        for ep, pk, url in jobs_for(cmp_cd, ymms)[1:]:
            if not covered and ep in ("cF5001", "cF5002"):
                continue        # 무커버 확정 후 잔여 연도 요청은 낭비다
            _, v, body, nb, ms = fetch(cmp_cd, ep, pk, url)
            out.append((ep, pk, v, body, nb, ms))
            if ep == "cF5001" and pk == ymms[0] and v == "ok":
                covered = is_covered(body)
        if covered:
            # v3 대체 축 (2026-09-01 실측 — frq 는 반드시 숫자, 'Y' 를 주면 에러 페이지다):
            #   flag=2 frq=0/1  연간·분기 컨센서스 표 (SALES~EV 11필드, 2028E·추정분기 3개 포함)
            #   flag=4 yymm=…   9항목×5시점 변동 매트릭스 — 투자의견 점수(610100) 포함
            dt = dt_today()
            t2 = {"cmp_cd": cmp_cd, "flag": "2", "finGubun": "MAIN", "sDT": dt, "chartType": "svg"}
            for pk, frq in (("T2Y", "0"), ("T2Q", "1")):
                q = urllib.parse.urlencode({**t2, "frq": frq})
                _, v, body, nb, ms = fetch(cmp_cd, "c1050001_data", pk,
                                           f"{WISE}/company/ajax/c1050001_data.aspx?{q}")
                out.append(("c1050001_data", pk, v, body, nb, ms))
            for ym in ymms:
                q = urllib.parse.urlencode({**t2, "flag": "4", "frq": "0", "yymm": ym})
                _, v, body, nb, ms = fetch(cmp_cd, "c1050001_data", f"T4:{ym}",
                                           f"{WISE}/company/ajax/c1050001_data.aspx?{q}")
                out.append(("c1050001_data", f"T4:{ym}", v, body, nb, ms))
            # 추정기관수(analyst_count)·투자의견 요약은 c1010001 "정적 페이지"에만 있다
            # (실측 2026-09-01: 어떤 AJAX 에도 없음). 페이지 원문을 통째로 보존한다 —
            # 시세현황·신용등급 등 다른 정적 표와 encparam 도 같이 실려 온다.
            _, v, body, nb, ms = fetch(cmp_cd, "c1010001", "",
                                       f"{WISE}/v2/company/c1010001.aspx?cmp_cd={cmp_cd}&cn=")
            out.append(("c1010001", "", v, body, nb, ms))
            # 컨센서스의 나머지 항목 축 — cF3002(손익 8항목 E + 차기분기 E 동봉) · cF4002(지표 E).
            # 연간 호출 하나에 DATAQ1~6(분기)까지 실려 오므로 분기 별도 호출은 없다 (실측).
            for ep, extra in (("cF3002", {"frq": "0", "rpt": "0", "frqTyp": "0"}),
                              ("cF4002", {"frq": "0", "rpt": "5", "frqTyp": "0"})):
                q = urllib.parse.urlencode({"cmp_cd": cmp_cd, "finGubun": "IFRSL", "cn": "",
                                            "encparam": encparam(), **extra})
                _, v, body, nb, ms = fetch(cmp_cd, ep, "Y", f"{WISE}/company/{ep}.aspx?{q}")
                if v == "ok" and body[:1] != b"{":
                    encparam(refresh=True)          # 토큰 만료 의심 — 1회 갱신 후 재시도
                    q = urllib.parse.urlencode({"cmp_cd": cmp_cd, "finGubun": "IFRSL", "cn": "",
                                                "encparam": encparam(), **extra})
                    _, v, body, nb, ms = fetch(cmp_cd, ep, "Y", f"{WISE}/company/{ep}.aspx?{q}")
                out.append((ep, "Y", v if body[:1] == b"{" or v != "ok" else "notjson",
                            body, nb, ms))
        return cmp_cd, covered, out

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for cmp_cd, covered, results in ex.map(collect, tks):
            for ep, pk, v, body, nb, ms in results:
                n_req += 1
                if v == "ok":
                    bad = validate(ep, body)
                    if bad:
                        v = f"bad/{bad}"    # 저장하지 않고 판정만 — 조용한 실패 차단
                        badkinds[bad] = badkinds.get(bad, 0) + 1
                con.execute("INSERT INTO ws_call_log VALUES (?,?,?,?,?,?,?)",
                            (now_utc(), cmp_cd, ep, pk, v, nb, ms))
                if v == "ok" and body:
                    n_ok += 1
                    con.execute("INSERT OR REPLACE INTO ws_raw VALUES (?,?,?,?,?,?,?,?)",
                                (cmp_cd, ep, pk or "", today, zlib.compress(body, 6),
                                 hashlib.sha256(body).hexdigest(), nb, now_utc()))
            con.execute("INSERT OR REPLACE INTO ws_coverage VALUES (?,?,?)",
                        (cmp_cd, "covered" if covered else "none", now_utc()))
            n_cov += covered
            n_none += not covered
            con.commit()
            done_n = n_cov + n_none
            if done_n % 200 == 0:
                el = time.time() - t0
                print(f"  {done_n:>6,}/{len(tks):,}  요청 {n_req:,} (ok {n_ok:,}) "
                      f"· 커버 {n_cov:,}/무 {n_none:,} · {n_req/el:.1f}req/s", flush=True)

    el = time.time() - t0
    n_bad = sum(badkinds.values())
    print(f"\n  ④ 종료 — 종목 {n_cov+n_none:,} (커버 {n_cov:,} · 무커버 {n_none:,}) "
          f"· 요청 {n_req:,} · {el/60:.1f}분 · {n_req/max(el,1):.1f}req/s", flush=True)
    con.execute("INSERT INTO ws_run_log VALUES (?,?,?,?,?,?,?)",
                (now_utc(), a.mode, n_cov + n_none, n_req, n_ok, n_bad,
                 json.dumps(badkinds, ensure_ascii=False)))
    if n_bad:
        print(f"  ⚠⚠ 본문 검증 실패 {n_bad}건 — {badkinds}", flush=True)
        if badkinds.get("struct_changed") or badkinds.get("marker_missing"):
            print("  ⚠⚠ 구조 개편 의심 — 파서 점검 전까지 해당 종목 결측을 신뢰하지 말 것",
                  flush=True)
    con.commit()
    con.close()


if __name__ == "__main__":
    main()
