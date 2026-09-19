"""doc_prepass — ZIP 1회 파싱 → 테이블 × 연도 JSON Lines 캐시 (DOC_DESIGN v1.1 §2.2·§2.5 ①)."""
import io
import json
import shutil
import sqlite3
import zipfile
from pathlib import Path

import pytest
from test_stage_doc_parsers import AUDIT_XML, FULL_G1, HTML_DOC

from stage import doc_prepass


def _zip(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for n, b in members.items():
            zf.writestr(n, b)
    return buf.getvalue()


def _setup(tmp_path: Path, broken: bool = False) -> tuple[Path, Path]:
    """doc_store + ZIP 2개(2020 사업보고서 main+audit · 2024 HTML). broken=True 면 깨진 ZIP 추가."""
    docs = tmp_path / "documents"
    (docs / "2020").mkdir(parents=True)
    (docs / "2024").mkdir()
    (docs / "2020" / "20200327001141.zip").write_bytes(
        _zip({"20200327001141_00760.xml": AUDIT_XML.encode("cp949"),
              "20200327001141.xml": FULL_G1.encode("cp949")}))
    (docs / "2024" / "20240311901285.zip").write_bytes(
        _zip({"20240311901285.xml": HTML_DOC.encode()}))
    rows = [
        ("20200327001141", 10, "s", 2, 1, "000", "2026-09-01T03:20:50"),
        ("20240311901285", 10, "s", 1, 1, "000", "2026-09-02T00:00:00"),
        ("20240100000000", 0, "s", 0, 0, "014", "2026-09-02T00:00:00"),   # zip_ok=0 → 제외
    ]
    if broken:
        (docs / "2024" / "20240399999999.zip").write_bytes(b"broken")
        rows.append(("20240399999999", 6, "s", 0, 1, "000", "2026-09-02T00:00:00"))
    db = tmp_path / "dart.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE doc_store (rcept_no TEXT PRIMARY KEY, bytes INTEGER, sha256 TEXT,"
                " n_files INTEGER, zip_ok INTEGER, http_status TEXT, fetched_at TEXT)")
    con.executemany("INSERT INTO doc_store VALUES (?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()
    return docs, db


def test_prepass_writes_jsonl_per_table_and_year_with_summary(tmp_path: Path) -> None:
    docs, db = _setup(tmp_path)
    s = doc_prepass.run(db, docs, tmp_path / "cache", snapshot_id="snap_t", workers=1)
    assert s.status == "ok" and s.detail == "ok"
    assert s.n_docs == 2 and s.modes == {"ok": 1, "html": 1}          # 문서 단위(main 멤버 기준)
    assert s.tables == {"stg_doc_meta": 3, "stg_doc_section": 8, "stg_doc_correction": 1,
                        "stg_doc_parse_log": 3}
    assert len(s.input_hash) == 16
    cache = tmp_path / "cache" / "snap_t"
    meta_2020 = (cache / "stg_doc_meta" / "year=2020_q1.jsonl").read_text().splitlines()
    assert len(meta_2020) == 2 and json.loads(meta_2020[1])["member_role"] == "main"
    summary = json.loads((cache / "summary.json").read_text())
    assert summary["tables"]["stg_doc_section"] == 8 and summary["d10_text_equal_violations"] == 0
    assert summary["input_hash"] == s.input_hash


def test_prepass_counts_broken_zip_under_d0_not_d2(tmp_path: Path) -> None:
    docs, db = _setup(tmp_path, broken=True)
    s = doc_prepass.run(db, docs, tmp_path / "cache", snapshot_id="snap_t", workers=1)
    assert s.status == "gate_failed" and "D0" in s.detail and "D2" not in s.detail
    assert s.d0_zip_open_failed == 1 and s.d2_failed_ratio == 0.0
    assert s.n_docs == 2 and s.tables["stg_doc_parse_log"] == 4     # 깨진 ZIP 도 로그 1행


def test_prepass_reports_missing_zip_under_d0(tmp_path: Path) -> None:
    docs, db = _setup(tmp_path)
    (docs / "2024" / "20240311901285.zip").unlink()
    s = doc_prepass.run(db, docs, tmp_path / "cache", snapshot_id="snap_t", workers=1)
    assert s.status == "gate_failed" and s.d0_zip_missing == 1 and "20240311901285" in s.detail


def test_prepass_fails_d3_when_an_unknown_tag_is_frequent(tmp_path: Path) -> None:
    docs, db = _setup(tmp_path)
    p = docs / "2020" / "20200327001141.zip"
    xml = FULL_G1.replace("<P>당사는", "<NEWTAG>x</NEWTAG><P>당사는")
    p.write_bytes(_zip({"20200327001141.xml": xml.encode("cp949")}))
    s = doc_prepass.run(db, docs, tmp_path / "cache", snapshot_id="snap_t", workers=1,
                        d3_limit=0.0)
    assert s.status == "gate_failed" and "NEWTAG" in s.detail


def test_prepass_clears_stale_shards_and_honours_rcept_list(tmp_path: Path) -> None:
    docs, db = _setup(tmp_path)
    cache = tmp_path / "cache" / "snap_t"
    (cache / "stg_doc_meta").mkdir(parents=True)
    (cache / "stg_doc_meta" / "year=1999.jsonl").write_text("{}\n")
    s = doc_prepass.run(db, docs, tmp_path / "cache", snapshot_id="snap_t", workers=1,
                        rcept_list={"20200327001141"})
    assert s.n_docs == 1 and s.modes == {"ok": 1}
    assert not (cache / "stg_doc_meta" / "year=1999.jsonl").exists()
    assert not (cache / "stg_doc_meta" / "year=2024_q1.jsonl").exists()   # 목록 밖은 샤드 없음


def test_scan_lists_documents_whose_text_matches(tmp_path: Path) -> None:
    docs, db = _setup(tmp_path)
    assert doc_prepass.scan(db, docs, r"감사보고서", workers=1) == ["20200327001141"]
    assert doc_prepass.scan(db, docs, r"주식분할결정", workers=1) == ["20240311901285"]
    assert doc_prepass.scan(db, docs, r"없는말", workers=1) == []


def test_summarize_from_cache_agrees_with_run(tmp_path: Path) -> None:
    docs, db = _setup(tmp_path)
    s = doc_prepass.run(db, docs, tmp_path / "cache", snapshot_id="snap_t", workers=1)
    s2, extra = doc_prepass.summarize_from_cache(tmp_path / "cache" / "snap_t", "snap_t",
                                                 s.input_hash, s.d0_zip_missing)
    assert (s2.n_docs, s2.modes, s2.tables, s2.status) == (s.n_docs, s.modes, s.tables, s.status)
    assert s2.d10_text_equal_violations == s.d10_text_equal_violations
    assert "unknown_tags_top" in extra


def test_repair_replaces_only_listed_documents_and_recomputes_summary(tmp_path: Path) -> None:
    docs, db = _setup(tmp_path)
    cache = tmp_path / "cache"
    s0 = doc_prepass.run(db, docs, cache, snapshot_id="snap_t", workers=1)
    # 2020 문서에 절을 하나 더 넣어 "파서/원문이 바뀐" 상황을 만든다
    xml = FULL_G1.replace("</BODY>", '<SECTION-1><TITLE ATOC="Y">새 절</TITLE></SECTION-1></BODY>')
    (docs / "2020" / "20200327001141.zip").write_bytes(
        _zip({"20200327001141_00760.xml": AUDIT_XML.encode("cp949"),
              "20200327001141.xml": xml.encode("cp949")}))
    s1 = doc_prepass.repair(db, docs, cache, "snap_t", {"20200327001141"}, workers=1)
    assert s1.tables["stg_doc_section"] == s0.tables["stg_doc_section"] + 1
    assert s1.tables["stg_doc_meta"] == 3 and s1.n_docs == 2 and s1.modes == {"ok": 1, "html": 1}
    assert s1.input_hash == s0.input_hash and len(s1.repairs) == 1
    assert s1.repairs[0]["n_docs"] == 1 and s1.status == "ok"
    html_rows = (cache / "snap_t" / "stg_doc_meta" / "year=2024_q1.jsonl").read_text().splitlines()
    assert len(html_rows) == 1                                   # 목록 밖 문서는 그대로
    sec = (cache / "snap_t" / "stg_doc_section" / "year=2020_q1.jsonl").read_text().splitlines()
    assert sum(1 for line in sec if "새 절" in line) == 1
    assert not (cache / "snap_t" / "_repair").exists()
    summary = json.loads((cache / "snap_t" / "summary.json").read_text())
    assert summary["repairs"][0]["parser_version"] and summary["tables"] == s1.tables
    with pytest.raises(ValueError, match="outside"):
        doc_prepass.repair(db, docs, cache, "snap_t", {"20240100000000"}, workers=1)


def _next_snapshot(tmp_path: Path, docs: Path, db: Path) -> Path:
    """base 스냅샷에 문서 1건(2024_q2)을 더하고 1건(2024_q1)을 뺀 새 스냅샷 DB."""
    (docs / "2024" / "20240515000001.zip").write_bytes(
        _zip({"20240515000001.xml": FULL_G1.encode("cp949")}))
    db2 = tmp_path / "dart_next.db"
    shutil.copyfile(db, db2)
    con = sqlite3.connect(db2)
    con.execute("DELETE FROM doc_store WHERE rcept_no = '20240311901285'")
    con.execute("INSERT INTO doc_store VALUES ('20240515000001',10,'s',1,1,'000',"
                "'2026-09-19T00:00:00')")
    con.commit()
    con.close()
    return db2


def _cache_bytes(cache: Path) -> dict[str, bytes]:
    return {str(f.relative_to(cache)): f.read_bytes() for f in sorted(cache.rglob("*"))
            if f.is_file()}


_TIMING = ("t_decode_ms", "t_sanitize_ms", "t_parse_ms")


def _rows(cache: Path, table: str) -> list[str]:
    """샤드 전체 행(정렬). 파싱 소요 시간은 실행마다 달라서 뺀다(TECH_DEBT B-1)."""
    out: list[str] = []
    for f in sorted((cache / table).glob("year=*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = {k: v for k, v in json.loads(line).items() if k not in _TIMING}
            out.append(json.dumps(r, ensure_ascii=False, sort_keys=True))
    return sorted(out)


def test_incremental_parses_only_the_diff_and_matches_a_full_rerun(tmp_path: Path) -> None:
    docs, db = _setup(tmp_path)
    root = tmp_path / "cache"
    base = doc_prepass.run(db, docs, root, snapshot_id="snap_base", workers=1)
    assert base.status == "ok"
    db2 = _next_snapshot(tmp_path, docs, db)
    ref = doc_prepass.run(db2, docs, root, snapshot_id="snap_ref", workers=1)
    before = _cache_bytes(root / "snap_base")
    inc = doc_prepass.incremental(db2, docs, root, "snap_next", "snap_base", workers=1)
    assert (inc.n_docs, inc.modes, inc.tables, inc.status) == (ref.n_docs, ref.modes, ref.tables,
                                                               ref.status)
    assert inc.input_hash == ref.input_hash == doc_prepass.input_hash_for(db2)
    assert inc.d0_zip_missing == ref.d0_zip_missing == 0
    for t in doc_prepass.TABLES:
        assert _rows(root / "snap_next", t) == _rows(root / "snap_ref", t), t
    assert _cache_bytes(root / "snap_base") == before          # base 캐시 불변(하드링크 오염 금지)
    assert len(inc.increments) == 1
    e = inc.increments[0]
    assert (e["from"], e["added"], e["removed"]) == ("snap_base", 1, 1)
    summary = json.loads((root / "snap_next" / "summary.json").read_text())
    assert summary["increments"][0]["parser_version"] and summary["tables"] == inc.tables


def test_incremental_hardlinks_untouched_shards_and_breaks_the_link_on_change(
        tmp_path: Path) -> None:
    docs, db = _setup(tmp_path)
    root = tmp_path / "cache"
    doc_prepass.run(db, docs, root, snapshot_id="snap_base", workers=1)
    db2 = _next_snapshot(tmp_path, docs, db)
    doc_prepass.incremental(db2, docs, root, "snap_next", "snap_base", workers=1)
    kept = ("stg_doc_meta", "year=2020_q1.jsonl")
    changed = ("stg_doc_meta", "year=2024_q1.jsonl")
    assert ((root / "snap_next" / kept[0] / kept[1]).stat().st_ino
            == (root / "snap_base" / kept[0] / kept[1]).stat().st_ino)
    assert ((root / "snap_next" / changed[0] / changed[1]).stat().st_ino
            != (root / "snap_base" / changed[0] / changed[1]).stat().st_ino)
    assert (root / "snap_base" / changed[0] / changed[1]).read_text().strip()   # 원본 행 그대로


def test_incremental_with_no_diff_only_links_and_keeps_the_summary_equal(tmp_path: Path) -> None:
    docs, db = _setup(tmp_path)
    root = tmp_path / "cache"
    base = doc_prepass.run(db, docs, root, snapshot_id="snap_base", workers=1)
    inc = doc_prepass.incremental(db, docs, root, "snap_next", "snap_base", workers=1)
    assert (inc.n_docs, inc.modes, inc.tables) == (base.n_docs, base.modes, base.tables)
    assert inc.input_hash == base.input_hash and inc.snapshot_id == "snap_next"
    assert inc.increments[0]["added"] == 0 and inc.increments[0]["removed"] == 0
    for t in doc_prepass.TABLES:
        for f in (root / "snap_next" / t).glob("year=*.jsonl"):
            assert f.stat().st_ino == (root / "snap_base" / t / f.name).stat().st_ino


def test_incremental_rejects_a_missing_base_and_its_own_snapshot(tmp_path: Path) -> None:
    docs, db = _setup(tmp_path)
    root = tmp_path / "cache"
    with pytest.raises(FileNotFoundError, match="base"):
        doc_prepass.incremental(db, docs, root, "snap_next", "snap_base", workers=1)
    doc_prepass.run(db, docs, root, snapshot_id="snap_base", workers=1)
    with pytest.raises(ValueError, match="same snapshot"):
        doc_prepass.incremental(db, docs, root, "snap_base", "snap_base", workers=1)
