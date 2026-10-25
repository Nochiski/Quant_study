"""daily.runlog — 러너 실행 기록. 플랜 P1 Task 1.1."""
import datetime as dt
import sqlite3

import pytest
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


# ── 그 source·date 의 가장 이른 시작 시각 (배포 묶음 3 M-5) ─────────────────────────
def _started(db, *runs):
    """(date, source, started 원문) 런을 남긴다 — 시작 시각만 고정한다."""
    for date, source, started in runs:
        rid = runlog.start(db, date=date, source=source)
        con = sqlite3.connect(db)
        con.execute("UPDATE run SET started=? WHERE run_id=?", (started, rid))
        con.commit()
        con.close()


def test_first_started_utc_is_the_earliest_start_of_that_source_and_date(tmp_path):
    db = tmp_path / "daily_run.db"
    _started(db, ("20260908", "dart", "2026-09-08T21:00:00Z"),
             ("20260908", "dart", "2026-09-08T09:05:00Z"),
             ("20260908", "kiwoom_fetch", "2026-09-08T01:00:00Z"),    # 다른 source
             ("20260907", "dart", "2026-09-07T09:05:00Z"))            # 다른 date
    assert runlog.first_started_utc(db, source="dart", date="20260908") == "2026-09-08T09:05:00"


def test_first_started_utc_reads_what_start_writes(tmp_path):
    db = tmp_path / "daily_run.db"
    runlog.start(db, date="20260908", source="dart")
    raw = runlog.recent(db, source="dart", limit=1)[0].started
    got = runlog.first_started_utc(db, source="dart", date="20260908")
    assert got == dt.datetime.strptime(raw, runlog.TS_FORMAT).strftime("%Y-%m-%dT%H:%M:%S")


def test_first_started_utc_is_none_without_file_table_or_rows(tmp_path):
    db = tmp_path / "daily_run.db"
    assert runlog.first_started_utc(db, source="dart", date="20260908") is None
    assert not db.exists()                                    # 읽기만 한다 — 파일을 만들지 않는다
    sqlite3.connect(db).close()                               # 표 없는 빈 파일
    assert runlog.first_started_utc(db, source="dart", date="20260908") is None
    con = sqlite3.connect(db)                                 # 표도 만들지 않는다
    assert con.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0] == 0
    con.close()
    runlog.start(db, date="20260907", source="dart")
    assert runlog.first_started_utc(db, source="dart", date="20260908") is None


def test_first_started_utc_rejects_an_unknown_format_with_context(tmp_path):
    """형식이 다르면 (b) 창을 조용히 잃지 않고 멈춘다 — 메시지에 db·source·date·원문 값."""
    db = tmp_path / "daily_run.db"
    _started(db, ("20260908", "dart", "2026-09-08 18:05:00+09:00"))
    with pytest.raises(ValueError) as ei:
        runlog.first_started_utc(db, source="dart", date="20260908")
    for token in (str(db), "source=dart", "date=20260908", "'2026-09-08 18:05:00+09:00'"):
        assert token in str(ei.value)
