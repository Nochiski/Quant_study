"""문서 프리패스 — 스냅샷 doc_store(zip_ok=1) 의 ZIP 을 한 번만 파싱해 테이블×연도 JSON Lines 캐시.

산출: <cache_root>/<snapshot_id>/<table>/year=YYYY.jsonl + summary.json (DOC_DESIGN §2.2·§2.5 ①).
게이트 D0(ZIP 부재·열기 실패 0)·D2(문서 단위 파싱 실패율)·D3(어휘 폐쇄)·D10(텍스트 등식) 을
여기서 판정한다. 문서 단위 = ZIP 1개, 문서의 parse_mode = main 멤버(없으면 첫 멤버)의 것.
CLI: PYTHONPATH=src python -m stage.doc_prepass --snapshot-id S [--workers 3] [--years 2020,2021]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import parsers_doc
from .doc_vocab import DocVocab, load_vocab

TABLES: tuple[str, ...] = ("stg_doc_meta", "stg_doc_section", "stg_doc_correction",
                           "stg_doc_parse_log")
_ROWS_ATTR = {"stg_doc_meta": "meta", "stg_doc_section": "section",
              "stg_doc_correction": "correction", "stg_doc_parse_log": "parse_log"}
_ROLE_PRIORITY = {"main": 0, "audit_cons": 1, "audit": 2, "other": 3}


@dataclass
class ShardResult:
    year: str
    n_docs: int = 0
    n_zip_missing: int = 0
    n_zip_open_failed: int = 0
    modes: Counter[str] = field(default_factory=Counter)          # 문서 단위
    tables: Counter[str] = field(default_factory=Counter)
    unknown_tags: Counter[str] = field(default_factory=Counter)   # 문서 빈도
    other_entities: Counter[str] = field(default_factory=Counter)
    text_equal_violations: int = 0
    missing: list[str] = field(default_factory=list)


@dataclass
class PrepassSummary:
    snapshot_id: str
    input_hash: str                      # zip_ok=1 접수번호 집합의 sha256 (D5 — _meta.json 에 기록)
    n_docs: int
    modes: dict[str, int]
    tables: dict[str, int]
    d0_zip_missing: int
    d0_zip_open_failed: int
    d2_failed_ratio: float
    d3_unknown_tags_over_limit: list[str]
    d10_text_equal_violations: int
    status: str                          # ok | gate_failed
    detail: str


def _docs_by_year(db: Path, years: set[str] | None,
                  rcept_list: set[str] | None) -> dict[str, list[tuple[str, str]]]:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT rcept_no, fetched_at FROM doc_store WHERE zip_ok = 1 "
                           "ORDER BY rcept_no").fetchall()
    finally:
        con.close()
    out: dict[str, list[tuple[str, str]]] = {}
    for rno, at in rows:
        y = str(rno)[:4]
        if years is not None and y not in years:
            continue
        if rcept_list is not None and str(rno) not in rcept_list:
            continue
        out.setdefault(y, []).append((str(rno), str(at)))
    return out


def _doc_mode(rows: parsers_doc.DocRows) -> str:
    logs = sorted(rows.parse_log, key=lambda r: _ROLE_PRIORITY.get(str(r["member_role"]), 3))
    return str(logs[0]["parse_mode"]) if logs else "failed"


def _run_shard(args: tuple[str, list[tuple[str, str]], Path, Path]) -> ShardResult:
    year, docs, docs_dir, cache = args
    vocab: DocVocab = load_vocab()
    res = ShardResult(year)
    files = {t: open(cache / t / f"year={year}.jsonl", "w", encoding="utf-8") for t in TABLES}
    try:
        for rno, fetched_at in docs:
            path = docs_dir / year / f"{rno}.zip"
            if not path.exists():
                res.n_zip_missing += 1
                res.missing.append(rno)
                continue
            rows = parsers_doc.parse_zip(rno, path.read_bytes(), fetched_at, vocab)
            for t in TABLES:
                rs: list[dict[str, str | None]] = getattr(rows, _ROWS_ATTR[t])
                res.tables[t] += len(rs)
                for r in rs:
                    files[t].write(json.dumps(r, ensure_ascii=False))
                    files[t].write("\n")
            if rows.zip_error is not None:
                res.n_zip_open_failed += 1       # D0 — D2 분모에 넣지 않는다
                continue
            res.n_docs += 1
            res.modes[_doc_mode(rows)] += 1
            tags: set[str] = set()
            for r in rows.parse_log:
                tags.update(json.loads(r["unknown_tags"] or "[]"))
            for tag in tags:
                res.unknown_tags[tag] += 1
            ents: set[str] = set()
            for m in rows.meta:
                if m.get("text_equal") == "false":
                    res.text_equal_violations += 1
                ents.update(json.loads(m.get("other_entities") or "[]"))
            for ent in ents:
                res.other_entities[ent] += 1
    finally:
        for f in files.values():
            f.close()
    return res


def _clear_cache(cache: Path) -> None:
    """부분 연도 재실행이 stale 파일을 남기면 G8 등식(캐시 행수 = summary 행수)이 깨진다."""
    for t in TABLES:
        d = cache / t
        d.mkdir(parents=True, exist_ok=True)
        for f in d.glob("year=*.jsonl"):
            f.unlink()


def run(db: Path, docs_dir: Path, cache_root: Path, snapshot_id: str, workers: int = 3,
        years: set[str] | None = None, d2_limit: float = 0.005, d3_limit: float = 0.01,
        limit_per_year: int | None = None, rcept_list: set[str] | None = None) -> PrepassSummary:
    """프리패스 실행. 결과는 값(PrepassSummary.status)으로, 예외는 환경 오류에만."""
    cache = cache_root / snapshot_id
    _clear_cache(cache)
    by_year = _docs_by_year(db, years, rcept_list)
    if limit_per_year is not None:
        by_year = {y: d[:limit_per_year] for y, d in by_year.items()}
    all_rno = sorted(r for docs in by_year.values() for r, _ in docs)
    input_hash = hashlib.sha256("\n".join(all_rno).encode()).hexdigest()[:16]
    jobs = [(y, d, docs_dir, cache) for y, d in sorted(by_year.items())]
    if workers <= 1:
        results = [_run_shard(j) for j in jobs]
    else:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            results = list(ex.map(_run_shard, jobs))
    modes: Counter[str] = Counter()
    tables: Counter[str] = Counter()
    unknown: Counter[str] = Counter()
    ents: Counter[str] = Counter()
    n_docs = n_missing = n_open_failed = n_eq = 0
    missing: list[str] = []
    for r in results:
        modes += r.modes
        tables += r.tables
        unknown += r.unknown_tags
        ents += r.other_entities
        n_docs += r.n_docs
        n_missing += r.n_zip_missing
        n_open_failed += r.n_zip_open_failed
        n_eq += r.text_equal_violations
        missing += r.missing
    if sum(modes.values()) != n_docs:
        raise RuntimeError(f"prepass mode census mismatch: docs={n_docs} modes={dict(modes)}")
    n_xml = modes["ok"] + modes["lenient"] + modes["failed"]
    d2 = modes["failed"] / n_xml if n_xml else 0.0
    d3 = sorted(t for t, c in unknown.items() if n_docs and c / n_docs >= d3_limit)
    d3 += sorted(e for e, c in ents.items() if n_docs and c / n_docs >= d3_limit)
    reasons: list[str] = []
    if n_missing or n_open_failed:
        reasons.append(f"D0 zip missing={n_missing} open_failed={n_open_failed} "
                       f"(first missing: {missing[:3]})")
    if d2 > d2_limit:
        reasons.append(f"D2 failed ratio {d2:.4f} > {d2_limit}")
    if d3:
        reasons.append(f"D3 unknown tags/entities over {d3_limit:.0%} of docs: {d3[:10]}")
    if n_eq:
        reasons.append(f"D10 text equality violations={n_eq}")
    summary = PrepassSummary(snapshot_id, input_hash, n_docs, dict(modes), dict(tables), n_missing,
                             n_open_failed, d2, d3, n_eq,
                             "gate_failed" if reasons else "ok", "; ".join(reasons) or "ok")
    (cache / "summary.json").write_text(
        json.dumps({**asdict(summary), "unknown_tags_top": unknown.most_common(50),
                    "other_entities_top": ents.most_common(20)},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    base = Path(os.environ.get("QL_HOME") or Path(__file__).resolve().parents[2])
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--snapshot-id", required=True)
    ap.add_argument("--snapshot-root", type=Path, default=base / "data" / "snapshots")
    ap.add_argument("--docs-dir", type=Path, default=base / "data" / "raw" / "documents")
    ap.add_argument("--stage-root", type=Path, default=base / "data" / "stage")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--years", help="쉼표 구분 연도 부분집합")
    ap.add_argument("--limit-per-year", type=int)
    ap.add_argument("--rcept-list", type=Path,
                    help="접수번호 목록 파일(한 줄 하나) — 표본·픽스처용")
    a = ap.parse_args(argv)
    db = a.snapshot_root / a.snapshot_id / "dart.db"
    rl = (set(a.rcept_list.read_text(encoding="utf-8").split()) if a.rcept_list else None)
    s = run(db, a.docs_dir, a.stage_root / "_tmp" / "doc", a.snapshot_id, a.workers,
            set(a.years.split(",")) if a.years else None, limit_per_year=a.limit_per_year,
            rcept_list=rl)
    print(f"{s.status} snapshot={s.snapshot_id} docs={s.n_docs:,} modes={s.modes} "
          f"tables={s.tables} input_hash={s.input_hash}")
    print(f"  D0 missing={s.d0_zip_missing} open_failed={s.d0_zip_open_failed} "
          f"D2 failed={s.d2_failed_ratio:.5f} D3 over-limit={s.d3_unknown_tags_over_limit} "
          f"D10 violations={s.d10_text_equal_violations}")
    if s.status != "ok":
        print(f"  {s.detail}")
    return 0 if s.status == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
