"""보기용 CSV 재배치. 파이프라인 흐름 순서로 번호를 매기고 한글 파일명을 붙인다.
ka10008 같은 TR 코드만 있으면 무슨 데이터인지 알 수 없다."""
import sqlite3, csv, json, os, shutil

D    = "/Users/claudeoscarmonet/Desktop/Quant_study/workspace/dongmin/data"
RAW  = f"{D}/raw"
BLD  = f"{D}/build"
META = f"{D}/meta"
OUT  = f"{D}/csv"
N    = 30
KO   = json.load(open(f"{META}/colmap.json"))
shutil.rmtree(OUT, ignore_errors=True); os.makedirs(OUT)

# (번호, DB, 테이블, 한글이름, 한줄설명)
PLAN = [
 # ── 1단계: KRX 원장 (날짜축 — 1콜에 전종목) ──
 ("10","krx","krx_stk_bydd_trd",      "KRX원장_유가증권_일별시세",   "날짜 1개 = 전종목. 가격의 정본이고 상장폐지 종목도 포함한다"),
 ("11","krx","krx_ksq_bydd_trd",      "KRX원장_코스닥_일별시세",     "위와 동일. 시장만 다르다"),
 ("12","krx","krx_stk_isu_base_info", "KRX원장_유가증권_종목기본",   "종목의 신분증. 상장주식수가 매일 바뀌므로 날마다 받아야 한다"),
 ("13","krx","krx_ksq_isu_base_info", "KRX원장_코스닥_종목기본",     "위와 동일"),
 ("14","krx","krx_kospi_dd_trd",      "KRX원장_KOSPI지수",           "지수 일별시세"),
 ("15","krx","krx_kosdaq_dd_trd",     "KRX원장_KOSDAQ지수",          "지수 일별시세"),
 ("16","krx","krx_etf_bydd_trd",      "KRX원장_ETF",                 "NAV·순자산총액 포함"),
 ("17","krx","ingest_log",            "KRX_수집로그",                "날짜별 성공/휴장/실패. 빈 응답이 휴장인지 실패인지 여기서 구분한다"),
 # ── 2단계: 키움 원장 (종목축 — 1콜에 1종목 N일) ──
 ("20","kiwoom","ka10014",            "키움원장_공매도",             "ka10014. 하한 2008-06-23 실측(계획서의 2019-06-17 은 오류)"),
 ("21","kiwoom","ka20068",            "키움원장_대차잔고",           "ka20068. 2011-07-25 이전은 전량 0-패딩이라 실데이터가 아니다"),
 ("22","kiwoom","ka10060",            "키움원장_투자자순매수_12주체", "ka10060. 카엘이 49곳에서 참조하는 핵심 데이터"),
 ("23","kiwoom","ka10008",            "키움원장_외국인보유",         "ka10008. next-key 를 합성해 임의 시점으로 점프한다"),
 ("24","kiwoom","ingest_shard",       "키움_수집상태",               "종목·구간별 판정. 캡에 걸린 절단(truncated)을 여기서 잡는다"),
 # ── 3단계: KIS 원장 ──
 ("30","kis","kis_daily_short_sale",  "KIS원장_공매도",              "키움과 교차검증용. 액면분할 종목은 비중이 틀리니 직접 계산해야 한다"),
 ("31","kis","kis_investor_trade_daily","KIS원장_투자자매매",        "키움 12주체보다 세분화(매수·매도 분리)"),
 ("32","kis","raw_call",              "KIS_호출로그",                "요청 URL·파라미터·응답코드 원문"),
 # ── 4단계: DART 원장 ──
 ("40","dart","dart_fin_raw",         "DART원장_재무제표",           "FY2015 부터만 있다(계획서의 FY2013 은 오류). CFS 없으면 OFS"),
 ("41","dart","dart_disclosure",      "DART원장_공시목록",           "날짜축 전시장 스윕"),
 ("42","dart","dart_corp_code",       "DART원장_기업코드",           "종목코드↔DART고유번호. 폐지 종목도 유지된다"),
 ("43","dart","dart_company",         "DART원장_기업개황",           "결산월(acc_mt). 3월 결산이면 사업연도 종료일이 9개월 다르다"),
 ("44","dart","dart_call_log",        "DART_호출로그",               "status=013 은 실패가 아니라 '원래 없음'이다"),
 # ── 5단계: 통합 DB (원장에서 재생성) ──
 ("50","equity_test","trading_calendar","통합_영업일달력",           "KRX 응답 유무로 영업일/휴장 판정"),
 ("51","equity_test","stock_master",  "통합_종목마스터",             "일별 스냅샷. 우선주/보통주 구분 유지"),
 ("52","equity_test","daily_prices",  "통합_일별가격",               "OHLCV + 시총 + 상장주식수"),
 ("53","equity_test","investor_flows","통합_투자자순매수",           "12주체 와이드"),
 ("54","equity_test","short_selling", "통합_공매도",                 "키움+KIS 병합. src 로 출처를 구분한다"),
 ("55","equity_test","financials",    "통합_재무제표",               "PIT 3요소. available_at 은 접수일이지 결산일이 아니다. PK 에 ord 필수(미사용태그 중복)"),
 # ── 6단계: 검증 산출물 ──
 ("60","equity_test","short_selling_conflict","검증_소스간_값차이",  "KRX 를 심판으로 어느 소스가 틀렸는지 판정"),
 ("61","equity_test","coverage_gap",  "검증_커버리지_공백",          "'수집 실패'와 '원래 없음'을 구분해 기록"),
 ("62","equity_test","parse_reject",  "검증_파싱실패",               "숫자 파싱 실패를 조용히 NULL 로 만들지 않는다"),
 ("63","equity_test","xform_note",    "검증_변환메모",               "변환 중 발견한 문제"),
]

idx = []
for no, db, tbl, name, desc in PLAN:
    p = f"{BLD}/equity_test.db" if db=="equity_test" else f"{RAW}/{db}.db"
    if not os.path.exists(p): continue
    c = sqlite3.connect(p)
    try:
        cur = c.execute(f"SELECT * FROM {tbl} LIMIT {N}")
    except sqlite3.OperationalError:
        c.close(); continue
    cols = [x[0] for x in cur.description]; data = cur.fetchall()
    total = c.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
    c.close()
    fn = f"{no}_{name}.csv"
    with open(f"{OUT}/{fn}", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow([f"# {desc}"])                      # 1행: 이 표가 무엇인지
        w.writerow([f"# 원본 {db}.db / {tbl} · 전체 {total:,}행 중 {len(data)}행 표시"])
        # "테이블.컬럼" 을 먼저 찾는다 — 같은 필드명이라도 TR 마다 단위가 다르다
        def lab(c):
            for k in (f"{tbl}.{c}", c):
                if k in KO: return f"{c}({KO[k]})"
            return c
        w.writerow([lab(c) for c in cols])
        w.writerows(data)
    idx.append((no, fn, db, tbl, total, len(data), len(cols), desc))

with open(f"{OUT}/00_목록.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f)
    w.writerow(["번호","파일명","원본DB","원본테이블","전체행수","샘플행수","컬럼수","설명"])
    w.writerows(idx)

print(f"{len(idx)}개 → {OUT}/\n")
for no, fn, db, tbl, total, n, nc, _ in idx:
    print(f"  {fn:<42} {total:>8,}행 {nc:>3}컬럼")
