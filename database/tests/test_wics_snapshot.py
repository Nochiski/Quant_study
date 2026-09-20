"""wics_snapshot — 원문 보존·멱등·연속 실패 중단·L1 검산 (네트워크 없음, fetch 주입)."""
import json
import sqlite3
import zlib
from pathlib import Path

import pytest

import wics_snapshot as ws

DT = "20260918"


def _body(cnt: int, idx_cd: str, idx_nm: str, sec_cd: str = "G45") -> bytes:
    rows = [{"CMP_CD": f"{i:06d}", "CMP_KOR": "x", "MKT_VAL": 1, "IDX_CD": idx_cd, "IDX_NM_KOR": idx_nm,
             "SEC_CD": sec_cd, "SEC_NM_KOR": "IT"} for i in range(cnt)]
    return json.dumps({"info": {"CNT": cnt, "MKT_VAL": 7}, "sector": [], "list": rows}).encode()


def _fake(table: dict[str, tuple[int, bytes]]):
    calls: list[str] = []

    def fetch(dt_: str, code: str):
        calls.append(code)
        st, body = table[code]
        return st, body, 0.01
    return fetch, calls


def test_saves_raw_body_and_call_log_then_skips_on_rerun(tmp_path: Path) -> None:
    db = str(tmp_path / "wiseindex.db")
    fetch, calls = _fake({"G4535": (200, _body(42, "G4535", "WICS 전자와 전기제품")),
                          "G45": (200, _body(42, "G45", "WICS IT"))})
    out = ws.run(DT, ("G4535", "G45"), db, sleep_s=0, fetch=fetch)
    assert out["G4535"]["status"] == "ok" and out["G4535"]["cnt"] == 42 and out["G4535"]["idx_nm"] == "WICS 전자와 전기제품"
    con = sqlite3.connect(db)
    dt_, code, st, n, body = con.execute("SELECT dt, sec_cd, http_status, n_rows, body FROM wics_raw WHERE sec_cd='G4535'").fetchone()
    assert (dt_, code, st, n) == (DT, "G4535", 200, 42)
    assert json.loads(zlib.decompress(body))["info"]["CNT"] == 42           # 원문 그대로
    assert con.execute("SELECT count(*) FROM wics_call_log WHERE status='ok'").fetchone()[0] == 2
    con.close()
    out2 = ws.run(DT, ("G4535", "G45"), db, sleep_s=0, fetch=fetch)         # 멱등 — 콜 0
    assert {r["status"] for r in out2.values()} == {"skip_exists"} and calls == ["G4535", "G45"]
    rep = ws.report(DT, out)
    assert "G45 ΣL2=   42 L1=   42 =" in rep


def test_empty_list_is_saved_as_zero_rows_and_retried_next_run(tmp_path: Path) -> None:
    """빈 응답(휴장일·구성 미공표)은 원문으로 남기되 멱등 판정에서는 "없음" — 다음 실행이 다시 부른다."""
    db = str(tmp_path / "w.db")
    fetch, calls = _fake({"G9999": (200, _body(0, None, None))})
    out = ws.run(DT, ("G9999",), db, sleep_s=0, fetch=fetch)
    assert out["G9999"]["status"] == "empty" and out["G9999"]["cnt"] == 0
    con = sqlite3.connect(db)
    assert con.execute("SELECT n_rows FROM wics_raw").fetchone()[0] == 0
    con.close()
    out2 = ws.run(DT, ("G9999",), db, sleep_s=0, fetch=fetch)
    assert out2["G9999"]["status"] == "empty" and calls == ["G9999", "G9999"]      # skip 이 아니라 재호출
    assert "empty=1" in ws.report(DT, out2)


def test_main_returns_4_when_every_fetched_code_is_empty(tmp_path: Path, monkeypatch) -> None:
    db = str(tmp_path / "w.db")
    fetch, _ = _fake({"G10": (200, _body(0, None, None)), "G15": (200, _body(0, None, None))})
    monkeypatch.setattr(ws, "fetch_http", fetch)
    assert ws.main(["--dt", DT, "--codes", "G10,G15", "--db", db, "--sleep", "0"]) == 4
    fetch_ok, _ = _fake({"G10": (200, _body(3, "G10", "WICS 에너지", "G10")), "G15": (200, _body(0, None, None))})
    monkeypatch.setattr(ws, "fetch_http", fetch_ok)
    assert ws.main(["--dt", DT, "--codes", "G10,G15", "--db", db, "--sleep", "0"]) == 0   # 일부만 비면 0


def test_three_consecutive_failures_abort(tmp_path: Path) -> None:
    db = str(tmp_path / "w.db")
    fetch, calls = _fake({c: (503, b"") for c in ("G10", "G15", "G20", "G25")})
    with pytest.raises(RuntimeError, match="3회 연속 실패"):
        ws.run(DT, ("G10", "G15", "G20", "G25"), db, sleep_s=0, fetch=fetch)
    assert calls == ["G10", "G15", "G20"]                                   # 4번째는 안 부른다
    con = sqlite3.connect(db)
    assert con.execute("SELECT count(*) FROM wics_call_log WHERE status='fail'").fetchone()[0] == 3
    assert con.execute("SELECT count(*) FROM wics_raw").fetchone()[0] == 0  # 빈 body 는 원문 없음
    con.close()


def test_resolve_codes() -> None:
    assert len(ws.resolve_codes("all")) == 38 and len(ws.resolve_codes("l2")) == 28 and len(ws.resolve_codes("l1")) == 10
    assert ws.resolve_codes("g4535, G45") == ("G4535", "G45")
