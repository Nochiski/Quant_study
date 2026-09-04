"""문서 프리패스 — 스냅샷 doc_store(zip_ok=1) 의 ZIP 을 한 번만 파싱해 테이블×연도 JSON Lines 캐시.

산출: <cache_root>/<snapshot_id>/<table>/year=YYYY_qN.jsonl + summary.json (DOC_DESIGN §2.2·§2.5 ①).
게이트 D0(ZIP 부재·열기 실패 0)·D2(문서 단위 파싱 실패율)·D3(어휘 폐쇄)·D10(텍스트 등식) 을
여기서 판정한다. 문서 단위 = ZIP 1개, 문서의 parse_mode = main 멤버(없으면 첫 멤버)의 것.
CLI: PYTHONPATH=src python -m stage.doc_prepass --snapshot-id S [--workers 3] [--years 2020,2021]
     … --scan REGEX --out LIST   디코딩만 해서 정규식에 걸리는 접수번호 목록 (파싱 없음, 분 단위)
     … --repair LIST             목록 문서만 재파싱해 샤드 행을 교체하고 summary 를 캐시에서 재집계
파서 규칙이 바뀌면 전량(3h)이 아니라 `--scan` 으로 영향 문서를 찾아 `--repair` 한다. 입력 해시는
그대로, 수리 이력(시각·건수·파서 버전)은 summary 의 `repairs` 에 남는다.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import sqlite3
import sys
import zipfile
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
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
    shard: str                           # 접수 연도×분기 (`2024_q1`)
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
    parser_version: str = parsers_doc.PARSER_VERSION
    repairs: list[dict[str, object]] = field(default_factory=list)


def _shard_of(rcept_no: str) -> str:
    """접수 연도×분기(`2024_q1`). 연도 단위면 큰 연도가 끝에 홀로 남아 워커가 논다."""
    q = (int(rcept_no[4:6]) - 1) // 3 + 1
    return f"{rcept_no[:4]}_q{q}"


def _docs_by_shard(db: Path, years: set[str] | None,
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
        out.setdefault(_shard_of(str(rno)), []).append((str(rno), str(at)))
    return out


def _doc_mode(rows: parsers_doc.DocRows) -> str:
    logs = sorted(rows.parse_log, key=lambda r: _ROLE_PRIORITY.get(str(r["member_role"]), 3))
    return str(logs[0]["parse_mode"]) if logs else "failed"


def _run_shard(args: tuple[str, list[tuple[str, str]], Path, Path]) -> ShardResult:
    shard, docs, docs_dir, cache = args
    vocab: DocVocab = load_vocab()
    res = ShardResult(shard)
    files = {t: open(cache / t / f"year={shard}.jsonl", "w", encoding="utf-8") for t in TABLES}
    try:
        for rno, fetched_at in docs:
            path = docs_dir / rno[:4] / f"{rno}.zip"
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


def _judge(snapshot_id: str, input_hash: str, n_docs: int, modes: Counter[str],
           tables: Counter[str], unknown: Counter[str], ents: Counter[str], n_missing: int,
           n_open_failed: int, n_eq: int, missing: list[str], d2_limit: float,
           d3_limit: float) -> tuple[PrepassSummary, dict[str, object]]:
    """집계값 → 게이트 판정. 반환 = (summary, summary.json 에 덧붙일 상위 목록)."""
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
    extra: dict[str, object] = {"unknown_tags_top": unknown.most_common(50),
                                "other_entities_top": ents.most_common(20)}
    return summary, extra


def _write_summary(cache: Path, summary: PrepassSummary, extra: dict[str, object]) -> None:
    (cache / "summary.json").write_text(
        json.dumps({**asdict(summary), **extra}, ensure_ascii=False, indent=1), encoding="utf-8")


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
    by_shard = _docs_by_shard(db, years, rcept_list)
    if limit_per_year is not None:
        by_shard = {k: d[:limit_per_year] for k, d in by_shard.items()}
    all_rno = sorted(r for docs in by_shard.values() for r, _ in docs)
    input_hash = hashlib.sha256("\n".join(all_rno).encode()).hexdigest()[:16]
    jobs = [(k, d, docs_dir, cache) for k, d in sorted(by_shard.items())]
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
    summary, extra = _judge(snapshot_id, input_hash, n_docs, modes, tables, unknown, ents,
                            n_missing, n_open_failed, n_eq, missing, d2_limit, d3_limit)
    _write_summary(cache, summary, extra)
    return summary


def _doc_stats(rows: parsers_doc.DocRows) -> tuple[str, set[str], set[str], int]:
    """문서 1건의 (mode, unknown_tags, other_entities, text_equal 위반 수)."""
    tags: set[str] = set()
    for r in rows.parse_log:
        tags.update(json.loads(r["unknown_tags"] or "[]"))
    ents: set[str] = set()
    n_eq = 0
    for m in rows.meta:
        if m.get("text_equal") == "false":
            n_eq += 1
        ents.update(json.loads(m.get("other_entities") or "[]"))
    return _doc_mode(rows), tags, ents, n_eq


def summarize_from_cache(cache: Path, snapshot_id: str, input_hash: str, n_missing: int,
                         d2_limit: float = 0.005, d3_limit: float = 0.01
                         ) -> tuple[PrepassSummary, dict[str, object]]:
    """캐시 파일만으로 summary 를 다시 집계한다 — `run()` 의 ShardResult 집계와 같은 값이어야 한다.

    문서 = parse_log 의 접수번호. ZIP 열기 실패 문서(member_name '' · error BadZipFile)는 D0 로 세고
    모드 집계에서 뺀다. n_missing(캐시에 행이 없는 문서)은 입력 집합이 같으므로 이전 summary 값.
    """
    by_doc: dict[str, parsers_doc.DocRows] = {}
    for t, attr in _ROWS_ATTR.items():
        for f in sorted((cache / t).glob("year=*.jsonl")):
            for line in open(f, encoding="utf-8"):
                if not line.strip():
                    continue
                r = json.loads(line)
                doc = by_doc.setdefault(str(r["rcept_no"]), parsers_doc.DocRows())
                getattr(doc, attr).append(r)
    modes: Counter[str] = Counter()
    tables: Counter[str] = Counter()
    unknown: Counter[str] = Counter()
    ents: Counter[str] = Counter()
    n_docs = n_open_failed = n_eq = 0
    for rows in by_doc.values():
        for t, attr in _ROWS_ATTR.items():
            tables[t] += len(getattr(rows, attr))
        if any(r.get("member_name") == "" and str(r.get("error") or "").startswith("BadZipFile")
               for r in rows.parse_log):
            n_open_failed += 1
            continue
        n_docs += 1
        mode, tags, es, eq = _doc_stats(rows)
        modes[mode] += 1
        for tag in tags:
            unknown[tag] += 1
        for e in es:
            ents[e] += 1
        n_eq += eq
    return _judge(snapshot_id, input_hash, n_docs, modes, tables, unknown, ents, n_missing,
                  n_open_failed, n_eq, [], d2_limit, d3_limit)


def _scan_shard(args: tuple[list[tuple[str, str]], Path, str]) -> list[str]:
    docs, docs_dir, pattern = args
    rx = re.compile(pattern)
    hits: list[str] = []
    for rno, _ in docs:
        path = docs_dir / rno[:4] / f"{rno}.zip"
        if not path.exists():
            continue
        try:
            zf = zipfile.ZipFile(io.BytesIO(path.read_bytes()))
        except zipfile.BadZipFile:
            continue
        with zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                if rx.search(parsers_doc.decode_bytes(zf.read(info)).text):
                    hits.append(rno)
                    break
    return hits


def scan(db: Path, docs_dir: Path, pattern: str, workers: int = 3,
         years: set[str] | None = None) -> list[str]:
    """디코딩만 하고 파싱은 하지 않는 원문 스캔 — 정규식에 걸리는 접수번호(정렬)."""
    by_shard = _docs_by_shard(db, years, None)
    jobs = [(d, docs_dir, pattern) for _, d in sorted(by_shard.items())]
    if workers <= 1:
        results = [_scan_shard(j) for j in jobs]
    else:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            results = list(ex.map(_scan_shard, jobs))
    return sorted(r for hits in results for r in hits)


def _replace_rows(path: Path, drop: set[str], add: list[dict[str, str | None]]) -> None:
    """샤드 파일에서 drop 접수번호의 행을 빼고 add 행을 덧붙인다(원자 교체)."""
    tmp = path.with_suffix(".jsonl.tmp")
    with open(tmp, "w", encoding="utf-8") as out:
        if path.exists():
            for line in open(path, encoding="utf-8"):
                if line.strip() and json.loads(line)["rcept_no"] not in drop:
                    out.write(line)
        for r in add:
            out.write(json.dumps(r, ensure_ascii=False))
            out.write("\n")
    os.replace(tmp, path)


def repair(db: Path, docs_dir: Path, cache_root: Path, snapshot_id: str, rcept_list: set[str],
           workers: int = 3, d2_limit: float = 0.005, d3_limit: float = 0.01) -> PrepassSummary:
    """목록 문서만 다시 파싱해 캐시 행을 교체하고 summary 를 캐시에서 재집계한다.

    전제: 입력 집합(스냅샷)은 같다 — input_hash 와 D0 missing 은 이전 summary 에서 이어받는다.
    파서 규칙 변경이 목록 밖 문서에 영향이 없다는 것은 `--scan` 으로 호출자가 보장한다.
    """
    cache = cache_root / snapshot_id
    prev_path = cache / "summary.json"
    if not prev_path.exists():
        raise FileNotFoundError(f"repair needs an existing summary: {prev_path}")
    prev = json.loads(prev_path.read_text(encoding="utf-8"))
    by_shard = _docs_by_shard(db, None, rcept_list)
    found = {r for docs in by_shard.values() for r, _ in docs}
    if found != rcept_list:
        raise ValueError(f"repair list has receipts outside doc_store(zip_ok=1): "
                         f"{sorted(rcept_list - found)[:5]}")
    scratch = cache / "_repair"
    _clear_cache(scratch)
    jobs = [(k, d, docs_dir, scratch) for k, d in sorted(by_shard.items())]
    if workers <= 1:
        for j in jobs:
            _run_shard(j)
    else:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            list(ex.map(_run_shard, jobs))
    for shard, docs in by_shard.items():
        drop = {r for r, _ in docs}
        for t in TABLES:
            new_rows = [json.loads(line) for line in open(scratch / t / f"year={shard}.jsonl",
                                                          encoding="utf-8") if line.strip()]
            _replace_rows(cache / t / f"year={shard}.jsonl", drop, new_rows)
    for t in TABLES:
        for f in (scratch / t).glob("year=*.jsonl"):
            f.unlink()
        (scratch / t).rmdir()
    scratch.rmdir()
    summary, extra = summarize_from_cache(cache, snapshot_id, str(prev["input_hash"]),
                                          int(prev.get("d0_zip_missing", 0)), d2_limit, d3_limit)
    summary.repairs = list(prev.get("repairs", [])) + [{
        "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "n_docs": len(rcept_list),
        "parser_version": parsers_doc.PARSER_VERSION}]
    _write_summary(cache, summary, extra)
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
    ap.add_argument("--scan", metavar="REGEX", help="원문 스캔만: 걸리는 접수번호를 --out 에 쓴다")
    ap.add_argument("--out", type=Path, help="--scan 결과 파일")
    ap.add_argument("--repair", type=Path, metavar="LIST",
                    help="목록 문서만 재파싱해 캐시 행 교체 + summary 재집계")
    a = ap.parse_args(argv)
    db = a.snapshot_root / a.snapshot_id / "dart.db"
    cache_root = a.stage_root / "_tmp" / "doc"
    years = set(a.years.split(",")) if a.years else None
    if a.scan:
        hits = scan(db, a.docs_dir, a.scan, a.workers, years)
        text = "\n".join(hits) + ("\n" if hits else "")
        if a.out:
            a.out.write_text(text, encoding="utf-8")
        print(f"scan /{a.scan}/ → {len(hits)} documents" + (f" → {a.out}" if a.out else ""))
        return 0
    if a.repair:
        lst = set(a.repair.read_text(encoding="utf-8").split())
        s = repair(db, a.docs_dir, cache_root, a.snapshot_id, lst, a.workers)
        print(f"repaired {len(lst)} documents; parser={s.parser_version} repairs={len(s.repairs)}")
    else:
        rl = (set(a.rcept_list.read_text(encoding="utf-8").split()) if a.rcept_list else None)
        s = run(db, a.docs_dir, cache_root, a.snapshot_id, a.workers, years,
                limit_per_year=a.limit_per_year, rcept_list=rl)
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
