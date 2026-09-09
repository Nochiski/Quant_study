"""daily.runlog — 러너 실행 기록. 플랜 P1 Task 1.1."""
from daily import runlog


def test_start_finish_roundtrip(tmp_path):
    db = tmp_path / "daily_run.db"
    rid = runlog.start(db, date="20260908", source="krx")
    runlog.finish(db, rid, status="ok", n_calls=7, n_rows=6543, detail="7 endpoints")
    rows = runlog.recent(db, source="krx", limit=5)
    assert len(rows) == 1
    r = rows[0]
    assert (r.date, r.source, r.status, r.n_calls, r.n_rows) == ("20260908", "krx", "ok", 7, 6543)
    assert r.started <= r.ended


def test_unfinished_run_is_visible_as_running(tmp_path):
    db = tmp_path / "daily_run.db"
    runlog.start(db, date="20260908", source="kw")
    r = runlog.recent(db, source="kw", limit=1)[0]
    assert r.status == "running" and r.ended is None
