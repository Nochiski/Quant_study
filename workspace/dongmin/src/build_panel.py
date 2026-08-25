"""스테이지(long) → 통합층(wide). 계정 매핑으로 지표를 뽑는다.

행 단위: (corp_code, bsns_year, reprt_code, fs_div, term_rank)
  term_rank 0 만 쓰면 "그 보고서가 말하는 당기"이고,
  1·2 는 같은 회계연도를 다른 보고서가 어떻게 말했는지 비교할 때 쓴다.

매핑에서 값이 모호한 칸(값이 다른 후보 2개 이상)은 NULL 로 두고 fin_reject 에 남긴다.
조용히 하나를 고르면 정렬 순서에 따라 결과가 달라지는 비결정 결함이 된다.
"""
import os, sys, sqlite3, argparse, collections

BASE = os.environ.get("QL_HOME") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "src"))
from fin_map import FIN_MAP, CORE

STAGE_DEFAULT = f"{BASE}/data/build/dart_stage.db"
OUT_DEFAULT   = f"{BASE}/data/build/equity_fin.db"

DDL = """
DROP TABLE IF EXISTS financials;
CREATE TABLE financials (
  corp_code TEXT NOT NULL,
  bsns_year TEXT NOT NULL,
  reprt_code TEXT NOT NULL,
  fs_div    TEXT NOT NULL,
  term_rank INTEGER NOT NULL,
  term_nm   TEXT,
  rcept_no  TEXT,
  {cols},
  revenue_basis TEXT,                 -- standard | banking_gross | insurance_gross | consensus
  PRIMARY KEY (corp_code, bsns_year, reprt_code, fs_div, term_rank)
);
DROP TABLE IF EXISTS fin_reject;
CREATE TABLE fin_reject (
  corp_code TEXT, bsns_year TEXT, reprt_code TEXT, fs_div TEXT, term_rank INTEGER,
  item TEXT, reason TEXT, detail TEXT
);
"""


def resolve(facts, rule):
    """(값, 사유). 값이 없거나 모호하면 (None, 사유)."""
    for sj in rule["sj"]:
        pool = [f for f in facts if f["sj_div"] == sj]
        if not pool: continue
        hit = [f for f in pool if f["concept"] in rule["concept"]]
        via = "concept"
        if not hit and rule["nm"]:
            hit = [f for f in pool if (f["account_nm"] or "").strip() in rule["nm"]]
            via = "nm"
        if not hit: continue
        if rule["agg"] == "sum":
            return sum(f["amount"] for f in hit if f["amount"] is not None), via
        vals = {f["amount"] for f in hit if f["amount"] is not None}
        if len(vals) > 1:
            # 값이 다른 후보가 둘 이상이다. 하나를 고르면 정렬 순서에 결과가 달린다.
            return None, f"ambiguous:{sorted(vals)[:3]}"
        if not vals:
            # 계정은 찾았는데 그 기(期)에 금액이 없다. 분기·반기 보고서의 전기 손익이
            # 여기 해당한다 — 전년 동기는 frmtrm_q_amount 로 따로 오므로
            # frmtrm_amount 가 비어 있다. 결손이 아니라 응답 구조다.
            return None, "empty_term"
        return vals.pop(), via
    return None, "missing"


def build(stage_path: str, out_path: str) -> None:
    stage_path, out_path = os.path.abspath(stage_path), os.path.abspath(out_path)
    if not os.path.exists(stage_path):
        raise FileNotFoundError(f"스테이지가 없다 — stage={stage_path}. build_stage.py 를 먼저 실행하라")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    items = list(FIN_MAP)
    cols = ",\n  ".join(f"{i} INTEGER" for i in items)
    con = sqlite3.connect(f"file:{out_path}", uri=True)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(DDL.format(cols=cols))
    con.execute("ATTACH DATABASE ? AS st", (f"file:{stage_path}?mode=ro",))

    rows = con.execute("""
        SELECT corp_code, bsns_year, reprt_code, fs_div, term_rank, term_nm, rcept_no,
               sj_div, concept, account_nm, amount
        FROM st.fin_fact""").fetchall()
    if not rows:
        con.close()
        raise RuntimeError(
            f"스테이지가 비어 있다 — stage={stage_path} fin_fact=0행. "
            f"원장에 dart_fin_raw 가 있는지 확인하고 build_stage.py 를 먼저 실행하라")
    K = ("corp_code", "bsns_year", "reprt_code", "fs_div", "term_rank")
    grp = collections.defaultdict(list)
    meta = {}
    for r in rows:
        key = r[:5]
        grp[key].append(dict(sj_div=r[7], concept=r[8], account_nm=r[9], amount=r[10]))
        meta.setdefault(key, (r[5], r[6]))

    out, rej = [], []
    for key, facts in grp.items():
        vals, basis = [], "standard"
        for item in items:
            v, why = resolve(facts, FIN_MAP[item])
            vals.append(v)
            if v is None and why != "missing":
                rej.append(key + (item, why.split(":")[0], why))
        out.append(list(key) + list(meta[key]) + vals + [basis])

    ph = ",".join("?" * (7 + len(items) + 1))
    con.executemany(f"INSERT OR REPLACE INTO financials VALUES ({ph})", out)
    con.executemany("INSERT INTO fin_reject VALUES (?,?,?,?,?,?,?,?)", rej)
    con.commit()

    n = con.execute("SELECT COUNT(*) FROM financials").fetchone()[0]
    print(f"  팩트 {len(rows):,}행 → 패널 {n:,}행  (압축 {len(rows)/n:.0f}:1)")
    print(f"  모호·거부 {len(rej)}건")
    con.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", default=STAGE_DEFAULT)
    ap.add_argument("--out", default=OUT_DEFAULT)
    a = ap.parse_args()
    print(f"통합층 생성: {a.out}")
    build(a.stage, a.out)


if __name__ == "__main__":
    main()
