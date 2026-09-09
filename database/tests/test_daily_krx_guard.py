"""backfill_krx — 거래일의 빈 응답은 휴장으로 확정하지 않는다(DEFECT-A-01). 플랜 P1 Task 1.2."""
import importlib
import json
import sqlite3
import sys


def _load(tmp_path, monkeypatch, holidays):
    env = tmp_path / ".env"
    env.write_text("KRX_API_KEY=k\nKRX_ID=i\nKRX_PW=p\n", encoding="utf-8")
    monkeypatch.setenv("QL_ENV", str(env))
    monkeypatch.setenv("QL_HOME", str(tmp_path))
    (tmp_path / "data" / "calendar").mkdir(parents=True)
    (tmp_path / "data" / "calendar" / "kis_holidays.json").write_text(
        json.dumps({"year": 2026, "holidays": holidays}), encoding="utf-8")
    for m in ("api", "backfill_krx"):
        sys.modules.pop(m, None)
    return importlib.import_module("backfill_krx")


def _statuses(tmp_path, d):
    con = sqlite3.connect(tmp_path / "data" / "raw" / "krx.db")
    return dict(con.execute("SELECT endpoint, status FROM ingest_log WHERE bas_dd=?", (d,)).fetchall())


def test_empty_response_on_trading_day_is_pending_not_holiday(tmp_path, monkeypatch):
    m = _load(tmp_path, monkeypatch, holidays=["20260924"])
    monkeypatch.setattr(m, "call", lambda path, bas_dd: ([], "holiday"))   # KRX 가 아직 안 준 상태
    monkeypatch.setattr(sys, "argv", ["x", "--from", "2026-09-08", "--to", "2026-09-08"])
    m.main()
    st = _statuses(tmp_path, "20260908")
    assert set(st.values()) == {"pending"} and len(st) == 7        # 가격 프로브 뒤 나머지 6개도 pending
    # 다음 실행에서는 done 에 안 들어가야 다시 친다
    seen = []
    monkeypatch.setattr(m, "call", lambda path, bas_dd: (seen.append(path) or [{"ISU_CD": "005930", "IDX_NM": "KOSPI", "X": "1"}], "ok"))
    m.main()
    assert len(seen) == 7 and _statuses(tmp_path, "20260908")["sto/stk_bydd_trd"] == "ok"


def test_empty_response_on_calendar_holiday_is_holiday(tmp_path, monkeypatch):
    m = _load(tmp_path, monkeypatch, holidays=["20260924"])
    calls = []
    monkeypatch.setattr(m, "call", lambda path, bas_dd: (calls.append(path) or [], "holiday"))
    monkeypatch.setattr(sys, "argv", ["x", "--from", "2026-09-24", "--to", "2026-09-24"])
    m.main()
    st = _statuses(tmp_path, "20260924")
    assert set(st.values()) == {"holiday"} and len(calls) == 1        # 가격 1콜로 판정, 나머지 skip


def test_refetch_clears_done_dates(tmp_path, monkeypatch):
    m = _load(tmp_path, monkeypatch, holidays=[])
    monkeypatch.setattr(m, "call", lambda path, bas_dd: ([{"ISU_CD": "005930", "IDX_NM": "KOSPI", "X": "1"}], "ok"))
    monkeypatch.setattr(sys, "argv", ["x", "--from", "2026-09-08", "--to", "2026-09-08"])
    m.main()
    n = []
    monkeypatch.setattr(m, "call", lambda path, bas_dd: (n.append(1) or [{"ISU_CD": "005930", "IDX_NM": "KOSPI", "X": "2"}], "ok"))
    monkeypatch.setattr(sys, "argv", ["x", "--from", "2026-09-09", "--to", "2026-09-09", "--refetch", "20260908"])
    m.main()
    assert len(n) == 14                                                   # 09-08 재수집 7 + 09-09 7
    con = sqlite3.connect(tmp_path / "data" / "raw" / "krx.db")
    assert con.execute("SELECT X FROM krx_stk_bydd_trd WHERE bas_dd_req='20260908'").fetchone()[0] == "2"
