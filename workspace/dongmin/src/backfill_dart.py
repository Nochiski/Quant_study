"""DART 원장 백필.

설계 근거:
  · 일 20,000 이 공식 한도(오류코드 020 설명). 카엘이 ~300 을 쓰므로 우리 상한 19,500.
  · 리셋 시각이 미공지 → 롤링 24h 로 관리하면 어느 기준이든 자동 만족.
  · 013(무자료)과 020(한도초과)을 절대 섞지 않는다. 섞으면 한도 소진이 영구 공백으로 굳는다.
  · 정정공시는 rcept_no 가 다르므로 자연키에 포함해 원본·정정본을 둘 다 남긴다.
  · account_detail 이 없으면 자본변동표가 105행 → 15행으로 뭉개진다(실측).
  · 키는 순차 폴백. 1순위를 다 쓴 뒤에만 2순위로 넘어가므로, 1순위로 끝나는 날은
    2순위(카엘 프로덕션 키)가 아예 나가지 않는다. 병렬로 쏘면 그 격리가 사라진다.
  · 예산 카운터는 키별로 분리한다. 한 카운터로 합산하면 전환 자체가 일어나지 않는다.
  · 재무 하한은 2015 사업연도(실측: 2014 이하는 전 엔드포인트 013 무자료).
  · DS005(주요사항보고서)는 bsns_year/reprt_code 를 안 받는다. corp_code+bgn_de+end_de 가
    전부 필수라 corp_year 축 파라미터로는 호출 자체가 안 된다 → axis="corp_range".
  · corp_range 는 날짜 상한이 없어 종목당 1콜에 전이력이 온다(실측 2026-08-26, k2 키 12콜).
    irdsSttus 를 "누적 이력형"으로 가정했다가 롤링 윈도우로 판명난 전례(DEFECT-A04)가 있어
    가정이 아니라 분할검정으로 확인했다 — wide 1콜의 rcept_no 집합 == 서로 겹치지 않는
    두 하위 구간 콜의 합집합:
      tsstkAqDecsn/00126380  wide 15 == old(~2019) 8 + new(2020~) 7
      cvbdIsDecsn /00919966  wide  8 == old(~2019) 4 + new(2020~) 4
    롤링 윈도우였다면 old 구간 콜이 013 이거나 범위 밖 최신 행을 돌려줬어야 한다.
"""
import os, sys, json, time, sqlite3, argparse, hashlib
from datetime import datetime, timedelta

BASE = os.environ.get("QL_HOME") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "src"))
import api

DB      = f"{BASE}/data/raw/dart.db"
# 키별 롤링24h 상한. kael 은 프로덕션 크론(1일 2회) 몫 ~300 을 남긴다.
CAPS    = {"k2": 19_800, "kael": 19_500}
PACE    = 0.25            # 초당 4콜. DART 는 초당 제한이 미공지라 보수적으로
# 013(무자료) 재확인 정책. 최근 N개 사업연도는 잠정일 수 있다고 보고,
# 마지막 확인으로부터 D일이 지났으면 한 번 더 쏜다.
RECHECK_YEARS = 2
RECHECK_DAYS  = 30

RETRY_MAX  = 3            # 800/900 서버 오류 재시도 횟수
RETRY_BASE = 5.0          # 지수 백오프 기준(초). 5 → 10 → 20

# corp_range 축(DS005)의 고정 조회창. 상수여야 한다 —
# end_de 를 "오늘"로 두면 재수집마다 요청 파라미터가 달라져, 같은 사건이 다른 값으로
# 원장에 남을 여지가 생기고 UTC/KST 경계에서 하루가 흔들린다.
# 20991231(미래 일자)이 status=000 으로 통과하는 것을 실측 확인했다.
RANGE_BGN = "19990101"    # DS005 실측 최소 rcept_no 는 2015 년이지만 하한을 걸 이유가 없다
RANGE_END = "20991231"

# 단계. 1차부터 순차로 돌린다. 재무제표만 먼저 받으면 나머지를 기다리지 않고 쓸 수 있다.
# company 가 1차다. acc_mt(결산월)가 013 의 영구/잠정 판별에 전제이기 때문이다 —
# 3월 결산 법인은 period_end 가 9개월 어긋나므로 12월 결산 폴백으로는 판정할 수 없다.
# 4차(DS005)를 3차에 합치지 않는 이유: 3차는 전부 정기보고서 계열(corp_year/corp)이고
# 4차는 주요사항보고서 계열(corp_range)이라 축도 소급특성도 다르다. 예산도 4차 혼자
# 24,346콜(≈1.2일)이라 한 스테이지로 묶으면 중간 재개점이 사라진다. 서로 의존이 없어
# 순서는 자유롭다 — 3차를 기다릴 필요 없이 단독으로 돌릴 수 있다.
STAGES  = {1: ["company"],
           2: ["fin"],
           3: ["dividend", "shares", "capital", "tesstk", "hyslr", "audit",
               "elestock", "majorstock"],
           4: ["tsstkAqDecsn", "piicDecsn", "cvbdIsDecsn",
               "ctrcvsBgrq", "dfOcr", "dsRsOcr", "bnkMngtPcbg"]}

# 엔드포인트 스펙. key = 자연키 컬럼(응답 필드명), axis = 수집 축
SPEC = {
 "company":      dict(ep="company.json",                   tbl="dart_company",    axis="corp",
                      key=["corp_code"], flat=True),
 "fin":          dict(ep="fnlttSinglAcntAll.json",         tbl="dart_fin_raw",    axis="corp_year",
                      key=["corp_code","bsns_year","reprt_code","fs_div_used","rcept_no",
                           "sj_div","account_id","ord","account_detail"], fallback=True),
 "dividend":     dict(ep="alotMatter.json",                tbl="dart_dividend",   axis="corp_year",
                      key=["corp_code","bsns_year","reprt_code","rcept_no","se","stock_knd"]),
 "shares":       dict(ep="stockTotqySttus.json",           tbl="dart_shares",     axis="corp_year",
                      key=["corp_code","bsns_year","reprt_code","rcept_no","se"]),
 "capital":      dict(ep="irdsSttus.json",                 tbl="dart_capital",    axis="corp_year",
                      key=["corp_code","bsns_year","rcept_no","isu_dcrs_de","isu_dcrs_stock_knd","ord"]),
 "tesstk":       dict(ep="tesstkAcqsDspsSttus.json",       tbl="dart_tesstk",     axis="corp_year",
                      key=["corp_code","bsns_year","reprt_code","rcept_no","acqs_mth1","acqs_mth2","acqs_mth3","stock_knd"]),
 "hyslr":        dict(ep="hyslrSttus.json",                tbl="dart_hyslr",      axis="corp_year",
                      key=["corp_code","bsns_year","reprt_code","rcept_no","nm","relate","stock_knd"]),
 "audit":        dict(ep="accnutAdtorNmNdAdtOpinion.json", tbl="dart_audit",      axis="corp_year",
                      key=["corp_code","bsns_year","reprt_code","rcept_no","bsns_year_"]),
 "elestock":     dict(ep="elestock.json",                  tbl="dart_elestock",   axis="corp"),
 "majorstock":   dict(ep="majorstock.json",                tbl="dart_majorstock", axis="corp"),

 # ── DS005 주요사항보고서 (FACTORS E05·E06·E07) ────────────────────
 # 이름을 엔드포인트 그대로 쓴다. docs/FACTORS.md·DART_CENSUS.md 가 이 철자로 팩터를
 # 지목하고 있어, 별도 축약어를 만들면 --only 인자와 문서가 어긋난다.
 # 엔드포인트별 개별 테이블: 필드가 9~46개로 편차가 크고 의미가 전혀 달라 통합은 손해다.
 # 자연 PK 는 전부 rcept_no 단독(실측 15/15·8/8·3/3·1/1 중복 0). key 는 문서용이다.
 # 정정공시가 같은 사건을 rcept_no 만 다른 별도 행으로 주므로 원장은 전 행을 남기고,
 # 사용 시 (corp_code, bddd[, bd_tm]) 로 묶는다 — 해석은 위층 몫(설계 §5-3).
 "tsstkAqDecsn": dict(ep="tsstkAqDecsn.json",              tbl="dart_tsstk_aq_decsn", axis="corp_range",
                      key=["rcept_no"]),   # E05 자기주식 취득 결정
 "piicDecsn":    dict(ep="piicDecsn.json",                 tbl="dart_piic_decsn",     axis="corp_range",
                      key=["rcept_no"]),   # E06 유상증자 결정
 "cvbdIsDecsn":  dict(ep="cvbdIsDecsn.json",               tbl="dart_cvbd_is_decsn",  axis="corp_range",
                      key=["rcept_no"]),   # E06 전환사채 발행 결정
 "ctrcvsBgrq":   dict(ep="ctrcvsBgrq.json",                tbl="dart_ctrcvs_bgrq",    axis="corp_range",
                      key=["rcept_no"]),   # E07 회생절차 개시신청
 "dfOcr":        dict(ep="dfOcr.json",                     tbl="dart_df_ocr",         axis="corp_range",
                      key=["rcept_no"]),   # E07 부도발생
 "dsRsOcr":      dict(ep="dsRsOcr.json",                   tbl="dart_ds_rs_ocr",      axis="corp_range",
                      key=["rcept_no"]),   # E07 해산사유 발생 (영업정지는 bsnSp — 별건)
 "bnkMngtPcbg":  dict(ep="bnkMngtPcbg.json",               tbl="dart_bnk_mngt_pcbg",  axis="corp_range",
                      key=["rcept_no"]),   # E07 채권은행 관리절차 개시
}

# ── 예산 (롤링 24h) ────────────────────────────────────────────
def budget_used(con, key_id):
    """키별 롤링24h 사용량. 합산하면 전환이 안 되므로 반드시 키로 좁힌다."""
    cut = (datetime.utcnow() - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S")
    return con.execute("SELECT COUNT(*) FROM dart_call_log WHERE ts > ? AND key_id = ?",
                       (cut, key_id)).fetchone()[0]

def pick_key(con, keys, blocked):
    """남은 예산이 있는 첫 키. 순서가 곧 우선순위다. 없으면 (None, None)."""
    for kid, k in keys:
        if kid in blocked: continue
        if budget_used(con, kid) < CAPS.get(kid, 19_500):
            return kid, k
    return None, None

# DART status → verdict. 화이트리스트다. 여기 없는 코드는 '모르는 상태'로 다룬다.
#   ok       정상
#   no_data  그 회사·그 해에 자료가 없다. 재시도해도 안 나온다
#   quota    키 한도 소진. 그 키만 접고 다음 키로
#   abuse    남용 판정·계정 만료. 계속 쏘면 제재로 간다 — 즉시 그 키를 접는다
#   fatal    키 자체가 잘못됐다(미등록·오류·사용불가)
#   retry    서버 측 일시 오류. 백오프 후 재시도
VERDICT = {
    "000": "ok",
    "013": "no_data",
    "020": "quota",
    "021": "abuse",    # 요청 과다 — 021 을 그냥 error 로 두면 계속 두들긴다
    "101": "abuse",    # 부정 사용 판정
    "901": "abuse",    # 계정 만료·정지
    "010": "fatal", "011": "fatal", "012": "fatal",
    "014": "no_data",  # 파일 미존재
    "100": "error",    # 필드 부적절 — 우리 요청이 틀렸다. 재시도해도 같다
    "800": "retry", "900": "retry",
}

def classify(j):
    """DART status → verdict. 013 과 020 을 절대 같게 처리하지 않는다.

    모르는 코드를 error 로 뭉개면 새 코드가 생겼을 때 조용히 지나간다.
    unknown 으로 따로 표시해서 로그에 드러나게 한다."""
    st = (j or {}).get("status")
    return VERDICT.get(st, "unknown"), st

def call(con, name, corp, key_id, key, year=None, reprt="11011", fs=None):
    """(rows, verdict, status). 콜은 여기서만 나가고 전부 로그에 남는다.
    로그에 key_id 를 같이 박아야 키별 예산이 성립한다."""
    s = SPEC[name]
    p = {"corp_code": corp}
    if s["axis"] == "corp_year":
        p["bsns_year"] = year
        p["reprt_code"] = reprt
    elif s["axis"] == "corp_range":
        # DS005 는 세 파라미터가 전부 필수다. 하나라도 빠지면 status=100(필드 부적절)이라
        # 재시도해도 같다. 고정 상수라 종목당 정확히 1콜이다.
        p["bgn_de"] = RANGE_BGN
        p["end_de"] = RANGE_END
    if fs: p["fs_div"] = fs

    def log(status, n):
        con.execute("INSERT INTO dart_call_log VALUES (?,?,?,?,?,?,?,?,?)",
                    (s["ep"], corp, year, reprt, fs, status, n,
                     datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S"), key_id))
        con.commit()

    for attempt in range(RETRY_MAX + 1):
        # 네트워크·HTTP·JSON 예외를 잡지 않으면 502 하나에 프로세스가 죽는다.
        try:
            j = api.dart(s["ep"], key=key, **p)
        except Exception as e:
            # 어느 샤드가 왜 죽었는지 없이 로그를 남기면 재개 때 추적이 안 된다.
            # 키 값 자체는 절대 찍지 않는다 — key_id 로만 식별한다.
            j = None
            print(f"    ! 콜 실패 — endpoint={s['ep']} corp={corp} year={year or '-'} "
                  f"reprt={reprt} fs={fs or '-'} key_id={key_id} "
                  f"{type(e).__name__}: {str(e)[:120]}")
        v, st = ("error", "exc") if j is None else classify(j)
        rows = [] if j is None else ([j] if (s.get("flat") and v == "ok") else (j.get("list") or []))
        log(st, len(rows))          # 실패도 예산에 계상한다 — DART 는 실패 콜도 셀 수 있다
        time.sleep(PACE)

        # v 는 verdict("error"), st 가 "exc" 다. v 로 "exc" 를 검사하면 예외 경로가
        # 첫 시도에서 빠져나가 재시도가 0 회가 된다.
        if (v != "retry" and st != "exc") or attempt == RETRY_MAX:
            if v == "unknown":
                print(f"    ? 모르는 status={st} — endpoint={s['ep']} corp={corp} "
                      f"year={year or '-'} reprt={reprt}. VERDICT 에 추가할 것")
            return rows, v, st
        # 800/900 은 DART 서버 측 일시 오류, exc 는 네트워크·JSON 예외다.
        # 즉시 다시 쏘면 같은 답이 온다.
        wait = RETRY_BASE * (2 ** attempt)
        print(f"    · {st} 재시도 {attempt+1}/{RETRY_MAX} — {wait:.0f}초 대기")
        time.sleep(wait)

def row_hash(cols, vals):
    """행 내용만으로 만드는 안정 해시. collected_at 은 넣지 않는다 — 넣으면 매 수집마다 달라진다.

    None 과 빈 문자열을 구분해서 직렬화한다. 둘을 같게 보면 서로 다른 응답이 한 행으로 접힌다.
    """
    payload = {c: (None if v is None else str(v)) for c, v in zip(cols, vals)}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:32]


def store(con, name, rows, extra):
    """응답을 원문 그대로 적재한다. PK 는 행 해시 하나.

    설계 근거:
      · 컬럼은 rows[0] 이 아니라 전 행의 키 합집합으로 정한다. 응답이 ragged 라서
        앞쪽 행에 없는 필드가 뒤에 나온다 — 분기 재무는 52행째부터 누계 4필드가 붙는다.
        첫 행 기준이면 그 컬럼이 아예 안 생기고 값이 조용히 버려진다(실측 유실 12%).
      · 요청 파라미터는 req_ 접두어로 분리한다. 응답에 같은 이름의 필드가 있으면
        그것이 요청값을 덮는다 — dart_audit.bsns_year 는 '제38기(당기)' 같은 기수 라벨이라
        요청 연도로 조인이 안 된다(실측 39/39행 오염).
      · PK 를 전체 컬럼으로 두면 그중 하나라도 NULL 일 때 SQLite 가 NULL≠NULL 로 봐서
        재수집마다 행이 증식한다(실측 audit +9/회, hyslr +20/회). 행 해시는 NULL 을
        값으로 직렬화하므로 그 경로가 막힌다.
      · 해시에 dup_seq 를 넣는다. DART 는 한 응답 안에 바이트 단위 동일한 행을
        여러 개 준다 — 배당 미지급사의 보통주/우선주 행이 그렇다. 내용만 해시하면
        INSERT OR IGNORE 가 그걸 1행으로 접는다(실측 유실 audit 13.3% ·
        tesstk 10.9% · dividend 5.3% · capital 2.0%). 재무·지분공시는 행마다 구분
        키가 있어 유실이 0 이었지만, 엔드포인트마다 따로 판단하면 새 엔드포인트에서
        같은 실수가 반복된다. 전 엔드포인트에 건다.

        dup_seq 는 배열 인덱스가 아니라 **같은 내용이 그 응답에서 몇 번째로
        나왔는가**다. 배열 인덱스를 쓰면 응답 순서가 흔들릴 때 같은 행이 다른
        해시를 받아 재수집마다 증식한다 — DS005 tsstkAqDecsn 응답은 rcept_no
        오름차순도 내림차순도 아니어서(실측) 새 이벤트가 배열 중간에 끼면
        뒤 행 전부의 인덱스가 밀린다. 내용별 출현 순번은 순서·삽입 양쪽에
        불변이면서 완전중복 행은 여전히 갈라낸다.
    """
    if not rows: return 0
    s = SPEC[name]; tbl = s["tbl"]
    keys = sorted(set().union(*(set(r) for r in rows)) - {"status", "message"})
    req  = {f"req_{k}": v for k, v in extra.items()}
    cols = keys + sorted(req) + ["dup_seq"]

    have = {r[1] for r in con.execute(f"PRAGMA table_info({tbl})")}
    if not have:
        ddl = ", ".join(f'"{c}" TEXT' for c in cols)
        con.execute(f'CREATE TABLE {tbl} (row_hash TEXT PRIMARY KEY, {ddl}, '
                    f'collected_at TEXT NOT NULL)')
        have = set(cols) | {"row_hash", "collected_at"}
    for c in cols:
        if c not in have:
            print(f"    [{tbl}] 새 필드 {c} — 컬럼 추가")
            con.execute(f'ALTER TABLE {tbl} ADD COLUMN "{c}" TEXT'); have.add(c)

    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
    vals, seen = [], {}
    for r in rows:
        body = [r.get(k) for k in keys] + [req[k] for k in sorted(req)]
        base = row_hash(cols[:-1], body)          # 내용만으로 만든 해시
        n = seen.get(base, 0)
        seen[base] = n + 1
        v = body + [str(n)]
        vals.append([row_hash(cols, v)] + v + [now])
    ph = ",".join("?" * (len(cols) + 2))
    quoted = ",".join(chr(34) + c + chr(34) for c in cols)
    before = con.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
    # IGNORE 여야 collected_at 이 "최초 관측 시각"으로 남는다.
    # REPLACE 면 주간 스냅샷을 돌릴 때마다 덮여서 언제 처음 봤는지가 사라진다.
    con.executemany(
        f'INSERT OR IGNORE INTO {tbl} (row_hash, {quoted}, collected_at) VALUES ({ph})', vals)
    con.commit()
    gained = con.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0] - before
    if gained < len(rows):
        # dup_seq 를 키에 넣은 뒤로 응답 내 중복은 유실을 만들지 않는다.
        # 여기가 찍히면 전부 재실행(이미 적재된 행)이다.
        print(f"    [{tbl}] 응답 {len(rows)}행 중 신규 {gained}행 (기적재 {len(rows)-gained})")
    return len(rows)

def fetch(con, name, corp, y, keys, blocked, reprt="11011"):
    """한 샤드를 받는다. 키가 020 이면 그 키를 접고 같은 샤드를 다음 키로 재시도한다.
    quota 를 결과로 흘려보내지 않는 이유: 호출부가 그걸 ingest_log 에 적으면 영구 공백이 된다.
    반환 None = 모든 키 소진(오늘은 여기까지)."""
    s_ = SPEC[name]
    while True:
        kid, k = pick_key(con, keys, blocked)
        if not kid:
            return None
        extra_fs = None
        if s_.get("fallback"):
            rows, v, st = call(con, name, corp, kid, k, y, reprt, fs="CFS")  # fs_div 는 필수 파라미터
            extra_fs = "CFS"
            if v in ("quota", "abuse"):
                print(f"  · {kid} {st} {'한도 소진' if v == 'quota' else '남용 판정 — 즉시 중단'}"
                      f" → 다음 키로"); blocked.add(kid); continue
            if v == "no_data":                                          # 연결재무제표가 없는 회사
                rows, v, st = call(con, name, corp, kid, k, y, reprt, fs="OFS")
                extra_fs = "OFS"
        else:
            rows, v, st = call(con, name, corp, kid, k, y, reprt)
        if v in ("quota", "abuse"):
            print(f"  · {kid} {st} {'한도 소진' if v == 'quota' else '남용 판정 — 즉시 중단'}"
                  f" → 다음 키로"); blocked.add(kid); continue
        return rows, v, st, extra_fs, kid


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corps", required=True, help="corp_code 쉼표구분, 또는 목록파일 경로")
    ap.add_argument("--years", default="", help="미지정 시 2015~올해")
    ap.add_argument("--stage", type=int, default=0,
                    help="1=기업개황 2=재무제표 3=정기보고서 나머지 8종 "
                         "4=DS005 주요사항보고서 7종 (0=전부)")
    ap.add_argument("--only",  default="", help="엔드포인트 이름 쉼표구분(stage 보다 우선)")
    ap.add_argument("--reprt", default="11011",
                    help="보고서 종류 쉼표구분. 11011=사업 11012=반기 11013=1분기 11014=3분기")
    a = ap.parse_args()

    os.makedirs(os.path.dirname(DB), exist_ok=True)
    con = sqlite3.connect(DB, timeout=60)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("""CREATE TABLE IF NOT EXISTS dart_call_log(
      endpoint TEXT NOT NULL, corp_code TEXT, bsns_year TEXT, reprt_code TEXT,
      fs_div TEXT, status TEXT NOT NULL, n_rows INTEGER NOT NULL, ts TEXT NOT NULL,
      key_id TEXT NOT NULL DEFAULT 'kael')""")
    # 기존 DB(key_id 이전) 승계. 그 시절 콜은 전부 카엘 키였다.
    if "key_id" not in {r[1] for r in con.execute("PRAGMA table_info(dart_call_log)")}:
        con.execute("ALTER TABLE dart_call_log ADD COLUMN key_id TEXT NOT NULL DEFAULT 'kael'")
    con.execute("CREATE INDEX IF NOT EXISTS ix_calllog_key_ts ON dart_call_log(key_id, ts)")
    # PK 에 reprt_code 가 없으면 사업/반기/1분기/3분기가 서로를 덮는다.
    # fs_div 가 없으면 CFS 성공 시 OFS 를 영원히 안 받는다.
    # NULL 대신 '' 를 쓰는 이유: SQLite 는 PK 안의 NULL 을 서로 다른 값으로 봐서
    # corp 축(bsns_year 없음)에서 INSERT OR REPLACE 가 REPLACE 되지 않는다.
    con.execute("""CREATE TABLE IF NOT EXISTS ingest_log(
      name TEXT NOT NULL, corp_code TEXT NOT NULL,
      bsns_year TEXT NOT NULL DEFAULT '', reprt_code TEXT NOT NULL DEFAULT '',
      fs_div TEXT NOT NULL DEFAULT '',
      status TEXT NOT NULL, n_rows INTEGER NOT NULL, note TEXT, ts TEXT NOT NULL,
      PRIMARY KEY (name, corp_code, bsns_year, reprt_code, fs_div))""")
    _cols = {r[1] for r in con.execute("PRAGMA table_info(ingest_log)")}
    if "reprt_code" not in _cols:
        print("  ✖ ingest_log 가 구 스키마다 — migrate_ingest_log.py 를 먼저 실행하라")
        con.close(); return
    con.commit()

    # --corps 가 경로처럼 생겼는데 파일이 없으면, 그 문자열이 corp_code 1건으로
    # 둔갑해 정상 종료한다. 경로 의도를 먼저 판정해서 조용한 오작동을 막는다.
    looks_like_path = ("/" in a.corps) or a.corps.endswith((".txt", ".csv", ".lst"))
    if os.path.exists(a.corps):
        # corps.txt 는 "corp_code<TAB>first_year<TAB>last_year". 탭이 없으면
        # 구형 포맷(코드만)이라 게이팅 없이 전 연도를 돈다.
        corps, gate = [], {}
        for line in open(a.corps):
            line = line.strip()
            if not line: continue
            parts = line.split("\t")
            corps.append(parts[0])
            if len(parts) == 3:
                gate[parts[0]] = (parts[1], parts[2])
    elif looks_like_path:
        print(f"  ✖ --corps 파일을 찾을 수 없다: {a.corps}"); con.close(); return
    else:
        corps = [c.strip() for c in a.corps.split(",") if c.strip()]
        gate = {}
    bad = [c for c in corps if not (len(c) == 8 and c.isdigit())]
    if bad:
        print(f"  ✖ corp_code 형식 오류 {len(bad)}건 (8자리 숫자여야 한다): {bad[:5]}")
        con.close(); return

    # 기본 상한은 작년이다. 올해 사업연도 정기보고서는 아직 제출 시점이 오지 않아
    # 전건 013 이 오고, 그게 ingest_log 에 no_data 로 종결 기록되면 내년에 실제
    # 공시가 올라와도 다시 부르지 않는다(DEFECT-B02 경로).
    years = ([y.strip() for y in a.years.split(",") if y.strip()] if a.years
             else [str(y) for y in range(2015, datetime.utcnow().year)])

    if a.only:
        # strip() 없이 매칭하면 "a, b" 의 뒤쪽이 조용히 탈락한다.
        # 전량 미매칭이면 names=[] 로 아무것도 안 하고 성공 종료한다 — 그게 더 나쁘다.
        want = [n.strip() for n in a.only.split(",") if n.strip()]
        names = [n for n in want if n in SPEC]
        miss = [n for n in want if n not in SPEC]
        if miss:
            print(f"  ✖ --only 에 없는 엔드포인트: {miss}")
            print(f"     사용 가능: {', '.join(SPEC)}"); con.close(); return
        if not names:
            print("  ✖ --only 가 비었다"); con.close(); return
    elif a.stage:
        names = STAGES[a.stage]
    else:
        names = list(SPEC)

    reprts = [r.strip() for r in a.reprt.split(",") if r.strip()]
    bad_rc = [r for r in reprts if r not in ("11011", "11012", "11013", "11014")]
    if bad_rc:
        print(f"  ✖ --reprt 값이 잘못됐다: {bad_rc} (11011/11012/11013/11014)")
        con.close(); return

    keys = api.dart_keys()
    if not keys:
        print("  ✖ DART 키가 없다"); con.close(); return
    if keys[0][0] != "k2":
        print(f"  ✖ 1순위 키가 k2 가 아니다({keys[0][0]}) — 카엘 프로덕션 키로 전량이 나간다")
        con.close(); return
    if len(keys) == 1:
        print(f"  ⚠ 키가 1개뿐이다({keys[0][0]}) — 폴백 없이 진행한다")
    print(f"  · 키 {[k for k, _ in keys]} · 대상 {len(corps)}사 × {names}")
    blocked = set()

    # bsns_year 는 corp 축에서 빈 문자열이다. 저장(:y or "")과 조회 키가 일치한다.
    #
    # 013 을 무조건 종결로 보면 안 된다. 013 은 두 가지를 같은 코드로 말한다 —
    # "그 회사에 원래 그 자료가 없다"(영구)와 "아직 공시 시점이 안 왔다"(잠정).
    # 후자를 종결로 적으면 나중에 실제 공시가 올라와도 다시 부르지 않는다.
    # 감사의견 거절로 사업보고서를 늦게 내는 종목이 그렇게 사라진다.
    #
    # 정확한 판별은 acc_mt 로 period_end 를 구해 +161일을 보는 것이지만(설계 §3-1),
    # 그 전에 값싼 근사를 쓴다: 최근 RECHECK_YEARS 사업연도의 no_data 는
    # RECHECK_DAYS 가 지났으면 한 번 더 확인한다. 오래된 연도의 no_data 는 종결.
    # 30분 캐치업 틱이 같은 유닛을 반복 재시도하지 않게 ts 조건이 필요하다.
    cutoff_year = str(datetime.utcnow().year - RECHECK_YEARS)
    cutoff_ts = (datetime.utcnow() - timedelta(days=RECHECK_DAYS)).strftime("%Y-%m-%dT%H:%M:%S")
    # corp·corp_range 축은 bsns_year 가 빈 문자열이다. 그냥 bsns_year < ? 로 쓰면
    # '' < '2024' 가 참이라 그 축의 no_data 가 전부 영구 종결된다 — DS005 의 상폐
    # 이벤트(E07)가 백필 시점에 동결되고, 나중에 부도·회생이 나도 다시 부르지 않는다.
    # 연도 조건은 corp_year 축에만 건다.
    done = {(r[0], r[1], r[2], r[3]) for r in con.execute(
        "SELECT name, corp_code, bsns_year, reprt_code FROM ingest_log "
        "WHERE status = 'ok' "
        "   OR (status = 'no_data' AND "
        "       ((bsns_year <> '' AND bsns_year < ?) OR ts > ?))",
        (cutoff_year, cutoff_ts))}
    pending = con.execute(
        "SELECT COUNT(*) FROM ingest_log "
        "WHERE status = 'no_data' AND ts <= ? "
        "  AND (bsns_year = '' OR bsns_year >= ?)",
        (cutoff_ts, cutoff_year)).fetchone()[0]
    if pending:
        print(f"  013 재확인 대상 {pending:,}유닛 "
              f"(FY{cutoff_year} 이후 · 마지막 확인 {RECHECK_DAYS}일 경과)")
    used0 = {kid: budget_used(con, kid) for kid, _ in keys}
    print("  키 " + " · ".join(f"{kid} {used0[kid]:,}/{CAPS.get(kid,19500):,}" for kid, _ in keys))
    print(f"  대상 {len(corps)}종목 × {len(names)}종 × {len(years)}년 × 보고서 {reprts}")
    print()

    tot = {}
    for corp in corps:
        # 상장구간 밖 연도는 쏘지 않는다. 폐지 종목을 올해까지 도는 것이
        # corp_year 축 예산을 배로 부풀리는 원인이다.
        lo, hi = gate.get(corp, ("0000", "9999"))
        yrs = [y for y in years if lo <= y <= hi]
        for name in names:
            s_ = SPEC[name]
            corp_year = s_["axis"] == "corp_year"
            ys = yrs if corp_year else [""]
            rcs = reprts if corp_year else [""]
            for y in ys:
              for rc in rcs:
                if (name, corp, y, rc) in done: continue
                got = fetch(con, name, corp, y or None, keys, blocked, rc or "11011")
                if got is None:
                    # 한도 소진이면 24h 뒤 재개하면 되고, 남용 판정이면 원인을 봐야 한다.
                    print(f"  ⚠ 쓸 수 있는 키가 없다 (접힌 키: {sorted(blocked) or '없음 — 전부 예산 소진'})")
                    print("     한도 소진이면 24시간 뒤 재개하면 이어진다. "
                          "021/101/901 이었다면 로그를 먼저 확인할 것")
                    con.close(); return
                rows, v, st, extra_fs, kid = got
                if v == "fatal":
                    # 010/011/012 는 키 문제(미등록·오류·사용불가)다. 프로세스를 죽이면
                    # 멀쩡한 다른 키까지 함께 멈춘다. 그 키만 접고 계속한다.
                    print(f"  ✖ {kid} 키 오류 {st} — 이 키를 접는다")
                    blocked.add(kid)
                    if len(blocked) >= len(keys):
                        print("  ✖ 전 키 사용 불가 — 중단"); con.close(); return
                    continue
                extra = {"corp_code": corp}
                if y: extra |= {"bsns_year": y, "reprt_code": rc}
                if extra_fs: extra["fs_div"] = extra_fs
                n = store(con, name, rows, extra) if v == "ok" else 0
                con.execute("INSERT OR REPLACE INTO ingest_log VALUES (?,?,?,?,?,?,?,?,?)",
                            (name, corp, y or "", rc or "", extra_fs or "", v, n, st,
                             datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")))
                con.commit()
                tot[name] = tot.get(name, 0) + n

    print("  콜 " + " · ".join(f"{kid} {budget_used(con,kid)-used0[kid]:,}" for kid, _ in keys) + "  적재:")
    for k, v in sorted(tot.items()):
        print(f"    {k:<12} {v:>7,}행")
    con.close()


if __name__ == "__main__":
    main()
