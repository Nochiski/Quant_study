"""원장(long) → 스테이지(long). 캐스팅 · 태그 정규화 · 당기 펼치기.

원장은 손대지 않는다. 스테이지는 전량 파생이므로 언제든 DROP 후 재생성한다.

왜 별도 DB 인가:
  · 원장 dart.db 안에 두면 "응답 보존 전용"이라는 원장의 유일한 계약이 깨진다.
  · SQLite 영구 VIEW 는 ATTACH 된 다른 DB 를 참조하지 못한다(실측). 원장 위에
    바로 뷰를 얹을 수 없어서 차원 복사본을 둘 자리가 필요하다.
  · 매핑 규칙이 자주 바뀐다. 표준계정코드 미사용 비율이 2015년 337행 → 2017년 4행으로
    요동쳐서 오늘의 필터가 내년에 틀린다.

당기만 펼친다:
  원장 1행에 당기·전기·전전기가 함께 들어 있으나, 스테이지로 펴는 것은 당기뿐이다.
  근거는 TERMS 주석에 있다. term_rank 컬럼은 남긴다 — 감사 목적으로 전기 축을
  되돌릴 때 스키마 변경 없이 TERMS 한 줄만 고치면 되게 하려는 것이다.
  term_rank 0=당기 1=전기 2=전전기 (상시 파이프라인은 0 만 만든다).
"""
import os, sys, sqlite3, argparse, re

BASE = os.environ.get("QL_HOME") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW_DEFAULT = f"{BASE}/data/raw/dart.db"
OUT_DEFAULT = f"{BASE}/data/build/dart_stage.db"

# ifrs_ 와 ifrs-full_ 는 FY2018/2019 경계에서 전면 교체된다(중첩 0건 실측).
# suffix 90종 중 81종이 양쪽에 같은 이름으로 존재하므로 접두어를 벗겨 하나로 본다.
TAG_PREFIX = re.compile(r"^(ifrs-full_|ifrs_|dart_)")

# 금액 문자열 → 정수. 원장은 전 컬럼 TEXT 다.
#   결측 마커가 셋이다: '' · '-' · None. 셋 다 NULL 로 보낸다.
#   콤마는 엔드포인트마다 다르게 온다(재무는 없고 지분·자사주는 있다).
def to_int(v):
    if v is None: return None
    s = str(v).strip().replace(",", "")
    if s in ("", "-"): return None
    neg = s.startswith("(") and s.endswith(")")      # 회계 괄호 표기
    if neg: s = s[1:-1]
    if s.startswith("+"): s = s[1:]
    try:
        n = int(s)
    except ValueError:
        try: n = int(float(s))
        except ValueError: return None
    return -n if neg else n


def concept(account_id):
    """네임스페이스를 벗긴 개념명. ifrs_Assets 과 ifrs-full_Assets 를 같게 본다."""
    if not account_id or account_id == "-표준계정코드 미사용-":
        return None
    return TAG_PREFIX.sub("", account_id)


DDL = """
DROP TABLE IF EXISTS fin_fact;
CREATE TABLE fin_fact (
  corp_code   TEXT NOT NULL,          -- 요청 기업 (req_corp_code)
  bsns_year   TEXT NOT NULL,          -- 요청 사업연도
  reprt_code  TEXT NOT NULL,          -- 11011 사업 11012 반기 11013 1분기 11014 3분기
  fs_div      TEXT NOT NULL,          -- CFS 연결 / OFS 개별
  rcept_no    TEXT,                   -- 접수번호. 시점의 근거
  sj_div      TEXT,                   -- BS IS CIS CF SCE
  account_id  TEXT,                   -- 원문 태그
  concept     TEXT,                   -- 네임스페이스 벗긴 개념명
  account_nm  TEXT,
  account_detail TEXT,                -- 자본변동표 열. 없으면 '-'
  ord         INTEGER,
  term_rank   INTEGER NOT NULL,       -- 0 당기 · 1 전기 · 2 전전기
  term_nm     TEXT,                   -- '제 57 기' 같은 원문 라벨
  amount      INTEGER,                -- 원 단위
  amount_cum  INTEGER,                -- 당기 누계(분기만). 연간엔 NULL
  currency    TEXT,
  PRIMARY KEY (corp_code, bsns_year, reprt_code, fs_div, rcept_no,
               sj_div, ord, account_detail, term_rank)
);
CREATE INDEX ix_fact_concept ON fin_fact(concept, corp_code, bsns_year);
CREATE INDEX ix_fact_corp    ON fin_fact(corp_code, bsns_year, reprt_code);
"""

# (term_rank, 금액컬럼, 라벨컬럼, 누계컬럼)
# 당기만 펼친다. 전기·전전기를 빼는 근거는 실측 둘이다.
#
#  · 전기(frmtrm_*)는 그 시점에 알 수 없던 값이다 — look-ahead 원천.
#    삼성생명 제69기말(2024-12-31) 자본총계를 사업보고서는 381,026억,
#    분기보고서 3종은 327,379억이라고 말한다(차 16.4%). 38.1조는 2025년 보험부채
#    산출지침 변경에 따른 소급 재작성치라, 전기 축을 "전년 실적"으로 쓰면
#    그 시점에 알 수 없던 값을 읽는다. 예외도 NULL 도 나지 않는다.
#    이마트도 제14기말에서 993억 차이가 난다.
#  · 전전기(bfefrmtrm_*)는 분기 보고서에 원래 오지 않는다. 주변 계정(사용권자산 등)
#    몇 건이 채워져 있다는 이유만으로 파티션이 생겨, 통합층 28행 중 18행(64%)이
#    전 항목 결측인 유령 행이 됐다. KB금융 11012 는 total_liab 한 칸만 있어
#    자산 없이 부채만 읽으면 부채비율이 발산한다.
#
# 당기만으로 충분한 근거: 백필이 FY2015~2025 연간 + FY2016~2025 분기를 전부 받으므로
# 전년 동기는 그해 보고서의 당기값으로 확보된다. YoY 에 전기 축이 필요 없다.
# 전기 축의 고유 용도인 재작성 탐지는 상시 파이프라인이 아니라 감사 작업이고,
# 원장이 frmtrm_q_amount·bfefrmtrm_amount 를 그대로 보존하므로 필요할 때 이 표에
# 행을 되돌리면 된다 — 정보 손실 0.
#
# 부수 효과로 DEFECT-C03 이 소멸한다. 분기의 전기 손익·CF 는 frmtrm_amount 가 아니라
# frmtrm_q_amount 에 오는데 여기서 전자를 읽어 100% 결측이었다(IS 110행 · CIS 982행이
# amount NULL 로 적재됐다 — 실측). 전기 행 자체가 사라지면서 잘못 읽을 것이 없어진다.
TERMS = [(0, "thstrm_amount", "thstrm_nm", "thstrm_add_amount")]


def build(raw_path: str, out_path: str) -> None:
    raw_path = os.path.abspath(raw_path)
    out_path = os.path.abspath(out_path)
    if not os.path.exists(raw_path):
        raise FileNotFoundError(f"원장이 없다 — raw={raw_path}")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    # ATTACH 에 URI 를 쓰려면 연결 자체가 URI 모드여야 한다.
    # 원장은 mode=ro 로 붙인다 — 스테이지 생성이 원장을 건드릴 수 없게.
    con = sqlite3.connect(f"file:{out_path}", uri=True)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(DDL)
    con.execute("ATTACH DATABASE ? AS raw", (f"file:{raw_path}?mode=ro",))

    cols = {r[1] for r in con.execute("PRAGMA raw.table_info(dart_fin_raw)")}
    if not cols:
        # 0행이 아니라 테이블 자체가 없다. 조용히 return 하면 다음 단계가
        # 빈 스테이지를 정상으로 오해한다.
        con.close()
        raise RuntimeError(
            f"원장에 dart_fin_raw 테이블이 없다 — raw={raw_path}. "
            f"재무 엔드포인트(fnlttSinglAcntAll) 를 아직 수집하지 않은 DB 다")
    need = {"req_corp_code", "req_bsns_year", "req_reprt_code"}
    missing = need - cols
    if missing:
        raise RuntimeError(
            f"원장이 구 스키마다 — 없는 컬럼={sorted(missing)} raw={raw_path}. "
            f"store() 수정 이후 재적재가 필요하다")

    sel = ", ".join(f'"{c}"' for c in sorted(cols))
    rows = list(con.execute(f"SELECT {sel} FROM raw.dart_fin_raw"))
    idx = {c: i for i, c in enumerate(sorted(cols))}
    g = lambda r, c: r[idx[c]] if c in idx else None

    facts, skipped = [], 0
    for r in rows:
        base = (g(r, "req_corp_code"), g(r, "req_bsns_year"), g(r, "req_reprt_code"),
                g(r, "req_fs_div") or "", g(r, "rcept_no"), g(r, "sj_div"),
                g(r, "account_id"), concept(g(r, "account_id")),
                g(r, "account_nm"), g(r, "account_detail") or "-",
                to_int(g(r, "ord")))
        for rank, amt_c, nm_c, cum_c in TERMS:
            amt = to_int(g(r, amt_c))
            cum = to_int(g(r, cum_c)) if cum_c else None
            if amt is None and cum is None:
                skipped += 1                      # 그 기(期)에 값이 없다
                continue
            facts.append(base + (rank, g(r, nm_c), amt, cum, g(r, "currency")))

    con.executemany(
        "INSERT OR IGNORE INTO fin_fact VALUES (" + ",".join("?" * 16) + ")", facts)
    con.commit()

    n = con.execute("SELECT COUNT(*) FROM fin_fact").fetchone()[0]
    print(f"  원장 {len(rows):,}행 → 팩트 {n:,}행  (빈 기 {skipped:,} 제외)")
    dist = con.execute("SELECT term_rank, COUNT(*) FROM fin_fact GROUP BY 1 ORDER BY 1").fetchall()
    print("  기별: " + " · ".join(f"{['당기','전기','전전기'][t]} {c:,}" for t, c in dist))
    nc = con.execute("SELECT COUNT(*) FROM fin_fact WHERE concept IS NULL").fetchone()[0]
    print(f"  표준태그 없음: {nc:,}행 ({nc/n*100:.1f}%) — account_nm 폴백 대상")
    con.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default=RAW_DEFAULT)
    ap.add_argument("--out", default=OUT_DEFAULT)
    a = ap.parse_args()
    print(f"스테이지 생성: {a.out}")
    build(a.raw, a.out)


if __name__ == "__main__":
    main()
