"""DART 공시목록(list.json) 전기간 스위퍼 — DART_DESIGN §2 P4 (34,473콜 · 1.8일).

설계 근거:
  · list.json 은 SPEC 10종과 달리 페이징이 있다(§6-1). 첫 페이지만 받으면 100건
    초과분이 status=000 인 채로 조용히 사라진다. 그래서 셋을 강제한다 —
      ① page_no 를 1..total_page 전부 순회한다
      ② 창 종료 시 수신 합계·적재 행수를 total_count 와 대조한다.
         불일치면 그 창을 완료(ok)로 적지 않는다
      ③ total_count 를 ingest_log.note 에 남긴다. n_rows(수신)만으로는
         "받은 것 = API 가 가진 전부" 를 사후 검증할 수 없다
  · 검색기간은 corp_code 없이 3개월 제한(실측: 92일 통과). 창을 분기 경계로 자른다.
    2010Q1~2026Q3 전기간이면 표준 분기 창 67개가 그대로 나온다.
  · sort=date&sort_mth=asc 로 고정한다. 정렬을 지정하지 않으면 기본값이 바뀔 때
    페이지 경계가 통째로 달라져 재개가 성립하지 않는다. asc 응답은 같은 창의 desc
    응답과 정확히 역순이다(실측) — 끝난 창에 대해 페이지 분할이 결정적이라는 뜻이고,
    그것이 페이지 단위 재개의 근거다. 단 rcept_dt 단조는 아니다(실측: 2010Q1
    마지막 페이지에 20100331 뒤로 20100204 가 섞여 온다). 그래서 '신규 공시는 꼬리에
    붙는다' 는 가정은 쓰지 않고, 진행 중인 창은 page 1 부터 다시 쓸어담는다.
  · page_no 가 total_page 를 넘으면 DART 는 빈 응답이 아니라 마지막 페이지를 그대로
    돌려준다(실측: 20100105 total_page=5 에 page_no=6 → page 5 의 69행과 rcept_no 69/69
    일치). status 는 000 이라 신호가 없다. 그래서 ① 순회 상한을 매 응답의 total_page 로
    다시 읽고 ② 재개 지점을 추정하지 않고 원장에 적재된 페이지에서만 이어간다.
  · 원장 적재는 backfill_dart.store() 를 그대로 쓴다. 복제하면 원장 규약
    (동적 컬럼 · 행 해시 PK · 응답 내 중복행 구분)이 두 곳으로 갈라진다.
  · 재개 단위는 (창, 페이지)다. 진행 상태를 별도 테이블에 적지 않고 원장에서
    직접 읽는다 — 별도 테이블은 원장과 어긋날 수 있고, 어긋난 쪽이 정답인지
    판정할 방법이 없다.
  · 예산·키 폴백·오류코드는 backfill_dart 의 것을 그대로 쓴다(k2 → kael 순).

운영 테이블 컬럼 매핑 (둘 다 corp 축 전용이라 파라미터 컬럼이 없다):
  dart_call_log : corp_code='' · bsns_year=bgn_de · reprt_code=end_de · fs_div=page_no
  ingest_log 유닛 키 : (name='disclosure', corp_code='', bsns_year=bgn_de,
                        reprt_code=end_de, fs_div='')

ingest_log.status 의미:
  ok        전 페이지 수신 + total_count 대조 통과. 재개 시 건너뛴다
  no_data   013 — 그 기간에 공시가 없다. 종결
  open      대조는 통과했으나 창 끝이 아직 오지 않았다(진행 중인 분기).
            종결이 아니다 — 다음 실행에서 page 1 부터 통째로 다시 받는다
  partial   페이지 순회 중(창 도중 크래시 시 여기서 멈춘다)
  mismatch  Σ수신 ≠ total_count. 완료로 적지 않는다
"""
import os, sys, json, time, sqlite3, argparse, re
from datetime import datetime, timedelta, date

BASE = os.environ.get("QL_HOME") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "src"))
import api
import backfill_dart as bf

DB   = f"{BASE}/data/raw/dart.db"
NAME = "disclosure"          # ingest_log.name
EP   = "list.json"
TBL  = "dart_disclosure"     # 원장
PAGE_COUNT = 100             # DART 상한. 창당 페이지 수를 정하는 값이다

# store() 는 SPEC[name] 의 tbl(+flat) 만 본다. 복제 대신 등록해서 재사용한다.
bf.SPEC.setdefault(NAME, dict(ep=EP, tbl=TBL, axis="window"))
if bf.SPEC[NAME]["tbl"] != TBL:
    raise RuntimeError(f"backfill_dart.SPEC['{NAME}'].tbl 이 {bf.SPEC[NAME]['tbl']!r} 다 "
                       f"— {TBL!r} 이어야 한다. 원장 테이블이 갈라졌다")

Q_END = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}
DONE  = ("ok", "no_data")    # 재개 시 건너뛰는 종결 상태. 'open' 은 종결이 아니다


# ── 창 ────────────────────────────────────────────────────────────
def parse_point(s, upper):
    """'2015Q1' | '20150101' | '2015-01-01' → date. upper=True 면 분기의 마지막 날."""
    t = s.strip().upper().replace("-", "")
    m = re.fullmatch(r"(\d{4})Q([1-4])", t)
    if m:
        y, q = int(m.group(1)), int(m.group(2))
        return date(y, *Q_END[q]) if upper else date(y, 3 * q - 2, 1)
    if re.fullmatch(r"\d{8}", t):
        return datetime.strptime(t, "%Y%m%d").date()
    raise ValueError(f"창 경계 형식이 잘못됐다: {s!r} — YYYYQn 또는 YYYYMMDD")

def q_end(d):
    return date(d.year, *Q_END[(d.month - 1) // 3 + 1])

def windows(bgn, end):
    """[bgn,end] 를 분기 경계로 자른다. 각 창 최장 92일 = 실측 통과 상한.

    좁은 범위를 주면 분기 안에서 더 잘린다(검증용). 그 경우 창 id 가 표준
    분기 창과 달라지므로 원장에 요청 파라미터가 다른 행으로 함께 남는다."""
    out, cur = [], bgn
    while cur <= end:
        we = min(q_end(cur), end)
        out.append((cur.strftime("%Y%m%d"), we.strftime("%Y%m%d")))
        cur = q_end(cur) + timedelta(days=1)
    return out


# ── 원장 조회(재개 근거) ──────────────────────────────────────────
def has_ledger(con):
    return bool(con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                            (TBL,)).fetchone())

def check_ledger(con):
    """store() 규약(row_hash PK · req_ 컬럼)이 아닌 dart_disclosure 가 이미 있으면 멈춘다.

    docs/DART_DESIGN.md §1.4 와 src/dart_collect.py 는 rcept_no PK 스키마를 만든다.
    그 테이블에 store() 를 태우면 'no column named row_hash' 로 죽거나, ALTER 로
    컬럼만 늘어난 반쪽 테이블이 된다."""
    cols = {r[1] for r in con.execute(f"PRAGMA table_info({TBL})")}
    if cols and "row_hash" not in cols:
        raise RuntimeError(
            f"{TBL} 이 store() 규약이 아니다 — 컬럼 {sorted(cols)}. "
            f"row_hash PK 스키마가 아니면 적재할 수 없다. 기존 테이블을 옮기고 다시 실행하라")

def ensure_index(con):
    """재개 조회(창별 MAX(page)·COUNT)가 3,436,971행 풀스캔이 되지 않게 한다."""
    if has_ledger(con):
        con.execute(f'CREATE INDEX IF NOT EXISTS ix_disc_win '
                    f'ON {TBL}("req_bgn_de","req_end_de","req_page_no")')
        con.commit()

def stored_max_page(con, bgn, end):
    """그 창에서 이미 적재된 최대 페이지 번호. 없으면 0."""
    if not has_ledger(con): return 0
    r = con.execute(f'SELECT MAX(CAST("req_page_no" AS INTEGER)) FROM {TBL} '
                    f'WHERE "req_bgn_de"=? AND "req_end_de"=?', (bgn, end)).fetchone()
    return r[0] or 0

def stored_rows(con, bgn, end, below=None, distinct=False):
    """그 창에서 원장에 적재된 행수. below 를 주면 그 페이지 번호 미만만 센다.

    distinct=True 면 rcept_no 기준 고유 건수를 센다. 창을 다시 쓸어담으면 같은 공시가
    다른 page_no 로 적재될 수 있고(요청 파라미터가 행 해시에 들어간다) 그때 COUNT(*) 가
    total_count 를 넘는다. 원장은 그대로 둔 채(원문 보존) 대조만 고유 건수로 한다 —
    rcept_no 는 공시 1건의 유일키다(E2E 900/900 distinct 실측)."""
    if not has_ledger(con): return 0
    col = 'DISTINCT "rcept_no"' if distinct else "*"
    q = f'SELECT COUNT({col}) FROM {TBL} WHERE "req_bgn_de"=? AND "req_end_de"=?'
    p = [bgn, end]
    if below is not None:
        q += ' AND CAST("req_page_no" AS INTEGER) < ?'; p.append(below)
    return con.execute(q, p).fetchone()[0]

def stored_rows_all(con):
    return con.execute(f"SELECT COUNT(*) FROM {TBL}").fetchone()[0] if has_ledger(con) else 0

def mark(con, bgn, end, status, n_rows, total_count, total_page, st):
    """유닛 상태 기록. total_count 를 note 에 JSON 으로 남긴다(§6-1 필수조건 3).

    컬럼을 명시해서 넣는다 — 위치형이면 ingest_log 에 컬럼이 하나 늘 때 조용히 깨진다."""
    note = json.dumps({"total_count": total_count, "total_page": total_page, "st": st},
                      ensure_ascii=False)
    con.execute("INSERT OR REPLACE INTO ingest_log "
                "(name, corp_code, bsns_year, reprt_code, fs_div, status, n_rows, note, ts) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (NAME, "", bgn, end, "", status, n_rows, note,
                 datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")))
    con.commit()


# ── 콜 ────────────────────────────────────────────────────────────
def call_page(con, kid, key, bgn, end, page_no):
    """(json, verdict, status). 콜은 여기서만 나가고 전부 dart_call_log 에 남는다.
    재시도·백오프는 backfill_dart.call() 과 같은 정책이다."""
    def log(status, n):
        con.execute("INSERT INTO dart_call_log (endpoint, corp_code, bsns_year, reprt_code, "
                    "fs_div, status, n_rows, ts, key_id) VALUES (?,?,?,?,?,?,?,?,?)",
                    (EP, "", bgn, end, str(page_no), status, n,
                     datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S"), kid))
        con.commit()

    for attempt in range(bf.RETRY_MAX + 1):
        try:
            j = api.dart(EP, key=key, bgn_de=bgn, end_de=end,
                         page_no=page_no, page_count=PAGE_COUNT,
                         sort="date", sort_mth="asc")
        except Exception as e:
            j = None
            print(f"    ! 콜 실패 — endpoint={EP} window={bgn}-{end} page={page_no} "
                  f"key_id={kid} {type(e).__name__}: {str(e)[:120]}")
        v, st = ("error", "exc") if j is None else bf.classify(j)
        log(st, 0 if j is None else len(j.get("list") or []))   # 실패도 예산에 계상한다
        time.sleep(bf.PACE)
        if (v != "retry" and st != "exc") or attempt == bf.RETRY_MAX:
            if v == "unknown":
                print(f"    ? 모르는 status={st} — endpoint={EP} window={bgn}-{end} "
                      f"page={page_no}. VERDICT 에 추가할 것")
            return j, v, st
        wait = bf.RETRY_BASE * (2 ** attempt)
        print(f"    · {st} 재시도 {attempt+1}/{bf.RETRY_MAX} — {wait:.0f}초 대기")
        time.sleep(wait)


# ── 창 1개 ────────────────────────────────────────────────────────
def sweep_window(con, bgn, end, keys, blocked, today, budget):
    """한 창을 page_no 1..total_page 로 끝까지 받는다.

    반환 (outcome, n_calls). outcome:
      done  창 종결(ok/no_data/open 중 하나로 기록됨)
      bad   대조 실패·오류 — 완료로 적지 않았다. 다음 창으로 넘어간다
      stop  쓸 수 있는 키가 없다 / 콜 상한 도달 — 런을 멈춘다
    """
    ensure_index(con)
    resume = stored_max_page(con, bgn, end)
    # 끝난 창은 정렬이 고정이라 이미 적재된 페이지를 다시 쏘지 않는다(실측: 같은 창의
    # asc 응답이 desc 응답의 정확한 역순 — 결정적 순서).
    # 진행 중인 창은 다르다. 정렬이 rcept_dt 단조가 아니어서(2010Q1 마지막 페이지에
    # 20100331 뒤에 20100204 가 섞여 온다) 신규 공시가 꼬리에만 붙는다고 보장할 수 없다.
    # 그래서 page 1 부터 통째로 다시 쓸어담는다 — 한 분기 ≈ 342콜(전체 예산의 1%).
    open_win = end >= today
    # mismatch 로 남은 창은 이전 런의 페이지 분할을 신뢰할 수 없다(적재 유실이면
    # resume 이 total_page 와 같아져 page tp+1 만 재조회하다 영구히 못 빠져나온다).
    prev = con.execute(
        "SELECT status FROM ingest_log WHERE name=? AND corp_code='' AND bsns_year=? "
        "AND reprt_code=? AND fs_div=''", (NAME, bgn, end)).fetchone()
    page = 1 if (open_win or (prev and prev[0] == "mismatch")) else resume + 1
    n_prior = stored_rows(con, bgn, end, below=page)
    if resume:
        print(f"  ↻ {bgn}-{end} 재개 — 적재된 최대 페이지 {resume} → page {page} 부터"
              f" (기적재 {n_prior:,}행"
              + (", 진행 중인 창이라 전량 재수집" if open_win else
                 ", 이전 런 mismatch 라 전량 재수집" if page == 1 else "") + ")")

    n_api, calls, tc, tp, st = 0, 0, None, None, None
    while True:
        if budget["max_calls"] and budget["used"] >= budget["max_calls"]:
            print(f"  · --max-calls {budget['max_calls']} 도달 — 중단 "
                  f"({bgn}-{end} page {page} 미수신)")
            return "stop", calls
        kid, key = bf.pick_key(con, keys, blocked)
        if not kid:
            return "stop", calls
        j, v, st = call_page(con, kid, key, bgn, end, page)
        calls += 1; budget["used"] += 1

        if v in ("quota", "abuse"):
            print(f"  · {kid} {st} {'한도 소진' if v == 'quota' else '남용 판정 — 즉시 중단'}"
                  f" → 다음 키로"); blocked.add(kid); continue      # 같은 페이지를 다음 키로
        if v == "fatal":
            print(f"  ✖ {kid} 키 오류 {st} — 이 키를 접는다"); blocked.add(kid)
            if len(blocked) >= len(keys):
                print("  ✖ 전 키 사용 불가"); return "stop", calls
            continue
        if v == "no_data":                                   # 013 — 그 기간에 공시가 없다
            tc, tp = 0, 0
            break
        if v != "ok":
            print(f"  ✖ {bgn}-{end} page {page} status={st}({v}) — 창을 완료로 적지 않는다")
            mark(con, bgn, end, "partial", n_prior + n_api, tc, tp, st)
            return "bad", calls

        rows, anom = bf.normalize_rows(j, False)
        if anom:
            print(f"  ! {bgn}-{end} page {page} 응답 형태 이상({anom}) — 정규화 경로로 처리했다")
        tc = int(j.get("total_count") or 0)
        tp = int(j.get("total_page") or 0)
        bf.store(con, NAME, rows, {"bgn_de": bgn, "end_de": end, "page_no": f"{page:04d}"})
        n_api += len(rows)
        # 페이지마다 적는다. 창 도중 크래시해도 total_count 와 진행이 남는다.
        mark(con, bgn, end, "partial", n_prior + n_api, tc, tp, st)
        if not rows:
            # total_page 안인데 빈 페이지 — 아래 대조에서 불일치로 잡힌다
            print(f"  ⚠ {bgn}-{end} page {page}/{tp} 가 빈 응답이다 (total_count={tc:,})")
            break
        if page >= tp:
            break
        page += 1

    ensure_index(con)          # 첫 창은 진입 시점에 원장 테이블이 없다. store() 뒤에 한 번 더
    # ── 필수조건 ②: total_count = Σ수신 = 적재(고유 rcept_no) 대조 ──
    #    앞의 등식은 "API 가 가진 전부를 받았나", 뒤의 등식은 "받은 것을 다 적었나"다.
    #    둘을 따로 봐야 페이징 누락과 적재 유실이 구분된다.
    n_recv = n_prior + n_api
    n_db   = stored_rows(con, bgn, end, distinct=True)
    if tc != n_recv or tc != n_db:
        print(f"  ✖ {bgn}-{end} 대조 실패 — total_count={tc:,} 수신={n_recv:,} 적재={n_db:,}"
              f" (총 페이지 {tp}, 이번 런 {calls}콜). 완료로 적지 않는다")
        mark(con, bgn, end, "mismatch", n_recv, tc, tp, st)
        return "bad", calls

    if tc == 0 and st == "013":
        # 열린 창의 013 은 "아직 공시 전"(분기 첫날·휴일)일 수 있다 — 종결하면 분기 통손실
        status = "open" if open_win else "no_data"
    elif tc == 0:
        # 000 + 0행: 명세상 0행은 013 담당이다. 전 시장 분기가 0건일 수는 없으므로
        # 한도·장애가 빈 응답으로 위장했을 수 있다 — 완료로 적지 않는다 (다음 런 재시도)
        print(f"  ✖ {bgn}-{end} status=000 인데 total_count=0 — 비정상. 완료로 적지 않는다")
        mark(con, bgn, end, "mismatch", n_recv, tc, tp, st)
        return "bad", calls
    else:
        status = "open" if open_win else "ok"
    mark(con, bgn, end, status, n_recv, tc, tp, st)
    print(f"  ✓ {bgn}-{end} {status:<8} total_count={tc:,} 수신={n_recv:,} 적재={n_db:,} "
          f"페이지={tp} 콜={calls}")
    return "done", calls


# ── main ──────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="DART 공시목록(list.json) 스윕")
    ap.add_argument("--from", dest="frm", default="2010Q1", help="시작 창 (YYYYQn 또는 YYYYMMDD)")
    ap.add_argument("--to",   dest="to",  default="",       help="끝 창 (기본: 오늘이 든 분기)")
    ap.add_argument("--max-calls", type=int, default=0, help="이 런의 콜 상한 (0=무제한)")
    a = ap.parse_args()

    today = datetime.utcnow().date()
    bgn = parse_point(a.frm, upper=False)
    end = parse_point(a.to, upper=True) if a.to else q_end(today)
    if end < bgn:
        print(f"  ✖ --to({end}) 가 --from({bgn}) 보다 앞이다"); return

    os.makedirs(os.path.dirname(DB), exist_ok=True)
    con = sqlite3.connect(DB, timeout=60)
    con.execute("PRAGMA journal_mode=WAL")
    # DDL 은 backfill_dart.main() 과 같아야 한다(같은 DB 를 공유한다). IF NOT EXISTS 라
    # 정본 DB 에서는 no-op 이고, 빈 QL_HOME 에서 단독 실행할 때만 만들어진다.
    con.execute("""CREATE TABLE IF NOT EXISTS dart_call_log(
      endpoint TEXT NOT NULL, corp_code TEXT, bsns_year TEXT, reprt_code TEXT,
      fs_div TEXT, status TEXT NOT NULL, n_rows INTEGER NOT NULL, ts TEXT NOT NULL,
      key_id TEXT NOT NULL DEFAULT 'kael')""")
    con.execute("CREATE INDEX IF NOT EXISTS ix_calllog_key_ts ON dart_call_log(key_id, ts)")
    con.execute("""CREATE TABLE IF NOT EXISTS ingest_log(
      name TEXT NOT NULL, corp_code TEXT NOT NULL,
      bsns_year TEXT NOT NULL DEFAULT '', reprt_code TEXT NOT NULL DEFAULT '',
      fs_div TEXT NOT NULL DEFAULT '',
      status TEXT NOT NULL, n_rows INTEGER NOT NULL, note TEXT, ts TEXT NOT NULL,
      PRIMARY KEY (name, corp_code, bsns_year, reprt_code, fs_div))""")
    if "reprt_code" not in {r[1] for r in con.execute("PRAGMA table_info(ingest_log)")}:
        print("  ✖ ingest_log 가 구 스키마다 — migrate_ingest_log.py 를 먼저 실행하라")
        con.close(); return
    con.commit()
    check_ledger(con)

    keys = api.dart_keys()
    if not keys:
        print("  ✖ DART 키가 없다"); con.close(); return
    if keys[0][0] != "k2":
        print(f"  ✖ 1순위 키가 k2 가 아니다({keys[0][0]}) — 카엘 프로덕션 키로 전량이 나간다")
        con.close(); return
    if len(keys) == 1:
        print(f"  ⚠ 키가 1개뿐이다({keys[0][0]}) — 폴백 없이 진행한다")

    plan = windows(bgn, end)
    # 분기 경계에 맞지 않는 범위는 창 id 가 표준 분기 창과 달라진다. 같은 공시가
    # 요청 파라미터만 다른 행으로 원장에 한 번 더 들어간다(검증 중 실제로 밟았다).
    if bgn != date(bgn.year, 3 * ((bgn.month - 1) // 3) + 1, 1) or end != q_end(end):
        print(f"  ⚠ 분기 경계 밖 범위다({bgn}~{end}) — 창 id 가 표준 분기 창과 달라져 "
              "원장에 중복 적재된다. 검증용으로만 쓸 것")
    done = {(r[0], r[1]) for r in con.execute(
        "SELECT bsns_year, reprt_code FROM ingest_log WHERE name = ? AND status IN (?,?)",
        (NAME, *DONE))}
    todo = [w for w in plan if w not in done]
    used0 = {kid: bf.budget_used(con, kid) for kid, _ in keys}
    tstr = today.strftime("%Y%m%d")
    print("  · 키 " + " · ".join(f"{kid} {used0[kid]:,}/{bf.CAPS.get(kid,19500):,}"
                                  for kid, _ in keys))
    print(f"  · 창 {len(plan)}개 ({plan[0][0]}~{plan[-1][1]}) · 완료 {len(plan)-len(todo)} "
          f"· 대상 {len(todo)}" + (f" · 콜 상한 {a.max_calls:,}" if a.max_calls else ""))
    print()

    budget = {"used": 0, "max_calls": a.max_calls}
    blocked, n_ok, n_bad = set(), 0, 0
    for bgn_s, end_s in todo:
        out, _ = sweep_window(con, bgn_s, end_s, keys, blocked, tstr, budget)
        if out == "stop":
            print(f"  ⚠ 중단 — 이어서 실행하면 {bgn_s}-{end_s} 부터 재개한다 "
                  f"(접힌 키: {sorted(blocked) or '없음'})")
            break
        n_ok += out == "done"; n_bad += out == "bad"

    tot = stored_rows_all(con)
    print()
    print("  콜 " + " · ".join(f"{kid} {bf.budget_used(con,kid)-used0[kid]:,}" for kid, _ in keys)
          + f"  창 완료 {n_ok} · 미완 {n_bad} · 원장 {tot:,}행")
    bad = con.execute("SELECT bsns_year, reprt_code, status, n_rows, note FROM ingest_log "
                      "WHERE name = ? AND status NOT IN (?,?,'open') ORDER BY bsns_year",
                      (NAME, *DONE)).fetchall()
    if bad:
        print(f"  ⚠ 미완 유닛 {len(bad)}개 (다음 실행에서 재개):")
        for b in bad[:20]:
            print(f"    {b[0]}-{b[1]} {b[2]} 수신={b[3]:,} {b[4]}")
    con.close()

if __name__ == "__main__":
    main()
