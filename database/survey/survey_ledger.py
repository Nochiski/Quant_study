#!/usr/bin/env python3
"""전수조사 본체 — 축 ①타입 ②단위힌트 ③결측 ④키 ⑤시간축 ⑥의미재료 ⑧격자 ⑨불변식.

방법 선언 (리포트에도 박힌다):
  전수(SQL 1패스)  결측 마커 · NULL · 키 유일성 · 불변식 위반 · 격자 완전성
  표본(stride)     문자열 패턴 분류 — 컬럼의 "형식"은 기업이 아니라 컬럼의 성질이므로
                   행 무작위 표본으로 충분하다. 패턴이 2종 이상 섞인 컬럼만 이상치로
                   올려 Phase C 에서 개별 전수 재검한다.

메모리: 어떤 테이블도 통째로 올리지 않는다 (build_stage OOM 교훈). 표본도 행 스트림.
재개성: 테이블 단위 체크포인트 — survey_out/ledger/{db}.{table}.json 있으면 스킵.
원장은 전부 mode=ro — 문서 수집(쓰기)과 병행 무해.
"""
import json
import os
import re
import sqlite3
import sys
import time
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from targets import DBS, TABLES, INVARIANTS, MISSING_MARKERS, SAMPLE_ROWS  # noqa: E402

BASE = os.environ.get("QL_HOME") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(BASE, "survey_out")
os.makedirs(os.path.join(OUT, "ledger"), exist_ok=True)

# ── 패턴 분류기 — 판정 순서가 곧 우선순위다 ─────────────────────────────
PATTERNS = [
    ("empty",      re.compile(r"^$")),
    ("dash",       re.compile(r"^-$")),
    ("int",        re.compile(r"^-?\d+$")),
    ("comma_int",  re.compile(r"^-?\d{1,3}(,\d{3})+$")),
    ("float",      re.compile(r"^-?\d+\.\d+$")),
    ("comma_float", re.compile(r"^-?\d{1,3}(,\d{3})+\.\d+$")),
    ("signed",     re.compile(r"^[+±]-?[\d,]+\.?\d*$")),          # 키움 부호 표기
    ("date_iso",   re.compile(r"^\d{4}-\d{2}-\d{2}$")),
    ("ts_iso",     re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")),
    ("date_slash", re.compile(r"^\d{4}/\d{2}(/\d{2})?$")),
    ("kr_date",    re.compile(r"^\d{4}년")),                       # DS005 한글 날짜
    ("pct_str",    re.compile(r"^-?[\d,]+\.?\d*\s*%$")),
]
_YMD = re.compile(r"^(19|20)\d{6}$")   # int 이면서 날짜인 것 — int 판정 후 재분류


def classify(s: str) -> str:
    if s is None:
        return "null"
    for name, rx in PATTERNS:
        if rx.match(s):
            if name == "int" and _YMD.match(s):
                return "date_ymd"
            return name
    if re.search(r"[가-힣]", s):
        return "korean_text"
    if re.match(r"^[A-Za-z0-9_./()\- ]+$", s):
        return "ascii_code"
    return "other"


def cols_of(con, tbl):
    return [r[1] for r in con.execute(f'PRAGMA table_info("{tbl}")')]


def survey_table(db, tbl, tk_col, dt_col, key):
    ck = os.path.join(OUT, "ledger", f"{db}.{tbl}.json")
    if os.path.exists(ck):
        return None  # 체크포인트
    path = DBS[db]
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    t0 = time.time()
    cols = cols_of(con, tbl)
    if not cols:
        con.close()
        return {"db": db, "table": tbl, "skip": "테이블 없음"}
    n = con.execute(f'SELECT COUNT(*) FROM "{tbl}"').fetchone()[0]
    rec = {"db": db, "table": tbl, "rows": n, "cols": {}, "method": {
        "missing": "full", "pattern": f"sample≤{SAMPLE_ROWS} (rowid stride)"}}

    # ── 결측 마커 + NULL — 전수 1패스 (컬럼을 40개씩 끊어 집계) ──
    for i in range(0, len(cols), 40):
        chunk = cols[i:i + 40]
        aggs = []
        for c in chunk:
            q = f'"{c}"'
            aggs.append(f"SUM({q} IS NULL)")
            aggs += [f"SUM({q}=?)" for _ in MISSING_MARKERS]
        params = [m for _ in chunk for m in MISSING_MARKERS]
        # SUM(col=?) 형태는 바인딩 순서가 aggs 순서와 같다
        sql = f'SELECT {", ".join(aggs)} FROM "{tbl}"'
        row = con.execute(sql, params).fetchone()
        w = 1 + len(MISSING_MARKERS)
        for j, c in enumerate(chunk):
            base = row[j * w: (j + 1) * w]
            rec["cols"][c] = {"null": base[0],
                              **{f"m_{m or 'empty'}": base[1 + k]
                                 for k, m in enumerate(MISSING_MARKERS)}}

    # ── 표본 패턴 분류 (행 stride — 전 기간·전 종목에서 고르게) ──
    stride = max(1, n // SAMPLE_ROWS)
    try:
        cur = con.execute(f'SELECT * FROM "{tbl}" WHERE (rowid % ?)=0', (stride,)) \
            if stride > 1 else con.execute(f'SELECT * FROM "{tbl}"')
    except sqlite3.OperationalError:
        # WITHOUT ROWID 테이블 — stride 불가. 선두 표본으로 폴백하고 방법을 기록한다
        rec["method"]["pattern"] = f"sample≤{SAMPLE_ROWS} (head — WITHOUT ROWID)"
        cur = con.execute(f'SELECT * FROM "{tbl}" LIMIT ?', (SAMPLE_ROWS,))
    hists = {c: Counter() for c in cols}
    tops = {c: Counter() for c in cols}
    nums = {c: [None, None] for c in cols}   # min,max (숫자 판정분만)
    sampled = 0
    for row in cur:
        sampled += 1
        for c, v in zip(cols, row):
            s = v if isinstance(v, str) else ("" if v is None else str(v))
            p = classify(s if v is not None else None)
            hists[c][p] += 1
            if len(tops[c]) < 5000:
                tops[c][s[:40]] += 1
            if p in ("int", "comma_int", "float", "comma_float"):
                try:
                    x = float(s.replace(",", ""))
                    lo, hi = nums[c]
                    nums[c] = [x if lo is None or x < lo else lo,
                               x if hi is None or x > hi else hi]
                except ValueError:
                    pass
        if sampled >= SAMPLE_ROWS:
            break
    for c in cols:
        h = hists[c]
        subst = {k: v for k, v in h.items() if k not in ("empty", "dash", "null")}
        rec["cols"][c].update({
            "patterns": dict(h.most_common(6)),
            "mixed": len(subst) > 1,
            "distinct_sample": len(tops[c]),
            "top": tops[c].most_common(3) if len(tops[c]) <= 50 else [],
            "num_range": nums[c] if nums[c][0] is not None else None,
        })

    # ── 자연키 유일성 — 전수 ──
    if key:
        kexpr = ", ".join(f'"{k}"' for k in key)
        dup = con.execute(
            f'SELECT COUNT(*) - COUNT(DISTINCT {kexpr} ) FROM "{tbl}"').fetchone()[0] \
            if len(key) == 1 else con.execute(
            f'SELECT COUNT(*) FROM (SELECT 1 FROM "{tbl}" GROUP BY {kexpr} HAVING COUNT(*)>1)'
        ).fetchone()[0]
        rec["key"] = {"cols": key, "violations": dup}

    # ── 불변식 — 전수 ──
    inv_out = []
    for label, cond in INVARIANTS.get(tbl, []):
        cnt = con.execute(f'SELECT COUNT(*) FROM "{tbl}" WHERE {cond}').fetchone()[0]
        smp = con.execute(f'SELECT * FROM "{tbl}" WHERE {cond} LIMIT 3').fetchall() if cnt else []
        inv_out.append({"label": label, "violations": cnt,
                        "sample": [str(r)[:200] for r in smp]})
    if inv_out:
        rec["invariants"] = inv_out

    # ── 격자 완전성 — 전수 (ticker × 그 테이블의 거래일, 종목 재적구간으로 절단) ──
    if tk_col and dt_col:
        row = con.execute(f'''
            WITH d AS (SELECT DISTINCT "{dt_col}" dd FROM "{tbl}" WHERE "{dt_col}"<>''),
            per AS (SELECT "{tk_col}" tk, MIN("{dt_col}") mn, MAX("{dt_col}") mx,
                           COUNT(DISTINCT "{dt_col}") c
                    FROM "{tbl}" WHERE "{dt_col}"<>'' GROUP BY 1)
            SELECT COUNT(*),
                   SUM(exp_c - c),
                   SUM(CASE WHEN exp_c > c THEN 1 ELSE 0 END)
            FROM (SELECT per.*, (SELECT COUNT(*) FROM d WHERE dd BETWEEN mn AND mx) exp_c
                  FROM per)''').fetchone()
        rec["grid"] = {"tickers": row[0], "missing_cells": row[1] or 0,
                       "tickers_with_holes": row[2] or 0,
                       "note": "기대=그 테이블 자체의 거래일 집합 × 종목별 [최초,최종] 구간"}
        if rec["grid"]["missing_cells"]:
            holes = con.execute(f'''
                WITH d AS (SELECT DISTINCT "{dt_col}" dd FROM "{tbl}" WHERE "{dt_col}"<>''),
                per AS (SELECT "{tk_col}" tk, MIN("{dt_col}") mn, MAX("{dt_col}") mx,
                               COUNT(DISTINCT "{dt_col}") c
                        FROM "{tbl}" WHERE "{dt_col}"<>'' GROUP BY 1)
                SELECT * FROM (
                  SELECT tk, (SELECT COUNT(*) FROM d WHERE dd BETWEEN mn AND mx)-c AS gap
                  FROM per) WHERE gap > 0 ORDER BY gap DESC LIMIT 8''').fetchall()
            rec["grid"]["worst"] = [list(h) for h in holes]

    rec["elapsed_s"] = round(time.time() - t0, 1)
    con.close()
    with open(ck, "w") as f:
        json.dump(rec, f, ensure_ascii=False, default=str)
    return rec


def md_summary(rec):
    if not rec or rec.get("skip"):
        return f"### {rec['db']}.{rec['table']} — SKIP ({rec.get('skip')})\n" if rec else ""
    lines = [f"### {rec['db']}.{rec['table']} — {rec['rows']:,}행 · {rec['elapsed_s']}s"]
    mixed = [c for c, v in rec["cols"].items() if v.get("mixed")]
    if mixed:
        lines.append(f"- ⚠ 혼합 패턴 컬럼 {len(mixed)}: {', '.join(mixed[:10])}")
    if "key" in rec:
        k = rec["key"]
        mark = "✓" if k["violations"] == 0 else f"⚠ 위반 {k['violations']:,}"
        lines.append(f"- 키 {k['cols']}: {mark}")
    for iv in rec.get("invariants", []):
        mark = "✓" if iv["violations"] == 0 else f"⚠ {iv['violations']:,}건"
        lines.append(f"- 불변식 [{iv['label']}]: {mark}")
    if "grid" in rec:
        g = rec["grid"]
        lines.append(f"- 격자: 종목 {g['tickers']:,} · 빈 셀 {g['missing_cells']:,} "
                     f"· 구멍 종목 {g['tickers_with_holes']:,}")
    return "\n".join(lines) + "\n"


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else ""
    report, t0 = [], time.time()
    for db, tbl, tk, dt, key in TABLES:
        if only and only not in f"{db}.{tbl}":
            continue
        if not os.path.exists(DBS[db]):
            print(f"  - {db}: DB 없음 — 스킵", flush=True)
            continue
        try:
            rec = survey_table(db, tbl, tk, dt, key)
        except sqlite3.Error as e:
            print(f"  ✖ {db}.{tbl}: {e!r}", flush=True)
            report.append(f"### {db}.{tbl} — ✖ 오류 {e!r}\n")
            continue
        if rec is None:
            print(f"  · {db}.{tbl}: 체크포인트 — 스킵", flush=True)
            continue
        if rec.get("skip"):
            print(f"  - {db}.{tbl}: {rec['skip']} — 스킵", flush=True)
        else:
            print(f"  ✓ {db}.{tbl}  {rec['rows']:,}행  {rec['elapsed_s']}s", flush=True)
        report.append(md_summary(rec))
    with open(os.path.join(OUT, "report_ledger.md"), "w") as f:
        f.write(f"# 원장 전수조사 리포트 — {time.strftime('%Y-%m-%d %H:%M')}\n\n"
                + "\n".join(report))
    print(f"\n  총 {round((time.time()-t0)/60,1)}분 — {OUT}/report_ledger.md", flush=True)


if __name__ == "__main__":
    main()
