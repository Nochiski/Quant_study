"""스테이지(long) → 통합층(wide). 계정 매핑으로 지표를 뽑는다.

행 단위: (corp_code, bsns_year, reprt_code, fs_div, term_rank)
  스테이지가 당기만 펼치므로 term_rank 는 실제로 0 뿐이다(build_stage.py TERMS 주석).
  컬럼과 PK 에는 남겨 둔다 — 감사 목적으로 전기 축을 되돌릴 때 이 키가 없으면
  같은 회계연도의 두 빈티지가 INSERT OR REPLACE 로 조용히 하나가 된다.

매핑에서 값이 모호한 칸(값이 다른 후보 2개 이상)은 NULL 로 두고 fin_reject 에 남긴다.
조용히 하나를 고르면 정렬 순서에 따라 결과가 달라지는 비결정 결함이 된다.

한 행 안에 기간이 다른 값이 섞인다 (DEFECT-C02):
  손익(revenue·op_profit·net_income…)은 분기에서 당분기 3개월, 현금흐름은 연초누계다.
  통합층은 원본을 보존하고 이름·메타로 기간을 명시한다 — CF 컬럼은 `_ytd`,
  손익 기간은 is_months 컬럼. 3개월 환산은 두 보고서를 결합해야 나오는 파생이라
  재작성 오염을 사실 층에 들이지 않도록 팩터 계산 층에서 한다.
"""
import os, sys, sqlite3, argparse, collections

BASE = os.environ.get("QL_HOME") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "src"))
from fin_map import FIN_MAP, CORE, REVENUE_FALLBACK, CHECK_EQ_LIAB

STAGE_DEFAULT = f"{BASE}/data/build/dart_stage.db"
OUT_DEFAULT   = f"{BASE}/data/build/equity_fin.db"

# 손익(IS·CIS) thstrm_amount 가 담는 기간(월). 분기 보고서는 반기 포함 전부 3개월이다 —
# 삼성전자 FY2025 실측: 1Q 791,405 + 2Q 745,663 = 1,537,068억 = 반기 누계(원 단위 일치).
# 현금흐름은 같은 보고서에서 누계다: 11013 3 · 11012 6 · 11014 9 · 11011 12 개월.
IS_MONTHS = {"11011": 12, "11012": 3, "11013": 3, "11014": 3}

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
  is_months INTEGER,                  -- 손익 컬럼이 담는 기간(월). 분기 3 · 사업 12
  {cols},
  revenue_basis TEXT,                 -- standard | banking_gross | insurance_gross | consensus
  PRIMARY KEY (corp_code, bsns_year, reprt_code, fs_div, term_rank)
);
DROP TABLE IF EXISTS fin_reject;
CREATE TABLE fin_reject (
  corp_code TEXT, bsns_year TEXT, reprt_code TEXT, fs_div TEXT, term_rank INTEGER,
  item TEXT, reason TEXT, detail TEXT
);
DROP TABLE IF EXISTS fin_anomaly;
CREATE TABLE fin_anomaly (
  corp_code TEXT, bsns_year TEXT, reprt_code TEXT, fs_div TEXT, term_rank INTEGER,
  check_id TEXT, detail TEXT
);
"""


def resolve(facts, rule):
    """(값, 사유). 값이 없거나 모호하면 (None, 사유).

    사유 어휘 넷. "왜 없나"가 갈려야 사후 진단이 된다 —
    사유가 empty_term 하나뿐이면 결측이 전부 "정상 응답 구조"로 읽힌다.
      ambiguous  값이 다른 후보 2개 이상. 하나를 고르면 정렬 순서에 결과가 달린다
      empty_term 계정은 찾았는데 그 기(期)에 금액이 없다
      no_account 재무제표는 응답에 있는데 그 계정이 없다 (원장 부재)
      no_stmt    허용 재무제표(sj_div) 자체가 응답에 없다
    """
    seen_stmt = False
    for sj in rule["sj"]:
        pool = [f for f in facts if f["sj_div"] == sj]
        if not pool: continue
        seen_stmt = True
        hit, via = [f for f in pool if f["concept"] in rule["concept"]], "concept"
        if not hit and rule.get("concept_alt"):
            hit, via = [f for f in pool if f["concept"] in rule["concept_alt"]], "concept_alt"
        if not hit and rule["nm"]:
            hit = [f for f in pool if (f["account_nm"] or "").strip() in rule["nm"]]
            via = "nm"
        if not hit: continue
        vals = [f["amount"] for f in hit if f["amount"] is not None]
        if not vals:
            # 계정은 찾았는데 그 기(期)에 금액이 없다. 이 검사가 agg 분기보다 먼저
            # 와야 한다 — sum() 이 빈 목록에서 0 을 돌려주면 결측이 0원으로 위장된다.
            return None, "empty_term"
        if rule["agg"] == "sum":
            return sum(vals), via
        uniq = set(vals)
        if len(uniq) > 1:
            # 값이 다른 후보가 둘 이상이다. 하나를 고르면 정렬 순서에 결과가 달린다.
            return None, f"ambiguous:{sorted(uniq)[:3]}"
        return uniq.pop(), via
    return None, ("no_account" if seen_stmt else "no_stmt")


def revenue_fallback(facts):
    """(값, basis, 사유). 표준 Revenue 태그가 없는 행(은행·보험)에만 태운다.

    규칙과 실측 근거는 fin_map.REVENUE_FALLBACK 주석에 있다.
    합산은 개념당 한 값만 취한다 — 같은 개념이 ord 를 달리해 여러 행으로 오면
    그냥 더할 경우 이중계상이 되고, 값이 갈리면 모호로 돌려보낸다.
    """
    for rule in REVENUE_FALLBACK:
        pool = [f for f in facts if f["sj_div"] in rule["sj"]]
        if not pool: continue
        have = {f["concept"] for f in pool}
        if not set(rule["require"]) <= have: continue
        total, used = 0, []
        for cpt in rule["concept"]:
            vals = {f["amount"] for f in pool
                    if f["concept"] == cpt and f["amount"] is not None}
            if len(vals) > 1:
                return None, "standard", f"ambiguous:{cpt}"
            if vals:
                total += vals.pop()
                used.append(cpt)
        if used:
            return total, rule["basis"], None
    return None, "standard", "no_account"


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

    ix = {i: n for n, i in enumerate(items)}
    out, rej, anom = [], [], []
    eqliab = {}
    for key, facts in grp.items():
        vals, basis = [], "standard"
        for item in items:
            v, why = resolve(facts, FIN_MAP[item])
            if v is None and item == "revenue":
                # 은행·보험. 섹터를 몰라도 Revenue 태그 부재가 이들만 집어낸다
                v, basis, why2 = revenue_fallback(facts)
                if v is None: why = f"{why}+fallback:{why2}"
            vals.append(v)
            if v is None:
                reason = why.split(":")[0].split("+")[0]
                # 결측 사유 기록 범위(T6). CORE 11 항목은 전부 남긴다 — 커버리지
                # 감사의 대상이다. 나머지는 no_stmt 를 뺀다: "재무제표가 통째로 없다"
                # 는 그 행에 대한 사실 하나지 항목마다의 사실이 아니라서, 같은 말을
                # 항목 수만큼 반복하면 로그가 노이즈가 된다. CORE 쪽에 이미 남는다.
                if item in CORE or reason != "no_stmt":
                    rej.append(key + (item, reason, why))
        out.append(list(key) + list(meta[key]) + [IS_MONTHS.get(key[2])] + vals + [basis])
        eqliab[key] = resolve(facts, CHECK_EQ_LIAB)[0]

    # ── DEFECT-S01 탐지 ──────────────────────────────────────
    # DART 원본이 틀린 건이라 원장은 고치지 않는다. 버리지도 않는다 — as-reported 가
    # 원장의 계약이고, 검사②는 정상인 1분기까지 함께 지목하므로 버리면 멀쩡한 행이
    # 사라진다. 표시만 하고 배제 여부는 소비 측이 fin_anomaly 조인으로 정한다.
    #
    # ① 자산총계 vs 자본과부채총계. 회계상 원 단위로 같아야 한다.
    #    10사 40행 실측: 38행 일치, 위반 2행이 정확히 삼성생명 반기·3분기다.
    ta = ix["total_asset"]
    for row in out:
        key = tuple(row[:5])
        a, e = row[8 + ta], eqliab.get(key)
        if a is None or e is None or a == e: continue
        anom.append(key + ("assets_vs_eqliab",
                           f"Assets={a} EquityAndLiabilities={e} diff={(a - e) / e * 100:+.2f}%"))
    # ② 분기 3건의 자산총계가 서로 다른 값이어야 한다.
    #    ①은 1분기를 못 잡는다 — 삼성생명 1분기는 그 자체로 항등식이 맞고
    #    반기·3분기가 그 값에 고정된 것이라, 고정의 시작점은 대조로 안 드러난다.
    QS = ("11013", "11012", "11014")
    byq = collections.defaultdict(dict)
    for row in out:
        if row[4] != 0 or row[2] not in QS: continue
        byq[(row[0], row[1], row[3])][row[2]] = row[8 + ta]
    for (corp, year, fs), m in byq.items():
        v = [m.get(rc) for rc in QS]
        if any(x is None for x in v) or len(set(v)) == len(QS): continue
        for rc in QS:
            anom.append((corp, year, rc, fs, 0, "quarterly_assets_stale",
                         "분기 자산총계 distinct=%d — 1Q/2Q/3Q=%s" % (len(set(v)), v)))

    ph = ",".join("?" * (8 + len(items) + 1))
    con.executemany(f"INSERT OR REPLACE INTO financials VALUES ({ph})", out)
    con.executemany("INSERT INTO fin_reject VALUES (?,?,?,?,?,?,?,?)", rej)
    con.executemany("INSERT INTO fin_anomaly VALUES (?,?,?,?,?,?,?)", anom)
    con.commit()

    n = con.execute("SELECT COUNT(*) FROM financials").fetchone()[0]
    print(f"  팩트 {len(rows):,}행 → 패널 {n:,}행  (압축 {len(rows)/n:.0f}:1)")
    print(f"  결측·모호 {len(rej):,}건")
    bs = con.execute("SELECT revenue_basis, COUNT(*) FROM financials "
                     "GROUP BY 1 ORDER BY 2 DESC").fetchall()
    print("  revenue_basis: " + " · ".join(f"{b} {c:,}" for b, c in bs))
    if anom:
        ck = con.execute("SELECT check_id, COUNT(*) FROM fin_anomaly "
                         "GROUP BY 1 ORDER BY 2 DESC").fetchall()
        print("  이상 탐지(DEFECT-S01): " + " · ".join(f"{k} {c}" for k, c in ck))
    else:
        print("  이상 탐지(DEFECT-S01): 0건")
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
