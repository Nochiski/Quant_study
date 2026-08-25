"""원장(long) → 스테이지(long). 캐스팅 · 태그 정규화 · 3개 기 펼치기.

원장은 손대지 않는다. 스테이지는 전량 파생이므로 언제든 DROP 후 재생성한다.

왜 별도 DB 인가:
  · 원장 dart.db 안에 두면 "응답 보존 전용"이라는 원장의 유일한 계약이 깨진다.
  · SQLite 영구 VIEW 는 ATTACH 된 다른 DB 를 참조하지 못한다(실측). 원장 위에
    바로 뷰를 얹을 수 없어서 차원 복사본을 둘 자리가 필요하다.
  · 매핑 규칙이 자주 바뀐다. 표준계정코드 미사용 비율이 2015년 337행 → 2017년 4행으로
    요동쳐서 오늘의 필터가 내년에 틀린다.

3개 기 펼치기:
  원장 1행에 당기·전기·전전기가 함께 들어 있다. 펴지 않으면 "FY2019 자산총계가
  두 보고서에 서로 다른 값으로 존재한다"를 SQL 로 다룰 수 없다.
  term_rank 0=당기 1=전기 2=전전기.
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
TERMS = [(0, "thstrm_amount",    "thstrm_nm",    "thstrm_add_amount"),
         (1, "frmtrm_amount",    "frmtrm_nm",    "frmtrm_add_amount"),
         (2, "bfefrmtrm_amount", "bfefrmtrm_nm", None)]


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
