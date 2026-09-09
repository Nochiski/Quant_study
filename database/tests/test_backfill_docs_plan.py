"""backfill_docs.build_plan — 014(문서 없음) 확정 건은 기본 재시도하지 않는다. 플랜 P2 실측 결함."""
import importlib
import sqlite3
import sys


def _load(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("KRX_API_KEY=k\nKRX_ID=i\nKRX_PW=p\nDART_API_KEY_2=x\n", encoding="utf-8")
    monkeypatch.setenv("QL_ENV", str(env))
    monkeypatch.setenv("QL_HOME", str(tmp_path))
    for m in ("api", "backfill_docs"):
        sys.modules.pop(m, None)
    return importlib.import_module("backfill_docs")


def _db(tmp_path):
    con = sqlite3.connect(tmp_path / "dart.db")
    con.execute("CREATE TABLE dart_disclosure (rcept_no TEXT, rcept_dt TEXT, stock_code TEXT, report_nm TEXT)")
    con.executemany("INSERT INTO dart_disclosure VALUES (?,?,?,?)", [
        ("20260901000001", "20260901", "005930", "사업보고서 (2025.12)"),
        ("20260901000002", "20260901", "005930", "[기재정정]사업보고서 (2025.12)"),
        ("20260901000003", "20260901", "005930", "반기보고서 (2026.06)")])
    con.execute("CREATE TABLE doc_store (rcept_no TEXT PRIMARY KEY, bytes INTEGER, sha256 TEXT, n_files INTEGER, "
                "zip_ok INTEGER, http_status TEXT, fetched_at TEXT)")
    con.executemany("INSERT INTO doc_store VALUES (?,?,?,?,?,?,?)", [
        ("20260901000001", 10, "s", 1, 1, "000", "t"),     # 받음
        ("20260901000002", 0, "s", 0, 0, "014", "t")])     # DART: 문서 없음(영구)
    con.commit()
    return con


def test_014_is_excluded_by_default(tmp_path, monkeypatch):
    m = _load(tmp_path, monkeypatch)
    con = _db(tmp_path)
    plan = m.build_plan(con, [2, 3])
    assert [r for _, r in plan] == ["20260901000003"]


def test_014_can_be_retried_explicitly(tmp_path, monkeypatch):
    m = _load(tmp_path, monkeypatch)
    con = _db(tmp_path)
    plan = m.build_plan(con, [2, 3], retry_014=True)
    assert sorted(r for _, r in plan) == ["20260901000002", "20260901000003"]
