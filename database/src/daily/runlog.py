"""러너 실행 기록 — `data/raw/daily_run.db`. 건전성 판정·알림이 이 표를 읽는다. 플랜 Task 1.1."""
from __future__ import annotations

import datetime as dt
import os
import sqlite3
from dataclasses import dataclass

_DDL = """CREATE TABLE IF NOT EXISTS run (
  run_id INTEGER PRIMARY KEY AUTOINCREMENT,
  date TEXT NOT NULL, source TEXT NOT NULL,
  started TEXT NOT NULL, ended TEXT,
  n_calls INTEGER, n_rows INTEGER, status TEXT NOT NULL, detail TEXT)"""
# started·ended 의 형식(UTC, Z 접미). 쓰는 곳은 `_now()`, 읽는 곳은 `first_started_utc()`
# (dart_daily 가 씀) — `scripts/daily_report.py` 는 아직 같은 형식을 직접 적는다
TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


@dataclass(frozen=True)
class Run:
    run_id: int
    date: str
    source: str
    started: str
    ended: str | None
    n_calls: int | None
    n_rows: int | None
    status: str
    detail: str | None


def _now() -> str:
    return dt.datetime.now(dt.UTC).strftime(TS_FORMAT)


def _open(db: str | os.PathLike[str]) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.fspath(db)) or ".", exist_ok=True)
    con = sqlite3.connect(db)
    con.execute(_DDL)
    return con


def start(db: str | os.PathLike[str], *, date: str, source: str) -> int:
    con = _open(db)
    try:
        cur = con.execute("INSERT INTO run (date, source, started, status) VALUES (?,?,?,'running')",
                          (date, source, _now()))
        con.commit()
        rid = cur.lastrowid
        if rid is None:
            raise RuntimeError(f"runlog insert returned no rowid: db={db} date={date} source={source}")
        return int(rid)
    finally:
        con.close()


def finish(db: str | os.PathLike[str], run_id: int, *, status: str, n_calls: int | None = None,
           n_rows: int | None = None, detail: str | None = None) -> None:
    con = _open(db)
    try:
        n = con.execute("UPDATE run SET ended=?, status=?, n_calls=?, n_rows=?, detail=? WHERE run_id=?",
                        (_now(), status, n_calls, n_rows, detail, run_id)).rowcount
        con.commit()
        if n != 1:
            raise ValueError(f"runlog finish matched {n} rows: db={db} run_id={run_id}")
    finally:
        con.close()


def recent(db: str | os.PathLike[str], *, source: str | None = None, limit: int = 20) -> list[Run]:
    con = _open(db)
    try:
        sql = "SELECT run_id,date,source,started,ended,n_calls,n_rows,status,detail FROM run"
        args: tuple[object, ...] = ()
        if source is not None:
            sql += " WHERE source=?"
            args = (source,)
        sql += " ORDER BY run_id DESC LIMIT ?"
        rows = con.execute(sql, (*args, limit)).fetchall()
        return [Run(*r) for r in rows]
    finally:
        con.close()


def first_started_utc(db: str | os.PathLike[str], *, source: str, date: str) -> str | None:
    """그 source·date 의 가장 이른 `started` 를 Z 없는 UTC `%Y-%m-%dT%H:%M:%S` 로 돌려준다.

    원장의 `collected_at` 과 같은 축·형식으로 맞춰 준다(dart 늦은 공시 창). 파일·`run` 표·행이
    없으면 None — 읽기만 하고 파일·표를 만들지 않는다. `TS_FORMAT` 이 아닌 값은 조용히 넘기지
    않고 ValueError 로 멈춘다.
    """
    if not os.path.exists(db):
        return None
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        has_run = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='run'").fetchone()
        if has_run is None:
            return None
        row = con.execute("SELECT MIN(started) FROM run WHERE source=? AND date=?",
                          (source, date)).fetchone()
    finally:
        con.close()
    if row is None or row[0] is None:
        return None
    raw = str(row[0])
    try:
        stamp = dt.datetime.strptime(raw, TS_FORMAT)
    except ValueError as e:
        raise ValueError(f"runlog started 형식이 {TS_FORMAT} 가 아니다: db={db} source={source} "
                         f"date={date} started={raw!r}") from e
    return stamp.strftime("%Y-%m-%dT%H:%M:%S")
