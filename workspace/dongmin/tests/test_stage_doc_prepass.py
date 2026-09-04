"""doc_prepass — ZIP 1회 파싱 → 테이블 × 연도 JSON Lines 캐시 (DOC_DESIGN v1.1 §2.2·§2.5 ①)."""
import io
import json
import sqlite3
import zipfile
from pathlib import Path

from stage import doc_prepass
from test_stage_doc_parsers import AUDIT_XML, FULL_G1, HTML_DOC


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
