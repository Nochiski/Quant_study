"""키움 대량 백필 수집기.

설계 근거 (2026-08-21 실측):
  · 한도는 API ID 별 5콜/s. 계정 합산이 아니다 → TR 별 워커를 병렬로 돌린다.
  · 429 복구 419~759ms, Retry-After 없음 → 고정 500ms 백오프.
  · rt_cd=0 이 완결성을 보증하지 않는다. 캡에 걸려도 성공으로 온다 → 행수로 판정한다.

역방향 커서:
  응답의 가장 오래된 dt 를 다음 요청의 end_dt 로 삼아 과거로 내려간다.
  영업일 캘린더가 필요 없고 캡이 바뀌어도 자동 적응한다.
  종목 간에는 의존이 없으므로 병렬에 지장이 없다.

재개:
  종목·구간 단위로 ingest_shard 에 기록하고, 이미 done 인 종목은 건너뛴다.
  7.2시간짜리 작업이 중간에 끊겨도 이어서 돌릴 수 있어야 한다.
"""
import json, sqlite3, os, sys, time, threading, argparse, re
from datetime import datetime, timedelta
import requests

# 프로젝트 루트 = 이 파일의 상위 디렉토리. QL_HOME 으로 덮어쓸 수 있다.
BASE = os.environ.get("QL_HOME") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "src"))
import api as A

KW  = "https://api.kiwoom.com"
DB  = f"{BASE}/data/raw/kiwoom.db"
RATE = 4.4          # TR 당. 실측 상한 5.0 아래로 잡는다
BACKOFF = 0.5       # 429 복구 실측 419~759ms
MAX_RETRY = 4

# ── TR 정의 ──────────────────────────────────────────────────────
# rows: 응답에서 리스트를 꺼내는 키 / cap: 콜당 최대 행수(실측) / cur: 역방향 커서 파라미터
TRS = {
 "ka10014": dict(
   url="/api/dostk/shsa", tbl="ka10014_short_selling", rows="shrts",
   cap=372, floor="20080623",       # 실측(2026-08-23). 2005·2007=0행, 2008 요청 시 20080623 부터 시작.
   #                              KIS 공매도 커버리지와 동일 경계 → KRX 원천 공시 개시일로 판단
   cols=["dt","close_pric","pred_pre_sig","pred_pre","flu_rt","trde_qty",
         "shrts_qty","ovr_shrts_qty","trde_wght","shrts_trde_prica","shrts_avg_pric"],
   body=lambda tk,s,e: {"stk_cd":tk,"tm_tp":"1","strt_dt":s,"end_dt":e}),
 "ka20068": dict(
   url="/api/dostk/slb", tbl="ka20068_lending_balance", rows=None,
   cap=100, floor="20110725",       # 이분탐색 실측: 20110722=0행 / 20110725=rmnd 비영 전환.
   #                              그 이전은 성공응답+만행수인데 5필드 전량 0(0-패딩)
   cols=["dt","dbrt_trde_cntrcnt","dbrt_trde_rpy","dbrt_trde_irds","rmnd","remn_amt"],
   body=lambda tk,s,e: {"stk_cd":tk,"strt_dt":s,"end_dt":e,"all_tp":"0"}),
 "ka10060": dict(
   url="/api/dostk/chart", tbl="ka10060_investor_flows", rows=None,
   cap=100, floor="20100101",
   cols=None,   # 첫 응답에서 자동 추론
   # dt 는 조회 "종료일" 이다. 구간이 아니라 끝점만 받으므로 역방향 커서와 그대로 맞는다.
   body=lambda tk,s,e: {"dt":e,"stk_cd":tk,"amt_qty_tp":"1","trde_tp":"0","unit_tp":"1000"}),
 "ka10008": dict(
   url="/api/dostk/frgnistt", tbl="ka10008_foreign_holdings", rows="stk_frgnr",
   cap=50, floor="20091101",        # 실측 하한 2009-11
   cols=["dt","close_pric","pred_pre","trde_qty","chg_qty","poss_stkcnt","wght",
         "gain_pos_stkcnt","frgnr_limit","frgnr_limit_irds","limit_exh_rt"],
   # 구간 파라미터가 없다. 커서는 next-key 헤더로 넘긴다 (아래 cursor_key 참조)
   body=lambda tk,s,e: {"stk_cd":tk},
   cursor_key=lambda tk,e: f"A{tk}{e}"),
}

TOK = A._kw_token()
STAT, SL = {}, threading.Lock()
ABORT = threading.Event()      # 전역 중단. 한 TR 이 완전히 막히면 나머지도 위험하다


class Circuit:
    """연속 차단 시 감속하고, 회복 불능이면 그 TR 을 멈춘다.

    이게 없으면 한도에 막힌 상태에서 종목마다 MAX_RETRY 만큼 콜을 태우며
    2,602 종목을 끝까지 훑는다 — 1만 콜을 버리고 전부 partial 로 남긴 뒤 '완료' 라고 찍는다.
    """
    def __init__(self, name):
        self.name, self.consec, self.blocked, self.r429 = name, 0, 0, 0
        self.codes = {}          # 유량 코드별 발생 횟수 (1700/1701/1702/429)

    def note(self, code):
        if code:
            self.codes[code] = self.codes.get(code, 0) + 1
            if code in ("1701", "1702") and self.codes[code] == 1:
                print(f"[{self.name}] ★ 계정/그룹 한도 관측: {code}")

    def ok(self):
        self.consec = 0

    def fail(self):
        """반환: 계속할 수 있으면 True, 포기해야 하면 False"""
        self.consec += 1
        self.blocked += 1
        if self.consec >= 12:
            print(f"[{self.name}] 연속 {self.consec}회 차단 — 회복 불능으로 판단, 중단")
            return False
        if self.consec >= 3:
            wait = min(600, 30 * (2 ** (self.consec - 3)))
            print(f"[{self.name}] 연속 {self.consec}회 차단 → {wait}초 대기")
            for _ in range(wait):
                if ABORT.is_set(): return False
                time.sleep(1)
        return True
DBL = threading.Lock()          # sqlite 는 스레드 간 쓰기를 직렬화한다

def prev_day(d):
    return (datetime.strptime(d, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")

# 키움 오류코드표(공식) 기준 분류. return_msg 안에 [NNNN:...] 상세코드가 실려 온다.
RATE_CODES  = {"1700", "1701", "1702"}          # 유량 — 백오프 후 재시도
NODATA_CODES= {"1901", "1902", "1903"}          # 종목정보 없음 — 폐지종목 등, 정상
TOKEN_CODES = {"8005", "8001"}                  # 토큰 문제 — 재발급 후 1회 재시도
FATAL_CODES = {"1501", "1504", "1505", "1511", "1512",
               "1513", "1514", "1515", "1516", "1517"}   # 요청 형식 오류 — 재시도 무의미

def classify(status, j):
    """(verdict, code) 반환. verdict: ok / rate / nodata / fatal / error"""
    # 키움은 HTTP 429 와 함께 본문에 [1700:...] 상세코드를 준다.
    # status 만 보고 끝내면 1700(초당)과 1701(계정)/1702(그룹)를 구분할 수 없다.
    rc = j.get("return_code") if isinstance(j, dict) else None
    msg = str(j.get("return_msg", "")) if isinstance(j, dict) else ""
    m = re.search(r"\[(\d{4})[:\]]", msg)
    code = m.group(1) if m else None
    if status == 429: return "rate", (code or "429")
    if rc == 0: return "ok", code
    if code in RATE_CODES or rc == 5:   return "rate",   code
    if code in NODATA_CODES:            return "nodata", code
    if code in TOKEN_CODES:             return "token",  code
    if code in FATAL_CODES:             return "fatal",  code
    # rc 가 0 이 아닌데 분류 못 하면 error. 절대 성공으로 넘기지 않는다 —
    # 이걸 성공 처리하면 빈 응답이 empty 로 원장에 확정 기록되고 영구 skip 된다.
    return ("error", code) if rc is not None else ("error", None)


def call(api_id, body, next_key=None):
    """(json, verdict, n429) 반환. verdict 는 classify() 참조."""
    global TOK
    t = TRS[api_id]
    h = {"Content-Type":"application/json;charset=UTF-8",
         "authorization": f"Bearer {TOK}", "api-id": api_id}
    if next_key:
        h["cont-yn"] = "Y"; h["next-key"] = next_key
    n429, tok_retried, last = 0, False, "error"
    rate_codes = []
    for _ in range(MAX_RETRY):
        try:
            r = requests.post(KW + t["url"], json=body, headers=h, timeout=30)
            try:    j = r.json()
            except Exception: j = {}
            v, code = classify(r.status_code, j)
            if v == "rate":
                n429 += 1; rate_codes.append(code); time.sleep(BACKOFF); last = "rate"; continue
            if v == "token" and not tok_retried:
                TOK = A._kw_token(force=True)         # 만료·무효 → 재발급 후 1회만 재시도
                h["authorization"] = f"Bearer {TOK}"
                tok_retried = True; last = "token"; continue
            if v == "token":
                print(f"[{api_id}] 토큰 재발급 후에도 실패 {code}")
                return j, "fatal", n429, rate_codes
            if v == "fatal":
                print(f"[{api_id}] 치명적 오류 {code}: {str(j.get('return_msg',''))[:70]}")
                return j, "fatal", n429, rate_codes
            return j, v, n429, rate_codes
        except Exception:
            time.sleep(0.3); last = "error"
    return None, last, n429, rate_codes


def extract(j, key):
    if j is None: return []
    if key and isinstance(j.get(key), list): return j[key]
    return next((v for v in j.values() if isinstance(v, list)), [])

def ensure_table(con, api_id, sample):
    """응답에 새 필드가 나오면 컬럼을 추가한다.

    첫 응답으로 컬럼셋을 고정하면 위험하다 — 역방향 커서라 첫 응답이 최신(2026)이고,
    과거에만 존재하는 필드가 있으면 조용히 버려진다. ka10060 은 cols=None 이라 특히 그렇다.
    """
    t = TRS[api_id]
    cols = list(t["cols"]) if t["cols"] else []
    new_cols = [c for c in sample.keys() if c not in cols]
    if new_cols and cols:
        print(f"[{api_id}] 새 필드 {new_cols} — 컬럼 추가")
    cols += new_cols
    t["cols"] = cols
    tbl = t["tbl"]
    ddl = ",\n  ".join(f'"{c}" TEXT' for c in cols)
    con.execute(f"""CREATE TABLE IF NOT EXISTS {tbl} (
  ticker TEXT NOT NULL,
  {ddl},
  src_api TEXT NOT NULL,
  collected_at TEXT NOT NULL,
  PRIMARY KEY (ticker, dt)
)""")
    have = {r[1] for r in con.execute(f"PRAGMA table_info({tbl})")}
    for c in cols:
        if c not in have:
            con.execute(f'ALTER TABLE {tbl} ADD COLUMN "{c}" TEXT')
    con.commit()

def worker(api_id, tickers, target_from, stamp):
    """tickers 는 (종목, 시작커서) 쌍. 커서는 중단된 지점부터 이어받기 위한 것이다."""
    t = TRS[api_id]
    floor = max(target_from, t["floor"])
    con = sqlite3.connect(DB, timeout=60)
    gap = 1.0 / RATE
    cb = Circuit(api_id)
    done = calls = r_tot = 0
    t0 = time.time()

    def save(tk, rows, ended, reached, cursor):
        st = ("done"   if ended in ("floor", "exhausted") else
              "empty"  if ended == "empty"  else
              "nodata" if ended == "nodata" else
              "error"  if ended in ("fatal", "error") else "partial")
        # 재시도 대상(partial·error)은 이어받을 커서를 남긴다.
        # error 에 커서를 안 남기면 이미 받은 청크를 버리고 처음부터 다시 받는다(실측: ka10008/037560).
        nxt = cursor if st in ("partial", "error") else None
        with DBL:
            if rows:
                ph = ",".join("?" * (len(t["cols"]) + 3))
                con.executemany(f"INSERT OR REPLACE INTO {t['tbl']} VALUES ({ph})", rows)
            con.execute("INSERT OR REPLACE INTO ingest_shard VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (api_id, tk, floor, "20260820", len(rows), t["cap"], st,
                 reached, "20260820", stamp, nxt))
            con.commit()
        return st

    for tk, start_cursor in tickers:
        if ABORT.is_set(): break
        cursor = start_cursor or "20260820"
        resumed = bool(start_cursor)          # 이어받기면 첫 콜부터 next-key 를 써야 한다
        rows, chunks, reached, ended = [], 0, None, None
        while True:
            if ABORT.is_set(): ended = "blocked"; break
            s = time.time()
            ck = t.get("cursor_key")
            nk = ck(tk, cursor) if (ck and (chunks or resumed)) else None
            j, verdict, n429, rcodes = call(api_id, t["body"](tk, floor, cursor), nk)
            calls += 1
            cb.r429 += n429
            for c in rcodes: cb.note(c)
            if verdict == "fatal":            # 앱키·인증 오류 → 전 종목이 실패한다. 즉시 중단
                ended = "fatal"; ABORT.set(); break
            if verdict in ("rate", "error"):
                ended = verdict
                if not cb.fail(): ABORT.set()
                break
            if verdict == "nodata":           # 폐지종목 등 — 실패가 아니다
                ended = "nodata"; break
            cb.ok()
            lst = extract(j, t["rows"])
            if not lst: ended = "exhausted" if chunks else "empty"; break
            if t["cols"] is None or not chunks or any(k not in t["cols"] for k in lst[0]):
                with DBL: ensure_table(con, api_id, lst[0])
            rows += [[tk] + [x.get(c) for c in t["cols"]] + [api_id, stamp] for x in lst]
            chunks += 1
            dts = sorted(x.get("dt", "") for x in lst if x.get("dt"))
            if not dts: ended = "exhausted"; break
            reached = dts[0]
            if reached <= floor:    ended = "floor";     break
            if len(lst) < t["cap"]: ended = "exhausted"; break   # 캡 미만 = 그 종목 데이터 소진
            cursor = prev_day(reached)
            time.sleep(max(0, gap - (time.time() - s)))

        r_tot += len(rows)
        save(tk, rows, ended, reached, cursor)
        done += 1
        with SL:
            STAT[api_id] = dict(done=done, total=len(tickers), calls=calls, rows=r_tot,
                                r429=cb.r429, blocked=cb.blocked, codes=dict(cb.codes),
                                rate=round(calls / (time.time() - t0), 2), el=int(time.time() - t0))
    con.close()

def todo(api_id, tickers, target_from):
    """(종목, 시작커서) 목록. done/empty/nodata 는 제외, partial 은 중단 지점부터 이어받는다."""
    # req_start 에는 clamp 된 floor 가 저장된다(worker 와 동일). 여기서 clamp 하지 않으면
    # TR floor 가 target_from 보다 늦은 경우 비교가 항상 False 가 되어 재개가 영원히 안 된다.
    floor = max(target_from, TRS[api_id]["floor"])
    con = sqlite3.connect(DB)
    try:
        seen = {r[0]: (r[1], r[2]) for r in con.execute(
            "SELECT ticker, status, next_cursor FROM ingest_shard WHERE src_api=? AND req_start<=?",
            (api_id, floor))}
    except sqlite3.OperationalError:
        seen = {}
    con.close()
    out = []
    for tk in tickers:
        st, cur = seen.get(tk, (None, None))
        if st in ("done", "empty", "nodata"):
            continue
        out.append((tk, cur))
    return out


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--tr", default="ka10014,ka20068,ka10060")
    p.add_argument("--from", dest="frm", default="20100101")
    p.add_argument("--limit", type=int, default=0, help="종목 수 제한 (스모크 테스트용)")
    a = p.parse_args()

    tickers = [l.strip() for l in open(f"{BASE}/data/jsonl/tickers.txt") if l.strip()]
    if a.limit: tickers = tickers[:a.limit]
    stamp = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    apis = [x for x in a.tr.split(",") if x in TRS]

    con = sqlite3.connect(DB)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("""CREATE TABLE IF NOT EXISTS ingest_shard (
      src_api TEXT NOT NULL, ticker TEXT NOT NULL, req_start TEXT NOT NULL, req_end TEXT NOT NULL,
      n_rows INTEGER NOT NULL, cap INTEGER NOT NULL, status TEXT NOT NULL,
      first_dt TEXT, last_dt TEXT, collected_at TEXT NOT NULL,
      next_cursor TEXT,
      PRIMARY KEY (src_api, ticker, req_start, req_end))""")
    try:
        con.execute("ALTER TABLE ingest_shard ADD COLUMN next_cursor TEXT")   # 기존 DB 마이그레이션
    except sqlite3.OperationalError:
        pass
    con.commit(); con.close()

    plan = {x: todo(x, tickers, a.frm) for x in apis}
    for x in apis:
        print(f"  {x}: 대상 {len(plan[x]):,}종목 (전체 {len(tickers):,} 중)")
    print()

    ts = [threading.Thread(target=worker, args=(x, plan[x], a.frm, stamp)) for x in apis if plan[x]]
    t0 = time.time()
    for t in ts: t.start()
    while any(t.is_alive() for t in ts):
        time.sleep(10)
        with SL: snap = dict(STAT)
        line = " | ".join(
            f"{k} {v['done']}/{v['total']} {v['calls']}콜 {v['rate']}/s 429={v.get('r429',0)} 차단={v.get('blocked',0)}"
            for k, v in sorted(snap.items()))
        open(f"{BASE}/logs/progress.txt", "w").write(
            f"{time.strftime('%H:%M:%S')} +{int(time.time()-t0)}s  {line}\n" + json.dumps(snap, indent=1))
    for t in ts: t.join()

    print(f"완료 {time.time()-t0:.0f}초")
    for k, v in sorted(STAT.items()):
        print(f"  {k}: {v['done']}종목 {v['calls']:,}콜 {v['rows']:,}행 {v['rate']}/s "
              f"429={v.get('r429',0)} 차단={v.get('blocked',0)}")
    if ABORT.is_set():
        print("\n  ⚠ 서킷 차단으로 중단됨. 같은 명령으로 재실행하면 중단 지점부터 이어받는다.")
