#!/usr/bin/env python3
"""전수조사 축 ⑦ — 크로스소스 정합성 대조 (targets.CROSS_CHECKS 선언 실행기).

원장 간 조인은 인덱스가 없어 그대로 걸면 터진다. 그래서:
  ① 각 소스에서 (종목, 날짜, 값)만 임시 DB 로 추출 (콤마 제거·abs 는 여기서)
  ② 임시 쪽에만 인덱스 생성
  ③ 정렬 조인으로 일치율·불일치 표본 산출
원장 본체는 mode=ro — 절대 건드리지 않는다.

비교 불능(빈값·대시)은 불일치가 아니라 별도 카운트다 — 결측과 오류를 섞지 않는다.
"""
import json
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from targets import DBS, CROSS_CHECKS  # noqa: E402

BASE = os.environ.get("QL_HOME") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(BASE, "survey_out")
os.makedirs(OUT, exist_ok=True)
TMP = os.path.join(OUT, "tmp_cross.db")


def extract(con, side, src, transform):
    """원장 → 임시 테이블 t_{side}. 값은 숫자 정규화(콤마 제거, 필요 시 abs)."""
    db, tbl, tk, dt, val = src[:5]
    where = src[5] if len(src) > 5 else ""
    con.execute(f"ATTACH DATABASE 'file:{DBS[db]}?mode=ro' AS src")
    vexpr = f'CAST(REPLACE("{val}", \',\', \'\') AS REAL)'
    if transform == "abs":
        vexpr = f"ABS({vexpr})"
    dexpr = f'"{dt}"' if dt else "''"
    con.execute(f"DROP TABLE IF EXISTS t_{side}")
    con.execute(f"""CREATE TABLE t_{side} AS
        SELECT "{tk}" AS tk, {dexpr} AS dt, {vexpr} AS v
        FROM src."{tbl}"
        WHERE "{val}" NOT IN ('', '-') AND "{val}" IS NOT NULL {where}""")
    n_bad = con.execute(f"""SELECT COUNT(*) FROM src."{tbl}"
        WHERE ("{val}" IN ('', '-') OR "{val}" IS NULL) {where}""").fetchone()[0]
    con.execute(f"CREATE INDEX ix_{side} ON t_{side}(tk, dt)")
    n = con.execute(f"SELECT COUNT(*) FROM t_{side}").fetchone()[0]
    con.execute("DETACH DATABASE src")
    return n, n_bad


def run_check(chk):
    t0 = time.time()
    if os.path.exists(TMP):
        os.remove(TMP)
    con = sqlite3.connect(f"file:{TMP}", uri=True)   # ATTACH 의 file:?mode=ro 를 살리려면 URI 필수
    try:
        n_a, bad_a = extract(con, "a", chk["a"], "none")
        n_b, bad_b = extract(con, "b", chk["b"], chk.get("transform_b", "none"))
    except sqlite3.Error as e:
        con.close()
        return {"concept": chk["concept"], "error": repr(e)}
    tol = chk.get("tol", 0)
    common, match = con.execute(f"""
        SELECT COUNT(*), SUM(CASE WHEN ABS(a.v - b.v) <= {tol} THEN 1 ELSE 0 END)
        FROM t_a a JOIN t_b b ON a.tk = b.tk AND a.dt = b.dt""").fetchone()
    mism = con.execute(f"""
        SELECT a.tk, a.dt, a.v, b.v FROM t_a a JOIN t_b b
        ON a.tk = b.tk AND a.dt = b.dt
        WHERE ABS(a.v - b.v) > {tol} LIMIT 6""").fetchall()
    con.close()
    common = common or 0
    match = match or 0
    return {"concept": chk["concept"], "note": chk.get("note", ""),
            "n_a": n_a, "n_b": n_b, "excluded_a": bad_a, "excluded_b": bad_b,
            "common": common, "match": match, "mismatch": common - match,
            "match_pct": round(100.0 * match / common, 4) if common else None,
            "mismatch_samples": [list(m) for m in mism],
            "elapsed_s": round(time.time() - t0, 1)}


def main():
    results = []
    for chk in CROSS_CHECKS:
        missing = [s[0] for s in (chk["a"], chk["b"]) if not os.path.exists(DBS[s[0]])]
        if missing:
            print(f"  - {chk['concept']}: DB 없음({missing}) — 스킵", flush=True)
            continue
        r = run_check(chk)
        results.append(r)
        if "error" in r:
            print(f"  ✖ {r['concept']}: {r['error']}", flush=True)
        else:
            print(f"  ✓ {r['concept']}  공통 {r['common']:,} · 일치 {r['match_pct']}% "
                  f"· {r['elapsed_s']}s", flush=True)

    with open(os.path.join(OUT, "cross.json"), "w") as f:
        json.dump(results, f, ensure_ascii=False, indent=1, default=str)
    lines = [f"# 크로스소스 대조 — {time.strftime('%Y-%m-%d %H:%M')}\n"]
    for r in results:
        if "error" in r:
            lines.append(f"### {r['concept']} — ✖ {r['error']}\n")
            continue
        lines.append(f"### {r['concept']}")
        lines.append(f"- {r['note']}")
        lines.append(f"- A {r['n_a']:,} (비교불능 {r['excluded_a']:,}) · "
                     f"B {r['n_b']:,} (비교불능 {r['excluded_b']:,}) · 공통 {r['common']:,}")
        lines.append(f"- **일치 {r['match_pct']}%** · 불일치 {r['mismatch']:,}")
        for m in r["mismatch_samples"]:
            lines.append(f"  - {m[0]} {m[1]}: A={m[2]} vs B={m[3]}")
        lines.append("")
    with open(os.path.join(OUT, "cross.md"), "w") as f:
        f.write("\n".join(lines))
    if os.path.exists(TMP):
        os.remove(TMP)
    print(f"\n  → {OUT}/cross.md", flush=True)


if __name__ == "__main__":
    main()
