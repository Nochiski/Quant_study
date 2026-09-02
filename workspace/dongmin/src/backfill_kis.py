"""KIS 원장 수집기. 키움이 원천적으로 못 주는 폐지종목 구간을 메운다.

왜 KIS 인가:
  키움 종목축은 상장폐지 종목에 rc=0 + 0행을 준다 — 에러가 아니라 정상 응답이라
  수집기 로그가 100% 성공으로 보인다(실측 909종목 전건). KIS 종목축은 같은 종목을
  정상 반환한다(한진해운 2017년 폐지 → 100행). 생존편향은 소스의 성질이 아니라
  **축의 성질**이다.

원장 계약:
  응답을 그대로 보존한다. 해석·단위환산·중복제거는 위층 몫이다.
  backfill_dart.store() 를 재사용한다 — 동적 컬럼, row_hash PK, dup_seq 가 이미 검증됐다.

요청 파라미터를 반드시 남기는 이유:
  FID_ORG_ADJ_PRC 는 응답의 가격 의미를 바꾼다(""/"0"=수정주가, "1"=원주가).
  키움 amt_qty_tp 가 이걸 안 남겨서 컬럼 의미를 영구히 잃은 전례가 있다.
  수정주가는 조회 시점 의존이라(나중에 분할이 또 나면 같은 (종목,날짜)에 다른 값)
  원장 불변성과 충돌한다 → chart 계열은 "1"(원주가)로 받는다. KRX 실체결가와 일치한다.
"""
import os, sys, json, time, sqlite3, argparse, requests
from datetime import datetime, timedelta

BASE = os.environ.get("QL_HOME") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "src"))
import api
from backfill_dart import store, SPEC as DART_SPEC          # noqa: F401  (store 재사용)

DB = f"{BASE}/data/raw/kis.db"

PACE       = 0.12         # 실측 초당 5콜. 네트워크 왕복이 병목이라 유량 제한(20/s)에 안 닿는다
RETRY_MAX  = 4
RETRY_BASE = 3.0          # 3 → 6 → 12 → 24초
ABORT_STREAK = 20         # 연속 실패 이 횟수면 중단. 계정 제재를 계속 두들기지 않는다
EMPTY_STREAK = 30         # 전 콜이 빈 응답인 "종목"이 이만큼 연속이면 중단. 한도가 빈 응답으로 올 수 있다

# 유량 초과(EGW00201·429) 정책.
#   KIS 는 키가 하나라 DART 처럼 다음 키로 넘어갈 수 없다. 그래서 두 경우를 갈라야 한다 —
#     · 순간 과속(초당 제한)  → 잠깐 쉬면 풀린다. 백오프가 의미 있다
#     · 일일 한도            → 자정까지 안 열린다. 기다리는 시간이 전부 낭비다
#   구분 기준은 "쉬고 나서 풀리는가"다. 한 유닛에서 QUOTA_RETRY 회까지만 시도하고,
#   그래도 안 되는 유닛이 QUOTA_STREAK 개 연속이면 일일 한도로 판단해 즉시 중단한다.
#   실측(2026-08-26): 하루 33,379콜을 오류 없이 통과했으므로 일일 한도는 그보다 높다.
QUOTA_RETRY  = 2          # 유닛당 유량 재시도 (30초 → 60초). 그 이상은 순간 과속이 아니다
QUOTA_STREAK = 3          # 유량으로 실패한 유닛이 이만큼 연속이면 일일 한도로 보고 중단

# ── 엔드포인트 ────────────────────────────────────────────────
#   axis="span"  : START_DATE/END_DATE 범위 (100행/콜)
#   axis="asof"  : 기준일 하나, 그 이전 N행 (30행/콜) — 날짜를 밀어가며 소급
SPEC = {
 "flow": dict(                                    # 수급 F01·F03·F04
   url="/uapi/domestic-stock/v1/quotations/investor-trade-by-stock-daily",
   tr="FHPTJ04160001", tbl="kis_investor_flow", axis="asof", rows=30, out="output2",
   params=lambda tk, d1, d2: {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": tk,
                              "FID_INPUT_DATE_1": d2,
                              "FID_ORG_ADJ_PRC": "0",     # 무시되지만 명시한다
                              "FID_ETC_CLS_CODE": "0"}),  # 필수 — 빠지면 에러
 "short": dict(                                   # 공매도 F05·F06
   url="/uapi/domestic-stock/v1/quotations/daily-short-sale",
   tr="FHPST04830000", tbl="kis_short_sale", axis="span", rows=100, out="output2",
   params=lambda tk, d1, d2: {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": tk,
                              "FID_INPUT_DATE_1": d1, "FID_INPUT_DATE_2": d2}),
 "loan": dict(                                    # 대차 F07. 하한 2014-01-02(실측)
   url="/uapi/domestic-stock/v1/quotations/daily-loan-trans",
   tr="HHPST074500C0", tbl="kis_loan_trans", axis="span", rows=100, out="output1",
   floor="20140102",
   params=lambda tk, d1, d2: {"MRKT_DIV_CLS_CODE": "3",   # 1:코스피 2:코스닥 3:종목
                              "MKSC_SHRN_ISCD": tk,
                              "START_DATE": d1, "END_DATE": d2, "CTS": ""}),
 "credit": dict(                                  # 신용잔고. 원장에 없던 축
   url="/uapi/domestic-stock/v1/quotations/daily-credit-balance",
   tr="FHPST04760000", tbl="kis_credit_balance", axis="asof", rows=30, out="output",
   params=lambda tk, d1, d2: {"FID_COND_MRKT_DIV_CODE": "J", "FID_COND_SCR_DIV_CODE": "20476",
                              "FID_INPUT_ISCD": tk, "FID_INPUT_DATE_1": d2}),
 "master": dict(                                  # 상장폐지일 등 마스터 67필드
   url="/uapi/domestic-stock/v1/quotations/search-stock-info",
   tr="CTPF1002R", tbl="kis_stock_info", axis="corp", rows=1, out="output",
   params=lambda tk, d1, d2: {"PRDT_TYPE_CD": "300", "PDNO": tk}),
}


# ── 오류 분류 ─────────────────────────────────────────────────
#   KIS 는 rt_cd(0 성공 / 1,2 실패) + msg_cd 로 온다. DART 의 status 체계와 다르다.
def classify(status, body):
    """(verdict, code). verdict: ok · empty · retry · token · quota · fatal · error"""
    if status is None:
        return "retry", "exc"                     # 네트워크·JSON 예외
    if status == 429:
        return "quota", "http429"
    if status >= 500:
        return "retry", f"http{status}"
    mc = (body or {}).get("msg_cd") or ""
    rt = (body or {}).get("rt_cd")
    if mc in ("EGW00121", "EGW00123"):            # 토큰 만료·유효하지 않음
        return "token", mc
    if mc == "EGW00201":                          # 유량 초과
        return "quota", mc
    if mc in ("EGW00133",):                       # 서비스 점검
        return "retry", mc
    if rt == "0":
        return "ok", mc or "0"
    if status != 200:
        return "error", f"http{status}"
    return "error", mc or str(rt)


def normalize(body, key):
    """응답 → (행 리스트, 이상신호). JSON 은 무엇이든 올 수 있다.

    `.get(k) or []` 는 falsy 만 막는다. 아래 둘은 조용히 오염된다 —
      · 값이 배열이 아니라 객체    → len() 이 키 개수를 세고 for 가 키를 순회한다
      · 원소가 dict 이 아님        → store() 의 set(r) 에서 깨진다
    살릴 수 있으면 살리되, 예상 밖이면 반드시 신호를 남긴다.
    """
    if not isinstance(body, dict):
        return [], f"resp_{type(body).__name__}"
    v = body.get(key)
    if v is None:
        return [], None
    if isinstance(v, dict):
        return [v], "out_is_object"
    if not isinstance(v, list):
        return [], f"out_is_{type(v).__name__}"
    rows = [r for r in v if isinstance(r, dict)]
    if len(rows) != len(v):
        return rows, f"non_dict_rows_{len(v) - len(rows)}"
    return rows, None


def call(name, tk, d1, d2, stat):
    """단일 콜. 재시도·백오프·토큰 재발급을 여기서 흡수한다."""
    s = SPEC[name]
    url = f"{api.KIS_BASE}{s['url']}"
    for attempt in range(RETRY_MAX + 1):
        body, code = None, None
        try:
            h = {"content-type": "application/json; charset=utf-8",
                 "authorization": f"Bearer {api._kis_token()}",
                 "appkey": api._K["KIS_APP_KEY"], "appsecret": api._K["KIS_APP_SECRET"],
                 "tr_id": s["tr"], "custtype": "P"}
            r = requests.get(url, headers=h, params=s["params"](tk, d1, d2), timeout=30)
            code = r.status_code
            body = r.json()
        except Exception as e:
            print(f"    ! 콜 실패 — {name} {tk} {d1}~{d2} {type(e).__name__}: {str(e)[:90]}")
        v, mc = classify(code, body)
        stat["calls"] += 1
        time.sleep(PACE)

        if v == "ok":
            out, odd = normalize(body, s["out"])
            if odd:
                print(f"    ? 응답 형태 이상({odd}) — {name} {tk} {d1}~{d2} → rows={len(out)}")
                mc = f"{mc}/{odd}"
            return ("ok" if out else "empty"), mc, out
        if v == "token":
            # 23h 캐싱이라 19시간 실행에서 만료될 수 있다. 캐시를 버리고 재발급한다.
            print(f"    · 토큰 만료({mc}) — 재발급")
            api._kis_tok = None
            try:
                os.remove(api._KIS_CACHE)
            except OSError:
                pass
            time.sleep(1.0)
            continue
        cap = QUOTA_RETRY if v == "quota" else RETRY_MAX
        if v in ("retry", "quota") and attempt < cap:
            wait = RETRY_BASE * (2 ** attempt)
            if v == "quota":
                wait = max(wait, 30.0)            # 유량 초과는 더 길게 쉰다
            print(f"    · {mc} 재시도 {attempt+1}/{cap} — {wait:.0f}초")
            time.sleep(wait)
            continue
        return v, mc, []
    return "error", "retry_exhausted", []


def ensure(con):
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("""CREATE TABLE IF NOT EXISTS kis_call_log(
      name TEXT NOT NULL, ticker TEXT NOT NULL, d1 TEXT, d2 TEXT,
      verdict TEXT NOT NULL, code TEXT, n_rows INTEGER NOT NULL, ts TEXT NOT NULL)""")
    # 유닛 = (엔드포인트, 종목, 조회창). 재개는 이 테이블이 기준이다.
    con.execute("""CREATE TABLE IF NOT EXISTS kis_ingest_log(
      name TEXT NOT NULL, ticker TEXT NOT NULL, d1 TEXT NOT NULL, d2 TEXT NOT NULL,
      status TEXT NOT NULL, n_rows INTEGER NOT NULL, ts TEXT NOT NULL,
      PRIMARY KEY (name, ticker, d1, d2))""")
    con.commit()


def windows(name, first, last):
    """조회창 목록. axis 에 따라 범위창(span) 또는 기준일 역행(asof)."""
    s = SPEC[name]
    floor = s.get("floor")
    if floor and last < floor:
        return []
    if floor and first < floor:
        first = floor
    if s["axis"] == "corp":
        return [("", "")]
    f = datetime.strptime(first, "%Y%m%d")
    l = datetime.strptime(last, "%Y%m%d")
    out = []
    if s["axis"] == "span":
        # 100행/콜 ≈ 140 캘린더일(거래일 비율 0.68 감안, 여유 두고 130)
        cur = l
        while cur >= f:
            beg = max(f, cur - timedelta(days=130))
            out.append((beg.strftime("%Y%m%d"), cur.strftime("%Y%m%d")))
            cur = beg - timedelta(days=1)
    else:
        # 30행/콜 ≈ 42 캘린더일. 기준일을 밀어가며 소급한다.
        cur = l
        while cur >= f:
            out.append((f.strftime("%Y%m%d"), cur.strftime("%Y%m%d")))
            cur = cur - timedelta(days=42)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", required=True, help=f"엔드포인트: {', '.join(SPEC)}")
    ap.add_argument("--scope", default="delisted", choices=("delisted", "all"),
                    help="delisted=폐지종목만 · all=전 종목")
    ap.add_argument("--limit", type=int, default=0, help="종목 수 상한(테스트용)")
    ap.add_argument("--max-calls", type=int, default=0, help="콜 상한. 0=무제한")
    a = ap.parse_args()

    names = [n.strip() for n in a.only.split(",") if n.strip()]
    bad = [n for n in names if n not in SPEC]
    if bad:
        print(f"  ✖ 없는 엔드포인트: {bad}\n     사용 가능: {', '.join(SPEC)}")
        return

    panel = f"{BASE}/data/build/equity_fin.db"
    if not os.path.exists(panel):
        print(f"  ✖ corp_ticker 가 없다 — {panel}. build_bridge.py 를 먼저 실행하라")
        return
    p = sqlite3.connect(f"file:{panel}?mode=ro", uri=True)
    cond = ("last_dd < (SELECT MAX(last_dd) FROM corp_ticker)"
            if a.scope == "delisted" else "1=1")
    # 스팩·우선주 제외: 공매도·대차 대상이 아니고(실측 60종목 중 57이 스팩),
    # 우선주는 본주와 재무를 공유하므로 수급 축에서 별도 의미가 없다.
    rows = p.execute(f"""
        SELECT ticker, first_dd, last_dd FROM corp_ticker
        WHERE is_common = 1 AND {cond}
          AND corp_name NOT LIKE '%기업인수목적%' AND corp_name NOT LIKE '%스팩%'
        ORDER BY ticker""").fetchall()
    p.close()
    if a.limit:
        rows = rows[:a.limit]

    os.makedirs(os.path.dirname(DB), exist_ok=True)
    con = sqlite3.connect(DB, timeout=60)
    ensure(con)
    # empty 를 종결로 보면 안 된다. KIS 에는 DART 의 013 같은 "정상 무자료" 코드가 없어서
    # 한도 초과·제재가 rt_cd=0 + 빈 배열로 나타나면 남은 유닛이 전부 "수집 완료, 데이터 없음"
    # 으로 굳는다. 키움이 폐지종목에 그렇게 답해 909종목이 통째로 비었던 전례가 있다.
    # 빈 응답 1회는 재방문 대상으로 두고, 서로 다른 실행에서 2회 비면 no_data 로 확정한다.
    done = {(r[0], r[1], r[2], r[3]) for r in con.execute(
        "SELECT name, ticker, d1, d2 FROM kis_ingest_log WHERE status = 'ok'")}
    # 빈 응답이 서로 다른 실행에서 2회 나온 유닛은 no_data 로 확정하고 건너뛴다.
    # 한도가 빈 응답으로 왔다면 다음 실행에서 ok 로 바뀌므로 확정에 이르지 않는다.
    no_data = {(r[0], r[1], r[2], r[3]) for r in con.execute(
        "SELECT name, ticker, d1, d2 FROM kis_call_log WHERE verdict = 'empty' "
        "GROUP BY 1, 2, 3, 4 HAVING COUNT(*) >= 2")} - done
    # 1회 empty 유닛 — 재방문에서 또 empty 여도 "예상된 결과"라 한도 증거가 아니다
    seen_empty = {(r[0], r[1], r[2], r[3]) for r in con.execute(
        "SELECT DISTINCT name, ticker, d1, d2 FROM kis_call_log WHERE verdict = 'empty'")}
    n_empty = con.execute(
        "SELECT COUNT(*) FROM kis_ingest_log WHERE status = 'empty'").fetchone()[0]
    if n_empty:
        print(f"  빈 응답 유닛 {n_empty:,} — no_data 확정 {len(no_data):,} · "
              f"재방문 {n_empty - len(no_data):,}")

    plan = []
    for name in names:
        for tk, f, l in rows:
            for d1, d2 in windows(name, f, l):
                if (name, tk, d1, d2) not in done and (name, tk, d1, d2) not in no_data:
                    plan.append((name, tk, d1, d2))
    print(f"  대상 {len(rows):,}종목 × {names} → 남은 유닛 {len(plan):,}"
          f"  (완료 {len(done):,})")
    if not plan:
        print("  전부 완료됨"); con.close(); return

    stat = {"calls": 0, "ok": 0, "empty": 0, "err": 0, "rows": 0}
    streak = estreak = qstreak = 0
    # 빈 응답 감시는 콜이 아니라 종목 단위로 센다. 무데이터 종목은 티커가 인접해
    # 구조적으로 뭉치므로(실측: 동북아10·11·12호선박투자회사 연속 27콜) 콜 단위는 오발화한다.
    # 판정 대상은 "처음 부르는 유닛이 있는 종목"뿐이다 — 재개 시 plan 앞머리는 지난번
    # empty 유닛의 재방문 행렬이라, 이를 세면 재개 직후 반드시 오발화한다(실측 89콜 만에).
    # 한도가 빈 응답으로 온다면 처음 부르는 유닛도 전부 비므로 감지력은 그대로다.
    e_cur, e_empty, e_fresh = None, False, False
    t0 = time.time()
    for i, (name, tk, d1, d2) in enumerate(plan, 1):
        if a.max_calls and stat["calls"] >= a.max_calls:
            print(f"  ⏸ 콜 상한 {a.max_calls} 도달 — 중단"); break
        if (name, tk) != e_cur:                     # 종목 경계 — 직전 종목을 판정한다
            if e_fresh:                             # 재방문뿐인 종목은 중립 (세지도 리셋도 않음)
                estreak = estreak + 1 if e_empty else 0
            if estreak >= EMPTY_STREAK:
                print(f"  ✖ 전 구간 빈 응답 종목 {estreak}개 연속 — 한도·제재가 빈 응답으로 "
                      f"오는 중일 수 있다. 중단한다. kis_call_log 를 확인할 것")
                break
            e_cur, e_empty, e_fresh = (name, tk), True, False
        v, code, out = call(name, tk, d1, d2, stat)
        now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
        con.execute("INSERT INTO kis_call_log VALUES (?,?,?,?,?,?,?,?)",
                    (name, tk, d1, d2, v, code, len(out), now))
        if (name, tk, d1, d2) not in seen_empty:
            e_fresh = True
        if v != "empty":
            e_empty = False
        if v in ("ok", "empty"):
            streak = qstreak = 0
            stat["ok" if v == "ok" else "empty"] += 1
            if out:
                # 요청 파라미터를 req_ 접두어로 남긴다 — 응답 의미를 바꾸는 값이 섞여 있다.
                extra = {"ticker": tk, "d1": d1, "d2": d2, "name": name}
                s = SPEC[name]
                pr = s["params"](tk, d1, d2)
                for k in ("FID_ORG_ADJ_PRC", "FID_ETC_CLS_CODE", "MRKT_DIV_CLS_CODE"):
                    if k in pr:
                        extra[k.lower()] = pr[k]
                store(con, _spec_shim(name), out, extra)
                stat["rows"] += len(out)
            con.execute("INSERT OR REPLACE INTO kis_ingest_log VALUES (?,?,?,?,?,?,?)",
                        (name, tk, d1, d2, v, len(out), now))
        else:
            if v == "quota":
                qstreak += 1
                if qstreak >= QUOTA_STREAK:
                    print(f"  ✖ 유량 초과가 {qstreak}유닛 연속이다({code}). "
                          f"쉬어도 안 풀리므로 일일 한도로 판단해 중단한다.\n"
                          f"     콜 {stat['calls']:,}회 사용 · 재개하면 여기서 이어진다")
                    break
            else:
                qstreak = 0
            streak += 1
            stat["err"] += 1
            if streak >= ABORT_STREAK:
                print(f"  ✖ 연속 실패 {streak}회 ({code}) — 중단한다. "
                      f"계정 제재일 수 있으니 로그를 먼저 확인하라")
                break
        con.commit()
        if i % 500 == 0:
            el = time.time() - t0
            print(f"  {i:,}/{len(plan):,}  콜 {stat['calls']:,} · 행 {stat['rows']:,} · "
                  f"{stat['calls']/el:.1f}콜/s · 남은 {(len(plan)-i)*el/i/3600:.1f}h")

    el = time.time() - t0
    print(f"\n  콜 {stat['calls']:,} ({stat['calls']/max(el,1):.1f}/s) · "
          f"ok {stat['ok']:,} · 빈응답 {stat['empty']:,} · 오류 {stat['err']:,} · "
          f"행 {stat['rows']:,}")
    con.close()


# store() 는 SPEC[name]["tbl"] 을 참조한다. KIS SPEC 을 그 형태로 잠깐 끼워 넣는다.
def _spec_shim(name):
    key = f"__kis_{name}"
    DART_SPEC[key] = {"tbl": SPEC[name]["tbl"]}
    return key


if __name__ == "__main__":
    main()
